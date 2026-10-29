# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) de la feature `proyeccion-por-tramos-multiactivo`.

Cubre el helper puro `meses_horizonte_inclusivo(inicio, fin)` del módulo
`scripts/proyeccion_tramos_multiactivo.py` (tarea 1.1), que centraliza el
`Conteo_Meses_Inclusivo` usado por el modo por tramos (`_meses_entre` de
`proyeccion_tramos.py`) y por el orquestador consolidado punta a punta.

Property 1 (design.md):
  Conteo de meses inclusivo — año completo = 12.
  ∀ (inicio, fin) con fin >= inicio:
    meses_horizonte_inclusivo(inicio, fin)
        == delta.years*12 + delta.months + (1 if delta.days > 0 else 0)
  y, en particular, un año calendario completo (1-ene → 31-dic del mismo año) y el
  periodo de un 1-ene al 1-ene siguiente devuelven 12.

Los tests son PUROS (no tocan la DB): la fixture autouse `db_tmp` de conftest.py
redirige cualquier acceso a SQLite a un archivo temporal, por lo que estos tests
NUNCA tocan la `portafolio.db` real.
"""

from datetime import date

from dateutil.relativedelta import relativedelta
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from proyeccion_tramos_multiactivo import meses_horizonte_inclusivo

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El workspace vive en Google Drive: el I/O del entorno puede disparar `too_slow`
# como artefacto (no por los datos). Se suprime esa health check por indicación
# del diseño/entorno.
_SUPPRESS = [HealthCheck.too_slow]

# Fechas acotadas a un rango razonable para no generar deltas absurdos.
_fechas = st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31))


def _formula_inclusiva(inicio: date, fin: date) -> int:
    """Referencia independiente de la fórmula del Conteo_Meses_Inclusivo."""
    delta = relativedelta(fin, inicio)
    return delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)


# ==============================================================================
# Property 1: Conteo de meses inclusivo — año completo = 12
# ==============================================================================

# Feature: proyeccion-por-tramos-multiactivo, Property 1: Conteo de meses inclusivo
# Validates: Requirements 10.1, 10.2, 10.3
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(inicio=_fechas, fin=_fechas)
def test_prop1_conteo_meses_inclusivo_equivale_a_formula(inicio, fin):
    """
    Propiedad general: para cualquier par (inicio, fin) con fin >= inicio, el helper
    equivale a `delta.years*12 + delta.months + (1 if delta.days > 0 else 0)`.
    """
    if fin < inicio:
        inicio, fin = fin, inicio  # normalizar a fin >= inicio

    esperado = _formula_inclusiva(inicio, fin)
    obtenido = meses_horizonte_inclusivo(inicio, fin)

    assert obtenido == esperado
    # El conteo inclusivo nunca es negativo (fin >= inicio).
    assert obtenido >= 0


# Feature: proyeccion-por-tramos-multiactivo, Property 1: Conteo de meses inclusivo
# Validates: Requirements 10.1, 10.2
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(anio=st.integers(min_value=2000, max_value=2060))
def test_prop1_anio_calendario_completo_ene_dic_da_12(anio):
    """
    Caso específico (Req 10.1): todo par (1-ene-año, 31-dic-mismo-año) cuenta 12 meses
    (no 11). Corrige el off-by-one de la fórmula histórica sin `+1`.
    """
    inicio = date(anio, 1, 1)
    fin = date(anio, 12, 31)
    assert meses_horizonte_inclusivo(inicio, fin) == 12


# Feature: proyeccion-por-tramos-multiactivo, Property 1: Conteo de meses inclusivo
# Validates: Requirements 10.2
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(anio=st.integers(min_value=2000, max_value=2059))
def test_prop1_de_un_ene_al_ene_siguiente_da_12(anio):
    """
    Caso específico (Req 10.2): de un 1-ene al 1-ene del año siguiente cuenta 12 meses
    (delta = 1 año exacto, 0 días residuales ⇒ 12 + 0).
    """
    inicio = date(anio, 1, 1)
    fin = date(anio + 1, 1, 1)
    assert meses_horizonte_inclusivo(inicio, fin) == 12
