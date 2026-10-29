# -*- coding: utf-8 -*-
"""
valoracion_derivados.py
-----------------------
Valora opciones y forwards del portafolio bajo múltiples escenarios de TRM.

Lee posiciones vigentes desde portafolio.db (hist_forwards, hist_opciones).
Inputs de mercado (vol, curvas PIP, TRM, escenarios) desde Inputs Py/.

Flujo:
    1. _cargar_*        →  lectura de archivos e inputs de mercado
    2. _renovar_*       →  extiende planos con renovaciones futuras
    3. _valorar_*_corte →  valoración en una fecha de corte específica
    4. valorar_portafolio_derivados  →  orquestador principal (bucle escenarios × cortes)
    5. exportar_resultados           →  Excel + consolidado P&G
"""

import sqlite3
import uuid
from datetime import datetime, timedelta
from itertools import chain
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from scipy.optimize import brentq, bisect

from libro_de_funciones import (
    construir_interpolador_vol,
    delta_bs,
    merge_lists_interleaved,
    precio_bs_signed,
    prima_neta_collar,
    valorar_fwds,
)

from valorar_bonos import valorar_bono_yankee, _dias_30_360, _generar_fechas_cupon

# =============================================================================
# RUTAS Y CONSTANTES
# =============================================================================

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR  # valoracion_derivados.py está en la raíz del proyecto AXIS
INPUTS_DIR   = SCRIPT_DIR  # Los CSV de curvas viven en la raíz del workspace
DB_PATH      = SCRIPT_DIR / "portafolio.db"

METODOS = {
    1: 'Nominal_Estable',
    3: 'Exposicion_Objetivo',
    4: 'Dejar_vencer_collares',
    5: 'Delta_Hedge',
}

# Tamaño de lote (USD) por portafolio para nuevos forwards (metodo 3)
STEP_FWD = {' 01-DW  ': 100_000, ' 02-DW  ': 5_000_000, ' 03-DW  ': 1_000_000}

# Pesos de asignación del diferencial de nominal entre portafolios (metodo 3)  # [REVISAR]
ALLOC_FWD = {' 01-DW  ': 0.002, ' 02-DW  ': 0.869, ' 03-DW  ': 0.129}

# Mapeo portafolio → compañía para el consolidado
PORT_FWD = {' 01-DW  ': 'Capi', ' 02-DW  ': 'Bol', ' 03-AT  ': 'Cls', ' 03-DW  ': 'Cls'}
PORT_OPC = {'01-OP': 'Capi', '02-OP': 'Bol', '03-OP': 'Cls', '03-OM': 'Cls'}

# Pesos de activos USD por portafolio (metodo 3, collares)  # [REVISAR]
PESO_ACT = {'01-OP': 0.0008, '02-OP': 0.94, '03-OP': 0.0592}

# =============================================================================
# 0. FÓRMULAS PURAS DE TASA Y DEVALUACIÓN (auditables y testeables)
# =============================================================================

# Rango cerrado admitido para la devaluación implícita (Req 3.8)
DEV_MIN = -0.5
DEV_MAX = 2.0


