# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 7.3 — Anclaje del `Mes_Absoluto` con convención `Mes_Caído`.

Función bajo prueba: `cargar_mapa_reservas(ruta_db, fecha_inicio, meses_horizonte)` en
`scripts/compras_ventas_reservas.py`, que lee la `Versión_Carga` MÁS RECIENTE de
`input_variacion_reservas_versiones` y ancla cada `Mes_Absoluto` "YYYY-MM" a la
`Fecha_Inicio_Proyeccion` aplicando la convención `Mes_Caído`
(design.md "Anclaje Mes_Absoluto → offset de proyección"; Req 7.1, 7.2).

Contrato bajo prueba (Property 13):
  Para cualquier `Fecha_Inicio_Proyeccion` y variación persistida en un `Mes_Absoluto`
  `M`, el `Cargador_Mapa_Reservas` ubica su delta en el offset de proyección `t` tal que
  el mes calendario de `t-1` es igual a `M` (convención `Mes_Caído`). En offsets:

      offset(M) = meses_entre(mes(fecha_inicio), M) + 1

  conservando SOLO los offsets dentro de `[1, meses_horizonte]`; el resto se omite (el
  motor asume Δ=0).

Estrategia de generación:
  - Se genera una `fecha_inicio` (primer día de mes) y un `meses_horizonte`.
  - Se genera un conjunto de filas `(ramo, mes_absoluto, delta)` con claves (ramo, mes)
    ÚNICAS por construcción, cuyos meses absolutos se sitúan en un rango que garantiza
    tanto offsets DENTRO como FUERA del horizonte (para ejercitar la omisión).
  - Se persiste con `persistir_input_reservas` (fuente de verdad versionada) y se llama
    a `cargar_mapa_reservas`. Se verifica que cada delta cae exactamente en el offset
    esperado por la fórmula `Mes_Caído`, y que los offsets fuera de `[1, horizonte]` se
    omiten.

