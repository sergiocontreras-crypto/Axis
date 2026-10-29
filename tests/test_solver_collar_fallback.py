"""
Property test de la feature `escalabilidad-derivados-portafolios`.

Cubre la **Property 20** del diseño: fallback del Solver_Canal ante no
convergencia.

Función pura bajo prueba (`valoracion_derivados.py`, tarea 12.2):
  - `resolver_canal_collar`: resuelve el strike de la Call de un collar tal que
    el valor absoluto de la prima neta sea <= tolerancia. Ante no convergencia
    (sin cambio de signo en el bracket, o máximo de iteraciones insuficiente, o
    dominio inválido) devuelve `k_call = k_put + canal_opc_fallback`, marca
    `uso_fallback = True` y NO lanza excepción (la corrida no se interrumpe).

Property test con Hypothesis (max_examples=25), etiquetado con la propiedad del
diseño. Reutiliza estrategias del `conftest.py` (TRM, plazos).

Property 20: Fallback del solver ante no convergencia.
Validates: Requirements 4.7
"""

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from valoracion_derivados import resolver_canal_collar

from tests.conftest import trm_strategy


# ── Estrategias ──
#
# Volatilidad implícita positiva (fracción anual) y plazo a vencimiento en días.
_vol_strategy = st.floats(
    min_value=0.05, max_value=0.60, allow_nan=False, allow_infinity=False
)
_dias_strategy = st.integers(min_value=1, max_value=3650)
# Tasas continuas r/q en rangos razonables.
_tasa_cont_strategy = st.floats(
    min_value=-0.05, max_value=0.30, allow_nan=False, allow_infinity=False
)
# Nominal positivo.
_nominal_strategy = st.floats(
    min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False
)
# Canal_Opc de respaldo: distancia Call-Put estrictamente positiva.
_canal_strategy = st.floats(
    min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False
)
# Tolerancia_Prima en COP por unidad de nominal.
_tolerancia_strategy = st.floats(
    min_value=1e-4, max_value=1.0, allow_nan=False, allow_infinity=False
)


def _interpol_vol_constante(vol):
    """Devuelve un interpolador de vol constante `(strike, dias) -> vol`."""

    def _interp(strike, dias_venc):
        return vol

    return _interp


def _assert_fallback(resultado, k_put, canal_opc):
    """
    Verifica el contrato del fallback (Req 4.7):
      - devuelve una tupla `(k_call, prima_neta, uso_fallback)`,
      - `k_call == k_put + canal_opc`,
      - `uso_fallback is True`.
    """
    assert isinstance(resultado, tuple) and len(resultado) == 3
    k_call, _prima, uso_fallback = resultado
    assert uso_fallback is True, "Debe marcar uso_fallback=True ante no convergencia"
    assert np.isclose(k_call, k_put + canal_opc, rtol=1e-12, atol=1e-9), (
        "El fallback debe fijar k_call = k_put + canal_opc"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Caso 1: sin cambio de signo en el bracket.
#
# Si ambas patas comparten sentido ('Compra'), la prima neta es la SUMA de dos
# primas positivas (Put + Call, ambas de compra) y por tanto nunca cruza cero
# hacia arriba desde k_put: f(k_call) > tolerancia para todo k_call del bracket.
# El solver no puede encontrar un cambio de signo → fallback.
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    trm=trm_strategy(min_trm=2000.0, max_trm=5000.0),
    # Vol suficientemente alta para que cada prima supere holgadamente la
    # tolerancia (evita el corte inicial `|f(k_put)| <= tolerancia`).
    vol=st.floats(min_value=0.15, max_value=0.60, allow_nan=False,
                  allow_infinity=False),
    r=_tasa_cont_strategy,
    q=_tasa_cont_strategy,
    # Plazo no trivial: primas materiales frente a la tolerancia.
    dias=st.integers(min_value=90, max_value=3650),
    # Nominal grande: la prima neta (positiva) es >> tolerancia en todo el bracket.
    nominal=st.floats(min_value=1e5, max_value=1e7, allow_nan=False,
                      allow_infinity=False),
    canal_opc=_canal_strategy,
    max_iter=st.integers(min_value=1, max_value=1000),
)
def test_fallback_sin_cambio_de_signo(
    trm, vol, r, q, dias, nominal, canal_opc, max_iter
):
    """
    **Property 20: Fallback del solver ante no convergencia**

    **Validates: Requirements 4.7**

    Con ambas patas en 'Compra' la prima neta es siempre positiva (suma de dos
    primas de compra). Con primas materiales (vol/plazo/nominal grandes) frente a
    una tolerancia estricta, `f(k_put) > tolerancia` y `f` sólo crece al alejar la
    Call: nunca hay cambio de signo en el bracket, por lo que el solver recurre al
    fallback `k_call = k_put + canal_opc`, sin lanzar excepción.
    """
    tolerancia = 1.0  # COP por unidad de nominal; estricta frente a primas grandes.
    k_put = trm  # Put fijada en la TRM del corte.
    resultado = resolver_canal_collar(
        k_put=k_put,
        trm=trm,
        interpol_vol=_interpol_vol_constante(vol),
        r_corte=r,
        q_corte=q,
        dias=dias,
        nominal=nominal,
        sentido_put="Compra",
        sentido_call="Compra",
        tolerancia=tolerancia,
        max_iter=max_iter,
        canal_opc_fallback=canal_opc,
    )
    _assert_fallback(resultado, k_put, canal_opc)


