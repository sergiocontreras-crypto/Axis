"""
clasificar_portafolio.py
------------------------
Clasificación DIARIA del portafolio vigente.
Toma el portafolio del día, lo cruza con el diccionario atemporal, y calcula
los atributos que dependen del momento:
  - tramo_plazo (días al vencimiento vs fecha de corte)
  - asset_class_definitivo (motor 5D con tramo)
  - clasificacion_solvencia (matriz de reglas + calificación vigente)

Se ejecuta cada día después de portfolio_loader_beta.py.
"""

import pandas as pd
import numpy as np
import sqlite3

# --- CONFIGURACIÓN DE RUTAS (fuente única: comun/rutas.py) ---
from comun.rutas import DB_PATH


# =============================================================================
# 1. EXTRACCIÓN DEL PORTAFOLIO DEL DÍA
# =============================================================================

def extraer_portafolio_dia(conn, fecha_corte):
    """Extrae el portafolio completo del día."""
    query = f"""
        SELECT 'TITULO' as clase_origen, fecha_reporte, id_posicion, especie,
               emisor, isin, calificacion, moneda, fecha_vencimiento,
               nominal, valor_mercado, tasa_compra, tir, tasa_mercado, estado, portafolio, clasificacion_contable
        FROM hist_titulos WHERE fecha_reporte = '{fecha_corte}'
        UNION ALL
        SELECT 'FORWARD' as clase_origen, fecha_reporte, id_posicion, especie,
               emisor, NULL as isin, NULL as calificacion, moneda_cotizacion as moneda,
               fecha_vencimiento, nominal, valor_mercado, NULL, NULL, NULL as tasa_mercado, estado, portafolio, NULL as clasificacion_contable
        FROM hist_forwards WHERE fecha_reporte = '{fecha_corte}'
        UNION ALL
        SELECT 'OPCION' as clase_origen, fecha_reporte, id_posicion, especie,
               emisor, NULL as isin, NULL as calificacion, moneda,
               fecha_vencimiento, nominal, valor_mercado, NULL, NULL, NULL as tasa_mercado, estado, portafolio, NULL as clasificacion_contable
        FROM hist_opciones WHERE fecha_reporte = '{fecha_corte}'
        UNION ALL
        SELECT 'SWAP' as clase_origen, fecha_reporte, id_posicion, especie,
               emisor, NULL as isin, NULL as calificacion, moneda_obl as moneda,
               fecha_vencimiento, nominal_obl as nominal, valor_mercado, NULL, NULL, NULL as tasa_mercado, estado, portafolio, NULL as clasificacion_contable
        FROM hist_swaps WHERE fecha_reporte = '{fecha_corte}'
    """
    return pd.read_sql_query(query, conn)


# =============================================================================
# 2. CÁLCULO DE TRAMOS DE PLAZO
# =============================================================================

def calcular_tramos_plazo(df, fecha_corte):
    """Calcula días al vencimiento y categoriza en tramos dinámicos."""
    df['fecha_vencimiento'] = pd.to_datetime(df['fecha_vencimiento'], errors='coerce')
    fecha_ref = pd.to_datetime(fecha_corte)
    df['dias_vencimiento'] = (df['fecha_vencimiento'] - fecha_ref).dt.days

    d = df['dias_vencimiento']
    v = df['subtipo_vehiculo']
    tasa = df.get('tasa_referencia', pd.Series('', index=df.index)).fillna('')

    condiciones = [
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'COP') & (d <= 1091),
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'COP') & (d > 1091) & (d <= 2551),
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'COP') & (d > 2551),
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'UVR') & (d <= 1456),
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'UVR') & (d > 1456) & (d <= 3646),
        (v == 'Titulo_Deuda_Publica') & (df['moneda'] == 'UVR') & (d > 3646),
        (v.isin(['CDT', 'Bono_Ordinario'])) & (d <= 90),
        (v.isin(['CDT', 'Bono_Ordinario'])) & (d > 90) & (d <= 365),
        (v.isin(['CDT', 'Bono_Ordinario', 'Titularizacion_No_Hipotecaria'])) & (tasa == 'IPC') & (d <= 1825),
        (v.isin(['CDT', 'Bono_Ordinario', 'Titularizacion_No_Hipotecaria'])) & (tasa == 'IPC') & (d > 1825),
        (v.isin(['CDT', 'Bono_Ordinario'])) & (tasa == 'FS') & (d <= 730),
        (v.isin(['CDT', 'Bono_Ordinario'])) & (tasa == 'FS') & (d > 730),
        (d <= 365),
        (d > 365) & (d <= 1825),
        (d > 1825),
    ]

    tramos = [
        'Corto Plazo', 'Mediano Plazo', 'Largo Plazo',
        'Corto Plazo', 'Mediano Plazo', 'Largo Plazo',
        'Q1', 'Q234',
        'Corto Plazo', 'Largo Plazo',
        'Corto Plazo', 'Largo Plazo',
        'Corto Plazo', 'Mediano Plazo', 'Largo Plazo',
    ]

    df['tramo_plazo'] = np.select(condiciones, tramos, default='Sin Plazo')
    return df


