# -*- coding: utf-8 -*-
"""
Property-based test (Hypothesis) de EXPLORACIÓN de la condición de bug para la spec
`uvr-capitalizacion-por-tramos`.

Tarea 1 — Property 1: Bug Condition — "La UVR se capitaliza y encadena por tramos".

CRÍTICO (metodología de bugfix exploratorio)
--------------------------------------------
Este test codifica el COMPORTAMIENTO ESPERADO (F', la orquestación corregida). Sobre el
código SIN corregir DEBE FALLAR: la falla CONFIRMA que el bug existe. Cuando el arreglo
de la tarea 3 esté implementado, este MISMO test debe PASAR (tarea 3.5). No se debe
"arreglar" el test ni el código al observar la falla en esta tarea.

Alcance / enfoque
-----------------
El bug es de ORQUESTACIÓN (`_ejecutar_escenario_por_tramos` en `app_dash.py` y la dataclass
`EstadoEncadenado` en `scripts/proyeccion_tramos_multiactivo.py`). Correr la orquestación
completa exige DB real, snapshots de portafolio, datos del informe 597 (`obtener_trm_para`)
y los cuatro motores; eso no es apto para una prueba de propiedad focalizada.

Por eso el test ejercita el ESTADO OBSERVABLE que el arreglo introduce y que la
orquestación consume end-to-end: la senda de UVR por tramo expresada como
`(uvr_inicio, uvr_cierre)` por tramo y el encadenamiento vía `EstadoEncadenado.uvr_cierre`.
La "función bajo prueba" es el helper de producción `construir_senda_uvr_por_tramos`
(scripts/proyeccion_tramos_multiactivo.py), que la orquestación debe usar para calcular,
por tramo, la UVR de inicio/cierre capitalizando `inflacion_ea` por la fracción de año del
tramo y encadenando el cierre de un tramo como inicio del siguiente, SIN hardcode 411.0.

En el código SIN corregir ese helper NO EXISTE: el test falla al importarlo/usarlo, lo que
constituye el contraejemplo de la causa raíz (la UVR queda plana porque la orquestación no
calcula ni encadena una senda de UVR por tramo; ver `_uvr = float(uvr_t0) if uvr_t0 else
411.0` y la ausencia de `uvr_cierre` en `EstadoEncadenado`).

Las aserciones codifican las Correctness Properties del diseño (Property 1):
  - uvr_crece_segun_inflacion_de_cada_tramo:
        uvr_cierre(tramo_i) == uvr_inicio_i * (1 + infl_ea_i) ** (meses_i / 12)
  - primer_tramo_escalado_por_fraccion_de_anio: el primer tramo parcial (~5 meses) aplica
        (1 + inflacion_ea) ** (meses/12)
  - uvr_cierre(tramo_i) == uvr_inicio(tramo_{i+1})            (encadenamiento)
  - infl_tramo >= 0  ⇒  senda de UVR no decreciente             (monotonía)
  - NOT contiene_hardcode(411.0): con `uvr_t0` vacío la construcción debe ABORTAR con un
        error verificable, no inyectar 411.0.

_Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
"""

from __future__ import annotations

import math
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import proyeccion_tramos as ptr
from proyeccion_tramos_multiactivo import EstadoEncadenado

# El diseño exige property-based testing con Hypothesis (ya presente en el proyecto,
# ver scripts/.hypothesis). Mínimo razonable de ejemplos para una prueba multi-tramo.
_MIN_EJEMPLOS = 100

# Tolerancia numérica para comparaciones de capitalización en coma flotante.
_REL = 1e-9
_ABS = 1e-6


def _aprox(valor: float):
    return pytest.approx(valor, rel=_REL, abs=_ABS)


# ─────────────────────────────────────────────────────────────────────────────
# Función bajo prueba (helper de producción que el arreglo debe proveer).
#
# Se importa de forma perezosa dentro de un helper para que, en el código SIN corregir
# (donde el helper NO existe todavía), el fallo se manifieste como una ASERCIÓN de la
# prueba (contraejemplo documentado) y NO como un error de recolección de pytest.
# ─────────────────────────────────────────────────────────────────────────────

