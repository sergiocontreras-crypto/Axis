# -*- coding: utf-8 -*-
"""
Pruebas de la Property 4 (Reconciliación RF y ORI=0 en no-RF) del informe por tramos.

Feature / spec: excel-por-tramos-consistencia (Defecto 4). Tarea 5.1 del plan.

Property 4: Reconciliación RF y ORI=0 en no-RF.

Para todo corte, la suma del P&G y del ORI sobre las categorías RF de
`TotalCategoria_<corte>` es igual, dentro de la tolerancia numérica del informe (float64),
a la suma del P&G y del ORI del `rf_detalle` de esas mismas categorías; y para toda fila
no-RF del resumen se cumple `ORI == 0` y `total_resultado == P&G`.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.6**

Tag: Feature: excel-por-tramos-consistencia, Property 4: Reconciliación RF y ORI=0 en no-RF.

Enfoque test-first (Tarea 5.1 -> 5.2 -> 5.3):
  - La parte RF reconcilia POR CONSTRUCCIÓN: `_resumen_rf` consume el MISMO `rf_detalle` por
    título que la hoja `RF_Detalle_Titulo_<corte>`, y `TotalCategoria` es un `groupby` de
    `ResumenPyG`. Por asociatividad de la suma, Σ(TotalCategoria[C]) == Σ(rf_detalle de C).
    Esto ya debe cumplirse con los Defectos 1 y 2 aplicados.
  - La parte no-RF (RV/Alt/derivados) es lo que formaliza la Tarea 5.2: ORI=0 explícito y
    todo el resultado en P&G. Si alguna fila no-RF trajera ORI != 0 o total != pyg, esta
    propiedad lo detecta.

La reconciliación se evalúa por la vía real reconstruida desde BD
(`generar_resumen_por_tramo`), sembrando `portafolio_clasificado` (clasificación por
título) e `informe_pyg_por_tramo` (payload con `rf_detalle`, `rv`, `alt`, `deriv_pyg` por
corte). Se incluyen casos con subyacentes ("Subyacentes de Swaps") y PEI para verificar la
coherencia con los Defectos 1 y 2.
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


# Categorías RF que produce `_categoria` para títulos de renta fija.
_CATEGORIAS_RF = {"RF Local", "RF Internacional", "Subyacentes de Swaps"}
# Categorías no-RF que puede producir `_resumen_no_rf`.
_CATEGORIAS_NO_RF = {
    "RV Local", "RV Internacional", "PEI", "Alt Local", "Alt Internacional",
    "Swaps", "Forwards", "Opciones",
}


# ==============================================================================
# Estrategias Hypothesis para rf_detalle sintético y bloques no-RF
# ==============================================================================

# Montos finitos y acotados: evitan overflow/NaN y mantienen la tolerancia float64 estable.
_MONTO = st.floats(min_value=-1e7, max_value=1e7, allow_nan=False, allow_infinity=False)
_VM = st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False)
# Monedas: locales (COP/UVR) e internacionales, para ejercitar RF Local vs Internacional.
_MONEDA = st.sampled_from(["COP", "UVR", "USD", "EUR"])
# Clasificación contable NEG/DV/VC (la regla no se altera; solo se agrupa por ella).
_CLASIFICACION = st.sampled_from(["NEG", "DV", "VC"])


@st.composite
def _fila_rf(draw, idx):
    """Una fila de `rf_detalle` por título. `pyg` y `ori` independientes; total = pyg + ori
    (coherente con la descomposición contable del detalle por título)."""
    pyg = draw(_MONTO)
    ori = draw(_MONTO)
    es_sub = draw(st.booleans())
    return {
        "id_posicion": f"T{idx}",
        "moneda": draw(_MONEDA),
        "asset_class": draw(st.sampled_from(["", "DP LP", "TES", "BONO"])),
        "es_subyacente": es_sub,
        "clasificacion": draw(_CLASIFICACION),
        "pyg": pyg,
        "ori": ori,
        "total_resultado": pyg + ori,
        "vm_inicial": draw(_VM),
        "vm_mercado": draw(_VM),
        "ramo": draw(st.sampled_from(["R1", "R2"])),
        "compania": draw(st.sampled_from(["C1", "C2"])),
    }


@st.composite
def _rf_detalle_strategy(draw):
    """Lista de filas rf_detalle (al menos una) con ids únicos."""
    n = draw(st.integers(min_value=1, max_value=12))
    return [draw(_fila_rf(i)) for i in range(n)]


# ==============================================================================
# Siembra de la BD por tramos sintética
# ==============================================================================

def _sembrar_bd(ruta_db, rf_detalle, rv, alt, especies_pei):
    """Siembra `portafolio_clasificado` + `informe_pyg_por_tramo` para un corte.

    - `portafolio_clasificado` describe las especies RV (PEI vs COLCAP) para que
      `_resumen_no_rf` pueda rutear la categoría RV y derivar `es_pei` (Defecto 2).
    - El payload del corte lleva `rf_detalle` (fuente única RF), `rv`, `alt` y bloques de
      derivados vacíos (no necesarios para esta propiedad).
    """
    conn = sqlite3.connect(str(ruta_db))
    try:
        filas_clasif = []
        for i, r in enumerate(rv):
            esp = str(r["titulo"])
            es_pei = esp in especies_pei
            filas_clasif.append({
                "id_posicion": f"RV_{i}", "especie": esp, "ramo": "R1",
                "compania": "C1", "moneda": r.get("moneda", "COP"),
                "clase_origen": "RENTA_VARIABLE",
                "asset_class_definitivo": "PEI" if es_pei else "COLCAP",
                "id_cobertura": "", "valor_mercado": 1000.0,
            })
        for i, r in enumerate(alt):
            esp = str(r["titulo"])
            filas_clasif.append({
                "id_posicion": f"ALT_{i}", "especie": esp, "ramo": "R1",
                "compania": "C1", "moneda": r.get("moneda", "COP"),
                "clase_origen": "ALTERNATIVO",
                "asset_class_definitivo": "ALT", "id_cobertura": "",
                "valor_mercado": 1000.0,
            })
        # Al menos una fila para crear la tabla con el esquema esperado.
        if not filas_clasif:
            filas_clasif.append({
                "id_posicion": "DUMMY", "especie": "DUMMY", "ramo": "R1",
                "compania": "C1", "moneda": "COP", "clase_origen": "TITULO",
                "asset_class_definitivo": "", "id_cobertura": "", "valor_mercado": 0.0,
            })
        pd.DataFrame(filas_clasif).to_sql(
            "portafolio_clasificado", conn, if_exists="replace", index=False)

        payload = {
            "rf_detalle": rf_detalle, "rv": rv, "alt": alt,
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
    finally:
        conn.close()


def _total_categoria(df_resumen):
    """Replica el groupby de TotalCategoria_<corte> (tools/exportar_excel_tramos.py):
    una fila por categoría con Σ pyg, Σ ori, Σ total_resultado."""
    if df_resumen is None or df_resumen.empty:
        return pd.DataFrame(columns=["categoria", "pyg", "ori", "total_resultado"])
    return df_resumen.groupby("categoria", as_index=False).agg(
        pyg=("pyg", "sum"), ori=("ori", "sum"),
        total_resultado=("total_resultado", "sum"),
    )


def _categoria_rf_de_fila(fila):
    """Categoría RF esperada de una fila de rf_detalle, usando la misma función real."""
    return ert._categoria("TITULO", fila.get("moneda"), fila.get("asset_class"),
                          bool(fila.get("es_subyacente")))


# ==============================================================================
# Property 4 — Reconciliación RF y ORI=0 en no-RF
# ==============================================================================

@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
@given(
    rf_detalle=_rf_detalle_strategy(),
    especies_rv=st.lists(
        st.text(alphabet=st.characters(min_codepoint=65, max_codepoint=90),
                min_size=3, max_size=6),
        min_size=0, max_size=6, unique=True),
    n_pei=st.integers(min_value=0, max_value=6),
    pygs_rv=st.lists(_MONTO, min_size=0, max_size=6),
    especies_alt=st.lists(
        st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122),
                min_size=3, max_size=6),
        min_size=0, max_size=4, unique=True),
    pygs_alt=st.lists(_MONTO, min_size=0, max_size=4),
)
def test_prop4_reconciliacion_rf_y_ori_cero_no_rf(
    tmp_path_factory, rf_detalle, especies_rv, n_pei, pygs_rv, especies_alt, pygs_alt,
):
    """
    Feature: excel-por-tramos-consistencia, Property 4: Reconciliación RF y ORI=0 en no-RF.

    (1) Para cada categoría RF, Σ pyg y Σ ori en TotalCategoria == Σ pyg/Σ ori del
        rf_detalle de las mismas categorías (tolerancia float64).
    (2) Toda fila no-RF del resumen cumple ORI == 0 y total_resultado == pyg.
    """
    # Construir bloques RV/Alt con P&G por especie (último corte).
    n_pei = min(n_pei, len(especies_rv))
    especies_pei = set(especies_rv[:n_pei])
    rv = []
    for i, esp in enumerate(especies_rv):
        pyg = pygs_rv[i] if i < len(pygs_rv) else 0.0
        rv.append({"titulo": esp, "moneda": "COP",
                   "fecha_proyeccion": "2026-12-31", "pyg_acumulado": pyg,
                   "valor_mercado": 0.0})
    alt = []
    for i, esp in enumerate(especies_alt):
        pyg = pygs_alt[i] if i < len(pygs_alt) else 0.0
        alt.append({"titulo": esp, "moneda": "COP",
                    "fecha_proyeccion": "2026-12-31", "pyg_acumulado": pyg,
                    "valor_mercado": 0.0})

    ruta = tmp_path_factory.mktemp("prop4") / "tramos_reconc.db"
    _sembrar_bd(ruta, rf_detalle, rv, alt, especies_pei)

    salida = ert.generar_resumen_por_tramo(str(ruta))
    assert salida, "la reconstrucción por tramos no debe estar vacía"
    df = salida["2026-12-31"]
    tot = _total_categoria(df)
    tot_idx = tot.set_index("categoria")

    # ── (1) Reconciliación RF por categoría: suma directa del rf_detalle ──
    esperado_pyg: dict[str, float] = {}
    esperado_ori: dict[str, float] = {}
    for fila in rf_detalle:
        cat = _categoria_rf_de_fila(fila)
        esperado_pyg[cat] = esperado_pyg.get(cat, 0.0) + float(fila["pyg"])
        esperado_ori[cat] = esperado_ori.get(cat, 0.0) + float(fila["ori"])

    for cat in esperado_pyg:
        assert cat in tot_idx.index, (
            f"la categoría RF '{cat}' debe existir en TotalCategoria"
        )
        obt_pyg = float(tot_idx.loc[cat, "pyg"])
        obt_ori = float(tot_idx.loc[cat, "ori"])
        assert math.isclose(obt_pyg, esperado_pyg[cat], rel_tol=1e-9, abs_tol=1e-3), (
            f"RF '{cat}': Sigma pyg TotalCategoria={obt_pyg} != rf_detalle={esperado_pyg[cat]}"
        )
        assert math.isclose(obt_ori, esperado_ori[cat], rel_tol=1e-9, abs_tol=1e-3), (
            f"RF '{cat}': Sigma ori TotalCategoria={obt_ori} != rf_detalle={esperado_ori[cat]}"
        )

    # ── (2) No-RF: ORI == 0 y total_resultado == pyg en TODAS las filas no-RF ──
    for _, fila in df.iterrows():
        if str(fila["categoria"]) in _CATEGORIAS_NO_RF:
            assert math.isclose(float(fila["ori"]), 0.0, abs_tol=1e-12), (
                f"no-RF '{fila['categoria']}' debe tener ORI == 0; obtenido {fila['ori']}"
            )
            assert math.isclose(
                float(fila["total_resultado"]), float(fila["pyg"]),
                rel_tol=1e-9, abs_tol=1e-6), (
                f"no-RF '{fila['categoria']}': total_resultado debe == pyg; "
                f"total={fila['total_resultado']} pyg={fila['pyg']}"
            )


# ==============================================================================
# Ejemplos (unit) — reconciliación y ORI=0 en casos concretos
# ==============================================================================

def test_reconciliacion_rf_simple_con_subyacente(tmp_path):
    """RF Local + RF Internacional + Subyacente: TotalCategoria == suma directa del detalle."""
    rf_detalle = [
        {"id_posicion": "T1", "moneda": "COP", "asset_class": "TES",
         "es_subyacente": False, "clasificacion": "NEG",
         "pyg": 100.0, "ori": 10.0, "total_resultado": 110.0,
         "vm_inicial": 1000.0, "vm_mercado": 1100.0, "ramo": "R1", "compania": "C1"},
        {"id_posicion": "T2", "moneda": "USD", "asset_class": "BONO",
         "es_subyacente": False, "clasificacion": "DV",
         "pyg": 50.0, "ori": -5.0, "total_resultado": 45.0,
         "vm_inicial": 500.0, "vm_mercado": 545.0, "ramo": "R1", "compania": "C1"},
        {"id_posicion": "T3", "moneda": "USD", "asset_class": "DP LP",
         "es_subyacente": True, "clasificacion": "VC",
         "pyg": 20.0, "ori": 3.0, "total_resultado": 23.0,
         "vm_inicial": 200.0, "vm_mercado": 223.0, "ramo": "R2", "compania": "C2"},
    ]
    ruta = tmp_path / "reconc_rf.db"
    _sembrar_bd(ruta, rf_detalle, rv=[], alt=[], especies_pei=set())
    salida = ert.generar_resumen_por_tramo(str(ruta))
    df = salida["2026-12-31"]
    tot = _total_categoria(df).set_index("categoria")

    assert math.isclose(float(tot.loc["RF Local", "pyg"]), 100.0, abs_tol=1e-6)
    assert math.isclose(float(tot.loc["RF Local", "ori"]), 10.0, abs_tol=1e-6)
    assert math.isclose(float(tot.loc["RF Internacional", "pyg"]), 50.0, abs_tol=1e-6)
    assert math.isclose(float(tot.loc["RF Internacional", "ori"]), -5.0, abs_tol=1e-6)
    assert math.isclose(float(tot.loc["Subyacentes de Swaps", "pyg"]), 20.0, abs_tol=1e-6)
    assert math.isclose(float(tot.loc["Subyacentes de Swaps", "ori"]), 3.0, abs_tol=1e-6)


def test_no_rf_ori_cero_y_total_igual_pyg(tmp_path):
    """RV (COLCAP + PEI) y Alt: todas las filas no-RF con ORI=0 y total == pyg."""
    rv = [
        {"titulo": "AAA", "moneda": "COP", "fecha_proyeccion": "2026-12-31",
         "pyg_acumulado": 77.0, "valor_mercado": 0.0},
        {"titulo": "BBB", "moneda": "COP", "fecha_proyeccion": "2026-12-31",
         "pyg_acumulado": 33.0, "valor_mercado": 0.0},
    ]
    alt = [
        {"titulo": "ccc", "moneda": "USD", "fecha_proyeccion": "2026-12-31",
         "pyg_acumulado": 12.0, "valor_mercado": 0.0},
    ]
    ruta = tmp_path / "no_rf.db"
    _sembrar_bd(ruta, rf_detalle=[], rv=rv, alt=alt, especies_pei={"BBB"})
    salida = ert.generar_resumen_por_tramo(str(ruta))
    df = salida["2026-12-31"]

    no_rf = df[df["categoria"].isin(_CATEGORIAS_NO_RF)]
    assert not no_rf.empty, "debe haber filas no-RF (RV/Alt)"
    for _, fila in no_rf.iterrows():
        assert math.isclose(float(fila["ori"]), 0.0, abs_tol=1e-12)
        assert math.isclose(float(fila["total_resultado"]), float(fila["pyg"]),
                            rel_tol=1e-9, abs_tol=1e-6)


# ==============================================================================
# EXPLORACIÓN de bug (Defecto D2) — Tarea 2, Ola 1 de la spec
# `derivados-pyg-cupones-y-reconciliacion-tramos`.
#
# Objetivo: este test DEBE FALLAR sobre el código actual, confirmando D2.
#
# Bug D2: en `_resumen_no_rf` (tools/export_resumen_tramos.py), el bloque RV/Alt agrega el
# P&G por `titulo`/especie en vez de por `id_posicion`:
#   - `col_id = "titulo" if "titulo" in df.columns else (...)` prioriza `titulo`.
#   - `idx = df.groupby(col_id)["fecha_proyeccion"].idxmax()` COLAPSA en una sola fila a
#     varias posiciones (id_posicion distintos) que comparten la MISMA especie (titulo).
#   - Luego PRORRATEA el P&G de esa única fila superviviente entre las posiciones del base
#     de esa especie según VM inicial.
# Consecuencia: si N posiciones (id_posicion distintos) comparten la MISMA especie con P&G
# distintos, la suma del detalle por posición NO coincide con el ResumenPyG/TotalCategoria
# de la categoría (se pierde el P&G de las posiciones colapsadas).
# ==============================================================================


def _sembrar_bd_idpos(ruta_db, filas_clasif, rv, alt, corte="2026-12-31",
                      trm_inicio=3900.0, trm_cierre=3900.0):
    """Siembra una BD por tramos permitiendo `id_posicion` por fila tanto en
    `portafolio_clasificado` como en el payload `rv`/`alt`.

    A diferencia de `_sembrar_bd`, NO deriva `id_posicion` del índice ni usa `titulo` como
    especie: cada fila del base y del payload trae su propio `id_posicion` y `especie`/
    `titulo`, lo que permite reproducir el escenario multiposición-misma-especie de D2.
    """
    conn = sqlite3.connect(str(ruta_db))
    try:
        pd.DataFrame(filas_clasif).to_sql(
            "portafolio_clasificado", conn, if_exists="replace", index=False)
        payload = {
            "rf_detalle": [], "rv": rv, "alt": alt,
            "deriv_pyg": [], "deriv_vm_cierre": {},
            "trm_inicio": trm_inicio, "trm_cierre": trm_cierre,
        }
        conn.execute(
            "CREATE TABLE informe_pyg_por_tramo (fecha_corte TEXT, payload_json TEXT)")
        conn.execute(
            "INSERT INTO informe_pyg_por_tramo (fecha_corte, payload_json) VALUES (?, ?)",
            (corte, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def test_reconciliacion_rv_multiposicion_misma_especie(tmp_path):
    """EXPLORACIÓN D2 (DEBE FALLAR hoy): 3 posiciones RV con id_posicion distintos pero la
    MISMA especie 'ACC1' y P&G distintos (100, 55, -20). La suma del detalle por posición
    en la categoría 'RV Local' debe ser 100+55-20 = 135, pero el bug D2 colapsa las 3
    posiciones por `titulo` y pierde el P&G de las colapsadas.
    """
    corte = "2026-12-31"
    # Base: 3 posiciones RV con id_posicion distintos, MISMA especie 'ACC1'.
    filas_clasif = [
        {"id_posicion": "RV_A", "especie": "ACC1", "ramo": "R1", "compania": "C1",
         "moneda": "COP", "clase_origen": "RENTA_VARIABLE",
         "asset_class_definitivo": "COLCAP", "id_cobertura": "", "valor_mercado": 1000.0},
        {"id_posicion": "RV_B", "especie": "ACC1", "ramo": "R1", "compania": "C1",
         "moneda": "COP", "clase_origen": "RENTA_VARIABLE",
         "asset_class_definitivo": "COLCAP", "id_cobertura": "", "valor_mercado": 2000.0},
        {"id_posicion": "RV_C", "especie": "ACC1", "ramo": "R1", "compania": "C1",
         "moneda": "COP", "clase_origen": "RENTA_VARIABLE",
         "asset_class_definitivo": "COLCAP", "id_cobertura": "", "valor_mercado": 3000.0},
    ]
    # Payload rv: 3 registros con id_posicion propio y P&G distintos, misma especie 'ACC1'.
    rv = [
        {"id_posicion": "RV_A", "titulo": "ACC1", "moneda": "COP",
         "fecha_proyeccion": corte, "pyg_acumulado": 100.0, "valor_mercado": 1100.0},
        {"id_posicion": "RV_B", "titulo": "ACC1", "moneda": "COP",
         "fecha_proyeccion": corte, "pyg_acumulado": 55.0, "valor_mercado": 2050.0},
        {"id_posicion": "RV_C", "titulo": "ACC1", "moneda": "COP",
         "fecha_proyeccion": corte, "pyg_acumulado": -20.0, "valor_mercado": 2980.0},
    ]

    ruta = tmp_path / "reconc_rv_multipos.db"
    _sembrar_bd_idpos(ruta, filas_clasif, rv=rv, alt=[], corte=corte)

    salida = ert.generar_resumen_por_tramo(str(ruta))
    assert salida, "la reconstrucción por tramos no debe estar vacía"
    df = salida[corte]

    # P&G esperado del detalle por id_posicion dentro de 'RV Local'.
    esperado = 100.0 + 55.0 + (-20.0)  # == 135.0
    rv_local = df[df["categoria"] == "RV Local"]
    assert not rv_local.empty, "debe existir la categoría 'RV Local'"
    obtenido = float(rv_local["pyg"].sum())

    # El número de posiciones RV no debe colapsarse: 3 id_posicion distintos deberían
    # poder distinguirse (al menos tantas filas/aportes como posiciones del base).
    n_aportes = int(rv_local["n"].sum()) if "n" in rv_local.columns else len(rv_local)
    assert n_aportes >= 3, (
        f"D2: se esperaban >=3 aportes RV (RV_A, RV_B, RV_C), pero el detalle colapsado "
        f"reporta {n_aportes}. El bug agrupa por 'titulo' y pierde posiciones."
    )

    assert math.isclose(obtenido, esperado, rel_tol=1e-9, abs_tol=1e-6), (
        f"D2: Sigma pyg 'RV Local' por id_posicion debe ser {esperado} "
        f"(100+55-20), pero se obtuvo {obtenido}. El bug colapsa posiciones distintas "
        f"con la misma especie 'ACC1' y pierde su P&G."
    )
