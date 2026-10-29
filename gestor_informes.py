"""
gestor_informes.py
------------------
Módulo de gestión de informes diarios para AXIS.
Maneja: identificación, procesamiento, carga en BD, y consulta de estado
de cada informe requerido (597, PIP, Forwards, Opciones, IPC, TRM).
"""

import pandas as pd
import sqlite3
import re
import shutil
from pathlib import Path
from datetime import datetime

# --- CONFIGURACIÓN ---
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "portafolio.db"

# Regla del 6º día hábil del IPC: fuente única en comun.fuentes_mercado.
from comun.fuentes_mercado import mes_ipc_vigente

# Tipos de informe reconocidos
TIPOS_INFORME = {
    '597': {
        'patrones': [r'PFTIS597', r'PFTIT597', r'597'],
        'tabla_destino': 'hist_titulos',
        'frecuencia': 'diaria',
    },
    # PIP_INT debe ir ANTES de PIP: su patrón es más específico (Preciospip_INT...)
    # Los patrones de PIP y PIP_INT son mutuamente excluyentes (no dependen del orden):
    #   - PIP_INT matchea SOLO cuando aparece 'preciospip_int'
    #   - PIP (Cons) exige 'preciospip_cons' (o 'pip_cons'), nunca el internacional
    'PIP_INT': {
        'patrones': [r'(?i)preciospip_int'],
        'tabla_destino': 'hist_pip_int',
        'frecuencia': 'diaria',
    },
    'PIP': {
        'patrones': [r'(?i)preciospip_cons', r'(?i)^pip_cons'],
        'tabla_destino': 'hist_pip',
        'frecuencia': 'diaria',
    },
    'FWD': {
        'patrones': [r'(?i)PFCLD572', r'(?i)^forward'],
        'tabla_destino': 'hist_forwards',
        'frecuencia': 'diaria',
    },
    'OPC': {
        'patrones': [r'(?i)opcion', r'(?i)opciones'],
        'tabla_destino': 'hist_opciones',
        'frecuencia': 'diaria',
    },
    'CURV': {
        'patrones': [r'(?i)curvaspip.*forward.*localsimple'],
        'tabla_destino': None,
        'frecuencia': 'diaria',
        'archivo_patron': 'Curvaspip_Forward_LocalSimple_T+0_*.csv',
        'carpeta': '.',
    },
    'VOL': {
        'patrones': [r'(?i)usdcop_strike'],
        'tabla_destino': None,
        'frecuencia': 'diaria',
        'archivo_patron': 'usdcop_strike*.csv',
        'carpeta': '.',
    },
    'OTC': {
        'patrones': [r'(?i)curvaspip.*derivados.*otcsbol'],
        'tabla_destino': None,
        'frecuencia': 'diaria',
        'archivo_patron': 'Curvaspip_Derivados_OTCSBOL_T+0_*.csv',
        'carpeta': '.',
    },
    'IPC': {
        'patrones': [r'(?i)inflaci[oó]n', r'(?i)IPC'],
        'tabla_destino': None,
        'frecuencia': 'mensual',
    },
    'SWP': {
        'patrones': [r'(?i)PFTIT695', r'(?i)^swap'],
        'tabla_destino': 'hist_swaps',
        'frecuencia': 'diaria',
    },
}


def inicializar_tablas():
    """Crea las tablas registro_informes y hist_pip si no existen."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registro_informes (
            informe TEXT,
            fecha_datos DATE,
            fecha_carga TIMESTAMP,
            archivo_original TEXT,
            n_registros INTEGER,
            estado TEXT DEFAULT 'OK'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hist_pip (
            fecha_reporte DATE,
            nemo TEXT,
            emisor TEXT,
            sector TEXT,
            codigo_isin TEXT,
            serie TEXT,
            fecha_emision TEXT,
            fecha_vencimiento TEXT,
            cupon REAL,
            tasa_referencia TEXT,
            spread REAL,
            margen REAL,
            precio_sucio REAL,
            precio_limpio REAL,
            interes_acumulado REAL,
            dias_acumulados INTEGER,
            rendimiento REAL,
            moneda TEXT,
            valor_nominal REAL,
            frecuencia_pago_cupon TEXT,
            uso_tasa TEXT,
            calificacion_homologada TEXT,
            familia TEXT,
            metodo_calculo TEXT,
            dias_al_vencimiento INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hist_pip_int (
            fecha_reporte DATE,
            nemo TEXT,
            emisor TEXT,
            sector TEXT,
            codigo_isin TEXT,
            serie TEXT,
            fecha_emision TEXT,
            fecha_vencimiento TEXT,
            cupon REAL,
            tasa_referencia TEXT,
            spread REAL,
            margen REAL,
            precio_sucio REAL,
            precio_limpio REAL,
            interes_acumulado REAL,
            dias_acumulados INTEGER,
            rendimiento REAL,
            moneda TEXT,
            valor_nominal REAL,
            frecuencia_pago_cupon TEXT,
            uso_tasa TEXT,
            calificacion_homologada TEXT,
            familia TEXT,
            metodo_calculo TEXT,
            dias_al_vencimiento INTEGER
        )
    """)
    conn.commit()
    conn.close()


def identificar_tipo_informe(filename):
    """Identifica el tipo de informe basado en el nombre del archivo."""
    for tipo, config in TIPOS_INFORME.items():
        for patron in config['patrones']:
            if re.search(patron, filename):
                return tipo
    return None