def _construir_senda_uvr_por_tramos(tramos, inflaciones_ea_por_tramo, uvr_t0):
    """
    Invoca el helper de producción `construir_senda_uvr_por_tramos` del módulo
    `proyeccion_tramos_multiactivo`, que el arreglo (tarea 3) debe introducir.

    Contrato esperado (F', comportamiento corregido):
      - Devuelve una lista de dicts por tramo con, al menos, las claves
        `{"indice", "uvr_inicio", "uvr_cierre"}`.
      - `uvr_cierre_i = uvr_inicio_i * (1 + infl_ea_i) ** (sub_horizonte_i / 12)`.
      - `uvr_inicio_0 = float(uvr_t0)`; `uvr_inicio_{i+1} = uvr_cierre_i` (encadenamiento),
        materializado vía `EstadoEncadenado.uvr_cierre`.
      - Si `uvr_t0` es vacío/no válido ⇒ lanza ValueError (abortar sin hardcode 411.0).

    En el código SIN corregir el símbolo no existe → se traduce a una aserción fallida con
    el contraejemplo de la causa raíz.
    """
    import proyeccion_tramos_multiactivo as mod

    fn = getattr(mod, "construir_senda_uvr_por_tramos", None)
    assert fn is not None, (
        "CONTRAEJEMPLO (causa raíz): no existe `construir_senda_uvr_por_tramos` en la "
        "orquestación por tramos. La UVR queda PLANA porque la orquestación fija "
        "`_uvr = float(uvr_t0) if uvr_t0 else 411.0` una sola vez, pasa solo `valor_uvr` "
        "al motor (sin `valor_uvr_cierre`) y no encadena `uvr_cierre` en EstadoEncadenado."
    )
    return fn(tramos, inflaciones_ea_por_tramo, uvr_t0)


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias Hypothesis.
# ─────────────────────────────────────────────────────────────────────────────

# UVR t0 realista para Colombia (> 0, finita).
_uvr_t0_strategy = st.floats(
    min_value=100.0, max_value=1500.0, allow_nan=False, allow_infinity=False
)

# Inflación EA del tramo en decimal, >= 0 (satisface la monotonía no decreciente y la
# condición de bug cuando al menos un tramo es > 0).
_infl_ea_strategy = st.floats(
    min_value=0.0, max_value=0.20, allow_nan=False, allow_infinity=False
)


@st.composite
def _escenario_por_tramos(draw):
    """
    Genera un escenario `por_tramos` multiactivo que satisface `isBugCondition`:
    `modo_proyeccion == "por_tramos"` (implícito), existe (al menos) un título UVR
    (implícito en la senda de UVR que se evalúa) y existe un tramo con inflación != 0.

    Devuelve `(tramos, inflaciones_ea, uvr_t0)` donde:
      - `tramos` proviene del REAL `ptr.calcular_tramos` sobre un horizonte multi-año con
        primer tramo PARCIAL (arranque a mitad de año) para ejercitar Req 2.3.
      - `inflaciones_ea` es una lista alineada 1:1 con `tramos`, con al menos un valor > 0.
    """
    anio_inicio = draw(st.integers(min_value=2024, max_value=2030))
    # Mes de arranque > 1 ⇒ primer tramo parcial (ejercita el escalado por fracción de año).
    mes_inicio = draw(st.integers(min_value=2, max_value=10))
    # Al menos 2 años más de horizonte para tener múltiples tramos encadenados.
    anios_extra = draw(st.integers(min_value=2, max_value=4))

    fecha_inicio = date(anio_inicio, mes_inicio, 1)
    fecha_fin = date(anio_inicio + anios_extra, 12, 31)

    tramos = ptr.calcular_tramos(fecha_inicio, fecha_fin)

    # Una inflación EA por tramo; forzar que AL MENOS UNA sea estrictamente > 0 para
    # satisfacer `existe_tramo_con_inflacion_no_cero` (condición de bug).
    inflaciones = draw(
        st.lists(_infl_ea_strategy, min_size=len(tramos), max_size=len(tramos))
    )
    idx_no_cero = draw(st.integers(min_value=0, max_value=len(tramos) - 1))
    bump = draw(st.floats(min_value=0.01, max_value=0.20,
                          allow_nan=False, allow_infinity=False))
    inflaciones[idx_no_cero] = bump

    uvr_t0 = draw(_uvr_t0_strategy)
    return tramos, inflaciones, uvr_t0


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (random): la UVR capitaliza por tramo, encadena y es monótona.
# ─────────────────────────────────────────────────────────────────────────────

