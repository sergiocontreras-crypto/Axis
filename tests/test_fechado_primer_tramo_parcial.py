# -*- coding: utf-8 -*-
"""
Test exploratorio de condición de bug — Defecto 3 (fechado del PRIMER tramo parcial).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix), Fase 2.
Tarea 8 del plan de implementación.

Property 4 (Fechado del primer tramo parcial): para un primer tramo que arranca a mitad
de año (fecha base real, día != 1), la serie del motor debe etiquetar el primer mes
PROYECTADO con el mes de VALORACIÓN (mes SIGUIENTE al T0), de modo que la serie cubra
hasta el mes de cierre SIN perder diciembre y SIN incluir el T0 como un mes de P&G.

**Validates: Requirements 3.1, 3.2, 3.3, 3bis**

CRÍTICO: `test_motor_primer_tramo_parcial_fecha_desde_mes_siguiente` DEBE FALLAR sobre el
código SIN corregir. La falla confirma la REGRESIÓN del fix 4.2:
`fecha_etiqueta_mes = fecha_inicio + relativedelta(months=mes - 1)` etiqueta el mes 1 como
el T0 (jul-2026, sin P&G proyectado real) y corre toda la serie un mes hacia atrás,
perdiendo dic-2026 (hoy emite jul..nov en vez de ago..dic).

Causa raíz (del diseño, Fase 2 — Defecto 3): la regla "el cierre de un año es el T0 del
siguiente" (months=mes-1) es correcta SOLO para tramos que arrancan el 1-ene (día == 1).
El primer tramo arranca en la fecha base real (p.ej. 31-jul-2026) y es PARCIAL: su T0 es
esa misma fecha, no el cierre de un año anterior, por lo que su primer mes proyectado debe
etiquetarse por VALORACIÓN (months=mes → ago-2026).

Fix esperado (en `scripts/motor_proyeccion.py`):
  - Si `fecha_inicio.day == 1` → etiqueta = fecha_inicio + relativedelta(months=mes - 1)
    (ene..dic; preserva la Fase 1 para tramos i>0).
  - Si `fecha_inicio.day != 1` → etiqueta = fecha_mes = fecha_inicio +
    relativedelta(months=mes) (mes de valoración; primer mes proyectado = mes siguiente al
    T0, sin perder diciembre).

La valoración (`fecha_mes`) NO cambia en ningún caso → VM/VTIR/P&L/cupones idénticos; solo
cambia la ETIQUETA del primer tramo parcial.
"""

from __future__ import annotations

from datetime import date

import pytest

# Rutas importables (raíz y scripts/) las resuelve conftest.py.
import motor_proyeccion as mp  # noqa: E402


def _titulos_sinteticos_rf(ids):
    """
    Títulos TF sintéticos para el t0 del tramo (evita leer hist_* / DB real).

    Mismo patrón que `tests/test_property_rf_mensual_por_tramos.py`: un bono TF COP vivo
    más allá del horizonte del test, de modo que el motor lo revalúe mes a mes y emita un
    `ResultadoMes` por mes del horizonte.
    """
    out = []
    for i in ids:
        t = mp.Titulo(
            id_posicion=i, especie=f"TFIT{i}", emisor="NACION", isin=f"CO{i}", moneda="COP",
            fecha_emision=date(2020, 1, 1), fecha_vencimiento=date(2032, 1, 1),
            nominal=1000.0, tasa_cupon=7.0, spread_ipc=0.0, tipo_bono="TF",
            frecuencia="anual", tipo_tasa="EFECTIVO", base_anual=365, uso_tasa="I",
            clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
            asset_class="DP LP", tasa_compra=8.0, tasa_mercado=8.0,
            valor_mercado=1000.0, valor_tir=1000.0,
        )
        t.spread_mercado = 8.0
        out.append(t)
    return out


@pytest.fixture(autouse=True)
def _aislar_cargas_auxiliares(monkeypatch):
    """
    Aísla la carga auxiliar del motor para que solo revalúe (no toque la DB real que vive
    en Google Drive). Mismo patrón que `test_prop1_motor_no_pierde_enero_de_arranque`.
    """
    import compras_ventas_reservas as cvr

    monkeypatch.setattr(cvr, "cargar_mapa_reservas", lambda *a, **k: {})
    monkeypatch.setattr(cvr, "cargar_universo_compra", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_ramos_reserva_matematica", lambda *a, **k: None)
    monkeypatch.setattr(cvr, "cargar_parametros_reservas", lambda *a, **k: None)


def _etiquetas_motor(fecha_inicio, meses_horizonte):
    """Corre el motor real y devuelve las etiquetas de fecha 'YYYY-MM' de cada mes."""
    port = _titulos_sinteticos_rf(["A"])
    resultados = mp.proyectar_portafolio(
        portafolio=port,
        fecha_inicio=fecha_inicio,
        meses_horizonte=meses_horizonte,
        sendas={},
        mapa_reservas={},
    )
    return [f"{r.fecha.year:04d}-{r.fecha.month:02d}" for r in resultados]


# ==============================================================================
# Defecto 3 — primer tramo PARCIAL (fecha base real, día != 1): FALLA hoy
# ==============================================================================

def test_motor_primer_tramo_parcial_fecha_desde_mes_siguiente():
    """
    Primer tramo que arranca el 31-jul-2026 (día != 1) con sub_horizonte = 5.

    Esperado (comportamiento correcto): el primer mes proyectado se etiqueta con el mes de
    VALORACIÓN (ago-2026), de modo que la serie cubra ago..dic 2026 (5 meses), SIN incluir
    jul-2026 (T0, sin P&G proyectado real) y SIN perder dic-2026.

    EXPECTED OUTCOME (código SIN corregir): FALLA — con months=mes-1 el motor etiqueta
    jul..nov 2026 (corrido -1 mes), perdiendo diciembre.
    """
    fechas = _etiquetas_motor(date(2026, 7, 31), 5)

    esperado = ["2026-08", "2026-09", "2026-10", "2026-11", "2026-12"]
    assert fechas == esperado, (
        "regresión del fix 4.2: el primer tramo parcial se etiqueta corrido -1 mes "
        "(jul..nov) perdiendo diciembre; "
        f"esperado {esperado} obtenido {fechas}"
    )


# ==============================================================================
# No-regresión Fase 1 — tramo que arranca el 1-ene (día == 1): ene..dic
# ==============================================================================

def test_motor_tramo_1ene_sigue_etiquetando_ene_a_dic():
    """
    No-regresión de la Fase 1 (gemelo de `test_prop1_motor_no_pierde_enero_de_arranque`):
    un tramo que arranca el 1-ene-2027 y proyecta 12 meses debe etiquetar ene-2027..dic-2027
    (regla "el cierre de un año es el T0 del siguiente").

    Debe pasar ANTES y DESPUÉS del fix (el fix solo toca la rama día != 1).
    """
    fechas = _etiquetas_motor(date(2027, 1, 1), 12)

    esperado = [f"2027-{m:02d}" for m in range(1, 13)]
    assert fechas == esperado, (
        "regresión en el fechado de tramos 1-ene (ene..dic); "
        f"esperado {esperado} obtenido {fechas}"
    )
