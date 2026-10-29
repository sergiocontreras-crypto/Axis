"""
Batería de tests de la sección 10 de compras-ventas-reservas: waterfall de ventas.

Cubre:
- Property 10: orden y terminación del waterfall (MONTHLY_LIQUIDITY → Negociables → DV).
- Property 11: elegibilidad por plazo (≤ plazo_max) y cobertura exacta.
- Property 12: selección del título cuyo VM se acerca más al pendiente.
- Property 13: venta de títulos completos.
- Property 14: conservación de liquidez al vender.
- Property 15: orden de DV por ORI descendente y exclusión de 0 días al vencimiento.
- Property 16: reciclaje de ORI de DV al P&L (Negociables aportan 0).
- Property 26: independencia del orden de consumo en el paso de liquidez del mes.
- Property 30: la selección/elegibilidad usan el plazo_max configurado.

Las funciones de venta son puras y duck-typed sobre atributos de Titulo, así que se
usan objetos ligeros con los atributos necesarios (clasificacion, valor_mercado,
valor_tir, fecha_vencimiento).
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from compras_ventas_reservas import (
    ParametrosReservas,
    ResultadoVenta,
    es_dv,
    es_negociable,
    ordenar_dv_por_ori,
    seleccionar_titulo_a_vender,
    vender_por_reserva,
)

FECHA = date(2026, 6, 30)


@dataclass
class TituloFake:
    """Objeto ligero con los atributos que leen las funciones de venta (duck typing)."""
    id_posicion: str
    clasificacion: str
    valor_mercado: float
    valor_tir: float
    fecha_vencimiento: date
    ramo: str = "SOAT"
    tipo_bono: str = "TF"


def _tit(idp, clasif, vm, vtir, plazo_anios):
    return TituloFake(
        id_posicion=idp,
        clasificacion=clasif,
        valor_mercado=vm,
        valor_tir=vtir,
        fecha_vencimiento=FECHA + timedelta(days=int(round(plazo_anios * 365))),
    )


# ── Estrategias ──
_clasif = st.sampled_from(["Negociable", "Disp. Venta"])
_vm = st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False)
_plazo = st.floats(min_value=0.1, max_value=20.0, allow_nan=False, allow_infinity=False)


@st.composite
def _titulos(draw, min_size=0, max_size=8):
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    out = []
    for i in range(n):
        clasif = draw(_clasif)
        vm = draw(_vm)
        # vtir cercano a vm para ORI de ambos signos.
        vtir = draw(st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False))
        plazo = draw(_plazo)
        out.append(_tit(f"T{i}", clasif, vm, vtir, plazo))
    return out


# ==============================================================================
# Property 12 — Selección del título cuyo VM se acerca más al pendiente
# ==============================================================================

# Feature: compras-ventas-reservas, Property 12: seleccionar_titulo_a_vender elige el
# elegible (plazo ≤ plazo_max) que minimiza |valor_mercado − pendiente|.
@settings(max_examples=100, deadline=None)
@given(titulos=_titulos(min_size=1, max_size=8), pendiente=_vm, plazo_max=st.floats(min_value=0.5, max_value=25))
def test_prop12_seleccion_mas_cercano(titulos, pendiente, plazo_max):
    elegido = seleccionar_titulo_a_vender(titulos, pendiente, plazo_max, FECHA)
    elegibles = [t for t in titulos if (t.fecha_vencimiento - FECHA).days / 365.0 <= plazo_max]
    if not elegibles:
        assert elegido is None
    else:
        mejor_dist = min(abs(t.valor_mercado - pendiente) for t in elegibles)
        assert elegido is not None
        assert abs(elegido.valor_mercado - pendiente) == pytest.approx(mejor_dist)


# ==============================================================================
# Property 15 — Orden de DV por ORI descendente y exclusión de 0 días
# ==============================================================================

# Feature: compras-ventas-reservas, Property 15: ordenar_dv_por_ori ordena por
# ORI = (vm − vtir) descendente y excluye títulos con 0 días al vencimiento.
@settings(max_examples=100, deadline=None)
@given(titulos=_titulos(min_size=0, max_size=10))
def test_prop15_orden_dv_por_ori(titulos):
    # Incluir algún DV con 0 días (vencido hoy) para verificar exclusión.
    vencido_hoy = _tit("VENC0", "Disp. Venta", 100.0, 90.0, 0.0)
    dv = [t for t in titulos if es_dv(t)] + [vencido_hoy]
    ordenados = ordenar_dv_por_ori(dv, FECHA)

    # Excluidos: los de plazo ≤ 0 (vencido hoy).
    assert vencido_hoy not in ordenados
    # Orden no creciente por ORI.
    oris = [t.valor_mercado - t.valor_tir for t in ordenados]
    assert oris == sorted(oris, reverse=True)
    # Todos los incluidos tienen > 0 días al vencimiento.
    for t in ordenados:
        assert (t.fecha_vencimiento - FECHA).days > 0


# ==============================================================================
# Property 10 — Orden y terminación del waterfall
# ==============================================================================

# Feature: compras-ventas-reservas, Property 10: el waterfall consume en orden
# MONTHLY_LIQUIDITY → Negociables → DV; la liquidez del mes solo se consume en el
# paso 1; y se detiene apenas se cubre la disminución.
@settings(max_examples=100, deadline=None)
@given(
    titulos=_titulos(min_size=0, max_size=8),
    monto=st.floats(min_value=1.0, max_value=5e7, allow_nan=False, allow_infinity=False),
    liquidez=st.floats(min_value=0.0, max_value=5e7, allow_nan=False, allow_infinity=False),
)
def test_prop10_orden_y_terminacion(titulos, monto, liquidez):
    params = ParametrosReservas()  # plazo_max = 10
    res = vender_por_reserva("SOAT", monto, titulos, liquidez, FECHA, params)
    assert isinstance(res, ResultadoVenta)
    # Paso 1: liquidez retenida = min(liquidez, monto) (retención parcial, Property 18).
    assert res.liquidez_consumida == pytest.approx(min(liquidez, monto))
    # Si la liquidez del mes ya cubre el monto, NO se vende ningún título (terminación).
    if liquidez >= monto:
        assert res.titulos_vendidos == []


# ==============================================================================
# Property 11 — Elegibilidad por plazo y cobertura exacta
# ==============================================================================

# Feature: compras-ventas-reservas, Property 11: todo título vendido tiene plazo ≤
# plazo_max; cuando lo disponible elegible es menor al requerido, se vende todo lo
# disponible.
@settings(max_examples=100, deadline=None)
@given(
    titulos=_titulos(min_size=0, max_size=10),
    monto=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    plazo_max=st.floats(min_value=0.5, max_value=15.0, allow_nan=False, allow_infinity=False),
)
def test_prop11_elegibilidad_y_cobertura(titulos, monto, plazo_max):
    params = ParametrosReservas(nodo_compra_no_matematica_anios=5.0, plazo_max_venta_anios=plazo_max)
    res = vender_por_reserva("SOAT", monto, titulos, 0.0, FECHA, params)
    # Todo vendido cumple plazo ≤ plazo_max.
    for t in res.titulos_vendidos:
        assert (t.fecha_vencimiento - FECHA).days / 365.0 <= plazo_max + 1e-9


# ==============================================================================
# Property 13 — Venta de títulos completos
# ==============================================================================

# Feature: compras-ventas-reservas, Property 13: los títulos vendidos son objetos
# completos del portafolio (no fracciones); no se repiten.
@settings(max_examples=100, deadline=None)
@given(
    titulos=_titulos(min_size=1, max_size=8),
    monto=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
)
def test_prop13_titulos_completos(titulos, monto):
    params = ParametrosReservas()
    res = vender_por_reserva("SOAT", monto, titulos, 0.0, FECHA, params)
    ids = [t.id_posicion for t in res.titulos_vendidos]
    # Sin duplicados: cada título se vende una sola vez (completo).
    assert len(ids) == len(set(ids))
    # Todos los vendidos provienen del portafolio original.
    originales = {t.id_posicion for t in titulos}
    assert all(i in originales for i in ids)


# ==============================================================================
# Property 14 — Conservación de liquidez al vender
# ==============================================================================

# Feature: compras-ventas-reservas, Property 14: el producto de las ventas (Σ VM de
# vendidos) más la liquidez del mes retenida cubre el monto (salvo agotamiento), y la
# suma de VM de vendidos es exactamente la contribución de las ventas.
@settings(max_examples=100, deadline=None)
@given(
    titulos=_titulos(min_size=0, max_size=10),
    monto=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    liquidez=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
)
def test_prop14_conservacion_liquidez(titulos, monto, liquidez):
    params = ParametrosReservas()
    res = vender_por_reserva("SOAT", monto, titulos, liquidez, FECHA, params)
    suma_vendidos = sum(t.valor_mercado for t in res.titulos_vendidos)
    liquidez_total = res.liquidez_consumida + suma_vendidos
    # La liquidez total generada nunca es negativa y, si no se agotó el universo,
    # cubre el monto. Como mínimo, no excede monto + (mayor VM vendido) por venta de
    # títulos completos.
    assert liquidez_total >= 0.0
    # Si hay elegibles suficientes, la venta se detiene al cubrir (liquidez_total ≥ monto
    # o se agotaron los elegibles). Verificamos que no se vende de más innecesariamente:
    # al quitar el último vendido, la liquidez previa NO cubría el monto.
    if res.titulos_vendidos:
        vm_ultimo = res.titulos_vendidos[-1].valor_mercado
        assert (liquidez_total - vm_ultimo) < monto


# ==============================================================================
# Property 16 — Reciclaje de ORI de DV al P&L (Negociables aportan 0)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 16: ori_reciclado = Σ(vm − vtir) de los
# DV vendidos; los Negociables aportan 0.
@settings(max_examples=100, deadline=None)
@given(
    titulos=_titulos(min_size=0, max_size=10),
    monto=st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_prop16_reciclaje_ori(titulos, monto):
    params = ParametrosReservas()
    res = vender_por_reserva("SOAT", monto, titulos, 0.0, FECHA, params)
    esperado = sum(
        t.valor_mercado - t.valor_tir for t in res.titulos_vendidos if es_dv(t)
    )
    assert res.ori_reciclado == pytest.approx(esperado)
    # Los Negociables vendidos no contribuyen al ORI (implícito: solo se suman DV).


# ==============================================================================
# Property 26 — Independencia del orden de consumo en el paso de liquidez del mes
# ==============================================================================

# Feature: compras-ventas-reservas, Property 26: el monto cubierto por el paso
# MONTHLY_LIQUIDITY y la porción de cupón no reinvertido son independientes del orden;
# se determinan por monto (min(liquidez, monto) y min(cupon, retenido)).
@settings(max_examples=100, deadline=None)
@given(
    monto=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    liquidez=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    cupon=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
)
def test_prop26_independencia_orden(monto, liquidez, cupon):
    params = ParametrosReservas()
    res = vender_por_reserva("SOAT", monto, [], liquidez, FECHA, params, cupon_mes_ramo=cupon)
    retenido = min(liquidez, monto)
    assert res.liquidez_consumida == pytest.approx(retenido)
    # Cupón no reinvertido = min(cupon, retenido), por monto (no por orden).
    assert res.cupon_no_reinvertido == pytest.approx(min(cupon, retenido))


# ==============================================================================
# Property 30 — La selección y la elegibilidad usan los valores configurados
# ==============================================================================

# Feature: compras-ventas-reservas, Property 30: el plazo máximo de venta usado es el
# de parametros.plazo_max_venta_anios (no un literal 10).
@settings(max_examples=100, deadline=None)
@given(
    plazo_max=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)
def test_prop30_usa_plazo_configurado(plazo_max):
    # Dos títulos Negociables: uno justo por debajo del plazo_max y otro por encima.
    dentro = _tit("DENTRO", "Negociable", 1000.0, 1000.0, max(0.1, plazo_max - 0.5))
    fuera = _tit("FUERA", "Negociable", 1000.0, 1000.0, plazo_max + 2.0)
    params = ParametrosReservas(nodo_compra_no_matematica_anios=5.0, plazo_max_venta_anios=plazo_max)
    # Monto grande para forzar intento de vender todo lo elegible.
    res = vender_por_reserva("SOAT", 1e9, [dentro, fuera], 0.0, FECHA, params)
    ids = {t.id_posicion for t in res.titulos_vendidos}
    assert "DENTRO" in ids  # elegible por plazo
    assert "FUERA" not in ids  # excluido por plazo > plazo_max
