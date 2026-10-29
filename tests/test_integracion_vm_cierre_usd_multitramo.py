# -*- coding: utf-8 -*-
"""
Test de INTEGRACIÓN multi-tramo del VM de cierre USD en COP (blindaje — Tarea 5.1).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Tarea 5.1 del plan de implementación (Defecto 2 reconvertido: NO reproducible → blindaje).

Property 2 (Expected Behavior): el VM de cierre de los títulos de RF internacional en USD
(p.ej. YANKEE) queda registrado en escala COP (×TRM), consistente con el VM inicial, en el
snapshot oficial `portafolio_clasificado_proy`, en CADA tramo (incluido i>0, multi-año).

**Validates: Requirements 2.8, 2.9, 2.11**

Motivación — brecha de cobertura que cierra este test
-----------------------------------------------------
El test de la Tarea 2 (`tests/test_property_vm_cierre_usd_por_tramos.py`) SOLO recorre el
pipeline de armado del snapshot aislado:

    _construir_df_proy_rf → _enriquecer_snapshot_tramo → _finalizar_df_proy

partiendo de un título cuyo `valor_mercado` ya está fijado a mano en COP. NO ejercita (a) la
RUTA REAL donde el diseño ubicaba el riesgo del Defecto 2 — el bucle mensual del motor
(`proyectar_portafolio`, que es quien asigna `titulo.valor_mercado = valorar_titulo(...,
trm=_senda_trm[mes])`) — ni (b) el ROUND-TRIP de persistencia por tramos y, crucialmente, (c)
el ENCADENAMIENTO del tramo i>0 (`_titulos_a_dataframe_base` con `tramo.indice > 0`, que
reconstruye la base del tramo siguiente desde los títulos del snapshot).

Este test de integración cierra esa brecha: corre el MOTOR REAL por tramo con un título
YANKEE/USD y recorre el round-trip COMPLETO del orquestador por tramos, igual que
`_ejecutar_escenario_por_tramos`:

    _titulos_a_dataframe_base          # (i>0 → reconstruye base desde los títulos del snapshot)
      → _construir_df_proy_rf          # mapea t.valor_mercado (COP) → df_proy
      → _enriquecer_snapshot_tramo
      → _finalizar_df_proy             # reclasifica (preserva VM)
      → persistir_snapshot_proy        # escribe portafolio_clasificado_proy (allowlist)
      → cargar_snapshot_proy           # relee el snapshot del corte
      → cargar_titulos_desde_snapshot  # reconstruye Titulos → base del tramo SIGUIENTE

y asevera en CADA tramo (incluido el tramo 1, multi-año, cuya base se arma vía
`_titulos_a_dataframe_base` con i>0) que:

    valor_mercado ≈ nominal × precio/100 × trm_cierre   (escala COP)

es decir, que el VM NO queda en escala USD (≈ COP/TRM). La aserción es RELATIVA (COP ≈
USD×TRM, y claramente ≠ COP/TRM), robusta frente a los parámetros exactos de valoración
(mismo criterio que el test de la Tarea 2): no fija un número absoluto sino la ESCALA.

Aislamiento (regla del proyecto: NUNCA tocar la `portafolio.db` real)
---------------------------------------------------------------------
- Se usa una DB SQLite TEMPORAL (`tmp_path`) y se siembra `portafolio_clasificado` con un
  título YANKEE/USD (moneda USD, tipo YANKEE), siguiendo el patrón de
  `tests/test_property_rf_mensual_por_tramos.py` y `tests/test_property_vm_cierre_usd_por_tramos.py`.
- El motor REAL `proyectar_portafolio` corre SIN la DB real: su bloque de reservas/universo
  (`hist_pip`, etc.) está envuelto en try/except (fail-safe) y, pasando `mapa_reservas={}`,
  no requiere `hist_*`. Verificado empíricamente: el motor revalúa el YANKEE/USD y deja
  `titulo.valor_mercado` en COP (= nominal × precio/100 × trm) sin acceder a la DB real.
- Las funciones de pipeline de snapshot (`app_dash._construir_df_proy_rf`, etc.) y de
  persistencia (`proyeccion_tramos.persistir_snapshot_proy`, etc.) operan sobre la conexión
  temporal que se les pasa; no abren la DB real.

EXPECTED OUTCOME: el test PASA sobre el código actual (confirma que el Defecto 2 no se
reproduce en la ruta real multi-tramo y BLINDA contra regresiones futuras).

Relación con la mitigación de `tools/detalle_pyg_tramo.py` (Tarea 5.2)
---------------------------------------------------------------------
Este test verifica el VM de cierre leído DIRECTAMENTE del snapshot oficial persistido
(`cargar_snapshot_proy`), SIN pasar por `construir_rf_detalle` ni por su re-valoración de
moneda extranjera. Es decir, demuestra que el P&G por diferencia de VM de la RF internacional
es coherente usando el snapshot oficial, con independencia de la mitigación: el VM ya está en
COP en la fuente. Por eso la re-valoración de `detalle_pyg_tramo.py` es una defensa IDEMPOTENTE
(coincide con el VM del snapshot) que se CONSERVA como red de seguridad, no un fix de un bug
activo. Decisión documentada en la Tarea 5.2 del spec.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date

import pandas as pd
import pytest

# El import de app_dash es pesado; si no está disponible en el entorno se salta con gracia
# (mismo patrón que la suite del Defecto 1 / Defecto 2) sin romper el resto de la suite.
app_dash = pytest.importorskip("app_dash")

import motor_proyeccion as mp  # noqa: E402
from motor_proyeccion import valorar_titulo  # noqa: E402
import proyeccion_tramos as ptr  # noqa: E402


# ==============================================================================
# PARÁMETROS DEL CASO (coherentes con el caso documentado id 817856)
# ==============================================================================
#
# La ASERCIÓN es relativa (COP ≈ USD×TRM, no sobre el número absoluto), de modo que el test
# es robusto frente a los parámetros exactos de valoración (igual criterio que el test de la
# Tarea 2). Se usan nominal/tasa/TRM representativos de un YANKEE/USD.
_ID_YANKEE = "817856"
_ESPECIE_YANKEE = "BN ECOPETROL USD"
_NOMINAL_USD = 3_600_000.0
_TASA_CUPON = 5.875
_SPREAD_MERCADO = 8.0

_INICIO_HORIZONTE = date(2026, 1, 1)
_FIN_HORIZONTE = date(2027, 12, 31)  # dos tramos (frontera de año → ejercita i>0)

# Senda de TRM por tramo: (trm_inicio, trm_cierre). El tramo 1 arranca en el cierre del
# tramo 0 (encadenamiento de TRM), igual que el orquestador real.
_SENDA_TRM_POR_TRAMO = {
    0: (3900.0, 4100.0),
    1: (4100.0, 4300.0),
}

# Tolerancia relativa de la aserción de escala COP. El VM que deja el bucle mensual del
# motor difiere ~0,02% del `valorar_titulo` evaluado directamente a la fecha de cierre
# (distinta fecha de valuación intra-mes + shift gradual del spread); por eso se usa una
# tolerancia holgada que verifica la ESCALA, no la igualdad exacta.
_TOL_REL_COP = 0.02


# ==============================================================================
# Construcción del título YANKEE/USD y de la base `portafolio_clasificado`
# ==============================================================================

def _titulo_yankee_usd(trm):
    """
    Construye un `Titulo` YANKEE/USD coherente con `cargar_portafolio_proyeccion`
    (moneda USD → tipo_bono YANKEE) con `valor_mercado` inicial en COP, EXACTAMENTE como lo
    deja el motor real (`valorar_titulo(..., trm)` devuelve COP = nominal × precio/100 × trm).
    """
    t = mp.Titulo(
        id_posicion=_ID_YANKEE, especie=_ESPECIE_YANKEE, emisor="ECOPETROL",
        isin=f"US{_ID_YANKEE}", moneda="USD",
        fecha_emision=date(2015, 6, 15), fecha_vencimiento=date(2030, 6, 15),
        nominal=_NOMINAL_USD, tasa_cupon=_TASA_CUPON, spread_ipc=0.0, tipo_bono="YANKEE",
        frecuencia="semestral", tipo_tasa="NOMINAL", base_anual=360, uso_tasa="I",
        clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
        asset_class="LEGATRUU", tasa_compra=_TASA_CUPON, tasa_mercado=_TASA_CUPON,
        valor_mercado=0.0, valor_tir=0.0,
    )
    t.spread_mercado = _SPREAD_MERCADO
    vm_cop = valorar_titulo(
        titulo=t, tasa_mercado_nueva=t.spread_mercado,
        fecha_valuacion=_INICIO_HORIZONTE, trm=trm,
    )
    t.valor_mercado = vm_cop
    t.valor_tir = vm_cop
    return t


def _sembrar_portafolio_clasificado(conn, titulo):
    """Siembra `portafolio_clasificado` (base del df_proy del PRIMER tramo, i==0) con el
    esquema que consume `_titulos_a_dataframe_base`/`_construir_df_proy_rf`."""
    df = pd.DataFrame({
        "id_posicion": [titulo.id_posicion], "especie": [titulo.especie],
        "emisor": [titulo.emisor], "isin": [titulo.isin], "moneda": ["USD"],
        "fecha_emision": [str(titulo.fecha_emision)],
        "fecha_vencimiento": [str(titulo.fecha_vencimiento)],
        "nominal": [titulo.nominal], "valor_mercado": [titulo.valor_mercado],
        "valor_tir": [titulo.valor_tir], "tasa_compra": [titulo.tasa_compra],
        "tir": [titulo.tasa_mercado], "asset_class_definitivo": [titulo.asset_class],
        "tasa_referencia": [""], "periodicidad": [titulo.frecuencia],
        "tasa_facial": [str(titulo.tasa_cupon)],
        "clasificacion_contable": [titulo.clasificacion],
        "clase_origen": ["TITULO"], "spread_mercado": [titulo.spread_mercado],
    })
    df.to_sql("portafolio_clasificado", conn, if_exists="replace", index=False)
    conn.commit()


# ==============================================================================
# Utilidades de recorrido del round-trip real por tramos
# ==============================================================================

def _vm_de(df, id_pos):
    """Extrae el `valor_mercado` (float) de la fila con `id_posicion == id_pos`."""
    if df is None or len(df) == 0 or "id_posicion" not in df.columns:
        return None
    sub = df[df["id_posicion"].astype(str) == str(id_pos)]
    if sub.empty or "valor_mercado" not in sub.columns:
        return None
    return float(pd.to_numeric(sub["valor_mercado"].iloc[0], errors="coerce"))


def _correr_tramo_round_trip(conn, port, tramo, trm_ini, trm_fin):
    """
    Ejercita la RUTA REAL de un tramo del orquestador por tramos, de principio a fin:

        proyectar_portafolio (MOTOR real)             # deja t.valor_mercado en COP
          → _titulos_a_dataframe_base(port, conn, tramo)  # i>0: base desde el snapshot
          → _construir_df_proy_rf
          → _enriquecer_snapshot_tramo
          → _finalizar_df_proy
          → persistir_snapshot_proy
          → cargar_snapshot_proy
          → cargar_titulos_desde_snapshot            # base del tramo SIGUIENTE

    Devuelve (vm_snapshot_persistido, titulos_siguiente), donde `vm_snapshot_persistido` es
    el VM del título YANKEE/USD leído DESDE el snapshot persistido (ida y vuelta por SQLite).
    """
    # 1) Motor REAL del tramo: revalúa y deja t.valor_mercado en COP (= valorar_titulo×TRM).
    mp.proyectar_portafolio(
        portafolio=port,
        fecha_inicio=tramo.fecha_inicio,
        meses_horizonte=tramo.sub_horizonte,
        sendas={},
        trm=trm_ini, trm_cierre=trm_fin,
        mapa_reservas={},
    )

    # 2) Armado del snapshot del tramo (misma secuencia que _ejecutar_escenario_por_tramos).
    #    Para el tramo i>0, _titulos_a_dataframe_base reconstruye la base desde `port`
    #    (los títulos encadenados del snapshot del tramo anterior) — ruta real del i>0.
    df_base = app_dash._titulos_a_dataframe_base(port, conn, tramo)
    df_proy = app_dash._construir_df_proy_rf(df_base, port, tramo.fecha_corte)[0]
    df_proy = app_dash._enriquecer_snapshot_tramo(df_proy, port, tramo.fecha_corte)
    df_proy = app_dash._finalizar_df_proy(df_proy, tramo.fecha_corte)

    # 3) Round-trip de persistencia: escribir → releer → reconstruir títulos.
    ptr.persistir_snapshot_proy(conn, df_proy, tramo.fecha_corte)
    df_cargado = ptr.cargar_snapshot_proy(conn, tramo.fecha_corte)
    titulos_siguiente = ptr.cargar_titulos_desde_snapshot(df_cargado)

    # VM del YANKEE/USD leído DESDE el snapshot persistido (lo que registra el oficial).
    vm_snapshot = _vm_de(df_cargado, _ID_YANKEE)
    return vm_snapshot, titulos_siguiente


def _assert_escala_cop(vm_snapshot, cop_esperado, trm, etiqueta):
    """
    Asevera que `vm_snapshot` está en escala COP (≈ cop_esperado = nominal × precio/100 ×
    trm) y NO en escala USD (≈ cop_esperado / trm). Aserción RELATIVA (sobre la escala),
    robusta frente a los parámetros exactos de valoración.
    """
    assert cop_esperado is not None and cop_esperado > 0, (
        f"[{etiqueta}] la valoración unitaria YANKEE no produjo un VM COP > 0"
    )
    assert vm_snapshot is not None, (
        f"[{etiqueta}] el título YANKEE/USD {_ID_YANKEE} no quedó en el snapshot persistido"
    )

    vm_usd = cop_esperado / trm
    diag = (
        f"[{etiqueta}] trm={trm:,.2f}  vm_snapshot={vm_snapshot:,.2f}  "
        f"COP_esperado={cop_esperado:,.2f} (ratio={vm_snapshot / cop_esperado:.4f})  "
        f"escala_USD(COP/TRM)={vm_usd:,.2f}"
    )

    # (1) El VM de cierre persistido está en escala COP (consistente con el VM inicial).
    assert math.isclose(vm_snapshot, cop_esperado, rel_tol=_TOL_REL_COP), (
        "VM de cierre USD NO está en escala COP (falta ×TRM) en la ruta real por tramos.\n"
        + diag
    )
    # (2) En particular, NO está en escala USD (≈ COP / TRM).
    assert not math.isclose(vm_snapshot, vm_usd, rel_tol=0.05), (
        "VM de cierre USD quedó en escala USD (~ VM_COP / TRM) en lugar de COP.\n" + diag
    )


# ==============================================================================
# Fixture: DB temporal con el YANKEE/USD sembrado (NUNCA la DB real)
# ==============================================================================

@pytest.fixture
def db_yankee_tmp(tmp_path, monkeypatch):
    """
    Crea una DB SQLite TEMPORAL con `portafolio_clasificado` sembrado con un YANKEE/USD, y
    aísla `app_dash` para que cualquier acceso incidental use la DB temporal (no la real).
    Devuelve (ruta_db, titulo_inicial).
    """
    import compras_ventas_reservas as cvr

    ruta = tmp_path / "integracion_vm_usd_multitramo.db"

    def _get_conn_tmp(*a, **k):
        return sqlite3.connect(str(ruta))

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp, raising=False)
    monkeypatch.setattr(app_dash, "DB_PATH", str(ruta), raising=False)
    # El motor real degrada con gracia sin hist_*; aun así, aislamos la carga auxiliar.
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {}, raising=False)

    titulo = _titulo_yankee_usd(trm=_SENDA_TRM_POR_TRAMO[0][0])
    conn = sqlite3.connect(str(ruta))
    try:
        _sembrar_portafolio_clasificado(conn, titulo)
    finally:
        conn.close()
    return ruta, titulo


# ==============================================================================
# TEST DE INTEGRACIÓN: VM de cierre USD en COP en CADA tramo (tramo 0 e i>0)
# ==============================================================================

def test_integracion_vm_cierre_usd_cop_multitramo(db_yankee_tmp):
    """
    Corre la ruta REAL multi-tramo (motor + round-trip de persistencia) con un YANKEE/USD y
    verifica que el VM de cierre persistido en `portafolio_clasificado_proy` está en escala
    COP (≈ nominal × precio/100 × trm_cierre) en CADA tramo, incluido el tramo 1 (i>0, cuya
    base se reconstruye vía `_titulos_a_dataframe_base` desde el snapshot del tramo 0).

    EXPECTED OUTCOME: PASA sobre el código actual (D2 no reproducible; blindaje de regresión).
    """
    ruta, titulo = db_yankee_tmp
    tramos = ptr.calcular_tramos(_INICIO_HORIZONTE, _FIN_HORIZONTE)

    # Debe haber al menos dos tramos para ejercitar el encadenamiento i>0 (frontera de año).
    assert len(tramos) >= 2, (
        f"el horizonte {_INICIO_HORIZONTE}..{_FIN_HORIZONTE} debe producir ≥2 tramos "
        f"(obtenidos {len(tramos)}) para ejercitar la ruta i>0"
    )

    conn = sqlite3.connect(str(ruta))
    try:
        port = [titulo]
        for tramo in tramos:
            trm_ini, trm_fin = _SENDA_TRM_POR_TRAMO[tramo.indice]

            # VM de cierre ESPERADO en COP (escala consistente con el VM inicial): el valor
            # que devuelve la valoración unitaria YANKEE a la fecha de cierre con trm_fin.
            # Se evalúa sobre el título ANTES de que el motor lo mute (los parámetros de
            # valoración —nominal, tasa, spread, fechas— son los mismos del título vivo).
            cop_esperado = valorar_titulo(
                titulo=port[0], tasa_mercado_nueva=port[0].spread_mercado,
                fecha_valuacion=tramo.fecha_corte, trm=trm_fin,
            )

            vm_snapshot, titulos_siguiente = _correr_tramo_round_trip(
                conn, port, tramo, trm_ini, trm_fin,
            )

            etiqueta = (f"tramo {tramo.indice} "
                        f"({'i=0' if tramo.indice == 0 else 'i>0 (encadenado)'}) "
                        f"corte {tramo.fecha_corte}")
            _assert_escala_cop(vm_snapshot, cop_esperado, trm_fin, etiqueta)

            # El título YANKEE/USD debe reconstruirse para alimentar el tramo siguiente.
            assert titulos_siguiente, (
                f"[{etiqueta}] cargar_titulos_desde_snapshot no reconstruyó títulos RF"
            )
            ids_sig = {str(t.id_posicion) for t in titulos_siguiente}
            assert _ID_YANKEE in ids_sig, (
                f"[{etiqueta}] el YANKEE/USD {_ID_YANKEE} no sobrevivió el round-trip de "
                f"persistencia (ids reconstruidos: {sorted(ids_sig)})"
            )

            # Encadenamiento: la base del tramo siguiente son los títulos del snapshot.
            port = titulos_siguiente
    finally:
        conn.close()
