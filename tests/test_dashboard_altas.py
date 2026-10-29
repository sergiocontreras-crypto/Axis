"""
Unit tests de los callbacks del dashboard de alta de portafolios y asignación de
subyacentes (feature escalabilidad-derivados-portafolios, tarea 4.3).

Invoca los callbacks `alta_portafolio` y `asignar_subyacente` de `app_dash.py`
DIRECTAMENTE (sin navegador), siguiendo el patrón de `tests/test_dashboard_reservas.py`.

Cubre:
- Mensajes de confirmación que identifican el `portafolio_porfin` / `id_posicion`
  afectado (Req 1.7, 2.7) y persistencia real en la DB temporal.
- Rechazos por validación: campo faltante, `portafolio_porfin` duplicado, swap ya
  registrado, subyacentes inexistentes, cero subyacentes (Req 1.5, 1.6, 2.4, 2.5, 2.6),
  sin escribir datos.
- Ocultamiento de los controles cuando no hay elementos nuevos en el corte: las
  funciones de sección devuelven `None` (Req 1.2, 2.2).
- Entradas adversarias de inyección SQL: los valores se enlazan como parámetros y se
  almacenan literalmente sin alterar el esquema ni otras filas (Req 5.2, refuerza 1.7/2.7).

`app_dash` se importa con `importorskip`; si no está disponible (entorno) los tests se
saltan. El fixture `db_tmp` (conftest) redirige `compras_ventas_reservas.get_conn` a una
DB temporal; aquí se redirige además `app_dash.get_conn` para que los callbacks lean y
escriban en esa DB temporal y NUNCA toquen la `portafolio.db` real.
"""

import sqlite3

import pandas as pd
import pytest

# Importar el dashboard (pesado); saltar con gracia si no está disponible.
app_dash = pytest.importorskip("app_dash")

from comun.validaciones_inputs import (  # noqa: E402  (import tras importorskip)
    detectar_portafolios_nuevos,
    detectar_swaps_sin_subyacente,
)


@pytest.fixture(autouse=True)
def _redirigir_get_conn_dashboard(db_tmp, monkeypatch):
    """
    El dashboard tiene su propia referencia `get_conn`; apuntarla a la DB temporal.

    `db_tmp` (conftest) ya redirige `comun.db.get_conn` y la del módulo de reservas.
    Aquí se agrega la del dashboard, que es la que usan los callbacks bajo prueba.
    """
    def _get_conn_tmp(check_same_thread: bool = False, timeout: float = 30.0):
        return sqlite3.connect(
            str(db_tmp), check_same_thread=check_same_thread, timeout=timeout
        )

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp, raising=True)
    yield


def _conn():
    """Conexión directa a la DB temporal vigente (vía el get_conn redirigido)."""
    return app_dash.get_conn()


def _crear_tablas_altas(portafolios_registrados=(), swaps_registrados=(), ids_corte=()):
    """
    Crea el estado de DB que leen los callbacks de altas (`_leer_estado_altas`):

    - `param_portafolios(portafolio_porfin, ramo, tipo, excluir)`
    - `param_diccionario_swaps(id_posicion, Subyacente)`
    - `portafolio_clasificado(id_posicion)`  (ids presentes en el corte)

    Rellena `param_*` con los registros ya existentes y `portafolio_clasificado`
    con los `id_posicion` del corte (usados para validar subyacentes, Req 2.4).
    """
    conn = _conn()
    try:
        conn.execute("DROP TABLE IF EXISTS param_portafolios")
        conn.execute("DROP TABLE IF EXISTS param_diccionario_swaps")
        conn.execute("DROP TABLE IF EXISTS portafolio_clasificado")
        conn.execute(
            "CREATE TABLE param_portafolios "
            "(portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"
        )
        conn.execute(
            "CREATE TABLE param_diccionario_swaps "
            "(id_posicion TEXT, Subyacente TEXT)"
        )
        conn.execute("CREATE TABLE portafolio_clasificado (id_posicion TEXT)")
        conn.executemany(
            "INSERT INTO param_portafolios (portafolio_porfin, ramo, tipo, excluir) "
            "VALUES (?, ?, ?, ?)",
            [(p, "R", "T", "NO") for p in portafolios_registrados],
        )
        conn.executemany(
            "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) "
            "VALUES (?, ?)",
            [(s, "X") for s in swaps_registrados],
        )
        conn.executemany(
            "INSERT INTO portafolio_clasificado (id_posicion) VALUES (?)",
            [(i,) for i in ids_corte],
        )
        conn.commit()
    finally:
        conn.close()


