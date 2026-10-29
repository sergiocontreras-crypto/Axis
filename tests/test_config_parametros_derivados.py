"""
Unit tests de configuración de parámetros y rangos de
`valorar_portafolio_derivados` (feature `escalabilidad-derivados-portafolios`,
tarea 15.2).

Verifica, sin ejecutar la valoración completa:

1. Los **defaults documentados** de los keyword args agregados en la tarea 15.1:
   `dev_forecast=0.085`, `canal_opc=1650`, `tolerancia_prima=1.0`,
   `max_iter_canal=100`, `persistir_auditoria=True`, `corrida_id=None`.
2. La **validación de rango fail-fast** (Req 3.10, 4.6, 4.8): valores fuera de
   rango de `dev_forecast`, `tolerancia_prima` y `max_iter_canal` lanzan
   `ValueError` ANTES de cualquier acceso a base de datos o trabajo pesado.
3. Que valores **dentro de rango** superan la validación (no lanzan el
   `ValueError` de rango). Para no ejecutar la valoración completa ni tocar la
   DB, se interpone un centinela en `sqlite3.connect` que detiene la ejecución
   inmediatamente después del bloque de validación.

Validates: Requirements 3.10, 4.6, 4.8, 4.9
"""

import inspect

import pytest

import valoracion_derivados
from valoracion_derivados import valorar_portafolio_derivados


# ── Argumentos posicionales mínimos requeridos por la firma ──
#
# La validación de rango es fail-fast (primer bloque de la función), por lo que
# estos valores solo necesitan permitir invocar la función; nunca se llega a
# usarlos cuando el parámetro bajo prueba está fuera de rango.
_BASE_ARGS = (
    "2026-05-20",  # fecha_corte
    "2026-06-30",  # fecha_fin
    1,             # metodo
    3900.0,        # nivel_actual
    0.0,           # activos_usd
    0.0,           # portafolio_pesos
)


class _Centinela(Exception):
    """Marca que la ejecución superó la validación y llegó al acceso a DB."""


@pytest.fixture
def bloquear_db(monkeypatch):
    """
    Interpone un centinela en `sqlite3.connect` dentro del módulo bajo prueba.

    Permite comprobar que un conjunto de parámetros VÁLIDOS supera el bloque de
    validación de rango (que es lo único que esta tarea verifica) sin ejecutar
    la valoración completa ni tocar la base de datos real.
    """

    def _connect_centinela(*_args, **_kwargs):
        raise _Centinela("se alcanzó sqlite3.connect tras la validación")

    monkeypatch.setattr(valoracion_derivados.sqlite3, "connect", _connect_centinela)


# =====================================================================
# 1. Defaults documentados
# =====================================================================

def test_defaults_documentados_de_parametros_configurables():
    """Los defaults de la firma coinciden con los documentados (tarea 15.1)."""
    params = inspect.signature(valorar_portafolio_derivados).parameters

    assert params["dev_forecast"].default == pytest.approx(0.085)
    assert params["canal_opc"].default == 1650
    assert params["tolerancia_prima"].default == pytest.approx(1.0)
    assert params["max_iter_canal"].default == 100
    assert params["persistir_auditoria"].default is True
    assert params["corrida_id"].default is None


# =====================================================================
# 2. Validación de rango: valores fuera de rango lanzan ValueError
# =====================================================================

@pytest.mark.parametrize("dev_forecast", [-0.5001, -1.0, 2.0001, 5.0])
def test_dev_forecast_fuera_de_rango_lanza_value_error(dev_forecast):
    """`dev_forecast` fuera de [-0.5, 2.0] lanza ValueError (Req 3.10)."""
    with pytest.raises(ValueError, match="dev_forecast"):
        valorar_portafolio_derivados(*_BASE_ARGS, dev_forecast=dev_forecast)


@pytest.mark.parametrize("tolerancia_prima", [0.0, 0.00009, 1000.01, 5000.0])
def test_tolerancia_prima_fuera_de_rango_lanza_value_error(tolerancia_prima):
    """`tolerancia_prima` fuera de [0.0001, 1000.0] lanza ValueError (Req 4.6)."""
    with pytest.raises(ValueError, match="tolerancia_prima"):
        valorar_portafolio_derivados(*_BASE_ARGS, tolerancia_prima=tolerancia_prima)


@pytest.mark.parametrize("max_iter_canal", [0, -1, 1001, 5000])
def test_max_iter_canal_fuera_de_rango_lanza_value_error(max_iter_canal):
    """`max_iter_canal` fuera de [1, 1000] lanza ValueError (Req 4.8)."""
    with pytest.raises(ValueError, match="max_iter_canal"):
        valorar_portafolio_derivados(*_BASE_ARGS, max_iter_canal=max_iter_canal)


# =====================================================================
# 3. Valores dentro de rango superan la validación
# =====================================================================

@pytest.mark.parametrize("dev_forecast", [-0.5, 0.0, 0.085, 2.0])
def test_dev_forecast_en_rango_supera_validacion(dev_forecast, bloquear_db):
    """Bordes/interior de [-0.5, 2.0] NO disparan el ValueError de rango."""
    with pytest.raises(_Centinela):
        valorar_portafolio_derivados(*_BASE_ARGS, dev_forecast=dev_forecast)


@pytest.mark.parametrize("tolerancia_prima", [0.0001, 1.0, 500.0, 1000.0])
def test_tolerancia_prima_en_rango_supera_validacion(tolerancia_prima, bloquear_db):
    """Bordes/interior de [0.0001, 1000.0] NO disparan el ValueError de rango."""
    with pytest.raises(_Centinela):
        valorar_portafolio_derivados(*_BASE_ARGS, tolerancia_prima=tolerancia_prima)


@pytest.mark.parametrize("max_iter_canal", [1, 100, 1000])
def test_max_iter_canal_en_rango_supera_validacion(max_iter_canal, bloquear_db):
    """Bordes/interior de [1, 1000] NO disparan el ValueError de rango."""
    with pytest.raises(_Centinela):
        valorar_portafolio_derivados(*_BASE_ARGS, max_iter_canal=max_iter_canal)


def test_defaults_conjuntos_superan_validacion(bloquear_db):
    """Con todos los defaults, la validación se supera hasta el acceso a DB."""
    with pytest.raises(_Centinela):
        valorar_portafolio_derivados(*_BASE_ARGS)


# =====================================================================
# 4. `canal_opc` no tiene validación de rango (Req 4.9): se acepta tal cual
# =====================================================================

@pytest.mark.parametrize("canal_opc", [0, 1650, 5000, -100])
def test_canal_opc_no_valida_rango_y_supera_validacion(canal_opc, bloquear_db):
    """
    `canal_opc` es configurable sin rango declarado (Req 4.9): cualquier valor
    supera el bloque de validación fail-fast.
    """
    with pytest.raises(_Centinela):
        valorar_portafolio_derivados(*_BASE_ARGS, canal_opc=canal_opc)
