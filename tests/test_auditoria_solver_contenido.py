"""
Property 21: Contenido del registro de auditoría del solver.

**Validates: Requirements 4.10**

Para toda resolución del solver de canal, el `Registro_Auditoria` producido contiene
el mes de proyección, el Shift_TES y el Shift_FED acumulados aplicados, el strike de
la Put, el strike de la Call resuelto, la prima neta resultante y el indicador de uso
de fallback.

Esta prueba ejercita directamente `_resolver_calls_renovados_en_corte`, que es el
punto donde (task 13.2) se acumula el `Registro_Auditoria` de tipo `COLLAR_SOLVER`
por cada collar renovado que el solver resuelve en el corte. Se construye un
`opts_plan` renovado (parejas Put/Call por `_collar_id`), se corre la función
acumulando `registros_auditoria` y se comprueba que cada registro `COLLAR_SOLVER`
lleva los campos exigidos por el Req 4.10 con valores coherentes.

Las estrategias de plazos / nominal / TRM se reutilizan/derivan de rangos
financieros; se usa max_examples=25 para corridas rápidas.
"""

import datetime as _dt

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from valoracion_derivados import _resolver_calls_renovados_en_corte

# En este entorno (FS montado en Drive, Python reciente) la GENERACIÓN de entradas
# de Hypothesis es lenta y puede disparar HealthCheck.too_slow, ajeno a la lógica
# bajo prueba. Se suprime ese health check manteniendo max_examples=25.
_AJUSTES = settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

# Claves exactas que el Registro_Auditoria del solver debe contener (Req 4.10),
# además del `corrida_id` y `tipo_registro` de contexto del esquema.
CLAVES_SOLVER_REQUERIDAS = (
    "mes_proyeccion",   # mes de proyección
    "shift_tes_bps",    # Shift_TES acumulado aplicado
    "shift_fed_bps",    # Shift_FED acumulado aplicado
    "strike_put",       # strike de la Put
    "strike_call",      # strike de la Call resuelto
    "prima_neta",       # prima neta resultante
    "uso_fallback",     # indicador de uso de fallback
)

_FECHA_CORTE = _dt.datetime(2026, 5, 20)


def _interpol_vol_constante(vol):
    """Interpolador de volatilidad plano: misma vol para cualquier strike/plazo."""
    def _f(strike, dias_venc):
        return vol
    return _f


@st.composite
def _collares_renovados_strategy(draw):
    """
    Genera un `opts_plan` con 1..4 collares renovados (pareja Put/Call por
    `_collar_id`), todos con fecha de constitución <= corte, más (opcionalmente)
    una opción existente NO renovada que el solver debe ignorar.

    Cada collar recibe sentidos OPUESTOS u OPUESTOS-invertidos o IGUALES: los
    sentidos iguales llevan a fallback (sin cambio de signo), lo cual también debe
    producir un Registro_Auditoria válido — cubriendo ambas ramas.
    """
    n = draw(st.integers(min_value=1, max_value=4))
    filas = []
    for i in range(n):
        trm = draw(st.floats(min_value=800.0, max_value=8000.0,
                             allow_nan=False, allow_infinity=False))
        dias = draw(st.integers(min_value=15, max_value=720))
        nominal = draw(st.floats(min_value=1_000.0, max_value=10_000_000.0,
                                 allow_nan=False, allow_infinity=False))
        sentidos = draw(st.sampled_from([
            ("Compra", "Venta"),   # collar clásico → converge
            ("Venta", "Compra"),   # invertido → converge
            ("Compra", "Compra"),  # mismo sentido → fallback
            ("Venta", "Venta"),    # mismo sentido → fallback
        ]))
        sentido_put, sentido_call = sentidos
        # Días de antigüedad de la constitución respecto del corte (>= 0).
        antiguedad = draw(st.integers(min_value=0, max_value=60))
        constitucion = _FECHA_CORTE - _dt.timedelta(days=int(antiguedad))
        vencimiento = constitucion + _dt.timedelta(days=int(dias))
        collar_id = f"COLLAR_{i}"

        # Pata Put (strike fijado en la TRM del corte de constitución).
        filas.append({
            "_collar_id": collar_id,
            "call_put": "Put",
            "sentido": sentido_put,
            "strike": float(trm),
            "nominal": float(nominal),
            "constitucion": pd.Timestamp(constitucion),
            "vencimiento": pd.Timestamp(vencimiento),
            "_renovado": True,
            "_call_resuelto": False,
        })
        # Pata Call (strike provisional que el solver debe reemplazar).
        filas.append({
            "_collar_id": collar_id,
            "call_put": "Call",
            "sentido": sentido_call,
            "strike": float(trm) + 1000.0,
            "nominal": float(nominal),
            "constitucion": pd.Timestamp(constitucion),
            "vencimiento": pd.Timestamp(vencimiento),
            "_renovado": True,
            "_call_resuelto": False,
        })

    # Opción existente NO renovada: nunca debe entrar al solver.
    if draw(st.booleans()):
        filas.append({
            "_collar_id": "EXISTENTE_0",
            "call_put": "Call",
            "sentido": "Venta",
            "strike": 4200.0,
            "nominal": 1_000_000.0,
            "constitucion": pd.Timestamp(_FECHA_CORTE - _dt.timedelta(days=10)),
            "vencimiento": pd.Timestamp(_FECHA_CORTE + _dt.timedelta(days=100)),
            "_renovado": False,
            "_call_resuelto": False,
        })

    opts_plan = pd.DataFrame(filas)
    return opts_plan, n


