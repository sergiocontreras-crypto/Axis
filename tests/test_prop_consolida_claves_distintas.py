"""
Property test de la feature `optimizacion-agrupacion-negociables`.

Property 4: Solo se fusionan Negociables con clave idéntica.

Para cualquier par de `Titulo_Negociable` que difieran en al menos un componente de la
`Clave_Fungibilidad`, la consolidación NO los fusiona (quedan como filas distintas en la
salida).

Se verifica sobre la función pura `consolidar_negociables_fungibles` de
`scripts/proyeccion_tramos.py`, usando el helper `_clave_fungibilidad` para calcular la
agrupación esperada (número de claves distintas = número de filas de salida esperadas
cuando todas las filas son Negociables consolidables).

Hypothesis >= 100 iteraciones. Función pura, sin mocks ni I/O.
"""

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import proyeccion_tramos as pt
from proyeccion_tramos import (
    consolidar_negociables_fungibles,
    _clave_fungibilidad,
)


# ── Perfiles de clave PRECOMPUTADOS que CONTROLAN cada componente de la clave ──
# En vez de dibujar muchos campos por fila (costoso para Hypothesis en este entorno),
# generamos un ÍNDICE a una lista pequeña de "perfiles de clave". Perfiles distintos
# tienen claves de fungibilidad distintas; el mismo perfil comparte clave. Así ejercemos
# tanto la fusión (perfil repetido) como la NO-fusión (perfiles distintos) de forma
# barata y determinista. Cada perfil fija los campos de texto/fecha/tasa de la clave,
# incluyendo variantes que SOLO difieren en un componente (ej. tasa_facial o moneda).
_PERFILES_CLAVE = [
    # (especie, isin, fecha_vencimiento, tasa_facial, periodicidad, tipo_tasa, moneda, asset_class, tasa_referencia)
    ("TFIT", "CO1", "2030-01-01", 7.0, "ANUAL", "EFECTIVO", "COP", "DP LP", ""),
    ("TFIT", "CO1", "2030-01-01", 7.5, "ANUAL", "EFECTIVO", "COP", "DP LP", ""),      # difiere SOLO en tasa_facial
    ("TFIT", "CO1", "2030-01-01", 7.0, "ANUAL", "EFECTIVO", "USD", "LEGATRUU", ""),   # difiere en moneda/tipo_bono
    ("TFIP", "CO2", "2032-06-30", 12.25, "SEMESTRAL", "NOMINAL", "UVR", "UVR_TF", ""),
    ("YANKEE1", "", "2028-12-31", 0.0, "ANUAL", "", "USD", "Estructuras", ""),
    ("TFIT", "CO1", "2030-01-01", 7.0, "ANUAL", "EFECTIVO", "COP", "DP LP", "IPC"),   # difiere en tipo_bono (IPC)
]


def _fila_desde_perfil(idx: int, nominal: float, vm: float, vtir: float, sufijo: int) -> dict:
    """Materializa una fila Negociable consolidable a partir de un índice de perfil."""
    (especie, isin, fv, tasa, period, ttasa, moneda, aclass, tref) = _PERFILES_CLAVE[idx]
    return {
        "id_posicion": f"ID{sufijo}",
        "especie": especie,
        "isin": isin,
        "fecha_vencimiento": fv,
        "tasa_facial": tasa,
        "periodicidad": period,
        "tipo_tasa": ttasa,
        "moneda": moneda,
        "asset_class_definitivo": aclass,
        "tasa_referencia": tref,
        "nominal": nominal,
        "valor_mercado": vm,
        "valor_tir": vtir,
        "fecha_emision": "2024-01-01",
        "clasificacion_contable": "Negociable",
        "clase_origen": "TITULO",
        "id_cobertura": "",
    }


