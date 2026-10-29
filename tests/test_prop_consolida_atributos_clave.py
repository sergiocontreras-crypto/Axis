"""
Property test de la feature `optimizacion-agrupacion-negociables`.

Cubre la Property 2 del diseño sobre la función pura
`consolidar_negociables_fungibles` de `scripts/proyeccion_tramos.py`:

    Para cualquier Grupo_Fungible con >= 2 miembros, el Titulo_Consolidado conserva
    idénticos todos los atributos de la Clave_Fungibilidad (especie, isin, vencimiento,
    tasa facial, periodicidad, tipo de tasa, moneda, tipo de bono) de los miembros, y su
    clasificacion_contable es Negociable.

Se generan DataFrames de filas tipo snapshot con Hypothesis (alfabetos acotados, fechas
válidas, tasas finitas, clasificaciones y clases de origen del dominio real) garantizando
que al menos un Grupo_Fungible tenga >= 2 miembros, para ejercitar el colapso.

Property test con Hypothesis (>= 100 iteraciones), etiquetado con el texto de la Property.
"""

from datetime import date

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from proyeccion_tramos import (
    consolidar_negociables_fungibles,
    _clave_fungibilidad,
)


# ── Alfabetos acotados para la Clave_Fungibilidad ──
# Deliberadamente pequeños para que distintas filas colisionen en la misma clave y se
# formen grupos de >= 2 miembros. Incluyen minúsculas/espacios y un carácter no-ASCII
# para ejercitar la normalización (str.strip().upper()).
_especies = st.sampled_from(["TFIT", "tfit ", " Tfit", "COLTES", "ñ-bono"])
_isines = st.sampled_from(["CO1", "co1 ", "US2", " us2"])
_vencimientos = st.sampled_from(
    [date(2030, 1, 1), date(2032, 6, 30), date(2035, 12, 31)]
)
# Tasas con pocos valores + ruido de punto flotante para verificar que agrupan.
_tasas = st.sampled_from([7.0, 7.0 + 1e-13, 8.25, 10.5])
_periodicidades = st.sampled_from(["ANUAL", "anual ", "SEMESTRAL"])
_tipos_tasa = st.sampled_from(["EFECTIVO", "efectivo ", "NOMINAL"])
# Monedas y asset_class limitados para derivar tipo_bono (UVR/IPC/YANKEE/TF).
_monedas = st.sampled_from(["COP", "cop ", "USD", "UVR"])
_asset_classes = st.sampled_from(["DP LP", "DP_TF", "LEGATRUU", "UVR LP"])
_tasas_referencia = st.sampled_from(["", "IPC", "ipc "])

# Clasificación contable y clase_origen del dominio real del snapshot.
_clasificaciones = st.sampled_from(["Negociable", "Vencimiento", "Disp. Venta"])
_clases_origen = st.sampled_from(
    ["TITULO", "RENTA_VARIABLE", "ALTERNATIVO", "DERIVADO"]
)
# id_cobertura opcional (subyacente de swap si no está vacío).
_id_cobertura = st.sampled_from(["", "SWAP_1"])

# Valores sumables finitos.
_nominales = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)
_vm = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
_vtir = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
_tasas_compra = st.floats(
    min_value=0.0, max_value=40.0, allow_nan=False, allow_infinity=False
)
_fechas_emision = st.sampled_from(
    [date(2024, 1, 1), date(2025, 6, 1), date(2026, 3, 15)]
)


# ── Columnas de la Clave_Fungibilidad y campos no-clave ──
# Estrategia de "campos de clave" como una sola tupla (un draw) para ser eficiente.
_clave_fields = st.tuples(
    _especies, _isines, _vencimientos, _tasas, _periodicidades,
    _tipos_tasa, _monedas, _asset_classes, _tasas_referencia,
)
# Estrategia de "campos variables" (no entran en la clave) como una sola tupla.
_var_fields = st.tuples(_fechas_emision, _nominales, _vm, _vtir, _tasas_compra)


def _fila_desde_tuplas(idx, clave_t, var_t, clasif, clase_origen, id_cob):
    """Construye un dict-fila desde tuplas ya generadas (sin draws internos)."""
    especie, isin, venc, tasa, period, tipo_tasa, moneda, asset_class, tasa_ref = clave_t
    fecha_emision, nominal, vm, vtir, tasa_compra = var_t
    return {
        "id_posicion": idx,
        "especie": especie,
        "isin": isin,
        "fecha_vencimiento": venc,
        "tasa_facial": tasa,
        "periodicidad": period,
        "tipo_tasa": tipo_tasa,
        "moneda": moneda,
        "asset_class_definitivo": asset_class,
        "tasa_referencia": tasa_ref,
        "clasificacion_contable": clasif,
        "clase_origen": clase_origen,
        "id_cobertura": id_cob,
        "fecha_emision": fecha_emision,
        "nominal": nominal,
        "valor_mercado": vm,
        "valor_tir": vtir,
        "tasa_compra": tasa_compra,
    }


