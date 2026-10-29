# -*- coding: utf-8 -*-
"""
Test de regresión por snapshot de `portafolio_clasificado` (tarea 5.3).

Objetivo (Req 5.4):
    "WHEN el Clasificador ejecuta después de estas mejoras con el mismo estado
    de insumos de Tipo, Ramo y Diccionario_Swaps, THE Clasificador SHALL producir
    para cada posición las mismas asignaciones de Encaje, Compania e Id_Cobertura
    que producía antes de estas mejoras."

Estrategia:
    1. Se construye una base de datos SQLite TEMPORAL (nunca la `portafolio.db`
       real) con un portafolio pequeño, fijo y determinista, y todas las tablas
       maestras que el Clasificador lee.
    2. Se ejecuta `clasificar_portafolio_diario` sobre esos insumos y se captura
       un baseline de (encaje, compania, id_cobertura) por `id_posicion`, que se
       persiste como fixture JSON en `tests/fixtures/`.
    3. Se aplican las ALTAS del dashboard (nuevas filas en `param_portafolios` y
       `param_diccionario_swaps` para portafolios/swaps que NO existen en los
       insumos del corte) y se vuelve a ejecutar el Clasificador sobre los MISMOS
       insumos del corte.
    4. Se verifica que (encaje, compania, id_cobertura) de las posiciones
       existentes permanecen INALTERADOS respecto al baseline capturado.

El Clasificador (`clasificar_portafolio.py`) se conecta a `sqlite3.connect(DB_PATH)`
usando el símbolo `DB_PATH` importado a su propio namespace, por lo que aquí se
monkeypatchea `clasificar_portafolio.DB_PATH` hacia la DB temporal.

**Validates: Requirements 5.4**
"""

import json
import os
import sqlite3
import sys

import pytest

# Raíz del proyecto y tests/ importables (robusto al modo de importación de pytest).
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import clasificar_portafolio  # noqa: E402

# Fecha de corte fija para todos los insumos.
_FECHA_CORTE = "2026-05-20"

# Ruta del baseline capturado (fixture versionable en tests/fixtures/).
_FIXTURES_DIR = os.path.join(_ESTE_DIR, "fixtures")
_BASELINE_PATH = os.path.join(_FIXTURES_DIR, "snapshot_clasificacion_baseline.json")

# Claves del snapshot que Req 5.4 exige inalteradas.
_CAMPOS_SNAPSHOT = ("encaje", "compania", "id_cobertura")


