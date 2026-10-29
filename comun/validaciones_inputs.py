"""
comun/validaciones_inputs.py
-----------------------------
Funciones puras de normalización y detección para las altas del dashboard.

Aísla como funciones **puras** (sin efectos secundarios, sin I/O, sin acceso a
DB) la lógica de:

- Normalización de identificadores de entrada (trim + coerción a str).
- Detección de Portafolio_Nuevo: portafolios presentes en el portafolio del
  corte pero ausentes de `param_portafolios` (Req 1.1, 1.2).
- Detección de Swap_Sin_Subyacente: swaps presentes en el corte pero ausentes
  de `param_diccionario_swaps` (Req 2.1, 2.2).

Al ser puras, pueden probarse con pruebas basadas en propiedades (Hypothesis)
sin necesidad de una base de datos ni del framework Dash.

Seguridad (rule-security): toda entrada se trata como no confiable. Los valores
se normalizan (trim, coerción a str) antes de compararse. Estas funciones no
construyen SQL ni tocan la base de datos; la persistencia parametrizada vive en
funciones aparte (tarea 3.1).
"""

from __future__ import annotations

import re
from typing import Iterable


def normalizar_id(valor: object) -> str:
    """
    Normaliza un identificador de entrada a una cadena canónica.

    Recorta espacios iniciales y finales y coacciona el valor a `str`. `None` se
    normaliza a cadena vacía para evitar comparar contra el literal ``"None"``.

    Es la única forma de normalizar identificadores en este módulo, de modo que
    la detección y la comparación de "ya registrados" sean consistentes.

    Args:
        valor: valor de entrada (típicamente `str`, pero puede llegar un número
            o `None` desde una tabla editable del dashboard).

    Returns:
        Identificador normalizado como `str` (posiblemente vacío).

    Examples:
        >>> normalizar_id("  123 ")
        '123'
        >>> normalizar_id(None)
        ''
        >>> normalizar_id(456)
        '456'
    """
    if valor is None:
        return ""
    return str(valor).strip()


def _normalizar_conjunto(valores: Iterable[object]) -> set[str]:
    """
    Normaliza un iterable de valores a un conjunto de ids no vacíos.

    Función auxiliar interna: aplica `normalizar_id` a cada elemento y descarta
    las cadenas vacías (entradas nulas o compuestas solo por espacios).

    Args:
        valores: iterable de valores de entrada.

    Returns:
        Conjunto de identificadores normalizados no vacíos.
    """
    return {norm for v in valores if (norm := normalizar_id(v))}


def detectar_portafolios_nuevos(
    portafolios_corte: Iterable[object],
    registrados: Iterable[object],
) -> list[str]:
    """
    Detecta los Portafolio_Nuevo del corte (Req 1.1, 1.2).

    Un Portafolio_Nuevo es un `portafolio_porfin` presente en el portafolio del
    corte cuyo valor normalizado no existe aún en `param_portafolios`.

    Ambos conjuntos se normalizan con `normalizar_id` antes de comparar, de modo
    que diferencias por espacios iniciales/finales no generen falsos positivos.

    Args:
        portafolios_corte: `portafolio_porfin` presentes en el portafolio del
            corte seleccionado.
        registrados: `portafolio_porfin` ya existentes en `param_portafolios`.

    Returns:
        Lista ordenada (determinista) de los `portafolio_porfin` normalizados
        que están en el corte pero no en los registrados. Sin duplicados.

    Examples:
        >>> detectar_portafolios_nuevos(["01A", " 02B ", "01A"], ["02B"])
        ['01A']
    """
    corte = _normalizar_conjunto(portafolios_corte)
    ya_registrados = _normalizar_conjunto(registrados)
    return sorted(corte - ya_registrados)


def detectar_swaps_sin_subyacente(
    swaps_corte: Iterable[object],
    registrados: Iterable[object],
) -> list[str]:
    """
    Detecta los Swap_Sin_Subyacente del corte (Req 2.1, 2.2).

    Un Swap_Sin_Subyacente es un `id_posicion` de swap presente en el portafolio
    del corte cuyo valor normalizado no existe aún en `param_diccionario_swaps`.

    Ambos conjuntos se normalizan con `normalizar_id` antes de comparar.

    Args:
        swaps_corte: `id_posicion` de swaps presentes en el corte seleccionado.
        registrados: `id_posicion` ya existentes en `param_diccionario_swaps`.

    Returns:
        Lista ordenada (determinista) de los `id_posicion` normalizados que están
        en el corte pero no en los registrados. Sin duplicados.

    Examples:
        >>> detectar_swaps_sin_subyacente(["1517", " 2536 "], ["1517"])
        ['2536']
    """
    corte = _normalizar_conjunto(swaps_corte)
    ya_registrados = _normalizar_conjunto(registrados)
    return sorted(corte - ya_registrados)


