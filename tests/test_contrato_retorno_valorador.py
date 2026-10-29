"""
Property 23: Preservación del contrato de retorno del valorador.

Validates: Requirements 3.12, 4.11

Enunciado (design.md, Property 23):
    *Para toda* corrida de `valorar_portafolio_derivados`, el valor retornado es un
    diccionario con las claves EXACTAS `{'opciones', 'forwards', 'activos', 'swaps'}`
    y cada valor es un DataFrame (conservando las columnas producidas por el
    comportamiento previo).

Enfoque elegido (documentado)
-----------------------------
`valorar_portafolio_derivados` es una función "pesada": lee curvas, superficie de
volatilidad, posiciones (forwards/opciones/swaps), TRM histórica y la tabla
`portafolio_clasificado` desde `portafolio.db`, y recorre un loop de cortes
mensuales. Para probar el CONTRATO DE RETORNO de forma rápida y determinista sin
depender de la `portafolio.db` real (que vive en Google Drive y puede estar
bloqueada) se hace lo siguiente:

  1. Se siembra una DB SQLite TEMPORAL mínima (vía `tempfile`) con SOLO las tablas
     y filas imprescindibles:
        - `hist_curvas_derivados`: nodos SWTIBR / LUTIBR / FWPCOP (curvas r/q/fwd).
        - `hist_vol_derivados`   : una superficie de volatilidad mínima pero válida.
        - `hist_forwards`, `hist_opciones`, `hist_swaps`: VACÍAS (esquema correcto).
        - `hist_curvas_otc`      : VACÍA (sin swaps que valorar).
        - `portafolio_clasificado`: VACÍA (sin activos USD por portafolio).
     Con posiciones vacías el motor recorre igualmente todos los cortes y construye
     los cuatro DataFrames de salida, ejercitando el camino real de la función
     (incluida la ampliación de firma de la tarea 15.1) SIN cómputo pesado.

  2. Se monkeypatchea `valoracion_derivados.DB_PATH` a la DB temporal (la función
     abre la conexión con `sqlite3.connect(DB_PATH)` directamente) y
     `_cargar_trm_hist` para devolver una TRM histórica sintética (evita leer el
     Excel `TRM.xlsx` de Google Drive y lo hace determinista).

  3. La propiedad varía SOLO los parámetros nuevos/configurables dentro de sus
     rangos válidos (`dev_forecast`, `canal_opc`, `tolerancia_prima`,
     `max_iter_canal`, `persistir_auditoria`) más el método y un horizonte corto,
     y verifica que el retorno siempre respeta el contrato.

Como el valorador es pesado, se usa `max_examples` pequeño (10) y `deadline=None`.
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import valoracion_derivados as vd
from valoracion_derivados import valorar_portafolio_derivados

# Claves EXACTAS del contrato de retorno (Req 3.12, 4.11).
CLAVES_CONTRATO = {"opciones", "forwards", "activos", "swaps"}

# Fecha de corte fija usada por la siembra (todas las filas la comparten).
FECHA_CORTE = "2026-05-20"


# ─────────────────────────────────────────────────────────────────────────────
# Siembra de una DB temporal mínima
# ─────────────────────────────────────────────────────────────────────────────
def _sembrar_db_minima(ruta: Path, fecha_corte: str) -> None:
    """Crea las tablas mínimas y siembra curvas + vol; posiciones vacías."""
    conn = sqlite3.connect(str(ruta))
    try:
        cur = conn.cursor()

        # --- Curvas de derivados (SWTIBR / LUTIBR / FWPCOP) ---
        cur.execute(
            "CREATE TABLE hist_curvas_derivados "
            "(fecha_reporte TEXT, Nombre_Indicador TEXT, Plazo_en_Dias REAL, Valor REAL)"
        )
        plazos = [1, 30, 90, 180, 365, 730, 1095, 1825, 3650]
        filas_curvas = []
        for p in plazos:
            # Tasas nominales en % base 360 (finitas, positivas, plausibles).
            filas_curvas.append((fecha_corte, "SWTIBR", float(p), 9.5))   # COP
            filas_curvas.append((fecha_corte, "LUTIBR", float(p), 4.5))   # USD
            # Puntos forward en pesos (FWPCOP): no son tasa, se suman a la TRM.
            filas_curvas.append((fecha_corte, "FWPCOP", float(p), 50.0 + p))
        cur.executemany(
            "INSERT INTO hist_curvas_derivados "
            "(fecha_reporte, Nombre_Indicador, Plazo_en_Dias, Valor) VALUES (?, ?, ?, ?)",
            filas_curvas,
        )

        # --- Superficie de volatilidad mínima pero válida ---
        # _cargar_vol descarta las columnas meta ('fecha_reporte','Fecha','Indicador','0')
        # y usa el resto como strikes; ordena por la columna '0' (día). Creamos 3 strikes
        # x 4 tenores para tener una superficie interpolable.
        cur.execute(
            'CREATE TABLE hist_vol_derivados '
            '(fecha_reporte TEXT, "Fecha" TEXT, "Indicador" TEXT, "0" REAL, '
            '"3000" REAL, "4000" REAL, "5000" REAL)'
        )
        vol_filas = [
            (fecha_corte, fecha_corte, "USDCOP", 30.0, 0.15, 0.14, 0.15),
            (fecha_corte, fecha_corte, "USDCOP", 180.0, 0.16, 0.15, 0.16),
            (fecha_corte, fecha_corte, "USDCOP", 365.0, 0.17, 0.16, 0.17),
            (fecha_corte, fecha_corte, "USDCOP", 730.0, 0.18, 0.17, 0.18),
        ]
        cur.executemany(
            'INSERT INTO hist_vol_derivados '
            '(fecha_reporte, "Fecha", "Indicador", "0", "3000", "4000", "5000") '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            vol_filas,
        )

        # --- Posiciones VACÍAS (esquema correcto) ---
        cur.execute(
            "CREATE TABLE hist_forwards "
            "(fecha_reporte TEXT, fecha_constitucion TEXT, fecha_vencimiento TEXT, "
            "nominal_usd REAL, sentido TEXT, portafolio TEXT, strike REAL)"
        )
        cur.execute(
            "CREATE TABLE hist_opciones "
            "(fecha_reporte TEXT, fecha_constitucion TEXT, fecha_vencimiento TEXT, "
            "tipo TEXT, sentido TEXT, portafolio TEXT, strike REAL, nominal_usd REAL)"
        )
        cur.execute(
            "CREATE TABLE hist_swaps "
            "(fecha_reporte TEXT, fecha_constitucion TEXT, fecha_vencimiento TEXT, "
            "nominal_der REAL, facial_der REAL, vpn_derecho REAL, "
            "nominal_obl REAL, facial_obl REAL, vpn_obligacion REAL, valor_mercado REAL)"
        )
        cur.execute(
            "CREATE TABLE hist_curvas_otc "
            "(fecha_reporte TEXT, Curva TEXT, Plazo_en_Dias REAL, Valor REAL)"
        )

        # --- portafolio_clasificado VACÍA (esquema mínimo consultado) ---
        cur.execute(
            "CREATE TABLE portafolio_clasificado "
            "(fecha_reporte TEXT, portafolio TEXT, valor_mercado REAL, "
            "clase_origen TEXT, moneda TEXT)"
        )

        conn.commit()
    finally:
        conn.close()


def _trm_hist_sintetica(fecha_corte_dt: datetime) -> pd.DataFrame:
    """TRM histórica sintética (dos puntos) para no depender de TRM.xlsx real."""
    return pd.DataFrame(
        {
            "fecha": [
                fecha_corte_dt - timedelta(days=30),
                fecha_corte_dt,
            ],
            "trm": [3900.0, 3950.0],
        }
    )


@pytest.fixture
def db_valorador_tmp(monkeypatch):
    """
    Siembra una DB temporal mínima, apunta `valoracion_derivados.DB_PATH` a ella y
    parchea `_cargar_trm_hist` para usar una TRM sintética determinista.
    """
    fd, nombre = tempfile.mkstemp(suffix=".db", prefix="axis_valorador_")
    import os

    os.close(fd)
    ruta = Path(nombre)
    _sembrar_db_minima(ruta, FECHA_CORTE)

    monkeypatch.setattr(vd, "DB_PATH", str(ruta), raising=True)
    monkeypatch.setattr(vd, "_cargar_trm_hist", _trm_hist_sintetica, raising=True)

    try:
        yield ruta
    finally:
        try:
            ruta.unlink()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias: parámetros configurables dentro de sus rangos válidos
# ─────────────────────────────────────────────────────────────────────────────
# dev_forecast ∈ [-0.5, 2.0] (Req 3.10)
_dev_forecast_strat = st.floats(
    min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False
)
# tolerancia_prima ∈ [0.0001, 1000.0] (Req 4.6)
_tolerancia_strat = st.floats(
    min_value=0.0001, max_value=1000.0, allow_nan=False, allow_infinity=False
)
# max_iter_canal ∈ [1, 1000] (Req 4.8)
_max_iter_strat = st.integers(min_value=1, max_value=1000)
# canal_opc: fallback en COP, rango operativo razonable (Req 4.9)
_canal_opc_strat = st.floats(
    min_value=100.0, max_value=5000.0, allow_nan=False, allow_infinity=False
)
# Métodos que no dependen de la rama pesada de exposición-objetivo/delta-hedge.
_metodo_strat = st.sampled_from([1, 2])
# Horizonte corto: 1 o 2 meses (mantiene la corrida ligera).
_meses_strat = st.integers(min_value=1, max_value=2)


def _fecha_fin(meses: int) -> str:
    from dateutil.relativedelta import relativedelta

    fin = datetime.strptime(FECHA_CORTE, "%Y-%m-%d") + relativedelta(months=meses)
    return fin.strftime("%Y-%m-%d")


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    metodo=_metodo_strat,
    meses=_meses_strat,
    dev_forecast=_dev_forecast_strat,
    canal_opc=_canal_opc_strat,
    tolerancia_prima=_tolerancia_strat,
    max_iter_canal=_max_iter_strat,
    persistir_auditoria=st.booleans(),
)
def test_contrato_retorno_valorador(
    db_valorador_tmp,
    metodo,
    meses,
    dev_forecast,
    canal_opc,
    tolerancia_prima,
    max_iter_canal,
    persistir_auditoria,
):
    """
    Para cualquier combinación válida de parámetros configurables, el retorno de
    `valorar_portafolio_derivados` es un dict con EXACTAMENTE las claves
    {'opciones','forwards','activos','swaps'} y cada valor es un DataFrame.
    """
    resultado = valorar_portafolio_derivados(
        fecha_corte=FECHA_CORTE,
        fecha_fin=_fecha_fin(meses),
        metodo=metodo,
        nivel_actual=3950.0,
        activos_usd=1_000_000.0,
        portafolio_pesos=1_000_000_000.0,
        dev_forecast=dev_forecast,
        canal_opc=canal_opc,
        tolerancia_prima=tolerancia_prima,
        max_iter_canal=max_iter_canal,
        persistir_auditoria=persistir_auditoria,
        corrida_id="test-contrato",
    )

    # 1) Es un diccionario.
    assert isinstance(resultado, dict), f"El retorno no es dict: {type(resultado)!r}"

    # 2) Claves EXACTAS (ni más ni menos) — contrato de retorno (Req 3.12, 4.11).
    assert set(resultado.keys()) == CLAVES_CONTRATO, (
        f"Claves del retorno {set(resultado.keys())!r} != {CLAVES_CONTRATO!r}"
    )

    # 3) Cada valor es un DataFrame.
    for clave in CLAVES_CONTRATO:
        assert isinstance(resultado[clave], pd.DataFrame), (
            f"resultado[{clave!r}] no es DataFrame: {type(resultado[clave])!r}"
        )
