"""
portfolio_loader_beta.py
------------------------
El "Operario Diario".
Lee los archivos crudos transaccionales del día, utiliza el libro de funciones
para limpiarlos, y guarda los datos al final de las tablas históricas en SQL.
"""

import pandas as pd
import sqlite3

# Importamos nuestras herramientas de la fábrica
from libro_de_funciones import limpiar_titulos, limpiar_forwards, limpiar_opciones, limpiar_swaps

# --- CONFIGURACIÓN DE RUTAS (fuente única: comun/rutas.py) ---
from comun.rutas import INPUTS_DIR, DB_PATH


def cargar_titulos_desde_df(df_titulos_crudo, fecha_reporte):
    """
    Carga títulos a hist_titulos desde un DataFrame ya parseado (para uso desde el dash).
    Aplica los mismos filtros y limpieza que procesar_carga_diaria.
    """
    fecha_reporte = pd.to_datetime(str(fecha_reporte).strip()).date()
    print(f"\n🚀 Cargando títulos desde DataFrame para la fecha: {fecha_reporte}")
    print("-" * 60)

    conn = sqlite3.connect(DB_PATH)

    try:
        # Diccionarios maestros
        df_mapeo_columnas = pd.read_sql_query("SELECT * FROM param_diccionario_columnas", conn)
        df_mapeo_monedas = pd.read_sql_query("SELECT * FROM param_monedas", conn)

        # Eliminar datos previos de esta fecha
        conn.execute("DELETE FROM hist_titulos WHERE fecha_reporte = ?", (fecha_reporte,))
        conn.commit()

        # Eliminar flujos de swaps (columna Flujo = SWF)
        if 'Flujo' in df_titulos_crudo.columns:
            df_titulos_crudo = df_titulos_crudo[~df_titulos_crudo['Flujo'].astype(str).str.contains('SWF', na=False)]

        # Limpiar con libro_de_funciones
        df_titulos_limpio = limpiar_titulos(df_titulos_crudo, df_mapeo_columnas, df_mapeo_monedas)

        # Inyectar fecha
        df_titulos_limpio['fecha_reporte'] = fecha_reporte

        # Filtrar solo portafolios que empiecen por 01-, 02- o 03-
        df_titulos_limpio = df_titulos_limpio[df_titulos_limpio['portafolio'].astype(str).str[:3].isin(['01-', '02-', '03-'])]

        # Excluir portafolios marcados en param_portafolios
        df_portafolios_param = pd.read_sql_query("SELECT portafolio_porfin, excluir FROM param_portafolios", conn)
        portafolios_excluidos = set(
            df_portafolios_param[df_portafolios_param['excluir'].astype(str).str.upper() == 'SI']['portafolio_porfin'].astype(str).str.strip()
        )
        if portafolios_excluidos:
            df_titulos_limpio = df_titulos_limpio[~df_titulos_limpio['portafolio'].astype(str).str.strip().isin(portafolios_excluidos)]

        # Solo insertar columnas que existen en la tabla destino
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(hist_titulos)").fetchall()]
        cols_to_insert = [c for c in df_titulos_limpio.columns if c in existing_cols]
        df_to_save = df_titulos_limpio[cols_to_insert]

        # Guardar en la tabla histórica (APPEND)
        df_to_save.to_sql('hist_titulos', conn, if_exists='append', index=False)
        print(f"✅ {len(df_to_save)} títulos guardados exitosamente.")
        return len(df_to_save)

    except Exception as e:
        import traceback
        print(f"❌ Error durante la carga: {e}")
        traceback.print_exc()
        return 0

    finally:
        conn.close()


