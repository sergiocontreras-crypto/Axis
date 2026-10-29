"""
Bugfix: P&G de enero en tramos RV/Alt (``modo_proyeccion = "por_tramos"``).

Spec: ``.kiro/specs/pyg-enero-tramos-rv-alt`` — **Task 3.2 (reinterpretada como
TEST DE INTEGRACIÓN END-TO-END, por decisión explícita del usuario)**.

Objetivo
--------
Demostrar que la **lógica corregida de producción** (el fix de la Task 3.1) elimina
de verdad la fila fantasma de enero y ubica el P&G en su mes correcto. A diferencia
de la prueba de exploración (``test_pyg_enero_tramos_rv_alt.py``), que usa un *stub*
local, y de la de preservación (``test_pyg_enero_preservacion.py``), que usa una
*referencia* de ``F'``, este módulo ejercita el **código real** importado desde
``app_dash.py``:

  - ``app_dash._realinear_pyg_enero_tramo`` — el helper de producción del fix.
  - ``app_dash._correr_rv_tramo`` — el adaptador real del orquestador por tramos.

Nivel de integración elegido y por qué
--------------------------------------
Se evaluaron los dos niveles descritos en la Task 3.2:

* **Nivel 1 (motores completos)**: invocar ``_correr_rv_tramo`` / ``_correr_alt_tramo``
  con un ``estado_previo`` que lleve ``vm_rv_por_posicion`` / ``vm_alt_por_posicion``.
  Se verificó en diagnóstico que estos adaptadores SÍ corren end-to-end sobre la BD
  real (``portafolio.db``, 526 posiciones RV). PERO el realineamiento depende de un
  ``VM_cierre_previo`` por posición que, en los datos reales, se agrupa por ``titulo``
  — y ``titulo`` NO es único (varias compañías comparten el mismo nombre de título).
  Reproducir fielmente el ``estado_previo`` encadenado exige correr el orquestador
  multiactivo completo (``proyectar_tramos_multiactivo`` + ``extraer_vm_cierre``);
  construirlo a mano produce VMs inconsistentes que no demuestran el fix de forma
  limpia. Es exactamente la "inviabilidad por dependencias de datos" que la Task 3.2
  contempla.

* **Nivel 2 (helper real de producción, ELEGIDO como núcleo)**: ejercitar directamente
  ``app_dash._realinear_pyg_enero_tramo`` sobre un DataFrame mínimo construido como la
  salida del motor (fila ``i == 0`` fantasma con ``pyg = 0`` y ``VM = VM_cierre_previo``),
  con un ``col_id`` único por posición. Esto prueba la lógica de PRODUCCIÓN real
  (no el stub, no la referencia), con un ``VM_cierre_previo`` controlado y verificable
  numéricamente.

Adicionalmente se incluye una **prueba E2E determinista del adaptador real**
(``_correr_rv_tramo``) en el **tramo 0** (``indice = 0``): el tramo base es determinista
(el helper es identidad), corre sobre la BD real y confirma que (a) el motor real emite
la fila fantasma ``i == 0`` que el fix debe tratar, y (b) para ``indice = 0`` el
adaptador real la deja intacta (``pyg(primer_mes) = 0``). Así se cubre el camino E2E real
sin la fragilidad del encadenamiento inter-tramo.

**Property 1: Expected Behavior** — Distribución mensual correcta en tramos
``indice > 0`` (Req 2.1, 2.2, 2.4). También se asevera la identidad del tramo 0.

**Validates: Requirements 2.1, 2.2, 2.4**
"""

from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

# Insertar raíz y scripts/ en sys.path (conftest ya lo hace, pero este módulo
# también se puede invocar aislado). Idempotente.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_ROOT, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import del CÓDIGO DE PRODUCCIÓN real. Importar app_dash NO levanta el server
# (``app.run`` vive bajo ``if __name__ == '__main__'``), aunque sí construye el
# objeto Dash (coste ~4 s, aceptable). Si la importación fallara se omite el
# módulo con un mensaje claro (no se arranca el server bajo ninguna circunstancia).
try:
    import app_dash  # noqa: E402
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - defensivo
    app_dash = None  # type: ignore
    _IMPORT_ERROR = exc

# El generador de periodos del motor real (primer día de cada mes, MS).
from proyeccion_renta_variable import generar_periodos  # noqa: E402

pytestmark = pytest.mark.skipif(
    app_dash is None,
    reason=f"No se pudo importar app_dash sin efectos colaterales: {_IMPORT_ERROR!r}",
)

