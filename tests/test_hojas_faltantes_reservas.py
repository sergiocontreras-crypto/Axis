# -*- coding: utf-8 -*-
"""
Prueba unitaria (basada en ejemplos) para la feature
lectura-reservas-encabezados-fecha.

Tarea 4.7 — Error de hojas faltantes distinto del error de formato.

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes,
ramos_portafolio)` en `scripts/compras_ventas_reservas.py`, que valida PRIMERO
(vía `_validar_hojas`) que el workbook contenga las dos hojas requeridas
("Saldo Reservas" e "Interés a la Reserva") ANTES de localizar columnas. Si falta
alguna hoja, señala un error de HOJAS FALTANTES (`ErrorHojasFaltantes`) que es una
causa DISTINTA del error de formato de encabezado (`ErrorFormatoEncabezado`).

Se construyen workbooks EN MEMORIA (pandas `ExcelWriter` sobre `BytesIO`) que
omiten cada hoja requerida (solo "Saldo Reservas", solo "Interés a la Reserva", y
ninguna de las dos). En cada caso se espera `ErrorHojasFaltantes`.

Además se verifica la relación de tipos entre las excepciones de dominio:
  - `ErrorHojasFaltantes` NO es subclase de `ErrorFormatoEncabezado` (y viceversa):
    son tipos DISTINTOS (Req 2.4).
  - ambas siguen siendo subclases de `ErrorInputReservas` (retrocompatibilidad de
    captura, según la jerarquía de errores del diseño).
"""

import io

import pandas as pd
import pytest

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import (
    derivar_variaciones_reservas,
    ErrorFormatoEncabezado,
    ErrorHojasFaltantes,
    ErrorInputReservas,
    _HOJA_SALDO,
    _HOJA_INTERES,
)


def _hoja_datos_valida() -> pd.DataFrame:
    """
    Contenido mínimo de una hoja de reservas (con encabezados de fecha válidos), de
    forma que el único motivo posible de fallo sea la AUSENCIA de una hoja y no un
    formato de encabezado inválido. Se escribe con `header=False`/`index=False`
    para reflejar el layout real (una columna-etiqueta seguida de columnas de fecha).
    """
    encabezados = [
        "RAMO",
        pd.Timestamp("2026-06-01"),
        pd.Timestamp("2026-07-01"),
    ]
    fila_datos = ["VIDA", 100.0, 150.0]
    return pd.DataFrame([encabezados, fila_datos])


# Nombre de una hoja NO relacionada, usada para que el workbook siempre tenga al
# menos una hoja visible (openpyxl no permite guardar un libro sin hojas). Su
# presencia no debe evitar el ErrorHojasFaltantes: no es ni "Saldo Reservas" ni
# "Interés a la Reserva".
_HOJA_IRRELEVANTE = "Otra Hoja"


def _construir_workbook(nombres_hojas: list[str]) -> bytes:
    """
    Construye un workbook XLSX en memoria con exactamente las hojas indicadas
    (cada una con contenido válido) y devuelve sus bytes.

    Si no se pide ninguna hoja requerida, se agrega una hoja NO relacionada para
    que el libro tenga al menos una hoja visible (requisito de openpyxl). Esa hoja
    irrelevante no satisface la validación de hojas requeridas, por lo que el caso
    "faltan ambas" sigue disparando `ErrorHojasFaltantes`.
    """
    df = _hoja_datos_valida()
    hojas = list(nombres_hojas) if nombres_hojas else [_HOJA_IRRELEVANTE]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nombre in hojas:
            df.to_excel(writer, sheet_name=nombre, header=False, index=False)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("hojas_presentes", "descripcion"),
    [
        ([_HOJA_INTERES], "falta 'Saldo Reservas'"),
        ([_HOJA_SALDO], "falta 'Interés a la Reserva'"),
        ([], "faltan ambas hojas requeridas"),
    ],
)
def test_hojas_faltantes_lanza_error_hojas_faltantes(hojas_presentes, descripcion):
    """
    Para un workbook al que le falte "Saldo Reservas", "Interés a la Reserva", o
    ambas, `derivar_variaciones_reservas` lanza `ErrorHojasFaltantes` (Req 2.4).

    **Property 5: Hojas faltantes señalan un error distinto del de formato**

    **Validates: Requirements 2.4**
    """
    contenido = _construir_workbook(hojas_presentes)

    with pytest.raises(ErrorHojasFaltantes):
        # El segundo argumento se conserva por compatibilidad de firma y se ignora.
        derivar_variaciones_reservas(contenido, set())


def test_error_hojas_faltantes_no_se_confunde_con_error_formato():
    """
    El error de hojas faltantes NO se captura como un error de formato de encabezado:
    una hoja faltante NO debe producir (ni ser un subtipo de) `ErrorFormatoEncabezado`.

    **Property 5: Hojas faltantes señalan un error distinto del de formato**

    **Validates: Requirements 2.4**
    """
    # Falta "Saldo Reservas": debe lanzar ErrorHojasFaltantes, capturado por su tipo
    # específico, y NO por ErrorFormatoEncabezado.
    contenido = _construir_workbook([_HOJA_INTERES])

    with pytest.raises(ErrorHojasFaltantes) as info:
        derivar_variaciones_reservas(contenido, set())

    # La excepción concreta NO es una instancia de ErrorFormatoEncabezado.
    assert not isinstance(info.value, ErrorFormatoEncabezado)


def test_tipos_de_error_son_distintos_pero_comparten_base():
    """
    Contrato de tipos de las excepciones de dominio (Req 2.4):
      - `ErrorHojasFaltantes` y `ErrorFormatoEncabezado` son tipos DISTINTOS: ninguno
        es subclase del otro.
      - ambas permanecen como subclases de `ErrorInputReservas` (retrocompatibilidad
        de captura según la jerarquía de errores del diseño).

    **Property 5: Hojas faltantes señalan un error distinto del de formato**

    **Validates: Requirements 2.4**
    """
    # Distintos entre sí (ninguno hereda del otro).
    assert not issubclass(ErrorHojasFaltantes, ErrorFormatoEncabezado)
    assert not issubclass(ErrorFormatoEncabezado, ErrorHojasFaltantes)

    # Ambos comparten la base común de errores de input de reservas.
    assert issubclass(ErrorHojasFaltantes, ErrorInputReservas)
    assert issubclass(ErrorFormatoEncabezado, ErrorInputReservas)
