"""
Batería de tests de las secciones 1-4 de la feature compras-ventas-reservas:
esquema idempotente, clasificación de ramos (carga/persistencia), parámetros de
plazo configurables (carga/persistencia) y cómputo de ramos pendientes.

Todos los tests corren contra una DB SQLite TEMPORAL (fixture autouse `db_tmp`
en conftest.py); nunca tocan la portafolio.db real.
"""

import math
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Property tests que usan la fixture de DB (function-scoped) escriben/leen la DB
# temporal; se limpia la tabla al inicio de cada input y se suprime el health check
# de fixture function-scoped (el aislamiento se garantiza limpiando la tabla).
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture]

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    ConfigRamosReserva,
    ErrorPersistenciaClasificacion,
    ErrorPersistenciaParametros,
    InstrumentoCompra,
    NODO_COMPRA_NO_MATEMATICA_ANIOS_DEFECTO,
    PLAZO_MAX_VENTA_ANIOS_DEFECTO,
    ParametrosReservas,
    _normalizar_ramo,
    asegurar_tabla_config_ramos,
    asegurar_tabla_parametros_reservas,
    cargar_parametros_reservas,
    cargar_ramos_reserva_matematica,
    persistir_clasificacion_ramo,
    persistir_parametros_reservas,
    ramos_pendientes_por_clasificar,
)

RUTA = "ignorada-por-conftest"  # get_conn está monkeypatcheado a la DB temporal


# ── Estrategias comunes ──
# Ramos: nombres cortos a partir de un alfabeto pequeño con mayúsculas/minúsculas y
# espacios (para ejercitar la normalización) sin disparar el health check de entropía.
_ramos = st.text(alphabet="ABCabc ", min_size=1, max_size=8).filter(
    lambda s: s.strip() != ""
)


def _limpiar_config_ramos():
    """Vacía config_ramos_reserva en la DB temporal (aislamiento entre inputs PBT)."""
    conn = cvr.get_conn()
    try:
        asegurar_tabla_config_ramos(conn)
        conn.execute("DELETE FROM config_ramos_reserva")
        conn.commit()
    finally:
        conn.close()


def _limpiar_parametros():
    """Vacía config_parametros_reservas en la DB temporal (aislamiento entre inputs)."""
    conn = cvr.get_conn()
    try:
        asegurar_tabla_parametros_reservas(conn)
        conn.execute("DELETE FROM config_parametros_reservas")
        conn.commit()
    finally:
        conn.close()


def _insertar_config_ramo(ruta, ramo, es_mat, instrumento):
    """Inserta una fila cruda en config_ramos_reserva (para tests de carga)."""
    conn = cvr.get_conn()
    try:
        asegurar_tabla_config_ramos(conn)
        conn.execute(
            "INSERT OR REPLACE INTO config_ramos_reserva "
            "(ramo, es_reserva_matematica, instrumento) VALUES (?, ?, ?)",
            (ramo, es_mat, instrumento),
        )
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# 1.3 — Property 22: Creación idempotente del esquema sin destruir datos
# ==============================================================================

# Feature: compras-ventas-reservas, Property 22: invocar asegurar_tabla_config_ramos
# una o varias veces no elimina ni altera las filas existentes (idempotencia).
@settings(max_examples=100, suppress_health_check=_SUPPRESS_DB, deadline=None)
@given(
    filas=st.lists(
        st.tuples(_ramos, st.integers(min_value=0, max_value=1)),
        min_size=0,
        max_size=6,
        unique_by=lambda t: _normalizar_ramo(t[0]),
    ),
    n_llamadas=st.integers(min_value=1, max_value=4),
)
def test_prop22_esquema_idempotente(db_tmp, filas, n_llamadas):
    _limpiar_config_ramos()
    conn = cvr.get_conn()
    try:
        asegurar_tabla_config_ramos(conn)
        for ramo, es_mat in filas:
            conn.execute(
                "INSERT OR REPLACE INTO config_ramos_reserva "
                "(ramo, es_reserva_matematica, instrumento) VALUES (?, ?, ?)",
                (_normalizar_ramo(ramo), es_mat, None),
            )
        conn.commit()
        antes = set(
            conn.execute(
                "SELECT ramo, es_reserva_matematica FROM config_ramos_reserva"
            ).fetchall()
        )
        # Invocar el asegurador varias veces no debe alterar las filas.
        for _ in range(n_llamadas):
            asegurar_tabla_config_ramos(conn)
        despues = set(
            conn.execute(
                "SELECT ramo, es_reserva_matematica FROM config_ramos_reserva"
            ).fetchall()
        )
        assert antes == despues
    finally:
        conn.close()


