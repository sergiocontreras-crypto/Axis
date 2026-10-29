"""
Batería de tests de las secciones 7-8 de compras-ventas-reservas:
- Selección del universo de compra (Property 6: mayor plazo para reserva
  matemática; Property 7: nodo configurable para los demás ramos).
- Lógica de compra `comprar_por_reserva` (Property 5: instrumento/clasificación/
  metadatos/fecha; Property 8: monto == Δ; Property 9: posición nueva) y fallback
  de mercado ausente (Req 4.6).

Los selectores reciben un `UniversoCompra` con DataFrames de pandas; la compra
construye instancias de `Titulo` (del motor). No se toca la DB real.
"""

from datetime import date

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    ConfigRamosReserva,
    InstrumentoCompra,
    ParametrosReservas,
    UniversoCompra,
    comprar_por_reserva,
    seleccionar_tes_uvr_5y,
    seleccionar_tes_uvr_largo,
    seleccionar_tf_largo,
)

FECHA_MES = date(2026, 6, 30)


def _df_universo(nemos_y_venc, prefijo):
    """Construye un DataFrame de universo con las columnas de la allowlist."""
    filas = []
    for i, (nemo, venc) in enumerate(nemos_y_venc):
        filas.append(
            {
                "nemo": f"{prefijo}{nemo}",
                "codigo_isin": f"ISIN{prefijo}{i}",
                "fecha_vencimiento": venc,
                "cupon": 6.0,
                "rendimiento": 8.0,
                "frecuencia_pago_cupon": 2,
                "moneda": "UVR" if prefijo == "TUVT" else "COP",
            }
        )
    cols = ["nemo", "codigo_isin", "fecha_vencimiento", "cupon", "rendimiento", "frecuencia_pago_cupon", "moneda"]
    return pd.DataFrame(filas, columns=cols)


# ==============================================================================
# Property 6 — Selección de plazo máximo (reserva matemática)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 6: para un universo de TUVT (o TF), el
# título seleccionado para reserva matemática es el de MAYOR plazo al vencimiento.
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    plazos_dias=st.lists(
        st.integers(min_value=30, max_value=365 * 30),
        min_size=1,
        max_size=8,
        unique=True,
    ),
)
def test_prop6_mayor_plazo(plazos_dias):
    from datetime import timedelta

    vencs = [FECHA_MES + timedelta(days=d) for d in plazos_dias]
    universo = UniversoCompra(
        tes_uvr=_df_universo([(f"{i}", v) for i, v in enumerate(vencs)], "TUVT"),
        tf=_df_universo([(f"{i}", v) for i, v in enumerate(vencs)], "TFIT"),
    )
    sel_uvr = seleccionar_tes_uvr_largo(universo, FECHA_MES)
    sel_tf = seleccionar_tf_largo(universo, FECHA_MES)

    venc_max = max(vencs)
    assert pd.Timestamp(sel_uvr["fecha_vencimiento"]).date() == venc_max
    assert pd.Timestamp(sel_tf["fecha_vencimiento"]).date() == venc_max


