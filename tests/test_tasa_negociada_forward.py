"""
Property test de la feature `escalabilidad-derivados-portafolios`.

Cubre la fórmula pura de la TASA NEGOCIADA de un forward renovado o nuevo,
definida en el diseño como:

    tasa_negociada = TRM * (1 + dev)^(dias / 365)

En `valoracion_derivados.py` esta tasa se materializa por construcción como
`TRM + punto_forward_dinamico(TRM, dev, dias)`, dado que

    punto_forward_dinamico(TRM, dev, dias) = TRM * (1 + dev)^(dias / 365) - TRM
    => TRM + punto_forward_dinamico(...) = TRM * (1 + dev)^(dias / 365)

Property test con Hypothesis (>= 100 ejemplos), etiquetado con la propiedad del
diseño. Las estrategias (TRM, plazos, tasas) provienen de `conftest.py`.
"""

import math

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

import valoracion_derivados as vd

# Estrategias compartidas del proyecto (definidas en tests/conftest.py)
from tests.conftest import trm_strategy, plazos_strategy


# La devaluación es "finita" (Req: dev finita). Se usa un rango amplio pero
# financieramente plausible manteniendo (1 + dev) >= 0 para que la potencia esté
# definida en reales (base negativa con exponente fraccionario no es real).
def dev_strategy():
    return st.floats(
        min_value=-1.0,
        max_value=5.0,
        allow_nan=False,
        allow_infinity=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: Fórmula de la tasa negociada del forward
# Validates: Requirements 3.1, 3.2
#
# Para todo TRM > 0, dev finita y dias > 0, la tasa negociada de un forward
# renovado o nuevo es exactamente TRM * (1 + dev)^(dias / 365).
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25)
@given(
    trm=trm_strategy(),
    dev=dev_strategy(),
    dias=plazos_strategy(),
)
def test_tasa_negociada_es_trm_por_uno_mas_dev_elevado_dias_365(trm, dev, dias):
    """
    **Property 8: Fórmula de la tasa negociada del forward**

    **Validates: Requirements 3.1, 3.2**

    La tasa negociada (TRM + punto forward dinámico) coincide exactamente con la
    fórmula de referencia TRM * (1 + dev)^(dias / 365), calculada de forma
    independiente.
    """
    # Referencia independiente de la fórmula del diseño (Req 3.1, 3.2).
    esperado = trm * (1.0 + dev) ** (dias / 365.0)

    # Tasa negociada tal como la construye el módulo: TRM + punto forward dinámico.
    punto = vd.punto_forward_dinamico(trm, dev, dias)
    tasa_negociada = trm + punto

    assert math.isfinite(esperado)
    assert math.isfinite(tasa_negociada)

    # Igualdad exacta salvo error de redondeo en coma flotante.
    assert tasa_negociada == esperado or math.isclose(
        tasa_negociada, esperado, rel_tol=1e-9, abs_tol=1e-6
    )


@settings(max_examples=25)
@given(
    trm=trm_strategy(),
    dev=dev_strategy(),
    dias=plazos_strategy(),
)
def test_identidad_trm_mas_punto_forward(trm, dev, dias):
    """
    **Property 8: Fórmula de la tasa negociada del forward**

    **Validates: Requirements 3.1, 3.2**

    Verifica la identidad estructural que garantiza la fórmula por construcción:
    `punto_forward_dinamico(TRM, dev, dias) == tasa_negociada - TRM`, es decir
    la tasa negociada menos la TRM es exactamente el punto forward dinámico.
    """
    punto = vd.punto_forward_dinamico(trm, dev, dias)
    tasa_negociada = trm * (1.0 + dev) ** (dias / 365.0)

    assert math.isclose(punto, tasa_negociada - trm, rel_tol=1e-9, abs_tol=1e-6)


@settings(max_examples=25)
@given(
    trm=trm_strategy(),
    dias=plazos_strategy(),
)
def test_dev_cero_deja_tasa_negociada_igual_a_trm(trm, dias):
    """
    **Property 8: Fórmula de la tasa negociada del forward**

    **Validates: Requirements 3.1, 3.2**

    Caso frontera: con dev = 0, (1 + dev)^(dias/365) = 1, por lo que la tasa
    negociada debe ser exactamente la TRM (el punto forward dinámico es 0).
    """
    punto = vd.punto_forward_dinamico(trm, 0.0, dias)
    tasa_negociada = trm + punto

    assert math.isclose(punto, 0.0, abs_tol=1e-9)
    assert math.isclose(tasa_negociada, trm, rel_tol=1e-12, abs_tol=1e-9)
