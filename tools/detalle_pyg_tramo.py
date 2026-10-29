"""
tools/detalle_pyg_tramo.py
==========================
Construccion del DETALLE de P&G/ORI por titulo de RF DEL TRAMO, reutilizando EXACTAMENTE
la misma logica contable (NEG / DV / VC) que el flujo punta a punta de app_dash
(`_ejecutar_escenario`, bloque `rf_detalle`). NO recalcula valoracion: solo LEE los
atributos que el motor ya dejo en cada `Titulo` tras `proyectar_portafolio`
(valor_mercado, valor_tir, _cupones_cobrados, clasificacion). Es extraccion pura.

Regla contable (identica a app_dash):
  - NEG (Negociable):          total = (VM_fin - VM_ini) + cupones;  P&G = total;  ORI = 0
  - DV  (Disponible p/ Venta): total = (VM_fin - VM_ini) + cupones;
                               P&G = (VTIR_fin - VTIR_ini) + cupones;  ORI = total - P&G
  - VC  (al Vencimiento):      total = (VTIR_fin - VTIR_ini) + cupones;  P&G = total;  ORI = 0

DEFENSA DOCUMENTADA VM-USD — IDEMPOTENTE (ver docs/deuda-tecnica.md seccion
"RESUELTO — VM de cierre de titulos USD (Yankees) en COP (por tramos) — NO REPRODUCIBLE" y
el spec `.kiro/specs/proyeccion-tramos-rf-mensual-y-vm-internacional`, Defecto 2):

El sintoma reportado originalmente ("el motor dejaba el VM de cierre de los Yankees/USD en
escala USD, sin ×TRM") fue INVESTIGADO a fondo (motor + orquestacion + encadenamiento +
persistencia) y CERRADO como NO REPRODUCIBLE. El motor y el snapshot oficial
`portafolio_clasificado_proy` ya aplican ×TRM correctamente en todas las ramas USD del bucle
mensual (TIME DEP, VISTA USD, YANKEE via `valorar_titulo`, FIC), de modo que el VM de cierre
de los titulos USD ya queda en escala COP, consistente con el VM inicial. Esta conclusion esta
blindada por el test de integracion multi-tramo
`tests/test_integracion_vm_cierre_usd_multitramo.py` (Tarea 5.1), que verifica el VM en COP a
traves del motor real y del round-trip de persistencia en el tramo 0 y en el tramo i>0.

Como el snapshot oficial ya esta en COP, la re-valoracion que hace este informe para titulos
en moneda extranjera (via `valorar_titulo(..., trm=trm_cierre, valor_uvr=uvr_cierre)`) es
IDEMPOTENTE: re-valora a un COP que coincide (dentro de tolerancia) con el `valor_mercado` que
ya trae el objeto. Se CONSERVA deliberadamente como una red de seguridad barata del P&G del
informe: si en el futuro reapareciera una ruta que dejara el VM de un titulo USD en escala USD,
esta re-valoracion lo corregiria SOLO en el informe, sin tocar el motor ni el snapshot oficial.
Mientras el motor sea correcto (lo es hoy), no cambia el resultado.

CONSOLIDACION (spec proyeccion-tramos-rf-mensual-y-vm-internacional, Tarea 4.1): la regla
contable NEG/DV/VC que antes estaba DUPLICADA aqui y en `app_dash._ejecutar_escenario` se
extrajo al helper puro compartido `scripts/rf_contable_mensual.py`
(`categoria_contable` + `descomponer_contable`). Este modulo ahora REUTILIZA ese helper como
unico punto de verdad de la regla contable; la clasificacion y la descomposicion P&G/ORI
provienen de alli. Lo especifico de este modulo (filtro de titulos vivos, armado del dict de
detalle y la mitigacion VM-USD) se conserva.
"""
from __future__ import annotations

try:  # pragma: no cover - resolucion de ruta segun contexto de ejecucion
    from rf_contable_mensual import categoria_contable, descomponer_contable
except ImportError:  # pragma: no cover
    import os as _os
    import sys as _sys

    _scripts_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "scripts"
    )
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    from rf_contable_mensual import categoria_contable, descomponer_contable


def snapshot_inicial(port):
    """Captura VM y VTIR iniciales por titulo ANTES de correr el motor del tramo."""
    vm_ini = {t.id_posicion: t.valor_mercado for t in port}
    vtir_ini = {
        t.id_posicion: (t.valor_tir if getattr(t, "valor_tir", None) else t.valor_mercado)
        for t in port
    }
    return vm_ini, vtir_ini


def _categoria_contable(clasificacion) -> str:
    """Alias de compatibilidad: delega en el helper compartido `categoria_contable`."""
    return categoria_contable(clasificacion)


