"""
proyeccion_tramos.py
--------------------
Helpers de la feature `proyeccion-por-tramos` (modo de proyección OPCIONAL en el que el
horizonte se divide en tramos por fin de año y se encadenan corridas anuales del motor).

Este módulo aísla la lógica pura y testeable de:
  - Troceo del horizonte en tramos por fin de año (`calcular_tramos`).
  - Prorrateo de la variación ANUAL de reserva sobre los meses del tramo
    (`prorratear_reservas_anual`).
  - Persistencia de los snapshots proyectados por corte en la tabla
    `portafolio_clasificado_proy` (esquema idempotente, queries parametrizadas, allowlist).

Principios (regla de consistencia del proyecto):
  - El `Motor_Proyeccion` (`scripts/motor_proyeccion.py`) NO se modifica desde aquí; su
    único cambio es un argumento opcional `mapa_reservas` (ver tarea 5).
  - El esquema se crea de forma IDEMPOTENTE (`CREATE TABLE IF NOT EXISTS`) desde la capa
    de aplicación, nunca desde el bootstrap destructivo `init_db.py`.
  - Toda entrada (fechas, variaciones, filas de DataFrame) se trata como NO confiable.

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


# ==============================================================================
# EXCEPCIONES DE DOMINIO
# ==============================================================================

class HorizonteInvalidoError(Exception):
    """El horizonte total es menor a un mes: el modo por tramos no puede ejecutarse."""


class SnapshotAusenteError(Exception):
    """No existe un snapshot persistido para la fecha de corte solicitada."""


# ==============================================================================
# CONSOLIDACIÓN DE NEGOCIABLES FUNGIBLES (feature
# optimizacion-agrupacion-negociables): alfabeto de clasificación y modelo de
# diagnóstico. Son datos inmutables de módulo (no estado mutable): el único efecto
# observable de la consolidación sigue siendo `logger.warning` reutilizando el
# `logger` de módulo definido arriba (Req 5.1, 5.2, 6.4).
# ==============================================================================

# Clases de origen NO-RF: toda fila con `clase_origen` en este conjunto es INTOCABLE
# (la consolidación aplica EXCLUSIVAMENTE a Renta Fija Negociable). Se replica el mismo
# alfabeto que usa `cargar_titulos_desde_snapshot` para omitir filas no-RF (Req 2.4).
CLASES_ORIGEN_NO_RF: frozenset[str] = frozenset(
    {"RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"}
)

# Marcadores de clasificación contable a TIR / costo amortizado: toda fila cuya
# `clasificacion_contable` corresponda a `Vencimiento` o `Disp. Venta` es INTOCABLE y
# NUNCA se agrupa (restricción de negocio inviolable, Req 2.1, 2.2).
CLASIFICACIONES_A_TIR: frozenset[str] = frozenset({"VENCIMIENTO", "DISP. VENTA"})


@dataclass(frozen=True)
class ResumenConsolidacion:
    """
    Diagnóstico de solo lectura devuelto por `consolidar_negociables_fungibles`.

    Permite al orquestador registrar el rastro `[TRAMOS-CONSOLIDA]` (análogo a
    `[SNAPSHOT-DEDUP]`, Req 6.4) sin que la función pura haga I/O. No contiene datos
    sensibles: solo conteos agregados antes/después de la consolidación.

    Campos:
      - n_entrada: filas de entrada.
      - n_salida: filas de salida.
      - n_negociables: filas Negociables candidatas (RF, sin swap, clave válida).
      - n_intocables: filas no consolidadas (TIR + no-RF + swap + inválidas).
      - n_grupos: grupos fungibles con >= 2 miembros.
      - n_colapsadas: filas removidas por consolidación (= n_entrada - n_salida).
    """
    n_entrada: int
    n_salida: int
    n_negociables: int
    n_intocables: int
    n_grupos: int
    n_colapsadas: int


# Precisión fija (nº de decimales) a la que se redondea `tasa_facial` al construir la
# clave. Neutraliza el ruido de punto flotante para que dos reinversiones con la MISMA
# tasa facial agrupen aunque difieran en el último bit del float (Req 1.5).
_PRECISION_TASA_FACIAL: int = 10


def _tipo_bono_fungibilidad(asset_class: str, moneda: str, tasa_referencia: str) -> str:
    """
    Deriva el `tipo_bono` (UVR/IPC/YANKEE/TF) con la MISMA lógica que
    `cargar_titulos_desde_snapshot`, a partir de `asset_class_definitivo`, `moneda` y
    `tasa_referencia`.

    Es CRÍTICO que la derivación sea idéntica a la del reconstructor del snapshot: dos
    `Titulo_Negociable` solo son fungibles (mismo precio por unidad de nominal) si al
    reconstruirse como t0 del tramo siguiente obtienen el mismo `tipo_bono` y, por tanto,
    la misma curva/convención de valoración (Req 1.5, 4.4).

    Los argumentos ya vienen normalizados por el llamador: `asset_class` con `.strip()`
    (preservando mayúsculas/minúsculas originales, como en el reconstructor), `moneda` y
    `tasa_referencia` con `.strip()`.
    """
    if "UVR" in asset_class or moneda == "UVR":
        return "UVR"
    if tasa_referencia == "IPC" or "IPC" in asset_class:
        return "IPC"
    if moneda == "USD" or asset_class in ("LEGATRUU", "Estructuras"):
        return "YANKEE"
    return "TF"


def _clave_fungibilidad(fila) -> tuple | None:
    """
    Construye la `Clave_Fungibilidad` normalizada de una fila Negociable, o `None` si
    algún componente no es coercionable (en cuyo caso la fila se tratará como intocable).

    Clave = (especie, isin, fecha_vencimiento_iso, tasa_facial_red, periodicidad,
             tipo_tasa, moneda, tipo_bono)

    Entrada NO confiable (Req 5.4): todos los campos se normalizan/coercionan y la
    función NUNCA lanza; ante cualquier dato degenerado devuelve `None` para que el
    llamador degrade la fila a intocable sin perderla.

    Args:
        fila: mapping tipo fila de DataFrame (soporta `.get`) con las columnas del
              snapshot (`especie`, `isin`, `fecha_vencimiento`, `tasa_facial`,
              `periodicidad`, `tipo_tasa`, `moneda`, `asset_class_definitivo`,
              `tasa_referencia`).

    Returns:
        Tupla hashable con los 8 componentes normalizados, o `None` si no coerciona.
    """
    import math

    import pandas as pd

    def _texto(valor) -> str:
        """Normaliza texto no confiable: str, trim y mayúsculas (unicode-safe)."""
        return str(valor).strip().upper()

    try:
        # --- Texto normalizado (mayúsculas / sin espacios de borde). ---
        especie = _texto(fila.get("especie", ""))
        isin = _texto(fila.get("isin", ""))
        periodicidad = _texto(fila.get("periodicidad", ""))
        tipo_tasa = _texto(fila.get("tipo_tasa", ""))
        moneda = _texto(fila.get("moneda", ""))

        # --- fecha_vencimiento -> ISO YYYY-MM-DD (None si no parsea). ---
        fv_raw = fila.get("fecha_vencimiento", None)
        try:
            fv_ts = pd.Timestamp(fv_raw)
        except Exception:
            return None
        if pd.isna(fv_ts):
            return None
        fecha_vencimiento_iso = fv_ts.date().isoformat()

        # --- tasa_facial -> float finito redondeado a precisión fija. ---
        tasa_num = pd.to_numeric(fila.get("tasa_facial", None), errors="coerce")
        try:
            tasa_facial = float(tasa_num)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(tasa_facial):
            return None
        tasa_facial_red = round(tasa_facial, _PRECISION_TASA_FACIAL)

        # --- tipo_bono: MISMA lógica que cargar_titulos_desde_snapshot. ---
        # El reconstructor usa asset_class y moneda SIN forzar mayúsculas (solo .strip())
        # y la tasa de referencia con .strip(); se replica fielmente para garantizar el
        # mismo precio por unidad de nominal al reconstruir el consolidado.
        asset_class = str(fila.get("asset_class_definitivo", "")).strip()
        moneda_tb = str(fila.get("moneda", "")).strip()
        tasa_referencia = str(fila.get("tasa_referencia", "")).strip()
        tipo_bono = _tipo_bono_fungibilidad(asset_class, moneda_tb, tasa_referencia)
    except Exception:
        # Cualquier degeneración inesperada: la fila se tratará como intocable.
        return None

    return (
        especie,
        isin,
        fecha_vencimiento_iso,
        tasa_facial_red,
        periodicidad,
        tipo_tasa,
        moneda,
        tipo_bono,
    )


# Columnas sumables del `Titulo_Consolidado`: su valor es la SUMA de los valores de los
# miembros del grupo (Req 1.2, 1.3, 1.4). El resto de columnas no sumables (p. ej.
# `tasa_compra`) se toman del miembro de mayor nominal (informativas).
_COLUMNAS_SUMABLES_CONSOLIDADO: tuple[str, ...] = ("nominal", "valor_mercado", "valor_tir")


def _es_vacio(valor) -> bool:
    """
    True si `valor` representa "vacío" para efectos de partición (NaN/None/''/espacios).
    Entrada NO confiable: nunca lanza.
    """
    import pandas as pd

    try:
        if valor is None:
            return True
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        pass
    return str(valor).strip() == ""


def _id_consolidado(clave: tuple) -> str:
    """
    `id_posicion` sintético y DETERMINISTA del `Titulo_Consolidado`: `CONS_<hash corto>`.

    Usa SHA-256 (determinista entre procesos, a diferencia de `hash()` que depende de
    PYTHONHASHSEED) sobre la representación de la clave normalizada. Se trunca a 16 hex
    para un id compacto y estable. No es un secreto: solo identifica el grupo.
    """
    import hashlib

    payload = repr(clave).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"CONS_{digest}"


def consolidar_negociables_fungibles(
    df,
    *,
    tolerancia: float = 1e-9,
) -> tuple["pd.DataFrame", ResumenConsolidacion]:
    """
    Colapsa los `Titulo_Negociable` fungibles del DataFrame de cierre del tramo en un
    único `Titulo_Consolidado` por `Grupo_Fungible`, dejando intactas TODAS las demás
    filas (títulos a TIR, no-RF, subyacentes de swap y filas con clave degenerada).

    Función PURA (Req 5.1): sin I/O, sin estado global mutable y SIN mutar la entrada
    (devuelve un DataFrame nuevo con las mismas columnas). El único efecto observable es
    `logger.warning` genérico ante filas con clave no coercionable (Req 5.4), sin volcar
    contenido sensible.

    Partición (ver tabla de Clasificación de filas / Error Handling del diseño):
      - CONSOLIDABLE: Renta Fija (`clase_origen` en {TITULO, vacío}) AND
        `clasificacion_contable == Negociable` AND `id_cobertura` vacío.
      - INTOCABLE: todo lo demás (a TIR, no-RF, subyacente de swap, clave inválida).
        Si falta la columna `clasificacion_contable`, se asume el valor más conservador
        (intocable). Si falta `clase_origen`/`id_cobertura`, se tratan como vacío.

    Colapso por grupo:
      - Grupos con >= 2 miembros -> `Titulo_Consolidado`:
        `nominal`/`valor_mercado`/`valor_tir` = suma del grupo (Req 1.2-1.4); atributos
        de la `Clave_Fungibilidad` comunes (Req 1.5); `clasificacion_contable` =
        `Negociable`; `fecha_emision` = MÍNIMA del grupo (Req 4.2); `id_posicion` =
        `CONS_<hash corto determinista de la clave>`; columnas no sumables tomadas del
        miembro de mayor nominal.
      - Grupos de 1 miembro -> fila emitida sin cambios (Req 1.6).

    Orden de salida estable y determinista: INTOCABLES (en su orden original) +
    consolidados + singletons (en el orden de primera aparición de su grupo).

    Casos degenerados (Req 6.5): DataFrame vacío o sin columnas esperadas -> se devuelve
    el DataFrame tal cual (copia) con un `ResumenConsolidacion` en ceros. NUNCA lanza.

    Args:
        df: pandas.DataFrame con las filas de cierre del tramo (allowlist `_COLUMNAS_PROY`).
        tolerancia: holgura numérica reservada para invariantes de suma; no altera el
            resultado del colapso (la suma es exacta por acumulación directa).

    Returns:
        (df_consolidado, resumen): DataFrame nuevo con las mismas columnas y el
        `ResumenConsolidacion` con los conteos antes/después para el diagnóstico.
    """
    import pandas as pd

    # ── Caso degenerado: entrada vacía o sin columnas esperadas → copia + ceros. ──
    if df is None or len(df) == 0 or "clasificacion_contable" not in df.columns:
        n = 0 if df is None else len(df)
        df_salida = df.copy() if df is not None else df
        resumen = ResumenConsolidacion(
            n_entrada=n, n_salida=n, n_negociables=0,
            n_intocables=n, n_grupos=0, n_colapsadas=0,
        )
        return df_salida, resumen

    n_entrada = len(df)
    tiene_clase_origen = "clase_origen" in df.columns
    tiene_id_cobertura = "id_cobertura" in df.columns

    # Registros posicionales para preservar orden original de las filas intocables.
    intocables_idx: list[int] = []          # posiciones de filas intocables
    # Grupos fungibles: clave -> {"pos": [posiciones], "primera": orden_1a_aparicion}
    grupos: dict[tuple, dict] = {}
    n_negociables = 0
    hubo_clave_invalida = False

    for pos in range(n_entrada):
        fila = df.iloc[pos]

        # --- Exclusión por clase_origen (no-RF ⇒ intocable, Req 2.4). ---
        if tiene_clase_origen:
            clase = str(fila.get("clase_origen", "")).strip().upper()
            es_rf = clase == "TITULO" or clase == ""
        else:
            # Ausencia de clase_origen: por compat. con snapshots RF-only, se trata como RF.
            es_rf = True

        # --- Exclusión por clasificación a TIR (Req 2.1, 2.2). ---
        clasif = str(fila.get("clasificacion_contable", "")).strip().upper()
        es_negociable = clasif == "NEGOCIABLE"

        # --- Exclusión por subyacente de swap (id_cobertura no vacío, Req 2.5). ---
        if tiene_id_cobertura:
            tiene_cobertura = not _es_vacio(fila.get("id_cobertura", None))
        else:
            tiene_cobertura = False

        if not (es_rf and es_negociable and not tiene_cobertura):
            intocables_idx.append(pos)
            continue

        # --- Candidata CONSOLIDABLE: calcular clave de fungibilidad. ---
        clave = _clave_fungibilidad(fila)
        if clave is None:
            # Fallback por fila: clave degenerada ⇒ intocable (nunca se pierde, Req 5.4).
            hubo_clave_invalida = True
            intocables_idx.append(pos)
            continue

        n_negociables += 1
        if clave not in grupos:
            grupos[clave] = {"pos": [], "primera": pos}
        grupos[clave]["pos"].append(pos)

    if hubo_clave_invalida:
        # Warning GENÉRICO: no se vuelca contenido de las filas (Req 5.4).
        logger.warning(
            "consolidar_negociables: una o más filas Negociables tienen clave de "
            "fungibilidad no coercionable; se preservan intactas (no se consolidan)."
        )

    columnas = list(df.columns)

    # ── Construir las filas de salida ──────────────────────────────────────────
    # 1) INTOCABLES, en su orden original.
    filas_salida: list[dict] = [df.iloc[p].to_dict() for p in intocables_idx]
    n_intocables = len(intocables_idx)

    n_grupos = 0
    # 2) Grupos (consolidados y singletons), en orden de primera aparición (determinista).
    for clave, info in sorted(grupos.items(), key=lambda kv: kv[1]["primera"]):
        posiciones = info["pos"]
        if len(posiciones) == 1:
            # Grupo unitario: fila sin cambios (Req 1.6).
            filas_salida.append(df.iloc[posiciones[0]].to_dict())
            continue

        n_grupos += 1
        miembros = df.iloc[posiciones]

        # Fila base: la del miembro de MAYOR nominal (fuente de columnas no sumables).
        if "nominal" in columnas:
            nominal_num = pd.to_numeric(miembros["nominal"], errors="coerce")
        else:
            nominal_num = None
        if nominal_num is not None and nominal_num.notna().any():
            pos_base_local = int(nominal_num.fillna(float("-inf")).to_numpy().argmax())
        else:
            pos_base_local = 0
        fila_consolidada = miembros.iloc[pos_base_local].to_dict()

        # Columnas sumables = suma del grupo (coerción segura; NaN se trata como 0).
        for col in _COLUMNAS_SUMABLES_CONSOLIDADO:
            if col in columnas:
                suma = pd.to_numeric(miembros[col], errors="coerce").sum(min_count=1)
                fila_consolidada[col] = suma

        # clasificacion_contable invariante del grupo = Negociable (Req 1.5).
        if "clasificacion_contable" in columnas:
            fila_consolidada["clasificacion_contable"] = "Negociable"

        # fecha_emision = MÍNIMA del grupo (Req 4.2): no recorta cupones futuros.
        if "fecha_emision" in columnas:
            fe = pd.to_datetime(miembros["fecha_emision"], errors="coerce")
            if fe.notna().any():
                fila_consolidada["fecha_emision"] = fe.min()

        # id_posicion sintético determinista del grupo.
        if "id_posicion" in columnas:
            fila_consolidada["id_posicion"] = _id_consolidado(clave)

        filas_salida.append(fila_consolidada)

    # Reconstruir DataFrame con las MISMAS columnas y orden de columnas que la entrada.
    df_consolidado = pd.DataFrame(filas_salida, columns=columnas)

    n_salida = len(df_consolidado)
    resumen = ResumenConsolidacion(
        n_entrada=n_entrada,
        n_salida=n_salida,
        n_negociables=n_negociables,
        n_intocables=n_intocables,
        n_grupos=n_grupos,
        n_colapsadas=n_entrada - n_salida,
    )
    return df_consolidado, resumen


# ==============================================================================
# TRAMO Y TROCEO DEL HORIZONTE (calcular_tramos)
# ==============================================================================

@dataclass(frozen=True)
class Tramo:
    """
    Sub-periodo del horizonte, desde `fecha_inicio` hasta `fecha_corte` (fin de año o,
    para el último tramo, la fecha de fin del horizonte).

    - indice: posición 0-based en orden cronológico.
    - fecha_inicio: inicio del tramo (t0 real para el primer tramo; día siguiente al
      corte previo para los demás).
    - fecha_corte: `Fecha_Corte_Proyeccion` del tramo (identifica su snapshot).
    - sub_horizonte: número de meses del tramo (>= 1), con el Conteo_Meses_Inclusivo
      (`delta.years*12 + delta.months + (1 if delta.days > 0 else 0)`), de modo que un
      año calendario completo (1-ene → 31-dic) cuenta 12 meses.
    """
    indice: int
    fecha_inicio: date
    fecha_corte: date
    sub_horizonte: int


def _meses_entre(inicio: date, fin: date) -> int:
    """
    Meses entre dos fechas con la regla de negocio ``Conteo_Meses_Inclusivo``:

        delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)

    Delega en ``meses_horizonte_inclusivo`` (feature `proyeccion-por-tramos-multiactivo`,
    módulo `proyeccion_tramos_multiactivo`) para centralizar el conteo y evitar
    divergencias entre el modo por tramos y el orquestador consolidado punta a punta
    (design D6, Req 10).

    Corrige el off-by-one de la fórmula histórica ``delta.years * 12 + delta.months``
    (sin ajuste por días), que devolvía 11 para un año calendario completo
    (`1-ene` → `31-dic`) porque ``relativedelta`` deja 11 meses + 30 días. Con el ajuste
    del mes residual, un año completo cuenta 12 meses.

    Se importa dentro de la función (import diferido) siguiendo el patrón ya usado en este
    módulo para dependencias entre scripts de `scripts/` (p. ej. `from motor_proyeccion
    import Titulo`), evitando ciclos de import a nivel de módulo.
    """
    from proyeccion_tramos_multiactivo import meses_horizonte_inclusivo

    return meses_horizonte_inclusivo(inicio, fin)


def calcular_tramos(fecha_inicio: date, fecha_fin: date) -> list[Tramo]:
    """
    Divide el horizonte [fecha_inicio, fecha_fin] en tramos por fin de año calendario.

    Reglas:
      - Los cortes intermedios son el 31-dic de cada año calendario contenido en el
        horizonte (todos los años estrictamente anteriores al de `fecha_fin`).
      - El corte del último tramo es `fecha_fin` (aunque no sea fin de año).
      - `fecha_inicio` de cada tramo posterior al primero es el día siguiente al corte
        del tramo anterior (tramos contiguos, sin gaps ni solapes).
      - `sub_horizonte` de cada tramo = `_meses_entre(inicio_tramo, corte_tramo)`.
      - Si el horizonte cae dentro de un único año calendario → exactamente un tramo.

    Args:
        fecha_inicio: inicio del horizonte.
        fecha_fin: fin del horizonte (>= fecha_inicio).

    Returns:
        Lista de `Tramo` en orden cronológico ascendente por `fecha_corte`.

    Raises:
        HorizonteInvalidoError: si el horizonte total es menor a un mes.
    """
    if _meses_entre(fecha_inicio, fecha_fin) < 1:
        raise HorizonteInvalidoError(
            "El horizonte de proyección debe ser de al menos un mes."
        )

    tramos: list[Tramo] = []
    inicio_tramo = fecha_inicio
    indice = 0

    # Un corte intermedio (31-dic) por cada año estrictamente anterior al de fecha_fin.
    for anio in range(fecha_inicio.year, fecha_fin.year):
        corte = date(anio, 12, 31)
        # Guardas defensivas: si por datos degenerados el corte no queda dentro del
        # tramo, se omite (el último tramo cubrirá el resto del horizonte).
        if corte <= inicio_tramo or corte >= fecha_fin:
            continue
        sub_h = _meses_entre(inicio_tramo, corte)
        if sub_h < 1:
            # Tramo demasiado corto para el paso mensual: se fusiona con el siguiente
            # (no se emite corte aquí; el inicio del siguiente tramo no avanza).
            continue
        tramos.append(Tramo(indice=indice, fecha_inicio=inicio_tramo,
                            fecha_corte=corte, sub_horizonte=sub_h))
        indice += 1
        inicio_tramo = corte + relativedelta(days=1)

    # Último tramo: hasta fecha_fin.
    sub_h_final = _meses_entre(inicio_tramo, fecha_fin)
    if sub_h_final < 1 and tramos:
        # El resto no alcanza un mes: se absorbe extendiendo el corte del último tramo
        # hasta fecha_fin (preserva cobertura total del horizonte sin tramos < 1 mes).
        ultimo = tramos[-1]
        tramos[-1] = Tramo(
            indice=ultimo.indice,
            fecha_inicio=ultimo.fecha_inicio,
            fecha_corte=fecha_fin,
            sub_horizonte=_meses_entre(ultimo.fecha_inicio, fecha_fin),
        )
    else:
        tramos.append(Tramo(indice=indice, fecha_inicio=inicio_tramo,
                            fecha_corte=fecha_fin, sub_horizonte=max(sub_h_final, 1)))

    return tramos


# ==============================================================================
# PRORRATEO DE LA VARIACIÓN ANUAL DE RESERVA (prorratear_reservas_anual)
# ==============================================================================

def prorratear_reservas_anual(
    variacion_anual_por_ramo: dict[str, float],
    sub_horizonte: int,
    tolerancia: float = 1e-6,
) -> dict[str, dict[int, float]]:
    """
    Reparte linealmente la variación ANUAL de reserva de cada ramo entre los meses del
    tramo, produciendo el `Mapa_Variaciones_Reserva` mensual que consume el motor.

    - Salida: `{ramo_normalizado: {mes_offset: delta}}`, con `mes_offset` en
      `1..sub_horizonte`.
    - Invariante: la suma de los deltas de un ramo es igual a su variación anual dentro
      de `tolerancia`. El residuo de redondeo del reparto se acumula en el ÚLTIMO mes
      para preservar la suma exacta.
    - Ramos con variación 0 (o no coercionable) se OMITEN: el motor asume Δ=0 para los
      meses/ramos ausentes.
    - Entrada NO confiable: se normaliza el ramo (strip/upper) y se coerciona el delta a
      float finito; las entradas inválidas se descartan con `logger.warning` genérico
      (sin volcar el contenido).

    Args:
        variacion_anual_por_ramo: {ramo: variación anual de reserva}.
        sub_horizonte: número de meses del tramo (>= 1).
        tolerancia: holgura para la invariante de suma (no altera el reparto).

    Returns:
        Mapa mensual `{ramo: {mes_offset: delta}}`.
    """
    if sub_horizonte < 1:
        raise ValueError("sub_horizonte debe ser >= 1 para prorratear reservas.")

    import math

    mapa: dict[str, dict[int, float]] = {}

    for ramo_crudo, valor_crudo in variacion_anual_por_ramo.items():
        # Normalizar y validar entrada NO confiable.
        ramo = str(ramo_crudo).strip().upper() if ramo_crudo is not None else ""
        if not ramo:
            logger.warning("prorrateo_reservas: se descarta una fila con ramo vacío.")
            continue
        try:
            variacion = float(valor_crudo)
        except (TypeError, ValueError):
            logger.warning("prorrateo_reservas: se descarta un ramo con delta no numérico.")
            continue
        if not math.isfinite(variacion):
            logger.warning("prorrateo_reservas: se descarta un ramo con delta no finito.")
            continue
        if variacion == 0.0:
            # Sin variación: no genera compra/venta → se omite (motor asume Δ=0).
            continue

        # Reparto lineal: cada mes recibe variacion/sub_horizonte; el residuo por
        # redondeo se acumula en el último mes para que la suma sea exacta.
        cuota = variacion / sub_horizonte
        meses = {mes: cuota for mes in range(1, sub_horizonte + 1)}
        suma_parcial = cuota * (sub_horizonte - 1)
        meses[sub_horizonte] = variacion - suma_parcial  # ajuste de residuo

        mapa[ramo] = meses

    return mapa


# ==============================================================================
# PERSISTENCIA DE SNAPSHOTS (portafolio_clasificado_proy)
# ==============================================================================

# Allowlist FIJA de columnas persistidas (literales controlados por el código; nunca se
# derivan de entrada externa ni se interpolan en el SQL). Núcleo estable para el
# encadenamiento (motor/clasificación) + análisis (régimen, solvencia, asset class).
_COLUMNAS_PROY: tuple[str, ...] = (
    "fecha_corte_proyeccion",   # identifica el corte ('YYYY-MM-DD')
    "id_posicion", "especie", "emisor", "isin", "moneda",
    "fecha_vencimiento", "nominal", "valor_mercado", "tasa_compra", "tir",
    "estado", "portafolio", "compania", "ramo", "clasificacion_contable",
    "subtipo_vehiculo", "tasa_referencia", "tasa_facial", "tipo_tasa",
    "uso_tasa", "periodicidad", "base_anual",
    "asset_class_definitivo", "tramo_plazo",
    "clasificacion_solvencia", "categoria", "ponderacion",
    "numeral_regimen", "descripcion_regimen", "encaje", "clase_origen",
    # ── Metadato de cobertura (subyacente de swap) ──
    # id del swap que cubre el título; necesario para que el resumen por corte separe los
    # "Subyacentes de Swaps" de la "RF Internacional" también en los tramos i>0 (Defecto 4,
    # Req 4.1). Es metadato: no altera la reconstrucción RF ni la valoración. TEXT por defecto.
    "id_cobertura",
    # ── Campos para reconstrucción AUTOSUFICIENTE del Titulo en el encadenamiento ──
    # El snapshot debe permitir reconstruir cualquier Titulo del tramo siguiente (incluidas
    # las reinversiones con ids sintéticos que NO existen en hist_titulos/hist_pip), sin
    # re-cruzar con hist_*. Los tres primeros son los indispensables:
    "fecha_emision",     # requerida por el motor para cupones/valoración
    "valor_tir",         # valor a TIR (costo amortizado) para P&L contable DV/VC
    "spread_mercado",    # SPREAD EFECTIVO DE CIERRE del tramo (no el PIP original) — clave
                         # del encadenamiento incremental (Opción A: lo expone el motor)
)

_TABLA_PROY = "portafolio_clasificado_proy"


def asegurar_tabla_portafolio_proy(conn: sqlite3.Connection) -> None:
    """
    Crea la tabla `portafolio_clasificado_proy` de forma IDEMPOTENTE si no existe, más un
    índice por `fecha_corte_proyeccion`. No siembra datos. NO se invoca desde
    `init_db.py` (bootstrap destructivo).

    El DDL usa `CREATE TABLE IF NOT EXISTS`; es seguro llamarlo varias veces (no borra ni
    altera filas existentes).
    """
    columnas_ddl = [
        "fecha_corte_proyeccion TEXT NOT NULL",
    ]
    # Columnas numéricas conocidas como REAL; el resto TEXT. Solo columnas de la allowlist.
    numericas = {"nominal", "valor_mercado", "tasa_compra", "tir", "tasa_facial",
                 "base_anual", "ponderacion", "valor_tir", "spread_mercado"}
    for col in _COLUMNAS_PROY:
        if col == "fecha_corte_proyeccion":
            continue
        tipo = "REAL" if col in numericas else "TEXT"
        columnas_ddl.append(f"{col} {tipo}")

    ddl = (
        f"CREATE TABLE IF NOT EXISTS {_TABLA_PROY} (\n    "
        + ",\n    ".join(columnas_ddl)
        + "\n)"
    )
    conn.execute(ddl)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS ix_pcp_corte "
        f"ON {_TABLA_PROY} (fecha_corte_proyeccion)"
    )
    conn.commit()


def limpiar_snapshots_proy(conn: sqlite3.Connection) -> None:
    """
    Vacía la tabla de snapshots (`DELETE FROM portafolio_clasificado_proy`, sin entrada
    externa) para reemplazar los snapshots de una corrida previa antes de escribir la
    nueva. Asegura primero el esquema idempotente.
    """
    asegurar_tabla_portafolio_proy(conn)
    conn.execute(f"DELETE FROM {_TABLA_PROY}")
    conn.commit()


# Tabla informativa del detalle P&G/ORI por tramo (una fila por corte). Se escribe con
# `INSERT OR REPLACE` por `fecha_corte`, por lo que es idempotente POR CORTE pero NO se
# auto-reduce: si una corrida previa dejó cortes de más (p. ej. 3 tramos) y la corrida
# nueva tiene menos (p. ej. 2), los cortes viejos SOBREVIVEN y el informe Excel los
# reconstruye como hojas fantasma. Por eso debe vaciarse al inicio de cada corrida por
# tramos, igual que `portafolio_clasificado_proy`.
_TABLA_INFORME_TRAMOS = "informe_pyg_por_tramo"


def limpiar_informe_pyg_por_tramo(conn: sqlite3.Connection) -> None:
    """
    Vacía la tabla informativa `informe_pyg_por_tramo` para reemplazar el detalle por
    tramo de una corrida previa antes de escribir el de la nueva. Evita que cortes
    huérfanos de una corrida anterior (con más tramos) aparezcan como hojas de más en el
    Excel por tramos.

    Es defensiva: si la tabla aún no existe (primera corrida), no hay nada que limpiar y
    la operación es un no-op silencioso. Sin entrada externa (no hay riesgo de inyección).
    """
    try:
        conn.execute(f"DELETE FROM {_TABLA_INFORME_TRAMOS}")
        conn.commit()
    except sqlite3.OperationalError:
        # La tabla aún no existe (primera corrida): nada que limpiar.
        pass


def persistir_snapshot_proy(
    conn: sqlite3.Connection,
    df,
    fecha_corte: date,
) -> int:
    """
    Persiste `df` como snapshot del corte `fecha_corte` en `portafolio_clasificado_proy`.

    - Escribe SOLO columnas de la allowlist `_COLUMNAS_PROY`: las columnas ausentes en el
      DataFrame se guardan como NULL, y las columnas extra del DataFrame se descartan.
    - `fecha_corte_proyeccion` se toma de `fecha_corte` (ISO 'YYYY-MM-DD'), no del df.
    - Valida `valor_mercado`/`nominal` a float finito; las filas no coercionables se
      descartan con `logger.warning` genérico (sin volcar contenido).
    - INSERT parametrizado por filas (`executemany` con placeholders '?'); nunca se
      interpola entrada en el SQL.

    Args:
        conn: conexión SQLite abierta (el llamador gestiona su ciclo de vida).
        df: DataFrame con el portafolio proyectado del tramo.
        fecha_corte: fecha de corte del snapshot.

    Returns:
        Número de filas persistidas.
    """
    import math
    from datetime import datetime
    import pandas as pd

    asegurar_tabla_portafolio_proy(conn)

    corte_iso = fecha_corte.isoformat() if isinstance(fecha_corte, date) else str(fecha_corte)
    columnas = list(_COLUMNAS_PROY)
    # Columnas que se toman del df (todas menos la fecha de corte, que es fija).
    columnas_df = [c for c in columnas if c != "fecha_corte_proyeccion"]

    filas_a_insertar: list[tuple] = []
    for _, fila in df.iterrows():
        # Validación de campos numéricos críticos.
        #
        # SOLO `valor_mercado` es obligatorio: una fila sin VM válido no aporta nada al
        # snapshot y se descarta. `nominal`, en cambio, NO es obligatorio: las filas no-RF
        # del Snapshot_Completo_Tramo (RV / Alternativos / Derivados; Req 14/16) no tienen
        # un "nominal" en el mismo sentido que un título de RF y llegan con NaN. Antes esas
        # filas se descartaban aquí (nominal NaN ⇒ fila inválida), lo que vaciaba el
        # snapshot no-RF (Fix 2). Ahora `nominal` NaN/ausente se persiste como NULL (lo
        # maneja la normalización de más abajo) sin descartar la fila.
        valida = True
        if "valor_mercado" in df.columns:
            try:
                _vm = float(fila["valor_mercado"])
                if not math.isfinite(_vm):
                    valida = False
            except (TypeError, ValueError):
                valida = False
        if not valida:
            logger.warning(
                "snapshot_proy: se descarta una fila sin valor_mercado numérico válido."
            )
            continue

        valores = [corte_iso]
        for col in columnas_df:
            if col in df.columns:
                valor = fila[col]
                # Normalizar a tipos que SQLite acepta (str/int/float/None).
                try:
                    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
                        valores.append(None)
                    elif pd.isna(valor):
                        # NaT (fechas faltantes) y otros nulos de pandas → NULL.
                        valores.append(None)
                    elif isinstance(valor, pd.Timestamp):
                        # SQLite no soporta bind directo de Timestamp: se serializa a ISO.
                        # 'YYYY-MM-DD' si es medianoche (fecha pura), si no ISO completo.
                        valores.append(
                            valor.date().isoformat()
                            if (valor.hour, valor.minute, valor.second, valor.microsecond) == (0, 0, 0, 0)
                            else valor.isoformat(sep=" ")
                        )
                    elif isinstance(valor, (datetime, date)):
                        # datetime/date de la stdlib → ISO string.
                        valores.append(valor.isoformat())
                    elif hasattr(valor, "item"):
                        # Escalares de numpy (np.int64/np.float64) → tipo Python nativo.
                        valores.append(valor.item())
                    else:
                        valores.append(valor)
                except (TypeError, ValueError):
                    valores.append(valor)
            else:
                valores.append(None)  # columna de la allowlist ausente → NULL
        filas_a_insertar.append(tuple(valores))

    if not filas_a_insertar:
        return 0

    placeholders = ", ".join("?" for _ in columnas)
    columnas_sql = ", ".join(columnas)
    conn.executemany(
        f"INSERT INTO {_TABLA_PROY} ({columnas_sql}) VALUES ({placeholders})",
        filas_a_insertar,
    )
    conn.commit()
    return len(filas_a_insertar)


def cargar_snapshot_proy(conn: sqlite3.Connection, fecha_corte: date):
    """
    Lee el snapshot del corte `fecha_corte` desde `portafolio_clasificado_proy` para
    usarlo como t0 del tramo siguiente.

    - SELECT sobre la allowlist de columnas `WHERE fecha_corte_proyeccion = ?`
      (parametrizado).
    - Devuelve un DataFrame de pandas con las columnas de la allowlist (sin la columna
      `fecha_corte_proyeccion`, que es metadato del corte).

    Raises:
        SnapshotAusenteError: si no hay filas para `fecha_corte`.
    """
    import pandas as pd

    asegurar_tabla_portafolio_proy(conn)

    corte_iso = fecha_corte.isoformat() if isinstance(fecha_corte, date) else str(fecha_corte)
    columnas_df = [c for c in _COLUMNAS_PROY if c != "fecha_corte_proyeccion"]
    columnas_sql = ", ".join(columnas_df)

    df = pd.read_sql_query(
        f"SELECT {columnas_sql} FROM {_TABLA_PROY} WHERE fecha_corte_proyeccion = ?",
        conn,
        params=(corte_iso,),
    )
    if df.empty:
        raise SnapshotAusenteError(
            f"No hay snapshot persistido para el corte {corte_iso}."
        )
    return df


# ==============================================================================
# RECONSTRUCCIÓN DE TÍTULOS DESDE UN SNAPSHOT (t0 del tramo siguiente)
# ==============================================================================

def cargar_titulos_desde_snapshot(df):
    """
    Reconstruye una lista de objetos `Titulo` (motor_proyeccion.Titulo) a partir del
    DataFrame de un snapshot de `portafolio_clasificado_proy`, para usarlo como t0 del
    tramo siguiente en el encadenamiento.

    Como el snapshot es AUTOSUFICIENTE (guarda fecha_emision, valor_tir y el
    spread_mercado EFECTIVO DE CIERRE), no se re-cruza con hist_*: cada título —incluidas
    las reinversiones con ids sintéticos— se reconstruye solo con las columnas del
    snapshot. Replica la inferencia de tipo_bono/frecuencia/clasificación de
    `cargar_portafolio_proyeccion`. Solo reconstruye títulos de renta fija (los mismos
    que el motor proyecta); las clases no-RF se omiten.

    Args:
        df: DataFrame del snapshot (salida de `cargar_snapshot_proy`).

    Returns:
        list[Titulo] lista para pasar a `proyectar_portafolio`.
    """
    import pandas as pd
    from motor_proyeccion import Titulo

    # Asset classes que el motor NO proyecta (renta variable, vista, fondos, etc.).
    _EXCLUIR_AC = {
        "VISTA", "COLCAP", "MXWD", "PEI", "COCAP",
        "Fondo Capital Priv. Local", "Fondo Capital Priv. Extranjero",
        "Accion Local", "Accion Internacional",
        "ETF Acciones Local", "ETF Acciones Internacional", "ETF Commodities",
    }

    freq_map = {"TRIMESTRAL": "trimestral", "SEMESTRAL": "semestral",
                "ANUAL": "anual", "MENSUAL": "mensual", "AV": "anual"}

    # clase_origen de activos NO-RF que el Snapshot_Completo_Tramo puede anexar (Req 14):
    # RV, alternativos y derivados se persisten como filas informativas en el mismo
    # snapshot; NO deben reconstruirse como Titulo (invariante Req 14.4). Se excluyen aquí
    # por clase_origen además del filtro por asset_class, para robustez ante filas no-RF
    # que no pueblen asset_class_definitivo.
    _CLASE_ORIGEN_NO_RF = {"RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"}

    titulos: list = []
    for _, row in df.iterrows():
        # Filtrar por clase_origen: solo se reconstruyen títulos de RF (clase_origen
        # 'TITULO' o vacío por compatibilidad con snapshots RF-only previos).
        clase_origen = str(row.get("clase_origen", "")).strip().upper()
        if clase_origen in _CLASE_ORIGEN_NO_RF:
            continue

        ac = str(row.get("asset_class_definitivo", "")).strip()
        # Filtrar no-RF (mismo criterio que cargar_portafolio_proyeccion).
        if ac in _EXCLUIR_AC or "Fondo Capital" in ac or "Accion" in ac or "ETF" in ac:
            continue

        # Inferir tipo de bono (misma lógica del motor).
        tasa_ref = str(row.get("tasa_referencia", "")).strip()
        moneda = str(row.get("moneda", "COP")).strip()
        if "UVR" in ac or moneda == "UVR":
            tipo_bono = "UVR"
        elif tasa_ref == "IPC" or "IPC" in ac:
            tipo_bono = "IPC"
        elif moneda == "USD" or ac in ("LEGATRUU", "Estructuras"):
            tipo_bono = "YANKEE"
        else:
            tipo_bono = "TF"

        # Frecuencia.
        per = str(row.get("periodicidad", "")).strip().upper()
        frecuencia = freq_map.get(per, "anual")

        # Fechas (fecha_emision y fecha_vencimiento vienen del snapshot autosuficiente).
        #
        # [optimizacion-agrupacion-negociables - Tarea 4.1 - Req 4.1/4.2] Anclaje de
        # cupones del Titulo_Consolidado: VERIFICADO que esta reconstruccion NO requiere
        # cambios. El calendario de cupones lo construye `motor_proyeccion.detectar_cupones`
        # caminando HACIA ATRAS desde `fecha_vencimiento` por pasos de `frecuencia`
        # (periodicidad), con monto segun `tasa_cupon` (tasa_facial); `fecha_emision` actua
        # SOLO como cota inferior (`if fc <= titulo.fecha_emision: break`). Por tanto el
        # consolidado -que conserva vencimiento/tasa/periodicidad comunes del grupo y toma
        # la `fecha_emision` MINIMA del grupo- reproduce EXACTAMENTE el mismo calendario que
        # cualquiera de sus miembros, sin recortar ningun cupon futuro (una fecha_emision mas
        # temprana solo puede retener MAS cupones, nunca menos), y su cupon por periodo iguala
        # la suma de los miembros por linealidad en el nominal. Confirmado empiricamente en
        # `_tmp_verify_4_1_anclaje_cupones.py` (desvio relativo 0.0 <= 1e-6). No se modifica la
        # valoracion del motor (Req 5.3).
        try:
            fv = pd.Timestamp(row["fecha_vencimiento"]).date()
            if pd.isna(row["fecha_vencimiento"]):
                continue
        except Exception:
            continue
        try:
            fe = pd.Timestamp(row["fecha_emision"]).date()
            if pd.isna(row["fecha_emision"]):
                fe = fv - relativedelta(years=50)
        except Exception:
            fe = fv - relativedelta(years=50)

        # Clasificación contable.
        clasif_contable = str(row.get("clasificacion_contable", "")).strip()
        if "Disp" in clasif_contable or "Venta" in clasif_contable:
            clasificacion = "Disp. Venta"
        elif "Venc" in clasif_contable:
            clasificacion = "Vencimiento"
        else:
            clasificacion = "Negociable"

        # Tasa facial.
        try:
            facial_raw = str(row.get("tasa_facial", "0"))
            facial = float(pd.to_numeric(facial_raw.split('-')[0].split('N')[0].strip(),
                                        errors='coerce') or 0)
        except Exception:
            facial = 0.0

        def _num(v, default=0.0):
            try:
                x = float(v)
                import math
                return x if math.isfinite(x) else default
            except (TypeError, ValueError):
                return default

        try:
            nominal = float(row["nominal"])
        except (TypeError, ValueError, KeyError):
            continue
        if nominal <= 0:
            continue

        vm = _num(row.get("valor_mercado"), 0.0)
        vtir = _num(row.get("valor_tir"), vm)

        t = Titulo(
            id_posicion=str(row["id_posicion"]),
            especie=str(row.get("especie", "")).strip(),
            emisor=str(row.get("emisor", "")).strip(),
            isin=str(row.get("isin", "")).strip(),
            moneda=moneda,
            fecha_emision=fe,
            fecha_vencimiento=fv,
            nominal=nominal,
            tasa_cupon=facial,
            spread_ipc=facial if tipo_bono == "IPC" else 0,
            tipo_bono=tipo_bono,
            frecuencia=frecuencia,
            tipo_tasa=str(row.get("tipo_tasa", "EFECTIVO")).strip() or "EFECTIVO",
            base_anual=_num(row.get("base_anual"), 365) or 365,
            uso_tasa=str(row.get("uso_tasa", "ACTUAL")).strip() or "ACTUAL",
            clasificacion=clasificacion,
            compania=str(row.get("compania", "")).strip(),
            ramo=str(row.get("ramo", "")).strip(),
            portafolio=str(row.get("portafolio", "")).strip(),
            asset_class=ac,
            tasa_compra=_num(row.get("tasa_compra"), 0.0),
            tasa_mercado=_num(row.get("tir"), 0.0),
            valor_mercado=vm,
            valor_tir=vtir,
            # Fix (Defecto 4): preservar id_cobertura del subyacente de swap a través del
            # round-trip, con la MISMA normalización nan/none que usa el tramo 0 en
            # `cargar_portafolio_proyeccion` (motor_proyeccion.py). Metadato: no altera la
            # reconstrucción RF ni la valoración, pero permite que el resumen por corte
            # separe "Subyacentes de Swaps" de "RF Internacional" también en los tramos i>0.
            id_cobertura=(
                "" if str(row.get("id_cobertura", "")).strip().lower()
                in ("", "nan", "none")
                else str(row.get("id_cobertura", "")).strip()
            ),
        )
        # CLAVE del encadenamiento incremental: el spread base del tramo siguiente es el
        # spread EFECTIVO DE CIERRE del tramo anterior (guardado en el snapshot), no el PIP.
        t.spread_mercado = _num(row.get("spread_mercado"), 0.0)
        titulos.append(t)

    return titulos


# ==============================================================================
# PERSISTENCIA DEL PLANO DE DERIVADOS POR TRAMO (Opción B, Req 13)
# ==============================================================================
# Encadenamiento REAL del plano de posiciones de derivados: el plano de posiciones VIVAS
# al cierre de un tramo (forwards/opciones/swaps con sus renovaciones) es el plano de
# INICIO del tramo siguiente. Se persiste en tablas de proyección SEPARADAS —análogo a
# `portafolio_clasificado_proy` para RF—, NUNCA en las tablas `hist_*` reales, para no
# contaminar la historia reportada con proyecciones.
#
# Estas tablas se crean de forma idempotente y se escriben con `to_sql` (esquema flexible,
# igual que las `hist_*` originales que también se crean con `to_sql`), añadiendo una
# columna `fecha_corte_proyeccion` que identifica el corte. La limpieza por corte usa
# DELETE parametrizado. Las columnas de cada plano son EXACTAMENTE las que consume el
# `Motor_Derivados` (`_cargar_posiciones` / `_cargar_swaps`), de modo que el plano
# recuperado se puede inyectar como `posiciones_iniciales` sin transformación.

_TABLAS_DERIV_PROY = {
    "fwds": "hist_forwards_proy",
    "opts": "hist_opciones_proy",
    "swaps": "hist_swaps_proy",
}


def asegurar_tablas_derivados_proy(conn: sqlite3.Connection) -> None:
    """
    Crea (si faltan) el índice por `fecha_corte_proyeccion` en las tablas de plano de
    derivados proyectado. Las tablas en sí se crean con `to_sql(if_exists='append')` en
    `persistir_plano_derivados_proy` (esquema flexible, como las `hist_*` originales); si
    aún no existen, este helper es un no-op seguro para el índice (se crea tras la primera
    escritura vía el propio persistidor). No siembra datos.
    """
    # No-op defensivo: el índice se asegura tras crear la tabla (ver persistidor). Este
    # helper existe por simetría con `asegurar_tabla_portafolio_proy` y para un futuro DDL
    # explícito si se decide fijar el esquema.
    return None


def limpiar_planos_derivados_proy(conn: sqlite3.Connection) -> None:
    """
    Vacía las tablas de plano de derivados proyectado (`hist_*_proy`) para reemplazar los
    planos de una corrida previa antes de escribir la nueva. Idempotente: si una tabla no
    existe todavía, se ignora silenciosamente (aún no se ha escrito ningún plano).

    Se usa DROP TABLE (no DELETE) para evitar la DERIVA DE ESQUEMA: estas tablas `_proy`
    se crean implícitamente con `to_sql` a partir del DataFrame del plano, por lo que su
    esquema queda congelado por la PRIMERA corrida. Si el plano evoluciona y añade columnas
    nuevas (p. ej. `_es_renovacion` del encadenamiento), un `to_sql(if_exists="append")`
    sobre una tabla vieja falla con "table ... has no column named ...". Como estas tablas
    se REEMPLAZAN por completo en cada corrida, soltarlas aquí permite que la primera
    escritura las recree con el esquema actual. Solo afecta las tablas `_proy` del mapa
    `_TABLAS_DERIV_PROY`; NUNCA toca las tablas `hist_*` reales.
    """
    for tabla in _TABLAS_DERIV_PROY.values():
        try:
            conn.execute(f"DROP TABLE IF EXISTS {tabla}")
        except sqlite3.OperationalError:
            # Falla-seguro: cualquier incidencia al soltar no debe abortar la corrida;
            # la tabla se (re)crea en la primera escritura del plano.
            pass
    conn.commit()


def persistir_plano_derivados_proy(conn: sqlite3.Connection, plano: dict, fecha_corte) -> dict:
    """
    Persiste el plano de posiciones VIVAS al cierre de un tramo (`plano_cierre` que
    devuelve `valorar_portafolio_derivados`) en las tablas `_proy`, con la
    `fecha_corte_proyeccion` del corte. Idempotente por corte (borra el corte previo antes
    de insertar). Usa DELETE parametrizado; `to_sql` para el INSERT (esquema flexible).

    Args:
        conn: conexión SQLite abierta (el llamador gestiona su ciclo de vida).
        plano: dict `{'fwds': DataFrame, 'opts': DataFrame, 'swaps': DataFrame}`.
        fecha_corte: fecha de cierre del tramo (identifica el corte).

    Returns:
        dict `{clave: n_filas_persistidas}` por cada plano escrito.
    """
    import pandas as pd

    corte_iso = fecha_corte.isoformat() if isinstance(fecha_corte, date) else str(fecha_corte)
    escritas: dict = {}
    plano = plano or {}

    for clave, tabla in _TABLAS_DERIV_PROY.items():
        df = plano.get(clave)
        if df is None or len(df) == 0:
            escritas[clave] = 0
            continue
        df_out = df.copy()
        df_out["fecha_corte_proyeccion"] = corte_iso
        # Idempotencia por corte: borrar filas previas de ESTE corte (parametrizado).
        try:
            conn.execute(
                f"DELETE FROM {tabla} WHERE fecha_corte_proyeccion = ?", (corte_iso,)
            )
        except sqlite3.OperationalError:
            pass  # tabla aún no existe; se crea con el to_sql siguiente
        df_out.to_sql(tabla, conn, if_exists="append", index=False)
        # Índice por corte (idempotente) tras asegurar que la tabla existe.
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{tabla}_corte "
            f"ON {tabla} (fecha_corte_proyeccion)"
        )
        escritas[clave] = len(df_out)
    conn.commit()
    return escritas


def cargar_plano_derivados_proy(conn: sqlite3.Connection, fecha_corte) -> dict:
    """
    Recupera el plano de posiciones de derivados persistido para `fecha_corte` desde las
    tablas `_proy`, listo para inyectar como `posiciones_iniciales` del tramo siguiente
    (Opción B, Req 13.3). Devuelve `{'fwds','opts','swaps'}` con las columnas que consume
    el `Motor_Derivados` (se descarta la columna auxiliar `fecha_corte_proyeccion`).

    Si un plano no tiene filas para ese corte (o la tabla no existe aún), su valor es un
    DataFrame vacío. El llamador decide si un plano vacío significa "sin posiciones vivas".

    SELECT parametrizado por `fecha_corte_proyeccion`.
    """
    import pandas as pd

    corte_iso = fecha_corte.isoformat() if isinstance(fecha_corte, date) else str(fecha_corte)
    resultado: dict = {}
    for clave, tabla in _TABLAS_DERIV_PROY.items():
        try:
            df = pd.read_sql_query(
                f"SELECT * FROM {tabla} WHERE fecha_corte_proyeccion = ?",
                conn, params=(corte_iso,),
            )
        except Exception:
            # Tabla inexistente (aún no se ha escrito ningún plano) → plano vacío.
            df = pd.DataFrame()
        if "fecha_corte_proyeccion" in df.columns:
            df = df.drop(columns=["fecha_corte_proyeccion"])
        resultado[clave] = df
    return resultado
