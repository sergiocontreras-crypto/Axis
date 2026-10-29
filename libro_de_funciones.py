import functools
from itertools import zip_longest

import numpy as np
import pandas as pd
from scipy.stats import norm

# ---------------------------------------------------------------------------
# 1. HERRAMIENTAS GENERALES (Se ponen arriba por orden lógico)
# ---------------------------------------------------------------------------

def limpiar_texto(df: pd.DataFrame) -> pd.DataFrame:
    """Elimina espacios al inicio y al final en todas las columnas de texto."""
    cols_texto = df.select_dtypes(include=["object"]).columns
    df[cols_texto] = df[cols_texto].apply(
        lambda col: col.astype(str).str.strip().where(col.notna(), other=None)
    )
    return df

def estandarizar_monedas(df, columna_moneda, df_mapeo_monedas):
    """Usa el diccionario para dejar todo en ISO 4217 (USD, EUR, etc)"""
    # Si no hay mapeo disponible, no hacer nada
    if df_mapeo_monedas is None or df_mapeo_monedas.empty or 'valor_original' not in df_mapeo_monedas.columns:
        return df
    # Creamos el diccionario de traducción
    diccionario_monedas = dict(zip(df_mapeo_monedas['valor_original'], df_mapeo_monedas['valor_estandar']))
    
    # Reemplazamos los valores
    if columna_moneda in df.columns:
        df[columna_moneda] = df[columna_moneda].replace(diccionario_monedas)
    return df


def parsear_facial(serie_facial: pd.Series) -> pd.DataFrame:
    """
    Parsea la columna 'facial' de hist_titulos y extrae:
      - tasa_referencia_parsed: IPC, IBR, FS (fija), etc.
      - tasa_facial_parsed: spread si es indexado, tasa si es fija
      - uso_tasa_parsed: PREVIA (I), ACTUAL (F), None para tasa fija
      - tipo_tasa_parsed: EFECTIVO, NOMINAL

    Patrones:
      Indexados: "IPC+4.94F", "IB1+4.65I", "IB3+3.1I"
      Tasa fija: "7.8200-Efectiv", "5.8750000000-No"
      Sin tasa:  "No aplica", None, ""
    """
    import re

    resultados = []

    for valor in serie_facial.fillna(''):
        valor = str(valor).strip()

        # Sin tasa
        if valor == '' or valor.lower() == 'no aplica' or valor == 'None':
            resultados.append({
                'tasa_referencia_parsed': None,
                'tasa_facial_parsed': None,
                'uso_tasa_parsed': None,
                'tipo_tasa_parsed': None,
            })
            continue

        # Patrón indexado: INDICADOR + SPREAD + USO_TASA (I o F)
        match_idx = re.match(r'^([A-Za-z]+\d*)\+([0-9.]+)([IF])$', valor)
        if match_idx:
            indicador_raw = match_idx.group(1).upper()
            spread = float(match_idx.group(2))
            uso_raw = match_idx.group(3)

            # IB1, IB3, IB6 → IBR
            if indicador_raw.startswith('IB'):
                tasa_ref = 'IBR'
            else:
                tasa_ref = indicador_raw

            uso_tasa = 'ACTUAL' if uso_raw == 'F' else 'PREVIA'

            # IPC siempre es EFECTIVO
            tipo_tasa = 'EFECTIVO' if tasa_ref == 'IPC' else 'NOMINAL'

            resultados.append({
                'tasa_referencia_parsed': tasa_ref,
                'tasa_facial_parsed': spread,
                'uso_tasa_parsed': uso_tasa,
                'tipo_tasa_parsed': tipo_tasa,
            })
            continue

        # Patrón tasa fija: NUMERO-TIPO (Efectiv o No)
        match_fija = re.match(r'^([0-9.]+)\-(.*)$', valor)
        if match_fija:
            tasa_val = float(match_fija.group(1)) if match_fija.group(1) else 0.0
            tipo_raw = match_fija.group(2).strip()

            if tipo_raw.lower().startswith(('efectiv', 'efec', 'efe')):
                tipo_tasa = 'EFECTIVO'
            elif tipo_raw.lower().startswith(('nom', 'no', 'n')):
                tipo_tasa = 'NOMINAL'
            else:
                tipo_tasa = 'NOMINAL'

            resultados.append({
                'tasa_referencia_parsed': 'FS',
                'tasa_facial_parsed': tasa_val,
                'uso_tasa_parsed': 'ACTUAL',
                'tipo_tasa_parsed': tipo_tasa,
            })
            continue

        # No reconocido
        resultados.append({
            'tasa_referencia_parsed': None,
            'tasa_facial_parsed': None,
            'uso_tasa_parsed': None,
            'tipo_tasa_parsed': None,
        })

    return pd.DataFrame(resultados, index=serie_facial.index)