# ==============================================================================
# 2.2 — Unit: ConfigRamosReserva y _normalizar_ramo
# ==============================================================================

def test_config_ramos_clasificado_true_false_y_none():
    cfg = ConfigRamosReserva(
        es_reserva_matematica_por_ramo={"RENTAS VITALICIAS": True, "SOAT": False},
        instrumento_por_ramo={"RENTAS VITALICIAS": InstrumentoCompra.TES_UVR},
    )
    assert cfg.es_reserva_matematica("RENTAS VITALICIAS") is True
    assert cfg.es_reserva_matematica("SOAT") is False
    # Ramo sin fila => None (NO False).
    assert cfg.es_reserva_matematica("RAMO INEXISTENTE") is None
    assert cfg.esta_clasificado("RENTAS VITALICIAS") is True
    assert cfg.esta_clasificado("RAMO INEXISTENTE") is False


def test_normalizar_ramo_mayusculas_y_espacios():
    assert _normalizar_ramo("  rentas vitalicias  ") == "RENTAS VITALICIAS"
    assert _normalizar_ramo("SoAt") == "SOAT"
    # La clasificación es consistente con la normalización.
    cfg = ConfigRamosReserva(
        es_reserva_matematica_por_ramo={"SOAT": False}, instrumento_por_ramo={}
    )
    assert cfg.esta_clasificado("  soat ") is True


# ==============================================================================
# 3.2 — Property 20: carga desde la tabla, sin default hardcodeado
# ==============================================================================

# Feature: compras-ventas-reservas, Property 20: cargar_ramos_reserva_matematica
# construye la config SOLO desde las filas de la tabla; tabla vacía => config vacío.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    filas=st.lists(
        st.tuples(
            _ramos,
            st.integers(min_value=0, max_value=1),
            st.sampled_from(["TES_UVR", "TF", "BASURA", "", None]),
        ),
        min_size=0,
        max_size=6,
        unique_by=lambda t: _normalizar_ramo(t[0]),
    ),
)
def test_prop20_carga_sin_default(db_tmp, filas):
    _limpiar_config_ramos()
    for ramo, es_mat, instrumento in filas:
        _insertar_config_ramo(db_tmp, _normalizar_ramo(ramo), es_mat, instrumento)

    cfg = cargar_ramos_reserva_matematica(RUTA)

    for ramo, es_mat, instrumento in filas:
        rn = _normalizar_ramo(ramo)
        if es_mat == 1:
            # Reserva matemática: solo queda clasificado si el instrumento es válido.
            if str(instrumento).strip().upper() in ("TES_UVR", "TF"):
                assert cfg.es_reserva_matematica(rn) is True
                assert cfg.instrumento(rn) in (
                    InstrumentoCompra.TES_UVR,
                    InstrumentoCompra.TF,
                )
            else:
                # Instrumento no reconocido => fila descartada => sin clasificar.
                assert cfg.esta_clasificado(rn) is False
        else:
            assert cfg.es_reserva_matematica(rn) is False


def test_prop20_tabla_vacia_config_vacia(db_tmp):
    cfg = cargar_ramos_reserva_matematica(RUTA)
    assert cfg.esta_clasificado("CUALQUIERA") is False
    assert cfg.es_reserva_matematica("CUALQUIERA") is None


# ==============================================================================
# 3.4 — Property 23: round-trip de persistencia de la clasificación
# ==============================================================================

