# -*- coding: utf-8 -*-
"""
Pruebas de EXPLORACIÓN de la condición de bug — "Derivados: nominal y encadenamiento
por tramos" (modo "por_tramos", multiactivo).
Spec: .kiro/specs/derivados-nominal-encadenamiento.

Metodología (bugfix test-first, FASE 1 — EXPLORACIÓN): estas pruebas codifican el
comportamiento ESPERADO POST-FIX de los TRES defectos confirmados por lectura de código
(ver design.md, anclado a file:line). DEBEN FALLAR sobre el código SIN corregir (confirman
los bugs) y PASARÁN cuando exista el fix.

Escopadas a SEAMS PUROS (NO requieren las tablas de mercado `hist_pip` /
`hist_curvas_derivados`, ausentes en el DB de prueba — falla pre-existente documentada en
docs/deuda-tecnica.md). En concreto:

  - D1 (nominal, re-renovación): se ejercita directamente el seam de renovación
    (`_renovar_fwds` / `_renovar_opciones`, valoracion_derivados.py ~L1164-1211 /
    ~L1258-1322). Se simula un plano YA renovado (resultado de una primera renovación)
    pasado como entrada a una segunda renovación. El nominal vivo NO debe crecer por
    re-renovar lo ya renovado. HOY FALLA (los bloques se re-siembran y el nominal se
    infla/compone).

  - D2 (flags): (a) round-trip del plano de opciones con `persistir_plano_derivados_proy`
    / `cargar_plano_derivados_proy` (scripts/proyeccion_tramos.py ~L1088-1174) sobre una
    BD SQLite temporal aislada: `_renovado`/`_call_resuelto` deben volver como `bool`
    (hoy vuelven int 0/1 → FALLA, pues `_normalizar_plano_opts` ~L1697-1710 no los retipa).
    (b) `_renovar_opciones` sobre un plano con `_renovado=True` NO debe re-marcar
    `_renovado=False` (hoy fuerza `base['_renovado']=False`, ~L1286 → FALLA).
    (c) `_renovar_fwds` debe exponer una columna `_es_renovacion` que distinga original
    (False) de renovado (True) — hoy no existe → FALLA.

  - D3 (curva): prueba unit-level del mecanismo de ACUMULACIÓN esperado. El helper
    `acumular_delta_curva`, el campo `EstadoEncadenado.delta_curva_acumulado` y el
    parámetro `valorar_portafolio_derivados(delta_curva_base=...)` AÚN NO EXISTEN (son del
    fix). Las pruebas los referencian de modo que FALLEN hoy por ausencia del seam, y
    codifican el comportamiento esperado: la curva de arranque del tramo 1 =
    base + delta_acum(tramo 0). Cuando exista el fix, PASAN.

Convención de ejecución (Windows / Google Drive) — ver tasks.md:

    python -m pytest "g:\\Mi unidad\\AXIS\\AXIS\\tests\\test_derivados_nominal_encadenamiento.py" ^
      --rootdir "g:\\Mi unidad\\AXIS\\AXIS" -o addopts="" -p no:cacheprovider -q

El terminal suele devolver exit code -1 aun en éxito — fiarse de la línea de resumen de
pytest. Hypothesis con `@settings(deadline=None, max_examples=100,
suppress_health_check=[HealthCheck.too_slow])` (la latencia del Drive dispara falsos
`DeadlineExceeded`).
"""

import inspect
import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

