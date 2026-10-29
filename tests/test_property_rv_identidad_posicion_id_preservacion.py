# -*- coding: utf-8 -*-
"""
Tests de PRESERVACIÓN basados en propiedades — RV Identidad por `id_posicion`
(spec `rv-identidad-posicion-id`, Tarea 2).

Property 2 (Preservation) del design:
  Para CUALQUIER entrada donde `isBugCondition(X)` es FALSE (posiciones de
  Alternativos/Derivados/RF, modo punta a punta, o RV SIN especies repetidas), el
  flujo corregido F' SHALL producir el MISMO resultado que el flujo original F,
  preservando:
    - la clave de identidad propia de Alternativos/RF (que YA usan `id_posicion`),
    - el comportamiento punta a punta,
    - la firma pública de `proyectar_renta_variable`,
    - la presencia de la columna de etiqueta `titulo`/`especie` en la salida de RV,
    - la escritura por diseño de los literales de `clase_origen`
      `RENTA_VARIABLE`/`ALTERNATIVO`/`DERIVADO` (Req 14.2).

METODOLOGÍA OBSERVATION-FIRST
-----------------------------
Primero se observó el comportamiento del código SIN corregir (ver
`tests/_probe_task2_observacion.py`) y luego se escribieron estas propiedades que ASERTAN
ese comportamiento. Observaciones registradas sobre el código sin corregir:

  * Firma pública de `proyectar_renta_variable`:
      ['fecha_inicio','fecha_fin','escenarios_rv_local','escenarios_rv_intl','tasa_pei',
       'ruta_db','conn','senda_trm','trm_inicio_ancla']
  * Literales: _CLASE_ORIGEN_RV='RENTA_VARIABLE', _ALT='ALTERNATIVO', _DERIV='DERIVADO'.
  * `_filas_cierre_no_rf(df, 'id_posicion', ...)` (patrón ya usado por Alternativos):
      una fila por `id_posicion`, `id_posicion` numérico (string de dígitos), `especie`
      poblada, `clase_origen` el literal pasado.
  * `extraer_vm_cierre(df, col_id='id_posicion')`: un entry por posición → {id: VM_cierre}.
  * `calcular_efecto_moneda_tramo(df, col_id='id_posicion')`: un EfectoMonedaTramo por
      posición; efecto moneda = 0 para locales (COP).
  * RV SIN especies repetidas con `col_id='titulo'`: una fila por posición (no hay colapso
      porque la clave es 1:1); la salida de `proyectar_bloque` conserva la columna
      `titulo`/`especie` como etiqueta.

INVARIANTE CENTRAL DE PRESERVACIÓN (F == F')
--------------------------------------------
En el dominio NO buggy la clave es 1:1 (cada `especie`/`titulo` ↔ un único `id_posicion`),
por lo que indexar por `titulo` (flujo F actual) produce EXACTAMENTE el mismo resultado que
indexar por `id_posicion` (flujo F' corregido). Estos tests fijan esa igualdad: así, cuando
el fix cambie RV a `col_id='id_posicion'`, estos tests deben SEGUIR pasando (no hay
regresión para entradas no-buggy).

**EXPECTED OUTCOME (código SIN corregir)**: los tests PASAN (línea base a preservar).

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from __future__ import annotations

import inspect
import os
import sys
from datetime import date

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# La raíz del repo (donde vive app_dash.py) y scripts/ deben ser importables.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

app_dash = pytest.importorskip("app_dash")
_ptma = pytest.importorskip("proyeccion_tramos_multiactivo")
_prv = pytest.importorskip("proyeccion_renta_variable")

_CLASE_RV = app_dash._CLASE_ORIGEN_RV
_CLASE_ALT = app_dash._CLASE_ORIGEN_ALT
_CLASE_DERIV = app_dash._CLASE_ORIGEN_DERIV

# El workspace vive en Google Drive: el I/O del entorno puede disparar `too_slow`.
_SUPPRESS = [HealthCheck.too_slow]
_MAX_EJEMPLOS = 100

# Firma pública observada de `proyectar_renta_variable` (contrato de
# tests/test_tramos_multiactivo_smoke.py:47-54). El fix NO debe añadir params requeridos.
_PARAMS_PUBLICOS_RV = (
    "fecha_inicio", "fecha_fin", "escenarios_rv_local", "escenarios_rv_intl",
    "tasa_pei", "senda_trm",
)


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias — dominio NO buggy (claves 1:1, sin especies repetidas)
# ─────────────────────────────────────────────────────────────────────────────

_ESPECIES = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z
    min_size=2, max_size=6,
)
_MONEDAS = st.sampled_from(["COP", "USD"])
_VM = st.floats(min_value=1_000.0, max_value=1e10, allow_nan=False, allow_infinity=False)


@st.composite
def _portafolio_no_buggy(draw):
    """
    Genera un portafolio NO buggy: cada posición tiene `id_posicion` ÚNICO y `especie`
    ÚNICA (clave 1:1), de modo que `isBugCondition` es False. Es el dominio de preservación
    (Alternativos/Derivados/RF o RV sin especies repetidas).
    """
    n = draw(st.integers(min_value=1, max_value=5))
    especies = draw(st.lists(_ESPECIES, min_size=n, max_size=n, unique=True))
    id0 = draw(st.integers(min_value=1, max_value=9000))
    posiciones = []
    for i, esp in enumerate(especies):
        posiciones.append({
            "id_posicion": id0 + i,
            "especie": esp,
            "moneda": draw(_MONEDAS),
            "vm": draw(_VM),
        })
    return posiciones


def _df_no_rf(posiciones, fecha_corte="2026-12-31"):
    """
    DataFrame de salida de un motor no-RF (RV/Alternativos) con la forma real:
    incluye `id_posicion` (numérico) Y la etiqueta `titulo`/`especie`. En el dominio no
    buggy `titulo == especie` y la clave es 1:1 con `id_posicion`.
    """
    filas = []
    for pos in posiciones:
        filas.append({
            "id_posicion": int(pos["id_posicion"]),
            "titulo": pos["especie"],
            "especie": pos["especie"],
            "compania": "CIA",
            "tipo_rv": "rv_local",
            "moneda": pos["moneda"],
            "fecha_proyeccion": fecha_corte,
            "valor_mercado": float(pos["vm"]),
            "pyg_mensual": 0.0,
            "pyg_acumulado": 0.0,
        })
    return pd.DataFrame(filas)


# ==============================================================================
# PRESERVACIÓN 1 — Firma pública de `proyectar_renta_variable` intacta
# ==============================================================================

# Validates: Requirements 3.3
def test_preservacion_firma_publica_proyectar_renta_variable():
    """
    La firma pública de `proyectar_renta_variable` conserva sus parámetros clave y NO añade
    parámetros requeridos nuevos (contrato de test_tramos_multiactivo_smoke.py:47-54).
    Observación-first: parámetros registrados sobre el código sin corregir.
    """
    fn = getattr(_prv, "proyectar_renta_variable", None)
    assert callable(fn)
    params = inspect.signature(fn).parameters
    for p in _PARAMS_PUBLICOS_RV:
        assert p in params, f"proyectar_renta_variable perdió el parámetro público '{p}'"
    # Ningún parámetro posicional SIN valor por defecto puede ser nuevo. Verificamos que el
    # conjunto de requeridos observado en el código SIN corregir se conserve EXACTAMENTE
    # (el fix no debe añadir ni quitar parámetros requeridos). Línea base registrada por la
    # observación-first (ver tests/_probe_task2_observacion.py).
    requeridos = [
        nombre for nombre, par in params.items()
        if par.default is inspect.Parameter.empty
        and par.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
    ]
    _REQUERIDOS_BASELINE = [
        "fecha_inicio", "fecha_fin", "escenarios_rv_local", "escenarios_rv_intl",
    ]
    assert requeridos == _REQUERIDOS_BASELINE, (
        f"cambió el conjunto de parámetros requeridos de proyectar_renta_variable: {requeridos} "
        f"(línea base: {_REQUERIDOS_BASELINE})"
    )


# ==============================================================================
# PRESERVACIÓN 2 — Literales de clase_origen por diseño (Req 14.2)
# ==============================================================================

# Validates: Requirements 3.5
def test_preservacion_literales_clase_origen():
    """Los literales de `clase_origen` escritos por diseño no cambian (Req 14.2)."""
    assert _CLASE_RV == "RENTA_VARIABLE"
    assert _CLASE_ALT == "ALTERNATIVO"
    assert _CLASE_DERIV == "DERIVADO"


# ==============================================================================
# PRESERVACIÓN 3 — Salida de RV expone la columna de etiqueta titulo/especie
# ==============================================================================

# Validates: Requirements 3.2
@settings(max_examples=_MAX_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(posiciones=_portafolio_no_buggy())
def test_preservacion_etiqueta_titulo_en_salida_rv(posiciones):
    """
    `proyectar_bloque` (motor RV) SIEMPRE expone la columna de etiqueta `titulo` consumida
    por `tools/export_resumen_tramos.py` y `tools/multiescenarios.py`. Observación-first:
    la salida del motor conserva `titulo` por fila con el valor de la posición.
    """
    port = pd.DataFrame([
        {"id_posicion": int(p["id_posicion"]), "compania": "CIA",
         "titulo": p["especie"], "especie": p["especie"], "tipo_rv": "rv_local",
         "moneda": p["moneda"], "valor_mercado": p["vm"]}
        for p in posiciones
    ])
    periodos = [date(2026, 1, 31), date(2026, 2, 28)]
    salida = _prv.proyectar_bloque(port, periodos, 0.08, "base")

    assert "titulo" in salida.columns, "la salida de RV perdió la columna de etiqueta 'titulo'"
    # Cada etiqueta de entrada aparece en la salida (sin pérdida de la columna etiqueta).
    etiquetas_entrada = {p["especie"] for p in posiciones}
    etiquetas_salida = set(salida["titulo"].astype(str))
    assert etiquetas_entrada.issubset(etiquetas_salida), (
        "la salida de RV no conserva todas las etiquetas 'titulo' de entrada"
    )


# ==============================================================================
# PRESERVACIÓN 4 — F == F' para _filas_cierre_no_rf en dominio NO buggy
# ==============================================================================

# Validates: Requirements 3.1, 3.5, 3.7
@settings(max_examples=_MAX_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(posiciones=_portafolio_no_buggy())
def test_preservacion_filas_cierre_no_rf_f_igual_f_prima(posiciones):
    """
    En el dominio NO buggy (clave 1:1), armar el cierre indexando por `titulo` (flujo F
    actual) produce el MISMO conjunto de filas que indexar por `id_posicion` (flujo F'
    corregido), SALVO el valor almacenado en la columna `id_posicion` (que es justamente lo
    que el fix corrige). Verificamos que:
      (a) el conteo de filas es idéntico (no hay colapso ni duplicación), y
      (b) el resto de atributos (valor_mercado, clase_origen, moneda, especie) coincide
          posición a posición (emparejando por la etiqueta especie, única en este dominio).
    Esto fija la invariante de preservación para Alternativos/RF/Derivados y RV no-buggy.
    """
    df = _df_no_rf(posiciones)

    filas_por_titulo = app_dash._filas_cierre_no_rf(df, "titulo", _CLASE_ALT, "2026-12-31")
    filas_por_id = app_dash._filas_cierre_no_rf(df, "id_posicion", _CLASE_ALT, "2026-12-31")

    # (a) mismo número de filas (una por posición en ambos; sin colapso en el dominio 1:1)
    assert len(filas_por_titulo) == len(filas_por_id) == len(posiciones)

    # (b) atributos no-identidad coinciden emparejando por `especie` (única en no-buggy)
    def _por_especie(filas):
        return {f["especie"]: f for f in filas}

    a = _por_especie(filas_por_titulo)
    b = _por_especie(filas_por_id)
    assert set(a) == set(b)
    for esp in a:
        for attr in ("valor_mercado", "clase_origen", "moneda", "especie",
                     "fecha_corte_proyeccion"):
            assert a[esp].get(attr) == b[esp].get(attr), (
                f"preservación rota en atributo '{attr}' para especie {esp!r}"
            )


# ==============================================================================
# PRESERVACIÓN 5 — F == F' para extraer_vm_cierre en dominio NO buggy
# ==============================================================================

# Validates: Requirements 3.1, 3.7
@settings(max_examples=_MAX_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(posiciones=_portafolio_no_buggy())
def test_preservacion_extraer_vm_cierre_f_igual_f_prima(posiciones):
    """
    En el dominio NO buggy (clave 1:1), `extraer_vm_cierre` indexado por `titulo` (F) y por
    `id_posicion` (F') produce los MISMOS valores de VM de cierre por posición (solo cambia
    la etiqueta de la clave). Igualdad de los VALORES del VM de cierre.
    """
    df = _df_no_rf(posiciones)

    vm_titulo = _ptma.extraer_vm_cierre(df, col_id="titulo")
    vm_id = _ptma.extraer_vm_cierre(df, col_id="id_posicion")

    # Mismo número de entradas (una por posición) en ambos.
    assert len(vm_titulo) == len(vm_id) == len(posiciones)
    # Los VM de cierre (ordenados) son idénticos: el fix no altera los valores.
    assert sorted(vm_titulo.values()) == sorted(vm_id.values())
    # Emparejando por especie↔id (1:1), cada VM coincide.
    esp_a_vm = {p["especie"]: float(p["vm"]) for p in posiciones}
    esp_a_id = {p["especie"]: str(p["id_posicion"]) for p in posiciones}
    for esp, vm in esp_a_vm.items():
        assert vm_titulo[esp] == pytest.approx(vm)
        assert vm_id[esp_a_id[esp]] == pytest.approx(vm)


# ==============================================================================
# PRESERVACIÓN 6 — Alternativos: calcular_efecto_moneda_tramo inalterado (col_id='id_posicion')
# ==============================================================================

# Validates: Requirements 3.1
@settings(max_examples=_MAX_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(posiciones=_portafolio_no_buggy())
def test_preservacion_efecto_moneda_alternativos_una_por_posicion(posiciones):
    """
    Alternativos YA usa `col_id='id_posicion'`: su descomposición de efecto moneda produce
    un `EfectoMonedaTramo` por posición y el efecto moneda es 0 para locales (COP). Esta es
    la clave de identidad propia de Alternativos que NO debe cambiar con el fix de RV.
    """
    df = _df_no_rf(posiciones)
    efectos = _ptma.calcular_efecto_moneda_tramo(
        df, activo="alternativos", tramo_indice=0, fecha_corte="2026-12-31",
        trm_inicio=4000.0, trm_cierre=4200.0, col_id="id_posicion",
    )
    # Una entrada por posición (sin colapso por especie, clave propia de Alt intacta).
    assert len(efectos) == len(posiciones)
    ids_reales = {str(p["id_posicion"]) for p in posiciones}
    assert {str(e.id_posicion) for e in efectos} == ids_reales
    # Las posiciones locales (COP) tienen efecto moneda nulo (Req 11.6) — invariante.
    esp_moneda = {str(p["id_posicion"]): p["moneda"] for p in posiciones}
    for e in efectos:
        if esp_moneda[str(e.id_posicion)] == "COP":
            assert e.efecto_moneda_cop == pytest.approx(0.0)


# ==============================================================================
# PRESERVACIÓN 7 — Default retrocompatible de col_id en los helpers del modo por tramos
# ==============================================================================

# Validates: Requirements 3.8
def test_preservacion_defaults_retrocompatibles_col_id():
    """
    Los helpers del modo por tramos conservan `col_id` como parámetro con valor por defecto
    (firma retrocompatible, sin defaults hardcodeados de negocio nuevos). Observación-first:
    `extraer_vm_cierre` y `calcular_efecto_moneda_tramo` tienen `col_id` con default.
    """
    for fn in (_ptma.extraer_vm_cierre, _ptma.calcular_efecto_moneda_tramo):
        params = inspect.signature(fn).parameters
        assert "col_id" in params, f"{fn.__name__} perdió el parámetro 'col_id'"
        assert params["col_id"].default is not inspect.Parameter.empty, (
            f"{fn.__name__}.col_id dejó de tener valor por defecto (firma no retrocompatible)"
        )
