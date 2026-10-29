# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 2.4 — Rechazo de formatos de encabezado inválidos por forma del encabezado.

Función bajo prueba: `_localizar_columnas_fecha(df)` en
`scripts/compras_ventas_reservas.py`, que localiza las columnas de datos por
`Encabezado_Fecha` y debe RECHAZAR (lanzando `ErrorFormatoEncabezado`) las hojas
cuyos encabezados sean:
  - nombres de mes ("ENERO", "FEB", ...), el formato viejo (Req 2.2), o
  - cualquier hoja sin ninguna columna con encabezado de fecha reconocible
    (Req 2.1, 2.3).

Cuando se lanza `ErrorFormatoEncabezado`, la función NO devuelve columnas: no se
derivan variaciones (la localización aborta antes de producir el eje temporal).

La hoja se modela como se lee del workbook: `pd.DataFrame` con `header=None`
(entrada NO confiable) donde la primera fila superior contiene los encabezados.
"""

import datetime as _dt

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import (
    _MESES_ORDEN,
    _localizar_columnas_fecha,
    ErrorFormatoEncabezado,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100


# ── Etiquetas de ramo (columna-etiqueta a la izquierda de los encabezados) ──
_etiquetas_ramo = st.sampled_from(
    ["VIDA", "RIESGOS LABORALES", "PENSIONES", "SALUD", "RAMO A", "TOTAL CAPI"]
)


# ── Ruido NUMÉRICO / de texto libre que NO es fecha ni nombre de mes ──
# Números (offset entero, floats, números serializados) y texto arbitrario sin
# fecha reconocible. Ninguno debe reconocerse como `Encabezado_Fecha`.
_texto_no_fecha = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z
    min_size=1,
    max_size=8,
).filter(lambda s: s.strip().upper() not in _MESES_ORDEN)

_celda_no_fecha = st.one_of(
    st.integers(min_value=-100, max_value=100),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=0, max_value=100).map(str),
    _texto_no_fecha,
)


# ── Nombres de mes (formato viejo) con casing y espacios arbitrarios ──
_NOMBRES_MES = list(_MESES_ORDEN) + [
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "SEPT", "OCT", "NOV", "DIC",
]


@st.composite
def _fila_nombres_mes(draw):
    """
    Fila de encabezados con NOMBRES DE MES (nunca fechas), en distinto casing y con
    espacios alrededor (entrada no confiable). Debe rechazarse (Req 2.2).
    """
    n = draw(st.integers(min_value=2, max_value=12))
    nombres = draw(
        st.lists(st.sampled_from(_NOMBRES_MES), min_size=n, max_size=n)
    )
    casing = draw(st.sampled_from(("upper", "lower", "title")))
    celdas = []
    for nombre in nombres:
        val = getattr(nombre, casing)()
        pre = draw(st.text(alphabet=" \t", max_size=2))
        post = draw(st.text(alphabet=" \t", max_size=2))
        celdas.append(f"{pre}{val}{post}")
    return celdas


@st.composite
def _fila_sin_fecha(draw):
    """
    Fila de encabezados SIN NINGUNA celda de fecha reconocible: solo ruido numérico
    y/o texto libre. Debe rechazarse (Req 2.1, 2.3).
    """
    n = draw(st.integers(min_value=1, max_value=12))
    return draw(st.lists(_celda_no_fecha, min_size=n, max_size=n))


@st.composite
def _hoja_invalida(draw):
    """
    Construye una hoja (`pd.DataFrame`, `header=None`) cuya fila de encabezados es
    inválida: nombres de mes, o sin ninguna fecha reconocible. La primera columna es
    la columna-etiqueta (texto de ramo) para reflejar la estructura real del insumo.
    """
    tipo = draw(st.sampled_from(("nombres_mes", "sin_fecha")))
    if tipo == "nombres_mes":
        encabezados = draw(_fila_nombres_mes())
    else:
        encabezados = draw(_fila_sin_fecha())

    etiqueta = draw(_etiquetas_ramo)
    # Fila de encabezados: [etiqueta, *encabezados_invalidos]
    fila_encabezado = [etiqueta] + list(encabezados)

    # Una fila de datos numéricos debajo, para simular un insumo con contenido.
    fila_datos = [draw(_etiquetas_ramo)] + [
        draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
        for _ in encabezados
    ]

    return pd.DataFrame([fila_encabezado, fila_datos])


# Feature: lectura-reservas-encabezados-fecha, Property 4: Saltos inválidos señalan
# error de formato
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(df=_hoja_invalida())
def test_prop4_encabezados_invalidos_rechazados(df):
    """
    **Property 4: Saltos inválidos señalan error de formato** (parcial: forma del
    encabezado)

    **Validates: Requirements 2.1, 2.2, 2.3**

    Para cualquier hoja cuyos encabezados sean nombres de mes ("ENERO", ...) o no
    contengan ninguna fecha reconocible, `_localizar_columnas_fecha` lanza
    `ErrorFormatoEncabezado` y no deriva columnas (no produce eje temporal).
    """
    columnas_localizadas = None
    try:
        columnas_localizadas = _localizar_columnas_fecha(df)
        lanzo_error = False
    except ErrorFormatoEncabezado:
        lanzo_error = True

    assert lanzo_error is True, (
        "Se esperaba ErrorFormatoEncabezado para encabezados inválidos"
    )
    # No se derivan variaciones: la localización no devolvió columnas.
    assert columnas_localizadas is None


# ── Control de sanidad: una hoja con >= 2 encabezados de fecha SÍ se localiza ──
# Garantiza que el generador de rechazos no es trivialmente cierto (que la función
# no rechaza SIEMPRE), reforzando que el rechazo se debe a la forma del encabezado.
@st.composite
def _hoja_valida_fechas(draw):
    """Hoja con una fila de >= 2 encabezados de fecha (primer día de mes)."""
    anio = draw(st.integers(min_value=2020, max_value=2040))
    n = draw(st.integers(min_value=2, max_value=6))
    meses = draw(
        st.lists(st.integers(min_value=1, max_value=12), min_size=n, max_size=n, unique=True)
    )
    fechas = [_dt.date(anio, m, 1) for m in meses]
    etiqueta = draw(_etiquetas_ramo)
    fila_encabezado = [etiqueta] + fechas
    fila_datos = [draw(_etiquetas_ramo)] + [
        draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
        for _ in fechas
    ]
    return pd.DataFrame([fila_encabezado, fila_datos])


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(df=_hoja_valida_fechas())
def test_prop4_control_hoja_valida_no_rechazada(df):
    """
    Control: una hoja con >= 2 `Encabezado_Fecha` NO se rechaza; se localizan las
    columnas de fecha. Confirma que el rechazo de la prueba principal es específico
    a la forma inválida del encabezado, no un rechazo indiscriminado.
    """
    fila_encabezados, col_etiqueta, columnas = _localizar_columnas_fecha(df)
    assert isinstance(columnas, list) and len(columnas) >= 2
