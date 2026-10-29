"""
Property-based tests para el fallback a `dev_forecast` del orquestador de
devaluación por corte.

Feature: escalabilidad-derivados-portafolios
Tarea 9.2 — Property 15.

Se valida la función `calcular_dev_y_punto_forward` de `valoracion_derivados.py`
(implementada en la tarea 9.1) con Hypothesis.

Property 15: Fallback a dev_forecast
    Para toda entrada en la que:
        - no exista curva al plazo, o
        - la derivación produzca un valor no finito, o
        - el resultado quede fuera de [-0.5, 2.0],
    `calcular_dev_y_punto_forward` devuelve `dev_forecast` como devaluación usada
    y marca `uso_fallback = True`.

Validates: Requirements 3.9
"""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from valoracion_derivados import (
    calcular_dev_y_punto_forward,
    devaluacion_implicita,
    dev_valida,
    tasa_ea,
)

# Estrategias compartidas del proyecto (definidas en tests/conftest.py)
from tests.conftest import plazos_strategy, tasas_strategy, trm_strategy


def _curva_df(dias, tasa_pct):
    """Curva de un tenor con el esquema del proyecto ('Plazo_en_Dias', 'Valor')."""
    return pd.DataFrame({"Plazo_en_Dias": [float(dias)], "Valor": [float(tasa_pct)]})


def _dev_forecast_strategy():
    """Dev_Forecast finita en el rango válido del parámetro (Req 3.10)."""
    return st.floats(
        min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False
    )


# ── Escenario 1: no existe curva al plazo (curvas vacías/ausentes) ──


@settings(max_examples=25)
@given(
    dias=plazos_strategy(),
    trm=trm_strategy(),
    dev_forecast=_dev_forecast_strategy(),
    swtibr_vacia=st.booleans(),
    lutibr_vacia=st.booleans(),
    tasa_presente=tasas_strategy(),
)
def test_fallback_sin_curva_al_plazo(
    dias, trm, dev_forecast, swtibr_vacia, lutibr_vacia, tasa_presente
):
    """
    Si no hay Curva_SWTIBR o Curva_LUTIBR al plazo (DataFrame vacío → tasa NaN),
    la derivación no es utilizable y se usa `dev_forecast` marcando el fallback.

    Validates: Requirements 3.9
    """
    # Al menos una curva ausente para forzar la ausencia de derivación válida.
    assume(swtibr_vacia or lutibr_vacia)

    vacia = pd.DataFrame({"Plazo_en_Dias": [], "Valor": []})
    swtibr = vacia if swtibr_vacia else _curva_df(dias, tasa_presente)
    lutibr = vacia if lutibr_vacia else _curva_df(dias, tasa_presente)

    dev_usada, punto_forward, reg = calcular_dev_y_punto_forward(
        dias, trm, swtibr, lutibr, dev_forecast
    )

    assert reg["uso_fallback"] is True
    assert dev_usada == pytest.approx(dev_forecast, rel=1e-12, abs=1e-12)
    # El registro reporta la misma devaluación adoptada (la de fallback).
    assert reg["dev"] == pytest.approx(dev_forecast, rel=1e-12, abs=1e-12)


# ── Escenario 2: la derivación produce un valor no finito ──


@settings(max_examples=25)
@given(
    dias=plazos_strategy(),
    trm=trm_strategy(),
    dev_forecast=_dev_forecast_strategy(),
    tasa_swtibr=tasas_strategy(),
)
def test_fallback_derivacion_no_finita(dias, trm, dev_forecast, tasa_swtibr):
    """
    Si la derivación produce un valor no finito (p. ej. denominador 1+LUTIBR_EA = 0
    → dev NaN), se usa `dev_forecast` marcando el fallback.

    Se construye una LUTIBR nominal tal que LUTIBR_EA ≈ -1 (denominador nulo):
    con tasa_nom/100 * dias/360 = -1 ⇒ base 0 ⇒ EA = -1.

    Validates: Requirements 3.9
    """
    # base = 1 + tasa/100 * dias/360 = 0  ⇒  tasa_pct = -100 * 360 / dias
    tasa_lutibr_pct = -100.0 * 360.0 / float(dias)

    swtibr = _curva_df(dias, tasa_swtibr)
    lutibr = _curva_df(dias, tasa_lutibr_pct)

    # Confirmar que la derivación es efectivamente no finita para esta entrada.
    lutibr_ea = tasa_ea(tasa_lutibr_pct, float(dias))
    swtibr_ea = tasa_ea(tasa_swtibr, float(dias))
    dev_derivada = devaluacion_implicita(swtibr_ea, lutibr_ea)
    assume(not math.isfinite(float(dev_derivada)))

    dev_usada, punto_forward, reg = calcular_dev_y_punto_forward(
        dias, trm, swtibr, lutibr, dev_forecast
    )

    assert reg["uso_fallback"] is True
    assert dev_usada == pytest.approx(dev_forecast, rel=1e-12, abs=1e-12)


