# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 6: Escritura parametrizada resistente a inyección.

    Para todo valor de entrada (incluyendo cadenas con comillas simples/dobles,
    `;`, `--` u otras secuencias SQL), la persistencia enlaza los valores como
    parámetros: tras la escritura el valor almacenado es la cadena literal de
    entrada (normalizada) y ninguna otra fila ni el esquema de la tabla resultan
    alterados.

Valida las funciones de persistencia `persistir_portafolio` y
`persistir_subyacente` de `comun/validaciones_inputs.py` (implementadas en la
tarea 3.1), que escriben con binding `?` para todos los valores y nombres de
columna desde un allowlist fijo.

La estrategia `ids_strategy` de `conftest.py` ya genera texto adversario que
incluye comillas, `;`, `--` y otros símbolos del rango ASCII imprimible, por lo
que se reutiliza para ejercitar activamente la resistencia a inyección. Además,
se inyectan explícitamente cargas útiles clásicas de inyección SQL como casos
sembrados (`example`-like) vía una estrategia mixta.

**Validates: Requirements 5.2**
"""

import contextlib
import os
import sqlite3
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegura que el directorio `tests/` (donde vive conftest.py) y la raíz del
# proyecto estén importables, con independencia del modo de importación de
# pytest. Esto permite `from conftest import ...` y `from comun...` de forma
# robusta al ejecutar este módulo aislado.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comun.validaciones_inputs import (
    persistir_portafolio,
    persistir_subyacente,
)

# Reutiliza las estrategias/constantes de esquema definidas en conftest.py.
from conftest import (
    COLUMNAS_PARAM_DICCIONARIO_SWAPS,
    COLUMNAS_PARAM_PORTAFOLIOS,
    ids_strategy,
)

# Número de ejemplos por propiedad (Hypothesis). Alineado con las demás
# propiedades de esta feature.
_MIN_EJEMPLOS = 25

# Ajustes de Hypothesis reutilizados por todas las propiedades de este módulo.
# `ids_strategy` genera texto adversario (símbolos, comillas, espacios, rango
# unicode amplio) cuya generación puede ser lenta; se desactiva `deadline` y se
# suprimen `too_slow`/`data_too_large`. Esto NO relaja la propiedad: solo evita
# que la lentitud del generador invalide la corrida.
#
# Nota de aislamiento: cada ejemplo de Hypothesis abre su PROPIA base de datos
# en memoria (`_db_param_memoria`) con el esquema `param_*` recién creado. NO se
# usa un fixture con estado compartido, porque Hypothesis reejecuta el cuerpo
# muchas veces dentro de una sola invocación del test (un fixture de scope
# `function` se reutilizaría entre ejemplos y las filas se acumularían).
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
    ],
)

# Cargas útiles clásicas de inyección SQL, sembradas explícitamente para
# garantizar que se ejercitan además de la generación aleatoria.
_PAYLOADS_INYECCION = (
    "'; DROP TABLE param_portafolios; --",
    "'; DROP TABLE param_diccionario_swaps; --",
    "' OR '1'='1",
    '" OR "1"="1',
    "1); DELETE FROM param_portafolios; --",
    "Robert'); DROP TABLE param_portafolios;--",
    "'--",
    "\\'; --",
    "%27%20OR%201=1",
    "admin'/*",
)


def _texto_adversario():
    """
    Estrategia de texto no vacío que mezcla:
      - los payloads de inyección sembrados (garantiza cobertura de casos clásicos),
      - texto adversario generado por `ids_strategy` (comillas/`;`/`--`/símbolos).
    """
    return st.one_of(
        st.sampled_from(_PAYLOADS_INYECCION),
        ids_strategy(min_size=1, max_size=40),
    )


@contextlib.contextmanager
def _db_param_memoria():
    """
    Crea una base de datos SQLite EN MEMORIA con las tablas `param_*` recién
    creadas, con el esquema exacto del proyecto (mismo que `conftest.py`).

    Se usa una DB en memoria por ejemplo de Hypothesis para garantizar
    aislamiento total y determinismo: cada ejemplo parte de un estado limpio,
    sin arrastrar filas de ejemplos previos. La conexión se cierra al salir.

    Yields:
        sqlite3.Connection: conexión a la DB en memoria lista para usar.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        conn.execute(
            "CREATE TABLE param_portafolios "
            "(portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"
        )
        conn.execute(
            "CREATE TABLE param_diccionario_swaps "
            "(id_posicion TEXT, Subyacente TEXT)"
        )
        conn.commit()
        yield conn
    finally:
        conn.close()


