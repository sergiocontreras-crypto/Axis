# -*- coding: utf-8 -*-
"""
Property-based test (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 4: No-regresión de la clasificación (ramo/tipo e id_cobertura).

    Para todo `portafolio_clasificado`, `param_portafolios` y
    `param_diccionario_swaps`: toda posición cuyo `portafolio` coincide con un
    `portafolio_porfin` recibe su `ramo` y `tipo`; y cada swap y sus
    subyacentes reciben el `id_cobertura` acorde al diccionario, produciendo las
    mismas asignaciones de encaje/id_cobertura que el comportamiento previo con
    los mismos insumos.

**Validates: Requirements 1.8, 2.8, 5.4**

Enfoque de la prueba (código real como fuente de verdad)
-------------------------------------------------------
El Clasificador (`clasificar_portafolio.py`) no expone el paso 7
(encaje/ramo/tipo) ni el paso 8.5 (id_cobertura) como funciones aisladas: ambos
viven embebidos dentro del orquestador `clasificar_portafolio_diario`, que exige
un esquema completo de DB. Para ejercitar los **mismos caminos de código
reales** sin re-ejecutar todo el pipeline, este test EXTRAE del fuente real, vía
AST, exactamente las sentencias de producción que:

  - hacen el `merge` con `param_portafolios` y calculan `encaje` (paso 7), y
  - mapean `id_cobertura` para swaps y títulos desde `param_diccionario_swaps`
    (paso 8.5),

y las ejecuta contra insumos sembrados en una DB SQLite TEMPORAL (nunca la
`portafolio.db` real). El resultado se contrasta contra una implementación de
referencia independiente derivada de las reglas de negocio (Req 1.8, 2.8, 5.4).
Como el fuente es la única fuente de verdad, si el bloque de producción cambiara
de forma, la extracción fallaría explícitamente y el test avisaría.
"""

import ast
import inspect
import os
import sqlite3
import sys
import tempfile
import textwrap

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Rutas importables (raíz del proyecto y tests/), robusto al modo de import.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import clasificar_portafolio  # módulo real bajo prueba

# Número de ejemplos por propiedad (corridas rápidas, por solicitud). El diseño
# exige >= 100 para la validación formal completa.
_MIN_EJEMPLOS = 25

_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Estados/tipos/ramos que fuerzan encaje 'NO' (según reglas del paso 7).
_ESTADOS_NO_ENCAJAN = ["RG", "GA"]
_TIPOS_NO_ENCAJAN = [
    "EMBARGO",
    "COLATERAL",
    "titulo_simultanea_activa",
    "simultanea_activa",
    "simultanea_pasiva",
    "AJUSTE LIQUIDEZ",
]
_RAMOS_NO_ENCAJAN = [
    "BOLIVAR EXCESO",
    "COMERCIALES EXCESO",
    "CAPITALIZADORA EXCESO",
]


# ─────────────────────────────────────────────────────────────────────────────
# Extracción del CÓDIGO REAL (no copia) desde clasificar_portafolio_diario
# ─────────────────────────────────────────────────────────────────────────────
def _dedent(codigo: str) -> str:
    return textwrap.dedent(codigo)


def _clave_subscript(nodo_subscript):
    """Devuelve la clave literal de un Subscript tipo df['x'] o None."""
    sl = nodo_subscript.slice
    if isinstance(sl, ast.Constant):  # Python 3.9+
        return sl.value
    if isinstance(sl, ast.Index) and isinstance(sl.value, ast.Constant):  # pragma: no cover
        return sl.value.value
    return None


def _es_asignacion_a(nodo, nombre_df, clave):
    """True si `nodo` es `nombre_df['clave'] = ...`."""
    if not isinstance(nodo, ast.Assign) or len(nodo.targets) != 1:
        return False
    obj = nodo.targets[0]
    return (
        isinstance(obj, ast.Subscript)
        and isinstance(obj.value, ast.Name)
        and obj.value.id == nombre_df
        and _clave_subscript(obj) == clave
    )


def _fuente_diario():
    fuente = inspect.getsource(clasificar_portafolio.clasificar_portafolio_diario)
    return _dedent(fuente)


def _cuerpo_top_level():
    """Devuelve (fuente_dedentada, lista de sentencias top-level de la función)."""
    fuente = _fuente_diario()
    fn_def = ast.parse(fuente).body[0]
    assert isinstance(fn_def, (ast.FunctionDef, ast.AsyncFunctionDef))
    return fuente, list(ast.walk(fn_def)), fn_def


