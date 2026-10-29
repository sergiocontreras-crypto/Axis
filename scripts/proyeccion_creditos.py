"""
AXIS - Asset-Liability eXecution & Investment Simulator
Módulo: Proyección de Créditos Contratados (Saldo y Costo)

Proyecta mensualmente el saldo vigente y el costo (intereses) de los
créditos contratados hasta su vencimiento, soportando:
  Moneda COP:
    - Fija
    - Variable indexada a IPC
    - Variable indexada a IBR
  Moneda USD:
    - Fija en USD (fija_usd)
    - Variable indexada a SOFR (sofr + spread)

Los créditos USD se proyectan en dólares y luego se convierten a COP
usando la senda de TRM proyectada.

Supuestos:
  - Amortización tipo francés (cuota fija para tasa fija) o cuota
    recalculada mensualmente para tasas variables.
  - El spread sobre IPC/IBR/SOFR se mantiene constante durante la vida
    del crédito.
  - Los pagos son mensuales (día 1 de cada mes).
  - No se contemplan prepagos ni reestructuraciones.

Inputs:
  - DataFrame de créditos con: id_credito, nombre, moneda, saldo_inicial,
    tasa_tipo (fija | ipc | ibr | fija_usd | sofr), tasa_fija o spread,
    fecha_desembolso, fecha_vencimiento, plazo_meses
  - Senda de IPC mensual proyectada (DataFrame: fecha, ipc)
  - Senda de IBR mensual proyectada (DataFrame: fecha, ibr)
  - Senda de SOFR mensual proyectada (DataFrame: fecha, sofr)
  - Senda de TRM mensual proyectada (DataFrame: fecha, trm)
    → PENDIENTE: conectar con modelo integral.

Output:
  DataFrame con columnas:
    id_credito | nombre | moneda | fecha_proyeccion | mes_numero |
    saldo_inicio_mes | tasa_efectiva_mes | interes_mes |
    amortizacion_mes | cuota_mes | saldo_fin_mes |
    trm | saldo_fin_cop | interes_cop
"""

from pathlib import Path

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
BASE = Path(
    r"G:\Unidades compartidas\Compartida Gestión Financiera (II)"
    r"\Proyecciones\Proyecto modelo proyecciones"
)

RUTA_DB_RESULTADO = str(BASE / "axis_proyeccion_creditos.db")
MESES_ANIO = 12

# Tasas base para pruebas (constantes).
# PENDIENTE: reemplazar con sendas proyectadas del modelo integral.
IPC_PRUEBAS = 0.0707  # 7.07% EA
IBR_PRUEBAS = 0.0707  # 7.07% EA
SOFR_PRUEBAS = 0.0525  # 5.25% EA (tasa SOFR referencia)
TRM_PRUEBAS = 4200.0   # COP/USD referencia


# ═════════════════════════════════════════════
# DATOS DE EJEMPLO (se reemplazarán con inputs definitivos)
# ═════════════════════════════════════════════

# Los créditos se definen con su saldo, plazo al vencimiento y spread.
# El spread es un input propio de cada obligación financiera.
CREDITOS_EJEMPLO = pd.DataFrame([
    {
        "id_credito": "CRED-001",
        "nombre": "Crédito Bancolombia Tasa Fija",
        "moneda": "COP",
        "saldo_inicial": 50_000_000_000,
        "tasa_tipo": "fija",
        "tasa_fija": 0.1050,       # 10.50% EA fija
        "spread": 0.0,             # No aplica para tasa fija
        "fecha_desembolso": "2025-01-01",
        "fecha_vencimiento": "2028-01-01",
        "plazo_meses": 36,
    },
    {
        "id_credito": "CRED-002",
        "nombre": "Crédito BBVA IBR + Spread",
        "moneda": "COP",
        "saldo_inicial": 30_000_000_000,
        "tasa_tipo": "ibr",
        "tasa_fija": 0.0,
        "spread": 0.0200,          # IBR + 2.00%
        "fecha_desembolso": "2025-06-01",
        "fecha_vencimiento": "2027-06-01",
        "plazo_meses": 24,
    },
    {
        "id_credito": "CRED-003",
        "nombre": "Crédito Davivienda IPC + Spread",
        "moneda": "COP",
        "saldo_inicial": 20_000_000_000,
        "tasa_tipo": "ipc",
        "tasa_fija": 0.0,
        "spread": 0.0350,          # IPC + 3.50%
        "fecha_desembolso": "2026-01-01",
        "fecha_vencimiento": "2029-01-01",
        "plazo_meses": 36,
    },
    {
        "id_credito": "CRED-004",
        "nombre": "Crédito Citi USD Tasa Fija",
        "moneda": "USD",
        "saldo_inicial": 12_000_000,   # 12M USD
        "tasa_tipo": "fija_usd",
        "tasa_fija": 0.0650,           # 6.50% EA fija en USD
        "spread": 0.0,
        "fecha_desembolso": "2025-03-01",
        "fecha_vencimiento": "2028-03-01",
        "plazo_meses": 36,
    },
    {
        "id_credito": "CRED-005",
        "nombre": "Crédito JPMorgan SOFR + Spread",
        "moneda": "USD",
        "saldo_inicial": 8_000_000,    # 8M USD
        "tasa_tipo": "sofr",
        "tasa_fija": 0.0,
        "spread": 0.0150,              # SOFR + 1.50%
        "fecha_desembolso": "2025-09-01",
        "fecha_vencimiento": "2027-09-01",
        "plazo_meses": 24,
    },
])

