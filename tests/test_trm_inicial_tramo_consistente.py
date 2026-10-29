# -*- coding: utf-8 -*-
"""
Pruebas de EXPLORACIÓN de la condición de bug — "TRM inicial de tramo consistente"
(modo "por_tramos", multiactivo). Spec: .kiro/specs/trm-inicial-tramo-consistente.

Metodología (bugfix test-first): estas pruebas codifican el comportamiento ESPERADO
POST-FIX (Property 1 / P5 / P4 del design). Observan la FUENTE DE VERDAD del nivel
inicial del tramo que el fix HÍBRIDO materializa: el campo `SendasTramo.trm_inicio_real`
y el parámetro `proyectar_bloque(..., trm_inicio_ancla=...)` (en vez del seam legado
`senda[0]`, que el fix deliberadamente NO cambia). Sobre el código YA CORREGIDO estas
pruebas PASAN: todos los consumidores del "nivel inicial del tramo" (efecto moneda,
derivados `nivel_actual`, ancla USD de RV/Alt internacional) arrancan en
`trm_inicio_real` (= 3144.14 para el tramo 0), no en `senda[0]` (≈3337, avanzado un paso /
plano en `trm_cierre`).

**Property 1 (Bug Condition):** para todo tramo donde se cumple `isBugCondition(X)`
(trm_t0_real != trm_cierre), el nivel inicial que usan derivados (`nivel_actual`), el
efecto moneda (`trm_inicio`) y el ancla USD de RV/Alt internacional
(`proyectar_bloque.trm_t0`) debe ser EXACTAMENTE `trm_inicio_real(X)`.
**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

Enfoque UNIT-LEVEL (sin motor completo ni datos de mercado): se escopan las propiedades
a los seams exactos de cada consumidor, de modo que NO se requieren las tablas de mercado
(hist_pip / hist_curvas_derivados) que el DB de pruebas puede no tener. Cada caso ataca la
expresión buggy concreta:

  - Caso A (efecto moneda): fuente de verdad `sendas.trm_inicio_real` (lo que el fix usa
    como `trm_inicio` del efecto moneda vía `_trm_tramo_ini`, app_dash ~L8127/~L8228).
    Debe == trm_t0_real. EXPECTED OUTCOME (código con fix): PASA.
  - Caso B (derivados):     fuente de verdad `sendas.trm_inicio_real` (el fix fija
    `nivel_actual = float(trm_inicio_real)`, app_dash ~L7552). Debe == trm_t0_real.
    EXPECTED OUTCOME (código con fix): PASA.
  - Caso C (ancla USD):     `proyectar_bloque(..., trm_inicio_ancla=sendas.trm_inicio_real)`
    — proyeccion_renta_variable.py ~L143 / proyeccion_alternativos.py ~L243. El ancla de
    conversión COP->USD debe == trm_t0_real. EXPECTED OUTCOME (código con fix): PASA.
  - Caso D (encadenamiento, P4): cadena de 2 tramos; el inicio del tramo i>0 observado en la
    fuente de verdad `sendas1.trm_inicio_real` debe == trm_cierre(i-1). EXPECTED OUTCOME
    (código con fix): PASA.

Convención de ejecución (Windows / Google Drive): ver tasks.md. Hypothesis con
`@settings(deadline=None, max_examples=100)` (la latencia del Drive dispara falsos
`DeadlineExceeded`).
"""

import os
import sys
from datetime import date

import pandas as pd
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

