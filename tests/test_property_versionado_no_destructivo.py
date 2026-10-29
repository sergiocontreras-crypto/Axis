# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 6.5 — Versionado NO destructivo y selección de la más reciente.

Función bajo prueba: `persistir_input_reservas(df)` en
`scripts/compras_ventas_reservas.py`, que persiste la variación DERIVADA de reservas de
forma VERSIONADA en `input_variacion_reservas_versiones`, asignando
`version_id = MAX(version_id) + 1` y una marca temporal `fecha_carga`, SIN borrar nunca
las versiones anteriores (design.md Decisión 1; Req 6.1, 6.2).

Contrato bajo prueba (Req 6.1, 6.2, 6.3 — Property 12):
  - Cada carga sucesiva crea una `Versión_Carga` NUEVA (un `version_id` distinto y
    monotónicamente creciente) preservando TODAS las versiones anteriores intactas
    (los recuentos de filas por `version_id` no cambian; ninguna se borra).
  - La versión "más reciente" es seleccionable por `MAX(fecha_carga)`, desempatando por
    `MAX(version_id)`. Como `fecha_carga` tiene precisión de SEGUNDOS, cargas sucesivas
    en el mismo segundo pueden EMPATAR en `fecha_carga`; el diseño desempata por
    `MAX(version_id)`, por lo que la selección de "la más reciente" se apoya en el
    incremento monotónico de `version_id`.
  - El contenido de la versión más reciente coincide con el del último DataFrame
    persistido.

La DB es la temporal por prueba (fixture autouse `db_tmp`). La lectura de vuelta se hace
directamente con `comun.db.get_conn` usando consultas parametrizadas.