def _generar_senda_constante(valor: float, fecha_inicio: str, meses: int, columna: str) -> pd.DataFrame:
    """
    Genera una senda con valor constante para pruebas.

    Args:
        valor: Tasa constante (decimal)
        fecha_inicio: Primer mes ("YYYY-MM-DD")
        meses: Cantidad de meses a generar
        columna: Nombre de la columna ("ipc" o "ibr")

    Returns:
        DataFrame con columnas: fecha, <columna>
    """
    inicio = pd.Timestamp(fecha_inicio)
    fechas = pd.date_range(start=inicio, periods=meses, freq="MS")
    return pd.DataFrame({
        "fecha": [f.strftime("%Y-%m-%d") for f in fechas],
        columna: [valor] * meses,
    })


# PENDIENTE: Conectar con sendas proyectadas del modelo integral.
# Por ahora se genera un valor constante para pruebas.
SENDA_IPC_EJEMPLO = _generar_senda_constante(IPC_PRUEBAS, "2025-01-01", 60, "ipc")
SENDA_IBR_EJEMPLO = _generar_senda_constante(IBR_PRUEBAS, "2025-01-01", 60, "ibr")
SENDA_SOFR_EJEMPLO = _generar_senda_constante(SOFR_PRUEBAS, "2025-01-01", 60, "sofr")
SENDA_TRM_EJEMPLO = _generar_senda_constante(TRM_PRUEBAS, "2025-01-01", 60, "trm")


# ═════════════════════════════════════════════
# FUNCIONES AUXILIARES
# ═════════════════════════════════════════════

def calcular_tasa_mensual(tasa_ea: float) -> float:
    """
    Convierte tasa efectiva anual a tasa efectiva mensual.

    Fórmula: (1 + TEA)^(1/12) - 1

    Args:
        tasa_ea: Tasa efectiva anual (decimal, e.g. 0.10 = 10%)

    Returns:
        Tasa efectiva mensual (decimal)
    """
    return (1 + tasa_ea) ** (1 / MESES_ANIO) - 1


def calcular_cuota_fija(saldo: float, tasa_mensual: float, meses_restantes: int) -> float:
    """
    Calcula la cuota fija mensual (sistema francés).

    Fórmula: C = S * [i * (1+i)^n] / [(1+i)^n - 1]

    Args:
        saldo: Saldo vigente del crédito
        tasa_mensual: Tasa efectiva mensual
        meses_restantes: Meses pendientes de pago

    Returns:
        Valor de la cuota mensual
    """
    if tasa_mensual == 0:
        return saldo / meses_restantes if meses_restantes > 0 else 0.0

    factor = (1 + tasa_mensual) ** meses_restantes
    cuota = saldo * (tasa_mensual * factor) / (factor - 1)
    return cuota


