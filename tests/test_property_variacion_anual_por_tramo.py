# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 9.2 — Construcción de la variación anual por tramo desde los `Salto_Anual`.

Bloque de construcción bajo prueba:
  - `cargar_variacion_anual_por_ano(ruta_db)` en `scripts/compras_ventas_reservas.py`,
    que lee la `Versión_Carga` más reciente y devuelve, SOLO para los `Salto_Anual`
    (`tipo_salto == 'anual'`), la variación anual agrupada por el año del diciembre
    destino: `{ano_diciembre: {ramo: delta_anual}}` (Req 8.1, 8.2).
  - La lógica de asignación por tramo que arma el `Orquestador` en
    `_ejecutar_proyeccion_por_tramos` (`app_dash.py`):

        variacion_anual_por_tramo = [
            variacion_anual_por_ano.get(tramo.fecha_corte.year, {})
            for tramo in tramos
        ]

    Los tramos se delimitan con `calcular_tramos` de `proyeccion_tramos.py` (Req 8.4),
    de modo que la prueba ejercita la construcción con tramos REALES.

Contrato bajo prueba (Req 8.1, 8.2 — Property 15):
  - AGRUPACIÓN (Req 8.1): tras persistir un conjunto de `Salto_Anual` con diciembres de
    años DISTINTOS, `cargar_variacion_anual_por_ano` agrupa cada variación anual por el
    año de su diciembre destino ("YYYY-12" → año YYYY), sin colisiones entre años.
  - ASIGNACIÓN POR TRAMO (Req 8.2): cada tramo recibe la variación anual del año de su
    `fecha_corte` si ese año tiene un salto anual disponible, o `{}` (vacío) cuando el
    año carece de salto anual.

