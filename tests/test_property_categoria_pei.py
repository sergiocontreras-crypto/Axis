# -*- coding: utf-8 -*-
"""
Pruebas de la Property 2 (PEI particionado fuera de RV Local) y casos de ejemplo de
`_categoria` / `_mapa_clasificacion`.

Feature / spec: excel-por-tramos-consistencia (Defecto 2). Tarea 2.1 del plan.

Property 2: PEI particionado fuera de RV Local.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

Tag: Feature: excel-por-tramos-consistencia, Property 2: PEI particionado fuera de RV Local.

Enfoque test-first: ANTES del cambio (Tarea 2.2) P2 y los ejemplos PEI deben FALLAR,
porque hoy `_categoria` clasifica la renta variable solo por moneda y todo PEI local cae
en "RV Local". Tras la corrección, el PEI se agrega en su categoría propia "PEI" y
"RV Local" queda únicamente con COLCAP (no-PEI).

La Property 2 construye el resumen por la vía real reconstruida desde BD
(`generar_resumen_por_tramo`), sembrando `portafolio_clasificado` (con
`asset_class_definitivo` para derivar PEI) e `informe_pyg_por_tramo` (payload con la lista
`rv` por corte). PEI se identifica SOLO por el dato `asset_class_definitivo='PEI'`, nunca
por un literal de especie embebido (Req 5.7).
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# El módulo vive en tools/ (no en scripts/); añadirlo al path para importarlo.
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import export_resumen_tramos as ert  # noqa: E402


# ==============================================================================
# UTILIDADES: siembra de una BD por tramos sintética (solo RV: PEI / COLCAP)
# ==============================================================================

def _sembrar_bd_rv(ruta_db, especies_pei, especies_colcap):
    """Siembra `portafolio_clasificado` + `informe_pyg_por_tramo` con posiciones RV.

    Cada especie aparece UNA sola vez en el base (prorrateo 1:1 en `_resumen_no_rf`), con
    `clase_origen='RENTA_VARIABLE'`, moneda local 'COP', ramo/compania fijos. El
    `asset_class_definitivo` marca PEI vs COLCAP. El payload por corte lleva la lista `rv`
    con el P&G por especie.

    Devuelve el dict {especie: pyg} usado para construir el payload, para que la prueba
    pueda verificar sumas por categoría.
    """
    conn = sqlite3.connect(str(ruta_db))
    try:
        filas = []
        pyg_por_especie = {}
        for i, esp in enumerate(especies_pei):
            filas.append({
                "id_posicion": f"PEI_{i}", "especie": esp, "ramo": "R1",
                "compania": "C1", "moneda": "COP", "clase_origen": "RENTA_VARIABLE",
                "asset_class_definitivo": "PEI", "id_cobertura": "",
                "valor_mercado": 1000.0,
            })
            pyg_por_especie[esp] = 100.0 + i
        for i, esp in enumerate(especies_colcap):
            filas.append({
                "id_posicion": f"COLCAP_{i}", "especie": esp, "ramo": "R1",
                "compania": "C1", "moneda": "COP", "clase_origen": "RENTA_VARIABLE",
                "asset_class_definitivo": "COLCAP", "id_cobertura": "",
                "valor_mercado": 2000.0,
            })
            pyg_por_especie[esp] = 50.0 + i
        df = pd.DataFrame(filas)
        df.to_sql("portafolio_clasificado", conn, if_exists="replace", index=False)

        # Payload por corte: lista `rv` con una fila por especie (último corte).
        rv = []
        for esp, pyg in pyg_por_especie.items():
            rv.append({
                "titulo": esp, "moneda": "COP",
                "fecha_proyeccion": "2026-12-31",
                "pyg_acumulado": pyg,
                "valor_mercado": 0.0,
            })
        payload = {
            "rf_detalle": [], "rv": rv, "alt": [],
            "deriv_pyg": [], "deriv_vm_cierre": {},
            "trm_inicio": 3900.0, "trm_cierre": 3900.0,
        }
        conn.execute(
            "CREATE TABLE informe_pyg_por_tramo (fecha_corte TEXT, payload_json TEXT)")
        conn.execute(
            "INSERT INTO informe_pyg_por_tramo (fecha_corte, payload_json) VALUES (?, ?)",
            ("2026-12-31", json.dumps(payload)),
        )
        conn.commit()
        return pyg_por_especie
    finally:
        conn.close()


def _totales_por_categoria(df_resumen):
    """Agrega el P&G por categoría del resumen de un corte (groupby, como TotalCategoria)."""
    if df_resumen is None or df_resumen.empty:
        return {}
    g = df_resumen.groupby("categoria")["pyg"].sum()
    return {str(k): float(v) for k, v in g.items()}


# ==============================================================================
# Property 2 — PEI particionado fuera de RV Local
# ==============================================================================

# Especies no vacías y únicas; se evita el literal 'PEI'/'COLCAP' como nombre de especie
# para demostrar que la clasificación NO depende del nombre, solo de asset_class_definitivo.
_ESPECIE = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z
    min_size=3, max_size=8,
)


@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
@given(
    especies=st.lists(_ESPECIE, min_size=2, max_size=10, unique=True),
    n_pei=st.integers(min_value=1, max_value=9),
)
def test_prop2_pei_particionado_fuera_rv_local(tmp_path_factory, especies, n_pei):
    """
    Feature: excel-por-tramos-consistencia, Property 2: PEI particionado fuera de RV Local.

    Para toda colección de posiciones RV locales mezclando PEI/no-PEI:
      - ninguna posición con asset_class_definitivo='PEI' se agrega en "RV Local";
      - total "RV Local" == Σ P&G de RV locales no-PEI (COLCAP);
      - total "PEI" == Σ P&G de posiciones PEI.

    EXPECTED OUTCOME (código sin corregir): FALLA (el PEI cae hoy en "RV Local").
    """
    n_pei = min(n_pei, len(especies) - 1)  # al menos una COLCAP
    especies_pei = especies[:n_pei]
    especies_colcap = especies[n_pei:]

    ruta = tmp_path_factory.mktemp("prop2") / "tramos_pei.db"
    pyg_por_especie = _sembrar_bd_rv(ruta, especies_pei, especies_colcap)

    salida = ert.generar_resumen_por_tramo(str(ruta))
    assert salida, "la reconstrucción por tramos no debe estar vacía"
    df = salida["2026-12-31"]
    tot = _totales_por_categoria(df)

    suma_pei = sum(pyg_por_especie[e] for e in especies_pei)
    suma_colcap = sum(pyg_por_especie[e] for e in especies_colcap)

    # (1) Ninguna posición PEI en "RV Local": RV Local == solo COLCAP.
    assert math.isclose(tot.get("RV Local", 0.0), suma_colcap, rel_tol=1e-9, abs_tol=1e-6), (
        f"'RV Local' debe ser solo COLCAP ({suma_colcap}); obtenido {tot.get('RV Local')}"
    )
    # (2) Total "PEI" == Σ P&G de posiciones PEI.
    assert math.isclose(tot.get("PEI", 0.0), suma_pei, rel_tol=1e-9, abs_tol=1e-6), (
        f"'PEI' debe sumar {suma_pei}; obtenido {tot.get('PEI')}"
    )
    # (3) La categoría "PEI" aparece cuando hay posiciones PEI.
    assert "PEI" in tot, "la categoría 'PEI' debe existir en cortes con posiciones PEI"


# ==============================================================================
# Ejemplos (unit) — _categoria y _mapa_clasificacion
# ==============================================================================

def test_categoria_pei_por_flag_es_pei():
    """RV con es_pei=True -> 'PEI'. EXPECTED (sin corregir): FALLA (hoy 'RV Local')."""
    assert ert._categoria("RENTA_VARIABLE", "COP", "PEI",
                          es_subyacente=False, es_pei=True) == "PEI"


def test_categoria_pei_por_asset_class():
    """RV con asset_class='PEI' -> 'PEI'. EXPECTED (sin corregir): FALLA."""
    assert ert._categoria("RENTA_VARIABLE", "COP", "PEI",
                          es_subyacente=False) == "PEI"


def test_categoria_rv_local_no_pei():
    """RV local no-PEI (COLCAP) -> 'RV Local'."""
    assert ert._categoria("RENTA_VARIABLE", "COP", "COLCAP",
                          es_subyacente=False, es_pei=False) == "RV Local"


def test_categoria_rv_internacional_no_pei():
    """RV internacional no-PEI -> 'RV Internacional'."""
    assert ert._categoria("RENTA_VARIABLE", "USD", "ETF",
                          es_subyacente=False, es_pei=False) == "RV Internacional"


def test_categoria_subyacente_swaps():
    """RF con es_subyacente=True -> 'Subyacentes de Swaps'."""
    assert ert._categoria("TITULO", "USD", "DP LP",
                          es_subyacente=True) == "Subyacentes de Swaps"


def test_mapa_clasificacion_deriva_es_pei(tmp_path):
    """`_mapa_clasificacion` deriva la columna booleana `es_pei` desde
    `asset_class_definitivo` (EXPECTED sin corregir: FALLA, columna ausente)."""
    ruta = tmp_path / "mapa.db"
    conn = sqlite3.connect(str(ruta))
    try:
        df = pd.DataFrame({
            "id_posicion": ["P1", "P2"],
            "especie": ["E1", "E2"],
            "ramo": ["R1", "R1"],
            "compania": ["C1", "C1"],
            "moneda": ["COP", "COP"],
            "clase_origen": ["RENTA_VARIABLE", "RENTA_VARIABLE"],
            "asset_class_definitivo": ["PEI", "COLCAP"],
            "id_cobertura": ["", ""],
        })
        df.to_sql("portafolio_clasificado", conn, if_exists="replace", index=False)
        conn.commit()
    finally:
        conn.close()

    conn_ro = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        mapa = ert._mapa_clasificacion(conn_ro)
    finally:
        conn_ro.close()

    assert "es_pei" in mapa.columns, "`_mapa_clasificacion` debe derivar la columna 'es_pei'"
    serie = mapa.set_index("id_posicion")["es_pei"]
    assert bool(serie.loc["P1"]) is True
    assert bool(serie.loc["P2"]) is False
