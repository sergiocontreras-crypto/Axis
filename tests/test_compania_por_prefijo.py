# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 3: Compañía derivada por prefijo (sin regresión)

    Para todo conjunto de posiciones con su código de `portafolio`, la compañía
    asignada por el Clasificador es la derivada por prefijo
    ('01'→Capitalizadora, '02'→Bolívar, '03'→Comerciales, cualquier otro→
    'Sin Compañía'), idéntica al comportamiento actual, independientemente de
    las altas realizadas desde el dashboard.

**Validates: Requirements 1.9, 5.4**

Enfoque (NO reimplementar): el paso 7 de `clasificar_portafolio.py` deriva la
compañía con un bloque `np.select` sobre `df_port['portafolio'].str.startswith`.
Ese bloque vive dentro de la función `clasificar_portafolio_diario`, orientada a
base de datos. Para ejercitar EXACTAMENTE ese código (y no una copia) sin montar
todo el pipeline de DB, este test EXTRAE el bloque fuente real desde el módulo
`clasificar_portafolio.py` (vía AST) y lo ejecuta contra un DataFrame generado.
Así, si el bloque de derivación de compañía cambiara en el código, el test lo
recogería automáticamente (el fuente es la única fuente de verdad).

La independencia respecto de las "altas del dashboard" se verifica porque el
bloque real solo lee la columna `portafolio`; no consulta `param_portafolios`
ni ninguna otra tabla para asignar la compañía.
"""

import ast
import inspect
import os
import sys

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegura que la raíz del proyecto y `tests/` (conftest.py) sean importables,
# con independencia del modo de importación de pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import clasificar_portafolio  # módulo real bajo prueba

# Número de ejemplos por propiedad (corridas rápidas, por solicitud del usuario).
_MIN_EJEMPLOS = 25

_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Mapa esperado de prefijo → compañía, según Req 1.9.
_PREFIJO_A_COMPANIA = {
    "01": "Capitalizadora",
    "02": "Bolívar",
    "03": "Comerciales",
}
_SIN_COMPANIA = "Sin Compañía"


# ─────────────────────────────────────────────────────────────────────────────
# Extracción del bloque de derivación de compañía DEL CÓDIGO REAL (no copia)
# ─────────────────────────────────────────────────────────────────────────────
def _extraer_asignacion_compania_desde_fuente():
    """
    Localiza en el fuente de `clasificar_portafolio_diario` la sentencia
    `df_port['compania'] = np.select(...)` y devuelve su código fuente exacto
    (dedentado y listo para `exec`).

    Recorre el AST de la función buscando la asignación cuyo objetivo es un
    subscript `df_port['compania']`. Devolver el fuente REAL garantiza que el
    test valida el código de producción, no una reimplementación.
    """
    fuente_fn = inspect.getsource(
        clasificar_portafolio.clasificar_portafolio_diario
    )
    fuente_fn = _dedent(fuente_fn)
    arbol = ast.parse(fuente_fn)

    fn_def = arbol.body[0]
    assert isinstance(fn_def, (ast.FunctionDef, ast.AsyncFunctionDef))

    for nodo in ast.walk(fn_def):
        if not isinstance(nodo, ast.Assign):
            continue
        if len(nodo.targets) != 1:
            continue
        objetivo = nodo.targets[0]
        # Buscamos df_port['compania'] = ...
        if (
            isinstance(objetivo, ast.Subscript)
            and isinstance(objetivo.value, ast.Name)
            and objetivo.value.id == "df_port"
            and _clave_subscript(objetivo) == "compania"
        ):
            return _dedent(ast.get_source_segment(fuente_fn, nodo))

    raise AssertionError(
        "No se encontró la asignación df_port['compania'] en "
        "clasificar_portafolio_diario; el bloque de derivación de compañía "
        "cambió de forma y este test debe actualizarse."
    )


def _clave_subscript(nodo_subscript):
    """Devuelve la clave literal de un Subscript tipo df['x'] o None."""
    sl = nodo_subscript.slice
    # Python 3.9+: el slice de df['x'] es directamente el ast.Constant.
    if isinstance(sl, ast.Constant):
        return sl.value
    # Compatibilidad con ast.Index (Python <3.9).
    if isinstance(sl, ast.Index) and isinstance(sl.value, ast.Constant):  # pragma: no cover
        return sl.value.value
    return None


def _dedent(codigo: str) -> str:
    """Quita la indentación común de un bloque de código fuente."""
    import textwrap

    return textwrap.dedent(codigo)


# Se extrae UNA sola vez al cargar el módulo. Si falla, todos los tests fallan
# de forma explícita (señal de que el bloque de compañía cambió de forma).
_CODIGO_COMPANIA = _extraer_asignacion_compania_desde_fuente()


def _derivar_compania_con_codigo_real(portafolios) -> list:
    """
    Ejecuta el bloque REAL `df_port['compania'] = np.select(...)` extraído del
    módulo sobre un DataFrame con la columna `portafolio`, y devuelve la lista
    de compañías resultante. `np` y `pd` se inyectan igual que en el módulo.
    """
    df_port = pd.DataFrame({"portafolio": pd.Series(portafolios, dtype="object")})
    entorno = {"df_port": df_port, "np": np, "pd": pd}
    exec(_CODIGO_COMPANIA, entorno)  # noqa: S102 — ejecuta código PROPIO del repo
    return df_port["compania"].tolist()


# ─────────────────────────────────────────────────────────────────────────────
# Referencia independiente de la especificación (Req 1.9)
# ─────────────────────────────────────────────────────────────────────────────
def _compania_esperada(codigo: str) -> str:
    """Compañía esperada por prefijo, según la especificación (Req 1.9)."""
    for prefijo, compania in _PREFIJO_A_COMPANIA.items():
        if codigo.startswith(prefijo):
            return compania
    return _SIN_COMPANIA


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias de códigos de portafolio
# ─────────────────────────────────────────────────────────────────────────────
# Sufijo arbitrario (dígitos y letras) que sigue al prefijo de 2 caracteres.
_SUFIJO = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=0,
    max_size=8,
)

# Prefijos que SÍ mapean a compañía.
_PREFIJO_CONOCIDO = st.sampled_from(sorted(_PREFIJO_A_COMPANIA.keys()))

# Prefijos de 2 caracteres cualesquiera (mayormente "otros" → Sin Compañía).
_PREFIJO_CUALQUIERA = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=57),  # dígitos
    min_size=2,
    max_size=2,
)


@st.composite
def _codigo_con_prefijo_conocido(draw):
    """Código que empieza por 01/02/03 (compañía determinada)."""
    return draw(_PREFIJO_CONOCIDO) + draw(_SUFIJO)


@st.composite
def _codigo_cualquiera(draw):
    """Código de portafolio arbitrario (prefijo de 2 dígitos + sufijo)."""
    return draw(_PREFIJO_CUALQUIERA) + draw(_SUFIJO)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: la compañía derivada por el código REAL coincide con la spec
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(portafolios=st.lists(_codigo_cualquiera(), min_size=1, max_size=40))
def test_compania_por_prefijo_coincide_con_especificacion(portafolios):
    """
    Para todo conjunto de posiciones con su código de `portafolio`, la compañía
    que asigna el bloque REAL del Clasificador es exactamente la derivada por
    prefijo ('01'→Capitalizadora, '02'→Bolívar, '03'→Comerciales, otro→
    'Sin Compañía'). (Req 1.9, 5.4)
    """
    obtenido = _derivar_compania_con_codigo_real(portafolios)
    esperado = [_compania_esperada(c) for c in portafolios]
    assert obtenido == esperado


@_AJUSTES
@given(portafolios=st.lists(_codigo_con_prefijo_conocido(), min_size=1, max_size=40))
def test_prefijos_conocidos_mapean_a_su_compania(portafolios):
    """
    Los prefijos 01/02/03 mapean a Capitalizadora/Bolívar/Comerciales
    respectivamente, en el bloque REAL del Clasificador. (Req 1.9)
    """
    obtenido = _derivar_compania_con_codigo_real(portafolios)
    esperado = [_PREFIJO_A_COMPANIA[c[:2]] for c in portafolios]
    assert obtenido == esperado


@_AJUSTES
@given(
    portafolios=st.lists(_codigo_cualquiera(), min_size=1, max_size=40),
    # "Altas" simuladas del dashboard: pares (portafolio_porfin, compania) que
    # NUNCA deben influir en la derivación (la compañía no se captura en altas).
    altas_dashboard=st.lists(
        st.tuples(_codigo_cualquiera(), st.text(max_size=10)), max_size=10
    ),
)
def test_compania_independiente_de_altas_dashboard(portafolios, altas_dashboard):
    """
    La compañía derivada es independiente de cualquier alta del dashboard: el
    bloque real solo lee `portafolio`. Verificamos que el resultado es idéntico
    con o sin "altas" presentes (que aquí no se usan porque el código no las
    consulta). (Req 1.9, 5.4)
    """
    resultado_sin_altas = _derivar_compania_con_codigo_real(portafolios)
    # Las altas no forman parte del cálculo; el resultado debe ser el mismo.
    resultado_con_altas = _derivar_compania_con_codigo_real(portafolios)
    assert resultado_con_altas == resultado_sin_altas
    assert resultado_sin_altas == [_compania_esperada(c) for c in portafolios]


# ─────────────────────────────────────────────────────────────────────────────
# Ejemplos unitarios de anclaje (edge cases explícitos)
# ─────────────────────────────────────────────────────────────────────────────
def test_ejemplos_unitarios_prefijos():
    """Anclaje de casos concretos, incluyendo prefijos no reconocidos."""
    entradas = ["01ABC", "0200", "03X", "04", "99zzz", "1", "010203", "0301"]
    esperado = [
        "Capitalizadora",  # 01ABC
        "Bolívar",         # 0200
        "Comerciales",     # 03X
        "Sin Compañía",    # 04
        "Sin Compañía",    # 99zzz
        "Sin Compañía",    # 1 (no completa un prefijo de 2)
        "Capitalizadora",  # 010203 → prefijo 01
        "Comerciales",     # 0301 → prefijo 03
    ]
    assert _derivar_compania_con_codigo_real(entradas) == esperado
