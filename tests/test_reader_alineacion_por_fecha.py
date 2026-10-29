"""
Corrección 1 — Alineación Saldo↔Interés por Mes_Absoluto (FECHA), no por posición.

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py`.

Contexto del bug corregido:
    El lector emparejaba las series de nivel de saldo e interés por POSICIÓN
    (índice cronológico) en cada hoja. El archivo real tiene ejes de fecha DISTINTOS
    por hoja (Saldo empieza 2026-06; Interés empieza 2026-07, ambos terminan con el
    patrón mixto de diciembres). Con emparejamiento posicional, el i-ésimo salto de
    saldo se restaba contra el i-ésimo salto de interés, que corresponde a un mes
    DISTINTO (M vs M+1) → resultado incorrecto para todo ramo con línea de interés.

Corrección: la hoja de Saldo es el eje temporal maestro. Para cada salto de saldo
    (mes_desde_date -> mes_hasta_date), el Δinterés se mide contra los niveles de
    interés EN ESAS MISMAS fechas; si el interés no tiene columna para una fecha, su
    nivel ahí es 0.0.

Estos tests son basados en ejemplos (pytest). El workbook se construye EN MEMORIA con
`pandas.ExcelWriter` (engine openpyxl) sobre un `BytesIO`. La DB usada por los mapeos
es la temporal por prueba (fixture autouse `db_tmp` de conftest.py); no se toca la
`portafolio.db` real.
"""

import datetime as _dt
import io

import pandas as pd
import pytest

# conftest.py añade la raíz del proyecto y scripts/ a sys.path.
from compras_ventas_reservas import (
    cargar_mapeos_reservas,
    derivar_variaciones_reservas,
)

RUTA = "ignorada-por-conftest"

# Etiqueta de saldo Bolívar con línea de interés homologable al mismo destino:
#   "PENSIONES LEY 100" (saldo) -> destino "RENTAS VITALICIAS"
#   "Rentas Vitalicias" (interés) -> mismo destino "RENTAS VITALICIAS"
_ETIQUETA_SALDO = "PENSIONES LEY 100"
_ETIQUETA_INTERES = "Rentas Vitalicias"
_RAMO_DESTINO = "RENTAS VITALICIAS"


def _construir_workbook_ejes_distintos(
    fechas_saldo: "list[_dt.date]",
    niveles_saldo: "list[float]",
    fechas_interes: "list[_dt.date]",
    niveles_interes: "list[float]",
) -> bytes:
    """
    Construye un workbook de dos hojas con EJES DE FECHA DISTINTOS por hoja.

    La hoja de Saldo lleva `fechas_saldo`/`niveles_saldo` para la etiqueta
    "PENSIONES LEY 100" (bloque BOLIVAR). La hoja de Interés lleva
    `fechas_interes`/`niveles_interes` para la línea "Rentas Vitalicias".

    Layout de fila: [col0 en blanco, col1 = etiqueta, col2.. = niveles por fecha].
    """
    def _fila(etiqueta, valores, n):
        fila = [None, etiqueta]
        fila += [None] * n if valores is None else list(valores)
        return fila

    n_s = len(fechas_saldo)
    n_i = len(fechas_interes)

    encabezado_saldo = _fila(None, [pd.Timestamp(f) for f in fechas_saldo], n_s)
    encabezado_interes = _fila(None, [pd.Timestamp(f) for f in fechas_interes], n_i)

    filas_saldo = [
        encabezado_saldo,
        _fila("BOLIVAR", None, n_s),
        _fila(_ETIQUETA_SALDO, niveles_saldo, n_s),
        _fila("TOTAL BOLIVAR", [0.0] * n_s, n_s),
        _fila("COMERCIALES", None, n_s),
        _fila("TOTAL COMERCIALES", [0.0] * n_s, n_s),
        _fila("CAPITALIZADORA", None, n_s),
        _fila("TOTAL CAPI", [0.0] * n_s, n_s),
        _fila("TOTAL COMPAÑIAS", [0.0] * n_s, n_s),
    ]
    df_saldo = pd.DataFrame(filas_saldo)

    filas_int = [
        encabezado_interes,
        _fila("Interés a la Reserva", None, n_i),
        _fila("Capitalizadora", [0.0] * n_i, n_i),
        _fila("Bolivar", None, n_i),  # sub-cabecera de sección
        _fila(_ETIQUETA_INTERES, niveles_interes, n_i),
        _fila("TOTAL COMPAÑIAS", [0.0] * n_i, n_i),
    ]
    df_int = pd.DataFrame(filas_int)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_saldo.to_excel(writer, sheet_name="Saldo Reservas", header=False, index=False)
        df_int.to_excel(writer, sheet_name="Interés a la Reserva", header=False, index=False)
    return buffer.getvalue()


