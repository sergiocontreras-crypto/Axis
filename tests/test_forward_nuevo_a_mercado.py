"""
Property 13: Un forward recién constituido a mercado vale aproximadamente cero.

Validates: Requirements 3.7

Para todo forward cuyo strike es `TRM_corte * (1 + dev)^(dias / 365)` valorado en
su fecha de constitución con el `fwdcop` dinámico correspondiente
(`punto_forward_dinamico`), su valoración es aproximadamente cero (dentro de
tolerancia numérica), tanto para sentido Compra como Venta.

Justificación de la identidad:
    fwdcop  = punto_forward_dinamico(TRM, dev, dias)
            = TRM * (1 + dev)^(dias/365) - TRM
    strike  = TRM * (1 + dev)^(dias/365)
    ⇒ last_trm + fwdcop = TRM + (strike - TRM) = strike
En `valorar_fwds`, vp_trm = (last_trm + fwdcop)/(1 + r_plazo) y
vp_strike = strike/(1 + r_plazo); al coincidir numerador, vp_trm == vp_strike,
por lo que la valoración (Compra o Venta) es 0 salvo error de punto flotante.

Se reutilizan las funciones reales del proyecto:
    - punto_forward_dinamico  (valoracion_derivados)
    - valorar_fwds            (libro_de_funciones)
y las estrategias de conftest (trm_strategy, plazos_strategy).
"""

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import plazos_strategy, tasas_strategy, trm_strategy

from libro_de_funciones import valorar_fwds
from valoracion_derivados import punto_forward_dinamico


# Devaluaciones dentro del rango de validez del diseño ([-0.5, 2.0]) y con base
# (1 + dev) > 0 para que punto_forward_dinamico no devuelva NaN.
_dev_strategy = st.floats(
    min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False
)

# Nominal positivo y finito, en un rango operativo razonable.
_nominal_strategy = st.floats(
    min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False
)


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    trm=trm_strategy(),
    dev=_dev_strategy,
    dias=plazos_strategy(),
    nominal=_nominal_strategy,
    r_fwd_v=tasas_strategy(min_pct=0.0, max_pct=40.0),
    tipo=st.sampled_from(["Compra", "Venta"]),
)
def test_forward_nuevo_a_mercado_vale_aprox_cero(trm, dev, dias, nominal, r_fwd_v, tipo):
    """Un forward constituido a mercado (strike = TRM*(1+dev)^(dias/365)) vale ~0."""
    # r_fwd_v está en % nominal base 360 en el dominio del proyecto; valorar_fwds
    # espera la tasa en su forma decimal (r_plazo = r_fwd_v / (360/dias)).
    r_fwd = r_fwd_v / 100.0

    # Strike "a mercado" en la fecha de constitución.
    factor = (1.0 + dev) ** (dias / 365.0)
    strike = trm * factor

    # Punto forward dinámico correspondiente (función real del proyecto).
    fwdcop = punto_forward_dinamico(trm, dev, dias)
    assert math.isfinite(fwdcop), "fwdcop debe ser finito para dev en [-0.5, 2.0]"

    # Valorado en su fecha de constitución: last_trm es la TRM del corte.
    valoracion, vp_trm, vp_strike = valorar_fwds(
        last_trm=trm,
        dias=dias,
        nominal=nominal,
        strike=strike,
        r_fwd_v=r_fwd,
        fwdcop=fwdcop,
        tipo=tipo,
    )

    val = float(valoracion)

    # Tolerancia numérica escalada por la magnitud de la exposición (nominal*TRM),
    # para absorber el error de punto flotante en las divisiones/potencias.
    escala = abs(nominal) * max(abs(trm), 1.0)
    tolerancia = 1e-6 * escala + 1e-6

    assert math.isfinite(val), f"valoración no finita: {val!r}"
    assert abs(val) <= tolerancia, (
        f"valoración de forward a mercado ({tipo}) no es ~0: "
        f"val={val!r}, tol={tolerancia!r}, vp_trm={float(vp_trm)!r}, "
        f"vp_strike={float(vp_strike)!r}"
    )