# Rutas importables con independencia del modo de importación de pytest.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
_SCRIPTS = os.path.join(_RAIZ, "scripts")
for _p in (_ESTE_DIR, _RAIZ, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import valoracion_derivados as vd  # noqa: E402
from proyeccion_tramos import (  # noqa: E402
    cargar_plano_derivados_proy,
    persistir_plano_derivados_proy,
)
import proyeccion_tramos_multiactivo as ptm  # noqa: E402

# Ajustes comunes de Hypothesis (>=100 ejemplos; Drive lento).
_MIN_EJEMPLOS = 100
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de construcción de planos mínimos (columnas que esperan los seams)
# ══════════════════════════════════════════════════════════════════════════════

def _trm_df_para(constituciones, trm_valor=4000.0):
    """Construye el `trm_df` que `_renovar_fwds`/`_renovar_opciones` esperan: columnas
    `fecha` (datetime) y `trm` (float). Cubre todas las fechas de constitución que puedan
    generarse por renovación (cadena de vencimientos), con holgura amplia."""
    fechas = pd.date_range(
        start=min(pd.to_datetime(list(constituciones))),
        periods=4000, freq="D",
    )
    return pd.DataFrame({"fecha": fechas, "trm": float(trm_valor)})


def _plano_fwds_base(n=2, nominal=1_000_000.0):
    """Plano mínimo de forwards con las columnas que `_renovar_fwds` consume:
    `constitucion/vencimiento/nominal/sentido/portafolio`."""
    const = pd.to_datetime("2026-07-31")
    filas = []
    for i in range(n):
        filas.append({
            "constitucion": const,
            "vencimiento": const + timedelta(days=90),
            "nominal": float(nominal),
            "sentido": "Compra" if i % 2 == 0 else "Venta",
            "portafolio": f"PORT_{i}",
        })
    return pd.DataFrame(filas)


def _plano_opts_base(nominal=1_000_000.0):
    """Plano mínimo de opciones con las columnas que `_renovar_opciones` consume:
    `constitucion/vencimiento/sentido/call_put/portafolio/nominal`. Un collar = Put+Call."""
    const = pd.to_datetime("2026-07-31")
    venc = const + timedelta(days=180)
    filas = [
        {"constitucion": const, "vencimiento": venc, "sentido": "Compra",
         "call_put": "Put", "portafolio": "PORT_C", "nominal": float(nominal),
         "strike": 3900.0},
        {"constitucion": const, "vencimiento": venc, "sentido": "Venta",
         "call_put": "Call", "portafolio": "PORT_C", "nominal": float(nominal),
         "strike": 4200.0},
    ]
    return pd.DataFrame(filas)


def _nominal_vivo(plano_fwds, fecha_cal):
    """Replica la agregación del motor (valoracion_derivados.py:1996-2000): suma `nominal`
    sobre TODAS las filas constituidas a `fecha_cal` (constitucion <= fecha_cal)."""
    df = plano_fwds.copy()
    df["constitucion"] = pd.to_datetime(df["constitucion"])
    vivos = df.loc[df["constitucion"] <= pd.to_datetime(fecha_cal)]
    return float(vivos["nominal"].sum())


# ══════════════════════════════════════════════════════════════════════════════
# D1 — Doble renovación → inflación/composición del nominal
# ══════════════════════════════════════════════════════════════════════════════

def test_d1_fwds_doble_renovacion_no_infla_nominal_vivo():
    """D1 (forwards): un plano YA renovado re-renovado NO debe aumentar el nominal vivo.

    Contraejemplo esperado HOY: `_renovar_fwds` genera bloques nuevos con el MISMO nominal
    del padre (valoracion_derivados.py:~L1192) y los CONCATENA sin reemplazar la fila
    original; al re-renovar el plano ya renovado se añade OTRA tanda de bloques, de modo
    que el nominal vivo del "doble renovado" > nominal del "renovado una vez".
    POST-FIX: la segunda renovación NO re-siembra las filas ya renovadas → nominal igual.
    """
    fwds = _plano_fwds_base(n=2)
    # Horizonte amplio: todas las renovaciones quedan constituidas (constitucion <= cal).
    fecha_cal = pd.to_datetime("2035-01-01")

    # 1ª renovación (plano fresco → plano renovado una vez).
    renov1 = vd._renovar_fwds(
        fwds, _trm_df_para(fwds["constitucion"]), iteraciones=3, dev_forecast=0.085,
    )
    nominal_una_vez = _nominal_vivo(renov1, fecha_cal)

    # 2ª renovación sobre el plano YA renovado (simula re-renovar el plano inyectado).
    renov2 = vd._renovar_fwds(
        renov1, _trm_df_para(renov1["constitucion"]), iteraciones=3, dev_forecast=0.085,
    )
    nominal_doble = _nominal_vivo(renov2, fecha_cal)

    # Comportamiento ESPERADO (post-fix): re-renovar lo ya renovado NO infla el nominal.
    assert nominal_doble == pytest.approx(nominal_una_vez), (
        "D1 (fwds): re-renovar un plano ya renovado infló el nominal vivo "
        f"(doble={nominal_doble:.2f} > una_vez={nominal_una_vez:.2f}); "
        "el nominal vivo se compone por el solo hecho de encadenar tramos."
    )


def test_d1_opts_doble_renovacion_no_infla_nominal_vivo():
    """D1 (opciones): re-renovar un plano de opciones ya renovado NO debe inflar el
    nominal vivo de opciones.

    Contraejemplo esperado HOY: `_renovar_opciones` (valoracion_derivados.py:~L1258-1322)
    AÑADE una tanda de collares con el MISMO nominal del padre (~L1301) sin distinguir si
    el plano ya venía renovado; re-ejecutarla duplica los bloques y el nominal vivo crece.
    POST-FIX: no re-renueva las filas ya marcadas `_renovado=True` → nominal conservado.
    """
    opts = _plano_opts_base()
    fecha_cal = pd.to_datetime("2035-01-01")

    renov1 = vd._renovar_opciones(opts, _trm_df_para(opts["constitucion"]), canal_opc=1650.0)
    nominal_una_vez = _nominal_vivo(renov1, fecha_cal)

    renov2 = vd._renovar_opciones(
        renov1, _trm_df_para(renov1["constitucion"]), canal_opc=1650.0,
    )
    nominal_doble = _nominal_vivo(renov2, fecha_cal)

    assert nominal_doble == pytest.approx(nominal_una_vez), (
        "D1 (opts): re-renovar un plano de opciones ya renovado infló el nominal vivo "
        f"(doble={nominal_doble:.2f} > una_vez={nominal_una_vez:.2f})."
    )


@given(
    nominal=st.floats(min_value=1_000.0, max_value=1e9,
                      allow_nan=False, allow_infinity=False),
    iteraciones=st.integers(min_value=1, max_value=4),
    n_filas=st.integers(min_value=1, max_value=3),
)
@example(nominal=1_000_000.0, iteraciones=3, n_filas=2)
@_AJUSTES
def test_d1_prop_fwds_renovacion_idempotente_en_nominal(nominal, iteraciones, n_filas):
    """D1 (property): renovar es IDEMPOTENTE respecto del nominal vivo — renovar una vez y
    volver a renovar el resultado deben dejar el MISMO nominal vivo total (no se re-siembra
    lo ya renovado). HOY FALLA (la segunda renovación añade bloques y el nominal crece)."""
    fwds = _plano_fwds_base(n=n_filas, nominal=nominal)
    fecha_cal = pd.to_datetime("2040-01-01")

    renov1 = vd._renovar_fwds(
        fwds, _trm_df_para(fwds["constitucion"]), iteraciones=iteraciones, dev_forecast=0.085,
    )
    renov2 = vd._renovar_fwds(
        renov1, _trm_df_para(renov1["constitucion"]), iteraciones=iteraciones, dev_forecast=0.085,
    )

    assert _nominal_vivo(renov2, fecha_cal) == pytest.approx(_nominal_vivo(renov1, fecha_cal)), (
        "D1 (property): re-renovar un plano ya renovado no es idempotente en el nominal "
        "vivo (se infla por composición)."
    )


# ══════════════════════════════════════════════════════════════════════════════
# D2 — Reset/pérdida de flags de control de collares
# ══════════════════════════════════════════════════════════════════════════════

def test_d2a_round_trip_opts_restaura_flags_bool(tmp_path):
    """D2 (a): round-trip del plano de opciones por SQLite debe restaurar
    `_renovado`/`_call_resuelto` como `bool` (hoy vuelven int 0/1).

    Usa una BD SQLite temporal/aislada (tmp_path), NO el portafolio.db real. Persiste con
    `persistir_plano_derivados_proy` y recarga con `cargar_plano_derivados_proy`.
    `_normalizar_plano_opts` (valoracion_derivados.py:1697-1710) NO retipa estos flags →
    el tipo recargado difiere del persistido → FALLA.
    """
    opts = _plano_opts_base()
    opts["_renovado"] = True
    opts["_collar_id"] = "COLLAR_X"
    opts["_call_resuelto"] = True

    db_path = os.path.join(str(tmp_path), "plano_opts_roundtrip.db")
    corte = date(2026, 7, 31)
    with sqlite3.connect(db_path) as conn:
        persistir_plano_derivados_proy(conn, {"opts": opts}, corte)
    with sqlite3.connect(db_path) as conn:
        recargado = cargar_plano_derivados_proy(conn, corte)

    opts_rt = vd._normalizar_plano_opts(recargado["opts"])
    assert len(opts_rt) == len(opts)

    # Los flags lógicos deben sobrevivir el round-trip con tipo booleano.
    for col in ("_renovado", "_call_resuelto"):
        valores = opts_rt[col].tolist()
        assert all(isinstance(v, (bool, np.bool_)) for v in valores), (
            f"D2 (a): la columna {col} volvió del round-trip como "
            f"{[type(v).__name__ for v in valores]} (esperado bool); "
            "los flags recargados difieren en tipo de los persistidos."
        )
        assert all(bool(v) is True for v in valores)


def test_d2b_renovar_opciones_no_remarca_renovado_false():
    """D2 (b): `_renovar_opciones` sobre un plano con `_renovado=True` NO debe re-marcar
    `_renovado=False` esas filas ya renovadas.

    Contraejemplo esperado HOY: `_renovar_opciones` fuerza `base['_renovado']=False`
    (valoracion_derivados.py:~L1286) sobre TODAS las filas base, incluidas las que ya
    venían como renovaciones del tramo anterior → viola la inmutabilidad del collar
    renovado. POST-FIX: respeta `_renovado=True` de las filas inyectadas.
    """
    opts = _plano_opts_base()
    # Simular un plano ya renovado inyectado del tramo anterior.
    opts["_renovado"] = True
    opts["_collar_id"] = "COLLAR_PREVIO"
    opts["_call_resuelto"] = True

    resultado = vd._renovar_opciones(opts, _trm_df_para(opts["constitucion"]), canal_opc=1650.0)

    # Localizar las filas que corresponden al collar YA renovado inyectado (mismo
    # collar_id previo) y verificar que conservan _renovado=True.
    previas = resultado.loc[resultado["_collar_id"] == "COLLAR_PREVIO"]
    assert len(previas) == len(opts), (
        "D2 (b): no se preservaron las filas del collar ya renovado inyectado "
        "(se perdió el _collar_id previo al re-renovar)."
    )
    assert previas["_renovado"].apply(lambda v: bool(v) is True).all(), (
        "D2 (b): `_renovar_opciones` re-marcó `_renovado=False` sobre filas que llegaban "
        "ya renovadas (base['_renovado']=False incondicional); se re-renueva un collar ya "
        "renovado violando su inmutabilidad."
    )


def test_d2c_renovar_fwds_expone_columna_es_renovacion():
    """D2 (c): `_renovar_fwds` debe exponer la columna `_es_renovacion` que distinga la
    fila original (False) de la renovada (True).

    HOY FALLA: `_renovar_fwds` produce solo
    `constitucion/vencimiento/nominal/sentido/portafolio/tasa_negociada`, sin ninguna
    columna de control → imposible distinguir original de renovado al recargar.
    """
    fwds = _plano_fwds_base(n=2)
    renov = vd._renovar_fwds(
        fwds, _trm_df_para(fwds["constitucion"]), iteraciones=2, dev_forecast=0.085,
    )

    assert "_es_renovacion" in renov.columns, (
        "D2 (c): `_renovar_fwds` no expone la columna `_es_renovacion`; no es posible "
        "distinguir un forward original de uno renovado tras el round-trip."
    )
    # Las filas base originales deben ser False; los bloques generados, True.
    n_originales = len(fwds)
    serie = renov["_es_renovacion"].astype(bool).tolist()
    assert serie[:n_originales] == [False] * n_originales, (
        "D2 (c): las filas originales no quedaron marcadas `_es_renovacion=False`."
    )
    assert any(serie[n_originales:]), (
        "D2 (c): ningún bloque de renovación quedó marcado `_es_renovacion=True`."
    )


# ══════════════════════════════════════════════════════════════════════════════
# D3 — La curva de valoración NO acumula los shifts/deltas entre tramos
# ══════════════════════════════════════════════════════════════════════════════

def test_d3a_existe_helper_acumular_delta_curva():
    """D3: debe existir el helper puro `acumular_delta_curva(prev, delta_tramo) -> dict`
    en scripts/proyeccion_tramos_multiactivo.py (seam del fix). HOY FALLA (no existe)."""
    assert hasattr(ptm, "acumular_delta_curva"), (
        "D3: no existe el helper `acumular_delta_curva` (seam del fix ausente); la curva "
        "no puede acumular el delta entre tramos."
    )


def test_d3b_estado_encadenado_tiene_delta_curva_acumulado():
    """D3: `EstadoEncadenado` debe tener el campo `delta_curva_acumulado` (análogo a
    `repo_cierre`/`inflacion_cierre`). HOY FALLA (el campo no existe en la dataclass)."""
    campos = set(getattr(ptm.EstadoEncadenado, "__dataclass_fields__", {}).keys())
    assert "delta_curva_acumulado" in campos, (
        "D3: `EstadoEncadenado` no tiene el campo `delta_curva_acumulado`; el estado de "
        f"curva no se encadena entre tramos. Campos actuales: {sorted(campos)}."
    )


def test_d3c_valorar_portafolio_derivados_acepta_delta_curva_base():
    """D3: la firma de `valorar_portafolio_derivados` debe aceptar `delta_curva_base`
    (desplazamiento base de la curva, default retrocompatible). HOY FALLA (no existe)."""
    firma = inspect.signature(vd.valorar_portafolio_derivados)
    assert "delta_curva_base" in firma.parameters, (
        "D3: `valorar_portafolio_derivados` no acepta `delta_curva_base`; no hay forma de "
        "inyectar el delta de curva acumulado como nivel de arranque del tramo. "
        f"Parámetros actuales: {list(firma.parameters)}."
    )


def test_d3d_acumulacion_curva_arranque_tramo1_igual_base_mas_delta_acum():
    """D3 (comportamiento esperado): dado delta_acum(tramo0) y delta(tramo1), la curva de
    arranque del tramo1 = base + delta_acum(tramo0).

    Codifica el mecanismo del fix usando `acumular_delta_curva`. HOY FALLA por ausencia del
    seam (AttributeError al no existir el helper); POST-FIX pasa.
    """
    # Delta acumulado de los tramos previos (cierre del tramo 0).
    delta_acum_tramo0 = {
        "delta_tes_cop": 50.0,
        "delta_tes_uvr": 30.0,
        "delta_fed_bps": 25.0,
        "delta_10t_bps": 10.0,
    }
    # Delta propio del tramo 1.
    delta_tramo1 = {
        "delta_tes_cop": 20.0,
        "delta_tes_uvr": 10.0,
        "delta_fed_bps": 5.0,
        "delta_10t_bps": 15.0,
    }

    # El acumulado al cerrar el tramo 1 = acum(tramo0) + delta(tramo1).
    acumulado = ptm.acumular_delta_curva(delta_acum_tramo0, delta_tramo1)

    esperado = {
        "delta_tes_cop": 70.0,
        "delta_tes_uvr": 40.0,
        "delta_fed_bps": 30.0,
        "delta_10t_bps": 25.0,
    }
    for clave, val in esperado.items():
        assert acumulado.get(clave) == pytest.approx(val), (
            f"D3: `acumular_delta_curva` no sumó correctamente {clave} "
            f"(obtenido={acumulado.get(clave)}, esperado={val})."
        )

    # La curva de arranque del tramo 1 debe partir de base + delta_acum(tramo 0), NO de la
    # base fija. Verificamos la propiedad sobre un nivel base arbitrario por tipo.
    base_curva = {"delta_tes_cop": 100.0, "delta_tes_uvr": 100.0,
                  "delta_fed_bps": 100.0, "delta_10t_bps": 100.0}
    arranque_tramo1 = {
        k: base_curva[k] + delta_acum_tramo0[k] for k in base_curva
    }
    assert arranque_tramo1 == {
        "delta_tes_cop": 150.0, "delta_tes_uvr": 130.0,
        "delta_fed_bps": 125.0, "delta_10t_bps": 110.0,
    }, "D3: la curva de arranque del tramo 1 no refleja base + delta_acum(tramo 0)."


@given(
    d0=st.fixed_dictionaries({
        "delta_tes_cop": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_tes_uvr": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_fed_bps": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_10t_bps": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
    }),
    d1=st.fixed_dictionaries({
        "delta_tes_cop": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_tes_uvr": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_fed_bps": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
        "delta_10t_bps": st.floats(min_value=-300.0, max_value=300.0,
                                   allow_nan=False, allow_infinity=False),
    }),
)
@_AJUSTES
def test_d3_prop_acumular_delta_curva_suma_por_tipo(d0, d1):
    """D3 (property): `acumular_delta_curva` suma componente a componente por tipo de curva
    para cualquier par de deltas. HOY FALLA por ausencia del seam."""
    assert hasattr(ptm, "acumular_delta_curva"), (
        "D3 (property): falta el seam `acumular_delta_curva`."
    )
    out = ptm.acumular_delta_curva(d0, d1)
    for clave in ("delta_tes_cop", "delta_tes_uvr", "delta_fed_bps", "delta_10t_bps"):
        assert out.get(clave) == pytest.approx(d0[clave] + d1[clave]), (
            f"D3 (property): {clave} no se acumuló como la suma de los deltas por tramo."
        )


# ══════════════════════════════════════════════════════════════════════════════
# PRUEBAS DE INTEGRACIÓN — flujo por tramos ENCADENADO (seams encadenados)
# ══════════════════════════════════════════════════════════════════════════════
#
# Estas pruebas (prefijo `test_integracion_`, sufijos d1/d2/d3) ejercitan el flujo
# por tramos encadenado END-TO-END a nivel de los SEAMS que el orquestador cablea,
# SIN requerir las tablas de mercado `hist_pip` / `hist_curvas_derivados` (ausentes
# en el DB de prueba — falla pre-existente documentada en docs/deuda-tecnica.md).
#
# El encadenamiento real que simulan (tal como lo hace `_ejecutar_escenario_por_tramos`):
#   1) El tramo i renueva su plano fresco (`_renovar_fwds`/`_renovar_opciones`).
#   2) Se persiste el plano de cierre con `persistir_plano_derivados_proy` en una BD
#      SQLite temporal aislada (tmp_path) e idempotente por corte.
#   3) Se recarga con `cargar_plano_derivados_proy` + `_normalizar_plano_opts`.
#   4) Se inyecta al tramo i+1 re-renovando (NO debe re-sembrar lo ya renovado).
#   5) El delta de curva se acumula entre tramos vía `acumular_delta_curva`
#      (análogo a `EstadoEncadenado.delta_curva_acumulado`).
#
# Reutilizan los helpers ya definidos arriba (`_plano_fwds_base`, `_plano_opts_base`,
# `_nominal_vivo`, `_trm_df_para`) — no se duplican.


def _roundtrip_plano(plano, corte, tmp_path, nombre_db="plano_integracion.db"):
    """Simula el seam de persistencia encadenada: persiste el `plano` de cierre de un
    tramo en una BD SQLite temporal aislada (tmp_path) con `persistir_plano_derivados_proy`
    y lo recarga con `cargar_plano_derivados_proy`, normalizando las opciones con
    `_normalizar_plano_opts` (igual que la inyección real al tramo siguiente). Devuelve el
    plano recargado `{'fwds','opts','swaps'}` listo para re-renovar en el tramo i+1."""
    db_path = os.path.join(str(tmp_path), nombre_db)
    with sqlite3.connect(db_path) as conn:
        persistir_plano_derivados_proy(conn, plano, corte)
    with sqlite3.connect(db_path) as conn:
        recargado = cargar_plano_derivados_proy(conn, corte)
    if "opts" in recargado and len(recargado["opts"]):
        recargado["opts"] = vd._normalizar_plano_opts(recargado["opts"])
    return recargado


# ──────────────────────────────────────────────────────────────────────────────
# D1 integración — nominal conservado a través de una cadena de >=2 tramos
# ──────────────────────────────────────────────────────────────────────────────

def test_integracion_d1_cadena_dos_tramos_conserva_nominal_vivo(tmp_path):
    """D1 (integración, 2 tramos): el nominal vivo del tramo 1 == nominal vivo del tramo 0.

    Simula el encadenamiento real: el tramo 0 renueva un plano fresco de forwards, se
    persiste/recarga por SQLite (seam `persistir_`/`cargar_plano_derivados_proy` sobre
    tmp_path) y se inyecta al tramo 1 re-renovando. El nominal vivo NO debe crecer por la
    re-renovación encadenada (D1: no se re-siembran los bloques ya renovados).
    """
    fecha_cal = pd.to_datetime("2040-01-01")
    corte0 = date(2026, 12, 31)

    # Tramo 0: plano fresco renovado una vez.
    fwds0 = _plano_fwds_base(n=2)
    plano_cierre_t0 = vd._renovar_fwds(
        fwds0, _trm_df_para(fwds0["constitucion"]), iteraciones=3, dev_forecast=0.085,
    )
    nominal_t0 = _nominal_vivo(plano_cierre_t0, fecha_cal)

    # Persistir/recargar (round-trip real del plano de cierre del tramo 0).
    recargado = _roundtrip_plano({"fwds": plano_cierre_t0}, corte0, tmp_path)
    fwds_inyectado = recargado["fwds"]
    # El round-trip debe preservar la marca de control que distingue original de renovado.
    assert "_es_renovacion" in fwds_inyectado.columns, (
        "D1 int: el round-trip perdió la columna `_es_renovacion`; sin ella el tramo 1 no "
        "puede saber qué filas ya eran renovaciones y re-sembraría bloques."
    )

    # Tramo 1: re-renovar el plano inyectado (lo que hace el orquestador al encadenar).
    plano_cierre_t1 = vd._renovar_fwds(
        fwds_inyectado, _trm_df_para(fwds_inyectado["constitucion"]),
        iteraciones=3, dev_forecast=0.085,
    )
    nominal_t1 = _nominal_vivo(plano_cierre_t1, fecha_cal)

    assert nominal_t1 == pytest.approx(nominal_t0), (
        "D1 int: el nominal vivo del tramo 1 creció por re-renovación encadenada "
        f"(t1={nominal_t1:.2f} != t0={nominal_t0:.2f}); el nominal se compone al encadenar."
    )


def test_integracion_d1_cadena_tres_tramos_nominal_no_monotono_creciente(tmp_path):
    """D1 (integración, 3 tramos 2026→2027→2028): el nominal vivo NO es monotónicamente
    creciente por el solo hecho de encadenar tramos.

    Encadena 3 tramos con el seam real (renovar → persistir → recargar → re-renovar) y
    afirma que el nominal vivo se CONSERVA tramo a tramo (t0 == t1 == t2), de modo que la
    secuencia no es estrictamente creciente por composición.
    """
    fecha_cal = pd.to_datetime("2045-01-01")
    cortes = [date(2026, 12, 31), date(2027, 12, 31), date(2028, 12, 31)]

    fwds = _plano_fwds_base(n=2)
    nominales = []
    for i, corte in enumerate(cortes):
        plano_cierre = vd._renovar_fwds(
            fwds, _trm_df_para(fwds["constitucion"]), iteraciones=3, dev_forecast=0.085,
        )
        nominales.append(_nominal_vivo(plano_cierre, fecha_cal))
        # Encadenar: persistir/recargar e inyectar al tramo siguiente.
        recargado = _roundtrip_plano(
            {"fwds": plano_cierre}, corte, tmp_path, nombre_db=f"d1_3tramos_{i}.db",
        )
        fwds = recargado["fwds"]

    # Conservación: los tres tramos comparten el mismo nominal vivo (no se infla al encadenar).
    assert nominales[0] == pytest.approx(nominales[1]) == pytest.approx(nominales[2]), (
        f"D1 int (3 tramos): nominal vivo no conservado al encadenar: {nominales}."
    )
    # Y, explícitamente, NO es monotónicamente creciente por encadenar.
    assert not (nominales[0] < nominales[1] < nominales[2]), (
        f"D1 int (3 tramos): el nominal vivo crece monotónicamente por encadenar: {nominales}."
    )


# ──────────────────────────────────────────────────────────────────────────────
# D2 integración — flags sobreviven el round-trip y el collar renovado es inmutable
# ──────────────────────────────────────────────────────────────────────────────

def test_integracion_d2_flags_y_collar_inmutable_a_traves_de_cadena(tmp_path):
    """D2 (integración, cadena de 2-3 tramos): los flags de opciones sobreviven el
    round-trip como bool y el collar YA renovado permanece inmutable a lo largo de la cadena.

    Simula el encadenamiento real del plano de opciones: tramo 0 renueva (crea el collar
    renovado con `_collar_id`, `_renovado=True`), se persiste/recarga por SQLite y se
    re-renueva en los tramos siguientes. Afirma, a través de 3 tramos:
      - los flags `_renovado`/`_call_resuelto` vuelven como bool tras cada round-trip;
      - el collar ya renovado conserva su `_collar_id`, su `_renovado=True`;
      - su `strike` es inmutable (resuelto una sola vez; no se re-resuelve al re-renovar).
    """
    cortes = [date(2026, 12, 31), date(2027, 12, 31), date(2028, 12, 31)]

    # Tramo 0: renovar un plano de opciones fresco (genera el collar renovado).
    opts = _plano_opts_base()
    plano_cierre = vd._renovar_opciones(opts, _trm_df_para(opts["constitucion"]), canal_opc=1650.0)

    # Capturar la firma del collar renovado del tramo 0 (identidad + strike) para comparar.
    renov_mask0 = plano_cierre["_renovado"].astype(bool)
    assert renov_mask0.any(), "D2 int: el tramo 0 no generó ningún collar renovado."
    collar_ids_ref = sorted(plano_cierre.loc[renov_mask0, "_collar_id"].astype(str).tolist())
    # strike por (_collar_id, call_put) del collar renovado del tramo 0 (debe ser inmutable).
    strikes_ref = {
        (str(r["_collar_id"]), str(r["call_put"])): float(r["strike"])
        for _, r in plano_cierre.loc[renov_mask0].iterrows()
    }

    for i, corte in enumerate(cortes):
        # Encadenar: persistir/recargar (seam real) e inyectar al tramo siguiente.
        recargado = _roundtrip_plano(
            {"opts": plano_cierre}, corte, tmp_path, nombre_db=f"d2_cadena_{i}.db",
        )
        opts_rt = recargado["opts"]

        # (a) Flags vuelven como bool tras el round-trip por SQLite.
        for col in ("_renovado", "_call_resuelto"):
            valores = opts_rt[col].tolist()
            assert all(isinstance(v, (bool, np.bool_)) for v in valores), (
                f"D2 int (tramo {i}): la columna {col} no volvió como bool tras el "
                f"round-trip ({[type(v).__name__ for v in valores]})."
            )

        # (b) El collar YA renovado del tramo 0 persiste con su _collar_id y _renovado=True.
        renov_rt = opts_rt.loc[opts_rt["_renovado"].astype(bool)]
        for cid in collar_ids_ref:
            filas_cid = renov_rt.loc[renov_rt["_collar_id"].astype(str) == cid]
            assert len(filas_cid) >= 1, (
                f"D2 int (tramo {i}): se perdió el collar renovado {cid} tras el round-trip."
            )
            assert filas_cid["_renovado"].apply(lambda v: bool(v) is True).all(), (
                f"D2 int (tramo {i}): el collar {cid} dejó de estar marcado _renovado=True."
            )

        # (c) Re-renovar el plano inyectado NO debe re-marcar ni re-resolver el collar previo.
        plano_cierre = vd._renovar_opciones(
            opts_rt, _trm_df_para(opts_rt["constitucion"]), canal_opc=1650.0,
        )
        renov_post = plano_cierre.loc[plano_cierre["_renovado"].astype(bool)]
        # El _collar_id del tramo 0 sigue presente, marcado renovado y con strike inmutable.
        for (cid, cp), strike0 in strikes_ref.items():
            filas = renov_post.loc[
                (renov_post["_collar_id"].astype(str) == cid)
                & (renov_post["call_put"].astype(str) == cp)
            ]
            assert len(filas) >= 1, (
                f"D2 int (tramo {i}): el collar renovado {cid}/{cp} desapareció al re-renovar."
            )
            assert filas["_renovado"].apply(lambda v: bool(v) is True).all(), (
                f"D2 int (tramo {i}): el collar {cid}/{cp} se re-marcó _renovado=False al "
                "re-renovar (violación de inmutabilidad)."
            )
            assert all(float(s) == pytest.approx(strike0) for s in filas["strike"]), (
                f"D2 int (tramo {i}): el strike del collar renovado {cid}/{cp} cambió "
                f"(obtenido={filas['strike'].tolist()}, inmutable esperado={strike0})."
            )


# ──────────────────────────────────────────────────────────────────────────────
# D3 integración — la curva de arranque del tramo i refleja el delta acumulado 0..i-1
# ──────────────────────────────────────────────────────────────────────────────

def test_integracion_d3_cadena_tres_tramos_delta_acumulado_de_arranque(tmp_path):
    """D3 (integración, 3 tramos): el delta de curva de ARRANQUE del tramo i == suma de los
    deltas de los tramos 0..i-1, usando `acumular_delta_curva` encadenado como lo hace el
    orquestador (`EstadoEncadenado.delta_curva_acumulado`).

    Cablea la cadena acum0 -> acum1 -> acum2 (igual que
    `estado_previo.delta_curva_acumulado = acumular_delta_curva(prev, delta_tramo)` al
    cerrar cada tramo) y afirma:
      - acum tras cerrar tramo0 = delta(tramo0);
      - el delta_curva_base que recibiría el tramo1 = ese acum (delta de arranque = acum0);
      - el delta de arranque del tramo2 = delta(tramo0) + delta(tramo1); etc.
    Valida el cableado conceptual que el orquestador aplica (no requiere curvas de mercado).
    """
    deltas_por_tramo = [
        {"delta_tes_cop": 50.0, "delta_tes_uvr": 30.0, "delta_fed_bps": 25.0, "delta_10t_bps": 10.0},
        {"delta_tes_cop": 20.0, "delta_tes_uvr": 10.0, "delta_fed_bps": 5.0, "delta_10t_bps": 15.0},
        {"delta_tes_cop": 10.0, "delta_tes_uvr": 40.0, "delta_fed_bps": 15.0, "delta_10t_bps": 20.0},
    ]
    tipos = ("delta_tes_cop", "delta_tes_uvr", "delta_fed_bps", "delta_10t_bps")

    # Encadenamiento tal como el orquestador: el delta de ARRANQUE del tramo i es el
    # acumulado de cierre de los tramos previos (None/vacío en el tramo 0).
    acum_previo = None
    arranques = []  # arranques[i] = delta de arranque (base) que recibe el tramo i
    for delta_tramo in deltas_por_tramo:
        # El tramo i arranca con el acumulado de los tramos 0..i-1.
        arranque = acum_previo if acum_previo is not None else {t: 0.0 for t in tipos}
        arranques.append({t: float(arranque.get(t, 0.0)) for t in tipos})
        # Al cerrar el tramo i, se acumula su propio delta sobre el previo.
        acum_previo = ptm.acumular_delta_curva(acum_previo, delta_tramo)

    # Tramo 0 arranca en cero (sin trayectoria previa).
    assert arranques[0] == {t: 0.0 for t in tipos}, (
        "D3 int: el tramo 0 no arranca en delta cero (no debería tener trayectoria previa)."
    )
    # Tramo 1 arranca en el delta del tramo 0.
    for t in tipos:
        assert arranques[1][t] == pytest.approx(deltas_por_tramo[0][t]), (
            f"D3 int: el delta de arranque del tramo 1 ({t}) != delta(tramo0)."
        )
    # Tramo 2 arranca en delta(tramo0) + delta(tramo1).
    for t in tipos:
        esperado = deltas_por_tramo[0][t] + deltas_por_tramo[1][t]
        assert arranques[2][t] == pytest.approx(esperado), (
            f"D3 int: el delta de arranque del tramo 2 ({t}) != delta(tramo0)+delta(tramo1)."
        )
    # Acumulado final (cierre del tramo 2) = suma de los 3 tramos.
    for t in tipos:
        esperado = sum(d[t] for d in deltas_por_tramo)
        assert acum_previo[t] == pytest.approx(esperado), (
            f"D3 int: el acumulado de cierre del tramo 2 ({t}) != suma de los 3 tramos."
        )


def _curva_ea_sintetica(valor=0.05):
    """Curva EA sintética mínima con la MISMA forma que consume `_aplicar_delta_curva_base_ea`
    (columnas `Plazo_en_Dias` y `Valor`). No requiere `hist_pip`/`hist_curvas_derivados`."""
    return pd.DataFrame({
        "Plazo_en_Dias": [1, 90, 365, 1825, 3650],
        "Valor": [float(valor)] * 5,
    })


def test_integracion_d3_delta_curva_base_none_vs_ceros_mismo_arranque(tmp_path):
    """D3 (integración, retrocompatibilidad): `valorar_portafolio_derivados` acepta
    `delta_curva_base`, y aplicar `delta_curva_base=None` vs un dict de ceros produce el
    MISMO arranque de curva; un delta base NO nulo SÍ desplaza la curva.

    Verifica el seam `_aplicar_delta_curva_base_ea` directamente sobre curvas EA sintéticas
    mínimas (DataFrames con `Plazo_en_Dias`/`Valor`), sin curvas de mercado reales. La firma
    real es `_aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, delta_curva_base)` (leída de
    valoracion_derivados.py).
    """
    import inspect as _inspect

    # (0) Retrocompatibilidad de firma: el parámetro existe y su default es None.
    firma = _inspect.signature(vd.valorar_portafolio_derivados)
    assert "delta_curva_base" in firma.parameters, (
        "D3 int: `valorar_portafolio_derivados` no acepta `delta_curva_base`."
    )
    assert firma.parameters["delta_curva_base"].default is None, (
        "D3 int: el default de `delta_curva_base` no es None (retrocompatibilidad rota)."
    )

    # Confirmar la firma real de `_aplicar_delta_curva_base_ea` antes de usarla.
    firma_ea = _inspect.signature(vd._aplicar_delta_curva_base_ea)
    assert list(firma_ea.parameters) == ["r_ea", "q_ea", "r_fwd", "delta_curva_base"], (
        f"D3 int: firma inesperada de `_aplicar_delta_curva_base_ea`: {list(firma_ea.parameters)}."
    )

    r_ea = _curva_ea_sintetica(0.05)
    q_ea = _curva_ea_sintetica(0.03)
    r_fwd = _curva_ea_sintetica(0.05)
    ceros = {"delta_tes_cop": 0.0, "delta_tes_uvr": 0.0, "delta_fed_bps": 0.0, "delta_10t_bps": 0.0}

    # (a) None NO cambia la curva (comportamiento byte-idéntico al actual).
    r_none, q_none, rf_none = vd._aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, None)
    assert r_none["Valor"].tolist() == r_ea["Valor"].tolist()
    assert q_none["Valor"].tolist() == q_ea["Valor"].tolist()
    assert rf_none["Valor"].tolist() == r_fwd["Valor"].tolist()

    # (b) Un dict de ceros produce el MISMO arranque que None (sin desplazamiento).
    r_cero, q_cero, rf_cero = vd._aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, ceros)
    assert r_cero["Valor"].tolist() == r_ea["Valor"].tolist()
    assert q_cero["Valor"].tolist() == q_ea["Valor"].tolist()
    assert rf_cero["Valor"].tolist() == r_fwd["Valor"].tolist()

    # (c) Un delta base NO nulo SÍ desplaza la curva de arranque (COP: shift paralelo).
    delta_base = {"delta_tes_cop": 100.0, "delta_tes_uvr": 0.0,
                  "delta_fed_bps": 0.0, "delta_10t_bps": 0.0}
    r_shift, _q_shift, rf_shift = vd._aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, delta_base)
    # delta_tes_cop = 100 bps => +0.01 EA en SWTIBR (r_ea) y en la tasa simple (r_fwd).
    esperado_r = [v + 0.01 for v in r_ea["Valor"].tolist()]
    assert r_shift["Valor"].tolist() == pytest.approx(esperado_r), (
        "D3 int: delta_tes_cop base no desplazó SWTIBR (r_ea) en +100 bps."
    )
    assert rf_shift["Valor"].tolist() == pytest.approx([v + 0.01 for v in r_fwd["Valor"].tolist()]), (
        "D3 int: delta_tes_cop base no desplazó la tasa simple (r_fwd) en +100 bps."
    )
    # Función pura: no muta las entradas.
    assert r_ea["Valor"].tolist() == [0.05] * 5, "D3 int: `_aplicar_delta_curva_base_ea` mutó r_ea."