def _esquema(conn, tabla):
    """
    Devuelve el esquema declarado de `tabla` como lista de tuplas
    (nombre, tipo, orden) a partir de PRAGMA table_info, para comparar que no se
    altera tras las escrituras.
    """
    filas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
    return [(f[1], f[2], f[0]) for f in filas]


# ─────────────────────────────────────────────────────────────────────────────
# param_portafolios (persistir_portafolio) — Req 5.2
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    porfin=_texto_adversario(),
    ramo=_texto_adversario(),
    tipo=_texto_adversario(),
    excluir=st.sampled_from(["SI", "NO"]),
)
def test_persistir_portafolio_almacena_literal_sin_inyeccion(
    porfin, ramo, tipo, excluir
):
    """
    Persistir un alta con valores adversarios (incl. inyección SQL) almacena el
    literal EXACTO de entrada en cada columna, sin ejecutar las secuencias SQL
    embebidas y sin alterar otras filas ni el esquema (Req 5.2).
    """
    with _db_param_memoria() as conn:
        # Fila "centinela" preexistente que NO debe verse afectada por la
        # escritura ni por ninguna inyección embebida en los valores.
        centinela = ("PF-CENTINELA", "RAMO-C", "TIPO-C", "NO")
        conn.execute(
            "INSERT INTO param_portafolios "
            "(portafolio_porfin, ramo, tipo, excluir) VALUES (?, ?, ?, ?)",
            centinela,
        )
        conn.commit()

        esquema_previo = _esquema(conn, "param_portafolios")

        payload = {
            "portafolio_porfin": porfin,
            "ramo": ramo,
            "tipo": tipo,
            "excluir": excluir,
        }
        persistir_portafolio(conn, payload)

        # 1) El valor almacenado es el literal de entrada, byte a byte.
        filas = conn.execute(
            "SELECT portafolio_porfin, ramo, tipo, excluir "
            "FROM param_portafolios WHERE portafolio_porfin = ?",
            (porfin,),
        ).fetchall()
        assert (porfin, ramo, tipo, excluir) in filas

        # 2) La tabla sigue existiendo (ningún DROP/DELETE se ejecutó) y contiene
        #    exactamente 2 filas: la centinela + la recién insertada. (Si el
        #    porfin adversario coincidiera con la clave centinela habría 2 filas
        #    con esa clave, pero el total sigue siendo 2.)
        total = conn.execute(
            "SELECT COUNT(*) FROM param_portafolios"
        ).fetchone()[0]
        assert total == 2

        # 3) La fila centinela permanece intacta (ninguna otra fila alterada).
        fila_c = conn.execute(
            "SELECT portafolio_porfin, ramo, tipo, excluir "
            "FROM param_portafolios WHERE portafolio_porfin = ?",
            ("PF-CENTINELA",),
        ).fetchall()
        assert centinela in fila_c

        # 4) El esquema (columnas, tipos y orden) no cambió.
        assert _esquema(conn, "param_portafolios") == esquema_previo
        assert [c[0] for c in _esquema(conn, "param_portafolios")] == list(
            COLUMNAS_PARAM_PORTAFOLIOS
        )