# Tolerancias: los motores redondean VM/pyg a 2 decimales; el helper también.
_TOL_ABS = 0.05  # centavos
_TOL_REL = 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# Validación de entradas de los helpers de test (secure coding: zero-trust input).
# ─────────────────────────────────────────────────────────────────────────────
def _validar_entrada_serie(vm_cierre_previo: float, tasa_ea: float, anio: int) -> None:
    if not isinstance(vm_cierre_previo, (int, float)) or vm_cierre_previo <= 0:
        raise ValueError("vm_cierre_previo debe ser un número > 0")
    if not isinstance(tasa_ea, (int, float)):
        raise ValueError("tasa_ea debe ser numérico")
    if not isinstance(anio, int) or not (2000 <= anio <= 2100):
        raise ValueError("anio fuera de rango razonable (2000..2100)")


def _construir_salida_motor_buggy(
    vm_cierre_previo: float,
    tasa_ea: float,
    anio: int,
    col_id: str,
    id_valor: str,
) -> pd.DataFrame:
    """
    Construye el DataFrame mínimo tal como lo emite el motor no-RF SIN corregir
    (``F``) para UNA posición de un tramo ``anio-01 .. anio-12``:

      - fila ``i == 0`` (enero): APERTURA FANTASMA → ``pyg_mensual = 0.0`` y
        ``valor_mercado = vm_cierre_previo`` (idéntico al cierre de dic del tramo
        previo),
      - filas ``i > 0``: capitalización por días reales (base 365) → el retorno
        real de enero queda mal etiquetado como febrero.

    Reproduce fielmente la rama ``if i == 0:`` de ``proyectar_bloque`` /
    ``proyectar_bloque_ipc_spread`` y las columnas reales del motor. Es la ENTRADA
    que recibe el helper de producción ``_realinear_pyg_enero_tramo``.
    """
    _validar_entrada_serie(vm_cierre_previo, tasa_ea, anio)
    periodos = generar_periodos(f"{anio}-01", f"{anio}-12")
    tasa_diaria = (1.0 + float(tasa_ea)) ** (1.0 / 365.0) - 1.0

    filas: list[dict] = []
    valor_anterior = float(vm_cierre_previo)
    pyg_acumulado = 0.0
    for i, periodo in enumerate(periodos):
        fecha_str = periodo.strftime("%Y-%m-%d")
        if i == 0:
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
        valor_actual = valor_anterior * (1.0 + tasa_diaria) ** dias
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


