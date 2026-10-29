# -*- coding: utf-8 -*-
"""
Unit tests de UI de la feature `proyeccion-por-tramos-multiactivo` (tarea 7.5).

Cubre los criterios de la tarea:
  - `render_controles_tramos('por_tramos', ...)` genera un grid con UNA COLUMNA por tramo
    y emite los ids dinámicos esperados (`{'type': 'tramo-<var>', 'index': i}`) para todas
    las variables de `Inputs_Por_Tramo` (Req 1.1, 2.2).
  - `render_controles_tramos('punta_a_punta', ...)` devuelve vacío (retrocompatible, Req 2.3).
  - `app_dash` importa sin errores y `app_dash.app is not None` tras eliminar los botones
    individuales por activo; no existen los ids `btn-proy-rv/alt/cred` en el layout
    serializado (Req 3.1, 3.4, 9.3).

Notas de entorno:
  - `app_dash` se importa con `pytest.importorskip` (import pesado; si el entorno no lo
    tiene disponible, los tests se saltan sin romper la suite).
  - La fixture autouse `db_tmp` de conftest redirige la DB a un archivo temporal; estos
    tests NO tocan la `portafolio.db` real.
  - `render_controles_tramos` es un callback de Dash, pero la función subyacente sigue
    siendo invocable directamente con sus argumentos posicionales.
"""

from __future__ import annotations

import json

import pytest

# Import pesado del dashboard: saltar con gracia si no está disponible.
app_dash = pytest.importorskip("app_dash")


# ==============================================================================
# UTILIDADES: recorrido del árbol de componentes Dash
# ==============================================================================

def _iter_componentes(nodo):
    """
    Recorre recursivamente un árbol de componentes Dash (o listas anidadas) haciendo
    `yield` de cada componente. Un "componente" es cualquier objeto con atributo
    `children` (los componentes de dash) o los nodos hoja.
    """
    if nodo is None:
        return
    if isinstance(nodo, (list, tuple)):
        for x in nodo:
            yield from _iter_componentes(x)
        return
    # Nodo (componente o valor). Emitirlo.
    yield nodo
    # Descender por `children` si existe.
    hijos = getattr(nodo, "children", None)
    if hijos is not None:
        yield from _iter_componentes(hijos)


def _ids_por_tipo(arbol) -> dict:
    """
    Extrae del árbol un mapeo `{tipo: set(indices)}` para los ids dict-pattern
    (`{'type': ..., 'index': ...}`) de los inputs/dropdowns por tramo.
    """
    por_tipo: dict = {}
    for comp in _iter_componentes(arbol):
        cid = getattr(comp, "id", None)
        if isinstance(cid, dict) and "type" in cid and "index" in cid:
            por_tipo.setdefault(cid["type"], set()).add(cid["index"])
    return por_tipo


# Variables de `Inputs_Por_Tramo` que el grid por tramo debe emitir (Req 1.1).
_TIPOS_ESPERADOS = (
    "tramo-inflacion",
    "tramo-repo",
    "tramo-shift-cop",
    "tramo-shift-uvr",
    "tramo-shift-fs",
    "tramo-shift-ipc",
    "tramo-spread-ipc",
    "tramo-spread-tf",
    "tramo-trm",
    "tramo-ret-acc-local",
    "tramo-ret-alt-local",
    "tramo-ret-alt-intl",
    "tramo-ret-alt-ipc",
    "tramo-pei",
    "tramo-periodicidad",
)


# ==============================================================================
# 7.5 (a) — grid por tramo con los ids esperados en modo `por_tramos`
# ==============================================================================

def test_render_por_tramos_genera_una_columna_por_tramo_con_ids():
    """
    En `por_tramos`, `render_controles_tramos` genera el grid completo de
    `Inputs_Por_Tramo` con una columna por tramo: para el horizonte 2026-07-31 → 2028-12-31
    hay 3 tramos (parcial 2026, 2027, 2028), por lo que cada `tipo` debe emitir índices
    {0, 1, 2}. Se verifican todos los tipos de la tabla del usuario.
    """
    import proyeccion_tramos as ptr
    import pandas as pd

    fi, ff = "2026-07-31", "2028-12-31"
    n_tramos = len(ptr.calcular_tramos(
        pd.to_datetime(fi).date(), pd.to_datetime(ff).date()))
    assert n_tramos == 3  # tramo parcial (jul-dic 2026), 2027, 2028

    arbol = app_dash.render_controles_tramos("por_tramos", 1, fi, ff)
    assert arbol is not None

    por_tipo = _ids_por_tipo(arbol)

    indices_esperados = set(range(n_tramos))
    for tipo in _TIPOS_ESPERADOS:
        assert tipo in por_tipo, f"falta el tipo de input por tramo '{tipo}' en el grid"
        assert por_tipo[tipo] == indices_esperados, (
            f"'{tipo}' debe tener un input por tramo {indices_esperados}, "
            f"tiene {por_tipo[tipo]}"
        )


