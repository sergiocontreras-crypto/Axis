"""
diccionario_titulos.py
----------------------
Construye el diccionario ATEMPORAL de títulos únicos del portafolio.
Asigna atributos intrínsecos que no cambian con el tiempo:
  - subtipo_vehiculo, clase_activo, codigo_solvencia
  - zona_geografica, sector, tipo_entidad
  - numeral_regimen, asset_class_base
  - tasa_referencia, tasa_facial, uso_tasa, tipo_tasa

Los atributos contextuales (tramo_plazo, asset_class_definitivo,
clasificacion_solvencia) se calculan en clasificar_portafolio.py.
"""

import pandas as pd
import numpy as np
import sqlite3

# --- CONFIGURACIÓN DE RUTAS (fuente única: comun/rutas.py) ---
from comun.rutas import DB_PATH


# =============================================================================
# 1. EXTRACCIÓN Y PREPARACIÓN
# =============================================================================

def extraer_portafolio_unificado(conn, fecha_corte):
    """Extrae títulos y derivados únicos de TODAS las fechas para construir el diccionario atemporal."""
    query = """
        SELECT 'TITULO' as clase_origen, especie, emisor, isin, moneda, facial, periodicidad
        FROM hist_titulos
        UNION
        SELECT 'FORWARD' as clase_origen, especie, emisor, NULL as isin,
               moneda_cotizacion as moneda, NULL as facial, NULL as periodicidad
        FROM hist_forwards
        UNION
        SELECT 'OPCION' as clase_origen, especie, emisor, NULL as isin,
               moneda, NULL as facial, NULL as periodicidad
        FROM hist_opciones
        UNION
        SELECT 'SWAP' as clase_origen, especie, emisor, NULL as isin,
               moneda_obl as moneda, NULL as facial, periodicidad_der as periodicidad
        FROM hist_swaps
    """
    return pd.read_sql_query(query, conn)


# =============================================================================
# 2. EXCEPCIONES (OVERRIDE DIRECTO)
# =============================================================================

def aplicar_excepciones(df_portafolio, df_excepciones):
    """Aplica clasificaciones directas desde la tabla de excepciones."""
    df_portafolio['especie'] = df_portafolio['especie'].str.strip()
    df_excepciones = df_excepciones.copy()
    df_excepciones['especie'] = df_excepciones['especie'].str.strip()

    cols_excepcion = [
        'especie', 'codigo_solvencia', 'zona_geografica',
        'sector', 'tipo_entidad', 'asset_class', 'es_ipc'
    ]
    # Solo tomar columnas que existan (retrocompatibilidad)
    cols_excepcion = [c for c in cols_excepcion if c in df_excepciones.columns]
    df_exc = df_excepciones[cols_excepcion].copy()
    df_exc = df_exc.rename(columns={'asset_class': 'asset_class_excepcion'})

    df_portafolio = df_portafolio.merge(df_exc, on='especie', how='left', suffixes=('', '_exc'))

    if 'codigo_solvencia_exc' in df_portafolio.columns:
        mascara = df_portafolio['codigo_solvencia_exc'].notna()
        df_portafolio.loc[mascara, 'codigo_solvencia'] = df_portafolio.loc[mascara, 'codigo_solvencia_exc']
        df_portafolio.loc[mascara, 'zona_geografica'] = df_portafolio.loc[mascara, 'zona_geografica_exc']
        df_portafolio.loc[mascara, 'sector'] = df_portafolio.loc[mascara, 'sector_exc']
        df_portafolio.loc[mascara, 'tipo_entidad'] = df_portafolio.loc[mascara, 'tipo_entidad_exc']
        cols_drop = [c for c in df_portafolio.columns if c.endswith('_exc')]
        df_portafolio = df_portafolio.drop(columns=cols_drop)
    else:
        mascara = df_portafolio['asset_class_excepcion'].notna()

    df_portafolio['asset_class_base'] = df_portafolio.get('asset_class_base', 'Sin Clasificar')
    df_portafolio.loc[mascara, 'asset_class_base'] = df_portafolio.loc[mascara, 'asset_class_excepcion']

    # Solo marcar como 'Excepcion' los que ya tienen asset class definitivo (no BMK_MATRIZ).
    # Los que tienen BMK_MATRIZ necesitan pasar por el regex de especies y el motor 5D.
    tiene_ac_definitivo = mascara & (df_portafolio['asset_class_base'] != 'BMK_MATRIZ')
    if tiene_ac_definitivo.any():
        df_portafolio.loc[tiene_ac_definitivo, 'subtipo_vehiculo'] = 'Excepcion'
        df_portafolio.loc[tiene_ac_definitivo, 'clase_activo'] = 'Excepcion'

    df_portafolio = df_portafolio.drop(columns=['asset_class_excepcion'], errors='ignore')

    # Asegurar que es_ipc tenga valor por defecto 'NO' para títulos sin excepción
    if 'es_ipc' in df_portafolio.columns:
        df_portafolio['es_ipc'] = df_portafolio['es_ipc'].fillna('NO')

    return df_portafolio


