"""
Property test de la feature `optimizacion-agrupacion-negociables` (tarea 2.7).

Property 6: Grupos unitarios inalterados.

    Para cualquier `Titulo_Negociable` cuya `Clave_Fungibilidad` no comparte ningún
    otro Negociable del conjunto, la consolidación deja ese título sin cambios.

_Validates: Requirements 1.6_

Enfoque: se generan filas Negociables (RF, sin cobertura) cuyas claves de
fungibilidad son TODAS distintas por construcción (cada fila lleva un `isin`
único, y el `isin` forma parte de la `Clave_Fungibilidad`). Como ningún
Negociable comparte clave con otro, cada uno es un grupo unitario y
`consolidar_negociables_fungibles` debe devolverlo verbatim: mismo `id_posicion`,
`nominal`, `valor_mercado` y `valor_tir`. Además el resumen no reporta grupos
colapsados.
"""

import math

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from proyeccion_tramos import consolidar_negociables_fungibles


# ── Columnas relevantes para la consolidación de un Negociable RF ────────────
_COLUMNAS = [
    "id_posicion",
    "especie",
    "isin",
    "moneda",
    "fecha_vencimiento",
    "tasa_facial",
    "periodicidad",
    "tipo_tasa",
    "asset_class_definitivo",
    "tasa_referencia",
    "clasificacion_contable",
    "clase_origen",
    "id_cobertura",
    "nominal",
    "valor_mercado",
    "valor_tir",
    "tasa_compra",
]


# ── Estrategias acotadas al espacio de entrada de un Negociable RF ───────────
# Se usan alfabetos pequeños vía sampled_from para que la generación sea rápida
# (la normalización de texto ya se cubre en los tests unitarios de la tarea 1.3).
_texto = st.sampled_from(["tes", " TES ", "BONO", "bono a", " cdt "])
_fechas = st.dates(
    min_value=pd.Timestamp("2026-01-01").date(),
    max_value=pd.Timestamp("2045-12-31").date(),
).map(lambda d: d.isoformat())
_tasas = st.floats(min_value=0.0, max_value=40.0, allow_nan=False, allow_infinity=False)
_montos = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)
_periodicidades = st.sampled_from(["ANUAL", "SEMESTRAL", "TRIMESTRAL"])
_tipos_tasa = st.sampled_from(["FIJA", "VARIABLE", "EFECTIVO"])
_monedas = st.sampled_from(["COP", "UVR", "USD"])


# Atributos "variables" de una fila (sin isin/id_posicion, que se fijan únicos).
# Un único st.fixed_dictionaries evita componer varios draws por fila.
_atributos_fila = st.fixed_dictionaries(
    {
        "especie": _texto,
        "moneda": _monedas,
        "fecha_vencimiento": _fechas,
        "tasa_facial": _tasas,
        "periodicidad": _periodicidades,
        "tipo_tasa": _tipos_tasa,
        "asset_class_definitivo": st.sampled_from(["TF", "Deuda", "TES UVR"]),
        "tasa_referencia": st.sampled_from(["", "IPC", "IBR"]),
        "clase_origen": st.sampled_from(["TITULO", ""]),
        "nominal": _montos,
        "valor_mercado": _montos,
        "valor_tir": _montos,
        "tasa_compra": _tasas,
    }
)


def _conjunto_singletons():
    """
    Estrategia de conjuntos de 1..8 filas Negociables con `isin` ÚNICOS entre sí,
    de modo que cada fila sea un grupo unitario (ningún Negociable comparte clave).
    """

    def _completar(lista_attrs):
        filas = []
        for i, attrs in enumerate(lista_attrs):
            fila = dict(attrs)
            fila["id_posicion"] = f"NEG{i:03d}"
            fila["isin"] = f"ISIN{i:03d}"  # único por índice ⇒ claves disjuntas
            fila["clasificacion_contable"] = "Negociable"
            fila["id_cobertura"] = ""  # sin cobertura ⇒ consolidable
            filas.append(fila)
        return filas

    return st.lists(_atributos_fila, min_size=1, max_size=8).map(_completar)


def _casi_igual(a, b) -> bool:
    """Igualdad numérica robusta a NaN y ruido de float."""
    fa = float(a)
    fb = float(b)
    if math.isnan(fa) and math.isnan(fb):
        return True
    return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-9)


# Feature: optimizacion-agrupacion-negociables, Property 6: Grupos unitarios inalterados
# Nota: se suprime HealthCheck.too_slow porque la generación de Hypothesis es lenta por
# el I/O del sistema de archivos (el proyecto vive en una ruta de Google Drive), no por la
# estrategia; esto es irrelevante para la propiedad verificada.
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(filas=_conjunto_singletons())
def test_prop6_grupos_unitarios_inalterados(filas):
    """
    Cada Negociable con clave única (grupo unitario) sobrevive la consolidación sin
    cambios: mismo id_posicion, nominal, valor_mercado y valor_tir. No se colapsa nada.
    """
    df_in = pd.DataFrame(filas, columns=_COLUMNAS)
    df_out, resumen = consolidar_negociables_fungibles(df_in)

    # Ningún grupo tiene >= 2 miembros ⇒ no se colapsa ninguna fila.
    assert resumen.n_grupos == 0
    assert resumen.n_colapsadas == 0
    assert resumen.n_salida == resumen.n_entrada == len(filas)

    # Cada id_posicion de entrada aparece verbatim (mismo id, sin ids sintéticos CONS_*).
    out_por_id = {row["id_posicion"]: row for _, row in df_out.iterrows()}
    assert set(out_por_id.keys()) == {f["id_posicion"] for f in filas}

    for original in filas:
        salida = out_por_id[original["id_posicion"]]
        # id_posicion conservado (no se reemplaza por CONS_<hash>).
        assert salida["id_posicion"] == original["id_posicion"]
        # Campos numéricos sumables inalterados (suma de un único miembro = él mismo).
        assert _casi_igual(salida["nominal"], original["nominal"])
        assert _casi_igual(salida["valor_mercado"], original["valor_mercado"])
        assert _casi_igual(salida["valor_tir"], original["valor_tir"])