Nota de compatibilidad: el fixture `db_tmp` redirige `compras_ventas_reservas.get_conn`
a la DB temporal. Para leer de vuelta la MISMA DB, este test importa `get_conn` desde el
módulo bajo prueba (que es el nombre monkeypatcheado por el fixture), tal como exige la
tarea ("Read back via comun.db.get_conn").
"""

import math

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import compras_ventas_reservas as cvr
from compras_ventas_reservas import persistir_input_reservas

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre
# ejemplos, lo que se declara explícitamente como comportamiento esperado. Además, como
# la DB temporal se reutiliza entre ejemplos y acumula versiones, se suprime `too_slow`
# (el coste no proviene de la generación de datos sino del estado acumulado en la DB).
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


# ── Generadores ──

# Deltas finitos (el persistidor descarta no finitos; aquí solo generamos válidos).
_delta_strategy = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)


def _mes_desde_indice(indice):
    """Convierte un índice entero en un `Mes_Absoluto` "YYYY-MM" válido y determinista."""
    anio = 2020 + (indice // 12)
    mes = (indice % 12) + 1
    return f"{anio:04d}-{mes:02d}"


@st.composite
def _dataframe_variacion(draw):
    """
    Genera un DataFrame de variación VÁLIDO con claves (ramo, mes) ÚNICAS.

    El persistidor exige unicidad de la clave (ramo, mes) dentro de una versión. Para que
    la generación sea BARATA (evitando rechazos por `unique=True` sobre un alfabeto
    pequeño), las claves se derivan de índices enteros distintos: cada índice `i` produce
    la clave `("R{i}", mes(i))`, garantizando unicidad por construcción.

    Returns:
        pandas.DataFrame: columnas {ramo, mes, delta_reserva, tipo_salto}.
    """
    indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=200),
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    filas = []
    for i in indices:
        delta = draw(_delta_strategy)
        tipo = draw(st.sampled_from(("mensual", "anual")))
        filas.append(
            {
                "ramo": f"R{i}",
                "mes": _mes_desde_indice(i),
                "delta_reserva": delta,
                "tipo_salto": tipo,
            }
        )
    return pd.DataFrame(filas, columns=["ramo", "mes", "delta_reserva", "tipo_salto"])


def _contenido_esperado(df):
    """
    Reproduce la normalización del persistidor (strip/upper ramo, mes "YYYY-MM",
    tipo_salto) para comparar el contenido recuperado con el DataFrame de entrada.

    Returns:
        dict[(ramo, mes) -> (delta, tipo)]: contenido canónico esperado en la DB.
    """
    esperado = {}
    for _, fila in df.iterrows():
        delta = float(fila["delta_reserva"])
        if not math.isfinite(delta):
            continue
        ramo = str(fila["ramo"]).strip().upper()
        mes = str(fila["mes"])
        tipo = str(fila["tipo_salto"]).strip().lower()
        esperado[(ramo, mes)] = (delta, tipo)
    return esperado


def _tabla_versiones_existe(conn):
    """True si la tabla versionada ya fue creada (aún no existe antes de la 1ª carga)."""
    fila = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = ?",
        ("input_variacion_reservas_versiones",),
    ).fetchone()
    return fila is not None


def _leer_conteos_por_version():
    """
    Devuelve {version_id: n_filas} leyendo la DB temporal (consulta parametrizada).

    Antes de la primera carga la tabla versionada no existe (la crea el persistidor de
    forma idempotente); en ese caso se devuelve un mapa vacío.
    """
    conn = cvr.get_conn()
    try:
        if not _tabla_versiones_existe(conn):
            return {}
        filas = conn.execute(
            "SELECT version_id, COUNT(*) "
            "FROM input_variacion_reservas_versiones "
            "GROUP BY version_id"
        ).fetchall()
    finally:
        conn.close()
    return {int(v): int(c) for (v, c) in filas}


def _leer_contenido_version_mas_reciente():
    """
    Lee el contenido de la versión más reciente seleccionada por MAX(version_id)
    (equivalente a MAX(fecha_carga) desempatando por version_id: como fecha_carga tiene
    precisión de segundos y puede empatar, el incremento monotónico de version_id es la
    señal fiable de "la más reciente"; ver docstring del módulo).

    Returns:
        tuple[int, dict[(ramo, mes) -> (delta, tipo)]]: (version_id, contenido).
    """
    conn = cvr.get_conn()
    try:
        fila_max = conn.execute(
            "SELECT MAX(version_id) FROM input_variacion_reservas_versiones"
        ).fetchone()
        version_id = fila_max[0] if fila_max is not None else None
        if version_id is None:
            return None, {}
        filas = conn.execute(
            "SELECT ramo, mes, delta_reserva, tipo_salto "
            "FROM input_variacion_reservas_versiones "
            "WHERE version_id = ?",
            (int(version_id),),
        ).fetchall()
    finally:
        conn.close()
    contenido = {
        (str(r), str(m)): (float(d), str(t)) for (r, m, d, t) in filas
    }
    return int(version_id), contenido


# Feature: lectura-reservas-encabezados-fecha, Property 12: Versionado no destructivo y
# selección de la más reciente. Para cualquier secuencia de cargas sucesivas, cada carga
# crea una Versión_Carga nueva preservando todas las anteriores, y la versión más reciente
# (MAX(version_id)/MAX(fecha_carga)) contiene el último DataFrame persistido.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(dataframes=st.lists(_dataframe_variacion(), min_size=1, max_size=5))
def test_prop12_versionado_no_destructivo_y_seleccion_reciente(db_tmp, dataframes):
    """
    **Property 12: Versionado no destructivo y selección de la más reciente**

    **Validates: Requirements 6.1, 6.2, 6.3**

    Realiza una secuencia de N persistencias sucesivas con DataFrames distintos y verifica:
      (a) tras cada persistencia, todas las versiones previas siguen existiendo con su
          recuento de filas intacto (versionado NO destructivo, Req 6.1, 6.2);
      (b) cada persistencia crea un `version_id` nuevo y distinto (monotónico creciente);
      (c) la versión más reciente (MAX(version_id)) coincide con el contenido del último
          DataFrame persistido (selección de la más reciente, Req 6.3).

    El fixture autouse `db_tmp` es function-scoped y Hypothesis lo REUTILIZA entre
    ejemplos, por lo que la DB puede contener versiones de ejemplos previos (por eso se
    suprime la health check `function_scoped_fixture`). Para que la propiedad sea robusta
    frente a esa reutilización, la no-destrucción se verifica RELATIVA a la línea base
    capturada al inicio del ejemplo: las versiones preexistentes deben permanecer
    intactas, y cada carga añade exactamente una versión nueva monotónicamente creciente.
    """
    # Línea base: conteos por versión ya presentes de ejemplos previos (DB reutilizada).
    conteos_base = _leer_conteos_por_version()
    max_version_previo = max(conteos_base.keys(), default=0)

    conteos_esperados = dict(conteos_base)  # version_id -> n_filas (base + nuevas)
    version_ids_nuevos = []

    for offset, df in enumerate(dataframes, start=1):
        n_persistidas = persistir_input_reservas(df)

        # El version_id asignado es MAX(version_id) previo + 1 → monotónico creciente y
        # distinto de todos los anteriores (los de la base y los nuevos de este ejemplo).
        version_id_esperado = max_version_previo + offset
        assert version_id_esperado not in conteos_esperados, (
            "el version_id no es nuevo/distinto en cada carga"
        )
        version_ids_nuevos.append(version_id_esperado)
        conteos_esperados[version_id_esperado] = n_persistidas

        # (a)+(b) Todas las versiones previas (base + cargas anteriores de este ejemplo)
        # se preservan con su recuento intacto; solo se AÑADE la nueva versión.
        conteos_actuales = _leer_conteos_por_version()
        assert conteos_actuales == conteos_esperados, (
            "una carga sucesiva alteró o borró versiones anteriores (destructivo)"
        )

        # (c) La versión más reciente coincide con el DataFrame recién persistido.
        version_reciente, contenido_reciente = _leer_contenido_version_mas_reciente()
        assert version_reciente == version_id_esperado, (
            "MAX(version_id) no corresponde a la última carga"
        )
        esperado = _contenido_esperado(df)
        # Comparación de claves y valores (con tolerancia flotante en delta).
        assert set(contenido_reciente.keys()) == set(esperado.keys())
        for clave, (delta_esp, tipo_esp) in esperado.items():
            delta_obt, tipo_obt = contenido_reciente[clave]
            assert tipo_obt == tipo_esp
            assert math.isclose(delta_obt, delta_esp, rel_tol=1e-9, abs_tol=1e-9)

    # Comprobación final: se añadieron exactamente N versiones nuevas, todas distintas y
    # monotónicamente crecientes por encima de la línea base.
    assert version_ids_nuevos == list(
        range(max_version_previo + 1, max_version_previo + len(dataframes) + 1)
    )