def estandarizar_columnas(df_crudo, tipo_archivo, df_mapeo_columnas):
    """
    Renombra las columnas usando el diccionario maestro CSV y
    limpia automáticamente todos los textos del DataFrame de espacios invisibles.
    """
    # 1. Limpiar los nombres de las columnas del archivo crudo
    df_crudo.columns = df_crudo.columns.str.strip()
    
    # 2. Extraer reglas del diccionario (si existe y tiene datos)
    if df_mapeo_columnas is not None and not df_mapeo_columnas.empty and 'archivo_origen' in df_mapeo_columnas.columns:
        reglas = df_mapeo_columnas[df_mapeo_columnas['archivo_origen'] == tipo_archivo].copy()
        reglas['nombre_original'] = reglas['nombre_original'].str.strip()
        reglas['nombre_estandarizado'] = reglas['nombre_estandarizado'].str.strip()
        
        # 3. Renombrar
        diccionario_renombre = dict(zip(reglas['nombre_original'], reglas['nombre_estandarizado']))
        df_estandarizado = df_crudo.rename(columns=diccionario_renombre)
    else:
        # Sin mapeo disponible: usar columnas tal como vienen
        df_estandarizado = df_crudo.copy()
    
    # 4. APLICAMOS LIMPIEZA GLOBAL DE TEXTOS A TODO EL DATAFRAME AQUÍ
    df_estandarizado = limpiar_texto(df_estandarizado)
    
    return df_estandarizado


# ---------------------------------------------------------------------------
# 2. FUNCIONES DE LIMPIEZA ESPECÍFICAS POR ACTIVO
# ---------------------------------------------------------------------------

def limpiar_titulos(df_crudo, df_mapeo_columnas, df_mapeo_monedas):
    """Toma el 1.597 crudo, estandariza columnas y monedas, y pasa el filtro de la aduana SQL."""
    # Al llamar estandarizar_columnas, ya se ejecuta limpiar_texto por debajo
    df = estandarizar_columnas(df_crudo, 'titulos', df_mapeo_columnas)
    df = estandarizar_monedas(df, 'moneda', df_mapeo_monedas)

    # Convertir fechas de formato yyyymmdd a datetime DESPUÉS de limpiar texto
    columnas_fecha = ['fecha_emision', 'fecha_vencimiento', 'fecha_constitucion', 'fecha_garantia']
    for col_fecha in columnas_fecha:
        if col_fecha in df.columns:
            df[col_fecha] = pd.to_datetime(df[col_fecha], format="%Y%m%d", errors="coerce")

    # Estandarizar periodicidad
    if 'periodicidad' in df.columns:
        mapeo_periodicidad = {
            'TV': 'TRIMESTRAL', 'TA': 'TRIMESTRAL',
            'AV': 'ANUAL',
            'MV': 'MENSUAL',
            'SV': 'SEMESTRAL',
            'PV': 'PERIÓDICA',
        }
        df['periodicidad'] = df['periodicidad'].map(mapeo_periodicidad)

    columnas_sql = [
        'fecha_reporte', 'id_posicion', 'clasificacion_contable', 'especie', 'emisor',
        'isin', 'calificacion', 'fecha_emision', 'fecha_vencimiento', 'nominal',
        'facial', 'periodicidad', 'fecha_constitucion', 'moneda', 'valor_compra',
        'tasa_compra', 'valor_tir', 'tir', 'valor_mercado', 'tasa_mercado',
        'precio', 'tasa_dcto', 'estado', 'fecha_garantia', 'portafolio', 'nemotecnico'
    ]
    columnas_finales = [col for col in columnas_sql if col in df.columns]

    # Convertir columnas numéricas que pueden venir como texto
    columnas_numericas = ['nominal', 'valor_compra', 'tasa_compra', 'valor_tir', 'tir',
                          'valor_mercado', 'tasa_mercado', 'precio', 'tasa_dcto']
    for col in columnas_numericas:
        if col in df.columns:
            # Quitar comas de miles (formato "1,234,567.89" → "1234567.89")
            df[col] = df[col].astype(str).str.replace(',', '', regex=False).str.strip()
            df[col] = df[col].replace(['None', 'nan', 'NaN', ''], pd.NA)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df[columnas_finales]

