# -*- coding: utf-8 -*-
"""
Test del exportador por tramos — Tarea 4.4 (hoja RF_Mensual_Tramos).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Property 1 (Expected Behavior): el Excel por tramos SHALL contener la hoja
`RF_Mensual_Tramos` con Σ filas = Σ meses de todos los cortes, reconstruida desde la clave
`rf_mensual_contable` que el orquestador persiste por corte en `informe_pyg_por_tramo`
(Tarea 4.3), ANÁLOGA a `RV_Mensual_Tramos`.

**Validates: Requirements 2.1, 2.6**

Preservación (Req 3.2): agregar la hoja RF NO altera las hojas RV/Alt/Derivados.

Enfoque: se ejercita la RUTA REAL de `tools/exportar_excel_tramos.py::
_anexar_series_mensuales_tramos` sobre una DB SQLite TEMPORAL con la tabla
`informe_pyg_por_tramo` sembrada con dos cortes (payload JSON con rf/rv/alt/deriv),
escribiendo a un `pd.ExcelWriter` real (openpyxl). No toca la BD real ni el motor.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

# El exportador vive en tools/ (no en scripts/); añadirlo al path para importarlo.
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import exportar_excel_tramos as eet  # noqa: E402


def _sembrar_informe_pyg_por_tramo(ruta_db: Path, cortes: list) -> None:
    """Crea `informe_pyg_por_tramo` y persiste un payload JSON por corte.

    Cada entrada de `cortes` es (fecha_corte, indice, payload_dict). El payload replica el
    shape que el orquestador por tramos persiste: claves `rf_mensual_contable`, `rv`, `alt`,
    `deriv_pyg`.
    """
    conn = sqlite3.connect(str(ruta_db))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS informe_pyg_por_tramo ("
            "fecha_corte TEXT PRIMARY KEY, indice INTEGER, payload_json TEXT)"
        )
        for fecha_corte, indice, payload in cortes:
            conn.execute(
                "INSERT OR REPLACE INTO informe_pyg_por_tramo "
                "(fecha_corte, indice, payload_json) VALUES (?, ?, ?)",
                (fecha_corte, indice, json.dumps(payload)),
            )
        conn.commit()
    finally:
        conn.close()


def _fila_rf(fecha: str, pyg: float) -> dict:
    """Fila mensual de RF con el shape de rf_contable_mensual.construir_rf_mensual_contable."""
    return {
        "mes": int(fecha[5:7]),
        "fecha": fecha,
        "pyg_mes": pyg,
        "ori_mes": 0.0,
        "total_resultado_mes": pyg,
        "cupones_mes": 0.0,
        "vm_total": 1000.0,
        "vtir_total": 1000.0,
        "n_titulos": 3,
    }


@pytest.fixture
def db_informe(tmp_path):
    """DB temporal con `informe_pyg_por_tramo` sembrada: 2 cortes con RF (12 + 6 meses)."""
    ruta = tmp_path / "informe_tramos.db"
    corte_a_rf = [_fila_rf(f"2026-{m:02d}", float(m)) for m in range(1, 13)]  # 12 meses
    corte_b_rf = [_fila_rf(f"2027-{m:02d}", float(10 + m)) for m in range(1, 7)]  # 6 meses
    cortes = [
        ("2026-12-31", 0, {
            "rf_mensual_contable": corte_a_rf,
            "rv": [{"fecha_proyeccion": "2026-07", "pyg": 5.0},
                   {"fecha_proyeccion": "2026-08", "pyg": 6.0}],
            "alt": [{"fecha_proyeccion": "2026-07", "pyg": 1.0}],
            "deriv_pyg": [{"fecha_valoracion": "2026-07", "pyg": -2.0}],
        }),
        ("2027-12-31", 1, {
            "rf_mensual_contable": corte_b_rf,
            "rv": [{"fecha_proyeccion": "2027-01", "pyg": 7.0}],
            "alt": [{"fecha_proyeccion": "2027-01", "pyg": 2.0}],
            "deriv_pyg": [{"fecha_valoracion": "2027-01", "pyg": -3.0}],
        }),
    ]
    _sembrar_informe_pyg_por_tramo(ruta, cortes)
    return ruta, corte_a_rf, corte_b_rf


def _escribir_y_leer(ruta_db: Path, out_xlsx: Path):
    """Ejecuta el exportador sobre un ExcelWriter real y devuelve (escritas, hojas_dict)."""
    with pd.ExcelWriter(str(out_xlsx), engine="openpyxl") as writer:
        escritas = eet._anexar_series_mensuales_tramos(writer, str(ruta_db))
    hojas = pd.read_excel(str(out_xlsx), sheet_name=None)
    return escritas, hojas


def test_rf_mensual_tramos_existe_y_suma_filas(db_informe, tmp_path):
    """
    Req 2.1/2.6: la hoja `RF_Mensual_Tramos` existe y Σ filas = Σ meses de todos los cortes
    (12 + 6 = 18), reconstruida desde `rf_mensual_contable` de ambos cortes.
    """
    ruta_db, corte_a_rf, corte_b_rf = db_informe
    escritas, hojas = _escribir_y_leer(ruta_db, tmp_path / "salida.xlsx")

    # (Req 2.1) La hoja RF nueva existe, análoga a RV_Mensual_Tramos.
    assert "RF_Mensual_Tramos" in hojas, "falta la hoja RF_Mensual_Tramos"
    assert "RF_Mensual_Tramos" in escritas

    # (Req 2.6) Σ filas = Σ meses de todos los cortes.
    total_meses = len(corte_a_rf) + len(corte_b_rf)
    assert escritas["RF_Mensual_Tramos"] == total_meses
    assert len(hojas["RF_Mensual_Tramos"]) == total_meses

    # Serie continua y ordenada cronológicamente por 'fecha' (ene-2026 .. jun-2027),
    # con el enero de arranque de cada tramo presente (sin desplazamiento +1 mes).
    fechas = [str(f)[:7] for f in hojas["RF_Mensual_Tramos"]["fecha"].tolist()]
    esperado = [f"2026-{m:02d}" for m in range(1, 13)] + [f"2027-{m:02d}" for m in range(1, 7)]
    assert fechas == esperado
    assert "2026-01" in fechas and "2027-01" in fechas


def test_rf_mensual_tramos_no_altera_rv_alt_deriv(db_informe, tmp_path):
    """
    Req 3.2: agregar la hoja RF NO altera las hojas RV/Alt/Derivados por tramos. Se compara
    el contenido de esas tres hojas contra la agregación esperada de los payloads sembrados.
    """
    ruta_db, _a, _b = db_informe
    _escritas, hojas = _escribir_y_leer(ruta_db, tmp_path / "salida2.xlsx")

    # Las hojas RV/Alt/Derivados siguen existiendo y con el contenido concatenado esperado.
    assert {"RV_Mensual_Tramos", "Alt_Mensual_Tramos", "Derivados_PyG_Tramos"}.issubset(hojas)

    rv = hojas["RV_Mensual_Tramos"]
    assert len(rv) == 3  # 2 del corte A + 1 del corte B
    assert [str(f)[:7] for f in rv["fecha_proyeccion"].tolist()] == ["2026-07", "2026-08", "2027-01"]
    assert rv["pyg"].tolist() == [5.0, 6.0, 7.0]

    alt = hojas["Alt_Mensual_Tramos"]
    assert alt["pyg"].tolist() == [1.0, 2.0]

    deriv = hojas["Derivados_PyG_Tramos"]
    assert deriv["pyg"].tolist() == [-2.0, -3.0]


def test_rf_mensual_tramos_ausente_sin_tabla(tmp_path):
    """
    Falla-segura: si no existe `informe_pyg_por_tramo`, el exportador devuelve {} sin romper
    y no escribe la hoja RF (no interfiere con el patrón RO existente).
    """
    ruta_db = tmp_path / "vacia.db"
    sqlite3.connect(str(ruta_db)).close()  # DB sin la tabla
    with pd.ExcelWriter(str(tmp_path / "vacia.xlsx"), engine="openpyxl") as writer:
        # openpyxl exige al menos una hoja: se agrega una placeholder.
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="placeholder", index=False)
        escritas = eet._anexar_series_mensuales_tramos(writer, str(ruta_db))
    assert escritas == {}
