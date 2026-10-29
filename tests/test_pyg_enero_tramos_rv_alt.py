"""
Bugfix: P&G de enero en tramos RV/Alt (``modo_proyeccion = "por_tramos"``).

Spec: ``.kiro/specs/pyg-enero-tramos-rv-alt``.

Este módulo contiene la **prueba de la condición del bug** (Task 1 del plan),
**reconectada al código de producción** tras implementar el fix (Task 3.1).

Metodología exploratoria de bugfix y reconexión al fix
------------------------------------------------------
- La prueba codifica el comportamiento ESPERADO *post-fix* (`F'`) descrito en la
  Propiedad 1 del diseño.
- Originalmente (fase de exploración, Task 1) la serie se aseveraba sobre el
  *stub* buggy ``F`` directamente, de modo que FALLABA — y ese fallo CONFIRMABA
  la fila fantasma de enero (``pyg = 0`` y ``VM(ene) = VM_cierre_previo``) en los
  tramos ``indice > 0``.
- Tras el fix (Task 3.1), la prueba se **reconecta a la lógica corregida real**:
  se aplica el helper de PRODUCCIÓN ``app_dash._realinear_pyg_enero_tramo`` al
  DataFrame del *stub* (``F``) para obtener ``F'`` y recién entonces se asevera la
  Propiedad 1. Así el test ejercita el fix real (no una referencia) y PASA cuando
  el bug está corregido (Task 3.2).

La serie mensual de un tramo se modela con un *stub determinista*
(`construir_serie_tramo_motor`) que replica EXACTAMENTE el patrón de salida de
los motores ``proyectar_bloque`` (RV, ``scripts/proyeccion_renta_variable.py``) y
``proyectar_bloque_ipc_spread`` (Alt FCP 4G, ``scripts/proyeccion_alternativos.py``):

  - una fila por posición y por ``fecha_proyeccion`` (periodos ene..dic),
  - la fila ``i == 0`` (enero, el primer mes del tramo) es la **fila de apertura
    fantasma**: ``pyg_mensual = 0.0`` y ``valor_mercado = VM_cierre_previo``,
  - los pasos ``i > 0`` capitalizan por días reales (base 365), de modo que el
    retorno real de enero queda mal etiquetado como febrero.

Ese ``F`` es la ENTRADA que recibe el helper de producción; el realineamiento
(``F'``) es lo que se asevera. Esto ejercita el fix sin depender de la base de
datos real.

**Property 1: Bug Condition / Expected Behavior** — Distribución mensual correcta
en tramos ``indice > 0``.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4**
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ``tests/conftest.py`` ya inserta la raíz del proyecto y ``scripts/`` en sys.path.
from proyeccion_renta_variable import generar_periodos

# Import del CÓDIGO DE PRODUCCIÓN real (el fix de la Task 3.1). Importar app_dash
# NO levanta el server (``app.run`` vive bajo ``if __name__ == '__main__'``), pero
# sí construye el objeto Dash (coste de import aceptable). La raíz del proyecto ya
# está en sys.path vía conftest; se refuerza de forma idempotente por si el módulo
# se invoca aislado. Si la importación fallara, los tests se omiten con un motivo
# claro (nunca se arranca el server).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from app_dash import _realinear_pyg_enero_tramo  # noqa: E402
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - defensivo
    _realinear_pyg_enero_tramo = None  # type: ignore
    _IMPORT_ERROR = exc

pytestmark = pytest.mark.skipif(
    _realinear_pyg_enero_tramo is None,
    reason=f"No se pudo importar app_dash._realinear_pyg_enero_tramo: {_IMPORT_ERROR!r}",
)

# Series no-RF afectadas por el bug.
SERIES_NO_RF = ("renta_variable", "alternativos")


# ─────────────────────────────────────────────────────────────────────────────
# Stub determinista: replica el patrón de salida de los motores no-RF
# (proyectar_bloque / proyectar_bloque_ipc_spread) para un tramo.
# ─────────────────────────────────────────────────────────────────────────────
def _tasa_ea_a_diaria(tasa_ea: float) -> float:
    """(1 + EA) ** (1/365) - 1, igual que ``convertir_ea_a_diaria`` del proyecto."""
    return (1.0 + tasa_ea) ** (1.0 / 365.0) - 1.0


def construir_serie_tramo_motor(
    vm_cierre_previo: float,
    tasa_ea: float,
    fecha_inicio: str,
    fecha_fin: str,
    col_id: str = "titulo",
    id_valor: str = "POS",
) -> pd.DataFrame:
    """
    Reproduce el DataFrame de salida del motor no-RF (``F``, buggy en
    distribución) para UNA posición de un tramo.

    Replica fielmente la rama ``if i == 0`` de ``proyectar_bloque`` /
    ``proyectar_bloque_ipc_spread``:

      - ``i == 0`` (enero): fila de apertura fantasma con ``pyg_mensual = 0.0`` y
        ``valor_mercado = vm_cierre_previo`` (= VM inicial del tramo, que al estar
        encadenado iguala el cierre de diciembre del tramo previo).
      - ``i > 0``: capitaliza ``valor_anterior * (1 + tasa_diaria) ** dias`` por
        días reales entre cortes; ``pyg_mensual = VM_i - VM_{i-1}``.

    Args:
        vm_cierre_previo: VM de cierre (31-dic) del tramo previo = VM inicial del
            tramo actual (encadenado). > 0.
        tasa_ea: retorno efectivo anual aplicado en el tramo.
        fecha_inicio: "YYYY-MM" del primer mes del tramo (p. ej. "2027-01").
        fecha_fin: "YYYY-MM" del último mes del tramo (p. ej. "2027-12").
        col_id: nombre de la columna identificadora ("titulo" para RV,
            "id_posicion" para Alt).
        id_valor: valor del identificador de la posición.

    Returns:
        DataFrame con columnas: ``fecha_proyeccion`` (str "YYYY-MM-DD"),
        ``valor_mercado``, ``pyg_mensual``, ``pyg_acumulado`` y ``col_id``.
    """
    periodos: list[date] = generar_periodos(fecha_inicio, fecha_fin)
    tasa_diaria = _tasa_ea_a_diaria(tasa_ea)

    filas: list[dict] = []
    valor_anterior = float(vm_cierre_previo)
    pyg_acumulado = 0.0

    for i, periodo in enumerate(periodos):
        fecha_str = periodo.strftime("%Y-%m-%d")
        if i == 0:
            # Fila de apertura fantasma (patrón del bug).
            filas.append(
                {
                    col_id: id_valor,
                    "fecha_proyeccion": fecha_str,
                    "valor_mercado": round(valor_anterior, 2),
                    "pyg_mensual": 0.0,
                    "pyg_acumulado": 0.0,
                }
            )
            continue

        dias = (periodo - periodos[i - 1]).days
        factor = (1.0 + tasa_diaria) ** dias
        valor_actual = valor_anterior * factor
        pyg_mes = valor_actual - valor_anterior
        pyg_acumulado += pyg_mes
        filas.append(
            {
                col_id: id_valor,
                "fecha_proyeccion": fecha_str,
                "valor_mercado": round(valor_actual, 2),
                "pyg_mensual": round(pyg_mes, 2),
                "pyg_acumulado": round(pyg_acumulado, 2),
            }
        )
        valor_anterior = valor_actual

    return pd.DataFrame(filas)


def _serie_ordenada(serie: pd.DataFrame) -> pd.DataFrame:
    """Serie ordenada ascendente por ``fecha_proyeccion`` (ene..dic)."""
    return serie.sort_values("fecha_proyeccion").reset_index(drop=True)


def _primer_mes(serie: pd.DataFrame) -> pd.Series:
    """Fila del corte con menor ``fecha_proyeccion`` (enero del tramo)."""
    return _serie_ordenada(serie).iloc[0]


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Bug Condition — Distribución mensual correcta en tramos indice > 0
#
# Codifica el comportamiento ESPERADO (post-fix, F'):
#   - pyg(ene) ≈ VM(ene) − VM_cierre_previo  (P&G real de enero, != 0 con retorno)
#   - NOT (VM(ene) == VM_cierre_previo AND pyg(ene) == 0) cuando el retorno real
#     del mes es no nulo.
# Sobre el código SIN corregir (fila fantasma) estas aserciones FALLAN → el bug
# queda demostrado.
#
# Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4
# ─────────────────────────────────────────────────────────────────────────────

# Tolerancia relativa para la igualdad de P&G de enero.
_TOL_REL = 1e-6
# Tolerancia absoluta por centavos: el helper de producción redondea VM/pyg a 2
# decimales (consistente con los motores), por lo que `pyg'(ene) = round(VM_1,2) −
# round(VM_prev,2)` puede diferir del valor "exacto" `VM_1 − VM_prev` en hasta ~1
# centavo. Se admite esa diferencia de redondeo (no es un error de lógica).
_TOL_ABS_CENTAVOS = 0.02


def _vm_real_fin_enero(serie_buggy: pd.DataFrame) -> float:
    """
    VM real de cierre de enero (``VM_1`` del motor) tomado del *stub* buggy ``F``.

    En la salida del motor SIN corregir, el retorno real de enero queda etiquetado
    como el segundo corte (``i == 1``, "febrero"): su ``valor_mercado`` es el nivel
    que, tras el fix, debe quedar en enero. Se captura ANTES de realinear.
    """
    orden = _serie_ordenada(serie_buggy)
    assert len(orden) >= 2, "La serie del tramo debe tener al menos 2 cortes"
    return float(orden.iloc[1]["valor_mercado"])  # VM_1 del motor


def _afirmar_distribucion_enero_correcta(
    serie: pd.DataFrame, vm_cierre_previo: float, vm_real_fin_enero: float
) -> None:
    """
    Aserciones de la Propiedad 1 sobre un tramo ``indice > 0``, evaluadas sobre la
    serie YA realineada ``F'`` (salida del helper de producción).

    EXPECTED (post-fix, ``F'``): el primer mes (enero) lleva su **P&G real**
    respecto al cierre previo y NO es la fila de apertura fantasma.

    ``vm_real_fin_enero`` es el nivel esperado de VM al cierre de enero (``VM_1``
    del motor), capturado del *stub* buggy ``F`` ANTES de realinear (ver
    ``_vm_real_fin_enero``); el P&G esperado de enero es
    ``VM_1 − VM_cierre_previo``.

    Sobre ``F`` sin realinear estas aserciones FALLARÍAN (fila fantasma); sobre
    ``F'`` (fix aplicado) PASAN.
    """
    orden = _serie_ordenada(serie)
    m0 = orden.iloc[0]  # enero del tramo (primer corte)
    vm_m0 = float(m0["valor_mercado"])
    pyg_m0 = float(m0["pyg_mensual"])

    pyg_real_enero = vm_real_fin_enero - float(vm_cierre_previo)

    escala_vm = max(abs(vm_real_fin_enero), abs(vm_m0), 1.0)
    escala_pyg = max(abs(pyg_real_enero), abs(pyg_m0), 1.0)

    # (1) El VM al cierre de enero debe reflejar el retorno real (no duplicar el
    #     cierre previo). Tolerancia = max(relativa, centavos por redondeo del fix).
    assert abs(vm_m0 - vm_real_fin_enero) <= max(
        _TOL_REL * escala_vm, _TOL_ABS_CENTAVOS
    ), (
        f"VM(enero)={vm_m0} debería reflejar el retorno real "
        f"(VM esperado={vm_real_fin_enero}), no duplicar VM_cierre_previo="
        f"{vm_cierre_previo} (fecha={m0['fecha_proyeccion']})"
    )

    # (2) El P&G de enero debe ser el retorno real respecto al cierre previo.
    #     Tolerancia = max(relativa, centavos por redondeo del fix a 2 decimales).
    assert abs(pyg_m0 - pyg_real_enero) <= max(
        _TOL_REL * escala_pyg, _TOL_ABS_CENTAVOS
    ), (
        f"pyg(enero)={pyg_m0} debería ser el P&G real de enero="
        f"{pyg_real_enero} (= VM_real_fin_enero − VM_cierre_previo); "
        f"fecha={m0['fecha_proyeccion']}"
    )

    # (3) No debe existir la fila fantasma (VM(ene)==cierre previo y pyg==0)
    #     cuando el retorno real del mes es no nulo.
    retorno_real_no_nulo = abs(pyg_real_enero) > _TOL_REL * max(
        abs(float(vm_cierre_previo)), 1.0
    )
    if retorno_real_no_nulo:
        es_fila_fantasma = (
            abs(vm_m0 - float(vm_cierre_previo))
            <= _TOL_REL * max(abs(float(vm_cierre_previo)), 1.0)
        ) and (abs(pyg_m0) <= _TOL_REL * max(abs(float(vm_cierre_previo)), 1.0))
        assert not es_fila_fantasma, (
            "Fila fantasma detectada en enero: "
            f"VM(enero)={vm_m0} == VM_cierre_previo={vm_cierre_previo} y "
            f"pyg(enero)={pyg_m0} == 0, pese a que el retorno real de enero es "
            f"{pyg_real_enero} (fecha={m0['fecha_proyeccion']})"
        )


@settings(
    max_examples=200,
    deadline=None,
    # Se suprime `function_scoped_fixture` (el fixture autouse de conftest se recrea
    # por ejemplo) y `too_slow` porque la generación por ejemplo incluye la creación
    # de una BD SQLite temporal en un filesystem lento (Google Drive); el trabajo por
    # ejemplo es legítimo, no una ineficiencia de la estrategia. `deadline=None` evita
    # falsos negativos por latencia de I/O. Mismo criterio que
    # `tests/test_pyg_enero_preservacion.py`.
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    vm_cierre_previo=st.floats(
        min_value=1_000.0,
        max_value=1_000_000_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    # Retorno EA no nulo (positivo o negativo), evitando el 0 exacto para que el
    # retorno real del mes sea no nulo y la Propiedad 1 sea exigible.
    tasa_ea=st.one_of(
        st.floats(min_value=0.005, max_value=0.60, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-0.40, max_value=-0.005, allow_nan=False, allow_infinity=False),
    ),
    anio=st.integers(min_value=2027, max_value=2035),
    serie=st.sampled_from(SERIES_NO_RF),
)
def test_property1_bug_condition_enero_lleva_pyg_real(
    vm_cierre_previo, tasa_ea, anio, serie
):
    """
    Property 1 (Expected Behavior): para un tramo ``indice > 0`` (12 periodos
    ene..dic), tras aplicar el fix de producción
    ``_realinear_pyg_enero_tramo(..., aplicar=True)`` el primer mes (enero) SHALL
    llevar su P&G real ``VM(enero) − VM_cierre_previo`` y no una fila de apertura
    fantasma.

    La entrada es el *stub* buggy ``F`` (fila fantasma en enero); la aserción se
    hace sobre ``F'`` (resultado del helper de producción). PASA cuando el fix
    está implementado (Task 3.2).

    Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4
    """
    col_id = "titulo" if serie == "renta_variable" else "id_posicion"
    id_valor = "POS"
    df = construir_serie_tramo_motor(
        vm_cierre_previo=vm_cierre_previo,
        tasa_ea=tasa_ea,
        fecha_inicio=f"{anio}-01",
        fecha_fin=f"{anio}-12",
        col_id=col_id,
        id_valor=id_valor,
    )
    # VM esperado de cierre de enero (VM_1 del motor), capturado del stub F antes
    # de realinear.
    vm_fin_enero = _vm_real_fin_enero(df)
    # Reconexión al fix real: tramo indice > 0 ⇒ aplicar=True sobre el helper de
    # PRODUCCIÓN. Se asevera la Propiedad 1 sobre F' (no sobre el stub F).
    df_prima = _realinear_pyg_enero_tramo(
        df, {id_valor: vm_cierre_previo}, col_id=col_id, aplicar=True
    )
    _afirmar_distribucion_enero_correcta(df_prima, vm_cierre_previo, vm_fin_enero)


# ─────────────────────────────────────────────────────────────────────────────
# Anclas deterministas (datos reales verificados). Reconectadas al fix: aplican el
# helper de producción al stub F y aseveran el comportamiento post-fix (F').
#
# EXPECTED (post-fix, F'): pyg(AAAA-01) = VM(AAAA-01) − VM_cierre_previo != 0
#   (el enero deja de ser la fila fantasma pyg == 0 / VM == VM_cierre_previo).
#
# Nota: los VM de cierre previo son valores sintéticos razonables; las sumas
# anuales exactas (332.542.499,40 / 117.671.762,08 / 1.318.634,14) se anclan en
# las pruebas de preservación (Task 2), no aquí. Aquí lo relevante es el patrón
# de la distribución de enero.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "nombre, vm_cierre_previo, tasa_ea, anio, serie",
    [
        # RV local AO PROTECCION, tramo 2027.
        ("AO_PROTECCION_2027", 3_000_000_000.0, 0.1108, 2027, "renta_variable"),
        # RV internacional ACWV iShares, tramo 2027.
        ("ACWV_2027", 1_200_000_000.0, 0.0980, 2027, "renta_variable"),
        # Alternativos BROOKFIELD (IPC + spread), tramo 2027.
        ("BROOKFIELD_2027", 20_000_000.0, 0.0659, 2027, "alternativos"),
    ],
)
def test_property1_bug_condition_anclas_datos_reales(
    nombre, vm_cierre_previo, tasa_ea, anio, serie
):
    """
    Reproducción determinista de los casos reales verificados (AO PROTECCION,
    ACWV, BROOKFIELD) para el tramo 2027, reconectada al fix de producción.

    EXPECTED OUTCOME tras el fix (sobre ``F'``): PASA — ``pyg(2027-01) =
    VM(2027-01) − VM(2026-12) != 0`` (el enero deja de ser la fila fantasma
    ``pyg == 0`` / ``VM == VM(2026-12)``).

    Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4
    """
    col_id = "titulo" if serie == "renta_variable" else "id_posicion"
    df = construir_serie_tramo_motor(
        vm_cierre_previo=vm_cierre_previo,
        tasa_ea=tasa_ea,
        fecha_inicio=f"{anio}-01",
        fecha_fin=f"{anio}-12",
        col_id=col_id,
        id_valor=nombre,
    )

    # Diciembre del tramo debe estar presente (invariante de meses).
    meses = set(df["fecha_proyeccion"])
    assert f"{anio}-12-01" in meses, f"Diciembre {anio} ausente en {nombre}"

    # VM esperado de cierre de enero (VM_1 del motor), capturado del stub F antes
    # de realinear.
    vm_fin_enero = _vm_real_fin_enero(df)
    # Reconexión al fix real: tramo indice > 0 ⇒ aplicar=True.
    df_prima = _realinear_pyg_enero_tramo(
        df, {nombre: vm_cierre_previo}, col_id=col_id, aplicar=True
    )
    _afirmar_distribucion_enero_correcta(df_prima, vm_cierre_previo, vm_fin_enero)
