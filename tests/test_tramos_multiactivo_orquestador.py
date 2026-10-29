"""
Tests del orquestador multiactivo por tramos (feature `proyeccion-por-tramos-multiactivo`).

Cubre las tareas de test:
  - 5.5  Property 6 y 7  → encadenamiento no-RF usa el cierre del tramo anterior; los
                           cuatro motores se invocan exactamente una vez por tramo.
  - 5.6  Property 11     → propagación de repo/inflación (senda_repo/senda_inflacion) al
                           motor RF (`proyectar_portafolio`) por tramo.
  - 5.7  Property 8      → detención y reporte ante fallo de un motor no-RF en un tramo
                           (identifica activo y fecha_corte, sin exponer fin de horizonte).
  - 5.9  Property 13,14,15 → descomposición del efecto moneda (reconstruye el PyG; cero
                           para locales; = ΔTRM × VM inicial en origen por tramo).

Reglas del entorno:
  - `app_dash` se importa con `pytest.importorskip` (import pesado; si no está disponible
    en el entorno, los tests se saltan sin romper la suite).
  - Se usa la fixture autouse `db_tmp` de `conftest` y DOBLES de motor (monkeypatch de
    `proyectar_renta_variable`, `proyectar_alternativos`, `proyectar_creditos_contratados`,
    `proyectar_portafolio`, `cargar_portafolio_proyeccion`/`cargar_portafolio_alternativos`)
    para NO tocar `portafolio.db` real ni correr los motores reales.
  - Se suprime `HealthCheck.too_slow` donde se usa hypothesis (artefacto del entorno:
    Google Drive + import pesado del dashboard).

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

# Helpers puros del módulo multiactivo (import ligero, sin efectos secundarios).
from proyeccion_tramos_multiactivo import descomponer_efecto_moneda_posicion  # noqa: E402


# ==============================================================================
# UTILIDADES DE PRUEBA: dobles de motor y siembra del portafolio de RF
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


def _doble_rf_factory(marca_rf=None):
    """
    Doble de `proyectar_portafolio` (motor RF). Marca el spread efectivo de cierre para el
    encadenamiento RF (como el motor real) y registra en `marca_rf` los kwargs relevantes
    (senda_repo, senda_inflacion, trm, trm_cierre) por tramo, para la Property 11.
    """
    def _doble(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        if marca_rf is not None:
            marca_rf.append({
                "fecha_inicio": fecha_inicio,
                "meses_horizonte": meses_horizonte,
                "senda_repo": kwargs.get("senda_repo"),
                "senda_inflacion": kwargs.get("senda_inflacion"),
                "trm": kwargs.get("trm"),
                "trm_cierre": kwargs.get("trm_cierre"),
            })
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []
    return _doble


def _df_no_rf(col_id, posiciones, fechas, vm_inicial, retorno_periodo, moneda="COP"):
    """
    Construye un DataFrame de resultado de un motor no-RF (RV/Alt) determinista:
      - una fila por posición y por fecha de corte;
      - `valor_mercado` compone linealmente el retorno del periodo entre T0 y el cierre;
      - `pyg_mensual`/`pyg_acumulado` coherentes con la evolución del VM.
    El PRIMER corte tiene VM = vm_inicial (T0, pyg 0), como el motor real.
    """
    filas = []
    n = len(fechas)
    for pos in posiciones:
        vm_prev = vm_inicial
        pyg_acum = 0.0
        for k, f in enumerate(fechas):
            # VM crece geométricamente hasta vm_inicial*(1+retorno) en el último corte.
            frac = k / (n - 1) if n > 1 else 1.0
            vm = vm_inicial * (1.0 + retorno_periodo) ** frac
            if k == 0:
                vm = vm_inicial
            pyg_mensual = vm - vm_prev
            pyg_acum += pyg_mensual
            filas.append({
                col_id: pos,
                "fecha_proyeccion": f,
                "valor_mercado": vm,
                "pyg_mensual": pyg_mensual,
                "pyg_acumulado": pyg_acum,
                "moneda": moneda,
            })
            vm_prev = vm
    return pd.DataFrame(filas)


def _fechas_tramo(tramo):
    """Fechas de corte 'YYYY-MM-DD' del tramo (una por mes del sub_horizonte)."""
    from dateutil.relativedelta import relativedelta
    meses = max(tramo.sub_horizonte, 1)
    fechas = []
    for mes in range(1, meses + 1):
        if mes == meses:
            fechas.append(tramo.fecha_corte.strftime("%Y-%m-%d"))
        else:
            fechas.append((tramo.fecha_inicio + relativedelta(months=mes)).strftime("%Y-%m-%d"))
    return fechas


@pytest.fixture
def orq_multiactivo(db_tmp, monkeypatch):
    """
    Prepara `app_dash` con DB temporal y dobles de los cuatro motores.

    Devuelve un dict con `app_dash`, la ruta de la DB y contadores/marcas de invocación,
    de modo que los tests puedan configurar el comportamiento de cada motor.
    """
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

    # El adaptador de alternativos consulta el portafolio para mapear spreads 4G; se le da
    # un portafolio vacío (sin columna es_ipc) para que no toque la DB real de alternativos.
    monkeypatch.setattr(_app, "cargar_portafolio_alternativos" if hasattr(_app, "cargar_portafolio_alternativos") else "get_conn",
                        _get_conn_tmp, raising=False)
    monkeypatch.setattr(palt, "cargar_portafolio_alternativos",
                        lambda *a, **k: pd.DataFrame(), raising=False)

    conn = sqlite3.connect(str(ruta))
    _sembrar_portafolio_clasificado(conn, ["A", "B", "C"])
    conn.close()

    env = {
        "app_dash": _app, "ruta": ruta, "monkeypatch": monkeypatch,
        "prv": prv, "palt": palt, "pcred": pcred, "mp": mp,
    }
    return env


# ==============================================================================
# TAREA 5.5 — Property 6 y 7 (encadenamiento no-RF + invocación por tramo)
# ==============================================================================

# Feature: proyeccion-por-tramos-multiactivo, Property 7: los 4 motores se invocan
# exactamente una vez por tramo (n_invocaciones == n_tramos). Validates: Requirements 4.1.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(anio_fin=st.integers(min_value=2026, max_value=2031))
def test_prop7_cuatro_motores_invocados_una_vez_por_tramo(orq_multiactivo, anio_fin):
    env = orq_multiactivo
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    contador = {"rf": 0, "rv": 0, "alt": 0, "cred": 0}

    def _rf(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        contador["rf"] += 1
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []

    def _rv(fecha_inicio, fecha_fin, **kwargs):
        contador["rv"] += 1
        return _df_no_rf("id_posicion", [1001], ["2026-12-31"], 100.0, 0.10)

    def _alt(fecha_inicio, fecha_fin, **kwargs):
        contador["alt"] += 1
        return _df_no_rf("id_posicion", [1], ["2026-12-31"], 100.0, 0.10)

    def _cred(fecha_inicio, **kwargs):
        contador["cred"] += 1
        return pd.DataFrame({
            "fecha_proyeccion": ["2026-12-31"],
            "interes_mes": [5.0], "saldo_fin_mes": [200.0],
        })

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _rf, raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable", _rv, raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos", _alt, raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados", _cred, raising=False)

    import proyeccion_tramos as ptr
    inicio, fin = date(2026, 1, 1), date(anio_fin, 12, 31)
    n_tramos = len(ptr.calcular_tramos(inicio, fin))

    inputs = [{"trm_cierre": 4000.0 + 100 * i, "ret_acc_local_ea": 0.10}
              for i in range(n_tramos)]

    res, msg = app_d._ejecutar_escenario_por_tramos(
        inicio, fin,
        shifts_por_tramo=[{} for _ in range(n_tramos)],
        inputs_por_tramo=inputs,
        uvr_t0=400.0,
    )

    assert res is not None
    assert contador["rf"] == n_tramos
    assert contador["rv"] == n_tramos
    assert contador["alt"] == n_tramos
    assert contador["cred"] == n_tramos


# Feature: proyeccion-por-tramos-multiactivo, Property 6: el encadenamiento no-RF usa el
# cierre del tramo anterior (el VM de inicio del activo en el tramo i es el VM de cierre
# del tramo i-1). Validates: Requirements 4.1, 4.2.
def test_prop6_encadenamiento_no_rf_usa_cierre_anterior(orq_multiactivo):
    env = orq_multiactivo
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    # Capturar el estado_previo (VM de cierre del tramo anterior) que recibe el adaptador
    # RV en cada tramo, y el VM T0 que el motor RV "ve" tras el escalado por encadenamiento.
    vm_inicial_marcado = 100.0
    retorno_rv = 0.10  # el doble RV cierra en vm_inicial * (1 + 0.10)

    estados_previos = []  # trm_cierre + VM de cierre por posición recibidos por tramo

    def _rf(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []

    # El doble RV reporta VM inicial=100 en su T0 (como si el motor leyera el portafolio
    # base) y cierra en vm_inicial*(1+retorno); el orquestador escala el resultado por el
    # cierre previo vía `_aplicar_encadenamiento_resultado`. Registramos qué estado_previo
    # (VM de cierre del tramo anterior) llega al adaptador. `fecha_inicio`/`fecha_fin` son
    # "YYYY-MM"; se construyen dos cortes deterministas (T0 y cierre) por tramo.
    def _rv(fecha_inicio, fecha_fin, **kwargs):
        return _df_no_rf("id_posicion", [1001],
                         [f"{fecha_inicio}-15", f"{fecha_fin}-28"],
                         vm_inicial_marcado, retorno_rv)

    def _alt(fecha_inicio, fecha_fin, **kwargs):
        return _df_no_rf("id_posicion", [1],
                         [f"{fecha_inicio}-15", f"{fecha_fin}-28"],
                         vm_inicial_marcado, retorno_rv)

    def _cred(fecha_inicio, **kwargs):
        return pd.DataFrame({
            "fecha_proyeccion": [f"{fecha_inicio}-28"],
            "interes_mes": [1.0], "saldo_fin_mes": [50.0],
        })

    # Envolver el adaptador RV real para capturar el estado_previo por tramo.
    _adaptador_rv_original = app_d._correr_rv_tramo

    def _correr_rv_capturado(tramo, inputs_tramo, sendas_tramo, estado_previo=None):
        estados_previos.append({
            "indice": tramo.indice,
            "vm_prev": dict(getattr(estado_previo, "vm_rv_por_posicion", {}) or {}) if estado_previo else {},
        })
        return _adaptador_rv_original(tramo, inputs_tramo, sendas_tramo, estado_previo)

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _rf, raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable", _rv, raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos", _alt, raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados", _cred, raising=False)
    mk.setattr(app_d, "_correr_rv_tramo", _correr_rv_capturado, raising=True)

    inicio, fin = date(2026, 1, 1), date(2028, 12, 31)
    inputs = [{"trm_cierre": 4000.0, "ret_acc_local_ea": 0.10} for _ in range(3)]

    res, msg = app_d._ejecutar_escenario_por_tramos(
        inicio, fin,
        shifts_por_tramo=[{}, {}, {}],
        inputs_por_tramo=inputs,
        uvr_t0=400.0,
    )
    assert res is not None

    # El primer tramo NO tiene cierre previo (estado vacío). Los siguientes reciben el VM
    # de cierre COMPUESTO del tramo anterior. Como el doble RV cierra en
    # vm_inicial*(1+retorno) y el orquestador ESCALA cada tramo por el cierre previo
    # (`_aplicar_encadenamiento_resultado`), el cierre se capitaliza tramo a tramo:
    #   VM_cierre(tramo i) = vm_inicial * (1 + retorno) ** (i + 1)
    # Por tanto el VM de INICIO del tramo i (= cierre del tramo i-1) es
    #   vm_inicial * (1 + retorno) ** i.  (Encadenamiento: usa el cierre anterior.)
    assert estados_previos[0]["vm_prev"] == {}
    for e in estados_previos[1:]:
        i = e["indice"]
        vm_prev_esperado = vm_inicial_marcado * (1.0 + retorno_rv) ** i
        assert "1001" in e["vm_prev"]
        assert math.isclose(e["vm_prev"]["1001"], vm_prev_esperado, rel_tol=1e-9), (
            f"tramo {i}: VM inicial encadenado {e['vm_prev']['1001']} != {vm_prev_esperado}"
        )


# ==============================================================================
# TAREA 5.6 — Property 11 (propagación de repo/inflación al motor RF)
# ==============================================================================

# Feature: proyeccion-por-tramos-multiactivo, Property 11: el orquestador pasa a
# `proyectar_portafolio` una senda_repo y una senda_inflacion NO vacías derivadas del
# Repo_Por_Tramo/Inflacion_Por_Tramo del tramo. Validates: Requirements 7.1, 7.2, 7.3.
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(
    repo_ea=st.floats(min_value=0.0, max_value=0.30, allow_nan=False, allow_infinity=False),
    inflacion_ea=st.floats(min_value=0.0, max_value=0.20, allow_nan=False, allow_infinity=False),
)
def test_prop11_propaga_repo_inflacion_al_motor_rf(orq_multiactivo, repo_ea, inflacion_ea):
    env = orq_multiactivo
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    marca_rf = []

    def _rv(fecha_inicio, fecha_fin, **kwargs):
        return _df_no_rf("id_posicion", [1001], [f"{fecha_inicio}-15", f"{fecha_fin}-28"], 100.0, 0.05)

    def _alt(fecha_inicio, fecha_fin, **kwargs):
        return _df_no_rf("id_posicion", [1], [f"{fecha_inicio}-15", f"{fecha_fin}-28"], 100.0, 0.05)

    def _cred(fecha_inicio, **kwargs):
        return pd.DataFrame({"fecha_proyeccion": [f"{fecha_inicio}-28"],
                             "interes_mes": [1.0], "saldo_fin_mes": [50.0]})

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _doble_rf_factory(marca_rf=marca_rf), raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable", _rv, raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos", _alt, raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados", _cred, raising=False)

    inicio, fin = date(2026, 1, 1), date(2027, 12, 31)
    import proyeccion_tramos as ptr
    n_tramos = len(ptr.calcular_tramos(inicio, fin))
    inputs = [{"trm_cierre": 4100.0, "repo_ea": repo_ea, "inflacion_ea": inflacion_ea}
              for _ in range(n_tramos)]

    res, _ = app_d._ejecutar_escenario_por_tramos(
        inicio, fin, shifts_por_tramo=[{} for _ in range(n_tramos)],
        inputs_por_tramo=inputs,
        uvr_t0=400.0,
    )
    assert res is not None
    assert len(marca_rf) == n_tramos

    for llamada in marca_rf:
        senda_repo = llamada["senda_repo"]
        senda_infl = llamada["senda_inflacion"]
        # No vacías (derivadas del repo/inflación del tramo).
        assert senda_repo is not None and len(senda_repo) > 0
        assert senda_infl is not None and len(senda_infl) > 0
        # El orquestador expresa las sendas al motor RF en % EA (valor * 100).
        assert all(math.isclose(v, repo_ea * 100.0, rel_tol=1e-9, abs_tol=1e-9) for v in senda_repo)
        assert all(math.isclose(v, inflacion_ea * 100.0, rel_tol=1e-9, abs_tol=1e-9) for v in senda_infl)


# ==============================================================================
# TAREA 5.7 — Property 8 (detención y reporte ante fallo de un tramo) — EJEMPLO
# ==============================================================================

# Feature: proyeccion-por-tramos-multiactivo, Property 8: si un motor no-RF lanza en el
# tramo i, el orquestador detiene y devuelve un mensaje que identifica el activo y la
# fecha_corte, sin exponer fin de horizonte. Validates: Requirements 4.4.
def test_prop8_detiene_y_reporta_fallo_de_tramo(orq_multiactivo):
    env = orq_multiactivo
    app_d = env["app_dash"]
    mk = env["monkeypatch"]

    def _rf(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []

    def _rv(fecha_inicio, fecha_fin, **kwargs):
        return _df_no_rf("id_posicion", [1001], [f"{fecha_inicio}-15", f"{fecha_fin}-28"], 100.0, 0.05)

    # Alternativos falla SIEMPRE (simula el fallo de un motor no-RF en el tramo).
    def _alt_falla(fecha_inicio, fecha_fin, **kwargs):
        raise RuntimeError("valor sensible del portafolio que NO debe filtrarse 12345")

    def _cred(fecha_inicio, **kwargs):
        return pd.DataFrame({"fecha_proyeccion": [f"{fecha_inicio}-28"],
                             "interes_mes": [1.0], "saldo_fin_mes": [50.0]})

    import motor_proyeccion as mp
    mk.setattr(mp, "proyectar_portafolio", _rf, raising=False)
    mk.setattr(env["prv"], "proyectar_renta_variable", _rv, raising=False)
    mk.setattr(env["palt"], "proyectar_alternativos", _alt_falla, raising=False)
    mk.setattr(env["pcred"], "proyectar_creditos_contratados", _cred, raising=False)

    inicio, fin = date(2026, 1, 1), date(2028, 12, 31)
    inputs = [{"trm_cierre": 4000.0} for _ in range(3)]

    res, msg = app_d._ejecutar_escenario_por_tramos(
        inicio, fin, shifts_por_tramo=[{}, {}, {}],
        inputs_por_tramo=inputs,
        uvr_t0=400.0,
    )

    # Detención: NO se expone resultado de fin de horizonte.
    assert res is None
    texto = str(msg)
    # El mensaje identifica el activo y la fecha de corte del PRIMER tramo (2026-12-31).
    assert "Alternativos" in texto
    assert "2026-12-31" in texto
    # NO filtra datos sensibles de la excepción.
    assert "12345" not in texto
    assert "valor sensible" not in texto


# ==============================================================================
# TAREA 5.9 — Property 13, 14, 15 (efecto moneda) — PBT con hypothesis
# ==============================================================================

_TRM = st.floats(min_value=500.0, max_value=10000.0, allow_nan=False, allow_infinity=False)
_VM = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False)
_RET = st.floats(min_value=-0.99, max_value=5.0, allow_nan=False, allow_infinity=False)


# Feature: proyeccion-por-tramos-multiactivo, Property 13: efecto_moneda + efecto_retorno
# == pyg_total (tolerancia). Validates: Requirements 11.3, 11.4.
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(vm_inicio=_VM, trm_inicio=_TRM, trm_cierre=_TRM, ret_origen=_RET)
def test_prop13_efecto_moneda_mas_retorno_reconstruye_pyg(vm_inicio, trm_inicio, trm_cierre, ret_origen):
    # VM de cierre según valoración en moneda origen (design D8):
    #   VM_cierre_COP = (VM_inicio_COP / TRM_inicio) * (1 + ret_origen) * TRM_cierre
    vm_cierre = (vm_inicio / trm_inicio) * (1.0 + ret_origen) * trm_cierre

    ef = descomponer_efecto_moneda_posicion(
        id_posicion="P", activo="rv", moneda="USD",
        vm_inicio_cop=vm_inicio, vm_cierre_cop=vm_cierre,
        trm_inicio=trm_inicio, trm_cierre=trm_cierre,
        tramo_indice=0, fecha_corte="2026-12-31",
    )

    # Property 13: la descomposición reconstruye el PyG total.
    assert math.isclose(
        ef.efecto_moneda_cop + ef.efecto_retorno_cop, ef.pyg_total_cop,
        rel_tol=1e-9, abs_tol=1e-6,
    )
    # Coherencia con la fórmula cerrada del PyG total.
    pyg_esperado = vm_inicio * ((1.0 + ret_origen) * trm_cierre / trm_inicio - 1.0)
    escala = max(abs(vm_inicio), abs(vm_cierre), 1.0)
    assert math.isclose(ef.pyg_total_cop, pyg_esperado, rel_tol=1e-9, abs_tol=1e-6 * escala)


# Feature: proyeccion-por-tramos-multiactivo, Property 14: activo local (COP) ⇒
# efecto_moneda == 0 y efecto_retorno == pyg_total. Validates: Requirements 11.6.
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(vm_inicio=_VM, vm_cierre=_VM, trm_inicio=_TRM, trm_cierre=_TRM)
def test_prop14_efecto_moneda_cero_para_locales(vm_inicio, vm_cierre, trm_inicio, trm_cierre):
    ef = descomponer_efecto_moneda_posicion(
        id_posicion="P", activo="rv", moneda="COP",
        vm_inicio_cop=vm_inicio, vm_cierre_cop=vm_cierre,
        trm_inicio=trm_inicio, trm_cierre=trm_cierre,  # ignorados por ser local
        tramo_indice=0, fecha_corte="2026-12-31",
    )
    assert ef.efecto_moneda_cop == 0.0
    assert math.isclose(ef.efecto_retorno_cop, ef.pyg_total_cop, rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(ef.pyg_total_cop, vm_cierre - vm_inicio, rel_tol=1e-9, abs_tol=1e-6)


# Feature: proyeccion-por-tramos-multiactivo, Property 15: efecto_moneda ==
# vm_inicio_origen*(trm_cierre-trm_inicio) por tramo; el total del horizonte es la suma de
# los efectos por tramo. Validates: Requirements 11.3, 11.5.
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    vm_inicio=st.floats(min_value=1.0, max_value=1e10, allow_nan=False, allow_infinity=False),
    trms=st.lists(_TRM, min_size=2, max_size=6),
    rets=st.lists(_RET, min_size=1, max_size=5),
)
def test_prop15_efecto_moneda_por_tramo_y_suma_horizonte(vm_inicio, trms, rets):
    # Encadenar tramos: la TRM de cierre de un tramo es la TRM de inicio del siguiente.
    # trms tiene n+1 niveles (t0..tn); rets tiene n retornos (uno por tramo).
    n = min(len(trms) - 1, len(rets))
    if n < 1:
        return  # nada que verificar

    efectos = []
    vm_ini_cop = vm_inicio
    total_efecto_moneda = 0.0
    for i in range(n):
        trm_ini = trms[i]
        trm_cie = trms[i + 1]
        ret = rets[i]
        vm_cie = (vm_ini_cop / trm_ini) * (1.0 + ret) * trm_cie

        ef = descomponer_efecto_moneda_posicion(
            id_posicion="P", activo="rv", moneda="USD",
            vm_inicio_cop=vm_ini_cop, vm_cierre_cop=vm_cie,
            trm_inicio=trm_ini, trm_cierre=trm_cie,
            tramo_indice=i, fecha_corte="2026-12-31",
        )
        efectos.append(ef)

        # Property 15 (por tramo): efecto_moneda == vm_inicio_origen * ΔTRM.
        esperado = (vm_ini_cop / trm_ini) * (trm_cie - trm_ini)
        escala = max(abs(vm_ini_cop), 1.0)
        assert math.isclose(ef.efecto_moneda_cop, esperado, rel_tol=1e-9, abs_tol=1e-6 * escala)

        total_efecto_moneda += ef.efecto_moneda_cop
        vm_ini_cop = vm_cie  # encadenar al siguiente tramo

    # Property 15 (horizonte): el total es la suma de los efectos por tramo.
    suma = sum(e.efecto_moneda_cop for e in efectos)
    escala_total = max(abs(vm_inicio), abs(total_efecto_moneda), 1.0)
    assert math.isclose(suma, total_efecto_moneda, rel_tol=1e-9, abs_tol=1e-6 * escala_total)