# =============================================================================
# 3. MOTOR DE ASSET CLASS 5D
# =============================================================================

def resolver_asset_class(df, df_matriz):
    """Cruza el portafolio con la matriz de 5 dimensiones."""
    df_matriz = df_matriz.rename(columns={
        'tasa_referencia': 'regla_tasa',
        'tramo_plazo': 'regla_plazo',
        'moneda': 'regla_moneda',
        'sector': 'regla_sector'
    })

    df = df.reset_index(drop=False).rename(columns={'index': 'temp_id'})
    cruce = df.merge(df_matriz, on='subtipo_vehiculo', how='left')

    for col in ['tasa_referencia', 'tramo_plazo', 'sector', 'moneda']:
        cruce[col] = cruce[col].fillna('N/A')

    cond_tasa = (cruce['tasa_referencia'] == cruce['regla_tasa']) | (cruce['regla_tasa'] == 'CUALQUIERA')
    cond_plazo = (cruce['tramo_plazo'] == cruce['regla_plazo']) | (cruce['regla_plazo'] == 'CUALQUIERA')
    cond_sector = (cruce['sector'] == cruce['regla_sector']) | (cruce['regla_sector'] == 'CUALQUIERA')
    cond_moneda = (
        (cruce['moneda'] == cruce['regla_moneda']) |
        (cruce['regla_moneda'] == 'CUALQUIERA') |
        ((cruce['regla_moneda'] == '!COP') & (cruce['moneda'] != 'COP'))
    )

    matches = cruce[cond_tasa & cond_plazo & cond_sector & cond_moneda].copy()
    matches = matches.drop_duplicates(subset=['temp_id'], keep='first')

    df_final = df.merge(matches[['temp_id', 'asset_class_final']], on='temp_id', how='left')
    df_final['asset_class_definitivo'] = np.where(
        df_final['asset_class_base'] == 'BMK_MATRIZ',
        df_final['asset_class_final'].fillna('Falta_Regla_Matriz'),
        df_final['asset_class_base']
    )

    return df_final.drop(columns=['temp_id', 'asset_class_final'])


# =============================================================================
# 4. CLASIFICACIÓN DE SOLVENCIA POR CALIFICACIÓN
# =============================================================================

def mapear_calificacion_a_grupo(serie_calificacion):
    """
    Mapea la calificación crediticia a un grupo para cruzar con la matriz.
    Soporta escalas colombianas (BRC, Fitch Colombia) e internacionales (Moody's).
    """
    cal = serie_calificacion.fillna('').str.upper().str.strip()

    condiciones = [
        # Largo plazo — GRUPO_AAA_AA
        cal.isin(['AAA', 'AA+', 'AA', 'AA-']) | cal.str.contains(r'^AA\s*\d', regex=True),
        # Largo plazo — GRUPO_A
        cal.isin(['A+', 'A', 'A-']) | cal.str.contains(r'^A\s*\d', regex=True),
        # Largo plazo — GRUPO_BBB
        cal.isin(['BBB+', 'BBB', 'BBB-']),
        # Corto plazo — GRUPO_CP_1
        cal.isin(['BRC1+', 'BRC1', 'BRC1-', 'F1+', 'F1 +', 'F1', '1+', '1', '1-']),
        # Corto plazo — GRUPO_CP_2
        cal.isin(['BRC2+', 'BRC2', 'BRC2-', 'F2', '2+', '2', '2-']),
        # Corto plazo — GRUPO_CP_3
        cal.isin(['BRC3', 'BRC3-', 'F3', '3', '3-', '4', '5', '6']),
        # Largo plazo inferior (BB y menos)
        cal.isin(['BB+', 'BB', 'BB-', 'B+', 'B', 'B-', 'CCC', 'CC', 'C', 'D']),
        # Exentos de calificación (Nación, acciones — NO incluye 'No def.' de renta fija)
        cal.isin(['NACION', 'ACCIONES', 'NO REQUIER']),
    ]

    grupos = [
        'GRUPO_AAA_AA',
        'GRUPO_A',
        'GRUPO_BBB',
        'GRUPO_CP_1',
        'GRUPO_CP_2',
        'GRUPO_CP_3',
        'GRUPO_INFERIOR_O_SIN_CALIFICACION',
        'CUALQUIERA',
    ]

    return pd.Series(
        np.select(condiciones, grupos, default='GRUPO_INFERIOR_O_SIN_CALIFICACION'),
        index=serie_calificacion.index
    )