def _trm_df():
    """Serie de TRM mínima usada por la función (columna fecha/trm)."""
    return pd.DataFrame({
        "fecha": [pd.Timestamp(_FECHA_CORTE)],
        "trm": [4000.0],
    })


@_AJUSTES
@given(
    caso=_collares_renovados_strategy(),
    vol=st.floats(min_value=0.05, max_value=0.45,
                  allow_nan=False, allow_infinity=False),
    r=st.floats(min_value=0.0, max_value=0.25, allow_nan=False, allow_infinity=False),
    q=st.floats(min_value=0.0, max_value=0.25, allow_nan=False, allow_infinity=False),
    m=st.integers(min_value=0, max_value=360),
    shift_tes_bps=st.floats(min_value=-500.0, max_value=500.0,
                            allow_nan=False, allow_infinity=False),
    shift_fed_bps=st.floats(min_value=-500.0, max_value=500.0,
                            allow_nan=False, allow_infinity=False),
)
def test_registro_solver_contiene_campos_requeridos(
    caso, vol, r, q, m, shift_tes_bps, shift_fed_bps,
):
    """
    Property 21 — cada Registro_Auditoria de tipo COLLAR_SOLVER acumulado por
    `_resolver_calls_renovados_en_corte` contiene el mes de proyección, los shifts
    TES/FED acumulados, el strike Put, el strike Call resuelto, la prima neta y el
    indicador de uso de fallback, con valores coherentes.
    **Validates: Requirements 4.10**
    """
    opts_plan, n_collares = caso
    registros = []
    corrida_id = "CORRIDA_TEST"

    _resolver_calls_renovados_en_corte(
        opts_plan=opts_plan,
        fecha_cal=_FECHA_CORTE,
        trm_df=_trm_df(),
        interpol_vol=_interpol_vol_constante(vol),
        r_df_corte=float(r),
        q_df_corte=float(q),
        tolerancia_prima=1.0,
        max_iter_canal=100,
        canal_opc=1650.0,
        memo_calls={},
        registros_auditoria=registros,
        corrida_id=corrida_id,
        m=m,
        shift_tes_bps=shift_tes_bps,
        shift_fed_bps=shift_fed_bps,
    )

    # Se resuelve exactamente un collar renovado por `_collar_id` (memoización), y
    # las opciones existentes no renovadas nunca entran → un registro por collar.
    solver_regs = [rg for rg in registros if rg.get("tipo_registro") == "COLLAR_SOLVER"]
    assert len(solver_regs) == n_collares, (
        f"Esperaba {n_collares} registros COLLAR_SOLVER, hubo {len(solver_regs)}"
    )

    strikes_put_por_collar = {}
    for idx, fila in opts_plan.iterrows():
        if fila["call_put"] == "Put":
            strikes_put_por_collar[fila["_collar_id"]] = float(fila["strike"])

    for reg in solver_regs:
        # 1) Contiene TODAS las claves exigidas por el Req 4.10.
        for clave in CLAVES_SOLVER_REQUERIDAS:
            assert clave in reg, f"Falta la clave requerida '{clave}' en {reg}"

        # 2) Contexto de la corrida y tipo de registro correctos.
        assert reg["corrida_id"] == corrida_id
        assert reg["tipo_registro"] == "COLLAR_SOLVER"

        # 3) El mes de proyección y los shifts acumulados quedan tal como se pasaron.
        assert reg["mes_proyeccion"] == m
        assert reg["shift_tes_bps"] == pytest.approx(shift_tes_bps)
        assert reg["shift_fed_bps"] == pytest.approx(shift_fed_bps)

        # 4) Strike Put coherente y positivo; strike Call resuelto finito y >= Put.
        assert reg["strike_put"] > 0.0
        assert np.isfinite(reg["strike_call"])
        assert reg["strike_call"] >= reg["strike_put"]

        # 5) Prima neta presente como número finito.
        assert np.isfinite(reg["prima_neta"])

        # 6) uso_fallback es un indicador 0/1.
        assert reg["uso_fallback"] in (0, 1)

    # Los strikes Put registrados deben corresponder a strikes Put reales del plano.
    puts_registrados = {round(rg["strike_put"], 6) for rg in solver_regs}
    puts_plan = {round(v, 6) for v in strikes_put_por_collar.values()}
    assert puts_registrados <= puts_plan


