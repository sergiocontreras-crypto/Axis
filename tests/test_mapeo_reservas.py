"""
Batería de tests de la feature `unificar-mapeo-ramos-reservas`.

Cubre el Lector_Mapeo y el modelo `MapeoDeReservas` (normalización, deduplicación
determinista, semántica del destino vacío, robustez ante entrada NO confiable,
idempotencia/siembra del esquema), la regla de agregación de Capitalizadora, la
equivalencia con el mapeo histórico salvo RIA, la consistencia entre flujos, el
round-trip de persistencia desde el dashboard y las verificaciones estáticas de
deprecación.

Todos los tests corren contra una DB SQLite TEMPORAL (fixture autouse `db_tmp` en
conftest.py); nunca tocan la portafolio.db real.
"""

import logging
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_SUPPRESS_DB = [HealthCheck.function_scoped_fixture]

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    MapeoDeReservas,
    _normalizar_ramo,
    _TABLA_MAPEO_SALDO,
    _TABLA_MAPEO_INTERES,
    asegurar_tablas_mapeo_reservas,
    cargar_mapeo_saldo,
    cargar_mapeo_interes,
    cargar_mapeos_reservas,
)

RUTA = "ignorada-por-conftest"  # get_conn está monkeypatcheado a la DB temporal


# ── Estrategias comunes ──
# Etiquetas: alfabeto pequeño con mayúsculas/minúsculas y espacios (ejercita la
# normalización) sin disparar el health check de entropía.
_etiquetas = st.text(alphabet="ABCabc ", min_size=1, max_size=8).filter(
    lambda s: s.strip() != ""
)
# Destinos: ramos de portafolio (no vacíos) o "" (ignorar).
_destinos = st.one_of(
    st.text(alphabet="XYZxyz ", min_size=1, max_size=8).map(lambda s: s.strip().upper()).filter(bool),
    st.just(""),
)


def _limpiar_tabla(tabla):
    """Vacía una tabla de mapeo en la DB temporal (aislamiento entre inputs PBT)."""
    conn = cvr.get_conn()
    try:
        asegurar_tablas_mapeo_reservas(conn)
        conn.execute(f"DELETE FROM {tabla}")
        conn.commit()
    finally:
        conn.close()


def _insertar_filas(tabla, filas):
    """Inserta filas crudas (formato_origen, ramo_portafolio) en una tabla de mapeo."""
    conn = cvr.get_conn()
    try:
        asegurar_tablas_mapeo_reservas(conn)
        conn.executemany(
            f"INSERT OR REPLACE INTO {tabla} (formato_origen, ramo_portafolio) VALUES (?, ?)",
            filas,
        )
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# Property 1: Invariancia a la normalización de la etiqueta
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 1: consultar el mapeo por una
# variante de la etiqueta que difiere solo en espacios/mayúsculas devuelve el mismo
# destino que la forma canónica.
@settings(max_examples=100, deadline=None)
@given(etiqueta=_etiquetas, destino=_destinos)
def test_prop1_invariancia_normalizacion(etiqueta, destino):
    clave = _normalizar_ramo(etiqueta)
    mapeo = MapeoDeReservas(entradas={clave: destino})
    variantes = [
        etiqueta,
        f"  {etiqueta}  ",
        etiqueta.lower(),
        etiqueta.upper(),
        f"\t{etiqueta.lower()} ",
    ]
    canonico = mapeo.destino(clave)
    for v in variantes:
        assert mapeo.destino(v) == canonico
        assert mapeo.ignora(v) == mapeo.ignora(clave)