def procesar_carga_diaria(fecha_reporte):
    # Limpiar caracteres invisibles y convertir a fecha
    fecha_reporte = str(fecha_reporte).strip().replace('\ufeff', '')
    fecha_reporte = pd.to_datetime(fecha_reporte).date()

    print(f"\n🚀 Iniciando carga diaria de portafolio para la fecha: {fecha_reporte}")
    print("-" * 60)
    
    # 1. Conectarnos a la base de datos
    conn = sqlite3.connect(DB_PATH)
    
    try:
        # 2. Extraer los diccionarios maestros de SQL (Data-Driven ETL)
        print("📥 Descargando parámetros maestros (Diccionarios y Monedas)...")
        df_mapeo_columnas = pd.read_sql_query("SELECT * FROM param_diccionario_columnas", conn)
        df_mapeo_monedas = pd.read_sql_query("SELECT * FROM param_monedas", conn)
        
        # ==========================================
        # 3. PROCESAMIENTO DE TÍTULOS (1.597)
        # ==========================================
        print("\n🔄 Procesando Títulos (1.597.xlsx)...")
        archivo_titulos = INPUTS_DIR / "1.597.xlsx"

        # Eliminar datos previos de esta fecha para permitir re-carga
        conn.execute("DELETE FROM hist_titulos WHERE fecha_reporte = ?", (fecha_reporte,))
        conn.commit()
        
        # Leemos el Excel desde la fila 6 y quitamos la fila de guiones
        df_titulos_crudo = pd.read_excel(archivo_titulos, header=6).iloc[1:].reset_index(drop=True)
        
        # Eliminar flujos de swaps (columna Flujo = SWF) antes de procesar
        if 'Flujo' in df_titulos_crudo.columns:
            df_titulos_crudo = df_titulos_crudo[~df_titulos_crudo['Flujo'].astype(str).str.contains('SWF', na=False)]

        # Limpiamos usando nuestra fábrica
        df_titulos_limpio = limpiar_titulos(df_titulos_crudo, df_mapeo_columnas, df_mapeo_monedas)
        
        # Inyectamos la fecha del reporte
        df_titulos_limpio['fecha_reporte'] = fecha_reporte
        
        # Filtrar solo portafolios que empiecen por 01-, 02- o 03-
        df_titulos_limpio = df_titulos_limpio[df_titulos_limpio['portafolio'].astype(str).str[:3].isin(['01-', '02-', '03-'])]

        # Excluir portafolios marcados en param_portafolios
        df_portafolios_param = pd.read_sql_query("SELECT portafolio_porfin, excluir FROM param_portafolios", conn)
        portafolios_excluidos = set(
            df_portafolios_param[df_portafolios_param['excluir'].astype(str).str.upper() == 'SI']['portafolio_porfin'].astype(str).str.strip()
        )
        if portafolios_excluidos:
            df_titulos_limpio = df_titulos_limpio[~df_titulos_limpio['portafolio'].astype(str).str.strip().isin(portafolios_excluidos)]

        # Guardamos en la tabla histórica (APPEND)
        df_titulos_limpio.to_sql('hist_titulos', conn, if_exists='append', index=False)
        print(f"✅ {len(df_titulos_limpio)} títulos guardados exitosamente.")

        # ==========================================
        # 4. PROCESAMIENTO DE FORWARDS
        # ==========================================
        print("\n🔄 Procesando Forwards (PFCLD572.CSV)...")
        archivo_fwds = INPUTS_DIR / "PFCLD572.CSV"

        # Eliminar datos previos de esta fecha para permitir re-carga
        conn.execute("DELETE FROM hist_forwards WHERE fecha_reporte = ?", (fecha_reporte,))
        conn.commit()

        # El CSV de PORFIN tiene: línea vacía + 5 filas de metadata (6 filas en total a saltar),
        # columnas en fila 6, y una fila de guiones (------) justo después → se descarta con iloc[1:]
        df_fwds_crudo = pd.read_csv(archivo_fwds, sep=';', encoding='latin-1',
                                     skiprows=6).iloc[1:].reset_index(drop=True)

        df_fwds_limpio = limpiar_forwards(df_fwds_crudo, df_mapeo_columnas, df_mapeo_monedas)
        df_fwds_limpio['fecha_reporte'] = fecha_reporte

        df_fwds_limpio.to_sql('hist_forwards', conn, if_exists='append', index=False)
        print(f"✅ {len(df_fwds_limpio)} forwards guardados exitosamente.")

        # ==========================================
        # 5. PROCESAMIENTO DE OPCIONES (opcional)
        # ==========================================
        archivo_opciones = INPUTS_DIR / f"Informe Valoración Opciones {fecha_reporte}.xlsx"
        if archivo_opciones.exists():
            print(f"\n🔄 Procesando Opciones ({archivo_opciones.name})...")

            # Eliminar datos previos de esta fecha para permitir re-carga
            conn.execute("DELETE FROM hist_opciones WHERE fecha_reporte = ?", (fecha_reporte,))
            conn.commit()

            df_opciones_crudo = pd.read_excel(archivo_opciones, sheet_name="Informe Valoración Opciones", header=7)
            # Eliminar la primera columna vacía si existe
            if df_opciones_crudo.columns[0] == "Unnamed: 0" or pd.isna(df_opciones_crudo.columns[0]):
                df_opciones_crudo = df_opciones_crudo.iloc[:, 1:]
            df_opciones_limpio = limpiar_opciones(df_opciones_crudo, df_mapeo_columnas, df_mapeo_monedas)
            df_opciones_limpio['fecha_reporte'] = fecha_reporte
            df_opciones_limpio.to_sql('hist_opciones', conn, if_exists='append', index=False)
            print(f"✅ {len(df_opciones_limpio)} opciones guardadas exitosamente.")
        else:
            print("\n⚠️  Opciones.csv no encontrado en Inputs Py/ — se omite la carga de opciones.")

        # ==========================================
        # 6. PROCESAMIENTO DE SWAPS (opcional)
        # ==========================================
        archivo_swaps = INPUTS_DIR / "PFTIT695.CSV"
        if archivo_swaps.exists():
            print(f"\n🔄 Procesando Swaps ({archivo_swaps.name})...")

            # Eliminar datos previos de esta fecha para permitir re-carga
            conn.execute("DELETE FROM hist_swaps WHERE fecha_reporte = ?", (fecha_reporte,))
            conn.commit()

            # El CSV de PORFIN tiene: 5 filas de metadata + header + guiones
            df_swaps_crudo = pd.read_csv(archivo_swaps, sep=';', encoding='latin-1',
                                          skiprows=6).iloc[1:].reset_index(drop=True)

            df_swaps_limpio = limpiar_swaps(df_swaps_crudo, df_mapeo_columnas, df_mapeo_monedas)
            df_swaps_limpio['fecha_reporte'] = fecha_reporte
            df_swaps_limpio.to_sql('hist_swaps', conn, if_exists='append', index=False)
            print(f"✅ {len(df_swaps_limpio)} swaps guardados exitosamente.")
        else:
            print("\n⚠️  PFTIT695.CSV no encontrado en Inputs Py/ — se omite la carga de swaps.")
        
        print("\n🎉 ¡CARGA DIARIA COMPLETADA! El portafolio histórico está actualizado.")
        print("-" * 60)

    except sqlite3.IntegrityError as e:
        print(f"\n❌ ERROR DE SEGURIDAD (Integridad SQL):")
        print(f"Parece que ya cargaste los datos de la fecha '{fecha_reporte}'.")
        print("La base de datos bloqueó la operación para evitar duplicar tu portafolio.")
        
    except FileNotFoundError as e:
        print(f"\n❌ ERROR DE ARCHIVO: No se encontró uno de los archivos del día.")
        print(f"Asegúrate de haber guardado el 1.597, los Forwards y las Opciones en la carpeta 'Inputs Py'.")
        print(f"Detalle técnico: {e}")
        
    except Exception as e:
        import traceback
        print(f"\n❌ ERROR INESPERADO durante la carga: {e}")
        traceback.print_exc()
        
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fecha_hoy = sys.argv[1].strip()
    else:
        print("BIENVENIDO AL CARGADOR DE PORTAFOLIO")
        fecha_hoy = input("📅 Ingresa la fecha de los archivos a cargar (Formato YYYY-MM-DD): ").strip().lstrip('﻿')
    procesar_carga_diaria(fecha_hoy)