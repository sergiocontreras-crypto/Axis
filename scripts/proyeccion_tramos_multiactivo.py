"""
proyeccion_tramos_multiactivo.py
--------------------------------
Helpers de la feature `proyeccion-por-tramos-multiactivo`.

Esta feature extiende el modo `por_tramos` (feature `proyeccion-por-tramos`) para
encadenar por tramos los CUATRO motores de valoración (Renta Fija, Renta Variable,
Alternativos y Créditos), capturar inputs macro/retorno por tramo, y unificar el
conteo de meses del horizonte en ambos modos de proyección.

Principios (regla de consistencia del proyecto):
  - Los motores de valoración (`proyectar_portafolio`, `proyectar_renta_variable`,
    `proyectar_alternativos`, `proyectar_creditos_contratados`) NO se modifican en su
    lógica interna. Toda la nueva orquestación vive en la capa de aplicación
    (`app_dash.py`) y en helpers puros y testeables como los de este módulo.
  - Toda entrada (fechas, retornos, niveles de TRM) se trata como NO confiable.

Componentes de este módulo (ver design.md, sección "Components and Interfaces"):
  - C1  meses_horizonte_inclusivo(inicio, fin)              <-- tarea 1.1
  - C2  convertir_ea_a_tramo(retorno_ea, meses_tramo)       <-- tarea 3.1
  - C3  interpolar_trm_tramo(trm_inicio, trm_cierre, ...)   <-- tarea 3.2
  - C3  SendasTramo / construir_sendas_tramo(...)           <-- tarea 3.3
  - C5  InputsPorTramo (dataclass con coerción/defaults)    <-- tarea 3.4

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


# ==============================================================================
# INSTRUMENTACIÓN DE TIEMPOS POR MOTOR Y TRAMO (Fix 3, Req 17.3)
# ==============================================================================

class CronometroTramos:
    """
    Acumulador simple de tiempos por ETAPA (motor) y por TRAMO para la proyección por
    tramos (Req 17.3). Permite identificar el componente más costoso ANTES de optimizar,
    sin volcar datos sensibles (solo nombres de etapa, índice de tramo y segundos).

    Uso::

        crono = CronometroTramos()
        with crono.medir("RF", tramo.indice):
            proyectar_portafolio(...)
        with crono.medir("Derivados", tramo.indice):
            _correr_derivados_tramo(...)
        ...
        crono.log_resumen()   # imprime el desglose al final de la corrida

    Es tolerante a fallos: si algo en la medición falla, no interrumpe la corrida.
    """

    def __init__(self) -> None:
        # {(etapa, tramo_indice): segundos_acumulados}
        self._tiempos: dict[tuple[str, int], float] = {}
        self._orden_etapas: list[str] = []

    @contextmanager
    def medir(self, etapa: str, tramo_indice: int):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            clave = (str(etapa), int(tramo_indice))
            self._tiempos[clave] = self._tiempos.get(clave, 0.0) + dt
            if etapa not in self._orden_etapas:
                self._orden_etapas.append(str(etapa))

    def log_resumen(self, logger_fn=None) -> str:
        """
        Devuelve (y loguea) un resumen legible: tiempo por etapa y tramo, totales por
        etapa y total global. No incluye datos sensibles. `logger_fn` por defecto usa
        `logger.info`; se puede pasar `print` para que salga al stdout del worker.
        """
        emit = logger_fn or logger.info
        if not self._tiempos:
            emit("[TIEMPOS] (sin mediciones)")
            return ""

        tramos = sorted({ti for (_e, ti) in self._tiempos})
        lineas = ["[TIEMPOS] Desglose por etapa y tramo (segundos):"]
        total_global = 0.0
        totales_etapa: dict[str, float] = {}
        for etapa in self._orden_etapas:
            parciales = []
            tot_etapa = 0.0
            for ti in tramos:
                seg = self._tiempos.get((etapa, ti), 0.0)
                tot_etapa += seg
                parciales.append(f"T{ti}={seg:.1f}")
            totales_etapa[etapa] = tot_etapa
            total_global += tot_etapa
            lineas.append(f"  {etapa:14s} | " + "  ".join(parciales) + f"  | total={tot_etapa:.1f}s")
        # Ranking de etapas por costo (lo más caro primero).
        ranking = sorted(totales_etapa.items(), key=lambda kv: kv[1], reverse=True)
        lineas.append("  --- Ranking por costo: " +
                      ", ".join(f"{e}={s:.1f}s" for e, s in ranking))
        lineas.append(f"  === TOTAL GLOBAL: {total_global:.1f}s ===")
        texto = "\n".join(lineas)
        emit(texto)
        return texto


# ==============================================================================
# COERCIÓN COMÚN DE ENTRADA NO CONFIABLE
# ==============================================================================

def _coerce_float_finito(valor, default: float = 0.0) -> float:
    """
    Coerciona ``valor`` a ``float`` finito. Entrada NO confiable (Req 11/12):

    - ``None``, cadenas vacías, valores no numéricos o no coercionables → ``default``.
    - ``NaN`` / ``+inf`` / ``-inf`` → ``default``.

    No emite logging ni vuelca el valor recibido: los llamadores que necesiten
    trazabilidad emiten su propio ``logger.warning`` genérico (sin filtrar el
    contenido), como exige la regla de no-filtrado de datos sensibles.
    """
    if valor is None:
        return default
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return x


# ==============================================================================
# C1 — CONTEO DE MESES INCLUSIVO (corrección del off-by-one)
# ==============================================================================

def meses_horizonte_inclusivo(inicio: date, fin: date) -> int:
    """
    Devuelve el número de meses de un periodo ``[inicio, fin]`` aplicando la regla de
    negocio ``Conteo_Meses_Inclusivo``:

        delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)

    donde ``delta = relativedelta(fin, inicio)``.

    Por qué corrige el off-by-one
    ------------------------------
    La fórmula histórica ``delta.years * 12 + delta.months`` descarta el residuo de
    días que deja ``relativedelta``. Para un año calendario completo
    (``1-ene`` → ``31-dic``) ``relativedelta`` da ``11 meses + 30 días``, por lo que la
    fórmula sin ajuste devuelve 11 y se pierde un mes de proyección. Al sumar el mes
    residual cuando quedan días sobrantes (``+ 1 si delta.days > 0``) el periodo anual
    cuenta 12 meses, que es lo esperado por el negocio. Esta es la misma fórmula ya
    usada correctamente en otras partes del código (p. ej.
    ``_calcular_uvr_cierre_desde_inflacion``); centralizarla aquí evita divergencias
    entre el modo por tramos y el orquestador consolidado punta a punta.

    Ejemplos
    --------
    - ``meses_horizonte_inclusivo(date(2026, 1, 1), date(2026, 12, 31))`` → 12
      (año calendario completo).
    - ``meses_horizonte_inclusivo(date(2027, 1, 1), date(2028, 1, 1))`` → 12
      (de un 1-ene al 1-ene siguiente).
    - ``meses_horizonte_inclusivo(date(2026, 8, 1), date(2026, 12, 31))`` → 5
      (tramo parcial ago-dic).

    Parámetros
    ----------
    inicio: date
        Fecha de inicio del periodo.
    fin: date
        Fecha de fin (corte) del periodo.

    Retorna
    -------
    int
        Número de meses inclusivo, ``>= 0``. NO se fuerza un mínimo aquí: el llamador
        aplica su propio mínimo (típicamente 1) según su contexto.
    """
    delta = relativedelta(fin, inicio)
    return delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)


# ==============================================================================
# C2 — CONVERSIÓN DE RETORNO EA A LA DURACIÓN DEL TRAMO (tarea 3.1)
# ==============================================================================

def convertir_ea_a_tramo(retorno_ea: float, meses_tramo: int) -> float:
    """
    Convierte un ``Retorno_EA`` (efectivo anual) al retorno equivalente para la duración
    de un tramo de ``meses_tramo`` meses, aplicando la regla de negocio
    ``Conversion_EA_A_Tramo`` (design C2, Req 5):

        (1 + retorno_ea) ** (meses_tramo / 12) - 1

    Casos de negocio
    ----------------
    - ``meses_tramo == 12`` → **identidad**: el resultado es exactamente ``retorno_ea``
      (un tramo de año completo aplica el retorno anual sin capitalización parcial)
      (Req 5.3).
    - ``meses_tramo < 12`` (``Tramo_Parcial``) → capitalización a ``meses_tramo/12`` de
      año; para ``retorno_ea > 0`` es estrictamente menor que ``retorno_ea`` (Req 5.4).

    Entrada NO confiable (Req 11/12)
    --------------------------------
    ``retorno_ea`` se coerciona a ``float`` finito: cualquier valor no numérico, ``None``,
    ``NaN`` o ``inf`` se trata como ``0.0`` (retorno nulo del tramo), sin volcar el valor
    recibido en logs.

    Parámetros
    ----------
    retorno_ea: float
        Retorno efectivo anual del activo en el tramo (p. ej. ``0.1433`` = 14.33% EA).
    meses_tramo: int
        Número de meses del tramo (``Sub_Horizonte``). Debe ser ``>= 1``.

    Retorna
    -------
    float
        Retorno equivalente para la duración del tramo, ``(1+r)^(m/12) - 1``.

    Lanza
    -----
    ValueError
        Si ``meses_tramo < 1`` (un tramo debe cubrir al menos un mes de proyección).

    Ejemplos
    --------
    - ``convertir_ea_a_tramo(0.1433, 12)`` → ``0.1433`` (identidad, año completo).
    - ``convertir_ea_a_tramo(0.1433, 5)`` → ``(1.1433) ** (5/12) - 1`` (tramo parcial).
    - ``convertir_ea_a_tramo("no-numerico", 6)`` → ``0.0`` (coerción de entrada inválida).
    """
    if meses_tramo < 1:
        raise ValueError("meses_tramo debe ser >= 1 para convertir un retorno EA al tramo.")

    r = _coerce_float_finito(retorno_ea, default=0.0)
    return (1.0 + r) ** (meses_tramo / 12.0) - 1.0


# ==============================================================================
# C3 — INTERPOLACIÓN LINEAL DE LA TRM DENTRO DE UN TRAMO (tarea 3.2)
# ==============================================================================

def interpolar_trm_tramo(
    trm_inicio: float,
    trm_cierre: float,
    periodos: list[date],
) -> dict[str, float]:
    """
    Construye la senda de TRM del tramo interpolando LINEALMENTE desde ``trm_inicio``
    (nivel al inicio del tramo = TRM de cierre del tramo anterior, o TRM t0 para el
    primer tramo) hasta ``trm_cierre`` (``TRM_Por_Tramo``, nivel al ``Fecha_Corte_Tramo``)
    sobre los cortes mensuales ``periodos`` (design D4, Req 6).

    La senda resultante es ``{'YYYY-MM-DD': trm}`` con las claves en el mismo formato que
    consumen los motores (``periodo.strftime("%Y-%m-%d")``), en el mismo orden ascendente
    de ``periodos``.

    Invariantes (Property 5 del design)
    ------------------------------------
    - El ÚLTIMO valor de la senda es EXACTAMENTE ``trm_cierre`` (nivel objetivo al corte),
      sin error de redondeo de la interpolación (Req 6.1, 6.2).
    - El PRIMER valor corresponde al primer periodo: con ``n`` cortes es
      ``trm_inicio + (trm_cierre - trm_inicio) * 1/n`` (el inicio del tramo es el cierre
      del tramo anterior; el primer corte ya ha avanzado un paso). Con un único corte, el
      primer (y último) valor es ``trm_cierre``.
    - La senda es monótona entre ``trm_inicio`` y ``trm_cierre`` (creciente o decreciente
      según el signo de ``trm_cierre - trm_inicio``).

    Entrada NO confiable (Req 11/12)
    --------------------------------
    ``trm_inicio`` y ``trm_cierre`` se coercionan a ``float`` finito (no numérico/None/
    NaN/inf → ``0.0``), sin volcar el valor recibido.

    Parámetros
    ----------
    trm_inicio: float
        Nivel de TRM al inicio del tramo (cierre del tramo anterior o TRM t0).
    trm_cierre: float
        Nivel de TRM al corte del tramo (``TRM_Por_Tramo``).
    periodos: list[date]
        Cortes mensuales del tramo en orden ascendente (típicamente fines de mes).

    Retorna
    -------
    dict[str, float]
        Senda ``{'YYYY-MM-DD': trm}``. Vacío si ``periodos`` está vacío.

    Ejemplo
    -------
    Con ``trm_inicio=3337``, ``trm_cierre=3484`` y 5 cortes: cada corte añade
    ``(3484-3337)/5 = 29.4``; el último valor es exactamente ``3484``.
    """
    ini = _coerce_float_finito(trm_inicio, default=0.0)
    fin = _coerce_float_finito(trm_cierre, default=0.0)

    n = len(periodos)
    if n == 0:
        return {}

    senda: dict[str, float] = {}
    paso = (fin - ini) / n  # el inicio del tramo es t0 (cierre previo); n pasos hasta el cierre
    for i, periodo in enumerate(periodos):
        fecha_str = periodo.strftime("%Y-%m-%d")
        if i == n - 1:
            # Anclar el último corte EXACTAMENTE al nivel de cierre (sin residuo de paso).
            senda[fecha_str] = fin
        else:
            senda[fecha_str] = ini + paso * (i + 1)
    return senda


def interpolar_nivel_tramo(
    nivel_inicio: float,
    nivel_cierre: float,
    n_meses: int,
) -> list[float]:
    """
    Interpola LINEALMENTE un nivel macro (repo o inflación) dentro de un tramo, desde
    ``nivel_inicio`` (índice 0) hasta ``nivel_cierre`` (índice ``n_meses``), devolviendo
    una LISTA de ``n_meses + 1`` valores indexada por mes del horizonte del tramo.

    Diferencia con :func:`interpolar_trm_tramo`
    -------------------------------------------
    ``interpolar_trm_tramo`` produce un ``dict[fecha -> trm]`` donde el PRIMER punto ya ha
    avanzado un paso (el inicio del tramo es el cierre del tramo previo y el primer corte
    corresponde al primer mes proyectado). Aquí, en cambio, se devuelve una LISTA por mes
    con el ÍNDICE 0 == ``nivel_inicio`` porque así la consume el motor RF: ``senda[m]`` con
    ``m`` en ``0..n_meses`` (el motor indexa por mes y NO interpola internamente), por lo
    que el índice 0 representa el nivel al arranque del tramo.

    Invariantes
    -----------
    - La lista tiene EXACTAMENTE ``n_meses + 1`` elementos (longitud ``sub_horizonte + 1``).
    - ``resultado[0] == nivel_inicio`` (nivel al inicio del tramo).
    - ``resultado[n_meses] == nivel_cierre`` EXACTO (anclado sin residuo de paso).
    - Monótona entre ``nivel_inicio`` y ``nivel_cierre`` (creciente/decreciente según el
      signo de ``nivel_cierre - nivel_inicio``).

    Entrada NO confiable (Req 11/12)
    --------------------------------
    ``nivel_inicio`` y ``nivel_cierre`` se coercionan a ``float`` finito (no numérico/None/
    NaN/inf → ``0.0``), sin volcar el valor recibido.

    Parámetros
    ----------
    nivel_inicio: float
        Nivel al inicio del tramo (cierre del nivel del tramo anterior, o el nivel vigente
        para el primer tramo). En % anual cuando se usa para repo/inflación del motor RF.
    nivel_cierre: float
        Nivel al cierre del tramo (nivel objetivo del tramo, en % anual).
    n_meses: int
        Número de meses del tramo (``Sub_Horizonte``). Debe ser ``>= 0``; con ``0`` la
        lista tiene un único elemento igual a ``nivel_cierre``.

    Retorna
    -------
    list[float]
        Lista de ``n_meses + 1`` niveles interpolados, del inicio (índice 0) al cierre
        (índice ``n_meses``).

    Ejemplo
    -------
    ``interpolar_nivel_tramo(10, 6.5, 12)`` → lista de 13 elementos, ``[0] == 10``,
    ``[12] == 6.5``, monótona decreciente.
    """
    ini = _coerce_float_finito(nivel_inicio, default=0.0)
    fin = _coerce_float_finito(nivel_cierre, default=0.0)

    n = int(n_meses) if n_meses and n_meses > 0 else 0
    if n <= 0:
        # Sin meses de transición: un único punto anclado al nivel de cierre.
        return [fin]

    paso = (fin - ini) / n  # n pasos desde el inicio (índice 0) hasta el cierre (índice n)
    senda: list[float] = []
    for m in range(n + 1):
        if m == 0:
            senda.append(ini)
        elif m == n:
            # Anclar el último punto EXACTAMENTE al nivel de cierre (sin residuo de paso).
            senda.append(fin)
        else:
            senda.append(ini + paso * m)
    return senda


# ==============================================================================
# C5 — INPUTS POR TRAMO (captura de UI, entrada NO confiable) (tarea 3.4)
# ==============================================================================

# Defaults documentados por campo (Req 1.3, 11.1). Se aplican cuando el valor de UI es no
# numérico / vacío / no finito. Se eligen valores macro plausibles para Colombia de modo
# que una corrida nunca se bloquee por un campo vacío:
#   - inflacion_ea = 0.05   → IPC objetivo de largo plazo (~5% EA).
#   - repo_ea      = 0.09   → tasa de política monetaria plausible (~9% EA).
#   - trm_cierre   = 4000.0 → nivel de TRM plausible y estrictamente > 0.
#   - movimientos y spreads en bps = 0.0 → sin shift respecto de la curva base.
#   - retornos EA (acciones, alternativos, PEI) = 0.0 → sin retorno adicional supuesto.
#   - ret_alt_ipc_bps = 0.0 → spread sobre IPC nulo para FCP 4G por defecto.
_DEFAULTS_INPUTS_TRAMO: dict[str, float] = {
    "inflacion_ea": 0.05,
    "repo_ea": 0.09,
    "mov_tes_cop_bps": 0.0,
    "mov_tes_uvr_bps": 0.0,
    "spread_corp_ipc_bps": 0.0,
    "spread_corp_tf_bps": 0.0,
    "trm_cierre": 4000.0,
    "ret_acc_local_ea": 0.0,
    "ret_acc_intl_ea": 0.0,
    "ret_alt_local_ea": 0.0,
    "ret_alt_intl_ea": 0.0,
    "ret_alt_ipc_bps": 0.0,
    "pei_ea": 0.0,
    # ── Extensión derivados por tramo (Req 13.4): deltas de curva del Motor_Derivados ──
    "delta_fed_bps": 0.0,          # Δ tasa FED del tramo (ancla corta LUTIBR)
    "delta_tesoros10y_bps": 0.0,   # Δ tesoros 10Y del tramo (ancla larga LUTIBR)
}

# ──────────────────────────────────────────────────────────────────────────────
# Banda plausible de nivel COP/USD de la TRM de cierre (bugfix 2B: escala TRM).
#
# Única fuente de verdad para la normalización de UNIDAD de ``trm_cierre`` (ruta por
# tramos) y ``trm_final`` (multiescenarios). La banda es ancha a propósito: cubre todos
# los niveles reales y de estrés plausibles de la TRM COP/USD, pero es suficientemente
# estrecha para detectar errores de unidad de ×1000 (un ``3.9`` o un ``3_900_000`` quedan
# claramente fuera). No es un criterio de pronóstico, solo de escala/unidad.
# ──────────────────────────────────────────────────────────────────────────────
TRM_MIN_PLAUSIBLE = 500.0
TRM_MAX_PLAUSIBLE = 20000.0


def normalizar_trm_nivel(trm: float) -> float:
    """
    Normaliza la UNIDAD de un nivel de TRM COP/USD (bugfix 2B, Property 1/2/4).

    Precondición
    ------------
    ``trm`` ya debe venir coercionado a ``float`` finito por el llamador (el caso
    vacío/no numérico lo intercepta antes el flujo de default de ``from_valores``; este
    helper NO maneja ese caso y asume un número finito).

    Reglas
    ------
    - Si ``trm`` ya está en la banda plausible ``[TRM_MIN_PLAUSIBLE, TRM_MAX_PLAUSIBLE]``
      ⇒ se devuelve SIN cambios (identidad; una TRM correcta nunca se reescala, Property 2).
    - Si está fuera de banda pero es un error de unidad recuperable (``trm × 1000^k`` cae
      en banda para algún ``k ≠ 0``) ⇒ se devuelve el nivel normalizado, eligiendo el ``k``
      de menor ``|k|`` (el ajuste de unidad más cercano), y se emite un ``logger.warning``
      GENÉRICO que NO vuelca el valor crudo (Property 1/3, regla de no-filtrado).
    - Si ningún factor de 1000 lo lleva a banda (dato arbitrario) ⇒ se lanza ``ValueError``
      con mensaje genérico: no se valora en silencio con una TRM dudosa (Property 4).

    Returns
    -------
    float
        El nivel de TRM normalizado dentro de la banda plausible.

    Raises
    ------
    ValueError
        Si ``trm`` es finito, fuera de banda y NO normalizable por factores de 1000.
    """
    if TRM_MIN_PLAUSIBLE <= trm <= TRM_MAX_PLAUSIBLE:
        return trm

    # Barrido por |k| ascendente (ajuste de unidad más cercano primero), k ≠ 0.
    for k in (-1, 1, -2, 2, -3, 3):
        candidato = trm * (1000.0**k)
        if TRM_MIN_PLAUSIBLE <= candidato <= TRM_MAX_PLAUSIBLE:
            # Warning GENÉRICO: traza que hubo corrección de unidad, sin volcar el valor.
            logger.warning(
                "normalizar_trm_nivel: TRM de cierre fuera de banda; se normalizó la "
                "unidad por un factor de 1000 al nivel plausible."
            )
            return candidato

    # Fuera de banda y no normalizable por 1000: política explícita y trazable.
    raise ValueError(
        "TRM de cierre fuera de banda plausible; revisa la unidad del input."
    )


@dataclass(frozen=True)
class InputsPorTramo:
    """
    Juego completo de variables de ``Inputs_Por_Tramo`` para UN tramo (uno por fin de
    año), según la tabla del usuario (design C5, Req 1.1, 11.1).

    Campos
    ------
    - inflacion_ea:         Inflación (IPC) EA del tramo (``Inflacion_Por_Tramo``).
    - repo_ea:              Tasa repo EA del tramo (``Repo_Por_Tramo``).
    - mov_tes_cop_bps:      Movimiento de la curva TES COP, en bps.
    - mov_tes_uvr_bps:      Movimiento de la curva TES UVR, en bps.
    - spread_corp_ipc_bps:  Spread corporativo sobre IPC, en bps.
    - spread_corp_tf_bps:   Spread corporativo sobre tasa fija, en bps.
    - trm_cierre:           TRM de cierre del tramo (``TRM_Por_Tramo``, nivel al corte).
    - ret_acc_local_ea:     Retorno EA de acciones locales (COLCAP).
    - ret_acc_intl_ea:      Retorno EA de acciones internacionales (MXWD), en moneda origen.
    - ret_alt_local_ea:     Retorno EA de alternativos locales.
    - ret_alt_intl_ea:      Retorno EA de alternativos internacionales.
    - ret_alt_ipc_bps:      Spread sobre IPC (bps) para FCP 4G indexados a IPC.
    - pei_ea:               Retorno EA del PEI.
    - delta_fed_bps:        Δ tasa FED del tramo, en bps (``Delta_Fed_Tramo``, Req 13.4).
    - delta_tesoros10y_bps: Δ tesoros 10Y del tramo, en bps (``Delta_Tesoros10Y_Tramo``).

    Todos los retornos son ``Retorno_EA`` (Req 5.1). La instancia es inmutable
    (``frozen=True``); use :meth:`from_valores` para construirla a partir de entrada NO
    confiable con coerción/validación y defaults.
    """
    inflacion_ea: float
    repo_ea: float
    mov_tes_cop_bps: float
    mov_tes_uvr_bps: float
    spread_corp_ipc_bps: float
    spread_corp_tf_bps: float
    trm_cierre: float
    ret_acc_local_ea: float
    ret_alt_local_ea: float
    ret_alt_intl_ea: float
    ret_alt_ipc_bps: float
    pei_ea: float
    ret_acc_intl_ea: float = 0.0
    delta_fed_bps: float = 0.0
    delta_tesoros10y_bps: float = 0.0

    @classmethod
    def from_valores(
        cls,
        inflacion_ea=None,
        repo_ea=None,
        mov_tes_cop_bps=None,
        mov_tes_uvr_bps=None,
        spread_corp_ipc_bps=None,
        spread_corp_tf_bps=None,
        trm_cierre=None,
        ret_acc_local_ea=None,
        ret_alt_local_ea=None,
        ret_alt_intl_ea=None,
        ret_alt_ipc_bps=None,
        pei_ea=None,
        ret_acc_intl_ea=None,
        delta_fed_bps=None,
        delta_tesoros10y_bps=None,
    ) -> "InputsPorTramo":
        """
        Construye un :class:`InputsPorTramo` desde valores de UI (entrada NO confiable),
        coercionando cada campo a ``float`` finito (Req 1.3, 11.1, 11.4).

        Para cada campo, si el valor es no numérico / vacío / ``None`` / no finito
        (``NaN``/``inf``), se sustituye por el default documentado del campo
        (``_DEFAULTS_INPUTS_TRAMO``) y se emite un ``logger.warning`` GENÉRICO que NO
        vuelca el valor recibido (regla de no filtrado de datos sensibles). Los valores
        válidos se conservan tal cual (Property 12).

        No aborta nunca por un campo inválido: la corrida continúa con el default, de modo
        que un campo vacío en la UI no bloquea la proyección (Req 1.3).
        """
        crudos: dict[str, object] = {
            "inflacion_ea": inflacion_ea,
            "repo_ea": repo_ea,
            "mov_tes_cop_bps": mov_tes_cop_bps,
            "mov_tes_uvr_bps": mov_tes_uvr_bps,
            "spread_corp_ipc_bps": spread_corp_ipc_bps,
            "spread_corp_tf_bps": spread_corp_tf_bps,
            "trm_cierre": trm_cierre,
            "ret_acc_local_ea": ret_acc_local_ea,
            "ret_alt_local_ea": ret_alt_local_ea,
            "ret_alt_intl_ea": ret_alt_intl_ea,
            "ret_alt_ipc_bps": ret_alt_ipc_bps,
            "pei_ea": pei_ea,
            "ret_acc_intl_ea": ret_acc_intl_ea,
            "delta_fed_bps": delta_fed_bps,
            "delta_tesoros10y_bps": delta_tesoros10y_bps,
        }

        coercionados: dict[str, float] = {}
        campos_a_default: list[str] = []
        for campo, crudo in crudos.items():
            default = _DEFAULTS_INPUTS_TRAMO[campo]
            # Sentinela: si el valor NO es coercionable, _coerce_float_finito devuelve el
            # default; para saber si hubo caída al default probamos con dos sentinelas
            # distintos (evita falsos positivos cuando el valor válido == default).
            val_a = _coerce_float_finito(crudo, default=0.0)
            val_b = _coerce_float_finito(crudo, default=1.0)
            if val_a != val_b:
                # Valor no coercionable (el default cambió el resultado) → usar default.
                coercionados[campo] = default
                campos_a_default.append(campo)
            else:
                coercionados[campo] = val_a

        if campos_a_default:
            # Warning GENÉRICO: cuenta de campos, sin volcar valores ni nombres sensibles.
            logger.warning(
                "InputsPorTramo: %d campo(s) inválido(s)/vacío(s) sustituido(s) por su "
                "default documentado.",
                len(campos_a_default),
            )

        # Normalización de UNIDAD de la TRM de cierre (bugfix 2B, Property 1/2/4). SOLO se
        # aplica cuando el valor crudo era un número finito; si cayó al default por
        # vacío/no numérico (`trm_cierre` en `campos_a_default`) se mantiene el default
        # documentado `4000.0` tal cual (ese caso NO es parte del defecto 2B, Req 3.2).
        # Los demás campos quedan EXACTAMENTE igual: la guarda solo toca `trm_cierre`
        # (Property 5, Req 3.5).
        if "trm_cierre" not in campos_a_default:
            coercionados["trm_cierre"] = normalizar_trm_nivel(coercionados["trm_cierre"])

        return cls(**coercionados)


# ==============================================================================
# C3 — SENDAS POR TRAMO (TRM interpolada + IPC/repo/IBR/SOFR planas) (tarea 3.3)
# ==============================================================================

@dataclass(frozen=True)
class SendasTramo:
    """
    Sendas por corte mensual de un tramo (design "Data Models", Req 6/7). Cada senda es un
    ``dict[str, float]`` con claves ``'YYYY-MM-DD'`` (formato que consumen los motores).

    - senda_trm:  TRM interpolada linealmente al nivel de cierre del tramo (D4).
    - senda_ipc:  ``{fecha: ipc_ea}`` plana al nivel del tramo (``Inflacion_Por_Tramo``).
    - senda_repo: ``{fecha: repo_ea}`` plana al nivel del tramo (``Repo_Por_Tramo``).
    - senda_ibr:  ``{fecha: ibr}`` para créditos (derivada del repo del tramo).
    - senda_sofr: ``{fecha: sofr}`` para créditos USD (plana; supuesto documentado).
    """
    senda_trm: dict[str, float]
    senda_ipc: dict[str, float]
    senda_repo: dict[str, float]
    senda_ibr: dict[str, float]
    senda_sofr: dict[str, float]
    # Nivel REAL de arranque del tramo (ancla de conversión USD para RV/Alt
    # internacional). NO modifica los nodos mensuales de senda_trm. Default 0.0
    # retrocompatible: los llamadores que no lo fijan caen al comportamiento previo
    # (senda[primera_fecha]) en proyectar_bloque.
    trm_inicio_real: float = 0.0


def _cortes_mensuales_tramo(fecha_inicio: date, fecha_corte: date) -> list[date]:
    """
    Construye los cortes mensuales de un tramo ``[fecha_inicio, fecha_corte]`` reutilizando
    el patrón ``relativedelta(months=...)`` ya usado por los motores del proyecto
    (p. ej. ``motor_proyeccion``: ``fecha_inicio + relativedelta(months=mes)`` para
    ``mes`` en ``1..meses_horizonte``).

    El primer corte es ``fecha_inicio + 1 mes`` y el último es el corte del tramo (se
    ancla exactamente a ``fecha_corte`` para que el último punto de la senda coincida con
    el ``Fecha_Corte_Tramo``). El número de cortes es ``meses_horizonte_inclusivo`` del
    tramo (mínimo 1), coherente con el ``Sub_Horizonte`` que consume el motor.

    NO modifica los motores; sólo replica su convención de avance mensual.
    """
    meses = max(meses_horizonte_inclusivo(fecha_inicio, fecha_corte), 1)
    cortes: list[date] = []
    for mes in range(1, meses + 1):
        if mes == meses:
            # Anclar el último corte al Fecha_Corte_Tramo exacto.
            cortes.append(fecha_corte)
        else:
            cortes.append(fecha_inicio + relativedelta(months=mes))
    return cortes


def construir_sendas_tramo(tramo, inputs_tramo: InputsPorTramo, estado_previo=None,
                           trm_t0_real=None) -> SendasTramo:
    """
    Construye las :class:`SendasTramo` de un tramo a partir de sus ``InputsPorTramo``
    (design C3, Req 6/7).

    Parámetros
    ----------
    tramo:
        Un ``Tramo`` de ``proyeccion_tramos.Tramo`` (tiene ``fecha_inicio``,
        ``fecha_corte`` y ``sub_horizonte``). Se usa para construir los cortes mensuales.
    inputs_tramo: InputsPorTramo
        Inputs del tramo (ya coercionados; ver :meth:`InputsPorTramo.from_valores`).
    estado_previo: optional
        Estado de cierre del tramo anterior. Si expone ``trm_cierre`` (o es un ``float``),
        se usa como TRM de INICIO del tramo (nivel al inicio = cierre del tramo previo,
        Req 6.2). Si es ``None`` (primer tramo), la TRM de inicio es la ``trm_cierre`` del
        propio tramo (senda plana en su nivel objetivo, sin trayectoria previa conocida);
        como último fallback se usa el default de TRM documentado.

    Construcción
    ------------
    - Cortes mensuales del tramo con :func:`_cortes_mensuales_tramo` (patrón
      ``relativedelta(months=...)`` del proyecto).
    - ``senda_trm``: :func:`interpolar_trm_tramo` desde la TRM de inicio (cierre previo o
      fallback) hasta ``inputs_tramo.trm_cierre`` (nivel al corte). Último valor exacto
      == ``trm_cierre`` (D4, Req 6.1).
    - ``senda_ipc`` / ``senda_repo``: PLANAS al nivel del tramo
      (``inputs_tramo.inflacion_ea`` / ``inputs_tramo.repo_ea``) en cada corte, para
      propagarlas al motor RF y a los activos indexados a IPC del tramo (Req 7.1–7.3).

    Sendas de créditos (supuestos documentados)
    -------------------------------------------
    - ``senda_ibr``: PLANA al nivel del repo del tramo (``inputs_tramo.repo_ea``).
      **Supuesto:** el IBR se aproxima por la tasa de política monetaria (repo) del tramo,
      dado que ``Inputs_Por_Tramo`` no captura un IBR independiente; el spread crediticio
      sobre el IBR vive en las condiciones de cada crédito, no en la senda.
    - ``senda_sofr``: PLANA en ``0.0``. **Supuesto:** ``Inputs_Por_Tramo`` no captura una
      senda SOFR; se deja en ``0.0`` (nivel neutro) para los créditos USD hasta que exista
      un input dedicado, sin alterar la firma del ``Motor_Creditos``.

    Retorna
    -------
    SendasTramo
        Las cinco sendas del tramo, listas para pasar a los motores por corte.
    """
    fecha_inicio = tramo.fecha_inicio
    fecha_corte = tramo.fecha_corte
    cortes = _cortes_mensuales_tramo(fecha_inicio, fecha_corte)

    # TRM de inicio del tramo = cierre del tramo anterior (Req 6.2), con fallbacks seguros.
    trm_cierre = _coerce_float_finito(
        inputs_tramo.trm_cierre, default=_DEFAULTS_INPUTS_TRAMO["trm_cierre"]
    )
    trm_inicio: float
    trm_inicio_real: float
    if estado_previo is None:
        # Primer tramo: sin trayectoria previa; se ancla al nivel de cierre del propio
        # tramo (senda plana en su nivel objetivo). La senda intermedia NO cambia.
        trm_inicio = trm_cierre
        # Nivel REAL de arranque del tramo 0 = TRM t0 real (informe 597) inyectada por el
        # orquestador. Fallback retrocompatible: trm_cierre (comportamiento previo).
        trm_inicio_real = _coerce_float_finito(trm_t0_real, default=trm_cierre)
    else:
        trm_previa = getattr(estado_previo, "trm_cierre", estado_previo)
        trm_inicio = _coerce_float_finito(
            trm_previa, default=_DEFAULTS_INPUTS_TRAMO["trm_cierre"]
        )
        # i>0: el inicio real del tramo == cierre del tramo anterior (encadenamiento).
        trm_inicio_real = trm_inicio

    senda_trm = interpolar_trm_tramo(trm_inicio, trm_cierre, cortes)

    ipc = _coerce_float_finito(
        inputs_tramo.inflacion_ea, default=_DEFAULTS_INPUTS_TRAMO["inflacion_ea"]
    )
    repo = _coerce_float_finito(
        inputs_tramo.repo_ea, default=_DEFAULTS_INPUTS_TRAMO["repo_ea"]
    )

    senda_ipc = {corte.strftime("%Y-%m-%d"): ipc for corte in cortes}
    senda_repo = {corte.strftime("%Y-%m-%d"): repo for corte in cortes}
    # Créditos (supuestos documentados en el docstring).
    senda_ibr = {corte.strftime("%Y-%m-%d"): repo for corte in cortes}
    senda_sofr = {corte.strftime("%Y-%m-%d"): 0.0 for corte in cortes}

    return SendasTramo(
        senda_trm=senda_trm,
        senda_ipc=senda_ipc,
        senda_repo=senda_repo,
        senda_ibr=senda_ibr,
        senda_sofr=senda_sofr,
        trm_inicio_real=trm_inicio_real,
    )


# ==============================================================================
# C3 — CAPITALIZACIÓN Y ENCADENAMIENTO DE LA UVR POR TRAMO
# (spec uvr-capitalizacion-por-tramos, tarea 3.3)
# ==============================================================================

def construir_senda_uvr_por_tramos(tramos, inflaciones_ea_por_tramo, uvr_t0) -> list[dict]:
    """
    Construye la senda de UVR por tramo (``uvr_inicio`` / ``uvr_cierre``) capitalizando la
    UVR con la inflación anual de cada tramo, escalada por la fracción de año que cubre el
    tramo, y ENCADENANDO el cierre de un tramo como inicio del siguiente (spec
    ``uvr-capitalizacion-por-tramos``, Property 1; Req 2.1, 2.3, 2.4, 2.5, 2.6).

    Esta es la MISMA semántica de capitalización que usa la orquestación en el loop de
    ``_ejecutar_escenario_por_tramos`` (``app_dash.py``), extraída a un helper puro y
    testeable para evitar divergencias entre lo que calcula la orquestación y lo que se
    verifica en las pruebas de propiedad.

    Fórmula por tramo (idéntica al in-loop)::

        factor_tramo_i = (1 + infl_ea_i) ** (sub_horizonte_i / 12)
        uvr_cierre_i   = uvr_inicio_i * factor_tramo_i

    con encadenamiento::

        uvr_inicio_0     = float(uvr_t0)
        uvr_inicio_{i+1} = uvr_cierre_i        # materializado vía EstadoEncadenado.uvr_cierre

    El exponente ``sub_horizonte_i / 12`` cubre tanto el primer tramo PARCIAL (p. ej.
    ``5/12``) como los tramos anuales completos (``12/12 == 1``), de modo que el escalado
    del primer tramo (Req 2.3) es un caso particular de la misma fórmula. Con
    ``infl_ea == 0`` ⇒ ``factor_tramo == 1`` ⇒ la UVR queda constante en el tramo
    (comportamiento preservado, Req 3.6). Para ``infl_ea >= 0`` ⇒ ``factor_tramo >= 1`` ⇒
    la UVR no decrece (monotonía no decreciente, Req 2.5).

    Parámetros
    ----------
    tramos:
        Lista de ``Tramo`` (``proyeccion_tramos.Tramo``) en orden cronológico. Se usa
        ``tramo.indice`` y ``tramo.sub_horizonte`` (meses del tramo).
    inflaciones_ea_por_tramo:
        Secuencia de inflaciones EA en DECIMAL (p. ej. ``0.05``) alineada 1:1 con
        ``tramos``. Se coerciona cada valor a ``float`` finito (entrada NO confiable;
        inválido/vacío ⇒ ``0.0``, es decir UVR constante en ese tramo).
    uvr_t0:
        Nivel de UVR al inicio del PRIMER tramo. Entrada NO confiable: si está vacía o no
        es válida (``None``, cadena vacía, ``0``, ``0.0``, no numérica, ``NaN``/``inf``)
        se ABORTA con ``ValueError`` (eliminación del hardcode ``411.0``, Req 2.6),
        idéntico en espíritu al patrón de la TRM.

    Retorna
    -------
    list[dict]
        Una entrada por tramo con las claves ``{"indice", "uvr_inicio", "uvr_cierre"}``,
        en el mismo orden que ``tramos``.

    Lanza
    -----
    ValueError
        Si ``uvr_t0`` no es un nivel de UVR válido y estrictamente positivo (sin
        inyectar el hardcode ``411.0``).
    """
    # Guarda de UVR t0 real: entrada NO confiable. Sin un nivel válido y > 0 se ABORTA con
    # error verificable (NOT contiene_hardcode(411.0), Req 2.6).
    if uvr_t0 is None or (isinstance(uvr_t0, str) and uvr_t0.strip() == ""):
        raise ValueError(
            "uvr_t0 ausente: no hay UVR t0 real para capitalizar la senda por tramos "
            "(se elimina el hardcode 411.0)."
        )
    try:
        uvr_t0_val = float(uvr_t0)
    except (TypeError, ValueError):
        raise ValueError(
            "uvr_t0 no es un nivel de UVR numérico válido para la senda por tramos."
        )
    if not math.isfinite(uvr_t0_val) or uvr_t0_val <= 0.0:
        raise ValueError(
            "uvr_t0 debe ser un nivel de UVR finito y estrictamente positivo "
            "(se elimina el hardcode 411.0)."
        )

    senda: list[dict] = []
    # El inicio del PRIMER tramo es exactamente uvr_t0; a partir de ahí se encadena vía
    # EstadoEncadenado.uvr_cierre (análogo a trm_cierre), para materializar el mecanismo
    # de encadenamiento que la orquestación arrastra entre tramos.
    estado_previo = None  # EstadoEncadenado del tramo anterior (None en el primero)
    for i, tramo in enumerate(tramos):
        infl_crudo = (
            inflaciones_ea_por_tramo[i]
            if i < len(inflaciones_ea_por_tramo)
            else 0.0
        )
        infl_ea = _coerce_float_finito(infl_crudo, default=0.0)
        meses_tramo = tramo.sub_horizonte
        factor_tramo = (1.0 + infl_ea) ** (meses_tramo / 12.0)

        # Encadenamiento: inicio del tramo = cierre del tramo previo (vía EstadoEncadenado),
        # o uvr_t0 en el primer tramo.
        if estado_previo is not None and getattr(estado_previo, "uvr_cierre", None) is not None:
            uvr_inicio = float(estado_previo.uvr_cierre)
        else:
            uvr_inicio = uvr_t0_val

        uvr_cierre_tramo = uvr_inicio * factor_tramo

        senda.append({
            "indice": tramo.indice,
            "uvr_inicio": uvr_inicio,
            "uvr_cierre": uvr_cierre_tramo,
        })

        # Arrastrar el cierre como inicio del siguiente tramo, materializado en el mismo
        # EstadoEncadenado que usa la orquestación (campo uvr_cierre, tarea 3.1).
        estado_previo = EstadoEncadenado(uvr_cierre=uvr_cierre_tramo)

    return senda


# ==============================================================================
# C4 — ESTADO ENCADENADO NO-RF Y HELPERS DE ENCADENAMIENTO (tarea 5.1)
# ==============================================================================

@dataclass
class EstadoEncadenado:
    """
    Estado de cierre por activo NO-RF que se arrastra entre tramos (design D1, C4;
    Req 4.1, 4.2).

    En el modo ``por_tramos``, el estado de cierre de un tramo (valor de mercado / saldo
    proyectado al ``Fecha_Corte_Tramo``) es el estado INICIAL (t0) del mismo activo en el
    tramo siguiente (``Encadenamiento``). A diferencia de RF —que encadena vía snapshots
    autosuficientes en ``portafolio_clasificado_proy``— los activos de retorno (RV,
    Alternativos) y los Créditos arrastran un estado simple en memoria: el VM/saldo de
    cierre por posición. No requiere tablas nuevas.

    Ecuación de encadenamiento (activos de retorno RV/Alternativos; ver design "Ecuación
    de encadenamiento" y :func:`escalar_vm_inicial`)::

        VM_inicio(tramo_i) = VM_cierre(tramo_{i-1})
        VM_cierre(tramo_i) = VM_inicio(tramo_i) × (1 + retorno_tramo_i)

    El VM de cierre del tramo ``i-1`` se INYECTA en la columna ``valor_mercado`` del
    portafolio que se pasa al motor en el tramo ``i`` (mecanismo verificado en
    ``proyectar_bloque``, que lee el VM inicial de esa columna).

    Campos
    ------
    - vm_rv_por_posicion:   ``{id_posicion: VM_cierre}`` de Renta Variable (local/intl/PEI).
    - vm_alt_por_posicion:  ``{id_posicion: VM_cierre}`` de Alternativos (local/intl/4G).
    - saldo_credito_por_id: ``{id_credito: saldo_cierre}`` de Créditos contratados.
    - trm_cierre:           TRM de cierre del tramo. La consume
                            :func:`construir_sendas_tramo` como TRM de INICIO del tramo
                            siguiente (``estado_previo.trm_cierre``, Req 6.2). ``None`` en
                            el estado inicial (primer tramo, sin trayectoria previa).
    - repo_cierre:          Nivel de repo (% anual) al cierre del tramo. Se arrastra como
                            nivel de INICIO del repo del tramo siguiente para interpolar la
                            senda de repo entre tramos, análogo a ``trm_cierre``. ``None``
                            en el estado inicial (primer tramo, sin trayectoria previa).
    - inflacion_cierre:     Nivel de inflación/IPC (% anual) al cierre del tramo. Se arrastra
                            como nivel de INICIO de la inflación del tramo siguiente para
                            interpolar la senda de inflación entre tramos, análogo a
                            ``trm_cierre``. ``None`` en el estado inicial (primer tramo).
    - uvr_cierre:           Nivel de UVR al cierre del tramo. Se arrastra como nivel de
                            UVR de INICIO del tramo siguiente
                            (``uvr_cierre(tramo_i) = uvr_inicio(tramo_{i+1})``), análogo a
                            ``trm_cierre``. ``None`` en el estado inicial (primer tramo,
                            sin trayectoria previa).
    - delta_curva_acumulado: Delta de curva ACUMULADO de derivados (dict por tipo de curva:
                            ``delta_tes_cop``/``delta_tes_uvr``/``delta_fed_bps``/
                            ``delta_10t_bps``) al cierre del tramo. Se arrastra como nivel
                            de ARRANQUE de la curva de valoración de derivados del tramo
                            siguiente (análogo a ``repo_cierre``/``inflacion_cierre``):
                            arrastra el delta de curva acumulado de derivados entre tramos
                            para que la curva avance de forma continua (igual que la senda
                            punta a punta), en vez de "devolverse" al base en cada tramo.
                            ``None`` en el estado inicial (primer tramo, sin trayectoria
                            previa).

    Notas
    -----
    Para activos internacionales el VM de cierre por posición YA está expresado en COP e
    incluye el efecto TRM del tramo; al arrastrarlo como VM inicial del tramo siguiente el
    motor lo reconvierte internamente a moneda origen con la TRM inicial del nuevo tramo
    (design "Componente cambiario"). Este estado no distingue moneda: sólo transporta el
    VM_COP de cierre por posición.
    """
    vm_rv_por_posicion: dict[str, float] = field(default_factory=dict)
    vm_alt_por_posicion: dict[str, float] = field(default_factory=dict)
    saldo_credito_por_id: dict[str, float] = field(default_factory=dict)
    trm_cierre: float | None = None
    repo_cierre: float | None = None
    inflacion_cierre: float | None = None
    uvr_cierre: float | None = None
    # Delta de curva acumulado de derivados (dict por tipo de curva). Último campo con
    # default None => retrocompatible: no altera la construcción posicional/keyword previa.
    delta_curva_acumulado: dict | None = None


# Tipos de curva soportados para el delta de curva de derivados (orden canónico).
_TIPOS_DELTA_CURVA = ("delta_tes_cop", "delta_tes_uvr", "delta_fed_bps", "delta_10t_bps")


def _coaccionar_delta_curva(valor) -> float:
    """Coacciona defensivamente un componente de delta de curva a float finito.

    Entrada no numérica, ``None`` o no finita (NaN/±inf) se trata como ``0.0``. Función
    pura, sin defaults de negocio: solo garantiza un número finito para la suma.
    """
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return f


def acumular_delta_curva(prev: dict | None, delta_tramo: dict | None) -> dict:
    """Acumula (suma componente a componente, por tipo de curva) el delta de curva de
    derivados entre tramos (design D3; Req 2.9, 2.10, 2.11).

    Suma ``prev`` (delta acumulado de los tramos 0..i-1) con ``delta_tramo`` (delta propio
    del tramo i) por cada tipo de curva (``delta_tes_cop``, ``delta_tes_uvr``,
    ``delta_fed_bps``, ``delta_10t_bps``). Es una función PURA, sin defaults de negocio:

    - ``None`` se trata como diccionario vacío (ceros).
    - Claves ausentes toman ``0.0``.
    - Valores no finitos/no numéricos se coaccionan a ``0.0`` defensivamente.

    Ejemplo::

        acumular_delta_curva(
            {"delta_tes_cop": 50, "delta_tes_uvr": 30,
             "delta_fed_bps": 25, "delta_10t_bps": 10},
            {"delta_tes_cop": 20, "delta_tes_uvr": 10,
             "delta_fed_bps": 5,  "delta_10t_bps": 15},
        ) == {"delta_tes_cop": 70, "delta_tes_uvr": 40,
              "delta_fed_bps": 30, "delta_10t_bps": 25}
    """
    prev = prev or {}
    delta_tramo = delta_tramo or {}
    return {
        tipo: _coaccionar_delta_curva(prev.get(tipo))
        + _coaccionar_delta_curva(delta_tramo.get(tipo))
        for tipo in _TIPOS_DELTA_CURVA
    }


def escalar_vm_inicial(
    df_portafolio,
    vm_cierre_por_posicion: dict[str, float],
    col_id: str = "id_posicion",
    col_vm: str = "valor_mercado",
):
    """
    Aplica la ecuación de encadenamiento inyectando el VM de cierre del tramo anterior
    como VM inicial del tramo actual (design C4, "Ecuación de encadenamiento"; Req 4.1,
    4.2).

    Devuelve una COPIA de ``df_portafolio`` en la que, para cada posición presente en
    ``vm_cierre_por_posicion``, la columna ``col_vm`` (``valor_mercado`` por defecto) se
    REEMPLAZA por el VM de cierre del tramo anterior. Se trata de una INYECCIÓN directa
    (no un factor multiplicativo): el motor no-RF lee el VM inicial del tramo desde esa
    columna (verificado en ``proyectar_bloque``: ``valor_anterior = float(posicion[
    "valor_mercado"])``), de modo que sustituir el valor equivale a arrancar el tramo ``i``
    desde ``VM_cierre(tramo_{i-1})``.

    Posiciones NO presentes en ``vm_cierre_por_posicion`` conservan su VM original (caso
    del primer tramo, donde el diccionario está vacío, o de posiciones nuevas que no
    existían en el tramo anterior).

    Parámetros
    ----------
    df_portafolio:
        ``pandas.DataFrame`` del portafolio que se pasará al motor. Debe contener las
        columnas ``col_id`` y ``col_vm``. NO se modifica in situ.
    vm_cierre_por_posicion: dict[str, float]
        ``{id_posicion: VM_cierre}`` del tramo anterior (típicamente
        ``EstadoEncadenado.vm_rv_por_posicion`` o ``vm_alt_por_posicion``). Vacío en el
        primer tramo.
    col_id: str
        Nombre de la columna identificadora de posición (por defecto ``'id_posicion'``).
        Parametrizable porque los motores no-RF usan distintos nombres de id (p. ej. RV
        usa ``'titulo'``).
    col_vm: str
        Nombre de la columna de valor de mercado a reemplazar (por defecto
        ``'valor_mercado'``).

    Retorna
    -------
    pandas.DataFrame
        COPIA del portafolio con el VM inicial encadenado. Si
        ``vm_cierre_por_posicion`` está vacío (primer tramo), devuelve una copia sin
        cambios de VM.

    Notas
    -----
    No toca los motores: sólo prepara el DataFrame de entrada que el motor ya sabe leer.
    Los ``id`` provienen del ``EstadoEncadenado`` (datos internos del portafolio); no se
    construye SQL con ellos.
    """
    df = df_portafolio.copy()

    if not vm_cierre_por_posicion:
        # Primer tramo (o sin cierres previos): el portafolio arranca de su VM original.
        return df

    if col_id not in df.columns or col_vm not in df.columns:
        # Sin las columnas esperadas no hay forma segura de inyectar; se devuelve la copia
        # intacta para no corromper el portafolio (el llamador valida el esquema).
        logger.warning(
            "escalar_vm_inicial: columnas requeridas ausentes; se conserva el VM original."
        )
        return df

    def _vm_encadenado(fila):
        id_pos = fila[col_id]
        if id_pos in vm_cierre_por_posicion:
            return _coerce_float_finito(
                vm_cierre_por_posicion[id_pos], default=fila[col_vm]
            )
        return fila[col_vm]

    df[col_vm] = df.apply(_vm_encadenado, axis=1)
    return df


def extraer_vm_cierre(
    df_resultado,
    col_id: str = "titulo",
    col_fecha: str = "fecha_proyeccion",
    col_vm: str = "valor_mercado",
) -> dict[str, float]:
    """
    Extrae el VM de CIERRE (último corte) por posición del DataFrame de resultado de un
    motor no-RF (RV/Alternativos), para poblar el ``EstadoEncadenado`` del tramo siguiente
    (design C4; Req 4.1, 4.2).

    Los motores no-RF (``proyectar_bloque`` de RV y Alternativos) devuelven una fila por
    posición y por ``fecha_proyeccion``, con columnas como ``titulo`` (identificador),
    ``fecha_proyeccion`` (``'YYYY-MM-DD'``) y ``valor_mercado`` (VM COP del corte). Esta
    función toma, para cada posición, el VM del ÚLTIMO ``fecha_proyeccion`` (el cierre del
    tramo) y devuelve ``{id_posicion: VM_cierre}``.

    Parámetros
    ----------
    df_resultado:
        ``pandas.DataFrame`` de salida del motor no-RF.
    col_id: str
        Columna identificadora de posición. Por defecto ``'titulo'`` porque el
        ``Motor_RV`` usa ``titulo`` como identificador; es PARAMETRIZABLE para otros
        motores no-RF que empleen un nombre de id distinto (p. ej. Alternativos).
    col_fecha: str
        Columna de fecha del corte (por defecto ``'fecha_proyeccion'``). Se usa para
        determinar el ÚLTIMO corte por posición.
    col_vm: str
        Columna de valor de mercado (por defecto ``'valor_mercado'``).

    Retorna
    -------
    dict[str, float]
        ``{id_posicion: VM_cierre}`` con el VM del último corte por posición. Diccionario
        VACÍO si ``df_resultado`` es ``None``/vacío o le faltan las columnas requeridas.

    Notas
    -----
    El "último corte" se determina por el máximo ``fecha_proyeccion`` de cada posición
    (las fechas vienen en formato ``'YYYY-MM-DD'``, cuyo orden lexicográfico coincide con
    el cronológico). No asume que el DataFrame venga ordenado.
    """
    if df_resultado is None or len(df_resultado) == 0:
        return {}

    columnas_requeridas = {col_id, col_fecha, col_vm}
    if not columnas_requeridas.issubset(set(df_resultado.columns)):
        logger.warning(
            "extraer_vm_cierre: faltan columnas requeridas; se devuelve estado vacío."
        )
        return {}

    # Índice de la fila con el mayor fecha_proyeccion por cada posición (último corte).
    idx_ultimo = df_resultado.groupby(col_id)[col_fecha].idxmax()
    df_cierre = df_resultado.loc[idx_ultimo]

    return {
        str(fila[col_id]): _coerce_float_finito(fila[col_vm], default=0.0)
        for _, fila in df_cierre.iterrows()
    }


# ==============================================================================
# D8 — DESCOMPOSICIÓN DEL EFECTO MONEDA POR ACTIVO INTERNACIONAL Y TRAMO (tarea 5.8)
# ==============================================================================

@dataclass(frozen=True)
class EfectoMonedaTramo:
    """
    Descomposición del PyG de un activo (posición) en un tramo entre el ``Efecto_Moneda``
    (variación de la TRM del tramo aplicada al capital inicial en moneda origen) y el
    ``Efecto_Retorno_Activo`` (el resto del PyG), de modo que ambos reconstruyen el PyG
    total del tramo (design D8, C4 "Discriminación del Efecto_Moneda"; Req 11.3–11.6).

    Fórmulas (descomposición Opción A del design)::

        vm_inicio_origen = vm_inicio_cop / trm_inicio
        efecto_moneda    = vm_inicio_origen * (trm_cierre - trm_inicio)
        pyg_total        = vm_cierre_cop - vm_inicio_cop
        efecto_retorno   = pyg_total - efecto_moneda

    Para activos LOCALES (moneda COP): ``efecto_moneda == 0`` y
    ``efecto_retorno == pyg_total`` (no aplica reconversión de TRM, Req 11.6). Se logra
    porque para locales ``trm_inicio == trm_cierre`` (o se fuerza ``es_internacional``
    False), anulando el término ``(trm_cierre - trm_inicio)``.

    Campos
    ------
    - id_posicion:      identificador de la posición (``titulo`` en RV, ``id_posicion`` en Alt).
    - activo:           etiqueta del bloque de activo (p. ej. ``'rv'`` / ``'alternativos'``).
    - moneda:           moneda de la posición (``'COP'`` para locales).
    - tramo_indice:     índice 0-based del tramo.
    - fecha_corte:      ``'YYYY-MM-DD'`` del cierre del tramo.
    - vm_inicio_cop:    VM inicial del tramo en COP.
    - trm_inicio:       TRM al inicio del tramo (primer valor de ``senda_trm``).
    - trm_cierre:       TRM al cierre del tramo (último valor de ``senda_trm``).
    - vm_inicio_origen: ``vm_inicio_cop / trm_inicio`` (VM inicial en moneda origen).
    - vm_cierre_cop:    VM de cierre del tramo en COP.
    - pyg_total_cop:    ``vm_cierre_cop - vm_inicio_cop``.
    - efecto_moneda_cop:``vm_inicio_origen * (trm_cierre - trm_inicio)``.
    - efecto_retorno_cop:``pyg_total_cop - efecto_moneda_cop``.
    """
    id_posicion: str
    activo: str
    moneda: str
    tramo_indice: int
    fecha_corte: str
    vm_inicio_cop: float
    trm_inicio: float
    trm_cierre: float
    vm_inicio_origen: float
    vm_cierre_cop: float
    pyg_total_cop: float
    efecto_moneda_cop: float
    efecto_retorno_cop: float

    def to_record(self) -> dict:
        """Serializa la descomposición a un ``dict`` plano (para el store/Excel)."""
        return {
            "id_posicion": self.id_posicion,
            "activo": self.activo,
            "moneda": self.moneda,
            "tramo_indice": self.tramo_indice,
            "fecha_corte": self.fecha_corte,
            "vm_inicio_cop": self.vm_inicio_cop,
            "trm_inicio": self.trm_inicio,
            "trm_cierre": self.trm_cierre,
            "vm_inicio_origen": self.vm_inicio_origen,
            "vm_cierre_cop": self.vm_cierre_cop,
            "pyg_total_cop": self.pyg_total_cop,
            "efecto_moneda_cop": self.efecto_moneda_cop,
            "efecto_retorno_cop": self.efecto_retorno_cop,
        }


def descomponer_efecto_moneda_posicion(
    id_posicion: str,
    activo: str,
    moneda: str,
    vm_inicio_cop,
    vm_cierre_cop,
    trm_inicio,
    trm_cierre,
    tramo_indice: int,
    fecha_corte: str,
) -> EfectoMonedaTramo:
    """
    Calcula la :class:`EfectoMonedaTramo` de UNA posición en un tramo (design D8; Req
    11.3, 11.4, 11.6).

    - Para posiciones INTERNACIONALES (``moneda != 'COP'``) con ``trm_inicio > 0`` se
      aplica la descomposición completa.
    - Para posiciones LOCALES (``moneda == 'COP'``) el ``efecto_moneda`` es 0 y el
      ``efecto_retorno`` es el PyG total (Req 11.6).

    Toda entrada se coerciona a float finito (entrada NO confiable, Req 12).
    """
    vm_ini = _coerce_float_finito(vm_inicio_cop, default=0.0)
    vm_cie = _coerce_float_finito(vm_cierre_cop, default=vm_ini)
    trm_ini = _coerce_float_finito(trm_inicio, default=0.0)
    trm_cie = _coerce_float_finito(trm_cierre, default=trm_ini)

    pyg_total = vm_cie - vm_ini

    es_local = str(moneda).strip().upper() == "COP"
    if es_local or trm_ini <= 0.0:
        # Local (o sin TRM válida): no hay reconversión de TRM (Req 11.6).
        vm_inicio_origen = 0.0
        efecto_moneda = 0.0
    else:
        vm_inicio_origen = vm_ini / trm_ini
        efecto_moneda = vm_inicio_origen * (trm_cie - trm_ini)

    efecto_retorno = pyg_total - efecto_moneda

    return EfectoMonedaTramo(
        id_posicion=str(id_posicion),
        activo=str(activo),
        moneda=str(moneda),
        tramo_indice=int(tramo_indice),
        fecha_corte=str(fecha_corte),
        vm_inicio_cop=vm_ini,
        trm_inicio=trm_ini,
        trm_cierre=trm_cie,
        vm_inicio_origen=vm_inicio_origen,
        vm_cierre_cop=vm_cie,
        pyg_total_cop=pyg_total,
        efecto_moneda_cop=efecto_moneda,
        efecto_retorno_cop=efecto_retorno,
    )


def calcular_efecto_moneda_tramo(
    df_resultado,
    activo: str,
    tramo_indice: int,
    fecha_corte: str,
    trm_inicio: float,
    trm_cierre: float,
    col_id: str = "titulo",
    col_fecha: str = "fecha_proyeccion",
    col_vm: str = "valor_mercado",
    col_moneda: str = "moneda",
) -> list[EfectoMonedaTramo]:
    """
    Calcula la descomposición del efecto moneda por posición para un tramo, a partir del
    DataFrame de resultado de un motor no-RF (RV/Alternativos) ya encadenado (design D8;
    Req 11.1–11.6).

    Para cada posición del resultado:
      - ``vm_inicio_cop`` = ``valor_mercado`` del PRIMER corte (menor ``fecha_proyeccion``).
      - ``vm_cierre_cop`` = ``valor_mercado`` del ÚLTIMO corte (mayor ``fecha_proyeccion``).
      - ``trm_inicio``/``trm_cierre`` = niveles del tramo (primer/último valor de la
        ``senda_trm`` del tramo; los pasa el llamador).
    Los internacionales se identifican por ``moneda != 'COP'`` en el resultado del motor
    (columna ``moneda`` presente en la salida de ``proyectar_bloque``). Para locales el
    efecto moneda es 0 (Req 11.6).

    Devuelve una lista de :class:`EfectoMonedaTramo` (una por posición). Lista VACÍA si
    ``df_resultado`` es ``None``/vacío o le faltan columnas requeridas.
    """
    if df_resultado is None or len(df_resultado) == 0:
        return []

    requeridas = {col_id, col_fecha, col_vm}
    if not requeridas.issubset(set(df_resultado.columns)):
        logger.warning(
            "calcular_efecto_moneda_tramo: faltan columnas requeridas; se omite el tramo."
        )
        return []

    tiene_moneda = col_moneda in df_resultado.columns

    idx_ini = df_resultado.groupby(col_id)[col_fecha].idxmin()
    idx_fin = df_resultado.groupby(col_id)[col_fecha].idxmax()
    df_ini = df_resultado.loc[idx_ini].set_index(col_id)
    df_fin = df_resultado.loc[idx_fin].set_index(col_id)

    efectos: list[EfectoMonedaTramo] = []
    for id_pos in df_ini.index:
        vm_ini = df_ini.at[id_pos, col_vm]
        vm_cie = df_fin.at[id_pos, col_vm] if id_pos in df_fin.index else vm_ini
        moneda = str(df_ini.at[id_pos, col_moneda]).strip().upper() if tiene_moneda else "COP"
        efectos.append(
            descomponer_efecto_moneda_posicion(
                id_posicion=id_pos,
                activo=activo,
                moneda=moneda,
                vm_inicio_cop=vm_ini,
                vm_cierre_cop=vm_cie,
                trm_inicio=trm_inicio,
                trm_cierre=trm_cierre,
                tramo_indice=tramo_indice,
                fecha_corte=fecha_corte,
            )
        )
    return efectos


def _extremos_senda_trm(senda_trm: dict[str, float]) -> tuple[float, float]:
    """
    Devuelve ``(trm_inicio, trm_cierre)`` de una ``senda_trm`` ``{'YYYY-MM-DD': trm}``
    tomando el primer y el último corte por orden cronológico de fechas (el orden
    lexicográfico de ``'YYYY-MM-DD'`` coincide con el cronológico). Devuelve ``(0.0, 0.0)``
    si la senda está vacía.
    """
    if not senda_trm:
        return (0.0, 0.0)
    fechas = sorted(senda_trm.keys())
    return (
        _coerce_float_finito(senda_trm[fechas[0]], default=0.0),
        _coerce_float_finito(senda_trm[fechas[-1]], default=0.0),
    )
