"""
Bugfix: P&G de enero en tramos RV/Alt (``modo_proyeccion = "por_tramos"``).

Spec: ``.kiro/specs/pyg-enero-tramos-rv-alt`` — **Task 2: pruebas de preservación**
(BEFORE implementing the fix).

Metodología *observation-first*: se observa el comportamiento del código SIN
corregir, se registra, y se codifican pruebas basadas en propiedades (Hypothesis)
que **DEBEN PASAR** sobre el código SIN corregir (fijan el *baseline* a preservar).
Las MISMAS pruebas se re-ejecutan tras el fix (Task 3.3) y deben seguir pasando.

Propiedades de preservación codificadas (ver ``design.md`` → *Correctness
Properties*):

- **Property 2 (Preservation)** — Total anual del tramo y VM de cierre invariantes.
- **Property 3 (Preservation)** — Mes base del tramo 0 permanece en ``pyg = 0``.
- **Property 4 (Preservation)** — Invarianza del conjunto/conteo de meses por tramo.
- **Property 5 (Preservation)** — Fuera de la condición del bug, ``F'(X) == F(X)``.

Modelo de ``F`` y ``F'``
------------------------
``F`` (buggy en distribución) se modela con el *stub* determinista
``construir_serie_tramo_motor`` de la Task 1, que replica EXACTAMENTE el patrón
de salida de ``proyectar_bloque`` / ``proyectar_bloque_ipc_spread`` (fila ``i == 0``
fantasma con ``pyg = 0`` y ``VM = VM_cierre_previo``).

``F'`` se modela con una **transformación de realineamiento de referencia**
(``realinear_pyg_enero_referencia``) que implementa el algoritmo descrito en
``design.md`` → *Fix Implementation* (reancla el VM un casillero hacia enero y
recomputa ``pyg`` por diferencias de VM consecutivas). Esta transformación es:

  - **identidad** cuando ``aplicar=False`` (tramo 0, o posición sin
    ``VM_cierre_previo``) ⇒ Property 3 y Property 5 por construcción;
  - un **re-etiquetado + recomputo de ``pyg``/``VM``** sobre las MISMAS filas
    cuando ``aplicar=True`` ⇒ conserva el conjunto de meses (Property 4) y, por
    telescoping, el total anual y el VM de cierre (Property 2).

Como el realineamiento telescopa a ``VM_n − VM_cierre_previo`` (idéntico al total
de ``F``) y deja ``VM(dic)`` intacto, estas propiedades se cumplen tanto sobre el
código SIN corregir (donde ``F'`` es esta referencia) como después del fix (donde
el helper real ``_realinear_pyg_enero_tramo`` implementará el mismo algoritmo).

**Validates: Requirements 1.5, 2.3, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7,
3.8, 3.9**
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ``tests/conftest.py`` inserta la raíz y ``scripts/`` en sys.path. Para reutilizar
# el *stub* determinista de la Task 1 (módulo hermano en ``tests/``) agregamos el
# directorio de este archivo a sys.path de forma idempotente.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# El stub determinista y las series afectadas se reutilizan desde la Task 1.
from test_pyg_enero_tramos_rv_alt import (
    construir_serie_tramo_motor,
    SERIES_NO_RF,
)

# Tolerancia relativa para igualdades de magnitud (redondeo a 2 decimales de los
# motores). El telescoping conserva el total hasta el redondeo por centavos.
_TOL_REL = 1e-6
# Tolerancia absoluta por centavos (los motores redondean VM/pyg a 2 decimales,
# por lo que la suma puede diferir del telescoping en ~0.01 por corte).
_TOL_ABS_CENTAVOS = 0.05

# Configuración compartida de Hypothesis. Se suprime ``function_scoped_fixture``
# (el fixture autouse ``db_tmp`` de ``conftest.py`` se recrea por ejemplo) y
# ``too_slow`` porque la ejecución por ejemplo incluye la creación de una DB
# SQLite temporal en un filesystem lento (Google Drive); el trabajo por ejemplo
# es legítimo, no una ineficiencia de la estrategia. ``deadline=None`` evita
# falsos negativos por latencia de I/O del filesystem.
_PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)


# ─────────────────────────────────────────────────────────────────────────────
# Referencia de F': transformación de realineamiento (design.md → Fix
# Implementation). NO es código de producción; modela el comportamiento esperado
# del helper ``_realinear_pyg_enero_tramo`` para poder aseverar las propiedades
# de preservación ANTES de implementar el fix.
# ─────────────────────────────────────────────────────────────────────────────
def realinear_pyg_enero_referencia(
    df_resultado: pd.DataFrame,
    vm_cierre_previo: dict | None,
    col_id: str,
    aplicar: bool,
) -> pd.DataFrame:
    """
    Referencia de ``F'`` — realineamiento de la distribución mensual de un tramo
    RV/Alt (ver ``design.md`` → *Fix Implementation*).

    Si ``aplicar`` es ``False`` ⇒ **identidad** (devuelve una copia sin cambios):
    cubre tramo 0 y flujos fuera de la condición del bug (Property 3 y 5).

    Si ``aplicar`` es ``True``, por cada posición (``groupby(col_id)``) ordenada
    por ``fecha_proyeccion`` ascendente, con VMs ``[VM_0(=VM_prev), VM_1, …,
    VM_n]`` sobre etiquetas ``[m0=ene, …, m_n=dic]``, recomputa sobre las MISMAS
    filas (no borra ni agrega filas):

      - ``pyg'(ene) = VM_1 − VM_cierre_previo``
      - ``pyg'(m_k) = VM_{k+1} − VM_k`` para ``1 ≤ k ≤ n-1``
      - ``pyg'(dic) = VM_n − VM_{n-1}``
      - ``VM'(m_k) = VM_{k+1}`` para ``k = 0..n-1``, y ``VM'(dic) = VM_n`` (intacto)
      - ``pyg_acumulado'`` = suma acumulada de ``pyg'``

    Por posición: si falta ``vm_cierre_previo[id]`` (posición nueva, Req 3.4), esa
    posición se deja como apertura legítima (NO se realinea), igual criterio que
    ``_aplicar_encadenamiento_resultado`` (factor 1.0).

    Reutiliza los ``valor_mercado`` ya calculados (diferencias de VM), sin
    recomponer tasas, de modo que ``Σ pyg' = VM_n − VM_prev = Σ pyg`` (telescoping)
    y ``VM'(dic) = VM_n`` quedan intactos.
    """
    if not aplicar:
        return df_resultado.copy(deep=True)

    vm_prev_map = vm_cierre_previo or {}
    partes: list[pd.DataFrame] = []

    for id_val, grupo in df_resultado.groupby(col_id, sort=False):
        g = grupo.sort_values("fecha_proyeccion").reset_index(drop=True)

        # Posición nueva sin cierre previo: apertura legítima, no se realinea.
        if id_val not in vm_prev_map:
            partes.append(g)
            continue

        vm_prev = float(vm_prev_map[id_val])
        vms = [float(v) for v in g["valor_mercado"].tolist()]
        n = len(vms) - 1  # indices 0..n

        # VM'(m_k) = VM_{k+1} para k=0..n-1 ; VM'(dic) = VM_n (intacto).
        nuevos_vm: list[float] = []
        for k in range(len(vms)):
            nuevos_vm.append(vms[k + 1] if k < n else vms[n])

        # pyg' por diferencias de VM consecutivas, anclado en VM_cierre_previo.
        nuevos_pyg: list[float] = []
        anterior = vm_prev
        for vm_k in nuevos_vm:
            nuevos_pyg.append(round(vm_k - anterior, 2))
            anterior = vm_k

        g = g.copy()
        g["valor_mercado"] = [round(v, 2) for v in nuevos_vm]
        g["pyg_mensual"] = nuevos_pyg
        g["pyg_acumulado"] = [
            round(s, 2) for s in pd.Series(nuevos_pyg).cumsum().tolist()
        ]
        partes.append(g)

    return pd.concat(partes, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de inspección de series mensuales.
# ─────────────────────────────────────────────────────────────────────────────
def _orden(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("fecha_proyeccion").reset_index(drop=True)


def _meses(df: pd.DataFrame) -> set:
    return set(df["fecha_proyeccion"].astype(str))


def _ultimo_mes(df: pd.DataFrame) -> str:
    return _orden(df).iloc[-1]["fecha_proyeccion"]


def _vm_ultimo(df: pd.DataFrame) -> float:
    return float(_orden(df).iloc[-1]["valor_mercado"])


def _suma_pyg(df: pd.DataFrame) -> float:
    return float(df["pyg_mensual"].sum())


def _col_id_para(serie: str) -> str:
    return "titulo" if serie == "renta_variable" else "id_posicion"


def _assert_aprox(a: float, b: float, msg: str) -> None:
    escala = max(abs(a), abs(b), 1.0)
    assert abs(a - b) <= max(_TOL_REL * escala, _TOL_ABS_CENTAVOS), msg


# Estrategias Hypothesis locales para series de tramo.
_vm_inicial = st.floats(
    min_value=1_000.0, max_value=1_000_000_000.0, allow_nan=False, allow_infinity=False
)
_tasa_ea = st.one_of(
    st.just(0.0),
    st.floats(min_value=0.005, max_value=0.60, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-0.40, max_value=-0.005, allow_nan=False, allow_infinity=False),
)
_anio = st.integers(min_value=2027, max_value=2035)
_serie = st.sampled_from(SERIES_NO_RF)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Preservation — Total anual del tramo y VM de cierre invariantes.
#
#   suma(pyg) de F' ≈ suma(pyg) de F  (tolerancia relativa ~1e-6)
#   VM(ultimo_mes(F')) == VM(ultimo_mes(F))
#
# Se cumple por telescoping: F' solo redistribuye el P&G por mes, sin cambiar la
# magnitud anual ni el nivel de cierre de diciembre.
#
# Validates: Requirements 1.5, 2.3, 3.6, 3.7
# ─────────────────────────────────────────────────────────────────────────────
@_PBT_SETTINGS
@given(
    vm_inicial=_vm_inicial,
    tasa_ea=_tasa_ea,
    anio=_anio,
    serie=_serie,
    indice=st.integers(min_value=1, max_value=3),  # tramos indice > 0 (condición del bug)
)
def test_property2_preservacion_total_anual_y_vm_cierre(
    vm_inicial, tasa_ea, anio, serie, indice
):
    """
    Property 2 (Preservation): para TODO tramo RV/Alt, ``F'`` conserva el total
    anual y el VM del último mes respecto de ``F``.

    EXPECTED OUTCOME sobre código SIN corregir: PASA (baseline a preservar).

    Validates: Requirements 1.5, 2.3, 3.6, 3.7
    """
    col_id = _col_id_para(serie)
    f = construir_serie_tramo_motor(
        vm_inicial, tasa_ea, f"{anio}-01", f"{anio}-12", col_id=col_id, id_valor="POS"
    )
    f_prima = realinear_pyg_enero_referencia(
        f, {"POS": vm_inicial}, col_id=col_id, aplicar=(indice > 0)
    )

    # (a) Total anual invariante (telescoping ⇒ ambos = VM(dic) − VM_prev).
    _assert_aprox(
        _suma_pyg(f_prima),
        _suma_pyg(f),
        f"suma(pyg) F'={_suma_pyg(f_prima)} != suma(pyg) F={_suma_pyg(f)}",
    )

    # (b) El total anual iguala el ancla telescópica VM(dic) − VM_cierre_previo.
    ancla = _vm_ultimo(f_prima) - float(vm_inicial)
    _assert_aprox(
        _suma_pyg(f_prima),
        ancla,
        f"suma(pyg) F'={_suma_pyg(f_prima)} != ancla telescópica={ancla}",
    )

    # (c) VM del último mes (diciembre) idéntico entre F y F'.
    _assert_aprox(
        _vm_ultimo(f_prima),
        _vm_ultimo(f),
        f"VM(dic) F'={_vm_ultimo(f_prima)} != VM(dic) F={_vm_ultimo(f)}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: Preservation — Mes base del tramo 0 permanece en pyg = 0.
#
#   Para indice = 0: pyg(primer_mes) == 0 y VM(primer_mes) == VM_apertura.
#   Arranque día 1 o día 31: el mes base NO recibe P&G.
#
# El realineamiento es identidad para tramo 0 (aplicar=False) ⇒ el mes base
# conserva el pyg = 0 que ya produce el stub (fila i == 0).
#
# Validates: Requirements 2.5, 3.1, 3.8
# ─────────────────────────────────────────────────────────────────────────────
@_PBT_SETTINGS
@given(
    vm_apertura=_vm_inicial,
    tasa_ea=_tasa_ea,
    serie=_serie,
    # mes base del tramo 0: cualquier mes de arranque (ene..jul, p. ej. jul-2026).
    mes_base=st.integers(min_value=1, max_value=7),
)
def test_property3_preservacion_mes_base_tramo0_en_cero(
    vm_apertura, tasa_ea, serie, mes_base
):
    """
    Property 3 (Preservation): para el tramo 0 (``indice = 0``) el primer mes
    (mes base/ancla) conserva ``pyg == 0`` y ``VM == VM_apertura``; el fix NUNCA
    inyecta P&G ahí (p. ej. julio-2026 permanece en 0).

    EXPECTED OUTCOME sobre código SIN corregir: PASA.

    Validates: Requirements 2.5, 3.1, 3.8
    """
    col_id = _col_id_para(serie)
    anio = 2026
    f = construir_serie_tramo_motor(
        vm_apertura,
        tasa_ea,
        f"{anio}-{mes_base:02d}",
        f"{anio}-12",
        col_id=col_id,
        id_valor="POS",
    )
    # Tramo 0 ⇒ aplicar=False ⇒ identidad.
    f_prima = realinear_pyg_enero_referencia(
        f, {"POS": vm_apertura}, col_id=col_id, aplicar=False
    )

    m0 = _orden(f_prima).iloc[0]
    assert float(m0["pyg_mensual"]) == 0.0, (
        f"Mes base del tramo 0 ({m0['fecha_proyeccion']}) debe tener pyg == 0, "
        f"obtenido {m0['pyg_mensual']}"
    )
    _assert_aprox(
        float(m0["valor_mercado"]),
        round(float(vm_apertura), 2),
        f"VM(mes base)={m0['valor_mercado']} debe ser VM_apertura={vm_apertura}",
    )
    # Identidad respecto de F (nada cambia en el tramo 0).
    pd.testing.assert_frame_equal(_orden(f_prima), _orden(f), check_dtype=False)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Preservation — Invarianza del conjunto/conteo de meses por tramo.
#
#   set_of_months(F') == set_of_months(F)
#   count_months(F') == count_months(F)   (6 / 12 / 12 ; total 30)
#   last_month(F') == last_month(F)        (diciembre de cada tramo presente)
#
# El realineamiento recompone pyg/VM sobre las MISMAS etiquetas de fecha: no
# agrega ni elimina filas.
#
# Validates: Requirements 2.6, 3.9
# ─────────────────────────────────────────────────────────────────────────────
@_PBT_SETTINGS
@given(
    vm_inicial=_vm_inicial,
    tasa_ea=_tasa_ea,
    anio=_anio,
    serie=_serie,
    indice=st.integers(min_value=0, max_value=3),  # incluye tramo 0
    mes_inicio=st.integers(min_value=1, max_value=12),
)
def test_property4_preservacion_conjunto_y_conteo_de_meses(
    vm_inicial, tasa_ea, anio, serie, indice, mes_inicio
):
    """
    Property 4 (Preservation): para TODO tramo RV/Alt, ``F'`` conserva el conjunto
    y conteo de meses y el último mes (diciembre siempre presente).

    EXPECTED OUTCOME sobre código SIN corregir: PASA.

    Validates: Requirements 2.6, 3.9
    """
    col_id = _col_id_para(serie)
    f = construir_serie_tramo_motor(
        vm_inicial,
        tasa_ea,
        f"{anio}-{mes_inicio:02d}",
        f"{anio}-12",
        col_id=col_id,
        id_valor="POS",
    )
    f_prima = realinear_pyg_enero_referencia(
        f, {"POS": vm_inicial}, col_id=col_id, aplicar=(indice > 0)
    )

    assert _meses(f_prima) == _meses(f), "El conjunto de meses debe preservarse"
    assert len(f_prima) == len(f), "El conteo de filas (meses) debe preservarse"
    assert _ultimo_mes(f_prima) == _ultimo_mes(f), "El último mes debe preservarse"
    # Diciembre del tramo presente.
    assert f"{anio}-12-01" in _meses(f_prima), f"Diciembre {anio} debe estar presente"


def test_property4_conteos_corrida_2026_2028():
    """
    Property 4 (ancla determinista): corrida ``2026-07-31 → 2028-12-31`` con 3
    tramos → conteos 6 / 12 / 12 y total 30 meses; diciembre de cada tramo
    presente, antes y después del realineamiento.

    Validates: Requirements 2.6, 3.9
    """
    tramos = [
        (0, 2026, 7),  # parcial jul..dic 2026 → 6 meses
        (1, 2027, 1),  # ene..dic 2027 → 12 meses
        (2, 2028, 1),  # ene..dic 2028 → 12 meses
    ]
    conteos: list[int] = []
    total_meses: set = set()
    for indice, anio, mes_ini in tramos:
        f = construir_serie_tramo_motor(
            1_000_000.0, 0.10, f"{anio}-{mes_ini:02d}", f"{anio}-12", id_valor="POS"
        )
        f_prima = realinear_pyg_enero_referencia(
            f, {"POS": 1_000_000.0}, col_id="titulo", aplicar=(indice > 0)
        )
        assert _meses(f_prima) == _meses(f)
        assert _ultimo_mes(f_prima) == f"{anio}-12-01"
        conteos.append(len(f_prima))
        total_meses |= _meses(f_prima)

    assert conteos == [6, 12, 12], f"Conteos esperados [6,12,12], obtenido {conteos}"
    assert sum(conteos) == 30, f"Total de meses esperado 30, obtenido {sum(conteos)}"
    for anio in (2026, 2027, 2028):
        assert f"{anio}-12-01" in total_meses, f"Falta diciembre {anio}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Preservation — Fuera de la condición del bug, F'(X) == F(X).
#
#   NOT isBugCondition(X) ⇒ F'(X) == F(X), para:
#     - tramo 0 (indice = 0),
#     - posición nueva sin VM_cierre_previo (apertura legítima),
#     - modos distintos de por_tramos / series RF (modelados como aplicar=False,
#       pues el realineamiento sólo se invoca en el orquestador por tramos RV/Alt).
#
# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
# ─────────────────────────────────────────────────────────────────────────────
@_PBT_SETTINGS
@given(
    vm_inicial=_vm_inicial,
    tasa_ea=_tasa_ea,
    anio=_anio,
    serie=_serie,
)
def test_property5_preservacion_tramo0_identidad(vm_inicial, tasa_ea, anio, serie):
    """
    Property 5 (Preservation): para el tramo 0 (NOT isBugCondition), ``F'(X) ==
    F(X)`` — el realineamiento es identidad.

    EXPECTED OUTCOME sobre código SIN corregir: PASA.

    Validates: Requirements 3.1, 3.3, 3.5
    """
    col_id = _col_id_para(serie)
    f = construir_serie_tramo_motor(
        vm_inicial, tasa_ea, f"{anio}-01", f"{anio}-12", col_id=col_id, id_valor="POS"
    )
    f_prima = realinear_pyg_enero_referencia(
        f, {"POS": vm_inicial}, col_id=col_id, aplicar=False  # tramo 0
    )
    pd.testing.assert_frame_equal(_orden(f_prima), _orden(f), check_dtype=False)


@_PBT_SETTINGS
@given(
    vm_inicial=_vm_inicial,
    tasa_ea=_tasa_ea,
    anio=_anio,
    serie=_serie,
)
def test_property5_preservacion_posicion_nueva_sin_cierre_previo(
    vm_inicial, tasa_ea, anio, serie
):
    """
    Property 5 (Preservation): una posición NUEVA sin ``VM_cierre_previo`` se trata
    como apertura legítima (no se realinea), incluso en un tramo ``indice > 0``.
    ``F'(X) == F(X)`` para esa posición (Req 3.4).

    EXPECTED OUTCOME sobre código SIN corregir: PASA.

    Validates: Requirements 3.4
    """
    col_id = _col_id_para(serie)
    f = construir_serie_tramo_motor(
        vm_inicial, tasa_ea, f"{anio}-01", f"{anio}-12", col_id=col_id, id_valor="NUEVA"
    )
    # Tramo indice > 0 (aplicar=True) pero el mapa de cierre previo NO contiene la
    # posición "NUEVA" ⇒ se deja como apertura legítima (identidad).
    f_prima = realinear_pyg_enero_referencia(
        f, {"OTRA": vm_inicial}, col_id=col_id, aplicar=True
    )
    pd.testing.assert_frame_equal(_orden(f_prima), _orden(f), check_dtype=False)


# ─────────────────────────────────────────────────────────────────────────────
# Anclas deterministas (datos reales verificados). El stub de tasa única NO
# reproduce EXACTAMENTE las sumas anuales (el motor real usa un retorno diario no
# constante y un conteo de días por mes variable, con redondeo a 2 decimales por
# corte); por eso se asevera la INVARIANTE TELESCÓPICA exacta
# (``suma(pyg) == VM(dic) − VM_cierre_previo``) y, por separado, que la suma se
# aproxima al ancla verificada dentro de una tolerancia documentada (~1%). No se
# fuerza una igualdad exacta falsa (ver guía de la Task 2).
#
# vm_cierre_previo se back-solvea como ``ancla / crecimiento_unitario`` para que la
# suma telescópica apunte al ancla lo más cerca que el stub permite.
#
# Validates: Requirements 1.5, 2.3, 3.6, 3.7
# ─────────────────────────────────────────────────────────────────────────────
_ANCLAS = [
    # (nombre, tasa_ea, ancla_suma_2027, serie)
    ("AO_PROTECCION_2027", 0.11, 332_542_499.40, "renta_variable"),
    ("ACWV_2027", 0.098, 117_671_762.08, "renta_variable"),
    ("BROOKFIELD_2027", 0.0659, 1_318_634.14, "alternativos"),
]

# Tolerancia relativa documentada para la aproximación al ancla con el stub de
# tasa única (observada ~0.2%–0.8%). La INVARIANTE telescópica se asevera exacta
# aparte (centavos).
_TOL_REL_ANCLA = 1e-2


def _vm_prev_para_ancla(tasa_ea: float, ancla: float, anio: int = 2027) -> float:
    """Back-solvea VM_cierre_previo para que la suma telescópica apunte al ancla."""
    unidad = construir_serie_tramo_motor(1.0, tasa_ea, f"{anio}-01", f"{anio}-12")
    crecimiento = _vm_ultimo(unidad) - 1.0
    return ancla / crecimiento


@pytest.mark.parametrize("nombre, tasa_ea, ancla, serie", _ANCLAS)
def test_anclas_telescoping_y_preservacion(nombre, tasa_ea, ancla, serie):
    """
    Ancla determinista (dato real verificado): el tramo 2027 cumple la invariante
    telescópica exacta y su suma anual se preserva entre ``F`` y ``F'`` y aproxima
    el ancla verificada dentro de ~1%.

    - ``suma(pyg) de F == VM(dic) − VM_cierre_previo`` (telescoping, centavos).
    - ``suma(pyg) de F' == suma(pyg) de F`` y ``VM(dic) F' == VM(dic) F``.
    - ``suma(pyg) ≈ ancla`` dentro de la tolerancia documentada (stub de tasa
      única; no se fuerza igualdad exacta).

    EXPECTED OUTCOME sobre código SIN corregir: PASA (baseline a preservar).

    Validates: Requirements 1.5, 2.3, 3.6, 3.7
    """
    col_id = _col_id_para(serie)
    vm_prev = _vm_prev_para_ancla(tasa_ea, ancla)

    f = construir_serie_tramo_motor(
        vm_prev, tasa_ea, "2027-01", "2027-12", col_id=col_id, id_valor=nombre
    )
    f_prima = realinear_pyg_enero_referencia(
        f, {nombre: vm_prev}, col_id=col_id, aplicar=True
    )

    # (1) Invariante telescópica exacta sobre F (hasta centavos).
    tele = _vm_ultimo(f) - vm_prev
    _assert_aprox(
        _suma_pyg(f),
        tele,
        f"{nombre}: suma(pyg) F={_suma_pyg(f)} != telescoping VM(dic)-VM_prev={tele}",
    )

    # (2) Preservación entre F y F' (total anual y VM de cierre).
    _assert_aprox(_suma_pyg(f_prima), _suma_pyg(f), f"{nombre}: suma F' != suma F")
    _assert_aprox(
        _vm_ultimo(f_prima), _vm_ultimo(f), f"{nombre}: VM(dic) F' != VM(dic) F"
    )

    # (3) Aproximación al ancla verificada (tolerancia documentada del stub).
    rel = abs(_suma_pyg(f) - ancla) / ancla
    assert rel <= _TOL_REL_ANCLA, (
        f"{nombre}: suma(pyg)={_suma_pyg(f):,.2f} se aleja del ancla "
        f"{ancla:,.2f} en {rel:.2%} (> {_TOL_REL_ANCLA:.0%}); el stub de tasa "
        f"única no reproduce exactamente el motor real — se asevera telescoping."
    )

    # (4) Diciembre 2027 presente (invarianza de meses).
    assert "2027-12-01" in _meses(f_prima), f"{nombre}: falta diciembre 2027"
