# -*- coding: utf-8 -*-
"""
Test de EXPLORACIÓN de la condición del bug — RV Identidad por `id_posicion`
(spec `rv-identidad-posicion-id`, Tarea 1).

Property 1 (Bug Condition / Expected Behavior) del design:
  Para cualquier conjunto de posiciones de RV donde `isBugCondition(X)` es true
  (existen p1, p2 con `p1.id_posicion != p2.id_posicion AND p1.especie == p2.especie`
  y la clave de identidad usada por RV es `titulo`), el flujo RV por tramos SHALL
  identificar cada posición por su `id_posicion` único, de modo que:
    (a) numero_filas_RENTA_VARIABLE(resultado) == numero_posiciones_RV(X)
    (b) toda fila RENTA_VARIABLE tiene id_posicion numérico real y especie != None
    (c) la dedup compara id numérico vs numérico (sin duplicados TITULO vs
        RENTA_VARIABLE ni filas clase_origen NULL espurias)
    (d) el factor de encadenamiento se calcula por id_posicion (sin inflación).

**CRÍTICO**: Este test DEBE FALLAR sobre el código SIN corregir — el fallo CONFIRMA
que el bug existe. NO se debe "arreglar" el test ni el código en esta fase.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9

Enfoque (PBT acotado a casos deterministas reproducibles): se construyen portafolios
sintéticos de RV con varias posiciones de la MISMA especie y distinto `id_posicion`/VM,
y se ejercitan las FUNCIONES REALES de producción que materializan la causa raíz:

  - `app_dash._construir_snapshot_completo_tramo` → arma el snapshot del tramo
    llamando internamente `_filas_cierre_no_rf(df_rv, 'titulo', ...)` (colapso por
    especie + escritura de `id_posicion=str(especie)`), y la deduplicación de fuente
    única (compara id numérico de las filas base `TITULO` contra el string de especie).
  - `proyeccion_tramos_multiactivo.extraer_vm_cierre` (col_id por defecto `'titulo'`)
    → colapsa N posiciones de una especie a 1 (edge case de inflación del P&G).

El flujo de RV emite hoy (ver `proyeccion_renta_variable.proyectar_bloque`) filas con
`titulo`=especie y SIN columna `id_posicion`. Reproducimos esa forma exacta.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y scripts/ a sys.path, pero app_dash vive en la
# RAÍZ del repo. Garantizamos ambas rutas por robustez.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

app_dash = pytest.importorskip("app_dash")
_ptma = pytest.importorskip("proyeccion_tramos_multiactivo")

_CLASE_RV = app_dash._CLASE_ORIGEN_RV  # 'RENTA_VARIABLE'

# El workspace vive en Google Drive: el I/O del entorno puede disparar `too_slow`.
_SUPPRESS = [HealthCheck.too_slow]
_MIN_EJEMPLOS = 100


# ─────────────────────────────────────────────────────────────────────────────
# Fábricas sintéticas que replican la FORMA real de los DataFrames del flujo RV
# ─────────────────────────────────────────────────────────────────────────────

def _df_rv_como_motor(posiciones, fecha_corte="2026-12-31"):
    """
    Construye un DataFrame de salida de RV con la MISMA forma que emite el motor REAL
    POST-fix `proyeccion_renta_variable.proyectar_bloque`: columnas
    (escenario, id_posicion, tipo_rv, compania, titulo, especie, moneda,
     fecha_proyeccion, valor_mercado, pyg_mensual, pyg_acumulado). Cada fila incluye
    `id_posicion` numérico real (clave de identidad) y conserva `titulo`/`especie`
    como etiqueta. `titulo` == especie (sigue siendo NO único, por lo que la condición
    del bug `isBugCondition` — misma especie, distinto id_posicion — se mantiene).

    `posiciones`: lista de dicts {id_posicion, especie, vm, moneda}.
    """
    filas = []
    for pos in posiciones:
        filas.append({
            "escenario": "base",
            "id_posicion": int(pos["id_posicion"]),  # clave de identidad numérica (post-fix)
            "tipo_rv": "rv_local",
            "compania": "CIA",
            "titulo": pos["especie"],          # titulo == especie (etiqueta no única)
            "especie": pos["especie"],         # etiqueta
            "moneda": pos.get("moneda", "COP"),
            "fecha_proyeccion": fecha_corte,
            "valor_mercado": float(pos["vm"]),
            "pyg_mensual": 0.0,
            "pyg_acumulado": 0.0,
        })
    return pd.DataFrame(filas)


def _df_proy_rf_base(posiciones, fecha_corte="2026-12-31"):
    """
    Construye el `df_proy_rf` que entra a `_construir_snapshot_completo_tramo`: arranca de
    `portafolio_clasificado` (df_base), por lo que CONSERVA las posiciones RV como filas
    `clase_origen='TITULO'` con su `id_posicion` NUMÉRICO real (sin proyectar por el motor
    RF). Esto reproduce el escenario donde la deduplicación de fuente única debe eliminar
    estas filas `TITULO` al anexar las filas `RENTA_VARIABLE` valoradas.
    """
    filas = []
    for pos in posiciones:
        filas.append({
            "id_posicion": int(pos["id_posicion"]),   # NUMÉRICO real
            "especie": pos["especie"],
            "valor_mercado": float(pos["vm"]),
            "moneda": pos.get("moneda", "COP"),
            "clase_origen": "TITULO",
            "asset_class_definitivo": "COLCAP",
            "fecha_corte_proyeccion": fecha_corte,
        })
    return pd.DataFrame(filas)


def _snapshot_bug_condition(posiciones, fecha_corte="2026-12-31"):
    """
    Ejecuta la función REAL de producción `_construir_snapshot_completo_tramo` sobre un
    portafolio donde `isBugCondition` es true (varias posiciones misma especie, distinto
    id_posicion). Devuelve el DataFrame del snapshot resultante.
    """
    df_rv = _df_rv_como_motor(posiciones, fecha_corte)
    df_proy_rf = _df_proy_rf_base(posiciones, fecha_corte)
    df_alt = pd.DataFrame()          # sin alternativos en el caso acotado
    deriv_resultado = None           # sin derivados
    return app_dash._construir_snapshot_completo_tramo(
        df_proy_rf, df_rv, df_alt, deriv_resultado, fecha_corte
    )


# ─────────────────────────────────────────────────────────────────────────────
# Aserciones de `expectedBehavior` (comportamiento correcto que el fix debe cumplir)
# ─────────────────────────────────────────────────────────────────────────────

def _es_entero(valor) -> bool:
    """True si `valor` representa un id_posicion numérico (int o string de dígitos)."""
    if valor is None:
        return False
    try:
        int(str(valor))
        return True
    except (TypeError, ValueError):
        return False


def _assert_expected_behavior(snapshot: pd.DataFrame, posiciones: list) -> None:
    """
    Aserta `expectedBehavior(resultado, X)` del design sobre el snapshot. En el código
    SIN corregir estas aserciones FALLAN (demuestran el bug).
    """
    n_posiciones = len(posiciones)
    ids_reales = {int(p["id_posicion"]) for p in posiciones}

    rv = snapshot[snapshot["clase_origen"] == _CLASE_RV].copy()

    # (a) una fila RENTA_VARIABLE por posición real (no colapsadas por especie)
    assert len(rv) == n_posiciones, (
        f"[a] colapso por especie: {len(rv)} filas RENTA_VARIABLE, "
        f"se esperaban {n_posiciones} (una por id_posicion). "
        f"ids en snapshot={list(rv['id_posicion'])}"
    )

    # (b) toda fila RENTA_VARIABLE: id_posicion numérico real y especie != None
    for _, r in rv.iterrows():
        assert _es_entero(r.get("id_posicion")), (
            f"[b] id_posicion NO numérico: {r.get('id_posicion')!r} "
            f"(se guardó la especie string en vez del id numérico)"
        )
        assert int(str(r["id_posicion"])) in ids_reales, (
            f"[b] id_posicion {r.get('id_posicion')!r} no corresponde a una posición real "
            f"{sorted(ids_reales)}"
        )
        especie = r.get("especie", None)
        assert especie is not None and not pd.isna(especie), (
            f"[b] especie=None en fila RENTA_VARIABLE con id_posicion={r.get('id_posicion')!r}"
        )

    # (c) dedup correcta: no quedan filas TITULO de posiciones RV ya proyectadas,
    #     ni duplicados id numérico vs RENTA_VARIABLE, ni filas clase_origen NULL espurias.
    titulo = snapshot[snapshot["clase_origen"].astype(str) == "TITULO"]
    ids_titulo = {int(x) for x in titulo["id_posicion"] if _es_entero(x)}
    duplicados = ids_titulo & ids_reales
    assert not duplicados, (
        f"[c] dedup fallida: {len(duplicados)} posiciones RV aparecen DUPLICADAS "
        f"(fila TITULO sin proyectar + fila RENTA_VARIABLE): ids={sorted(duplicados)}"
    )

    clase = snapshot["clase_origen"]
    n_nulas = int(clase.isna().sum() + (clase.astype(str).str.strip().isin(["", "None", "nan"])).sum())
    assert n_nulas == 0, f"[c] {n_nulas} filas con clase_origen NULL/espuria en el snapshot"


# ==============================================================================
# TEST 1 — Colapso por especie + id_posicion mal poblado + dedup fallida
# ==============================================================================

# Spec rv-identidad-posicion-id, Property 1: Bug Condition / Expected Behavior
# Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
def test_exploracion_colapso_especie_3_posiciones_ao_isa():
    """
    Caso concreto reproducible: 3 posiciones "AO ISA" (ids 1001/1002/1003) con VM
    distinto. El snapshot debe tener 3 filas RENTA_VARIABLE con id_posicion numérico y
    especie poblada, dedup numérica y sin filas NULL.

    EXPECTED (código sin corregir): FALLA — colapsa a 1 fila con id_posicion='AO ISA'
    y especie=None, la dedup no encuentra coincidencias (0 matches) y dejan filas TITULO
    duplicadas.
    """
    posiciones = [
        {"id_posicion": 1001, "especie": "AO ISA", "vm": 10_000_000.0},
        {"id_posicion": 1002, "especie": "AO ISA", "vm": 25_000_000.0},
        {"id_posicion": 1003, "especie": "AO ISA", "vm": 7_000_000.0},
    ]
    snapshot = _snapshot_bug_condition(posiciones)
    _assert_expected_behavior(snapshot, posiciones)


# ==============================================================================
# TEST 2 — Property-based: cualquier portafolio con especies repetidas
# ==============================================================================

@st.composite
def _portafolio_rv_bug(draw):
    """
    Genera un portafolio de RV donde `isBugCondition` es true: al menos una especie con
    >= 2 posiciones de distinto id_posicion. Garantiza ids únicos y VM positivos.
    """
    n_especies = draw(st.integers(min_value=1, max_value=3))
    especies = [f"ESP_{i}" for i in range(n_especies)]
    posiciones = []
    siguiente_id = draw(st.integers(min_value=1000, max_value=5000))
    # Al menos una especie debe tener >= 2 posiciones (condición del bug).
    conteos = draw(
        st.lists(st.integers(min_value=1, max_value=4), min_size=n_especies, max_size=n_especies)
    )
    conteos[0] = max(conteos[0], 2)
    for especie, k in zip(especies, conteos):
        for _ in range(k):
            vm = draw(st.floats(min_value=1_000.0, max_value=1e10,
                                allow_nan=False, allow_infinity=False))
            posiciones.append({"id_posicion": siguiente_id, "especie": especie, "vm": vm})
            siguiente_id += 1
    return posiciones


# Spec rv-identidad-posicion-id, Property 1: Bug Condition / Expected Behavior
# Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS)
@given(posiciones=_portafolio_rv_bug())
def test_exploracion_identidad_unica_por_id_posicion(posiciones):
    """
    Para TODO portafolio con especies repetidas (isBugCondition true), el snapshot debe
    exponer una fila RENTA_VARIABLE por id_posicion con id numérico y especie poblada,
    dedup numérica y sin filas NULL.

    EXPECTED (código sin corregir): FALLA.
    """
    snapshot = _snapshot_bug_condition(posiciones)
    _assert_expected_behavior(snapshot, posiciones)


# ==============================================================================
# TEST 3 — Edge case de inflación: encadenamiento por id_posicion (no por especie)
# ==============================================================================

# Spec rv-identidad-posicion-id, Property 1: Bug Condition / Expected Behavior (d)
# Validates: Requirements 1.7, 1.8, 1.9
def test_exploracion_inflacion_vm_cierre_por_id_posicion():
    """
    Dos posiciones de la MISMA especie con VM MUY distinto. El VM de cierre por posición
    (base del factor de encadenamiento `VM_cierre_previo / VM_inicial_original`) debe
    extraerse por `id_posicion`, de modo que haya UNA entrada por posición con SU propio
    denominador.

    Se ejercita la función REAL `extraer_vm_cierre`, que hoy usa `col_id='titulo'` por
    defecto y colapsa ambas posiciones a una sola entrada (clave = especie), perdiendo el
    denominador propio de cada posición → inflación/compounding del P&G.

    EXPECTED (código sin corregir): FALLA — `extraer_vm_cierre` devuelve 1 sola clave
    ('AO ISA') en vez de 2 (una por id_posicion).
    """
    posiciones = [
        {"id_posicion": 2001, "especie": "AO ISA", "vm": 1_000_000.0},
        {"id_posicion": 2002, "especie": "AO ISA", "vm": 900_000_000.0},
    ]
    df_rv = _df_rv_como_motor(posiciones)

    # Extracción tal como la invoca el flujo RV por tramos POST-fix: el flujo RV pasa
    # explícitamente `col_id="id_posicion"` (el default retrocompatible sigue siendo
    # 'titulo'), de modo que haya UNA entrada por id_posicion con SU propio denominador.
    vm_cierre = _ptma.extraer_vm_cierre(df_rv, col_id="id_posicion")

    # Debe haber UNA entrada por id_posicion real (no colapsada por especie).
    assert len(vm_cierre) == len(posiciones), (
        f"colapso por especie en el encadenamiento: extraer_vm_cierre devolvió "
        f"{len(vm_cierre)} entrada(s) {list(vm_cierre.keys())}, se esperaban "
        f"{len(posiciones)} (una por id_posicion). Esto infla el factor "
        f"VM_cierre_previo/VM_inicial_original al compartir denominador."
    )
    # Cada id_posicion real debe estar presente como clave.
    claves = set(vm_cierre.keys())
    for pos in posiciones:
        assert str(pos["id_posicion"]) in claves, (
            f"id_posicion {pos['id_posicion']} ausente del VM de cierre por posición "
            f"(claves={sorted(claves)}): el encadenamiento no usa id_posicion."
        )