def limpiar_forwards(df_crudo, df_mapeo_columnas, df_mapeo_monedas):
    """Toma el DataFrame crudo de Forwards, estandariza columnas, monedas y fabrica llaves."""
    
    df = estandarizar_columnas(df_crudo, 'forwards', df_mapeo_columnas)
    
    df = estandarizar_monedas(df, 'moneda_base', df_mapeo_monedas)
    df = estandarizar_monedas(df, 'moneda_cotizacion', df_mapeo_monedas)
    
    # Convertir fechas
    columnas_fecha = ['fecha_constitucion', 'fecha_vencimiento', 'fecha_cumplimiento']
    for col in columnas_fecha:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # Reglas Específicas
    df['tipo_liquidacion'] = np.where(df['tipo'].str.contains('ND'), 'Non delivery', 'Delivery')
    df['tipo'] = df['tipo'].str.replace('.*Fw.*', 'FWD', regex=True)
    
    df['especie'] = 'FWD_' + df['moneda_base'].astype(str) + '_' + df['moneda_cotizacion'].astype(str)
    df['subyacente'] = df['moneda_base'].astype(str) + '/' + df['moneda_cotizacion'].astype(str)
    
    # Convertir columnas numéricas (quitar comas de formato de miles)
    columnas_numericas = ['nominal', 'nominal_usd', 'strike', 'valor_derecho',
                          'valor_obligacion', 'tasa_conversion', 'valor_mercado', 'pyg_dia']
    for col in columnas_numericas:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    columnas_sql = [
        'fecha_reporte', 'id_posicion', 'especie', 'tipo', 'sentido', 'emisor',
        'fecha_constitucion', 'fecha_vencimiento', 'fecha_cumplimiento',
        'moneda_base', 'nominal', 'moneda_cotizacion', 'strike', 'nominal_usd',
        'valor_derecho', 'valor_obligacion', 'tasa_conversion', 'valor_mercado',
        'pyg_dia', 'portafolio', 'estado', 'objetivo', 'subyacente', 'tipo_liquidacion'
    ]
    columnas_finales = [col for col in columnas_sql if col in df.columns]
    
    return df[columnas_finales]

# ¡ARREGLADO!: Agregado df_mapeo_monedas a los parámetros
def limpiar_opciones(df_crudo, df_mapeo_columnas, df_mapeo_monedas):
    """Toma el DataFrame crudo de Opciones, estandariza columnas, monedas y fabrica llaves."""
    
    df = estandarizar_columnas(df_crudo, 'opciones', df_mapeo_columnas)
    df = estandarizar_monedas(df, 'moneda', df_mapeo_monedas)
    
    # Reglas Específicas
    if 'subyacente' not in df.columns:
        # Intentar detectar columna por nombres alternativos
        for alt in ['Subyacente', 'SUBYACENTE', 'Underlying', 'underlying']:
            if alt in df.columns:
                df = df.rename(columns={alt: 'subyacente'})
                break
        else:
            df['subyacente'] = 'USDCOP'  # Default si no existe
    df['subyacente'] = df['subyacente'].replace({'USD': 'USDCOP', 'EUR': 'EURCOP'})
    df['especie'] = 'OPCION_' + df['subyacente'].astype(str)
    
    # Convertir columnas numéricas (quitar comas de formato de miles)
    columnas_numericas = ['nominal', 'nominal_usd', 'strike', 'spot_negociado',
                          'devaluacion_negociada', 'volatilidad_negociada', 'prima_cop',
                          'tasa_local', 'tasa_foranea', 'trm', 'volatilidad_valoracion',
                          'valor_mercado', 'pyg_dia', 'delta', 'gamma', 'theta', 'vega',
                          'rho_local', 'rho_foranea']
    for col in columnas_numericas:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    columnas_sql = [
        'fecha_reporte', 'id_posicion', 'especie', 'fecha_constitucion', 'portafolio',
        'sentido', 'tipo', 'emisor', 'nominal', 'subyacente', 'spot_negociado',
        'devaluacion_negociada', 'volatilidad_negociada', 'strike', 'moneda',
        'fecha_vencimiento', 'fecha_cumplimiento', 'prima_cop', 'tipo_liquidacion',
        'tipo_contraparte', 'observaciones', 'tasa_local', 'tasa_foranea', 'trm',
        'nominal_usd', 'volatilidad_valoracion', 'valor_mercado', 'pyg_dia',
        'delta', 'gamma', 'theta', 'vega', 'rho_local', 'rho_foranea', 'estado'
    ]
    columnas_finales = [col for col in columnas_sql if col in df.columns]

    return df[columnas_finales]