def _leer_portafolios():
    conn = _conn()
    try:
        return pd.read_sql_query(
            "SELECT portafolio_porfin, ramo, tipo, excluir FROM param_portafolios", conn
        )
    finally:
        conn.close()


def _leer_swaps():
    conn = _conn()
    try:
        return pd.read_sql_query(
            "SELECT id_posicion, Subyacente FROM param_diccionario_swaps", conn
        )
    finally:
        conn.close()


def _esquema(tabla):
    """Devuelve [(nombre, tipo), ...] de las columnas de una tabla (PRAGMA)."""
    conn = _conn()
    try:
        filas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    finally:
        conn.close()
    return [(f[1], f[2]) for f in filas]


# ==============================================================================
# alta_portafolio — confirmación (Req 1.7) y persistencia
# ==============================================================================

def test_alta_portafolio_confirmacion_identifica_id_y_persiste():
    """Alta válida ⇒ mensaje que identifica el portafolio afectado + fila persistida (Req 1.7)."""
    _crear_tablas_altas()
    filas = [{"portafolio_porfin": "0199NEW", "ramo": "VIDA", "tipo": "T1", "excluir": "NO"}]

    msg, data = app_dash.alta_portafolio(1, filas)

    texto = str(msg)
    assert "✅" in texto
    assert "0199NEW" in texto  # identifica el id afectado (Req 1.7)

    df = _leer_portafolios()
    assert df.shape[0] == 1
    fila = df.iloc[0]
    assert fila["portafolio_porfin"] == "0199NEW"
    assert fila["ramo"] == "VIDA"
    assert fila["tipo"] == "T1"
    assert fila["excluir"] == "NO"
    # La fila dada de alta ya no queda pendiente en la tabla editable.
    assert data == []


def test_alta_portafolio_normaliza_trim_del_id():
    """El `portafolio_porfin` se persiste sin espacios iniciales/finales (Req 1.3)."""
    _crear_tablas_altas()
    filas = [{"portafolio_porfin": "  0201X  ", "ramo": "AUTO", "tipo": "T2", "excluir": "SI"}]

    msg, _ = app_dash.alta_portafolio(1, filas)
    assert "0201X" in str(msg)

    df = _leer_portafolios()
    assert df.iloc[0]["portafolio_porfin"] == "0201X"
    assert df.iloc[0]["excluir"] == "SI"


# ==============================================================================
# alta_portafolio — rechazos por validación (Req 1.5, 1.6)
# ==============================================================================

def test_alta_portafolio_rechaza_campo_faltante_sin_escribir():
    """Ramo vacío ⇒ rechazo con mensaje del campo faltante, sin escribir (Req 1.5)."""
    _crear_tablas_altas()
    filas = [{"portafolio_porfin": "0199NEW", "ramo": "   ", "tipo": "T1", "excluir": "NO"}]

    msg, data = app_dash.alta_portafolio(1, filas)

    texto = str(msg)
    assert "⚠️" in texto
    assert "Ramo" in texto
    # No se escribió nada.
    assert _leer_portafolios().shape[0] == 0
    # La fila queda pendiente para corrección.
    assert data == filas


def test_alta_portafolio_rechaza_duplicado_identificando_id():
    """`portafolio_porfin` ya existente ⇒ rechazo identificando el duplicado, sin escribir (Req 1.6)."""
    _crear_tablas_altas(portafolios_registrados=["0150DUP"])
    filas = [{"portafolio_porfin": "0150DUP", "ramo": "VIDA", "tipo": "T1", "excluir": "NO"}]

    msg, data = app_dash.alta_portafolio(1, filas)

    texto = str(msg)
    assert "⚠️" in texto
    assert "0150DUP" in texto
    # Sigue existiendo una sola fila (la preexistente); no se agregó otra.
    assert _leer_portafolios().shape[0] == 1
    assert data == filas


def test_alta_portafolio_rechaza_duplicado_en_mismo_lote():
    """Dos filas con el mismo id en el lote: la primera se da de alta, la segunda se rechaza."""
    _crear_tablas_altas()
    filas = [
        {"portafolio_porfin": "0177Z", "ramo": "VIDA", "tipo": "T1", "excluir": "NO"},
        {"portafolio_porfin": "0177Z", "ramo": "AUTO", "tipo": "T2", "excluir": "SI"},
    ]

    msg, data = app_dash.alta_portafolio(1, filas)

    texto = str(msg)
    assert "0177Z" in texto  # confirmación de la primera
    assert "⚠️" in texto      # y aviso de la segunda (duplicada)
    # Solo una fila persistida.
    assert _leer_portafolios().shape[0] == 1
    # La fila duplicada queda pendiente.
    assert data == [filas[1]]