def _sentencias_efectivas(fn_def):
    """
    Devuelve las sentencias del cuerpo "efectivo" de la función.

    El Clasificador envuelve todo su cuerpo en un `try:` de nivel superior
    (para cerrar la conexión en `finally`), por lo que las asignaciones a
    `df_port[...]` viven dentro de ese `try`, no directamente en `fn_def.body`.
    Este helper baja un nivel al cuerpo del primer `try` de nivel superior
    cuando existe, de modo que la extracción encuentre las sentencias reales
    con independencia de ese envoltorio.
    """
    cuerpo = list(fn_def.body)
    for nodo in cuerpo:
        if isinstance(nodo, ast.Try):
            # El cuerpo real de la función está dentro de este try principal.
            return list(nodo.body)
    return cuerpo


def _extraer_bloque_encaje():
    """
    Extrae del fuente real las sentencias del PASO 7 necesarias para calcular
    `encaje` a partir del merge con `param_portafolios`:

      df_portafolios = pd.read_sql_query("SELECT portafolio_porfin, ramo, tipo ...")
      df_port = df_port.merge(df_portafolios, ...)
      tipos_no_encajan = [...]
      ramos_no_encajan = [...]
      df_port['encaje'] = np.where(...)

    Devuelve una cadena de código lista para `exec`. Se identifican por su forma
    (merge de param_portafolios + asignación de 'encaje'), no por comentarios.
    """
    fuente, _, fn_def = _cuerpo_top_level()
    sentencias = _sentencias_efectivas(fn_def)

    segmentos = []
    capturando = False
    for nodo in sentencias:
        seg = ast.get_source_segment(fuente, nodo)
        if seg is None:
            continue

        # Inicio: lectura de param_portafolios (paso 7).
        if not capturando and "param_portafolios" in seg and "read_sql_query" in seg:
            capturando = True

        if capturando:
            # No arrastrar la eliminación de columnas (conservamos 'ramo').
            if _es_asignacion_a(nodo, "df_port", "compania"):
                break
            # Ignorar el drop de columnas para preservar 'ramo' y 'tipo' que
            # necesitamos para verificar la propiedad.
            es_drop = (
                isinstance(nodo, ast.Assign)
                and "df_port.drop" in seg
                and "portafolio_porfin" in seg
            )
            if not es_drop:
                segmentos.append(seg)

            # Fin: tras asignar 'encaje' ya tenemos lo necesario.
            if _es_asignacion_a(nodo, "df_port", "encaje"):
                break

    if not segmentos:
        raise AssertionError(
            "No se encontró el bloque de encaje (paso 7) en "
            "clasificar_portafolio_diario; su forma cambió y este test debe "
            "actualizarse."
        )
    return "\n".join(_dedent(s) for s in segmentos)


def _extraer_bloque_id_cobertura():
    """
    Extrae del fuente real las sentencias del PASO 8.5 que asignan `id_cobertura`
    a swaps y títulos desde `param_diccionario_swaps`, hasta (sin incluir) la
    herencia de `clasificacion_contable`, que no es objeto de esta propiedad.

    Se toma el bloque `try:` cuyo cuerpo lee `param_diccionario_swaps`, se le
    quita el manejo de excepción (para que los errores afloren en el test) y se
    truncan las sentencias a partir de la que menciona `clasificacion_contable`.
    """
    fuente, _, fn_def = _cuerpo_top_level()

    # 1) Localizar la asignación df_port['id_cobertura'] = None (inicio real).
    idx_init = None
    cuerpo = _sentencias_efectivas(fn_def)
    for i, nodo in enumerate(cuerpo):
        if _es_asignacion_a(nodo, "df_port", "id_cobertura"):
            seg = ast.get_source_segment(fuente, nodo)
            if seg is not None and "None" in seg:
                idx_init = i
                break
    if idx_init is None:
        raise AssertionError(
            "No se encontró la inicialización df_port['id_cobertura'] = None."
        )

    segmentos = [_dedent(ast.get_source_segment(fuente, cuerpo[idx_init]))]

    # 2) El siguiente nodo debe ser el try/except del paso 8.5.
    nodo_try = None
    for nodo in cuerpo[idx_init + 1 :]:
        if isinstance(nodo, ast.Try):
            seg_try = ast.get_source_segment(fuente, nodo)
            if seg_try and "param_diccionario_swaps" in seg_try:
                nodo_try = nodo
                break
    if nodo_try is None:
        raise AssertionError(
            "No se encontró el try/except del paso 8.5 (id_cobertura)."
        )

    # 3) Tomar las sentencias del cuerpo del try, truncando al llegar a la
    #    herencia de clasificacion_contable.
    for nodo in nodo_try.body:
        seg = ast.get_source_segment(fuente, nodo)
        if seg is None:
            continue
        if "clasificacion_contable" in seg:
            break
        segmentos.append(_dedent(seg))

    return "\n".join(segmentos)


