"""
Property test de la feature `optimizacion-agrupacion-negociables`.

Property 5: Los subyacentes de swap no se consolidan.

Verifica que `consolidar_negociables_fungibles` deja intacto todo `Titulo_Negociable`
que sea subyacente de un swap (tiene `id_cobertura` no vacío): no se fusiona con ningún
otro título, aunque comparta la `Clave_Fungibilidad` con otras filas Negociables que, de
no tener cobertura, sí se consolidarían. Esto preserva el enlace de cobertura (Req 2.5).

Property test con Hypothesis (>= 100 iteraciones), etiquetado con el texto de la
propiedad del diseño. Sigue las convenciones de `tests/test_proyeccion_tramos.py`
(import de `proyeccion_tramos`, `settings(deadline=None)`), apoyado por el ajuste de
`sys.path` que hace `tests/conftest.py`.
"""

from datetime import date

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

import proyeccion_tramos as pt
from proyeccion_tramos import consolidar_negociables_fungibles


# ── Columnas que una fila del snapshot RF debe traer para ejercitar la clave y las
#    exclusiones de la consolidación (subconjunto relevante de `_COLUMNAS_PROY`). ──
_COLUMNAS = [
    "id_posicion",
    "especie",
    "isin",
    "fecha_vencimiento",
    "tasa_facial",
    "periodicidad",
    "tipo_tasa",
    "moneda",
    "asset_class_definitivo",
    "tasa_referencia",
    "fecha_emision",
    "nominal",
    "valor_mercado",
    "valor_tir",
    "clasificacion_contable",
    "clase_origen",
    "id_cobertura",
]


# ── Estrategias acotadas (alfabeto pequeño para forzar colisiones de clave). ──
_especies = st.sampled_from(["TFIT", "TUVT", "YANKEE1", "CORP"])
_isines = st.sampled_from(["CO1", "CO2", "US1"])
_monedas = st.sampled_from(["COP", "UVR", "USD"])
_periodicidades = st.sampled_from(["ANUAL", "SEMESTRAL", "TRIMESTRAL"])
_tipos_tasa = st.sampled_from(["EFECTIVO", "NOMINAL"])
_vencimientos = st.sampled_from([date(2030, 1, 1), date(2032, 6, 30), date(2035, 12, 31)])
_tasas = st.sampled_from([5.0, 6.2, 6.3, 7.0])
_nominales = st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False)
_vm = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
# id_cobertura NO vacío: alfabeto acotado de identificadores de cobertura. Se incluye un
# valor con espacios de borde (" SW X ") para ejercitar la normalización de `_es_vacio`
# (que hace strip). Se usa `sampled_from` en lugar de `text().filter(...)` para evitar la
# generación lenta de entradas (HealthCheck.too_slow de Hypothesis).
_coberturas = st.sampled_from(["SWP1", "SWP-2", "COB_3", " SW X ", "9A8B"])


@st.composite
def _fila_negociable(draw, *, con_cobertura: bool, id_pos: str):
    """
    Genera una fila `Titulo_Negociable` de Renta Fija. Si `con_cobertura` es True la fila
    es subyacente de swap (id_cobertura no vacío) y por diseño debe quedar INTOCABLE.
    """
    return {
        "id_posicion": id_pos,
        "especie": draw(_especies),
        "isin": draw(_isines),
        "fecha_vencimiento": draw(_vencimientos),
        "tasa_facial": draw(_tasas),
        "periodicidad": draw(_periodicidades),
        "tipo_tasa": draw(_tipos_tasa),
        "moneda": draw(_monedas),
        "asset_class_definitivo": "DP LP",
        "tasa_referencia": "",
        "fecha_emision": date(2026, 1, 1),
        "nominal": draw(_nominales),
        "valor_mercado": draw(_vm),
        "valor_tir": draw(_vm),
        "clasificacion_contable": "Negociable",
        "clase_origen": "TITULO",
        "id_cobertura": draw(_coberturas) if con_cobertura else "",
    }


@st.composite
def _conjunto_titulos(draw):
    """
    Construye un conjunto de filas mezclando:
      - 1..6 subyacentes de swap (Negociables con id_cobertura no vacío),
      - 0..6 Negociables SIN cobertura (candidatos a consolidarse entre sí).

    Las claves de fungibilidad colisionan deliberadamente (alfabetos pequeños), de modo
    que SIN la exclusión por swap los subyacentes se fusionarían con otras filas; la
    propiedad exige que no lo hagan.
    """
    n_swap = draw(st.integers(min_value=1, max_value=6))
    n_libres = draw(st.integers(min_value=0, max_value=6))

    filas = []
    for i in range(n_swap):
        filas.append(draw(_fila_negociable(con_cobertura=True, id_pos=f"SWAP_{i}")))
    for i in range(n_libres):
        filas.append(draw(_fila_negociable(con_cobertura=False, id_pos=f"LIBRE_{i}")))
    return filas


# ==============================================================================
# Property 5: Los subyacentes de swap no se consolidan
# ==============================================================================

# Feature: optimizacion-agrupacion-negociables, Property 5: Los subyacentes de swap no se
# consolidan
# Validates: Requirements 2.5
@settings(max_examples=100, deadline=None)
@given(filas=_conjunto_titulos())
def test_prop5_subyacentes_swap_no_se_consolidan(filas):
    df_entrada = pd.DataFrame(filas, columns=_COLUMNAS)

    df_salida, _resumen = consolidar_negociables_fungibles(df_entrada)

    # Las filas con id_cobertura no vacío son los subyacentes de swap: deben preservarse
    # verbatim en la salida, cada una como una fila distinta (nunca fusionada).
    mask_swap = df_entrada["id_cobertura"].map(lambda v: not pt._es_vacio(v))
    ids_swap = list(df_entrada.loc[mask_swap, "id_posicion"])

    ids_salida = list(df_salida["id_posicion"])

    for id_swap in ids_swap:
        # 1) El subyacente de swap sigue presente por su id_posicion ORIGINAL (no se
        #    reemplazó por un id consolidado CONS_...).
        assert id_swap in ids_salida, (
            f"subyacente de swap {id_swap!r} desapareció de la salida"
        )
        # 2) Aparece EXACTAMENTE una vez (no se duplicó ni se fusionó).
        assert ids_salida.count(id_swap) == 1

        fila_ent = df_entrada.loc[df_entrada["id_posicion"] == id_swap].iloc[0]
        fila_sal = df_salida.loc[df_salida["id_posicion"] == id_swap].iloc[0]

        # 3) La fila queda INTACTA: nominal, VM, VTIR, cobertura y clave sin cambios.
        assert fila_sal["nominal"] == fila_ent["nominal"]
        assert fila_sal["valor_mercado"] == fila_ent["valor_mercado"]
        assert fila_sal["valor_tir"] == fila_ent["valor_tir"]
        assert fila_sal["id_cobertura"] == fila_ent["id_cobertura"]
        assert fila_sal["especie"] == fila_ent["especie"]
        assert fila_sal["isin"] == fila_ent["isin"]
        assert fila_sal["tasa_facial"] == fila_ent["tasa_facial"]
        assert fila_sal["moneda"] == fila_ent["moneda"]

    # 4) Ningún id de consolidado puede corresponder a un subyacente de swap: todos los
    #    ids originales de swap sobreviven, luego ninguno fue colapsado.
    assert set(ids_swap).issubset(set(ids_salida))
