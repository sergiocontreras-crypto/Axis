"""
Property test de EXPLORACIÓN de bug — Tarea 1, Ola 1 de la spec
`derivados-pyg-cupones-y-reconciliacion-tramos` (bug D1).

Objetivo
--------
Confirmar que `valoracion_derivados.calcular_pyg` NO incorpora el cupón neto
realizado acumulado de los Swaps al P&G. La función solo agrega la columna
`valoracion` (= vpn_der_calc - vpn_obl_calc) por corte y calcula
`p_y_g = valoracion - valor_base`, ignorando por completo las columnas
`cupones_der_acum_cop` y `cupones_obl_acum_cop` que trae el DataFrame de swaps.

El cupón neto realizado ACUMULADO desde la base (t0) hasta un corte t es:

    cup_neto_acum(t) = cupones_der_acum_cop(t) - cupones_obl_acum_cop(t)
    cupon_realizado(t) = cup_neto_acum(t) - cup_neto_acum(t0)

El P&G de Swaps CORRECTO en el último corte debería ser:

    p_y_g(Swaps, último) =
        (Σ valoracion(último) - Σ valoracion(base))      # variación de VPN
      + (Σ cup_neto_acum(último) - Σ cup_neto_acum(base)) # cupón neto realizado

Este test DEBE FALLAR sobre el código actual (eso confirma el bug D1). NO se
modifica código de producción.

El caso de control (unit) con cupones acumulados CONSTANTES entre cortes
(cupón neto realizado = 0) DEBE PASAR incluso hoy, porque entonces el término
de cupón se anula y el P&G coincide con la variación de VPN pura.

Convenciones: el ajuste de `sys.path` lo hace `tests/conftest.py`; aquí además
se añade explícitamente la raíz del repo (`parents[1]`) para que el módulo sea
importable aunque el test se ejecute de forma aislada.
"""

import math
import sys
from pathlib import Path

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

# ── Raíz del repo importable (self-contained, aunque conftest ya lo hace). ──
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from valoracion_derivados import calcular_pyg


# Cortes ordenables (diciembre de años consecutivos). Se usan los primeros K.
_CORTES = [
    "2026-12-31",
    "2027-12-31",
    "2028-12-31",
    "2029-12-31",
    "2030-12-31",
]


def _df_vacio_tipo():
    """DataFrame vacío con las columnas mínimas que consume `calcular_pyg`."""
    return pd.DataFrame({"valoracion": [], "fecha_valoracion": []})


def construir_resultados(swaps_rows):
    """
    Construye un dict `resultados` sintético mínimo para `calcular_pyg`.

    `opciones`, `forwards` y `activos` quedan vacíos (DataFrames con las columnas
    `valoracion` y `fecha_valoracion`) de modo que el P&G de Swaps no se mezcle
    con otros tipos. `swaps` es el DataFrame multi-corte provisto por el llamador.

    Args:
        swaps_rows: lista de dicts con llaves `id_posicion`, `fecha_valoracion`,
            `valoracion`, `cupones_der_acum_cop`, `cupones_obl_acum_cop`.

    Returns:
        dict apto para `calcular_pyg`.
    """
    swaps = pd.DataFrame(
        swaps_rows,
        columns=[
            "id_posicion",
            "fecha_valoracion",
            "valoracion",
            "cupones_der_acum_cop",
            "cupones_obl_acum_cop",
        ],
    )
    return {
        "opciones": _df_vacio_tipo(),
        "forwards": _df_vacio_tipo(),
        "activos": _df_vacio_tipo(),
        "swaps": swaps,
    }


def _pyg_swaps_ultimo(resultados, corte_ultimo):
    """Extrae `p_y_g` de la fila (Swaps, último corte) del resultado."""
    pyg = calcular_pyg(resultados)
    fila = pyg[(pyg["tipo"] == "Swaps") & (pyg["fecha_valoracion"] == corte_ultimo)]
    assert len(fila) == 1, (
        f"Se esperaba exactamente una fila (Swaps, {corte_ultimo}); "
        f"obtenidas {len(fila)}. Resultado:\n{pyg}"
    )
    return float(fila["p_y_g"].iloc[0])


# ── Estrategias Hypothesis ──
# Sendas MONÓTONAS CRECIENTES: se genera un valor inicial y una lista de
# incrementos NO negativos por corte; el acumulado es la suma prefija. Para los
# cupones se fuerza al menos un incremento estrictamente positivo de modo que el
# cupón neto realizado total sea != 0 (si fuera 0, el bug no sería observable).

_valor = st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False)
_incr = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
_incr_pos = st.floats(
    min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False
)


@st.composite
def senda_monotona(draw, k):
    """Senda acumulada no decreciente de longitud `k` (valor base + incrementos)."""
    base = draw(_valor)
    incrementos = draw(st.lists(_incr, min_size=k - 1, max_size=k - 1))
    senda = [base]
    for inc in incrementos:
        senda.append(senda[-1] + inc)
    return senda


@st.composite
def senda_cupon_monotona(draw, k):
    """
    Senda acumulada de cupón POR PATA, no decreciente, de longitud `k`, con al
    menos un incremento estrictamente positivo para garantizar realización != 0.
    """
    base = draw(
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
    )
    incrementos = draw(st.lists(_incr, min_size=k - 1, max_size=k - 1))
    # Forzar que al menos un corte tenga incremento positivo.
    idx_pos = draw(st.integers(min_value=0, max_value=k - 2))
    extra = draw(_incr_pos)
    incrementos[idx_pos] = incrementos[idx_pos] + extra
    senda = [base]
    for inc in incrementos:
        senda.append(senda[-1] + inc)
    return senda


