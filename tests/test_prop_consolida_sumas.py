"""
Property-based test de la feature `optimizacion-agrupacion-negociables`.

Property 1: Conservación de sumas por grupo (nominal, VM, VTIR) y conservación de los
totales globales tras la consolidación de reinversiones Negociables fungibles.

Función bajo prueba: `consolidar_negociables_fungibles(df, *, tolerancia=1e-9)` en
`scripts/proyeccion_tramos.py`, que colapsa las filas RF Negociable fungibles (misma
`Clave_Fungibilidad`) en un único `Titulo_Consolidado` sumando `nominal`,
`valor_mercado` y `valor_tir`, dejando intactas el resto de filas (a TIR, no-RF,
subyacentes de swap y filas con clave degenerada).

Property tests con Hypothesis (>= 100 iteraciones), etiquetados con la propiedad del
diseño. El fixture `conftest.py` pone `scripts/` en `sys.path`.
"""

import math
from datetime import date

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import proyeccion_tramos as pt
from proyeccion_tramos import (
    consolidar_negociables_fungibles,
    _clave_fungibilidad,
    _COLUMNAS_SUMABLES_CONSOLIDADO,
)


# ── Columnas que lee la función (allowlist del snapshot usada por la consolidación) ──
_COLUMNAS_FILA = (
    "id_posicion",
    "especie",
    "isin",
    "fecha_vencimiento",
    "fecha_emision",
    "tasa_facial",
    "periodicidad",
    "tipo_tasa",
    "moneda",
    "asset_class_definitivo",
    "tasa_referencia",
    "clasificacion_contable",
    "clase_origen",
    "id_cobertura",
    "tasa_compra",
    "nominal",
    "valor_mercado",
    "valor_tir",
)


# ── Alfabetos acotados para forzar colisiones de clave (grupos fungibles con >=2) ──
# Alfabetos pequeños ⇒ alta probabilidad de que varias filas compartan la clave.
_especies = st.sampled_from(["TES", "BONO", "ÑÁND", "yankee"])
_isines = st.sampled_from(["ISINA", "ISINB", "isinc"])
_monedas = st.sampled_from(["COP", "USD", "UVR"])
_periodicidades = st.sampled_from(["ANUAL", "SEMESTRAL", "TRIMESTRAL"])
_tipos_tasa = st.sampled_from(["FIJA", "VARIABLE"])
_tasas_referencia = st.sampled_from(["", "IPC", "IBR"])
_asset_class = st.sampled_from(["", "TF", "LEGATRUU", "UVR_X"])
# Fechas de vencimiento de un conjunto reducido para que coincidan entre filas.
_vencimientos = st.sampled_from(
    [date(2027, 12, 31), date(2030, 6, 15), date(2028, 3, 1)]
)
_emisiones = st.sampled_from([date(2025, 1, 1), date(2026, 7, 1), date(2026, 11, 30)])
# Tasas faciales de un pozo pequeño para colisionar; finitas.
_tasas_facial = st.sampled_from([0.0, 2.5, 6.2, 6.3, 10.75])
# Clasificaciones: Negociable (consolidable) + a TIR (intocable).
_clasificaciones = st.sampled_from(["Negociable", "Vencimiento", "Disp. Venta"])
# Clase origen: RF (TITULO / vacío) + no-RF.
_clases_origen = st.sampled_from(
    ["TITULO", "", "RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"]
)
# Montos finitos (incluye negativos y extremos moderados para ejercitar la suma).
_montos = st.floats(
    min_value=-1e7, max_value=1e9, allow_nan=False, allow_infinity=False
)


@st.composite
def _fila_titulo(draw, idx: int):
    """Genera una fila-título (dict) con las columnas que lee la consolidación."""
    # id_cobertura: mayormente vacío, a veces un subyacente de swap (intocable).
    id_cobertura = draw(st.sampled_from(["", "", "", "SWAP_1", "SWAP_2"]))
    return {
        "id_posicion": f"ID{idx}",
        "especie": draw(_especies),
        "isin": draw(_isines),
        "fecha_vencimiento": draw(_vencimientos),
        "fecha_emision": draw(_emisiones),
        "tasa_facial": draw(_tasas_facial),
        "periodicidad": draw(_periodicidades),
        "tipo_tasa": draw(_tipos_tasa),
        "moneda": draw(_monedas),
        "asset_class_definitivo": draw(_asset_class),
        "tasa_referencia": draw(_tasas_referencia),
        "clasificacion_contable": draw(_clasificaciones),
        "clase_origen": draw(_clases_origen),
        "id_cobertura": id_cobertura,
        "tasa_compra": draw(_montos),
        "nominal": draw(_montos),
        "valor_mercado": draw(_montos),
        "valor_tir": draw(_montos),
    }


@st.composite
def _df_titulos(draw):
    """Genera un DataFrame de 0..12 filas-título con las columnas de la allowlist."""
    n = draw(st.integers(min_value=0, max_value=12))
    filas = [draw(_fila_titulo(i)) for i in range(n)]
    return pd.DataFrame(filas, columns=list(_COLUMNAS_FILA))


