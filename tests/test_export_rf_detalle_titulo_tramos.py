# -*- coding: utf-8 -*-
"""
Test del exportador por tramos — Tarea 11 / Defecto 6 (hoja RF_Detalle_Titulo_<corte>).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix), Fase 2, Defecto 6.

Requirement 6.1 (Expected Behavior): el Excel por tramos SHALL escribir el detalle de
P&G/ORI por título de RF de cada corte, reutilizando la clave `rf_detalle` que el orquestador
por tramos ya persiste por corte en `informe_pyg_por_tramo` (sin recalcular). Se escribe una
hoja por corte `RF_Detalle_Titulo_<corte>`, consistente con `Portafolio_<corte>` /
`ResumenPyG_<corte>`.

**Validates: Requirement 6.1**

Preservación: añadir las hojas RF_Detalle_Titulo_<corte> NO altera las hojas existentes
(Portafolio_/ResumenPyG_/series mensuales RV/Alt/Deriv/RF).

Enfoque (igual patrón que tests/test_export_rf_mensual_tramos.py): se ejercita la RUTA REAL
del exportador sobre una DB SQLite TEMPORAL con la tabla `informe_pyg_por_tramo` sembrada con
dos cortes (payload JSON con la clave `rf_detalle`), escribiendo a un `pd.ExcelWriter` real
(openpyxl). No toca la BD real ni el motor.
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


# Columnas que produce tools/detalle_pyg_tramo.construir_rf_detalle (orden de referencia).
_COLS_RF_DETALLE = [
    "id_posicion", "especie", "compania", "ramo", "asset_class", "clasificacion",
    "moneda", "es_subyacente", "vm_inicial", "vm_mercado", "vm_revaluado_usd",
    "pyg", "ori", "total_resultado",
]


def _sembrar_informe_pyg_por_tramo(ruta_db: Path, cortes: list) -> None:
    """Crea `informe_pyg_por_tramo` y persiste un payload JSON por corte.

    Cada entrada de `cortes` es (fecha_corte, indice, payload_dict). El payload replica el
    shape que el orquestador por tramos persiste, incluida la clave `rf_detalle`.
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


def _fila_detalle(id_posicion: str, especie: str, moneda: str, pyg: float,
                  ori: float = 0.0, es_subyacente: bool = False,
                  vm_revaluado_usd: bool = False) -> dict:
    """Fila de detalle por título con el shape de construir_rf_detalle."""
    return {
        "id_posicion": id_posicion,
        "especie": especie,
        "compania": "COMPANIA_X",
        "ramo": "RAMO_Y",
        "asset_class": "RENTA_FIJA",
        "clasificacion": "NEG",
        "moneda": moneda,
        "es_subyacente": es_subyacente,
        "vm_inicial": 1000.0,
        "vm_mercado": 1000.0 + pyg,
        "vm_revaluado_usd": vm_revaluado_usd,
        "pyg": pyg,
        "ori": ori,
        "total_resultado": pyg + ori,
    }


@pytest.fixture
def db_informe(tmp_path):
    """DB temporal con `informe_pyg_por_tramo` sembrada: 2 cortes con `rf_detalle`.

    Corte A (2026-12-31): 2 títulos. Corte B (2027-12-31): 3 títulos.
    """
    ruta = tmp_path / "informe_tramos_detalle.db"
    corte_a_det = [
        _fila_detalle("100", "BONO COP A", "COP", pyg=10.0),
        _fila_detalle("101", "BN ECOPETROL USD", "USD", pyg=25.0, vm_revaluado_usd=True),
    ]
    corte_b_det = [
        _fila_detalle("100", "BONO COP A", "COP", pyg=12.0),
        _fila_detalle("101", "BN ECOPETROL USD", "USD", pyg=30.0, vm_revaluado_usd=True),
        _fila_detalle("200", "SUBYACENTE SWAP", "COP", pyg=5.0, ori=1.0, es_subyacente=True),
    ]
    cortes = [
        ("2026-12-31", 0, {
            "rf_detalle": corte_a_det,
            "rf_mensual_contable": [],
            "rv": [], "alt": [], "deriv_pyg": [],
        }),
        ("2027-12-31", 1, {
            "rf_detalle": corte_b_det,
            "rf_mensual_contable": [],
            "rv": [], "alt": [], "deriv_pyg": [],
        }),
    ]
    _sembrar_informe_pyg_por_tramo(ruta, cortes)
    return ruta, corte_a_det, corte_b_det


def _escribir_detalle_y_leer(ruta_db: Path, out_xlsx: Path):
    """Ejecuta la función de detalle sobre un ExcelWriter real y devuelve (escritas, hojas)."""
    with pd.ExcelWriter(str(out_xlsx), engine="openpyxl") as writer:
        escritas = eet._anexar_rf_detalle_titulo_tramos(writer, str(ruta_db))
    hojas = pd.read_excel(str(out_xlsx), sheet_name=None)
    return escritas, hojas


