"""
verificar_spread_mercado.py — Valida que todos los títulos RF tienen spread_mercado > 0.
Ejecutar: python scripts/verificar_spread_mercado.py
"""
import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "portafolio.db"


def verificar():
    conn = sqlite3.connect(DB_PATH)

    # Verificar que la tabla existe
    tablas = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table'", conn)
    print(f"Tablas en DB: {tablas['name'].tolist()}")

    # Cargar portafolio con spread_mercado
    try:
        df = pd.read_sql_query("""
            SELECT p.especie, p.isin, p.compania, p.tipo_tasa, p.asset_class_definitivo,
                   p.valor_mercado, h.rendimiento as spread_mercado
            FROM portafolio_clasificado p
            LEFT JOIN hist_pip h ON p.isin = h.codigo_isin
            WHERE p.asset_class_definitivo NOT IN ('COLCAP','MXWD','PEI',
                'Fondo Capital Priv. Local','Fondo Capital Priv. Extranjero')
            AND CAST(p.valor_mercado AS REAL) > 0
        """, conn)
    except Exception as e:
        print(f"❌ Error en query: {e}")
        conn.close()
        return

    conn.close()

    total = len(df)
    print(f"\n{'='*60}")
    print(f"VERIFICACIÓN SPREAD_MERCADO — Títulos RF")
    print(f"{'='*60}")
    print(f"Total títulos RF con VM > 0: {total}")

    # Con spread_mercado
    con_spread = df[df["spread_mercado"].notna() & (df["spread_mercado"] > 0)]
    sin_spread = df[df["spread_mercado"].isna() | (df["spread_mercado"] <= 0)]

    print(f"✅ Con spread_mercado > 0: {len(con_spread)} ({len(con_spread)/total*100:.1f}%)")
    print(f"⚠️  Sin spread o <= 0: {len(sin_spread)} ({len(sin_spread)/total*100:.1f}%)")

    if not sin_spread.empty:
        print(f"\n--- Títulos sin spread_mercado (caen a fallback curva genérica): ---")
        cols = ['especie', 'isin', 'compania', 'tipo_tasa', 'asset_class_definitivo']
        cols_available = [c for c in cols if c in sin_spread.columns]
        print(sin_spread[cols_available].to_string(index=False, max_rows=30))

    # Verificar Treasury/Estructuras y Time Deposits
    print(f"\n{'='*60}")
    print("VERIFICACIÓN: Estructuras (antes LEGATRUU) y Time Deposits (VISTA USD)")
    print(f"{'='*60}")

    treasury = df[df["asset_class_definitivo"].isin(["LEGATRUU", "Estructuras"])]
    vista_usd = df[df["asset_class_definitivo"] == "VISTA USD"]
    print(f"Estructuras/LEGATRUU: {len(treasury)} títulos")
    print(f"Time Deposits (VISTA USD): {len(vista_usd)} títulos")

    if not treasury.empty:
        print(f"  VM total Treasury: ${treasury['valor_mercado'].astype(float).sum():,.0f}")
    if not vista_usd.empty:
        print(f"  VM total VISTA USD: ${vista_usd['valor_mercado'].astype(float).sum():,.0f}")

    print(f"\n{'='*60}")
    print("FIN VERIFICACIÓN")


if __name__ == "__main__":
    verificar()
