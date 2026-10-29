"""
Property tests para la proyección lineal del shift y la interpolación por plazo
(Property 10) de `valoracion_derivados.py`.

Property 10: Proyección lineal del shift por progreso e interpolación por plazo
    Para todo shift total S (pbs), horizonte N meses y mes m, el shift acumulado
    aplicado a la tasa en % es `S/100 * m/N`; y para todo plazo entre las anclas
    del Shift_FED, el shift interpolado es la interpolación lineal entre el ancla
    corta (1 día, FED) y el ancla larga (3650 días, tesoros 10a), con clamp plano
    fuera del rango.

Validates: Requirements 3.4

Funciones bajo prueba:
    - `shift_lutibr_por_plazo(plazo_dias, shift_fed_bps, shift_10t_bps)`
        interpolación lineal por plazo entre anclas (1 día FED, 3650 días 10a),
        clamp plano fuera del rango; devuelve el shift en % (pbs / 100).
    - `curva_proyectada_pct(curva_pct, shift_pct_por_plazo, progreso)`
        suma a la tasa (en %) el shift escalado por el progreso `m/N`.
"""

import math

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from valoracion_derivados import (
    ANCLA_CORTA_DIAS,
    ANCLA_LARGA_DIAS,
    INDICADOR_FWD_COP,
    curva_proyectada_pct,
    shift_lutibr_por_plazo,
)

# Mínimo 100 ejemplos por propiedad (política de la feature).
_AJUSTES = settings(max_examples=200)

# Tolerancia relativa/absoluta para comparaciones de punto flotante.
_TOL = 1e-9


# ── Estrategias locales (rangos financieramente razonables) ──
_shift_bps = st.floats(
    min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False
)
_plazo_dias = st.integers(min_value=1, max_value=3650)
# Progreso mensual m/N: fracción del horizonte en [0, 1].
_progreso = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)
_tasa_pct = st.floats(
    min_value=-5.0, max_value=40.0, allow_nan=False, allow_infinity=False
)


# ─────────────────────────────────────────────────────────────────────────────
# Parte A — Interpolación por plazo del Shift_FED (shift_lutibr_por_plazo)
# ─────────────────────────────────────────────────────────────────────────────
@given(shift_fed_bps=_shift_bps, shift_10t_bps=_shift_bps, plazo=_plazo_dias)
@_AJUSTES
def test_interpolacion_lineal_entre_anclas(shift_fed_bps, shift_10t_bps, plazo):
    """
    Para todo plazo en [1, 3650] días, el shift es la interpolación lineal entre
    el ancla corta (1 día = FED) y el ancla larga (3650 días = tesoros 10a),
    convertido a % (pbs / 100).

    Validates: Requirements 3.4
    """
    fraccion = (plazo - ANCLA_CORTA_DIAS) / (ANCLA_LARGA_DIAS - ANCLA_CORTA_DIAS)
    esperado_bps = shift_fed_bps + (shift_10t_bps - shift_fed_bps) * fraccion
    esperado_pct = esperado_bps / 100.0

    obtenido = shift_lutibr_por_plazo(plazo, shift_fed_bps, shift_10t_bps)
    assert math.isclose(obtenido, esperado_pct, rel_tol=_TOL, abs_tol=_TOL)


@given(shift_fed_bps=_shift_bps, shift_10t_bps=_shift_bps)
@_AJUSTES
def test_anclas_devuelven_shift_exacto(shift_fed_bps, shift_10t_bps):
    """
    En el ancla corta (1 día) el shift es exactamente el FED; en el ancla larga
    (3650 días) es exactamente el de tesoros 10a. Ambos en % (pbs / 100).

    Validates: Requirements 3.4
    """
    corto = shift_lutibr_por_plazo(ANCLA_CORTA_DIAS, shift_fed_bps, shift_10t_bps)
    largo = shift_lutibr_por_plazo(ANCLA_LARGA_DIAS, shift_fed_bps, shift_10t_bps)
    assert math.isclose(corto, shift_fed_bps / 100.0, rel_tol=_TOL, abs_tol=_TOL)
    assert math.isclose(largo, shift_10t_bps / 100.0, rel_tol=_TOL, abs_tol=_TOL)


