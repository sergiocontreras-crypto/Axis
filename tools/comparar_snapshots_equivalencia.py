"""
tools/comparar_snapshots_equivalencia.py
----------------------------------------
Comparador de EQUIVALENCIA de snapshots de un corte, CON vs SIN consolidación de
Negociables fungibles (feature `optimizacion-agrupacion-negociables`, Tarea 7.1).

Complementa a `tools/equivalencia_motor_rf.py` (baseline/check/profile del motor RF)
con la verificación del PRINCIPIO DE NO-REGRESIÓN de la consolidación: la corrida con
consolidación debe producir los MISMOS resultados observables que la corrida sin
consolidación, dentro de la `Tolerancia_Equivalencia` (`1e-6` relativo, Req 3).

Qué compara (Req 3.1, 3.2, 3.3, 3.4):
  1. VM total y VTIR total de cierre del corte (suma sobre todas las filas del snapshot).
  2. P&L mercado/TIR y cupones POR MES, agregados por `Clave_Fungibilidad` (clave de
     reconstrucción: la 8-tupla de `scripts/proyeccion_tramos._clave_fungibilidad`).

Diseño (Req 5 — pureza y testeabilidad):
  - El núcleo es un conjunto de FUNCIONES PURAS que reciben DataFrames/estructuras y
    devuelven las desviaciones; NO hacen I/O y NO mutan la entrada.
  - `verificar_equivalencia_snapshots(...)` levanta `EquivalenciaExcedidaError` si
    alguna desviación relativa supera la tolerancia (aserción automatizada, Req 3.6).
  - Dos snapshots son equivalentes aunque difieran FILA A FILA (uno trae Negociables
    individuales y el otro el `Titulo_Consolidado`): la comparación agrega por
    `Clave_Fungibilidad`, de modo que la suma por grupo debe coincidir.

Uso como script (opcional; las corridas reales son largas y las ejecuta el usuario):
    python tools/comparar_snapshots_equivalencia.py <sin_consolidar.csv> <con_consolidar.csv>

NO modifica la DB (solo lee). Si se omiten argumentos corre un self-test con snapshots
sintéticos pequeños (misma lógica que ejercita la suite de pytest).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# `Tolerancia_Equivalencia`: valor de referencia histórico del proyecto (relativo).
TOLERANCIA_EQUIVALENCIA: float = 1e-6


class EquivalenciaExcedidaError(AssertionError):
    """Se excedió la `Tolerancia_Equivalencia` en al menos una métrica comparada.

    Hereda de `AssertionError` para integrarse de forma natural con pytest y con
    cualquier arnés que trate las aserciones como fallos (Req 3.6).
    """


@dataclass(frozen=True)
class Desvio:
    """Desviación de UNA métrica entre la corrida sin y con consolidación.

    Campos:
      - etiqueta: identifica la métrica (p. ej. 'VM_total', 'pnl_mercado_mes[mes=3]',
        'valor_mercado[grupo#0]'). No contiene datos sensibles, solo agregados.
      - sin_consolidar / con_consolidar: valores comparados.
      - desvio_relativo: desviación relativa simétrica (0.0 si ambos son 0).
      - excede: True si `desvio_relativo` supera la tolerancia.
    """
    etiqueta: str
    sin_consolidar: float
    con_consolidar: float
    desvio_relativo: float
    excede: bool


@dataclass(frozen=True)
class ReporteEquivalencia:
    """Resultado de una comparación. Es de solo lectura (función pura)."""
    tolerancia: float
    desvios: tuple[Desvio, ...] = field(default_factory=tuple)

    @property
    def excedidos(self) -> tuple[Desvio, ...]:
        """Subconjunto de desvíos que superan la tolerancia."""
        return tuple(d for d in self.desvios if d.excede)

    @property
    def equivalente(self) -> bool:
        """True si NINGÚN desvío supera la tolerancia."""
        return not self.excedidos


# ==============================================================================
# Núcleo numérico (funciones puras)
# ==============================================================================

def _desvio_relativo(a: float, b: float) -> float:
    """Desviación relativa simétrica entre `a` y `b`.

    - Si ambos son cero → 0.0.
    - En otro caso: |a - b| / max(|a|, |b|). Es simétrica y acotada en [0, 2].

    Entrada tratada como no confiable: NaN se propaga como desvío infinito (fallo),
    nunca lanza.
    """
    import math

    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return math.inf
    if math.isnan(fa) or math.isnan(fb):
        return math.inf
    denom = max(abs(fa), abs(fb))
    if denom == 0.0:
        return 0.0
    return abs(fa - fb) / denom


def _mk_desvio(etiqueta: str, a, b, tolerancia: float) -> Desvio:
    """Construye un `Desvio` a partir de dos valores y la tolerancia."""
    import math

    try:
        fa = float(a)
    except (TypeError, ValueError):
        fa = math.nan
    try:
        fb = float(b)
    except (TypeError, ValueError):
        fb = math.nan
    rel = _desvio_relativo(fa, fb)
    return Desvio(
        etiqueta=etiqueta,
        sin_consolidar=fa,
        con_consolidar=fb,
        desvio_relativo=rel,
        excede=rel > tolerancia,
    )


def _suma_columna(df, columna: str) -> float:
    """Suma segura de una columna numérica del snapshot (coerción; NaN→0)."""
    import pandas as pd

    if df is None or columna not in getattr(df, "columns", []):
        return 0.0
    serie = pd.to_numeric(df[columna], errors="coerce")
    return float(serie.fillna(0.0).sum())


def _agrupar_por_clave(df, columnas_valor: tuple[str, ...]) -> dict:
    """Agrupa un snapshot por `Clave_Fungibilidad` sumando `columnas_valor`.

    Reutiliza EXACTAMENTE la clave de reconstrucción
    `scripts/proyeccion_tramos._clave_fungibilidad` (la 8-tupla), de modo que un
    snapshot con Negociables individuales y otro con el `Titulo_Consolidado` del mismo
    grupo produzcan la MISMA clave y, por tanto, sumas comparables.

    Las filas cuya clave no coerciona (clave `None`) se acumulan bajo una clave
    sentinela única por posición, para no perderlas en el total.

    Returns:
        dict[clave] -> dict[columna] -> suma (float).
    """
    import pandas as pd

    from proyeccion_tramos import _clave_fungibilidad

    acumulado: dict = {}
    if df is None or len(df) == 0:
        return acumulado

    for pos in range(len(df)):
        fila = df.iloc[pos]
        clave = _clave_fungibilidad(fila)
        if clave is None:
            # Fila sin clave válida: clave sentinela única por posición (no se agrupa).
            clave = ("__SIN_CLAVE__", pos)
        bucket = acumulado.setdefault(clave, {})
        for col in columnas_valor:
            if col in df.columns:
                valor = pd.to_numeric(pd.Series([fila[col]]), errors="coerce").iloc[0]
                if pd.isna(valor):
                    valor = 0.0
                bucket[col] = bucket.get(col, 0.0) + float(valor)
    return acumulado


def comparar_totales_cierre(
    df_sin,
    df_con,
    *,
    tolerancia: float = TOLERANCIA_EQUIVALENCIA,
) -> list[Desvio]:
    """Compara VM total y VTIR total de cierre entre dos snapshots (Req 3.1, 3.2).

    Función PURA: no muta ni hace I/O. VM/VTIR totales son la suma sobre TODAS las filas
    del snapshot (consolidado o no), que deben coincidir por linealidad de la suma.
    """
    desvios: list[Desvio] = []
    for metrica, columna in (("VM_total", "valor_mercado"), ("VTIR_total", "valor_tir")):
        desvios.append(
            _mk_desvio(metrica, _suma_columna(df_sin, columna),
                       _suma_columna(df_con, columna), tolerancia)
        )
    return desvios


def comparar_snapshots_por_clave(
    df_sin,
    df_con,
    *,
    tolerancia: float = TOLERANCIA_EQUIVALENCIA,
    columnas_valor: tuple[str, ...] = ("valor_mercado", "valor_tir", "nominal"),
) -> list[Desvio]:
    """Compara, por `Clave_Fungibilidad`, las sumas de `columnas_valor` (Req 3.3).

    Para cada clave presente en cualquiera de los dos snapshots, compara la suma de cada
    columna. Si una clave existe en uno y no en el otro, el faltante se trata como 0.0
    (lo que fuerza el desvío a 1.0 y marca el fallo). Función PURA.
    """
    izq = _agrupar_por_clave(df_sin, columnas_valor)
    der = _agrupar_por_clave(df_con, columnas_valor)
    claves = sorted(set(izq) | set(der), key=repr)

    desvios: list[Desvio] = []
    for i, clave in enumerate(claves):
        b_izq = izq.get(clave, {})
        b_der = der.get(clave, {})
        for col in columnas_valor:
            etiqueta = f"{col}[grupo#{i}]"
            desvios.append(
                _mk_desvio(etiqueta, b_izq.get(col, 0.0), b_der.get(col, 0.0), tolerancia)
            )
    return desvios


def comparar_series_mensuales(
    serie_sin,
    serie_con,
    *,
    tolerancia: float = TOLERANCIA_EQUIVALENCIA,
    campos: tuple[str, ...] = ("pnl_mercado_mes", "pnl_tir_mes", "cupones_cobrados_mes"),
) -> list[Desvio]:
    """Compara P&L mercado/TIR y cupones POR MES entre dos series (Req 3.3, 3.4).

    `serie_sin`/`serie_con` son listas de dicts por mes con las claves `mes` + `campos`
    (el mismo formato que produce `tools/equivalencia_motor_rf._serializar_resultado`).
    Función PURA: no hace I/O.

    Si el número de meses difiere, se emite un desvío infinito marcado (fallo).
    """
    import math

    desvios: list[Desvio] = []
    if len(serie_sin) != len(serie_con):
        desvios.append(
            Desvio("n_meses", float(len(serie_sin)), float(len(serie_con)),
                   math.inf, True)
        )
        return desvios

    for ms, mc in zip(serie_sin, serie_con):
        mes = ms.get("mes", "?")
        for campo in campos:
            desvios.append(
                _mk_desvio(f"{campo}[mes={mes}]", ms.get(campo, 0.0),
                           mc.get(campo, 0.0), tolerancia)
            )
    return desvios


# ==============================================================================
# Orquestación de la comparación + aserción automatizada
# ==============================================================================

def comparar_snapshots(
    df_sin,
    df_con,
    *,
    tolerancia: float = TOLERANCIA_EQUIVALENCIA,
    serie_sin=None,
    serie_con=None,
) -> ReporteEquivalencia:
    """Compara dos snapshots de un corte (CON vs SIN consolidación) y devuelve el reporte.

    Agrega (sin lanzar):
      - Totales de cierre VM/VTIR (Req 3.1, 3.2).
      - Sumas por `Clave_Fungibilidad` de VM/VTIR/nominal (Req 3.3).
      - (Opcional) P&L mercado/TIR y cupones por mes, si se pasan `serie_sin`/`serie_con`
        (Req 3.3, 3.4). Estas series provienen del arnés del motor por mes.

    Función PURA: devuelve un `ReporteEquivalencia`; NO lanza (usar
    `verificar_equivalencia_snapshots` para la aserción).
    """
    desvios: list[Desvio] = []
    desvios.extend(comparar_totales_cierre(df_sin, df_con, tolerancia=tolerancia))
    desvios.extend(comparar_snapshots_por_clave(df_sin, df_con, tolerancia=tolerancia))
    if serie_sin is not None and serie_con is not None:
        desvios.extend(
            comparar_series_mensuales(serie_sin, serie_con, tolerancia=tolerancia)
        )
    return ReporteEquivalencia(tolerancia=tolerancia, desvios=tuple(desvios))


def verificar_equivalencia_snapshots(
    df_sin,
    df_con,
    *,
    tolerancia: float = TOLERANCIA_EQUIVALENCIA,
    serie_sin=None,
    serie_con=None,
) -> ReporteEquivalencia:
    """Igual que `comparar_snapshots` pero LANZA si se excede la tolerancia (Req 3.6).

    Aserción automatizada: si alguna desviación relativa supera `tolerancia`, levanta
    `EquivalenciaExcedidaError` con el detalle de los desvíos excedidos (sin volcar datos
    sensibles: solo etiquetas agregadas y valores numéricos). Devuelve el reporte cuando
    todo está dentro de tolerancia.
    """
    reporte = comparar_snapshots(
        df_sin, df_con, tolerancia=tolerancia,
        serie_sin=serie_sin, serie_con=serie_con,
    )
    if not reporte.equivalente:
        detalle = "; ".join(
            f"{d.etiqueta}: sin={d.sin_consolidar:.6g} con={d.con_consolidar:.6g} "
            f"rel={d.desvio_relativo:.3g}"
            for d in reporte.excedidos[:20]
        )
        raise EquivalenciaExcedidaError(
            f"Equivalencia EXCEDIDA (tol={tolerancia:g}) en "
            f"{len(reporte.excedidos)} metrica(s): {detalle}"
        )
    return reporte


# ==============================================================================
# Carga de snapshots desde CSV / DB (solo lectura) — para uso como script
# ==============================================================================

def _cargar_snapshot_csv(ruta: str):
    """Lee un snapshot desde CSV (solo lectura). Entrada no confiable: canonicaliza la
    ruta y verifica que exista."""
    import pandas as pd

    p = Path(ruta).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"No existe el archivo de snapshot: {p.name}")
    return pd.read_csv(p)


def _cargar_snapshot_db(db_path: str, corte: str):
    """Lee un snapshot del corte `corte` desde `portafolio_clasificado_proy`.

    Usa consulta PARAMETRIZADA (nunca interpola `corte`), coherente con
    `proyeccion_tramos.cargar_snapshot_proy`.
    """
    import sqlite3
    from datetime import date

    from proyeccion_tramos import cargar_snapshot_proy

    try:
        fecha = date.fromisoformat(corte)
    except ValueError as exc:
        raise ValueError("El corte debe ser una fecha ISO 'YYYY-MM-DD'.") from exc

    conn = sqlite3.connect(str(Path(db_path).resolve()))
    try:
        return cargar_snapshot_proy(conn, fecha)
    finally:
        conn.close()


# ==============================================================================
# Self-test con snapshots sintéticos (sin DB)
# ==============================================================================

def _snapshots_sinteticos():
    """Construye un par de snapshots pequeños (sin / con consolidación) equivalentes.

    SIN: tres reinversiones Negociables fungibles (misma clave) + un título a TIR.
    CON: el `Titulo_Consolidado` (suma de las tres) + el mismo título a TIR.
    Deben ser equivalentes por `Clave_Fungibilidad` y en totales de cierre.
    """
    import pandas as pd

    cols = [
        "id_posicion", "especie", "isin", "moneda", "fecha_emision",
        "fecha_vencimiento", "nominal", "valor_mercado", "valor_tir",
        "clasificacion_contable", "clase_origen", "id_cobertura",
        "tasa_referencia", "tasa_facial", "tipo_tasa", "periodicidad",
        "asset_class_definitivo",
    ]

    def fila(idp, nom, vm, vtir, clasif="Negociable", fe="2027-01-31"):
        return {
            "id_posicion": idp, "especie": "TFIT", "isin": "CO1", "moneda": "COP",
            "fecha_emision": fe, "fecha_vencimiento": "2032-01-01",
            "nominal": nom, "valor_mercado": vm, "valor_tir": vtir,
            "clasificacion_contable": clasif, "clase_origen": "TITULO",
            "id_cobertura": "", "tasa_referencia": "", "tasa_facial": 7.0,
            "tipo_tasa": "EFECTIVO", "periodicidad": "ANUAL",
            "asset_class_definitivo": "DP LP",
        }

    sin = pd.DataFrame([
        fila("R1", 1000.0, 1010.0, 1005.0, fe="2027-01-31"),
        fila("R2", 2000.0, 2020.0, 2010.0, fe="2027-02-28"),
        fila("R3", 3000.0, 3030.0, 3015.0, fe="2027-03-31"),
        fila("VC1", 5000.0, 5100.0, 5050.0, clasif="Vencimiento"),
    ], columns=cols)

    con = pd.DataFrame([
        fila("VC1", 5000.0, 5100.0, 5050.0, clasif="Vencimiento"),
        fila("CONS", 6000.0, 6060.0, 6030.0, fe="2027-01-31"),
    ], columns=cols)

    serie = [
        {"mes": 1, "pnl_mercado_mes": 10.0, "pnl_tir_mes": 5.0, "cupones_cobrados_mes": 0.0},
        {"mes": 2, "pnl_mercado_mes": 20.0, "pnl_tir_mes": 10.0, "cupones_cobrados_mes": 120.0},
    ]
    return sin, con, serie


def _main(argv: list[str]) -> int:
    """Punto de entrada CLI. Devuelve 0 si equivalente, 1 si se excede la tolerancia."""
    args = argv[1:]
    try:
        if len(args) >= 2 and not args[0].startswith("--"):
            df_sin = _cargar_snapshot_csv(args[0])
            df_con = _cargar_snapshot_csv(args[1])
        else:
            print("[self-test] sin argumentos: comparando snapshots sinteticos.")
            df_sin, df_con, serie = _snapshots_sinteticos()
            reporte = verificar_equivalencia_snapshots(
                df_sin, df_con, serie_sin=serie, serie_con=serie
            )
            print(f"OK EQUIVALENTE (self-test): {len(reporte.desvios)} metricas, "
                  f"0 excedidas (tol={reporte.tolerancia:g}).")
            return 0
    except Exception as exc:  # noqa: BLE001 — CLI robusto, mensaje genérico
        print(f"ERROR al cargar snapshots: {type(exc).__name__}: {exc}")
        return 2

    try:
        reporte = verificar_equivalencia_snapshots(df_sin, df_con)
    except EquivalenciaExcedidaError as exc:
        print(f"DIFERENCIAS: {exc}")
        return 1
    print(f"OK EQUIVALENTE: {len(reporte.desvios)} metricas comparadas, "
          f"0 excedidas (tol={reporte.tolerancia:g}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
