# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 1.3 — Robustez de la detección de `Encabezado_Fecha` a nivel de celda.

Función bajo prueba: `_es_encabezado_fecha(valor)` en
`scripts/compras_ventas_reservas.py`.

Contrato (Decisión 4 del diseño):
  - Devuelve True para `datetime`/`date`/`pd.Timestamp` nativos y para texto
    parseable a fecha (fechas serializadas por Excel, p. ej. "2026-06-01").
  - Devuelve False para valores puramente numéricos (offset entero `1`, floats)
    y para nombres de mes ("ENERO", "ENE", ...), que son el formato viejo que el
    lector debe rechazar.

La propiedad ejercita, con >= 100 iteraciones, celdas de fecha (True) mezcladas
conceptualmente con celdas de ruido numérico y de nombre de mes (False).
"""

import datetime as _dt

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import _MESES_ORDEN, _es_encabezado_fecha

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# En corridas de la SUITE COMPLETA sobre el sistema de archivos de Google Drive, la
# generación de entradas de Hypothesis puede exceder el umbral de `too_slow` por la
# latencia de E/S del entorno (no por el coste real de generar estas celdas simples).
# Se suprime esa health check, consistente con el resto de las pruebas de propiedad de
# la feature; el mínimo de 100 iteraciones y `deadline=None` se conservan.
_SUPPRESS_SLOW = [HealthCheck.too_slow]


# ── Generadores de celdas de FECHA (deben detectarse como Encabezado_Fecha) ──

# Rango realista de fechas de encabezado (2020–2040), alineado con la Estrategia
# de Pruebas del diseño. Se usa el primer día del mes como en el insumo real.
_fechas_validas = st.dates(
    min_value=_dt.date(2020, 1, 1),
    max_value=_dt.date(2040, 12, 31),
)


@st.composite
def celdas_fecha(draw):
    """
    Celda que DEBE reconocerse como fecha. Cubre las tres formas que llegan del
    insumo real:
      - `datetime.date` nativo.
      - `datetime.datetime` nativo (Excel suele entregar datetime a medianoche).
      - fecha serializada como texto ISO ("YYYY-MM-DD"), como cuando Excel exporta
        el encabezado a string.
    """
    fecha = draw(_fechas_validas)
    forma = draw(st.sampled_from(("date", "datetime", "texto_iso")))
    if forma == "date":
        return fecha
    if forma == "datetime":
        return _dt.datetime(fecha.year, fecha.month, fecha.day)
    return fecha.isoformat()  # "YYYY-MM-DD"


# ── Generadores de celdas de RUIDO (NO deben reconocerse como fecha) ──

# Ruido numérico: offsets enteros y floats. `1`, `12`, `3.5` no son fechas.
_ruido_numerico = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    # Números serializados como texto ("1", "12", "3.5") tampoco son fecha.
    st.integers(min_value=0, max_value=1000).map(str),
)

# Ruido de nombre de mes: nombres completos y abreviaturas, en distinto casing y
# con espacios alrededor (entrada no confiable). Ninguno es un Encabezado_Fecha.
_NOMBRES_MES = list(_MESES_ORDEN) + [
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "SEPT", "OCT", "NOV", "DIC",
]


@st.composite
def ruido_nombre_mes(draw):
    """Nombre de mes con casing y espacios arbitrarios (nunca es fecha)."""
    nombre = draw(st.sampled_from(_NOMBRES_MES))
    casing = draw(st.sampled_from(("upper", "lower", "title")))
    nombre = getattr(nombre, casing)()
    pre = draw(st.text(alphabet=" \t", max_size=3))
    post = draw(st.text(alphabet=" \t", max_size=3))
    return f"{pre}{nombre}{post}"


_celdas_ruido = st.one_of(_ruido_numerico, ruido_nombre_mes())


# Feature: lectura-reservas-encabezados-fecha, Property 1: Localización de columnas
# por Encabezado_Fecha (parcial: detección a nivel de celda). Para cualquier celda de
# fecha (datetime/serializada/date) el detector devuelve True, y para cualquier celda
# de ruido numérico o de nombre de mes devuelve False.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_SLOW)
@given(celda_fecha=celdas_fecha(), celda_ruido=_celdas_ruido)
def test_prop1_deteccion_fecha_vs_ruido(celda_fecha, celda_ruido):
    """
    **Property 1: Localización de columnas por Encabezado_Fecha** (nivel de celda)

    **Validates: Requirements 1.1**

    Una celda de fecha se reconoce (True) y una celda de ruido (numérico o nombre
    de mes) se rechaza (False). Al evaluar ambas en la misma iteración se ejercita
    la discriminación fecha-vs-ruido sobre el espacio de entradas.
    """
    assert _es_encabezado_fecha(celda_fecha) is True
    assert _es_encabezado_fecha(celda_ruido) is False