# ─────────────────────────────────────────────────────────────────────────────
# Construcción de la DB temporal con insumos FIJOS
# ─────────────────────────────────────────────────────────────────────────────
def _crear_esquema(conn):
    """Crea las tablas que lee el Clasificador con su esquema mínimo requerido."""
    cur = conn.cursor()

    # Portafolio del corte: títulos, forwards, opciones y swaps.
    cur.execute(
        """CREATE TABLE hist_titulos (
            fecha_reporte TEXT, id_posicion TEXT, especie TEXT, emisor TEXT,
            isin TEXT, calificacion TEXT, moneda TEXT, fecha_vencimiento TEXT,
            nominal REAL, valor_mercado REAL, tasa_compra REAL, tir REAL,
            tasa_mercado REAL, estado TEXT, portafolio TEXT,
            clasificacion_contable TEXT)"""
    )
    cur.execute(
        """CREATE TABLE hist_forwards (
            fecha_reporte TEXT, id_posicion TEXT, especie TEXT, emisor TEXT,
            moneda_cotizacion TEXT, fecha_vencimiento TEXT, nominal REAL,
            valor_mercado REAL, estado TEXT, portafolio TEXT, objetivo TEXT)"""
    )
    cur.execute(
        """CREATE TABLE hist_opciones (
            fecha_reporte TEXT, id_posicion TEXT, especie TEXT, emisor TEXT,
            moneda TEXT, fecha_vencimiento TEXT, nominal REAL, valor_mercado REAL,
            estado TEXT, portafolio TEXT)"""
    )
    cur.execute(
        """CREATE TABLE hist_swaps (
            fecha_reporte TEXT, id_posicion TEXT, especie TEXT, emisor TEXT,
            moneda_obl TEXT, fecha_vencimiento TEXT, nominal_obl REAL,
            valor_mercado REAL, estado TEXT, portafolio TEXT)"""
    )

    # Diccionario atemporal (merge por isin y por especie+emisor).
    cur.execute(
        """CREATE TABLE diccionario_clasificado (
            especie TEXT, emisor TEXT, isin TEXT, moneda TEXT,
            subtipo_vehiculo TEXT, clase_activo TEXT, codigo_solvencia TEXT,
            zona_geografica TEXT, sector TEXT, tipo_entidad TEXT,
            numeral_regimen TEXT, descripcion_regimen TEXT, asset_class_base TEXT,
            tasa_referencia TEXT, tasa_facial REAL, tipo_tasa TEXT, uso_tasa TEXT,
            periodicidad TEXT, base_anual TEXT, es_ipc TEXT)"""
    )

    # Tablas param_* de reglas.
    cur.execute(
        """CREATE TABLE param_matriz_reglas_asset_class (
            subtipo_vehiculo TEXT, tasa_referencia TEXT, tramo_plazo TEXT,
            moneda TEXT, sector TEXT, asset_class_final TEXT)"""
    )
    cur.execute(
        """CREATE TABLE param_matriz_reglas_solvencia (
            clase_activo TEXT, zona_geografica TEXT, tipo_entidad TEXT,
            grupo_calificacion TEXT, clasificacion_solvencia TEXT)"""
    )
    cur.execute(
        """CREATE TABLE param_categorias_solvencia (
            codigo_solvencia TEXT, clasificacion_solvencia TEXT, categoria TEXT,
            ponderacion REAL)"""
    )
    cur.execute(
        """CREATE TABLE param_calificaciones_faltantes (
            emisor TEXT, calificacion TEXT)"""
    )
    cur.execute(
        """CREATE TABLE param_objetivo_derivados (
            id_posicion TEXT, objetivo TEXT)"""
    )
    # Tablas param_* editadas por las altas del dashboard.
    cur.execute(
        """CREATE TABLE param_portafolios (
            portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"""
    )
    cur.execute(
        """CREATE TABLE param_diccionario_swaps (
            id_posicion TEXT, Subyacente TEXT)"""
    )
    conn.commit()


