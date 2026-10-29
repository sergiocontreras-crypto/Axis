"""
Batería de tests de la feature `proyeccion-por-tramos`.

Cubre los helpers puros y de persistencia del módulo `scripts/proyeccion_tramos.py`:
troceo del horizonte (`calcular_tramos`), prorrateo de reservas anuales
(`prorratear_reservas_anual`) y persistencia de snapshots (`portafolio_clasificado_proy`).

Property tests con hypothesis (>= 100 iteraciones), etiquetados con la propiedad del
diseño. La persistencia usa una conexión SQLite temporal por test (fixture `conn_tmp`).
"""

import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import proyeccion_tramos as pt
from proyeccion_tramos import (
    Tramo,
    HorizonteInvalidoError,
    SnapshotAusenteError,
    calcular_tramos,
    prorratear_reservas_anual,
    asegurar_tabla_portafolio_proy,
    persistir_snapshot_proy,
    limpiar_snapshots_proy,
    cargar_snapshot_proy,
    _COLUMNAS_PROY,
    _TABLA_PROY,
)


# ── Fixture: conexión SQLite temporal por test (persistencia recibe conn por parámetro) ──
@pytest.fixture
def conn_tmp(tmp_path):
    ruta = tmp_path / "test_proy.db"
    conn = sqlite3.connect(str(ruta))
    yield conn
    conn.close()


# ── Estrategias comunes ──
# Fechas acotadas para no disparar rangos absurdos; horizonte controlado por construcción.
_fechas = st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31))
# Ramos: alfabeto pequeño con espacios/mayúsculas para ejercitar la normalización.
_ramos = st.text(alphabet="ABCabc ", min_size=1, max_size=8).filter(lambda s: s.strip() != "")
_deltas = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)


def _meses(inicio: date, fin: date) -> int:
    # Conteo_Meses_Inclusivo: debe reflejar la MISMA regla que `calcular_tramos`
    # (feature proyeccion-por-tramos-multiactivo, fix de `_meses_entre`), que suma el
    # mes residual cuando `relativedelta` deja días sobrantes (`+1 si delta.days > 0`).
    # Así un año calendario completo (1-ene → 31-dic) cuenta 12 (no 11) y este helper
    # de apoyo permanece en sync con el `sub_horizonte` que producen los tramos.
    from dateutil.relativedelta import relativedelta
    d = relativedelta(fin, inicio)
    return d.years * 12 + d.months + (1 if d.days > 0 else 0)


# ==============================================================================
# Property 2: Cobertura del horizonte sin solapamiento
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 2: los tramos cubren el horizonte de forma
# contigua (sin gaps ni solapes), el primero arranca en fecha_inicio, el último cierra en
# fecha_fin, los cortes intermedios son 31-dic y el orden es cronológico ascendente.
@settings(max_examples=100, deadline=None)
@given(fi=_fechas, extra_meses=st.integers(min_value=1, max_value=60))
def test_prop2_cortes_cubren_horizonte_sin_solape(fi, extra_meses):
    from dateutil.relativedelta import relativedelta
    ff = fi + relativedelta(months=extra_meses)
    tramos = calcular_tramos(fi, ff)

    assert tramos, "debe haber al menos un tramo"
    assert tramos[0].fecha_inicio == fi
    assert tramos[-1].fecha_corte == ff
    # Contigüidad y orden ascendente.
    for i in range(1, len(tramos)):
        assert tramos[i].fecha_inicio == tramos[i - 1].fecha_corte + timedelta(days=1)
        assert tramos[i].fecha_corte > tramos[i - 1].fecha_corte
    # Cortes intermedios = 31-dic.
    for t in tramos[:-1]:
        assert (t.fecha_corte.month, t.fecha_corte.day) == (12, 31)
    # sub_horizonte >= 1 en todos.
    assert all(t.sub_horizonte >= 1 for t in tramos)
    # Índices consecutivos desde 0.
    assert [t.indice for t in tramos] == list(range(len(tramos)))


# ==============================================================================
# Property 3: Un solo tramo si el horizonte cabe en un año
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 3: si inicio y fin caen en el mismo año
# calendario, hay exactamente un tramo cuyo sub_horizonte es el horizonte completo.
@settings(max_examples=100, deadline=None)
@given(anio=st.integers(min_value=2020, max_value=2035),
       mes_ini=st.integers(min_value=1, max_value=11),
       meses_extra=st.integers(min_value=1, max_value=11))
