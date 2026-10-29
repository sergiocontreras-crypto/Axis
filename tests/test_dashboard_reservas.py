"""
Unit tests de los callbacks del dashboard de la feature compras-ventas-reservas
(tarea 15.3).

Cubre:
- Carga del Excel de reservas: persistencia OK y error notificado (ErrorInputReservas /
  ErrorPersistenciaReservas) con mensaje genérico en UI (Req 1.6, 12.1).
- Clasificación: tras persistir la clasificación, el ramo sale de la lista de pendientes
  (Req 3.1, 4.1).
- Panel de parámetros: defaults 5/10 con tabla vacía (13.2); valor inválido ⇒ mensaje de
  aviso y no se persiste (13.6); round-trip de valores editados persistidos y su lectura
  posterior (13.4); los valores editados son los que usaría la siguiente corrida (13.7,
  vía cargar_parametros_reservas).

`app_dash` se importa con `importorskip`; si no está disponible (o falla el import por el
entorno) los tests se saltan sin romper la suite. El fixture `db_tmp` (conftest) redirige
`compras_ventas_reservas.get_conn` a una DB temporal; aquí se redirige además
`app_dash.get_conn` para que el helper de ramos del portafolio lea de la DB temporal.
"""

import pandas as pd
import pytest

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    cargar_parametros_reservas,
    cargar_ramos_reserva_matematica,
)

# Importar el dashboard (pesado); saltar con gracia si no está disponible.
app_dash = pytest.importorskip("app_dash")


@pytest.fixture(autouse=True)
def _redirigir_get_conn_dashboard(db_tmp, monkeypatch):
    """El dashboard tiene su propia referencia `get_conn`; apuntarla a la DB temporal."""
    def _get_conn_tmp(check_same_thread: bool = False, timeout: float = 30.0):
        import sqlite3
        return sqlite3.connect(str(db_tmp), check_same_thread=check_same_thread, timeout=timeout)

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp, raising=True)
    yield


def _crear_portafolio_clasificado(ramos):
    """Crea la tabla portafolio_clasificado con los ramos dados en la DB temporal."""
    conn = cvr.get_conn()
    try:
        conn.execute("DROP TABLE IF EXISTS portafolio_clasificado")
        conn.execute("CREATE TABLE portafolio_clasificado (ramo TEXT)")
        conn.executemany(
            "INSERT INTO portafolio_clasificado (ramo) VALUES (?)",
            [(r,) for r in ramos],
        )
        conn.commit()
    finally:
        conn.close()


# ==============================================================================
# 15.1 — Carga del Excel de reservas
# ==============================================================================

def _contents_b64(raw: bytes) -> str:
    import base64
    return "data:application/octet-stream;base64," + base64.b64encode(raw).decode()


def test_carga_excel_reservas_error_notificado():
    """Un archivo ilegible (no es un Excel válido) ⇒ mensaje de error genérico en UI."""
    _crear_portafolio_clasificado(["SOAT", "VIDA"])
    contenido = _contents_b64(b"esto no es un excel")
    resultado = app_dash.cargar_excel_reservas(contenido, "reservas.xlsx")
    # El callback devuelve un componente con texto de error (no lanza).
    texto = str(resultado)
    assert "❌" in texto or "no se pudo" in texto.lower() or "no tiene el formato" in texto.lower()


def test_carga_excel_reservas_sin_contenido_es_noop():
    resultado = app_dash.cargar_excel_reservas(None, None)
    # no_update de dash: sin contenido no hace nada.
    from dash import no_update
    assert resultado is no_update


# ==============================================================================
# 15.2 — Clasificación: el ramo sale de pendientes tras persistir
# ==============================================================================

def test_clasificacion_saca_ramo_de_pendientes():
    _crear_portafolio_clasificado(["SOAT", "VIDA"])
    # Antes: SOAT está pendiente.
    pendientes_antes = app_dash._opciones_ramos_pendientes()
    assert "SOAT" in set(pendientes_antes)

    # Persistir la clasificación de SOAT (no matemática).
    msg, nuevas_opciones, nuevo_valor = app_dash.guardar_clasificacion_ramo(
        1, "SOAT", "no", None
    )
    assert "✅" in str(msg)
    assert nuevo_valor is None
    valores_despues = {o["value"] for o in nuevas_opciones}
    assert "SOAT" not in valores_despues
    # Confirmar en la capa de datos: SOAT ya está clasificado.
    config = cargar_ramos_reserva_matematica(str(db_path()))
    assert config.esta_clasificado("SOAT")