def consultar_estado_informes(fecha_referencia):
    """
    Devuelve un dict con el estado de cada informe para la fecha dada.
    Retorna: {tipo: {'estado': 'verde'|'amarillo'|'rojo', 'fecha': date|None}}
    
    Revisa PRIMERO las tablas históricas reales (hist_titulos, hist_pip, etc.)
    y luego el registro de informes como fallback.
    """
    conn = sqlite3.connect(DB_PATH)
    # Asegurar que la tabla existe
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registro_informes (
            informe TEXT,
            fecha_datos DATE,
            fecha_carga TIMESTAMP,
            archivo_original TEXT,
            n_registros INTEGER,
            estado TEXT DEFAULT 'OK'
        )
    """)
    conn.commit()

    # Obtener tablas existentes
    tablas_existentes = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    resultado = {}
    fecha_str = str(fecha_referencia)

    # Mapeo: tipo → (tabla_historica, columna_fecha)
    tablas_directas = {
        '597': ('hist_titulos', 'fecha_reporte'),
        'PIP': ('hist_pip', 'fecha_reporte'),
        'PIP_INT': ('hist_pip_int', 'fecha_reporte'),
        'FWD': ('hist_forwards', 'fecha_reporte'),
        'OPC': ('hist_opciones', 'fecha_reporte'),
        'CURV': ('hist_curvas_derivados', 'fecha_reporte'),
        'VOL': ('hist_vol_derivados', 'fecha_reporte'),
        'OTC': ('hist_curvas_otc', 'fecha_reporte'),
        'SWP': ('hist_swaps', 'fecha_reporte'),
    }

    for tipo in TIPOS_INFORME:
        estado_final = None

        # 1. Revisar tabla histórica directamente (fuente de verdad)
        if tipo in tablas_directas:
            tabla, col_fecha = tablas_directas[tipo]
            if tabla in tablas_existentes:
                row = conn.execute(
                    f"SELECT {col_fecha} FROM {tabla} WHERE {col_fecha} = ? LIMIT 1",
                    (fecha_str,)
                ).fetchone()
                if row:
                    estado_final = {'estado': 'verde', 'fecha': fecha_str}

        # (PIP, PIP_INT, CURV, VOL y OTC: su estado se determina por las tablas de la DB
        #  en el paso 1; ya no se escriben archivos físicos, por lo que no hay fallback de archivo)

        # 4. Para IPC: archivo existe en el proyecto
        if tipo == 'IPC' and estado_final is None:
            archivo_ipc = PROJECT_ROOT / "Serie Inflación.xlsx"
            if archivo_ipc.exists():
                estado_final = {'estado': 'verde', 'fecha': 'presente'}

        # 5. Fallback: registro_informes
        if estado_final is None:
            row = conn.execute(
                "SELECT fecha_datos FROM registro_informes WHERE informe = ? AND fecha_datos = ? AND estado = 'OK' LIMIT 1",
                (tipo, fecha_str)
            ).fetchone()
            if row:
                estado_final = {'estado': 'verde', 'fecha': str(row[0])}

        # 6. Si nada → rojo
        if estado_final is None:
            estado_final = {'estado': 'rojo', 'fecha': None}

        resultado[tipo] = estado_final

    conn.close()
    return resultado


def existe_informe(tipo, fecha_datos):
    """Verifica si ya existe un registro para ese informe + fecha."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM registro_informes WHERE informe = ? AND fecha_datos = ? AND estado = 'OK' LIMIT 1",
        (tipo, str(fecha_datos))
    ).fetchone()
    conn.close()
    return row is not None