@_AJUSTES
@given(
    r=st.floats(min_value=0.0, max_value=0.25, allow_nan=False, allow_infinity=False),
    q=st.floats(min_value=0.0, max_value=0.25, allow_nan=False, allow_infinity=False),
    vol=st.floats(min_value=0.05, max_value=0.45,
                  allow_nan=False, allow_infinity=False),
    m=st.integers(min_value=0, max_value=360),
)
def test_registro_solver_marca_fallback_para_sentidos_iguales(r, q, vol, m):
    """
    Property 21 (rama fallback) — un collar con patas del MISMO sentido no tiene
    cambio de signo en el bracket, por lo que el solver usa fallback; el registro
    resultante debe seguir conteniendo todos los campos y marcar `uso_fallback == 1`
    con `strike_call == strike_put + canal_opc`.
    **Validates: Requirements 4.10**
    """
    trm = 4000.0
    dias = 90
    nominal = 1_000_000.0
    canal_opc = 1650.0
    opts_plan = pd.DataFrame([
        {
            "_collar_id": "C0", "call_put": "Put", "sentido": "Compra",
            "strike": trm, "nominal": nominal,
            "constitucion": pd.Timestamp(_FECHA_CORTE),
            "vencimiento": pd.Timestamp(_FECHA_CORTE + _dt.timedelta(days=dias)),
            "_renovado": True, "_call_resuelto": False,
        },
        {
            "_collar_id": "C0", "call_put": "Call", "sentido": "Compra",
            "strike": trm + 1000.0, "nominal": nominal,
            "constitucion": pd.Timestamp(_FECHA_CORTE),
            "vencimiento": pd.Timestamp(_FECHA_CORTE + _dt.timedelta(days=dias)),
            "_renovado": True, "_call_resuelto": False,
        },
    ])
    registros = []

    _resolver_calls_renovados_en_corte(
        opts_plan=opts_plan, fecha_cal=_FECHA_CORTE, trm_df=_trm_df(),
        interpol_vol=_interpol_vol_constante(vol),
        r_df_corte=float(r), q_df_corte=float(q),
        tolerancia_prima=1.0, max_iter_canal=100, canal_opc=canal_opc,
        memo_calls={}, registros_auditoria=registros,
        corrida_id="CORRIDA_FB", m=m,
        shift_tes_bps=0.0, shift_fed_bps=0.0,
    )

    solver_regs = [rg for rg in registros if rg.get("tipo_registro") == "COLLAR_SOLVER"]
    assert len(solver_regs) == 1
    reg = solver_regs[0]
    for clave in CLAVES_SOLVER_REQUERIDAS:
        assert clave in reg
    assert reg["uso_fallback"] == 1
    assert reg["strike_call"] == pytest.approx(reg["strike_put"] + canal_opc)
    assert np.isfinite(reg["prima_neta"])