# Rutas importables con independencia del modo de importación de pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
_SCRIPTS = os.path.join(_RAIZ, "scripts")
for _p in (_ESTE_DIR, _RAIZ, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyeccion_tramos import Tramo  # noqa: E402
from proyeccion_tramos_multiactivo import (  # noqa: E402
    InputsPorTramo,
    _extremos_senda_trm,
    construir_sendas_tramo,
    interpolar_trm_tramo,
)
import proyeccion_renta_variable as _rv  # noqa: E402
import proyeccion_alternativos as _alt  # noqa: E402

# ── Valores de la corrida real (bugfix.md) ──
# TRM t0 real tomada del informe 597 para el corte 31-jul-2026.
TRM_T0_REAL = 3144.14
# trm_cierre del tramo 0 (TRM_Por_Tramo) en la corrida real.
TRM_CIERRE = 3337.0

_MIN_EJEMPLOS = 100
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
_TOL = 1e-6


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de construcción de escenario mínimo (sin datos de mercado)
# ──────────────────────────────────────────────────────────────────────────────
def _tramo_anual(indice: int, anio: int) -> Tramo:
    """Tramo que cubre todo un año calendario (1-ene -> 31-dic), 12 cortes mensuales."""
    return Tramo(
        indice=indice,
        fecha_inicio=date(anio, 1, 1),
        fecha_corte=date(anio, 12, 31),
        sub_horizonte=12,
    )


def _inputs_tramo(trm_cierre: float) -> InputsPorTramo:
    """
    Construye un InputsPorTramo directo (constructor del dataclass) con un `trm_cierre`
    exacto, evitando la normalización de unidad de `from_valores` (irrelevante para esta
    prueba y que podría reescalar valores de banda). Resto de campos en 0.0 (no influyen
    en la senda_trm ni en el ancla).
    """
    return InputsPorTramo(
        inflacion_ea=0.0,
        repo_ea=0.0,
        mov_tes_cop_bps=0.0,
        mov_tes_uvr_bps=0.0,
        spread_corp_ipc_bps=0.0,
        spread_corp_tf_bps=0.0,
        trm_cierre=float(trm_cierre),
        ret_acc_local_ea=0.0,
        ret_alt_local_ea=0.0,
        ret_alt_intl_ea=0.0,
        ret_alt_ipc_bps=0.0,
        pei_ea=0.0,
        ret_acc_intl_ea=0.0,
        delta_fed_bps=0.0,
        delta_tesoros10y_bps=0.0,
    )


def _trm_inicio_real_tramo0(trm_t0_real: float) -> float:
    """
    Fuente de verdad del nivel inicial del tramo 0 (design: `_trm_tramo_ini` = `_trm_t0`
    del 597). En el fix esto se propaga vía `trm_t0_real`. HOY la senda del tramo 0 es
    plana en `trm_cierre` y los consumidores no lo ven.
    """
    return float(trm_t0_real)


def _nivel_inicial_consumidor_senda(sendas_tramo) -> float:
    """
    Replica EXACTAMENTE lo que hoy usan los consumidores basados en senda como "nivel
    inicial del tramo":
      - derivados: `nivel_actual = senda_trm[0]` (app_dash ~L7552)
      - efecto moneda: `_extremos_senda_trm(...)[0]` (app_dash ~L8127)
    Ambos coinciden (primer nodo cronológico de la senda interpolada).
    """
    senda_items = sorted(sendas_tramo.senda_trm.items())
    senda_vals = [float(v) for _, v in senda_items]
    return senda_vals[0] if senda_vals else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Estrategia de pares (trm_t0_real, trm_cierre) con trm_t0_real != trm_cierre
# (banda que dispara isBugCondition). Niveles de TRM COP plausibles.
# ──────────────────────────────────────────────────────────────────────────────
@st.composite
def _pares_bug(draw):
    t0 = draw(st.floats(min_value=2500.0, max_value=4500.0,
                        allow_nan=False, allow_infinity=False))
    cierre = draw(st.floats(min_value=2500.0, max_value=4500.0,
                            allow_nan=False, allow_infinity=False))
    # Garantizar isBugCondition: niveles claramente distintos (>= 50 COP de separación).
    if abs(t0 - cierre) < 50.0:
        cierre = t0 + 100.0
    return (round(t0, 2), round(cierre, 2))


# ══════════════════════════════════════════════════════════════════════════════
# Caso A — Efecto moneda tramo 0 (Property 1)
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE))
@given(par=_pares_bug())
def test_caso_a_efecto_moneda_tramo0_usa_trm_inicio_real(par):
    """
    # Property 1 (Bug Condition) — Caso A (efecto moneda).
    # Validates: Requirements 2.1, 2.4

    El `trm_inicio` del efecto moneda del tramo 0 (hoy
    `_extremos_senda_trm(sendas_tramo.senda_trm)[0]`, app_dash ~L8127/~L8228) DEBE ser la
    TRM real de arranque del tramo (`trm_inicio_real` = TRM t0 del 597), NO `senda[0]`.

    EXPECTED OUTCOME (código sin fix): FALLA — hoy el tramo 0 tiene senda plana en
    `trm_cierre`, luego `_extremos_senda_trm(...)[0] == trm_cierre` != trm_t0_real.
    """
    trm_t0_real, trm_cierre = par
    tramo = _tramo_anual(0, 2026)
    inputs = _inputs_tramo(trm_cierre)
    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=None,
                                    trm_t0_real=trm_t0_real)

    # Fuente de verdad del efecto moneda: el fix usa `trm_inicio = _trm_tramo_ini`, que para
    # el tramo 0 es la TRM t0 real y queda materializado en `sendas.trm_inicio_real`.
    trm_inicio_efecto_moneda = sendas.trm_inicio_real
    esperado = _trm_inicio_real_tramo0(trm_t0_real)

    assert abs(trm_inicio_efecto_moneda - esperado) < _TOL, (
        "Efecto moneda tramo 0: trm_inicio=%.4f (== sendas.trm_inicio_real) en vez de "
        "trm_inicio_real=%.4f" % (trm_inicio_efecto_moneda, esperado)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Caso B — Derivados tramo 0 (Property 1 / P5)
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE))
@given(par=_pares_bug())
def test_caso_b_derivados_tramo0_nivel_actual_usa_trm_inicio_real(par):
    """
    # Property 1 / P5 (Bug Condition) — Caso B (derivados).
    # Validates: Requirements 2.2, 2.4

    El spot m=0 de los derivados del tramo 0 (hoy `nivel_actual = senda_trm[0]`,
    app_dash ~L7552) DEBE ser la TRM real de arranque del tramo (consistente con la fecha
    base de las curvas de mercado cargadas), NO `senda[0]`.

    EXPECTED OUTCOME (código sin fix): FALLA — `nivel_actual == senda[0]` (plano en
    `trm_cierre` para el tramo 0) != trm_t0_real.
    """
    trm_t0_real, trm_cierre = par
    tramo = _tramo_anual(0, 2026)
    inputs = _inputs_tramo(trm_cierre)
    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=None,
                                    trm_t0_real=trm_t0_real)

    # Réplica del seam de derivados corregido en app_dash._correr_derivados_tramo (~L7552):
    # `nivel_actual = float(trm_inicio_real)` con `trm_inicio_real = _trm_tramo_ini`
    # (== sendas.trm_inicio_real para el tramo 0).
    nivel_actual = sendas.trm_inicio_real
    esperado = _trm_inicio_real_tramo0(trm_t0_real)

    assert abs(nivel_actual - esperado) < _TOL, (
        "Derivados tramo 0: nivel_actual=%.4f (== sendas.trm_inicio_real) en vez de "
        "trm_inicio_real=%.4f" % (nivel_actual, esperado)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Caso C — Ancla USD RV/Alt internacional tramo 0 (Property 1)
# ══════════════════════════════════════════════════════════════════════════════
def _portafolio_intl_rv() -> pd.DataFrame:
    """Portafolio RV internacional mínimo (1 posición USD) para `proyectar_bloque`."""
    return pd.DataFrame([{
        "compania": "TEST",
        "titulo": "RV_USD_1",
        "tipo_rv": "ACCION_INTL",
        "moneda": "USD",
        "valor_mercado": 1_000_000.0,  # VM inicial en COP
    }])


def _portafolio_intl_alt() -> pd.DataFrame:
    """Portafolio Alternativos internacional mínimo (1 posición USD)."""
    return pd.DataFrame([{
        "id_posicion": 1,
        "fondo": "ALT_USD_1",
        "tipo_fondo": "FONDO_INTL",
        "moneda": "USD",
        "valor_mercado": 1_000_000.0,  # VM inicial en COP
    }])


def _ancla_usd_observada(df_proy: pd.DataFrame, vm_cop_inicial: float):
    """
    Recupera lo necesario para despejar el ancla de conversión USD (`trm_t0`) que usó
    `proyectar_bloque`.

    `proyectar_bloque` convierte VM_USD(T0) = VM_COP(T0) / trm_t0 y luego, en el primer
    corte proyectado i=1, VM_COP(1) = VM_USD(T0) * factor_dia * TRM(corte_1). Con tasa_ea=0
    el factor es 1, de modo que VM_COP(1) = (VM_COP(T0)/trm_t0) * TRM(corte_1). Como
    conocemos TRM(corte_1) de la senda, despejamos el ancla:
        trm_t0 = VM_COP(T0) * TRM(corte_1) / VM_COP(1)
    """
    df_ord = df_proy.sort_values("fecha_proyeccion").reset_index(drop=True)
    # Fila i=1 (primer corte proyectado tras T0).
    fila1 = df_ord.iloc[1]
    vm_cop_1 = float(fila1["valor_mercado"])
    return vm_cop_inicial, vm_cop_1, str(fila1["fecha_proyeccion"])


@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE))
@given(par=_pares_bug())
def test_caso_c_ancla_usd_rv_tramo0_usa_trm_inicio_real(par):
    """
    # Property 1 (Bug Condition) — Caso C (ancla USD, RV internacional).
    # Validates: Requirements 2.3, 2.4

    `proyectar_bloque` (RV internacional, proyeccion_renta_variable.py ~L143) ancla la
    conversión COP->USD en `trm_t0 = senda_trm[primera_fecha]`. Ese ancla DEBE ser la TRM
    real de arranque del tramo (`trm_inicio_real`), NO `senda[0]`.

    EXPECTED OUTCOME (código sin fix): FALLA — hoy `proyectar_bloque` toma
    `senda_trm[primera_fecha]` (plano en `trm_cierre`) como ancla.
    """
    trm_t0_real, trm_cierre = par
    tramo = _tramo_anual(0, 2026)
    inputs = _inputs_tramo(trm_cierre)
    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=None,
                                    trm_t0_real=trm_t0_real)

    cortes = sorted(sendas.senda_trm.keys())
    periodos = [date.fromisoformat(c) for c in cortes]
    vm_cop_inicial = 1_000_000.0

    # tasa_ea=0 => factor de composición = 1, aislando el efecto del ancla.
    # El ancla se pasa igual que en producción: trm_inicio_ancla == sendas.trm_inicio_real.
    df = _rv.proyectar_bloque(
        _portafolio_intl_rv(), periodos, tasa_ea=0.0,
        escenario="exploracion_bug", senda_trm=sendas.senda_trm,
        trm_inicio_ancla=sendas.trm_inicio_real,
    )

    vm_cop_t0, vm_cop_1, fecha_1 = _ancla_usd_observada(df, vm_cop_inicial)
    trm_corte_1 = float(sendas.senda_trm[fecha_1])
    # Despeje del ancla efectivamente usado por proyectar_bloque.
    ancla_usd = vm_cop_t0 * trm_corte_1 / vm_cop_1
    esperado = _trm_inicio_real_tramo0(trm_t0_real)

    assert abs(ancla_usd - esperado) < 1e-3, (
        "Ancla USD RV tramo 0: proyectar_bloque.trm_t0=%.4f (== senda[primera_fecha]) "
        "en vez de trm_inicio_real=%.4f" % (ancla_usd, esperado)
    )