# Allowlist de valores admitidos para la columna `excluir` de `param_portafolios`.
# Regla de seguridad (rule-security): validación por lista blanca, nunca por
# lista negra. Toda entrada fuera de este conjunto se rechaza.
_EXCLUIR_VALIDOS: frozenset[str] = frozenset({"SI", "NO"})

# Cota superior de subyacentes admitidos por swap (Req 2.1). El mínimo es 1.
_MAX_SUBYACENTES: int = 50


def validar_alta_portafolio(
    porfin: object,
    ramo: object,
    tipo: object,
    excluir: object,
    existentes: Iterable[object],
) -> tuple[bool, str, dict]:
    """
    Valida el alta de un Portafolio_Nuevo (Req 1.3, 1.4, 1.5, 1.6).

    Función pura: no toca la base de datos ni construye SQL. Devuelve el
    veredicto y, cuando acepta, un payload ya normalizado listo para persistir
    con una consulta parametrizada aparte.

    Reglas de validación (todas sobre valores normalizados con `normalizar_id`):

    - Rechaza si `ramo` o `tipo` quedan vacíos o compuestos solo por espacios
      (Req 1.5), identificando el campo faltante en el mensaje.
    - Rechaza si `excluir` no pertenece a la lista blanca {'SI', 'NO'} (Req 1.4).
    - Rechaza si el `portafolio_porfin` normalizado ya existe en `existentes`,
      identificando el duplicado en el mensaje (Req 1.6).
    - Rechaza si el `portafolio_porfin` normalizado queda vacío.

    El orden de comprobación es determinista: primero el identificador, luego
    los campos requeridos, luego el enum y por último la unicidad.

    Args:
        porfin: `portafolio_porfin` a dar de alta (se normaliza con trim).
        ramo: Ramo indicado para el portafolio.
        tipo: Tipo indicado para el portafolio.
        excluir: marca de exclusión; debe normalizar a 'SI' o 'NO'.
        existentes: `portafolio_porfin` ya registrados en `param_portafolios`.

    Returns:
        Tupla `(ok, mensaje, payload)`:
        - `ok`: `True` si el alta es válida.
        - `mensaje`: cadena de confirmación (ok) o de error específico (rechazo).
        - `payload`: dict con `portafolio_porfin`, `ramo`, `tipo`, `excluir`
          normalizados cuando `ok`; dict vacío cuando se rechaza.

    Examples:
        >>> validar_alta_portafolio("  01A ", "VIDA", "T1", "no", {"02B"})[0]
        True
        >>> validar_alta_portafolio("01A", " ", "T1", "NO", set())[0]
        False
    """
    porfin_norm = normalizar_id(porfin)
    if not porfin_norm:
        return False, "El identificador del portafolio (portafolio_porfin) es obligatorio.", {}

    ramo_norm = normalizar_id(ramo)
    if not ramo_norm:
        return False, "El campo Ramo es obligatorio y no puede estar vacío.", {}

    tipo_norm = normalizar_id(tipo)
    if not tipo_norm:
        return False, "El campo Tipo es obligatorio y no puede estar vacío.", {}

    # Enum por lista blanca: se compara en mayúsculas para tolerar 'si'/'no',
    # pero el valor persistido queda canonizado a 'SI'/'NO'.
    excluir_norm = normalizar_id(excluir).upper()
    if excluir_norm not in _EXCLUIR_VALIDOS:
        return (
            False,
            "El campo Excluir debe ser 'SI' o 'NO'.",
            {},
        )

    ya_registrados = _normalizar_conjunto(existentes)
    if porfin_norm in ya_registrados:
        return (
            False,
            f"El portafolio '{porfin_norm}' ya existe en param_portafolios.",
            {},
        )

    payload = {
        "portafolio_porfin": porfin_norm,
        "ramo": ramo_norm,
        "tipo": tipo_norm,
        "excluir": excluir_norm,
    }
    return True, f"Portafolio '{porfin_norm}' válido para alta.", payload


