"""
Property 17: El solver produce un collar de prima neta acotada.

**Validates: Requirements 4.1, 4.2, 4.5**

Para todo `k_put`, volatilidad, plazo `t`, tasas `r`/`q` del corte y nominal en
rangos financieros donde exista solución en el bracket, `resolver_canal_collar`
devuelve un `k_call` tal que la prima neta del collar cumple
`|prima_neta| <= tolerancia`, donde
`prima_neta = precio_bs_signed(Put) + precio_bs_signed(Call)` con los signos por
sentido y por pata.

Diseño del generador (por qué garantiza que "exista solución en el bracket"):
- El solver arranca el bracket en `a = k_put` (Call == Put) y ensancha la Call
  hacia arriba. La prima neta del collar es monótona en `k_call` porque el valor
  Black-Scholes de la Call decrece al alejarse el strike.
- Para que exista un cambio de signo (y por tanto una raíz dentro del bracket),
  las dos patas deben tener sentidos OPUESTOS (una Compra, una Venta). Con
  sentidos iguales las primas se suman en magnitud y no hay cambio de signo
  (eso es el caso de fallback, cubierto por la Property 20 / task 12.4).
- Se fija `k_put = trm` (Put ATM) para mantener el problema bien planteado con
  volatilidad, tasas y plazo positivos, condiciones bajo las cuales el solver
  encuentra el bracket y converge por Brent.

Se usa Hypothesis con max_examples=25 (rangos financieros) y también se incluyen
unit tests deterministas para los dos emparejamientos de sentidos válidos.
"""

import numpy as np
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from libro_de_funciones import precio_bs_signed, prima_neta_collar
from valoracion_derivados import resolver_canal_collar


# ── Estrategias en rangos financieros ──
_trm = st.floats(min_value=800.0, max_value=8000.0,
                 allow_nan=False, allow_infinity=False)
# Volatilidad implícita anualizada (tanto por uno), estrictamente positiva.
_vol = st.floats(min_value=0.03, max_value=0.60,
                 allow_nan=False, allow_infinity=False)
# Tasas continuas r (doméstica) y q (foránea), tanto por uno.
_tasa = st.floats(min_value=0.0, max_value=0.30,
                  allow_nan=False, allow_infinity=False)
_dias = st.integers(min_value=7, max_value=1080)
_nominal = st.floats(min_value=1_000.0, max_value=50_000_000.0,
                     allow_nan=False, allow_infinity=False)
# Ancho del canal de respaldo / semilla del ensanchamiento del bracket.
_canal = st.floats(min_value=50.0, max_value=1000.0,
                   allow_nan=False, allow_infinity=False)
# Emparejamientos de sentidos OPUESTOS: garantizan cambio de signo en el bracket.
_sentidos_opuestos = st.sampled_from([('Compra', 'Venta'), ('Venta', 'Compra')])


def _interpol_vol_constante(vol):
    """Interpolador de volatilidad plano: misma vol para cualquier strike/plazo."""
    def _f(strike, dias_venc):
        return vol
    return _f


def _prima_neta_directa(k_call, k_put, trm, vol, t, r, q, nominal,
                        sentido_put, sentido_call):
    """
    Recalcula la prima neta desde `precio_bs_signed` de forma independiente al
    envoltorio, para comprobar la definición de la propiedad
    (prima = Put firmada + Call firmada) sin acoplarse a `prima_neta_collar`.
    """
    put = precio_bs_signed(trm, k_put, vol, t, r, q, nominal, 'Put', sentido_put)
    call = precio_bs_signed(trm, k_call, vol, t, r, q, nominal, 'Call', sentido_call)
    return float(put + call)


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(trm=_trm, vol=_vol, r=_tasa, q=_tasa, dias=_dias, nominal=_nominal,
       canal=_canal, sentidos=_sentidos_opuestos)