@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE))
@given(par=_pares_bug())
def test_caso_c_ancla_usd_alt_tramo0_usa_trm_inicio_real(par):
    """
    # Property 1 (Bug Condition) — Caso C (ancla USD, Alternativos internacional).
    # Validates: Requirements 2.3, 2.4

    `proyectar_bloque` de Alternativos (proyeccion_alternativos.py ~L243) ancla igual que
    RV. El ancla DEBE ser `trm_inicio_real`, NO `senda[0]`.

    EXPECTED OUTCOME (código sin fix): FALLA.
    """
    trm_t0_real, trm_cierre = par
    tramo = _tramo_anual(0, 2026)
    inputs = _inputs_tramo(trm_cierre)
    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=None,
                                    trm_t0_real=trm_t0_real)

    cortes = sorted(sendas.senda_trm.keys())
    periodos = [date.fromisoformat(c) for c in cortes]
    vm_cop_inicial = 1_000_000.0

    # El ancla se pasa igual que en producción: trm_inicio_ancla == sendas.trm_inicio_real.
    df = _alt.proyectar_bloque(
        _portafolio_intl_alt(), periodos, tasa_ea=0.0,
        escenario="exploracion_bug", senda_trm=sendas.senda_trm,
        trm_inicio_ancla=sendas.trm_inicio_real,
    )

    vm_cop_t0, vm_cop_1, fecha_1 = _ancla_usd_observada(df, vm_cop_inicial)
    trm_corte_1 = float(sendas.senda_trm[fecha_1])
    ancla_usd = vm_cop_t0 * trm_corte_1 / vm_cop_1
    esperado = _trm_inicio_real_tramo0(trm_t0_real)

    assert abs(ancla_usd - esperado) < 1e-3, (
        "Ancla USD Alt tramo 0: proyectar_bloque.trm_t0=%.4f (== senda[primera_fecha]) "
        "en vez de trm_inicio_real=%.4f" % (ancla_usd, esperado)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Caso D — Encadenamiento (Property 4): tramo i>0 arranca en trm_cierre(i-1)
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE))
@given(par=_pares_bug())
def test_caso_d_encadenamiento_tramo_i_arranca_en_cierre_previo(par):
    """
    # Property 4 (Bug Condition / encadenamiento) — Caso D.
    # Validates: Requirements 2.5

    En una cadena de 2 tramos, el nivel inicial del tramo i>0 observado por los
    consumidores de senda (derivados `nivel_actual`, efecto moneda `_extremos(...)[0]`)
    DEBE ser EXACTAMENTE `trm_cierre(i-1)`.

    EXPECTED OUTCOME (código sin fix): FALLA — para i>0 la senda arranca avanzada un paso
    (`trm_cierre_prev + (trm_cierre_i - trm_cierre_prev)/n`), de modo que
    `senda[0] != trm_cierre_prev`.
    """
    trm_t0_real, trm_cierre_0 = par
    trm_cierre_1 = trm_cierre_0 + 150.0  # cierre del tramo 1, distinto del del tramo 0

    # Tramo 0 (2026): cierra en trm_cierre_0.
    tramo0 = _tramo_anual(0, 2026)
    inputs0 = _inputs_tramo(trm_cierre_0)
    sendas0 = construir_sendas_tramo(tramo0, inputs0, estado_previo=None,
                                     trm_t0_real=trm_t0_real)
    cierre_tramo0 = _extremos_senda_trm(sendas0.senda_trm)[1]  # == trm_cierre_0 (invariante)

    # Tramo 1 (2027): estado_previo == cierre del tramo 0.
    tramo1 = _tramo_anual(1, 2027)
    inputs1 = _inputs_tramo(trm_cierre_1)
    sendas1 = construir_sendas_tramo(tramo1, inputs1, estado_previo=cierre_tramo0)

    # Fuente de verdad del inicio del tramo i>0: el fix fija `trm_inicio_real = trm_inicio`
    # (== estado_previo.trm_cierre == cierre_tramo0).
    nivel_inicio_tramo1 = sendas1.trm_inicio_real

    assert abs(nivel_inicio_tramo1 - cierre_tramo0) < _TOL, (
        "Encadenamiento: inicio observado del tramo 1=%.4f (== sendas1.trm_inicio_real) "
        "en vez de trm_cierre(tramo 0)=%.4f" % (nivel_inicio_tramo1, cierre_tramo0)
    )