# ─────────────────────────────────────────────────────────────────────────────
# param_diccionario_swaps (persistir_subyacente) — Req 5.2
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    id_swap=_texto_adversario(),
    subyacente=_texto_adversario(),
)
def test_persistir_subyacente_almacena_literal_sin_inyeccion(id_swap, subyacente):
    """
    Persistir una asignación de subyacentes con valores adversarios (incl.
    inyección SQL) almacena el literal EXACTO de entrada, sin ejecutar SQL
    embebido y sin alterar otras filas ni el esquema (Req 5.2).
    """
    with _db_param_memoria() as conn:
        centinela = ("SWAP-CENTINELA", "SUB-A|SUB-B")
        conn.execute(
            "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) "
            "VALUES (?, ?)",
            centinela,
        )
        conn.commit()

        esquema_previo = _esquema(conn, "param_diccionario_swaps")

        payload = {"id_posicion": id_swap, "Subyacente": subyacente}
        persistir_subyacente(conn, payload)

        # 1) Literal almacenado == entrada.
        filas = conn.execute(
            "SELECT id_posicion, Subyacente FROM param_diccionario_swaps "
            "WHERE id_posicion = ?",
            (id_swap,),
        ).fetchall()
        assert (id_swap, subyacente) in filas

        # 2) La tabla sigue viva y con 2 filas exactas.
        total = conn.execute(
            "SELECT COUNT(*) FROM param_diccionario_swaps"
        ).fetchone()[0]
        assert total == 2

        # 3) La centinela permanece intacta.
        fila_c = conn.execute(
            "SELECT id_posicion, Subyacente FROM param_diccionario_swaps "
            "WHERE id_posicion = ?",
            ("SWAP-CENTINELA",),
        ).fetchall()
        assert centinela in fila_c

        # 4) Esquema inalterado.
        assert _esquema(conn, "param_diccionario_swaps") == esquema_previo
        assert [c[0] for c in _esquema(conn, "param_diccionario_swaps")] == list(
            COLUMNAS_PARAM_DICCIONARIO_SWAPS
        )


# ─────────────────────────────────────────────────────────────────────────────
# Robustez explícita: cargas de inyección clásicas no ejecutan SQL destructivo
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(payload_iny=st.sampled_from(_PAYLOADS_INYECCION))
def test_payloads_inyeccion_no_ejecutan_sql_destructivo(payload_iny):
    """
    Un payload de inyección SQL clásico enlazado como parámetro se almacena como
    dato literal y NO ejecuta las sentencias embebidas: las tablas siguen
    existiendo y las filas preexistentes se conservan (Req 5.2).
    """
    with _db_param_memoria() as conn:
        conn.execute(
            "INSERT INTO param_portafolios "
            "(portafolio_porfin, ramo, tipo, excluir) VALUES (?, ?, ?, ?)",
            ("PF-BASE", "R", "T", "NO"),
        )
        conn.execute(
            "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) "
            "VALUES (?, ?)",
            ("SW-BASE", "SUB"),
        )
        conn.commit()

        persistir_portafolio(
            conn,
            {
                "portafolio_porfin": payload_iny,
                "ramo": payload_iny,
                "tipo": payload_iny,
                "excluir": "SI",
            },
        )
        persistir_subyacente(
            conn,
            {"id_posicion": payload_iny, "Subyacente": payload_iny},
        )

        # Ambas tablas siguen existiendo (si un DROP se hubiera ejecutado, estas
        # consultas lanzarían OperationalError).
        n_pf = conn.execute("SELECT COUNT(*) FROM param_portafolios").fetchone()[0]
        n_sw = conn.execute(
            "SELECT COUNT(*) FROM param_diccionario_swaps"
        ).fetchone()[0]

        # Base + fila insertada = 2 en cada tabla; nada fue borrado.
        assert n_pf == 2
        assert n_sw == 2

        # El literal de inyección quedó almacenado como dato.
        almacenado_pf = conn.execute(
            "SELECT ramo FROM param_portafolios WHERE portafolio_porfin = ?",
            (payload_iny,),
        ).fetchone()
        assert almacenado_pf == (payload_iny,)

        almacenado_sw = conn.execute(
            "SELECT Subyacente FROM param_diccionario_swaps WHERE id_posicion = ?",
            (payload_iny,),
        ).fetchone()
        assert almacenado_sw == (payload_iny,)
