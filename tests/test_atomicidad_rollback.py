# -*- coding: utf-8 -*-
"""
tests/test_atomicidad_rollback.py
---------------------------------
Prueba basada en propiedades (Hypothesis) para la Property 7 del diseño de la
feature `escalabilidad-derivados-portafolios`.

**Property 7: Atomicidad de las escrituras (rollback)**

    Para toda operación de alta de portafolio o asignación de subyacentes que
    falle antes de completarse, el estado de la tabla afectada tras el fallo es
    idéntico al estado previo a la operación (rollback total, sin escrituras
    parciales) y su esquema se preserva.

**Validates: Requirements 5.5**

Funciones bajo prueba: `persistir_portafolio` y `persistir_subyacente` de
`comun/validaciones_inputs.py`. Ambas envuelven el `INSERT` en una transacción
atómica (`with conn:`), con `rollback` explícito y re-raise ante excepción.

Estrategia de inyección del fallo
---------------------------------
Para verificar el rollback necesitamos que la escritura falle DESPUÉS de haber
comenzado (para que haya algo que revertir) pero DENTRO de la misma transacción.
Se usa una conexión sqlite3 envuelta (`_ConexionQueFalla`) cuyo `execute`:

  1. Ejecuta realmente el `INSERT` sobre la conexión real (escritura parcial
     dentro de la transacción abierta por `with conn:`), y luego
  2. Lanza una excepción,

de modo que el context manager de la transacción deba revertir esa escritura
parcial. Si el rollback funciona, la fila insertada desaparece y el estado
final es idéntico al inicial.

Se compara el estado completo de la tabla (todas las filas) y su esquema
(`PRAGMA table_info`) antes y después de la operación fallida.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegura que el directorio `tests/` (donde vive conftest.py) y la raíz del
# proyecto estén importables, con independencia del modo de importación de
# pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comun.validaciones_inputs import (  # noqa: E402
    persistir_portafolio,
    persistir_subyacente,
)

# Reutiliza las estrategias de ids de conftest.
from conftest import ids_strategy  # noqa: E402

# Estrategia barata para IDENTIFICADORES DE SEMILLA (filas preexistentes). Su
# contenido no es relevante para la propiedad (Property 7 trata del rollback, no
# del contenido de los ids), y `ids_strategy` (unicode adversario) es costosa de
# generar en volumen. Se usa texto ASCII simple para acelerar la corrida sin
# debilitar la propiedad; el VALOR que realmente se intenta escribir (payload)
# sí usa `ids_strategy` adversaria.
_ID_SEMILLA = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=8,
)


def _nueva_db_param() -> sqlite3.Connection:
    """
    Crea una DB SQLite en memoria con las tablas `param_*` (mismo esquema que la
    fábrica de conftest). Se usa memoria en vez de un archivo temporal para
    evitar el costoso churn de ficheros sobre la ruta de Google Drive por cada
    ejemplo de Hypothesis; el esquema es idéntico al del proyecto.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE param_portafolios "
        "(portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"
    )
    conn.execute(
        "CREATE TABLE param_diccionario_swaps (id_posicion TEXT, Subyacente TEXT)"
    )
    conn.commit()
    return conn

# Contadores de casos de prueba superados. Se escriben a un archivo sentinela en
# `teardown_module` (que corre tras todas las pruebas del módulo pero antes del
# apagado del intérprete), para poder verificar el resultado de forma fiable en
# este entorno (Python 3.14 en Windows presenta un crash nativo al finalizar el
# proceso que impide leer el código de salida del shell).
_CONTADOR = {"portafolio": 0, "subyacente": 0}


def _escribir_sentinela(nombre: str) -> None:
    """Escribe/actualiza un sentinela de finalización de una prueba concreta."""
    ruta = os.path.join(_ESTE_DIR, f"_atom_{nombre}_done.txt")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(f"{nombre}_pasos={_CONTADOR[nombre]}\nCOMPLETADO=OK\n")


def teardown_module(module):  # noqa: ARG001
    """Escribe un sentinela con los contadores para verificación externa."""
    ruta = os.path.join(_ESTE_DIR, "_atom_sentinel.txt")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(
            f"portafolio_pasos={_CONTADOR['portafolio']}\n"
            f"subyacente_pasos={_CONTADOR['subyacente']}\n"
            "MODULO_COMPLETADO=OK\n"
        )


# Mínimo de ejemplos por propiedad. Se usa 25 para corridas de verificación.
_AJUSTES = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


class _FalloInyectado(Exception):
    """Excepción sintética inyectada para forzar el fallo de la escritura."""


