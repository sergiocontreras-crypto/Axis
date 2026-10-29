"""
AXIS - Asset-Liability eXecution & Investment Simulator
Módulo: Proyección Simultáneas Pasivas

Proyecta mensualmente el costo de financiación de las operaciones de
simultáneas pasivas, utilizando una senda de tasa repo como input.

Supuestos:
  - La simultánea se renueva mensualmente (plazo 1 mes)
  - El valor base de cada título es su valor de mercado actual
  - El costo mensual = valor_mercado * tasa_repo_mes / 12
  - La senda de tasa repo define la tasa vigente para cada mes proyectado
  - Escenario único: base

Inputs:
  - Portafolio desde portafolio.db (tabla portafolio_clasificado,
    filtrado por ramo = 'Simultaneas Pasivas')
  - Senda de tasa repo mensual (DataFrame con fecha y tasa_repo)
  - Fecha inicio y fin de proyección

Output:
  DataFrame con columnas:
    titulo | id_posicion | portafolio | valor_mercado |
    fecha_proyeccion | tasa_repo | costo_mensual | costo_acumulado
"""

import sqlite3
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

RUTA_DB_PORTAFOLIO = str(Path(__file__).parent / "portafolio.db")
RUTA_DB_RESULTADO = str(Path(__file__).parent / "portafolio.db")

RAMO_SIMULTANEAS = "Simultaneas Pasivas"
MESES_ANIO = 12


# ═════════════════════════════════════════════
# SENDA REPO PLACEHOLDER (se reemplazará con input definitivo)
# ═════════════════════════════════════════════

SENDA_REPO_EJEMPLO = pd.DataFrame([
    {"fecha": "2026-06-01", "tasa_repo": 0.0975},
    {"fecha": "2026-07-01", "tasa_repo": 0.0950},
    {"fecha": "2026-08-01", "tasa_repo": 0.0925},
    {"fecha": "2026-09-01", "tasa_repo": 0.0900},
    {"fecha": "2026-10-01", "tasa_repo": 0.0875},
    {"fecha": "2026-11-01", "tasa_repo": 0.0850},
    {"fecha": "2026-12-01", "tasa_repo": 0.0850},
    {"fecha": "2027-01-01", "tasa_repo": 0.0825},
    {"fecha": "2027-02-01", "tasa_repo": 0.0825},
    {"fecha": "2027-03-01", "tasa_repo": 0.0800},
    {"fecha": "2027-04-01", "tasa_repo": 0.0800},
    {"fecha": "2027-05-01", "tasa_repo": 0.0775},
    {"fecha": "2027-06-01", "tasa_repo": 0.0775},
])


# ═════════════════════════════════════════════
# FUNCIONES DE CARGA
# ═════════════════════════════════════════════

def cargar_portafolio_simultaneas(
    ruta_db: str = RUTA_DB_PORTAFOLIO,
) -> pd.DataFrame:
    """
    Carga títulos de simultáneas pasivas desde portafolio.db.

    Identifica simultáneas por:
    1. ramo = 'Simultaneas Pasivas' en portafolio_clasificado, O
    2. tipo = 'simultanea_pasiva' en param_portafolios (join por portafolio)

    Returns:
        DataFrame con columnas: id_posicion, especie, emisor, portafolio,
        valor_mercado, tasa_compra, fecha_vencimiento
    """
    conn = sqlite3.connect(ruta_db)
    try:
        # Intentar por ramo directo
        df = pd.read_sql("""
            SELECT id_posicion, especie, emisor, portafolio, valor_mercado,
                   tasa_compra, fecha_vencimiento
            FROM portafolio_clasificado
            WHERE ramo = ?
        """, conn, params=(RAMO_SIMULTANEAS,))

        # Si no hay resultados, buscar por tipo de portafolio
        if df.empty:
            df = pd.read_sql("""
                SELECT pc.id_posicion, pc.especie, pc.emisor, pc.portafolio,
                       pc.valor_mercado, pc.tasa_compra, pc.fecha_vencimiento
                FROM portafolio_clasificado pc
                INNER JOIN param_portafolios pp
                    ON TRIM(pc.portafolio) = TRIM(pp.portafolio_porfin)
                WHERE pp.tipo = 'simultanea_pasiva'
            """, conn)
    finally:
        conn.close()

    # Asegurar tipos numericos
    if 'valor_mercado' in df.columns:
        df['valor_mercado'] = pd.to_numeric(df['valor_mercado'], errors='coerce').fillna(0)

    print(f"  Portafolio simultaneas: {len(df)} titulos")
    if not df.empty:
        print(f"    Valor mercado total: ${df['valor_mercado'].sum():,.0f} COP")
    return df