# =============================================================================
# 3. MOTORES DE EXPRESIONES REGULARES (REGEX)
# =============================================================================

def aplicar_mapeo_emisores_regex(df_portafolio, df_mapeo_emisores):
    """Clasifica los emisores y asigna zona, sector y tipo_entidad."""
    df_reglas = df_mapeo_emisores.sort_values('prioridad')

    for col in ['zona_geografica', 'sector', 'tipo_entidad']:
        if col not in df_portafolio.columns:
            df_portafolio[col] = 'Sin Clasificar'
        else:
            df_portafolio[col] = df_portafolio[col].fillna('Sin Clasificar')

    for _, regla in df_reglas.iterrows():
        mascara = df_portafolio['emisor'].str.contains(
            regla['palabra_clave_emisor'], regex=True, case=False, na=False
        )
        sin_clasificar = df_portafolio['sector'] == 'Sin Clasificar'
        condicion = mascara & sin_clasificar
        df_portafolio.loc[condicion, 'zona_geografica'] = regla['zona_geografica']
        df_portafolio.loc[condicion, 'sector'] = regla['sector']
        df_portafolio.loc[condicion, 'tipo_entidad'] = regla['tipo_entidad']

    return df_portafolio


def aplicar_mapeo_especies_regex(df_portafolio, df_mapeo_especies):
    """Asigna subtipo_vehiculo, codigo_solvencia y asset_class_base."""
    df_reglas = df_mapeo_especies.sort_values('prioridad')

    for col in ['clase_activo', 'codigo_solvencia', 'subtipo_vehiculo', 'asset_class_base']:
        if col not in df_portafolio.columns:
            df_portafolio[col] = 'Sin Clasificar'
        else:
            df_portafolio[col] = df_portafolio[col].fillna('Sin Clasificar')

    for _, regla in df_reglas.iterrows():
        mascara = df_portafolio['especie'].str.contains(
            regla['palabra_clave'], regex=True, case=False, na=False
        )
        sin_clasificar = df_portafolio['subtipo_vehiculo'] == 'Sin Clasificar'
        condicion = mascara & sin_clasificar
        df_portafolio.loc[condicion, 'clase_activo'] = regla['clase_activo']
        df_portafolio.loc[condicion, 'codigo_solvencia'] = regla['codigo_solvencia']
        df_portafolio.loc[condicion, 'subtipo_vehiculo'] = regla['subtipo_vehiculo']
        df_portafolio.loc[condicion, 'asset_class_base'] = regla['asset_class']

    return df_portafolio


# =============================================================================
# 4. CLASIFICACIÓN POR RÉGIMEN (DECRETO 2555)
# =============================================================================