# Estrategia barata: lista de (índice_perfil, nominal, vm, vtir). min_size=2 para que
# haya al menos un par que comparar.
_num = st.floats(min_value=1.0, max_value=1e7, allow_nan=False, allow_infinity=False)
_registros = st.lists(
    st.tuples(st.integers(min_value=0, max_value=len(_PERFILES_CLAVE) - 1), _num, _num, _num),
    min_size=2,
    max_size=12,
)


# ==============================================================================
# Property 4: Solo se fusionan Negociables con clave idéntica
# ==============================================================================

# Feature: optimizacion-agrupacion-negociables, Property 4: Solo se fusionan Negociables
# con clave idéntica
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(registros=_registros)
def test_prop4_solo_fusiona_claves_identicas(registros):
    filas = [
        _fila_desde_perfil(idx, nominal, vm, vtir, sufijo=i)
        for i, (idx, nominal, vm, vtir) in enumerate(registros)
    ]
    df = pd.DataFrame(filas)

    df_out, resumen = consolidar_negociables_fungibles(df)

    # Agrupación esperada según el MISMO helper que usa la función: cada clave distinta
    # produce exactamente UNA fila de salida (consolidado si >=2 miembros, singleton si 1).
    claves = [_clave_fungibilidad(df.iloc[i]) for i in range(len(df))]
    # Todas las filas generadas son coercionables (dominios controlados): sin None.
    assert all(k is not None for k in claves), "las filas controladas deben tener clave"
    claves_distintas = set(claves)

    # (a) El nº de filas de salida es exactamente el nº de claves distintas: dos filas con
    #     clave distinta NUNCA colapsan en la misma fila (no se fusionan).
    assert len(df_out) == len(claves_distintas)

    # (b) El resumen es coherente: no hay intocables (todas consolidables) y las filas
    #     colapsadas = entrada - claves distintas.
    assert resumen.n_intocables == 0
    assert resumen.n_negociables == len(df)
    assert resumen.n_colapsadas == len(df) - len(claves_distintas)

    # (c) Invariante fuerte de no-fusión por par: para cualquier par de filas de ENTRADA
    #     con claves distintas, no existe una única fila de salida que "contenga" a ambas.
    #     Lo verificamos comprobando que cada clave distinta aparece en a lo sumo una fila
    #     de salida (biyección clave_distinta -> fila_salida).
    claves_salida = [_clave_fungibilidad(df_out.iloc[j]) for j in range(len(df_out))]
    assert all(k is not None for k in claves_salida)
    # Sin claves de salida repetidas: cada grupo fungible produjo exactamente una fila.
    assert len(set(claves_salida)) == len(claves_salida)
    # El conjunto de claves de salida coincide con el conjunto de claves de entrada.
    assert set(claves_salida) == claves_distintas


# ==============================================================================
# Ejemplo dirigido: dos Negociables que difieren en UN solo componente no se fusionan
# ==============================================================================

def test_prop4_ejemplo_difieren_en_un_componente():
    """
    Dos filas idénticas salvo `tasa_facial` (un único componente de la clave): la
    consolidación debe dejarlas como DOS filas distintas (no fusiona).
    """
    base = {
        "id_posicion": "A", "especie": "TFIT", "isin": "CO1",
        "fecha_vencimiento": "2030-01-01", "tasa_facial": 7.0,
        "periodicidad": "ANUAL", "tipo_tasa": "EFECTIVO", "moneda": "COP",
        "asset_class_definitivo": "DP LP", "tasa_referencia": "",
        "nominal": 1000.0, "valor_mercado": 1000.0, "valor_tir": 1000.0,
        "fecha_emision": "2024-01-01", "clasificacion_contable": "Negociable",
        "clase_origen": "TITULO", "id_cobertura": "",
    }
    otra = dict(base, id_posicion="B", tasa_facial=7.5)  # difiere SOLO en tasa_facial
    df = pd.DataFrame([base, otra])

    df_out, resumen = consolidar_negociables_fungibles(df)

    assert len(df_out) == 2            # no se fusionaron
    assert resumen.n_grupos == 0       # ningún grupo con >=2 miembros
    assert resumen.n_colapsadas == 0