def obtener_tasa_efectiva(
    tasa_tipo: str,
    tasa_fija: float,
    spread: float,
    fecha: str,
    senda_ipc: dict[str, float],
    senda_ibr: dict[str, float],
    senda_sofr: dict[str, float] | None = None,
) -> float:
    """
    Determina la tasa efectiva anual vigente para un mes dado.

    Args:
        tasa_tipo: "fija", "ipc", "ibr", "fija_usd" o "sofr"
        tasa_fija: Tasa fija del crédito (si aplica)
        spread: Spread sobre el indicador variable
        fecha: Fecha del mes de proyección
        senda_ipc: Dict {fecha: ipc_anual}
        senda_ibr: Dict {fecha: ibr_anual}
        senda_sofr: Dict {fecha: sofr_anual} (para créditos USD)

    Returns:
        Tasa efectiva anual aplicable al mes
    """
    if tasa_tipo in ("fija", "fija_usd"):
        return tasa_fija

    if tasa_tipo == "ipc":
        ipc_base = senda_ipc.get(fecha, 0.0)
        # IPC + Spread: tasa compuesta
        return (1 + ipc_base) * (1 + spread) - 1

    if tasa_tipo == "ibr":
        ibr_base = senda_ibr.get(fecha, 0.0)
        return ibr_base + spread

    if tasa_tipo == "sofr":
        if senda_sofr is None:
            senda_sofr = {}
        sofr_base = senda_sofr.get(fecha, SOFR_PRUEBAS)
        return sofr_base + spread

    raise ValueError(f"Tipo de tasa no soportado: '{tasa_tipo}'. "
                     f"Use 'fija', 'fija_usd', 'ipc', 'ibr' o 'sofr'.")


# ═════════════════════════════════════════════
# FUNCIONES DE PROYECCIÓN
# ═════════════════════════════════════════════

def generar_periodos_credito(
    fecha_inicio_proyeccion: str,
    fecha_vencimiento: str,
) -> list[str]:
    """
    Genera la lista de meses de proyección desde el inicio hasta
    el vencimiento del crédito.

    Args:
        fecha_inicio_proyeccion: Primer mes ("YYYY-MM-DD" o "YYYY-MM")
        fecha_vencimiento: Fecha de vencimiento del crédito

    Returns:
        Lista de fechas "YYYY-MM-01" mensuales
    """
    if len(fecha_inicio_proyeccion) == 7:
        fecha_inicio_proyeccion = f"{fecha_inicio_proyeccion}-01"
    if len(fecha_vencimiento) == 7:
        fecha_vencimiento = f"{fecha_vencimiento}-01"

    inicio = pd.Timestamp(fecha_inicio_proyeccion)
    fin = pd.Timestamp(fecha_vencimiento)

    # La proyección va hasta el mes anterior al vencimiento
    rango = pd.date_range(start=inicio, end=fin, freq="MS")
    return [d.strftime("%Y-%m-%d") for d in rango]


def calcular_mes_numero(
    fecha_desembolso: str,
    fecha_proyeccion: str,
) -> int:
    """
    Calcula el número de mes transcurrido desde el desembolso.

    Args:
        fecha_desembolso: Fecha de desembolso del crédito
        fecha_proyeccion: Fecha del mes de proyección

    Returns:
        Número de mes (1-based)
    """
    desembolso = pd.Timestamp(fecha_desembolso)
    proyeccion = pd.Timestamp(fecha_proyeccion)
    return (proyeccion.year - desembolso.year) * 12 + (proyeccion.month - desembolso.month) + 1