# Feature: compras-ventas-reservas, Property 23: persistir + recargar devuelve la
# misma clasificación (es_reserva_matematica e instrumento cuando aplica).
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    ramo=_ramos,
    es_mat=st.booleans(),
    instrumento=st.sampled_from(list(InstrumentoCompra)),
)
def test_prop23_roundtrip_clasificacion(db_tmp, ramo, es_mat, instrumento):
    _limpiar_config_ramos()
    instrumento_arg = instrumento if es_mat else None
    persistir_clasificacion_ramo(ramo, es_mat, instrumento_arg)
    cfg = cargar_ramos_reserva_matematica(RUTA)
    rn = _normalizar_ramo(ramo)
    assert cfg.es_reserva_matematica(rn) is es_mat
    if es_mat:
        assert cfg.instrumento(rn) is instrumento


# ==============================================================================
# 3.5 — Unit: validación de la persistencia de clasificación
# ==============================================================================

def test_persistir_clasificacion_rechaza_ramo_vacio(db_tmp):
    with pytest.raises(ValueError):
        persistir_clasificacion_ramo("   ", False, None)


def test_persistir_clasificacion_matematica_requiere_instrumento(db_tmp):
    with pytest.raises(ValueError):
        persistir_clasificacion_ramo("RENTAS VITALICIAS", True, None)


def test_persistir_clasificacion_no_matematica_acepta_none(db_tmp):
    persistir_clasificacion_ramo("SOAT", False, None)
    cfg = cargar_ramos_reserva_matematica(RUTA)
    assert cfg.es_reserva_matematica("SOAT") is False


def test_persistir_clasificacion_rechaza_claves_extra(db_tmp):
    # Firma fija sin **kwargs => una clave extra es TypeError.
    with pytest.raises(TypeError):
        persistir_clasificacion_ramo(  # type: ignore[call-arg]
            "SOAT", False, None, columna_extra="x"
        )


def _conn_bloqueada_factory():
    """Devuelve una fábrica get_conn cuya conexión simula 'database is locked'."""

    class _ConnBloqueada:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def executemany(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        def commit(self):
            pass

        def close(self):
            pass

    return lambda *a, **k: _ConnBloqueada()


def test_persistir_clasificacion_db_bloqueada(db_tmp, monkeypatch):
    monkeypatch.setattr(cvr, "get_conn", _conn_bloqueada_factory())
    with pytest.raises(ErrorPersistenciaClasificacion):
        persistir_clasificacion_ramo("SOAT", False, None, reintentos=2)


# ==============================================================================
# 3.8 — Property 29: defaulting de parámetros y equivalencia con el fijo
# ==============================================================================

def _insertar_param(ruta, clave, valor) -> bool:
    """
    Inserta un valor crudo en config_parametros_reservas. Devuelve True si se pudo
    persistir. SQLite tiene la columna `valor REAL NOT NULL`; almacena NaN como NULL,
    lo que viola la restricción — ese caso NO es representable en la tabla (en
    producción `persistir_parametros_reservas` rechaza NaN antes de escribir). Cuando
    el valor no es almacenable, se omite (equivale a 'clave ausente', que también debe
    resolverse con el default en la lectura). Así el test cubre los inválidos
    REALMENTE persistibles (0, negativos) sin chocar con la restricción NOT NULL.
    """
    conn = cvr.get_conn()
    try:
        asegurar_tabla_parametros_reservas(conn)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO config_parametros_reservas (clave, valor) VALUES (?, ?)",
                (clave, valor),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Valor no almacenable (p. ej. NaN → NULL). Se trata como clave ausente.
            return False
    finally:
        conn.close()


# Feature: compras-ventas-reservas, Property 29: cada parámetro es el valor
# persistido si es numérico y estrictamente positivo, o su default en cualquier
# otro caso, sin lanzar excepción.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    nodo=st.one_of(
        st.floats(min_value=0.1, max_value=40, allow_nan=False, allow_infinity=False),
        st.sampled_from([0.0, -3.0, float("nan"), float("inf")]),
        st.none(),
    ),
    plazo=st.one_of(
        st.floats(min_value=0.1, max_value=40, allow_nan=False, allow_infinity=False),
        st.sampled_from([0.0, -1.0, float("nan")]),
        st.none(),
    ),
)
def test_prop29_defaulting_parametros(db_tmp, nodo, plazo):
    _limpiar_parametros()
    nodo_persistido = _insertar_param(db_tmp, "nodo_compra_no_matematica_anios", nodo) if nodo is not None else False
    plazo_persistido = _insertar_param(db_tmp, "plazo_max_venta_anios", plazo) if plazo is not None else False

    params = cargar_parametros_reservas(RUTA)

    def _esperado(valor, persistido, defecto):
        # Clave ausente o valor no persistible (NaN→NULL) => default.
        if valor is None or not persistido:
            return defecto
        try:
            v = float(valor)
        except (TypeError, ValueError):
            return defecto
        if not math.isfinite(v) or v <= 0.0:
            return defecto
        return v

    assert params.nodo_compra_no_matematica_anios == _esperado(
        nodo, nodo_persistido, NODO_COMPRA_NO_MATEMATICA_ANIOS_DEFECTO
    )
    assert params.plazo_max_venta_anios == _esperado(
        plazo, plazo_persistido, PLAZO_MAX_VENTA_ANIOS_DEFECTO
    )