# Spec: uvr-capitalizacion-por-tramos — Property 1: Bug Condition — La UVR se capitaliza
# y encadena por tramos. Para toda entrada con isBugCondition(X)=true, la orquestación
# corregida hace crecer la UVR dentro de cada tramo según `inflacion_ea` escalada por la
# fracción de año del tramo, encadena uvr_cierre(i)=uvr_inicio(i+1), garantiza monotonía
# no decreciente para infl>=0 y no usa el hardcode 411.0.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None)
@given(escenario=_escenario_por_tramos())
def test_prop1_uvr_capitaliza_y_encadena_por_tramos(escenario):
    """
    **Property 1: Bug Condition — La UVR se capitaliza y encadena por tramos**

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

    EXPECTED OUTCOME sobre el código SIN corregir: FALLA (prueba que el bug existe).
    """
    tramos, inflaciones, uvr_t0 = escenario

    senda = _construir_senda_uvr_por_tramos(tramos, inflaciones, uvr_t0)

    assert len(senda) == len(tramos), (
        "La senda de UVR debe tener un punto (uvr_inicio, uvr_cierre) por tramo."
    )

    # El inicio del PRIMER tramo es exactamente uvr_t0 (sin hardcode 411.0).
    assert float(senda[0]["uvr_inicio"]) == _aprox(float(uvr_t0))

    uvr_cierre_previo = None
    for i, (tramo, infl_ea) in enumerate(zip(tramos, inflaciones)):
        punto = senda[i]
        uvr_inicio = float(punto["uvr_inicio"])
        uvr_cierre = float(punto["uvr_cierre"])
        meses = tramo.sub_horizonte

        # (2.1 / 2.3) uvr_crece_segun_inflacion_de_cada_tramo + escalado por fracción de año.
        factor = (1.0 + float(infl_ea)) ** (meses / 12.0)
        esperado_cierre = uvr_inicio * factor
        assert uvr_cierre == _aprox(esperado_cierre), (
            f"Tramo {i}: uvr_cierre debe ser uvr_inicio*(1+infl)^(meses/12). "
            f"CONTRAEJEMPLO esperado en el bug: la UVR queda plana (uvr_cierre==uvr_inicio) "
            f"pese a inflación {infl_ea}."
        )

        # (2.5) Monotonía no decreciente para inflación >= 0.
        assert uvr_cierre >= uvr_inicio - _ABS, (
            f"Tramo {i}: con infl_ea>=0 la UVR no debe decrecer."
        )

        # (2.4) Encadenamiento: inicio del tramo i == cierre del tramo i-1.
        if uvr_cierre_previo is not None:
            assert uvr_inicio == _aprox(uvr_cierre_previo), (
                f"Tramo {i}: uvr_inicio debe ser la uvr_cierre del tramo anterior "
                f"(encadenamiento). CONTRAEJEMPLO esperado en el bug: el inicio del tramo "
                f"i+1 no es el cierre del tramo i."
            )
        uvr_cierre_previo = uvr_cierre


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (determinista): caso reproducible del diseño — horizonte 3 años,
# inflación 5% EA por tramo, UVR t0 = 400 (Scoped PBT: casos concretos reproducibles).
# ─────────────────────────────────────────────────────────────────────────────

def test_prop1_caso_determinista_3anios_5pct_uvr400():
    """
    **Property 1 (caso determinista del diseño)**

    **Validates: Requirements 2.1, 2.3, 2.4**

    Horizonte de 3 años calendario completos, inflación 5% EA por tramo, UVR t0 = 400.
    La UVR de cierre del tercer tramo debe ser 400 * 1.05**3 ≈ 463.05.

    EXPECTED OUTCOME sobre el código SIN corregir: FALLA (la UVR queda plana en 400).
    """
    tramos = ptr.calcular_tramos(date(2026, 1, 1), date(2028, 12, 31))
    # Tres tramos anuales completos (12 meses c/u).
    assert len(tramos) == 3
    for t in tramos:
        assert t.sub_horizonte == 12

    inflaciones = [0.05, 0.05, 0.05]
    uvr_t0 = 400.0

    senda = _construir_senda_uvr_por_tramos(tramos, inflaciones, uvr_t0)

    assert float(senda[0]["uvr_inicio"]) == _aprox(400.0)
    assert float(senda[0]["uvr_cierre"]) == _aprox(400.0 * 1.05)
    assert float(senda[1]["uvr_inicio"]) == _aprox(400.0 * 1.05)
    assert float(senda[1]["uvr_cierre"]) == _aprox(400.0 * 1.05**2)
    assert float(senda[2]["uvr_cierre"]) == _aprox(400.0 * 1.05**3)
    # Comprobación numérica del valor del diseño (≈ 463.05).
    assert float(senda[2]["uvr_cierre"]) == pytest.approx(463.05, abs=1e-2)


