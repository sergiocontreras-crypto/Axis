"""
Batería de tests de la sección 6 de compras-ventas-reservas:
- Derivación de la variación mensual desde el Excel de dos hojas (Property 27), con
  mapeo por DICCIONARIO explícito (etiqueta del Excel → ramo de portafolio) y las reglas:
  variación = Δsaldo − Δinterés por ramo de portafolio; Capitalizadora = agregado único
  desde TOTAL CAPI − interés Capitalizadora.
- Conservación de la agrupación "demás ramos" por diccionario (Property 28): varias
  etiquetas que apuntan al mismo destino (DEMAS RAMOS DE SEGUROS DE VIDA / GENERALES) se
  suman; un destino "" se ignora.
- Round-trip de persistencia del input derivado (Property 1).
- Unit tests de la derivación (ramo sin interés, agregado Capitalizadora, hojas
  faltantes) y de la persistencia del input.

Se construyen workbooks Excel EN MEMORIA (openpyxl vía pandas) con ENCABEZADOS DE FECHA
(datetime normalizados al primer día del mes), acorde al refactor base
(lectura-reservas-encabezados-fecha): el `Lector_Reservas` localiza las columnas de datos
por `Encabezado_Fecha` (no por nombres de mes) y devuelve `mes` como `Mes_Absoluto`
"YYYY-MM". La DB usada por la persistencia y por los mapeos es la temporal (fixture
db_tmp), que se siembra con los diccionarios semilla al primer acceso.
"""

import datetime as _dt
import io

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    ErrorInputReservas,
    RAMO_CAPITALIZADORA,
    RAMO_DEMAS_BOLIVAR,
    _normalizar_ramo,
    cargar_mapa_reservas,
    derivar_variaciones_reservas,
    persistir_input_reservas,
)

RUTA = "ignorada-por-conftest"
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]

# 12 encabezados de fecha: primer día de 12 meses consecutivos (2026-01 .. 2026-12).
# El refactor base localiza las columnas de datos por estos `Encabezado_Fecha` y ordena
# cronológicamente; los 11 saltos consecutivos (todos Δ=1 mes) son Salto_Mensual y su
# `Mes_Absoluto` destino es "2026-02" .. "2026-12".
_FECHAS = [_dt.date(2026, m, 1) for m in range(1, 13)]
# `Mes_Absoluto` ("YYYY-MM") de la columna DESTINO de cada uno de los 11 saltos.
_MESES_DESTINO = [f"{f.year:04d}-{f.month:02d}" for f in _FECHAS[1:]]


