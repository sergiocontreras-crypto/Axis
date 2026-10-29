# -*- coding: utf-8 -*-
"""
Tests unitarios y de propiedad del helper contable compartido
`scripts/rf_contable_mensual.py` (Tarea 4.1 de la spec
proyeccion-tramos-rf-mensual-y-vm-internacional).

Objetivo: garantizar que la EXTRACCION de la regla contable NEG/DV/VC a un helper puro
produce EXACTAMENTE los mismos valores que la logica embebida original en
`app_dash._ejecutar_escenario` (Req 2.7 helper compartido; Req 3.6 preservacion).

El test incluye una re-implementacion de referencia IDENTICA a la logica embebida original
(copiada literalmente del bloque "RF Mensual Contable") y asevera equivalencia byte a byte
contra el helper, sobre portafolios y snapshots sinteticos. No usa la DB real: es puro.

**Validates: Requirements 2.7, 3.6**
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rf_contable_mensual import (  # noqa: E402
    categoria_contable,
    construir_rf_mensual_contable,
    descomponer_contable,
)


# ──────────────────────────────────────────────────────────────────────────────
# Dobles de prueba ligeros (sin dependencias del motor)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class _TituloFake:
    id_posicion: str
    clasificacion: str
    fecha_vencimiento: date
    _snapshots_mensuales: dict = field(default_factory=dict)


@dataclass
class _ResMesFake:
    fecha: date
    vm_total: float
    vtir_total: float
    n_titulos: int


# ──────────────────────────────────────────────────────────────────────────────
# Referencia: logica contable embebida ORIGINAL (copia literal) para equivalencia
# ──────────────────────────────────────────────────────────────────────────────
def _rf_mensual_contable_original(res_rf, port, fecha_end):
    """Re-implementacion identica a la logica embebida original en app_dash."""
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


# ──────────────────────────────────────────────────────────────────────────────
# Tests unitarios de la clasificacion contable
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "clasif, esperado",
    [
        ("Disponible para la Venta", "DV"),
        ("Disp", "DV"),  # case-sensitive: 'Disp' presente
        ("Venta", "DV"),
        ("DISP", "NEG"),  # mayusculas NO coinciden con 'Disp' (regla original case-sensitive)
        ("Al Vencimiento", "VC"),
        ("Vencimiento", "VC"),
        ("Negociable", "NEG"),
        ("", "NEG"),
        (None, "NEG"),
        ("  Negociable  ", "NEG"),
    ],
)
def test_categoria_contable(clasif, esperado):
    assert categoria_contable(clasif) == esperado


# ──────────────────────────────────────────────────────────────────────────────
# Tests unitarios de la descomposicion contable por categoria
# ──────────────────────────────────────────────────────────────────────────────
def test_descomponer_neg_todo_a_pyg():
    total, pyg, ori = descomponer_contable("NEG", 110.0, 100.0, 108.0, 100.0, 2.0)
    assert total == pytest.approx(12.0)
    assert pyg == pytest.approx(12.0)
    assert ori == pytest.approx(0.0)


def test_descomponer_dv_total_mercado_pyg_causacion():
    total, pyg, ori = descomponer_contable("DV", 110.0, 100.0, 105.0, 100.0, 2.0)
    assert total == pytest.approx(12.0)  # (110-100)+2
    assert pyg == pytest.approx(7.0)  # (105-100)+2
    assert ori == pytest.approx(5.0)  # total - pyg


def test_descomponer_vc_todo_por_causacion():
    total, pyg, ori = descomponer_contable("VC", 110.0, 100.0, 103.0, 100.0, 2.0)
    assert total == pytest.approx(5.0)  # (103-100)+2
    assert pyg == pytest.approx(5.0)
    assert ori == pytest.approx(0.0)


def test_descomponer_total_igual_pyg_mas_ori():
    """Invariante contable: total == pyg + ori en todas las categorias."""
    for cat in ("NEG", "DV", "VC"):
        total, pyg, ori = descomponer_contable(cat, 123.0, 100.0, 115.0, 101.0, 3.0)
        assert total == pytest.approx(pyg + ori)


# ──────────────────────────────────────────────────────────────────────────────
# Test de la serie mensual: caso conocido con tres categorias
# ──────────────────────────────────────────────────────────────────────────────
def test_construir_rf_mensual_caso_conocido():
    fecha_end = date(2026, 12, 31)
    port = [
        _TituloFake(
            "NEG1",
            "Negociable",
            date(2030, 1, 1),
            {
                0: {"vm": 100.0, "vtir": 100.0, "cupones": 0.0},
                1: {"vm": 110.0, "vtir": 108.0, "cupones": 1.0},
            },
        ),
        _TituloFake(
            "DV1",
            "Disponible para la Venta",
            date(2030, 1, 1),
            {
                0: {"vm": 200.0, "vtir": 200.0, "cupones": 0.0},
                1: {"vm": 215.0, "vtir": 205.0, "cupones": 2.0},
            },
        ),
        # Titulo muerto al cierre -> debe excluirse de la serie
        _TituloFake(
            "MUERTO",
            "Negociable",
            date(2026, 6, 30),
            {0: {"vm": 50.0, "vtir": 50.0, "cupones": 0.0},
             1: {"vm": 55.0, "vtir": 55.0, "cupones": 0.0}},
        ),
    ]
    res_rf = [_ResMesFake(date(2026, 1, 31), 325.0, 313.0, 2)]

    filas = construir_rf_mensual_contable(res_rf, port, fecha_end)
    assert len(filas) == 1
    fila = filas[0]
    # NEG: total=(110-100)+1=11, pyg=11, ori=0
    # DV:  total=(215-200)+2=17, pyg=(205-200)+2=7, ori=10
    assert fila["total_resultado_mes"] == pytest.approx(28.0)
    assert fila["pyg_mes"] == pytest.approx(18.0)
    assert fila["ori_mes"] == pytest.approx(10.0)
    assert fila["cupones_mes"] == pytest.approx(3.0)
    assert fila["fecha"] == "2026-01"
    assert fila["n_titulos"] == 2


def test_construir_rf_mensual_omite_mes_sin_snapshot_anterior():
    """Si falta el snapshot del mes anterior, el titulo se omite para ese mes."""
    fecha_end = date(2026, 12, 31)
    port = [
        _TituloFake(
            "NEG1",
            "Negociable",
            date(2030, 1, 1),
            {1: {"vm": 110.0, "vtir": 108.0, "cupones": 1.0}},  # falta mes 0
        ),
    ]
    res_rf = [_ResMesFake(date(2026, 1, 31), 110.0, 108.0, 1)]
    filas = construir_rf_mensual_contable(res_rf, port, fecha_end)
    assert filas[0]["total_resultado_mes"] == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Propiedad: equivalencia byte a byte con la logica embebida original (Req 3.6)
# ──────────────────────────────────────────────────────────────────────────────
from hypothesis import HealthCheck  # noqa: E402

_CLASIFS = st.sampled_from(
    ["Negociable", "Disponible para la Venta", "Al Vencimiento", "Disp", "Venc"]
)
_finitos = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)
# Un snapshot mensual es un dict de tamano fijo (mas barato de generar que 3 draws sueltos)
# o None (ausencia de snapshot, ejercita la salvaguarda del helper).
_snap_o_none = st.one_of(
    st.none(),
    st.fixed_dictionaries({"vm": _finitos, "vtir": _finitos, "cupones": _finitos}),
)


@st.composite
def _portafolio_y_snapshots(draw):
    n_meses = draw(st.integers(min_value=1, max_value=5))
    n_titulos = draw(st.integers(min_value=0, max_value=5))
    fecha_end = date(2026, 12, 31)
    port = []
    for i in range(n_titulos):
        # El titulo esta vivo o muerto al cierre (ejercita ambos caminos).
        fv = date(2030, 1, 1) if draw(st.booleans()) else date(2026, 3, 31)
        # Snapshots de meses 0..n_meses, cada uno presente o ausente.
        snaps_lista = draw(
            st.lists(_snap_o_none, min_size=n_meses + 1, max_size=n_meses + 1)
        )
        snaps = {m: s for m, s in enumerate(snaps_lista) if s is not None}
        port.append(_TituloFake(f"T{i}", draw(_CLASIFS), fv, snaps))

    totales = draw(
        st.lists(
            st.tuples(_finitos, _finitos, st.integers(min_value=0, max_value=n_titulos)),
            min_size=n_meses,
            max_size=n_meses,
        )
    )
    res_rf = [
        _ResMesFake(date(2026, 1, 1), vm, vtir, n)  # strftime solo usa Y-m
        for (vm, vtir, n) in totales
    ]
    return res_rf, port, fecha_end


# Columnas de TOTAL producidas por la logica embebida original. El fix del Defecto 7
# (discriminacion local/intl) anade columnas *_local/*_intl de forma ADITIVA: estas
# columnas de total NO cambian de valor ni de nombre (equivalencia byte a byte).
_COLS_TOTAL_ORIGINAL = (
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


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_portafolio_y_snapshots())
def test_helper_equivale_a_logica_embebida_original(caso):
    """El helper produce EXACTAMENTE los mismos valores que la logica embebida original en
    las columnas de TOTAL existentes (Req 3.6). Tras el Defecto 7, el helper anade columnas
    *_local/*_intl de forma ADITIVA; la equivalencia byte a byte se verifica sobre las
    columnas de total, que no cambian de valor ni de nombre."""
    res_rf, port, fecha_end = caso
    esperado = _rf_mensual_contable_original(res_rf, port, fecha_end)
    obtenido = construir_rf_mensual_contable(res_rf, port, fecha_end)
    assert len(obtenido) == len(esperado)
    for fila_obt, fila_esp in zip(obtenido, esperado):
        # Las columnas de total existentes son identicas a la referencia (byte a byte).
        assert {k: fila_obt[k] for k in _COLS_TOTAL_ORIGINAL} == fila_esp
