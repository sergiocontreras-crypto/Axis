# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 4.4 — Normalización de etiquetas insensible a ruido.

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py` (versión basada en `Encabezado_Fecha`, devuelve
`DataFrame[ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto]`).

Contrato (Req 13.2): el `Lector_Reservas` normaliza cada etiqueta de ramo (recorte de
espacios y mayúsculas, `strip`/`upper`) ANTES de aplicar los `Diccionarios_Mapeo`. Por
tanto, una etiqueta con ruido de espacios en blanco y mayúsculas/minúsculas
(p. ej. "  soat ", "SoAt") produce EXACTAMENTE el mismo ramo de portafolio de salida que
su forma canónica normalizada.

La prueba construye, en memoria, las dos hojas ("Saldo Reservas" e "Interés a la
Reserva") con encabezados de FECHA (layout `header=None`), colocando en la celda de la
etiqueta del ramo una variante con ruido. Para mantener el coste por ejemplo bajo
(≥100 iteraciones), las hojas se inyectan directamente al lector interceptando el LÍMITE
de lectura de archivo (`_validar_hojas` / `_leer_hoja_reservas`), sin serializar/parsear
un `.xlsx` en cada ejemplo. La lógica bajo prueba (localización por fecha, normalización
de etiquetas y aplicación del diccionario) se ejecuta REAL, sin mocks. El mapeo semilla lo
siembra `cargar_mapeos_reservas` sobre la DB temporal del `conftest`.
"""

import datetime as _dt
from unittest import mock

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path y redirige la DB a una
# temporal por prueba (el mapeo semilla se siembra ahí al leerlo).
import compras_ventas_reservas as _cvr
from compras_ventas_reservas import (
    _HOJA_INTERES,
    _HOJA_SALDO,
    _SEED_MAPEO_SALDO,
    _normalizar_ramo,
    derivar_variaciones_reservas,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# Dos encabezados de fecha consecutivos (salto de 1 mes = 'mensual'), suficientes para
# generar exactamente una variación por ramo. El eje temporal se deriva de las fechas.
_FECHA_1 = _dt.datetime(2026, 6, 1)
_FECHA_2 = _dt.datetime(2026, 7, 1)

# Etiquetas de saldo semilla con un destino de portafolio NO vacío (destino "" = ignorar).
# Se usan como forma canónica; la prueba les inyecta ruido de espacios/case.
_ETIQUETAS_SALDO_CANONICAS = tuple(
    sorted({origen for origen, destino in _SEED_MAPEO_SALDO if str(destino).strip()})
)


def _hoja_saldo(etiqueta_ramo: str, nivel_1: float, nivel_2: float) -> pd.DataFrame:
    """
    Construye la hoja "Saldo Reservas" con layout `header=None`:

      fila 0: [None, None,        FECHA_1, FECHA_2]   <- fila de encabezados de fecha
      fila 1: [None, "BOLIVAR",   None,    None   ]   <- cabecera de bloque (no es ramo)
      fila 2: [None, etiqueta,    nivel_1, nivel_2]   <- fila del ramo (con o sin ruido)

    La columna 1 es la columna-etiqueta (primera columna de texto a la izquierda de la
    primera fecha). El bloque BOLIVAR evita el descarte de ramos del bloque Capitalizadora.
    """
    return pd.DataFrame(
        [
            [None, None, _FECHA_1, _FECHA_2],
            [None, "BOLIVAR", None, None],
            [None, etiqueta_ramo, nivel_1, nivel_2],
        ]
    )


def _hoja_interes() -> pd.DataFrame:
    """
    Hoja "Interés a la Reserva" mínima con dos encabezados de fecha y sin filas de ramo
    mapeadas (todas se ignoran): el interés es 0 para todos los ramos y la variación se
    reduce a Δsaldo. Solo comparte el mismo eje de fechas (dos columnas) que el saldo.
    """
    return pd.DataFrame(
        [
            [None, None, _FECHA_1, _FECHA_2],
            [None, "BOLIVAR", None, None],
        ]
    )


def _derivar_para_etiqueta(
    etiqueta_ramo: str, nivel_1: float, nivel_2: float
) -> pd.DataFrame:
    """
    Deriva variaciones para una hoja de saldo cuya única fila de ramo lleva
    `etiqueta_ramo`, inyectando las hojas en memoria al lector.

    Se interceptan SOLO los límites de lectura de archivo (`_validar_hojas` y
    `_leer_hoja_reservas`) mediante context managers (compatibles con `@given`); toda la
    lógica de localización por `Encabezado_Fecha`, normalización de etiquetas y aplicación
    del diccionario se ejecuta REAL.
    """
    df_saldo = _hoja_saldo(etiqueta_ramo, nivel_1, nivel_2)
    df_interes = _hoja_interes()

    def _validar_ok(_origen):
        return None

    def _leer(_origen, nombre_hoja):
        if nombre_hoja == _HOJA_SALDO:
            return df_saldo.copy()
        if nombre_hoja == _HOJA_INTERES:
            return df_interes.copy()
        raise AssertionError(f"hoja inesperada: {nombre_hoja}")

    # Context managers en lugar del fixture `monkeypatch` (function-scoped), para que el
    # parcheo se aplique y revierta LIMPIAMENTE en cada ejemplo generado por `@given`.
    with mock.patch.object(_cvr, "_validar_hojas", _validar_ok), mock.patch.object(
        _cvr, "_leer_hoja_reservas", _leer
    ):
        # `ruta_excel_o_bytes` es irrelevante (las hojas se inyectan); `ramos_portafolio`
        # se conserva por firma pero se ignora (mapeo por diccionario).
        return derivar_variaciones_reservas(b"", set())


@st.composite
def etiqueta_con_ruido(draw):
    """
    Genera `(etiqueta_canonica, etiqueta_con_ruido)` a partir de una etiqueta semilla:

      - `etiqueta_canonica`: la etiqueta semilla tal cual (su forma en el Excel real).
      - `etiqueta_con_ruido`: la MISMA etiqueta con ruido de mayúsculas/minúsculas y
        espacios en blanco alrededor — pero SIEMPRE con la misma forma normalizada
        (`strip`/`upper`).

    Nota: `_normalizar_ramo` solo aplica `strip`/`upper`, por lo que el ruido se limita a
    espacios EXTERNOS (incluidos `\\t`/`\\n`, que `str.strip()` elimina) y a diferencias de
    case; los espacios internos no se tocan para preservar la clave normalizada.
    """
    canonica = draw(st.sampled_from(_ETIQUETAS_SALDO_CANONICAS))

    # Ruido de case: reescribir cada carácter con un patrón de case aleatorio.
    patron_case = draw(
        st.lists(st.booleans(), min_size=len(canonica), max_size=len(canonica))
    )
    con_case = "".join(
        ch.upper() if hacer_mayus else ch.lower()
        for ch, hacer_mayus in zip(canonica, patron_case)
    )

    # Ruido de espacios EXTERNOS (los internos no se tocan para no cambiar la clave).
    prefijo = draw(st.sampled_from(("", " ", "  ", "\t", "\n", " \t ", "   ")))
    sufijo = draw(st.sampled_from(("", " ", "  ", "\t", "\n", " \t", "  \n")))
    con_ruido = f"{prefijo}{con_case}{sufijo}"

    return canonica, con_ruido


# Feature: lectura-reservas-encabezados-fecha, Property 7: Normalización de etiquetas
# insensible a ruido. Para cualquier etiqueta de ramo con variaciones de espacios en
# blanco y mayúsculas/minúsculas, el Lector_Reservas produce el mismo ramo de salida que
# su forma canónica normalizada (strip/upper), antes de aplicar los Diccionarios_Mapeo.
# El `conftest` usa un fixture autouse (`db_tmp`) que a su vez recibe `monkeypatch`
# (function-scoped) para redirigir la DB a una temporal por prueba. Esa redirección es
# ESTABLE entre ejemplos (la ruta de la DB no cambia por ejemplo), por lo que se suprime
# el health check `function_scoped_fixture` de forma acotada.
@settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    etiquetas=etiqueta_con_ruido(),
    nivel_1=st.floats(
        min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
    ),
    nivel_2=st.floats(
        min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
    ),
)
def test_prop7_normalizacion_etiquetas_insensible_a_ruido(etiquetas, nivel_1, nivel_2):
    """
    **Property 7: Normalización de etiquetas insensible a ruido**

    **Validates: Requirements 13.2**

    La etiqueta canónica y su variante con ruido de espacios/case tienen la MISMA forma
    normalizada; por tanto el `Lector_Reservas` debe derivar EXACTAMENTE el mismo ramo de
    portafolio de salida (y el mismo delta) para ambas.
    """
    canonica, con_ruido = etiquetas

    # Precondición del generador: ambas comparten forma normalizada (strip/upper).
    assert _normalizar_ramo(canonica) == _normalizar_ramo(con_ruido)

    df_canonico = _derivar_para_etiqueta(canonica, nivel_1, nivel_2)
    df_ruido = _derivar_para_etiqueta(con_ruido, nivel_1, nivel_2)

    # El ramo de salida (destino del Diccionario_Mapeo) debe ser el mismo conjunto para
    # ambas entradas: la normalización ocurre antes del mapeo (Req 13.2).
    ramos_canonicos = set(df_canonico["ramo"].tolist())
    ramos_ruido = set(df_ruido["ramo"].tolist())
    assert ramos_ruido == ramos_canonicos

    # Y el delta derivado por ramo coincide (mismos niveles, misma normalización).
    resumen_canonico = (
        df_canonico.groupby("ramo")["delta_reserva"].sum().round(6).to_dict()
    )
    resumen_ruido = df_ruido.groupby("ramo")["delta_reserva"].sum().round(6).to_dict()
    assert resumen_ruido == resumen_canonico