La DB es la temporal por prueba (fixture autouse `db_tmp`). Como Hypothesis reutiliza el
fixture function-scoped entre ejemplos, la tabla versionada acumula versiones; sin
embargo `cargar_mapa_reservas` lee SIEMPRE la versión MÁS RECIENTE (MAX(fecha_carga),
desempate MAX(version_id)), que es la recién persistida en cada ejemplo, por lo que la
propiedad se evalúa contra esa última carga.
"""

from datetime import date

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import cargar_mapa_reservas, persistir_input_reservas

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre ejemplos.
# Se suprime esa health check (comportamiento esperado) y `too_slow` (el coste proviene
# del estado acumulado en la DB temporal, no de la generación de datos).
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


def _mes_absoluto(anio, mes):
    """Formatea (anio, mes) como `Mes_Absoluto` "YYYY-MM"."""
    return f"{anio:04d}-{mes:02d}"


def _offset_esperado(fecha_inicio, anio_m, mes_m):
    """
    Reproduce la fórmula de anclaje del diseño (Mes_Caído):

        offset(M) = meses_entre(mes(fecha_inicio), M) + 1

    donde `meses_entre` cuenta los meses calendario entre el mes de inicio y M.
    """
    meses_entre = (anio_m - fecha_inicio.year) * 12 + (mes_m - fecha_inicio.month)
    return meses_entre + 1


@st.composite
def _caso_anclaje(draw):
    """
    Genera un caso (fecha_inicio, meses_horizonte, filas) para probar el anclaje.

    - `fecha_inicio`: primer día de un mes en 2020..2035.
    - `meses_horizonte`: 1..24.
    - `filas`: lista de (ramo, anio, mes, delta) con clave (ramo, mes_absoluto) ÚNICA.
      Los meses absolutos se derivan de un desplazamiento entero `d` respecto del mes de
      inicio, en un rango [-3, horizonte + 3] para cubrir offsets DENTRO y FUERA de
      [1, horizonte] (la omisión de los que caen fuera es parte de la propiedad).

    Returns:
        tuple(date, int, list[dict]): (fecha_inicio, horizonte, filas).
    """
    anio_ini = draw(st.integers(min_value=2020, max_value=2035))
    mes_ini = draw(st.integers(min_value=1, max_value=12))
    fecha_inicio = date(anio_ini, mes_ini, 1)

    horizonte = draw(st.integers(min_value=1, max_value=24))

    # Desplazamientos en meses respecto del mes de inicio. offset = d + 1, por lo que
    # d en [0, horizonte-1] cae DENTRO de [1, horizonte]; d fuera de ese rango se omite.
    desplazamientos = draw(
        st.lists(
            st.integers(min_value=-3, max_value=horizonte + 3),
            min_size=1,
            max_size=8,
            unique=True,
        )
    )

    delta_strategy = st.floats(
        min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
    )

    filas = []
    for idx, d in enumerate(desplazamientos):
        total = (fecha_inicio.year * 12 + (fecha_inicio.month - 1)) + d
        anio_m, mes_m = divmod(total, 12)
        mes_m += 1
        delta = draw(delta_strategy)
        # Ramo único por fila para garantizar clave (ramo, mes) única y aislar cada
        # colocación (no dependemos de que los meses absolutos sean distintos entre sí).
        filas.append(
            {
                "ramo": f"RAMO{idx}",
                "anio": anio_m,
                "mes": mes_m,
                "delta": delta,
            }
        )
    return fecha_inicio, horizonte, filas


# Feature: lectura-reservas-encabezados-fecha, Property 13: Anclaje del Mes_Absoluto con
# convención Mes_Caído. Para cualquier Fecha_Inicio_Proyeccion y variación persistida en
# un Mes_Absoluto M, el Cargador_Mapa_Reservas ubica su delta en el offset de proyección
# t tal que el mes calendario de t-1 es igual a M, conservando solo los offsets dentro de
# [1, meses_horizonte].
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(caso=_caso_anclaje())
def test_prop13_anclaje_mes_caido(db_tmp, caso):
    """
    **Property 13: Anclaje del Mes_Absoluto con convención Mes_Caído**

    **Validates: Requirements 7.1, 7.2**

    Persiste una variación con meses absolutos arbitrarios y verifica que
    `cargar_mapa_reservas` coloca cada delta en el offset de proyección esperado según la
    fórmula `Mes_Caído` (`offset(M) = meses_entre(mes(fecha_inicio), M) + 1`), y que los
    offsets fuera de `[1, meses_horizonte]` se omiten del mapa devuelto.
    """
    fecha_inicio, horizonte, filas = caso

    # DataFrame de variación válido (claves (ramo, mes) únicas por construcción).
    df = pd.DataFrame(
        [
            {
                "ramo": f["ramo"],
                "mes": _mes_absoluto(f["anio"], f["mes"]),
                "delta_reserva": f["delta"],
                "tipo_salto": "mensual",
            }
            for f in filas
        ],
        columns=["ramo", "mes", "delta_reserva", "tipo_salto"],
    )

    persistir_input_reservas(df)

    # ruta_db es informativo: get_conn() abre la DB temporal redirigida por el fixture.
    mapa = cargar_mapa_reservas(str(db_tmp), fecha_inicio, horizonte)

    # Contenido esperado: para cada fila, calcular su offset con la fórmula Mes_Caído y
    # conservar solo los que caen en [1, horizonte].
    esperado = {}
    for f in filas:
        offset = _offset_esperado(fecha_inicio, f["anio"], f["mes"])
        if 1 <= offset <= horizonte:
            ramo_norm = f["ramo"].strip().upper()
            esperado.setdefault(ramo_norm, {})[offset] = float(f["delta"])

    # (1) Los ramos presentes en el mapa son exactamente los que tienen algún offset
    #     dentro del horizonte (los demás se omiten por completo).
    assert set(mapa.keys()) == set(esperado.keys()), (
        "el mapa incluye ramos sin ningún offset dentro del horizonte, o falta alguno"
    )

    # (2) Cada delta cae en el offset esperado (anclaje Mes_Caído) y los offsets fuera de
    #     [1, horizonte] fueron omitidos.
    for ramo, offsets_esp in esperado.items():
        offsets_obt = mapa[ramo]
        assert set(offsets_obt.keys()) == set(offsets_esp.keys()), (
            "los offsets colocados no coinciden con la fórmula Mes_Caído / la omisión "
            "fuera del horizonte"
        )
        for offset, delta_esp in offsets_esp.items():
            # Todo offset devuelto debe caer dentro del horizonte (invariante de la prop).
            assert 1 <= offset <= horizonte
            assert offsets_obt[offset] == delta_esp
