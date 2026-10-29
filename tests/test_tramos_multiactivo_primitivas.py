# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature `proyeccion-por-tramos-multiactivo`.

Cubre las primitivas puras del módulo `scripts/proyeccion_tramos_multiactivo.py`:

  - Tarea 3.5 → Property 3 y 4: conversión EA a la duración del tramo
    (identidad en año completo; monotonía y ≈ (1+r)^(m/12)−1).
  - Tarea 3.6 → Property 5: interpolación TRM (ancla extremos, monotonía,
    número de claves == número de periodos).
  - Tarea 3.7 → Property 12: validación de `InputsPorTramo` (entrada NO confiable
    sustituida por defaults sin abortar; valores válidos conservados).

Notas de entorno (por solicitud del usuario):
  - Se suprime `HealthCheck.too_slow` porque el proyecto vive en Google Drive y la
    E/S puede volver lentos los generadores; esto NO relaja las propiedades.
  - Estas primitivas son PURAS (sin acceso a SQLite); la fixture autouse `db_tmp`
    de conftest redirige la DB a un archivo temporal, por lo que ningún test toca
    la `portafolio.db` real.
"""

import math
import os
import sys
from datetime import date

from dateutil.relativedelta import relativedelta
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegura que la raíz del proyecto, `scripts/` y `tests/` estén importables con
# independencia del modo de importación de pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
_SCRIPTS = os.path.join(_RAIZ, "scripts")
for _p in (_ESTE_DIR, _RAIZ, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyeccion_tramos_multiactivo import (  # noqa: E402
    InputsPorTramo,
    _DEFAULTS_INPUTS_TRAMO,
    convertir_ea_a_tramo,
    interpolar_trm_tramo,
)

# ≥100 iteraciones por propiedad (exigencia del diseño para PBT).
_MIN_EJEMPLOS = 100

# Ajustes comunes: sin deadline (E/S de Google Drive) y suprimiendo `too_slow`
# como artefacto del entorno; no relaja las propiedades.
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Tolerancia relativa para comparaciones de punto flotante.
_TOL_REL = 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# TAREA 3.5 — Conversión EA a la duración del tramo (Property 3 y 4)
# ══════════════════════════════════════════════════════════════════════════════

# Retornos EA finitos en un rango amplio pero financieramente acotado.
_retorno_ea = st.floats(
    min_value=-0.95, max_value=5.0, allow_nan=False, allow_infinity=False
)
# Retornos EA estrictamente positivos (para la propiedad de monotonía).
_retorno_ea_pos = st.floats(
    min_value=1e-6, max_value=5.0, allow_nan=False, allow_infinity=False
)


@_AJUSTES
@given(retorno_ea=_retorno_ea)
def test_conversion_ea_identidad_ano_completo(retorno_ea):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 3: Conversión EA es
    # identidad en año completo — convertir_ea_a_tramo(r, 12) == r (dentro de tolerancia).
    # Validates: Requirements 5.3

    Para todo `r` finito, un tramo de 12 meses aplica el retorno anual sin
    capitalización parcial: la conversión es la identidad.
    """
    resultado = convertir_ea_a_tramo(retorno_ea, 12)
    assert math.isclose(resultado, retorno_ea, rel_tol=_TOL_REL, abs_tol=1e-12)


