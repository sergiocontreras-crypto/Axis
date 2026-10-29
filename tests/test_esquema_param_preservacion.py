# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 5: Preservación de esquema de tablas param_*

    Para toda secuencia de altas de portafolios o de asignaciones de
    subyacentes, el conjunto de columnas, su orden y su tipo declarado en
    `param_portafolios` y en `param_diccionario_swaps` permanecen exactamente
    iguales a los previos a la operación.

Valida las funciones `persistir_portafolio` y `persistir_subyacente` de
`comun/validaciones_inputs.py` (tarea 3.1): al insertar filas mediante
sentencias `INSERT` parametrizadas (sin DDL), el esquema declarado de ambas
tablas — capturado con `PRAGMA table_info` — no cambia tras cualquier secuencia
de altas/asignaciones.

**Validates: Requirements 1.10, 2.9, 5.1**
"""

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

# Reutiliza las estrategias, constantes de esquema y fábrica de DB definidas en
# conftest.py.
from conftest import (
    COLUMNAS_PARAM_DICCIONARIO_SWAPS,
    COLUMNAS_PARAM_PORTAFOLIOS,
    crear_db_param_temporal,
    ids_strategy,
    lista_ids_strategy,
)

# Número de ejemplos por propiedad. Reducido para corridas rápidas de
# verificación (por solicitud del usuario); el diseño exige >= 100 para la
# validación formal completa.
_MIN_EJEMPLOS = 25

# Ajustes de Hypothesis reutilizados por todas las propiedades de este módulo.
# `ids_strategy` genera texto adversario (símbolos, espacios, rango unicode
# amplio) para ejercitar las escrituras parametrizadas; su generación puede ser
# lenta, por lo que se desactiva el `deadline` y se suprimen los health checks
# `too_slow`/`data_too_large`. Esto NO relaja la propiedad: solo evita que la
# lentitud del *generador* invalide la corrida.
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: snapshot del esquema declarado de una tabla vía PRAGMA table_info
# ─────────────────────────────────────────────────────────────────────────────
def _snapshot_esquema(conn, tabla: str) -> list:
    """
    Devuelve el esquema declarado de `tabla` como una lista de tuplas
    (cid, nombre, tipo, notnull, dflt_value, pk), en el ORDEN de columnas que
    reporta `PRAGMA table_info`.

    Captura conjunto, orden y tipo declarado de cada columna, que son
    exactamente los tres invariantes de la Property 5.
    """
    filas = conn.execute(f'PRAGMA table_info("{tabla}")').fetchall()
    # Cada fila: (cid, name, type, notnull, dflt_value, pk). Se conserva completa
    # para detectar cualquier alteración de orden, nombre o tipo declarado.
    return [tuple(f) for f in filas]


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias de operaciones (payloads válidos ya normalizados)
#
# Property 5 habla de "toda secuencia de altas/asignaciones". Modelamos cada
# operación como un payload listo para persistir (el que producirían los
# validadores puros). Los valores usan `ids_strategy` para incluir contenido
# adversario (comillas, ';', '--', unicode) y así ejercitar la robustez de la
# escritura parametrizada sin afectar el esquema.
# ─────────────────────────────────────────────────────────────────────────────
@st.composite
def _op_portafolio(draw):
    """Un alta de portafolio: ('portafolio', payload) con las 4 columnas."""
    return (
        "portafolio",
        {
            "portafolio_porfin": draw(ids_strategy()),
            "ramo": draw(ids_strategy()),
            "tipo": draw(ids_strategy()),
            "excluir": draw(st.sampled_from(["SI", "NO"])),
        },
    )


@st.composite
def _op_subyacente(draw):
    """Una asignación de subyacentes: ('subyacente', payload) con 2 columnas."""
    subyacentes = draw(lista_ids_strategy(min_ids=1, max_ids=8))
    return (
        "subyacente",
        {
            "id_posicion": draw(ids_strategy()),
            "Subyacente": "|".join(subyacentes),
        },
    )


def _secuencia_ops_strategy(max_ops: int = 12):
    """Secuencia arbitraria de altas de portafolio y asignaciones de subyacente."""
    return st.lists(
        st.one_of(_op_portafolio(), _op_subyacente()),
        max_size=max_ops,
    )


def _aplicar_op(conn, op):
    """Ejecuta una operación (alta o asignación) sobre la conexión dada."""
    tipo, payload = op
    if tipo == "portafolio":
        persistir_portafolio(conn, payload)
    else:
        persistir_subyacente(conn, payload)


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: el esquema declarado no cambia tras cualquier secuencia de escrituras
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(operaciones=_secuencia_ops_strategy())
def test_esquema_param_se_preserva_tras_secuencia(operaciones):
    """
    Para toda secuencia de altas/asignaciones, el esquema declarado (columnas,
    orden y tipo) de `param_portafolios` y `param_diccionario_swaps` es idéntico
    antes y después de aplicar la secuencia completa. (Req 1.10, 2.9, 5.1)

    Se crea una DB temporal FRESCA por cada ejemplo generado (vía
    `crear_db_param_temporal`) en lugar de usar el fixture function-scoped
    `db_param_tmp`, porque Hypothesis no reinicia los fixtures entre ejemplos.
    """
    ruta = crear_db_param_temporal()
    conn = sqlite3.connect(str(ruta))
    try:
        # Esquema previo a cualquier escritura.
        esquema_pp_antes = _snapshot_esquema(conn, "param_portafolios")
        esquema_pds_antes = _snapshot_esquema(conn, "param_diccionario_swaps")

        # Aplica toda la secuencia de operaciones.
        for op in operaciones:
            _aplicar_op(conn, op)

        # Esquema tras la secuencia completa.
        esquema_pp_despues = _snapshot_esquema(conn, "param_portafolios")
        esquema_pds_despues = _snapshot_esquema(conn, "param_diccionario_swaps")

        # Invariante central de la Property 5: el esquema declarado (conjunto,
        # orden y tipo de columnas) permanece EXACTAMENTE igual.
        assert esquema_pp_despues == esquema_pp_antes
        assert esquema_pds_despues == esquema_pds_antes

        # Refuerzo explícito: nombres y orden coinciden con el allowlist del
        # proyecto (no se agregó ni eliminó ninguna columna).
        nombres_pp = [fila[1] for fila in esquema_pp_despues]
        nombres_pds = [fila[1] for fila in esquema_pds_despues]
        assert tuple(nombres_pp) == COLUMNAS_PARAM_PORTAFOLIOS
        assert tuple(nombres_pds) == COLUMNAS_PARAM_DICCIONARIO_SWAPS
    finally:
        conn.close()
        try:
            ruta.unlink()
        except OSError:
            pass


@_AJUSTES
@given(operaciones=_secuencia_ops_strategy())
def test_esquema_estable_operacion_a_operacion(operaciones):
    """
    Refuerzo incremental: el esquema declarado es invariante DESPUÉS DE CADA
    operación individual de la secuencia, no solo al final. Esto captura la
    intención "para toda secuencia" en cada punto intermedio. (Req 1.10, 2.9, 5.1)

    DB temporal fresca por ejemplo (ver nota en el test anterior).
    """
    ruta = crear_db_param_temporal()
    conn = sqlite3.connect(str(ruta))
    try:
        esquema_pp_ref = _snapshot_esquema(conn, "param_portafolios")
        esquema_pds_ref = _snapshot_esquema(conn, "param_diccionario_swaps")

        for op in operaciones:
            _aplicar_op(conn, op)
            assert _snapshot_esquema(conn, "param_portafolios") == esquema_pp_ref
            assert (
                _snapshot_esquema(conn, "param_diccionario_swaps") == esquema_pds_ref
            )
    finally:
        conn.close()
        try:
            ruta.unlink()
        except OSError:
            pass