def limpiar_swaps(df_crudo, df_mapeo_columnas, df_mapeo_monedas):
    """
    Toma el DataFrame crudo de Swaps (PFTIT695), estandariza columnas y limpia.
    
    El CSV tiene columnas duplicadas (Titulo, Facial, Mo, Moneda) para derecho y obligación,
    por lo que se procesa por posición de columna en vez de usar estandarizar_columnas.
    """
    # El CSV tiene 26 columnas por posición
    # Asignar nombres por posición (las columnas vienen duplicadas)
    nombres_posicionales = [
        'fecha_registro', 'fecha_constitucion', 'fecha_vencimiento', 'id_posicion',
        'tipo_der', 'id_derecho', 'nominal_der', 'facial_der', 'periodicidad_der', 'moneda_der',
        'vpn_der_local', 'vpn_derecho',
        'tipo_obl', 'id_obligacion', 'nominal_obl', 'facial_obl', 'periodicidad_obl', 'moneda_obl',
        'vpn_obl_local', 'vpn_obligacion',
        'valor_mercado', 'valor_cva_dva', 'up_dia_cva_dva',
        'portafolio', 'nit_contraparte', 'emisor'
    ]

    # Si el CSV tiene más o menos columnas, ajustar
    if len(df_crudo.columns) >= len(nombres_posicionales):
        df = df_crudo.iloc[:, :len(nombres_posicionales)].copy()
        df.columns = nombres_posicionales
    else:
        df = df_crudo.copy()
        df.columns = nombres_posicionales[:len(df.columns)]

    # Limpiar texto (trim espacios)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()

    # Convertir fechas
    for col in ['fecha_constitucion', 'fecha_vencimiento']:
        df[col] = pd.to_datetime(df[col], format='%Y/%m/%d', errors='coerce')

    # Limpiar facial (viene como "5.9500000000-No" → extraer solo el número)
    for col in ['facial_der', 'facial_obl']:
        df[col] = df[col].astype(str).str.split('-').str[0]
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Convertir columnas numéricas (quitar comas de miles)
    columnas_numericas = ['nominal_der', 'vpn_derecho',
                          'nominal_obl', 'vpn_obligacion', 'valor_mercado']
    for col in columnas_numericas:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Estandarizar periodicidad (SV→SEMESTRAL, TV→TRIMESTRAL, etc.)
    mapeo_periodicidad = {'SV': 'SEMESTRAL', 'TV': 'TRIMESTRAL', 'AV': 'ANUAL', 'MV': 'MENSUAL'}
    for col in ['periodicidad_der', 'periodicidad_obl']:
        df[col] = df[col].map(mapeo_periodicidad).fillna(df[col])

    # Estandarizar monedas
    df = estandarizar_monedas(df, 'moneda_der', df_mapeo_monedas)
    df = estandarizar_monedas(df, 'moneda_obl', df_mapeo_monedas)

    # Formatear portafolio: '02SV' → '02-SV'
    df['portafolio'] = df['portafolio'].apply(
        lambda x: f"{x[:2]}-{x[2:]}" if len(str(x)) >= 4 and '-' not in str(x) else x
    )

    # Construir especie: CCS_{moneda_der}_{moneda_obl}
    df['especie'] = 'CCS_' + df['moneda_der'].astype(str) + '_' + df['moneda_obl'].astype(str)

    # Estado: siempre vigente
    df['estado'] = 'V'

    # Seleccionar columnas finales (las que coinciden con hist_swaps)
    columnas_sql = [
        'fecha_reporte', 'id_posicion', 'especie', 'fecha_constitucion', 'fecha_vencimiento',
        'tipo_der', 'id_derecho', 'nominal_der', 'facial_der', 'periodicidad_der', 'moneda_der',
        'vpn_derecho',
        'tipo_obl', 'id_obligacion', 'nominal_obl', 'facial_obl', 'periodicidad_obl', 'moneda_obl',
        'vpn_obligacion',
        'valor_mercado', 'portafolio', 'emisor', 'estado'
    ]
    columnas_finales = [col for col in columnas_sql if col in df.columns]

    return df[columnas_finales]


# ---------------------------------------------------------------------------
# 3. FUNCIONES DE VALORACIÓN DE DERIVADOS (Black-Scholes y Forwards)
# ---------------------------------------------------------------------------

def merge_lists_interleaved(list1, list2, list3, fillvalue=None):
    """Intercala tres listas en orden (a1,b1,c1, a2,b2,c2...), ignorando Nones."""
    merged = []
    for a, b, c in zip_longest(list1, list2, list3, fillvalue=fillvalue):
        if a is not None: merged.append(a)
        if b is not None: merged.append(b)
        if c is not None: merged.append(c)
    return merged


