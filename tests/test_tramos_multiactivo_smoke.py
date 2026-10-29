# -*- coding: utf-8 -*-
"""
Smoke tests de reuso y restricciones de arquitectura (tarea 8.2) de la feature
`proyeccion-por-tramos-multiactivo`.

Verifican el PRINCIPIO RECTOR de la feature (los cuatro motores NO se modifican) y las
restricciones de arquitectura:

  - Los cuatro motores (`proyectar_portafolio`, `proyectar_renta_variable`,
    `proyectar_alternativos`, `proyectar_creditos_contratados`) siguen existiendo, son
    callables y CONSERVAN su firma (parámetros esperados) — no se modificó su contrato
    (Req 4.3, 9.2).
  - La orquestación REUTILIZA `calcular_tramos` y la tabla `portafolio_clasificado_proy`
    (existencia de `asegurar_tabla_portafolio_proy`) y el store (Req 12.4).
  - No hay dependencias nuevas sin pinear: `proyeccion_tramos_multiactivo` sólo importa
    stdlib + `dateutil` (chequeo ligero, Req 12.2).

Estos tests son LIGEROS (inspección de firmas/imports); usan `pytest.importorskip` para
los módulos de motores por si el entorno no los tiene disponibles.
"""

from __future__ import annotations

import ast
import inspect
import os

import pytest


# ==============================================================================
# 8.2 (a) — los cuatro motores NO cambiaron su firma/contrato
# ==============================================================================

def test_motor_rf_firma_intacta():
    """`proyectar_portafolio` (RF) es callable y conserva sus parámetros clave."""
    mp = pytest.importorskip("motor_proyeccion")
    fn = getattr(mp, "proyectar_portafolio", None)
    assert callable(fn), "proyectar_portafolio no existe o no es callable"
    params = inspect.signature(fn).parameters
    for p in ("portafolio", "fecha_inicio", "meses_horizonte", "sendas",
              "trm", "trm_cierre", "senda_inflacion", "senda_repo", "mapa_reservas"):
        assert p in params, f"proyectar_portafolio perdió el parámetro '{p}'"


def test_motor_rv_firma_intacta():
    """`proyectar_renta_variable` es callable y conserva sus parámetros clave."""
    prv = pytest.importorskip("proyeccion_renta_variable")
    fn = getattr(prv, "proyectar_renta_variable", None)
    assert callable(fn)
    params = inspect.signature(fn).parameters
    for p in ("fecha_inicio", "fecha_fin", "escenarios_rv_local",
              "escenarios_rv_intl", "tasa_pei", "senda_trm"):
        assert p in params, f"proyectar_renta_variable perdió el parámetro '{p}'"


def test_motor_alternativos_firma_intacta():
    """`proyectar_alternativos` es callable y conserva sus parámetros clave."""
    palt = pytest.importorskip("proyeccion_alternativos")
    fn = getattr(palt, "proyectar_alternativos", None)
    assert callable(fn)
    params = inspect.signature(fn).parameters
    for p in ("fecha_inicio", "fecha_fin", "tasa_ea_local", "tasa_ea_intl",
              "senda_ipc", "spreads_4g", "senda_trm"):
        assert p in params, f"proyectar_alternativos perdió el parámetro '{p}'"


def test_motor_creditos_firma_intacta():
    """`proyectar_creditos_contratados` es callable y conserva sus parámetros clave."""
    pcred = pytest.importorskip("proyeccion_creditos")
    fn = getattr(pcred, "proyectar_creditos_contratados", None)
    assert callable(fn)
    params = inspect.signature(fn).parameters
    for p in ("fecha_inicio", "df_creditos", "df_senda_ipc", "df_senda_ibr",
              "df_senda_sofr", "df_senda_trm"):
        assert p in params, f"proyectar_creditos_contratados perdió el parámetro '{p}'"


# ==============================================================================
# 8.2 (b) — la orquestación reutiliza calcular_tramos + portafolio_clasificado_proy + store
# ==============================================================================

