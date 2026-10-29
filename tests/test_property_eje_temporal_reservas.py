# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 2.3 — Derivación y orden cronológico del eje temporal.

Función bajo prueba: `_localizar_columnas_fecha(df)` en
`scripts/compras_ventas_reservas.py`.

Contrato (según el diseño, "Components and Interfaces" de `Lector_Reservas`):
  - Recibe una hoja leída con `header=None` (celdas crudas, entrada NO confiable).
  - Devuelve `(fila_encabezados, col_etiqueta, columnas)` donde `columnas` es la
    lista de `(indice_columna, primer_dia_del_mes)` ORDENADA cronológicamente
    ascendente.
  - Cada `primer_dia_del_mes` es `date(año, mes, 1)` del `Encabezado_Fecha` de esa
    columna (el resultado depende SOLO del (año, mes), no del día).

Property 2 verifica, con >= 100 iteraciones, que presentando los encabezados de
fecha en ORDEN ARBITRARIO el lector:
  - deriva para cada columna el `Mes_Absoluto` = (año, mes) de su fecha,
  - devuelve las columnas ordenadas cronológicamente ascendente,
  - el primer mes coincide con el mínimo de las fechas,
  - el resultado depende solo de las fechas y no del mes de inicio.
"""

import datetime as _dt

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import _localizar_columnas_fecha

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# Rango realista de fechas de encabezado (2020–2040), alineado con la Estrategia
# de Pruebas del diseño.
_ANIO_MIN = 2020
_ANIO_MAX = 2040


@st.composite
def conjunto_encabezados_fecha(draw):
    """
    Genera un conjunto de `Encabezado_Fecha` con (año, mes) DISTINTOS entre sí,
    presentados en ORDEN ARBITRARIO, junto con su representación de celda.

    - Los meses son únicos por (año, mes) para que el orden cronológico sea total
      y sin ambigüedad (el objetivo de Property 2 es el eje temporal derivado).
    - El día se elige arbitrariamente (1..28) para verificar que el resultado
      depende SOLO de (año, mes) y ancla al día 1.
    - Cada celda se serializa de forma variada (date / datetime / texto ISO) como
      llega del insumo real (Excel).

    Returns:
        list[tuple[object, _dt.date]]: pares (celda_cruda, mes_absoluto_esperado)
        en orden de presentación arbitrario.
    """
    n = draw(st.integers(min_value=2, max_value=8))
    pares_ym = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=_ANIO_MIN, max_value=_ANIO_MAX),
                st.integers(min_value=1, max_value=12),
            ),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    encabezados = []
    for anio, mes in pares_ym:
        dia = draw(st.integers(min_value=1, max_value=28))
        forma = draw(st.sampled_from(("date", "datetime", "texto_iso")))
        fecha = _dt.date(anio, mes, dia)
        if forma == "date":
            celda = fecha
        elif forma == "datetime":
            celda = _dt.datetime(anio, mes, dia)
        else:  # texto_iso
            celda = fecha.isoformat()
        esperado = _dt.date(anio, mes, 1)
        encabezados.append((celda, esperado))
    # Presentar en orden ARBITRARIO (independiente del mes de inicio).
    orden = draw(st.permutations(list(range(len(encabezados)))))
    return [encabezados[i] for i in orden]


def _construir_hoja(encabezados):
    """
    Construye una hoja `header=None` con una fila de etiqueta a la izquierda y una
    fila de encabezados de fecha, replicando la forma del insumo real.

    Layout (fila 0 = encabezados):
      col 0        : etiqueta de columna ("RAMO") — texto no fecha
      col 1..N     : encabezados de fecha en el orden de presentación dado
    Fila 1 aporta una fila de datos de ejemplo (irrelevante para la localización).
    """
    fila_encabezados = ["RAMO"] + [celda for celda, _ in encabezados]
    fila_datos = ["Etiqueta A"] + [0 for _ in encabezados]
    return pd.DataFrame([fila_encabezados, fila_datos])


# Feature: lectura-reservas-encabezados-fecha, Property 2: Derivación y orden
# cronológico del eje temporal. Para cualquier conjunto de Encabezado_Fecha en orden
# arbitrario, el lector deriva por columna el Mes_Absoluto = (año, mes), devuelve las
# columnas ordenadas cronológicamente ascendente, el primer mes coincide con el mínimo
# de las fechas, y el resultado depende solo de las fechas y no del mes de inicio.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(encabezados=conjunto_encabezados_fecha())
def test_prop2_derivacion_y_orden_cronologico(encabezados):
    """
    **Property 2: Derivación y orden cronológico del eje temporal**

    **Validates: Requirements 1.2, 1.3, 4.1, 4.4**
    """
    df = _construir_hoja(encabezados)

    _fila, _col_etiqueta, columnas = _localizar_columnas_fecha(df)

    # Se localizan EXACTAMENTE las columnas de fecha presentes (una por encabezado).
    assert len(columnas) == len(encabezados)

    meses_derivados = [mes for _idx, mes in columnas]
    meses_esperados = sorted(esperado for _celda, esperado in encabezados)

    # (Req 1.2) Cada Mes_Absoluto derivado = (año, mes) anclado al día 1, sin depender
    # del día del encabezado. Comparar el multiconjunto de meses derivados con el
    # esperado a partir de las fechas de entrada.
    assert sorted(meses_derivados) == meses_esperados

    # (Req 1.3, 4.4) Orden cronológico ascendente e independiente del orden de
    # presentación (mes de inicio arbitrario).
    assert meses_derivados == sorted(meses_derivados)

    # (Req 4.1) El primer mes coincide con el mínimo de las fechas presentes.
    assert meses_derivados[0] == meses_esperados[0]

    # Cada primer_dia_del_mes está efectivamente anclado al día 1 del mes.
    for _idx, mes in columnas:
        assert mes.day == 1
