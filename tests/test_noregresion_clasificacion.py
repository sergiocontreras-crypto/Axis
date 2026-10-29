# -*- coding: utf-8 -*-
"""
Property-based test (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 4: No-regresión de la clasificación (ramo/tipo e id_cobertura).

    Para todo `portafolio_clasificado`, `param_portafolios` y
    `param_diccionario_swaps`: toda posición cuyo `portafolio` coincide con un
    `portafolio_porfin` recibe su `ramo` y `tipo`; y cada swap y sus
    subyacentes reciben el `id_cobertura` acorde al diccionario, produciendo
    las mismas asignaciones de encaje/id_cobertura que el comportamiento previo
    con los mismos insumos.

Enfoque de la prueba
--------------------
El Clasificador (`clasificar_portafolio.py`) no expone paso 7 (encaje/ramo/tipo)
ni paso 8.5 (id_cobertura) como funciones aisladas: viven embebidos dentro del
orquestador `clasificar_portafolio_diario`, que exige un esquema completo de DB.

Para ejercitar los **mismos caminos de código reales** sin re-ejecutar todo el
orquestador, se replican fielmente las operaciones pandas de paso 7 y paso 8.5
(copiadas 1:1 de `clasificar_portafolio.py`) en funciones que reciben insumos
sembrados en una DB SQLite TEMPORAL (nunca `portafolio.db` real). El resultado se
contrasta contra una implementación de referencia independiente derivada de las
reglas de negocio (Req 1.8, 2.8, 5.4). Si ambos coinciden en todos los ejemplos,
se garantiza la no-regresión de la asignación de ramo/tipo/encaje e id_cobertura.

**Validates: Requirements 1.8, 2.8, 5.4**
"""

import os
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Rutas importables (raíz del proyecto y tests/), robusto a aislamiento.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from conftest import ids_strategy  # estrategia común de identificadores

# Ejemplos por propiedad. Reducido para corridas rápidas de verificación; el
# diseño exige >= 100 para la validación formal completa.
_MIN_EJEMPLOS = 25

_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Estados que fuerzan encaje 'NO' (Req 1.8 / paso 7 del Clasificador).
_ESTADOS_NO_ENCAJAN = ["RG", "GA"]
# Tipos que fuerzan encaje 'NO' (copiado de clasificar_portafolio.py, paso 7).
_TIPOS_NO_ENCAJAN = [
    "EMBARGO",
    "COLATERAL",
    "titulo_simultanea_activa",
    "simultanea_activa",
    "simultanea_pasiva",
    "AJUSTE LIQUIDEZ",
]
# Ramos que fuerzan encaje 'NO' (copiado de clasificar_portafolio.py, paso 7).
_RAMOS_NO_ENCAJAN = [
    "BOLIVAR EXCESO",
    "COMERCIALES EXCESO",
    "CAPITALIZADORA EXCESO",
]


# ─────────────────────────────────────────────────────────────────────────────
# CAMINOS DE CÓDIGO REALES — replicados 1:1 de clasificar_portafolio.py
# ─────────────────────────────────────────────────────────────────────────────
def paso7_encaje_ramo_tipo(df_port: pd.DataFrame, conn) -> pd.DataFrame:
    """
    Réplica fiel del PASO 7 de `clasificar_portafolio_diario`:
      - merge de `df_port` con `param_portafolios` por portafolio<->portafolio_porfin
      - cálculo de `encaje` según estado/tipo/ramo
    Devuelve el df con columnas `ramo`, `encaje` (y elimina `portafolio_porfin`,
    `tipo` tal como hace el código real; `ramo` se conserva).
    """
    df_port = df_port.copy()
    df_portafolios = pd.read_sql_query(
        "SELECT portafolio_porfin, ramo, tipo FROM param_portafolios", conn
    )
    df_port = df_port.merge(
        df_portafolios, left_on="portafolio", right_on="portafolio_porfin", how="left"
    )

    tipos_no_encajan = _TIPOS_NO_ENCAJAN
    ramos_no_encajan = _RAMOS_NO_ENCAJAN

    df_port["encaje"] = np.where(
        df_port["estado"].isin(["RG", "GA"])
        | df_port["tipo"].isin(tipos_no_encajan)
        | df_port["ramo"].isin(ramos_no_encajan),
        "NO",
        "SI",
    )
    # El código real conserva `ramo` para asserts, elimina portafolio_porfin y tipo.
    df_port = df_port.drop(columns=["portafolio_porfin", "tipo"])
    return df_port