def registrar_informe(tipo, fecha_datos, archivo_original, n_registros, estado='OK'):
    """Registra un informe procesado en la tabla de control."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO registro_informes (informe, fecha_datos, fecha_carga, archivo_original, n_registros, estado) VALUES (?, ?, ?, ?, ?, ?)",
        (tipo, str(fecha_datos), datetime.now().isoformat(), archivo_original, n_registros, estado)
    )
    conn.commit()
    conn.close()


def obtener_trm_implicita(fecha_referencia):
    """
    Obtiene la TRM del 597 para la fecha seleccionada.
    Busca en hist_titulos títulos USD (cuentas, time deposits) y calcula VM/nominal.
    Retorna (trm: float|None, fecha_usada: str|None)
    """
    conn = sqlite3.connect(DB_PATH)
    fecha_str = str(fecha_referencia)

    try:
        # Buscar en hist_titulos por fecha exacta
        df = pd.read_sql_query(
            """SELECT nominal, valor_mercado, especie, fecha_reporte FROM hist_titulos 
            WHERE fecha_reporte = ? AND moneda IN ('USD', 'US$', 'U')
            AND CAST(nominal AS REAL) > 0 AND CAST(valor_mercado AS REAL) > 0
            AND (especie LIKE '%Cta%' OR especie LIKE '%TIME DEP%' 
                 OR especie LIKE '%CITIBANK%' OR especie LIKE '%MIAMI%')
            LIMIT 5""",
            conn, params=(fecha_str,)
        )

        # Si no hay para esa fecha, buscar la más cercana
        if df.empty:
            df = pd.read_sql_query(
                """SELECT nominal, valor_mercado, especie, fecha_reporte FROM hist_titulos 
                WHERE moneda IN ('USD', 'US$', 'U')
                AND CAST(nominal AS REAL) > 0 AND CAST(valor_mercado AS REAL) > 0
                AND (especie LIKE '%Cta%' OR especie LIKE '%TIME DEP%' 
                     OR especie LIKE '%CITIBANK%' OR especie LIKE '%MIAMI%')
                ORDER BY ABS(julianday(fecha_reporte) - julianday(?)) 
                LIMIT 5""",
                conn, params=(fecha_str,)
            )

        if df.empty:
            conn.close()
            return None, None

        df['nominal'] = pd.to_numeric(df['nominal'], errors='coerce')
        df['valor_mercado'] = pd.to_numeric(df['valor_mercado'], errors='coerce')
        df = df.dropna(subset=['nominal', 'valor_mercado'])
        df = df[df['nominal'] > 0]

        df['trm'] = df['valor_mercado'] / df['nominal']
        df = df[(df['trm'] > 2000) & (df['trm'] < 8000)]

        if df.empty:
            conn.close()
            return None, None

        trm = float(df['trm'].iloc[0])
        fecha_usada = str(df['fecha_reporte'].iloc[0])
        conn.close()
        return trm, fecha_usada

    except Exception:
        conn.close()
        return None, None


def obtener_ipc_vigente(fecha_referencia):
    """
    Calcula el IPC anual vigente para una fecha usando la regla del 6to día hábil.
    Lee la Serie Inflación.xlsx del proyecto.
    Retorna (ipc_pct: float|None, mes_vigente: str|None)
    """
    archivo_ipc = PROJECT_ROOT / "Serie Inflación.xlsx"
    if not archivo_ipc.exists():
        return None, None

    try:
        df_ipc = pd.read_excel(archivo_ipc)

        # Normalizar nombres de columnas
        df_ipc.columns = df_ipc.columns.str.strip().str.lower()

        # Buscar columna de fecha y de IPC
        col_fecha = None
        col_ipc = None
        for c in df_ipc.columns:
            if 'fecha' in c:
                col_fecha = c
            elif 'ipc' in c or 'inflac' in c or 'anual' in c:
                col_ipc = c

        if not col_fecha or not col_ipc:
            # Intentar por posición (primera = fecha, segunda = valor)
            col_fecha = df_ipc.columns[0]
            col_ipc = df_ipc.columns[1]

        df_ipc = df_ipc[[col_fecha, col_ipc]].dropna()
        df_ipc.columns = ['fecha', 'ipc_anual']
        df_ipc['fecha'] = pd.to_datetime(df_ipc['fecha'], errors='coerce')
        df_ipc = df_ipc.dropna(subset=['fecha'])
        df_ipc['ipc_anual'] = pd.to_numeric(df_ipc['ipc_anual'], errors='coerce')

        if isinstance(fecha_referencia, str):
            fecha_ref = pd.to_datetime(fecha_referencia).date()
        else:
            fecha_ref = fecha_referencia

        # Regla del 6to día hábil
        from datetime import timedelta
        def _sexto_habil(anio, mes):
            dia = pd.Timestamp(anio, mes, 1).date()
            habiles = 0
            while habiles < 6:
                if dia.weekday() < 5:
                    habiles += 1
                if habiles < 6:
                    dia += timedelta(days=1)
            return dia

        sexto = _sexto_habil(fecha_ref.year, fecha_ref.month)

        if fecha_ref >= sexto:
            mes_ipc = fecha_ref.month - 1
            anio_ipc = fecha_ref.year
            if mes_ipc == 0:
                mes_ipc = 12
                anio_ipc -= 1
        else:
            mes_ipc = fecha_ref.month - 2
            anio_ipc = fecha_ref.year
            if mes_ipc <= 0:
                mes_ipc += 12
                anio_ipc -= 1

        # Buscar en la serie
        fila = df_ipc[(df_ipc['fecha'].dt.month == mes_ipc) & (df_ipc['fecha'].dt.year == anio_ipc)]
        if fila.empty:
            # Fallback: último disponible
            ipc_val = float(df_ipc.iloc[-1]['ipc_anual'])
            mes_label = df_ipc.iloc[-1]['fecha'].strftime('%b %Y')
        else:
            ipc_val = float(fila.iloc[0]['ipc_anual'])
            mes_label = fila.iloc[0]['fecha'].strftime('%b %Y')

        # Si está en formato decimal (0.05) o porcentual (5.0)
        if abs(ipc_val) < 1:
            ipc_pct = ipc_val * 100
        else:
            ipc_pct = ipc_val

        return ipc_pct, mes_label

    except Exception:
        return None, None


def obtener_uvr_vigente(fecha_referencia):
    """
    Calcula la UVR vigente extrayéndola implícitamente de los TUVT del portafolio.
    Fórmula: UVR = valor_mercado / (nominal × precio / 100)
    Retorna (uvr: float|None, fecha_usada: str|None)
    """
    conn = sqlite3.connect(DB_PATH)
    fecha_str = str(fecha_referencia)
    try:
        df = pd.read_sql_query("""
            SELECT nominal, valor_mercado, precio, fecha_reporte
            FROM hist_titulos
            WHERE especie LIKE 'TUVT%'
              AND fecha_reporte = ?
              AND CAST(nominal AS REAL) > 0
              AND CAST(precio AS REAL) > 0
              AND CAST(valor_mercado AS REAL) > 0
            LIMIT 10
        """, conn, params=(fecha_str,))

        # Fallback: fecha más cercana
        if df.empty:
            df = pd.read_sql_query("""
                SELECT nominal, valor_mercado, precio, fecha_reporte
                FROM hist_titulos
                WHERE especie LIKE 'TUVT%'
                  AND CAST(nominal AS REAL) > 0
                  AND CAST(precio AS REAL) > 0
                  AND CAST(valor_mercado AS REAL) > 0
                ORDER BY ABS(julianday(fecha_reporte) - julianday(?))
                LIMIT 10
            """, conn, params=(fecha_str,))

        conn.close()
        if df.empty:
            return None, None

        df['nominal'] = pd.to_numeric(df['nominal'], errors='coerce')
        df['valor_mercado'] = pd.to_numeric(df['valor_mercado'], errors='coerce')
        df['precio'] = pd.to_numeric(df['precio'], errors='coerce')
        df = df.dropna()
        df = df[df['nominal'] > 0]

        df['uvr'] = df['valor_mercado'] / (df['nominal'] * df['precio'] / 100)
        # Filtrar valores razonables (300 < UVR < 600)
        df = df[(df['uvr'] > 300) & (df['uvr'] < 600)]

        if df.empty:
            return None, None

        uvr = round(float(df['uvr'].median()), 4)
        fecha_usada = str(df['fecha_reporte'].iloc[0])
        return uvr, fecha_usada

    except Exception:
        conn.close()
        return None, None


def _extraer_fecha_597(contenido_bytes):
    """Extrae la fecha del contenido del 597 (línea con formato YYYYMMDD en el membrete)."""
    try:
        texto = contenido_bytes.decode('latin1')
        lineas = texto.split('\n')
        for linea in lineas[:6]:
            match = re.search(r'A\s+(\d{8})', linea)
            if match:
                fecha_str = match.group(1)
                return pd.to_datetime(fecha_str, format='%Y%m%d').date()
            match2 = re.search(r'(\d{4}/\d{2}/\d{2})\s', linea)
            if match2:
                pass
    except Exception:
        pass
    return None


def _extraer_fecha_pip(filename):
    """Extrae la fecha del nombre del archivo PIP: Preciospip_..._YYYYMMDD.csv"""
    match = re.search(r'(\d{8})', filename)
    if match:
        return pd.to_datetime(match.group(1), format='%Y%m%d').date()
    return None


def _actualizar_trm_xlsx(fecha_datos):
    """
    Actualiza TRM.xlsx agregando la TRM implícita calculada del 597 para la fecha dada.
    Si el archivo no existe, lo crea con el CSV histórico base.
    Se llama automáticamente después de cada procesamiento de 597.
    """
    import openpyxl

    trm_path = PROJECT_ROOT / "TRM.xlsx"

    # Calcular TRM para esta fecha
    trm_val, _ = obtener_trm_implicita(fecha_datos)
    if trm_val is None:
        return  # No hay TRM calculable para esta fecha

    fecha_dt = pd.to_datetime(fecha_datos)

    # Cargar archivo existente o crear desde CSV base
    if trm_path.exists():
        try:
            df_trm = pd.read_excel(trm_path)
            df_trm.columns = ['fecha', 'trm']
            df_trm['fecha'] = pd.to_datetime(df_trm['fecha'])
        except Exception:
            df_trm = pd.DataFrame(columns=['fecha', 'trm'])
    else:
        # Intentar crear desde CSV histórico
        csv_path = PROJECT_ROOT / "Tasa de cambio del peso colombiano.csv"
        if csv_path.exists():
            try:
                df_trm = pd.read_csv(csv_path, sep='\t')
                df_trm.columns = ['fecha', 'trm']
                df_trm['fecha'] = pd.to_datetime(df_trm['fecha'], dayfirst=True)
                df_trm['trm'] = pd.to_numeric(df_trm['trm'], errors='coerce')
                df_trm = df_trm.dropna()
            except Exception:
                df_trm = pd.DataFrame(columns=['fecha', 'trm'])
        else:
            df_trm = pd.DataFrame(columns=['fecha', 'trm'])

    # Agregar o actualizar la fecha
    mask = df_trm['fecha'] == fecha_dt
    if mask.any():
        df_trm.loc[mask, 'trm'] = trm_val
    else:
        df_trm = pd.concat([df_trm, pd.DataFrame([{'fecha': fecha_dt, 'trm': trm_val}])], ignore_index=True)

    # Ordenar y guardar
    df_trm = df_trm.sort_values('fecha').reset_index(drop=True)
    df_trm.to_excel(trm_path, index=False)


def procesar_597(contenido_bytes, filename):
    """
    Procesa el archivo 597: parsea el CSV y delega a portfolio_loader_beta
    para la carga con todos los filtros correctos.
    Retorna (exito: bool, mensaje: str, fecha_datos: date|None)
    """
    from clasificar_portafolio import clasificar_portafolio_diario

    # 1. Detectar fecha
    fecha_datos = _extraer_fecha_597(contenido_bytes)
    if not fecha_datos:
        return False, "No se pudo detectar la fecha en el archivo 597", None

    # 2. Parsear CSV
    try:
        texto = contenido_bytes.decode('latin1')
        lineas = texto.split('\n')

        linea_titulos = 0
        for i, linea in enumerate(lineas):
            if linea.strip().startswith('Cla;'):
                linea_titulos = i
                break

        if linea_titulos == 0:
            return False, "No se encontró la fila de encabezados (Cla;...)", None

        import io
        df = pd.read_csv(io.StringIO(texto), sep=';', encoding='latin1', skiprows=linea_titulos)
        df.columns = df.columns.str.strip()
        df = df[df['Cla'] != '---'].copy()
        df = df.dropna(subset=['Especie'])

    except Exception as e:
        return False, f"Error parseando el CSV: {e}", None

    # 3. Delegar la carga a portfolio_loader_beta (tiene toda la lógica de filtros)
    try:
        from portfolio_loader_beta import cargar_titulos_desde_df
        n_registros = cargar_titulos_desde_df(df, fecha_datos)
    except Exception as e:
        return False, f"Error en portfolio_loader_beta: {e}", None

    if n_registros == 0:
        return False, f"No se cargaron registros para {fecha_datos}", None

    # 4. Ejecutar clasificación
    try:
        from diccionario_titulos import generar_diccionario_clasificado
        generar_diccionario_clasificado(str(fecha_datos))
        clasificar_portafolio_diario(str(fecha_datos))
        msg_clasif = " + Diccionario + Clasificación OK"
    except Exception as e:
        msg_clasif = f" (Clasificación falló: {e})"

    # 5. Registrar
    registrar_informe('597', fecha_datos, filename, n_registros)

    # 6. Actualizar TRM.xlsx
    _actualizar_trm_xlsx(fecha_datos)

    return True, f"597 cargado: {n_registros} títulos para {fecha_datos}{msg_clasif}", fecha_datos

    # 6. Ejecutar clasificación
    try:
        from diccionario_titulos import generar_diccionario_clasificado
        generar_diccionario_clasificado(str(fecha_datos))
        clasificar_portafolio_diario(str(fecha_datos))
        msg_clasif = " + Diccionario + Clasificación OK"
    except Exception as e:
        msg_clasif = f" (Clasificación falló: {e})"

    # 7. Registrar
    registrar_informe('597', fecha_datos, filename, n_registros)

    # 8. Actualizar TRM.xlsx con la TRM implícita de este 597
    _actualizar_trm_xlsx(fecha_datos)

    return True, f"597 cargado: {n_registros} títulos para {fecha_datos}{msg_clasif}", fecha_datos


def procesar_pip(contenido_bytes, filename):
    """
    Procesa archivo PIP: guarda en hist_pip y copia el archivo.
    Retorna (exito, mensaje, fecha_datos)
    """
    fecha_datos = _extraer_fecha_pip(filename)
    if not fecha_datos:
        return False, "No se pudo detectar la fecha en el nombre del archivo PIP", None

    try:
        import io
        texto = contenido_bytes.decode('latin1')
        
        # El PIP de BVC tiene coma trailing en cada línea de datos,
        # lo que desplaza las columnas. Removemos la coma final.
        lineas = texto.split('\n')
        lineas_limpias = [linea.rstrip(',').rstrip(',\r') for linea in lineas]
        texto_limpio = '\n'.join(lineas_limpias)
        
        df = pd.read_csv(io.StringIO(texto_limpio), encoding='latin1', low_memory=False)
        df.columns = df.columns.str.strip()

        # Seleccionar columnas relevantes para hist_pip
        cols_pip = {
            'FECHA': 'fecha_reporte', 'NEMO': 'nemo', 'EMISOR': 'emisor',
            'SECTOR': 'sector', 'CODIGO_ISIN': 'codigo_isin', 'SERIE': 'serie',
            'FECHA_EMISION': 'fecha_emision', 'FECHA_VENCIMIENTO': 'fecha_vencimiento',
            'CUPON': 'cupon', 'TASA_REFERENCIA': 'tasa_referencia',
            'SPREAD': 'spread', 'MARGEN': 'margen',
            'PRECIO_SUCIO': 'precio_sucio', 'PRECIO_LIMPIO': 'precio_limpio',
            'INTERES_ACUMULADO': 'interes_acumulado',
            'DIAS_ACUMULADOS': 'dias_acumulados', 'RENDIMIENTO': 'rendimiento',
            'MONEDA': 'moneda', 'VALOR_NOMINAL': 'valor_nominal',
            'FRECUENCIA_PAGO_CUPON': 'frecuencia_pago_cupon',
            'USO_TASA': 'uso_tasa', 'CALIFICACION_HOMOLOGADA': 'calificacion_homologada',
            'FAMILIA': 'familia', 'METODO_DE_CALCULO': 'metodo_calculo',
            'DIAS_AL_VENCIMIENTO': 'dias_al_vencimiento',
        }
        existing_cols = {k: v for k, v in cols_pip.items() if k in df.columns}
        df_pip = df[list(existing_cols.keys())].rename(columns=existing_cols)

        # Convertir fecha al formato estándar (string YYYY-MM-DD para SQLite)
        if 'fecha_reporte' in df_pip.columns:
            df_pip['fecha_reporte'] = pd.to_datetime(
                df_pip['fecha_reporte'], format='%Y%m%d', errors='coerce'
            ).dt.strftime('%Y-%m-%d')

        n_registros = len(df_pip)

        # Guardar en hist_pip
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM hist_pip WHERE fecha_reporte = ?", (str(fecha_datos),))
        conn.commit()
        df_pip.to_sql('hist_pip', conn, if_exists='append', index=False)
        conn.close()

        # No se escribe archivo físico: los consumidores leen de hist_pip por fecha_reporte

        registrar_informe('PIP', fecha_datos, filename, n_registros)
        return True, f"PIP cargado: {n_registros} registros para {fecha_datos}", fecha_datos

    except Exception as e:
        return False, f"Error procesando PIP: {e}", None


def procesar_pip_int(contenido_bytes, filename):
    """
    Procesa archivo PIP Internacional (Preciospip_INT_SBOL): guarda en hist_pip_int.
    Tabla separada del PIP Cons (hist_pip) para no mezclar las curvas locales con los
    rendimientos internacionales. motor_proyeccion lee el spread por ISIN desde aquí.
    Retorna (exito, mensaje, fecha_datos)
    """
    fecha_datos = _extraer_fecha_pip(filename)
    if not fecha_datos:
        return False, "No se pudo detectar la fecha en el nombre del archivo PIP INT", None

    try:
        import io
        texto = contenido_bytes.decode('latin1')
        # El PIP de BVC tiene coma trailing en cada línea de datos; removerla
        lineas = texto.split('\n')
        lineas_limpias = [linea.rstrip(',').rstrip(',\r') for linea in lineas]
        texto_limpio = '\n'.join(lineas_limpias)

        df = pd.read_csv(io.StringIO(texto_limpio), encoding='latin1', low_memory=False)
        df.columns = df.columns.str.strip()

        # Mismo subconjunto de columnas que el PIP Cons (misma estructura de archivo)
        cols_pip = {
            'FECHA': 'fecha_reporte', 'NEMO': 'nemo', 'EMISOR': 'emisor',
            'SECTOR': 'sector', 'CODIGO_ISIN': 'codigo_isin', 'SERIE': 'serie',
            'FECHA_EMISION': 'fecha_emision', 'FECHA_VENCIMIENTO': 'fecha_vencimiento',
            'CUPON': 'cupon', 'TASA_REFERENCIA': 'tasa_referencia',
            'SPREAD': 'spread', 'MARGEN': 'margen',
            'PRECIO_SUCIO': 'precio_sucio', 'PRECIO_LIMPIO': 'precio_limpio',
            'INTERES_ACUMULADO': 'interes_acumulado',
            'DIAS_ACUMULADOS': 'dias_acumulados', 'RENDIMIENTO': 'rendimiento',
            'MONEDA': 'moneda', 'VALOR_NOMINAL': 'valor_nominal',
            'FRECUENCIA_PAGO_CUPON': 'frecuencia_pago_cupon',
            'USO_TASA': 'uso_tasa', 'CALIFICACION_HOMOLOGADA': 'calificacion_homologada',
            'FAMILIA': 'familia', 'METODO_DE_CALCULO': 'metodo_calculo',
            'DIAS_AL_VENCIMIENTO': 'dias_al_vencimiento',
        }
        existing_cols = {k: v for k, v in cols_pip.items() if k in df.columns}
        df_pip = df[list(existing_cols.keys())].rename(columns=existing_cols)

        if 'fecha_reporte' in df_pip.columns:
            df_pip['fecha_reporte'] = pd.to_datetime(
                df_pip['fecha_reporte'], format='%Y%m%d', errors='coerce'
            ).dt.strftime('%Y-%m-%d')

        n_registros = len(df_pip)

        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM hist_pip_int WHERE fecha_reporte = ?", (str(fecha_datos),))
        conn.commit()
        df_pip.to_sql('hist_pip_int', conn, if_exists='append', index=False)
        conn.close()

        # No se escribe archivo físico: motor_proyeccion lee de hist_pip_int

        registrar_informe('PIP_INT', fecha_datos, filename, n_registros)
        return True, f"PIP INT cargado: {n_registros} registros para {fecha_datos}", fecha_datos

    except Exception as e:
        return False, f"Error procesando PIP INT: {e}", None


def procesar_forward(contenido_bytes, filename):
    """Procesa archivo de Forwards (PFCLD572) — parsea CSV y carga en hist_forwards."""
    import io
    from libro_de_funciones import limpiar_forwards

    try:
        texto = contenido_bytes.decode('latin1')
        lineas = texto.split('\n')

        # Detectar fecha: buscar YYYYMMDD en las primeras 6 líneas (membrete entre ====)
        fecha_datos = None
        for linea in lineas[:6]:
            match = re.search(r'A\s+(\d{8})', linea)
            if match:
                fecha_datos = pd.to_datetime(match.group(1), format='%Y%m%d').date()
                break
            match2 = re.search(r'(20\d{6})', linea)
            if match2:
                try:
                    fecha_datos = pd.to_datetime(match2.group(1), format='%Y%m%d').date()
                    break
                except Exception:
                    pass
        # Fallback: extraer del nombre del archivo
        if not fecha_datos:
            match3 = re.search(r'(\d{8})', filename)
            fecha_datos = pd.to_datetime(match3.group(1), format='%Y%m%d').date() if match3 else datetime.now().date()

        fecha_str = str(fecha_datos)

        # Parsear CSV: buscar fila de encabezados (primera que contenga ';' con texto significativo)
        linea_header = 0
        for i, linea in enumerate(lineas[:10]):
            if ';' in linea and not linea.strip().startswith('=') and len(linea.strip()) > 20:
                # Verificar que no sea la línea de guiones
                if '---' not in linea:
                    linea_header = i
                    break

        df_crudo = pd.read_csv(io.StringIO(texto), sep=';', encoding='latin-1',
                               skiprows=linea_header)
        # Eliminar fila de guiones si existe
        if not df_crudo.empty and df_crudo.iloc[0].astype(str).str.contains('---').any():
            df_crudo = df_crudo.iloc[1:].reset_index(drop=True)

        # Cargar mapeos desde DB
        conn = sqlite3.connect(DB_PATH)
        try:
            df_mapeo_columnas = pd.read_sql_query("SELECT * FROM param_diccionario_columnas", conn)
        except Exception:
            df_mapeo_columnas = pd.DataFrame()
        try:
            df_mapeo_monedas = pd.read_sql_query("SELECT * FROM param_monedas", conn)
        except Exception:
            df_mapeo_monedas = pd.DataFrame()

        df_limpio = limpiar_forwards(df_crudo, df_mapeo_columnas, df_mapeo_monedas)
        df_limpio['fecha_reporte'] = fecha_str

        # Eliminar datos previos de esta fecha
        conn.execute("DELETE FROM hist_forwards WHERE fecha_reporte = ?", (fecha_str,))
        conn.commit()

        df_limpio.to_sql('hist_forwards', conn, if_exists='append', index=False)
        conn.close()

        n = len(df_limpio)
        registrar_informe('FWD', fecha_datos, filename, n)
        return True, f"✅ {n} forwards cargados para {fecha_datos}", fecha_datos

    except Exception as e:
        # Fallback: solo registrar existencia
        import traceback
        print(f"[GESTOR] Error FWD: {e}\n{traceback.format_exc()[-400:]}")
        try:
            registrar_informe('FWD', datetime.now().date(), filename, 0)
        except Exception:
            pass
        return False, f"Error procesando Forward: {e}", None


def procesar_opciones(contenido_bytes, filename):
    """Procesa archivo de Opciones — parsea Excel y carga en hist_opciones."""
    import io
    from libro_de_funciones import limpiar_opciones

    try:
        # Detectar fecha del nombre
        fecha_datos = datetime.now().date()
        match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
        if match:
            fecha_datos = pd.to_datetime(match.group(1)).date()
        else:
            match2 = re.search(r'(\d{8})', filename)
            if match2:
                fecha_datos = pd.to_datetime(match2.group(1), format='%Y%m%d').date()

        fecha_str = str(fecha_datos)

        # Parsear: hoja "Informe Valoración Opciones", header en fila 8 (index 7)
        try:
            df_crudo = pd.read_excel(io.BytesIO(contenido_bytes),
                                     sheet_name="Informe Valoración Opciones", header=7)
        except Exception:
            try:
                # Fallback: primera hoja con header en fila 7
                df_crudo = pd.read_excel(io.BytesIO(contenido_bytes), header=7)
            except Exception:
                try:
                    df_crudo = pd.read_excel(io.BytesIO(contenido_bytes), header=0)
                except Exception:
                    texto = contenido_bytes.decode('latin1', errors='replace')
                    df_crudo = pd.read_csv(io.StringIO(texto), sep=';', encoding='latin-1')

        # Cargar mapeos desde DB
        conn = sqlite3.connect(DB_PATH)
        try:
            df_mapeo_columnas = pd.read_sql_query("SELECT * FROM param_diccionario_columnas", conn)
        except Exception:
            df_mapeo_columnas = pd.DataFrame()
        try:
            df_mapeo_monedas = pd.read_sql_query("SELECT * FROM param_monedas", conn)
        except Exception:
            df_mapeo_monedas = pd.DataFrame()

        df_limpio = limpiar_opciones(df_crudo, df_mapeo_columnas, df_mapeo_monedas)
        df_limpio['fecha_reporte'] = fecha_str

        # Eliminar datos previos de esta fecha
        conn.execute("DELETE FROM hist_opciones WHERE fecha_reporte = ?", (fecha_str,))
        conn.commit()

        df_limpio.to_sql('hist_opciones', conn, if_exists='append', index=False)
        conn.close()

        n = len(df_limpio)
        registrar_informe('OPC', fecha_datos, filename, n)
        return True, f"✅ {n} opciones cargadas para {fecha_datos}", fecha_datos

    except Exception as e:
        # Fallback: solo registrar existencia
        import traceback
        print(f"[GESTOR] Error OPC: {e}\n{traceback.format_exc()[-400:]}")
        try:
            registrar_informe('OPC', datetime.now().date(), filename, 0)
        except Exception:
            pass
        return False, f"Error procesando Opciones: {e}", None


def procesar_swaps(contenido_bytes, filename):
    """Procesa archivo de Swaps (PFTIT695) — parsea CSV y carga en hist_swaps."""
    import io
    from libro_de_funciones import limpiar_swaps

    try:
        texto = contenido_bytes.decode('latin1')
        lineas = texto.split('\n')

        # Detectar fecha: buscar YYYYMMDD en las primeras 6 líneas (membrete entre ====)
        fecha_datos = None
        for linea in lineas[:6]:
            match = re.search(r'A\s+(\d{8})', linea)
            if match:
                fecha_datos = pd.to_datetime(match.group(1), format='%Y%m%d').date()
                break
            match2 = re.search(r'(20\d{6})', linea)
            if match2:
                try:
                    fecha_datos = pd.to_datetime(match2.group(1), format='%Y%m%d').date()
                    break
                except Exception:
                    pass
        if not fecha_datos:
            match3 = re.search(r'(\d{8})', filename)
            fecha_datos = pd.to_datetime(match3.group(1), format='%Y%m%d').date() if match3 else datetime.now().date()

        fecha_str = str(fecha_datos)

        # Parsear CSV: buscar fila de encabezados
        linea_header = 0
        for i, linea in enumerate(lineas[:10]):
            if ';' in linea and not linea.strip().startswith('=') and len(linea.strip()) > 20:
                if '---' not in linea:
                    linea_header = i
                    break

        df_crudo = pd.read_csv(io.StringIO(texto), sep=';', encoding='latin-1',
                               skiprows=linea_header)
        if not df_crudo.empty and df_crudo.iloc[0].astype(str).str.contains('---').any():
            df_crudo = df_crudo.iloc[1:].reset_index(drop=True)

        # Cargar mapeos desde DB
        conn = sqlite3.connect(DB_PATH)
        try:
            df_mapeo_columnas = pd.read_sql_query("SELECT * FROM param_diccionario_columnas", conn)
        except Exception:
            df_mapeo_columnas = pd.DataFrame()
        try:
            df_mapeo_monedas = pd.read_sql_query("SELECT * FROM param_monedas", conn)
        except Exception:
            df_mapeo_monedas = pd.DataFrame()

        df_limpio = limpiar_swaps(df_crudo, df_mapeo_columnas, df_mapeo_monedas)
        df_limpio['fecha_reporte'] = fecha_str

        # Eliminar datos previos de esta fecha
        conn.execute("DELETE FROM hist_swaps WHERE fecha_reporte = ?", (fecha_str,))
        conn.commit()

        df_limpio.to_sql('hist_swaps', conn, if_exists='append', index=False)
        conn.close()

        n = len(df_limpio)
        registrar_informe('SWP', fecha_datos, filename, n)
        return True, f"✅ {n} swaps cargados para {fecha_datos}", fecha_datos

    except Exception as e:
        import traceback
        print(f"[GESTOR] Error SWP: {e}\n{traceback.format_exc()[-400:]}")
        try:
            registrar_informe('SWP', datetime.now().date(), filename, 0)
        except Exception:
            pass
        return False, f"Error procesando Swaps: {e}", None


def procesar_ipc(contenido_bytes, filename):
    """Procesa archivo Serie Inflación — copia y registra."""
    try:
        dest = PROJECT_ROOT / "Serie Inflación.xlsx"
        dest.write_bytes(contenido_bytes)
        fecha_datos = datetime.now().date()
        registrar_informe('IPC', fecha_datos, filename, 0)
        return True, f"IPC actualizado ({filename})", fecha_datos

    except Exception as e:
        return False, f"Error procesando IPC: {e}", None


def procesar_curvas_derivados(contenido_bytes, filename):
    """
    Procesa archivo Curvaspip_Forward_LocalSimple_T+0_{YYYYMMDD}.csv
    Guarda en hist_curvas_derivados y copia a la raíz del proyecto.
    """
    import io

    # Extraer fecha del nombre
    match = re.search(r'(\d{8})', filename)
    if not match:
        return False, f"No se encontró fecha en el nombre: {filename}", None

    fecha_yyyymmdd = match.group(1)
    fecha_datos = pd.to_datetime(fecha_yyyymmdd, format='%Y%m%d').date()
    fecha_str = str(fecha_datos)

    try:
        # Parsear CSV
        texto = contenido_bytes.decode('utf-8', errors='replace')
        df = pd.read_csv(io.StringIO(texto))
        df['fecha_reporte'] = fecha_str

        # Guardar en DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hist_curvas_derivados (
                fecha_reporte DATE,
                Nombre_Indicador TEXT,
                Plazo_en_Dias REAL,
                Valor REAL
            )
        """)
        conn.execute("DELETE FROM hist_curvas_derivados WHERE fecha_reporte = ?", (fecha_str,))
        conn.commit()

        # Solo guardar columnas relevantes
        cols = ['fecha_reporte']
        for c in ['Nombre_Indicador', 'Plazo_en_Dias', 'Valor']:
            if c in df.columns:
                cols.append(c)
        df[cols].to_sql('hist_curvas_derivados', conn, if_exists='append', index=False)
        conn.close()

        # (Ya no se escribe el archivo físico: valoracion_derivados lee de hist_curvas_derivados)

        n = len(df)
        registrar_informe('CURV', fecha_datos, filename, n)
        return True, f"Curvas Forward: {n} puntos para {fecha_datos}", fecha_datos

    except Exception as e:
        return False, f"Error procesando curvas Forward: {e}", None


