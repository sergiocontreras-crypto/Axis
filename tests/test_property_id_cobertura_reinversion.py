# -*- coding: utf-8 -*-
"""
Pruebas de propiedad — Defecto 1 (herencia de `id_cobertura` en cupones reinvertidos).

Feature / spec: excel-por-tramos-consistencia. Tarea 1.1 del plan de implementación
(test-first).

Property 1 (P1) — Herencia de `id_cobertura` y condición de subyacente:
    Para todo `Titulo` origen y cualquier cupón/vencimiento reinvertido a partir de él, el
    `Titulo` reinvertido hereda EXACTAMENTE el `id_cobertura` del origen; en consecuencia el
    `es_subyacente` derivado (vía `tools/detalle_pyg_tramo.py::construir_rf_detalle`) es
    verdadero si y solo si `bool(str(id_cobertura).strip())`.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**

Property 5 (P5) — Regresión del motor; campos inalterados salvo `id_cobertura`:
    Para toda reinversión de cupón o vencimiento, todos los campos del `Titulo` reinvertido son
    idénticos a los que producía el motor antes del cambio (snapshot de la construcción previa),
    con la única excepción permitida de `id_cobertura`.

    **Validates: Requirements 5.2**

Feature: excel-por-tramos-consistencia, Property 1: Herencia de id_cobertura y condición de
subyacente.
Feature: excel-por-tramos-consistencia, Property 5: Regresión del motor — campos inalterados
salvo id_cobertura.

Nota de estado esperado (test-first): sobre el código SIN el cambio de la tarea 1.2, P1 debe
FALLAR (el cupón/vencimiento reinvertido llega con `id_cobertura=""`); P5 debe PASAR (la única
diferencia frente al snapshot previo es, por construcción, `id_cobertura`, que es exactamente
la excepción permitida).

El `Titulo` por reinversión de VENCIMIENTO no se expone en el valor de retorno de
`proyectar_portafolio` (que solo devuelve `list[ResultadoMes]`). Para observarlo SIN tocar
internals del motor, se envuelve el símbolo público `motor_proyeccion.Titulo` con un espía que
captura cada instancia construida durante la proyección; luego se filtran los hijos por
vencimiento (`es_reinversion=True` y sufijo `_V` en el id).

Convención de ejecución (Windows / Google Drive):
    python -m pytest "g:\\Mi unidad\\AXIS\\AXIS\\tests\\test_property_id_cobertura_reinversion.py" ^
      --rootdir "g:\\Mi unidad\\AXIS\\AXIS" -o addopts="" -p no:cacheprovider
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import motor_proyeccion as mp  # scripts/ está en sys.path vía conftest

# `tools/` no está en sys.path por conftest; se añade aquí la raíz del proyecto
# (misma convención que `tests/test_comparar_snapshots_equivalencia.py`).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.detalle_pyg_tramo import construir_rf_detalle, snapshot_inicial  # noqa: E402


# ==============================================================================
# Generadores (smart strategies acotadas al espacio de entrada real)
# ==============================================================================

# `id_cobertura` es un str; su "verdad de subyacente" se deriva de
# bool(str(id_cobertura).strip()). Se generan tanto valores vacíos/espacios (no
# subyacente) como no vacíos (subyacente), para ejercitar el si-y-solo-si.
_id_cobertura_vacio = st.sampled_from(["", "   ", "\t", "\n"])
_id_cobertura_no_vacio = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=10
)
id_cobertura_strategy = st.one_of(_id_cobertura_vacio, _id_cobertura_no_vacio)

montos_strategy = st.floats(min_value=1.0, max_value=1_000_000.0,
                            allow_nan=False, allow_infinity=False)
tasas_strategy = st.floats(min_value=0.1, max_value=20.0,
                           allow_nan=False, allow_infinity=False)
precios_strategy = st.floats(min_value=50.0, max_value=150.0,
                             allow_nan=False, allow_infinity=False)


def _titulo_origen(id_cobertura: str, nominal: float = 1000.0,
                   clasificacion: str = "Negociable") -> mp.Titulo:
    """Construye un `Titulo` TF origen con un `id_cobertura` dado."""
    return mp.Titulo(
        id_posicion="ORIG1",
        especie="TFIT1",
        emisor="NACION",
        isin="CO0001",
        moneda="COP",
        fecha_emision=date(2020, 1, 1),
        fecha_vencimiento=date(2032, 1, 1),
        nominal=nominal,
        tasa_cupon=7.0,
        spread_ipc=0.0,
        tipo_bono="TF",
        frecuencia="anual",
        tipo_tasa="EFECTIVO",
        base_anual=365,
        uso_tasa="I",
        clasificacion=clasificacion,
        compania="C1",
        ramo="R1",
        portafolio="P1",
        asset_class="DP LP",
        tasa_compra=8.0,
        tasa_mercado=8.0,
        valor_mercado=nominal,
        valor_tir=nominal,
        spread_mercado=8.0,
        id_cobertura=id_cobertura,
    )


def _cupon_de(origen: mp.Titulo, monto: float) -> mp.Cupon:
    """Cupón sintético cobrado sobre `origen`."""
    return mp.Cupon(
        titulo=origen,
        fecha_pago=date(2026, 6, 30),
        monto_cupon=monto,
        monto_capital=0.0,
        monto_total=monto,
    )


# ==============================================================================
# P1 — Herencia de id_cobertura y condición de subyacente
# ==============================================================================

@settings(deadline=None, max_examples=150,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    id_cobertura=id_cobertura_strategy,
    monto=montos_strategy,
    precio=precios_strategy,
    tasa_nueva=tasas_strategy,
)
def test_prop1_reinvertir_cupon_hereda_id_cobertura(id_cobertura, monto, precio, tasa_nueva):
    """
    Feature: excel-por-tramos-consistencia, Property 1: Herencia de id_cobertura y condición
    de subyacente (ruta `reinvertir_cupon`).

    El `Titulo` reinvertido por cupón hereda exactamente el `id_cobertura` del origen, y su
    `es_subyacente` derivado por `construir_rf_detalle` es True si y solo si el `id_cobertura`
    del origen (strip) es no vacío.

    EXPECTED (código sin corregir): FALLA — el cupón llega con `id_cobertura=""`.
    """
    origen = _titulo_origen(id_cobertura)
    cupon = _cupon_de(origen, monto)

    nuevo = mp.reinvertir_cupon(cupon, precio_sucio_pct=precio, tasa_compra_nueva=tasa_nueva)

    # (AC 1.1) herencia exacta del id_cobertura
    assert nuevo.id_cobertura == origen.id_cobertura, (
        "el cupón reinvertido no heredó el id_cobertura del origen"
    )

    # (AC 1.3 / 1.4) es_subyacente derivado <=> id_cobertura no vacío
    esperado_subyacente = bool(str(id_cobertura).strip())
    obtenido_subyacente = _detalle_es_subyacente([nuevo])
    assert obtenido_subyacente == esperado_subyacente, (
        f"es_subyacente derivado={obtenido_subyacente} != esperado={esperado_subyacente} "
        f"para id_cobertura={id_cobertura!r}"
    )


@settings(deadline=None, max_examples=120,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(
    id_cobertura=id_cobertura_strategy,
    nominal=montos_strategy,
)
def test_prop1_vencimiento_hereda_id_cobertura(monkeypatch_module, id_cobertura, nominal):
    """
    Feature: excel-por-tramos-consistencia, Property 1: Herencia de id_cobertura y condición
    de subyacente (ruta de reinversión por VENCIMIENTO dentro de `proyectar_portafolio`).

    Un título que vence dentro del horizonte genera un `Titulo` hijo por reinversión al
    vencimiento (`es_reinversion=True`, id con sufijo `_V...`). Ese hijo debe heredar el
    `id_cobertura` del título que venció, y su `es_subyacente` derivado debe cumplir el
    si-y-solo-si.

    EXPECTED (código sin corregir): FALLA — el hijo por vencimiento llega con `id_cobertura=""`.
    """
    origen = _titulo_origen(id_cobertura, nominal=nominal)
    hijos = _correr_y_capturar_vencimientos(monkeypatch_module, origen)
    assert hijos, "no se generó ningún Titulo por reinversión de vencimiento"

    for hijo in hijos:
        assert hijo.id_cobertura == origen.id_cobertura, (
            "el título reinvertido por vencimiento no heredó el id_cobertura del origen"
        )
        esperado_subyacente = bool(str(id_cobertura).strip())
        assert _detalle_es_subyacente([hijo]) == esperado_subyacente


# ==============================================================================
# P5 — Regresión: campos inalterados salvo id_cobertura
# ==============================================================================

@settings(deadline=None, max_examples=150,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    id_cobertura=id_cobertura_strategy,
    monto=montos_strategy,
    precio=precios_strategy,
    tasa_nueva=tasas_strategy,
)
def test_prop5_reinvertir_cupon_solo_cambia_id_cobertura(id_cobertura, monto, precio, tasa_nueva):
    """
    Feature: excel-por-tramos-consistencia, Property 5: Regresión del motor — campos
    inalterados salvo id_cobertura (ruta `reinvertir_cupon`).

    Comparación campo a campo del `Titulo` reinvertido contra el snapshot de la construcción
    PREVIA (sin propagar id_cobertura → default ""). La ÚNICA diferencia permitida es
    `id_cobertura`.
    """
    origen = _titulo_origen(id_cobertura)
    cupon = _cupon_de(origen, monto)

    nuevo = mp.reinvertir_cupon(cupon, precio_sucio_pct=precio, tasa_compra_nueva=tasa_nueva)
    esperado_pre_cambio = _snapshot_previo_cupon(origen, cupon, precio, tasa_nueva)

    _assert_solo_difiere_id_cobertura(nuevo, esperado_pre_cambio)


@settings(deadline=None, max_examples=120,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(
    id_cobertura=id_cobertura_strategy,
    nominal=montos_strategy,
)
def test_prop5_vencimiento_solo_cambia_id_cobertura(monkeypatch_module, id_cobertura, nominal):
    """
    Feature: excel-por-tramos-consistencia, Property 5: Regresión del motor — campos
    inalterados salvo id_cobertura (ruta de reinversión por VENCIMIENTO).

    El hijo por vencimiento debe ser idéntico, campo a campo, al mismo hijo generado con un
    origen de `id_cobertura` vacío (construcción previa), salvo por el propio `id_cobertura`.
    """
    origen = _titulo_origen(id_cobertura, nominal=nominal)
    origen_previo = _titulo_origen("", nominal=nominal)

    hijos = _correr_y_capturar_vencimientos(monkeypatch_module, origen)
    hijos_previo = _correr_y_capturar_vencimientos(monkeypatch_module, origen_previo)

    assert hijos and hijos_previo, "no se generaron hijos por vencimiento en ambas corridas"
    assert len(hijos) == len(hijos_previo), "difiere la cantidad de hijos por vencimiento"

    for hijo, hijo_previo in zip(hijos, hijos_previo):
        _assert_solo_difiere_id_cobertura(hijo, hijo_previo, campo_previo_vacio=True)


# ==============================================================================
# Fixtures / Helpers
# ==============================================================================

@pytest.fixture
def monkeypatch_module():
    """
    MonkeyPatch de alcance de función reutilizable dentro de un test de Hypothesis
    (que invoca el cuerpo múltiples veces). Se instancia y deshace manualmente en cada
    captura para no acumular parches entre ejemplos.
    """
    from _pytest.monkeypatch import MonkeyPatch
    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


def _correr_y_capturar_vencimientos(mpatch, origen: mp.Titulo):
    """
    Corre `proyectar_portafolio` con un título que vence en el mes 1 del horizonte y captura
    los `Titulo` construidos durante la proyección envolviendo el símbolo público
    `motor_proyeccion.Titulo`. Devuelve los hijos por reinversión de vencimiento.
    """
    # El título vence dentro del mes 1 (fecha_inicio < vencimiento <= fin de mes 1).
    origen.fecha_vencimiento = date(2026, 1, 20)
    origen.fecha_emision = date(2021, 1, 20)  # plazo ~5 años

    construidos: list[mp.Titulo] = []
    _TituloReal = mp.Titulo

    def _spy(*args, **kwargs):
        inst = _TituloReal(*args, **kwargs)
        construidos.append(inst)
        return inst

    mpatch.setattr(mp, "Titulo", _spy)
    try:
        mp.proyectar_portafolio(
            portafolio=[origen],
            fecha_inicio=date(2026, 1, 1),
            meses_horizonte=2,
            sendas={},
            mapa_reservas={},
        )
    finally:
        mpatch.undo()

    return [t for t in construidos
            if getattr(t, "es_reinversion", False)
            and "_V" in str(getattr(t, "id_posicion", ""))
            and getattr(t, "titulo_origen", "") == origen.id_posicion]


def _detalle_es_subyacente(titulos) -> bool:
    """Deriva `es_subyacente` de un único título vía `construir_rf_detalle`."""
    vm_ini, vtir_ini = snapshot_inicial(titulos)
    detalle = construir_rf_detalle(
        titulos, vm_ini, vtir_ini, fecha_fin=date(2026, 12, 31)
    )
    assert detalle, "construir_rf_detalle no devolvió filas"
    return bool(detalle[0]["es_subyacente"])


def _snapshot_previo_cupon(origen: mp.Titulo, cupon: mp.Cupon,
                           precio: float, tasa_nueva: float) -> mp.Titulo:
    """
    Reconstruye el `Titulo` tal como lo producía `reinvertir_cupon` ANTES del cambio
    (sin propagar id_cobertura → default ""), replicando exactamente el resto de kwargs.
    """
    if precio > 0:
        nominal_nuevo = cupon.monto_total / (precio / 100.0)
    else:
        nominal_nuevo = cupon.monto_total
    return mp.Titulo(
        id_posicion=f"{origen.id_posicion}_R{cupon.fecha_pago.strftime('%Y%m%d')}",
        especie=origen.especie,
        emisor=origen.emisor,
        isin=origen.isin,
        moneda=origen.moneda,
        fecha_emision=cupon.fecha_pago,
        fecha_vencimiento=origen.fecha_vencimiento,
        nominal=nominal_nuevo,
        tasa_cupon=origen.tasa_cupon,
        spread_ipc=origen.spread_ipc,
        tipo_bono=origen.tipo_bono,
        frecuencia=origen.frecuencia,
        tipo_tasa=origen.tipo_tasa,
        base_anual=origen.base_anual,
        uso_tasa=origen.uso_tasa,
        clasificacion=origen.clasificacion,
        compania=origen.compania,
        ramo=origen.ramo,
        portafolio=origen.portafolio,
        asset_class=origen.asset_class,
        tasa_compra=tasa_nueva,
        tasa_mercado=tasa_nueva,
        valor_mercado=cupon.monto_total,
        valor_tir=cupon.monto_total,
        es_reinversion=True,
        fecha_reinversion=cupon.fecha_pago,
        titulo_origen=origen.id_posicion,
        vm_compra=cupon.monto_total,
        spread_mercado=origen.spread_mercado,
        # id_cobertura NO se pasa: queda en el default "" (construcción previa).
    )


def _assert_solo_difiere_id_cobertura(obtenido: mp.Titulo, esperado: mp.Titulo,
                                      campo_previo_vacio: bool = False):
    """
    Afirma que `obtenido` y `esperado` son idénticos en TODOS los campos salvo
    `id_cobertura` (la única diferencia permitida por P5).
    """
    campos = [f.name for f in dataclasses.fields(mp.Titulo)]
    diferencias = []
    for campo in campos:
        if campo == "id_cobertura":
            continue
        v_obt = getattr(obtenido, campo)
        v_esp = getattr(esperado, campo)
        if v_obt != v_esp:
            diferencias.append((campo, v_esp, v_obt))
    assert not diferencias, (
        f"P5 violada: campos distintos además de id_cobertura: {diferencias}"
    )
    if campo_previo_vacio:
        # El snapshot previo tiene id_cobertura="" (construcción anterior).
        assert esperado.id_cobertura == "", "el snapshot previo debería tener id_cobertura vacío"
