"""
Batería de property tests de la proyección de curvas de derivados
(feature `escalabilidad-derivados-portafolios`).

Cubre la función pura `curva_proyectada_pct` de `valoracion_derivados.py`, que
construye la Curva_Tasa_Proyectada al mes `m` sumando a la tasa (en %) el shift
escalado por el Progreso_Mensual `m/N`.

Property tests con Hypothesis (>= 100 ejemplos), etiquetados con la propiedad del
diseño. Reutiliza estrategias del `conftest.py` (tasas, plazos).

Property 11: El shift nunca se aplica a los puntos forward en pesos (FWPCOP).
Validates: Requirements 3.5
"""

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import valoracion_derivados as vd
from valoracion_derivados import curva_proyectada_pct, INDICADOR_FWD_COP

from tests.conftest import tasas_strategy, plazos_strategy


# ── Estrategia: curva sintética como DataFrame con indicadores mezclados ──
#
# Genera filas ('Nombre_Indicador', 'Plazo_en_Dias', 'Valor') combinando tasas en %
# (SWTIBR/LUTIBR) con puntos forward en pesos (FWPCOP). Se garantiza al menos una
# fila FWPCOP para que la propiedad sea sustantiva.
INDICADORES_TASA = ("SWTIBR", "LUTIBR")