# ==============================================================================
# Property 2: Deduplicación determinista por clave normalizada
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 2: ante etiquetas que colisionan
# tras normalizar, el MapeoDeReservas resultante tiene exactamente una entrada por
# clave normalizada y el resultado es determinista.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    filas=st.lists(
        st.tuples(_etiquetas, _destinos), min_size=0, max_size=8
    )
)
def test_prop2_dedup_determinista(db_tmp, filas):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    # Insertar con clave normalizada (la PK colapsa colisiones; "última gana").
    filas_norm = [(_normalizar_ramo(e), d) for e, d in filas]
    _insertar_filas(_TABLA_MAPEO_SALDO, filas_norm)

    m1 = cargar_mapeo_saldo(RUTA)
    m2 = cargar_mapeo_saldo(RUTA)

    # Una entrada por clave normalizada.
    claves = list(m1.entradas.keys())
    assert len(claves) == len(set(claves))
    assert all(k == _normalizar_ramo(k) for k in claves)
    # Determinista: dos lecturas idénticas.
    assert m1.entradas == m2.entradas


# ==============================================================================
# Property 3: Destino vacío implica etiqueta ignorada
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 3: toda fila con destino "" hace
# que la etiqueta se represente como ignorada (ignora() verdadero, destino() == "").
@settings(max_examples=100, deadline=None)
@given(etiqueta=_etiquetas)
def test_prop3_destino_vacio_ignora(etiqueta):
    clave = _normalizar_ramo(etiqueta)
    mapeo = MapeoDeReservas(entradas={clave: ""})
    assert mapeo.ignora(etiqueta) is True
    assert mapeo.destino(etiqueta) == ""


# ==============================================================================
# Property 4: Robustez ante filas NO confiables
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 4: ante una mezcla de filas válidas
# e inválidas (etiqueta que normaliza a ""), el Lector_Mapeo no lanza ni aborta, y el
# resultado contiene exactamente las entradas válidas, sin claves vacías.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    validas=st.lists(st.tuples(_etiquetas, _destinos), min_size=0, max_size=6),
    invalidas=st.lists(
        st.tuples(st.sampled_from(["", "   ", "\t", " \n "]), _destinos),
        min_size=0,
        max_size=4,
    ),
)
def test_prop4_robustez_filas_no_confiables(db_tmp, validas, invalidas):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    # Insertar válidas con clave normalizada; inválidas se insertan crudas pero como su
    # PK es el texto en blanco, coexisten (distintas cadenas). El lector debe descartarlas.
    filas = [(_normalizar_ramo(e), d) for e, d in validas]
    # Las inválidas: usar cadenas en blanco distintas para no colisionar en PK.
    for i, (e, d) in enumerate(invalidas):
        filas.append((e + " " * i if e == "" else e, d))
    _insertar_filas(_TABLA_MAPEO_SALDO, filas)

    # No debe lanzar.
    mapeo = cargar_mapeo_saldo(RUTA)

    # Ninguna clave vacía.
    assert all(k.strip() != "" for k in mapeo.entradas)
    # Todas las claves válidas esperadas están presentes.
    esperadas = {_normalizar_ramo(e) for e, _ in validas if _normalizar_ramo(e) != ""}
    assert esperadas.issubset(set(mapeo.entradas.keys()))


# ==============================================================================
# Property 5: Idempotencia del esquema y siembra que preserva ediciones
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 5: asegurar el esquema 2 veces = 1
# vez (idempotencia); si está vacío se siembra con las semillas; si hay ediciones del
# usuario, no se sobrescriben.
@settings(max_examples=50, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    editar=st.booleans(),
    etiqueta=_etiquetas,
    destino=_destinos,
    n_llamadas=st.integers(min_value=1, max_value=3),
)
def test_prop5_idempotencia_y_siembra(db_tmp, editar, etiqueta, destino, n_llamadas):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)

    conn = cvr.get_conn()
    try:
        if editar:
            # Estado con edición del usuario: no debe sembrarse encima.
            conn.execute(
                f"INSERT OR REPLACE INTO {_TABLA_MAPEO_SALDO} "
                "(formato_origen, ramo_portafolio) VALUES (?, ?)",
                (_normalizar_ramo(etiqueta), destino),
            )
            conn.commit()

        for _ in range(n_llamadas):
            asegurar_tablas_mapeo_reservas(conn)

        filas = dict(
            conn.execute(
                f"SELECT formato_origen, ramo_portafolio FROM {_TABLA_MAPEO_SALDO}"
            ).fetchall()
        )
    finally:
        conn.close()

    if editar:
        # La edición del usuario se conserva exactamente (no se sobrescribe).
        assert filas.get(_normalizar_ramo(etiqueta)) == destino
    else:
        # Tabla vacía => se siembra con las semillas (no queda vacía).
        assert len(filas) > 0


