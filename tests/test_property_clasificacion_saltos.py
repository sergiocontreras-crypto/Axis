# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 3.2 — Clasificación de saltos por diferencia de meses.

Función bajo prueba: `_clasificar_saltos(columnas)` en
`scripts/compras_ventas_reservas.py`.

Contrato (Req 3.1, 3.2, 3.3):
  - Para columnas consecutivas ya ordenadas cronológicamente, un salto de 1 mes se
    clasifica como `'mensual'` (Salto_Mensual) y un salto de 12 meses como `'anual'`
    (Salto_Anual, diciembre-a-diciembre).
  - Para una secuencia con los meses del año en curso seguida de solo los diciembres
    de años siguientes (patrón MIXTO del insumo real), todos los saltos del año en
    curso son mensuales y los diciembre-a-diciembre son anuales.

El generador de patrón mixto produce [meses m0..dic del año Y, luego dic(Y+1),
dic(Y+2), …] para ejercitar ambas clasificaciones en la misma secuencia, con >= 100
iteraciones por prueba de propiedad.
"""

import datetime as _dt

from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import _clasificar_saltos

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100


def _primer_dia(anio: int, mes: int) -> _dt.date:
    """Primer día del mes/año, la forma normalizada de `Mes_Absoluto`."""
    return _dt.date(anio, mes, 1)


@st.composite
def patron_mixto(draw):
    """
    Genera el patrón MIXTO del insumo real de reservas:

      1. Una secuencia contigua de meses del año en curso `Y`: desde un mes de inicio
         `m0` hasta diciembre de `Y` (al menos dos meses para que haya un salto
         mensual).
      2. Luego SOLO diciembre de los años siguientes: `dic(Y+1), dic(Y+2), …`.

    Devuelve `(columnas, n_saltos_mensuales, n_saltos_anuales)` donde `columnas` es la
    lista `(indice_columna, primer_dia_del_mes)` ORDENADA cronológicamente que espera
    `_clasificar_saltos`.

    - Los saltos dentro del bloque del año en curso son de 1 mes -> 'mensual'.
    - El salto de dic(Y) a dic(Y+1) y los sucesivos son de 12 meses -> 'anual'.
    """
    anio = draw(st.integers(min_value=2020, max_value=2035))
    # Mes de inicio del año en curso (1..11) para garantizar al menos un salto mensual
    # hasta diciembre. No se asume un mes de inicio fijo (Req 4).
    mes_inicio = draw(st.integers(min_value=1, max_value=11))
    # Cuántos diciembres de años siguientes se añaden (0..5).
    anios_siguientes = draw(st.integers(min_value=0, max_value=5))

    fechas: "list[_dt.date]" = []

    # Bloque del año en curso: meses contiguos hasta diciembre.
    for mes in range(mes_inicio, 13):
        fechas.append(_primer_dia(anio, mes))

    # Diciembres de años siguientes.
    for k in range(1, anios_siguientes + 1):
        fechas.append(_primer_dia(anio + k, 12))

    # Saltos mensuales: pares consecutivos dentro del bloque del año en curso.
    n_mensuales = (12 - mes_inicio)
    # Saltos anuales: dic(Y)->dic(Y+1), dic(Y+1)->dic(Y+2), ...
    n_anuales = anios_siguientes

    columnas = [(idx, fecha) for idx, fecha in enumerate(fechas)]
    return columnas, n_mensuales, n_anuales


# Feature: lectura-reservas-encabezados-fecha, Property 3: Clasificación de saltos por
# diferencia de meses. Para cualquier par de columnas consecutivas ordenadas
# cronológicamente, el salto es 'mensual' si difieren en 1 mes y 'anual' si difieren en
# 12 meses; en el patrón mixto (meses del año en curso + diciembres siguientes) todos
# los saltos del año en curso son mensuales y los diciembre-a-diciembre son anuales.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(datos=patron_mixto())
def test_prop3_clasificacion_saltos_patron_mixto(datos):
    """
    **Property 3: Clasificación de saltos por diferencia de meses**

    **Validates: Requirements 3.1, 3.2, 3.3**

    Sobre el patrón mixto del insumo real (meses del año en curso seguidos de solo los
    diciembres de años siguientes), `_clasificar_saltos` produce exactamente:
      - un salto por cada par consecutivo,
      - 'mensual' para los saltos de 1 mes del año en curso (Req 3.1),
      - 'anual' para los saltos de 12 meses diciembre-a-diciembre (Req 3.2),
    reproduciendo el comportamiento esperado del patrón mixto completo (Req 3.3).
    """
    columnas, n_mensuales_esperados, n_anuales_esperados = datos

    saltos = _clasificar_saltos(columnas)

    # Un salto por cada par consecutivo.
    assert len(saltos) == max(len(columnas) - 1, 0)

    tipos = [tipo for (_, _, tipo, _) in saltos]
    assert tipos.count("mensual") == n_mensuales_esperados
    assert tipos.count("anual") == n_anuales_esperados
    # No hay otros tipos posibles.
    assert set(tipos) <= {"mensual", "anual"}

    # Cada salto es coherente con la diferencia de meses real entre sus columnas y su
    # `mes_absoluto_hasta` corresponde a la columna destino.
    for (col_desde, col_hasta, tipo, mes_hasta) in saltos:
        _, mes_desde_fecha = columnas[col_desde]
        _, mes_hasta_fecha = columnas[col_hasta]
        delta_meses = (mes_hasta_fecha.year - mes_desde_fecha.year) * 12 + (
            mes_hasta_fecha.month - mes_desde_fecha.month
        )
        if tipo == "mensual":
            assert delta_meses == 1
        else:  # 'anual'
            assert delta_meses == 12
        assert mes_hasta == mes_hasta_fecha


# Feature: lectura-reservas-encabezados-fecha, Property 3: Clasificación de saltos por
# diferencia de meses. Complemento que ejercita saltos aislados de 1 y de 12 meses en
# posiciones/años arbitrarios para cubrir directamente Req 3.1 y 3.2.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    anio=st.integers(min_value=2020, max_value=2039),
    mes=st.integers(min_value=1, max_value=12),
    tipo_esperado=st.sampled_from(("mensual", "anual")),
)
def test_prop3_salto_individual_1_o_12_meses(anio, mes, tipo_esperado):
    """
    **Property 3: Clasificación de saltos por diferencia de meses**

    **Validates: Requirements 3.1, 3.2**

    Un par de columnas consecutivas que difiere en 1 mes se clasifica 'mensual' y uno
    que difiere en 12 meses se clasifica 'anual', para cualquier mes/año de partida.
    """
    inicio = _primer_dia(anio, mes)
    delta = 1 if tipo_esperado == "mensual" else 12

    # Segundo mes = inicio + delta meses (aritmética de calendario).
    total = (mes - 1) + delta
    anio_hasta = anio + total // 12
    mes_hasta = total % 12 + 1
    fin = _primer_dia(anio_hasta, mes_hasta)

    columnas = [(0, inicio), (1, fin)]
    saltos = _clasificar_saltos(columnas)

    assert len(saltos) == 1
    col_desde, col_hasta, tipo, mes_absoluto_hasta = saltos[0]
    assert (col_desde, col_hasta) == (0, 1)
    assert tipo == tipo_esperado
    assert mes_absoluto_hasta == fin