def _sembrar_insumos_corte(conn):
    """
    Inserta el portafolio del corte y las tablas maestras. Insumos FIJOS y
    deterministas. Los prefijos de `portafolio` ejercitan la derivación de
    compañía ('01'->Capitalizadora, '02'->Bolívar, '03'->Comerciales, otro->
    Sin Compañía). El swap SW1 cubre el título T1 para ejercitar id_cobertura.
    """
    cur = conn.cursor()

    # Títulos: cuatro prefijos distintos de portafolio.
    titulos = [
        # id, especie, emisor, isin, calif, moneda, venc, nominal, vm, estado, portafolio
        ("T1", "TES2028", "NACION", "ISIN0001", "NACION", "COP",
         "2028-05-20", 1_000_000.0, 1_050_000.0, "DP", "0100"),
        ("T2", "CDTBANCO", "BANCO_A", "ISIN0002", "AAA", "COP",
         "2027-05-20", 500_000.0, 505_000.0, "DP", "0200"),
        ("T3", "BONOEMP", "EMPRESA_B", "ISIN0003", "AA+", "COP",
         "2029-05-20", 300_000.0, 305_000.0, "DP", "0300"),
        ("T4", "CDTOTRO", "BANCO_C", "ISIN0004", "A", "USD",
         "2030-05-20", 200_000.0, 205_000.0, "DP", "9900"),
    ]
    cur.executemany(
        "INSERT INTO hist_titulos "
        "(fecha_reporte, id_posicion, especie, emisor, isin, calificacion, "
        " moneda, fecha_vencimiento, nominal, valor_mercado, tasa_compra, tir, "
        " tasa_mercado, estado, portafolio, clasificacion_contable) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?,?)",
        [
            (_FECHA_CORTE, t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8],
             t[9], t[10], "1CP")
            for t in titulos
        ],
    )

    # Forward de cobertura.
    cur.execute(
        "INSERT INTO hist_forwards "
        "(fecha_reporte, id_posicion, especie, emisor, moneda_cotizacion, "
        " fecha_vencimiento, nominal, valor_mercado, estado, portafolio, objetivo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (_FECHA_CORTE, "FW1", "FWD USDCOP", "BANCO_A", "USD",
         "2026-08-20", 100_000.0, 1_500.0, "DP", "0200", "cobertura"),
    )

    # Swap CCS que cubre el título T1 (id_cobertura).
    cur.execute(
        "INSERT INTO hist_swaps "
        "(fecha_reporte, id_posicion, especie, emisor, moneda_obl, "
        " fecha_vencimiento, nominal_obl, valor_mercado, estado, portafolio) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_FECHA_CORTE, "SW1", "CCS", "BANCO_A", "USD",
         "2028-05-20", 1_000_000.0, 25_000.0, "DP", "0100"),
    )

    # Diccionario atemporal: una fila por especie/isin del corte.
    dic_rows = [
        # especie, emisor, isin, moneda, subtipo_vehiculo, clase_activo, cod_solv,
        # zona, sector, tipo_entidad, numeral, desc, asset_class_base, tasa_ref
        ("TES2028", "NACION", "ISIN0001", "COP", "Titulo_Deuda_Publica",
         "DEUDA_PUBLICA", "CS_03", "LOCAL", "PUBLICO", "GOBIERNO",
         "1.1", "Deuda pública", "BMK_MATRIZ", "TF"),
        ("CDTBANCO", "BANCO_A", "ISIN0002", "COP", "CDT",
         "DEUDA_PRIVADA", "CS_MATRIZ", "LOCAL", "FINANCIERO", "BANCO",
         "2.1", "Deuda privada", "BMK_MATRIZ", "TF"),
        ("BONOEMP", "EMPRESA_B", "ISIN0003", "COP", "Bono_Ordinario",
         "DEUDA_PRIVADA", "CS_MATRIZ", "LOCAL", "REAL", "EMPRESA",
         "2.1", "Deuda privada", "BMK_MATRIZ", "TF"),
        ("CDTOTRO", "BANCO_C", "ISIN0004", "USD", "CDT",
         "DEUDA_PRIVADA", "CS_MATRIZ", "EXTERIOR", "FINANCIERO", "BANCO",
         "2.1", "Deuda privada", "BMK_MATRIZ", "TF"),
        # Derivados: match por especie+emisor (sin isin).
        ("FWD USDCOP", "BANCO_A", "", "USD", "Forward",
         "DERIVADO", "NO_APLICA", "LOCAL", "FINANCIERO", "BANCO",
         "3.4", "Derivado", "Derivados", "N/A"),
        ("CCS", "BANCO_A", "", "USD", "Swap",
         "DERIVADO", "NO_APLICA", "LOCAL", "FINANCIERO", "BANCO",
         "3.4", "Derivado", "Derivados", "N/A"),
    ]
    cur.executemany(
        "INSERT INTO diccionario_clasificado "
        "(especie, emisor, isin, moneda, subtipo_vehiculo, clase_activo, "
        " codigo_solvencia, zona_geografica, sector, tipo_entidad, "
        " numeral_regimen, descripcion_regimen, asset_class_base, tasa_referencia, "
        " tasa_facial, tipo_tasa, uso_tasa, periodicidad, base_anual, es_ipc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL)",
        dic_rows,
    )

    # Matriz asset class (resuelve BMK_MATRIZ). Reglas CUALQUIERA amplias.
    cur.executemany(
        "INSERT INTO param_matriz_reglas_asset_class "
        "(subtipo_vehiculo, tasa_referencia, tramo_plazo, moneda, sector, "
        " asset_class_final) VALUES (?,?,?,?,?,?)",
        [
            ("Titulo_Deuda_Publica", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA",
             "CUALQUIERA", "Deuda Pública Local"),
            ("CDT", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA",
             "Deuda Privada Local"),
            ("Bono_Ordinario", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA",
             "CUALQUIERA", "Deuda Privada Local"),
        ],
    )

    # Matriz de solvencia (reglas CUALQUIERA que siempre resuelven).
    cur.executemany(
        "INSERT INTO param_matriz_reglas_solvencia "
        "(clase_activo, zona_geografica, tipo_entidad, grupo_calificacion, "
        " clasificacion_solvencia) VALUES (?,?,?,?,?)",
        [
            ("DEUDA_PUBLICA", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA",
             "Categoria I"),
            ("DEUDA_PRIVADA", "CUALQUIERA", "CUALQUIERA", "GRUPO_AAA_AA",
             "Categoria II"),
            ("DEUDA_PRIVADA", "CUALQUIERA", "CUALQUIERA", "CUALQUIERA",
             "Categoria III"),
        ],
    )
    cur.executemany(
        "INSERT INTO param_categorias_solvencia "
        "(codigo_solvencia, clasificacion_solvencia, categoria, ponderacion) "
        "VALUES (?,?,?,?)",
        [
            ("CS_03", "Categoria I", "I", 0.0),
            ("CS_02", "Categoria II", "II", 0.5),
            ("CS_01", "Categoria III", "III", 1.0),
        ],
    )

    # param_objetivo_derivados: objetivo del swap.
    cur.execute(
        "INSERT INTO param_objetivo_derivados (id_posicion, objetivo) VALUES (?,?)",
        ("SW1", "cobertura"),
    )

    # param_portafolios: SOLO los portafolios del corte (estado inicial previo
    # a las altas del dashboard).
    cur.executemany(
        "INSERT INTO param_portafolios (portafolio_porfin, ramo, tipo, excluir) "
        "VALUES (?,?,?,?)",
        [
            ("0100", "VIDA", "PENSIONES", "NO"),
            ("0200", "SALUD", "GENERALES", "NO"),
            ("0300", "AUTOS", "GENERALES", "NO"),
            ("9900", "OTROS", "GENERALES", "NO"),
        ],
    )

    # param_diccionario_swaps: SOLO el swap del corte (SW1 cubre T1).
    cur.execute(
        "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) VALUES (?,?)",
        ("SW1", "T1"),
    )

    conn.commit()


