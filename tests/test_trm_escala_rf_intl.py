# -*- coding: utf-8 -*-
"""
Pruebas del bugfix `trm-escala-rf-internacional-por-tramos` (sub-defecto 2B).

Este archivo cubre la metodología de condición del bug:

  - **Task 1 (ESTE bloque): prueba de EXPLORACIÓN** que codifica el comportamiento
    ESPERADO (post-fix, `F'`) de la normalización de unidad de la TRM de cierre.
    **DEBE FALLAR en el código sin corregir** (hoy `InputsPorTramo.from_valores`
    coerciona `trm_cierre` a `float` finito pero NO normaliza la unidad ni valida
    la banda plausible). La falla confirma que el bug existe. Cuando el fix de la
    tarea 3.1 (`normalizar_trm_nivel` + wiring) esté implementado, esta MISMA prueba
    debe PASAR (valida el fix).

Propiedades cubiertas (ver `design.md`):
  - **Property 1 / 3**: una TRM fuera de banda pero normalizable por factores de 1000
    (error de unidad ×1000) se normaliza al nivel plausible dentro de `[500, 20000]`.
  - **Property 4**: una TRM finita, fuera de banda y NO normalizable por 1000 (dato
    arbitrario, p. ej. `150`) se rechaza con `ValueError` (no se valora en silencio).

Banda plausible del diseño: `[500, 20000]`. Política fuera-de-banda no normalizable:
`ValueError`.

Notas de entorno (filesystem Google Drive):
  - `deadline=None` y se suprime `HealthCheck.too_slow` (artefacto de E/S lenta; NO
    relaja las propiedades). Estas pruebas son PURAS (no tocan SQLite).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4**
"""

import os
import sys

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