def clasificar_solvencia(df, df_matriz_solvencia, df_categorias_solvencia):
    """
    Resuelve la clasificación de solvencia definitiva.
    Para codigo_solvencia fijo (no CS_MATRIZ): usa directo.
    Para CS_MATRIZ: cruza con matriz_reglas_solvencia + calificación vigente.
    """
    df['grupo_calificacion'] = mapear_calificacion_a_grupo(df['calificacion'])

    tiene_codigo_fijo = (df['codigo_solvencia'] != 'CS_MATRIZ') & (df['codigo_solvencia'].notna())

    # Para los que necesitan la matriz
    necesita_matriz = ~tiene_codigo_fijo
    if necesita_matriz.any():
        df_necesita = df.loc[necesita_matriz].copy()
        df_reglas = df_matriz_solvencia.copy()

        cruce = df_necesita.merge(df_reglas, on='clase_activo', how='left', suffixes=('', '_regla'))

        cond_zona = (cruce['zona_geografica'] == cruce['zona_geografica_regla']) | (cruce['zona_geografica_regla'] == 'CUALQUIERA')
        cond_entidad = (cruce['tipo_entidad'] == cruce['tipo_entidad_regla']) | (cruce['tipo_entidad_regla'] == 'CUALQUIERA')
        cond_calif = (cruce['grupo_calificacion'] == cruce['grupo_calificacion_regla']) | (cruce['grupo_calificacion_regla'] == 'CUALQUIERA')

        matches = cruce[cond_zona & cond_entidad & cond_calif].copy()
        matches = matches.drop_duplicates(subset=['id_posicion'], keep='first')

        if 'clasificacion_solvencia' in matches.columns:
            # Buscar el codigo_solvencia que corresponde a la clasificación encontrada
            matches = matches.merge(
                df_categorias_solvencia[['clasificacion_solvencia', 'codigo_solvencia']],
                on='clasificacion_solvencia',
                how='left',
                suffixes=('_orig', '_resuelto')
            )
            # Actualizar el codigo_solvencia solo para los que resolvieron (no sobreescribir con NaN)
            resueltos = matches.set_index('id_posicion')['codigo_solvencia_resuelto']
            mapeado = df.loc[necesita_matriz, 'id_posicion'].map(resueltos)
            resolvio = mapeado.notna()
            if resolvio.any():
                df.loc[resolvio.index[resolvio], 'codigo_solvencia'] = mapeado[resolvio].values

    # Enriquecer con categorías de solvencia (ponderación, categoría)
    df = df.merge(
        df_categorias_solvencia[['codigo_solvencia', 'clasificacion_solvencia', 'categoria', 'ponderacion']],
        on='codigo_solvencia',
        how='left'
    )

    return df


# =============================================================================
# 5. ORQUESTADOR PRINCIPAL
# =============================================================================

