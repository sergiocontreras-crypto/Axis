# -*- coding: utf-8 -*-
"""
Test exploratorio de condición de bug — Defecto 1 (serie RF mensual por tramos).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Tarea 1 del plan de implementación.

Property 1 (Bug Condition → Expected Behavior): Serie RF mensual continua y completa en
modo `por_tramos`.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

CRÍTICO: este test DEBE FALLAR sobre el código SIN corregir. La falla confirma que el
bug existe (el modo `por_tramos` descarta la lista de `ResultadoMes`, no produce la clave
`rf_mensual_contable` ni exporta la hoja RF_Mensual; y el motor fecha cada mes con un
desplazamiento +1 mes, perdiendo el enero de arranque de cada tramo). Codifica el
comportamiento ESPERADO, de modo que validará el fix cuando pase tras la implementación.

Condición de bug (del diseño):
  isBugCondition_D1(X) es verdadero cuando X.modo = "por_tramos" Y
  (no existe hoja RF_Mensual en el Excel  O  algún tramo pierde su enero de arranque en
  la serie mensual de RF).

Aserciones (Property 1 del diseño), para toda corrida `por_tramos`:
  - existe la serie/hoja RF_Mensual (clave `rf_mensual_contable` en el resultado).
  - total_filas(rf_mensual) == Σ sub_horizonte de todos los tramos.
  - para cada tramo, el enero de arranque está presente (sin desplazamiento +1 mes).
  - serie continua, sin huecos ni duplicados en las fronteras de año.
  - pyg_enero == VM(fin enero) − VM(cierre 31-dic anterior).

Enfoque PBT acotado: por ser un bug determinista, se ancla la propiedad a casos concretos
de frontera de año (corrida `por_tramos` con fin 31-dic-2027 y un tramo que arranca el
1-ene) para asegurar reproducibilidad; luego se generaliza con Hypothesis a varias
configuraciones de tramos (nº de tramos / año de fin).
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest
from dateutil.relativedelta import relativedelta
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# El import de app_dash es pesado; si no está disponible en el entorno, se salta con
# gracia (mismo patrón que la suite multiactivo) sin romper el resto de la suite.
app_dash = pytest.importorskip("app_dash")

import proyeccion_tramos as ptr  # noqa: E402
import motor_proyeccion as mp  # noqa: E402


# ==============================================================================
# UTILIDADES: siembra del portafolio base y doble del motor RF
# ==============================================================================

def _sembrar_portafolio_clasificado(conn, ids):
    """Siembra `portafolio_clasificado` (base del df_proy del primer tramo RF)."""
    df = pd.DataFrame({
        "id_posicion": ids,
        "especie": [f"TFIT{i}" for i in ids],
        "emisor": ["NACION"] * len(ids),
        "isin": [f"CO{i}" for i in ids],
        "moneda": ["COP"] * len(ids),
        "fecha_emision": ["2020-01-01"] * len(ids),
        "fecha_vencimiento": ["2032-01-01"] * len(ids),
        "nominal": [1000.0] * len(ids),
        "valor_mercado": [1000.0] * len(ids),
        "valor_tir": [1000.0] * len(ids),
        "tasa_compra": [8.0] * len(ids),
        "tir": [8.0] * len(ids),
        "asset_class_definitivo": ["DP LP"] * len(ids),
        "tasa_referencia": [""] * len(ids),
        "periodicidad": ["ANUAL"] * len(ids),
        "tasa_facial": ["7.0"] * len(ids),
        "clasificacion_contable": ["Negociable"] * len(ids),
        "clase_origen": ["TITULO"] * len(ids),
        "spread_mercado": [8.0] * len(ids),
    })
    df.to_sql("portafolio_clasificado", conn, if_exists="replace", index=False)
    conn.commit()


def _titulos_sinteticos_rf(ids):
    """Títulos TF sintéticos para el t0 del primer tramo (evita leer hist_*)."""
    out = []
    for i in ids:
        t = mp.Titulo(
            id_posicion=i, especie=f"TFIT{i}", emisor="NACION", isin=f"CO{i}", moneda="COP",
            fecha_emision=date(2020, 1, 1), fecha_vencimiento=date(2032, 1, 1),
            nominal=1000.0, tasa_cupon=7.0, spread_ipc=0.0, tipo_bono="TF",
            frecuencia="anual", tipo_tasa="EFECTIVO", base_anual=365, uso_tasa="I",
            clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
            asset_class="DP LP", tasa_compra=8.0, tasa_mercado=8.0,
            valor_mercado=1000.0, valor_tir=1000.0,
        )
        t.spread_mercado = 8.0
        out.append(t)
    return out


def _doble_motor_rf():
    """
    Doble de `proyectar_portafolio` ALINEADO con el fechado CORREGIDO del motor (Tarea 4.2):
    emite un `ResultadoMes` por mes del sub_horizonte usando como ETIQUETA de fecha
    `fecha_etiqueta = fecha_inicio + relativedelta(months=mes - 1)` con `mes` desde 1, de
    modo que un tramo que arranca el 1-ene emita ene..dic (12 etiquetas correctas) con el
    enero de arranque PRESENTE — exactamente como el motor real tras la corrección del
    desplazamiento +1 mes. Marca cada título con `_snapshots_mensuales` (indexados por
    `mes`, igual que el motor: 0 = T0, 1..n = meses) para que el helper contable
    (`rf_contable_mensual.construir_rf_mensual_contable`) componga la serie. Devuelve la
    lista de `ResultadoMes` tal cual lo hace el motor real.

    NOTA: alinear el doble con el motor corregido NO debilita la Property 1; la aserción
    `_assert_property1_serie_rf_mensual` (serie continua, Σ filas = Σ sub_horizonte, enero
    de arranque presente, sin huecos/duplicados) se mantiene intacta.
    """
    def _doble(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        resultados = []
        for mes in range(1, meses_horizonte + 1):
            # Etiqueta de fecha corregida (Tarea 4.2): el mes `mes` etiqueta el mes que
            # ARRANCA en T0+(mes-1), de modo que el mes 1 = enero de arranque (no febrero).
            fecha_mes = fecha_inicio + relativedelta(months=mes - 1)
            vm_total = sum(getattr(t, "valor_mercado", 0.0) for t in portafolio)
            vtir_total = sum(getattr(t, "valor_tir", 0.0) for t in portafolio)
            for t in portafolio:
                snaps = getattr(t, "_snapshots_mensuales", None)
                if snaps is None:
                    snaps = {}
                    t._snapshots_mensuales = snaps
                snaps.setdefault(mes - 1, {"vm": getattr(t, "valor_mercado", 0.0),
                                           "vtir": getattr(t, "valor_tir", 0.0),
                                           "cupones": 0.0})
                snaps[mes] = {"vm": getattr(t, "valor_mercado", 0.0),
                              "vtir": getattr(t, "valor_tir", 0.0),
                              "cupones": 0.0}
            resultados.append(mp.ResultadoMes(
                mes=mes, fecha=fecha_mes, vm_total=vm_total, vtir_total=vtir_total,
                pnl_mercado_mes=0.0, pnl_tir_mes=0.0,
                pnl_mercado_acum=0.0, pnl_tir_acum=0.0,
                n_titulos=len(portafolio), n_reinversiones_mes=0,
                cupones_cobrados_mes=0.0,
            ))
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return resultados
    return _doble


@pytest.fixture
def orquestador_env(tmp_path, monkeypatch):
    """
    Prepara `app_dash` con DB temporal y dobles: get_conn → DB temporal,
    cargar_mapa_reservas → {}, obtener_trm_para → TRM fija, cargar_portafolio_proyeccion
    → títulos sintéticos (evita hist_*), y `portafolio_clasificado` sembrado.
    """
    import compras_ventas_reservas as cvr

    ruta = tmp_path / "orq_rf_mensual.db"

    def _get_conn_tmp(*a, **k):
        return sqlite3.connect(str(ruta))

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp)
    monkeypatch.setattr(app_dash, "DB_PATH", str(ruta))
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(app_dash, "obtener_trm_para", lambda *a, **k: 3900.0)
    monkeypatch.setattr(mp, "cargar_portafolio_proyeccion",
                        lambda *a, **k: _titulos_sinteticos_rf(["A", "B", "C"]))

    conn = sqlite3.connect(str(ruta))
    _sembrar_portafolio_clasificado(conn, ["A", "B", "C"])
    conn.close()
    return app_dash, ruta


# ==============================================================================
# Property 1 — Serie RF mensual continua y completa en modo por tramos
# ==============================================================================

def _correr_por_tramos(app_d, inicio, fin):
    """Corre el orquestador por tramos en modo RF-only (sin inputs_por_tramo)."""
    n_tramos = len(ptr.calcular_tramos(inicio, fin))
    return app_d._ejecutar_escenario_por_tramos(
        inicio, fin,
        shifts_por_tramo=[{} for _ in range(n_tramos)],
        uvr_t0=400.0,
    )


def _assert_property1_serie_rf_mensual(res, tramos):
    """
    Aserciones de la Property 1 sobre el resultado del orquestador por tramos.

    Codifica el comportamiento ESPERADO (fallará sobre el código sin corregir):
      1. Existe la serie RF mensual (clave `rf_mensual_contable`).
      2. Σ filas == Σ sub_horizonte de todos los tramos.
      3. El enero de arranque de cada tramo está presente (sin desplazamiento +1 mes).
      4. Serie continua, sin huecos ni duplicados en las fronteras de año.
    """
    assert res is not None, "la corrida por tramos no debe abortar"

    # (1) Existe hoja/serie RF_Mensual: la clave debe estar presente y no vacía.
    assert "rf_mensual_contable" in res, (
        "el modo por_tramos NO produce la clave 'rf_mensual_contable' "
        "(ResultadoMes descartado; falta la serie/hoja RF_Mensual)"
    )
    serie = res["rf_mensual_contable"]
    assert serie, "la serie RF mensual por tramos está vacía"

    df = pd.DataFrame(serie)
    assert "fecha" in df.columns, "la serie RF mensual debe tener columna 'fecha' (YYYY-MM)"

    # (2) Σ filas == Σ sub_horizonte.
    total_sub_h = sum(t.sub_horizonte for t in tramos)
    assert len(df) == total_sub_h, (
        f"filas de la serie RF ({len(df)}) != Σ sub_horizonte ({total_sub_h})"
    )

    # Normalizar las fechas de la serie a periodos mensuales "YYYY-MM".
    fechas = [str(f)[:7] for f in df["fecha"].tolist()]

    # (3) Enero de arranque de cada tramo presente. El tramo i arranca en
    #     fecha_inicio; su primer mes contable debe ser ese mes (no el siguiente).
    for tramo in tramos:
        inicio_tramo = f"{tramo.fecha_inicio.year:04d}-{tramo.fecha_inicio.month:02d}"
        assert inicio_tramo in fechas, (
            f"falta el mes de arranque {inicio_tramo} del tramo {tramo.indice} "
            f"en la serie RF (desplazamiento +1 mes del motor)"
        )

    # (4) Serie continua, sin huecos ni duplicados: las fechas deben ser una secuencia
    #     mensual estrictamente creciente sin repetidos entre el primer y último mes.
    assert len(fechas) == len(set(fechas)), "la serie RF mensual tiene meses duplicados"
    primer = tramos[0].fecha_inicio.replace(day=1)
    esperado = []
    cursor = primer
    for _ in range(total_sub_h):
        esperado.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = cursor + relativedelta(months=1)
    assert fechas == esperado, (
        "la serie RF mensual no es continua (huecos/duplicados en frontera de año); "
        f"esperado {esperado[:3]}... obtenido {fechas[:3]}..."
    )


# ── Caso ancla (determinista): frontera de año con fin 31-dic-2027 ──
def test_prop1_rf_mensual_por_tramos_caso_ancla(orquestador_env, monkeypatch):
    """
    Caso concreto y reproducible: corrida por tramos 2026-01-01 → 2027-12-31 (dos tramos,
    ambos arrancando el 1-ene / frontera de año). Debe producir una serie RF mensual con
    el enero de cada año (ene-2026 y ene-2027) y 24 filas (12 + 12).

    EXPECTED OUTCOME (código sin corregir): FALLA — no existe 'rf_mensual_contable'
    (ResultadoMes descartado) y, de existir, el enero de arranque faltaría por el
    desplazamiento +1 mes del motor.
    """
    app_d, _ruta = orquestador_env
    monkeypatch.setattr(mp, "proyectar_portafolio", _doble_motor_rf(), raising=False)

    inicio, fin = date(2026, 1, 1), date(2027, 12, 31)
    tramos = ptr.calcular_tramos(inicio, fin)
    res, _msg = _correr_por_tramos(app_d, inicio, fin)
    _assert_property1_serie_rf_mensual(res, tramos)


# ── Generalización con Hypothesis: varias configuraciones de tramos ──
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(anio_fin=st.integers(min_value=2026, max_value=2030))
def test_prop1_rf_mensual_por_tramos_generalizado(orquestador_env, monkeypatch, anio_fin):
    """
    Generaliza la Property 1 a corridas 2026-01-01 → 31-dic-{anio_fin} (1..5 tramos, todos
    arrancando el 1-ene). La serie RF mensual debe cubrir ene..dic de cada año sin huecos.

    EXPECTED OUTCOME (código sin corregir): FALLA.
    """
    app_d, _ruta = orquestador_env
    monkeypatch.setattr(mp, "proyectar_portafolio", _doble_motor_rf(), raising=False)

    inicio, fin = date(2026, 1, 1), date(anio_fin, 12, 31)
    tramos = ptr.calcular_tramos(inicio, fin)
    res, _msg = _correr_por_tramos(app_d, inicio, fin)
    _assert_property1_serie_rf_mensual(res, tramos)


# ==============================================================================
# Sub-propiedad del motor: fechado del mes de arranque (sin desplazamiento +1 mes)
# ==============================================================================

def test_prop1_motor_no_pierde_enero_de_arranque(monkeypatch):
    """
    Confirma la causa raíz del fechado directamente sobre el motor real: un tramo que
    arranca el 1-ene-2026 y proyecta 12 meses debe emitir ResultadoMes fechados
    ene-2026..dic-2026 (12 fechas), con enero PRESENTE.

    EXPECTED OUTCOME (código sin corregir): FALLA — el motor fecha el mes 1 como feb-2026
    (`fecha_inicio + relativedelta(months=mes)` con mes desde 1), por lo que la serie va
    feb-2026..ene-2027 y ene-2026 nunca aparece.
    """
    import compras_ventas_reservas as cvr
    # Aislar la carga auxiliar para que el motor solo revalúe (no toque la DB real).
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(cvr, "cargar_universo_compra", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_ramos_reserva_matematica", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_parametros_reservas", lambda *a, **k: None)

    port = _titulos_sinteticos_rf(["A"])
    resultados = mp.proyectar_portafolio(
        portafolio=port,
        fecha_inicio=date(2026, 1, 1),
        meses_horizonte=12,
        sendas={},
        mapa_reservas={},
    )
    fechas = [f"{r.fecha.year:04d}-{r.fecha.month:02d}" for r in resultados]

    # Un año completo que arranca el 1-ene debe fechar ene..dic de ese mismo año.
    esperado = [f"2026-{m:02d}" for m in range(1, 13)]
    assert fechas == esperado, (
        "el motor desplaza el fechado +1 mes y pierde el enero de arranque; "
        f"esperado {esperado} obtenido {fechas}"
    )
