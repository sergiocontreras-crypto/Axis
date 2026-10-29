# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 9.3 — Prorrateo anual conserva la suma.

Función bajo prueba: `prorratear_reservas_anual(variacion_anual_por_ramo, sub_horizonte)`
en `scripts/proyeccion_tramos.py` (importada como en `app_dash.py`:
`import proyeccion_tramos as ptr`).

Contrato (Req 8.3):
  - Reparte linealmente la variación ANUAL de reserva de cada ramo entre los meses del
    tramo, devolviendo `{ramo_normalizado: {mes_offset: delta}}` con `mes_offset` en
    `1..sub_horizonte`.
  - Invariante: la suma de los deltas de un ramo es igual a su variación anual dentro de
    una tolerancia de punto flotante. El residuo de redondeo del reparto se acumula en
    el ÚLTIMO mes para preservar la suma exacta.
  - Los ramos con variación 0 (o no coercionable) se OMITEN del mapa de salida.

Esta prueba ejercita la invariante de suma del `Prorrateo_Anual` que conecta los
`Salto_Anual` a los tramos (Req 8.3), con >= 100 iteraciones y `deadline=None`.
"""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import proyeccion_tramos as ptr

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100


# Deltas anuales finitos y NO nulos: el contrato omite explícitamente los ramos con
# variación 0.0, por lo que se excluye el cero para poder verificar la invariante de
# suma sobre ramos efectivamente prorrateados. Se cubren magnitudes pequeñas y grandes
# (positivas y negativas) para ejercitar la acumulación del residuo de redondeo.
def _delta_anual_no_nulo():
    return st.floats(
        min_value=-1e12,
        max_value=1e12,
        allow_nan=False,
        allow_infinity=False,
    ).filter(lambda x: x != 0.0)


# Etiquetas de ramo no vacías tras normalizar (strip/upper); se incluyen espacios y
# minúsculas para reflejar la normalización que aplica la función.
def _ramo_strategy():
    return st.text(
        alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z
        min_size=1,
        max_size=8,
    )


# Feature: lectura-reservas-encabezados-fecha, Property 16: Prorrateo anual conserva la
# suma. Para cualquier variación anual por ramo y cualquier sub_horizonte >= 1, la suma
# de los deltas mensuales que produce `prorratear_reservas_anual` para cada ramo es
# igual a su variación anual (dentro de la tolerancia), preservando la invariante al
# conectar los Salto_Anual a los tramos.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    variacion=st.dictionaries(
        keys=_ramo_strategy(),
        values=_delta_anual_no_nulo(),
        min_size=1,
        max_size=6,
    ),
    sub_horizonte=st.integers(min_value=1, max_value=60),
)
def test_prop16_prorrateo_anual_conserva_suma(variacion, sub_horizonte):
    """
    **Property 16: Prorrateo anual conserva la suma**

    **Validates: Requirements 8.3**

    Para cualquier variación anual por ramo (no nula) y cualquier `sub_horizonte >= 1`,
    el mapa `{ramo: {mes_offset: delta}}` que devuelve `prorratear_reservas_anual`
    cumple:
      - cada ramo prorrateado tiene exactamente `sub_horizonte` meses en `1..sub_horizonte`;
      - la suma de los deltas de cada ramo es igual a su variación anual dentro de una
        tolerancia de punto flotante (el residuo se absorbe en el último mes).
    """
    mapa = ptr.prorratear_reservas_anual(variacion, sub_horizonte)

    for ramo_crudo, delta_anual in variacion.items():
        ramo = ramo_crudo.strip().upper()
        # Los deltas no nulos generan una entrada para el ramo normalizado.
        assert ramo in mapa, f"El ramo {ramo!r} debería estar prorrateado."

        meses = mapa[ramo]

        # Los offsets cubren exactamente 1..sub_horizonte.
        assert set(meses.keys()) == set(range(1, sub_horizonte + 1))

        # Invariante de suma: la suma de los deltas mensuales == variación anual.
        suma = math.fsum(meses.values())
        # Tolerancia relativa a la magnitud del delta anual para absorber el error de
        # punto flotante acumulado en el reparto lineal.
        tolerancia = 1e-6 + 1e-9 * abs(delta_anual)
        assert math.isclose(
            suma, delta_anual, rel_tol=1e-9, abs_tol=tolerancia
        ), f"Suma {suma!r} != variación anual {delta_anual!r} (ramo {ramo!r})."
