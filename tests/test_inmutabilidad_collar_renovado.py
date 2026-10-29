# -*- coding: utf-8 -*-
"""
Property 19: Inmutabilidad del collar renovado tras su creación.

**Validates: Requirements 4.4**

Para todo collar renovado, una vez que el Solver_Canal fija el strike de la Call
en el corte de su fecha de constitución, ese strike (y las características del
collar) permanecen INMUTABLES en cortes posteriores: no se vuelve a resolver
(memoización por `_collar_id`), el strike no cambia y no se produce un segundo
Registro_Auditoria para el mismo collar.

Diseño / modelado (siguiendo `tools/verif_task_13_1.py`):
- Se construye un plano de opciones con `_renovar_opciones` a partir de un par
  Put/Call existente. Eso genera un collar renovado (constitución = vencimiento
  original) con su Call pendiente de resolver.
- Se invoca `_resolver_calls_renovados_en_corte` DOS veces con un `memo_calls`
  compartido:
    1) en el corte de la fecha de constitución del collar → resuelve el k_call
       (una resolución, un registro de auditoría);
    2) en un corte posterior → NO debe recalcular; el strike se mantiene y no se
       agrega auditoría.
- Curvas r/q planas y volatilidad constante para que el problema quede bien
  planteado y la resolución sea determinista respecto del strike.

Se usa Hypothesis con max_examples=25 (rangos financieros) más unit tests
deterministas.
"""

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import valoracion_derivados as vd


# ── Estrategias en rangos financieros ──
_trm = st.floats(min_value=800.0, max_value=8000.0,
                 allow_nan=False, allow_infinity=False)
_vol = st.floats(min_value=0.03, max_value=0.60,
                 allow_nan=False, allow_infinity=False)
_tasa = st.floats(min_value=0.0, max_value=0.30,
                  allow_nan=False, allow_infinity=False)
_nominal = st.floats(min_value=1_000.0, max_value=50_000_000.0,
                     allow_nan=False, allow_infinity=False)
_canal = st.floats(min_value=50.0, max_value=2000.0,
                   allow_nan=False, allow_infinity=False)
# Días extra entre cortes para verificar inmutabilidad en un corte posterior.
_dias_posterior = st.integers(min_value=1, max_value=365)
# Emparejamientos de sentidos OPUESTOS (collar clásico): garantizan solución.
_sentidos_opuestos = st.sampled_from([('Venta', 'Compra'), ('Compra', 'Venta')])


def _interpol_vol_constante(vol):
    """Interpolador de volatilidad plano: misma vol para cualquier strike/plazo."""
    def _f(strike, dias_venc):
        return vol
    return _f


def _construir_plan_renovado(trm, canal, sentido_put, sentido_call):
    """
    Construye un plano de opciones con un collar renovado (Put + Call) a partir
    de un par de opciones existentes, usando `_renovar_opciones`.

    Devuelve (plan, venc_original, trm_df, mascara_call_renovada).
    """
    venc_original = pd.Timestamp('2024-06-01')
    opts = pd.DataFrame({
        'constitucion': pd.to_datetime(['2024-01-01', '2024-01-01']),
        'vencimiento':  pd.to_datetime([venc_original, venc_original]),
        'call_put':     ['Put', 'Call'],
        'sentido':      [sentido_put, sentido_call],
        'portafolio':   ['01-OP', '01-OP'],
        'strike':       [trm - 200.0, trm + canal],
        'nominal':      [1_000_000.0, 1_000_000.0],
    })
    # TRM plana alrededor de la fecha de constitución del collar renovado
    # (= vencimiento original) y cortes posteriores.
    fechas = pd.date_range(venc_original, venc_original + pd.Timedelta(days=400))
    trm_df = pd.DataFrame({'fecha': fechas, 'trm': [trm] * len(fechas)})

    plan = vd._renovar_opciones(opts, trm_df, canal_opc=canal)
    call_msk = (
        (plan['_renovado'] == True)  # noqa: E712
        & (plan['call_put'] == 'Call')
    )
    return plan, venc_original, trm_df, call_msk


