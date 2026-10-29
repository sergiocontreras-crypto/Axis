# -*- coding: utf-8 -*-
"""
Test de INTEGRACIÓN (basado en ejemplo) para la feature
lectura-reservas-encabezados-fecha.

Tarea 11.2 — Integración con la forma real del archivo (patrón MIXTO).

Objetivo (Req 3.3, 8.1, 8.2, 9.3, 10.1):
  Ejercitar la cadena completa reader → persistor → loaders con un workbook de DOS
  hojas cuyos encabezados son FECHAS en el patrón MIXTO del insumo real:

      - Meses CONTIGUOS del año en curso  m0..Diciembre  (p. ej. 2026-06..2026-12)
      - Luego SOLO los diciembres de los años siguientes  (2027-12, 2028-12)

  y verificar que:
    (1) La RUTA MENSUAL (`cargar_mapa_reservas`, anclada por `Mes_Caído` a la
        `Fecha_Inicio_Proyeccion`) contiene los deltas mensuales esperados para los
        saltos de 1 mes del año en curso (Req 3.3).
    (2) La RUTA ANUAL (`cargar_variacion_anual_por_ano`) devuelve el mapa
        `{año: {ramo: delta}}` esperado para los saltos diciembre-a-diciembre de los
        años siguientes (Req 8.1, 8.2, 9.3), y que los años del insumo sí presentes NO
        se confunden entre sí; los años sin columna simplemente no aparecen (Δ=0, Req
        10.1 — el motor asume 0 para lo ausente).

El workbook se construye EN MEMORIA con `pandas.ExcelWriter` sobre un `BytesIO`
(engine openpyxl). Las etiquetas de ramo provienen del diccionario semilla que
`derivar_variaciones_reservas` siembra automáticamente en la DB temporal por prueba
(fixture autouse `db_tmp` de conftest.py). No se toca la `portafolio.db` real.
"""

import io
from datetime import date

import pandas as pd
import pytest

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    RAMO_CAPITALIZADORA,
    cargar_mapa_reservas,
    cargar_variacion_anual_por_ano,
    derivar_variaciones_reservas,
    persistir_input_reservas,
)


# ── Parámetros del patrón MIXTO del insumo real ──
# Año en curso y mes de inicio: la corrida arranca en junio de 2026 (no en enero) para
# ejercitar el mes de inicio dinámico (Req 4) además del patrón mixto.
_ANIO = 2026
_MES_INICIO = 6  # junio
# Diciembres de años siguientes presentes en el insumo (saltos anuales).
_ANIOS_SIGUIENTES = (2027, 2028)


def _fechas_patron_mixto() -> "list[date]":
    """
    Devuelve las fechas de encabezado en el patrón MIXTO, en orden cronológico:
      2026-06, 2026-07, ..., 2026-12, 2027-12, 2028-12.
    Se usan como `Encabezado_Fecha` (primer día del mes).
    """
    fechas = [date(_ANIO, m, 1) for m in range(_MES_INICIO, 13)]  # jun..dic 2026
    fechas += [date(a, 12, 1) for a in _ANIOS_SIGUIENTES]         # dic 2027, dic 2028
    return fechas


def _fila(etiqueta, valores=None, n=0):
    """
    Fila del layout: [columna en blanco, etiqueta, v0, v1, ...].
    `valores` es la serie de niveles por columna de fecha; `n` la longitud si va vacía.
    """
    fila = [None, etiqueta]
    fila += [None] * n if valores is None else list(valores)
    return fila


