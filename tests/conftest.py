"""
Configuración de pruebas para la feature compras-ventas-reservas.

Objetivos:
- Asegurar que la raíz del proyecto y `scripts/` estén importables.
- Redirigir SIEMPRE el acceso a base de datos a un archivo SQLite TEMPORAL por
  prueba, de modo que los tests NUNCA toquen la `portafolio.db` real (que además
  vive en una ruta de Google Drive y puede estar bloqueada).

El módulo bajo prueba (`scripts/compras_ventas_reservas.py`) hace
`from comun.db import get_conn` al importarse, por lo que tiene su propia
referencia `get_conn`. Para redirigir la DB se monkeypatchea esa referencia
(`compras_ventas_reservas.get_conn`) para que abra la DB temporal. También se
apunta `comun.db.get_conn`/`comun.rutas.DB_PATH` a la DB temporal por si algún
código la usa indirectamente.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# ── Rutas importables: raíz del proyecto y scripts/ ──
_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def db_tmp(tmp_path, monkeypatch):
    """
    Fixture autouse: crea una DB SQLite temporal y redirige todo acceso a ella.

    Devuelve la ruta del archivo temporal por si una prueba la necesita.
    """
    ruta_tmp = tmp_path / "test_portafolio.db"

    def _get_conn_tmp(check_same_thread: bool = False, timeout: float = 30.0):
        return sqlite3.connect(
            str(ruta_tmp), check_same_thread=check_same_thread, timeout=timeout
        )

    # Redirigir la ruta única.
    import comun.rutas as _rutas
    import comun.db as _db

    monkeypatch.setattr(_rutas, "DB_PATH", ruta_tmp, raising=False)
    monkeypatch.setattr(_db, "DB_PATH", ruta_tmp, raising=False)
    monkeypatch.setattr(_db, "get_conn", _get_conn_tmp, raising=True)

    # El módulo bajo prueba importó get_conn a su namespace: redirigir esa
    # referencia también (es la que realmente usan sus funciones).
    import compras_ventas_reservas as _cvr

    monkeypatch.setattr(_cvr, "get_conn", _get_conn_tmp, raising=True)

    yield ruta_tmp


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades comunes para la feature escalabilidad-derivados-portafolios
#
# Estas utilidades son ADITIVAS: no modifican el fixture `db_tmp` de arriba (que
# pertenece a la feature compras-ventas-reservas). Aquí se agregan:
#   1. Una fábrica de DB SQLite temporal (basada en `tempfile`) que crea las
#      tablas `param_portafolios` y `param_diccionario_swaps` con el esquema
#      exacto del proyecto (verificado en `init_db.py`/`clasificar_portafolio.py`).
#   2. Estrategias/generadores Hypothesis para tasas, plazos, TRM, listas de ids
#      y curvas sintéticas, reutilizables por las pruebas basadas en propiedades.
# ─────────────────────────────────────────────────────────────────────────────

import tempfile as _tempfile

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

# ─────────────────────────────────────────────────────────────────────────────
# Perfil Hypothesis — supresión de HealthCheck.too_slow (ADITIVO).
#
# En este entorno la raíz del repo vive en una ruta de Google Drive (sincronizada
# en red, con espacios). Hypothesis mantiene su base de ejemplos y su caché de
# "constants" bajo `.hypothesis/`; en ese sistema de archivos cada *draw* puede
# tardar decenas de segundos (lectura/escritura sincronizada), disparando
# `HealthCheck.too_slow` durante la GENERACIÓN de datos aunque el cuerpo del test
# corra en ~0.001 s. Esa lentitud es ajena a la lógica bajo prueba.
#
# Se registra un perfil que SOLO suprime `too_slow` (sin tocar `deadline` ni
# ninguna otra verificación) y se activa por defecto. Es aditivo: no altera las
# propiedades ni las aserciones de ningún test; únicamente evita que un artefacto
# de I/O del entorno marque como fallidas pruebas cuya propiedad sí se cumple.
settings.register_profile(
    "axis_fs_lento",
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("axis_fs_lento")

# Esquema exacto de las tablas param_* afectadas por esta feature (Req 1.10, 2.9,
# 5.1). El orden y los nombres de columna deben coincidir con el proyecto real:
#   - param_portafolios(portafolio_porfin, ramo, tipo, excluir)
#   - param_diccionario_swaps(id_posicion, Subyacente)
COLUMNAS_PARAM_PORTAFOLIOS = ("portafolio_porfin", "ramo", "tipo", "excluir")
COLUMNAS_PARAM_DICCIONARIO_SWAPS = ("id_posicion", "Subyacente")


def crear_db_param_temporal():
    """
    Crea un archivo SQLite temporal (vía `tempfile`) con las tablas `param_*`
    afectadas por esta feature, usando el esquema exacto del proyecto.

    A diferencia del fixture autouse `db_tmp`, esta fábrica NO monkeypatchea
    ningún módulo: devuelve la ruta del archivo para que la prueba abra la
    conexión que necesite. El llamador es responsable de eliminar el archivo.

    Returns:
        pathlib.Path: ruta del archivo SQLite temporal recién creado.
    """
    fd, nombre = _tempfile.mkstemp(suffix=".db", prefix="axis_param_")
    import os as _os

    _os.close(fd)
    ruta = Path(nombre)
    conn = sqlite3.connect(str(ruta))
    try:
        conn.execute(
            "CREATE TABLE param_portafolios "
            "(portafolio_porfin TEXT, ramo TEXT, tipo TEXT, excluir TEXT)"
        )
        conn.execute(
            "CREATE TABLE param_diccionario_swaps "
            "(id_posicion TEXT, Subyacente TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    return ruta


@pytest.fixture
def db_param_tmp():
    """
    Fixture que entrega la ruta de una DB SQLite temporal con las tablas `param_*`
    ya creadas (esquema del proyecto). Limpia el archivo al finalizar.

    Yields:
        pathlib.Path: ruta del archivo SQLite temporal.
    """
    ruta = crear_db_param_temporal()
    try:
        yield ruta
    finally:
        try:
            ruta.unlink()
        except OSError:
            pass


# ── Estrategias Hypothesis reutilizables ──
#
# Rangos financieramente razonables, alineados con el diseño (tasas en %, plazos
# en días, TRM en COP, curvas sintéticas). Se exponen como funciones para poder
# parametrizarlas por prueba.

# Identificadores: cadenas no vacías, incluyendo caracteres "adversarios" para
# ejercitar la normalización y las escrituras parametrizadas (anti-inyección).
_ALFABETO_IDS = st.characters(
    min_codepoint=32, max_codepoint=126, blacklist_categories=("Cs",)
)


def ids_strategy(min_size: int = 1, max_size: int = 12):
    """Estrategia de identificadores str no triviales (con espacios y símbolos)."""
    return st.text(alphabet=_ALFABETO_IDS, min_size=min_size, max_size=max_size)


def lista_ids_strategy(min_ids: int = 1, max_ids: int = 50):
    """Estrategia de listas de identificadores (p.ej. subyacentes de un swap)."""
    return st.lists(ids_strategy(), min_size=min_ids, max_size=max_ids)


def tasas_strategy(min_pct: float = -5.0, max_pct: float = 40.0):
    """
    Tasas nominales en % (base 360 en el dominio del proyecto). Rango amplio pero
    financieramente plausible; finitas y sin NaN.
    """
    return st.floats(
        min_value=min_pct,
        max_value=max_pct,
        allow_nan=False,
        allow_infinity=False,
    )


def plazos_strategy(min_dias: int = 1, max_dias: int = 3650):
    """Plazos en días (> 0), hasta 10 años."""
    return st.integers(min_value=min_dias, max_value=max_dias)


def trm_strategy(min_trm: float = 500.0, max_trm: float = 10000.0):
    """TRM (USD/COP) en un rango histórico plausible; positiva y finita."""
    return st.floats(
        min_value=min_trm,
        max_value=max_trm,
        allow_nan=False,
        allow_infinity=False,
    )


@st.composite
def curva_sintetica_strategy(draw, min_puntos: int = 2, max_puntos: int = 12):
    """
    Genera una curva sintética como lista de pares (plazo_dias, tasa_pct) con
    plazos estrictamente crecientes y tasas finitas. Útil para probar
    interpolación/proyección de curvas (SWTIBR/LUTIBR).

    Returns:
        list[tuple[int, float]]: pares (dias, tasa_pct) ordenados por plazo.
    """
    n = draw(st.integers(min_value=min_puntos, max_value=max_puntos))
    plazos = draw(
        st.lists(
            plazos_strategy(),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    plazos = sorted(plazos)
    tasas = draw(
        st.lists(tasas_strategy(), min_size=len(plazos), max_size=len(plazos))
    )
    return list(zip(plazos, tasas))
