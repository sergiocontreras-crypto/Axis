# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 6.2 — Persistencia con Mes_Absoluto válido y rechazo de meses mal formados.

Función bajo prueba: `persistir_input_reservas(df)` en
`scripts/compras_ventas_reservas.py`. En esta versión la persistencia es VERSIONADA:
escribe en la tabla `input_variacion_reservas_versiones` (no destructiva, con
`version_id` y `fecha_carga`), devuelve el número de filas persistidas (int) y valida
cada `mes` con `_normalizar_mes_reserva`, EXIGIENDO el formato estricto "YYYY-MM"
(año de 4 dígitos, mes 01-12).

Contrato bajo prueba (Req 5.1, 5.4 — Property 9):
  - CASO VÁLIDO: para cualquier DataFrame con meses "YYYY-MM" válidos, tras persistir,
    todo `mes` recuperado desde `input_variacion_reservas_versiones` cumple el patrón
    "YYYY-MM" (año de 4 dígitos, mes 01-12).
  - CASO MAL FORMADO: para cualquier `mes` que NO cumpla el patrón (año ≠ 4 dígitos,
    mes 00/13, texto libre, offset entero), `persistir_input_reservas` lanza
    `ValueError` SIN persistir nada de esa llamada.

La lectura de comprobación usa `comun.db.get_conn()` — la MISMA DB que escribe la
función (el fixture autouse `db_tmp` de conftest redirige `get_conn` a una SQLite
temporal por prueba).
"""

import re

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from comun.db import get_conn
from compras_ventas_reservas import (
    _TABLA_VERSIONES_RESERVAS,
    persistir_input_reservas,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre
# ejemplos, lo que se declara explícitamente como comportamiento esperado. Se suprime
# además `too_slow`: cada ejemplo abre/escribe/lee una SQLite temporal en disco (ruta de
# Google Drive), por lo que Hypothesis puede atribuir la latencia de E/S del cuerpo del
# test a la generación de entradas; no es un fallo de la propiedad.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]

# Patrón "YYYY-MM" estricto usado para verificar los meses recuperados.
_PATRON_YYYY_MM = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _leer_ultima_version_meses():
    """
    Lee de `input_variacion_reservas_versiones` la lista de valores `mes` de la
    versión MÁS RECIENTE (MAX(version_id)) usando la MISMA DB que escribe la función.

    Returns:
        list[str]: valores `mes` de la última versión (vacía si no hay versiones).
    """
    conn = get_conn()
    try:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLA_VERSIONES_RESERVAS,),
        ).fetchone()
        if not existe:
            return []
        fila_max = conn.execute(
            "SELECT MAX(version_id) FROM input_variacion_reservas_versiones"
        ).fetchone()
        version = fila_max[0] if fila_max is not None else None
        if version is None:
            return []
        filas = conn.execute(
            "SELECT mes FROM input_variacion_reservas_versiones WHERE version_id = ?",
            (version,),
        ).fetchall()
        return [str(f[0]) for f in filas]
    finally:
        conn.close()


def _contar_filas_versiones():
    """Total de filas en la tabla de versiones (para verificar 'nada persistido')."""
    conn = get_conn()
    try:
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLA_VERSIONES_RESERVAS,),
        ).fetchone()
        if not existe:
            return 0
        fila = conn.execute(
            "SELECT COUNT(*) FROM input_variacion_reservas_versiones"
        ).fetchone()
        return int(fila[0]) if fila is not None else 0
    finally:
        conn.close()


@st.composite
def mes_valido(draw):
    """Genera un `mes` VÁLIDO "YYYY-MM" (año 4 dígitos, mes 01-12)."""
    anio = draw(st.integers(min_value=2000, max_value=2099))
    mes = draw(st.integers(min_value=1, max_value=12))
    return f"{anio:04d}-{mes:02d}"


@st.composite
def df_variacion_valido(draw):
    """
    Genera un DataFrame de variación VÁLIDO con columnas {ramo, mes, delta_reserva}.

    Los meses son "YYYY-MM" válidos y las claves (ramo, mes) son ÚNICAS tras normalizar
    (`persistir_input_reservas` exige unicidad de la clave dentro de la versión). Se
    asigna un ramo DISTINTO por fila (RAMO_0, RAMO_1, …), lo que garantiza la unicidad
    de la clave con independencia del mes, sin bucles de reintento (generación rápida).
    Los deltas son finitos.
    """
    n = draw(st.integers(min_value=1, max_value=6))
    meses = draw(
        st.lists(mes_valido(), min_size=n, max_size=n)
    )
    deltas = draw(
        st.lists(
            st.floats(
                min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
            ),
            min_size=n,
            max_size=n,
        )
    )
    filas = [
        {"ramo": f"RAMO_{i}", "mes": meses[i], "delta_reserva": deltas[i]}
        for i in range(n)
    ]
    return pd.DataFrame(filas)


@st.composite
def mes_mal_formado(draw):
    """
    Genera un valor de `mes` MAL FORMADO que NO cumple "YYYY-MM":
      - año ≠ 4 dígitos ("26-12"),
      - mes fuera de rango ("2026-13", "2026-00"),
      - mes sin cero de relleno / no dos dígitos ("2026-7"),
      - texto libre ("abc"),
      - offset ENTERO (7) o su string ("7").
    """
    anio4 = draw(st.integers(min_value=2000, max_value=2099))
    opcion = draw(
        st.sampled_from(
            [
                "anio_2_digitos",
                "mes_13",
                "mes_00",
                "mes_1_digito",
                "texto_libre",
                "offset_int",
                "offset_str",
            ]
        )
    )
    if opcion == "anio_2_digitos":
        mes = draw(st.integers(min_value=1, max_value=12))
        return f"{anio4 % 100:02d}-{mes:02d}"  # p. ej. "26-12"
    if opcion == "mes_13":
        return f"{anio4:04d}-13"
    if opcion == "mes_00":
        return f"{anio4:04d}-00"
    if opcion == "mes_1_digito":
        mes = draw(st.integers(min_value=1, max_value=9))
        return f"{anio4:04d}-{mes}"  # p. ej. "2026-7"
    if opcion == "texto_libre":
        return draw(
            st.text(
                alphabet=st.characters(min_codepoint=97, max_codepoint=122),
                min_size=1,
                max_size=5,
            )
        )
    if opcion == "offset_int":
        return draw(st.integers(min_value=1, max_value=12))
    # offset_str
    return str(draw(st.integers(min_value=1, max_value=12)))


# Feature: lectura-reservas-encabezados-fecha, Property 9: Persistencia con Mes_Absoluto
# válido y rechazo de meses mal formados. Para cualquier DataFrame de variación válido,
# tras persistir, todo valor de `mes` recuperado cumple el patrón "YYYY-MM" (año de 4
# dígitos, mes 01-12); y para cualquier valor de mes que no cumpla ese patrón, el
# Persistidor_Reservas lo rechaza con ValueError sin persistir.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(df=df_variacion_valido())
def test_prop9_round_trip_mes_absoluto_valido(db_tmp, df):
    """
    **Property 9: Persistencia con Mes_Absoluto válido y rechazo de meses mal formados**
    (porción VÁLIDA / round-trip)

    **Validates: Requirements 5.1, 5.4**

    Persiste un DataFrame de variación con meses "YYYY-MM" válidos y verifica que TODO
    `mes` recuperado desde `input_variacion_reservas_versiones` cumple el patrón
    "YYYY-MM" (año de 4 dígitos, mes 01-12), y que el número de filas persistidas
    coincide con las filas de entrada.
    """
    n_persistidas = persistir_input_reservas(df)

    assert n_persistidas == len(df.index)

    meses_recuperados = _leer_ultima_version_meses()
    assert len(meses_recuperados) == len(df.index)
    for mes in meses_recuperados:
        assert _PATRON_YYYY_MM.fullmatch(mes), (
            f"mes recuperado no cumple 'YYYY-MM': {mes!r}"
        )


# Feature: lectura-reservas-encabezados-fecha, Property 9: Persistencia con Mes_Absoluto
# válido y rechazo de meses mal formados. Un mes que no cumple el patrón "YYYY-MM" debe
# provocar ValueError sin persistir nada de esa llamada.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(mes_malo=mes_mal_formado())
def test_prop9_rechazo_mes_mal_formado_sin_persistir(db_tmp, mes_malo):
    """
    **Property 9: Persistencia con Mes_Absoluto válido y rechazo de meses mal formados**
    (porción MAL FORMADA / rechazo)

    **Validates: Requirements 5.1, 5.4**

    Un DataFrame cuyo `mes` NO cumple el formato "YYYY-MM" (año ≠ 4 dígitos, mes 00/13,
    texto libre, offset entero) debe provocar `ValueError` y NO persistir ninguna fila
    de esa llamada (la tabla de versiones queda intacta).
    """
    filas_antes = _contar_filas_versiones()

    df_malo = pd.DataFrame(
        [{"ramo": "RAMO_A", "mes": mes_malo, "delta_reserva": 100.0}]
    )

    with pytest.raises(ValueError):
        persistir_input_reservas(df_malo)

    # No se persiste nada: el recuento total de filas no cambia por la llamada fallida.
    assert _contar_filas_versiones() == filas_antes
