"""
Property tests para `dev_valida` (Property 14).

Property 14: Rango de validez de la devaluación
    Para todo valor `dev`, `dev_valida(dev)` es verdadero si y solo si `dev` es
    finita y `-0.5 <= dev <= 2.0`.

Validates: Requirements 3.8
"""

import math

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from valoracion_derivados import DEV_MAX, DEV_MIN, dev_valida

# Ejecución rápida: 25 ejemplos por propiedad.
_AJUSTES = settings(max_examples=25)


# ── Oráculo independiente de la implementación ──
def _es_valida_oraculo(dev) -> bool:
    """dev es finita y está dentro del rango cerrado [DEV_MIN, DEV_MAX]."""
    try:
        valor = float(dev)
    except (TypeError, ValueError):
        return False
    return math.isfinite(valor) and (DEV_MIN <= valor <= DEV_MAX)


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 — equivalencia bidireccional (si y solo si)
# ─────────────────────────────────────────────────────────────────────────────
@given(
    dev=st.floats(allow_nan=True, allow_infinity=True),
)
@_AJUSTES
def test_dev_valida_iff_finita_y_en_rango(dev):
    """
    dev_valida(dev) es True <=> dev es finita y -0.5 <= dev <= 2.0.

    Validates: Requirements 3.8
    """
    esperado = math.isfinite(dev) and (DEV_MIN <= dev <= DEV_MAX)
    # `dev_valida` puede devolver un booleano de numpy (np.bool_); se compara por
    # valor de verdad, no por identidad de objeto.
    assert bool(dev_valida(dev)) == esperado


@given(
    dev=st.floats(min_value=DEV_MIN, max_value=DEV_MAX, allow_nan=False, allow_infinity=False),
)
@_AJUSTES
def test_dev_valida_dentro_del_rango_es_true(dev):
    """Todo valor finito dentro de [-0.5, 2.0] es aceptado.

    Validates: Requirements 3.8
    """
    assert bool(dev_valida(dev)) is True


@given(
    dev=st.floats(allow_nan=False, allow_infinity=False).filter(
        lambda x: x < DEV_MIN or x > DEV_MAX
    ),
)
@_AJUSTES
def test_dev_valida_fuera_del_rango_es_false(dev):
    """Todo valor finito fuera de [-0.5, 2.0] es rechazado.

    Validates: Requirements 3.8
    """
    assert bool(dev_valida(dev)) is False


# ─────────────────────────────────────────────────────────────────────────────
# Casos borde explícitos: NaN, +inf, -inf y las fronteras exactas del rango
# ─────────────────────────────────────────────────────────────────────────────
def test_dev_valida_rechaza_nan_e_infinitos():
    """NaN, +inf y -inf (float y numpy) nunca son válidos.

    Validates: Requirements 3.8
    """
    for valor in (
        float("nan"),
        float("inf"),
        float("-inf"),
        np.nan,
        np.inf,
        -np.inf,
    ):
        assert bool(dev_valida(valor)) is False


def test_dev_valida_fronteras_inclusivas():
    """Las fronteras exactas -0.5 y 2.0 son válidas (rango cerrado).

    Validates: Requirements 3.8
    """
    assert bool(dev_valida(DEV_MIN)) is True
    assert bool(dev_valida(DEV_MAX)) is True
    # Justo fuera de las fronteras.
    assert bool(dev_valida(np.nextafter(DEV_MIN, -np.inf))) is False
    assert bool(dev_valida(np.nextafter(DEV_MAX, np.inf))) is False


def test_dev_valida_entrada_no_numerica_es_false():
    """Entradas no convertibles a float son rechazadas sin lanzar excepción.

    Validates: Requirements 3.8
    """
    for valor in (None, "abc", object(), [0.0]):
        assert bool(dev_valida(valor)) is False