def clasificar_portafolio_diario(fecha_corte):
    """Clasificación completa del portafolio vigente para una fecha de corte."""
    print(f"📊 Clasificando portafolio vigente para: {fecha_corte}")
    print("-" * 60)
    conn = sqlite3.connect(DB_PATH)

    try:
        # 1. Extraer portafolio del día
        df_port = extraer_portafolio_dia(conn, fecha_corte)
        df_port['fecha_reporte'] = pd.to_datetime(df_port['fecha_reporte'], errors='coerce')
        df_port['fecha_vencimiento'] = pd.to_datetime(df_port['fecha_vencimiento'], errors='coerce')

        # Estandarizar clasificación contable
        mapeo_clasif_contable = {'1CP': 'Negociable', '1DV': 'Disp. Venta', '1VC': 'Vencimiento'}
        if 'clasificacion_contable' in df_port.columns:
            df_port['clasificacion_contable'] = df_port['clasificacion_contable'].map(mapeo_clasif_contable).fillna(df_port['clasificacion_contable'])
            # Forwards y opciones siempre son Negociable
            mask_fwd_opc = df_port['clase_origen'].isin(['FORWARD', 'OPCION'])
            df_port.loc[mask_fwd_opc, 'clasificacion_contable'] = 'Negociable'

        print(f"📥 Portafolio extraído: {len(df_port)} posiciones")

        # 2. Traer diccionario atemporal
        df_diccionario = pd.read_sql_query("SELECT * FROM diccionario_clasificado", conn)

        # 3. Cruzar portafolio con diccionario (merge relajado)
        cols_diccionario = [
            'especie', 'emisor', 'isin', 'moneda', 'subtipo_vehiculo',
            'clase_activo', 'codigo_solvencia', 'zona_geografica', 'sector',
            'tipo_entidad', 'numeral_regimen', 'descripcion_regimen', 'asset_class_base',
            'tasa_referencia', 'tasa_facial', 'tipo_tasa', 'uso_tasa', 'periodicidad', 'base_anual',
            'es_ipc'
        ]
        cols_disponibles = [c for c in cols_diccionario if c in df_diccionario.columns]
        df_dict_subset = df_diccionario[cols_disponibles].drop_duplicates()

        # Normalizar ISIN para evitar problemas de espacios
        df_port['isin'] = df_port['isin'].fillna('').str.strip()
        df_dict_subset['isin'] = df_dict_subset['isin'].fillna('').str.strip()
        df_port['especie'] = df_port['especie'].fillna('').str.strip()
        df_dict_subset['especie'] = df_dict_subset['especie'].fillna('').str.strip()

        # Merge primario por ISIN (para títulos con ISIN válido)
        cols_merge_resultado = [c for c in cols_disponibles if c not in ['especie', 'emisor', 'isin', 'moneda']]
        isin_valido = (df_dict_subset['isin'] != '') & (df_dict_subset['isin'] != '.')
        df_dict_por_isin = df_dict_subset[isin_valido][['isin'] + cols_merge_resultado].drop_duplicates(subset=['isin'])
        # También excluir ISIN='.' del portafolio para que no haga match erróneo
        df_port['isin'] = df_port['isin'].replace('.', '')
        df_port = df_port.merge(df_dict_por_isin, on='isin', how='left', suffixes=('', '_dict'))

        # Consolidar columnas con suffix _dict (priorizar diccionario sobre portafolio)
        for col in cols_merge_resultado:
            col_dict = f"{col}_dict"
            if col_dict in df_port.columns:
                df_port[col] = df_port[col_dict].fillna(df_port.get(col))
                df_port = df_port.drop(columns=[col_dict])

        # Merge secundario por especie+emisor (para derivados y títulos sin ISIN)
        sin_match = df_port['subtipo_vehiculo'].isna()
        if sin_match.any():
            df_dict_por_especie = df_dict_subset[['especie', 'emisor'] + cols_merge_resultado].drop_duplicates(subset=['especie', 'emisor'])
            df_fallback = df_port.loc[sin_match, ['especie', 'emisor']].merge(
                df_dict_por_especie, on=['especie', 'emisor'], how='left'
            )
            for col in cols_merge_resultado:
                if col in df_fallback.columns:
                    df_port.loc[sin_match, col] = df_fallback[col].values

        # 4. Traer tasa de referencia (ya viene del diccionario en el paso 3)

        # Regla directa: títulos de la Nación → CS_03 (Categoría I)
        if 'calificacion' in df_port.columns and 'codigo_solvencia' in df_port.columns:
            es_nacion = df_port['calificacion'].str.upper().str.strip() == 'NACION'
            df_port.loc[es_nacion, 'codigo_solvencia'] = 'CS_03'

        # Reemplazar calificaciones 'No def.' con calificaciones_faltantes (por emisor)
        if 'calificacion' in df_port.columns and 'emisor' in df_port.columns:
            df_calif_faltantes = pd.read_sql_query("SELECT emisor, calificacion FROM param_calificaciones_faltantes WHERE calificacion IS NOT NULL AND calificacion != ''", conn)
            if not df_calif_faltantes.empty:
                df_calif_faltantes['emisor'] = df_calif_faltantes['emisor'].str.strip()
                es_no_def = df_port['calificacion'].str.strip().isin(['No def.', 'NO REQUIER', ''])
                if es_no_def.any():
                    mapeo_calif = dict(zip(df_calif_faltantes['emisor'], df_calif_faltantes['calificacion']))
                    nuevas_calif = df_port.loc[es_no_def, 'emisor'].str.strip().map(mapeo_calif)
                    tiene_match = nuevas_calif.notna()
                    if tiene_match.any():
                        df_port.loc[tiene_match.index[tiene_match], 'calificacion'] = nuevas_calif[tiene_match].values

        # 5. Calcular tramo de plazo
        print("⏱️  Calculando tramos de plazo...")
        df_port = calcular_tramos_plazo(df_port, fecha_corte)

        # 6. Resolver asset class definitivo
        print("⚙️  Resolviendo Asset Class 5D...")
        df_matriz_ac = pd.read_sql_query("SELECT * FROM param_matriz_reglas_asset_class", conn)
        # Asignar ID temporal para no perder alineación
        df_port['_row_id'] = range(len(df_port))
        df_ac_result = resolver_asset_class(df_port[['_row_id', 'subtipo_vehiculo', 'tasa_referencia', 'tramo_plazo', 'sector', 'moneda', 'asset_class_base']].copy(), df_matriz_ac)
        df_port['asset_class_definitivo'] = df_ac_result['asset_class_definitivo'].values
        df_port = df_port.drop(columns=['_row_id'])

        # 6.5. Clasificar colaterales como "Colateral Recibido" / "Colateral Enviado"
        mask_col_recibido = df_port['especie'].str.contains('recibido', case=False, na=False)
        mask_col_enviado = df_port['especie'].str.contains('enviado', case=False, na=False)
        df_port.loc[mask_col_recibido, 'asset_class_definitivo'] = 'Colateral Recibido'
        df_port.loc[mask_col_enviado, 'asset_class_definitivo'] = 'Colateral Enviado'

        # 6.6. Reclasificar subyacentes de Swaps y Swaps como "Estructuras"
        print("🏗️  Asignando asset class 'Estructuras' a subyacentes de Swaps...")
        try:
            df_dic_swaps_ac = pd.read_sql_query("SELECT id_posicion, Subyacente FROM param_diccionario_swaps", conn)
            # Recopilar todos los id_posicion de subyacentes (maneja pipe para múltiples)
            ids_subyacentes = set()
            for _, row in df_dic_swaps_ac.iterrows():
                for sub in str(row['Subyacente']).split('|'):
                    ids_subyacentes.add(sub.strip())
            # Reclasificar subyacentes de Swaps → Estructuras
            mask_subyacentes = df_port['id_posicion'].astype(str).str.strip().isin(ids_subyacentes)
            n_sub_reclasificados = mask_subyacentes.sum()
            df_port.loc[mask_subyacentes, 'asset_class_definitivo'] = 'Estructuras'
            # Reclasificar Swaps → Estructuras
            mask_swaps_ac = df_port['clase_origen'] == 'SWAP'
            n_swaps_reclasificados = mask_swaps_ac.sum()
            df_port.loc[mask_swaps_ac, 'asset_class_definitivo'] = 'Estructuras'
            print(f"  ✅ {n_sub_reclasificados} subyacentes + {n_swaps_reclasificados} swaps → 'Estructuras'")
        except Exception as e:
            print(f"  ⚠️ No se pudo asignar 'Estructuras': {e}")

        # 7. Determinar encaje según estado, tipo de portafolio y ramo
        print("🔍 Determinando encaje...")
        df_portafolios = pd.read_sql_query("SELECT portafolio_porfin, ramo, tipo FROM param_portafolios", conn)
        df_port = df_port.merge(
            df_portafolios, left_on='portafolio', right_on='portafolio_porfin', how='left'
        )

        tipos_no_encajan = ['EMBARGO', 'COLATERAL', 'titulo_simultanea_activa', 'simultanea_activa', 'simultanea_pasiva', 'AJUSTE LIQUIDEZ']
        ramos_no_encajan = ['BOLIVAR EXCESO', 'COMERCIALES EXCESO', 'CAPITALIZADORA EXCESO']

        df_port['encaje'] = np.where(
            df_port['estado'].isin(['RG', 'GA']) |
            df_port['tipo'].isin(tipos_no_encajan) |
            df_port['ramo'].isin(ramos_no_encajan),
            'NO', 'SI'
        )
        df_port = df_port.drop(columns=['portafolio_porfin', 'tipo'])

        # Agregar compañía según prefijo del portafolio
        df_port['compania'] = np.select(
            [
                df_port['portafolio'].str.startswith('02'),
                df_port['portafolio'].str.startswith('03'),
                df_port['portafolio'].str.startswith('01'),
            ],
            ['Bolívar', 'Comerciales', 'Capitalizadora'],
            default='Sin Compañía'
        )

        # 7.5. Asignar régimen a derivados (3.4.1 cobertura / 3.4.2 inversión)
        print("📋 Asignando régimen a derivados...")
        mask_derivados = df_port['clase_origen'].isin(['FORWARD', 'OPCION', 'SWAP'])
        if mask_derivados.any():
            # Para forwards: leer objetivo desde hist_forwards
            mask_fwd = df_port['clase_origen'] == 'FORWARD'
            if mask_fwd.any():
                df_obj_fwd = pd.read_sql_query(
                    "SELECT id_posicion, objetivo FROM hist_forwards WHERE fecha_reporte = ?",
                    conn, params=(fecha_corte,)
                )
                df_obj_fwd['id_posicion'] = df_obj_fwd['id_posicion'].astype(str).str.strip()
                df_port['id_posicion'] = df_port['id_posicion'].astype(str).str.strip()
                obj_map_fwd = dict(zip(df_obj_fwd['id_posicion'], df_obj_fwd['objetivo']))
                df_port.loc[mask_fwd, 'objetivo_derivado'] = df_port.loc[mask_fwd, 'id_posicion'].map(obj_map_fwd)

            # Para opciones y swaps: leer desde param_objetivo_derivados
            mask_opc_sw = df_port['clase_origen'].isin(['OPCION', 'SWAP'])
            if mask_opc_sw.any():
                df_obj_param = pd.read_sql_query("SELECT id_posicion, objetivo FROM param_objetivo_derivados", conn)
                df_obj_param['id_posicion'] = df_obj_param['id_posicion'].astype(str).str.strip()
                obj_map_param = dict(zip(df_obj_param['id_posicion'], df_obj_param['objetivo']))
                df_port.loc[mask_opc_sw, 'objetivo_derivado'] = df_port.loc[mask_opc_sw, 'id_posicion'].map(obj_map_param)

            # Asignar numeral_regimen según objetivo
            obj = df_port['objetivo_derivado'].fillna('').str.lower().str.strip()
            es_cobertura = mask_derivados & obj.isin(['cobertura'])
            es_inversion = mask_derivados & obj.isin(['inversion', 'inversión', 'especulacion', 'especulación'])
            es_pendiente = mask_derivados & ~es_cobertura & ~es_inversion

            df_port.loc[es_cobertura, 'numeral_regimen'] = '3.4.1'
            df_port.loc[es_cobertura, 'descripcion_regimen'] = 'Derivados con fines de cobertura'
            df_port.loc[es_inversion, 'numeral_regimen'] = '3.4.2'
            df_port.loc[es_inversion, 'descripcion_regimen'] = 'Derivados con fines de inversión'
            df_port.loc[es_pendiente, 'numeral_regimen'] = 'PENDIENTE_OBJETIVO'
            df_port.loc[es_pendiente, 'descripcion_regimen'] = 'PENDIENTE_OBJETIVO'

            # Limpiar columna temporal
            if 'objetivo_derivado' in df_port.columns:
                df_port = df_port.drop(columns=['objetivo_derivado'])

        # 8. Clasificar solvencia por calificación (solo para los que encajan)
        print("🏛️  Clasificando solvencia por calificación...")
        df_matriz_solv = pd.read_sql_query("SELECT * FROM param_matriz_reglas_solvencia", conn)
        df_categorias_solv = pd.read_sql_query("SELECT * FROM param_categorias_solvencia", conn)
        df_matriz_solv = df_matriz_solv.rename(columns={
            'zona_geografica': 'zona_geografica_regla',
            'tipo_entidad': 'tipo_entidad_regla',
            'grupo_calificacion': 'grupo_calificacion_regla'
        })

        mask_encaja = df_port['encaje'] == 'SI'
        if mask_encaja.any():
            df_encaja = df_port.loc[mask_encaja].copy()
            idx_original = df_encaja.index
            df_encaja = clasificar_solvencia(df_encaja, df_matriz_solv, df_categorias_solv)
            # Asegurar que el índice no cambió (el merge de solvencia puede duplicar filas)
            if len(df_encaja) != len(idx_original):
                df_encaja = df_encaja.drop_duplicates(subset=['id_posicion'], keep='first')
                df_encaja.index = idx_original[:len(df_encaja)]
            else:
                df_encaja.index = idx_original
            # Solo asignar columnas nuevas que trajo clasificar_solvencia
            cols_nuevas = [c for c in df_encaja.columns if c in ['clasificacion_solvencia', 'categoria', 'ponderacion', 'grupo_calificacion', 'codigo_solvencia']]
            for col in cols_nuevas:
                df_port.loc[mask_encaja, col] = df_encaja[col].values

        df_port.loc[~mask_encaja, 'clasificacion_solvencia'] = 'NO_COMPUTA_SOLVENCIA'
        df_port.loc[~mask_encaja, 'numeral_regimen'] = 'NO_APLICA'
        df_port.loc[~mask_encaja, 'descripcion_regimen'] = 'NO_COMPUTA_REGIMEN'

        # Los CCS (swaps) no computan solvencia
        mask_swap = df_port['clase_origen'] == 'SWAP'
        df_port.loc[mask_swap, 'clasificacion_solvencia'] = 'NO_COMPUTA_SOLVENCIA'
        df_port.loc[mask_swap, 'codigo_solvencia'] = 'NO_APLICA'

        # Swaps: si VPN negativo → no encaja y no computa régimen
        if mask_swap.any():
            swap_negativo = mask_swap & (df_port['valor_mercado'] < 0)
            swap_positivo = mask_swap & (df_port['valor_mercado'] >= 0)
            df_port.loc[swap_negativo, 'encaje'] = 'NO'
            df_port.loc[swap_negativo, 'numeral_regimen'] = 'NO_APLICA'
            df_port.loc[swap_negativo, 'descripcion_regimen'] = 'NO_COMPUTA_REGIMEN'
            df_port.loc[swap_positivo, 'encaje'] = 'SI'

        # 8.5. Asignar id_cobertura y clasificacion_contable a swaps
        print("🔗 Asignando relaciones de cobertura (id_cobertura)...")
        df_port['id_cobertura'] = None
        try:
            df_dic_swaps = pd.read_sql_query("SELECT id_posicion, Subyacente FROM param_diccionario_swaps", conn)
            df_dic_swaps['id_posicion'] = df_dic_swaps['id_posicion'].astype(str).str.strip()
            df_dic_swaps['Subyacente'] = df_dic_swaps['Subyacente'].astype(str).str.strip()

            # Swap → id_cobertura = Subyacente (puede tener pipe para múltiples)
            swap_a_sub = dict(zip(df_dic_swaps['id_posicion'], df_dic_swaps['Subyacente']))
            mask_swap_port = df_port['clase_origen'] == 'SWAP'
            df_port.loc[mask_swap_port, 'id_cobertura'] = df_port.loc[mask_swap_port, 'id_posicion'].astype(str).str.strip().map(swap_a_sub)

            # Título → id_cobertura = id del swap que lo cubre (maneja pipe)
            sub_a_swap = {}
            for _, row in df_dic_swaps.iterrows():
                for sub in str(row['Subyacente']).split('|'):
                    sub_a_swap[sub.strip()] = row['id_posicion']
            mask_titulo = df_port['clase_origen'] == 'TITULO'
            df_port.loc[mask_titulo, 'id_cobertura'] = df_port.loc[mask_titulo, 'id_posicion'].astype(str).str.strip().map(sub_a_swap)

            # Swap hereda clasificacion_contable del primer título subyacente
            if 'clasificacion_contable' in df_port.columns:
                mapeo_clasif = df_port.loc[mask_titulo, ['id_posicion', 'clasificacion_contable']].copy()
                mapeo_clasif['id_posicion'] = mapeo_clasif['id_posicion'].astype(str).str.strip()
                clasif_por_titulo = dict(zip(mapeo_clasif['id_posicion'], mapeo_clasif['clasificacion_contable']))
                # Para swaps con múltiples subyacentes, tomar el primero
                df_port.loc[mask_swap_port, 'clasificacion_contable'] = df_port.loc[mask_swap_port, 'id_cobertura'].apply(
                    lambda x: clasif_por_titulo.get(str(x).split('|')[0].strip()) if pd.notna(x) else None
                )
        except Exception as e:
            print(f"  ⚠️ No se pudo asignar id_cobertura: {e}")

        # 9. Persistir resultado (siempre recrear tabla para evitar columnas huérfanas)
        # Eliminar filas sin especie (filas fantasma de merges o residuales del 597)
        df_port = df_port[df_port['especie'].notna() & (df_port['especie'].str.strip() != '')]

        # Garantizar tipos numéricos antes de guardar
        cols_numericas = ['nominal', 'valor_mercado', 'valor_compra', 'tasa_compra',
                          'valor_tir', 'tir', 'tasa_mercado', 'precio', 'tasa_dcto', 'ponderacion']
        for col in cols_numericas:
            if col in df_port.columns:
                df_port[col] = pd.to_numeric(df_port[col], errors='coerce')

        df_port.to_sql('portafolio_clasificado', conn, if_exists='replace', index=False)
        conn.commit()

        print(f"✅ Portafolio clasificado y guardado ({len(df_port)} posiciones).")
        print("-" * 60)
        return df_port

    except Exception as e:
        import traceback
        print(f"❌ Error durante la clasificación: {e}")
        traceback.print_exc()
        return None
    finally:
        conn.close()


