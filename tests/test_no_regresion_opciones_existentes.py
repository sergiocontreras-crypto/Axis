"""
Property 18: No-regresión de las opciones existentes.

**Validates: Requirements 4.3**

*Para todo* conjunto de opciones cargadas desde `hist_opciones` (strike dado), su
precio y su delta calculados con la funcionalidad activa son idénticos a los
calculados por el comportamiento actual; las opciones existentes NUNCA pasan por
el `Solver_Canal`.

# Feature: escalabilidad-derivados-portafolios, Property 18: No-regresión de las
# opciones existentes.

Enfoque de la prueba
--------------------
La no-regresión de las Opciones_Existentes se garantiza en dos puntos del
`valoracion_derivados.py`:

  1. `_renovar_opciones` etiqueta las opciones cargadas de `hist_opciones` con las
     columnas de control `_renovado=False`, `_collar_id=None`, `_call_resuelto=False`
     y NO altera su `strike` (solo AGREGA filas de renovación al final del plano).

  2. `_resolver_calls_renovados_en_corte` filtra estrictamente por
     `_renovado == True`, por lo que NUNCA toca (ni pasa por el solver) las filas
     con `_renovado == False`. Sus `strike` y demás características quedan idénticos
     tras la resolución del corte.

Como el precio y el delta de Black-Scholes de una opción son funciones
determinísticas de su `strike` (y del resto de sus atributos, que tampoco cambian),
demostrar que el `strike` y los atributos de las opciones existentes permanecen
idénticos es suficiente para garantizar que su precio y su delta no cambian.

Se usa Hypothesis con `max_examples=25` (rápido) y generadores en rangos
financieros. Se incluye además un unit test determinista.
"""

from datetime import datetime

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from valoracion_derivados import (
    _renovar_opciones,
    _resolver_calls_renovados_en_corte,
)


# ── Estrategias en rangos financieros ──
_trm = st.floats(min_value=800.0, max_value=8000.0,
                 allow_nan=False, allow_infinity=False)
_strike = st.floats(min_value=800.0, max_value=8000.0,
                    allow_nan=False, allow_infinity=False)
_nominal = st.floats(min_value=1_000.0, max_value=50_000_000.0,
                     allow_nan=False, allow_infinity=False)
_sentido = st.sampled_from(['Compra', 'Venta'])
_call_put = st.sampled_from(['Call', 'Put'])
_anio = st.integers(min_value=2018, max_value=2024)
_mes = st.integers(min_value=1, max_value=12)
_dia = st.integers(min_value=1, max_value=28)


@st.composite
def _opciones_existentes(draw, min_filas=1, max_filas=6):
    """
    Genera un DataFrame de Opciones_Existentes con el esquema mínimo que consume
    `_renovar_opciones` (portafolio, constitucion, vencimiento, sentido, call_put,
    strike, nominal). Las fechas de constitución preceden a las de vencimiento.
    """
    n = draw(st.integers(min_value=min_filas, max_value=max_filas))
    filas = []
    for i in range(n):
        c_anio = draw(_anio)
        constitucion = datetime(c_anio, draw(_mes), draw(_dia))
        # Vencimiento posterior a la constitución (entre ~1 y ~3 años después).
        dias_al_venc = draw(st.integers(min_value=30, max_value=1080))
        vencimiento = constitucion + pd.Timedelta(days=dias_al_venc)
        filas.append({
            'portafolio': f'PORT{i:02d}',
            'constitucion': constitucion,
            'vencimiento': vencimiento,
            'sentido': draw(_sentido),
            'call_put': draw(_call_put),
            'strike': draw(_strike),
            'nominal': draw(_nominal),
        })
    df = pd.DataFrame(filas)
    df['constitucion'] = pd.to_datetime(df['constitucion'])
    df['vencimiento'] = pd.to_datetime(df['vencimiento'])
    return df


def _trm_df_para(opts, trm_valor):
    """
    Construye un `trm_df` (columnas 'fecha', 'trm') que cubre TODAS las fechas de
    constitución y de vencimiento presentes en `opts` (las renovaciones usan la TRM
    en la fecha de vencimiento de la opción original como fecha de constitución del
    collar renovado).
    """
    fechas = pd.to_datetime(pd.concat([
        opts['constitucion'], opts['vencimiento'],
        # Fechas de constitución de las renovaciones (venc original + 1.5 años).
        pd.to_datetime(opts['vencimiento']) + pd.to_timedelta(1.5 * 365, unit='D'),
    ]).unique())
    return pd.DataFrame({'fecha': fechas, 'trm': float(trm_valor)})


def _interpol_vol_constante(vol=0.12):
    def _f(strike, dias_venc):
        return vol
    return _f


def _snapshot_existentes(plan):
    """Captura strike + atributos clave de las filas existentes (_renovado==False)."""
    exist = plan.loc[plan['_renovado'] == False]  # noqa: E712
    cols = ['portafolio', 'constitucion', 'vencimiento', 'sentido',
            'call_put', 'strike', 'nominal']
    return exist[cols].reset_index(drop=True).copy()