# ==============================================================================
# 3.9 — Unit: parámetros configurables
# ==============================================================================

def test_parametros_defaults_tabla_vacia(db_tmp):
    params = cargar_parametros_reservas(RUTA)
    assert params.nodo_compra_no_matematica_anios == 5.0
    assert params.plazo_max_venta_anios == 10.0


def test_parametros_invalido_cae_a_default(db_tmp):
    _insertar_param(db_tmp, "nodo_compra_no_matematica_anios", -2.0)
    _insertar_param(db_tmp, "plazo_max_venta_anios", 0.0)
    params = cargar_parametros_reservas(RUTA)
    assert params.nodo_compra_no_matematica_anios == 5.0
    assert params.plazo_max_venta_anios == 10.0


def test_parametros_roundtrip_positivo(db_tmp):
    persistir_parametros_reservas(7.0, 8.0)
    params = cargar_parametros_reservas(RUTA)
    assert params.nodo_compra_no_matematica_anios == 7.0
    assert params.plazo_max_venta_anios == 8.0


def test_persistir_parametros_rechaza_no_positivo(db_tmp):
    with pytest.raises(ValueError):
        persistir_parametros_reservas(-1.0, 10.0)
    with pytest.raises(ValueError):
        persistir_parametros_reservas(5.0, 0.0)


def test_persistir_parametros_db_bloqueada(db_tmp, monkeypatch):
    monkeypatch.setattr(cvr, "get_conn", _conn_bloqueada_factory())
    with pytest.raises(ErrorPersistenciaParametros):
        persistir_parametros_reservas(5.0, 10.0, reintentos=2)


# ==============================================================================
# 4.2 — Property 24: pendientes = ramos del portafolio menos los ya clasificados
# ==============================================================================

# Feature: compras-ventas-reservas, Property 24: ramos_pendientes_por_clasificar
# devuelve exactamente los ramos del portafolio (normalizados) sin fila en la
# clasificación; ningún ramo clasificado aparece.
@settings(max_examples=100, deadline=None)
@given(
    ramos_portafolio=st.sets(_ramos, min_size=0, max_size=8),
    clasificados=st.sets(_ramos, min_size=0, max_size=8),
)
def test_prop24_pendientes(ramos_portafolio, clasificados):
    cfg = ConfigRamosReserva(
        es_reserva_matematica_por_ramo={_normalizar_ramo(r): False for r in clasificados},
        instrumento_por_ramo={},
    )
    pendientes = ramos_pendientes_por_clasificar(ramos_portafolio, cfg)

    port_norm = {_normalizar_ramo(r) for r in ramos_portafolio}
    clasif_norm = {_normalizar_ramo(r) for r in clasificados}
    esperado = sorted(port_norm - clasif_norm)

    assert pendientes == esperado
    # Ningún clasificado aparece.
    assert all(r not in clasif_norm for r in pendientes)