def _construir_workbook(
    bolivar_saldo: dict,
    comerciales_saldo: dict,
    capi_saldo_ramos: dict,
    total_capi: list,
    interes_lineas: dict,
    interes_capi: list,
    *,
    omitir_hoja=None,
) -> bytes:
    """
    Construye un .xlsx en memoria con las hojas 'Saldo Reservas' e
    'Interés a la Reserva' replicando el layout: columna en blanco, columna-etiqueta,
    y 12 columnas de datos cuyos ENCABEZADOS son fechas (primer día de 2026-01..2026-12).
    Devuelve los bytes del archivo.

    - bolivar_saldo/comerciales_saldo/capi_saldo_ramos: {etiqueta_saldo: [12 niveles]}
      (etiquetas del FORMATO SALDOS, p. ej. 'PENSIONES LEY 100').
    - total_capi: [12] niveles de la fila TOTAL CAPI.
    - interes_lineas: {etiqueta_interes: [12]} (etiquetas del FORMATO INTERÉS, p. ej.
      'Rentas Vitalicias', 'ARL Matemática').
    - interes_capi: [12] de la línea 'Capitalizadora' en la hoja de interés.
    - omitir_hoja: 'Saldo Reservas' | 'Interés a la Reserva' para simular hoja faltante.
    """
    def _fila(etiqueta, valores=None):
        fila = [None, etiqueta]
        fila += [None] * 12 if valores is None else list(valores)
        return fila

    # Fila de ENCABEZADOS DE FECHA: [None, None, ts_2026-01, ..., ts_2026-12].
    encabezado = _fila(None, [pd.Timestamp(f) for f in _FECHAS])

    # ── Hoja Saldo Reservas ──
    filas_saldo = [encabezado, _fila("BOLIVAR")]
    for ramo, vals in bolivar_saldo.items():
        filas_saldo.append(_fila(ramo, vals))
    filas_saldo.append(_fila("TOTAL BOLIVAR", [0] * 12))
    filas_saldo.append(_fila("COMERCIALES"))
    for ramo, vals in comerciales_saldo.items():
        filas_saldo.append(_fila(ramo, vals))
    filas_saldo.append(_fila("TOTAL COMERCIALES", [0] * 12))
    filas_saldo.append(_fila("CAPITALIZADORA"))
    for ramo, vals in capi_saldo_ramos.items():
        filas_saldo.append(_fila(ramo, vals))
    filas_saldo.append(_fila("TOTAL CAPI", total_capi))
    filas_saldo.append(_fila("TOTAL COMPAÑIAS", [0] * 12))
    df_saldo = pd.DataFrame(filas_saldo)

    # ── Hoja Interés a la Reserva ──
    filas_int = [encabezado, _fila("Interés a la Reserva")]
    filas_int.append(_fila("Capitalizadora", interes_capi))
    filas_int.append(_fila("Bolivar"))  # sub-cabecera de sección
    for linea, vals in interes_lineas.items():
        filas_int.append(_fila(linea, vals))
    filas_int.append(_fila("TOTAL COMPAÑIAS", [0] * 12))
    df_int = pd.DataFrame(filas_int)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        if omitir_hoja != "Saldo Reservas":
            df_saldo.to_excel(writer, sheet_name="Saldo Reservas", header=False, index=False)
        if omitir_hoja != "Interés a la Reserva":
            df_int.to_excel(writer, sheet_name="Interés a la Reserva", header=False, index=False)
        if omitir_hoja == "ambas":
            pd.DataFrame([[1]]).to_excel(writer, sheet_name="dummy", header=False, index=False)
    return buffer.getvalue()


def _delta_mes_a_mes(niveles):
    return [niveles[t] - niveles[t - 1] for t in range(1, len(niveles))]


def _serie_por_mes(df, ramo):
    """Devuelve la lista de delta_reserva del ramo, ordenada por Mes_Absoluto asc."""
    f = df[df["ramo"] == ramo].sort_values("mes")
    return list(f["delta_reserva"])


# ==============================================================================
# 6.6 (unit) — Ramo sin interés (Δsaldo) y agregado Capitalizadora
# ==============================================================================

