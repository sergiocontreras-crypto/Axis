"""
Test de INTEGRACIÓN del orquestador por tramos: consolidación + persistencia +
fallback (feature `optimizacion-agrupacion-negociables`, Tarea 5.2).

Verifica el "seam" (costura) del bloque 5c de `_ejecutar_escenario_por_tramos`
(`app_dash.py`): consolidar las reinversiones Negociables fungibles ANTES de
persistir el snapshot del tramo, dentro de un `try/except` fail-safe.

En lugar de arrancar el orquestador completo del dashboard (que requiere la DB
real y corridas largas), se prueba el MISMO comportamiento de forma focalizada
(approach (a) del diseño):

  1. Se construye un `df_proy` sintético que mezcla:
       - Negociables fungibles (dos grupos que deben colapsar),
       - un título a TIR `Vencimiento` y uno `Disp. Venta` (intocables, Req 2.1/2.2),
       - una fila no-RF `RENTA_VARIABLE` (intocable, Req 2.4),
       - un Negociable subyacente de swap con `id_cobertura` (intocable, Req 2.5).
  2. Se replica EXACTAMENTE la costura 5c:
       `df_proy, _ = ptr.consolidar_negociables_fungibles(df_proy)` dentro de un
       `try/except`, seguido de `ptr.persistir_snapshot_proy(conn, df_proy, corte)`.
  3. Se persiste en una DB SQLite TEMPORAL (`tmp_path`), se recarga con
       `ptr.cargar_snapshot_proy(...)` y se asertan las invariantes del snapshot
       persistido (Req 6.3).
  4. Caso fallback (Req 6.5): se fuerza una excepción en la consolidación
       (monkeypatch) y se verifica que el `except` persiste el conjunto SIN
       consolidar, sin abortar.

No se usan mocks para la lógica bajo prueba: la consolidación y la persistencia
son reales; solo en el caso de fallback se inyecta una variante de la
consolidación que lanza, para simular el fallo de datos degenerados que el
`except` debe absorber.

_Requirements: 6.3, 6.5_
"""

import sqlite3
from datetime import date

import pandas as pd
import pytest

import proyeccion_tramos as ptr
from proyeccion_tramos import (
    SnapshotAusenteError,
    cargar_snapshot_proy,
    consolidar_negociables_fungibles,
    persistir_snapshot_proy,
)


# ── Subconjunto estable de la allowlist `_COLUMNAS_PROY` usado por el snapshot. ──
_COLS = [
    "id_posicion", "especie", "emisor", "isin", "moneda", "fecha_emision",
    "fecha_vencimiento", "nominal", "valor_mercado", "valor_tir", "tasa_compra",
    "tir", "clasificacion_contable", "clase_origen", "id_cobertura",
    "tasa_referencia", "tasa_facial", "tipo_tasa", "periodicidad",
    "asset_class_definitivo",
]


def _fila(
    id_posicion,
    *,
    especie="TFIT",
    isin="CO1",
    moneda="COP",
    fecha_emision="2026-06-30",
    fecha_vencimiento="2032-01-01",
    nominal=1000.0,
    valor_mercado=1000.0,
    valor_tir=1000.0,
    tasa_compra=8.0,
    clasificacion_contable="Negociable",
    clase_origen="TITULO",
    id_cobertura="",
    tasa_referencia="",
    tasa_facial=7.0,
    tipo_tasa="EFECTIVO",
    periodicidad="ANUAL",
    asset_class_definitivo="DP LP",
):
    """Fila (dict) de cierre de tramo con defaults de un Negociable fungible RF."""
    return {
        "id_posicion": id_posicion,
        "especie": especie,
        "emisor": "EMISOR_X",
        "isin": isin,
        "moneda": moneda,
        "fecha_emision": fecha_emision,
        "fecha_vencimiento": fecha_vencimiento,
        "nominal": nominal,
        "valor_mercado": valor_mercado,
        "valor_tir": valor_tir,
        "tasa_compra": tasa_compra,
        "tir": 0.085,
        "clasificacion_contable": clasificacion_contable,
        "clase_origen": clase_origen,
        "id_cobertura": id_cobertura,
        "tasa_referencia": tasa_referencia,
        "tasa_facial": tasa_facial,
        "tipo_tasa": tipo_tasa,
        "periodicidad": periodicidad,
        "asset_class_definitivo": asset_class_definitivo,
    }


