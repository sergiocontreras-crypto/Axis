"""
Property test de la feature `optimizacion-agrupacion-negociables`.

Property 3: Los títulos a TIR y las filas no-RF nunca se alteran.

Verifica la restricción de negocio INVIOLABLE (Req 2.1, 2.2, 2.4) de
`consolidar_negociables_fungibles` (`scripts/proyeccion_tramos.py`): toda fila a TIR
(`Vencimiento` / `Disp. Venta`) y toda fila no-RF (`RENTA_VARIABLE`, `ALTERNATIVO`,
`DERIVADO`) debe aparecer en la salida SIN CAMBIOS (mismo `id_posicion`, `nominal`,
`tasa_compra`, `valor_tir`) y NUNCA fusionarse con ninguna otra fila.

Convenciones: mismas que `tests/test_proyeccion_tramos.py` / `tests/conftest.py`
(scripts/ importable, Hypothesis >= 100 iteraciones, generadores con alfabeto acotado).
"""

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from proyeccion_tramos import consolidar_negociables_fungibles


# ── Alfabetos acotados para ejercitar la normalización sin explotar el espacio ──
_ESPECIES = st.sampled_from(["TFIT", "TUVT", "BONO", "YANK"])
_ISINES = st.sampled_from(["CO1", "CO2", "US1", ""])
_MONEDAS = st.sampled_from(["COP", "USD", "UVR"])
_PERIODICIDADES = st.sampled_from(["ANUAL", "SEMESTRAL", "TRIMESTRAL"])
_TIPOS_TASA = st.sampled_from(["EFECTIVO", "NOMINAL"])
_VENCIMIENTOS = st.sampled_from(
    ["2030-01-01", "2032-06-30", "2035-12-31", "2028-03-15"]
)
_TASAS_FACIALES = st.sampled_from(["7.0", "7.5", "6.25", "8.0"])

# Clasificaciones a TIR (INTOCABLES por Req 2.1, 2.2) y clases no-RF (Req 2.4).
_CLASIF_A_TIR = st.sampled_from(["Vencimiento", "Disp. Venta"])
_CLASES_NO_RF = st.sampled_from(["RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"])

# Montos finitos (incluye extremos y negativos para ejercitar la no-alteración).
_MONTOS = st.floats(
    min_value=-1e9, max_value=1e12, allow_nan=False, allow_infinity=False
)


@st.composite
def _fila_negociable(draw, idx):
    """Fila RF Negociable (candidata a consolidación)."""
    return {
        "id_posicion": f"NEG_{idx}",
        "especie": draw(_ESPECIES),
        "isin": draw(_ISINES),
        "moneda": draw(_MONEDAS),
        "fecha_vencimiento": draw(_VENCIMIENTOS),
        "fecha_emision": "2024-01-01",
        "nominal": draw(_MONTOS),
        "valor_mercado": draw(_MONTOS),
        "valor_tir": draw(_MONTOS),
        "tasa_compra": draw(_MONTOS),
        "tasa_facial": draw(_TASAS_FACIALES),
        "periodicidad": draw(_PERIODICIDADES),
        "tipo_tasa": draw(_TIPOS_TASA),
        "asset_class_definitivo": "DP_TF",
        "tasa_referencia": "",
        "clasificacion_contable": "Negociable",
        "clase_origen": "TITULO",
        "id_cobertura": "",
    }


@st.composite
def _fila_a_tir(draw, idx):
    """Fila a TIR (Vencimiento / Disp. Venta): INTOCABLE (Req 2.1, 2.2)."""
    return {
        "id_posicion": f"TIR_{idx}",
        "especie": draw(_ESPECIES),
        "isin": draw(_ISINES),
        "moneda": draw(_MONEDAS),
        "fecha_vencimiento": draw(_VENCIMIENTOS),
        "fecha_emision": "2024-01-01",
        "nominal": draw(_MONTOS),
        "valor_mercado": draw(_MONTOS),
        "valor_tir": draw(_MONTOS),
        "tasa_compra": draw(_MONTOS),
        "tasa_facial": draw(_TASAS_FACIALES),
        "periodicidad": draw(_PERIODICIDADES),
        "tipo_tasa": draw(_TIPOS_TASA),
        "asset_class_definitivo": "DP_TF",
        "tasa_referencia": "",
        "clasificacion_contable": draw(_CLASIF_A_TIR),
        "clase_origen": "TITULO",
        "id_cobertura": "",
    }