# ---------------------------------------------------------------------------
# CONVERSIONES DE TASA (puras) — nominal simple base 360 ↔ EA ↔ continua
# ---------------------------------------------------------------------------

def nominal_a_ea(tasa_nom_pct, dias):
    """
    Convierte una tasa nominal simple base 360 (en %) a efectiva anual (EA, en tanto por uno)
    para un plazo en días: (1 + tasa_nom/100 * dias/360)^(365/dias) - 1.

    Función pura. Soporta escalares o arrays de igual longitud (numpy).
    Protege plazos no positivos (dias <= 0) y bases negativas devolviendo NaN,
    ya que en esos casos la tasa EA no está definida de forma real.
    """
    tasa_nom_pct = np.asarray(tasa_nom_pct, dtype=np.float64)
    dias = np.asarray(dias, dtype=np.float64)
    base = 1.0 + (tasa_nom_pct / 100.0) * (dias / 360.0)
    # dias > 0 y base > 0 para que el exponente real esté definido
    valido = (dias > 0.0) & (base > 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        exponente = np.where(dias > 0.0, 365.0 / dias, np.nan)
        resultado = np.power(np.where(valido, base, np.nan), exponente) - 1.0
    resultado = np.where(valido, resultado, np.nan)
    return resultado if resultado.ndim else resultado.item()


def ea_a_nominal(tasa_ea, dias):
    """
    Convierte una tasa efectiva anual (EA, en tanto por uno) a nominal simple base 360 (en %)
    para un plazo en días: ((1 + EA)^(dias/365) - 1) * (360/dias) * 100.

    Función pura. Inversa de `nominal_a_ea`. Protege dias <= 0 y bases (1+EA) negativas
    devolviendo NaN.
    """
    tasa_ea = np.asarray(tasa_ea, dtype=np.float64)
    dias = np.asarray(dias, dtype=np.float64)
    base = 1.0 + tasa_ea
    valido = (dias > 0.0) & (base > 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        factor = np.power(np.where(valido, base, np.nan), np.where(dias > 0.0, dias / 365.0, np.nan))
        resultado = (factor - 1.0) * np.where(dias > 0.0, 360.0 / dias, np.nan) * 100.0
    resultado = np.where(valido, resultado, np.nan)
    return resultado if resultado.ndim else resultado.item()


def ea_a_continua(tasa_ea):
    """
    Convierte una tasa efectiva anual (EA, en tanto por uno) a tasa continua compuesta:
    ln(1 + EA).

    Función pura. Protege bases (1+EA) no positivas devolviendo NaN.
    """
    tasa_ea = np.asarray(tasa_ea, dtype=np.float64)
    base = 1.0 + tasa_ea
    with np.errstate(invalid='ignore', divide='ignore'):
        resultado = np.where(base > 0.0, np.log(np.where(base > 0.0, base, np.nan)), np.nan)
    return resultado if resultado.ndim else resultado.item()


def continua_a_ea(tasa_continua):
    """
    Convierte una tasa continua compuesta a efectiva anual (EA, en tanto por uno):
    exp(tasa_continua) - 1.

    Función pura. Inversa de `ea_a_continua`.
    """
    tasa_continua = np.asarray(tasa_continua, dtype=np.float64)
    resultado = np.exp(tasa_continua) - 1.0
    return resultado if resultado.ndim else resultado.item()


def delta_bs(S, K, vol, años, r, q, tipo):
    """
    Delta Black-Scholes para una opción europea.
    tipo: 'Call' o 'Put'. Soporta inputs escalares o arrays de igual longitud.
    """
    S, K, vol, años, r, q = (np.asarray(x, dtype=np.float64) for x in (S, K, vol, años, r, q))
    dp = (np.log(S / K) + (r - q + 0.5 * vol ** 2) * años) / (vol * np.sqrt(años))
    if tipo == 'Call':
        return np.exp(-q * años) * norm.cdf(dp)
    else:
        return -np.exp(-q * años) * norm.cdf(-dp)


def precio_bs_signed(S, K, vol, años, r, q, nominal, call_put, sentido):
    """
    Black-Scholes vectorizado con signo: Compra → positivo, Venta → negativo.
    Todos los parámetros deben ser arrays de igual longitud.
    """
    S, K, vol, años, r, q, nominal = (np.asarray(x, dtype=np.float64) for x in (S, K, vol, años, r, q, nominal))
    d1 = (np.log(S / K) + (r - q + 0.5 * vol ** 2) * años) / (vol * np.sqrt(años))
    d2 = d1 - vol * np.sqrt(años)
    call_val = S * np.exp(-q * años) * norm.cdf(d1) - K * np.exp(-r * años) * norm.cdf(d2)
    put_val  = K * np.exp(-r * años) * norm.cdf(-d2) - S * np.exp(-q * años) * norm.cdf(-d1)
    base  = np.where(np.asarray(call_put) == 'Call', call_val, put_val)
    signo = np.where(np.asarray(sentido)  == 'Compra', 1.0, -1.0)
    return base * signo * nominal


def prima_neta_collar(k_call, k_put, trm, vol_put, vol_call, t, r, q, nominal,
                      sentido_put, sentido_call):
    """
    Prima neta de un collar = precio_bs_signed(Put) + precio_bs_signed(Call).

    Envoltorio puro sobre `precio_bs_signed` (sin modificar su firma) que suma la
    prima firmada de la pata Put y la pata Call de un collar sobre USD/COP. Cada
    pata lleva su propio signo por sentido (Compra → positivo, Venta → negativo)
    y por pata (Call/Put), tal como los aplica `precio_bs_signed`. (Req 4.2)

    Parámetros
    ----------
    k_call, k_put : float
        Strikes de la Call y de la Put del collar.
    trm : float
        Spot USD/COP (S en Black-Scholes) sobre el que se valoran ambas patas.
    vol_put, vol_call : float
        Volatilidades implícitas de la pata Put y de la pata Call.
    t : float
        Tiempo a vencimiento en años (años en `precio_bs_signed`).
    r, q : float
        Tasa doméstica (r) y foránea (q) continuas.
    nominal : float
        Nominal de cada pata.
    sentido_put, sentido_call : str
        Sentido de cada pata ('Compra' / 'Venta').

    Retorna
    -------
    float
        Prima neta del collar (Put firmada + Call firmada).
    """
    prima_put = precio_bs_signed(trm, k_put, vol_put, t, r, q, nominal,
                                 'Put', sentido_put)
    prima_call = precio_bs_signed(trm, k_call, vol_call, t, r, q, nominal,
                                  'Call', sentido_call)
    return float(prima_put + prima_call)


def valorar_fwds(last_trm, dias, nominal, strike, r_fwd_v, fwdcop, tipo):
    """
    Valora un grupo homogéneo de forwards del mismo tipo ('Compra' o 'Venta').
    Inputs pueden ser scalares o Series de igual índice.
    Retorna (valoracion, vp_trm, vp_strike).
    """
    dias, nominal, strike, r_fwd_v, fwdcop = (
        np.asarray(x, dtype=np.float64) for x in (dias, nominal, strike, r_fwd_v, fwdcop)
    )
    last_trm = float(last_trm)
    r_plazo   = r_fwd_v / (360 / dias)
    vp_trm    = (last_trm + fwdcop) / (1 + r_plazo)
    vp_strike = strike / (1 + r_plazo)
    if tipo == 'Compra':
        val = (vp_trm - vp_strike) * nominal
    else:
        val = (vp_strike - vp_trm) * nominal
    return val, vp_trm, vp_strike


def construir_interpolador_vol(matriz_vol_df: pd.DataFrame):
    """
    Devuelve una función memoizada: (strike: float, dias: int) → volatilidad implícita.

    matriz_vol_df: DataFrame con index=strikes (float) y columnas 0..N-1 (tenores en días).
                   Resultado de _cargar_vol() en valoracion_derivados.py.
    """
    strikes_arr = matriz_vol_df.index.to_numpy(dtype=float)

    @functools.lru_cache(maxsize=4096)
    def interpolar(strike_obj: float, dias_venc: int) -> float:
        idx   = int(np.argmin(np.abs(strikes_arr - strike_obj)))
        upper = idx if strikes_arr[idx] >= strike_obj else min(idx + 1, len(strikes_arr) - 1)
        lower = max(upper - 1, 0)
        col   = min(dias_venc - 1, matriz_vol_df.shape[1] - 1)
        vol_l = float(matriz_vol_df.iloc[lower, col])
        vol_u = float(matriz_vol_df.iloc[upper, col])
        s_l, s_u = float(strikes_arr[lower]), float(strikes_arr[upper])
        if s_l == s_u:
            return vol_l
        if strike_obj <= float(strikes_arr[-1]):
            return float(np.interp(strike_obj, [s_l, s_u], [vol_l, vol_u]))
        coefs = np.polyfit([s_l, s_u], [vol_l, vol_u], 1)
        return float(np.polyval(coefs, strike_obj))

    return interpolar


# ---------------------------------------------------------------------------
# SENDA UVR — Metodología BanRep con ponderaciones estacionales
# ---------------------------------------------------------------------------
#
# La UVR se capitaliza con inflación MENSUAL, pero el input disponible es la
# inflación ANUAL esperada a fin de año (31-dic). La inflación mensual se
# despeja aplicando ponderaciones estacionales:
#
#     inf_mensual[mes] = (1 + IPC_fin_de_año)^(ponderacion[mes]) - 1
#
# Como las ponderaciones suman 100%, el producto de las 12 inflaciones
# mensuales reproduce exactamente la inflación anual:
#
#     ∏(1 + inf_mensual[m]) = (1 + IPC_fin_de_año)^(∑ pond[m]) = 1 + IPC_fin_de_año
#
# Rezago de vigencia: la inflación medida en el mes M rige la UVR desde el 15
# del mes M+1 hasta el 15 del mes M+2 (publicación con un mes de rezago).

# Ponderación estacional mensual (suma = 100%).
PONDERACION_INFLACION_MENSUAL = {
    1:  0.175046664,   # Enero
    2:  0.210220473,   # Febrero
    3:  0.128870690,   # Marzo
    4:  0.113330425,   # Abril
    5:  0.067503331,   # Mayo
    6:  0.023441611,   # Junio
    7:  0.035771456,   # Julio
    8:  0.037389248,   # Agosto
    9:  0.071717161,   # Septiembre
    10: 0.011328700,   # Octubre
    11: 0.024642478,   # Noviembre
    12: 0.100737763,   # Diciembre
}


def inflacion_mensual_por_ponderacion(mes: int, ipc_fin_de_anio: float) -> float:
    """
    Despeja la inflación mensual del mes indicado a partir de la inflación
    anual esperada a fin de año (decimal, ej. 0.045 = 4.5%).

        inf_mensual = (1 + IPC_fin_de_año)^ponderacion[mes] - 1
    """
    w = PONDERACION_INFLACION_MENSUAL.get(mes, 1.0 / 12.0)
    return (1.0 + ipc_fin_de_anio) ** w - 1.0


def construir_ipc_fin_de_anio(senda_inflacion_anual: dict) -> dict:
    """
    De una senda de inflación anual {(año, mes): ipc_ea_decimal}, obtiene el
    IPC de fin de año por año: usa el valor de DICIEMBRE si existe, si no el
    del mes más cercano a diciembre disponible para ese año.

    Retorna {año: ipc_fin_de_año_decimal}.
    """
    por_anio = {}
    for (anio, mes), ipc in senda_inflacion_anual.items():
        por_anio.setdefault(anio, {})[mes] = ipc

    ipc_fin = {}
    for anio, meses in por_anio.items():
        if 12 in meses:
            ipc_fin[anio] = meses[12]
        else:
            mes_cercano = max(meses.keys())  # el más cercano a diciembre disponible
            ipc_fin[anio] = meses[mes_cercano]
    return ipc_fin


def calcular_senda_uvr(uvr_t0, fecha_inicio, fecha_fin, inf_mensual_por_mes):
    """
    Calcula la UVR de cierre capitalizando desde uvr_t0 con la fórmula BanRep,
    usando una senda de inflación MENSUAL ya despejada.

    Args:
        uvr_t0: UVR a la fecha de inicio (float).
        fecha_inicio / fecha_fin: datetime.date del rango de proyección.
        inf_mensual_por_mes: dict {(año, mes): inf_mensual_decimal} de la
                             inflación mensual despejada (puede ser negativa).

    Returns:
        (uvr_cierre, trayectoria) donde trayectoria es [(fecha_15, uvr), ...].
    """
    from dateutil.relativedelta import relativedelta

    # Punto de partida: día 15 más cercano hacia atrás
    if fecha_inicio.day >= 15:
        periodo = fecha_inicio.replace(day=15)
    else:
        periodo = (fecha_inicio - relativedelta(months=1)).replace(day=15)

    uvr = float(uvr_t0)
    trayectoria = [(periodo, uvr)]

    while periodo < fecha_fin:
        inicio_p = periodo
        fin_p = inicio_p + relativedelta(months=1)
        dias_periodo = (fin_p - inicio_p).days

        # Rezago: la UVR del 15 del mes M al 15 del mes M+1 usa la inflación
        # MEDIDA en el mes M-1 (publicada con un mes de rezago).
        mes_medicion_dt = inicio_p - relativedelta(months=1)
        inf_mes = inf_mensual_por_mes.get((mes_medicion_dt.year, mes_medicion_dt.month), 0.0)

        if fecha_fin <= fin_p:
            dias_transcurridos = (fecha_fin - inicio_p).days
            uvr = uvr * (1 + inf_mes) ** (dias_transcurridos / dias_periodo)
            trayectoria.append((fecha_fin, uvr))
            break
        else:
            uvr = uvr * (1 + inf_mes)
            trayectoria.append((fin_p, uvr))
            periodo = fin_p

    return round(uvr, 4), trayectoria


# ---------------------------------------------------------------------------
# SENDA DE INFLACIÓN MENSUAL desde YoY (identidad de año corrido)
# ---------------------------------------------------------------------------
#
# La inflación mensual se despeja de la relación entre dos YoY consecutivas:
#
#     inf_mensual[M] = (1 + YoY[M]) / (1 + YoY[M-1]) × (1 + estacional[mes]) - 1
#
# donde:
#   - YoY[M]   = inflación anual (12 meses) vigente en el mes M
#   - YoY[M-1] = inflación anual del mes anterior
#   - estacional[mes] = inflación mensual "típica" del mismo mes del año anterior
#                       (el mes que SALE del cálculo de 12 meses), aproximada con
#                       la ponderación estacional aplicada a la YoY vigente:
#                          estacional[mes] = (1 + YoY_vigente)^pond[mes] - 1
#
# Esto permite que, cuando la YoY cae (desinflación), la inflación mensual pueda
# ser negativa — reflejando correctamente un escenario de bajada de precios.


def construir_senda_yoy_mensual(yoy_vigente, yoy_final, fecha_inicio, n_meses):
    """
    Construye la senda de inflación YoY mensual (año corrido a 12 meses)
    interpolando linealmente entre la YoY vigente (fecha de corte) y la YoY
    final (fecha fin del horizonte = corte + n_meses).

    Args:
        yoy_vigente: YoY vigente a la fecha de corte (decimal, ej. 0.0614).
        yoy_final: YoY objetivo a la fecha fin del horizonte (decimal, ej. 0.04).
        fecha_inicio: date de corte.
        n_meses: número de meses del horizonte.

    Returns:
        dict {(año, mes): yoy_decimal} para el mes anterior al corte y cada mes
        del horizonte.
    """
    from dateutil.relativedelta import relativedelta

    senda_yoy = {}
    # Mes anterior al corte (para tener YoY[M-1] del primer mes proyectado)
    f_prev = fecha_inicio.replace(day=1) - relativedelta(months=1)
    senda_yoy[(f_prev.year, f_prev.month)] = yoy_vigente

    # Interpolación lineal entre corte (mes 0) y fin (mes n_meses)
    for m in range(0, n_meses + 1):
        f = fecha_inicio.replace(day=1) + relativedelta(months=m)
        w = m / n_meses if n_meses > 0 else 0.0
        senda_yoy[(f.year, f.month)] = yoy_vigente + (yoy_final - yoy_vigente) * w
    return senda_yoy


def despejar_inflacion_mensual_desde_yoy(senda_yoy, yoy_vigente, fecha_inicio, n_meses):
    """
    Despeja la inflación MENSUAL de cada mes del horizonte a partir de la senda
    YoY mensual, usando la identidad de año corrido y la estacionalidad estimada.

        inf_mensual[M] = (1 + YoY[M]) / (1 + YoY[M-1]) × (1 + estacional[mes]) - 1
        estacional[mes] = (1 + yoy_vigente)^pond[mes] - 1

    Returns:
        list de dicts [{'fecha': 'YYYY-MM', 'inf_mensual': x, 'yoy': y}, ...]
    """
    from dateutil.relativedelta import relativedelta

    resultado = []
    for m in range(1, n_meses + 1):
        f = fecha_inicio.replace(day=1) + relativedelta(months=m)
        f_ant = f - relativedelta(months=1)
        yoy_m = senda_yoy.get((f.year, f.month), yoy_vigente)
        yoy_ant = senda_yoy.get((f_ant.year, f_ant.month), yoy_vigente)

        estacional = (1.0 + yoy_vigente) ** PONDERACION_INFLACION_MENSUAL.get(f.month, 1/12) - 1.0
        inf_mensual = (1.0 + yoy_m) / (1.0 + yoy_ant) * (1.0 + estacional) - 1.0

        resultado.append({
            'fecha': f.strftime('%Y-%m'),
            'inf_mensual': inf_mensual,
            'yoy': yoy_m,
        })
    return resultado
