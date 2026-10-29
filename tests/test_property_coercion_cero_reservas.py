# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 4.5 — Coerción de valores no numéricos a cero (Property 8).

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py` (lectura por Encabezado_Fecha; devuelve un
DataFrame con columnas {ramo, mes "YYYY-MM", delta_reserva, tipo_salto}).

Contrato (Req 13.1):
  - Para cualquier celda de saldo o interés cuyo valor sea VACÍO o NO NUMÉRICO, el nivel
    usado por el lector para esa posición es `0.0` (coerción a numérico finito), sin
    abortar la derivación.

Estrategia de verificación:
  - Se construyen workbooks EN MEMORIA (openpyxl vía pandas) con encabezados de FECHA
    (dos columnas separadas 1 mes → Salto_Mensual).
  - Cada celda de saldo/interés se genera como un número finito O como un valor vacío/no
    numérico (cadena vacía o texto arbitrario).
  - Se compara la variación derivada contra un ORÁCULO que coerciona los vacíos/no
    numéricos a `0.0`. Como la variación mensual es `Δsaldo − Δinterés`, si los niveles
    no numéricos NO se trataran como 0.0 el resultado diferiría del oráculo.
  - La derivación debe COMPLETAR (no lanzar excepción) en todos los casos.

conftest.py añade la raíz del proyecto y `scripts/` a sys.path y redirige la DB a una
SQLite temporal por prueba; los diccionarios de mapeo se siembran al primer acceso.
"""

import datetime as _dt
import io

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from compras_ventas_reservas import derivar_variaciones_reservas

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El workbook se pasa como bytes; conftest ignora cualquier ruta y usa la DB temporal.
# Se suprime `too_slow`: cada ejemplo construye y relee un .xlsx (openpyxl) y siembra los
# mapeos en la DB temporal, lo que hace la generación legítimamente lenta; el diseño exige
# >= 100 iteraciones con `deadline=None`, no rapidez de generación.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


# ── Generadores de celdas ──
#
# Una celda "numérica" es un float finito. Una celda "no coercionable" es un valor
# vacío o no numérico que el lector debe tratar como 0.0 (Req 13.1): cadena vacía,
# texto libre, o None.
_celda_numerica = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)
_celda_no_numerica = st.sampled_from(["", "   ", "N/A", "n/d", "-", "texto", "abc", None])
# Mezcla: cada celda es numérica o no numérica (para ejercitar ambas ramas).
_celda_mixta = st.one_of(_celda_numerica, _celda_no_numerica)


def _coerce_cero(valor) -> float:
    """
    Oráculo de coerción: reproduce la semántica esperada del lector (Req 13.1).
    Un valor numérico finito conserva su valor; cualquier otro (vacío/no numérico/no
    finito) se coerciona a 0.0.
    """
    v = pd.to_numeric(pd.Series([valor]), errors="coerce").iloc[0]
    if pd.isna(v):
        return 0.0
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):  # NaN/inf defensivo
        return 0.0
    return v


def _construir_workbook_fecha(
    etq_saldo: str,
    saldo_c1,
    saldo_c2,
    *,
    etq_interes: "str | None" = None,
    interes_c1=None,
    interes_c2=None,
) -> bytes:
    """
    Construye un .xlsx EN MEMORIA con dos hojas ('Saldo Reservas' e 'Interés a la
    Reserva') y ENCABEZADOS DE FECHA: dos columnas de datos separadas 1 mes (Salto
    Mensual). Layout por fila: [columna en blanco, columna-etiqueta, col_fecha_1,
    col_fecha_2].

    - La fila 0 es el encabezado: en las columnas de datos van dos `datetime` (mes M y
      mes M+1); la columna-etiqueta lleva un rótulo de texto para que quede a la
      izquierda de la primera fecha.
    - `etq_saldo` es una etiqueta del FORMATO SALDOS que el diccionario mapea a un ramo.
    - Las celdas `saldo_c1`/`saldo_c2` (y opcionalmente `interes_c*`) pueden ser
      numéricas o vacías/no numéricas.

    Devuelve los bytes del workbook.
    """
    # Encabezados de fecha: dos meses consecutivos (Salto_Mensual). Se usa 'object'
    # como dtype para permitir mezclar datetimes, textos y números en la misma columna.
    fecha_1 = _dt.datetime(2026, 6, 1)
    fecha_2 = _dt.datetime(2026, 7, 1)

    # ── Hoja Saldo Reservas ──
    filas_saldo = [
        [None, "RESERVAS", fecha_1, fecha_2],   # fila de encabezados (2 celdas fecha)
        [None, "BOLIVAR", None, None],           # cabecera de bloque (se ignora)
        [None, etq_saldo, saldo_c1, saldo_c2],   # fila del ramo bajo prueba
    ]
    df_saldo = pd.DataFrame(filas_saldo, dtype=object)

    # ── Hoja Interés a la Reserva ──
    filas_int = [
        [None, "INTERES", fecha_1, fecha_2],     # fila de encabezados (2 celdas fecha)
        [None, "Bolivar", None, None],           # sub-cabecera de sección (se ignora)
    ]
    if etq_interes is not None:
        filas_int.append([None, etq_interes, interes_c1, interes_c2])
    df_int = pd.DataFrame(filas_int, dtype=object)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_saldo.to_excel(
            writer, sheet_name="Saldo Reservas", header=False, index=False
        )
        df_int.to_excel(
            writer, sheet_name="Interés a la Reserva", header=False, index=False
        )
    return buffer.getvalue()


def _delta_ramo(df: pd.DataFrame, ramo: str) -> "list[float]":
    """Serie de `delta_reserva` para un ramo, ordenada por `mes`."""
    filas = df[df["ramo"] == ramo].sort_values("mes")
    return [float(v) for v in filas["delta_reserva"].tolist()]


# Etiquetas de saldo del diccionario semilla que mapean a un ramo SIN línea de interés,
# de modo que la variación se reduce a Δsaldo y aísla la coerción del saldo.
_ETQ_SALDO_SIN_INTERES = st.sampled_from(
    ["SOAT", "AUTOMOVILES", "TERREMOTO", "RENTAS VOLUNTARIAS", "ACCIDENTES PERSONALES"]
)
_RAMO_DE = {
    "SOAT": "SOAT",
    "AUTOMOVILES": "AUTOMOVILES",
    "TERREMOTO": "TERREMOTO",
    "RENTAS VOLUNTARIAS": "RENTAS VOLUNTARIAS",
    "ACCIDENTES PERSONALES": "ACCIDENTES PERSONALES",
}


# Feature: lectura-reservas-encabezados-fecha, Property 8: Coerción de valores no
# numéricos a cero. Para cualquier celda de saldo cuyo valor sea vacío o no numérico, el
# nivel usado por el lector es 0.0 (coerción a numérico finito), sin abortar la
# derivación; la variación mensual derivada coincide con el oráculo que coerciona
# vacíos/no numéricos a 0.0.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    etq_saldo=_ETQ_SALDO_SIN_INTERES,
    saldo_c1=_celda_mixta,
    saldo_c2=_celda_mixta,
)
def test_prop8_coercion_saldo_no_numerico_a_cero(db_tmp, etq_saldo, saldo_c1, saldo_c2):
    """
    **Property 8: Coerción de valores no numéricos a cero**

    **Validates: Requirements 13.1**

    Con celdas de saldo mezcladas (numéricas y vacías/no numéricas) sobre un ramo sin
    interés, la derivación NO aborta y el nivel de cada celda no numérica se trata como
    0.0: la variación mensual coincide con `coerce(saldo_c2) − coerce(saldo_c1)`.
    """
    wb = _construir_workbook_fecha(etq_saldo, saldo_c1, saldo_c2)

    # La derivación debe completar sin lanzar excepción (Req 13.1: no abortar).
    df = derivar_variaciones_reservas(wb, None)

    ramo = _RAMO_DE[etq_saldo]
    deltas = _delta_ramo(df, ramo)

    # Un único salto (dos columnas → Salto_Mensual) ⇒ una sola variación.
    assert len(deltas) == 1

    # Igualdad aproximada: los floats se serializan y releen desde el .xlsx (openpyxl),
    # por lo que puede haber diferencia en el último dígito de la mantisa. Lo relevante
    # (Req 13.1) es que las celdas no numéricas se traten como 0.0, no la precisión
    # exacta del round-trip Excel.
    esperado = _coerce_cero(saldo_c2) - _coerce_cero(saldo_c1)
    assert deltas[0] == pytest.approx(esperado, rel=1e-9, abs=1e-9)


# Pares (etiqueta_saldo, etiqueta_interes, ramo_destino) del diccionario semilla donde el
# MISMO ramo de portafolio recibe saldo e interés. Permite ejercitar la coerción tanto
# en la hoja de saldo como en la de interés.
_PARES_SALDO_INTERES = st.sampled_from(
    [
        ("PENSIONES LEY 100", "Rentas Vitalicias", "RENTAS VITALICIAS"),
        ("VIDA INDIVIDUAL - MATEMÁTICA", "Vida Individual", "VIDA INDIVIDUAL-MATEMATICA"),
        (
            "PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA",
            "Invalidez y Sobrevivencia (Previsional)",
            "PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA",
        ),
    ]
)


# Feature: lectura-reservas-encabezados-fecha, Property 8: Coerción de valores no
# numéricos a cero. Para cualquier celda de saldo O de interés vacía/no numérica, el
# nivel usado es 0.0 sin abortar; la variación `Δsaldo − Δinterés` coincide con el
# oráculo que coerciona a 0.0 tanto los saldos como los intereses no numéricos.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    par=_PARES_SALDO_INTERES,
    saldo_c1=_celda_mixta,
    saldo_c2=_celda_mixta,
    interes_c1=_celda_mixta,
    interes_c2=_celda_mixta,
)
def test_prop8_coercion_saldo_e_interes_no_numericos_a_cero(
    db_tmp, par, saldo_c1, saldo_c2, interes_c1, interes_c2
):
    """
    **Property 8: Coerción de valores no numéricos a cero**

    **Validates: Requirements 13.1**

    Con celdas de saldo e interés mezcladas (numéricas y vacías/no numéricas) sobre un
    ramo que recibe ambas, la derivación NO aborta y cada celda no numérica se trata como
    0.0: la variación coincide con
    `(coerce(saldo_c2) − coerce(saldo_c1)) − (coerce(interes_c2) − coerce(interes_c1))`.
    """
    etq_saldo, etq_interes, ramo = par
    wb = _construir_workbook_fecha(
        etq_saldo,
        saldo_c1,
        saldo_c2,
        etq_interes=etq_interes,
        interes_c1=interes_c1,
        interes_c2=interes_c2,
    )

    # La derivación debe completar sin lanzar excepción (Req 13.1: no abortar).
    df = derivar_variaciones_reservas(wb, None)

    deltas = _delta_ramo(df, ramo)
    assert len(deltas) == 1

    delta_saldo = _coerce_cero(saldo_c2) - _coerce_cero(saldo_c1)
    delta_interes = _coerce_cero(interes_c2) - _coerce_cero(interes_c1)
    esperado = delta_saldo - delta_interes
    # Igualdad aproximada por el round-trip de floats a través del .xlsx (openpyxl).
    assert deltas[0] == pytest.approx(esperado, rel=1e-9, abs=1e-9)