def test_ramo_sin_interes_es_delta_saldo(db_tmp):
    # 'RENTAS VOLUNTARIAS' (saldo) mapea a 'RENTAS VOLUNTARIAS' (portafolio) y no tiene
    # línea de interés → variación = Δsaldo.
    saldo = [100.0 + 10 * i for i in range(12)]
    wb = _construir_workbook(
        bolivar_saldo={"RENTAS VOLUNTARIAS": saldo},
        comerciales_saldo={},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={},
        interes_capi=[0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    assert _serie_por_mes(df, "RENTAS VOLUNTARIAS") == pytest.approx(_delta_mes_a_mes(saldo))


def test_capitalizadora_agregado(db_tmp):
    total_capi = [1000.0 + 50 * i for i in range(12)]
    interes_capi = [0.0 + 5 * i for i in range(12)]
    wb = _construir_workbook(
        bolivar_saldo={},
        comerciales_saldo={},
        capi_saldo_ramos={"DABUENAVIDA": [1.0] * 12, "MAGNATE": [2.0] * 12},
        total_capi=total_capi,
        interes_lineas={},
        interes_capi=interes_capi,
    )
    df = derivar_variaciones_reservas(wb, None)
    assert RAMO_CAPITALIZADORA in set(df["ramo"])
    assert "DABUENAVIDA" not in set(df["ramo"])
    esperado = [ds - di for ds, di in
                zip(_delta_mes_a_mes(total_capi), _delta_mes_a_mes(interes_capi))]
    assert _serie_por_mes(df, RAMO_CAPITALIZADORA) == pytest.approx(esperado)


def test_bolivar_con_interes_resta_por_ramo_destino(db_tmp):
    # 'PENSIONES LEY 100' (saldo) y 'Rentas Vitalicias' (interés) mapean AMBOS a
    # 'RENTAS VITALICIAS' (portafolio) → variación = Δsaldo − Δinterés en ese ramo.
    saldo = [200.0 + 20 * i for i in range(12)]
    interes = [0.0 + 8 * i for i in range(12)]
    wb = _construir_workbook(
        bolivar_saldo={"PENSIONES LEY 100": saldo},
        comerciales_saldo={},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={"Rentas Vitalicias": interes},
        interes_capi=[0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    esperado = [ds - di for ds, di in zip(_delta_mes_a_mes(saldo), _delta_mes_a_mes(interes))]
    assert _serie_por_mes(df, "RENTAS VITALICIAS") == pytest.approx(esperado)


def test_comerciales_solo_delta_saldo(db_tmp):
    saldo = [500.0 - 30 * i for i in range(12)]
    wb = _construir_workbook(
        bolivar_saldo={},
        comerciales_saldo={"SOAT": saldo},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={},
        interes_capi=[0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    assert _serie_por_mes(df, "SOAT") == pytest.approx(_delta_mes_a_mes(saldo))


def test_ramo_ignorado_no_aparece(db_tmp):
    # 'RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY' mapea a "" (ignorar) → no sale.
    wb = _construir_workbook(
        bolivar_saldo={"RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY": [9.0] * 12},
        comerciales_saldo={},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={},
        interes_capi=[0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    # No debe aparecer bajo ninguna etiqueta (ni cruda ni agrupada).
    assert "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" not in set(df["ramo"])


def test_columnas_exactas(db_tmp):
    wb = _construir_workbook(
        bolivar_saldo={"RENTAS VOLUNTARIAS": [1.0] * 12},
        comerciales_saldo={},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={},
        interes_capi=[0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    # El refactor base añade `tipo_salto` y usa `mes` como Mes_Absoluto "YYYY-MM".
    assert list(df.columns) == ["ramo", "mes", "delta_reserva", "tipo_salto"]
    # Los 12 encabezados mensuales producen 11 saltos: destinos 2026-02..2026-12.
    assert set(df["mes"]) <= set(_MESES_DESTINO)
    # Todos los saltos aquí son mensuales (Δ=1 mes).
    assert set(df["tipo_salto"]) <= {"mensual"}


def test_workbook_hoja_faltante(db_tmp):
    wb = _construir_workbook(
        bolivar_saldo={"RENTAS VOLUNTARIAS": [1.0] * 12},
        comerciales_saldo={},
        capi_saldo_ramos={},
        total_capi=[0] * 12,
        interes_lineas={},
        interes_capi=[0] * 12,
        omitir_hoja="Interés a la Reserva",
    )
    with pytest.raises(ErrorInputReservas):
        derivar_variaciones_reservas(wb, None)


# ==============================================================================
# 6.8 (unit) — persistir_input_reservas: rechazo de columnas
# ==============================================================================

def test_persistir_rechaza_columnas_extra(db_tmp):
    df = pd.DataFrame(
        {"ramo": ["SOAT"], "mes": ["2026-02"], "delta_reserva": [1.0], "extra": [9]}
    )
    with pytest.raises(ValueError):
        persistir_input_reservas(df)


def test_persistir_rechaza_columnas_faltantes(db_tmp):
    df = pd.DataFrame({"ramo": ["SOAT"], "mes": ["2026-02"]})
    with pytest.raises(ValueError):
        persistir_input_reservas(df)


# ==============================================================================
# Property 1 — Round-trip de persistencia del input de reservas
# ==============================================================================

# Fecha de inicio de proyección para anclar el Mes_Absoluto con la convención Mes_Caído.
# Con inicio 2026-01, el offset de proyección de un Mes_Absoluto M es
#   offset = (M.year - 2026) * 12 + (M.month - 1) + 1
# de modo que M = 2026-02 cae en offset 2 ... M = 2026-12 en offset 12 (dentro de [1,12]).
_FECHA_INICIO = _dt.date(2026, 1, 1)


def _offset_mes_caido(mes_yyyymm: str, fecha_inicio: _dt.date) -> int:
    anio, mes = int(mes_yyyymm[:4]), int(mes_yyyymm[5:7])
    meses_entre = (anio - fecha_inicio.year) * 12 + (mes - fecha_inicio.month)
    return meses_entre + 1


# Feature: compras-ventas-reservas, Property 1: persistir la variación derivada y
# recargarla con cargar_mapa_reservas devuelve exactamente los mismos Δ por (ramo, mes),
# anclando cada Mes_Absoluto "YYYY-MM" al offset de proyección con Mes_Caído.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    registros=st.lists(
        st.tuples(
            st.sampled_from(["SOAT", "RENTAS VITALICIAS", RAMO_CAPITALIZADORA, RAMO_DEMAS_BOLIVAR]),
            # Mes_Absoluto "YYYY-MM" cuyo offset con inicio 2026-01 cae en [1, 12].
            st.sampled_from(_MESES_DESTINO),
            st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
        ),
        min_size=0,
        max_size=15,
        unique_by=lambda t: (_normalizar_ramo(t[0]), t[1]),
    ),
)
def test_prop1_roundtrip_input(db_tmp, registros):
    df = pd.DataFrame(registros, columns=["ramo", "mes", "delta_reserva"])
    persistir_input_reservas(df)

    mapa = cargar_mapa_reservas(RUTA, _FECHA_INICIO, 12)

    esperado: dict = {}
    for ramo, mes, delta in registros:
        offset = _offset_mes_caido(mes, _FECHA_INICIO)
        if 1 <= offset <= 12:
            esperado.setdefault(_normalizar_ramo(ramo), {})[offset] = float(delta)

    assert mapa == esperado


# ==============================================================================
# Property 27 — Derivación mes a mes por ramo de portafolio (saldo − interés)
# ==============================================================================

# Pares (etiqueta_saldo, etiqueta_interes, ramo_destino) del diccionario semilla, donde
# el mismo ramo de portafolio recibe saldo e interés.
_PARES_BOL = st.sampled_from([
    ("PENSIONES LEY 100", "Rentas Vitalicias", "RENTAS VITALICIAS"),
    ("VIDA INDIVIDUAL - MATEMÁTICA", "Vida Individual", "VIDA INDIVIDUAL-MATEMATICA"),
    ("PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA",
     "Invalidez y Sobrevivencia (Previsional)", "PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA"),
])
_ramos_com = st.sampled_from([("SOAT", "SOAT"), ("AUTOMOVILES", "AUTOMOVILES"),
                              ("TERREMOTO", "TERREMOTO")])
_niveles = st.lists(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    min_size=12, max_size=12,
)


# Feature: compras-ventas-reservas, Property 27: la variación derivada por ramo de
# portafolio es exactamente Δsaldo − Δinterés (interés 0 si no hay); Capitalizadora es
# ΔTOTAL_CAPI − Δinterés_capi; el mapeo saldo/interés lleva al mismo ramo destino.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    par_bol=_PARES_BOL,
    saldo_bol=_niveles,
    interes_bol=_niveles,
    par_com=_ramos_com,
    saldo_com=_niveles,
    total_capi=_niveles,
    interes_capi=_niveles,
)
def test_prop27_derivacion_por_ramo_destino(
    db_tmp, par_bol, saldo_bol, interes_bol, par_com, saldo_com, total_capi, interes_capi
):
    etq_saldo_bol, etq_int_bol, destino_bol = par_bol
    etq_saldo_com, destino_com = par_com

    wb = _construir_workbook(
        bolivar_saldo={etq_saldo_bol: saldo_bol},
        comerciales_saldo={etq_saldo_com: saldo_com},
        capi_saldo_ramos={},
        total_capi=total_capi,
        interes_lineas={etq_int_bol: interes_bol},
        interes_capi=interes_capi,
    )
    df = derivar_variaciones_reservas(wb, None)

    # Bolívar: Δsaldo − Δinterés en el ramo de portafolio destino.
    esp_bol = [ds - di for ds, di in
               zip(_delta_mes_a_mes(saldo_bol), _delta_mes_a_mes(interes_bol))]
    assert _serie_por_mes(df, destino_bol) == pytest.approx(esp_bol)

    # Comerciales: Δsaldo (sin interés).
    assert _serie_por_mes(df, destino_com) == pytest.approx(_delta_mes_a_mes(saldo_com))

    # Capitalizadora: ΔTOTAL_CAPI − Δinterés_capi.
    esp_capi = [ds - di for ds, di in
                zip(_delta_mes_a_mes(total_capi), _delta_mes_a_mes(interes_capi))]
    assert _serie_por_mes(df, RAMO_CAPITALIZADORA) == pytest.approx(esp_capi)


# ==============================================================================
# Property 28 — Conservación de la agrupación "demás ramos" por diccionario
# ==============================================================================

# Nombres de los grupos "demás ramos" tal como los produce la derivación: provienen
# de los destinos del diccionario semilla (`_SEED_MAPEO_SALDO`), no de los constantes
# RAMO_DEMAS_* (que son etiquetas de otra convención). Se declaran aquí como literales
# para verificar la agrupación por diccionario.
_GRUPO_DEMAS_GENERALES = "DEMAS RAMOS DE SEGUROS GENERALES"
_GRUPO_DEMAS_VIDA = "DEMAS RAMOS DE SEGUROS DE VIDA"

# Etiquetas de saldo que el diccionario agrupa en DEMAS RAMOS DE SEGUROS GENERALES.
_ETQ_DEMAS_GENERALES = ["CORRIENTE DEBIL", "LUCROCESANTE", "MANEJO", "TRANSPORTE", "HOGAR"]
# Etiqueta de Bolívar que agrupa en DEMAS RAMOS DE SEGUROS DE VIDA.
_ETQ_DEMAS_VIDA = ["VIDA GRUPO-SINIESTROS"]


# Feature: compras-ventas-reservas, Property 28: varias etiquetas de saldo que el
# diccionario mapea al mismo grupo "demás ramos" se SUMAN mes a mes en ese ramo destino,
# y la suma se conserva.
@settings(max_examples=100, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    saldos_generales=st.dictionaries(
        st.sampled_from(_ETQ_DEMAS_GENERALES), _niveles, min_size=1, max_size=5),
    saldo_vida=_niveles,
    incluir_vida=st.booleans(),
)
def test_prop28_conservacion_demas_ramos(db_tmp, saldos_generales, saldo_vida, incluir_vida):
    bolivar = {"VIDA GRUPO-SINIESTROS": saldo_vida} if incluir_vida else {}
    wb = _construir_workbook(
        bolivar_saldo=bolivar,
        comerciales_saldo=saldos_generales,
        capi_saldo_ramos={},
        total_capi=[0.0] * 12,
        interes_lineas={},  # sin interés → Δsaldo
        interes_capi=[0.0] * 12,
    )
    df = derivar_variaciones_reservas(wb, None)
    ramos_salida = set(df["ramo"])

    # DEMAS RAMOS DE SEGUROS GENERALES = suma de las variaciones de sus etiquetas.
    assert _GRUPO_DEMAS_GENERALES in ramos_salida
    suma_generales = [0.0] * 11
    for vals in saldos_generales.values():
        for i, d in enumerate(_delta_mes_a_mes(vals)):
            suma_generales[i] += d
    assert _serie_por_mes(df, _GRUPO_DEMAS_GENERALES) == pytest.approx(suma_generales)

    # DEMAS RAMOS DE SEGUROS DE VIDA solo si se incluyó la etiqueta correspondiente.
    if incluir_vida:
        assert _GRUPO_DEMAS_VIDA in ramos_salida
        assert _serie_por_mes(df, _GRUPO_DEMAS_VIDA) == pytest.approx(_delta_mes_a_mes(saldo_vida))