@st.composite
def escenario_swaps(draw):
    """
    Genera K cortes (2..5) y 1..3 swaps. Cada swap tiene sendas monótonas de
    `valoracion` y de cupones acumulados por pata (der/obl). Devuelve
    (swaps_rows, cortes).
    """
    k = draw(st.integers(min_value=2, max_value=5))
    n_swaps = draw(st.integers(min_value=1, max_value=3))
    cortes = _CORTES[:k]

    filas = []
    for s in range(n_swaps):
        val = draw(senda_monotona(k))
        cup_der = draw(senda_cupon_monotona(k))
        cup_obl = draw(senda_cupon_monotona(k))
        for i, corte in enumerate(cortes):
            filas.append(
                {
                    "id_posicion": f"SW{s}",
                    "fecha_valoracion": corte,
                    "valoracion": val[i],
                    "cupones_der_acum_cop": cup_der[i],
                    "cupones_obl_acum_cop": cup_obl[i],
                }
            )
    return filas, cortes


# ─────────────────────────────────────────────────────────────────────────────
# PROPERTY TEST (exploración de bug D1) — DEBE FALLAR sobre el código actual.
# ─────────────────────────────────────────────────────────────────────────────
@settings(deadline=None, max_examples=100)
@given(escenario_swaps())
def test_calcular_pyg_swaps_incluye_cupon_neto_realizado(escenario):
    """
    Propiedad esperada (que el código DEBERÍA cumplir y hoy NO cumple):

        p_y_g(Swaps, último corte) ==
            (Σ valoracion(último) - Σ valoracion(base))
          + (Σ cup_neto_acum(último) - Σ cup_neto_acum(base))

    con cup_neto_acum = cupones_der_acum_cop - cupones_obl_acum_cop.
    """
    swaps_rows, cortes = escenario
    corte_base = cortes[0]
    corte_ultimo = cortes[-1]

    df = pd.DataFrame(swaps_rows)
    df["cup_neto_acum"] = df["cupones_der_acum_cop"] - df["cupones_obl_acum_cop"]

    por_corte = df.groupby("fecha_valoracion").agg(
        valoracion=("valoracion", "sum"),
        cup_neto_acum=("cup_neto_acum", "sum"),
    )

    var_vpn = (
        por_corte.loc[corte_ultimo, "valoracion"]
        - por_corte.loc[corte_base, "valoracion"]
    )
    cupon_realizado = (
        por_corte.loc[corte_ultimo, "cup_neto_acum"]
        - por_corte.loc[corte_base, "cup_neto_acum"]
    )
    pyg_esperado = var_vpn + cupon_realizado

    resultados = construir_resultados(swaps_rows)
    pyg_obtenido = _pyg_swaps_ultimo(resultados, corte_ultimo)

    assert math.isclose(pyg_obtenido, pyg_esperado, rel_tol=1e-9, abs_tol=1e-3), (
        f"P&G de Swaps en {corte_ultimo} no incluye el cupón neto realizado.\n"
        f"  variación VPN      = {var_vpn}\n"
        f"  cupón realizado    = {cupon_realizado}\n"
        f"  p_y_g esperado     = {pyg_esperado}\n"
        f"  p_y_g obtenido     = {pyg_obtenido}\n"
        f"  diferencia         = {pyg_esperado - pyg_obtenido}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CASO DE CONTROL (unit) — DEBE PASAR incluso hoy.
# Cupones acumulados CONSTANTES entre cortes => cupón neto realizado = 0, por lo
# que el P&G de Swaps coincide con la variación de VPN pura (comportamiento
# actual de `calcular_pyg`).
# ─────────────────────────────────────────────────────────────────────────────
def test_control_cupon_constante_pyg_igual_variacion_vpn():
    cortes = _CORTES[:3]
    # VPN varía; cupones acumulados por pata CONSTANTES (no se realizan cupones).
    valoraciones = [100.0, 150.0, 220.0]
    cup_der_const = 1000.0
    cup_obl_const = 400.0

    swaps_rows = []
    for corte, val in zip(cortes, valoraciones):
        swaps_rows.append(
            {
                "id_posicion": "SW0",
                "fecha_valoracion": corte,
                "valoracion": val,
                "cupones_der_acum_cop": cup_der_const,
                "cupones_obl_acum_cop": cup_obl_const,
            }
        )

    resultados = construir_resultados(swaps_rows)
    corte_base = cortes[0]
    corte_ultimo = cortes[-1]

    # Cupón neto realizado = 0 => P&G esperado = variación de VPN pura.
    var_vpn = valoraciones[-1] - valoraciones[0]
    pyg_obtenido = _pyg_swaps_ultimo(resultados, corte_ultimo)

    assert math.isclose(pyg_obtenido, var_vpn, rel_tol=1e-9, abs_tol=1e-3), (
        f"Con cupones constantes el P&G de Swaps debe igualar la variación de VPN.\n"
        f"  variación VPN  = {var_vpn}\n"
        f"  p_y_g obtenido = {pyg_obtenido}"
    )
    # Sanidad: la base del corte inicial debe ser 0 por construcción de calcular_pyg.
    pyg_base = _pyg_swaps_ultimo(resultados, corte_base)
    assert math.isclose(pyg_base, 0.0, rel_tol=1e-9, abs_tol=1e-3)