def test_alta_portafolio_sin_filas_es_noop():
    """Sin filas en la tabla ⇒ mensaje informativo y no_update."""
    from dash import no_update
    _crear_tablas_altas()
    msg, data = app_dash.alta_portafolio(1, [])
    assert "No hay portafolios nuevos" in str(msg)
    assert data is no_update


# ==============================================================================
# asignar_subyacente — confirmación (Req 2.7) y persistencia
# ==============================================================================

def test_asignar_subyacente_confirmacion_identifica_id_y_persiste():
    """Asignación válida ⇒ mensaje que identifica el swap + fila persistida con '|' (Req 2.7)."""
    _crear_tablas_altas(ids_corte=["A1", "A2", "1517"])
    filas = [{"id_posicion": "1517", "subyacentes": "A1 | A2"}]

    msg, data = app_dash.asignar_subyacente(1, filas)

    texto = str(msg)
    assert "✅" in texto
    assert "1517" in texto  # identifica el swap afectado (Req 2.7)

    df = _leer_swaps()
    assert df.shape[0] == 1
    assert df.iloc[0]["id_posicion"] == "1517"
    assert df.iloc[0]["Subyacente"] == "A1|A2"  # ids unidos por '|'
    assert data == []


# ==============================================================================
# asignar_subyacente — rechazos por validación (Req 2.4, 2.5, 2.6)
# ==============================================================================

def test_asignar_subyacente_rechaza_subyacente_inexistente_identificandolo():
    """Subyacente ausente del corte ⇒ rechazo que lo identifica, sin escribir (Req 2.4)."""
    _crear_tablas_altas(ids_corte=["A1", "1517"])
    filas = [{"id_posicion": "1517", "subyacentes": "A1 | FANTASMA"}]

    msg, data = app_dash.asignar_subyacente(1, filas)

    texto = str(msg)
    assert "⚠️" in texto
    assert "FANTASMA" in texto
    assert _leer_swaps().shape[0] == 0
    assert data == filas


def test_asignar_subyacente_rechaza_cero_subyacentes():
    """Cero subyacentes ⇒ rechazo indicando que se requiere al menos uno, sin escribir (Req 2.5)."""
    _crear_tablas_altas(ids_corte=["A1", "1517"])
    filas = [{"id_posicion": "1517", "subyacentes": "   "}]

    msg, data = app_dash.asignar_subyacente(1, filas)

    texto = str(msg)
    assert "⚠️" in texto
    assert "al menos un Subyacente" in texto
    assert _leer_swaps().shape[0] == 0
    assert data == filas


def test_asignar_subyacente_rechaza_swap_ya_registrado():
    """Swap ya en param_diccionario_swaps ⇒ rechazo que lo identifica, sin modificar (Req 2.6)."""
    _crear_tablas_altas(swaps_registrados=["2536"], ids_corte=["A1", "2536"])
    filas = [{"id_posicion": "2536", "subyacentes": "A1"}]

    msg, data = app_dash.asignar_subyacente(1, filas)

    texto = str(msg)
    assert "⚠️" in texto
    assert "2536" in texto
    # El registro previo (Subyacente='X') no se modifica y no se agregó otro.
    df = _leer_swaps()
    assert df.shape[0] == 1
    assert df.iloc[0]["Subyacente"] == "X"
    assert data == filas


def test_asignar_subyacente_sin_filas_es_noop():
    from dash import no_update
    _crear_tablas_altas()
    msg, data = app_dash.asignar_subyacente(1, [])
    assert "No hay swaps sin subyacente" in str(msg)
    assert data is no_update


# ==============================================================================
# Ocultamiento de controles sin elementos nuevos (Req 1.2, 2.2)
# ==============================================================================

def test_seccion_alta_portafolios_oculta_sin_nuevos():
    """Sin portafolios nuevos, la sección devuelve None (control oculto, Req 1.2)."""
    assert app_dash._seccion_alta_portafolios([]) is None


def test_seccion_alta_portafolios_visible_con_nuevos():
    """Con portafolios nuevos, la sección se construye (control visible, Req 1.1)."""
    seccion = app_dash._seccion_alta_portafolios(["0199NEW"])
    assert seccion is not None
    assert "0199NEW" in str(seccion)


def test_seccion_asignar_subyacentes_oculta_sin_nuevos():
    """Sin swaps sin subyacente, la sección devuelve None (control oculto, Req 2.2)."""
    assert app_dash._seccion_asignar_subyacentes([]) is None


