# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 2.2 — Localización de columnas de datos por `Encabezado_Fecha`.

Función bajo prueba: `_localizar_columnas_fecha(df)` en
`scripts/compras_ventas_reservas.py`.

Contrato (según "Components and Interfaces" del diseño, `Lector_Reservas`):
  - Recibe una hoja leída con `header=None` (DataFrame de celdas crudas).
  - Localiza la fila de encabezados (primera con >= 2 celdas de fecha), la
    columna-etiqueta y las columnas de datos por `Encabezado_Fecha`.
  - Devuelve `(fila_encabezados, col_etiqueta, columnas)` donde `columnas` es una
    lista de `(indice_columna, primer_dia_del_mes)` ORDENADA cronológicamente
    ascendente.

La propiedad (Property 1) exige que, dada una hoja con un conjunto arbitrario de
columnas de FECHA mezcladas con columnas de RUIDO no-fecha (numérico y nombres de
mes), el lector localice EXACTAMENTE esas columnas de fecha y ninguna otra,
independientemente de cuántas sean (Req 1.4, sin exigir 12 columnas).
"""

import datetime as _dt

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import _MESES_ORDEN, _localizar_columnas_fecha

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100


# ── Generador de un conjunto de meses-encabezado DISTINTOS ──
#
# Se generan (año, mes) únicos para que cada columna de fecha tenga un
# `primer_dia_del_mes` distinto: así el conteo y el orden cronológico son
# deterministas y no hay colisiones que hagan ambiguo el resultado esperado.
@st.composite
def _conjunto_meses(draw, min_n: int = 2, max_n: int = 8):
    n = draw(st.integers(min_value=min_n, max_value=max_n))
    pares = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=2020, max_value=2040),
                st.integers(min_value=1, max_value=12),
            ),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    return [_dt.date(anio, mes, 1) for (anio, mes) in pares]


# ── Formas en que una celda de fecha puede aparecer en el encabezado ──
def _celda_fecha(fecha, forma):
    """Materializa una fecha como date / datetime / texto ISO."""
    if forma == "date":
        return fecha
    if forma == "datetime":
        return _dt.datetime(fecha.year, fecha.month, fecha.day)
    return fecha.isoformat()  # "YYYY-MM-DD"


_formas_fecha = st.sampled_from(("date", "datetime", "texto_iso"))


# ── Celdas de RUIDO: NO deben reconocerse como Encabezado_Fecha ──
#
# Ruido numérico (offsets enteros / floats) y nombres de mes del formato viejo.
_NOMBRES_MES = list(_MESES_ORDEN)

_ruido_numerico = st.one_of(
    st.integers(min_value=-100, max_value=100),
    st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
)
_ruido_nombre_mes = st.sampled_from(_NOMBRES_MES).map(
    lambda nombre: nombre.title()
)
_celda_ruido = st.one_of(_ruido_numerico, _ruido_nombre_mes)


# Feature: lectura-reservas-encabezados-fecha, Property 1: Localización de columnas
# por Encabezado_Fecha.
#
# Para cualquier hoja cuyos encabezados contengan un conjunto de celdas de fecha en
# posiciones y valores arbitrarios (más columnas de ruido no-fecha), el
# `Lector_Reservas` localiza EXACTAMENTE ese conjunto de columnas de datos y ninguna
# otra.
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    meses=_conjunto_meses(),
    formas=st.data(),
    filas_arriba=st.integers(min_value=0, max_value=3),
    ruido_izq=st.integers(min_value=0, max_value=2),
    celdas_ruido=st.data(),
)
def test_prop1_localiza_exactamente_columnas_fecha(
    meses, formas, filas_arriba, ruido_izq, celdas_ruido
):
    """
    **Property 1: Localización de columnas por Encabezado_Fecha**

    **Validates: Requirements 1.1, 1.4**

    Se construye una hoja `header=None` con:
      - `filas_arriba` filas de ruido por encima del encabezado (para ejercitar el
        escaneo de filas superiores),
      - una columna-etiqueta de TEXTO,
      - opcionalmente columnas de ruido a la izquierda de las fechas,
      - un conjunto arbitrario de columnas de FECHA (distintos meses),
      - columnas de RUIDO (numérico / nombre de mes) intercaladas.

    Se afirma que `_localizar_columnas_fecha` localiza exactamente las columnas de
    fecha (mismo conjunto de índices) y las devuelve ordenadas cronológicamente,
    sin depender de la cantidad de columnas (Req 1.4).
    """
    # Layout de la fila de encabezados:
    #   [col 0: etiqueta texto]
    #   [ruido_izq columnas de ruido]
    #   luego se intercalan columnas de FECHA y columnas de RUIDO.
    # Registramos qué índice de columna corresponde a qué mes esperado.
    fila_encab: list[object] = []
    esperado_col_a_mes: dict[int, _dt.date] = {}

    # Columna-etiqueta de texto (no fecha, no nombre de mes) en la posición 0.
    fila_encab.append("Ramo")

    # Ruido a la izquierda de la primera fecha (números, no fechas).
    for _ in range(ruido_izq):
        fila_encab.append(celdas_ruido.draw(_ruido_numerico))

    # Intercalar columnas de fecha con columnas de ruido. Para cada mes decidimos si
    # antes de él va una celda de ruido, garantizando que las fechas queden en
    # posiciones arbitrarias.
    for fecha in meses:
        if celdas_ruido.draw(st.booleans()):
            fila_encab.append(celdas_ruido.draw(_celda_ruido))
        forma = formas.draw(_formas_fecha)
        idx = len(fila_encab)
        fila_encab.append(_celda_fecha(fecha, forma))
        esperado_col_a_mes[idx] = fecha

    # Posible ruido final a la derecha.
    if celdas_ruido.draw(st.booleans()):
        fila_encab.append(celdas_ruido.draw(_celda_ruido))

    n_cols = len(fila_encab)

    # Construir el DataFrame header=None: filas de ruido arriba + fila de encabezados
    # + una fila de datos numéricos debajo (irrelevante para la localización).
    filas: list[list[object]] = []
    for _ in range(filas_arriba):
        # Fila superior de ruido: números y textos que NO forman >= 2 fechas.
        filas.append([celdas_ruido.draw(_ruido_numerico) for _ in range(n_cols)])
    filas.append(fila_encab)
    filas.append([0 for _ in range(n_cols)])  # fila de datos

    df = pd.DataFrame(filas)

    fila_encabezados, col_etiqueta, columnas = _localizar_columnas_fecha(df)

    # 1) Se localiza EXACTAMENTE el conjunto de columnas de fecha (ni más ni menos).
    indices_localizados = [idx for (idx, _mes) in columnas]
    assert set(indices_localizados) == set(esperado_col_a_mes.keys())

    # 2) El mes derivado de cada columna coincide con el primer día del mes esperado.
    for idx, mes in columnas:
        assert mes == esperado_col_a_mes[idx]

    # 3) Las columnas se devuelven ordenadas cronológicamente ascendente (Req 1.3),
    #    lo que implica que el conteo es exactamente el número de fechas presentes
    #    (Req 1.4: sin exigir 12 columnas).
    meses_ordenados = [mes for (_idx, mes) in columnas]
    assert meses_ordenados == sorted(meses)
    assert len(columnas) == len(meses)

    # 4) La columna-etiqueta queda a la izquierda de la primera columna de fecha.
    primera_col_fecha = min(esperado_col_a_mes.keys())
    assert col_etiqueta < primera_col_fecha
