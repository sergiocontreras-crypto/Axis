# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 4.6 — Inyectividad de meses absolutos para diciembres de años distintos
(porción del LECTOR / reader keys).

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py`, que lee un workbook de dos hojas con
`Encabezado_Fecha` (datetime) y devuelve un DataFrame con columnas
{ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto}.

Contrato bajo prueba (Req 5.3, 9.4 — Property 10, porción del lector):
  - Para un conjunto de columnas de diciembre de años DISTINTOS, cada diciembre debe
    producir una clave `mes` "YYYY-MM" DISTINTA en el DataFrame de salida
    ("2026-12", "2027-12", "2028-12", …), sin colisiones.

Restricción de formato del insumo (Req 3.4): dos columnas consecutivas solo pueden
diferir en 1 o 12 meses; de lo contrario el lector lanza `ErrorFormatoEncabezado`. Por
eso la secuencia de encabezados se construye como:
  - un bloque de meses contiguos del año en curso `Y` (m0, m0+1, …, diciembre de Y), y
  - luego SOLO los diciembres de años siguientes: dic(Y+1), dic(Y+2), …
Cada salto queda en {1, 12} meses, y aparecen múltiples columnas de diciembre de años
distintos (dic(Y), dic(Y+1), …) cuyas claves absolutas no deben colisionar.

Los workbooks se construyen EN MEMORIA (openpyxl vía pandas). La DB de mapeos es la
temporal (fixture autouse `db_tmp`), sembrada con los diccionarios semilla.
"""

import datetime as _dt
import io

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import derivar_variaciones_reservas

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre
# ejemplos, lo que se declara explícitamente como comportamiento esperado.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture]


def _fila(etiqueta, valores=None, n=None):
    """Fila del layout: [blanco, etiqueta, v0, v1, …]. `n` fija el ancho de datos."""
    fila = [None, etiqueta]
    if valores is None:
        fila += [None] * (n if n is not None else 0)
    else:
        fila += list(valores)
    return fila


def _construir_workbook_fechas(fechas, saldo_por_columna):
    """
    Construye un .xlsx en memoria con las hojas 'Saldo Reservas' e
    'Interés a la Reserva' cuyos ENCABEZADOS son FECHAS (datetime), replicando el
    layout: columna en blanco, columna-etiqueta, y N columnas de fecha.

    Args:
        fechas: lista de `datetime`/`date` de encabezado (una por columna de datos),
            en el orden en que aparecen en la hoja.
        saldo_por_columna: lista de N niveles de saldo para el ramo 'RENTAS VOLUNTARIAS'
            (una entrada por fecha). Se usa una única etiqueta de saldo simple que mapea
            a un ramo de portafolio, suficiente para ejercitar las claves de mes.

    Returns:
        bytes: contenido del workbook .xlsx.
    """
    n = len(fechas)
    encabezados = list(fechas)

    # ── Hoja Saldo Reservas ──
    # Fila 0: blanco (col A) + blanco (col etiqueta) + fila de fechas (encabezados).
    fila_encabezados_saldo = [None, None] + encabezados
    filas_saldo = [
        fila_encabezados_saldo,
        _fila("BOLIVAR", encabezados),  # sub-cabecera de sección (texto/fechas ruido)
        _fila("RENTAS VOLUNTARIAS", saldo_por_columna),
        _fila("TOTAL BOLIVAR", [0] * n),
        _fila("TOTAL COMPAÑIAS", [0] * n),
    ]
    df_saldo = pd.DataFrame(filas_saldo)

    # ── Hoja Interés a la Reserva ── (sin interés → variación = Δsaldo)
    fila_encabezados_int = [None, None] + encabezados
    filas_int = [
        fila_encabezados_int,
        _fila("Interés a la Reserva", encabezados),
        _fila("Capitalizadora", [0] * n),
        _fila("TOTAL COMPAÑIAS", [0] * n),
    ]
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


@st.composite
def secuencia_con_diciembres_distintos(draw):
    """
    Genera una secuencia de `Encabezado_Fecha` VÁLIDA (saltos ∈ {1, 12}) que contiene
    múltiples columnas de diciembre de años distintos.

    Dos variantes, ambas con saltos legales:
      - "mixto": meses contiguos del año en curso `Y` (desde `m0` hasta diciembre de Y),
        seguidos de SOLO los diciembres de años siguientes dic(Y+1..Y+k). Produce los
        diciembres dic(Y), dic(Y+1), …, dic(Y+k).
      - "solo_diciembres": diciembres consecutivos separados 12 meses:
        dic(Y), dic(Y+1), …, dic(Y+k).

    Returns:
        tuple[list[datetime], list[str]]: (fechas de encabezado, claves "YYYY-MM" de los
        diciembres presentes cuya destinación produce filas — es decir, todos los
        diciembres SALVO el primero de la serie, que no tiene columna anterior).

    Nota: la variación se deriva columna-contra-columna, por lo que solo las columnas con
    una columna cronológicamente anterior producen filas. En ambas variantes el primer
    diciembre destino de un salto es dic(Y) cuando hay meses previos del año en curso, o
    dic(Y+1) cuando la serie empieza directamente en diciembre.
    """
    variante = draw(st.sampled_from(("mixto", "solo_diciembres")))
    anio = draw(st.integers(min_value=2021, max_value=2035))
    # Número de diciembres de años siguientes (>=2 para asegurar varios diciembres).
    anios_siguientes = draw(st.integers(min_value=2, max_value=5))

    fechas: "list[_dt.datetime]" = []

    if variante == "mixto":
        mes_inicio = draw(st.integers(min_value=1, max_value=11))
        for mes in range(mes_inicio, 13):
            fechas.append(_dt.datetime(anio, mes, 1))
        for k in range(1, anios_siguientes + 1):
            fechas.append(_dt.datetime(anio + k, 12, 1))
    else:  # solo_diciembres
        for k in range(0, anios_siguientes + 1):
            fechas.append(_dt.datetime(anio + k, 12, 1))

    # Claves "YYYY-MM" de TODOS los diciembres presentes en la serie.
    claves_diciembre_todas = [
        f"{f.year:04d}-{f.month:02d}" for f in fechas if f.month == 12
    ]
    # El primer diciembre destino de un salto: si la serie empieza en diciembre
    # (variante solo_diciembres), el primer diciembre NO tiene columna anterior y no
    # produce fila; en la variante mixto, dic(Y) sí tiene un mes anterior (noviembre o
    # anterior) y produce fila.
    if variante == "solo_diciembres":
        claves_diciembre_esperadas = claves_diciembre_todas[1:]
    else:
        claves_diciembre_esperadas = claves_diciembre_todas

    return fechas, claves_diciembre_esperadas


# Feature: lectura-reservas-encabezados-fecha, Property 10: Inyectividad de meses
# absolutos para diciembres de años distintos. Para cualquier conjunto de columnas de
# diciembre pertenecientes a años distintos, el lector produce una clave `mes` "YYYY-MM"
# DISTINTA por cada diciembre ("2026-12", "2027-12", …), sin colisiones.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(datos=secuencia_con_diciembres_distintos())
def test_prop10_diciembres_distintos_claves_reader(db_tmp, datos):
    """
    **Property 10: Inyectividad de meses absolutos para diciembres de años distintos**
    (porción del lector / reader keys)

    **Validates: Requirements 5.3, 9.4**

    Genera columnas de diciembre de años distintos (en secuencias con saltos válidos de
    1 y/o 12 meses) y verifica que cada diciembre produce una clave `mes` "YYYY-MM"
    DISTINTA en el DataFrame de salida, de modo que múltiples diciembres nunca colisionan
    en la misma clave.
    """
    fechas, claves_diciembre_esperadas = datos

    # Saldos crecientes arbitrarios (una entrada por columna de fecha). No importan los
    # valores: el foco es la inyectividad de las claves de mes.
    saldo = [100.0 + 10.0 * i for i in range(len(fechas))]

    wb = _construir_workbook_fechas(fechas, saldo)
    df = derivar_variaciones_reservas(wb, None)

    # La columna `mes` está en formato absoluto "YYYY-MM" (Req 5.1/5.3).
    claves_mes = list(df["mes"].astype(str).unique())

    # Todas las claves de diciembre esperadas están presentes …
    claves_diciembre_en_salida = sorted(
        {c for c in claves_mes if c.endswith("-12")}
    )
    assert claves_diciembre_en_salida == sorted(set(claves_diciembre_esperadas))

    # … y son INYECTIVAS: tantas claves distintas de diciembre como diciembres distintos
    # (ningún par de diciembres de años distintos colisiona en la misma clave).
    assert len(claves_diciembre_en_salida) == len(set(claves_diciembre_esperadas))

    # Refuerzo de inyectividad a nivel de (ramo, mes): dentro de un mismo ramo, cada
    # diciembre aparece a lo sumo una vez (claves de mes únicas por ramo).
    for ramo, grupo in df.groupby("ramo"):
        meses_ramo = list(grupo["mes"].astype(str))
        diciembres_ramo = [m for m in meses_ramo if m.endswith("-12")]
        assert len(diciembres_ramo) == len(set(diciembres_ramo)), (
            f"diciembres duplicados para ramo {ramo!r}"
        )
