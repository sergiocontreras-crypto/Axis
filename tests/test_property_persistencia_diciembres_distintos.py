# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 6.3 — Inyectividad de meses absolutos para diciembres de años distintos
(porción de PERSISTENCIA / storage).

Función bajo prueba: `persistir_input_reservas(df)` en
`scripts/compras_ventas_reservas.py`, que persiste de forma VERSIONADA el DataFrame de
variación de reservas en la tabla `input_variacion_reservas_versiones` (una `Versión_Carga`
nueva por llamada, sin sobrescribir cargas previas).

Contrato bajo prueba (Req 5.3, 9.4 — Property 10, porción de storage):
  - Al persistir múltiples columnas de diciembre de años DISTINTOS ("2026-12",
    "2027-12", "2028-12", …) —para uno o más ramos— cada diciembre queda almacenado
    bajo una clave `mes` "YYYY-MM" DISTINTA, SIN colisión. En particular, el número de
    pares distintos `(ramo, mes)` recuperados de la versión más reciente es igual al
    número de pares `(ramo, mes)` de la entrada.

Estrategia:
  - Se construyen DataFrames directamente (esta es la porción de storage; la derivación
    del lector se prueba aparte en la tarea 4.6).
  - Se persiste con `persistir_input_reservas` y se lee de vuelta la versión MÁS RECIENTE
    (`MAX(version_id)`) de `input_variacion_reservas_versiones` vía `comun.db.get_conn`.
  - La DB es la temporal por prueba (fixture autouse `db_tmp`).
"""

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import persistir_input_reservas
from comun.db import get_conn

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre
# ejemplos, lo que se declara explícitamente como comportamiento esperado. Se
# suprime además `too_slow`: cada ejemplo persiste en SQLite (I/O real), por lo
# que la ronda de generación+ejecución puede exceder el umbral por defecto sin
# que ello indique una violación de la propiedad.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]

# Ramos de portafolio de ejemplo (ya en forma canónica normalizada: strip/upper).
_RAMOS = ("RENTAS VOLUNTARIAS", "CAPITALIZADORA", "SALUD", "AUTOS", "VIDA GRUPO")


def _leer_version_mas_reciente():
    """
    Lee de la tabla versionada `input_variacion_reservas_versiones` las filas
    (ramo, mes) de la Versión_Carga MÁS RECIENTE (`MAX(version_id)`), usando la
    conexión de `comun.db.get_conn` (redirigida a la DB temporal por el fixture).

    Returns:
        list[tuple[str, str]]: pares (ramo, mes) de la versión más reciente.
    """
    conn = get_conn()
    try:
        fila_max = conn.execute(
            "SELECT MAX(version_id) FROM input_variacion_reservas_versiones"
        ).fetchone()
        version_id = fila_max[0] if fila_max is not None else None
        if version_id is None:
            return []
        filas = conn.execute(
            "SELECT ramo, mes FROM input_variacion_reservas_versiones "
            "WHERE version_id = ?",
            (version_id,),
        ).fetchall()
        return [(str(r), str(m)) for (r, m) in filas]
    finally:
        conn.close()


@st.composite
def diciembres_distintos_por_ramo(draw):
    """
    Genera un conjunto de filas de variación con columnas de diciembre de años
    DISTINTOS, para uno o más ramos.

    Para cada ramo seleccionado se eligen años DISTINTOS y se produce una fila
    `(ramo, "YYYY-12", delta, 'anual')` por cada año. Las claves de diciembre difieren
    solo por el año, por lo que ejercitan la inyectividad de `Mes_Absoluto` frente a
    múltiples diciembres.

    Returns:
        list[tuple[str, str, float, str]]: filas (ramo, mes "YYYY-12", delta, tipo_salto).
    """
    n_ramos = draw(st.integers(min_value=1, max_value=3))
    ramos = draw(
        st.lists(
            st.sampled_from(_RAMOS),
            min_size=n_ramos,
            max_size=n_ramos,
            unique=True,
        )
    )

    filas: "list[tuple[str, str, float, str]]" = []
    for ramo in ramos:
        # Al menos 2 diciembres de años distintos por ramo (varios diciembres).
        anios = draw(
            st.lists(
                st.integers(min_value=2020, max_value=2060),
                min_size=2,
                max_size=5,
                unique=True,
            )
        )
        for i, anio in enumerate(anios):
            delta = float(1000 + 17 * i)  # finito, arbitrario
            filas.append((ramo, f"{anio:04d}-12", delta, "anual"))

    return filas


# Feature: lectura-reservas-encabezados-fecha, Property 10: Inyectividad de meses
# absolutos para diciembres de años distintos
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(filas=diciembres_distintos_por_ramo())
def test_prop10_diciembres_distintos_storage(db_tmp, filas):
    """
    **Property 10: Inyectividad de meses absolutos para diciembres de años distintos**
    (porción de storage / persistencia)

    **Validates: Requirements 5.3, 9.4**

    Persiste múltiples columnas de diciembre de años distintos ("2026-12", "2027-12",
    "2028-12", …) para uno o más ramos y verifica que cada diciembre queda almacenado
    bajo una clave `mes` "YYYY-MM" DISTINTA, sin colisión: el número de pares distintos
    `(ramo, mes)` recuperados de la versión más reciente es igual al número de pares
    `(ramo, mes)` de la entrada.
    """
    df = pd.DataFrame(
        filas, columns=["ramo", "mes", "delta_reserva", "tipo_salto"]
    )

    pares_entrada = {(r, m) for (r, m, _d, _t) in filas}
    # La estrategia genera (ramo, año) únicos, así que no hay duplicados de entrada.
    assert len(pares_entrada) == len(filas)

    n_persistidas = persistir_input_reservas(df)
    assert n_persistidas == len(filas)

    pares_guardados = _leer_version_mas_reciente()

    # Sin colisiones en almacenamiento: cada (ramo, mes) aparece exactamente una vez.
    assert len(pares_guardados) == len(set(pares_guardados))

    # Inyectividad: la cantidad de pares distintos (ramo, mes) guardados es igual a la
    # cantidad de pares de entrada, y el conjunto coincide.
    assert set(pares_guardados) == pares_entrada
    assert len(set(pares_guardados)) == len(pares_entrada)

    # Refuerzo: dentro de cada ramo, cada diciembre (año) es una clave distinta.
    for ramo in {r for (r, _m) in pares_guardados}:
        meses_ramo = [m for (r, m) in pares_guardados if r == ramo]
        diciembres = [m for m in meses_ramo if m.endswith("-12")]
        assert len(diciembres) == len(set(diciembres)), (
            f"diciembres duplicados para ramo {ramo!r}"
        )