def _df_proy_mezclado():
    """
    Construye un `df_proy` sintético que mezcla todas las categorías relevantes:
      - Grupo A: 3 Negociables fungibles (misma clave) → colapsan a 1.
      - Grupo B: 2 Negociables fungibles (otra especie/vencimiento) → colapsan a 1.
      - Un Negociable singleton (clave única) → se mantiene sin cambios.
      - Un título a TIR `Vencimiento` y uno `Disp. Venta` → intocables.
      - Una fila no-RF `RENTA_VARIABLE` → intocable.
      - Un Negociable subyacente de swap (`id_cobertura`) → intocable.
    """
    filas = [
        # Grupo A (especie TFIT, isin CO1, vto 2032-01-01, tasa 7.0).
        _fila("A1", nominal=1000.0, valor_mercado=1010.0, valor_tir=1005.0,
              fecha_emision="2026-03-31"),
        _fila("A2", nominal=2000.0, valor_mercado=2020.0, valor_tir=2010.0,
              fecha_emision="2026-06-30"),
        _fila("A3", nominal=3000.0, valor_mercado=3030.0, valor_tir=3015.0,
              fecha_emision="2026-09-30"),
        # Grupo B (especie TFIP, isin CO2, vto 2030-07-15, tasa 5.5).
        _fila("B1", especie="TFIP", isin="CO2", fecha_vencimiento="2030-07-15",
              tasa_facial=5.5, nominal=500.0, valor_mercado=505.0, valor_tir=502.0),
        _fila("B2", especie="TFIP", isin="CO2", fecha_vencimiento="2030-07-15",
              tasa_facial=5.5, nominal=1500.0, valor_mercado=1515.0, valor_tir=1506.0),
        # Negociable singleton (clave única) → sin cambios (Req 1.6).
        _fila("SOLO", especie="TFIU", isin="CO3", fecha_vencimiento="2029-02-01",
              tasa_facial=9.0, nominal=777.0, valor_mercado=780.0, valor_tir=778.0),
        # Título a TIR Vencimiento (intocable, Req 2.1/2.2).
        _fila("VC1", clasificacion_contable="Vencimiento", tasa_compra=6.2,
              nominal=4000.0, valor_mercado=4100.0, valor_tir=4050.0),
        # Título a TIR Disp. Venta (intocable).
        _fila("DV1", clasificacion_contable="Disp. Venta", tasa_compra=6.3,
              nominal=5000.0, valor_mercado=5100.0, valor_tir=5050.0),
        # Fila no-RF Renta Variable (intocable, Req 2.4). Clasificada Negociable para
        # demostrar que la exclusión es por clase_origen, no por clasificación.
        _fila("RV1", clase_origen="RENTA_VARIABLE", clasificacion_contable="Negociable",
              nominal=9000.0, valor_mercado=9500.0, valor_tir=9500.0),
        # Negociable subyacente de swap (intocable, Req 2.5).
        _fila("SWP1", id_cobertura="SWAP_123", nominal=6000.0, valor_mercado=6050.0,
              valor_tir=6025.0),
    ]
    return pd.DataFrame(filas, columns=_COLS)


def _ejecutar_seam_5c(conn, df_proy, fecha_corte, *, consolidar=consolidar_negociables_fungibles):
    """
    Réplica fiel del bloque 5c de `_ejecutar_escenario_por_tramos`:
    consolida dentro de un `try/except` fail-safe y persiste el resultado
    (consolidado en éxito, o SIN consolidar en el `except`), tal como el
    orquestador real. Devuelve el número de filas persistidas.

    El parámetro `consolidar` permite inyectar una variante que lance, para
    ejercitar el camino de fallback (Req 6.5) sin tocar `app_dash.py`.
    """
    try:
        df_proy, _resumen = consolidar(df_proy)
    except Exception:
        # Fallback no destructivo: se persiste el conjunto SIN consolidar.
        pass
    return persistir_snapshot_proy(conn, df_proy, fecha_corte)


# ==============================================================================
# Caso 1 — Éxito: el snapshot persistido contiene los consolidados en lugar de
#          los Negociables individuales, y deja intactas filas a TIR / no-RF.
# ==============================================================================