@given(
    shift_fed_bps=_shift_bps,
    shift_10t_bps=_shift_bps,
    plazo_corto=st.floats(
        min_value=-1000.0, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
    plazo_largo=st.floats(
        min_value=3650.0, max_value=20000.0, allow_nan=False, allow_infinity=False
    ),
)
@_AJUSTES
def test_clamp_plano_fuera_del_rango(shift_fed_bps, shift_10t_bps, plazo_corto, plazo_largo):
    """
    Para plazos <= 1 día el shift es plano e igual al ancla corta (FED); para
    plazos >= 3650 días es plano e igual al ancla larga (tesoros 10a).

    Validates: Requirements 3.4
    """
    obtenido_corto = shift_lutibr_por_plazo(plazo_corto, shift_fed_bps, shift_10t_bps)
    obtenido_largo = shift_lutibr_por_plazo(plazo_largo, shift_fed_bps, shift_10t_bps)
    assert math.isclose(obtenido_corto, shift_fed_bps / 100.0, rel_tol=_TOL, abs_tol=_TOL)
    assert math.isclose(obtenido_largo, shift_10t_bps / 100.0, rel_tol=_TOL, abs_tol=_TOL)


@given(shift_fed_bps=_shift_bps, shift_10t_bps=_shift_bps)
@_AJUSTES
def test_monotonia_por_plazo(shift_fed_bps, shift_10t_bps):
    """
    El shift interpolado es monótono en el plazo: si el ancla larga >= la corta,
    el shift no decrece con el plazo (y viceversa). La interpolación lineal
    preserva el orden entre las anclas.

    Validates: Requirements 3.4
    """
    plazos = np.linspace(ANCLA_CORTA_DIAS, ANCLA_LARGA_DIAS, 50)
    valores = shift_lutibr_por_plazo(plazos, shift_fed_bps, shift_10t_bps)
    diffs = np.diff(valores)
    if shift_10t_bps >= shift_fed_bps:
        assert np.all(diffs >= -_TOL)
    else:
        assert np.all(diffs <= _TOL)


@given(shift_fed_bps=_shift_bps, shift_10t_bps=_shift_bps)
@_AJUSTES
def test_soporta_array_de_plazos(shift_fed_bps, shift_10t_bps):
    """
    Aplicado a un array de plazos, el resultado coincide fila a fila con la
    aplicación escalar (vectorización consistente).

    Validates: Requirements 3.4
    """
    plazos = np.array([1, 100, 500, 1826, 3650, 5000], dtype=float)
    vector = np.asarray(shift_lutibr_por_plazo(plazos, shift_fed_bps, shift_10t_bps))
    for i, p in enumerate(plazos):
        escalar = shift_lutibr_por_plazo(float(p), shift_fed_bps, shift_10t_bps)
        assert math.isclose(vector[i], escalar, rel_tol=_TOL, abs_tol=_TOL)


# ─────────────────────────────────────────────────────────────────────────────
# Parte B — Proyección lineal por progreso (curva_proyectada_pct)
# ─────────────────────────────────────────────────────────────────────────────
def _curva_df(indicadores, plazos, valores):
    """Construye un DataFrame de curva con el esquema esperado."""
    return pd.DataFrame(
        {
            "Nombre_Indicador": indicadores,
            "Plazo_en_Dias": plazos,
            "Valor": valores,
        }
    )


@given(
    tasa=_tasa_pct,
    shift_pct=st.floats(
        min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False
    ),
    progreso=_progreso,
)
@_AJUSTES
def test_shift_acumulado_por_progreso(tasa, shift_pct, progreso):
    """
    El shift acumulado aplicado a la tasa en % es `shift_pct * progreso`, es
    decir, para un shift total S (%) a N meses el desplazamiento en el mes m es
    `S * m/N`. Se verifica sobre una tasa (indicador SWTIBR).

    Validates: Requirements 3.4
    """
    curva = _curva_df(["SWTIBR"], [180], [tasa])
    proyectada = curva_proyectada_pct(curva, np.array([shift_pct]), progreso)

    esperado = tasa + shift_pct * progreso
    assert math.isclose(
        proyectada["Valor"].iloc[0], esperado, rel_tol=1e-9, abs_tol=1e-9
    )


@given(
    S_bps=st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
    N=st.integers(min_value=1, max_value=36),
    m=st.integers(min_value=0, max_value=36),
    tasa=_tasa_pct,
)
@_AJUSTES
def test_senda_lineal_mensual_S_m_sobre_N(S_bps, N, m, tasa):
    """
    Réplica exacta de la senda lineal de renta fija: para un shift total de S pbs
    a N meses, el desplazamiento acumulado (en %) en el mes m es `S/100 * m/N`.

    Validates: Requirements 3.4
    """
    # m no puede exceder N en la senda mensual.
    m = min(m, N)
    shift_pct = S_bps / 100.0
    progreso = m / N

    curva = _curva_df(["LUTIBR"], [360], [tasa])
    proyectada = curva_proyectada_pct(curva, np.array([shift_pct]), progreso)

    esperado_incremento = (S_bps / 100.0) * (m / N)
    obtenido_incremento = proyectada["Valor"].iloc[0] - tasa
    assert math.isclose(
        obtenido_incremento, esperado_incremento, rel_tol=1e-9, abs_tol=1e-9
    )


@given(tasa=_tasa_pct, shift_pct=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False))
@_AJUSTES
def test_progreso_cero_no_desplaza(tasa, shift_pct):
    """Con progreso = 0 (mes 0) la curva proyectada es idéntica a la original.

    Validates: Requirements 3.4
    """
    curva = _curva_df(["SWTIBR"], [90], [tasa])
    proyectada = curva_proyectada_pct(curva, np.array([shift_pct]), 0.0)
    assert math.isclose(proyectada["Valor"].iloc[0], tasa, rel_tol=1e-9, abs_tol=1e-9)