def test_prop1_primer_tramo_parcial_escalado_por_fraccion_de_anio():
    """
    **Property 1 (primer tramo parcial)**

    **Validates: Requirement 2.3**

    Primer tramo parcial de ~5 meses (ago→dic), inflación 6% EA: la UVR debe crecer
    (1 + 0.06) ** (5/12) ≈ 1.0245 (≈ +2.45%), no quedar plana.

    EXPECTED OUTCOME sobre el código SIN corregir: FALLA.
    """
    tramos = ptr.calcular_tramos(date(2026, 8, 1), date(2027, 12, 31))
    primer = tramos[0]
    meses = primer.sub_horizonte
    assert meses == 5  # ago..dic inclusive

    uvr_t0 = 400.0
    infl_ea = 0.06
    inflaciones = [infl_ea] + [0.0] * (len(tramos) - 1)

    senda = _construir_senda_uvr_por_tramos(tramos, inflaciones, uvr_t0)

    factor = (1.0 + infl_ea) ** (meses / 12.0)
    assert float(senda[0]["uvr_inicio"]) == _aprox(uvr_t0)
    assert float(senda[0]["uvr_cierre"]) == _aprox(uvr_t0 * factor)
    # El factor del tramo parcial es estrictamente menor que (1+infl) de un año completo.
    assert factor < (1.0 + infl_ea)
    assert factor == pytest.approx(1.02458, abs=1e-4)


def test_prop1_sin_uvr_t0_aborta_sin_hardcode_411():
    """
    **Property 1 (edge case): NOT contiene_hardcode(411.0)**

    **Validates: Requirement 2.6**

    Con `uvr_t0` vacío/no válido la construcción debe ABORTAR con un error verificable
    (patrón idéntico en espíritu al de la TRM), NO inyectar el hardcode 411.0.

    EXPECTED OUTCOME sobre el código SIN corregir: FALLA (hoy se inyecta 411.0).
    """
    tramos = ptr.calcular_tramos(date(2026, 1, 1), date(2027, 12, 31))
    inflaciones = [0.05] * len(tramos)

    for uvr_vacia in (None, "", 0, 0.0):
        with pytest.raises((ValueError, TypeError)):
            senda = _construir_senda_uvr_por_tramos(tramos, inflaciones, uvr_vacia)
            # Si no aborta, al menos NO debe haber inyectado el hardcode 411.0.
            for punto in senda:
                assert not math.isclose(float(punto["uvr_inicio"]), 411.0, abs_tol=1e-6), (
                    "CONTRAEJEMPLO (causa raíz): con uvr_t0 vacío se inyecta el hardcode "
                    "411.0 en vez de abortar con error verificable."
                )


def test_prop1_estado_encadenado_soporta_uvr_cierre():
    """
    **Property 1 (encadenamiento vía EstadoEncadenado)**

    **Validates: Requirements 2.4, 3.4**

    El encadenamiento de la UVR entre tramos se materializa añadiendo `uvr_cierre` al
    `EstadoEncadenado` (análogo a `trm_cierre`/`repo_cierre`/`inflacion_cierre`).

    EXPECTED OUTCOME sobre el código SIN corregir: FALLA (EstadoEncadenado no tiene el
    campo `uvr_cierre`).
    """
    estado = EstadoEncadenado()
    assert hasattr(estado, "uvr_cierre"), (
        "CONTRAEJEMPLO (causa raíz): EstadoEncadenado no arrastra `uvr_cierre`, por lo que "
        "el inicio de cada tramo no toma la UVR de cierre del tramo anterior."
    )
    # Debe aceptar el valor por constructor y arrastrarlo intacto.
    estado2 = EstadoEncadenado(uvr_cierre=463.05)
    assert float(estado2.uvr_cierre) == pytest.approx(463.05, abs=1e-6)