def _delta_por_mes(df, ramo):
    """Devuelve {mes(str): delta_reserva} para un ramo dado."""
    filas = df[df["ramo"] == ramo]
    return {str(m): float(v) for m, v in zip(filas["mes"], filas["delta_reserva"])}


def test_alineacion_interes_por_fecha_no_posicional(db_tmp):
    """
    Ejes distintos: Saldo 2026-06..2026-09 (mensual); Interés 2026-07..2026-09.

    Para el salto de saldo 2026-08 -> 2026-09, Δinterés debe medirse en las fechas
    2026-08 y 2026-09 del INTERÉS (alineación por fecha), NO contra el salto de interés
    en la misma posición.

    Se comprueba además que el resultado por-fecha es distinto del que produciría el
    emparejamiento posicional viejo (prueba del bug).
    """
    # Verificar la premisa del mapeo (Fuente_Unica_De_Verdad en la DB temporal).
    mapeo_saldo, mapeo_interes = cargar_mapeos_reservas(RUTA)
    assert mapeo_saldo.get(_ETIQUETA_SALDO.upper()) == _RAMO_DESTINO
    assert mapeo_interes.get(_ETIQUETA_INTERES.upper()) == _RAMO_DESTINO

    fechas_saldo = [
        _dt.date(2026, 6, 1),
        _dt.date(2026, 7, 1),
        _dt.date(2026, 8, 1),
        _dt.date(2026, 9, 1),
    ]
    # Niveles de saldo por fecha: 2026-06=100, 07=130, 08=170, 09=220
    niveles_saldo = [100.0, 130.0, 170.0, 220.0]
    saldo_por_fecha = dict(zip(fechas_saldo, niveles_saldo))

    # Interés empieza un mes DESPUÉS (2026-07) y NO tiene 2026-06.
    fechas_interes = [
        _dt.date(2026, 7, 1),
        _dt.date(2026, 8, 1),
        _dt.date(2026, 9, 1),
    ]
    # Niveles de interés por fecha: 2026-07=10, 08=25, 09=60
    niveles_interes = [10.0, 25.0, 60.0]
    interes_por_fecha = dict(zip(fechas_interes, niveles_interes))

    wb = _construir_workbook_ejes_distintos(
        fechas_saldo, niveles_saldo, fechas_interes, niveles_interes
    )
    df = derivar_variaciones_reservas(wb, None)
    obtenido = _delta_por_mes(df, _RAMO_DESTINO)

    # Saltos de saldo (mensuales): 06->07, 07->08, 08->09. Mes destino en "YYYY-MM".
    saltos = [
        (_dt.date(2026, 6, 1), _dt.date(2026, 7, 1)),
        (_dt.date(2026, 7, 1), _dt.date(2026, 8, 1)),
        (_dt.date(2026, 8, 1), _dt.date(2026, 9, 1)),
    ]

    for mes_desde, mes_hasta in saltos:
        clave = f"{mes_hasta.year:04d}-{mes_hasta.month:02d}"
        # CORRECTO (por fecha): Δinterés medido en mes_desde/mes_hasta del propio interés,
        # con nivel 0.0 donde el interés no tiene esa fecha (p. ej. 2026-06).
        d_saldo = saldo_por_fecha[mes_hasta] - saldo_por_fecha[mes_desde]
        d_interes = interes_por_fecha.get(mes_hasta, 0.0) - interes_por_fecha.get(
            mes_desde, 0.0
        )
        esperado = d_saldo - d_interes
        assert clave in obtenido, f"Falta la fila del mes {clave}."
        assert obtenido[clave] == pytest.approx(esperado), (
            f"Δ por fecha incorrecto para {clave}: obtenido {obtenido[clave]}, "
            f"esperado {esperado}."
        )

    # ── Prueba del bug: el emparejamiento POSICIONAL daría otro número ──
    # Posicional: salto i del saldo usa posiciones i, i+1 de la serie de interés.
    # Para el 2º salto de saldo (07->08, i=1) el interés posicional usaría interes[1]
    # e interes[2] (que son 2026-08 y 2026-09), es decir 25-10=15 en vez del correcto
    # 25-10=15... construimos el caso donde difieran claramente:
    clave_08 = "2026-08"  # salto 07->08 (i=1)
    # Correcto (fecha): interés en 2026-07=10 y 2026-08=25 -> Δinterés=15.
    correcto_08 = (170.0 - 130.0) - (25.0 - 10.0)
    # Posicional (viejo): interés en posición 1 y 2 -> niveles_interes[1]=25,
    # niveles_interes[2]=60 -> Δinterés=35, dando un delta DISTINTO.
    posicional_08 = (170.0 - 130.0) - (60.0 - 25.0)
    assert correcto_08 != posicional_08, "El caso debe distinguir fecha vs posición."
    assert obtenido[clave_08] == pytest.approx(correcto_08)
    assert obtenido[clave_08] != pytest.approx(posicional_08)


