"""
Property-based tests para el punto forward dinámico e identidad de descuento.

Feature: escalabilidad-derivados-portafolios
Tarea 7.4 — Property 12.

Se valida la función pura `punto_forward_dinamico` de `valoracion_derivados.py`
(implementada en la tarea 7.1) con Hypothesis (mínimo 100 ejemplos).

Property 12: Punto forward dinámico e identidad de descuento
    Para todo TRM > 0, dev finita y dias > 0:
        Punto_Forward_Dinamico = TRM * (1 + dev)^(dias / 365) - TRM
    y por tanto:
        TRM + Punto_Forward_Dinamico = TRM * (1 + dev)^(dias / 365)

Validates: Requirements 3.6, 3.7
"""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from valoracion_derivados import punto_forward_dinamico

# Estrategias compartidas del proyecto (definidas en tests/conftest.py)
from tests.conftest import trm_strategy, plazos_strategy


# Devaluación finita en un rango financieramente razonable. La base (1 + dev)
# debe ser no negativa para que el punto forward esté definido; el rango incluye
# devaluaciones negativas moderadas y positivas amplias.
def _dev_strategy():
    return st.floats(
        min_value=-0.99,
        max_value=5.0,
        allow_nan=False,
        allow_infinity=False,
    )


@settings(max_examples=25)
@given(trm=trm_strategy(), dev=_dev_strategy(), dias=plazos_strategy())
def test_punto_forward_dinamico_formula(trm, dev, dias):
    """
    Property 12 (fórmula): el punto forward dinámico es exactamente
    TRM * (1 + dev)^(dias / 365) - TRM.

    Validates: Requirements 3.6
    """
    esperado = trm * (1.0 + dev) ** (dias / 365.0) - trm
    obtenido = punto_forward_dinamico(trm, dev, dias)

    assert math.isfinite(obtenido)
    # Tolerancia relativa a la magnitud de la TRM (los valores pueden ser grandes).
    assert obtenido == pytest.approx(esperado, rel=1e-9, abs=1e-6)


@settings(max_examples=25)
@given(trm=trm_strategy(), dev=_dev_strategy(), dias=plazos_strategy())
def test_identidad_de_descuento(trm, dev, dias):
    """
    Property 12 (identidad de descuento): sumar la TRM al punto forward dinámico
    reconstruye el forward teórico TRM * (1 + dev)^(dias / 365).

    Validates: Requirements 3.7
    """
    forward_teorico = trm * (1.0 + dev) ** (dias / 365.0)
    punto = punto_forward_dinamico(trm, dev, dias)

    assert math.isfinite(punto)
    assert (trm + punto) == pytest.approx(forward_teorico, rel=1e-9, abs=1e-6)


@settings(max_examples=25)
@given(trm=trm_strategy(), dias=plazos_strategy())
def test_dev_cero_implica_punto_forward_cero(trm, dias):
    """
    Caso frontera coherente con la identidad: con dev = 0 el forward iguala a la
    TRM, por lo que el punto forward dinámico es cero.

    Validates: Requirements 3.6, 3.7
    """
    punto = punto_forward_dinamico(trm, 0.0, dias)
    assert punto == pytest.approx(0.0, abs=1e-6)
