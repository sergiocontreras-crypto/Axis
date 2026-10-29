"""
Corrección 2 — La línea de saldo PRE-AGREGADA tiene precedencia sobre los individuos.

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py`.

Regla confirmada por el usuario (precedencia):
    Cuando la hoja de Saldo trae una fila cuya etiqueta ES un destino del mapeo
    (p. ej. "DEMAS RAMOS DE SEGUROS GENERALES") ADEMÁS de ramos individuales que
    mapean a ese mismo destino (TRANSPORTE, HOGAR, MANEJO, ...), se USA la línea
    pre-agregada y se IGNORAN los individuos de ese destino (evita doble conteo).

    Un ramo 1:1 (origen == destino, p. ej. "SOAT" -> "SOAT") no supersede nada por sí
    mismo. Cuando NO hay línea pre-agregada, los individuos se suman como antes.

Tests basados en ejemplos (pytest). Workbook en memoria con `pandas.ExcelWriter`
(engine openpyxl). Mapeos leídos de la DB temporal por prueba (fixture autouse
`db_tmp`). No se toca la `portafolio.db` real.
"""

import datetime as _dt
import io

import pandas as pd
import pytest

from compras_ventas_reservas import (
    cargar_mapeos_reservas,
    derivar_variaciones_reservas,
)

RUTA = "ignorada-por-conftest"

_DESTINO_AGRUPADO = "DEMAS RAMOS DE SEGUROS GENERALES"
# Individuos comerciales que mapean al destino agrupado (según _SEED_MAPEO_SALDO).
_INDIVIDUOS = ("TRANSPORTE", "HOGAR", "MANEJO")

# Dos fechas mensuales consecutivas (un único salto mensual).
_FECHAS = [_dt.date(2026, 6, 1), _dt.date(2026, 7, 1)]


def _construir_workbook_comerciales(
    comerciales_saldo: "dict[str, list[float]]",
) -> bytes:
    """
    Construye un workbook de dos hojas con `_FECHAS` como encabezados de fecha. Todas
    las etiquetas dadas se colocan en el bloque COMERCIALES de la hoja de Saldo. La hoja
    de Interés no tiene líneas homologables a estos ramos (interés = 0 para el destino).
    """
    n = len(_FECHAS)

    def _fila(etiqueta, valores=None):
        fila = [None, etiqueta]
        fila += [None] * n if valores is None else list(valores)
        return fila

    encabezado = _fila(None, [pd.Timestamp(f) for f in _FECHAS])

    filas_saldo = [encabezado, _fila("BOLIVAR"), _fila("TOTAL BOLIVAR", [0.0] * n)]
    filas_saldo.append(_fila("COMERCIALES"))
    for etiqueta, vals in comerciales_saldo.items():
        filas_saldo.append(_fila(etiqueta, vals))
    filas_saldo.append(_fila("TOTAL COMERCIALES", [0.0] * n))
    filas_saldo.append(_fila("CAPITALIZADORA"))
    filas_saldo.append(_fila("TOTAL CAPI", [0.0] * n))
    filas_saldo.append(_fila("TOTAL COMPAÑIAS", [0.0] * n))
    df_saldo = pd.DataFrame(filas_saldo)

    filas_int = [
        encabezado,
        _fila("Interés a la Reserva"),
        _fila("Capitalizadora", [0.0] * n),
        _fila("Bolivar"),
        _fila("TOTAL COMPAÑIAS", [0.0] * n),
    ]
    df_int = pd.DataFrame(filas_int)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_saldo.to_excel(writer, sheet_name="Saldo Reservas", header=False, index=False)
        df_int.to_excel(writer, sheet_name="Interés a la Reserva", header=False, index=False)
    return buffer.getvalue()


def _delta_unico(df, ramo):
    """Delta del único salto (mensual) para el ramo. Falla si no hay exactamente 1 fila."""
    filas = df[df["ramo"] == ramo]
    assert len(filas) == 1, f"Se esperaba exactamente una fila para {ramo!r}, hay {len(filas)}."
    return float(filas["delta_reserva"].iloc[0])