def _orden(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("fecha_proyeccion").reset_index(drop=True)


# ═════════════════════════════════════════════════════════════════════════════
# NIVEL 2 — Helper de PRODUCCIÓN real: app_dash._realinear_pyg_enero_tramo
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "nombre, vm_cierre_previo, tasa_ea, anio, serie",
    [
        ("AO_PROTECCION_2027", 3_000_000_000.0, 0.1108, 2027, "renta_variable"),
        ("ACWV_2027", 1_200_000_000.0, 0.0980, 2027, "renta_variable"),
        ("BROOKFIELD_2027", 20_000_000.0, 0.0659, 2027, "alternativos"),
    ],
)
def test_integracion_helper_real_enero_lleva_pyg_real(
    nombre, vm_cierre_previo, tasa_ea, anio, serie
):
    """
    Task 3.2 — Property 1 (Expected Behavior) sobre el HELPER DE PRODUCCIÓN real.

    Dado el DataFrame de salida del motor SIN corregir (fila ``i == 0`` fantasma),
    ``app_dash._realinear_pyg_enero_tramo(..., aplicar=True)`` (equivalente a un
    tramo ``indice > 0``) SHALL producir:

      - ``pyg(ene) = VM(ene) − VM_cierre_previo`` (P&G real de enero, != 0),
      - ``NOT (VM(ene) == VM_cierre_previo AND pyg(ene) == 0)`` (sin fila fantasma),
      - diciembre presente y ``VM(dic)`` intacto, total anual preservado.

    Validates: Requirements 2.1, 2.2, 2.4
    """
    col_id = "titulo" if serie == "renta_variable" else "id_posicion"
    f = _construir_salida_motor_buggy(vm_cierre_previo, tasa_ea, anio, col_id, nombre)

    # Patrón del bug confirmado en la ENTRADA (pre-fix): enero es la fila fantasma.
    f_ord = _orden(f)
    assert float(f_ord.iloc[0]["pyg_mensual"]) == 0.0
    assert abs(float(f_ord.iloc[0]["valor_mercado"]) - vm_cierre_previo) <= _TOL_ABS
    vm_1_motor = float(f_ord.iloc[1]["valor_mercado"])  # retorno real de enero (mal etiquetado)

    # ── LÓGICA CORREGIDA REAL (tramo indice > 0 => aplicar=True) ────────────────
    f_prima = app_dash._realinear_pyg_enero_tramo(
        f, {nombre: vm_cierre_previo}, col_id=col_id, aplicar=True
    )
    fp = _orden(f_prima)

    m0 = fp.iloc[0]  # enero del tramo
    vm_m0 = float(m0["valor_mercado"])
    pyg_m0 = float(m0["pyg_mensual"])
    pyg_real_enero = vm_1_motor - vm_cierre_previo

    # (1) El VM al cierre de enero refleja el retorno real (no duplica el cierre previo).
    assert abs(vm_m0 - vm_1_motor) <= _TOL_ABS + _TOL_REL * abs(vm_1_motor), (
        f"[{nombre}] VM(enero)={vm_m0:,.2f} debería reflejar el retorno real "
        f"(esperado {vm_1_motor:,.2f}), no VM_cierre_previo={vm_cierre_previo:,.2f}"
    )

    # (2) P&G de enero = retorno real respecto al cierre previo (Req 2.1, 2.2).
    assert abs(pyg_m0 - pyg_real_enero) <= _TOL_ABS + _TOL_REL * abs(pyg_real_enero), (
        f"[{nombre}] pyg(enero)={pyg_m0:,.2f} debería ser VM(ene)-VM_cierre_previo="
        f"{pyg_real_enero:,.2f}"
    )

    # (3) Ya NO existe la fila fantasma (enero != cierre previo y pyg != 0).
    es_fantasma = (
        abs(vm_m0 - vm_cierre_previo) <= _TOL_ABS and abs(pyg_m0) <= _TOL_ABS
    )
    assert not es_fantasma, f"[{nombre}] la fila fantasma de enero NO fue eliminada"
    assert abs(pyg_m0) > _TOL_ABS, f"[{nombre}] pyg(enero) debe ser != 0 con retorno real"

    # (4) Diciembre presente y VM(dic) intacto (Req 2.4 / preservación de cierre).
    assert f"{anio}-12-01" in set(fp["fecha_proyeccion"]), f"[{nombre}] falta diciembre"
    assert abs(float(fp.iloc[-1]["valor_mercado"]) - float(f_ord.iloc[-1]["valor_mercado"])) <= _TOL_ABS, (
        f"[{nombre}] VM(dic) no debe cambiar tras el realineamiento"
    )

    # (5) Total anual preservado (telescoping): Σ pyg' == Σ pyg == VM(dic) − VM_prev.
    suma_fp = float(fp["pyg_mensual"].sum())
    suma_f = float(f_ord["pyg_mensual"].sum())
    ancla = float(fp.iloc[-1]["valor_mercado"]) - vm_cierre_previo
    assert abs(suma_fp - suma_f) <= max(_TOL_ABS, _TOL_REL * abs(suma_f)), (
        f"[{nombre}] total anual F'={suma_fp:,.2f} != F={suma_f:,.2f}"
    )
    assert abs(suma_fp - ancla) <= max(_TOL_ABS, _TOL_REL * abs(ancla)), (
        f"[{nombre}] total anual F'={suma_fp:,.2f} != ancla telescópica={ancla:,.2f}"
    )


def test_integracion_helper_real_tramo0_es_identidad():
    """
    Task 3.2 — Preservación por construcción: para un tramo 0 (``aplicar=False``)
    el helper de PRODUCCIÓN real es IDENTIDAD; el mes base conserva ``pyg = 0``
    (Req 2.5/3.1/3.8, aquí como contraste del Expected Behavior de 2.1/2.4).
    """
    col_id = "titulo"
    # Tramo 0 parcial jul..dic 2026, con fila fantasma en el mes base (jul).
    periodos = generar_periodos("2026-07", "2026-12")
    vm0 = 1_000_000.0
    tasa_diaria = (1.0 + 0.10) ** (1.0 / 365.0) - 1.0
    filas, va, acc = [], vm0, 0.0
    for i, p in enumerate(periodos):
        if i == 0:
            filas.append({col_id: "POS", "fecha_proyeccion": p.strftime("%Y-%m-%d"),
                          "valor_mercado": round(va, 2), "pyg_mensual": 0.0, "pyg_acumulado": 0.0})
            continue
        dias = (p - periodos[i - 1]).days
        vac = va * (1.0 + tasa_diaria) ** dias
        acc += vac - va
        filas.append({col_id: "POS", "fecha_proyeccion": p.strftime("%Y-%m-%d"),
                      "valor_mercado": round(vac, 2), "pyg_mensual": round(vac - va, 2),
                      "pyg_acumulado": round(acc, 2)})
        va = vac
    f = pd.DataFrame(filas)

    # Tramo 0 => aplicar=False => identidad real.
    f_prima = app_dash._realinear_pyg_enero_tramo(
        f, {"POS": vm0}, col_id=col_id, aplicar=False
    )
    pd.testing.assert_frame_equal(_orden(f_prima), _orden(f), check_dtype=False)
    assert float(_orden(f_prima).iloc[0]["pyg_mensual"]) == 0.0, "mes base del tramo 0 debe quedar en 0"


