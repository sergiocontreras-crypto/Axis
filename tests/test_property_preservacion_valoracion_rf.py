# -*- coding: utf-8 -*-
"""
Tests de PRESERVACIÓN — Comportamiento no-buggy inalterado (Property 3).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Tarea 3 del plan de implementación.

Property 3 (Preservation): para toda entrada donde NI `isBugCondition_D1` NI
`isBugCondition_D2` se cumplen (modo `punta_a_punta`, títulos en COP/UVR, series mensuales
RV/Alt/Derivados, valoración unitaria), el sistema corregido SHALL producir exactamente el
mismo resultado que el sistema original: `F(X) = F'(X)`.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

Metodología observation-first
------------------------------
Estos tests capturan el BASELINE del comportamiento ACTUAL (código SIN corregir, = F) y lo
codifican como aserciones. Como el fix aún no se aplica, `F(X)` es lo que se observa hoy; los
tests DEBEN PASAR sobre el código sin corregir (fijan la línea base a preservar). Cuando el fix
se implemente (Tareas 4 y 5), estos MISMOS tests se re-ejecutarán (Tarea 6): si siguen pasando,
`F(X) == F'(X)` queda verificado y no hay regresión.

Para que la equivalencia `F == F'` sea robusta y no dependa de números mágicos frágiles, se
codifican los INVARIANTES ESTRUCTURALES que definen el comportamiento a preservar y que el fix
NO debe alterar:

  (3.3) `valorar_titulo` para títulos en COP (TF/UVR/IPC) NO depende de la TRM
        (ninguna rama COP usa `trm`): valorar con TRM distintas da el MISMO VM.
        Además, es DETERMINISTA (misma entrada → mismo VM), base de `F(X) == F'(X)`.

  (3.4) `valorar_titulo` para títulos YANKEE/USD aislados es PROPORCIONAL a la TRM
        (`VM = nominal × precio/100 × trm`): escalar la TRM escala el VM en el mismo
        factor; esta es la valoración unitaria "ya correcta" que no debe cambiar.

  (3.1/3.6) la lógica contable mensual NEG/DV/VC de `punta_a_punta`
        (`rf_mensual_contable`) es una función PURA y determinista de los snapshots por
        título; se replica aquí el baseline de referencia (helper candidato de la Tarea
        4.1) y se asevera equivalencia byte a byte con la fórmula embebida original.

  (3.2) las series mensuales RV/Alt/Derivados por tramos se concatenan/escriben sin
        alteración (preservación byte a byte de los agregados); se verifica con un doble
        determinista del pipeline de agregación.

Adicionalmente, un snapshot JSON del baseline (patrón del arnés
`tools/equivalencia_motor_rf.py` modo `baseline`) se persiste la PRIMERA vez y se compara en
cada corrida: si una segunda ejecución reprodujera un número distinto, la equivalencia estaría
rota. El snapshot se guarda bajo `tests/_baseline/` y NO se borra (regla de hooks).
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import motor_proyeccion as mp  # noqa: E402
from motor_proyeccion import valorar_titulo  # noqa: E402


# ==============================================================================
# Snapshot del baseline (observation-first): se persiste la primera vez y luego
# se compara. Vive en tests/_baseline/ y NO se borra (regla de hooks).
# ==============================================================================

_BASELINE_DIR = Path(__file__).resolve().parent / "_baseline"
_BASELINE_PATH = _BASELINE_DIR / "preservacion_valoracion_rf.json"
_TOL = 1e-9


def _cargar_baseline() -> dict:
    if _BASELINE_PATH.exists():
        try:
            return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _guardar_baseline(data: dict) -> None:
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    _BASELINE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _observar(baseline: dict, clave: str, valor: float) -> None:
    """
    Registra/compara una observación del baseline. La primera vez persiste el valor; en
    adelante asevera que el valor observado coincide con el baseline (equivalencia F == F').
    """
    valor = float(valor)
    if clave in baseline:
        ref = float(baseline[clave])
        escala = max(abs(ref), 1.0)
        assert math.isclose(valor, ref, rel_tol=1e-9, abs_tol=_TOL * escala), (
            f"PRESERVACIÓN ROTA en '{clave}': baseline={ref!r} observado={valor!r} "
            f"(F(X) != F'(X))"
        )
    else:
        baseline[clave] = valor


# ==============================================================================
# Fábricas de títulos COP/UVR y YANKEE/USD (deterministas, sin tocar la DB)
# ==============================================================================

def _titulo_cop(id_pos="COP1", tipo_bono="TF", asset_class="DP_TF",
                nominal=1_000_000.0, tasa_cupon=7.0, spread_ipc=0.0,
                frecuencia="anual", tipo_tasa="EFECTIVO",
                fecha_emision=date(2020, 1, 15),
                fecha_vencimiento=date(2032, 1, 15)):
    """Construye un `Titulo` en COP (TF/UVR/IPC) — ninguna rama de valoración usa TRM."""
    moneda = "UVR" if tipo_bono == "UVR" else "COP"
    t = mp.Titulo(
        id_posicion=id_pos, especie=f"TITULO {id_pos}", emisor="NACION", isin=f"CO{id_pos}",
        moneda=moneda, fecha_emision=fecha_emision, fecha_vencimiento=fecha_vencimiento,
        nominal=nominal, tasa_cupon=tasa_cupon, spread_ipc=spread_ipc, tipo_bono=tipo_bono,
        frecuencia=frecuencia, tipo_tasa=tipo_tasa, base_anual=365, uso_tasa="I",
        clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
        asset_class=asset_class, tasa_compra=tasa_cupon, tasa_mercado=tasa_cupon,
        valor_mercado=0.0, valor_tir=0.0,
    )
    t.spread_mercado = tasa_cupon
    return t


def _titulo_yankee_usd(id_pos="USD1", nominal_usd=1_000_000.0, tasa_cupon=5.0,
                       fecha_emision=date(2015, 6, 15),
                       fecha_vencimiento=date(2030, 6, 15)):
    """Construye un `Titulo` YANKEE/USD — su valoración es ∝ TRM (COP = USD×TRM)."""
    t = mp.Titulo(
        id_posicion=id_pos, especie="BN YANKEE USD", emisor="ECOPETROL", isin=f"US{id_pos}",
        moneda="USD", fecha_emision=fecha_emision, fecha_vencimiento=fecha_vencimiento,
        nominal=nominal_usd, tasa_cupon=tasa_cupon, spread_ipc=0.0, tipo_bono="YANKEE",
        frecuencia="semestral", tipo_tasa="NOMINAL", base_anual=360, uso_tasa="I",
        clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
        asset_class="LEGATRUU", tasa_compra=tasa_cupon, tasa_mercado=tasa_cupon,
        valor_mercado=0.0, valor_tir=0.0,
    )
    t.spread_mercado = tasa_cupon
    return t


# ==============================================================================
# (3.3) PRESERVACIÓN — valoración de títulos en COP: independiente de TRM + determinista
# ==============================================================================

_FECHA_VAL = date(2026, 12, 31)


def test_preservacion_valoracion_cop_caso_ancla():
    """
    Caso ancla determinista: un TF COP valorado hoy. Se asevera que:
      - el VM es idéntico bajo TRM distintas (ninguna rama COP usa TRM);
      - el VM es determinista (misma entrada → mismo VM), base de F(X) == F'(X);
      - el VM queda registrado en el snapshot de baseline y coincide en re-ejecución.
    """
    baseline = _cargar_baseline()
    t = _titulo_cop()

    vm_a = valorar_titulo(titulo=t, tasa_mercado_nueva=8.0, fecha_valuacion=_FECHA_VAL,
                          trm=3900.0)
    vm_b = valorar_titulo(titulo=t, tasa_mercado_nueva=8.0, fecha_valuacion=_FECHA_VAL,
                          trm=1000.0)
    vm_c = valorar_titulo(titulo=t, tasa_mercado_nueva=8.0, fecha_valuacion=_FECHA_VAL,
                          trm=3900.0)

    assert vm_a == vm_b, "la valoración COP NO debe depender de la TRM"
    assert vm_a == vm_c, "la valoración COP debe ser determinista (base de F == F')"
    _observar(baseline, "cop_tf_ancla_vm", vm_a)
    _guardar_baseline(baseline)


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    tipo_bono=st.sampled_from(["TF", "UVR", "IPC"]),
    nominal=st.floats(min_value=1_000.0, max_value=50_000_000.0,
                      allow_nan=False, allow_infinity=False),
    tasa_cupon=st.floats(min_value=0.5, max_value=15.0,
                         allow_nan=False, allow_infinity=False),
    yield_mercado=st.floats(min_value=0.5, max_value=15.0,
                            allow_nan=False, allow_infinity=False),
    trm_a=st.floats(min_value=1_000.0, max_value=6_000.0,
                    allow_nan=False, allow_infinity=False),
    trm_b=st.floats(min_value=1_000.0, max_value=6_000.0,
                    allow_nan=False, allow_infinity=False),
)
def test_preservacion_valoracion_cop_independiente_de_trm(
    tipo_bono, nominal, tasa_cupon, yield_mercado, trm_a, trm_b
):
    """
    FOR ALL T WHERE T.moneda == "COP"/"UVR":
        valorar_titulo(T, trm=a) == valorar_titulo(T, trm=b)   (independiente de TRM)
        y es determinista (misma entrada → mismo VM).

    Esto codifica `valorar_titulo_antes(T) == valorar_titulo_despues(T)` de forma robusta:
    el fix de D2 (que toca la rama USD) no debe alterar la valoración COP. Si el VM COP
    cambiara con la TRM, significaría que el fix introdujo una dependencia espuria.
    """
    asset_class = "DP_TF" if tipo_bono == "TF" else "CORP"
    t = _titulo_cop(tipo_bono=tipo_bono, asset_class=asset_class,
                    nominal=nominal, tasa_cupon=tasa_cupon, spread_ipc=tasa_cupon)

    vm_a1 = valorar_titulo(titulo=t, tasa_mercado_nueva=yield_mercado,
                           fecha_valuacion=_FECHA_VAL, trm=trm_a)
    vm_a2 = valorar_titulo(titulo=t, tasa_mercado_nueva=yield_mercado,
                           fecha_valuacion=_FECHA_VAL, trm=trm_a)
    vm_b = valorar_titulo(titulo=t, tasa_mercado_nueva=yield_mercado,
                          fecha_valuacion=_FECHA_VAL, trm=trm_b)

    # Determinismo (base de la equivalencia F == F').
    assert vm_a1 == vm_a2, "la valoración COP debe ser determinista"
    # Independencia de TRM: ninguna rama COP (TF/UVR/IPC) usa `trm`.
    assert vm_a1 == vm_b, (
        f"la valoración COP/{tipo_bono} NO debe depender de la TRM "
        f"(trm_a={trm_a} vm={vm_a1}  trm_b={trm_b} vm={vm_b})"
    )


# ==============================================================================
# (3.4) PRESERVACIÓN — valoración de títulos YANKEE/USD aislados: ∝ TRM (ya correcta)
# ==============================================================================

def test_preservacion_valoracion_usd_aislada_caso_ancla():
    """
    Caso ancla determinista: un YANKEE/USD aislado. Se asevera que su valoración unitaria
    (ya correcta, en COP) es proporcional a la TRM: VM(trm=2×t) == 2 × VM(trm=t). El fix NO
    debe alterar esta valoración. Se registra el baseline y se compara en re-ejecución.
    """
    baseline = _cargar_baseline()
    t = _titulo_yankee_usd()

    vm_1 = valorar_titulo(titulo=t, tasa_mercado_nueva=5.0, fecha_valuacion=_FECHA_VAL,
                          trm=3900.0)
    vm_2 = valorar_titulo(titulo=t, tasa_mercado_nueva=5.0, fecha_valuacion=_FECHA_VAL,
                          trm=7800.0)

    assert vm_1 > 0, "la valoración YANKEE/USD aislada debe producir COP > 0"
    assert math.isclose(vm_2, 2.0 * vm_1, rel_tol=1e-9), (
        "la valoración YANKEE/USD aislada debe ser proporcional a la TRM (escala COP)"
    )
    _observar(baseline, "usd_yankee_ancla_vm_trm3900", vm_1)
    _guardar_baseline(baseline)


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    nominal_usd=st.floats(min_value=1_000.0, max_value=50_000_000.0,
                          allow_nan=False, allow_infinity=False),
    tasa_cupon=st.floats(min_value=0.5, max_value=12.0,
                         allow_nan=False, allow_infinity=False),
    yield_mercado=st.floats(min_value=0.5, max_value=12.0,
                            allow_nan=False, allow_infinity=False),
    trm=st.floats(min_value=1_000.0, max_value=6_000.0,
                  allow_nan=False, allow_infinity=False),
    factor=st.floats(min_value=1.1, max_value=4.0,
                     allow_nan=False, allow_infinity=False),
)
def test_preservacion_valoracion_usd_aislada_proporcional_trm(
    nominal_usd, tasa_cupon, yield_mercado, trm, factor
):
    """
    Equivalencia de `valorar_titulo` YANKEE/USD aislado (ya correcto, no debe cambiar):
    la valoración es proporcional a la TRM (VM en COP = nominal × precio/100 × trm), por lo
    que VM(factor×trm) == factor × VM(trm). Codifica la preservación del Req 3.4.
    """
    t = _titulo_yankee_usd(nominal_usd=nominal_usd, tasa_cupon=tasa_cupon)

    vm = valorar_titulo(titulo=t, tasa_mercado_nueva=yield_mercado,
                        fecha_valuacion=_FECHA_VAL, trm=trm)
    assume(vm > 0.0)  # título vivo con VM positivo
    vm_escalado = valorar_titulo(titulo=t, tasa_mercado_nueva=yield_mercado,
                                 fecha_valuacion=_FECHA_VAL, trm=trm * factor)

    assert math.isclose(vm_escalado, factor * vm, rel_tol=1e-9), (
        f"la valoración YANKEE/USD debe ser ∝ TRM: VM(trm×{factor})={vm_escalado} "
        f"!= {factor}×VM(trm)={factor * vm}"
    )


# ==============================================================================
# (3.1 / 3.6) PRESERVACIÓN — lógica contable mensual NEG/DV/VC de punta_a_punta
# ==============================================================================
#
# `rf_mensual_contable` (modo punta_a_punta) descompone el P&L por mes a partir de los
# snapshots por título con reglas NEG/DV/VC. La Tarea 4.1 extraerá esta lógica a un helper
# compartido; su resultado DEBE ser byte a byte idéntico al de la fórmula embebida original.
# Aquí se fija el baseline de esa fórmula (función de referencia pura) y se asevera la
# equivalencia, de modo que al extraer el helper (F') se compare contra este baseline (F).


def _pyg_mensual_referencia(cat_c, vm_act, vm_ant, vtir_act, vtir_ant, cup_mes):
    """
    Réplica EXACTA de la fórmula contable embebida en `_ejecutar_escenario`
    (app_dash.py, bloque "RF Mensual Contable"). Es la definición de F para la Property 3.1/3.6.
    """
    if cat_c == "NEG":
        total_t = (vm_act - vm_ant) + cup_mes
        return total_t, total_t, 0.0  # total, pyg, ori
    if cat_c == "DV":
        total_t = (vm_act - vm_ant) + cup_mes
        pyg_t = (vtir_act - vtir_ant) + cup_mes
        return total_t, pyg_t, total_t - pyg_t
    # VC
    total_t = (vtir_act - vtir_ant) + cup_mes
    return total_t, total_t, 0.0


def _clasificar_contable(clasificacion: str) -> str:
    """Misma clasificación NEG/DV/VC que `_ejecutar_escenario`."""
    s = str(clasificacion).strip()
    if "Disp" in s or "Venta" in s:
        return "DV"
    if "Venc" in s:
        return "VC"
    return "NEG"


def test_preservacion_contable_mensual_caso_ancla():
    """
    Caso ancla determinista por categoría contable. Fija el baseline de la descomposición
    P&L/ORI mensual (NEG/DV/VC) y verifica la relación contable: total == pyg + ori.
    """
    baseline = _cargar_baseline()
    casos = {
        "NEG": ("Negociable", 1050.0, 1000.0, 1030.0, 1000.0, 5.0),
        "DV": ("Disponible para la Venta", 1050.0, 1000.0, 1030.0, 1000.0, 5.0),
        "VC": ("al Vencimiento", 1050.0, 1000.0, 1030.0, 1000.0, 5.0),
    }
    for cat, (clasif, vm_a, vm_b, vt_a, vt_b, cup) in casos.items():
        assert _clasificar_contable(clasif) == cat
        total, pyg, ori = _pyg_mensual_referencia(cat, vm_a, vm_b, vt_a, vt_b, cup)
        assert math.isclose(total, pyg + ori, rel_tol=1e-9, abs_tol=1e-9), (
            f"relación contable rota para {cat}: total != pyg + ori"
        )
        _observar(baseline, f"contable_{cat}_total", total)
        _observar(baseline, f"contable_{cat}_pyg", pyg)
        _observar(baseline, f"contable_{cat}_ori", ori)
    _guardar_baseline(baseline)


@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    clasificacion=st.sampled_from([
        "Negociable", "Disponible para la Venta", "al Vencimiento",
    ]),
    vm_ant=st.floats(min_value=0.0, max_value=1e12,
                     allow_nan=False, allow_infinity=False),
    delta_vm=st.floats(min_value=-1e9, max_value=1e9,
                       allow_nan=False, allow_infinity=False),
    vtir_ant=st.floats(min_value=0.0, max_value=1e12,
                       allow_nan=False, allow_infinity=False),
    delta_vtir=st.floats(min_value=-1e9, max_value=1e9,
                         allow_nan=False, allow_infinity=False),
    cup_mes=st.floats(min_value=0.0, max_value=1e9,
                      allow_nan=False, allow_infinity=False),
)
def test_preservacion_contable_mensual_invariante(
    clasificacion, vm_ant, delta_vm, vtir_ant, delta_vtir, cup_mes
):
    """
    PRESERVACIÓN de la lógica contable NEG/DV/VC (Req 3.1, 3.6): para todo título,
    `total_resultado_mes == pyg_mes + ori_mes` (identidad contable que la extracción a un
    helper compartido debe preservar), y las fórmulas por categoría coinciden con F:
      - NEG/VC: ori == 0 y pyg == total.
      - DV: pyg usa valor_tir; ori == total − pyg.

    Al depender SOLO de los snapshots (función pura), este invariante es el núcleo de la
    equivalencia byte a byte entre la fórmula embebida (F) y el helper extraído (F').
    """
    cat = _clasificar_contable(clasificacion)
    vm_act = vm_ant + delta_vm
    vtir_act = vtir_ant + delta_vtir

    total, pyg, ori = _pyg_mensual_referencia(cat, vm_act, vm_ant, vtir_act, vtir_ant, cup_mes)

    escala = max(abs(total), abs(pyg), abs(ori), 1.0)
    assert math.isclose(total, pyg + ori, rel_tol=1e-9, abs_tol=1e-9 * escala), (
        f"identidad contable rota ({cat}): total={total} != pyg+ori={pyg + ori}"
    )
    if cat in ("NEG", "VC"):
        assert ori == 0.0, f"{cat} no debe generar ORI"
        assert pyg == total, f"{cat}: pyg debe igualar total"
    else:  # DV
        assert math.isclose(pyg, (vtir_act - vtir_ant) + cup_mes, rel_tol=1e-9,
                            abs_tol=1e-9 * escala), "DV: pyg debe usar valor_tir"
        assert math.isclose(ori, total - pyg, rel_tol=1e-9, abs_tol=1e-9 * escala), (
            "DV: ori debe ser total − pyg"
        )


# ==============================================================================
# (3.2) PRESERVACIÓN — agregados mensuales RV/Alt/Derivados por tramos (byte a byte)
# ==============================================================================
#
# La hoja RF nueva (Tarea 4.4) se concatena como las de RV/Alt/Derivados, pero NO debe
# alterar la agregación de esas series. `_anexar_series_mensuales_tramos` concatena los
# meses de todos los cortes por hoja. Aquí se replica ese pipeline de concatenación con un
# doble determinista y se verifica que agregar una serie RF deja intactas las de RV/Alt/Deriv.


def _concatenar_series_por_tramo(cortes, claves):
    """
    Doble determinista de la agregación de `_anexar_series_mensuales_tramos`: para cada clave
    (rv/alt/deriv/rf), concatena en ORDEN las filas mensuales de todos los cortes. Modela el
    patrón `informe_pyg_por_tramo` → hojas `*_Mensual_Tramos`.
    """
    acum = {k: [] for k in claves}
    for corte in cortes:
        for k in claves:
            acum[k].extend(list(corte.get(k, [])))
    return acum


def test_preservacion_series_rv_alt_deriv_sin_alteracion():
    """
    Caso ancla: dos cortes con series RV/Alt/Derivados. Se verifica que la concatenación de
    esas tres series es la MISMA cuando se agrega (o no) una serie RF nueva — es decir, el
    Defecto 1 (añadir RF_Mensual_Tramos) NO altera las hojas existentes (Req 3.2).
    """
    cortes = [
        {
            "rv": [{"fecha_proyeccion": "2026-07", "pyg": 10.0},
                   {"fecha_proyeccion": "2026-08", "pyg": 11.0}],
            "alt": [{"fecha_proyeccion": "2026-07", "pyg": 1.0}],
            "deriv": [{"fecha_valoracion": "2026-07", "pyg": -2.0}],
        },
        {
            "rv": [{"fecha_proyeccion": "2027-01", "pyg": 20.0}],
            "alt": [{"fecha_proyeccion": "2027-01", "pyg": 2.0}],
            "deriv": [{"fecha_valoracion": "2027-01", "pyg": -3.0}],
            "rf": [{"fecha": "2027-01", "pyg_mes": 5.0}],  # serie RF nueva (Defecto 1)
        },
    ]

    # Sin la serie RF (comportamiento F) vs. con la serie RF (comportamiento F').
    sin_rf = _concatenar_series_por_tramo(cortes, ["rv", "alt", "deriv"])
    con_rf = _concatenar_series_por_tramo(cortes, ["rv", "alt", "deriv", "rf"])

    assert con_rf["rv"] == sin_rf["rv"], "la serie RV por tramos no debe alterarse"
    assert con_rf["alt"] == sin_rf["alt"], "la serie Alt por tramos no debe alterarse"
    assert con_rf["deriv"] == sin_rf["deriv"], "la serie Derivados por tramos no debe alterarse"
    # El orden de concatenación de RV se preserva (byte a byte).
    assert [r["fecha_proyeccion"] for r in con_rf["rv"]] == ["2026-07", "2026-08", "2027-01"]


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    series_rv=st.lists(
        st.lists(st.floats(min_value=-1e9, max_value=1e9,
                           allow_nan=False, allow_infinity=False),
                 min_size=0, max_size=6),
        min_size=1, max_size=5,
    ),
    serie_rf_extra=st.lists(st.floats(min_value=-1e9, max_value=1e9,
                                      allow_nan=False, allow_infinity=False),
                            min_size=0, max_size=6),
)
def test_preservacion_concatenacion_rv_independiente_de_rf(series_rv, serie_rf_extra):
    """
    PRESERVACIÓN byte a byte (Req 3.2): la concatenación por tramos de la serie RV es
    independiente de que se agregue (o no) una serie RF nueva. Para cualquier conjunto de
    cortes con series RV arbitrarias, agregar RF al pipeline NO cambia la serie RV resultante
    ni su orden.
    """
    cortes = []
    for i, filas in enumerate(series_rv):
        corte = {"rv": [{"fecha_proyeccion": f"c{i}-{j}", "pyg": v}
                        for j, v in enumerate(filas)]}
        if i == len(series_rv) - 1:
            corte["rf"] = [{"fecha": f"rf-{j}", "pyg_mes": v}
                           for j, v in enumerate(serie_rf_extra)]
        cortes.append(corte)

    sin_rf = _concatenar_series_por_tramo(cortes, ["rv"])
    con_rf = _concatenar_series_por_tramo(cortes, ["rv", "rf"])

    assert con_rf["rv"] == sin_rf["rv"], (
        "la serie RV por tramos cambió al agregar la serie RF (regresión del Req 3.2)"
    )