def cargar_senda_repo(
    conn: sqlite3.Connection | None = None,
    tabla: str = "senda_tasa_repo",
) -> pd.DataFrame:
    """
    Carga la senda de tasa repo mensual proyectada.

    Intenta leer de SQLite; si no existe, usa el ejemplo placeholder.

    Args:
        conn: Conexión SQLite opcional (a la DB de inputs)
        tabla: Nombre de la tabla con la senda

    Returns:
        DataFrame con columnas: fecha, tasa_repo
    """
    if conn is not None:
        try:
            df = pd.read_sql(f"SELECT fecha, tasa_repo FROM [{tabla}]", conn)
            if not df.empty:
                print(f"  ✓ Senda repo cargada desde {tabla}: {len(df)} meses")
                return df
        except Exception:
            pass

    print("  ⚠️  Usando senda repo de ejemplo (tabla definitiva no disponible)")
    return SENDA_REPO_EJEMPLO.copy()


# ═════════════════════════════════════════════
# FUNCIONES DE PROYECCIÓN
# ═════════════════════════════════════════════

def generar_periodos(fecha_inicio: str, fecha_fin: str) -> list[str]:
    """
    Genera lista de fechas mensuales (primer día del mes).

    Args:
        fecha_inicio: "YYYY-MM" primer mes de proyección
        fecha_fin: "YYYY-MM" último mes de proyección

    Returns:
        Lista de strings "YYYY-MM-01"
    """
    inicio = pd.Timestamp(f"{fecha_inicio}-01")
    fin = pd.Timestamp(f"{fecha_fin}-01")
    rango = pd.date_range(start=inicio, end=fin, freq="MS")
    return [d.strftime("%Y-%m-%d") for d in rango]


def proyectar_titulo(
    id_posicion: str,
    especie: str,
    portafolio: str,
    valor_mercado: float,
    periodos: list[str],
    senda_repo: dict[str, float],
    fecha_inicio_real: str = None,
) -> pd.DataFrame:
    """
    Proyecta el costo mensual de una simultanea pasiva.

    Costo = valor_mercado * tasa_repo * dias_periodo / 360

    El primer mes se prorratea desde la fecha real de inicio hasta fin de mes.
    Los demas meses usan dias reales del mes.

    Args:
        id_posicion: ID del titulo
        especie: Nombre de la especie
        portafolio: Codigo del portafolio
        valor_mercado: Valor de mercado base (COP)
        periodos: Lista de fechas de proyeccion (primer dia de cada mes)
        senda_repo: Dict {fecha_str: tasa_repo}
        fecha_inicio_real: Fecha exacta de inicio (para prorrateo del primer mes)
    """
    import calendar

    filas = []
    costo_acumulado = 0.0

    fecha_ini_real = pd.Timestamp(fecha_inicio_real) if fecha_inicio_real else None

    for i, periodo in enumerate(periodos):
        tasa_repo = senda_repo.get(periodo, 0.0)
        fecha_periodo = pd.Timestamp(periodo)
        anio = fecha_periodo.year
        mes = fecha_periodo.month
        dias_mes = calendar.monthrange(anio, mes)[1]

        # Primer mes: prorratear desde fecha real hasta fin de mes
        if i == 0 and fecha_ini_real is not None and fecha_ini_real.month == mes and fecha_ini_real.year == anio:
            dias_periodo = dias_mes - fecha_ini_real.day + 1
        else:
            dias_periodo = dias_mes

        # Costo = VM * tasa * dias / 360
        costo_mensual = valor_mercado * tasa_repo * dias_periodo / 360.0
        costo_acumulado += costo_mensual

        filas.append({
            "id_posicion": id_posicion,
            "especie": especie,
            "portafolio": portafolio,
            "valor_mercado": round(valor_mercado, 2),
            "fecha_proyeccion": periodo,
            "tasa_repo": round(tasa_repo, 6),
            "dias": dias_periodo,
            "costo_mensual": round(costo_mensual, 2),
            "costo_acumulado": round(costo_acumulado, 2),
        })

    return pd.DataFrame(filas)