def test_reutiliza_calcular_tramos_y_tabla_proy():
    """
    La orquestación reutiliza `calcular_tramos` (troceo del horizonte) y la tabla
    `portafolio_clasificado_proy` vía `asegurar_tabla_portafolio_proy`; ambos son callables
    del módulo `proyeccion_tramos` (no se reimplementan).
    """
    ptr = pytest.importorskip("proyeccion_tramos")
    assert callable(getattr(ptr, "calcular_tramos", None)), "falta calcular_tramos"
    assert callable(getattr(ptr, "asegurar_tabla_portafolio_proy", None)), \
        "falta asegurar_tabla_portafolio_proy (tabla portafolio_clasificado_proy)"
    # `asegurar_tabla_portafolio_proy` referencia la tabla por su nombre exacto.
    fuente = inspect.getsource(ptr.asegurar_tabla_portafolio_proy)
    assert "portafolio_clasificado_proy" in fuente


def test_orquestador_usa_helpers_de_reuso():
    """
    El orquestador `_ejecutar_escenario_por_tramos` reutiliza `calcular_tramos`,
    `asegurar_tabla_portafolio_proy` y persiste snapshots en la tabla proy (store) — se
    verifica por inspección del código fuente, sin ejecutar la corrida.
    """
    app_dash = pytest.importorskip("app_dash")
    fn = getattr(app_dash, "_ejecutar_escenario_por_tramos", None)
    assert callable(fn), "falta el orquestador por tramos"
    fuente = inspect.getsource(fn)
    assert "calcular_tramos" in fuente
    assert "asegurar_tabla_portafolio_proy" in fuente
    assert "persistir_snapshot_proy" in fuente
    # Consolida en el store las claves multiactivo (reuso del contrato de resultados).
    for clave in ("rf_resumen", "rv", "alternativos", "creditos"):
        assert f"'{clave}'" in fuente or f'"{clave}"' in fuente


# ==============================================================================
# 8.2 (c) — sin dependencias nuevas sin pinear (chequeo ligero de imports)
# ==============================================================================

# Módulos permitidos a nivel superior en `proyeccion_tramos_multiactivo`: stdlib + dateutil.
# (pandas se usa sólo dentro de funciones, no como import de módulo; el chequeo se limita
#  a los imports de nivel superior del módulo de helpers.)
_STDLIB_PERMITIDOS = {
    "__future__", "logging", "math", "dataclasses", "datetime", "typing",
    "collections", "functools", "itertools", "sys", "os",
}
_TERCEROS_PERMITIDOS = {"dateutil"}


def _raiz_modulo(nombre: str) -> str:
    return (nombre or "").split(".")[0]


def test_multiactivo_solo_importa_stdlib_y_dateutil():
    """
    Req 12.2: `proyeccion_tramos_multiactivo` (helpers puros) no introduce dependencias
    nuevas. Sus imports de NIVEL SUPERIOR se limitan a stdlib + `dateutil`.
    """
    ptma = pytest.importorskip("proyeccion_tramos_multiactivo")
    ruta = inspect.getsourcefile(ptma)
    assert ruta and os.path.exists(ruta)

    with open(ruta, "r", encoding="utf-8") as fh:
        arbol = ast.parse(fh.read(), filename=ruta)

    permitidos = _STDLIB_PERMITIDOS | _TERCEROS_PERMITIDOS
    modulos_top: set[str] = set()
    for nodo in arbol.body:  # SOLO nivel superior del módulo.
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                modulos_top.add(_raiz_modulo(alias.name))
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level and nodo.level > 0:
                # Import relativo del propio paquete (permitido).
                continue
            modulos_top.add(_raiz_modulo(nodo.module or ""))

    no_permitidos = {m for m in modulos_top if m and m not in permitidos}
    assert not no_permitidos, (
        f"imports de nivel superior no permitidos en proyeccion_tramos_multiactivo: "
        f"{sorted(no_permitidos)} (permitidos: stdlib + dateutil)"
    )