def test_integracion_helper_real_posicion_nueva_sin_cierre_previo():
    """
    Task 3.2 — Req 3.4: una posición NUEVA sin ``VM_cierre_previo`` se deja como
    apertura legítima (no se realinea) aun con ``aplicar=True`` (indice > 0). El
    helper de producción real debe devolver esa posición intacta.
    """
    col_id = "titulo"
    f = _construir_salida_motor_buggy(500_000_000.0, 0.09, 2027, col_id, "NUEVA")
    # aplicar=True pero el mapa de cierre previo NO contiene "NUEVA".
    f_prima = app_dash._realinear_pyg_enero_tramo(
        f, {"OTRA": 1.0}, col_id=col_id, aplicar=True
    )
    pd.testing.assert_frame_equal(_orden(f_prima), _orden(f), check_dtype=False)


# ═════════════════════════════════════════════════════════════════════════════
# NIVEL 1 (parcial, determinista) — Adaptador REAL _correr_rv_tramo sobre BD real.
#
# El tramo 0 (indice=0) es determinista: el helper es identidad y el adaptador
# corre end-to-end sobre portafolio.db. Confirma que (a) el motor real emite la
# fila fantasma i==0 que el fix debe tratar y (b) para indice=0 el adaptador real
# la deja intacta (pyg del primer mes = 0). Así se ejercita el camino E2E real.
# ═════════════════════════════════════════════════════════════════════════════
def _db_disponible() -> bool:
    try:
        p = str(getattr(app_dash, "DB_PATH", ""))
        return bool(p) and os.path.exists(p) and os.path.getsize(p) > 0
    except Exception:
        return False


@pytest.mark.skipif(not _db_disponible(), reason="portafolio.db no disponible")
def test_integracion_adaptador_real_rv_tramo0_emite_fila_fantasma_y_la_preserva():
    """
    Task 3.2 — Camino E2E real (Nivel 1, tramo determinista): invoca el adaptador
    de producción ``_correr_rv_tramo`` con ``indice = 0`` y ``estado_previo = None``
    sobre la BD real.

    Verifica, por posición (``titulo``):
      - el primer mes (enero) es la fila de apertura con ``pyg_mensual = 0`` (el
        patrón que el fix debe corregir en tramos ``indice > 0``), y
      - como ``indice = 0`` ⇒ el helper real es identidad ⇒ ese ``pyg = 0`` del mes
        base se PRESERVA (Property 5 / Req 3.1, contraste de 2.1/2.4).

    Esto demuestra, sobre el código de producción real end-to-end, que la fila
    fantasma existe a la salida del motor y que el fix solo actúa cuando
    ``indice > 0`` (en el tramo 0 queda intacta, como exige el usuario).
    """
    anio = 2027
    tramo0 = SimpleNamespace(
        indice=0, fecha_inicio=date(anio, 1, 1), fecha_corte=date(anio, 12, 31)
    )
    inputs = SimpleNamespace(ret_acc_local_ea=0.11, ret_acc_intl_ea=0.098, pei_ea=0.05)
    fechas = pd.date_range(f"{anio}-01-01", f"{anio}-12-01", freq="MS")
    senda_trm = {f.strftime("%Y-%m-%d"): 4000.0 for f in fechas}
    sendas = SimpleNamespace(senda_trm=senda_trm, senda_ipc={})

    df = app_dash._correr_rv_tramo(tramo0, inputs, sendas, estado_previo=None)

    assert df is not None and len(df) > 0, "el adaptador real debe producir resultado"
    assert {"titulo", "fecha_proyeccion", "valor_mercado", "pyg_mensual"}.issubset(df.columns)

    # Primer corte global = enero del tramo.
    primer_mes = df.sort_values("fecha_proyeccion").iloc[0]["fecha_proyeccion"]
    assert primer_mes == f"{anio}-01-01", f"primer corte debería ser {anio}-01-01, es {primer_mes}"

    # Para CADA posición, la fila de enero (apertura, tramo 0) tiene pyg == 0 y se
    # preserva (identidad del helper en indice=0).
    enero = df[df["fecha_proyeccion"] == f"{anio}-01-01"]
    assert len(enero) > 0, "debe existir el corte de enero"
    pyg_enero_max = float(enero["pyg_mensual"].abs().max())
    assert pyg_enero_max == 0.0, (
        f"en el tramo 0 el mes base (enero) debe conservar pyg == 0 para todas las "
        f"posiciones; max|pyg(enero)|={pyg_enero_max}"
    )

    # Diciembre del tramo presente (invarianza de meses).
    assert f"{anio}-12-01" in set(df["fecha_proyeccion"]), "diciembre del tramo debe estar presente"