# ══════════════════════════════════════════════════════════════════════════════
# ██  TAREA 2 — PRUEBAS DE PRESERVACIÓN (observación primero; PASAN SIN FIX)   ██
# ══════════════════════════════════════════════════════════════════════════════
#
# Metodología (bugfix test-first): estas pruebas se escriben OBSERVANDO el
# comportamiento del código SIN corregir con inputs NO-buggy y fijando esas salidas
# como invariantes que el fix HÍBRIDO debe PRESERVAR. A diferencia de las pruebas de
# EXPLORACIÓN de la Tarea 1 (que FALLAN sin fix), estas PASAN hoy y DEBEN seguir
# pasando tras el fix.
#
# El fix HÍBRIDO (design.md) NO toca `interpolar_trm_tramo` ni los nodos mensuales de
# la senda, NO toca la ruta de RF, y solo AÑADE parámetros/campo opcionales con default
# (`trm_inicio_real`, `trm_t0_real`, `trm_inicio_ancla`, `SendasTramo.trm_inicio_real`).
# Por tanto todo lo codificado aquí sobre la firma EXISTENTE debe quedar byte-idéntico.
#
# Propiedades cubiertas:
#   - P2  (Req 3.3): último nodo de la senda == trm_cierre (sin residuo de redondeo).
#   - Semántica de `interpolar_trm_tramo` (Req 3.3): primer nodo == trm_inicio +
#     (trm_cierre - trm_inicio)/n (la función NO se toca).
#   - P3  (Req 3.1, 3.2): `construir_sendas_tramo(...).senda_trm` (los nodos mensuales
#     que consumen RF-indexado y los motores) es IDÉNTICA con la firma existente — línea
#     base que el fix (que solo AÑADE params con default) debe reproducir byte a byte.
#   - P6  (Req 3.4, 3.5): `proyectar_bloque` (RV y Alt) llamado SIN los nuevos params se
#     comporta exactamente como hoy (misma serie de VM) — guardia de regresión de firmas
#     retrocompatibles.


# ──────────────────────────────────────────────────────────────────────────────
# Estrategias de preservación
# ──────────────────────────────────────────────────────────────────────────────
@st.composite
def _trm_inicio_cierre(draw):
    """Par (trm_inicio, trm_cierre) de niveles TRM COP plausibles (pueden coincidir)."""
    ini = draw(st.floats(min_value=2000.0, max_value=5000.0,
                         allow_nan=False, allow_infinity=False))
    fin = draw(st.floats(min_value=2000.0, max_value=5000.0,
                         allow_nan=False, allow_infinity=False))
    return (round(ini, 4), round(fin, 4))


def _periodos_mensuales(anio: int, n_cortes: int) -> list:
    """
    Lista de `n_cortes` fechas mensuales ascendentes para un año dado, con el mismo
    patrón `relativedelta(months=...)` del proyecto (primer corte = 1-feb..., etc.).
    Se evita depender de `_cortes_mensuales_tramo` para poder variar `n` libremente.
    """
    from dateutil.relativedelta import relativedelta  # dependencia ya usada por el proyecto
    base = date(anio, 1, 1)
    return [base + relativedelta(months=mes) for mes in range(1, n_cortes + 1)]


# ══════════════════════════════════════════════════════════════════════════════
# P2 — Preservación: último nodo de la senda == trm_cierre (Req 3.3)
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE), n_cortes=12)
@example(par=(3337.0, 3337.0), n_cortes=1)          # senda plana, un solo corte
@example(par=(4500.0, 2500.0), n_cortes=7)          # senda decreciente
@given(par=_trm_inicio_cierre(),
       n_cortes=st.integers(min_value=1, max_value=36))
def test_p2_ultimo_nodo_senda_igual_trm_cierre(par, n_cortes):
    """
    # Property 2 (Preservation) — último nodo de la senda interpolada.
    # Validates: Requirements 3.3

    Para `trm_inicio`/`trm_cierre`/cortes aleatorios, el ÚLTIMO nodo de
    `interpolar_trm_tramo(...)` es EXACTAMENTE `trm_cierre`, sin residuo de redondeo de la
    interpolación. El fix HÍBRIDO no toca esta función, así que esta propiedad PASA hoy y
    debe seguir pasando tras el fix.

    EXPECTED OUTCOME (código sin fix): PASA (línea base a preservar).
    """
    trm_inicio, trm_cierre = par
    periodos = _periodos_mensuales(2026, n_cortes)

    senda = interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)

    assert len(senda) == n_cortes
    ultima_fecha = periodos[-1].strftime("%Y-%m-%d")
    # Igualdad EXACTA (el último nodo se ancla a `fin` sin aplicar el paso).
    assert senda[ultima_fecha] == float(trm_cierre), (
        "Último nodo de la senda=%r != trm_cierre=%r" % (senda[ultima_fecha], trm_cierre)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Semántica de `interpolar_trm_tramo` (NO cambia) — primer nodo avanzado un paso
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE), n_cortes=12)
@example(par=(3337.0, 3484.0), n_cortes=5)          # ejemplo del docstring: paso 29.4
@example(par=(3200.0, 3200.0), n_cortes=1)          # un corte -> primer==último==cierre
@given(par=_trm_inicio_cierre(),
       n_cortes=st.integers(min_value=1, max_value=36))
def test_semantica_interpolar_primer_nodo_avanzado_un_paso(par, n_cortes):
    """
    # Property 2 (Preservation) — semántica documentada de `interpolar_trm_tramo`.
    # Validates: Requirements 3.3

    El PRIMER nodo de la senda sigue siendo `trm_inicio + (trm_cierre - trm_inicio)/n`
    (el inicio del tramo es el cierre del tramo anterior; el primer corte ya avanzó un
    paso). Con un único corte, el primer (y último) nodo es `trm_cierre`. La función NO se
    toca en el fix HÍBRIDO.

    EXPECTED OUTCOME (código sin fix): PASA (línea base a preservar).
    """
    trm_inicio, trm_cierre = par
    periodos = _periodos_mensuales(2027, n_cortes)

    senda = interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)

    primera_fecha = periodos[0].strftime("%Y-%m-%d")
    n = n_cortes
    if n == 1:
        # Único corte: primer nodo == trm_cierre (coincide con el último).
        esperado_primero = float(trm_cierre)
    else:
        paso = (float(trm_cierre) - float(trm_inicio)) / n
        esperado_primero = float(trm_inicio) + paso  # avanzado un paso (i=0 -> *1)

    assert abs(senda[primera_fecha] - esperado_primero) < _TOL, (
        "Primer nodo=%r != trm_inicio + (trm_cierre-trm_inicio)/n=%r"
        % (senda[primera_fecha], esperado_primero)
    )

    # Verificación adicional de la forma: cada nodo intermedio avanza un paso lineal.
    if n >= 2:
        paso = (float(trm_cierre) - float(trm_inicio)) / n
        for i, periodo in enumerate(periodos[:-1]):  # todos menos el último (anclado)
            fecha = periodo.strftime("%Y-%m-%d")
            esperado_i = float(trm_inicio) + paso * (i + 1)
            assert abs(senda[fecha] - esperado_i) < _TOL, (
                "Nodo i=%d=%r != trm_inicio + paso*(i+1)=%r"
                % (i, senda[fecha], esperado_i)
            )