def _construir_workbook(
    *,
    fechas: "list[date]",
    saldo_bolivar: dict,
    saldo_comerciales: dict,
    total_capi: "list[float]",
    interes_lineas: dict,
    interes_capi: "list[float]",
) -> bytes:
    """
    Construye un .xlsx EN MEMORIA con las hojas 'Saldo Reservas' e
    'Interés a la Reserva', usando ENCABEZADOS DE FECHA (datetime) en las columnas de
    datos (patrón mixto). Devuelve los bytes del archivo.

    Layout por hoja (leído con header=None por el reader):
      - Columna 0: en blanco.
      - Columna 1: etiqueta (columna-etiqueta, texto a la izquierda de las fechas).
      - Columnas 2..: una por cada fecha de `fechas` (los `Encabezado_Fecha`).
      La FILA de encabezados pone las fechas en las columnas de datos; la columna-etiqueta
      de esa fila lleva un texto no-fecha ('BOLIVAR' / 'Interés a la Reserva').
    """
    n = len(fechas)
    # pandas escribe datetimes nativos como fechas de Excel; el reader los reconoce como
    # Encabezado_Fecha. Se usan Timestamps para que openpyxl los serialice como fechas.
    fila_encabezados_saldo = _fila("BOLIVAR", [pd.Timestamp(f) for f in fechas])
    fila_encabezados_int = _fila(
        "Interés a la Reserva", [pd.Timestamp(f) for f in fechas]
    )

    # ── Hoja Saldo Reservas ──
    filas_saldo = [_fila(None, n=n), fila_encabezados_saldo]
    for etiqueta, vals in saldo_bolivar.items():
        filas_saldo.append(_fila(etiqueta, vals))
    filas_saldo.append(_fila("TOTAL BOLIVAR", [0.0] * n))
    filas_saldo.append(_fila("COMERCIALES", n=n))
    for etiqueta, vals in saldo_comerciales.items():
        filas_saldo.append(_fila(etiqueta, vals))
    filas_saldo.append(_fila("TOTAL COMERCIALES", [0.0] * n))
    filas_saldo.append(_fila("CAPITALIZADORA", n=n))
    filas_saldo.append(_fila("TOTAL CAPI", total_capi))
    filas_saldo.append(_fila("TOTAL COMPAÑIAS", [0.0] * n))
    df_saldo = pd.DataFrame(filas_saldo)

    # ── Hoja Interés a la Reserva ──
    filas_int = [_fila(None, n=n), fila_encabezados_int]
    filas_int.append(_fila("Capitalizadora", interes_capi))
    filas_int.append(_fila("Bolivar", [0.0] * n))  # sub-cabecera de sección
    for linea, vals in interes_lineas.items():
        filas_int.append(_fila(linea, vals))
    filas_int.append(_fila("TOTAL COMPAÑIAS", [0.0] * n))
    df_int = pd.DataFrame(filas_int)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_saldo.to_excel(
            writer, sheet_name="Saldo Reservas", header=False, index=False
        )
        df_int.to_excel(
            writer, sheet_name="Interés a la Reserva", header=False, index=False
        )
    return buffer.getvalue()


def _delta_consecutivo(niveles):
    """Δ entre columnas cronológicamente consecutivas."""
    return [niveles[i] - niveles[i - 1] for i in range(1, len(niveles))]