def clasificar_regimen(df, df_excepciones, df_categorias_regimen):
    """Asigna el numeral del Régimen Legal usando dimensiones intrínsecas."""
    v = df['subtipo_vehiculo']
    e = df['tipo_entidad']
    z = df['zona_geografica']

    df_exc = df_excepciones[['especie', 'numeral_regimen']].copy()
    df_exc['especie'] = df_exc['especie'].str.strip()
    df_exc = df_exc.drop_duplicates(subset=['especie'])
    df_exc = df_exc.rename(columns={'numeral_regimen': 'numeral_excepcion'})
    df['especie'] = df['especie'].str.strip()
    df = df.merge(df_exc, on='especie', how='left')
    sin_regimen = df['numeral_excepcion'].isna() | (df['numeral_excepcion'] == '')

    condiciones = [
        # --- LOCALES ---
        sin_regimen & (v.isin(['Titulo_Deuda_Publica', 'Bono_Ordinario'])) & (e == 'Soberano') & (z == 'Local'),
        sin_regimen & (v.isin(['Titulo_Deuda_Publica', 'Bono_Ordinario'])) & (e == 'Ente_Territorial') & (z == 'Local'),
        sin_regimen & (v == 'Titulo_Deuda_Publica') & (z == 'Local'),
        sin_regimen & (v.isin(['CDT', 'Bono_Ordinario'])) & (e == 'Vigilada_SFC') & (z == 'Local'),
        sin_regimen & (v == 'Titularizacion_No_Hipotecaria') & (z == 'Local'),
        sin_regimen & (v.isin(['CDT', 'Bono_Ordinario'])) & (e == 'No_Vigilada_SFC') & (z == 'Local'),
        sin_regimen & (v.isin(['FIC_Local_COP', 'ETF_Acciones_Generico'])) & (z == 'Local'),
        sin_regimen & (v == 'Accion_Alta_Media') & (z == 'Local'),
        sin_regimen & (v == 'ETF_Acciones_Local') & (z == 'Local'),
        sin_regimen & (v == 'FCP_Local') & (z == 'Local'),
        # --- MULTILATERAL ---
        sin_regimen & (v == 'Bono_Ordinario') & (e == 'Multilateral'),
        # --- EXTERIOR ---
        sin_regimen & (v.isin(['Titulo_Deuda_Publica', 'Bono_Ordinario'])) & (e == 'Soberano') & (z.isin(['Exterior', 'G-10'])),
        sin_regimen & (v == 'Bono_Ordinario') & (e == 'Banco_Exterior') & (z == 'Exterior'),
        sin_regimen & (v == 'Bono_Ordinario') & (e == 'No_Banco_Exterior') & (z == 'Exterior'),
        sin_regimen & (v.isin(['ETF_Acciones_Ext', 'ETF_Acciones_Generico'])) & (z == 'Exterior'),
        sin_regimen & (v == 'ETF_Commodities_Ext'),
        sin_regimen & (v == 'Accion_Alta_Media') & (z == 'Exterior'),
        # --- CAJA Y DERIVADOS ---
        sin_regimen & (v == 'Deposito_Vista') & (e == 'Soberano') & (z == 'Local'),
        sin_regimen & (v == 'Deposito_Vista') & (e == 'Vigilada_SFC') & (z == 'Local'),
        sin_regimen & (v.isin(['Deposito_Vista', 'FIC_Local_USD'])) & (e == 'Banco_Exterior') & (z == 'Exterior'),
        sin_regimen & (v == 'Repo_Simultanea'),
        sin_regimen & (v.isin(['Forward_General', 'Swap_General', 'Opcion_General'])),
    ]

    numerales = [
        '1.1.1', '1.1.2', '1.1.2', '1.2', '1.4', '1.6', '1.7', '1.10.1', '1.10.3', '1.11',
        '2.4',
        '2.1', '2.2', '2.3', '2.6.1', '2.8', '2.6.2',
        '3.1', '3.1', '3.2', '3.3.1', '3.4.2',
    ]

    numeral_calculado = np.select(condiciones, numerales, default='SIN_NUMERAL')
    df['numeral_regimen'] = pd.Series(numeral_calculado, index=df.index)
    tiene_excepcion = ~sin_regimen
    df.loc[tiene_excepcion, 'numeral_regimen'] = df.loc[tiene_excepcion, 'numeral_excepcion']
    df = df.drop(columns=['numeral_excepcion'], errors='ignore')

    # Enriquecer con descripción y límites
    df['numeral_regimen'] = df['numeral_regimen'].astype(str)
    df_categorias_regimen = df_categorias_regimen.copy()
    df_categorias_regimen['numeral_regimen'] = df_categorias_regimen['numeral_regimen'].astype(str)
    df = df.merge(df_categorias_regimen, on='numeral_regimen', how='left')

    return df


# =============================================================================
# 5. ORQUESTADOR PRINCIPAL
# =============================================================================