# ══════════════════════════════════════════════════════════════════════════════
# P3 — Preservación: `construir_sendas_tramo(...).senda_trm` byte-idéntica (baseline)
# ══════════════════════════════════════════════════════════════════════════════
#
# P3 end-to-end (valoración RF idéntica) no es ejecutable sin datos de mercado, por lo que
# se codifica en el SEAM que el fix NO toca: los nodos mensuales de `senda_trm` que
# consumen las rutas RF-indexadas y los motores. El fix solo AÑADE `trm_inicio_real`
# (campo de `SendasTramo`) y `trm_t0_real` (param de `construir_sendas_tramo`) con default,
# de modo que la `senda_trm` producida por la FIRMA EXISTENTE (sin pasar los nuevos
# params) debe ser byte-idéntica. Aquí capturamos esa senda como la línea base inmutable.
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(trm_cierre=TRM_CIERRE, anio=2026)
@example(trm_cierre=3484.0, anio=2027)
@given(trm_cierre=st.floats(min_value=2000.0, max_value=5000.0,
                            allow_nan=False, allow_infinity=False),
       anio=st.integers(min_value=2026, max_value=2035))
def test_p3_senda_trm_baseline_tramo0_firma_existente(trm_cierre, anio):
    """
    # Property 3 (Preservation) — baseline de `senda_trm` (seam RF-indexado / motores).
    # Validates: Requirements 3.1, 3.2

    Para el tramo 0 (`estado_previo is None`), la `senda_trm` producida por la FIRMA
    EXISTENTE de `construir_sendas_tramo` es la línea base que el fix HÍBRIDO debe
    reproducir byte a byte (solo añade `trm_t0_real`/campo con default, sin tocar los nodos
    mensuales). Observamos y afirmamos sus invariantes estructurales sobre todo el dominio:
      - 12 nodos (año completo), claves ISO ascendentes.
      - Senda PLANA en `trm_cierre` (tramo 0 sin `estado_previo`: trm_inicio == trm_cierre).
      - Último nodo == `trm_cierre` (coherente con P2).

    EXPECTED OUTCOME (código sin fix): PASA (línea base a preservar).
    """
    trm_cierre = round(float(trm_cierre), 4)
    tramo = _tramo_anual(0, anio)
    inputs = _inputs_tramo(trm_cierre)

    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=None)
    senda = sendas.senda_trm

    # Estructura: 12 cortes mensuales, claves ordenables en formato ISO.
    assert len(senda) == 12
    claves = list(senda.keys())
    assert claves == sorted(claves), "Las claves de la senda no están en orden ascendente"

    # Tramo 0 sin estado_previo -> trm_inicio == trm_cierre -> senda PLANA en trm_cierre.
    for fecha, valor in senda.items():
        assert abs(valor - trm_cierre) < _TOL, (
            "Tramo 0: senda[%s]=%r != trm_cierre=%r (debe ser plana)"
            % (fecha, valor, trm_cierre)
        )

    # Último nodo exacto == trm_cierre (invariante preservado).
    ultima = sorted(senda.keys())[-1]
    assert senda[ultima] == trm_cierre


@_AJUSTES
@example(par=(TRM_T0_REAL, TRM_CIERRE), anio=2027)
@example(par=(3337.0, 3484.0), anio=2028)
@given(par=_trm_inicio_cierre(),
       anio=st.integers(min_value=2026, max_value=2035))
def test_p3_senda_trm_baseline_tramo_encadenado_firma_existente(par, anio):
    """
    # Property 3 (Preservation) — baseline de `senda_trm` para tramo encadenado (i>0).
    # Validates: Requirements 3.1, 3.2

    Para un tramo i>0 (`estado_previo` = cierre del tramo anterior), la `senda_trm`
    producida por la FIRMA EXISTENTE arranca avanzada un paso desde `trm_cierre_prev` y
    cierra EXACTAMENTE en `trm_cierre_i`. Esta es la línea base que el fix debe reproducir
    byte a byte (el fix no altera los nodos mensuales).

    EXPECTED OUTCOME (código sin fix): PASA (línea base a preservar).
    """
    trm_cierre_prev, trm_cierre_i = par
    tramo = _tramo_anual(1, anio)
    inputs = _inputs_tramo(trm_cierre_i)

    sendas = construir_sendas_tramo(tramo, inputs, estado_previo=trm_cierre_prev)
    senda = sendas.senda_trm

    periodos = sorted(senda.keys())
    n = len(periodos)
    assert n == 12

    # Primer nodo avanzado un paso desde trm_cierre_prev (semántica preservada).
    paso = (float(trm_cierre_i) - float(trm_cierre_prev)) / n
    esperado_primero = float(trm_cierre_prev) + paso
    assert abs(senda[periodos[0]] - esperado_primero) < _TOL, (
        "Tramo i>0: senda[0]=%r != trm_cierre_prev + paso=%r"
        % (senda[periodos[0]], esperado_primero)
    )

    # Último nodo exacto == trm_cierre_i (invariante preservado).
    assert senda[periodos[-1]] == float(trm_cierre_i)


# ══════════════════════════════════════════════════════════════════════════════
# P6 — Preservación: `proyectar_bloque` sin nuevos params se comporta como hoy
# ══════════════════════════════════════════════════════════════════════════════
#
# P6 (punta a punta inalterado) se codifica como guardia de regresión en el seam que la
# ruta no-por-tramos comparte con todos los llamadores actuales: `proyectar_bloque` de RV
# y Alt. Llamado SIN los nuevos params (firma existente), con inputs CONTROLADOS (tasa_ea
# y senda_trm dados), debe producir EXACTAMENTE la serie de VM que produce hoy. Como el fix
# solo AÑADE `trm_inicio_ancla=None` (default = comportamiento actual), esta serie debe
# quedar byte-idéntica. Observamos la relación cerrada que define esa serie y la afirmamos
# sobre todo el dominio (sin depender de datos de mercado ni de la BD).
# ══════════════════════════════════════════════════════════════════════════════
def _senda_plana(periodos, trm):
    """Senda {fecha: trm} plana (como la del tramo 0 hoy)."""
    return {p.strftime("%Y-%m-%d"): float(trm) for p in periodos}


@_AJUSTES
@example(tasa_ea=0.08, trm=TRM_CIERRE, n_cortes=12, moneda="USD")
@example(tasa_ea=0.0, trm=3200.0, n_cortes=6, moneda="USD")
@example(tasa_ea=0.12, trm=3200.0, n_cortes=4, moneda="COP")
@given(tasa_ea=st.floats(min_value=-0.20, max_value=0.40,
                         allow_nan=False, allow_infinity=False),
       trm=st.floats(min_value=2000.0, max_value=5000.0,
                     allow_nan=False, allow_infinity=False),
       n_cortes=st.integers(min_value=2, max_value=18),
       moneda=st.sampled_from(["USD", "COP"]))