def test_integracion_patron_mixto_rutas_mensual_y_anual(db_tmp):
    """
    Integración reader → persistor → loaders con el patrón MIXTO real.

    **Validates: Requirements 3.3, 8.1, 8.2, 9.3, 10.1**
    """
    fechas = _fechas_patron_mixto()
    n = len(fechas)  # 7 (jun..dic) + 2 (dic 2027, dic 2028) = 9 columnas

    # ── Niveles por etiqueta (una posición por fecha, en orden cronológico) ──
    # SOAT (comerciales) → ramo SOAT: variación = Δsaldo (sin interés).
    saldo_soat = [1000.0 + 100.0 * i for i in range(n)]
    # PENSIONES LEY 100 (saldo) y 'Rentas Vitalicias' (interés) → RENTAS VITALICIAS:
    # variación = Δsaldo − Δinterés en ese ramo destino.
    saldo_pensiones = [5000.0 + 250.0 * i for i in range(n)]
    interes_rentas = [200.0 + 30.0 * i for i in range(n)]
    # Capitalizadora: ΔTOTAL_CAPI − Δinterés_capi.
    total_capi = [9000.0 + 400.0 * i for i in range(n)]
    interes_capi = [100.0 + 10.0 * i for i in range(n)]

    wb = _construir_workbook(
        fechas=fechas,
        saldo_bolivar={"PENSIONES LEY 100": saldo_pensiones},
        saldo_comerciales={"SOAT": saldo_soat},
        total_capi=total_capi,
        interes_lineas={"Rentas Vitalicias": interes_rentas},
        interes_capi=interes_capi,
    )

    # ── 1) READER: derivar la variación por Encabezado_Fecha ──
    df = derivar_variaciones_reservas(wb, None)

    # Columnas EXACTAS que espera el persistidor (más tipo_salto).
    assert list(df.columns) == ["ramo", "mes", "delta_reserva", "tipo_salto"]

    # Meses absolutos esperados: la columna DESTINO de cada salto (todas menos la 1ª).
    meses_destino = [f"{f.year:04d}-{f.month:02d}" for f in fechas[1:]]
    # Los saltos jun..dic 2026 son MENSUALES; dic2026→dic2027 y dic2027→dic2028 ANUALES.
    tipos_esperados = {}
    for prev, cur in zip(fechas, fechas[1:]):
        delta_meses = (cur.year - prev.year) * 12 + (cur.month - prev.month)
        clave = f"{cur.year:04d}-{cur.month:02d}"
        tipos_esperados[clave] = "mensual" if delta_meses == 1 else "anual"

    # Verificar la clasificación de saltos por fila (Req 3.3) para el ramo SOAT.
    df_soat = df[df["ramo"] == "SOAT"].set_index("mes")
    assert set(df_soat.index) == set(meses_destino)
    for mes, tipo in tipos_esperados.items():
        assert df_soat.loc[mes, "tipo_salto"] == tipo

    # Δ esperados por columna consecutiva (mismos para saltos mensuales y anuales: el
    # reader deriva columna-contra-columna cronológica).
    delta_soat = _delta_consecutivo(saldo_soat)
    delta_rentas = [
        ds - di
        for ds, di in zip(
            _delta_consecutivo(saldo_pensiones), _delta_consecutivo(interes_rentas)
        )
    ]
    delta_capi = [
        ds - di
        for ds, di in zip(
            _delta_consecutivo(total_capi), _delta_consecutivo(interes_capi)
        )
    ]
    esperado_por_mes = {
        "SOAT": dict(zip(meses_destino, delta_soat)),
        "RENTAS VITALICIAS": dict(zip(meses_destino, delta_rentas)),
        RAMO_CAPITALIZADORA: dict(zip(meses_destino, delta_capi)),
    }

    # El reader deriva exactamente Δsaldo − Δinterés por ramo destino en cada salto.
    for ramo, por_mes in esperado_por_mes.items():
        filas = df[df["ramo"] == ramo].set_index("mes")["delta_reserva"]
        for mes, delta in por_mes.items():
            assert filas.loc[mes] == pytest.approx(delta)

    # ── 2) PERSISTOR: guardar de forma versionada ──
    n_filas = persistir_input_reservas(df)
    assert n_filas == len(df.index)

    # ── 3a) LOADER MENSUAL: mapa anclado por Mes_Caído a la fecha de inicio ──
    # La corrida arranca en el primer mes del insumo (jun 2026). Horizonte suficiente
    # para cubrir el año en curso; los diciembres siguientes caen fuera y se omiten
    # (offset > horizonte), lo que también ejercita la política Δ=0 (Req 10.1).
    fecha_inicio = date(_ANIO, _MES_INICIO, 1)
    meses_horizonte = 12
    mapa = cargar_mapa_reservas(str(db_tmp), fecha_inicio, meses_horizonte)

    # Mes_Caído: offset(M) = meses_entre(mes_inicio, M) + 1. Solo offsets en [1, horizonte].
    def _offset(mes_abs: str) -> int:
        anio_m, mes_m = int(mes_abs[:4]), int(mes_abs[5:7])
        return (anio_m - fecha_inicio.year) * 12 + (mes_m - fecha_inicio.month) + 1

    mapa_esperado: dict = {}
    for ramo, por_mes in esperado_por_mes.items():
        for mes_abs, delta in por_mes.items():
            off = _offset(mes_abs)
            if 1 <= off <= meses_horizonte:
                mapa_esperado.setdefault(ramo, {})[off] = pytest.approx(delta)

    assert set(mapa.keys()) == set(mapa_esperado.keys())
    for ramo, offsets_esp in mapa_esperado.items():
        assert mapa[ramo] == offsets_esp

    # La ruta mensual solo contiene los saltos MENSUALES del año en curso (jul..dic 2026,
    # anclados a offsets 2..7). Los diciembres siguientes (offsets 19, 31) quedan fuera.
    # Verificación explícita para SOAT: offsets exactos esperados.
    offsets_mensuales = {
        _offset(m): d
        for m, d in esperado_por_mes["SOAT"].items()
        if tipos_esperados[m] == "mensual"
    }
    assert set(mapa["SOAT"].keys()) == set(offsets_mensuales.keys())

    # ── 3b) LOADER ANUAL: {año: {ramo: delta}} para los saltos diciembre-a-diciembre ──
    anual = cargar_variacion_anual_por_ano(str(db_tmp))

    # Solo los años de los saltos ANUALES (destino diciembre): 2027 y 2028 (Req 8.1/9.3).
    anios_anuales_esperados = {
        int(m[:4]) for m, t in tipos_esperados.items() if t == "anual"
    }
    assert set(anual.keys()) == anios_anuales_esperados
    assert anios_anuales_esperados == set(_ANIOS_SIGUIENTES)

    # Cada año anual lleva la variación de su salto diciembre-a-diciembre por ramo, y los
    # años NO se confunden entre sí (Req 9.3): 2027 usa dic2026→dic2027, 2028 usa
    # dic2027→dic2028.
    for anio in anios_anuales_esperados:
        mes_abs = f"{anio:04d}-12"
        por_ramo = anual[anio]
        for ramo, por_mes in esperado_por_mes.items():
            assert por_ramo[ramo] == pytest.approx(por_mes[mes_abs])

    # El año en curso (2026) NO aparece en la ruta anual (sus saltos son mensuales), y un
    # año fuera del insumo (p. ej. 2030) tampoco: el motor asumiría Δ=0 (Req 10.1).
    assert _ANIO not in anual
    assert 2030 not in anual
