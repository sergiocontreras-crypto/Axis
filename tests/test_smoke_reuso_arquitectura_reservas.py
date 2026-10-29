"""
Smoke tests de REUSO y RESTRICCIONES DE ARQUITECTURA para la feature
`lectura-reservas-encabezados-fecha` (Task 11.3).

Estos tests NO ejercitan lógica de datos: verifican invariantes estructurales del
diseño que deben preservarse para minimizar el riesgo de regresión (Req 12) y para
que el número de tramos siga siendo dinámico vía `calcular_tramos` (Req 9.1):

  (1) Estructura de DOS HOJAS preservada: existen las constantes de nombre de hoja
      (`_HOJA_SALDO == "Saldo Reservas"`, `_HOJA_INTERES == "Interés a la Reserva"`)
      y `_validar_hojas` EXIGE ambas.                              (Req 12.1)

  (2) Diccionarios_Mapeo preservados: existen las tablas semilla / helpers
      (`mapeo_ramos_saldo_reservas` / `mapeo_ramos_interes_reservas`,
      `cargar_mapeos_reservas`, `_SEED_MAPEO_SALDO` / `_SEED_MAPEO_INTERES`
      NO vacíos).                                                  (Req 12.1)

  (3) `calcular_tramos` y `prorratear_reservas_anual` se REUTILIZAN desde
      `proyeccion_tramos` (no se reimplementan): el orquestador de tramos de
      `app_dash.py` importa y llama a `proyeccion_tramos` (`ptr.calcular_tramos`,
      `ptr.prorratear_reservas_anual`), y `compras_ventas_reservas` NO define su
      propia versión de esas funciones.                           (Req 9.1, 12.3)

La ruta importable (raíz del proyecto + `scripts/`) la fija `tests/conftest.py`.
El orquestador de `app_dash.py` se inspecciona LEYENDO el archivo fuente de forma
estática (sin importar el módulo Dash), para evitar cualquier efecto secundario de
arranque de la aplicación.
"""

import ast
import inspect
import re
from pathlib import Path

import compras_ventas_reservas as cvr
import proyeccion_tramos as ptr


# Raíz del proyecto (donde vive app_dash.py). conftest ya añade raíz + scripts/.
_ROOT = Path(__file__).resolve().parents[1]
_APP_DASH = _ROOT / "app_dash.py"


# ─────────────────────────────────────────────────────────────────────────────
# (1) Estructura de DOS HOJAS preservada (Req 12.1)
# ─────────────────────────────────────────────────────────────────────────────


def test_constantes_de_hojas_preservadas():
    """Las constantes de nombre de las dos hojas existen y conservan su valor."""
    assert cvr._HOJA_SALDO == "Saldo Reservas"
    assert cvr._HOJA_INTERES == "Interés a la Reserva"


def test_validar_hojas_requiere_ambas_hojas():
    """
    `_validar_hojas` debe EXIGIR literalmente las dos hojas esperadas: su fuente
    referencia ambas constantes y calcula el conjunto de hojas faltantes contra
    `{_HOJA_SALDO, _HOJA_INTERES}`.
    """
    assert hasattr(cvr, "_validar_hojas")
    assert callable(cvr._validar_hojas)

    fuente = inspect.getsource(cvr._validar_hojas)
    assert "_HOJA_SALDO" in fuente
    assert "_HOJA_INTERES" in fuente
    # La estructura de dos hojas se valida como conjunto requerido.
    assert "{_HOJA_SALDO, _HOJA_INTERES}" in fuente


# ─────────────────────────────────────────────────────────────────────────────
# (2) Diccionarios_Mapeo preservados (Req 12.1)
# ─────────────────────────────────────────────────────────────────────────────


def test_helpers_de_mapeo_existen():
    """Existen el cargador de mapeos y el asegurador de tablas de mapeo."""
    assert hasattr(cvr, "cargar_mapeos_reservas")
    assert callable(cvr.cargar_mapeos_reservas)
    assert hasattr(cvr, "asegurar_tablas_mapeo_reservas")
    assert callable(cvr.asegurar_tablas_mapeo_reservas)


