"""
Tests unitarios de BORDE para `consolidar_negociables_fungibles`
(feature `optimizacion-agrupacion-negociables`, Tarea 2.9).

A diferencia de los property tests (Hypothesis), aquí se usan DataFrames concretos
construidos a mano para ejercitar casos de borde específicos del diseño:

  - DataFrame vacío (caso degenerado → copia + ResumenConsolidacion en ceros).
  - Un único grupo fungible grande (muchas reinversiones Negociables idénticas).
  - Grupo cuyas `tasa_facial` difieren por ruido de punto flotante (deben agrupar).
  - Filas con clave degenerada (fallback a intocable + `logger.warning` genérico).
  - Columnas `clasificacion_contable` / `clase_origen` / `id_cobertura` ausentes
    (valor conservador según la tabla de Error Handling del diseño).
  - Verificación de los conteos del `ResumenConsolidacion` en cada caso.

Se verifican además las sumas (nominal/VM/VTIR) y que las filas intocables se
preservan intactas. No se usan mocks: la función pura opera sobre DataFrames reales.

Convenciones: se importa `proyeccion_tramos` como en `tests/test_proyeccion_tramos.py`
(la ruta `scripts/` ya está en `sys.path` por `tests/conftest.py`).

_Requirements: 5.4, 5.5, 6.5_
"""

import logging

import pandas as pd
import pytest

import proyeccion_tramos as pt
from proyeccion_tramos import (
    ResumenConsolidacion,
    consolidar_negociables_fungibles,
)


# ── Columnas usadas por los casos (subconjunto estable de la allowlist _COLUMNAS_PROY). ──
_COLS = [
    "id_posicion", "especie", "isin", "moneda", "fecha_emision",
    "fecha_vencimiento", "nominal", "valor_mercado", "valor_tir", "tasa_compra",
    "clasificacion_contable", "clase_origen", "id_cobertura",
    "tasa_referencia", "tasa_facial", "tipo_tasa", "periodicidad",
    "asset_class_definitivo",
]


def _fila(
    id_posicion,
    *,
    especie="TFIT",
    isin="CO1",
    moneda="COP",
    fecha_emision="2026-06-30",
    fecha_vencimiento="2032-01-01",
    nominal=1000.0,
    valor_mercado=1000.0,
    valor_tir=1000.0,
    tasa_compra=8.0,
    clasificacion_contable="Negociable",
    clase_origen="TITULO",
    id_cobertura="",
    tasa_referencia="",
    tasa_facial=7.0,
    tipo_tasa="EFECTIVO",
    periodicidad="ANUAL",
    asset_class_definitivo="DP LP",
):
    """Construye una fila (dict) de cierre de tramo con defaults Negociable fungibles."""
    return {
        "id_posicion": id_posicion,
        "especie": especie,
        "isin": isin,
        "moneda": moneda,
        "fecha_emision": fecha_emision,
        "fecha_vencimiento": fecha_vencimiento,
        "nominal": nominal,
        "valor_mercado": valor_mercado,
        "valor_tir": valor_tir,
        "tasa_compra": tasa_compra,
        "clasificacion_contable": clasificacion_contable,
        "clase_origen": clase_origen,
        "id_cobertura": id_cobertura,
        "tasa_referencia": tasa_referencia,
        "tasa_facial": tasa_facial,
        "tipo_tasa": tipo_tasa,
        "periodicidad": periodicidad,
        "asset_class_definitivo": asset_class_definitivo,
    }


def _df(filas, columnas=_COLS):
    return pd.DataFrame(filas, columns=columnas)


# ==============================================================================
# Caso 1 — DataFrame vacío (degenerado): copia + ResumenConsolidacion en ceros
# ==============================================================================