def test_prop3_un_solo_tramo_en_mismo_anio(anio, mes_ini, meses_extra):
    from dateutil.relativedelta import relativedelta
    fi = date(anio, mes_ini, 1)
    ff = fi + relativedelta(months=meses_extra)
    # Solo considerar casos donde fin sigue en el mismo año.
    if ff.year != anio:
        return
    tramos = calcular_tramos(fi, ff)
    assert len(tramos) == 1
    assert tramos[0].sub_horizonte == _meses(fi, ff)


# ==============================================================================
# Property 4: Rechazo de horizonte < 1 mes
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 4: un horizonte menor a un mes lanza
# HorizonteInvalidoError.
@settings(max_examples=100, deadline=None)
@given(fi=_fechas, dias=st.integers(min_value=0, max_value=27))
def test_prop4_rechaza_horizonte_menor_a_un_mes(fi, dias):
    ff = fi + timedelta(days=dias)
    # Con el Conteo_Meses_Inclusivo, un periodo cuenta 0 meses solo si `relativedelta`
    # no deja meses NI días residuales (mismo inicio == fin). En cuanto hay >= 1 día el
    # conteo inclusivo es >= 1 y el horizonte deja de ser inválido, así que solo se
    # espera HorizonteInvalidoError cuando el conteo inclusivo es < 1.
    if _meses(fi, ff) >= 1:
        return
    with pytest.raises(HorizonteInvalidoError):
        calcular_tramos(fi, ff)


# ==============================================================================
# Property 5: Suma de deltas prorrateados = variación anual
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 5: para cada ramo con variación != 0, la suma
# de los deltas mensuales prorrateados es igual a su variación anual (dentro de tolerancia).
@settings(max_examples=100, deadline=None)
@given(
    variacion=st.dictionaries(_ramos, _deltas, min_size=0, max_size=6),
    sub_h=st.integers(min_value=1, max_value=24),
)
def test_prop5_suma_prorrateo_igual_variacion_anual(variacion, sub_h):
    mapa = prorratear_reservas_anual(variacion, sub_h)
    # Reconstruir la variación esperada por ramo NORMALIZADO replicando el comportamiento
    # del código: solo las variantes con valor != 0 escriben la entrada (las de valor 0
    # hacen `continue` sin sobreescribir), por lo que gana la ÚLTIMA variante no-cero.
    esperado: dict[str, float] = {}
    for ramo, val in variacion.items():
        rn = str(ramo).strip().upper()
        if not rn:
            continue
        v = float(val)
        if v != 0.0:
            esperado[rn] = v  # última no-cero gana (igual que el código)
    # Todo ramo presente en el mapa debe tener suma == su última variación no-cero.
    for ramo, meses in mapa.items():
        assert ramo in esperado
        assert abs(sum(meses.values()) - esperado[ramo]) <= 1e-3


# ==============================================================================
# Property 6: Formato y dominio del mapa prorrateado
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 6: las claves de mes están en 1..sub_horizonte,
# los valores son floats finitos y los ramos con variación 0 no aparecen.
@settings(max_examples=100, deadline=None)
@given(
    variacion=st.dictionaries(_ramos, _deltas, min_size=0, max_size=6),
    sub_h=st.integers(min_value=1, max_value=24),
)
def test_prop6_formato_dominio_mapa(variacion, sub_h):
    import math
    mapa = prorratear_reservas_anual(variacion, sub_h)
    for ramo, meses in mapa.items():
        assert all(1 <= m <= sub_h for m in meses)
        assert all(math.isfinite(v) for v in meses.values())
    # Un ramo NORMALIZADO está ausente del mapa si y solo si TODAS sus variantes (claves
    # que colisionan tras normalizar) tienen variación cero. Si alguna variante es != 0,
    # el ramo debe aparecer.
    alguna_no_cero: dict[str, bool] = {}
    for ramo, val in variacion.items():
        rn = str(ramo).strip().upper()
        if not rn:
            continue
        alguna_no_cero[rn] = alguna_no_cero.get(rn, False) or (float(val) != 0.0)
    for rn, hay_no_cero in alguna_no_cero.items():
        if hay_no_cero:
            assert rn in mapa
        else:
            assert rn not in mapa