def _curvas_planas(r, q):
    r_df = pd.DataFrame({'Plazo_en_Dias': [1, 3650], 'Valor': [r, r]})
    q_df = pd.DataFrame({'Plazo_en_Dias': [1, 3650], 'Valor': [q, q]})
    return r_df, q_df


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(trm=_trm, vol=_vol, r=_tasa, q=_tasa, canal=_canal,
       dias_post=_dias_posterior, sentidos=_sentidos_opuestos)
def test_collar_renovado_inmutable_tras_creacion(trm, vol, r, q, canal,
                                                 dias_post, sentidos):
    """
    Tras resolver el k_call en el corte de constitución, un corte posterior no
    recalcula: el strike de la Call se mantiene idéntico, la memoización conserva
    una sola entrada y no se produce un segundo Registro_Auditoria (Req 4.4).
    """
    sentido_put, sentido_call = sentidos
    plan, venc_original, trm_df, call_msk = _construir_plan_renovado(
        trm, canal, sentido_put, sentido_call)
    r_df, q_df = _curvas_planas(r, q)
    interpol_vol = _interpol_vol_constante(vol)

    memo = {}
    registros = []

    # ── Corte 1: fecha de constitución del collar renovado (= vencimiento orig) ──
    vd._resolver_calls_renovados_en_corte(
        plan, venc_original, trm_df, interpol_vol, r_df, q_df,
        tolerancia_prima=1.0, max_iter_canal=100, canal_opc=canal,
        memo_calls=memo, registros_auditoria=registros, corrida_id='P19', m=150,
    )

    # El collar quedó resuelto: strike fijado, memo con una entrada, un registro.
    assert bool(plan.loc[call_msk, '_call_resuelto'].iloc[0]) is True
    k_call_creacion = float(plan.loc[call_msk, 'strike'].iloc[0])
    assert np.isfinite(k_call_creacion)
    assert len(memo) == 1
    n_registros_tras_creacion = len(registros)
    assert n_registros_tras_creacion == 1
    assert registros[0]['tipo_registro'] == 'COLLAR_SOLVER'

    # Snapshot de las características inmutables del collar (Req 4.4).
    fila_call = plan.loc[call_msk].iloc[0]
    caracteristicas = {
        'constitucion': fila_call['constitucion'],
        'vencimiento': fila_call['vencimiento'],
        'call_put': fila_call['call_put'],
        'sentido': fila_call['sentido'],
        'nominal': float(fila_call['nominal']),
        'strike': float(fila_call['strike']),
        'collar_id': fila_call['_collar_id'],
    }

    # ── Corte 2: posterior. NO debe recalcular ni mutar el collar. ──
    vd._resolver_calls_renovados_en_corte(
        plan, venc_original + pd.Timedelta(days=dias_post), trm_df, interpol_vol,
        r_df, q_df, tolerancia_prima=1.0, max_iter_canal=100, canal_opc=canal,
        memo_calls=memo, registros_auditoria=registros, corrida_id='P19', m=150 + dias_post,
    )

    k_call_posterior = float(plan.loc[call_msk, 'strike'].iloc[0])

    # Inmutabilidad del strike de la Call (Req 4.4).
    assert k_call_posterior == k_call_creacion, (
        f"strike Call cambió entre cortes: {k_call_creacion} -> {k_call_posterior}"
    )
    # No se volvió a resolver: memo intacto y sin nuevos registros de auditoría.
    assert len(memo) == 1, "el collar no debe re-resolverse (memoización por _collar_id)"
    assert len(registros) == n_registros_tras_creacion, (
        "no debe generarse un segundo Registro_Auditoria para el mismo collar"
    )

    # El resto de características del collar permanecen inmutables (Req 4.4).
    fila_call_post = plan.loc[call_msk].iloc[0]
    assert fila_call_post['constitucion'] == caracteristicas['constitucion']
    assert fila_call_post['vencimiento'] == caracteristicas['vencimiento']
    assert fila_call_post['call_put'] == caracteristicas['call_put']
    assert fila_call_post['sentido'] == caracteristicas['sentido']
    assert float(fila_call_post['nominal']) == caracteristicas['nominal']
    assert float(fila_call_post['strike']) == caracteristicas['strike']
    assert fila_call_post['_collar_id'] == caracteristicas['collar_id']


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(trm=_trm, vol=_vol, r=_tasa, q=_tasa, canal=_canal,
       sentidos=_sentidos_opuestos)
