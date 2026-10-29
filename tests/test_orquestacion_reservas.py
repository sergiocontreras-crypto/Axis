"""
Batería de tests de la sección 12 de compras-ventas-reservas: orquestación por mes
(`aplicar_compras_ventas_reservas`).

Cubre:
- Property 2: la variación se interpreta mes a mes (no acumulada) — se usa el valor
  almacenado de Δ(t-1) tal cual.
- Property 3: convención de mes caído — para el mes t se consulta Δ del mes t-1.
- Property 4: mapeo de decisión por signo (Δ>0 compra; Δ<0 venta; Δ=0/ausente no-op).
- Property 17: independencia por ramo (procesar un ramo aislado da el mismo resultado;
  modificar un ramo no altera a otro).
- Property 21: ramos con Δ≠0 sin clasificar se omiten con advertencia (pendientes).

La compra usa el universo/valoración reales del módulo; para aislar la orquestación se
usa un universo con TUVT/TFIT sintéticos y un portafolio de TituloFake (duck-typed).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    ConfigRamosReserva,
    InstrumentoCompra,
    ParametrosReservas,
    UniversoCompra,
    aplicar_compras_ventas_reservas,
)

FECHA = date(2026, 6, 30)


@dataclass
class TituloFake:
    id_posicion: str
    ramo: str
    clasificacion: str = "Negociable"
    valor_mercado: float = 1000.0
    valor_tir: float = 1000.0
    tipo_bono: str = "TF"
    compania: str = "BOLIVAR"
    portafolio: str = "P1"
    fecha_vencimiento: date = field(default_factory=lambda: FECHA + timedelta(days=365 * 3))


def _universo():
    vencs = [FECHA + timedelta(days=365 * n) for n in (2, 5, 8)]
    def _df(prefijo):
        filas = [
            {
                "nemo": f"{prefijo}{i}",
                "codigo_isin": f"ISIN{prefijo}{i}",
                "fecha_vencimiento": v,
                "cupon": 6.0,
                "rendimiento": 8.0,
                "frecuencia_pago_cupon": 2,
                "moneda": "UVR" if prefijo == "TUVT" else "COP",
            }
            for i, v in enumerate(vencs)
        ]
        cols = ["nemo", "codigo_isin", "fecha_vencimiento", "cupon", "rendimiento", "frecuencia_pago_cupon", "moneda"]
        return pd.DataFrame(filas, columns=cols)
    return UniversoCompra(tes_uvr=_df("TUVT"), tf=_df("TFIT"))


def _cfg(clasificacion_por_ramo):
    """clasificacion_por_ramo: {ramo: bool es_matematica} (todos con instrumento TES_UVR si mat)."""
    es_mat = {cvr._normalizar_ramo(r): b for r, b in clasificacion_por_ramo.items()}
    instr = {r: InstrumentoCompra.TES_UVR for r, b in es_mat.items() if b}
    return ConfigRamosReserva(es_reserva_matematica_por_ramo=es_mat, instrumento_por_ramo=instr)


def _aplicar(portafolio, mapa, mes, cfg, **kw):
    return aplicar_compras_ventas_reservas(
        portafolio, mapa, mes, FECHA, _universo(),
        sendas={}, valor_uvr=411.0, trm=3900.0, serie_ipc=None,
        config_ramos=cfg, parametros=ParametrosReservas(), **kw
    )


# ==============================================================================
# Property 3 — Convención de mes caído
# ==============================================================================

# Feature: compras-ventas-reservas, Property 3: para el mes t se decide con Δ(t-1).
@settings(max_examples=100, deadline=None)
@given(
    mes=st.integers(min_value=1, max_value=11),
    delta_prev=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
)
def test_prop3_mes_caido(mes, delta_prev):
    cfg = _cfg({"SOAT": False})
    # Δ positivo en el mes t-1 (caído) => compra en el mes t.
    mapa = {"SOAT": {mes - 1: delta_prev}}
    res = _aplicar([], mapa, mes, cfg)
    assert len(res.compras) == 1  # se usó Δ(t-1)
    # Si en cambio el Δ estuviera en el mes t (no t-1), no debería comprar.
    mapa2 = {"SOAT": {mes: delta_prev}}
    res2 = _aplicar([], mapa2, mes, cfg)
    assert len(res2.compras) == 0


# ==============================================================================
# Property 4 — Mapeo de decisión por signo
# ==============================================================================

# Feature: compras-ventas-reservas, Property 4: Δ>0 compra; Δ<0 venta; Δ=0/ausente no-op.
@settings(max_examples=100, deadline=None)
@given(
    delta=st.floats(min_value=-1e8, max_value=1e8, allow_nan=False, allow_infinity=False),
)
def test_prop4_mapeo_por_signo(delta):
    ramo = "SOAT"
    cfg = _cfg({ramo: False})
    # Portafolio con un Negociable del ramo, elegible por plazo, para permitir venta.
    port = [TituloFake("T1", cvr._normalizar_ramo(ramo), "Negociable", 5000.0, 5000.0)]
    mes = 3
    mapa = {ramo: {mes - 1: delta}}
    res = _aplicar(port, mapa, mes, cfg, liquidez_mes_por_ramo={cvr._normalizar_ramo(ramo): 0.0})
    if delta > 0:
        assert len(res.compras) == 1 and len(res.ventas) == 0
    elif delta < 0:
        assert len(res.compras) == 0  # es venta (puede o no vender según montos)
    else:
        assert len(res.compras) == 0 and len(res.ventas) == 0


def test_prop4_delta_cero_es_noop():
    ramo = "SOAT"
    cfg = _cfg({ramo: False})
    port = [TituloFake("T1", cvr._normalizar_ramo(ramo))]
    res = _aplicar(port, {ramo: {2: 0.0}}, 3, cfg)
    assert res.compras == [] and res.ventas == []
    # Portafolio inalterado (mismo conjunto de ids).
    assert {t.id_posicion for t in res.portafolio} == {"T1"}


# ==============================================================================
# Property 2 — Interpretación mes a mes (no acumulada)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 2: se usa Δ(t-1) tal cual, no una suma
# acumulada de meses anteriores.
@settings(max_examples=100, deadline=None)
@given(
    d1=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    d2=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
)
def test_prop2_no_acumulada(d1, d2):
    ramo = "SOAT"
    cfg = _cfg({ramo: False})
    # Δ en meses 1 y 2; al procesar el mes 3 se usa SOLO Δ(mes 2), no d1+d2.
    mapa = {ramo: {1: d1, 2: d2}}
    res = _aplicar([], mapa, 3, cfg)
    assert len(res.compras) == 1
    # El monto de la compra (valor_mercado) == d2 (Δ del mes caído), no d1+d2.
    assert res.compras[0].valor_mercado == pytest.approx(d2)


# ==============================================================================
# Property 21 — Ramos sin clasificar con Δ≠0 se omiten con advertencia
# ==============================================================================

# Feature: compras-ventas-reservas, Property 21: un ramo con Δ≠0 sin fila en la
# clasificación se omite (no-op para ese ramo) y se reporta como pendiente.
@settings(max_examples=100, deadline=None)
@given(
    delta=st.floats(min_value=-1e7, max_value=1e7, allow_nan=False, allow_infinity=False).filter(lambda x: abs(x) > 1e-6),
)
def test_prop21_sin_clasificar_se_omite(delta):
    ramo = "RAMO NUEVO SIN CLASIFICAR"
    cfg = _cfg({})  # vacío: nada clasificado
    port = [TituloFake("T1", cvr._normalizar_ramo(ramo), "Negociable", 5000.0, 5000.0)]
    mes = 4
    res = _aplicar(port, {ramo: {mes - 1: delta}}, mes, cfg)
    # No-op para el ramo: sin compras ni ventas.
    assert res.compras == [] and res.ventas == []
    # Reportado como pendiente.
    assert cvr._normalizar_ramo(ramo) in [cvr._normalizar_ramo(r) for r in res.ramos_pendientes]
    # Portafolio del ramo idéntico.
    assert {t.id_posicion for t in res.portafolio} == {"T1"}


# ==============================================================================
# Property 17 — Independencia por ramo
# ==============================================================================

# Feature: compras-ventas-reservas, Property 17: procesar dos ramos juntos produce,
# para cada ramo, el mismo resultado que procesarlo aislado; modificar un ramo no
# altera el resultado del otro.
@settings(max_examples=100, deadline=None)
@given(
    d_a=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    d_b=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
)
def test_prop17_independencia_por_ramo(d_a, d_b):
    ra, rb = "SOAT", "AUTOMOVILES"
    cfg = _cfg({ra: False, rb: False})
    mes = 3
    mapa_ambos = {ra: {mes - 1: d_a}, rb: {mes - 1: d_b}}
    res_ambos = _aplicar([], mapa_ambos, mes, cfg)

    # Aislado A.
    res_a = _aplicar([], {ra: {mes - 1: d_a}}, mes, cfg)
    # Aislado B.
    res_b = _aplicar([], {rb: {mes - 1: d_b}}, mes, cfg)

    def _monto_por_ramo(res, ramo):
        return sorted(
            round(c.valor_mercado, 6) for c in res.compras if c.ramo == cvr._normalizar_ramo(ramo)
        )

    # El resultado por ramo en la corrida conjunta == corrida aislada.
    assert _monto_por_ramo(res_ambos, ra) == _monto_por_ramo(res_a, ra)
    assert _monto_por_ramo(res_ambos, rb) == _monto_por_ramo(res_b, rb)
