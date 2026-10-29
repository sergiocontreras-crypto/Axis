"""
app_dash.py — Dashboard AXIS (Dash)
Ejecutar: python app_dash.py
"""

import dash
from dash import dcc, html, dash_table, callback, Input, Output, State, no_update, ALL
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import sqlite3
import subprocess, sys
import base64
from pathlib import Path
from datetime import datetime, timedelta, date
import sys

# Agregar scripts/ al path para que valoracion_derivados pueda importar valorar_bonos
_scripts_dir = str(Path(__file__).parent / 'scripts')
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

try:
    from valoracion_derivados import valorar_portafolio_derivados, calcular_pyg
    DERIVADOS_DISPONIBLE = True
    print(f"[AXIS] ✅ valoracion_derivados importado OK")
except ImportError as _e:
    print(f"[AXIS] ❌ valoracion_derivados no disponible: {_e}")
    DERIVADOS_DISPONIBLE = False

from gestor_informes import (
    consultar_estado_informes, procesar_archivo, existe_informe,
    identificar_tipo_informe, inicializar_tablas, TIPOS_INFORME,
    obtener_trm_implicita, obtener_ipc_vigente
)
from comun.fuentes_mercado import construir_senda_trm_dict
from comun.validaciones_inputs import (
    detectar_portafolios_nuevos, detectar_swaps_sin_subyacente,
    validar_alta_portafolio, validar_asignacion_swap, parsear_subyacentes,
    persistir_portafolio, persistir_subyacente,
)
# datos_economicos: módulo opcional para gráficos de indicadores macro (no core).
# Si no está disponible, se usan stubs para que el dashboard arranque igual.
try:
    from datos_economicos import (
        cargar_indicadores_desde_bytes, INDICADORES_REQUERIDOS,
        guardar_indicadores_local, cargar_indicadores_desde_db,
    )
except ModuleNotFoundError:
    INDICADORES_REQUERIDOS = []

    def cargar_indicadores_desde_bytes(*_a, **_k):
        raise RuntimeError("Módulo datos_economicos no disponible")

    def guardar_indicadores_local(*_a, **_k):
        return 0

    def cargar_indicadores_desde_db(*_a, **_k):
        import pandas as _pd
        return _pd.DataFrame()

# --- CONFIGURACIÓN ---
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR  # app_dash.py está en la raíz del proyecto
DB_PATH = PROJECT_ROOT / "portafolio.db"

print("[AXIS] ✅ app_dash.py cargado — versión con SWP semáforo (19-ago-2026)")
print(f"[AXIS] TIPOS_INFORME: {list(TIPOS_INFORME.keys())}")


def get_conn():
    # Delegación en la capa compartida (comun.db). Se mantiene el wrapper local
    # para no tocar las ~decenas de llamadas get_conn() existentes en el dashboard.
    from comun.db import get_conn as _get_conn
    return _get_conn()


def load_table(tabla):
    """Carga tabla desde SQLite con tipos corregidos."""
    try:
        conn = get_conn()
        df = pd.read_sql_query(f"SELECT * FROM {tabla}", conn)
        conn.close()
        for col in ['nominal', 'valor_mercado', 'valor_compra', 'tasa_compra',
                    'valor_tir', 'tir', 'tasa_mercado', 'precio', 'tasa_dcto',
                    'ponderacion', 'base_anual', 'tasa_facial', 'limite_vida', 'limite_generales_cap']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        for col in ['fecha_reporte', 'fecha_vencimiento', 'fecha_emision', 'fecha_constitucion', 'fecha_actualizacion']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame()


def detectar_incompletos(df):
    """Detecta títulos con campos faltantes."""
    campos_criticos = ['subtipo_vehiculo', 'sector', 'zona_geografica',
                       'tipo_entidad', 'codigo_solvencia', 'numeral_regimen']
    campos_facial = ['tasa_referencia', 'base_anual', 'periodicidad']

    def esta_incompleto(row):
        for col in campos_criticos:
            val = str(row.get(col, '')).strip()
            if val in ('', 'None', 'nan', 'Sin Clasificar', 'SIN_NUMERAL'):
                return True
        if row.get('clase_activo') == 'Renta Fija':
            for col in campos_facial:
                val = str(row.get(col, '')).strip()
                if val in ('', 'None', 'nan', 'Sin Clasificar'):
                    return True
        return False

    df['_incompleto'] = df.apply(esta_incompleto, axis=1)
    return df


# Opciones para dropdowns editables
OPCIONES = {
    'subtipo_vehiculo': ['CDT', 'Bono_Ordinario', 'Titulo_Deuda_Publica', 'Titularizacion_No_Hipotecaria',
                         'Accion_Alta_Media', 'Deposito_Vista', 'FIC_Local_COP', 'FIC_Local_USD',
                         'ETF_Acciones_Local', 'ETF_Acciones_Ext', 'ETF_Acciones_Generico',
                         'ETF_Commodities_Ext', 'FCP_Local', 'Repo_Simultanea',
                         'Forward_General', 'Swap_General', 'Opcion_General', 'Excepcion'],
    'sector': ['Financiero', 'Real'],
    'zona_geografica': ['Local', 'Exterior', 'Multilateral', 'G-10'],
    'tipo_entidad': ['Vigilada_SFC', 'No_Vigilada_SFC', 'Soberano', 'Ente_Territorial',
                     'Banco_Exterior', 'No_Banco_Exterior', 'Multilateral',
                     'Vehiculo_Inversion', 'Gestor_Fondo', 'Camara_Riesgo_Central'],
    'codigo_solvencia': ['CS_MATRIZ', 'CS_03', 'CS_11', 'CS_14', 'CS_15', 'CS_35', 'CS_36'],
    'tasa_referencia': ['FS', 'IPC', 'IBR', 'DTF', 'UVR'],
    'base_anual': ['360', '365'],
    'periodicidad': ['TRIMESTRAL', 'SEMESTRAL', 'ANUAL', 'MENSUAL', 'PERIÓDICA'],
}

CALIFICACIONES = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-',
                  'BBB+', 'BBB', 'BBB-', 'BB+', 'BB', 'BB-',
                  'BRC1+', 'BRC1', 'BRC1-', 'F1+', 'F1',
                  'BRC2+', 'BRC2', 'BRC2-', 'F2',
                  'NACION', 'ACCIONES', 'NO REQUIER']


# --- APP ---
# Background callback manager (DiskcacheManager): saca las corridas largas (escenario
# con horizonte de varios años / tramos encadenados) a un proceso worker y hace polling
# de progreso, evitando el timeout HTTP del navegador. Usa diskcache (dependencia local,
# sin Redis/Celery). El caché vive en ./cache (ignorado por git).
import diskcache
from dash import DiskcacheManager

_bg_cache = diskcache.Cache(str(SCRIPT_DIR / "cache"))
background_callback_manager = DiskcacheManager(_bg_cache)

# ── Parche defensivo: terminate_job tolerante a procesos ya terminados ──
# BUG conocido de Dash + diskcache en Windows: al TERMINAR una corrida de background,
# `DiskcacheManager.get_result` llama `terminate_job`, que usa psutil para matar el
# proceso worker y sus hijos. Si el worker YA terminó por su cuenta (corrida completada),
# psutil lanza `NoSuchProcess`, la excepción sube y REVIENTA la entrega del resultado al
# navegador: la barra desaparece pero el `Store` queda vacío y NO aparece el botón de
# descarga, pese a que el cálculo terminó bien (los snapshots sí quedaron en la DB).
# Aquí envolvemos `terminate_job` para que CUALQUIER fallo de limpieza del proceso
# (NoSuchProcess u otros) se ignore silenciosamente: la limpieza es best-effort y no debe
# impedir devolver el resultado ya calculado. No cambia la lógica de cálculo.
try:
    _bcm_cls = type(background_callback_manager)
    _orig_terminate_job = _bcm_cls.terminate_job

    def _terminate_job_seguro(self, job):
        try:
            return _orig_terminate_job(self, job)
        except Exception as _e:  # NoSuchProcess / AccessDenied / etc.: proceso ya terminó
            print(f"[AXIS] terminate_job ignorado (worker ya finalizado): "
                  f"{type(_e).__name__}")
            return None

    _bcm_cls.terminate_job = _terminate_job_seguro
    print("[AXIS] ✅ Parche terminate_job aplicado (entrega de resultado robusta en Windows).")
except Exception as _e_patch:
    print(f"[AXIS] ⚠️ No se pudo aplicar el parche terminate_job: {type(_e_patch).__name__}")

app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    background_callback_manager=background_callback_manager,
)
app.title = "AXIS Dashboard"

# Permitir cargas de archivos grandes por el drop zone (PIP ~19 MB, mt ~82 MB, etc.).
# Flask rechaza por defecto cuerpos de request que superen MAX_CONTENT_LENGTH; sin un
# límite amplio, `dcc.Upload` con archivos grandes falla en silencio (el Log queda vacío
# porque el POST se rechaza antes de llegar al callback). Se fija en 512 MB, holgado para
# los informes más pesados del corte. MAX_FORM_MEMORY_SIZE evita que Werkzeug/Flask 3.x
# aborte el parseo multipart de campos grandes (el contenido base64 del upload viaja como
# campo de formulario). Ambos son del objeto Flask subyacente (app.server).
app.server.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024  # 512 MB
try:
    # Disponible en Werkzeug/Flask recientes; si no existe, se ignora (retrocompatible).
    app.server.config['MAX_FORM_MEMORY_SIZE'] = 512 * 1024 * 1024
except Exception:
    pass


def _fmt_cop(valor):
    """Formatea valor COP con nomenclatura correcta: MM=Millones, MdM=Miles de Millones, B=Billones."""
    abs_v = abs(valor)
    signo = '-' if valor < 0 else ''
    if abs_v >= 1e12:
        return f"{signo}${abs_v/1e12:,.2f} B"
    elif abs_v >= 1e9:
        return f"{signo}${abs_v/1e9:,.1f} MdM"
    elif abs_v >= 1e6:
        return f"{signo}${abs_v/1e6:,.0f} MM"
    else:
        return f"{signo}${abs_v:,.0f}"

# --- SIDEBAR COMPONENT ---
def _build_sidebar():
    """Construye el sidebar con fechas, semáforos, drop zone y log."""
    # Fecha por defecto = última disponible en hist_titulos
    hoy = date.today()
    try:
        _conn = get_conn()
        _row = _conn.execute("SELECT MAX(fecha_reporte) FROM hist_titulos").fetchone()
        _conn.close()
        fecha_default = pd.to_datetime(_row[0]).date() if _row and _row[0] else hoy
    except Exception:
        fecha_default = hoy

    return html.Div([
        # Header
        html.H4("AXIS", style={'margin': '0 0 4px 0', 'fontSize': '16px', 'fontWeight': '700'}),
        html.P("Proyecciones de Portafolio", style={'margin': '0 0 12px 0', 'fontSize': '10px', 'color': '#6c757d'}),

        # Fechas
        html.Label("📅 Fecha Inicio", style={'fontSize': '11px', 'fontWeight': '600'}),
        dcc.DatePickerSingle(
            id='sidebar-fecha-inicio',
            date=fecha_default,
            display_format='YYYY-MM-DD',
            style={'width': '100%', 'marginBottom': '8px'}
        ),
        html.Label("📅 Fecha Fin", style={'fontSize': '11px', 'fontWeight': '600'}),
        dcc.DatePickerSingle(
            id='sidebar-fecha-fin',
            date=fecha_default + timedelta(days=365),
            display_format='YYYY-MM-DD',
            style={'width': '100%', 'marginBottom': '8px'}
        ),
        html.Button("▶ Aplicar Fechas", id='btn-aplicar-fechas', n_clicks=0,
                    style={'width': '100%', 'backgroundColor': '#28a745', 'color': 'white',
                           'border': 'none', 'padding': '6px', 'borderRadius': '4px',
                           'cursor': 'pointer', 'fontWeight': '600', 'fontSize': '11px',
                           'marginBottom': '12px'}),

        html.Hr(style={'margin': '8px 0', 'borderColor': '#dee2e6'}),

        # Semáforos
        html.Label("INFORMES", style={'fontSize': '11px', 'fontWeight': '700', 'letterSpacing': '0.5px'}),
        html.Div(id='sidebar-semaforos', children=[
            html.Div("⏳ Cargando semáforos...", style={'fontSize': '10px', 'color': '#999'})
        ], style={'marginTop': '6px', 'marginBottom': '8px'}),

        # Indicadores calculados (TRM, IPC)
        html.Div(id='sidebar-indicadores', style={'marginBottom': '12px'}),

        html.Hr(style={'margin': '8px 0', 'borderColor': '#dee2e6'}),

        # Drop Zone
        dcc.Upload(
            id='sidebar-upload',
            children=html.Div([
                html.Div("📂", style={'fontSize': '24px'}),
                html.Div("SOLTAR ARCHIVOS AQUÍ", style={'fontSize': '10px', 'fontWeight': '600'}),
                html.Div("o click para seleccionar", style={'fontSize': '9px', 'color': '#6c757d'}),
            ], style={'textAlign': 'center', 'padding': '12px 4px'}),
            style={
                'border': '2px dashed #adb5bd',
                'borderRadius': '6px',
                'cursor': 'pointer',
                'marginBottom': '10px',
                'backgroundColor': '#ffffff',
            },
            multiple=True,
            # max_size=-1: sin límite de tamaño del lado cliente (por defecto dcc.Upload
            # puede rechazar archivos grandes). El PIP (~19 MB) y otros informes pesados
            # deben poder subirse; el límite real lo gobierna MAX_CONTENT_LENGTH de Flask.
            max_size=-1,
        ),

        # Log de procesamiento
        html.Label("Log:", style={'fontSize': '10px', 'fontWeight': '600'}),
        html.Div(id='sidebar-log', style={
            'fontSize': '10px', 'maxHeight': '150px', 'overflowY': 'auto',
            'padding': '4px', 'backgroundColor': '#ffffff', 'borderRadius': '4px',
            'border': '1px solid #dee2e6', 'marginTop': '4px'
        }),

    ], style={
        'width': '200px', 'minWidth': '200px', 'maxWidth': '200px',
        'backgroundColor': '#f8f9fa', 'borderRight': '1px solid #dee2e6',
        'padding': '12px', 'overflowY': 'scroll', 'height': '100vh',
        'position': 'fixed', 'top': 0, 'left': 0, 'zIndex': 100,
    })


app.layout = html.Div([
    # Sidebar fijo a la izquierda
    _build_sidebar(),

    # Contenido principal (desplazado por el sidebar)
    html.Div([
        dcc.Tabs(id='tabs', value='tab-portafolio', children=[
            dcc.Tab(label='📋 Diccionario', value='tab-diccionario'),
            dcc.Tab(label='📊 Portafolio', value='tab-portafolio'),
            dcc.Tab(label='⚠️ Alertas', value='tab-alertas'),
            dcc.Tab(label='📥 Inputs', value='tab-inputs'),
            dcc.Tab(label='📊 Resultados', value='tab-resultados'),
            dcc.Tab(label='🏦 Encaje', value='tab-encaje'),
            dcc.Tab(label='✓ Validacion', value='tab-validacion'),
            dcc.Tab(label='⚙️ Ajustes', value='tab-ajustes'),
        ]),
        # Contenido dinámico (diccionario, alertas, validación, causación)
        html.Div(id='tab-content-dynamic'),
        # Portafolio: siempre en el DOM, se oculta/muestra con display
        html.Div(id='tab-content-portafolio', style={'display': 'none'}),
        # Inputs: siempre en el DOM, se oculta/muestra con display
        html.Div(id='tab-content-inputs', style={'display': 'none'}),
        # Resultados: siempre en el DOM, se oculta/muestra con display
        html.Div(id='tab-content-resultados', style={'display': 'none'}),
    ], style={'marginLeft': '212px', 'padding': '10px'}),

    # Stores para datos cargados
    dcc.Store(id='refresh-trigger', data=0),
    dcc.Store(id='store-tes-cop', data=None),
    dcc.Store(id='store-tes-uvr', data=None),
    dcc.Store(id='store-sendas', data=None),
    dcc.Store(id='store-corporativos', data=None),
    dcc.Store(id='store-resultados-pyg', data=None),
    dcc.Store(id='store-upload-log', data=[]),
    dcc.Store(id='store-resultados-escenario', data=None),
], style={'fontFamily': 'Arial, sans-serif'})


# =============================================================================
# SIDEBAR CALLBACKS
# =============================================================================
@callback(
    Output('sidebar-semaforos', 'children'),
    [Input('sidebar-fecha-inicio', 'date'),
     Input('store-upload-log', 'data')]
)
def actualizar_semaforos(fecha_inicio, _log):
    """Actualiza los indicadores de estado de cada informe."""
    if not fecha_inicio:
        fecha_inicio = date.today()
    else:
        fecha_inicio = pd.to_datetime(fecha_inicio).date()

    try:
        estados = consultar_estado_informes(fecha_inicio)
    except Exception as e:
        print(f"[AXIS ERROR] consultar_estado_informes: {e}")
        estados = {}

    nombres = {'597': '597', 'PIP': 'PIP', 'FWD': 'FWD', 'SWP': 'SWP', 'OPC': 'OPC', 'CURV': 'Curvas', 'VOL': 'Vol USD', 'OTC': 'OTC'}

    items = []
    for tipo, nombre in nombres.items():
        info = estados.get(tipo, {'estado': 'rojo', 'fecha': None})
        icono = '🟢' if info['estado'] == 'verde' else '🔴'
        fecha_info = f" ({info['fecha']})" if info.get('fecha') else ''
        items.append(html.Div(
            f"{icono} {nombre}{fecha_info}",
            style={'fontSize': '11px', 'padding': '2px 0', 'fontFamily': 'monospace'}
        ))

    print(f"[AXIS] Semáforos renderizados: {len(items)} items ({list(nombres.keys())})")
    return items


@callback(
    Output('sidebar-indicadores', 'children'),
    [Input('sidebar-fecha-inicio', 'date'),
     Input('store-upload-log', 'data')]
)
def actualizar_indicadores(fecha_inicio, _log):
    """Calcula y muestra TRM, IPC y UVR vigente para la fecha seleccionada."""
    if not fecha_inicio:
        fecha_inicio = date.today()
    else:
        fecha_inicio = pd.to_datetime(fecha_inicio).date()

    from gestor_informes import obtener_uvr_vigente

    items = []

    # TRM implícita
    trm, fecha_trm = obtener_trm_implicita(fecha_inicio)
    if trm:
        items.append(html.Div(
            f"💲 TRM: ${trm:,.2f}",
            style={'fontSize': '11px', 'padding': '2px 0', 'fontWeight': '600', 'color': '#2c3e50'}
        ))
    else:
        items.append(html.Div(
            "💲 TRM: sin datos USD",
            style={'fontSize': '11px', 'padding': '2px 0', 'color': '#95a5a6'}
        ))

    # IPC vigente
    ipc, mes_label = obtener_ipc_vigente(fecha_inicio)
    if ipc is not None:
        items.append(html.Div(
            f"📊 IPC: {ipc:.2f}% ({mes_label})",
            style={'fontSize': '11px', 'padding': '2px 0', 'fontWeight': '600', 'color': '#2c3e50'}
        ))
    else:
        items.append(html.Div(
            "📊 IPC: sin serie",
            style={'fontSize': '11px', 'padding': '2px 0', 'color': '#95a5a6'}
        ))

    # UVR vigente (implícita de TUVT)
    uvr, fecha_uvr = obtener_uvr_vigente(fecha_inicio)
    if uvr is not None:
        items.append(html.Div(
            f"📐 UVR: {uvr:,.4f}",
            style={'fontSize': '11px', 'padding': '2px 0', 'fontWeight': '600', 'color': '#2c3e50'}
        ))

    return items


@callback(
    [Output('sidebar-log', 'children'),
     Output('store-upload-log', 'data')],
    [Input('sidebar-upload', 'contents')],
    [State('sidebar-upload', 'filename'),
     State('store-upload-log', 'data')],
    prevent_initial_call=True,
)
def procesar_upload(contents_list, filenames_list, log_actual):
    """Procesa archivos subidos via drop zone."""
    if not contents_list or not filenames_list:
        return no_update, no_update

    if log_actual is None:
        log_actual = []

    mensajes = []
    for contenido_b64, filename in zip(contents_list, filenames_list):
        # Decodificar contenido base64 de dcc.Upload
        try:
            content_type, content_string = contenido_b64.split(',')
            contenido_bytes = base64.b64decode(content_string)
        except Exception as e:
            mensajes.append(f"❌ Error leyendo {filename}: {e}")
            continue

        # Identificar tipo
        tipo = identificar_tipo_informe(filename)
        if not tipo:
            mensajes.append(f"⚠️ No reconocido: {filename}")
            continue

        # Procesar
        exito, mensaje, _tipo, _fecha = procesar_archivo(contenido_bytes, filename)
        if exito:
            mensajes.append(f"✅ {mensaje}")
        else:
            mensajes.append(f"❌ {mensaje}")

    log_actual = mensajes + log_actual
    # Mantener solo últimos 20 mensajes
    log_actual = log_actual[:20]

    log_items = [html.Div(m, style={'padding': '1px 0'}) for m in log_actual]
    return log_items, log_actual


# =============================================================================
# TAB ROUTING
# =============================================================================
@callback(
    [Output('tab-content-dynamic', 'children'),
     Output('tab-content-dynamic', 'style'),
     Output('tab-content-portafolio', 'style'),
     Output('tab-content-inputs', 'style'),
     Output('tab-content-resultados', 'style')],
    [Input('tabs', 'value'),
     Input('refresh-trigger', 'data'),
     Input('btn-aplicar-fechas', 'n_clicks')],
    [State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')]
)
def render_tab(tab, _, _n_fechas, fecha_inicio, fecha_fin):
    hide = {'display': 'none'}
    show = {'display': 'block'}

    if tab == 'tab-inputs':
        return no_update, hide, hide, show, hide
    elif tab == 'tab-resultados':
        return no_update, hide, hide, hide, show
    elif tab == 'tab-portafolio':
        return no_update, hide, show, hide, hide
    elif tab == 'tab-diccionario':
        return layout_diccionario(), show, hide, hide, hide
    elif tab == 'tab-alertas':
        return layout_alertas(), show, hide, hide, hide
    elif tab == 'tab-validacion':
        return layout_validacion(), show, hide, hide, hide
    elif tab == 'tab-encaje':
        return layout_encaje(fecha_inicio, fecha_fin), show, hide, hide, hide
    elif tab == 'tab-ajustes':
        return layout_ajustes_reservas(), show, hide, hide, hide
    return html.Div(), show, hide, hide, hide


# Inicializar contenido persistente al arrancar
@callback(
    Output('tab-content-portafolio', 'children'),
    [Input('refresh-trigger', 'data'),
     Input('btn-aplicar-fechas', 'n_clicks')],
    State('sidebar-fecha-inicio', 'date'),
    prevent_initial_call=False
)
def init_portafolio_tab(_, n_clicks, fecha_inicio):
    """Re-clasifica el portafolio para la fecha seleccionada y renderiza la tab."""
    from clasificar_portafolio import clasificar_portafolio_diario

    fecha_ref = pd.to_datetime(fecha_inicio).strftime('%Y-%m-%d') if fecha_inicio else None

    if fecha_ref and n_clicks:
        # Verificar si hay datos crudos para esa fecha en hist_titulos
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM hist_titulos WHERE fecha_reporte = ?", (fecha_ref,)
            ).fetchone()

            if row[0] > 0:
                conn.close()
                print(f"[AXIS] Reclasificando portafolio para {fecha_ref}...")
                clasificar_portafolio_diario(fecha_ref)
                _cache_clasificacion['fecha'] = None
                _cache_clasificacion['df'] = None
            else:
                # No hay datos exactos → buscar la fecha más cercana disponible
                closest = conn.execute(
                    "SELECT DISTINCT fecha_reporte FROM hist_titulos ORDER BY ABS(julianday(fecha_reporte) - julianday(?)) LIMIT 1",
                    (fecha_ref,)
                ).fetchone()
                conn.close()
                if closest:
                    fecha_cercana = closest[0]
                    print(f"[AXIS] No hay datos para {fecha_ref}, usando fecha más cercana: {fecha_cercana}")
                    clasificar_portafolio_diario(fecha_cercana)
                    _cache_clasificacion['fecha'] = None
                    _cache_clasificacion['df'] = None
                else:
                    print(f"[AXIS] No hay ninguna fecha en hist_titulos.")
        except Exception as e:
            print(f"[AXIS] Error al reclasificar: {e}")
            import traceback
            traceback.print_exc()

    return layout_portafolio(fecha_inicio)


@callback(
    Output('tab-content-inputs', 'children'),
    Input('refresh-trigger', 'data'),
    prevent_initial_call=False
)
def init_inputs_tab(_):
    return layout_inputs()


@callback(
    Output('tab-content-resultados', 'children'),
    Input('refresh-trigger', 'data'),
    prevent_initial_call=False
)
def init_resultados_tab(_):
    return layout_resultados()


# =============================================================================
# PESTAÑA 1: DICCIONARIO
# =============================================================================
def layout_diccionario():
    df = load_table('diccionario_clasificado')
    if df.empty:
        return html.Div("No hay datos. Ejecuta diccionario_titulos.py primero.", style={'padding': '20px'})

    df = detectar_incompletos(df)
    # Excluir colaterales (especies con "enviado" o "recibido") antes de contar
    df = df[~df['especie'].str.contains('enviado|recibido', case=False, na=False)]
    n_incompletos = df['_incompleto'].sum()

    if n_incompletos == 0:
        return html.Div([
            html.H4("✅ Todos los títulos están clasificados.", style={'color': 'green', 'padding': '20px'}),
            html.Button("🔄 Re-clasificar Portafolio", id='btn-reclasificar-dict', n_clicks=0, style={'margin': '20px'}),
            html.Div(id='msg-reclasificar-dict'),
        ])

    df_inc = df[df['_incompleto']].drop(columns=['_incompleto'])
    cols_editables = list(OPCIONES.keys())
    cols_mostrar = ['especie', 'emisor', 'isin', 'clase_activo'] + cols_editables

    # Configurar columnas con dropdowns
    columns = []
    for col in cols_mostrar:
        if col in cols_editables:
            columns.append({
                'name': f"⚠️ {col}", 'id': col, 'editable': True,
                'presentation': 'dropdown'
            })
        else:
            columns.append({'name': col, 'id': col, 'editable': False})

    dropdown_conditionals = {col: {'options': [{'label': v, 'value': v} for v in opts]} for col, opts in OPCIONES.items()}

    return html.Div([
        html.H4(f"⚠️ {n_incompletos} título(s) con campos incompletos"),
        dash_table.DataTable(
            id='tabla-editar-diccionario',
            columns=columns,
            data=df_inc[cols_mostrar].to_dict('records'),
            dropdown=dropdown_conditionals,
            editable=True,
            page_size=20,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            style_data_conditional=[
                {'if': {'column_id': col}, 'backgroundColor': '#fff3cd'} for col in cols_editables
            ],
            filter_action='native',
            sort_action='native',
        ),
        html.Br(),
        html.Button("💾 Guardar Cambios", id='btn-guardar-dict', n_clicks=0, style={'marginRight': '10px'}),
        html.Button("🔄 Re-clasificar Portafolio", id='btn-reclasificar-dict', n_clicks=0),
        html.Div(id='msg-guardar-dict', style={'marginTop': '10px'}),
        html.Div(id='msg-reclasificar-dict', style={'marginTop': '10px'}),
    ], style={'padding': '10px'})


@callback(Output('msg-guardar-dict', 'children'),
          Input('btn-guardar-dict', 'n_clicks'),
          State('tabla-editar-diccionario', 'data'),
          prevent_initial_call=True)
def guardar_diccionario(n_clicks, data):
    if not data:
        return ""
    conn = get_conn()
    cambios = 0
    for row in data:
        especie = row.get('especie', '')
        isin = row.get('isin', '')
        for col in OPCIONES.keys():
            val = str(row.get(col, '') or '').strip()
            if val and val not in ('', 'None', 'nan', 'Sin Clasificar', 'SIN_NUMERAL'):
                if isin and str(isin) not in ('', 'None', 'nan'):
                    conn.execute(f"UPDATE diccionario_clasificado SET [{col}] = ? WHERE isin = ?", (val, isin))
                else:
                    conn.execute(f"UPDATE diccionario_clasificado SET [{col}] = ? WHERE especie = ?", (val, especie))
                cambios += 1
    conn.commit()
    conn.close()
    if cambios > 0:
        return html.Span(f"✅ {cambios} campo(s) guardados.", style={'color': 'green'})
    return html.Span("No se detectaron cambios.", style={'color': 'gray'})


@callback(Output('msg-reclasificar-dict', 'children'),
          Input('btn-reclasificar-dict', 'n_clicks'),
          prevent_initial_call=True)
def reclasificar_from_dict(n_clicks):
    return reclasificar_portafolio()


# =============================================================================
# PESTAÑA 2: PORTAFOLIO
# =============================================================================
def _generar_graficas_portafolio(df, titulo, id_suffix=''):
    """
    Genera las gráficas del portafolio (Tabla Cía, Asset Class, Solvencia,
    Consumo Límites Régimen, Plazo) como componentes Dash reutilizables.
    Replica exactamente el diseño de la vista actual del Tab Portafolio.
    """
    SB_COLORS = ['#008f4f', '#00b864', '#66cc99', '#004d2a', '#c8a415', '#f2d024',
                 '#2c3e50', '#5dade2', '#af7ac5', '#e74c3c', '#f39c12', '#1abc9c']

    children = [html.H5(titulo, style={'textAlign': 'center', 'color': '#004d2a',
                                        'marginBottom': '8px', 'fontSize': '14px'})]

    # --- Tabla resumen por compañía (completa, 4 cols) ---
    tabla_cia = html.Div()
    if 'compania' in df.columns and 'valor_mercado' in df.columns:
        df_cia = df[df['compania'].notna() & (df['compania'] != '')].copy()
        vm_total = df_cia['valor_mercado'].sum()
        if vm_total > 0:
            resumen_cia = df_cia.groupby('compania').agg(
                vm_total=('valor_mercado', 'sum'),
                n_titulos=('especie', 'count'),
            ).reset_index().sort_values('vm_total', ascending=False)
            resumen_cia['pct'] = (resumen_cia['vm_total'] / vm_total * 100)
            resumen_cia['vm_fmt'] = resumen_cia['vm_total'].apply(_fmt_cop)
            resumen_cia['pct_fmt'] = resumen_cia['pct'].apply(lambda x: f"{x:.1f}%")
            resumen_cia['n_titulos'] = resumen_cia['n_titulos'].astype(int)
            tabla_cia = html.Table([
                html.Thead(html.Tr([
                    html.Th("Compañía", style={'padding': '4px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center', 'fontSize': '10px'}),
                    html.Th("VM Total", style={'padding': '4px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center', 'fontSize': '10px'}),
                    html.Th("# Títulos", style={'padding': '4px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center', 'fontSize': '10px'}),
                    html.Th("% Portafolio", style={'padding': '4px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center', 'fontSize': '10px'}),
                ])),
                html.Tbody([
                    html.Tr([
                        html.Td(row['compania'], style={'padding': '3px', 'textAlign': 'center', 'fontSize': '10px'}),
                        html.Td(row['vm_fmt'], style={'padding': '3px', 'textAlign': 'center', 'fontSize': '10px'}),
                        html.Td(str(row['n_titulos']), style={'padding': '3px', 'textAlign': 'center', 'fontSize': '10px'}),
                        html.Td(row['pct_fmt'], style={'padding': '3px', 'textAlign': 'center', 'fontSize': '10px'}),
                    ], style={'backgroundColor': '#f0faf5' if i % 2 == 1 else 'white'})
                    for i, (_, row) in enumerate(resumen_cia.iterrows())
                ]),
            ], style={'width': '100%', 'borderCollapse': 'collapse', 'border': '1px solid #dee2e6'})

    children.append(html.Div([
        html.H6("Resumen por Compañía", style={'marginBottom': '6px', 'color': '#004d2a', 'fontSize': '13px'}),
        tabla_cia,
    ], style={'marginBottom': '10px'}))

    # --- Asset Class - barras horizontales ---
    fig_ac = go.Figure()
    if 'asset_class_definitivo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('asset_class_definitivo')['valor_mercado'].sum().sort_values(ascending=True).reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        r['label'] = r['vm_mm'].apply(lambda x: f"${x:,.0f} MM")
        fig_ac = go.Figure(go.Bar(
            y=r['asset_class_definitivo'], x=r['vm_mm'], orientation='h',
            marker_color=SB_COLORS[0], text=r['label'], textposition='outside',
            textfont=dict(size=8)))
        fig_ac.update_layout(title='Valor por Asset Class', xaxis_title='Miles de Millones COP',
                             height=280, margin=dict(l=10, r=60, t=30, b=10), xaxis_tickformat=',.0f')

    # --- Solvencia - donut (filtra vacíos en categoría) ---
    fig_solv = go.Figure()
    if 'categoria' in df.columns and 'valor_mercado' in df.columns:
        r = df[df['categoria'].notna() & (df['categoria'] != '')].groupby('categoria')['valor_mercado'].sum().reset_index()
        r = r[r['valor_mercado'] > 0]
        if not r.empty:
            r['pct'] = r['valor_mercado'] / r['valor_mercado'].sum() * 100
            r['etiqueta'] = r.apply(
                lambda row: f"{row['categoria']}<br>{_fmt_cop(row['valor_mercado'])} ({row['pct']:.1f}%)", axis=1)
            fig_solv = go.Figure(go.Pie(
                labels=r['etiqueta'], values=r['valor_mercado'],
                hole=0.4, marker=dict(colors=SB_COLORS[:len(r)]),
                textinfo='label', textposition='outside',
                textfont=dict(size=8), insidetextorientation='radial'))
            fig_solv.update_layout(title='Distribución por Categoría Solvencia', height=280,
                                   margin=dict(l=10, r=10, t=30, b=10), showlegend=False)

    # --- Consumo Límites Régimen - barras agrupadas ---
    fig_reg = go.Figure()
    if 'numeral_regimen' in df.columns and 'valor_mercado' in df.columns:
        df_enc = df[df['encaje'] == 'SI'] if 'encaje' in df.columns else df
        total = df_enc['valor_mercado'].sum()
        if total > 0:
            resumen = df_enc.groupby('numeral_regimen')['valor_mercado'].sum().reset_index()
            resumen['pct'] = resumen['valor_mercado'] / total
            df_lim = load_table('param_categorias_regimen')
            if not df_lim.empty:
                col_l = 'limite_vida'
                df_lim['numeral_regimen'] = df_lim['numeral_regimen'].astype(str)
                resumen['numeral_regimen'] = resumen['numeral_regimen'].astype(str)
                resumen = resumen.merge(df_lim[['numeral_regimen', col_l]], on='numeral_regimen', how='left')
                resumen = resumen.rename(columns={col_l: 'limite'})
                resumen = resumen.dropna(subset=['limite'])
                resumen = resumen[resumen['limite'] < 1.0].sort_values('pct', ascending=False).head(12)
                if not resumen.empty:
                    fig_reg.add_trace(go.Bar(name='% Actual', x=resumen['numeral_regimen'], y=resumen['pct'], marker_color=SB_COLORS[0]))
                    fig_reg.add_trace(go.Bar(name='% Límite', x=resumen['numeral_regimen'], y=resumen['limite'], marker_color=SB_COLORS[4]))
                    fig_reg.update_layout(barmode='group', title='Consumo Límites Régimen',
                                          yaxis_tickformat='.0%', height=280, margin=dict(l=10, r=10, t=30, b=10))

    # --- Plazo - barras verticales ---
    fig_plazo = go.Figure()
    if 'tramo_plazo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('tramo_plazo')['valor_mercado'].sum().reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        fig_plazo = go.Figure(go.Bar(
            x=r['tramo_plazo'], y=r['vm_mm'],
            marker_color=SB_COLORS[1], text=r['valor_mercado'].apply(_fmt_cop),
            textposition='outside', textfont=dict(size=8)))
        fig_plazo.update_layout(title='Valor por Tramo de Plazo', yaxis_title='MdM COP',
                                height=280, margin=dict(l=10, r=10, t=30, b=30))

    # --- Grid 2x2: AC + Solvencia / Régimen + Plazo ---
    children.append(html.Div([
        html.Div([dcc.Graph(figure=fig_ac, id=f'grafico-ac{id_suffix}', config={'responsive': True, 'displayModeBar': False},
                            style={'height': '100%'})],
                 style={'gridColumn': '1', 'gridRow': '1', 'overflow': 'hidden'}),
        html.Div([dcc.Graph(figure=fig_solv, id=f'grafico-solv{id_suffix}', config={'responsive': True, 'displayModeBar': False},
                            style={'height': '100%'})],
                 style={'gridColumn': '2', 'gridRow': '1', 'overflow': 'hidden'}),
        html.Div([dcc.Graph(figure=fig_reg, id=f'grafico-reg{id_suffix}', config={'responsive': True, 'displayModeBar': False},
                            style={'height': '100%'})],
                 style={'gridColumn': '1', 'gridRow': '2', 'overflow': 'hidden'}),
        html.Div([dcc.Graph(figure=fig_plazo, id=f'grafico-plazo{id_suffix}', config={'responsive': True, 'displayModeBar': False},
                            style={'height': '100%'})],
                 style={'gridColumn': '2', 'gridRow': '2', 'overflow': 'hidden'}),
    ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr',
              'gridTemplateRows': 'auto auto', 'gap': '4px'}))

    return html.Div(children)


def layout_portafolio(fecha_inicio=None):
    # Pre-cargar datos y gráficos
    df = _cargar_portafolio_fecha(fecha_inicio)

    # Opciones compañía
    companias = ['Todas'] + sorted(df['compania'].dropna().unique().tolist()) if 'compania' in df.columns else ['Todas']
    opts_compania = [{'label': c, 'value': c} for c in companias]

    # Ramos
    ramos = ['Todos'] + sorted(df['ramo'].dropna().unique().tolist()) if 'ramo' in df.columns else ['Todos']
    opts_ramo = [{'label': r, 'value': r} for r in ramos]

    # Count
    count = f"{len(df)} posiciones"
    if 'fecha_reporte' in df.columns and not df.empty:
        fecha_real = pd.to_datetime(df['fecha_reporte'].iloc[0]).strftime('%Y-%m-%d')
        count = f"{len(df)} posiciones — Fecha datos: {fecha_real}"

    # Gráficos
    # Paleta Seguros Bolívar (primario #008f4f)
    SB_COLORS = ['#008f4f', '#00b864', '#66cc99', '#004d2a', '#c8a415', '#f2d024',
                 '#2c3e50', '#5dade2', '#af7ac5', '#e74c3c', '#f39c12', '#1abc9c']

    fig_ac = go.Figure()
    if 'asset_class_definitivo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('asset_class_definitivo')['valor_mercado'].sum().sort_values(ascending=True).reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        r['label'] = r['vm_mm'].apply(lambda x: f"${x:,.0f} MM")
        fig_ac = go.Figure(go.Bar(
            y=r['asset_class_definitivo'], x=r['vm_mm'], orientation='h',
            marker_color=SB_COLORS[0], text=r['label'], textposition='outside',
            textfont=dict(size=9)))
        fig_ac.update_layout(
            title='Valor por Asset Class', xaxis_title='Miles de Millones COP',
            height=320, margin=dict(l=10, r=80, t=40, b=10),
            xaxis_tickformat=',.0f')

    fig_solv = go.Figure()
    if 'categoria' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('categoria')['valor_mercado'].sum().reset_index()
        r['pct'] = r['valor_mercado'] / r['valor_mercado'].sum() * 100
        r['vm_mm'] = r['valor_mercado'] / 1e9
        r['etiqueta'] = r.apply(lambda row: f"{row['categoria']}<br>{_fmt_cop(row['valor_mercado'])} ({row['pct']:.1f}%)", axis=1)
        fig_solv = go.Figure(go.Pie(
            labels=r['etiqueta'], values=r['valor_mercado'],
            hole=0.4, marker=dict(colors=SB_COLORS[:len(r)]),
            textinfo='label', textposition='outside',
            textfont=dict(size=10), insidetextorientation='radial'))
        fig_solv.update_layout(
            title='Distribución por Categoría Solvencia',
            height=320, margin=dict(l=10, r=10, t=40, b=10),
            showlegend=False)

    fig_reg = go.Figure()
    if 'numeral_regimen' in df.columns and 'valor_mercado' in df.columns:
        df_enc = df[df['encaje'] == 'SI'] if 'encaje' in df.columns else df
        total = df_enc['valor_mercado'].sum()
        if total > 0:
            resumen = df_enc.groupby('numeral_regimen')['valor_mercado'].sum().reset_index()
            resumen['pct'] = resumen['valor_mercado'] / total
            df_lim = load_table('param_categorias_regimen')
            if not df_lim.empty:
                col_l = 'limite_vida'
                df_lim['numeral_regimen'] = df_lim['numeral_regimen'].astype(str)
                resumen['numeral_regimen'] = resumen['numeral_regimen'].astype(str)
                resumen = resumen.merge(df_lim[['numeral_regimen', col_l]], on='numeral_regimen', how='left')
                resumen = resumen.rename(columns={col_l: 'limite'})
                resumen = resumen.dropna(subset=['limite'])
                resumen = resumen[resumen['limite'] < 1.0].sort_values('pct', ascending=False).head(12)
                if not resumen.empty:
                    fig_reg.add_trace(go.Bar(name='% Actual', x=resumen['numeral_regimen'], y=resumen['pct'], marker_color=SB_COLORS[0]))
                    fig_reg.add_trace(go.Bar(name='% Límite', x=resumen['numeral_regimen'], y=resumen['limite'], marker_color=SB_COLORS[4]))
                    fig_reg.update_layout(barmode='group', title='Consumo Límites Régimen', yaxis_tickformat='.0%', height=320, margin=dict(l=10, r=10, t=40, b=10))

    fig_plazo = go.Figure()
    if 'tramo_plazo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('tramo_plazo')['valor_mercado'].sum().reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        fig_plazo = go.Figure(go.Bar(
            x=r['tramo_plazo'], y=r['vm_mm'],
            marker_color=SB_COLORS[1], text=r['valor_mercado'].apply(_fmt_cop),
            textposition='outside', textfont=dict(size=9)))
        fig_plazo.update_layout(
            title='Valor por Tramo de Plazo', yaxis_title='MdM COP',
            height=320, margin=dict(l=10, r=10, t=40, b=30))

    # Tabla resumen por compañía
    tabla_cia = None
    print(f"[PORTAFOLIO] df tiene {len(df)} filas, cols incluyen compania={'compania' in df.columns}, vm={'valor_mercado' in df.columns}")
    if 'compania' in df.columns and 'valor_mercado' in df.columns:
        try:
            df_cia = df[df['compania'].notna()].copy()
            vm_total = df_cia['valor_mercado'].sum()
            if vm_total > 0:
                resumen_cia = df_cia.groupby('compania').agg(
                    vm_total=('valor_mercado', 'sum'),
                    n_titulos=('especie', 'count'),
                ).reset_index().sort_values('vm_total', ascending=False)
                resumen_cia['pct'] = (resumen_cia['vm_total'] / vm_total * 100)
                resumen_cia['vm_fmt'] = resumen_cia['vm_total'].apply(_fmt_cop)
                resumen_cia['pct_fmt'] = resumen_cia['pct'].apply(lambda x: f"{x:.1f}%")
                resumen_cia['n_titulos'] = resumen_cia['n_titulos'].astype(int)
                print(f"[PORTAFOLIO] Tabla compañía creada: {len(resumen_cia)} filas")
                tabla_cia = html.Table([
                    html.Thead(html.Tr([
                        html.Th("Compañía", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                        html.Th("VM Total", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                        html.Th("# Títulos", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                        html.Th("% Portafolio", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                    ])),
                    html.Tbody([
                        html.Tr([
                            html.Td(row['compania'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                            html.Td(row['vm_fmt'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                            html.Td(str(row['n_titulos']), style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                            html.Td(row['pct_fmt'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                        ], style={'backgroundColor': '#f0faf5' if i % 2 == 1 else 'white'})
                        for i, (_, row) in enumerate(resumen_cia.iterrows())
                    ]),
                ], style={'width': '100%', 'borderCollapse': 'collapse', 'border': '1px solid #dee2e6'})
        except Exception as e:
            print(f"[AXIS] Error creando tabla compañía: {e}")
            tabla_cia = html.Span(f"Error: {e}", style={'color': 'red', 'fontSize': '10px'})

    return html.Div([
        # Filtros
        html.Div([
            html.Div([
                html.Label("Compañía"),
                dcc.Dropdown(id='filtro-compania', options=opts_compania, value='Todas', clearable=False),
            ], style={'width': '30%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginRight': '2%'}),
            html.Div([
                html.Label("Encaje"),
                dcc.Dropdown(id='filtro-encaje', options=[{'label': e, 'value': e} for e in ['Todos', 'SI', 'NO']], value='Todos', clearable=False),
            ], style={'width': '30%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginRight': '2%'}),
            html.Div([
                html.Label("Ramo"),
                dcc.Dropdown(id='filtro-ramo', options=opts_ramo, value='Todos', clearable=False),
            ], style={'width': '30%', 'display': 'inline-block', 'verticalAlign': 'top'}),
        ], style={'marginBottom': '15px'}),
        html.Div(id='portafolio-count', children=count, style={'fontWeight': 'bold', 'marginBottom': '10px'}),
        # === SPLIT VERTICAL: Actual (izq) + Proyectado (der) ===
        html.Div([
            # Columna izquierda: Portafolio Actual
            html.Div(id='portafolio-actual-container', children=[
                html.H5("📊 Actual", style={'textAlign': 'center', 'color': '#004d2a', 'fontSize': '14px', 'marginBottom': '5px'}),
                # Tabla resumen por compañía (dentro de la columna actual)
                html.Div(id='tabla-resumen-cia-container', children=[
                    html.H6("Resumen por Compañía", style={'marginBottom': '6px', 'color': '#004d2a', 'fontSize': '13px'}),
                    tabla_cia if tabla_cia else html.Div(),
                ], style={'marginBottom': '10px'}),
                # Grid 2x2 para las gráficas
                html.Div([
                    html.Div([dcc.Graph(id='grafico-ac', figure=fig_ac, config={'responsive': True},
                                        style={'height': '100%'})],
                             style={'gridColumn': '1', 'gridRow': '1', 'overflow': 'hidden'}),
                    html.Div([dcc.Graph(id='grafico-solv', figure=fig_solv, config={'responsive': True},
                                        style={'height': '100%'})],
                             style={'gridColumn': '2', 'gridRow': '1', 'overflow': 'hidden'}),
                    html.Div([dcc.Graph(id='grafico-reg', figure=fig_reg, config={'responsive': True},
                                        style={'height': '100%'})],
                             style={'gridColumn': '1', 'gridRow': '2', 'overflow': 'hidden'}),
                    html.Div([dcc.Graph(id='grafico-plazo', figure=fig_plazo, config={'responsive': True},
                                        style={'height': '100%'})],
                             style={'gridColumn': '2', 'gridRow': '2', 'overflow': 'hidden'}),
                ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr',
                          'gridTemplateRows': 'auto auto', 'gap': '4px'}),
            ], style={'flex': '1 1 100%', 'minWidth': '0'}),
            # Columna derecha: Portafolio Proyectado (se llena vía callback)
            html.Div(id='portafolio-proyectado-container', children=[],
                     style={'display': 'none'}),
        ], id='portafolio-split-wrapper', style={'display': 'flex', 'gap': '10px'}),
    ], style={'padding': '10px', 'overflowY': 'auto'})


_cache_clasificacion = {'fecha': None, 'df': None}

def _cargar_portafolio_fecha(fecha_inicio):
    """Helper: carga portafolio clasificado para la fecha del sidebar."""
    global _cache_clasificacion
    fecha_ref = pd.to_datetime(fecha_inicio).strftime('%Y-%m-%d') if fecha_inicio else None

    # Cache hit → devolver directo
    if fecha_ref and _cache_clasificacion['fecha'] == fecha_ref and _cache_clasificacion['df'] is not None:
        return _cache_clasificacion['df']

    df = pd.DataFrame()
    try:
        conn = get_conn()
        df = pd.read_sql_query("SELECT * FROM portafolio_clasificado", conn)
        conn.close()
    except Exception:
        df = load_table('portafolio_clasificado')

    # Filtrar por fecha si hay datos de esa fecha
    if fecha_ref and 'fecha_reporte' in df.columns:
        df_fecha = df[df['fecha_reporte'].astype(str).str[:10] == fecha_ref]
        if not df_fecha.empty:
            df = df_fecha

    for col in ['nominal', 'valor_mercado', 'valor_compra', 'tasa_compra', 'valor_tir']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Excluir colaterales (recibido/enviado) y simultáneas para las gráficas
    if 'especie' in df.columns:
        df = df[~df['especie'].str.contains('(?i)recibido|enviado', na=False)]
    if 'subtipo_vehiculo' in df.columns:
        df = df[df['subtipo_vehiculo'] != 'Repo_Simultanea']

    _cache_clasificacion['fecha'] = fecha_ref
    _cache_clasificacion['df'] = df

    return df


@callback(Output('filtro-ramo', 'options'),
          Input('filtro-compania', 'value'),
          State('sidebar-fecha-inicio', 'date'),
          prevent_initial_call=True)
def update_ramos(compania, fecha_inicio):
    df = _cargar_portafolio_fecha(fecha_inicio)
    if compania and compania != 'Todas' and 'compania' in df.columns:
        df = df[df['compania'] == compania]
    ramos = ['Todos'] + sorted(df['ramo'].dropna().unique().tolist()) if 'ramo' in df.columns else ['Todos']
    return [{'label': r, 'value': r} for r in ramos]


def _build_tabla_cia(df):
    """Genera la tabla HTML de resumen por compañía."""
    children = [html.H6("Resumen por Compañía", style={'marginBottom': '6px', 'color': '#004d2a', 'fontSize': '13px'})]
    if 'compania' in df.columns and 'valor_mercado' in df.columns:
        df_cia = df[df['compania'].notna()].copy()
        vm_total = df_cia['valor_mercado'].sum()
        if vm_total > 0:
            resumen_cia = df_cia.groupby('compania').agg(
                vm_total=('valor_mercado', 'sum'),
                n_titulos=('especie', 'count'),
            ).reset_index().sort_values('vm_total', ascending=False)
            resumen_cia['pct'] = (resumen_cia['vm_total'] / vm_total * 100)
            resumen_cia['vm_fmt'] = resumen_cia['vm_total'].apply(_fmt_cop)
            resumen_cia['pct_fmt'] = resumen_cia['pct'].apply(lambda x: f"{x:.1f}%")
            resumen_cia['n_titulos'] = resumen_cia['n_titulos'].astype(int)
            children.append(html.Table([
                html.Thead(html.Tr([
                    html.Th("Compañía", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                    html.Th("VM Total", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                    html.Th("# Títulos", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                    html.Th("% Portafolio", style={'padding': '8px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
                ])),
                html.Tbody([
                    html.Tr([
                        html.Td(row['compania'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                        html.Td(row['vm_fmt'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                        html.Td(str(row['n_titulos']), style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                        html.Td(row['pct_fmt'], style={'padding': '6px', 'textAlign': 'center', 'fontSize': '11px'}),
                    ], style={'backgroundColor': '#f0faf5' if i % 2 == 1 else 'white'})
                    for i, (_, row) in enumerate(resumen_cia.iterrows())
                ]),
            ], style={'width': '100%', 'borderCollapse': 'collapse', 'border': '1px solid #dee2e6'}))
    return children


@callback(
    [Output('filtro-compania', 'options'),
     Output('portafolio-count', 'children'),
     Output('grafico-ac', 'figure'),
     Output('grafico-solv', 'figure'),
     Output('grafico-reg', 'figure'),
     Output('grafico-plazo', 'figure'),
     Output('tabla-resumen-cia-container', 'children')],
    [Input('filtro-compania', 'value'),
     Input('filtro-encaje', 'value'),
     Input('filtro-ramo', 'value')],
    State('sidebar-fecha-inicio', 'date'),
    prevent_initial_call=True
)
def update_graficos(compania, encaje, ramo, fecha_inicio):
    df = _cargar_portafolio_fecha(fecha_inicio)

    # Opciones de compañía (actualizar dinámicamente)
    companias = ['Todas'] + sorted(df['compania'].dropna().unique().tolist()) if 'compania' in df.columns else ['Todas']
    opts_compania = [{'label': c, 'value': c} for c in companias]

    # Aplicar filtros
    if compania and compania != 'Todas' and 'compania' in df.columns:
        df = df[df['compania'] == compania]
    if encaje and encaje != 'Todos' and 'encaje' in df.columns:
        df = df[df['encaje'] == encaje]
    if ramo and ramo != 'Todos' and 'ramo' in df.columns:
        df = df[df['ramo'] == ramo]

    # Paleta Seguros Bolívar
    SB_COLORS = ['#008f4f', '#00b864', '#66cc99', '#004d2a', '#c8a415', '#f2d024',
                 '#2c3e50', '#5dade2', '#af7ac5', '#e74c3c', '#f39c12', '#1abc9c']

    count = f"{len(df)} posiciones"
    if 'fecha_reporte' in df.columns and not df.empty:
        fecha_real = pd.to_datetime(df['fecha_reporte'].iloc[0]).strftime('%Y-%m-%d')
        count = f"{len(df)} posiciones — Fecha datos: {fecha_real}"

    # Asset Class
    fig_ac = go.Figure()
    if 'asset_class_definitivo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('asset_class_definitivo')['valor_mercado'].sum().sort_values(ascending=True).reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        r['label'] = r['valor_mercado'].apply(_fmt_cop)
        fig_ac = go.Figure(go.Bar(
            y=r['asset_class_definitivo'], x=r['vm_mm'], orientation='h',
            marker_color=SB_COLORS[0], text=r['label'], textposition='outside',
            textfont=dict(size=9)))
        fig_ac.update_layout(title='Valor por Asset Class', xaxis_title='MdM COP',
                             height=320, margin=dict(l=10, r=80, t=40, b=10), xaxis_tickformat=',.0f')

    # Solvencia
    fig_solv = go.Figure()
    if 'categoria' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('categoria')['valor_mercado'].sum().reset_index()
        r['pct'] = r['valor_mercado'] / r['valor_mercado'].sum() * 100
        r['etiqueta'] = r.apply(lambda row: f"{row['categoria']}<br>{_fmt_cop(row['valor_mercado'])} ({row['pct']:.1f}%)", axis=1)
        fig_solv = go.Figure(go.Pie(
            labels=r['etiqueta'], values=r['valor_mercado'],
            hole=0.4, marker=dict(colors=SB_COLORS[:len(r)]),
            textinfo='label', textposition='outside',
            textfont=dict(size=10), insidetextorientation='radial'))
        fig_solv.update_layout(title='Distribución por Categoría Solvencia',
                               height=320, margin=dict(l=10, r=10, t=40, b=10), showlegend=False)

    # Régimen
    fig_reg = go.Figure()
    if 'numeral_regimen' in df.columns and 'valor_mercado' in df.columns:
        df_enc = df[df['encaje'] == 'SI'] if 'encaje' in df.columns else df
        total = df_enc['valor_mercado'].sum()
        if total > 0:
            resumen = df_enc.groupby('numeral_regimen')['valor_mercado'].sum().reset_index()
            resumen['pct'] = resumen['valor_mercado'] / total
            df_lim = load_table('param_categorias_regimen')
            if not df_lim.empty:
                col_l = 'limite_vida' if compania == 'Bolívar' else 'limite_generales_cap'
                df_lim['numeral_regimen'] = df_lim['numeral_regimen'].astype(str)
                resumen['numeral_regimen'] = resumen['numeral_regimen'].astype(str)
                resumen = resumen.merge(df_lim[['numeral_regimen', col_l]], on='numeral_regimen', how='left')
                resumen = resumen.rename(columns={col_l: 'limite'})
                resumen = resumen.dropna(subset=['limite'])
                resumen = resumen[resumen['limite'] < 1.0].sort_values('pct', ascending=False).head(12)
                if not resumen.empty:
                    fig_reg.add_trace(go.Bar(name='% Actual', x=resumen['numeral_regimen'], y=resumen['pct'], marker_color=SB_COLORS[0]))
                    fig_reg.add_trace(go.Bar(name='% Límite', x=resumen['numeral_regimen'], y=resumen['limite'], marker_color=SB_COLORS[4]))
                    fig_reg.update_layout(barmode='group', title='Consumo Límites Régimen', yaxis_tickformat='.0%', height=320, margin=dict(l=10, r=10, t=40, b=10))

    # Plazo
    fig_plazo = go.Figure()
    if 'tramo_plazo' in df.columns and 'valor_mercado' in df.columns:
        r = df.groupby('tramo_plazo')['valor_mercado'].sum().reset_index()
        r['vm_mm'] = r['valor_mercado'] / 1e9
        fig_plazo = go.Figure(go.Bar(
            x=r['tramo_plazo'], y=r['vm_mm'],
            marker_color=SB_COLORS[1], text=r['valor_mercado'].apply(_fmt_cop),
            textposition='outside', textfont=dict(size=9)))
        fig_plazo.update_layout(title='Valor por Tramo de Plazo', yaxis_title='MdM COP',
                                height=320, margin=dict(l=10, r=10, t=40, b=30))

    return opts_compania, count, fig_ac, fig_solv, fig_reg, fig_plazo, _build_tabla_cia(df)


# =============================================================================
# CALLBACK: RENDERIZAR PORTAFOLIO PROYECTADO (Split derecho)
# =============================================================================
@callback(
    [Output('portafolio-proyectado-container', 'children'),
     Output('portafolio-proyectado-container', 'style'),
     Output('portafolio-actual-container', 'style')],
    Input('store-resultados-escenario', 'data'),
    prevent_initial_call=True
)
def renderizar_portafolio_proyectado(data):
    """Cuando se corre un escenario, muestra el portafolio proyectado al lado del actual."""
    if not data or not data.get('portafolio_proyectado'):
        # Sin proyección: actual ocupa 100%
        return [], {'display': 'none'}, {'flex': '1 1 100%'}

    params = data.get('parametros', {})
    fecha_fin = params.get('fecha_fin', '')

    df_proy = pd.DataFrame(data['portafolio_proyectado'])
    if df_proy.empty:
        return [], {'display': 'none'}, {'flex': '1 1 100%'}

    # Asegurar tipos numéricos
    df_proy['valor_mercado'] = pd.to_numeric(df_proy['valor_mercado'], errors='coerce').fillna(0)

    # Generar gráficas del portafolio proyectado
    titulo_proy = f"📈 Proyectado — {fecha_fin}"
    graficas_proy = _generar_graficas_portafolio(df_proy, titulo_proy, id_suffix='-proy')

    # Estilo split: ambas columnas ~48% con flex
    style_proy = {'flex': '1 1 48%', 'borderLeft': '2px solid #008f4f', 'paddingLeft': '10px'}
    style_actual = {'flex': '1 1 48%'}

    return [graficas_proy], style_proy, style_actual


# =============================================================================
# PESTAÑA 3: ALERTAS
# =============================================================================
def layout_alertas():
    df = load_table('diccionario_clasificado')
    df_port = load_table('portafolio_clasificado')
    df_calif = load_table('param_calificaciones_faltantes')

    alertas_html = []

    # Alerta calificaciones desactualizadas
    if not df_calif.empty and 'fecha_actualizacion' in df_calif.columns:
        ultima = df_calif['fecha_actualizacion'].max()
        if pd.isna(ultima) or ultima < (datetime.now() - timedelta(days=30)):
            fecha_str = str(ultima.date()) if pd.notna(ultima) else 'NUNCA'
            alertas_html.append(html.Li(f"🟠 Calificaciones faltantes desactualizadas (última: {fecha_str})", style={'color': '#e65100'}))

    # Alertas diccionario
    if not df.empty:
        # Excluir colaterales (enviado/recibido)
        df_alertable = df[~df['especie'].str.contains('enviado|recibido', case=False, na=False)]
        sin_sub = df_alertable[df_alertable['subtipo_vehiculo'].isin(['Sin Clasificar', '']) | df_alertable['subtipo_vehiculo'].isna()]
        if len(sin_sub) > 0:
            alertas_html.append(html.Li(f"🔴 {len(sin_sub)} título(s) sin subtipo_vehiculo"))
        sin_reg = df_alertable[df_alertable['numeral_regimen'].isin(['SIN_NUMERAL', '']) | df_alertable['numeral_regimen'].isna()]
        if len(sin_reg) > 0:
            alertas_html.append(html.Li(f"🟠 {len(sin_reg)} título(s) sin numeral_regimen"))

    # Alertas portafolio
    sin_solv_data = []
    sin_ac_data = []
    if not df_port.empty and 'encaje' in df_port.columns:
        df_enc = df_port[df_port['encaje'] == 'SI']
        if 'clasificacion_solvencia' in df_enc.columns:
            sin_cat = df_enc[df_enc['clasificacion_solvencia'].isna() | (df_enc['clasificacion_solvencia'] == '') | (df_enc['clasificacion_solvencia'] == 'USAR_MATRIZ')]
            if len(sin_cat) > 0:
                alertas_html.append(html.Li(f"🔴 {len(sin_cat)} posición(es) sin clasificación de solvencia"))
                sin_solv_data = sin_cat[['especie', 'emisor', 'isin', 'calificacion']].drop_duplicates().head(20).to_dict('records')
        if 'asset_class_definitivo' in df_enc.columns:
            sin_ac = df_enc[df_enc['asset_class_definitivo'].isin(['', 'Falta_Regla_Matriz', 'Sin Clasificar']) | df_enc['asset_class_definitivo'].isna()]
            if len(sin_ac) > 0:
                alertas_html.append(html.Li(f"🔴 {len(sin_ac)} posición(es) sin asset_class_definitivo"))
                sin_ac_data = sin_ac[['especie', 'emisor', 'isin', 'subtipo_vehiculo']].drop_duplicates().to_dict('records')

    children = []
    if not alertas_html:
        children.append(html.H4("✅ Todo clasificado correctamente.", style={'color': 'green'}))
    else:
        children.append(html.H4(f"{len(alertas_html)} alerta(s):"))
        children.append(html.Ul(alertas_html))

    # Tabla editable de calificaciones (resolver solvencia)
    if sin_solv_data:
        children.append(html.Hr())
        children.append(html.H5("🏛️ Resolver Solvencia — Editar Calificación"))
        columns_calif = [
            {'name': 'especie', 'id': 'especie', 'editable': False},
            {'name': 'emisor', 'id': 'emisor', 'editable': False},
            {'name': 'isin', 'id': 'isin', 'editable': False},
            {'name': 'calificacion', 'id': 'calificacion', 'editable': False},
            {'name': '⚠️ Nueva Calificación', 'id': 'nueva_calificacion', 'editable': True, 'presentation': 'dropdown'},
        ]
        for row in sin_solv_data:
            row['nueva_calificacion'] = ''

        children.append(dash_table.DataTable(
            id='tabla-calificaciones',
            columns=columns_calif,
            data=sin_solv_data,
            dropdown={'nueva_calificacion': {'options': [{'label': c, 'value': c} for c in CALIFICACIONES]}},
            editable=True,
            page_size=10,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            style_data_conditional=[{'if': {'column_id': 'nueva_calificacion'}, 'backgroundColor': '#fff3cd'}],
        ))
        children.append(html.Br())
        children.append(html.Button("💾 Guardar Calificaciones", id='btn-guardar-calif', n_clicks=0))
        children.append(html.Div(id='msg-guardar-calif'))

    # Tabla de calificaciones faltantes (solo si desactualizadas > 30 días)
    calif_desactualizada = False
    if not df_calif.empty and 'fecha_actualizacion' in df_calif.columns:
        ultima = pd.to_datetime(df_calif['fecha_actualizacion'], errors='coerce').max()
        if pd.isna(ultima) or ultima < (datetime.now() - timedelta(days=30)):
            calif_desactualizada = True

    if not df_calif.empty and calif_desactualizada:
        children.append(html.Hr())
        children.append(html.H5("📋 Calificaciones Faltantes — Actualizar"))
        calif_data = df_calif[['emisor', 'calificacion', 'fecha_actualizacion']].to_dict('records')
        for row in calif_data:
            row['nueva_calificacion'] = row.get('calificacion', '')

        columns_calif_falt = [
            {'name': 'emisor', 'id': 'emisor', 'editable': False},
            {'name': 'calificacion actual', 'id': 'calificacion', 'editable': False},
            {'name': 'última actualización', 'id': 'fecha_actualizacion', 'editable': False},
            {'name': '⚠️ Nueva Calificación', 'id': 'nueva_calificacion', 'editable': True, 'presentation': 'dropdown'},
        ]
        children.append(dash_table.DataTable(
            id='tabla-calif-faltantes',
            columns=columns_calif_falt,
            data=calif_data,
            dropdown={'nueva_calificacion': {'options': [{'label': c, 'value': c} for c in CALIFICACIONES]}},
            editable=True,
            page_size=15,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            style_data_conditional=[{'if': {'column_id': 'nueva_calificacion'}, 'backgroundColor': '#fff3cd'}],
        ))
        children.append(html.Br())
        children.append(html.Button("💾 Actualizar Calificaciones Faltantes", id='btn-guardar-calif-falt', n_clicks=0))
        children.append(html.Div(id='msg-guardar-calif-falt'))

    # Botón re-clasificar
    children.append(html.Hr())
    children.append(html.Button("🔄 Re-clasificar Portafolio", id='btn-reclasificar-alertas', n_clicks=0))
    children.append(html.Div(id='msg-reclasificar-alertas'))

    return html.Div(children, style={'padding': '10px'})


@callback(Output('msg-guardar-calif', 'children'),
          Input('btn-guardar-calif', 'n_clicks'),
          State('tabla-calificaciones', 'data'),
          prevent_initial_call=True)
def guardar_calificaciones(n_clicks, data):
    if not data:
        return ""
    conn = get_conn()
    cambios = 0
    fecha_hoy = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for row in data:
        nueva = str(row.get('nueva_calificacion', '') or '').strip()
        if not nueva or nueva in ('', 'None'):
            continue
        isin = row.get('isin', '')
        emisor = str(row.get('emisor', '') or '').strip()
        # Actualizar hist_titulos
        if isin and str(isin) not in ('', 'None', 'nan'):
            conn.execute("UPDATE hist_titulos SET calificacion = ? WHERE isin = ?", (nueva, isin))
        # Actualizar param_calificaciones_faltantes
        existe = pd.read_sql_query("SELECT COUNT(*) as n FROM param_calificaciones_faltantes WHERE emisor = ?", conn, params=(emisor,))['n'].iloc[0]
        if existe > 0:
            conn.execute("UPDATE param_calificaciones_faltantes SET calificacion = ?, fecha_actualizacion = ? WHERE emisor = ?", (nueva, fecha_hoy, emisor))
        else:
            pd.DataFrame([{'emisor': emisor, 'calificacion': nueva, 'fecha_actualizacion': fecha_hoy}]).to_sql('param_calificaciones_faltantes', conn, if_exists='append', index=False)
        cambios += 1
    conn.commit()
    conn.close()
    if cambios > 0:
        return html.Span(f"✅ {cambios} calificación(es) guardadas. Re-clasifica para ver cambios.", style={'color': 'green'})
    return ""


@callback(Output('msg-guardar-calif-falt', 'children'),
          Input('btn-guardar-calif-falt', 'n_clicks'),
          State('tabla-calif-faltantes', 'data'),
          prevent_initial_call=True)
def guardar_calif_faltantes(n_clicks, data):
    if not data:
        return ""
    conn = get_conn()
    cambios = 0
    fecha_hoy = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for row in data:
        nueva = str(row.get('nueva_calificacion', '') or '').strip()
        emisor = str(row.get('emisor', '') or '').strip()
        if not emisor:
            continue
        # Siempre actualizar fecha; si cambió la calificación, actualizar también
        if nueva and nueva != row.get('calificacion', ''):
            conn.execute("UPDATE param_calificaciones_faltantes SET calificacion = ?, fecha_actualizacion = ? WHERE emisor = ?", (nueva, fecha_hoy, emisor))
        else:
            conn.execute("UPDATE param_calificaciones_faltantes SET fecha_actualizacion = ? WHERE emisor = ?", (fecha_hoy, emisor))
        cambios += 1
    conn.commit()
    conn.close()
    return html.Span(f"✅ Calificaciones actualizadas correctamente ({cambios} emisores).", style={'color': 'green'})


@callback(Output('msg-reclasificar-alertas', 'children'),
          Input('btn-reclasificar-alertas', 'n_clicks'),
          prevent_initial_call=True)
def reclasificar_from_alertas(n_clicks):
    return reclasificar_portafolio()


def reclasificar_portafolio():
    """Corre clasificar_portafolio directamente (sin subprocess a src/)."""
    try:
        conn = get_conn()
        # Intentar obtener fecha de portafolio_clasificado, si no de hist_titulos
        try:
            fecha_port = pd.read_sql_query("SELECT DISTINCT fecha_reporte FROM portafolio_clasificado LIMIT 1", conn)
            fecha = str(fecha_port['fecha_reporte'].iloc[0])[:10] if not fecha_port.empty else None
        except Exception:
            fecha = None
        if not fecha:
            fecha_port = pd.read_sql_query("SELECT MAX(fecha_reporte) as f FROM hist_titulos", conn)
            fecha = str(fecha_port['f'].iloc[0])[:10] if not fecha_port.empty and fecha_port['f'].iloc[0] else None
        conn.close()

        if not fecha:
            return html.Span("❌ No hay datos cargados para clasificar.", style={'color': 'red'})

        from clasificar_portafolio import clasificar_portafolio_diario
        resultado = clasificar_portafolio_diario(fecha)
        if resultado is not None and not resultado.empty:
            # Invalidar cache
            _cache_clasificacion['fecha'] = None
            _cache_clasificacion['df'] = None
            return html.Span("✅ Portafolio re-clasificado. Recarga la página.", style={'color': 'green'})
        else:
            return html.Span("❌ clasificar_portafolio no devolvió datos.", style={'color': 'red'})
    except Exception as e:
        return html.Span(f"❌ Error: {str(e)[:200]}", style={'color': 'red'})


# =============================================================================
# PESTANA 6: VALIDACION — Comparar valoracion AXIS vs VM del 597
# =============================================================================
def layout_validacion():
    return html.Div([
        html.H4("Validacion de Valoracion"),
        html.P("Compara el VM calculado por AXIS vs el VM reportado en el 597 (hist_titulos).",
               style={'fontSize': '12px', 'color': '#555'}),
        html.Div([
            html.Div([
                html.Label("UVR:", style={'fontSize': '11px'}),
                dcc.Input(id='val-uvr', type='number', value=obtener_uvr_para(None), step=0.01, style={'width': '90px', 'marginLeft': '5px'}),
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Div([
                html.Label("TRM:", style={'fontSize': '11px'}),
                dcc.Input(id='val-trm', type='number', value=obtener_trm_para(None), step=1, style={'width': '90px', 'marginLeft': '5px'}),
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
        ], style={'marginBottom': '10px', 'padding': '8px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
        html.Button("Correr Validacion", id='btn-validacion', n_clicks=0,
                    style={'backgroundColor': '#dc3545', 'color': 'white', 'border': 'none',
                           'padding': '8px 20px', 'borderRadius': '4px', 'cursor': 'pointer', 'fontWeight': 'bold'}),
        dcc.Loading(id='loading-validacion', type='circle', children=[
            html.Div(id='resultado-validacion', style={'marginTop': '15px'}),
        ]),
    ], style={'padding': '10px'})


@callback(
    Output('resultado-validacion', 'children'),
    Input('btn-validacion', 'n_clicks'),
    [State('sidebar-fecha-inicio', 'date'),
     State('val-uvr', 'value'),
     State('val-trm', 'value')],
    prevent_initial_call=True
)
def correr_validacion(n_clicks, fecha_inicio, valor_uvr, trm):
    """Valora cada titulo con la metodologia AXIS y compara vs VM del 597."""
    import sys, traceback
    try:
        return _correr_validacion_impl(fecha_inicio, valor_uvr, trm)
    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"Error: {e}", style={'color': 'red', 'fontWeight': 'bold'}),
            html.Pre(tb, style={'fontSize': '10px', 'maxHeight': '300px', 'overflow': 'auto'}),
        ])


def _correr_validacion_impl(fecha_inicio, valor_uvr, trm):
    import sys
    from motor_proyeccion import Titulo, valorar_titulo
    from curvas_rendimiento import extraer_curva_base_pip_db, interpolar_tasa, obtener_nombre_curva
    from valorar_bonos import cargar_serie_ipc

    if not fecha_inicio:
        return html.Span("⚠️ Selecciona una fecha de corte en el sidebar.", style={'color': '#e65100'})
    fecha_ref = pd.to_datetime(fecha_inicio).strftime('%Y-%m-%d')
    fecha_val = pd.to_datetime(fecha_ref).date()

    # Cargar datos
    conn = get_conn()
    df_hist = pd.read_sql_query(
        "SELECT * FROM hist_titulos WHERE fecha_reporte = ?", conn, params=(fecha_ref,))
    df_clasif = pd.read_sql_query(
        """SELECT id_posicion, asset_class_definitivo, tasa_referencia, tasa_facial,
                  periodicidad, tipo_tasa, uso_tasa, base_anual, compania
           FROM portafolio_clasificado""", conn)
    conn.close()

    if df_hist.empty:
        return html.Span("No hay datos en hist_titulos para esa fecha.", style={'color': 'red'})

    df_hist['id_posicion'] = df_hist['id_posicion'].astype(str).str.strip()
    df_clasif['id_posicion'] = df_clasif['id_posicion'].astype(str).str.strip()
    df_clasif = df_clasif.drop_duplicates(subset=['id_posicion'], keep='first')
    df = df_hist.merge(df_clasif, on='id_posicion', how='left', suffixes=('', '_pc'))

    for col in ['nominal', 'valor_mercado', 'tasa_compra', 'tasa_mercado', 'valor_tir']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Solo titulos con nominal > 0 y moneda local
    df = df[(df['nominal'] > 0) & (~df['moneda'].isin(['USD', 'US$', 'U']))]
    df = df[df['asset_class_definitivo'].notna() & (df['asset_class_definitivo'].str.strip() != '')]

    # Curvas PIP desde hist_pip para la fecha del sidebar
    curvas_pip = extraer_curva_base_pip_db(str(DB_PATH), fecha_ref)

    # Serie IPC
    try:
        # Buscar archivo con patron (evitar problemas de encoding con tilde)
        ipc_files = list(SCRIPT_DIR.glob("Serie Inflaci*.xlsx"))
        if ipc_files:
            serie_ipc = cargar_serie_ipc(str(ipc_files[0]))
        else:
            serie_ipc = pd.DataFrame()
    except Exception:
        serie_ipc = pd.DataFrame()

    resultados = []
    for _, row in df.iterrows():
        ac = str(row.get('asset_class_definitivo', '') or '').strip()
        tasa_ref = str(row.get('tasa_referencia', '') or '').strip()
        moneda = str(row.get('moneda', 'COP')).strip()
        clasificacion = str(row.get('clasificacion_contable', '1CP')).strip()

        # Tipo bono
        if moneda == 'UVR' or 'UVR' in ac:
            tipo_bono = 'UVR'
        elif tasa_ref == 'IPC' or 'IPC' in ac:
            tipo_bono = 'IPC'
        else:
            tipo_bono = 'TF'

        # Frecuencia
        per = str(row.get('periodicidad', '') or '').strip().upper()
        freq_map = {'TRIMESTRAL': 'trimestral', 'SEMESTRAL': 'semestral',
                    'ANUAL': 'anual', 'MENSUAL': 'mensual', 'AV': 'anual'}
        frecuencia = freq_map.get(per, 'anual')

        # Fechas
        try:
            fv = pd.Timestamp(row['fecha_vencimiento'])
            if pd.isna(fv):
                continue
            fv = fv.date()
        except Exception:
            continue
        try:
            fe = pd.Timestamp(row['fecha_emision'])
            if pd.isna(fe):
                from dateutil.relativedelta import relativedelta as rd
                fe = fv - rd(years=10)
            else:
                fe = fe.date()
        except Exception:
            from dateutil.relativedelta import relativedelta as rd
            fe = fv - rd(years=10)

        # Tasa facial
        try:
            facial_raw = str(row.get('tasa_facial', '0') or '0')
            facial = float(pd.to_numeric(facial_raw.split('-')[0].split('N')[0].strip(), errors='coerce') or 0)
        except Exception:
            facial = 0.0

        titulo = Titulo(
            id_posicion=str(row['id_posicion']),
            especie=str(row.get('especie', '')).strip(),
            emisor=str(row.get('emisor', '')).strip(),
            isin=str(row.get('isin', '')).strip(),
            moneda=moneda,
            fecha_emision=fe,
            fecha_vencimiento=fv,
            nominal=float(row['nominal']),
            tasa_cupon=facial,
            spread_ipc=facial if tipo_bono == 'IPC' else 0,
            tipo_bono=tipo_bono,
            frecuencia=frecuencia,
            tipo_tasa=str(row.get('tipo_tasa', 'EFECTIVO') or 'EFECTIVO').strip(),
            base_anual=float(row['base_anual']) if pd.notna(row.get('base_anual')) else 365,
            uso_tasa=str(row.get('uso_tasa', 'ACTUAL') or 'ACTUAL').strip(),
            clasificacion=clasificacion,
            compania=str(row.get('compania', '')).strip(),
            ramo='',
            portafolio='',
            asset_class=ac,
            tasa_compra=float(row['tasa_compra']),
            tasa_mercado=float(row.get('tasa_mercado', 0) or 0),
            valor_mercado=float(row['valor_mercado']),
            valor_tir=float(row['valor_tir']),
        )

        # Tasa de valoracion
        if clasificacion == '1VC':
            tasa_val = titulo.tasa_compra
        else:
            # Para 1CP/1DV: usar tasa_mercado del 597 directamente (es la tasa PIP del dia)
            tasa_val = titulo.tasa_mercado if titulo.tasa_mercado > 0 else titulo.tasa_compra

        if tasa_val <= 0:
            tasa_val = titulo.tasa_compra if titulo.tasa_compra > 0 else 8.0

        # Valorar
        try:
            if tipo_bono == 'IPC' and clasificacion == '1VC':
                # IPC al vencimiento: usar valorar_ipc_vencimiento con valor_tir del 597
                from valorar_bonos import valorar_ipc_vencimiento
                valor_inv_base100 = (titulo.valor_tir / titulo.nominal) * 100.0 if titulo.nominal > 0 else 100.0
                res_ipc = valorar_ipc_vencimiento(
                    spread=titulo.spread_ipc,
                    valor_inversion=valor_inv_base100,
                    fecha_emision=titulo.fecha_emision,
                    fecha_vencimiento=titulo.fecha_vencimiento,
                    serie_ipc=serie_ipc,
                    uso_tasa="I" if titulo.uso_tasa in ("I", "ACTUAL") else "F",
                    valor_nominal=100.0,
                    frecuencia=titulo.frecuencia,
                    fecha_valuacion=fecha_val,
                )
                vm_axis = titulo.nominal * res_ipc['precio_sucio'] / 100.0
            else:
                vm_axis = valorar_titulo(
                    titulo=titulo, tasa_mercado_nueva=tasa_val,
                    fecha_valuacion=fecha_val, valor_uvr=valor_uvr, trm=trm, serie_ipc=serie_ipc,
                )
        except Exception:
            vm_axis = 0.0

        vm_597 = float(row['valor_mercado'])
        diff = vm_axis - vm_597
        diff_pct = (diff / vm_597 * 100) if vm_597 != 0 else 0

        resultados.append({
            'id_posicion': titulo.id_posicion,
            'especie': titulo.especie[:30],
            'tipo_bono': tipo_bono,
            'clasificacion': clasificacion,
            'asset_class': ac[:15],
            'nominal': titulo.nominal,
            'vm_597': round(vm_597, 0),
            'vm_axis': round(vm_axis, 0),
            'diff': round(diff, 0),
            'diff_pct': round(diff_pct, 2),
            'tasa_usada': round(tasa_val, 4),
        })

    df_res = pd.DataFrame(resultados)
    if df_res.empty:
        return html.Span("No se pudo valorar ningun titulo.", style={'color': 'red'})

    # Metricas resumen
    total_597 = df_res['vm_597'].sum()
    total_axis = df_res['vm_axis'].sum()
    diff_total = total_axis - total_597
    n_titulos = len(df_res)
    n_ok = len(df_res[df_res['diff_pct'].abs() < 1.0])
    n_warn = len(df_res[(df_res['diff_pct'].abs() >= 1.0) & (df_res['diff_pct'].abs() < 5.0)])
    n_error = len(df_res[df_res['diff_pct'].abs() >= 5.0])

    # Top 20 mayores diferencias absolutas
    df_top = df_res.reindex(df_res['diff'].abs().sort_values(ascending=False).index).head(20)

    resumen = html.Div([
        html.Div([
            html.Div([
                html.H6(f"VM 597: ${total_597/1e9:,.1f} MdM", style={'margin': '0'}),
                html.H6(f"VM AXIS: ${total_axis/1e9:,.1f} MdM", style={'margin': '0'}),
                html.H6(f"Diferencia: ${diff_total/1e9:,.2f} MdM ({diff_total/total_597*100:.2f}%)",
                         style={'margin': '0', 'color': 'green' if abs(diff_total/total_597) < 0.01 else 'red'}),
            ], style={'display': 'inline-block', 'marginRight': '40px', 'verticalAlign': 'top'}),
            html.Div([
                html.Span(f"OK (<1%): {n_ok}", style={'color': 'green', 'marginRight': '15px'}),
                html.Span(f"Warn (1-5%): {n_warn}", style={'color': 'orange', 'marginRight': '15px'}),
                html.Span(f"Error (>5%): {n_error}", style={'color': 'red'}),
                html.Br(),
                html.Span(f"Total: {n_titulos} titulos valorados"),
            ], style={'display': 'inline-block', 'verticalAlign': 'top'}),
        ], style={'marginBottom': '15px', 'padding': '10px', 'backgroundColor': '#f0f0f0', 'borderRadius': '4px'}),

        html.H6("Top 20 mayores diferencias absolutas:"),
        dash_table.DataTable(
            data=df_top.to_dict('records'),
            columns=[
                {'name': 'ID', 'id': 'id_posicion'},
                {'name': 'Especie', 'id': 'especie'},
                {'name': 'Tipo', 'id': 'tipo_bono'},
                {'name': 'Clasif', 'id': 'clasificacion'},
                {'name': 'AC', 'id': 'asset_class'},
                {'name': 'VM 597', 'id': 'vm_597', 'type': 'numeric', 'format': {'specifier': ',.0f'}},
                {'name': 'VM AXIS', 'id': 'vm_axis', 'type': 'numeric', 'format': {'specifier': ',.0f'}},
                {'name': 'Diff', 'id': 'diff', 'type': 'numeric', 'format': {'specifier': ',.0f'}},
                {'name': 'Diff %', 'id': 'diff_pct', 'type': 'numeric', 'format': {'specifier': '.2f'}},
                {'name': 'Tasa', 'id': 'tasa_usada', 'type': 'numeric', 'format': {'specifier': '.4f'}},
            ],
            style_cell={'fontSize': '11px', 'padding': '4px', 'textAlign': 'right'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#e9ecef'},
            style_data_conditional=[
                {'if': {'filter_query': '{diff_pct} > 5 || {diff_pct} < -5'}, 'backgroundColor': '#ffe0e0'},
                {'if': {'filter_query': '{diff_pct} > 1 && {diff_pct} <= 5'}, 'backgroundColor': '#fff3cd'},
                {'if': {'filter_query': '{diff_pct} < -1 && {diff_pct} >= -5'}, 'backgroundColor': '#fff3cd'},
            ],
            page_size=20,
        ),
    ])
    return resumen


# =============================================================================
# PESTANA 4: INPUTS (Proyecciones) — Tabla editable + Grafico reactivo + Slider
# =============================================================================

# Allowlist de valores admitidos para la columna `excluir` de `param_portafolios`
# (Req 1.4). Se restringe la captura desde el dashboard a este conjunto vía
# dropdown; NO se captura compañía (se deriva por prefijo en el Clasificador).
EXCLUIR_OPCIONES = ('SI', 'NO')


def _detectar_altas_pendientes():
    """
    Detecta los Portafolio_Nuevo y Swap_Sin_Subyacente del corte actual.

    Compara el portafolio del corte (`portafolio_clasificado`) contra las tablas
    de parámetros existentes para identificar los elementos que aún no están
    dados de alta, reutilizando las funciones puras de `comun.validaciones_inputs`
    (patrón `detectar_incompletos`). Solo lee la base de datos; no escribe nada.

    - Portafolio_Nuevo: `portafolio` presente en el corte cuyo valor normalizado
      no existe en `param_portafolios.portafolio_porfin` (Req 1.1, 1.2).
    - Swap_Sin_Subyacente: `id_posicion` de posiciones con `clase_origen == 'SWAP'`
      presente en el corte cuyo valor no existe en
      `param_diccionario_swaps.id_posicion` (Req 2.1, 2.2).

    Returns:
        Tupla `(portafolios_nuevos, swaps_sin_subyacente)` de listas de `str`
        normalizados y ordenados. Ante cualquier fallo de lectura devuelve dos
        listas vacías, de modo que los controles simplemente no se muestran.
    """
    portafolios_nuevos: list[str] = []
    swaps_sin_subyacente: list[str] = []
    try:
        conn = get_conn()
        try:
            df_port = pd.read_sql_query(
                "SELECT portafolio, clase_origen, id_posicion "
                "FROM portafolio_clasificado", conn)
            try:
                df_pp = pd.read_sql_query(
                    "SELECT portafolio_porfin FROM param_portafolios", conn)
            except Exception:
                df_pp = pd.DataFrame(columns=['portafolio_porfin'])
            try:
                df_ds = pd.read_sql_query(
                    "SELECT id_posicion FROM param_diccionario_swaps", conn)
            except Exception:
                df_ds = pd.DataFrame(columns=['id_posicion'])
        finally:
            conn.close()
    except Exception:
        return [], []

    if df_port.empty:
        return [], []

    # Portafolios nuevos: todos los del corte ausentes de param_portafolios.
    portafolios_corte = df_port.get('portafolio', pd.Series(dtype=str)).tolist()
    registrados_pp = df_pp.get('portafolio_porfin', pd.Series(dtype=str)).tolist()
    portafolios_nuevos = detectar_portafolios_nuevos(portafolios_corte, registrados_pp)

    # Swaps sin subyacente: solo posiciones cuyo clase_origen es 'SWAP'.
    if 'clase_origen' in df_port.columns:
        mask_swap = df_port['clase_origen'].astype(str).str.strip().str.upper() == 'SWAP'
        swaps_corte = df_port.loc[mask_swap, 'id_posicion'].tolist()
    else:
        swaps_corte = []
    registrados_ds = df_ds.get('id_posicion', pd.Series(dtype=str)).tolist()
    swaps_sin_subyacente = detectar_swaps_sin_subyacente(swaps_corte, registrados_ds)

    return portafolios_nuevos, swaps_sin_subyacente


def _seccion_alta_portafolios(portafolios_nuevos):
    """
    Construye la sección de alta de portafolios nuevos (Req 1.1, 1.2, 1.4).

    Devuelve un `html.Div` con un `dash_table.DataTable` editable (una fila por
    Portafolio_Nuevo con su `portafolio_porfin` ya identificado y no editable, y
    columnas editables Ramo, Tipo y Excluir), un botón de guardar y un `html.Div`
    de mensaje. `excluir` es un dropdown restringido a {'SI','NO'}; NO se captura
    compañía. Si no hay portafolios nuevos, devuelve `None` para que el control
    no se muestre (Req 1.2).
    """
    if not portafolios_nuevos:
        return None

    filas = [
        {'portafolio_porfin': p, 'ramo': '', 'tipo': '', 'excluir': 'NO'}
        for p in portafolios_nuevos
    ]
    columnas = [
        {'name': 'Portafolio (Porfin)', 'id': 'portafolio_porfin', 'editable': False},
        {'name': '⚠️ Ramo', 'id': 'ramo', 'editable': True},
        {'name': '⚠️ Tipo', 'id': 'tipo', 'editable': True},
        {'name': '⚠️ Excluir', 'id': 'excluir', 'editable': True, 'presentation': 'dropdown'},
    ]
    return html.Div([
        html.H5(f"🆕 Alta de portafolios nuevos ({len(portafolios_nuevos)})"),
        html.P("Portafolios presentes en el corte que aún no están en "
               "param_portafolios. Indica Ramo, Tipo y Excluir. La compañía se "
               "deriva automáticamente por el prefijo del código.",
               style={'fontSize': '11px', 'color': '#666'}),
        dash_table.DataTable(
            id='tabla-portafolios-nuevos',
            columns=columnas,
            data=filas,
            dropdown={'excluir': {'options': [{'label': v, 'value': v} for v in EXCLUIR_OPCIONES]}},
            editable=True,
            page_size=20,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            style_data_conditional=[
                {'if': {'column_id': c}, 'backgroundColor': '#fff3cd'}
                for c in ('ramo', 'tipo', 'excluir')
            ],
        ),
        html.Br(),
        html.Button("💾 Guardar portafolios", id='btn-alta-portafolio', n_clicks=0),
        html.Div(id='msg-alta-portafolio', style={'marginTop': '10px'}),
    ], style={'marginBottom': '30px', 'padding': '15px',
              'border': '1px solid #ddd', 'borderRadius': '5px'})


def _seccion_asignar_subyacentes(swaps_sin_subyacente):
    """
    Construye la sección de asignación de subyacentes a swaps nuevos (Req 2.1, 2.2).

    Devuelve un `html.Div` con un `dash_table.DataTable` editable (una fila por
    Swap_Sin_Subyacente con su `id_posicion` ya identificado y no editable, y una
    columna editable de subyacentes en texto libre separados por '|'), un botón de
    guardar y un `html.Div` de mensaje. Si no hay swaps sin subyacente, devuelve
    `None` para que el control no se muestre (Req 2.2).
    """
    if not swaps_sin_subyacente:
        return None

    filas = [
        {'id_posicion': s, 'subyacentes': ''}
        for s in swaps_sin_subyacente
    ]
    columnas = [
        {'name': 'Swap (id_posicion)', 'id': 'id_posicion', 'editable': False},
        {'name': '⚠️ Subyacentes (id_posicion separados por |)', 'id': 'subyacentes', 'editable': True},
    ]
    return html.Div([
        html.H5(f"🔗 Asignar subyacentes a swaps nuevos ({len(swaps_sin_subyacente)})"),
        html.P("Swaps presentes en el corte que aún no tienen subyacente en "
               "param_diccionario_swaps. Indica entre 1 y 50 id_posicion de "
               "subyacente separados por '|' (deben existir en el corte).",
               style={'fontSize': '11px', 'color': '#666'}),
        dash_table.DataTable(
            id='tabla-swaps-sin-subyacente',
            columns=columnas,
            data=filas,
            editable=True,
            page_size=20,
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
            style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            style_data_conditional=[
                {'if': {'column_id': 'subyacentes'}, 'backgroundColor': '#fff3cd'},
            ],
        ),
        html.Br(),
        html.Button("💾 Guardar subyacentes", id='btn-asignar-subyacente', n_clicks=0),
        html.Div(id='msg-asignar-subyacente', style={'marginTop': '10px'}),
    ], style={'marginBottom': '30px', 'padding': '15px',
              'border': '1px solid #ddd', 'borderRadius': '5px'})


# =============================================================================
# Callbacks de alta de portafolios y asignación de subyacentes (Req 1.5-1.7,
# 2.4-2.7, 5.2, 5.5). Reutilizan las funciones puras de validación y la
# persistencia parametrizada/atómica de `comun.validaciones_inputs`.
# =============================================================================

# Mensaje genérico ante fallo de base de datos (rule-security / Req 5.5): no se
# expone el detalle interno de la excepción al usuario; el detalle queda para el
# log del servidor. El estado previo de la tabla se preserva por el rollback de
# las funciones `persistir_*`.
_MSG_ERROR_DB = ("La operación no se completó por un error al escribir en la "
                 "base de datos. No se guardó ningún cambio.")


def _leer_estado_altas():
    """
    Lee el estado de la base de datos necesario para validar las altas.

    Devuelve una tupla con:
    - `registrados_pp`: `portafolio_porfin` ya presentes en `param_portafolios`.
    - `registrados_ds`: `id_posicion` ya presentes en `param_diccionario_swaps`.
    - `ids_portafolio_corte`: todos los `id_posicion` del portafolio del corte
      (`portafolio_clasificado`), usados para verificar la existencia de los
      subyacentes indicados (Req 2.4).

    Solo lee la base de datos; no escribe nada. Ante cualquier fallo de lectura
    devuelve tres listas vacías para que la validación posterior rechace de forma
    segura (fail-closed) sin lanzar.
    """
    try:
        conn = get_conn()
        try:
            try:
                df_pp = pd.read_sql_query(
                    "SELECT portafolio_porfin FROM param_portafolios", conn)
                registrados_pp = df_pp['portafolio_porfin'].tolist()
            except Exception:
                registrados_pp = []
            try:
                df_ds = pd.read_sql_query(
                    "SELECT id_posicion FROM param_diccionario_swaps", conn)
                registrados_ds = df_ds['id_posicion'].tolist()
            except Exception:
                registrados_ds = []
            try:
                df_ids = pd.read_sql_query(
                    "SELECT id_posicion FROM portafolio_clasificado", conn)
                ids_portafolio_corte = df_ids['id_posicion'].tolist()
            except Exception:
                ids_portafolio_corte = []
        finally:
            conn.close()
    except Exception:
        return [], [], []
    return registrados_pp, registrados_ds, ids_portafolio_corte


@callback(Output('msg-alta-portafolio', 'children'),
          Output('tabla-portafolios-nuevos', 'data'),
          Input('btn-alta-portafolio', 'n_clicks'),
          State('tabla-portafolios-nuevos', 'data'),
          prevent_initial_call=True)
def alta_portafolio(n_clicks, filas):
    """
    Da de alta cada Portafolio_Nuevo con Ramo/Tipo/Excluir (Req 1.5, 1.6, 1.7).

    Para cada fila de la tabla editable valida con `validar_alta_portafolio`
    (campos requeridos, `excluir ∈ {'SI','NO'}`, no duplicar un
    `portafolio_porfin` ya registrado) y, si es válida, la persiste con
    `persistir_portafolio` (escritura parametrizada y atómica). Devuelve un
    mensaje que identifica los `portafolio_porfin` dados de alta o el error
    específico (campo faltante / duplicado), y un mensaje genérico ante fallo de
    base de datos, sin escribir datos parciales (el rollback vive en la
    persistencia).

    Returns:
        Tupla `(mensaje, data)` donde `data` es la tabla actualizada: se eliminan
        las filas ya dadas de alta y se conservan las que quedaron pendientes por
        error de validación, para que el usuario pueda corregirlas.
    """
    if not filas:
        return html.Span("No hay portafolios nuevos por dar de alta.",
                         style={'color': 'gray'}), no_update

    registrados_pp, _, _ = _leer_estado_altas()
    # Los ya registrados se acumulan en memoria para rechazar duplicados dentro
    # del mismo lote (dos filas con el mismo portafolio_porfin).
    existentes = set(registrados_pp)

    conn = get_conn()
    try:
        dados_de_alta: list[str] = []
        errores: list[str] = []
        filas_pendientes: list[dict] = []
        for fila in filas:
            ok, mensaje, payload = validar_alta_portafolio(
                fila.get('portafolio_porfin'),
                fila.get('ramo'),
                fila.get('tipo'),
                fila.get('excluir'),
                existentes,
            )
            if not ok:
                errores.append(mensaje)
                filas_pendientes.append(fila)
                continue
            try:
                persistir_portafolio(conn, payload)
            except Exception:
                # Fallo de escritura: la persistencia ya hizo rollback de esta
                # fila. Se aborta el lote con mensaje genérico (Req 5.5).
                return html.Span(_MSG_ERROR_DB, style={'color': 'red'}), no_update
            dados_de_alta.append(payload['portafolio_porfin'])
            existentes.add(payload['portafolio_porfin'])
    finally:
        conn.close()

    partes = []
    if dados_de_alta:
        partes.append(html.Span(
            "✅ Portafolios dados de alta: " + ", ".join(dados_de_alta) + ".",
            style={'color': 'green', 'display': 'block'}))
    for err in errores:
        partes.append(html.Span("⚠️ " + err,
                                style={'color': '#b8860b', 'display': 'block'}))
    if not partes:
        partes.append(html.Span("No se detectaron cambios.",
                                style={'color': 'gray'}))
    return html.Div(partes), filas_pendientes


@callback(Output('msg-asignar-subyacente', 'children'),
          Output('tabla-swaps-sin-subyacente', 'data'),
          Input('btn-asignar-subyacente', 'n_clicks'),
          State('tabla-swaps-sin-subyacente', 'data'),
          prevent_initial_call=True)
def asignar_subyacente(n_clicks, filas):
    """
    Asigna subyacentes a cada Swap_Sin_Subyacente (Req 2.4, 2.5, 2.6, 2.7).

    Para cada fila parsea los subyacentes con `parsear_subyacentes` y valida con
    `validar_asignacion_swap` (entre 1 y 50 subyacentes, todos existentes en el
    portafolio del corte, swap no registrado previamente). Si es válida, persiste
    con `persistir_subyacente` (escritura parametrizada y atómica). Devuelve un
    mensaje que identifica los `id_posicion` de swap asignados o el error
    específico (cero subyacentes / subyacentes inexistentes / swap ya
    registrado), y un mensaje genérico ante fallo de base de datos.

    Returns:
        Tupla `(mensaje, data)` donde `data` es la tabla actualizada: se eliminan
        las filas ya asignadas y se conservan las pendientes por error de
        validación.
    """
    if not filas:
        return html.Span("No hay swaps sin subyacente por asignar.",
                         style={'color': 'gray'}), no_update

    _, registrados_ds, ids_portafolio_corte = _leer_estado_altas()
    swaps_registrados = set(registrados_ds)

    conn = get_conn()
    try:
        asignados: list[str] = []
        errores: list[str] = []
        filas_pendientes: list[dict] = []
        for fila in filas:
            subyacentes = parsear_subyacentes(fila.get('subyacentes'))
            ok, mensaje, payload = validar_asignacion_swap(
                fila.get('id_posicion'),
                subyacentes,
                ids_portafolio_corte,
                swaps_registrados,
            )
            if not ok:
                errores.append(mensaje)
                filas_pendientes.append(fila)
                continue
            try:
                persistir_subyacente(conn, payload)
            except Exception:
                return html.Span(_MSG_ERROR_DB, style={'color': 'red'}), no_update
            asignados.append(payload['id_posicion'])
            swaps_registrados.add(payload['id_posicion'])
    finally:
        conn.close()

    partes = []
    if asignados:
        partes.append(html.Span(
            "✅ Subyacentes asignados a swaps: " + ", ".join(asignados) + ".",
            style={'color': 'green', 'display': 'block'}))
    for err in errores:
        partes.append(html.Span("⚠️ " + err,
                                style={'color': '#b8860b', 'display': 'block'}))
    if not partes:
        partes.append(html.Span("No se detectaron cambios.",
                                style={'color': 'gray'}))
    return html.Div(partes), filas_pendientes


def _generar_grafico_curvas_derivados():
    """Genera gráfica con curvas SWTIBR (r COP) y LUTIBR (q USD) continuas."""
    import plotly.graph_objects as go
    try:
        # Buscar el archivo PIP más reciente
        files = sorted(PROJECT_ROOT.glob("Curvaspip_Forward_LocalSimple_T+0_*.csv"))
        if not files:
            return go.Figure().update_layout(title="Sin archivo de curvas PIP")
        path = files[-1]
        c = pd.read_csv(path)
        c['Valor'] = pd.to_numeric(c['Valor'], errors='coerce')
        c['Plazo_en_Dias'] = pd.to_numeric(c['Plazo_en_Dias'], errors='coerce')
        c = c.dropna(subset=['Valor', 'Plazo_en_Dias'])

        # Convertir a continua
        def a_continua(df):
            plazo = df['Plazo_en_Dias'].values.astype(float)
            tasa = df['Valor'].values.astype(float)
            cont = np.log(1 + ((tasa / 100) * (365 / 360)) * plazo / 365) / (plazo / 365)
            return plazo / 365, cont * 100  # en años y %

        swtibr = c[c['Nombre_Indicador'] == 'SWTIBR'][['Plazo_en_Dias', 'Valor']]
        lutibr = c[c['Nombre_Indicador'] == 'LUTIBR'][['Plazo_en_Dias', 'Valor']]

        # Samplear cada 30 días para no sobrecargar la gráfica
        swtibr_s = swtibr.iloc[::30]
        lutibr_s = lutibr.iloc[::30]

        x_sw, y_sw = a_continua(swtibr_s)
        x_lu, y_lu = a_continua(lutibr_s)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x_sw, y=y_sw, mode='lines', name='SWTIBR (r COP)',
                                 line=dict(color='#008f4f', width=2)))
        fig.add_trace(go.Scatter(x=x_lu, y=y_lu, mode='lines', name='LUTIBR (q USD)',
                                 line=dict(color='#e83e8c', width=2)))
        fecha_curva = path.stem.split('_')[-1]
        fig.update_layout(
            title=f'Curvas Continuas BS — {fecha_curva[:4]}-{fecha_curva[4:6]}-{fecha_curva[6:]}',
            xaxis_title='Plazo (años)', yaxis_title='Tasa Continua (%)',
            height=300, margin=dict(l=50, r=20, t=35, b=30),
            legend=dict(x=0.7, y=0.95))
        return fig
    except Exception as e:
        return go.Figure().update_layout(title=f"Error cargando curvas: {e}")


def _fecha_corte_vigente(fecha=None):
    """Devuelve la fecha de corte a usar: la dada, o la última de hist_titulos."""
    if fecha:
        return pd.to_datetime(fecha).strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(DB_PATH)
        f = pd.read_sql_query("SELECT MAX(fecha_reporte) as f FROM hist_titulos", conn)['f'].iloc[0]
        conn.close()
        return str(f)[:10] if f else None
    except Exception:
        return None


def obtener_uvr_para(fecha=None):
    """UVR vigente (implícita de TUVT) para la fecha dada. None si no se puede calcular."""
    try:
        from gestor_informes import obtener_uvr_vigente
        f = _fecha_corte_vigente(fecha)
        if not f:
            return None
        uvr, _ = obtener_uvr_vigente(f)
        return round(uvr, 4) if uvr else None
    except Exception:
        return None


def obtener_trm_para(fecha=None):
    """TRM implícita del 597 para la fecha dada. None si no se puede calcular."""
    try:
        f = _fecha_corte_vigente(fecha)
        if not f:
            return None
        trm, _ = obtener_trm_implicita(f)
        return round(trm, 2) if trm else None
    except Exception:
        return None


def obtener_ipc_para(fecha=None):
    """IPC anual vigente (%) para la fecha dada. None si no se puede calcular."""
    try:
        f = _fecha_corte_vigente(fecha)
        if not f:
            return None
        ipc, _ = obtener_ipc_vigente(f)
        return round(ipc, 2) if ipc is not None else None
    except Exception:
        return None


def _uvr_auto_t0(fecha=None):
    """UVR vigente para la fecha dada (o la última disponible). None si no se puede."""
    return obtener_uvr_para(fecha)


def _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, store_sendas):
    """
    Calcula la UVR de cierre capitalizando uvr_t0 con la senda de inflación mensual
    (despejada de la senda YoY de la Sección 2). Metodología BanRep con ponderaciones
    estacionales. Devuelve float(uvr_cierre) o None si no se puede calcular.

    Reutiliza la misma lógica que el callback calcular_uvr_proyectada.
    """
    try:
        from dateutil.relativedelta import relativedelta as _rd
        from libro_de_funciones import (
            construir_senda_yoy_mensual, despejar_inflacion_mensual_desde_yoy,
            calcular_senda_uvr,
        )
        uvr_base = float(uvr_t0)
        # YoY vigente y final desde la senda de inflación (Sección 2)
        yoy_vigente, yoy_final = 0.06, 0.04
        if store_sendas:
            df_s = pd.DataFrame(store_sendas)
            df_s['inflación'] = pd.to_numeric(df_s.get('inflación'), errors='coerce')
            vals = df_s['inflación'].dropna().tolist()
            if vals:
                yoy_vigente, yoy_final = float(vals[0]), float(vals[-1])
        delta = _rd(fecha_end, fecha_ini)
        n_meses = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
        if n_meses < 1:
            n_meses = 12
        _senda_yoy = construir_senda_yoy_mensual(yoy_vigente, yoy_final, fecha_ini, n_meses)
        _mensuales = despejar_inflacion_mensual_desde_yoy(_senda_yoy, yoy_vigente, fecha_ini, n_meses)
        _inf_mensual_por_mes = {}
        for d in _mensuales:
            _y, _m = int(d['fecha'][:4]), int(d['fecha'][5:7])
            _inf_mensual_por_mes[(_y, _m)] = d['inf_mensual']
        uvr_cierre_val, _ = calcular_senda_uvr(
            uvr_t0=uvr_base, fecha_inicio=fecha_ini, fecha_fin=fecha_end,
            inf_mensual_por_mes=_inf_mensual_por_mes,
        )
        return round(float(uvr_cierre_val), 4)
    except Exception:
        return None


def layout_inputs():
    # Detectar altas pendientes del corte (portafolios nuevos y swaps sin
    # subyacente). Las secciones solo se renderizan cuando hay elementos nuevos,
    # siguiendo el patrón `detectar_incompletos` (Req 1.2, 2.2).
    portafolios_nuevos, swaps_sin_subyacente = _detectar_altas_pendientes()
    seccion_altas = [
        s for s in (
            _seccion_alta_portafolios(portafolios_nuevos),
            _seccion_asignar_subyacentes(swaps_sin_subyacente),
        ) if s is not None
    ]

    # Extraer TRM e IPC vigentes de la fuente (sin hardcode). None si no se pueden calcular.
    trm_hoy = None
    ipc_hoy = None
    try:
        # TRM: primero hist_opciones (TRM del informe de opciones), luego TRM implícita del 597
        conn = get_conn()
        try:
            _trm = pd.read_sql_query(
                "SELECT trm FROM hist_opciones WHERE fecha_reporte = (SELECT MAX(fecha_reporte) FROM hist_opciones) LIMIT 1",
                conn)
            if not _trm.empty:
                trm_hoy = float(str(_trm['trm'].iloc[0]).replace(',', ''))
        except Exception:
            pass
        conn.close()
        if trm_hoy is None:
            trm_hoy = obtener_trm_para(None)
        # IPC vigente para la última fecha disponible
        ipc_hoy = obtener_ipc_para(None)
    except Exception:
        pass

    return html.Div([
        html.H4("📥 Inputs para Proyecciones"),

        # --- MODO DE PROYECCIÓN (punta a punta vs por tramos) ---
        # Reubicado al INICIO de la pestaña de Inputs (tarea 7.1, Req 2.1).
        # El selector y el grid por tramo aparecen antes de las secciones de
        # variables para que el usuario elija el modo y vea el grid primero.
        html.Div([
            html.Label("Modo de proyección", style={'fontSize': '12px', 'fontWeight': '700',
                                                    'color': '#004d2a'}),
            dcc.RadioItems(
                id='modo-proyeccion',
                options=[
                    {'label': ' Punta a punta (un solo juego de retornos)', 'value': 'punta_a_punta'},
                    {'label': ' Por tramos (un retorno por cada fin de año)', 'value': 'por_tramos'},
                ],
                value='punta_a_punta',  # default retrocompatible
                labelStyle={'display': 'block', 'fontSize': '11px', 'margin': '2px 0'},
                style={'marginTop': '4px'},
            ),
            html.P("En «por tramos», el cierre de cada año alimenta el siguiente y se "
                   "guarda un portafolio proyectado por corte. Configura los retornos y la "
                   "periodicidad de reservas de cada año en el grid.",
                   style={'fontSize': '10px', 'color': '#666', 'marginTop': '4px'}),
            # --- Carga de inputs por tramo desde Excel (deuda técnica documentada) ---
            # Permite llenar el grid por tramo automáticamente subiendo un Excel con una
            # fila por variable y una columna por tramo (ver docstring del callback
            # `cargar_inputs_tramos_excel`). Aparece dentro del bloque de modo por tramos.
            dcc.Upload(
                id='upload-inputs-tramos',
                children=html.Button(
                    "📄 Cargar inputs por tramo (Excel)",
                    style={'fontSize': '11px', 'padding': '5px 12px',
                           'backgroundColor': '#004d2a', 'color': 'white',
                           'border': 'none', 'borderRadius': '4px', 'cursor': 'pointer'}),
                multiple=False,
                style={'display': 'inline-block', 'marginTop': '6px'},
            ),
            html.Span(
                " Sube una tabla: una fila por variable (Inflación, Repo, Mov TES COP/UVR, "
                "Mov Corp TF/IPC, TRM, Ret Acc Local, Ret Alt Local/Intl, Ret Alt IPC, PEI) "
                "y una columna por tramo.",
                style={'fontSize': '9px', 'color': '#888', 'marginLeft': '6px'}),
            html.Div(id='msg-upload-inputs-tramos',
                     style={'fontSize': '10px', 'marginTop': '4px'}),
            # Store con los valores cargados del Excel; el callback de render del grid lo
            # usa como valores iniciales de cada input por tramo.
            dcc.Store(id='store-inputs-tramos', data=None),
            # Contenedor de controles por tramo (se rellena dinámicamente).
            html.Div(id='tramos-container', style={'marginTop': '8px'}),
        ], style={'padding': '10px', 'border': '1px solid #cce', 'borderRadius': '6px',
                  'marginBottom': '12px', 'backgroundColor': '#f4f8ff'}),

        # --- Altas pendientes del corte (solo si hay elementos nuevos) ---
        # Secciones de alta de portafolios nuevos y de asignación de subyacentes
        # a swaps nuevos. Aparecen únicamente cuando el corte tiene elementos sin
        # dar de alta (Req 1.1, 1.2, 2.1, 2.2). Los callbacks se implementan en
        # la tarea 4.2.
        *seccion_altas,

        # --- 1. Curvas TES ---
        html.Div([
            html.H5("1. Curvas TES"),
            html.Div([
                html.Div([
                    html.P("Opción A: Cargar niveles actuales desde PIP", style={'fontWeight': 'bold', 'fontSize': '12px'}),
                    html.Button("📊 Cargar Curvas PIP (T+0)", id='btn-cargar-pip', n_clicks=0,
                                style={'backgroundColor': '#17a2b8', 'color': 'white', 'border': 'none',
                                       'padding': '6px 15px', 'borderRadius': '4px', 'cursor': 'pointer'}),
                ], style={'display': 'inline-block', 'marginRight': '30px', 'verticalAlign': 'top'}),
                html.Div([
                    html.P("Opción B: Subir Excel target (ISIN, tasa)", style={'fontWeight': 'bold', 'fontSize': '12px'}),
                    dcc.Upload(id='upload-tes', children=html.Button("📄 Cargar Excel TES"), multiple=False),
                ], style={'display': 'inline-block', 'verticalAlign': 'top'}),
            ], style={'marginBottom': '10px'}),
            html.Div(id='msg-upload-tes', style={'marginTop': '5px'}),
            html.Div([
                # TES COP
                html.Div([
                    html.H6("TES COP"),
                    html.Button("↺ Restablecer", id='btn-reset-cop', n_clicks=0, style={'fontSize': '10px', 'marginBottom': '5px'}),
                    dcc.Graph(id='grafico-curva-tes-cop'),
                    html.Div(id='sliders-tes-cop'),
                    html.Div([
                        html.Label("Shift COP (bps):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                        dcc.Input(id='esc-shift-tes-cop', type='number', value=0, step=5,
                                  style={'width': '70px', 'marginLeft': '5px'}),
                    ], style={'marginTop': '8px', 'padding': '6px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
                # TES UVR
                html.Div([
                    html.H6("TES UVR"),
                    html.Button("↺ Restablecer", id='btn-reset-uvr', n_clicks=0, style={'fontSize': '10px', 'marginBottom': '5px'}),
                    dcc.Graph(id='grafico-curva-tes-uvr'),
                    html.Div(id='sliders-tes-uvr'),
                    html.Div([
                        html.Label("Shift UVR (bps):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                        dcc.Input(id='esc-shift-tes-uvr', type='number', value=0, step=5,
                                  style={'width': '70px', 'marginLeft': '5px'}),
                    ], style={'marginTop': '8px', 'padding': '6px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginLeft': '4%'}),
            ]),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 2. Variables Macro ---
        html.Div([
            html.H5("2. Variables Macro"),
            # Inputs compartidos (aplican para ambas opciones)
            html.Div([
                html.Div([
                    html.Label("TRM actual:", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='input-trm-hoy', type='number', value=trm_hoy, disabled=True, style={'width': '90px', 'marginLeft': '5px', 'backgroundColor': '#e9ecef'}),
                ], style={'display': 'inline-block', 'marginRight': '20px'}),
                html.Div([
                    html.Label("Inflación actual (%):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='input-inf-hoy', type='number', value=ipc_hoy, step=0.1, disabled=True,
                              style={'width': '70px', 'marginLeft': '5px', 'backgroundColor': '#e9ecef'}),
                ], style={'display': 'inline-block', 'marginRight': '20px'}),
                html.Div([
                    html.Label("IBR actual (%):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='input-ibr-hoy', type='number', value=9.25, step=0.25, style={'width': '70px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block'}),
            ], style={'marginBottom': '10px', 'padding': '8px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
            html.Div([
                # Opción A: Upload
                html.Div([
                    html.P("Opción A: Subir Excel", style={'fontWeight': 'bold', 'fontSize': '12px'}),
                    html.P("Columnas: mes, TRM, inflación", style={'fontSize': '11px'}),
                    dcc.Upload(id='upload-sendas', children=html.Button("📄 Cargar Excel"), multiple=False),
                    html.Div(id='msg-upload-sendas', style={'marginTop': '5px'}),
                ], style={'width': '35%', 'display': 'inline-block', 'verticalAlign': 'top', 'padding': '10px', 'borderRight': '1px solid #ddd'}),
                # Opción B: Generar lineal
                html.Div([
                    html.P("Opción B: Generar senda lineal", style={'fontWeight': 'bold', 'fontSize': '12px'}),
                    html.Div([
                        html.Div([
                            html.Label("TRM final:", style={'fontSize': '11px'}),
                            dcc.Input(id='input-trm-final', type='number', value=trm_hoy, style={'width': '80px'}),
                        ], style={'display': 'inline-block', 'marginRight': '10px'}),
                        html.Div([
                            html.Label("Inflación final (%):", style={'fontSize': '11px'}),
                            dcc.Input(id='input-inf-final', type='number', value=4.0, step=0.1, style={'width': '70px'}),
                        ], style={'display': 'inline-block', 'marginRight': '10px'}),
                    ]),
                    html.Button("📈 Generar Senda Lineal", id='btn-generar-senda', n_clicks=0, style={'marginTop': '8px'}),
                    html.Div(id='msg-generar-senda', style={'marginTop': '5px'}),
                ], style={'width': '60%', 'display': 'inline-block', 'verticalAlign': 'top', 'padding': '10px'}),
            ]),
            html.Div([
                html.Div([
                    html.Button("↺ Restablecer TRM", id='btn-reset-trm', n_clicks=0, style={'fontSize': '10px', 'marginBottom': '5px'}),
                    dcc.Graph(id='grafico-senda-trm'),
                    html.Div(id='sliders-trm'),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
                html.Div([
                    html.Button("↺ Restablecer Inflación", id='btn-reset-inf', n_clicks=0, style={'fontSize': '10px', 'marginBottom': '5px'}),
                    dcc.Graph(id='grafico-senda-inflacion'),
                    html.Div(id='sliders-inflacion'),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginLeft': '4%'}),
            ]),
            # Senda Repo / TPM
            html.Hr(style={'margin': '15px 0', 'borderColor': '#dee2e6'}),
            html.H6("Tasa Repo / TPM", style={'fontWeight': '600'}),
            html.Div([
                html.Div([
                    html.Label("Repo inicial (%):", style={'fontSize': '11px'}),
                    dcc.Input(id='esc-repo-ini', type='number', value=9.75, step=0.25,
                              style={'width': '70px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Div([
                    html.Label("Repo final (%):", style={'fontSize': '11px'}),
                    dcc.Input(id='esc-repo-fin', type='number', value=7.75, step=0.25,
                              style={'width': '70px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                # IPC vigente actual: arranque de la inflación del PRIMER tramo en el modo
                # por tramos (no hay 'Serie Inflación.xlsx' en el proyecto, así que se
                # captura manualmente aquí). Análogo al 'Repo inicial'.
                html.Div([
                    html.Label("IPC vigente (%):", style={'fontSize': '11px'}),
                    dcc.Input(id='esc-ipc-vigente', type='number', value=None, step=0.01,
                              placeholder='ej. 5.2',
                              style={'width': '70px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Button("Generar Senda", id='btn-generar-senda-repo', n_clicks=0,
                            style={'fontSize': '11px', 'padding': '4px 10px'}),
            ], style={'marginBottom': '10px', 'padding': '8px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
            dcc.Graph(id='grafico-senda-repo', figure=go.Figure()),
            html.Button("↺ Restablecer", id='btn-reset-senda-repo', n_clicks=0,
                        style={'fontSize': '10px', 'marginTop': '5px', 'marginBottom': '5px'}),
            html.Div(id='sliders-repo', style={'marginTop': '5px'}),
            dcc.Store(id='store-senda-repo', data=None),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 3. Deuda Corporativa ---
        html.Div([
            html.H5("3. Curvas Corporativas"),
            html.P("Se cargan automáticamente desde PIP (mediana por año de vencimiento). También puedes subir Excel.",
                   style={'fontSize': '10px', 'color': '#666'}),
            dcc.Upload(id='upload-corporativos', children=html.Button("📄 Cargar Excel Corporativos (opcional)"), multiple=False),
            html.Div(id='msg-upload-corporativos', style={'marginTop': '5px'}),
            html.Div([
                html.Div([
                    html.Label("Shift FS (bps):", style={'fontSize': '11px'}),
                    dcc.Slider(id='slider-fs-shift', min=-200, max=200, step=5, value=0,
                               marks={i: str(i) for i in range(-200, 201, 100)}, tooltip={'placement': 'top'}),
                    html.Label("Twist FS (bps):", style={'fontSize': '11px'}),
                    dcc.Slider(id='slider-fs-twist', min=-100, max=100, step=5, value=0,
                               marks={i: str(i) for i in range(-100, 101, 50)}, tooltip={'placement': 'top'}),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
                html.Div([
                    html.Label("Shift IPC (bps):", style={'fontSize': '11px'}),
                    dcc.Slider(id='slider-ipc-shift', min=-200, max=200, step=5, value=0,
                               marks={i: str(i) for i in range(-200, 201, 100)}, tooltip={'placement': 'top'}),
                    html.Label("Twist IPC (bps):", style={'fontSize': '11px'}),
                    dcc.Slider(id='slider-ipc-twist', min=-100, max=100, step=5, value=0,
                               marks={i: str(i) for i in range(-100, 101, 50)}, tooltip={'placement': 'top'}),
                ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginLeft': '4%'}),
            ]),
            dcc.Graph(id='grafico-curva-corporativos'),
            html.H6("Sliders por nodo (año de vencimiento)", style={'fontSize': '10px', 'marginTop': '10px'}),
            html.Div(id='sliders-corporativos'),
            dcc.Store(id='store-corporativos', data=None),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 4. Curva EEUU ---
        html.Div([
            html.H5("4. Curva EEUU"),
            # Fila 1: Niveles T0 (solo para la gráfica)
            html.Div([
                html.Div([
                    html.Label("Fed T0 (%):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='esc-fed-t0', type='number', value=5.25, step=0.25,
                              style={'width': '80px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '20px'}),
                html.Div([
                    html.Label("Tesoros 10Y T0 (%):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='esc-tesoros10y-t0', type='number', value=4.25, step=0.05,
                              style={'width': '80px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block'}),
                html.Span("← niveles actuales (solo gráfica)",
                          style={'fontSize': '10px', 'color': '#6c757d', 'marginLeft': '12px'}),
            ], style={'marginBottom': '6px', 'padding': '6px 8px', 'backgroundColor': '#eef7f2', 'borderRadius': '4px'}),
            # Fila 2: Deltas (input del motor)
            html.Div([
                html.Div([
                    html.Label("Δ Fed (bps):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='esc-fed', type='number', value=0, step=25,
                              style={'width': '80px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '20px'}),
                html.Div([
                    html.Label("Δ Tesoros 10Y (bps):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='esc-tesoros10y', type='number', value=0, step=10,
                              style={'width': '80px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block'}),
                html.Span("← delta para escenario (afecta opciones y swaps)",
                          style={'fontSize': '10px', 'color': '#6c757d', 'marginLeft': '12px'}),
            ], style={'marginBottom': '10px', 'padding': '6px 8px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
            dcc.Graph(id='grafico-curva-eeuu', figure=go.Figure(
                layout=go.Layout(title='Curva EEUU (afecta Opciones y Swaps — NO Forwards)',
                                 yaxis_title='%', height=260,
                                 margin=dict(l=40, r=20, t=50, b=30))
            )),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 5. Derivados ---
        html.Div([
            html.H5("5. Derivados"),
            html.Div([
                html.Div([
                    html.Label("Shift Subyacentes (bps):", style={'fontSize': '11px', 'fontWeight': 'bold'}),
                    dcc.Input(id='esc-shift-subyac', type='number', value=0, step=5,
                              style={'width': '70px', 'marginLeft': '5px'}),
                ], style={'display': 'inline-block', 'marginRight': '20px'}),
            ], style={'marginBottom': '10px', 'padding': '8px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
            # Configuración método de cobertura
            html.Details([
                html.Summary("⚙️ Método de cobertura", style={'fontWeight': 'bold', 'cursor': 'pointer', 'fontSize': '11px'}),
                html.Div([
                    html.Div([
                        html.Label("Método:", style={'fontSize': '11px'}),
                        dcc.Dropdown(id='proy-metodo', options=[
                            {'label': '1 - Nominal Estable', 'value': 1},
                            {'label': '3 - Exposición Objetivo', 'value': 3},
                            {'label': '4 - Dejar vencer collares', 'value': 4},
                            {'label': '5 - Delta Hedge', 'value': 5},
                        ], value=1, clearable=False, style={'width': '250px'}),
                    ], style={'marginBottom': '8px'}),
                    html.Div(id='desc-metodo', style={'fontSize': '11px', 'color': '#333',
                                                      'padding': '6px 10px', 'backgroundColor': '#f8f9fa',
                                                      'borderLeft': '3px solid #007bff', 'borderRadius': '3px'}),
                    html.Div(id='params-metodo3', children=[
                        html.Div([
                            html.Div([
                                html.Label("Exposición Objetivo (%):", style={'fontSize': '11px'}),
                                dcc.Input(id='proy-exp-obj', type='number', value=30, min=0, max=100, style={'width': '70px'}),
                            ], style={'display': 'inline-block', 'marginRight': '15px'}),
                            html.Div([
                                html.Label("Trato exposición:", style={'fontSize': '11px'}),
                                dcc.Dropdown(id='proy-trato', options=[
                                    {'label': '1 - Con delta opciones', 'value': 1},
                                    {'label': '2 - Sin delta opciones', 'value': 2},
                                ], value=1, clearable=False, style={'width': '200px'}),
                            ], style={'display': 'inline-block', 'marginRight': '15px', 'verticalAlign': 'top'}),
                            html.Div([
                                html.Label("Momento exposición:", style={'fontSize': '11px'}),
                                dcc.Dropdown(id='proy-momento', options=[
                                    {'label': '1 - Actual', 'value': 1},
                                    {'label': '2 - Proyectado', 'value': 2},
                                ], value=1, clearable=False, style={'width': '150px'}),
                            ], style={'display': 'inline-block', 'marginRight': '15px', 'verticalAlign': 'top'}),
                            html.Div([
                                html.Label("Opciones sobre largo (%):", style={'fontSize': '11px'}),
                                dcc.Input(id='proy-opc-largo', type='number', value=70, min=0, max=100, style={'width': '70px'}),
                            ], style={'display': 'inline-block'}),
                        ], style={'padding': '8px', 'backgroundColor': '#f0f8ff', 'borderRadius': '4px'}),
                    ], style={'marginTop': '10px', 'display': 'none'}),
                ], style={'padding': '10px'}),
            ], open=False, style={'border': '1px solid #eee', 'borderRadius': '4px', 'padding': '5px'}),
            # Gráfica de curvas continuas (SWTIBR + LUTIBR)
            html.Div([
                html.H6("Curvas Derivados (Continuas)", style={'fontSize': '12px', 'color': '#004d2a'}),
                dcc.Graph(id='grafico-curvas-derivados', figure=_generar_grafico_curvas_derivados(),
                          config={'responsive': True, 'displayModeBar': False},
                          style={'height': '300px'}),
            ], style={'marginTop': '15px'}),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 6. Renta Variable ---
        html.Div([
            html.H5("6. Renta Variable"),
            html.Div([
                html.Div([
                    html.Label("Ret RV Local (EA %):", style={'fontSize': '11px', 'width': '140px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-rv-local', type='number', value=12, step=0.5,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Label("Ret RV Intl (EA %):", style={'fontSize': '11px', 'width': '140px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-rv-intl', type='number', value=8, step=0.5,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Label("Ret PEI (EA %):", style={'fontSize': '11px', 'width': '140px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-rv-pei', type='number', value=10, step=0.5,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block'}),
            ]),
            html.P("Fuente: portafolio_clasificado (COLCAP / MXWD / PEI)", 
                   style={'fontSize': '9px', 'color': '#888', 'marginTop': '6px'}),
            # Botón individual eliminado (tarea 7.4): la corrida se dispara desde el
            # Boton_Consolidado. Se conservan el contenedor de resultado y el store base
            # porque los callbacks de estrés RV siguen leyendo `store-rv-base`.
            html.Div(id='resultado-proy-rv', style={'marginTop': '10px'}),
            dcc.Store(id='store-rv-base'),

            # --- Estrés por Título RV ---
            html.Hr(style={'margin': '20px 0', 'borderColor': '#bdc3c7'}),
            html.H6("🔬 Estrés por Título RV", style={'color': '#8e44ad', 'marginBottom': '8px', 'fontSize': '12px'}),
            html.P("Selecciona títulos y asigna un retorno estresado individual.",
                   style={'fontSize': '10px', 'color': '#7f8c8d'}),
            # Estrés RV Local
            html.Div([
                html.Label("RV Local (COLCAP / PEI)", style={'fontWeight': '700', 'fontSize': '10px',
                           'color': '#008f4f', 'marginBottom': '5px'}),
                dcc.Dropdown(id='stress-titulos-local', multi=True,
                             placeholder="Seleccionar títulos locales...",
                             style={'fontSize': '11px'}),
                html.Div(id='stress-inputs-local', style={'marginTop': '5px'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid #008f4f', 'borderRadius': '6px',
                      'backgroundColor': '#f0faf5'}),
            # Estrés RV Internacional
            html.Div([
                html.Label("RV Internacional (MXWD)", style={'fontWeight': '700', 'fontSize': '10px',
                           'color': 'rgb(41,128,185)', 'marginBottom': '5px'}),
                dcc.Dropdown(id='stress-titulos-intl', multi=True,
                             placeholder="Seleccionar títulos internacionales...",
                             style={'fontSize': '11px'}),
                html.Div(id='stress-inputs-intl', style={'marginTop': '5px'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid rgb(41,128,185)', 'borderRadius': '6px',
                      'backgroundColor': '#f0f7fc'}),
            html.Button("⚡ Ejecutar Estrés RV", id='btn-stress-rv', n_clicks=0,
                        style={'backgroundColor': '#8e44ad', 'color': 'white',
                               'border': 'none', 'padding': '6px 14px',
                               'borderRadius': '4px', 'cursor': 'pointer',
                               'fontWeight': '600', 'fontSize': '11px'}),
            html.Div(id='resultado-stress-rv', style={'marginTop': '10px'}),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 7. Alternativos ---
        html.Div([
            html.H5("7. Alternativos"),
            html.Div([
                html.Div([
                    html.Label("Ret Alt. Locales (EA %):", style={'fontSize': '11px', 'width': '160px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-ret-alt', type='number', value=10, step=0.5,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Label("Ret Alt. Intl (EA %):", style={'fontSize': '11px', 'width': '160px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-ret-alt-intl', type='number', value=12, step=0.5,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block'}),
            ], style={'marginBottom': '10px'}),
            html.Hr(style={'margin': '8px 0', 'borderColor': '#eee'}),
            html.Div([
                html.Span("FCP 4G (IPC + Spread)", style={'fontSize': '11px', 'fontWeight': '700', 'color': '#e83e8c'}),
                html.Div([
                    html.Div([
                        html.Label("IPC: usa senda de inflación (Sección 2)", style={'fontSize': '11px', 'color': '#666', 'display': 'inline-block'}),
                        dcc.Input(id='esc-ipc-4g', type='number', value=7.07, step=0.01,
                                  style={'width': '70px', 'fontSize': '11px', 'display': 'none'}),
                    ], style={'display': 'inline-block', 'marginRight': '30px'}),
                    html.Div([
                        html.Label("Spread FCP 4G (%):", style={'fontSize': '11px', 'width': '160px', 'display': 'inline-block'}),
                        dcc.Input(id='esc-spread-4g', type='number', value=3.50, step=0.1,
                                  style={'width': '70px', 'fontSize': '11px'}),
                    ], style={'display': 'inline-block', 'marginRight': '20px'}),
                    html.Div([
                        html.Span("Tasa EA FCP 4G: ", style={'fontSize': '10px', 'color': '#555'}),
                        html.Span(id='esc-tasa-4g-calc', style={'fontSize': '10px', 'fontWeight': '600', 'color': '#e83e8c'}),
                    ], style={'display': 'inline-block', 'verticalAlign': 'middle'}),
                ], style={'marginTop': '6px'}),
            ]),
            # Botón individual eliminado (tarea 7.4): la corrida se dispara desde el
            # Boton_Consolidado. Se conservan el contenedor de resultado y el store base
            # porque los callbacks de estrés de alternativos siguen leyendo `store-alt-base`.
            html.Div(id='resultado-proy-alt', style={'marginTop': '10px'}),
            dcc.Store(id='store-alt-base'),

            # --- Estrés por Fondo Alternativo ---
            html.Hr(style={'margin': '20px 0', 'borderColor': '#bdc3c7'}),
            html.H6("🔬 Estrés por Fondo", style={'color': '#8e44ad', 'marginBottom': '8px', 'fontSize': '12px'}),
            html.P("Selecciona fondos y asigna un retorno estresado individual.",
                   style={'fontSize': '10px', 'color': '#7f8c8d'}),
            # Estrés Alt Local
            html.Div([
                html.Label("Alternativos Local", style={'fontWeight': '700', 'fontSize': '10px',
                           'color': 'rgb(142,68,173)', 'marginBottom': '5px'}),
                dcc.Dropdown(id='stress-fondos-local', multi=True,
                             placeholder="Seleccionar fondos locales...",
                             style={'fontSize': '11px'}),
                html.Div(id='stress-inputs-alt-local', style={'marginTop': '5px'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid rgb(142,68,173)', 'borderRadius': '6px',
                      'backgroundColor': '#faf5ff'}),
            # Estrés Alt Internacional
            html.Div([
                html.Label("Alternativos Internacional", style={'fontWeight': '700', 'fontSize': '10px',
                           'color': 'rgb(41,128,185)', 'marginBottom': '5px'}),
                dcc.Dropdown(id='stress-fondos-intl', multi=True,
                             placeholder="Seleccionar fondos internacionales...",
                             style={'fontSize': '11px'}),
                html.Div(id='stress-inputs-alt-intl', style={'marginTop': '5px'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid rgb(41,128,185)', 'borderRadius': '6px',
                      'backgroundColor': '#f0f7fc'}),
            html.Button("⚡ Ejecutar Estrés Alt", id='btn-stress-alt', n_clicks=0,
                        style={'backgroundColor': '#8e44ad', 'color': 'white',
                               'border': 'none', 'padding': '6px 14px',
                               'borderRadius': '4px', 'cursor': 'pointer',
                               'fontWeight': '600', 'fontSize': '11px'}),
            html.Div(id='resultado-stress-alt', style={'marginTop': '10px'}),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 8. Créditos Contratados ---
        html.Div([
            html.H5("8. Créditos Contratados"),
            html.P("Proyección de saldo y costo mensual de créditos con tasa fija, IPC+spread o IBR+spread.",
                   style={'fontSize': '10px', 'color': '#666', 'marginBottom': '10px'}),
            html.Div([
                html.Div([
                    html.Label("IPC proyectado (EA %):", style={'fontSize': '11px', 'width': '150px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-cred-ipc', type='number', value=7.07, step=0.01,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '30px'}),
                html.Div([
                    html.Label("IBR proyectado (EA %):", style={'fontSize': '11px', 'width': '150px', 'display': 'inline-block'}),
                    dcc.Input(id='esc-cred-ibr', type='number', value=7.07, step=0.01,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block'}),
            ], style={'marginBottom': '10px'}),
            # Botón individual eliminado (tarea 7.4): la corrida se dispara desde el
            # Boton_Consolidado. Se conservan el contenedor de resultado y el store base
            # porque los callbacks de estrés de créditos siguen leyendo `store-cred-base`.
            html.Div(id='resultado-proy-cred', style={'marginTop': '10px'}),
            dcc.Store(id='store-cred-base'),

            # --- Estrés de Créditos ---
            html.Hr(style={'margin': '20px 0', 'borderColor': '#bdc3c7'}),
            html.H6("🔬 Estrés de Tasas", style={'color': '#2980b9', 'marginBottom': '8px', 'fontSize': '12px'}),
            html.P("Ajusta las tasas estresadas. Se reproyectan todos los créditos y se compara vs base.",
                   style={'fontSize': '10px', 'color': '#7f8c8d'}),
            html.Div([
                html.Div([
                    html.Label("IPC Estrés (EA %):", style={'fontSize': '10px', 'fontWeight': '600'}),
                    dcc.Input(id='stress-cred-ipc', type='number', value=9.0, step=0.1,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Div([
                    html.Label("IBR Estrés (EA %):", style={'fontSize': '10px', 'fontWeight': '600'}),
                    dcc.Input(id='stress-cred-ibr', type='number', value=9.0, step=0.1,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Div([
                    html.Label("SOFR Estrés (EA %):", style={'fontSize': '10px', 'fontWeight': '600'}),
                    dcc.Input(id='stress-cred-sofr', type='number', value=6.5, step=0.1,
                              style={'width': '70px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Div([
                    html.Label("TRM Estrés Ini:", style={'fontSize': '10px', 'fontWeight': '600'}),
                    dcc.Input(id='stress-cred-trm-ini', type='number', value=4500, step=50,
                              style={'width': '80px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block', 'marginRight': '15px'}),
                html.Div([
                    html.Label("TRM Estrés Fin:", style={'fontSize': '10px', 'fontWeight': '600'}),
                    dcc.Input(id='stress-cred-trm-fin', type='number', value=4800, step=50,
                              style={'width': '80px', 'fontSize': '11px'}),
                ], style={'display': 'inline-block'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid rgb(41,128,185)', 'borderRadius': '6px',
                      'backgroundColor': '#f0f7fc'}),
            html.Button("⚡ Ejecutar Estrés Créditos", id='btn-stress-cred', n_clicks=0,
                        style={'backgroundColor': '#8e44ad', 'color': 'white',
                               'border': 'none', 'padding': '6px 14px',
                               'borderRadius': '4px', 'cursor': 'pointer',
                               'fontWeight': '600', 'fontSize': '11px'}),
            html.Div(id='resultado-stress-cred', style={'marginTop': '10px'}),

            # --- Estrés por Crédito Individual ---
            html.Hr(style={'margin': '20px 0', 'borderColor': '#bdc3c7'}),
            html.H6("🔬 Estrés por Crédito Individual", style={'color': '#2980b9', 'marginBottom': '8px', 'fontSize': '12px'}),
            html.P("Selecciona un crédito y modifica su variable de valoración.",
                   style={'fontSize': '10px', 'color': '#7f8c8d'}),
            html.Div([
                dcc.Dropdown(id='stress-cred-individual-select', multi=True,
                             placeholder="Seleccionar crédito(s)...",
                             style={'fontSize': '11px'}),
                html.Div(id='stress-inputs-cred-individual', style={'marginTop': '5px'}),
            ], style={'marginBottom': '10px', 'padding': '8px',
                      'border': '1px solid rgb(41,128,185)', 'borderRadius': '6px',
                      'backgroundColor': '#f0f7fc'}),
            html.Button("⚡ Ejecutar Estrés Individual", id='btn-stress-cred-ind', n_clicks=0,
                        style={'backgroundColor': '#8e44ad', 'color': 'white',
                               'border': 'none', 'padding': '6px 14px',
                               'borderRadius': '4px', 'cursor': 'pointer',
                               'fontWeight': '600', 'fontSize': '11px'}),
            html.Div(id='resultado-stress-cred-ind', style={'marginTop': '10px'}),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # --- 9. UVR ---
        html.Div([
            html.H5("9. UVR"),
            html.Div([
                html.P("La UVR se calcula automáticamente: UVR T0 = valor vigente en la fecha de corte; "
                       "UVR Cierre = capitalización de UVR T0 con la senda de inflación mensual. "
                       "No es editable para mantener la coherencia con la inflación proyectada.",
                       style={'fontSize': '10px', 'color': '#666', 'margin': '0'}),
                html.Div(id='esc-uvr-info', style={'fontSize': '11px', 'color': '#007bff', 'marginTop': '6px'}),
            ], style={'padding': '10px', 'backgroundColor': '#fff9f0', 'borderRadius': '6px', 'marginBottom': '10px',
                      'border': '1px solid #fde0b5'}),
            # Senda Inflación (para cálculo UVR)
            html.Details([
                html.Summary("📈 Senda Inflación Mensual (para cálculo UVR)",
                             style={'fontWeight': 'bold', 'cursor': 'pointer', 'fontSize': '11px'}),
                html.Div([
                    html.Button("Cargar Senda", id='btn-cargar-senda-inflacion', n_clicks=0,
                                style={'fontSize': '10px', 'padding': '4px 10px', 'marginBottom': '8px'}),
                    dcc.Graph(id='grafico-senda-inf-esc', figure=go.Figure(), style={'height': '220px'}),
                    html.Div(id='sliders-inf-esc', style={'marginTop': '5px'}),
                    dcc.Store(id='store-senda-inf-esc', data=None),
                ], style={'padding': '8px'}),
            ], open=False, style={'border': '1px solid #eee', 'borderRadius': '4px', 'padding': '5px'}),
        ], style={'marginBottom': '30px', 'padding': '15px', 'border': '1px solid #ddd', 'borderRadius': '5px'}),

        # Hidden components for compatibility
        html.Div(id='resultado-sensibilizacion', style={'display': 'none'}),
        html.Div(id='graficos-datos-eco', style={'display': 'none'}),
        html.Div(id='msg-upload-datos-eco', style={'display': 'none'}),
        html.Div(id='resultado-rf', style={'display': 'none'}),
        html.Div(id='resultado-rv', style={'display': 'none'}),
        html.Div(id='resultado-sim', style={'display': 'none'}),
        html.Div(id='msg-proy', style={'display': 'none'}),
        html.Div(id='info-portafolio-proy', style={'display': 'none'}),
        dcc.Graph(id='grafico-pyg', style={'display': 'none'}),
        dcc.Download(id='download-proy-excel'),
        html.Div(id='esc-senda-trm-preview', style={'display': 'none'}),
        dcc.Dropdown(id='esc-inflacion', options=[{'label': 'Senda EE', 'value': 'senda_ee'}],
                     value='senda_ee', style={'display': 'none'}),

        # NOTA (tarea 7.1): el selector «Modo de proyección» y el contenedor
        # `tramos-container` se reubicaron al INICIO de la pestaña de Inputs
        # (ver bloque tras el H4). No dupliques `id='modo-proyeccion'` ni
        # `id='tramos-container'` aquí; Dash falla con ids duplicados.

        # --- BOTÓN CORRER ESCENARIO ---
        html.Div([
            html.Button("▶ CORRER ESCENARIO", id='btn-correr-escenario', n_clicks=0,
                        style={'backgroundColor': '#007bff', 'color': 'white', 'border': 'none',
                               'padding': '12px 40px', 'borderRadius': '6px', 'cursor': 'pointer',
                               'fontWeight': 'bold', 'fontSize': '14px', 'width': '100%'}),
            html.P("Ejecuta: RF + Derivados + Simultáneas + RV + Alternativos + Créditos",
                   style={'fontSize': '10px', 'color': '#555', 'textAlign': 'center', 'marginTop': '4px'}),
            html.P("⏱️ El motor RF tarda ~5 min. No cierres la pestaña.",
                   style={'fontSize': '10px', 'color': '#888', 'textAlign': 'center', 'marginTop': '2px'}),
        ], style={'textAlign': 'center', 'marginBottom': '15px', 'marginTop': '20px'}),
        # Indicador de progreso por tramo (lo actualiza set_progress del background callback).
        html.Div(id='escenario-progreso', style={'marginTop': '6px', 'fontSize': '11px',
                                                  'color': '#0d6efd', 'textAlign': 'center'}),
        html.Div(id='msg-escenario', style={'marginTop': '10px'}),

    ], style={'padding': '10px'})


# --- Callback: controles por tramo (modo por_tramos) ---
@callback(
    Output('tramos-container', 'children'),
    [Input('modo-proyeccion', 'value'),
     Input('btn-aplicar-fechas', 'n_clicks'),
     Input('store-inputs-tramos', 'data')],
    [State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
)
def render_controles_tramos(modo, _n_fechas, inputs_cargados, fecha_inicio, fecha_fin):
    """
    Rellena el contenedor de controles por tramo. En modo 'punta_a_punta' no muestra nada
    (comportamiento actual). En 'por_tramos' genera, por cada año del horizonte, los inputs
    de todas las variables (una columna por tramo) y un selector de periodicidad de reservas.

    Si `inputs_cargados` (del Store `store-inputs-tramos`, poblado al subir un Excel con el
    callback `cargar_inputs_tramos_excel`) no es None, sus valores se usan como valores
    INICIALES de cada input por tramo: `inputs_cargados` es `{tipo_id: [v_tramo0, v_tramo1, ...]}`.
    """
    if str(modo) != 'por_tramos':
        return []  # modo actual: sin controles adicionales

    inputs_cargados = inputs_cargados or {}

    import sys as _sys
    _sdir = str(Path(__file__).parent / 'scripts')
    if _sdir not in _sys.path:
        _sys.path.insert(0, _sdir)
    try:
        from proyeccion_tramos import calcular_tramos, HorizonteInvalidoError
        fi = pd.to_datetime(fecha_inicio).date() if fecha_inicio else date.today()
        ff = pd.to_datetime(fecha_fin).date() if fecha_fin else fi + timedelta(days=365)
        tramos = calcular_tramos(fi, ff)
    except HorizonteInvalidoError:
        return html.Span("⚠️ El horizonte debe ser de al menos un mes.",
                         style={'color': '#e65100', 'fontSize': '11px'})
    except Exception as e:
        return html.Span(f"⚠️ No se pudieron calcular los tramos: {e}",
                         style={'color': '#e65100', 'fontSize': '11px'})

    def _valor_cargado(tipo, idx, default):
        """Valor precargado del Excel para (tipo, tramo idx), o el default si no hay."""
        vals = inputs_cargados.get(tipo)
        if isinstance(vals, list) and idx < len(vals) and vals[idx] is not None:
            return vals[idx]
        return default

    def _num_input(tipo, idx, ph, default=0, step=5):
        """Input numérico por tramo con id dinámico {'type': tipo, 'index': idx}.

        step='any' evita que el navegador marque en ROJO (validación HTML5 `:invalid`)
        los valores que no caen exactamente en el múltiplo del `step` (p. ej. una TRM de
        3337 con step=10, o una inflación de 6.89 con step=0.1). El `step` recibido se usa
        solo como incremento de las flechitas, no como restricción de validez.

        Si hay un valor precargado desde el Excel (`store-inputs-tramos`) para este
        (tipo, tramo), se usa como valor INICIAL en lugar del default.
        """
        valor_ini = _valor_cargado(tipo, idx, default)
        return dcc.Input(id={'type': tipo, 'index': idx}, type='number', value=valor_ini,
                         step='any', placeholder=ph,
                         style={'width': '100%', 'fontSize': '11px', 'boxSizing': 'border-box'})

    # Definición del grid completo de Inputs_Por_Tramo (Req 1.1). Cada entrada:
    # (id_type, etiqueta, placeholder, valor_default, step). Los bps y retornos
    # arrancan en 0; TRM/inflación/repo usan placeholder (sin default numérico) para
    # que el usuario ingrese un nivel sensato (Req 1.3).
    variables = [
        ('tramo-inflacion',      'Inflación EA (%)',        'ej. 4.0',   None, 0.1),
        ('tramo-repo',           'Repo EA (%)',             'ej. 8.0',   None, 0.25),
        ('tramo-shift-cop',      'Mov TES COP (bps)',       'bps',       0,    5),
        ('tramo-shift-uvr',      'Mov TES UVR (bps)',       'bps',       0,    5),
        ('tramo-shift-fs',       'Mov Corp TF (bps)',       'bps',       0,    5),
        ('tramo-shift-ipc',      'Mov Corp IPC (bps)',      'bps',       0,    5),
        ('tramo-trm',            'TRM cierre',              'ej. 4000',  None, 10),
        ('tramo-ret-acc-local',  'Ret Acc Local EA (%)',    'ej. 8.0',   0,    0.1),
        ('tramo-ret-acc-intl',   'Ret Acc Intl EA (%)',     'ej. 8.0',   0,    0.1),
        ('tramo-ret-alt-local',  'Ret Alt Local EA (%)',    'ej. 8.0',   0,    0.1),
        ('tramo-ret-alt-intl',   'Ret Alt Intl EA (%)',     'ej. 8.0',   0,    0.1),
        ('tramo-ret-alt-ipc',    'Ret Alt IPC (bps)',       'bps',       0,    5),
        ('tramo-pei',            'PEI EA (%)',              'ej. 8.0',   0,    0.1),
        # ── Extensión derivados por tramo (Req 13.4): Δ Fed y Δ Tesoros 10Y en bps ──
        ('tramo-fed',            'Δ Fed (bps)',             'bps',       0,    5),
        ('tramo-tesoros10y',     'Δ Tesoros 10Y (bps)',     'bps',       0,    5),
    ]

    ancho_col = '150px'
    ancho_label = '150px'

    # Encabezado: primera celda vacía (columna de etiquetas) + una columna por tramo.
    encabezado = [
        html.Div("Variable", style={
            'minWidth': ancho_label, 'width': ancho_label, 'fontSize': '11px',
            'fontWeight': '700', 'color': '#004d2a', 'padding': '4px'}),
    ]
    for t in tramos:
        encabezado.append(html.Div(
            f"Tramo {t.indice + 1}\ncierre {t.fecha_corte} ({t.sub_horizonte} mes(es))",
            style={'minWidth': ancho_col, 'width': ancho_col, 'fontSize': '10px',
                   'fontWeight': '700', 'color': '#004d2a', 'padding': '4px',
                   'whiteSpace': 'pre-line', 'textAlign': 'center'}))

    filas_grid = [html.Div(encabezado, style={'display': 'flex', 'flexDirection': 'row',
                                              'borderBottom': '2px solid #cce'})]

    # Una fila por variable, con una celda (input) por tramo.
    for tipo, etiqueta, ph, default, step in variables:
        celdas = [html.Div(
            html.Label(etiqueta, style={'fontSize': '10px', 'fontWeight': '600'}),
            style={'minWidth': ancho_label, 'width': ancho_label, 'padding': '4px',
                   'display': 'flex', 'alignItems': 'center'})]
        for t in tramos:
            celdas.append(html.Div(
                _num_input(tipo, t.indice, ph, default=default, step=step),
                style={'minWidth': ancho_col, 'width': ancho_col, 'padding': '4px'}))
        filas_grid.append(html.Div(celdas, style={
            'display': 'flex', 'flexDirection': 'row', 'borderBottom': '1px solid #eee'}))

    # Fila de periodicidad de reservas (dropdown por tramo, conservado del motor RF).
    celdas_reservas = [html.Div(
        html.Label("Periodicidad reservas", style={'fontSize': '10px', 'fontWeight': '600'}),
        style={'minWidth': ancho_label, 'width': ancho_label, 'padding': '4px',
               'display': 'flex', 'alignItems': 'center'})]
    for t in tramos:
        celdas_reservas.append(html.Div(
            dcc.Dropdown(
                id={'type': 'tramo-periodicidad', 'index': t.indice},
                options=[{'label': 'Mensual', 'value': 'mensual'},
                         {'label': 'Anual (prorrateada)', 'value': 'anual'}],
                value='mensual', clearable=False,
                style={'fontSize': '11px'}),
            style={'minWidth': ancho_col, 'width': ancho_col, 'padding': '4px'}))
    filas_grid.append(html.Div(celdas_reservas, style={
        'display': 'flex', 'flexDirection': 'row', 'borderBottom': '1px solid #eee'}))

    return html.Div([
        html.Div("Inputs por tramo (una columna por tramo)",
                 style={'fontSize': '11px', 'fontWeight': '700', 'color': '#004d2a',
                        'marginBottom': '4px'}),
        # Contenedor con scroll horizontal por si hay muchos tramos.
        html.Div(filas_grid, style={'overflowX': 'auto', 'padding': '6px',
                                    'border': '1px solid #dde', 'borderRadius': '4px',
                                    'backgroundColor': 'white'}),
    ], style={'marginTop': '4px'})


# ==============================================================================
# CARGA DE INPUTS POR TRAMO DESDE EXCEL (deuda técnica documentada)
# ==============================================================================
# Mapa: nombre normalizado de la fila del Excel -> tipo de input del grid por tramo.
# La normalización (minúsculas, sin acentos, sin signos %/() ) hace el match tolerante.
_MAPA_FILAS_INPUTS_TRAMO = {
    "inflacion": "tramo-inflacion",
    "inflacion ea": "tramo-inflacion",
    "tasa repo": "tramo-repo",
    "repo": "tramo-repo",
    "repo ea": "tramo-repo",
    "mov tes cop": "tramo-shift-cop",
    "mov tes uvr": "tramo-shift-uvr",
    "mov corp tf": "tramo-shift-fs",
    "spread corp tf": "tramo-shift-fs",
    "mov fs": "tramo-shift-fs",
    "mov corp ipc": "tramo-shift-ipc",
    "spread corp ipc": "tramo-shift-ipc",
    "mov ipc": "tramo-shift-ipc",
    "trm": "tramo-trm",
    "ret acc locales": "tramo-ret-acc-local",
    "ret acc local": "tramo-ret-acc-local",
    "ret acciones locales": "tramo-ret-acc-local",
    "ret acc intl": "tramo-ret-acc-intl",
    "ret acc internacional": "tramo-ret-acc-intl",
    "ret acc internacionales": "tramo-ret-acc-intl",
    "ret acciones internacionales": "tramo-ret-acc-intl",
    "ret acciones intl": "tramo-ret-acc-intl",
    "ret alternativos": "tramo-ret-alt-local",
    "ret alt local": "tramo-ret-alt-local",
    "ret alternativos local": "tramo-ret-alt-local",
    "ret alternativos intl": "tramo-ret-alt-intl",
    "ret alt intl": "tramo-ret-alt-intl",
    "ret alternativos internacional": "tramo-ret-alt-intl",
    "ret alternativos ipc": "tramo-ret-alt-ipc",
    "ret alt ipc": "tramo-ret-alt-ipc",
    "pei": "tramo-pei",
    # ── Extensión derivados por tramo (Req 13.4) ──
    "delta fed": "tramo-fed",
    "mov fed": "tramo-fed",
    "fed": "tramo-fed",
    "tasa fed": "tramo-fed",
    "delta tesoros 10y": "tramo-tesoros10y",
    "delta tesoros 10 y": "tramo-tesoros10y",
    "mov tesoros 10y": "tramo-tesoros10y",
    "tesoros 10y": "tramo-tesoros10y",
    "tesoros 10 anos": "tramo-tesoros10y",
    "delta 10y": "tramo-tesoros10y",
}


def _normalizar_nombre_fila(texto) -> str:
    """Normaliza el nombre de una fila del Excel: minúsculas, sin acentos ni signos."""
    import unicodedata
    s = str(texto or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    for ch in ("%", "(", ")", ".", ":", "$"):
        s = s.replace(ch, " ")
    return " ".join(s.split())  # colapsa espacios


def _parsear_excel_inputs_tramos(contenido_bytes):
    """
    Parsea el Excel de inputs por tramo → `{tipo_input: [v0, v1, ...]}` (uno por tramo).

    Formato esperado: una fila por variable (nombre en la primera columna, tolerante a
    acentos/mayúsculas/signos) y una columna por tramo. Los porcentajes se ingresan como
    número tal cual (6.89 para 6.89%, NO 0.0689): así los espera el grid, y el callback
    de corrida ya los divide /100. Los bps y la TRM van como número.

    Devuelve `(valores, filas_reconocidas, filas_ignoradas, n_columnas)`.
    Entrada NO confiable: cada celda se coacciona a float; celdas no numéricas → None.
    """
    import io as _io
    df = pd.read_excel(_io.BytesIO(contenido_bytes), header=None)
    if df.empty or df.shape[1] < 2:
        raise ValueError("El Excel no tiene una columna de nombres y al menos una de datos.")

    valores: dict = {}
    reconocidas: list = []
    ignoradas: list = []
    n_cols = 0
    for _, fila in df.iterrows():
        nombre = _normalizar_nombre_fila(fila.iloc[0])
        if not nombre:
            continue
        tipo = _MAPA_FILAS_INPUTS_TRAMO.get(nombre)
        if tipo is None:
            ignoradas.append(str(fila.iloc[0]))
            continue
        # Coaccionar las celdas de datos (columnas 1..N) a float; no numéricas → None.
        serie = []
        for v in fila.iloc[1:]:
            try:
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    serie.append(None)
                else:
                    serie.append(float(v))
            except (TypeError, ValueError):
                serie.append(None)
        # Recortar Nones finales (columnas vacías del Excel).
        while serie and serie[-1] is None:
            serie.pop()
        valores[tipo] = serie
        n_cols = max(n_cols, len(serie))
        reconocidas.append(nombre)
    return valores, reconocidas, ignoradas, n_cols


@callback(
    [Output('store-inputs-tramos', 'data'),
     Output('msg-upload-inputs-tramos', 'children')],
    Input('upload-inputs-tramos', 'contents'),
    [State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True,
)
def cargar_inputs_tramos_excel(contents, fecha_inicio, fecha_fin):
    """
    Llena el grid de inputs por tramo desde un Excel (deuda técnica documentada).

    El Excel trae una fila por variable y una columna por tramo (ver
    `_parsear_excel_inputs_tramos`). El resultado se guarda en `store-inputs-tramos`, que
    dispara el re-render del grid (`render_controles_tramos`) usando esos valores como
    iniciales. Las columnas del Excel se asignan a los tramos EN ORDEN; si su número no
    coincide con los tramos del horizonte (fecha inicio/fin del sidebar), se avisa pero se
    llenan los que coincidan (los sobrantes se ignoran; los faltantes conservan su default).
    """
    if not contents:
        return no_update, no_update
    try:
        _ctype, content_string = contents.split(',')
        contenido_bytes = base64.b64decode(content_string)
    except Exception:
        return no_update, html.Span("❌ No se pudo leer el archivo subido.",
                                     style={'color': 'red'})

    try:
        valores, reconocidas, ignoradas, n_cols = _parsear_excel_inputs_tramos(contenido_bytes)
    except Exception as e:
        return no_update, html.Span(f"❌ Excel inválido: {str(e)[:120]}",
                                     style={'color': 'red'})

    if not valores:
        return no_update, html.Span(
            "⚠️ No se reconoció ninguna fila de variable. Usa nombres como 'Inflación', "
            "'Tasa repo', 'TRM', 'Ret Acc Locales', etc. en la primera columna.",
            style={'color': '#e65100'})

    # Número de tramos del horizonte actual, para avisar si el Excel trae otro número de
    # columnas (no bloquea: el grid usa los que coincidan).
    n_tramos = None
    try:
        import sys as _sys
        _sdir = str(Path(__file__).parent / 'scripts')
        if _sdir not in _sys.path:
            _sys.path.insert(0, _sdir)
        from proyeccion_tramos import calcular_tramos
        fi = pd.to_datetime(fecha_inicio).date() if fecha_inicio else date.today()
        ff = pd.to_datetime(fecha_fin).date() if fecha_fin else fi + timedelta(days=365)
        n_tramos = len(calcular_tramos(fi, ff))
    except Exception:
        n_tramos = None

    partes = [html.Span(
        f"✅ Cargadas {len(reconocidas)} variable(s), {n_cols} columna(s) de tramo.",
        style={'color': 'green', 'fontWeight': '600'})]
    if n_tramos is not None and n_cols != n_tramos:
        partes.append(html.Br())
        partes.append(html.Span(
            f"⚠️ El Excel trae {n_cols} columna(s) pero el horizonte tiene {n_tramos} "
            f"tramo(s). Se llenan los que coinciden; revisa las fechas o el archivo.",
            style={'color': '#e65100', 'fontSize': '10px'}))
    if ignoradas:
        partes.append(html.Br())
        partes.append(html.Span(
            f"ℹ️ Filas ignoradas (nombre no reconocido): {', '.join(ignoradas[:8])}.",
            style={'color': '#0c5460', 'fontSize': '10px'}))

    return valores, html.Div(partes)


# --- Callback: Cargar curvas PIP automáticamente ---
@callback(
    [Output('msg-upload-tes', 'children', allow_duplicate=True),
     Output('store-tes-cop', 'data', allow_duplicate=True),
     Output('store-tes-uvr', 'data', allow_duplicate=True),
     Output('sliders-tes-cop', 'children', allow_duplicate=True),
     Output('sliders-tes-uvr', 'children', allow_duplicate=True),
     Output('grafico-curva-tes-cop', 'figure', allow_duplicate=True),
     Output('grafico-curva-tes-uvr', 'figure', allow_duplicate=True),
     Output('store-corporativos', 'data', allow_duplicate=True),
     Output('sliders-corporativos', 'children', allow_duplicate=True),
     Output('grafico-curva-corporativos', 'figure', allow_duplicate=True)],
    Input('btn-cargar-pip', 'n_clicks'),
    State('sidebar-fecha-inicio', 'date'),
    prevent_initial_call=True
)
def cargar_curvas_pip(n_clicks, fecha_inicio):
    """Carga curvas T+0 desde hist_pip para la fecha del sidebar."""
    import sys
    try:
        from curvas_rendimiento import extraer_curva_con_nombres_db

        # Cargar curvas de hist_pip para la fecha EXACTA de inicio del sidebar.
        # hist_pip es la fuente de verdad (se puebla al procesar el PIP del día).
        # Si no hay PIP para esa fecha exacta, no se cargan curvas.
        fecha_ref = pd.to_datetime(fecha_inicio).strftime('%Y-%m-%d') if fecha_inicio else None
        curvas_nom = extraer_curva_con_nombres_db(str(DB_PATH), fecha_ref)
        fecha_pip_label = pd.to_datetime(fecha_ref).strftime('%Y%m%d') if fecha_ref else "hist_pip"

        # Construir DataFrames como si vinieran del Excel (ISIN, tasa)
        df_cop = curvas_nom.get("DP_TF", pd.DataFrame())
        df_uvr = curvas_nom.get("DP_UVR", pd.DataFrame())

        if df_cop.empty and df_uvr.empty:
            _fstr = fecha_ref or "(sin fecha)"
            return html.Span(f"⚠️ No hay PIP cargado para el {_fstr}. Carga el PIP de esa fecha exacta.", style={'color': 'orange'}), None, None, html.Div(), html.Div(), go.Figure(), go.Figure(), None, html.Div(), go.Figure()

        # Convertir a formato compatible con el store (ISIN, tasa)
        # 'nodo' del PIP contiene el NEMO (TFIT..., TUVT...) que actúa como ISIN
        def pip_to_store(df_curva):
            return pd.DataFrame({'ISIN': df_curva['nodo'].values, 'tasa': df_curva['tasa'].values})

        store_cop = pip_to_store(df_cop).to_dict('records') if not df_cop.empty else None
        store_uvr = pip_to_store(df_uvr).to_dict('records') if not df_uvr.empty else None

        # Generar labels y gráficos
        def make_labels_pip(df_curva, prefix):
            return [f"{prefix} {n}" for n in df_curva['nodo'].values]

        def make_fig_pip(df_curva, labels, title, color):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=labels, y=df_curva['tasa'], mode='lines+markers', name='PIP T+0', marker=dict(size=9, color=color)))
            fig.update_layout(title=title, yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
            return fig

        def make_sliders_pip(n, labels, slider_type):
            return html.Div([
                html.Div([
                    html.Div(labels[i][-8:], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
                    dcc.Slider(id={'type': slider_type, 'index': i}, min=-150, max=150, step=5, value=0,
                               vertical=True, verticalHeight=120, marks={-150: '', 0: '0', 150: ''},
                               tooltip={'placement': 'right', 'always_visible': False}),
                ], style={'display': 'inline-block', 'width': f'{100/n:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
                for i in range(n)
            ], style={'marginTop': '5px'})

        labels_cop = make_labels_pip(df_cop, 'COP') if not df_cop.empty else []
        labels_uvr = make_labels_pip(df_uvr, 'UVR') if not df_uvr.empty else []

        fig_cop = make_fig_pip(df_cop, labels_cop, 'Curva TES COP (PIP T+0)', 'blue') if not df_cop.empty else go.Figure()
        fig_uvr = make_fig_pip(df_uvr, labels_uvr, 'Curva TES UVR (PIP T+0)', 'green') if not df_uvr.empty else go.Figure()

        sliders_cop = make_sliders_pip(len(df_cop), labels_cop, 'slider-cop') if not df_cop.empty else html.Div()
        sliders_uvr = make_sliders_pip(len(df_uvr), labels_uvr, 'slider-uvr') if not df_uvr.empty else html.Div()

        # --- Corporativos (mediana por año de vencimiento) ---
        df_corp = curvas_nom.get("CORP_TF", pd.DataFrame())
        if not df_corp.empty:
            labels_corp = [f"{n}" for n in df_corp['nodo'].values]
            store_corp = pd.DataFrame({
                'plazo_años': (df_corp['dias'].values / 365).round(1),
                'nodo': df_corp['nodo'].values,
                'fs': df_corp['tasa'].values,
                'ipc': df_corp['tasa'].values,  # CORP_IPC = proxy de CORP_TF
            }).to_dict('records')

            fig_corp = go.Figure()
            fig_corp.add_trace(go.Scatter(
                x=labels_corp, y=df_corp['tasa'], mode='lines+markers',
                name='Corp TF (mediana PIP)', marker=dict(size=9, color='#e83e8c')))
            fig_corp.update_layout(
                title='Curvas Corporativas (Mediana PIP por Año Vcto)',
                xaxis_title='Año Vcto', yaxis_title='Tasa (%)',
                height=300, margin=dict(l=50, r=10, t=35, b=10))

            sliders_corp = make_sliders_pip(len(df_corp), labels_corp, 'slider-corp')
        else:
            store_corp = None
            fig_corp = go.Figure()
            sliders_corp = html.Div()

        n_cop = len(df_cop) if not df_cop.empty else 0
        n_uvr = len(df_uvr) if not df_uvr.empty else 0
        n_corp = len(df_corp) if not df_corp.empty else 0
        msg = html.Span(f"✅ Curvas PIP cargadas - {fecha_pip_label} ({n_cop} COP, {n_uvr} UVR, {n_corp} Corp)", style={'color': 'green'})
        return msg, store_cop, store_uvr, sliders_cop, sliders_uvr, fig_cop, fig_uvr, store_corp, sliders_corp, fig_corp

    except Exception as e:
        return html.Span(f"❌ {e}", style={'color': 'red'}), None, None, html.Div(), html.Div(), go.Figure(), go.Figure(), None, html.Div(), go.Figure()


# --- Callback: Upload Datos Económicos ---
@callback(
    [Output('msg-upload-datos-eco', 'children'),
     Output('graficos-datos-eco', 'children')],
    Input('upload-datos-eco', 'contents'),
    State('upload-datos-eco', 'filename'),
    prevent_initial_call=True
)
def upload_datos_economicos(contents, filename):
    """Procesa el Excel de Estudios Económicos y genera gráficos de indicadores."""
    if contents is None:
        return no_update, no_update
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    try:
        df = cargar_indicadores_desde_bytes(decoded)
        n_guardados = guardar_indicadores_local(df)
        msg = html.Span(f"✅ {n_guardados:,} registros guardados ({len(df['nombre'].unique())} indicadores)",
                        style={'color': 'green', 'fontSize': '12px'})
        graficos = _generar_graficos_eco(df)
        return msg, graficos
    except Exception as e:
        return html.Span(f"❌ Error: {e}", style={'color': 'red', 'fontSize': '12px'}), html.Div()


# Callback para cargar gráficos de datos eco al inicializar la tab Inputs
@callback(
    Output('graficos-datos-eco', 'children', allow_duplicate=True),
    Input('refresh-trigger', 'data'),
    prevent_initial_call='initial_duplicate'
)
def init_graficos_eco(_):
    """Carga gráficos de indicadores macro si ya existen en la BD."""
    df = cargar_indicadores_desde_db()
    if df.empty:
        return html.P("No hay datos económicos cargados aún.", style={'fontSize': '11px', 'color': '#999'})
    return _generar_graficos_eco(df)


def _generar_graficos_eco(df):
    """Genera los mini-gráficos de sendas económicas (3 columnas × 3 filas)."""
    indicadores = df['nombre'].unique()
    graficos = []
    for nombre in sorted(indicadores):
        df_ind = df[df['nombre'] == nombre].sort_values('fecha')
        nombre_display = df_ind['nombre_original'].iloc[0] if 'nombre_original' in df_ind.columns else nombre
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_ind['fecha'], y=df_ind['valor'],
            mode='lines+markers', marker=dict(size=3),
            name=nombre_display
        ))
        fig.update_layout(
            title=dict(text=nombre_display, font=dict(size=11)),
            height=200, margin=dict(l=40, r=10, t=30, b=25),
            xaxis=dict(tickfont=dict(size=9)),
            yaxis=dict(tickfont=dict(size=9)),
        )
        graficos.append(
            html.Div(dcc.Graph(figure=fig, config={'displayModeBar': False}),
                     style={'width': '32%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginRight': '1%'})
        )
    return html.Div(graficos)


# --- Callback: Upload TES → llenar stores y generar sliders ---
@callback(
    [Output('msg-upload-tes', 'children'),
     Output('store-tes-cop', 'data'),
     Output('store-tes-uvr', 'data'),
     Output('sliders-tes-cop', 'children'),
     Output('sliders-tes-uvr', 'children'),
     Output('grafico-curva-tes-cop', 'figure'),
     Output('grafico-curva-tes-uvr', 'figure')],
    Input('upload-tes', 'contents'),
    State('upload-tes', 'filename'),
    prevent_initial_call=True
)
def upload_tes(contents, filename):
    import base64, io
    if contents is None:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    try:
        xls = pd.ExcelFile(io.BytesIO(decoded))
        df_cop = pd.read_excel(xls, sheet_name='TES COP')
        df_uvr = pd.read_excel(xls, sheet_name='TES UVR')

        # Normalizar tasas: si vienen como % string ("5.99%") o decimal (0.0599)
        # convertir siempre a porcentaje directo (5.99, 14.65, etc.)
        def normalizar_tasa(df):
            if 'tasa' in df.columns:
                # Si es string con %, quitar % y convertir
                if df['tasa'].dtype == object:
                    df['tasa'] = df['tasa'].astype(str).str.replace('%', '').str.strip()
                df['tasa'] = pd.to_numeric(df['tasa'], errors='coerce')
                # Si todas las tasas son < 1, están en decimal → multiplicar por 100
                if df['tasa'].max() < 1.0:
                    df['tasa'] = df['tasa'] * 100
            return df

        df_cop = normalizar_tasa(df_cop)
        df_uvr = normalizar_tasa(df_uvr)

        def make_labels(df, prefix):
            """Busca fecha_vencimiento por ISIN, ordena y genera etiquetas con mes abreviado."""
            meses = {1: 'ene', 2: 'feb', 3: 'mar', 4: 'abr', 5: 'may', 6: 'jun',
                     7: 'jul', 8: 'ago', 9: 'sep', 10: 'oct', 11: 'nov', 12: 'dic'}
            try:
                conn = get_conn()
                # Intentar ambas tablas, fallback a solo portafolio_clasificado
                try:
                    df_port_fechas = pd.read_sql_query("""
                        SELECT DISTINCT isin, fecha_vencimiento FROM portafolio_clasificado WHERE isin IS NOT NULL
                        UNION
                        SELECT DISTINCT isin, fecha_vencimiento FROM hist_titulos WHERE isin IS NOT NULL
                    """, conn)
                except Exception:
                    df_port_fechas = pd.read_sql_query("""
                        SELECT DISTINCT isin, fecha_vencimiento FROM portafolio_clasificado WHERE isin IS NOT NULL
                    """, conn)
                conn.close()
                df_port_fechas['fecha_vencimiento'] = pd.to_datetime(df_port_fechas['fecha_vencimiento'], errors='coerce')
                mapeo_fecha = dict(zip(df_port_fechas['isin'].str.strip(), df_port_fechas['fecha_vencimiento']))
            except Exception:
                mapeo_fecha = {}

            df['_fecha_vcto'] = df['ISIN'].apply(lambda x: mapeo_fecha.get(str(x).strip()))
            df.sort_values('_fecha_vcto', inplace=True, na_position='last')
            df.reset_index(drop=True, inplace=True)

            labels = []
            for _, row in df.iterrows():
                fecha = row['_fecha_vcto']
                if pd.notna(fecha):
                    mes_str = meses.get(fecha.month, str(fecha.month))
                    labels.append(f"{prefix} {mes_str}{int(fecha.year) % 100:02d}")
                else:
                    labels.append(f"{prefix} {str(row['ISIN'])[-4:]}")

            df.drop(columns=['_fecha_vcto'], inplace=True)
            return labels

        def make_fig(df, labels, title, color):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=labels, y=df['tasa'], mode='lines+markers', name='Base', marker=dict(size=9, color=color)))
            fig.update_layout(title=title, yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
            return fig

        def make_sliders(n, labels, slider_type):
            return html.Div([
                html.Div([
                    html.Div(labels[i], style={'textAlign': 'center', 'fontSize': '9px', 'marginBottom': '3px'}),
                    dcc.Slider(
                        id={'type': slider_type, 'index': i},
                        min=-150, max=150, step=5, value=0,
                        vertical=True, verticalHeight=120,
                        marks={-150: '', 0: '0', 150: ''},
                        tooltip={'placement': 'right', 'always_visible': False},
                    ),
                ], style={'display': 'inline-block', 'width': f'{100/n:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
                for i in range(n)
            ], style={'marginTop': '5px'})

        labels_cop = make_labels(df_cop, 'COP')
        labels_uvr = make_labels(df_uvr, 'UVR')

        fig_cop = make_fig(df_cop, labels_cop, 'Curva TES COP', 'blue')
        fig_uvr = make_fig(df_uvr, labels_uvr, 'Curva TES UVR', 'green')

        sliders_cop = make_sliders(len(df_cop), labels_cop, 'slider-cop')
        sliders_uvr = make_sliders(len(df_uvr), labels_uvr, 'slider-uvr')

        msg = html.Span(f"✅ {filename} ({len(df_cop)} COP, {len(df_uvr)} UVR)", style={'color': 'green'})
        return msg, df_cop.to_dict('records'), df_uvr.to_dict('records'), sliders_cop, sliders_uvr, fig_cop, fig_uvr

    except Exception as e:
        return html.Span(f"❌ {e}", style={'color': 'red'}), None, None, html.Div(), html.Div(), go.Figure(), go.Figure()


def _get_sorted_labels_and_order(df, prefix):
    """Helper: ordena df por fecha_vencimiento y genera labels con mes abreviado."""
    meses = {1: 'ene', 2: 'feb', 3: 'mar', 4: 'abr', 5: 'may', 6: 'jun',
             7: 'jul', 8: 'ago', 9: 'sep', 10: 'oct', 11: 'nov', 12: 'dic'}

    try:
        conn = get_conn()
        try:
            df_port_fechas = pd.read_sql_query("""
                SELECT DISTINCT isin, fecha_vencimiento FROM portafolio_clasificado WHERE isin IS NOT NULL
                UNION
                SELECT DISTINCT isin, fecha_vencimiento FROM hist_titulos WHERE isin IS NOT NULL
            """, conn)
        except Exception:
            df_port_fechas = pd.read_sql_query("""
                SELECT DISTINCT isin, fecha_vencimiento FROM portafolio_clasificado WHERE isin IS NOT NULL
            """, conn)
        conn.close()
        df_port_fechas['fecha_vencimiento'] = pd.to_datetime(df_port_fechas['fecha_vencimiento'], errors='coerce')
        mapeo_fecha = dict(zip(df_port_fechas['isin'].str.strip(), df_port_fechas['fecha_vencimiento']))
    except Exception:
        mapeo_fecha = {}

    df['_fecha_vcto'] = df['ISIN'].apply(lambda x: mapeo_fecha.get(str(x).strip()))
    df = df.sort_values('_fecha_vcto', na_position='last').reset_index(drop=True)

    labels = []
    for _, row in df.iterrows():
        fecha = row['_fecha_vcto']
        if pd.notna(fecha):
            mes_str = meses.get(fecha.month, str(fecha.month))
            labels.append(f"{prefix} {mes_str}{int(fecha.year) % 100:02d}")
        else:
            labels.append(f"{prefix} {str(row['ISIN'])[-4:]}")

    df = df.drop(columns=['_fecha_vcto'])
    return df, labels


# --- Callback: TES COP sliders → actualizar gráfico ---
@callback(
    Output('grafico-curva-tes-cop', 'figure', allow_duplicate=True),
    Input({'type': 'slider-cop', 'index': ALL}, 'value'),
    State('store-tes-cop', 'data'),
    prevent_initial_call=True
)
def update_tes_cop_sliders(slider_values, store_data):
    if not store_data or not slider_values:
        return no_update
    df = pd.DataFrame(store_data)
    df['tasa'] = pd.to_numeric(df['tasa'], errors='coerce')
    df, labels = _get_sorted_labels_and_order(df, 'COP')
    n = len(df)
    if len(slider_values) != n:
        return no_update

    y_ajustada = [df['tasa'].iloc[i] + slider_values[i] / 100 for i in range(n)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=df['tasa'], mode='lines+markers', name='Base', line=dict(dash='dot', color='gray'), marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=labels, y=y_ajustada, mode='lines+markers', name='Escenario', marker=dict(size=9, color='blue')))
    fig.update_layout(title='Curva TES COP', yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# --- Callback: TES UVR sliders → actualizar gráfico ---
@callback(
    Output('grafico-curva-tes-uvr', 'figure', allow_duplicate=True),
    Input({'type': 'slider-uvr', 'index': ALL}, 'value'),
    State('store-tes-uvr', 'data'),
    prevent_initial_call=True
)
def update_tes_uvr_sliders(slider_values, store_data):
    if not store_data or not slider_values:
        return no_update
    df = pd.DataFrame(store_data)
    df['tasa'] = pd.to_numeric(df['tasa'], errors='coerce')
    df, labels = _get_sorted_labels_and_order(df, 'UVR')
    n = len(df)
    if len(slider_values) != n:
        return no_update

    y_ajustada = [df['tasa'].iloc[i] + slider_values[i] / 100 for i in range(n)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=df['tasa'], mode='lines+markers', name='Base', line=dict(dash='dot', color='gray'), marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=labels, y=y_ajustada, mode='lines+markers', name='Escenario', marker=dict(size=9, color='green')))
    fig.update_layout(title='Curva TES UVR', yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# --- Callbacks: Reset buttons ---
@callback(
    [Output('sliders-tes-cop', 'children', allow_duplicate=True),
     Output('grafico-curva-tes-cop', 'figure', allow_duplicate=True)],
    Input('btn-reset-cop', 'n_clicks'),
    State('store-tes-cop', 'data'),
    prevent_initial_call=True
)
def reset_cop(n, store_data):
    if not store_data:
        return no_update, no_update
    df = pd.DataFrame(store_data)
    df['tasa'] = pd.to_numeric(df['tasa'], errors='coerce')
    df, labels = _get_sorted_labels_and_order(df, 'COP')
    n_pts = len(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=df['tasa'], mode='lines+markers', name='Base', marker=dict(size=9, color='blue')))
    fig.update_layout(title='Curva TES COP', yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
    sliders = html.Div([
        html.Div([
            html.Div(labels[i], style={'textAlign': 'center', 'fontSize': '9px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-cop', 'index': i}, min=-150, max=150, step=5, value=0,
                       vertical=True, verticalHeight=120, marks={-150: '', 0: '0', 150: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})
    return sliders, fig


@callback(
    [Output('sliders-tes-uvr', 'children', allow_duplicate=True),
     Output('grafico-curva-tes-uvr', 'figure', allow_duplicate=True)],
    Input('btn-reset-uvr', 'n_clicks'),
    State('store-tes-uvr', 'data'),
    prevent_initial_call=True
)
def reset_uvr(n, store_data):
    if not store_data:
        return no_update, no_update
    df = pd.DataFrame(store_data)
    df['tasa'] = pd.to_numeric(df['tasa'], errors='coerce')
    df, labels = _get_sorted_labels_and_order(df, 'UVR')
    n_pts = len(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=df['tasa'], mode='lines+markers', name='Base', marker=dict(size=9, color='green')))
    fig.update_layout(title='Curva TES UVR', yaxis_title='Tasa (%)', height=300, margin=dict(l=50, r=10, t=35, b=10))
    sliders = html.Div([
        html.Div([
            html.Div(labels[i], style={'textAlign': 'center', 'fontSize': '9px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-uvr', 'index': i}, min=-150, max=150, step=5, value=0,
                       vertical=True, verticalHeight=120, marks={-150: '', 0: '0', 150: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})
    return sliders, fig


@callback(
    [Output('sliders-trm', 'children', allow_duplicate=True),
     Output('grafico-senda-trm', 'figure', allow_duplicate=True)],
    Input('btn-reset-trm', 'n_clicks'),
    State('store-sendas', 'data'),
    prevent_initial_call=True
)
def reset_trm(n, store_data):
    if not store_data:
        return no_update, no_update
    df = pd.DataFrame(store_data)
    df['TRM'] = pd.to_numeric(df['TRM'], errors='coerce')
    n_pts = len(df)
    meses_labels = [str(m) for m in df['mes']]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=meses_labels, y=df['TRM'], mode='lines+markers', name='Base', marker=dict(size=9)))
    fig.update_layout(title='Senda TRM', height=300, margin=dict(l=50, r=10, t=35, b=10))
    sliders = html.Div([
        html.Div([
            html.Div(meses_labels[i][-6:], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-trm', 'index': i}, min=-500, max=500, step=25, value=0,
                       vertical=True, verticalHeight=100, marks={-500: '', 0: '0', 500: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})
    return sliders, fig


@callback(
    [Output('sliders-inflacion', 'children', allow_duplicate=True),
     Output('grafico-senda-inflacion', 'figure', allow_duplicate=True)],
    Input('btn-reset-inf', 'n_clicks'),
    State('store-sendas', 'data'),
    prevent_initial_call=True
)
def reset_inf(n, store_data):
    if not store_data:
        return no_update, no_update
    df = pd.DataFrame(store_data)
    df['inflación'] = pd.to_numeric(df['inflación'], errors='coerce')
    n_pts = len(df)
    meses_labels = [str(m) for m in df['mes']]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=meses_labels, y=df['inflación'], mode='lines+markers', name='Base', marker=dict(size=9)))
    fig.update_layout(title='Senda Inflación', yaxis_tickformat='.2%', height=300, margin=dict(l=50, r=10, t=35, b=10))
    sliders = html.Div([
        html.Div([
            html.Div(meses_labels[i][-6:], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-inf', 'index': i}, min=-200, max=200, step=10, value=0,
                       vertical=True, verticalHeight=100, marks={-200: '', 0: '0', 200: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})
    return sliders, fig


# --- Callback: Generar senda lineal ---
@callback(
    [Output('msg-generar-senda', 'children'),
     Output('store-sendas', 'data', allow_duplicate=True),
     Output('sliders-trm', 'children', allow_duplicate=True),
     Output('sliders-inflacion', 'children', allow_duplicate=True),
     Output('grafico-senda-trm', 'figure', allow_duplicate=True),
     Output('grafico-senda-inflacion', 'figure', allow_duplicate=True)],
    Input('btn-generar-senda', 'n_clicks'),
    [State('input-trm-hoy', 'value'),
     State('input-trm-final', 'value'),
     State('input-inf-hoy', 'value'),
     State('input-inf-final', 'value'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def generar_senda_lineal(n_clicks, trm_hoy, trm_final, inf_hoy, inf_final, fecha_inicio_str, fecha_fin_str):
    if not all([trm_hoy, trm_final, inf_hoy, inf_final]):
        return "Completa todos los campos.", no_update, no_update, no_update, no_update, no_update

    from dateutil.relativedelta import relativedelta

    fecha_inicio = pd.to_datetime(fecha_inicio_str).date() if fecha_inicio_str else date.today()
    fecha_fin = pd.to_datetime(fecha_fin_str).date() if fecha_fin_str else fecha_inicio + timedelta(days=365)

    # Extender 4 meses más allá de fecha_fin para renovaciones de forwards
    fecha_fin_extendida = fecha_fin + relativedelta(months=4)

    # Generar cortes reales entre fecha_inicio y fecha_fin_extendida
    meses_es = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

    fecha_corte_ts = pd.Timestamp(fecha_inicio)
    fecha_fin_ts = pd.Timestamp(fecha_fin_extendida)

    # Generar fines de mes entre corte y fin extendido, luego incluir fecha_fin_extendida si no coincide
    fechas_me = pd.date_range(start=fecha_corte_ts, end=fecha_fin_ts, freq='ME')
    fechas_cortes = pd.DatetimeIndex([fecha_corte_ts]).union(fechas_me)
    if fecha_fin_ts not in fechas_cortes:
        fechas_cortes = fechas_cortes.union(pd.DatetimeIndex([fecha_fin_ts]))

    # Etiquetas: fecha_corte como "Corte" y luego mes-año
    meses_labels = [f"{meses_es[f.month - 1]}-{f.year % 100:02d}" for f in fechas_cortes]
    meses_labels[0] = 'Corte'

    n_pts = len(fechas_cortes)

    # Generar puntos lineales (interpolar hasta fecha_fin, luego mantener plano los 4 extra)
    # Encontrar cuántos puntos corresponden a fecha_fin original
    fecha_fin_orig_ts = pd.Timestamp(fecha_fin)
    n_pts_orig = sum(1 for f in fechas_cortes if f <= fecha_fin_orig_ts)
    if n_pts_orig < 2:
        n_pts_orig = n_pts

    trm_senda_orig = np.linspace(float(trm_hoy), float(trm_final), n_pts_orig)
    # Extender con el valor final para los 4 nodos adicionales
    trm_senda = np.concatenate([trm_senda_orig, np.full(n_pts - n_pts_orig, float(trm_final))])

    inf_senda_orig = np.linspace(float(inf_hoy) / 100, float(inf_final) / 100, n_pts_orig)
    inf_senda = np.concatenate([inf_senda_orig, np.full(n_pts - n_pts_orig, float(inf_final) / 100)])

    df = pd.DataFrame({'mes': meses_labels, 'TRM': trm_senda, 'inflación': inf_senda})

    # Gráficos
    fig_trm = go.Figure()
    fig_trm.add_trace(go.Scatter(x=meses_labels, y=trm_senda, mode='lines+markers', name='Base', marker=dict(size=9)))
    fig_trm.update_layout(title='Senda TRM (lineal)', height=300, margin=dict(l=50, r=10, t=35, b=10))

    fig_inf = go.Figure()
    fig_inf.add_trace(go.Scatter(x=meses_labels, y=inf_senda, mode='lines+markers', name='Base', marker=dict(size=9)))
    fig_inf.update_layout(title='Senda Inflación (lineal)', yaxis_tickformat='.2%', height=300, margin=dict(l=50, r=10, t=35, b=10))

    # Sliders TRM
    sliders_trm = html.Div([
        html.Div([
            html.Div(meses_labels[i], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-trm', 'index': i}, min=-500, max=500, step=25, value=0,
                       vertical=True, verticalHeight=100, marks={-500: '', 0: '0', 500: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})

    # Sliders Inflación
    sliders_inf = html.Div([
        html.Div([
            html.Div(meses_labels[i], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
            dcc.Slider(id={'type': 'slider-inf', 'index': i}, min=-200, max=200, step=10, value=0,
                       vertical=True, verticalHeight=100, marks={-200: '', 0: '0', 200: ''},
                       tooltip={'placement': 'right', 'always_visible': False}),
        ], style={'display': 'inline-block', 'width': f'{100/n_pts:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
        for i in range(n_pts)
    ], style={'marginTop': '5px'})

    msg = html.Span(f"✅ Senda lineal generada ({n_pts} puntos, TRM {trm_hoy}→{trm_final})", style={'color': 'green'})
    return msg, df.to_dict('records'), sliders_trm, sliders_inf, fig_trm, fig_inf


# --- Callback: Upload Sendas → sliders + gráficos ---
@callback(
    [Output('msg-upload-sendas', 'children'),
     Output('store-sendas', 'data'),
     Output('sliders-trm', 'children'),
     Output('sliders-inflacion', 'children'),
     Output('grafico-senda-trm', 'figure'),
     Output('grafico-senda-inflacion', 'figure')],
    Input('upload-sendas', 'contents'),
    State('upload-sendas', 'filename'),
    prevent_initial_call=True
)
def upload_sendas(contents, filename):
    import base64, io
    if contents is None:
        return no_update, no_update, no_update, no_update, no_update, no_update
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    try:
        df = pd.read_excel(io.BytesIO(decoded))
        df['TRM'] = pd.to_numeric(df['TRM'], errors='coerce')
        df['inflación'] = pd.to_numeric(df['inflación'], errors='coerce')
        n = len(df)
        meses_labels = [str(m) for m in df['mes']]

        fig_trm = go.Figure()
        fig_trm.add_trace(go.Scatter(x=meses_labels, y=df['TRM'], mode='lines+markers', name='Base', marker=dict(size=9)))
        fig_trm.update_layout(title='Senda TRM', height=300, margin=dict(l=50, r=10, t=35, b=10))

        fig_inf = go.Figure()
        fig_inf.add_trace(go.Scatter(x=meses_labels, y=df['inflación'], mode='lines+markers', name='Base', marker=dict(size=9)))
        fig_inf.update_layout(title='Senda Inflación', yaxis_tickformat='.2%', height=300, margin=dict(l=50, r=10, t=35, b=10))

        sliders_trm = html.Div([
            html.Div([
                html.Div(meses_labels[i][-6:], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
                dcc.Slider(id={'type': 'slider-trm', 'index': i}, min=-500, max=500, step=25, value=0,
                           vertical=True, verticalHeight=100, marks={-500: '', 0: '0', 500: ''},
                           tooltip={'placement': 'right', 'always_visible': False}),
            ], style={'display': 'inline-block', 'width': f'{100/n:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
            for i in range(n)
        ], style={'marginTop': '5px'})

        sliders_inf = html.Div([
            html.Div([
                html.Div(meses_labels[i][-6:], style={'textAlign': 'center', 'fontSize': '8px', 'marginBottom': '3px'}),
                dcc.Slider(id={'type': 'slider-inf', 'index': i}, min=-200, max=200, step=10, value=0,
                           vertical=True, verticalHeight=100, marks={-200: '', 0: '0', 200: ''},
                           tooltip={'placement': 'right', 'always_visible': False}),
            ], style={'display': 'inline-block', 'width': f'{100/n:.0f}%', 'textAlign': 'center', 'verticalAlign': 'top'})
            for i in range(n)
        ], style={'marginTop': '5px'})

        msg = html.Span(f"✅ {filename} ({n} meses)", style={'color': 'green'})
        return msg, df.to_dict('records'), sliders_trm, sliders_inf, fig_trm, fig_inf
    except Exception as e:
        return html.Span(f"❌ {e}", style={'color': 'red'}), None, html.Div(), html.Div(), go.Figure(), go.Figure()


# --- Callback: TRM sliders → actualizar gráfico ---
@callback(
    Output('grafico-senda-trm', 'figure', allow_duplicate=True),
    Input({'type': 'slider-trm', 'index': ALL}, 'value'),
    State('store-sendas', 'data'),
    prevent_initial_call=True
)
def update_trm_sliders(slider_values, store_data):
    if not store_data or not slider_values:
        return no_update
    df = pd.DataFrame(store_data)
    df['TRM'] = pd.to_numeric(df['TRM'], errors='coerce')
    n = len(df)
    if len(slider_values) != n:
        return no_update
    meses_labels = [str(m) for m in df['mes']]
    y_base = df['TRM'].tolist()
    y_ajustada = [df['TRM'].iloc[i] + slider_values[i] for i in range(n)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=meses_labels, y=y_base, mode='lines+markers', name='Base', line=dict(dash='dot', color='gray'), marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=meses_labels, y=y_ajustada, mode='lines+markers', name='Escenario', marker=dict(size=9, color='orange')))
    fig.update_layout(title='Senda TRM', height=300, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# --- Callback: Inflación sliders → actualizar gráfico ---
@callback(
    Output('grafico-senda-inflacion', 'figure', allow_duplicate=True),
    Input({'type': 'slider-inf', 'index': ALL}, 'value'),
    State('store-sendas', 'data'),
    prevent_initial_call=True
)
def update_inf_sliders(slider_values, store_data):
    if not store_data or not slider_values:
        return no_update
    df = pd.DataFrame(store_data)
    df['inflación'] = pd.to_numeric(df['inflación'], errors='coerce')
    n = len(df)
    if len(slider_values) != n:
        return no_update
    meses_labels = [str(m) for m in df['mes']]
    y_ajustada = [df['inflación'].iloc[i] + slider_values[i] / 10000 for i in range(n)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=meses_labels, y=df['inflación'], mode='lines+markers', name='Base', line=dict(dash='dot', color='gray'), marker=dict(size=7)))
    fig.add_trace(go.Scatter(x=meses_labels, y=y_ajustada, mode='lines+markers', name='Escenario', marker=dict(size=9, color='purple')))
    fig.update_layout(title='Senda Inflación', yaxis_tickformat='.2%', height=300, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# --- Callback: Upload Corporativos ---
@callback(
    [Output('msg-upload-corporativos', 'children'),
     Output('store-corporativos', 'data')],
    Input('upload-corporativos', 'contents'),
    State('upload-corporativos', 'filename'),
    prevent_initial_call=True
)
def upload_corporativos(contents, filename):
    import base64, io
    if contents is None:
        return no_update, no_update
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)
    try:
        df = pd.read_excel(io.BytesIO(decoded))
        msg = html.Span(f"✅ {filename} ({len(df)} puntos)", style={'color': 'green'})
        return msg, df.to_dict('records')
    except Exception as e:
        return html.Span(f"❌ {e}", style={'color': 'red'}), None


# --- Callback: Corporativos gráfico reactivo (shift + twist por curva) ---
@callback(
    Output('grafico-curva-corporativos', 'figure'),
    [Input('store-corporativos', 'data'),
     Input('slider-fs-shift', 'value'),
     Input('slider-fs-twist', 'value'),
     Input('slider-ipc-shift', 'value'),
     Input('slider-ipc-twist', 'value')],
    prevent_initial_call=True
)
def update_corporativos_grafico(data, fs_shift, fs_twist, ipc_shift, ipc_twist):
    if not data:
        return go.Figure()

    df = pd.DataFrame(data)

    # Si viene del PIP (tiene columna 'nodo'), usar nodos como eje X
    if 'nodo' in df.columns:
        x_labels = df['nodo'].astype(str).tolist()
    elif 'plazo_años' in df.columns:
        x_labels = df['plazo_años'].astype(str).tolist()
    else:
        x_labels = list(range(len(df)))

    df['fs'] = pd.to_numeric(df.get('fs', 0), errors='coerce').fillna(0)
    df['ipc'] = pd.to_numeric(df.get('ipc', 0), errors='coerce').fillna(0)
    n = len(df)
    posiciones = np.linspace(-1, 1, n)

    fs_ajustada = df['fs'] + fs_shift / 10000 + (posiciones * fs_twist) / 10000
    ipc_ajustada = df['ipc'] + ipc_shift / 10000 + (posiciones * ipc_twist) / 10000

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_labels, y=df['fs'], mode='lines', name='FS Base (mediana PIP)', line=dict(dash='dot', color='#e83e8c')))
    fig.add_trace(go.Scatter(x=x_labels, y=df['ipc'], mode='lines', name='IPC Base', line=dict(dash='dot', color='lightsalmon')))
    fig.add_trace(go.Scatter(x=x_labels, y=fs_ajustada, mode='lines+markers', name='FS Escenario', line=dict(color='#e83e8c')))
    fig.add_trace(go.Scatter(x=x_labels, y=ipc_ajustada, mode='lines+markers', name='IPC Escenario', line=dict(color='darkorange')))
    fig.update_layout(title='Curvas Corporativas (Mediana PIP + Shift/Twist)',
                      xaxis_title='Año Vcto', yaxis_title='Tasa (%)',
                      yaxis_tickformat='.2%', height=350, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# --- Callback: Corporativos sliders por nodo → actualizar gráfico ---
@callback(
    Output('grafico-curva-corporativos', 'figure', allow_duplicate=True),
    Input({'type': 'slider-corp', 'index': ALL}, 'value'),
    State('store-corporativos', 'data'),
    prevent_initial_call=True
)
def update_corp_sliders(slider_values, store_data):
    """Actualiza la gráfica corporativa cuando se mueven los sliders por nodo."""
    if not store_data or not slider_values:
        return no_update
    df = pd.DataFrame(store_data)
    df['fs'] = pd.to_numeric(df.get('fs', 0), errors='coerce').fillna(0)
    df['ipc'] = pd.to_numeric(df.get('ipc', 0), errors='coerce').fillna(0)
    n = len(df)
    if len(slider_values) != n:
        return no_update

    if 'nodo' in df.columns:
        x_labels = df['nodo'].astype(str).tolist()
    elif 'plazo_años' in df.columns:
        x_labels = df['plazo_años'].astype(str).tolist()
    else:
        x_labels = list(range(n))

    # Sliders aplican shift individual en bps → convertir a %
    fs_ajustada = [df['fs'].iloc[i] + slider_values[i] / 10000 for i in range(n)]
    ipc_ajustada = [df['ipc'].iloc[i] + slider_values[i] / 10000 for i in range(n)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_labels, y=df['fs'].tolist(), mode='lines', name='FS Base (mediana PIP)',
                             line=dict(dash='dot', color='#e83e8c')))
    fig.add_trace(go.Scatter(x=x_labels, y=df['ipc'].tolist(), mode='lines', name='IPC Base',
                             line=dict(dash='dot', color='lightsalmon')))
    fig.add_trace(go.Scatter(x=x_labels, y=fs_ajustada, mode='lines+markers', name='FS Escenario',
                             line=dict(color='#e83e8c')))
    fig.add_trace(go.Scatter(x=x_labels, y=ipc_ajustada, mode='lines+markers', name='IPC Escenario',
                             line=dict(color='darkorange')))
    fig.update_layout(title='Curvas Corporativas (Shift por Nodo)',
                      xaxis_title='Año Vcto', yaxis_title='Tasa (%)',
                      yaxis_tickformat='.2%', height=350, margin=dict(l=50, r=10, t=35, b=10))
    return fig


# =============================================================================
# PESTAÑA 5: PROYECCIONES (ahora solo Variables Adicionales — lo principal está en Inputs)
# =============================================================================
def layout_proyecciones():
    """DEPRECATED — Los inputs de Proyecciones ahora viven en la Tab Inputs (secciones 6-9)."""
    return html.Div(style={'display': 'none'})


@callback(
    Output('desc-metodo', 'children'),
    Input('proy-metodo', 'value')
)
def describir_metodo(metodo):
    descripciones = {
        1: "Nominal Estable: Renueva forwards y collares al vencimiento con el mismo nominal. Mantiene la cobertura actual.",
        3: "Exposición Objetivo: Genera nuevos forwards y collares para alcanzar un % objetivo de exposición en USD.",
        4: "Dejar vencer collares: Renueva forwards al vencimiento pero deja que las opciones (collares) se venzan sin renovar.",
        5: "Delta Hedge: No renueva forwards. En cada corte genera forwards para neutralizar el delta de las opciones vigentes.",
    }
    return descripciones.get(metodo, "")


# --- Callback: Gráfica Curva EEUU reactiva ---
@callback(
    Output('grafico-curva-eeuu', 'figure'),
    [Input('esc-fed', 'value'),
     Input('esc-tesoros10y', 'value'),
     Input('esc-fed-t0', 'value'),
     Input('esc-tesoros10y-t0', 'value')],
    prevent_initial_call=False
)
def actualizar_grafica_curva_eeuu(delta_fed, delta_tesoros, fed_t0, tesoros_t0):
    """
    Actualiza la gráfica de Curva EEUU mostrando curva base (punteada gris)
    y curva desplazada (sólida color) con los deltas aplicados progresivamente.
    Afecta opciones y swaps (NO forwards).
    """
    # Niveles T0 desde inputs del usuario
    _fed = float(fed_t0 or 5.25)
    _tes = float(tesoros_t0 or 4.25)

    # Interpolar nodos intermedios (2Y, 5Y) linealmente entre Fed y 10Y
    _2y = _fed + (_tes - _fed) * 0.3
    _5y = _fed + (_tes - _fed) * 0.7

    nodos = ['Fed (O/N)', '2Y', '5Y', '10Y']
    base = [_fed, _2y, _5y, _tes]

    # Aplicar deltas progresivamente (Fed afecta corto, Tesoros afecta largo)
    d_fed = float(delta_fed or 0) / 100.0  # bps a %
    d_tes = float(delta_tesoros or 0) / 100.0

    # Interpolación progresiva: Fed domina O/N-2Y, Tesoros domina 5Y-10Y
    pesos_fed = [1.0, 0.6, 0.3, 0.0]
    pesos_tes = [0.0, 0.4, 0.7, 1.0]

    shifted = [
        b + d_fed * pf + d_tes * pt
        for b, pf, pt in zip(base, pesos_fed, pesos_tes)
    ]

    fig = go.Figure()

    # Curva base (punteada gris)
    fig.add_trace(go.Scatter(
        x=nodos, y=base,
        mode='lines+markers',
        line=dict(width=2, color='#adb5bd', dash='dot'),
        marker=dict(size=6, color='#adb5bd'),
        name='Base T0',
    ))

    # Curva desplazada (sólida color)
    tiene_shift = abs(d_fed) > 0.001 or abs(d_tes) > 0.001
    if tiene_shift:
        fig.add_trace(go.Scatter(
            x=nodos, y=shifted,
            mode='lines+markers',
            line=dict(width=3, color='#1f77b4'),
            marker=dict(size=8, color='#1f77b4'),
            name=f'Escenario (Δ Fed {int(delta_fed or 0)} / Δ 10Y {int(delta_tesoros or 0)} bps)',
        ))

    fig.update_layout(
        title='Curva EEUU (afecta Opciones y Swaps — NO Forwards)',
        yaxis_title='Tasa (%)',
        height=260,
        margin=dict(l=40, r=20, t=50, b=30),
        showlegend=True,
        legend=dict(x=0.02, y=0.98, font=dict(size=9)),
    )

    return fig


# --- Callback: Calcular tasa EA FCP 4G en tiempo real ---
@callback(
    Output('esc-tasa-4g-calc', 'children'),
    [Input('esc-ipc-4g', 'value'),
     Input('esc-spread-4g', 'value')]
)
def calcular_tasa_4g(ipc_val, spread_val):
    """Muestra la tasa EA compuesta: (1+IPC)*(1+spread)-1."""
    ipc = float(ipc_val or 0) / 100
    spread = float(spread_val or 0) / 100
    tasa_ea = (1 + ipc) * (1 + spread) - 1
    return f"{tasa_ea * 100:.2f}% EA"



# --- Callback: Mostrar UVR T0 y UVR Cierre calculadas (solo informativo) ---
@callback(
    Output('esc-uvr-info', 'children'),
    [Input('sidebar-fecha-inicio', 'date'),
     Input('sidebar-fecha-fin', 'date'),
     Input('store-sendas', 'data')],
    prevent_initial_call=False
)
def mostrar_uvr_calculada(fecha_inicio, fecha_fin, store_sendas):
    """
    Muestra (solo informativo) la UVR T0 vigente y la UVR de cierre calculada
    capitalizando con la senda de inflación mensual. No hay inputs editables:
    la UVR se deriva de la inflación para mantener coherencia.
    """
    if not fecha_inicio:
        return "Selecciona una fecha de corte en el sidebar."

    fecha_ini = pd.to_datetime(fecha_inicio).date()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + timedelta(days=365)

    uvr_t0 = obtener_uvr_para(fecha_ini.strftime('%Y-%m-%d'))
    if not uvr_t0:
        return f"⚠️ No hay UVR vigente para {fecha_ini}."
    uvr_base = float(uvr_t0)

    uvr_cierre = _calcular_uvr_cierre_desde_inflacion(uvr_base, fecha_ini, fecha_end, store_sendas)
    if not uvr_cierre:
        return f"UVR T0: {uvr_base:.2f} · UVR Cierre: (carga la senda de inflación)"

    dias_total = (fecha_end - fecha_ini).days
    if dias_total > 0 and uvr_base > 0:
        inf_anual_equiv = ((uvr_cierre / uvr_base) ** (365 / dias_total) - 1) * 100
    else:
        inf_anual_equiv = 0.0
    return (f"UVR T0: {uvr_base:.2f}  →  UVR Cierre: {uvr_cierre:.2f}  "
            f"(≈ {inf_anual_equiv:.1f}% EA)")


# --- Callbacks Senda Inflación: Cargar desde archivo y generar sliders ---
@callback(
    [Output('store-senda-inf-esc', 'data'),
     Output('sliders-inf-esc', 'children'),
     Output('grafico-senda-inf-esc', 'figure')],
    [Input('btn-cargar-senda-inflacion', 'n_clicks'),
     Input('store-sendas', 'data')],
    [State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def cargar_senda_inflacion(n_clicks, store_sendas, fecha_inicio, fecha_fin):
    """
    Despeja la inflación MENSUAL desde la senda de inflación ANUAL de la
    Sección 2 (store-sendas), usando las ponderaciones estacionales.
    La inflación mensual de cada mes = (1 + IPC_fin_de_año)^ponderacion[mes] - 1.
    """
    from dateutil.relativedelta import relativedelta
    from libro_de_funciones import (
        construir_senda_yoy_mensual, despejar_inflacion_mensual_desde_yoy,
    )

    fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else date.today()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + timedelta(days=365)

    delta = relativedelta(fecha_end, fecha_ini)
    n_meses = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
    if n_meses < 1:
        n_meses = 12

    # ── YoY vigente (primer punto de la senda Sección 2) y YoY final (último) ──
    yoy_vigente, yoy_final = 0.06, 0.04
    if store_sendas:
        try:
            df_s = pd.DataFrame(store_sendas)
            df_s['inflación'] = pd.to_numeric(df_s['inflación'], errors='coerce')
            vals = df_s['inflación'].dropna().tolist()
            if vals:
                yoy_vigente = float(vals[0])   # inflación hoy (YoY vigente)
                yoy_final = float(vals[-1])    # inflación a la fecha fin (YoY)
        except Exception as e:
            print(f"[SENDA UVR] Error leyendo senda Sección 2: {e}")

    # ── Construir senda YoY mensual y despejar inflación mensual ──
    senda_yoy = construir_senda_yoy_mensual(yoy_vigente, yoy_final, fecha_ini, n_meses)
    mensuales = despejar_inflacion_mensual_desde_yoy(senda_yoy, yoy_vigente, fecha_ini, n_meses)
    senda_data = [
        {'fecha': d['fecha'], 'inf_mensual': d['inf_mensual'], 'inf_anual': d['yoy'] * 100.0}
        for d in mensuales
    ]

    # Gráfico de barras: inflación mensual (%)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[d['fecha'] for d in senda_data],
        y=[d['inf_mensual'] * 100 for d in senda_data],
        marker_color='#fd7e14', name='Inflación mensual (%)'))
    fig.update_layout(
        title='Inflación mensual despejada (ponderaciones estacionales)',
        yaxis_title='%', yaxis_tickformat='.2f',
        height=220, margin=dict(l=40, r=10, t=30, b=30),
        xaxis_tickangle=-45, xaxis_tickfont=dict(size=9))

    # Sliders (uno por mes, ajuste fino en bps)
    sliders = []
    for i, d in enumerate(senda_data):
        sliders.append(html.Div([
            html.Span(d['fecha'][-5:], style={'fontSize': '9px', 'width': '40px', 'display': 'inline-block'}),
            dcc.Slider(
                id={'type': 'slider-inf-mensual', 'index': i},
                min=-50, max=50, step=1, value=0,
                marks={-50: '-50', 0: '0', 50: '+50'},
                tooltip={'placement': 'bottom', 'always_visible': False},
            ),
            html.Span(f"{d['inf_mensual']*100:.2f}%", style={'fontSize': '9px', 'color': '#666', 'marginLeft': '5px'}),
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '2px'}))

    return senda_data, sliders, fig


@callback(
    [Output('store-senda-inf-esc', 'data', allow_duplicate=True),
     Output('grafico-senda-inf-esc', 'figure', allow_duplicate=True)],
    Input({'type': 'slider-inf-mensual', 'index': ALL}, 'value'),
    State('store-senda-inf-esc', 'data'),
    prevent_initial_call=True
)
def update_senda_inflacion_sliders(slider_values, store_data):
    """Actualiza la senda de inflación cuando se mueven los sliders (delta en bps)."""
    if not store_data or not slider_values:
        return no_update, no_update

    # Aplicar deltas de sliders
    senda_actualizada = []
    for i, item in enumerate(store_data):
        delta_bps = slider_values[i] if i < len(slider_values) else 0
        inf_nueva = item['inf_mensual'] + (delta_bps or 0) / 10000.0
        # Recalcular inf_anual: (1 + inf_mensual)^12 - 1 en %
        inf_anual_pct = ((1 + inf_nueva) ** 12 - 1) * 100.0
        senda_actualizada.append({'fecha': item['fecha'], 'inf_mensual': inf_nueva, 'inf_anual': inf_anual_pct})

    # Gráfico actualizado
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[d['fecha'] for d in senda_actualizada],
        y=[d['inf_mensual'] * 100 for d in senda_actualizada],
        marker_color='#fd7e14', name='Inflación mensual (%)'))
    fig.update_layout(
        yaxis_title='%', yaxis_tickformat='.2f',
        height=200, margin=dict(l=40, r=10, t=20, b=30),
        xaxis_tickangle=-45, xaxis_tickfont=dict(size=9))

    return senda_actualizada, fig


# --- Callbacks Simultaneas: Generar senda repo con sliders ---
@callback(
    [Output('store-senda-repo', 'data'),
     Output('sliders-repo', 'children'),
     Output('grafico-senda-repo', 'figure')],
    Input('btn-generar-senda-repo', 'n_clicks'),
    [State('esc-repo-ini', 'value'),
     State('esc-repo-fin', 'value'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def generar_senda_repo(n_clicks, repo_ini_val, repo_fin_val, fecha_inicio, fecha_fin):
    """Genera senda lineal de tasa repo entre fecha inicio y fecha fin del sidebar."""
    from dateutil.relativedelta import relativedelta

    fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else pd.Timestamp.today().date()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + relativedelta(months=12)

    # Calcular meses entre inicio y fin
    delta = relativedelta(fecha_end, fecha_ini)
    n = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
    if n < 1:
        n = 1

    r_ini = float(repo_ini_val or 9.75)
    r_fin = float(repo_fin_val or 7.75)

    periodos = pd.date_range(start=fecha_ini.replace(day=1), periods=n, freq='MS')
    tasas = np.linspace(r_ini, r_fin, n)
    labels = [d.strftime('%Y-%m') for d in periodos]

    store = [{'fecha': labels[i], 'tasa': round(tasas[i], 2)} for i in range(n)]

    # Meses con decision de tasa: Ene(1), Mar(3), Abr(4), Jun(6), Jul(7), Sep(9), Oct(10), Dic(12)
    MESES_DECISION = {1, 3, 4, 6, 7, 9, 10, 12}

    # Sliders solo en meses con decision de tasa
    sliders_items = []
    for i in range(n):
        mes_num = periodos[i].month
        if mes_num in MESES_DECISION:
            sliders_items.append(
                html.Div([
                    html.Div(labels[i], style={'textAlign': 'center', 'fontSize': '9px', 'fontWeight': 'bold', 'marginBottom': '2px'}),
                    html.Div(f"{tasas[i]:.1f}%", style={'textAlign': 'center', 'fontSize': '8px', 'color': '#e83e8c', 'marginBottom': '2px'}),
                    dcc.Slider(id={'type': 'slider-repo', 'index': i}, min=-300, max=300, step=25, value=0,
                               vertical=True, verticalHeight=100, marks={},
                               tooltip={'placement': 'right', 'always_visible': True}),
                ], style={'display': 'inline-block', 'width': f'{100/len([m for m in range(n) if periodos[m].month in MESES_DECISION]):.0f}%',
                          'textAlign': 'center', 'verticalAlign': 'top'})
            )
        else:
            # Hidden slider (necesario para mantener indices consistentes con el store)
            sliders_items.append(
                html.Div([
                    dcc.Slider(id={'type': 'slider-repo', 'index': i}, min=-300, max=300, step=25, value=0,
                               vertical=True, verticalHeight=1),
                ], style={'display': 'none'})
            )

    sliders = html.Div(sliders_items, style={'marginTop': '5px'})

    # Grafico
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=list(tasas), mode='lines+markers',
                             name='Senda Repo', marker=dict(size=8, color='#e83e8c')))
    fig.update_layout(title='Senda Tasa Repo (%)', yaxis_title='Tasa (%)',
                      height=250, margin=dict(l=50, r=10, t=35, b=25))

    return store, sliders, fig


# Reset senda repo a la lineal original
@callback(
    [Output('store-senda-repo', 'data', allow_duplicate=True),
     Output('sliders-repo', 'children', allow_duplicate=True),
     Output('grafico-senda-repo', 'figure', allow_duplicate=True)],
    Input('btn-reset-senda-repo', 'n_clicks'),
    [State('esc-repo-ini', 'value'),
     State('esc-repo-fin', 'value'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def reset_senda_repo(n_clicks, repo_ini_val, repo_fin_val, fecha_inicio, fecha_fin):
    """Restaura la senda repo a la lineal original."""
    from dateutil.relativedelta import relativedelta

    fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else pd.Timestamp.today().date()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + relativedelta(months=12)
    delta = relativedelta(fecha_end, fecha_ini)
    n = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
    if n < 1:
        n = 1

    r_ini = float(repo_ini_val or 9.75)
    r_fin = float(repo_fin_val or 7.75)
    periodos = pd.date_range(start=fecha_ini.replace(day=1), periods=n, freq='MS')
    tasas = np.linspace(r_ini, r_fin, n)
    labels = [d.strftime('%Y-%m') for d in periodos]

    store = [{'fecha': labels[i], 'tasa': round(tasas[i], 2)} for i in range(n)]

    MESES_DECISION = {1, 3, 4, 6, 7, 9, 10, 12}
    sliders_items = []
    for i in range(n):
        mes_num = periodos[i].month
        if mes_num in MESES_DECISION:
            sliders_items.append(
                html.Div([
                    html.Div(labels[i], style={'textAlign': 'center', 'fontSize': '9px', 'fontWeight': 'bold', 'marginBottom': '2px'}),
                    html.Div(f"{tasas[i]:.1f}%", style={'textAlign': 'center', 'fontSize': '8px', 'color': '#e83e8c', 'marginBottom': '2px'}),
                    dcc.Slider(id={'type': 'slider-repo', 'index': i}, min=-300, max=300, step=25, value=0,
                               vertical=True, verticalHeight=100, marks={},
                               tooltip={'placement': 'right', 'always_visible': True}),
                ], style={'display': 'inline-block', 'width': f'{100/len([m for m in range(n) if periodos[m].month in MESES_DECISION]):.0f}%',
                          'textAlign': 'center', 'verticalAlign': 'top'})
            )
        else:
            sliders_items.append(
                html.Div([
                    dcc.Slider(id={'type': 'slider-repo', 'index': i}, min=-300, max=300, step=25, value=0, vertical=True, verticalHeight=1),
                ], style={'display': 'none'})
            )
    sliders = html.Div(sliders_items, style={'marginTop': '5px'})

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=list(tasas), mode='lines+markers',
                             name='Senda Repo', marker=dict(size=8, color='#e83e8c')))
    fig.update_layout(title='Senda Tasa Repo (%)', yaxis_title='Tasa (%)',
                      height=250, margin=dict(l=50, r=10, t=35, b=25))

    return store, sliders, fig


@callback(
    [Output('store-senda-repo', 'data', allow_duplicate=True),
     Output('grafico-senda-repo', 'figure', allow_duplicate=True)],
    Input({'type': 'slider-repo', 'index': ALL}, 'value'),
    State('store-senda-repo', 'data'),
    prevent_initial_call=True
)
def update_senda_repo_sliders(slider_values, store_data):
    """Actualiza la senda repo interpolando entre nodos de decision.
    Cada slider define la tasa del nodo. Entre nodos se interpola linealmente.
    Decision del mes X rige desde X+1. El ultimo punto es siempre la tasa final."""
    if not store_data or not slider_values:
        return no_update, no_update

    MESES_DECISION = {1, 3, 4, 6, 7, 9, 10, 12}

    labels = [d['fecha'] for d in store_data]
    tasas_base = [d['tasa'] for d in store_data]
    n = len(tasas_base)

    # Construir nodos: indice donde RIGE la decision (mes siguiente al de decision)
    # + la tasa ajustada en ese nodo
    nodos = []  # lista de (indice_vigencia, tasa_en_nodo)

    # El primer punto siempre es la tasa base del primer mes
    nodos.append((0, tasas_base[0]))

    for i in range(n):
        mes_num = int(labels[i].split('-')[1])
        if mes_num in MESES_DECISION and slider_values[i] != 0:
            idx_vigencia = min(i + 1, n - 1)
            tasa_nodo = tasas_base[idx_vigencia] + slider_values[i] / 100.0
            nodos.append((idx_vigencia, tasa_nodo))

    # El ultimo punto siempre es la tasa base final (repo final no cambia)
    nodos.append((n - 1, tasas_base[-1]))

    # Eliminar duplicados de indice (mantener el ultimo)
    nodos_dict = {}
    for idx, tasa in nodos:
        nodos_dict[idx] = tasa
    nodos_sorted = sorted(nodos_dict.items())

    # Interpolar linealmente entre nodos
    tasas_ajustadas = [0.0] * n
    for seg in range(len(nodos_sorted) - 1):
        idx_ini, tasa_ini = nodos_sorted[seg]
        idx_fin, tasa_fin = nodos_sorted[seg + 1]
        for i in range(idx_ini, idx_fin + 1):
            if idx_fin == idx_ini:
                tasas_ajustadas[i] = tasa_ini
            else:
                frac = (i - idx_ini) / (idx_fin - idx_ini)
                tasas_ajustadas[i] = tasa_ini + (tasa_fin - tasa_ini) * frac

    new_store = [{'fecha': labels[i], 'tasa': round(tasas_ajustadas[i], 2)} for i in range(n)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=tasas_base, mode='lines',
                             name='Base', line=dict(dash='dot', color='gray')))
    fig.add_trace(go.Scatter(x=labels, y=tasas_ajustadas, mode='lines+markers',
                             name='Ajustada', marker=dict(size=8, color='#e83e8c')))
    fig.update_layout(title='Senda Tasa Repo (%)', yaxis_title='Tasa (%)',
                      height=250, margin=dict(l=50, r=10, t=35, b=25))

    return new_store, fig


# --- Callback: Proyectar Simultaneas Pasivas ---
@callback(
    Output('resultado-sim', 'children'),
    Input('btn-proyectar-sim', 'n_clicks'),
    [State('esc-repo-ini', 'value'),
     State('esc-repo-fin', 'value'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date'),
     State('store-senda-repo', 'data')],
    prevent_initial_call=True
)
def proyectar_simultaneas(n_clicks, repo_actual, repo_final, fecha_inicio, fecha_fin, store_senda):
    """Proyecta costo de simultaneas pasivas con senda repo (de sliders o lineal)."""
    import traceback
    try:
        from proyeccion_simultaneas_pasivas import proyectar_simultaneas_pasivas
        from dateutil.relativedelta import relativedelta

        fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else pd.Timestamp.today().date()
        fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + relativedelta(months=12)
        fecha_ini_str = fecha_ini.strftime('%Y-%m')
        fecha_fin_str = fecha_end.strftime('%Y-%m')

        # Calcular meses
        delta = relativedelta(fecha_end, fecha_ini)
        n_meses = delta.years * 12 + delta.months + (1 if delta.days > 0 else 0)
        if n_meses < 1:
            n_meses = 1

        # Usar senda de sliders si disponible, sino generar lineal
        if store_senda:
            periodos_str = [d['fecha'] + '-01' for d in store_senda]
            tasas = [d['tasa'] / 100.0 for d in store_senda]
            df_senda = pd.DataFrame({'fecha': periodos_str, 'tasa_repo': tasas})
        else:
            repo_ini = float(repo_actual or 9.75) / 100.0
            repo_end_val = float(repo_final or 7.75) / 100.0
            periodos = pd.date_range(start=f"{fecha_ini_str}-01", periods=n_meses, freq='MS')
            tasas = np.linspace(repo_ini, repo_end_val, n_meses)
            df_senda = pd.DataFrame({'fecha': [d.strftime('%Y-%m-%d') for d in periodos], 'tasa_repo': tasas})

        # Guardar senda en portafolio.db
        conn = get_conn()
        df_senda.to_sql('senda_tasa_repo', conn, if_exists='replace', index=False)
        conn.close()

        # Ejecutar proyeccion
        df_resultado = proyectar_simultaneas_pasivas(
            fecha_inicio=fecha_ini_str,
            fecha_fin=fecha_fin_str,
            ruta_db_portafolio=str(DB_PATH),
            conn_inputs=get_conn(),
            fecha_inicio_exacta=fecha_ini.strftime('%Y-%m-%d'),
        )

        if df_resultado.empty:
            return html.Span("No hay titulos en Simultaneas Pasivas.", style={'color': 'orange'})

        # Resumen mensual
        resumen = df_resultado.groupby('fecha_proyeccion').agg(
            costo_mensual=('costo_mensual', 'sum'),
            n_titulos=('id_posicion', 'nunique'),
            vm_total=('valor_mercado', 'sum'),
        ).reset_index()
        resumen['costo_acum'] = resumen['costo_mensual'].cumsum()

        # Grafico
        fig = go.Figure()
        fig.add_trace(go.Bar(x=resumen['fecha_proyeccion'], y=resumen['costo_mensual'] / 1e6,
                             name='Costo Mensual (M)', marker_color='#e83e8c'))
        fig.add_trace(go.Scatter(x=resumen['fecha_proyeccion'], y=resumen['costo_acum'] / 1e6,
                                 name='Acumulado (M)', yaxis='y2', line=dict(color='#333', width=2)))
        fig.update_layout(
            title='Costo Repo Simultaneas Pasivas',
            yaxis=dict(title='Costo Mensual (Millones COP)'),
            yaxis2=dict(title='Acumulado (Millones COP)', overlaying='y', side='right'),
            height=320, margin=dict(l=50, r=50, t=40, b=30),
            legend=dict(x=0, y=1.1, orientation='h'),
        )

        costo_total = resumen['costo_mensual'].sum()
        vm_base = resumen['vm_total'].iloc[0] if not resumen.empty else 0

        return html.Div([
            html.Div([
                html.Span(f"VM Base: ${vm_base/1e6:,.0f} M", style={'marginRight': '20px'}),
                html.Span(f"Costo Total: ${costo_total/1e6:,.0f} M ({n_meses} meses)", style={'fontWeight': 'bold'}),
                html.Span(f" | Repo: {store_senda[0]['tasa'] if store_senda else repo_actual}% -> {store_senda[-1]['tasa'] if store_senda else repo_final}%",
                          style={'color': '#666'}),
            ], style={'padding': '8px', 'backgroundColor': '#fce4ec', 'borderRadius': '4px', 'marginBottom': '10px'}),
            dcc.Graph(figure=fig),
        ])

    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"Error: {e}", style={'color': 'red'}),
            html.Pre(tb, style={'fontSize': '10px', 'maxHeight': '200px', 'overflow': 'auto'}),
        ])


@callback(
    Output('params-metodo3', 'style'),
    Input('proy-metodo', 'value')
)
def toggle_params_metodo3(metodo):
    if metodo == 3:
        return {'marginBottom': '10px', 'display': 'block'}
    return {'marginBottom': '10px', 'display': 'none'}


@callback(
    [Output('msg-proy', 'children'),
     Output('grafico-pyg', 'figure'),
     Output('store-resultados-pyg', 'data')],
    Input('btn-correr-proy', 'n_clicks'),
    [State('proy-fecha-corte', 'value'),
     State('proy-fecha-fin', 'value'),
     State('proy-metodo', 'value'),
     State('store-sendas', 'data'),
     State({'type': 'slider-trm', 'index': ALL}, 'value'),
     State('proy-exp-obj', 'value'),
     State('proy-trato', 'value'),
     State('proy-momento', 'value'),
     State('proy-opc-largo', 'value')],
    prevent_initial_call=True
)
def correr_proyeccion(n_clicks, fecha_corte, fecha_fin, metodo,
                       store_sendas, slider_trm_values,
                       exp_obj, trato, momento, opc_largo):
    if not DERIVADOS_DISPONIBLE:
        return html.Span("⚠️ Módulo de derivados no disponible (valoracion_derivados.py no encontrado).", style={'color': 'orange'}), go.Figure(), None
    if not all([fecha_corte, fecha_fin, metodo]):
        return html.Span("Completa todos los campos.", style={'color': 'red'}), go.Figure(), None

    # Extraer TRM actual de hist_opciones; si falla, TRM vigente de la fuente para la fecha
    conn = get_conn()
    try:
        trm_df = pd.read_sql_query("SELECT trm FROM hist_opciones LIMIT 1", conn)
        trm_actual_f = float(str(trm_df['trm'].iloc[0]).replace(',', ''))
    except Exception:
        trm_actual_f = obtener_trm_para(fecha_corte)
    conn.close()
    if not trm_actual_f:
        return html.Span(
            f"⚠️ No hay TRM disponible para {fecha_corte}. Carga los datos de esa fecha.",
            style={'color': '#e65100'}), go.Figure(), None

    # Calcular activos_usd y portafolio_pesos desde la base
    conn = get_conn()
    try:
        df_port = pd.read_sql_query("SELECT moneda, valor_mercado, clase_origen FROM portafolio_clasificado", conn)
    except Exception:
        df_port = pd.DataFrame()
    conn.close()

    activos_usd_f = 0.0
    port_pesos_f = 0.0
    if not df_port.empty:
        df_port['valor_mercado'] = pd.to_numeric(df_port['valor_mercado'], errors='coerce').fillna(0)
        df_titulos = df_port[df_port['clase_origen'] == 'TITULO'] if 'clase_origen' in df_port.columns else df_port
        mask_usd = ~df_titulos['moneda'].isin(['COP', 'UVR'])
        activos_usd_f = float(df_titulos.loc[mask_usd, 'valor_mercado'].sum()) / trm_actual_f
        mask_pesos = df_titulos['moneda'].isin(['COP', 'UVR'])
        port_pesos_f = float(df_titulos.loc[mask_pesos, 'valor_mercado'].sum())

    # Construir senda TRM desde sliders
    senda = None
    if store_sendas and slider_trm_values:
        df_sendas = pd.DataFrame(store_sendas)
        df_sendas['TRM'] = pd.to_numeric(df_sendas['TRM'], errors='coerce')
        n = len(df_sendas)
        if len(slider_trm_values) == n:
            senda = [float(df_sendas['TRM'].iloc[i]) + float(slider_trm_values[i]) for i in range(n)]

    # Si no hay senda de sliders, usar senda plana
    if senda is None:
        senda = None  # valorar_portafolio_derivados generará senda plana

    try:
        # Construir metodo_params para método 3
        metodo_params = None
        if int(metodo) == 3:
            metodo_params = {
                'exposicion_obj': float(exp_obj or 30) / 100,
                'trato_exposicion': int(trato or 1),
                'momento_exposicion': int(momento or 1),
                'opciones_sobre_largo': float(opc_largo or 70) / 100,
            }

        resultados = valorar_portafolio_derivados(
            fecha_corte=fecha_corte,
            fecha_fin=fecha_fin,
            metodo=int(metodo),
            nivel_actual=trm_actual_f,
            activos_usd=activos_usd_f,
            portafolio_pesos=port_pesos_f,
            senda_trm=senda,
            metodo_params=metodo_params,
        )

        pyg = calcular_pyg(resultados)

        # Gráfico de P&G por tipo
        fig = go.Figure()
        colores = {'Opciones': '#e74c3c', 'Forwards': '#2ecc71', 'Activos': '#3498db'}
        for tipo in pyg['tipo'].unique():
            df_tipo = pyg[pyg['tipo'] == tipo].sort_values('fecha_valoracion')
            fig.add_trace(go.Scatter(
                x=df_tipo['fecha_valoracion'],
                y=df_tipo['p_y_g'],
                mode='lines+markers',
                name=tipo,
                line=dict(color=colores.get(tipo, None)),
                marker=dict(size=7),
            ))
        fig.update_layout(
            title='P&G Proyectado por Tipo de Instrumento',
            xaxis_title='Fecha',
            yaxis_title='P&G (COP)',
            yaxis_tickformat=',.0f',
            height=450,
            margin=dict(l=60, r=20, t=50, b=40),
            legend=dict(orientation='h', y=-0.15),
        )

        n_opc = len(resultados['opciones'])
        n_fwd = len(resultados['forwards'])
        msg = html.Span(f"✅ Proyección completada — {n_opc} opciones, {n_fwd} forwards valorados", style={'color': 'green'})

        # Guardar Excel en store para descarga
        import io, base64

        # Agregar columna trm_valoracion a cada DataFrame
        from valoracion_derivados import _construir_serie_trm
        from datetime import datetime as dt_cls
        fecha_corte_dt = dt_cls.strptime(fecha_corte, '%Y-%m-%d')
        serie_local = _construir_serie_trm(senda if senda else [trm_actual_f] * 13, fecha_corte_dt)
        for df_name in ['forwards', 'opciones', 'activos']:
            df_r = resultados[df_name]
            df_r['trm_valoracion'] = df_r['fecha_valoracion'].apply(
                lambda fv: trm_actual_f if fv == pd.Timestamp(fecha_corte) else float(serie_local.asof(pd.Timestamp(fv)))
            )

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            resultados['forwards'].to_excel(writer, sheet_name='forwards', index=False)
            resultados['opciones'].to_excel(writer, sheet_name='opciones', index=False)
            resultados['activos'].to_excel(writer, sheet_name='activos', index=False)
            pyg.to_excel(writer, sheet_name='pyg_consolidado', index=False)
        encoded = base64.b64encode(buffer.getvalue()).decode()

        return msg, fig, encoded

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[AXIS ERROR]\n{tb}")
        lines = tb.strip().split('\n')
        error_detail = '\n'.join(lines[-4:]) if len(lines) >= 4 else tb
        return html.Span(f"❌ {error_detail}", style={'color': 'red', 'whiteSpace': 'pre-wrap', 'fontSize': '11px'}), go.Figure(), None


@callback(
    Output('download-proy-excel', 'data'),
    Input('btn-descargar-proy', 'n_clicks'),
    State('store-resultados-pyg', 'data'),
    prevent_initial_call=True
)
def descargar_excel_proy(n_clicks, encoded_data):
    if not encoded_data:
        return no_update
    import base64
    decoded = base64.b64decode(encoded_data)
    return dcc.send_bytes(decoded, filename='proyeccion_derivados.xlsx')


# =============================================================================
# CALLBACK: SENSIBILIZACIÓN REACTIVA (VM Actual vs VM Ajustado)
# =============================================================================

@callback(
    Output('resultado-sensibilizacion', 'children'),
    Input('btn-sensibilizar', 'n_clicks'),
    [State('store-tes-cop', 'data'),
     State('store-tes-uvr', 'data'),
     State({'type': 'slider-cop', 'index': ALL}, 'value'),
     State({'type': 'slider-uvr', 'index': ALL}, 'value'),
     State('slider-fs-shift', 'value'),
     State('slider-fs-twist', 'value'),
     State('slider-ipc-shift', 'value'),
     State('slider-ipc-twist', 'value'),
     State('store-sendas', 'data'),
     State({'type': 'slider-trm', 'index': ALL}, 'value'),
     State({'type': 'slider-inf', 'index': ALL}, 'value'),
     State('input-trm-hoy', 'value'),
     State('input-inf-hoy', 'value'),
     State('input-uvr-sens', 'value'),
     State('sidebar-fecha-inicio', 'date')],
    prevent_initial_call=True
)
def calcular_sensibilizacion(n_clicks, store_cop, store_uvr,
                              sliders_cop, sliders_uvr,
                              fs_shift, fs_twist, ipc_shift, ipc_twist,
                              store_sendas, sliders_trm, sliders_inf,
                              trm_hoy, inf_hoy, uvr_valor, fecha_inicio):
    import sys
    try:
        from motor_proyeccion import cargar_portafolio_proyeccion, valorar_titulo
        from curvas_rendimiento import (extraer_curva_base_pip_db, obtener_nombre_curva,
                                         interpolar_tasa)

        if not fecha_inicio:
            return html.Span("⚠️ Selecciona una fecha de corte en el sidebar.", style={'color': '#e65100'})

        fecha_val = pd.Timestamp(fecha_inicio).date()
        fecha_pip = fecha_val.strftime('%Y-%m-%d')
        port = cargar_portafolio_proyeccion(str(SCRIPT_DIR / "portafolio.db"), fecha_pip)
        curvas_base = extraer_curva_base_pip_db(str(DB_PATH), fecha_pip)
        if not curvas_base:
            return html.Span(
                f"⚠️ No hay curvas PIP en hist_pip para {fecha_pip}. Carga el PIP de esa fecha por la pestaña Inputs.",
                style={'color': '#e65100'}
            )
        curvas_shock = {k: dict(v) for k, v in curvas_base.items()}

        # Resolver UVR: valor del usuario, o vigente para la fecha del sidebar. Sin hardcode.
        uvr_resuelta = uvr_valor if uvr_valor else obtener_uvr_para(fecha_pip)
        if not uvr_resuelta:
            return html.Span(
                f"⚠️ No hay UVR disponible para {fecha_pip}. Ingresa la UVR o carga los datos de esa fecha.",
                style={'color': '#e65100'}
            )
        uvr_resuelta = float(uvr_resuelta)

        # ── TES COP: sliders mueven sobre PIP; si hay Excel, reemplaza con target ──
        if store_cop and "DP_TF" in curvas_shock:
            df_cop_x = pd.DataFrame(store_cop)
            df_cop_x['tasa'] = pd.to_numeric(df_cop_x['tasa'], errors='coerce')
            if df_cop_x['tasa'].max() < 1.0:
                df_cop_x['tasa'] = df_cop_x['tasa'] * 100
            df_cop_x, _ = _get_sorted_labels_and_order(df_cop_x, 'COP')
            nodos_pip = sorted(curvas_base["DP_TF"].keys())
            for i in range(min(len(df_cop_x), len(nodos_pip))):
                delta = (sliders_cop[i] if sliders_cop and i < len(sliders_cop) else 0) or 0
                curvas_shock["DP_TF"][nodos_pip[i]] = float(df_cop_x['tasa'].iloc[i]) + delta / 100.0
        elif sliders_cop and "DP_TF" in curvas_shock:
            for i, nodo in enumerate(sorted(curvas_shock["DP_TF"].keys())[:len(sliders_cop)]):
                curvas_shock["DP_TF"][nodo] += (sliders_cop[i] or 0) / 100.0

        # ── TES UVR ──
        if store_uvr and "DP_UVR" in curvas_shock:
            df_uvr_x = pd.DataFrame(store_uvr)
            df_uvr_x['tasa'] = pd.to_numeric(df_uvr_x['tasa'], errors='coerce')
            if df_uvr_x['tasa'].max() < 1.0:
                df_uvr_x['tasa'] = df_uvr_x['tasa'] * 100
            df_uvr_x, _ = _get_sorted_labels_and_order(df_uvr_x, 'UVR')
            nodos_pip_u = sorted(curvas_base["DP_UVR"].keys())
            for i in range(min(len(df_uvr_x), len(nodos_pip_u))):
                delta = (sliders_uvr[i] if sliders_uvr and i < len(sliders_uvr) else 0) or 0
                curvas_shock["DP_UVR"][nodos_pip_u[i]] = float(df_uvr_x['tasa'].iloc[i]) + delta / 100.0
        elif sliders_uvr and "DP_UVR" in curvas_shock:
            for i, nodo in enumerate(sorted(curvas_shock["DP_UVR"].keys())[:len(sliders_uvr)]):
                curvas_shock["DP_UVR"][nodo] += (sliders_uvr[i] or 0) / 100.0

        # ── Corporativos: shift + twist ──
        if "CORP_TF" in curvas_shock:
            nodos_c = sorted(curvas_shock["CORP_TF"].keys())
            pos = np.linspace(-1, 1, len(nodos_c)) if len(nodos_c) > 1 else [0]
            for i, nodo in enumerate(nodos_c):
                curvas_shock["CORP_TF"][nodo] += (fs_shift or 0)/100 + pos[i]*(fs_twist or 0)/100
        if "CORP_IPC" in curvas_shock:
            nodos_ci = sorted(curvas_shock["CORP_IPC"].keys())
            pos_i = np.linspace(-1, 1, len(nodos_ci)) if len(nodos_ci) > 1 else [0]
            for i, nodo in enumerate(nodos_ci):
                curvas_shock["CORP_IPC"][nodo] += (ipc_shift or 0)/100 + pos_i[i]*(ipc_twist or 0)/100

        # ── TRM: base vs shock ── (valor mostrado o TRM vigente de la fuente, sin hardcode)
        _trm_base = trm_hoy if trm_hoy else obtener_trm_para(fecha_pip)
        if not _trm_base:
            return html.Span(
                f"⚠️ No hay TRM disponible para {fecha_pip}. Carga los datos de esa fecha.",
                style={'color': '#e65100'})
        trm_base = float(_trm_base)
        trm_shock = trm_base
        if store_sendas:
            df_s = pd.DataFrame(store_sendas)
            df_s['TRM'] = pd.to_numeric(df_s['TRM'], errors='coerce')
            if not df_s.empty:
                idx_last = len(df_s) - 1
                trm_shock = float(df_s['TRM'].iloc[idx_last])
                if sliders_trm and idx_last < len(sliders_trm):
                    trm_shock += float(sliders_trm[idx_last] or 0)

        # ── IPC: base vs shock ── (valor mostrado o IPC vigente de la fuente, sin hardcode)
        _ipc_base = inf_hoy if inf_hoy is not None else obtener_ipc_para(fecha_pip)
        if _ipc_base is None:
            return html.Span(
                f"⚠️ No hay IPC disponible para {fecha_pip}. Carga los datos de esa fecha.",
                style={'color': '#e65100'})
        ipc_base_pct = float(_ipc_base) / 100.0
        ipc_shock_pct = ipc_base_pct
        if store_sendas:
            df_s = pd.DataFrame(store_sendas)
            df_s['inflación'] = pd.to_numeric(df_s['inflación'], errors='coerce')
            if not df_s.empty:
                idx_last = len(df_s) - 1
                ipc_shock_pct = float(df_s['inflación'].iloc[idx_last])
                if sliders_inf and idx_last < len(sliders_inf):
                    ipc_shock_pct += float(sliders_inf[idx_last] or 0) / 10000.0

        # ── Serie IPC ──
        try:
            from valorar_bonos import cargar_serie_ipc
            serie_ipc = cargar_serie_ipc(str(SCRIPT_DIR / "Serie Inflación.xlsx"))
        except Exception:
            serie_ipc = pd.DataFrame()

        # ── Valorar portafolio ──
        # VM Base = valorar con tasa PIP de la especie (reproducible)
        # VM Shock = valorar con tasa PIP + delta del slider
        # Mapear especie del portafolio → tasa PIP por NEMO

        from curvas_rendimiento import extraer_curva_con_nombres_db
        curvas_nom = extraer_curva_con_nombres_db(str(DB_PATH), fecha_pip)

        # Construir mapeo NEMO → tasa PIP
        tasas_pip = {}  # {nemo: tasa_pip}
        for curva_name, df_c in curvas_nom.items():
            for _, row in df_c.iterrows():
                tasas_pip[str(row['nodo']).strip()] = float(row['tasa'])

        # Construir deltas por slider
        deltas_por_nemo = {}  # {nemo: delta_pct}

        # Sliders COP
        if "DP_TF" in curvas_nom and sliders_cop:
            df_pip_tf = curvas_nom["DP_TF"]
            for i in range(min(len(df_pip_tf), len(sliders_cop))):
                nemo = str(df_pip_tf['nodo'].iloc[i]).strip()
                delta = (sliders_cop[i] or 0) / 100.0  # bps → %
                if abs(delta) > 0.0001:
                    deltas_por_nemo[nemo] = delta

        # Sliders UVR
        if "DP_UVR" in curvas_nom and sliders_uvr:
            df_pip_uvr = curvas_nom["DP_UVR"]
            for i in range(min(len(df_pip_uvr), len(sliders_uvr))):
                nemo = str(df_pip_uvr['nodo'].iloc[i]).strip()
                delta = (sliders_uvr[i] or 0) / 100.0
                if abs(delta) > 0.0001:
                    deltas_por_nemo[nemo] = delta

        # Corporativos: shift paralelo
        corp_delta_tf = (fs_shift or 0) / 100.0
        corp_delta_ipc = (ipc_shift or 0) / 100.0

        resultados = []
        for t in port:
            dias_r = (t.fecha_vencimiento - fecha_val).days
            if dias_r <= 0:
                continue

            especie = t.especie.strip()

            # ¿Tiene tasa PIP? (es un TES que está en el archivo PIP)
            if especie in tasas_pip:
                tasa_pip = tasas_pip[especie]
                delta = deltas_por_nemo.get(especie, 0.0)

                # VM Base = valorar con tasa PIP
                vm_b = valorar_titulo(t, tasa_pip, fecha_val, valor_uvr=uvr_resuelta, trm=trm_base, serie_ipc=serie_ipc)
                # VM Shock = valorar con tasa PIP + delta
                if abs(delta) > 0.0001:
                    vm_s = valorar_titulo(t, tasa_pip + delta, fecha_val, valor_uvr=uvr_resuelta, trm=trm_shock, serie_ipc=serie_ipc)
                else:
                    vm_s = vm_b

                resultados.append({'Asset_Class': t.asset_class, 'Especie': especie,
                                   'Compania': t.compania, 'VM_Actual': t.valor_mercado,
                                   'VM_Base': vm_b, 'VM_Shock': vm_s, 'PnL': vm_s - vm_b})

            else:
                # Corporativo u otro sin PIP directo
                curva_n = obtener_nombre_curva(t.asset_class)
                if curva_n == "CORP_TF" and abs(corp_delta_tf) > 0.0001:
                    vm_b = t.valor_mercado
                    vm_s = valorar_titulo(t, t.tasa_mercado + corp_delta_tf, fecha_val,
                                          valor_uvr=uvr_resuelta, trm=trm_shock, serie_ipc=serie_ipc)
                    resultados.append({'Asset_Class': t.asset_class, 'Especie': especie,
                                       'Compania': t.compania, 'VM_Actual': t.valor_mercado,
                                       'VM_Base': vm_b, 'VM_Shock': vm_s, 'PnL': vm_s - vm_b})
                elif curva_n == "CORP_IPC" and abs(corp_delta_ipc) > 0.0001:
                    vm_b = t.valor_mercado
                    vm_s = valorar_titulo(t, t.tasa_mercado + corp_delta_ipc, fecha_val,
                                          valor_uvr=uvr_resuelta, trm=trm_shock, serie_ipc=serie_ipc)
                    resultados.append({'Asset_Class': t.asset_class, 'Especie': especie,
                                       'Compania': t.compania, 'VM_Actual': t.valor_mercado,
                                       'VM_Base': vm_b, 'VM_Shock': vm_s, 'PnL': vm_s - vm_b})
                else:
                    # Sin shock → P&L = 0
                    resultados.append({'Asset_Class': t.asset_class, 'Especie': especie,
                                       'Compania': t.compania, 'VM_Actual': t.valor_mercado,
                                       'VM_Base': t.valor_mercado, 'VM_Shock': t.valor_mercado, 'PnL': 0.0})

        df_res = pd.DataFrame(resultados)
        if df_res.empty:
            return html.Span("Sin datos.", style={'color': 'orange'})

        resumen = df_res.groupby('Asset_Class').agg(
            Posiciones=('Especie','count'), VM_Actual=('VM_Actual','sum'),
            VM_Base=('VM_Base','sum'), VM_Shock=('VM_Shock','sum'), PnL=('PnL','sum')).reset_index()
        resumen['PnL_%'] = resumen['PnL'] / resumen['VM_Base'] * 100
        t_base = resumen['VM_Base'].sum()
        t_shock = resumen['VM_Shock'].sum()
        t_pnl = resumen['PnL'].sum()

        rs = resumen.copy()
        rs['VM_Actual'] = rs['VM_Actual'].apply(lambda x: f"{_fmt_cop(x)}")
        rs['VM_Base'] = rs['VM_Base'].apply(lambda x: f"{_fmt_cop(x)}")
        rs['VM_Shock'] = rs['VM_Shock'].apply(lambda x: f"{_fmt_cop(x)}")
        rs['PnL'] = rs['PnL'].apply(lambda x: f"{_fmt_cop(x)}")
        rs['PnL_%'] = rs['PnL_%'].apply(lambda x: f"{x:+.2f}%")

        fig = go.Figure()
        fig.add_trace(go.Bar(name='VM Base (PIP)', x=resumen['Asset_Class'], y=resumen['VM_Base']/1e9, marker_color='#3498db'))
        fig.add_trace(go.Bar(name='VM Escenario', x=resumen['Asset_Class'], y=resumen['VM_Shock']/1e9, marker_color='#e74c3c'))
        fig.update_layout(barmode='group', title='VM por Asset Class (MM)', yaxis_title='MM COP', height=400, margin=dict(l=60,r=20,t=50,b=80))

        info = f"TRM: {trm_base:,.0f} → {trm_shock:,.0f} | IPC: {ipc_base_pct*100:.2f}% → {ipc_shock_pct*100:.2f}%"
        return html.Div([
            html.Div(info, style={'fontSize':'11px','color':'#555','marginBottom':'10px','padding':'5px','backgroundColor':'#e9ecef','borderRadius':'3px'}),
            html.Div([
                html.Div([html.Div("VM Base (PIP)",style={'fontSize':'10px','color':'#666'}),
                          html.Div(f"${t_base/1e12:.3f} B",style={'fontSize':'20px','fontWeight':'bold'})],
                         style={'display':'inline-block','width':'30%','textAlign':'center'}),
                html.Div([html.Div("VM Escenario",style={'fontSize':'10px','color':'#666'}),
                          html.Div(f"${t_shock/1e12:.3f} B",style={'fontSize':'20px','fontWeight':'bold'})],
                         style={'display':'inline-block','width':'30%','textAlign':'center'}),
                html.Div([html.Div("P&L Total",style={'fontSize':'10px','color':'#666'}),
                          html.Div(f"{_fmt_cop(t_pnl)}",style={'fontSize':'20px','fontWeight':'bold',
                                   'color':'#28a745' if t_pnl>=0 else '#dc3545'})],
                         style={'display':'inline-block','width':'30%','textAlign':'center'}),
            ],style={'marginBottom':'15px','padding':'10px','backgroundColor':'#f8f9fa','borderRadius':'5px'}),
            dash_table.DataTable(columns=[{'name':c,'id':c} for c in rs.columns], data=rs.to_dict('records'),
                                style_table={'overflowX':'auto'}, style_cell={'textAlign':'center','fontSize':'11px','padding':'4px'},
                                style_header={'fontWeight':'bold','backgroundColor':'#f0f0f0'}, sort_action='native'),
            dcc.Graph(figure=fig),
        ])
    except Exception as e:
        import traceback
        return html.Span(f"❌ {traceback.format_exc()[-500:]}", style={'color':'red','whiteSpace':'pre-wrap','fontSize':'10px'})


# =============================================================================
# CALLBACKS: RENTA FIJA — Flujos por Especie y Proyección
# =============================================================================

@callback(
    Output('tabla-flujos-especie', 'children'),
    Input('btn-flujos-especie', 'n_clicks'),
    [State('rf-horizonte', 'value'), State('rf-fecha-inicio', 'value')],
    prevent_initial_call=True
)
def mostrar_flujos_especie(n_clicks, horizonte, fecha_inicio):
    import sys
    try:
        from motor_proyeccion import cargar_portafolio_proyeccion, generar_tabla_flujos_futuros
        from datetime import date as dt_date

        if not fecha_inicio:
            return html.Span("⚠️ Selecciona una fecha de corte en el sidebar.", style={'color': '#e65100'})
        fecha = pd.Timestamp(fecha_inicio).date()
        fecha_pip = fecha.strftime('%Y-%m-%d')
        port = cargar_portafolio_proyeccion(str(SCRIPT_DIR / "portafolio.db"), fecha_pip)
        df = generar_tabla_flujos_futuros(port, fecha, int(horizonte or 12))

        if df.empty:
            return html.Span("No se detectaron cupones en el horizonte.", style={'color': 'orange'})

        # Formatear
        df_show = df.copy()
        df_show['Total_Cupones'] = df_show['Total_Cupones'].apply(lambda x: f"${x/1e6:,.1f}M")
        df_show['Nominal_Total'] = df_show['Nominal_Total'].apply(lambda x: f"${x/1e6:,.0f}M")

        return html.Div([
            html.Span(f"✅ {len(df)} especies con flujos en {horizonte} meses", style={'color': 'green', 'fontSize': '11px'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in ['Especie', 'Tipo_Bono', 'Asset_Class',
                          'Compania', 'Ramo', 'Clasificacion', 'N_Cupones', 'Total_Cupones', 'Nominal_Total']],
                data=df_show[['Especie', 'Tipo_Bono', 'Asset_Class', 'Compania', 'Ramo',
                              'Clasificacion', 'N_Cupones', 'Total_Cupones', 'Nominal_Total']].to_dict('records'),
                page_size=15,
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'left', 'fontSize': '10px', 'padding': '3px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
                sort_action='native',
                filter_action='native',
            ),
        ])
    except Exception as e:
        return html.Span(f"❌ Error: {e}", style={'color': 'red', 'fontSize': '11px'})


@callback(
    Output('resultado-rf', 'children'),
    Input('btn-proyectar-rf', 'n_clicks'),
    [State('rf-horizonte', 'value'),
     State('rf-fecha-inicio', 'value'),
     State('rf-uvr', 'value'),
     State('rf-clasif-global', 'value'),
     State('rf-reinv-misma', 'value'),
     State('store-tes-cop', 'data'),
     State('store-tes-uvr', 'data'),
     State('store-corporativos', 'data'),
     State({'type': 'slider-cop', 'index': ALL}, 'value'),
     State({'type': 'slider-uvr', 'index': ALL}, 'value'),
     State('slider-fs-shift', 'value'),
     State('slider-ipc-shift', 'value')],
    prevent_initial_call=True
)
def proyectar_renta_fija(n_clicks, horizonte, fecha_inicio, uvr,
                          clasif_global, reinv_misma,
                          store_cop, store_uvr, store_corp,
                          sliders_cop, sliders_uvr, fs_shift, ipc_shift):
    import sys
    try:
        from motor_proyeccion import (cargar_portafolio_proyeccion, proyectar_portafolio,
                                       calcular_posicion_por_ramo)
        from curvas_rendimiento import (extraer_curva_base_pip_db, generar_senda_lineal)

        if not fecha_inicio:
            return html.Span("⚠️ Selecciona una fecha de corte en el sidebar.", style={'color': '#e65100'})

        # Cargar portafolio
        fecha = pd.Timestamp(fecha_inicio).date()
        fecha_pip = fecha.strftime('%Y-%m-%d')
        port = cargar_portafolio_proyeccion(str(SCRIPT_DIR / "portafolio.db"), fecha_pip)
        h = int(horizonte or 12)
        # UVR: valor del usuario o UVR vigente de la fuente para la fecha (sin hardcode)
        valor_uvr = uvr if uvr else obtener_uvr_para(fecha_pip)
        if not valor_uvr:
            return html.Span(
                f"⚠️ No hay UVR disponible para {fecha_pip}. Ingresa la UVR o carga los datos de esa fecha.",
                style={'color': '#e65100'})
        valor_uvr = float(valor_uvr)

        # Construir sendas desde curvas PIP de hist_pip para la fecha del sidebar
        curvas_base = extraer_curva_base_pip_db(str(DB_PATH), fecha_pip)
        if not curvas_base:
            return html.Span(
                f"⚠️ No hay curvas PIP en hist_pip para {fecha_pip}. Carga el PIP de esa fecha por la pestaña Inputs.",
                style={'color': '#e65100'}
            )

        # Aplicar shocks de sliders TES COP
        if store_cop and sliders_cop and "DP_TF" in curvas_base:
            df_cop = pd.DataFrame(store_cop)
            df_cop['tasa'] = pd.to_numeric(df_cop['tasa'], errors='coerce')
            # Reconstruir curva con shocks aplicados
            nodos_tf = sorted(curvas_base["DP_TF"].keys())
            if len(sliders_cop) == len(df_cop):
                for i, nodo in enumerate(nodos_tf[:len(sliders_cop)]):
                    curvas_base["DP_TF"][nodo] += sliders_cop[i] / 100.0

        if store_uvr and sliders_uvr and "DP_UVR" in curvas_base:
            nodos_uvr = sorted(curvas_base["DP_UVR"].keys())
            if len(sliders_uvr) == len(nodos_uvr):
                for i, nodo in enumerate(nodos_uvr[:len(sliders_uvr)]):
                    curvas_base["DP_UVR"][nodo] += sliders_uvr[i] / 100.0

        # Shift corporativos
        if "CORP_TF" in curvas_base and fs_shift:
            for nodo in curvas_base["CORP_TF"]:
                curvas_base["CORP_TF"][nodo] += float(fs_shift) / 100.0
        if "CORP_IPC" in curvas_base and ipc_shift:
            for nodo in curvas_base["CORP_IPC"]:
                curvas_base["CORP_IPC"][nodo] += float(ipc_shift) / 100.0

        # Generar sendas: target = curvas ajustadas (planas, ya con shocks)
        sendas = {}
        for nombre, curva in curvas_base.items():
            # Senda plana: la curva ajustada se mantiene constante
            sendas[nombre] = generar_senda_lineal(curva, curva, h)

        # Ejecutar proyección
        resultados = proyectar_portafolio(
            portafolio=port,
            fecha_inicio=fecha,
            meses_horizonte=h,
            sendas=sendas,
            valor_uvr=valor_uvr,
        )

        if not resultados:
            return html.Span("Sin resultados.", style={'color': 'orange'})

        # Tabla de resultados
        df_res = pd.DataFrame([{
            'Mes': r.mes,
            'Fecha': r.fecha.strftime('%Y-%m'),
            'VM (B)': f"${r.vm_total/1e12:.3f}",
            'P&L Mes (MM)': f"${r.pnl_mercado_mes/1e9:,.1f}",
            'P&L Acum (MM)': f"${r.pnl_mercado_acum/1e9:,.1f}",
            'Títulos': r.n_titulos,
            'Reinversiones': r.n_reinversiones_mes,
            'Cupones (MM)': f"${r.cupones_cobrados_mes/1e9:,.2f}",
        } for r in resultados])

        # Gráfico P&L
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[r.fecha.strftime('%Y-%m') for r in resultados],
            y=[r.pnl_mercado_acum / 1e9 for r in resultados],
            mode='lines+markers', name='P&L Mercado',
            marker=dict(size=8, color='#28a745'),
        ))
        fig.add_trace(go.Scatter(
            x=[r.fecha.strftime('%Y-%m') for r in resultados],
            y=[r.pnl_tir_acum / 1e9 for r in resultados],
            mode='lines+markers', name='P&L TIR',
            line=dict(dash='dot'), marker=dict(size=6, color='#17a2b8'),
        ))
        fig.update_layout(title='P&L Acumulado Renta Fija (Miles de Millones)',
                          yaxis_title='MM COP', height=400,
                          margin=dict(l=60, r=20, t=50, b=40))

        # Encaje proyectado
        df_ramo = calcular_posicion_por_ramo(port, resultados[-1].fecha)

        children = [
            html.Span(f"✅ Proyección RF: {h} meses, {len(port)} títulos", style={'color': 'green'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in df_res.columns],
                data=df_res.to_dict('records'),
                page_size=12,
                style_table={'overflowX': 'auto', 'marginTop': '10px'},
                style_cell={'textAlign': 'center', 'fontSize': '10px', 'padding': '3px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ),
            dcc.Graph(figure=fig),
        ]

        if not df_ramo.empty:
            df_ramo['VM_Total'] = df_ramo['VM_Total'].apply(lambda x: f"{_fmt_cop(x)}")
            children.append(html.H6("Posición por Ramo (fin horizonte)"))
            children.append(dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in df_ramo.columns],
                data=df_ramo.to_dict('records'),
                page_size=10,
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'left', 'fontSize': '10px', 'padding': '3px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ))

        return html.Div(children)

    except Exception as e:
        import traceback
        return html.Span(f"❌ {traceback.format_exc()[-500:]}", style={'color': 'red', 'whiteSpace': 'pre-wrap', 'fontSize': '10px'})


# =============================================================================
# CALLBACKS: RENTA VARIABLE — Proyección por escenario
# =============================================================================

@callback(
    Output('resultado-rv', 'children'),
    Input('btn-proyectar-rv', 'n_clicks'),
    [State('rv-fecha-inicio', 'value'),
     State('rv-fecha-fin', 'value'),
     State('rv-local-base', 'value'),
     State('rv-local-optimista', 'value'),
     State('rv-local-pesimista', 'value'),
     State('rv-intl-base', 'value'),
     State('rv-intl-optimista', 'value'),
     State('rv-intl-pesimista', 'value'),
     State('store-sendas', 'data'),
     State({'type': 'slider-trm', 'index': ALL}, 'value')],
    prevent_initial_call=True
)
def proyectar_rv(n_clicks, fecha_ini, fecha_fin,
                  local_base, local_opt, local_pes,
                  intl_base, intl_opt, intl_pes,
                  store_sendas, sliders_trm):
    import sys
    try:
        from proyeccion_renta_variable import proyectar_bloque, generar_periodos

        escenarios_local = {
            "Base": float(local_base or 12) / 100,
            "Optimista": float(local_opt or 18) / 100,
            "Pesimista": float(local_pes or 5) / 100,
        }
        escenarios_intl = {
            "Base": float(intl_base or 8) / 100,
            "Optimista": float(intl_opt or 14) / 100,
            "Pesimista": float(intl_pes or 2) / 100,
        }

        # Cargar RV desde portafolio_clasificado
        conn = get_conn()
        try:
            df_rv = pd.read_sql_query("""
                SELECT id_posicion, especie as titulo, especie, compania, moneda, valor_mercado, asset_class_definitivo
                FROM portafolio_clasificado
                WHERE asset_class_definitivo IN ('COLCAP', 'MXWD', 'PEI')
            """, conn)
        except Exception:
            df_rv = pd.DataFrame()
        conn.close()

        if df_rv.empty:
            return html.Span("⚠️ No hay posiciones de RV en portafolio_clasificado (COLCAP, MXWD, PEI).", style={'color': 'orange'})

        df_rv['valor_mercado'] = pd.to_numeric(df_rv['valor_mercado'], errors='coerce').fillna(0)
        df_rv['tipo_rv'] = df_rv['asset_class_definitivo'].apply(
            lambda x: 'rv_local' if x in ('COLCAP', 'PEI') else 'rv_internacional')

        rv_local = df_rv[df_rv['tipo_rv'] == 'rv_local'].copy()
        rv_intl = df_rv[df_rv['tipo_rv'] == 'rv_internacional'].copy()

        periodos = generar_periodos(fecha_ini, fecha_fin)

        # Proyectar cada escenario
        resultados = []
        for esc, tasa in escenarios_local.items():
            df_p = proyectar_bloque(rv_local, periodos, tasa, esc)
            resultados.append(df_p)
        for esc, tasa in escenarios_intl.items():
            df_p = proyectar_bloque(rv_intl, periodos, tasa, esc)
            resultados.append(df_p)

        df_resultado = pd.concat(resultados, ignore_index=True)

        if df_resultado.empty:
            return html.Span("Sin datos de RV para proyectar.", style={'color': 'orange'})

        # Resumen por escenario y tipo
        resumen = df_resultado.groupby(['escenario', 'tipo_rv']).agg(
            VM_Final=('valor_mercado', 'last'),
            PYG_Total=('pyg_acumulado', 'max'),
            Posiciones=('titulo', 'nunique'),
        ).reset_index()
        resumen['VM_Final'] = resumen['VM_Final'].apply(lambda x: f"{_fmt_cop(x)}" if x > 1e6 else f"${x/1e3:,.0f}K")
        resumen['PYG_Total'] = resumen['PYG_Total'].apply(lambda x: f"{_fmt_cop(x)}" if abs(x) > 1e6 else f"${x/1e3:,.0f}K")

        # Gráfico P&G acumulado por escenario (consolidado local + intl)
        df_resultado['fecha_proyeccion'] = pd.to_datetime(df_resultado['fecha_proyeccion'])
        pyg_esc = df_resultado.groupby(['escenario', 'fecha_proyeccion'])['pyg_acumulado'].sum().reset_index()

        fig = go.Figure()
        colores_esc = {'Base': '#3498db', 'Optimista': '#27ae60', 'Pesimista': '#e74c3c'}
        for esc in pyg_esc['escenario'].unique():
            df_e = pyg_esc[pyg_esc['escenario'] == esc].sort_values('fecha_proyeccion')
            fig.add_trace(go.Scatter(
                x=df_e['fecha_proyeccion'], y=df_e['pyg_acumulado'],
                mode='lines+markers', name=esc,
                line=dict(color=colores_esc.get(esc, None)),
                marker=dict(size=6),
            ))
        fig.update_layout(title='P&G Acumulado Renta Variable por Escenario',
                          yaxis_title='P&G (COP)', yaxis_tickformat=',.0f',
                          height=400, margin=dict(l=60, r=20, t=50, b=40),
                          legend=dict(orientation='h', y=-0.15))

        # Detalle por título (escenario base)
        df_base = df_resultado[df_resultado['escenario'] == 'Base'].copy()
        detalle_final = df_base.groupby(['titulo', 'tipo_rv', 'compania', 'moneda']).agg(
            VM_Inicial=('valor_mercado', 'first'),
            VM_Final=('valor_mercado', 'last'),
            PYG=('pyg_acumulado', 'last'),
        ).reset_index()
        detalle_final['Retorno'] = ((detalle_final['VM_Final'] / detalle_final['VM_Inicial']) - 1).apply(lambda x: f"{x:.1%}")
        detalle_final['VM_Final'] = detalle_final['VM_Final'].apply(lambda x: _fmt_cop(x) if x > 1e6 else f"${x:,.0f}")
        detalle_final['PYG'] = detalle_final['PYG'].apply(lambda x: _fmt_cop(x) if abs(x) > 1e6 else f"${x:,.0f}")

        return html.Div([
            html.Span(f"✅ Proyección RV: {len(df_resultado)} registros", style={'color': 'green'}),
            html.H6("Resumen por Escenario", style={'marginTop': '10px'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in resumen.columns],
                data=resumen.to_dict('records'),
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'center', 'fontSize': '11px', 'padding': '4px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ),
            dcc.Graph(figure=fig),
            html.H6("Detalle por Título (Escenario Base)", style={'marginTop': '10px'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in ['titulo', 'tipo_rv', 'compania', 'moneda', 'VM_Final', 'PYG', 'Retorno']],
                data=detalle_final[['titulo', 'tipo_rv', 'compania', 'moneda', 'VM_Final', 'PYG', 'Retorno']].to_dict('records'),
                page_size=10,
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'left', 'fontSize': '10px', 'padding': '3px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
                sort_action='native',
            ),
        ])

    except Exception as e:
        import traceback
        return html.Span(f"❌ {traceback.format_exc()[-500:]}", style={'color': 'red', 'whiteSpace': 'pre-wrap', 'fontSize': '10px'})


# =============================================================================
# PESTAÑA 6: RESULTADOS (nuevo)
# =============================================================================
def layout_resultados():
    """Tab de resultados del escenario consolidado."""
    return html.Div([
        html.H4("📊 Resultados del Escenario", style={'marginBottom': '10px'}),
        html.Div(id='resultados-contenido', children=[
            html.Div([
                html.P("⏳ Aún no se ha corrido un escenario. Ve a la pestaña Inputs y presiona "
                       "'CORRER ESCENARIO'.", style={'color': '#666', 'fontSize': '12px', 'textAlign': 'center',
                                                     'padding': '40px 20px'}),
            ], style={'border': '2px dashed #dee2e6', 'borderRadius': '8px', 'marginTop': '30px'}),
        ]),
        dcc.Download(id='download-resultados-excel'),
    ], style={'padding': '15px'})


# =============================================================================
# PESTAÑA 7: ENCAJE POR RAMO
# =============================================================================
def _cargar_reservas_excel(fecha_inicio=None, fecha_fin=None):
    """
    Carga los saldos de reservas por ramo desde el insumo operativo
    'Inputs_Reservas e interes a la reserva actualizado.xlsx' (hoja 'Saldo
    Reservas'), ya homologados al diccionario de portafolios.

    Selecciona el mes de reserva aplicando la regla de MES CAIDO sobre
    `fecha_inicio` (día >= 20 → mes anterior; día < 20 → dos meses atrás) y, si
    el mes objetivo cae fuera del rango del insumo, proyecta con la tasa de
    crecimiento mensual geométrica propia de cada ramo, limitada por `fecha_fin`.

    Devuelve DataFrame[Ramo, Saldo_Actual, Compania].
    """
    from lector_reservas import cargar_reservas

    if fecha_inicio is None:
        # Sin fecha del sidebar no hay mes objetivo; devolver vacío controlado.
        return pd.DataFrame(columns=['Ramo', 'Saldo_Actual', 'Compania'])

    try:
        return cargar_reservas(fecha_inicio, fecha_fin)
    except Exception as e:
        print(f"[ENCAJE] Error cargando reservas: {e}")
        return pd.DataFrame(columns=['Ramo', 'Saldo_Actual', 'Compania'])


def _cargar_posicion_encaje():
    """
    Carga la posición del portafolio agrupada por ramo y compania para encaje=SI.
    Incluye primas por recaudar para ramos aplicables.
    """
    try:
        conn = get_conn()
        df = pd.read_sql_query(
            "SELECT ramo, compania, SUM(CAST(valor_mercado AS REAL)) as posicion "
            "FROM portafolio_clasificado WHERE encaje = 'SI' "
            "GROUP BY ramo, compania",
            conn
        )
        conn.close()

        # Sumar primas por recaudar a los ramos que aplican
        primas = _cargar_primas_por_recaudar()
        if not primas.empty:
            df['ramo_norm'] = df['ramo'].str.upper().str.strip()
            primas['ramo_norm'] = primas['Ramo'].str.upper().str.strip()
            df = df.merge(primas[['ramo_norm', 'Prima']], on='ramo_norm', how='left')
            df['Prima'] = df['Prima'].fillna(0.0)
            df['posicion'] = df['posicion'] + df['Prima']
            df = df.drop(columns=['ramo_norm', 'Prima'])

        return df
    except Exception as e:
        print(f"[ENCAJE] Error cargando posición: {e}")
        return pd.DataFrame(columns=['ramo', 'compania', 'posicion'])


def _cargar_primas_por_recaudar():
    """
    Primas por recaudar por ramo (valores fijos para Autos y Demás Generales).

    El insumo nuevo 'Inputs_Reservas e interes a la reserva actualizado.xlsx' no
    trae la columna 'Info Cartera' del formato antiguo, por lo que solo se
    aplican las primas fijas conocidas. Las primas porcentuales se calculan en
    modo proyección dentro de _aplicar_primas_proyectadas().

    Devuelve DataFrame[Ramo, Prima].
    """
    # Valores fijos para Autos y Demás Generales (en COP)
    PRIMAS_FIJAS = {
        'AUTOMOVILES': 39_000_000_000,
        'DEMAS RAMOS DE SEGUROS GENERALES': 13_500_000_000,
    }

    registros = [{'Ramo': ramo, 'Prima': prima} for ramo, prima in PRIMAS_FIJAS.items()]
    return pd.DataFrame(registros, columns=['Ramo', 'Prima'])


def _aplicar_proyeccion_reservas(df_reservas, mes_objetivo, escenario='Base'):
    """
    Aplica variaciones acumuladas desde enero hasta mes_objetivo a las reservas actuales.
    Reservas_Proyectadas = Saldo_Actual + Variacion_Acumulada(ene→mes)
    """
    from calcular_encaje import cargar_variaciones_reservas, acumular_variaciones_hasta_mes

    ruta_inputs = str(PROJECT_ROOT / "Inputs_Planeacion_Reservas.xlsx")

    try:
        df_var_total = cargar_variaciones_reservas(ruta_inputs, escenario)
        if df_var_total.empty:
            return df_reservas

        df_var_acum = acumular_variaciones_hasta_mes(df_var_total, mes_objetivo)
        if df_var_acum.empty:
            return df_reservas

        # Merge variaciones a reservas
        df = df_reservas.copy()
        df['ramo_norm'] = df['Ramo'].str.upper().str.strip()
        df_var_acum['ramo_norm'] = df_var_acum['Ramo'].str.upper().str.strip()
        df_var_agg = df_var_acum.groupby('ramo_norm', as_index=False)['Variacion_Acumulada'].sum()

        df = df.merge(df_var_agg, on='ramo_norm', how='left')
        df['Variacion_Acumulada'] = df['Variacion_Acumulada'].fillna(0.0)
        df['Saldo_Actual'] = df['Saldo_Actual'] + df['Variacion_Acumulada']
        df = df.drop(columns=['ramo_norm', 'Variacion_Acumulada'])

        return df

    except Exception as e:
        print(f"[ENCAJE] Error aplicando proyección: {e}")
        return df_reservas


def _aplicar_primas_proyectadas(df_posicion):
    """
    En modo proyección, las primas por recaudar se calculan como porcentaje
    de la posición total de cada ramo (sin primas).
    - Accidentes Personales: 53.1%
    - Vida Grupo: 41.6%
    - Vida Individual-Matemática: 6.6%
    - Salud: 30.6%
    - Automóviles: $39,000 MM fijo
    - Demás Ramos Generales: $13,500 MM fijo
    """
    PRIMAS_PORCENTAJE = {
        'ACCIDENTES PERSONALES': 0.531,
        'VIDA GRUPO': 0.416,
        'VIDA INDIVIDUAL-MATEMATICA': 0.066,
        'SALUD': 0.306,
    }
    PRIMAS_FIJAS = {
        'AUTOMOVILES': 39_000_000_000,
        'DEMAS RAMOS DE SEGUROS GENERALES': 13_500_000_000,
    }

    df = df_posicion.copy()
    df['ramo_upper'] = df['ramo'].str.upper().str.strip()

    for ramo, pct in PRIMAS_PORCENTAJE.items():
        mask = df['ramo_upper'] == ramo
        if mask.any():
            # Prima = posición * porcentaje / (1 - porcentaje)
            # Esto es: posición_final = posición_portafolio + prima
            # donde prima = posición_final * pct → prima = posición / (1-pct) * pct
            df.loc[mask, 'posicion'] = df.loc[mask, 'posicion'] * (1 + pct / (1 - pct))

    for ramo, prima_fija in PRIMAS_FIJAS.items():
        mask = df['ramo_upper'] == ramo
        if mask.any():
            df.loc[mask, 'posicion'] = df.loc[mask, 'posicion'] + prima_fija

    df = df.drop(columns=['ramo_upper'])
    return df


def _calcular_encaje(df_posicion, df_reservas):
    """
    Cruza posición vs reservas y calcula ratio + semáforo.
    """
    from calcular_encaje import UMBRAL_ENCAJE, UMBRAL_DEFAULT

    if df_posicion.empty or df_reservas.empty:
        return pd.DataFrame(columns=['Ramo', 'Compania', 'Posicion', 'Reservas', 'Encaje', 'Semaforo', 'Color_Semaforo'])

    # Normalizar claves para join
    df_pos = df_posicion.copy()
    df_pos['ramo_norm'] = df_pos['ramo'].str.upper().str.strip()
    df_pos['compania_norm'] = df_pos['compania'].str.upper().str.strip()

    df_res = df_reservas.copy()
    df_res['ramo_norm'] = df_res['Ramo'].str.upper().str.strip()
    df_res['compania_norm'] = df_res['Compania'].str.upper().str.strip()

    # Merge por ramo (la compañía puede diferir ligeramente, usamos solo ramo)
    merged = df_res.merge(
        df_pos.groupby('ramo_norm', as_index=False)['posicion'].sum(),
        on='ramo_norm', how='left'
    )
    merged['posicion'] = merged['posicion'].fillna(0.0)

    # Calcular encaje
    merged['Encaje'] = np.where(
        merged['Saldo_Actual'] > 0,
        merged['posicion'] / merged['Saldo_Actual'],
        np.nan
    )

    # Aplicar semáforo
    def clasificar_semaforo(row):
        compania = str(row.get('Compania', '')).upper().strip()
        umbral = UMBRAL_ENCAJE.get(compania, UMBRAL_DEFAULT)
        encaje = row.get('Encaje')

        if pd.isna(encaje):
            return '⚪ SIN DATO', '#999999'
        elif encaje < umbral:
            return '🔴 ALERTA', '#e74c3c'
        elif encaje < umbral + 0.05:
            return '🟡 AJUSTADO', '#c8a415'
        else:
            return '🟢 OK', '#008f4f'

    merged[['Semaforo', 'Color_Semaforo']] = merged.apply(
        lambda row: pd.Series(clasificar_semaforo(row)), axis=1
    )

    # Resultado limpio
    resultado = merged[['Ramo', 'Compania', 'posicion', 'Saldo_Actual', 'Encaje', 'Semaforo', 'Color_Semaforo']].copy()
    resultado = resultado.rename(columns={'posicion': 'Posicion', 'Saldo_Actual': 'Reservas'})
    resultado = resultado.sort_values('Reservas', ascending=False).reset_index(drop=True)

    return resultado


def layout_encaje(fecha_inicio=None, fecha_fin=None):
    """Tab de Encaje por Ramo: barras dobles posición vs reservas con semáforo.

    Las reservas se seleccionan según la fecha de inicio del sidebar (regla de
    mes caído) y se proyectan hasta la fecha fin cuando aplica.
    """
    # Cargar datos (reservas según fecha del sidebar)
    df_reservas = _cargar_reservas_excel(fecha_inicio, fecha_fin)
    df_posicion = _cargar_posicion_encaje()

    # Etiqueta del mes de reserva efectivamente usado (regla mes caído)
    etiqueta_mes = ""
    try:
        from lector_reservas import resolver_mes_objetivo
        if fecha_inicio is not None:
            _a, _m = resolver_mes_objetivo(fecha_inicio)
            _nombres = ['', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
            etiqueta_mes = f" — reserva de {_nombres[_m].capitalize()} {_a}"
    except Exception:
        etiqueta_mes = ""

    # Si no hay datos, mostrar mensaje informativo
    if df_reservas.empty:
        return html.Div([
            html.H4("🏦 Encaje por Ramo", style={'marginBottom': '10px'}),
            html.Div([
                html.P("⚠️ No se encontraron datos de reservas. Verifica que el archivo "
                       "'Inputs_Reservas e interes a la reserva actualizado.xlsx' esté en "
                       "'Plantillas Input' y que la fecha del sidebar sea válida.",
                       style={'color': '#666', 'fontSize': '12px', 'textAlign': 'center', 'padding': '40px 20px'}),
            ], style={'border': '2px dashed #dee2e6', 'borderRadius': '8px', 'marginTop': '30px'}),
        ], style={'padding': '15px'})

    # Calcular encaje
    df_encaje = _calcular_encaje(df_posicion, df_reservas)

    if df_encaje.empty:
        return html.Div([
            html.H4("🏦 Encaje por Ramo", style={'marginBottom': '10px'}),
            html.P("No hay datos de posición para calcular encaje.", style={'color': '#999'}),
        ], style={'padding': '15px'})

    # --- Cards resumen ---
    total_posicion = df_encaje['Posicion'].sum()
    total_reservas = df_encaje['Reservas'].sum()
    encaje_global = total_posicion / total_reservas if total_reservas > 0 else 0
    n_alertas = len(df_encaje[df_encaje['Semaforo'].str.contains('ALERTA')])

    card_style = {
        'display': 'inline-block', 'width': '23%', 'padding': '12px',
        'textAlign': 'center', 'borderRadius': '8px', 'marginRight': '2%',
        'border': '1px solid #008f4f', 'backgroundColor': '#f0faf5',
        'verticalAlign': 'top'
    }
    card_title_style = {'fontSize': '10px', 'color': '#555', 'margin': '0'}
    card_value_style = {'fontSize': '18px', 'fontWeight': 'bold', 'color': '#004d2a', 'margin': '4px 0'}

    encaje_color = '#008f4f' if encaje_global >= 1.02 else ('#c8a415' if encaje_global >= 1.0 else '#e74c3c')

    cards = html.Div([
        html.Div([
            html.P("Encaje Global", style=card_title_style),
            html.P(f"{encaje_global*100:.1f}%", style={**card_value_style, 'color': encaje_color}),
        ], style=card_style),
        html.Div([
            html.P("Ramos en Alerta", style=card_title_style),
            html.P(f"{n_alertas}", style={**card_value_style, 'color': '#e74c3c' if n_alertas > 0 else '#008f4f'}),
        ], style=card_style),
        html.Div([
            html.P("Posición Total", style=card_title_style),
            html.P(_fmt_cop(total_posicion), style=card_value_style),
        ], style=card_style),
        html.Div([
            html.P("Reservas Total", style=card_title_style),
            html.P(_fmt_cop(total_reservas), style=card_value_style),
        ], style={**card_style, 'marginRight': '0'}),
    ], style={'marginBottom': '15px'})

    # --- Opciones de compañía para filtro ---
    companias_disponibles = df_encaje['Compania'].dropna().unique().tolist()
    opciones_cia = [{'label': 'Todas', 'value': 'Todas'}] + [
        {'label': c.title(), 'value': c} for c in sorted(companias_disponibles)
    ]

    # --- Layout completo ---
    return html.Div([
        html.H4(f"🏦 Encaje por Ramo{etiqueta_mes}", style={'marginBottom': '10px', 'color': '#004d2a'}),
        cards,
        # Filtro por compañía
        html.Div([
            html.Label("Filtrar por Compañía:", style={'fontSize': '11px', 'fontWeight': 'bold', 'marginRight': '10px'}),
            dcc.Dropdown(
                id='encaje-filtro-compania',
                options=opciones_cia,
                value='Todas',
                clearable=False,
                style={'width': '220px', 'display': 'inline-block', 'verticalAlign': 'middle'}
            ),
        ], style={'marginBottom': '15px'}),
        # --- Proyección de encaje ---
        html.Div([
            html.Label("Proyección:", style={'fontSize': '11px', 'fontWeight': 'bold', 'marginRight': '10px'}),
            dcc.Dropdown(
                id='encaje-mes-proyeccion',
                options=[{'label': 'Actual (sin proyección)', 'value': 'actual'}] + [
                    {'label': m.capitalize(), 'value': m}
                    for m in ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                              'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
                ],
                value='actual',
                clearable=False,
                style={'width': '180px', 'display': 'inline-block', 'verticalAlign': 'middle', 'marginRight': '15px'}
            ),
        ], style={'marginBottom': '15px', 'padding': '10px', 'backgroundColor': '#f8f9fa',
                  'borderRadius': '6px', 'border': '1px solid #dee2e6'}),
        # Gráfica de barras dobles
        dcc.Loading(type='circle', children=[
            html.Div(id='encaje-grafico-container'),
        ]),
        # Store con datos base para callbacks
        dcc.Store(id='store-encaje-reservas', data=df_reservas.to_dict('records')),
        dcc.Store(id='store-encaje-posicion', data=df_posicion.to_dict('records')),
    ], style={'padding': '15px'})


@callback(
    Output('encaje-grafico-container', 'children'),
    [Input('encaje-filtro-compania', 'value'),
     Input('encaje-mes-proyeccion', 'value')],
    [State('store-encaje-reservas', 'data'),
     State('store-encaje-posicion', 'data'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=False
)
def actualizar_grafico_encaje(compania_filtro, mes_proy, reservas_data,
                              posicion_data, fecha_inicio, fecha_fin):
    """Actualiza el gráfico de barras dobles de encaje según filtro y proyección."""
    if not reservas_data or not posicion_data:
        return html.P("Sin datos de encaje.", style={'color': '#999'})

    df_reservas = pd.DataFrame(reservas_data)
    df_posicion = pd.DataFrame(posicion_data)

    # --- Selección del mes de reserva ---
    # 'actual'  -> la base ya viene del mes caído sobre la FECHA DE INICIO del
    #              sidebar (store-encaje-reservas), no se recalcula.
    # otro mes  -> el mes del dropdown es el mes de PORTAFOLIO; la reserva a usar
    #              es el mes anterior (M-1, mes caído de proyección). El insumo ya
    #              trae el saldo proyectado por mes, así que se toma directo (sin
    #              sumar variaciones).
    if mes_proy and mes_proy != 'actual':
        _MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
        try:
            mes_num = _MESES.index(str(mes_proy).lower()) + 1
            anio_portafolio = pd.to_datetime(fecha_inicio).year if fecha_inicio else datetime.now().year
            from lector_reservas import cargar_reservas_mes_portafolio
            df_nuevo = cargar_reservas_mes_portafolio(anio_portafolio, mes_num, fecha_fin)
            if not df_nuevo.empty:
                df_reservas = df_nuevo
        except Exception as e:
            print(f"[ENCAJE] Error cargando reservas del mes '{mes_proy}': {e}")

    # --- Calcular primas (en proyección usar porcentajes, en actual usar Excel) ---
    if mes_proy and mes_proy != 'actual':
        # En proyección: primas = porcentaje del total posición del ramo
        df_posicion = _aplicar_primas_proyectadas(df_posicion)
    # En modo actual, las primas ya están sumadas en df_posicion

    # Calcular encaje
    df_encaje = _calcular_encaje(df_posicion, df_reservas)

    if df_encaje.empty:
        return html.P("Sin datos para calcular encaje.", style={'color': '#999'})

    # Filtrar por compañía
    if compania_filtro and compania_filtro != 'Todas':
        df_encaje = df_encaje[df_encaje['Compania'] == compania_filtro]

    if df_encaje.empty:
        return html.P("Sin datos para esta compañía.", style={'color': '#999'})

    # Ordenar por reservas descendente
    df = df_encaje.sort_values('Reservas', ascending=True).reset_index(drop=True)

    # Crear gráfico de barras dobles horizontales
    fig = go.Figure()

    # Barra de Reservas (gris)
    fig.add_trace(go.Bar(
        y=df['Ramo'],
        x=df['Reservas'] / 1e9,
        orientation='h',
        name='Reservas',
        marker_color='rgba(44, 62, 80, 0.6)',
        text=df['Reservas'].apply(_fmt_cop),
        textposition='outside',
        textfont=dict(size=9),
    ))

    # Barra de Posición (verde si cumple, roja si no)
    colores_posicion = []
    for _, row in df.iterrows():
        if row['Posicion'] < row['Reservas']:
            colores_posicion.append('#e74c3c')  # Rojo — no cumple
        else:
            colores_posicion.append('#008f4f')  # Verde SB — cumple

    fig.add_trace(go.Bar(
        y=df['Ramo'],
        x=df['Posicion'] / 1e9,
        orientation='h',
        name='Posición Portafolio',
        marker_color=colores_posicion,
        text=df['Posicion'].apply(_fmt_cop),
        textposition='outside',
        textfont=dict(size=9),
    ))

    # Calcular max para el eje x
    max_val = max(df['Reservas'].max(), df['Posicion'].max()) / 1e9
    x_range = [0, max_val * 1.3]

    n_ramos = len(df)
    fig_height = max(400, n_ramos * 50 + 100)

    fig.update_layout(
        barmode='group',
        xaxis_title='Miles de Millones COP',
        yaxis_title='',
        height=fig_height,
        margin=dict(l=10, r=120, t=30, b=30),
        xaxis=dict(range=x_range, tickformat=',.0f'),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        plot_bgcolor='white',
    )

    # Tabla resumen con semáforo
    df_display = df.sort_values('Reservas', ascending=False).reset_index(drop=True)
    tabla_rows = []
    for _, row in df_display.iterrows():
        encaje_pct = f"{row['Encaje']*100:.1f}%" if pd.notna(row['Encaje']) else "N/A"
        tabla_rows.append(html.Tr([
            html.Td(row['Semaforo'][:2], style={'textAlign': 'center', 'fontSize': '14px', 'padding': '4px'}),
            html.Td(row['Ramo'], style={'fontSize': '11px', 'padding': '4px'}),
            html.Td(row['Compania'].title() if row['Compania'] else '', style={'fontSize': '11px', 'padding': '4px', 'textAlign': 'center'}),
            html.Td(_fmt_cop(row['Posicion']), style={'fontSize': '11px', 'padding': '4px', 'textAlign': 'right'}),
            html.Td(_fmt_cop(row['Reservas']), style={'fontSize': '11px', 'padding': '4px', 'textAlign': 'right'}),
            html.Td(encaje_pct, style={'fontSize': '11px', 'padding': '4px', 'textAlign': 'center',
                                       'fontWeight': 'bold', 'color': row['Color_Semaforo']}),
        ]))

    tabla = html.Table([
        html.Thead(html.Tr([
            html.Th("", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center', 'width': '30px'}),
            html.Th("Ramo", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'left'}),
            html.Th("Compañía", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
            html.Th("Posición", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'right'}),
            html.Th("Reservas", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'right'}),
            html.Th("Encaje", style={'padding': '6px', 'backgroundColor': '#008f4f', 'color': 'white', 'textAlign': 'center'}),
        ])),
        html.Tbody(tabla_rows),
    ], style={'width': '100%', 'borderCollapse': 'collapse', 'border': '1px solid #dee2e6', 'marginTop': '15px'})

    return html.Div([
        dcc.Graph(figure=fig, config={'responsive': True}),
        tabla,
    ])


# =============================================================================
# CALLBACK: CORRER ESCENARIO CONSOLIDADO
# =============================================================================
@app.callback(
    [Output('store-resultados-escenario', 'data'),
     Output('msg-escenario', 'children')],
    Input('btn-correr-escenario', 'n_clicks'),
    [State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date'),
     State('input-trm-final', 'value'),
     State('esc-shift-tes-cop', 'value'),
     State('esc-shift-tes-uvr', 'value'),
     State('slider-fs-shift', 'value'),
     State('slider-ipc-shift', 'value'),
     State('esc-rv-local', 'value'),
     State('esc-rv-intl', 'value'),
     State('esc-rv-pei', 'value'),
     State('esc-ret-alt', 'value'),
     State('esc-ret-alt-intl', 'value'),
     State('esc-ipc-4g', 'value'),
     State('esc-spread-4g', 'value'),
     State('esc-cred-ipc', 'value'),
     State('esc-cred-ibr', 'value'),
     State('esc-repo-ini', 'value'),
     State('esc-repo-fin', 'value'),
     State('esc-fed', 'value'),
     State('esc-tesoros10y', 'value'),
     State('esc-shift-subyac', 'value'),
     State('store-tes-cop', 'data'),
     State('store-tes-uvr', 'data'),
     State({'type': 'slider-cop', 'index': ALL}, 'value'),
     State({'type': 'slider-uvr', 'index': ALL}, 'value'),
     State('store-sendas', 'data'),
     State('store-senda-repo', 'data'),
     State('proy-metodo', 'value'),
     State('proy-exp-obj', 'value'),
     State('proy-trato', 'value'),
     State('proy-momento', 'value'),
     State('proy-opc-largo', 'value'),
     State('store-senda-inf-esc', 'data'),
     # Modo de proyección y controles por tramo (feature proyeccion-por-tramos).
     State('modo-proyeccion', 'value'),
     State({'type': 'tramo-shift-cop', 'index': ALL}, 'value'),
     State({'type': 'tramo-shift-uvr', 'index': ALL}, 'value'),
     State({'type': 'tramo-shift-fs', 'index': ALL}, 'value'),
     State({'type': 'tramo-shift-ipc', 'index': ALL}, 'value'),
     State({'type': 'tramo-periodicidad', 'index': ALL}, 'value'),
     # Grid multiactivo por tramo (tarea 7.2/7.3, feature proyeccion-por-tramos-multiactivo).
     # ORDEN CRÍTICO: Dash pasa los States posicionalmente en el orden del decorador; estos
     # se añaden al final del decorador Y de la firma en el MISMO orden.
     State({'type': 'tramo-inflacion', 'index': ALL}, 'value'),
     State({'type': 'tramo-repo', 'index': ALL}, 'value'),
     State({'type': 'tramo-trm', 'index': ALL}, 'value'),
     State({'type': 'tramo-ret-acc-local', 'index': ALL}, 'value'),
     State({'type': 'tramo-ret-acc-intl', 'index': ALL}, 'value'),
     State({'type': 'tramo-ret-alt-local', 'index': ALL}, 'value'),
     State({'type': 'tramo-ret-alt-intl', 'index': ALL}, 'value'),
     State({'type': 'tramo-ret-alt-ipc', 'index': ALL}, 'value'),
     State({'type': 'tramo-pei', 'index': ALL}, 'value'),
     # Derivados por tramo (Req 13.4): Δ Fed y Δ Tesoros 10Y en bps.
     State({'type': 'tramo-fed', 'index': ALL}, 'value'),
     State({'type': 'tramo-tesoros10y', 'index': ALL}, 'value'),
     # IPC vigente (arranque de la inflación del primer tramo en modo por tramos).
     State('esc-ipc-vigente', 'value')],
    background=True,
    progress=[Output('escenario-progreso', 'children')],
    running=[
        # Deshabilita el botón mientras corre.
        (Output('btn-correr-escenario', 'disabled'), True, False),
        # Indicador de progreso: "procesando" al iniciar, se limpia al terminar. NO se
        # usa `msg-escenario` aquí (lo controla el `return` del callback), para que el
        # mensaje final de ÉXITO o ERROR nunca sea pisado por el `running` (antes el
        # `no_update` de finalización dejaba el error sin mostrarse en pantalla).
        (Output('escenario-progreso', 'children'),
         html.Span("⏳ Iniciando corrida…", style={'color': '#0d6efd'}),
         html.Span("")),
    ],
    prevent_initial_call=True
)
def correr_escenario_consolidado(
        set_progress,
        n_clicks, fecha_inicio, fecha_fin, trm_final,
        shift_cop, shift_uvr, shift_fs, shift_ipc,
        rv_local, rv_intl, rv_pei, ret_alt, ret_alt_intl,
        ipc_4g, spread_4g, cred_ipc, cred_ibr,
        repo_ini, repo_fin, esc_fed, esc_tesoros10y, shift_subyac,
        store_cop, store_uvr, sliders_cop, sliders_uvr,
        store_sendas, store_senda_repo,
        metodo_deriv, exp_obj, trato, momento, opc_largo,
        store_senda_inf,
        modo_proyeccion=None, tramo_shift_cop=None, tramo_shift_uvr=None,
        tramo_shift_fs=None, tramo_shift_ipc=None, tramo_periodicidad=None,
        tramo_inflacion=None, tramo_repo=None, tramo_trm=None, tramo_ret_acc_local=None,
        tramo_ret_acc_intl=None,
        tramo_ret_alt_local=None, tramo_ret_alt_intl=None,
        tramo_ret_alt_ipc=None, tramo_pei=None,
        tramo_fed=None, tramo_tesoros10y=None, ipc_vigente=None):
    """Ejecuta todos los motores y consolida resultados.

    UVR t0 y UVR cierre ya no son inputs: se calculan internamente (t0 vigente,
    cierre desde la senda de inflación).
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    import sys, traceback
    try:
        if str(modo_proyeccion) == 'por_tramos':
            # Armar el juego de shifts por tramo desde los inputs dinámicos (ALL respeta
            # el orden por índice de tramo). Cada elemento es un dict de shifts del tramo.
            n = max(
                len(tramo_shift_cop or []), len(tramo_periodicidad or []),
                len(tramo_inflacion or []), len(tramo_repo or []),
                len(tramo_trm or []), len(tramo_ret_acc_local or []),
            )
            def _g(lst, i, d=0):
                return (lst[i] if lst and i < len(lst) and lst[i] is not None else d)
            shifts_por_tramo = [{
                'shift_cop': _g(tramo_shift_cop, i),
                'shift_uvr': _g(tramo_shift_uvr, i),
                'shift_fs': _g(tramo_shift_fs, i),
                'shift_ipc': _g(tramo_shift_ipc, i),
            } for i in range(n)]
            periodicidad_por_tramo = [
                _g(tramo_periodicidad, i, 'mensual') for i in range(n)
            ]
            # ── Grid multiactivo por tramo (tarea 7.3) ──────────────────────────────
            # Convención de unidades (documentada):
            #   * Inputs de PORCENTAJE en la UI (inflación, repo, retornos EA, PEI) se
            #     ingresan como número tipo 14.33 (%). Se dividen entre 100 para pasarlos
            #     a decimal EA que espera `InputsPorTramo.from_valores` (ej. 0.1433).
            #   * Los BPS (movimientos de curva y spreads: shift-cop/uvr, spread-ipc/tf,
            #     ret-alt-ipc) se pasan TAL CUAL (el motor los interpreta como bps).
            #   * TRM de cierre se pasa TAL CUAL (nivel, no porcentaje).
            # `from_valores` coerciona/valida cada campo (entrada NO confiable) y aplica
            # el default documentado a los vacíos/no numéricos, sin abortar la corrida.
            def _pct(lst, i):
                """Lee un input de porcentaje por índice y lo pasa a decimal EA (/100).

                Devuelve None cuando el valor es vacío/no numérico para que
                `from_valores` aplique su default documentado por campo.
                """
                v = _g(lst, i, None)
                if v is None:
                    return None
                try:
                    return float(v) / 100.0
                except (TypeError, ValueError):
                    return None
            inputs_por_tramo = [{
                'inflacion_ea':        _pct(tramo_inflacion, i),
                'repo_ea':             _pct(tramo_repo, i),
                'mov_tes_cop_bps':     _g(tramo_shift_cop, i, None),
                'mov_tes_uvr_bps':     _g(tramo_shift_uvr, i, None),
                # Mov Corp TF / Corp IPC del grid (ids tramo-shift-fs / tramo-shift-ipc):
                # el motor RF los consume vía shifts_por_tramo (shift_fs / shift_ipc). Se
                # replican aquí para que InputsPorTramo quede completo (documentación del
                # tramo); el spread corp diferencial se retiró de la UI por redundante.
                'spread_corp_ipc_bps': _g(tramo_shift_ipc, i, None),
                'spread_corp_tf_bps':  _g(tramo_shift_fs, i, None),
                'trm_cierre':          _g(tramo_trm, i, None),
                'ret_acc_local_ea':    _pct(tramo_ret_acc_local, i),
                'ret_acc_intl_ea':     _pct(tramo_ret_acc_intl, i),
                'ret_alt_local_ea':    _pct(tramo_ret_alt_local, i),
                'ret_alt_intl_ea':     _pct(tramo_ret_alt_intl, i),
                'ret_alt_ipc_bps':     _g(tramo_ret_alt_ipc, i, None),
                'pei_ea':              _pct(tramo_pei, i),
                # Δ Fed y Δ Tesoros 10Y del tramo en bps (Req 13.4), tal cual (no /100).
                'delta_fed_bps':          _g(tramo_fed, i, None),
                'delta_tesoros10y_bps':   _g(tramo_tesoros10y, i, None),
            } for i in range(n)]
            return _ejecutar_escenario_dispatch(
                'por_tramos',
                fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                shifts_por_tramo=shifts_por_tramo,
                periodicidad_por_tramo=periodicidad_por_tramo,
                variacion_anual_por_tramo=None,  # (variación anual de reserva: entrada futura)
                trm_final=trm_final, repo_ini=repo_ini, repo_fin=repo_fin,
                store_senda_inf=store_senda_inf,
                ipc_vigente=ipc_vigente,  # arranque de inflación del primer tramo
                inputs_por_tramo=inputs_por_tramo,  # tarea 7.3: cablea el grid multiactivo
                metodo_deriv=metodo_deriv,  # método de derivados (Req 13)
                set_progress=set_progress,  # reporte de progreso por tramo (9.3)
            )
        # Modo actual (punta a punta), retrocompatible.
        return _ejecutar_escenario(
            fecha_inicio, fecha_fin, None, None, trm_final,
            shift_cop, shift_uvr, shift_fs, shift_ipc,
            rv_local, rv_intl, rv_pei, ret_alt, ret_alt_intl,
            ipc_4g, spread_4g, cred_ipc, cred_ibr,
            repo_ini, repo_fin, esc_fed, esc_tesoros10y,
            store_cop, store_uvr, sliders_cop, sliders_uvr,
            store_sendas, store_senda_repo,
            metodo_deriv, exp_obj, trato, momento, opc_largo,
            store_senda_inf, shift_subyac)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[AXIS ERROR] correr_escenario:\n{tb}")
        msg = html.Div([
            html.Span(f"❌ Error: {str(e)[:200]}", style={'color': 'red'}),
            html.Pre(tb[-400:], style={'fontSize': '9px', 'maxHeight': '150px', 'overflow': 'auto'}),
        ])
        return None, msg


def _construir_df_proy_rf(df_base, port, fecha_corte):
    """
    Construye la parte de RENTA FIJA del portafolio proyectado a partir del portafolio
    base y de la lista de títulos del motor (`port`) tras la corrida.

    Réplica EXACTA de la lógica de la sección 7 (RF) de `_ejecutar_escenario`, extraída a
    un helper reutilizable por el modo actual y por el orquestador por tramos:
      1. Elimina los títulos vencidos (fecha_vencimiento <= fecha_corte) que pasaron por el motor.
      2. Actualiza `valor_mercado` de los títulos vivos con el VM del motor.
      3. Añade filas de reinversión (títulos nuevos del motor), heredando la clasificación
         del título origen.

    NO excluye colaterales/simultáneas ni reclasifica: esos pasos se aplican después
    (ver `_finalizar_df_proy`), para permitir intercalar RV/alt/derivados en el modo actual.

    Args:
        df_base: DataFrame del portafolio de inicio (portafolio_clasificado o snapshot).
        port: lista de objetos Titulo del motor tras `proyectar_portafolio`.
        fecha_corte: fecha de corte del tramo/horizonte (date).

    Returns:
        (df_proy, resumen) donde resumen es un dict con conteos para diagnóstico.
    """
    df_proy = df_base.copy()
    df_proy['valor_mercado'] = pd.to_numeric(df_proy['valor_mercado'], errors='coerce').fillna(0)

    if not port:
        return df_proy, {'actualizados': 0, 'vencidos': 0, 'reinversiones': 0}

    vm_motor_rf = {t.id_posicion: t.valor_mercado for t in port if t.fecha_vencimiento > fecha_corte}
    ids_vencidos = {t.id_posicion for t in port if t.fecha_vencimiento <= fecha_corte}

    # Guardar filas de vencidos ANTES de eliminarlos (template para reinversiones).
    df_vencidos_template = df_proy[df_proy['id_posicion'].isin(ids_vencidos)].copy()

    df_proy = df_proy[~df_proy['id_posicion'].isin(ids_vencidos)]
    # Actualizar VM para los que siguen vivos.
    mask_rf = df_proy['id_posicion'].isin(vm_motor_rf.keys())
    df_proy.loc[mask_rf, 'valor_mercado'] = df_proy.loc[mask_rf, 'id_posicion'].map(vm_motor_rf)

    # Reinversiones: títulos nuevos del motor que no existen en el portafolio base.
    ids_existentes = set(df_proy['id_posicion'].tolist())
    reinversiones = [t for t in port
                     if t.fecha_vencimiento > fecha_corte
                     and t.id_posicion not in ids_existentes
                     and hasattr(t, 'es_reinversion') and t.es_reinversion]
    if reinversiones:
        # VECTORIZACIÓN (Fix 3, Req 17.4): el bucle original hacía, por cada reinversión,
        # un filtro lineal del DataFrame (`df_proy[df_proy['id_posicion']==origen]`) +
        # `.iloc[0].to_dict()`. Con decenas de miles de reinversiones eso es O(n²) y es el
        # cuello de botella. Aquí se construye UNA sola vez un índice {id_posicion: fila}
        # para la plantilla de herencia (lookup O(1)), preservando EXACTAMENTE la lógica:
        #   - La fila origen se busca primero en los vivos (df_proy) y, si no está, en los
        #     vencidos (df_vencidos_template) — mismo orden de precedencia que el original.
        #   - Si no hay fila origen, se parte de un dict vacío.
        #   - Se sobrescriben los MISMOS 9 campos con los valores del título del motor.
        # El resultado (filas y valores) es idéntico al del bucle fila-por-fila.
        _plantillas: dict = {}
        # Vencidos primero para que los vivos SOBRESCRIBAN (precedencia: vivos > vencidos).
        for _df_src in (df_vencidos_template, df_proy):
            if _df_src is not None and not _df_src.empty and 'id_posicion' in _df_src.columns:
                # drop_duplicates(keep='first') replica iloc[0] (primera coincidencia).
                _src = _df_src.drop_duplicates(subset='id_posicion', keep='first')
                for _rec in _src.to_dict('records'):
                    _plantillas[_rec['id_posicion']] = _rec

        import re as _re_reinv

        def _id_raiz_reinv(_idp):
            """Id del título ORIGINAL del que desciende una reinversión.

            Las reinversiones tienen ids sintéticos encadenados, p. ej.
            '849718_R20260819_R20261119' (título 849718 reinvertido dos veces). El prefijo
            antes del primer sufijo '_R<fecha>'/'_V<fecha>' es el id del título real del
            base, cuyos atributos de clasificación (compania/ramo/emisor/asset_class/…) se
            quieren heredar. Para ids sin ese patrón, devuelve el id tal cual.
            """
            _s = str(_idp)
            _m = _re_reinv.split(r'_[RV]\d', _s, maxsplit=1)
            return _m[0] if _m else _s

        filas_reinv = []
        for t in reinversiones:
            titulo_origen_id = t.titulo_origen if hasattr(t, 'titulo_origen') else ''
            _base = _plantillas.get(titulo_origen_id)
            # Fallback: si el origen directo es a su vez una reinversión (no está en el
            # base), heredar del TÍTULO RAÍZ (id original antes del primer _R/_V). Así las
            # reinversiones de reinversiones conservan compania/ramo/emisor/clasificación
            # en vez de quedar con todas las columnas vacías en el snapshot.
            if _base is None and titulo_origen_id:
                _base = _plantillas.get(_id_raiz_reinv(titulo_origen_id))
            if _base is None:
                _base = _plantillas.get(_id_raiz_reinv(t.id_posicion))
            nueva_fila = dict(_base) if _base is not None else {}
            nueva_fila['id_posicion'] = t.id_posicion
            nueva_fila['especie'] = t.especie
            nueva_fila['valor_mercado'] = t.valor_mercado
            nueva_fila['nominal'] = t.nominal
            nueva_fila['compania'] = t.compania
            nueva_fila['ramo'] = t.ramo
            nueva_fila['portafolio'] = t.portafolio
            nueva_fila['fecha_vencimiento'] = str(t.fecha_vencimiento)
            nueva_fila['asset_class_definitivo'] = t.asset_class if hasattr(t, 'asset_class') else nueva_fila.get('asset_class_definitivo', '')
            filas_reinv.append(nueva_fila)
        if filas_reinv:
            df_reinv = pd.DataFrame(filas_reinv)
            df_proy = pd.concat([df_proy, df_reinv], ignore_index=True)

    resumen = {
        'actualizados': int(mask_rf.sum()),
        'vencidos': len(ids_vencidos),
        'reinversiones': len(reinversiones),
    }
    return df_proy, resumen


def _finalizar_df_proy(df_proy, fecha_corte):
    """
    Aplica los pasos finales comunes a la construcción del portafolio proyectado:
    excluir colaterales (recibido/enviado) y simultáneas (Repo_Simultanea), y reclasificar
    en memoria a `fecha_corte`. Réplica exacta del cierre de la sección 7.
    """
    from clasificar_portafolio import clasificar_portafolio_en_memoria
    if 'especie' in df_proy.columns:
        df_proy = df_proy[~df_proy['especie'].str.contains('(?i)recibido|enviado', na=False)]
    if 'subtipo_vehiculo' in df_proy.columns:
        df_proy = df_proy[df_proy['subtipo_vehiculo'] != 'Repo_Simultanea']
    return clasificar_portafolio_en_memoria(df_proy, fecha_corte)


def _construir_df_proy_tramo(df_base, port, fecha_corte):
    """
    Construye el df_proy COMPLETO de un tramo (RF) a partir del portafolio base y la lista
    de títulos del motor, y lo finaliza (excluye colaterales/simultáneas + reclasifica a
    `fecha_corte`). Es la pieza reutilizable por el orquestador por tramos.

    Nota: el modo por tramos solo re-proyecta renta fija por tramo; RV/alt/derivados no se
    encadenan corte a corte en esta feature (ver design). El modo actual (punta a punta)
    sigue intercalando RV/alt/derivados en su propia sección 7.
    """
    df_proy, _resumen = _construir_df_proy_rf(df_base, port, fecha_corte)
    return _finalizar_df_proy(df_proy, fecha_corte)


def _ejecutar_escenario(fecha_inicio, fecha_fin, uvr_t0, uvr_cierre, trm_final,
                         shift_cop, shift_uvr, shift_fs, shift_ipc,
                         rv_local, rv_intl, rv_pei, ret_alt, ret_alt_intl,
                         ipc_4g, spread_4g, cred_ipc, cred_ibr,
                         repo_ini, repo_fin, esc_fed, esc_tesoros10y,
                         store_cop, store_uvr, sliders_cop, sliders_uvr,
                         store_sendas, store_senda_repo,
                         metodo_deriv, exp_obj, trato, momento, opc_largo,
                         store_senda_inf=None, shift_subyac=None):
    """Lógica interna del escenario."""
    import sys, traceback
    from dateutil.relativedelta import relativedelta
    from proyeccion_tramos_multiactivo import meses_horizonte_inclusivo

    fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else date.today()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + timedelta(days=365)
    # Conteo_Meses_Inclusivo (Req 10.2/10.6/10.7): se usa el helper centralizado
    # meses_horizonte_inclusivo, que aplica delta.years*12 + delta.months +
    # (1 if delta.days > 0 else 0). Antes esta rama omitía el "+1" de días y un año
    # completo (p. ej. 31-dic → 31-dic siguiente) proyectaba solo 11 meses. Con la
    # corrección un periodo anual proyecta los 12 meses completos, unificando la
    # fórmula ya usada correctamente en el resto del archivo.
    n_meses = meses_horizonte_inclusivo(fecha_ini, fecha_end)
    if n_meses < 1:
        n_meses = 1
    # NOTA: se respeta el horizonte real solicitado por el usuario. Antes existía un
    # recorte silencioso a 12 meses ("if n_meses > 12: n_meses = 12") que hacía que una
    # proyección a varios años valorara solo el primer año sin avisar, llevando a decidir
    # sobre datos falsos. El horizonte proyectado se reporta en el mensaje de estado.
    # Horizontes largos pueden tardar; la solución de fondo (background callback) es una
    # fase posterior del plan.
    print(f"[ESCENARIO] Inicio: {fecha_ini}, Fin: {fecha_end}, Meses: {n_meses}")

    resultados = {
        'parametros': {
            'fecha_inicio': str(fecha_ini), 'fecha_fin': str(fecha_end),
            'uvr_t0': uvr_t0, 'uvr_cierre': uvr_cierre, 'trm_final': trm_final,
            'shift_cop': shift_cop, 'shift_uvr': shift_uvr,
            'shift_fs': shift_fs, 'shift_ipc': shift_ipc,
            'rv_local': rv_local, 'rv_intl': rv_intl,
            'ret_alt': ret_alt, 'ret_alt_intl': ret_alt_intl,
            'repo_ini': repo_ini, 'repo_fin': repo_fin,
        },
        'rf_resumen': None, 'derivados': None,
        'simultaneas': None, 'rv': None,
        'alternativos': None, 'creditos': None,
    }
    errores = []
    port = []  # Inicializar para uso en portafolio proyectado
    # OPTIMIZACIÓN #1 (dedup condicional): se promueven a scope de función los
    # DataFrames crudos del motor RV (Sección 4) y Alternativos (Sección 5) para
    # poder REUTILIZARLOS en la Sección 7 y evitar una segunda ejecución del motor,
    # SIEMPRE que la reutilización sea numéricamente idéntica al camino actual
    # (ver Sección 7). Son locales a _ejecutar_escenario (no colisionan con el
    # df_alt del orquestador por tramos, que está en otra función).
    df_rv_res = None   # salida cruda de proyectar_renta_variable (Sección 4)
    df_alt = None      # salida cruda de proyectar_alternativos (Sección 5)
    # Senda TRM del escenario para RV (Sección 4): si es None/plana==trm_final,
    # la Sección 7 puede reutilizar df_rv_res sin cambiar números.
    senda_trm_rv_esc = None
    # Inputs efectivos de la Sección 5 (Alternativos), para decidir en la Sección 7
    # si df_alt es reutilizable (mismos inputs que la 2ª corrida ⇒ mismo resultado).
    _alt_senda_ipc_esc = None    # dict senda_ipc usada en Sección 5
    _alt_spreads_esc = None      # spreads_4g usados en Sección 5 (None = default)
    _alt_senda_trm_esc = None    # senda_trm usada en Sección 5 (None = sin TRM)

    # Extraer senda de inflación mensual (% EA) del store
    _senda_inf_mensual = None
    if store_senda_inf:
        try:
            # store_senda_inf es una lista de dicts: [{'fecha': 'YYYY-MM', 'inf_mensual': 0.003xx}, ...]
            # inf_mensual es la tasa mensual (decimal). Convertir a IPC anual (% EA):
            # IPC_EA = (1 + inf_mensual)^12 - 1, luego pasar a %
            _senda_inf_mensual = []
            for item in store_senda_inf:
                inf_m = item.get('inf_mensual', 0.004)
                # Si ya viene como inf_anual en %, usar directo
                if 'inf_anual' in item and item['inf_anual']:
                    _senda_inf_mensual.append(float(item['inf_anual']))
                else:
                    # Convertir mensual → anual en %
                    ipc_ea_pct = ((1 + inf_m) ** 12 - 1) * 100.0
                    _senda_inf_mensual.append(ipc_ea_pct)
        except Exception:
            _senda_inf_mensual = None

    # Fallback: si no hay senda del store-senda-inf, usar inflación de store_sendas (senda lineal)
    if _senda_inf_mensual is None and store_sendas:
        try:
            _df_sendas_inf = pd.DataFrame(store_sendas)
            if 'inflación' in _df_sendas_inf.columns:
                _df_sendas_inf['inflación'] = pd.to_numeric(_df_sendas_inf['inflación'], errors='coerce')
                # store_sendas tiene inflación como decimal (ej: 0.067 = 6.7%)
                # Convertir a % EA para el motor
                _senda_inf_mensual = [float(v) * 100.0 for v in _df_sendas_inf['inflación'].dropna().tolist()]
                if _senda_inf_mensual:
                    print(f"[ESCENARIO] Senda inflación de store_sendas: {len(_senda_inf_mensual)} pts, "
                          f"{_senda_inf_mensual[0]:.2f}%→{_senda_inf_mensual[-1]:.2f}% EA")
        except Exception as _e:
            print(f"[ESCENARIO] No se pudo extraer inflación de store_sendas: {_e}")

    if _senda_inf_mensual is None:
        print("[ESCENARIO] ⚠️ Sin senda de inflación — títulos IPC usarán IPC vigente constante")

    # ─── 1. RENTA FIJA ───
    try:
        from motor_proyeccion import cargar_portafolio_proyeccion, proyectar_portafolio
        from curvas_rendimiento import extraer_curva_base_pip_db, generar_senda_lineal
        import time as _time
        _t0 = _time.time()
        _fecha_pip_str = fecha_ini.strftime('%Y-%m-%d')
        port = cargar_portafolio_proyeccion(str(DB_PATH), _fecha_pip_str)
        print(f"[ESCENARIO] RF: {len(port)} títulos, horizonte={n_meses}m")
        # Guardar VM inicial y valor TIR inicial por título para calcular P&L individual
        vm_inicial_por_titulo = {t.id_posicion: t.valor_mercado for t in port}
        vtir_inicial_por_titulo = {t.id_posicion: (t.valor_tir if hasattr(t, 'valor_tir') and t.valor_tir else t.valor_mercado) for t in port}
        valor_uvr = float(uvr_t0)
        # UVR de cierre: valor del usuario, o calcularla desde la senda de inflación
        # (capitalizando uvr_t0 con el IPC mensual). Nunca cae a uvr_t0 (inflación cero).
        if uvr_cierre:
            valor_uvr_cierre_rf = float(uvr_cierre)
        else:
            _uvr_c = _calcular_uvr_cierre_desde_inflacion(uvr_t0, fecha_ini, fecha_end, store_sendas)
            if not _uvr_c:
                return no_update, html.Span(
                    "⚠️ No se pudo calcular la UVR de cierre. Ingresa UVR Cierre o carga la senda de inflación.",
                    style={'color': '#e65100'})
            valor_uvr_cierre_rf = float(_uvr_c)

        # TRM: extraer senda del store o interpolar linealmente.
        # _trm_t0 = TRM de mercado vigente HOY (fecha de corte); _trm_final = pronóstico del usuario.
        _trm_t0_vigente = obtener_trm_para(_fecha_ini_str)
        _trm_t0 = float(store_sendas[0]['TRM']) if store_sendas else float(_trm_t0_vigente or trm_final)
        _trm_final = float(trm_final)
        if store_sendas:
            try:
                _df_sendas = pd.DataFrame(store_sendas)
                _df_sendas['TRM'] = pd.to_numeric(_df_sendas['TRM'], errors='coerce')
                if not _df_sendas['TRM'].isna().all():
                    _trm_t0 = float(_df_sendas['TRM'].iloc[0])
                    _trm_final = float(_df_sendas['TRM'].iloc[-1])
            except Exception:
                pass

        # Curvas PIP desde hist_pip para la fecha de inicio del escenario
        # (_fecha_pip_str ya calculado arriba, mismo valor)
        curvas_base = extraer_curva_base_pip_db(str(DB_PATH), _fecha_pip_str)
        if not curvas_base:
            return no_update, html.Span(
                f"⚠️ No hay curvas PIP en hist_pip para {_fecha_pip_str}. Carga el PIP de esa fecha por la pestaña Inputs.",
                style={'color': '#e65100'}
            )
        print(f"[ESCENARIO] PIP usado desde hist_pip: {_fecha_pip_str}")
        
        # Guardar curvas originales (sin shift) para generar sendas graduales
        curvas_originales = {nombre: dict(curva) for nombre, curva in curvas_base.items()}
        
        # Aplicar sliders individuales de la curva TES (nodo por nodo)
        # Los sliders están en la misma unidad que el gráfico: delta / 100 = puntos de tasa
        if store_cop and sliders_cop and "DP_TF" in curvas_base:
            nodos_tf = sorted(curvas_base["DP_TF"].keys())
            for i, nodo in enumerate(nodos_tf[:len(sliders_cop)]):
                if i < len(sliders_cop) and sliders_cop[i]:
                    curvas_base["DP_TF"][nodo] += float(sliders_cop[i]) / 100.0
        if store_uvr and sliders_uvr and "DP_UVR" in curvas_base:
            nodos_uvr_list = sorted(curvas_base["DP_UVR"].keys())
            for i, nodo in enumerate(nodos_uvr_list[:len(sliders_uvr)]):
                if i < len(sliders_uvr) and sliders_uvr[i]:
                    curvas_base["DP_UVR"][nodo] += float(sliders_uvr[i]) / 100.0
        
        # Aplicar shifts paralelos adicionales (se suman a los sliders)
        if "DP_TF" in curvas_base and shift_cop:
            for nodo in curvas_base["DP_TF"]:
                curvas_base["DP_TF"][nodo] += float(shift_cop) / 100.0
        if "DP_UVR" in curvas_base and shift_uvr:
            for nodo in curvas_base["DP_UVR"]:
                curvas_base["DP_UVR"][nodo] += float(shift_uvr) / 100.0
        if "CORP_TF" in curvas_base and shift_fs:
            for nodo in curvas_base["CORP_TF"]:
                curvas_base["CORP_TF"][nodo] += float(shift_fs) / 100.0
        if "CORP_IPC" in curvas_base and shift_ipc:
            for nodo in curvas_base["CORP_IPC"]:
                curvas_base["CORP_IPC"][nodo] += float(shift_ipc) / 100.0
        
        # Generar sendas: transición lineal de curva_original → curva_target (con sliders+shifts)
        sendas = {}
        for nombre, curva_target in curvas_base.items():
            curva_t0 = curvas_originales.get(nombre, curva_target)
            sendas[nombre] = generar_senda_lineal(curva_t0, curva_target, n_meses)
        # DEBUG: verificar que los shifts se están aplicando
        print(f"[ESCENARIO] RF shifts: COP={shift_cop}, UVR={shift_uvr}, FS={shift_fs}, IPC={shift_ipc}")
        print(f"[ESCENARIO] RF sliders: COP={len(sliders_cop) if sliders_cop else 0} nodos, UVR={len(sliders_uvr) if sliders_uvr else 0} nodos")
        if sliders_uvr:
            print(f"[ESCENARIO] RF sliders UVR valores: {sliders_uvr}")
        if sliders_cop:
            non_zero_cop = [(i, v) for i, v in enumerate(sliders_cop) if v and v != 0]
            if non_zero_cop:
                print(f"[ESCENARIO] RF sliders COP non-zero: {non_zero_cop[:5]}")
        if "DP_TF" in curvas_originales and "DP_TF" in curvas_base:
            nodo_ejemplo = list(curvas_originales["DP_TF"].keys())[len(curvas_originales["DP_TF"])//2]
            t0 = curvas_originales["DP_TF"][nodo_ejemplo]
            tf = curvas_base["DP_TF"][nodo_ejemplo]
            print(f"[ESCENARIO] RF curva DP_TF nodo {nodo_ejemplo}: t0={t0:.6f} → target={tf:.6f} (delta={tf-t0:.6f})")
        # Obtener IPC vigente a T0 para deflactar rendimientos PIP de bonos IPC
        _ipc_t0_valor = None
        try:
            from gestor_informes import obtener_ipc_vigente as _get_ipc
            _ipc_val_t0, _ = _get_ipc(fecha_ini)
            if _ipc_val_t0 is not None:
                _ipc_t0_valor = _ipc_val_t0 / 100.0  # De % a decimal
        except Exception:
            pass
        if _ipc_t0_valor is None:
            # Fallback: usar último de la serie
            try:
                from valorar_bonos import cargar_serie_ipc as _csi2, _obtener_ipc_vigente as _oiv2
                _s2 = _csi2(str(SCRIPT_DIR / "Serie Inflación.xlsx"))
                _ipc_t0_valor = _oiv2(fecha_ini, _s2)
            except Exception:
                _ipc_t0_valor = 0.0614  # Default

        # Construir senda repo mensual (% anual) para Cta Ahorro y FIC
        _senda_repo_mensual = None
        if store_senda_repo:
            try:
                _senda_repo_mensual = [float(d['tasa']) for d in store_senda_repo]
            except Exception:
                _senda_repo_mensual = None
        if _senda_repo_mensual is None:
            _r_ini = float(repo_ini or 9.75)
            _r_fin = float(repo_fin or repo_ini or 9.75)
            _senda_repo_mensual = list(np.linspace(_r_ini, _r_fin, n_meses + 1))

        res_rf = proyectar_portafolio(
            portafolio=port, fecha_inicio=fecha_ini,
            meses_horizonte=n_meses, sendas=sendas,
            valor_uvr=valor_uvr, valor_uvr_cierre=valor_uvr_cierre_rf,
            trm=_trm_t0, trm_cierre=_trm_final,
            senda_inflacion=_senda_inf_mensual,
            shift_ipc_bps=float(shift_ipc or 0),
            shift_cop_bps=float(shift_cop or 0),
            shift_uvr_bps=float(shift_uvr or 0),
            shift_fs_bps=float(shift_fs or 0),
            shift_subyac_bps=float(shift_subyac or 0),
            shift_tesoros_bps=float(esc_tesoros10y or 0),  # Ya es delta en bps
            ipc_vigente_t0=_ipc_t0_valor,
            senda_repo=_senda_repo_mensual)
        print(f"[ESCENARIO] RF completado en {_time.time()-_t0:.1f}s, IPC_T0={_ipc_t0_valor*100:.2f}%, shift_IPC={shift_ipc}bps")

        # Importar valorar_titulo para la re-valoración parcial
        from motor_proyeccion import valorar_titulo as _vt
        from curvas_rendimiento import obtener_nombre_curva as _onc, obtener_tasa_senda as _ots

        # Re-valorar al fecha_end exacto (días parciales después del último mes completo)
        fecha_ultimo_mes = fecha_ini + relativedelta(months=n_meses)
        if fecha_end > fecha_ultimo_mes:
            from valorar_bonos import cargar_serie_ipc as _csi
            try:
                _serie_ipc_rf = _csi(str(SCRIPT_DIR / "Serie Inflación.xlsx"))
            except Exception:
                _serie_ipc_rf = pd.DataFrame()
            for t in port:
                if t.fecha_vencimiento <= fecha_end:
                    continue
                try:
                    # Para títulos IPC: recalcular yield con spread deflactado + shift + IPC cierre
                    if t.tipo_bono == 'IPC' and t.spread_mercado > 0:
                        _spread_r = (1 + t.spread_mercado / 100.0) / (1 + _ipc_t0_valor) - 1
                        _spread_sh = _spread_r + float(shift_ipc or 0) / 10000.0
                        # IPC vigente a fecha_end: la senda está indexada por mes de MEDICIÓN
                        # IPC vigente en mes M = IPC medido en M-1 = senda[M-1]
                        # Para dic-2026: vigente = medido en nov = penúltimo punto
                        if _senda_inf_mensual and len(_senda_inf_mensual) >= 2:
                            _ipc_end = _senda_inf_mensual[-2] / 100.0
                        elif _senda_inf_mensual:
                            _ipc_end = _senda_inf_mensual[-1] / 100.0
                        else:
                            _ipc_end = _ipc_t0_valor
                        # Yield de descuento siempre usa IPC vigente a fecha de valoración
                        _tasa_target = ((1 + _ipc_end) * (1 + _spread_sh) - 1) * 100.0
                    else:
                        # TF, UVR, Yankees: rendimiento PIP individual + shift directo
                        if t.spread_mercado > 0:
                            _curva_nombre = _onc(t.asset_class)
                            if t.tipo_bono == 'UVR':
                                _shift_t = float(shift_uvr or 0) / 100.0
                            elif t.asset_class in ('LEGATRUU', 'Estructuras'):
                                _shift_t = float(esc_tesoros10y or 0) / 100.0
                            elif t.tipo_bono == 'YANKEE' or _curva_nombre == 'USD':
                                _shift_t = float(shift_subyac or 0) / 100.0
                            elif _curva_nombre == 'DP_TF':
                                _shift_t = float(shift_cop or 0) / 100.0
                            else:
                                _shift_t = float(shift_fs or 0) / 100.0
                            _tasa_target = t.spread_mercado + _shift_t
                        else:
                            # Fallback: curva genérica
                            _curva_nombre = _onc(t.asset_class)
                            _dias_res = (t.fecha_vencimiento - fecha_end).days
                            if _curva_nombre in sendas and _dias_res > 0:
                                _tasa_target = _ots(sendas[_curva_nombre], n_meses, _dias_res)
                            else:
                                _tasa_target = t.tasa_mercado
                    # VM a mercado al fecha_end
                    vm_end = _vt(t, _tasa_target, fecha_end, valor_uvr=valor_uvr_cierre_rf,
                                 trm=float(trm_final or _trm_t0), serie_ipc=_serie_ipc_rf,
                                 senda_inflacion=_senda_inf_mensual, fecha_inicio_senda=fecha_ini)
                    t.valor_mercado = vm_end
                    t.tasa_mercado = _tasa_target
                    # VTir al fecha_end (para IPC VC/DV se usa la lógica de ipc_vencimiento inline)
                    if t.tipo_bono == 'IPC' and t.clasificacion in ('1VC', 'Vencimiento', '1DV', 'Disp. Venta'):
                        from valorar_bonos import valorar_ipc_vencimiento as _vipc, _dias_nl365 as _dnl
                        import calendar as _cal2
                        _valor_inv = (t.valor_tir / t.nominal) * 100.0 if t.nominal > 0 else 100.0
                        _s2 = t.spread_ipc / 100.0
                        _ipc_p = float(_serie_ipc_rf.iloc[-1]["ipc_anual"]) if not _serie_ipc_rf.empty else 0.06
                        if _senda_inf_mensual:
                            _ipc_p = _senda_inf_mensual[min(n_meses, len(_senda_inf_mensual)-1)] / 100.0
                        # Generar fechas cupon
                        _mp2 = {'anual':12,'semestral':6,'trimestral':3,'mensual':1}.get(t.frecuencia, 3)
                        _tfs = []
                        _db2 = t.fecha_emision.day; _mb2 = t.fecha_emision.month; _ab2 = t.fecha_emision.year
                        _p2 = 1
                        while True:
                            _tm2 = _mb2 + _mp2 * _p2
                            _a2 = _ab2 + (_tm2-1)//12; _mc2 = (_tm2-1)%12+1
                            _md2 = _cal2.monthrange(_a2, _mc2)[1]
                            _fc2 = date(_a2, _mc2, min(_db2, _md2))
                            if _fc2 > t.fecha_vencimiento: break
                            _tfs.append(_fc2); _p2 += 1
                        if not _tfs or _tfs[-1] != t.fecha_vencimiento:
                            _tfs.append(t.fecha_vencimiento)
                        # Flujos desde fecha_ultimo_mes
                        _flujos2 = []
                        for _i2, _fc2 in enumerate(_tfs):
                            if _fc2 <= fecha_ultimo_mes: continue
                            _fip2 = _tfs[_i2-1] if _i2 > 0 else t.fecha_emision
                            # Determinar IPC per-period según uso_tasa y senda
                            if t.uso_tasa in ("F", "PREVIA"):
                                _fecha_ipc_ref2 = _fip2
                            else:
                                _fecha_ipc_ref2 = _fc2
                            _ipc_fl = _ipc_p  # default
                            if _senda_inf_mensual:
                                _m_desde = ((_fecha_ipc_ref2.year - fecha_ini.year) * 12
                                            + _fecha_ipc_ref2.month - fecha_ini.month)
                                if _m_desde >= 0:
                                    _ipc_fl = _senda_inf_mensual[min(_m_desde, len(_senda_inf_mensual)-1)] / 100.0
                            _fea2 = (1+_ipc_fl)*(1+_s2)-1
                            _dp2 = _dnl(_fip2, _fc2)
                            _tp2 = (1+_fea2)**(_dp2/365.0)-1
                            _fl2 = 100.0*_tp2 + (100.0 if _fc2 == _tfs[-1] else 0)
                            _flujos2.append((_fc2, _fl2))
                        if _flujos2:
                            # OPTIMIZACIÓN #3 (patrón Fix 3): los plazos en años
                            # `_dnl(fecha_ultimo_mes, _f2)/365.0` NO dependen de la tasa
                            # `_ym2` que itera la bisección; se precalculan UNA sola vez
                            # como pares (monto, t_años) en lugar de recalcular _dnl en
                            # cada iteración y por flujo. Mismas fechas, montos y fórmula
                            # (mismo orden de suma) → resultado numéricamente idéntico.
                            _flujos2_ini_t = [(_m2, _dnl(fecha_ultimo_mes, _f2) / 365.0)
                                              for _f2, _m2 in _flujos2]
                            # Resolver TIR desde fecha_ultimo_mes
                            # OPTIMIZACIÓN #4b: 60 iteraciones de bisección sobre
                            # [-0.05, 3.0] dan ancho 3.05/2^60 ≈ 2.6e-18 << tol 1e-6
                            # (por debajo de la resolución del float64); float idéntico
                            # al de 500 iteraciones.
                            _yl2, _yh2 = -0.05, 3.0
                            for _ in range(60):
                                _ym2 = (_yl2+_yh2)/2
                                _vp2 = sum(_m2/(1+_ym2)**_t2 for _m2,_t2 in _flujos2_ini_t)
                                if _vp2 > _valor_inv: _yl2 = _ym2
                                else: _yh2 = _ym2
                            # VP al fecha_end (días precalculados una sola vez)
                            _flujos_end_t = [(_m2, _dnl(fecha_end, _f2) / 365.0)
                                             for _f2, _m2 in _flujos2 if _f2 > fecha_end]
                            _vp_end = sum(_m2/(1+_ym2)**_t2 for _m2,_t2 in _flujos_end_t) if _flujos_end_t else 0
                            t.valor_tir = t.nominal * _vp_end / 100.0
                    else:
                        vtir_end = _vt(t, t.tasa_compra, fecha_end, valor_uvr=valor_uvr_cierre_rf,
                                       trm=float(trm_final or _trm_t0), serie_ipc=_serie_ipc_rf)
                        t.valor_tir = vtir_end
                except Exception:
                    pass  # Mantener valores del último mes completo
            print(f"[ESCENARIO] RF re-valorado a {fecha_end} (días parciales)")

        if res_rf:
            resultados['rf_resumen'] = [{
                'mes': r.mes, 'fecha': r.fecha.strftime('%Y-%m'),
                'vm_total': r.vm_total, 'vtir_total': r.vtir_total,
                'pnl_mercado_mes': r.pnl_mercado_mes, 'pnl_tir_mes': r.pnl_tir_mes,
                'pnl_mercado_acum': r.pnl_mercado_acum,
                'n_titulos': r.n_titulos,
                'cupones_mes': r.cupones_cobrados_mes,
            } for r in res_rf]
            # Detalle por título: usar valor_tir del motor (incluye reinversiones)
            from motor_proyeccion import valorar_titulo as _vt
            valor_uvr_fin = float(valor_uvr_cierre_rf)
            valor_uvr_ini = float(valor_uvr)
            trm_v = float(trm_final)
            detalle = []
            n_dv = 0

            for t in port:
                if t.fecha_vencimiento <= fecha_end:
                    continue
                vm_ini = vm_inicial_por_titulo.get(t.id_posicion, 0)
                # Para reinversiones: vm_ini es el monto del cupón reinvertido
                if vm_ini == 0 and hasattr(t, 'es_reinversion') and t.es_reinversion:
                    vm_ini = getattr(t, 'vm_compra', t.nominal)
                vm_mercado = t.valor_mercado  # valorado a tasa de mercado por el motor
                vm_tir = t.valor_tir if hasattr(t, 'valor_tir') and t.valor_tir else vm_ini
                vm_tir_ini = vtir_inicial_por_titulo.get(t.id_posicion, vm_ini)

                # Clasificación contable
                clasif = t.clasificacion if hasattr(t, 'clasificacion') else ''
                clasif_str = str(clasif).strip()
                if 'Disp' in clasif_str or 'Venta' in clasif_str:
                    cat_contable = 'DV'
                    n_dv += 1
                elif 'Venc' in clasif_str:
                    cat_contable = 'VC'
                else:
                    cat_contable = 'NEG'

                # P&L según categoría contable
                if cat_contable == 'NEG':
                    # Negociables: Todo va a P&L (variación a mercado + cupones cobrados)
                    cupones_titulo = getattr(t, '_cupones_cobrados', 0.0)
                    total_resultado = (vm_mercado - vm_ini) + cupones_titulo
                    pyg = total_resultado
                    ori = 0.0
                elif cat_contable == 'DV':
                    # DV: Total = variación a mercado + cupones, P&L = causación, ORI = diferencia
                    cupones_titulo = getattr(t, '_cupones_cobrados', 0.0)
                    total_resultado = (vm_mercado - vm_ini) + cupones_titulo
                    pyg = (vm_tir - vm_tir_ini) + cupones_titulo
                    ori = total_resultado - pyg
                else:  # VC
                    # VC: Todo va a P&L (causación a TIR de compra)
                    cupones_titulo = getattr(t, '_cupones_cobrados', 0.0)
                    total_resultado = (vm_tir - vm_tir_ini) + cupones_titulo
                    pyg = total_resultado
                    ori = 0.0

                detalle.append({
                    'id_posicion': t.id_posicion,
                    'especie': t.especie,
                    'compania': t.compania,
                    'ramo': t.ramo,
                    'asset_class': t.asset_class,
                    'clasificacion': cat_contable,
                    'tipo_bono': t.tipo_bono,
                    'moneda': t.moneda,
                    'nominal': t.nominal,
                    'vm_inicial': vm_ini,
                    'vm_mercado': vm_mercado,
                    'vtir_ini': vm_tir_ini,
                    'vtir_fin': vm_tir,
                    'pyg': pyg,
                    'ori': ori,
                    'total_resultado': total_resultado,
                    'tasa_mercado': t.tasa_mercado,
                    'tasa_compra': t.tasa_compra,
                    'tir_descuento': getattr(t, '_tir_descuento', None),
                    'fecha_vencimiento': str(t.fecha_vencimiento),
                })
            resultados['rf_detalle'] = detalle

            # --- RF Mensual Contable: P&L por mes desde snapshots por título ---
            # Se usa EXACTAMENTE la misma lógica contable de RF_Detalle_Titulo (NEG/DV/VC)
            # pero descompuesta mes a mes. Así la suma de RF_Mensual == suma de RF_Detalle_Titulo.
            # Solo se incluyen títulos vivos al final (mismos que aparecen en RF_Detalle_Titulo).
            # La regla contable vive en el helper puro compartido `rf_contable_mensual`
            # (reutilizable por el modo por_tramos), extraído aquí sin cambio de resultado.
            try:
                from rf_contable_mensual import construir_rf_mensual_contable
                rf_mensual_contable = construir_rf_mensual_contable(res_rf, port, fecha_end)
                resultados['rf_mensual_contable'] = rf_mensual_contable
                print(f"[ESCENARIO] RF_Mensual contable calculado: {len(rf_mensual_contable)} meses")
            except Exception as e:
                print(f"[ESCENARIO] Error calculando RF_Mensual contable: {e}")
                resultados['rf_mensual_contable'] = None
            # Debug
            cats = {}
            for d in detalle:
                c = d['clasificacion']
                cats[c] = cats.get(c, 0) + 1
            print(f"[ESCENARIO] RF detalle: {len(detalle)} títulos, clasificación: {cats}")
            print(f"[ESCENARIO] DV: {n_dv} títulos")
            if n_dv > 0:
                dv_sample = [d for d in detalle if d['clasificacion'] == 'DV'][:3]
                for s in dv_sample:
                    print(f"  DV: {s['especie'][:25]} vtir_ini={s['vtir_ini']:.0f} vtir_fin={s['vtir_fin']:.0f} pyg={s['pyg']:.0f} vm_mkt={s['vm_mercado']:.0f} ori={s['ori']:.0f}")
    except Exception as e:
        errores.append(f"RF: {str(e)[:100]}")

    # ─── 2. DERIVADOS ───
    try:
        if DERIVADOS_DISPONIBLE:
            conn = get_conn()
            try:
                trm_df = pd.read_sql_query("SELECT trm FROM hist_opciones LIMIT 1", conn)
                trm_actual = float(str(trm_df['trm'].iloc[0]).replace(',', ''))
            except Exception:
                trm_actual = float(trm_final)
            conn.close()
            senda_trm = None
            if store_sendas:
                df_s = pd.DataFrame(store_sendas)
                df_s['TRM'] = pd.to_numeric(df_s['TRM'], errors='coerce')
                if not df_s['TRM'].isna().all():
                    senda_trm = df_s['TRM'].tolist()
                    # nivel_actual debe ser el primer punto de la senda (consistencia)
                    trm_actual = senda_trm[0]
            # Si no hay senda cargada pero sí TRM final, generar senda lineal
            if senda_trm is None and trm_final:
                trm_fin = float(trm_final)
                if abs(trm_fin - trm_actual) > 1:  # solo si hay diferencia
                    senda_trm = list(np.linspace(trm_actual, trm_fin, n_meses + 1))
            print(f"[ESCENARIO] Derivados: TRM_actual={trm_actual}, senda_trm={'plana' if senda_trm is None else f'{len(senda_trm)} pts [{senda_trm[0]:.0f}→{senda_trm[-1]:.0f}]'}")
            # Construir metodo_params con deltas de curvas para derivados
            metodo_params = {}
            if int(metodo_deriv or 1) == 3:
                metodo_params = {
                    'exposicion_obj': float(exp_obj or 30) / 100,
                    'trato_exposicion': int(trato or 1),
                    'momento_exposicion': int(momento or 1),
                    'opciones_sobre_largo': float(opc_largo or 70) / 100,
                }
            # Delta TES COP (shift paralelo en bps → convertir a delta EA por nodo)
            if shift_cop and float(shift_cop) != 0:
                # Shift paralelo: mismo delta para todos los nodos
                delta_cop_ea = float(shift_cop) / 10000  # bps a decimal
                # Nodos estándar de la curva TES COP (días)
                nodos_cop = [30, 90, 180, 365, 730, 1095, 1825, 2555, 3650]
                metodo_params['delta_tes_cop'] = {n: delta_cop_ea for n in nodos_cop}
            # Delta TES UVR (shift paralelo)
            if shift_uvr and float(shift_uvr) != 0:
                delta_uvr_ea = float(shift_uvr) / 10000
                nodos_uvr = [30, 90, 180, 365, 730, 1095, 1825, 2555, 3650]
                metodo_params['delta_tes_uvr'] = {n: delta_uvr_ea for n in nodos_uvr}
            # Delta Fed y Tesoros 10Y (variación en bps respecto al nivel actual)
            # Los inputs son niveles finales (%), calcular delta vs default
            fed_actual = 5.25  # valor por defecto del input
            tesoros10y_actual = 4.70  # valor por defecto del input
            delta_fed_bps = float(esc_fed or 0)  # Ya viene como delta en bps
            delta_10t_bps = float(esc_tesoros10y or 0)  # Ya viene como delta en bps
            if delta_fed_bps != 0 or delta_10t_bps != 0:
                metodo_params['delta_fed_bps'] = delta_fed_bps
                metodo_params['delta_10t_bps'] = delta_10t_bps

            conn = get_conn()
            try:
                df_port = pd.read_sql_query(
                    "SELECT moneda, valor_mercado, clase_origen FROM portafolio_clasificado", conn)
            except Exception:
                df_port = pd.DataFrame()
            conn.close()
            activos_usd, port_pesos = 0.0, 0.0
            if not df_port.empty:
                df_port['valor_mercado'] = pd.to_numeric(df_port['valor_mercado'], errors='coerce').fillna(0)
                df_tit = df_port[df_port['clase_origen'] == 'TITULO'] if 'clase_origen' in df_port.columns else df_port
                mask_usd = ~df_tit['moneda'].isin(['COP', 'UVR'])
                activos_usd = float(df_tit.loc[mask_usd, 'valor_mercado'].sum()) / trm_actual
                port_pesos = float(df_tit.loc[df_tit['moneda'].isin(['COP', 'UVR']), 'valor_mercado'].sum())
            res_deriv = valorar_portafolio_derivados(
                fecha_corte=str(fecha_ini), fecha_fin=str(fecha_end),
                metodo=int(metodo_deriv or 1), nivel_actual=trm_actual,
                activos_usd=activos_usd, portafolio_pesos=port_pesos,
                senda_trm=senda_trm, metodo_params=metodo_params if metodo_params else None)
            print(f"[DERIV INPUTS] TRM={trm_actual:.2f}, senda={senda_trm[:3]}...{senda_trm[-1:]}, metodo={metodo_deriv}")
            print(f"[DERIV INPUTS] metodo_params={metodo_params}")
            pyg_deriv = calcular_pyg(res_deriv)
            resultados['derivados'] = pyg_deriv.to_dict('records')
            print(f"[ESCENARIO] Derivados OK: {len(pyg_deriv)} registros, P&L recs={len(resultados['derivados'])}")
    except Exception as e:
        import traceback as _tb
        print(f"[ESCENARIO] ERROR Derivados: {e}")
        print(_tb.format_exc()[-500:])
        errores.append(f"Derivados: {str(e)[:100]}")

    # ─── 3. SIMULTÁNEAS PASIVAS ───
    try:
        from proyeccion_simultaneas_pasivas import proyectar_simultaneas_pasivas
        fecha_ini_str = fecha_ini.strftime('%Y-%m')
        fecha_fin_str = fecha_end.strftime('%Y-%m')
        if store_senda_repo:
            periodos_str = [d['fecha'] + '-01' for d in store_senda_repo]
            tasas = [d['tasa'] / 100.0 for d in store_senda_repo]
            df_senda_r = pd.DataFrame({'fecha': periodos_str, 'tasa_repo': tasas})
        else:
            r_ini = float(repo_ini or 9.75) / 100.0
            r_fin_v = float(repo_fin or 7.75) / 100.0
            periodos = pd.date_range(start=f"{fecha_ini_str}-01", periods=n_meses, freq='MS')
            tasas = np.linspace(r_ini, r_fin_v, n_meses)
            df_senda_r = pd.DataFrame({'fecha': [d.strftime('%Y-%m-%d') for d in periodos], 'tasa_repo': tasas})
        conn = get_conn()
        df_senda_r.to_sql('senda_tasa_repo', conn, if_exists='replace', index=False)
        conn.close()
        df_sim = proyectar_simultaneas_pasivas(
            fecha_inicio=fecha_ini_str, fecha_fin=fecha_fin_str,
            ruta_db_portafolio=str(DB_PATH), conn_inputs=get_conn(),
            fecha_inicio_exacta=fecha_ini.strftime('%Y-%m-%d'))
        if not df_sim.empty:
            resumen_sim = df_sim.groupby('fecha_proyeccion').agg(
                costo_mensual=('costo_mensual', 'sum'),
                vm_total=('valor_mercado', 'sum')).reset_index()
            resultados['simultaneas'] = resumen_sim.to_dict('records')
    except Exception as e:
        errores.append(f"Simultáneas: {str(e)[:100]}")

    # ─── 4. RENTA VARIABLE ───
    try:
        from proyeccion_renta_variable import proyectar_renta_variable as proyectar_rv_fn
        fecha_ini_str = fecha_ini.strftime('%Y-%m')
        fecha_end_str = fecha_end.strftime('%Y-%m')
        tasa_local_rv = float(rv_local or 12) / 100
        tasa_intl_rv = float(rv_intl or 8) / 100
        tasa_pei_rv = float(rv_pei or 10) / 100
        trm_v = float(trm_final)

        # Construir senda TRM para RV internacional
        _periodos_rv = pd.date_range(start=f"{fecha_ini_str}-01", end=f"{fecha_end_str}-01", freq="MS")
        senda_trm_rv_esc = construir_senda_trm_dict(store_sendas, _periodos_rv)

        df_rv_res = proyectar_rv_fn(
            fecha_inicio=fecha_ini_str, fecha_fin=fecha_end_str,
            escenarios_rv_local={"base": tasa_local_rv},
            escenarios_rv_intl={"base": tasa_intl_rv},
            tasa_pei=tasa_pei_rv,
            ruta_db=str(SCRIPT_DIR / 'portafolio.db'),
            senda_trm=senda_trm_rv_esc,
        )
        if not df_rv_res.empty:
            # portafolio_clasificado ya tiene valor_mercado en COP (incluso MXWD)
            # NO se requiere conversión TRM adicional.

            rv_resumen = df_rv_res.groupby('fecha_proyeccion').agg(
                vm_total=('valor_mercado', 'sum'),
                pyg_mensual=('pyg_mensual', 'sum')).reset_index()
            rv_resumen['pyg_acum'] = rv_resumen['pyg_mensual'].cumsum()
            resultados['rv'] = rv_resumen.to_dict('records')
            # Detalle por título (mismo agrupamiento que la tabla del dash)
            # Primero sumar VM por titulo+compania+fecha (multiple id_posicion del mismo ETF)
            rv_agrupado = df_rv_res.groupby(['titulo', 'compania', 'tipo_rv', 'moneda', 'fecha_proyeccion']).agg(
                valor_mercado=('valor_mercado', 'sum'),
                pyg_mensual=('pyg_mensual', 'sum')
            ).reset_index().sort_values(['titulo', 'compania', 'fecha_proyeccion'])
            # Luego tomar primero y último mes por titulo+compania
            rv_por_titulo = rv_agrupado.groupby(['titulo', 'compania', 'tipo_rv', 'moneda']).agg(
                valor_inicial=('valor_mercado', 'first'),
                valor_final=('valor_mercado', 'last'),
                pyg_total=('pyg_mensual', 'sum')
            ).reset_index()
            rv_por_titulo['retorno_%'] = ((rv_por_titulo['valor_final'] / rv_por_titulo['valor_inicial']) - 1) * 100
            resultados['rv_detalle'] = rv_por_titulo.to_dict('records')
            print(f"[ESCENARIO] RV: {len(df_rv_res)} registros, PyG total: {rv_resumen['pyg_acum'].iloc[-1]/1e9:.1f} MM")
    except Exception as e:
        errores.append(f"RV: {str(e)[:100]}")

    # ─── 5. ALTERNATIVOS ───
    try:
        from proyeccion_alternativos import proyectar_alternativos
        fecha_ini_str = fecha_ini.strftime('%Y-%m')
        fecha_end_str = fecha_end.strftime('%Y-%m')
        tasa_alt_local = float(ret_alt or 10) / 100
        tasa_alt_intl = float(ret_alt_intl or 12) / 100
        ipc_4g_val = float(ipc_4g or 7.07) / 100
        spread_4g_val = float(spread_4g or 3.5) / 100

        periodos_alt = pd.date_range(start=f"{fecha_ini_str}-01", end=f"{fecha_end_str}-01", freq="MS")

        # Construir senda IPC por corte usando la senda de inflación del escenario
        if _senda_inf_mensual and len(_senda_inf_mensual) >= len(periodos_alt):
            # _senda_inf_mensual es % EA por mes → convertir a decimal
            senda_ipc_alt = {p.strftime("%Y-%m-%d"): _senda_inf_mensual[i] / 100.0
                            for i, p in enumerate(periodos_alt)}
        elif _senda_inf_mensual:
            # Usar lo que haya, extender con el último valor
            senda_ipc_alt = {}
            for i, p in enumerate(periodos_alt):
                idx = min(i, len(_senda_inf_mensual) - 1)
                senda_ipc_alt[p.strftime("%Y-%m-%d")] = _senda_inf_mensual[idx] / 100.0
        else:
            # Fallback: IPC constante del input
            senda_ipc_alt = {p.strftime("%Y-%m-%d"): ipc_4g_val for p in periodos_alt}

        # Construir senda TRM por corte desde store_sendas
        # (se conserva la guarda de que el store traiga la columna 'mes', como en el original)
        senda_trm_alt = None
        if store_sendas and 'mes' in pd.DataFrame(store_sendas).columns:
            senda_trm_alt = construir_senda_trm_dict(store_sendas, periodos_alt)

        # Spread uniforme — el motor detecta dinámicamente cuáles títulos son IPC por es_ipc='SI'
        spreads_4g_dict = None  # Usa SPREAD_IPC_DEFAULT (3.5%) si no se especifica
        if spread_4g_val != 0.035:
            from proyeccion_alternativos import cargar_portafolio_alternativos as _cpa_esc
            _port_alt_esc = _cpa_esc(str(SCRIPT_DIR / 'portafolio.db'))
            _ids_ipc_esc = _port_alt_esc[_port_alt_esc['es_ipc'] == 'SI']['id_posicion'].tolist()
            spreads_4g_dict = {int(id_pos): spread_4g_val for id_pos in _ids_ipc_esc}

        # Registrar los inputs efectivos para la decisión de reutilización (#1) en S7.
        _alt_senda_ipc_esc = senda_ipc_alt
        _alt_spreads_esc = spreads_4g_dict
        _alt_senda_trm_esc = senda_trm_alt

        df_alt = proyectar_alternativos(
            fecha_inicio=fecha_ini_str, fecha_fin=fecha_end_str,
            tasa_ea_local=tasa_alt_local, tasa_ea_intl=tasa_alt_intl,
            senda_ipc=senda_ipc_alt, spreads_4g=spreads_4g_dict,
            senda_trm=senda_trm_alt,
            ruta_db_portafolio=str(SCRIPT_DIR / 'portafolio.db'))

        if not df_alt.empty:
            alt_resumen = df_alt.groupby('fecha_proyeccion').agg(
                pyg_mensual=('pyg_mensual', 'sum'),
                vm_total=('valor_mercado', 'sum')).reset_index()
            alt_resumen['pyg_acum'] = alt_resumen['pyg_mensual'].cumsum()
            resultados['alternativos'] = alt_resumen.to_dict('records')
            # Detalle por título (mismo agrupamiento que la tabla del dash)
            alt_agrupado = df_alt.groupby(['fondo', 'tipo_fondo', 'moneda', 'fecha_proyeccion']).agg(
                valor_mercado=('valor_mercado', 'sum'),
                pyg_mensual=('pyg_mensual', 'sum'),
                metodologia=('metodologia', 'first')
            ).reset_index().sort_values(['fondo', 'fecha_proyeccion'])
            alt_por_titulo = alt_agrupado.groupby(['fondo', 'tipo_fondo', 'moneda']).agg(
                valor_inicial=('valor_mercado', 'first'),
                valor_final=('valor_mercado', 'last'),
                pyg_total=('pyg_mensual', 'sum'),
                metodologia=('metodologia', 'first')
            ).reset_index()
            alt_por_titulo['retorno_%'] = ((alt_por_titulo['valor_final'] / alt_por_titulo['valor_inicial']) - 1) * 100
            resultados['alt_detalle'] = alt_por_titulo.to_dict('records')
            print(f"[ESCENARIO] Alternativos: {len(df_alt)} registros, PyG total: {alt_resumen['pyg_acum'].iloc[-1]/1e9:.1f} MM")
    except Exception as e:
        errores.append(f"Alternativos: {str(e)[:100]}")

    # ─── 6. CRÉDITOS CONTRATADOS ───
    try:
        from proyeccion_creditos import proyectar_creditos_contratados
        fecha_ini_str = fecha_ini.strftime('%Y-%m')
        ipc_cred = float(cred_ipc or 7.07) / 100
        ibr_cred = float(cred_ibr or 7.07) / 100

        periodos_cred = pd.date_range(start=f"{fecha_ini_str}-01", end=fecha_end, freq="MS")
        df_senda_ipc_c = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos_cred], "ipc": [ipc_cred] * len(periodos_cred)})
        df_senda_ibr_c = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos_cred], "ibr": [ibr_cred] * len(periodos_cred)})

        df_cred = proyectar_creditos_contratados(
            fecha_inicio=fecha_ini_str, df_senda_ipc=df_senda_ipc_c, df_senda_ibr=df_senda_ibr_c)

        if not df_cred.empty:
            cred_resumen = df_cred.groupby('fecha_proyeccion').agg(
                interes_mes=('interes_mes', 'sum'),
                saldo_fin=('saldo_fin_mes', 'sum')).reset_index()
            cred_resumen['interes_acum'] = cred_resumen['interes_mes'].cumsum()
            resultados['creditos'] = cred_resumen.to_dict('records')
            print(f"[ESCENARIO] Créditos: {len(df_cred)} registros, Costo total: {cred_resumen['interes_acum'].iloc[-1]/1e9:.1f} MM")
    except Exception as e:
        errores.append(f"Créditos: {str(e)[:100]}")

    # ─── 7. CONSTRUIR PORTAFOLIO PROYECTADO ───
    try:
        from clasificar_portafolio import clasificar_portafolio_en_memoria

        # Leer TODO el portafolio clasificado actual (ya tiene clasificación correcta vía
        # diccionario_titulos.py + clasificar_portafolio.py)
        conn_proy = get_conn()
        df_proy = pd.read_sql_query("SELECT * FROM portafolio_clasificado", conn_proy)
        conn_proy.close()

        if not df_proy.empty:
            df_proy['valor_mercado'] = pd.to_numeric(df_proy['valor_mercado'], errors='coerce').fillna(0)

            # --- Actualizar VM de RF con el motor (vía helper reutilizable) ---
            if resultados['rf_resumen'] and port:
                df_proy, _resumen_rf = _construir_df_proy_rf(df_proy, port, fecha_end)
                print(f"[PROY] RF: {_resumen_rf['actualizados']} títulos actualizados, "
                      f"{_resumen_rf['vencidos']} vencidos eliminados, "
                      f"{_resumen_rf['reinversiones']} reinversiones nuevas")

            # --- Actualizar VM de RV con el motor ---
            if resultados['rv']:
                try:
                    trm_v = float(trm_final)
                    # OPTIMIZACIÓN #1 (dedup condicional RV): la Sección 7 necesita el VM
                    # que produce el motor RV SIN senda_trm y luego ×trm_final para los
                    # internacionales. La Sección 4 ya corrió el motor en `df_rv_res` con
                    # `senda_trm_rv_esc`. Si esa senda es None o PLANA == trm_final, la
                    # corrida de la Sección 4 es numéricamente idéntica (por id_posicion al
                    # último mes) a la 2ª corrida sin senda_trm → se REUTILIZA df_rv_res y
                    # NO se re-ejecuta el motor. En cualquier otro caso (senda TRM no plana)
                    # se CONSERVA la 2ª corrida actual para no cambiar ningún número.
                    def _senda_trm_equiv_trm_final(_senda, _trm_final, _tol=1e-6):
                        if not _senda:
                            return True  # sin senda ⇒ df_rv_res == corrida sin senda_trm
                        try:
                            for _v in _senda.values():
                                _vf = float(_v)
                                _den = max(abs(_vf), abs(_trm_final), 1.0)
                                if abs(_vf - _trm_final) / _den > _tol:
                                    return False
                            return True
                        except Exception:
                            return False

                    _rv_reutilizable = (
                        df_rv_res is not None and not df_rv_res.empty
                        and _senda_trm_equiv_trm_final(senda_trm_rv_esc, trm_v))

                    if _rv_reutilizable:
                        df_rv_full = df_rv_res
                        _rv_rama = "reutilizada (sin re-ejecutar motor)"
                    else:
                        from proyeccion_renta_variable import proyectar_renta_variable as _prv
                        df_rv_full = _prv(
                            fecha_inicio=fecha_ini.strftime('%Y-%m'),
                            fecha_fin=fecha_end.strftime('%Y-%m'),
                            escenarios_rv_local={"base": float(rv_local or 12) / 100},
                            escenarios_rv_intl={"base": float(rv_intl or 8) / 100},
                            tasa_pei=float(rv_pei or 10) / 100,
                            ruta_db=str(SCRIPT_DIR / 'portafolio.db'),
                        )
                        _rv_rama = "doble corrida (senda TRM no plana)"

                    if df_rv_full is not None and not df_rv_full.empty:
                        ultima_fecha_rv = df_rv_full['fecha_proyeccion'].max()
                        df_rv_fin = df_rv_full[df_rv_full['fecha_proyeccion'] == ultima_fecha_rv]
                        _n_rv = 0
                        # OPTIMIZACIÓN #2 (vectorización): map id_posicion→VM en lugar de
                        # iterrows()+máscara O(n²). Para rv_internacional se aplica ×trm_v
                        # (idéntico al bucle original). Las posiciones ausentes conservan su
                        # VM previo vía .fillna(df_proy['valor_mercado']).
                        if 'id_posicion' in df_rv_fin.columns:
                            _vm_rv = df_rv_fin.copy()
                            _tipo_rv = _vm_rv['tipo_rv'] if 'tipo_rv' in _vm_rv.columns else ''
                            _vm_rv['_vm_final'] = _vm_rv['valor_mercado'].where(
                                _tipo_rv != 'rv_internacional',
                                _vm_rv['valor_mercado'] * trm_v)
                            # Normalizar id a str para igualar el match del `==` original
                            _serie_rv = _vm_rv.assign(
                                _idk=_vm_rv['id_posicion'].astype(str)
                            ).set_index('_idk')['_vm_final']
                            if _serie_rv.index.has_duplicates:
                                _serie_rv = _serie_rv.groupby(level=0).last()
                            _idk_proy = df_proy['id_posicion'].astype(str)
                            _n_rv = int(_idk_proy.isin(_serie_rv.index).sum())
                            df_proy['valor_mercado'] = (
                                _idk_proy.map(_serie_rv).fillna(df_proy['valor_mercado']))
                        print(f"[PROY] RV: {len(df_rv_fin)} posiciones del motor, "
                              f"{_n_rv} aplicadas a df_proy — rama {_rv_rama}")
                except Exception as e_rv:
                    print(f"[PROY] Error RV: {e_rv}")

            # --- Actualizar VM de Alternativos con el motor ---
            if resultados['alternativos']:
                try:
                    # Inputs que usaría la 2ª corrida (igual que hoy la Sección 7):
                    _ipc7 = float(ipc_4g or 7.07) / 100
                    _periodos_alt7 = pd.date_range(
                        start=f"{fecha_ini.strftime('%Y-%m')}-01",
                        end=f"{fecha_end.strftime('%Y-%m')}-01", freq="MS")
                    _senda_ipc7 = {p.strftime("%Y-%m-%d"): _ipc7 for p in _periodos_alt7}

                    # OPTIMIZACIÓN #1 (dedup condicional Alt): la Sección 5 ya corrió el
                    # motor en `df_alt`. Sólo es reutilizable si sus inputs efectivos son
                    # idénticos a los de la 2ª corrida (motor determinista ⇒ mismo VM por
                    # id_posicion, Δ=0 ≤ 1e-6): sin senda_trm, spreads por defecto (None),
                    # y senda_ipc plana == ipc_4g. En cualquier otro caso se CONSERVA la
                    # 2ª corrida para no cambiar números.
                    def _dict_equiv(_a, _b, _tol=1e-9):
                        if _a is None or _b is None:
                            return _a is None and _b is None
                        if set(_a.keys()) != set(_b.keys()):
                            return False
                        try:
                            return all(abs(float(_a[k]) - float(_b[k])) <= _tol for k in _a)
                        except Exception:
                            return False

                    _alt_reutilizable = (
                        df_alt is not None and not df_alt.empty
                        and _alt_senda_trm_esc is None
                        and _alt_spreads_esc is None
                        and _dict_equiv(_alt_senda_ipc_esc, _senda_ipc7))

                    if _alt_reutilizable:
                        df_alt_full = df_alt
                        _alt_rama = "reutilizada (sin re-ejecutar motor)"
                    else:
                        from proyeccion_alternativos import proyectar_alternativos as _pa
                        df_alt_full = _pa(
                            fecha_inicio=fecha_ini.strftime('%Y-%m'),
                            fecha_fin=fecha_end.strftime('%Y-%m'),
                            tasa_ea_local=float(ret_alt or 10) / 100,
                            tasa_ea_intl=float(ret_alt_intl or 12) / 100,
                            senda_ipc=_senda_ipc7,
                            spreads_4g=None,  # Dinámico por es_ipc='SI' en la DB
                            ruta_db_portafolio=str(SCRIPT_DIR / 'portafolio.db'),
                        )
                        _alt_rama = "doble corrida (inputs S5 != S7)"

                    if df_alt_full is not None and not df_alt_full.empty:
                        ultima_fecha_alt = df_alt_full['fecha_proyeccion'].max()
                        df_alt_fin = df_alt_full[df_alt_full['fecha_proyeccion'] == ultima_fecha_alt]
                        _n_alt = 0
                        # OPTIMIZACIÓN #2 (vectorización): map id_posicion→VM. Alt no aplica
                        # conversión TRM aquí (igual que el bucle original). Ausentes:
                        # conservan su VM previo.
                        if 'id_posicion' in df_alt_fin.columns:
                            _serie_alt = df_alt_fin.assign(
                                _idk=df_alt_fin['id_posicion'].astype(str)
                            ).set_index('_idk')['valor_mercado']
                            if _serie_alt.index.has_duplicates:
                                _serie_alt = _serie_alt.groupby(level=0).last()
                            _idk_proy = df_proy['id_posicion'].astype(str)
                            _n_alt = int(_idk_proy.isin(_serie_alt.index).sum())
                            df_proy['valor_mercado'] = (
                                _idk_proy.map(_serie_alt).fillna(df_proy['valor_mercado']))
                        print(f"[PROY] Alternativos: {len(df_alt_fin)} posiciones del motor, "
                              f"{_n_alt} aplicadas a df_proy — rama {_alt_rama}")
                except Exception as e_alt:
                    print(f"[PROY] Error Alternativos: {e_alt}")

            # --- Actualizar VM de Derivados con el motor ---
            if resultados['derivados']:
                try:
                    df_deriv = pd.DataFrame(resultados['derivados'])
                    if not df_deriv.empty and 'valor_mercado' in df_deriv.columns:
                        if 'fecha_valoracion' in df_deriv.columns:
                            ultima_fecha_d = df_deriv['fecha_valoracion'].max()
                            df_deriv_fin = df_deriv[df_deriv['fecha_valoracion'] == ultima_fecha_d]
                        else:
                            df_deriv_fin = df_deriv
                        _n_d = 0
                        # OPTIMIZACIÓN #2 (vectorización): map id_posicion→VM (float),
                        # igual que el bucle original. Ausentes conservan su VM previo.
                        if 'id_posicion' in df_deriv_fin.columns:
                            _serie_d = df_deriv_fin.assign(
                                _idk=df_deriv_fin['id_posicion'].astype(str)
                            ).set_index('_idk')['valor_mercado'].astype(float)
                            if _serie_d.index.has_duplicates:
                                _serie_d = _serie_d.groupby(level=0).last()
                            _idk_proy = df_proy['id_posicion'].astype(str)
                            _n_d = int(_idk_proy.isin(_serie_d.index).sum())
                            df_proy['valor_mercado'] = (
                                _idk_proy.map(_serie_d).fillna(df_proy['valor_mercado']))
                        print(f"[PROY] Derivados: {len(df_deriv_fin)} posiciones del motor, "
                              f"{_n_d} aplicadas a df_proy")
                except Exception as e_d:
                    print(f"[PROY] Error Derivados: {e_d}")

            # Excluir colaterales/simultáneas y reclasificar a fecha_end (vía helper).
            df_proy = _finalizar_df_proy(df_proy, fecha_end)

            # Diagnóstico final: comparar VM total motor RF vs VM total df_proy
            vm_motor_total = sum(t.valor_mercado for t in port) if port else 0
            vm_proy_total = df_proy['valor_mercado'].sum()
            print(f"[PROY FINAL] VM motor RF (todos titulos en port): {vm_motor_total/1e9:.2f} MdM")
            print(f"[PROY FINAL] VM df_proy total: {vm_proy_total/1e9:.2f} MdM ({len(df_proy)} posiciones)")
            # Desglose por asset_class
            if 'asset_class_definitivo' in df_proy.columns:
                desglose = df_proy.groupby('asset_class_definitivo')['valor_mercado'].sum().sort_values(ascending=False)
                for ac, vm in desglose.head(10).items():
                    print(f"  [PROY]   {ac}: {vm/1e9:.2f} MdM")
            # Comparar con portafolio actual
            try:
                conn_diag = get_conn()
                df_actual = pd.read_sql_query("SELECT asset_class_definitivo, SUM(valor_mercado) as vm FROM portafolio_clasificado GROUP BY asset_class_definitivo ORDER BY vm DESC", conn_diag)
                conn_diag.close()
                vm_actual_total = df_actual['vm'].sum()
                print(f"[PROY FINAL] VM actual en DB: {vm_actual_total/1e9:.2f} MdM")
                print(f"[PROY FINAL] DELTA (proy - actual): {(vm_proy_total - vm_actual_total)/1e9:.2f} MdM")
            except Exception:
                pass

            resultados['portafolio_proyectado'] = df_proy.to_dict('records')
            print(f"[ESCENARIO] Portafolio proyectado: {len(df_proy)} posiciones")
        else:
            resultados['portafolio_proyectado'] = []

    except Exception as e_proy:
        import traceback as _tbp
        print(f"[ESCENARIO] Error construyendo portafolio proyectado: {e_proy}")
        print(_tbp.format_exc()[-300:])
        resultados['portafolio_proyectado'] = []

    # ─── Mensaje de estado ───
    motores_ok = sum(1 for k in ['rf_resumen', 'derivados', 'simultaneas', 'rv', 'alternativos', 'creditos'] if resultados[k])
    _txt_horizonte = f"Horizonte proyectado: {n_meses} mes(es) ({fecha_ini} → {fecha_end})."
    if errores:
        msg = html.Div([
            html.Span(f"⚠️ Escenario parcial: {motores_ok}/6 motores. ",
                      style={'color': '#fd7e14', 'fontWeight': 'bold'}),
            html.Span(" | ".join(errores), style={'fontSize': '10px', 'color': '#dc3545'}),
            html.Br(),
            html.Span(_txt_horizonte, style={'fontSize': '10px', 'color': '#555'}),
        ])
    else:
        msg = html.Div([
            html.Span(f"✅ Escenario completado: {motores_ok}/6 motores OK. ",
                      style={'color': '#28a745', 'fontWeight': 'bold'}),
            html.Br(),
            html.Span(_txt_horizonte, style={'fontSize': '10px', 'color': '#555'}),
        ])
    return resultados, msg


# =============================================================================
# ORQUESTADOR POR TRAMOS (feature proyeccion-por-tramos)
# =============================================================================

def _sendas_planas_tramo(ruta_db, fecha_pip_str, sub_horizonte):
    """
    Construye las sendas de curva para un tramo. Como el motor aplica los shifts por bps
    directamente sobre el spread individual de cada título (mecanismo principal validado
    con el spread efectivo de cierre), las sendas de curva se mantienen PLANAS en el
    tramo (curva base del PIP replicada por mes); el movimiento del tramo lo aportan los
    shifts_bps que se pasan al motor. Devuelve {} si no hay curvas PIP.
    """
    try:
        from curvas_rendimiento import extraer_curva_base_pip_db, generar_senda_lineal
        curvas_base = extraer_curva_base_pip_db(str(ruta_db), fecha_pip_str)
        if not curvas_base:
            return {}
        sendas = {}
        for nombre, curva in curvas_base.items():
            sendas[nombre] = generar_senda_lineal(curva, curva, max(sub_horizonte, 1))
        return sendas
    except Exception as e:
        print(f"[TRAMOS] No se pudieron construir sendas de tramo: {e}")
        return {}


# ==============================================================================
# ADAPTADORES POR TRAMO DE LOS MOTORES NO-RF (feature proyeccion-por-tramos-multiactivo,
# tarea 5.2). Cada adaptador invoca UN motor no-RF para UN tramo, SIN modificar la firma
# ni la lógica interna del motor (principio rector del design; Req 4, 12.4).
#
# ─────────────────────────────────────────────────────────────────────────────
# CONFIRMACIÓN DE CAPITALIZACIÓN EA (verificado en el código, no asumido)
# ─────────────────────────────────────────────────────────────────────────────
# `proyeccion_renta_variable.proyectar_bloque` y `proyeccion_alternativos.proyectar_bloque`
# convierten la tasa EA a DIARIA con `convertir_ea_a_diaria` = (1+EA)^(1/365)-1 y componen
# por DÍAS REALES entre cortes: `factor = (1+tasa_diaria)^dias`. Al acotar el motor al
# sub-periodo del tramo con `fecha_inicio`/`fecha_fin` ("YYYY-MM"), la suma de días del
# tramo de `m` meses hace que el VM de cierre sea `VM_inicio × (1+EA)^(dias_tramo/365)`,
# es decir ≈ `(1+EA)^(m/12)` (capitalización parcial correcta del tramo).
# ⇒ VÍA ELEGIDA: se pasa la tasa EA TAL CUAL al motor (NO se pre-convierte con
#   `convertir_ea_a_tramo`), para no doble-capitalizar. El tramo parcial (5 meses) cuadra
#   automáticamente porque el motor ya capitaliza por los días reales del sub-periodo.
#   `convertir_ea_a_tramo` queda reservado para mecanismos que reciban un retorno de
#   periodo cerrado (no es el caso de estos motores).
#
# ─────────────────────────────────────────────────────────────────────────────
# VÍA DE ENCADENAMIENTO POR MOTOR (elegida y justificada)
# ─────────────────────────────────────────────────────────────────────────────
# Los motores RV y Alternativos LEEN el portafolio internamente de `portafolio_clasificado`
# (`cargar_portafolio_rv` / `cargar_portafolio_alternativos`) y NO aceptan un DataFrame de
# portafolio por parámetro. Por tanto la vía (a) del design (inyectar el VM de cierre en la
# columna `valor_mercado` del portafolio que se pasa al motor) NO es aplicable a través de
# la firma pública del motor.
# ⇒ VÍA ELEGIDA: OPCIÓN (c) del design — escalar el resultado del motor por posición.
#   Se corre el motor para el tramo desde su VM original de `portafolio_clasificado`, y
#   luego se multiplica la senda de VM (y su PyG) de cada posición por el factor de cierre
#   acumulado `VM_cierre_previo / VM_inicial_original(posicion)`. Es CORRECTO porque el
#   retorno del motor es MULTIPLICATIVO sobre el VM inicial de la posición:
#       VM_motor(t)              = VM_inicial_original × (1+retorno_hasta_t)
#       VM_encadenado(t)         = VM_cierre_previo × (1+retorno_hasta_t)
#                                = VM_motor(t) × [VM_cierre_previo / VM_inicial_original]
#   de modo que el escalado por el factor reconstruye exactamente
#   `VM_cierre(tramo_i) = VM_cierre(tramo_{i-1}) × (1+retorno_tramo_i)` (design "Ecuación de
#   encadenamiento"). En el primer tramo (estado_previo vacío) el factor es 1 (identidad).
#   `escalar_vm_inicial` (que inyecta en un DataFrame de portafolio) se conserva disponible
#   en el módulo de helpers para el motor RF/consumidores que sí acepten portafolio, pero
#   NO se usa aquí porque estos motores no exponen ese punto de entrada.
#
# Créditos NO usa esta ecuación de retorno: encadena por saldo/amortización; se le pasan las
# sendas del tramo y `df_creditos=None` (usa los créditos base del propio motor).
# ==============================================================================

def _factor_encadenamiento_por_posicion(vm_cierre_previo: dict, col_id_valores) -> dict:
    """
    Devuelve `{id_posicion: factor}` para escalar el resultado del motor no-RF por el
    encadenamiento (Opción c). El factor de una posición es
    `VM_cierre_previo(id) / VM_inicial_original(id)`; si no hay cierre previo para la
    posición (primer tramo o posición nueva), el factor es 1.0 (identidad).

    Parámetros
    ----------
    vm_cierre_previo: dict[str, float]
        `{id_posicion: VM_cierre}` del tramo anterior (vacío en el primer tramo).
    col_id_valores: iterable de (id_posicion, vm_inicial_original)
        Pares con el VM inicial ORIGINAL (T0) que el motor leyó para cada posición.
    """
    from proyeccion_tramos_multiactivo import _coerce_float_finito
    factores: dict = {}
    for id_pos, vm_inicial in col_id_valores:
        clave = str(id_pos)
        vm_prev = vm_cierre_previo.get(clave) if vm_cierre_previo else None
        vm_ini = _coerce_float_finito(vm_inicial, default=0.0)
        if vm_prev is None or vm_ini == 0.0:
            factores[clave] = 1.0
        else:
            factores[clave] = _coerce_float_finito(vm_prev, default=vm_ini) / vm_ini
    return factores


def _aplicar_encadenamiento_resultado(df_resultado, vm_cierre_previo: dict, col_id: str):
    """
    Aplica la Opción (c) de encadenamiento sobre el DataFrame de resultado de un motor
    no-RF (RV/Alternativos): escala `valor_mercado`, `pyg_mensual` y `pyg_acumulado` de
    cada posición por su factor `VM_cierre_previo / VM_inicial_original`.

    El VM inicial original de cada posición se toma del primer corte (T0) que el motor
    registra (fila con menor `fecha_proyeccion`), donde `pyg` es 0 y `valor_mercado` es el
    VM inicial leído de `portafolio_clasificado`.

    Devuelve una COPIA escalada; si `vm_cierre_previo` está vacío (primer tramo) o faltan
    columnas, devuelve el resultado sin cambios (identidad).
    """
    if df_resultado is None or len(df_resultado) == 0:
        return df_resultado
    if not vm_cierre_previo:
        return df_resultado

    columnas = set(df_resultado.columns)
    if col_id not in columnas or "valor_mercado" not in columnas or "fecha_proyeccion" not in columnas:
        return df_resultado

    df = df_resultado.copy()

    # VM inicial original por posición: el del primer corte (menor fecha_proyeccion).
    idx_t0 = df.groupby(col_id)["fecha_proyeccion"].idxmin()
    df_t0 = df.loc[idx_t0]
    pares = [(fila[col_id], fila["valor_mercado"]) for _, fila in df_t0.iterrows()]
    factores = _factor_encadenamiento_por_posicion(vm_cierre_previo, pares)

    def _factor(fila):
        return factores.get(str(fila[col_id]), 1.0)

    df["__factor"] = df.apply(_factor, axis=1)
    for col in ("valor_mercado", "pyg_mensual", "pyg_acumulado"):
        if col in columnas:
            df[col] = df[col] * df["__factor"]
    df = df.drop(columns="__factor")
    return df


def _realinear_pyg_enero_tramo(df_resultado, vm_cierre_previo: dict, col_id: str, aplicar: bool):
    """
    Corrige la distribución mensual del P&G en tramos RV/Alternativos con `indice > 0`
    (bugfix `pyg-enero-tramos-rv-alt`; Req 2.1, 2.2, 2.3, 2.4, 3.1, 3.4, 3.6, 3.7).

    En los motores no-RF la rama `if i == 0:` emite una fila de apertura fantasma (primer
    mes del tramo = enero) con `pyg_mensual = 0.0` y `valor_mercado = VM_cierre_previo`, lo
    que desplaza cada P&G mensual un mes tarde. Este post-proceso reancla el VM un casillero
    hacia enero y recomputa `pyg`/`VM`/`pyg_acumulado` sobre las MISMAS filas (no borra ni
    agrega filas), de modo que:

      - `pyg'(ene) = VM_1 − VM_cierre_previo[id]`           (P&G real de enero)
      - `pyg'(m_k) = VM_{k+1} − VM_k`  para `1 ≤ k ≤ n-1`
      - `pyg'(dic) = VM_n − VM_{n-1}`                        (último paso real)
      - `VM'(m_k) = VM_{k+1}` para `k = 0..n-1`, y `VM'(dic) = VM_n` (diciembre intacto)
      - `pyg_acumulado'` = suma acumulada de `pyg'`

    Garantías de preservación (telescoping): `Σ pyg' = VM_n − VM_cierre_previo = Σ pyg` y
    `VM'(dic) = VM_n`, por lo que el total anual del tramo y el VM de cierre de diciembre
    quedan intactos (Req 2.3, 3.6, 3.7), y el conjunto/conteo de meses se preserva (Req 3.9).

    Reutiliza los `valor_mercado` ya calculados por el motor (diferencias de VM); NO recompone
    con tasas, para igualdad numérica. Se redondea a 2 decimales de forma consistente con los
    motores.

    - `aplicar=False` (tramo 0, o `vm_cierre_previo` vacío) ⇒ identidad: devuelve el resultado
      sin cambios (Property 3 y Property 5 por construcción).
    - Por posición: si `id` no está en `vm_cierre_previo` (posición nueva, Req 3.4), esa
      posición se deja como apertura legítima (no se realinea), mismo criterio que
      `_aplicar_encadenamiento_resultado` (factor 1.0).
    """
    if df_resultado is None or len(df_resultado) == 0:
        return df_resultado
    if not aplicar or not vm_cierre_previo:
        return df_resultado

    columnas = set(df_resultado.columns)
    requeridas = {col_id, "valor_mercado", "fecha_proyeccion"}
    if not requeridas.issubset(columnas):
        return df_resultado

    vm_prev_map = vm_cierre_previo or {}
    claves_prev = {str(k) for k in vm_prev_map}
    partes: list = []

    for id_val, grupo in df_resultado.groupby(col_id, sort=False):
        g = grupo.sort_values("fecha_proyeccion").reset_index(drop=True)

        # Posición nueva sin cierre previo: apertura legítima, no se realinea (Req 3.4).
        if str(id_val) not in claves_prev:
            partes.append(g)
            continue

        # Lookup robusto al tipo de clave (str o id original).
        vm_prev_raw = vm_prev_map.get(id_val, vm_prev_map.get(str(id_val)))
        vm_prev = float(vm_prev_raw)

        vms = [float(v) for v in g["valor_mercado"].tolist()]
        n = len(vms) - 1  # índices 0..n

        if n < 1:
            # Serie de un solo corte: nada que realinear.
            partes.append(g)
            continue

        # VM'(m_k) = VM_{k+1} para k=0..n-1 ; VM'(dic) = VM_n (intacto).
        nuevos_vm = [vms[k + 1] if k < n else vms[n] for k in range(len(vms))]

        # pyg' por diferencias de VM consecutivas, anclado en VM_cierre_previo.
        nuevos_pyg = []
        anterior = vm_prev
        for vm_k in nuevos_vm:
            nuevos_pyg.append(round(vm_k - anterior, 2))
            anterior = vm_k

        g = g.copy()
        g["valor_mercado"] = [round(v, 2) for v in nuevos_vm]
        if "pyg_mensual" in columnas:
            g["pyg_mensual"] = nuevos_pyg
        if "pyg_acumulado" in columnas:
            g["pyg_acumulado"] = [
                round(s, 2) for s in pd.Series(nuevos_pyg).cumsum().tolist()
            ]
        partes.append(g)

    return pd.concat(partes, ignore_index=True)


def _correr_rv_tramo(tramo, inputs_tramo, sendas_tramo, estado_previo=None):
    """
    Adaptador por tramo del Motor_RV (`proyectar_renta_variable`) para UN tramo
    (tarea 5.2; Req 4.1, 5.1, 5.2, 6.3).

    - Acota el motor al sub-periodo del tramo con `fecha_inicio`/`fecha_fin` en "YYYY-MM"
      (de `tramo.fecha_inicio`/`tramo.fecha_corte`).
    - Pasa las tasas EA del tramo TAL CUAL (RV local = `ret_acc_local_ea`,
      RV intl = `ret_acc_intl_ea` —retorno de acciones internacionales en moneda origen—,
      PEI = `pei_ea`); el motor capitaliza por los días reales del tramo (ver confirmación
      EA arriba) y convierte el retorno internacional a COP con `senda_trm`.
    - Usa `sendas_tramo.senda_trm` (TRM interpolada del tramo) para el efecto cambiario.
    - ENCADENA vía Opción (c): tras la corrida, escala el resultado por posición con el
      VM de cierre del tramo anterior (`estado_previo.vm_rv_por_posicion`).

    NO modifica la firma ni la lógica del motor.

    Retorna
    -------
    pandas.DataFrame
        Resultado del motor RV para el tramo, YA encadenado (escalado).
    """
    from proyeccion_renta_variable import proyectar_renta_variable

    fecha_inicio = tramo.fecha_inicio.strftime("%Y-%m")
    fecha_fin = tramo.fecha_corte.strftime("%Y-%m")

    tasa_local = float(inputs_tramo.ret_acc_local_ea)
    tasa_intl = float(inputs_tramo.ret_acc_intl_ea)  # RV internacional (acciones), no alt intl
    tasa_pei = float(inputs_tramo.pei_ea)

    df_resultado = proyectar_renta_variable(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        escenarios_rv_local={"base": tasa_local},
        escenarios_rv_intl={"base": tasa_intl},
        tasa_pei=tasa_pei,
        ruta_db=str(DB_PATH),
        senda_trm=sendas_tramo.senda_trm,
        # Ancla USD = fuente de verdad. getattr retrocompatible: si el objeto de sendas no
        # expone el campo (p. ej. dobles de prueba previos al fix), cae a 0.0 -> el motor
        # usa senda[primera_fecha] como antes (default documentado de proyectar_bloque).
        trm_inicio_ancla=getattr(sendas_tramo, "trm_inicio_real", 0.0),
    )

    vm_previo = getattr(estado_previo, "vm_rv_por_posicion", {}) if estado_previo else {}
    # El Motor_RV identifica cada posición por su `id_posicion` único (igual que
    # Alternativos). El factor VM_cierre_previo / VM_inicial_original se calcula y aplica por
    # id_posicion, de modo que cada posición use su propio denominador (no una base agregada
    # por especie).
    df_encadenado = _aplicar_encadenamiento_resultado(df_resultado, vm_previo, col_id="id_posicion")
    # Bugfix pyg-enero-tramos-rv-alt: realinea la distribución mensual SOLO para tramos
    # indice > 0 (elimina la fila fantasma de enero), tras el encadenamiento. Tramo 0 =>
    # identidad.
    return _realinear_pyg_enero_tramo(
        df_encadenado, vm_previo, col_id="id_posicion", aplicar=(tramo.indice > 0)
    )


def _correr_alt_tramo(tramo, inputs_tramo, sendas_tramo, estado_previo=None):
    """
    Adaptador por tramo del Motor_Alternativos (`proyectar_alternativos`) para UN tramo
    (tarea 5.2; Req 4.1, 5.1, 5.2, 6.3, 7.3).

    - Acota el motor al sub-periodo del tramo con `fecha_inicio`/`fecha_fin` "YYYY-MM".
    - Pasa las tasas EA del tramo TAL CUAL (local = `ret_alt_local_ea`,
      intl = `ret_alt_intl_ea`); el motor capitaliza por días reales del tramo.
    - `ret_alt_ipc_bps` (spread sobre IPC para FCP 4G, en bps) se traduce a `spreads_4g`:
      spread decimal = bps / 10000, aplicado a TODAS las posiciones IPC del portafolio.
    - Usa `sendas_tramo.senda_ipc` (IPC del tramo para FCP 4G) y `sendas_tramo.senda_trm`
      (efecto cambiario de intl).
    - ENCADENA vía Opción (c) usando `estado_previo.vm_alt_por_posicion`.

    NO modifica la firma ni la lógica del motor.
    """
    from proyeccion_alternativos import proyectar_alternativos, cargar_portafolio_alternativos

    fecha_inicio = tramo.fecha_inicio.strftime("%Y-%m")
    fecha_fin = tramo.fecha_corte.strftime("%Y-%m")

    tasa_local = float(inputs_tramo.ret_alt_local_ea)
    tasa_intl = float(inputs_tramo.ret_alt_intl_ea)

    # ret_alt_ipc_bps → spread decimal sobre IPC para los FCP 4G (indexados a IPC).
    spread_ipc = float(inputs_tramo.ret_alt_ipc_bps) / 10000.0
    spreads_4g: dict = {}
    try:
        port_alt = cargar_portafolio_alternativos(str(DB_PATH))
        if port_alt is not None and not port_alt.empty and "es_ipc" in port_alt.columns:
            ids_ipc = port_alt[port_alt["es_ipc"] == "SI"]["id_posicion"].values
            spreads_4g = {int(id_pos): spread_ipc for id_pos in ids_ipc}
    except Exception as e:
        # Sin acceso al portafolio no se puede mapear el spread por id; se deja al motor
        # aplicar su default documentado (SPREAD_IPC_DEFAULT). No se vuelca detalle sensible.
        print(f"[TRAMOS-ALT] No se pudieron mapear spreads 4G del tramo: {type(e).__name__}")
        spreads_4g = {}

    df_resultado = proyectar_alternativos(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        tasa_ea_local=tasa_local,
        tasa_ea_intl=tasa_intl,
        ruta_db_portafolio=str(DB_PATH),
        senda_ipc=sendas_tramo.senda_ipc,
        spreads_4g=spreads_4g,
        senda_trm=sendas_tramo.senda_trm,
        # Ancla USD = fuente de verdad. getattr retrocompatible: si el objeto de sendas no
        # expone el campo (p. ej. dobles de prueba previos al fix), cae a 0.0 -> el motor
        # usa senda[primera_fecha] como antes (default documentado de proyectar_bloque).
        trm_inicio_ancla=getattr(sendas_tramo, "trm_inicio_real", 0.0),
    )

    vm_previo = getattr(estado_previo, "vm_alt_por_posicion", {}) if estado_previo else {}
    # El Motor_Alternativos identifica cada posición por 'id_posicion'.
    df_encadenado = _aplicar_encadenamiento_resultado(df_resultado, vm_previo, col_id="id_posicion")
    # Bugfix pyg-enero-tramos-rv-alt: realinea la distribución mensual SOLO para tramos
    # indice > 0 (cubre proyectar_bloque y proyectar_bloque_ipc_spread sobre el DataFrame
    # unificado). Tramo 0 => identidad.
    return _realinear_pyg_enero_tramo(
        df_encadenado, vm_previo, col_id="id_posicion", aplicar=(tramo.indice > 0)
    )


def _correr_creditos_tramo(tramo, inputs_tramo, sendas_tramo, estado_previo=None):
    """
    Adaptador por tramo del Motor_Creditos (`proyectar_creditos_contratados`) para UN
    tramo (tarea 5.2; Req 4.1, 7.3).

    - El motor sólo recibe `fecha_inicio` (proyecta cada crédito hasta su vencimiento);
      se le pasa el inicio del tramo en "YYYY-MM".
    - Construye los DataFrames de senda que el motor espera (columna 'fecha' + la columna
      del indicador) desde `sendas_tramo`, con las claves de fecha alineadas a la
      convención del motor de créditos: meses de inicio de mes ("YYYY-MM-01", `freq='MS'`),
      que es la clave con la que `generar_periodos_credito` construye sus periodos y con la
      que hace el lookup de las sendas. Las sendas del tramo son PLANAS al nivel del tramo
      (IPC=`inflacion_ea`, IBR≈`repo_ea`, SOFR=0.0) y la TRM se toma del nivel de cierre
      del tramo (constante en la senda de créditos del tramo).
    - `df_creditos=None`: el motor usa su set de créditos base (no se inyecta portafolio;
      la firma se respeta). El encadenamiento de créditos se realiza por saldo dentro del
      propio motor; `estado_previo` se acepta por simetría de firma pero no se escala el
      resultado (los créditos no siguen la ecuación multiplicativa de retorno).

    NO modifica la firma ni la lógica del motor.
    """
    from proyeccion_creditos import proyectar_creditos_contratados

    fecha_inicio_mm = tramo.fecha_inicio.strftime("%Y-%m")

    # Cortes de INICIO de mes que cubren el tramo, para que las claves de las sendas
    # coincidan con las que arma `generar_periodos_credito` (freq='MS' → "YYYY-MM-01").
    ini_ms = pd.Timestamp(tramo.fecha_inicio).to_period("M").to_timestamp()
    fin_ms = pd.Timestamp(tramo.fecha_corte).to_period("M").to_timestamp()
    periodos_ms = pd.date_range(start=ini_ms, end=fin_ms, freq="MS")
    fechas = [p.strftime("%Y-%m-%d") for p in periodos_ms]

    ipc = float(inputs_tramo.inflacion_ea)
    ibr = float(inputs_tramo.repo_ea)  # supuesto documentado: IBR ≈ repo del tramo
    trm_cierre = float(inputs_tramo.trm_cierre)

    df_senda_ipc = pd.DataFrame({"fecha": fechas, "ipc": [ipc] * len(fechas)})
    df_senda_ibr = pd.DataFrame({"fecha": fechas, "ibr": [ibr] * len(fechas)})
    df_senda_sofr = pd.DataFrame({"fecha": fechas, "sofr": [0.0] * len(fechas)})
    df_senda_trm = pd.DataFrame({"fecha": fechas, "trm": [trm_cierre] * len(fechas)})

    df_resultado = proyectar_creditos_contratados(
        fecha_inicio=fecha_inicio_mm,
        df_creditos=None,
        df_senda_ipc=df_senda_ipc,
        df_senda_ibr=df_senda_ibr,
        df_senda_sofr=df_senda_sofr,
        df_senda_trm=df_senda_trm,
    )
    return df_resultado


# Nodos estándar (en días) para el shift paralelo de las curvas TES COP/UVR que consume
# el Motor_Derivados vía `delta_tes_cop`/`delta_tes_uvr` (mismo set que el modo punta a
# punta, ~línea 6720). Constante controlada por el código (no entrada externa).
_NODOS_TES_DERIVADOS = (30, 90, 180, 365, 730, 1095, 1825, 2555, 3650)


def _correr_derivados_tramo(tramo, inputs_tramo, sendas_tramo, conn,
                            metodo_deriv=1, metodo_params_extra=None,
                            plano_inicial=None, fecha_mercado=None,
                            trm_inicio_real=None, delta_curva_base=None):
    """
    Adaptador por tramo del Motor_Derivados (`valorar_portafolio_derivados`) para UN
    tramo con ENCADENAMIENTO REAL del plano de posiciones (Opción B; tarea 10.6;
    Req 13.1, 13.2, 13.3, 13.5, 13.8).

    El plano de posiciones VIVAS al cierre de un tramo (con sus renovaciones) es el plano
    de INICIO del tramo siguiente —análogo al snapshot RF—. Para el tramo i>0 se pasa el
    `plano_inicial` (recuperado de las tablas `_proy` del corte anterior) como
    `posiciones_iniciales` del motor; para el tramo 0 se pasa `None` y el motor lee el
    plano base real de `hist_*`. Siempre se pide `devolver_plano_cierre=True` para capturar
    el plano vivo al `fecha_fin` del tramo y persistirlo como inicio del siguiente.

    Parámetros clave:
      - fecha_corte = inicio del tramo, fecha_fin = Fecha_Corte_Tramo ('YYYY-MM-DD').
      - nivel_actual = TRM de INICIO del tramo (primer punto de la senda del tramo).
      - senda_trm = lista de niveles de la senda interpolada del tramo (orden cronológico).
      - metodo_params: delta_tes_cop/delta_tes_uvr (shift paralelo bps/10000 por nodo),
        delta_fed_bps = inputs.delta_fed_bps, delta_10t_bps = inputs.delta_tesoros10y_bps.
      - posiciones_iniciales = plano_inicial (None en el tramo 0 → lee hist_* reales).
      - delta_curva_base = delta de curva ACUMULADO de los tramos previos (0..i-1), que se
        pasa al motor como offset BASE de la curva de valoración (D3; Req 2.9-2.11). None en
        el tramo 0 → comportamiento actual; para i>0 es `estado_previo.delta_curva_acumulado`.

    Retorna
    -------
    dict
        {'pyg': [...registros calcular_pyg...], 'vm_cierre': {id_posicion: VM_cierre},
         'plano_cierre': {'fwds','opts','swaps'}}.
        Si el módulo de derivados no está disponible, devuelve
        {'pyg': [], 'vm_cierre': {}, 'plano_cierre': None}.
    """
    if not DERIVADOS_DISPONIBLE:
        # Aviso genérico; no aborta los demás activos (Req 13.6, Error Handling).
        print("[TRAMOS-DERIV] Módulo de derivados no disponible; se omite el tramo.")
        return {'pyg': [], 'vm_cierre': {}, 'plano_cierre': None}

    fecha_corte = tramo.fecha_inicio.strftime("%Y-%m-%d")
    fecha_fin = tramo.fecha_corte.strftime("%Y-%m-%d")

    # Senda TRM del tramo como lista ordenada cronológicamente (el motor la consume por
    # posición: [hoy, mes1, ..., mesN]). El primer punto es el nivel de inicio del tramo.
    senda_items = sorted(sendas_tramo.senda_trm.items())
    senda_trm = [float(v) for _, v in senda_items]
    # Spot m=0 = TRM real de arranque del tramo (fuente de verdad), consistente con la
    # fecha de las curvas de mercado. Fallback retrocompatible: primer nodo de la senda.
    if trm_inicio_real is not None:
        nivel_actual = float(trm_inicio_real)
    else:
        nivel_actual = senda_trm[0] if senda_trm else float(inputs_tramo.trm_cierre)

    # ── Deltas de curva del tramo (Req 13.5) — mismo patrón que punta a punta ──
    metodo_params: dict = {}
    if metodo_params_extra:
        metodo_params.update(metodo_params_extra)
    mov_cop = float(getattr(inputs_tramo, "mov_tes_cop_bps", 0.0) or 0.0)
    mov_uvr = float(getattr(inputs_tramo, "mov_tes_uvr_bps", 0.0) or 0.0)
    if mov_cop != 0.0:
        metodo_params['delta_tes_cop'] = {n: mov_cop / 10000.0 for n in _NODOS_TES_DERIVADOS}
    if mov_uvr != 0.0:
        metodo_params['delta_tes_uvr'] = {n: mov_uvr / 10000.0 for n in _NODOS_TES_DERIVADOS}
    delta_fed = float(getattr(inputs_tramo, "delta_fed_bps", 0.0) or 0.0)
    delta_10t = float(getattr(inputs_tramo, "delta_tesoros10y_bps", 0.0) or 0.0)
    if delta_fed != 0.0 or delta_10t != 0.0:
        metodo_params['delta_fed_bps'] = delta_fed
        metodo_params['delta_10t_bps'] = delta_10t

    # activos_usd / portafolio_pesos desde portafolio_clasificado (parametrizado), igual
    # que el modo punta a punta.
    activos_usd, port_pesos = 0.0, 0.0
    try:
        df_port = pd.read_sql_query(
            "SELECT moneda, valor_mercado, clase_origen FROM portafolio_clasificado", conn)
        if not df_port.empty:
            df_port['valor_mercado'] = pd.to_numeric(df_port['valor_mercado'], errors='coerce').fillna(0)
            df_tit = (df_port[df_port['clase_origen'] == 'TITULO']
                      if 'clase_origen' in df_port.columns else df_port)
            mask_usd = ~df_tit['moneda'].isin(['COP', 'UVR'])
            if nivel_actual:
                activos_usd = float(df_tit.loc[mask_usd, 'valor_mercado'].sum()) / nivel_actual
            port_pesos = float(df_tit.loc[df_tit['moneda'].isin(['COP', 'UVR']), 'valor_mercado'].sum())
    except Exception as e:
        print(f"[TRAMOS-DERIV] No se pudo leer portafolio_clasificado: {type(e).__name__}")

    # Plano de inicio del tramo: inyectado (tramo i>0, encadenamiento Opción B) o None
    # (tramo 0 → el motor lee hist_* reales). Solo se inyecta si trae forwards y opciones.
    _pos_ini = None
    if plano_inicial and isinstance(plano_inicial, dict):
        _fw = plano_inicial.get('fwds')
        _op = plano_inicial.get('opts')
        if _fw is not None and len(_fw) and _op is not None and len(_op):
            _pos_ini = {'fwds': _fw, 'opts': _op, 'swaps': plano_inicial.get('swaps')}

    res_deriv = valorar_portafolio_derivados(
        fecha_corte=fecha_corte, fecha_fin=fecha_fin,
        metodo=int(metodo_deriv or 1), nivel_actual=nivel_actual,
        activos_usd=activos_usd, portafolio_pesos=port_pesos,
        senda_trm=senda_trm if senda_trm else None,
        metodo_params=metodo_params if metodo_params else None,
        persistir_auditoria=False,
        posiciones_iniciales=_pos_ini,     # Opción B: plano de cierre del tramo anterior
        devolver_plano_cierre=True,        # capturar plano vivo al fin del tramo
        fecha_mercado=fecha_mercado,       # insumos de mercado desde la fecha base real
        delta_curva_base=delta_curva_base, # D3: delta de curva acumulado de tramos previos
    )
    plano_cierre = res_deriv.get('plano_cierre')
    pyg_deriv = calcular_pyg(res_deriv)

    # VM de cierre por posición: último corte de cada DataFrame de posiciones del tramo,
    # para reflejarlo en el Snapshot_Completo_Tramo (Req 14.7). Se agregan opciones,
    # forwards y swaps (los 'activos' USD ya se reflejan en el snapshot RF/otros).
    vm_cierre: dict = {}
    for _tipo in ('opciones', 'forwards', 'swaps'):
        _df = res_deriv.get(_tipo)
        if _df is None or getattr(_df, 'empty', True):
            continue
        try:
            _dfc = _df.copy()
            if 'fecha_valoracion' in _dfc.columns:
                _ult = _dfc['fecha_valoracion'].max()
                _dfc = _dfc[_dfc['fecha_valoracion'] == _ult]
            _col_vm = ('valor_mercado' if 'valor_mercado' in _dfc.columns
                       else ('valoracion' if 'valoracion' in _dfc.columns else None))
            _col_id = 'id_posicion' if 'id_posicion' in _dfc.columns else None
            if _col_vm and _col_id:
                for _, _row in _dfc.iterrows():
                    vm_cierre[f"{_tipo}:{_row[_col_id]}"] = float(_row.get(_col_vm, 0) or 0)
        except Exception:
            # No es crítico para el PyG; el snapshot de derivados es informativo.
            pass

    # Detalle COMPLETO por tipo de derivado (clave nueva, retrocompatible). Se vuelcan
    # TODAS las columnas y TODOS los cortes del DataFrame del motor (no solo el cierre),
    # para habilitar el detalle por posición × mes en el Excel por tramos y la
    # reconciliación de P&G de swaps con el ResumenPyG (D1). Para swaps esto incluye
    # id_posicion, fecha_valoracion, vpn_der_calc, vpn_obl_calc, valoracion,
    # cupones_der_acum_cop, cupones_obl_acum_cop, nominal_der, nominal_obl, moneda_der,
    # moneda_obl, especie, etc.; para forwards/opciones sus columnas de valoración y,
    # si existe, 'liquidacion'. Falla-seguro por tipo: un tipo ausente o vacío se omite.
    detalle: dict = {}
    for _tipo in ('swaps', 'forwards', 'opciones'):
        try:
            _df = res_deriv.get(_tipo)
            if _df is None or getattr(_df, 'empty', True):
                continue
            detalle[_tipo] = _df.to_dict('records')
        except Exception:
            # No es crítico para el PyG; el detalle de derivados es informativo.
            pass

    return {'pyg': pyg_deriv.to_dict('records'), 'vm_cierre': vm_cierre,
            'plano_cierre': plano_cierre, 'detalle': detalle}


def _barra_progreso_tramo(pct, tramo_num, n_tramos, fecha_corte):
    """
    Componente de barra de progreso para el modo por tramos (opción A).

    Muestra una barra horizontal rellena al `pct`% más el texto
    "Tramo X de N (cierre …) — P%". Se usa desde `set_progress` del background callback,
    que lo enruta al `Output('escenario-progreso', 'children')`.
    """
    pct = max(0, min(int(pct), 100))
    return html.Div([
        html.Div(
            f"⏳ Tramo {tramo_num} de {n_tramos} (cierre {fecha_corte}) — {pct}%",
            style={'fontSize': '11px', 'color': '#0d6efd', 'marginBottom': '4px',
                   'fontWeight': '600'}),
        # Riel de la barra.
        html.Div(
            html.Div(style={
                'width': f'{pct}%', 'height': '10px', 'backgroundColor': '#0d6efd',
                'borderRadius': '5px', 'transition': 'width 0.3s ease'}),
            style={'width': '100%', 'maxWidth': '360px', 'height': '10px',
                   'backgroundColor': '#e9ecef', 'borderRadius': '5px',
                   'margin': '0 auto', 'overflow': 'hidden'}),
    ])


def _persistir_detalle_informe_tramo(conn, registro: dict):
    """[BATCH/INFORME - Opcion 1, feature informe-portafolio-por-tramo-batch]
    Persiste el detalle P&G/ORI de UN tramo como JSON en `informe_pyg_por_tramo` (una fila
    por corte, idempotente por fecha_corte). Tabla INFORMATIVA, independiente de la
    valoracion y del encadenamiento.

    DEUDA TECNICA (spec formal): esquema JSON provisional para desbloquear el informe
    nocturno. El diseno definitivo debe modelar columnas tipadas. Ver
    `.kiro/specs/informe-portafolio-por-tramo-batch/`.
    """
    import json as _json
    conn.execute(
        "CREATE TABLE IF NOT EXISTS informe_pyg_por_tramo ("
        "fecha_corte TEXT PRIMARY KEY, indice INTEGER, payload_json TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO informe_pyg_por_tramo (fecha_corte, indice, payload_json) "
        "VALUES (?, ?, ?)",
        (str(registro.get("fecha_corte")), int(registro.get("indice", -1)),
         _json.dumps(registro, default=str)),
    )
    conn.commit()


def _ejecutar_escenario_por_tramos(
    fecha_inicio, fecha_fin,
    shifts_por_tramo,
    periodicidad_por_tramo=None,
    variacion_anual_por_tramo=None,
    trm_final=None, uvr_t0=None, uvr_cierre=None,
    store_senda_inf=None, repo_ini=None, repo_fin=None,
    ipc_vigente=None,
    set_progress=None,
    inputs_por_tramo=None,
    metodo_deriv=1,
):
    """
    Orquesta la proyección por tramos (feature proyeccion-por-tramos / -multiactivo).

    Encadena corridas anuales del motor: el cierre de un tramo es el t0 del siguiente.

    Multiactivo (tarea 5.3/5.4/5.8, feature proyeccion-por-tramos-multiactivo):
      Cuando se recibe `inputs_por_tramo` (lista de dicts/valores por tramo, uno por
      fin de año), el orquestador —tras la corrida RF de cada tramo— construye las
      sendas del tramo (`construir_sendas_tramo`), invoca los tres motores no-RF
      (RV, Alternativos, Créditos) con el `EstadoEncadenado` del tramo anterior,
      propaga `senda_repo`/`senda_inflacion` y la TRM del tramo al motor RF, actualiza
      el `EstadoEncadenado` del siguiente tramo con el VM de cierre de RV/Alt, acumula
      la descomposición del efecto moneda por activo internacional y consolida los
      resultados del ÚLTIMO tramo en el store (claves `rv`, `alternativos`, `creditos`,
      `efecto_moneda`).

      RETROCOMPATIBILIDAD: si `inputs_por_tramo` es None (o vacío), el orquestador se
      comporta EXACTAMENTE como antes (solo RF, sin efecto moneda ni claves no-RF), con
      la misma TRM global `_trm_t0`/`_trm_final` y las sendas planas actuales.
    Por cada tramo:
      1. Carga el portafolio de inicio: tramo 0 = portafolio_clasificado real
         (vía cargar_portafolio_proyeccion); tramo i>0 = snapshot de cierre del tramo
         anterior (vía cargar_snapshot_proy + cargar_titulos_desde_snapshot), de modo que
         el spread base sea el spread EFECTIVO de cierre (encadenamiento incremental).
      2. Deriva el mapa de reservas del tramo: mensual (cargar_mapa_reservas) o anual
         (prorratear_reservas_anual).
      3. Corre proyectar_portafolio con el sub_horizonte y los shifts DE ESE TRAMO, e
         inyecta el mapa_reservas del tramo.
      4. Construye el df_proy del tramo (_construir_df_proy_tramo) y lo persiste como
         snapshot en portafolio_clasificado_proy con su fecha_corte.
    Expone el df_proy del ÚLTIMO tramo al store (clave 'portafolio_proyectado').

    Manejo de errores por tramo: si un tramo falla, detiene el encadenamiento y devuelve
    un mensaje que identifica la fecha_corte del tramo fallido, sin exponer resultado de
    fin de horizonte incompleto ni datos sensibles.

    Returns:
        (resultados: dict, msg) — igual contrato que _ejecutar_escenario.
    """
    from dateutil.relativedelta import relativedelta as _rd
    from datetime import date as _date
    import proyeccion_tramos as ptr
    from motor_proyeccion import cargar_portafolio_proyeccion, proyectar_portafolio
    from compras_ventas_reservas import cargar_mapa_reservas, cargar_variacion_anual_por_ano
    from proyeccion_tramos_multiactivo import (
        InputsPorTramo, EstadoEncadenado, construir_sendas_tramo,
        extraer_vm_cierre, calcular_efecto_moneda_tramo, _extremos_senda_trm,
        interpolar_nivel_tramo, CronometroTramos, acumular_delta_curva,
    )

    # Instrumentación de tiempos por motor y tramo (Fix 3, Req 17.3). Medir antes de
    # optimizar: el resumen se loguea al final de la corrida (stdout del worker).
    _crono = CronometroTramos()

    fecha_ini = pd.to_datetime(fecha_inicio).date() if fecha_inicio else _date.today()
    fecha_end = pd.to_datetime(fecha_fin).date() if fecha_fin else fecha_ini + timedelta(days=365)

    # 1. Trocear el horizonte.
    try:
        tramos = ptr.calcular_tramos(fecha_ini, fecha_end)
    except ptr.HorizonteInvalidoError:
        return (None, html.Span("⚠️ El horizonte debe ser de al menos un mes para el modo por tramos.",
                                style={'color': '#e65100'}))

    n_tramos = len(tramos)
    shifts_por_tramo = shifts_por_tramo or []
    periodicidad_por_tramo = periodicidad_por_tramo or []
    variacion_anual_por_tramo = variacion_anual_por_tramo or []

    # ── Multiactivo (tarea 5.3): derivar un InputsPorTramo por tramo si llegan inputs. ──
    # Si `inputs_por_tramo` es None/vacío ⇒ modo RF-only retrocompatible (multiactivo=False).
    multiactivo = bool(inputs_por_tramo)
    inputs_tramo_objs: list = []
    if multiactivo:
        inputs_por_tramo = list(inputs_por_tramo)
        for i in range(n_tramos):
            crudo = inputs_por_tramo[i] if i < len(inputs_por_tramo) else {}
            if isinstance(crudo, InputsPorTramo):
                inputs_tramo_objs.append(crudo)
            elif isinstance(crudo, dict):
                # `from_valores` coerciona/valida cada campo (entrada NO confiable).
                inputs_tramo_objs.append(InputsPorTramo.from_valores(**crudo))
            else:
                # Valor no reconocido para el tramo → defaults documentados.
                inputs_tramo_objs.append(InputsPorTramo.from_valores())

    # Conectar los Salto_Anual derivados del insumo de reservas a los tramos (Req 8):
    # se construye la variación anual por tramo asignando a cada tramo la variación
    # anual del año de su fecha_corte (o {} cuando ese año no tiene salto anual → Δ=0).
    # `calcular_tramos` sigue delimitando los tramos (Req 8.4, 9); no se reimplementa.
    try:
        variacion_anual_por_ano = cargar_variacion_anual_por_ano(str(DB_PATH))
    except Exception:
        variacion_anual_por_ano = {}
    if not variacion_anual_por_tramo:
        variacion_anual_por_tramo = [
            variacion_anual_por_ano.get(tramo.fecha_corte.year, {})
            for tramo in tramos
        ]

    # Política de años sin columna en el insumo (Req 10): los años del horizonte sin
    # variación de reserva usan Δ=0. Se enumeran en una nota informativa (no bloqueante).
    #
    # IMPORTANTE (fix falso positivo): la variación de reservas puede venir por DOS rutas:
    #   - ANUAL (`variacion_anual_por_ano`): saltos dic-a-dic (tipo_salto='anual').
    #   - MENSUAL (`cargar_mapa_reservas`): deltas mensuales por `Mes_Caído`. Es la ruta
    #     que usa el insumo con `Encabezado_Fecha` + Fecha_Corte (prorrateo mensual del
    #     Tramo_Parcial / Tramo_Anual; feature lectura-reservas-encabezados-fecha).
    # Antes el aviso miraba SOLO la ruta anual, por lo que daba un FALSO POSITIVO ("Δ=0")
    # cuando las reservas estaban en la ruta mensual (que el motor SÍ aplica cuando la
    # periodicidad del tramo es 'mensual'). Ahora un año se considera "sin columna" solo si
    # NO tiene variación en NINGUNA de las dos rutas.
    _anos_horizonte: list[int] = []
    for _tramo in tramos:
        _ano = _tramo.fecha_corte.year
        if _ano not in _anos_horizonte:
            _anos_horizonte.append(_ano)
    # Años con variación por la ruta MENSUAL (mapa de reservas anclado a cada tramo).
    _anos_con_mensual: set[int] = set()
    try:
        for _tramo in tramos:
            _mapa_t = cargar_mapa_reservas(str(DB_PATH), _tramo.fecha_inicio, _tramo.sub_horizonte)
            if _mapa_t and any(v for v in _mapa_t.values()):
                _anos_con_mensual.add(_tramo.fecha_corte.year)
    except Exception:
        pass
    anos_sin_columna = [
        _ano for _ano in _anos_horizonte
        if not variacion_anual_por_ano.get(_ano) and _ano not in _anos_con_mensual
    ]

    _fecha_pip_str = fecha_ini.strftime('%Y-%m-%d')
    # TRM t0 = TRM implícita REAL del 597 para la fecha de corte. Principio del proyecto:
    # "sin defaults hardcodeados". Antes se usaba `trm_final or 3900.0` como fallback, lo
    # cual mezclaba el pronóstico (trm_final) y un hardcode (3900) con la TRM actual; se
    # eliminó. Si no hay TRM implícita para la fecha (no se cargó el 597 de esa fecha), se
    # aborta ANTES del loop de tramos con un error verificable, tanto en la ruta multiactivo
    # como en la ruta RF-only (inputs_por_tramo=None): sin TRM real no se puede proyectar
    # correctamente el componente cambiario.
    _trm_t0_raw = obtener_trm_para(_fecha_pip_str)
    if _trm_t0_raw is None:
        return (None, html.Span(
            "❌ No hay TRM implícita para la fecha de corte; carga el informe 597 de esa fecha.",
            style={'color': 'red'}))
    _trm_t0 = float(_trm_t0_raw)
    # _trm_final es el fallback de trm_cierre del tramo SOLO en la ruta RF-only
    # retrocompatible (multiactivo=False). El hardcode 3900 se eliminó por ser un default
    # hardcodeado; ahora, si no hay pronóstico (trm_final), cae a la TRM t0 real.
    _trm_final = float(trm_final or _trm_t0)
    # UVR t0 = UVR vigente REAL. Principio del proyecto: "sin defaults hardcodeados"
    # (antes `float(uvr_t0) if uvr_t0 else 411.0`, se eliminó el hardcode 411.0). En modo
    # por tramos la UVR t0 se calcula AUTOMÁTICAMENTE (igual que la TRM): si el llamador no
    # la pasa, se resuelve con `obtener_uvr_para` para la fecha de corte. Solo se aborta si
    # tampoco hay UVR automática disponible. `_uvr_t0` es el nivel de inicio del PRIMER
    # tramo (análogo a `_trm_t0`).
    _uvr_t0_raw = uvr_t0 if uvr_t0 else obtener_uvr_para(_fecha_pip_str)
    if not _uvr_t0_raw:
        return (None, html.Span(
            "❌ No hay UVR t0 para la fecha de corte; carga los datos (TUVT) de esa fecha.",
            style={'color': 'red'}))
    _uvr_t0 = float(_uvr_t0_raw)

    conn = get_conn()
    try:
        ptr.asegurar_tabla_portafolio_proy(conn)
        ptr.limpiar_snapshots_proy(conn)  # reemplazar corrida previa
        # Detalle informativo por tramo (informe_pyg_por_tramo): vaciar los cortes de la
        # corrida previa. Esta tabla se escribe con INSERT OR REPLACE por fecha_corte, así
        # que NO se auto-reduce: sin esta limpieza, una corrida previa con más tramos deja
        # cortes huérfanos que el Excel reconstruye como hojas fantasma (p. ej. 2 tramos
        # mostrados como 3). Falla-seguro si la tabla aún no existe (primera corrida).
        try:
            ptr.limpiar_informe_pyg_por_tramo(conn)
        except Exception as _e_li:
            print(f"[TRAMOS] No se pudo limpiar informe_pyg_por_tramo: {type(_e_li).__name__}")
        # Encadenamiento del plano de derivados (Opción B, Req 13): limpiar los planos
        # _proy de la corrida previa antes de escribir los nuevos.
        try:
            ptr.limpiar_planos_derivados_proy(conn)
        except Exception as _e_lp:
            print(f"[TRAMOS] No se pudieron limpiar planos de derivados _proy: {type(_e_lp).__name__}")

        df_proy_final = None
        # ── Estado de encadenamiento no-RF (multiactivo). Vacío = primer tramo. ──
        estado_previo = None                 # EstadoEncadenado del tramo anterior (None en el 1º)
        df_rv_ultimo = None                  # resultado RV del último tramo (para el store)
        df_alt_ultimo = None                 # resultado Alt del último tramo
        df_cred_ultimo = None                # resultado Créditos del último tramo
        deriv_ultimo = None                  # resultado Derivados del último tramo (Req 13.6)
        efectos_moneda: list = []            # descomposición efecto moneda por activo/tramo
        # [BATCH/INFORME - Opcion 1, feature informe-portafolio-por-tramo-batch]
        # Acumula, POR TRAMO, el detalle P&G/ORI por titulo de RF y los datos no-RF, para
        # el runner batch nocturno. Informativo (no afecta encadenamiento ni valoracion).
        detalle_por_tramo: list = []
        # [Tarea 4.3] Serie RF mensual contable CONTINUA a traves de TODOS los tramos.
        # Se captura el retorno de proyectar_portafolio por tramo (lista ResultadoMes, que
        # antes se descartaba), se construye la serie contable del tramo con el helper
        # compartido (scripts/rf_contable_mensual.construir_rf_mensual_contable, Tarea 4.1)
        # y se concatena. Regla "el cierre de un anio es el T0 del siguiente": cada tramo
        # arranca el 1-ene y el fechado corregido (Tarea 4.2) emite ene..dic, de modo que
        # el P&G de enero queda = VM(fin enero) - VM(cierre 31-dic del anio anterior) por el
        # encadenamiento del portafolio entre tramos.
        rf_mensual_contable_tramos: list = []
        for tramo in tramos:
            if set_progress is not None:
                try:
                    # Barra de progreso con % por tramo (opción A). El % refleja los
                    # tramos ya COMPLETADOS: al iniciar el tramo `i` (0-based), hay `i`
                    # tramos terminados de `n_tramos` → i/n_tramos. Es un progreso a nivel
                    # de tramo (el motor RF no reporta avance intra-tramo; ver deuda B).
                    _pct = int(round(100.0 * tramo.indice / n_tramos)) if n_tramos else 0
                    set_progress((_barra_progreso_tramo(
                        _pct, tramo.indice + 1, n_tramos, tramo.fecha_corte),))
                except Exception:
                    pass

            # ── 1. Portafolio de inicio del tramo ──
            if tramo.indice == 0:
                port = cargar_portafolio_proyeccion(str(DB_PATH), _fecha_pip_str)
            else:
                corte_prev = tramos[tramo.indice - 1].fecha_corte
                try:
                    df_prev = ptr.cargar_snapshot_proy(conn, corte_prev)
                except ptr.SnapshotAusenteError:
                    return (None, html.Span(
                        f"❌ Falta el snapshot del corte {corte_prev} para encadenar el tramo siguiente.",
                        style={'color': 'red'}))
                port = ptr.cargar_titulos_desde_snapshot(df_prev)

            if not port:
                return (None, html.Span(
                    f"⚠️ Sin títulos de renta fija para el tramo con cierre {tramo.fecha_corte}.",
                    style={'color': '#e65100'}))

            # ── 2. Shifts del tramo ──
            sh = shifts_por_tramo[tramo.indice] if tramo.indice < len(shifts_por_tramo) else {}
            sh = sh or {}

            # ── 3. Mapa de reservas del tramo (mensual | anual prorrateado) ──
            periodicidad = (periodicidad_por_tramo[tramo.indice]
                            if tramo.indice < len(periodicidad_por_tramo) else 'mensual')
            if str(periodicidad).strip().lower() == 'anual':
                variacion = (variacion_anual_por_tramo[tramo.indice]
                             if tramo.indice < len(variacion_anual_por_tramo) else {})
                mapa_reservas = ptr.prorratear_reservas_anual(variacion or {}, tramo.sub_horizonte)
            else:
                try:
                    mapa_reservas = cargar_mapa_reservas(str(DB_PATH), tramo.fecha_inicio, tramo.sub_horizonte)
                except Exception:
                    mapa_reservas = {}

            # ── 3b. Multiactivo: sendas del tramo (TRM interpolada, IPC/repo planas) ──
            # Solo en modo multiactivo; en RF-only se conserva el comportamiento actual.
            sendas_tramo = None
            inputs_tramo_i = None
            # TRM del tramo (por defecto la global, retrocompatible).
            _trm_tramo_ini = _trm_t0
            _trm_tramo_fin = _trm_final
            _senda_repo_rf = None
            _senda_infl_rf = None
            # ── UVR del tramo (spec uvr-capitalizacion-por-tramos, tarea 3.3) ──
            # Por defecto (RF-only / retrocompatible) la UVR arranca y cierra en el nivel
            # t0 (senda plana), como antes. En modo multiactivo se capitaliza y encadena.
            _uvr_inicio = _uvr_t0
            _uvr_cierre_tramo = _uvr_t0
            if multiactivo:
                inputs_tramo_i = inputs_tramo_objs[tramo.indice]
                sendas_tramo = construir_sendas_tramo(
                    tramo, inputs_tramo_i, estado_previo, trm_t0_real=_trm_t0
                )
                # TRM del tramo (Req 6.2): nivel de INICIO = cierre del tramo previo
                # (encadenado) o TRM t0 para el primer tramo; nivel de CIERRE = input del
                # tramo. NOTA: el nivel de inicio NO es el primer corte de la senda (que ya
                # avanzó un paso), sino la TRM al arranque del tramo.
                if estado_previo is not None and getattr(estado_previo, "trm_cierre", None):
                    _trm_tramo_ini = float(estado_previo.trm_cierre)
                else:
                    _trm_tramo_ini = _trm_t0
                _trm_tramo_fin = float(inputs_tramo_i.trm_cierre)
                # senda_repo / senda_inflacion como listas de % anual por mes
                # (0..sub_horizonte), que es la forma que consume el motor RF por índice
                # `senda[m]` (NO interpola internamente) (Req 7.1, 7.2). Antes eran PLANAS
                # al nivel de cierre del tramo; ahora INTERPOLAN dentro del tramo desde el
                # nivel de CIERRE del tramo anterior hasta el nivel del tramo actual (que es
                # un nivel de cierre), igual que ya se hace con la TRM.
                #
                # UNIDADES: `inputs_tramo_i.repo_ea` / `inflacion_ea` son DECIMALES
                # (ej 0.05) y el motor espera la lista en % anual (×100). `obtener_ipc_para`
                # devuelve % anual (ej 5.84). Todas las listas se construyen en % anual.
                #
                # Nivel de INICIO del repo: cierre del repo del tramo previo si existe
                # (tramos i>0); para el PRIMER tramo se usa el `repo_ini` de la Sección 2
                # (Variables macro) — la Repo inicial vigente que captura el usuario —, de
                # modo que el primer tramo TRANSICIONE desde el repo actual hasta su nivel
                # de cierre. Si `repo_ini` no está disponible, cae al propio nivel de cierre
                # del tramo (arranque plano); NO es un hardcode: es el input del usuario.
                # `repo_ini` viene en % anual (mismo formato que consume el motor RF).
                _repo_cierre_pct = float(inputs_tramo_i.repo_ea) * 100.0
                if estado_previo is not None and getattr(estado_previo, "repo_cierre", None) is not None:
                    _repo_inicio_pct = float(estado_previo.repo_cierre)
                elif repo_ini is not None:
                    try:
                        _repo_inicio_pct = float(repo_ini)
                    except (TypeError, ValueError):
                        _repo_inicio_pct = _repo_cierre_pct
                else:
                    _repo_inicio_pct = _repo_cierre_pct
                # Nivel de INICIO de la inflación: cierre de la inflación del tramo previo
                # si existe (tramos i>0). Para el PRIMER tramo, en orden de prioridad:
                #   1) el campo 'IPC vigente (%)' de la Sección 2 (`ipc_vigente`), que el
                #      usuario captura manualmente (no hay 'Serie Inflación.xlsx' en el
                #      proyecto);
                #   2) el IPC vigente automático `obtener_ipc_para` (si existe la serie);
                #   3) el propio input de cierre del tramo (inflacion_ea×100) como arranque
                #      plano (fallback; NO es hardcode, es el valor capturado del tramo).
                # `ipc_vigente` y `obtener_ipc_para` vienen en % anual (mismo formato del motor RF).
                _infl_cierre_pct = float(inputs_tramo_i.inflacion_ea) * 100.0
                if estado_previo is not None and getattr(estado_previo, "inflacion_cierre", None) is not None:
                    _infl_inicio_pct = float(estado_previo.inflacion_cierre)
                elif ipc_vigente is not None:
                    try:
                        _infl_inicio_pct = float(ipc_vigente)
                    except (TypeError, ValueError):
                        _infl_inicio_pct = _infl_cierre_pct
                else:
                    _ipc_auto = obtener_ipc_para(_fecha_pip_str)
                    _infl_inicio_pct = float(_ipc_auto) if _ipc_auto is not None else _infl_cierre_pct
                # Interpolar mes a mes (índice 0 = inicio, índice sub_horizonte = cierre).
                _senda_repo_rf = interpolar_nivel_tramo(
                    _repo_inicio_pct, _repo_cierre_pct, tramo.sub_horizonte
                )
                _senda_infl_rf = interpolar_nivel_tramo(
                    _infl_inicio_pct, _infl_cierre_pct, tramo.sub_horizonte
                )

                # ── UVR del tramo: capitalización + encadenamiento (tarea 3.3) ──
                # La UVR crece dentro del tramo según la inflación anual del tramo
                # (`inflacion_ea`, DECIMAL EA), escalada por la fracción de año que cubre el
                # tramo. El exponente `meses_tramo / 12` cubre tanto el primer tramo PARCIAL
                # (p. ej. 5/12) como los tramos anuales completos (12/12 = 1), de modo que
                # el escalado del primer tramo (Req 2.3) es un caso particular de la misma
                # fórmula. Con infl_ea == 0 ⇒ factor_tramo == 1 ⇒ UVR constante en el tramo
                # (Req 3.6); con infl_ea >= 0 ⇒ factor_tramo >= 1 ⇒ UVR no decreciente
                # (Req 2.5). El inicio del tramo es la UVR de cierre del tramo previo
                # (encadenamiento vía EstadoEncadenado.uvr_cierre) o `_uvr_t0` en el primer
                # tramo. Misma semántica que `construir_senda_uvr_por_tramos`.
                _meses_tramo = tramo.sub_horizonte
                _infl_ea = float(inputs_tramo_i.inflacion_ea)
                _factor_tramo = (1.0 + _infl_ea) ** (_meses_tramo / 12.0)
                if estado_previo is not None and getattr(estado_previo, "uvr_cierre", None) is not None:
                    _uvr_inicio = float(estado_previo.uvr_cierre)
                else:
                    _uvr_inicio = _uvr_t0
                _uvr_cierre_tramo = _uvr_inicio * _factor_tramo

            # ── 4. Correr el motor RF del tramo ──
            # [BATCH/INFORME - Opcion 1] Captura VM/VTIR INICIAL del tramo ANTES de correr
            # el motor, para el P&G/ORI DEL TRAMO por titulo. NO altera la valoracion (solo
            # lee atributos). proyectar_portafolio extiende `port` con las reinversiones.
            try:
                from tools.detalle_pyg_tramo import snapshot_inicial as _snap_ini_tramo
                _vm_ini_tramo, _vtir_ini_tramo = _snap_ini_tramo(port)
            except Exception:
                _vm_ini_tramo, _vtir_ini_tramo = {}, {}
            try:
                sendas = _sendas_planas_tramo(DB_PATH, _fecha_pip_str, tramo.sub_horizonte)
                with _crono.medir("RF", tramo.indice):
                    # [Tarea 4.3] Capturar la lista de ResultadoMes del tramo (antes se
                    # descartaba). Se usa para componer la serie RF mensual contable.
                    _res_rf_tramo = proyectar_portafolio(
                        portafolio=port,
                        fecha_inicio=tramo.fecha_inicio,
                        meses_horizonte=tramo.sub_horizonte,
                        sendas=sendas,
                        valor_uvr=_uvr_inicio,
                        valor_uvr_cierre=_uvr_cierre_tramo,
                        trm=_trm_tramo_ini, trm_cierre=_trm_tramo_fin,
                        shift_cop_bps=float(sh.get('shift_cop', 0) or 0),
                        shift_uvr_bps=float(sh.get('shift_uvr', 0) or 0),
                        shift_fs_bps=float(sh.get('shift_fs', 0) or 0),
                        shift_ipc_bps=float(sh.get('shift_ipc', 0) or 0),
                        shift_subyac_bps=float(sh.get('shift_subyac', 0) or 0),
                        shift_tesoros_bps=float(sh.get('shift_tesoros', 0) or 0),
                        mapa_reservas=mapa_reservas,
                        senda_repo=_senda_repo_rf,
                        senda_inflacion=_senda_infl_rf,
                    )
            except Exception as e_motor:
                print(f"[TRAMOS] Error en el motor RF del tramo {tramo.fecha_corte}: {e_motor}")
                return (None, html.Span(
                    f"❌ Falló la proyección del tramo con cierre {tramo.fecha_corte}.",
                    style={'color': 'red'}))

            # [BATCH/INFORME - Opcion 1] Detalle P&G/ORI por titulo de RF DEL TRAMO, con la
            # MISMA regla contable (NEG/DV/VC) del flujo punta a punta (helper
            # tools.detalle_pyg_tramo, extraccion pura). Falla-seguro: no aborta la corrida.
            try:
                from tools.detalle_pyg_tramo import construir_rf_detalle as _rf_det_tramo
                # trm_cierre/uvr_cierre: re-valoracion correcta de USD (fix bug VM-USD del
                # informe, ver docs/deuda-tecnica). Usa la TRM/UVR de cierre del tramo.
                _det_rf = _rf_det_tramo(port, _vm_ini_tramo, _vtir_ini_tramo, tramo.fecha_corte,
                                        trm_cierre=_trm_tramo_fin, uvr_cierre=_uvr_cierre_tramo)
                detalle_por_tramo.append({
                    "indice": tramo.indice,
                    "fecha_corte": tramo.fecha_corte.strftime("%Y-%m-%d")
                        if hasattr(tramo.fecha_corte, "strftime") else str(tramo.fecha_corte),
                    "rf_detalle": _det_rf,
                })
            except Exception as _e_det:
                print(f"[INFORME] No se pudo construir el detalle RF del tramo "
                      f"{tramo.fecha_corte}: {type(_e_det).__name__}")

            # [Tarea 4.3] Serie RF mensual contable DEL TRAMO con el helper compartido
            # (Tarea 4.1), reutilizando la MISMA regla NEG/DV/VC del flujo punta a punta.
            # Se anexa al registro del tramo (detalle_por_tramo[-1]) y se concatena a la
            # serie continua global, de modo que exportar_excel_tramos pueda reconstruir
            # una hoja RF_Mensual_Tramos continua. Falla-seguro: no aborta la corrida.
            try:
                from rf_contable_mensual import construir_rf_mensual_contable as _rf_mens_tramo
                _rf_mensual = _rf_mens_tramo(_res_rf_tramo, port, tramo.fecha_corte)
                if detalle_por_tramo and detalle_por_tramo[-1].get("indice") == tramo.indice:
                    detalle_por_tramo[-1]["rf_mensual_contable"] = _rf_mensual
                rf_mensual_contable_tramos.extend(_rf_mensual)
                print(f"[INFORME] RF_Mensual del tramo {tramo.fecha_corte}: "
                      f"{len(_rf_mensual)} meses.", flush=True)
            except Exception as _e_rfm:
                print(f"[INFORME] No se pudo construir la serie RF mensual del tramo "
                      f"{tramo.fecha_corte}: {type(_e_rfm).__name__}")

            # ── 4b. Multiactivo: correr RV / Alternativos / Créditos y encadenar ──
            if multiactivo:
                _trm_ini_tr, _trm_cie_tr = _extremos_senda_trm(sendas_tramo.senda_trm)
                # Nivel de arranque REAL del tramo = fuente de verdad (igual que RF). El
                # inicio del efecto moneda NO es senda[0] (avanzado un paso / plano en el
                # tramo 0); _trm_cie_tr (== trm_cierre) se conserva sin cambios.
                _trm_inicio_real = _trm_tramo_ini
                _corte_iso = tramo.fecha_corte.strftime("%Y-%m-%d")
                # (activo, adaptador, col_id) — orden cronológico de invocación por tramo.
                _motores_no_rf = (
                    ("Renta Variable", _correr_rv_tramo, "id_posicion"),
                    ("Alternativos", _correr_alt_tramo, "id_posicion"),
                    ("Créditos", _correr_creditos_tramo, None),
                )
                _res_no_rf: dict = {}
                for _nombre, _adaptador, _col_id in _motores_no_rf:
                    try:
                        with _crono.medir(_nombre, tramo.indice):
                            _res_no_rf[_nombre] = _adaptador(
                                tramo, inputs_tramo_i, sendas_tramo, estado_previo
                            )
                    except Exception as e_no_rf:
                        # Detener el encadenamiento; identificar activo + fecha_corte,
                        # SIN exponer fin de horizonte ni datos sensibles (Req 4.4, 12.3).
                        print(f"[TRAMOS] Error en {_nombre} del tramo "
                              f"{tramo.fecha_corte}: {type(e_no_rf).__name__}")
                        return (None, html.Span(
                            f"❌ Falló {_nombre} en el tramo con cierre {tramo.fecha_corte}.",
                            style={'color': 'red'}))

                df_rv = _res_no_rf["Renta Variable"]
                df_alt = _res_no_rf["Alternativos"]
                df_cred = _res_no_rf["Créditos"]
                df_rv_ultimo, df_alt_ultimo, df_cred_ultimo = df_rv, df_alt, df_cred

                # Efecto moneda por activo internacional y tramo (Req 11).
                efectos_moneda.extend(calcular_efecto_moneda_tramo(
                    df_rv, activo="rv", tramo_indice=tramo.indice, fecha_corte=_corte_iso,
                    trm_inicio=_trm_inicio_real, trm_cierre=_trm_cie_tr, col_id="id_posicion",
                ))
                efectos_moneda.extend(calcular_efecto_moneda_tramo(
                    df_alt, activo="alternativos", tramo_indice=tramo.indice,
                    fecha_corte=_corte_iso, trm_inicio=_trm_inicio_real, trm_cierre=_trm_cie_tr,
                    col_id="id_posicion",
                ))

                # D3 — Delta de curva ACUMULADO de derivados entre tramos (análogo a
                # repo_cierre/inflacion_cierre). `_delta_acum_previo` = acumulado de los
                # tramos 0..i-1 (None en el tramo 0 → el motor NO desplaza la curva base).
                # `_delta_tramo_i` = delta propio de ESTE tramo con el MISMO mapeo que
                # `_correr_derivados_tramo` (mov_tes_cop_bps→delta_tes_cop, etc.), en bps.
                _delta_acum_previo = (
                    getattr(estado_previo, "delta_curva_acumulado", None)
                    if estado_previo is not None else None
                )
                _delta_tramo_i = {
                    "delta_tes_cop": float(getattr(inputs_tramo_i, "mov_tes_cop_bps", 0.0) or 0.0),
                    "delta_tes_uvr": float(getattr(inputs_tramo_i, "mov_tes_uvr_bps", 0.0) or 0.0),
                    "delta_fed_bps": float(getattr(inputs_tramo_i, "delta_fed_bps", 0.0) or 0.0),
                    "delta_10t_bps": float(getattr(inputs_tramo_i, "delta_tesoros10y_bps", 0.0) or 0.0),
                }
                _delta_curva_acum_tramo = acumular_delta_curva(_delta_acum_previo, _delta_tramo_i)

                # Actualizar el EstadoEncadenado del SIGUIENTE tramo con el VM de cierre.
                estado_previo = EstadoEncadenado(
                    vm_rv_por_posicion=extraer_vm_cierre(df_rv, col_id="id_posicion"),
                    vm_alt_por_posicion=extraer_vm_cierre(df_alt, col_id="id_posicion"),
                    saldo_credito_por_id={},
                    trm_cierre=float(inputs_tramo_i.trm_cierre),
                    # repo/inflación de cierre guardados en % anual, para que el tramo
                    # siguiente los tome como nivel de INICIO directo al interpolar (mismas
                    # unidades que consume el motor RF).
                    repo_cierre=float(inputs_tramo_i.repo_ea) * 100.0,
                    inflacion_cierre=float(inputs_tramo_i.inflacion_ea) * 100.0,
                    # UVR de cierre del tramo: se arrastra como UVR de INICIO del tramo
                    # siguiente (encadenamiento análogo a trm_cierre, tarea 3.3).
                    uvr_cierre=_uvr_cierre_tramo,
                    # D3: delta de curva acumulado hasta el cierre de ESTE tramo (0..i),
                    # que alimentará el inicio de la curva de derivados del tramo siguiente.
                    delta_curva_acumulado=_delta_curva_acum_tramo,
                )

                # ── 4c. Derivados por tramo con encadenamiento del plano (Opción B, Req 13) ──
                # El plano de posiciones VIVAS al cierre del tramo anterior es el plano de
                # INICIO de este tramo. Tramo 0: plano_inicial=None (el motor lee hist_*
                # reales). Tramo i>0: se carga el plano _proy del corte anterior. Tras
                # correr, se persiste el plano de cierre de ESTE tramo para el siguiente.
                # Si falla, se detiene el encadenamiento (Req 13.7).
                try:
                    _plano_ini = None
                    if tramo.indice > 0:
                        corte_prev = tramos[tramo.indice - 1].fecha_corte
                        _plano_ini = ptr.cargar_plano_derivados_proy(conn, corte_prev)
                    with _crono.medir("Derivados", tramo.indice):
                        deriv_ultimo = _correr_derivados_tramo(
                            tramo, inputs_tramo_i, sendas_tramo, conn,
                            metodo_deriv=metodo_deriv, plano_inicial=_plano_ini,
                            fecha_mercado=_fecha_pip_str,  # curvas de mercado desde la fecha base real
                            trm_inicio_real=_trm_tramo_ini,  # misma fuente de verdad que RF
                            # D3: delta de curva acumulado de los tramos previos (0..i-1).
                            # None en el tramo 0 → comportamiento actual (sin desplazar la base).
                            delta_curva_base=_delta_acum_previo,
                        )
                    # Persistir el plano de cierre de este tramo en las tablas _proy
                    # (inicio del tramo siguiente). Nunca escribe en hist_* reales.
                    _plano_cierre = (deriv_ultimo or {}).get('plano_cierre')
                    if _plano_cierre:
                        ptr.persistir_plano_derivados_proy(conn, _plano_cierre, tramo.fecha_corte)
                except Exception as e_deriv:
                    import traceback as _tb_d
                    # Traceback completo al log del worker (diagnóstico); al usuario solo
                    # un mensaje genérico con activo + fecha de corte (sin datos sensibles).
                    print(f"[TRAMOS] Error en Derivados del tramo "
                          f"{tramo.fecha_corte}: {type(e_deriv).__name__}: {e_deriv}")
                    print(_tb_d.format_exc())
                    return (None, html.Span(
                        f"❌ Falló Derivados en el tramo con cierre {tramo.fecha_corte}.",
                        style={'color': 'red'}))

                # [BATCH/INFORME - Opcion 1] Anexar al registro del tramo los resultados
                # no-RF (RV/Alt serializables + VM de cierre de derivados y su PyG) y las
                # TRM del tramo, para el resumen por categoria/ramo. Falla-seguro.
                try:
                    if detalle_por_tramo and detalle_por_tramo[-1].get("indice") == tramo.indice:
                        _reg = detalle_por_tramo[-1]
                        _reg["rv"] = (df_rv.to_dict("records")
                                      if df_rv is not None and len(df_rv) else [])
                        _reg["alt"] = (df_alt.to_dict("records")
                                       if df_alt is not None and len(df_alt) else [])
                        _reg["deriv_vm_cierre"] = (deriv_ultimo or {}).get("vm_cierre", {})
                        _reg["deriv_pyg"] = (deriv_ultimo or {}).get("pyg", [])
                        # Clave nueva en el payload JSON (retrocompatible): detalle completo
                        # por tipo de derivado (dict {tipo: records}) para las hojas de
                        # detalle del Excel por tramos. Lectores existentes no la requieren.
                        _reg["deriv_detalle"] = (deriv_ultimo or {}).get("detalle", {})
                        _reg["trm_inicio"] = float(_trm_inicio_real) if _trm_inicio_real else None
                        _reg["trm_cierre"] = float(_trm_cie_tr) if _trm_cie_tr else None
                except Exception as _e_nrf:
                    print(f"[INFORME] No se pudo anexar no-RF del tramo "
                          f"{tramo.fecha_corte}: {type(_e_nrf).__name__}")

            # ── 5. Construir df_proy del tramo y persistir el snapshot ──
            # Base para el df_proy: el portafolio de inicio del tramo como DataFrame.
            with _crono.medir("Snapshot_build", tramo.indice):
                df_base = _titulos_a_dataframe_base(port, conn, tramo)
                df_proy = _construir_df_proy_rf(df_base, port, tramo.fecha_corte)[0]
                # Enriquecer con el spread efectivo de cierre y valores para autosuficiencia.
                df_proy = _enriquecer_snapshot_tramo(df_proy, port, tramo.fecha_corte)
                df_proy = _finalizar_df_proy(df_proy, tramo.fecha_corte)
            # ── 5b. Snapshot COMPLETO del tramo (Req 14): RF + RV + Alt + Derivados ──
            # En modo multiactivo se anexan filas no-RF (distinguidas por clase_origen)
            # al df_proy RF. La reconstrucción del tramo siguiente sigue filtrando solo RF
            # (invariante Req 14.4), por lo que estas filas son informativas.
            if multiactivo:
                try:
                    df_proy = _construir_snapshot_completo_tramo(
                        df_proy, df_rv_ultimo, df_alt_ultimo, deriv_ultimo,
                        tramo.fecha_corte, conn_base=conn,
                    )
                except Exception as e_snap:
                    print(f"[TRAMOS] Snapshot completo del tramo {tramo.fecha_corte} "
                          f"no pudo anexar no-RF: {type(e_snap).__name__}")
            # ── 5c. Consolidar reinversiones Negociables fungibles (feature
            #        optimizacion-agrupacion-negociables, Req 1/6). Solo aplica en el
            #        modo por_tramos (este orquestador); el flujo punta_a_punta no pasa
            #        por aquí (Req 6.1/6.2). Fail-safe: si la consolidación falla por
            #        datos degenerados, se persiste el conjunto SIN consolidar (fallback
            #        no destructivo) sin abortar el encadenamiento (Req 6.5). El
            #        diagnóstico [TRAMOS-CONSOLIDA] es análogo al [SNAPSHOT-DEDUP].
            try:
                _n_antes = len(df_proy)
                df_proy, _resumen_cons = ptr.consolidar_negociables_fungibles(df_proy)
                print(f"[TRAMOS-CONSOLIDA] tramo {tramo.fecha_corte} "
                      f"{_n_antes}->{len(df_proy)} filas; "
                      f"grupos={_resumen_cons.n_grupos} "
                      f"colapsadas={_resumen_cons.n_colapsadas} "
                      f"negociables={_resumen_cons.n_negociables} "
                      f"intocables={_resumen_cons.n_intocables}", flush=True)
            except Exception as _e_cons:
                print(f"[TRAMOS-CONSOLIDA] tramo {tramo.fecha_corte} "
                      f"consolidación OMITIDA por error: {type(_e_cons).__name__} "
                      f"(se persiste sin consolidar).", flush=True)
            with _crono.medir("Persistencia_IO", tramo.indice):
                ptr.persistir_snapshot_proy(conn, df_proy, tramo.fecha_corte)
                # [BATCH/INFORME - Opcion 1] Persistir INCREMENTALMENTE el detalle P&G/ORI
                # de ESTE tramo (JSON por corte), para que el runner batch arme el Excel
                # parcial aunque un tramo posterior no termine. Falla-seguro.
                try:
                    if detalle_por_tramo and detalle_por_tramo[-1].get("indice") == tramo.indice:
                        _n_rf = len(detalle_por_tramo[-1].get("rf_detalle", []))
                        _persistir_detalle_informe_tramo(conn, detalle_por_tramo[-1])
                        print(f"[INFORME] Detalle del tramo {tramo.fecha_corte} persistido "
                              f"(RF={_n_rf} titulos).", flush=True)
                    else:
                        print(f"[INFORME] AVISO: detalle vacio o indice no coincide para "
                              f"el tramo {tramo.fecha_corte}.", flush=True)
                except Exception as _e_pdi:
                    print(f"[INFORME] No se pudo persistir el detalle del tramo "
                          f"{tramo.fecha_corte}: {type(_e_pdi).__name__}")
            df_proy_final = df_proy
    finally:
        conn.close()

    # Resumen de tiempos por motor y tramo (Fix 3, Req 17.3). Al stdout del worker para
    # que sea visible en el log de la corrida; identifica el componente más costoso.
    try:
        _crono.log_resumen(logger_fn=print)
    except Exception:
        pass

    resultados = {
        'parametros': {'fecha_inicio': str(fecha_ini), 'fecha_fin': str(fecha_end),
                       'modo': 'por_tramos', 'n_tramos': n_tramos},
        'portafolio_proyectado': df_proy_final.to_dict('records') if df_proy_final is not None else [],
        'rf_resumen': True, 'derivados': None, 'simultaneas': None,
        'rv': None, 'alternativos': None, 'creditos': None,
        # [Tarea 4.3] Serie RF mensual contable CONTINUA a traves de todos los tramos
        # (concatenada por tramo con el helper compartido). Analoga a la clave
        # 'rf_mensual_contable' del modo punta_a_punta; alimenta la hoja RF_Mensual_Tramos
        # (Tarea 4.4) y hace que isBugCondition_D1 deje de cumplirse por clave ausente.
        'rf_mensual_contable': rf_mensual_contable_tramos,
    }

    # ── Consolidación multiactivo del ÚLTIMO tramo en el store (tarea 5.4/5.8). ──
    # Se replica el MISMO formato que produce el modo punta a punta (mismos groupbys y
    # claves) para que el renderizador de resultados existente lo consuma sin cambios.
    if multiactivo:
        try:
            if df_rv_ultimo is not None and len(df_rv_ultimo) > 0:
                _rv_res = df_rv_ultimo.groupby('fecha_proyeccion').agg(
                    vm_total=('valor_mercado', 'sum'),
                    pyg_mensual=('pyg_mensual', 'sum')).reset_index()
                _rv_res['pyg_acum'] = _rv_res['pyg_mensual'].cumsum()
                resultados['rv'] = _rv_res.to_dict('records')
        except Exception as _e_rv:
            print(f"[TRAMOS] No se pudo consolidar RV del último tramo: {type(_e_rv).__name__}")
        try:
            if df_alt_ultimo is not None and len(df_alt_ultimo) > 0:
                _alt_res = df_alt_ultimo.groupby('fecha_proyeccion').agg(
                    pyg_mensual=('pyg_mensual', 'sum'),
                    vm_total=('valor_mercado', 'sum')).reset_index()
                _alt_res['pyg_acum'] = _alt_res['pyg_mensual'].cumsum()
                resultados['alternativos'] = _alt_res.to_dict('records')
        except Exception as _e_alt:
            print(f"[TRAMOS] No se pudo consolidar Alternativos del último tramo: {type(_e_alt).__name__}")
        try:
            if df_cred_ultimo is not None and len(df_cred_ultimo) > 0:
                _cred_res = df_cred_ultimo.groupby('fecha_proyeccion').agg(
                    interes_mes=('interes_mes', 'sum'),
                    saldo_fin=('saldo_fin_mes', 'sum')).reset_index()
                _cred_res['interes_acum'] = _cred_res['interes_mes'].cumsum()
                resultados['creditos'] = _cred_res.to_dict('records')
        except Exception as _e_cred:
            print(f"[TRAMOS] No se pudo consolidar Créditos del último tramo: {type(_e_cred).__name__}")
        # Derivados del último tramo (Req 13.6): PyG en el mismo formato que punta a punta
        # (registros de calcular_pyg). Reemplaza el `None` inicial.
        try:
            if deriv_ultimo and deriv_ultimo.get('pyg'):
                resultados['derivados'] = deriv_ultimo['pyg']
        except Exception as _e_der:
            print(f"[TRAMOS] No se pudo consolidar Derivados del último tramo: {type(_e_der).__name__}")
        # Efecto moneda: lista de registros serializables (para la tarea 8.0 → Excel).
        resultados['efecto_moneda'] = [ef.to_record() for ef in efectos_moneda]
    _msg_hijos = [
        html.Span(f"✅ Escenario por tramos completado: {n_tramos} tramo(s).",
                  style={'color': '#28a745', 'fontWeight': 'bold'}),
        html.Br(),
        html.Span(f"Cortes: {', '.join(str(t.fecha_corte) for t in tramos)}.",
                  style={'fontSize': '10px', 'color': '#555'}),
    ]

    # Nota informativa (no bloqueante) para años del horizonte sin columna en el insumo:
    # esos años usan variación de reserva Δ=0 (Req 10.2, 10.3). Se muestra con estilo
    # informativo (azul), NUNCA como alerta bloqueante roja, y no impide la corrida.
    if anos_sin_columna:
        _anos_txt = ', '.join(str(a) for a in anos_sin_columna)
        _msg_hijos.extend([
            html.Br(),
            html.Span(
                f"ℹ️ Nota informativa: los años {_anos_txt} no tienen columna en el "
                f"insumo de reservas; usan variación de reserva Δ=0.",
                style={'fontSize': '11px', 'color': '#0c5460',
                       'backgroundColor': '#d1ecf1', 'padding': '2px 6px',
                       'borderRadius': '3px', 'display': 'inline-block',
                       'marginTop': '4px'},
            ),
        ])

    msg = html.Div(_msg_hijos)
    return resultados, msg


def _titulos_a_dataframe_base(port, conn, tramo):
    """
    Construye el DataFrame base del tramo (el portafolio de inicio) para alimentar
    `_construir_df_proy_rf`. Para el primer tramo se lee de portafolio_clasificado; para
    tramos posteriores se reconstruye desde los propios títulos (que vienen del snapshot).
    """
    if tramo.indice == 0:
        df = pd.read_sql_query("SELECT * FROM portafolio_clasificado", conn)
        return df
    # Tramos posteriores: base a partir de los títulos reconstruidos del snapshot.
    # Fix 1 (bug snapshot i>0): se incluye 'clase_origen': 'TITULO' para que las filas RF
    # reconstruidas queden etiquetadas igual que en el tramo 0 (donde vienen de
    # portafolio_clasificado con clase_origen poblado). Sin esto, el snapshot de los
    # tramos i>0 persistía las filas RF con clase_origen NULL y el Excel no discriminaba
    # tipos de activo en 2027/2028.
    filas = []
    for t in port:
        filas.append({
            'id_posicion': t.id_posicion, 'especie': t.especie, 'emisor': t.emisor,
            'isin': t.isin, 'moneda': t.moneda,
            'fecha_emision': str(t.fecha_emision), 'fecha_vencimiento': str(t.fecha_vencimiento),
            'nominal': t.nominal, 'valor_mercado': t.valor_mercado, 'valor_tir': t.valor_tir,
            'tasa_compra': t.tasa_compra, 'tir': t.tasa_mercado,
            'portafolio': t.portafolio, 'compania': t.compania, 'ramo': t.ramo,
            'asset_class_definitivo': t.asset_class, 'clasificacion_contable': t.clasificacion,
            'periodicidad': t.frecuencia, 'tasa_facial': t.tasa_cupon,
            'spread_mercado': t.spread_mercado,
            'clase_origen': 'TITULO',
            # Fix (Defecto 4): preservar id_cobertura en el round-trip del snapshot i>0, de
            # modo que los subyacentes de swaps sigan separándose de la RF internacional en
            # 2027/2028 igual que en el tramo 0. Metadato: no altera reconstrucción RF ni VM.
            'id_cobertura': t.id_cobertura,
        })
    return pd.DataFrame(filas)


def _enriquecer_snapshot_tramo(df_proy, port, fecha_corte):
    """
    Añade al df_proy los campos de autosuficiencia por título (fecha_emision, valor_tir y
    el spread EFECTIVO DE CIERRE) para que el snapshot permita reconstruir el t0 del tramo
    siguiente. Cruza por id_posicion con los títulos del motor.
    """
    por_id = {t.id_posicion: t for t in port}
    df = df_proy.copy()

    def _campo(idp, attr, default=None):
        t = por_id.get(idp)
        if t is None:
            return default
        return getattr(t, attr, default)

    if 'id_posicion' in df.columns:
        df['fecha_emision'] = df['id_posicion'].map(
            lambda x: str(_campo(x, 'fecha_emision', '')) if _campo(x, 'fecha_emision') else None)
        df['valor_tir'] = df['id_posicion'].map(lambda x: _campo(x, 'valor_tir', None))
        # spread EFECTIVO de cierre expuesto por el motor (Opción A).
        df['spread_mercado'] = df['id_posicion'].map(
            lambda x: _campo(x, '_spread_efectivo_cierre', _campo(x, 'spread_mercado', None)))
    return df


# Valores de `clase_origen` para distinguir el tipo de activo en el Snapshot_Completo_Tramo
# (Req 14.2). Literales controlados por el código (no entrada externa).
_CLASE_ORIGEN_RV = 'RENTA_VARIABLE'
_CLASE_ORIGEN_ALT = 'ALTERNATIVO'
_CLASE_ORIGEN_DERIV = 'DERIVADO'


def _filas_cierre_no_rf(df_resultado, col_id, clase_origen, fecha_corte):
    """
    Extrae las filas del ÚLTIMO corte de un DataFrame de motor no-RF (RV/Alternativos) y
    las mapea al esquema del snapshot (`_COLUMNAS_PROY`): `id_posicion`, `valor_mercado`,
    `moneda` (si existe), `especie` (si existe) y `clase_origen`. Las columnas ausentes
    quedan sin poblar (→ NULL al persistir). Devuelve una lista de dicts.
    """
    if df_resultado is None or len(df_resultado) == 0:
        return []
    if not {col_id, 'fecha_proyeccion', 'valor_mercado'}.issubset(set(df_resultado.columns)):
        return []
    idx_ultimo = df_resultado.groupby(col_id)['fecha_proyeccion'].idxmax()
    df_cierre = df_resultado.loc[idx_ultimo]
    filas = []
    for _, r in df_cierre.iterrows():
        fila = {
            'id_posicion': str(r[col_id]),
            'valor_mercado': float(pd.to_numeric(r.get('valor_mercado'), errors='coerce') or 0.0),
            'clase_origen': clase_origen,
            'fecha_corte_proyeccion': fecha_corte.strftime('%Y-%m-%d')
                if hasattr(fecha_corte, 'strftime') else str(fecha_corte),
        }
        for _c_src, _c_dst in (('moneda', 'moneda'), ('especie', 'especie'),
                               ('emisor', 'emisor'), ('asset_class', 'asset_class_definitivo')):
            if _c_src in df_cierre.columns and pd.notna(r.get(_c_src)):
                fila[_c_dst] = str(r.get(_c_src))
        filas.append(fila)
    return filas


def _construir_snapshot_completo_tramo(df_proy_rf, df_rv, df_alt, deriv_resultado, fecha_corte,
                                       conn_base=None):
    """
    Une en un solo DataFrame las filas de cierre del tramo (Req 14): RF + RV +
    Alternativos + Derivados, cada una con su `clase_origen`. Las columnas de la allowlist
    que no apliquen a un tipo de activo quedan ausentes (→ NULL al persistir vía
    `persistir_snapshot_proy`).

    FUENTE ÚNICA POR POSICIÓN (Req 16.4/16.5, tarea 14.3): antes de anexar, se eliminan de
    `df_proy_rf` las filas no-RF heredadas del portafolio base (derivados FORWARD/OPCION/
    SWAP y las posiciones RV/Alt que llegan como 'TITULO'), que están SIN proyectar. Así
    cada posición no-RF queda una sola vez, con el VM valorado por su motor.

    INVARIANTE (Req 14.4): las filas RF puras NO se modifican y
    `cargar_titulos_desde_snapshot` sigue filtrando no-RF (por clase_origen y asset_class),
    por lo que la deduplicación no altera la reconstrucción de títulos del tramo siguiente.
    """
    # RV identifica cada posición por `id_posicion` (único), igual que Alternativos. Así el
    # snapshot produce una fila RENTA_VARIABLE por posición real, con `id_posicion` numérico
    # y `especie` poblada (la columna de etiqueta `especie` se conserva en df_rv).
    _filas_rv = _filas_cierre_no_rf(df_rv, 'id_posicion', _CLASE_ORIGEN_RV, fecha_corte)
    _filas_alt = _filas_cierre_no_rf(df_alt, 'id_posicion', _CLASE_ORIGEN_ALT, fecha_corte)
    filas_extra = list(_filas_rv) + list(_filas_alt)
    # Derivados: VM de cierre por posición (dict {tipo:id → vm}) del adaptador.
    _filas_der = 0
    if deriv_resultado and deriv_resultado.get('vm_cierre'):
        _corte = (fecha_corte.strftime('%Y-%m-%d')
                  if hasattr(fecha_corte, 'strftime') else str(fecha_corte))
        for _pos_id, _vm in deriv_resultado['vm_cierre'].items():
            filas_extra.append({
                'id_posicion': str(_pos_id),
                'valor_mercado': float(pd.to_numeric(_vm, errors='coerce') or 0.0),
                'clase_origen': _CLASE_ORIGEN_DERIV,
                'fecha_corte_proyeccion': _corte,
            })
            _filas_der += 1

    # ── DEDUPLICACIÓN / FUENTE ÚNICA POR POSICIÓN (Fix 2, tarea 14.3, Req 16.4/16.5) ──
    # `df_proy_rf` arranca de `portafolio_clasificado` (`df_base.copy()` en
    # `_construir_df_proy_rf`), que CONSERVA todas las posiciones del portafolio base,
    # incluidas las NO-RF: los derivados (clase_origen FORWARD/OPCION/SWAP) y la RV/
    # Alternativos (que viven con clase_origen='TITULO' pero asset_class de acción/fondo).
    # Esas filas quedan con el VM INICIAL del base (sin proyectar), porque el motor RF solo
    # actualiza el VM de los títulos de renta fija. Si además anexamos las filas valoradas
    # por los motores no-RF (DERIVADO/RENTA_VARIABLE/ALTERNATIVO), cada posición no-RF
    # aparecería DOS veces (una sin proyectar + una proyectada).
    #
    # Regla de fuente única: para cada posición no-RF, el snapshot conserva SOLO la fila
    # valorada por su motor. Se eliminan de df_proy_rf:
    #   (a) las filas de derivados del base (clase_origen ∈ {FORWARD, OPCION, SWAP}), y
    #   (b) las filas cuyo id_posicion es aportado por los motores RV/Alternativos.
    # Las filas RF puras NO se tocan, por lo que la reconstrucción del tramo siguiente
    # (que ya filtra no-RF por asset_class en cargar_titulos_desde_snapshot) queda intacta
    # (invariante Req 14.4).
    _n_pre = len(df_proy_rf)
    _dedup_deriv = 0
    _dedup_rvalt = 0
    # ── PRESERVACIÓN DE ATRIBUTOS DE CLASIFICACIÓN (fix de metadatos no-RF) ──
    # Las filas no-RF (RV/Alt/Derivados) que arma el motor solo traen id_posicion,
    # valor_mercado, moneda, especie, emisor y asset_class. PIERDEN los atributos de
    # agrupación (compania, ramo, portafolio, asset_class_definitivo, clasificacion_*,
    # categoria, numeral_regimen, encaje, etc.) que la posición SÍ tiene en el portafolio
    # base. Como la dedup de abajo elimina la fila del base (que los contiene), aquí se
    # capturan esos atributos por id_posicion ANTES de borrarla, para re-inyectarlos en la
    # fila proyectada. Así el snapshot proyectado conserva la clasificación de RV/Alt/Deriv
    # y se puede agrupar por compañía/ramo/asset class. No altera valor_mercado (lo manda
    # el motor); solo rellena metadatos descriptivos que de otro modo quedarían NULL.
    _ATRIBS_PRESERVAR = (
        'compania', 'ramo', 'portafolio', 'asset_class_definitivo',
        'clasificacion_contable', 'clasificacion_solvencia', 'categoria',
        'ponderacion', 'numeral_regimen', 'descripcion_regimen', 'encaje',
        'subtipo_vehiculo', 'tramo_plazo', 'moneda', 'especie', 'emisor', 'isin',
    )
    _attrs_por_id: dict = {}
    if df_proy_rf is not None and len(df_proy_rf) and 'id_posicion' in df_proy_rf.columns:
        _cols_disp = [c for c in _ATRIBS_PRESERVAR if c in df_proy_rf.columns]
        if _cols_disp:
            _src_attrs = df_proy_rf.drop_duplicates(subset='id_posicion', keep='first')
            _src_attrs = _src_attrs.set_index(_src_attrs['id_posicion'].astype(str))
            for _idp in _src_attrs.index:
                _rec = {}
                for _c in _cols_disp:
                    _v = _src_attrs.at[_idp, _c] if _idp in _src_attrs.index else None
                    if pd.notna(_v) and str(_v).strip() not in ('', 'nan', 'None'):
                        _rec[_c] = _v
                if _rec:
                    _attrs_por_id[_idp] = _rec

    # RESPALDO para tramos i>0: en el PRIMER tramo, `df_proy_rf` viene de
    # `portafolio_clasificado` y contiene las filas no-RF con todos sus atributos. Pero en
    # los tramos SIGUIENTES, `df_proy_rf` se reconstruye desde el snapshot del tramo previo
    # —que YA deduplicó las no-RF— así que no hay de dónde heredar y RV/Alt/Derivados
    # saldrían sin compania/ramo/portafolio/asset_class. Para esos ids que el motor no-RF
    # va a anexar y que NO estén ya en `_attrs_por_id`, se consultan desde la tabla base
    # `portafolio_clasificado` (que SIEMPRE tiene esos atributos por id_posicion). Es solo
    # metadato descriptivo: no toca valor_mercado ni la reconstrucción RF del encadenamiento.
    if conn_base is not None:
        _ids_norf_necesarios = set()
        for _f in filas_extra:
            _idp_s = str(_f.get('id_posicion', '')).strip()
            # Para derivados ("tipo:id") se prueba también el id numérico tras ':'.
            _cands = [_idp_s]
            if ':' in _idp_s:
                _cands.append(_idp_s.split(':', 1)[1].strip())
            if not any(c in _attrs_por_id for c in _cands):
                for c in _cands:
                    _ids_norf_necesarios.add(c)
        if _ids_norf_necesarios:
            try:
                _df_base_pc = pd.read_sql_query(
                    "SELECT * FROM portafolio_clasificado", conn_base)
                # Mapear nombres de la tabla base -> nombres del esquema del snapshot.
                # OJO: en portafolio_clasificado la columna de asset class se llama
                # `asset_class_base` (el `asset_class_definitivo` lo calcula la
                # clasificación en memoria). Se mapea para poblar la columna del snapshot.
                _mapa_cols = {
                    'compania': 'compania', 'ramo': 'ramo', 'portafolio': 'portafolio',
                    'asset_class_base': 'asset_class_definitivo',
                    'asset_class_definitivo': 'asset_class_definitivo',
                    'clasificacion_contable': 'clasificacion_contable',
                    'clasificacion_solvencia': 'clasificacion_solvencia',
                    'categoria': 'categoria', 'numeral_regimen': 'numeral_regimen',
                    'descripcion_regimen': 'descripcion_regimen', 'encaje': 'encaje',
                    'subtipo_vehiculo': 'subtipo_vehiculo', 'moneda': 'moneda',
                    'especie': 'especie', 'emisor': 'emisor', 'isin': 'isin',
                }
                _pres = {s: d for s, d in _mapa_cols.items() if s in _df_base_pc.columns}
                _df_base_pc = _df_base_pc.drop_duplicates(subset='id_posicion', keep='first')
                _df_base_pc = _df_base_pc.set_index(_df_base_pc['id_posicion'].astype(str))
                _n_bk = 0
                for _idp in _ids_norf_necesarios:
                    if _idp in _attrs_por_id or _idp not in _df_base_pc.index:
                        continue
                    _rec = {}
                    for _src, _dst in _pres.items():
                        _v = _df_base_pc.at[_idp, _src]
                        if pd.notna(_v) and str(_v).strip() not in ('', 'nan', 'None'):
                            # Si ya se mapeo asset_class_definitivo desde asset_class_base,
                            # no sobreescribir con un segundo origen vacio.
                            _rec.setdefault(_dst, _v)
                    if _rec:
                        _attrs_por_id[_idp] = _rec
                        _n_bk += 1
                print(f"[SNAPSHOT-NORF] respaldo base: {_n_bk} de "
                      f"{len(_ids_norf_necesarios)} ids no-RF enriquecidos desde "
                      f"portafolio_clasificado (tramo i>0).")
            except Exception as _e_bk:
                print(f"[SNAPSHOT-NORF] respaldo portafolio_clasificado no disponible: "
                      f"{type(_e_bk).__name__}: {_e_bk}")
    # Re-inyectar los atributos del base en cada fila no-RF proyectada (sin pisar lo que el
    # motor ya trae: id_posicion, valor_mercado, clase_origen, y los campos que el motor
    # sí pobló). El valor_mercado y clase_origen de la fila proyectada NUNCA se tocan.
    # Para derivados, el id_posicion de la fila proyectada viene como "{tipo}:{id}"
    # (p. ej. "swaps:1218"); el base lo indexa por el id numérico ("1218"). Se prueba el
    # id tal cual y, si no hay match, la parte posterior a ':' (id numérico del derivado).
    def _lookup_attrs(_idp_raw):
        _idp_s = str(_idp_raw).strip()
        _a = _attrs_por_id.get(_idp_s)
        if _a is None and ':' in _idp_s:
            _a = _attrs_por_id.get(_idp_s.split(':', 1)[1].strip())
        return _a
    if _attrs_por_id:
        for _f in filas_extra:
            _base_attr = _lookup_attrs(_f.get('id_posicion', ''))
            if not _base_attr:
                continue
            for _c, _v in _base_attr.items():
                # Solo rellenar si la fila del motor NO trae ya ese atributo poblado.
                _actual = _f.get(_c)
                if _actual is None or (isinstance(_actual, float) and pd.isna(_actual)) \
                        or str(_actual).strip() in ('', 'nan', 'None'):
                    _f[_c] = _v
    if df_proy_rf is not None and len(df_proy_rf) and 'id_posicion' in df_proy_rf.columns:
        df_proy_rf = df_proy_rf.copy()
        # (a) Derivados heredados del base: se identifican por clase_origen (robusto; no
        # depende del formato de id, que en vm_cierre viene como "{tipo}:{id}").
        if 'clase_origen' in df_proy_rf.columns:
            _co = df_proy_rf['clase_origen'].astype(str).str.upper()
            _mask_deriv_base = _co.isin({'FORWARD', 'OPCION', 'SWAP'})
            _dedup_deriv = int(_mask_deriv_base.sum())
            if _dedup_deriv:
                df_proy_rf = df_proy_rf[~_mask_deriv_base]
        # (b) RV/Alt: por id_posicion aportado por los motores no-RF. Ahora TANTO RV como
        # Alt escriben el `id_posicion` numérico real (vía _filas_cierre_no_rf con
        # col_id='id_posicion'), de modo que `_ids_no_rf` contiene ids numéricos (como str)
        # y la máscara `isin` compara numérico-vs-numérico contra el `id_posicion` numérico
        # del base. Así elimina las filas 'TITULO' sin proyectar de posiciones RV/Alt ya
        # proyectadas, sin dejar duplicados ni filas `clase_origen` NULL espurias.
        _ids_no_rf = {f['id_posicion'] for f in (_filas_rv + _filas_alt)}
        if _ids_no_rf:
            _ids_str = df_proy_rf['id_posicion'].astype(str)
            _mask_rvalt = _ids_str.isin(_ids_no_rf)
            _dedup_rvalt = int(_mask_rvalt.sum())
            if _dedup_rvalt:
                df_proy_rf = df_proy_rf[~_mask_rvalt]
    if _dedup_deriv or _dedup_rvalt:
        print(f"[SNAPSHOT-DEDUP] df_proy_rf {_n_pre}→{len(df_proy_rf)} filas; "
              f"se removieron {_dedup_deriv} derivados del base y "
              f"{_dedup_rvalt} RV/Alt del base (fuente única por posición).")

    # Diagnóstico (Fix 2): deja rastro de cuántas filas no-RF se anexaron por tipo. Si en
    # una corrida real alguno sale en 0, el problema está AGUAS ARRIBA (el adaptador devolvió
    # un DataFrame vacío o sin las columnas esperadas), no en esta función. Sin datos sensibles.
    _diag_rv = "sí" if df_rv is not None and len(df_rv) else "VACÍO"
    _diag_alt = "sí" if df_alt is not None and len(df_alt) else "VACÍO"
    _diag_der = "sí" if (deriv_resultado and deriv_resultado.get('vm_cierre')) else "VACÍO"
    print(f"[SNAPSHOT-NORF] anexadas RV={len(_filas_rv)} (entrada {_diag_rv}), "
          f"Alt={len(_filas_alt)} (entrada {_diag_alt}), "
          f"Deriv={_filas_der} (entrada {_diag_der})")

    if not filas_extra:
        return df_proy_rf
    df_extra = pd.DataFrame(filas_extra)
    return pd.concat([df_proy_rf, df_extra], ignore_index=True)


def _ejecutar_escenario_dispatch(modo, *args, **kwargs):
    """
    Enruta la ejecución según el Modo_Proyeccion:
      - 'por_tramos' → orquestador por tramos.
      - cualquier otro (default 'punta_a_punta') → _ejecutar_escenario (modo actual).
    Los kwargs específicos de tramos (shifts_por_tramo, etc.) se extraen aquí.
    """
    if str(modo).strip().lower() == 'por_tramos':
        return _ejecutar_escenario_por_tramos(
            kwargs.get('fecha_inicio'), kwargs.get('fecha_fin'),
            kwargs.get('shifts_por_tramo'),
            periodicidad_por_tramo=kwargs.get('periodicidad_por_tramo'),
            variacion_anual_por_tramo=kwargs.get('variacion_anual_por_tramo'),
            trm_final=kwargs.get('trm_final'), uvr_t0=kwargs.get('uvr_t0'),
            uvr_cierre=kwargs.get('uvr_cierre'), store_senda_inf=kwargs.get('store_senda_inf'),
            repo_ini=kwargs.get('repo_ini'), repo_fin=kwargs.get('repo_fin'),
            ipc_vigente=kwargs.get('ipc_vigente'),  # arranque inflación primer tramo
            set_progress=kwargs.get('set_progress'),
            inputs_por_tramo=kwargs.get('inputs_por_tramo'),  # tarea 7.3: grid multiactivo
            metodo_deriv=kwargs.get('metodo_deriv', 1),  # derivados por tramo (Req 13)
        )
    return _ejecutar_escenario(*args, **kwargs)


# =============================================================================
# CALLBACK: RENDERIZAR RESULTADOS
# =============================================================================
@callback(
    Output('resultados-contenido', 'children'),
    Input('store-resultados-escenario', 'data'),
    prevent_initial_call=True
)
def renderizar_resultados(data):
    """Renderiza la tab de resultados con los datos del escenario."""
    if not data:
        return html.P("Sin resultados. Corre un escenario primero.", style={'color': '#999'})

    params = data.get('parametros', {})
    rf = data.get('rf_resumen')
    deriv = data.get('derivados')
    simult = data.get('simultaneas')
    rv = data.get('rv')
    alt = data.get('alternativos')
    cred = data.get('creditos')

    # Calcular totales desde rf_detalle (con lógica contable)
    rf_det = data.get('rf_detalle')
    pnl_rf = 0
    ori_rf = 0
    if rf_det:
        pnl_rf = sum(r['pyg'] for r in rf_det)
        ori_rf = sum(r['ori'] for r in rf_det)
    elif isinstance(rf, list) and rf:
        # En modo por tramos `rf_resumen` es un booleano (True), no una lista de registros;
        # solo se indexa cuando es una lista real (modo punta a punta). Evita el
        # TypeError: 'bool' object is not subscriptable que rompía la pestaña Resultados.
        pnl_rf = rf[-1].get('pnl_mercado_acum', 0)

    pnl_deriv = 0
    if deriv:
        # Solo sumar Forwards, Opciones y Swaps (excluir Activos)
        import pandas as _pd_temp
        df_d = _pd_temp.DataFrame(deriv)
        if 'fecha_valoracion' in df_d.columns and 'p_y_g' in df_d.columns:
            ultima_fecha = df_d['fecha_valoracion'].max()
            df_d_fin = df_d[df_d['fecha_valoracion'] == ultima_fecha]
            # Excluir tipo 'Activos' del P&L de derivados
            if 'tipo' in df_d_fin.columns:
                df_d_fin = df_d_fin[df_d_fin['tipo'] != 'Activos']
            pnl_deriv = float(df_d_fin['p_y_g'].sum())
        else:
            pnl_deriv = sum(r.get('p_y_g', 0) for r in deriv if r.get('tipo') != 'Activos')
    pnl_sim = -sum(r.get('costo_mensual', 0) for r in simult) if simult else 0
    pnl_rv = rv[-1].get('pyg_acum', 0) if rv else 0
    pnl_alt = alt[-1].get('pyg_acum', 0) if alt else 0
    pnl_cred = -cred[-1].get('interes_acum', 0) if cred else 0
    # Créditos NO suman al P&L ni al Total Resultado (es un costo informativo)
    total_resultado = pnl_rf + ori_rf + pnl_deriv + pnl_sim + pnl_rv + pnl_alt

    children = []

    # ─── Resumen Ejecutivo ───
    children.append(html.Div([
        html.H5("RESUMEN EJECUTIVO", style={'marginBottom': '10px',
                 'borderBottom': '2px solid #007bff', 'paddingBottom': '5px'}),
        html.Div([
            _card_metrica("TOTAL RESULTADO", total_resultado, '#007bff'),
            _card_metrica("P&L (Estado Resultados)", pnl_rf + pnl_deriv + pnl_sim + pnl_rv + pnl_alt, '#28a745'),
            _card_metrica("ORI (Patrimonio)", ori_rf, '#6f42c1'),
            _card_metrica("P&L Derivados", pnl_deriv, '#17a2b8'),
            _card_metrica("P&L Simultáneas", pnl_sim, '#e83e8c'),
            _card_metrica("P&L Renta Variable", pnl_rv, '#6f42c1'),
            _card_metrica("P&L Alternativos", pnl_alt, '#fd7e14'),
            _card_metrica("Costo Créditos", pnl_cred, '#0d6efd'),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'flexWrap': 'wrap', 'marginBottom': '15px', 'gap': '8px'}),
        html.Div([
            html.Span(f"Período: {params.get('fecha_inicio','')} → {params.get('fecha_fin','')}",
                      style={'fontSize': '11px', 'color': '#666', 'marginRight': '20px'}),
            html.Span(f"UVR: {params.get('uvr_t0','')} → {params.get('uvr_cierre','')}",
                      style={'fontSize': '11px', 'color': '#666', 'marginRight': '20px'}),
            html.Span(f"TRM final: {params.get('trm_final','')}",
                      style={'fontSize': '11px', 'color': '#666'}),
        ], style={'padding': '6px', 'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}),
    ], style={'padding': '15px', 'border': '1px solid #dee2e6',
              'borderRadius': '6px', 'marginBottom': '20px'}))

    # ─── P&L / ORI / TOTAL por Compañía ───
    if rf_det:
        df_det = pd.DataFrame(rf_det)
        # Por Compañía
        resumen_cia = df_det.groupby('compania').agg(
            pyg=('pyg', 'sum'), ori=('ori', 'sum'), total_resultado=('total_resultado', 'sum')
        ).reset_index()
        resumen_cia['pyg_fmt'] = resumen_cia['pyg'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_cia['ori_fmt'] = resumen_cia['ori'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_cia['total_fmt'] = resumen_cia['total_resultado'].apply(lambda x: f"{_fmt_cop(x)}")

        children.append(html.Div([
            html.H6("P&L / ORI / TOTAL por Compañía", style={'marginBottom': '8px'}),
            html.P("NEG = valoración a mercado | DV = causación + ORI | VC = solo causación",
                   style={'fontSize': '10px', 'color': '#666', 'marginBottom': '8px'}),
            dash_table.DataTable(
                columns=[
                    {'name': 'Compañía', 'id': 'compania'},
                    {'name': 'P&L (ER)', 'id': 'pyg_fmt'},
                    {'name': 'ORI (Patrimonio)', 'id': 'ori_fmt'},
                    {'name': 'TOTAL RESULTADO', 'id': 'total_fmt'},
                ],
                data=resumen_cia[['compania', 'pyg_fmt', 'ori_fmt', 'total_fmt']].to_dict('records'),
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'center', 'fontSize': '11px', 'padding': '5px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #dee2e6', 'borderRadius': '6px'}))

        # Por Asset Class
        resumen_ac = df_det.groupby('asset_class').agg(
            pyg=('pyg', 'sum'), ori=('ori', 'sum'), total_resultado=('total_resultado', 'sum')
        ).reset_index()
        resumen_ac['pyg_fmt'] = resumen_ac['pyg'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_ac['ori_fmt'] = resumen_ac['ori'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_ac['total_fmt'] = resumen_ac['total_resultado'].apply(lambda x: f"{_fmt_cop(x)}")

        children.append(html.Div([
            html.H6("P&L / ORI / TOTAL por Asset Class", style={'marginBottom': '8px'}),
            dash_table.DataTable(
                columns=[
                    {'name': 'Asset Class', 'id': 'asset_class'},
                    {'name': 'P&L (ER)', 'id': 'pyg_fmt'},
                    {'name': 'ORI (Patrimonio)', 'id': 'ori_fmt'},
                    {'name': 'TOTAL RESULTADO', 'id': 'total_fmt'},
                ],
                data=resumen_ac[['asset_class', 'pyg_fmt', 'ori_fmt', 'total_fmt']].to_dict('records'),
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'center', 'fontSize': '11px', 'padding': '5px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #dee2e6', 'borderRadius': '6px'}))

        # Resumen por Clasificación Contable (NEG/DV/VC)
        resumen_cl = df_det.groupby('clasificacion').agg(
            n_titulos=('id_posicion', 'count'),
            pyg=('pyg', 'sum'), ori=('ori', 'sum'), total_resultado=('total_resultado', 'sum')
        ).reset_index()
        resumen_cl['pyg_fmt'] = resumen_cl['pyg'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_cl['ori_fmt'] = resumen_cl['ori'].apply(lambda x: f"{_fmt_cop(x)}")
        resumen_cl['total_fmt'] = resumen_cl['total_resultado'].apply(lambda x: f"{_fmt_cop(x)}")

        children.append(html.Div([
            html.H6("Resumen por Clasificación Contable", style={'marginBottom': '8px'}),
            dash_table.DataTable(
                columns=[
                    {'name': 'Clasificación', 'id': 'clasificacion'},
                    {'name': '# Títulos', 'id': 'n_titulos'},
                    {'name': 'P&L (ER)', 'id': 'pyg_fmt'},
                    {'name': 'ORI', 'id': 'ori_fmt'},
                    {'name': 'TOTAL', 'id': 'total_fmt'},
                ],
                data=resumen_cl[['clasificacion', 'n_titulos', 'pyg_fmt', 'ori_fmt', 'total_fmt']].to_dict('records'),
                style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'center', 'fontSize': '11px', 'padding': '5px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
            ),
        ], style={'marginBottom': '20px', 'padding': '10px', 'border': '1px solid #dee2e6', 'borderRadius': '6px'}))

    # ─── Evolución mensual ───
    # En modo por tramos `rf` llega como booleano/resumen (rf_resumen=True), NO como lista
    # de registros mensuales. Solo se puede graficar la evolución si `rf` es una lista con
    # elementos (modo mensual clásico). Evita `ValueError: DataFrame constructor not
    # properly called!` que rompía toda la pestaña Resultados (y con ella el botón de
    # descarga del Excel).
    if isinstance(rf, list) and rf:
        df_rf = pd.DataFrame(rf)
        fig_rf = go.Figure()
        fig_rf.add_trace(go.Scatter(
            x=df_rf['fecha'], y=df_rf['pnl_mercado_acum'].apply(lambda x: x / 1e9),
            mode='lines+markers', name='P&L RF Acumulado',
            line=dict(color='#28a745', width=2), marker=dict(size=6)))
        if simult:
            df_sim_g = pd.DataFrame(simult)
            df_sim_g['costo_acum'] = df_sim_g['costo_mensual'].cumsum()
            fig_rf.add_trace(go.Scatter(
                x=df_sim_g['fecha_proyeccion'],
                y=df_sim_g['costo_acum'].apply(lambda x: -x / 1e9),
                mode='lines+markers', name='Costo Simultáneas (acum)',
                line=dict(color='#e83e8c', width=2, dash='dot'), marker=dict(size=5)))
        fig_rf.update_layout(title='Evolución Mensual P&L',
                             yaxis_title='Miles de Millones COP', height=350,
                             margin=dict(l=60, r=20, t=40, b=40),
                             legend=dict(orientation='h', y=-0.15))
        children.append(dcc.Graph(figure=fig_rf))

    # ─── Tabla RF mensual ───
    # Igual que el gráfico de evolución: en modo por tramos `rf` es booleano/resumen y no
    # se puede tabular como registros mensuales. Guarda `isinstance(rf, list)` para no
    # reventar la pestaña (y preservar el botón de descarga).
    if isinstance(rf, list) and rf:
        df_show = pd.DataFrame(rf).copy()
        df_show['vm_total'] = df_show['vm_total'].apply(lambda x: f"${x/1e12:.3f} B")
        df_show['pnl_mercado_acum'] = df_show['pnl_mercado_acum'].apply(lambda x: f"{_fmt_cop(x)}")
        df_show['cupones_mes'] = df_show['cupones_mes'].apply(lambda x: f"{_fmt_cop(x)}")
        children.append(html.Div([
            html.H6("Detalle RF Mensual", style={'marginTop': '15px'}),
            dash_table.DataTable(
                columns=[{'name': c, 'id': c} for c in ['mes', 'fecha', 'vm_total',
                          'pnl_mercado_acum', 'cupones_mes', 'n_titulos']],
                data=df_show[['mes', 'fecha', 'vm_total', 'pnl_mercado_acum',
                              'cupones_mes', 'n_titulos']].to_dict('records'),
                page_size=12, style_table={'overflowX': 'auto'},
                style_cell={'textAlign': 'center', 'fontSize': '10px', 'padding': '3px'},
                style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'}),
        ], style={'marginBottom': '20px'}))

    # ─── Derivados ───
    if deriv:
        pnl_d = sum(r.get('p_y_g', 0) for r in deriv)
        children.append(html.Div([
            html.H6("Derivados"),
            html.Span(f"P&L Total Derivados: {_fmt_cop(pnl_d)}",
                      style={'fontSize': '12px', 'fontWeight': 'bold',
                             'color': '#28a745' if pnl_d >= 0 else '#dc3545'}),
        ], style={'marginBottom': '20px', 'padding': '10px',
                  'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}))

    # ─── Renta Variable ───
    if rv:
        df_rv_g = pd.DataFrame(rv)
        pnl_rv_total = df_rv_g['pyg_acum'].iloc[-1] if 'pyg_acum' in df_rv_g.columns else 0
        fig_rv = go.Figure()
        if 'pyg_acum' in df_rv_g.columns:
            fig_rv.add_trace(go.Scatter(
                x=df_rv_g['fecha_proyeccion'], y=df_rv_g['pyg_acum'].apply(lambda x: x / 1e9),
                mode='lines+markers', name='PyG RV Acumulado',
                line=dict(color='#6f42c1', width=2), marker=dict(size=5)))
        fig_rv.update_layout(title='Renta Variable — PyG Acumulado',
                             yaxis_title='Miles de Millones COP', height=280,
                             margin=dict(l=50, r=20, t=40, b=30))
        children.append(html.Div([
            html.H6("Renta Variable"),
            html.Span(f"P&L Total RV: {_fmt_cop(pnl_rv_total)}",
                      style={'fontSize': '12px', 'fontWeight': 'bold',
                             'color': '#28a745' if pnl_rv_total >= 0 else '#dc3545'}),
            dcc.Graph(figure=fig_rv),
        ], style={'marginBottom': '20px', 'padding': '10px',
                  'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}))

    # ─── Alternativos ───
    if alt:
        df_alt_g = pd.DataFrame(alt)
        pnl_alt_total = df_alt_g['pyg_acum'].iloc[-1] if 'pyg_acum' in df_alt_g.columns else 0
        fig_alt = go.Figure()
        fig_alt.add_trace(go.Bar(
            x=df_alt_g['fecha_proyeccion'],
            y=df_alt_g['pyg_mensual'].apply(lambda x: x / 1e9),
            name='PyG Mensual', marker_color='#fd7e14'))
        fig_alt.add_trace(go.Scatter(
            x=df_alt_g['fecha_proyeccion'],
            y=df_alt_g['pyg_acum'].apply(lambda x: x / 1e9),
            name='PyG Acumulado', mode='lines+markers',
            line=dict(color='rgb(255, 194, 0)', width=2.5), marker=dict(size=5), yaxis='y2'))
        fig_alt.update_layout(
            title='Alternativos — PyG Mensual y Acumulado',
            yaxis=dict(title='PyG Mensual (Miles MM)'), height=280,
            yaxis2=dict(title='PyG Acumulado (Miles MM)', overlaying='y', side='right'),
            margin=dict(l=50, r=50, t=40, b=30),
            legend=dict(orientation='h', y=-0.15))
        children.append(html.Div([
            html.H6("Alternativos"),
            html.Span(f"P&L Total Alternativos: {_fmt_cop(pnl_alt_total)}",
                      style={'fontSize': '12px', 'fontWeight': 'bold',
                             'color': '#28a745' if pnl_alt_total >= 0 else '#dc3545'}),
            dcc.Graph(figure=fig_alt),
        ], style={'marginBottom': '20px', 'padding': '10px',
                  'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}))

    # ─── Créditos Contratados ───
    if cred:
        df_cred_g = pd.DataFrame(cred)
        costo_total_cred = df_cred_g['interes_acum'].iloc[-1] if 'interes_acum' in df_cred_g.columns else 0
        fig_cred = go.Figure()
        fig_cred.add_trace(go.Bar(
            x=df_cred_g['fecha_proyeccion'],
            y=df_cred_g['interes_mes'].apply(lambda x: x / 1e9),
            name='Costo Mensual', marker_color='#0d6efd'))
        fig_cred.add_trace(go.Scatter(
            x=df_cred_g['fecha_proyeccion'],
            y=df_cred_g['saldo_fin'].apply(lambda x: x / 1e9),
            name='Saldo Vigente', mode='lines+markers',
            line=dict(color='rgb(255, 194, 0)', width=2.5), marker=dict(size=5), yaxis='y2'))
        fig_cred.update_layout(
            title='Créditos Contratados — Costo Mensual y Saldo',
            yaxis=dict(title='Costo Mensual (Miles MM)'), height=280,
            yaxis2=dict(title='Saldo Vigente (Miles MM)', overlaying='y', side='right'),
            margin=dict(l=50, r=50, t=40, b=30),
            legend=dict(orientation='h', y=-0.15))
        children.append(html.Div([
            html.H6("Créditos Contratados"),
            html.Span(f"Costo Total Créditos: {_fmt_cop(costo_total_cred)}",
                      style={'fontSize': '12px', 'fontWeight': 'bold', 'color': '#dc3545'}),
            dcc.Graph(figure=fig_cred),
        ], style={'marginBottom': '20px', 'padding': '10px',
                  'backgroundColor': '#f8f9fa', 'borderRadius': '4px'}))

    # ─── Botón descargar ───
    children.append(html.Div([
        html.Button("📥 Descargar Excel Detallado", id='btn-descargar-resultados', n_clicks=0,
                    style={'padding': '8px 20px', 'borderRadius': '4px', 'cursor': 'pointer',
                           'backgroundColor': '#28a745', 'color': 'white', 'border': 'none',
                           'fontWeight': 'bold'}),
    ], style={'textAlign': 'center', 'marginTop': '20px'}))

    return children


def _card_metrica(titulo, valor, color='#333'):
    """Helper: Tarjeta de métrica para el resumen ejecutivo."""
    signo = '+' if valor >= 0 else ''
    return html.Div([
        html.Div(titulo, style={'fontSize': '10px', 'color': '#666', 'marginBottom': '2px'}),
        html.Div(f"{signo}{_fmt_cop(valor)}",
                 style={'fontSize': '16px', 'fontWeight': 'bold',
                        'color': '#28a745' if valor >= 0 else '#dc3545'}),
    ], style={'textAlign': 'center', 'padding': '8px 12px', 'backgroundColor': '#fff',
              'border': f'2px solid {color}', 'borderRadius': '6px', 'minWidth': '120px'})


def _escribir_hojas_store_por_modo(writer, data):
    """Escribe las hojas del store puerteadas por el modo de proyección (Defecto 3).

    En modo ``por_tramos`` el store solo contiene el ÚLTIMO tramo, de modo que las hojas
    ``Derivados``, ``RV_Mensual``, ``Alt_Mensual`` y ``Creditos`` escritas desde el store serían
    engañosas (muestran un solo corte). Las series correctas de TODOS los tramos ya existen como
    ``*_Tramos`` (Vía A, reconstruidas desde la BD), por lo que esas cuatro hojas se OMITEN en
    por tramos. No se construye ``Creditos_Tramos``.

    En modo ``punta_a_punta`` el comportamiento es idéntico al anterior: las cuatro hojas se
    escriben siempre que haya datos en el store.

    El modo se lee de ``data['parametros']['modo']`` (AC 3.1); si el campo falta se usa el valor
    seguro ``'punta_a_punta'`` (el flujo por tramos siempre fija ``'por_tramos'``). Función pura:
    solo depende de ``pd``, del ``writer`` recibido y del dict ``data`` (sin acceso a BD ni al
    estado global), para que el callback y las pruebas compartan el mismo origen de verdad.
    """
    _modo = str((data.get('parametros') or {}).get('modo') or 'punta_a_punta')
    _es_por_tramos = (_modo == 'por_tramos')

    if not _es_por_tramos and data.get('derivados'):
        pd.DataFrame(data['derivados']).to_excel(writer, sheet_name='Derivados', index=False)
    if not _es_por_tramos and data.get('rv'):
        pd.DataFrame(data['rv']).to_excel(writer, sheet_name='RV_Mensual', index=False)
    if not _es_por_tramos and data.get('alternativos'):
        pd.DataFrame(data['alternativos']).to_excel(writer, sheet_name='Alt_Mensual', index=False)
    if not _es_por_tramos and data.get('creditos'):
        pd.DataFrame(data['creditos']).to_excel(writer, sheet_name='Creditos', index=False)


@callback(
    Output('download-resultados-excel', 'data'),
    Input('btn-descargar-resultados', 'n_clicks'),
    State('store-resultados-escenario', 'data'),
    prevent_initial_call=True
)
def descargar_resultados_excel(n_clicks, data):
    """Genera Excel con los resultados del escenario."""
    if not data:
        return no_update
    import io
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        pd.DataFrame([data.get('parametros', {})]).to_excel(writer, sheet_name='Parametros', index=False)

        # --- RF Mensual con P&L contable por corte ---
        # NOTA: en modo por tramos `rf_resumen` llega como booleano (True), no como lista de
        # registros mensuales. Se exige `isinstance(..., list)` para no reventar con
        # `ValueError: DataFrame constructor not properly called!` al construir el DataFrame.
        _rf_resumen = data.get('rf_resumen')
        _rf_resumen_es_lista = isinstance(_rf_resumen, list) and len(_rf_resumen) > 0
        if data.get('rf_detalle') and _rf_resumen_es_lista:
            df_det = pd.DataFrame(data['rf_detalle'])
            df_resumen = pd.DataFrame(data['rf_resumen'])

            # --- RF Mensual: usar P&L contable calculado desde snapshots por título ---
            if data.get('rf_mensual_contable'):
                df_mensual = pd.DataFrame(data['rf_mensual_contable'])
                df_mensual['pyg_acum'] = df_mensual['pyg_mes'].cumsum()
                df_mensual['ori_acum'] = df_mensual['ori_mes'].cumsum()
                df_mensual['total_resultado_acum'] = df_mensual['total_resultado_mes'].cumsum()
                df_mensual.to_excel(writer, sheet_name='RF_Mensual', index=False)
            elif 'pnl_tir_mes' in df_resumen.columns:
                # Fallback: método anterior (agregado del motor)
                df_resumen['total_resultado_mes'] = df_resumen['pnl_mercado_mes'] + df_resumen['cupones_mes']
                df_resumen['pyg_mes'] = df_resumen['pnl_tir_mes'] + df_resumen['cupones_mes']
                df_resumen['ori_mes'] = df_resumen['total_resultado_mes'] - df_resumen['pyg_mes']
                df_resumen['total_resultado_acum'] = df_resumen['total_resultado_mes'].cumsum()
                df_resumen['pyg_acum'] = df_resumen['pyg_mes'].cumsum()
                df_resumen['ori_acum'] = df_resumen['ori_mes'].cumsum()
                df_resumen.to_excel(writer, sheet_name='RF_Mensual', index=False)
            else:
                df_resumen.to_excel(writer, sheet_name='RF_Mensual', index=False)

            # Por clasificación contable
            por_clasif = df_det.groupby('clasificacion').agg(
                n_titulos=('id_posicion', 'count'),
                vm_inicial=('vm_inicial', 'sum'),
                vm_mercado=('vm_mercado', 'sum'),
                pyg=('pyg', 'sum'), ori=('ori', 'sum'),
                total_resultado=('total_resultado', 'sum')).reset_index()
            por_clasif.to_excel(writer, sheet_name='RF_Por_Clasificacion', index=False)
        elif _rf_resumen_es_lista:
            pd.DataFrame(data['rf_resumen']).to_excel(writer, sheet_name='RF_Mensual', index=False)

        if data.get('rf_detalle'):
            df_det = pd.DataFrame(data['rf_detalle'])
            df_det.to_excel(writer, sheet_name='RF_Detalle_Titulo', index=False)
            # Resumen por Compañía
            resumen_cia = df_det.groupby('compania').agg(
                pyg=('pyg', 'sum'), ori=('ori', 'sum'),
                total_resultado=('total_resultado', 'sum')).reset_index()
            resumen_cia.to_excel(writer, sheet_name='Resumen_Compania', index=False)
            # Resumen por Asset Class
            resumen_ac = df_det.groupby('asset_class').agg(
                pyg=('pyg', 'sum'), ori=('ori', 'sum'),
                total_resultado=('total_resultado', 'sum')).reset_index()
            resumen_ac.to_excel(writer, sheet_name='Resumen_AssetClass', index=False)
            # Detalle por Ramo
            resumen_ramo = df_det.groupby(['compania', 'ramo', 'clasificacion']).agg(
                n_titulos=('id_posicion', 'count'),
                nominal=('nominal', 'sum'),
                vm_inicial=('vm_inicial', 'sum'),
                vm_mercado=('vm_mercado', 'sum'),
                vtir_fin=('vtir_fin', 'sum'),
                pyg=('pyg', 'sum'), ori=('ori', 'sum'),
                total_resultado=('total_resultado', 'sum')).reset_index()
            resumen_ramo.to_excel(writer, sheet_name='Detalle_Ramo', index=False)

        # Defecto 3: en modo por_tramos el store solo contiene el ÚLTIMO tramo, por lo que las
        # hojas Derivados / RV_Mensual / Alt_Mensual / Creditos escritas desde el store serían
        # engañosas. Las series correctas de TODOS los tramos ya existen como *_Tramos (Vía A,
        # reconstruidas desde la BD). El puerteo por modo vive en el helper puro
        # `_escribir_hojas_store_por_modo` (mismo origen de verdad para el callback y las pruebas).
        if data.get('simultaneas'):
            pd.DataFrame(data['simultaneas']).to_excel(writer, sheet_name='Simultaneas', index=False)

        # --- Renta Variable: detalle completo por título ---
        if data.get('rv_detalle'):
            df_rv_det = pd.DataFrame(data['rv_detalle'])
            df_rv_det.to_excel(writer, sheet_name='RV_Detalle_Titulo', index=False)

        # --- Alternativos: detalle completo por título ---
        if data.get('alt_detalle'):
            df_alt_det = pd.DataFrame(data['alt_detalle'])
            df_alt_det.to_excel(writer, sheet_name='Alt_Detalle_Titulo', index=False)

        # Hojas del store puerteadas por modo (Defecto 3): Derivados, RV_Mensual, Alt_Mensual,
        # Creditos. Se omiten en por_tramos; se escriben igual que antes en punta_a_punta.
        _escribir_hojas_store_por_modo(writer, data)

        if data.get('portafolio_proyectado'):
            pd.DataFrame(data['portafolio_proyectado']).to_excel(writer, sheet_name='Portafolio_Proyectado', index=False)

        # --- Efecto moneda: descomposición del PyG por activo internacional y tramo ---
        # (Tarea 8.0 / Req 11.3-11.6). Los registros provienen de EfectoMonedaTramo.to_record()
        # generados por el orquestador por tramos (tarea 5.8) y guardados en data['efecto_moneda'].
        # Se escribe una hoja de DETALLE por tramo (Efecto_Moneda) y una hoja de TOTAL del
        # horizonte por activo (Efecto_Moneda_Total).
        efecto_moneda = data.get('efecto_moneda')
        if efecto_moneda:
            df_em = pd.DataFrame(efecto_moneda)
            if not df_em.empty:
                # Mapa de nombres crudos -> nombres legibles para el negocio.
                columnas_legibles = {
                    "id_posicion": "Posición",
                    "activo": "Activo",
                    "moneda": "Moneda",
                    "tramo_indice": "Tramo",
                    "fecha_corte": "Fecha corte",
                    "vm_inicio_cop": "VM inicial (COP)",
                    "trm_inicio": "TRM inicio",
                    "trm_cierre": "TRM cierre",
                    "vm_inicio_origen": "VM inicial (USD)",
                    "vm_cierre_cop": "VM cierre (COP)",
                    "pyg_total_cop": "PyG total (COP)",
                    "efecto_moneda_cop": "Efecto moneda (COP)",
                    "efecto_retorno_cop": "Efecto retorno (COP)",
                }
                # Orden legible; solo columnas presentes en los datos (robusto a cambios de esquema).
                orden_detalle = [c for c in columnas_legibles if c in df_em.columns]
                df_detalle = df_em[orden_detalle].rename(columns=columnas_legibles)
                if "Tramo" in df_detalle.columns and "Fecha corte" in df_detalle.columns:
                    df_detalle = df_detalle.sort_values(
                        ["Posición", "Tramo"] if "Posición" in df_detalle.columns else ["Tramo"]
                    ).reset_index(drop=True)
                df_detalle.to_excel(writer, sheet_name='Efecto_Moneda', index=False)

                # TOTAL del horizonte por activo/posición: suma de efectos y PyG por id_posicion.
                claves_grupo = [c for c in ("id_posicion", "activo", "moneda") if c in df_em.columns]
                metricas_suma = [
                    c for c in ("pyg_total_cop", "efecto_moneda_cop", "efecto_retorno_cop")
                    if c in df_em.columns
                ]
                if claves_grupo and metricas_suma:
                    df_total = df_em.groupby(claves_grupo, as_index=False)[metricas_suma].sum()
                    total_legibles = {
                        "id_posicion": "Posición",
                        "activo": "Activo",
                        "moneda": "Moneda",
                        "pyg_total_cop": "PyG total (COP)",
                        "efecto_moneda_cop": "Efecto moneda (COP)",
                        "efecto_retorno_cop": "Efecto retorno (COP)",
                    }
                    df_total = df_total.rename(columns=total_legibles)
                    df_total.to_excel(writer, sheet_name='Efecto_Moneda_Total', index=False)

        # ── DETALLE COMPLETO POR TRAMO (leído de la BD, independiente del store) ──
        # Añade, por cada corte persistido en `portafolio_clasificado_proy`:
        #   - `Portafolio_<corte>`: portafolio clasificado proyectado COMPLETO del corte.
        #   - `ResumenPyG_<corte>` + `TotalCategoria_<corte>`: P&G/ORI por categoría ×
        #     compañía × ramo (y total por categoría) del corte.
        # Esto garantiza que el Excel por tramos traiga TODO el detalle que el modo mensual
        # no deja en el store (en por tramos el store solo trae el último tramo). Lectura en
        # modo RO: no interfiere con la corrida ni con el dash. Si no hay tablas por tramo
        # (modo mensual clásico), no añade nada y el Excel queda igual que antes.
        try:
            import os as _os
            import sys as _sys
            _tools_dir = _os.path.join(str(SCRIPT_DIR), "tools")
            if _tools_dir not in _sys.path:
                _sys.path.insert(0, _tools_dir)
            from exportar_excel_tramos import anexar_hojas_detalle_tramos
            _hojas = anexar_hojas_detalle_tramos(writer, str(DB_PATH))
            if _hojas:
                print(f"[DESCARGA] Hojas de detalle por tramo añadidas: {_hojas}")
        except Exception as _e_det:
            # No romper la descarga si el detalle por tramo falla: el resto del Excel ya está.
            print(f"[DESCARGA] No se pudieron añadir hojas de detalle por tramo: "
                  f"{type(_e_det).__name__}: {_e_det}")
    return dcc.send_bytes(buffer.getvalue(), filename='resultados_escenario_AXIS.xlsx')


# =============================================================================
# CALLBACK: Cargar opciones de dropdowns de estrés (títulos RV y fondos Alt)
# =============================================================================
@callback(
    [Output('stress-titulos-local', 'options'),
     Output('stress-titulos-intl', 'options'),
     Output('stress-fondos-local', 'options'),
     Output('stress-fondos-intl', 'options'),
     Output('stress-cred-individual-select', 'options')],
    Input('tabs', 'value'),
    prevent_initial_call=True
)
def cargar_opciones_stress(tab):
    """Carga opciones de dropdowns de estrés cuando se entra al tab Inputs."""
    if tab != 'tab-inputs':
        return no_update, no_update, no_update, no_update, no_update

    titulos_local_opts = []
    titulos_intl_opts = []
    fondos_local_opts = []
    fondos_intl_opts = []
    cred_opts = []

    try:
        conn = get_conn()
        # RV titles
        df_rv = pd.read_sql_query(
            "SELECT DISTINCT especie, asset_class_definitivo FROM portafolio_clasificado "
            "WHERE asset_class_definitivo IN ('COLCAP','MXWD','PEI') "
            "AND CAST(valor_mercado AS REAL) > 0 ORDER BY especie", conn)
        local_set = set(df_rv[df_rv["asset_class_definitivo"].isin(["COLCAP", "PEI"])]["especie"].tolist())
        intl_set = set(df_rv[df_rv["asset_class_definitivo"] == "MXWD"]["especie"].tolist())
        titulos_local_opts = [{"label": t, "value": t} for t in sorted(local_set)]
        titulos_intl_opts = [{"label": t, "value": t} for t in sorted(intl_set)]

        # Alt funds
        df_alt = pd.read_sql_query(
            "SELECT DISTINCT especie, moneda, asset_class_definitivo "
            "FROM portafolio_clasificado "
            "WHERE (asset_class_definitivo IN ('Fondo Capital Priv. Local','Fondo Capital Priv. Extranjero') "
            "  OR numeral_regimen IN ('1.9','1.11','1.11.infra','2.7','2.8')) "
            "AND asset_class_definitivo NOT IN ('PEI','COLCAP','MXWD') "
            "AND CAST(valor_mercado AS REAL) > 0 ORDER BY especie", conn)
        fl = set(df_alt[df_alt["asset_class_definitivo"] == "Fondo Capital Priv. Local"]["especie"].tolist())
        fl.update(df_alt[(df_alt["moneda"] == "COP") & (~df_alt["especie"].isin(fl))]["especie"].tolist())
        fi = set(df_alt[df_alt["asset_class_definitivo"] == "Fondo Capital Priv. Extranjero"]["especie"].tolist())
        fi.update(df_alt[(df_alt["moneda"] == "USD") & (~df_alt["especie"].isin(fi))]["especie"].tolist())
        fondos_local_opts = [{"label": f, "value": f} for f in sorted(fl)]
        fondos_intl_opts = [{"label": f, "value": f} for f in sorted(fi)]
        conn.close()
    except Exception:
        pass

    # Créditos
    try:
        import sys
        from proyeccion_creditos import CREDITOS_EJEMPLO
        cred_opts = [{"label": f"{r['nombre']} [{r['tasa_tipo']}]", "value": r["id_credito"]}
                     for r in CREDITOS_EJEMPLO.to_dict("records")]
    except Exception:
        pass

    return titulos_local_opts, titulos_intl_opts, fondos_local_opts, fondos_intl_opts, cred_opts


# =============================================================================
# CALLBACKS: ESTRÉS RV — Generar inputs dinámicos + ejecutar estrés
# =============================================================================
@callback(
    Output('stress-inputs-local', 'children'),
    Input('stress-titulos-local', 'value'),
    prevent_initial_call=True
)
def generar_inputs_stress_local(titulos):
    """Genera un input de retorno por cada título local seleccionado."""
    if not titulos:
        return []
    return html.Div([
        html.Div([
            html.Span(t[:25], style={'fontSize': '10px', 'fontWeight': '600',
                                      'width': '55%', 'display': 'inline-block',
                                      'overflow': 'hidden', 'textOverflow': 'ellipsis',
                                      'whiteSpace': 'nowrap', 'verticalAlign': 'middle'}),
            dcc.Input(
                id={'type': 'stress-input-local', 'index': t},
                type='number', value=-10, step=1,
                style={'width': '35%', 'display': 'inline-block', 'fontSize': '11px',
                       'verticalAlign': 'middle'}),
            html.Span(" %EA", style={'fontSize': '9px', 'color': '#666',
                                      'verticalAlign': 'middle'}),
        ], style={'marginBottom': '4px'})
        for t in titulos
    ])


@callback(
    Output('stress-inputs-intl', 'children'),
    Input('stress-titulos-intl', 'value'),
    prevent_initial_call=True
)
def generar_inputs_stress_intl(titulos):
    """Genera un input de retorno por cada título internacional seleccionado."""
    if not titulos:
        return []
    return html.Div([
        html.Div([
            html.Span(t[:25], style={'fontSize': '10px', 'fontWeight': '600',
                                      'width': '55%', 'display': 'inline-block',
                                      'overflow': 'hidden', 'textOverflow': 'ellipsis',
                                      'whiteSpace': 'nowrap', 'verticalAlign': 'middle'}),
            dcc.Input(
                id={'type': 'stress-input-intl', 'index': t},
                type='number', value=-15, step=1,
                style={'width': '35%', 'display': 'inline-block', 'fontSize': '11px',
                       'verticalAlign': 'middle'}),
            html.Span(" %EA", style={'fontSize': '9px', 'color': '#666',
                                      'verticalAlign': 'middle'}),
        ], style={'marginBottom': '4px'})
        for t in titulos
    ])


@callback(
    Output('resultado-stress-rv', 'children'),
    Input('btn-stress-rv', 'n_clicks'),
    [State('stress-titulos-local', 'value'),
     State({'type': 'stress-input-local', 'index': ALL}, 'value'),
     State('stress-titulos-intl', 'value'),
     State({'type': 'stress-input-intl', 'index': ALL}, 'value'),
     State('store-rv-base', 'data'),
     State('input-trm-final', 'value')],
    prevent_initial_call=True
)
def correr_stress_rv(n_clicks, titulos_local, retornos_local,
                     titulos_intl, retornos_intl, store_base, trm_val):
    """Reproyecta títulos seleccionados con retornos individuales y compara vs base."""
    import sys, traceback

    titulos_local = titulos_local or []
    titulos_intl = titulos_intl or []
    titulos_stress = titulos_local + titulos_intl

    if not titulos_stress:
        return html.Span("⚠️ Selecciona al menos un título para estresar.",
                         style={'color': 'orange', 'fontSize': '11px'})
    if store_base is None:
        return html.Span("⚠️ Primero ejecuta la proyección base (botón ▶️ Proyectar RV).",
                         style={'color': 'orange', 'fontSize': '11px'})

    try:
        from proyeccion_renta_variable import proyectar_bloque, generar_periodos, \
            cargar_portafolio_rv

        trm = float(trm_val or store_base.get("trm", 4200))
        fecha_ini = store_base["fecha_ini"]
        fecha_end = store_base["fecha_end"]
        tasa_local_base = store_base["tasa_local"]
        tasa_intl_base = store_base["tasa_intl"]
        tasa_pei_base = store_base["tasa_pei"]

        # Mapa título → tasa estresada
        stress_map = {}
        for i, t in enumerate(titulos_local):
            val = retornos_local[i] if i < len(retornos_local) else -10
            stress_map[t] = float(val or 0) / 100
        for i, t in enumerate(titulos_intl):
            val = retornos_intl[i] if i < len(retornos_intl) else -15
            stress_map[t] = float(val or 0) / 100

        portafolio = cargar_portafolio_rv(str(SCRIPT_DIR / 'portafolio.db'))
        periodos = generar_periodos(fecha_ini, fecha_end)

        # --- Escenario ESTRESADO ---
        resultados_stress = []
        for titulo, tasa_stress in stress_map.items():
            subset = portafolio[portafolio["titulo"] == titulo]
            if not subset.empty:
                df_s = proyectar_bloque(subset, periodos, tasa_stress, "stress")
                resultados_stress.append(df_s)

        mask_no_stress = ~portafolio["titulo"].isin(titulos_stress)
        port_ns = portafolio[mask_no_stress]
        for tipo_rv, tasa_base in [("rv_local", tasa_local_base),
                                    ("rv_internacional", tasa_intl_base),
                                    ("rv_pei", tasa_pei_base)]:
            subset = port_ns[port_ns["tipo_rv"] == tipo_rv]
            if not subset.empty:
                df_ns = proyectar_bloque(subset, periodos, tasa_base, "stress")
                resultados_stress.append(df_ns)

        df_stress = pd.concat(resultados_stress, ignore_index=True)
        df_stress.loc[df_stress["tipo_rv"] == "rv_pei", "tipo_rv"] = "rv_local"

        # --- Escenario BASE ---
        resultados_base = []
        for tipo_rv, tasa_base in [("rv_local", tasa_local_base),
                                    ("rv_internacional", tasa_intl_base),
                                    ("rv_pei", tasa_pei_base)]:
            subset = portafolio[portafolio["tipo_rv"] == tipo_rv]
            if not subset.empty:
                df_b = proyectar_bloque(subset, periodos, tasa_base, "base")
                resultados_base.append(df_b)

        df_base = pd.concat(resultados_base, ignore_index=True)
        df_base.loc[df_base["tipo_rv"] == "rv_pei", "tipo_rv"] = "rv_local"

        # Nota: portafolio_clasificado ya tiene valor_mercado en COP
        # (incluso para MXWD), NO se requiere conversión TRM.

        pyg_base_total = df_base["pyg_mensual"].sum()
        pyg_stress_total = df_stress["pyg_mensual"].sum()
        delta_pyg = pyg_stress_total - pyg_base_total

        # Gráfica comparativa
        base_mes = (df_base.groupby("fecha_proyeccion").agg(pyg=("pyg_mensual", "sum"))
                    .reset_index().sort_values("fecha_proyeccion"))
        base_mes["pyg_acum"] = base_mes["pyg"].cumsum()
        stress_mes = (df_stress.groupby("fecha_proyeccion").agg(pyg=("pyg_mensual", "sum"))
                      .reset_index().sort_values("fecha_proyeccion"))
        stress_mes["pyg_acum"] = stress_mes["pyg"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=base_mes["fecha_proyeccion"], y=base_mes["pyg"] / 1e9,
                             name="Base", marker_color="rgb(0,144,86)", yaxis="y"))
        fig.add_trace(go.Bar(x=stress_mes["fecha_proyeccion"], y=stress_mes["pyg"] / 1e9,
                             name="Estrés", marker_color="rgb(230,126,34)", yaxis="y"))
        fig.add_trace(go.Scatter(x=base_mes["fecha_proyeccion"], y=base_mes["pyg_acum"] / 1e9,
                                 name="Base (acum)", mode="lines+markers",
                                 line=dict(color="rgb(0,100,60)", width=2.5), marker=dict(size=4), yaxis="y2"))
        fig.add_trace(go.Scatter(x=stress_mes["fecha_proyeccion"], y=stress_mes["pyg_acum"] / 1e9,
                                 name="Estrés (acum)", mode="lines+markers",
                                 line=dict(color="rgb(150,40,30)", width=2.5, dash="dash"), marker=dict(size=4), yaxis="y2"))
        fig.update_layout(
            title=f"Estrés RV: {len(titulos_local)} local + {len(titulos_intl)} intl",
            barmode="group", yaxis=dict(title="PyG Mensual (Miles MM)"),
            yaxis2=dict(title="PyG Acumulado (Miles MM)", overlaying="y", side="right"),
            xaxis=dict(tickangle=-45), height=350,
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            margin=dict(l=50, r=50, t=50, b=50))

        # Tabla impacto por título estresado
        pyg_base_tit = (df_base[df_base["titulo"].isin(titulos_stress)]
                        .groupby(["titulo", "tipo_rv"]).agg(pyg_base=("pyg_mensual", "sum")).reset_index())
        pyg_stress_tit = (df_stress[df_stress["titulo"].isin(titulos_stress)]
                          .groupby(["titulo", "tipo_rv"]).agg(pyg_stress=("pyg_mensual", "sum")).reset_index())
        comparativa = pyg_base_tit.merge(pyg_stress_tit, on=["titulo", "tipo_rv"], how="outer").fillna(0)
        comparativa["delta"] = comparativa["pyg_stress"] - comparativa["pyg_base"]
        comp_display = comparativa.copy()
        for col in ["pyg_base", "pyg_stress", "delta"]:
            comp_display[col] = comp_display[col].apply(lambda x: f"${x:,.0f}")

        delta_color = "rgb(192,57,43)" if delta_pyg < 0 else "rgb(39,174,96)"

        return html.Div([
            html.Div([
                html.Span("⚡ Estrés RV: ", style={'fontWeight': '600', 'fontSize': '12px'}),
                html.Span(f"ΔPyG = {_fmt_cop(delta_pyg)} ",
                          style={'fontWeight': '700', 'fontSize': '12px', 'color': delta_color}),
            ], style={'marginBottom': '5px'}),
            dcc.Graph(figure=fig, style={'marginTop': '10px'}),
            html.H6("Impacto por Título", style={'fontSize': '11px', 'marginTop': '10px'}),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in comp_display.columns],
                data=comp_display.to_dict("records"),
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "fontSize": "10px", "padding": "4px"},
                style_header={"fontWeight": "bold", "backgroundColor": "#f3e5f5"},
                page_size=15, sort_action="native"),
        ])
    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"❌ {e}", style={'color': 'red', 'fontSize': '11px'}),
            html.Pre(tb, style={'fontSize': '9px', 'maxHeight': '150px', 'overflowY': 'auto',
                                'backgroundColor': '#fff3f3', 'padding': '5px', 'marginTop': '5px'}),
        ])


# =============================================================================
# CALLBACKS: ESTRÉS CRÉDITOS
# =============================================================================
@callback(
    Output('resultado-stress-cred', 'children'),
    Input('btn-stress-cred', 'n_clicks'),
    [State('stress-cred-ipc', 'value'),
     State('stress-cred-ibr', 'value'),
     State('stress-cred-sofr', 'value'),
     State('stress-cred-trm-ini', 'value'),
     State('stress-cred-trm-fin', 'value'),
     State('store-cred-base', 'data'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def correr_stress_creditos(n_clicks, ipc_stress, ibr_stress, sofr_stress,
                           trm_stress_ini, trm_stress_fin, store_base,
                           fecha_inicio, fecha_fin):
    """Reproyecta créditos con tasas estresadas y compara vs base."""
    import sys, traceback

    if store_base is None:
        return html.Span("⚠️ Primero ejecuta la proyección base.",
                         style={'color': 'orange', 'fontSize': '11px'})

    try:
        from proyeccion_creditos import proyectar_creditos_contratados

        fecha_ini = store_base["fecha_ini"]
        fecha_end = pd.to_datetime(store_base["fecha_end"])
        ipc_base = store_base["ipc"]
        ibr_base = store_base["ibr"]

        ipc_s = float(ipc_stress or 9.0) / 100
        ibr_s = float(ibr_stress or 9.0) / 100
        sofr_s = float(sofr_stress or 6.5) / 100
        trm_s_ini = float(trm_stress_ini or 4500)
        trm_s_fin = float(trm_stress_fin or 4800)

        periodos = pd.date_range(start=f"{fecha_ini}-01", end=fecha_end, freq="MS")
        n_per = len(periodos)

        # Sendas base
        df_ipc_base = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ipc": [ipc_base] * n_per})
        df_ibr_base = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ibr": [ibr_base] * n_per})

        # Sendas estresadas
        df_ipc_stress = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ipc": [ipc_s] * n_per})
        df_ibr_stress = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ibr": [ibr_s] * n_per})

        # Proyectar base
        df_base = proyectar_creditos_contratados(
            fecha_inicio=fecha_ini, df_senda_ipc=df_ipc_base, df_senda_ibr=df_ibr_base)

        # Proyectar estrés
        df_stress_r = proyectar_creditos_contratados(
            fecha_inicio=fecha_ini, df_senda_ipc=df_ipc_stress, df_senda_ibr=df_ibr_stress)

        if df_base.empty or df_stress_r.empty:
            return html.Span("⚠️ Sin resultados.", style={'color': 'orange'})

        costo_base = df_base["interes_mes"].sum()
        costo_stress = df_stress_r["interes_mes"].sum()
        delta_costo = costo_stress - costo_base

        # Gráfica mensual
        base_mes = (df_base.groupby("fecha_proyeccion").agg(costo=("interes_mes", "sum"))
                    .reset_index().sort_values("fecha_proyeccion"))
        base_mes["costo_acum"] = base_mes["costo"].cumsum()
        stress_mes = (df_stress_r.groupby("fecha_proyeccion").agg(costo=("interes_mes", "sum"))
                      .reset_index().sort_values("fecha_proyeccion"))
        stress_mes["costo_acum"] = stress_mes["costo"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=base_mes["fecha_proyeccion"], y=base_mes["costo"] / 1e9,
                             name="Base", marker_color="rgb(0,144,86)", yaxis="y"))
        fig.add_trace(go.Bar(x=stress_mes["fecha_proyeccion"], y=stress_mes["costo"] / 1e9,
                             name="Estrés", marker_color="rgb(230,126,34)", yaxis="y"))
        fig.add_trace(go.Scatter(x=base_mes["fecha_proyeccion"], y=base_mes["costo_acum"] / 1e9,
                                 name="Base (acum)", mode="lines+markers",
                                 line=dict(color="rgb(0,100,60)", width=2.5), marker=dict(size=4), yaxis="y2"))
        fig.add_trace(go.Scatter(x=stress_mes["fecha_proyeccion"], y=stress_mes["costo_acum"] / 1e9,
                                 name="Estrés (acum)", mode="lines+markers",
                                 line=dict(color="rgb(150,40,30)", width=2.5, dash="dash"), marker=dict(size=4), yaxis="y2"))
        fig.update_layout(
            title=f"Estrés Créditos: IPC {ipc_s:.1%} | IBR {ibr_s:.1%}",
            barmode="group", yaxis=dict(title="Costo Mensual (Miles MM)"),
            yaxis2=dict(title="Acumulado (Miles MM)", overlaying="y", side="right"),
            xaxis=dict(tickangle=-45), height=350,
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            margin=dict(l=50, r=50, t=50, b=50))

        delta_color = "rgb(192,57,43)" if delta_costo > 0 else "rgb(39,174,96)"

        return html.Div([
            html.Div([
                html.Span("⚡ Estrés Créditos: ", style={'fontWeight': '600', 'fontSize': '12px'}),
                html.Span(f"ΔCosto = {_fmt_cop(delta_costo)} ",
                          style={'fontWeight': '700', 'fontSize': '12px', 'color': delta_color}),
            ], style={'marginBottom': '5px'}),
            dcc.Graph(figure=fig, style={'marginTop': '10px'}),
        ])
    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"❌ {e}", style={'color': 'red', 'fontSize': '11px'}),
            html.Pre(tb, style={'fontSize': '9px', 'maxHeight': '150px', 'overflowY': 'auto',
                                'backgroundColor': '#fff3f3', 'padding': '5px', 'marginTop': '5px'}),
        ])


# =============================================================================
# CALLBACKS: ESTRÉS CRÉDITOS INDIVIDUAL
# =============================================================================
@callback(
    Output('stress-inputs-cred-individual', 'children'),
    Input('stress-cred-individual-select', 'value'),
    prevent_initial_call=True
)
def generar_inputs_stress_cred_ind(creditos_sel):
    """Genera inputs dinámicos por crédito seleccionado."""
    if not creditos_sel:
        return []
    import sys
    try:
        from proyeccion_creditos import CREDITOS_EJEMPLO
        cred_info = {r["id_credito"]: r for r in CREDITOS_EJEMPLO.to_dict("records")}
    except Exception:
        cred_info = {}

    rows = []
    for cid in creditos_sel:
        info = cred_info.get(cid, {})
        nombre = info.get("nombre", cid)[:35] if info else str(cid)[:35]
        tasa_tipo = info.get("tasa_tipo", "fija")
        if tasa_tipo in ("fija", "fija_usd"):
            label = "Tasa (%)"
            default = (info.get("tasa_fija", 0.10) * 100) + 2
        elif tasa_tipo == "ipc":
            label = "Spread IPC (%)"
            default = (info.get("spread", 0.035) * 100) + 2
        elif tasa_tipo == "ibr":
            label = "Spread IBR (%)"
            default = (info.get("spread", 0.02) * 100) + 2
        else:
            label = "Tasa (%)"
            default = 10.0

        rows.append(
            html.Div([
                html.Span(f"{nombre} [{tasa_tipo}]",
                          style={'fontSize': '10px', 'fontWeight': '600',
                                 'width': '50%', 'display': 'inline-block',
                                 'overflow': 'hidden', 'textOverflow': 'ellipsis',
                                 'whiteSpace': 'nowrap', 'verticalAlign': 'middle'}),
                dcc.Input(
                    id={'type': 'stress-input-cred-ind', 'index': cid},
                    type='number', value=round(default, 2), step=0.1,
                    style={'width': '20%', 'display': 'inline-block', 'fontSize': '11px',
                           'verticalAlign': 'middle'}),
                html.Span(f" {label}", style={'fontSize': '9px', 'color': '#666',
                                              'verticalAlign': 'middle', 'marginLeft': '5px'}),
            ], style={'marginBottom': '4px'})
        )
    return html.Div(rows)


@callback(
    Output('resultado-stress-cred-ind', 'children'),
    Input('btn-stress-cred-ind', 'n_clicks'),
    [State('stress-cred-individual-select', 'value'),
     State({'type': 'stress-input-cred-ind', 'index': ALL}, 'value'),
     State('store-cred-base', 'data'),
     State('sidebar-fecha-inicio', 'date'),
     State('sidebar-fecha-fin', 'date')],
    prevent_initial_call=True
)
def correr_stress_cred_individual(n_clicks, creditos_sel, valores_stress,
                                  store_base, fecha_inicio, fecha_fin):
    """Reproyecta créditos seleccionados con su variable modificada."""
    import sys, traceback

    if not creditos_sel:
        return html.Span("⚠️ Selecciona al menos un crédito.",
                         style={'color': 'orange', 'fontSize': '11px'})
    if store_base is None:
        return html.Span("⚠️ Primero ejecuta la proyección base.",
                         style={'color': 'orange', 'fontSize': '11px'})

    try:
        from proyeccion_creditos import proyectar_creditos_contratados, CREDITOS_EJEMPLO

        fecha_ini = store_base["fecha_ini"]
        fecha_end = pd.to_datetime(store_base["fecha_end"])
        ipc_base = store_base["ipc"]
        ibr_base = store_base["ibr"]

        periodos = pd.date_range(start=f"{fecha_ini}-01", end=fecha_end, freq="MS")
        n_per = len(periodos)

        df_ipc = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ipc": [ipc_base] * n_per})
        df_ibr = pd.DataFrame({"fecha": [p.strftime("%Y-%m-%d") for p in periodos], "ibr": [ibr_base] * n_per})

        # Base
        df_base = proyectar_creditos_contratados(
            fecha_inicio=fecha_ini, df_senda_ipc=df_ipc, df_senda_ibr=df_ibr)

        # Créditos modificados
        cred_info = {r["id_credito"]: r for r in CREDITOS_EJEMPLO.to_dict("records")}
        df_creditos_stress = CREDITOS_EJEMPLO.copy()

        for i, cid in enumerate(creditos_sel):
            val = valores_stress[i] if i < len(valores_stress) else None
            if val is None:
                continue
            val_dec = float(val) / 100
            info = cred_info.get(cid, {})
            tasa_tipo = info.get("tasa_tipo", "fija")
            idx = df_creditos_stress[df_creditos_stress["id_credito"] == cid].index
            if tasa_tipo in ("fija", "fija_usd"):
                df_creditos_stress.loc[idx, "tasa_fija"] = val_dec
            else:
                df_creditos_stress.loc[idx, "spread"] = val_dec

        df_stress_r = proyectar_creditos_contratados(
            fecha_inicio=fecha_ini, df_creditos=df_creditos_stress,
            df_senda_ipc=df_ipc, df_senda_ibr=df_ibr)

        if df_base.empty or df_stress_r.empty:
            return html.Span("⚠️ Sin resultados.", style={'color': 'orange'})

        costo_base = df_base["interes_mes"].sum()
        costo_stress = df_stress_r["interes_mes"].sum()
        delta_costo = costo_stress - costo_base

        base_mes = (df_base.groupby("fecha_proyeccion").agg(costo=("interes_mes", "sum"))
                    .reset_index().sort_values("fecha_proyeccion"))
        base_mes["costo_acum"] = base_mes["costo"].cumsum()
        stress_mes = (df_stress_r.groupby("fecha_proyeccion").agg(costo=("interes_mes", "sum"))
                      .reset_index().sort_values("fecha_proyeccion"))
        stress_mes["costo_acum"] = stress_mes["costo"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=base_mes["fecha_proyeccion"], y=base_mes["costo"] / 1e9,
                             name="Base", marker_color="rgb(0,144,86)", yaxis="y"))
        fig.add_trace(go.Bar(x=stress_mes["fecha_proyeccion"], y=stress_mes["costo"] / 1e9,
                             name="Estrés", marker_color="rgb(230,126,34)", yaxis="y"))
        fig.add_trace(go.Scatter(x=base_mes["fecha_proyeccion"], y=base_mes["costo_acum"] / 1e9,
                                 name="Base (acum)", mode="lines+markers",
                                 line=dict(color="rgb(0,100,60)", width=2.5), marker=dict(size=4), yaxis="y2"))
        fig.add_trace(go.Scatter(x=stress_mes["fecha_proyeccion"], y=stress_mes["costo_acum"] / 1e9,
                                 name="Estrés (acum)", mode="lines+markers",
                                 line=dict(color="rgb(150,40,30)", width=2.5, dash="dash"), marker=dict(size=4), yaxis="y2"))

        cred_names = [cred_info.get(c, {}).get("nombre", c)[:20] for c in creditos_sel]
        fig.update_layout(
            title=f"Estrés Individual: {', '.join(cred_names[:3])}{'...' if len(cred_names) > 3 else ''}",
            barmode="group", yaxis=dict(title="Costo Mensual (Miles MM)"),
            yaxis2=dict(title="Acumulado (Miles MM)", overlaying="y", side="right"),
            xaxis=dict(tickangle=-45), height=350,
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            margin=dict(l=50, r=50, t=50, b=50))

        delta_color = "rgb(192,57,43)" if delta_costo > 0 else "rgb(39,174,96)"

        return html.Div([
            html.Div([
                html.Span("⚡ Estrés Individual: ", style={'fontWeight': '600', 'fontSize': '12px'}),
                html.Span(f"ΔCosto = {_fmt_cop(delta_costo)} ",
                          style={'fontWeight': '700', 'fontSize': '12px', 'color': delta_color}),
            ], style={'marginBottom': '5px'}),
            dcc.Graph(figure=fig, style={'marginTop': '10px'}),
        ])
    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"❌ {e}", style={'color': 'red', 'fontSize': '11px'}),
            html.Pre(tb, style={'fontSize': '9px', 'maxHeight': '150px', 'overflowY': 'auto',
                                'backgroundColor': '#fff3f3', 'padding': '5px', 'marginTop': '5px'}),
        ])


# =============================================================================
# CALLBACKS: ESTRÉS ALTERNATIVOS
# =============================================================================
@callback(
    Output('stress-inputs-alt-local', 'children'),
    Input('stress-fondos-local', 'value'),
    prevent_initial_call=True
)
def generar_inputs_stress_alt_local(fondos):
    """Genera inputs por fondo local seleccionado."""
    if not fondos:
        return []
    import sys
    try:
        from proyeccion_alternativos import FCP_4G_NOMBRES
        fcp4g_set = set(FCP_4G_NOMBRES)
    except Exception:
        fcp4g_set = set()

    rows = []
    for f in fondos:
        is_4g = f in fcp4g_set
        label = "Spread %" if is_4g else "%EA"
        default_val = 3.5 if is_4g else -10
        rows.append(
            html.Div([
                html.Span(f[:30], style={'fontSize': '10px', 'fontWeight': '600',
                                         'width': '50%', 'display': 'inline-block',
                                         'overflow': 'hidden', 'textOverflow': 'ellipsis',
                                         'whiteSpace': 'nowrap', 'verticalAlign': 'middle'}),
                dcc.Input(
                    id={'type': 'stress-input-alt-local', 'index': f},
                    type='number', value=default_val, step=0.5,
                    style={'width': '25%', 'display': 'inline-block', 'fontSize': '11px',
                           'verticalAlign': 'middle'}),
                html.Span(f" {label}", style={'fontSize': '9px', 'color': '#666',
                                              'verticalAlign': 'middle'}),
                html.Span(" (IPC+spread)" if is_4g else "",
                          style={'fontSize': '8px', 'color': '#8e44ad',
                                 'verticalAlign': 'middle', 'marginLeft': '3px'}),
            ], style={'marginBottom': '4px'})
        )
    return html.Div(rows)


@callback(
    Output('stress-inputs-alt-intl', 'children'),
    Input('stress-fondos-intl', 'value'),
    prevent_initial_call=True
)
def generar_inputs_stress_alt_intl(fondos):
    """Genera inputs por fondo internacional seleccionado."""
    if not fondos:
        return []
    return html.Div([
        html.Div([
            html.Span(f[:30], style={'fontSize': '10px', 'fontWeight': '600',
                                     'width': '55%', 'display': 'inline-block',
                                     'overflow': 'hidden', 'textOverflow': 'ellipsis',
                                     'whiteSpace': 'nowrap', 'verticalAlign': 'middle'}),
            dcc.Input(
                id={'type': 'stress-input-alt-intl', 'index': f},
                type='number', value=-15, step=1,
                style={'width': '35%', 'display': 'inline-block', 'fontSize': '11px',
                       'verticalAlign': 'middle'}),
            html.Span(" %EA", style={'fontSize': '9px', 'color': '#666',
                                     'verticalAlign': 'middle'}),
        ], style={'marginBottom': '4px'})
        for f in fondos
    ])


@callback(
    Output('resultado-stress-alt', 'children'),
    Input('btn-stress-alt', 'n_clicks'),
    [State('stress-fondos-local', 'value'),
     State({'type': 'stress-input-alt-local', 'index': ALL}, 'value'),
     State('stress-fondos-intl', 'value'),
     State({'type': 'stress-input-alt-intl', 'index': ALL}, 'value'),
     State('store-alt-base', 'data')],
    prevent_initial_call=True
)
def correr_stress_alt(n_clicks, fondos_local, retornos_local,
                      fondos_intl, retornos_intl, store_base):
    """Reproyecta fondos seleccionados con retornos individuales y compara vs base."""
    import sys, traceback

    fondos_local = fondos_local or []
    fondos_intl = fondos_intl or []
    fondos_stress = fondos_local + fondos_intl

    if not fondos_stress:
        return html.Span("⚠️ Selecciona al menos un fondo para estresar.",
                         style={'color': 'orange', 'fontSize': '11px'})
    if store_base is None:
        return html.Span("⚠️ Primero ejecuta la proyección base.",
                         style={'color': 'orange', 'fontSize': '11px'})

    try:
        from proyeccion_alternativos import (cargar_portafolio_alternativos,
            proyectar_bloque, generar_periodos,
            proyectar_bloque_ipc_spread, SPREAD_IPC_DEFAULT)

        fecha_ini = store_base["fecha_ini"]
        fecha_end = store_base["fecha_end"]
        tasa_local_base = store_base["tasa_local"]
        tasa_intl_base = store_base["tasa_intl"]
        ipc_val = store_base["ipc_val"]
        spread_val = store_base["spread_val"]

        # Mapa fondo → valor estresado
        stress_map = {}
        for i, f in enumerate(fondos_local):
            val = retornos_local[i] if i < len(retornos_local) else -10
            stress_map[f] = float(val or 0) / 100
        for i, f in enumerate(fondos_intl):
            val = retornos_intl[i] if i < len(retornos_intl) else -15
            stress_map[f] = float(val or 0) / 100

        portafolio = cargar_portafolio_alternativos(str(SCRIPT_DIR / 'portafolio.db'))
        periodos = generar_periodos(fecha_ini, fecha_end)
        senda_ipc = {p.strftime("%Y-%m-%d"): ipc_val for p in periodos}

        # Identificar fondos IPC dinámicamente por columna es_ipc
        mask_ipc = portafolio["es_ipc"] == "SI"
        nombres_ipc = set(portafolio.loc[mask_ipc, "fondo"].tolist())
        spreads_4g = {int(row["id_posicion"]): spread_val for _, row in portafolio[mask_ipc].iterrows()}

        # --- Escenario ESTRESADO ---
        resultados_stress = []
        for fondo, val_stress in stress_map.items():
            subset = portafolio[portafolio["fondo"] == fondo]
            if subset.empty:
                continue
            if fondo in nombres_ipc:
                spreads_stress = {int(row["id_posicion"]): val_stress for _, row in subset.iterrows()}
                df_s = proyectar_bloque_ipc_spread(subset, periodos, senda_ipc, spreads_stress, "stress")
            else:
                df_s = proyectar_bloque(subset, periodos, val_stress, "stress")
            resultados_stress.append(df_s)

        # No estresados
        mask_no_stress = ~portafolio["fondo"].isin(fondos_stress)
        port_ns = portafolio[mask_no_stress]

        mask_ipc_ns = port_ns["es_ipc"] == "SI"
        port_ipc_ns = port_ns[mask_ipc_ns]
        port_ns_resto = port_ns[~mask_ipc_ns]

        if not port_ipc_ns.empty:
            spreads_ns = {int(row["id_posicion"]): spread_val for _, row in port_ipc_ns.iterrows()}
            df_ns_ipc = proyectar_bloque_ipc_spread(port_ipc_ns, periodos, senda_ipc, spreads_ns, "stress")
            resultados_stress.append(df_ns_ipc)

        for tipo_fondo, tasa_base in [("alt_local", tasa_local_base),
                                       ("alt_internacional", tasa_intl_base)]:
            subset = port_ns_resto[port_ns_resto["tipo_fondo"] == tipo_fondo]
            if not subset.empty:
                df_ns = proyectar_bloque(subset, periodos, tasa_base, "stress")
                resultados_stress.append(df_ns)

        df_stress = pd.concat(resultados_stress, ignore_index=True)

        # --- Escenario BASE ---
        resultados_base = []
        mask_ipc_base = portafolio["es_ipc"] == "SI"
        port_ipc_base = portafolio[mask_ipc_base]
        port_resto = portafolio[~mask_ipc_base]

        if not port_ipc_base.empty:
            spreads_base = {int(row["id_posicion"]): spread_val for _, row in port_ipc_base.iterrows()}
            df_b_ipc = proyectar_bloque_ipc_spread(port_ipc_base, periodos, senda_ipc, spreads_base, "base")
            resultados_base.append(df_b_ipc)

        for tipo_fondo, tasa_base in [("alt_local", tasa_local_base),
                                       ("alt_internacional", tasa_intl_base)]:
            subset = port_resto[port_resto["tipo_fondo"] == tipo_fondo]
            if not subset.empty:
                df_b = proyectar_bloque(subset, periodos, tasa_base, "base")
                resultados_base.append(df_b)

        df_base = pd.concat(resultados_base, ignore_index=True)

        pyg_base_total = df_base["pyg_mensual"].sum()
        pyg_stress_total = df_stress["pyg_mensual"].sum()
        delta_pyg = pyg_stress_total - pyg_base_total

        # Gráfica
        base_mes = (df_base.groupby("fecha_proyeccion").agg(pyg=("pyg_mensual", "sum"))
                    .reset_index().sort_values("fecha_proyeccion"))
        base_mes["pyg_acum"] = base_mes["pyg"].cumsum()
        stress_mes = (df_stress.groupby("fecha_proyeccion").agg(pyg=("pyg_mensual", "sum"))
                      .reset_index().sort_values("fecha_proyeccion"))
        stress_mes["pyg_acum"] = stress_mes["pyg"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Bar(x=base_mes["fecha_proyeccion"], y=base_mes["pyg"] / 1e9,
                             name="Base", marker_color="rgb(0,144,86)", yaxis="y"))
        fig.add_trace(go.Bar(x=stress_mes["fecha_proyeccion"], y=stress_mes["pyg"] / 1e9,
                             name="Estrés", marker_color="rgb(230,126,34)", yaxis="y"))
        fig.add_trace(go.Scatter(x=base_mes["fecha_proyeccion"], y=base_mes["pyg_acum"] / 1e9,
                                 name="Base (acum)", mode="lines+markers",
                                 line=dict(color="rgb(0,100,60)", width=2.5), marker=dict(size=4), yaxis="y2"))
        fig.add_trace(go.Scatter(x=stress_mes["fecha_proyeccion"], y=stress_mes["pyg_acum"] / 1e9,
                                 name="Estrés (acum)", mode="lines+markers",
                                 line=dict(color="rgb(150,40,30)", width=2.5, dash="dash"), marker=dict(size=4), yaxis="y2"))

        fig.update_layout(
            title=f"Estrés Alt: {len(fondos_local)} local + {len(fondos_intl)} intl",
            barmode="group", yaxis=dict(title="PyG Mensual (Miles MM)"),
            yaxis2=dict(title="PyG Acumulado (Miles MM)", overlaying="y", side="right"),
            xaxis=dict(tickangle=-45), height=350,
            legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
            margin=dict(l=50, r=50, t=50, b=50))

        # Tabla por fondo estresado
        pyg_base_tit = (df_base[df_base["fondo"].isin(fondos_stress)]
                        .groupby(["fondo", "tipo_fondo"]).agg(pyg_base=("pyg_mensual", "sum")).reset_index())
        pyg_stress_tit = (df_stress[df_stress["fondo"].isin(fondos_stress)]
                          .groupby(["fondo", "tipo_fondo"]).agg(pyg_stress=("pyg_mensual", "sum")).reset_index())
        comparativa = pyg_base_tit.merge(pyg_stress_tit, on=["fondo", "tipo_fondo"], how="outer").fillna(0)
        comparativa["delta"] = comparativa["pyg_stress"] - comparativa["pyg_base"]
        comp_display = comparativa.copy()
        for col in ["pyg_base", "pyg_stress", "delta"]:
            comp_display[col] = comp_display[col].apply(lambda x: f"${x:,.0f}")

        delta_color = "rgb(192,57,43)" if delta_pyg < 0 else "rgb(39,174,96)"

        return html.Div([
            html.Div([
                html.Span("⚡ Estrés Alt: ", style={'fontWeight': '600', 'fontSize': '12px'}),
                html.Span(f"ΔPyG = {_fmt_cop(delta_pyg)} ",
                          style={'fontWeight': '700', 'fontSize': '12px', 'color': delta_color}),
            ], style={'marginBottom': '5px'}),
            dcc.Graph(figure=fig, style={'marginTop': '10px'}),
            html.H6("Impacto por Fondo", style={'fontSize': '11px', 'marginTop': '10px'}),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in comp_display.columns],
                data=comp_display.to_dict("records"),
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "fontSize": "10px", "padding": "4px"},
                style_header={"fontWeight": "bold", "backgroundColor": "#f3e5f5"},
                page_size=15, sort_action="native"),
        ])
    except Exception as e:
        tb = traceback.format_exc()
        return html.Div([
            html.Span(f"❌ {e}", style={'color': 'red', 'fontSize': '11px'}),
            html.Pre(tb, style={'fontSize': '9px', 'maxHeight': '150px', 'overflowY': 'auto',
                                'backgroundColor': '#fff3f3', 'padding': '5px', 'marginTop': '5px'}),
        ])


# =============================================================================
# PESTAÑA AJUSTES: Reservas (clasificación de ramos + parámetros de plazo)
# Feature: compras-ventas-reservas (tareas 15.1, 15.2, 15.4)
# =============================================================================

def _ramos_del_portafolio_inicial() -> list:
    """
    Ramos del portafolio INICIAL (`portafolio_clasificado`) normalizados.

    Se usa como universo de ramos para la clasificación y para la agrupación
    demás-ramos del Excel de reservas. Lectura de solo lectura y parametrizada.
    """
    from compras_ventas_reservas import _normalizar_ramo
    try:
        conn = get_conn()
        df = pd.read_sql_query("SELECT DISTINCT ramo FROM portafolio_clasificado", conn)
        conn.close()
    except Exception:
        return []
    ramos = set()
    for valor in df.get("ramo", pd.Series(dtype=str)).tolist():
        if valor is None:
            continue
        norm = _normalizar_ramo(valor)
        if norm:
            ramos.add(norm)
    return sorted(ramos)


def _opciones_ramos_pendientes() -> list:
    """
    Lista de ramos pendientes por clasificar, incluyendo los grupos derivados que
    realmente produce la derivación de reservas y que existen en el portafolio
    (`DEMAS RAMOS DE SEGUROS DE VIDA`, `DEMAS RAMOS DE SEGUROS GENERALES`,
    `CAPITALIZADORA`).

    Nota: estos son los nombres de destino del mapeo de reservas (los que circulan por
    el sistema y aparecen en `portafolio_clasificado`), NO las constantes
    `RAMO_DEMAS_BOLIVAR`/`RAMO_DEMAS_COMERCIALES` ("DEMÁS RAMOS BOLÍVAR/COMERCIALES"),
    que pertenecen a otra convención y no corresponden a ningún ramo real.
    """
    from comun.rutas import DB_PATH as _DB
    from compras_ventas_reservas import (
        cargar_ramos_reserva_matematica,
        ramos_pendientes_por_clasificar,
    )
    ramos_port = list(_ramos_del_portafolio_inicial())
    # Grupos derivados que también deben poder clasificarse (nombres reales de destino).
    for grupo in ("DEMAS RAMOS DE SEGUROS DE VIDA", "DEMAS RAMOS DE SEGUROS GENERALES",
                  "CAPITALIZADORA"):
        if grupo not in ramos_port:
            ramos_port.append(grupo)
    try:
        config = cargar_ramos_reserva_matematica(str(_DB))
        pendientes = ramos_pendientes_por_clasificar(ramos_port, config)
    except Exception:
        pendientes = ramos_port
    return sorted(pendientes)


def layout_ajustes_reservas():
    """Pestaña de Ajustes de la feature compras-ventas-reservas."""
    from comun.rutas import DB_PATH as _DB
    from compras_ventas_reservas import cargar_parametros_reservas

    try:
        parametros = cargar_parametros_reservas(str(_DB))
        nodo_actual = parametros.nodo_compra_no_matematica_anios
        plazo_actual = parametros.plazo_max_venta_anios
    except Exception:
        nodo_actual, plazo_actual = 5, 10

    pendientes = _opciones_ramos_pendientes()
    opciones_ramo = [{"label": r, "value": r} for r in pendientes]

    return html.Div([
        html.H4("⚙️ Ajustes — Compras/Ventas por Reservas", style={'fontSize': '15px'}),

        # ── 15.1: Carga del Excel de reservas ──
        html.Div([
            html.H5("1. Cargar Excel de reservas", style={'fontSize': '13px', 'marginBottom': '4px'}),
            html.P("Workbook de dos hojas: «Saldo Reservas» e «Interés a la Reserva». "
                   "La variación se deriva por ramo (saldo − interés) y se agrupan los "
                   "ramos ausentes del portafolio en «DEMÁS RAMOS».",
                   style={'fontSize': '11px', 'color': '#666'}),
            dcc.Upload(
                id='upload-reservas',
                children=html.Button("📄 Cargar Excel de reservas"),
                multiple=False,
            ),
            html.Div(id='msg-upload-reservas', style={'marginTop': '6px', 'fontSize': '11px'}),
        ], style={'padding': '10px', 'border': '1px solid #ddd', 'borderRadius': '6px',
                  'marginBottom': '12px'}),

        # ── 15.2: Clasificación de ramos pendientes ──
        html.Div([
            html.H5("2. Clasificación de ramos pendientes", style={'fontSize': '13px', 'marginBottom': '4px'}),
            html.P("Clasifica cada ramo pendiente. Los ramos de reserva matemática "
                   "requieren instrumento (TES UVR o TF). Al guardar, el ramo desaparece "
                   "de la lista.", style={'fontSize': '11px', 'color': '#666'}),
            html.Div([
                html.Label("Ramo pendiente", style={'fontSize': '11px', 'fontWeight': '600'}),
                dcc.Dropdown(id='ajustes-ramo-pendiente', options=opciones_ramo,
                             placeholder="Selecciona un ramo…", style={'fontSize': '12px'}),
            ], style={'marginBottom': '6px'}),
            html.Div([
                html.Label("¿Reserva matemática?", style={'fontSize': '11px', 'fontWeight': '600'}),
                dcc.Dropdown(
                    id='ajustes-es-matematica',
                    options=[{"label": "Sí (reserva matemática)", "value": "si"},
                             {"label": "No", "value": "no"}],
                    placeholder="Selecciona…", style={'fontSize': '12px'}),
            ], style={'marginBottom': '6px'}),
            html.Div([
                html.Label("Instrumento (solo si es reserva matemática)",
                           style={'fontSize': '11px', 'fontWeight': '600'}),
                dcc.Dropdown(
                    id='ajustes-instrumento',
                    options=[{"label": "TES UVR", "value": "TES_UVR"},
                             {"label": "TF", "value": "TF"}],
                    placeholder="Selecciona instrumento…", style={'fontSize': '12px'}),
            ], style={'marginBottom': '6px'}),
            html.Button("💾 Guardar clasificación", id='btn-guardar-clasificacion',
                        n_clicks=0, style={'fontSize': '12px'}),
            html.Div(id='msg-clasificacion', style={'marginTop': '6px', 'fontSize': '11px'}),
        ], style={'padding': '10px', 'border': '1px solid #ddd', 'borderRadius': '6px',
                  'marginBottom': '12px'}),

        # ── Sección 3: Edición del mapeo de ramos (Fuente_Unica_De_Verdad) ──
        html.Div([
            html.H5("3. Mapeo de ramos de reservas", style={'fontSize': '13px', 'marginBottom': '4px'}),
            html.P("Correspondencia «etiqueta del Excel» → «ramo de portafolio». Un destino "
                   "vacío ignora esa etiqueta. La tabla es la fuente única que consumen la "
                   "pestaña Encaje y el motor de proyección.",
                   style={'fontSize': '11px', 'color': '#666'}),
            html.Div([
                html.Label("Tipo de mapeo", style={'fontSize': '11px', 'fontWeight': '600'}),
                dcc.Dropdown(
                    id='mapeo-tipo',
                    options=[{"label": "Saldos", "value": "saldo"},
                             {"label": "Interés", "value": "interes"}],
                    value="saldo", clearable=False, style={'fontSize': '12px', 'width': '220px'}),
            ], style={'marginBottom': '6px'}),
            html.Div(id='tabla-mapeo-container'),
            html.Button("💾 Guardar mapeo", id='btn-guardar-mapeo', n_clicks=0,
                        style={'fontSize': '12px', 'marginTop': '6px'}),
            html.Div(id='msg-mapeo', style={'marginTop': '6px', 'fontSize': '11px'}),
        ], style={'padding': '10px', 'border': '1px solid #ddd', 'borderRadius': '6px',
                  'marginBottom': '12px'}),

        # ── 15.4: Panel avanzado/colapsable de parámetros de plazo ──
        html.Details([
            html.Summary("⚙️ Parámetros avanzados de plazo (nodo de compra / plazo máx. de venta)",
                         style={'fontSize': '12px', 'fontWeight': '600', 'cursor': 'pointer'}),
            html.Div([
                html.P("Estos valores rara vez se modifican. Nodo de compra por defecto: "
                       "5 años. Plazo máximo de venta por defecto: 10 años.",
                       style={'fontSize': '11px', 'color': '#666'}),
                html.Div([
                    html.Label("Nodo de compra demás ramos (años)",
                               style={'fontSize': '11px', 'fontWeight': '600'}),
                    dcc.Input(id='param-nodo-compra', type='number', min=0.1, step=0.5,
                              value=nodo_actual, style={'fontSize': '12px', 'width': '120px'}),
                ], style={'marginBottom': '6px'}),
                html.Div([
                    html.Label("Plazo máximo de venta (años)",
                               style={'fontSize': '11px', 'fontWeight': '600'}),
                    dcc.Input(id='param-plazo-max-venta', type='number', min=0.1, step=0.5,
                              value=plazo_actual, style={'fontSize': '12px', 'width': '120px'}),
                ], style={'marginBottom': '6px'}),
                html.Button("💾 Guardar parámetros", id='btn-guardar-parametros',
                            n_clicks=0, style={'fontSize': '12px'}),
                html.Div(id='msg-parametros', style={'marginTop': '6px', 'fontSize': '11px'}),
            ], style={'padding': '8px 4px'}),
        ], style={'padding': '10px', 'border': '1px solid #ddd', 'borderRadius': '6px'}),
    ], style={'maxWidth': '620px'})


# ── 15.1: Callback de carga del Excel de reservas (derivación + persistencia) ──
@callback(
    Output('msg-upload-reservas', 'children'),
    Input('upload-reservas', 'contents'),
    State('upload-reservas', 'filename'),
    State('sidebar-fecha-inicio', 'date'),
    prevent_initial_call=True,
)
def cargar_excel_reservas(contents, filename, fecha_corte=None):
    """
    Recibe el workbook de dos hojas, deriva la variación con
    `derivar_variaciones_reservas` (usando los ramos del portafolio inicial) y
    persiste el resultado con `persistir_input_reservas`.

    La `Fecha_Corte` de la corrida (State `sidebar-fecha-inicio`) se pasa al lector
    para dimensionar el `Tramo_Parcial` del `Layout_Por_Tramo` (Req 15.1). Cuando la
    fecha es `None`/vacía, el lector opera en la ruta `Layout_Mensual` existente sin
    cambios observables.

    Los errores se discriminan por causa (hojas faltantes, formato de encabezado,
    lectura del archivo, persistencia) y se notifican con una alerta bloqueante y
    específica en la UI; el detalle queda en el log del servidor (sin PII). Un fallo
    deja el insumo en estado inválido, por lo que la corrida de proyección no
    procede con ese archivo (la persistencia versionada solo crea una versión
    consumible cuando la derivación fue exitosa).
    """
    if not contents:
        return no_update
    from io import BytesIO
    from compras_ventas_reservas import (
        derivar_variaciones_reservas,
        persistir_input_reservas,
        ErrorHojasFaltantes,
        ErrorFormatoEncabezado,
        ErrorInputReservas,
        ErrorPersistenciaReservas,
    )
    try:
        _content_type, content_string = contents.split(',')
        contenido_bytes = base64.b64decode(content_string)
    except Exception as e:
        print(f"[AXIS] Error decodificando Excel de reservas: {e}")
        return html.Span("❌ No se pudo leer el archivo subido.", style={'color': 'red'})

    ramos_port = _ramos_del_portafolio_inicial()
    # Coerción de la Fecha_Corte de la corrida (Req 15.1). Un valor vacío/no
    # coercible ⇒ None ⇒ ruta Layout_Mensual del lector.
    try:
        fc = pd.to_datetime(fecha_corte).date() if fecha_corte else None
    except Exception:
        fc = None
    try:
        df = derivar_variaciones_reservas(BytesIO(contenido_bytes), ramos_port, fecha_corte=fc)
        n = persistir_input_reservas(df)
    # IMPORTANTE: capturar las subclases (ErrorHojasFaltantes, ErrorFormatoEncabezado)
    # ANTES que la base ErrorInputReservas; de lo contrario la base las eclipsaría.
    except ErrorHojasFaltantes as e:
        print(f"[AXIS] ErrorHojasFaltantes: {e}")
        return html.Span("❌ El archivo debe contener las hojas «Saldo Reservas» e "
                         "«Interés a la Reserva».",
                         style={'color': 'red'})
    except ErrorFormatoEncabezado as e:
        print(f"[AXIS] ErrorFormatoEncabezado: {e}")
        return html.Span("❌ Los encabezados de columna deben ser FECHAS (no nombres de "
                         "mes ni otros formatos). Verifica el archivo.",
                         style={'color': 'red'})
    except ErrorInputReservas as e:
        print(f"[AXIS] ErrorInputReservas: {e}")
        return html.Span("❌ No se pudo leer el archivo de reservas.",
                         style={'color': 'red'})
    except ErrorPersistenciaReservas as e:
        print(f"[AXIS] ErrorPersistenciaReservas: {e}")
        return html.Span("❌ No se pudo guardar la variación de reservas. Intenta de nuevo.",
                         style={'color': 'red'})
    except Exception as e:
        print(f"[AXIS] Error inesperado cargando reservas: {e}")
        return html.Span("❌ Error inesperado al procesar el archivo.", style={'color': 'red'})

    filas = n if isinstance(n, int) else len(df)
    return html.Span(f"✅ Variación de reservas cargada ({filas} filas).",
                     style={'color': 'green'})


# ── 15.2: Callback de persistencia de la clasificación de ramos ──
@callback(
    [Output('msg-clasificacion', 'children'),
     Output('ajustes-ramo-pendiente', 'options'),
     Output('ajustes-ramo-pendiente', 'value')],
    Input('btn-guardar-clasificacion', 'n_clicks'),
    [State('ajustes-ramo-pendiente', 'value'),
     State('ajustes-es-matematica', 'value'),
     State('ajustes-instrumento', 'value')],
    prevent_initial_call=True,
)
def guardar_clasificacion_ramo(_n, ramo, es_matematica, instrumento):
    """
    Persiste la clasificación de un ramo. Tras guardar, el ramo sale de la lista
    de pendientes (se recomputan las opciones del dropdown).
    """
    from compras_ventas_reservas import (
        persistir_clasificacion_ramo,
        ErrorPersistenciaClasificacion,
    )
    if not ramo:
        return (html.Span("⚠️ Selecciona un ramo.", style={'color': 'orange'}),
                no_update, no_update)
    if es_matematica not in ("si", "no"):
        return (html.Span("⚠️ Indica si es reserva matemática.", style={'color': 'orange'}),
                no_update, no_update)

    es_mat = (es_matematica == "si")
    instr = instrumento if es_mat else None
    if es_mat and not instr:
        return (html.Span("⚠️ Los ramos de reserva matemática requieren instrumento.",
                          style={'color': 'orange'}), no_update, no_update)

    try:
        persistir_clasificacion_ramo(ramo, es_mat, instr)
    except ErrorPersistenciaClasificacion as e:
        print(f"[AXIS] ErrorPersistenciaClasificacion: {e}")
        return (html.Span("❌ No se pudo guardar la clasificación. Intenta de nuevo.",
                          style={'color': 'red'}), no_update, no_update)
    except Exception as e:
        print(f"[AXIS] Error inesperado guardando clasificación: {e}")
        return (html.Span("❌ Error inesperado al guardar.", style={'color': 'red'}),
                no_update, no_update)

    nuevas_opciones = [{"label": r, "value": r} for r in _opciones_ramos_pendientes()]
    return (html.Span(f"✅ Ramo «{ramo}» clasificado.", style={'color': 'green'}),
            nuevas_opciones, None)


# ── 15.4: Callback de persistencia de los parámetros de plazo ──
@callback(
    Output('msg-parametros', 'children'),
    Input('btn-guardar-parametros', 'n_clicks'),
    [State('param-nodo-compra', 'value'),
     State('param-plazo-max-venta', 'value')],
    prevent_initial_call=True,
)
def guardar_parametros_reservas(_n, nodo_compra, plazo_max_venta):
    """Persiste los parámetros de plazo; notifica mensaje genérico ante fallo."""
    from compras_ventas_reservas import (
        persistir_parametros_reservas,
        ErrorPersistenciaParametros,
    )
    try:
        nodo = float(nodo_compra)
        plazo = float(plazo_max_venta)
    except (TypeError, ValueError):
        return html.Span("⚠️ Ambos valores deben ser numéricos y positivos.",
                         style={'color': 'orange'})
    if nodo <= 0 or plazo <= 0:
        return html.Span("⚠️ Ambos valores deben ser positivos.", style={'color': 'orange'})

    try:
        persistir_parametros_reservas(nodo, plazo)
    except ErrorPersistenciaParametros as e:
        print(f"[AXIS] ErrorPersistenciaParametros: {e}")
        return html.Span("❌ No se pudieron guardar los parámetros. Intenta de nuevo.",
                         style={'color': 'red'})
    except Exception as e:
        print(f"[AXIS] Error inesperado guardando parámetros: {e}")
        return html.Span("❌ Error inesperado al guardar.", style={'color': 'red'})

    return html.Span(f"✅ Parámetros guardados (nodo compra {nodo:g}a, "
                     f"plazo máx. venta {plazo:g}a).", style={'color': 'green'})


# ── Sección 3: Render de la tabla de mapeo (saldos / interés) ──
@callback(
    Output('tabla-mapeo-container', 'children'),
    [Input('mapeo-tipo', 'value'),
     Input('msg-mapeo', 'children')],
)
def render_tabla_mapeo(tipo, _msg):
    """
    Muestra las entradas de la tabla de mapeo elegida (saldos o interés) en una
    DataTable editable. Se refresca al cambiar de tipo o tras guardar.
    """
    from compras_ventas_reservas import (
        cargar_mapeo_saldo, cargar_mapeo_interes,
    )
    from comun.rutas import DB_PATH as _DB
    try:
        mapeo = cargar_mapeo_interes(str(_DB)) if tipo == "interes" else cargar_mapeo_saldo(str(_DB))
    except Exception as e:
        print(f"[AXIS] Error cargando mapeo para render: {e}")
        return html.Span("❌ No se pudo cargar el mapeo.", style={'color': 'red'})

    filas = [{"formato_origen": k, "ramo_portafolio": v}
             for k, v in sorted(mapeo.entradas.items())]
    return dash_table.DataTable(
        id='tabla-mapeo',
        columns=[
            {"name": "Etiqueta del Excel (formato_origen)", "id": "formato_origen", "editable": False},
            {"name": "Ramo de portafolio (vacío = ignorar)", "id": "ramo_portafolio", "editable": True},
        ],
        data=filas,
        editable=True,
        page_size=15,
        style_table={'overflowX': 'auto'},
        style_cell={'textAlign': 'left', 'fontSize': '11px', 'padding': '4px'},
        style_header={'fontWeight': 'bold', 'backgroundColor': '#f0f0f0'},
        filter_action='native',
        sort_action='native',
    )


# ── Sección 3: Guardado de las ediciones del mapeo (persistencia parametrizada) ──
@callback(
    Output('msg-mapeo', 'children'),
    Input('btn-guardar-mapeo', 'n_clicks'),
    [State('tabla-mapeo', 'data'),
     State('mapeo-tipo', 'value')],
    prevent_initial_call=True,
)
def guardar_mapeo(_n, data, tipo):
    """
    Persiste las filas editadas de la tabla de mapeo vía `persistir_mapeo_reserva`
    (UPSERT parametrizado + normalización de la clave). Etiquetas que quedan vacías se
    rechazan y se reportan; el resto se guarda.
    """
    from compras_ventas_reservas import (
        persistir_mapeo_reserva, ErrorPersistenciaMapeo,
        _TABLA_MAPEO_SALDO, _TABLA_MAPEO_INTERES,
    )
    if not data:
        return html.Span("⚠️ No hay filas para guardar.", style={'color': 'orange'})

    tabla = _TABLA_MAPEO_INTERES if tipo == "interes" else _TABLA_MAPEO_SALDO
    guardadas, rechazadas = 0, 0
    for fila in data:
        etiqueta = fila.get("formato_origen", "")
        destino = fila.get("ramo_portafolio", "") or ""
        try:
            persistir_mapeo_reserva(tabla, etiqueta, destino)
            guardadas += 1
        except ValueError:
            # Etiqueta vacía tras normalizar: se rechaza esa fila (Req 10.5).
            rechazadas += 1
        except ErrorPersistenciaMapeo as e:
            print(f"[AXIS] ErrorPersistenciaMapeo: {e}")
            return html.Span("❌ No se pudo guardar el mapeo. Intenta de nuevo.",
                             style={'color': 'red'})
        except Exception as e:
            print(f"[AXIS] Error inesperado guardando mapeo: {e}")
            return html.Span("❌ Error inesperado al guardar el mapeo.", style={'color': 'red'})

    if rechazadas:
        return html.Span(
            f"✅ {guardadas} entradas guardadas. ⚠️ {rechazadas} con etiqueta vacía rechazadas.",
            style={'color': '#fd7e14'})
    return html.Span(f"✅ Mapeo guardado ({guardadas} entradas).", style={'color': 'green'})


# --- RUN ---
if __name__ == '__main__':
    # debug=False evita reloader que causa timeouts en callbacks largos
    app.run(debug=False, port=8070, threaded=True)