def proyectar_simultaneas_pasivas(
    fecha_inicio: str,
    fecha_fin: str,
    ruta_db_portafolio: str = RUTA_DB_PORTAFOLIO,
    conn_inputs: sqlite3.Connection | None = None,
    fecha_inicio_exacta: str = None,
) -> pd.DataFrame:
    """
    Función principal: proyecta el costo de simultáneas pasivas.

    Args:
        fecha_inicio: Primer mes de proyección ("YYYY-MM")
        fecha_fin: Último mes de proyección ("YYYY-MM")
        ruta_db_portafolio: Ruta a portafolio.db
        conn_inputs: Conexión SQLite con la senda de tasa repo (opcional)

    Returns:
        DataFrame consolidado con la proyección de todos los títulos
    """
    print("=" * 60)
    print("AXIS - Proyección Simultáneas Pasivas (Costo Repo)")
    print("=" * 60)
    print(f"\nPeríodo: {fecha_inicio} → {fecha_fin}")
    print(f"Escenario: base\n")

    # Cargar datos
    portafolio = cargar_portafolio_simultaneas(ruta_db_portafolio)
    df_repo = cargar_senda_repo(conn_inputs)

    # Convertir senda repo a dict para lookup rápido
    senda_repo = dict(zip(df_repo["fecha"], df_repo["tasa_repo"]))

    # Generar períodos
    periodos = generar_periodos(fecha_inicio, fecha_fin)
    print(f"  Meses a proyectar: {len(periodos)}\n")

    # Proyectar cada título
    resultados = []

    for _, row in portafolio.iterrows():
        df = proyectar_titulo(
            id_posicion=str(row["id_posicion"]),
            especie=row["especie"],
            portafolio=row["portafolio"],
            valor_mercado=row["valor_mercado"],
            periodos=periodos,
            senda_repo=senda_repo,
            fecha_inicio_real=fecha_inicio_exacta or f"{fecha_inicio}-01",
        )
        resultados.append(df)

    if not resultados:
        print("  Sin titulos para proyectar")
        return pd.DataFrame()

    df_final = pd.concat(resultados, ignore_index=True)

    print(f"✓ Proyección completada: {len(df_final):,} registros")
    print(f"  Costo total proyectado: ${df_final['costo_mensual'].sum():,.0f} COP")
    return df_final


def guardar_proyeccion(
    df: pd.DataFrame,
    ruta_db: str = RUTA_DB_RESULTADO,
    tabla: str = "proyeccion_simultaneas",
) -> None:
    """
    Persiste el resultado de la proyección en SQLite.

    Args:
        df: DataFrame con la proyección
        ruta_db: Ruta a la DB de resultado
        tabla: Nombre de la tabla de salida
    """
    conn = sqlite3.connect(ruta_db)
    try:
        df.to_sql(tabla, conn, if_exists="replace", index=False)
    finally:
        conn.close()
    print(f"  ✓ Guardado en '{tabla}': {len(df):,} registros → {ruta_db}")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main() -> None:
    """Ejecuta la proyección de simultáneas pasivas."""

    fecha_inicio = "2026-06"
    fecha_fin = "2027-06"

    # Proyectar
    df_resultado = proyectar_simultaneas_pasivas(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )

    # Mostrar resumen por título
    print("\n── Resumen por título ──")
    resumen = (
        df_resultado
        .groupby(["id_posicion", "especie"])
        .agg(
            valor_mercado=("valor_mercado", "first"),
            costo_total=("costo_acumulado", "last"),
        )
        .reset_index()
    )
    print(resumen.to_string(index=False))

    # Guardar en SQLite
    guardar_proyeccion(df_resultado)

    print(f"\n✓ Fin del módulo")


if __name__ == "__main__":
    main()