def test_solver_prima_neta_acotada(trm, vol, r, q, dias, nominal, canal, sentidos):
    """
    Cuando existe solución en el bracket (sentidos opuestos, insumos financieros
    válidos), el solver NO usa fallback y la prima neta resultante está acotada
    por la tolerancia. Además, la prima neta reportada coincide con la definición
    Put firmada + Call firmada evaluada en el `k_call` resuelto.
    """
    sentido_put, sentido_call = sentidos
    k_put = trm  # Put ATM: problema bien planteado.
    interpol_vol = _interpol_vol_constante(vol)
    # Tolerancia absoluta pequeña pero proporcional al nominal, dentro del rango
    # de diseño [0.0001, 1000.0].
    tolerancia = min(1000.0, max(1.0, 1e-6 * nominal))

    # Precondición de la propiedad: "donde exista solución en el bracket".
    # La prima neta es monótona en k_call: arranca en f(k_put) y tiende a la prima
    # de la sola pata Put cuando k_call → ∞ (la Call se hace despreciable). Por
    # tanto existe raíz en el bracket si y solo si f(k_put) y f(k_call_grande)
    # tienen signos opuestos. Filtramos las entradas que NO cumplen la precondición
    # (esos casos son legítimo fallback y los cubre la Property 20 / task 12.4).
    t = dias / 365.0
    f_inf = precio_bs_signed(trm, k_put, vol, t, r, q, nominal, 'Put', sentido_put)
    f_a = prima_neta_collar(k_put, k_put, trm, vol, vol, t, r, q, nominal,
                            sentido_put, sentido_call)
    assume(np.isfinite(f_a) and np.isfinite(f_inf))
    # Debe existir cambio de signo estricto entre el inicio del bracket y el límite.
    assume(f_a * float(f_inf) < 0.0)

    k_call, prima_neta, uso_fallback = resolver_canal_collar(
        k_put=k_put, trm=trm, interpol_vol=interpol_vol,
        r_corte=r, q_corte=q, dias=dias, nominal=nominal,
        sentido_put=sentido_put, sentido_call=sentido_call,
        tolerancia=tolerancia, max_iter=100, canal_opc_fallback=canal,
    )

    # Existe solución en el bracket → no debe recurrir al fallback (Req 4.1, 4.5).
    assert uso_fallback is False, (
        f"Esperaba solución en el bracket, hubo fallback. "
        f"k_call={k_call}, prima={prima_neta}"
    )
    # La prima neta reportada está acotada por la tolerancia (Req 4.5).
    assert np.isfinite(prima_neta)
    assert abs(prima_neta) <= tolerancia, (
        f"|prima_neta|={abs(prima_neta)} excede tolerancia={tolerancia}"
    )
    # El k_call resuelto se aleja hacia arriba desde el strike de la Put.
    assert k_call >= k_put
    # La prima neta coincide con la definición Put firmada + Call firmada (Req 4.2),
    # recalculada de forma independiente en el strike resuelto.
    prima_recalculada = _prima_neta_directa(
        k_call, k_put, trm, vol, t, r, q, nominal, sentido_put, sentido_call)
    assert abs(prima_recalculada) <= tolerancia + 1e-6, (
        f"prima recalculada {prima_recalculada} no acotada por {tolerancia}"
    )
    # Consistencia entre la prima reportada y la recálculo directo.
    assert abs(prima_recalculada - prima_neta) <= max(1e-6, 1e-6 * abs(prima_neta) + 1e-3)


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(trm=_trm, vol=_vol, r=_tasa, q=_tasa, dias=_dias, nominal=_nominal,
       canal=_canal, sentidos=_sentidos_opuestos)
def test_prima_neta_collar_coincide_con_bs_signed(trm, vol, r, q, dias, nominal,
                                                  canal, sentidos):
    """
    `prima_neta_collar` es exactamente `precio_bs_signed(Put) + precio_bs_signed(Call)`
    con signos por sentido y por pata (Req 4.2), para el `k_call` resuelto.
    """
    sentido_put, sentido_call = sentidos
    k_put = trm
    interpol_vol = _interpol_vol_constante(vol)
    tolerancia = min(1000.0, max(1.0, 1e-6 * nominal))

    k_call, _prima, _fb = resolver_canal_collar(
        k_put=k_put, trm=trm, interpol_vol=interpol_vol,
        r_corte=r, q_corte=q, dias=dias, nominal=nominal,
        sentido_put=sentido_put, sentido_call=sentido_call,
        tolerancia=tolerancia, max_iter=100, canal_opc_fallback=canal,
    )

    t = dias / 365.0
    envoltorio = prima_neta_collar(
        k_call, k_put, trm, vol, vol, t, r, q, nominal, sentido_put, sentido_call)
    directa = _prima_neta_directa(
        k_call, k_put, trm, vol, t, r, q, nominal, sentido_put, sentido_call)
    assert abs(envoltorio - directa) <= 1e-9 * max(1.0, abs(directa))


# ── Unit tests deterministas (ejemplos concretos) ──

def test_collar_put_compra_call_venta_ejemplo():
    """Ejemplo concreto: Put comprada + Call vendida (collar clásico)."""
    trm = 4000.0
    vol = 0.12
    dias = 90
    r, q = 0.10, 0.02
    nominal = 1_000_000.0
    tolerancia = 1.0
    k_call, prima, fb = resolver_canal_collar(
        k_put=trm, trm=trm, interpol_vol=_interpol_vol_constante(vol),
        r_corte=r, q_corte=q, dias=dias, nominal=nominal,
        sentido_put='Compra', sentido_call='Venta',
        tolerancia=tolerancia, max_iter=100, canal_opc_fallback=200.0,
    )
    assert fb is False
    assert abs(prima) <= tolerancia
    assert k_call > trm


def test_collar_put_venta_call_compra_ejemplo():
    """Ejemplo concreto con sentidos invertidos: sigue acotado por tolerancia."""
    trm = 4000.0
    vol = 0.12
    dias = 90
    r, q = 0.10, 0.02
    nominal = 1_000_000.0
    tolerancia = 1.0
    k_call, prima, fb = resolver_canal_collar(
        k_put=trm, trm=trm, interpol_vol=_interpol_vol_constante(vol),
        r_corte=r, q_corte=q, dias=dias, nominal=nominal,
        sentido_put='Venta', sentido_call='Compra',
        tolerancia=tolerancia, max_iter=100, canal_opc_fallback=200.0,
    )
    assert fb is False
    assert abs(prima) <= tolerancia
    assert k_call > trm