@st.composite
def _fila_no_rf(draw, idx):
    """Fila no-RF (RENTA_VARIABLE / ALTERNATIVO / DERIVADO): INTOCABLE (Req 2.4)."""
    return {
        "id_posicion": f"NORF_{idx}",
        "especie": draw(_ESPECIES),
        "isin": draw(_ISINES),
        "moneda": draw(_MONEDAS),
        "fecha_vencimiento": draw(_VENCIMIENTOS),
        "fecha_emision": "2024-01-01",
        "nominal": draw(_MONTOS),
        "valor_mercado": draw(_MONTOS),
        "valor_tir": draw(_MONTOS),
        "tasa_compra": draw(_MONTOS),
        "tasa_facial": draw(_TASAS_FACIALES),
        "periodicidad": draw(_PERIODICIDADES),
        "tipo_tasa": draw(_TIPOS_TASA),
        "asset_class_definitivo": "RV",
        "tasa_referencia": "",
        # Clasificación arbitraria: la exclusión manda por clase_origen (Req 2.4).
        "clasificacion_contable": draw(
            st.sampled_from(["Negociable", "Vencimiento", "Disp. Venta", ""])
        ),
        "clase_origen": draw(_CLASES_NO_RF),
        "id_cobertura": "",
    }


@st.composite
def _mezcla_filas(draw):
    """
    Mezcla filas Negociables RF con filas a TIR y no-RF. Cada tipo con id único por
    familia (NEG_/TIR_/NORF_) para poder localizar las INTOCABLES verbatim en la salida.
    """
    n_neg = draw(st.integers(min_value=0, max_value=6))
    n_tir = draw(st.integers(min_value=0, max_value=5))
    n_norf = draw(st.integers(min_value=0, max_value=5))

    filas = []
    for i in range(n_neg):
        filas.append(draw(_fila_negociable(i)))
    for i in range(n_tir):
        filas.append(draw(_fila_a_tir(i)))
    for i in range(n_norf):
        filas.append(draw(_fila_no_rf(i)))

    # Barajar el orden para no asumir posición de las intocables.
    orden = draw(st.permutations(list(range(len(filas)))))
    return [filas[k] for k in orden]


def _misma_celda(a, b) -> bool:
    """Igualdad robusta celda-a-celda (NaN == NaN, floats exactos tras round-trip)."""
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


# Feature: optimizacion-agrupacion-negociables, Property 3: Los títulos a TIR y las filas
# no-RF nunca se alteran
@settings(max_examples=200, deadline=None)
@given(filas=_mezcla_filas())
def test_prop3_tir_y_no_rf_intocables(filas):
    """
    Toda fila a TIR (Vencimiento/Disp. Venta) y toda fila no-RF (RENTA_VARIABLE,
    ALTERNATIVO, DERIVADO) aparece en la salida sin cambios (mismo id_posicion, nominal,
    tasa_compra, valor_tir) y nunca se fusiona (Req 2.1, 2.2, 2.4).
    """
    df_entrada = pd.DataFrame(filas)
    df_salida, _resumen = consolidar_negociables_fungibles(df_entrada)

    # Entrada no mutada (función pura): mismo nº de filas que la mezcla original.
    assert len(df_entrada) == len(filas)

    # ids de salida (incluye consolidados sintéticos CONS_<hash> para los Negociables).
    # Caso degenerado: con `filas` vacío el DataFrame no tiene columnas y la función
    # devuelve el df tal cual (sin `id_posicion`); no hay intocables que verificar.
    ids_salida = (
        list(df_salida["id_posicion"]) if "id_posicion" in df_salida.columns else []
    )

    # Las filas intocables son las que empiezan por TIR_ o NORF_ (ids inyectados).
    intocables = [
        f for f in filas
        if str(f["id_posicion"]).startswith(("TIR_", "NORF_"))
    ]

    for fila_in in intocables:
        pid = fila_in["id_posicion"]

        # 1) Aparece EXACTAMENTE una vez (nunca fusionada ni duplicada).
        apariciones = ids_salida.count(pid)
        assert apariciones == 1, (
            f"la fila intocable {pid!r} debe aparecer 1 vez, aparece {apariciones}"
        )

        # 2) Su id nunca se transformó en un consolidado sintético.
        assert not str(pid).startswith("CONS_")

        # 3) La fila de salida es idéntica a la de entrada en los campos clave.
        fila_out = df_salida.loc[df_salida["id_posicion"] == pid].iloc[0]
        for campo in ("id_posicion", "nominal", "tasa_compra", "valor_tir"):
            assert _misma_celda(fila_out[campo], fila_in[campo]), (
                f"fila intocable {pid!r} alterada en {campo!r}: "
                f"entrada={fila_in[campo]!r} salida={fila_out[campo]!r}"
            )

    # 4) Ninguna fila intocable se fusionó: el nº de ids intocables presentes en la salida
    #    es exactamente el nº de intocables de entrada (ni se pierden ni colapsan).
    ids_intocables_entrada = {f["id_posicion"] for f in intocables}
    presentes = sum(1 for pid in ids_salida if pid in ids_intocables_entrada)
    assert presentes == len(intocables)