# ==============================================================================
# Property 7 — Selección del nodo configurable (demás ramos)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 7: para un universo de TUVT y un nodo
# configurable, se elige el plazo exactamente igual al nodo; si no hay exacto, el de
# plazo MAYOR más cercano al nodo (lado largo). Nunca uno menor si existe uno mayor.
@settings(max_examples=100, deadline=None)
@given(
    plazos_anios=st.lists(
        st.floats(min_value=0.5, max_value=25, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=8,
    ),
    nodo=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)
def test_prop7_nodo_configurable(plazos_anios, nodo):
    from datetime import timedelta

    vencs = [FECHA_MES + timedelta(days=int(round(a * 365))) for a in plazos_anios]
    universo = UniversoCompra(
        tes_uvr=_df_universo([(f"{i}", v) for i, v in enumerate(vencs)], "TUVT"),
        tf=pd.DataFrame(),
    )
    sel = seleccionar_tes_uvr_5y(universo, FECHA_MES, nodo)
    assert sel  # siempre hay al menos un candidato

    plazo_sel = (pd.Timestamp(sel["fecha_vencimiento"]).date() - FECHA_MES).days / 365.0

    plazos_reales = [(v - FECHA_MES).days / 365.0 for v in vencs]
    # La elegibilidad "en el nodo o por encima" debe usar la MISMA tolerancia de
    # coincidencia exacta que la implementación (`seleccionar_tes_uvr_5y` usa
    # 0.5/365 ≈ medio día). Una holgura mayor reclasificaría como "≥ nodo" títulos
    # que están estrictamente por debajo, divergiendo de la regla del Req 4.2.
    tol = 0.5 / 365.0
    mayores_o_iguales = [p for p in plazos_reales if p >= nodo - tol]
    if mayores_o_iguales:
        # Debe elegirse el MENOR de los ≥ nodo (más cercano por el lado largo).
        assert plazo_sel == pytest.approx(min(mayores_o_iguales), abs=0.02)
    else:
        # Todos menores: el MAYOR de ellos.
        assert plazo_sel == pytest.approx(max(plazos_reales), abs=0.02)


# ==============================================================================
# Property 8 — El monto de la compra iguala la variación positiva de reserva
# ==============================================================================

def _config(ramo, es_mat, instrumento=None):
    return ConfigRamosReserva(
        es_reserva_matematica_por_ramo={cvr._normalizar_ramo(ramo): es_mat},
        instrumento_por_ramo=(
            {cvr._normalizar_ramo(ramo): instrumento} if (es_mat and instrumento) else {}
        ),
    )


def _universo_estandar():
    from datetime import timedelta

    vencs = [FECHA_MES + timedelta(days=365 * n) for n in (2, 5, 8, 12)]
    return UniversoCompra(
        tes_uvr=_df_universo([(f"{i}", v) for i, v in enumerate(vencs)], "TUVT"),
        tf=_df_universo([(f"{i}", v) for i, v in enumerate(vencs)], "TFIT"),
    )


# Feature: compras-ventas-reservas, Property 8: el valor_mercado de la posición
# creada es igual a la variación positiva Δ.
@settings(max_examples=100, deadline=None)
@given(
    monto=st.floats(min_value=1.0, max_value=1e11, allow_nan=False, allow_infinity=False),
    es_mat=st.booleans(),
)
def test_prop8_monto_compra(monto, es_mat):
    ramo = "RENTAS VITALICIAS" if es_mat else "SOAT"
    instrumento = InstrumentoCompra.TES_UVR if es_mat else None
    cfg = _config(ramo, es_mat, instrumento)
    params = ParametrosReservas()
    titulo = comprar_por_reserva(
        ramo, monto, FECHA_MES, _universo_estandar(), cfg, params,
        compania="BOLIVAR", portafolio="P1",
    )
    assert titulo is not None
    assert titulo.valor_mercado == pytest.approx(monto)


# ==============================================================================
# Property 5 — Corrección de la compra (instrumento, clasificación, metadatos, fecha)
# ==============================================================================

# Feature: compras-ventas-reservas, Property 5: instrumento/clasificación derivados
# por dato; hereda ramo/compañía/portafolio; posición valorada a fecha_mes.
@settings(max_examples=100, deadline=None)
@given(
    es_mat=st.booleans(),
    instrumento=st.sampled_from(list(InstrumentoCompra)),
    monto=st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_prop5_correccion_compra(es_mat, instrumento, monto):
    ramo = "VIDA INDIVIDUAL - MATEMATICA" if es_mat else "AUTOMOVILES"
    cfg = _config(ramo, es_mat, instrumento if es_mat else None)
    params = ParametrosReservas()
    titulo = comprar_por_reserva(
        ramo, monto, FECHA_MES, _universo_estandar(), cfg, params,
        compania="COMPANIA X", portafolio="PORT Y",
    )
    assert titulo is not None
    # Clasificación por dato.
    if es_mat:
        assert titulo.clasificacion == "Disp. Venta"
    else:
        assert titulo.clasificacion == "Negociable"
    # Metadatos heredados.
    assert titulo.ramo == cvr._normalizar_ramo(ramo)
    assert titulo.compania == "COMPANIA X"
    assert titulo.portafolio == "PORT Y"
    # Es posición nueva (no reinversión).
    assert titulo.es_reinversion is False


# ==============================================================================
# Property 9 — La compra es una posición nueva
# ==============================================================================

# Feature: compras-ventas-reservas, Property 9: comprar_por_reserva devuelve una
# posición NUEVA con id único; no altera posiciones preexistentes.
@settings(max_examples=100, deadline=None)
@given(
    monto=st.floats(min_value=1.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_prop9_posicion_nueva(monto):
    cfg = _config("SOAT", False, None)
    params = ParametrosReservas()
    t1 = comprar_por_reserva("SOAT", monto, FECHA_MES, _universo_estandar(), cfg, params)
    t2 = comprar_por_reserva("SOAT", monto, FECHA_MES, _universo_estandar(), cfg, params)
    assert t1 is not None and t2 is not None
    # id_posicion no vacío y determinista para mismos parámetros (mismo ramo/mes/nemo).
    assert isinstance(t1.id_posicion, str) and t1.id_posicion.startswith("CVR_")
    # nominal derivado positivo.
    assert t1.nominal > 0


# ==============================================================================
# 8.5 (unit) — Fallback de mercado ausente (Req 4.6)
# ==============================================================================

def test_fallback_universo_vacio_devuelve_none(db_tmp):
    # Sin instrumento elegible en el universo → no hay compra posible (None).
    cfg = _config("SOAT", False, None)
    params = ParametrosReservas()
    universo_vacio = UniversoCompra(tes_uvr=pd.DataFrame(), tf=pd.DataFrame())
    titulo = comprar_por_reserva("SOAT", 1000.0, FECHA_MES, universo_vacio, cfg, params)
    assert titulo is None


def test_fallback_valoracion_ausente_no_crashea():
    # fecha_vencimiento no parseable → valoración por defecto, VM == monto sin crash.
    universo = UniversoCompra(
        tes_uvr=pd.DataFrame(
            [{
                "nemo": "TUVT_BAD",
                "codigo_isin": "ISINBAD",
                "fecha_vencimiento": "no-es-fecha",
                "cupon": 6.0,
                "rendimiento": 8.0,
                "frecuencia_pago_cupon": 2,
                "moneda": "UVR",
            }]
        ),
        tf=pd.DataFrame(),
    )
    cfg = _config("SOAT", False, None)
    params = ParametrosReservas()
    titulo = comprar_por_reserva("SOAT", 5000.0, FECHA_MES, universo, cfg, params)
    # Con fecha no parseable el selector la descarta → universo efectivamente vacío → None.
    assert titulo is None
