# -*- coding: utf-8 -*-
"""
Test del resumen por corte — Tarea 10 / Defecto 5 (Fase 2).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).

Property 6 (Expected Behavior): el resumen por corte SHALL NO emitir la advertencia estática
del "bug VM-USD" para "RF Internacional"/"Subyacentes de Swaps". El Defecto 2 fue CERRADO como
NO REPRODUCIBLE (ver docs/deuda-tecnica.md sección RESUELTO y Tarea 5.2), por lo que esa
advertencia es un residuo engañoso que debe retirarse.

**Validates: Requirements 5.1**

Enfoque (robustez): se ejercita la RUTA REAL de
`tools/export_resumen_tramos.py::generar_resumen_por_tramo` sobre una DB SQLite TEMPORAL con
las tablas `portafolio_clasificado` (fuente de clasificación) e `informe_pyg_por_tramo`
(payload con `rf_detalle`) sembradas con una fila de RF Internacional (USD) y una de
Subyacente de Swap. Se asevera que NINGUNA fila del resumen lleva el literal de advertencia
del bug VM-USD, ni existe una columna 'advertencia' con ese texto.

Se eligió llamar a la función real (en lugar de solo inspeccionar el código fuente) porque es
más robusto: valida el DataFrame efectivamente producido para las categorías en cuestión y
seguirá protegiendo contra una reintroducción accidental de la advertencia en cualquier forma.
No toca la BD real ni el motor; usa el patrón de siembra de los tests existentes
(test_export_rf_mensual_tramos.py).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

# El módulo vive en tools/ (no en scripts/); añadirlo al path para importarlo.
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import export_resumen_tramos as ert  # noqa: E402


# Literal estático del bug VM-USD que debe desaparecer del resumen (Defecto 5).
_LITERAL_VM_USD = "REVISAR: P&G afectado por bug VM-USD del motor"


def _fila_rf_detalle(id_pos: str, especie: str, moneda: str, es_subyacente: bool) -> dict:
    """Fila por título con el shape de tools/detalle_pyg_tramo.construir_rf_detalle."""
    return {
        "id_posicion": id_pos,
        "especie": especie,
        "compania": "CIA_X",
        "ramo": "RAMO_1",
        "asset_class": "RENTA_FIJA",
        "clasificacion": "Negociable",
        "moneda": moneda,
        "es_subyacente": es_subyacente,
        "vm_inicial": 1_000_000.0,
        "vm_mercado": 1_100_000.0,
        "vm_revaluado_usd": False,
        "pyg": 100_000.0,
        "ori": 0.0,
        "total_resultado": 100_000.0,
    }


def _sembrar_db(ruta_db: Path) -> None:
    """Crea portafolio_clasificado + informe_pyg_por_tramo con un corte que incluye
    RF Internacional (USD) y un Subyacente de Swap en rf_detalle."""
    conn = sqlite3.connect(str(ruta_db))
    try:
        # Fuente de clasificación (consumida por _mapa_clasificacion).
        conn.execute(
            "CREATE TABLE portafolio_clasificado ("
            "id_posicion TEXT, especie TEXT, ramo TEXT, compania TEXT, moneda TEXT, "
            "clase_origen TEXT, asset_class_definitivo TEXT, id_cobertura TEXT)"
        )
        conn.executemany(
            "INSERT INTO portafolio_clasificado "
            "(id_posicion, especie, ramo, compania, moneda, clase_origen, "
            "asset_class_definitivo, id_cobertura) VALUES (?,?,?,?,?,?,?,?)",
            [
                ("817856", "BN ECOPETROL USD", "RAMO_1", "CIA_X", "USD", "TITULO",
                 "RENTA_FIJA", ""),
                ("900001", "SUBYACENTE SWAP USD", "RAMO_1", "CIA_X", "USD", "TITULO",
                 "RENTA_FIJA", "COB_1"),
            ],
        )

        # Payload por corte con rf_detalle: una RF Internacional y un Subyacente de Swap.
        conn.execute(
            "CREATE TABLE informe_pyg_por_tramo ("
            "fecha_corte TEXT PRIMARY KEY, indice INTEGER, payload_json TEXT)"
        )
        payload = {
            "rf_detalle": [
                _fila_rf_detalle("817856", "BN ECOPETROL USD", "USD", False),
                _fila_rf_detalle("900001", "SUBYACENTE SWAP USD", "USD", True),
            ],
        }
        conn.execute(
            "INSERT INTO informe_pyg_por_tramo (fecha_corte, indice, payload_json) "
            "VALUES (?,?,?)",
            ("2026-12-31", 0, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_resumen(tmp_path):
    ruta = tmp_path / "resumen_tramos.db"
    _sembrar_db(ruta)
    return ruta


def test_resumen_no_lleva_advertencia_vm_usd(db_resumen):
    """
    Req 5.1: ninguna fila del resumen por corte lleva el literal de advertencia del bug
    VM-USD, y no existe una columna 'advertencia' con ese texto, ni siquiera para las
    categorías "RF Internacional"/"Subyacentes de Swaps".
    """
    resumen = ert.generar_resumen_por_tramo(str(db_resumen))

    assert resumen, "el resumen por corte no debería estar vacío con rf_detalle sembrado"

    for fecha_corte, df in resumen.items():
        assert isinstance(df, pd.DataFrame) and not df.empty

        # Las categorías objetivo del Defecto 5 deben estar presentes en los datos sembrados.
        cats = set(df["categoria"].astype(str))
        assert "RF Internacional" in cats
        assert "Subyacentes de Swaps" in cats

        # (Req 5.1) Si existe la columna 'advertencia', NINGUNA fila puede llevar el literal
        # del bug VM-USD. Lo robusto es que no quede rastro del literal en ninguna columna.
        if "advertencia" in df.columns:
            textos = df["advertencia"].astype(str).tolist()
            assert all(_LITERAL_VM_USD not in t for t in textos), (
                f"corte {fecha_corte}: la advertencia estática del bug VM-USD sigue presente")

        # Barrido defensivo: el literal no debe aparecer en NINGUNA celda de texto.
        for col in df.columns:
            celdas = df[col].astype(str).tolist()
            assert all(_LITERAL_VM_USD not in c for c in celdas), (
                f"corte {fecha_corte}, col {col}: literal del bug VM-USD presente")