def _es_moneda_extranjera(moneda) -> bool:
    return str(moneda or "COP").strip().upper() not in ("", "COP", "UVR")


def construir_rf_detalle(port, vm_inicial_por_titulo, vtir_inicial_por_titulo, fecha_fin,
                         trm_cierre=None, uvr_cierre=None):
    """Lista de dicts por titulo vivo al cierre del tramo, con P&G/ORI del tramo.
    Logica contable IDENTICA a app_dash (NEG/DV/VC). Para titulos en moneda extranjera,
    si se pasa `trm_cierre`, RE-VALORA el VM de cierre con `valorar_titulo` como DEFENSA
    DOCUMENTADA e IDEMPOTENTE (no un parche de un bug activo): el motor y el snapshot oficial
    ya dejan el VM en escala COP, por lo que esta re-valoracion coincide con `valor_mercado`
    y solo actuaria como red de seguridad del informe si una ruta futura dejara el VM en USD
    (ver docstring del modulo y docs/deuda-tecnica.md seccion RESUELTO)."""
    _valorar_titulo = None
    if trm_cierre:
        try:
            from motor_proyeccion import valorar_titulo as _valorar_titulo
        except Exception:
            _valorar_titulo = None

    detalle = []
    for t in port:
        if t.fecha_vencimiento <= fecha_fin:
            continue
        vm_ini = vm_inicial_por_titulo.get(t.id_posicion, 0)
        if vm_ini == 0 and getattr(t, "es_reinversion", False):
            vm_ini = getattr(t, "vm_compra", t.nominal)
        vm_mercado = t.valor_mercado
        vm_tir = t.valor_tir if getattr(t, "valor_tir", None) else vm_ini
        vm_tir_ini = vtir_inicial_por_titulo.get(t.id_posicion, vm_ini)
        moneda = getattr(t, "moneda", "COP")

        # ── Re-valoracion del VM de cierre para moneda extranjera (defensa idempotente) ──
        # Solo para el informe y DOCUMENTADA como defensa, no como parche de un bug activo:
        # el Defecto 2 ("VM de cierre USD sin ×TRM") fue cerrado como NO REPRODUCIBLE — el
        # motor y el snapshot oficial ya dejan el VM en escala COP (ver docstring del modulo,
        # docs/deuda-tecnica.md seccion RESUELTO y tests/test_integracion_vm_cierre_usd_multitramo.py).
        # Por eso esta re-valoracion es IDEMPOTENTE (coincide con t.valor_mercado). Se conserva
        # como red de seguridad: si una ruta futura dejara el VM de un USD en escala USD, aqui
        # se corrige SOLO en el informe. Se re-valora a la fecha de cierre del tramo con la TRM
        # (y UVR) de cierre. Si la re-valoracion falla, se cae al VM del objeto.
        _revaluado = False
        if _valorar_titulo is not None and _es_moneda_extranjera(moneda):
            try:
                _kwargs = {"titulo": t, "tasa_mercado_nueva": getattr(t, "tasa_mercado", 0.0),
                           "fecha_valuacion": fecha_fin, "trm": float(trm_cierre)}
                if uvr_cierre:
                    _kwargs["valor_uvr"] = float(uvr_cierre)
                _vm_rev = _valorar_titulo(**_kwargs)
                if _vm_rev and _vm_rev > 0:
                    vm_mercado = float(_vm_rev)
                    _revaluado = True
            except Exception:
                pass

        cat = categoria_contable(getattr(t, "clasificacion", ""))
        cupones = getattr(t, "_cupones_cobrados", 0.0)
        # Regla contable unica compartida (NEG/DV/VC): mismo nucleo que la serie mensual
        # y que el detalle punta a punta (helper `rf_contable_mensual`).
        total, pyg, ori = descomponer_contable(
            cat, vm_mercado, vm_ini, vm_tir, vm_tir_ini, cupones
        )

        detalle.append({
            "id_posicion": str(t.id_posicion),
            "especie": getattr(t, "especie", ""),
            "compania": getattr(t, "compania", ""),
            "ramo": getattr(t, "ramo", ""),
            "asset_class": getattr(t, "asset_class", ""),
            "clasificacion": cat,
            "moneda": moneda,
            "es_subyacente": bool(str(getattr(t, "id_cobertura", "") or "").strip()),
            "vm_inicial": float(vm_ini),
            "vm_mercado": float(vm_mercado),
            "vm_revaluado_usd": _revaluado,
            "pyg": float(pyg),
            "ori": float(ori),
            "total_resultado": float(total),
        })
    return detalle