# =============================================================================
# 6. CLASIFICACIÓN EN MEMORIA (sin acceso a DB — para portafolio proyectado)
# =============================================================================

def clasificar_portafolio_en_memoria(df, fecha_corte):
    """
    Versión ligera de la clasificación que recalcula tramos de plazo
    y asset_class_definitivo para un DataFrame en memoria.
    Se usa para el portafolio proyectado (donde las fechas de vencimiento
    cambian relativo a la nueva fecha de corte).

    Args:
        df: DataFrame con portafolio (debe tener fecha_vencimiento, subtipo_vehiculo, etc.)
        fecha_corte: fecha de referencia (str o date/datetime)

    Returns:
        DataFrame con tramo_plazo y asset_class_definitivo actualizados.
    """
    if df.empty:
        return df

    df = df.copy()

    # Recalcular tramo de plazo
    if 'fecha_vencimiento' in df.columns and 'subtipo_vehiculo' in df.columns:
        df = calcular_tramos_plazo(df, fecha_corte)

    # Recalcular asset_class_definitivo si hay datos de la matriz
    if 'asset_class_base' in df.columns:
        try:
            conn = sqlite3.connect(DB_PATH)
            df_matriz_ac = pd.read_sql_query("SELECT * FROM param_matriz_reglas_asset_class", conn)
            conn.close()

            # Solo re-resolver los que usan la matriz (BMK_MATRIZ) y tienen datos completos
            mask_matriz = (df['asset_class_base'] == 'BMK_MATRIZ') & \
                          (df['subtipo_vehiculo'].notna()) & \
                          (df['subtipo_vehiculo'] != '') & \
                          (df['subtipo_vehiculo'] != 'Sin Clasificar')

            if mask_matriz.any():
                # Guardar asset_class anterior como fallback
                ac_anterior = df.loc[mask_matriz, 'asset_class_definitivo'].copy()

                df_sub = df.loc[mask_matriz].copy()
                df_sub['_row_id'] = range(len(df_sub))
                for col in ['tasa_referencia', 'tramo_plazo', 'sector', 'moneda']:
                    if col in df_sub.columns:
                        df_sub[col] = df_sub[col].fillna('N/A')

                df_ac_result = resolver_asset_class(
                    df_sub[['_row_id', 'subtipo_vehiculo', 'tasa_referencia', 'tramo_plazo', 'sector', 'moneda', 'asset_class_base']].copy(),
                    df_matriz_ac
                )
                nuevos_ac = pd.Series(df_ac_result['asset_class_definitivo'].values, index=ac_anterior.index)

                # Solo actualizar si la resolución dio resultado válido
                fallo = nuevos_ac == 'Falta_Regla_Matriz'
                nuevos_ac[fallo] = ac_anterior[fallo]
                df.loc[mask_matriz, 'asset_class_definitivo'] = nuevos_ac.values
        except Exception:
            pass  # Si no hay DB disponible, mantener todo como está

    return df


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fecha = sys.argv[1].strip()
    else:
        fecha = input("📅 Ingresa la fecha de corte (YYYY-MM-DD): ").strip()

    df_resultado = clasificar_portafolio_diario(fecha)
    if df_resultado is not None:
        print("\n📊 Muestra del Portafolio Clasificado:")
        cols_muestra = ['especie', 'tramo_plazo', 'asset_class_definitivo', 'calificacion', 'categoria', 'ponderacion']
        cols_muestra = [c for c in cols_muestra if c in df_resultado.columns]
        print(df_resultado[cols_muestra].head(10))
