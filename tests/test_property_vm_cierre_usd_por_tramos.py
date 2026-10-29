# -*- coding: utf-8 -*-
"""
Test exploratorio de condición de bug — Defecto 2 (escala COP del VM de cierre USD).

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Tarea 2 del plan de implementación.

Property 2 (Bug Condition → Expected Behavior): el VM de cierre de los títulos de RF
internacional en USD (p.ej. YANKEE) debe quedar en el snapshot oficial
`portafolio_clasificado_proy` en escala COP (×TRM), consistente con el VM inicial.

**Validates: Requirements 1.6, 1.7, 1.8, 1.9**

HALLAZGO (Tarea 2 — exploración de condición de bug): el test se escribió para FALLAR sobre
el código sin corregir (confirmar que el VM de cierre USD quedaba ~TRM veces menor). Al
ejecutarlo, PASÓ: el Defecto 2 YA NO se reproduce en el código/datos actuales. La
investigación de causa raíz (con `portafolio.db` real, solo lectura) mostró que el snapshot
oficial `portafolio_clasificado_proy` del id 817856 (corte 2026-12-31) ya registra el VM de
cierre en escala COP: `valor_mercado = 1.438984e+10` (14.389.840.000 COP), consistente con el
VM base `1.265763e+10` (12.657.630.000 COP). El "14.389.842" documentado en
`docs/deuda-tecnica.md` es ESE MISMO valor dividido ~1000 (el defecto fue corregido o el
snapshot se regeneró con el fix). Por decisión del usuario, este test se CONSERVA como test de
REGRESIÓN: garantiza que el VM de cierre USD siga en escala COP (≈ nominal × precio/100 × trm)
y que la discrepancia ×TRM no reaparezca.

Evidencia de la investigación (scripts conservados):
  - `tests/_diag_d2_real_817856.py`   — inspección del caso real (solo lectura).
  - `tests/_diag_d2_vm_usd_e2e.py`     — orquestador real end-to-end (portafolio sintético).
  - `tests/_diag_d2_dump_snapshot.py`  — volcado del snapshot persistido.

El test codifica el comportamiento ESPERADO (escala COP); al pasar, confirma que se cumple.

Condición de bug (del diseño):
  isBugCondition_D2(X) es verdadero cuando
    X.moneda = "USD"  Y  X.tipo IN {YANKEE, EstructuraUSD}  Y  X.es_cierre_de_tramo
    Y el VM de cierre en el snapshot NO está en escala COP (falta ×TRM).

Aserciones (Property 2 del diseño), para todo título USD YANKEE al cierre de un tramo:
  - vm_cierre_snapshot ≈ nominal * precio_sucio/100 * trm   (escala COP).
  - Para el caso documentado (id 817856 "BN ECOPETROL USD"): VM de cierre esperado
    ≈ 12.657.628.506 COP (= VM×TRM), NO 14.389.842 (escala USD).

GOAL (del plan): surgir contraejemplos que demuestren la pérdida de escala e INSTRUMENTAR
el punto exacto donde se pierde (rama del bucle mensual vs. armado de `df_proy` vs.
encadenamiento `_titulos_a_dataframe_base`) para confirmar/refutar la hipótesis de causa
raíz.

Enfoque PBT acotado: por ser un bug determinista con caso documentado, se ancla la
propiedad al caso id 817856 (corte 2026-12-31, YANKEE, Negociable) para asegurar
reproducibilidad; luego se generaliza con Hypothesis a títulos USD (nominal, precio, TRM).

Metodología de instrumentación de causa raíz
---------------------------------------------
La valoración unitaria `valorar_titulo` (rama YANKEE) devuelve COP
(`nominal × precio_sucio/100 × trm`) — está confirmado por lectura de código. El motor deja
`titulo.valor_mercado` en COP. Por tanto, para localizar la pérdida de escala se recorre el
MISMO pipeline de snapshot que usa el orquestador por tramos:

    _construir_df_proy_rf(df_base, port, fecha_corte)   # mapea t.valor_mercado → df_proy
        → _enriquecer_snapshot_tramo(df_proy, port, fecha_corte)
        → _finalizar_df_proy(df_proy, fecha_corte)       # reclasifica (preserva VM)

y se compara, en cada etapa, el VM del snapshot contra el COP esperado. La etapa donde el
VM cae ~TRM veces es el punto de pérdida de escala. El test registra ese diagnóstico en el
mensaje de aserción para documentar el contraejemplo.
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

# El import de app_dash es pesado; si no está disponible en el entorno se salta con gracia
# (mismo patrón que la suite multiactivo / Defecto 1) sin romper el resto de la suite.
app_dash = pytest.importorskip("app_dash")

import motor_proyeccion as mp  # noqa: E402
from motor_proyeccion import valorar_titulo  # noqa: E402

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


# ==============================================================================
# CASO DOCUMENTADO (id 817856 "BN ECOPETROL USD")
# ==============================================================================
#
# Parámetros reproducibles del caso documentado. Los valores exactos de la posición real no
# son necesarios para evidenciar el bug (que es una pérdida de escala ×TRM independiente del
# precio); se usa un nominal/precio/TRM representativos tal que la escala COP sea del orden
# de ~12,66 mil millones (consistente con el VM base documentado) y la escala USD (sin ×TRM)
# del orden de ~14,4 millones. La ASERCIÓN es relativa (COP ≈ USD×TRM), no sobre el número
# absoluto, de modo que el test es robusto frente a los parámetros exactos de valoración.
_ID_DOC = "817856"
_ESPECIE_DOC = "BN ECOPETROL USD"
_TRM_DOC = 3900.0                 # TRM representativa COP/USD
_NOMINAL_USD_DOC = 3_600_000.0    # nominal en USD
_CORTE_DOC = date(2026, 12, 31)   # cierre del tramo documentado


def _titulo_yankee_usd(id_pos, especie, nominal_usd, tasa_cupon, trm,
                       fecha_emision=date(2015, 6, 15),
                       fecha_vencimiento=date(2030, 6, 15),
                       spread_mercado=8.0):
    """
    Construye un `Titulo` YANKEE/USD coherente con `cargar_portafolio_proyeccion`
    (moneda USD → tipo_bono YANKEE) y lo deja con `valor_mercado` en COP, EXACTAMENTE como
    lo hace el motor real tras `proyectar_portafolio` (que asigna
    `titulo.valor_mercado = valorar_titulo(..., trm=_senda_trm[mes])`, en COP).
    """
    t = mp.Titulo(
        id_posicion=id_pos, especie=especie, emisor="ECOPETROL", isin=f"US{id_pos}",
        moneda="USD",
        fecha_emision=fecha_emision, fecha_vencimiento=fecha_vencimiento,
        nominal=nominal_usd, tasa_cupon=tasa_cupon, spread_ipc=0.0, tipo_bono="YANKEE",
        frecuencia="semestral", tipo_tasa="NOMINAL", base_anual=360, uso_tasa="I",
        clasificacion="Negociable", compania="C", ramo="R", portafolio="P",
        asset_class="LEGATRUU", tasa_compra=tasa_cupon, tasa_mercado=tasa_cupon,
        valor_mercado=0.0, valor_tir=0.0,
    )
    t.spread_mercado = spread_mercado
    # VM en COP, tal como lo deja el motor (valoración unitaria correcta = COP).
    vm_cop = valorar_titulo(
        titulo=t, tasa_mercado_nueva=t.spread_mercado,
        fecha_valuacion=_CORTE_DOC, trm=trm,
    )
    t.valor_mercado = vm_cop
    t.valor_tir = vm_cop
    t._spread_efectivo_cierre = t.spread_mercado
    return t


def _vm_cop_esperado(titulo, trm, fecha_corte):
    """VM de cierre ESPERADO en COP (escala consistente con el VM inicial): el valor que
    devuelve la valoración unitaria YANKEE = nominal × precio_sucio/100 × trm."""
    return valorar_titulo(
        titulo=titulo, tasa_mercado_nueva=titulo.spread_mercado,
        fecha_valuacion=fecha_corte, trm=trm,
    )


def _df_base_desde_titulo(titulo):
    """
    DataFrame base (una fila) con el esquema de `portafolio_clasificado` que alimenta
    `_construir_df_proy_rf`. El VM del base NO importa para la aserción (el helper lo
    sobrescribe con el VM del motor `t.valor_mercado`), pero se incluye por completitud.
    """
    return pd.DataFrame({
        "id_posicion": [titulo.id_posicion],
        "especie": [titulo.especie],
        "emisor": [titulo.emisor],
        "isin": [titulo.isin],
        "moneda": ["USD"],
        "fecha_emision": [str(titulo.fecha_emision)],
        "fecha_vencimiento": [str(titulo.fecha_vencimiento)],
        "nominal": [titulo.nominal],
        "valor_mercado": [titulo.valor_mercado],
        "valor_tir": [titulo.valor_tir],
        "tasa_compra": [titulo.tasa_compra],
        "tir": [titulo.tasa_mercado],
        "asset_class_definitivo": [titulo.asset_class],
        "tasa_referencia": [""],
        "periodicidad": [titulo.frecuencia],
        "tasa_facial": [str(titulo.tasa_cupon)],
        "clasificacion_contable": [titulo.clasificacion],
        "clase_origen": ["TITULO"],
        "spread_mercado": [titulo.spread_mercado],
    })


def _vm_de(df, id_pos):
    """Extrae el `valor_mercado` (float) de la fila con `id_posicion == id_pos`."""
    if df is None or len(df) == 0 or "id_posicion" not in df.columns:
        return None
    sub = df[df["id_posicion"].astype(str) == str(id_pos)]
    if sub.empty or "valor_mercado" not in sub.columns:
        return None
    return float(pd.to_numeric(sub["valor_mercado"].iloc[0], errors="coerce"))


def _vm_snapshot_por_tramos(titulo, trm, fecha_corte):
    """
    Recorre el MISMO pipeline de snapshot que `_ejecutar_escenario_por_tramos` (líneas
    ~8128-8133 de app_dash) y devuelve (vm_df_proy_rf, vm_snapshot_final, etapas), donde
    `etapas` es un dict con el VM observado en cada etapa para localizar la pérdida de escala:

        'valorar_titulo' : COP esperado (valoración unitaria, referencia).
        't.valor_mercado': VM que el motor deja en el título (debe ser COP).
        'df_proy_rf'     : VM tras `_construir_df_proy_rf` (mapea t.valor_mercado).
        'enriquecido'    : VM tras `_enriquecer_snapshot_tramo`.
        'snapshot_final' : VM tras `_finalizar_df_proy` (lo que se persiste).
    """
    port = [titulo]
    df_base = _df_base_desde_titulo(titulo)

    etapas = {
        "valorar_titulo": _vm_cop_esperado(titulo, trm, fecha_corte),
        "t.valor_mercado": titulo.valor_mercado,
    }

    df_proy_rf, _resumen = app_dash._construir_df_proy_rf(df_base, port, fecha_corte)
    etapas["df_proy_rf"] = _vm_de(df_proy_rf, titulo.id_posicion)

    df_enr = app_dash._enriquecer_snapshot_tramo(df_proy_rf, port, fecha_corte)
    etapas["enriquecido"] = _vm_de(df_enr, titulo.id_posicion)

    df_final = app_dash._finalizar_df_proy(df_enr, fecha_corte)
    etapas["snapshot_final"] = _vm_de(df_final, titulo.id_posicion)

    return etapas["df_proy_rf"], etapas["snapshot_final"], etapas


def _diagnostico_escala(etapas, trm):
    """Construye un texto de diagnóstico que localiza la etapa donde se pierde la escala
    (el VM cae ~TRM veces respecto del COP esperado). Documenta el contraejemplo."""
    cop = etapas["valorar_titulo"]
    lineas = [f"TRM={trm:.4f}  COP_esperado(valorar_titulo)={cop:,.2f}"]
    etapa_perdida = None
    for nombre in ("t.valor_mercado", "df_proy_rf", "enriquecido", "snapshot_final"):
        vm = etapas.get(nombre)
        if vm is None:
            lineas.append(f"  {nombre:16s}: <ausente>")
            continue
        ratio = (cop / vm) if vm not in (0, None) else float("inf")
        marca = ""
        if cop > 0 and vm > 0 and math.isclose(ratio, trm, rel_tol=0.05):
            marca = "  <-- ESCALA USD (cayo xTRM): PUNTO DE PERDIDA"
            if etapa_perdida is None:
                etapa_perdida = nombre
        lineas.append(f"  {nombre:16s}: {vm:,.2f}  (COP/vm={ratio:,.2f}){marca}")
    if etapa_perdida:
        lineas.append(f"PUNTO DE PERDIDA DE ESCALA confirmado en etapa: '{etapa_perdida}'")
    else:
        lineas.append("No se localizo una caida xTRM en el pipeline de snapshot "
                      "(re-hipotetizar: la perdida podria estar en una rama del bucle "
                      "mensual del motor o en el encadenamiento _titulos_a_dataframe_base).")
    return "\n".join(lineas)


def _assert_property2_vm_cierre_cop(titulo, trm, fecha_corte):
    """
    Aserción de la Property 2: el VM de cierre en el snapshot debe estar en escala COP
    (≈ nominal × precio_sucio/100 × trm). Codifica el comportamiento ESPERADO.

    EXPECTED OUTCOME (código sin corregir): FALLA — el VM de cierre queda ~TRM veces menor.
    """
    _vm_df_rf, vm_snapshot, etapas = _vm_snapshot_por_tramos(titulo, trm, fecha_corte)
    cop_esperado = etapas["valorar_titulo"]

    assert cop_esperado is not None and cop_esperado > 0, (
        "la valoracion unitaria YANKEE no produjo un VM COP > 0 (precondicion del caso)"
    )
    assert vm_snapshot is not None, "el titulo USD no quedo en el snapshot del tramo"

    diag = _diagnostico_escala(etapas, trm)
    escala = max(abs(cop_esperado), 1.0)

    # (1) VM de cierre en el snapshot ≈ COP esperado (nominal × precio/100 × trm).
    assert math.isclose(vm_snapshot, cop_esperado, rel_tol=1e-3, abs_tol=1e-3 * escala), (
        "VM de cierre USD en el snapshot NO esta en escala COP (falta xTRM).\n"
        f"snapshot={vm_snapshot:,.2f}  esperado(COP)={cop_esperado:,.2f}\n{diag}"
    )

    # (2) En particular, NO debe estar en escala USD (≈ COP / trm).
    vm_usd = cop_esperado / trm
    assert not math.isclose(vm_snapshot, vm_usd, rel_tol=0.05), (
        "VM de cierre USD quedo en escala USD (~ VM_COP / TRM) en lugar de COP.\n"
        f"snapshot={vm_snapshot:,.2f}  escala_USD(COP/TRM)={vm_usd:,.2f}\n{diag}"
    )


# ==============================================================================
# Caso ANCLA (determinista): caso documentado id 817856 BN ECOPETROL USD
# ==============================================================================

def test_prop2_vm_cierre_usd_caso_documentado_817856():
    """
    Caso concreto y reproducible: título YANKEE/USD id 817856 "BN ECOPETROL USD" valorado
    al cierre del tramo 2026-12-31. El VM de cierre en el snapshot debe estar en escala COP
    (= nominal × precio/100 × trm), consistente con el VM inicial.

    EXPECTED OUTCOME (código sin corregir): FALLA — el VM de cierre queda ~TRM veces menor
    (escala USD ≈ VM_COP / trm), reproduciendo la discrepancia documentada
    (14.389.842 sin ×TRM vs. 12.657.628.506 COP con ×TRM).
    """
    titulo = _titulo_yankee_usd(
        _ID_DOC, _ESPECIE_DOC, _NOMINAL_USD_DOC, tasa_cupon=5.875, trm=_TRM_DOC,
    )
    _assert_property2_vm_cierre_cop(titulo, _TRM_DOC, _CORTE_DOC)


# ==============================================================================
# Generalización con Hypothesis: títulos USD (nominal, precio vía tasa_cupon, TRM)
# ==============================================================================

@settings(max_examples=50, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    nominal_usd=st.floats(min_value=1_000.0, max_value=50_000_000.0,
                          allow_nan=False, allow_infinity=False),
    tasa_cupon=st.floats(min_value=0.5, max_value=12.0,
                         allow_nan=False, allow_infinity=False),
    trm=st.floats(min_value=1_000.0, max_value=6_000.0,
                  allow_nan=False, allow_infinity=False),
)
def test_prop2_vm_cierre_usd_generalizado(nominal_usd, tasa_cupon, trm):
    """
    Generaliza la Property 2 a títulos YANKEE/USD arbitrarios: para todo título USD valorado
    al cierre de un tramo, el VM de cierre en el snapshot debe estar en escala COP
    (≈ nominal × precio/100 × trm), no en escala USD.

    EXPECTED OUTCOME (código sin corregir): FALLA.
    """
    titulo = _titulo_yankee_usd(
        "USDGEN", "BN YANKEE USD", nominal_usd, tasa_cupon=tasa_cupon, trm=trm,
    )
    _assert_property2_vm_cierre_cop(titulo, trm, _CORTE_DOC)
