# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 3.3 — Rechazo de saltos inválidos entre columnas consecutivas.

Función bajo prueba: `_clasificar_saltos(columnas)` en
`scripts/compras_ventas_reservas.py`.

Contrato (según el diseño, "Components and Interfaces" de `Lector_Reservas`):
  - Recibe `columnas`: lista de `(indice_columna, primer_dia_del_mes)` ORDENADA
    cronológicamente ascendente (entrada NO confiable).
  - Para cada par consecutivo clasifica el salto por su diferencia de meses:
      Δmeses == 1  -> 'mensual'
      Δmeses == 12 -> 'anual'
      cualquier otro -> `ErrorFormatoEncabezado` (Req 3.4).

Property 4 (porción de saltos): para cualquier par de columnas consecutivas cuya
diferencia en meses no sea 1 ni 12, `_clasificar_saltos` lanza
`ErrorFormatoEncabezado` y no deriva variaciones.
"""

import datetime as _dt

from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import (
    _clasificar_saltos,
    ErrorFormatoEncabezado,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# Rango realista de fechas de encabezado (2020–2040), alineado con la Estrategia
# de Pruebas del diseño.
_ANIO_MIN = 2020
_ANIO_MAX = 2040


def _diff_meses(desde: _dt.date, hasta: _dt.date) -> int:
    """Diferencia en meses (con signo) entre dos primeros-de-mes."""
    return (hasta.year - desde.year) * 12 + (hasta.month - desde.month)


@st.composite
def par_salto_invalido(draw):
    """
    Genera un par de columnas consecutivas `[(0, mes_desde), (1, mes_hasta)]` en orden
    cronológico ascendente cuya diferencia en meses NO sea 1 ni 12.

    - `mes_desde` es el primer día de un mes en un año/mes arbitrario del rango.
    - `delta` es un número de meses hacia adelante en `[2, 60]` excluyendo 12 (los
      valores 1 y 12 son los ÚNICOS saltos válidos; se excluyen aquí para forzar el
      caso inválido). `delta >= 2` garantiza orden cronológico ascendente estricto.
    - `mes_hasta = mes_desde + delta meses`, anclado al día 1.

    Returns:
        list[tuple[int, _dt.date]]: dos columnas consecutivas ordenadas.
    """
    anio = draw(st.integers(min_value=_ANIO_MIN, max_value=_ANIO_MAX))
    mes = draw(st.integers(min_value=1, max_value=12))
    mes_desde = _dt.date(anio, mes, 1)

    # Δmeses inválido: cualquier valor en [2, 60] distinto de 12.
    delta = draw(
        st.integers(min_value=2, max_value=60).filter(lambda d: d != 12)
    )

    total = (mes - 1) + delta
    anio_hasta = anio + total // 12
    mes_hasta_idx = total % 12 + 1
    mes_hasta = _dt.date(anio_hasta, mes_hasta_idx, 1)

    # Sanidad del generador: el salto NO puede ser válido (ni 1 ni 12 meses).
    d = _diff_meses(mes_desde, mes_hasta)
    assert d not in (1, 12)
    assert d >= 2

    return [(0, mes_desde), (1, mes_hasta)]


# Feature: lectura-reservas-encabezados-fecha, Property 4: Saltos inválidos señalan
# error de formato
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(columnas=par_salto_invalido())
def test_prop4_salto_invalido_rechazado(columnas):
    """
    **Property 4: Saltos inválidos señalan error de formato** (porción de saltos)

    **Validates: Requirements 3.4**

    Para cualquier par de columnas consecutivas cuya diferencia en meses no sea 1 ni
    12, `_clasificar_saltos` lanza `ErrorFormatoEncabezado` y no deriva saltos (no
    produce clasificación de variaciones).
    """
    saltos = None
    try:
        saltos = _clasificar_saltos(columnas)
        lanzo_error = False
    except ErrorFormatoEncabezado:
        lanzo_error = True

    assert lanzo_error is True, (
        "Se esperaba ErrorFormatoEncabezado para un salto de meses no soportado"
    )
    # No se derivan variaciones: la clasificación abortó sin devolver saltos.
    assert saltos is None


# ── Control de sanidad: saltos válidos (1 o 12 meses) NO se rechazan ──
# Garantiza que el rechazo de la prueba principal se debe al salto inválido y no a
# que la función rechace SIEMPRE.
@st.composite
def _cadena_saltos_validos(draw):
    """
    Cadena de columnas consecutivas con saltos válidos (1 o 12 meses) en orden
    cronológico ascendente.
    """
    anio = draw(st.integers(min_value=_ANIO_MIN, max_value=_ANIO_MAX - 5))
    mes = draw(st.integers(min_value=1, max_value=12))
    actual = _dt.date(anio, mes, 1)
    columnas = [(0, actual)]

    n_saltos = draw(st.integers(min_value=1, max_value=6))
    for i in range(n_saltos):
        delta = draw(st.sampled_from((1, 12)))
        total = (actual.month - 1) + delta
        actual = _dt.date(actual.year + total // 12, total % 12 + 1, 1)
        columnas.append((i + 1, actual))
    return columnas


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(columnas=_cadena_saltos_validos())
def test_prop4_control_saltos_validos_no_rechazados(columnas):
    """
    Control: una cadena con saltos válidos (1 o 12 meses) NO se rechaza; produce un
    salto tipado por cada par consecutivo. Confirma que el rechazo de la prueba
    principal es específico al salto inválido.
    """
    saltos = _clasificar_saltos(columnas)
    assert len(saltos) == len(columnas) - 1
    for _desde, _hasta, tipo, _mes in saltos:
        assert tipo in ("mensual", "anual")