def test_df_vacio_devuelve_copia_y_resumen_en_ceros():
    df_in = _df([])
    df_out, resumen = consolidar_negociables_fungibles(df_in)

    assert isinstance(resumen, ResumenConsolidacion)
    assert len(df_out) == 0
    # Mismas columnas que la entrada (no se altera el esquema).
    assert list(df_out.columns) == list(df_in.columns)
    # Resumen todo en ceros (Req 6.5).
    assert resumen == ResumenConsolidacion(
        n_entrada=0, n_salida=0, n_negociables=0,
        n_intocables=0, n_grupos=0, n_colapsadas=0,
    )


def test_df_none_no_lanza():
    # Entrada None es un degenerado válido: no debe lanzar (Req 6.5).
    df_out, resumen = consolidar_negociables_fungibles(None)
    assert df_out is None
    assert resumen.n_entrada == 0 and resumen.n_colapsadas == 0


# ==============================================================================
# Caso 2 — Un único grupo fungible grande (muchas reinversiones idénticas)
# ==============================================================================

def test_un_unico_grupo_fungible_grande_colapsa_a_una_fila():
    n = 50
    # 50 reinversiones Negociables idénticas en clave (distinta fecha_emision/id).
    filas = [
        _fila(
            f"R{i}",
            fecha_emision=f"2026-{(i % 12) + 1:02d}-01",
            nominal=100.0 + i,
            valor_mercado=200.0 + i,
            valor_tir=300.0 + i,
        )
        for i in range(n)
    ]
    df_in = _df(filas)
    df_out, resumen = consolidar_negociables_fungibles(df_in)

    # Colapsa a exactamente una fila.
    assert len(df_out) == 1
    cons = df_out.iloc[0]

    # Sumas conservadas (Req 1.2, 1.3, 1.4).
    assert cons["nominal"] == pytest.approx(sum(100.0 + i for i in range(n)))
    assert cons["valor_mercado"] == pytest.approx(sum(200.0 + i for i in range(n)))
    assert cons["valor_tir"] == pytest.approx(sum(300.0 + i for i in range(n)))

    # id_posicion sintético determinista del consolidado.
    assert str(cons["id_posicion"]).startswith("CONS_")
    # clasificacion_contable invariante del grupo (Req 1.5).
    assert cons["clasificacion_contable"] == "Negociable"
    # fecha_emision = MÍNIMA del grupo (Req 4.2): enero 2026 es la mínima.
    assert pd.Timestamp(cons["fecha_emision"]) == pd.Timestamp("2026-01-01")

    # ResumenConsolidacion.
    assert resumen.n_entrada == n
    assert resumen.n_salida == 1
    assert resumen.n_negociables == n
    assert resumen.n_intocables == 0
    assert resumen.n_grupos == 1
    assert resumen.n_colapsadas == n - 1


# ==============================================================================
# Caso 3 — Tasas que difieren por ruido de punto flotante → DEBEN agrupar
# ==============================================================================

def test_tasas_con_ruido_de_punto_flotante_agrupan():
    # Dos tasas "iguales" cuya representación float difiere en los últimos bits.
    tasa_a = 0.1 + 0.2          # 0.30000000000000004
    tasa_b = 0.3                # 0.3
    assert tasa_a != tasa_b     # efectivamente distintas como float

    filas = [
        _fila("A", tasa_facial=tasa_a, nominal=1000.0, valor_mercado=1000.0, valor_tir=1000.0),
        _fila("B", tasa_facial=tasa_b, nominal=2000.0, valor_mercado=2000.0, valor_tir=2000.0),
    ]
    df_out, resumen = consolidar_negociables_fungibles(_df(filas))

    # El redondeo a precisión fija neutraliza el ruido → un solo grupo colapsado.
    assert len(df_out) == 1
    assert resumen.n_grupos == 1
    assert resumen.n_colapsadas == 1
    assert df_out.iloc[0]["nominal"] == pytest.approx(3000.0)
    assert df_out.iloc[0]["valor_mercado"] == pytest.approx(3000.0)


