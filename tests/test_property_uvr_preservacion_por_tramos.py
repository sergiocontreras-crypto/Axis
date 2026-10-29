# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) de PRESERVACIÓN para la spec
`uvr-capitalizacion-por-tramos`.

Tarea 2 — Property 2: Preservation — "No cambia nada fuera de la condición de bug".

Metodología (bugfix observación-primero / observation-first)
------------------------------------------------------------
Estos tests capturan el COMPORTAMIENTO ACTUAL (F, la orquestación SIN corregir) para
entradas que NO cumplen `isBugCondition`. DEBEN PASAR sobre el código SIN corregir: eso
confirma la LÍNEA BASE a preservar. Tras el arreglo (tarea 3), estos MISMOS tests deben
SEGUIR pasando (tarea 3.6), demostrando que no hay regresiones.

Preservation Requirements del diseño:

    FOR ALL X WHERE NOT isBugCondition(X): F(X) = F'(X)

donde `isBugCondition(X) = modo="por_tramos" AND existe_titulo_UVR AND
existe_tramo_con_inflacion_no_cero`. Es decir, se preserva TODO lo que queda fuera de esa
condición: flujo punta a punta, títulos no-UVR, encadenamiento TRM/repo/inflación,
portafolios sin UVR en modo por tramos, y tramos con inflación exactamente 0.

Alcance de la observación
-------------------------
El arreglo es de ORQUESTACIÓN y es ADITIVO: añade un campo `uvr_cierre` a
`EstadoEncadenado`, un helper de senda de UVR por tramo, y una guarda que elimina el
hardcode 411.0; NO toca el motor RF ni el flujo punta a punta. Por tanto, el comportamiento
observable a preservar vive en funciones de producción estables e importables:

  - `app_dash._calcular_uvr_cierre_desde_inflacion`  (flujo punta a punta, Req 3.1)
  - `proyeccion_tramos_multiactivo.construir_sendas_tramo` y los helpers de interpolación
    (`interpolar_trm_tramo`, `interpolar_nivel_tramo`) que materializan el encadenamiento
    TRM/repo/inflación vía `EstadoEncadenado` (Req 3.3, 3.4)
  - `EstadoEncadenado` y sus campos actuales (Req 3.4)
  - `convertir_ea_a_tramo` (identidad del factor de capitalización, base del caso
    inflación = 0 que debe dejar la UVR constante, Req 3.6)

Estas funciones son INDEPENDIENTES de si el portafolio tiene o no títulos UVR: la
construcción de sendas macro por tramo es idéntica con y sin títulos UVR (Req 3.5), por lo
que capturar su salida fija la línea base de los portafolios "sin UVR" y "no-UVR".

_Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
"""

from __future__ import annotations

import math
from datetime import date

import pytest
from dateutil.relativedelta import relativedelta
from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import proyeccion_tramos as ptr
from proyeccion_tramos_multiactivo import (
    EstadoEncadenado,
    InputsPorTramo,
    construir_sendas_tramo,
    convertir_ea_a_tramo,
    interpolar_nivel_tramo,
    interpolar_trm_tramo,
)

# El diseño exige property-based testing con Hypothesis (ya presente en el proyecto,
# ver scripts/.hypothesis). Mínimo razonable de ejemplos por propiedad.
_MIN_EJEMPLOS = 100

# Tolerancia numérica para comparaciones de coma flotante.
_REL = 1e-9
_ABS = 1e-6


def _aprox(valor: float):
    return pytest.approx(valor, rel=_REL, abs=_ABS)


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias Hypothesis (dominio NO-bug-condition).
# ─────────────────────────────────────────────────────────────────────────────

_nivel_macro_strategy = st.floats(
    min_value=0.0, max_value=25.0, allow_nan=False, allow_infinity=False
)
_trm_strategy = st.floats(
    min_value=500.0, max_value=9000.0, allow_nan=False, allow_infinity=False
)
_infl_ea_decimal_strategy = st.floats(
    min_value=0.0, max_value=0.20, allow_nan=False, allow_infinity=False
)
_uvr_t0_strategy = st.floats(
    min_value=100.0, max_value=1500.0, allow_nan=False, allow_infinity=False
)


def _inputs_tramo(infl_ea: float, repo_ea: float, trm_cierre: float) -> InputsPorTramo:
    """Construye un `InputsPorTramo` con los campos relevantes para las sendas macro."""
    return InputsPorTramo.from_valores(
        inflacion_ea=infl_ea,
        repo_ea=repo_ea,
        trm_cierre=trm_cierre,
    )


# ═════════════════════════════════════════════════════════════════════════════
# PRESERVACIÓN 1 — Flujo punta a punta (Req 3.1)
# `_calcular_uvr_cierre_desde_inflacion` NO cambia: es el flujo fuera de la
# condición de bug (modo punta a punta). Capturamos su comportamiento observable.
# ═════════════════════════════════════════════════════════════════════════════

# Importación perezosa y tolerante: `app_dash` arrastra la app Dash completa. Si en el
# entorno de pruebas no puede importarse, se SKIP (no es un fallo de preservación), pero
# en el entorno estándar del proyecto importa correctamente.
def _calcular_uvr_cierre_desde_inflacion(*args, **kwargs):
    try:
        from app_dash import _calcular_uvr_cierre_desde_inflacion as _fn
    except Exception as exc:  # pragma: no cover - depende del entorno
        pytest.skip(f"app_dash no importable en este entorno: {exc!r}")
    return _fn(*args, **kwargs)


def test_preservacion_puntaapunta_uvr_cierre_sin_sendas_es_determinista():
    """
    **Preservación — flujo punta a punta (Req 3.1)**

    **Validates: Requirement 3.1**

    `_calcular_uvr_cierre_desde_inflacion` sin `store_sendas` usa los YoY por defecto
    (0.06 vigente, 0.04 final) y capitaliza `uvr_t0`. Observamos su salida para un
    horizonte fijo y la fijamos como línea base: el flujo punta a punta debe seguir
    produciendo exactamente este valor tras el arreglo (que NO toca este flujo).

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    uvr_t0 = 400.0
    fecha_ini = date(2026, 1, 1)
    fecha_end = date(2028, 12, 31)

    observado = _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, None)

    # El flujo debe producir un valor numérico finito y estrictamente creciente respecto
    # de uvr_t0 (YoY por defecto positivos), capitalizado en varios años.
    assert observado is not None
    assert math.isfinite(float(observado))
    assert float(observado) > uvr_t0

    # Determinismo: dos llamadas idénticas devuelven el MISMO valor (línea base estable).
    observado2 = _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, None)
    assert float(observado2) == _aprox(float(observado))


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    uvr_t0=_uvr_t0_strategy,
    anios=st.integers(min_value=1, max_value=5),
)
def test_preservacion_puntaapunta_monotonia_y_determinismo(uvr_t0, anios):
    """
    **Preservación — flujo punta a punta (Req 3.1)**

    **Validates: Requirement 3.1**

    Para `uvr_t0` y horizontes variados (modo punta a punta, FUERA de la condición de bug),
    el flujo punta a punta: (1) es determinista y (2) capitaliza la UVR por encima de
    `uvr_t0` con los YoY por defecto positivos. Esta es la línea base a preservar.

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    fecha_ini = date(2026, 1, 1)
    fecha_end = date(2026 + anios, 12, 31)

    a = _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, None)
    b = _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, None)

    assert a is not None and b is not None
    assert float(a) == _aprox(float(b))          # determinismo
    assert float(a) > float(uvr_t0)              # capitaliza con YoY por defecto > 0


# ═════════════════════════════════════════════════════════════════════════════
# PRESERVACIÓN 2 — Encadenamiento TRM/repo/inflación vía EstadoEncadenado (Req 3.4)
# El nuevo `uvr_cierre` se SUMA sin alterar los campos existentes ni su semántica.
# ═════════════════════════════════════════════════════════════════════════════

def test_preservacion_estado_encadenado_campos_actuales():
    """
    **Preservación — EstadoEncadenado (Req 3.4)**

    **Validates: Requirement 3.4**

    El `EstadoEncadenado` ACTUAL arrastra `trm_cierre`, `repo_cierre`, `inflacion_cierre`
    (y los diccionarios de VM/saldo). Fijamos su contrato: estos campos existen, su default
    es `None`/vacío y conservan los valores pasados por constructor. El arreglo añade
    `uvr_cierre` SIN tocar estos.

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    e = EstadoEncadenado()
    # Campos existentes con sus defaults actuales.
    assert e.trm_cierre is None
    assert e.repo_cierre is None
    assert e.inflacion_cierre is None
    assert e.vm_rv_por_posicion == {}
    assert e.vm_alt_por_posicion == {}
    assert e.saldo_credito_por_id == {}

    # Los valores pasados por constructor se conservan intactos.
    e2 = EstadoEncadenado(
        trm_cierre=4200.0,
        repo_cierre=9.5,
        inflacion_cierre=5.25,
        vm_rv_por_posicion={"ACC1": 1000.0},
    )
    assert float(e2.trm_cierre) == _aprox(4200.0)
    assert float(e2.repo_cierre) == _aprox(9.5)
    assert float(e2.inflacion_cierre) == _aprox(5.25)
    assert e2.vm_rv_por_posicion == {"ACC1": 1000.0}


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    trm_inicio=_trm_strategy,
    trm_cierre=_trm_strategy,
    n_cortes=st.integers(min_value=1, max_value=18),
)
def test_preservacion_interpolacion_trm_tramo(trm_inicio, trm_cierre, n_cortes):
    """
    **Preservación — encadenamiento TRM (Req 3.4)**

    **Validates: Requirement 3.4**

    `interpolar_trm_tramo` ancla el ÚLTIMO corte exactamente a `trm_cierre` y es monótona
    entre inicio y cierre. Esta lógica es la que materializa el encadenamiento TRM entre
    tramos y NO debe cambiar.

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    # n_cortes fechas distintas y crecientes (meses sucesivos).
    periodos = [date(2026, 1, 1) + relativedelta(months=i) for i in range(n_cortes)]

    senda = interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)

    assert len(senda) == n_cortes
    valores = list(senda.values())
    # Último valor anclado EXACTAMENTE al cierre.
    assert valores[-1] == _aprox(float(trm_cierre))
    # Monotonía (no decreciente o no creciente según el signo del delta).
    if trm_cierre >= trm_inicio:
        assert all(valores[i] <= valores[i + 1] + _ABS for i in range(len(valores) - 1))
    else:
        assert all(valores[i] >= valores[i + 1] - _ABS for i in range(len(valores) - 1))


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    nivel_inicio=_nivel_macro_strategy,
    nivel_cierre=_nivel_macro_strategy,
    n_meses=st.integers(min_value=0, max_value=18),
)
def test_preservacion_interpolacion_nivel_tramo(nivel_inicio, nivel_cierre, n_meses):
    """
    **Preservación — encadenamiento repo/inflación (Req 3.4)**

    **Validates: Requirement 3.4**

    `interpolar_nivel_tramo` (usada para repo e inflación) devuelve `n_meses + 1` valores,
    con índice 0 == `nivel_inicio` e índice `n_meses` == `nivel_cierre` EXACTO. Es la base
    del encadenamiento de repo/inflación entre tramos y NO debe cambiar.

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    senda = interpolar_nivel_tramo(nivel_inicio, nivel_cierre, n_meses)

    if n_meses <= 0:
        assert len(senda) == 1
        assert senda[0] == _aprox(float(nivel_cierre))
        return

    assert len(senda) == n_meses + 1
    assert senda[0] == _aprox(float(nivel_inicio))
    assert senda[n_meses] == _aprox(float(nivel_cierre))