def test_tablas_de_mapeo_referenciadas_por_nombre():
    """
    Los nombres de las dos tablas de Diccionarios_Mapeo se conservan y se aplican
    (se crean/consultan) dentro de `asegurar_tablas_mapeo_reservas`.
    """
    fuente = inspect.getsource(cvr.asegurar_tablas_mapeo_reservas)
    assert "mapeo_ramos_saldo_reservas" in fuente
    assert "mapeo_ramos_interes_reservas" in fuente


def test_seed_mapeo_saldo_no_vacio():
    """La tabla semilla de saldos existe y NO está vacía (se siembra al aplicar)."""
    assert hasattr(cvr, "_SEED_MAPEO_SALDO")
    assert len(cvr._SEED_MAPEO_SALDO) > 0
    # Cada entrada es un par (origen, destino).
    for entrada in cvr._SEED_MAPEO_SALDO:
        assert len(entrada) == 2


def test_seed_mapeo_interes_no_vacio():
    """La tabla semilla de interés existe y NO está vacía (se siembra al aplicar)."""
    assert hasattr(cvr, "_SEED_MAPEO_INTERES")
    assert len(cvr._SEED_MAPEO_INTERES) > 0
    for entrada in cvr._SEED_MAPEO_INTERES:
        assert len(entrada) == 2


# ─────────────────────────────────────────────────────────────────────────────
# (3) `calcular_tramos` / `prorratear_reservas_anual` REUTILIZADOS (Req 9.1, 12.3)
# ─────────────────────────────────────────────────────────────────────────────


def _fuente_funcion_app_dash(nombre: str) -> str:
    """
    Extrae de forma ESTÁTICA (sin importar el módulo Dash) el código fuente de la
    función `nombre` definida a nivel de módulo en `app_dash.py`.
    """
    codigo = _APP_DASH.read_text(encoding="utf-8")
    arbol = ast.parse(codigo)
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return ast.get_source_segment(codigo, nodo)
    raise AssertionError(f"No se encontró la función {nombre!r} en app_dash.py")


def test_funciones_de_tramos_viven_en_proyeccion_tramos():
    """`calcular_tramos` y `prorratear_reservas_anual` son provistas por el módulo
    reutilizable `proyeccion_tramos` (fuente única de esa lógica)."""
    assert callable(ptr.calcular_tramos)
    assert callable(ptr.prorratear_reservas_anual)


def test_orquestador_reusa_proyeccion_tramos():
    """
    El orquestador por tramos de `app_dash.py` importa `proyeccion_tramos as ptr` y
    llama `ptr.calcular_tramos` y `ptr.prorratear_reservas_anual` en vez de
    reimplementar esa lógica.
    """
    fuente = _fuente_funcion_app_dash("_ejecutar_escenario_por_tramos")

    assert re.search(r"import\s+proyeccion_tramos\s+as\s+ptr", fuente), (
        "El orquestador debe importar proyeccion_tramos as ptr"
    )
    assert "ptr.calcular_tramos" in fuente, (
        "El orquestador debe REUSAR ptr.calcular_tramos (no reimplementarlo)"
    )
    assert "ptr.prorratear_reservas_anual" in fuente, (
        "El orquestador debe REUSAR ptr.prorratear_reservas_anual (no reimplementarlo)"
    )


def test_orquestador_no_reimplementa_tramos_localmente():
    """
    El orquestador NO define funciones locales `calcular_tramos` /
    `prorratear_reservas_anual` (evita reimplementar la maquinaria reutilizada).
    """
    fuente = _fuente_funcion_app_dash("_ejecutar_escenario_por_tramos")
    assert "def calcular_tramos" not in fuente
    assert "def prorratear_reservas_anual" not in fuente


def test_lector_no_reimplementa_maquinaria_de_tramos():
    """
    `compras_ventas_reservas` NO define su propia `calcular_tramos` /
    `prorratear_reservas_anual`: la maquinaria de tramos se reutiliza, no se
    reimplementa (Req 12.3).
    """
    assert not hasattr(cvr, "calcular_tramos"), (
        "compras_ventas_reservas NO debe definir su propia calcular_tramos"
    )
    assert not hasattr(cvr, "prorratear_reservas_anual"), (
        "compras_ventas_reservas NO debe definir su propia prorratear_reservas_anual"
    )