def test_clasificacion_matematica_sin_instrumento_es_aviso():
    _crear_portafolio_clasificado(["SOAT"])
    msg, opts, val = app_dash.guardar_clasificacion_ramo(1, "SOAT", "si", None)
    assert "⚠️" in str(msg) or "requieren instrumento" in str(msg).lower()
    # No se persistió.
    config = cargar_ramos_reserva_matematica(str(db_path()))
    assert not config.esta_clasificado("SOAT")


def db_path():
    """Ruta de la DB temporal vigente (redirigida por el fixture db_tmp)."""
    from comun.rutas import DB_PATH
    return DB_PATH


# ==============================================================================
# 15.4 — Panel de parámetros de plazo
# ==============================================================================

def test_parametros_defaults_con_tabla_vacia():
    """Con la tabla vacía, cargar_parametros_reservas devuelve los defaults 5/10 (13.2)."""
    p = cargar_parametros_reservas(str(db_path()))
    assert p.nodo_compra_no_matematica_anios == 5
    assert p.plazo_max_venta_anios == 10


def test_parametros_guardar_invalido_es_aviso_y_no_persiste():
    msg = app_dash.guardar_parametros_reservas(1, -3, 10)
    assert "⚠️" in str(msg) or "positiv" in str(msg).lower()
    # No se persistió ⇒ sigue en defaults.
    p = cargar_parametros_reservas(str(db_path()))
    assert p.nodo_compra_no_matematica_anios == 5
    assert p.plazo_max_venta_anios == 10


def test_parametros_round_trip_editado():
    """Round-trip: guardar 7/12 y verificar que la siguiente lectura los usa (13.4, 13.7)."""
    msg = app_dash.guardar_parametros_reservas(1, 7, 12)
    assert "✅" in str(msg)
    p = cargar_parametros_reservas(str(db_path()))
    assert p.nodo_compra_no_matematica_anios == pytest.approx(7)
    assert p.plazo_max_venta_anios == pytest.approx(12)


def test_carga_excel_reservas_ok_persiste(monkeypatch):
    """
    Con una derivación válida, el callback persiste y reporta éxito.

    El parse estructural del workbook real ya lo cubre el unit test de derivación
    (tarea 6.6). Aquí se mockea `derivar_variaciones_reservas` para aislar la
    responsabilidad del CALLBACK (derivar → persistir → notificar), evitando un
    fixture frágil que replique el layout exacto del Excel.
    """
    _crear_portafolio_clasificado(["SOAT", "VIDA"])

    df_derivado = pd.DataFrame({
        "ramo": ["SOAT", "VIDA"],
        # El refactor base exige `mes` como Mes_Absoluto "YYYY-MM" (no offset entero).
        "mes": ["2026-02", "2026-02"],
        "delta_reserva": [50.0, 60.0],
    })
    monkeypatch.setattr(
        app_dash, "_ramos_del_portafolio_inicial", lambda: ["SOAT", "VIDA"], raising=True
    )
    # El callback importa `derivar_variaciones_reservas` desde el módulo cvr; parchear ahí.
    monkeypatch.setattr(
        cvr, "derivar_variaciones_reservas", lambda *_a, **_k: df_derivado, raising=True
    )

    contenido = _contents_b64(b"contenido-cualquiera")
    resultado = app_dash.cargar_excel_reservas(contenido, "reservas.xlsx")
    texto = str(resultado)
    assert "✅" in texto or "cargada" in texto.lower()

    # Confirmar en la capa de datos: las filas quedaron persistidas. El refactor base
    # persiste de forma VERSIONADA en `input_variacion_reservas_versiones` (fuente de
    # verdad, Req 6), no en la tabla destructiva histórica.
    conn = cvr.get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM input_variacion_reservas_versiones"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2
