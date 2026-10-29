"""
Tests del comparador de equivalencia de snapshots CON vs SIN consolidación
(feature `optimizacion-agrupacion-negociables`, Tarea 7.1).

Verifican que `tools/comparar_snapshots_equivalencia.py`:
  - Reconoce como EQUIVALENTES dos snapshots donde uno tiene las reinversiones
    Negociables individuales y el otro el `Titulo_Consolidado` (suma por grupo), tanto
    en totales de cierre VM/VTIR como agregando por `Clave_Fungibilidad` (Req 3.1-3.3).
  - Reconoce como EQUIVALENTES las series de P&L mercado/TIR y cupones por mes cuando
    coinciden dentro de la tolerancia, y las marca cuando no (Req 3.3, 3.4).
  - LANZA `EquivalenciaExcedidaError` (aserción automatizada) cuando se excede la
    `Tolerancia_Equivalencia` `1e-6` relativo (Req 3.6).

No se usan mocks: la comparación opera sobre DataFrames reales y reutiliza la clave de
reconstrucción real `proyeccion_tramos._clave_fungibilidad`.

Convenciones: `tests/conftest.py` ya pone `scripts/` en `sys.path`; el módulo bajo
prueba (en `tools/`) añade la raíz y `scripts/` a su propio `sys.path` al importarse.

_Requirements: 3.1, 3.2, 3.3, 3.4, 3.6_
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# `tools/` no está en sys.path por conftest; se añade aquí la raíz del proyecto.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.comparar_snapshots_equivalencia import (  # noqa: E402
    EquivalenciaExcedidaError,
    comparar_series_mensuales,
    comparar_snapshots,
    comparar_snapshots_por_clave,
    comparar_totales_cierre,
    verificar_equivalencia_snapshots,
    TOLERANCIA_EQUIVALENCIA,
)


# ── Columnas de un snapshot (subconjunto estable de la allowlist _COLUMNAS_PROY). ──
_COLS = [
    "id_posicion", "especie", "isin", "moneda", "fecha_emision",
    "fecha_vencimiento", "nominal", "valor_mercado", "valor_tir",
    "clasificacion_contable", "clase_origen", "id_cobertura",
    "tasa_referencia", "tasa_facial", "tipo_tasa", "periodicidad",
    "asset_class_definitivo",
]


def _fila(idp, nominal, vm, vtir, *, clasif="Negociable", fe="2027-01-31",
          especie="TFIT", tasa_facial=7.0, fv="2032-01-01"):
    return {
        "id_posicion": idp, "especie": especie, "isin": "CO1", "moneda": "COP",
        "fecha_emision": fe, "fecha_vencimiento": fv,
        "nominal": nominal, "valor_mercado": vm, "valor_tir": vtir,
        "clasificacion_contable": clasif, "clase_origen": "TITULO",
        "id_cobertura": "", "tasa_referencia": "", "tasa_facial": tasa_facial,
        "tipo_tasa": "EFECTIVO", "periodicidad": "ANUAL",
        "asset_class_definitivo": "DP LP",
    }


def _df(filas):
    return pd.DataFrame(filas, columns=_COLS)


# ==============================================================================
# Totales de cierre VM/VTIR (Req 3.1, 3.2)
# ==============================================================================

def test_totales_cierre_equivalentes_sin_vs_consolidado():
    # SIN: tres Negociables fungibles. CON: su consolidado (suma exacta).
    sin = _df([
        _fila("R1", 1000.0, 1010.0, 1005.0),
        _fila("R2", 2000.0, 2020.0, 2010.0),
        _fila("R3", 3000.0, 3030.0, 3015.0),
    ])
    con = _df([_fila("CONS", 6000.0, 6060.0, 6030.0)])

    desvios = comparar_totales_cierre(sin, con)
    assert {d.etiqueta for d in desvios} == {"VM_total", "VTIR_total"}
    assert all(not d.excede for d in desvios)
    assert all(d.desvio_relativo <= TOLERANCIA_EQUIVALENCIA for d in desvios)


def test_totales_cierre_excede_marca_desvio():
    sin = _df([_fila("R1", 1000.0, 1010.0, 1005.0)])
    # VM alterado muy por encima de la tolerancia relativa 1e-6.
    con = _df([_fila("CONS", 1000.0, 1011.0, 1005.0)])

    desvios = comparar_totales_cierre(sin, con)
    vm = next(d for d in desvios if d.etiqueta == "VM_total")
    assert vm.excede
    assert vm.desvio_relativo > TOLERANCIA_EQUIVALENCIA


# ==============================================================================
# Agregación por Clave_Fungibilidad (Req 3.3)
# ==============================================================================

def test_por_clave_consolidado_iguala_suma_de_miembros():
    sin = _df([
        _fila("R1", 1000.0, 1010.0, 1005.0, fe="2027-01-31"),
        _fila("R2", 2000.0, 2020.0, 2010.0, fe="2027-02-28"),
        _fila("R3", 3000.0, 3030.0, 3015.0, fe="2027-03-31"),
    ])
    # El consolidado comparte la MISMA clave (misma especie/isin/venc/tasa/.../moneda).
    con = _df([_fila("CONS", 6000.0, 6060.0, 6030.0)])

    desvios = comparar_snapshots_por_clave(sin, con)
    assert desvios, "debe comparar al menos un grupo"
    assert all(not d.excede for d in desvios)


def test_por_clave_detecta_grupo_mal_sumado():
    sin = _df([
        _fila("R1", 1000.0, 1010.0, 1005.0),
        _fila("R2", 2000.0, 2020.0, 2010.0),
    ])
    # Consolidado con VM que NO es la suma (3030 esperado) → desvío por clave.
    con = _df([_fila("CONS", 3000.0, 3035.0, 3015.0)])

    desvios = comparar_snapshots_por_clave(sin, con)
    assert any(d.excede for d in desvios)


def test_por_clave_distinta_no_se_mezcla():
    # Dos claves distintas (tasa_facial diferente): se comparan por separado.
    sin = _df([
        _fila("R1", 1000.0, 1010.0, 1005.0, tasa_facial=7.0),
        _fila("R2", 2000.0, 2020.0, 2010.0, tasa_facial=9.0),
    ])
    con = _df([
        _fila("C1", 1000.0, 1010.0, 1005.0, tasa_facial=7.0),
        _fila("C2", 2000.0, 2020.0, 2010.0, tasa_facial=9.0),
    ])
    desvios = comparar_snapshots_por_clave(sin, con)
    assert all(not d.excede for d in desvios)


# ==============================================================================
# Series mensuales: P&L mercado/TIR y cupones por mes (Req 3.3, 3.4)
# ==============================================================================

def _serie(valores):
    """valores: lista de (mes, pnl_mercado, pnl_tir, cupones)."""
    return [
        {"mes": m, "pnl_mercado_mes": pm, "pnl_tir_mes": pt, "cupones_cobrados_mes": cu}
        for (m, pm, pt, cu) in valores
    ]


def test_series_mensuales_equivalentes():
    s = _serie([(1, 10.0, 5.0, 0.0), (2, 20.0, 10.0, 120.0)])
    desvios = comparar_series_mensuales(s, list(s))
    assert all(not d.excede for d in desvios)


def test_series_mensuales_cupon_distinto_marca_desvio():
    sin = _serie([(1, 10.0, 5.0, 100.0)])
    con = _serie([(1, 10.0, 5.0, 100.1)])  # cupón del mes difiere
    desvios = comparar_series_mensuales(sin, con)
    cupon = next(d for d in desvios if d.etiqueta.startswith("cupones_cobrados_mes"))
    assert cupon.excede


def test_series_mensuales_distinto_numero_de_meses():
    sin = _serie([(1, 1.0, 1.0, 1.0)])
    con = _serie([(1, 1.0, 1.0, 1.0), (2, 1.0, 1.0, 1.0)])
    desvios = comparar_series_mensuales(sin, con)
    assert any(d.etiqueta == "n_meses" and d.excede for d in desvios)


# ==============================================================================
# Aserción automatizada end-to-end (Req 3.6)
# ==============================================================================

def test_verificar_equivalencia_no_lanza_cuando_equivalente():
    sin = _df([
        _fila("R1", 1000.0, 1010.0, 1005.0),
        _fila("R2", 2000.0, 2020.0, 2010.0),
    ])
    con = _df([_fila("CONS", 3000.0, 3030.0, 3015.0)])
    serie = _serie([(1, 10.0, 5.0, 0.0)])

    reporte = verificar_equivalencia_snapshots(
        sin, con, serie_sin=serie, serie_con=list(serie)
    )
    assert reporte.equivalente
    assert reporte.excedidos == ()


def test_verificar_equivalencia_lanza_cuando_excede():
    sin = _df([_fila("R1", 1000.0, 1010.0, 1005.0)])
    con = _df([_fila("CONS", 1000.0, 1100.0, 1005.0)])  # VM muy distinto
    with pytest.raises(EquivalenciaExcedidaError):
        verificar_equivalencia_snapshots(sin, con)


def test_comparar_snapshots_no_lanza_y_reporta():
    # `comparar_snapshots` es la variante pura (no lanza): reporta aunque haya desvíos.
    sin = _df([_fila("R1", 1000.0, 1010.0, 1005.0)])
    con = _df([_fila("CONS", 1000.0, 1100.0, 1005.0)])
    reporte = comparar_snapshots(sin, con)
    assert not reporte.equivalente
    assert len(reporte.excedidos) >= 1


def test_tolerancia_de_referencia_es_1e_6():
    assert TOLERANCIA_EQUIVALENCIA == 1e-6