# Asegura que la raíz del proyecto, `scripts/` y `tests/` estén importables con
# independencia del modo de importación de pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
_SCRIPTS = os.path.join(_RAIZ, "scripts")
for _p in (_ESTE_DIR, _RAIZ, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyeccion_tramos_multiactivo import (  # noqa: E402
    InputsPorTramo,
    normalizar_trm_nivel,
)

# ── Banda plausible de nivel COP/USD (del diseño) ──
TRM_MIN_PLAUSIBLE = 500.0
TRM_MAX_PLAUSIBLE = 20000.0

# Tolerancia relativa para comparar niveles de TRM normalizados.
_TOL_REL = 1e-9

# ≥100 iteraciones por propiedad (exigencia del diseño para PBT).
_MIN_EJEMPLOS = 100


def _en_banda(x: float) -> bool:
    """Predicado de banda plausible de nivel COP/USD: ``[500, 20000]``."""
    return TRM_MIN_PLAUSIBLE <= x <= TRM_MAX_PLAUSIBLE


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Prueba de EXPLORACIÓN (DEBE FALLAR en código sin corregir)
#
# Codifica el comportamiento ESPERADO (post-fix) a través de
# `InputsPorTramo.from_valores`, que HOY no normaliza la unidad de `trm_cierre`.
# ─────────────────────────────────────────────────────────────────────────────


@given(
    trm_correcta=st.floats(
        min_value=TRM_MIN_PLAUSIBLE,
        max_value=TRM_MAX_PLAUSIBLE,
        allow_nan=False,
        allow_infinity=False,
    ),
    k=st.sampled_from([-2, -1, 1, 2]),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
# Reproducciones deterministas verificadas (error de ×1000 recuperable):
@example(trm_correcta=3900.0, k=1)  # trm_cruda = 3.9        -> esperado 3900
@example(trm_correcta=3900.0, k=-1)  # trm_cruda = 3_900_000 -> esperado 3900
def test_exploracion_trm_mal_escalada_se_normaliza_a_banda(trm_correcta, k):
    """
    Property 1/3 (Fix): una `trm_cierre` cruda con error de unidad x1000^k
    (`trm_cruda = trm_correcta / 1000^k`) DEBE normalizarse al nivel plausible
    dentro de `[500, 20000]` y recuperar `trm_correcta`.

    En codigo SIN corregir: `from_valores` conserva `trm_cruda` tal cual (fuera de
    banda) => esta asercion FALLA, confirmando el bug.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**
    """
    # trm_cruda es un error de unidad de x1000^k respecto del nivel correcto.
    trm_cruda = trm_correcta / (1000.0**k)

    inp = InputsPorTramo.from_valores(trm_cierre=trm_cruda)

    # (a) El nivel propagado al motor queda dentro de la banda plausible.
    assert _en_banda(inp.trm_cierre), (
        f"trm_cierre={inp.trm_cierre!r} NO esta en banda [500, 20000] "
        f"(cruda={trm_cruda!r}); la unidad no fue normalizada."
    )
    # (b) El nivel normalizado recupera la TRM correcta (solo se ajusta la unidad).
    assert inp.trm_cierre == pytest.approx(trm_correcta, rel=_TOL_REL), (
        f"trm_cierre normalizada={inp.trm_cierre!r} != trm_correcta={trm_correcta!r} "
        f"(cruda={trm_cruda!r})."
    )


@given(
    trm_arbitraria=st.sampled_from([150.0, 200.0, 350.0, 0.15, 42.0, 100.0]),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@example(trm_arbitraria=150.0)  # 150, 150000, 150000000, 0.15 ... ninguno cae en banda
def test_exploracion_trm_no_normalizable_lanza_valueerror(trm_arbitraria):
    """
    Property 4 (Fix): una `trm_cierre` finita, fuera de banda y NO normalizable por
    factores de 1000 (dato arbitrario) DEBE rechazarse con `ValueError`, sin valorar
    en silencio con una TRM dudosa.

    En codigo SIN corregir: `from_valores` conserva el valor tal cual (sin error) =>
    la ausencia de `ValueError` hace FALLAR esta prueba, confirmando el bug.

    **Validates: Requirements 2.4**
    """
    # Precondicion del caso: ningun factor de 1000 lleva el valor a la banda.
    assert not any(
        _en_banda(trm_arbitraria * (1000.0**k)) for k in (-3, -2, -1, 0, 1, 2, 3)
    ), f"El valor de prueba {trm_arbitraria!r} SI es normalizable; no sirve como caso."

    with pytest.raises(ValueError):
        InputsPorTramo.from_valores(trm_cierre=trm_arbitraria)


# =============================================================================
# Task 2 — Pruebas de PRESERVACIÓN (DEBEN PASAR en código SIN corregir)
#
# Metodología observación-primero: se observó el comportamiento del código SIN
# corregir y se codifica aquí como propiedades que el fix (tarea 3.1) DEBE seguir
# cumpliendo. Cubren Property 2 (identidad en banda) y Property 5 (default para
# vacío, campos no-TRM intactos, valoración intacta).
#
# Baseline observado (código sin corregir, 2026-05-20) sobre el cual se anclan:
#   - from_valores(trm_cierre=4100).trm_cierre == 4100.0        (identidad en banda)
#   - from_valores(trm_cierre="").trm_cierre   == 4000.0        (default, Req 3.2)
#   - campos no-TRM se conservan tal cual los coerciona from_valores hoy
#   - valorar_titulo / valorar_bono_yankee: VM lineal en trm; COP (TF) NO depende
#     de trm; USD (YANKEE) escala linealmente con trm (ratio exacto 1000 entre
#     trm=3900 y trm=3.9). Para un Yankee nominal 1.000.000 USD con trm en banda
#     el VM queda en el orden de miles de millones de COP (consistente con el
#     nominal en USD), confirmando la escala correcta que el fix debe preservar.
#
# **Validates: Requirements 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
# =============================================================================

from datetime import date  # noqa: E402

from proyeccion_tramos_multiactivo import (  # noqa: E402
    _DEFAULTS_INPUTS_TRAMO,
    _coerce_float_finito,
)
from valorar_bonos import valorar_bono_yankee  # noqa: E402
from motor_proyeccion import Titulo, valorar_titulo  # noqa: E402

# TRM default documentado para vacío/no numérico (Req 3.2).
_TRM_DEFAULT = 4000.0

# Fecha de valoración de referencia para las anclas de valoración.
_FECHA_VAL = date(2026, 5, 20)

# Campos de InputsPorTramo distintos de ``trm_cierre`` (la guarda SOLO toca TRM).
_CAMPOS_NO_TRM = tuple(c for c in _DEFAULTS_INPUTS_TRAMO if c != "trm_cierre")


def _nuevo_titulo_yankee_usd(nominal_usd: float = 1_000_000.0) -> Titulo:
    """Construye un título Yankee (USD) representativo para las anclas de VM."""
    return Titulo(
        id_posicion="USD1",
        especie="BN YANKEES",
        emisor="EMI",
        isin="US195325DP79",
        moneda="USD",
        fecha_emision=date(2015, 6, 18),
        fecha_vencimiento=date(2045, 6, 18),
        nominal=nominal_usd,
        tasa_cupon=5.875,
        spread_ipc=0.0,
        tipo_bono="YANKEE",
        frecuencia="semestral",
        tipo_tasa="NOMINAL",
        base_anual=360.0,
        uso_tasa="I",
        clasificacion="1CP",
        compania="C",
        ramo="R",
        portafolio="P",
        asset_class="RF_INTL",
        tasa_compra=5.875,
        tasa_mercado=8.81,
        valor_mercado=0.0,
        valor_tir=0.0,
    )


def _nuevo_titulo_tf_cop(nominal_cop: float = 1_000_000.0) -> Titulo:
    """Construye un título COP (TF, deuda pública) que NO multiplica por TRM."""
    return Titulo(
        id_posicion="COP1",
        especie="BONO COP",
        emisor="EMI",
        isin="COBOND",
        moneda="COP",
        fecha_emision=date(2018, 1, 1),
        fecha_vencimiento=date(2030, 1, 1),
        nominal=nominal_cop,
        tasa_cupon=7.0,
        spread_ipc=0.0,
        tipo_bono="TF",
        frecuencia="anual",
        tipo_tasa="NOMINAL",
        base_anual=365.0,
        uso_tasa="I",
        clasificacion="1CP",
        compania="C",
        ramo="R",
        portafolio="P",
        asset_class="DP_TF",
        tasa_compra=7.0,
        tasa_mercado=8.0,
        valor_mercado=0.0,
        valor_tir=0.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 — Identidad en banda (Preservation)
#   Para toda TRM dentro de [500, 20000], from_valores NO reescala el nivel.
# ─────────────────────────────────────────────────────────────────────────────


@given(
    trm=st.floats(
        min_value=TRM_MIN_PLAUSIBLE,
        max_value=TRM_MAX_PLAUSIBLE,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@example(trm=4100.0)  # ancla determinista en banda
@example(trm=TRM_MIN_PLAUSIBLE)  # borde inferior
@example(trm=TRM_MAX_PLAUSIBLE)  # borde superior
def test_preservacion_identidad_trm_en_banda(trm):
    """
    Property 2 (Preservation): una TRM ya en banda `[500, 20000]` queda EXACTAMENTE
    igual al pasar por `from_valores` (la normalización es identidad para niveles
    correctos). Vale hoy y DEBE seguir valiendo tras el fix.

    **Validates: Requirements 2.3, 3.1**
    """
    inp = InputsPorTramo.from_valores(trm_cierre=trm)
    assert inp.trm_cierre == trm, (
        f"trm_cierre={inp.trm_cierre!r} != trm en banda={trm!r}; "
        f"una TRM correcta NO debe reescalarse."
    )


def test_preservacion_identidad_trm_en_banda_ancla_4100():
    """Ancla determinista (Req 2.3, 3.1): `from_valores(4100).trm_cierre == 4100.0`."""
    assert InputsPorTramo.from_valores(trm_cierre=4100).trm_cierre == 4100.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 — Default para vacío / None / no numérico (Preservation, Req 3.2)
# ─────────────────────────────────────────────────────────────────────────────


@given(
    trm_invalida=st.sampled_from(["", "   ", "abc", "N/A", "-", None]),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@example(trm_invalida="")
@example(trm_invalida=None)
def test_preservacion_default_trm_vacia_o_no_numerica(trm_invalida):
    """
    Property 5 (Preservation): una `trm_cierre` vacía / `None` / no numérica cae al
    default documentado `4000.0`, EXACTAMENTE como hoy. Ese caso NO es parte del
    defecto 2B y el fix NO debe cambiarlo.

    **Validates: Requirements 3.2**
    """
    inp = InputsPorTramo.from_valores(trm_cierre=trm_invalida)
    assert inp.trm_cierre == _TRM_DEFAULT, (
        f"trm_cierre={inp.trm_cierre!r} != default {_TRM_DEFAULT!r} para entrada "
        f"vacía/no numérica."
    )


def test_preservacion_default_trm_vacia_ancla():
    """Ancla determinista (Req 3.2): `from_valores(trm_cierre="").trm_cierre == 4000.0`."""
    assert InputsPorTramo.from_valores(trm_cierre="").trm_cierre == 4000.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 — Campos no-TRM intactos (Preservation, Req 3.5)
#   Para cualquier conjunto de inputs crudos, todo campo != trm_cierre queda
#   idéntico al que produce la coerción documentada de from_valores (la guarda de
#   unidad SOLO toca trm_cierre).
# ─────────────────────────────────────────────────────────────────────────────


# Estrategia de un valor crudo por campo: números finitos "válidos", además de
# entradas inválidas (vacío/no numérico) que deben caer al default documentado.
_valor_crudo_strategy = st.one_of(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.sampled_from(["", "  ", "abc", None, "N/A"]),
)


@given(
    inflacion_ea=_valor_crudo_strategy,
    repo_ea=_valor_crudo_strategy,
    mov_tes_cop_bps=_valor_crudo_strategy,
    mov_tes_uvr_bps=_valor_crudo_strategy,
    spread_corp_ipc_bps=_valor_crudo_strategy,
    spread_corp_tf_bps=_valor_crudo_strategy,
    ret_acc_local_ea=_valor_crudo_strategy,
    ret_alt_local_ea=_valor_crudo_strategy,
    ret_alt_intl_ea=_valor_crudo_strategy,
    ret_alt_ipc_bps=_valor_crudo_strategy,
    pei_ea=_valor_crudo_strategy,
    ret_acc_intl_ea=_valor_crudo_strategy,
    delta_fed_bps=_valor_crudo_strategy,
    delta_tesoros10y_bps=_valor_crudo_strategy,
    # trm_cierre siempre en banda para que from_valores sea construible hoy y tras
    # el fix sin disparar la guarda (aquí NO se prueba la TRM, sino los demás campos).
    trm_cierre=st.floats(
        min_value=TRM_MIN_PLAUSIBLE,
        max_value=TRM_MAX_PLAUSIBLE,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_preservacion_campos_no_trm_intactos(
    inflacion_ea,
    repo_ea,
    mov_tes_cop_bps,
    mov_tes_uvr_bps,
    spread_corp_ipc_bps,
    spread_corp_tf_bps,
    ret_acc_local_ea,
    ret_alt_local_ea,
    ret_alt_intl_ea,
    ret_alt_ipc_bps,
    pei_ea,
    ret_acc_intl_ea,
    delta_fed_bps,
    delta_tesoros10y_bps,
    trm_cierre,
):
    """
    Property 5 (Preservation): para cualquier conjunto de inputs crudos, todos los
    campos de `InputsPorTramo` distintos de `trm_cierre` quedan EXACTAMENTE con el
    valor que produce la coerción documentada actual (número finito conservado, o
    default del campo si vacío/no numérico). La guarda de unidad SOLO afecta
    `trm_cierre`.

    El valor esperado por campo se recomputa con la MISMA regla de coerción pública
    (`_coerce_float_finito` con doble sentinela) que usa `from_valores` hoy, de modo
    que el test no asume la implementación interna más allá de su contrato observado.

    **Validates: Requirements 3.5**
    """
    crudos = {
        "inflacion_ea": inflacion_ea,
        "repo_ea": repo_ea,
        "mov_tes_cop_bps": mov_tes_cop_bps,
        "mov_tes_uvr_bps": mov_tes_uvr_bps,
        "spread_corp_ipc_bps": spread_corp_ipc_bps,
        "spread_corp_tf_bps": spread_corp_tf_bps,
        "ret_acc_local_ea": ret_acc_local_ea,
        "ret_alt_local_ea": ret_alt_local_ea,
        "ret_alt_intl_ea": ret_alt_intl_ea,
        "ret_alt_ipc_bps": ret_alt_ipc_bps,
        "pei_ea": pei_ea,
        "ret_acc_intl_ea": ret_acc_intl_ea,
        "delta_fed_bps": delta_fed_bps,
        "delta_tesoros10y_bps": delta_tesoros10y_bps,
    }

    inp = InputsPorTramo.from_valores(trm_cierre=trm_cierre, **crudos)

    for campo in _CAMPOS_NO_TRM:
        crudo = crudos[campo]
        # Regla de coerción documentada: finito → se conserva; si no → default.
        val_a = _coerce_float_finito(crudo, default=0.0)
        val_b = _coerce_float_finito(crudo, default=1.0)
        if val_a != val_b:
            esperado = _DEFAULTS_INPUTS_TRAMO[campo]
        else:
            esperado = val_a

        obtenido = getattr(inp, campo)
        assert obtenido == esperado, (
            f"Campo no-TRM {campo!r} cambió: obtenido={obtenido!r}, "
            f"esperado(coerción documentada)={esperado!r}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 — Valoración intacta (Preservation, Req 3.3, 3.4, 3.6)
# ─────────────────────────────────────────────────────────────────────────────


def test_preservacion_valoracion_cop_no_depende_de_trm():
    """
    Property 5 (Preservation, Req 3.4): un título COP (TF) NO multiplica por TRM; su
    VM es idéntico con cualquier nivel de TRM (la normalización de la TRM no cambia
    nada observable en títulos sin moneda extranjera, Req 3.6).

    **Validates: Requirements 3.3, 3.4, 3.6**
    """
    tf_cop = _nuevo_titulo_tf_cop()
    vm_a = valorar_titulo(tf_cop, 8.0, _FECHA_VAL, trm=3900.0)
    vm_b = valorar_titulo(tf_cop, 8.0, _FECHA_VAL, trm=3.9)
    vm_c = valorar_titulo(tf_cop, 8.0, _FECHA_VAL, trm=12345.6)
    assert vm_a == vm_b == vm_c, (
        f"VM COP depende de la TRM: {vm_a!r} / {vm_b!r} / {vm_c!r} (no debería)."
    )
    assert vm_a > 0.0, "El VM del título COP debe ser positivo con inputs válidos."


@given(
    trm=st.floats(
        min_value=TRM_MIN_PLAUSIBLE,
        max_value=TRM_MAX_PLAUSIBLE,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@example(trm=3900.0)
def test_preservacion_valoracion_usd_lineal_en_trm(trm):
    """
    Property 5 (Preservation, Req 3.3): la valoración USD (YANKEE) es lineal en el
    nivel de TRM — `valorar_titulo` y `valorar_bono_yankee` NO se tocan por el fix.
    El VM en COP de un Yankee con nivel `trm` es `precio_sucio/100 × nominal × trm`,
    de modo que escala exactamente con la TRM que se le pasa.

    **Validates: Requirements 3.3**
    """
    nominal = 1_000_000.0
    yk = _nuevo_titulo_yankee_usd(nominal_usd=nominal)

    vm = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=trm)

    res = valorar_bono_yankee(
        tasa_nominal=yk.tasa_cupon,
        yield_mercado=8.81,
        fecha_emision=yk.fecha_emision,
        fecha_vencimiento=yk.fecha_vencimiento,
        valor_nominal_usd=100.0,
        trm=trm,
        fecha_valuacion=_FECHA_VAL,
    )
    vm_esperado = nominal * res["precio_sucio"] / 100.0 * trm

    assert vm == pytest.approx(vm_esperado, rel=1e-9), (
        f"VM USD={vm!r} no coincide con precio_sucio×nominal/100×trm={vm_esperado!r} "
        f"(trm={trm!r}); la valoración debe quedar intacta."
    )


def test_preservacion_valoracion_usd_escala_x1000():
    """
    Property 5 / baseline observado: el VM de un Yankee con `trm=3900` (nivel) es
    exactamente 1000× el VM con `trm=3.9` (escala "miles"), confirmando el síntoma
    ÷1000 del bug y la linealidad de la valoración que el fix NO altera. Además, con
    `trm=3900` el VM de un nominal de 1.000.000 USD queda en el orden de miles de
    millones de COP (escala correcta, consistente con el nominal en USD).

    Se ancla al VM OBSERVADO en código sin corregir con los parámetros de este título
    de prueba (la preservación exige estabilidad, no un valor del título real).

    **Validates: Requirements 3.3, 3.4**
    """
    yk = _nuevo_titulo_yankee_usd(nominal_usd=1_000_000.0)
    vm_3900 = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=3900.0)
    vm_39 = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=3.9)

    assert vm_39 != 0.0, "El VM con trm=3.9 no debe ser cero (el título es válido)."
    assert vm_3900 == pytest.approx(vm_39 * 1000.0, rel=1e-9), (
        f"VM(trm=3900)={vm_3900!r} no es 1000× VM(trm=3.9)={vm_39!r}."
    )
    # Escala correcta: nominal 1.000.000 USD con TRM en banda ⇒ miles de millones COP.
    assert vm_3900 > 1e9, (
        f"VM(trm=3900)={vm_3900!r} debería estar en el orden de miles de millones "
        f"de COP para un nominal de 1.000.000 USD."
    )
    # Ancla al VM observado (baseline a preservar) con estos parámetros de prueba.
    assert vm_3900 == pytest.approx(3_001_533_366.0, rel=1e-6)


# =============================================================================
# Task 3.4 — Equivalencia del VM USD extremo a extremo (Fix, Property 3)
#
# Objetivo: demostrar, a nivel de valoración, que el fix de la UNIDAD de la TRM
# produce la ESCALA correcta del VM en COP. Se valora un MISMO título Yankee
# (nominal 1.000.000 USD, precio base) con tres TRM y se contrasta la escala:
#
#   - trm=3900                        -> nivel correcto directo
#   - normalizar_trm_nivel(3.9)       -> nivel recuperado del error de unidad x1000
#   - trm=3.9  (crudo, SIN normalizar)-> nivel mal escalado, SOLO para contraste
#
# NO se modifican `valorar_titulo` ni `valorar_bono_yankee`: solo se invocan con
# distintas TRM. La valoración es pura y controlada (no toca la BD / SQLite), por
# lo que NO requiere la convención de BD (deadline/fixtures); aun así se mantiene
# `deadline=None` por el filesystem lento (Google Drive).
#
# **Validates: Requirements 2.1, 2.2**
# =============================================================================


def test_equivalencia_vm_usd_escala_extremo_a_extremo():
    """
    Property 3 (Fix): la normalización de unidad de la TRM recupera la ESCALA
    correcta del VM USD de extremo a extremo.

    Dado un Yankee de nominal 1.000.000 USD valorado vía `valorar_titulo`:

      - El VM con `trm=3900` (nivel correcto) y el VM con `normalizar_trm_nivel(3.9)`
        (nivel recuperado del error de unidad x1000) son IDÉNTICOS (tol. ~1e-6),
        porque `normalizar_trm_nivel(3.9) == 3900` y la valoración no se tocó.
      - El VM con `trm=3.9` crudo (SIN normalizar) es exactamente ÷1000 del VM
        normalizado, confirmando el factor de unidad que el fix recupera (el síntoma
        del bug era precisamente un VM ÷1000).

    Se usa el helper compartido `_nuevo_titulo_yankee_usd` (mismo título que las
    anclas de preservación) y el helper puro `normalizar_trm_nivel` del fix.

    **Validates: Requirements 2.1, 2.2**
    """
    yk = _nuevo_titulo_yankee_usd(nominal_usd=1_000_000.0)

    # Precondición explícita del caso: el helper recupera el nivel correcto desde el
    # error de unidad x1000 (3.9 -> 3900), que es lo que estamos verificando end-to-end.
    trm_normalizada = normalizar_trm_nivel(3.9)
    assert trm_normalizada == pytest.approx(3900.0, rel=_TOL_REL), (
        f"normalizar_trm_nivel(3.9)={trm_normalizada!r} debería recuperar 3900."
    )

    # Tres valoraciones del MISMO título, variando SOLO la TRM (valoración intacta).
    vm_directo = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=3900.0)
    vm_normalizado = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=trm_normalizada)
    vm_mal_escalado = valorar_titulo(yk, 8.81, _FECHA_VAL, trm=3.9)

    # (a) Equivalencia: el nivel correcto directo y el recuperado por el helper dan
    #     EXACTAMENTE el mismo VM (la normalización solo ajusta la unidad).
    assert vm_normalizado == pytest.approx(vm_directo, rel=1e-6), (
        f"VM(normalizar_trm_nivel(3.9))={vm_normalizado!r} != VM(3900)={vm_directo!r}; "
        f"el nivel recuperado debe dar el mismo VM que el nivel correcto directo."
    )

    # (b) Contraste de escala: el VM mal escalado (3.9 crudo) es ÷1000 del normalizado,
    #     reproduciendo el síntoma del bug (VM USD ÷1000) que el fix corrige.
    assert vm_mal_escalado != 0.0, "El VM con trm=3.9 no debe ser cero (título válido)."
    assert vm_mal_escalado == pytest.approx(vm_normalizado / 1000.0, rel=1e-6), (
        f"VM(3.9 crudo)={vm_mal_escalado!r} no es ÷1000 del normalizado="
        f"{vm_normalizado!r}; no se recupera el factor de unidad esperado."
    )

    # (c) Escala correcta: nominal 1.000.000 USD con TRM en banda ⇒ miles de millones COP.
    assert vm_normalizado > 1e9, (
        f"VM normalizado={vm_normalizado!r} debería estar en el orden de miles de "
        f"millones de COP para un nominal de 1.000.000 USD."
    )
