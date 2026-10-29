"""
AXIS - Asset-Liability eXecution & Investment Simulator
Módulo: Proyección Activos Alternativos (Fondos de Capital Privado)

Proyecta mensualmente el valor de mercado de los fondos de capital privado
aplicando una tasa EA por escenario.

Supuestos:
  - Portafolio estático (sin llamadas de capital ni distribuciones durante la proyección)
  - Dividendos/distribuciones implícitos en la tasa EA
  - Fondos locales e internacionales se proyectan por separado en moneda de origen
  - La valoración mensual se compone sobre el valor del mes anterior
  - Los fondos FCP 4G (infraestructura) se valoran con IPC + spread
    en vez de la tasa EA fija de escenario.

Inputs:
  - Tabla SQLite con posiciones actuales de fondos de capital privado
  - Fecha inicio y fin de proyección
  - Tasa EA por escenario para alternativos locales e internacionales
  - Senda IPC proyectada para los fondos FCP 4G (IPC + spread)

Output:
  DataFrame con columnas:
    escenario | tipo_fondo | fondo | moneda | fecha_proyeccion |
    valor_mercado | pyg_mensual | pyg_acumulado
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
BASE = Path(__file__).resolve().parent

RUTA_DB_PORTAFOLIO = str(BASE / "portafolio.db")

# Clasificación de tipo de fondo
TIPO_FONDO_LOCAL = "alt_local"
TIPO_FONDO_INTL = "alt_internacional"

# ─────────────────────────────────────────────
# FCP 4G - TÍTULOS CON VALORACIÓN IPC + SPREAD
# ─────────────────────────────────────────────
# Estos fondos de infraestructura 4G se valoran con una tasa
# compuesta IPC + spread, diferente al resto que usa tasa EA fija.
# NOTA: La identificación ahora es DINÁMICA desde portafolio_clasificado
# usando la columna `es_ipc = 'SI'` en vez de IDs hardcodeados.
# Las constantes se mantienen solo como fallback si la DB no tiene la columna.
FCP_4G_IDS = [829887, 829889, 884159, 795782]
FCP_4G_NOMBRES = [
    "FCP 4G COMPART LIQUIDEZ I",
    "FCP 4G DEUDA TIPO E",
    "FCP 4G SURA ASSET MANAGMENT",
    "FDO CAP PRIVADO BROOKFIELD COL",
]

# Spread de cada FCP 4G sobre IPC (input por título)
# PENDIENTE: conectar con input definitivo del modelo
FCP_4G_SPREADS = {
    829887: 0.0350,  # FCP 4G COMPART LIQUIDEZ I: IPC + 3.50%
    829889: 0.0350,  # FCP 4G DEUDA TIPO E: IPC + 3.50%
    884159: 0.0350,  # FCP 4G SURA ASSET MANAGMENT: IPC + 3.50%
    795782: 0.0350,  # FDO CAP PRIVADO BROOKFIELD COL: IPC + 3.50%
}

# Spread por defecto para títulos detectados dinámicamente con es_ipc='SI'
SPREAD_IPC_DEFAULT = 0.0350  # 3.50%

# Tasa IPC para pruebas (constante).
# PENDIENTE: conectar con senda IPC proyectada del modelo integral.
IPC_PRUEBAS = 0.0707  # 7.07% EA


# ═════════════════════════════════════════════
# TABLA DE EJEMPLO (temporal hasta tener definitiva)
# ═════════════════════════════════════════════

PORTAFOLIO_EJEMPLO = pd.DataFrame([
    # Fondos locales (tasa EA fija por escenario)
    {"id_posicion": 880588, "fondo": "Fondo Inmobiliario Colombia (PEI)",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 11_430_220_000},
    {"id_posicion": 859041, "fondo": "FCP MAS Colombia",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 9_090_845_000},
    # Fondos FCP 4G (IPC + spread) - se tratan diferente
    {"id_posicion": 829887, "fondo": "FCP 4G COMPART LIQUIDEZ I",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 4_500_000_000},
    {"id_posicion": 829889, "fondo": "FCP 4G DEUDA TIPO E",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 3_800_000_000},
    {"id_posicion": 884159, "fondo": "FCP 4G SURA ASSET MANAGMENT",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 5_200_000_000},
    {"id_posicion": 795782, "fondo": "FDO CAP PRIVADO BROOKFIELD COL",
     "tipo_fondo": TIPO_FONDO_LOCAL, "moneda": "COP",
     "valor_mercado": 6_100_000_000},
    # Fondos internacionales (tasa EA fija por escenario)
    {"id_posicion": 871256, "fondo": "PIMCO Funds: Global Investors",
     "tipo_fondo": TIPO_FONDO_INTL, "moneda": "USD",
     "valor_mercado": 5_580_000},
    {"id_posicion": 872842, "fondo": "ETF GLDM Gold Shares",
     "tipo_fondo": TIPO_FONDO_INTL, "moneda": "USD",
     "valor_mercado": 2_200_000},
])


# ═════════════════════════════════════════════
# FUNCIONES DE CÁLCULO
# ═════════════════════════════════════════════

def convertir_ea_a_diaria(tasa_ea: float) -> float:
    """
    Convierte una tasa efectiva anual a tasa efectiva diaria compuesta (base 365).

    Args:
        tasa_ea: Tasa efectiva anual (ej: 0.10 para 10%)

    Returns:
        Tasa efectiva diaria equivalente
    """
    return (1 + tasa_ea) ** (1 / 365) - 1


# Mantener para compatibilidad
def convertir_ea_a_mensual(tasa_ea: float) -> float:
    """Convierte EA a mensual (legacy)."""
    return (1 + tasa_ea) ** (1 / 12) - 1


def generar_periodos(fecha_inicio: str, fecha_fin: str) -> list[date]:
    """
    Genera una lista de fechas mensuales (primer día del mes) entre inicio y fin.

    Args:
        fecha_inicio: Primer mes de proyección en formato "YYYY-MM"
        fecha_fin: Último mes de proyección en formato "YYYY-MM"

    Returns:
        Lista de objetos date correspondientes al primer día de cada mes
    """
    inicio = pd.Timestamp(f"{fecha_inicio}-01")
    fin = pd.Timestamp(f"{fecha_fin}-01")
    rango = pd.date_range(start=inicio, end=fin, freq="MS")
    return [d.date() for d in rango]


def cargar_portafolio_alternativos(
    ruta_db: str = RUTA_DB_PORTAFOLIO,
) -> pd.DataFrame:
    """
    Carga las posiciones de activos alternativos desde portafolio.db.

    Filtra por asset_class_definitivo:
      - 'Fondo Capital Priv. Local' → alt_local
      - 'Fondo Capital Priv. Extranjero' → alt_internacional
    O por numeral_regimen (1.9, 1.11, 1.11.infra, 2.7, 2.8) excluyendo PEI.

    Incluye columna `es_ipc` para identificar dinámicamente los fondos
    que se valoran con IPC + spread (antes hardcodeado como FCP_4G_IDS).

    Args:
        ruta_db: Ruta a portafolio.db

    Returns:
        DataFrame con columnas: id_posicion, fondo, tipo_fondo, moneda, valor_mercado, es_ipc
    """
    query = """
        SELECT
            id_posicion,
            especie AS fondo,
            moneda,
            CAST(valor_mercado AS REAL) AS valor_mercado,
            asset_class_definitivo,
            COALESCE(UPPER(TRIM(es_ipc)), 'NO') AS es_ipc
        FROM portafolio_clasificado
        WHERE (
            asset_class_definitivo IN ('Fondo Capital Priv. Local', 'Fondo Capital Priv. Extranjero')
            OR numeral_regimen IN ('1.9', '1.11', '1.11.infra', '2.7', '2.8')
        )
        AND asset_class_definitivo NOT IN ('PEI', 'COLCAP', 'MXWD', 'COCAP')
        AND CAST(valor_mercado AS REAL) > 0
    """
    try:
        conn = sqlite3.connect(ruta_db)
        df = pd.read_sql(query, conn)
        conn.close()

        if not df.empty:
            # Clasificar local vs internacional por asset_class o moneda
            def clasificar_tipo(row):
                ac = row.get("asset_class_definitivo", "")
                if ac == "Fondo Capital Priv. Extranjero":
                    return TIPO_FONDO_INTL
                elif ac == "Fondo Capital Priv. Local":
                    return TIPO_FONDO_LOCAL
                else:
                    # Fallback por moneda
                    return TIPO_FONDO_LOCAL if row["moneda"] == "COP" else TIPO_FONDO_INTL

            df["tipo_fondo"] = df.apply(clasificar_tipo, axis=1)
            df = df.drop(columns=["asset_class_definitivo"])
            n_local = (df["tipo_fondo"] == TIPO_FONDO_LOCAL).sum()
            n_intl = (df["tipo_fondo"] == TIPO_FONDO_INTL).sum()
            n_ipc = (df["es_ipc"] == "SI").sum()
            print(f"  ✓ Portafolio Alternativos cargado: {len(df)} posiciones")
            print(f"    Local: {n_local} | Internacional: {n_intl} | IPC+Spread: {n_ipc}")
            print(f"    VM Total: ${df['valor_mercado'].sum()/1e9:,.1f} MM COP")
            return df
    except Exception as e:
        print(f"  ⚠️  Error leyendo portafolio.db: {e}")
        # Fallback: intentar sin la columna es_ipc (DB legacy)
        try:
            conn = sqlite3.connect(ruta_db)
            df = pd.read_sql(query.replace("COALESCE(UPPER(TRIM(es_ipc)), 'NO') AS es_ipc",
                                           "'NO' AS es_ipc"), conn)
            conn.close()
            if not df.empty:
                # Marcar los FCP_4G_IDS como fallback
                df["es_ipc"] = df["id_posicion"].apply(
                    lambda x: "SI" if int(x) in FCP_4G_IDS else "NO")
                def clasificar_tipo(row):
                    ac = row.get("asset_class_definitivo", "")
                    if ac == "Fondo Capital Priv. Extranjero":
                        return TIPO_FONDO_INTL
                    return TIPO_FONDO_LOCAL if row["moneda"] == "COP" else TIPO_FONDO_INTL
                df["tipo_fondo"] = df.apply(clasificar_tipo, axis=1)
                df = df.drop(columns=["asset_class_definitivo"])
                print(f"  ⚠️  Fallback: usando FCP_4G_IDS hardcodeado (columna es_ipc no disponible)")
                return df
        except Exception:
            pass

    print("  ℹ️  Usando portafolio de ejemplo (portafolio.db no disponible)")
    return PORTAFOLIO_EJEMPLO.copy()


def proyectar_bloque(portafolio: pd.DataFrame,
                     periodos: list[date],
                     tasa_ea: float,
                     escenario: str,
                     senda_trm: dict[str, float] | None = None,
                     trm_inicio_ancla: float | None = None) -> pd.DataFrame:
    """
    Proyecta el valor de mercado mes a mes usando composición diaria (base 365).
    Para fondos internacionales, aplica efecto cambiario si se provee senda_trm.

    Args:
        portafolio: DataFrame con posiciones (id_posicion, fondo, tipo_fondo, moneda, valor_mercado)
        periodos: Lista de fechas mensuales a proyectar
        tasa_ea: Tasa efectiva anual del escenario (retorno en moneda local para COP, en USD para intl)
        escenario: Nombre del escenario
        senda_trm: Dict {fecha_str: trm} con la TRM por corte mensual.
            Si se provee, fondos internacionales (moneda != COP) se convierten a COP con la TRM del corte.
            VM_COP(t) = VM_USD(t) * TRM(t). El VM inicial ya está en COP (convertido con TRM del portafolio).

    Returns:
        DataFrame con la proyección mensual de cada fondo
    """
    tasa_diaria = convertir_ea_a_diaria(tasa_ea)
    filas = []

    # TRM del primer periodo (para calcular VM en USD inicial si hay senda)
    trm_t0 = None
    if senda_trm:
        if trm_inicio_ancla is not None and trm_inicio_ancla > 0:
            # Ancla REAL de arranque del tramo (fuente de verdad); NO altera los nodos
            # mensuales de la senda, que siguen moviendo el VM intermedio.
            trm_t0 = float(trm_inicio_ancla)
        else:
            primera_fecha = periodos[0].strftime("%Y-%m-%d")
            trm_t0 = senda_trm.get(primera_fecha)
            if trm_t0 is None:
                # Buscar la primera TRM disponible
                trm_t0 = list(senda_trm.values())[0] if senda_trm else None

    for _, posicion in portafolio.iterrows():
        valor_anterior = posicion["valor_mercado"]  # en COP (del portafolio_clasificado)
        moneda = posicion.get("moneda", "COP")
        es_intl = moneda != "COP" and senda_trm is not None and trm_t0 is not None and trm_t0 > 0

        # Para fondos intl: convertir VM inicial a USD
        if es_intl:
            vm_usd_anterior = valor_anterior / trm_t0
        else:
            vm_usd_anterior = None

        pyg_acumulado = 0.0

        for i, periodo in enumerate(periodos):
            if i == 0:
                # T0: registrar VM original sin retorno
                filas.append({
                    "escenario": escenario,
                    "id_posicion": int(posicion["id_posicion"]),
                    "tipo_fondo": posicion["tipo_fondo"],
                    "fondo": posicion["fondo"],
                    "moneda": posicion["moneda"],
                    "fecha_proyeccion": periodo.strftime("%Y-%m-%d"),
                    "valor_mercado": round(valor_anterior, 2),
                    "pyg_mensual": 0.0,
                    "pyg_acumulado": 0.0,
                    "metodologia": "tasa_ea_fija" + (" + TRM" if es_intl else ""),
                })
                continue

            dias = (periodo - periodos[i - 1]).days
            factor = (1 + tasa_diaria) ** dias

            if es_intl:
                # Proyectar en USD y convertir con TRM del corte
                vm_usd_actual = vm_usd_anterior * factor
                trm_corte = senda_trm.get(periodo.strftime("%Y-%m-%d"), trm_t0)
                valor_actual = vm_usd_actual * trm_corte
                vm_usd_anterior = vm_usd_actual
            else:
                valor_actual = valor_anterior * factor

            pyg_mes = valor_actual - valor_anterior
            pyg_acumulado += pyg_mes

            filas.append({
                "escenario": escenario,
                "id_posicion": int(posicion["id_posicion"]),
                "tipo_fondo": posicion["tipo_fondo"],
                "fondo": posicion["fondo"],
                "moneda": posicion["moneda"],
                "fecha_proyeccion": periodo.strftime("%Y-%m-%d"),
                "valor_mercado": round(valor_actual, 2),
                "pyg_mensual": round(pyg_mes, 2),
                "pyg_acumulado": round(pyg_acumulado, 2),
                "metodologia": "tasa_ea_fija" + (" + TRM" if es_intl else ""),
            })

            valor_anterior = valor_actual

    return pd.DataFrame(filas)


def proyectar_bloque_ipc_spread(
    portafolio: pd.DataFrame,
    periodos: list[date],
    senda_ipc: dict[str, float],
    spreads: dict[int, float],
    escenario: str,
) -> pd.DataFrame:
    """
    Proyecta el valor de mercado de los fondos FCP 4G usando IPC + spread.

    La tasa efectiva anual de cada título es: (1 + IPC) * (1 + spread) - 1
    y se convierte a diaria para componer por días reales entre cortes.

    Args:
        portafolio: DataFrame con posiciones FCP 4G
        periodos: Lista de fechas mensuales a proyectar
        senda_ipc: Dict {fecha_str: ipc_ea} con la senda IPC proyectada
        spreads: Dict {id_posicion: spread} con el spread de cada título
        escenario: Nombre del escenario

    Returns:
        DataFrame con la proyección mensual de cada fondo FCP 4G
    """
    filas = []

    for _, posicion in portafolio.iterrows():
        valor_anterior = posicion["valor_mercado"]
        pyg_acumulado = 0.0
        id_pos = str(posicion["id_posicion"])
        spread = spreads.get(int(id_pos), spreads.get(id_pos, 0.0))

        for i, periodo in enumerate(periodos):
            fecha_str = periodo.strftime("%Y-%m-%d")

            if i == 0:
                # T0: registrar VM original sin retorno
                filas.append({
                    "escenario": escenario,
                    "id_posicion": id_pos,
                    "tipo_fondo": posicion["tipo_fondo"],
                    "fondo": posicion["fondo"],
                    "moneda": posicion["moneda"],
                    "fecha_proyeccion": fecha_str,
                    "valor_mercado": round(valor_anterior, 2),
                    "pyg_mensual": 0.0,
                    "pyg_acumulado": 0.0,
                    "metodologia": f"ipc_spread (T0)",
                })
                continue

            # Días del período
            dias = (periodo - periodos[i - 1]).days

            # IPC vigente = el del mes ANTERIOR (regla 6to día hábil: a fin de mes rige el IPC del mes previo)
            fecha_anterior_str = periodos[i - 1].strftime("%Y-%m-%d")
            ipc_ea = senda_ipc.get(fecha_anterior_str, IPC_PRUEBAS)

            # Tasa EA compuesta: (1 + IPC) * (1 + spread) - 1
            tasa_ea = (1 + ipc_ea) * (1 + spread) - 1
            tasa_diaria = convertir_ea_a_diaria(tasa_ea)

            factor = (1 + tasa_diaria) ** dias
            valor_actual = valor_anterior * factor
            pyg_mes = valor_actual - valor_anterior
            pyg_acumulado += pyg_mes

            filas.append({
                "escenario": escenario,
                "id_posicion": id_pos,
                "tipo_fondo": posicion["tipo_fondo"],
                "fondo": posicion["fondo"],
                "moneda": posicion["moneda"],
                "fecha_proyeccion": fecha_str,
                "valor_mercado": round(valor_actual, 2),
                "pyg_mensual": round(pyg_mes, 2),
                "pyg_acumulado": round(pyg_acumulado, 2),
                "metodologia": f"ipc_spread ({ipc_ea:.2%} + {spread:.2%})",
            })

            valor_anterior = valor_actual

    return pd.DataFrame(filas)


def proyectar_alternativos(
    fecha_inicio: str,
    fecha_fin: str,
    tasa_ea_local: float,
    tasa_ea_intl: float,
    ruta_db_portafolio: str = RUTA_DB_PORTAFOLIO,
    senda_ipc: dict[str, float] | None = None,
    spreads_4g: dict[int, float] | None = None,
    senda_trm: dict[str, float] | None = None,
    trm_inicio_ancla: float | None = None,
) -> pd.DataFrame:
    """
    Función principal: proyecta los fondos de capital privado (escenario base).

    Lee el portafolio desde portafolio.db, separa los FCP IPC+Spread y aplica:
      - Fondos IPC+Spread (es_ipc=SI): valoración con IPC mensual + spread
      - Resto locales: tasa EA fija
      - Internacionales: tasa EA fija en USD + conversión con senda TRM

    Args:
        fecha_inicio: Primer mes de proyección ("YYYY-MM")
        fecha_fin: Último mes de proyección ("YYYY-MM")
        tasa_ea_local: Tasa EA para fondos locales (escenario base)
        tasa_ea_intl: Tasa EA para fondos internacionales en USD (escenario base)
        ruta_db_portafolio: Ruta a portafolio.db
        senda_ipc: Dict {fecha_str: ipc_ea} con la inflación anual vigente por corte.
            Si None, usa IPC_PRUEBAS constante.
        spreads_4g: Dict {id_posicion: spread} para fondos IPC+Spread.
            Si None, usa SPREAD_IPC_DEFAULT.
        senda_trm: Dict {fecha_str: trm} con la TRM por corte mensual.
            Si se provee, fondos internacionales incluyen efecto cambiario.

    Returns:
        DataFrame con la proyección base de todos los fondos
    """
    print("=" * 60)
    print("AXIS - Proyección Activos Alternativos (Capital Privado)")
    print("=" * 60)
    print(f"\nPeríodo: {fecha_inicio} → {fecha_fin}")
    print(f"Escenario: base")
    print(f"Tasa EA Local: {tasa_ea_local:.2%}")
    print(f"Tasa EA Intl:  {tasa_ea_intl:.2%}")
    if senda_trm:
        trm_vals = list(senda_trm.values())
        print(f"Senda TRM:     {trm_vals[0]:,.0f} → {trm_vals[-1]:,.0f}")
    print()

    # Cargar portafolio desde portafolio.db
    portafolio = cargar_portafolio_alternativos(ruta_db_portafolio)

    # Separar fondos IPC+Spread (dinámico por columna es_ipc) del resto
    mask_ipc = portafolio["es_ipc"] == "SI"
    portafolio_ipc = portafolio[mask_ipc].copy()
    portafolio_resto = portafolio[~mask_ipc].copy()

    alt_local = portafolio_resto[portafolio_resto["tipo_fondo"] == TIPO_FONDO_LOCAL].copy()
    alt_intl = portafolio_resto[portafolio_resto["tipo_fondo"] == TIPO_FONDO_INTL].copy()

    print(f"  Fondos Locales (tasa EA fija): {len(alt_local)} posiciones")
    print(f"  Fondos Internacionales (tasa EA fija): {len(alt_intl)} posiciones")
    print(f"  Fondos IPC + Spread (es_ipc=SI): {len(portafolio_ipc)} posiciones\n")

    # Generar períodos
    periodos = generar_periodos(fecha_inicio, fecha_fin)
    print(f"  Meses a proyectar: {len(periodos)}\n")

    # Usar spreads por defecto si no se proveen
    if spreads_4g is None:
        # Construir spreads dinámicamente: usar FCP_4G_SPREADS si existe, si no, SPREAD_IPC_DEFAULT
        spreads_4g = {}
        for id_pos in portafolio_ipc["id_posicion"].values:
            spreads_4g[int(id_pos)] = FCP_4G_SPREADS.get(int(id_pos), SPREAD_IPC_DEFAULT)

    # Generar senda IPC constante si no se provee
    if senda_ipc is None:
        senda_ipc = {p.strftime("%Y-%m-%d"): IPC_PRUEBAS for p in periodos}
        print(f"  ⚠️  Usando IPC constante de prueba: {IPC_PRUEBAS:.2%}")
    else:
        print(f"  ✓ Senda IPC cargada: {len(senda_ipc)} meses")

    print()

    # Proyectar escenario base
    resultados = []

    # Fondos locales (sin FCP 4G)
    if not alt_local.empty:
        print(f"  Proyectando Alt Local | base (EA={tasa_ea_local:.2%})...")
        df = proyectar_bloque(alt_local, periodos, tasa_ea_local, "base")
        resultados.append(df)

    # FCP 4G / IPC+Spread
    if not portafolio_ipc.empty:
        print(f"  Proyectando IPC+Spread | {len(portafolio_ipc)} fondos...")
        for id_pos in portafolio_ipc["id_posicion"].values:
            spread_val = spreads_4g.get(int(id_pos), spreads_4g.get(str(id_pos), SPREAD_IPC_DEFAULT))
            nombre = portafolio_ipc[portafolio_ipc["id_posicion"] == id_pos]["fondo"].iloc[0]
            print(f"    → {nombre}: spread = {spread_val:.2%}")

        df_4g = proyectar_bloque_ipc_spread(
            portafolio=portafolio_ipc,
            periodos=periodos,
            senda_ipc=senda_ipc,
            spreads=spreads_4g,
            escenario="base",
        )
        resultados.append(df_4g)

    # Fondos internacionales
    if not alt_intl.empty:
        print(f"  Proyectando Alt Intl  | base (EA={tasa_ea_intl:.2%})...")
        df = proyectar_bloque(alt_intl, periodos, tasa_ea_intl, "base", senda_trm=senda_trm,
                              trm_inicio_ancla=trm_inicio_ancla)
        resultados.append(df)

    df_final = pd.concat(resultados, ignore_index=True)

    print(f"\n✓ Proyección completada: {len(df_final):,} registros")
    return df_final


def guardar_proyeccion(df: pd.DataFrame,
                       conn: sqlite3.Connection,
                       tabla: str = "proyeccion_alternativos") -> None:
    """
    Persiste el resultado de la proyección en SQLite.

    Args:
        df: DataFrame con la proyección
        conn: Conexión SQLite de destino
        tabla: Nombre de la tabla de salida
    """
    df.to_sql(tabla, conn, if_exists="replace", index=False)
    print(f"  ✓ Guardado en tabla '{tabla}': {len(df):,} registros")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main() -> None:
    """Ejecuta la proyección de alternativos (escenario base)."""

    # Parámetros
    fecha_inicio = "2026-06"
    fecha_fin = "2027-06"

    # Tasas EA escenario base
    tasa_ea_local = 0.10   # 10% EA para fondos locales
    tasa_ea_intl = 0.12    # 12% EA para fondos internacionales

    # Proyectar (lee portafolio desde portafolio.db)
    df_resultado = proyectar_alternativos(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        tasa_ea_local=tasa_ea_local,
        tasa_ea_intl=tasa_ea_intl,
    )

    # Mostrar resumen por tipo de fondo
    print("\n── Resumen por tipo de fondo ──")
    resumen = (
        df_resultado
        .groupby(["tipo_fondo", "metodologia"])
        .agg(
            posiciones=("fondo", "nunique"),
            valor_mercado_final=("valor_mercado", "last"),
            pyg_total=("pyg_acumulado", "max"),
        )
        .reset_index()
    )
    print(resumen.to_string(index=False))

    # Guardar en SQLite
    ruta_db_resultado = str(BASE / "axis_proyeccion_alternativos.db")
    with sqlite3.connect(ruta_db_resultado) as conn:
        guardar_proyeccion(df_resultado, conn)

    print(f"\n✓ DB resultado: {ruta_db_resultado}")


if __name__ == "__main__":
    main()
