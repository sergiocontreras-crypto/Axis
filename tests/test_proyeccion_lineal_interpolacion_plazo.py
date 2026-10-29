"""
Property test de la feature `escalabilidad-derivados-portafolios`.

Cubre la **Property 10** del diseño: proyección lineal del shift por progreso
mensual e interpolación por plazo del Shift_FED.

Funciones puras bajo prueba (`valoracion_derivados.py`, implementadas en tarea 8.1):
  - `curva_proyectada_pct`: para un shift total S (en bps) a N meses, el shift
    acumulado aplicado a la tasa en % en el mes m es `S/100 * m/N`.
  - `shift_lutibr_por_plazo`: interpola linealmente el Shift_FED entre el ancla
    corta (1 día, FED) y el ancla larga (3650 días, tesoros 10a), con clamp plano
    fuera del rango.

Property test con Hypothesis (>= 100 ejemplos), etiquetado con la propiedad del
diseño. Reutiliza estrategias del `conftest.py` (tasas, plazos).

Property 10: Proyección lineal del shift por progreso e interpolación por plazo.
Validates: Requirements 3.4
"""

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import valoracion_derivados as vd
from valoracion_derivados import (
    ANCLA_CORTA_DIAS,
    ANCLA_LARGA_DIAS,
    curva_proyectada_pct,
    shift_lutibr_por_plazo,
)

from tests.conftest import tasas_strategy, plazos_strategy


# ── Estrategias ──
#
# Shift total en puntos básicos (bps). Rango amplio y financieramente plausible
# (p.ej. 120 pbs a 12 meses => 10 pbs/mes). Se admiten shifts negativos.
_shift_bps_strategy = st.floats(
    min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False
)

# Horizonte N en meses (>= 1) y mes m dentro del horizonte (0 <= m <= N).
_horizonte_strategy = st.integers(min_value=1, max_value=120)


@st.composite
def mes_y_horizonte_strategy(draw, m_min: int = 0):
    """
    Genera un par (m, N) con `m_min <= m <= N` y `1 <= N <= 120`, evitando el uso
    de `st.data()` (que ralentiza la generación al interleavear con estrategias
    compuestas y dispara el health check `too_slow`).
    """
    n = draw(_horizonte_strategy)
    m = draw(st.integers(min_value=m_min, max_value=n))
    return m, n

# Plazo en días extendido: incluye por debajo del ancla corta y por encima del
# ancla larga para ejercitar el clamp plano.
_plazo_extendido_strategy = st.floats(
    min_value=0.0, max_value=7300.0, allow_nan=False, allow_infinity=False
)


@st.composite
def curva_tasa_df_strategy(draw, min_filas: int = 1, max_filas: int = 6):
    """
    DataFrame de curva de TASA (SWTIBR/LUTIBR) con columnas
    ('Nombre_Indicador', 'Plazo_en_Dias', 'Valor'), valores en % nominal.

    No incluye FWPCOP: la Property 10 se centra en la proyección del shift sobre
    las tasas (la exclusión de FWPCOP la cubre la Property 11).
    """
    n = draw(st.integers(min_value=min_filas, max_value=max_filas))
    filas = [
        {
            "Nombre_Indicador": draw(st.sampled_from(("SWTIBR", "LUTIBR"))),
            "Plazo_en_Dias": float(draw(plazos_strategy())),
            "Valor": draw(tasas_strategy()),
        }
        for _ in range(n)
    ]
    return pd.DataFrame(filas, columns=["Nombre_Indicador", "Plazo_en_Dias", "Valor"])