@_AJUSTES
@given(
    retorno_ea=_retorno_ea_pos,
    m1=st.integers(min_value=1, max_value=12),
    m2=st.integers(min_value=1, max_value=12),
)
def test_conversion_ea_monotona_por_duracion(retorno_ea, m1, m2):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 4: Conversión EA monótona
    # por duración — para r > 0 y 1 <= m1 < m2 <= 12,
    # convertir_ea_a_tramo(r, m1) < convertir_ea_a_tramo(r, m2).
    # Validates: Requirements 5.2, 5.4

    Para un retorno EA positivo, capitalizar más meses del tramo produce un
    retorno estrictamente mayor (la capitalización crece con la duración).
    """
    # Ordena para tener 1 <= a < b <= 12; descarta el caso degenerado a == b.
    a, b = min(m1, m2), max(m1, m2)
    if a == b:
        return
    assert convertir_ea_a_tramo(retorno_ea, a) < convertir_ea_a_tramo(retorno_ea, b)


@_AJUSTES
@given(retorno_ea=_retorno_ea, meses=st.integers(min_value=1, max_value=12))
def test_conversion_ea_coincide_formula_capitalizacion(retorno_ea, meses):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 4: Conversión EA coincide
    # con (1+r)^(m/12)-1 para todo r finito y 1 <= m <= 12.
    # Validates: Requirements 5.2, 5.4

    La conversión implementa exactamente la regla de negocio Conversion_EA_A_Tramo.
    Caso trabajado del diseño: r=0.1433, m=5 ⇒ ≈ (1.1433)^(5/12)−1.
    """
    esperado = (1.0 + retorno_ea) ** (meses / 12.0) - 1.0
    resultado = convertir_ea_a_tramo(retorno_ea, meses)
    assert math.isclose(resultado, esperado, rel_tol=_TOL_REL, abs_tol=1e-12)


# ══════════════════════════════════════════════════════════════════════════════
# TAREA 3.6 — Interpolación TRM dentro del tramo (Property 5)
# ══════════════════════════════════════════════════════════════════════════════

# Niveles de TRM estrictamente positivos y finitos.
_trm = st.floats(
    min_value=0.5, max_value=1e5, allow_nan=False, allow_infinity=False
)


@st.composite
def _periodos_mensuales(draw, min_n: int = 1, max_n: int = 24):
    """
    Genera una lista NO vacía de cortes mensuales ascendentes a partir de una fecha
    base, replicando el patrón `relativedelta(months=...)` del proyecto.
    """
    n = draw(st.integers(min_value=min_n, max_value=max_n))
    anio = draw(st.integers(min_value=2024, max_value=2035))
    mes = draw(st.integers(min_value=1, max_value=12))
    dia = draw(st.integers(min_value=1, max_value=28))
    base = date(anio, mes, dia)
    return [base + relativedelta(months=k) for k in range(1, n + 1)]


@_AJUSTES
@given(trm_inicio=_trm, trm_cierre=_trm, periodos=_periodos_mensuales())
def test_interpolacion_trm_ancla_extremos(trm_inicio, trm_cierre, periodos):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 5: Interpolación TRM ancla
    # extremos — el último valor de la senda == trm_cierre; la senda es monótona entre
    # extremos; el número de claves == número de periodos.
    # Validates: Requirements 6.1, 6.2, 6.3

    Para trm_inicio, trm_cierre > 0 y `periodos` no vacío, la senda interpolada
    linealmente ancla su último corte al nivel de cierre, es monótona entre los
    extremos y tiene exactamente una clave por corte.
    """
    senda = interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)

    # Número de claves == número de periodos (una clave 'YYYY-MM-DD' por corte).
    assert len(senda) == len(periodos)

    # Las claves están en el formato y orden esperados (una por corte).
    claves = list(senda.keys())
    assert claves == [p.strftime("%Y-%m-%d") for p in periodos]

    valores = list(senda.values())

    # El ÚLTIMO valor es EXACTAMENTE trm_cierre (anclaje del corte del tramo).
    assert valores[-1] == trm_cierre

    # Monotonía entre extremos, según el signo de (trm_cierre - trm_inicio).
    if trm_cierre > trm_inicio:
        assert all(a <= b for a, b in zip(valores, valores[1:]))
    elif trm_cierre < trm_inicio:
        assert all(a >= b for a, b in zip(valores, valores[1:]))
    else:
        # Extremos iguales ⇒ senda plana en ese nivel.
        assert all(math.isclose(v, trm_cierre, rel_tol=_TOL_REL) for v in valores)

    # Todos los valores quedan dentro del intervalo cerrado [min, max] de extremos.
    lo, hi = min(trm_inicio, trm_cierre), max(trm_inicio, trm_cierre)
    for v in valores:
        assert lo - 1e-6 <= v <= hi + 1e-6


@_AJUSTES
@given(trm_inicio=_trm, trm_cierre=_trm, periodos=_periodos_mensuales())
def test_interpolacion_trm_primer_paso_lineal(trm_inicio, trm_cierre, periodos):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 5: Interpolación TRM lineal —
    # el primer valor corresponde a un paso lineal desde trm_inicio; con un solo corte
    # coincide con trm_cierre.
    # Validates: Requirements 6.1, 6.2

    El inicio del tramo es el cierre del tramo anterior (t0); con `n` cortes el paso
    es (trm_cierre - trm_inicio)/n y el primer corte ya avanzó un paso.
    """
    senda = interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)
    valores = list(senda.values())
    n = len(periodos)

    if n == 1:
        # Un único corte ⇒ es el cierre exacto.
        assert valores[0] == trm_cierre
    else:
        paso = (trm_cierre - trm_inicio) / n
        esperado_primero = trm_inicio + paso
        assert math.isclose(
            valores[0], esperado_primero, rel_tol=1e-9, abs_tol=1e-9
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAREA 3.7 — Validación de InputsPorTramo (Property 12)
# ══════════════════════════════════════════════════════════════════════════════

_CAMPOS = tuple(_DEFAULTS_INPUTS_TRAMO.keys())

# Valores válidos: floats finitos en un rango amplio.
_valor_valido = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)