La DB es la temporal por prueba (fixture autouse `db_tmp` de conftest.py, que redirige
`compras_ventas_reservas.get_conn` a un archivo SQLite temporal). El persistidor crea la
tabla versionada de forma idempotente; no se toca la `portafolio.db` real.
"""

import math
from datetime import date

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
from compras_ventas_reservas import (
    cargar_variacion_anual_por_ano,
    persistir_input_reservas,
)
from proyeccion_tramos import calcular_tramos

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo reutiliza entre ejemplos.
# La DB temporal acumula versiones entre ejemplos, pero la lectura toma SIEMPRE la
# versión más reciente (MAX(fecha_carga)/MAX(version_id)), por lo que solo el ÚLTIMO
# `persistir_input_reservas` de cada ejemplo determina el resultado leído. Se suprime la
# health check correspondiente y `too_slow` (coste por estado acumulado, no por datos).
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


# ── Generadores ──

# Deltas anuales finitos (el cargador descarta no finitos; aquí generamos válidos).
_delta_strategy = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Años de diciembre en un rango realista (design.md: encabezados 2020–2040).
_anio_strategy = st.integers(min_value=2020, max_value=2040)

# Ramos: alfabeto pequeño para claves canónicas estables tras normalizar (strip/upper).
_ramo_strategy = st.sampled_from(("VIDA", "SALUD", "AUTOS", "HOGAR", "CAPITALIZADORA"))


@st.composite
def _saltos_anuales(draw):
    """
    Genera un conjunto de `Salto_Anual` como un mapa {anio_diciembre: {ramo: delta}}.

    Cada año de diciembre es DISTINTO (claves únicas de año) y para cada uno se genera un
    conjunto no vacío de (ramo, delta) con ramos únicos por año. Ese mapa es tanto el
    insumo a persistir (como filas 'YYYY-12', tipo_salto='anual') como el resultado
    ESPERADO de `cargar_variacion_anual_por_ano` (agrupación por año de diciembre).

    Returns:
        dict[int, dict[str, float]]: variación anual esperada por año de diciembre.
    """
    anios = draw(
        st.lists(_anio_strategy, min_size=1, max_size=6, unique=True)
    )
    esperado: dict[int, dict[str, float]] = {}
    for anio in anios:
        ramos = draw(
            st.lists(_ramo_strategy, min_size=1, max_size=5, unique=True)
        )
        por_ramo: dict[str, float] = {}
        for ramo in ramos:
            por_ramo[ramo] = draw(_delta_strategy)
        esperado[anio] = por_ramo
    return esperado


def _df_desde_saltos_anuales(esperado):
    """
    Construye el DataFrame de variación (columnas {ramo, mes, delta_reserva, tipo_salto})
    a persistir a partir del mapa esperado {anio: {ramo: delta}}.

    Cada (anio, ramo) produce una fila con `mes = "YYYY-12"` (los saltos anuales caen en
    diciembre) y `tipo_salto = "anual"`.
    """
    filas = []
    for anio, por_ramo in esperado.items():
        for ramo, delta in por_ramo.items():
            filas.append(
                {
                    "ramo": ramo,
                    "mes": f"{anio:04d}-12",
                    "delta_reserva": delta,
                    "tipo_salto": "anual",
                }
            )
    return pd.DataFrame(
        filas, columns=["ramo", "mes", "delta_reserva", "tipo_salto"]
    )


def _iguales_por_ramo(obtenido, esperado):
    """Compara dos mapas {ramo: delta} con tolerancia flotante en el delta."""
    if set(obtenido.keys()) != set(esperado.keys()):
        return False
    for ramo, delta_esp in esperado.items():
        if not math.isclose(obtenido[ramo], delta_esp, rel_tol=1e-9, abs_tol=1e-9):
            return False
    return True


# Feature: lectura-reservas-encabezados-fecha, Property 15: Construcción de la variación
# anual por tramo desde los Salto_Anual. Para cualquier conjunto de Salto_Anual
# derivados, el Orquestador agrupa la variación anual por el año de su diciembre destino
# y asigna a cada tramo la variación anual del año de su fecha de corte (o vacío si ese
# año no tiene salto anual).
@settings(max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB)
@given(
    esperado=_saltos_anuales(),
    horizonte_anios=st.integers(min_value=1, max_value=8),
)
def test_prop15_construccion_variacion_anual_por_tramo(
    db_tmp, esperado, horizonte_anios
):
    """
    **Property 15: Construcción de la variación anual por tramo desde los Salto_Anual**

    **Validates: Requirements 8.1, 8.2**

    Pasos:
      1. Persistir el conjunto de `Salto_Anual` (filas "YYYY-12", tipo_salto='anual')
         para años de diciembre DISTINTOS vía `persistir_input_reservas`.
      2. Llamar `cargar_variacion_anual_por_ano` y verificar que la AGRUPACIÓN por año de
         diciembre coincide exactamente con el mapa esperado (Req 8.1). No hay colisiones
         entre años distintos.
      3. Delimitar tramos REALES con `calcular_tramos` sobre un horizonte que ancla en un
         año base y abarca `horizonte_anios` años (para incluir años CON y SIN salto
         anual). Replicar la asignación del Orquestador:
             variacion_anual_por_tramo[i] = variacion_anual_por_ano.get(
                 tramos[i].fecha_corte.year, {}
             )
         y verificar que cada tramo recibe la variación anual del año de su `fecha_corte`
         o `{}` cuando ese año no tiene salto anual (Req 8.2).
    """
    # 1. Persistir los saltos anuales.
    df = _df_desde_saltos_anuales(esperado)
    n_persistidas = persistir_input_reservas(df)
    assert n_persistidas == sum(len(v) for v in esperado.values())

    # 2. AGRUPACIÓN por año de diciembre (Req 8.1). El cargador toma la versión más
    #    reciente, que es la recién persistida.
    variacion_anual_por_ano = cargar_variacion_anual_por_ano(str(db_tmp))

    assert set(variacion_anual_por_ano.keys()) == set(esperado.keys()), (
        "la agrupación por año de diciembre no coincide con los Salto_Anual persistidos"
    )
    for anio, por_ramo_esp in esperado.items():
        assert _iguales_por_ramo(variacion_anual_por_ano[anio], por_ramo_esp), (
            f"la variación anual agrupada para {anio} no coincide"
        )

    # 3. ASIGNACIÓN POR TRAMO (Req 8.2) con tramos REALES de `calcular_tramos`.
    #    Se ancla el horizonte al menor año con salto anual para maximizar la mezcla de
    #    tramos con y sin salto anual; el horizonte abarca `horizonte_anios` años.
    anio_base = min(esperado.keys())
    fecha_inicio = date(anio_base, 1, 15)
    fecha_fin = date(anio_base + horizonte_anios, 6, 30)
    tramos = calcular_tramos(fecha_inicio, fecha_fin)

    # Réplica EXACTA de la lógica del Orquestador (_ejecutar_proyeccion_por_tramos).
    variacion_anual_por_tramo = [
        variacion_anual_por_ano.get(tramo.fecha_corte.year, {})
        for tramo in tramos
    ]

    assert len(variacion_anual_por_tramo) == len(tramos)

    for tramo, asignada in zip(tramos, variacion_anual_por_tramo):
        anio_corte = tramo.fecha_corte.year
        if anio_corte in esperado:
            # El tramo de un año con Salto_Anual recibe su variación anual (Req 8.2).
            assert _iguales_por_ramo(asignada, esperado[anio_corte]), (
                f"el tramo con corte en {anio_corte} no recibió su variación anual"
            )
        else:
            # Un año sin Salto_Anual disponible produce {} (Req 8.2 / política Δ=0).
            assert asignada == {}, (
                f"el tramo con corte en {anio_corte} debería recibir {{}} (sin salto "
                f"anual) y recibió {asignada!r}"
            )
