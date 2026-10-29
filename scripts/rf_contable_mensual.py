# -*- coding: utf-8 -*-
"""
scripts/rf_contable_mensual.py
==============================
Helper PURO y testeable con la lógica contable de Renta Fija NEG / DV / VC, extraída del
bloque "RF Mensual Contable" embebido en `app_dash._ejecutar_escenario` y consolidando la
regla contable que estaba DUPLICADA en `tools/detalle_pyg_tramo.py::construir_rf_detalle`.

Este módulo NO recalcula valoración ni muta el portafolio: solo LEE los atributos que el
motor (`scripts/motor_proyeccion.py::proyectar_portafolio`) ya dejó en cada `Titulo`
(`valor_mercado`, `valor_tir`, `_cupones_cobrados`, `clasificacion`,
`_snapshots_mensuales`) y en cada `ResultadoMes` (`mes`, `fecha`, `vm_total`,
`vtir_total`, `n_titulos`). Es extracción pura — mismos valores que la lógica embebida
original (equivalencia).

Regla contable (idéntica a la de app_dash y detalle_pyg_tramo):
  - NEG (Negociable):          total = (VM_fin − VM_ini) + cupones;  P&G = total;  ORI = 0
  - DV  (Disponible p/ Venta): total = (VM_fin − VM_ini) + cupones;
                               P&G = (VTIR_fin − VTIR_ini) + cupones;  ORI = total − P&G
  - VC  (al Vencimiento):      total = (VTIR_fin − VTIR_ini) + cupones;  P&G = total;  ORI = 0

Spec: `.kiro/specs/proyeccion-tramos-rf-mensual-y-vm-internacional/` (Tarea 4.1).
Requisitos: 2.7 (helper compartido y testeable), 3.6 (preservación de los valores
contables mensuales de la lógica embebida original).
"""
from __future__ import annotations

from typing import Any, Iterable


__all__ = [
    "categoria_contable",
    "descomponer_contable",
    "es_moneda_internacional",
    "construir_rf_mensual_contable",
]


def es_moneda_internacional(moneda: Any) -> bool:
    """Clasifica un título como RF Internacional (True) o RF Local (False) por su moneda.

    Replica EXACTAMENTE la regla de `tools/export_resumen_tramos._categoria`:
      `mon = (moneda or "").strip().upper()`; es internacional si
      `mon not in ("", "COP", "UVR")`. Es decir, LOCAL cuando la moneda (tras strip y
      upper, case-insensitive) está en {"", "COP", "UVR"}; INTERNACIONAL en cualquier otro
      caso.

    Args:
        moneda: valor crudo de `Titulo.moneda` (str o None).

    Returns:
        True si es RF Internacional, False si es RF Local.
    """
    mon = str(moneda if moneda is not None else "").strip().upper()
    return mon not in ("", "COP", "UVR")


def categoria_contable(clasificacion: Any) -> str:
    """Clasifica un título en su categoría contable NEG / DV / VC.

    Replica EXACTAMENTE el criterio de `app_dash` y `detalle_pyg_tramo`:
      - 'DV' si la clasificación menciona 'Disp' o 'Venta' (Disponible para la Venta).
      - 'VC' si menciona 'Venc' (al Vencimiento).
      - 'NEG' en cualquier otro caso (Negociable), que es el valor por defecto.

    Args:
        clasificacion: valor crudo de `Titulo.clasificacion` (str o None).

    Returns:
        'DV', 'VC' o 'NEG'.
    """
    clasif_str = str(clasificacion if clasificacion is not None else "").strip()
    if "Disp" in clasif_str or "Venta" in clasif_str:
        return "DV"
    if "Venc" in clasif_str:
        return "VC"
    return "NEG"