def procesar_vol_derivados(contenido_bytes, filename):
    """
    Procesa archivo usdcop_strike{YYYYMMDD}.csv
    Guarda en hist_vol_derivados y copia a la raíz del proyecto.
    """
    import io

    # Extraer fecha del nombre
    match = re.search(r'(\d{8})', filename)
    if not match:
        return False, f"No se encontró fecha en el nombre: {filename}", None

    fecha_yyyymmdd = match.group(1)
    fecha_datos = pd.to_datetime(fecha_yyyymmdd, format='%Y%m%d').date()
    fecha_str = str(fecha_datos)

    try:
        # Parsear CSV (separador coma — este archivo usa , como delimitador)
        texto = contenido_bytes.decode('utf-8', errors='replace')
        df = pd.read_csv(io.StringIO(texto))

        # Agregar fecha_reporte como primera columna
        df.insert(0, 'fecha_reporte', fecha_str)

        # Guardar en DB como tabla normalizada
        conn = sqlite3.connect(DB_PATH)
        # Eliminar datos previos de esta fecha (si la tabla existe)
        try:
            conn.execute("DELETE FROM hist_vol_derivados WHERE fecha_reporte = ?", (fecha_str,))
            conn.commit()
        except Exception:
            pass  # Tabla no existe aún, se creará con to_sql
        df.to_sql('hist_vol_derivados', conn, if_exists='append', index=False)
        conn.close()

        # (Ya no se escribe el archivo físico: valoracion_derivados lee de hist_vol_derivados)

        registrar_informe('VOL', fecha_datos, filename, len(df))
        return True, f"✅ Volatilidad USDCOP: {len(df)} filas para {fecha_datos}", fecha_datos

    except Exception as e:
        return False, f"Error procesando volatilidad: {e}", None