# Se extraen una sola vez al cargar el módulo. Si la extracción falla, todos los
# tests fallan de forma explícita (señal de que el bloque cambió de forma).
_CODIGO_ENCAJE = _extraer_bloque_encaje()
_CODIGO_ID_COBERTURA = _extraer_bloque_id_cobertura()


def _ejecutar_pasos_reales(df_port: pd.DataFrame, conn) -> pd.DataFrame:
    """
    Ejecuta los bloques REALES extraídos (paso 7 encaje + paso 8.5 id_cobertura)
    sobre `df_port`, inyectando `conn`, `np` y `pd` igual que el módulo real.
    """
    entorno = {"df_port": df_port.copy(), "conn": conn, "np": np, "pd": pd}
    exec(_CODIGO_ENCAJE, entorno)  # noqa: S102 — ejecuta código PROPIO del repo
    exec(_CODIGO_ID_COBERTURA, entorno)  # noqa: S102
    return entorno["df_port"]


# ─────────────────────────────────────────────────────────────────────────────
# IMPLEMENTACIÓN DE REFERENCIA INDEPENDIENTE (reglas de negocio)
# ─────────────────────────────────────────────────────────────────────────────
def _ref_ramo_tipo_encaje(fila, mapa_portafolios):
    """Referencia independiente de la asignación ramo/tipo/encaje (paso 7)."""
    ramo = tipo = None
    if fila["portafolio"] in mapa_portafolios:
        ramo, tipo = mapa_portafolios[fila["portafolio"]]
    encaje = (
        "NO"
        if (
            fila["estado"] in _ESTADOS_NO_ENCAJAN
            or (tipo is not None and tipo in _TIPOS_NO_ENCAJAN)
            or (ramo is not None and ramo in _RAMOS_NO_ENCAJAN)
        )
        else "SI"
    )
    return ramo, tipo, encaje


def _ref_id_cobertura(fila, swap_a_sub, sub_a_swap):
    """Referencia independiente de la asignación id_cobertura (paso 8.5)."""
    id_norm = str(fila["id_posicion"]).strip()
    if fila["clase_origen"] == "SWAP":
        return swap_a_sub.get(id_norm)
    if fila["clase_origen"] == "TITULO":
        return sub_a_swap.get(id_norm)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias (identificadores simples y deterministas para el merge/mapeo)
# ─────────────────────────────────────────────────────────────────────────────
# Ids alfanuméricos (sin '|', que es el separador de subyacentes).
_id_simple = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=90, blacklist_categories=("Cs",)),
    min_size=1,
    max_size=6,
).filter(lambda s: s.strip() != "" and "|" not in s)

_estado_estrategia = st.sampled_from(["RG", "GA", "VIG", "ACT", "XYZ", ""])
_ramo_estrategia = st.sampled_from(
    ["VIDA", "SOAT", "AUTOS", "BOLIVAR EXCESO", "CAPITALIZADORA EXCESO", ""]
)
_tipo_estrategia = st.sampled_from(
    ["T1", "T2", "EMBARGO", "COLATERAL", "simultanea_activa", ""]
)
_clase_estrategia = st.sampled_from(["TITULO", "SWAP", "FORWARD", "OPCION"])


