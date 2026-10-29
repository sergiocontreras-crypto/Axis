# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 6.4 — Descarte de filas no coercionables sin afectar a las válidas
(porción del PERSISTIDOR).

Función bajo prueba: `persistir_input_reservas(df)` en
`scripts/compras_ventas_reservas.py`, que persiste de forma VERSIONADA la variación
DERIVADA de reservas en la tabla `input_variacion_reservas_versiones`, PRESERVANDO
las cargas anteriores (Req 6). Descarta individualmente las filas con
`delta_reserva` NO FINITO (NaN/inf/no numérico) registrando una advertencia genérica
(sin volcar contenido de fila) y devuelve el número de filas efectivamente
persistidas (Req 11.5).

Contrato bajo prueba (Req 13.4 — Property 11):
  - Para un DataFrame que MEZCLE filas con `delta_reserva` no finito (NaN/inf) y filas
    válidas, el persistidor descarta ÚNICAMENTE las filas inválidas y persiste TODAS
    las válidas.
  - El valor de retorno es igual al número de filas válidas.
  - Al leer la versión más reciente de `input_variacion_reservas_versiones`, aparecen
    EXACTAMENTE las filas válidas y NINGUNA de las inválidas.

Nota de diseño del generador (evitar el camino de error de clave duplicada):
  `persistir_input_reservas` lanza `ValueError` ante una clave `(ramo, mes)` duplicada
  tras normalizar (la PK `(version_id, ramo, mes)` debe ser única dentro de la versión).
  Por eso el generador mantiene las claves `(ramo, mes)` ÚNICAS entre TODAS las filas
  (válidas e inválidas), de modo que el único comportamiento ejercitado sea el descarte
  de filas no coercionables.