def generar_diccionario_clasificado(fecha_corte):
    """Genera el diccionario atemporal de títulos únicos."""
    print(f"🧠 Generando diccionario atemporal para la fecha: {fecha_corte}")
    conn = sqlite3.connect(DB_PATH)

    try:
        # 1. Traer datos
        df_port = extraer_portafolio_unificado(conn, fecha_corte)
        df_faciales = pd.read_sql_query("SELECT * FROM param_condiciones_faciales", conn)
        df_map_emisores = pd.read_sql_query("SELECT * FROM param_emisores", conn)
        df_map_especies = pd.read_sql_query("SELECT * FROM param_especies", conn)
        df_excepciones = pd.read_sql_query("SELECT * FROM param_excepciones", conn)
        df_categorias_regimen = pd.read_sql_query("SELECT * FROM param_categorias_regimen", conn)

        # 2. Filtrar únicos por especie + emisor + moneda + isin
        cols_clave = ['clase_origen', 'especie', 'emisor', 'isin', 'moneda']
        df_unicos = df_port[cols_clave].drop_duplicates().reset_index(drop=True)

        # Traer facial del portafolio (primer valor no nulo por ISIN)
        df_facial_port = df_port[['isin', 'facial']].dropna(subset=['facial']).drop_duplicates(subset=['isin'])
        df_unicos = df_unicos.merge(df_facial_port, on='isin', how='left')

        # Traer periodicidad del portafolio (primer valor no nulo por ISIN)
        df_period_port = df_port[['isin', 'periodicidad']].dropna(subset=['periodicidad', 'isin'])
        df_period_port['isin'] = df_period_port['isin'].str.strip()
        df_period_port = df_period_port.drop_duplicates(subset=['isin'])
        df_unicos['isin'] = df_unicos['isin'].fillna('').str.strip()
        df_unicos = df_unicos.merge(df_period_port, on='isin', how='left')

        # 3. Enriquecer con faciales (tasa de referencia, tasa facial, etc.)
        df_faciales_dedup = df_faciales.drop_duplicates(subset=['isin'])
        df_unicos = df_unicos.merge(df_faciales_dedup, on='isin', how='left', suffixes=('_port', '_param'))

        # Resolver periodicidad: param tiene prioridad, si no, usar hist_titulos
        if 'periodicidad_param' in df_unicos.columns and 'periodicidad_port' in df_unicos.columns:
            df_unicos['periodicidad'] = df_unicos['periodicidad_param'].fillna(df_unicos['periodicidad_port'])
            df_unicos = df_unicos.drop(columns=['periodicidad_param', 'periodicidad_port'])
        elif 'periodicidad_port' in df_unicos.columns:
            df_unicos = df_unicos.rename(columns={'periodicidad_port': 'periodicidad'})

        # Fallback: parsear columna 'facial' para títulos sin tasa_referencia
        from libro_de_funciones import parsear_facial
        sin_tasa_ref = df_unicos['tasa_referencia'].isna()
        if sin_tasa_ref.any() and 'facial' in df_unicos.columns:
            df_parsed = parsear_facial(df_unicos.loc[sin_tasa_ref, 'facial'])
            df_unicos.loc[sin_tasa_ref, 'tasa_referencia'] = df_parsed['tasa_referencia_parsed'].values
            if 'tasa_facial' not in df_unicos.columns:
                df_unicos['tasa_facial'] = None
            df_unicos.loc[sin_tasa_ref, 'tasa_facial'] = df_parsed['tasa_facial_parsed'].values
            if 'tipo_tasa' not in df_unicos.columns:
                df_unicos['tipo_tasa'] = None
            df_unicos.loc[sin_tasa_ref, 'tipo_tasa'] = df_parsed['tipo_tasa_parsed'].values
            if 'uso_tasa' not in df_unicos.columns:
                df_unicos['uso_tasa'] = None
            df_unicos.loc[sin_tasa_ref, 'uso_tasa'] = df_parsed['uso_tasa_parsed'].values

        # Regla específica: TCO (títulos cero cupón del Banco de la República)
        es_tco = df_unicos['especie'].str.startswith('TCO', na=False)
        df_unicos.loc[es_tco, 'tasa_referencia'] = 'FS'
        df_unicos.loc[es_tco, 'base_anual'] = 365
        df_unicos.loc[es_tco, 'tasa_facial'] = 0
        df_unicos.loc[es_tco, 'periodicidad'] = 'PERIÓDICA'
        df_unicos.loc[es_tco, 'tipo_tasa'] = 'NOMINAL'
        df_unicos.loc[es_tco, 'uso_tasa'] = 'ACTUAL'

        # Regla: TES y títulos de deuda pública (TFIT, TUVT, TRST, TFVT) → base 365
        es_tes = df_unicos['especie'].str.match(r'^(TFIT|TUVT|TRST|TFVT)', na=False)
        df_unicos.loc[es_tes, 'base_anual'] = 365

        # Regla: Yankees y títulos en USD → base 360
        if 'moneda' in df_unicos.columns:
            es_usd = df_unicos['moneda'].str.upper().str.strip().isin(['USD', 'US', 'DOLAR'])
            sin_base_usd = es_usd & df_unicos['base_anual'].isna()
            df_unicos.loc[sin_base_usd, 'base_anual'] = 360

        # Regla: CDTs → base 365
        es_cdt = df_unicos['especie'].str.match(r'^(CDT|CDTF|CDTV)', na=False)
        sin_base_cdt = es_cdt & df_unicos['base_anual'].isna()
        df_unicos.loc[sin_base_cdt, 'base_anual'] = 365

        # Regla: Bonos COP genéricos → base 365 (si no fue asignado antes)
        es_bono = df_unicos['especie'].str.match(r'^(BONO|BON|BORD|BORE)', na=False)
        sin_base_bono = es_bono & df_unicos['base_anual'].isna()
        df_unicos.loc[sin_base_bono, 'base_anual'] = 365

        df_unicos = df_unicos.drop(columns=['facial'], errors='ignore')

        # 4. Aplicar excepciones (override directo antes de regex)
        df_unicos = aplicar_excepciones(df_unicos, df_excepciones)

        # 5. Clasificaciones intrínsecas por Regex
        print("🔍 Aplicando motores de Regex...")
        df_unicos = aplicar_mapeo_emisores_regex(df_unicos, df_map_emisores)
        df_unicos = aplicar_mapeo_especies_regex(df_unicos, df_map_especies)

        # 6. Clasificación por Régimen
        print("⚖️ Clasificando por Régimen Legal...")
        df_unicos = clasificar_regimen(df_unicos, df_excepciones, df_categorias_regimen)

        # 7. Persistir — INCREMENTAL: solo agregar nuevos, no sobreescribir existentes
        try:
            df_existente = pd.read_sql_query("SELECT * FROM diccionario_clasificado", conn)
        except Exception:
            df_existente = pd.DataFrame()

        if df_existente.empty:
            # Primera vez: guardar todo
            df_unicos.to_sql('diccionario_clasificado', conn, if_exists='replace', index=False)
            n_nuevos = len(df_unicos)
        else:
            # Identificar títulos nuevos (no están en el diccionario existente por especie+emisor)
            claves_existentes = set(
                (str(r['especie']).strip() + '|' + str(r['emisor']).strip())
                for _, r in df_existente.iterrows()
            )
            df_nuevos = df_unicos[
                df_unicos.apply(
                    lambda r: (str(r['especie']).strip() + '|' + str(r['emisor']).strip()) not in claves_existentes,
                    axis=1
                )
            ]
            n_nuevos = len(df_nuevos)
            if n_nuevos > 0:
                # Agregar solo los nuevos
                df_nuevos.to_sql('diccionario_clasificado', conn, if_exists='append', index=False)
                print(f"  ➕ {n_nuevos} título(s) nuevo(s) agregado(s) al diccionario.")
            else:
                print(f"  ✓ No hay títulos nuevos por agregar.")

        conn.commit()
        # Leer el diccionario completo (existentes + nuevos)
        df_final = pd.read_sql_query("SELECT * FROM diccionario_clasificado", conn)
        print(f"✅ Diccionario atemporal: {len(df_final)} registros ({n_nuevos} nuevos).")
        return df_final

    except Exception as e:
        import traceback
        print(f"❌ Error durante el procesamiento: {e}")
        traceback.print_exc()
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fecha_prueba = sys.argv[1].strip()
    else:
        # Usar la última fecha disponible en hist_titulos
        conn = sqlite3.connect(DB_PATH)
        ultima = pd.read_sql_query("SELECT MAX(fecha_reporte) as f FROM hist_titulos", conn)
        conn.close()
        fecha_prueba = str(ultima['f'].iloc[0])[:10] if not ultima.empty and ultima['f'].iloc[0] else None
        if not fecha_prueba:
            print("❌ No hay datos en hist_titulos. Corre portfolio_loader_beta primero.")
            sys.exit(1)
    df_res = generar_diccionario_clasificado(fecha_prueba)
    if df_res is not None:
        print("\n📊 Muestra del Diccionario Atemporal:")
        cols = ['especie', 'subtipo_vehiculo', 'zona_geografica', 'sector',
                'numeral_regimen', 'asset_class_base', 'tasa_referencia']
        cols = [c for c in cols if c in df_res.columns]
        print(df_res[cols].head(10))