@st.composite
def _escenario(draw):
    """
    Genera un escenario coherente:
      - `param_portafolios`: portafolios registrados con ramo/tipo (únicos),
      - `portafolio_clasificado`: posiciones con clase_origen/estado/portafolio
        (algunas coinciden con registrados, otras no),
      - `param_diccionario_swaps`: swaps que existen como posiciones SWAP,
        cubriendo subyacentes que son ids de posición TITULO existentes.
    """
    n_pf = draw(st.integers(min_value=0, max_value=6))
    porfins = draw(st.lists(_id_simple, min_size=n_pf, max_size=n_pf, unique=True))
    param_portafolios = [
        {
            "portafolio_porfin": pf,
            "ramo": draw(_ramo_estrategia),
            "tipo": draw(_tipo_estrategia),
            "excluir": draw(st.sampled_from(["SI", "NO"])),
        }
        for pf in porfins
    ]

    n_pos = draw(st.integers(min_value=1, max_value=12))
    ids_pos = draw(st.lists(_id_simple, min_size=n_pos, max_size=n_pos, unique=True))

    portafolios_ajenos = draw(st.lists(_id_simple, min_size=0, max_size=4, unique=True))
    universo_portafolios = list(porfins) + list(portafolios_ajenos)
    if not universo_portafolios:
        universo_portafolios = draw(
            st.lists(_id_simple, min_size=1, max_size=3, unique=True)
        )

    posiciones = [
        {
            "id_posicion": idp,
            "clase_origen": draw(_clase_estrategia),
            "estado": draw(_estado_estrategia),
            "portafolio": draw(st.sampled_from(universo_portafolios)),
        }
        for idp in ids_pos
    ]

    ids_swaps = [p["id_posicion"] for p in posiciones if p["clase_origen"] == "SWAP"]
    ids_titulos = [p["id_posicion"] for p in posiciones if p["clase_origen"] == "TITULO"]
    param_diccionario_swaps = []
    if ids_swaps and ids_titulos:
        n_dic = draw(st.integers(min_value=0, max_value=len(ids_swaps)))
        swaps_elegidos = draw(
            st.lists(
                st.sampled_from(ids_swaps),
                min_size=n_dic,
                max_size=n_dic,
                unique=True,
            )
        )
        for sw in swaps_elegidos:
            subs = draw(
                st.lists(
                    st.sampled_from(ids_titulos),
                    min_size=1,
                    max_size=min(3, len(ids_titulos)),
                    unique=True,
                )
            )
            param_diccionario_swaps.append(
                {"id_posicion": sw, "Subyacente": "|".join(subs)}
            )

    return param_portafolios, posiciones, param_diccionario_swaps


def _crear_db_tmp():
    """DB SQLite temporal con las tablas param_* necesarias (nunca la real)."""
    fd, nombre = tempfile.mkstemp(suffix=".db", prefix="axis_prop4_")
    os.close(fd)
    conn = sqlite3.connect(nombre)
    conn.execute(
        "CREATE TABLE param_portafolios "
        "(portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"
    )
    conn.execute(
        "CREATE TABLE param_diccionario_swaps (id_posicion TEXT, Subyacente TEXT)"
    )
    conn.commit()
    return conn, nombre


def _sembrar(conn, param_portafolios, param_diccionario_swaps):
    """Siembra param_* con binding parametrizado (anti-inyección)."""
    conn.executemany(
        "INSERT INTO param_portafolios (portafolio_porfin, ramo, tipo, excluir) "
        "VALUES (?, ?, ?, ?)",
        [
            (r["portafolio_porfin"], r["ramo"], r["tipo"], r["excluir"])
            for r in param_portafolios
        ],
    )
    conn.executemany(
        "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) VALUES (?, ?)",
        [(r["id_posicion"], r["Subyacente"]) for r in param_diccionario_swaps],
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# PROPIEDAD 4
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(escenario=_escenario())
def test_noregresion_ramo_tipo_encaje_e_id_cobertura(escenario):
    """
    Los bloques REALES del paso 7 (ramo/tipo/encaje) y del paso 8.5
    (id_cobertura) del Clasificador producen exactamente las mismas asignaciones
    que la referencia de negocio, para cualesquiera insumos (Req 1.8, 2.8, 5.4).
    """
    param_portafolios, posiciones, param_diccionario_swaps = escenario

    conn, nombre = _crear_db_tmp()
    try:
        _sembrar(conn, param_portafolios, param_diccionario_swaps)

        df_port = pd.DataFrame(posiciones)
        df_final = _ejecutar_pasos_reales(df_port, conn)

        # Referencia independiente.
        mapa_portafolios = {
            r["portafolio_porfin"]: (r["ramo"], r["tipo"]) for r in param_portafolios
        }
        swap_a_sub = {}
        sub_a_swap = {}
        for r in param_diccionario_swaps:
            id_norm = str(r["id_posicion"]).strip()
            sub_val = str(r["Subyacente"]).strip()
            swap_a_sub[id_norm] = sub_val
            for sub in sub_val.split("|"):
                sub_a_swap[sub.strip()] = id_norm

        df_final = df_final.set_index("id_posicion")
        for pos in posiciones:
            idp = pos["id_posicion"]
            fila_real = df_final.loc[idp]
            ramo_ref, tipo_ref, encaje_ref = _ref_ramo_tipo_encaje(pos, mapa_portafolios)
            cobertura_ref = _ref_id_cobertura(pos, swap_a_sub, sub_a_swap)

            # Ramo: presente si el portafolio está registrado; ausente si no.
            ramo_real = fila_real["ramo"]
            if ramo_ref is None:
                assert pd.isna(ramo_real), (idp, ramo_real, ramo_ref)
            else:
                assert ramo_real == ramo_ref, (idp, ramo_real, ramo_ref)

            # Encaje: coincide con la referencia (Req 1.8).
            assert fila_real["encaje"] == encaje_ref, (
                idp,
                fila_real["encaje"],
                encaje_ref,
            )

            # id_cobertura: coincide con el diccionario (Req 2.8).
            cob_real = fila_real["id_cobertura"]
            if cobertura_ref is None:
                assert pd.isna(cob_real) or cob_real is None, (idp, cob_real, cobertura_ref)
            else:
                assert cob_real == cobertura_ref, (idp, cob_real, cobertura_ref)
    finally:
        conn.close()
        try:
            os.unlink(nombre)
        except OSError:
            pass


@_AJUSTES
@given(escenario=_escenario())
def test_noregresion_determinista_dos_corridas_iguales(escenario):
    """
    Con los mismos insumos, dos corridas de los bloques reales (paso 7 + 8.5)
    producen exactamente las mismas asignaciones (no-regresión / determinismo,
    Req 5.4).
    """
    param_portafolios, posiciones, param_diccionario_swaps = escenario

    def _corrida():
        conn, nombre = _crear_db_tmp()
        try:
            _sembrar(conn, param_portafolios, param_diccionario_swaps)
            df = _ejecutar_pasos_reales(pd.DataFrame(posiciones), conn)
            return df.set_index("id_posicion")[["ramo", "encaje", "id_cobertura"]]
        finally:
            conn.close()
            try:
                os.unlink(nombre)
            except OSError:
                pass

    a = _corrida()
    b = _corrida()
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())


