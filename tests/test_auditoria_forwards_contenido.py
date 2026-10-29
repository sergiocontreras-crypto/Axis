"""
Property 16: Contenido del registro de auditoría de forwards.

**Validates: Requirements 3.11**

Para todo cálculo de devaluación / punto forward, el `Registro_Auditoria` producido
por `calcular_dev_y_punto_forward` contiene la fecha de constitución, el mes de
proyección, el plazo en días, la TRM del corte, la SWTIBR_EA y la LUTIBR_EA
aplicadas, la devaluación resultante, el punto forward y el indicador de uso de
fallback.

Las estrategias (plazos, tasas, TRM) se reutilizan desde `tests/conftest.py`. Las
curvas SWTIBR/LUTIBR proyectadas se pasan como tasas nominales escalares (% base
360), forma admitida por `_tasa_al_plazo` (escalar/callable/DataFrame).
"""

import datetime as _dt
import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# En este entorno (FS montado, Python 3.14) la GENERACIÓN de entradas de Hypothesis
# es lenta y dispara HealthCheck.too_slow, que es un problema de rendimiento del
# muestreo (no de la lógica bajo prueba). Se suprime ese health check manteniendo
# max_examples=25 para corridas rápidas.
_AJUSTES = settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])

from tests.conftest import plazos_strategy, tasas_strategy, trm_strategy

from valoracion_derivados import calcular_dev_y_punto_forward

# Claves exactas que el Registro_Auditoria de forwards debe contener (Req 3.11).
CLAVES_AUDITORIA_FORWARD = (
    "fecha_constitucion",   # fecha de constitución
    "mes_proyeccion",       # mes de proyección
    "plazo_dias",           # plazo en días
    "trm_corte",            # TRM del corte
    "swtibr_ea",            # SWTIBR_EA aplicada
    "lutibr_ea",            # LUTIBR_EA aplicada
    "dev",                  # devaluación resultante
    "punto_forward",        # punto forward
    "uso_fallback",         # indicador de uso de fallback
)


_FECHA_BASE = _dt.date(2020, 1, 1)


def _fechas_strategy():
    """
    Fechas de constitución dentro de un rango plausible (~11 años). Se construyen a
    partir de un desplazamiento entero de días sobre una fecha base para evitar el
    costoso muestreo de `st.dates()` en este entorno (input generation demasiado
    lenta); el objeto resultante sigue siendo un `datetime.date` real.
    """
    return st.integers(min_value=0, max_value=4017).map(
        lambda d: _FECHA_BASE + _dt.timedelta(days=d)
    )


@_AJUSTES
@given(
    dias=plazos_strategy(),
    trm_corte=trm_strategy(),
    swtibr_nom=tasas_strategy(),
    lutibr_nom=tasas_strategy(),
    dev_forecast=st.floats(
        min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False
    ),
    fecha_constitucion=_fechas_strategy(),
    mes_proyeccion=st.integers(min_value=0, max_value=360),
)
def test_registro_auditoria_forward_contiene_todos_los_campos(
    dias, trm_corte, swtibr_nom, lutibr_nom, dev_forecast,
    fecha_constitucion, mes_proyeccion,
):
    """
    Property 16 — el Registro_Auditoria producido contiene todos los campos exigidos
    por el Req 3.11, con valores coherentes con la tupla de retorno.
    **Validates: Requirements 3.11**
    """
    dev_usada, punto_forward, registro = calcular_dev_y_punto_forward(
        dias=dias,
        trm_corte=trm_corte,
        swtibr_proy=swtibr_nom,
        lutibr_proy=lutibr_nom,
        dev_forecast=dev_forecast,
        fecha_constitucion=fecha_constitucion,
        mes_proyeccion=mes_proyeccion,
    )

    # 1) El registro es un dict que contiene EXACTAMENTE las claves exigidas.
    assert isinstance(registro, dict)
    assert set(registro.keys()) == set(CLAVES_AUDITORIA_FORWARD)

    # 2) Los insumos de contexto quedan registrados tal como se pasaron.
    assert registro["fecha_constitucion"] == fecha_constitucion
    assert registro["mes_proyeccion"] == mes_proyeccion
    assert registro["plazo_dias"] == pytest.approx(float(dias))
    assert registro["trm_corte"] == pytest.approx(float(trm_corte))

    # 3) SWTIBR_EA y LUTIBR_EA están presentes como valores numéricos (float).
    assert isinstance(registro["swtibr_ea"], float)
    assert isinstance(registro["lutibr_ea"], float)

    # 4) La devaluación resultante y el punto forward coinciden con el retorno.
    if math.isnan(dev_usada):
        assert math.isnan(registro["dev"])
    else:
        assert registro["dev"] == pytest.approx(dev_usada)

    if math.isnan(punto_forward):
        assert math.isnan(registro["punto_forward"])
    else:
        assert registro["punto_forward"] == pytest.approx(punto_forward)

    # 5) El indicador de uso de fallback es booleano.
    assert isinstance(registro["uso_fallback"], bool)


@_AJUSTES
@given(
    dias=plazos_strategy(),
    trm_corte=trm_strategy(),
    dev_forecast=st.floats(
        min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False
    ),
)
def test_registro_auditoria_forward_presente_incluso_sin_metadatos(
    dias, trm_corte, dev_forecast,
):
    """
    Property 16 — aun cuando no se pasan `fecha_constitucion` ni `mes_proyeccion`
    (quedan en None), el Registro_Auditoria sigue conteniendo TODAS las claves
    exigidas por el Req 3.11.
    **Validates: Requirements 3.11**
    """
    # Sin curvas resolubles (None) → derivación falla → se activa el fallback,
    # pero el registro debe seguir completo.
    _dev, _pf, registro = calcular_dev_y_punto_forward(
        dias=dias,
        trm_corte=trm_corte,
        swtibr_proy=None,
        lutibr_proy=None,
        dev_forecast=dev_forecast,
    )

    assert set(registro.keys()) == set(CLAVES_AUDITORIA_FORWARD)
    assert registro["fecha_constitucion"] is None
    assert registro["mes_proyeccion"] is None
    assert registro["uso_fallback"] is True
