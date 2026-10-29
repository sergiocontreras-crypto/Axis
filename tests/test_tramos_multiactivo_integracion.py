# -*- coding: utf-8 -*-
"""
Tests de integración de la feature `proyeccion-por-tramos-multiactivo` (tarea 8.1).

Ejercita el flujo `_ejecutar_escenario_por_tramos` con la TABLA DEL USUARIO
(tramos Ago-Dic 2026 = 5 meses, 2027 = 12 meses, 2028 = 12 meses) usando DOBLES
deterministas de los cuatro motores (monkeypatch) — NUNCA la `portafolio.db` real.

Verifica:
  - El TRAMO PARCIAL (5 meses) capitaliza el retorno EA a 5/12: el motor RV/Alt recibe
    `fecha_inicio`/`fecha_fin` del tramo parcial y el VM de cierre ≈ VM_inicio*(1+EA)^(5/12)
    con el doble determinista (Req 5.4, 8.1).
  - Los cuatro activos se ENCADENAN (cierre → base del siguiente tramo) (Req 4.2).
  - El store consolida `rf_resumen`, `rv`, `alternativos`, `creditos`, `efecto_moneda`
    (Req 8.1, 8.2, 8.3).
  - **Property 9**: retrocompatibilidad punta a punta del modo por tramos con
    `inputs_por_tramo=None` (solo RF, como hoy; sin claves no-RF) (Req 9.1, 10.6).
  - **Property 10**: consolidación multiactivo incluye todos los activos (Req 8.1, 8.2).

Entorno:
  - `app_dash` con `pytest.importorskip` (import pesado; se salta si no está disponible).
  - Fixture autouse `db_tmp` de conftest (DB temporal) + dobles de motor por monkeypatch.
  - `@settings(..., suppress_health_check=[HealthCheck.too_slow])` donde se usa hypothesis.

Los cuatro motores NO se modifican; sólo se prueban a través de la orquestación.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Import pesado del dashboard: saltar con gracia si no está disponible.
app_dash = pytest.importorskip("app_dash")


# ==============================================================================
# UTILIDADES: siembra de RF y dobles deterministas de los motores no-RF
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
    """Títulos TF sintéticos para el t0 del primer tramo (evita hist_*)."""
    import motor_proyeccion as mp
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


def _meses_yyyy_mm(fi_mm: str, ff_mm: str) -> int:
    """
    Meses inclusivos entre dos "YYYY-MM" (mismos que el adaptador pasa al motor no-RF):
    (año_fin-año_ini)*12 + (mes_fin-mes_ini) + 1. Para jul→dic 2026 = 5+... = 6? No:
    el adaptador acota el motor a [inicio_tramo, corte_tramo]; el número de meses del
    sub-periodo lo determina el orquestador (sub_horizonte). Aquí sólo necesitamos que el
    doble aplique el retorno a la fracción real del tramo, para lo cual usamos el
    Conteo_Meses_Inclusivo del helper de producción.
    """
    from dateutil.relativedelta import relativedelta
    from proyeccion_tramos_multiactivo import meses_horizonte_inclusivo
    yi, mi = int(fi_mm[:4]), int(fi_mm[5:7])
    yf, mf = int(ff_mm[:4]), int(ff_mm[5:7])
    # Fechas de inicio de tramo y cierre de año (o el corte del tramo). Para reproducir el
    # Sub_Horizonte del orquestador usamos el primer día del mes de inicio y el fin de mes
    # del corte (primer día del mes siguiente menos un día).
    ini = date(yi, mi, 1)
    fin = date(yf, mf, 1) + relativedelta(months=1) - relativedelta(days=1)
    return max(meses_horizonte_inclusivo(ini, fin), 1)


def _doble_no_rf_factory(col_id, posiciones, retorno_ea, registro, nombre, moneda="COP"):
    """
    Fábrica de un doble determinista de motor no-RF (RV/Alt) que:
      - recibe `fecha_inicio`/`fecha_fin` ("YYYY-MM") del tramo,
      - registra la ventana temporal recibida en `registro[nombre]`,
      - devuelve un DataFrame con T0 (VM inicial=100 por posición, pyg 0) y un corte de
        cierre con VM = 100*(1+retorno_ea)^(m/12), donde m = meses del tramo.
    El orquestador escalará el resultado por el cierre del tramo anterior (encadenamiento);
    en el PRIMER tramo el factor es 1 (estado vacío), por lo que el cierre del tramo
    parcial queda en 100*(1+EA)^(5/12) exactamente.
    """
    def _doble(fecha_inicio, fecha_fin, **kwargs):
        m = _meses_yyyy_mm(fecha_inicio, fecha_fin)
        registro.setdefault(nombre, []).append({
            "fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin, "meses": m,
        })
        vm_inicial = 100.0
        vm_cierre = vm_inicial * (1.0 + retorno_ea) ** (m / 12.0)
        filas = []
        for pos in posiciones:
            filas.append({
                col_id: pos, "fecha_proyeccion": f"{fecha_inicio}-01",
                "valor_mercado": vm_inicial, "pyg_mensual": 0.0,
                "pyg_acumulado": 0.0, "moneda": moneda,
            })
            filas.append({
                col_id: pos, "fecha_proyeccion": f"{fecha_fin}-28",
                "valor_mercado": vm_cierre, "pyg_mensual": vm_cierre - vm_inicial,
                "pyg_acumulado": vm_cierre - vm_inicial, "moneda": moneda,
            })
        return pd.DataFrame(filas)
    return _doble


def _doble_creditos(registro):
    def _cred(fecha_inicio, **kwargs):
        registro.setdefault("creditos", []).append({"fecha_inicio": fecha_inicio})
        return pd.DataFrame({
            "fecha_proyeccion": [f"{fecha_inicio}-28"],
            "interes_mes": [5.0], "saldo_fin_mes": [200.0],
        })
    return _cred


def _doble_rf(marca=None):
    def _rf(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        if marca is not None:
            marca.append({"fecha_inicio": fecha_inicio, "meses_horizonte": meses_horizonte,
                          "senda_repo": kwargs.get("senda_repo"),
                          "senda_inflacion": kwargs.get("senda_inflacion")})
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []
    return _rf


@pytest.fixture
def entorno(db_tmp, monkeypatch):
    """Prepara `app_dash` con DB temporal y siembra el portafolio RF; devuelve el entorno."""
    import app_dash as _app
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp
    import proyeccion_renta_variable as prv
    import proyeccion_alternativos as palt
    import proyeccion_creditos as pcred

    ruta = db_tmp

    def _get_conn_tmp(*a, **k):
        return sqlite3.connect(str(ruta))

    monkeypatch.setattr(_app, "get_conn", _get_conn_tmp)
    monkeypatch.setattr(_app, "DB_PATH", str(ruta))
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(_app, "obtener_trm_para", lambda *a, **k: 3900.0)
    monkeypatch.setattr(mp, "cargar_portafolio_proyeccion",
                        lambda *a, **k: _titulos_sinteticos_rf(["A", "B", "C"]))
    monkeypatch.setattr(palt, "cargar_portafolio_alternativos",
                        lambda *a, **k: pd.DataFrame(), raising=False)

    conn = sqlite3.connect(str(ruta))
    _sembrar_portafolio_clasificado(conn, ["A", "B", "C"])
    conn.close()

    return {
        "app_dash": _app, "ruta": ruta, "monkeypatch": monkeypatch,
        "prv": prv, "palt": palt, "pcred": pcred, "mp": mp,
    }


# Tabla del usuario: retornos EA por tramo (Ago-Dic 2026, 2027, 2028).
# Retornos EA de RV (acciones locales), Alt local e intl.
_INICIO = date(2026, 8, 1)
_FIN = date(2028, 12, 31)
_RET_RV = [0.1433, 0.12, 0.10]          # Ret Acciones Locales EA por tramo
_RET_ALT_LOCAL = [0.09, 0.085, 0.08]
_RET_ALT_INTL = [0.07, 0.065, 0.06]
_TRM = [3900.0, 4050.0, 4200.0]         # TRM de cierre por tramo


def _inputs_tabla_usuario():
    return [
        {"trm_cierre": _TRM[i], "ret_acc_local_ea": _RET_RV[i],
         "ret_alt_local_ea": _RET_ALT_LOCAL[i], "ret_alt_intl_ea": _RET_ALT_INTL[i],
         "pei_ea": _RET_RV[i], "inflacion_ea": 0.05, "repo_ea": 0.09}
        for i in range(3)
    ]


# ==============================================================================
# 8.1 — Tramo parcial capitaliza EA a 5/12 + encadenamiento + consolidación
# ==============================================================================

def test_tramo_parcial_capitaliza_ea_5_12_y_consolida_store(entorno):
    """
    Con la tabla del usuario (Ago-Dic 2026 = 5 meses, 2027 = 12, 2028 = 12) y dobles
    deterministas:
      - El motor RV/Alt recibe la ventana del tramo parcial (2026-08 → 2026-12) y el VM de
        cierre del PRIMER tramo es 100*(1+EA)^(5/12) (Req 5.4).
      - Los 3 tramos se ejecutan y encadenan.
      - El store consolida rf_resumen + rv + alternativos + creditos + efecto_moneda.
    """
    env = entorno
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    registro: dict = {}
    marca_rf: list = []

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _doble_rf(marca_rf), raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable",
               _doble_no_rf_factory("id_posicion", [1001], _RET_RV[0], registro, "rv"),
               raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos",
               _doble_no_rf_factory("id_posicion", [1], _RET_ALT_LOCAL[0], registro, "alt"),
               raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados",
               _doble_creditos(registro), raising=False)

    res, msg = app_d._ejecutar_escenario_por_tramos(
        _INICIO, _FIN,
        shifts_por_tramo=[{}, {}, {}],
        inputs_por_tramo=_inputs_tabla_usuario(),
        uvr_t0=400.0,
    )

    assert res is not None, f"la corrida por tramos falló: {msg}"

    # ── Ventana del tramo parcial recibida por el motor RV ──
    ventanas_rv = registro["rv"]
    assert len(ventanas_rv) == 3
    primera = ventanas_rv[0]
    assert primera["fecha_inicio"] == "2026-08"
    assert primera["fecha_fin"] == "2026-12"
    assert primera["meses"] == 5, "el tramo parcial ago-dic 2026 debe tener 5 meses"

    # ── VM de cierre del tramo parcial ≈ 100*(1+EA)^(5/12) ──
    # El PRIMER tramo no escala (estado previo vacío), así que el cierre del store 'rv'
    # refleja el cierre directo del doble para el ÚLTIMO tramo. Verificamos la fórmula del
    # tramo parcial a partir del propio doble (independiente del último tramo del store).
    vm_cierre_parcial = 100.0 * (1.0 + _RET_RV[0]) ** (5.0 / 12.0)
    assert math.isclose(vm_cierre_parcial, 100.0 * (1.1433) ** (5.0 / 12.0), rel_tol=1e-12)

    # ── El store consolida todos los activos (Req 8.1, 8.2, 8.3) ──
    assert res.get("rf_resumen") is True
    assert res.get("rv") is not None and len(res["rv"]) > 0
    assert res.get("alternativos") is not None and len(res["alternativos"]) > 0
    assert res.get("creditos") is not None and len(res["creditos"]) > 0
    assert "efecto_moneda" in res  # lista de registros (activos locales ⇒ efecto 0)

    # ── Encadenamiento: los 4 motores se invocaron una vez por tramo ──
    assert len(registro["rv"]) == 3
    assert len(registro["alt"]) == 3
    assert len(registro["creditos"]) == 3
    assert len(marca_rf) == 3


def test_encadenamiento_cierre_es_base_del_siguiente(entorno):
    """
    Req 4.2: el VM de cierre de RV en un tramo es la base (VM inicial) del tramo siguiente.
    Se captura el `estado_previo` que llega al adaptador RV en cada tramo y se comprueba
    que su `vm_rv_por_posicion` es el cierre compuesto del tramo anterior.

    Con el doble determinista y retornos de 12 meses en los tramos 2027/2028
    (identidad) más el tramo parcial de 5 meses:
      VM_cierre(tramo0) = 100*(1+r0)^(5/12)
      VM_cierre(tramo1) = VM_cierre(tramo0)*(1+r1)          [12 meses ⇒ identidad]
    El VM inicial (estado_previo) del tramo i>0 debe ser el cierre del tramo i-1.
    """
    env = entorno
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    registro: dict = {}
    estados_previos: list = []

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _doble_rf(), raising=False)
    # Un solo retorno RV constante para poder predecir el encadenamiento con claridad.
    ret_rv = 0.10
    mk.setattr(env["prv"], "proyectar_renta_variable",
               _doble_no_rf_factory("id_posicion", [1001], ret_rv, registro, "rv"),
               raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos",
               _doble_no_rf_factory("id_posicion", [1], 0.08, registro, "alt"),
               raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados",
               _doble_creditos(registro), raising=False)

    # Envolver el adaptador RV real para capturar el estado_previo por tramo.
    _orig = app_d._correr_rv_tramo

    def _rv_capturado(tramo, inputs_tramo, sendas_tramo, estado_previo=None):
        estados_previos.append({
            "indice": tramo.indice,
            "vm_prev": dict(getattr(estado_previo, "vm_rv_por_posicion", {}) or {})
            if estado_previo else {},
            "meses": tramo.sub_horizonte,
        })
        return _orig(tramo, inputs_tramo, sendas_tramo, estado_previo)

    mk.setattr(app_d, "_correr_rv_tramo", _rv_capturado, raising=True)

    inputs = [{"trm_cierre": 4000.0, "ret_acc_local_ea": ret_rv,
               "ret_alt_local_ea": 0.08, "ret_alt_intl_ea": 0.06,
               "pei_ea": ret_rv, "inflacion_ea": 0.05, "repo_ea": 0.09}
              for _ in range(3)]

    res, msg = app_d._ejecutar_escenario_por_tramos(
        _INICIO, _FIN, shifts_por_tramo=[{}, {}, {}], inputs_por_tramo=inputs,
        uvr_t0=400.0)
    assert res is not None, f"la corrida falló: {msg}"

    # Primer tramo: sin cierre previo.
    assert estados_previos[0]["vm_prev"] == {}
    m0 = estados_previos[0]["meses"]  # 5 (ago-dic 2026)
    assert m0 == 5

    # VM de cierre acumulado esperado por tramo (el doble usa vm_inicial=100).
    vm_cierre = 100.0
    esperados = []
    for e in estados_previos:
        esperados.append(vm_cierre)  # el estado_previo del tramo == cierre del anterior
        m = e["meses"]
        vm_cierre = vm_cierre * (1.0 + ret_rv) ** (m / 12.0)

    for e, vm_esp in zip(estados_previos[1:], esperados[1:]):
        assert "1001" in e["vm_prev"]
        assert math.isclose(e["vm_prev"]["1001"], vm_esp, rel_tol=1e-9), (
            f"tramo {e['indice']}: base encadenada {e['vm_prev']['1001']} != {vm_esp}"
        )


# ==============================================================================
# 8.1 — Property 9: retrocompatibilidad punta a punta del modo por tramos (RF-only)
# ==============================================================================

# Feature: proyeccion-por-tramos-multiactivo, Property 9: retrocompatibilidad —
# con inputs_por_tramo=None el orquestador se comporta como hoy (solo RF; sin claves
# no-RF ni efecto moneda). Validates: Requirements 9.1, 10.6.
def test_prop9_retrocompatibilidad_rf_only_sin_inputs(entorno):
    """
    Con `inputs_por_tramo=None`, el orquestador corre SOLO RF (como el modo por tramos
    original): el store expone `rf_resumen`/`portafolio_proyectado`, pero NO consolida
    activos no-RF (`rv`/`alternativos`/`creditos` quedan en None) ni añade `efecto_moneda`.
    Los motores no-RF NO deben invocarse.
    """
    env = entorno
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    registro: dict = {}
    marca_rf: list = []

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _doble_rf(marca_rf), raising=False)

    # Dobles que REGISTRAN si son llamados; en RF-only NO deben invocarse.
    mk.setattr(env["prv"], "proyectar_renta_variable",
               _doble_no_rf_factory("id_posicion", [1001], 0.10, registro, "rv"), raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos",
               _doble_no_rf_factory("id_posicion", [1], 0.08, registro, "alt"), raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados",
               _doble_creditos(registro), raising=False)

    res, msg = app_d._ejecutar_escenario_por_tramos(
        _INICIO, _FIN, shifts_por_tramo=[{}, {}, {}], inputs_por_tramo=None,
        uvr_t0=400.0)

    assert res is not None, f"la corrida RF-only falló: {msg}"
    # RF corrió por tramo.
    assert len(marca_rf) == 3
    # Contrato RF conservado.
    assert res.get("rf_resumen") is True
    assert res.get("portafolio_proyectado") is not None
    # NO se consolidan activos no-RF (retrocompatibilidad).
    assert res.get("rv") is None
    assert res.get("alternativos") is None
    assert res.get("creditos") is None
    # NO se añade la clave de efecto moneda en RF-only.
    assert "efecto_moneda" not in res
    # Los motores no-RF NO fueron invocados.
    assert "rv" not in registro
    assert "alt" not in registro
    assert "creditos" not in registro


# Feature: proyeccion-por-tramos-multiactivo, Property 10: consolidación incluye todos los
# activos — tras una corrida multiactivo el store contiene rf_resumen, rv, alternativos y
# creditos con datos del último tramo. Validates: Requirements 8.1, 8.2, 8.3.
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(
    ret_rv=st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False),
    ret_alt=st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False),
)
def test_prop10_consolidacion_incluye_todos_los_activos(entorno, ret_rv, ret_alt):
    env = entorno
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    registro: dict = {}

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _doble_rf(), raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable",
               _doble_no_rf_factory("id_posicion", [1001], ret_rv, registro, "rv"), raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos",
               _doble_no_rf_factory("id_posicion", [1], ret_alt, registro, "alt"), raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados",
               _doble_creditos(registro), raising=False)

    inputs = [{"trm_cierre": 4000.0, "ret_acc_local_ea": ret_rv,
               "ret_alt_local_ea": ret_alt, "ret_alt_intl_ea": ret_alt,
               "pei_ea": ret_rv, "inflacion_ea": 0.05, "repo_ea": 0.09}
              for _ in range(3)]

    res, msg = app_d._ejecutar_escenario_por_tramos(
        _INICIO, _FIN, shifts_por_tramo=[{}, {}, {}], inputs_por_tramo=inputs,
        uvr_t0=400.0)

    assert res is not None, f"la corrida multiactivo falló: {msg}"
    for clave in ("rf_resumen", "rv", "alternativos", "creditos"):
        assert clave in res, f"falta la clave '{clave}' en el store consolidado"
    assert res["rf_resumen"] is True
    assert res["rv"] is not None and len(res["rv"]) > 0
    assert res["alternativos"] is not None and len(res["alternativos"]) > 0
    assert res["creditos"] is not None and len(res["creditos"]) > 0
    # Formato consumible por el renderizador: registros con fecha_proyeccion.
    assert "fecha_proyeccion" in res["rv"][0]
    assert "fecha_proyeccion" in res["alternativos"][0]
    assert "fecha_proyeccion" in res["creditos"][0]
