# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 4.3 — Property 6: Variación como Δsaldo − Δinterés homologada por los
Diccionarios_Mapeo.

Función bajo prueba: `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
en `scripts/compras_ventas_reservas.py`, que ahora lee workbooks con ENCABEZADOS DE
FECHA (datetime) y devuelve un DataFrame con columnas
`{ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto}`.

Contrato (Req 3.5, 12.2):
  Para cualquier par de columnas consecutivas con niveles de saldo e interés por
  etiqueta, la variación derivada por cada ramo de portafolio es igual a
      (saldo_hasta − saldo_desde) − (interés_hasta − interés_desde)
  agregada según los `Diccionarios_Mapeo` (etiquetas al mismo destino se SUMAN; destino
  vacío se ignora), respecto de la columna cronológicamente anterior.

Los `Diccionarios_Mapeo` provienen de `cargar_mapeos_reservas` con la semilla por
defecto (conftest siembra una DB temporal al primer acceso). Se usan etiquetas presentes
en la semilla (p. ej. "SOAT", "AUTOMOVILES", "PENSIONES LEY 100"). La derivación esperada
se calcula reutilizando ESA MISMA semilla para no duplicar reglas de homologación.
"""

import datetime as _dt
import io

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    RAMO_CAPITALIZADORA,
    _normalizar_ramo,
    cargar_mapeos_reservas,
    derivar_variaciones_reservas,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# Health checks suprimidos:
#  - function_scoped_fixture: el fixture db_tmp es autouse y de ámbito de función;
#    Hypothesis lo advertiría al reutilizar la misma DB temporal entre ejemplos. La DB
#    solo se LEE (mapeos semilla), por lo que la reutilización es segura y determinista.
#  - too_slow: cada ejemplo hace un round-trip REAL de workbook Excel en memoria
#    (openpyxl write + pandas read), ~0.3 s por ejemplo. Con >= 100 ejemplos el tiempo
#    de ejecución es intrínsecamente alto y NO se debe a generación de datos costosa;
#    se suprime este health check tal como documenta Hypothesis para I/O legítimo por
#    ejemplo.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]

RUTA = "ignorada-por-conftest"

# Etiquetas de SALDO presentes en la semilla (_SEED_MAPEO_SALDO):
#   - Comerciales con destino propio (no colisionan): "SOAT", "AUTOMOVILES".
#   - Bolívar con destino propio y CON línea de interés homologable: "PENSIONES LEY 100"
#     (destino "RENTAS VITALICIAS"), cuyo interés es "Rentas Vitalicias".
#   - Varias etiquetas comerciales que SUMAN al mismo destino agregado
#     ("DEMAS RAMOS DE SEGUROS GENERALES"): "TRANSPORTE", "HOGAR", "MANEJO".
_ETIQUETAS_SALDO_COMERCIALES_PROPIAS = ("SOAT", "AUTOMOVILES")
_ETIQUETAS_SALDO_COMERCIALES_AGREGADAS = ("TRANSPORTE", "HOGAR", "MANEJO")
_ETIQUETA_SALDO_BOLIVAR_CON_INTERES = "PENSIONES LEY 100"
# Etiqueta de INTERÉS (formato interés) que homologa al MISMO destino que
# "PENSIONES LEY 100" ("RENTAS VITALICIAS").
_ETIQUETA_INTERES_RENTAS = "Rentas Vitalicias"

# Todas las etiquetas de saldo Bolívar/Comerciales usadas en el workbook.
_ETIQUETAS_SALDO = (
    (_ETIQUETA_SALDO_BOLIVAR_CON_INTERES,)
    + _ETIQUETAS_SALDO_COMERCIALES_PROPIAS
    + _ETIQUETAS_SALDO_COMERCIALES_AGREGADAS
)


def _construir_workbook_fechas(
    fechas: "list[_dt.date]",
    saldo_por_etiqueta: "dict[str, list[float]]",
    interes_por_etiqueta: "dict[str, list[float]]",
) -> bytes:
    """
    Construye un .xlsx en memoria con las hojas 'Saldo Reservas' e
    'Interés a la Reserva' usando ENCABEZADOS DE FECHA (datetime), replicando el layout
    por bloques (BOLIVAR / COMERCIALES / CAPITALIZADORA) que espera el lector.

    Layout de cada fila: [col0 en blanco, col1 = etiqueta, col2.. = niveles por columna
    de fecha]. La fila de encabezados coloca las fechas a partir de la col2 (>= 2 fechas
    para que `_localizar_columnas_fecha` la reconozca como fila de encabezados).

    - `fechas`: fechas (primer día de mes) EN ORDEN CRONOLÓGICO, una por columna de datos.
    - `saldo_por_etiqueta`: {etiqueta_saldo: [niveles por columna]} para Bolívar/Comerciales.
    - `interes_por_etiqueta`: {etiqueta_interes: [niveles por columna]} (hoja de interés).
    """
    n = len(fechas)

    def _fila(etiqueta, valores=None):
        fila = [None, etiqueta]
        fila += [None] * n if valores is None else list(valores)
        return fila

    # Fila de encabezados: fechas datetime a partir de la col2.
    fila_encabezados = _fila(None, [pd.Timestamp(f) for f in fechas])

    # ── Hoja Saldo Reservas ──
    # Se separan las etiquetas por bloque: PENSIONES LEY 100 va en BOLIVAR; las demás
    # (SOAT/AUTOMOVILES/TRANSPORTE/HOGAR/MANEJO) van en COMERCIALES.
    bolivar = {
        k: v for k, v in saldo_por_etiqueta.items()
        if k == _ETIQUETA_SALDO_BOLIVAR_CON_INTERES
    }
    comerciales = {
        k: v for k, v in saldo_por_etiqueta.items()
        if k != _ETIQUETA_SALDO_BOLIVAR_CON_INTERES
    }

    filas_saldo = [fila_encabezados, _fila("BOLIVAR")]
    for etiqueta, vals in bolivar.items():
        filas_saldo.append(_fila(etiqueta, vals))
    filas_saldo.append(_fila("TOTAL BOLIVAR", [0.0] * n))
    filas_saldo.append(_fila("COMERCIALES"))
    for etiqueta, vals in comerciales.items():
        filas_saldo.append(_fila(etiqueta, vals))
    filas_saldo.append(_fila("TOTAL COMERCIALES", [0.0] * n))
    # Bloque CAPITALIZADORA sin ramos individuales; TOTAL CAPI en cero (no se ejercita
    # aquí; su corrección se cubre en otros tests).
    filas_saldo.append(_fila("CAPITALIZADORA"))
    filas_saldo.append(_fila("TOTAL CAPI", [0.0] * n))
    filas_saldo.append(_fila("TOTAL COMPAÑIAS", [0.0] * n))
    df_saldo = pd.DataFrame(filas_saldo)

    # ── Hoja Interés a la Reserva ──
    filas_int = [fila_encabezados, _fila("Interés a la Reserva")]
    filas_int.append(_fila("Capitalizadora", [0.0] * n))
    filas_int.append(_fila("Bolivar"))  # sub-cabecera de sección
    for etiqueta, vals in interes_por_etiqueta.items():
        filas_int.append(_fila(etiqueta, vals))
    filas_int.append(_fila("TOTAL COMPAÑIAS", [0.0] * n))
    df_int = pd.DataFrame(filas_int)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_saldo.to_excel(writer, sheet_name="Saldo Reservas", header=False, index=False)
        df_int.to_excel(writer, sheet_name="Interés a la Reserva", header=False, index=False)
    return buffer.getvalue()


# Niveles finitos, en rango financiero razonable, con decimales.
_niveles_celda = st.floats(
    min_value=-1_000_000.0,
    max_value=1_000_000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Un par de niveles (columna origen, columna destino) por etiqueta.
_par_niveles = st.tuples(_niveles_celda, _niveles_celda)


def _caso_variacion():
    """
    Estrategia de un caso: dos columnas consecutivas (Salto_Mensual, 1 mes) con niveles
    `(origen, destino)` por cada etiqueta de saldo y una línea de interés homologable al
    destino "RENTAS VITALICIAS".

    Se construye con combinadores planos (`st.builds`/`st.fixed_dictionaries`) en vez de
    un `@st.composite` con bucles Python, para que la GENERACIÓN de entradas sea rápida
    (evita el HealthCheck de generación lenta): Hypothesis dibuja unos pocos valores
    escalares y la construcción del workbook ocurre DENTRO del test, no en la generación.

    Devuelve `(fechas, saldo_por_etiqueta, interes_por_etiqueta)` donde cada nivel es una
    lista `[origen, destino]` de dos columnas cronológicas consecutivas.
    """
    fechas = st.builds(
        lambda anio, mes: [_dt.date(anio, mes, 1), _dt.date(anio, mes + 1, 1)],
        st.integers(min_value=2021, max_value=2039),
        st.integers(min_value=1, max_value=11),  # mes+1 sigue en el mismo año
    )

    # {etiqueta_saldo: (origen, destino)} para todas las etiquetas de saldo usadas.
    saldos = st.fixed_dictionaries(
        {etiqueta: _par_niveles for etiqueta in _ETIQUETAS_SALDO}
    )
    # Una única línea de interés que homologa al mismo destino que PENSIONES LEY 100.
    intereses = st.fixed_dictionaries({_ETIQUETA_INTERES_RENTAS: _par_niveles})

    def _armar(fechas, saldos, intereses):
        saldo_por_etiqueta = {k: [v[0], v[1]] for k, v in saldos.items()}
        interes_por_etiqueta = {k: [v[0], v[1]] for k, v in intereses.items()}
        return fechas, saldo_por_etiqueta, interes_por_etiqueta

    return st.builds(_armar, fechas, saldos, intereses)


def _delta_esperado_por_ramo(
    saldo_por_etiqueta: "dict[str, list[float]]",
    interes_por_etiqueta: "dict[str, list[float]]",
    mapeo_saldo: "dict[str, str]",
    mapeo_interes: "dict[str, str]",
) -> "dict[str, float]":
    """
    Calcula la variación esperada por RAMO destino para el ÚNICO salto (dos columnas),
    homologando por los `Diccionarios_Mapeo`:

      Δsaldo[ramo]   = Σ_etiquetas→ramo (saldo_hasta − saldo_desde)
      Δinterés[ramo] = Σ_etiquetas→ramo (interés_hasta − interés_desde)
      variación[ramo] = Δsaldo[ramo] − Δinterés[ramo]

    Destino "" (vacío) se ignora. Es la MISMA regla de homologación que aplica el lector.
    """
    delta_saldo: "dict[str, float]" = {}
    for etiqueta, niveles in saldo_por_etiqueta.items():
        destino = mapeo_saldo.get(_normalizar_ramo(etiqueta))
        if not destino:  # None (ausente) o "" (ignorar)
            continue
        d = float(niveles[1]) - float(niveles[0])
        delta_saldo[destino] = delta_saldo.get(destino, 0.0) + d

    delta_interes: "dict[str, float]" = {}
    for etiqueta, niveles in interes_por_etiqueta.items():
        destino = mapeo_interes.get(_normalizar_ramo(etiqueta))
        if not destino:
            continue
        d = float(niveles[1]) - float(niveles[0])
        delta_interes[destino] = delta_interes.get(destino, 0.0) + d

    ramos = set(delta_saldo) | set(delta_interes)
    return {
        ramo: delta_saldo.get(ramo, 0.0) - delta_interes.get(ramo, 0.0)
        for ramo in ramos
    }


# Feature: lectura-reservas-encabezados-fecha, Property 6: Variación como Δsaldo −
# Δinterés homologada por los Diccionarios_Mapeo. Para cualquier par de columnas
# consecutivas con niveles de saldo e interés por etiqueta, la variación derivada por
# cada ramo de portafolio es igual a (saldo_hasta − saldo_desde) − (interés_hasta −
# interés_desde) agregada según los Diccionarios_Mapeo (etiquetas al mismo destino se
# suman; destino vacío se ignora), respecto de la columna cronológicamente anterior.
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(caso=_caso_variacion())
def test_prop6_variacion_delta_saldo_menos_interes_homologada(caso, db_tmp):
    """
    **Property 6: Variación como Δsaldo − Δinterés homologada por los Diccionarios_Mapeo**

    **Validates: Requirements 3.5, 12.2**

    Construye un workbook de dos columnas de fecha consecutivas (Salto_Mensual) con
    niveles por etiqueta de saldo/interés, deriva variaciones y comprueba que, por cada
    ramo de portafolio, `delta_reserva` es exactamente
    `(saldo_hasta − saldo_desde) − (interés_hasta − interés_desde)` agregado según los
    `Diccionarios_Mapeo` (suma de etiquetas al mismo destino; destino "" ignorado),
    respecto de la columna cronológicamente anterior.
    """
    fechas, saldo_por_etiqueta, interes_por_etiqueta = caso

    # Diccionarios_Mapeo semilla (Fuente_Unica_De_Verdad), leídos de la DB temporal.
    mapeo_saldo, mapeo_interes = cargar_mapeos_reservas(RUTA)

    esperado = _delta_esperado_por_ramo(
        saldo_por_etiqueta, interes_por_etiqueta, mapeo_saldo, mapeo_interes
    )

    wb = _construir_workbook_fechas(fechas, saldo_por_etiqueta, interes_por_etiqueta)
    df = derivar_variaciones_reservas(wb, None)

    # El único salto es mensual (1 mes) → mes destino = segunda fecha en "YYYY-MM".
    mes_destino = f"{fechas[1].year:04d}-{fechas[1].month:02d}"

    # Para cada ramo esperado (de los mapeados), el lector produce EXACTAMENTE una fila
    # con ese mes destino y su delta homologado. Capitalizadora sale en cero y no se
    # compara aquí (TOTAL CAPI e interés capi son 0).
    for ramo, delta_esperado in esperado.items():
        filas = df[(df["ramo"] == ramo) & (df["mes"] == mes_destino)]
        assert len(filas) == 1, f"Se esperaba una fila para el ramo {ramo!r}."
        obtenido = float(filas["delta_reserva"].iloc[0])
        assert obtenido == _aprox(delta_esperado), (
            f"Variación homologada incorrecta para el ramo {ramo!r}."
        )
        # El salto de un mes se clasifica como 'mensual'.
        assert filas["tipo_salto"].iloc[0] == "mensual"

    # El lector NO debe inventar ramos fuera de los homologados (más Capitalizadora, que
    # aquí es 0). Todo ramo != CAPITALIZADORA en la salida debe estar en `esperado`.
    ramos_salida = set(df[df["ramo"] != RAMO_CAPITALIZADORA]["ramo"])
    assert ramos_salida <= set(esperado), (
        "El lector produjo ramos no homologados por los Diccionarios_Mapeo."
    )


def _aprox(valor: float):
    """Tolerancia absoluta/relativa para comparaciones en coma flotante."""
    import pytest

    return pytest.approx(valor, rel=1e-9, abs=1e-6)