# ==============================================================================
# Property 8: Consistencia de destino entre flujos (Encaje vs Motor)
# ==============================================================================

# Feature: unificar-mapeo-ramos-reservas, Property 8: para toda etiqueta, el destino
# que resuelve el Lector_Mapeo (Encaje) y el que usa el motor (cargar_mapeos_reservas)
# son iguales (misma fuente, misma normalización).
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(filas=st.lists(st.tuples(_etiquetas, _destinos), min_size=0, max_size=8))
def test_prop8_consistencia_entre_flujos(db_tmp, filas):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    _limpiar_tabla(_TABLA_MAPEO_INTERES)
    filas_norm = [(_normalizar_ramo(e), d) for e, d in filas]
    _insertar_filas(_TABLA_MAPEO_SALDO, filas_norm)
    _insertar_filas(_TABLA_MAPEO_INTERES, filas_norm)

    # Flujo Encaje (Lector_Mapeo tipado).
    mapeo_saldo = cargar_mapeo_saldo(RUTA)
    mapeo_interes = cargar_mapeo_interes(RUTA)
    # Flujo Motor (dicts crudos).
    d_saldo, d_interes = cargar_mapeos_reservas(RUTA)

    assert mapeo_saldo.entradas == d_saldo
    assert mapeo_interes.entradas == d_interes


# ==============================================================================
# Unit / ejemplo: existencia, seguridad de acceso, log genérico, RIA
# ==============================================================================

def test_existencia_operaciones_devuelven_mapeo(db_tmp):
    assert isinstance(cargar_mapeo_saldo(RUTA), MapeoDeReservas)
    assert isinstance(cargar_mapeo_interes(RUTA), MapeoDeReservas)


def test_seguridad_etiquetas_con_sql_se_tratan_como_dato(db_tmp):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    # Etiquetas con caracteres especiales de SQL: deben tratarse como DATO (sin inyección).
    peligrosas = [
        ("O'BRIEN", "RAMO A"),
        ("DROP; --", "RAMO B"),
        ("A'; DELETE FROM mapeo_ramos_saldo_reservas; --", "RAMO C"),
    ]
    filas = [(_normalizar_ramo(e), d) for e, d in peligrosas]
    _insertar_filas(_TABLA_MAPEO_SALDO, filas)
    mapeo = cargar_mapeo_saldo(RUTA)
    # La tabla no fue vaciada por inyección y los destinos se leen correctamente.
    for e, d in peligrosas:
        assert mapeo.destino(e) == d


def test_log_generico_no_incluye_contenido_de_fila(db_tmp, caplog):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    # Fila con etiqueta en blanco (clave normaliza a vacío) => se descarta con warning.
    _insertar_filas(_TABLA_MAPEO_SALDO, [("   ", "RAMO SECRETO")])
    with caplog.at_level(logging.WARNING):
        mapeo = cargar_mapeo_saldo(RUTA)
    # No aparece en el resultado.
    assert "" not in mapeo.entradas
    # El log no vuelca el contenido de la fila descartada.
    assert "RAMO SECRETO" not in caplog.text


def test_ria_se_ignora_desde_semilla(db_tmp):
    # Con la siembra por defecto, RIA debe estar mapeada a "" (ignorar).
    mapeo = cargar_mapeo_saldo(RUTA)
    assert mapeo.ignora("RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY") is True


# ==============================================================================
# Property 6: CAPITALIZADORA se obtiene de la fila TOTAL CAPI
# ==============================================================================

import generar_inputs_reservas as gir
from generar_inputs_reservas import (
    leer_saldos,
    COL_DIC_PREV,
    COLS_MESES_SALDO,
    FILA_TOTAL_CAPI,
)


