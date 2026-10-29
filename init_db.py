import pandas as pd
import sqlite3

# --- CONFIGURACIÓN DE RUTAS (fuente única: comun/rutas.py) ---
from comun.rutas import INPUTS_DIR, DB_PATH

def inicializar_sistema():
    print("🏗️  Iniciando creación de la Base de Datos SQL...")

    # Borrar la DB existente para empezar desde cero
    if DB_PATH.exists():
        DB_PATH.unlink()
        print("🗑️  Base de datos anterior eliminada.")

    # 1. Conexión (Crea el archivo portafolio.db en la raíz del proyecto)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # ---------------------------------------------------------
        # 2. Carga de Tablas Maestras (Se reemplazan con if_exists='replace')
        # ---------------------------------------------------------
        print("\n--- Cargando Tablas Maestras ---")

        # Usamos encoding='utf-8' para prevenir errores por tildes o eñes
        df_especies = pd.read_csv(INPUTS_DIR / "mapeo_especies.csv", encoding='utf-8')
        df_especies.to_sql('param_especies', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_especies' cargada.")

        df_emisores = pd.read_csv(INPUTS_DIR / "mapeo_emisores.csv", encoding='utf-8')
        df_emisores.to_sql('param_emisores', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_emisores' cargada.")

        df_excepciones = pd.read_csv(INPUTS_DIR / "mapeo_excepciones.csv", encoding='utf-8')
        df_excepciones.to_sql('param_excepciones', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_excepciones' cargada.")

        df_diccionario = pd.read_csv(INPUTS_DIR / "diccionario_columnas.csv", encoding='utf-8')
        df_diccionario.to_sql('param_diccionario_columnas', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_diccionario_columnas' cargada.")

        df_monedas = pd.read_csv(INPUTS_DIR / "mapeo_monedas.csv", encoding='utf-8')
        df_monedas.to_sql('param_monedas', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_monedas' cargada.")

        df_portafolios = pd.read_csv(INPUTS_DIR / "mapeo_portafolios_porfin.csv", encoding='utf-8')
        df_portafolios.to_sql('param_portafolios', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_portafolios' cargada.")

        df_categorias_regimen = pd.read_csv(INPUTS_DIR / "categorias_regimen.csv", encoding='utf-8')
        df_categorias_regimen.to_sql('param_categorias_regimen', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_categorias_regimen' cargada.")

        df_categorias_solvencia = pd.read_csv(INPUTS_DIR / "categorias_solvencia.csv", encoding='utf-8')
        df_categorias_solvencia.to_sql('param_categorias_solvencia', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_categorias_solvencia' cargada.")

        df_grupos_limites = pd.read_csv(INPUTS_DIR / "grupos_limites_regimen.csv", encoding='utf-8')
        df_grupos_limites.to_sql('param_grupos_limites_regimen', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_grupos_limites_regimen' cargada.")

        df_matriz_solvencia = pd.read_csv(INPUTS_DIR / "matriz_reglas_solvencia.csv", encoding='utf-8')
        df_matriz_solvencia.to_sql('param_matriz_reglas_solvencia', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_matriz_reglas_solvencia' cargada.")

        df_matriz_asset_class = pd.read_csv(INPUTS_DIR / "matriz_asset_class.csv", encoding='utf-8')
        df_matriz_asset_class.to_sql('param_matriz_reglas_asset_class', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_matriz_reglas_asset_class' cargada.")

        df_calificaciones = pd.read_csv(INPUTS_DIR / "calificaciones_faltantes.csv", encoding='utf-8', dtype=str)
        df_calificaciones = df_calificaciones.dropna(subset=['emisor'])
        df_calificaciones.to_sql('param_calificaciones_faltantes', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_calificaciones_faltantes' cargada.")

        df_objetivo_derivados = pd.read_csv(INPUTS_DIR / "objetivo_derivados.csv", encoding='utf-8', dtype=str)
        df_objetivo_derivados.to_sql('param_objetivo_derivados', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_objetivo_derivados' cargada.")

        df_diccionario_swaps = pd.read_csv(INPUTS_DIR / "diccionario_swaps.csv", encoding='utf-8', dtype=str)
        df_diccionario_swaps.to_sql('param_diccionario_swaps', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_diccionario_swaps' cargada.")

        print("⏳ Consolidando condiciones faciales (Master Trader + Manual)...")

        base_master_trader = pd.read_excel(INPUTS_DIR / "2.mt.xlsx")
        base_master_trader = base_master_trader[base_master_trader["ISIN"].notna()]

        base_adicional_manual = pd.read_excel(INPUTS_DIR / "3.base_manual_condiciones.xlsx", header=1)

        base_total_faciales = pd.concat([base_master_trader, base_adicional_manual])

        columnas_utiles = ["ISIN", "TASA REFERENCIA", "BASE ANUAL3", "TIPO TASA", "USO TASA", "TASA FACIAL", "PERIODICIDAD"]
        base_total_faciales = base_total_faciales[columnas_utiles].drop_duplicates()

        base_total_faciales = base_total_faciales.rename(columns={
            "ISIN": "isin",
            "TASA REFERENCIA": "tasa_referencia",
            "BASE ANUAL3": "base_anual",
            "TIPO TASA": "tipo_tasa",
            "USO TASA": "uso_tasa",
            "TASA FACIAL": "tasa_facial",
            "PERIODICIDAD": "periodicidad"
        })
        base_total_faciales['isin'] = base_total_faciales['isin'].astype(str).str.strip()

        base_total_faciales.to_sql('param_condiciones_faciales', conn, if_exists='replace', index=False)
        print("✅ Tabla 'param_condiciones_faciales' cargada.")

        # ---------------------------------------------------------
        # 3. Creación de Estructuras Históricas (Uso de SQL Puro)
        # ---------------------------------------------------------
        print("\n--- Creando Estructuras de Tablas Históricas Estandarizadas ---")

        # 1. HISTÓRICO DE TÍTULOS (1.597)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hist_titulos (
                fecha_reporte DATE,
                id_posicion TEXT,
                clasificacion_contable TEXT,
                especie TEXT,
                emisor TEXT,
                isin TEXT,
                calificacion TEXT,
                fecha_emision DATE,
                fecha_vencimiento DATE,
                nominal REAL,
                facial REAL,
                periodicidad TEXT,
                fecha_constitucion DATE,
                moneda TEXT,
                valor_compra REAL,
                tasa_compra REAL,
                valor_tir REAL,
                tir REAL,
                valor_mercado REAL,
                tasa_mercado REAL,
                precio REAL,
                tasa_dcto REAL,
                estado TEXT,
                fecha_garantia DATE,
                portafolio TEXT,
                nemotecnico TEXT,
                PRIMARY KEY (fecha_reporte, id_posicion)
            );
        """)
        print("✅ Estructura 'hist_titulos' creada.")

        # 2. HISTÓRICO DE FORWARDS (PFCLD572)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hist_forwards (
                fecha_reporte DATE,
                id_posicion TEXT,
                especie TEXT,
                tipo TEXT,
                sentido TEXT,
                emisor TEXT,
                fecha_constitucion DATE,
                fecha_vencimiento DATE,
                fecha_cumplimiento DATE,
                moneda_base TEXT,
                nominal REAL,
                moneda_cotizacion TEXT,
                strike REAL,
                nominal_usd REAL,
                valor_derecho REAL,
                valor_obligacion REAL,
                tasa_conversion REAL,
                valor_mercado REAL,
                pyg_dia REAL,
                portafolio TEXT,
                estado TEXT,
                objetivo TEXT,
                subyacente TEXT,
                tipo_liquidacion TEXT,
                PRIMARY KEY (fecha_reporte, id_posicion)
            );
        """)
        print("✅ Estructura 'hist_forwards' creada.")

        # 3. HISTÓRICO DE OPCIONES
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hist_opciones (
                fecha_reporte DATE,
                id_posicion TEXT,
                especie TEXT,
                fecha_constitucion DATE,
                portafolio TEXT,
                sentido TEXT,
                tipo TEXT,
                emisor TEXT,
                nominal REAL,
                subyacente TEXT,
                spot_negociado REAL,
                devaluacion_negociada REAL,
                volatilidad_negociada REAL,
                strike REAL,
                moneda TEXT,
                fecha_vencimiento DATE,
                fecha_cumplimiento DATE,
                prima_cop REAL,
                tipo_liquidacion TEXT,
                tipo_contraparte TEXT,
                observaciones TEXT,
                tasa_local REAL,
                tasa_foranea REAL,
                trm REAL,
                nominal_usd REAL,
                volatilidad_valoracion REAL,
                valor_mercado REAL,
                pyg_dia REAL,
                delta REAL,
                gamma REAL,
                theta REAL,
                vega REAL,
                rho_local REAL,
                rho_foranea REAL,
                estado TEXT,
                PRIMARY KEY (fecha_reporte, id_posicion)
            );
        """)
        print("✅ Estructura 'hist_opciones' creada.")

        # 4. HISTÓRICO DE SWAPS (PFTIT695)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hist_swaps (
                fecha_reporte DATE,
                id_posicion TEXT,
                especie TEXT,
                fecha_constitucion DATE,
                fecha_vencimiento DATE,
                tipo_der TEXT,
                id_derecho TEXT,
                nominal_der REAL,
                facial_der REAL,
                periodicidad_der TEXT,
                moneda_der TEXT,
                vpn_derecho REAL,
                tipo_obl TEXT,
                id_obligacion TEXT,
                nominal_obl REAL,
                facial_obl REAL,
                periodicidad_obl TEXT,
                moneda_obl TEXT,
                vpn_obligacion REAL,
                valor_mercado REAL,
                portafolio TEXT,
                emisor TEXT,
                estado TEXT,
                PRIMARY KEY (fecha_reporte, id_posicion)
            );
        """)
        print("✅ Estructura 'hist_swaps' creada.")

        # 5. INPUT DE VARIACIÓN DE RESERVAS POR RAMO
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS input_variacion_reservas (
                ramo TEXT NOT NULL,          -- ramo normalizado (upper/strip)
                mes TEXT NOT NULL,           -- 'YYYY-MM' del mes de la variación
                delta_reserva REAL NOT NULL, -- Δreserva mes a mes (positivo=aumento, negativo=disminución)
                fecha_carga TEXT,            -- timestamp de la carga
                PRIMARY KEY (ramo, mes)
            );
        """)
        print("✅ Estructura 'input_variacion_reservas' creada.")

        conn.commit()

        print("\n🚀 ¡Base de Datos inicializada con éxito!")
        print(f"12 Tablas Maestras cargadas y 4 Tablas Históricas listas para recibir datos diarios.")
        print(f"📍 Ubicación del archivo de base de datos: {DB_PATH}")

    except FileNotFoundError as e:
        print(f"\n❌ Error: No se encontró uno de los archivos CSV maestros.")
        print(f"Verifica que la carpeta se llame exactamente '{INPUTS_DIR.name}'")
        print(f"Detalle técnico: {e}")
    except Exception as e:
        import traceback
        print(f"\n❌ Error inesperado durante la inicialización: {e}")
        traceback.print_exc()

    finally:
        conn.close()

if __name__ == "__main__":
    inicializar_sistema()