# ─────────────────────────────────────────────────────────────────────────────
# Caso 2: dominio inválido → fallback inmediato sin excepción.
#
# Plazo no positivo, vol no positiva o TRM/k_put no positivos hacen que el
# problema no esté bien planteado. El solver devuelve el fallback sin lanzar.
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    trm=trm_strategy(),
    vol=_vol_strategy,
    r=_tasa_cont_strategy,
    q=_tasa_cont_strategy,
    nominal=_nominal_strategy,
    canal_opc=_canal_strategy,
    tolerancia=_tolerancia_strategy,
    max_iter=st.integers(min_value=1, max_value=1000),
    dominio=st.sampled_from(("dias", "vol", "trm")),
)
def test_fallback_dominio_invalido(
    trm, vol, r, q, nominal, canal_opc, tolerancia, max_iter, dominio
):
    """
    **Property 20: Fallback del solver ante no convergencia**

    **Validates: Requirements 4.7**

    Ante un problema mal planteado (plazo no positivo, vol no positiva o spot no
    positivo) el solver no puede converger y devuelve el fallback
    `k_call = k_put + canal_opc` con `uso_fallback=True`, sin lanzar excepción.
    """
    k_put = trm
    dias = 0 if dominio == "dias" else 100
    vol_eff = 0.0 if dominio == "vol" else vol
    trm_eff = trm
    if dominio == "trm":
        trm_eff = 0.0
        k_put = 0.0

    resultado = resolver_canal_collar(
        k_put=k_put,
        trm=trm_eff,
        interpol_vol=_interpol_vol_constante(vol_eff),
        r_corte=r,
        q_corte=q,
        dias=dias,
        nominal=nominal,
        sentido_put="Compra",
        sentido_call="Venta",
        tolerancia=tolerancia,
        max_iter=max_iter,
        canal_opc_fallback=canal_opc,
    )
    _assert_fallback(resultado, k_put, canal_opc)


# ─────────────────────────────────────────────────────────────────────────────
# Caso 3: máximo de iteraciones insuficiente para el bracketing.
#
# El bracketing amplía el ancho del canal geométricamente `max_iter` veces. Con
# un `canal_opc_fallback` muy pequeño frente a la separación necesaria y
# `max_iter = 1`, el bracket no alcanza a capturar el cambio de signo → fallback.
# En todo caso el contrato del fallback debe cumplirse cuando `uso_fallback`.
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    trm=trm_strategy(min_trm=2000.0, max_trm=5000.0),
    vol=_vol_strategy,
    r=_tasa_cont_strategy,
    q=_tasa_cont_strategy,
    dias=_dias_strategy,
    nominal=_nominal_strategy,
)
def test_fallback_max_iter_insuficiente(trm, vol, r, q, dias, nominal):
    """
    **Property 20: Fallback del solver ante no convergencia**

    **Validates: Requirements 4.7**

    Con `max_iter = 1` y un `canal_opc_fallback` diminuto, el bracketing no llega
    a capturar el cambio de signo para un collar 'Compra'/'Venta' de prima neta
    cero lejano. El solver no lanza excepción y, si recurre al fallback, respeta
    `k_call = k_put + canal_opc` con `uso_fallback=True`.
    """
    k_put = trm
    canal_opc = 1e-6  # Ancho de bracket insignificante.
    resultado = resolver_canal_collar(
        k_put=k_put,
        trm=trm,
        interpol_vol=_interpol_vol_constante(vol),
        r_corte=r,
        q_corte=q,
        dias=dias,
        nominal=nominal,
        sentido_put="Compra",
        sentido_call="Venta",
        tolerancia=1e-9,  # Tolerancia estricta: el punto inicial no basta.
        max_iter=1,
        canal_opc_fallback=canal_opc,
    )
    # No debe lanzar. El resultado es una tupla de 3 elementos.
    assert isinstance(resultado, tuple) and len(resultado) == 3
    k_call, _prima, uso_fallback = resultado
    assert isinstance(uso_fallback, bool)
    if uso_fallback:
        assert np.isclose(k_call, k_put + canal_opc, rtol=1e-12, atol=1e-12), (
            "El fallback debe fijar k_call = k_put + canal_opc"
        )