def parsear_subyacentes(raw: object) -> list[str]:
    """
    Parsea la entrada de subyacentes a una lista de ids normalizados (Req 2.3).

    Función pura y tolerante al formato que llega desde una tabla editable del
    dashboard. Acepta:

    - Una lista o tupla de ids (cada elemento se normaliza).
    - Una cadena con ids separados por '|', ',', ';' o espacios en blanco.
    - `None` o vacío, que producen una lista vacía.

    Descarta las entradas vacías o compuestas solo por espacios y elimina
    duplicados preservando el orden de primera aparición, de modo que el
    posterior `'|'.join(...)` sea determinista.

    Args:
        raw: entrada cruda (cadena, lista/tupla o `None`).

    Returns:
        Lista de ids normalizados, sin vacíos ni duplicados, en orden de
        aparición.

    Examples:
        >>> parsear_subyacentes("1517 | 2536 |1517")
        ['1517', '2536']
        >>> parsear_subyacentes(["1517", " ", "2536"])
        ['1517', '2536']
        >>> parsear_subyacentes(None)
        []
    """
    if raw is None:
        return []

    if isinstance(raw, (list, tuple)):
        candidatos: list[str] = [normalizar_id(elem) for elem in raw]
    else:
        # Cadena (o cualquier escalar): normaliza a str y separa por los
        # delimitadores admitidos.
        texto = normalizar_id(raw)
        candidatos = re.split(r"[|,;\s]+", texto) if texto else []

    vistos: set[str] = set()
    resultado: list[str] = []
    for cand in candidatos:
        norm = normalizar_id(cand)
        if norm and norm not in vistos:
            vistos.add(norm)
            resultado.append(norm)
    return resultado


def validar_asignacion_swap(
    id_swap: object,
    subyacentes: list[str],
    ids_portafolio_corte: Iterable[object],
    swaps_registrados: Iterable[object],
) -> tuple[bool, str, dict]:
    """
    Valida la asignación de subyacentes a un Swap_Sin_Subyacente (Req 2.3-2.6).

    Función pura: no toca la base de datos ni construye SQL. Devuelve el
    veredicto y, cuando acepta, un payload normalizado listo para persistir con
    una consulta parametrizada aparte.

    Reglas de validación (todas sobre valores normalizados):

    - Rechaza si el `id_posicion` del swap normalizado queda vacío.
    - Rechaza si hay 0 subyacentes indicados (Req 2.5).
    - Rechaza si el número de subyacentes queda fuera del rango [1, 50] (Req 2.1).
    - Rechaza, identificando cada id ausente, si algún subyacente no existe en el
      portafolio del corte (Req 2.4).
    - Rechaza si el swap ya está registrado en `param_diccionario_swaps` (Req 2.6).

    El orden de comprobación es determinista: identificador, cardinalidad,
    existencia de subyacentes y por último unicidad del swap.

    Args:
        id_swap: `id_posicion` del swap a asignar (se normaliza con trim).
        subyacentes: lista de `id_posicion` de Subyacente (idealmente ya pasada
            por `parsear_subyacentes`).
        ids_portafolio_corte: `id_posicion` presentes en el portafolio del corte.
        swaps_registrados: `id_posicion` de swaps ya en `param_diccionario_swaps`.

    Returns:
        Tupla `(ok, mensaje, payload)`:
        - `ok`: `True` si la asignación es válida.
        - `mensaje`: cadena de confirmación (ok) o de error específico (rechazo).
        - `payload`: dict con `id_posicion` y `Subyacente` (ids unidos por '|')
          cuando `ok`; dict vacío cuando se rechaza.

    Examples:
        >>> validar_asignacion_swap("1517", ["A", "B"], {"A", "B"}, set())
        (True, "Swap '1517' válido para asignación.", {'id_posicion': '1517', 'Subyacente': 'A|B'})
        >>> validar_asignacion_swap("1517", [], {"A"}, set())[0]
        False
    """
    swap_norm = normalizar_id(id_swap)
    if not swap_norm:
        return False, "El identificador del swap (id_posicion) es obligatorio.", {}

    # Renormaliza y deduplica los subyacentes de forma defensiva: aunque el
    # llamador use `parsear_subyacentes`, esta función no confía en su entrada.
    subyacentes_norm: list[str] = []
    vistos: set[str] = set()
    for s in subyacentes or []:
        norm = normalizar_id(s)
        if norm and norm not in vistos:
            vistos.add(norm)
            subyacentes_norm.append(norm)

    n = len(subyacentes_norm)
    if n == 0:
        return (
            False,
            "Se requiere al menos un Subyacente para el swap "
            f"'{swap_norm}'.",
            {},
        )
    if n > _MAX_SUBYACENTES:
        return (
            False,
            f"El swap '{swap_norm}' admite entre 1 y {_MAX_SUBYACENTES} "
            f"subyacentes; se indicaron {n}.",
            {},
        )

    ids_corte = _normalizar_conjunto(ids_portafolio_corte)
    ausentes = [s for s in subyacentes_norm if s not in ids_corte]
    if ausentes:
        return (
            False,
            "Los siguientes subyacentes no existen en el portafolio del corte: "
            + ", ".join(ausentes)
            + ".",
            {},
        )

    swaps_ya = _normalizar_conjunto(swaps_registrados)
    if swap_norm in swaps_ya:
        return (
            False,
            f"El swap '{swap_norm}' ya está registrado en "
            "param_diccionario_swaps.",
            {},
        )

    payload = {
        "id_posicion": swap_norm,
        "Subyacente": "|".join(subyacentes_norm),
    }
    return True, f"Swap '{swap_norm}' válido para asignación.", payload