# ==============================================================================
# Property 7: Reparto uniforme entre meses
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 7: los deltas mensuales de un ramo son
# aproximadamente uniformes (max - min <= tolerancia salvo el ajuste de residuo).
@settings(max_examples=100, deadline=None)
@given(
    ramo=_ramos,
    variacion=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    sub_h=st.integers(min_value=1, max_value=24),
)
def test_prop7_reparto_uniforme(ramo, variacion, sub_h):
    if variacion == 0.0:
        return
    mapa = prorratear_reservas_anual({ramo: variacion}, sub_h)
    rn = ramo.strip().upper()
    valores = list(mapa[rn].values())
    # El delta uniforme es variacion/sub_h; el residuo se acumula en el último mes.
    cuota = variacion / sub_h
    # Todos los meses salvo el último son exactamente la cuota.
    for m in range(1, sub_h):
        assert abs(mapa[rn][m] - cuota) <= 1e-6
    # El último mes difiere a lo sumo por el residuo de redondeo acumulado.
    assert abs(mapa[rn][sub_h] - cuota) <= 1e-3 * (abs(variacion) + 1)


# ==============================================================================
# Property 12: Idempotencia del esquema
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 12: asegurar el esquema N veces no lanza, no
# borra filas y deja la tabla existente.
@settings(max_examples=50, deadline=None)
@given(n=st.integers(min_value=1, max_value=6))
def test_prop12_idempotencia_esquema(tmp_path_factory, n):
    ruta = tmp_path_factory.mktemp("idem") / "t.db"
    conn = sqlite3.connect(str(ruta))
    try:
        for _ in range(n):
            asegurar_tabla_portafolio_proy(conn)
        # La tabla existe.
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLA_PROY,),
        ).fetchone()
        assert r is not None
    finally:
        conn.close()


# ==============================================================================
# Property 11: Reemplazo de corrida previa
# ==============================================================================

def _df_min(fecha_corte_ignorada, ids):
    """DataFrame mínimo con columnas de la allowlist para persistir."""
    return pd.DataFrame({
        "id_posicion": ids,
        "valor_mercado": [100.0] * len(ids),
        "nominal": [1000.0] * len(ids),
        "ramo": ["SOAT"] * len(ids),
        "asset_class_definitivo": ["DP_TF"] * len(ids),
    })


# Feature: proyeccion-por-tramos, Property 11: tras limpiar y reescribir, la tabla contiene
# solo los cortes de la corrida vigente.
@settings(max_examples=30, deadline=None)
@given(ids1=st.lists(st.text(alphabet="0123456789", min_size=1, max_size=4), min_size=1, max_size=5, unique=True),
       ids2=st.lists(st.text(alphabet="0123456789", min_size=1, max_size=4), min_size=1, max_size=5, unique=True))
def test_prop11_reemplazo_corrida_previa(tmp_path_factory, ids1, ids2):
    ruta = tmp_path_factory.mktemp("reemplazo") / "t.db"
    conn = sqlite3.connect(str(ruta))
    try:
        # Corrida 1.
        limpiar_snapshots_proy(conn)
        persistir_snapshot_proy(conn, _df_min(None, ids1), date(2026, 12, 31))
        # Corrida 2: limpia y escribe otro corte.
        limpiar_snapshots_proy(conn)
        persistir_snapshot_proy(conn, _df_min(None, ids2), date(2027, 12, 31))
        cortes = [r[0] for r in conn.execute(
            f"SELECT DISTINCT fecha_corte_proyeccion FROM {_TABLA_PROY}").fetchall()]
        assert cortes == ["2027-12-31"]
    finally:
        conn.close()


# ==============================================================================
# Property 13 (ejemplo): seguridad SQL con valores hostiles
# ==============================================================================

def test_prop13_sql_parametrizado_valores_hostiles(conn_tmp):
    hostil = "'; DROP TABLE portafolio_clasificado_proy; --"
    df = pd.DataFrame({
        "id_posicion": [hostil],
        "valor_mercado": [100.0],
        "nominal": [1000.0],
        "ramo": [hostil],
    })
    limpiar_snapshots_proy(conn_tmp)
    persistir_snapshot_proy(conn_tmp, df, date(2026, 12, 31))
    # La tabla sigue existiendo (no hubo inyección) y el valor hostil se guardó como dato.
    out = cargar_snapshot_proy(conn_tmp, date(2026, 12, 31))
    assert len(out) == 1
    assert out.iloc[0]["ramo"] == hostil


# ==============================================================================
# Ejemplos: snapshot ausente, round-trip básico
# ==============================================================================

