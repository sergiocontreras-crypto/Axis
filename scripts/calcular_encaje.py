"""
Módulo de Cálculo de Encaje por Ramo
=====================================
Calcula el ratio de encaje (posición del portafolio / reservas) consolidado
a nivel de Ramo, soportando:
  - Proyecciones mensuales ACUMULATIVAS
  - Múltiples escenarios (Base, Negativo, Positivo, Alternativo 1, Alternativo 2)
  - Sensibilización por Ramo (shocks individuales a reservas)
  - Semáforo de alertas con umbrales por compañía

Fórmula:
    Encaje_Ramo = Posición_Portafolio_Ramo / Reservas_Ramo

Fuentes de datos:
    1. PFTIS597 (CSV)         → Posición actual del portafolio (Valor Mercado por `Por`)
    2. Diccionario_portafolios → Mapeo Portafolio → Ramo
    3. Formato Información Reservas (Excel) → Saldo Actual de reservas por Ramo
    4. Inputs_Planeación_Reservas (Excel)   → Variaciones mensuales proyectadas

Autor: Gerencia Financiera - AXIS
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional


# ==============================================================================
# CONFIGURACIÓN DE RUTAS (ajustar según entorno)
# ==============================================================================
RUTA_597 = "PFTIS597 (20).CSV"
RUTA_DICCIONARIO = "Diccionario_portafolios.csv"
RUTA_RESERVAS = "Formato Información Reservas (7).xlsx"
RUTA_INPUTS_RESERVAS = "Inputs_Planeacion_Reservas.xlsx"

# Mapeo de prefijo del portafolio → hoja en Inputs_Planeación_Reservas
PREFIJO_HOJA = {
    "01": "Capitalizadora",
    "02": "Bolivar",
    "03": "Comerciales",
}

# Orden de meses para acumulación
MESES_ORDEN = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Mapeo nombre de escenario → columna en el Excel (0-based después de skiprows=3)
# Columnas: A=Tipo, B=Ramo, C=Mes, D=Base, E=Negativo, F=Positivo, G=Alt1, H=Alt2
ESCENARIOS = {
    "Base": "Base",
    "Negativo": "Negativo",
    "Positivo": "Positivo",
    "Alternativo_1": "Alt_0",
    "Alternativo_2": "Alt_1",
}

# ==============================================================================
# UMBRALES DE ENCAJE POR COMPAÑÍA (semáforo de alertas)
# ==============================================================================
# Si el encaje cae por debajo de este valor, se marca como ALERTA
UMBRAL_ENCAJE = {
    "BOLIVAR": 1.02,        # 102%
    "COMERCIALES": 1.03,    # 103%
    "CAPITALIZADORA": 1.00, # 100% (default)
}
UMBRAL_DEFAULT = 1.00  # Para compañías no listadas


# ==============================================================================
# TABLA DE HOMOLOGACIÓN: Nombre en Reservas → Nombre en Diccionario
# ==============================================================================
# DEPRECADO (feature unificar-mapeo-ramos-reservas): la fuente única de verdad del
# mapeo Etiqueta_De_Reserva → Ramo_De_Portafolio son ahora las tablas SQLite editables
# `mapeo_ramos_saldo_reservas` / `mapeo_ramos_interes_reservas`, leídas vía el
# Lector_Mapeo (`compras_ventas_reservas.cargar_mapeo_saldo` / `cargar_mapeo_interes`).
# Este diccionario solo lo consume la ruta legacy `cargar_reservas_actuales` /
# `calcular_encaje_por_ramo`, que NO forma parte del flujo activo de la pestaña Encaje
# (ese flujo usa `lector_reservas.py`, ya migrado a la fuente única). No usar en código
# nuevo; migrar cualquier consumidor restante al Lector_Mapeo.
# NOTA: este dict conserva "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" y los
# sub-ramos DABUENAVIDA/MAGNATE/GANAMAS, cuyo tratamiento fue reconciliado en la fuente
# única (RIA se ignora; Capitalizadora usa TOTAL CAPI). Refleja el comportamiento legacy.
HOMOLOGACION_RESERVAS_A_DICCIONARIO = {
    # Bolívar
    "PENSIONES LEY 100": "RENTAS VITALICIAS",
    "VIDA GRUPO-SINIESTROS": "DEMAS RAMOS DE SEGUROS DE VIDA",
    "VIDA GRUPO-TECNICA": "VIDA GRUPO",
    "VIDA INDIVIDUAL - MATEMÁTICA": "VIDA INDIVIDUAL-MATEMATICA",
    "VIDA INDIVIDUAL - SINIESTROS": "VIDA INDIVIDUAL-SINIESTROS",
    "VIP - \"MATEMÁTICA\"": "VIP",
    "FAP - \"MATEMÁTICA\"": "FAP",
    "FONDO LIFE LIBERTY": "FONDO LIFE USD",
    "FONDO EN PESOS 52 LIBERTY": "FONDO VIDA PESOS",
    "FONDO EN PESOS 53 LIBERTY": "FONDO LATIN VIDA",
    "PREVISIONAL - NEGOCIO PERÚ": "PREVISIONAL - PERU",
    "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY": "VIDA INDIVIDUAL-RESERVA INSUFICIENCIA RIA",
    # Capitalizadora (se suman DABUENAVIDA + MAGNATE + GANAMAS → CAPITALIZADORA)
    "DABUENAVIDA": "CAPITALIZADORA",
    "MAGNATE": "CAPITALIZADORA",
    "GANAMAS": "CAPITALIZADORA",
    # Comerciales (se agrupan bajo DEMAS RAMOS DE SEGUROS GENERALES)
    "CORRIENTE DEBIL": "DEMAS RAMOS DE SEGUROS GENERALES",
    "LUCROCESANTE": "DEMAS RAMOS DE SEGUROS GENERALES",
    "MANEJO": "DEMAS RAMOS DE SEGUROS GENERALES",
    "ROTURA DE MAQUINARIA": "DEMAS RAMOS DE SEGUROS GENERALES",
    "NAVEGACION": "DEMAS RAMOS DE SEGUROS GENERALES",
    "SUSTRACCIÓN": "DEMAS RAMOS DE SEGUROS GENERALES",
    "TRANSPORTE": "DEMAS RAMOS DE SEGUROS GENERALES",
    "ROTURA DE VIDRIOS": "DEMAS RAMOS DE SEGUROS GENERALES",
    "DESEMPLEO": "DEMAS RAMOS DE SEGUROS GENERALES",
    "HOGAR": "DEMAS RAMOS DE SEGUROS GENERALES",
    "AGRICOLA": "DEMAS RAMOS DE SEGUROS GENERALES",
    "MANEJO GLOBAL BANCARIO": "DEMAS RAMOS DE SEGUROS GENERALES",
    "ARRENDAMIENTO": "DEMAS RAMOS DE SEGUROS GENERALES",
    "DECENAL": "DEMAS RAMOS DE SEGUROS GENERALES",
}


# ==============================================================================
# FUNCIÓN AUXILIAR: Cargar base 597 (portafolio)
# ==============================================================================
def cargar_portafolio(ruta: str = RUTA_597) -> pd.DataFrame:
    """
    Carga el reporte PFTIS597, limpia encabezados y convierte Valor Mercado
    a numérico.
    """
    with open(ruta, "r", encoding="latin1") as f:
        lineas = f.readlines()

    linea_titulos = 0
    for i, linea in enumerate(lineas):
        if linea.strip().startswith("Cla;"):
            linea_titulos = i
            break

    df = pd.read_csv(ruta, sep=";", encoding="latin1", skiprows=linea_titulos)
    df.columns = df.columns.str.strip()
    df = df[df["Cla"].astype(str).str.strip().isin(["Neg", "NEG"])].copy()
    df["Por"] = df["Por"].astype(str).str.strip()

    df["Valor_Mercado"] = (
        df["Valor Mercado"]
        .astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace(" ", "", regex=False)
    )
    df["Valor_Mercado"] = pd.to_numeric(df["Valor_Mercado"], errors="coerce")

    # Extraer tasas de compra y mercado
    df["T_Compra"] = pd.to_numeric(
        df["T.Compra"].astype(str).str.strip().str.replace(",", "."), errors="coerce"
    )
    df["T_Mercado"] = pd.to_numeric(
        df["T.Mer"].astype(str).str.strip().str.replace(",", "."), errors="coerce"
    )
    # Valor TIR (valoración a costo amortizado del 597)
    df["Valor_TIR"] = (
        df["Valor TIR"]
        .astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace(" ", "", regex=False)
    )
    df["Valor_TIR"] = pd.to_numeric(df["Valor_TIR"], errors="coerce")
    # Clasificación contable (1CP=Negociable, 1DV=Disponible, 1VC=Vencimiento)
    df["Clasificacion"] = df["Fte"].astype(str).str.strip()

    return df


# ==============================================================================
# FUNCIÓN AUXILIAR: Cargar diccionario de portafolios
# ==============================================================================
def cargar_diccionario(ruta: str = RUTA_DICCIONARIO) -> pd.DataFrame:
    """
    Carga el diccionario Portafolio → Ramo.
    Maneja líneas con campos extra (separador ; en el nombre del ramo).
    """
    registros = []
    with open(ruta, "r", encoding="latin1") as f:
        encabezado = f.readline()
        for linea in f:
            partes = linea.strip().split(";")
            if len(partes) >= 2:
                portafolio = partes[0].strip()
                ramo = partes[1].strip()
                registros.append({"Portafolio": portafolio, "Ramo": ramo})

    return pd.DataFrame(registros)


# ==============================================================================
# FUNCIÓN AUXILIAR: Cargar saldos actuales de reservas
# ==============================================================================
def cargar_reservas_actuales(ruta: str = RUTA_RESERVAS) -> pd.DataFrame:
    """
    Lee la hoja 'Reservas' del archivo Formato Información Reservas.
    Extrae el Saldo Actual (última columna TOTAL disponible) por cada Ramo.
    Aplica homologación y agrupación.
    """
    import openpyxl

    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    ws = wb["Reservas"]

    row3 = list(ws.iter_rows(min_row=3, max_row=3, values_only=True))[0]

    last_total_idx = None
    for i in range(len(row3) - 1, -1, -1):
        if row3[i] == "TOTAL":
            last_total_idx = i
            break

    if last_total_idx is None:
        raise ValueError("No se encontró columna TOTAL en hoja Reservas")

    all_rows = list(ws.iter_rows(min_row=4, max_row=60, values_only=True))
    wb.close()

    registros = []
    compania_actual = None

    for row in all_rows:
        nombre = row[1] if len(row) > 1 else None
        if nombre is None:
            continue

        nombre_str = str(nombre).strip()

        if nombre_str in ("BOLIVAR", "COMERCIALES", "CAPITALIZADORA"):
            compania_actual = nombre_str
            continue

        if nombre_str.startswith("TOTAL"):
            saldo = row[last_total_idx] if len(row) > last_total_idx else None
            registros.append({
                "Ramo": nombre_str,
                "Saldo_Actual": float(saldo) if saldo is not None else 0.0,
                "Compania": compania_actual,
            })
            continue

        saldo = row[last_total_idx] if len(row) > last_total_idx else None
        registros.append({
            "Ramo": nombre_str,
            "Saldo_Actual": float(saldo) if saldo is not None else 0.0,
            "Compania": compania_actual,
        })

    df = pd.DataFrame(registros)
    df["Ramo"] = df["Ramo"].replace(HOMOLOGACION_RESERVAS_A_DICCIONARIO)
    df = (
        df.groupby(["Ramo", "Compania"], as_index=False)
        .agg({"Saldo_Actual": "sum"})
    )

    return df


# ==============================================================================
# FUNCIÓN AUXILIAR: Cargar variaciones proyectadas de reservas
# ==============================================================================
def cargar_variaciones_reservas(
    ruta: str = RUTA_INPUTS_RESERVAS,
    escenario: str = "Base",
) -> pd.DataFrame:
    """
    Lee TODAS las variaciones mensuales de un escenario dado, de cada hoja.
    Retorna el DataFrame completo (todos los meses) para permitir acumulación.

    Args:
        ruta: Ruta al archivo Excel de Inputs.
        escenario: Nombre del escenario ('Base', 'Negativo', 'Positivo',
                   'Alternativo_1', 'Alternativo_2').

    Returns:
        DataFrame con columnas: Compania, Ramo, Mes, Variacion
    """
    if escenario not in ESCENARIOS:
        raise ValueError(
            f"Escenario '{escenario}' no válido. "
            f"Opciones: {list(ESCENARIOS.keys())}"
        )

    col_escenario = ESCENARIOS[escenario]
    registros = []

    for hoja in ["Capitalizadora", "Bolivar", "Comerciales"]:
        df_hoja = pd.read_excel(
            ruta,
            sheet_name=hoja,
            header=None,
            skiprows=3,
        )

        # Asignar nombres de columnas
        col_names = ["Tipo", "Ramo", "Mes", "Base", "Negativo", "Positivo"]
        extras = len(df_hoja.columns) - 6
        if extras > 0:
            col_names += [f"Alt_{i}" for i in range(extras)]
        df_hoja.columns = col_names[:len(df_hoja.columns)]

        # Verificar que la columna del escenario existe
        if col_escenario not in df_hoja.columns:
            continue

        # Filtrar solo Cambio_reserva
        df_cambio = df_hoja[df_hoja["Tipo"] == "Cambio_reserva"].copy()

        df_cambio["Compania"] = hoja.upper()
        df_cambio["Variacion"] = pd.to_numeric(
            df_cambio[col_escenario], errors="coerce"
        ).fillna(0.0)

        # Normalizar mes a minúsculas
        df_cambio["Mes"] = df_cambio["Mes"].astype(str).str.lower().str.strip()

        registros.append(
            df_cambio[["Compania", "Ramo", "Mes", "Variacion"]]
        )

    if not registros:
        return pd.DataFrame(columns=["Compania", "Ramo", "Mes", "Variacion"])

    return pd.concat(registros, ignore_index=True)


# ==============================================================================
# FUNCIÓN AUXILIAR: Calcular variación acumulada hasta un mes dado
# ==============================================================================
def acumular_variaciones_hasta_mes(
    df_variaciones: pd.DataFrame,
    mes_objetivo: str,
) -> pd.DataFrame:
    """
    Suma las variaciones desde enero hasta el mes_objetivo (inclusive).

    Ejemplo: si mes_objetivo='marzo', acumula enero + febrero + marzo.

    Args:
        df_variaciones: DataFrame con todas las variaciones (todos los meses).
        mes_objetivo: Mes hasta el cual acumular.

    Returns:
        DataFrame con columnas: Ramo, Variacion_Acumulada
    """
    mes_lower = mes_objetivo.lower().strip()

    if mes_lower not in MESES_ORDEN:
        raise ValueError(
            f"Mes '{mes_objetivo}' no válido. Opciones: {MESES_ORDEN}"
        )

    # Obtener índice del mes objetivo
    idx_mes = MESES_ORDEN.index(mes_lower)

    # Filtrar meses desde enero hasta el mes objetivo (inclusive)
    meses_a_sumar = MESES_ORDEN[: idx_mes + 1]
    df_filtrado = df_variaciones[
        df_variaciones["Mes"].isin(meses_a_sumar)
    ].copy()

    # Agrupar por Ramo y sumar todas las variaciones acumuladas
    df_acum = (
        df_filtrado.groupby("Ramo", as_index=False)["Variacion"]
        .sum()
        .rename(columns={"Variacion": "Variacion_Acumulada"})
    )

    # Convertir de millones a pesos
    df_acum["Variacion_Acumulada"] = df_acum["Variacion_Acumulada"] * 1_000_000

    return df_acum


# ==============================================================================
# FUNCIÓN AUXILIAR: Aplicar shocks de sensibilización por Ramo
# ==============================================================================
def aplicar_shocks(
    df: pd.DataFrame,
    shocks: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Aplica shocks de sensibilización a las reservas por Ramo.

    Los shocks pueden ser:
      - Porcentuales: {"AUTOMOVILES": 0.10} → +10% sobre las reservas
      - Absolutos: {"AUTOMOVILES": 50_000_000_000} → +50 mil millones en pesos

    El tipo se infiere por magnitud:
      - Si |valor| <= 5 → se interpreta como porcentaje (ej: 0.10 = +10%)
      - Si |valor| > 5  → se interpreta como monto absoluto en pesos

    Args:
        df: DataFrame con la columna 'Reservas' ya calculada.
        shocks: Diccionario {Ramo: valor_shock}.
                Valor positivo = incremento, negativo = decremento.

    Returns:
        DataFrame con columnas adicionales: Shock_Aplicado, Reservas_Shock
    """
    df = df.copy()
    df["Shock_Aplicado"] = 0.0

    if not shocks:
        df["Reservas_Sensibilizadas"] = df["Reservas"]
        return df

    for ramo, valor in shocks.items():
        ramo_upper = ramo.upper().strip()
        mask = df["Ramo"].str.upper().str.strip() == ramo_upper

        if not mask.any():
            print(f"  ⚠ Shock ignorado: Ramo '{ramo}' no encontrado")
            continue

        # Inferir tipo de shock
        if abs(valor) <= 5:
            # Porcentual: valor = 0.10 → +10%
            shock_monto = df.loc[mask, "Reservas"] * valor
            tipo_str = f"{valor*100:+.1f}%"
        else:
            # Absoluto: valor en pesos
            shock_monto = valor
            tipo_str = f"${valor:+,.0f}"

        df.loc[mask, "Shock_Aplicado"] = shock_monto
        print(f"  → Shock en {ramo_upper}: {tipo_str}")

    df["Reservas_Sensibilizadas"] = df["Reservas"] + df["Shock_Aplicado"]

    return df