# ---------------------------------------------------------------------------
# Persistencia parametrizada y atómica (tarea 3.1 — Req 1.10, 2.9, 5.1, 5.2, 5.5)
# ---------------------------------------------------------------------------
#
# Estas dos funciones son la única forma de escribir un alta de portafolio o una
# asignación de subyacente en la base de datos. A diferencia del resto del
# módulo, tienen efecto secundario (I/O sobre SQLite), por lo que reciben la
# conexión ya abierta por el llamador (el callback del dashboard) y NO la abren
# ni la cierran: la gestión del ciclo de vida de la conexión es responsabilidad
# de quien la crea.
#
# Reglas de seguridad (rule-security, Req 5.2):
# - Los VALORES siempre se enlazan con placeholders `?` (nunca se concatenan al
#   texto SQL), lo que neutraliza la inyección SQL.
# - Los NOMBRES DE COLUMNA provienen de un allowlist fijo definido como
#   constante en tiempo de importación; nunca se toman de la entrada del
#   usuario. La sentencia SQL completa es, por tanto, estática y conocida.
#
# Atomicidad (Req 5.5):
# - La escritura se envuelve en `with conn:`, el context manager de sqlite3 que
#   hace COMMIT si el bloque termina sin excepción y ROLLBACK si se lanza una.
#   Ante cualquier excepción se revierten los cambios parciales y se relanza
#   (re-raise) para que el llamador muestre el mensaje genérico de fallo.
#
# Preservación de esquema (Req 1.10, 2.9, 5.1):
# - Las sentencias nombran explícitamente solo las columnas del allowlist; no se
#   ejecuta ningún DDL (`CREATE`/`ALTER`), de modo que el conjunto, el orden y el
#   tipo de columnas de las tablas existentes permanecen intactos. En tablas con
#   columnas adicionales (p.ej. `param_diccionario_swaps` tiene `Derecho` y
#   `Obligacion`), esas columnas no nombradas se dejan tal cual las gestione la
#   tabla (NULL/DEFAULT), sin alterar el esquema.

# Allowlist fijo de columnas por tabla. Se materializa como tuplas para dejar
# claro que es un contrato inmutable y no derivado de la entrada del usuario.
_COLUMNAS_PORTAFOLIO: tuple[str, ...] = ("portafolio_porfin", "ramo", "tipo", "excluir")
_COLUMNAS_SUBYACENTE: tuple[str, ...] = ("id_posicion", "Subyacente")

# Sentencias SQL estáticas construidas una sola vez a partir del allowlist. Los
# nombres de columna quedan fijados en tiempo de importación; solo los valores
# viajan como parámetros posicionales `?`.
_SQL_INSERT_PORTAFOLIO: str = (
    "INSERT INTO param_portafolios ("
    + ", ".join(f'"{c}"' for c in _COLUMNAS_PORTAFOLIO)
    + ") VALUES ("
    + ", ".join("?" for _ in _COLUMNAS_PORTAFOLIO)
    + ")"
)
_SQL_INSERT_SUBYACENTE: str = (
    "INSERT INTO param_diccionario_swaps ("
    + ", ".join(f'"{c}"' for c in _COLUMNAS_SUBYACENTE)
    + ") VALUES ("
    + ", ".join("?" for _ in _COLUMNAS_SUBYACENTE)
    + ")"
)