def test_rf_detalle_titulo_una_hoja_por_corte(db_informe, tmp_path):
    """
    Req 6.1: se crea una hoja `RF_Detalle_Titulo_<corte>` por corte con las filas esperadas,
    reutilizando el `rf_detalle` persistido (sin recalcular).
    """
    ruta_db, corte_a_det, corte_b_det = db_informe
    escritas, hojas = _escribir_detalle_y_leer(ruta_db, tmp_path / "detalle.xlsx")

    hoja_a = eet._nombre_hoja("RF_Detalle_Titulo_", "2026-12-31")
    hoja_b = eet._nombre_hoja("RF_Detalle_Titulo_", "2027-12-31")

    # Una hoja por corte, presente en el writer y en el dict de retorno.
    assert hoja_a in hojas, f"falta la hoja {hoja_a}"
    assert hoja_b in hojas, f"falta la hoja {hoja_b}"
    assert escritas.get(hoja_a) == len(corte_a_det)
    assert escritas.get(hoja_b) == len(corte_b_det)

    # Filas esperadas por corte.
    assert len(hojas[hoja_a]) == len(corte_a_det)
    assert len(hojas[hoja_b]) == len(corte_b_det)


def test_rf_detalle_titulo_contenido_por_titulo(db_informe, tmp_path):
    """
    Req 6.1: el contenido escrito coincide con el `rf_detalle` sembrado (sin recalcular):
    columnas y valores por título preservados.
    """
    ruta_db, corte_a_det, corte_b_det = db_informe
    _escritas, hojas = _escribir_detalle_y_leer(ruta_db, tmp_path / "detalle2.xlsx")

    hoja_b = eet._nombre_hoja("RF_Detalle_Titulo_", "2027-12-31")
    df_b = hojas[hoja_b]

    # Todas las columnas de construir_rf_detalle están presentes.
    for col in _COLS_RF_DETALLE:
        assert col in df_b.columns, f"falta la columna {col}"

    # Ids y valores por título preservados (sin recálculo).
    ids = [str(x) for x in df_b["id_posicion"].tolist()]
    assert ids == ["100", "101", "200"]
    pyg_por_id = dict(zip(ids, df_b["pyg"].tolist()))
    assert pyg_por_id["100"] == 12.0
    assert pyg_por_id["101"] == 30.0
    assert pyg_por_id["200"] == 5.0

    # El flag de subyacente del título 200 se preserva.
    sub_por_id = dict(zip(ids, df_b["es_subyacente"].tolist()))
    assert bool(sub_por_id["200"]) is True


def test_rf_detalle_titulo_enganchado_en_anexar(db_informe, tmp_path):
    """
    Req 6.1 (integración): `anexar_hojas_detalle_tramos` incluye las hojas RF_Detalle_Titulo_
    por corte en su resultado, enganchado junto a las demás hojas por tramo.
    """
    ruta_db, corte_a_det, corte_b_det = db_informe
    out = tmp_path / "detalle_full.xlsx"
    with pd.ExcelWriter(str(out), engine="openpyxl") as writer:
        escritas = eet.anexar_hojas_detalle_tramos(writer, str(ruta_db))

    hoja_a = eet._nombre_hoja("RF_Detalle_Titulo_", "2026-12-31")
    hoja_b = eet._nombre_hoja("RF_Detalle_Titulo_", "2027-12-31")
    assert escritas.get(hoja_a) == len(corte_a_det)
    assert escritas.get(hoja_b) == len(corte_b_det)


def test_rf_detalle_titulo_no_altera_series_mensuales(db_informe, tmp_path):
    """
    Preservación: la función de detalle solo escribe hojas RF_Detalle_Titulo_<corte> y NO
    toca las hojas de series mensuales (RF/RV/Alt/Deriv). Se verifica que el conjunto de
    hojas escritas por la función de detalle sean únicamente las de detalle.
    """
    ruta_db, _a, _b = db_informe
    escritas, _hojas = _escribir_detalle_y_leer(ruta_db, tmp_path / "detalle3.xlsx")
    assert all(k.startswith("RF_Detalle_Titulo_") for k in escritas)


def test_rf_detalle_titulo_ausente_sin_tabla(tmp_path):
    """
    Falla-segura: si no existe `informe_pyg_por_tramo`, la función devuelve {} sin romper y
    no escribe hojas (patrón RO existente).
    """
    ruta_db = tmp_path / "vacia.db"
    sqlite3.connect(str(ruta_db)).close()  # DB sin la tabla
    with pd.ExcelWriter(str(tmp_path / "vacia.xlsx"), engine="openpyxl") as writer:
        # openpyxl exige al menos una hoja: placeholder.
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="placeholder", index=False)
        escritas = eet._anexar_rf_detalle_titulo_tramos(writer, str(ruta_db))
    assert escritas == {}


def test_rf_detalle_titulo_corte_sin_detalle_se_omite(tmp_path):
    """
    Falla-segura: un corte sin `rf_detalle` (o lista vacía) no genera hoja; los cortes con
    detalle sí.
    """
    ruta = tmp_path / "mixto.db"
    cortes = [
        ("2026-12-31", 0, {"rf_detalle": []}),  # vacío -> sin hoja
        ("2027-12-31", 1, {"rf_detalle": [_fila_detalle("1", "BONO", "COP", pyg=1.0)]}),
    ]
    _sembrar_informe_pyg_por_tramo(ruta, cortes)
    with pd.ExcelWriter(str(tmp_path / "mixto.xlsx"), engine="openpyxl") as writer:
        escritas = eet._anexar_rf_detalle_titulo_tramos(writer, str(ruta))

    hoja_a = eet._nombre_hoja("RF_Detalle_Titulo_", "2026-12-31")
    hoja_b = eet._nombre_hoja("RF_Detalle_Titulo_", "2027-12-31")
    assert hoja_a not in escritas
    assert escritas.get(hoja_b) == 1