def test_alineacion_por_fecha_patron_mixto_con_diciembres(db_tmp):
    """
    Patrón mixto real: Saldo 2026-06..2026-12, 2027-12, 2028-12; Interés
    2026-07..2026-12, 2027-12, 2028-12 (empieza un mes después, mismo final).

    El salto ANUAL 2026-12 -> 2027-12 del saldo debe restar el Δinterés medido en
    2026-12 y 2027-12 del interés (que SÍ existen), alineado por fecha.
    """
    fechas_saldo = [
        _dt.date(2026, 6, 1),
        _dt.date(2026, 7, 1),
        _dt.date(2026, 8, 1),
        _dt.date(2026, 9, 1),
        _dt.date(2026, 10, 1),
        _dt.date(2026, 11, 1),
        _dt.date(2026, 12, 1),
        _dt.date(2027, 12, 1),
        _dt.date(2028, 12, 1),
    ]
    niveles_saldo = [100.0 + 25.0 * i for i in range(len(fechas_saldo))]
    saldo_por_fecha = dict(zip(fechas_saldo, niveles_saldo))

    fechas_interes = [
        _dt.date(2026, 7, 1),
        _dt.date(2026, 8, 1),
        _dt.date(2026, 9, 1),
        _dt.date(2026, 10, 1),
        _dt.date(2026, 11, 1),
        _dt.date(2026, 12, 1),
        _dt.date(2027, 12, 1),
        _dt.date(2028, 12, 1),
    ]
    niveles_interes = [5.0 + 7.0 * i for i in range(len(fechas_interes))]
    interes_por_fecha = dict(zip(fechas_interes, niveles_interes))

    wb = _construir_workbook_ejes_distintos(
        fechas_saldo, niveles_saldo, fechas_interes, niveles_interes
    )
    df = derivar_variaciones_reservas(wb, None)
    obtenido = _delta_por_mes(df, _RAMO_DESTINO)

    # El primer salto (2026-06 -> 2026-07): interés no tiene 2026-06 -> nivel 0.0.
    d_saldo_1 = saldo_por_fecha[_dt.date(2026, 7, 1)] - saldo_por_fecha[_dt.date(2026, 6, 1)]
    d_interes_1 = interes_por_fecha.get(_dt.date(2026, 7, 1), 0.0) - interes_por_fecha.get(
        _dt.date(2026, 6, 1), 0.0
    )
    assert obtenido["2026-07"] == pytest.approx(d_saldo_1 - d_interes_1)

    # Salto ANUAL 2026-12 -> 2027-12: alineado por fecha en ambas hojas.
    d_saldo_a = saldo_por_fecha[_dt.date(2027, 12, 1)] - saldo_por_fecha[_dt.date(2026, 12, 1)]
    d_interes_a = interes_por_fecha[_dt.date(2027, 12, 1)] - interes_por_fecha[
        _dt.date(2026, 12, 1)
    ]
    assert obtenido["2027-12"] == pytest.approx(d_saldo_a - d_interes_a)

    # El tipo_salto de 2027-12 debe ser anual (Δ=12 meses).
    fila_anual = df[(df["ramo"] == _RAMO_DESTINO) & (df["mes"] == "2027-12")]
    assert len(fila_anual) == 1
    assert fila_anual["tipo_salto"].iloc[0] == "anual"