def _es_consolidable(fila) -> bool:
    """Replica la partición de la función: RF + Negociable + sin cobertura + clave válida."""
    clase = str(fila.get("clase_origen", "")).strip().upper()
    es_rf = clase == "TITULO" or clase == ""
    es_negociable = (
        str(fila.get("clasificacion_contable", "")).strip().upper() == "NEGOCIABLE"
    )
    tiene_cobertura = str(fila.get("id_cobertura", "")).strip() != ""
    if not (es_rf and es_negociable and not tiene_cobertura):
        return False
    return _clave_fungibilidad(fila) is not None


def _suma_segura(serie) -> float:
    """
    Suma coercionando a numérico (NaN/no numéricos tratados como ausencia). Una serie
    vacía o totalmente no numérica suma 0.0 (neutro aditivo), evitando NaN espurios en la
    comparación de totales.
    """
    total = pd.to_numeric(serie, errors="coerce").sum()  # NaN -> omitidos; vacío -> 0.0
    return 0.0 if pd.isna(total) else float(total)


# ==============================================================================
# Property 1: Conservación de sumas por grupo (nominal, VM, VTIR)
# ==============================================================================

# Feature: optimizacion-agrupacion-negociables, Property 1: Conservación de sumas por
# grupo (nominal, VM, VTIR). Para cualquier conjunto de títulos de cierre de tramo, el
# Titulo_Consolidado que reemplaza a cada Grupo_Fungible tiene nominal, valor_mercado y
# valor_tir iguales a la suma de los respectivos valores de los miembros del grupo
# (dentro de la tolerancia), y los totales globales de nominal, valor_mercado y
# valor_tir del conjunto se conservan tras la consolidación.
# Validates: Requirements 1.2, 1.3, 1.4, 4.4
@settings(
    max_examples=200,
    deadline=None,
    # La construcción de un DataFrame por ejemplo es inherente a probar una función
    # sobre DataFrames; en esta máquina el primer draw puede ser lento, por lo que se
    # suprime el health check de generación (no afecta la validez de la propiedad).
    suppress_health_check=[HealthCheck.too_slow],
)
@given(df=_df_titulos())
def test_prop1_conservacion_sumas_grupo_y_totales(df):
    df_entrada = df.copy(deep=True)
    df_salida, resumen = consolidar_negociables_fungibles(df)

    # (0) La función es pura: no muta la entrada.
    pd.testing.assert_frame_equal(df, df_entrada)

    # ── (A) Conservación de los TOTALES GLOBALES de las columnas sumables ──
    # La suma total de nominal/VM/VTIR debe conservarse tras consolidar (colapsar
    # miembros fungibles en su suma no altera el agregado del conjunto).
    for col in _COLUMNAS_SUMABLES_CONSOLIDADO:
        total_entrada = _suma_segura(df_entrada[col])
        total_salida = _suma_segura(df_salida[col])
        escala = max(1.0, abs(total_entrada))
        assert math.isclose(
            total_entrada, total_salida, rel_tol=1e-9, abs_tol=1e-6 * escala
        ), (
            f"Total global de '{col}' no conservado: "
            f"entrada={total_entrada} salida={total_salida}"
        )

    # ── (B) Conservación POR GRUPO: cada consolidado = suma de sus miembros ──
    # Reconstruimos los grupos fungibles de la entrada igual que la función.
    grupos: dict[tuple, list[int]] = {}
    for pos in range(len(df_entrada)):
        fila = df_entrada.iloc[pos]
        if not _es_consolidable(fila):
            continue
        clave = _clave_fungibilidad(fila)
        grupos.setdefault(clave, []).append(pos)

    grupos_multi = {k: v for k, v in grupos.items() if len(v) >= 2}

    # El resumen debe reportar el nº de grupos consolidados (>=2 miembros).
    assert resumen.n_grupos == len(grupos_multi), (
        f"n_grupos={resumen.n_grupos} != grupos multimiembro={len(grupos_multi)}"
    )

    # id_posicion determinista del consolidado por clave (CONS_<hash>).
    for clave, posiciones in grupos_multi.items():
        miembros = df_entrada.iloc[posiciones]
        id_cons = pt._id_consolidado(clave)
        fila_cons = df_salida[df_salida["id_posicion"] == id_cons]
        assert len(fila_cons) == 1, (
            f"Debe existir exactamente 1 consolidado para la clave {clave}; "
            f"encontrados {len(fila_cons)}"
        )
        fila_cons = fila_cons.iloc[0]
        for col in _COLUMNAS_SUMABLES_CONSOLIDADO:
            suma_miembros = _suma_segura(miembros[col])
            valor_cons = float(pd.to_numeric(fila_cons[col], errors="coerce"))
            escala = max(1.0, abs(suma_miembros))
            assert math.isclose(
                suma_miembros, valor_cons, rel_tol=1e-9, abs_tol=1e-6 * escala
            ), (
                f"Grupo {clave}: '{col}' del consolidado={valor_cons} != "
                f"suma de miembros={suma_miembros}"
            )