def _fila_saldo(nombre, serie13):
    """
    Construye una tupla de celdas para la hoja 'Saldo Reservas':
      idx 1 = nombre; idx 2 (COL_DIC_PREV) = Dic25; idx 3..14 = Ene..Dic.
    `serie13` es [Dic25, Ene..Dic] (13 valores, pueden ser None).
    """
    fila = [None] * 15
    fila[1] = nombre
    fila[COL_DIC_PREV] = serie13[0]
    for k, j in enumerate(COLS_MESES_SALDO):
        fila[j] = serie13[k + 1]
    return tuple(fila)


# Series de 13 puntos (Dic25 + 12 meses) con valores positivos arbitrarios.
_serie13 = st.lists(
    st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    min_size=13, max_size=13,
)


# Feature: unificar-mapeo-ramos-reservas, Property 6: el valor mensual de CAPITALIZADORA
# en leer_saldos es la serie de la fila TOTAL CAPI, y no cambia al variar los sub-ramos
# DABUENAVIDA/MAGNATE/GANAMAS.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(serie_capi=_serie13, serie_sub=_serie13)
def test_prop6_capitalizadora_desde_total_capi(db_tmp, serie_capi, serie_sub):
    # Construir hoja: fila TOTAL CAPI con la serie objetivo + sub-ramos con otra serie.
    filas = {
        FILA_TOTAL_CAPI: _fila_saldo("TOTAL CAPI", serie_capi),
        FILA_TOTAL_CAPI - 3: _fila_saldo("DABUENAVIDA", serie_sub),
        FILA_TOTAL_CAPI - 2: _fila_saldo("MAGNATE", serie_sub),
        FILA_TOTAL_CAPI - 1: _fila_saldo("GANAMAS", serie_sub),
    }
    saldos = leer_saldos(filas)  # carga el mapeo sembrado por defecto
    capi = saldos.get(("CAPITALIZADORA", "CAPITALIZADORA"))
    assert capi is not None
    # La serie de CAPITALIZADORA es exactamente la de TOTAL CAPI, mes a mes.
    assert capi == list(serie_capi)


# ==============================================================================
# Property 7: Equivalencia con el mapeo histórico salvo RIA
# ==============================================================================

from calcular_encaje import HOMOLOGACION_RESERVAS_A_DICCIONARIO as _HOMOL_HIST

_RIA = "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY"


# Feature: unificar-mapeo-ramos-reservas, Property 7: homologar una etiqueta con la
# fuente única (sembrada) coincide con el diccionario histórico, EXCEPTO para RIA (que
# la fuente única ignora y el histórico conservaba).
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    etiqueta=st.sampled_from(sorted(_HOMOL_HIST.keys()) + [_RIA, "SOAT", "AUTOMOVILES"])
)
def test_prop7_equivalencia_salvo_ria(db_tmp, etiqueta):
    mapeo = cargar_mapeo_saldo(RUTA)  # sembrado por defecto
    destino_fuente = "" if mapeo.ignora(etiqueta) else mapeo.destino(etiqueta)
    destino_hist = _HOMOL_HIST.get(etiqueta, etiqueta.strip().upper())

    if _normalizar_ramo(etiqueta) == _normalizar_ramo(_RIA):
        # Único cambio intencional: la fuente única ignora RIA.
        assert mapeo.ignora(etiqueta) is True
    else:
        # Para el resto, el destino coincide con el histórico (normalizado).
        # DABUENAVIDA/MAGNATE/GANAMAS no están en la tabla de saldos (Capitalizadora usa
        # TOTAL CAPI), por lo que la fuente devuelve la etiqueta tal cual: se excluyen.
        if etiqueta.strip().upper() in ("DABUENAVIDA", "MAGNATE", "GANAMAS"):
            return
        assert destino_fuente == destino_hist or destino_fuente == _normalizar_ramo(destino_hist)


# ==============================================================================
# Property 9 / 10: persistencia desde el dashboard (round-trip y rechazo de vacías)
# ==============================================================================