class _ConexionQueFalla:
    """
    Envoltura de una conexión sqlite3 que hace fallar el primer `execute`
    DESPUÉS de aplicarlo realmente sobre la conexión subyacente.

    Delega todo lo demás (incluidos `__enter__`/`__exit__`, `commit`, `rollback`,
    `execute`, `close`) en la conexión real, de modo que el context manager
    `with conn:` de sqlite3 se comporte con normalidad: al propagarse la
    excepción, sqlite3 revierte la transacción (rollback), eliminando la
    escritura parcial.
    """

    def __init__(self, conn_real: sqlite3.Connection):
        self._conn = conn_real
        self._debe_fallar = True

    def execute(self, sql, parametros=()):
        # Aplica realmente la escritura parcial dentro de la transacción abierta
        # por `__enter__`, y luego lanza para forzar el rollback.
        resultado = self._conn.execute(sql, parametros)
        if self._debe_fallar:
            self._debe_fallar = False
            raise _FalloInyectado("fallo inyectado tras iniciar la escritura")
        return resultado

    def __enter__(self):
        # sqlite3.Connection.__enter__ devuelve la propia conexión; aquí abrimos
        # la transacción sobre la conexión real y devolvemos ESTE wrapper para
        # que el `execute` intermedio pase por la lógica de fallo.
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        # Delega el commit/rollback a la conexión real según haya o no excepción.
        return self._conn.__exit__(exc_type, exc, tb)

    def __getattr__(self, nombre):
        # Cualquier otro atributo/método (rollback, commit, cursor, ...) se
        # delega en la conexión real.
        return getattr(self._conn, nombre)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para capturar estado y esquema de una tabla
# ─────────────────────────────────────────────────────────────────────────────
def _snapshot_esquema(conn: sqlite3.Connection, tabla: str) -> list:
    """Devuelve el esquema de la tabla (PRAGMA table_info) como lista de tuplas."""
    return list(conn.execute(f"PRAGMA table_info({tabla})").fetchall())


def _snapshot_filas(conn: sqlite3.Connection, tabla: str) -> list:
    """
    Devuelve todas las filas de la tabla ordenadas de forma determinista, para
    comparar el estado completo antes/después con independencia del orden físico.
    """
    filas = conn.execute(f"SELECT * FROM {tabla}").fetchall()
    return sorted(filas, key=lambda r: tuple("" if v is None else str(v) for v in r))


def _sembrar_portafolios(conn: sqlite3.Connection, filas) -> None:
    conn.executemany(
        'INSERT INTO param_portafolios ("portafolio_porfin","ramo","tipo","excluir") '
        "VALUES (?,?,?,?)",
        filas,
    )
    conn.commit()


def _sembrar_subyacentes(conn: sqlite3.Connection, filas) -> None:
    conn.executemany(
        'INSERT INTO param_diccionario_swaps ("id_posicion","Subyacente") VALUES (?,?)',
        filas,
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 — persistir_portafolio
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    porfin=ids_strategy(),
    ramo=ids_strategy(),
    tipo=ids_strategy(),
    excluir=st.sampled_from(["SI", "NO"]),
    filas_previas=st.lists(
        st.tuples(_ID_SEMILLA, _ID_SEMILLA, _ID_SEMILLA, st.sampled_from(["SI", "NO"])),
        max_size=5,
    ),
)
def test_persistir_portafolio_rollback_total(porfin, ramo, tipo, excluir, filas_previas):
    """
    Un alta de portafolio que falla antes de completarse deja `param_portafolios`
    en un estado idéntico al previo (sin escrituras parciales) y con el esquema
    intacto. (Req 5.5)
    """
    conn = _nueva_db_param()
    try:
        _sembrar_portafolios(conn, filas_previas)

        esquema_antes = _snapshot_esquema(conn, "param_portafolios")
        filas_antes = _snapshot_filas(conn, "param_portafolios")

        payload = {
            "portafolio_porfin": porfin,
            "ramo": ramo,
            "tipo": tipo,
            "excluir": excluir,
        }

        # La escritura debe fallar (excepción inyectada) y relanzarse.
        conn_falla = _ConexionQueFalla(conn)
        fallo = False
        try:
            persistir_portafolio(conn_falla, payload)
        except _FalloInyectado:
            fallo = True

        # La operación efectivamente falló (precondición de la propiedad).
        assert fallo is True

        filas_despues = _snapshot_filas(conn, "param_portafolios")
        esquema_despues = _snapshot_esquema(conn, "param_portafolios")

        # Rollback total: estado y esquema idénticos a los previos.
        assert filas_despues == filas_antes
        assert esquema_despues == esquema_antes
        _CONTADOR["portafolio"] += 1
        _escribir_sentinela("portafolio")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 — persistir_subyacente
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    id_swap=ids_strategy(),
    subyacente=ids_strategy(),
    filas_previas=st.lists(
        st.tuples(_ID_SEMILLA, _ID_SEMILLA),
        max_size=5,
    ),
)
def test_persistir_subyacente_rollback_total(id_swap, subyacente, filas_previas):
    """
    Una asignación de subyacentes que falla antes de completarse deja
    `param_diccionario_swaps` en un estado idéntico al previo (sin escrituras
    parciales) y con el esquema intacto. (Req 5.5)
    """
    conn = _nueva_db_param()
    try:
        _sembrar_subyacentes(conn, filas_previas)

        esquema_antes = _snapshot_esquema(conn, "param_diccionario_swaps")
        filas_antes = _snapshot_filas(conn, "param_diccionario_swaps")

        payload = {"id_posicion": id_swap, "Subyacente": subyacente}

        conn_falla = _ConexionQueFalla(conn)
        fallo = False
        try:
            persistir_subyacente(conn_falla, payload)
        except _FalloInyectado:
            fallo = True

        assert fallo is True

        filas_despues = _snapshot_filas(conn, "param_diccionario_swaps")
        esquema_despues = _snapshot_esquema(conn, "param_diccionario_swaps")

        assert filas_despues == filas_antes
        assert esquema_despues == esquema_antes
        _CONTADOR["subyacente"] += 1
    finally:
        conn.close()