# ── Escenario 3: el resultado derivado queda fuera de [-0.5, 2.0] ──


@settings(max_examples=25)
@given(
    dias=plazos_strategy(min_dias=90, max_dias=3650),
    trm=trm_strategy(),
    dev_forecast=_dev_forecast_strategy(),
    # Regímenes que producen dev fuera de [-0.5, 2.0] por construcción:
    #   'alta'  → SWTIBR muy alta vs LUTIBR moderada  ⇒ dev > 2.0
    #   'baja'  → LUTIBR muy alta vs SWTIBR baja/neg   ⇒ dev < -0.5
    regimen=st.sampled_from(["alta", "baja"]),
    tasa_alta=st.floats(
        min_value=300.0, max_value=1500.0, allow_nan=False, allow_infinity=False
    ),
    tasa_moderada=st.floats(
        min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
)
def test_fallback_fuera_de_rango(
    dias, trm, dev_forecast, regimen, tasa_alta, tasa_moderada
):
    """
    Si la Devaluacion_Implicita derivada es finita pero cae fuera del rango
    [-0.5, 2.0], se descarta y se usa `dev_forecast` marcando el fallback.

    Se construyen los regímenes de tasas de forma que la derivación sea finita
    pero inválida por rango, y se confirma esa condición antes de valorar.

    Validates: Requirements 3.9
    """
    if regimen == "alta":
        tasa_swtibr, tasa_lutibr = tasa_alta, tasa_moderada
    else:
        tasa_swtibr, tasa_lutibr = tasa_moderada, tasa_alta

    swtibr_ea = tasa_ea(tasa_swtibr, float(dias))
    lutibr_ea = tasa_ea(tasa_lutibr, float(dias))
    dev_derivada = float(devaluacion_implicita(swtibr_ea, lutibr_ea))

    # Confirmar que la derivación es finita pero inválida por rango.
    assume(math.isfinite(dev_derivada))
    assume(not dev_valida(dev_derivada))

    swtibr = _curva_df(dias, tasa_swtibr)
    lutibr = _curva_df(dias, tasa_lutibr)

    dev_usada, punto_forward, reg = calcular_dev_y_punto_forward(
        dias, trm, swtibr, lutibr, dev_forecast
    )

    assert reg["uso_fallback"] is True
    assert dev_usada == pytest.approx(dev_forecast, rel=1e-12, abs=1e-12)


# ── Contrapositivo: cuando la derivación SÍ es válida, NO hay fallback ──


@settings(max_examples=25)
@given(
    dias=plazos_strategy(),
    trm=trm_strategy(),
    dev_forecast=_dev_forecast_strategy(),
    tasa_swtibr=tasas_strategy(),
    tasa_lutibr=tasas_strategy(),
)
def test_sin_fallback_cuando_derivacion_valida(
    dias, trm, dev_forecast, tasa_swtibr, tasa_lutibr
):
    """
    Complemento de la Property 15: cuando existe curva al plazo y la derivación es
    finita y dentro de [-0.5, 2.0], NO se usa fallback y `dev_usada` es la derivada.

    Validates: Requirements 3.9
    """
    swtibr_ea = tasa_ea(tasa_swtibr, float(dias))
    lutibr_ea = tasa_ea(tasa_lutibr, float(dias))
    dev_derivada = float(devaluacion_implicita(swtibr_ea, lutibr_ea))

    assume(dev_valida(dev_derivada))

    swtibr = _curva_df(dias, tasa_swtibr)
    lutibr = _curva_df(dias, tasa_lutibr)

    dev_usada, punto_forward, reg = calcular_dev_y_punto_forward(
        dias, trm, swtibr, lutibr, dev_forecast
    )

    assert reg["uso_fallback"] is False
    assert dev_usada == pytest.approx(dev_derivada, rel=1e-9, abs=1e-12)