def _aplicar_altas_dashboard(conn):
    """
    Simula las ALTAS del dashboard: registra portafolios y swaps que NO existen
    en el portafolio del corte (elementos "nuevos" para OTROS cortes). Estas
    altas NO deben alterar la clasificación de las posiciones del corte actual
    (Req 5.4). Se escriben con binding parametrizado (Req 5.2).
    """
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO param_portafolios (portafolio_porfin, ramo, tipo, excluir) "
        "VALUES (?,?,?,?)",
        [
            ("0400", "NUEVO_RAMO", "NUEVO_TIPO", "SI"),
            ("0500", "OTRO_RAMO", "OTRO_TIPO", "NO"),
        ],
    )
    cur.executemany(
        "INSERT INTO param_diccionario_swaps (id_posicion, Subyacente) VALUES (?,?)",
        [
            ("SW_NUEVO", "TX|TY"),
        ],
    )
    conn.commit()


def _ejecutar_clasificador(ruta_db, monkeypatch):
    """Ejecuta el Clasificador contra la DB temporal y devuelve el snapshot."""
    monkeypatch.setattr(clasificar_portafolio, "DB_PATH", str(ruta_db), raising=True)
    df = clasificar_portafolio.clasificar_portafolio_diario(_FECHA_CORTE)
    assert df is not None, "El Clasificador retornó None (error interno)."
    return _extraer_snapshot(str(ruta_db))


def _extraer_snapshot(ruta_db):
    """
    Lee `portafolio_clasificado` y devuelve un dict determinista
    {id_posicion: {encaje, compania, id_cobertura}} para las posiciones.
    """
    conn = sqlite3.connect(ruta_db)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id_posicion, encaje, compania, id_cobertura "
            "FROM portafolio_clasificado ORDER BY id_posicion"
        )
        filas = cur.fetchall()
    finally:
        conn.close()

    snapshot = {}
    for id_pos, encaje, compania, id_cob in filas:
        snapshot[str(id_pos)] = {
            "encaje": None if encaje is None else str(encaje),
            "compania": None if compania is None else str(compania),
            "id_cobertura": None if id_cob is None else str(id_cob),
        }
    return snapshot