@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(opts=_opciones_existentes(), trm_valor=_trm)
def test_opciones_existentes_no_pasan_por_solver_ni_cambian(opts, trm_valor):
    """
    Tras `_renovar_opciones`, las Opciones_Existentes:
      - quedan etiquetadas `_renovado=False`, `_collar_id=None`, `_call_resuelto=False`,
      - conservan su `strike` original,
    y tras `_resolver_calls_renovados_en_corte` (que resuelve los collares renovados)
    NINGUNA fila existente resulta modificada (no pasa por el solver).
    """
    canal_opc = 1650.0
    strikes_originales = opts['strike'].reset_index(drop=True).copy()

    plan = _renovar_opciones(opts, _trm_df_para(opts, trm_valor), canal_opc)

    # --- Etiquetado correcto de las opciones existentes (Req 4.3) ---
    exist = plan.loc[plan['_renovado'] == False]  # noqa: E712
    # Hay exactamente tantas filas existentes como opciones de entrada.
    assert len(exist) == len(opts)
    assert (exist['_renovado'] == False).all()          # noqa: E712
    assert exist['_collar_id'].isna().all()             # _collar_id = None
    assert (exist['_call_resuelto'] == False).all()     # noqa: E712
    # El strike de cada opción existente es idéntico al original.
    np.testing.assert_array_equal(
        exist['strike'].reset_index(drop=True).to_numpy(),
        strikes_originales.to_numpy(),
    )

    # Snapshot de las filas existentes ANTES de resolver los collares renovados.
    antes = _snapshot_existentes(plan)

    # --- Resolver los collares renovados en un corte posterior a toda constitución ---
    # Elegimos una fecha de corte que supere todas las constituciones renovadas para
    # forzar el máximo de resoluciones posibles (las existentes deben quedar intactas).
    fecha_cal = pd.Timestamp(plan['constitucion'].max()) + pd.Timedelta(days=1)
    memo = {}
    _resolver_calls_renovados_en_corte(
        opts_plan=plan,
        fecha_cal=fecha_cal,
        trm_df=_trm_df_para(opts, trm_valor),
        interpol_vol=_interpol_vol_constante(),
        r_df_corte=0.10,
        q_df_corte=0.02,
        tolerancia_prima=1.0,
        max_iter_canal=100,
        canal_opc=canal_opc,
        memo_calls=memo,
    )

    # --- Las opciones existentes NO fueron modificadas por el solver (Req 4.3) ---
    despues = _snapshot_existentes(plan)
    pd.testing.assert_frame_equal(antes, despues, check_dtype=False)
    # Siguen marcadas como no resueltas / no renovadas (jamás entraron al solver).
    exist_post = plan.loc[plan['_renovado'] == False]  # noqa: E712
    assert (exist_post['_call_resuelto'] == False).all()  # noqa: E712
    assert exist_post['_collar_id'].isna().all()


def test_solver_no_modifica_filas_renovado_false_ejemplo():
    """
    Unit test determinista: un plano mixto (una opción existente + su collar
    renovado). Tras resolver, la fila existente conserva strike y marcas; solo la
    Call renovada cambia su strike.
    """
    opts = pd.DataFrame({
        'portafolio': ['P01', 'P01'],
        'constitucion': pd.to_datetime(['2020-01-15', '2020-01-15']),
        'vencimiento': pd.to_datetime(['2020-07-15', '2020-07-15']),
        'sentido': ['Compra', 'Venta'],
        'call_put': ['Put', 'Call'],
        'strike': [4000.0, 4200.0],
        'nominal': [1_000_000.0, 1_000_000.0],
    })
    trm_df = _trm_df_para(opts, 4000.0)
    plan = _renovar_opciones(opts, trm_df, canal_opc=1650.0)

    # Snapshot de las filas existentes.
    antes = _snapshot_existentes(plan)
    assert len(antes) == 2  # las dos opciones de hist_opciones

    fecha_cal = pd.Timestamp(plan['constitucion'].max()) + pd.Timedelta(days=1)
    _resolver_calls_renovados_en_corte(
        opts_plan=plan,
        fecha_cal=fecha_cal,
        trm_df=trm_df,
        interpol_vol=_interpol_vol_constante(),
        r_df_corte=0.10,
        q_df_corte=0.02,
        tolerancia_prima=1.0,
        max_iter_canal=100,
        canal_opc=1650.0,
        memo_calls={},
    )

    despues = _snapshot_existentes(plan)
    pd.testing.assert_frame_equal(antes, despues, check_dtype=False)
    # Las existentes nunca se marcan como resueltas.
    exist_post = plan.loc[plan['_renovado'] == False]  # noqa: E712
    assert (exist_post['_call_resuelto'] == False).all()  # noqa: E712