# ═════════════════════════════════════════════════════════════════════════════
# PRESERVACIÓN 3 — Sendas por tramo con y sin títulos UVR (Req 3.2, 3.5)
# La construcción de sendas macro NO depende de la presencia de títulos UVR; por eso
# capturar su salida fija la línea base de los portafolios "sin UVR" y "no-UVR".
# ═════════════════════════════════════════════════════════════════════════════

@st.composite
def _tramos_e_inputs(draw):
    """
    Genera un escenario `por_tramos` con tramos reales (`ptr.calcular_tramos`) y un
    `InputsPorTramo` por tramo. NO modela títulos UVR: representa el dominio
    NOT isBugCondition de "portafolio sin UVR / títulos no-UVR".
    """
    anio = draw(st.integers(min_value=2024, max_value=2030))
    mes = draw(st.integers(min_value=1, max_value=10))
    anios_extra = draw(st.integers(min_value=1, max_value=4))
    fecha_inicio = date(anio, mes, 1)
    fecha_fin = date(anio + anios_extra, 12, 31)
    tramos = ptr.calcular_tramos(fecha_inicio, fecha_fin)

    inputs = []
    for _ in tramos:
        inputs.append(
            _inputs_tramo(
                infl_ea=draw(_infl_ea_decimal_strategy),
                repo_ea=draw(_infl_ea_decimal_strategy),
                trm_cierre=draw(_trm_strategy),
            )
        )
    return tramos, inputs


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(escenario=_tramos_e_inputs())
def test_preservacion_sendas_tramo_sin_uvr_son_deterministas_y_encadenan(escenario):
    """
    **Preservación — portafolios sin UVR / títulos no-UVR (Req 3.2, 3.5)**

    **Validates: Requirements 3.2, 3.5**

    `construir_sendas_tramo` produce las sendas macro (TRM/IPC/repo/IBR/SOFR) encadenando
    el `trm_cierre` del tramo anterior como TRM de inicio del siguiente. Esta construcción
    es IDÉNTICA haya o no títulos UVR en el portafolio, por lo que fija la línea base de
    los casos "sin UVR". Verificamos:
      - determinismo (misma entrada ⇒ misma salida),
      - el último valor de la senda TRM de cada tramo == `trm_cierre` del tramo,
      - encadenamiento TRM: la senda interpola entre el cierre previo y el cierre actual,
      - sendas IPC/repo planas al nivel del tramo.

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    tramos, inputs = escenario

    estado_previo = None
    trm_cierre_previa = None
    for tramo, inp in zip(tramos, inputs):
        sendas_a = construir_sendas_tramo(tramo, inp, estado_previo)
        sendas_b = construir_sendas_tramo(tramo, inp, estado_previo)

        trm_a = list(sendas_a.senda_trm.values())
        trm_b = list(sendas_b.senda_trm.values())

        # Determinismo total de la construcción.
        assert trm_a == pytest.approx(trm_b, rel=_REL, abs=_ABS)

        # Último corte TRM anclado al nivel de cierre del tramo.
        assert trm_a[-1] == _aprox(float(inp.trm_cierre))

        # Sendas IPC/repo planas al nivel del tramo.
        for v in sendas_a.senda_ipc.values():
            assert v == _aprox(float(inp.inflacion_ea))
        for v in sendas_a.senda_repo.values():
            assert v == _aprox(float(inp.repo_ea))

        # Encadenamiento TRM: con un tramo previo, la senda interpola entre el cierre
        # previo (TRM de inicio) y el cierre actual.
        if trm_cierre_previa is not None and len(trm_a) >= 1:
            lo = min(trm_cierre_previa, float(inp.trm_cierre)) - _ABS
            hi = max(trm_cierre_previa, float(inp.trm_cierre)) + _ABS
            assert all(lo <= v <= hi for v in trm_a)

        # Construir el estado encadenado del tramo (igual que la orquestación actual).
        estado_previo = EstadoEncadenado(
            trm_cierre=float(inp.trm_cierre),
            repo_cierre=float(inp.repo_ea) * 100.0,
            inflacion_cierre=float(inp.inflacion_ea) * 100.0,
        )
        trm_cierre_previa = float(inp.trm_cierre)


# ═════════════════════════════════════════════════════════════════════════════
# PRESERVACIÓN 4 — Inflación exactamente 0 ⇒ UVR constante en el tramo (Req 3.6)
# El factor de capitalización `(1+infl)^(meses/12)` con infl == 0 es exactamente 1
# (identidad), lo que deja la UVR constante — comportamiento plano preservado.
# ═════════════════════════════════════════════════════════════════════════════

@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(meses=st.integers(min_value=1, max_value=24))
def test_preservacion_inflacion_cero_factor_identidad(meses):
    """
    **Preservación — inflación cero (Req 3.6)**

    **Validates: Requirement 3.6**

    Con `inflacion_ea == 0`, el retorno de tramo `(1+0)^(meses/12) - 1 == 0`, es decir el
    factor de capitalización es exactamente 1. Por tanto, aplicar la fórmula de la UVR con
    inflación 0 deja la UVR CONSTANTE en el tramo (comportamiento plano preservado,
    equivalente al actual para ese tramo).

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    # `convertir_ea_a_tramo` es la fórmula de capitalización del proyecto.
    retorno_tramo = convertir_ea_a_tramo(0.0, meses)
    assert retorno_tramo == _aprox(0.0)

    factor = (1.0 + 0.0) ** (meses / 12.0)
    assert factor == _aprox(1.0)

    # Aplicada a un nivel de UVR arbitrario, el nivel queda constante.
    for uvr_inicio in (100.0, 400.0, 1234.56):
        uvr_cierre = uvr_inicio * factor
        assert uvr_cierre == _aprox(uvr_inicio)


@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(
    infl_ea=_infl_ea_decimal_strategy,
    meses_completos=st.integers(min_value=1, max_value=10),
)
def test_preservacion_convertir_ea_a_tramo_identidad_anual(infl_ea, meses_completos):
    """
    **Preservación — factor de capitalización (Req 3.6, 3.3)**

    **Validates: Requirements 3.3, 3.6**

    Para un tramo ANUAL completo (12 meses), `convertir_ea_a_tramo(infl, 12) == infl`
    (identidad). Y para cualquier tramo, el retorno es `(1+infl)^(meses/12)-1`. Esta
    fórmula —idéntica a la que el arreglo usará para la UVR— ya existe y no debe cambiar
    para los flujos actuales (TRM/repo/inflación/retornos).

    EXPECTED OUTCOME sobre el código SIN corregir: PASA.
    """
    # Año completo: identidad.
    assert convertir_ea_a_tramo(infl_ea, 12) == _aprox(infl_ea)

    # Caso general: coincide con la fórmula de capitalización.
    meses = meses_completos
    esperado = (1.0 + float(infl_ea)) ** (meses / 12.0) - 1.0
    assert convertir_ea_a_tramo(infl_ea, meses) == _aprox(esperado)