# Valores NO confiables/inválidos: no numéricos, vacíos, None, NaN, inf.
_valor_invalido = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.text(alphabet="abcXYZ$;-", min_size=1, max_size=6),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.just([1, 2, 3]),
    st.just({"k": "v"}),
)


@_AJUSTES
@given(
    valores=st.fixed_dictionaries(
        {campo: st.one_of(_valor_valido, _valor_invalido) for campo in _CAMPOS}
    )
)
def test_inputs_por_tramo_valida_sin_abortar(valores):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 12: Validación de inputs no
    # confiables — para inputs con campos no numéricos/vacíos/no finitos,
    # InputsPorTramo.from_valores sustituye por el default documentado sin lanzar; los
    # valores válidos se conservan.
    # Validates: Requirements 12.1

    Se genera cada campo como un valor válido (float finito) o un valor NO confiable
    (None, vacío, texto, NaN, inf, contenedores). La construcción nunca lanza; los
    campos inválidos caen a su default documentado y los válidos se conservan.
    """
    # No debe lanzar ante ningún input no confiable.
    inputs = InputsPorTramo.from_valores(**valores)

    for campo in _CAMPOS:
        crudo = valores[campo]
        obtenido = getattr(inputs, campo)
        default = _DEFAULTS_INPUTS_TRAMO[campo]

        # ¿Es el crudo un float finito (valor válido esperado)?
        es_valido = isinstance(crudo, float) and math.isfinite(crudo)

        # El resultado siempre es un float finito, pase lo que pase.
        assert isinstance(obtenido, float)
        assert math.isfinite(obtenido)

        if es_valido:
            # Valor válido conservado exactamente.
            assert obtenido == crudo
        else:
            # Valor inválido/no confiable sustituido por el default documentado.
            assert obtenido == default


@_AJUSTES
@given(valores=st.fixed_dictionaries({campo: _valor_valido for campo in _CAMPOS}))
def test_inputs_por_tramo_conserva_validos(valores):
    """
    # Feature: proyeccion-por-tramos-multiactivo, Property 12: Validación de inputs no
    # confiables — cuando TODOS los campos son floats finitos válidos, se conservan sin
    # sustitución.
    # Validates: Requirements 12.1
    """
    inputs = InputsPorTramo.from_valores(**valores)
    for campo in _CAMPOS:
        assert getattr(inputs, campo) == valores[campo]
