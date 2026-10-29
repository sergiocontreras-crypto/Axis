# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 9: Devaluación implícita por paridad de tasas.

    Para todo par de tasas nominales SWTIBR y LUTIBR y todo `dias > 0`:
        SWTIBR_EA = (1 + SWTIBR_nom/100 * dias/360)^(365/dias) - 1
        LUTIBR_EA = (1 + LUTIBR_nom/100 * dias/360)^(365/dias) - 1
        Devaluacion_Implicita = (1 + SWTIBR_EA) / (1 + LUTIBR_EA) - 1

Valida las funciones puras `tasa_ea` y `devaluacion_implicita` de
`valoracion_derivados.py` (implementadas en la tarea 7.1) contra una
implementación de referencia independiente basada en `math`.

**Validates: Requirements 3.3**
"""

import math

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from valoracion_derivados import devaluacion_implicita, tasa_ea

# Reutiliza las estrategias comunes definidas en conftest.py.
from tests.conftest import plazos_strategy, tasas_strategy

# Número de ejemplos por propiedad. Se mantiene acotado para corridas rápidas
# de la property test (el diseño exige un mínimo, y 25 es suficiente para una
# verificación veloz sin degradar la cobertura del espacio de entradas).
_MIN_EJEMPLOS = 25

# Tolerancia numérica relativa/absoluta para comparar contra la referencia.
_RTOL = 1e-9
_ATOL = 1e-12


def _tasa_ea_ref(tasa_nom_pct: float, dias: int) -> float:
    """Referencia independiente de Tasa_EA (base 360, EA base 365)."""
    base = 1.0 + (tasa_nom_pct / 100.0) * (dias / 360.0)
    if dias <= 0 or base <= 0.0:
        return math.nan
    return base ** (365.0 / dias) - 1.0


def _devaluacion_ref(swtibr_ea: float, lutibr_ea: float) -> float:
    """Referencia independiente de la devaluación implícita por paridad."""
    denom = 1.0 + lutibr_ea
    if denom == 0.0:
        return math.nan
    return (1.0 + swtibr_ea) / denom - 1.0


@settings(max_examples=_MIN_EJEMPLOS)
@given(tasa_nom_pct=tasas_strategy(), dias=plazos_strategy())
def test_tasa_ea_coincide_con_paridad_base_360(tasa_nom_pct, dias):
    """
    **Property 9: Devaluación implícita por paridad de tasas**

    **Validates: Requirements 3.3**

    SWTIBR_EA/LUTIBR_EA = (1 + nom/100 * dias/360)^(365/dias) - 1.
    """
    obtenido = tasa_ea(tasa_nom_pct, dias)
    esperado = _tasa_ea_ref(tasa_nom_pct, dias)

    if math.isnan(esperado):
        assert math.isnan(obtenido)
    else:
        assert np.isclose(obtenido, esperado, rtol=_RTOL, atol=_ATOL)


@settings(max_examples=_MIN_EJEMPLOS)
@given(
    swtibr_nom=tasas_strategy(),
    lutibr_nom=tasas_strategy(),
    dias=plazos_strategy(),
)
def test_devaluacion_implicita_por_paridad_de_tasas(swtibr_nom, lutibr_nom, dias):
    """
    **Property 9: Devaluación implícita por paridad de tasas**

    **Validates: Requirements 3.3**

    Cadena completa Req 3.3: derivar SWTIBR_EA y LUTIBR_EA con `tasa_ea` al mismo
    plazo `dias` y combinar con `devaluacion_implicita`; debe coincidir con la
    fórmula de paridad de referencia.
    """
    swtibr_ea = tasa_ea(swtibr_nom, dias)
    lutibr_ea = tasa_ea(lutibr_nom, dias)

    obtenido = devaluacion_implicita(swtibr_ea, lutibr_ea)

    # Referencia independiente reconstruyendo toda la cadena.
    swtibr_ea_ref = _tasa_ea_ref(swtibr_nom, dias)
    lutibr_ea_ref = _tasa_ea_ref(lutibr_nom, dias)
    esperado = _devaluacion_ref(swtibr_ea_ref, lutibr_ea_ref)

    if math.isnan(esperado):
        assert math.isnan(obtenido)
    else:
        assert np.isclose(obtenido, esperado, rtol=_RTOL, atol=_ATOL)


@settings(max_examples=_MIN_EJEMPLOS)
@given(
    swtibr_ea=st.floats(
        min_value=-0.9, max_value=2.0, allow_nan=False, allow_infinity=False
    ),
    lutibr_ea=st.floats(
        min_value=-0.9, max_value=2.0, allow_nan=False, allow_infinity=False
    ),
)
def test_devaluacion_implicita_formula_directa(swtibr_ea, lutibr_ea):
    """
    **Property 9: Devaluación implícita por paridad de tasas**

    **Validates: Requirements 3.3**

    Núcleo: devaluacion_implicita(a, b) == (1 + a)/(1 + b) - 1
    para todo par de tasas EA con (1 + b) != 0.
    """
    obtenido = devaluacion_implicita(swtibr_ea, lutibr_ea)
    esperado = _devaluacion_ref(swtibr_ea, lutibr_ea)

    if math.isnan(esperado):
        assert math.isnan(obtenido)
    else:
        assert np.isclose(obtenido, esperado, rtol=_RTOL, atol=_ATOL)