# ─────────────────────────────────────────────────────────────────────────────
# Ejemplo unitario de anclaje (edge cases explícitos)
# ─────────────────────────────────────────────────────────────────────────────
def test_ejemplo_unitario_ramo_tipo_e_id_cobertura():
    """
    Caso concreto: un swap con dos subyacentes-título y una posición cuyo
    portafolio no está registrado (ramo ausente, encaje 'SI').
    """
    param_portafolios = [
        {"portafolio_porfin": "P1", "ramo": "VIDA", "tipo": "T1", "excluir": "NO"},
        {
            "portafolio_porfin": "P2",
            "ramo": "BOLIVAR EXCESO",
            "tipo": "T2",
            "excluir": "NO",
        },
    ]
    posiciones = [
        {"id_posicion": "SW1", "clase_origen": "SWAP", "estado": "VIG", "portafolio": "P1"},
        {"id_posicion": "T1", "clase_origen": "TITULO", "estado": "VIG", "portafolio": "P1"},
        {"id_posicion": "T2", "clase_origen": "TITULO", "estado": "VIG", "portafolio": "P2"},
        {"id_posicion": "T3", "clase_origen": "TITULO", "estado": "VIG", "portafolio": "PX"},
    ]
    param_diccionario_swaps = [{"id_posicion": "SW1", "Subyacente": "T1|T2"}]

    conn, nombre = _crear_db_tmp()
    try:
        _sembrar(conn, param_portafolios, param_diccionario_swaps)
        df = _ejecutar_pasos_reales(pd.DataFrame(posiciones), conn).set_index("id_posicion")

        # ramo/encaje
        assert df.loc["SW1", "ramo"] == "VIDA"
        assert df.loc["SW1", "encaje"] == "SI"
        assert df.loc["T2", "ramo"] == "BOLIVAR EXCESO"
        assert df.loc["T2", "encaje"] == "NO"  # ramo fuerza NO
        assert pd.isna(df.loc["T3", "ramo"])   # portafolio no registrado
        assert df.loc["T3", "encaje"] == "SI"

        # id_cobertura
        assert df.loc["SW1", "id_cobertura"] == "T1|T2"  # swap -> subyacente(s)
        assert df.loc["T1", "id_cobertura"] == "SW1"      # título -> swap
        assert df.loc["T2", "id_cobertura"] == "SW1"
        assert pd.isna(df.loc["T3", "id_cobertura"])      # sin cobertura
    finally:
        conn.close()
        try:
            os.unlink(nombre)
        except OSError:
            pass
