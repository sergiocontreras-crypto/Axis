# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature lectura-reservas-encabezados-fecha.

Tarea 7.4 — Ramo/mes ausente se omite (el motor asume Δ=0).

Función bajo prueba: `cargar_mapa_reservas(ruta_db, fecha_inicio, meses_horizonte)` en
`scripts/compras_ventas_reservas.py`, que lee la `Versión_Carga` MÁS RECIENTE de
`input_variacion_reservas_versiones` y devuelve `{ramo: {mes_offset: delta}}` anclando
cada `Mes_Absoluto` a la `Fecha_Inicio_Proyeccion` con la convención `Mes_Caído`,
conservando solo los offsets dentro de `[1, meses_horizonte]`.

Contrato bajo prueba (Req 7.3, 10.1 — Property 14):
  - Cualquier ramo SIN variación persistida no aparece como clave del mapa devuelto.
  - Cualquier (ramo, mes) sin variación persistida no aparece en el dict interno; en
    particular, los meses que caen FUERA de `[1, meses_horizonte]` (offsets ≤ 0 o
    posteriores al horizonte, incluidos años del horizonte sin columna en el insumo) se
    OMITEN.
  - Por tanto, el mapa devuelto contiene EXACTAMENTE las entradas persistidas cuyo
    offset anclado cae dentro del horizonte; todo lo demás está ausente y el
    `Motor_Proyeccion` asume Δ=0.