def proyectar_credito(
    id_credito: str,
    nombre: str,
    moneda: str,
    saldo_inicial: float,
    tasa_tipo: str,
    tasa_fija: float,
    spread: float,
    fecha_desembolso: str,
    plazo_meses: int,
    periodos: list[str],
    senda_ipc: dict[str, float],
    senda_ibr: dict[str, float],
    senda_sofr: dict[str, float] | None = None,
    senda_trm: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Proyecta mes a mes el saldo y costo de un crédito individual.

    Para tasa fija: cuota constante (sistema francés).
    Para tasa variable: cuota recalculada cada mes con la tasa vigente
    y los meses restantes.

    Los créditos USD se proyectan en dólares y se convierten a COP
    con la TRM del mes correspondiente.

    Args:
        id_credito: Identificador del crédito
        nombre: Nombre descriptivo
        moneda: "COP" o "USD"
        saldo_inicial: Monto desembolsado (en moneda de origen)
        tasa_tipo: "fija", "fija_usd", "ipc", "ibr" o "sofr"
        tasa_fija: Tasa fija anual (si aplica)
        spread: Spread sobre indicador variable
        fecha_desembolso: Fecha de desembolso
        plazo_meses: Plazo total en meses
        periodos: Lista de fechas de proyección
        senda_ipc: Dict {fecha: ipc}
        senda_ibr: Dict {fecha: ibr}
        senda_sofr: Dict {fecha: sofr} para créditos USD
        senda_trm: Dict {fecha: trm} para conversión USD→COP

    Returns:
        DataFrame con proyección mensual del crédito
    """
    filas = []
    saldo_vigente = saldo_inicial

    for periodo in periodos:
        if saldo_vigente <= 0:
            break

        mes_numero = calcular_mes_numero(fecha_desembolso, periodo)
        meses_restantes = plazo_meses - mes_numero + 1

        if meses_restantes <= 0:
            break

        # Tasa efectiva anual vigente para el mes
        tasa_ea = obtener_tasa_efectiva(
            tasa_tipo=tasa_tipo,
            tasa_fija=tasa_fija,
            spread=spread,
            fecha=periodo,
            senda_ipc=senda_ipc,
            senda_ibr=senda_ibr,
            senda_sofr=senda_sofr,
        )

        # Convertir a tasa mensual
        tasa_mensual = calcular_tasa_mensual(tasa_ea)

        # Calcular cuota (recalculada cada mes para tasas variables)
        cuota = calcular_cuota_fija(saldo_vigente, tasa_mensual, meses_restantes)

        # Descomponer cuota
        interes = saldo_vigente * tasa_mensual
        amortizacion = cuota - interes
        saldo_fin = saldo_vigente - amortizacion

        # Ajuste para último mes (evitar saldos negativos por redondeo)
        if saldo_fin < 0:
            amortizacion = saldo_vigente
            cuota = interes + amortizacion
            saldo_fin = 0.0

        # TRM para conversión (solo aplica a USD)
        trm = 1.0
        if moneda == "USD" and senda_trm:
            trm = senda_trm.get(periodo, TRM_PRUEBAS)
        elif moneda == "USD":
            trm = TRM_PRUEBAS

        filas.append({
            "id_credito": id_credito,
            "nombre": nombre,
            "moneda": moneda,
            "tasa_tipo": tasa_tipo,
            "fecha_proyeccion": periodo,
            "mes_numero": mes_numero,
            "saldo_inicio_mes": round(saldo_vigente, 2),
            "tasa_ea_vigente": round(tasa_ea, 6),
            "tasa_mensual": round(tasa_mensual, 6),
            "interes_mes": round(interes, 2),
            "amortizacion_mes": round(amortizacion, 2),
            "cuota_mes": round(cuota, 2),
            "saldo_fin_mes": round(saldo_fin, 2),
            "trm": round(trm, 2),
            "saldo_fin_cop": round(saldo_fin * trm, 2) if moneda == "USD" else round(saldo_fin, 2),
            "interes_cop": round(interes * trm, 2) if moneda == "USD" else round(interes, 2),
        })

        saldo_vigente = saldo_fin

    return pd.DataFrame(filas)


def proyectar_creditos_contratados(
    fecha_inicio: str,
    df_creditos: pd.DataFrame | None = None,
    df_senda_ipc: pd.DataFrame | None = None,
    df_senda_ibr: pd.DataFrame | None = None,
    df_senda_sofr: pd.DataFrame | None = None,
    df_senda_trm: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Función principal: proyecta saldo y costo de todos los créditos (COP y USD).

    Itera sobre cada crédito, determina sus períodos de proyección
    (desde fecha_inicio hasta su vencimiento individual) y calcula
    mes a mes el saldo e intereses. Los créditos USD se convierten a COP.

    Args:
        fecha_inicio: Primer mes de proyección ("YYYY-MM")
        df_creditos: DataFrame con los créditos (usa ejemplo si None)
        df_senda_ipc: Senda IPC proyectada (usa ejemplo si None)
        df_senda_ibr: Senda IBR proyectada (usa ejemplo si None)
        df_senda_sofr: Senda SOFR proyectada (usa ejemplo si None)
        df_senda_trm: Senda TRM proyectada (usa ejemplo si None)
            → PENDIENTE: conectar con modelo integral.

    Returns:
        DataFrame consolidado con la proyección de todos los créditos
    """
    print("=" * 60)
    print("AXIS - Proyección Créditos Contratados (COP + USD)")
    print("=" * 60)

    # Cargar datos o usar ejemplos
    if df_creditos is None:
        print("\n  ⚠️  Usando créditos de ejemplo (input definitivo no disponible)")
        df_creditos = CREDITOS_EJEMPLO.copy()
    else:
        print(f"\n  ✓ Créditos cargados: {len(df_creditos)} registros")

    # Asegurar campo moneda (retrocompatibilidad)
    if "moneda" not in df_creditos.columns:
        df_creditos["moneda"] = "COP"

    if df_senda_ipc is None:
        print("  ⚠️  Usando senda IPC de ejemplo")
        df_senda_ipc = SENDA_IPC_EJEMPLO.copy()
    else:
        print(f"  ✓ Senda IPC cargada: {len(df_senda_ipc)} meses")

    if df_senda_ibr is None:
        print("  ⚠️  Usando senda IBR de ejemplo")
        df_senda_ibr = SENDA_IBR_EJEMPLO.copy()
    else:
        print(f"  ✓ Senda IBR cargada: {len(df_senda_ibr)} meses")

    if df_senda_sofr is None:
        print("  ⚠️  Usando senda SOFR de ejemplo")
        df_senda_sofr = SENDA_SOFR_EJEMPLO.copy()
    else:
        print(f"  ✓ Senda SOFR cargada: {len(df_senda_sofr)} meses")

    if df_senda_trm is None:
        print("  ⚠️  Usando senda TRM de ejemplo (constante)")
        df_senda_trm = SENDA_TRM_EJEMPLO.copy()
    else:
        print(f"  ✓ Senda TRM cargada: {len(df_senda_trm)} meses")

    # Convertir sendas a dict para lookup rápido
    senda_ipc = dict(zip(df_senda_ipc["fecha"].astype(str), df_senda_ipc["ipc"]))
    senda_ibr = dict(zip(df_senda_ibr["fecha"].astype(str), df_senda_ibr["ibr"]))
    senda_sofr = dict(zip(df_senda_sofr["fecha"].astype(str), df_senda_sofr["sofr"]))
    senda_trm = dict(zip(df_senda_trm["fecha"].astype(str), df_senda_trm["trm"]))

    # Resumen
    n_cop = (df_creditos["moneda"] == "COP").sum()
    n_usd = (df_creditos["moneda"] == "USD").sum()
    print(f"\n  Fecha inicio proyección: {fecha_inicio}")
    print(f"  Créditos a proyectar: {len(df_creditos)} ({n_cop} COP, {n_usd} USD)")
    print(f"  Saldo COP: ${df_creditos[df_creditos['moneda']=='COP']['saldo_inicial'].sum():,.0f}")
    if n_usd > 0:
        print(f"  Saldo USD: USD ${df_creditos[df_creditos['moneda']=='USD']['saldo_inicial'].sum():,.0f}")
    print()

    # Proyectar cada crédito
    resultados = []

    for _, row in df_creditos.iterrows():
        fecha_vencimiento = row["fecha_vencimiento"]
        moneda = row.get("moneda", "COP")

        # Períodos: desde fecha_inicio hasta vencimiento del crédito
        periodos = generar_periodos_credito(fecha_inicio, fecha_vencimiento)

        if not periodos:
            print(f"  ⚠️  {row['nombre']}: ya vencido, se omite")
            continue

        df_proy = proyectar_credito(
            id_credito=str(row["id_credito"]),
            nombre=row["nombre"],
            moneda=moneda,
            saldo_inicial=row["saldo_inicial"],
            tasa_tipo=row["tasa_tipo"],
            tasa_fija=row["tasa_fija"],
            spread=row["spread"],
            fecha_desembolso=row["fecha_desembolso"],
            plazo_meses=int(row["plazo_meses"]),
            periodos=periodos,
            senda_ipc=senda_ipc,
            senda_ibr=senda_ibr,
            senda_sofr=senda_sofr,
            senda_trm=senda_trm,
        )

        if not df_proy.empty:
            print(f"  ✓ {row['nombre']} [{moneda}]: {len(df_proy)} meses proyectados")
            resultados.append(df_proy)

    if not resultados:
        print("\n  ⚠️  No hay créditos para proyectar en el período indicado")
        return pd.DataFrame()

    df_final = pd.concat(resultados, ignore_index=True)

    # Resumen
    interes_total_cop = df_final["interes_cop"].sum()
    print(f"\n{'─' * 60}")
    print(f"✓ Proyección completada: {len(df_final):,} registros")
    print(f"  Costo total (intereses en COP): ${interes_total_cop:,.0f}")
    print(f"{'─' * 60}")

    return df_final


# ═════════════════════════════════════════════
# PERSISTENCIA
# ═════════════════════════════════════════════

def guardar_proyeccion(
    df: pd.DataFrame,
    ruta_db: str = RUTA_DB_RESULTADO,
) -> None:
    """
    Persiste el resultado en SQLite con tablas claras de saldo y costo.

    Genera tres tablas:
      - saldo_mensual: saldo vigente de cada crédito por mes
      - costo_mensual: interés (costo) de cada crédito por mes
      - detalle_proyeccion: detalle completo para auditoría

    Las tablas saldo_mensual y costo_mensual tienen formato pivoteado:
    cada fila es un mes, cada columna es un crédito.

    Args:
        df: DataFrame con la proyección completa
        ruta_db: Ruta a la DB de resultado
    """
    import sqlite3

    # Tabla pivoteada de saldo mensual (saldo_fin_mes por crédito)
    pivot_saldo = df.pivot_table(
        index="fecha_proyeccion",
        columns="id_credito",
        values="saldo_fin_mes",
        aggfunc="first",
    ).reset_index()
    pivot_saldo.rename(columns={"fecha_proyeccion": "fecha"}, inplace=True)

    # Tabla pivoteada de costo/interés mensual por crédito
    pivot_costo = df.pivot_table(
        index="fecha_proyeccion",
        columns="id_credito",
        values="interes_mes",
        aggfunc="first",
    ).reset_index()
    pivot_costo.rename(columns={"fecha_proyeccion": "fecha"}, inplace=True)

    conn = sqlite3.connect(ruta_db)
    try:
        # Tabla 1: Saldo mensual por crédito
        pivot_saldo.to_sql("saldo_mensual", conn, if_exists="replace", index=False)

        # Tabla 2: Costo (interés) mensual por crédito
        pivot_costo.to_sql("costo_mensual", conn, if_exists="replace", index=False)

        # Tabla 3: Detalle completo para auditoría
        df.to_sql("detalle_proyeccion", conn, if_exists="replace", index=False)
    finally:
        conn.close()

    print(f"  ✓ Tabla 'saldo_mensual': saldo de cada crédito por mes")
    print(f"  ✓ Tabla 'costo_mensual': interés de cada crédito por mes")
    print(f"  ✓ Tabla 'detalle_proyeccion': {len(df):,} registros (detalle)")
    print(f"    → {ruta_db}")


def exportar_excel(
    df: pd.DataFrame,
    ruta: str | None = None,
) -> None:
    """
    Exporta la proyección a Excel con hojas de saldo y costo mensual.

    Args:
        df: DataFrame con la proyección
        ruta: Ruta del archivo de salida (opcional)
    """
    if ruta is None:
        ruta = str(BASE / "Proyeccion_Creditos_Contratados.xlsx")

    # Pivotear saldo mensual
    pivot_saldo = df.pivot_table(
        index="fecha_proyeccion",
        columns="nombre",
        values="saldo_fin_mes",
        aggfunc="first",
    ).reset_index()
    pivot_saldo.rename(columns={"fecha_proyeccion": "fecha"}, inplace=True)

    # Pivotear costo mensual
    pivot_costo = df.pivot_table(
        index="fecha_proyeccion",
        columns="nombre",
        values="interes_mes",
        aggfunc="first",
    ).reset_index()
    pivot_costo.rename(columns={"fecha_proyeccion": "fecha"}, inplace=True)

    with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
        pivot_saldo.to_excel(writer, sheet_name="Saldo Mensual", index=False)
        pivot_costo.to_excel(writer, sheet_name="Costo Mensual", index=False)
        df.to_excel(writer, sheet_name="Detalle", index=False)

    print(f"  ✓ Exportado a Excel: {ruta}")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main() -> None:
    """Ejecuta la proyección de créditos contratados."""

    fecha_inicio = "2026-06"

    # Proyectar (incluye COP + USD)
    df_resultado = proyectar_creditos_contratados(
        fecha_inicio=fecha_inicio,
    )

    if df_resultado.empty:
        print("\nNo se generaron resultados.")
        return

    # Mostrar resumen por crédito
    print("\n── Resumen por crédito ──")
    resumen = (
        df_resultado
        .groupby(["id_credito", "nombre", "moneda", "tasa_tipo"])
        .agg(
            saldo_inicial=("saldo_inicio_mes", "first"),
            saldo_final=("saldo_fin_mes", "last"),
            interes_total=("interes_mes", "sum"),
            interes_total_cop=("interes_cop", "sum"),
            meses=("mes_numero", "count"),
        )
        .reset_index()
    )
    print(resumen.to_string(index=False))

    # Guardar en SQLite
    guardar_proyeccion(df_resultado)

    # Exportar a Excel
    exportar_excel(df_resultado)

    print(f"\n✓ Fin del módulo")


if __name__ == "__main__":
    main()