def test_snapshot_ausente_lanza(conn_tmp):
    asegurar_tabla_portafolio_proy(conn_tmp)
    with pytest.raises(SnapshotAusenteError):
        cargar_snapshot_proy(conn_tmp, date(2099, 12, 31))


def test_persistir_y_cargar_roundtrip(conn_tmp):
    df = _df_min(None, ["A1", "A2", "A3"])
    limpiar_snapshots_proy(conn_tmp)
    n = persistir_snapshot_proy(conn_tmp, df, date(2026, 12, 31))
    assert n == 3
    out = cargar_snapshot_proy(conn_tmp, date(2026, 12, 31))
    assert set(out["id_posicion"]) == {"A1", "A2", "A3"}
    # Columnas de la allowlist ausentes en el df se cargan como NULL/None.
    assert "emisor" in out.columns


def test_persistir_descarta_fila_vm_no_numerico(conn_tmp):
    df = pd.DataFrame({
        "id_posicion": ["OK", "BAD"],
        "valor_mercado": [100.0, "no-numerico"],
        "nominal": [1000.0, 2000.0],
    })
    limpiar_snapshots_proy(conn_tmp)
    n = persistir_snapshot_proy(conn_tmp, df, date(2026, 12, 31))
    assert n == 1  # la fila BAD se descarta
    out = cargar_snapshot_proy(conn_tmp, date(2026, 12, 31))
    assert list(out["id_posicion"]) == ["OK"]


# ==============================================================================
# Tarea 5.2 — Motor: retrocompatibilidad y uso del override `mapa_reservas`
# ==============================================================================

def test_motor_mapa_reservas_none_llama_cargar(monkeypatch):
    """
    Con mapa_reservas=None (default), el motor debe derivar el mapa vía
    cargar_mapa_reservas (comportamiento actual, retrocompatible).
    """
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp

    llamadas = {"n": 0}
    _orig = cvr.cargar_mapa_reservas

    def _spy(ruta, fecha_inicio, meses_horizonte):
        llamadas["n"] += 1
        return {}  # mapa vacío: el motor corre sin operar reservas

    monkeypatch.setattr(cvr, "cargar_mapa_reservas", _spy)

    # Portafolio vacío → corrida barata; solo nos interesa la ruta de carga del mapa.
    mp.proyectar_portafolio(
        portafolio=[],
        fecha_inicio=date(2026, 5, 20),
        meses_horizonte=2,
        sendas={},
        mapa_reservas=None,
    )
    assert llamadas["n"] == 1  # se derivó desde la DB


def test_motor_mapa_reservas_override_no_llama_cargar(monkeypatch):
    """
    Con mapa_reservas provisto, el motor NO debe llamar a cargar_mapa_reservas: usa el
    override tal cual (feature proyeccion-por-tramos).
    """
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp

    llamadas = {"n": 0}

    def _spy(ruta, fecha_inicio, meses_horizonte):
        llamadas["n"] += 1
        return {}

    monkeypatch.setattr(cvr, "cargar_mapa_reservas", _spy)

    override = {"SOAT": {1: 10.0, 2: 10.0}}
    mp.proyectar_portafolio(
        portafolio=[],
        fecha_inicio=date(2026, 5, 20),
        meses_horizonte=2,
        sendas={},
        mapa_reservas=override,
    )
    assert llamadas["n"] == 0  # NO se derivó desde la DB; se usó el override


# ==============================================================================
# Tarea 6.2 — Refactor: _construir_df_proy_rf produce el df esperado
# ==============================================================================

class _TituloFake:
    """Doble ligero de Titulo con los atributos que lee _construir_df_proy_rf."""
    def __init__(self, id_posicion, fecha_vencimiento, valor_mercado, *,
                 es_reinversion=False, titulo_origen="", especie="X", nominal=0.0,
                 compania="C", ramo="R", portafolio="P", asset_class="DP_TF"):
        self.id_posicion = id_posicion
        self.fecha_vencimiento = fecha_vencimiento
        self.valor_mercado = valor_mercado
        self.es_reinversion = es_reinversion
        self.titulo_origen = titulo_origen
        self.especie = especie
        self.nominal = nominal
        self.compania = compania
        self.ramo = ramo
        self.portafolio = portafolio
        self.asset_class = asset_class


