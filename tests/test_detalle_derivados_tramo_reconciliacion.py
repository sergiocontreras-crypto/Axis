# -*- coding: utf-8 -*-
"""
Test de INTEGRACIÓN — Reconciliación del detalle de derivados por tramo (Defecto D3).

Feature / spec: `derivados-pyg-cupones-y-reconciliacion-tramos`, Ola 4 (D3), Tarea 10.

Objetivo
--------
Verificar que la hoja de detalle de SWAPS por corte que produce
`tools/exportar_excel_tramos._anexar_derivados_detalle_tramos` (Tarea 9) RECONCILIA, en su
fila de cierre por posición, con el P&G de Swaps del ResumenPyG que arma
`tools/export_resumen_tramos.generar_resumen_por_tramo` (vía `deriv_pyg`, que tras D1 ya
incluye el cupón neto realizado).

La columna derivada clave del detalle es (ver docstring de la función de producción):

    cupon_neto_acum_cop = cupones_der_acum_cop - cupones_obl_acum_cop
    pyg_corte           = (valoracion - valoracion_base_por_id)
                          + (cupon_neto_acum_cop - cupon_neto_base_por_id)

donde la base por `id_posicion` es el PRIMER corte (menor `fecha_valoracion`).

Definición D1 del P&G de Swaps por corte (coherente con el detalle):

    p_y_g(Swaps, corte) = Σ_swaps [ (valoracion(corte)   - valoracion(base))
                                  + (cup_neto_acum(corte) - cup_neto_acum(base)) ]

Por asociatividad de la suma, el P&G del ÚLTIMO corte == Σ pyg_corte de la fila de CIERRE
(mayor `fecha_valoracion`) de cada swap. Eso es lo que afirma la reconciliación.

NO modifica código de producción: solo siembra una BD temporal, invoca la función de
exportación real, reabre el .xlsx y compara.

Convención Windows/Drive: ruta absoluta + `--rootdir` + `-o addopts="" -p no:cacheprovider`
(ver el comando de ejecución en la tarea).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

# Las funciones de producción viven en tools/ (igual que el test de reconciliación
# existente `test_property_reconciliacion_total_categoria.py`): añadir tools/ al path.
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import exportar_excel_tramos as eet  # noqa: E402
import export_resumen_tramos as ert  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Datos sintéticos de swaps (números simples, pyg_corte calculable a mano)
# ──────────────────────────────────────────────────────────────────────────────
# Tres cortes por swap; el PRIMERO ('2026-12-31') es la base por id_posicion.
_CORTES = ["2026-12-31", "2027-12-31", "2028-12-31"]

# SW1: valoracion [100,150,220]; cup_neto = der-obl = [0,6,18] (der [0,10,30], obl [0,4,12])
# SW2: valoracion [200,180,260]; cup_neto = der-obl = [0,3,16] (der [0,5,25],  obl [0,2,9])
_SWAPS_DEF = {
    "SW1": {
        "valoracion": [100.0, 150.0, 220.0],
        "cupones_der_acum_cop": [0.0, 10.0, 30.0],
        "cupones_obl_acum_cop": [0.0, 4.0, 12.0],
    },
    "SW2": {
        "valoracion": [200.0, 180.0, 260.0],
        "cupones_der_acum_cop": [0.0, 5.0, 25.0],
        "cupones_obl_acum_cop": [0.0, 2.0, 9.0],
    },
}


def _construir_records_swaps() -> list[dict]:
    """Lista de records (posición × corte) para `deriv_detalle['swaps']`."""
    records = []
    for id_pos, serie in _SWAPS_DEF.items():
        for i, corte in enumerate(_CORTES):
            records.append({
                "id_posicion": id_pos,
                "fecha_valoracion": corte,
                "valoracion": serie["valoracion"][i],
                "cupones_der_acum_cop": serie["cupones_der_acum_cop"][i],
                "cupones_obl_acum_cop": serie["cupones_obl_acum_cop"][i],
                # Columnas de desglose adicionales (presentes en producción; no afectan pyg).
                "vpn_der_calc": serie["valoracion"][i] + serie["cupones_der_acum_cop"][i],
                "vpn_obl_calc": serie["cupones_obl_acum_cop"][i],
                "nominal": 1000.0,
                "moneda_der": "USD",
                "moneda_obl": "COP",
            })
    return records


def _deriv_pyg_desde_records(records: list[dict]) -> list[dict]:
    """Construye la serie `deriv_pyg` para tipo 'Swaps' a partir de los MISMOS records del
    detalle, usando la definición D1 (así el esperado y el detalle no se desincronizan):

        p_y_g(Swaps, corte) = Σ_swaps [ (valoracion(corte)   - valoracion(base))
                                      + (cup_neto_acum(corte) - cup_neto_acum(base)) ]

    La base por id es el corte de menor `fecha_valoracion`.
    """
    df = pd.DataFrame(records)
    df["cup_neto_acum"] = df["cupones_der_acum_cop"] - df["cupones_obl_acum_cop"]
    df = df.sort_values(["id_posicion", "fecha_valoracion"])
    base_val = df.groupby("id_posicion")["valoracion"].transform("first")
    base_cup = df.groupby("id_posicion")["cup_neto_acum"].transform("first")
    df["pyg_fila"] = (df["valoracion"] - base_val) + (df["cup_neto_acum"] - base_cup)
    # Σ por corte sobre todos los swaps.
    serie = df.groupby("fecha_valoracion")["pyg_fila"].sum()
    return [
        {"fecha_valoracion": corte, "tipo": "Swaps", "p_y_g": float(serie.loc[corte])}
        for corte in _CORTES
    ]


def _sembrar_bd(ruta_db: str, deriv_detalle: dict, deriv_pyg: list[dict],
                corte: str = "2026-12-31", indice: int = 0) -> None:
    """Siembra `informe_pyg_por_tramo` (con columna `indice`, que el exporter requiere) y una
    `portafolio_clasificado` mínima para que `generar_resumen_por_tramo` no falle.

    Los swaps NO necesitan match por id en `portafolio_clasificado`: el P&G de Swaps del
    ResumenPyG se calcula a nivel de TIPO desde `deriv_pyg` (no hereda ramo por id).
    """
    conn = sqlite3.connect(ruta_db)
    try:
        # portafolio_clasificado mínima (al menos una fila para crear la tabla/esquema).
        filas_clasif = [{
            "id_posicion": "DUMMY", "especie": "DUMMY", "ramo": "R1",
            "compania": "C1", "moneda": "COP", "clase_origen": "TITULO",
            "asset_class_definitivo": "", "id_cobertura": "", "valor_mercado": 0.0,
        }]
        pd.DataFrame(filas_clasif).to_sql(
            "portafolio_clasificado", conn, if_exists="replace", index=False)

        payload = {
            "deriv_detalle": deriv_detalle,
            "deriv_pyg": deriv_pyg,
            "rf_detalle": [], "rv": [], "alt": [],
            "deriv_vm_cierre": {},
            "trm_inicio": 3900.0, "trm_cierre": 3900.0,
        }
        conn.execute(
            "CREATE TABLE informe_pyg_por_tramo "
            "(fecha_corte TEXT, indice INTEGER, payload_json TEXT)")
        conn.execute(
            "INSERT INTO informe_pyg_por_tramo (fecha_corte, indice, payload_json) "
            "VALUES (?, ?, ?)",
            (corte, indice, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _nombre_hoja_swaps(corte: str) -> str:
    """Replica `_nombre_hoja('Swaps_Detalle_', corte)` de producción (saneo + truncado 31)."""
    corte_safe = re.sub(r"[:\\/?*\[\]]", "-", str(corte).strip())
    return f"Swaps_Detalle_{corte_safe}"[:31]


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 — Reconciliación detalle de swaps ↔ ResumenPyG
# ──────────────────────────────────────────────────────────────────────────────

def test_swaps_detalle_reconcilia_con_resumen_pyg(tmp_path):
    """La suma de `pyg_corte` sobre la fila de CIERRE (mayor `fecha_valoracion`) por swap del
    detalle `Swaps_Detalle_<corte>` == P&G de Swaps del ResumenPyG (último corte de
    `deriv_pyg`, coherente con D1).
    """
    corte = "2026-12-31"
    records = _construir_records_swaps()
    deriv_detalle = {"swaps": records}
    deriv_pyg = _deriv_pyg_desde_records(records)

    ruta_db = str(tmp_path / "tramos_deriv_detalle.db")
    _sembrar_bd(ruta_db, deriv_detalle, deriv_pyg, corte=corte, indice=0)

    # ── Exportar el detalle de derivados a un .xlsx temporal ──
    ruta_xlsx = str(tmp_path / "detalle_derivados.xlsx")
    with pd.ExcelWriter(ruta_xlsx, engine="openpyxl") as writer:
        escritas = eet._anexar_derivados_detalle_tramos(writer, ruta_db)

    hoja = _nombre_hoja_swaps(corte)
    assert hoja in escritas, (
        f"debe haberse escrito la hoja de swaps '{hoja}'; escritas={list(escritas)}"
    )

    # ── (a) La hoja existe, trae 6 filas (2 swaps × 3 cortes) y las columnas derivadas ──
    df_hoja = pd.read_excel(ruta_xlsx, sheet_name=hoja)
    assert len(df_hoja) == 6, f"se esperaban 6 filas (2 swaps × 3 cortes), hay {len(df_hoja)}"
    assert "cupon_neto_acum_cop" in df_hoja.columns, "falta columna cupon_neto_acum_cop"
    assert "pyg_corte" in df_hoja.columns, "falta columna pyg_corte"

    # ── (b) Reconciliación: Σ pyg_corte de la fila de CIERRE por id == Swaps del ResumenPyG ──
    # Fila de cierre por swap = la de mayor fecha_valoracion por id_posicion.
    idx_cierre = df_hoja.groupby("id_posicion")["fecha_valoracion"].idxmax()
    suma_pyg_cierre = float(df_hoja.loc[idx_cierre, "pyg_corte"].sum())

    # Esperado calculado a mano (último corte, base = 2026-12-31):
    #   SW1: (220-100) + ((30-12)-(0-0)) = 120 + 18 = 138
    #   SW2: (260-200) + ((25-9)-(0-0)) =  60 + 16 =  76
    #   Σ cierre = 214
    assert math.isclose(suma_pyg_cierre, 214.0, rel_tol=1e-9, abs_tol=1e-6), (
        f"Σ pyg_corte de cierre debe ser 214.0 (138 + 76); obtenido {suma_pyg_cierre}"
    )

    # P&G de Swaps del ResumenPyG (categoría 'Swaps' del corte) vía la función real.
    resumen = ert.generar_resumen_por_tramo(ruta_db)
    assert corte in resumen, f"el ResumenPyG debe traer el corte {corte}"
    df_res = resumen[corte]
    swaps_res = df_res[df_res["categoria"] == "Swaps"]
    assert not swaps_res.empty, "el ResumenPyG debe traer la categoría 'Swaps'"
    pyg_swaps_resumen = float(swaps_res["pyg"].sum())

    assert math.isclose(suma_pyg_cierre, pyg_swaps_resumen, rel_tol=1e-9, abs_tol=1e-6), (
        f"RECONCILIACIÓN D3: Σ pyg_corte de cierre del detalle ({suma_pyg_cierre}) debe == "
        f"P&G de Swaps del ResumenPyG ({pyg_swaps_resumen})."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 — Falla-segura: corte sin deriv_detalle no crea hoja ni rompe
# ──────────────────────────────────────────────────────────────────────────────

def test_corte_sin_deriv_detalle_no_crea_hoja_ni_rompe(tmp_path):
    """Un corte cuyo payload NO trae `deriv_detalle` (o trae `{}`) no debe crear hoja de
    swaps ni lanzar excepción. Un segundo corte CON detalle sí crea su hoja.
    """
    corte_sin = "2026-12-31"
    corte_con = "2027-12-31"
    records = _construir_records_swaps()

    ruta_db = str(tmp_path / "tramos_sin_detalle.db")
    conn = sqlite3.connect(ruta_db)
    try:
        pd.DataFrame([{
            "id_posicion": "DUMMY", "especie": "DUMMY", "ramo": "R1",
            "compania": "C1", "moneda": "COP", "clase_origen": "TITULO",
            "asset_class_definitivo": "", "id_cobertura": "", "valor_mercado": 0.0,
        }]).to_sql("portafolio_clasificado", conn, if_exists="replace", index=False)

        conn.execute(
            "CREATE TABLE informe_pyg_por_tramo "
            "(fecha_corte TEXT, indice INTEGER, payload_json TEXT)")

        # Corte 0: payload SIN deriv_detalle (dict vacío) → no debe crear hoja de swaps.
        payload_sin = {
            "deriv_detalle": {},
            "deriv_pyg": [], "rf_detalle": [], "rv": [], "alt": [],
            "deriv_vm_cierre": {}, "trm_inicio": 3900.0, "trm_cierre": 3900.0,
        }
        # Corte 1: payload CON deriv_detalle de swaps → sí crea su hoja.
        payload_con = {
            "deriv_detalle": {"swaps": records},
            "deriv_pyg": _deriv_pyg_desde_records(records),
            "rf_detalle": [], "rv": [], "alt": [],
            "deriv_vm_cierre": {}, "trm_inicio": 3900.0, "trm_cierre": 3900.0,
        }
        conn.executemany(
            "INSERT INTO informe_pyg_por_tramo (fecha_corte, indice, payload_json) "
            "VALUES (?, ?, ?)",
            [
                (corte_sin, 0, json.dumps(payload_sin)),
                (corte_con, 1, json.dumps(payload_con)),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    ruta_xlsx = str(tmp_path / "detalle_derivados_falla_segura.xlsx")
    with pd.ExcelWriter(ruta_xlsx, engine="openpyxl") as writer:
        escritas = eet._anexar_derivados_detalle_tramos(writer, ruta_db)

    # No lanza y devuelve dict.
    assert isinstance(escritas, dict)
    # El corte SIN detalle no genera hoja de swaps.
    assert _nombre_hoja_swaps(corte_sin) not in escritas, (
        "el corte sin deriv_detalle NO debe crear hoja de swaps"
    )
    # El corte CON detalle sí genera su hoja (la función no se rompe por el otro corte).
    assert _nombre_hoja_swaps(corte_con) in escritas, (
        "el corte con deriv_detalle debe crear su hoja de swaps"
    )