def _cargar_o_capturar_baseline(snapshot_actual):
    """
    Devuelve el baseline persistido. Si no existe todavía, lo captura a partir
    del snapshot actual y lo persiste como fixture JSON versionable.
    """
    if os.path.exists(_BASELINE_PATH):
        with open(_BASELINE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)

    os.makedirs(_FIXTURES_DIR, exist_ok=True)
    with open(_BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot_actual, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return snapshot_actual


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: DB temporal poblada (independiente de `db_tmp` autouse de conftest)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def db_clasificacion(tmp_path):
    """Crea y puebla una DB temporal con los insumos fijos del corte."""
    ruta = tmp_path / "snapshot_clasificacion.db"
    conn = sqlite3.connect(str(ruta))
    try:
        _crear_esquema(conn)
        _sembrar_insumos_corte(conn)
    finally:
        conn.close()
    return ruta


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────
def test_snapshot_clasificacion_estable_tras_altas(db_clasificacion, monkeypatch):
    """
    Regresión por snapshot (Req 5.4): sobre insumos iguales del corte, las
    asignaciones de encaje/compania/id_cobertura permanecen inalteradas tras
    dar de alta portafolios y swaps NUEVOS desde el dashboard.
    """
    # 1) Corrida inicial → snapshot base (antes de las altas).
    snapshot_inicial = _ejecutar_clasificador(db_clasificacion, monkeypatch)
    assert snapshot_inicial, "El snapshot inicial no debe estar vacío."

    # Baseline persistido (se captura la primera vez).
    baseline = _cargar_o_capturar_baseline(snapshot_inicial)

    # El snapshot inicial debe coincidir con el baseline capturado.
    assert snapshot_inicial == baseline, (
        "La corrida sobre insumos iguales difiere del baseline capturado."
    )

    # 2) Aplicar las ALTAS del dashboard (portafolios/swaps NUEVOS, ajenos al corte).
    conn = sqlite3.connect(str(db_clasificacion))
    try:
        _aplicar_altas_dashboard(conn)
    finally:
        conn.close()

    # 3) Re-ejecutar el Clasificador sobre los MISMOS insumos del corte.
    snapshot_tras_altas = _ejecutar_clasificador(db_clasificacion, monkeypatch)

    # 4) Las asignaciones del corte deben ser idénticas al baseline (no-regresión).
    assert set(snapshot_tras_altas.keys()) == set(baseline.keys()), (
        "El conjunto de posiciones cambió tras las altas."
    )
    for id_pos, esperado in baseline.items():
        obtenido = snapshot_tras_altas[id_pos]
        for campo in _CAMPOS_SNAPSHOT:
            assert obtenido[campo] == esperado[campo], (
                f"Regresión en {campo} de la posición {id_pos}: "
                f"baseline={esperado[campo]!r} vs tras_altas={obtenido[campo]!r}"
            )


def test_snapshot_captura_encaje_compania_id_cobertura(db_clasificacion, monkeypatch):
    """
    Verifica que el snapshot captura correctamente los tres campos de Req 5.4:
      - compania se deriva por prefijo del portafolio,
      - id_cobertura relaciona swap SW1 <-> título T1,
      - encaje se marca 'SI'/'NO' por posición.
    """
    snapshot = _ejecutar_clasificador(db_clasificacion, monkeypatch)

    # Compañía por prefijo del código de portafolio.
    assert snapshot["T1"]["compania"] == "Capitalizadora"  # 0100 -> '01'
    assert snapshot["T2"]["compania"] == "Bolívar"          # 0200 -> '02'
    assert snapshot["T3"]["compania"] == "Comerciales"      # 0300 -> '03'
    assert snapshot["T4"]["compania"] == "Sin Compañía"     # 9900 -> otro

    # id_cobertura: swap SW1 cubre T1; T1 (título) referencia a SW1.
    assert snapshot["SW1"]["id_cobertura"] == "T1"
    assert snapshot["T1"]["id_cobertura"] == "SW1"

    # encaje presente y en el dominio {'SI','NO'} para toda posición.
    for id_pos, campos in snapshot.items():
        assert campos["encaje"] in ("SI", "NO"), (
            f"encaje inesperado para {id_pos}: {campos['encaje']!r}"
        )