def procesar_curvas_otc(contenido_bytes, filename):
    """
    Procesa archivo Curvaspip_Derivados_OTCSBOL_T+0_{YYYYMMDD}.csv
    Guarda en hist_curvas_otc y copia a la raíz del proyecto.
    Curvas: SWTIBR, UVTIBR, LUTIBR, TONIBR
    """
    import io

    # Extraer fecha del nombre
    match = re.search(r'(\d{8})', filename)
    if not match:
        return False, f"No se encontró fecha en el nombre: {filename}", None

    fecha_yyyymmdd = match.group(1)
    fecha_datos = pd.to_datetime(fecha_yyyymmdd, format='%Y%m%d').date()
    fecha_str = str(fecha_datos)

    try:
        # Parsear CSV
        texto = contenido_bytes.decode('utf-8', errors='replace')
        df = pd.read_csv(io.StringIO(texto))
        df['fecha_reporte'] = fecha_str

        # Guardar en DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hist_curvas_otc (
                fecha_reporte DATE,
                Curva TEXT,
                Plazo_en_Dias INTEGER,
                Valor REAL
            )
        """)
        conn.execute("DELETE FROM hist_curvas_otc WHERE fecha_reporte = ?", (fecha_str,))
        conn.commit()

        # Solo guardar columnas relevantes
        cols = ['fecha_reporte']
        for c in ['Curva', 'Plazo_en_Dias', 'Valor']:
            if c in df.columns:
                cols.append(c)
        df[cols].to_sql('hist_curvas_otc', conn, if_exists='append', index=False)
        conn.close()

        # (Ya no se escribe el archivo físico: valoracion_derivados lee de hist_curvas_otc)

        n = len(df)
        curvas_unicas = df['Curva'].nunique() if 'Curva' in df.columns else 0
        registrar_informe('OTC', fecha_datos, filename, n)
        return True, f"Curvas OTC: {curvas_unicas} curvas, {n} puntos para {fecha_datos}", fecha_datos

    except Exception as e:
        return False, f"Error procesando curvas OTC: {e}", None


def procesar_archivo(contenido_bytes, filename):
    """
    Punto de entrada principal: identifica el tipo y lo procesa.
    Retorna (exito, mensaje, tipo, fecha_datos)
    """
    tipo = identificar_tipo_informe(filename)
    if not tipo:
        return False, f"No se reconoce el tipo de archivo: {filename}", None, None

    procesadores = {
        '597': procesar_597,
        'PIP': procesar_pip,
        'PIP_INT': procesar_pip_int,
        'FWD': procesar_forward,
        'OPC': procesar_opciones,
        'SWP': procesar_swaps,
        'CURV': procesar_curvas_derivados,
        'VOL': procesar_vol_derivados,
        'OTC': procesar_curvas_otc,
        'IPC': procesar_ipc,
    }

    func = procesadores[tipo]
    exito, mensaje, fecha_datos = func(contenido_bytes, filename)
    return exito, mensaje, tipo, fecha_datos


# Inicializar tablas al importar
inicializar_tablas()