def test_seam_consolida_y_persiste_snapshot(tmp_path):
    corte = date(2027, 12, 31)
    df_proy = _df_proy_mezclado()
    # Totales de entrada (deben conservarse tras la consolidación).
    vm_total_in = df_proy["valor_mercado"].sum()
    nominal_total_in = df_proy["nominal"].sum()

    ruta = tmp_path / "snap_consolida.db"
    conn = sqlite3.connect(str(ruta))
    try:
        n_persistidas = _ejecutar_seam_5c(conn, df_proy, corte)
        df_rl = cargar_snapshot_proy(conn, corte)
    finally:
        conn.close()

    ids = set(df_rl["id_posicion"].astype(str))

    # 1) Los Negociables individuales de cada grupo YA NO están en el snapshot…
    for individual in ("A1", "A2", "A3", "B1", "B2"):
        assert individual not in ids, f"{individual} debería haberse consolidado"

    # 2) …reemplazados por consolidados `CONS_*` (uno por grupo, 2 grupos).
    consolidados = [i for i in ids if i.startswith("CONS_")]
    assert len(consolidados) == 2

    # 3) Las filas a TIR y no-RF quedan intactas (mismo id_posicion presente, Req 6.3).
    for intocable in ("VC1", "DV1", "RV1", "SWP1", "SOLO"):
        assert intocable in ids

    # 4) A TIR conserva su tasa_compra individual (no se agrupó ni se tocó).
    vc = df_rl[df_rl["id_posicion"] == "VC1"].iloc[0]
    dv = df_rl[df_rl["id_posicion"] == "DV1"].iloc[0]
    assert float(vc["tasa_compra"]) == pytest.approx(6.2)
    assert float(dv["tasa_compra"]) == pytest.approx(6.3)

    # 5) Conservación de totales: VM y nominal globales no cambian con la consolidación.
    assert df_rl["valor_mercado"].sum() == pytest.approx(vm_total_in)
    assert df_rl["nominal"].sum() == pytest.approx(nominal_total_in)

    # 6) El consolidado del grupo A suma los nominales/VM de A1+A2+A3.
    #    (Se identifica por su especie/vencimiento, invariantes de la clave.)
    cons_a = df_rl[
        (df_rl["id_posicion"].astype(str).str.startswith("CONS_"))
        & (df_rl["especie"] == "TFIT")
    ]
    assert len(cons_a) == 1
    assert float(cons_a.iloc[0]["nominal"]) == pytest.approx(6000.0)
    assert float(cons_a.iloc[0]["valor_mercado"]) == pytest.approx(6060.0)

    # 7) El conteo persistido: 10 filas de entrada → 7 de salida (3+2 colapsan a 1+1).
    assert n_persistidas == 7
    assert len(df_rl) == 7


# ==============================================================================
# Caso 2 — Fallback (Req 6.5): una excepción en la consolidación persiste el
#          conjunto SIN consolidar, sin abortar el encadenamiento.
# ==============================================================================

def test_seam_fallback_persiste_sin_consolidar_ante_excepcion(tmp_path):
    corte = date(2027, 12, 31)
    df_proy = _df_proy_mezclado()
    n_entrada = len(df_proy)

    def _consolidar_que_falla(_df, **_kw):
        # Simula el fallo por datos degenerados que el `except` debe absorber.
        raise RuntimeError("fallo sintético de consolidación")

    ruta = tmp_path / "snap_fallback.db"
    conn = sqlite3.connect(str(ruta))
    try:
        # No debe lanzar: el `try/except` del seam absorbe la excepción.
        n_persistidas = _ejecutar_seam_5c(
            conn, df_proy, corte, consolidar=_consolidar_que_falla
        )
        df_rl = cargar_snapshot_proy(conn, corte)
    finally:
        conn.close()

    ids = set(df_rl["id_posicion"].astype(str))

    # El conjunto se persiste SIN consolidar: TODOS los ids originales presentes…
    for original in ("A1", "A2", "A3", "B1", "B2", "SOLO", "VC1", "DV1", "RV1", "SWP1"):
        assert original in ids
    # …y NINGÚN consolidado (la consolidación no se aplicó).
    assert not any(i.startswith("CONS_") for i in ids)

    # Conteo intacto: se persistieron todas las filas de entrada.
    assert n_persistidas == n_entrada
    assert len(df_rl) == n_entrada


# ==============================================================================
# Caso 3 — Si no hay snapshot para el corte, cargar_snapshot_proy avisa (sanidad).
# ==============================================================================

def test_corte_sin_snapshot_lanza_snapshot_ausente(tmp_path):
    ruta = tmp_path / "snap_vacio.db"
    conn = sqlite3.connect(str(ruta))
    try:
        with pytest.raises(SnapshotAusenteError):
            cargar_snapshot_proy(conn, date(2099, 12, 31))
    finally:
        conn.close()