# Fila de ruido: una sola tupla con clave + variables + clasificación/origen/cobertura.
_fila_ruido_tuple = st.tuples(
    _clave_fields, _var_fields, _clasificaciones, _clases_origen, _id_cobertura
)


@st.composite
def _df_con_grupo_fungible(draw):
    """
    Genera un DataFrame garantizando >= 1 Grupo_Fungible con >= 2 miembros:
    inserta explícitamente 2+ filas Negociables RF (sin cobertura) que comparten
    exactamente los campos de la Clave_Fungibilidad, más filas aleatorias de ruido.

    Diseño eficiente: pocas llamadas a `draw()` (una por lista/tupla), evitando el
    HealthCheck de generación lenta de Hypothesis.
    """
    # Clave compartida fija para el grupo garantizado (un solo draw).
    clave_grupo = draw(_clave_fields)
    # Campos variables de cada miembro del grupo (una lista de tuplas, un draw).
    vars_grupo = draw(st.lists(_var_fields, min_size=2, max_size=5))
    # Filas de ruido como lista de tuplas (un draw).
    ruido = draw(st.lists(_fila_ruido_tuple, min_size=0, max_size=8))

    filas = []
    for i, var_t in enumerate(vars_grupo):
        filas.append(
            _fila_desde_tuplas(f"G{i}", clave_grupo, var_t, "Negociable", "TITULO", "")
        )
    for j, (clave_t, var_t, clasif, clase_origen, id_cob) in enumerate(ruido):
        filas.append(
            _fila_desde_tuplas(f"T{j}", clave_t, var_t, clasif, clase_origen, id_cob)
        )

    return pd.DataFrame(filas)


# ==============================================================================
# Property 2: Atributos de la clave preservados e invariantes del grupo
# ==============================================================================

# Feature: optimizacion-agrupacion-negociables, Property 2: Atributos de la clave
# preservados e invariantes del grupo
# Validates: Requirements 1.5
@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(df=_df_con_grupo_fungible())
def test_prop2_atributos_clave_preservados_invariantes_grupo(df):
    # Clave de cada fila CONSOLIDABLE de ENTRADA (Negociable, RF, sin cobertura).
    conteo_por_clave: dict[tuple, int] = {}
    for pos in range(len(df)):
        fila = df.iloc[pos]
        clase = str(fila.get("clase_origen", "")).strip().upper()
        es_rf = clase in ("TITULO", "")
        es_neg = str(fila.get("clasificacion_contable", "")).strip().upper() == "NEGOCIABLE"
        sin_cob = str(fila.get("id_cobertura", "")).strip() == ""
        if es_rf and es_neg and sin_cob:
            clave = _clave_fungibilidad(fila)
            if clave is not None:
                conteo_por_clave[clave] = conteo_por_clave.get(clave, 0) + 1

    # Claves que forman Grupo_Fungible (>= 2 miembros) → deben colapsar.
    claves_grupo = {k for k, n in conteo_por_clave.items() if n >= 2}
    # Precondición del generador: debe existir al menos un grupo fungible.
    assert claves_grupo, "el generador debe producir al menos un grupo fungible"

    df_cons, resumen = consolidar_negociables_fungibles(df)

    # Para cada fila consolidada (id sintético CONS_...), su clave reconstruida debe
    # coincidir EXACTAMENTE con la clave compartida por los miembros del grupo, y su
    # clasificación debe ser Negociable (Req 1.5).
    filas_cons = [
        df_cons.iloc[i]
        for i in range(len(df_cons))
        if str(df_cons.iloc[i].get("id_posicion", "")).startswith("CONS_")
    ]
    # Debe haber exactamente un consolidado por clave de grupo.
    assert len(filas_cons) == len(claves_grupo)

    claves_salida = set()
    for fila in filas_cons:
        # clasificacion_contable invariante del grupo (Req 1.5).
        assert str(fila["clasificacion_contable"]) == "Negociable"
        # La clave reconstruida del consolidado debe pertenecer a las claves de grupo
        # (conserva idénticos todos los atributos de la Clave_Fungibilidad).
        clave_cons = _clave_fungibilidad(fila)
        assert clave_cons is not None
        assert clave_cons in claves_grupo, (
            "el consolidado debe conservar idénticos los atributos de la clave del grupo"
        )
        claves_salida.add(clave_cons)

    # Cada clave de grupo quedó representada por su consolidado (biyección).
    assert claves_salida == claves_grupo
    # El resumen reporta tantos grupos como claves fungibles con >= 2 miembros.
    assert resumen.n_grupos == len(claves_grupo)
