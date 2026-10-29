"""
AXIS - Asset-Liability eXecution & Investment Simulator
Módulo: Proyección Renta Variable

Proyecta mensualmente el valor de mercado del portafolio de renta variable
(local, internacional, PEI) aplicando una tasa EA por categoría.

Fuente de datos: portafolio_clasificado (portafolio.db)
  - COLCAP → RV Local
  - MXWD   → RV Internacional
  - PEI    → Patrimonio Estrategia Inmobiliaria (misma lógica que local)

Supuestos:
  - Portafolio estático (sin compras/ventas durante la proyección)
  - Dividendos implícitos en la tasa EA
  - La valoración mensual se compone sobre el valor del mes anterior

Inputs:
  - Tabla portafolio_clasificado con posiciones actuales
  - Fecha inicio y fin de proyección
  - Tasa EA para RV Local (COLCAP)
  - Tasa EA para RV Internacional (MXWD)
  - Tasa EA para PEI

Output:
  DataFrame con columnas:
    escenario | tipo_rv | compania | titulo | moneda | fecha_proyeccion |
    valor_mercado | pyg_mensual | pyg_acumulado
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
# DB local del workspace
RUTA_DB_PORTAFOLIO = str(Path(__file__).resolve().parent / "portafolio.db")

# Clasificación de tipo RV según asset_class_definitivo
TIPO_RV_LOCAL = "rv_local"
TIPO_RV_INTL = "rv_internacional"
TIPO_RV_PEI = "rv_pei"

# Mapeo asset_class_definitivo → tipo_rv
ASSET_CLASS_MAP = {
    "COLCAP": TIPO_RV_LOCAL,
    "MXWD": TIPO_RV_INTL,
    "PEI": TIPO_RV_PEI,
}


# ═════════════════════════════════════════════
# FUNCIONES DE CÁLCULO
# ═════════════════════════════════════════════

def convertir_ea_a_diaria(tasa_ea: float) -> float:
    """
    Convierte una tasa efectiva anual a tasa efectiva diaria compuesta (base 365).

    Args:
        tasa_ea: Tasa efectiva anual (ej: 0.12 para 12%)

    Returns:
        Tasa efectiva diaria equivalente
    """
    return (1 + tasa_ea) ** (1 / 365) - 1


# Mantener para compatibilidad (usada por el dashboard en estrés)
def convertir_ea_a_mensual(tasa_ea: float) -> float:
    """Convierte EA a mensual (legacy, usar convertir_ea_a_diaria preferentemente)."""
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


def cargar_portafolio_rv(
    ruta_db: str = RUTA_DB_PORTAFOLIO,
) -> pd.DataFrame:
    """
    Carga posiciones de RV desde portafolio_clasificado.

    Filtra por asset_class_definitivo IN ('COLCAP', 'MXWD', 'PEI')
    y agrupa por especie + compañía para obtener una posición consolidada.

    Args:
        ruta_db: Ruta a portafolio.db

    Returns:
        DataFrame con columnas: id_posicion, compania, titulo, especie, tipo_rv, moneda, valor_mercado
    """
    query = """
        SELECT
            id_posicion,
            especie AS titulo,
            especie,
            compania,
            moneda,
            CAST(valor_mercado AS REAL) AS valor_mercado,
            asset_class_definitivo
        FROM portafolio_clasificado
        WHERE asset_class_definitivo IN ('COLCAP', 'MXWD', 'PEI')
          AND CAST(valor_mercado AS REAL) > 0
    """
    try:
        conn = sqlite3.connect(ruta_db)
        df = pd.read_sql(query, conn)
        conn.close()

        if not df.empty:
            # Mapear asset_class a tipo_rv
            df["tipo_rv"] = df["asset_class_definitivo"].map(ASSET_CLASS_MAP)
            df = df.drop(columns=["asset_class_definitivo"])
            print(f"  [OK] Portafolio RV cargado desde portafolio.db: {len(df)} posiciones")
            print(f"    COLCAP: {(df['tipo_rv']==TIPO_RV_LOCAL).sum()} | "
                  f"MXWD: {(df['tipo_rv']==TIPO_RV_INTL).sum()} | "
                  f"PEI: {(df['tipo_rv']==TIPO_RV_PEI).sum()}")
            return df
    except Exception as e:
        print(f"  [WARN] Error leyendo portafolio.db: {e}")

    print("  [WARN] portafolio.db no disponible -- retornando vacio")
    return pd.DataFrame(columns=["id_posicion", "compania", "titulo", "especie", "tipo_rv", "moneda", "valor_mercado"])


def proyectar_bloque(portafolio: pd.DataFrame,
                     periodos: list[date],
                     tasa_ea: float,
                     escenario: str,
                     senda_trm: dict[str, float] | None = None,
                     trm_inicio_ancla: float | None = None) -> pd.DataFrame:
    """
    Proyecta el valor de mercado mes a mes para un bloque de posiciones
    usando composición diaria (base 365).

    Para títulos internacionales (moneda USD):
      1. VM_COP(T0) / TRM(T0) = VM_USD(T0)
      2. VM_USD(t) = VM_USD(t-1) × (1+tasa_diaria)^dias
      3. VM_COP(t) = VM_USD(t) × TRM(t)

    Args:
        portafolio: DataFrame con posiciones (id_posicion, compania, titulo, especie, tipo_rv, moneda, valor_mercado)
        periodos: Lista de fechas mensuales a proyectar
        tasa_ea: Tasa efectiva anual del escenario (retorno en moneda origen)
        escenario: Nombre del escenario
        senda_trm: Dict {fecha_str: trm} con la TRM por corte mensual.

    Returns:
        DataFrame con la proyección mensual de cada posición
    """
    tasa_diaria = convertir_ea_a_diaria(tasa_ea)
    filas = []

    def _id_posicion(posicion):
        """Identificador de la posición, retrocompatible. Usa `id_posicion` cuando está
        presente (ruta de producción vía `cargar_portafolio_rv`); si falta (llamadores que
        arman el portafolio sin esa columna, p. ej. dobles de prueba o SELECTs legados), cae
        al `titulo` como etiqueta. No se fuerza int para no romper ids no numéricos."""
        val = posicion.get("id_posicion", None)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return posicion["titulo"]
        try:
            return int(val)
        except (TypeError, ValueError):
            return val

    # TRM T0 para convertir VM inicial de COP a USD
    trm_t0 = None
    if senda_trm:
        if trm_inicio_ancla is not None and trm_inicio_ancla > 0:
            # Ancla REAL de arranque del tramo (fuente de verdad); NO altera los nodos
            # mensuales de la senda, que siguen moviendo el VM intermedio.
            trm_t0 = float(trm_inicio_ancla)
        else:
            primera_fecha = periodos[0].strftime("%Y-%m-%d") if periodos else None
            trm_t0 = senda_trm.get(primera_fecha) if primera_fecha else None
            if trm_t0 is None:
                trm_t0 = list(senda_trm.values())[0] if senda_trm else None

    for _, posicion in portafolio.iterrows():
        valor_anterior = float(posicion["valor_mercado"])  # en COP
        moneda = str(posicion.get("moneda", "COP")).strip().upper()
        es_intl = moneda != "COP" and senda_trm is not None and trm_t0 is not None and trm_t0 > 0

        # Para internacionales: convertir VM inicial a USD
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
                    "id_posicion": _id_posicion(posicion),
                    "tipo_rv": posicion["tipo_rv"],
                    "compania": posicion["compania"],
                    "titulo": posicion["titulo"],
                    "especie": posicion.get("especie", posicion["titulo"]),
                    "moneda": posicion["moneda"],
                    "fecha_proyeccion": periodo.strftime("%Y-%m-%d"),
                    "valor_mercado": round(valor_anterior, 2),
                    "pyg_mensual": 0.0,
                    "pyg_acumulado": 0.0,
                })
                continue

            dias = (periodo - periodos[i - 1]).days

            # Componer por días reales del mes
            factor = (1 + tasa_diaria) ** dias

            if es_intl:
                # Internacional: retorno en USD, luego convertir a COP con TRM del corte
                vm_usd_actual = vm_usd_anterior * factor
                trm_corte = senda_trm.get(periodo.strftime("%Y-%m-%d"), trm_t0)
                valor_actual = vm_usd_actual * trm_corte
                vm_usd_anterior = vm_usd_actual
            else:
                # Local: retorno directo en COP
                valor_actual = valor_anterior * factor

            pyg_mes = valor_actual - valor_anterior
            pyg_acumulado += pyg_mes

            filas.append({
                "escenario": escenario,
                "id_posicion": _id_posicion(posicion),
                "tipo_rv": posicion["tipo_rv"],
                "compania": posicion["compania"],
                "titulo": posicion["titulo"],
                "especie": posicion.get("especie", posicion["titulo"]),
                "moneda": posicion["moneda"],
                "fecha_proyeccion": periodo.strftime("%Y-%m-%d"),
                "valor_mercado": round(valor_actual, 2),
                "pyg_mensual": round(pyg_mes, 2),
                "pyg_acumulado": round(pyg_acumulado, 2),
            })

            valor_anterior = valor_actual

    return pd.DataFrame(filas)


def proyectar_renta_variable(
    fecha_inicio: str,
    fecha_fin: str,
    escenarios_rv_local: dict[str, float],
    escenarios_rv_intl: dict[str, float],
    tasa_pei: float | None = None,
    ruta_db: str = RUTA_DB_PORTAFOLIO,
    conn: sqlite3.Connection | None = None,
    senda_trm: dict[str, float] | None = None,
    trm_inicio_ancla: float | None = None,
) -> pd.DataFrame:
    """
    Función principal: proyecta el portafolio de RV local, internacional y PEI.

    Lee de portafolio_clasificado y aplica:
      - COLCAP: tasa de escenarios_rv_local (COP directo)
      - MXWD: tasa de escenarios_rv_intl (USD) + conversión con senda TRM
      - PEI: tasa_pei (si None, usa la misma que rv_local)

    Args:
        fecha_inicio: Primer mes de proyección ("YYYY-MM")
        fecha_fin: Último mes de proyección ("YYYY-MM")
        escenarios_rv_local: Tasas EA por escenario para RV local (COLCAP)
        escenarios_rv_intl: Tasas EA por escenario para RV internacional en USD (MXWD)
        tasa_pei: Tasa EA para PEI (si None, usa la del escenario base de rv_local)
        ruta_db: Ruta a portafolio.db
        conn: Conexión SQLite opcional (deprecated, se ignora)
        senda_trm: Dict {fecha_str: trm} con la TRM por corte mensual.
            Si se provee, MXWD se proyecta en USD y convierte a COP con TRM del corte.

    Returns:
        DataFrame consolidado con la proyección de todos los escenarios
    """
    print("=" * 60)
    print("AXIS - Proyeccion Renta Variable")
    print("=" * 60)
    print(f"\nPeriodo: {fecha_inicio} -> {fecha_fin}")
    print(f"Escenarios RV Local: {list(escenarios_rv_local.keys())}")
    print(f"Escenarios RV Intl:  {list(escenarios_rv_intl.keys())}")
    if tasa_pei is not None:
        print(f"Tasa PEI: {tasa_pei:.2%}")
    print()

    # Cargar portafolio desde portafolio.db
    portafolio = cargar_portafolio_rv(ruta_db)

    if portafolio.empty:
        print("  [WARN] No hay posiciones de RV en portafolio_clasificado")
        return pd.DataFrame()

    rv_local = portafolio[portafolio["tipo_rv"] == TIPO_RV_LOCAL].copy()
    rv_intl = portafolio[portafolio["tipo_rv"] == TIPO_RV_INTL].copy()
    rv_pei = portafolio[portafolio["tipo_rv"] == TIPO_RV_PEI].copy()

    print(f"  RV Local (COLCAP): {len(rv_local)} posiciones, VM=${rv_local['valor_mercado'].sum()/1e9:,.1f} MM")
    print(f"  RV Internacional (MXWD): {len(rv_intl)} posiciones, VM=${rv_intl['valor_mercado'].sum()/1e9:,.1f} MM")
    print(f"  PEI: {len(rv_pei)} posiciones, VM=${rv_pei['valor_mercado'].sum()/1e9:,.1f} MM\n")

    # Generar períodos
    periodos = generar_periodos(fecha_inicio, fecha_fin)
    print(f"  Meses a proyectar: {len(periodos)}\n")

    # Proyectar cada escenario
    resultados = []

    for escenario, tasa_ea in escenarios_rv_local.items():
        if not rv_local.empty:
            print(f"  Proyectando RV Local (COLCAP) | {escenario} (EA={tasa_ea:.2%})...")
            df = proyectar_bloque(rv_local, periodos, tasa_ea, escenario)
            resultados.append(df)

    for escenario, tasa_ea in escenarios_rv_intl.items():
        if not rv_intl.empty:
            print(f"  Proyectando RV Intl (MXWD)    | {escenario} (EA={tasa_ea:.2%})...")
            df = proyectar_bloque(rv_intl, periodos, tasa_ea, escenario, senda_trm=senda_trm,
                                  trm_inicio_ancla=trm_inicio_ancla)
            resultados.append(df)

    # PEI: usa tasa_pei si se provee, sino la tasa base de rv_local
    if not rv_pei.empty:
        tasa_pei_eff = tasa_pei if tasa_pei is not None else escenarios_rv_local.get("base", 0.12)
        print(f"  Proyectando PEI               | base (EA={tasa_pei_eff:.2%})...")
        # PEI se marca como rv_local para efectos de agregación (es COP, misma lógica)
        rv_pei_proj = rv_pei.copy()
        rv_pei_proj["tipo_rv"] = TIPO_RV_LOCAL  # Se comporta como local
        df_pei = proyectar_bloque(rv_pei_proj, periodos, tasa_pei_eff, "base")
        resultados.append(df_pei)

    if not resultados:
        return pd.DataFrame()

    df_final = pd.concat(resultados, ignore_index=True)

    print(f"\n[OK] Proyeccion completada: {len(df_final):,} registros")
    return df_final


def guardar_proyeccion(df: pd.DataFrame,
                       conn: sqlite3.Connection,
                       tabla: str = "proyeccion_rv") -> None:
    """
    Persiste el resultado de la proyección en SQLite.

    Args:
        df: DataFrame con la proyección
        conn: Conexión SQLite de destino
        tabla: Nombre de la tabla de salida
    """
    df.to_sql(tabla, conn, if_exists="replace", index=False)
    print(f"  [OK] Guardado en tabla '{tabla}': {len(df):,} registros")


# ═════════════════════════════════════════════
# MAIN (ejemplo de uso)
# ═════════════════════════════════════════════

def main() -> None:
    """Ejecuta una proyección de ejemplo para validar el módulo."""

    fecha_inicio = "2026-06"
    fecha_fin = "2027-06"

    escenarios_rv_local = {"base": 0.12}
    escenarios_rv_intl = {"base": 0.08}
    tasa_pei = 0.10  # 10% EA para PEI

    df_resultado = proyectar_renta_variable(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        escenarios_rv_local=escenarios_rv_local,
        escenarios_rv_intl=escenarios_rv_intl,
        tasa_pei=tasa_pei,
    )

    if df_resultado.empty:
        print("\nNo se generaron resultados.")
        return

    # Mostrar resumen
    print("\n-- Resumen por tipo --")
    resumen = (
        df_resultado
        .groupby(["escenario", "tipo_rv"])
        .agg(
            posiciones=("titulo", "nunique"),
            vm_final=("valor_mercado", "last"),
            pyg_total=("pyg_acumulado", "max"),
        )
        .reset_index()
    )
    print(resumen.to_string(index=False))


if __name__ == "__main__":
    main()