def test_construir_df_proy_rf_vivos_vencidos_reinversion():
    import app_dash

    fecha_corte = date(2027, 12, 31)
    # Portafolio base: 2 títulos (uno sobrevive, uno vence).
    df_base = pd.DataFrame({
        "id_posicion": ["VIVO", "VENCE"],
        "valor_mercado": [100.0, 50.0],
        "especie": ["A", "B"],
        "asset_class_definitivo": ["DP_TF", "DP_TF"],
    })
    # Motor: VIVO sobrevive con nuevo VM; VENCE vence; REINV es reinversión nueva.
    port = [
        _TituloFake("VIVO", date(2030, 1, 1), 120.0),
        _TituloFake("VENCE", date(2027, 6, 30), 0.0),
        _TituloFake("REINV", date(2031, 1, 1), 60.0, es_reinversion=True, titulo_origen="VENCE"),
    ]
    df_proy, resumen = app_dash._construir_df_proy_rf(df_base, port, fecha_corte)

    ids = set(df_proy["id_posicion"])
    assert "VENCE" not in ids            # vencido eliminado
    assert "VIVO" in ids and "REINV" in ids
    # VM del vivo actualizado por el motor.
    assert float(df_proy.loc[df_proy["id_posicion"] == "VIVO", "valor_mercado"].iloc[0]) == 120.0
    # VM de la reinversión.
    assert float(df_proy.loc[df_proy["id_posicion"] == "REINV", "valor_mercado"].iloc[0]) == 60.0
    assert resumen["actualizados"] == 1
    assert resumen["vencidos"] == 1
    assert resumen["reinversiones"] == 1


def test_construir_df_proy_rf_sin_port_devuelve_base():
    import app_dash
    df_base = pd.DataFrame({"id_posicion": ["X"], "valor_mercado": [10.0]})
    df_proy, resumen = app_dash._construir_df_proy_rf(df_base, [], date(2027, 12, 31))
    assert list(df_proy["id_posicion"]) == ["X"]
    assert resumen == {"actualizados": 0, "vencidos": 0, "reinversiones": 0}


# ==============================================================================
# Tarea 7 (motor, Opción A) — spread efectivo de cierre por título
# ==============================================================================

def _titulo_tf(spread_mercado):
    """Construye un Titulo TF mínimo para probar el spread efectivo de cierre."""
    import motor_proyeccion as mp
    t = mp.Titulo(
        id_posicion="TF1", especie="TFIT", emisor="NACION", isin="CO1", moneda="COP",
        fecha_emision=date(2020, 1, 1), fecha_vencimiento=date(2032, 1, 1),
        nominal=1000.0, tasa_cupon=7.0, spread_ipc=0.0, tipo_bono="TF",
        frecuencia="anual", tipo_tasa="EFECTIVO", base_anual=365, uso_tasa="I",
        clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
        asset_class="DP_TF", tasa_compra=8.0, tasa_mercado=8.0,
        valor_mercado=1000.0, valor_tir=1000.0,
    )
    t.spread_mercado = spread_mercado
    return t


def test_motor_expone_spread_efectivo_cierre_tf(monkeypatch):
    """
    Para un TF con spread_mercado=8.0 y shift_cop=100 bps en 12 meses, el spread efectivo
    de cierre debe ser 8.0 + 1.0 = 9.0 (shift completo al último mes).
    """
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp

    # Aislar COMPLETAMENTE la carga de reservas/universo/config del motor para no depender
    # de la DB temporal de la fixture autouse (que puede inyectar variaciones que operen
    # ventas y alteren el estado del título). Con esto el motor solo revalúa (aplica shift).
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(cvr, "cargar_universo_compra", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_ramos_reserva_matematica", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_parametros_reservas", lambda *a, **k: None)

    t = _titulo_tf(8.0)
    port = [t]
    # Pasar el mismo shift en cop/fs/uvr para que el resultado sea +100 bps sin depender de
    # a qué curva concreta (DP_TF vs CORP_TF) resuelva el asset_class en el entorno de test.
    mp.proyectar_portafolio(
        portafolio=port,
        fecha_inicio=date(2026, 1, 1),
        meses_horizonte=12,
        sendas={},
        shift_cop_bps=100.0,
        shift_fs_bps=100.0,
        shift_uvr_bps=100.0,
        mapa_reservas={},
    )
    assert hasattr(t, "_spread_efectivo_cierre")
    assert abs(t._spread_efectivo_cierre - 9.0) < 1e-6


def test_motor_spread_efectivo_sin_shift_es_spread_base(monkeypatch):
    """Sin shift, el spread efectivo de cierre es igual al spread base."""
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})

    t = _titulo_tf(8.0)
    mp.proyectar_portafolio(
        portafolio=[t], fecha_inicio=date(2026, 1, 1), meses_horizonte=12,
        sendas={}, shift_cop_bps=0.0, mapa_reservas={},
    )
    assert abs(t._spread_efectivo_cierre - 8.0) < 1e-6