def test_seccion_asignar_subyacentes_visible_con_nuevos():
    """Con swaps sin subyacente, la sección se construye (control visible, Req 2.1)."""
    seccion = app_dash._seccion_asignar_subyacentes(["1517"])
    assert seccion is not None
    assert "1517" in str(seccion)


def test_deteccion_nuevos_alimenta_ocultamiento():
    """
    Coherencia detección→ocultamiento: si la detección no arroja nuevos, la sección
    se oculta; si arroja nuevos, se muestra (Req 1.1, 1.2, 2.1, 2.2).
    """
    # Todos los portafolios del corte ya están registrados ⇒ sin nuevos ⇒ oculto.
    nuevos_pp = detectar_portafolios_nuevos(["0101", "0102"], ["0101", "0102"])
    assert nuevos_pp == []
    assert app_dash._seccion_alta_portafolios(nuevos_pp) is None

    # Un swap del corte no está registrado ⇒ hay nuevo ⇒ visible.
    nuevos_sw = detectar_swaps_sin_subyacente(["1517", "2536"], ["1517"])
    assert nuevos_sw == ["2536"]
    assert app_dash._seccion_asignar_subyacentes(nuevos_sw) is not None


# ==============================================================================
# Entradas adversarias de inyección SQL (Req 5.2 — refuerza 1.7 / 2.7)
# ==============================================================================

@pytest.mark.parametrize(
    "id_malicioso",
    [
        "0199'; DROP TABLE param_portafolios; --",
        "0199' OR '1'='1",
        'x"; DELETE FROM param_portafolios; --',
        "0199'); DROP TABLE param_portafolios;--",
    ],
)
def test_alta_portafolio_inyeccion_sql_se_almacena_literal(id_malicioso):
    """
    Un `portafolio_porfin` con carga de inyección SQL se enlaza como parámetro:
    se almacena literal (normalizado) y ni la tabla ni su esquema resultan
    alterados (Req 5.2).
    """
    _crear_tablas_altas()
    esquema_antes = _esquema("param_portafolios")

    filas = [{"portafolio_porfin": id_malicioso, "ramo": "VIDA", "tipo": "T1", "excluir": "NO"}]
    msg, _ = app_dash.alta_portafolio(1, filas)
    assert "✅" in str(msg)

    df = _leer_portafolios()  # la tabla sigue existiendo (no fue "DROP")
    assert df.shape[0] == 1
    # Se almacenó el literal normalizado (trim), no se ejecutó como SQL.
    assert df.iloc[0]["portafolio_porfin"] == id_malicioso.strip()
    # El esquema permanece intacto.
    assert _esquema("param_portafolios") == esquema_antes


def test_asignar_subyacente_inyeccion_en_subyacente_es_rechazada_o_literal():
    """
    Un subyacente con carga SQL no existe en el corte ⇒ se rechaza (Req 2.4) sin
    escribir; la tabla y su esquema permanecen intactos (Req 5.2).
    """
    payload_sql = "A1'; DROP TABLE param_diccionario_swaps; --"
    _crear_tablas_altas(ids_corte=["A1", "1517"])
    esquema_antes = _esquema("param_diccionario_swaps")

    filas = [{"id_posicion": "1517", "subyacentes": payload_sql}]
    msg, data = app_dash.asignar_subyacente(1, filas)

    # El id malicioso no está entre los ids del corte ⇒ rechazo por inexistente.
    assert "⚠️" in str(msg)
    assert _leer_swaps().shape[0] == 0
    assert data == filas
    # La tabla sigue existiendo con su esquema intacto (no se ejecutó el DROP).
    assert _esquema("param_diccionario_swaps") == esquema_antes


def test_asignar_subyacente_inyeccion_en_id_swap_se_almacena_literal():
    """
    Un `id_posicion` de swap con carga SQL, con subyacentes válidos del corte, se
    enlaza como parámetro y se almacena literal sin alterar el esquema (Req 5.2).
    """
    swap_sql = "9999'; DROP TABLE param_diccionario_swaps; --"
    _crear_tablas_altas(ids_corte=["A1", swap_sql])
    esquema_antes = _esquema("param_diccionario_swaps")

    filas = [{"id_posicion": swap_sql, "subyacentes": "A1"}]
    msg, _ = app_dash.asignar_subyacente(1, filas)
    assert "✅" in str(msg)

    df = _leer_swaps()  # la tabla sobrevive
    assert df.shape[0] == 1
    assert df.iloc[0]["id_posicion"] == swap_sql.strip()
    assert df.iloc[0]["Subyacente"] == "A1"
    assert _esquema("param_diccionario_swaps") == esquema_antes