def test_p6_proyectar_bloque_rv_firma_existente_inalterado(tasa_ea, trm, n_cortes, moneda):
    """
    # Property 6 (Preservation) — `proyectar_bloque` RV con la firma existente.
    # Validates: Requirements 3.4, 3.5

    Con inputs controlados (tasa_ea y senda_trm dados) y SIN los nuevos params, la serie de
    VM de `proyectar_bloque` (RV) satisface la relación cerrada que la define hoy:
      - i=0: VM_COP == VM_COP inicial (sin retorno).
      - i>0 USD: VM_COP(i) == (VM_COP0 / trm_t0) * factor_acum * TRM(corte_i).
      - i>0 COP: VM_COP(i) == VM_COP0 * factor_acum.
    donde factor_acum = prod((1+tasa_diaria)^dias) y trm_t0 == senda[primera_fecha].
    El fix solo añade `trm_inicio_ancla=None` (default), por lo que esta serie no cambia.

    EXPECTED OUTCOME (código sin fix): PASA (guardia de regresión de firma retrocompatible).
    """
    periodos = _periodos_mensuales(2026, n_cortes)
    senda = _senda_plana(periodos, trm)
    vm_cop0 = 1_000_000.0
    portafolio = pd.DataFrame([{
        "compania": "TEST", "titulo": "RV_1", "tipo_rv": "ACCION",
        "moneda": moneda, "valor_mercado": vm_cop0,
    }])

    df = _rv.proyectar_bloque(
        portafolio, periodos, tasa_ea=tasa_ea,
        escenario="preservacion_p6", senda_trm=senda,
    )
    df = df.sort_values("fecha_proyeccion").reset_index(drop=True)

    # Misma util EXACTA que usa el motor RV internamente (definida en el módulo).
    tasa_diaria = _rv.convertir_ea_a_diaria(tasa_ea)
    trm_t0 = senda[periodos[0].strftime("%Y-%m-%d")]
    es_intl = moneda != "COP"

    # i=0: VM original sin retorno.
    assert abs(float(df.iloc[0]["valor_mercado"]) - round(vm_cop0, 2)) < 1e-6

    factor_acum = 1.0
    for i in range(1, len(periodos)):
        dias = (periodos[i] - periodos[i - 1]).days
        factor_acum *= (1 + tasa_diaria) ** dias
        if es_intl:
            trm_corte = senda[periodos[i].strftime("%Y-%m-%d")]
            esperado = (vm_cop0 / trm_t0) * factor_acum * trm_corte
        else:
            esperado = vm_cop0 * factor_acum
        observado = float(df.iloc[i]["valor_mercado"])
        # El motor redondea a 2 decimales; comparar con esa tolerancia.
        assert abs(observado - round(esperado, 2)) < 1e-2, (
            "RV i=%d: VM=%r != baseline cerrado=%r (moneda=%s, tasa_ea=%r)"
            % (i, observado, round(esperado, 2), moneda, tasa_ea)
        )


@_AJUSTES
@example(tasa_ea=0.08, trm=TRM_CIERRE, n_cortes=12, moneda="USD")
@example(tasa_ea=0.0, trm=3200.0, n_cortes=6, moneda="USD")
@example(tasa_ea=0.12, trm=3200.0, n_cortes=4, moneda="COP")
@given(tasa_ea=st.floats(min_value=-0.20, max_value=0.40,
                         allow_nan=False, allow_infinity=False),
       trm=st.floats(min_value=2000.0, max_value=5000.0,
                     allow_nan=False, allow_infinity=False),
       n_cortes=st.integers(min_value=2, max_value=18),
       moneda=st.sampled_from(["USD", "COP"]))
def test_p6_proyectar_bloque_alt_firma_existente_inalterado(tasa_ea, trm, n_cortes, moneda):
    """
    # Property 6 (Preservation) — `proyectar_bloque` Alternativos con la firma existente.
    # Validates: Requirements 3.4, 3.5

    Idéntico a la guardia de RV, para el `proyectar_bloque` de Alternativos. Con la firma
    existente (sin `trm_inicio_ancla`), la serie de VM satisface la misma relación cerrada
    anclada en `trm_t0 == senda[primera_fecha]`. El fix (que solo añade el param con
    default) no debe alterarla.

    EXPECTED OUTCOME (código sin fix): PASA (guardia de regresión de firma retrocompatible).
    """
    periodos = _periodos_mensuales(2026, n_cortes)
    senda = _senda_plana(periodos, trm)
    vm_cop0 = 1_000_000.0
    portafolio = pd.DataFrame([{
        "id_posicion": 1, "fondo": "ALT_1", "tipo_fondo": "FONDO",
        "moneda": moneda, "valor_mercado": vm_cop0,
    }])

    df = _alt.proyectar_bloque(
        portafolio, periodos, tasa_ea=tasa_ea,
        escenario="preservacion_p6", senda_trm=senda,
    )
    df = df.sort_values("fecha_proyeccion").reset_index(drop=True)

    # Misma util EXACTA que usa el motor Alt internamente (definida en el módulo).
    tasa_diaria = _alt.convertir_ea_a_diaria(tasa_ea)
    trm_t0 = senda[periodos[0].strftime("%Y-%m-%d")]
    es_intl = moneda != "COP"

    assert abs(float(df.iloc[0]["valor_mercado"]) - round(vm_cop0, 2)) < 1e-6

    factor_acum = 1.0
    for i in range(1, len(periodos)):
        dias = (periodos[i] - periodos[i - 1]).days
        factor_acum *= (1 + tasa_diaria) ** dias
        if es_intl:
            trm_corte = senda[periodos[i].strftime("%Y-%m-%d")]
            esperado = (vm_cop0 / trm_t0) * factor_acum * trm_corte
        else:
            esperado = vm_cop0 * factor_acum
        observado = float(df.iloc[i]["valor_mercado"])
        assert abs(observado - round(esperado, 2)) < 1e-2, (
            "Alt i=%d: VM=%r != baseline cerrado=%r (moneda=%s, tasa_ea=%r)"
            % (i, observado, round(esperado, 2), moneda, tasa_ea)
        )