La DB es la temporal por prueba (fixture autouse `db_tmp`). Se persiste con el
persistidor real (`persistir_input_reservas`) para ejercitar el camino de escritura
versionado, y luego se llama `cargar_mapa_reservas` sobre la versión más reciente.
"""

from datetime import date

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# conftest.py añade la raíz del proyecto y `scripts/` a sys.path.
import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    _mes_inicio_desde_fecha,
    _normalizar_ramo,
    cargar_mapa_reservas,
    persistir_input_reservas,
)

# El diseño exige un mínimo de 100 iteraciones por prueba de propiedad.
_MIN_EJEMPLOS = 100

# El fixture autouse `db_tmp` es function-scoped; Hypothesis lo REUTILIZA entre ejemplos.
# La DB temporal acumula versiones entre ejemplos, pero `cargar_mapa_reservas` selecciona
# SIEMPRE la versión más reciente (la que este ejemplo acaba de persistir), por lo que la
# reutilización no contamina la aserción. Se suprimen las health checks correspondientes.
_SUPPRESS_DB = [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]


def _mes_absoluto(anio: int, mes: int) -> str:
    """Formatea (año, mes) como `Mes_Absoluto` "YYYY-MM"."""
    return f"{anio:04d}-{mes:02d}"


@st.composite
def _escenario(draw):
    """
    Genera un escenario de omisión:

      - `fecha_inicio`: primer día de un mes calendario en un rango realista.
      - `meses_horizonte`: horizonte de proyección `>= 1`.
      - `entradas`: conjunto de (ramo, mes_absoluto) ÚNICAS a PERSISTIR, con delta finito.
        Las claves se derivan de índices enteros distintos para que la clave (ramo, mes)
        sea única por construcción (el persistidor lo exige) sin rechazos costosos.

    Los meses persistidos abarcan tanto offsets DENTRO como FUERA del horizonte (incluidos
    meses anteriores a la fecha de inicio y muy posteriores al horizonte) para ejercitar
    explícitamente la OMISIÓN por rango.

    Returns:
        tuple[date, int, list[tuple[str, str, float]]]:
            (fecha_inicio, meses_horizonte, entradas) donde cada entrada es
            (ramo, mes_absoluto "YYYY-MM", delta).
    """
    anio_inicio = draw(st.integers(min_value=2021, max_value=2035))
    mes_inicio = draw(st.integers(min_value=1, max_value=12))
    fecha_inicio = date(anio_inicio, mes_inicio, 1)
    meses_horizonte = draw(st.integers(min_value=1, max_value=36))

    # Índices distintos → ramos distintos y meses distintos por construcción.
    indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=120),
            min_size=0,
            max_size=8,
            unique=True,
        )
    )

    # Meses candidatos: se generan offsets calendario relativos a la fecha de inicio que
    # cubren un rango amplio (incluye offsets fuera del horizonte, tanto negativos como
    # muy posteriores), de modo que la propiedad de OMISIÓN por rango quede ejercitada.
    entradas = []
    for i in indices:
        # Desplazamiento en meses respecto al mes de inicio (puede ser negativo).
        desplazamiento = draw(st.integers(min_value=-6, max_value=48))
        total = (fecha_inicio.year * 12 + (fecha_inicio.month - 1)) + desplazamiento
        anio = total // 12
        mes = (total % 12) + 1
        delta = draw(
            st.floats(
                min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
            )
        )
        entradas.append((f"RAMO{i}", _mes_absoluto(anio, mes), delta))

    return fecha_inicio, meses_horizonte, entradas


def _offset_esperado(mes_absoluto: str, fecha_inicio: date) -> int:
    """
    Reproduce el anclaje `Mes_Caído` de `cargar_mapa_reservas`:

        offset = meses_entre(mes(fecha_inicio), M) + 1

    Returns:
        int: offset de proyección del `Mes_Absoluto` respecto a la fecha de inicio.
    """
    mes_inicio = _mes_inicio_desde_fecha(fecha_inicio)
    anio, mes = int(mes_absoluto[:4]), int(mes_absoluto[5:7])
    meses_entre = (anio - mes_inicio.year) * 12 + (mes - mes_inicio.month)
    return meses_entre + 1


def _reset_tabla_versiones():
    """
    Vacía la tabla versionada `input_variacion_reservas_versiones` si existe.

    El fixture autouse `db_tmp` es function-scoped y Hypothesis REUTILIZA la misma DB
    temporal entre ejemplos, acumulando versiones. Como `cargar_mapa_reservas` selecciona
    la versión MÁS RECIENTE, un ejemplo que persiste POCO (o nada) leería una versión de
    un ejemplo anterior, contaminando la aserción. Para aislar cada ejemplo se limpia la
    tabla al inicio, de modo que la versión más reciente corresponda EXACTAMENTE a las
    entradas de este ejemplo (o a ninguna, si `entradas` está vacío → mapa vacío).
    """
    conn = cvr.get_conn()
    try:
        existe = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = ?",
            ("input_variacion_reservas_versiones",),
        ).fetchone()
        if existe is not None:
            with conn:
                conn.execute("DELETE FROM input_variacion_reservas_versiones")
    finally:
        conn.close()


def _mapa_esperado(entradas, fecha_inicio, meses_horizonte):
    """
    Construye el mapa que DEBE devolver `cargar_mapa_reservas`: SOLO las entradas cuyo
    offset anclado cae dentro de `[1, meses_horizonte]`. Cualquier ramo/mes ausente o
    fuera de rango NO debe aparecer (Property 14).
    """
    esperado: dict[str, dict[int, float]] = {}
    for ramo, mes_abs, delta in entradas:
        offset = _offset_esperado(mes_abs, fecha_inicio)
        if offset < 1 or offset > meses_horizonte:
            continue
        esperado.setdefault(_normalizar_ramo(ramo), {})[offset] = float(delta)
    return esperado


# Feature: lectura-reservas-encabezados-fecha, Property 14: Ramo/mes ausente se omite
# (motor asume Δ=0). Para cualquier ramo o mes sin variación persistida (incluidos años
# del horizonte sin columna en el insumo), la clave correspondiente no aparece en el mapa
# devuelto por el Cargador_Mapa_Reservas, de modo que el Motor_Proyeccion asume Δ=0.
@settings(
    max_examples=_MIN_EJEMPLOS, deadline=None, suppress_health_check=_SUPPRESS_DB
)
@given(escenario=_escenario())
def test_prop14_ramo_mes_ausente_se_omite(db_tmp, escenario):
    """
    **Property 14: Ramo/mes ausente se omite (motor asume Δ=0)**

    **Validates: Requirements 7.3, 10.1**

    Persiste una versión con un conjunto conocido de entradas (ramo, "YYYY-MM") y llama a
    `cargar_mapa_reservas`. Verifica que:
      (a) el mapa devuelto contiene EXACTAMENTE las entradas persistidas cuyo offset
          anclado cae en `[1, meses_horizonte]`;
      (b) cualquier ramo NO persistido está ausente del mapa (no aparece como clave);
      (c) cualquier offset sin mes persistido (incluidos meses fuera del horizonte, es
          decir años del horizonte sin columna en el insumo) está ausente del dict
          interno; de modo que el motor asume Δ=0 para esos ramos/meses.
    """
    fecha_inicio, meses_horizonte, entradas = escenario

    # Aislar el ejemplo de versiones acumuladas por ejemplos previos (DB reutilizada):
    # así la versión más reciente refleja EXACTAMENTE las entradas de este ejemplo.
    _reset_tabla_versiones()

    # Persistir la versión con las entradas conocidas (si hay alguna).
    if entradas:
        df = pd.DataFrame(
            [
                {
                    "ramo": ramo,
                    "mes": mes_abs,
                    "delta_reserva": delta,
                    "tipo_salto": "mensual",
                }
                for (ramo, mes_abs, delta) in entradas
            ],
            columns=["ramo", "mes", "delta_reserva", "tipo_salto"],
        )
        persistir_input_reservas(df)

    mapa = cargar_mapa_reservas("ignorada", fecha_inicio, meses_horizonte)
    esperado = _mapa_esperado(entradas, fecha_inicio, meses_horizonte)

    # (a) El mapa devuelto coincide EXACTAMENTE con las entradas dentro del horizonte:
    #     ni de más (no inventa ramos/meses) ni de menos (no omite los persistidos válidos).
    assert set(mapa.keys()) == set(esperado.keys()), (
        "el mapa contiene ramos ausentes de la persistencia o le faltan ramos válidos"
    )
    for ramo, offsets_esp in esperado.items():
        assert set(mapa[ramo].keys()) == set(offsets_esp.keys()), (
            "el dict interno contiene offsets sin mes persistido o le faltan offsets"
        )
        for offset, delta_esp in offsets_esp.items():
            assert mapa[ramo][offset] == delta_esp

    # (b) Cualquier ramo NO persistido está AUSENTE del mapa.
    ramos_persistidos = {_normalizar_ramo(r) for (r, _m, _d) in entradas}
    for ramo in mapa:
        assert ramo in ramos_persistidos, (
            "el mapa incluye un ramo que nunca fue persistido"
        )

    # (c) Cualquier offset presente proviene de un mes persistido que cae en el horizonte;
    #     los meses fuera de [1, meses_horizonte] se omiten (Δ=0 implícito).
    offsets_validos_persistidos = set()
    for _ramo, mes_abs, _delta in entradas:
        offset = _offset_esperado(mes_abs, fecha_inicio)
        if 1 <= offset <= meses_horizonte:
            offsets_validos_persistidos.add((_normalizar_ramo(_ramo), offset))
    for ramo, offsets in mapa.items():
        for offset in offsets:
            assert 1 <= offset <= meses_horizonte, (
                "el mapa contiene un offset fuera del horizonte (debería omitirse)"
            )
            assert (ramo, offset) in offsets_validos_persistidos, (
                "el mapa contiene un (ramo, offset) sin mes persistido correspondiente"
            )