# ==============================================================================
# FUNCIÓN AUXILIAR: Aplicar semáforo de alertas
# ==============================================================================
def aplicar_semaforo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clasifica cada ramo según su nivel de encaje vs. el umbral de su compañía.

    Semáforo:
      🟢 VERDE  → Encaje >= Umbral + 5pp (holgado)
      🟡 AMARILLO → Umbral <= Encaje < Umbral + 5pp (ajustado)
      🔴 ROJO   → Encaje < Umbral (alerta)

    Umbrales:
      - BOLIVAR: 102%
      - COMERCIALES: 103%
      - CAPITALIZADORA: 100%
    """
    df = df.copy()

    def clasificar(row):
        compania = str(row.get("Compania", "")).upper().strip()
        umbral = UMBRAL_ENCAJE.get(compania, UMBRAL_DEFAULT)
        encaje = row.get("Encaje_Final")

        if pd.isna(encaje):
            return "⚪ SIN RESERVA"
        elif encaje < umbral:
            return "🔴 ALERTA"
        elif encaje < umbral + 0.05:
            return "🟡 AJUSTADO"
        else:
            return "🟢 OK"

    df["Umbral"] = df["Compania"].apply(
        lambda x: UMBRAL_ENCAJE.get(str(x).upper().strip(), UMBRAL_DEFAULT)
    )
    df["Semaforo"] = df.apply(clasificar, axis=1)

    return df


# ==============================================================================
# FUNCIÓN PRINCIPAL: Calcular Encaje por Ramo
# ==============================================================================
def calcular_encaje_por_ramo(
    mes_proyeccion: Optional[str] = None,
    escenario: str = "Base",
    shocks: Optional[dict] = None,
    ruta_597: str = RUTA_597,
    ruta_diccionario: str = RUTA_DICCIONARIO,
    ruta_reservas: str = RUTA_RESERVAS,
    ruta_inputs: str = RUTA_INPUTS_RESERVAS,
) -> pd.DataFrame:
    """
    Calcula el encaje del portafolio consolidado a nivel de Ramo.

    Encaje = Posición Portafolio (Valor Mercado) por Ramo
             / Reservas por Ramo (actual o proyectada, con o sin shock)

    Proyección ACUMULATIVA:
        Si mes_proyeccion='marzo', las reservas se proyectan como:
        Reservas = Saldo_Actual + Var_Enero + Var_Febrero + Var_Marzo

    Sensibilización:
        shocks = {"AUTOMOVILES": 0.10, "SOAT": -50_000_000_000}
        Aplica +10% a reservas de Autos y resta 50 mil millones a SOAT.

    Args:
        mes_proyeccion: Mes hasta el cual acumular variaciones.
                        Si None, usa saldo actual sin proyección.
        escenario: 'Base', 'Negativo', 'Positivo', 'Alternativo_1', 'Alternativo_2'
        shocks: Dict de sensibilización por Ramo (ver aplicar_shocks).
        ruta_597: Ruta al archivo PFTIS597.
        ruta_diccionario: Ruta al diccionario de portafolios.
        ruta_reservas: Ruta al Formato Información Reservas.
        ruta_inputs: Ruta al archivo Inputs_Planeación_Reservas.

    Returns:
        DataFrame con columnas:
            - Ramo, Compania
            - Posicion_Portafolio
            - Saldo_Actual, Variacion_Acumulada, Reservas
            - Shock_Aplicado, Reservas_Sensibilizadas
            - Encaje_Final
            - Umbral, Semaforo
    """
    print("=" * 70)
    print("  CÁLCULO DE ENCAJE POR RAMO")
    print(f"  Escenario: {escenario} | Mes: {mes_proyeccion or 'Actual'}")
    if shocks:
        print(f"  Shocks: {len(shocks)} ramos sensibilizados")
    print("=" * 70)

    # ── 1. Cargar portafolio ─────────────────────────────────────────────
    print("\n[1/5] Cargando portafolio (PFTIS597)...")
    df_port = cargar_portafolio(ruta_597)
    print(f"       → {len(df_port):,} títulos cargados")

    # ── 2. Mapear Por → Ramo ─────────────────────────────────────────────
    print("[2/5] Cargando diccionario de portafolios...")
    df_dicc = cargar_diccionario(ruta_diccionario)
    print(f"       → {len(df_dicc):,} mapeos cargados")

    df_port = df_port.merge(
        df_dicc, left_on="Por", right_on="Portafolio", how="left"
    )

    sin_mapeo = df_port[df_port["Ramo"].isna()]["Por"].unique()
    if len(sin_mapeo) > 0:
        print(f"       ⚠ {len(sin_mapeo)} portafolios sin mapeo a Ramo: "
              f"{list(sin_mapeo[:5])}...")

    posicion_ramo = (
        df_port.groupby("Ramo", dropna=True)["Valor_Mercado"]
        .sum()
        .reset_index()
        .rename(columns={"Valor_Mercado": "Posicion_Portafolio"})
    )

    # ── 3. Cargar reservas actuales ──────────────────────────────────────
    print("[3/5] Cargando saldos de reservas actuales...")
    df_reservas = cargar_reservas_actuales(ruta_reservas)
    df_reservas_ramos = df_reservas[
        ~df_reservas["Ramo"].str.startswith("TOTAL")
    ].copy()
    print(f"       → {len(df_reservas_ramos):,} ramos con saldo")

    # ── 4. Cargar y acumular variaciones ─────────────────────────────────
    if mes_proyeccion:
        print(f"[4/5] Variaciones acumuladas ene→{mes_proyeccion} "
              f"(escenario: {escenario})...")
        df_var_total = cargar_variaciones_reservas(ruta_inputs, escenario)

        if not df_var_total.empty:
            df_var_acum = acumular_variaciones_hasta_mes(
                df_var_total, mes_proyeccion
            )
            print(f"       → {len(df_var_acum):,} ramos con variación acumulada")
        else:
            print("       → Sin variaciones (columnas vacías en este escenario)")
            df_var_acum = pd.DataFrame(
                columns=["Ramo", "Variacion_Acumulada"]
            )
    else:
        print("[4/5] Sin proyección (usando saldo actual)...")
        df_var_acum = pd.DataFrame(columns=["Ramo", "Variacion_Acumulada"])

    # ── 5. Consolidar ────────────────────────────────────────────────────
    print("[5/5] Consolidando y calculando encaje...")

    posicion_ramo["Ramo_norm"] = posicion_ramo["Ramo"].str.upper().str.strip()
    df_reservas_ramos["Ramo_norm"] = (
        df_reservas_ramos["Ramo"].str.upper().str.strip()
    )

    df_resultado = posicion_ramo.merge(
        df_reservas_ramos[["Ramo_norm", "Saldo_Actual", "Compania"]],
        on="Ramo_norm",
        how="outer",
    )

    # Merge variaciones acumuladas
    if not df_var_acum.empty:
        df_var_acum["Ramo_norm"] = df_var_acum["Ramo"].str.upper().str.strip()
        df_var_acum_agg = (
            df_var_acum.groupby("Ramo_norm")["Variacion_Acumulada"]
            .sum()
            .reset_index()
        )
        df_resultado = df_resultado.merge(
            df_var_acum_agg, on="Ramo_norm", how="left"
        )
    else:
        df_resultado["Variacion_Acumulada"] = 0.0

    # Rellenar nulos
    df_resultado["Posicion_Portafolio"] = df_resultado[
        "Posicion_Portafolio"
    ].fillna(0.0)
    df_resultado["Saldo_Actual"] = df_resultado["Saldo_Actual"].fillna(0.0)
    df_resultado["Variacion_Acumulada"] = df_resultado[
        "Variacion_Acumulada"
    ].fillna(0.0)

    # Reservas proyectadas = Saldo Actual + Variación Acumulada
    df_resultado["Reservas"] = (
        df_resultado["Saldo_Actual"] + df_resultado["Variacion_Acumulada"]
    )

    # ── Aplicar shocks de sensibilización ────────────────────────────────
    df_resultado = aplicar_shocks(df_resultado, shocks)

    # ── Calcular encaje final (sobre reservas sensibilizadas) ────────────
    df_resultado["Encaje_Final"] = np.where(
        df_resultado["Reservas_Sensibilizadas"] > 0,
        df_resultado["Posicion_Portafolio"]
        / df_resultado["Reservas_Sensibilizadas"],
        np.nan,
    )

    # ── Aplicar semáforo ─────────────────────────────────────────────────
    df_resultado["Ramo"] = df_resultado["Ramo"].fillna(
        df_resultado["Ramo_norm"]
    )
    df_resultado = aplicar_semaforo(df_resultado)

    # ── Resultado final ──────────────────────────────────────────────────
    columnas_finales = [
        "Ramo", "Compania", "Posicion_Portafolio",
        "Saldo_Actual", "Variacion_Acumulada", "Reservas",
        "Shock_Aplicado", "Reservas_Sensibilizadas",
        "Encaje_Final", "Umbral", "Semaforo",
    ]

    df_resultado = (
        df_resultado[columnas_finales]
        .sort_values(["Compania", "Ramo"])
        .reset_index(drop=True)
    )

    # ── Resumen ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESUMEN DE ENCAJE POR RAMO")
    print("=" * 70)
    print(f"\n  Escenario: {escenario}")
    print(f"  Mes proyección: {mes_proyeccion or 'N/A (saldo actual)'}")
    print(f"  Total Ramos: {len(df_resultado)}")
    print(f"  Posición total: "
          f"${df_resultado['Posicion_Portafolio'].sum():,.0f}")
    print(f"  Reservas totales: "
          f"${df_resultado['Reservas_Sensibilizadas'].sum():,.0f}")

    total_pos = df_resultado["Posicion_Portafolio"].sum()
    total_res = df_resultado["Reservas_Sensibilizadas"].sum()
    encaje_global = total_pos / total_res if total_res > 0 else np.nan
    print(f"  Encaje global: {encaje_global:.4f} ({encaje_global*100:.2f}%)")

    # Resumen semáforo
    conteo = df_resultado["Semaforo"].value_counts()
    print(f"\n  Semáforo:")
    for estado, n in conteo.items():
        print(f"    {estado}: {n} ramos")
    print("=" * 70)

    return df_resultado


# ==============================================================================
# FUNCIÓN: Proyección multi-mes (evolución del encaje en el tiempo)
# ==============================================================================
def proyectar_encaje_multimes(
    meses: Optional[list] = None,
    escenario: str = "Base",
    shocks: Optional[dict] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    Genera una tabla de encaje proyectado para múltiples meses.
    Cada mes acumula las variaciones desde enero hasta ese mes.

    Args:
        meses: Lista de meses. Si None, proyecta los 12.
        escenario: Escenario a usar para las variaciones.
        shocks: Shocks de sensibilización (se aplican en todos los meses).
        **kwargs: Argumentos adicionales pasados a calcular_encaje_por_ramo.

    Returns:
        DataFrame con Ramo, Compania, Semaforo, y una columna de Encaje por mes.
    """
    if meses is None:
        meses = MESES_ORDEN.copy()

    resultados = []
    for mes in meses:
        df_mes = calcular_encaje_por_ramo(
            mes_proyeccion=mes,
            escenario=escenario,
            shocks=shocks,
            **kwargs,
        )
        df_mes_resumen = df_mes[["Ramo", "Compania", "Encaje_Final"]].rename(
            columns={"Encaje_Final": f"Encaje_{mes.capitalize()}"}
        )
        resultados.append(df_mes_resumen)

    df_final = resultados[0]
    for df_mes in resultados[1:]:
        df_final = df_final.merge(
            df_mes, on=["Ramo", "Compania"], how="outer"
        )

    return df_final