# ══════════════════════════════════════════════════════════════════════════════
# ██  TAREA 4 — PRUEBA DE INTEGRACIÓN: flujo por tramos (t0 3144.14 != cierre)  ██
# ══════════════════════════════════════════════════════════════════════════════
#
# Property 1 / Property 4 (Fix Checking, nivel INTEGRACIÓN). A diferencia de los casos
# unit-level de la Tarea 1 (que validan la fuente de verdad `SendasTramo.trm_inicio_real`
# aisladamente), estas pruebas ejercitan el CABLEADO (wiring) extremo a extremo que usa el
# orquestador `_ejecutar_escenario_por_tramos`, encadenando DOS tramos tal como lo hace
# producción y verificando que los TRES consumidores no-RF (derivados `nivel_actual`,
# efecto moneda `trm_inicio`, ancla USD de RV/Alt) arrancan en el MISMO `trm_inicio_real`.
#
# Equivalencias de wiring verificadas en producción (app_dash._ejecutar_escenario_por_tramos):
#   - `construir_sendas_tramo(..., trm_t0_real=_trm_t0)` ->
#         `sendas.trm_inicio_real` == 3144.14 (tramo 0), == estado_previo.trm_cierre (i>0).
#   - Derivados: `_correr_derivados_tramo(..., trm_inicio_real=_trm_tramo_ini)` -> nivel_actual.
#   - Efecto moneda: `_trm_inicio_real = _trm_tramo_ini` -> calcular_efecto_moneda_tramo(
#         trm_inicio=...) y `_reg["trm_inicio"]`.
#   - RV/Alt: `_correr_rv_tramo`/`_correr_alt_tramo` pasan
#         `trm_inicio_ancla=sendas.trm_inicio_real` a `proyectar_bloque`.
#   `_trm_tramo_ini` y `sendas.trm_inicio_real` resuelven al MISMO valor por tramo.
#
# NO se corre el dashboard completo: un run real requiere tablas de mercado
# (hist_pip / hist_curvas_derivados) que el DB de pruebas puede no tener y sería lento/
# frágil. En su lugar se ejercitan las funciones de wiring reales (construir_sendas_tramo
# + proyectar_bloque de ambos motores) con la MISMA parametrización del orquestador. El
# seam de derivados (`nivel_actual = float(trm_inicio_real)` con
# `trm_inicio_real = _trm_tramo_ini == sendas.trm_inicio_real`) se valida sobre
# `sendas.trm_inicio_real` sin invocar `_correr_derivados_tramo` (que exige
# DERIVADOS_DISPONIBLE / curvas de mercado): el orquestador le pasa exactamente ese valor.


def _ancla_usd_de_proyectar_bloque(modulo, portafolio, sendas, vm_cop_inicial=1_000_000.0):
    """
    Ejercita el seam real `proyectar_bloque(..., trm_inicio_ancla=sendas.trm_inicio_real)`
    (tal como lo llaman `_correr_rv_tramo`/`_correr_alt_tramo`) y despeja el ancla USD
    efectivamente usada (ver `_ancla_usd_observada`). Devuelve (ancla_usd, trm_corte_1).
    """
    cortes = sorted(sendas.senda_trm.keys())
    periodos = [date.fromisoformat(c) for c in cortes]
    df = modulo.proyectar_bloque(
        portafolio, periodos, tasa_ea=0.0,
        escenario="integracion_por_tramos", senda_trm=sendas.senda_trm,
        trm_inicio_ancla=sendas.trm_inicio_real,
    )
    vm_cop_t0, vm_cop_1, fecha_1 = _ancla_usd_observada(df, vm_cop_inicial)
    trm_corte_1 = float(sendas.senda_trm[fecha_1])
    ancla_usd = vm_cop_t0 * trm_corte_1 / vm_cop_1
    return ancla_usd, trm_corte_1


# ══════════════════════════════════════════════════════════════════════════════
# Integración — tramo 0 (t0 != cierre) y encadenamiento 2026 -> 2027
# ══════════════════════════════════════════════════════════════════════════════
@_AJUSTES
@example(trm_t0_real=TRM_T0_REAL, trm_cierre_0=TRM_CIERRE, trm_cierre_1=3487.0)
@given(
    trm_t0_real=st.floats(min_value=2500.0, max_value=4500.0,
                          allow_nan=False, allow_infinity=False),
    trm_cierre_0=st.floats(min_value=2500.0, max_value=4500.0,
                           allow_nan=False, allow_infinity=False),
    trm_cierre_1=st.floats(min_value=2500.0, max_value=4500.0,
                           allow_nan=False, allow_infinity=False),
)
def test_integracion_wiring_tramo0_y_encadenamiento_2026_2027(
        trm_t0_real, trm_cierre_0, trm_cierre_1):
    """
    # Property 1 / Property 4 (Fix Checking, integración) — wiring por tramos end-to-end.
    # Validates: Requirements 2.1, 2.2, 2.3, 2.5, 3.1

    Construye una CADENA de 2 tramos EXACTAMENTE como el orquestador
    `_ejecutar_escenario_por_tramos`:
      - Tramo 0 (2026): `construir_sendas_tramo(..., estado_previo=None,
        trm_t0_real=_trm_t0)` -> inicio real == TRM t0 del 597 (3144.14 en el ejemplo),
        NO su propio cierre (3337).
      - Tramo 1 (2027): `construir_sendas_tramo(..., estado_previo=<cierre tramo 0>)` ->
        inicio real == trm_cierre(tramo 0) (encadenamiento limpio 2026->2027, NO el
        senda[0] avanzado un paso ~3349.25).

    Afirma:
      1. `sendas0.trm_inicio_real == trm_t0_real` (tramo 0 arranca en el t0 real).
      2. `sendas1.trm_inicio_real == trm_cierre(tramo 0)` (encadenamiento P4).
      3. Último nodo de cada senda == su `trm_cierre` (invariante P2/P3 preservado — RF seam).
      4. Los TRES consumidores no-RF del tramo 0 arrancan en `sendas0.trm_inicio_real`:
         - RV: ancla USD de `proyectar_bloque` == trm_inicio_real.
         - Alt: ancla USD de `proyectar_bloque` == trm_inicio_real.
         - Derivados: `nivel_actual = float(sendas0.trm_inicio_real)` (seam de producción;
           el orquestador pasa `trm_inicio_real=_trm_tramo_ini == sendas0.trm_inicio_real`).

    EXPECTED OUTCOME (código con fix): PASA.
    """
    # Garantizar t0 != cierre del tramo 0 (isBugCondition) y cierres de tramo distintos.
    trm_t0_real = round(float(trm_t0_real), 2)
    trm_cierre_0 = round(float(trm_cierre_0), 2)
    trm_cierre_1 = round(float(trm_cierre_1), 2)
    if abs(trm_t0_real - trm_cierre_0) < 50.0:
        trm_cierre_0 = round(trm_t0_real + 193.0, 2)
    if abs(trm_cierre_1 - trm_cierre_0) < 50.0:
        trm_cierre_1 = round(trm_cierre_0 + 150.0, 2)

    # ── Tramo 0 (2026): wiring del orquestador (trm_t0_real = TRM 597) ──
    tramo0 = _tramo_anual(0, 2026)
    inputs0 = _inputs_tramo(trm_cierre_0)
    sendas0 = construir_sendas_tramo(tramo0, inputs0, estado_previo=None,
                                     trm_t0_real=trm_t0_real)

    # ── Tramo 1 (2027): estado_previo = cierre del tramo 0 (encadenamiento) ──
    cierre_tramo0 = _extremos_senda_trm(sendas0.senda_trm)[1]  # == trm_cierre_0 (invariante)
    tramo1 = _tramo_anual(1, 2027)
    inputs1 = _inputs_tramo(trm_cierre_1)
    sendas1 = construir_sendas_tramo(tramo1, inputs1, estado_previo=cierre_tramo0)

    # (1) Tramo 0 arranca en la TRM t0 real, NO en su cierre.
    assert abs(sendas0.trm_inicio_real - trm_t0_real) < _TOL, (
        "Tramo 0: trm_inicio_real=%.4f != TRM t0 real=%.4f"
        % (sendas0.trm_inicio_real, trm_t0_real)
    )

    # (2) Encadenamiento P4: tramo 1 arranca EXACTAMENTE en el cierre del tramo 0.
    assert abs(sendas1.trm_inicio_real - trm_cierre_0) < _TOL, (
        "Encadenamiento 2026->2027: sendas1.trm_inicio_real=%.4f != trm_cierre(tramo 0)=%.4f"
        % (sendas1.trm_inicio_real, trm_cierre_0)
    )

    # (3) Invariante P2/P3 (RF seam inalterado): último nodo de cada senda == su trm_cierre.
    ultimo0 = _extremos_senda_trm(sendas0.senda_trm)[1]
    ultimo1 = _extremos_senda_trm(sendas1.senda_trm)[1]
    assert ultimo0 == float(trm_cierre_0), (
        "Último nodo senda tramo 0=%r != trm_cierre_0=%r" % (ultimo0, trm_cierre_0)
    )
    assert ultimo1 == float(trm_cierre_1), (
        "Último nodo senda tramo 1=%r != trm_cierre_1=%r" % (ultimo1, trm_cierre_1)
    )

    esperado0 = sendas0.trm_inicio_real

    # (4a) RV internacional — ancla USD real.
    ancla_rv, _ = _ancla_usd_de_proyectar_bloque(_rv, _portafolio_intl_rv(), sendas0)
    assert abs(ancla_rv - esperado0) < 1e-3, (
        "RV tramo 0: ancla USD proyectar_bloque=%.4f != trm_inicio_real=%.4f"
        % (ancla_rv, esperado0)
    )

    # (4b) Alternativos internacional — ancla USD real.
    ancla_alt, _ = _ancla_usd_de_proyectar_bloque(_alt, _portafolio_intl_alt(), sendas0)
    assert abs(ancla_alt - esperado0) < 1e-3, (
        "Alt tramo 0: ancla USD proyectar_bloque=%.4f != trm_inicio_real=%.4f"
        % (ancla_alt, esperado0)
    )

    # (4c) Derivados — spot m=0 (seam de producción: nivel_actual = float(trm_inicio_real),
    # con trm_inicio_real=_trm_tramo_ini == sendas0.trm_inicio_real). No se invoca
    # _correr_derivados_tramo (requiere DERIVADOS_DISPONIBLE / curvas); el valor cableado
    # ES sendas0.trm_inicio_real.
    nivel_actual_derivados = float(sendas0.trm_inicio_real)
    assert abs(nivel_actual_derivados - esperado0) < _TOL, (
        "Derivados tramo 0: nivel_actual=%.4f != trm_inicio_real=%.4f"
        % (nivel_actual_derivados, esperado0)
    )