# ─────────────────────────────────────────────────────────────────────────────
# Parte A: Proyección lineal del shift por progreso mensual.
#
# Para un shift total S (bps) a N meses, el shift acumulado aplicado a la tasa en
# % en el mes m es exactamente S/100 * m/N.
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    curva=curva_tasa_df_strategy(),
    shift_bps=_shift_bps_strategy,
    mes_horizonte=mes_y_horizonte_strategy(m_min=0),
)
def test_shift_acumulado_es_s_sobre_100_por_m_sobre_n(curva, shift_bps, mes_horizonte):
    """
    **Property 10: Proyección lineal del shift por progreso**

    **Validates: Requirements 3.4**

    Para todo shift total S (bps), horizonte N y mes m, el incremento aplicado a
    la tasa en % es exactamente `S/100 * m/N` (senda lineal mensual de RF).
    """
    m, n_meses = mes_horizonte
    progreso = m / n_meses

    # El shift entra a `curva_proyectada_pct` ya en % (bps / 100), tal como lo
    # produce `shift_lutibr_por_plazo` y como se documenta en la firma.
    shift_pct = shift_bps / 100.0

    proyectada = curva_proyectada_pct(curva, shift_pct, progreso)

    orig = curva["Valor"].to_numpy(dtype=np.float64)
    proy = proyectada["Valor"].to_numpy(dtype=np.float64)

    incremento_esperado = (shift_bps / 100.0) * (m / n_meses)
    assert np.allclose(proy - orig, incremento_esperado, rtol=1e-12, atol=1e-9), (
        "El incremento aplicado a la tasa no es S/100 * m/N"
    )


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    curva=curva_tasa_df_strategy(),
    shift_bps=_shift_bps_strategy,
    n_meses=_horizonte_strategy,
)
def test_progreso_cero_no_desplaza_y_progreso_uno_aplica_shift_completo(
    curva, shift_bps, n_meses
):
    """
    **Property 10: Proyección lineal del shift por progreso**

    **Validates: Requirements 3.4**

    Casos frontera del progreso m/N:
      - m = 0  => progreso 0 => tasa idéntica a la original (sin desplazamiento).
      - m = N  => progreso 1 => se aplica el shift total S/100 completo.
    """
    shift_pct = shift_bps / 100.0
    orig = curva["Valor"].to_numpy(dtype=np.float64)

    # Progreso 0: sin desplazamiento.
    proy_0 = curva_proyectada_pct(curva, shift_pct, 0.0)["Valor"].to_numpy(np.float64)
    assert np.allclose(proy_0, orig, rtol=1e-12, atol=1e-9)

    # Progreso 1 (m = N): shift total.
    proy_1 = curva_proyectada_pct(curva, shift_pct, 1.0)["Valor"].to_numpy(np.float64)
    assert np.allclose(proy_1, orig + shift_bps / 100.0, rtol=1e-12, atol=1e-9)


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    curva=curva_tasa_df_strategy(),
    shift_bps=_shift_bps_strategy,
    mes_horizonte=mes_y_horizonte_strategy(m_min=1),
)
def test_progreso_es_lineal_y_monotono_en_el_mes(curva, shift_bps, mes_horizonte):
    """
    **Property 10: Proyección lineal del shift por progreso (linealidad)**

    **Validates: Requirements 3.4**

    El desplazamiento acumulado crece linealmente con el mes: el incremento en el
    mes m es exactamente m veces el incremento del mes 1 (`S/100 * 1/N`), lo que
    garantiza la senda lineal mensual.
    """
    m, n_meses = mes_horizonte
    shift_pct = shift_bps / 100.0
    orig = curva["Valor"].to_numpy(dtype=np.float64)

    incremento_mes_1 = (
        curva_proyectada_pct(curva, shift_pct, 1 / n_meses)["Valor"].to_numpy(np.float64)
        - orig
    )
    incremento_mes_m = (
        curva_proyectada_pct(curva, shift_pct, m / n_meses)["Valor"].to_numpy(np.float64)
        - orig
    )

    assert np.allclose(incremento_mes_m, m * incremento_mes_1, rtol=1e-9, atol=1e-9), (
        "El desplazamiento acumulado no es lineal en el mes"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parte B: Interpolación lineal por plazo del Shift_FED con clamp plano.
#
# Entre el ancla corta (1 día, FED) y el ancla larga (3650 días, tesoros 10a) el
# shift es la interpolación lineal en el plazo; fuera del rango es flat (clamp).
# El resultado se devuelve en % (bps / 100).
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None)
@given(
    plazo=_plazo_extendido_strategy,
    shift_fed_bps=_shift_bps_strategy,
    shift_10t_bps=_shift_bps_strategy,
)
def test_interpolacion_lineal_por_plazo_con_clamp(plazo, shift_fed_bps, shift_10t_bps):
    """
    **Property 10: Interpolación por plazo del Shift_FED**

    **Validates: Requirements 3.4**

    El shift interpolado es la interpolación lineal entre el ancla corta
    (1 día = FED) y el ancla larga (3650 días = tesoros 10a), con clamp plano
    fuera del rango, devuelto en % (bps / 100).
    """
    resultado = shift_lutibr_por_plazo(plazo, shift_fed_bps, shift_10t_bps)

    # Referencia independiente: clamp del plazo al rango [1, 3650] y lerp en bps.
    plazo_clamp = min(max(plazo, ANCLA_CORTA_DIAS), ANCLA_LARGA_DIAS)
    fraccion = (plazo_clamp - ANCLA_CORTA_DIAS) / (ANCLA_LARGA_DIAS - ANCLA_CORTA_DIAS)
    esperado_bps = shift_fed_bps + (shift_10t_bps - shift_fed_bps) * fraccion
    esperado_pct = esperado_bps / 100.0

    assert np.isclose(resultado, esperado_pct, rtol=1e-12, atol=1e-12)