# ==============================================================================
# FUNCIÓN: Comparación de escenarios
# ==============================================================================
def comparar_escenarios(
    mes_proyeccion: str,
    escenarios: Optional[list] = None,
    shocks: Optional[dict] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    Compara el encaje proyectado bajo diferentes escenarios para un mismo mes.

    Args:
        mes_proyeccion: Mes de referencia para la comparación.
        escenarios: Lista de escenarios a comparar.
                    Default: ['Base', 'Negativo', 'Positivo']
        shocks: Shocks adicionales (se aplican en todos los escenarios).

    Returns:
        DataFrame con Ramo, Compania, y una columna de Encaje por escenario.
    """
    if escenarios is None:
        escenarios = ["Base", "Negativo", "Positivo"]

    resultados = []
    for esc in escenarios:
        df_esc = calcular_encaje_por_ramo(
            mes_proyeccion=mes_proyeccion,
            escenario=esc,
            shocks=shocks,
            **kwargs,
        )
        df_esc_resumen = df_esc[["Ramo", "Compania", "Encaje_Final"]].rename(
            columns={"Encaje_Final": f"Encaje_{esc}"}
        )
        resultados.append(df_esc_resumen)

    df_final = resultados[0]
    for df_esc in resultados[1:]:
        df_final = df_final.merge(
            df_esc, on=["Ramo", "Compania"], how="outer"
        )

    return df_final


# ==============================================================================
# EJECUCIÓN DIRECTA (ejemplo de uso)
# ==============================================================================
if __name__ == "__main__":
    import sys

    # Ejemplo: python calcular_encaje.py marzo Base
    mes = sys.argv[1] if len(sys.argv) > 1 else None
    escenario = sys.argv[2] if len(sys.argv) > 2 else "Base"

    resultado = calcular_encaje_por_ramo(
        mes_proyeccion=mes,
        escenario=escenario,
    )

    print("\n\nDetalle por Ramo:")
    print("-" * 120)
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 140)
    print(
        resultado[
            ["Ramo", "Compania", "Posicion_Portafolio",
             "Reservas_Sensibilizadas", "Encaje_Final", "Umbral", "Semaforo"]
        ].to_string(index=False)
    )