from compras_ventas_reservas import (
    persistir_mapeo_reserva,
    ErrorPersistenciaMapeo,
)


# Feature: unificar-mapeo-ramos-reservas, Property 9: persistir una entrada desde el
# dashboard y luego cargar el mapeo devuelve el destino bajo la clave normalizada, con
# independencia de las variantes de espaciado/mayúsculas usadas al guardar.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(etiqueta=_etiquetas, destino=_destinos)
def test_prop9_roundtrip_persistencia(db_tmp, etiqueta, destino):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    # Guardar con una variante "sucia" (espacios + minúsculas).
    persistir_mapeo_reserva(_TABLA_MAPEO_SALDO, f"  {etiqueta.lower()}  ", destino)
    mapeo = cargar_mapeo_saldo(RUTA)
    clave = _normalizar_ramo(etiqueta)
    assert mapeo.entradas.get(clave) == destino
    # Consultar por otra variante devuelve el mismo destino.
    assert mapeo.destino(etiqueta.upper()) == (destino if destino != "" else "")


# Feature: unificar-mapeo-ramos-reservas, Property 10: persistir una etiqueta que
# normaliza a "" se rechaza (no crea ni actualiza fila) y notifica el motivo.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    blanco=st.sampled_from(["", "   ", "\t", " \n ", "  \t  "]),
    destino=_destinos,
)
def test_prop10_rechazo_etiqueta_vacia(db_tmp, blanco, destino):
    _limpiar_tabla(_TABLA_MAPEO_SALDO)
    conn = cvr.get_conn()
    try:
        antes = conn.execute(
            f"SELECT COUNT(*) FROM {_TABLA_MAPEO_SALDO}"
        ).fetchone()[0]
    finally:
        conn.close()

    with pytest.raises(ValueError):
        persistir_mapeo_reserva(_TABLA_MAPEO_SALDO, blanco, destino)

    conn = cvr.get_conn()
    try:
        despues = conn.execute(
            f"SELECT COUNT(*) FROM {_TABLA_MAPEO_SALDO}"
        ).fetchone()[0]
    finally:
        conn.close()
    # No se creó ni actualizó ninguna fila.
    assert antes == despues


def test_persistir_mapeo_tabla_no_permitida(db_tmp):
    with pytest.raises(ValueError):
        persistir_mapeo_reserva("tabla_arbitraria", "SOAT", "SOAT")


def test_persistir_mapeo_db_bloqueada(db_tmp, monkeypatch):
    class _ConnBloqueada:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def executemany(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cvr, "get_conn", lambda *a, **k: _ConnBloqueada())
    with pytest.raises(ErrorPersistenciaMapeo):
        persistir_mapeo_reserva(_TABLA_MAPEO_SALDO, "SOAT", "SOAT", reintentos=2)


# ==============================================================================
# Dashboard: render de la sección de mapeo (Tarea 8.5, Req 10.1)
# ==============================================================================

def test_dashboard_render_seccion_mapeo_lista_ambas_tablas(db_tmp):
    """
    El callback de render de la sección de mapeo debe listar las filas de la tabla
    elegida (saldos e interés) en una DataTable. Se valida contra la DB temporal.
    """
    import importlib
    # app_dash importa muchísimo; si no está disponible en el entorno de test, se omite.
    app_dash = pytest.importorskip("app_dash")

    # Sembrar la fuente única.
    cargar_mapeo_saldo(RUTA)
    cargar_mapeo_interes(RUTA)

    # Render de saldos.
    comp_saldo = app_dash.render_tabla_mapeo("saldo", None)
    # Render de interés.
    comp_interes = app_dash.render_tabla_mapeo("interes", None)

    # Ambos deben ser una DataTable con filas (id 'tabla-mapeo').
    for comp in (comp_saldo, comp_interes):
        assert getattr(comp, "id", None) == "tabla-mapeo"
        assert len(comp.data) > 0
        assert set(comp.data[0].keys()) == {"formato_origen", "ramo_portafolio"}