La DB es la temporal por prueba (fixture autouse `db_tmp` de conftest.py), a la que se
accede vía `comun.db.get_conn` (monkeypatcheado por el fixture; el módulo bajo prueba
usa su propia referencia `get_conn`, también redirigida).
"""

import math

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    _TABLA_VERSIONES_RESERVAS,
    _normalizar_ramo,
    persistir_input_reservas,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre
# ejemplos, lo que se declara explícitamente como comportamiento esperado.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture]

# Columnas de la allowlist FIJA de la tabla versionada, en orden, para leer de vuelta
# la versión persistida sin interpolar entrada externa en el SQL.
_COLUMNAS_LECTURA = ("ramo", "mes", "delta_reserva")


# ── Generadores ──

# Etiquetas de ramo: texto no vacío con posibles espacios/case (el persistidor las
# normaliza con `_normalizar_ramo`).
_ramo_texto = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z
    min_size=1,
    max_size=6,
)

# Deltas finitos válidos (números reales acotados y representables).
_delta_finito = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Deltas NO FINITOS (no coercionables a numérico finito): NaN, +inf, -inf.
_delta_no_finito = st.sampled_from(
    [float("nan"), float("inf"), float("-inf")]
)


# Clave (ramo_crudo, año, mes) generada de una sola vez; la unicidad se garantiza a
# nivel de la clave NORMALIZADA (ramo_norm, "YYYY-MM") mediante `unique_by` en la lista,
# evitando el patrón lento de rejection-sampling con `draw` dentro de un bucle.
_clave_crudo = st.tuples(
    _ramo_texto,
    st.integers(min_value=2020, max_value=2040),
    st.integers(min_value=1, max_value=12),
)


def _mes_absoluto(anio: int, mes: int) -> str:
    """Formatea (año, mes) como Mes_Absoluto estricto 'YYYY-MM'."""
    return f"{anio:04d}-{mes:02d}"


def _clave_normalizada(clave_crudo) -> tuple[str, str]:
    """(ramo_crudo, año, mes) → (ramo_norm, 'YYYY-MM'), la clave que ve el persistidor."""
    ramo_crudo, anio, mes = clave_crudo
    return (_normalizar_ramo(ramo_crudo), _mes_absoluto(anio, mes))


@st.composite
def mezcla_filas(draw):
    """
    Genera un DataFrame que MEZCLA filas válidas (delta finito) y filas inválidas
    (delta NaN/inf), con claves `(ramo, mes)` ÚNICAS entre TODAS las filas.

    Estrategia de unicidad EFICIENTE: se genera de una sola vez una lista de claves
    crudas cuya clave NORMALIZADA `(ramo_norm, 'YYYY-MM')` es única (`unique_by`), y
    luego se reparte esa lista entre filas válidas e inválidas y se asignan deltas.
    Así jamás se dispara el camino de `ValueError` por clave duplicada, sin recurrir a
    rejection-sampling lento (que activa `HealthCheck.too_slow`).

    Returns:
        tuple[pandas.DataFrame, set[tuple[str, str]]]:
            - el DataFrame mezclado (orden de filas barajado), y
            - el conjunto de claves `(ramo_norm, mes)` de las filas VÁLIDAS esperadas.
    """
    # Al menos 1 clave (para 1 fila válida) y hasta 14 (8 válidas + 6 inválidas).
    claves_crudas = draw(
        st.lists(
            _clave_crudo,
            min_size=1,
            max_size=14,
            unique_by=_clave_normalizada,
        )
    )
    total = len(claves_crudas)
    # Al menos una fila válida; el resto (0..total-1) son inválidas.
    n_validas = draw(st.integers(min_value=1, max_value=total))

    # Deltas finitos para las filas válidas y no finitos para las inválidas.
    deltas_validos = draw(
        st.lists(_delta_finito, min_size=n_validas, max_size=n_validas)
    )
    n_invalidas = total - n_validas
    deltas_invalidos = draw(
        st.lists(_delta_no_finito, min_size=n_invalidas, max_size=n_invalidas)
    )

    filas: list[dict] = []
    validas_esperadas: set[tuple[str, str]] = set()

    for i in range(n_validas):
        ramo_crudo, anio, mes = claves_crudas[i]
        mes_txt = _mes_absoluto(anio, mes)
        filas.append(
            {"ramo": ramo_crudo, "mes": mes_txt, "delta_reserva": deltas_validos[i]}
        )
        validas_esperadas.add((_normalizar_ramo(ramo_crudo), mes_txt))

    for j in range(n_invalidas):
        ramo_crudo, anio, mes = claves_crudas[n_validas + j]
        mes_txt = _mes_absoluto(anio, mes)
        filas.append(
            {"ramo": ramo_crudo, "mes": mes_txt, "delta_reserva": deltas_invalidos[j]}
        )

    # Barajar el orden de las filas para no depender de la posición.
    orden = draw(st.permutations(list(range(len(filas)))))
    filas_barajadas = [filas[k] for k in orden]

    df = pd.DataFrame(filas_barajadas, columns=["ramo", "mes", "delta_reserva"])
    return df, validas_esperadas


def _leer_version_mas_reciente(ruta_db):
    """
    Lee de la DB temporal las filas `(ramo, mes, delta_reserva)` de la versión MÁS
    RECIENTE (mayor `version_id`) de `input_variacion_reservas_versiones`.

    Usa `comun.db.get_conn` (monkeypatcheado al archivo temporal por el fixture) y
    una consulta PARAMETRIZADA sobre una allowlist FIJA de columnas; nunca interpola
    entrada externa en el SQL. La tabla es un literal controlado por el código.

    Returns:
        set[tuple[str, str]]: conjunto de claves `(ramo, mes)` persistidas en la
        versión más reciente.
    """
    columnas_sql = ", ".join(_COLUMNAS_LECTURA)
    conn = cvr.get_conn()
    try:
        fila_max = conn.execute(
            f"SELECT MAX(version_id) FROM {_TABLA_VERSIONES_RESERVAS}"
        ).fetchone()
        version_id = fila_max[0] if fila_max is not None else None
        if version_id is None:
            return set(), {}
        filas = conn.execute(
            f"SELECT {columnas_sql} FROM {_TABLA_VERSIONES_RESERVAS} "
            "WHERE version_id = ?",
            (version_id,),
        ).fetchall()
    finally:
        conn.close()

    claves = {(str(r[0]), str(r[1])) for r in filas}
    deltas = {(str(r[0]), str(r[1])): float(r[2]) for r in filas}
    return claves, deltas


# Feature: lectura-reservas-encabezados-fecha, Property 11: Descarte de filas no
# coercionables sin afectar a las válidas. Para cualquier DataFrame que mezcle filas con
# delta_reserva no finito (NaN/inf/no numérico) y filas válidas, el Persistidor_Reservas
# descarta únicamente las filas inválidas (registrando una advertencia genérica) y
# persiste todas las válidas.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(datos=mezcla_filas())
def test_prop11_descarte_invalidas_sin_afectar_validas(db_tmp, datos):
    """
    **Property 11: Descarte de filas no coercionables sin afectar a las válidas**

    **Validates: Requirements 13.4**

    Mezcla filas con `delta_reserva` no finito (NaN/inf) y filas válidas (delta finito),
    con claves `(ramo, mes)` únicas entre todas las filas, y verifica que:
      - `persistir_input_reservas` devuelve exactamente el número de filas válidas, y
      - la versión más reciente persistida contiene EXACTAMENTE las filas válidas y
        NINGUNA de las inválidas.
    """
    df, validas_esperadas = datos

    n_validas_esperadas = len(validas_esperadas)

    # Persistir: las filas inválidas (delta no finito) deben descartarse sin abortar.
    n_persistidas = persistir_input_reservas(df)

    # (1) El retorno es igual al número de filas válidas.
    assert n_persistidas == n_validas_esperadas

    # (2) La versión más reciente contiene EXACTAMENTE las claves válidas y ninguna
    #     inválida.
    claves_persistidas, deltas_persistidos = _leer_version_mas_reciente(db_tmp)
    assert claves_persistidas == validas_esperadas

    # (3) Refuerzo: todo delta persistido es FINITO (ninguna fila NaN/inf llegó a la DB).
    for _clave, delta in deltas_persistidos.items():
        assert math.isfinite(delta)
