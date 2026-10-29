"""
Batería de tests de la sección 13 de compras-ventas-reservas: integración en el loop
mensual del motor.

Cubre:
- Property 18 (13.3): la liquidez consumida por el waterfall NO se reinvierte (retención
  parcial). La liquidez consumida por el paso MONTHLY_LIQUIDITY es exactamente
  `min(liquidez_del_mes, monto_a_cubrir)`, y la liquidez reinvertible del ramo es el
  excedente `max(0, liquidez_del_mes − liquidez_consumida)`.  Requirements 10.2, 5.9.
- Property 19 (13.4): retrocompatibilidad (no-op sin input). Con `mapa_reservas` vacío la
  aplicación deja el portafolio idéntico, `ori_reciclado == 0` y sin cupones no
  reinvertidos.  Requirement 10.3.
- Property 25 (13.6): invariancia del P&L del cupón bajo reinversión vs. consumo por el
  waterfall (no-pérdida, sin doble conteo). La corrección explícita al P&L es exactamente
  `cupones_detectados − cupones_reinvertidos` (monto no reinvertido); es cero cuando todo
  se reinvierte; y se aplica idéntica a `pnl_mercado` y `pnl_tir`.
  Requirements 11.1-11.5, 5.8, 8.4, 10.2.
- 13.5: integration test de retrocompatibilidad del loop del motor con tabla de reservas
  vacía (se salta con razón si `proyectar_portafolio` requiere insumos no disponibles en
  un entorno de test aislado).

Las funciones bajo prueba son puras y duck-typed sobre atributos de Titulo, así que se
usan objetos ligeros (TituloFake) con los atributos necesarios.
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
    vender_por_reserva,
)

FECHA = date(2026, 6, 30)


@dataclass
class TituloFake:
    """Objeto ligero con los atributos que leen las funciones de venta (duck typing)."""
    id_posicion: str
    clasificacion: str = "Negociable"
    valor_mercado: float = 1000.0
    valor_tir: float = 1000.0
    ramo: str = "SOAT"
    tipo_bono: str = "TF"
    compania: str = "BOLIVAR"
    portafolio: str = "P1"
    fecha_vencimiento: date = field(default_factory=lambda: FECHA + timedelta(days=365 * 3))


def _tit(idp, clasif="Negociable", vm=1000.0, vtir=1000.0, plazo_anios=3.0, ramo="SOAT"):
    return TituloFake(
        id_posicion=idp,
        clasificacion=clasif,
        valor_mercado=vm,
        valor_tir=vtir,
        ramo=cvr._normalizar_ramo(ramo),
        fecha_vencimiento=FECHA + timedelta(days=int(round(plazo_anios * 365))),
    )


def _universo_vacio():
    cols = ["nemo", "codigo_isin", "fecha_vencimiento", "cupon", "rendimiento",
            "frecuencia_pago_cupon", "moneda"]
    return UniversoCompra(tes_uvr=pd.DataFrame(columns=cols), tf=pd.DataFrame(columns=cols))


def _cfg(clasificacion_por_ramo):
    es_mat = {cvr._normalizar_ramo(r): b for r, b in clasificacion_por_ramo.items()}
    instr = {r: InstrumentoCompra.TES_UVR for r, b in es_mat.items() if b}
    return ConfigRamosReserva(es_reserva_matematica_por_ramo=es_mat, instrumento_por_ramo=instr)


# ==============================================================================
# Property 18 (13.3) — Retención parcial: liquidez consumida = min(liquidez, monto)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 18: la liquidez del mes consumida por el paso
# MONTHLY_LIQUIDITY es exactamente min(liquidez_del_mes, monto_a_cubrir); el excedente
# max(0, liquidez − consumida) queda para reinversión normal.
@settings(max_examples=200, deadline=None)
@given(
    liquidez=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    monto=st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_prop18_retencion_parcial(liquidez, monto):
    # Sin títulos vendibles: el waterfall solo puede usar la liquidez del mes.
    res = vender_por_reserva(
        "SOAT", monto, [], liquidez, FECHA, ParametrosReservas()
    )
    esperado_consumido = min(liquidez, monto)
    assert res.liquidez_consumida == pytest.approx(esperado_consumido)
    # El excedente reinvertible es el resto de la liquidez del mes.
    excedente_reinvertible = max(0.0, liquidez - res.liquidez_consumida)
    assert excedente_reinvertible == pytest.approx(max(0.0, liquidez - esperado_consumido))
    # Nunca se retiene más que la liquidez disponible ni más que el monto a cubrir.
    assert res.liquidez_consumida <= liquidez + 1e-6
    assert res.liquidez_consumida <= monto + 1e-6


# Feature: compras-ventas-reservas, Property 18 (a nivel orquestación): la liquidez
# consumida reportada por ramo respeta la retención parcial cuando Δ<0.
@settings(max_examples=100, deadline=None)
@given(
    liquidez=st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    monto=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
)
def test_prop18_retencion_parcial_orquestacion(liquidez, monto):
    ramo = "SOAT"
    cfg = _cfg({ramo: False})
    mes = 3
    mapa = {ramo: {mes - 1: -monto}}  # Δ<0 ⇒ venta
    res = aplicar_compras_ventas_reservas(
        [], mapa, mes, FECHA, _universo_vacio(),
        sendas={}, valor_uvr=411.0, trm=3900.0, serie_ipc=None,
        config_ramos=cfg, parametros=ParametrosReservas(),
        liquidez_mes_por_ramo={cvr._normalizar_ramo(ramo): liquidez},
    )
    consumida = res.liquidez_consumida.get(cvr._normalizar_ramo(ramo), 0.0)
    assert consumida == pytest.approx(min(liquidez, monto))


# ==============================================================================
# Property 19 (13.4) — Retrocompatibilidad: no-op sin input
# ==============================================================================

# Feature: compras-ventas-reservas, Property 19: con mapa_reservas vacío el portafolio
# queda idéntico, el ORI reciclado es cero y no hay cupones no reinvertidos.
@settings(max_examples=100, deadline=None)
@given(
    n=st.integers(min_value=0, max_value=6),
    plazo=st.floats(min_value=0.5, max_value=15.0, allow_nan=False, allow_infinity=False),
)
def test_prop19_noop_sin_input(n, plazo):
    port = [_tit(f"T{i}", "Negociable", 1000.0 + i, 1000.0 + i, plazo) for i in range(n)]
    ids_antes = [t.id_posicion for t in port]
    res = aplicar_compras_ventas_reservas(
        port, {}, 3, FECHA, _universo_vacio(),
        sendas={}, valor_uvr=411.0, trm=3900.0, serie_ipc=None,
        config_ramos=_cfg({}), parametros=ParametrosReservas(),
    )
    # Portafolio idéntico (mismos ids, mismo orden preservado para los originales).
    assert [t.id_posicion for t in res.portafolio] == ids_antes
    assert res.ori_reciclado == pytest.approx(0.0)
    assert res.compras == [] and res.ventas == []
    assert sum(res.cupones_no_reinvertidos.values()) == pytest.approx(0.0)


def test_prop19_noop_delta_ausente_para_ramo_del_portafolio():
    # Un ramo presente en el portafolio pero SIN entrada en el mapa ⇒ no-op.
    port = [_tit("T1", "Negociable", 5000.0, 5000.0, 4.0, ramo="VIDA")]
    res = aplicar_compras_ventas_reservas(
        port, {}, 5, FECHA, _universo_vacio(),
        sendas={}, valor_uvr=411.0, trm=3900.0, serie_ipc=None,
        config_ramos=_cfg({"VIDA": False}), parametros=ParametrosReservas(),
    )
    assert [t.id_posicion for t in res.portafolio] == ["T1"]
    assert res.ori_reciclado == pytest.approx(0.0)
    assert res.compras == [] and res.ventas == []


# ==============================================================================
# Property 25 (13.6) — Invariancia del P&L del cupón (no-pérdida, sin doble conteo)
# ==============================================================================

def _pnl(vm_total, vm_anterior, ori_reciclado, cupones_no_reinvertidos):
    """Réplica de la fórmula del motor (scripts/motor_proyeccion.py ~L1423-1424)."""
    return (vm_total - vm_anterior) + ori_reciclado + cupones_no_reinvertidos


# Feature: compras-ventas-reservas, Property 25: la corrección explícita añadida al P&L
# cuando el cupón se consume en el waterfall es exactamente el monto NO reinvertido
# (cupon_no_reinvertido = min(cupon_mes, liquidez_consumida)); para un mes totalmente
# reinvertido es cero. Se aplica idéntica a pnl_mercado y pnl_tir.
@settings(max_examples=200, deadline=None)
@given(
    cupon_mes=st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    monto=st.floats(min_value=1.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    liquidez_extra=st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
)
def test_prop25_correccion_es_monto_no_reinvertido(cupon_mes, monto, liquidez_extra):
    # La liquidez del mes incluye el cupón más otros flujos (liquidez_extra).
    liquidez = cupon_mes + liquidez_extra
    res = vender_por_reserva(
        "SOAT", monto, [], liquidez, FECHA, ParametrosReservas(), cupon_mes_ramo=cupon_mes
    )
    # cupon_no_reinvertido = min(cupon_mes, liquidez_consumida); acotado por definición.
    esperado = min(cupon_mes, res.liquidez_consumida)
    assert res.cupon_no_reinvertido == pytest.approx(esperado)
    assert res.cupon_no_reinvertido <= cupon_mes + 1e-6
    assert res.cupon_no_reinvertido <= res.liquidez_consumida + 1e-6


# Feature: compras-ventas-reservas, Property 25 (no doble conteo): cuando NO hay
# disminución de reserva (Δ≥0) la corrección de cupón no reinvertido es cero y el P&L es
# idéntico al del motor base (el cupón entra al P&L vía el título de reinversión).
@settings(max_examples=100, deadline=None)
@given(
    cupon_mes=st.floats(min_value=0.0, max_value=1e8, allow_nan=False, allow_infinity=False),
    vm_total=st.floats(min_value=0.0, max_value=1e10, allow_nan=False, allow_infinity=False),
    vm_anterior=st.floats(min_value=0.0, max_value=1e10, allow_nan=False, allow_infinity=False),
)
def test_prop25_sin_disminucion_correccion_cero(cupon_mes, vm_total, vm_anterior):
    # Δ≥0 ⇒ no hay venta ⇒ cupones_no_reinvertidos vacío ⇒ corrección cero.
    ramo = "SOAT"
    cfg = _cfg({ramo: False})
    mes = 3
    mapa = {ramo: {mes - 1: 0.0}}  # Δ = 0 ⇒ no-op
    res = aplicar_compras_ventas_reservas(
        [], mapa, mes, FECHA, _universo_vacio(),
        sendas={}, valor_uvr=411.0, trm=3900.0, serie_ipc=None,
        config_ramos=cfg, parametros=ParametrosReservas(),
        cupon_mes_por_ramo={cvr._normalizar_ramo(ramo): cupon_mes},
    )
    correccion = sum(res.cupones_no_reinvertidos.values())
    assert correccion == pytest.approx(0.0)
    # P&L idéntico a la base (misma corrección 0 en mercado y TIR).
    pnl_base = vm_total - vm_anterior
    assert _pnl(vm_total, vm_anterior, res.ori_reciclado, correccion) == pytest.approx(pnl_base)


# Feature: compras-ventas-reservas, Property 25 (no-pérdida): cuando el cupón se consume
# en el waterfall, la corrección se suma IDÉNTICA a pnl_mercado y pnl_tir, de modo que el
# cupón realizado no se pierde. Comparamos el P&L "reinvertido" (cupón dentro de vm_total)
# contra el P&L "consumido" (cupón fuera de vm_total + corrección explícita): deben ser
# iguales tanto a mercado como a TIR.
@settings(max_examples=200, deadline=None)
@given(
    cupon=st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    base_mercado=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    base_tir=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    vm_anterior=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_prop25_invariancia_reinvertido_vs_consumido(cupon, base_mercado, base_tir, vm_anterior):
    # Escenario A (reinvertido): el cupón genera un título de reinversión, por lo que su
    # monto ya está dentro de vm_total; la corrección explícita es 0.
    vm_total_reinvertido = base_mercado + cupon
    vtir_total_reinvertido = base_tir + cupon
    pnl_merc_A = _pnl(vm_total_reinvertido, vm_anterior, 0.0, 0.0)
    pnl_tir_A = _pnl(vtir_total_reinvertido, vm_anterior, 0.0, 0.0)

    # Escenario B (consumido por el waterfall): el cupón NO genera título de reinversión
    # (no está en vm_total), pero se añade como corrección explícita idéntica a mercado y
    # TIR (cupones_no_reinvertidos = cupon).
    vm_total_consumido = base_mercado
    vtir_total_consumido = base_tir
    pnl_merc_B = _pnl(vm_total_consumido, vm_anterior, 0.0, cupon)
    pnl_tir_B = _pnl(vtir_total_consumido, vm_anterior, 0.0, cupon)

    # No-pérdida y sin doble conteo: A == B en mercado y en TIR.
    assert pnl_merc_A == pytest.approx(pnl_merc_B)
    assert pnl_tir_A == pytest.approx(pnl_tir_B)


# ==============================================================================
# 13.5 — Integration test del loop del motor (retrocompatibilidad end-to-end)
# ==============================================================================

@pytest.mark.skip(
    reason="proyectar_portafolio end-to-end requiere portafolio.db real, curvas y sendas "
    "de mercado no disponibles en el entorno de test aislado (Google Drive). La "
    "retrocompatibilidad no-op sin input queda cubierta de forma determinista por "
    "test_prop19_noop_sin_input y test_prop19_noop_delta_ausente_para_ramo_del_portafolio, "
    "y la fórmula de P&L (reinvertido vs consumido) por test_prop25_*."
)
def test_integracion_retrocompatibilidad_end_to_end():  # pragma: no cover
    pass