@settings(max_examples=25, deadline=None)
@given(shift_fed_bps=_shift_bps_strategy, shift_10t_bps=_shift_bps_strategy)
def test_anclas_devuelven_su_shift_exacto(shift_fed_bps, shift_10t_bps):
    """
    **Property 10: Interpolación por plazo (anclas)**

    **Validates: Requirements 3.4**

    En el ancla corta (1 día) el shift es exactamente el FED, y en el ancla larga
    (3650 días) es exactamente el de tesoros 10a; ambos en % (bps / 100).
    """
    en_corta = shift_lutibr_por_plazo(ANCLA_CORTA_DIAS, shift_fed_bps, shift_10t_bps)
    en_larga = shift_lutibr_por_plazo(ANCLA_LARGA_DIAS, shift_fed_bps, shift_10t_bps)

    assert np.isclose(en_corta, shift_fed_bps / 100.0, rtol=1e-12, atol=1e-12)
    assert np.isclose(en_larga, shift_10t_bps / 100.0, rtol=1e-12, atol=1e-12)


@settings(max_examples=25, deadline=None)
@given(
    plazo_corto=st.floats(
        min_value=-100.0, max_value=ANCLA_CORTA_DIAS, allow_nan=False, allow_infinity=False
    ),
    plazo_largo=st.floats(
        min_value=ANCLA_LARGA_DIAS, max_value=20000.0, allow_nan=False, allow_infinity=False
    ),
    shift_fed_bps=_shift_bps_strategy,
    shift_10t_bps=_shift_bps_strategy,
)
def test_clamp_plano_fuera_del_rango(
    plazo_corto, plazo_largo, shift_fed_bps, shift_10t_bps
):
    """
    **Property 10: Interpolación por plazo (clamp plano)**

    **Validates: Requirements 3.4**

    Fuera del rango de anclas el shift es flat: para plazos <= 1 día se usa el
    shift FED, y para plazos >= 3650 días el shift de tesoros 10a.
    """
    fuera_corta = shift_lutibr_por_plazo(plazo_corto, shift_fed_bps, shift_10t_bps)
    fuera_larga = shift_lutibr_por_plazo(plazo_largo, shift_fed_bps, shift_10t_bps)

    assert np.isclose(fuera_corta, shift_fed_bps / 100.0, rtol=1e-12, atol=1e-12)
    assert np.isclose(fuera_larga, shift_10t_bps / 100.0, rtol=1e-12, atol=1e-12)


@settings(max_examples=25, deadline=None)
@given(
    plazos=st.lists(plazos_strategy(), min_size=1, max_size=20),
    shift_fed_bps=_shift_bps_strategy,
    shift_10t_bps=_shift_bps_strategy,
)
def test_interpolacion_vectorial_coincide_con_escalar(
    plazos, shift_fed_bps, shift_10t_bps
):
    """
    **Property 10: Interpolación por plazo (vectorial)**

    **Validates: Requirements 3.4**

    La interpolación aplicada a un array de plazos coincide, elemento a elemento,
    con la aplicación escalar a cada plazo (soporte de arrays numpy).
    """
    arr = np.asarray(plazos, dtype=np.float64)
    resultado_vec = np.asarray(
        shift_lutibr_por_plazo(arr, shift_fed_bps, shift_10t_bps), dtype=np.float64
    )
    resultado_esc = np.asarray(
        [shift_lutibr_por_plazo(p, shift_fed_bps, shift_10t_bps) for p in plazos],
        dtype=np.float64,
    )
    assert np.allclose(resultado_vec, resultado_esc, rtol=1e-12, atol=1e-12)