@given(tasa=_tasa_pct, shift_pct=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False))
@_AJUSTES
def test_progreso_uno_aplica_shift_completo(tasa, shift_pct):
    """Con progreso = 1 (mes N) se aplica el shift total (S/100).

    Validates: Requirements 3.4
    """
    curva = _curva_df(["LUTIBR"], [720], [tasa])
    proyectada = curva_proyectada_pct(curva, np.array([shift_pct]), 1.0)
    assert math.isclose(
        proyectada["Valor"].iloc[0], tasa + shift_pct, rel_tol=1e-9, abs_tol=1e-9
    )


@given(
    tasas=st.lists(_tasa_pct, min_size=2, max_size=8),
    shift_pct=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    progreso=_progreso,
)
@_AJUSTES
def test_curva_multiples_puntos_linealidad(tasas, shift_pct, progreso):
    """
    Sobre una curva de varios puntos (todos de tasa), cada punto recibe el mismo
    desplazamiento `shift_pct * progreso` — la proyección es una traslación
    uniforme lineal en el progreso.

    Validates: Requirements 3.4
    """
    n = len(tasas)
    plazos = list(range(30, 30 * (n + 1), 30))[:n]
    curva = _curva_df(["SWTIBR"] * n, plazos, tasas)
    shift_arr = np.full(n, shift_pct)

    proyectada = curva_proyectada_pct(curva, shift_arr, progreso)
    incremento = shift_pct * progreso
    for i in range(n):
        assert math.isclose(
            proyectada["Valor"].iloc[i], tasas[i] + incremento, rel_tol=1e-9, abs_tol=1e-9
        )


@given(
    tasa=_tasa_pct,
    shift_pct=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
    progreso=_progreso,
)
@_AJUSTES
def test_proyeccion_no_muta_la_entrada(tasa, shift_pct, progreso):
    """La proyección es pura: no muta el DataFrame de entrada.

    Validates: Requirements 3.4
    """
    curva = _curva_df(["SWTIBR"], [270], [tasa])
    original = curva["Valor"].iloc[0]
    _ = curva_proyectada_pct(curva, np.array([shift_pct]), progreso)
    assert curva["Valor"].iloc[0] == original