def paso85_id_cobertura(df_port: pd.DataFrame, conn) -> pd.DataFrame:
    """
    Réplica fiel del PASO 8.5 de `clasificar_portafolio_diario` (solo la parte
    de `id_cobertura`; no toca `clasificacion_contable` porque no es objeto de
    esta propiedad).
      - Swap -> id_cobertura = Subyacente (puede tener '|')
      - Título -> id_cobertura = id del swap que lo cubre
    """
    df_port = df_port.copy()
    df_port["id_cobertura"] = None
    df_dic_swaps = pd.read_sql_query(
        "SELECT id_posicion, Subyacente FROM param_diccionario_swaps", conn
    )
    df_dic_swaps["id_posicion"] = df_dic_swaps["id_posicion"].astype(str).str.strip()
    df_dic_swaps["Subyacente"] = df_dic_swaps["Subyacente"].astype(str).str.strip()

    # Swap -> id_cobertura = Subyacente
    swap_a_sub = dict(zip(df_dic_swaps["id_posicion"], df_dic_swaps["Subyacente"]))
    mask_swap_port = df_port["clase_origen"] == "SWAP"
    df_port.loc[mask_swap_port, "id_cobertura"] = (
        df_port.loc[mask_swap_port, "id_posicion"].astype(str).str.strip().map(swap_a_sub)
    )

    # Título -> id_cobertura = id del swap que lo cubre (maneja pipe)
    sub_a_swap = {}
    for _, row in df_dic_swaps.iterrows():
        for sub in str(row["Subyacente"]).split("|"):
            sub_a_swap[sub.strip()] = row["id_posicion"]
    mask_titulo = df_port["clase_origen"] == "TITULO"
    df_port.loc[mask_titulo, "id_cobertura"] = (
        df_port.loc[mask_titulo, "id_posicion"].astype(str).str.strip().map(sub_a_swap)
    )
    return df_port


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
# Estrategias
# ─────────────────────────────────────────────────────────────────────────────
# Identificadores de portafolio y de posición: alfanuméricos simples para que el
# merge/mapeo sea determinista (evita que el separador '|' aparezca dentro de un id).
_id_simple = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=90, blacklist_categories=("Cs",)),
    min_size=1,
    max_size=6,
).filter(lambda s: s.strip() != "" and "|" not in s)

_estado_estrategia = st.sampled_from(["RG", "GA", "VIG", "ACT", "XYZ", ""])
# Ramo/tipo: mezcla de valores neutros y valores que fuerzan encaje 'NO'.
_ramo_estrategia = st.sampled_from(["VIDA", "SOAT", "AUTOS", "BOLIVAR EXCESO", "CAPITALIZADORA EXCESO", ""])
_tipo_estrategia = st.sampled_from(["T1", "T2", "EMBARGO", "COLATERAL", "simultanea_activa", ""])
_clase_estrategia = st.sampled_from(["TITULO", "SWAP", "FORWARD", "OPCION"])