def descomponer_contable(
    categoria: str,
    vm_fin: float,
    vm_ini: float,
    vtir_fin: float,
    vtir_ini: float,
    cupones: float,
) -> tuple[float, float, float]:
    """Descompone el resultado contable de un título en (total_resultado, pyg, ori).

    Es el núcleo contable único reutilizado por el detalle por título (del tramo /
    punta a punta) y por la descomposición mensual. Mantiene la semántica exacta de la
    lógica embebida original:

      - NEG: todo a P&G (variación a mercado + cupones); ORI = 0.
      - DV:  total a mercado; P&G por causación (variación a TIR + cupones);
             ORI = total − P&G.
      - VC:  todo por causación a TIR; ORI = 0.

    Args:
        categoria: 'NEG', 'DV' o 'VC' (ver `categoria_contable`).
        vm_fin: valor de mercado final del período.
        vm_ini: valor de mercado inicial del período.
        vtir_fin: valor a TIR final del período.
        vtir_ini: valor a TIR inicial del período.
        cupones: cupones cobrados en el período.

    Returns:
        (total_resultado, pyg, ori).
    """
    if categoria == "NEG":
        total = (vm_fin - vm_ini) + cupones
        return total, total, 0.0
    if categoria == "DV":
        total = (vm_fin - vm_ini) + cupones
        pyg = (vtir_fin - vtir_ini) + cupones
        return total, pyg, total - pyg
    # VC
    total = (vtir_fin - vtir_ini) + cupones
    return total, total, 0.0


