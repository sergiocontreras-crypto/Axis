"""
tools/export_resumen_tramos.py
==============================
Genera, por cada corte de tramo persistido en `portafolio_clasificado_proy`, un resumen
de P&G por CATEGORÍA de activo × compañía × ramo, con el P&G DEL TRAMO (del período) y el
EFECTO MONEDA separado para los activos internacionales.

Enfoque (sin tocar motores): el P&G del tramo de una posición es la diferencia de su
valor de mercado entre el cierre del tramo y el cierre del tramo anterior (o el VM inicial
del portafolio base para el primer tramo). Se agrega por categoría × compañía × ramo.

Categorías (9):
  RF Local, RF Internacional, Swaps, Forwards, Opciones,
  RV Local, RV Internacional, Alt Local, Alt Internacional.

Lee la BD en modo SOLO LECTURA: no interfiere con una corrida en curso. El ramo/compañía
de CADA posición (incluidos no-RF) se toma de `portafolio_clasificado` (fuente única de
clasificación), resolviendo el enlace de id para derivados (`tipo:NNNN` -> NNNN).

NOTA (Defecto D2, Ola 3 — RV/Alt por id_posicion): los motores de RV/Alternativos no
conservan ramo/compañía, pero su P&G por posición del snapshot viene por `id_posicion`
propio. El resumen AGREGA por `id_posicion` (UNA fila por posición, SIN prorrateo),
heredando ramo/compañía/moneda del base por id (mismo patrón que los derivados). Antes se
agregaba por especie y se prorrateaba el P&G entre las posiciones del base de esa especie
según VM inicial; eso colapsaba N posiciones distintas con la misma especie y rompía la
reconciliación. Retrocompatibilidad: si el snapshot no expone `id_posicion`, se usa
`titulo` como clave de agregación (comportamiento previo).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


# ── Clasificación de categoría a partir de atributos del portafolio_clasificado ──
def _categoria(clase_origen: str, moneda: str, asset_class: str,
               es_subyacente: bool, es_pei: bool = False) -> str:
    co = (clase_origen or "").strip().upper()
    ac = (asset_class or "").strip()
    mon = (moneda or "").strip().upper()
    es_intl = mon not in ("", "COP", "UVR")

    if co == "SWAP":
        return "Swaps"
    if co == "FORWARD":
        return "Forwards"
    if co == "OPCION":
        return "Opciones"
    if co == "RENTA_VARIABLE":
        # Defecto 2: el PEI es categoría propia; "RV Local" queda solo con COLCAP (no PEI).
        # El PEI se identifica por el dato asset_class_definitivo='PEI' (vía flag es_pei o
        # el propio asset_class), nunca por un literal de especie embebido (Req 5.7).
        if es_pei or ac.upper() == "PEI":
            return "PEI"
        return "RV Internacional" if es_intl else "RV Local"
    if co == "ALTERNATIVO":
        return "Alt Internacional" if es_intl else "Alt Local"
    # RF (clase_origen TITULO o vacío). Los SUBYACENTES de swap van en categoría propia
    # (petición del usuario): se separan del resto de RF internacional.
    if es_subyacente:
        return "Subyacentes de Swaps"
    return "RF Internacional" if es_intl else "RF Local"


_CATEGORIAS_INTL = {"RF Internacional", "RV Internacional", "Alt Internacional"}


def _mapa_clasificacion(conn) -> pd.DataFrame:
    """Mapa por id_posicion con ramo/compania/moneda/asset_class/clase del base."""
    cols = [d[1] for d in conn.execute("PRAGMA table_info(portafolio_clasificado)")]
    ac = "asset_class_definitivo" if "asset_class_definitivo" in cols else None
    idc = "id_cobertura" if "id_cobertura" in cols else None
    sel = ["id_posicion", "especie", "ramo", "compania", "moneda", "clase_origen"]
    if ac:
        sel.append(ac)
    if idc:
        sel.append(idc)
    df = pd.read_sql_query(
        f"SELECT {', '.join(sel)} FROM portafolio_clasificado", conn
    )
    df["id_posicion"] = df["id_posicion"].astype(str).str.strip()
    if ac and ac != "asset_class":
        df = df.rename(columns={ac: "asset_class"})
    if "asset_class" not in df.columns:
        df["asset_class"] = ""
    # Defecto 2: flag PEI derivado del dato asset_class (= asset_class_definitivo). Default
    # defensivo False si la columna faltara. Identifica el PEI sin literales de especie.
    df["es_pei"] = df["asset_class"].astype(str).str.strip().str.upper().eq("PEI")
    if idc:
        df["es_subyacente"] = df[idc].astype(str).str.strip().ne("")
    else:
        df["es_subyacente"] = False
    return df


def _id_base_desde_snapshot(id_snap: str) -> str:
    """Normaliza el id del snapshot al id_posicion del base.
    Derivados del snapshot vienen como 'swaps:1218' / 'forwards:NNN' / 'opciones:NNN'."""
    s = str(id_snap).strip()
    for pref in ("swaps:", "forwards:", "opciones:"):
        if s.lower().startswith(pref):
            return s.split(":", 1)[1].strip()
    return s


def _resumen_rf(rf_detalle, df_clasif) -> pd.DataFrame:
    """Resumen RF por categoria x compania x ramo x clasificacion desde rf_detalle.
    rf_detalle ya trae ramo/compania/moneda por titulo (del motor). Categoria RF Local/Int
    por moneda.

    Defecto 4 (Req 4.1/4.2): esta es la ÚNICA vía de agregación RF. Consume el MISMO
    `rf_detalle` por título que alimenta la hoja `RF_Detalle_Titulo_<corte>` (fuente única,
    sin recálculo ni vía alterna). Como `TotalCategoria_<corte>` es un `groupby` de
    `ResumenPyG_<corte>`, por asociatividad de la suma la agregación por categoría reconcilia
    por construcción con el detalle por título. El ruteo de categoría usa `_categoria` sobre
    el `es_subyacente` del propio `rf_detalle` (Defecto 1) y su `asset_class`/moneda, por lo
    que subyacentes ("Subyacentes de Swaps") y RF Local/Internacional quedan coherentes."""
    if not rf_detalle:
        return pd.DataFrame()
    df = pd.DataFrame(rf_detalle)
    df["categoria"] = df.apply(
        lambda r: _categoria("TITULO", r.get("moneda"), r.get("asset_class"),
                              bool(r.get("es_subyacente"))), axis=1)
    # Los subyacentes de swap (id_cobertura no vacío) quedan en su categoría propia
    # "Subyacentes de Swaps", separados del resto de RF internacional (petición del usuario).
    g = df.groupby(["categoria", "compania", "ramo", "clasificacion"], dropna=False).agg(
        pyg=("pyg", "sum"), ori=("ori", "sum"),
        total_resultado=("total_resultado", "sum"),
        vm_inicial=("vm_inicial", "sum"), vm_final=("vm_mercado", "sum"),
        n=("id_posicion", "count"),
    ).reset_index()
    return g


def _resumen_no_rf(registro, df_clasif) -> pd.DataFrame:
    """Resumen de RV/Alt/derivados por categoria x compania x ramo. El ramo/compania se
    hereda del base por id (derivados: 'tipo:NNNN' -> NNNN; RV/Alt: por `id_posicion`,
    identidad de Spec A).

    Defecto D2 (Ola 3): RV/Alt se agregan por `id_posicion` (UNA fila por posición, SIN
    prorrateo por especie), heredando ramo/compañía/moneda del base por id igual que los
    derivados. Antes se agregaba por `titulo`/especie y se prorrateaba el P&G entre las
    posiciones del base de la especie según VM inicial; eso colapsaba N posiciones distintas
    con la misma especie y rompía la reconciliación. Retrocompatibilidad: si el snapshot no
    expone `id_posicion`, se usa `titulo` como clave de agregación (comportamiento previo).

    LIMITACIÓN CONOCIDA (Defecto 4, Req 4.4/4.5): las posiciones no-RF (renta variable,
    alternativos y derivados) NO tienen descomposición contable NEG/DV/VC (esa regla, en
    scripts/rf_contable_mensual.py, aplica solo a renta fija). Por ello, por definición del
    informe, TODA fila no-RF reporta ORI = 0.0 explícito y asigna todo el resultado a P&G,
    es decir `total_resultado == pyg`. Este invariante se mantiene en todas las ramas de
    abajo (derivados, RV y Alt) y NO afecta la reconciliación RF, que se construye por
    separado desde el mismo `rf_detalle` por título en `_resumen_rf` (fuente única)."""
    filas = []
    trm_ini = registro.get("trm_inicio")
    trm_cie = registro.get("trm_cierre")
    var_trm = ((trm_cie / trm_ini) - 1.0) if (trm_ini and trm_cie) else 0.0

    # Mapa id_posicion -> (ramo, compania, moneda) del base.
    base = df_clasif.set_index(df_clasif["id_posicion"].astype(str))

    # ── Derivados: P&G DEL TRAMO por tipo (Swaps/Forwards/Opciones) desde deriv_pyg ──
    # deriv_pyg es [{fecha_valoracion, tipo, p_y_g}]; el P&G del tramo es el p_y_g del
    # ULTIMO corte de cada tipo. LIMITACION (deuda tecnica, spec formal): el P&G de
    # derivados se reporta a nivel de TIPO (total), no abierto por compania/ramo, porque
    # el motor de derivados no expone ramo por contrato. El VM de cierre por posicion
    # (deriv_vm_cierre) se usa como informativo (vm_final), heredando ramo del base por id.
    _pyg_deriv = registro.get("deriv_pyg") or []
    if _pyg_deriv:
        _dfp = pd.DataFrame(_pyg_deriv)
        if {"fecha_valoracion", "tipo", "p_y_g"}.issubset(_dfp.columns):
            _idx = _dfp.groupby("tipo")["fecha_valoracion"].idxmax()
            for _, r in _dfp.loc[_idx].iterrows():
                _cat = {"Swaps": "Swaps", "Forwards": "Forwards",
                        "Opciones": "Opciones"}.get(str(r["tipo"]), None)
                if _cat is None:
                    continue  # 'Activos' no es una categoria de derivado por si misma
                # Defecto 4: derivado es no-RF -> ORI = 0.0 explícito y total_resultado == pyg.
                _pyg_der = float(r["p_y_g"] or 0.0)
                filas.append({"categoria": _cat, "compania": "", "ramo": "",
                              "clasificacion": "", "pyg": _pyg_der,
                              "ori": 0.0, "total_resultado": _pyg_der,
                              "vm_final": 0.0, "n": 1})
    # VM de cierre por posicion de derivados (informativo, con ramo del base por id).
    for pos_id, vm in (registro.get("deriv_vm_cierre") or {}).items():
        id_base = _id_base_desde_snapshot(pos_id)
        tipo = str(pos_id).split(":", 1)[0].lower() if ":" in str(pos_id) else ""
        cat = {"swaps": "Swaps", "forwards": "Forwards", "opciones": "Opciones"}.get(tipo, "Swaps")
        ramo = compania = ""
        if id_base in base.index:
            fila = base.loc[[id_base]].iloc[0]
            ramo = fila.get("ramo", "")
            compania = fila.get("compania", "")
        filas.append({"categoria": cat, "compania": compania, "ramo": ramo,
                      "clasificacion": "VM_cierre_informativo", "pyg": 0.0, "ori": 0.0,
                      "total_resultado": 0.0, "vm_final": float(vm or 0.0), "n": 1})

    # ── RV / Alt: agregación por id_posicion heredando ramo/compañía/moneda del base ──
    # Defecto D2 (Ola 3): el P&G de RV/Alt se agrega por `id_posicion` (identidad de Spec A),
    # UNA fila por posición y SIN prorrateo por especie. Esto corrige la reconciliación
    # cuando N posiciones (id_posicion distintos) comparten la MISMA especie: antes el
    # groupby por `titulo` las colapsaba y el P&G de las colapsadas se perdía. El ramo/
    # compañía/moneda se heredan del base por id (mismo patrón que los derivados con
    # `base = df_clasif.set_index("id_posicion")`); si el id no está en el base se cae a los
    # valores del propio snapshot (falla-seguro). Retrocompatibilidad: si el payload no trae
    # `id_posicion`, se usa `titulo` como clave de agregación (comportamiento previo).
    for bloque, key, es_rv in (("RV", "rv", True), ("ALT", "alt", False)):
        regs = registro.get(key) or []
        if not regs:
            continue
        df = pd.DataFrame(regs)
        # Clave de identidad: preferir `id_posicion` (Spec A); `titulo` solo como fallback
        # retrocompatible cuando el snapshot no expone `id_posicion`.
        col_id = "id_posicion" if "id_posicion" in df.columns else ("titulo" if "titulo" in df.columns else None)
        if col_id is None or "valor_mercado" not in df.columns:
            continue
        # Último corte POR posición (VM/P&G de cierre del tramo), una fila por `col_id`.
        if "fecha_proyeccion" in df.columns:
            idx = df.groupby(col_id)["fecha_proyeccion"].idxmax()
            df = df.loc[idx]
        for _, r in df.iterrows():
            id_pos = str(r[col_id]).strip()
            pyg = float(r.get("pyg_acumulado", r.get("pyg_mensual", 0.0)) or 0.0)
            # Herencia del base por id (ramo/compañía/moneda/es_pei). Falla-seguro: si el id
            # no está en el base, moneda del snapshot (default COP) y ramo/compañía vacíos.
            ramo = compania = ""
            moneda = str(r.get("moneda", "COP")).strip()
            es_pei = False
            if id_pos in base.index:
                fila_base = base.loc[[id_pos]].iloc[0]
                ramo = fila_base.get("ramo", "") or ""
                compania = fila_base.get("compania", "") or ""
                mon_base = fila_base.get("moneda", "")
                if mon_base is not None and str(mon_base).strip():
                    moneda = str(mon_base).strip()
                es_pei = bool(fila_base.get("es_pei", False)) if "es_pei" in base.columns else False
            es_intl = moneda.upper() not in ("", "COP", "UVR")
            if es_rv:
                # Defecto 2: el flag PEI se resuelve desde el base por id (no por especie);
                # una posición PEI va a la categoría propia "PEI", nunca bajo "RV Local".
                cat = _categoria("RENTA_VARIABLE", moneda,
                                 "PEI" if es_pei else "",
                                 es_subyacente=False, es_pei=es_pei)
            else:
                cat = "Alt Internacional" if es_intl else "Alt Local"
            # Defecto 4: RV/Alt es no-RF -> ORI = 0.0 explícito y total_resultado == pyg.
            # UNA fila por posición con su P&G propio (sin prorrateo).
            ef_moneda = pyg * (var_trm / (1 + var_trm)) if es_intl and var_trm else 0.0
            filas.append({
                "categoria": cat, "compania": compania, "ramo": ramo,
                "clasificacion": "", "pyg": pyg, "ori": 0.0,
                "total_resultado": pyg, "vm_final": 0.0,
                "efecto_moneda": ef_moneda, "n": 1})
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame(filas)
    agg = {"pyg": "sum", "ori": "sum", "total_resultado": "sum", "vm_final": "sum", "n": "sum"}
    if "efecto_moneda" in df.columns:
        agg["efecto_moneda"] = "sum"
    return df.groupby(["categoria", "compania", "ramo", "clasificacion"], dropna=False).agg(agg).reset_index()


def generar_resumen_por_tramo(db_path: str):
    """Lee informe_pyg_por_tramo + portafolio_clasificado y arma, por corte, el resumen
    por categoria x compania x ramo. Devuelve {fecha_corte: DataFrame}."""
    import json
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        df_clasif = _mapa_clasificacion(conn)
        try:
            registros = list(conn.execute(
                "SELECT fecha_corte, payload_json FROM informe_pyg_por_tramo ORDER BY fecha_corte"))
        except Exception:
            registros = []
    finally:
        conn.close()

    # NOTA (Defecto 5, Fase 2): se retiró la columna `advertencia` estática del "bug VM-USD".
    # El Defecto 2 ("VM de cierre USD sin ×TRM") fue CERRADO como NO REPRODUCIBLE (ver
    # docs/deuda-tecnica.md sección RESUELTO, Tarea 5.2 y
    # tests/test_integracion_vm_cierre_usd_multitramo.py): el snapshot oficial ya registra el
    # VM de cierre USD en escala COP (×TRM). Esa advertencia era un residuo engañoso, así que
    # se eliminó junto con la constante `_CATS_USD_EN_REVISION`.
    salida = {}
    for fecha_corte, payload in registros:
        reg = json.loads(payload)
        partes = []
        rf = _resumen_rf(reg.get("rf_detalle", []), df_clasif)
        if not rf.empty:
            partes.append(rf)
        no_rf = _resumen_no_rf(reg, df_clasif)
        if not no_rf.empty:
            partes.append(no_rf)
        if partes:
            df = pd.concat(partes, ignore_index=True)
            salida[fecha_corte] = df
    return salida