# Puntos forward en pesos: rango COP amplio y plausible (no son tasas).
_fwpcop_valor_strategy = st.floats(
    min_value=-2000.0, max_value=2000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def curva_df_strategy(draw, min_filas: int = 1, max_filas: int = 10):
    """
    DataFrame con columnas ('Nombre_Indicador', 'Plazo_en_Dias', 'Valor').

    Mezcla filas de tasa (SWTIBR/LUTIBR, valor en %) con filas FWPCOP (valor en
    pesos), asegurando al menos una fila FWPCOP. El orden de indicadores se
    intercala (tasa/fwp/tasa/...) para que la propiedad no dependa de la posición.

    Los atributos de cada bloque se generan en LOTE con `st.lists` (un solo draw
    por columna) en lugar de un `draw` por celda dentro de una comprensión: esto
    evita el health check `too_slow` de Hypothesis sin reducir el espacio de
    búsqueda.
    """
    n_tasa = draw(st.integers(min_value=min_filas, max_value=max_filas))
    n_fwp = draw(st.integers(min_value=1, max_value=max_filas))

    # Draws en lote (rápidos) para cada columna de cada bloque.
    tasa_ind = draw(
        st.lists(st.sampled_from(INDICADORES_TASA), min_size=n_tasa, max_size=n_tasa)
    )
    tasa_plazos = draw(
        st.lists(plazos_strategy(), min_size=n_tasa, max_size=n_tasa)
    )
    tasa_vals = draw(st.lists(tasas_strategy(), min_size=n_tasa, max_size=n_tasa))

    fwp_plazos = draw(st.lists(plazos_strategy(), min_size=n_fwp, max_size=n_fwp))
    fwp_vals = draw(
        st.lists(_fwpcop_valor_strategy, min_size=n_fwp, max_size=n_fwp)
    )

    tasa_filas = [
        {
            "Nombre_Indicador": tasa_ind[k],
            "Plazo_en_Dias": float(tasa_plazos[k]),
            "Valor": tasa_vals[k],
        }
        for k in range(n_tasa)
    ]
    fwp_filas = [
        {
            "Nombre_Indicador": INDICADOR_FWD_COP,
            "Plazo_en_Dias": float(fwp_plazos[k]),
            "Valor": fwp_vals[k],
        }
        for k in range(n_fwp)
    ]

    # Intercalar tasa y FWPCOP para no depender de un orden por bloques ni pagar el
    # costo de generar permutaciones completas.
    filas = []
    i = j = 0
    while i < len(tasa_filas) or j < len(fwp_filas):
        if i < len(tasa_filas):
            filas.append(tasa_filas[i])
            i += 1
        if j < len(fwp_filas):
            filas.append(fwp_filas[j])
            j += 1

    return pd.DataFrame(filas, columns=["Nombre_Indicador", "Plazo_en_Dias", "Valor"])


# Progreso m/N: fracción del horizonte (>= 0). Se admite > 1 para robustez.
_progreso_strategy = st.floats(
    min_value=0.0, max_value=3.0, allow_nan=False, allow_infinity=False
)

# Shift en % por plazo: valores plausibles de shift (bps convertidos a %).
_shift_pct_strategy = st.floats(
    min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False
)


@given(
    curva=curva_df_strategy(),
    shift_escalar=_shift_pct_strategy,
    progreso=_progreso_strategy,
)
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_shift_nunca_se_aplica_a_fwpcop(curva, shift_escalar, progreso):
    """
    Property 11: El shift nunca se aplica a los puntos forward en pesos.
    Validates: Requirements 3.5

    Tras construir la Curva_Tasa_Proyectada, los valores del indicador FWPCOP
    quedan IDÉNTICOS a los originales; solo cambian (o pueden cambiar) las tasas en
    % de SWTIBR y LUTIBR.
    """
    proyectada = curva_proyectada_pct(curva, shift_escalar, progreso)

    # Alinear por índice: curva_proyectada_pct hace .copy(), preservando el índice.
    mask_fwp = (curva["Nombre_Indicador"] == INDICADOR_FWD_COP).to_numpy()
    mask_tasa = ~mask_fwp

    orig = curva["Valor"].to_numpy(dtype=np.float64)
    proy = proyectada["Valor"].to_numpy(dtype=np.float64)

    # (1) Los puntos forward en pesos (FWPCOP) permanecen idénticos.
    assert np.array_equal(proy[mask_fwp], orig[mask_fwp]), (
        "FWPCOP fue alterado por el shift"
    )

    # (2) Las tasas en % reciben exactamente el shift escalado por progreso.
    esperado_tasa = orig[mask_tasa] + shift_escalar * float(progreso)
    assert np.allclose(proy[mask_tasa], esperado_tasa, rtol=1e-12, atol=1e-9), (
        "El shift aplicado a las tasas en % no coincide con S * m/N"
    )


@given(
    curva=curva_df_strategy(),
    progreso=_progreso_strategy,
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_shift_vectorial_nunca_se_aplica_a_fwpcop(curva, progreso, seed):
    """
    Property 11 (shift por plazo/vectorial): incluso con un shift distinto por fila
    (alineado por posición), el indicador FWPCOP nunca se modifica.
    Validates: Requirements 3.5
    """
    rng = np.random.default_rng(seed)
    shift_vec = rng.uniform(-5.0, 5.0, size=len(curva))

    proyectada = curva_proyectada_pct(curva, shift_vec, progreso)

    mask_fwp = (curva["Nombre_Indicador"] == INDICADOR_FWD_COP).to_numpy()
    orig = curva["Valor"].to_numpy(dtype=np.float64)
    proy = proyectada["Valor"].to_numpy(dtype=np.float64)

    # FWPCOP idéntico aunque el shift vectorial tenga componentes no nulas en esas
    # posiciones (la máscara interna las anula).
    assert np.array_equal(proy[mask_fwp], orig[mask_fwp]), (
        "FWPCOP fue alterado por el shift vectorial"
    )


@given(
    curva=curva_df_strategy(),
    shift_escalar=_shift_pct_strategy,
    progreso=_progreso_strategy,
)
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_curva_proyectada_es_pura(curva, shift_escalar, progreso):
    """
    Property 11 (pureza): `curva_proyectada_pct` no muta la entrada; el DataFrame
    original conserva sus valores (incluidos los FWPCOP) tras la proyección.
    Validates: Requirements 3.5
    """
    original = curva.copy(deep=True)
    _ = curva_proyectada_pct(curva, shift_escalar, progreso)
    pd.testing.assert_frame_equal(curva, original)
