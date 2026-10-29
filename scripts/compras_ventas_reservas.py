"""
Compras y ventas de renta fija dirigidas por la variación de reservas.

Este módulo aísla la lógica pura y testeable de compras/ventas de títulos de
renta fija que el motor de proyección (`scripts/motor_proyeccion.py`) ejecuta cada
mes en función de la variación mensual de la reserva técnica de cada ramo. Aquí
viven, además, los helpers de esquema y acceso a las tablas de configuración que
se gestionan desde la capa de aplicación (no desde `init_db.py`).

Convenciones de esquema:
- Las tablas de configuración de reservas (`config_ramos_reserva`,
  `config_parametros_reservas`) se crean de forma IDEMPOTENTE
  (`CREATE TABLE IF NOT EXISTS`) desde la capa de aplicación, nunca en el bootstrap
  destructivo `init_db.py`. Los helpers `asegurar_tabla_*` no eliminan ni siembran
  datos: la tabla persiste entre corridas y se puebla exclusivamente desde el
  dashboard.

El módulo crecerá con tareas posteriores (clasificación de ramos, universo de
compra, waterfall de ventas, orquestación por mes).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# La raíz del proyecto (padre de scripts/) debe estar en el path para importar `comun`.
# `asegurar_tabla_config_ramos` recibe la conexión ya abierta; las funciones que abren
# la conexión ellas mismas (p. ej. `cargar_ramos_reserva_matematica`) reutilizan
# `comun.db.get_conn`, importado tras ajustar el path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from comun.db import get_conn

logger = logging.getLogger(__name__)


# ==============================================================================
# ESQUEMA IDEMPOTENTE: config_ramos_reserva (capa de aplicación, no init_db.py)
# ==============================================================================

def asegurar_tabla_config_ramos(conn: sqlite3.Connection) -> None:
    """
    Crea la tabla `config_ramos_reserva` de forma IDEMPOTENTE si no existe.

    La tabla almacena la clasificación de cada ramo (si es de reserva matemática y,
    en su caso, con qué instrumento compra). Se crea desde la capa de aplicación
    —invocada perezosamente por el dashboard (al abrir la pestaña *Ajustes*) y por el
    motor (antes de leer la tabla)— y NO desde `init_db.py`, que es un bootstrap
    destructivo de un solo uso.

    El DDL usa `CREATE TABLE IF NOT EXISTS`, por lo que es seguro llamarlo varias
    veces: no elimina la tabla, no borra filas y no siembra ningún dato. La tabla
    arranca vacía y persiste entre corridas.

    Columnas:
        ramo TEXT PRIMARY KEY        -- ramo/grupo normalizado (clave)
        es_reserva_matematica INTEGER NOT NULL  -- booleano 0/1
        instrumento TEXT             -- InstrumentoCompra (TES_UVR/TF); NULL si no aplica

    Args:
        conn: conexión sqlite3 abierta (p. ej. de `comun.db.get_conn`). El llamador
            gestiona el ciclo de vida de la conexión; esta función solo emite el DDL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_ramos_reserva (
            ramo TEXT PRIMARY KEY,
            es_reserva_matematica INTEGER NOT NULL,
            instrumento TEXT
        )
        """
    )
    conn.commit()


# ==============================================================================
# CLASIFICACIÓN DE RAMOS (cargada desde config_ramos_reserva, sin default hardcodeado)
# ==============================================================================

class InstrumentoCompra(Enum):
    """Instrumento con el que un ramo de reserva matemática ejecuta sus compras."""

    TES_UVR = "TES_UVR"   # TUVT
    TF = "TF"             # Tasa Fija (caso especial: Vida Individual-Matemática)


def _normalizar_ramo(ramo: str) -> str:
    """Normaliza una etiqueta de ramo para usarla como clave (strip/upper)."""
    return str(ramo).strip().upper()


@dataclass(frozen=True)
class ConfigRamosReserva:
    """
    Clasificación de ramos cargada desde `config_ramos_reserva` (poblada por el
    dashboard). Se construye únicamente a partir de las filas de la tabla; no hay
    conjunto por defecto hardcodeado.

    - es_reserva_matematica_por_ramo: {ramo_normalizado: bool} para cada ramo con fila
      en la tabla. Un ramo AUSENTE de este mapa está "sin clasificar".
    - instrumento_por_ramo: {ramo_normalizado: InstrumentoCompra} solo para ramos con
      es_reserva_matematica == True (el instrumento solo es significativo en ese caso).
    """

    es_reserva_matematica_por_ramo: dict[str, bool]
    instrumento_por_ramo: dict[str, InstrumentoCompra]

    def esta_clasificado(self, ramo: str) -> bool:
        """True si el ramo tiene fila en la tabla (fue clasificado por el usuario)."""
        return _normalizar_ramo(ramo) in self.es_reserva_matematica_por_ramo

    def es_reserva_matematica(self, ramo: str) -> bool | None:
        """
        True/False según la clasificación almacenada; None si el ramo está SIN
        clasificar. El motor NO debe tratar None como False: debe omitir el ramo.
        """
        return self.es_reserva_matematica_por_ramo.get(_normalizar_ramo(ramo))

    def instrumento(self, ramo: str) -> InstrumentoCompra:
        # Solo se consulta para ramos de reserva matemática ya clasificados.
        return self.instrumento_por_ramo.get(
            _normalizar_ramo(ramo), InstrumentoCompra.TES_UVR
        )


# Allowlist FIJA de columnas leídas de config_ramos_reserva. Nunca se interpola
# entrada externa en el SQL: los nombres son literales controlados por el código.
_COLUMNAS_CONFIG_RAMOS = ("ramo", "es_reserva_matematica", "instrumento")


def cargar_ramos_reserva_matematica(ruta_db: str) -> ConfigRamosReserva:
    """
    Asegura el esquema y lee `config_ramos_reserva`, devolviendo un
    `ConfigRamosReserva` construido SOLO con las filas de la tabla.

    Comportamiento:
      - Asegura primero el esquema de forma idempotente
        (`asegurar_tabla_config_ramos`, `CREATE TABLE IF NOT EXISTS`, sin dependencia
        de `init_db.py`).
      - Query parametrizada de solo lectura sobre una allowlist FIJA de columnas
        (`ramo`, `es_reserva_matematica`, `instrumento`). Nunca se interpola SQL.
      - Cada fila se trata como entrada NO confiable: se normaliza el ramo
        (`strip`/`upper`), se interpreta `es_reserva_matematica` como booleano y, cuando
        es True, se valida el instrumento contra el enum `InstrumentoCompra`. Las filas
        con ramo vacío o (siendo reserva matemática) instrumento no reconocido se
        descartan con log de advertencia (mensaje genérico, sin datos sensibles), sin
        abortar la corrida.
      - Si la tabla está vacía, el resultado es un `ConfigRamosReserva` vacío: TODOS los
        ramos quedan "sin clasificar" (NO hay fallback a un conjunto histórico).

    Args:
        ruta_db: ruta a la base de datos SQLite. Nota: `get_conn()` abre la base
            principal del proyecto (`comun.rutas.DB_PATH`); el parámetro se conserva
            por consistencia con las demás funciones del módulo (diseño/pruebas).

    Returns:
        ConfigRamosReserva con la clasificación derivada exclusivamente de las filas
        válidas de la tabla.
    """
    es_reserva_matematica_por_ramo: dict[str, bool] = {}
    instrumento_por_ramo: dict[str, InstrumentoCompra] = {}

    # Query de solo lectura con columnas de una allowlist fija (literales, sin
    # interpolación de entrada externa). Ver _COLUMNAS_CONFIG_RAMOS.
    consulta = (
        "SELECT ramo, es_reserva_matematica, instrumento FROM config_ramos_reserva"
    )

    conn = get_conn()
    try:
        asegurar_tabla_config_ramos(conn)
        filas = conn.execute(consulta).fetchall()
    finally:
        conn.close()

    for fila in filas:
        # Entrada NO confiable: normalizar y validar cada campo.
        ramo_norm = _normalizar_ramo(fila[0]) if fila[0] is not None else ""
        if not ramo_norm:
            logger.warning(
                "config_ramos_reserva: se descarta una fila con ramo vacío."
            )
            continue

        es_matematica = _interpretar_booleano(fila[1])

        if es_matematica:
            instrumento = _interpretar_instrumento(fila[2])
            if instrumento is None:
                logger.warning(
                    "config_ramos_reserva: se descarta un ramo de reserva "
                    "matemática con instrumento no reconocido."
                )
                continue
            es_reserva_matematica_por_ramo[ramo_norm] = True
            instrumento_por_ramo[ramo_norm] = instrumento
        else:
            es_reserva_matematica_por_ramo[ramo_norm] = False

    return ConfigRamosReserva(
        es_reserva_matematica_por_ramo=es_reserva_matematica_por_ramo,
        instrumento_por_ramo=instrumento_por_ramo,
    )


def _interpretar_booleano(valor: object) -> bool:
    """
    Interpreta un valor almacenado (entrada NO confiable) como booleano.

    Acepta enteros (0/1 y otros), strings ('0'/'1', 'true'/'false', 'sí'/'no') y
    None. Cualquier valor no reconocido se interpreta como False (conservador: no
    clasifica un ramo como reserva matemática ante datos ambiguos).
    """
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return False
    if isinstance(valor, (int, float)):
        return valor != 0
    texto = str(valor).strip().lower()
    if texto in ("1", "true", "t", "si", "sí", "yes", "y"):
        return True
    return False


def _interpretar_instrumento(valor: object) -> InstrumentoCompra | None:
    """
    Valida un instrumento almacenado (entrada NO confiable) contra el enum
    `InstrumentoCompra`. Normaliza (strip/upper) y devuelve el miembro del enum, o
    None si el valor no corresponde a ningún instrumento reconocido.
    """
    if valor is None:
        return None
    clave = str(valor).strip().upper()
    if not clave:
        return None
    try:
        return InstrumentoCompra(clave)
    except ValueError:
        return None


# ==============================================================================
# ESQUEMA IDEMPOTENTE: config_parametros_reservas (capa de aplicación, no init_db.py)
# ==============================================================================

# Valores por defecto de los parámetros de plazo (Req 13.2). Con estos defaults el
# comportamiento reproduce exactamente el de los literales fijos previos (5 y 10 años).
NODO_COMPRA_NO_MATEMATICA_ANIOS_DEFECTO: float = 5.0
PLAZO_MAX_VENTA_ANIOS_DEFECTO: float = 10.0

# Allowlist FIJA de claves reconocidas en config_parametros_reservas. Nunca se
# interpola en SQL: se usa como parámetros de una query de solo lectura y para mapear
# cada valor a su default. Claves fuera de este conjunto se ignoran.
_CLAVES_PARAMETROS_RESERVAS: tuple[str, ...] = (
    "nodo_compra_no_matematica_anios",
    "plazo_max_venta_anios",
)
_DEFECTOS_PARAMETROS_RESERVAS: dict[str, float] = {
    "nodo_compra_no_matematica_anios": NODO_COMPRA_NO_MATEMATICA_ANIOS_DEFECTO,
    "plazo_max_venta_anios": PLAZO_MAX_VENTA_ANIOS_DEFECTO,
}


def asegurar_tabla_parametros_reservas(conn: sqlite3.Connection) -> None:
    """
    Crea la tabla `config_parametros_reservas` de forma IDEMPOTENTE si no existe.

    La tabla es un almacén clave-valor (`clave TEXT PRIMARY KEY`, `valor REAL NOT NULL`)
    que guarda los parámetros de plazo configurables (nodo objetivo de compra para ramos
    no matemáticos y plazo máximo elegible de venta en el waterfall). Se crea desde la
    capa de aplicación —invocada perezosamente por el dashboard (al abrir el panel
    avanzado de *Ajustes*) y por el motor (antes de leer)— y NO desde `init_db.py`, que es
    un bootstrap destructivo de un solo uso.

    El DDL usa `CREATE TABLE IF NOT EXISTS`, por lo que es seguro llamarlo varias veces:
    no elimina la tabla, no borra filas y no siembra ningún dato. La tabla puede arrancar
    vacía; la ausencia de una clave se resuelve con su valor por defecto en la lectura
    (`cargar_parametros_reservas`), no con una semilla.

    Args:
        conn: conexión sqlite3 abierta (p. ej. de `comun.db.get_conn`). El llamador
            gestiona el ciclo de vida de la conexión; esta función solo emite el DDL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_parametros_reservas (
            clave TEXT PRIMARY KEY,
            valor REAL NOT NULL
        )
        """
    )
    conn.commit()


# ==============================================================================
# PARÁMETROS DE PLAZO CONFIGURABLES (cargados desde config_parametros_reservas, Req 13)
# ==============================================================================

@dataclass(frozen=True)
class ParametrosReservas:
    """
    Parámetros de plazo configurables leídos de `config_parametros_reservas` (Req 13).

    - nodo_compra_no_matematica_anios: nodo objetivo (años) para la compra de ramos que
      NO son de reserva matemática (por defecto 5).
    - plazo_max_venta_anios: plazo máximo (años) elegible para vender en el waterfall
      (por defecto 10).

    Ambos son floats positivos; con los defaults (5 y 10) el comportamiento reproduce
    exactamente el de los literales fijos previos (Req 13.2).
    """

    nodo_compra_no_matematica_anios: float = NODO_COMPRA_NO_MATEMATICA_ANIOS_DEFECTO
    plazo_max_venta_anios: float = PLAZO_MAX_VENTA_ANIOS_DEFECTO


def _coercer_parametro_positivo(clave: str, valor_crudo: object) -> float:
    """
    Coerciona un valor almacenado (NO confiable) a float estrictamente positivo y finito.

    Devuelve el valor coercionado si es un número finito y > 0; en caso contrario (valor
    ausente/None, no numérico, NaN/inf, o no positivo) devuelve el valor por defecto de la
    clave y registra una advertencia genérica (sin volcar el dato para no filtrar
    contenido no confiable en logs). Req 13.6.
    """
    import logging
    import math

    defecto = _DEFECTOS_PARAMETROS_RESERVAS[clave]
    try:
        valor = float(valor_crudo)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "Parámetro de reservas '%s' no es numérico; se usa el valor por defecto.",
            clave,
        )
        return defecto

    if not math.isfinite(valor) or valor <= 0.0:
        logging.getLogger(__name__).warning(
            "Parámetro de reservas '%s' no es finito y positivo; se usa el valor por defecto.",
            clave,
        )
        return defecto

    return valor


def cargar_parametros_reservas(ruta_db: str) -> ParametrosReservas:
    """
    Asegura el esquema (`asegurar_tabla_parametros_reservas`) y lee
    `config_parametros_reservas`, devolviendo un `ParametrosReservas`. El motor la llama
    UNA sola vez al inicio de la corrida (Req 13.3, 13.4).

    Entrada NO confiable (Req 13.6): cada valor almacenado se valida como numérico y
    ESTRICTAMENTE POSITIVO y finito. Si una clave falta, no es numérica, es NaN/inf o no
    es positiva, se aplica su valor por defecto (5 para el nodo de compra, 10 para el
    plazo máximo de venta) SIN abortar la corrida. Se usa una query PARAMETRIZADA de solo
    lectura sobre la allowlist FIJA de claves
    ({'nodo_compra_no_matematica_anios', 'plazo_max_venta_anios'}); nunca se interpola SQL.
    Las claves desconocidas se ignoran (no aparecen en la allowlist de la consulta).

    Args:
        ruta_db: ruta a la base de datos. Nota: el acceso se realiza siempre contra la DB
            principal vía `comun.db.get_conn()` (misma convención que el dashboard).

    Returns:
        ParametrosReservas con los dos parámetros validados o sus defaults.
    """
    import logging

    from comun.db import get_conn

    valores: dict[str, object] = {}
    conn = get_conn()
    try:
        asegurar_tabla_parametros_reservas(conn)
        # Query parametrizada de solo lectura sobre la allowlist fija de claves. Los
        # placeholders '?' evitan cualquier interpolación de SQL; el número de
        # placeholders coincide con la cantidad de claves permitidas.
        placeholders = ",".join("?" for _ in _CLAVES_PARAMETROS_RESERVAS)
        filas = conn.execute(
            f"SELECT clave, valor FROM config_parametros_reservas WHERE clave IN ({placeholders})",
            _CLAVES_PARAMETROS_RESERVAS,
        ).fetchall()
        for clave, valor in filas:
            # Solo se aceptan claves de la allowlist (la propia consulta ya filtra, pero
            # se reafirma para ignorar cualquier fila inesperada sin abortar).
            if clave in _DEFECTOS_PARAMETROS_RESERVAS:
                valores[str(clave)] = valor
            else:
                logging.getLogger(__name__).warning(
                    "Clave desconocida en config_parametros_reservas; se ignora."
                )
    finally:
        conn.close()

    return ParametrosReservas(
        nodo_compra_no_matematica_anios=_coercer_parametro_positivo(
            "nodo_compra_no_matematica_anios",
            valores.get("nodo_compra_no_matematica_anios"),
        ),
        plazo_max_venta_anios=_coercer_parametro_positivo(
            "plazo_max_venta_anios",
            valores.get("plazo_max_venta_anios"),
        ),
    )


# ==============================================================================
# UNIVERSO DE COMPRA (títulos comprables desde hist_pip: TES UVR / TUVT y TF)
# ==============================================================================

# Prefijos de nemotécnico (nemo) que identifican cada instrumento comprable en
# hist_pip. Son literales FIJOS controlados por el código; nunca provienen de
# entrada externa y se usan como patrón LIKE en una query PARAMETRIZADA (jamás se
# interpolan en el SQL):
#   - TES UVR:            nemo LIKE 'TUVT%'  (TUVT = TES denominados en UVR).
#   - TF (deuda pública): nemo LIKE 'TFIT%'  (TFIT = TES Tasa Fija en pesos).
_PREFIJO_NEMO_TES_UVR = "TUVT%"
_PREFIJO_NEMO_TF = "TFIT%"

# Allowlist FIJA de columnas leídas de hist_pip para el universo de compra. Son las
# que necesitan los selectores de la tarea 7.2 (nemo, isin, vencimiento, cupón,
# rendimiento, frecuencia de cupón, moneda). Todas existen en el esquema de hist_pip
# (ver gestor_informes.inicializar_tablas). Nombres literales, sin interpolación de
# entrada externa.
_COLUMNAS_UNIVERSO_COMPRA: tuple[str, ...] = (
    "nemo",
    "codigo_isin",
    "fecha_vencimiento",
    "cupon",
    "rendimiento",
    "frecuencia_pago_cupon",
    "moneda",
)


@dataclass
class UniversoCompra:
    """
    Universo de títulos comprables leídos de `hist_pip` a la `fecha_pip` de corte.

    - tes_uvr: TES denominados en UVR (nemo LIKE 'TUVT%'), con las columnas que
      necesitan los selectores aguas abajo (tarea 7.2): nemo, codigo_isin,
      fecha_vencimiento, cupon, rendimiento, frecuencia_pago_cupon, moneda.
    - tf: TES Tasa Fija de deuda pública (nemo LIKE 'TFIT%'), con las mismas columnas.

    Ambos son DataFrames de pandas (posiblemente vacíos, con las columnas de la
    allowlist) para que los consumidores puedan operar sin ramas especiales cuando no
    hay universo disponible.
    """

    tes_uvr: "pd.DataFrame"
    tf: "pd.DataFrame"


def _universo_vacio() -> UniversoCompra:
    """
    Construye un `UniversoCompra` con DataFrames vacíos pero con las columnas de la
    allowlist, para devolver ante `hist_pip` inexistente/vacía sin que los consumidores
    tengan que manejar el caso de columnas ausentes.
    """
    import pandas as pd

    vacio = pd.DataFrame(columns=list(_COLUMNAS_UNIVERSO_COMPRA))
    return UniversoCompra(tes_uvr=vacio.copy(), tf=vacio.copy())


def cargar_universo_compra(ruta_db: str, fecha_pip: str | None) -> UniversoCompra:
    """
    Carga el universo de compra (TES UVR / TUVT y TF) desde la tabla `hist_pip`.

    Comportamiento:
      - Abre la conexión con `comun.db.get_conn()` (base principal del proyecto) y la
        cierra siempre al terminar.
      - Si la tabla `hist_pip` no existe o está vacía, devuelve un `UniversoCompra` con
        DataFrames vacíos (con las columnas de la allowlist) SIN abortar la corrida.
      - Selecciona una allowlist FIJA de columnas (`_COLUMNAS_UNIVERSO_COMPRA`) mediante
        queries PARAMETRIZADAS: el prefijo de nemo (patrón LIKE) y la `fecha_pip` viajan
        SIEMPRE como parámetros ('?'), nunca interpolados en el SQL.
      - Filtro por instrumento: TES UVR con `nemo LIKE 'TUVT%'`; TF con `nemo LIKE
        'TFIT%'` (TES Tasa Fija de deuda pública).
      - Filtro por fecha: si `fecha_pip` no es None, filtra `fecha_reporte = ?`
        (parametrizado). Si es None, se usa la fecha máxima disponible en `hist_pip`
        (subconsulta sin entrada externa) para leer un corte coherente.

    Args:
        ruta_db: ruta a la base de datos SQLite. Nota: `get_conn()` abre la base
            principal del proyecto (`comun.rutas.DB_PATH`); el parámetro se conserva por
            consistencia con las demás funciones del módulo (diseño/pruebas).
        fecha_pip: fecha de corte 'YYYY-MM-DD' o None. Cuando es None se usa el corte más
            reciente disponible en `hist_pip`.

    Returns:
        UniversoCompra con los DataFrames `tes_uvr` y `tf` (posiblemente vacíos).
    """
    import pandas as pd

    conn = get_conn()
    try:
        # ¿Existe la tabla hist_pip? Si no, universo vacío (no abortar).
        existe = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hist_pip'"
        ).fetchone()
        if not existe:
            logger.warning(
                "hist_pip no existe; se devuelve un universo de compra vacío."
            )
            return _universo_vacio()

        # Columnas de la allowlist (literales controlados por el código).
        columnas_sql = ", ".join(_COLUMNAS_UNIVERSO_COMPRA)

        # Cláusula de fecha PARAMETRIZADA. Con fecha_pip -> igualdad parametrizada; sin
        # ella -> corte más reciente vía subconsulta MAX (sin entrada externa).
        if fecha_pip is not None:
            clausula_fecha = "fecha_reporte = ?"
            params_fecha: tuple[object, ...] = (fecha_pip,)
        else:
            clausula_fecha = (
                "fecha_reporte = (SELECT MAX(fecha_reporte) FROM hist_pip)"
            )
            params_fecha = ()

        def _leer(prefijo_nemo: str) -> "pd.DataFrame":
            # Query de solo lectura: columnas de allowlist fija; el prefijo de nemo y la
            # fecha viajan como parámetros ('?'), nunca interpolados. El orden de params
            # respeta el orden de los '?' en el SQL (primero el prefijo del LIKE, luego
            # la fecha si aplica).
            consulta = (
                f"SELECT {columnas_sql} FROM hist_pip "
                f"WHERE nemo LIKE ? AND {clausula_fecha}"
            )
            params = (prefijo_nemo, *params_fecha)
            return pd.read_sql_query(consulta, conn, params=params)

        tes_uvr = _leer(_PREFIJO_NEMO_TES_UVR)
        tf = _leer(_PREFIJO_NEMO_TF)
    finally:
        conn.close()

    # Si el corte no arroja ninguna fila para ambos instrumentos, se devuelve un
    # universo vacío homogéneo (mismas columnas), coherente con "hist_pip vacía".
    if tes_uvr.empty and tf.empty:
        logger.warning(
            "hist_pip sin filas TUVT/TFIT para el corte solicitado; universo vacío."
        )
        return _universo_vacio()

    return UniversoCompra(tes_uvr=tes_uvr, tf=tf)


# ==============================================================================
# DERIVACIÓN DE LA VARIACIÓN DE RESERVA DESDE EL EXCEL (dos hojas, Req 12)
# ==============================================================================

import pandas as pd

# Nombres esperados de las dos hojas del workbook de reservas (entrada NO confiable:
# se valida que existan exactamente estos nombres antes de procesar).
_HOJA_SALDO = "Saldo Reservas"
_HOJA_INTERES = "Interés a la Reserva"

# Etiquetas de mes en orden (Enero=1 .. Diciembre=12). El 'mes' resultante se
# representa como el offset ENTERO de la columna del mes en la hoja (Enero=1..
# Diciembre=12), consistente con persistir_input_reservas. La variación es mes contra
# mes, por lo que solo hay variación para los meses con un mes anterior (offsets 2..12).
_MESES_ORDEN = (
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
)

# Cabeceras de bloque de la hoja de saldo (marcan el inicio de cada compañía).
_HEADER_BOLIVAR = "BOLIVAR"
_HEADER_COMERCIALES = "COMERCIALES"
_HEADER_CAPITALIZADORA = "CAPITALIZADORA"

# Filas TOTAL de la hoja de saldo. Todas se ignoran EXCEPTO TOTAL CAPI, que provee el
# saldo del agregado Capitalizadora (Req 12.6).
_TOTAL_BOLIVAR = "TOTAL BOLIVAR"
_TOTAL_COMERCIALES = "TOTAL COMERCIALES"
_TOTAL_CAPI = "TOTAL CAPI"
_TOTAL_COMPANIAS = "TOTAL COMPAÑIAS"

# Etiqueta de la línea de interés del agregado Capitalizadora (hoja de interés).
_INTERES_CAPITALIZADORA = "CAPITALIZADORA"
# Sub-cabecera de sección de la hoja de interés (no es un ramo con datos propios).
_INTERES_SUBHEADER_BOLIVAR = "BOLIVAR"

# Nombres agregados de salida (Req 12.8, 12.6).
RAMO_DEMAS_BOLIVAR = "DEMÁS RAMOS BOLÍVAR"
RAMO_DEMAS_COMERCIALES = "DEMÁS RAMOS COMERCIALES"
RAMO_CAPITALIZADORA = "CAPITALIZADORA"

# Etiquetas que nunca son ramos operables (cabeceras/totales), para descartarlas al
# recorrer la hoja de saldo.
_ETIQUETAS_NO_RAMO_SALDO = frozenset(
    {
        _HEADER_BOLIVAR,
        _HEADER_COMERCIALES,
        _HEADER_CAPITALIZADORA,
        _TOTAL_BOLIVAR,
        _TOTAL_COMERCIALES,
        _TOTAL_CAPI,
        _TOTAL_COMPANIAS,
    }
)


# ==============================================================================
# DICCIONARIOS DE MAPEO: etiqueta del Excel de reservas → ramo de portafolio
# ==============================================================================
#
# El destino de cada ramo (columna `formato_portafolio`) DEBE coincidir exactamente con
# la columna `ramo` de `portafolio_clasificado`, que viene en MAYÚSCULAS y SIN TILDES.
# Las claves (`formato_saldos` / `formato_interes`) son las etiquetas tal como aparecen
# en cada hoja del Excel; se normalizan con `_normalizar_ramo` (strip/upper) para el
# match. Un destino vacío ("") significa IGNORAR ese ramo (no genera compra/venta).
#
# La agrupación "demás ramos" se resuelve por este diccionario (no por presencia en el
# portafolio): varios saldos que apuntan al mismo destino se SUMAN en ese ramo.

# --- Saldos: formato_saldos → ramo_portafolio (Bolívar + Comerciales) ---
# Vacío ("") = ignorar (p. ej. RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY).
_SEED_MAPEO_SALDO: tuple[tuple[str, str], ...] = (
    # Bolívar
    ("RIESGOS PROFESIONALES-MATEMATICA", "RIESGOS PROFESIONALES-MATEMATICA"),
    ("RIESGOS PROFESIONALES-SINIESTROS", "RIESGOS PROFESIONALES-SINIESTROS"),
    ("PENSIONES LEY 100", "RENTAS VITALICIAS"),
    ("PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA", "PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA"),
    ("PENSIONES CONMUTACION PENSIONAL", "PENSIONES CONMUTACION PENSIONAL"),
    ("RENTAS VOLUNTARIAS", "RENTAS VOLUNTARIAS"),
    ("ACCIDENTES PERSONALES", "ACCIDENTES PERSONALES"),
    ("VIDA GRUPO-TECNICA", "VIDA GRUPO"),
    ("VIDA GRUPO-SINIESTROS", "DEMAS RAMOS DE SEGUROS DE VIDA"),
    ("PREVISIONAL - NEGOCIO PERÚ", "PREVISIONAL - PERU"),
    ('VIP - "MATEMÁTICA"', "VIP"),
    ('FAP - "MATEMÁTICA"', "FAP"),
    ("VIDA INDIVIDUAL - MATEMÁTICA", "VIDA INDIVIDUAL-MATEMATICA"),
    ("FONDO LIFE LIBERTY", "FONDO LIFE USD"),
    ("FONDO EN PESOS 52 LIBERTY", "FONDO VIDA PESOS"),
    ("FONDO EN PESOS 53 LIBERTY", "FONDO LATIN VIDA"),
    ("VIDA INDIVIDUAL - SINIESTROS", "VIDA INDIVIDUAL-SINIESTROS"),
    ("RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY", ""),  # se ignora
    ("SALUD", "SALUD"),
    # Comerciales
    ("SOAT", "SOAT"),
    ("AUTOMOVILES", "AUTOMOVILES"),
    ("TERREMOTO", "TERREMOTO"),
    ("INCENDIO", "INCENDIO"),
    ("CUMPLIMIENTO", "CUMPLIMIENTO"),
    ("RESPONSABILIDAD CIVIL", "RESPONSABILIDAD CIVIL"),
    ("TODO RIESGO CONTRATISTA", "TODO RIESGO CONTRATISTA"),
    ("CORRIENTE DEBIL", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("LUCROCESANTE", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("MANEJO", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("ROTURA DE MAQUINARIA", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("NAVEGACION", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("SUSTRACCIÓN", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("TRANSPORTE", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("ROTURA DE VIDRIOS", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("DESEMPLEO", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("HOGAR", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("AGRICOLA", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("MANEJO GLOBAL BANCARIO", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("ARRENDAMIENTO", "DEMAS RAMOS DE SEGUROS GENERALES"),
    ("DECENAL", "DEMAS RAMOS DE SEGUROS GENERALES"),
)

# --- Interés: formato_interes → ramo_portafolio (solo Bolívar + Capitalizadora) ---
_SEED_MAPEO_INTERES: tuple[tuple[str, str], ...] = (
    ("Capitalizadora", "CAPITALIZADORA"),
    ("Rentas Vitalicias", "RENTAS VITALICIAS"),
    ("Invalidez y Sobrevivencia (Previsional)", "PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA"),
    ("ARL siniestros", "RIESGOS PROFESIONALES-SINIESTROS"),
    ("ARL Matemática", "RIESGOS PROFESIONALES-MATEMATICA"),
    ("Rentas Perú", "PREVISIONAL - PERU"),
    ("Vida Individual", "VIDA INDIVIDUAL-MATEMATICA"),
    ("Vida Protección", "VIDA INDIVIDUAL - SINIESTROS"),
)

# Etiqueta de la línea de interés de Capitalizadora, para separarla del bloque Bolívar.
_MAPEO_INTERES_CLAVE_CAPI = _normalizar_ramo("Capitalizadora")


def asegurar_tablas_mapeo_reservas(conn: sqlite3.Connection) -> None:
    """
    Crea de forma IDEMPOTENTE las tablas de mapeo `mapeo_ramos_saldo_reservas` y
    `mapeo_ramos_interes_reservas` y las SIEMBRA con los diccionarios semilla si están
    vacías (Req 12.11).

    - `formato_origen`: etiqueta NORMALIZADA (strip/upper) tal como aparece en la hoja.
    - `ramo_portafolio`: destino, exactamente como en `portafolio_clasificado.ramo`
      (mayúsculas, sin tildes). Cadena vacía = IGNORAR ese ramo.

    El sembrado solo ocurre si la tabla está vacía (`SELECT COUNT(*) == 0`), de modo que
    ediciones posteriores del usuario en SQL se respetan (no se sobrescriben). El DDL usa
    `CREATE TABLE IF NOT EXISTS`; nunca elimina ni pisa datos existentes.

    Args:
        conn: conexión sqlite3 abierta. El llamador gestiona su ciclo de vida.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mapeo_ramos_saldo_reservas (
            formato_origen TEXT PRIMARY KEY,
            ramo_portafolio TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mapeo_ramos_interes_reservas (
            formato_origen TEXT PRIMARY KEY,
            ramo_portafolio TEXT NOT NULL
        )
        """
    )
    # Sembrar solo si están vacías (respeta ediciones posteriores del usuario).
    n_saldo = conn.execute(
        "SELECT COUNT(*) FROM mapeo_ramos_saldo_reservas"
    ).fetchone()[0]
    if n_saldo == 0:
        conn.executemany(
            "INSERT INTO mapeo_ramos_saldo_reservas (formato_origen, ramo_portafolio) "
            "VALUES (?, ?)",
            [(_normalizar_ramo(o), d) for o, d in _SEED_MAPEO_SALDO],
        )
    n_interes = conn.execute(
        "SELECT COUNT(*) FROM mapeo_ramos_interes_reservas"
    ).fetchone()[0]
    if n_interes == 0:
        conn.executemany(
            "INSERT INTO mapeo_ramos_interes_reservas (formato_origen, ramo_portafolio) "
            "VALUES (?, ?)",
            [(_normalizar_ramo(o), d) for o, d in _SEED_MAPEO_INTERES],
        )
    conn.commit()


def cargar_mapeos_reservas(ruta_db: str) -> tuple[dict[str, str], dict[str, str]]:
    """
    Asegura/siembra las tablas de mapeo y devuelve dos dicts
    `{formato_origen_normalizado: ramo_portafolio}` para saldo e interés.

    El destino se toma tal cual está en la tabla (ya coincide con
    `portafolio_clasificado.ramo`). Un destino vacío se conserva como "" (el consumidor
    lo interpreta como "ignorar"). Lectura de solo lectura y parametrizada.

    Args:
        ruta_db: ruta a la DB (el acceso real es vía `comun.db.get_conn`, misma
            convención que el resto del módulo).

    Returns:
        (mapeo_saldo, mapeo_interes)
    """
    conn = get_conn()
    try:
        asegurar_tablas_mapeo_reservas(conn)
        filas_saldo = conn.execute(
            "SELECT formato_origen, ramo_portafolio FROM mapeo_ramos_saldo_reservas"
        ).fetchall()
        filas_interes = conn.execute(
            "SELECT formato_origen, ramo_portafolio FROM mapeo_ramos_interes_reservas"
        ).fetchall()
    finally:
        conn.close()

    mapeo_saldo = {
        _normalizar_ramo(o): ("" if d is None else str(d)) for o, d in filas_saldo
    }
    mapeo_interes = {
        _normalizar_ramo(o): ("" if d is None else str(d)) for o, d in filas_interes
    }
    return mapeo_saldo, mapeo_interes


# ==============================================================================
# MODELO: MapeoDeReservas (representación normalizada de la Fuente_Unica_De_Verdad)
# ==============================================================================

@dataclass(frozen=True)
class MapeoDeReservas:
    """
    Mapeo Etiqueta_De_Reserva(normalizada) → Ramo_De_Portafolio, leído de la
    Fuente_Unica_De_Verdad (tablas `mapeo_ramos_saldo_reservas` /
    `mapeo_ramos_interes_reservas`). Encapsula el tratamiento ÚNICO del destino vacío.

    - `entradas`: {clave_normalizada: destino}. Un destino "" significa IGNORAR la
      etiqueta (no genera saldo/variación para ese ramo).

    Este objeto es la interfaz tipada que consumen el Flujo_Encaje y el Flujo_Motor,
    en lugar de reimplementar la interpretación de "" en cada consumidor.
    """

    entradas: dict[str, str]

    def destino(self, etiqueta: str) -> str:
        """
        Destino (Ramo_De_Portafolio) para una `etiqueta` (se normaliza internamente
        con `_normalizar_ramo`).

        Retrocompatibilidad: si la etiqueta no tiene clave en el mapeo, devuelve la
        etiqueta normalizada tal cual, replicando el comportamiento previo
        `HOMOL.get(x, x)`. Si el destino mapeado es "", devuelve "" (etiqueta ignorada);
        usar `ignora(...)` para distinguir "ignorar" de "ausente".
        """
        clave = _normalizar_ramo(etiqueta)
        return self.entradas.get(clave, clave)

    def ignora(self, etiqueta: str) -> bool:
        """True si la etiqueta está mapeada EXPLÍCITAMENTE a destino vacío ("")."""
        return self.entradas.get(_normalizar_ramo(etiqueta), None) == ""


# ==============================================================================
# LECTOR_MAPEO: lectura centralizada, normalizada y validada de la Fuente_Unica
# ==============================================================================

# Allowlist FIJA de columnas leídas de las tablas de mapeo. Son literales controlados
# por el código; NUNCA se interpola entrada externa en el SQL. El orden coincide con el
# desempaquetado (formato_origen, ramo_portafolio) en `_leer_mapeo_tabla`.
_COLUMNAS_MAPEO_RESERVAS: tuple[str, str] = ("formato_origen", "ramo_portafolio")

# Allowlist FIJA de tablas de mapeo válidas. Los nombres de tabla NO pueden ir como
# parámetro '?' en SQLite, por lo que se restringen a esta allowlist literal para que
# jamás provenga de entrada externa.
_TABLA_MAPEO_SALDO = "mapeo_ramos_saldo_reservas"
_TABLA_MAPEO_INTERES = "mapeo_ramos_interes_reservas"
_TABLAS_MAPEO_VALIDAS = frozenset({_TABLA_MAPEO_SALDO, _TABLA_MAPEO_INTERES})


def _leer_mapeo_tabla(conn: sqlite3.Connection, tabla: str) -> MapeoDeReservas:
    """
    Lee una tabla de mapeo y devuelve un `MapeoDeReservas` normalizado y validado.

    Trata cada fila como entrada NO confiable:
      - Normaliza `formato_origen` con `_normalizar_ramo` (strip/upper).
      - Descarta filas cuya clave normalizada quede vacía, con `logger.warning`
        GENÉRICO (sin volcar el contenido de la fila) y sin abortar.
      - Ante claves normalizadas duplicadas, conserva una única entrada DETERMINISTA:
        gana la última fila en orden de lectura (documentado).
      - Un `ramo_portafolio` NULL se interpreta como "" (ignorar), consistente con
        `cargar_mapeos_reservas`.

    Seguridad: el nombre de tabla se valida contra la allowlist fija
    `_TABLAS_MAPEO_VALIDAS` (no puede parametrizarse en SQLite) y las columnas provienen
    de la allowlist literal `_COLUMNAS_MAPEO_RESERVAS`. La consulta es de solo lectura.
    """
    if tabla not in _TABLAS_MAPEO_VALIDAS:
        # Defensa en profundidad: el nombre de tabla es literal del código, nunca
        # entrada externa. Si llegara uno inesperado, se falla de forma explícita.
        raise ValueError("Tabla de mapeo no permitida.")

    columnas_sql = ", ".join(_COLUMNAS_MAPEO_RESERVAS)
    # Nombre de tabla desde allowlist (no parametrizable); columnas desde allowlist.
    filas = conn.execute(f"SELECT {columnas_sql} FROM {tabla}").fetchall()

    entradas: dict[str, str] = {}
    for formato_origen, ramo_portafolio in filas:
        clave = _normalizar_ramo(formato_origen) if formato_origen is not None else ""
        if not clave:
            logger.warning(
                "mapeo_reservas: se descarta una fila con formato_origen vacío."
            )
            continue
        destino = "" if ramo_portafolio is None else str(ramo_portafolio)
        # Última fila gana: determinista para una misma colección de entrada.
        entradas[clave] = destino

    return MapeoDeReservas(entradas=entradas)


def cargar_mapeo_saldo(ruta_db: str) -> MapeoDeReservas:
    """
    Asegura el esquema (idempotente + siembra si vacío) y devuelve el
    `MapeoDeReservas` de SALDOS leído de la Fuente_Unica_De_Verdad (Req 2.1).

    No depende de `init_db.py`: el esquema se crea desde la capa de aplicación vía
    `asegurar_tablas_mapeo_reservas`. Usa una query parametrizada de solo lectura sobre
    la allowlist fija de columnas; trata cada fila como entrada NO confiable.

    Args:
        ruta_db: ruta a la DB (el acceso real es vía `comun.db.get_conn`, misma
            convención que el resto del módulo).
    """
    conn = get_conn()
    try:
        asegurar_tablas_mapeo_reservas(conn)
        return _leer_mapeo_tabla(conn, _TABLA_MAPEO_SALDO)
    finally:
        conn.close()


def cargar_mapeo_interes(ruta_db: str) -> MapeoDeReservas:
    """
    Asegura el esquema (idempotente + siembra si vacío) y devuelve el
    `MapeoDeReservas` de INTERÉS leído de la Fuente_Unica_De_Verdad (Req 2.2).

    Mismas garantías de seguridad y normalización que `cargar_mapeo_saldo`.
    """
    conn = get_conn()
    try:
        asegurar_tablas_mapeo_reservas(conn)
        return _leer_mapeo_tabla(conn, _TABLA_MAPEO_INTERES)
    finally:
        conn.close()


class ErrorPersistenciaMapeo(Exception):
    """
    Error de dominio al persistir una entrada de mapeo de reservas en la
    Fuente_Unica_De_Verdad (`mapeo_ramos_saldo_reservas` / `mapeo_ramos_interes_reservas`).

    Se lanza cuando, tras agotar los reintentos, la escritura sigue fallando (p. ej.
    `database is locked`). El callback del dashboard lo captura para mostrar un mensaje
    GENÉRICO al usuario; el detalle queda solo en el log, sin volcar datos sensibles.
    """


def persistir_mapeo_reserva(
    tabla: str,
    formato_origen: str,
    ramo_portafolio: str,
    *,
    reintentos: int = 3,
) -> None:
    """
    Persiste (UPSERT parametrizado) una entrada de mapeo en la Fuente_Unica_De_Verdad,
    desde la edición del usuario en el Dashboard.

    Validaciones (entrada NO confiable):
      - `tabla`: debe pertenecer a la allowlist fija `_TABLAS_MAPEO_VALIDAS`
        (nombre de tabla, no parametrizable en SQLite). Cualquier otro valor => ValueError.
      - `formato_origen`: se NORMALIZA (`strip`/`upper`) con `_normalizar_ramo`; si queda
        vacío se rechaza con `ValueError` (no se crea ni actualiza fila) — Req 10.5.
      - `ramo_portafolio`: se conserva tal cual (str); "" es válido y significa IGNORAR
        la etiqueta.

    Persistencia:
      - INSERT ... ON CONFLICT(formato_origen) DO UPDATE PARAMETRIZADO (placeholders '?');
        jamás se interpola la entrada del usuario en el SQL.
      - Reintenta ante `database is locked` hasta `reintentos` veces; agotados, lanza
        `ErrorPersistenciaMapeo` (mensaje genérico; detalle solo en log).

    Args:
        tabla: `mapeo_ramos_saldo_reservas` o `mapeo_ramos_interes_reservas`.
        formato_origen: etiqueta del Excel a mapear (se normaliza internamente).
        ramo_portafolio: ramo de portafolio destino ("" = ignorar).
        reintentos: intentos máximos ante `database is locked` (default 3).

    Raises:
        ValueError: tabla no permitida o `formato_origen` vacío tras normalizar.
        ErrorPersistenciaMapeo: si la escritura falla tras agotar los reintentos.
    """
    import time

    if tabla not in _TABLAS_MAPEO_VALIDAS:
        raise ValueError("Tabla de mapeo no permitida.")

    clave = _normalizar_ramo(formato_origen)
    if not clave:
        # Req 10.5: se rechaza sin crear ni actualizar fila.
        raise ValueError("El formato_origen del mapeo no puede estar vacío.")

    destino = "" if ramo_portafolio is None else str(ramo_portafolio)
    params: tuple[object, ...] = (clave, destino)

    # Nombre de tabla desde allowlist (no parametrizable); valores como placeholders '?'.
    sentencia = (
        f"INSERT INTO {tabla} (formato_origen, ramo_portafolio) VALUES (?, ?) "
        "ON CONFLICT(formato_origen) DO UPDATE SET "
        "ramo_portafolio = excluded.ramo_portafolio"
    )

    intentos = max(1, int(reintentos))
    for intento in range(1, intentos + 1):
        conn = get_conn()
        try:
            asegurar_tablas_mapeo_reservas(conn)
            conn.execute(sentencia, params)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                logger.warning(
                    "Fallo operacional no recuperable al persistir una entrada de mapeo."
                )
                raise ErrorPersistenciaMapeo(
                    "No se pudo guardar la entrada de mapeo."
                ) from exc
            if intento >= intentos:
                logger.warning(
                    "Persistencia de mapeo agotó reintentos por 'database is locked'."
                )
                raise ErrorPersistenciaMapeo(
                    "No se pudo guardar la entrada de mapeo (base de datos ocupada)."
                ) from exc
            time.sleep(0.1 * intento)  # backoff simple
        finally:
            conn.close()


class ErrorInputReservas(Exception):
    """
    Error de lectura/validación del workbook Excel de reservas (entrada NO confiable).

    Se lanza cuando falta alguna de las hojas esperadas ('Saldo Reservas' /
    'Interés a la Reserva') o cuando el layout no puede interpretarse (no se localizan
    las cabeceras de bloque ni la columna-etiqueta con los 12 meses). El mensaje es
    GENÉRICO: no incluye contenido del archivo para no filtrar datos no confiables.
    """


class ErrorFormatoEncabezado(ErrorInputReservas):
    """
    Error de formato de encabezado del workbook de reservas.

    Se lanza cuando los encabezados de columna no son fechas (por ejemplo, nombres
    de mes como 'ENERO'), cuando no se reconoce ninguna columna con Encabezado_Fecha,
    o cuando el salto en meses entre dos columnas consecutivas es distinto de 1 y de
    12. Hereda de ErrorInputReservas para preservar la retrocompatibilidad de captura,
    a la vez que permite al dashboard discriminar esta causa. El mensaje es GENÉRICO:
    no incluye contenido del archivo para no filtrar datos no confiables.
    """


class ErrorHojasFaltantes(ErrorInputReservas):
    """
    Error por hojas requeridas faltantes en el workbook de reservas.

    Se lanza cuando falta la hoja 'Saldo Reservas' o 'Interés a la Reserva'. Es una
    causa distinta del error de formato de encabezado. Hereda de ErrorInputReservas
    para preservar la retrocompatibilidad de captura, a la vez que permite al
    dashboard discriminar esta causa. El mensaje es GENÉRICO: no incluye contenido
    del archivo para no filtrar datos no confiables.
    """


# Nombres de mes (español) que NO deben interpretarse como Encabezado_Fecha aunque
# `pd.to_datetime` pudiera parsearlos parcialmente. Se comparan normalizados
# (strip/upper). Reutiliza `_MESES_ORDEN` y añade abreviaturas frecuentes.
_NOMBRES_MES_NO_FECHA = frozenset(_MESES_ORDEN) | frozenset(
    {"ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
     "JUL", "AGO", "SEP", "SEPT", "OCT", "NOV", "DIC"}
)


def _es_encabezado_fecha(valor: object) -> bool:
    """
    Determina si una celda de encabezado es un `Encabezado_Fecha` reconocible.

    Regla de detección robusta (Decisión 4 del diseño):
      - Es fecha si el valor ya es un `datetime`/`date`/`pd.Timestamp` nativo.
      - En otro caso, es fecha si `pd.to_datetime(valor, errors="coerce")` NO es `NaT`
        Y el valor NO es puramente numérico NI un nombre de mes.

    El doble guardado evita:
      * Falsos positivos: un offset entero (`1`, `2`, ...) o un float no debe leerse
        como fecha (pandas interpretaría enteros como nanosegundos epoch); un nombre de
        mes suelto ("ENERO") tampoco es un `Encabezado_Fecha` (es el formato viejo que
        el lector debe rechazar).
      * Falsos negativos: fechas serializadas por Excel que llegan como texto
        ("2026-06-01", "01/06/2026") sí deben reconocerse.

    Args:
        valor: contenido crudo de la celda (entrada NO confiable).

    Returns:
        True si la celda representa una fecha reconocible; False en caso contrario.
    """
    import datetime as _dt

    # Valores nulos / vacíos nunca son fecha.
    if valor is None:
        return False

    # Fecha nativa (datetime, date, pd.Timestamp). `bool` es subclase de int, no de
    # date, por lo que no interfiere. `datetime` es subclase de `date`, así que la
    # comprobación de `date` cubre ambos.
    if isinstance(valor, (_dt.date, pd.Timestamp)):
        return True

    # Guard contra falsos positivos: valores puramente numéricos (int/float, o bool)
    # NO son fechas. Un offset entero `1` no debe interpretarse como fecha.
    if isinstance(valor, (bool, int, float)):
        return False

    # A partir de aquí solo tratamos texto. Cualquier otro tipo se coacciona a str.
    texto = str(valor).strip()
    if not texto:
        return False

    # Guard: texto que es un número puro (p. ej. "1", "12", "3.5") no es fecha.
    try:
        float(texto)
        return False
    except ValueError:
        pass

    # Guard: nombres de mes ("ENERO", "ENE", ...) no son un Encabezado_Fecha.
    if texto.upper() in _NOMBRES_MES_NO_FECHA:
        return False

    # Intento de parseo tolerante. errors="coerce" devuelve NaT si no es parseable.
    parseado = pd.to_datetime(texto, errors="coerce", dayfirst=False)
    return not pd.isna(parseado)


def _primer_dia_del_mes(valor: object) -> "date":
    """
    Normaliza una celda de `Encabezado_Fecha` (ya reconocida por `_es_encabezado_fecha`)
    al primer día de su mes como `datetime.date`.

    El `Encabezado_Fecha` puede llegar como fecha nativa (`datetime`/`date`/
    `pd.Timestamp`) o como texto serializado por Excel ("2026-06-01", "01/06/2026").
    Se coerciona con `pd.to_datetime` y se ancla al día 1 del (año, mes) resultante,
    de modo que el resultado depende SOLO del año y mes del encabezado (Req 1.2).

    Args:
        valor: contenido crudo de la celda de encabezado (entrada NO confiable) que ya
            fue clasificado como fecha.

    Returns:
        `date` del primer día del mes/año del encabezado.

    Raises:
        ErrorFormatoEncabezado: si el valor no puede normalizarse a una fecha (no
            debería ocurrir cuando la celda pasó `_es_encabezado_fecha`, pero se trata
            como entrada no confiable para no propagar `NaT`).
    """
    from datetime import date as _date

    marca = pd.to_datetime(valor, errors="coerce", dayfirst=False)
    if pd.isna(marca):
        # Defensa en profundidad: una celda clasificada como fecha que no se puede
        # normalizar es un formato inesperado. Mensaje genérico (Req 13/11).
        logger.warning("Un encabezado de fecha no pudo normalizarse a (año, mes).")
        raise ErrorFormatoEncabezado(
            "El input de reservas tiene un encabezado de fecha no interpretable."
        )
    return _date(int(marca.year), int(marca.month), 1)


def _localizar_columnas_fecha(
    df: pd.DataFrame,
) -> "tuple[int, int, list[tuple[int, date]]]":
    """
    Localiza la columna-etiqueta y las columnas de datos por `Encabezado_Fecha` en una
    hoja leída con `header=None`.

    Estrategia (según el diseño, "Components and Interfaces" de `Lector_Reservas`):
      - Recorre las filas superiores buscando la PRIMERA que contenga al menos 2 celdas
        `Encabezado_Fecha` (`_es_encabezado_fecha`). Esa es la fila de encabezados.
      - La `col_etiqueta` es la primera columna de TEXTO (no fecha, no vacía) a la
        izquierda de la primera columna de fecha de esa fila. Si no hay texto a la
        izquierda, se usa la columna inmediatamente anterior a la primera fecha.
      - Cada columna de fecha se normaliza al primer día de su mes (`date`) y las
        columnas se devuelven ORDENADAS cronológicamente ascendente por ese mes.

    Rechazos (Req 2.2, 2.3):
      - Si la fila de encabezados contiene NOMBRES DE MES ('ENERO', ...) en lugar de
        fechas -> `ErrorFormatoEncabezado`.
      - Si NINGUNA columna tiene un `Encabezado_Fecha` reconocible -> `ErrorFormatoEncabezado`.

    Args:
        df: hoja de reservas leída con `header=None` (entrada NO confiable).

    Returns:
        `(fila_encabezados, col_etiqueta, columnas)` donde `columnas` es una lista de
        `(indice_columna, primer_dia_del_mes)` ordenada cronológicamente ascendente.

    Raises:
        ErrorFormatoEncabezado: encabezados con nombre de mes o sin ninguna fecha.
    """
    n_filas, n_cols = df.shape

    # ¿Hay algún nombre de mes en las celdas? Sirve para dar el error específico
    # "encabezados son nombres de mes" cuando no aparece ninguna fecha.
    hay_nombre_mes = False

    for i in range(n_filas):
        indices_fecha: list[int] = []
        for j in range(n_cols):
            celda = df.iat[i, j]
            if _es_encabezado_fecha(celda):
                indices_fecha.append(j)
            elif not hay_nombre_mes and _normalizar_ramo(celda) in _NOMBRES_MES_NO_FECHA:
                hay_nombre_mes = True

        # La fila de encabezados es la primera con >= 2 celdas de fecha.
        if len(indices_fecha) < 2:
            continue

        primer_col_fecha = indices_fecha[0]

        # La columna-etiqueta es la primera columna de TEXTO a la izquierda de la
        # primera columna de fecha. Se recorre de derecha a izquierda para tomar la
        # más cercana con texto no vacío que no sea a su vez una fecha.
        col_etiqueta = primer_col_fecha - 1 if primer_col_fecha >= 1 else primer_col_fecha
        for j in range(primer_col_fecha - 1, -1, -1):
            celda = df.iat[i, j]
            if _es_encabezado_fecha(celda):
                continue
            if _normalizar_ramo(celda):  # texto no vacío
                col_etiqueta = j
                break

        columnas = [(j, _primer_dia_del_mes(df.iat[i, j])) for j in indices_fecha]
        # Orden cronológico ascendente por el primer día del mes (Req 1.3). Se desempata
        # por índice de columna para un orden estable y determinista.
        columnas.sort(key=lambda par: (par[1], par[0]))
        return i, col_etiqueta, columnas

    # No se encontró una fila de encabezados con >= 2 fechas. Discriminar la causa:
    # nombres de mes (formato viejo) vs. ausencia total de encabezados de fecha.
    if hay_nombre_mes:
        logger.warning(
            "Los encabezados del input de reservas son nombres de mes, no fechas."
        )
    else:
        logger.warning(
            "No se reconoció ninguna columna con encabezado de fecha en la hoja."
        )
    raise ErrorFormatoEncabezado(
        "El input de reservas debe traer encabezados de FECHA (no nombres de mes ni "
        "otros formatos)."
    )


def _clasificar_saltos(
    columnas: "list[tuple[int, date]]",
) -> "list[tuple[int, int, str, date]]":
    """
    Clasifica los saltos entre columnas consecutivas ya ordenadas cronológicamente por
    su diferencia de meses (Req 3.1, 3.2, 3.4).

    Para cada par de columnas consecutivas `(col_desde, col_hasta)` calcula la diferencia
    en meses entre sus `Mes_Absoluto` y produce un salto tipado:
      - Δmeses == 1  -> `'mensual'` (Salto_Mensual, variación mensual, Req 3.1).
      - Δmeses == 12 -> `'anual'`   (Salto_Anual, diciembre-a-diciembre, Req 3.2).
      - cualquier otro valor -> `ErrorFormatoEncabezado` (Req 3.4).

    Al aplicarse a una secuencia con los meses del año en curso seguida de solo los
    diciembres de años siguientes, produce saltos mensuales para el tramo del año en
    curso y saltos anuales para los diciembre-a-diciembre (Req 3.3).

    Args:
        columnas: lista de `(indice_columna, primer_dia_del_mes)` ORDENADA
            cronológicamente ascendente (tal como la devuelve
            `_localizar_columnas_fecha`). Entrada NO confiable.

    Returns:
        Lista de `(col_desde, col_hasta, tipo, mes_absoluto_hasta)` — un elemento por
        cada par consecutivo — con `tipo` en `{'mensual', 'anual'}` y
        `mes_absoluto_hasta` igual al `Mes_Absoluto` (primer día del mes) de la columna
        destino del salto. Para menos de dos columnas la lista es vacía (no hay saltos).

    Raises:
        ErrorFormatoEncabezado: si algún par consecutivo difiere en un número de meses
            distinto de 1 y de 12.
    """
    saltos: "list[tuple[int, int, str, date]]" = []

    for (col_desde, mes_desde), (col_hasta, mes_hasta) in zip(columnas, columnas[1:]):
        delta_meses = (mes_hasta.year - mes_desde.year) * 12 + (
            mes_hasta.month - mes_desde.month
        )

        if delta_meses == 1:
            tipo = "mensual"
        elif delta_meses == 12:
            tipo = "anual"
        else:
            # Salto no soportado. Mensaje genérico sin volcar valores (Req 13/11).
            logger.warning(
                "Salto de meses no soportado entre dos columnas consecutivas del "
                "input de reservas."
            )
            raise ErrorFormatoEncabezado(
                "El input de reservas tiene columnas de fecha con un salto de meses no "
                "soportado (solo se admiten saltos de 1 mes o de 12 meses)."
            )

        saltos.append((col_desde, col_hasta, tipo, mes_hasta))

    return saltos


@dataclass(frozen=True)
class Segmento:
    """
    Segmento de columnas del `Layout_Por_Tramo` detectado a partir de la `Fecha_Corte`
    (Req 14.3, 15.2, 15.3). Es una estructura de datos pura, producida por
    `detectar_tramos_por_corte`, que describe cómo se agrupa un intervalo de meses para
    su posterior prorrateo (uniforme para el saldo, lineal direccional para el interés).

    Cada `Segmento` es uno de tres tipos:
      - `'parcial'`: el Tramo_Parcial, los meses POSTERIORES al Mes_Corte hasta la primera
        columna de diciembre destino inclusive (N meses, N >= 2).
      - `'anual'`: un Tramo_Anual diciembre-a-diciembre (12 meses).
      - `'mensual'`: un salto mensual simple fuera del Tramo_Parcial (1 mes).

    Attributes:
        tipo: tipo del segmento; uno de `{'parcial', 'anual', 'mensual'}`.
        mes_base: `Mes_Absoluto` (primer día del mes) del NIVEL BASE del segmento, contra
            el que se calcula la variación del periodo. Para el Tramo_Parcial es el
            Mes_Corte; NO se proyecta ni se emite como fila (Req 15.2).
        meses: lista de `Mes_Absoluto` (primer día de cada mes) destino cubiertos por el
            segmento, en orden cronológico ASCENDENTE. Contiene N elementos para
            `'parcial'`, 12 para `'anual'` y 1 para `'mensual'`.
    """

    tipo: str
    mes_base: "date"
    meses: "list[date]"


def _delta_meses(desde: "date", hasta: "date") -> int:
    """
    Diferencia en meses calendario entre dos primeros-días-de-mes (`hasta - desde`).

    Reutiliza la misma aritmética que `_clasificar_saltos` para mantener la
    clasificación de saltos consistente en ambos caminos (Layout_Mensual y
    Layout_Por_Tramo).
    """
    return (hasta.year - desde.year) * 12 + (hasta.month - desde.month)


def detectar_tramos_por_corte(
    columnas: "list[tuple[int, date]]",
    mes_corte: "date",
) -> "list[Segmento]":
    """
    Segmenta las columnas ordenadas del `Layout_Por_Tramo` en tramos según la
    `Fecha_Corte` (Req 14.3, 15.2, 15.3, 15.5, 15.6, 15.7, 15.8).

    El `Mes_Corte` es el NIVEL BASE del `Tramo_Parcial`: NO se proyecta ni se emite como
    fila (Req 15.2). Si el `Mes_Corte` no está entre las columnas, se usa como base
    sintética el nivel de la última columna `<= Mes_Corte` (Req 15.2).

    Reglas de segmentación:
      - `Tramo_Parcial` (Req 15.3): meses estrictamente POSTERIORES al `Mes_Corte` hasta
        la PRIMERA columna de diciembre destino inclusive (N meses). Solo se crea cuando
        esa primera columna destino es un diciembre y `N != 1` (si `N == 1` es un simple
        `Salto_Mensual` del `Layout_Mensual`). El salto `Mes_Corte`→diciembre dentro del
        tramo parcial es válido por definición aunque su Δ ∉ {1, 12}.
      - `Tramo_Anual` (Req 15.5): cada intervalo diciembre-a-diciembre POSTERIOR al
        `Tramo_Parcial` de 12 meses.
      - `'mensual'` (Req 15.6): saltos de 1 mes fuera del `Tramo_Parcial` siguen siendo
        mensuales. Las columnas mensuales estrictamente ANTERIORES al `Mes_Corte`
        (Δ=1) se leen como columnas mensuales válidas SIN error (Req 15.8); sus offsets
        caerán < 1 y la convención `Mes_Caído` los omite aguas abajo.
      - Cualquier salto FUERA del `Tramo_Parcial` cuya diferencia de meses ∉ {1, 12} ⇒
        `ErrorFormatoEncabezado` (Req 15.7).

    Args:
        columnas: lista `(indice_columna, primer_dia_del_mes)` ORDENADA cronológicamente
            ascendente, tal como la produce `_localizar_columnas_fecha`. Entrada NO
            confiable.
        mes_corte: primer día del `Mes_Corte` (mes de la `Fecha_Corte`).

    Returns:
        Lista de `Segmento(tipo, mes_base, meses)` en orden cronológico ascendente, con
        `tipo ∈ {'parcial', 'anual', 'mensual'}`. El `Mes_Corte` nunca aparece como mes
        destino de ningún segmento (es solo base).

    Raises:
        ErrorFormatoEncabezado: si un salto fuera del `Tramo_Parcial` difiere en un
            número de meses distinto de 1 y de 12.
    """
    from datetime import date as _date  # noqa: F401  (usado en anotaciones en runtime)

    # Solo los meses (primer día de cada mes), ya ordenados ascendentes.
    meses_cols = [mes for _indice, mes in columnas]

    # Menos de una columna no produce ningún tramo.
    if not meses_cols:
        return []

    segmentos: "list[Segmento]" = []

    # --- 1) Columnas hasta el Mes_Corte inclusive: se leen como mensuales (Req 15.8) ---
    # Cada salto entre columnas <= Mes_Corte debe ser mensual (Δ=1); cualquier otro
    # salto en esta región queda fuera del Tramo_Parcial y por tanto se valida contra
    # {1, 12} (Req 15.7). El Mes_Corte NO se emite como fila; solo sirve de base.
    indice_ultima_pre = -1  # índice de la última columna <= Mes_Corte
    for pos, mes in enumerate(meses_cols):
        if mes <= mes_corte:
            indice_ultima_pre = pos
        else:
            break

    # Emitir como 'mensual' los saltos entre columnas estrictamente anteriores/iguales
    # al Mes_Corte. El destino de cada salto es una columna <= Mes_Corte; el Mes_Corte
    # mismo no se emite (es base), por lo que se excluye como destino.
    for pos in range(1, indice_ultima_pre + 1):
        mes_desde = meses_cols[pos - 1]
        mes_hasta = meses_cols[pos]
        if mes_hasta == mes_corte:
            # El salto que llega al Mes_Corte no emite fila destino (base). Se valida
            # igualmente que sea mensual para no admitir saltos raros pre-corte.
            delta = _delta_meses(mes_desde, mes_hasta)
            if delta not in (1, 12):
                _lanzar_error_salto_fuera_de_parcial()
            continue
        delta = _delta_meses(mes_desde, mes_hasta)
        if delta == 1:
            segmentos.append(Segmento("mensual", mes_desde, [mes_hasta]))
        elif delta == 12:
            segmentos.append(Segmento("anual", mes_desde, _rango_meses(mes_desde, mes_hasta)))
        else:
            _lanzar_error_salto_fuera_de_parcial()

    # --- 2) Determinar la base del Tramo_Parcial (nivel base = Mes_Corte) -------------
    # Si el Mes_Corte no está entre las columnas, la base es sintética: la última
    # columna <= Mes_Corte (Req 15.2). En ambos casos, `mes_base_parcial` es el
    # Mes_Corte como nivel base declarado del tramo.
    mes_base_parcial = mes_corte

    # Primera columna estrictamente posterior al Mes_Corte.
    indice_post = indice_ultima_pre + 1
    if indice_post >= len(meses_cols):
        # No hay columnas posteriores al Mes_Corte: no hay Tramo_Parcial ni tramos
        # anuales. Solo quedan (posibles) columnas mensuales pre-corte ya emitidas.
        return segmentos

    primer_destino = meses_cols[indice_post]
    n_parcial = _delta_meses(mes_corte, primer_destino)

    # --- 3) ¿Hay Tramo_Parcial? Solo si la primera columna destino es diciembre y N!=1
    hay_tramo_parcial = primer_destino.month == 12 and n_parcial != 1

    if hay_tramo_parcial:
        # Tramo_Parcial: meses estrictamente posteriores al Mes_Corte hasta el diciembre
        # destino inclusive (N meses). El salto Mes_Corte→dic es válido por definición.
        meses_parcial = _rango_meses(mes_corte, primer_destino)
        segmentos.append(Segmento("parcial", mes_base_parcial, meses_parcial))
        indice_cursor = indice_post
    else:
        # No hay Tramo_Parcial: la primera columna posterior al Mes_Corte inicia el
        # camino "mensual/anual" ordinario. El salto Mes_Corte→primer_destino se valida
        # contra {1, 12} (Req 15.7); el Mes_Corte no emite fila (base).
        if n_parcial == 1:
            segmentos.append(Segmento("mensual", mes_corte, [primer_destino]))
        elif n_parcial == 12:
            segmentos.append(Segmento("anual", mes_corte, _rango_meses(mes_corte, primer_destino)))
        else:
            _lanzar_error_salto_fuera_de_parcial()
        indice_cursor = indice_post

    # --- 4) Tramos posteriores: dic-a-dic (anual, 12) o mensual (1); otro ⇒ error -----
    for pos in range(indice_cursor + 1, len(meses_cols)):
        mes_desde = meses_cols[pos - 1]
        mes_hasta = meses_cols[pos]
        delta = _delta_meses(mes_desde, mes_hasta)
        if delta == 1:
            segmentos.append(Segmento("mensual", mes_desde, [mes_hasta]))
        elif delta == 12:
            segmentos.append(Segmento("anual", mes_desde, _rango_meses(mes_desde, mes_hasta)))
        else:
            _lanzar_error_salto_fuera_de_parcial()

    return segmentos


def _rango_meses(mes_base: "date", mes_hasta: "date") -> "list[date]":
    """
    Devuelve la lista de primeros-días-de-mes ESTRICTAMENTE posteriores a `mes_base`
    hasta `mes_hasta` inclusive, en orden ascendente. `mes_base` NO se incluye (es el
    nivel base del segmento). Usado para materializar los meses destino de un
    `Tramo_Parcial` (N meses) o `Tramo_Anual` (12 meses).
    """
    from datetime import date as _date

    meses: "list[date]" = []
    anio, mes = mes_base.year, mes_base.month
    total = _delta_meses(mes_base, mes_hasta)
    for _ in range(total):
        mes += 1
        if mes > 12:
            mes = 1
            anio += 1
        meses.append(_date(anio, mes, 1))
    return meses


def _lanzar_error_salto_fuera_de_parcial() -> None:
    """
    Lanza `ErrorFormatoEncabezado` con mensaje GENÉRICO (sin volcar valores del archivo,
    Req 13/15.7) cuando un salto fuera del `Tramo_Parcial` difiere en un número de meses
    distinto de 1 y de 12.
    """
    logger.warning(
        "Salto de meses no soportado fuera del tramo parcial del input de reservas."
    )
    raise ErrorFormatoEncabezado(
        "El input de reservas tiene columnas de fecha con un salto de meses no "
        "soportado fuera del tramo parcial (solo se admiten saltos de 1 o 12 meses)."
    )


def _coercer_float_finito(valor: object) -> float:
    """
    Coerciona un valor (entrada NO confiable) a `float` finito. Cualquier valor no
    numérico, vacío o no finito (NaN/inf) se mapea a `0.0`. No registra el contenido
    para no filtrar entrada no confiable en logs (Req 20.1).
    """
    import math

    try:
        numero = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numero):
        return 0.0
    return numero


def prorratear_interes_lineal_direccional(
    interes_acumulado: float,
    n_meses: int,
    delta_saldo_periodo: float,
    tolerancia: float = 1e-6,
) -> "list[float]":
    """
    Prorrateo_Lineal_Direccional del interés (Req 17). Función NUEVA, distinta del
    Prorrateo_Uniforme (`prorratear_reservas_anual`). Devuelve N deltas mensuales de
    interés cuyo ORDEN depende del signo de `delta_saldo_periodo`:

      - `delta_saldo_periodo >= 0` → CRECIENTE: peso del mes `i` (i=1..N) = `i`.
        `delta_i = interes_acumulado * i / (1+2+...+N)`                    (Req 17.2)
      - `delta_saldo_periodo <  0` → DECRECIENTE: peso del mes `i` (i=1..N) = `N-i+1`.
        `delta_i = interes_acumulado * (N-i+1) / (1+2+...+N)`              (Req 17.3)

    Invariante (Req 17.4): `sum(deltas) == interes_acumulado` dentro de `tolerancia`,
    preservando el SIGNO de `interes_acumulado` (solo se invierte el ORDEN de los pesos).
    El residuo de redondeo se absorbe en el mes de MAYOR peso —último mes para creciente,
    primer mes para decreciente— para que la suma sea exacta, igual que
    `prorratear_reservas_anual` (Req 17.7).

    Ejemplos (Req 17.5/17.6): `interes_acumulado=75`, `N=5` →
      creciente: `[5, 10, 15, 20, 25]`;  decreciente: `[25, 20, 15, 10, 5]`.

    Entrada NO confiable: `interes_acumulado` y `delta_saldo_periodo` se coercionan a
    float finito (no numérico/vacío → `0.0`, Req 20.1); `n_meses < 1` ⇒ `ValueError`.

    Args:
        interes_acumulado: Interes_Acumulado_Periodo (columna destino de interés).
        n_meses: número de meses del tramo (>= 1).
        delta_saldo_periodo: Δsaldo del periodo; su SIGNO fija la dirección.
        tolerancia: holgura para la invariante de suma (no altera el reparto).

    Returns:
        Lista de `n_meses` deltas mensuales de interés cuya suma es `interes_acumulado`.

    Raises:
        ValueError: si `n_meses < 1`.
    """
    if n_meses < 1:
        raise ValueError("n_meses debe ser >= 1 para prorratear el interés.")

    interes = _coercer_float_finito(interes_acumulado)
    delta_saldo = _coercer_float_finito(delta_saldo_periodo)

    # Número triangular 1+2+...+N (siempre > 0 porque N >= 1).
    triangular = n_meses * (n_meses + 1) / 2

    # CRECIENTE (Δsaldo >= 0): peso del mes i = i.
    # DECRECIENTE (Δsaldo < 0):  peso del mes i = N-i+1.
    if delta_saldo >= 0.0:
        pesos = [i for i in range(1, n_meses + 1)]
        indice_mayor_peso = n_meses - 1  # último mes (mayor peso)
    else:
        pesos = [n_meses - i + 1 for i in range(1, n_meses + 1)]
        indice_mayor_peso = 0  # primer mes (mayor peso)

    # Reparto proporcional al peso, preservando el signo de `interes`. El residuo por
    # redondeo se acumula en el mes de mayor peso para que la suma sea exacta.
    deltas = [interes * peso / triangular for peso in pesos]
    suma_sin_mayor = sum(
        delta for pos, delta in enumerate(deltas) if pos != indice_mayor_peso
    )
    deltas[indice_mayor_peso] = interes - suma_sin_mayor

    return deltas


def _leer_hoja_reservas(ruta_excel_o_bytes, nombre_hoja: str) -> pd.DataFrame:
    """
    Lee una hoja del workbook sin cabecera (header=None) para poder localizar los
    bloques por posición. Trata cualquier fallo de apertura/lectura como entrada no
    confiable y lo convierte en ErrorInputReservas con mensaje genérico.
    """
    import io

    origen: object = ruta_excel_o_bytes
    if isinstance(ruta_excel_o_bytes, (bytes, bytearray)):
        origen = io.BytesIO(bytes(ruta_excel_o_bytes))

    try:
        return pd.read_excel(origen, sheet_name=nombre_hoja, header=None)
    except Exception as exc:  # noqa: BLE001 - normalizamos a un error de dominio genérico
        logger.warning("No se pudo leer una hoja del workbook de reservas.")
        raise ErrorInputReservas(
            "El input de reservas no se pudo leer (hoja no disponible o ilegible)."
        ) from exc


def _validar_hojas(ruta_excel_o_bytes) -> None:
    """
    Valida que el workbook contenga las dos hojas esperadas por nombre. Debe ser el
    PRIMER paso de la lectura, antes de cualquier localización de columnas (Req 2.4,
    12.1).

    Ante hojas faltantes ('Saldo Reservas' o 'Interés a la Reserva') lanza
    `ErrorHojasFaltantes`, una causa distinta del error de formato de encabezado, para
    que el dashboard pueda discriminarla. Como `ErrorHojasFaltantes` hereda de
    `ErrorInputReservas`, las capturas existentes siguen funcionando.

    Un fallo al ABRIR el workbook (corrupto/ilegible) se normaliza al genérico
    `ErrorInputReservas`. Los mensajes son GENÉRICOS: no vuelcan contenido del archivo
    (entrada no confiable, Req 13).
    """
    import io

    origen: object = ruta_excel_o_bytes
    if isinstance(ruta_excel_o_bytes, (bytes, bytearray)):
        origen = io.BytesIO(bytes(ruta_excel_o_bytes))

    try:
        hojas = set(pd.ExcelFile(origen).sheet_names)
    except Exception as exc:  # noqa: BLE001 - normalizamos a error de dominio
        logger.warning("No se pudo abrir el workbook de reservas.")
        raise ErrorInputReservas(
            "El input de reservas no se pudo abrir."
        ) from exc

    faltantes = {_HOJA_SALDO, _HOJA_INTERES} - hojas
    if faltantes:
        logger.warning("El workbook de reservas no contiene las hojas esperadas.")
        raise ErrorHojasFaltantes(
            "El input de reservas no tiene la estructura esperada (hojas faltantes)."
        )


def _localizar_columna_etiqueta(df: pd.DataFrame) -> tuple[int, int]:
    """
    Localiza la columna-etiqueta y la primera columna de mes en una hoja leída con
    header=None. El layout tiene una columna en blanco inicial, luego la etiqueta y a
    continuación 12 columnas de mes (Enero..Diciembre).

    Estrategia robusta: busca una fila cuyas celdas contengan la secuencia de meses;
    la columna del primer mes (Enero) marca `col_mes_ini`, y la columna inmediatamente
    anterior es la columna-etiqueta.

    Returns:
        (col_etiqueta, col_mes_ini)

    Raises:
        ErrorInputReservas si no se puede localizar la fila de meses.
    """
    n_filas, n_cols = df.shape
    for i in range(n_filas):
        for j in range(n_cols):
            if _normalizar_ramo(df.iat[i, j]) != "ENERO":
                continue
            # Verificar que a partir de j haya suficientes columnas para 12 meses y que
            # la secuencia coincida con Enero..Diciembre.
            if j + len(_MESES_ORDEN) > n_cols:
                continue
            secuencia = [
                _normalizar_ramo(df.iat[i, j + k]) for k in range(len(_MESES_ORDEN))
            ]
            if secuencia == list(_MESES_ORDEN) and j >= 1:
                return j - 1, j
    logger.warning("No se localizó la fila de meses en la hoja de reservas.")
    raise ErrorInputReservas(
        "El input de reservas no tiene la estructura esperada (columnas de mes)."
    )


def _fila_valores_meses(df: pd.DataFrame, fila: int, col_mes_ini: int) -> list[float]:
    """
    Extrae los 12 valores mensuales (niveles) de una fila, coercionados a numérico.
    NaN/vacío -> 0.0 (Req 1.4). Entrada NO confiable: se usa pd.to_numeric(coerce).
    """
    crudos = [df.iat[fila, col_mes_ini + k] for k in range(len(_MESES_ORDEN))]
    serie = pd.to_numeric(pd.Series(crudos), errors="coerce").fillna(0.0)
    return [float(v) for v in serie.tolist()]


def _fila_valores_columnas(
    df: pd.DataFrame,
    fila: int,
    columnas: "list[tuple[int, date]]",
) -> list[float]:
    """
    Extrae los valores de una fila para el conjunto de columnas de fecha localizadas
    (ORDENADAS cronológicamente por `_localizar_columnas_fecha`), coercionados a
    numérico finito.

    Entrada NO confiable (Req 13.1): cada celda vacía o no numérica se coerciona a
    `0.0` con `pd.to_numeric(errors='coerce')` + `fillna(0.0)`; se defiende además
    contra `inf`/`-inf` reemplazándolos por `0.0` para garantizar niveles FINITOS.

    Args:
        df: hoja de reservas leída con `header=None`.
        fila: índice de fila de la que se leen los niveles.
        columnas: lista de `(indice_columna, primer_dia_del_mes)` ya ordenada
            cronológicamente. Solo se usan los índices de columna.

    Returns:
        Lista de niveles finitos (float) en el MISMO orden que `columnas`.
    """
    import math

    crudos = [df.iat[fila, indice_col] for indice_col, _mes in columnas]
    serie = pd.to_numeric(pd.Series(crudos), errors="coerce").fillna(0.0)
    return [float(v) if math.isfinite(float(v)) else 0.0 for v in serie.tolist()]


def _construir_saldos_por_ramo(
    df_saldo: pd.DataFrame,
    mapeo_saldo: dict[str, str],
    columnas: "list[tuple[int, date]] | None" = None,
    col_etiqueta: "int | None" = None,
) -> tuple[dict[str, list[float]], list[float]]:
    """
    Recorre la hoja de saldo y agrega los niveles por RAMO DE PORTAFOLIO usando el
    diccionario de mapeo (`formato_origen_normalizado → ramo_portafolio`). Devuelve
    además el saldo TOTAL CAPI (para el agregado Capitalizadora).

    Reglas (Req 12):
      - La etiqueta de cada fila se normaliza y se busca en `mapeo_saldo`. Si el destino
        es "" (vacío) el ramo se IGNORA. Si la etiqueta no está en el mapeo, también se
        ignora (con log de advertencia): la clasificación es por diccionario explícito.
      - Varias etiquetas que apuntan al mismo `ramo_portafolio` (p. ej. los demás ramos)
        se SUMAN mes a mes en ese ramo destino.
      - Ignora cabeceras de bloque y filas TOTAL, EXCEPTO TOTAL CAPI (se guarda aparte).
      - Los ramos individuales del bloque CAPITALIZADORA se ignoran (el agregado sale de
        TOTAL CAPI, Req 12.6).

    Modos de localización de columnas de datos:
      - Por `Encabezado_Fecha` (nuevo, Req 1.1): si se pasan `columnas` y `col_etiqueta`
        (tal como los devuelve `_localizar_columnas_fecha`), los niveles se leen de esas
        columnas de fecha, en su orden cronológico. Cada lista de niveles tiene tantos
        elementos como columnas de fecha.
      - Por nombre de mes (retrocompatible): si NO se pasan `columnas`, se localiza la
        secuencia Enero..Diciembre con `_localizar_columna_etiqueta` y se leen 12 niveles.

    Returns:
        (saldos_por_ramo_portafolio, saldo_total_capi)
        saldos_por_ramo_portafolio: {ramo_portafolio: [niveles agregados por columna]}
        saldo_total_capi: [niveles por columna] (todo 0.0 si no hay fila TOTAL CAPI)
    """
    # Modo Encabezado_Fecha vs. modo nombre-de-mes (retrocompatible).
    usar_fechas = columnas is not None and col_etiqueta is not None
    if usar_fechas:
        n_niveles = len(columnas)  # type: ignore[arg-type]

        def _leer(fila: int) -> list[float]:
            return _fila_valores_columnas(df_saldo, fila, columnas)  # type: ignore[arg-type]
    else:
        col_etiqueta, col_mes_ini = _localizar_columna_etiqueta(df_saldo)
        n_niveles = len(_MESES_ORDEN)

        def _leer(fila: int) -> list[float]:
            return _fila_valores_meses(df_saldo, fila, col_mes_ini)

    saldos: dict[str, list[float]] = {}
    saldo_total_capi: list[float] = [0.0] * n_niveles

    def _sumar(destino: str, niveles: list[float]) -> None:
        acum = saldos.get(destino)
        if acum is None:
            saldos[destino] = list(niveles)
        else:
            saldos[destino] = [a + b for a, b in zip(acum, niveles)]

    compania_actual: str | None = None
    for fila in range(df_saldo.shape[0]):
        etiqueta = _normalizar_ramo(df_saldo.iat[fila, col_etiqueta])
        if not etiqueta:
            continue

        # Cambios de bloque (para poder saltar los ramos individuales de Capitalizadora).
        if etiqueta == _HEADER_BOLIVAR:
            compania_actual = _HEADER_BOLIVAR
            continue
        if etiqueta == _HEADER_COMERCIALES:
            compania_actual = _HEADER_COMERCIALES
            continue
        if etiqueta == _HEADER_CAPITALIZADORA:
            compania_actual = _HEADER_CAPITALIZADORA
            continue

        # TOTAL CAPI: único total que se usa (saldo del agregado Capitalizadora).
        if etiqueta == _TOTAL_CAPI:
            saldo_total_capi = _leer(fila)
            continue

        # Resto de cabeceras/totales: ignorar.
        if etiqueta in _ETIQUETAS_NO_RAMO_SALDO:
            continue

        # Ramos individuales de Capitalizadora: se ignoran (Req 12.6).
        if compania_actual == _HEADER_CAPITALIZADORA:
            continue

        # Resolver el destino por el diccionario de mapeo (entrada por dato, no por regla
        # presente/ausente). Ausente en el mapeo o destino "" ⇒ ignorar.
        destino = mapeo_saldo.get(etiqueta)
        if destino is None:
            logger.warning(
                "Etiqueta de saldo de reservas sin mapeo a ramo de portafolio; se ignora."
            )
            continue
        if destino == "":
            continue  # ignorar explícito (p. ej. reserva insuficiencia de activos)

        _sumar(destino, _leer(fila))

    return saldos, saldo_total_capi


def _construir_intereses(
    df_interes: pd.DataFrame,
    mapeo_interes: dict[str, str],
    columnas: "list[tuple[int, date]] | None" = None,
    col_etiqueta: "int | None" = None,
) -> tuple[dict[str, list[float]], list[float]]:
    """
    Recorre la hoja de interés y agrega los niveles por RAMO DE PORTAFOLIO usando el
    diccionario de mapeo de interés, más la línea 'Capitalizadora' por separado.

    Reglas:
      - La etiqueta de cada fila se normaliza y se busca en `mapeo_interes`. La línea que
        mapea a 'CAPITALIZADORA' se devuelve aparte (interés del agregado capi). El resto
        (Bolívar) se agrupa por `ramo_portafolio`.
      - Etiqueta ausente del mapeo o destino "" ⇒ se ignora (sub-cabeceras 'Bolivar',
        totales, etc. no están en el diccionario).
      - Varias etiquetas al mismo ramo destino se SUMAN mes a mes.

    Modos de localización de columnas de datos (idénticos a `_construir_saldos_por_ramo`):
      - Por `Encabezado_Fecha` cuando se pasan `columnas` y `col_etiqueta`.
      - Por nombre de mes (retrocompatible) cuando NO se pasan.

    Returns:
        (intereses_por_ramo_portafolio, interes_capitalizadora)
        intereses_por_ramo_portafolio: {ramo_portafolio (Bolívar): [niveles por columna]}
    """
    usar_fechas = columnas is not None and col_etiqueta is not None
    if usar_fechas:
        n_niveles = len(columnas)  # type: ignore[arg-type]

        def _leer(fila: int) -> list[float]:
            return _fila_valores_columnas(df_interes, fila, columnas)  # type: ignore[arg-type]
    else:
        col_etiqueta, col_mes_ini = _localizar_columna_etiqueta(df_interes)
        n_niveles = len(_MESES_ORDEN)

        def _leer(fila: int) -> list[float]:
            return _fila_valores_meses(df_interes, fila, col_mes_ini)

    intereses: dict[str, list[float]] = {}
    interes_capi: list[float] = [0.0] * n_niveles

    def _sumar(destino: str, niveles: list[float]) -> None:
        acum = intereses.get(destino)
        if acum is None:
            intereses[destino] = list(niveles)
        else:
            intereses[destino] = [a + b for a, b in zip(acum, niveles)]

    for fila in range(df_interes.shape[0]):
        etiqueta = _normalizar_ramo(df_interes.iat[fila, col_etiqueta])
        if not etiqueta:
            continue

        destino = mapeo_interes.get(etiqueta)
        if destino is None or destino == "":
            continue  # sub-cabeceras/totales u otras filas no mapeadas: ignorar

        niveles = _leer(fila)
        if destino == RAMO_CAPITALIZADORA:
            # Interés del agregado Capitalizadora (se resta de TOTAL CAPI).
            interes_capi = [a + b for a, b in zip(interes_capi, niveles)]
        else:
            _sumar(destino, niveles)

    return intereses, interes_capi


def _fila_niveles_por_fecha(
    df: pd.DataFrame,
    fila: int,
    columnas: "list[tuple[int, date]]",
) -> "dict[date, float]":
    """
    Lee los niveles de una fila indexados por su `Mes_Absoluto` (`date`), en lugar de por
    posición. Reutiliza `_fila_valores_columnas` (coerción a numérico FINITO, no
    numérico/vacío/inf → 0.0, Req 13.1) y empareja cada nivel con la fecha de su columna.

    Args:
        df: hoja de reservas leída con `header=None`.
        fila: índice de fila de la que se leen los niveles.
        columnas: lista de `(indice_columna, primer_dia_del_mes)` ya ordenada
            cronológicamente (tal como la devuelve `_localizar_columnas_fecha`).

    Returns:
        `{primer_dia_del_mes: nivel_finito}` para cada columna de fecha de la hoja.
    """
    valores = _fila_valores_columnas(df, fila, columnas)
    return {mes: valores[idx] for idx, (_col, mes) in enumerate(columnas)}


def _construir_saldos_por_ramo_fecha(
    df_saldo: pd.DataFrame,
    mapeo_saldo: dict[str, str],
    columnas: "list[tuple[int, date]]",
    col_etiqueta: int,
) -> "tuple[dict[str, dict[date, float]], dict[date, float]]":
    """
    Variante indexada por FECHA de `_construir_saldos_por_ramo` (Corrección 1): agrega
    los niveles de saldo por RAMO DE PORTAFOLIO en un mapa `{ramo: {Mes_Absoluto: nivel}}`
    en lugar de listas posicionales, para que el emparejamiento saldo↔interés se haga por
    la MISMA fecha y no por posición (los ejes de fecha de las dos hojas pueden diferir).

    Precedencia de línea pre-agregada (Corrección 2, regla confirmada por el usuario):
    cuando una hoja de saldo trae una fila cuya etiqueta normalizada ES un destino del
    mapeo (p. ej. "DEMAS RAMOS DE SEGUROS GENERALES") ADEMÁS de ramos individuales que
    mapean a ese mismo destino, se USA la línea pre-agregada y se IGNORAN los individuos
    de ese destino (para evitar doble conteo). La precedencia aplica solo cuando la línea
    pre-agregada supersede a OTROS orígenes individuales del mismo destino; un ramo 1:1
    (origen == destino, p. ej. "SOAT" → "SOAT") NO supersede nada por sí solo.

    Reglas conservadas de la versión posicional (Req 12):
      - Etiqueta ausente del mapeo o destino "" ⇒ se ignora (salvo que la etiqueta sea a
        su vez un destino: en ese caso es la línea pre-agregada y se RECONOCE).
      - Ignora cabeceras de bloque y filas TOTAL, EXCEPTO TOTAL CAPI (se guarda aparte).
      - Los ramos individuales del bloque CAPITALIZADORA se ignoran (Req 12.6).

    Returns:
        `(saldos_por_ramo, saldo_total_capi)` donde
        `saldos_por_ramo` es `{ramo_portafolio: {Mes_Absoluto: nivel_agregado}}` y
        `saldo_total_capi` es `{Mes_Absoluto: nivel}` (0.0 por fecha si no hay TOTAL CAPI).
    """
    # Conjunto de valores-destino del mapeo (excluyendo "") — sirve para RECONOCER una
    # fila cuya etiqueta ES un destino (línea pre-agregada). Se comparan etiquetas ya
    # normalizadas (strip/upper), igual que las claves del mapeo.
    destinos_validos = {d for d in mapeo_saldo.values() if d}

    def _leer(fila: int) -> "dict[date, float]":
        return _fila_niveles_por_fecha(df_saldo, fila, columnas)

    # ── Pass 1: recolectar, por destino, la línea pre-agregada (si existe) y las
    #    contribuciones de los orígenes individuales (etiquetas cuyo destino != su
    #    propia etiqueta). Un ramo 1:1 (origen == destino) se trata aparte para no
    #    "superseder" nada por sí mismo (ver Pass 2).
    lineas_preagregadas: "dict[str, dict[date, float]]" = {}
    individuos_por_destino: "dict[str, list[dict[date, float]]]" = {}
    saldo_total_capi: "dict[date, float]" = {mes: 0.0 for _col, mes in columnas}

    compania_actual: str | None = None
    for fila in range(df_saldo.shape[0]):
        etiqueta = _normalizar_ramo(df_saldo.iat[fila, col_etiqueta])
        if not etiqueta:
            continue

        # Cambios de bloque.
        if etiqueta == _HEADER_BOLIVAR:
            compania_actual = _HEADER_BOLIVAR
            continue
        if etiqueta == _HEADER_COMERCIALES:
            compania_actual = _HEADER_COMERCIALES
            continue
        if etiqueta == _HEADER_CAPITALIZADORA:
            compania_actual = _HEADER_CAPITALIZADORA
            continue

        # TOTAL CAPI: único total que se usa (saldo del agregado Capitalizadora).
        if etiqueta == _TOTAL_CAPI:
            saldo_total_capi = _leer(fila)
            continue

        # Resto de cabeceras/totales: ignorar.
        if etiqueta in _ETIQUETAS_NO_RAMO_SALDO:
            continue

        # Ramos individuales de Capitalizadora: se ignoran (Req 12.6).
        if compania_actual == _HEADER_CAPITALIZADORA:
            continue

        # ¿La etiqueta ES en sí misma un destino del mapeo? → línea pre-agregada
        # (Corrección 2). Se registra como la línea del destino con ESE nombre.
        if etiqueta in destinos_validos:
            niveles = _leer(fila)
            acum = lineas_preagregadas.get(etiqueta)
            if acum is None:
                lineas_preagregadas[etiqueta] = niveles
            else:
                # Si hubiese más de una fila con el nombre del destino, se suman entre sí.
                lineas_preagregadas[etiqueta] = {
                    mes: acum.get(mes, 0.0) + niveles.get(mes, 0.0)
                    for mes in set(acum) | set(niveles)
                }
            continue

        # Origen individual: resolver destino por el diccionario. Ausente ⇒ ignorar;
        # destino "" ⇒ ignorar explícito.
        destino = mapeo_saldo.get(etiqueta)
        if destino is None:
            logger.warning(
                "Etiqueta de saldo de reservas sin mapeo a ramo de portafolio; se ignora."
            )
            continue
        if destino == "":
            continue

        individuos_por_destino.setdefault(destino, []).append(_leer(fila))

    # ── Pass 2: agregación final por destino aplicando la precedencia. ──
    saldos: "dict[str, dict[date, float]]" = {}

    def _sumar_en(destino: str, niveles: "dict[date, float]") -> None:
        acum = saldos.get(destino)
        if acum is None:
            saldos[destino] = dict(niveles)
        else:
            for mes, v in niveles.items():
                acum[mes] = acum.get(mes, 0.0) + v

    destinos = set(lineas_preagregadas) | set(individuos_por_destino)
    for destino in destinos:
        preagregada = lineas_preagregadas.get(destino)
        individuos = individuos_por_destino.get(destino, [])

        if preagregada is not None and individuos:
            # Hay línea pre-agregada Y orígenes individuales → la pre-agregada SUPERSEDE
            # a los individuos (evita doble conteo). Advertencia genérica (sin volcar
            # contenido de celdas, Req 13).
            logger.warning(
                "Línea de saldo pre-agregada presente para un destino: se usa la línea "
                "agregada y se ignoran los ramos individuales de ese destino (para "
                "evitar doble conteo)."
            )
            _sumar_en(destino, preagregada)
        elif preagregada is not None:
            # Solo la línea 1:1 / pre-agregada, sin otros individuos → comportamiento
            # sin cambios (ramo normal cuya etiqueta coincide con su destino).
            _sumar_en(destino, preagregada)
        else:
            # Sin línea pre-agregada → sumar los individuos como siempre.
            for niveles in individuos:
                _sumar_en(destino, niveles)

    return saldos, saldo_total_capi


def _construir_intereses_fecha(
    df_interes: pd.DataFrame,
    mapeo_interes: dict[str, str],
    columnas: "list[tuple[int, date]]",
    col_etiqueta: int,
) -> "tuple[dict[str, dict[date, float]], dict[date, float]]":
    """
    Variante indexada por FECHA de `_construir_intereses` (Corrección 1): agrega los
    niveles de interés por RAMO DE PORTAFOLIO en `{ramo: {Mes_Absoluto: nivel}}` y el
    interés del agregado Capitalizadora en `{Mes_Absoluto: nivel}`.

    Se conservan las reglas de mapeo por diccionario: etiqueta ausente del mapeo o
    destino "" ⇒ se ignora; varias etiquetas al mismo destino se SUMAN por fecha; la
    línea que mapea a 'CAPITALIZADORA' se devuelve aparte.

    Returns:
        `(intereses_por_ramo, interes_capi)` con claves de fecha (`Mes_Absoluto`).
    """
    def _leer(fila: int) -> "dict[date, float]":
        return _fila_niveles_por_fecha(df_interes, fila, columnas)

    intereses: "dict[str, dict[date, float]]" = {}
    interes_capi: "dict[date, float]" = {mes: 0.0 for _col, mes in columnas}

    def _sumar_en(destino: str, niveles: "dict[date, float]") -> None:
        acum = intereses.get(destino)
        if acum is None:
            intereses[destino] = dict(niveles)
        else:
            for mes, v in niveles.items():
                acum[mes] = acum.get(mes, 0.0) + v

    for fila in range(df_interes.shape[0]):
        etiqueta = _normalizar_ramo(df_interes.iat[fila, col_etiqueta])
        if not etiqueta:
            continue

        destino = mapeo_interes.get(etiqueta)
        if destino is None or destino == "":
            continue  # sub-cabeceras/totales u otras filas no mapeadas: ignorar

        niveles = _leer(fila)
        if destino == RAMO_CAPITALIZADORA:
            for mes, v in niveles.items():
                interes_capi[mes] = interes_capi.get(mes, 0.0) + v
        else:
            _sumar_en(destino, niveles)

    return intereses, interes_capi


def _variacion_mes_a_mes(
    niveles: "list[float] | float",
    nivel_desde: "float | None" = None,
) -> "list[float] | float":
    """
    Deriva la variación entre niveles consecutivos (Δ = nivel_hasta − nivel_desde).

    Generalizada (Req 3.5) para soportar dos modos:
      - Modo lista (retrocompatible): recibe una lista de niveles y devuelve las
        variaciones consecutivas [nivel(t) − nivel(t-1) para t=1..len-1].
      - Modo par de columnas: recibe dos escalares `(nivel_hasta, nivel_desde)` y
        devuelve la variación `nivel_hasta − nivel_desde` de ese único salto. Este es
        el modo usado por la derivación por `Encabezado_Fecha`, donde cada salto entre
        dos columnas cronológicamente consecutivas produce una variación.

    Args:
        niveles: lista de niveles (modo lista) o el nivel de la columna DESTINO del
            salto (modo par, cuando se pasa también `nivel_desde`).
        nivel_desde: cuando se provee, activa el modo par; es el nivel de la columna
            cronológicamente ANTERIOR (origen del salto).

    Returns:
        Lista de variaciones consecutivas (modo lista) o el escalar
        `nivel_hasta − nivel_desde` (modo par).
    """
    if nivel_desde is not None:
        # Modo par de columnas: variación de un único salto (destino − origen).
        return float(niveles) - float(nivel_desde)  # type: ignore[arg-type]
    # Modo lista (retrocompatible): variaciones entre niveles consecutivos.
    return [niveles[t] - niveles[t - 1] for t in range(1, len(niveles))]  # type: ignore[index]


def _derivar_layout_mensual(
    *,
    columnas_saldo: "list[tuple[int, date]]",
    saldos_por_ramo: "dict[str, dict[date, float]]",
    saldo_total_capi: "dict[date, float]",
    intereses_por_ramo: "dict[str, dict[date, float]]",
    interes_capi: "dict[date, float]",
) -> pd.DataFrame:
    """
    Derivación `Layout_Mensual` (comportamiento EXISTENTE, extraído sin cambios).

    Clasifica los saltos de la hoja de saldo (eje temporal maestro) con
    `_clasificar_saltos` (Δ ∈ {1, 12}; otro ⇒ `ErrorFormatoEncabezado`) y, por ramo de
    portafolio y para la Capitalizadora, emite una fila por salto con
    `delta_reserva = Δsaldo − Δinterés` medido sobre las MISMAS fechas del salto y
    `mes = Mes_Absoluto` de la columna DESTINO en formato "YYYY-MM". El `tipo_salto`
    (`'mensual'`/`'anual'`) refleja la clasificación por fila (Req 8).

    Se extrae a una función propia para que el camino `Layout_Por_Tramo` pueda reutilizar
    la MISMA derivación mensual como fallback/mezcla sin duplicar la lógica. La salida es
    idéntica a la del camino con `fecha_corte=None` (retrocompatibilidad total, Req 14.2).

    Args:
        columnas_saldo: columnas `(indice, primer_dia_del_mes)` del eje maestro de saldo,
            ordenadas cronológicamente ascendente.
        saldos_por_ramo: `{ramo: {Mes_Absoluto: nivel_saldo}}` por ramo de portafolio.
        saldo_total_capi: `{Mes_Absoluto: nivel_saldo}` del agregado Capitalizadora.
        intereses_por_ramo: `{ramo: {Mes_Absoluto: nivel_interes}}` por ramo.
        interes_capi: `{Mes_Absoluto: nivel_interes}` del agregado Capitalizadora.

    Returns:
        DataFrame con columnas EXACTAS {ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto}.

    Raises:
        ErrorFormatoEncabezado: si algún salto difiere en meses ∉ {1, 12}.
    """
    saltos_saldo = _clasificar_saltos(columnas_saldo)

    # Fecha de origen de cada salto: el `mes_hasta` del salto anterior es el `mes_desde`
    # del actual; para el primer salto, es la primera columna de saldo. `columnas_saldo`
    # viene ordenada cronológicamente ascendente por su segundo elemento (la fecha).
    fechas_saldo = [mes for _col, mes in columnas_saldo]

    filas: list[dict[str, object]] = []

    def _nivel_en(niveles_por_fecha: "dict[date, float]", mes: "date") -> float:
        # Nivel finito para la fecha `mes`; 0.0 si esa hoja no tiene columna para esa
        # fecha (p. ej. interés sin la fecha del salto de saldo). Corrección 1.
        return float(niveles_por_fecha.get(mes, 0.0))

    def _emitir(
        ramo_salida: str,
        niveles_saldo: "dict[date, float]",
        niveles_interes: "dict[date, float]",
    ) -> None:
        for i, (_cd, _ch, tipo, mes_hasta) in enumerate(saltos_saldo):
            mes_desde = fechas_saldo[i]  # origen del salto (cronológicamente anterior)
            delta_saldo = _nivel_en(niveles_saldo, mes_hasta) - _nivel_en(
                niveles_saldo, mes_desde
            )
            # Δinterés se mide en LAS MISMAS fechas del salto de saldo; si el interés no
            # tiene columna para una fecha, su nivel ahí es 0.0 (finito).
            delta_interes = _nivel_en(niveles_interes, mes_hasta) - _nivel_en(
                niveles_interes, mes_desde
            )
            delta = float(delta_saldo) - float(delta_interes)
            filas.append(
                {
                    "ramo": ramo_salida,
                    "mes": f"{mes_hasta.year:04d}-{mes_hasta.month:02d}",
                    "delta_reserva": delta,
                    "tipo_salto": tipo,
                }
            )

    # ── Por ramo de portafolio: variación = Δsaldo − Δinterés (interés 0 si no hay) ──
    ceros_int: "dict[date, float]" = {}
    for ramo, niveles_saldo in saldos_por_ramo.items():
        niveles_interes = intereses_por_ramo.get(ramo, ceros_int)
        _emitir(ramo, niveles_saldo, niveles_interes)

    # ── Capitalizadora: ramo único, TOTAL CAPI − interés Capitalizadora ──
    _emitir(RAMO_CAPITALIZADORA, saldo_total_capi, interes_capi)

    return pd.DataFrame(
        filas, columns=["ramo", "mes", "delta_reserva", "tipo_salto"]
    )


def _coercer_fecha_corte(fecha_corte: object) -> "date":
    """
    Coerciona la `Fecha_Corte` recibida por `derivar_variaciones_reservas` (entrada NO
    confiable) al primer día de su mes (`date`) para dimensionar el `Tramo_Parcial`.

    A diferencia de `_primer_dia_del_mes` (que lanza `ErrorFormatoEncabezado`, una causa
    de FORMATO DE ENCABEZADO del archivo), aquí una `Fecha_Corte` no coercible es un
    error de la ENTRADA de la corrida y debe señalarse con el error GENÉRICO
    `ErrorInputReservas` ANTES de dimensionar tramos o derivar variaciones (Req 20.3),
    sin volcar el valor recibido en el mensaje ni en los logs.

    Args:
        fecha_corte: `Fecha_Corte` del dashboard (entrada NO confiable). Puede llegar
            como `date`/`datetime`/`pd.Timestamp` o como texto ("2026-07-31").

    Returns:
        `date` del primer día del mes de la `Fecha_Corte` (el `Mes_Corte`).

    Raises:
        ErrorInputReservas: si `fecha_corte` no es coercible a una fecha válida.
    """
    marca = pd.to_datetime(fecha_corte, errors="coerce", dayfirst=False)
    if pd.isna(marca):
        # Fecha_Corte inválida: error GENÉRICO sin volcar el valor recibido (Req 20.3).
        logger.warning("La Fecha_Corte recibida por el lector de reservas no es válida.")
        raise ErrorInputReservas(
            "La fecha de corte de la proyección no es una fecha válida."
        )
    # Reutiliza `_primer_dia_del_mes` para el camino de fecha válida (obtiene mes_corte).
    return _primer_dia_del_mes(marca)


def _aplica_tramo_parcial(
    columnas: "list[tuple[int, date]]",
    mes_corte: "date",
) -> bool:
    """
    Decide si corresponde ACTIVAR el camino `Layout_Por_Tramo` para la `Fecha_Corte`
    dada (Decisión 6): el camino por tramos SOLO se activa cuando una columna de
    diciembre sigue INMEDIATAMENTE a la columna del `Mes_Corte`.

    Concretamente, localiza la última columna cuyo `Mes_Absoluto` es `<= mes_corte`
    (base del tramo, que puede ser sintética si el `Mes_Corte` no está entre las
    columnas, Req 15.2) y evalúa la primera columna estrictamente posterior. El camino
    por tramos aplica solo si esa primera columna posterior es un diciembre y el salto
    `Mes_Corte`→diciembre abarca `N != 1` meses (si `N == 1` es un simple `Salto_Mensual`
    del `Layout_Mensual`).

    En cualquier otro caso (no hay columnas posteriores al `Mes_Corte`, la siguiente no
    es diciembre, o `N == 1`) el lector cae de vuelta al camino puro `Layout_Mensual`
    (Δ ∈ {1, 12}), preservando el comportamiento actual.

    Args:
        columnas: lista `(indice_columna, primer_dia_del_mes)` ORDENADA cronológicamente
            ascendente (la del eje maestro de saldo).
        mes_corte: primer día del `Mes_Corte`.

    Returns:
        `True` si corresponde el camino `Layout_Por_Tramo` (existe un `Tramo_Parcial`);
        `False` si corresponde el camino puro `Layout_Mensual`.
    """
    meses_cols = [mes for _indice, mes in columnas]
    if not meses_cols:
        return False

    # Índice de la última columna <= Mes_Corte (base del tramo, posiblemente sintética).
    indice_ultima_pre = -1
    for pos, mes in enumerate(meses_cols):
        if mes <= mes_corte:
            indice_ultima_pre = pos
        else:
            break

    indice_post = indice_ultima_pre + 1
    if indice_post >= len(meses_cols):
        # No hay columnas posteriores al Mes_Corte ⇒ no hay Tramo_Parcial.
        return False

    primer_destino = meses_cols[indice_post]
    n_parcial = _delta_meses(mes_corte, primer_destino)
    # Tramo_Parcial válido: diciembre inmediatamente posterior y N != 1 (Decisión 6).
    return primer_destino.month == 12 and n_parcial != 1


def _derivar_por_tramos(
    *,
    columnas_saldo: "list[tuple[int, date]]",
    saldos: "dict[str, dict[date, float]]",
    saldo_total_capi: "dict[date, float]",
    intereses: "dict[str, dict[date, float]]",
    interes_capi: "dict[date, float]",
    mes_corte: "date",
) -> pd.DataFrame:
    """
    Bucle de derivación del camino `Layout_Por_Tramo` (Decisión 6/7).

    Dimensiona los tramos con `detectar_tramos_por_corte` (que además VALIDA los saltos
    fuera del `Tramo_Parcial`, Req 15.7, y materializa el `Tramo_Parcial`, los
    `Tramo_Anual` y los saltos mensuales) y recorre los `segmentos` resultantes por cada
    ramo de portafolio y para la Capitalizadora:

      - `tipo == 'mensual'`: conserva el comportamiento `Δsaldo − Δinterés` por salto
        (nivel destino − nivel base para saldo e interés), emitiendo la fila del mes
        destino con `tipo_salto = 'mensual'`.
      - `tipo ∈ {'parcial', 'anual'}` (N = `len(segmento.meses)`):
          * `Δsaldo_periodo = nivel_saldo(dic destino) − nivel_saldo(mes_base)` (Req 16.2).
          * Deltas mensuales de saldo por Prorrateo_Uniforme, reutilizando
            `prorratear_reservas_anual({ramo: Δsaldo_periodo}, N)` (Req 16.3, 16.6).
          * Deltas mensuales de interés por Prorrateo_Lineal_Direccional,
            `prorratear_interes_lineal_direccional(nivel_interes(dic destino), N,
            Δsaldo_periodo)`, usando el `Interes_Acumulado_Periodo` DIRECTAMENTE (es la
            variación acumulada del periodo, NO un nivel; no se resta contra la base,
            Req 17.1).
          * Emite UNA fila por cada mes del tramo con `delta_reserva = Δsaldo_mensual −
            Δinteres_mensual` (Req 18.1), `mes = Mes_Absoluto` "YYYY-MM" (Req 18.5) y
            `tipo_salto = 'mensual'` (Decisión 7: se emiten como mensuales para que
            persistidor/cargador/motor NO cambien).

    Recibe los niveles por ramo indexados por fecha para saldo (`saldos`,
    `saldo_total_capi`) e interés (`intereses`, `interes_capi`) ya construidos por
    `_construir_saldos_por_ramo_fecha` / `_construir_intereses_fecha` (UNCHANGED), más el
    `mes_corte` (nivel base del `Tramo_Parcial`). Devuelve el
    `DataFrame[ramo, mes, delta_reserva, tipo_salto]` que espera `persistir_input_reservas`
    (sin cambios al persistidor).

    Args:
        columnas_saldo: columnas `(indice, primer_dia_del_mes)` del eje maestro de saldo,
            ordenadas cronológicamente ascendente.
        saldos: `{ramo: {Mes_Absoluto: nivel_saldo}}` por ramo de portafolio.
        saldo_total_capi: `{Mes_Absoluto: nivel_saldo}` del agregado Capitalizadora.
        intereses: `{ramo: {Mes_Absoluto: nivel_interes}}` por ramo de portafolio.
        interes_capi: `{Mes_Absoluto: nivel_interes}` del agregado Capitalizadora.
        mes_corte: primer día del `Mes_Corte` (nivel BASE del `Tramo_Parcial`).

    Returns:
        DataFrame con columnas EXACTAS {ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto}.

    Raises:
        ErrorFormatoEncabezado: si un salto fuera del `Tramo_Parcial` difiere en un
            número de meses distinto de 1 y de 12 (Req 15.7), detectado por
            `detectar_tramos_por_corte`.
    """
    # Dimensionar/validar los tramos según la Fecha_Corte (Req 14.3, 15). Esto VALIDA el
    # formato del layout por tramos (saltos fuera del Tramo_Parcial ∈ {1,12}, Req 15.7) y
    # deja los segmentos listos para el bucle de derivación.
    segmentos = detectar_tramos_por_corte(columnas_saldo, mes_corte)

    # Prorrateo_Uniforme del saldo: se REUTILIZA `prorratear_reservas_anual` de
    # `proyeccion_tramos` (fuente única de esa lógica, Req 19.4). Import perezoso y local
    # —igual que el orquestador de `app_dash.py`— para no crear ciclos de importación ni
    # duplicar la maquinaria en este módulo.
    import proyeccion_tramos as _ptr

    def _prorratear_saldo_uniforme(
        delta_saldo_periodo: float, n_meses: int
    ) -> "list[float]":
        """
        Reparte UNIFORMEMENTE `delta_saldo_periodo` entre los N meses del tramo (Req 16.3,
        16.6), delegando en `prorratear_reservas_anual` (Prorrateo_Uniforme). Devuelve una
        lista de N deltas de saldo en orden de mes ASCENDENTE (offset 1..N ↔ meses[0..N-1]).

        `prorratear_reservas_anual` OMITE ramos con variación 0 y normaliza la clave del
        ramo (strip/upper). Aquí se usa una clave FIJA controlada por el código y se
        reconstruye la lista por offset; cuando el ramo se omite (Δ==0 o no coercible), la
        invariante de suma se mantiene trivialmente con una lista de ceros.
        """
        clave = "R"  # clave controlada por el código (no proviene de entrada externa)
        mapa = _ptr.prorratear_reservas_anual({clave: delta_saldo_periodo}, n_meses)
        por_offset = mapa.get(clave, {})
        return [float(por_offset.get(offset, 0.0)) for offset in range(1, n_meses + 1)]

    filas: list[dict[str, object]] = []

    def _nivel_en(niveles_por_fecha: "dict[date, float]", mes: "date") -> float:
        # Nivel finito para la fecha `mes`; 0.0 si esa hoja no tiene columna para esa
        # fecha (p. ej. interés sin la fecha destino del saldo). Consistente con el
        # camino `Layout_Mensual` (`_derivar_layout_mensual._nivel_en`).
        return float(niveles_por_fecha.get(mes, 0.0))

    def _emitir(
        ramo_salida: str,
        niveles_saldo: "dict[date, float]",
        niveles_interes: "dict[date, float]",
    ) -> None:
        for segmento in segmentos:
            if segmento.tipo == "mensual":
                # Salto mensual (Δ=1 o 12 fuera del Tramo_Parcial): conserva el
                # comportamiento `Δsaldo − Δinterés` por nivel destino menos nivel base,
                # emitiendo la fila del mes destino con `tipo_salto = 'mensual'`.
                mes_hasta = segmento.meses[-1]
                mes_base = segmento.mes_base
                delta_saldo = _nivel_en(niveles_saldo, mes_hasta) - _nivel_en(
                    niveles_saldo, mes_base
                )
                delta_interes = _nivel_en(niveles_interes, mes_hasta) - _nivel_en(
                    niveles_interes, mes_base
                )
                filas.append(
                    {
                        "ramo": ramo_salida,
                        "mes": f"{mes_hasta.year:04d}-{mes_hasta.month:02d}",
                        "delta_reserva": float(delta_saldo) - float(delta_interes),
                        "tipo_salto": "mensual",
                    }
                )
                continue

            # Tramo_Parcial ('parcial', N meses) o Tramo_Anual ('anual', 12 meses).
            n_meses = len(segmento.meses)
            if n_meses < 1:
                continue
            mes_base = segmento.mes_base
            mes_destino = segmento.meses[-1]  # diciembre destino del tramo

            # Δsaldo_periodo = Nivel_Saldo(dic destino) − Nivel_Saldo(Mes_Corte/base)
            # (Req 16.1, 16.2). El saldo trae NIVELES, se deriva por resta.
            delta_saldo_periodo = _nivel_en(niveles_saldo, mes_destino) - _nivel_en(
                niveles_saldo, mes_base
            )
            # Deltas mensuales de saldo por Prorrateo_Uniforme (Req 16.3, 16.4, 16.6).
            deltas_saldo = _prorratear_saldo_uniforme(delta_saldo_periodo, n_meses)

            # Interes_Acumulado_Periodo: el interés trae una variación YA ACUMULADA del
            # periodo (NO un nivel, Req 17.1). Se usa DIRECTAMENTE el nivel de la columna
            # destino como monto a prorratear (no por resta contra la base). Deltas
            # mensuales por Prorrateo_Lineal_Direccional según el signo de Δsaldo_periodo
            # (Req 17.2-17.4, 17.7).
            interes_acumulado = _nivel_en(niveles_interes, mes_destino)
            deltas_interes = prorratear_interes_lineal_direccional(
                interes_acumulado, n_meses, delta_saldo_periodo
            )

            # Una fila por mes del tramo: Variación_Reserva = Δsaldo − Δinterés (Req 18.1),
            # `mes = Mes_Absoluto` "YYYY-MM" (Req 18.5), `tipo_salto = 'mensual'`
            # (Decisión 7: se emiten como mensuales para no tocar persistidor/cargador/motor).
            for i, mes_i in enumerate(segmento.meses):
                filas.append(
                    {
                        "ramo": ramo_salida,
                        "mes": f"{mes_i.year:04d}-{mes_i.month:02d}",
                        "delta_reserva": float(deltas_saldo[i]) - float(deltas_interes[i]),
                        "tipo_salto": "mensual",
                    }
                )

    # ── Por ramo de portafolio: variación = Δsaldo − Δinterés (interés 0 si no hay) ──
    ceros_int: "dict[date, float]" = {}
    for ramo, niveles_saldo in saldos.items():
        niveles_interes = intereses.get(ramo, ceros_int)
        _emitir(ramo, niveles_saldo, niveles_interes)

    # ── Capitalizadora: ramo único, TOTAL CAPI − interés Capitalizadora ──
    _emitir(RAMO_CAPITALIZADORA, saldo_total_capi, interes_capi)

    return pd.DataFrame(
        filas, columns=["ramo", "mes", "delta_reserva", "tipo_salto"]
    )


def derivar_variaciones_reservas(
    ruta_excel_o_bytes: "str | bytes",
    ramos_portafolio: "set[str]",
    fecha_corte: "object | None" = None,
) -> pd.DataFrame:
    """
    Lee el Excel de reservas (hojas 'Saldo Reservas' e 'Interés a la Reserva'), deriva
    la variación por ramo entre columnas cronológicamente consecutivas localizadas por
    `Encabezado_Fecha`, y devuelve un DataFrame con columnas EXACTAS
    {ramo, mes, delta_reserva, tipo_salto} listo para `persistir_input_reservas`.

    Coexistencia de disposiciones (Req 14, 15, Decisión 5/6). El parámetro OPCIONAL
    `fecha_corte` (la `Fecha_Corte` del dashboard) selecciona la disposición:
      - `fecha_corte is None` ⇒ camino `Layout_Mensual` EXACTAMENTE el actual, sin ningún
        cambio observable en la salida (retrocompatibilidad total, Req 14.2).
      - `fecha_corte` presente pero NO coercible a fecha ⇒ `ErrorInputReservas` genérico
        ANTES de dimensionar tramos o derivar, sin volcar el valor recibido (Req 20.3).
      - `fecha_corte` válida ⇒ se calcula el `Mes_Corte` (`_primer_dia_del_mes`) y se
        activa el camino `Layout_Por_Tramo` SOLO cuando una columna de diciembre sigue
        INMEDIATAMENTE a la columna del `Mes_Corte` (Decisión 6); en otro caso se cae de
        vuelta al camino puro `Layout_Mensual`.

    Representación de 'mes' (Req 5.1/5.2): `Mes_Absoluto` en formato "YYYY-MM" derivado
    del `Encabezado_Fecha` de la columna DESTINO de cada salto. Como la variación es
    columna-contra-columna, solo se generan filas para las columnas con una columna
    cronológicamente anterior (todas menos la primera). Múltiples diciembres de años
    distintos producen claves de mes distintas ("2026-12" ≠ "2027-12", Req 5.3/9.4).

    Clasificación de saltos (Req 3.1-3.4): cada par de columnas consecutivas se clasifica
    por `_clasificar_saltos` en `'mensual'` (Δ=1 mes) o `'anual'` (Δ=12 meses); cualquier
    otro salto es `ErrorFormatoEncabezado`. La columna `tipo_salto` refleja esa
    clasificación por fila para que el orquestador separe los `Salto_Anual` (Req 8).

    Entrada NO confiable (allowlist / normalización):
      - Valida que existan las hojas 'Saldo Reservas' e 'Interés a la Reserva'
        (nombres esperados) ANTES de localizar columnas; ante hojas faltantes lanza
        `ErrorHojasFaltantes`; ante layout ilegible, `ErrorInputReservas` (mensaje
        genérico, sin volcar contenido del archivo).
      - Localiza en cada hoja la columna-etiqueta y las columnas de datos por
        `Encabezado_Fecha` (`_localizar_columnas_fecha`), ordenadas cronológicamente
        (Req 1.1-1.4). Encabezados con nombre de mes u otros formatos ⇒
        `ErrorFormatoEncabezado`.
      - Normaliza etiquetas de ramo (strip/upper, Req 13.2) y coerciona cada celda de
        saldo/interés a numérico FINITO (no numérico/vacío/inf → 0.0, Req 13.1).
      - Ignora las filas TOTAL de bloque y las cabeceras; usa TOTAL CAPI SOLO para el
        agregado Capitalizadora.

    Mapeo por DICCIONARIO explícito (Req 12.2), NO por presencia en el portafolio:
      - Cada etiqueta de saldo se mapea a un ramo de portafolio vía
        `mapeo_ramos_saldo_reservas`; destino "" ⇒ se ignora ese ramo; etiquetas que
        apuntan al mismo destino se SUMAN.
      - Cada etiqueta de interés se mapea vía `mapeo_ramos_interes_reservas` al mismo
        ramo de portafolio; el interés se resta del saldo del MISMO ramo destino.

    Derivación (columna destino vs. columna origen), por ramo de portafolio:
      - variación = (saldo_hasta − saldo_desde) − (interés_hasta − interés_desde)
        con interés 0 cuando el ramo no tiene línea de interés (reduciéndose a Δsaldo).
      - Capitalizadora (ramo único 'CAPITALIZADORA'):
        variación = (TOTAL_CAPI_hasta − TOTAL_CAPI_desde)
                    − (interés_capi_hasta − interés_capi_desde).

    Args:
        ruta_excel_o_bytes: ruta al workbook o su contenido en bytes (entrada no
            confiable proveniente del upload del dashboard).
        ramos_portafolio: parámetro conservado por compatibilidad de firma; la
            clasificación demás-ramos se resuelve por el diccionario de mapeo, por lo que
            este argumento se IGNORA.
        fecha_corte: `Fecha_Corte` OPCIONAL de la corrida (entrada NO confiable). `None`
            (por defecto) ⇒ camino `Layout_Mensual` actual, sin cambios observables. Una
            fecha válida activa la detección del `Tramo_Parcial` (Decisión 6); una fecha
            no coercible ⇒ `ErrorInputReservas` genérico (Req 20.3), sin volcar el valor.

    Returns:
        DataFrame con columnas EXACTAS {ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto}:
        un ramo de portafolio por destino del diccionario (incluidos los grupos "demás
        ramos" y 'CAPITALIZADORA'), por cada salto entre columnas consecutivas.

    Raises:
        ErrorHojasFaltantes: falta 'Saldo Reservas' o 'Interés a la Reserva'.
        ErrorFormatoEncabezado: encabezados no-fecha o salto de meses ∉ {1, 12}.
        ErrorInputReservas: layout no interpretable / workbook ilegible, o `fecha_corte`
            no coercible a una fecha válida (Req 20.3).
    """
    del ramos_portafolio  # ya no se usa: la agrupación es por diccionario de mapeo.

    from comun.rutas import DB_PATH as _DB_PATH_MAPEO

    # 0) Fecha_Corte inválida ⇒ error GENÉRICO ANTES de dimensionar tramos o derivar
    #    (Req 20.3). Se coerciona primero para no realizar ningún trabajo con una corrida
    #    mal parametrizada. `None` deja `mes_corte = None` (camino Layout_Mensual puro).
    mes_corte: "date | None" = None
    if fecha_corte is not None:
        mes_corte = _coercer_fecha_corte(fecha_corte)

    mapeo_saldo, mapeo_interes = cargar_mapeos_reservas(str(_DB_PATH_MAPEO))

    # 1) Validar hojas PRIMERO (Req 2.4): hojas faltantes ⇒ ErrorHojasFaltantes.
    _validar_hojas(ruta_excel_o_bytes)
    df_saldo = _leer_hoja_reservas(ruta_excel_o_bytes, _HOJA_SALDO)
    df_interes = _leer_hoja_reservas(ruta_excel_o_bytes, _HOJA_INTERES)

    # 2) Localizar columnas por Encabezado_Fecha y ORDENAR cronológicamente (Req 1.1-1.4).
    _fila_saldo, col_etq_saldo, columnas_saldo = _localizar_columnas_fecha(df_saldo)
    _fila_int, col_etq_int, columnas_int = _localizar_columnas_fecha(df_interes)

    # 3) Construir niveles por ramo INDEXADOS POR FECHA (Corrección 1). La hoja de saldo
    #    es el eje temporal MAESTRO; el interés se lee por su propia fecha. Con niveles
    #    keyed-by-date, el emparejamiento saldo↔interés se hace por la MISMA fecha (no por
    #    posición), lo que corrige el desfase cuando ambos ejes de fecha difieren.
    #    `_construir_saldos_por_ramo_fecha` aplica además la precedencia de la línea
    #    pre-agregada sobre los individuos del mismo destino (Corrección 2).
    saldos, saldo_total_capi = _construir_saldos_por_ramo_fecha(
        df_saldo, mapeo_saldo, columnas_saldo, col_etq_saldo
    )
    intereses, interes_capi = _construir_intereses_fecha(
        df_interes, mapeo_interes, columnas_int, col_etq_int
    )

    # 3.b) DECISIÓN DE DISPOSICIÓN (Decisión 6). El camino `Layout_Por_Tramo` se activa
    #      SOLO cuando hay `Fecha_Corte` válida Y una columna de diciembre sigue
    #      INMEDIATAMENTE a la columna del `Mes_Corte` (existe `Tramo_Parcial`). En
    #      cualquier otro caso se cae de vuelta al camino puro `Layout_Mensual` de abajo,
    #      preservando EXACTAMENTE el comportamiento actual (Req 14.2).
    if mes_corte is not None and _aplica_tramo_parcial(columnas_saldo, mes_corte):
        return _derivar_por_tramos(
            columnas_saldo=columnas_saldo,
            saldos=saldos,
            saldo_total_capi=saldo_total_capi,
            intereses=intereses,
            interes_capi=interes_capi,
            mes_corte=mes_corte,
        )

    # 4) Derivación `Layout_Mensual` (comportamiento actual, sin cambios observables).
    return _derivar_layout_mensual(
        columnas_saldo=columnas_saldo,
        saldos_por_ramo=saldos,
        saldo_total_capi=saldo_total_capi,
        intereses_por_ramo=intereses,
        interes_capi=interes_capi,
    )


# ==============================================================================
# PERSISTENCIA DE LA CLASIFICACIÓN DE RAMOS (dashboard → config_ramos_reserva)
# ==============================================================================

# Allowlist FIJA de columnas que se pueden escribir en config_ramos_reserva. Se rechaza
# cualquier clave/entrada fuera de este conjunto. Los nombres son literales controlados
# por el código; nunca provienen de entrada externa ni se interpolan en el SQL (se usan
# únicamente como texto de una sentencia parametrizada con placeholders '?').
_COLUMNAS_PERSIST_CONFIG_RAMOS: tuple[str, ...] = (
    "ramo",
    "es_reserva_matematica",
    "instrumento",
)


class ErrorPersistenciaClasificacion(Exception):
    """
    Error de dominio al persistir la clasificación de un ramo en `config_ramos_reserva`.

    Se lanza cuando, tras agotar los reintentos, la escritura sigue fallando por
    `sqlite3.OperationalError: database is locked` (u otro fallo de escritura). El
    callback del dashboard lo captura para mostrar un mensaje GENÉRICO al usuario; el
    detalle queda solo en el log estructurado, sin volcar datos sensibles.
    """


def persistir_clasificacion_ramo(
    ramo: str,
    es_reserva_matematica: bool,
    instrumento: "InstrumentoCompra | None",
    *,
    reintentos: int = 3,
) -> None:
    """
    Persiste (INSERT/UPSERT parametrizado) una fila en `config_ramos_reserva` tras
    asegurar el esquema (`asegurar_tabla_config_ramos`).

    Validaciones (entrada NO confiable; allowlist / normalización):
      - allowlist de columnas: solo se escriben `ramo`, `es_reserva_matematica` e
        `instrumento` (`_COLUMNAS_PERSIST_CONFIG_RAMOS`). No se aceptan claves extra: la
        firma es posicional/keyword fija, sin **kwargs, de modo que cualquier entrada
        inesperada es un TypeError en la llamada.
      - `ramo`: se normaliza (`strip`/`upper`) con `_normalizar_ramo`; si queda vacío se
        rechaza con `ValueError`.
      - `es_reserva_matematica`: se coerciona a `bool` y se almacena como 0/1.
      - `instrumento`: OBLIGATORIO y validado contra `InstrumentoCompra` SOLO cuando
        `es_reserva_matematica` es True (se almacena `instrumento.value`); cuando es False
        se almacena NULL (el instrumento no es significativo).

    Persistencia:
      - INSERT ... ON CONFLICT(ramo) DO UPDATE PARAMETRIZADO (placeholders '?'); jamás se
        interpola SQL con la entrada.
      - Reintenta ante `sqlite3.OperationalError: database is locked` con un backoff
        simple, hasta `reintentos` veces. Agotados los reintentos lanza
        `ErrorPersistenciaClasificacion` (mensaje genérico; el detalle queda en el log,
        sin volcar datos sensibles).
      - Usa `comun.db.get_conn()` y cierra siempre la conexión.

    Args:
        ramo: etiqueta del ramo/grupo a clasificar (se normaliza internamente).
        es_reserva_matematica: True si el ramo es de reserva matemática.
        instrumento: `InstrumentoCompra` requerido cuando `es_reserva_matematica` es True;
            se ignora (se guarda NULL) cuando es False.
        reintentos: número máximo de intentos ante `database is locked` (default 3).

    Raises:
        ValueError: si el `ramo` normalizado queda vacío, o si `es_reserva_matematica`
            es True e `instrumento` no es un `InstrumentoCompra` válido.
        ErrorPersistenciaClasificacion: si la escritura falla tras agotar los reintentos.
    """
    import time

    # --- Validación de entrada (allowlist / normalización) ---
    ramo_norm = _normalizar_ramo(ramo)
    if not ramo_norm:
        raise ValueError("El ramo a clasificar no puede estar vacío.")

    es_matematica = bool(es_reserva_matematica)

    if es_matematica:
        # El instrumento es obligatorio y debe ser un miembro válido del enum.
        if not isinstance(instrumento, InstrumentoCompra):
            raise ValueError(
                "Un ramo de reserva matemática requiere un instrumento válido "
                "(InstrumentoCompra)."
            )
        instrumento_val: str | None = instrumento.value
    else:
        # Sin reserva matemática, el instrumento no es significativo: se guarda NULL.
        instrumento_val = None

    # Parámetros en el orden EXACTO de los placeholders del SQL. La conversión a int
    # (0/1) mantiene el mismo formato de almacenamiento que asegurar_tabla_config_ramos.
    params: tuple[object, ...] = (ramo_norm, 1 if es_matematica else 0, instrumento_val)

    # UPSERT parametrizado: nunca se interpola entrada externa en el SQL.
    sentencia = (
        "INSERT INTO config_ramos_reserva "
        "(ramo, es_reserva_matematica, instrumento) VALUES (?, ?, ?) "
        "ON CONFLICT(ramo) DO UPDATE SET "
        "es_reserva_matematica = excluded.es_reserva_matematica, "
        "instrumento = excluded.instrumento"
    )

    intentos = max(1, int(reintentos))
    for intento in range(1, intentos + 1):
        conn = get_conn()
        try:
            asegurar_tabla_config_ramos(conn)
            conn.execute(sentencia, params)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            # Solo reintentar el caso conocido "database is locked"; otros errores
            # operacionales se elevan como fallo de persistencia genérico.
            if "database is locked" not in str(exc).lower():
                logger.warning(
                    "Fallo operacional no recuperable al persistir la clasificación "
                    "de un ramo."
                )
                raise ErrorPersistenciaClasificacion(
                    "No se pudo guardar la clasificación del ramo."
                ) from exc

            if intento >= intentos:
                logger.warning(
                    "No se pudo persistir la clasificación de un ramo: la base de "
                    "datos permaneció bloqueada tras %d intento(s).",
                    intentos,
                )
                raise ErrorPersistenciaClasificacion(
                    "No se pudo guardar la clasificación del ramo."
                ) from exc

            # Backoff simple y creciente antes del siguiente intento.
            logger.warning(
                "Base de datos bloqueada al persistir clasificación de ramo; "
                "reintentando (%d/%d).",
                intento,
                intentos,
            )
            time.sleep(0.1 * intento)
        finally:
            conn.close()


# ==============================================================================
# PERSISTENCIA DE PARÁMETROS DE PLAZO CONFIGURABLES (config_parametros_reservas, Req 13)
# ==============================================================================

class ErrorPersistenciaParametros(Exception):
    """
    Error de dominio al persistir los parámetros de plazo en `config_parametros_reservas`.

    Se lanza cuando, tras agotar los reintentos, la escritura sigue fallando por
    `sqlite3.OperationalError: database is locked` (u otro fallo operacional no
    recuperable). El panel avanzado del dashboard lo captura para mostrar un mensaje
    GENÉRICO al usuario; el detalle queda solo en el log estructurado, sin volcar datos
    sensibles. Es distinto y de nombre claro respecto a `ErrorPersistenciaClasificacion`
    (que corresponde a `config_ramos_reserva`).
    """


def persistir_parametros_reservas(
    nodo_compra_no_matematica_anios: float,
    plazo_max_venta_anios: float,
    *,
    reintentos: int = 3,
) -> None:
    """
    Persiste (UPSERT parametrizado) los dos parámetros de plazo configurables en
    `config_parametros_reservas` tras asegurar el esquema
    (`asegurar_tabla_parametros_reservas`).

    Nota sobre la política defaulting-vs-rechazo: el *defaulting* de valores ausentes
    o inválidos vive en la LECTURA (`cargar_parametros_reservas`, Req 13.6). La
    PERSISTENCIA es estricta: rechaza cualquier valor inválido con `ValueError` en vez
    de guardar silenciosamente un dato que luego habría que corregir al leer.

    Validaciones (entrada NO confiable; allowlist / normalización):
      - allowlist FIJA de claves: solo se escriben `nodo_compra_no_matematica_anios` y
        `plazo_max_venta_anios` (`_CLAVES_PARAMETROS_RESERVAS`). La firma es
        posicional/keyword fija (sin `**kwargs`), de modo que cualquier clave inesperada
        es un `TypeError` en la llamada; jamás se derivan nombres de columna/clave de
        entrada externa.
      - cada valor debe ser numérico, FINITO y ESTRICTAMENTE POSITIVO (> 0); en caso
        contrario se lanza `ValueError` (no se persiste un valor inválido).

    Persistencia:
      - INSERT ... ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor,
        PARAMETRIZADO (placeholders '?') para ambas claves; jamás se interpola SQL con la
        entrada.
      - Reintenta ante `sqlite3.OperationalError: database is locked` con un backoff
        simple, hasta `reintentos` veces. Agotados los reintentos lanza
        `ErrorPersistenciaParametros` (mensaje genérico; el detalle queda en el log, sin
        volcar valores).
      - Usa `comun.db.get_conn()` y cierra SIEMPRE la conexión en un `finally`.

    Args:
        nodo_compra_no_matematica_anios: nodo objetivo (años) de compra para ramos que
            NO son de reserva matemática. Debe ser numérico, finito y > 0.
        plazo_max_venta_anios: plazo máximo (años) elegible para vender en el waterfall.
            Debe ser numérico, finito y > 0.
        reintentos: número máximo de intentos ante `database is locked` (default 3).

    Raises:
        ValueError: si algún valor no es numérico, no es finito o no es estrictamente
            positivo.
        ErrorPersistenciaParametros: si la escritura falla tras agotar los reintentos.
    """
    import math
    import time

    # --- Validación de entrada estricta (allowlist FIJA de claves) ---
    # Mapa clave -> valor crudo, con las claves tomadas de la allowlist fija (no de
    # entrada externa). El orden es determinista para una escritura reproducible.
    valores_por_clave: dict[str, object] = {
        "nodo_compra_no_matematica_anios": nodo_compra_no_matematica_anios,
        "plazo_max_venta_anios": plazo_max_venta_anios,
    }

    valores_validados: dict[str, float] = {}
    for clave in _CLAVES_PARAMETROS_RESERVAS:
        crudo = valores_por_clave[clave]
        try:
            valor = float(crudo)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"El parámetro '{clave}' debe ser numérico."
            ) from exc
        if not math.isfinite(valor) or valor <= 0.0:
            raise ValueError(
                f"El parámetro '{clave}' debe ser finito y estrictamente positivo."
            )
        valores_validados[clave] = valor

    # UPSERT parametrizado: nunca se interpola entrada externa en el SQL. Se ejecuta la
    # misma sentencia para cada clave de la allowlist.
    sentencia = (
        "INSERT INTO config_parametros_reservas (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor"
    )
    # Lista de tuplas (clave, valor) en el orden de la allowlist fija.
    filas_params: list[tuple[str, float]] = [
        (clave, valores_validados[clave]) for clave in _CLAVES_PARAMETROS_RESERVAS
    ]

    intentos = max(1, int(reintentos))
    for intento in range(1, intentos + 1):
        conn = get_conn()
        try:
            asegurar_tabla_parametros_reservas(conn)
            # Ambas claves se escriben en la misma transacción para dejar el par de
            # parámetros en un estado consistente.
            conn.executemany(sentencia, filas_params)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            # Solo reintentar el caso conocido "database is locked"; otros errores
            # operacionales se elevan como fallo de persistencia genérico.
            if "database is locked" not in str(exc).lower():
                logger.warning(
                    "Fallo operacional no recuperable al persistir los parámetros de "
                    "reservas."
                )
                raise ErrorPersistenciaParametros(
                    "No se pudieron guardar los parámetros de reservas."
                ) from exc

            if intento >= intentos:
                logger.warning(
                    "No se pudieron persistir los parámetros de reservas: la base de "
                    "datos permaneció bloqueada tras %d intento(s).",
                    intentos,
                )
                raise ErrorPersistenciaParametros(
                    "No se pudieron guardar los parámetros de reservas."
                ) from exc

            # Backoff simple y creciente antes del siguiente intento.
            logger.warning(
                "Base de datos bloqueada al persistir parámetros de reservas; "
                "reintentando (%d/%d).",
                intento,
                intentos,
            )
            time.sleep(0.1 * intento)
        finally:
            conn.close()


# ==============================================================================
# PERSISTENCIA DE LA VARIACIÓN DERIVADA DE RESERVAS (input_variacion_reservas, Req 1)
# ==============================================================================

# Allowlist FIJA de columnas de entrada REQUERIDAS por persistir_input_reservas. El
# DataFrame que produce `derivar_variaciones_reservas` trae además `tipo_salto`
# ('mensual'/'anual'); esa columna es OPCIONAL: si viene se persiste, si falta se asume
# 'mensual'. Cualquier columna fuera de la unión {ramo, mes, delta_reserva, tipo_salto}
# se rechaza. Nombres literales controlados por el código; nunca se derivan de entrada
# externa ni se interpolan en el SQL.
_COLUMNAS_INPUT_RESERVAS: tuple[str, ...] = ("ramo", "mes", "delta_reserva")

# Columna opcional adicional que emite el lector para separar Salto_Anual (Req 8). Si
# está presente se persiste; si no, se asume 'mensual'.
_COLUMNA_TIPO_SALTO = "tipo_salto"
_TIPOS_SALTO_VALIDOS: frozenset[str] = frozenset({"mensual", "anual"})
_TIPO_SALTO_POR_DEFECTO = "mensual"

# Nombre de la tabla de versiones (fuente de verdad de la persistencia versionada,
# design.md Decisión 1). Literal controlado por el código; nunca proviene de entrada
# externa ni se interpola con datos del archivo.
_TABLA_VERSIONES_RESERVAS = "input_variacion_reservas_versiones"


class ErrorPersistenciaReservas(Exception):
    """
    Error de dominio al persistir la variación derivada de reservas en
    `input_variacion_reservas`.

    Se lanza cuando, tras agotar los reintentos, la escritura sigue fallando por
    `sqlite3.OperationalError: database is locked` (u otro fallo operacional no
    recuperable). El callback del dashboard lo captura para mostrar un mensaje GENÉRICO
    al usuario; el detalle queda solo en el log estructurado, sin volcar datos
    sensibles. Es distinto y de nombre claro respecto a `ErrorPersistenciaClasificacion`
    (config_ramos_reserva) y `ErrorPersistenciaParametros` (config_parametros_reservas).
    """


def _asegurar_tabla_input_reservas(conn: sqlite3.Connection) -> None:
    """
    Crea `input_variacion_reservas` de forma IDEMPOTENTE (`CREATE TABLE IF NOT EXISTS`)
    si no existe.

    La tabla la crea normalmente el bootstrap `init_db.py`, pero este helper la asegura
    de forma defensiva desde la capa de aplicación para que la persistencia funcione
    aunque `init_db.py` no se haya ejecutado. El DDL replica EXACTAMENTE el esquema de
    `init_db.py`: columnas `(ramo, mes, delta_reserva, fecha_carga)` con
    `PRIMARY KEY (ramo, mes)`. Es seguro llamarlo varias veces: no elimina la tabla, no
    borra filas y no siembra datos.

    Args:
        conn: conexión sqlite3 abierta. El llamador gestiona su ciclo de vida; esta
            función solo emite el DDL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS input_variacion_reservas (
            ramo TEXT NOT NULL,
            mes TEXT NOT NULL,
            delta_reserva REAL NOT NULL,
            fecha_carga TEXT,
            PRIMARY KEY (ramo, mes)
        )
        """
    )
    conn.commit()


def _asegurar_tabla_input_reservas_versiones(conn: sqlite3.Connection) -> None:
    """
    Crea la tabla VERSIONADA `input_variacion_reservas_versiones` y su índice de
    forma IDEMPOTENTE (`CREATE TABLE/INDEX IF NOT EXISTS`), design.md Decisión 1.

    Esta tabla es la FUENTE DE VERDAD de la persistencia de la variación de reservas
    (Req 6): a diferencia de la histórica `input_variacion_reservas` (PK `(ramo, mes)`,
    que impediría múltiples versiones de la misma `(ramo, mes)`), su clave primaria
    `(version_id, ramo, mes)` permite conservar TODAS las cargas anteriores sin
    sobrescribirlas.

    Columnas:
        version_id    INTEGER NOT NULL  -- identificador de la Versión_Carga
        ramo          TEXT    NOT NULL
        mes           TEXT    NOT NULL  -- Mes_Absoluto 'YYYY-MM'
        delta_reserva REAL    NOT NULL
        tipo_salto    TEXT    NOT NULL  -- 'mensual' | 'anual'
        fecha_carga   TEXT    NOT NULL  -- ISO-8601, precisión de segundos
        PRIMARY KEY (version_id, ramo, mes)

    El índice `ix_ivrv_carga` sobre `fecha_carga` acelera la selección de la versión
    más reciente (Req 6.3, 6.4). Es seguro llamarlo varias veces: no elimina la tabla,
    no borra filas y no siembra datos.

    Args:
        conn: conexión sqlite3 abierta. El llamador gestiona su ciclo de vida; esta
            función solo emite el DDL.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS input_variacion_reservas_versiones (
            version_id    INTEGER NOT NULL,
            ramo          TEXT    NOT NULL,
            mes           TEXT    NOT NULL,
            delta_reserva REAL    NOT NULL,
            tipo_salto    TEXT    NOT NULL,
            fecha_carga   TEXT    NOT NULL,
            PRIMARY KEY (version_id, ramo, mes)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ivrv_carga
            ON input_variacion_reservas_versiones (fecha_carga)
        """
    )
    conn.commit()


def _normalizar_mes_reserva(valor: object) -> str:
    """
    Valida y normaliza el 'mes' de una fila de reservas (entrada NO confiable) como
    `Mes_Absoluto`, devolviéndolo SIEMPRE como TEXT en formato ESTRICTO "YYYY-MM".

    Per design.md Decisión 2 y la Property 9, la persistencia versionada exige el mes
    como fecha calendario ABSOLUTA "YYYY-MM":
      - El año debe tener EXACTAMENTE cuatro dígitos.
      - El mes debe estar entre 01 y 12.

    Cualquier otro valor —incluidos los antiguos offsets ENTEROS (p. ej. 7, '7', -1),
    valores vacíos, nulos, booleanos, no numéricos, o cadenas que no cumplan el patrón
    estricto "YYYY-MM"— provoca `ValueError` (Req 5.4). No se acepta ni normaliza un
    offset relativo: eliminar la ambigüedad del offset es el propósito de esta versión.

    El mensaje de error es GENÉRICO y no vuelca el contenido de la fila (Req 13.4).
    """
    import re

    # Caso booleano: bool es subtipo de int; se rechaza explícitamente por ambiguo.
    if isinstance(valor, bool):
        raise ValueError(
            "El 'mes' de la variación de reserva debe tener formato 'YYYY-MM'."
        )

    if valor is None:
        raise ValueError(
            "El 'mes' de la variación de reserva debe tener formato 'YYYY-MM'."
        )

    texto = str(valor).strip()
    if not texto:
        raise ValueError(
            "El 'mes' de la variación de reserva debe tener formato 'YYYY-MM'."
        )

    # Cadena en formato ESTRICTO 'YYYY-MM': año de 4 dígitos, mes 01..12.
    coincidencia = re.fullmatch(r"(\d{4})-(\d{2})", texto)
    if coincidencia is not None:
        mes_num = int(coincidencia.group(2))
        if 1 <= mes_num <= 12:
            return texto

    raise ValueError(
        "El 'mes' de la variación de reserva debe tener formato 'YYYY-MM' "
        "(año de 4 dígitos, mes 01-12)."
    )


def _normalizar_tipo_salto(valor: object) -> str:
    """
    Normaliza el `tipo_salto` de una fila (entrada NO confiable) a 'mensual'/'anual'.

    Acepta las cadenas 'mensual'/'anual' (insensible a espacios y mayúsculas). Cualquier
    otro valor —ausente/None, vacío o no reconocido— se resuelve como 'mensual' (el
    salto por defecto, design.md Persistidor_Reservas: "tipo_salto opcional"). No aborta
    la carga por un tipo desconocido, pero nunca persiste un valor fuera de la allowlist.
    """
    if valor is None:
        return _TIPO_SALTO_POR_DEFECTO
    texto = str(valor).strip().lower()
    if texto in _TIPOS_SALTO_VALIDOS:
        return texto
    return _TIPO_SALTO_POR_DEFECTO


def persistir_input_reservas(df: "pd.DataFrame", *, reintentos: int = 3) -> int:
    """
    Valida y persiste la variación DERIVADA de reservas de forma VERSIONADA en
    `input_variacion_reservas_versiones`, PRESERVANDO las cargas anteriores (Req 6).

    Recibe el `DataFrame` YA producido por `derivar_variaciones_reservas`
    (`{ramo, mes ('YYYY-MM'), delta_reserva, tipo_salto}`); esta función NO deriva nada.
    A diferencia de la versión histórica (destructiva: `DELETE` + `INSERT OR REPLACE`
    sobre `input_variacion_reservas`), aquí cada carga crea una `Versión_Carga` NUEVA
    con su propio `version_id` y `fecha_carga`, sin borrar versiones previas
    (design.md Decisión 1; Req 6.1, 6.2).

    Entrada NO confiable (allowlist / normalización / rangos):
      - Columnas REQUERIDAS: {'ramo', 'mes', 'delta_reserva'}. La columna 'tipo_salto'
        es OPCIONAL (si viene se persiste; si falta se asume 'mensual'). Cualquier
        columna fuera de esa unión provoca `ValueError` (se rechazan claves inesperadas).
      - `ramo`: se normaliza con `_normalizar_ramo` (strip/upper); vacío ⇒ `ValueError`.
      - `mes`: se valida/normaliza con `_normalizar_mes_reserva` EXIGIENDO 'YYYY-MM'
        (año de 4 dígitos, mes 01-12); otros valores ⇒ `ValueError` (Req 5.1, 5.4).
      - `delta_reserva`: las filas con delta NO FINITO (NaN/inf/no numérico) se
        DESCARTAN individualmente registrando una advertencia GENÉRICA (sin volcar el
        contenido de la fila, Req 13.4); NO abortan la carga.
      - `tipo_salto`: se normaliza a 'mensual'/'anual' (`_normalizar_tipo_salto`).
      - Filas con `(ramo, mes)` duplicado tras normalizar ⇒ `ValueError` (la clave
        primaria `(version_id, ramo, mes)` debe ser única dentro de la versión).

    Persistencia versionada:
      - Asegura el esquema de forma idempotente (`_asegurar_tabla_input_reservas_versiones`,
        `CREATE TABLE/INDEX IF NOT EXISTS`).
      - Dentro de la MISMA transacción: calcula `version_id = MAX(version_id) + 1`
        (1 si la tabla está vacía) y un `fecha_carga` ISO-8601 con precisión de segundos,
        e inserta las filas de esta versión. NO borra ninguna versión anterior.
      - Todo PARAMETRIZADO con placeholders '?'; jamás se interpola SQL con la entrada
        (Req 13.3).
      - Reintenta ante `sqlite3.OperationalError: database is locked` con backoff simple,
        hasta `reintentos` veces. Agotados los reintentos lanza `ErrorPersistenciaReservas`
        (mensaje genérico; el detalle queda en el log, sin volcar datos sensibles).
      - Usa `comun.db.get_conn()` y cierra SIEMPRE la conexión en un `finally`.

    Args:
        df: DataFrame con columnas requeridas {ramo, mes, delta_reserva} y opcional
            {tipo_salto}, producido por `derivar_variaciones_reservas`.
        reintentos: número máximo de intentos ante `database is locked` (default 3).

    Returns:
        int: número de filas efectivamente persistidas en esta versión (tras descartar
            filas con delta no finito). Usado por el dashboard (Req 11.5).

    Raises:
        ValueError: si las columnas no coinciden con la allowlist, o si alguna fila tiene
            ramo vacío, mes no 'YYYY-MM', o clave (ramo, mes) duplicada.
        ErrorPersistenciaReservas: si la escritura falla tras agotar los reintentos.
    """
    import math
    import time
    from datetime import datetime, timezone

    # --- Validación de allowlist de columnas (rechazar extra; exigir requeridas) ---
    columnas_recibidas = set(df.columns)
    columnas_permitidas = set(_COLUMNAS_INPUT_RESERVAS) | {_COLUMNA_TIPO_SALTO}
    faltan_requeridas = set(_COLUMNAS_INPUT_RESERVAS) - columnas_recibidas
    columnas_inesperadas = columnas_recibidas - columnas_permitidas
    if faltan_requeridas or columnas_inesperadas:
        # Mensaje genérico: no se vuelca el contenido, solo la forma esperada.
        raise ValueError(
            "El DataFrame de reservas debe tener las columnas requeridas "
            "{'ramo', 'mes', 'delta_reserva'} y opcionalmente 'tipo_salto'."
        )

    tiene_tipo_salto = _COLUMNA_TIPO_SALTO in columnas_recibidas
    tipos_crudos = (
        df[_COLUMNA_TIPO_SALTO].tolist()
        if tiene_tipo_salto
        else [None] * len(df.index)
    )

    # --- Validación/normalización fila a fila (entrada NO confiable) ---
    filas_validadas: list[tuple[str, str, float, str]] = []
    claves_vistas: set[tuple[str, str]] = set()
    descartadas_delta = 0
    # Se recorre por posición usando la allowlist para no depender del orden de columnas.
    for ramo_crudo, mes_crudo, delta_crudo, tipo_crudo in zip(
        df["ramo"].tolist(),
        df["mes"].tolist(),
        df["delta_reserva"].tolist(),
        tipos_crudos,
    ):
        ramo_norm = _normalizar_ramo(ramo_crudo) if ramo_crudo is not None else ""
        if not ramo_norm:
            raise ValueError(
                "Una fila de la variación de reservas tiene el ramo vacío."
            )

        # Exige 'YYYY-MM' estricto (Req 5.4); un mes mal formado aborta con ValueError.
        mes_norm = _normalizar_mes_reserva(mes_crudo)

        # Filas con delta no finito/no numérico se DESCARTAN (no abortan, Req 13.4).
        try:
            delta = float(delta_crudo)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            descartadas_delta += 1
            continue
        if not math.isfinite(delta):
            descartadas_delta += 1
            continue

        clave = (ramo_norm, mes_norm)
        if clave in claves_vistas:
            raise ValueError(
                "La variación de reservas tiene una clave (ramo, mes) duplicada."
            )
        claves_vistas.add(clave)

        tipo_norm = _normalizar_tipo_salto(tipo_crudo)
        filas_validadas.append((ramo_norm, mes_norm, delta, tipo_norm))

    if descartadas_delta:
        # Advertencia GENÉRICA (recuento agregado), sin volcar contenido de fila.
        logger.warning(
            "Se descartaron %d fila(s) de la variación de reservas con "
            "delta_reserva no finito.",
            descartadas_delta,
        )

    intentos = max(1, int(reintentos))
    for intento in range(1, intentos + 1):
        conn = get_conn()
        try:
            _asegurar_tabla_input_reservas_versiones(conn)
            # Timestamp de carga común a todas las filas de esta versión (sin PII).
            fecha_carga = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # Transacción explícita: asignar version_id y persistir SIN borrar versiones
            # anteriores (versionado no destructivo, Req 6.1, 6.2).
            with conn:
                fila_max = conn.execute(
                    "SELECT MAX(version_id) "
                    "FROM input_variacion_reservas_versiones"
                ).fetchone()
                version_actual = (fila_max[0] if fila_max is not None else None) or 0
                nuevo_version_id = int(version_actual) + 1

                if filas_validadas:
                    # INSERT parametrizado (nunca se interpola entrada externa en el SQL).
                    filas_insert = [
                        (nuevo_version_id, ramo, mes, delta, tipo, fecha_carga)
                        for (ramo, mes, delta, tipo) in filas_validadas
                    ]
                    conn.executemany(
                        "INSERT INTO input_variacion_reservas_versiones "
                        "(version_id, ramo, mes, delta_reserva, tipo_salto, "
                        "fecha_carga) VALUES (?, ?, ?, ?, ?, ?)",
                        filas_insert,
                    )
            return len(filas_validadas)
        except sqlite3.OperationalError as exc:
            # Solo reintentar el caso conocido "database is locked"; otros errores
            # operacionales se elevan como fallo de persistencia genérico.
            if "database is locked" not in str(exc).lower():
                logger.warning(
                    "Fallo operacional no recuperable al persistir la variación de "
                    "reservas."
                )
                raise ErrorPersistenciaReservas(
                    "No se pudo guardar la variación de reservas."
                ) from exc

            if intento >= intentos:
                logger.warning(
                    "No se pudo persistir la variación de reservas: la base de datos "
                    "permaneció bloqueada tras %d intento(s).",
                    intentos,
                )
                raise ErrorPersistenciaReservas(
                    "No se pudo guardar la variación de reservas."
                ) from exc

            # Backoff simple y creciente antes del siguiente intento.
            logger.warning(
                "Base de datos bloqueada al persistir variación de reservas; "
                "reintentando (%d/%d).",
                intento,
                intentos,
            )
            time.sleep(0.1 * intento)
        finally:
            conn.close()

    # Inalcanzable en la práctica: el bucle retorna o eleva en todos los caminos.
    raise ErrorPersistenciaReservas(
        "No se pudo guardar la variación de reservas."
    )


# ==============================================================================
# CARGA DEL MAPA DE RESERVAS PARA EL MOTOR
# (lectura de la versión más reciente de input_variacion_reservas_versiones,
#  anclaje Mes_Absoluto -> offset con Mes_Caído; Req 6.3, 7.1-7.3, 10.1)
# ==============================================================================

def _mes_inicio_desde_fecha(fecha_inicio: object) -> "date":
    """
    Normaliza la `Fecha_Inicio_Proyeccion` (entrada NO confiable) al primer día de su
    mes calendario. Acepta `date`/`datetime`/`pd.Timestamp` o texto/otros parseables a
    fecha; devuelve `date(anio, mes, 1)`.

    Un valor no parseable a fecha provoca `ValueError` con mensaje GENÉRICO (no vuelca
    contenido). El anclaje del `Mes_Absoluto` a la fecha de inicio requiere un mes de
    inicio bien definido (design.md "Anclaje Mes_Absoluto → offset de proyección").
    """
    from datetime import date as _date, datetime as _datetime

    valor = fecha_inicio
    if isinstance(valor, _datetime):
        return _date(valor.year, valor.month, 1)
    if isinstance(valor, _date):
        return _date(valor.year, valor.month, 1)

    convertido = pd.to_datetime(valor, errors="coerce")
    if convertido is None or pd.isna(convertido):
        raise ValueError(
            "La fecha de inicio de la proyección no es una fecha válida."
        )
    return _date(int(convertido.year), int(convertido.month), 1)


def cargar_mapa_reservas(
    ruta_db: str,
    fecha_inicio: object,
    meses_horizonte: int,
) -> dict[str, dict[int, float]]:
    """
    Lee la `Versión_Carga` MÁS RECIENTE de `input_variacion_reservas_versiones` (la
    FUENTE DE VERDAD versionada, Req 6) y devuelve `{ramo: {mes_offset: delta_reserva}}`
    anclando el `Mes_Absoluto` a la `Fecha_Inicio_Proyeccion` con la convención
    `Mes_Caído` (Req 6.3, 7.1, 7.2, 7.3, 10.1, 12.4).

    El 'mes' persistido es un `Mes_Absoluto` "YYYY-MM" (NO un offset entero). Para cada
    fila `(ramo, mes="YYYY-MM", delta)` se ancla ese mes calendario `M` a `fecha_inicio`
    aplicando `Mes_Caído`: el delta del mes calendario `M` se asigna al offset de
    proyección `t` tal que el mes de proyección `t-1 == M`. En offsets
    (design.md "Anclaje Mes_Absoluto → offset de proyección"):

        offset_proyeccion(M) = meses_entre(mes(fecha_inicio), M) + 1

    Selección de versión (Req 6.3):
      - Se toma la versión con `MAX(fecha_carga)`, desempatando por `MAX(version_id)`.

    Comportamiento defensivo:
      - Si la tabla `input_variacion_reservas_versiones` no existe o no tiene versiones,
        devuelve `{}` (sin input, el motor no opera compras/ventas). No crea la tabla ni
        aborta la corrida.
      - Query de solo lectura PARAMETRIZADA; nunca se interpola SQL. Cada fila se trata
        como entrada NO confiable: se normaliza el ramo, se valida el mes como "YYYY-MM"
        y se coerciona el delta a float. Las filas cuyo 'mes' no es "YYYY-MM" o cuyo
        delta no es finito se descartan con un log de advertencia genérico (sin volcar
        datos).
      - Solo se conservan los offsets dentro de `[1, meses_horizonte]`; el resto se omite
        (el motor asume Δ=0, Req 7.3, 10.1). Ramos/meses sin fila NO se incluyen.
      - Usa `comun.db.get_conn()` y cierra SIEMPRE la conexión.

    Args:
        ruta_db: ruta a la base de datos. Nota: `get_conn()` abre la base principal del
            proyecto (`comun.rutas.DB_PATH`); el parámetro se conserva por consistencia
            con las demás funciones del módulo (diseño/pruebas).
        fecha_inicio: `Fecha_Inicio_Proyeccion` (mes de proyección `t=1`). Se usa para
            anclar cada `Mes_Absoluto` al offset de proyección correspondiente.
        meses_horizonte: horizonte de la proyección (número de meses). Solo se conservan
            los offsets en `[1, meses_horizonte]`.

    Returns:
        `{ramo: {mes_offset: delta_reserva}}` construido solo con filas válidas cuyo
        offset cae en `[1, meses_horizonte]`; `{}` si no hay versión disponible.
    """
    import math

    mapa: dict[str, dict[int, float]] = {}

    horizonte = int(meses_horizonte)
    if horizonte < 1:
        # Sin horizonte válido no hay offsets que conservar: mapa vacío (no aborta).
        return {}

    # El anclaje exige un mes de inicio bien definido; si la fecha es inválida se eleva
    # ValueError (la corrida no debe proceder con un ancla ambigua).
    mes_inicio = _mes_inicio_desde_fecha(fecha_inicio)

    conn = get_conn()
    try:
        # ¿Existe la tabla versionada? Si no, mapa vacío (no abortar, no crear).
        existe = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='input_variacion_reservas_versiones'"
        ).fetchone()
        if not existe:
            return {}

        # Versión MÁS RECIENTE por MAX(fecha_carga), desempate por MAX(version_id).
        fila_version = conn.execute(
            "SELECT version_id FROM input_variacion_reservas_versiones "
            "ORDER BY fecha_carga DESC, version_id DESC LIMIT 1"
        ).fetchone()
        if fila_version is None or fila_version[0] is None:
            return {}
        version_id = fila_version[0]

        # Consulta de solo lectura PARAMETRIZADA (el version_id nunca se interpola).
        filas = conn.execute(
            "SELECT ramo, mes, delta_reserva "
            "FROM input_variacion_reservas_versiones "
            "WHERE version_id = ?",
            (version_id,),
        ).fetchall()
    finally:
        conn.close()

    for fila in filas:
        ramo_crudo, mes_crudo, delta_crudo = fila[0], fila[1], fila[2]

        ramo_norm = _normalizar_ramo(ramo_crudo) if ramo_crudo is not None else ""
        if not ramo_norm:
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila con ramo "
                "vacío."
            )
            continue

        # El mes persistido es un Mes_Absoluto "YYYY-MM"; se valida de forma estricta.
        try:
            mes_absoluto = _normalizar_mes_reserva(mes_crudo)
        except ValueError:
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila con 'mes' "
                "que no cumple el formato 'YYYY-MM'."
            )
            continue

        try:
            delta = float(delta_crudo)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila con delta no "
                "numérico."
            )
            continue
        if not math.isfinite(delta):
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila con delta no "
                "finito."
            )
            continue

        # Anclaje Mes_Absoluto -> offset de proyección con Mes_Caído (design.md):
        #   offset = meses_entre(mes(fecha_inicio), M) + 1
        anio_mes, mes_mes = int(mes_absoluto[:4]), int(mes_absoluto[5:7])
        meses_entre = (anio_mes - mes_inicio.year) * 12 + (mes_mes - mes_inicio.month)
        offset = meses_entre + 1

        # Solo offsets dentro del horizonte de proyección; el resto se omite (Δ=0).
        if offset < 1 or offset > horizonte:
            continue

        mapa.setdefault(ramo_norm, {})[offset] = delta

    return mapa


def cargar_variacion_anual_por_ano(ruta_db: str) -> dict[int, dict[str, float]]:
    """
    Lee la `Versión_Carga` MÁS RECIENTE de `input_variacion_reservas_versiones` y
    devuelve, SOLO para los `Salto_Anual` (`tipo_salto == 'anual'`), la variación anual
    agrupada por el año del diciembre destino:

        {ano_diciembre: {ramo: delta_anual}}

    Usado por el `Orquestador` para construir `variacion_anual_por_tramo` (Req 8.1, 8.2).
    Para los saltos anuales el `Mes_Absoluto` es siempre un diciembre ("YYYY-12"); el
    `ano_diciembre` es el año de ese "YYYY-MM".

    Selección de versión (Req 6.3) — consistente con `cargar_mapa_reservas`:
      - Se toma la versión con `MAX(fecha_carga)`, desempatando por `MAX(version_id)`.

    Comportamiento defensivo:
      - Si la tabla `input_variacion_reservas_versiones` no existe o no tiene versiones,
        devuelve `{}` (sin input; no crea la tabla ni aborta la corrida).
      - Consulta de solo lectura PARAMETRIZADA; nunca se interpola SQL. Cada fila se
        trata como entrada NO confiable: se normaliza el ramo, se valida el mes como
        "YYYY-MM" y se coerciona el delta a float finito. Las filas cuyo 'mes' no es
        "YYYY-MM" o cuyo delta no es finito se descartan con un log de advertencia
        genérico (sin volcar datos).
      - Solo se consideran las filas con `tipo_salto == 'anual'` (Req 8.1); las
        mensuales se ignoran.
      - Usa `comun.db.get_conn()` y cierra SIEMPRE la conexión.

    Args:
        ruta_db: ruta a la base de datos. Nota: `get_conn()` abre la base principal del
            proyecto (`comun.rutas.DB_PATH`); el parámetro se conserva por consistencia
            con las demás funciones del módulo (diseño/pruebas).

    Returns:
        `{ano_diciembre: {ramo: delta_anual}}` construido solo con filas anuales
        válidas; `{}` si no hay versión disponible.
    """
    import math

    resultado: dict[int, dict[str, float]] = {}

    conn = get_conn()
    try:
        # ¿Existe la tabla versionada? Si no, mapa vacío (no abortar, no crear).
        existe = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='input_variacion_reservas_versiones'"
        ).fetchone()
        if not existe:
            return {}

        # Versión MÁS RECIENTE por MAX(fecha_carga), desempate por MAX(version_id).
        # Selección idéntica a `cargar_mapa_reservas` (Req 6.3).
        fila_version = conn.execute(
            "SELECT version_id FROM input_variacion_reservas_versiones "
            "ORDER BY fecha_carga DESC, version_id DESC LIMIT 1"
        ).fetchone()
        if fila_version is None or fila_version[0] is None:
            return {}
        version_id = fila_version[0]

        # Consulta de solo lectura PARAMETRIZADA (el version_id nunca se interpola).
        filas = conn.execute(
            "SELECT ramo, mes, delta_reserva, tipo_salto "
            "FROM input_variacion_reservas_versiones "
            "WHERE version_id = ?",
            (version_id,),
        ).fetchall()
    finally:
        conn.close()

    for fila in filas:
        ramo_crudo, mes_crudo, delta_crudo, tipo_crudo = (
            fila[0],
            fila[1],
            fila[2],
            fila[3],
        )

        # Solo saltos ANUALES (Req 8.1); los mensuales se ignoran.
        if _normalizar_tipo_salto(tipo_crudo) != "anual":
            continue

        ramo_norm = _normalizar_ramo(ramo_crudo) if ramo_crudo is not None else ""
        if not ramo_norm:
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila anual con "
                "ramo vacío."
            )
            continue

        # El mes persistido es un Mes_Absoluto "YYYY-MM"; se valida de forma estricta.
        try:
            mes_absoluto = _normalizar_mes_reserva(mes_crudo)
        except ValueError:
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila anual con "
                "'mes' que no cumple el formato 'YYYY-MM'."
            )
            continue

        try:
            delta = float(delta_crudo)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila anual con "
                "delta no numérico."
            )
            continue
        if not math.isfinite(delta):
            logger.warning(
                "input_variacion_reservas_versiones: se descarta una fila anual con "
                "delta no finito."
            )
            continue

        # El ano_diciembre es el año del Mes_Absoluto (para saltos anuales, "YYYY-12").
        ano_diciembre = int(mes_absoluto[:4])
        resultado.setdefault(ano_diciembre, {})[ramo_norm] = delta

    return resultado


# ==============================================================================
# RAMOS PENDIENTES POR CLASIFICAR (Tarea 4.1, Req 3.1, 4.1)
# ==============================================================================

def ramos_pendientes_por_clasificar(
    ramos_portafolio: set[str],
    config_ramos: ConfigRamosReserva,
) -> list[str]:
    """
    Calcula los ramos del portafolio que aún NO han sido clasificados en
    `config_ramos_reserva`.

    Pendientes = ramos DISTINTOS del portafolio (normalizados con `_normalizar_ramo`)
    MENOS los ramos que ya tienen fila en la tabla de clasificación (es decir, aquellos
    para los que `config_ramos.esta_clasificado(ramo)` es True). Un ramo ya clasificado
    (sea reserva matemática o no) NUNCA aparece en la lista de pendientes; al persistir
    su clasificación desaparece de esta lista.

    Entrada NO confiable: cada etiqueta de `ramos_portafolio` se normaliza (strip/upper)
    antes de comparar, de modo que variaciones de mayúsculas/espacios no producen
    duplicados ni "falsos pendientes". Los valores vacíos (tras normalizar) se descartan.

    Args:
        ramos_portafolio: conjunto de ramos del portafolio inicial de la corrida (o de
            los grupos derivados a mostrar en la pestaña *Ajustes*).
        config_ramos: clasificación cargada desde `config_ramos_reserva`.

    Returns:
        Lista ordenada (orden estable, alfabético ascendente) y SIN duplicados de los
        ramos normalizados que carecen de fila en `config_ramos_reserva`.
    """
    # Deduplicar y normalizar el universo de ramos del portafolio; descartar vacíos.
    pendientes: set[str] = set()
    for ramo in ramos_portafolio:
        ramo_norm = _normalizar_ramo(ramo)
        if not ramo_norm:
            continue
        # Un ramo ya clasificado (tiene fila en la tabla) no es pendiente.
        if not config_ramos.esta_clasificado(ramo_norm):
            pendientes.add(ramo_norm)

    # Orden estable y determinista (alfabético ascendente).
    return sorted(pendientes)


# ==============================================================================
# SELECTORES DE TÍTULO DE COMPRA (Tarea 7.2, Req 3.1, 3.2, 4.1, 4.2, 13.7)
# ==============================================================================

# Número de días por año usado para expresar el plazo al vencimiento en años. Es una
# convención de conteo simple (act/365) suficiente para comparar plazos entre títulos;
# no busca precisión de day-count financiero.
_DIAS_POR_ANIO: float = 365.0


def _a_timestamp(valor: object) -> "pd.Timestamp | None":
    """
    Convierte un valor (entrada NO confiable) a `pd.Timestamp`, o devuelve None si no es
    parseable.

    `fecha_vencimiento` puede venir como string ('YYYY-MM-DD' u otros formatos), como
    date/datetime, o como Timestamp. Se usa `pd.to_datetime(errors='coerce')`, que
    devuelve `NaT` ante valores no parseables; ese caso se normaliza a None para que el
    llamador lo descarte sin abortar.
    """
    ts = pd.to_datetime(valor, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts


def _plazo_en_anios(fecha_vencimiento: object, fecha_mes: object) -> float | None:
    """
    Calcula el plazo al vencimiento en años como (fecha_vencimiento - fecha_mes) en días
    dividido por `_DIAS_POR_ANIO`. Devuelve None si alguna fecha no es parseable.

    El plazo puede ser negativo (título ya vencido a `fecha_mes`); el filtrado de
    vencidos, si aplica, es responsabilidad del llamador. Aquí solo se computa el plazo.
    """
    ts_venc = _a_timestamp(fecha_vencimiento)
    ts_mes = _a_timestamp(fecha_mes)
    if ts_venc is None or ts_mes is None:
        return None
    return (ts_venc - ts_mes).days / _DIAS_POR_ANIO


def _candidatos_con_plazo(
    df: "pd.DataFrame", fecha_mes: object
) -> list[tuple[float, dict]]:
    """
    A partir de un DataFrame del universo, produce una lista de tuplas
    `(plazo_en_anios, fila_como_dict)` para las filas con `fecha_vencimiento` parseable.

    Las filas cuya `fecha_vencimiento` no es parseable se descartan silenciosamente (no
    pueden compararse por plazo). Devuelve lista vacía si el DataFrame es None/está vacío
    o no tiene la columna `fecha_vencimiento`.
    """
    if df is None or getattr(df, "empty", True):
        return []
    if "fecha_vencimiento" not in df.columns:
        return []

    candidatos: list[tuple[float, dict]] = []
    for fila in df.to_dict(orient="records"):
        plazo = _plazo_en_anios(fila.get("fecha_vencimiento"), fecha_mes)
        if plazo is None:
            continue
        candidatos.append((plazo, fila))
    return candidatos


def _seleccionar_mayor_plazo(
    df: "pd.DataFrame", fecha_mes: object
) -> dict:
    """
    Devuelve la fila (como dict) con el MAYOR plazo al vencimiento a `fecha_mes`.

    Universo vacío / sin filas con vencimiento parseable ⇒ dict vacío `{}`: el
    orquestador/llamador decide cómo tratar la ausencia de universo (se documenta la
    elección de devolver `{}` en las funciones públicas). No lanza excepción por universo
    vacío para que los consumidores operen sin ramas especiales.
    """
    candidatos = _candidatos_con_plazo(df, fecha_mes)
    if not candidatos:
        return {}
    # max por plazo; ante empate, `max` conserva el primero encontrado (estable).
    _, fila = max(candidatos, key=lambda par: par[0])
    return fila


def seleccionar_tes_uvr_largo(universo: UniversoCompra, fecha_mes: object) -> dict:
    """
    Selecciona el TES UVR (TUVT) de MAYOR plazo al vencimiento a `fecha_mes` (Property 6).

    Se usa para ramos de reserva matemática cuyo instrumento es TES UVR: se compra el de
    plazo más largo disponible en el universo.

    Manejo de universo vacío: si `universo.tes_uvr` está vacío o ninguna fila tiene una
    `fecha_vencimiento` parseable, devuelve `{}` (el llamador maneja la ausencia de
    universo, típicamente omitiendo la compra ese mes). Req 3.1, 3.2.

    Args:
        universo: universo de compra precargado (`cargar_universo_compra`).
        fecha_mes: fecha del mes de la proyección (date/datetime/str/Timestamp) desde la
            que se mide el plazo al vencimiento.

    Returns:
        La fila del TES UVR de mayor plazo como dict, o `{}` si no hay candidatos.
    """
    return _seleccionar_mayor_plazo(universo.tes_uvr, fecha_mes)


def seleccionar_tf_largo(universo: UniversoCompra, fecha_mes: object) -> dict:
    """
    Selecciona el TF (Tasa Fija) de MAYOR plazo al vencimiento a `fecha_mes` (Property 6).

    Se usa para el caso especial de reserva matemática que compra TF (p. ej. Vida
    Individual-Matemática): se compra el TF de plazo más largo disponible.

    Manejo de universo vacío: si `universo.tf` está vacío o ninguna fila tiene una
    `fecha_vencimiento` parseable, devuelve `{}` (el llamador maneja la ausencia de
    universo). Req 3.1, 3.2.

    Args:
        universo: universo de compra precargado (`cargar_universo_compra`).
        fecha_mes: fecha del mes de la proyección desde la que se mide el plazo.

    Returns:
        La fila del TF de mayor plazo como dict, o `{}` si no hay candidatos.
    """
    return _seleccionar_mayor_plazo(universo.tf, fecha_mes)


def seleccionar_tes_uvr_5y(
    universo: UniversoCompra, fecha_mes: object, nodo_anios: float
) -> dict:
    """
    Selecciona el TES UVR (TUVT) cuyo plazo al vencimiento (en años desde `fecha_mes`)
    coincide con el nodo objetivo configurable `nodo_anios`; si no hay un plazo
    exactamente igual al nodo, elige el de plazo MAYOR más cercano al nodo (preferencia
    por el lado largo: nunca uno de plazo menor si existe alguno de plazo mayor cercano al
    nodo). Property 7, Req 4.2, 13.7.

    El nombre conserva el sufijo '5y' por retrocompatibilidad, pero el nodo NO es un
    literal: proviene de `parametros.nodo_compra_no_matematica_anios` (por defecto 5
    años). Con `nodo_anios = 5` la selección coincide con el comportamiento fijo previo.

    Regla de selección (sobre los plazos disponibles):
      1. Si existe un plazo exactamente igual al nodo, se elige ese título.
      2. Si existen plazos MAYORES o iguales al nodo, se elige el MENOR de esos plazos
         (el más cercano al nodo por el lado largo).
      3. Si todos los plazos son MENORES al nodo, se elige el MAYOR de ellos (el más
         cercano al nodo por abajo, único lado disponible).

    Manejo de universo vacío: si `universo.tes_uvr` está vacío o ninguna fila tiene
    `fecha_vencimiento` parseable, devuelve `{}` (el llamador maneja la ausencia de
    universo). Req 3.1, 3.2.

    Args:
        universo: universo de compra precargado (`cargar_universo_compra`).
        fecha_mes: fecha del mes de la proyección desde la que se mide el plazo.
        nodo_anios: nodo objetivo en años (configurable; por defecto 5).

    Returns:
        La fila del TES UVR seleccionada como dict, o `{}` si no hay candidatos.
    """
    candidatos = _candidatos_con_plazo(universo.tes_uvr, fecha_mes)
    if not candidatos:
        return {}

    # 1) Coincidencia exacta con el nodo (dentro de una tolerancia numérica pequeña para
    #    evitar problemas de punto flotante al comparar plazos derivados de días/365).
    tolerancia = 0.5 / _DIAS_POR_ANIO  # ~medio día expresado en años
    exactos = [par for par in candidatos if abs(par[0] - nodo_anios) <= tolerancia]
    if exactos:
        # Ante varios "exactos", se elige el de mayor plazo (preferencia lado largo).
        _, fila = max(exactos, key=lambda par: par[0])
        return fila

    # 2) Preferir el lado largo: plazos > nodo, elegir el MENOR de ellos (más cercano al
    #    nodo por arriba).
    mayores = [par for par in candidatos if par[0] > nodo_anios]
    if mayores:
        _, fila = min(mayores, key=lambda par: par[0])
        return fila

    # 3) No hay ninguno de plazo mayor: elegir el MAYOR de los menores (más cercano al
    #    nodo por abajo).
    _, fila = max(candidatos, key=lambda par: par[0])
    return fila


# ==============================================================================
# LÓGICA DE COMPRA: comprar_por_reserva (Req 3, 4)
# ==============================================================================

# asset_class asignado a la posición nueva según el instrumento comprado. Son literales
# FIJOS controlados por el código (no provienen de entrada externa). 'DP_TUVT' es la
# clase de deuda pública en UVR y 'DP_TF' la de deuda pública en tasa fija; ambas son
# las curvas que `motor_proyeccion.obtener_nombre_curva`/`valorar_titulo` esperan para
# valorar TUVT y TFIT respectivamente.
_ASSET_CLASS_TES_UVR = "DP_TUVT"
_ASSET_CLASS_TF = "DP_TF"

# tipo_bono del `Titulo` según instrumento (allowlist del enum de motor_proyeccion:
# TF, UVR, IPC, YANKEE). TES UVR (TUVT) => 'UVR'; TF (TFIT) => 'TF'.
_TIPO_BONO_TES_UVR = "UVR"
_TIPO_BONO_TF = "TF"

# Clasificaciones contables asignadas a la posición nueva (Req 3.1, 3.2). Se usan las
# etiquetas legibles que el diseño especifica para las compras por reserva.
_CLASIFICACION_RESERVA_MATEMATICA = "Disp. Venta"   # DV
_CLASIFICACION_DEMAS_RAMOS = "Negociable"

# Valores por defecto seguros cuando el universo/valoración no aportan datos (Req 4.6).
# Se usan para no abortar la creación de la posición: la tasa por defecto solo sirve
# para poblar tasa_compra/tasa_mercado; el valor_mercado se fuerza a `monto` de todas
# formas (invariante Δreserva == valor_mercado). No es un dato de mercado real, solo un
# marcador conservador para que la posición quede creada.
_TASA_DEFECTO_COMPRA: float = 0.0
# Frecuencia por defecto si la fila del universo no la trae; 'semestral' es la más común
# en TUVT/TFIT y es un valor válido para `valorar_titulo`.
_FRECUENCIA_DEFECTO = "semestral"


def _a_float_seguro(valor: object, defecto: float = 0.0) -> float:
    """
    Coerciona un valor (entrada NO confiable, proveniente de una fila del universo) a
    float finito. Devuelve `defecto` ante None, valores no numéricos o NaN/inf. No
    registra el dato para no filtrar contenido no confiable en logs.
    """
    import math

    if valor is None:
        return defecto
    try:
        num = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return defecto
    if not math.isfinite(num):
        return defecto
    return num


def _texto_seguro(valor: object, defecto: str = "") -> str:
    """
    Normaliza un valor de una fila del universo (entrada NO confiable) a str con strip.
    Devuelve `defecto` si el valor es None o queda vacío tras el strip.
    """
    if valor is None:
        return defecto
    texto = str(valor).strip()
    return texto if texto else defecto


def _frecuencia_desde_universo(valor: object) -> str:
    """
    Interpreta la frecuencia de pago de cupón de una fila del universo. `hist_pip` suele
    almacenar la frecuencia como número de pagos al año (1, 2, 4, 12) o como etiqueta de
    texto. Se normaliza a las etiquetas que entiende `valorar_titulo` ('anual',
    'semestral', 'trimestral', 'mensual'); ante valor ausente/no reconocido se usa el
    valor por defecto seguro (`_FRECUENCIA_DEFECTO`).
    """
    if valor is None:
        return _FRECUENCIA_DEFECTO

    # Caso numérico: pagos por año.
    try:
        num = int(float(valor))  # type: ignore[arg-type]
        mapa_num = {1: "anual", 2: "semestral", 4: "trimestral", 12: "mensual"}
        if num in mapa_num:
            return mapa_num[num]
    except (TypeError, ValueError):
        pass

    # Caso texto: normalizar contra las etiquetas conocidas.
    texto = str(valor).strip().lower()
    etiquetas = {"anual", "semestral", "trimestral", "mensual"}
    if texto in etiquetas:
        return texto
    return _FRECUENCIA_DEFECTO


def _importar_motor_proyeccion():
    """
    Importa perezosamente `Titulo` y `valorar_titulo` desde `scripts/motor_proyeccion.py`.

    Este módulo vive en `scripts/`, por lo que se garantiza que el directorio propio esté
    en `sys.path` antes de importar por nombre de módulo de nivel superior
    (`motor_proyeccion`). Se hace de forma perezosa (dentro de la función) para evitar un
    import circular en tiempo de carga: `motor_proyeccion` importa de este módulo.

    Returns:
        (Titulo, valorar_titulo)
    """
    import sys as _sys
    from pathlib import Path as _Path

    _dir_scripts = str(_Path(__file__).resolve().parent)
    if _dir_scripts not in _sys.path:
        _sys.path.insert(0, _dir_scripts)

    from motor_proyeccion import Titulo, valorar_titulo  # import perezoso, ver docstring

    return Titulo, valorar_titulo


def _id_posicion_compra(ramo: str, fecha_mes: object, nemo: str) -> str:
    """
    Construye un id_posicion único y estable para una compra por reserva, con el prefijo
    'CVR_' (Compras/Ventas por Reserva). Normaliza ramo, fecha y nemo (strip/upper y
    reemplazo de espacios) para obtener una clave legible y sin espacios.
    """
    ramo_norm = _normalizar_ramo(ramo).replace(" ", "_")
    nemo_norm = _normalizar_ramo(nemo).replace(" ", "_") if nemo else "SININSTRUMENTO"
    ts = _a_timestamp(fecha_mes)
    fecha_norm = ts.strftime("%Y%m%d") if ts is not None else str(fecha_mes)
    return f"CVR_{ramo_norm}_{fecha_norm}_{nemo_norm}"


def comprar_por_reserva(
    ramo: str,
    monto: float,
    fecha_mes,
    universo_compra: UniversoCompra,
    config_ramos: ConfigRamosReserva,
    parametros: ParametrosReservas,
    *,
    compania: str = "",
    portafolio: str = "",
    sendas=None,
    valor_uvr=None,
    trm=None,
    serie_ipc=None,
    senda_inflacion=None,
    fecha_inicio_senda=None,
):
    """
    Crea una POSICIÓN NUEVA de renta fija para ejecutar una compra dirigida por el
    AUMENTO de reserva de un ramo (Req 3, 4). Se invoca SOLO para ramos YA CLASIFICADOS
    (el orquestador —tarea 12.1— omite los no clasificados), de modo que
    `config_ramos.es_reserva_matematica(ramo)` nunca es None aquí.

    Selección por DATO (no por literales), según el diseño ("Selección del título a
    comprar"):
      - es_reserva_matematica(ramo) es True  ⇒ reserva matemática:
          * clasificación 'Disp. Venta' (DV);
          * instrumento según config_ramos.instrumento(ramo):
              - InstrumentoCompra.TES_UVR → TES UVR (TUVT) de MAYOR plazo
                (`seleccionar_tes_uvr_largo`);
              - InstrumentoCompra.TF      → TF de MAYOR plazo (`seleccionar_tf_largo`).
      - es_reserva_matematica(ramo) es False ⇒ demás ramos:
          * clasificación 'Negociable';
          * TES UVR en el nodo configurable
            `parametros.nodo_compra_no_matematica_anios` (por defecto 5 años) vía
            `seleccionar_tes_uvr_5y` (Req 13.7, NO literal 5).

    La posición nueva:
      - Tiene un id_posicion único ('CVR_<ramo>_<YYYYMMDD>_<nemo>'), es_reinversion=False.
      - Hereda ramo (normalizado), compañía y portafolio (los pasa el orquestador).
      - Se valora a `fecha_mes` con `valorar_titulo`; el nominal se deriva del precio a
        fecha_mes de modo que `valor_mercado == monto` (Δreserva positiva). Es decir,
        nominal = monto / (precio_pct/100), donde precio_pct es el precio porcentual
        (valoración de nominal=100 → VM/100 · 100). valor_tir se inicializa también a
        `monto`.
      - Poblada con los campos de seguridad de la fila del universo (nemo→especie,
        codigo_isin→isin, fecha_vencimiento, cupon→tasa_cupon,
        frecuencia_pago_cupon→frecuencia, moneda, tipo_bono/asset_class por instrumento).

    Fallback de datos de mercado (Req 4.6): si el selector devuelve {} (no hay TUVT/TF
    elegible) o si `valorar_titulo` falla / faltan datos de mercado, NO se aborta: la
    posición se crea igualmente con `valor_mercado == monto` usando una valoración segura
    por defecto (nominal aproximado = monto cuando no hay precio). Solo se devuelve None
    si genuinamente NO hay instrumento que comprar (universo vacío para el instrumento
    del ramo); en ese caso se registra una advertencia genérica. El orquestador decide
    cómo tratar ese None (típicamente omitir la compra ese mes).

    Args:
        ramo: ramo objetivo (se normaliza para almacenarlo).
        monto: Δreserva positiva a invertir (== valor_mercado objetivo de la posición).
        fecha_mes: fecha del mes de la proyección (valoración y plazo se miden desde aquí).
        universo_compra: universo precargado (`cargar_universo_compra`).
        config_ramos: clasificación de ramos (por dato).
        parametros: parámetros de plazo configurables (nodo de compra, Req 13).
        compania, portafolio: metadatos del ramo (los pasa el orquestador).
        sendas, valor_uvr, trm, serie_ipc, senda_inflacion, fecha_inicio_senda: contexto
            de mercado del mes para `valorar_titulo` (opcionales; hay defaults seguros).

    Returns:
        El `Titulo` de la posición nueva, o None si no hay instrumento comprable.
    """
    ramo_norm = _normalizar_ramo(ramo)

    # ── 1. Resolver clasificación e instrumento POR DATO ──
    es_matematica = config_ramos.es_reserva_matematica(ramo_norm)
    # Contrato de la tarea: solo se invoca para ramos clasificados; None no debería
    # ocurrir. Por robustez (defensa en profundidad) se trata None como "no comprar".
    if es_matematica is None:
        logger.warning(
            "comprar_por_reserva invocada para un ramo sin clasificar; se omite la compra."
        )
        return None

    if es_matematica:
        clasificacion = _CLASIFICACION_RESERVA_MATEMATICA
        instrumento = config_ramos.instrumento(ramo_norm)
        if instrumento is InstrumentoCompra.TF:
            fila = seleccionar_tf_largo(universo_compra, fecha_mes)
            tipo_bono = _TIPO_BONO_TF
            asset_class = _ASSET_CLASS_TF
        else:
            # InstrumentoCompra.TES_UVR (default del enum) → TUVT de mayor plazo.
            fila = seleccionar_tes_uvr_largo(universo_compra, fecha_mes)
            tipo_bono = _TIPO_BONO_TES_UVR
            asset_class = _ASSET_CLASS_TES_UVR
    else:
        clasificacion = _CLASIFICACION_DEMAS_RAMOS
        fila = seleccionar_tes_uvr_5y(
            universo_compra, fecha_mes, parametros.nodo_compra_no_matematica_anios
        )
        tipo_bono = _TIPO_BONO_TES_UVR
        asset_class = _ASSET_CLASS_TES_UVR

    # ── 2. Sin instrumento comprable → no hay compra posible (Req 4.6) ──
    if not fila:
        logger.warning(
            "comprar_por_reserva: sin instrumento elegible en el universo; se omite la compra."
        )
        return None

    # ── 3. Poblar campos de seguridad desde la fila del universo (entrada NO confiable) ──
    nemo = _texto_seguro(fila.get("nemo"))
    isin = _texto_seguro(fila.get("codigo_isin"))
    moneda = _texto_seguro(fila.get("moneda"), defecto="UVR" if tipo_bono == _TIPO_BONO_TES_UVR else "COP")
    tasa_cupon = _a_float_seguro(fila.get("cupon"), defecto=0.0)
    frecuencia = _frecuencia_desde_universo(fila.get("frecuencia_pago_cupon"))
    # La tasa de mercado (rendimiento) se usa para valorar y para poblar tasa_mercado.
    tasa_mercado = _a_float_seguro(fila.get("rendimiento"), defecto=_TASA_DEFECTO_COMPRA)

    ts_venc = _a_timestamp(fila.get("fecha_vencimiento"))
    ts_mes = _a_timestamp(fecha_mes)
    fecha_vencimiento = ts_venc.date() if ts_venc is not None else None
    # fecha_emision se aproxima a la fecha del mes (compra en el mes). Si no se puede
    # parsear la fecha del mes, se usa la misma fecha de vencimiento como marcador.
    fecha_emision = ts_mes.date() if ts_mes is not None else fecha_vencimiento

    # ── 4. Importar Titulo/valorar_titulo perezosamente y construir la posición ──
    Titulo, valorar_titulo = _importar_motor_proyeccion()

    id_posicion = _id_posicion_compra(ramo_norm, fecha_mes, nemo)

    # Contexto de mercado con defaults seguros (valorar_titulo ya tiene sus propios
    # defaults, pero solo pasamos los kwargs cuando el llamador los provee para no pisar
    # esos defaults con None).
    kwargs_valoracion: dict[str, object] = {}
    if valor_uvr is not None:
        kwargs_valoracion["valor_uvr"] = valor_uvr
    if trm is not None:
        kwargs_valoracion["trm"] = trm
    if serie_ipc is not None:
        kwargs_valoracion["serie_ipc"] = serie_ipc
    if senda_inflacion is not None:
        kwargs_valoracion["senda_inflacion"] = senda_inflacion
    if fecha_inicio_senda is not None:
        kwargs_valoracion["fecha_inicio_senda"] = fecha_inicio_senda

    # Construimos primero un título "sonda" con nominal=100 para obtener el precio
    # porcentual a fecha_mes (valorar_titulo devuelve VM = nominal · precio_pct/100).
    # Con nominal=100, VM_sonda = precio_pct; de ahí derivamos el nominal que hace que
    # valor_mercado == monto (Req 3.4, 3.5, 3.6).
    titulo = Titulo(
        id_posicion=id_posicion,
        especie=nemo,
        emisor="",
        isin=isin,
        moneda=moneda,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        nominal=100.0,
        tasa_cupon=tasa_cupon,
        spread_ipc=0.0,
        tipo_bono=tipo_bono,
        frecuencia=frecuencia,
        tipo_tasa="EFECTIVO",
        base_anual=_DIAS_POR_ANIO,
        uso_tasa="I",
        clasificacion=clasificacion,
        compania=str(compania).strip(),
        ramo=ramo_norm,
        portafolio=str(portafolio).strip(),
        asset_class=asset_class,
        tasa_compra=tasa_mercado,
        tasa_mercado=tasa_mercado,
        valor_mercado=float(monto),
        valor_tir=float(monto),
        es_reinversion=False,
        vm_compra=float(monto),
    )

    # ── 5. Derivar nominal para que valor_mercado == monto (Req 4.6: fallback seguro) ──
    # Si faltan fechas para valorar, no se puede obtener precio: fallback nominal≈monto.
    nominal_final = float(monto)
    if fecha_vencimiento is not None and fecha_emision is not None and ts_mes is not None:
        try:
            vm_sonda = valorar_titulo(
                titulo, tasa_mercado, ts_mes.date(), **kwargs_valoracion
            )
        except Exception:  # noqa: BLE001 - datos de mercado no confiables; no abortar
            logger.warning(
                "comprar_por_reserva: falló la valoración del título; se usa nominal por defecto."
            )
            vm_sonda = 0.0

        # vm_sonda == precio_pct (porque nominal_sonda == 100). Si es positivo y finito,
        # nominal = monto / (precio_pct/100) = monto * 100 / vm_sonda.
        import math as _math

        if _math.isfinite(vm_sonda) and vm_sonda > 0.0:
            nominal_final = float(monto) * 100.0 / vm_sonda
        else:
            logger.warning(
                "comprar_por_reserva: valoración no positiva; se usa nominal por defecto (VM=monto)."
            )
    else:
        logger.warning(
            "comprar_por_reserva: sin fechas para valorar; se usa nominal por defecto (VM=monto)."
        )

    # ── 6. Fijar nominal final; asegurar el invariante valor_mercado == monto ──
    # Con el nominal derivado, la revaloración debería dar exactamente `monto`. Aun así,
    # forzamos valor_mercado/valor_tir a `monto` para garantizar el invariante Δreserva
    # positiva == valor_mercado, incluso ante el fallback (Req 3.4, 3.5, 3.6, 4.6).
    from dataclasses import replace as _replace

    titulo = _replace(
        titulo,
        nominal=nominal_final,
        valor_mercado=float(monto),
        valor_tir=float(monto),
        vm_compra=float(monto),
    )
    return titulo


# ==============================================================================
# TAREA 10.1 — WATERFALL DE VENTAS: pasos y selección de títulos a vender
# ==============================================================================
#
# Solo la RF entra al waterfall (el llamador — el motor — pasa títulos RF del ramo).
# La clasificación contable de cada Titulo se lee de su atributo `clasificacion`, que
# el motor puede exponer como etiqueta legible ('Negociable', 'Disp. Venta',
# 'Vencimiento') o como código ('1CP', '1DV', '1VC'). Aquí se aceptan AMBAS formas
# (entrada NO confiable: se normaliza y se compara contra allowlists fijas).


class PasoWaterfall(Enum):
    """
    Pasos del waterfall de ventas cuando la reserva de un ramo disminuye (Req 5.1).

    El orden ESTRICTO de prelación es el valor entero de cada miembro:
      1. MONTHLY_LIQUIDITY: liquidez de RF del mes (vencimientos + amortizaciones +
         pagos de cupón), retenida solo hasta cubrir la disminución (retención parcial).
      2. NEGOCIABLES: venta de Negociables RF con plazo ≤ plazo_max_venta_anios.
      3. DV: venta de DV RF con plazo ≤ plazo_max_venta_anios.
    """

    MONTHLY_LIQUIDITY = 1
    NEGOCIABLES = 2
    DV = 3


# Allowlists FIJAS de clasificaciones contables reconocidas (etiqueta legible y código).
# Son literales controlados por el código; la entrada (el atributo `clasificacion` de un
# Titulo) se normaliza (strip/upper) y se compara contra estos conjuntos, nunca al revés.
_CLASIFICACIONES_NEGOCIABLE = frozenset({"NEGOCIABLE", "1CP"})
_CLASIFICACIONES_DV = frozenset({"DISP. VENTA", "DISP VENTA", "DISPONIBLE PARA LA VENTA", "1DV"})
_CLASIFICACIONES_VENCIMIENTO = frozenset({"VENCIMIENTO", "1VC"})


def _clasificacion_normalizada(titulo: object) -> str:
    """
    Devuelve la clasificación contable de un `Titulo` normalizada (strip/upper).

    `titulo` se trata por duck typing: se lee su atributo `clasificacion` sin asumir un
    tipo concreto (evita acoplar el módulo al import de `Titulo`). Un valor ausente/None
    se normaliza a cadena vacía.
    """
    valor = getattr(titulo, "clasificacion", None)
    if valor is None:
        return ""
    return str(valor).strip().upper()


def es_negociable(titulo: object) -> bool:
    """True si el título está clasificado como Negociable ('Negociable' / '1CP')."""
    return _clasificacion_normalizada(titulo) in _CLASIFICACIONES_NEGOCIABLE


def es_dv(titulo: object) -> bool:
    """True si el título está clasificado como DV ('Disp. Venta' / '1DV')."""
    return _clasificacion_normalizada(titulo) in _CLASIFICACIONES_DV


def es_vencimiento(titulo: object) -> bool:
    """True si el título está clasificado como Vencimiento ('Vencimiento' / '1VC')."""
    return _clasificacion_normalizada(titulo) in _CLASIFICACIONES_VENCIMIENTO


def _valor_mercado_seguro(titulo: object) -> float:
    """
    Lee `valor_mercado` de un `Titulo` (duck typing) como float finito. Un valor
    ausente/None/no numérico/no finito se trata como 0.0 (conservador: un título sin VM
    válido no aporta liquidez ni puede acercarse a un monto pendiente).
    """
    import math as _math

    try:
        vm = float(getattr(titulo, "valor_mercado", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return vm if _math.isfinite(vm) else 0.0


def _valor_tir_seguro(titulo: object) -> float:
    """
    Lee `valor_tir` de un `Titulo` (duck typing) como float finito, con fallback 0.0.
    Se usa para el ORI aproximado `valor_mercado − valor_tir` de los DV (Req 8, Glosario).
    """
    import math as _math

    try:
        vtir = float(getattr(titulo, "valor_tir", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return vtir if _math.isfinite(vtir) else 0.0


def _plazo_titulo_en_anios(titulo: object, fecha_venta: object) -> float | None:
    """
    Plazo al vencimiento (años) de un `Titulo` a `fecha_venta`, leyendo su atributo
    `fecha_vencimiento` (date/datetime/Timestamp/str) por duck typing. Reutiliza el
    helper `_plazo_en_anios`. Devuelve None si la fecha no es parseable.
    """
    return _plazo_en_anios(getattr(titulo, "fecha_vencimiento", None), fecha_venta)


def _es_elegible_por_plazo(
    titulo: object, plazo_max_venta_anios: float, fecha_venta: object
) -> bool:
    """
    True si el título es elegible para venta por plazo: tiene `fecha_vencimiento`
    parseable y su plazo al vencimiento a `fecha_venta` es ≤ `plazo_max_venta_anios`
    (Req 5.4, 5.6). Un título sin fecha parseable NO es elegible (se descarta sin abortar).
    """
    plazo = _plazo_titulo_en_anios(titulo, fecha_venta)
    if plazo is None:
        return False
    return plazo <= float(plazo_max_venta_anios)


def seleccionar_titulo_a_vender(
    candidatos: list,
    monto_pendiente: float,
    plazo_max_venta_anios: float,
    fecha_venta,
):
    """
    Elige, entre los candidatos ELEGIBLES por plazo, el título cuyo `valor_mercado` se
    acerque más al `monto_pendiente` (minimiza `|valor_mercado − monto_pendiente|`),
    Req 6.1, 6.3.

    Elegibilidad (Req 5.4/5.6, 13.7): un candidato es elegible si su plazo al vencimiento
    a `fecha_venta` es ≤ `plazo_max_venta_anios` (parámetro configurable de
    `ParametrosReservas.plazo_max_venta_anios`, NO un literal 10). Los candidatos sin
    `fecha_vencimiento` parseable se descartan.

    Args:
        candidatos: lista de `Titulo` (RF del ramo) del paso actual (Negociables o DV).
        monto_pendiente: monto aún por cubrir en el waterfall.
        plazo_max_venta_anios: plazo máximo elegible (años), configurable.
        fecha_venta: fecha del mes de venta, para medir el plazo al vencimiento.

    Returns:
        El `Titulo` elegible más cercano al monto pendiente, o None si no hay ninguno
        elegible. Ante empate en la distancia se conserva el primero encontrado (estable).
    """
    mejor = None
    mejor_distancia = None
    for titulo in candidatos:
        if not _es_elegible_por_plazo(titulo, plazo_max_venta_anios, fecha_venta):
            continue
        distancia = abs(_valor_mercado_seguro(titulo) - float(monto_pendiente))
        if mejor_distancia is None or distancia < mejor_distancia:
            mejor = titulo
            mejor_distancia = distancia
    return mejor


def ordenar_dv_por_ori(candidatos_dv: list, fecha_venta) -> list:
    """
    Ordena los candidatos DV por ORI acumulado (`valor_mercado − valor_tir`) DESCENDENTE
    (Req 7.1): primero la mayor ganancia y, cuando ninguno gana, la menor pérdida (la
    regla descendente única cubre ambos casos, sin fallback por plazo).

    Excluye del universo los DV con 0 días al vencimiento, es decir ya vencidos o que
    vencen hoy (`fecha_vencimiento <= fecha_venta`, plazo ≤ 0), Req 7.2, 7.3. Los
    candidatos con `fecha_vencimiento` no parseable también se excluyen (no puede
    determinarse que tengan días al vencimiento).

    Args:
        candidatos_dv: lista de `Titulo` DV del ramo.
        fecha_venta: fecha del mes de venta, para medir días al vencimiento.

    Returns:
        Nueva lista ordenada por ORI descendente (no muta la lista de entrada). El orden
        es estable ante empates de ORI (conserva el orden relativo original).
    """
    elegibles = []
    for titulo in candidatos_dv:
        plazo = _plazo_titulo_en_anios(titulo, fecha_venta)
        # Excluir sin fecha parseable (None) y con 0 días o menos al vencimiento.
        if plazo is None or plazo <= 0.0:
            continue
        elegibles.append(titulo)

    # sorted es estable: ante empate de ORI conserva el orden de aparición. Se ordena por
    # -ORI para obtener descendente (mayor ganancia / menor pérdida primero).
    return sorted(
        elegibles,
        key=lambda t: _valor_mercado_seguro(t) - _valor_tir_seguro(t),
        reverse=True,
    )


# ==============================================================================
# TAREA 10.2 — vender_por_reserva: waterfall estricto de generación de liquidez
# ==============================================================================


@dataclass
class ResultadoVenta:
    """
    Resultado del waterfall de ventas de un ramo cuando su reserva disminuye (Req 5-8, 11).

    Campos:
      - liquidez_consumida: monto de LIQUIDEZ DEL MES retenido (no reinvertido) en el paso
        MONTHLY_LIQUIDITY, igual a min(liquidez_mes_ramo, monto_a_cubrir) — RETENCIÓN
        PARCIAL (Req 5.9). El excedente de liquidez del mes por encima de la disminución NO
        se bloquea. Coincide con la semántica de `liquidez_consumida` del motor (Property 18
        del diseño): el producto de las ventas de títulos se refleja implícitamente en
        `titulos_vendidos`, no aquí.
      - titulos_vendidos: lista de `Titulo` (Negociables y/o DV) retirados del portafolio.
      - ori_reciclado: suma de (valor_mercado − valor_tir) sobre los DV vendidos, a sumar
        al P&L realizado (Req 8.1, 8.2). Los Negociables contribuyen 0 (Req 8.3).
      - cupon_no_reinvertido: porción de la liquidez retenida en MONTHLY_LIQUIDITY que
        corresponde a pagos de cupón de RF (acotada a min(cupon_mes_ramo,
        liquidez_consumida)), reportada para que el motor la sume al P&L (mercado y TIR)
        sin doble conteo, preservando el reconocimiento del cupón (Req 5.8, 11.2).
    """

    liquidez_consumida: float
    titulos_vendidos: list
    ori_reciclado: float
    cupon_no_reinvertido: float


def vender_por_reserva(
    ramo: str,
    monto_a_cubrir: float,
    portafolio_ramo: list,
    liquidez_mes_ramo: float,
    fecha_mes,
    parametros: "ParametrosReservas",
    *,
    cupon_mes_ramo: float = 0.0,
) -> ResultadoVenta:
    """
    Genera la liquidez requerida para cubrir la disminución de reserva de un ramo, en el
    orden ESTRICTO del waterfall (Req 5.1):

        1) MONTHLY_LIQUIDITY  → 2) NEGOCIABLES  → 3) DV

    El plazo máximo elegible de venta se toma de `parametros.plazo_max_venta_anios`
    (configurable, por defecto 10 años, Req 13.7), NO de un literal 10.

    Pasos:
      1. MONTHLY_LIQUIDITY (Req 5.2, 5.9, 5.10): se RETIENE (no se reinvierte) solo
         `min(liquidez_mes_ramo, monto_a_cubrir)` — retención parcial; el excedente de la
         liquidez del mes no se bloquea. No hay orden de prelación dentro del paso: la
         liquidez del mes es un monto agregado. La porción de cupón no reinvertido se
         determina por monto/proporción como `min(cupon_mes_ramo, liquidez_consumida)`.
      2. NEGOCIABLES (Req 5.3, 5.4, 5.7): si el paso 1 no cubre, se venden Negociables RF
         con plazo ≤ plazo_max, eligiendo repetidamente con `seleccionar_titulo_a_vender`
         el título más cercano al pendiente. Cada venta retira el título y suma su
         `valor_mercado` a la liquidez generada. Se detiene apenas la liquidez acumulada
         cubre `monto_a_cubrir`.
      3. DV (Req 5.5, 5.6, 7, 8): si aún no se cubre, se venden DV RF con plazo ≤ plazo_max,
         ordenados por `ordenar_dv_por_ori` (mayor ORI primero). Si el requerido excede lo
         disponible, se vende exactamente lo disponible (Req 5.6). Se acumula
         `ori_reciclado = Σ(vm − vtir)` sobre los DV vendidos.

    Se venden títulos ENTEROS (Req 6.2). Retirar un título vendido y sumar su
    `valor_mercado` a la liquidez conserva la liquidez del ramo (Req 6.4). Los Negociables
    aportan 0 al ORI reciclado (Req 8.3).

    Mutación: esta función NO muta la lista `portafolio_ramo` del llamador. Opera sobre una
    COPIA superficial interna y devuelve los títulos vendidos en `ResultadoVenta`; es
    responsabilidad del llamador retirarlos del portafolio (o usar la lista de vendidos).

    Args:
        ramo: etiqueta del ramo (informativa; no altera la lógica de selección).
        monto_a_cubrir: monto de la disminución de reserva a cubrir (se toma su valor
            absoluto; valores ≤ 0 producen un resultado vacío).
        portafolio_ramo: títulos RF del ramo candidatos a venta (el llamador filtra a RF).
        liquidez_mes_ramo: liquidez de RF del mes del ramo (vencimientos + amortizaciones +
            cupones). Valores negativos/no finitos se tratan como 0.
        fecha_mes: fecha del mes de venta, para medir el plazo al vencimiento.
        parametros: `ParametrosReservas` con `plazo_max_venta_anios` (configurable).
        cupon_mes_ramo: porción de la liquidez del mes que corresponde a cupones de RF del
            ramo (por defecto 0). Se usa solo para reportar `cupon_no_reinvertido`.

    Returns:
        ResultadoVenta con la liquidez del mes retenida, los títulos vendidos, el ORI
        reciclado de los DV y el cupón no reinvertido.
    """
    import math as _math

    # Normalizar entradas NO confiables a floats finitos no negativos.
    def _no_negativo_finito(valor: object) -> float:
        try:
            v = float(valor)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        if not _math.isfinite(v) or v <= 0.0:
            return 0.0
        return v

    monto_a_cubrir = _no_negativo_finito(monto_a_cubrir)
    liquidez_mes = _no_negativo_finito(liquidez_mes_ramo)
    cupon_mes = _no_negativo_finito(cupon_mes_ramo)
    plazo_max = float(parametros.plazo_max_venta_anios)

    titulos_vendidos: list = []
    ori_reciclado = 0.0

    # Resultado vacío si no hay disminución que cubrir.
    if monto_a_cubrir <= 0.0:
        return ResultadoVenta(
            liquidez_consumida=0.0,
            titulos_vendidos=titulos_vendidos,
            ori_reciclado=0.0,
            cupon_no_reinvertido=0.0,
        )

    # ── Paso 1: MONTHLY_LIQUIDITY (retención parcial, Req 5.2, 5.9) ──
    liquidez_consumida = min(liquidez_mes, monto_a_cubrir)
    # Porción de cupón dentro de la liquidez retenida, por monto/proporción (Req 5.10, 11.2).
    cupon_no_reinvertido = min(cupon_mes, liquidez_consumida)

    # Liquidez acumulada disponible para cubrir la disminución. El paso 1 aporta la
    # liquidez retenida; los pasos 2 y 3 aportan el producto de las ventas.
    liquidez_acumulada = liquidez_consumida

    # Copia superficial: no mutamos la lista del llamador. Vamos removiendo los vendidos.
    disponibles = list(portafolio_ramo)

    # ── Paso 2: NEGOCIABLES (plazo ≤ plazo_max, Req 5.3, 5.4, 5.7) ──
    if liquidez_acumulada < monto_a_cubrir:
        while liquidez_acumulada < monto_a_cubrir:
            candidatos = [t for t in disponibles if es_negociable(t)]
            pendiente = monto_a_cubrir - liquidez_acumulada
            elegido = seleccionar_titulo_a_vender(
                candidatos, pendiente, plazo_max, fecha_mes
            )
            if elegido is None:
                break  # no quedan Negociables elegibles
            disponibles.remove(elegido)
            titulos_vendidos.append(elegido)
            liquidez_acumulada += _valor_mercado_seguro(elegido)  # Req 6.4

    # ── Paso 3: DV (plazo ≤ plazo_max, ordenados por ORI desc, Req 5.5, 5.6, 7, 8) ──
    if liquidez_acumulada < monto_a_cubrir:
        candidatos_dv = [t for t in disponibles if es_dv(t)]
        # Elegibles por plazo, ordenados por ORI descendente (excluye 0 días al venc.).
        dv_ordenados = ordenar_dv_por_ori(
            [t for t in candidatos_dv if _es_elegible_por_plazo(t, plazo_max, fecha_mes)],
            fecha_mes,
        )
        for titulo in dv_ordenados:
            if liquidez_acumulada >= monto_a_cubrir:
                break  # ya cubierto (Req 5.7)
            titulos_vendidos.append(titulo)
            ori_reciclado += _valor_mercado_seguro(titulo) - _valor_tir_seguro(titulo)
            liquidez_acumulada += _valor_mercado_seguro(titulo)  # Req 6.4
        # Si el requerido excede lo disponible, se vendió exactamente lo disponible (Req 5.6).

    return ResultadoVenta(
        liquidez_consumida=liquidez_consumida,
        titulos_vendidos=titulos_vendidos,
        ori_reciclado=ori_reciclado,
        cupon_no_reinvertido=cupon_no_reinvertido,
    )


# ==============================================================================
# ORQUESTACIÓN POR MES (tarea 12.1): ResultadoCompraVenta + aplicar_compras_ventas_reservas
# ==============================================================================

# tipo_bono que identifican renta fija en el `Titulo` del motor (allowlist del enum de
# motor_proyeccion: TF, UVR, IPC, YANKEE). Son literales controlados por el código; el
# atributo `tipo_bono` del título se normaliza (strip/upper) y se compara contra este
# conjunto, nunca al revés.
_TIPOS_BONO_RF = frozenset({"TF", "UVR", "IPC", "YANKEE"})


def _tipo_bono_normalizado(titulo: object) -> str:
    """Devuelve el `tipo_bono` de un `Titulo` normalizado (strip/upper); '' si no aplica."""
    valor = getattr(titulo, "tipo_bono", None)
    if valor is None:
        return ""
    return str(valor).strip().upper()


def es_renta_fija(titulo: object) -> bool:
    """
    True si un `Titulo` es de renta fija (entrada duck-typed y defensiva).

    Criterio (Req 5, 9): se considera RF si su `tipo_bono` está en la allowlist
    {TF, UVR, IPC, YANKEE} O si su clasificación contable pertenece a alguno de los
    conjuntos reconocidos (Negociable / DV / Vencimiento). El OR permite clasificar
    correctamente títulos cuyo `tipo_bono` venga vacío pero con clasificación conocida,
    y viceversa, sin lanzar ante atributos ausentes.
    """
    if _tipo_bono_normalizado(titulo) in _TIPOS_BONO_RF:
        return True
    return es_negociable(titulo) or es_dv(titulo) or es_vencimiento(titulo)


def _ramo_de_titulo(titulo: object) -> str:
    """Devuelve el ramo NORMALIZADO (strip/upper) de un `Titulo`; '' si no tiene."""
    valor = getattr(titulo, "ramo", None)
    if valor is None:
        return ""
    return _normalizar_ramo(valor)


def _titulos_rf_del_ramo(portafolio_activo: list, ramo_norm: str) -> list:
    """
    Filtra de `portafolio_activo` los títulos de RENTA FIJA cuyo ramo normalizado coincide
    con `ramo_norm`. Duck-typed: ignora elementos sin atributos esperados sin abortar.
    """
    seleccion: list = []
    for titulo in portafolio_activo:
        try:
            if _ramo_de_titulo(titulo) != ramo_norm:
                continue
            if not es_renta_fija(titulo):
                continue
        except Exception:  # noqa: BLE001 - defensivo ante elementos inesperados
            continue
        seleccion.append(titulo)
    return seleccion


def _metadatos_ramo(portafolio_activo: list, ramo_norm: str) -> tuple[str, str]:
    """
    Deriva (compania, portafolio) de una posición existente del ramo en el portafolio,
    para heredarlos en una compra nueva. Devuelve ('', '') si no hay ninguna posición del
    ramo de la cual heredar (Req 3.5, 4.4 — el llamador crea la posición igualmente).
    """
    for titulo in portafolio_activo:
        try:
            if _ramo_de_titulo(titulo) != ramo_norm:
                continue
            compania = str(getattr(titulo, "compania", "") or "").strip()
            portafolio = str(getattr(titulo, "portafolio", "") or "").strip()
            return compania, portafolio
        except Exception:  # noqa: BLE001 - defensivo
            continue
    return "", ""


@dataclass
class ResultadoCompraVenta:
    """
    Resultado agregado de las compras/ventas por variación de reserva de un mes,
    procesando cada ramo de forma independiente (Req 2, 9).

    Campos:
      - portafolio: portafolio actualizado (compras añadidas, ventas retiradas). Es una
        lista NUEVA; el input `portafolio_activo` NO se muta en el lugar (Property 17).
      - liquidez_consumida: por ramo, liquidez del mes retenida por el waterfall (paso
        MONTHLY_LIQUIDITY) — la reporta `vender_por_reserva`. El motor la resta de los
        flujos reinvertibles del ramo (Req 10.2).
      - ori_reciclado: ORI total reciclado de los DV vendidos (Σ vm − vtir), sumado sobre
        todos los ramos, a añadir al P&L realizado (Req 8).
      - cupones_no_reinvertidos: por ramo, cupón de RF detectado en el mes que se DESVIÓ al
        waterfall y NO se reinvirtió (cupón detectado − cupón reinvertido). A este nivel de
        orquestación equivale a `ResultadoVenta.cupon_no_reinvertido` del ramo (alimentado
        por `cupon_mes_por_ramo`, que el motor —tarea 13.2— provee desde
        `cupones_por_titulo_mes`). El motor lo suma al P&L (mercado y TIR) sin doble conteo
        (Req 11.2, 11.3).
      - compras: `Titulo` de posiciones nuevas creadas este mes.
      - ventas: `Titulo` retirados por ventas este mes.
      - ramos_pendientes: ramos con Δ≠0 OMITIDOS por estar sin clasificar, para que el
        motor emita la advertencia agregada (Req 2.3, 2.4, Property 21).
    """

    portafolio: list
    liquidez_consumida: dict
    ori_reciclado: float
    cupones_no_reinvertidos: dict
    compras: list
    ventas: list
    ramos_pendientes: list


def aplicar_compras_ventas_reservas(
    portafolio_activo: list,
    mapa_reservas: dict,
    mes: int,
    fecha_mes,
    universo_compra: UniversoCompra,
    sendas,
    valor_uvr,
    trm,
    serie_ipc,
    config_ramos: ConfigRamosReserva,
    parametros: ParametrosReservas,
    *,
    liquidez_mes_por_ramo: "dict | None" = None,
    cupon_mes_por_ramo: "dict | None" = None,
) -> ResultadoCompraVenta:
    """
    Orquesta, para el mes `mes`, las compras/ventas de RF dirigidas por la variación de
    reserva de cada ramo, de forma INDEPENDIENTE por ramo (Req 2, 9).

    Convención de MES CAÍDO (Req 2.1): para el mes `t == mes` se consulta la variación del
    mes anterior, `Δ = mapa_reservas.get(ramo, {}).get(mes - 1, 0.0)`.

    Por cada ramo (unión de las claves de `mapa_reservas` y de los ramos presentes en
    `portafolio_activo`), de forma independiente y order-independiente (Property 17):
      - Δ == 0 o ausente → no-op para ese ramo (Req 2.5, 10.3).
      - Δ != 0 y ramo NO clasificado (`config_ramos.esta_clasificado(ramo)` False) → se
        OMITE el ramo este mes, se añade a `ramos_pendientes` y se registra una advertencia
        genérica (no se asume "no matemática", no se aborta) (Req 2.3, 2.4, Property 21).
      - Δ > 0 y clasificado → `comprar_por_reserva(...)`. Si devuelve un `Titulo`, se añade
        a `compras` y al portafolio (posición nueva, Req 3, 4, 9.2).
      - Δ < 0 y clasificado → `vender_por_reserva(...)` sobre los títulos RF del ramo. Los
        `titulos_vendidos` se retiran del portafolio; se registra `liquidez_consumida[ramo]`,
        se acumula `ori_reciclado` y se fija `cupones_no_reinvertidos[ramo]` (Req 5-8, 11).

    El portafolio devuelto es una lista NUEVA (no se muta el input); cada ramo se procesa
    de forma aislada, de modo que el resultado es independiente del orden de procesamiento
    (Property 17).

    Args:
        portafolio_activo: títulos activos del mes (no se muta; se copia).
        mapa_reservas: {ramo: {mes_offset: delta_reserva}} (de `cargar_mapa_reservas`).
        mes: mes de la proyección (offset t); se consulta Δ en `mes - 1` (mes caído).
        fecha_mes: fecha del mes (valoración/plazo).
        universo_compra: universo precargado (`cargar_universo_compra`).
        sendas, valor_uvr, trm, serie_ipc: contexto de mercado del mes (se propaga a
            `comprar_por_reserva`).
        config_ramos: clasificación de ramos (por dato, sin default hardcodeado).
        parametros: parámetros de plazo configurables (nodo de compra y plazo máximo de
            venta); se propaga a compra y venta en lugar de literales 5/10 (Req 13.4, 13.7).
        liquidez_mes_por_ramo: por ramo, liquidez de RF del mes disponible para el paso
            MONTHLY_LIQUIDITY del waterfall (el motor —tarea 13.2— la provee; None ⇒ 0.0).
        cupon_mes_por_ramo: por ramo, porción de esa liquidez que corresponde a cupones de
            RF (el motor la provee desde `cupones_por_titulo_mes`; None ⇒ 0.0).

    Returns:
        ResultadoCompraVenta con el portafolio actualizado y los agregados del mes.
    """
    liquidez_mes_por_ramo = liquidez_mes_por_ramo or {}
    cupon_mes_por_ramo = cupon_mes_por_ramo or {}

    # Lista NUEVA: no mutar el input en el lugar (Property 17).
    portafolio_resultante: list = list(portafolio_activo)

    liquidez_consumida: dict = {}
    cupones_no_reinvertidos: dict = {}
    compras: list = []
    ventas: list = []
    ramos_pendientes: list = []
    ori_reciclado_total = 0.0

    # Mes caído (Req 2.1): la variación relevante para el mes `mes` es la de `mes - 1`.
    mes_caido = mes - 1

    # Conjunto de ramos a evaluar: claves del mapa de reservas ∪ ramos presentes en el
    # portafolio. Se normalizan para consultas consistentes. Se recorre en orden
    # determinista (ordenado) solo por reproducibilidad; el resultado es independiente del
    # orden porque cada ramo se procesa de forma aislada (Property 17).
    ramos_evaluar: set = set()
    for clave in mapa_reservas.keys():
        ramos_evaluar.add(_normalizar_ramo(clave))
    for titulo in portafolio_activo:
        ramo_t = _ramo_de_titulo(titulo)
        if ramo_t:
            ramos_evaluar.add(ramo_t)

    for ramo_norm in sorted(ramos_evaluar):
        # Δreserva del mes caído. El mapa puede estar indexado por la clave normalizada o
        # por la clave original; se intenta la normalizada primero.
        por_mes = mapa_reservas.get(ramo_norm)
        if por_mes is None:
            # Búsqueda tolerante: alguna clave que normalice al mismo ramo.
            for clave, valor in mapa_reservas.items():
                if _normalizar_ramo(clave) == ramo_norm:
                    por_mes = valor
                    break
        delta = 0.0
        if isinstance(por_mes, dict):
            try:
                delta = float(por_mes.get(mes_caido, 0.0))
            except (TypeError, ValueError):
                delta = 0.0

        # Δ == 0 o ausente → no-op para ese ramo (Req 2.5, 10.3).
        if delta == 0.0:
            continue

        # Δ != 0 y ramo sin clasificar → omitir con advertencia (Req 2.3, 2.4, Property 21).
        if not config_ramos.esta_clasificado(ramo_norm):
            ramos_pendientes.append(ramo_norm)
            logger.warning(
                "Ramo con variación de reserva no nula pero SIN CLASIFICAR; se omite la "
                "compra/venta este mes (revisar la pestaña Ajustes del dashboard)."
            )
            continue

        if delta > 0.0:
            # ── COMPRA (Req 3, 4, 9.2) ──
            compania, portafolio_ramo_meta = _metadatos_ramo(
                portafolio_activo, ramo_norm
            )
            titulo_nuevo = comprar_por_reserva(
                ramo_norm,
                delta,
                fecha_mes,
                universo_compra,
                config_ramos,
                parametros,
                compania=compania,
                portafolio=portafolio_ramo_meta,
                sendas=sendas,
                valor_uvr=valor_uvr,
                trm=trm,
                serie_ipc=serie_ipc,
            )
            if titulo_nuevo is not None:
                compras.append(titulo_nuevo)
                portafolio_resultante.append(titulo_nuevo)
        else:
            # ── VENTA (waterfall, Req 5-8, 11) ──
            titulos_rf_ramo = _titulos_rf_del_ramo(portafolio_activo, ramo_norm)
            liquidez_ramo = 0.0
            try:
                liquidez_ramo = float(
                    liquidez_mes_por_ramo.get(ramo_norm, 0.0)
                )
            except (TypeError, ValueError):
                liquidez_ramo = 0.0
            cupon_ramo = 0.0
            try:
                cupon_ramo = float(cupon_mes_por_ramo.get(ramo_norm, 0.0))
            except (TypeError, ValueError):
                cupon_ramo = 0.0

            resultado_venta = vender_por_reserva(
                ramo_norm,
                abs(delta),
                titulos_rf_ramo,
                liquidez_ramo,
                fecha_mes,
                parametros,
                cupon_mes_ramo=cupon_ramo,
            )

            # Retirar del portafolio resultante los títulos vendidos (por identidad).
            if resultado_venta.titulos_vendidos:
                ids_vendidos = {id(t) for t in resultado_venta.titulos_vendidos}
                portafolio_resultante = [
                    t for t in portafolio_resultante if id(t) not in ids_vendidos
                ]
                ventas.extend(resultado_venta.titulos_vendidos)

            liquidez_consumida[ramo_norm] = resultado_venta.liquidez_consumida
            ori_reciclado_total += resultado_venta.ori_reciclado
            cupones_no_reinvertidos[ramo_norm] = resultado_venta.cupon_no_reinvertido

    return ResultadoCompraVenta(
        portafolio=portafolio_resultante,
        liquidez_consumida=liquidez_consumida,
        ori_reciclado=ori_reciclado_total,
        cupones_no_reinvertidos=cupones_no_reinvertidos,
        compras=compras,
        ventas=ventas,
        ramos_pendientes=ramos_pendientes,
    )