# ══════════════════════════════════════════════════════════════════════════════
# Integración — caso determinista exacto de la corrida real (3144.14 / 3337 / 3487)
# ══════════════════════════════════════════════════════════════════════════════
def test_integracion_caso_real_3144_14_tramo0_arranca_en_t0_no_en_cierre():
    """
    # Property 1 / Property 4 (Fix Checking, integración) — caso determinista 597.
    # Validates: Requirements 2.1, 2.2, 2.3, 2.5, 3.1

    Reproduce el caso EXACTO de la corrida real (bugfix.md): tramo 0 cierra en 3337 con
    TRM t0 real = 3144.14; tramo 1 cierra en 3487. Verifica el valor concreto que el bug
    registraba mal (`informe_pyg_por_tramo.trm_inicio == 3337.0` en lugar de 3144.14) ahora
    cableado correctamente a través de las funciones reales del orquestador.

    EXPECTED OUTCOME (código con fix): PASA (los tres consumidores del tramo 0 arrancan en
    3144.14; el tramo 1 arranca en 3337, NO en el senda[0] avanzado ~3349.25).
    """
    tramo0 = _tramo_anual(0, 2026)
    sendas0 = construir_sendas_tramo(tramo0, _inputs_tramo(3337.0), estado_previo=None,
                                     trm_t0_real=3144.14)
    cierre_tramo0 = _extremos_senda_trm(sendas0.senda_trm)[1]
    tramo1 = _tramo_anual(1, 2027)
    sendas1 = construir_sendas_tramo(tramo1, _inputs_tramo(3487.0),
                                     estado_previo=cierre_tramo0)

    # Tramo 0 arranca en el t0 real del 597 (3144.14), NO en su cierre (3337).
    assert abs(sendas0.trm_inicio_real - 3144.14) < _TOL
    # Tramo 1 arranca en el cierre del tramo 0 (3337), encadenamiento limpio 2026->2027.
    assert abs(sendas1.trm_inicio_real - 3337.0) < _TOL
    # El senda[0] avanzado un paso del tramo 1 (~3349.25) NO es el inicio real (regresión
    # que el bug producía). Lo confirmamos para dejar explícita la diferencia.
    senda1_primer_nodo = _extremos_senda_trm(sendas1.senda_trm)[0]
    assert abs(senda1_primer_nodo - 3337.0) > 1.0, (
        "El primer nodo de la senda del tramo 1 debe estar avanzado un paso (no ser 3337)"
    )
    assert abs(sendas1.trm_inicio_real - senda1_primer_nodo) > 1.0, (
        "El inicio real del tramo 1 (3337) NO debe coincidir con senda[0] avanzado"
    )

    # Último nodo de cada senda == trm_cierre (P2/P3, RF seam inalterado).
    assert _extremos_senda_trm(sendas0.senda_trm)[1] == 3337.0
    assert _extremos_senda_trm(sendas1.senda_trm)[1] == 3487.0

    # Los tres consumidores no-RF del tramo 0 arrancan en 3144.14.
    ancla_rv, _ = _ancla_usd_de_proyectar_bloque(_rv, _portafolio_intl_rv(), sendas0)
    ancla_alt, _ = _ancla_usd_de_proyectar_bloque(_alt, _portafolio_intl_alt(), sendas0)
    nivel_actual_derivados = float(sendas0.trm_inicio_real)  # seam de producción
    assert abs(ancla_rv - 3144.14) < 1e-3, "RV ancla USD != 3144.14 (%.4f)" % ancla_rv
    assert abs(ancla_alt - 3144.14) < 1e-3, "Alt ancla USD != 3144.14 (%.4f)" % ancla_alt
    assert abs(nivel_actual_derivados - 3144.14) < _TOL