def _valores_desde_payload(payload: dict, columnas: tuple[str, ...]) -> tuple:
    """
    Extrae los valores del payload en el orden EXACTO del allowlist de columnas.

    Función auxiliar interna. Garantiza que la tupla de valores enlazados
    corresponda posicionalmente a las columnas de la sentencia `INSERT`, y falla
    de forma explícita (`KeyError`) si el payload no trae alguna clave esperada,
    evitando insertar datos silenciosamente desalineados.

    Args:
        payload: dict normalizado producido por los validadores puros.
        columnas: allowlist fijo de columnas (define el orden).

    Returns:
        Tupla de valores en el orden de `columnas`, lista para binding `?`.

    Raises:
        KeyError: si falta alguna columna esperada en el payload.
    """
    return tuple(payload[col] for col in columnas)


def persistir_portafolio(conn, payload: dict) -> None:
    """
    Inserta un alta de Portafolio_Nuevo en `param_portafolios` (Req 1.10, 5.2, 5.5).

    Escribe las cuatro columnas del esquema existente (`portafolio_porfin`,
    `ramo`, `tipo`, `excluir`) dentro de una única transacción atómica. Todos los
    valores se enlazan con placeholders `?`; los nombres de columna provienen del
    allowlist fijo `_COLUMNAS_PORTAFOLIO`. Ante cualquier excepción, revierte los
    cambios parciales (rollback) y relanza la excepción.

    No abre ni cierra la conexión: la recibe ya abierta del llamador y respeta su
    ciclo de vida. No ejecuta DDL, por lo que preserva el esquema de la tabla.

    Args:
        conn: conexión sqlite3 abierta (típicamente de `comun.db.get_conn`).
        payload: dict normalizado con las claves `portafolio_porfin`, `ramo`,
            `tipo` y `excluir` (el que devuelve `validar_alta_portafolio`).

    Returns:
        None.

    Raises:
        KeyError: si el payload no contiene todas las columnas esperadas.
        sqlite3.Error: si la escritura falla; los cambios se revierten antes de
            relanzar.
    """
    valores = _valores_desde_payload(payload, _COLUMNAS_PORTAFOLIO)
    try:
        # `with conn:` hace COMMIT al salir sin excepción y ROLLBACK si se lanza.
        with conn:
            conn.execute(_SQL_INSERT_PORTAFOLIO, valores)
    except Exception:
        # Rollback defensivo explícito por si el context manager no lo cubre
        # (p.ej. una conexión en modo autocommit), y re-raise para que el
        # llamador informe el fallo sin dejar escrituras parciales (Req 5.5).
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def persistir_subyacente(conn, payload: dict) -> None:
    """
    Inserta una asignación de subyacentes en `param_diccionario_swaps` (Req 2.9, 5.2, 5.5).

    Escribe las dos columnas gestionadas por esta funcionalidad (`id_posicion` y
    `Subyacente`, con los ids unidos por '|') dentro de una única transacción
    atómica. Todos los valores se enlazan con placeholders `?`; los nombres de
    columna provienen del allowlist fijo `_COLUMNAS_SUBYACENTE`. Ante cualquier
    excepción, revierte los cambios parciales (rollback) y relanza la excepción.

    Las columnas adicionales de la tabla (`Derecho`, `Obligacion`) no se nombran
    y quedan con su valor por defecto/NULL: no se ejecuta DDL, de modo que el
    esquema existente permanece intacto (Req 2.9, 5.1).

    No abre ni cierra la conexión: la recibe ya abierta del llamador.

    Args:
        conn: conexión sqlite3 abierta (típicamente de `comun.db.get_conn`).
        payload: dict normalizado con las claves `id_posicion` y `Subyacente`
            (el que devuelve `validar_asignacion_swap`).

    Returns:
        None.

    Raises:
        KeyError: si el payload no contiene todas las columnas esperadas.
        sqlite3.Error: si la escritura falla; los cambios se revierten antes de
            relanzar.
    """
    valores = _valores_desde_payload(payload, _COLUMNAS_SUBYACENTE)
    try:
        with conn:
            conn.execute(_SQL_INSERT_SUBYACENTE, valores)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
