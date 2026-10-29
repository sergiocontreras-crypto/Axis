# -*- coding: utf-8 -*-
"""
Tests de la discriminacion RF Local vs Internacional en la serie mensual contable
`scripts/rf_contable_mensual.py::construir_rf_mensual_contable` (Tarea 12 / Defecto 7 de la
spec proyeccion-tramos-rf-mensual-y-vm-internacional).

Objetivo (fix ADITIVO):
  - Property 7: para una mezcla de titulos locales (COP/UVR) e internacionales (USD),
    `pyg_mes_local + pyg_mes_intl == pyg_mes` (y analogas para ori y total) en CADA mes.
  - Preservacion: las columnas de TOTAL existentes (`pyg_mes`, `ori_mes`,
    `total_resultado_mes`, `cupones_mes`, `vm_total`, `vtir_total`, `n_titulos`) quedan
    IDENTICAS a las que produce la version previa (sin discriminacion). Se compara contra
    una re-implementacion de referencia que replica la logica previa, igual que el test de
    equivalencia byte a byte ya existente (`test_rf_contable_mensual_helper.py`).

Regla de clasificacion por moneda (identica a `tools/export_resumen_tramos._categoria`):
  `mon = (moneda or "").strip().upper()`;  `es_intl = mon not in ("", "COP", "UVR")`.

No usa la DB real: es puro (patron de dobles de `test_rf_contable_mensual_helper.py`).

**Validates: Requirements 7.1, 7.2, 7bis**
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rf_contable_mensual import construir_rf_mensual_contable  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Dobles de prueba ligeros (con moneda), analogos a test_rf_contable_mensual_helper
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class _TituloFake:
    id_posicion: str
    clasificacion: str
    fecha_vencimiento: date
    moneda: str = "COP"
    _snapshots_mensuales: dict = field(default_factory=dict)


@dataclass
class _ResMesFake:
    fecha: date
    vm_total: float
    vtir_total: float
    n_titulos: int


# ──────────────────────────────────────────────────────────────────────────────
# Referencia: logica PREVIA (sin discriminacion local/intl) para preservacion
# Copia literal de la version previa del helper (solo columnas de total).
# ──────────────────────────────────────────────────────────────────────────────
def _rf_mensual_contable_previo(res_rf, port, fecha_end):
    rf_mensual_contable = []
    titulos_vivos_fin = {t.id_posicion for t in port if t.fecha_vencimiento > fecha_end}

    for m_idx, r in enumerate(res_rf):
        mes_num = m_idx + 1
        pyg_mes_total = 0.0
        ori_mes_total = 0.0
        total_mes_total = 0.0
        cupones_mes_total = 0.0

        for t in port:
            if t.id_posicion not in titulos_vivos_fin:
                continue
            snaps = getattr(t, "_snapshots_mensuales", {})
            if mes_num not in snaps:
                continue
            snap_actual = snaps[mes_num]
            snap_anterior = snaps.get(mes_num - 1, None)
            if snap_anterior is None:
                continue

            vm_act = snap_actual["vm"]
            vtir_act = snap_actual["vtir"]
            vm_ant = snap_anterior["vm"]
            vtir_ant = snap_anterior["vtir"]
            cup_mes_t = snap_actual.get("cupones", 0.0)

            clasif_str = str(t.clasificacion).strip()
            if "Disp" in clasif_str or "Venta" in clasif_str:
                cat_c = "DV"
            elif "Venc" in clasif_str:
                cat_c = "VC"
            else:
                cat_c = "NEG"

            if cat_c == "NEG":
                total_t = (vm_act - vm_ant) + cup_mes_t
                pyg_t = total_t
                ori_t = 0.0
            elif cat_c == "DV":
                total_t = (vm_act - vm_ant) + cup_mes_t
                pyg_t = (vtir_act - vtir_ant) + cup_mes_t
                ori_t = total_t - pyg_t
            else:  # VC
                total_t = (vtir_act - vtir_ant) + cup_mes_t
                pyg_t = total_t
                ori_t = 0.0

            pyg_mes_total += pyg_t
            ori_mes_total += ori_t
            total_mes_total += total_t
            cupones_mes_total += cup_mes_t

        rf_mensual_contable.append(
            {
                "mes": mes_num,
                "fecha": r.fecha.strftime("%Y-%m"),
                "pyg_mes": pyg_mes_total,
                "ori_mes": ori_mes_total,
                "total_resultado_mes": total_mes_total,
                "cupones_mes": cupones_mes_total,
                "vm_total": r.vm_total,
                "vtir_total": r.vtir_total,
                "n_titulos": r.n_titulos,
            }
        )
    return rf_mensual_contable


# Columnas de TOTAL que NO deben cambiar (preservacion / equivalencia byte a byte).
_COLS_TOTAL = (
    "mes",
    "fecha",
    "pyg_mes",
    "ori_mes",
    "total_resultado_mes",
    "cupones_mes",
    "vm_total",
    "vtir_total",
    "n_titulos",
)


# ──────────────────────────────────────────────────────────────────────────────
# Test unitario: caso conocido con mezcla local (COP/UVR) e internacional (USD)
# ──────────────────────────────────────────────────────────────────────────────
def test_discriminacion_local_intl_caso_conocido():
    fecha_end = date(2026, 12, 31)
    port = [
        # LOCAL (COP), Negociable: total=(110-100)+1=11, pyg=11, ori=0
        _TituloFake(
            "COP_NEG",
            "Negociable",
            date(2030, 1, 1),
            "COP",
            {
                0: {"vm": 100.0, "vtir": 100.0, "cupones": 0.0},
                1: {"vm": 110.0, "vtir": 108.0, "cupones": 1.0},
            },
        ),
        # LOCAL (UVR), DV: total=(215-200)+2=17, pyg=(205-200)+2=7, ori=10
        _TituloFake(
            "UVR_DV",
            "Disponible para la Venta",
            date(2030, 1, 1),
            "UVR",
            {
                0: {"vm": 200.0, "vtir": 200.0, "cupones": 0.0},
                1: {"vm": 215.0, "vtir": 205.0, "cupones": 2.0},
            },
        ),
        # INTERNACIONAL (USD), Negociable: total=(330-300)+3=33, pyg=33, ori=0
        _TituloFake(
            "USD_NEG",
            "Negociable",
            date(2030, 1, 1),
            "USD",
            {
                0: {"vm": 300.0, "vtir": 300.0, "cupones": 0.0},
                1: {"vm": 330.0, "vtir": 320.0, "cupones": 3.0},
            },
        ),
    ]
    res_rf = [_ResMesFake(date(2026, 1, 31), 655.0, 633.0, 3)]

    filas = construir_rf_mensual_contable(res_rf, port, fecha_end)
    assert len(filas) == 1
    f = filas[0]

    # Totales existentes (sin cambio)
    assert f["total_resultado_mes"] == pytest.approx(11.0 + 17.0 + 33.0)  # 61
    assert f["pyg_mes"] == pytest.approx(11.0 + 7.0 + 33.0)  # 51
    assert f["ori_mes"] == pytest.approx(0.0 + 10.0 + 0.0)  # 10

    # Discriminacion local (COP_NEG + UVR_DV)
    assert f["total_resultado_mes_local"] == pytest.approx(11.0 + 17.0)  # 28
    assert f["pyg_mes_local"] == pytest.approx(11.0 + 7.0)  # 18
    assert f["ori_mes_local"] == pytest.approx(10.0)

    # Discriminacion internacional (USD_NEG)
    assert f["total_resultado_mes_intl"] == pytest.approx(33.0)
    assert f["pyg_mes_intl"] == pytest.approx(33.0)
    assert f["ori_mes_intl"] == pytest.approx(0.0)

    # vm por titulo del grupo (suma de snap_actual['vm'] de los vivos con snapshot)
    assert f["vm_total_local"] == pytest.approx(110.0 + 215.0)  # 325
    assert f["vm_total_intl"] == pytest.approx(330.0)


@pytest.mark.parametrize(
    "moneda, es_local",
    [
        ("", True),
        ("COP", True),
        ("UVR", True),
        ("cop", True),  # case-insensitive (strip + upper)
        ("  uvr  ", True),  # strip
        ("USD", False),
        ("EUR", False),
        ("usd", False),
    ],
)
def test_clasificacion_moneda_local_vs_intl(moneda, es_local):
    """La regla de moneda coincide con export_resumen_tramos._categoria."""
    fecha_end = date(2026, 12, 31)
    port = [
        _TituloFake(
            "T",
            "Negociable",
            date(2030, 1, 1),
            moneda,
            {
                0: {"vm": 100.0, "vtir": 100.0, "cupones": 0.0},
                1: {"vm": 110.0, "vtir": 108.0, "cupones": 0.0},
            },
        ),
    ]
    res_rf = [_ResMesFake(date(2026, 1, 31), 110.0, 108.0, 1)]
    f = construir_rf_mensual_contable(res_rf, port, fecha_end)[0]
    if es_local:
        assert f["pyg_mes_local"] == pytest.approx(10.0)
        assert f["pyg_mes_intl"] == pytest.approx(0.0)
    else:
        assert f["pyg_mes_local"] == pytest.approx(0.0)
        assert f["pyg_mes_intl"] == pytest.approx(10.0)


# ──────────────────────────────────────────────────────────────────────────────
# Property 7 + Preservacion (Hypothesis)
# ──────────────────────────────────────────────────────────────────────────────
_CLASIFS = st.sampled_from(
    ["Negociable", "Disponible para la Venta", "Al Vencimiento", "Disp", "Venc"]
)
_MONEDAS = st.sampled_from(["", "COP", "UVR", "USD", "EUR", "cop", "usd", "  UVR  "])
_finitos = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)
_snap_o_none = st.one_of(
    st.none(),
    st.fixed_dictionaries({"vm": _finitos, "vtir": _finitos, "cupones": _finitos}),
)


@st.composite
def _portafolio_y_snapshots(draw):
    n_meses = draw(st.integers(min_value=1, max_value=5))
    n_titulos = draw(st.integers(min_value=0, max_value=6))
    fecha_end = date(2026, 12, 31)
    port = []
    for i in range(n_titulos):
        fv = date(2030, 1, 1) if draw(st.booleans()) else date(2026, 3, 31)
        snaps_lista = draw(
            st.lists(_snap_o_none, min_size=n_meses + 1, max_size=n_meses + 1)
        )
        snaps = {m: s for m, s in enumerate(snaps_lista) if s is not None}
        port.append(
            _TituloFake(f"T{i}", draw(_CLASIFS), fv, draw(_MONEDAS), snaps)
        )

    totales = draw(
        st.lists(
            st.tuples(_finitos, _finitos, st.integers(min_value=0, max_value=n_titulos)),
            min_size=n_meses,
            max_size=n_meses,
        )
    )
    res_rf = [_ResMesFake(date(2026, 1, 1), vm, vtir, n) for (vm, vtir, n) in totales]
    return res_rf, port, fecha_end


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_portafolio_y_snapshots())
def test_prop7_local_mas_intl_igual_total(caso):
    """Property 7: local + intl == total para pyg, ori y total_resultado en cada mes."""
    res_rf, port, fecha_end = caso
    filas = construir_rf_mensual_contable(res_rf, port, fecha_end)
    for f in filas:
        assert f["pyg_mes_local"] + f["pyg_mes_intl"] == pytest.approx(
            f["pyg_mes"], abs=1e-6, rel=1e-9
        )
        assert f["ori_mes_local"] + f["ori_mes_intl"] == pytest.approx(
            f["ori_mes"], abs=1e-6, rel=1e-9
        )
        assert f["total_resultado_mes_local"] + f["total_resultado_mes_intl"] == pytest.approx(
            f["total_resultado_mes"], abs=1e-6, rel=1e-9
        )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_portafolio_y_snapshots())
def test_prop7_preserva_columnas_de_total(caso):
    """Preservacion (7bis): las columnas de total existentes quedan IDENTICAS a la
    version previa (equivalencia byte a byte); las nuevas son aditivas."""
    res_rf, port, fecha_end = caso
    esperado = _rf_mensual_contable_previo(res_rf, port, fecha_end)
    obtenido = construir_rf_mensual_contable(res_rf, port, fecha_end)
    assert len(obtenido) == len(esperado)
    for fila_obt, fila_esp in zip(obtenido, esperado):
        # Las columnas de total deben existir e igualar la referencia previa.
        proyeccion = {k: fila_obt[k] for k in _COLS_TOTAL}
        assert proyeccion == fila_esp
