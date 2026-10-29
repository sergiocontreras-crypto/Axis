# -*- coding: utf-8 -*-
"""
Test del round-trip de `id_cobertura` en el snapshot por tramos (Defecto 4 — Tarea 9).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix), Fase 2, Defecto 4.
Tarea 9 del plan de implementación.

Property 5 (Expected Behavior): "Subyacentes de Swaps" separado en TODOS los cortes.

**Validates: Requirements 4.1, 4bis**

Contexto del bug
----------------
La categoría "Subyacentes de Swaps" del resumen por corte
(`tools/export_resumen_tramos.py::_categoria`, flag `es_subyacente`) depende de
`Titulo.id_cobertura`, que `tools/detalle_pyg_tramo.py::construir_rf_detalle` deriva como
`es_subyacente = bool(str(t.id_cobertura or "").strip())`.

En el tramo 0 `id_cobertura` viene de `portafolio_clasificado`; pero en los tramos i>0 el
round-trip del snapshot NO preservaba `id_cobertura`:
  (1) no estaba en la allowlist `_COLUMNAS_PROY` (`scripts/proyeccion_tramos.py`),
  (2) `app_dash._titulos_a_dataframe_base` (rama i>0) no lo emitía, y
  (3) `cargar_titulos_desde_snapshot` no lo pasaba al constructor `Titulo(...)`.

Como consecuencia, los subyacentes de swaps caían en "RF Internacional" en 2027/2028.

Este test siembra un snapshot (vía `persistir_snapshot_proy`) de un DataFrame que incluye
una fila con `id_cobertura` no vacío (un subyacente), luego `cargar_snapshot_proy` +
`cargar_titulos_desde_snapshot`, y asevera que el `Titulo` reconstruido conserva
`id_cobertura` no vacío (y por tanto `es_subyacente=True`).

ANTES del fix este test FALLA (el `id_cobertura` reconstruido queda "").

Aislamiento (regla del proyecto: NUNCA tocar la `portafolio.db` real): se usa una DB SQLite
TEMPORAL (`tmp_path`); se reutiliza el patrón de siembra/round-trip de
`tests/test_integracion_vm_cierre_usd_multitramo.py`.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

import proyeccion_tramos as ptr  # noqa: E402


# ==============================================================================
# Datos del caso: un subyacente (id_cobertura no vacío) y un título normal (vacío)
# ==============================================================================

_ID_SUBYACENTE = "SUBY_001"
_ID_COBERTURA = "SWAP_1517"  # id del swap que cubre el subyacente (no vacío)
_ID_NORMAL = "RF_NORMAL_002"


def _df_cierre_tramo():
    """
    DataFrame de cierre del tramo con el esquema de la allowlist `_COLUMNAS_PROY`:
      - Una fila RF que es SUBYACENTE de swap (`id_cobertura` = _ID_COBERTURA).
      - Una fila RF normal (`id_cobertura` vacío).

    Ambas son títulos de RF (clase_origen 'TITULO', asset_class de RF) para que
    `cargar_titulos_desde_snapshot` las reconstruya como `Titulo`.
    """
    base = {
        "especie": "TF 10Y",
        "emisor": "ECOPETROL",
        "moneda": "USD",  # internacional: sin id_cobertura caería en "RF Internacional"
        "fecha_emision": "2015-06-15",
        "fecha_vencimiento": "2030-06-15",
        "nominal": 1_000_000.0,
        "valor_mercado": 1_000_000.0,
        "valor_tir": 1_000_000.0,
        "tasa_compra": 5.0,
        "tir": 5.0,
        "asset_class_definitivo": "LEGATRUU",
        "tasa_referencia": "",
        "periodicidad": "SEMESTRAL",
        "tasa_facial": "5.875",
        "tipo_tasa": "NOMINAL",
        "base_anual": 360,
        "uso_tasa": "I",
        "clasificacion_contable": "Negociable",
        "compania": "C",
        "ramo": "R",
        "portafolio": "P",
        "clase_origen": "TITULO",
        "spread_mercado": 8.0,
    }
    fila_subyacente = dict(base, id_posicion=_ID_SUBYACENTE, isin="US_SUBY",
                           id_cobertura=_ID_COBERTURA)
    fila_normal = dict(base, id_posicion=_ID_NORMAL, isin="US_NORMAL",
                       id_cobertura="")
    return pd.DataFrame([fila_subyacente, fila_normal])


# ==============================================================================
# TEST: id_cobertura sobrevive el round-trip persistir → cargar → reconstruir
# ==============================================================================

def test_id_cobertura_sobrevive_roundtrip_snapshot(tmp_path):
    """
    Siembra un snapshot con un subyacente (id_cobertura no vacío), lo persiste y lo
    reconstruye. El `Titulo` reconstruido DEBE conservar `id_cobertura` no vacío.

    ANTES del fix: FALLA (id_cobertura == "" tras el round-trip en i>0).
    DESPUÉS del fix: PASA (id_cobertura preservado).
    """
    ruta = tmp_path / "id_cobertura_roundtrip.db"
    corte = date(2027, 12, 31)

    conn = sqlite3.connect(str(ruta))
    try:
        df_cierre = _df_cierre_tramo()
        n = ptr.persistir_snapshot_proy(conn, df_cierre, corte)
        assert n == 2, f"se esperaban 2 filas persistidas, se persistieron {n}"

        df_cargado = ptr.cargar_snapshot_proy(conn, corte)
        titulos = ptr.cargar_titulos_desde_snapshot(df_cargado)
    finally:
        conn.close()

    por_id = {str(t.id_posicion): t for t in titulos}
    assert _ID_SUBYACENTE in por_id, (
        f"el subyacente {_ID_SUBYACENTE} no se reconstruyó (ids: {sorted(por_id)})"
    )
    assert _ID_NORMAL in por_id, (
        f"el título normal {_ID_NORMAL} no se reconstruyó (ids: {sorted(por_id)})"
    )

    t_suby = por_id[_ID_SUBYACENTE]
    t_normal = por_id[_ID_NORMAL]

    # NÚCLEO DEL BUG: el subyacente debe conservar su id_cobertura tras el round-trip.
    assert str(getattr(t_suby, "id_cobertura", "") or "").strip() == _ID_COBERTURA, (
        "El round-trip del snapshot NO preservó id_cobertura del subyacente "
        f"(obtenido: {getattr(t_suby, 'id_cobertura', None)!r}; esperado: {_ID_COBERTURA!r}). "
        "Sin id_cobertura, el subyacente cae en 'RF Internacional' en los tramos i>0."
    )

    # El título normal (sin cobertura) debe seguir con id_cobertura vacío.
    assert str(getattr(t_normal, "id_cobertura", "") or "").strip() == "", (
        "El título normal no debe tener id_cobertura tras el round-trip "
        f"(obtenido: {getattr(t_normal, 'id_cobertura', None)!r})."
    )


def test_es_subyacente_tras_roundtrip(tmp_path):
    """
    End-to-end (opcional del plan): tras el round-trip, la derivación de
    `construir_rf_detalle` marca `es_subyacente=True` para el subyacente y `False` para el
    título normal, de modo que el resumen por corte lo separe de "RF Internacional".

    Se replica la MISMA derivación que `tools/detalle_pyg_tramo.py::construir_rf_detalle`:
        es_subyacente = bool(str(t.id_cobertura or "").strip())
    """
    ruta = tmp_path / "es_subyacente_roundtrip.db"
    corte = date(2027, 12, 31)

    conn = sqlite3.connect(str(ruta))
    try:
        ptr.persistir_snapshot_proy(conn, _df_cierre_tramo(), corte)
        df_cargado = ptr.cargar_snapshot_proy(conn, corte)
        titulos = ptr.cargar_titulos_desde_snapshot(df_cargado)
    finally:
        conn.close()

    por_id = {str(t.id_posicion): t for t in titulos}

    def _es_subyacente(t):
        return bool(str(getattr(t, "id_cobertura", "") or "").strip())

    assert _es_subyacente(por_id[_ID_SUBYACENTE]) is True, (
        "El subyacente no quedó marcado como es_subyacente tras el round-trip "
        "(caería en 'RF Internacional' en 2027/2028)."
    )
    assert _es_subyacente(por_id[_ID_NORMAL]) is False, (
        "El título normal no debe marcarse como es_subyacente."
    )
