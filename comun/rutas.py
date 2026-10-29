"""
comun/rutas.py
--------------
Fuente única de rutas del proyecto AXIS.

En vez de que cada módulo calcule sus propias rutas con Path(__file__).parent
(frágil ante movimientos de archivos), todos importan las rutas desde aquí:

    from comun.rutas import DB_PATH, INPUTS_DIR, PROJECT_ROOT

Notas:
- PROJECT_ROOT se calcula subiendo un nivel desde comun/ (que vive en la raíz).
  Si en el futuro comun/ se mueve a src/comun/, solo hay que ajustar aquí.
- Las rutas de datos de mercado (curvas de derivados, TRM, PIP, Serie Inflación)
  NO se centralizan todavía: en la Fase D esa lógica migrará a leer desde tablas
  de la DB en vez de archivos físicos, por lo que esas rutas desaparecerán.
"""

import os
from pathlib import Path

# Raíz del proyecto (comun/ está directamente bajo la raíz).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Carpeta de insumos crudos y tablas maestras (597, forwards, opciones, swaps,
# mapeos, matrices, categorías, diccionarios).
INPUTS_DIR = PROJECT_ROOT / "Inputs Py"

# Base de datos SQLite principal del proyecto.
#
# Rendimiento (Fix 3, Req 17.5): la ruta es CONFIGURABLE por la variable de entorno
# `AXIS_DB_PATH`. SQLite sobre un sistema de archivos sincronizado en la nube
# (p. ej. Google Drive) sufre latencia alta y bloqueos (`database is locked`); para
# acelerar las corridas se puede apuntar la DB a un disco LOCAL definiendo
# `AXIS_DB_PATH=C:\axis_runtime\portafolio.db` antes de arrancar la app. Si la variable
# NO está definida, se usa la ruta histórica (raíz del proyecto), de modo que el
# comportamiento por defecto es IDÉNTICO al actual (retrocompatible). La DB está en
# `.gitignore`, por lo que mover su ubicación NO afecta el repositorio.
_DB_PATH_ENV = os.getenv("AXIS_DB_PATH")
DB_PATH = Path(_DB_PATH_ENV) if _DB_PATH_ENV else (PROJECT_ROOT / "portafolio.db")