def test_precedencia_linea_agrupada_ignora_individuos(db_tmp):
    """
    Con la línea pre-agregada "DEMAS RAMOS DE SEGUROS GENERALES" presente ADEMÁS de
    individuos (TRANSPORTE, HOGAR, MANEJO), el destino toma el Δ de la línea agregada y
    los individuos se IGNORAN (no hay doble conteo).
    """
    mapeo_saldo, _mapeo_interes = cargar_mapeos_reservas(RUTA)
    for ind in _INDIVIDUOS:
        assert mapeo_saldo.get(ind) == _DESTINO_AGRUPADO, (
            f"Premisa: {ind} debe mapear a {_DESTINO_AGRUPADO}."
        )

    # Línea pre-agregada: niveles [1000, 1500] -> Δ = 500.
    agregada = [1000.0, 1500.0]
    comerciales = {_DESTINO_AGRUPADO: agregada}
    # Individuos con valores grandes que, de sumarse, cambiarían el resultado.
    for ind in _INDIVIDUOS:
        comerciales[ind] = [200.0, 400.0]  # Δ individual = 200 c/u (sumarían 600)

    wb = _construir_workbook_comerciales(comerciales)
    df = derivar_variaciones_reservas(wb, None)

    obtenido = _delta_unico(df, _DESTINO_AGRUPADO)
    delta_agregada = agregada[1] - agregada[0]  # 500
    delta_si_sumara_individuos = delta_agregada + 3 * (400.0 - 200.0)  # 1100

    assert obtenido == pytest.approx(delta_agregada), (
        "El destino debe tomar el Δ de la línea PRE-AGREGADA."
    )
    assert obtenido != pytest.approx(delta_si_sumara_individuos), (
        "Los individuos no deben sumarse cuando existe la línea pre-agregada."
    )
    # Y no debe haberse contado agregada + individuos.
    assert obtenido != pytest.approx(delta_agregada + delta_si_sumara_individuos)


def test_control_solo_individuos_se_suman(db_tmp):
    """
    Caso de control: SIN línea pre-agregada, los individuos que mapean al destino
    agrupado se SUMAN como antes.
    """
    comerciales = {
        "TRANSPORTE": [100.0, 250.0],  # Δ = 150
        "HOGAR": [50.0, 90.0],        # Δ = 40
        "MANEJO": [10.0, 35.0],       # Δ = 25
    }
    wb = _construir_workbook_comerciales(comerciales)
    df = derivar_variaciones_reservas(wb, None)

    obtenido = _delta_unico(df, _DESTINO_AGRUPADO)
    esperado = (250.0 - 100.0) + (90.0 - 50.0) + (35.0 - 10.0)  # 215
    assert obtenido == pytest.approx(esperado), (
        "Sin línea pre-agregada, los individuos deben sumarse."
    )


def test_ramo_1a1_no_supersede(db_tmp):
    """
    Un ramo 1:1 (origen == destino, p. ej. SOAT -> SOAT) es un ramo normal: su Δ es el
    de su propia línea y no "supersede" nada. Coexiste con el destino agrupado sin
    afectarlo.
    """
    comerciales = {
        "SOAT": [300.0, 500.0],  # 1:1, Δ = 200
        # Individuos del destino agrupado (sin línea pre-agregada) -> se suman.
        "TRANSPORTE": [100.0, 160.0],  # Δ = 60
        "HOGAR": [10.0, 50.0],         # Δ = 40
    }
    wb = _construir_workbook_comerciales(comerciales)
    df = derivar_variaciones_reservas(wb, None)

    assert _delta_unico(df, "SOAT") == pytest.approx(200.0)
    assert _delta_unico(df, _DESTINO_AGRUPADO) == pytest.approx(60.0 + 40.0)