def test_render_por_tramos_regenera_columnas_al_cambiar_num_tramos():
    """
    Req 2.2: al cambiar el número de tramos (por cambio de fechas) el grid se regenera con
    exactamente una columna por tramo. Un horizonte de un solo año calendario ⇒ 1 tramo.
    """
    import proyeccion_tramos as ptr
    import pandas as pd

    fi, ff = "2027-01-01", "2027-12-31"
    n_tramos = len(ptr.calcular_tramos(
        pd.to_datetime(fi).date(), pd.to_datetime(ff).date()))
    assert n_tramos == 1

    arbol = app_dash.render_controles_tramos("por_tramos", 1, fi, ff)
    por_tipo = _ids_por_tipo(arbol)

    for tipo in _TIPOS_ESPERADOS:
        assert por_tipo.get(tipo) == {0}, (
            f"con 1 tramo, '{tipo}' debe tener solo el índice 0; tiene {por_tipo.get(tipo)}"
        )


# ==============================================================================
# 7.5 (b) — modo `punta_a_punta` devuelve vacío (retrocompatible)
# ==============================================================================

def test_render_punta_a_punta_devuelve_vacio():
    """
    Req 2.3 / retrocompatibilidad: en `punta_a_punta` no se generan controles por tramo
    (el layout actual de una sola columna se conserva); la función devuelve `[]`.
    """
    salida = app_dash.render_controles_tramos("punta_a_punta", 1, "2026-07-31", "2028-12-31")
    assert salida == []
    # Sin ids por tramo en la salida.
    assert _ids_por_tipo(salida) == {}


# ==============================================================================
# 7.5 (c) — arranque del dashboard sin callbacks colgados; sin botones individuales
# ==============================================================================

def test_app_dash_importa_y_app_no_es_none():
    """
    Req 9.3: tras eliminar los botones individuales por activo, `app_dash` importa sin
    errores y expone `app` (la app Dash quedó bien construida, sin callbacks colgados que
    rompan el arranque).
    """
    assert getattr(app_dash, "app", None) is not None


def test_layout_no_contiene_botones_individuales():
    """
    Req 3.2, 3.3, 9.3: los ids de los botones individuales por activo
    (`btn-proy-rv/alt/cred`) NO deben existir en el layout serializado; el botón único
    consolidado (`btn-correr-escenario`) SÍ debe existir.
    """
    layout = app_dash.layout_inputs()

    ids_encontrados = set()
    for comp in _iter_componentes(layout):
        cid = getattr(comp, "id", None)
        if isinstance(cid, str):
            ids_encontrados.add(cid)

    # Botones individuales eliminados (Req 3.2, 3.3).
    for prohibido in ("btn-proy-rv", "btn-proy-alt", "btn-proy-cred"):
        assert prohibido not in ids_encontrados, (
            f"el id '{prohibido}' de un botón individual sigue en el layout"
        )

    # Refuerzo: verificación sobre el layout serializado (JSON) por si algún id vive en
    # una subestructura no recorrida por `children`.
    def _serial(obj):
        # Serializa componentes Dash a un dict simple para el volcado a JSON.
        d = {}
        cid = getattr(obj, "id", None)
        if cid is not None:
            d["id"] = cid
        return d.get("id", str(obj))

    serial = json.dumps(layout, default=_serial)
    for prohibido in ("btn-proy-rv", "btn-proy-alt", "btn-proy-cred"):
        assert prohibido not in serial

    # El botón consolidado único debe seguir presente (Req 3.1, 3.4).
    assert "btn-correr-escenario" in ids_encontrados


def test_selector_modo_presente_en_inputs():
    """
    Req 2.1: el `Selector_Modo` (`modo-proyeccion`) está presente en la pestaña de Inputs
    (reubicado al inicio) y conserva el default `punta_a_punta`.
    """
    layout = app_dash.layout_inputs()
    modo_comp = None
    for comp in _iter_componentes(layout):
        if getattr(comp, "id", None) == "modo-proyeccion":
            modo_comp = comp
            break
    assert modo_comp is not None, "no se encontró el selector 'modo-proyeccion' en Inputs"
    # Default retrocompatible.
    assert getattr(modo_comp, "value", None) == "punta_a_punta"