def tasa_ea(tasa_nom_pct, dias):
    """
    Tasa efectiva anual (EA, en tanto por uno) derivada de una tasa nominal simple
    base 360 (en %) a un plazo en días:

        (1 + tasa_nom/100 * dias/360)^(365/dias) - 1

    Función pura. Soporta escalares o arrays numpy de igual longitud.
    Protege `dias <= 0` y bases negativas devolviendo NaN (tasa EA no definida).
    (Req 3.3)
    """
    tasa_nom_pct = np.asarray(tasa_nom_pct, dtype=np.float64)
    dias = np.asarray(dias, dtype=np.float64)
    base = 1.0 + (tasa_nom_pct / 100.0) * (dias / 360.0)
    valido = (dias > 0.0) & (base > 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        exponente = np.where(dias > 0.0, 365.0 / dias, np.nan)
        resultado = np.power(np.where(valido, base, np.nan), exponente) - 1.0
    resultado = np.where(valido, resultado, np.nan)
    return resultado if resultado.ndim else resultado.item()


def _ea_a_nominal_pct(tasa_ea_tpu, dias):
    """
    Inversa de `tasa_ea`: convierte una tasa efectiva anual (EA, en tanto por uno) a
    tasa nominal simple base 360 expresada en % al plazo `dias`:

        tasa_nom% = ((1 + EA)^(dias/365) - 1) * (360 / dias) * 100

    Función pura interna. Soporta escalares o arrays numpy de igual longitud.
    Protege `dias <= 0` y bases (1 + EA) negativas devolviendo NaN. Se usa para
    reconstruir las Curva_Tasa_Proyectada en % nominal (formato que consume
    `calcular_dev_y_punto_forward`) a partir de las curvas EA ya proyectadas al mes.
    """
    tasa_ea_tpu = np.asarray(tasa_ea_tpu, dtype=np.float64)
    dias = np.asarray(dias, dtype=np.float64)
    base = 1.0 + tasa_ea_tpu
    valido = (dias > 0.0) & (base > 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        exponente = np.where(dias > 0.0, dias / 365.0, np.nan)
        factor = np.power(np.where(valido, base, np.nan), exponente) - 1.0
        resultado = factor * (360.0 / np.where(dias > 0.0, dias, np.nan)) * 100.0
    resultado = np.where(valido, resultado, np.nan)
    return resultado if resultado.ndim else resultado.item()


def devaluacion_implicita(swtibr_ea, lutibr_ea):
    """
    Devaluación efectiva anual por paridad de tasas de interés:

        ((1 + SWTIBR_EA) / (1 + LUTIBR_EA)) - 1

    Función pura. Soporta escalares o arrays numpy de igual longitud.
    Protege denominadores no positivos (1 + LUTIBR_EA <= 0) devolviendo NaN.
    (Req 3.3)
    """
    swtibr_ea = np.asarray(swtibr_ea, dtype=np.float64)
    lutibr_ea = np.asarray(lutibr_ea, dtype=np.float64)
    denom = 1.0 + lutibr_ea
    with np.errstate(invalid='ignore', divide='ignore'):
        resultado = np.where(denom != 0.0, (1.0 + swtibr_ea) / denom - 1.0, np.nan)
    return resultado if resultado.ndim else resultado.item()


def dev_valida(dev) -> bool:
    """
    True si `dev` es finita y está en el rango cerrado [-0.5, 2.0]. (Req 3.8)

    Función pura escalar.
    """
    try:
        valor = float(dev)
    except (TypeError, ValueError):
        return False
    return np.isfinite(valor) and (DEV_MIN <= valor <= DEV_MAX)


def punto_forward_dinamico(trm, dev, dias):
    """
    Punto forward dinámico en pesos:

        TRM * (1 + dev)^(dias / 365) - TRM

    Función pura. Soporta escalares o arrays numpy de igual longitud.
    Protege `dias <= 0` y bases (1 + dev) negativas devolviendo NaN.
    (Req 3.6, 3.7)
    """
    trm = np.asarray(trm, dtype=np.float64)
    dev = np.asarray(dev, dtype=np.float64)
    dias = np.asarray(dias, dtype=np.float64)
    base = 1.0 + dev
    valido = (dias > 0.0) & (base >= 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        exponente = np.where(dias > 0.0, dias / 365.0, np.nan)
        factor = np.power(np.where(valido, base, np.nan), exponente)
        resultado = trm * factor - trm
    resultado = np.where(valido, resultado, np.nan)
    return resultado if resultado.ndim else resultado.item()


# Anclas de plazo (días) para la interpolación del Shift_FED de la Curva_LUTIBR (Req 3.4)
ANCLA_CORTA_DIAS = 1.0      # ancla corta: shift FED
ANCLA_LARGA_DIAS = 3650.0   # ancla larga: shift tesoros 10 años

# Indicador de puntos forward en pesos: nunca recibe shift (Req 3.5)
INDICADOR_FWD_COP = 'FWPCOP'


def shift_lutibr_por_plazo(plazo_dias, shift_fed_bps, shift_10t_bps):
    """
    Interpola linealmente el Shift_FED de la Curva_LUTIBR por plazo entre dos anclas:

        - ancla corta  (1 día    = FED)      : shift FED           (`shift_fed_bps`)
        - ancla larga  (3650 días = 10 años) : shift tesoros 10a   (`shift_10t_bps`)

    Entre ambas anclas el shift es la interpolación lineal en el plazo; fuera del
    rango se aplica clamp plano (para plazos <= 1 día se usa el shift FED, para
    plazos >= 3650 días se usa el shift de tesoros 10 años).

    Los shifts de entrada están en puntos básicos (pbs); el resultado se devuelve
    en % (dividido por 100), listo para SUMARse a una tasa expresada en % nominal.

    Función pura. Soporta escalares o arrays numpy. (Req 3.4)
    """
    plazo = np.asarray(plazo_dias, dtype=np.float64)
    fed_bps = float(shift_fed_bps)
    t10_bps = float(shift_10t_bps)

    # Interpolación lineal en el plazo, con clamp plano fuera de [1, 3650] días
    plazo_clamp = np.clip(plazo, ANCLA_CORTA_DIAS, ANCLA_LARGA_DIAS)
    fraccion = (plazo_clamp - ANCLA_CORTA_DIAS) / (ANCLA_LARGA_DIAS - ANCLA_CORTA_DIAS)
    shift_bps = fed_bps + (t10_bps - fed_bps) * fraccion

    # Plazos no finitos -> NaN (no se puede ubicar en la curva)
    shift_bps = np.where(np.isfinite(plazo), shift_bps, np.nan)

    resultado = shift_bps / 100.0  # pbs -> %
    return resultado if resultado.ndim else resultado.item()


def curva_proyectada_pct(curva_pct, shift_pct_por_plazo, progreso):
    """
    Construye la Curva_Tasa_Proyectada al mes `m` sumando a la tasa (en %) el shift
    (también en %) escalado por el Progreso_Mensual `progreso = m / N`, replicando la
    senda lineal mensual de las curvas de renta fija: para un shift total S a N meses,
    el desplazamiento acumulado en el mes m es `S * m / N`.

    Args:
        curva_pct: DataFrame con columnas ('Nombre_Indicador', 'Plazo_en_Dias',
            'Valor'), donde 'Valor' es la tasa en % (SWTIBR/LUTIBR) o los puntos
            forward en pesos (FWPCOP).
        shift_pct_por_plazo: shift en % a sumar, alineado por posición con las filas
            de `curva_pct` (escalar o array de igual longitud). Para la Curva_LUTIBR
            típicamente proviene de `shift_lutibr_por_plazo`.
        progreso: fracción m/N del horizonte (>= 0).

    El shift se suma ÚNICAMENTE a las tasas en % de SWTIBR/LUTIBR y NUNCA a los
    puntos forward en pesos del indicador FWPCOP, que quedan idénticos (Req 3.4, 3.5).

    Función pura: no muta la entrada, devuelve un DataFrame nuevo.
    """
    proyectada = curva_pct.copy()
    shift = np.asarray(shift_pct_por_plazo, dtype=np.float64)
    shift_escalado = shift * float(progreso)

    # Máscara: filas que SÍ reciben shift (todo indicador de tasa, excepto FWPCOP)
    aplica_shift = (proyectada['Nombre_Indicador'] != INDICADOR_FWD_COP).to_numpy()

    incremento = np.where(aplica_shift, shift_escalado, 0.0)
    proyectada['Valor'] = proyectada['Valor'].to_numpy(dtype=np.float64) + incremento
    return proyectada


def _tasa_al_plazo(curva, dias):
    """
    Resuelve una tasa (r o q) continua al plazo `dias` a partir de `curva`, que puede ser:

        - un escalar (float/int): se usa tal cual, ya evaluada al plazo del corte;
        - un callable: se invoca como `curva(dias)` y se toma su valor;
        - un DataFrame de curva con columnas ('Plazo_en_Dias', 'Valor'): se toma el
          valor del tenor más cercano (mismo criterio de vecino más cercano que
          `_merge_curva`, robusto ante tenores faltantes).

    Función auxiliar pura usada por `resolver_canal_collar` para reutilizar la
    Curva_R_Corte / Curva_Q_Corte ya proyectadas al mes del corte sin re-implementar
    la proyección. Devuelve un float (NaN si no puede resolverse).
    """
    if callable(curva):
        try:
            return float(curva(float(dias)))
        except Exception:
            return float('nan')
    if isinstance(curva, pd.DataFrame):
        if curva.empty or 'Plazo_en_Dias' not in curva or 'Valor' not in curva:
            return float('nan')
        plazos = curva['Plazo_en_Dias'].to_numpy(dtype=np.float64)
        idx = int(np.argmin(np.abs(plazos - float(dias))))
        return float(curva['Valor'].to_numpy(dtype=np.float64)[idx])
    try:
        return float(curva)
    except (TypeError, ValueError):
        return float('nan')


def calcular_dev_y_punto_forward(dias, trm_corte, swtibr_proy, lutibr_proy,
                                 dev_forecast, fecha_constitucion=None,
                                 mes_proyeccion=None):
    """
    Orquesta el cálculo de la Devaluacion_Implicita y el Punto_Forward_Dinamico de un
    forward a un plazo `dias`, combinando las curvas SWTIBR/LUTIBR ya proyectadas al
    mes del corte con las fórmulas puras (`tasa_ea`, `devaluacion_implicita`,
    `punto_forward_dinamico`), y aplicando el fallback a `dev_forecast` cuando la
    derivación no es utilizable.

    Fallback a `dev_forecast` (Req 3.9) — se activa (`uso_fallback = True`) cuando:
        - no existe curva SWTIBR o LUTIBR al plazo (tasa EA no resoluble / no finita), o
        - la Devaluacion_Implicita derivada no es finita, o
        - queda fuera del rango cerrado [-0.5, 2.0] (Req 3.8, vía `dev_valida`).

    En cualquiera de esos casos se usa `dev_forecast` como devaluación (siempre que sea
    válida; de no serlo, la `dev_usada` queda en NaN pero se marca el fallback igual).

    El Punto_Forward_Dinamico se calcula SIEMPRE con la `dev_usada` finalmente adoptada
    (derivada o de fallback) mediante `punto_forward_dinamico(trm_corte, dev_usada, dias)`.

    Parámetros
    ----------
    dias : float
        Plazo en días desde el corte hasta el vencimiento del forward.
    trm_corte : float
        TRM del corte (spot USD/COP) sobre la que se calcula el punto forward.
    swtibr_proy, lutibr_proy : pandas.DataFrame | float | callable
        Curva_Tasa_Proyectada de la Curva_SWTIBR y de la Curva_LUTIBR al mes del corte,
        expresadas en % nominal simple base 360. Se resuelven al plazo `dias` por el
        tenor más cercano (`_tasa_al_plazo`).
    dev_forecast : float
        Devaluación plana de respaldo (Dev_Forecast) usada como fallback.
    fecha_constitucion : opcional
        Fecha de constitución del forward, para el Registro_Auditoria (Req 3.11).
    mes_proyeccion : opcional
        Mes de proyección `m` (días desde el corte), para el Registro_Auditoria.

    Retorna
    -------
    tuple[float, float, dict]
        (dev_usada, punto_forward, registro_auditoria), donde `registro_auditoria`
        contiene: fecha_constitucion, mes_proyeccion, plazo_dias, trm_corte,
        swtibr_ea, lutibr_ea, dev, punto_forward y uso_fallback.
    (Req 3.1, 3.2, 3.6, 3.9, 3.11)
    """
    dias = float(dias)
    trm_corte = float(trm_corte)

    # Tasas nominales (% base 360) al plazo desde las curvas proyectadas al mes.
    swtibr_nom = _tasa_al_plazo(swtibr_proy, dias)
    lutibr_nom = _tasa_al_plazo(lutibr_proy, dias)

    # Conversión a EA al plazo (NaN si no hay curva / plazo inválido).
    with np.errstate(invalid='ignore', divide='ignore'):
        swtibr_ea = tasa_ea(swtibr_nom, dias)
        lutibr_ea = tasa_ea(lutibr_nom, dias)
        dev_derivada = devaluacion_implicita(swtibr_ea, lutibr_ea)

    swtibr_ea = float(swtibr_ea)
    lutibr_ea = float(lutibr_ea)
    dev_derivada = float(dev_derivada)

    # Decisión de fallback (Req 3.8, 3.9): sin curva al plazo, no finita o fuera de rango.
    if dev_valida(dev_derivada):
        dev_usada = dev_derivada
        uso_fallback = False
    else:
        dev_usada = float(dev_forecast)
        uso_fallback = True

    # El punto forward se calcula con la devaluación finalmente adoptada.
    punto_forward = punto_forward_dinamico(trm_corte, dev_usada, dias)
    punto_forward = float(punto_forward)

    registro_auditoria = {
        'fecha_constitucion': fecha_constitucion,
        'mes_proyeccion': mes_proyeccion,
        'plazo_dias': dias,
        'trm_corte': trm_corte,
        'swtibr_ea': swtibr_ea,
        'lutibr_ea': lutibr_ea,
        'dev': dev_usada,
        'punto_forward': punto_forward,
        'uso_fallback': uso_fallback,
    }

    return dev_usada, punto_forward, registro_auditoria


def resolver_canal_collar(k_put, trm, interpol_vol, r_corte, q_corte, dias, nominal,
                          sentido_put, sentido_call, tolerancia, max_iter,
                          canal_opc_fallback):
    """
    Resuelve el strike de la Call de un collar tal que el valor absoluto de la prima
    neta del collar sea <= `tolerancia` (Collar_Zero_Cost), dado el strike de la Put.

    Método: buscar-objetivo (Solver_Canal) sobre
        f(k_call) = prima_neta_collar(k_call, k_put, ...)
    usando Brent (`scipy.optimize.brentq`) por robustez, con bisección
    (`scipy.optimize.bisect`) como respaldo si Brent falla pero el bracket presenta
    cambio de signo. La prima neta es monótona en `k_call`, por lo que basta con un
    bracket con cambio de signo para garantizar convergencia.

    Insumos de valoración (Req 4.2, 4.3):
        - Volatilidad por pata desde `interpol_vol(strike, dias_venc)`.
        - Tasas continuas `r`/`q` de la Curva_R_Corte / Curva_Q_Corte ya proyectadas
          al mes del corte, resueltas al plazo `dias` vía `_tasa_al_plazo` (acepta
          escalar, callable o DataFrame de curva). No re-implementa la proyección.

    Fallback (Req 4.7): si no se puede establecer un bracket con cambio de signo, o
    el solver no converge dentro de `max_iter` iteraciones, se devuelve el strike de
    la Call como `k_put + canal_opc_fallback`, SIN lanzar excepción, marcando
    `uso_fallback = True`.

    Parámetros
    ----------
    k_put : float
        Strike de la Put (fijado, típicamente en la TRM del corte).
    trm : float
        Spot USD/COP (S en Black-Scholes) sobre el que se valoran ambas patas.
    interpol_vol : callable
        Interpolador de volatilidad `(strike: float, dias_venc: int) -> vol`.
    r_corte, q_corte : float | callable | pandas.DataFrame
        Curva_R_Corte / Curva_Q_Corte continuas ya proyectadas al mes del corte.
    dias : float
        Plazo a vencimiento en días (t = dias / 365 años).
    nominal : float
        Nominal de cada pata.
    sentido_put, sentido_call : str
        Sentido de cada pata ('Compra' / 'Venta').
    tolerancia : float
        Tolerancia_Prima: cota máxima de |prima neta| para el Collar_Zero_Cost.
    max_iter : int
        Número máximo de iteraciones del solver.
    canal_opc_fallback : float
        Canal_Opc de respaldo (distancia Call-Put) usado ante no convergencia.

    Retorna
    -------
    tuple[float, float, bool]
        (k_call_resuelto, prima_neta, uso_fallback).
    (Req 4.1, 4.5, 4.7)
    """
    k_put = float(k_put)
    trm = float(trm)
    dias = float(dias)
    nominal = float(nominal)
    tolerancia = float(tolerancia)
    max_iter = int(max_iter)
    canal_opc_fallback = float(canal_opc_fallback)

    t = dias / 365.0
    r = _tasa_al_plazo(r_corte, dias)
    q = _tasa_al_plazo(q_corte, dias)
    vol_put = float(interpol_vol(k_put, int(dias)))

    def f(k_call):
        """Prima neta del collar en función del strike de la Call."""
        vol_call = float(interpol_vol(float(k_call), int(dias)))
        return prima_neta_collar(k_call, k_put, trm, vol_put, vol_call, t,
                                 r, q, nominal, sentido_put, sentido_call)

    fallback_call = k_put + canal_opc_fallback

    def _fallback():
        """Prima neta evaluada en el strike Call de respaldo (sin lanzar)."""
        try:
            with np.errstate(invalid='ignore', divide='ignore'):
                prima = f(fallback_call)
        except Exception:
            prima = float('nan')
        return fallback_call, prima, True

    # Guardas de dominio: sin plazo/tasas/vol válidos no hay problema bien planteado.
    if not (dias > 0.0 and np.isfinite(r) and np.isfinite(q) and np.isfinite(vol_put)
            and vol_put > 0.0 and trm > 0.0 and k_put > 0.0):
        return _fallback()

    # --- Bracketing: buscar [a, b] con cambio de signo en f ---
    # La Call arranca en k_put y se aleja hacia arriba; ampliamos el ancho del canal
    # geométricamente hasta capturar el cambio de signo o agotar los intentos.
    a = k_put
    try:
        fa = f(a)
    except Exception:
        return _fallback()
    if not np.isfinite(fa):
        return _fallback()
    if abs(fa) <= tolerancia:
        return a, fa, False

    ancho = canal_opc_fallback if canal_opc_fallback > 0.0 else max(1.0, 0.1 * k_put)
    b, fb = a, fa
    encontrado = False
    for _ in range(max(1, max_iter)):
        b = a + ancho
        try:
            fb = f(b)
        except Exception:
            fb = float('nan')
        if np.isfinite(fb):
            if abs(fb) <= tolerancia:
                return b, fb, False
            if fa * fb < 0.0:
                encontrado = True
                break
        ancho *= 2.0

    if not encontrado:
        return _fallback()

    # --- Brent (con bisección de respaldo) sobre el bracket con cambio de signo ---
    try:
        k_call = brentq(f, a, b, xtol=1e-8, maxiter=max_iter)
    except Exception:
        try:
            k_call = bisect(f, a, b, xtol=1e-8, maxiter=max_iter)
        except Exception:
            return _fallback()

    try:
        prima = f(k_call)
    except Exception:
        return _fallback()

    if not np.isfinite(prima) or abs(prima) > tolerancia:
        return _fallback()

    return float(k_call), float(prima), False


# =============================================================================
# 0.b PERSISTENCIA DE AUDITORÍA DE LA CORRIDA (Registro_Auditoria)
# =============================================================================

# Nombre de la tabla de auditoría de la corrida de derivados (Req 5.3).
TABLA_AUDIT_DERIVADOS = 'audit_derivados_corrida'

# Allowlist FIJO de columnas de la tabla de auditoría, en el orden EXACTO del
# esquema del diseño. Este orden es la única fuente de verdad usada tanto para el
# CREATE TABLE como para el INSERT (binding posicional), evitando cualquier
# interpolación de nombres de columna provenientes de datos de entrada (Req 5.2).
_COLUMNAS_AUDIT_DERIVADOS = (
    'corrida_id',
    'tipo_registro',
    'fecha_constitucion',
    'mes_proyeccion',
    'plazo_dias',
    'trm_corte',
    'swtibr_ea',
    'lutibr_ea',
    'dev',
    'punto_forward',
    'shift_tes_bps',
    'shift_fed_bps',
    'strike_put',
    'strike_call',
    'prima_neta',
    'uso_fallback',
)

# DDL con el esquema EXACTO del diseño (Data Models). No se altera ni amplía.
_DDL_AUDIT_DERIVADOS = f"""
CREATE TABLE IF NOT EXISTS {TABLA_AUDIT_DERIVADOS} (
    corrida_id          TEXT NOT NULL,
    tipo_registro       TEXT NOT NULL,
    fecha_constitucion  DATE,
    mes_proyeccion      INTEGER,
    plazo_dias          INTEGER,
    trm_corte           REAL,
    swtibr_ea           REAL,
    lutibr_ea           REAL,
    dev                 REAL,
    punto_forward       REAL,
    shift_tes_bps       REAL,
    shift_fed_bps       REAL,
    strike_put          REAL,
    strike_call         REAL,
    prima_neta          REAL,
    uso_fallback        INTEGER NOT NULL
);
"""


def _fila_audit_por_binding(corrida_id: str, registro: dict) -> tuple:
    """
    Proyecta un `Registro_Auditoria` (dict) a una tupla de valores en el orden EXACTO
    del allowlist `_COLUMNAS_AUDIT_DERIVADOS`, para binding posicional en `executemany`.

    - `corrida_id` se inyecta desde el argumento (agrupa la corrida completa).
    - Los campos no presentes en el registro quedan como `None` (NULL en SQLite),
      tal como exige el diseño para los campos no aplicables al tipo de registro.
    - `uso_fallback` se normaliza a 0/1 (INTEGER NOT NULL).

    Función pura: no toca la base de datos.
    """
    valores = []
    for col in _COLUMNAS_AUDIT_DERIVADOS:
        if col == 'corrida_id':
            valores.append(corrida_id)
        elif col == 'uso_fallback':
            valores.append(1 if registro.get('uso_fallback') else 0)
        else:
            valores.append(registro.get(col))
    return tuple(valores)


def persistir_auditoria_derivados(conn, corrida_id: str, registros) -> bool:
    """
    Persiste los `Registro_Auditoria` acumulados de una corrida en la tabla
    `audit_derivados_corrida` (Req 5.3).

    Comportamiento:
        - `CREATE TABLE IF NOT EXISTS` con el esquema EXACTO del diseño.
        - Inserta todos los registros con un único `executemany`, enlazando TODOS
          los valores como parámetros (`?`); los nombres de columna provienen del
          allowlist fijo `_COLUMNAS_AUDIT_DERIVADOS`, nunca de datos de entrada
          (Req 5.2 — sin concatenar valores en el texto SQL).
        - Todo dentro de UNA transacción atómica (`with conn:`): si algo falla, el
          gestor de contexto de sqlite3 hace rollback automático.

    Tolerancia a fallos (Req 5.3): un fallo al escribir la auditoría NO invalida el
    resultado en memoria de la corrida. Cualquier excepción se captura y se registra
    (log), y la función retorna False SIN re-lanzar al llamador.

    Args:
        conn: conexión sqlite3 abierta.
        corrida_id: identificador que agrupa la corrida completa.
        registros: iterable de dicts `Registro_Auditoria` (claves ⊂ columnas del
            esquema; los campos ausentes se persisten como NULL).

    Returns:
        True si la escritura se completó (incluido el caso de 0 registros); False si
        ocurrió un fallo que fue capturado (el resultado en memoria sigue válido).
    """
    try:
        filas = [_fila_audit_por_binding(corrida_id, reg) for reg in registros]

        # Placeholders posicionales generados a partir del allowlist (no de datos).
        columnas_sql = ', '.join(_COLUMNAS_AUDIT_DERIVADOS)
        placeholders = ', '.join(['?'] * len(_COLUMNAS_AUDIT_DERIVADOS))
        insert_sql = (
            f"INSERT INTO {TABLA_AUDIT_DERIVADOS} ({columnas_sql}) "
            f"VALUES ({placeholders})"
        )

        # Transacción atómica: commit al salir sin error, rollback ante excepción.
        with conn:
            conn.execute(_DDL_AUDIT_DERIVADOS)
            if filas:
                conn.executemany(insert_sql, filas)
        return True
    except Exception as exc:
        # El fallo de auditoría NO invalida el resultado en memoria (Req 5.3).
        print(f"[AXIS] Advertencia: no se pudo persistir la auditoría de derivados "
              f"(corrida_id={corrida_id!r}): {type(exc).__name__}: {exc}")
        return False


# =============================================================================
# 1. LOADERS
# =============================================================================

def _cargar_curvas(fecha_corte: str, conn):
    """
    Carga curvas PIP Forward desde la tabla hist_curvas_derivados (filtrando por
    fecha_reporte) y genera múltiples versiones:
    - Continuas (r, q) para opciones (Black-Scholes)
    - Simples (r_fwd) para forwards (solo /100)
    - EA (r_ea, q_ea) para aplicar deltas de proyección
    - FWPCOP: puntos forward (sin transformar)
    Retorna: (fwd_cop, r, q, r_simple, r_ea, q_ea)

    fecha_corte: 'YYYY-MM-DD' (debe coincidir con fecha_reporte de la tabla).
    conn: conexión SQLite abierta.
    """
    c = pd.read_sql_query(
        "SELECT Nombre_Indicador, Plazo_en_Dias, Valor "
        "FROM hist_curvas_derivados WHERE fecha_reporte = ?",
        conn, params=(fecha_corte,)
    )
    if c.empty:
        raise ValueError(
            f"No hay curvas Forward en hist_curvas_derivados para la fecha {fecha_corte}. "
            f"Verifica que el archivo Curvaspip_Forward_LocalSimple se haya cargado por el dashboard."
        )
    c['Valor'] = pd.to_numeric(c['Valor'], errors='coerce')
    c['Plazo_en_Dias'] = pd.to_numeric(c['Plazo_en_Dias'], errors='coerce')
    c = c.dropna(subset=['Valor', 'Plazo_en_Dias']).reset_index(drop=True)
    c['Valor'] = c['Valor'].astype(np.float64)
    c['Plazo_en_Dias'] = c['Plazo_en_Dias'].astype(np.float64)

    # Tasas simples (para forwards): solo /100
    r_simple = c.loc[c.Nombre_Indicador == 'SWTIBR', ['Plazo_en_Dias', 'Valor']].copy()
    r_simple['Valor'] = r_simple['Valor'] / 100

    # Tasas EA (para aplicar deltas antes de convertir a continua)
    # EA = (1 + tasa_nom/100 * plazo/360)^(365/plazo) - 1
    c_ea = c.copy()
    mask_no_fwp = c_ea.Nombre_Indicador != 'FWPCOP'
    ea_vals = (1 + (c_ea.loc[mask_no_fwp, 'Valor'] / 100) * (c_ea.loc[mask_no_fwp, 'Plazo_en_Dias'] / 360)) ** (365 / c_ea.loc[mask_no_fwp, 'Plazo_en_Dias']) - 1
    c_ea.loc[mask_no_fwp, 'Valor'] = ea_vals
    r_ea = c_ea.loc[c_ea.Nombre_Indicador == 'SWTIBR', ['Plazo_en_Dias', 'Valor']].copy()
    q_ea = c_ea.loc[c_ea.Nombre_Indicador == 'LUTIBR', ['Plazo_en_Dias', 'Valor']].copy()

    # Tasas continuas (para opciones BS)
    # Continua = ln(1 + tasa_nom/100 * (365/360) * plazo/365) / (plazo/365)
    cont = (np.log(1 + ((c['Valor'] / 100) * (365 / 360)) * c['Plazo_en_Dias'] / 365)
            / (c['Plazo_en_Dias'] / 365))
    c_cont = c.copy()
    c_cont.loc[c_cont.Nombre_Indicador != 'FWPCOP', 'Valor'] = cont

    fwd_cop = c.loc[c.Nombre_Indicador == 'FWPCOP', ['Plazo_en_Dias', 'Valor']].copy()
    r       = c_cont.loc[c_cont.Nombre_Indicador == 'SWTIBR', ['Plazo_en_Dias', 'Valor']].copy()
    q       = c_cont.loc[c_cont.Nombre_Indicador == 'LUTIBR', ['Plazo_en_Dias', 'Valor']].copy()
    return fwd_cop, r, q, r_simple, r_ea, q_ea


def _cargar_vol(fecha_corte: str, conn) -> pd.DataFrame:
    """
    Carga la superficie de volatilidad USDCOP desde la tabla hist_vol_derivados
    (filtrando por fecha_reporte).

    En la tabla cada fila es un tenor: la columna '0' es el día al vencimiento y las
    columnas con nombre de strike contienen la vol. Se reconstruye la MISMA orientación
    que producía el CSV: index = strikes (float), columns = 0..N-1 (posición = día-1).

    Retorna DataFrame con index=strikes (float) y columnas 0..N-1 (días).
    fecha_corte: 'YYYY-MM-DD'. conn: conexión SQLite abierta.
    """
    df = pd.read_sql_query(
        "SELECT * FROM hist_vol_derivados WHERE fecha_reporte = ?",
        conn, params=(fecha_corte,)
    )
    if df.empty:
        raise ValueError(
            f"No hay superficie de volatilidad en hist_vol_derivados para la fecha {fecha_corte}. "
            f"Verifica que el archivo usdcop_strike se haya cargado por el dashboard."
        )
    # Columnas meta (no-strike) a descartar
    meta = ['fecha_reporte', 'Fecha', 'Indicador', '0']
    strike_cols = [c for c in df.columns if c not in meta]
    # Ordenar filas por día (columna '0') para que las columnas queden en orden 0..N-1
    df = df.sort_values('0').reset_index(drop=True)
    mat = df[strike_cols].T.copy()
    mat.index   = mat.index.astype(float)   # strikes al índice
    mat.columns = range(mat.shape[1])        # días como columnas 0..N-1
    mat = mat.apply(pd.to_numeric, errors='coerce')
    return mat


def _cargar_trm_hist(fecha_corte_dt: datetime) -> pd.DataFrame:
    """
    Carga TRM histórica hasta fecha_corte desde Inputs Py/TRM.xlsx.
    Espera dos columnas: fecha (col 0) y TRM (col 1).
    """
    path = INPUTS_DIR / "TRM.xlsx"
    trm  = pd.read_excel(path, usecols=[0, 1])
    trm.columns = ['fecha', 'trm']
    trm['fecha'] = pd.to_datetime(trm['fecha'])
    return trm.loc[trm['fecha'] <= fecha_corte_dt].dropna().reset_index(drop=True)


def _cargar_posiciones(fecha_str: str, conn, port_analizado: int):
    """
    Lee forwards y opciones vigentes desde portafolio.db para la fecha dada.
    Retorna (fwds_df, opts_df) con columnas estandarizadas.
    """
    fwds = pd.read_sql_query(
        """SELECT fecha_constitucion, fecha_vencimiento,
                  nominal_usd AS nominal, sentido, portafolio,
                  strike AS tasa_negociada
           FROM hist_forwards WHERE fecha_reporte = ?""",
        conn, params=(fecha_str,)
    )
    fwds.columns = ['constitucion', 'vencimiento', 'nominal', 'sentido',
                    'portafolio', 'tasa_negociada']
    fwds['constitucion'] = pd.to_datetime(fwds['constitucion'])
    fwds['vencimiento']  = pd.to_datetime(fwds['vencimiento'])
    fwds['nominal'] = pd.to_numeric(fwds['nominal'], errors='coerce')
    fwds['tasa_negociada'] = pd.to_numeric(fwds['tasa_negociada'], errors='coerce')
    if port_analizado == 2:
        fwds = fwds.loc[fwds['portafolio'] == ' 03-AT  ']

    opts = pd.read_sql_query(
        """SELECT fecha_constitucion, fecha_vencimiento,
                  tipo AS call_put, sentido, portafolio,
                  strike, nominal_usd AS nominal
           FROM hist_opciones WHERE fecha_reporte = ?""",
        conn, params=(fecha_str,)
    )
    opts.columns = ['constitucion', 'vencimiento', 'call_put', 'sentido',
                    'portafolio', 'strike', 'nominal']
    opts['constitucion'] = pd.to_datetime(opts['constitucion'])
    opts['vencimiento']  = pd.to_datetime(opts['vencimiento'])
    opts['strike'] = pd.to_numeric(opts['strike'], errors='coerce')
    opts['nominal'] = pd.to_numeric(opts['nominal'], errors='coerce')
    # [REVISAR] normalizar si call_put o sentido vienen con formato distinto del Excel
    return fwds.reset_index(drop=True), opts.reset_index(drop=True)


def _cargar_curvas_swaps(fecha_corte: str, conn) -> dict:
    """
    Carga curvas de descuento para swaps desde la tabla hist_curvas_otc (filtrando por
    fecha_reporte) y las convierte a EA base 360.
    Retorna dict {nombre_curva: {plazo_dias: tasa_ea}}.
    Curvas: SWTIBR (COP), UVTIBR (UVR), LUTIBR (USD).

    fecha_corte: 'YYYY-MM-DD' (debe coincidir con fecha_reporte de la tabla).
    conn: conexión SQLite abierta.
    """
    df = pd.read_sql_query(
        "SELECT Curva, Plazo_en_Dias, Valor FROM hist_curvas_otc WHERE fecha_reporte = ?",
        conn, params=(fecha_corte,)
    )
    if df.empty:
        # Sin curvas OTC para esa fecha → no se pueden valorar swaps (retorna vacío
        # para mantener el comportamiento previo cuando no existía el archivo).
        return {}
    df['Plazo_en_Dias'] = pd.to_numeric(df['Plazo_en_Dias'], errors='coerce').astype('Int64')
    df['Valor'] = pd.to_numeric(df['Valor'], errors='coerce')
    df = df.dropna(subset=['Valor', 'Plazo_en_Dias'])
    df['Plazo_en_Dias'] = df['Plazo_en_Dias'].astype(int)

    curvas = {}
    for nombre in ['SWTIBR', 'UVTIBR', 'LUTIBR']:
        sub = df[df['Curva'] == nombre].copy()
        # Conversión: nominal base 360 → efectiva anual base 360
        sub['tasa_ea'] = (1 + (sub['Valor'] / 100) * (sub['Plazo_en_Dias'] / 360)) ** (360 / sub['Plazo_en_Dias']) - 1
        curvas[nombre] = dict(zip(sub['Plazo_en_Dias'], sub['tasa_ea']))
    return curvas


def proyectar_curvas_swaps(
    curvas_base: dict,
    delta_tes_cop: dict = None,
    delta_tes_uvr: dict = None,
    delta_fed_bps: float = 0,
    delta_10t_bps: float = 0,
    progreso: float = 1.0,
) -> dict:
    """
    Proyecta las curvas de swaps aplicando un delta proporcional al progreso.

    Args:
        curvas_base: {nombre_curva: {plazo_dias: tasa_ea}} — curvas base (SWTIBR, UVTIBR, LUTIBR)
        delta_tes_cop: {plazo_dias: delta_ea} — delta por nodo de TES COP (escenario - vigente)
        delta_tes_uvr: {plazo_dias: delta_ea} — delta por nodo de TES UVR
        delta_fed_bps: variación de tasa FED en puntos básicos (para LUTIBR corto plazo)
        delta_10t_bps: variación de tesoros 10Y en bps (para LUTIBR largo plazo)
        progreso: fracción del horizonte (m / N_total). 0=inicio, 1=final

    Returns:
        dict con curvas proyectadas en el mismo formato que curvas_base
    """
    curvas_proy = {}

    # --- SWTIBR: mover con delta TES COP ---
    if 'SWTIBR' in curvas_base:
        curva = curvas_base['SWTIBR'].copy()
        if delta_tes_cop:
            plazos_tes = sorted(delta_tes_cop.keys())
            for plazo in curva:
                # Interpolar delta del TES COP al plazo de la curva
                delta = _interpolar_delta(plazo, plazos_tes, delta_tes_cop)
                curva[plazo] = curva[plazo] + delta * progreso
        curvas_proy['SWTIBR'] = curva

    # --- UVTIBR: mover con delta TES UVR ---
    if 'UVTIBR' in curvas_base:
        curva = curvas_base['UVTIBR'].copy()
        if delta_tes_uvr:
            plazos_tes = sorted(delta_tes_uvr.keys())
            for plazo in curva:
                delta = _interpolar_delta(plazo, plazos_tes, delta_tes_uvr)
                curva[plazo] = curva[plazo] + delta * progreso
        curvas_proy['UVTIBR'] = curva

    # --- LUTIBR: mover con FED (corto) y 10T (largo), interpolación lineal ---
    if 'LUTIBR' in curvas_base:
        curva = curvas_base['LUTIBR'].copy()
        delta_fed = delta_fed_bps / 10000  # bps a decimal
        delta_10t = delta_10t_bps / 10000
        plazo_fed = 1       # 1 día (overnight)
        plazo_10t = 3650    # 10 años
        for plazo in curva:
            if plazo <= plazo_fed:
                delta = delta_fed
            elif plazo >= plazo_10t:
                delta = delta_10t  # flat después de 10Y
            else:
                # Interpolación lineal entre FED y 10T
                peso = (plazo - plazo_fed) / (plazo_10t - plazo_fed)
                delta = delta_fed + peso * (delta_10t - delta_fed)
            curva[plazo] = curva[plazo] + delta * progreso
        curvas_proy['LUTIBR'] = curva

    return curvas_proy


def _interpolar_delta(plazo: int, plazos_nodos: list, deltas: dict) -> float:
    """Interpola linealmente el delta de una curva de TES al plazo dado."""
    if plazo <= plazos_nodos[0]:
        return deltas[plazos_nodos[0]]
    if plazo >= plazos_nodos[-1]:
        return deltas[plazos_nodos[-1]]
    # Buscar nodos vecinos
    for i in range(len(plazos_nodos) - 1):
        if plazos_nodos[i] <= plazo <= plazos_nodos[i + 1]:
            p1, p2 = plazos_nodos[i], plazos_nodos[i + 1]
            d1, d2 = deltas[p1], deltas[p2]
            peso = (plazo - p1) / (p2 - p1)
            return d1 + peso * (d2 - d1)
    return 0.0


def _cargar_swaps(fecha_str: str, conn) -> pd.DataFrame:
    """Lee swaps vigentes desde hist_swaps para la fecha dada."""
    swaps = pd.read_sql_query(
        "SELECT * FROM hist_swaps WHERE fecha_reporte = ?",
        conn, params=(fecha_str,)
    )
    if swaps.empty:
        return swaps
    swaps['fecha_constitucion'] = pd.to_datetime(swaps['fecha_constitucion'])
    swaps['fecha_vencimiento'] = pd.to_datetime(swaps['fecha_vencimiento'])
    for col in ['nominal_der', 'facial_der', 'vpn_derecho', 'nominal_obl', 'facial_obl', 'vpn_obligacion', 'valor_mercado']:
        if col in swaps.columns:
            swaps[col] = pd.to_numeric(swaps[col], errors='coerce')
    return swaps


def _cupones_pata_acumulados(
    tasa_nominal: float,
    valor_nominal: float,
    fecha_emision,
    fecha_vencimiento,
    fecha_hasta,
) -> float:
    """
    Suma de los cupones de UNA pata del swap efectivamente PAGADOS/RECIBIDOS en moneda
    ORIGINAL desde la constitución (`fecha_emision`) hasta `fecha_hasta` inclusive.

    Reutiliza EXACTAMENTE la convención de `valorar_bono_yankee` (cupón semestral,
    `pago_cupon = (tasa_nominal/100 / 2) * valor_nominal`, fechas generadas con
    `_generar_fechas_cupon(..., "semestral")`) para que el flujo realizado sea
    consistente con la caída del precio sucio entre cortes: cuando un cupón deja de ser
    un flujo futuro (sale del VPN), aquí se contabiliza como caja realizada.

    El resultado NO se convierte a COP: el llamador aplica el factor de moneda del corte
    (TRM/UVR) coherente con la valoración de esa pata.

    Función pura. Devuelve 0.0 si los datos no son utilizables.
    """
    try:
        nominal = float(valor_nominal)
        tasa_nom = float(tasa_nominal) / 100.0
    except (TypeError, ValueError):
        return 0.0
    if nominal == 0.0 or tasa_nom == 0.0:
        return 0.0

    fe = fecha_emision.date() if hasattr(fecha_emision, 'date') else fecha_emision
    fv = fecha_vencimiento.date() if hasattr(fecha_vencimiento, 'date') else fecha_vencimiento
    fh = fecha_hasta.date() if hasattr(fecha_hasta, 'date') else fecha_hasta
    if fe is None or fv is None or fh is None or fv <= fe:
        return 0.0

    pago_cupon = (tasa_nom / 2.0) * nominal
    fechas = _generar_fechas_cupon(fe, fv, "semestral")
    # Cupones ya pagados: fechas de cupón posteriores a la emisión y <= fecha de corte.
    cupones_pagados = [f for f in fechas if fe < f <= fh]
    return pago_cupon * len(cupones_pagados)


def _valorar_swaps_corte(swaps: pd.DataFrame, curvas: dict,
                          trm_val: float, uvr_val: float,
                          fecha_val: datetime) -> pd.DataFrame:
    """
    Valora todos los swaps en una fecha de corte.
    Cada swap se valora como: VP_derecho * factor_der - VP_obligacion * factor_obl.

    Además de los VPN de cada pata, expone los CUPONES acumulados pagados/recibidos por
    pata (en COP, con el factor de moneda del corte) desde la constitución hasta la fecha
    de valoración. El P&G contable usa la variación de esos acumulados entre cortes como
    el flujo de cupón REALIZADO en la ventana, evitando que el cupón "desaparezca" al
    salir del precio sucio (mismo criterio que vencimientos en forwards/opciones).
    """
    if swaps.empty:
        return swaps.assign(valoracion=pd.Series(dtype=float),
                            vpn_der_calc=pd.Series(dtype=float),
                            vpn_obl_calc=pd.Series(dtype=float))

    # Mapeo moneda → curva y factor de conversión
    CURVA_MAP = {'UVR': 'UVTIBR', 'COP': 'SWTIBR', 'USD': 'LUTIBR'}
    FACTOR_MAP = {'UVR': uvr_val, 'COP': 1.0, 'USD': trm_val}

    resultados = []
    fecha_val_date = fecha_val.date() if hasattr(fecha_val, 'date') else fecha_val

    for _, row in swaps.iterrows():
        fecha_inicio = row['fecha_constitucion'].date() if hasattr(row['fecha_constitucion'], 'date') else row['fecha_constitucion']
        fecha_vcto = row['fecha_vencimiento'].date() if hasattr(row['fecha_vencimiento'], 'date') else row['fecha_vencimiento']

        # Pata derecho
        curva_der = curvas.get(CURVA_MAP.get(row['moneda_der'], 'SWTIBR'), {})
        factor_der = FACTOR_MAP.get(row['moneda_der'], 1.0)
        if curva_der and fecha_vcto > fecha_val_date:
            res_der = valorar_bono_yankee(
                tasa_nominal=float(row['facial_der']),
                yield_mercado=curva_der,
                fecha_emision=fecha_inicio,
                fecha_vencimiento=fecha_vcto,
                valor_nominal_usd=float(row['nominal_der']),
                trm=factor_der,
                fecha_valuacion=fecha_val_date,
            )
            vpn_der_cop = res_der['precio_sucio_cop']
        else:
            vpn_der_cop = 0.0

        # Pata obligación
        curva_obl = curvas.get(CURVA_MAP.get(row['moneda_obl'], 'LUTIBR'), {})
        factor_obl = FACTOR_MAP.get(row['moneda_obl'], 1.0)
        if curva_obl and fecha_vcto > fecha_val_date:
            res_obl = valorar_bono_yankee(
                tasa_nominal=float(row['facial_obl']),
                yield_mercado=curva_obl,
                fecha_emision=fecha_inicio,
                fecha_vencimiento=fecha_vcto,
                valor_nominal_usd=float(row['nominal_obl']),
                trm=factor_obl,
                fecha_valuacion=fecha_val_date,
            )
            vpn_obl_cop = res_obl['precio_sucio_cop']
        else:
            vpn_obl_cop = 0.0

        # Cupones acumulados pagados/recibidos por pata (moneda original → COP con el
        # factor de moneda del corte). La variación entre cortes es el cupón REALIZADO.
        cup_der_orig = _cupones_pata_acumulados(
            tasa_nominal=float(row['facial_der']),
            valor_nominal=float(row['nominal_der']),
            fecha_emision=fecha_inicio,
            fecha_vencimiento=fecha_vcto,
            fecha_hasta=fecha_val_date,
        )
        cup_obl_orig = _cupones_pata_acumulados(
            tasa_nominal=float(row['facial_obl']),
            valor_nominal=float(row['nominal_obl']),
            fecha_emision=fecha_inicio,
            fecha_vencimiento=fecha_vcto,
            fecha_hasta=fecha_val_date,
        )
        cupones_der_cop = cup_der_orig * factor_der
        cupones_obl_cop = cup_obl_orig * factor_obl

        resultados.append({
            'id_posicion': row['id_posicion'],
            'especie': row['especie'],
            'portafolio': row['portafolio'],
            'fecha_vencimiento': row['fecha_vencimiento'],
            'nominal_der': row['nominal_der'],
            'nominal_obl': row['nominal_obl'],
            'moneda_der': row['moneda_der'],
            'moneda_obl': row['moneda_obl'],
            'vpn_der_calc': vpn_der_cop,
            'vpn_obl_calc': vpn_obl_cop,
            'valoracion': vpn_der_cop - vpn_obl_cop,
            # Cupones acumulados (COP) recibidos (der) / pagados (obl) hasta el corte.
            'cupones_der_acum_cop': cupones_der_cop,
            'cupones_obl_acum_cop': cupones_obl_cop,
        })

    return pd.DataFrame(resultados)


def _construir_serie_trm(senda_trm: list, fecha_corte_dt: datetime) -> pd.Series:
    """
    Convierte la senda mensual de TRM (lista de floats) en una Serie diaria
    con índice de fechas desde fecha_corte.

    senda_trm: [trm_corte, trm_fin_mes1, ..., trm_fin_mesN]
               Punto 0 = nivel actual (fecha_corte), puntos 1..N = fin de cada mes.
    Retorna Serie con freq diaria (interpolación lineal entre puntos).
    """
    n_meses = len(senda_trm) - 1  # el primer valor es fecha_corte
    # Generar las mismas fechas que los cortes de valoración (fecha_corte + fin de mes)
    fecha_corte_ts = pd.Timestamp(fecha_corte_dt)
    fechas_cortes = pd.DatetimeIndex([fecha_corte_ts]).union(
        pd.date_range(start=fecha_corte_ts, periods=n_meses, freq='ME')
    )
    # Si hay más puntos en senda que fechas generadas, ajustar
    fechas_mes = list(fechas_cortes[:len(senda_trm)])

    s = pd.Series(senda_trm[:len(fechas_mes)], index=pd.DatetimeIndex(fechas_mes))
    # Expandir a frecuencia diaria con interpolación lineal entre puntos
    idx_diario = pd.date_range(start=fechas_mes[0], end=fechas_mes[-1], freq='D')
    serie = s.reindex(idx_diario).interpolate(method='index')
    return serie


# =============================================================================
# 2. RENOVACIÓN DE POSICIONES
# =============================================================================

def _curvas_proy_para_progreso(r_ea, q_ea, metodo_params, progreso):
    """Construye la Curva_Tasa_Proyectada de SWTIBR y LUTIBR en % nominal simple base 360
    a un Progreso_Mensual dado (`progreso = m / N`), reutilizando exactamente la misma
    senda lineal mensual (EA base + delta escalado por progreso) que emplea el loop de
    cortes de `valorar_portafolio_derivados` (Req 3.4, 3.5).

    Se usa para derivar la Devaluacion_Implicita de forwards renovados/nuevos en el mes
    de su fecha de constitución (Req 3.1, 3.2), sin re-implementar la proyección.

    Devuelve `(swtibr_proy, lutibr_proy)` como DataFrames con columnas
    ['Plazo_en_Dias', 'Valor'] en % nominal, o `(None, None)` si no hay curvas base.
    """
    if r_ea is None or q_ea is None:
        return None, None

    progreso = float(progreso)
    _delta_cop = metodo_params.get('delta_tes_cop') if metodo_params else None
    _delta_fed = metodo_params.get('delta_fed_bps', 0) if metodo_params else 0
    _delta_10t = metodo_params.get('delta_10t_bps', 0) if metodo_params else 0

    r_ea_proy = r_ea
    q_ea_proy = q_ea

    if (_delta_cop or _delta_fed or _delta_10t) and progreso > 0:
        # --- SWTIBR (r, COP): EA base + delta TES COP escalado por progreso ---
        r_ea_proy = r_ea.copy()
        if _delta_cop:
            plazos_tes = sorted(_delta_cop.keys())
            for i in r_ea_proy.index:
                plazo = int(r_ea_proy.at[i, 'Plazo_en_Dias'])
                delta = _interpolar_delta(plazo, plazos_tes, _delta_cop)
                r_ea_proy.at[i, 'Valor'] = r_ea_proy.at[i, 'Valor'] + delta * progreso

        # --- LUTIBR (q, USD): EA base + delta FED/tesoros 10a escalado por progreso ---
        q_ea_proy = q_ea.copy()
        if _delta_fed or _delta_10t:
            delta_fed = _delta_fed / 10000
            delta_10t = _delta_10t / 10000
            plazo_fed, plazo_10t = 1, 3650
            for i in q_ea_proy.index:
                plazo = int(q_ea_proy.at[i, 'Plazo_en_Dias'])
                if plazo <= plazo_fed:
                    delta = delta_fed
                elif plazo >= plazo_10t:
                    delta = delta_10t
                else:
                    peso = (plazo - plazo_fed) / (plazo_10t - plazo_fed)
                    delta = delta_fed + peso * (delta_10t - delta_fed)
                q_ea_proy.at[i, 'Valor'] = q_ea_proy.at[i, 'Valor'] + delta * progreso

    swtibr_proy = r_ea_proy.copy()
    swtibr_proy['Valor'] = _ea_a_nominal_pct(
        r_ea_proy['Valor'].to_numpy(), r_ea_proy['Plazo_en_Dias'].to_numpy())
    lutibr_proy = q_ea_proy.copy()
    lutibr_proy['Valor'] = _ea_a_nominal_pct(
        q_ea_proy['Valor'].to_numpy(), q_ea_proy['Plazo_en_Dias'].to_numpy())
    return swtibr_proy, lutibr_proy


def _aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, delta_curva_base):
    """Aplica el desplazamiento BASE acumulado ``delta_curva_base`` a las curvas EA/simple
    cargadas de ``_cargar_curvas`` (encadenamiento por tramos, D3; Req 2.9-2.11).

    Representa el delta ACUMULADO de los tramos previos (0..i-1) que debe desplazar la
    curva de ARRANQUE del tramo (progreso=0 parte de ``base + delta_curva_base``). Se
    aplica como un offset CONSTANTE (equivalente a progreso=1) para TODO el tramo, con la
    MISMA semántica de nodos/interpolación que los deltas por corte de ``metodo_params``:

    - ``delta_tes_cop`` (bps): shift paralelo a SWTIBR (``r_ea``) y a la tasa simple
      ``r_fwd`` (``bps/10000`` en EA/decimal).
    - ``delta_fed_bps``/``delta_10t_bps`` (bps): shift a LUTIBR (``q_ea``) interpolado entre
      el nodo FED (1 día) y el nodo tesoros 10a (3650 días), igual que el loop por corte.

    ``delta_tes_uvr`` NO afecta a ``r_ea``/``q_ea`` (SWTIBR/LUTIBR de forwards/opciones);
    se aplica a la curva de swaps por separado (ver ``_aplicar_delta_curva_base_swaps``).

    Devuelve ``(r_ea, q_ea, r_fwd)`` desplazados (COPIAS). Si ``delta_curva_base`` es None
    o no tiene componentes relevantes, devuelve las curvas SIN cambios (comportamiento
    byte-idéntico al actual). Función pura: no muta las entradas.
    """
    if not delta_curva_base:
        return r_ea, q_ea, r_fwd

    # Coacción defensiva a bps finitos; None/no-numérico/no-finito -> 0.0.
    def _bps(clave):
        try:
            v = float(delta_curva_base.get(clave, 0.0))
        except (TypeError, ValueError):
            return 0.0
        return v if np.isfinite(v) else 0.0

    delta_cop_bps = _bps('delta_tes_cop')
    delta_fed_bps = _bps('delta_fed_bps')
    delta_10t_bps = _bps('delta_10t_bps')

    r_ea_out, q_ea_out, r_fwd_out = r_ea, q_ea, r_fwd

    # --- SWTIBR (r, COP): offset paralelo = delta_tes_cop (bps -> EA decimal) ---
    if delta_cop_bps != 0.0:
        delta_cop_ea = delta_cop_bps / 10000.0
        if r_ea_out is not None:
            r_ea_out = r_ea.copy()
            r_ea_out['Valor'] = r_ea_out['Valor'].to_numpy() + delta_cop_ea
        if r_fwd_out is not None:
            r_fwd_out = r_fwd.copy()
            r_fwd_out['Valor'] = r_fwd_out['Valor'].to_numpy() + delta_cop_ea

    # --- LUTIBR (q, USD): offset FED/tesoros 10a interpolado por nodo ---
    if (delta_fed_bps != 0.0 or delta_10t_bps != 0.0) and q_ea is not None:
        delta_fed = delta_fed_bps / 10000.0
        delta_10t = delta_10t_bps / 10000.0
        plazo_fed, plazo_10t = 1, 3650
        q_ea_out = q_ea.copy()
        for i in q_ea_out.index:
            plazo = int(q_ea_out.at[i, 'Plazo_en_Dias'])
            if plazo <= plazo_fed:
                delta = delta_fed
            elif plazo >= plazo_10t:
                delta = delta_10t
            else:
                peso = (plazo - plazo_fed) / (plazo_10t - plazo_fed)
                delta = delta_fed + peso * (delta_10t - delta_fed)
            q_ea_out.at[i, 'Valor'] = q_ea_out.at[i, 'Valor'] + delta

    return r_ea_out, q_ea_out, r_fwd_out


def _aplicar_delta_curva_base_swaps(curvas_swaps, delta_curva_base):
    """Aplica el desplazamiento BASE acumulado ``delta_curva_base`` a las curvas de swaps
    cargadas de ``_cargar_curvas_swaps`` (encadenamiento por tramos, D3; Req 2.9-2.11).

    Reutiliza ``proyectar_curvas_swaps`` con ``progreso=1.0`` para aplicar el offset
    CONSTANTE con la MISMA semántica (nodos/interpolación) que el loop por corte. Los
    deltas van en las MISMAS unidades que ``metodo_params`` del swap: ``delta_tes_cop``/
    ``delta_tes_uvr`` como dict ``{nodo: delta_ea}`` (bps/10000 por nodo) y
    ``delta_fed_bps``/``delta_10t_bps`` como bps escalares.

    Si ``delta_curva_base`` es None o ``curvas_swaps`` está vacío, devuelve ``curvas_swaps``
    SIN cambios (comportamiento byte-idéntico al actual).
    """
    if not delta_curva_base or not curvas_swaps:
        return curvas_swaps

    def _bps(clave):
        try:
            v = float(delta_curva_base.get(clave, 0.0))
        except (TypeError, ValueError):
            return 0.0
        return v if np.isfinite(v) else 0.0

    delta_cop_bps = _bps('delta_tes_cop')
    delta_uvr_bps = _bps('delta_tes_uvr')
    delta_fed_bps = _bps('delta_fed_bps')
    delta_10t_bps = _bps('delta_10t_bps')

    if (delta_cop_bps == 0.0 and delta_uvr_bps == 0.0
            and delta_fed_bps == 0.0 and delta_10t_bps == 0.0):
        return curvas_swaps

    # delta_tes_cop/uvr como dict por nodo (misma forma que metodo_params del swap),
    # construidos sobre los plazos de la curva base correspondiente.
    delta_cop_dict = None
    if delta_cop_bps != 0.0 and 'SWTIBR' in curvas_swaps:
        delta_cop_dict = {p: delta_cop_bps / 10000.0 for p in curvas_swaps['SWTIBR']}
    delta_uvr_dict = None
    if delta_uvr_bps != 0.0 and 'UVTIBR' in curvas_swaps:
        delta_uvr_dict = {p: delta_uvr_bps / 10000.0 for p in curvas_swaps['UVTIBR']}

    return proyectar_curvas_swaps(
        curvas_swaps,
        delta_tes_cop=delta_cop_dict,
        delta_tes_uvr=delta_uvr_dict,
        delta_fed_bps=delta_fed_bps,
        delta_10t_bps=delta_10t_bps,
        progreso=1.0,
    )


def _progreso_en_fecha(constitucion, fecha_corte_dt, horizonte_dias):
    """Progreso_Mensual `m / N` (continuo, sobre días) de una fecha de constitución en el
    horizonte de proyección, replicando la senda lineal mensual de las curvas RF.

    `m` = días desde el corte hasta la constitución; `N` = días totales del horizonte.
    Se recorta a [0, 1]. Devuelve 0.0 si el horizonte es no positivo.
    """
    horizonte_dias = float(horizonte_dias)
    if horizonte_dias <= 0:
        return 0.0
    dias_desde_corte = (pd.to_datetime(constitucion) - fecha_corte_dt).days
    progreso = dias_desde_corte / horizonte_dias
    return float(min(max(progreso, 0.0), 1.0))


def _renovar_fwds(fwds: pd.DataFrame, trm_df: pd.DataFrame,
                   iteraciones: int, dev_forecast: float,
                   r_ea: pd.DataFrame = None, q_ea: pd.DataFrame = None,
                   metodo_params: dict = None, fecha_corte_dt=None,
                   horizonte_dias: float = None) -> pd.DataFrame:
    """Extiende el plano con `iteraciones` renovaciones sucesivas de 100 días.

    La `tasa_negociada` de cada renovación se calcula como `TRM * (1 + dev)^(100/365)`
    (Req 3.1), donde `dev` es la Devaluacion_Implicita al plazo 100 días evaluada con las
    curvas SWTIBR/LUTIBR proyectadas al mes de la fecha de constitución de la renovación,
    con `dev_forecast` como fallback (Req 3.9).

    Firma ampliada retrocompatible: si no se proporcionan las curvas EA base
    (`r_ea`/`q_ea`) ni el contexto de proyección, se usa `dev_forecast` plano por defecto
    (comportamiento previo).
    """
    # D1 (idempotencia del nominal): el DataFrame base conserva TODAS sus filas (originales
    # y, si viene de un plano ya renovado inyectado, también las renovaciones previas).
    base = fwds.copy()

    # Marca de control `_es_renovacion` (D2): las filas que lleguen SIN la columna se tratan
    # como ORIGINALES (retrocompatibilidad con el tramo 0). Si la columna ya existe (plano
    # inyectado), se respeta su valor (NaN/ausente -> False).
    if '_es_renovacion' in base.columns:
        base['_es_renovacion'] = base['_es_renovacion'].fillna(False).astype(bool)
    else:
        base['_es_renovacion'] = False

    bloques = [base]

    # CRÍTICO (D1): la cadena de renovación parte SOLO de las posiciones ORIGINALES
    # (`_es_renovacion == False`). Las filas que ya son renovaciones previas NO se usan como
    # semilla, de modo que re-renovar un plano ya renovado produce el MISMO nominal vivo
    # (idempotencia). Si no hay marca, todas se consideran originales (comportamiento previo).
    src = base.loc[~base['_es_renovacion']].drop(columns=['_es_renovacion']).copy()

    # Idempotencia en nominal (D1): claves de las renovaciones que YA están presentes en el
    # plano inyectado. Un bloque regenerado por el loop solo se AÑADE si su clave
    # (portafolio|sentido|constitucion|vencimiento) no existe aún; así re-renovar un plano
    # ya renovado no duplica bloques y el nominal vivo se conserva.
    def _clave_fwd(portafolio, sentido, constitucion, vencimiento):
        return (
            f"{str(portafolio).strip()}|{str(sentido).strip()}|"
            f"{pd.Timestamp(constitucion).strftime('%Y-%m-%d')}|"
            f"{pd.Timestamp(vencimiento).strftime('%Y-%m-%d')}"
        )

    renov_previas = base.loc[base['_es_renovacion']]
    claves_existentes = set()
    for _, _fila in renov_previas.iterrows():
        claves_existentes.add(
            _clave_fwd(_fila['portafolio'], _fila['sentido'],
                       _fila['constitucion'], _fila['vencimiento'])
        )

    proyeccion_ok = (r_ea is not None and q_ea is not None
                     and fecha_corte_dt is not None and horizonte_dias is not None)

    for _ in range(iteraciones):
        if src.empty:
            break
        blq = pd.DataFrame({
            'constitucion':  pd.to_datetime(src['vencimiento'].values),
            'vencimiento':   pd.to_datetime(src['vencimiento'].values) + timedelta(days=100),
            'nominal':       src['nominal'].astype(np.float64).values,
            'sentido':       src['sentido'].values,
            'portafolio':    src['portafolio'].values,
        })
        blq = blq.merge(trm_df, left_on='constitucion', right_on='fecha', how='left')

        if proyeccion_ok:
            blq['tasa_negociada'] = _tasa_negociada_dev_implicita(
                blq['constitucion'], blq['trm'].astype(np.float64).to_numpy(),
                plazo_dias=100, dev_forecast=dev_forecast, r_ea=r_ea, q_ea=q_ea,
                metodo_params=metodo_params, fecha_corte_dt=fecha_corte_dt,
                horizonte_dias=horizonte_dias,
            )
        else:
            blq['tasa_negociada'] = blq['trm'].astype(np.float64) * ((1 + dev_forecast) ** (100 / 365))

        blq = blq.drop(columns=['fecha', 'trm']).dropna(subset=['tasa_negociada'])
        # Cada bloque generado por el loop es una renovación (D2).
        blq['_es_renovacion'] = True
        # El loop sigue encadenando desde `blq` completo (para cubrir todo el horizonte),
        # pero solo se AÑADEN las filas cuya renovación aún no existía en el plano.
        if claves_existentes:
            _claves_blq = [
                _clave_fwd(p, s, c, v)
                for p, s, c, v in zip(blq['portafolio'], blq['sentido'],
                                      blq['constitucion'], blq['vencimiento'])
            ]
            _nuevas = pd.Series([k not in claves_existentes for k in _claves_blq],
                                index=blq.index)
            blq_add = blq.loc[_nuevas]
        else:
            blq_add = blq
        if len(blq_add) > 0:
            bloques.append(blq_add)
        src = blq.copy()
    return pd.concat(bloques, ignore_index=True)


def _tasa_negociada_dev_implicita(constituciones, trm_arr, plazo_dias, dev_forecast,
                                  r_ea, q_ea, metodo_params, fecha_corte_dt,
                                  horizonte_dias):
    """Calcula la `tasa_negociada = TRM * (1 + dev)^(plazo/365)` por fila usando la
    Devaluacion_Implicita al `plazo_dias` en el mes de cada fecha de constitución
    (Req 3.1, 3.2), con `dev_forecast` como fallback (Req 3.9).

    Reutiliza `_curvas_proy_para_progreso` (senda lineal mensual) y el orquestador
    `calcular_dev_y_punto_forward` (rango de validez + fallback). Memoiza las curvas
    proyectadas por progreso para evitar reconstruirlas por fila.
    """
    trm_arr = np.asarray(trm_arr, dtype=np.float64)
    constituciones = list(pd.to_datetime(pd.Series(list(constituciones)).values))
    tasas = np.full(trm_arr.shape, np.nan, dtype=np.float64)

    cache_curvas = {}
    for i, (const, trm_val) in enumerate(zip(constituciones, trm_arr)):
        if not np.isfinite(trm_val):
            continue
        progreso = _progreso_en_fecha(const, fecha_corte_dt, horizonte_dias)
        clave = round(progreso, 6)
        if clave not in cache_curvas:
            cache_curvas[clave] = _curvas_proy_para_progreso(
                r_ea, q_ea, metodo_params, progreso)
        swtibr_proy, lutibr_proy = cache_curvas[clave]

        dev_usada, _punto, _reg = calcular_dev_y_punto_forward(
            float(plazo_dias), float(trm_val), swtibr_proy, lutibr_proy,
            float(dev_forecast), fecha_constitucion=const,
        )
        tasas[i] = float(trm_val) * ((1 + dev_usada) ** (plazo_dias / 365))

    return tasas


def _collar_id(portafolio, constitucion, vencimiento) -> str:
    """Identificador estable de un Collar renovado (Put+Call comparten portafolio,
    fecha de constitución y fecha de vencimiento). Se usa para memoizar la resolución
    del strike de la Call y garantizar su inmutabilidad entre cortes (Req 4.4)."""
    return (f"{str(portafolio).strip()}|"
            f"{pd.Timestamp(constitucion).strftime('%Y-%m-%d')}|"
            f"{pd.Timestamp(vencimiento).strftime('%Y-%m-%d')}")


def _renovar_opciones(opts: pd.DataFrame, trm_df: pd.DataFrame,
                       canal_opc: float) -> pd.DataFrame:
    """Agrega una renovación a 1.5 años sobre el plano de opciones existente.

    A diferencia del comportamiento previo (que fijaba el strike de la Call renovada
    como ``trm + canal_opc``), esta función deja marcadas las patas Call de los collares
    renovados para que su strike se resuelva vía Solver_Canal en el corte de su fecha de
    constitución (Req 4.3, 4.4). Aquí solo se fija:

      - el strike de la Put renovada en la TRM del corte de constitución;
      - un strike **provisional** (``trm + canal_opc``) para la Call renovada, que se
        conserva únicamente como respaldo hasta que el solver lo resuelva en el corte de
        constitución.

    Las opciones existentes de ``hist_opciones`` (strike dado) NUNCA se marcan ni pasan
    por el solver: conservan ``_renovado = False`` (Req 4.3, no-regresión).
    """
    if opts.empty:
        base = opts.copy()
        # Anotar columnas de control aun cuando no haya opciones, para preservar el
        # contrato de columnas del plano en cortes posteriores.
        base['_renovado'] = pd.Series(dtype=bool)
        base['_collar_id'] = pd.Series(dtype=object)
        base['_call_resuelto'] = pd.Series(dtype=bool)
        return base

    base = opts.copy()

    # D1/D2 (no re-renovar ni re-marcar lo ya renovado): si la entrada ya trae filas con
    # `_renovado=True` (plano inyectado del tramo anterior), esas filas CONSERVAN sus flags
    # (`_renovado`, `_collar_id`, `_call_resuelto`) y su `strike` inmutable; NO se re-marcan
    # ni re-entran al solver. Solo se INICIALIZAN los flags de control en las filas base
    # NUEVAS (originales de hist_opciones) que aún no los traen. Retrocompatible: si la
    # columna no existe, se asume original (`_renovado=False`).
    if '_renovado' in base.columns:
        base['_renovado'] = base['_renovado'].fillna(False).astype(bool)
    else:
        base['_renovado'] = False
    if '_collar_id' not in base.columns:
        base['_collar_id'] = None
    if '_call_resuelto' in base.columns:
        base['_call_resuelto'] = base['_call_resuelto'].fillna(False).astype(bool)
    else:
        base['_call_resuelto'] = False

    # CRÍTICO (D1): solo las opciones ORIGINALES (no renovadas) pueden sembrar el collar de
    # renovación. Las filas ya renovadas NO se vuelven a sembrar.
    semilla = base.loc[~base['_renovado'].astype(bool)].copy()

    # Idempotencia en nominal (D1): una opción original NO se re-siembra si su renovación
    # (identificada por `_collar_id = portafolio|vencimiento_original|vencimiento_renov`) YA
    # está presente en el plano inyectado. Así, re-renovar un plano ya renovado NO añade
    # bloques nuevos y el nominal vivo se conserva.
    collares_existentes = set(
        base.loc[base['_renovado'].astype(bool), '_collar_id'].dropna().astype(str)
    )
    if not semilla.empty and collares_existentes:
        _const_renov = pd.to_datetime(semilla['vencimiento'].values)
        _venc_renov = _const_renov + pd.to_timedelta(1.5 * 365, unit='D')
        _ids_candidatos = [
            _collar_id(p, c, v)
            for p, c, v in zip(semilla['portafolio'].values, _const_renov, _venc_renov)
        ]
        _ya_renovada = pd.Series(
            [cid in collares_existentes for cid in _ids_candidatos],
            index=semilla.index,
        )
        semilla = semilla.loc[~_ya_renovada]

    if semilla.empty:
        # Nada que renovar (todas las originales ya tienen su renovación constituida, o el
        # plano viene 100% renovado): se conserva el plano tal cual (nominal conservado).
        return base.reset_index(drop=True)

    renov = pd.DataFrame({
        'constitucion': pd.to_datetime(semilla['vencimiento'].values),
        'vencimiento':  pd.to_datetime(semilla['vencimiento'].values) + pd.to_timedelta(1.5 * 365, unit='D'),
        'sentido':      semilla['sentido'].values,
        'call_put':     semilla['call_put'].values,
        'portafolio':   semilla['portafolio'].values,
        'nominal':      semilla['nominal'].astype(np.float64).values,
    })
    renov = renov.merge(trm_df, left_on='constitucion', right_on='fecha', how='left')
    # Put: strike fijo en la TRM del corte de constitución.
    # Call: strike PROVISIONAL (fallback canal_opc); el Solver_Canal lo resolverá en el
    # corte de la fecha de constitución (Req 4.1, 4.3).
    renov['strike'] = np.where(
        renov['call_put'] == 'Put',
        renov['trm'],
        renov['trm'] + canal_opc
    )
    renov = renov.dropna(subset=['strike']).drop(columns=['fecha', 'trm'])
    renov['strike'] = renov['strike'].astype(np.float64)
    renov['nominal'] = renov['nominal'].astype(np.float64)

    # Marca de renovación e identificador de collar (para memoizar la resolución).
    renov['_renovado'] = True
    renov['_collar_id'] = [
        _collar_id(p, c, v)
        for p, c, v in zip(renov['portafolio'], renov['constitucion'], renov['vencimiento'])
    ]
    # La Put queda "resuelta" por definición (su strike es la TRM); solo la Call renovada
    # requiere resolución por el solver.
    renov['_call_resuelto'] = renov['call_put'] != 'Call'

    return pd.concat([base, renov], ignore_index=True)


def _resolver_calls_renovados_en_corte(
    opts_plan: pd.DataFrame,
    fecha_cal: datetime,
    trm_df: pd.DataFrame,
    interpol_vol,
    r_df_corte: pd.DataFrame,
    q_df_corte: pd.DataFrame,
    tolerancia_prima: float,
    max_iter_canal: int,
    canal_opc: float,
    memo_calls: dict,
    registros_auditoria: list = None,
    corrida_id: str = None,
    m: int = None,
    shift_tes_bps: float = None,
    shift_fed_bps: float = None,
) -> None:
    """Resuelve, in-place sobre ``opts_plan``, el strike de la Call de los collares
    renovados cuya fecha de constitución ya ocurrió al corte ``fecha_cal`` y que aún no
    han sido resueltos (Req 4.3, 4.4).

    El strike se resuelve UNA sola vez, en el corte de la fecha de constitución del
    collar, usando la Curva_R_Corte / Curva_Q_Corte ya proyectadas al mes del corte
    (``r_df_corte``/``q_df_corte``) y ``interpol_vol`` — el mecanismo existente, sin
    re-implementar la proyección. El resultado se persiste en ``opts_plan`` y se memoiza
    por ``_collar_id`` en ``memo_calls``; en cortes posteriores el collar es una opción
    más para la valoración y su strike permanece inmutable (Req 4.4).

    Las opciones existentes (``_renovado == False``) nunca entran a este flujo.

    Por cada resolución de collar se acumula un Registro_Auditoria del solver (Req 4.10)
    en ``registros_auditoria`` con: ``mes_proyeccion`` (``m``), ``plazo_dias`` del collar,
    los shifts TES/FED acumulados aplicados en el corte (``shift_tes_bps``/``shift_fed_bps``),
    el ``strike_put``, el ``strike_call`` resuelto, la ``prima_neta`` y ``uso_fallback``.
    """
    if opts_plan.empty or '_renovado' not in opts_plan.columns:
        return

    # Patas Call renovadas cuyo collar nació en/antes de este corte y aún sin resolver.
    pendientes = opts_plan.loc[
        (opts_plan['_renovado'] == True)  # noqa: E712
        & (opts_plan['call_put'] == 'Call')
        & (opts_plan['_call_resuelto'] == False)  # noqa: E712
        & (opts_plan['constitucion'] <= fecha_cal)
    ]
    if pendientes.empty:
        return

    for idx_call in pendientes.index:
        collar_id = opts_plan.at[idx_call, '_collar_id']

        # Memoización: si este collar ya fue resuelto, reutilizar sin recalcular.
        if collar_id in memo_calls:
            k_call_memo, _prima_memo, _fb_memo = memo_calls[collar_id]
            opts_plan.at[idx_call, 'strike'] = float(k_call_memo)
            opts_plan.at[idx_call, '_call_resuelto'] = True
            continue

        constitucion = opts_plan.at[idx_call, 'constitucion']
        vencimiento = opts_plan.at[idx_call, 'vencimiento']
        nominal = float(opts_plan.at[idx_call, 'nominal'])
        sentido_call = opts_plan.at[idx_call, 'sentido']

        # Strike de la Put del mismo collar (fijado en la TRM del corte de constitución).
        put_msk = (
            (opts_plan['_collar_id'] == collar_id)
            & (opts_plan['call_put'] == 'Put')
        )
        if put_msk.any():
            k_put = float(opts_plan.loc[put_msk, 'strike'].iloc[0])
            sentido_put = opts_plan.loc[put_msk, 'sentido'].iloc[0]
        else:
            # Sin Put pareja (caso atípico): usar la TRM del mes de constitución.
            _trm_row = trm_df.loc[trm_df['fecha'] == pd.Timestamp(constitucion), 'trm']
            k_put = float(_trm_row.iloc[0]) if len(_trm_row) else float('nan')
            sentido_put = 'Venta' if sentido_call == 'Compra' else 'Compra'

        # Plazo del collar (constitución → vencimiento), en días.
        dias = float((pd.Timestamp(vencimiento) - pd.Timestamp(constitucion)).days)
        # TRM del corte de constitución (spot sobre el que se resuelve el collar).
        trm = k_put

        if not (np.isfinite(k_put) and k_put > 0 and dias > 0):
            # No se puede plantear el problema: conservar el strike provisional (fallback).
            opts_plan.at[idx_call, '_call_resuelto'] = True
            memo_calls[collar_id] = (float(opts_plan.at[idx_call, 'strike']),
                                     float('nan'), True)
            continue

        k_call, prima_neta, uso_fallback = resolver_canal_collar(
            k_put, trm, interpol_vol, r_df_corte, q_df_corte, dias, nominal,
            sentido_put, sentido_call, tolerancia_prima, max_iter_canal, canal_opc,
        )

        opts_plan.at[idx_call, 'strike'] = float(k_call)
        opts_plan.at[idx_call, '_call_resuelto'] = True
        memo_calls[collar_id] = (float(k_call), float(prima_neta), bool(uso_fallback))

        # Registro_Auditoria del solver (Req 4.10): mes de proyección, shifts TES/FED
        # acumulados aplicados en el corte, strike Put, strike Call resuelto, prima neta
        # y uso_fallback. Los campos no aplicables al tipo de registro quedan como None
        # (NULL en la tabla), conforme al esquema de `audit_derivados_corrida`.
        if registros_auditoria is not None:
            registros_auditoria.append({
                'corrida_id': corrida_id,
                'tipo_registro': 'COLLAR_SOLVER',
                'mes_proyeccion': m,
                'plazo_dias': int(dias),
                'shift_tes_bps': shift_tes_bps,
                'shift_fed_bps': shift_fed_bps,
                'strike_put': float(k_put),
                'strike_call': float(k_call),
                'prima_neta': float(prima_neta),
                'uso_fallback': 1 if uso_fallback else 0,
            })


# =============================================================================
# 3. VALORACIÓN POR CORTE
# =============================================================================

def _merge_curva(df: pd.DataFrame, curva: pd.DataFrame,
                  col_dias: str, nombre: str) -> pd.DataFrame:
    """Merge por vecino más cercano en Plazo_en_Dias (robusto ante tenores faltantes)."""
    orden_orig = df.index
    df_s = df.copy()
    df_s[col_dias] = df_s[col_dias].astype(np.float64)
    df_s = df_s.sort_values(col_dias)
    c_s  = curva.copy()
    c_s['Plazo_en_Dias'] = c_s['Plazo_en_Dias'].astype(np.float64)
    c_s  = c_s.sort_values('Plazo_en_Dias')
    df_s = pd.merge_asof(df_s, c_s, left_on=col_dias, right_on='Plazo_en_Dias',
                          direction='nearest').rename(columns={'Valor': nombre})
    return df_s.loc[orden_orig] if set(orden_orig) == set(df_s.index) else df_s.reset_index(drop=True)


def _fwdcop_dinamico_por_fila(dias_serie: pd.Series, trm_val: float,
                              swtibr_proy: pd.DataFrame = None,
                              lutibr_proy: pd.DataFrame = None,
                              dev_forecast: float = 0.085) -> np.ndarray:
    """Calcula el Punto_Forward_Dinamico (`fwdcop`) por fila para el corte (Req 3.6, 3.7).

    Para cada plazo `dias` (del corte al vencimiento) se deriva la Devaluacion_Implicita
    al plazo con las curvas SWTIBR/LUTIBR ya proyectadas al mes del corte y se obtiene
    `fwdcop = TRM_corte * (1 + dev)^(dias/365) - TRM_corte`. Si no hay curvas
    proyectadas disponibles (o el orquestador `calcular_dev_y_punto_forward` aún no está
    presente), se usa `dev_forecast` como fallback por fila.

    La identidad `last_trm + fwdcop = TRM*(1+dev)^(dias/365)` queda garantizada por
    construcción (misma fórmula que emplea `valorar_fwds`).
    """
    dias_arr = np.asarray(dias_serie, dtype=np.float64)
    fwdcop = np.full(dias_arr.shape, np.nan, dtype=np.float64)

    orquestador = globals().get('calcular_dev_y_punto_forward')
    curvas_ok = (swtibr_proy is not None and lutibr_proy is not None
                 and callable(orquestador))

    for i, dias in enumerate(dias_arr):
        if not np.isfinite(dias) or dias <= 0.0:
            # Filas vencidas/no vivas: no requieren fwdcop (se liquidan aparte).
            continue
        if curvas_ok:
            # El orquestador aplica el rango de validez y el fallback a dev_forecast.
            _dev, punto, _reg = orquestador(
                float(dias), float(trm_val), swtibr_proy, lutibr_proy, float(dev_forecast)
            )
            fwdcop[i] = float(punto)
        else:
            # Fallback: dev plana (dev_forecast) al plazo de la fila.
            fwdcop[i] = float(punto_forward_dinamico(float(trm_val), float(dev_forecast), float(dias)))

    return fwdcop


def _valorar_fwds_corte(fwds: pd.DataFrame, trm_val: float,
                          fwd_cop: pd.DataFrame, r_fwd: pd.DataFrame,
                          fecha_val: datetime, serie_trm: pd.Series = None,
                          swtibr_proy: pd.DataFrame = None,
                          lutibr_proy: pd.DataFrame = None,
                          dev_forecast: float = 0.085) -> pd.DataFrame:
    """Valora el plano de forwards en una fecha de corte.
    Para vencidos, usa la TRM del día de vencimiento (de serie_trm).

    El `fwdcop` de descuento se calcula dinámicamente por fila como el
    Punto_Forward_Dinamico al plazo del corte al vencimiento (Req 3.6, 3.7),
    reemplazando la lectura estática de la Curva_Forward_COP de PIP. El parámetro
    `fwd_cop` se conserva por compatibilidad de firma pero ya no alimenta el descuento.
    """
    if fwds.empty:
        return fwds.assign(dias=pd.Series(dtype=int), valoracion=pd.Series(dtype=float),
                           derecho=pd.Series(dtype=float), obligacion=pd.Series(dtype=float),
                           delta=pd.Series(dtype=float), delta_monto=pd.Series(dtype=float))
    df = fwds.copy()
    df['dias'] = (pd.to_datetime(df['vencimiento']) - fecha_val).dt.days
    # fwdcop dinámico por fila (Punto_Forward_Dinamico), en vez de fwd_cop estático de PIP.
    df['fwdcop'] = _fwdcop_dinamico_por_fila(
        df['dias'], trm_val, swtibr_proy, lutibr_proy, dev_forecast
    )
    df = _merge_curva(df, r_fwd,   'dias', 'r_fwd')

    vivos     = df['dias'] > 0
    es_compra = df['sentido'] == 'Compra'

    for op in ['Compra', 'Venta']:
        msk = vivos & (df['sentido'] == op)
        if msk.any():
            val, vptrm, vpk = valorar_fwds(
                trm_val, df.loc[msk, 'dias'], df.loc[msk, 'nominal'],
                df.loc[msk, 'tasa_negociada'], df.loc[msk, 'r_fwd'],
                df.loc[msk, 'fwdcop'], op
            )
            df.loc[msk, 'valoracion'] = np.asarray(val).flatten()
            df.loc[msk, 'derecho']    = np.asarray(vptrm * df.loc[msk, 'nominal'].to_numpy()).flatten()
            df.loc[msk, 'obligacion'] = np.asarray(vpk * df.loc[msk, 'nominal'].to_numpy()).flatten()

    # Vencidos: liquidar con la TRM del día de vencimiento (no del corte)
    vencidos = ~vivos
    if vencidos.any() and serie_trm is not None:
        venc_compra = vencidos & es_compra
        venc_venta  = vencidos & ~es_compra
        if venc_compra.any():
            trm_liq_c = df.loc[venc_compra, 'vencimiento'].apply(
                lambda v: float(serie_trm.asof(pd.Timestamp(v)))
            ).to_numpy()
            df.loc[venc_compra, 'valoracion'] = (trm_liq_c - df.loc[venc_compra, 'tasa_negociada'].to_numpy()) * df.loc[venc_compra, 'nominal'].to_numpy()
        if venc_venta.any():
            trm_liq_v = df.loc[venc_venta, 'vencimiento'].apply(
                lambda v: float(serie_trm.asof(pd.Timestamp(v)))
            ).to_numpy()
            df.loc[venc_venta, 'valoracion'] = (df.loc[venc_venta, 'tasa_negociada'].to_numpy() - trm_liq_v) * df.loc[venc_venta, 'nominal'].to_numpy()
    elif vencidos.any():
        # Fallback sin serie: usar trm_val del corte
        df.loc[vencidos & es_compra,  'valoracion'] = (trm_val - df.loc[vencidos & es_compra, 'tasa_negociada']) * df.loc[vencidos & es_compra, 'nominal']
        df.loc[vencidos & ~es_compra, 'valoracion'] = (df.loc[vencidos & ~es_compra, 'tasa_negociada'] - trm_val) * df.loc[vencidos & ~es_compra, 'nominal']

    df['delta']       = np.where(vivos, 1.0, 0.0)
    df['delta_monto'] = np.where(es_compra,
                                  df['nominal'] * df['delta'],
                                 -df['nominal'] * df['delta'])
    return df


def _valorar_opciones_corte(opts: pd.DataFrame, trm_val: float,
                              r_df: pd.DataFrame, q_df: pd.DataFrame,
                              interpol_vol, fecha_val: datetime,
                              m: int, trm_df: pd.DataFrame,
                              fecha_corte_dt: datetime,
                              nivel_actual: float,
                              serie_trm: pd.Series = None) -> pd.DataFrame:
    """Valora el plano de opciones en una fecha de corte (Black-Scholes).

    Las opciones vencidas se liquidan con el payoff intrínseco evaluado a la TRM del
    DÍA DE VENCIMIENTO (`serie_trm.asof(vencimiento)`), no a la TRM del corte actual.
    Así su valor queda CONGELADO tras el vencimiento y no se mueve en cortes
    posteriores (mismo criterio que los forwards vencidos). Si no se provee
    `serie_trm`, cae al comportamiento anterior (TRM del corte) como fallback.
    """
    if opts.empty:
        return opts.assign(dias=pd.Series(dtype=int), t=pd.Series(dtype=float),
                           r=pd.Series(dtype=float), q=pd.Series(dtype=float),
                           vol=pd.Series(dtype=float), valoracion=pd.Series(dtype=float),
                           delta=pd.Series(dtype=float), delta_monto=pd.Series(dtype=float))
    df = opts.copy()
    df['dias'] = (pd.to_datetime(df['vencimiento']) - fecha_val).dt.days
    df['t']    = df['dias'] / 365
    df = _merge_curva(df, r_df, 'dias', 'r')
    df = _merge_curva(df, q_df, 'dias', 'q')

    vivos = df['dias'] > 0

    # Volatilidad interpolada (memoizada)
    vols = np.full(len(df), np.nan)
    for i in df.index[vivos]:
        vols[i] = interpol_vol(float(df.at[i, 'strike']), int(df.at[i, 'dias']))
    df['vol'] = vols

    # Valoración vigente — una sola pasada vectorizada
    pv = df.loc[vivos]
    if len(pv):
        df.loc[vivos, 'valoracion'] = precio_bs_signed(
            np.full(len(pv), trm_val),
            pv['strike'].to_numpy(), pv['vol'].to_numpy(), pv['t'].to_numpy(),
            pv['r'].to_numpy(), pv['q'].to_numpy(), pv['nominal'].to_numpy(),
            pv['call_put'].to_numpy(), pv['sentido'].to_numpy()
        )

    # Payoff intrínseco para opciones vencidas.
    # La liquidación se evalúa con la TRM del DÍA DE VENCIMIENTO (congelada), no con
    # la TRM del corte actual: una opción vencida ya liquidó y su valor no debe
    # seguir moviéndose en cortes posteriores.
    cp, tt = df['call_put'], df['sentido']
    venc   = ~vivos
    if venc.any():
        if serie_trm is not None:
            trm_liq = df.loc[venc, 'vencimiento'].apply(
                lambda v: float(serie_trm.asof(pd.Timestamp(v)))
            ).to_numpy()
        else:
            # Fallback: TRM del corte (comportamiento anterior).
            trm_liq = np.full(int(venc.sum()), trm_val)
        strike_v = df.loc[venc, 'strike'].to_numpy()
        nom_v    = df.loc[venc, 'nominal'].to_numpy()
        k_s = (strike_v - trm_liq) * nom_v   # payoff Put (TRM < strike)
        s_k = (trm_liq - strike_v) * nom_v   # payoff Call (TRM > strike)
        cpv = cp[venc].to_numpy()
        ttv = tt[venc].to_numpy()
        val_venc = np.zeros(int(venc.sum()))
        val_venc = np.where((cpv == 'Put')  & (ttv == 'Compra'),  np.maximum(k_s, 0), val_venc)
        val_venc = np.where((cpv == 'Call') & (ttv == 'Compra'),  np.maximum(s_k, 0), val_venc)
        val_venc = np.where((cpv == 'Put')  & (ttv == 'Venta'),  -np.maximum(k_s, 0), val_venc)
        val_venc = np.where((cpv == 'Call') & (ttv == 'Venta'),  -np.maximum(s_k, 0), val_venc)
        df.loc[venc, 'valoracion'] = val_venc
        df.loc[venc, 'liquidacion'] = val_venc

    # Delta vigente
    for cpv in ['Call', 'Put']:
        for sv, sg in [('Compra', 1), ('Venta', -1)]:
            msk = vivos & (cp == cpv) & (tt == sv)
            if msk.any():
                df.loc[msk, 'delta'] = sg * delta_bs(
                    trm_val, df.loc[msk, 'strike'], df.loc[msk, 'vol'],
                    df.loc[msk, 't'], df.loc[msk, 'r'], df.loc[msk, 'q'], cpv
                )

    # Delta inicial (base para delta hedge)
    if m == 0:
        df['t_inicial'] = (pd.to_datetime(df['vencimiento']) - fecha_corte_dt).dt.days / 365
        S0, vol0, r0, q0 = nivel_actual, 0.1662, 0.0985, 0.0385          # [REVISAR]
    else:
        fecha_prev  = fecha_val - relativedelta(days=30)
        df['t_inicial'] = (pd.to_datetime(df['vencimiento']) - fecha_prev).dt.days / 365
        trm_prev = trm_df.loc[trm_df['fecha'] == fecha_prev, 'trm']
        S0  = float(trm_prev.iloc[0]) if len(trm_prev) else trm_val
        vol0, r0, q0 = 0.1662, 0.125, 0.035                              # [REVISAR]

    for cpv in ['Call', 'Put']:
        for sv, sg in [('Compra', 1), ('Venta', -1)]:
            msk = (cp == cpv) & (tt == sv)
            if msk.any():
                df.loc[msk, 'delta_inicial'] = sg * delta_bs(
                    S0, df.loc[msk, 'strike'], vol0,
                    df.loc[msk, 't_inicial'], r0, q0, cpv
                )

    df['delta_monto'] = (df.get('delta', pd.Series(0.0, index=df.index)) * df['nominal']).fillna(0)
    return df


# =============================================================================
# 4. ORQUESTADOR PRINCIPAL
# =============================================================================

def _normalizar_plano_fwds(fwds: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza los tipos de un plano de forwards inyectado (Opción B) a los MISMOS tipos
    que produce `_cargar_posiciones`: fechas a datetime, numéricos a float. Cuando el
    plano viene de SQLite (tablas _proy) las columnas llegan como texto/object; sin esta
    normalización, comparaciones como `vencimiento > fecha_corte_dt` lanzan TypeError.
    Solo toca columnas presentes; no reordena ni descarta columnas.
    """
    if fwds is None or len(fwds) == 0:
        return fwds if fwds is not None else pd.DataFrame()
    df = fwds.copy()
    for col in ('constitucion', 'vencimiento'):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')
    for col in ('nominal', 'tasa_negociada'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.reset_index(drop=True)


def _normalizar_plano_opts(opts: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos de un plano de opciones inyectado (Opción B), como
    `_cargar_posiciones`: fechas a datetime, strike/nominal a float."""
    if opts is None or len(opts) == 0:
        return opts if opts is not None else pd.DataFrame()
    df = opts.copy()
    for col in ('constitucion', 'vencimiento'):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')
    for col in ('strike', 'nominal'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    # D2 (round-trip de flags): SQLite devuelve los booleanos como int 0/1 y los `None`
    # como NaN. Retipar los flags de control a su tipo correcto para que
    # `flags_recargados == flags_persistidos`. Solo si la columna existe (retrocompatible
    # con planos previos al fix).
    for col in ('_renovado', '_call_resuelto'):
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(bool)
    if '_collar_id' in df.columns:
        # str o None (NaN -> None); nunca float NaN.
        df['_collar_id'] = df['_collar_id'].apply(
            lambda v: None if (v is None or (isinstance(v, float) and pd.isna(v)) or
                               (isinstance(v, str) and v == '')) else str(v)
        )
    return df.reset_index(drop=True)


def _normalizar_plano_swaps(swaps: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos de un plano de swaps inyectado (Opción B), como `_cargar_swaps`:
    fechas a datetime, columnas de nominal/facial/vpn/valor_mercado a float."""
    if swaps is None or len(swaps) == 0:
        return swaps if swaps is not None else pd.DataFrame()
    df = swaps.copy()
    for col in ('fecha_constitucion', 'fecha_vencimiento'):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')
    for col in ('nominal_der', 'facial_der', 'vpn_derecho', 'nominal_obl',
                'facial_obl', 'vpn_obligacion', 'valor_mercado'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.reset_index(drop=True)


def valorar_portafolio_derivados(
    fecha_corte: str,
    fecha_fin: str,
    metodo: int,
    nivel_actual: float,
    activos_usd: float,
    portafolio_pesos: float,
    senda_trm: list = None,
    dev_forecast: float = 0.085,
    canal_opc: float = 1650,
    rent_usd: float = 0,
    port_analizado: int = 1,
    metodo_params: dict = None,
    tolerancia_prima: float = 1.0,
    max_iter_canal: int = 100,
    persistir_auditoria: bool = True,
    corrida_id: str = None,
    posiciones_iniciales: dict = None,
    devolver_plano_cierre: bool = False,
    fecha_mercado: str = None,
    delta_curva_base: dict = None,
) -> dict:
    """
    Valora opciones y forwards bajo un escenario de TRM.

    Parámetros
    ----------
    fecha_corte      : 'YYYY-MM-DD'  fecha del portafolio base
    fecha_fin        : 'YYYY-MM-DD'  fin del horizonte de proyección
    metodo           : 1=Nominal Estable  2=Cambiando Nominal  3=Exposición Objetivo
                       4=Dejar vencer collares  5=Delta Hedge
    nivel_actual     : TRM spot en fecha_corte
    activos_usd      : posición neta en USD (scalar)
    portafolio_pesos : valor total del portafolio en COP
    senda_trm        : lista de niveles TRM mensuales [hoy, mes1, mes2, ..., mesN]
                       Si None, genera senda plana al nivel_actual.
    dev_forecast     : devaluación anual implícita usada como fallback de la
                       Devaluacion_Implicita (Req 3.10). Rango válido [-0.5, 2.0].
    canal_opc        : spread Call–Put en collares (COP), usado como fallback del
                       Solver_Canal (Req 4.9).
    port_analizado   : 1=Total  2=Autos (filtra portafolio 03-AT)
    metodo_params    : dict para método 3 con claves:
                       exposicion_obj, trato_exposicion, momento_exposicion, opciones_sobre_largo
    tolerancia_prima : cota máxima del |prima neta| del collar en COP por unidad de
                       nominal para el Solver_Canal (Req 4.6). Rango [0.0001, 1000.0].
    max_iter_canal   : número máximo de iteraciones del Solver_Canal (Req 4.8).
                       Rango [1, 1000].
    persistir_auditoria : si True, escribe los Registro_Auditoria acumulados en la
                       tabla `audit_derivados_corrida` al final de la corrida (Req 5.3).
    corrida_id       : identificador que agrupa la corrida completa en la auditoría;
                       si None se genera uno automáticamente al persistir.
    posiciones_iniciales : plano de posiciones a usar como base EN LUGAR de leer de
                       `hist_*`. Dict `{'fwds': DataFrame, 'opts': DataFrame,
                       'swaps': DataFrame}` (claves faltantes → se leen de `hist_*`).
                       Habilita el encadenamiento por tramos (feature
                       proyeccion-por-tramos-multiactivo, Opción B): el plano de cierre de
                       un tramo es el plano de inicio del siguiente. Si es None (default),
                       el comportamiento es IDÉNTICO al actual (lee `hist_*`). Los
                       DataFrames deben traer las MISMAS columnas que producen
                       `_cargar_posiciones` (fwds: constitucion, vencimiento, nominal,
                       sentido, portafolio, tasa_negociada; opts: constitucion,
                       vencimiento, call_put, sentido, portafolio, strike, nominal) y
                       `_cargar_swaps` (esquema de hist_swaps).
    devolver_plano_cierre : si True, el dict de retorno incluye la clave `plano_cierre`
                       = `{'fwds','opts','swaps'}` con las posiciones VIVAS al `fecha_fin`
                       (vencimiento > fecha_fin), tomadas de los planos ya proyectados y
                       renovados. Para el encadenamiento por tramos (Opción B).
    fecha_mercado    : fecha ('YYYY-MM-DD') de la que se leen los INSUMOS DE MERCADO
                       (curvas forward `hist_curvas_derivados`, volatilidad
                       `hist_vol_derivados`, curvas OTC `hist_curvas_otc`, y los activos
                       USD de `portafolio_clasificado`). Las curvas se PROYECTAN en el
                       tiempo aplicando los shifts de `metodo_params`; no existen curvas
                       reportadas para fechas futuras, por lo que en el encadenamiento por
                       tramos se pasa aquí la fecha base REAL (donde sí hay datos) mientras
                       `fecha_corte` apunta al inicio del tramo (para las posiciones). Si es
                       None (default), se usa `fecha_corte` (comportamiento actual, punta a
                       punta intacto).
    delta_curva_base : desplazamiento BASE acumulado de la curva de valoración (dict por
                       tipo de curva: `delta_tes_cop`, `delta_tes_uvr`, `delta_fed_bps`,
                       `delta_10t_bps`), que representa el delta ACUMULADO de los tramos
                       previos (0..i-1) en el encadenamiento por tramos. Cuando se provee,
                       la curva de ARRANQUE del tramo (progreso=0) parte de
                       `base_EA + delta_curva_base` en vez de la base cruda del PIP, de modo
                       que la curva avance de forma continua entre tramos (igual que la
                       senda punta a punta); encima de ese desplazamiento, los deltas de
                       `metodo_params` del tramo se siguen aplicando escalados por
                       `progreso`. Se aplica con la MISMA semántica de nodos/interpolación
                       que los deltas por corte, como offset constante (progreso=1) para
                       todo el tramo. Si es None (default), el comportamiento es
                       byte-idéntico al actual (tramo 0 y punta a punta nunca lo pasan).

    Retorna
    -------
    dict con DataFrames: {'opciones', 'forwards', 'activos', 'swaps'}.
    Si `devolver_plano_cierre=True`, incluye además {'plano_cierre': {...}}.
    """
    # -- Validación de rango fail-fast de parámetros configurables (Req 3.10, 4.6, 4.8) --
    dev_forecast = float(dev_forecast)
    if not (-0.5 <= dev_forecast <= 2.0):
        raise ValueError(
            f"dev_forecast={dev_forecast!r} fuera del rango válido [-0.5, 2.0] (Req 3.10)."
        )
    tolerancia_prima = float(tolerancia_prima)
    if not (0.0001 <= tolerancia_prima <= 1000.0):
        raise ValueError(
            f"tolerancia_prima={tolerancia_prima!r} fuera del rango válido "
            f"[0.0001, 1000.0] (Req 4.6)."
        )
    max_iter_canal = int(max_iter_canal)
    if not (1 <= max_iter_canal <= 1000):
        raise ValueError(
            f"max_iter_canal={max_iter_canal!r} fuera del rango válido [1, 1000] (Req 4.8)."
        )

    if metodo_params is None:
        metodo_params = {}

    # Asegurar tipos numéricos
    nivel_actual = float(nivel_actual)
    activos_usd = float(activos_usd)
    portafolio_pesos = float(portafolio_pesos)

    fecha_corte_dt = datetime.strptime(fecha_corte, '%Y-%m-%d')
    fecha_fin_dt   = datetime.strptime(fecha_fin,   '%Y-%m-%d')
    fecha_str_comp = fecha_corte_dt.strftime('%Y%m%d')

    # Si no se pasa senda, generar senda plana al nivel actual
    if senda_trm is None:
        n_meses = (fecha_fin_dt.year - fecha_corte_dt.year) * 12 + (fecha_fin_dt.month - fecha_corte_dt.month)
        senda_trm = [nivel_actual] * (n_meses + 1)

    year_left = (fecha_fin_dt - fecha_corte_dt).days
    if metodo == 5:
        year_left = 150

    # Fecha de la que se leen los INSUMOS DE MERCADO. En punta a punta == fecha_corte
    # (retrocompatible). En el encadenamiento por tramos se pasa la fecha base REAL (las
    # curvas de mercado no existen para fechas futuras; se proyectan con los shifts).
    _fecha_mkt = fecha_mercado or fecha_corte

    print(f"[AXIS] Cargando inputs de mercado para {_fecha_mkt}...")
    _conn_curvas = sqlite3.connect(DB_PATH)
    try:
        fwd_cop, r_df, q_df, r_fwd, r_ea, q_ea = _cargar_curvas(_fecha_mkt, _conn_curvas)
        interpol_vol = construir_interpolador_vol(_cargar_vol(_fecha_mkt, _conn_curvas))
    finally:
        _conn_curvas.close()

    # D3 — Encadenamiento por tramos: aplicar el delta de curva ACUMULADO de los tramos
    # previos (0..i-1) como offset BASE de la curva de valoración, para que el tramo i
    # arranque (progreso=0) en `base + delta_curva_base` en vez de la base cruda del PIP,
    # igual que la senda continua de punta a punta. Si `delta_curva_base` es None (tramo 0 y
    # punta a punta, que nunca lo pasan) el comportamiento es byte-idéntico al actual.
    # Encima de este offset, los deltas de `metodo_params` del tramo se siguen aplicando
    # escalados por `progreso` en el loop por corte (misma mecánica de nodos/interpolación).
    if delta_curva_base:
        r_ea, q_ea, r_fwd = _aplicar_delta_curva_base_ea(r_ea, q_ea, r_fwd, delta_curva_base)
        # Regenerar las curvas continuas (BS) desde las EA desplazadas: cont = ln(1 + ea),
        # misma transformación que usa el loop por corte al aplicar deltas.
        if r_ea is not None:
            r_df = r_ea.copy()
            r_df['Valor'] = np.log(1 + r_ea['Valor'].to_numpy())
        if q_ea is not None:
            q_df = q_ea.copy()
            q_df['Valor'] = np.log(1 + q_ea['Valor'].to_numpy())
    trm_hist     = _cargar_trm_hist(fecha_corte_dt)
    serie        = _construir_serie_trm(senda_trm, fecha_corte_dt)

    # Cortes de valoración: inicio + fin de cada mes hasta fecha_fin
    fechas_cortes = pd.DatetimeIndex([fecha_corte]).union(
        pd.date_range(start=fecha_corte, end=fecha_fin, freq='ME')
    )
    diffs = (fechas_cortes - pd.to_datetime(fecha_corte)).days.tolist()

    # Plano de posiciones base: inyectado (encadenamiento por tramos, Opción B) o leído de
    # hist_* (comportamiento actual). Las curvas de swaps SIEMPRE se leen de hist_curvas_otc
    # de la fecha_corte (son insumo de mercado, no posiciones encadenables).
    _pi = posiciones_iniciales or {}
    conn = sqlite3.connect(DB_PATH)
    try:
        if 'fwds' in _pi and 'opts' in _pi:
            # Copias defensivas para no mutar los DataFrames del llamador. Cuando el plano
            # viene inyectado (encadenamiento por tramos, Opción B) suele provenir de
            # SQLite (tablas _proy), donde las fechas se leen como TEXTO y los numéricos
            # como object. Se normalizan a los MISMOS tipos que produce `_cargar_posiciones`
            # (datetime en fechas, float en nominal/strike/tasa) para que el resto del
            # motor —que asume esos tipos— funcione idéntico a cuando lee de hist_*.
            fwds_base = _normalizar_plano_fwds(_pi['fwds'].copy())
            opts_base = _normalizar_plano_opts(_pi['opts'].copy())
        else:
            fwds_base, opts_base = _cargar_posiciones(fecha_corte, conn, port_analizado)
        if 'swaps' in _pi and _pi['swaps'] is not None:
            swaps_base = _normalizar_plano_swaps(_pi['swaps'].copy())
        else:
            swaps_base = _cargar_swaps(fecha_corte, conn)
        # Curvas OTC de swaps: insumo de mercado → fecha base real (_fecha_mkt).
        curvas_swaps = _cargar_curvas_swaps(_fecha_mkt, conn)
    finally:
        conn.close()

    # D3 — Encadenamiento por tramos: aplicar el mismo offset BASE acumulado a las curvas
    # de swaps (misma semántica que el loop por corte, a progreso constante). None (tramo 0
    # y punta a punta) => sin cambios, comportamiento byte-idéntico al actual.
    if delta_curva_base:
        curvas_swaps = _aplicar_delta_curva_base_swaps(curvas_swaps, delta_curva_base)

    # Calcular distribución dinámica de forwards por portafolio (para método 3)
    _fwds_vigentes = fwds_base.loc[fwds_base['vencimiento'] > fecha_corte_dt].copy()
    # Agrupar 03-AT con 03-DW para el cálculo de distribución
    _fwds_vigentes['_port_alloc'] = _fwds_vigentes['portafolio'].replace({' 03-AT  ': ' 03-DW  '})
    _total_nom_fwd = _fwds_vigentes['nominal'].sum()
    if _total_nom_fwd > 0:
        alloc_fwd = (_fwds_vigentes.groupby('_port_alloc')['nominal'].sum() / _total_nom_fwd).to_dict()
    else:
        alloc_fwd = ALLOC_FWD  # fallback al hardcodeado si no hay forwards

    # Calcular peso de activos USD por portafolio de opciones (para método 3)
    # Activos USD = títulos con moneda diferente a COP y UVR, distribuidos por portafolio
    with sqlite3.connect(DB_PATH) as _conn_usd:
        _df_titulos_usd = pd.read_sql_query(
            "SELECT portafolio, valor_mercado FROM portafolio_clasificado "
            "WHERE clase_origen = 'TITULO' AND moneda NOT IN ('COP','UVR') "
            "AND fecha_reporte = ?",
            _conn_usd,
            params=(_fecha_mkt,),
        )
    _df_titulos_usd['valor_mercado'] = pd.to_numeric(_df_titulos_usd['valor_mercado'], errors='coerce').fillna(0)
    # Mapear portafolios de títulos a portafolios de opciones (01→01-OP, 02→02-OP, 03→03-OP)
    _df_titulos_usd['_port_opc'] = _df_titulos_usd['portafolio'].str[:2].map({'01': '01-OP', '02': '02-OP', '03': '03-OP'})
    _total_usd = _df_titulos_usd['valor_mercado'].sum()
    if _total_usd > 0:
        peso_act = (_df_titulos_usd.groupby('_port_opc')['valor_mercado'].sum() / _total_usd).to_dict()
    else:
        peso_act = PESO_ACT  # fallback

    print(f"[AXIS] {len(diffs)} corte(s) de valoración.")

    nivel_final  = float(serie.iloc[-1])
    port_usd_sig = activos_usd * (1 + rent_usd)
    port_total   = portafolio_pesos / nivel_final + port_usd_sig

    # TRM completa para renovaciones (se construye una vez)
    trm_fut    = serie[serie.index > fecha_corte_dt]
    trm_fut_df = pd.DataFrame({'fecha': trm_fut.index, 'trm': trm_fut.to_numpy()})
    trm_df_full = pd.concat([trm_hist, trm_fut_df], ignore_index=True)

    # =================================================================
    # RENOVACIÓN DE POSICIONES (una sola vez, fuera del loop)
    # =================================================================
    opts_plan = opts_base.loc[opts_base['vencimiento'] >= fecha_corte_dt].copy()
    fwds_plan = fwds_base.loc[fwds_base['vencimiento'] > fecha_corte_dt].copy().reset_index(drop=True)

    if metodo != 5:
        # Iteraciones de renovación dinámicas: cada renovación cubre 100 días, así que
        # se necesitan al menos ceil(year_left/100) bloques para cubrir el horizonte.
        # Se agregan 2 de margen por la desalineación entre los bloques de 100 días y
        # los cortes de fin de mes (evita que el ÚLTIMO corte quede sin renovación
        # vigente y el nominal caiga espuriamente). Mínimo 5 (comportamiento previo).
        import math as _math
        _iter_fwd = max(5, _math.ceil(year_left / 100) + 2)
        fwds_plan = _renovar_fwds(
            fwds_plan, trm_df_full, iteraciones=_iter_fwd, dev_forecast=dev_forecast,
            r_ea=r_ea, q_ea=q_ea, metodo_params=metodo_params,
            fecha_corte_dt=fecha_corte_dt, horizonte_dias=year_left,
        )

    if metodo in (1, 3):
        opts_plan = _renovar_opciones(opts_plan, trm_df_full, canal_opc)

    # Memoización de la resolución del strike Call por collar renovado (Req 4.4): se
    # resuelve una sola vez, en el corte de la fecha de constitución, y se conserva
    # inmutable en los cortes posteriores.
    memo_calls_renovados: dict = {}
    # Acumulador de Registro_Auditoria del solver (Req 4.10; persistencia en tarea 14).
    registros_auditoria: list = []

    opc_cortes, fwd_cortes, act_cortes, swap_cortes = [], [], [], []

    for m in diffs:
            fecha_cal = fecha_corte_dt + relativedelta(days=m)

            # TRM completa = histórica real + proyección del escenario
            trm_df = trm_df_full

            # TRM en el corte
            trm_val = float(nivel_actual if m == 0 else serie.asof(fecha_cal))

            # Filtrar planos por fecha de constitución (solo lo que ya existía en fecha_cal)
            opts = opts_plan.loc[opts_plan['constitucion'] <= fecha_cal].copy()
            fwds = fwds_plan.loc[fwds_plan['constitucion'] <= fecha_cal].copy()

            # =================================================================
            # EXPOSICIÓN OBJETIVO — cálculo del nominal objetivo (metodo 3)
            # =================================================================
            if metodo == 3 and m > 0:
                r_opc = float(r_df.iloc[453]['Valor'])   # [REVISAR] índice 453 ≈ plazo 453 días
                q_opc = float(q_df.iloc[453]['Valor'])
                trm_post    = trm_df.loc[trm_df['fecha'] > fecha_corte_dt, 'trm']
                spot_proy   = float(trm_post.mean() * 0.30 + 3551.17 * 0.7)  # [REVISAR]
                vol_put     = interpol_vol(spot_proy, 453)
                vol_call    = interpol_vol(spot_proy + canal_opc, 453)
                exp_obj     = metodo_params.get('exposicion_obj', 0.3)
                trato       = metodo_params.get('trato_exposicion', 1)
                momento     = metodo_params.get('momento_exposicion', 1)
                opc_largo   = metodo_params.get('opciones_sobre_largo', 0.7)
                d_call_proy = -delta_bs(trm_val, spot_proy + canal_opc, vol_call, 1.21, r_opc, q_opc, 'Call')
                d_put_proy  =  delta_bs(trm_val, spot_proy,             vol_put,  1.21, r_opc, q_opc, 'Put')
                delta_proy  = d_call_proy + d_put_proy
                da_call = -delta_bs(nivel_actual, 5229, vol_call, 1.21, r_opc, q_opc, 'Call')  # [REVISAR]
                da_put  =  delta_bs(nivel_actual, 3844, vol_put,  1.21, r_opc, q_opc, 'Put')   # [REVISAR]
                delta_act   = da_call + da_put

                if trato == 1 and momento == 2:
                    nom_fwds_proy = ((exp_obj * port_total) - port_usd_sig - (delta_proy * opc_largo * port_usd_sig)) / (1 + delta_proy * opc_largo)
                elif trato == 2 and momento == 2:
                    nom_fwds_proy = (exp_obj * port_total) - port_usd_sig
                elif trato == 2 and momento == 1:
                    nom_fwds_proy = (exp_obj * (portafolio_pesos / nivel_actual + activos_usd)) - activos_usd
                else:  # trato==1, momento==1
                    nom_fwds_proy = ((exp_obj * (portafolio_pesos / nivel_actual + activos_usd)) - activos_usd - (delta_act * opc_largo * activos_usd)) / (1 + delta_act * opc_largo)

            # =================================================================
            # FORWARDS
            # =================================================================
            if metodo != 5:
                # Nuevos forwards para cubrir el gap de exposición (metodo 3)
                if metodo == 3 and m > 0:
                    vivos_nom   = fwds.loc[fwds['vencimiento'] > fecha_cal, 'nominal'].sum()
                    gap         = nom_fwds_proy - vivos_nom
                    fechas_biz  = pd.bdate_range(fecha_corte_dt + relativedelta(days=1),
                                                  fecha_cal + relativedelta(days=1000))
                    filas_nvo = []
                    for port_key, alloc in alloc_fwd.items():
                        nom_port = gap * alloc
                        step     = STEP_FWD[port_key]
                        q_lots   = int(round(abs(nom_port / step), 0))
                        # 4 tranches separadas 67 días hábiles cada una  # [REVISAR]
                        lf_port  = pd.to_datetime(list(chain.from_iterable(
                            [fechas_biz[x * 67: x * 67 + q_lots] for x in range(4)]
                        )))
                        for fecha_const in lf_port:
                            filas_nvo.append({
                                'constitucion': fecha_const,
                                'vencimiento':  fecha_const + timedelta(days=93),
                                'nominal':      step,
                                'sentido':      'Compra' if nom_port > 0 else 'Venta',
                                'portafolio':   port_key,
                            })
                    if filas_nvo:
                        nvo = pd.DataFrame(filas_nvo)
                        nvo = nvo.merge(trm_df, left_on='constitucion', right_on='fecha', how='left')
                        # tasa_negociada = TRM * (1 + dev)^(93/365) con la Devaluacion_Implicita
                        # al plazo 93 días en el mes de la fecha de constitución (Req 3.2),
                        # con dev_forecast como fallback (Req 3.9).
                        nvo['tasa_negociada'] = _tasa_negociada_dev_implicita(
                            nvo['constitucion'], nvo['trm'].astype(np.float64).to_numpy(),
                            plazo_dias=93, dev_forecast=dev_forecast, r_ea=r_ea, q_ea=q_ea,
                            metodo_params=metodo_params, fecha_corte_dt=fecha_corte_dt,
                            horizonte_dias=year_left,
                        )
                        nvo = nvo.drop(columns=['fecha', 'trm']).dropna(subset=['tasa_negociada'])
                        fwds = pd.concat([fwds, nvo], ignore_index=True)

            # =================================================================
            # OPCIONES
            # =================================================================
            if metodo == 5:
                opts = opts_base.loc[
                    (opts_base['constitucion'] >= datetime(2024, 1, 11)) &   # [REVISAR]
                    (opts_base['constitucion'] <= datetime(2024, 2, 5))
                ].copy()
                if port_analizado == 2:
                    opts = opts.loc[opts['portafolio'] == '03-OM']

            # Nuevos collares para cubrir exposición (metodo 3, m > 0)
            if metodo == 3 and m > 0:
                def _sum_fwd(por, op):
                    return fwds.loc[(fwds['vencimiento'] > fecha_cal) &
                                    (fwds['portafolio'] == por) &
                                    (fwds['sentido'] == op), 'nominal'].sum()
                def _sum_opc(pors):
                    return opts.loc[(opts['vencimiento'] > fecha_cal) &
                                    (opts['portafolio'].isin(pors)), 'nominal'].sum()

                nom_col = {
                    '01-OP': ((_sum_fwd(' 01-DW  ', 'Compra') - _sum_fwd(' 01-DW  ', 'Venta')
                               + port_usd_sig * peso_act.get('01-OP', 0)) * opc_largo) - _sum_opc(['01-OP']) / 2,
                    '02-OP': ((_sum_fwd(' 02-DW  ', 'Compra') - _sum_fwd(' 02-DW  ', 'Venta')
                               + port_usd_sig * peso_act.get('02-OP', 0)) * opc_largo) - _sum_opc(['02-OP']) / 2,
                    '03-OP': ((_sum_fwd(' 03-DW  ', 'Compra') - _sum_fwd(' 03-DW  ', 'Venta')
                               + _sum_fwd(' 03-AT  ', 'Compra') - _sum_fwd(' 03-AT  ', 'Venta')
                               + port_usd_sig * peso_act.get('03-OP', 0)) * opc_largo) - _sum_opc(['03-OP', '03-OM']) / 2,
                }
                signo_neg = all(v < 0 for v in nom_col.values())
                lf_opc = trm_df.loc[trm_df['fecha'] > fecha_cal - timedelta(days=m), 'fecha'].tolist()
                filas_opc = []
                for por, nom_v in nom_col.items():
                    nom_diario = abs(nom_v) / (year_left + 1)
                    for fecha_const in lf_opc[:year_left + 1]:
                        for cp in ['Put', 'Call']:
                            filas_opc.append({
                                'portafolio':  por,
                                'nominal':     nom_diario,
                                'call_put':    cp,
                                'sentido':     ('Venta' if signo_neg else 'Compra') if cp == 'Put' else ('Compra' if signo_neg else 'Venta'),
                                'constitucion': pd.to_datetime(fecha_const),
                                'vencimiento': pd.to_datetime(fecha_const) + pd.to_timedelta(1.5 * 365, unit='D'),
                            })
                if filas_opc:
                    nvo_opc = pd.DataFrame(filas_opc)
                    nvo_opc = nvo_opc.merge(trm_df, left_on='constitucion', right_on='fecha', how='left')
                    nvo_opc.loc[nvo_opc['call_put'] == 'Put',  'strike'] = nvo_opc['trm']
                    nvo_opc.loc[nvo_opc['call_put'] == 'Call', 'strike'] = nvo_opc['trm'] + canal_opc
                    nvo_opc = nvo_opc.dropna(subset=['strike']).drop(columns=['fecha', 'trm'])
                    opts = pd.concat([opts, nvo_opc], ignore_index=True)

            opts = opts.loc[opts['constitucion'] < fecha_cal].reset_index(drop=True)

            # =================================================================
            # VALORACIÓN EN EL CORTE
            # =================================================================
            # Proyectar curvas de valoración según progreso
            n_cortes = len(diffs)
            idx_corte = diffs.index(m)
            progreso = idx_corte / max(n_cortes - 1, 1)

            # Aplicar delta de curvas a forwards y opciones
            # Lógica correcta: EA base + delta → reconvertir a continua para opciones
            r_fwd_corte = r_fwd.copy()
            # Curvas EA proyectadas del corte (None si no se aplican deltas en este mes),
            # reiniciadas por iteración para no arrastrar estado de cortes previos.
            r_ea_corte = None
            q_ea_corte = None

            _delta_cop = metodo_params.get('delta_tes_cop') if metodo_params else None
            _delta_uvr = metodo_params.get('delta_tes_uvr') if metodo_params else None
            _delta_fed = metodo_params.get('delta_fed_bps', 0) if metodo_params else 0
            _delta_10t = metodo_params.get('delta_10t_bps', 0) if metodo_params else 0

            if (_delta_cop or _delta_fed or _delta_10t) and progreso > 0:
                # --- r (COP): EA + delta → continua ---
                r_ea_corte = r_ea.copy()
                if _delta_cop:
                    plazos_tes = sorted(_delta_cop.keys())
                    for i in r_ea_corte.index:
                        plazo = int(r_ea_corte.at[i, 'Plazo_en_Dias'])
                        delta = _interpolar_delta(plazo, plazos_tes, _delta_cop)
                        r_ea_corte.at[i, 'Valor'] = r_ea_corte.at[i, 'Valor'] + delta * progreso
                    # También mover r_fwd (tasa simple) proporcionalmente
                    for i in r_fwd_corte.index:
                        plazo = int(r_fwd_corte.at[i, 'Plazo_en_Dias'])
                        delta = _interpolar_delta(plazo, plazos_tes, _delta_cop)
                        r_fwd_corte.at[i, 'Valor'] = r_fwd_corte.at[i, 'Valor'] + delta * progreso
                # Convertir EA → continua: cont = ln(1 + ea)
                r_df_corte = r_ea_corte.copy()
                r_df_corte['Valor'] = np.log(1 + r_ea_corte['Valor'].to_numpy())

                # --- q (USD): EA + delta → continua ---
                q_ea_corte = q_ea.copy()
                if _delta_fed or _delta_10t:
                    delta_fed = _delta_fed / 10000
                    delta_10t = _delta_10t / 10000
                    plazo_fed, plazo_10t = 1, 3650
                    for i in q_ea_corte.index:
                        plazo = int(q_ea_corte.at[i, 'Plazo_en_Dias'])
                        if plazo <= plazo_fed:
                            delta = delta_fed
                        elif plazo >= plazo_10t:
                            delta = delta_10t
                        else:
                            peso = (plazo - plazo_fed) / (plazo_10t - plazo_fed)
                            delta = delta_fed + peso * (delta_10t - delta_fed)
                        q_ea_corte.at[i, 'Valor'] = q_ea_corte.at[i, 'Valor'] + delta * progreso
                q_df_corte = q_ea_corte.copy()
                q_df_corte['Valor'] = np.log(1 + q_ea_corte['Valor'].to_numpy())
            else:
                r_df_corte = r_df.copy()
                q_df_corte = q_df.copy()

            # Curva_Tasa_Proyectada de SWTIBR/LUTIBR en % nominal simple base 360 al mes
            # del corte, para derivar el Punto_Forward_Dinamico por fila en el descuento
            # de forwards (Req 3.6, 3.7). Se reconstruyen desde las curvas EA ya
            # proyectadas (`r_ea_corte`/`q_ea_corte` cuando hay deltas; base `r_ea`/`q_ea`
            # en caso contrario), invirtiendo `tasa_ea` con `_ea_a_nominal_pct`.
            _r_ea_proy = r_ea_corte if r_ea_corte is not None else r_ea
            _q_ea_proy = q_ea_corte if q_ea_corte is not None else q_ea
            swtibr_proy = _r_ea_proy.copy()
            swtibr_proy['Valor'] = _ea_a_nominal_pct(
                _r_ea_proy['Valor'].to_numpy(), _r_ea_proy['Plazo_en_Dias'].to_numpy())
            lutibr_proy = _q_ea_proy.copy()
            lutibr_proy['Valor'] = _ea_a_nominal_pct(
                _q_ea_proy['Valor'].to_numpy(), _q_ea_proy['Plazo_en_Dias'].to_numpy())

            # =================================================================
            # SOLVER DE CANAL — fijar el strike Call de collares renovados (Req 4.3, 4.4)
            # =================================================================
            # Se resuelve sobre el plano (`opts_plan`) usando las curvas r/q ya
            # proyectadas al mes del corte, una sola vez por collar (memoización), en el
            # corte de su fecha de constitución. Las opciones existentes de hist_opciones
            # (no renovadas) nunca pasan por el solver.
            if metodo in (1, 3):
                # Shifts acumulados aplicados en este corte para la auditoría del solver
                # (Req 4.10). Se acumulan por Progreso_Mensual `m/N` (`progreso`), igual
                # que la senda lineal mensual que mueve las curvas r/q del corte.
                #
                # Shift_TES (SWTIBR / r COP): `delta_tes_cop` es {plazo: delta_ea} en EA
                # decimal (bps/10000); el shift paralelo acumulado en bps es
                # `delta_ea * 10000 * progreso`. Se toma el nodo representativo (mediana)
                # por robustez ante curvas no perfectamente paralelas.
                _shift_tes_corte = 0.0
                if _delta_cop and progreso > 0:
                    _delta_cop_bps = float(np.median(
                        [v * 10000.0 for v in _delta_cop.values()]))
                    _shift_tes_corte = _delta_cop_bps * progreso
                # Shift_FED (LUTIBR / q USD): ancla corta FED, ya expresada en bps.
                _shift_fed_corte = (_delta_fed * progreso) if progreso > 0 else 0.0
                _resolver_calls_renovados_en_corte(
                    opts_plan, fecha_cal, trm_df, interpol_vol,
                    r_df_corte, q_df_corte,
                    tolerancia_prima=tolerancia_prima,
                    max_iter_canal=max_iter_canal, canal_opc=canal_opc,
                    memo_calls=memo_calls_renovados,
                    registros_auditoria=registros_auditoria,
                    corrida_id=corrida_id, m=m,
                    shift_tes_bps=_shift_tes_corte, shift_fed_bps=_shift_fed_corte,
                )
                # Re-sincronizar los strikes resueltos en el `opts` de este corte
                # (derivado de `opts_plan`) por identificador de collar.
                if '_collar_id' in opts.columns and memo_calls_renovados:
                    _resueltos = opts['_collar_id'].map(
                        lambda cid: memo_calls_renovados[cid][0]
                        if (cid in memo_calls_renovados) else np.nan
                    )
                    _msk_call = (opts.get('call_put') == 'Call') & _resueltos.notna()
                    opts.loc[_msk_call, 'strike'] = _resueltos[_msk_call].astype(np.float64)

            # Descartar columnas de control internas antes de la valoración para
            # preservar el contrato de columnas del resultado (Req 4.11).
            opts = opts.drop(columns=['_renovado', '_collar_id', '_call_resuelto'],
                             errors='ignore')

            fwds_val = (_valorar_fwds_corte(fwds, trm_val, fwd_cop, r_fwd_corte, fecha_cal, serie,
                                             swtibr_proy=swtibr_proy, lutibr_proy=lutibr_proy,
                                             dev_forecast=dev_forecast)
                        if metodo != 5 else fwds.copy())
            opts_val = _valorar_opciones_corte(opts, trm_val, r_df_corte, q_df_corte, interpol_vol,
                                               fecha_cal, m, trm_df,
                                               fecha_corte_dt, nivel_actual,
                                               serie_trm=serie)

            # =================================================================
            # DELTA HEDGE (metodo 5)
            # =================================================================
            if metodo == 5 and m != year_left - 1:
                comp_map = {'02-OP': ' 02-DW  ', '03-OM': ' 03-AT  ',
                            '01-OP': ' 01-DW  ', '03-OP': ' 03-DW  '}
                pcall = opts_val.loc[opts_val['call_put'] == 'Call'].copy()
                delta_col = 'delta_inicial' if m == 0 else 'delta'
                filas_dh  = []
                for _, row in pcall.iterrows():
                    dd = -(row[delta_col] if m == 0 else (row['delta'] - row['delta_inicial']))
                    nom_dh = row['nominal'] * dd
                    filas_dh.append({
                        'vencimiento': row['vencimiento'],
                        'portafolio':  comp_map.get(row['portafolio'], row['portafolio']),
                        'constitucion': fecha_cal,
                        'nominal':     abs(nom_dh),
                        'sentido':     'Venta' if nom_dh < 0 else 'Compra',
                    })
                if filas_dh:
                    ph = pd.DataFrame(filas_dh)
                    ph['dias'] = (ph['vencimiento'] - fecha_cal).dt.days
                    ph = ph.loc[ph['dias'] > 0].reset_index(drop=True)
                    ph = _merge_curva(ph, fwd_cop, 'dias', 'fwdcop')
                    ph = _merge_curva(ph, r_fwd,   'dias', 'r_fwd')
                    ph['tasa_negociada'] = trm_val * ((1 + dev_forecast) ** (ph['dias'] / 365))
                    for op in ['Compra', 'Venta']:
                        msk = ph['sentido'] == op
                        if msk.any():
                            result = valorar_fwds(
                                trm_val, ph.loc[msk, 'dias'], ph.loc[msk, 'nominal'],
                                ph.loc[msk, 'tasa_negociada'], ph.loc[msk, 'r_fwd'],
                                ph.loc[msk, 'fwdcop'], op
                            )
                            ph.loc[msk, 'valoracion'] = np.asarray(result[0]).flatten()
                    ph['delta']       = 1.0
                    ph['delta_monto'] = np.where(ph['sentido'] == 'Compra',
                                                  ph['nominal'], -ph['nominal'])
                    fwds_val = ph
                else:
                    fwds_val = pd.DataFrame(columns=['valoracion', 'nominal', 'sentido',
                                                      'delta', 'delta_monto', 'portafolio'])

            elif metodo == 5 and m == year_left - 1:
                fwds_val = pd.DataFrame(columns=['valoracion', 'nominal', 'sentido',
                                                  'delta', 'delta_monto', 'portafolio'])

            # =================================================================
            # ACTIVOS USD
            # =================================================================
            nivel_corte = float(serie.asof(fecha_cal))
            if m == 0:
                act_val = pd.DataFrame({
                    'nominal':    [activos_usd],
                    'valoracion': [activos_usd * nivel_actual],
                    'strike':     [nivel_actual],
                })
            else:
                act_val = pd.DataFrame({
                    'nominal':    [port_usd_sig],
                    'valoracion': [port_usd_sig * nivel_corte],
                    'strike':     [nivel_corte],
                })
            act_val['portafolio']  = 'Total'
            act_val['tipo']        = 'Activos'
            act_val['delta_monto'] = act_val['nominal']

            for df_r in [opts_val, fwds_val, act_val]:
                df_r['fecha_valoracion'] = fecha_cal

            opc_cortes.append(opts_val)
            fwd_cortes.append(fwds_val)
            act_cortes.append(act_val)

            # Valorar swaps en este corte
            if not swaps_base.empty and curvas_swaps:
                # UVR proyectado: por ahora usar UVR fijo (TODO: derivar de inflación)
                uvr_val = 411.0752  # TODO: calcular desde senda de inflación
                # Proyectar curvas según progreso en el horizonte
                n_cortes = len(diffs)
                idx_corte = diffs.index(m)
                progreso = idx_corte / max(n_cortes - 1, 1)
                curvas_corte = proyectar_curvas_swaps(
                    curvas_swaps,
                    delta_tes_cop=metodo_params.get('delta_tes_cop') if metodo_params else None,
                    delta_tes_uvr=metodo_params.get('delta_tes_uvr') if metodo_params else None,
                    delta_fed_bps=metodo_params.get('delta_fed_bps', 0) if metodo_params else 0,
                    delta_10t_bps=metodo_params.get('delta_10t_bps', 0) if metodo_params else 0,
                    progreso=progreso,
                )
                swaps_val = _valorar_swaps_corte(swaps_base, curvas_corte, trm_val, uvr_val, fecha_cal)
                swaps_val['fecha_valoracion'] = fecha_cal
                swap_cortes.append(swaps_val)

    print("  ✅ Valoración completada.")

    # =================================================================
    # PERSISTENCIA DE AUDITORÍA DE LA CORRIDA (Req 5.3)
    # =================================================================
    # Se escriben los Registro_Auditoria acumulados en `audit_derivados_corrida`
    # dentro de una transacción atómica. Un fallo de auditoría NO invalida el
    # resultado en memoria: `persistir_auditoria_derivados` captura cualquier
    # excepción y retorna False sin re-lanzar.
    if persistir_auditoria and registros_auditoria:
        _corrida_id = corrida_id or uuid.uuid4().hex
        _conn_audit = sqlite3.connect(DB_PATH)
        try:
            persistir_auditoria_derivados(_conn_audit, _corrida_id, registros_auditoria)
        finally:
            _conn_audit.close()

    resultado = {
        'opciones': pd.concat(opc_cortes, ignore_index=True),
        'forwards': pd.concat(fwd_cortes, ignore_index=True),
        'activos':  pd.concat(act_cortes, ignore_index=True),
        'swaps':    pd.concat(swap_cortes, ignore_index=True) if swap_cortes else pd.DataFrame(),
    }

    # Plano de posiciones VIVAS al fecha_fin (encadenamiento por tramos, Opción B):
    # se toman de los planos ya proyectados/renovados (fwds_plan/opts_plan incluyen las
    # renovaciones que el motor generó) las posiciones cuyo vencimiento es posterior al
    # fin del horizonte de este tramo, que serán el plano de INICIO del tramo siguiente.
    if devolver_plano_cierre:
        def _vivas(df, col_venc='vencimiento'):
            if df is None or len(df) == 0 or col_venc not in df.columns:
                return df.copy() if df is not None else pd.DataFrame()
            _v = pd.to_datetime(df[col_venc], errors='coerce')
            return df.loc[_v > fecha_fin_dt].copy().reset_index(drop=True)

        resultado['plano_cierre'] = {
            'fwds':  _vivas(fwds_plan),
            'opts':  _vivas(opts_plan),
            'swaps': _vivas(swaps_base, 'fecha_vencimiento'),
        }

    return resultado


# =============================================================================
# 5. EXPORTACIÓN
# =============================================================================

def exportar_resultados(resultados: dict, metodo: int,
                         metodo_params: dict = None,
                         carpeta_salida: str = '.') -> None:
    """Exporta resultados a Excel y genera el consolidado de P&G."""
    if metodo_params is None:
        metodo_params = {}
    salida = Path(carpeta_salida)

    if metodo == 3:
        exp   = int(metodo_params.get('exposicion_obj', 0.3) * 100)
        trato = metodo_params.get('trato_exposicion', 1)
        mom   = metodo_params.get('momento_exposicion', 1)
        opc_l = float(metodo_params.get('opciones_sobre_largo', 0.7)) * 100
        tag   = f"{trato}_{mom}_{exp}pct_collars{opc_l:.0f}pct"
    else:
        tag = METODOS[metodo]

    out_name  = salida / f"Valoracion_derivados_{tag}.xlsx"
    cons_name = salida / f"Consolidado_me_{tag}.xlsx"

    with pd.ExcelWriter(out_name) as writer:
        resultados['opciones'].to_excel(writer, sheet_name='opciones', index=False)
        resultados['forwards'].to_excel(writer, sheet_name='forwards', index=False)
        resultados['activos'].to_excel(writer,  sheet_name='activos',  index=False)
    print(f"✅ {out_name}")

    if metodo != 5:
        opc = resultados['opciones'].copy()
        fwd = resultados['forwards'].copy()
        act = resultados['activos'].copy()
        opc['tipo'] = 'opc'
        fwd['tipo'] = 'fwd'
        opc['portafolio'] = opc['portafolio'].replace(PORT_OPC)
        if 'portafolio' in fwd.columns:
            fwd['portafolio'] = fwd['portafolio'].replace(PORT_FWD)

        cols = ['portafolio', 'valoracion', 'fecha_valoracion',
                'tipo', 'nominal', 'delta_monto']
        cons = pd.concat([
            fwd[[c for c in cols if c in fwd.columns]],
            opc[[c for c in cols if c in opc.columns]],
            act[['portafolio', 'valoracion', 'fecha_valoracion',
                  'tipo', 'nominal', 'delta_monto']],
        ], ignore_index=True)

        pyg = (cons.groupby(['fecha_valoracion', 'tipo'])['valoracion']
                    .sum().reset_index(name='valoracion'))
        pyg['valor_base'] = pyg.groupby('tipo')['valoracion'].transform('first')
        pyg['p_y_g']      = pyg['valoracion'] - pyg['valor_base']

        with pd.ExcelWriter(cons_name) as writer:
            pyg[['fecha_valoracion', 'tipo', 'p_y_g']].to_excel(
                writer, sheet_name='consolidado', index=False
            )
        print(f"✅ {cons_name}")


def calcular_pyg(resultados: dict) -> pd.DataFrame:
    """
    Calcula P&G mensual a partir de los resultados de valoración.
    Retorna DataFrame con columnas: fecha_valoracion, tipo (Opciones/Forwards/Activos/Swaps), p_y_g
    Útil para mostrar directamente en el dashboard.
    """
    opc = resultados['opciones'].copy()
    fwd = resultados['forwards'].copy()
    act = resultados['activos'].copy()
    opc['tipo'] = 'Opciones'
    fwd['tipo'] = 'Forwards'
    act['tipo'] = 'Activos'

    cols = ['valoracion', 'fecha_valoracion', 'tipo']

    def _parte(df):
        """
        Selecciona las columnas homogéneas (`valoracion`, `fecha_valoracion`,
        `tipo`) y añade `cup_neto = 0.0` para los tipos SIN cupón realizado
        (opciones/forwards/activos). Mantener la columna en todas las partes hace
        homogéneo el concat y deja su P&G invariante (0 + 0 = sin cambio).
        """
        parte = df[[c for c in cols if c in df.columns]].copy()
        parte['cup_neto'] = 0.0
        return parte

    partes = [_parte(fwd), _parte(opc), _parte(act)]

    # Incluir swaps si existen. Para swaps el cupón neto realizado SÍ forma parte
    # del P&G: al pagarse/recibirse un cupón sale del precio sucio y el VPN cae,
    # pero esa caja realizada debe contabilizarse (análogo a los vencimientos de
    # forwards/opciones, que ya se reflejan en su valoración). El DataFrame de
    # swaps trae los cupones ACUMULADOS en COP por pata; el cupón neto por fila es
    # la diferencia der - obl.
    if 'swaps' in resultados and not resultados['swaps'].empty:
        swp = resultados['swaps'].copy()
        swp['tipo'] = 'Swaps'
        parte_swp = swp[[c for c in cols if c in swp.columns]].copy()
        # Retrocompatibilidad: si una corrida vieja NO trae columnas de cupones,
        # cup_neto = 0 ⇒ el P&G de Swaps queda idéntico al comportamiento previo.
        if ('cupones_der_acum_cop' in swp.columns
                and 'cupones_obl_acum_cop' in swp.columns):
            parte_swp['cup_neto'] = (
                swp['cupones_der_acum_cop'] - swp['cupones_obl_acum_cop']
            )
        else:
            parte_swp['cup_neto'] = 0.0
        partes.append(parte_swp)

    cons = pd.concat(partes, ignore_index=True)

    pyg = (cons.groupby(['fecha_valoracion', 'tipo'])
                .agg(valoracion=('valoracion', 'sum'),
                     cup_neto_acum=('cup_neto', 'sum'))
                .reset_index())
    pyg['valor_base']    = pyg.groupby('tipo')['valoracion'].transform('first')
    pyg['cup_neto_base'] = pyg.groupby('tipo')['cup_neto_acum'].transform('first')
    # P&G = variación de VPN + cupón neto realizado acumulado desde la base. Para
    # tipos sin cupón el segundo término es 0 ⇒ su P&G NO cambia (invariancia).
    pyg['p_y_g'] = (
        (pyg['valoracion'] - pyg['valor_base'])
        + (pyg['cup_neto_acum'] - pyg['cup_neto_base'])
    )
    return pyg[['fecha_valoracion', 'tipo', 'p_y_g']]


def calcular_pyg_swaps_contable(resultados: dict, senda_trm: list,
                                  senda_uvr: list, fecha_corte_dt) -> pd.DataFrame:
    """
    Calcula P&G de swaps con doble tratamiento contable. En ambos casos el P&G incluye
    el CUPÓN NETO REALIZADO en la ventana (cupón recibido pata derecho − cupón pagado
    pata obligación), que antes se perdía: al pagarse un cupón sale del precio sucio y el
    VPN cae, pero esa caja realizada no se contabilizaba (equivalente a ignorar los
    vencimientos de forwards/opciones).
    - Negociable: P&G = variación VPN total + cupón neto realizado, ORI = 0
    - Disp. Venta: P&G = efecto cambiario neto + cupón neto realizado,
      ORI = (variación VPN − efecto cambiario − cupón realizado) por pata

    Args:
        resultados: dict con key 'swaps' (DataFrame con vpn_der_calc, vpn_obl_calc por corte)
        senda_trm: lista de TRM por corte [trm_0, trm_1, ..., trm_N]
        senda_uvr: lista de UVR por corte [uvr_0, uvr_1, ..., uvr_N]
        fecha_corte_dt: datetime del corte inicial

    Returns:
        DataFrame con columnas:
        id_posicion, fecha_valoracion, nominal_der, nominal_obl, moneda_der, moneda_obl,
        vpn_der_cop, vpn_obl_cop, valoracion,
        # Negociable
        pyg_negociable, ori_negociable, total_negociable,
        # Disp. Venta
        efecto_cam_der, efecto_cam_obl, pyg_dv, ori_der, ori_obl, ori_dv, total_dv
    """
    df_swaps = resultados.get('swaps', pd.DataFrame())
    if df_swaps.empty:
        return pd.DataFrame()

    # Ordenar por swap y fecha
    df = df_swaps.sort_values(['id_posicion', 'fecha_valoracion']).copy()

    # Mapear TRM y UVR a cada corte
    fechas_unicas = sorted(df['fecha_valoracion'].unique())
    # Construir mapeo fecha → TRM y UVR
    trm_map = {}
    uvr_map = {}
    for i, f in enumerate(fechas_unicas):
        trm_map[f] = senda_trm[i] if i < len(senda_trm) else senda_trm[-1]
        uvr_map[f] = senda_uvr[i] if i < len(senda_uvr) else senda_uvr[-1]

    df['trm_corte'] = df['fecha_valoracion'].map(trm_map)
    df['uvr_corte'] = df['fecha_valoracion'].map(uvr_map)

    # Calcular variaciones mes a mes por swap
    resultados_list = []

    for id_pos, grupo in df.groupby('id_posicion'):
        grupo = grupo.sort_values('fecha_valoracion').reset_index(drop=True)

        for i in range(len(grupo)):
            row = grupo.iloc[i]
            vpn_der_cop = row.get('vpn_der_calc', 0) or 0
            vpn_obl_cop = row.get('vpn_obl_calc', 0) or 0
            valoracion = row.get('valoracion', 0) or 0
            nominal_der = row.get('nominal_obl', 0) or 0  # nominal en moneda original
            nominal_obl = row.get('nominal_obl', 0) or 0

            # Obtener nominales originales del swap (de la primera fila)
            nom_der_orig = grupo.iloc[0].get('nominal_obl', 0)  # placeholder
            nom_obl_orig = grupo.iloc[0].get('nominal_obl', 0)

            trm_t = row['trm_corte']
            uvr_t = row['uvr_corte']

            # Cupones acumulados por pata hasta este corte (COP).
            cup_der_acum = row.get('cupones_der_acum_cop', 0) or 0
            cup_obl_acum = row.get('cupones_obl_acum_cop', 0) or 0

            if i == 0:
                # Primer corte: base, todo en 0
                efecto_cam_der = 0.0
                efecto_cam_obl = 0.0
                var_vpn_der = 0.0
                var_vpn_obl = 0.0
                # Cupón realizado en la ventana (no hay corte previo → 0).
                cupon_der_realizado = 0.0
                cupon_obl_realizado = 0.0
            else:
                prev = grupo.iloc[i - 1]
                trm_prev = prev['trm_corte']
                uvr_prev = prev['uvr_corte']
                vpn_der_prev = prev.get('vpn_der_calc', 0) or 0
                vpn_obl_prev = prev.get('vpn_obl_calc', 0) or 0

                # Cupón REALIZADO entre (t-1, t] = variación del acumulado por pata.
                cup_der_acum_prev = prev.get('cupones_der_acum_cop', 0) or 0
                cup_obl_acum_prev = prev.get('cupones_obl_acum_cop', 0) or 0
                cupon_der_realizado = cup_der_acum - cup_der_acum_prev
                cupon_obl_realizado = cup_obl_acum - cup_obl_acum_prev

                # Nominales en moneda original
                nom_der = row.get('nominal_der', 0) or 0
                nom_obl = row.get('nominal_obl', 0) or 0
                moneda_der = row.get('moneda_der', 'UVR')
                moneda_obl = row.get('moneda_obl', 'USD')

                # Efecto cambiario según moneda
                if moneda_der == 'UVR':
                    efecto_cam_der = nom_der * (uvr_t - uvr_prev)
                elif moneda_der == 'USD':
                    efecto_cam_der = nom_der * (trm_t - trm_prev)
                else:  # COP
                    efecto_cam_der = 0.0

                if moneda_obl == 'USD':
                    efecto_cam_obl = nom_obl * (trm_t - trm_prev)
                elif moneda_obl == 'UVR':
                    efecto_cam_obl = nom_obl * (uvr_t - uvr_prev)
                else:  # COP
                    efecto_cam_obl = 0.0

                # Variación VPN en COP
                var_vpn_der = vpn_der_cop - vpn_der_prev
                var_vpn_obl = vpn_obl_cop - vpn_obl_prev

            # Cupón NETO realizado en la ventana (recibido pata derecho − pagado pata
            # obligación). Es caja efectiva que debe sumarse al P&G, análogo a los
            # vencimientos en forwards/opciones: la caída del VPN por el cupón que sale
            # del precio sucio se compensa con este flujo realizado.
            cupon_neto_realizado = cupon_der_realizado - cupon_obl_realizado

            # --- NEGOCIABLE: todo al P&G ---
            if i == 0:
                var_valoracion = 0.0
            else:
                var_valoracion = valoracion - (grupo.iloc[i - 1].get('valoracion', 0) or 0)
            # P&G = variación de VPN + cupón neto realizado.
            pyg_neg = var_valoracion + cupon_neto_realizado
            ori_neg = 0.0
            total_neg = pyg_neg

            # --- DISP. VENTA ---
            # El cupón realizado es resultado efectivo → va al P&G (no al ORI).
            pyg_dv = (efecto_cam_der - efecto_cam_obl) + cupon_neto_realizado
            # La variación de VPN de cada pata incluye la caída por el cupón que salió
            # del precio sucio; se descuenta ese cupón realizado del ORI para no
            # duplicarlo (ya se reconoció como P&G efectivo arriba).
            ori_der = var_vpn_der - efecto_cam_der + cupon_der_realizado
            ori_obl = var_vpn_obl - efecto_cam_obl + cupon_obl_realizado
            ori_dv = ori_der - ori_obl  # ORI neto
            total_dv = pyg_dv + ori_dv  # Total Resultado

            resultados_list.append({
                'id_posicion': id_pos,
                'fecha_valoracion': row['fecha_valoracion'],
                'vpn_der_cop': vpn_der_cop,
                'vpn_obl_cop': vpn_obl_cop,
                'valoracion': valoracion,
                'trm_corte': trm_t,
                'uvr_corte': uvr_t,
                # Cupones realizados en la ventana (COP)
                'cupon_der_realizado': cupon_der_realizado,
                'cupon_obl_realizado': cupon_obl_realizado,
                'cupon_neto_realizado': cupon_neto_realizado,
                # Negociable
                'pyg_negociable': pyg_neg,
                'ori_negociable': ori_neg,
                'total_negociable': total_neg,
                # Disp. Venta
                'efecto_cam_der': efecto_cam_der,
                'efecto_cam_obl': efecto_cam_obl,
                'pyg_dv': pyg_dv,
                'ori_der': ori_der,
                'ori_obl': ori_obl,
                'ori_dv': ori_dv,
                'total_dv': total_dv,
            })

    return pd.DataFrame(resultados_list)


# =============================================================================
# EJECUCIÓN DIRECTA
# =============================================================================
if __name__ == "__main__":
    # Extraer TRM actual de hist_opciones
    conn = sqlite3.connect(DB_PATH)
    _trm_df = pd.read_sql_query("SELECT trm FROM hist_opciones LIMIT 1", conn)
    _trm_actual = float(str(_trm_df['trm'].iloc[0]).replace(',', ''))
    conn.close()
    print(f"[AXIS] TRM actual extraída de hist_opciones: {_trm_actual}")

    resultados = valorar_portafolio_derivados(
        fecha_corte='2026-05-20',
        fecha_fin='2027-05-20',
        metodo=1,
        nivel_actual=_trm_actual,
        activos_usd=400_747_349,
        portafolio_pesos=3_557_487_293 * _trm_actual,
        senda_trm=None,  # None = senda plana al nivel_actual
    )
    exportar_resultados(resultados, metodo=1)