# ==============================================================================
# Tarea 7 — Orquestador por tramos (P8, P9, P10, E1, E2) con doble de motor
# ==============================================================================

def _sembrar_portafolio_clasificado(conn, ids):
    """Crea portafolio_clasificado mínimo en la DB temporal con títulos RF."""
    df = pd.DataFrame({
        "id_posicion": ids,
        "especie": [f"TFIT{i}" for i in range(len(ids))],
        "emisor": ["NACION"] * len(ids),
        "isin": [f"CO{i}" for i in range(len(ids))],
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


def _doble_motor_factory(marca=None, falla_en_corte=None):
    """
    Devuelve un doble de proyectar_portafolio que:
      - respeta meses_horizonte (registrado para P10),
      - marca cada título con _spread_efectivo_cierre = spread + 1 (encadenamiento),
      - opcionalmente lanza si la fecha_inicio del tramo cae en un corte dado.
    Registra las llamadas en `marca` (lista).
    """
    def _doble(portafolio, fecha_inicio, meses_horizonte, sendas, **kwargs):
        if marca is not None:
            marca.append({
                "fecha_inicio": fecha_inicio,
                "meses_horizonte": meses_horizonte,
                "n_titulos": len(portafolio),
                "spreads_entrada": [getattr(t, "spread_mercado", None) for t in portafolio],
                "mapa_reservas": kwargs.get("mapa_reservas"),
            })
        if falla_en_corte is not None and fecha_inicio.year == falla_en_corte:
            raise RuntimeError("fallo simulado del motor")
        # Marcar spread efectivo de cierre = spread + 1 (incremental determinista).
        for t in portafolio:
            t._spread_efectivo_cierre = getattr(t, "spread_mercado", 0.0) + 1.0
        return []
    return _doble


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


@pytest.fixture
def orquestador_env(tmp_path, monkeypatch):
    """
    Prepara app_dash con DB temporal y dobles: get_conn → DB temporal,
    cargar_mapa_reservas → {}, cargar_portafolio_proyeccion → títulos sintéticos
    (evita hist_*), y portafolio_clasificado sembrado (base para el df_proy del tramo 0).
    """
    import app_dash
    import compras_ventas_reservas as cvr
    import motor_proyeccion as mp

    ruta = tmp_path / "orq.db"

    def _get_conn_tmp(*a, **k):
        return sqlite3.connect(str(ruta))

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp)
    monkeypatch.setattr(app_dash, "DB_PATH", str(ruta))
    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(app_dash, "obtener_trm_para", lambda *a, **k: 3900.0)
    # El orquestador importa cargar_portafolio_proyeccion desde motor_proyeccion.
    monkeypatch.setattr(mp, "cargar_portafolio_proyeccion",
                        lambda *a, **k: _titulos_sinteticos_rf(["A", "B", "C"]))

    # Sembrar portafolio_clasificado (base del df_proy del primer tramo).
    conn = sqlite3.connect(str(ruta))
    _sembrar_portafolio_clasificado(conn, ["A", "B", "C"])
    conn.close()
    return app_dash, ruta


# Feature: proyeccion-por-tramos, Property 8: nº de snapshots persistidos = nº de tramos.
def test_prop8_n_snapshots_igual_n_tramos(orquestador_env, monkeypatch):
    app_dash, ruta = orquestador_env
    import proyeccion_tramos as ptr
    # Doble del motor y de la carga del portafolio inicial (evita hist_*).
    import motor_proyeccion as _mp_patch; monkeypatch.setattr(_mp_patch, "proyectar_portafolio", _doble_motor_factory(), raising=False)

    from datetime import date as _d
    res, msg = app_dash._ejecutar_escenario_por_tramos(
        _d(2026, 1, 1), _d(2028, 12, 31),
        shifts_por_tramo=[{"shift_cop": 100}, {"shift_cop": 50}, {"shift_cop": 25}],
        uvr_t0=400.0,
    )
    # 3 tramos (2026, 2027, 2028) → 3 cortes distintos en la tabla.
    conn = sqlite3.connect(str(ruta))
    try:
        cortes = [r[0] for r in conn.execute(
            f"SELECT DISTINCT fecha_corte_proyeccion FROM {ptr._TABLA_PROY} ORDER BY 1").fetchall()]
    finally:
        conn.close()
    assert cortes == ["2026-12-31", "2027-12-31", "2028-12-31"]
    assert res is not None


# Feature: proyeccion-por-tramos, Property 10: el paso del motor sigue siendo mensual
# (meses_horizonte == sub_horizonte de cada tramo).
def test_prop10_paso_motor_mensual(orquestador_env, monkeypatch):
    app_dash, ruta = orquestador_env
    marca = []
    import motor_proyeccion as _mp_patch; monkeypatch.setattr(_mp_patch, "proyectar_portafolio", _doble_motor_factory(marca=marca), raising=False)

    from datetime import date as _d
    import proyeccion_tramos as ptr
    tramos = ptr.calcular_tramos(_d(2026, 1, 1), _d(2028, 12, 31))
    app_dash._ejecutar_escenario_por_tramos(
        _d(2026, 1, 1), _d(2028, 12, 31),
        shifts_por_tramo=[{}, {}, {}],
        uvr_t0=400.0,
    )
    # El motor se invocó una vez por tramo con meses_horizonte == sub_horizonte.
    assert len(marca) == len(tramos)
    for llamada, tramo in zip(marca, tramos):
        assert llamada["meses_horizonte"] == tramo.sub_horizonte
        # El mapa de reservas nunca tiene meses fuera de 1..sub_horizonte.
        mapa = llamada["mapa_reservas"] or {}
        for ramo, meses in mapa.items():
            assert all(1 <= m <= tramo.sub_horizonte for m in meses)


# Feature: proyeccion-por-tramos, Property 9: el t0 del tramo i usa el cierre del tramo
# anterior (spread efectivo acumulado), no el spread PIP original.
def test_prop9_encadenamiento_usa_cierre_anterior(orquestador_env, monkeypatch):
    app_dash, ruta = orquestador_env
    marca = []
    import motor_proyeccion as _mp_patch; monkeypatch.setattr(_mp_patch, "proyectar_portafolio", _doble_motor_factory(marca=marca), raising=False)

    from datetime import date as _d
    app_dash._ejecutar_escenario_por_tramos(
        _d(2026, 1, 1), _d(2028, 12, 31),
        shifts_por_tramo=[{}, {}, {}],
        uvr_t0=400.0,
    )
    # El doble marca spread_efectivo = spread + 1 por tramo. El spread de entrada de cada
    # tramo debe ser el del cierre anterior: tramo0=8.0, tramo1=9.0, tramo2=10.0.
    entradas = [set(l["spreads_entrada"]) for l in marca]
    assert entradas[0] == {8.0}
    assert entradas[1] == {9.0}
    assert entradas[2] == {10.0}


# Feature: proyeccion-por-tramos, E1: si un tramo falla, se reporta la fecha de corte y NO
# se expone resultado de fin de horizonte.
def test_e1_tramo_falla_reporta_corte(orquestador_env, monkeypatch):
    app_dash, ruta = orquestador_env
    # Falla en el tramo cuyo fecha_inicio cae en 2027.
    import motor_proyeccion as _mp_patch
    monkeypatch.setattr(_mp_patch, "proyectar_portafolio",
                        _doble_motor_factory(falla_en_corte=2027), raising=False)

    from datetime import date as _d
    res, msg = app_dash._ejecutar_escenario_por_tramos(
        _d(2026, 1, 1), _d(2028, 12, 31),
        shifts_por_tramo=[{}, {}, {}],
        uvr_t0=400.0,
    )
    assert res is None  # no se expone resultado incompleto
    # El mensaje menciona el corte del tramo fallido (2027-12-31) y no vuelca datos.
    import dash
    texto = str(msg)
    assert "2027" in texto


# ==============================================================================
# Tarea 7 — Dispatcher (P1: retrocompatibilidad punta_a_punta)
# ==============================================================================

# Feature: proyeccion-por-tramos, Property 1: el dispatcher en modo 'punta_a_punta'
# (default) delega en _ejecutar_escenario sin alterar el resultado.
def test_prop1_dispatch_punta_a_punta_delega(monkeypatch):
    import app_dash

    llamadas = {"n": 0, "args": None, "kwargs": None}
    _centinela = ({"parametros": {"modo": "punta_a_punta"}}, "OK")

    def _fake_ejecutar(*args, **kwargs):
        llamadas["n"] += 1
        llamadas["args"] = args
        llamadas["kwargs"] = kwargs
        return _centinela

    monkeypatch.setattr(app_dash, "_ejecutar_escenario", _fake_ejecutar)

    # El dispatcher debe delegar tal cual en _ejecutar_escenario y devolver su salida.
    salida = app_dash._ejecutar_escenario_dispatch("punta_a_punta", "2026-01-01", "2026-12-31", 100)
    assert salida is _centinela
    assert llamadas["n"] == 1
    assert llamadas["args"] == ("2026-01-01", "2026-12-31", 100)


def test_prop1_dispatch_modo_desconocido_usa_punta_a_punta(monkeypatch):
    """Un modo no reconocido cae en el comportamiento por defecto (punta_a_punta)."""
    import app_dash
    llamadas = {"n": 0}

    def _fake_ejecutar(*args, **kwargs):
        llamadas["n"] += 1
        return ({}, "OK")

    monkeypatch.setattr(app_dash, "_ejecutar_escenario", _fake_ejecutar)
    app_dash._ejecutar_escenario_dispatch("modo_raro", "2026-01-01", "2026-12-31")
    assert llamadas["n"] == 1


# Feature: proyeccion-por-tramos, E2: si falta el snapshot del tramo anterior, se reporta
# error verificable y no se completa el encadenamiento.
def test_e2_snapshot_ausente_detiene(orquestador_env, monkeypatch):
    app_dash, ruta = orquestador_env
    import motor_proyeccion as _mp_patch
    import proyeccion_tramos as ptr

    # Doble de motor que NO marca spread ni persiste (para forzar snapshot ausente en i>0
    # eliminamos el snapshot del tramo 0 tras el primer paso).
    orig_persistir = ptr.persistir_snapshot_proy

    def _persistir_que_no_guarda(conn, df, fecha_corte):
        return 0  # simula que no se persistió el snapshot

    monkeypatch.setattr(_mp_patch, "proyectar_portafolio", _doble_motor_factory(), raising=False)
    monkeypatch.setattr(app_dash.ptr if hasattr(app_dash, 'ptr') else ptr,
                        "persistir_snapshot_proy", _persistir_que_no_guarda, raising=False)
    # Parchear en el módulo que usa el orquestador (import proyeccion_tramos as ptr dentro).
    monkeypatch.setattr(ptr, "persistir_snapshot_proy", _persistir_que_no_guarda, raising=False)

    from datetime import date as _d
    res, msg = app_dash._ejecutar_escenario_por_tramos(
        _d(2026, 1, 1), _d(2028, 12, 31),
        shifts_por_tramo=[{}, {}, {}],
    )
    assert res is None
    texto = str(msg)
    assert "snapshot" in texto.lower() or "corte" in texto.lower()


# ==============================================================================
# Tarea 9.4 — Render de controles por tramo (UI)
# ==============================================================================

def test_render_controles_punta_a_punta_vacio():
    """En modo punta_a_punta el contenedor de tramos no muestra controles."""
    import app_dash
    out = app_dash.render_controles_tramos("punta_a_punta", 1, "2026-01-01", "2028-12-31")
    assert out == []


def test_render_controles_por_tramos_genera_por_anio():
    """En modo por_tramos se genera un bloque de controles por cada tramo del horizonte."""
    import app_dash
    out = app_dash.render_controles_tramos("por_tramos", 1, "2026-01-01", "2028-12-31")
    # Debe ser un contenedor con 3 bloques (2026, 2027, 2028). Serializamos y contamos
    # las etiquetas de tramo.
    texto = str(out)
    assert "Tramo 1" in texto
    assert "Tramo 2" in texto
    assert "Tramo 3" in texto
    # Incluye el selector de periodicidad de reservas.
    assert "Anual (prorrateada)" in texto or "Mensual" in texto


def test_render_controles_horizonte_invalido():
    """Horizonte < 1 mes → mensaje de advertencia, sin bloques.

    Con el Conteo_Meses_Inclusivo (fix feature proyeccion-por-tramos-multiactivo), un
    periodo con días residuales (p. ej. 2026-01-01 → 2026-01-10) YA cuenta 1 mes y es
    válido. El único horizonte que sigue siendo < 1 mes es fecha_inicio == fecha_fin
    (0 meses y 0 días residuales), que es el caso que debe disparar la advertencia.
    """
    import app_dash
    out = app_dash.render_controles_tramos("por_tramos", 1, "2026-01-01", "2026-01-01")
    assert "horizonte" in str(out).lower()