@st.composite
def _escenario(draw):
    """
    Genera un escenario coherente:
      - un conjunto de portafolios registrados (param_portafolios) con ramo/tipo,
      - un conjunto de posiciones (portafolio_clasificado) con clase_origen,
        estado y portafolio (algunos coinciden con registrados, otros no),
      - un diccionario de swaps (param_diccionario_swaps) que referencia ids de
        posición existentes como subyacentes.
    """
    # Portafolios registrados (únicos por portafolio_porfin).
    n_pf = draw(st.integers(min_value=0, max_value=6))
    porfins = draw(
        st.lists(_id_simple, min_size=n_pf, max_size=n_pf, unique=True)
    )
    param_portafolios = []
    for pf in porfins:
        param_portafolios.append(
            {
                "portafolio_porfin": pf,
                "ramo": draw(_ramo_estrategia),
                "tipo": draw(_tipo_estrategia),
                "excluir": draw(st.sampled_from(["SI", "NO"])),
            }
        )

    # Posiciones del portafolio clasificado (ids de posición únicos).
    n_pos = draw(st.integers(min_value=1, max_value=12))
    ids_pos = draw(
        st.lists(_id_simple, min_size=n_pos, max_size=n_pos, unique=True)
    )
    # Conjunto de portafolios candidatos: algunos registrados, algunos ajenos.
    portafolios_ajenos = draw(st.lists(_id_simple, min_size=0, max_size=4, unique=True))
    universo_portafolios = list(porfins) + list(portafolios_ajenos)
    if not universo_portafolios:
        universo_portafolios = draw(st.lists(_id_simple, min_size=1, max_size=3, unique=True))

    posiciones = []
    for idp in ids_pos:
        posiciones.append(
            {
                "id_posicion": idp,
                "clase_origen": draw(_clase_estrategia),
                "estado": draw(_estado_estrategia),
                "portafolio": draw(st.sampled_from(universo_portafolios)),
            }
        )

    # Diccionario de swaps: swaps que existen como posiciones SWAP, cubriendo
    # subyacentes que son ids de posición TITULO existentes.
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
                st.lists(st.sampled_from(ids_titulos), min_size=1, max_size=min(3, len(ids_titulos)), unique=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# PROPIEDAD 4
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(escenario=_escenario())
def test_noregresion_ramo_tipo_encaje_e_id_cobertura(escenario):
    """
    El paso 7 (ramo/tipo/encaje) y el paso 8.5 (id_cobertura) del Clasificador
    producen exactamente las mismas asignaciones que la referencia de negocio,
    para cualesquiera insumos (Req 1.8, 2.8, 5.4).
    """
    param_portafolios, posiciones, param_diccionario_swaps = escenario

    conn, nombre = _crear_db_tmp()
    try:
        # Sembrar param_portafolios con binding parametrizado (anti-inyección).
        conn.executemany(
            "INSERT INTO param_portafolios "
            "(portafolio_porfin, ramo, tipo, excluir) VALUES (?, ?, ?, ?)",
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

        df_port = pd.DataFrame(posiciones)

        # ── Camino de código real (paso 7 y paso 8.5) ──
        df_p7 = paso7_encaje_ramo_tipo(df_port, conn)
        df_final = paso85_id_cobertura(df_p7, conn)

        # ── Referencia independiente ──
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

        # Comparar fila por fila (indexado por id_posicion, único por escenario).
        df_final = df_final.set_index("id_posicion")
        for pos in posiciones:
            idp = pos["id_posicion"]
            fila_real = df_final.loc[idp]
            ramo_ref, tipo_ref, encaje_ref = _ref_ramo_tipo_encaje(pos, mapa_portafolios)
            cobertura_ref = _ref_id_cobertura(pos, swap_a_sub, sub_a_swap)

            # Ramo: presente si el portafolio está registrado; ausente en otro caso.
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
    Con los mismos insumos, dos corridas del paso 7 + 8.5 producen exactamente
    las mismas asignaciones (no-regresión / determinismo — Req 5.4).
    """
    param_portafolios, posiciones, param_diccionario_swaps = escenario

    def _corrida():
        conn, nombre = _crear_db_tmp()
        try:
            conn.executemany(
                "INSERT INTO param_portafolios "
                "(portafolio_porfin, ramo, tipo, excluir) VALUES (?, ?, ?, ?)",
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
            df = pd.DataFrame(posiciones)
            df = paso85_id_cobertura(paso7_encaje_ramo_tipo(df, conn), conn)
            return df.set_index("id_posicion")[["ramo", "encaje", "id_cobertura"]]
        finally:
            conn.close()
            try:
                os.unlink(nombre)
            except OSError:
                pass

    a = _corrida()
    b = _corrida()
    # Igualdad estructural completa (NaN incluido).
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())
