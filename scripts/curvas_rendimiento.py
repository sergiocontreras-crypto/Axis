"""
AXIS — Módulo de Curvas de Rendimiento (Rediseño v2)
=====================================================
Gestiona curvas de rendimiento usando:
  - Deuda Pública: nodos por ESPECIE (TFIT/TFVT/TUVT del PIP)
  - Corporativos: nodos por AÑO DE VENCIMIENTO

Inputs en producción:
  1. portafolio_clasificado (SQLite) → asset_class_definitivo
  2. Archivo PIP (CSV) → rendimientos por especie

Funcionalidades:
  - Carga de curvas base desde PIP (por especie DP, por año Corp)
  - Interpolación lineal entre nodos
  - Generación de senda lineal entre T+0 y target
  - Shocks individuales por nodo
  - Asignación de curva según asset_class_definitivo

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd


# ==============================================================================
# TIPOS Y CONSTANTES
# ==============================================================================

# Curva por nodo numérico (días) → tasa (%)
Curva = dict[int, float]

# Curva por nombre de especie → tasa (%) — para DP
CurvaEspecie = dict[str, float]

# Curva por año de vencimiento → tasa (%) — para Corporativos
CurvaAnio = dict[int, float]

# Nombres estándar de curvas soportadas
CURVAS_VALIDAS = [
    "DP_TF",       # Deuda Pública Tasa Fija (TES)
    "DP_UVR",      # Deuda Pública UVR (TES UVR)
    "CORP_IPC",    # Corporativos indexados a IPC
    "CORP_TF",     # Corporativos Tasa Fija (CDTs, bonos)
    "USD",         # Yankees / deuda en USD
    "VISTA",       # Depósitos a la vista (tasa plana)
]

# Mapeo: asset_class_definitivo → nombre de curva
ASSET_CLASS_A_CURVA = {
    # Deuda Pública TF
    "DP LP": "DP_TF",
    "DP MP": "DP_TF",
    "DP CP": "DP_TF",
    # Deuda Pública UVR
    "DP UVR LP": "DP_UVR",
    "DP UVR MP": "DP_UVR",
    "DP UVR CP": "DP_UVR",
    # Corporativos IPC (SF = Sector Financiero, SR = Sector Real)
    # Son bonos que pagan spread + IPC. Su yield de descuento correlaciona
    # con la curva corporativa TF, por eso CORP_IPC = proxy de CORP_TF.
    "SF IPC LP": "CORP_IPC",
    "SF IPC CP": "CORP_IPC",
    "SF IPC MP": "CORP_IPC",
    "SR IPC LP": "CORP_IPC",
    "SR IPC CP": "CORP_IPC",
    "SR IPC MP": "CORP_IPC",
    # Corporativos Tasa Fija (CDTs, bonos FS = Financiero + Real Fija)
    "SRyF FS LP": "CORP_TF",
    "SRyF FS CP": "CORP_TF",
    "SRyF FS MP": "CORP_TF",
    # USD / Yankees
    "LEGATRUU": "USD",
    # Estructuras (subyacentes de swaps — misma curva USD)
    "Estructuras": "USD",
    # Cuasi-monetarios
    "Q1": "CORP_TF",
    "Q234": "CORP_TF",
    # Vista
    "VISTA": "VISTA",
    "VISTA USD": "VISTA",
}


# ==============================================================================
# FUNCIÓN 1: EXTRAER CURVAS BASE DESDE PIP (REDISEÑADO)
# ==============================================================================

def extraer_curva_base_pip(ruta_pip: str = "Preciospip_Cons_T+0_20260522.csv") -> dict[str, Curva]:
    """
    Extrae curvas base desde el archivo PIP.

    Deuda Pública (DP_TF, DP_UVR): nodos = días al vencimiento (uno por especie).
        Internamente guarda {dias: tasa} para interpolación numérica.
    Corporativos (CORP_TF, CORP_IPC): nodos = año de vencimiento promedio.
        Internamente guarda {dias_medio_del_año: tasa_promedio}.
    USD: nodos = días al vencimiento.

    Args:
        ruta_pip: Ruta al archivo CSV de precios PIP.

    Returns:
        Diccionario de curvas {nombre_curva: {plazo_dias: tasa_rendimiento}}
    """
    ruta_path = Path(ruta_pip)
    if not ruta_path.exists():
        return {}

    # El PIP tiene una coma extra en cada fila de datos vs el header
    with open(ruta_path, "r", encoding="utf-8") as f:
        header_line = f.readline().strip()
    col_names = header_line.split(",") + ["_EXTRA"]

    df = pd.read_csv(
        ruta_path, encoding="utf-8", low_memory=False,
        on_bad_lines="skip", names=col_names, skiprows=1,
    )
    df.columns = df.columns.str.strip()
    df["NEMO"] = df["NEMO"].astype(str).str.strip()
    df["RENDIMIENTO"] = pd.to_numeric(df["RENDIMIENTO"], errors="coerce")
    df["DIAS_AL_VENCIMIENTO"] = pd.to_numeric(df["DIAS_AL_VENCIMIENTO"], errors="coerce")
    df["FECHA_VENCIMIENTO"] = df["FECHA_VENCIMIENTO"].astype(str).str.strip()
    df = df[df["RENDIMIENTO"] > 0].copy()
    df = df[df["DIAS_AL_VENCIMIENTO"] > 0].copy()

    curvas: dict[str, Curva] = {}

    # ── Deuda Pública TF (TFIT, TFVT) ──
    mask_tf = df["NEMO"].str.startswith("TFIT") | df["NEMO"].str.startswith("TFVT")
    for _, row in df[mask_tf].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("DP_TF", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    # ── Deuda Pública UVR (TUVT) ──
    mask_uvr = df["NEMO"].str.startswith("TUVT")
    for _, row in df[mask_uvr].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("DP_UVR", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    # ── Yankees (BGLT) ──
    mask_usd = df["NEMO"].str.startswith("BGLT")
    for _, row in df[mask_usd].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("USD", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    # ── Corporativos por año de vencimiento ──
    # Solo aplica como curva de descuento para bonos TASA FIJA corporativos
    # (CDTs, bonos FS). Los bonos IPC se valoran con spread + IPC, pero
    # su yield de descuento correlaciona con esta curva, así que se usa
    # también como proxy.
    mask_corp = (~mask_tf) & (~mask_uvr) & (~mask_usd)
    df_corp = df[mask_corp].copy()
    df_corp["anio_vcto"] = df_corp["FECHA_VENCIMIENTO"].str[:4].astype(int)

    # Promedio de rendimiento por año de vencimiento
    resumen_corp = df_corp.groupby("anio_vcto").agg(
        tasa_media=("RENDIMIENTO", "median"),
        dias_medio=("DIAS_AL_VENCIMIENTO", "median"),
    ).reset_index()

    for _, row in resumen_corp.iterrows():
        curvas.setdefault("CORP_TF", {})[int(row["dias_medio"])] = float(row["tasa_media"])

    # CORP_IPC: no existe como curva propia en el PIP.
    # Se usa CORP_TF como proxy de la yield de descuento.
    if "CORP_TF" in curvas:
        curvas["CORP_IPC"] = dict(curvas["CORP_TF"])

    return curvas


# ==============================================================================
# FUNCIÓN 1B: EXTRAER NODOS CON NOMBRES LEGIBLES (para dashboard)
# ==============================================================================

def extraer_curva_con_nombres(ruta_pip: str = "Preciospip_Cons_T+0_20260522.csv") -> dict[str, pd.DataFrame]:
    """
    Extrae curvas con nodos nombrados para visualización en dashboard.

    Deuda Pública: nodos = nombre de especie (TFIT16180930, TUVT20250333, etc.)
    Corporativos: nodos = año de vencimiento (2026, 2027, 2028, etc.)

    Returns:
        Dict {nombre_curva: DataFrame[nodo, tasa, dias]}
        Donde 'nodo' es el nombre legible (especie o año).
    """
    ruta_path = Path(ruta_pip)
    if not ruta_path.exists():
        return {}

    with open(ruta_path, "r", encoding="utf-8") as f:
        header_line = f.readline().strip()
    col_names = header_line.split(",") + ["_EXTRA"]

    df = pd.read_csv(
        ruta_path, encoding="utf-8", low_memory=False,
        on_bad_lines="skip", names=col_names, skiprows=1,
    )
    df.columns = df.columns.str.strip()
    df["NEMO"] = df["NEMO"].astype(str).str.strip()
    df["RENDIMIENTO"] = pd.to_numeric(df["RENDIMIENTO"], errors="coerce")
    df["DIAS_AL_VENCIMIENTO"] = pd.to_numeric(df["DIAS_AL_VENCIMIENTO"], errors="coerce")
    df["FECHA_VENCIMIENTO"] = df["FECHA_VENCIMIENTO"].astype(str).str.strip()
    df = df[df["RENDIMIENTO"] > 0].copy()
    df = df[df["DIAS_AL_VENCIMIENTO"] > 0].copy()

    resultado: dict[str, pd.DataFrame] = {}

    # ── DP TF: por especie ──
    mask_tf = df["NEMO"].str.startswith("TFIT") | df["NEMO"].str.startswith("TFVT")
    df_tf = df[mask_tf].drop_duplicates("NEMO").copy()
    if not df_tf.empty:
        resultado["DP_TF"] = pd.DataFrame({
            "nodo": df_tf["NEMO"].values,
            "tasa": df_tf["RENDIMIENTO"].values,
            "dias": df_tf["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    # ── DP UVR: por especie ──
    mask_uvr = df["NEMO"].str.startswith("TUVT")
    df_uvr = df[mask_uvr].drop_duplicates("NEMO").copy()
    if not df_uvr.empty:
        resultado["DP_UVR"] = pd.DataFrame({
            "nodo": df_uvr["NEMO"].values,
            "tasa": df_uvr["RENDIMIENTO"].values,
            "dias": df_uvr["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    # ── USD: por especie ──
    mask_usd = df["NEMO"].str.startswith("BGLT")
    df_usd = df[mask_usd].drop_duplicates("NEMO").copy()
    if not df_usd.empty:
        resultado["USD"] = pd.DataFrame({
            "nodo": df_usd["NEMO"].values,
            "tasa": df_usd["RENDIMIENTO"].values,
            "dias": df_usd["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    # ── Corporativos TF: por año de vencimiento ──
    # Los corporativos del PIP son mayormente tasa fija (CDTs, bonos FS).
    # Nodos = año de vencimiento (2026, 2027, ...).
    mask_corp = (~mask_tf) & (~mask_uvr) & (~mask_usd)
    df_corp = df[mask_corp].copy()
    df_corp["anio_vcto"] = df_corp["FECHA_VENCIMIENTO"].str[:4].astype(int)
    resumen = df_corp.groupby("anio_vcto").agg(
        tasa=("RENDIMIENTO", "median"),
        dias=("DIAS_AL_VENCIMIENTO", "median"),
    ).reset_index()
    if not resumen.empty:
        resultado["CORP_TF"] = pd.DataFrame({
            "nodo": resumen["anio_vcto"].astype(str).values,
            "tasa": resumen["tasa"].values,
            "dias": resumen["dias"].values,
        }).sort_values("dias").reset_index(drop=True)
        # CORP_IPC usa la misma curva como proxy de yield de descuento
        resultado["CORP_IPC"] = resultado["CORP_TF"].copy()

    return resultado


# ==============================================================================
# FUNCIÓN 2: INTERPOLACIÓN LINEAL ENTRE NODOS
# ==============================================================================

def interpolar_tasa(curva: Curva, dias: int) -> float:
    """
    Interpola linealmente la tasa para un plazo dado entre los nodos de la curva.

    Si el plazo está fuera del rango, extrapola con el nodo más cercano (flat).

    Args:
        curva: Diccionario {plazo_dias: tasa_pct}
        dias: Plazo en días para el cual se desea la tasa.

    Returns:
        Tasa interpolada en porcentaje.
    """
    if not curva:
        raise ValueError("La curva está vacía, no se puede interpolar.")

    nodos_ordenados = sorted(curva.keys())
    tasas_ordenadas = [curva[n] for n in nodos_ordenados]

    if len(nodos_ordenados) == 1:
        return tasas_ordenadas[0]

    # Flat extrapolation fuera del rango
    if dias <= nodos_ordenados[0]:
        return tasas_ordenadas[0]
    if dias >= nodos_ordenados[-1]:
        return tasas_ordenadas[-1]

    # Buscar nodos vecinos
    for i in range(len(nodos_ordenados) - 1):
        n_inf = nodos_ordenados[i]
        n_sup = nodos_ordenados[i + 1]
        if n_inf <= dias <= n_sup:
            t_inf = tasas_ordenadas[i]
            t_sup = tasas_ordenadas[i + 1]
            proporcion = (dias - n_inf) / (n_sup - n_inf)
            return t_inf + (t_sup - t_inf) * proporcion

    return tasas_ordenadas[-1]


# ==============================================================================
# FUNCIÓN 3: GENERAR SENDA LINEAL ENTRE T0 Y TARGET
# ==============================================================================

def generar_senda_lineal(
    curva_t0: Curva,
    curva_target: Curva,
    meses_horizonte: int,
) -> dict[int, list[float]]:
    """
    Genera una senda lineal por nodo entre la curva actual y la curva target.

    Para cada nodo presente en AMBAS curvas, interpola linealmente mes a mes:
        tasa_mes_m = tasa_t0 + (tasa_target - tasa_t0) * m / horizonte

    Args:
        curva_t0: Curva base (tasas actuales).
        curva_target: Curva objetivo (tasas al horizonte).
        meses_horizonte: Número de meses hasta el target.

    Returns:
        Diccionario {plazo_dias: [tasa_mes_0, tasa_mes_1, ..., tasa_mes_horizonte]}
    """
    if meses_horizonte < 1:
        raise ValueError("El horizonte debe ser al menos 1 mes.")

    todos_nodos = sorted(set(curva_t0.keys()) | set(curva_target.keys()))
    senda: dict[int, list[float]] = {}

    for nodo in todos_nodos:
        tasa_0 = interpolar_tasa(curva_t0, nodo) if nodo not in curva_t0 else curva_t0[nodo]
        tasa_target = interpolar_tasa(curva_target, nodo) if nodo not in curva_target else curva_target[nodo]

        tasas_mensuales = [
            tasa_0 + (tasa_target - tasa_0) * m / meses_horizonte
            for m in range(meses_horizonte + 1)
        ]
        senda[nodo] = tasas_mensuales

    return senda


# ==============================================================================
# FUNCIÓN 4: OBTENER TASA DE LA SENDA PARA UN MES Y PLAZO DADOS
# ==============================================================================

def obtener_tasa_senda(
    senda: dict[int, list[float]],
    mes: int,
    dias_plazo: int,
) -> float:
    """
    Obtiene la tasa para un plazo dado en un mes específico de la senda.

    Interpola entre los nodos de la senda para el mes indicado.

    Args:
        senda: Resultado de generar_senda_lineal().
        mes: Mes de la proyección (0 = T0, 1 = primer mes, ...).
        dias_plazo: Plazo residual del título en días.

    Returns:
        Tasa interpolada para ese mes y plazo.
    """
    if not senda:
        raise ValueError("La senda está vacía.")

    nodos = sorted(senda.keys())
    max_mes = len(next(iter(senda.values()))) - 1
    mes_clamp = max(0, min(mes, max_mes))

    curva_mes: Curva = {nodo: senda[nodo][mes_clamp] for nodo in nodos}
    return interpolar_tasa(curva_mes, dias_plazo)


# ==============================================================================
# FUNCIÓN 5: APLICAR SHOCK A UN NODO INDIVIDUAL
# ==============================================================================

def aplicar_shock_nodo(
    senda: dict[int, list[float]],
    nodo_dias: int,
    mes: int,
    delta_bps: float,
) -> dict[int, list[float]]:
    """
    Aplica un shock en puntos básicos a un nodo específico desde un mes dado.

    Args:
        senda: Senda original (se modifica una copia).
        nodo_dias: Nodo (plazo en días) a shockear.
        mes: Mes desde el cual aplica el shock.
        delta_bps: Shock en puntos básicos (ej. +50 = +0.50%).

    Returns:
        Nueva senda con el shock aplicado.
    """
    senda_nueva = {k: list(v) for k, v in senda.items()}

    if nodo_dias not in senda_nueva:
        return senda_nueva

    delta_pct = delta_bps / 100.0
    n_meses = len(senda_nueva[nodo_dias])

    for m in range(mes, n_meses):
        senda_nueva[nodo_dias][m] += delta_pct

    return senda_nueva


# ==============================================================================
# FUNCIÓN 6: DETERMINAR CURVA PARA UN TÍTULO
# ==============================================================================

def obtener_nombre_curva(asset_class_definitivo: str) -> str:
    """
    Determina qué curva de rendimiento corresponde a un título
    según su asset_class_definitivo del portafolio_clasificado.

    Args:
        asset_class_definitivo: Clasificación del título.

    Returns:
        Nombre de la curva (ej. "DP_TF", "CORP_IPC", etc.)
        Si no se encuentra, retorna "CORP_TF" como default.
    """
    ac = str(asset_class_definitivo).strip()
    return ASSET_CLASS_A_CURVA.get(ac, "CORP_TF")


# ==============================================================================
# FUNCIÓN 7: CARGAR CURVAS DESDE EXCEL (para target manual)
# ==============================================================================

def cargar_curvas_excel(ruta: str) -> dict[str, Curva]:
    """
    Carga curvas de rendimiento desde un archivo Excel.

    El archivo debe tener una hoja con columnas:
        Curva | Plazo_Dias | Tasa_Pct

    Args:
        ruta: Ruta al archivo Excel.

    Returns:
        Diccionario {nombre_curva: {plazo_dias: tasa_pct}}
    """
    ruta_path = Path(ruta)
    if not ruta_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de curvas: {ruta}")

    df = pd.read_excel(ruta, sheet_name=0)
    df.columns = df.columns.str.strip()

    col_curva = next((c for c in df.columns if "curva" in c.lower()), None)
    col_plazo = next((c for c in df.columns if "plazo" in c.lower() or "dias" in c.lower()), None)
    col_tasa = next((c for c in df.columns if "tasa" in c.lower() or "rendimiento" in c.lower()), None)

    if not all([col_curva, col_plazo, col_tasa]):
        raise ValueError(
            f"El Excel debe tener columnas: Curva, Plazo_Dias, Tasa_Pct. "
            f"Encontradas: {df.columns.tolist()}"
        )

    curvas: dict[str, Curva] = {}
    for _, row in df.iterrows():
        nombre = str(row[col_curva]).strip()
        plazo = int(row[col_plazo])
        tasa = float(row[col_tasa])
        if nombre not in curvas:
            curvas[nombre] = {}
        curvas[nombre][plazo] = tasa

    return curvas


# ==============================================================================
# EJEMPLO DE USO
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AXIS — Módulo de Curvas de Rendimiento v2")
    print("=" * 60)

    # Extraer curvas base del PIP (nodos numéricos para motor)
    curvas_pip = extraer_curva_base_pip()
    for nombre, curva in curvas_pip.items():
        nodos_sorted = sorted(curva.keys())
        print(f"\n{nombre}: {len(curva)} nodos")
        for n in nodos_sorted[:5]:
            print(f"  {n:>5} días → {curva[n]:.4f}%")
        if len(nodos_sorted) > 5:
            print(f"  ... ({len(nodos_sorted) - 5} nodos más)")

    # Extraer curvas con nombres legibles (para dashboard)
    print("\n" + "-" * 60)
    print("Curvas con nombres legibles:")
    curvas_nombre = extraer_curva_con_nombres()
    for nombre, df_curva in curvas_nombre.items():
        print(f"\n{nombre}:")
        print(df_curva.to_string(index=False))

    # Ejemplo de senda lineal
    if "DP_TF" in curvas_pip:
        curva_target = {k: v - 2.0 for k, v in curvas_pip["DP_TF"].items()}
        senda = generar_senda_lineal(curvas_pip["DP_TF"], curva_target, 12)
        primer_nodo = sorted(senda.keys())[0]
        print(f"\nSenda DP_TF nodo {primer_nodo} días (12 meses):")
        for m, t in enumerate(senda[primer_nodo]):
            print(f"  Mes {m:>2}: {t:.4f}%")

    print("\n✓ Módulo de curvas v2 operativo")


# ==============================================================================
# CURVAS DESDE hist_pip (SQLite) — fuente preferida en el dashboard
# ==============================================================================

def _construir_curvas_desde_df(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Construye las curvas nombradas (DP_TF, DP_UVR, USD, CORP_TF, CORP_IPC)
    a partir de un DataFrame con columnas: NEMO, RENDIMIENTO, DIAS_AL_VENCIMIENTO,
    FECHA_VENCIMIENTO. Misma lógica que extraer_curva_con_nombres pero desde
    un DataFrame ya cargado (p. ej. de hist_pip).
    """
    df = df.copy()
    df["NEMO"] = df["NEMO"].astype(str).str.strip()
    df["RENDIMIENTO"] = pd.to_numeric(df["RENDIMIENTO"], errors="coerce")
    df["DIAS_AL_VENCIMIENTO"] = pd.to_numeric(df["DIAS_AL_VENCIMIENTO"], errors="coerce")
    df["FECHA_VENCIMIENTO"] = df["FECHA_VENCIMIENTO"].astype(str).str.strip()
    df = df[df["RENDIMIENTO"] > 0].copy()
    df = df[df["DIAS_AL_VENCIMIENTO"] > 0].copy()

    resultado: dict[str, pd.DataFrame] = {}

    mask_tf = df["NEMO"].str.startswith("TFIT") | df["NEMO"].str.startswith("TFVT")
    df_tf = df[mask_tf].drop_duplicates("NEMO").copy()
    if not df_tf.empty:
        resultado["DP_TF"] = pd.DataFrame({
            "nodo": df_tf["NEMO"].values,
            "tasa": df_tf["RENDIMIENTO"].values,
            "dias": df_tf["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    mask_uvr = df["NEMO"].str.startswith("TUVT")
    df_uvr = df[mask_uvr].drop_duplicates("NEMO").copy()
    if not df_uvr.empty:
        resultado["DP_UVR"] = pd.DataFrame({
            "nodo": df_uvr["NEMO"].values,
            "tasa": df_uvr["RENDIMIENTO"].values,
            "dias": df_uvr["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    mask_usd = df["NEMO"].str.startswith("BGLT")
    df_usd = df[mask_usd].drop_duplicates("NEMO").copy()
    if not df_usd.empty:
        resultado["USD"] = pd.DataFrame({
            "nodo": df_usd["NEMO"].values,
            "tasa": df_usd["RENDIMIENTO"].values,
            "dias": df_usd["DIAS_AL_VENCIMIENTO"].values,
        }).sort_values("dias").reset_index(drop=True)

    mask_corp = (~mask_tf) & (~mask_uvr) & (~mask_usd)
    df_corp = df[mask_corp].copy()
    df_corp = df_corp[df_corp["FECHA_VENCIMIENTO"].str.len() >= 4].copy()
    if not df_corp.empty:
        df_corp["anio_vcto"] = df_corp["FECHA_VENCIMIENTO"].str[:4]
        df_corp = df_corp[df_corp["anio_vcto"].str.isdigit()].copy()
        df_corp["anio_vcto"] = df_corp["anio_vcto"].astype(int)
        resumen = df_corp.groupby("anio_vcto").agg(
            tasa=("RENDIMIENTO", "median"),
            dias=("DIAS_AL_VENCIMIENTO", "median"),
        ).reset_index()
        if not resumen.empty:
            resultado["CORP_TF"] = pd.DataFrame({
                "nodo": resumen["anio_vcto"].astype(str).values,
                "tasa": resumen["tasa"].values,
                "dias": resumen["dias"].values,
            }).sort_values("dias").reset_index(drop=True)
            resultado["CORP_IPC"] = resultado["CORP_TF"].copy()

    return resultado


def extraer_curva_con_nombres_db(ruta_db: str, fecha_reporte: str | None = None) -> dict[str, pd.DataFrame]:
    """
    Extrae curvas nombradas desde la tabla hist_pip de la base de datos.

    Args:
        ruta_db: ruta a portafolio.db.
        fecha_reporte: fecha 'YYYY-MM-DD'. Si None, usa la más reciente.
                       Si no hay dato exacto, usa la fecha PIP más cercana.

    Returns:
        Dict {nombre_curva: DataFrame[nodo, tasa, dias]}.
    """
    import sqlite3
    conn = sqlite3.connect(ruta_db)
    try:
        if fecha_reporte:
            # Fecha EXACTA: si no hay PIP para esa fecha, no se devuelve nada.
            fecha_usar = fecha_reporte
        else:
            row = conn.execute("SELECT MAX(fecha_reporte) FROM hist_pip").fetchone()
            if not row or not row[0]:
                return {}
            fecha_usar = row[0]

        df = pd.read_sql_query(
            "SELECT nemo AS NEMO, rendimiento AS RENDIMIENTO, "
            "dias_al_vencimiento AS DIAS_AL_VENCIMIENTO, "
            "fecha_vencimiento AS FECHA_VENCIMIENTO "
            "FROM hist_pip WHERE fecha_reporte = ?",
            conn, params=(fecha_usar,),
        )
    finally:
        conn.close()

    if df.empty:
        return {}
    return _construir_curvas_desde_df(df)


def extraer_curva_base_pip_db(ruta_db: str, fecha_reporte: str | None = None) -> dict[str, "Curva"]:
    """
    Variante de extraer_curva_base_pip que lee desde la tabla hist_pip (filtrando
    por fecha_reporte) en vez de un archivo CSV. Devuelve el MISMO formato:
    {nombre_curva: {plazo_dias: tasa_rendimiento}} para el motor de proyección.

    Args:
        ruta_db: ruta a portafolio.db.
        fecha_reporte: fecha 'YYYY-MM-DD'. Si None, usa la más reciente en hist_pip.

    Returns:
        Dict {nombre_curva: {dias: tasa}}. Vacío si no hay PIP para esa fecha.
    """
    import sqlite3
    conn = sqlite3.connect(ruta_db)
    try:
        if fecha_reporte:
            fecha_usar = fecha_reporte
        else:
            row = conn.execute("SELECT MAX(fecha_reporte) FROM hist_pip").fetchone()
            if not row or not row[0]:
                return {}
            fecha_usar = row[0]

        df = pd.read_sql_query(
            "SELECT nemo AS NEMO, rendimiento AS RENDIMIENTO, "
            "dias_al_vencimiento AS DIAS_AL_VENCIMIENTO, "
            "fecha_vencimiento AS FECHA_VENCIMIENTO "
            "FROM hist_pip WHERE fecha_reporte = ?",
            conn, params=(fecha_usar,),
        )
    finally:
        conn.close()

    if df.empty:
        return {}

    # Replicar EXACTAMENTE las transformaciones de extraer_curva_base_pip
    df["NEMO"] = df["NEMO"].astype(str).str.strip()
    df["RENDIMIENTO"] = pd.to_numeric(df["RENDIMIENTO"], errors="coerce")
    df["DIAS_AL_VENCIMIENTO"] = pd.to_numeric(df["DIAS_AL_VENCIMIENTO"], errors="coerce")
    df["FECHA_VENCIMIENTO"] = df["FECHA_VENCIMIENTO"].astype(str).str.strip()
    df = df[df["RENDIMIENTO"] > 0].copy()
    df = df[df["DIAS_AL_VENCIMIENTO"] > 0].copy()

    curvas: dict[str, Curva] = {}

    mask_tf = df["NEMO"].str.startswith("TFIT") | df["NEMO"].str.startswith("TFVT")
    for _, row in df[mask_tf].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("DP_TF", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    mask_uvr = df["NEMO"].str.startswith("TUVT")
    for _, row in df[mask_uvr].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("DP_UVR", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    mask_usd = df["NEMO"].str.startswith("BGLT")
    for _, row in df[mask_usd].drop_duplicates("NEMO").iterrows():
        curvas.setdefault("USD", {})[int(row["DIAS_AL_VENCIMIENTO"])] = float(row["RENDIMIENTO"])

    mask_corp = (~mask_tf) & (~mask_uvr) & (~mask_usd)
    df_corp = df[mask_corp].copy()
    df_corp = df_corp[df_corp["FECHA_VENCIMIENTO"].str.len() >= 4].copy()
    if not df_corp.empty:
        df_corp["anio_vcto"] = df_corp["FECHA_VENCIMIENTO"].str[:4]
        df_corp = df_corp[df_corp["anio_vcto"].str.isdigit()].copy()
        df_corp["anio_vcto"] = df_corp["anio_vcto"].astype(int)
        resumen_corp = df_corp.groupby("anio_vcto").agg(
            tasa_media=("RENDIMIENTO", "median"),
            dias_medio=("DIAS_AL_VENCIMIENTO", "median"),
        ).reset_index()
        for _, row in resumen_corp.iterrows():
            curvas.setdefault("CORP_TF", {})[int(row["dias_medio"])] = float(row["tasa_media"])
        if "CORP_TF" in curvas:
            curvas["CORP_IPC"] = dict(curvas["CORP_TF"])

    return curvas