def test_multiples_cortes_posteriores_no_alteran_collar(trm, vol, r, q, canal,
                                                        sentidos):
    """
    Invocar el resolvedor en VARIOS cortes posteriores nunca altera el strike
    resuelto ni añade auditoría (idempotencia de la memoización, Req 4.4).
    """
    sentido_put, sentido_call = sentidos
    plan, venc_original, trm_df, call_msk = _construir_plan_renovado(
        trm, canal, sentido_put, sentido_call)
    r_df, q_df = _curvas_planas(r, q)
    interpol_vol = _interpol_vol_constante(vol)

    memo = {}
    registros = []

    vd._resolver_calls_renovados_en_corte(
        plan, venc_original, trm_df, interpol_vol, r_df, q_df,
        tolerancia_prima=1.0, max_iter_canal=100, canal_opc=canal,
        memo_calls=memo, registros_auditoria=registros, corrida_id='P19', m=150,
    )
    k_call_creacion = float(plan.loc[call_msk, 'strike'].iloc[0])
    assert len(registros) == 1

    for extra in (15, 45, 90, 180, 360):
        vd._resolver_calls_renovados_en_corte(
            plan, venc_original + pd.Timedelta(days=extra), trm_df, interpol_vol,
            r_df, q_df, tolerancia_prima=1.0, max_iter_canal=100, canal_opc=canal,
            memo_calls=memo, registros_auditoria=registros, corrida_id='P19',
            m=150 + extra,
        )
        assert float(plan.loc[call_msk, 'strike'].iloc[0]) == k_call_creacion
        assert len(memo) == 1
        assert len(registros) == 1


# ── Unit test determinista (ejemplo concreto), análogo a verif_task_13_1.py ──

def test_ejemplo_collar_renovado_inmutable():
    """Ejemplo concreto: resolver en creación y verificar inmutabilidad posterior."""
    trm = 4200.0
    plan, venc_original, trm_df, call_msk = _construir_plan_renovado(
        trm, canal=1650.0, sentido_put='Venta', sentido_call='Compra')
    r_df, q_df = _curvas_planas(0.09, 0.03)
    interpol_vol = _interpol_vol_constante(0.15)

    memo = {}
    registros = []

    vd._resolver_calls_renovados_en_corte(
        plan, venc_original, trm_df, interpol_vol, r_df, q_df,
        tolerancia_prima=1.0, max_iter_canal=100, canal_opc=1650.0,
        memo_calls=memo, registros_auditoria=registros, corrida_id='X', m=150,
    )
    k_call_1 = float(plan.loc[call_msk, 'strike'].iloc[0])
    assert plan.loc[call_msk, '_call_resuelto'].iloc[0]
    assert len(memo) == 1
    assert len(registros) == 1

    vd._resolver_calls_renovados_en_corte(
        plan, venc_original + pd.Timedelta(days=30), trm_df, interpol_vol,
        r_df, q_df, tolerancia_prima=1.0, max_iter_canal=100, canal_opc=1650.0,
        memo_calls=memo, registros_auditoria=registros, corrida_id='X', m=180,
    )
    k_call_2 = float(plan.loc[call_msk, 'strike'].iloc[0])
    assert k_call_1 == k_call_2
    assert len(memo) == 1
    assert len(registros) == 1
