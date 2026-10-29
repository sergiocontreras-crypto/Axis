"""
comun/db.py
-----------
Capa mínima de acceso a la base de datos SQLite del proyecto AXIS.

Objetivo: una sola forma de abrir la conexión a `portafolio.db`, con la ruta
tomada de `comun.rutas.DB_PATH` (fuente única) y las opciones de conexión en un
único lugar. Antes cada módulo hacía su propio `sqlite3.connect(DB_PATH)` con la
ruta calculada por su lado; esto centraliza ese punto.

Uso:
    from comun.db import get_conn
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT ...", conn, params=(...,))

Notas:
- `check_same_thread=False`: el dashboard Dash atiende callbacks en varios hilos;
  la conexión debe poder usarse fuera del hilo que la creó (era el ajuste que ya
  tenía el get_conn local de app_dash).
- `timeout`: espera hasta 30 s si la DB está bloqueada por otra escritura, en vez
  de fallar de inmediato con "database is locked".
- Los módulos de `scripts/*` reciben `ruta_db` por parámetro (diseño intencional
  para poder apuntar a otra DB en pruebas); esos NO usan get_conn y se dejan como
  están. get_conn es para los módulos de la raíz que apuntan siempre a portafolio.db.
"""

import sqlite3

from comun.rutas import DB_PATH


def get_conn(check_same_thread: bool = False, timeout: float = 30.0) -> sqlite3.Connection:
    """
    Abre una conexión a la base de datos principal (`comun.rutas.DB_PATH`).

    Args:
        check_same_thread: si False, la conexión puede usarse desde otros hilos
            (necesario para los callbacks del dashboard). Default False.
        timeout: segundos de espera si la DB está bloqueada. Default 30.

    Returns:
        Conexión sqlite3 a portafolio.db.
    """
    return sqlite3.connect(str(DB_PATH), check_same_thread=check_same_thread, timeout=timeout)