def test_tasas_genuinamente_distintas_no_agrupan():
    # Tasas que difieren más allá de la precisión de redondeo → NO agrupan (Req 2.3).
    filas = [
        _fila("A", tasa_facial=6.2),
        _fila("B", tasa_facial=6.3),
    ]
    df_out, resumen = consolidar_negociables_fungibles(_df(filas))
    assert len(df_out) == 2
    assert resumen.n_grupos == 0
    assert resumen.n_colapsadas == 0


# ==============================================================================
# Caso 4 — Clave degenerada: fallback a intocable + logger.warning genérico
# ==============================================================================

def test_clave_degenerada_fallback_intocable_y_warning(caplog):
    # Fila con fecha_vencimiento no parseable y otra con tasa_facial no finita:
    # ambas Negociables candidatas pero con clave None → se preservan como intocables.
    filas = [
        _fila("BAD_FECHA", fecha_vencimiento="no-es-fecha"),
        _fila("BAD_TASA", tasa_facial="xx"),
        _fila("OK1", nominal=1000.0),
        _fila("OK2", nominal=2000.0),  # fungible con OK1 → colapsan
    ]
    df_in = _df(filas)

    with caplog.at_level(logging.WARNING, logger="proyeccion_tramos"):
        df_out, resumen = consolidar_negociables_fungibles(df_in)

    ids = set(df_out["id_posicion"])
    # Las filas con clave degenerada NUNCA se pierden (siguen en la salida).
    assert "BAD_FECHA" in ids
    assert "BAD_TASA" in ids
    # OK1 y OK2 colapsaron a un consolidado (CONS_...).
    assert any(str(i).startswith("CONS_") for i in ids)
    assert "OK1" not in ids and "OK2" not in ids

    # Se emitió UN warning genérico, sin volcar contenido sensible de las filas.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "clave de fungibilidad no coercionable" in msg
    assert "BAD_FECHA" not in msg and "BAD_TASA" not in msg

    # Resumen: 2 negociables válidos (OK1, OK2) → 1 grupo; 2 intocables (las degeneradas).
    assert resumen.n_negociables == 2
    assert resumen.n_intocables == 2
    assert resumen.n_grupos == 1
    assert resumen.n_entrada == 4
    assert resumen.n_salida == 3  # 2 intocables + 1 consolidado
    assert resumen.n_colapsadas == 1


# ==============================================================================
# Caso 5 — Columnas ausentes: valor conservador (tabla de Error Handling)
# ==============================================================================

def test_sin_columna_clasificacion_contable_devuelve_tal_cual():
    # Si falta `clasificacion_contable`, se asume el valor más conservador: nada se
    # consolida y se devuelve el DataFrame tal cual con resumen degenerado (Req 6.5 / Req 2).
    cols = [c for c in _COLS if c != "clasificacion_contable"]
    filas = [_fila("A"), _fila("B")]
    filas = [{k: v for k, v in f.items() if k != "clasificacion_contable"} for f in filas]
    df_in = _df(filas, columnas=cols)

    df_out, resumen = consolidar_negociables_fungibles(df_in)

    # No se consolida nada; mismas filas y columnas.
    assert len(df_out) == 2
    assert list(df_out.columns) == cols
    assert resumen.n_entrada == 2 and resumen.n_salida == 2
    assert resumen.n_negociables == 0
    assert resumen.n_intocables == 2
    assert resumen.n_grupos == 0 and resumen.n_colapsadas == 0


def test_sin_columna_clase_origen_se_trata_como_rf():
    # Sin `clase_origen`, por compatibilidad con snapshots RF-only se asume RF → los
    # Negociables fungibles SÍ se consolidan.
    cols = [c for c in _COLS if c != "clase_origen"]
    filas = [
        {k: v for k, v in _fila("A", nominal=1000.0).items() if k != "clase_origen"},
        {k: v for k, v in _fila("B", nominal=2000.0).items() if k != "clase_origen"},
    ]
    df_out, resumen = consolidar_negociables_fungibles(_df(filas, columnas=cols))

    assert len(df_out) == 1
    assert resumen.n_grupos == 1
    assert resumen.n_colapsadas == 1
    assert df_out.iloc[0]["nominal"] == pytest.approx(3000.0)


