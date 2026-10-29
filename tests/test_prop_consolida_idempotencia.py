"""
Property test de IDEMPOTENCIA de `consolidar_negociables_fungibles`
(feature `optimizacion-agrupacion-negociables`, tarea 2.8 / Property 7).

`consolidar_negociables_fungibles(df)` colapsa los `Titulo_Negociable` fungibles
del DataFrame de cierre del tramo en un único `Titulo_Consolidado` por
`Grupo_Fungible`, dejando intactas las demás filas (a TIR, no-RF, subyacentes de
swap y filas con clave degenerada). Es una función PURA que devuelve
`(df_consolidado, ResumenConsolidacion)`.

Tras la primera pasada cada grupo fungible queda con a lo sumo un miembro (los
miembros se colapsan en un único `Titulo_Consolidado` con `id_posicion` sintético
determinista). Por tanto, una segunda pasada no puede colapsar nada más: el
resultado debe ser idéntico y el `ResumenConsolidacion` de la segunda pasada debe
reportar `n_grupos == 0` y `n_colapsadas == 0`.

_Requirements: 1.1, 1.2_
"""

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from proyeccion_tramos import consolidar_negociables_fungibles


# ── Columnas del DataFrame de cierre (subconjunto estable de la allowlist) ───
COLS = [
    "id_posicion", "especie", "isin", "fecha_vencimiento", "nominal",
    "valor_mercado", "valor_tir", "tasa_compra", "clasificacion_contable",
    "tasa_facial", "tipo_tasa", "moneda", "periodicidad",
    "asset_class_definitivo", "tasa_referencia", "clase_origen",
    "id_cobertura", "fecha_emision",
]


# ── Alfabetos acotados para forzar COLISIONES de clave (grupos fungibles) ────
# Pocos valores por campo ⇒ alta probabilidad de que dos Negociables compartan
# la Clave_Fungibilidad y por tanto se consoliden (ejercita el colapso real).
_especies = st.sampled_from(["ESP1", "esp1 ", "  ESP2", "BONÓ"])
_isines = st.sampled_from(["ISIN1", "isin1", "ISIN2"])
_vencimientos = st.sampled_from(["2030-12-31", "2031-06-30", "no-es-fecha"])
_tasas_faciales = st.sampled_from([0.05, 0.05 + 1e-15, 0.07, float("nan")])
_periodicidades = st.sampled_from(["ANUAL", "anual ", "SEMESTRAL"])
_tipos_tasa = st.sampled_from(["FIJA", "fija", "FLOTANTE"])
_monedas = st.sampled_from(["COP", "cop ", "USD", "UVR"])
_asset_classes = st.sampled_from(["TF", "TES UVR", "LEGATRUU"])
_tasas_ref = st.sampled_from(["", "IPC"])
_clasificaciones = st.sampled_from(
    ["Negociable", "negociable ", "Vencimiento", "Disp. Venta"]
)
_clases_origen = st.sampled_from(
    ["TITULO", "", "RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"]
)
_coberturas = st.sampled_from(["", "COB123"])
_montos = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)
_fechas_emision = st.sampled_from(["2026-01-31", "2026-03-31", "2025-12-15"])


@st.composite
def _fila(draw, idx: int):
    """Genera una fila arbitraria del snapshot de cierre con `id_posicion` único."""
    return {
        "id_posicion": f"ID{idx}",
        "especie": draw(_especies),
        "isin": draw(_isines),
        "fecha_vencimiento": draw(_vencimientos),
        "nominal": draw(_montos),
        "valor_mercado": draw(_montos),
        "valor_tir": draw(_montos),
        "tasa_compra": draw(_montos),
        "clasificacion_contable": draw(_clasificaciones),
        "tasa_facial": draw(_tasas_faciales),
        "tipo_tasa": draw(_tipos_tasa),
        "moneda": draw(_monedas),
        "periodicidad": draw(_periodicidades),
        "asset_class_definitivo": draw(_asset_classes),
        "tasa_referencia": draw(_tasas_ref),
        "clase_origen": draw(_clases_origen),
        "id_cobertura": draw(_coberturas),
        "fecha_emision": draw(_fechas_emision),
    }


@st.composite
def _df_titulos(draw):
    """Genera un DataFrame con una mezcla arbitraria de 0..12 filas."""
    n = draw(st.integers(min_value=0, max_value=12))
    filas = [draw(_fila(i)) for i in range(n)]
    return pd.DataFrame(filas, columns=COLS)


def _normalizar_para_comparar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza un DataFrame para comparar robustamente dos pasadas:
    ordena las filas por `id_posicion` (los ids consolidados son deterministas:
    `CONS_<hash>`), resetea el índice y usa representación en texto para evitar
    falsos negativos por dtype (NaN, Timestamp vs str, etc.).
    """
    out = df.sort_values(by="id_posicion", kind="stable").reset_index(drop=True)
    return out.astype(str)


# Feature: optimizacion-agrupacion-negociables, Property 7: Idempotencia de la consolidación
# Para cualquier conjunto de títulos, consolidar(consolidar(x)) ≡ consolidar(x),
# porque tras la primera pasada cada grupo fungible ya tiene a lo sumo un miembro.
# Validates: Requirements 1.1, 1.2
@settings(max_examples=200, deadline=None)
@given(df=_df_titulos())
def test_prop7_idempotencia_consolidacion(df):
    # Primera pasada.
    out1, res1 = consolidar_negociables_fungibles(df)
    # Segunda pasada sobre el resultado de la primera.
    out2, res2 = consolidar_negociables_fungibles(out1)

    # (a) El resultado es un punto fijo: la segunda pasada no cambia las filas.
    assert len(out1) == len(out2)
    izq = _normalizar_para_comparar(out1)
    der = _normalizar_para_comparar(out2)
    assert list(izq.columns) == list(der.columns)
    assert izq.equals(der), (
        "La segunda pasada difiere de la primera (no idempotente)\n"
        f"out1=\n{izq}\nout2=\n{der}"
    )

    # (b) Tras la primera pasada no queda ningún grupo fungible por colapsar.
    assert res2.n_grupos == 0, f"res2.n_grupos={res2.n_grupos} (debería ser 0)"
    assert res2.n_colapsadas == 0, f"res2.n_colapsadas={res2.n_colapsadas} (debería ser 0)"

    # (c) Coherencia: la segunda pasada ni crea ni destruye filas.
    assert res2.n_entrada == res2.n_salida == len(out1)