def construir_rf_mensual_contable(
    res_rf: Iterable[Any],
    port: Iterable[Any],
    fecha_fin: Any,
) -> list[dict]:
    """Construye la serie mensual contable de RF (`rf_mensual_contable`).

    Descompone mes a mes el P&G/ORI de los títulos vivos al final del horizonte usando la
    MISMA regla contable NEG/DV/VC que el detalle por título, de modo que la suma de la
    serie mensual coincide con la suma del detalle. Función pura: no muta `port` ni
    `res_rf`, solo lee sus atributos.

    Reglas (idénticas a la lógica embebida original en `_ejecutar_escenario`):
      - Solo se consideran títulos VIVOS al final (`fecha_vencimiento > fecha_fin`),
        los mismos que aparecen en `RF_Detalle_Titulo`.
      - Para cada mes `m` (1-indexado) se usan los snapshots por título
        `_snapshots_mensuales[m]` y `_snapshots_mensuales[m-1]`. Si falta cualquiera de
        los dos, el título se omite para ese mes (misma salvaguarda que el original).
      - El VM/VTIR total, el conteo y la fecha de la fila provienen del `ResultadoMes`.

    Args:
        res_rf: lista de `ResultadoMes` retornada por `proyectar_portafolio`.
        port: lista de `Titulo` tras la corrida (mutada por el motor con reinversiones).
        fecha_fin: fecha de fin del horizonte (`datetime.date`), usada para filtrar
            títulos vivos al cierre.

    Discriminación RF Local vs Internacional (Defecto 7 — Req 7.1, 7.2, 7bis). ADITIVO:
      Además de las columnas de TOTAL (que NO cambian de valor ni de nombre), cada fila
      incluye columnas `*_local` / `*_intl` que parten los agregados por moneda del título,
      usando la MISMA regla que `tools/export_resumen_tramos._categoria`
      (`es_moneda_internacional`: LOCAL si moneda ∈ {"", "COP", "UVR"} tras strip+upper,
      INTERNACIONAL en otro caso).

      INVARIANTE garantizada por construcción (se derivan por título):
        pyg_mes_local + pyg_mes_intl == pyg_mes
        ori_mes_local + ori_mes_intl == ori_mes
        total_resultado_mes_local + total_resultado_mes_intl == total_resultado_mes

      DECISIÓN DE DISEÑO sobre `vm_total` (importante): `vm_total` / `vtir_total` de la fila
      provienen de `r.vm_total` / `r.vtir_total` (totales del MOTOR en el `ResultadoMes`), NO
      de la suma por título. En cambio `vm_total_local` / `vm_total_intl` se computan como la
      suma por título de `snap_actual["vm"]` de los títulos del grupo (local/intl). Por tanto
      NO se exige la invariante estricta `vm_total == vm_total_local + vm_total_intl`: el total
      del motor puede diferir levemente de la suma por título si el motor agrega distinto (p.ej.
      títulos sin snapshot del mes, o diferencias de redondeo/agregación). `vm_total` se deja
      INTACTO (del motor); las columnas por grupo son una aproximación por título documentada.
      Para pyg/ori/total la invariante SÍ se cumple exactamente porque esos agregados ya se
      derivan por título en el helper.

    Returns:
        Lista de dicts con las claves de TOTAL (sin cambio): `mes`, `fecha` ('YYYY-MM'),
        `pyg_mes`, `ori_mes`, `total_resultado_mes`, `cupones_mes`, `vm_total`, `vtir_total`,
        `n_titulos`; más las columnas ADITIVAS por grupo: `pyg_mes_local`/`pyg_mes_intl`,
        `ori_mes_local`/`ori_mes_intl`,
        `total_resultado_mes_local`/`total_resultado_mes_intl`,
        `cupones_mes_local`/`cupones_mes_intl`, `vm_total_local`/`vm_total_intl`,
        `n_titulos_local`/`n_titulos_intl`.
    """
    port_list = list(port)

    # Set de títulos vivos al final (los mismos de RF_Detalle_Titulo).
    titulos_vivos_fin = {
        t.id_posicion for t in port_list if t.fecha_vencimiento > fecha_fin
    }

    rf_mensual_contable: list[dict] = []

    for m_idx, r in enumerate(res_rf):
        mes_num = m_idx + 1
        pyg_mes_total = 0.0
        ori_mes_total = 0.0
        total_mes_total = 0.0
        cupones_mes_total = 0.0

        # Acumuladores ADITIVOS por grupo (local / internacional). NO alteran los totales.
        pyg_local = pyg_intl = 0.0
        ori_local = ori_intl = 0.0
        total_local = total_intl = 0.0
        cupones_local = cupones_intl = 0.0
        vm_local = vm_intl = 0.0
        n_local = n_intl = 0

        for t in port_list:
            # Solo títulos que están vivos al final del horizonte.
            if t.id_posicion not in titulos_vivos_fin:
                continue
            snaps = getattr(t, "_snapshots_mensuales", {})
            # El título debe tener snapshot del mes actual y del mes anterior.
            if mes_num not in snaps:
                continue
            snap_actual = snaps[mes_num]
            snap_anterior = snaps.get(mes_num - 1, None)
            if snap_anterior is None:
                continue

            vm_act = snap_actual["vm"]
            vtir_act = snap_actual["vtir"]
            vm_ant = snap_anterior["vm"]
            vtir_ant = snap_anterior["vtir"]
            cup_mes_t = snap_actual.get("cupones", 0.0)

            cat_c = categoria_contable(getattr(t, "clasificacion", ""))
            total_t, pyg_t, ori_t = descomponer_contable(
                cat_c, vm_act, vm_ant, vtir_act, vtir_ant, cup_mes_t
            )

            pyg_mes_total += pyg_t
            ori_mes_total += ori_t
            total_mes_total += total_t
            cupones_mes_total += cup_mes_t

            # Partición ADITIVA por moneda (misma regla que _categoria).
            if es_moneda_internacional(getattr(t, "moneda", "")):
                pyg_intl += pyg_t
                ori_intl += ori_t
                total_intl += total_t
                cupones_intl += cup_mes_t
                vm_intl += vm_act
                n_intl += 1
            else:
                pyg_local += pyg_t
                ori_local += ori_t
                total_local += total_t
                cupones_local += cup_mes_t
                vm_local += vm_act
                n_local += 1

        rf_mensual_contable.append(
            {
                "mes": mes_num,
                "fecha": r.fecha.strftime("%Y-%m"),
                "pyg_mes": pyg_mes_total,
                "ori_mes": ori_mes_total,
                "total_resultado_mes": total_mes_total,
                "cupones_mes": cupones_mes_total,
                "vm_total": r.vm_total,
                "vtir_total": r.vtir_total,
                "n_titulos": r.n_titulos,
                # ── Columnas ADITIVAS por grupo (Defecto 7). Totales intactos arriba. ──
                "pyg_mes_local": pyg_local,
                "pyg_mes_intl": pyg_intl,
                "ori_mes_local": ori_local,
                "ori_mes_intl": ori_intl,
                "total_resultado_mes_local": total_local,
                "total_resultado_mes_intl": total_intl,
                "cupones_mes_local": cupones_local,
                "cupones_mes_intl": cupones_intl,
                # vm_total_* es suma por título (ver DECISIÓN DE DISEÑO en el docstring);
                # NO se exige vm_total == vm_total_local + vm_total_intl.
                "vm_total_local": vm_local,
                "vm_total_intl": vm_intl,
                "n_titulos_local": n_local,
                "n_titulos_intl": n_intl,
            }
        )

    return rf_mensual_contable