def test_sin_columna_id_cobertura_se_trata_como_sin_swap():
    # Sin `id_cobertura`, se trata como vacío (sin swap) → los Negociables consolidan.
    cols = [c for c in _COLS if c != "id_cobertura"]
    filas = [
        {k: v for k, v in _fila("A", nominal=1000.0).items() if k != "id_cobertura"},
        {k: v for k, v in _fila("B", nominal=2000.0).items() if k != "id_cobertura"},
    ]
    df_out, resumen = consolidar_negociables_fungibles(_df(filas, columnas=cols))

    assert len(df_out) == 1
    assert resumen.n_grupos == 1
    assert resumen.n_colapsadas == 1


# ==============================================================================
# Caso 6 — Exclusiones inviolables preservan filas intactas (apoyo al Resumen)
# ==============================================================================

def test_mezcla_tir_norf_swap_preserva_intocables_y_consolida_negociables(caplog):
    filas = [
        # Dos Negociables fungibles RF sin swap → colapsan.
        _fila("NEG1", nominal=1000.0, valor_mercado=1000.0, valor_tir=1000.0),
        _fila("NEG2", nominal=1500.0, valor_mercado=1500.0, valor_tir=1500.0),
        # Título a TIR (Vencimiento) → intocable.
        _fila("VC1", clasificacion_contable="Vencimiento", tasa_compra=6.2),
        # Título a TIR (Disp. Venta) → intocable.
        _fila("DV1", clasificacion_contable="Disp. Venta", tasa_compra=6.3),
        # Fila no-RF (Renta Variable) → intocable.
        _fila("RV1", clase_origen="RENTA_VARIABLE", clasificacion_contable="Negociable"),
        # Negociable subyacente de swap (id_cobertura no vacío) → intocable (Req 2.5).
        _fila("SWP1", id_cobertura="SWAP_123", nominal=1000.0),
    ]
    df_in = _df(filas)

    with caplog.at_level(logging.WARNING, logger="proyeccion_tramos"):
        df_out, resumen = consolidar_negociables_fungibles(df_in)

    ids = set(df_out["id_posicion"])
    # Intocables intactos (mismo id_posicion presente).
    for intocable in ("VC1", "DV1", "RV1", "SWP1"):
        assert intocable in ids
    # Negociables colapsados.
    assert "NEG1" not in ids and "NEG2" not in ids
    cons = df_out[df_out["id_posicion"].astype(str).str.startswith("CONS_")]
    assert len(cons) == 1
    assert cons.iloc[0]["nominal"] == pytest.approx(2500.0)

    # A TIR conserva su tasa_compra individual (no se toca).
    vc = df_out[df_out["id_posicion"] == "VC1"].iloc[0]
    dv = df_out[df_out["id_posicion"] == "DV1"].iloc[0]
    assert vc["tasa_compra"] == pytest.approx(6.2)
    assert dv["tasa_compra"] == pytest.approx(6.3)

    # No hubo clave degenerada → ningún warning.
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    # Resumen: 2 negociables (NEG1, NEG2) → 1 grupo; 4 intocables (VC1,DV1,RV1,SWP1).
    assert resumen.n_entrada == 6
    assert resumen.n_negociables == 2
    assert resumen.n_intocables == 4
    assert resumen.n_grupos == 1
    assert resumen.n_salida == 5  # 4 intocables + 1 consolidado
    assert resumen.n_colapsadas == 1


def test_no_muta_la_entrada():
    # La función es pura: no debe mutar el DataFrame de entrada (Req 5.1).
    filas = [_fila("A", nominal=1000.0), _fila("B", nominal=2000.0)]
    df_in = _df(filas)
    df_in_copia = df_in.copy(deep=True)

    consolidar_negociables_fungibles(df_in)

    pd.testing.assert_frame_equal(df_in, df_in_copia)
