"""
AXIS — Motor de Proyección de Portafolio (Rediseño v2)
=======================================================
Simula la evolución del portafolio de renta fija mes a mes,
RE-VALORANDO cada título con las tasas de la senda en cada período.

Usa las funciones de valoración de valorar_bonos.py para obtener
precios exactos según la fórmula BVC/Deceval.

Inputs en producción:
  1. portafolio_clasificado (SQLite) → títulos con asset_class_definitivo
  2. Archivo PIP (CSV) → curvas base

Funcionalidades:
  - Loop mensual de simulación con RE-VALORACIÓN real
  - Detección de cupones por período
  - Reinversión automática (clonación de títulos)
  - Cálculo de P&L por período (mercado y TIR)
  - Cálculo de encaje por ramo proyectado

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

import copy
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from curvas_rendimiento import (
    Curva,
    generar_senda_lineal,
    interpolar_tasa,
    obtener_nombre_curva,
    obtener_tasa_senda,
)

from valorar_bonos import (
    valorar_bono_tf,
    valorar_bono_uvr,
    valorar_bono_ipc,
    valorar_bono_corporativo,
    valorar_bono_yankee,
    cargar_serie_ipc,
)


# ==============================================================================
# DATACLASSES: Estructuras de datos del motor
# ==============================================================================

@dataclass
class Titulo:
    """Representa un título de renta fija en el portafolio."""
    id_posicion: str
    especie: str
    emisor: str
    isin: str
    moneda: str
    fecha_emision: date
    fecha_vencimiento: date
    nominal: float
    tasa_cupon: float          # Tasa facial (%)
    spread_ipc: float          # Spread IPC (%) — 0 si no es IPC
    tipo_bono: str             # TF, UVR, IPC, YANKEE
    frecuencia: str            # anual, semestral, trimestral, mensual
    tipo_tasa: str             # NOMINAL, EFECTIVO
    base_anual: float          # 360 o 365
    uso_tasa: str              # I, F
    clasificacion: str         # 1CP, 1DV, 1VC
    compania: str
    ramo: str
    portafolio: str
    asset_class: str           # asset_class_definitivo
    tasa_compra: float         # T.Compra (%)
    tasa_mercado: float        # T.Mer (%)
    valor_mercado: float       # VM actual en COP
    valor_tir: float           # Valor TIR actual en COP
    es_reinversion: bool = False
    fecha_reinversion: date | None = None
    titulo_origen: str = ""
    vm_compra: float = 0.0     # VM al momento de compra/reinversión
    spread_mercado: float = 0.0  # Spread de mercado deflactado (%) para IPC
    id_cobertura: str = ""     # id del swap que cubre este título (subyacente cubierto); '' si no aplica


@dataclass
class Cupon:
    """Representa un flujo de cupón detectado en un período."""
    titulo: Titulo
    fecha_pago: date
    monto_cupon: float
    monto_capital: float
    monto_total: float


@dataclass
class ConfigReinversion:
    """
    Configuración de reinversión para un cupón.

    El usuario puede definir POR ESPECIE ORIGEN:
      - especie_destino: en qué especie se reinvierte el nominal
      - clasificacion: clasificación contable del nuevo título (1CP, 1DV, 1VC)
      - ramo: ramo al que se asigna
      - portafolio: portafolio destino
      - compania: compañía destino

    Estas variables son las que se sensibilizan en el dashboard
    para definir la política de reinversión.
    """
    especie_destino: str       # Especie donde se reinvierte
    clasificacion: str         # 1CP, 1DV, 1VC
    ramo: str                  # Ramo destino
    portafolio: str            # Portafolio destino
    compania: str              # Compañía destino


@dataclass
class ResultadoMes:
    """Resultado de la simulación para un mes específico."""
    mes: int
    fecha: date
    vm_total: float
    vtir_total: float
    pnl_mercado_mes: float
    pnl_tir_mes: float
    pnl_mercado_acum: float
    pnl_tir_acum: float
    n_titulos: int
    n_reinversiones_mes: int
    cupones_cobrados_mes: float
    reinversiones: list[Titulo] = field(default_factory=list)


# ==============================================================================
# FUNCIÓN 1: CARGAR PORTAFOLIO DESDE DB (portafolio_clasificado)
# ==============================================================================

def cargar_portafolio_proyeccion(
    ruta_db: str = "portafolio.db",
    fecha_pip: str | None = None,
) -> list[Titulo]:
    """
    Carga el portafolio de renta fija desde portafolio_clasificado (fuente única)
    y trae fecha_emision de hist_titulos.

    portafolio_clasificado tiene: especie, nominal, valor_mercado, asset_class_definitivo,
    compania, ramo, tasa_referencia, tasa_facial, periodicidad, etc.
    hist_titulos aporta: fecha_emision real.

    Args:
        ruta_db: Ruta a la base de datos SQLite.
        fecha_pip: Fecha de corte 'YYYY-MM-DD' (la del sidebar del dashboard). Filtra
            hist_titulos, hist_pip y hist_pip_int por esa fecha exacta para que la
            valoración use datos coherentes con la fecha de análisis. Si es None, se
            usa MAX(fecha_reporte) de cada tabla (comportamiento por defecto para el
            orquestador y ejecución directa del motor).

    Returns:
        Lista de objetos Titulo.
    """
    conn = sqlite3.connect(ruta_db)

    # Selector de fecha: exacta (sidebar) o la más reciente disponible en cada tabla.
    if fecha_pip:
        sel_titulos = "?"
        sel_pip = "?"
        params = [fecha_pip, fecha_pip]
    else:
        sel_titulos = "(SELECT MAX(fecha_reporte) FROM hist_titulos)"
        sel_pip = "(SELECT MAX(fecha_reporte) FROM hist_pip)"
        params = []

    # Fuente principal: portafolio_clasificado + fecha_emision de hist_titulos + rendimiento de hist_pip
    df = pd.read_sql(f"""
        SELECT pc.id_posicion, pc.especie, pc.emisor, pc.isin, pc.moneda,
               h.fecha_emision, h.valor_tir, pc.fecha_vencimiento, pc.nominal, pc.valor_mercado,
               pc.tasa_compra, pc.tir, pc.estado, pc.portafolio,
               pc.asset_class_definitivo, pc.tasa_referencia,
               pc.tasa_facial, pc.tipo_tasa, pc.uso_tasa, pc.periodicidad,
               pc.base_anual, pc.compania, pc.ramo, pc.clasificacion_contable,
               pc.id_cobertura,
               pip.rendimiento AS rend_pip
        FROM portafolio_clasificado pc
        LEFT JOIN (
            SELECT id_posicion, fecha_emision, valor_tir
            FROM hist_titulos
            WHERE fecha_reporte = {sel_titulos}
        ) h ON pc.id_posicion = h.id_posicion
        LEFT JOIN (
            SELECT codigo_isin, rendimiento
            FROM hist_pip
            WHERE fecha_reporte = {sel_pip}
              AND rendimiento > 0
        ) pip ON pc.isin = pip.codigo_isin
        WHERE pc.clase_origen = 'TITULO'
          AND pc.asset_class_definitivo NOT IN (
              'VISTA', 'COLCAP', 'MXWD', 'PEI', 'COCAP',
              'Fondo Capital Priv. Local', 'Fondo Capital Priv. Extranjero',
              'Accion Local', 'Accion Internacional',
              'ETF Acciones Local', 'ETF Acciones Internacional', 'ETF Commodities'
          )
          AND pc.asset_class_definitivo NOT LIKE '%%Fondo Capital%%'
          AND pc.asset_class_definitivo NOT LIKE '%%Accion%%'
          AND pc.asset_class_definitivo NOT LIKE '%%ETF%%'
          AND pc.nominal > 0
    """, conn, params=params)
    conn.close()

    titulos = []
    for _, row in df.iterrows():
        # Tipo de bono desde asset_class_definitivo
        tasa_ref = str(row.get("tasa_referencia", "")).strip()
        ac = str(row.get("asset_class_definitivo", "")).strip()

        if "UVR" in ac or str(row.get("moneda", "")) == "UVR":
            tipo_bono = "UVR"
        elif tasa_ref == "IPC" or "IPC" in ac:
            tipo_bono = "IPC"
        elif str(row.get("moneda", "")) == "USD" or ac in ("LEGATRUU", "Estructuras"):
            tipo_bono = "YANKEE"
        else:
            tipo_bono = "TF"

        # Frecuencia
        per = str(row.get("periodicidad", "")).strip().upper()
        freq_map = {"TRIMESTRAL": "trimestral", "SEMESTRAL": "semestral",
                    "ANUAL": "anual", "MENSUAL": "mensual", "AV": "anual"}
        frecuencia = freq_map.get(per, "anual")

        # Fechas
        try:
            fv = pd.Timestamp(row["fecha_vencimiento"]).date()
            if pd.isna(row["fecha_vencimiento"]):
                continue
        except Exception:
            continue

        try:
            fe = pd.Timestamp(row["fecha_emision"]).date()
            if pd.isna(row["fecha_emision"]):
                fe = fv - relativedelta(years=50)
        except Exception:
            fe = fv - relativedelta(years=50)

        # Clasificación
        clasif_contable = str(row.get("clasificacion_contable", "")).strip()
        if 'Disp' in clasif_contable or 'Venta' in clasif_contable:
            clasificacion = "Disp. Venta"
        elif 'Venc' in clasif_contable:
            clasificacion = "Vencimiento"
        else:
            clasificacion = "Negociable"

        # Tasa facial
        try:
            facial_raw = str(row.get("tasa_facial", "0"))
            facial = float(pd.to_numeric(facial_raw.split('-')[0].split('N')[0].strip(), errors='coerce') or 0)
        except Exception:
            facial = 0.0

        t = Titulo(
            id_posicion=str(row["id_posicion"]),
            especie=str(row["especie"]).strip(),
            emisor=str(row.get("emisor", "")).strip(),
            isin=str(row.get("isin", "")).strip(),
            moneda=str(row.get("moneda", "COP")).strip(),
            fecha_emision=fe,
            fecha_vencimiento=fv,
            nominal=float(row["nominal"]),
            tasa_cupon=facial,
            spread_ipc=facial if tipo_bono == "IPC" else 0,
            tipo_bono=tipo_bono,
            frecuencia=frecuencia,
            tipo_tasa=str(row.get("tipo_tasa", "EFECTIVO")).strip(),
            base_anual=float(row["base_anual"]) if pd.notna(row.get("base_anual")) else 365,
            uso_tasa=str(row.get("uso_tasa", "ACTUAL")).strip(),
            clasificacion=clasificacion,
            compania=str(row.get("compania", "")).strip(),
            ramo=str(row.get("ramo", "")).strip(),
            portafolio=str(row.get("portafolio", "")).strip(),
            asset_class=ac,
            tasa_compra=float(row["tasa_compra"]) if pd.notna(row.get("tasa_compra")) else 0,
            tasa_mercado=float(row["tir"]) if pd.notna(row.get("tir")) else 0,
            valor_mercado=float(row["valor_mercado"]) if pd.notna(row.get("valor_mercado")) else 0,
            valor_tir=float(row["valor_tir"]) if pd.notna(row.get("valor_tir")) else float(row["valor_mercado"]) if pd.notna(row.get("valor_mercado")) else 0,
            id_cobertura=("" if pd.isna(row.get("id_cobertura"))
                          or str(row.get("id_cobertura")).strip().lower() in ("", "nan", "none")
                          else str(row.get("id_cobertura")).strip()),
        )

        # Para TODOS los títulos: guardar rendimiento PIP individual
        # Para IPC se deflactará en el motor; para TF/UVR/Yankees se usa directo + shift
        if pd.notna(row.get("rend_pip")) and float(row["rend_pip"]) > 0:
            t.spread_mercado = float(row["rend_pip"])  # Rendimiento PIP bruto (%)

        titulos.append(t)

    # ── Enriquecer con PIP Internacional TODOS los títulos con ISIN sin spread ──
    # (no solo Yankees: también bonos globales clasificados como TF, etc.)
    sin_spread = [t for t in titulos if t.spread_mercado == 0 and t.isin]
    if sin_spread:
        try:
            conn_int = sqlite3.connect(ruta_db)
            if fecha_pip:
                sel_int = "?"
                params_int = [fecha_pip]
            else:
                sel_int = "(SELECT MAX(fecha_reporte) FROM hist_pip_int)"
                params_int = []
            df_pip_int = pd.read_sql(f"""
                SELECT codigo_isin, rendimiento
                FROM hist_pip_int
                WHERE fecha_reporte = {sel_int}
                  AND rendimiento > 0
            """, conn_int, params=params_int)
            conn_int.close()
            if not df_pip_int.empty:
                df_pip_int['codigo_isin'] = df_pip_int['codigo_isin'].str.strip()
                pip_int_map = dict(zip(df_pip_int['codigo_isin'], df_pip_int['rendimiento']))
                n_enriquecidos = 0
                for t in sin_spread:
                    if t.isin in pip_int_map:
                        t.spread_mercado = pip_int_map[t.isin]
                        n_enriquecidos += 1
                if n_enriquecidos > 0:
                    print(f"  PIP INT: {n_enriquecidos} títulos internacionales enriquecidos con rendimiento")
        except Exception as e:
            print(f"  ⚠️ PIP INT no disponible: {e}")

    return titulos


# ==============================================================================
# FUNCIÓN 2: VALORAR UN TÍTULO CON UNA TASA DE MERCADO DADA
# ==============================================================================

def valorar_titulo(
    titulo: Titulo,
    tasa_mercado_nueva: float,
    fecha_valuacion: date,
    valor_uvr: float = 411.2813,
    trm: float = 3667.06,
    serie_ipc: pd.DataFrame | None = None,
    senda_inflacion: list | None = None,
    fecha_inicio_senda: date | None = None,
) -> float:
    """
    Re-valora un título individual usando las fórmulas BVC de valorar_bonos.py.

    Retorna el VALOR DE MERCADO en COP (no el precio porcentual).

    Args:
        titulo: Objeto Titulo a valorar.
        tasa_mercado_nueva: Nueva tasa de descuento (%) de la senda.
        fecha_valuacion: Fecha de valoración del período.
        valor_uvr: Valor UVR a la fecha.
        trm: TRM a la fecha.
        serie_ipc: Serie de inflación (requerida para bonos IPC).
        senda_inflacion: Lista de inflaciones mensuales proyectadas (% EA).
        fecha_inicio_senda: Fecha de inicio de la senda de inflación.

    Returns:
        Valor de mercado en COP.
    """
    if titulo.fecha_vencimiento <= fecha_valuacion:
        return 0.0  # Título vencido

    nominal = titulo.nominal
    if nominal <= 0:
        return 0.0

    # ── Excepciones de valoración: nominal × TRM ──
    # 1. Títulos con "Enviado" o "Recibido" en la especie (posiciones de colateral)
    _esp_lower = titulo.especie.lower()
    if 'enviado' in _esp_lower or 'recibido' in _esp_lower:
        return nominal * trm
    # 4. JP MORGAN USD (depósitos, no son bonos)
    if 'JP MORGAN USD' in titulo.especie:
        return nominal * trm

    try:
        if titulo.tipo_bono == "TF":
            # Deuda pública TF o corporativo TF
            curva = obtener_nombre_curva(titulo.asset_class)
            if curva in ("DP_TF",):
                # Fórmula BVC NL/365
                res = valorar_bono_tf(
                    tasa_cupon=titulo.tasa_cupon,
                    yield_mercado=tasa_mercado_nueva,
                    fecha_emision=titulo.fecha_emision,
                    fecha_vencimiento=titulo.fecha_vencimiento,
                    valor_nominal=100.0,
                    frecuencia=titulo.frecuencia,
                    fecha_valuacion=fecha_valuacion,
                )
            else:
                # Corporativo 30/360
                res = valorar_bono_corporativo(
                    tasa_nominal=titulo.tasa_cupon,
                    yield_mercado=tasa_mercado_nueva,
                    fecha_emision=titulo.fecha_emision,
                    fecha_vencimiento=titulo.fecha_vencimiento,
                    valor_nominal=100.0,
                    frecuencia=titulo.frecuencia,
                    fecha_valuacion=fecha_valuacion,
                    tipo_tasa=titulo.tipo_tasa,
                )
            precio_pct = res["precio_sucio"]
            return nominal * precio_pct / 100.0

        elif titulo.tipo_bono == "UVR":
            res = valorar_bono_uvr(
                tasa_cupon_real=titulo.tasa_cupon,
                yield_real=tasa_mercado_nueva,
                fecha_emision=titulo.fecha_emision,
                fecha_vencimiento=titulo.fecha_vencimiento,
                valor_uvr=valor_uvr,
                valor_nominal_uvr=100.0,
                frecuencia=titulo.frecuencia,
                fecha_valuacion=fecha_valuacion,
            )
            precio_cop_por_uvr = res["precio_sucio_cop"]
            # nominal está en unidades de UVR
            return nominal * precio_cop_por_uvr / 100.0

        elif titulo.tipo_bono == "IPC":
            # Corporativos IPC (SF IPC, SR IPC): se valoran con spread + IPC.
            # La tasa_mercado_nueva aquí es la yield de descuento compuesta
            # que sale de la curva CORP_IPC (proxy de CORP_TF del PIP).
            # Los flujos se calculan con el spread facial + IPC vigente.
            if serie_ipc is None or serie_ipc.empty:
                # Sin serie IPC, usar aproximación corporativa
                res = valorar_bono_corporativo(
                    tasa_nominal=titulo.tasa_cupon,
                    yield_mercado=tasa_mercado_nueva,
                    fecha_emision=titulo.fecha_emision,
                    fecha_vencimiento=titulo.fecha_vencimiento,
                    valor_nominal=100.0,
                    frecuencia=titulo.frecuencia,
                    fecha_valuacion=fecha_valuacion,
                    tipo_tasa=titulo.tipo_tasa,
                )
                return nominal * res["precio_sucio"] / 100.0

            res = valorar_bono_ipc(
                spread=titulo.spread_ipc,
                yield_mercado=tasa_mercado_nueva,
                fecha_emision=titulo.fecha_emision,
                fecha_vencimiento=titulo.fecha_vencimiento,
                serie_ipc=serie_ipc,
                uso_tasa="F" if titulo.uso_tasa in ("F", "PREVIA") else "I",
                valor_nominal=100.0,
                frecuencia=titulo.frecuencia,
                fecha_valuacion=fecha_valuacion,
                senda_inflacion=senda_inflacion,
                fecha_inicio_senda=fecha_inicio_senda,
            )
            return nominal * res["precio_sucio"] / 100.0

        elif titulo.tipo_bono == "YANKEE":
            res = valorar_bono_yankee(
                tasa_nominal=titulo.tasa_cupon,
                yield_mercado=tasa_mercado_nueva,
                fecha_emision=titulo.fecha_emision,
                fecha_vencimiento=titulo.fecha_vencimiento,
                valor_nominal_usd=100.0,
                trm=trm,
                fecha_valuacion=fecha_valuacion,
            )
            # nominal en USD
            return nominal * res["precio_sucio"] / 100.0 * trm

        else:
            # Fallback: corporativo genérico
            res = valorar_bono_corporativo(
                tasa_nominal=titulo.tasa_cupon,
                yield_mercado=tasa_mercado_nueva,
                fecha_emision=titulo.fecha_emision,
                fecha_vencimiento=titulo.fecha_vencimiento,
                valor_nominal=100.0,
                frecuencia=titulo.frecuencia,
                fecha_valuacion=fecha_valuacion,
                tipo_tasa=titulo.tipo_tasa,
            )
            return nominal * res["precio_sucio"] / 100.0

    except Exception:
        # Si falla la valoración, mantener VM anterior
        return titulo.valor_mercado


# ==============================================================================
# FUNCIÓN 3: DETECTAR CUPONES EN UN PERÍODO
# ==============================================================================

def detectar_cupones(
    portafolio: list[Titulo],
    fecha_inicio: date,
    fecha_fin: date,
    serie_ipc: pd.DataFrame | None = None,
    senda_inflacion: list | None = None,
    fecha_inicio_senda: date | None = None,
) -> list[Cupon]:
    """
    Detecta todos los cupones que se pagan entre fecha_inicio y fecha_fin.

    Para bonos IPC, calcula el cupón con spread + IPC (misma lógica de valorar_bono_ipc).

    Args:
        portafolio: Lista de títulos activos.
        fecha_inicio: Inicio del período (exclusivo).
        fecha_fin: Fin del período (inclusivo).
        serie_ipc: Serie de inflación para cupones IPC.
        senda_inflacion: Lista de inflaciones mensuales proyectadas (% EA).
        fecha_inicio_senda: Fecha de inicio de la senda de inflación.

    Returns:
        Lista de Cupon detectados.
    """
    import calendar
    cupones_detectados: list[Cupon] = []

    meses_freq = {"anual": 12, "semestral": 6, "trimestral": 3, "mensual": 1}

    for titulo in portafolio:
        if titulo.fecha_vencimiento <= fecha_inicio:
            continue

        meses = meses_freq.get(titulo.frecuencia, 3)
        dia_base = titulo.fecha_vencimiento.day
        mes_base = titulo.fecha_vencimiento.month
        anio_base = titulo.fecha_vencimiento.year

        fechas_cupon = []
        periodo = 0
        while True:
            total_meses = mes_base - meses * periodo
            anio = anio_base + (total_meses - 1) // 12
            mes_calc = (total_meses - 1) % 12 + 1
            max_dia = calendar.monthrange(anio, mes_calc)[1]
            dia = min(dia_base, max_dia)
            fc = date(anio, mes_calc, dia)

            if fc <= titulo.fecha_emision:
                break
            fechas_cupon.append(fc)
            periodo += 1

        # Filtrar cupones en el rango
        for fc in fechas_cupon:
            if fecha_inicio < fc <= fecha_fin:
                monto = _calcular_monto_cupon(
                    titulo, meses, serie_ipc=serie_ipc, fecha_cupon=fc,
                    senda_inflacion=senda_inflacion, fecha_inicio_senda=fecha_inicio_senda)
                cupones_detectados.append(Cupon(
                    titulo=titulo,
                    fecha_pago=fc,
                    monto_cupon=monto,
                    monto_capital=0.0,
                    monto_total=monto,
                ))

    return cupones_detectados


def _calcular_monto_cupon(
    titulo: Titulo,
    meses_periodo: int,
    serie_ipc: pd.DataFrame | None = None,
    fecha_cupon: date | None = None,
    senda_inflacion: list | None = None,
    fecha_inicio_senda: date | None = None,
) -> float:
    """
    Calcula el monto del cupón según el tipo de bono.

    Para IPC: usa la misma lógica de valorar_bono_ipc() →
      facial_EA = (1 + IPC) * (1 + spread) - 1
      cupón = Nominal * [(1 + facial_EA)^(dias_periodo/360) - 1]

    Para TF/UVR: usa tasa facial directa.
    
    Para bonos IPC con uso_tasa PREVIA: el IPC del cupón es el vigente al INICIO
    del período (un trimestre antes). Para ACTUAL: IPC vigente a la FECHA del cupón.
    """
    nominal = titulo.nominal
    dias_periodo = meses_periodo * 30

    if titulo.tipo_bono == "IPC":
        # Lógica idéntica a valorar_bono_ipc: spread + IPC compuesto
        spread = titulo.spread_ipc / 100.0

        # Determinar fecha relevante para el IPC según uso_tasa
        # PREVIA (uso_tasa "F"): IPC vigente al inicio del período (un período antes del cupón)
        # ACTUAL (uso_tasa "I"): IPC vigente a la fecha del cupón
        if titulo.uso_tasa in ("F", "PREVIA"):
            # Fecha inicio del período = fecha_cupon - meses_periodo
            from dateutil.relativedelta import relativedelta as _rd
            fecha_ipc_ref = fecha_cupon - _rd(months=meses_periodo) if fecha_cupon else None
        else:
            fecha_ipc_ref = fecha_cupon

        # Obtener IPC vigente — priorizar senda de inflación para fechas futuras
        ipc_anual = 0.0584  # Default: ~5.84%
        if senda_inflacion and fecha_ipc_ref and fecha_inicio_senda:
            # Calcular índice en la senda
            meses_desde_inicio = ((fecha_ipc_ref.year - fecha_inicio_senda.year) * 12
                                  + fecha_ipc_ref.month - fecha_inicio_senda.month)
            if meses_desde_inicio >= 0:
                idx_senda = min(meses_desde_inicio, len(senda_inflacion) - 1)
                ipc_anual = senda_inflacion[idx_senda] / 100.0  # Convertir % a decimal
            elif serie_ipc is not None and not serie_ipc.empty:
                # Antes de la senda: usar IPC histórico
                try:
                    from valorar_bonos import _obtener_ipc_vigente
                    ipc_anual = _obtener_ipc_vigente(fecha_ipc_ref, serie_ipc)
                except Exception:
                    ipc_anual = float(serie_ipc.iloc[-1]["ipc_anual"])
        elif serie_ipc is not None and not serie_ipc.empty:
            try:
                from valorar_bonos import _obtener_ipc_vigente
                if fecha_ipc_ref is not None:
                    ipc_anual = _obtener_ipc_vigente(fecha_ipc_ref, serie_ipc)
                else:
                    ipc_anual = float(serie_ipc.iloc[-1]["ipc_anual"])
            except Exception:
                ipc_anual = float(serie_ipc.iloc[-1]["ipc_anual"]) if not serie_ipc.empty else 0.0584

        # facial_EA = (1 + IPC) * (1 + spread) - 1
        facial_ea = (1 + ipc_anual) * (1 + spread) - 1

        # Cupón del período: Nominal * [(1 + facial_EA)^(dias/360) - 1]
        base = titulo.base_anual if titulo.base_anual > 0 else 360
        tasa_periodo = (1 + facial_ea) ** (dias_periodo / base) - 1
        return nominal * tasa_periodo

    elif titulo.tipo_bono == "UVR":
        # UVR: cupón real sobre nominal en UVR
        # NOTA: el monto retornado está en UVR, se multiplica por valor_uvr en detectar_cupones
        tasa = titulo.tasa_cupon / 100.0
        base = titulo.base_anual if titulo.base_anual > 0 else 365
        return nominal * ((1 + tasa) ** (dias_periodo / base) - 1)

    else:
        # TF y YANKEE: tasa facial directa
        tasa = titulo.tasa_cupon / 100.0
        if titulo.tipo_tasa == "NOMINAL":
            periodos_anio = 12 // meses_periodo
            return nominal * (tasa / periodos_anio)
        else:
            base = titulo.base_anual if titulo.base_anual > 0 else 365
            return nominal * ((1 + tasa) ** (dias_periodo / base) - 1)


# ==============================================================================
# FUNCIÓN 4: REINVERTIR CUPÓN
# ==============================================================================

def reinvertir_cupon(
    cupon: Cupon,
    precio_sucio_pct: float,
    tasa_compra_nueva: float,
    config: ConfigReinversion | None = None,
) -> Titulo:
    """
    Crea un nuevo título por reinversión de un cupón.

    Args:
        cupon: Cupón cobrado.
        precio_sucio_pct: Precio sucio del título destino (%).
        tasa_compra_nueva: Tasa de compra del nuevo título (%).
        config: Override de reinversión (si None, hereda todo).

    Returns:
        Nuevo Titulo creado por reinversión.
    """
    original = cupon.titulo

    if config is None:
        config = ConfigReinversion(
            especie_destino=original.especie,
            clasificacion=original.clasificacion,
            ramo=original.ramo,
            portafolio=original.portafolio,
            compania=original.compania,
        )

    if precio_sucio_pct > 0:
        nominal_nuevo = cupon.monto_total / (precio_sucio_pct / 100.0)
    else:
        nominal_nuevo = cupon.monto_total

    nuevo = Titulo(
        id_posicion=f"{original.id_posicion}_R{cupon.fecha_pago.strftime('%Y%m%d')}",
        especie=config.especie_destino,
        emisor=original.emisor,
        isin=original.isin,
        moneda=original.moneda,
        fecha_emision=cupon.fecha_pago,
        fecha_vencimiento=original.fecha_vencimiento,
        nominal=nominal_nuevo,
        tasa_cupon=original.tasa_cupon,
        spread_ipc=original.spread_ipc,
        tipo_bono=original.tipo_bono,
        frecuencia=original.frecuencia,
        tipo_tasa=original.tipo_tasa,
        base_anual=original.base_anual,
        uso_tasa=original.uso_tasa,
        clasificacion=config.clasificacion,
        compania=config.compania,
        ramo=config.ramo,
        portafolio=config.portafolio,
        asset_class=original.asset_class,
        tasa_compra=tasa_compra_nueva,
        tasa_mercado=tasa_compra_nueva,
        valor_mercado=cupon.monto_total,
        valor_tir=cupon.monto_total,
        es_reinversion=True,
        fecha_reinversion=cupon.fecha_pago,
        titulo_origen=original.id_posicion,
        vm_compra=cupon.monto_total,
        spread_mercado=original.spread_mercado,
        id_cobertura=original.id_cobertura,   # Defecto 1: hereda condición de subyacente
    )

    return nuevo


# ==============================================================================
# FUNCIÓN 5: LOOP PRINCIPAL DE PROYECCIÓN (CON RE-VALORACIÓN REAL)
# ==============================================================================

def proyectar_portafolio(
    portafolio: list[Titulo],
    fecha_inicio: date,
    meses_horizonte: int,
    sendas: dict[str, dict[int, list[float]]],
    overrides_reinversion: dict[str, ConfigReinversion] | None = None,
    valor_uvr: float = 411.2813,
    valor_uvr_cierre: float | None = None,
    trm: float = 3667.06,
    trm_cierre: float | None = None,
    serie_ipc: pd.DataFrame | None = None,
    senda_inflacion: list | None = None,
    shift_ipc_bps: float = 0.0,
    shift_cop_bps: float = 0.0,
    shift_uvr_bps: float = 0.0,
    shift_fs_bps: float = 0.0,
    shift_subyac_bps: float = 0.0,
    shift_tesoros_bps: float = 0.0,
    ipc_vigente_t0: float | None = None,
    senda_repo: list | None = None,
    mapa_reservas: dict[str, dict[int, float]] | None = None,
) -> list[ResultadoMes]:
    """
    Ejecuta la simulación completa del portafolio mes a mes.

    Para cada mes:
    1. Detecta cupones cobrados en el período
    2. Reinvierte cada cupón (crea título clonado)
    3. RE-VALORA cada título con la tasa de la senda del mes
    4. Calcula P&L real por movimiento de tasas

    Args:
        portafolio: Lista de títulos al inicio.
        fecha_inicio: Fecha de valoración inicial (T0).
        meses_horizonte: Número de meses a proyectar.
        sendas: Sendas por curva {nombre_curva: {nodo: [tasas_mensuales]}}
        overrides_reinversion: Overrides por id_posicion o especie.
        valor_uvr: Valor UVR inicial.
        trm: TRM inicial.
        serie_ipc: Serie de inflación para bonos IPC.
        senda_inflacion: Lista de inflaciones mensuales proyectadas (% EA, ej [5.5, 5.3, ...]).
                         Usado para valorar bonos IPC al vencimiento con inflación futura.

    Returns:
        Lista de ResultadoMes con el detalle de cada período.
    """
    if overrides_reinversion is None:
        overrides_reinversion = {}

    # Cargar serie IPC si no se provee
    if serie_ipc is None:
        try:
            serie_ipc = cargar_serie_ipc("Serie Inflación.xlsx")
        except Exception:
            serie_ipc = pd.DataFrame()

    resultados: list[ResultadoMes] = []
    portafolio_activo = list(portafolio)

    # Track cupones cobrados por título (para P&L VC/DV)
    cupones_por_titulo: dict[str, float] = {}

    # Track per-title monthly snapshots for contable P&L (RF_Mensual fix)
    # Structure: {id_posicion: {mes: {'vm': float, 'vtir': float, 'cupones': float}}}
    snapshots_por_titulo: dict[str, dict[int, dict]] = {}
    # Track cupones cobrados por título POR MES
    cupones_por_titulo_mes: dict[str, dict[int, float]] = {}

    # Senda UVR interpolada linealmente mes a mes
    _uvr_t0 = valor_uvr
    _uvr_fin = valor_uvr_cierre if valor_uvr_cierre else valor_uvr
    _senda_uvr = [_uvr_t0 + (_uvr_fin - _uvr_t0) * m / meses_horizonte
                  for m in range(meses_horizonte + 1)]
    # _senda_uvr[0] = UVR T0, _senda_uvr[n_meses] = UVR cierre

    # Senda TRM interpolada linealmente mes a mes
    _trm_t0 = trm
    _trm_fin = trm_cierre if trm_cierre else trm
    _senda_trm = [_trm_t0 + (_trm_fin - _trm_t0) * m / meses_horizonte
                  for m in range(meses_horizonte + 1)]

    # Senda Repo/TPM por mes (para Vista COP: Cta Ahorro y FIC).
    # senda_repo viene como lista de tasas anuales (%) por mes desde el dash.
    # Si no se provee, usa 0 (los vista COP quedan planos).
    if senda_repo and len(senda_repo) > 0:
        _senda_repo = [float(senda_repo[min(m, len(senda_repo) - 1)]) / 100.0
                       for m in range(meses_horizonte + 1)]
    else:
        _senda_repo = [0.0] * (meses_horizonte + 1)

    # IPC vigente a T0 para deflactar rendimientos PIP de bonos IPC
    _ipc_t0 = ipc_vigente_t0 if ipc_vigente_t0 is not None else 0.06

    # Valoración inicial T0
    vm_anterior = sum(t.valor_mercado for t in portafolio_activo)
    vtir_anterior = vm_anterior
    pnl_mercado_acum = 0.0
    pnl_tir_acum = 0.0

    # Snapshot T0: guardar VM y VTIR iniciales por título (valores del 597)
    for t in portafolio_activo:
        snapshots_por_titulo[t.id_posicion] = {
            0: {'vm': t.valor_mercado, 'vtir': t.valor_tir, 'cupones': 0.0}
        }

    # ── Carga única de compras/ventas por variación de reserva (Req 10.3, 13.3, 13.4) ──
    # Se cargan una sola vez por corrida el mapa de variaciones de reserva, el universo
    # de compra (hist_pip), la clasificación de ramos y los parámetros de plazo. Todo va
    # envuelto en try/except: si algo falla, mapa_reservas queda vacío y el motor corre
    # con su comportamiento actual intacto (retrocompatibilidad).
    #
    # Inyección del mapa (feature proyeccion-por-tramos): si el llamador pasa
    # `mapa_reservas` (override, p. ej. el mapa prorrateado de un tramo), el motor lo usa
    # TAL CUAL sin abrir la DB para ese dato. Si es None (default), se conserva el
    # comportamiento actual: se deriva vía `cargar_mapa_reservas`. En ambos casos el
    # universo de compra, la clasificación de ramos y los parámetros de plazo se cargan de
    # la DB (son ortogonales al mapa de variaciones).
    _mapa_reservas_override = mapa_reservas
    mapa_reservas: dict[str, dict[int, float]] = {}
    universo_compra = None
    config_ramos = None
    parametros_reservas = None
    try:
        from compras_ventas_reservas import (
            cargar_mapa_reservas,
            cargar_universo_compra,
            cargar_ramos_reserva_matematica,
            cargar_parametros_reservas,
            aplicar_compras_ventas_reservas,
        )
        from comun.rutas import DB_PATH as _DB_PATH_RESERVAS

        _ruta_db = str(_DB_PATH_RESERVAS)
        # fecha_pip=None → cargar_universo_compra usa el corte más reciente de hist_pip.
        if _mapa_reservas_override is not None:
            # Override explícito del llamador (p. ej. mapa prorrateado del tramo).
            mapa_reservas = _mapa_reservas_override
        else:
            # Comportamiento actual: derivar el mapa desde la DB.
            mapa_reservas = cargar_mapa_reservas(_ruta_db, fecha_inicio, meses_horizonte)
        universo_compra = cargar_universo_compra(_ruta_db, None)
        config_ramos = cargar_ramos_reserva_matematica(_ruta_db)
        parametros_reservas = cargar_parametros_reservas(_ruta_db)
    except Exception:
        logging.getLogger('motor_proyeccion').warning(
            'No se pudo cargar el input de compras/ventas por reserva; '
            'el motor continúa sin operar reservas.'
        )
        # Si hubo override, se respeta aun ante fallo de la carga auxiliar; si no, vacío.
        mapa_reservas = _mapa_reservas_override if _mapa_reservas_override is not None else {}

    for mes in range(1, meses_horizonte + 1):
        # `fecha_mes` es la FECHA DE VALORACIÓN del mes (fin del período medido): el P&L del
        # mes `mes` se mide sobre (fecha_anterior, fecha_mes]. NO se altera: de ella dependen
        # la detección de cupones, los vencimientos, la valoración (`valorar_titulo`), los
        # días residuales y los conteos. Cambiarla modificaría VM/VTIR/P&L/cupones.
        fecha_mes = fecha_inicio + relativedelta(months=mes)
        fecha_anterior = fecha_inicio + relativedelta(months=mes - 1)

        # Etiqueta de fecha del `ResultadoMes`. La ETIQUETA depende del CARÁCTER del arranque
        # del tramo (Fase 2 — Defecto 3, corrige la regresión del fix de Fase 1):
        #
        #   - Si el tramo arranca el 1-ene (`fecha_inicio.day == 1`): su T0 es el CIERRE del
        #     año anterior, por lo que aplica la regla de negocio "el cierre de un año es el
        #     T0 del año siguiente". El mes `mes` ETIQUETA el mes que ARRANCA en T0+(mes-1):
        #     el mes 1 es ENERO (no febrero) y el mes 12 es DICIEMBRE → un año completo produce
        #     ene..dic (12 etiquetas correctas) y el enero de arranque de cada tramo i>0 queda
        #     presente. (Comportamiento de la Fase 1 — se preserva sin regresión.)
        #
        #   - Si el tramo arranca a MITAD de mes (`fecha_inicio.day != 1`): es el PRIMER tramo
        #     PARCIAL (p.ej. 31-jul-2026) —o una corrida `punta_a_punta` cuya fecha base de
        #     sidebar también es de mitad de mes—. Aquí el T0 NO es el cierre de un año: es la
        #     fecha base real, un mes SIN P&G proyectado. Por eso la etiqueta usa el mes de
        #     VALORACIÓN (`fecha_mes = T0 + months=mes`), de modo que el primer mes PROYECTADO
        #     se etiquete con el mes SIGUIENTE al T0 (ago-2026) y la serie cubra ago..dic sin
        #     perder diciembre ni introducir el T0 como un mes de P&G.
        #
        # En AMBOS casos la VALORACIÓN usa `fecha_mes` (no se toca) → VM/VTIR/P&L/cupones son
        # idénticos; aquí solo cambia la ETIQUETA del `ResultadoMes`. El P&G de enero (tramos
        # 1-ene) sigue siendo VM(fin enero) − VM(cierre 31-dic anterior = T0), coherente con
        # `_snapshots_mensuales` (indexados por `mes`, no por la etiqueta).
        if fecha_inicio.day == 1:
            fecha_etiqueta_mes = fecha_inicio + relativedelta(months=mes - 1)
        else:
            fecha_etiqueta_mes = fecha_mes

        # Correcciones de P&L por compras/ventas de reserva (0.0 si no hay input).
        # Se inicializan al tope de cada iteración para que la ruta "sin input" y el
        # bloque de P&L (── 4 ──) siempre las tengan definidas.
        ori_reciclado_mes = 0.0
        cupones_no_reinvertidos_mes = 0.0
        liquidez_bloqueada: dict[str, float] = {}

        # ── 1. Detectar cupones del período ──
        cupones = detectar_cupones(portafolio_activo, fecha_anterior, fecha_mes, serie_ipc,
                                   senda_inflacion=senda_inflacion, fecha_inicio_senda=fecha_inicio)
        # Para UVR: convertir cupones de UVR a COP con la UVR del mes
        for cupon in cupones:
            if cupon.titulo.tipo_bono == 'UVR':
                cupon.monto_cupon = cupon.monto_cupon * _senda_uvr[mes]
                cupon.monto_total = cupon.monto_cupon + cupon.monto_capital
        cupones_total = sum(c.monto_total for c in cupones)

        # Acumular cupones por título
        for cupon in cupones:
            tid = cupon.titulo.id_posicion
            cupones_por_titulo[tid] = cupones_por_titulo.get(tid, 0.0) + cupon.monto_total
            # Acumular cupones por título por mes
            if tid not in cupones_por_titulo_mes:
                cupones_por_titulo_mes[tid] = {}
            cupones_por_titulo_mes[tid][mes] = cupones_por_titulo_mes[tid].get(mes, 0.0) + cupon.monto_total

        # ── 1B. Reinvertir títulos que vencen en este período ──
        vencimientos_mes: list[Titulo] = []
        for titulo in portafolio_activo:
            if fecha_anterior < titulo.fecha_vencimiento <= fecha_mes:
                # Este título venció en el período
                plazo_anos = round((titulo.fecha_vencimiento - titulo.fecha_emision).days / 365)
                if plazo_anos < 1:
                    plazo_anos = 1

                # Monto recibido al vencimiento = nominal (principal devuelto)
                monto_vencimiento = titulo.nominal
                # Para UVR: convertir nominal (en UVR) a COP
                if titulo.tipo_bono == 'UVR':
                    monto_vencimiento = titulo.nominal * _senda_uvr[mes]

                # Buscar título hermano con mismo plazo y clasificación
                hermano = None
                candidatos = [t for t in portafolio_activo
                              if t.id_posicion != titulo.id_posicion
                              and t.fecha_vencimiento > fecha_mes
                              and not t.es_reinversion
                              and t.tipo_bono == titulo.tipo_bono]

                # Filtrar por plazo similar (±1 año)
                for c in candidatos:
                    plazo_c = round((c.fecha_vencimiento - c.fecha_emision).days / 365)
                    if abs(plazo_c - plazo_anos) <= 1:
                        if c.clasificacion == titulo.clasificacion:
                            if hermano is None or c.nominal > hermano.nominal:
                                hermano = c

                # Si no encontró con misma clasificación, buscar sin esa restricción
                if hermano is None:
                    for c in candidatos:
                        plazo_c = round((c.fecha_vencimiento - c.fecha_emision).days / 365)
                        if abs(plazo_c - plazo_anos) <= 1:
                            if hermano is None or c.nominal > hermano.nominal:
                                hermano = c

                # Crear título hijo por reinversión al vencimiento
                nueva_fecha_emision = titulo.fecha_vencimiento
                nueva_fecha_vencimiento = nueva_fecha_emision + relativedelta(years=plazo_anos)

                if hermano:
                    # Heredar del hermano
                    _spread_reinv_venc = hermano.spread_mercado
                    _tasa_cupon_reinv = hermano.tasa_cupon
                    _spread_ipc_reinv = hermano.spread_ipc
                    _frecuencia_reinv = hermano.frecuencia
                    _tipo_tasa_reinv = hermano.tipo_tasa
                    _base_anual_reinv = hermano.base_anual
                    _uso_tasa_reinv = hermano.uso_tasa
                    _especie_reinv = hermano.especie
                    _isin_reinv = hermano.isin
                else:
                    # Autoréplica: mantener características, tasa por mediana del plazo
                    _spread_reinv_venc = titulo.spread_mercado  # se ajustará abajo si es 0
                    _tasa_cupon_reinv = titulo.tasa_cupon
                    _spread_ipc_reinv = titulo.spread_ipc
                    _frecuencia_reinv = titulo.frecuencia
                    _tipo_tasa_reinv = titulo.tipo_tasa
                    _base_anual_reinv = titulo.base_anual
                    _uso_tasa_reinv = titulo.uso_tasa
                    _especie_reinv = titulo.especie
                    _isin_reinv = titulo.isin
                    # Si no tiene PIP, buscar mediana del PIP para ese plazo
                    if _spread_reinv_venc == 0:
                        _dias_plazo_nuevo = plazo_anos * 365
                        curva_n = obtener_nombre_curva(titulo.asset_class)
                        if curva_n in sendas:
                            _spread_reinv_venc = obtener_tasa_senda(sendas[curva_n], mes, _dias_plazo_nuevo)

                # Tasa de compra interpolada al mes
                if _spread_reinv_venc > 0:
                    if titulo.tipo_bono == 'IPC':
                        _sr = (1 + _spread_reinv_venc / 100.0) / (1 + _ipc_t0) - 1
                        _si = (shift_ipc_bps / 10000.0) * mes / meses_horizonte
                        _tasa_compra_venc = (_sr + _si) * 100.0
                    elif titulo.tipo_bono == 'UVR':
                        _si = (shift_uvr_bps / 100.0) * mes / meses_horizonte
                        _tasa_compra_venc = _spread_reinv_venc + _si
                    elif titulo.tipo_bono == 'YANKEE' or obtener_nombre_curva(titulo.asset_class) == 'USD':
                        _si = (shift_subyac_bps / 100.0) * mes / meses_horizonte
                        _tasa_compra_venc = _spread_reinv_venc + _si
                    elif obtener_nombre_curva(titulo.asset_class) == 'DP_TF':
                        _si = (shift_cop_bps / 100.0) * mes / meses_horizonte
                        _tasa_compra_venc = _spread_reinv_venc + _si
                    else:
                        _si = (shift_fs_bps / 100.0) * mes / meses_horizonte
                        _tasa_compra_venc = _spread_reinv_venc + _si
                else:
                    _tasa_compra_venc = titulo.tasa_mercado

                # Calcular nominal nuevo = monto / (precio/100)
                _precio_venc = 100.0  # default par
                try:
                    _titulo_temp = copy.copy(titulo)
                    _titulo_temp.fecha_emision = nueva_fecha_emision
                    _titulo_temp.fecha_vencimiento = nueva_fecha_vencimiento
                    _vm_temp = valorar_titulo(_titulo_temp, _tasa_compra_venc, nueva_fecha_emision,
                                             valor_uvr=_senda_uvr[mes], trm=_senda_trm[mes], serie_ipc=serie_ipc)
                    if _vm_temp > 0 and titulo.nominal > 0:
                        _precio_venc = _vm_temp / titulo.nominal * 100.0
                except Exception:
                    _precio_venc = 100.0

                if _precio_venc > 0:
                    _nominal_nuevo = monto_vencimiento / (_precio_venc / 100.0)
                else:
                    _nominal_nuevo = monto_vencimiento

                nuevo_venc = Titulo(
                    id_posicion=f"{titulo.id_posicion}_V{titulo.fecha_vencimiento.strftime('%Y%m%d')}",
                    especie=_especie_reinv,
                    emisor=titulo.emisor,
                    isin=_isin_reinv,
                    moneda=titulo.moneda,
                    fecha_emision=nueva_fecha_emision,
                    fecha_vencimiento=nueva_fecha_vencimiento,
                    nominal=_nominal_nuevo,
                    tasa_cupon=_tasa_cupon_reinv,
                    spread_ipc=_spread_ipc_reinv,
                    tipo_bono=titulo.tipo_bono,
                    frecuencia=_frecuencia_reinv,
                    tipo_tasa=_tipo_tasa_reinv,
                    base_anual=_base_anual_reinv,
                    uso_tasa=_uso_tasa_reinv,
                    clasificacion=titulo.clasificacion,
                    compania=titulo.compania,
                    ramo=titulo.ramo,
                    portafolio=titulo.portafolio,
                    asset_class=titulo.asset_class,
                    tasa_compra=_tasa_compra_venc,
                    tasa_mercado=_tasa_compra_venc,
                    valor_mercado=monto_vencimiento,
                    valor_tir=monto_vencimiento,
                    es_reinversion=True,
                    fecha_reinversion=titulo.fecha_vencimiento,
                    titulo_origen=titulo.id_posicion,
                    vm_compra=monto_vencimiento,
                    spread_mercado=_spread_reinv_venc,
                    id_cobertura=titulo.id_cobertura,   # Defecto 1: hereda condición de subyacente
                )
                vencimientos_mes.append(nuevo_venc)

        portafolio_activo.extend(vencimientos_mes)

        # ── 1C. Compras/Ventas por variación de reserva (mes caído: usa t-1) ──
        # Solo se ejecuta si hay input de reservas; si no, comportamiento actual intacto.
        if mapa_reservas:
            try:
                # Liquidez del mes por ramo = cupones detectados este mes agrupados por
                # ramo. La porción de cupón coincide con esa misma liquidez (cada cupón
                # aporta su monto_total al ramo del título).
                liquidez_mes_por_ramo: dict[str, float] = {}
                cupon_mes_por_ramo: dict[str, float] = {}
                for _cup in cupones:
                    _r = (getattr(_cup.titulo, 'ramo', '') or '').strip().upper()
                    liquidez_mes_por_ramo[_r] = liquidez_mes_por_ramo.get(_r, 0.0) + _cup.monto_total
                    cupon_mes_por_ramo[_r] = cupon_mes_por_ramo.get(_r, 0.0) + _cup.monto_total
                resultado_cvr = aplicar_compras_ventas_reservas(
                    portafolio_activo, mapa_reservas, mes, fecha_mes, universo_compra,
                    sendas, _senda_uvr[mes], _senda_trm[mes], serie_ipc,
                    config_ramos, parametros_reservas,
                    liquidez_mes_por_ramo=liquidez_mes_por_ramo,
                    cupon_mes_por_ramo=cupon_mes_por_ramo,
                )
                portafolio_activo = resultado_cvr.portafolio
                ori_reciclado_mes = resultado_cvr.ori_reciclado
                cupones_no_reinvertidos_mes = sum(resultado_cvr.cupones_no_reinvertidos.values())
                # Liquidez consumida por el waterfall: NO debe reinvertirse (Req 10.2).
                liquidez_bloqueada = dict(resultado_cvr.liquidez_consumida)
                # Ramos pendientes de clasificar → advertencia agregada.
                if resultado_cvr.ramos_pendientes:
                    logging.getLogger('motor_proyeccion').warning(
                        'Hay ramos con variación de reserva sin clasificar; revisar Ajustes.')
            except Exception:
                # Fail-safe (Req 2.6): si la decisión falla, se continúa con la
                # reinversión normal sin correcciones de P&L.
                ori_reciclado_mes = 0.0
                cupones_no_reinvertidos_mes = 0.0
                liquidez_bloqueada = {}

        # ── 2. Reinvertir cada cupón ──
        reinversiones_mes: list[Titulo] = []
        for cupon in cupones:
            # Liquidez bloqueada por el waterfall (Req 10.2): si el flujo de cupón de un
            # ramo fue consumido para cubrir la disminución de reserva, NO se reinvierte
            # hasta agotar el monto bloqueado (retención parcial). Sin input de reservas,
            # liquidez_bloqueada está vacío y este bloque es un no-op.
            if liquidez_bloqueada:
                _r_cup = (getattr(cupon.titulo, 'ramo', '') or '').strip().upper()
                _bloqueado = liquidez_bloqueada.get(_r_cup, 0.0)
                if _bloqueado > 0.0:
                    liquidez_bloqueada[_r_cup] = max(0.0, _bloqueado - cupon.monto_total)
                    continue

            curva_nombre = obtener_nombre_curva(cupon.titulo.asset_class)
            dias_residual = (cupon.titulo.fecha_vencimiento - cupon.fecha_pago).days
            if dias_residual <= 0:
                continue

            # Tasa de compra para la reinversión (interpolada linealmente en el horizonte)
            if cupon.titulo.spread_mercado > 0:
                # Interpolar shift: al mes M, el shift aplicado = shift_total * M / n_meses
                if cupon.titulo.tipo_bono == 'IPC':
                    _spread_r_reinv = (1 + cupon.titulo.spread_mercado / 100.0) / (1 + _ipc_t0) - 1
                    _shift_interp = (shift_ipc_bps / 10000.0) * mes / meses_horizonte
                    _spread_reinv = _spread_r_reinv + _shift_interp
                    tasa_compra_nueva = _spread_reinv * 100.0
                elif cupon.titulo.tipo_bono == 'UVR':
                    _shift_interp = (shift_uvr_bps / 100.0) * mes / meses_horizonte
                    tasa_compra_nueva = cupon.titulo.spread_mercado + _shift_interp
                elif cupon.titulo.tipo_bono == 'YANKEE' or curva_nombre == 'USD':
                    _shift_interp = (shift_subyac_bps / 100.0) * mes / meses_horizonte
                    tasa_compra_nueva = cupon.titulo.spread_mercado + _shift_interp
                elif curva_nombre == 'DP_TF':
                    _shift_interp = (shift_cop_bps / 100.0) * mes / meses_horizonte
                    tasa_compra_nueva = cupon.titulo.spread_mercado + _shift_interp
                else:
                    _shift_interp = (shift_fs_bps / 100.0) * mes / meses_horizonte
                    tasa_compra_nueva = cupon.titulo.spread_mercado + _shift_interp
            elif curva_nombre in sendas:
                tasa_compra_nueva = obtener_tasa_senda(
                    sendas[curva_nombre], mes, dias_residual)
            else:
                tasa_compra_nueva = cupon.titulo.tasa_mercado

            # Precio sucio del título destino: valorar a la tasa de compra al plazo residual
            if cupon.titulo.nominal > 0:
                # Calcular precio real con la tasa de la senda (tasa_compra_nueva)
                try:
                    precio_res = valorar_titulo(
                        cupon.titulo, tasa_compra_nueva, cupon.fecha_pago,
                        valor_uvr=_senda_uvr[mes] if '_senda_uvr' in dir() else valor_uvr,
                        trm=trm, serie_ipc=serie_ipc)
                    precio_sucio_pct = precio_res / cupon.titulo.nominal * 100 if cupon.titulo.nominal > 0 else 100.0
                except Exception:
                    precio_sucio_pct = cupon.titulo.valor_mercado / cupon.titulo.nominal * 100
            else:
                precio_sucio_pct = 100.0

            override = overrides_reinversion.get(
                cupon.titulo.id_posicion,
                overrides_reinversion.get(cupon.titulo.especie, None)
            )

            nuevo_titulo = reinvertir_cupon(
                cupon, precio_sucio_pct, tasa_compra_nueva, override)
            # Para IPC: el spread_ipc de la reinversión es el spread deflactado
            if cupon.titulo.tipo_bono == 'IPC' and senda_inflacion:
                nuevo_titulo.spread_ipc = tasa_compra_nueva  # ya es el spread en %
            reinversiones_mes.append(nuevo_titulo)

        portafolio_activo.extend(reinversiones_mes)

        # ── 3. RE-VALORAR portafolio con tasas de la senda del mes ──
        vm_total = 0.0
        vtir_total = 0.0

        # IPC vigente para el mes actual (para recomponer yield IPC)
        # Senda indexada por mes de MEDICIÓN: senda[0]=Corte, [5]=nov(vigente en dic)
        # IPC vigente en el mes de valoración = senda[mes] (el punto medido ese mes = vigente al siguiente)
        _ipc_mes = None
        if senda_inflacion and len(senda_inflacion) > 0:
            _idx_ipc_mes = min(mes, len(senda_inflacion) - 1)
            _ipc_mes = senda_inflacion[_idx_ipc_mes] / 100.0
        elif ipc_vigente_t0 is not None:
            _ipc_mes = ipc_vigente_t0
        else:
            _ipc_mes = 0.06  # Default

        for titulo in portafolio_activo:
            if titulo.fecha_vencimiento <= fecha_mes:
                continue  # Ya venció

            # ── Cuentas Vista y Time Deposits ──
            _esp_up = titulo.especie.upper()
            _es_time = 'TIME' in _esp_up
            _es_fic = titulo.asset_class.upper().startswith('FIC') or _esp_up.startswith('FIC')
            _es_cta_ahorro = 'CTA AHORRO' in _esp_up or 'CUENTA AHORRO' in _esp_up

            # TIME DEP: nominal USD × TRM × (1 + tasa_compra/360 × 50 días), renovación a 50d
            if _es_time:
                _tasa_td = (titulo.tasa_compra / 100.0) if titulo.tasa_compra else (titulo.tasa_cupon / 100.0 if titulo.tasa_cupon else 0.0)
                _factor_td = 1 + _tasa_td / 360.0 * 50.0
                vm_nuevo = titulo.nominal * _senda_trm[mes] * _factor_td
                vtir_nuevo = vm_nuevo
                titulo.valor_mercado = vm_nuevo
                titulo.valor_tir = vtir_nuevo
                titulo.tasa_mercado = titulo.tasa_compra
                vm_total += vm_nuevo
                vtir_total += vtir_nuevo
                continue

            # VM base (T0) de la cuenta vista, guardado en el snapshot inicial
            _vm_base_t0 = snapshots_por_titulo.get(titulo.id_posicion, {}).get(0, {}).get('vm', titulo.valor_mercado)

            # FIC (Vista): retorno = tasa repo del período (capitaliza desde T0)
            if _es_fic:
                _dias_trans = (fecha_mes - fecha_inicio).days
                _tasa_fic = _senda_repo[mes]
                _factor_fic = 1 + _tasa_fic * _dias_trans / 365.0
                vm_nuevo = _vm_base_t0 * _factor_fic
                vtir_nuevo = vm_nuevo
                titulo.valor_mercado = vm_nuevo
                titulo.valor_tir = vtir_nuevo
                vm_total += vm_nuevo
                vtir_total += vtir_nuevo
                continue

            # Cta Ahorro (Vista COP): retorno = max(tasa repo - 6%, 0)
            if _es_cta_ahorro:
                _dias_trans = (fecha_mes - fecha_inicio).days
                _tasa_ahorro = max(_senda_repo[mes] - 0.06, 0.0)
                _factor_ah = 1 + _tasa_ahorro * _dias_trans / 365.0
                vm_nuevo = _vm_base_t0 * _factor_ah
                vtir_nuevo = vm_nuevo
                titulo.valor_mercado = vm_nuevo
                titulo.valor_tir = vtir_nuevo
                vm_total += vm_nuevo
                vtir_total += vtir_nuevo
                continue

            # Resto VISTA USD (depósitos USD sin remuneración explícita): nominal × TRM
            if titulo.asset_class == 'VISTA USD':
                vm_nuevo = titulo.nominal * _senda_trm[mes]
                vtir_nuevo = vm_nuevo
                titulo.valor_mercado = vm_nuevo
                titulo.valor_tir = vtir_nuevo
                vm_total += vm_nuevo
                vtir_total += vtir_nuevo
                continue

            curva_nombre = obtener_nombre_curva(titulo.asset_class)
            dias_residual = (titulo.fecha_vencimiento - fecha_mes).days

            # _spread_efectivo: spread base desplazado por el shift aplicado este mes,
            # expresado en la MISMA convención que `spread_mercado` de entrada (nominal),
            # para el encadenamiento por tramos (Opción A). Se sobrescribe cada mes; al
            # terminar el loop conserva el valor del ÚLTIMO mes = spread efectivo de cierre.
            _spread_efectivo = titulo.spread_mercado

            # Para títulos IPC: usar spread individual deflactado + shift gradual + recomponer con IPC
            if titulo.tipo_bono == 'IPC' and titulo.spread_mercado > 0:
                # 1. Deflactar rendimiento PIP con IPC vigente a T0
                _spread_real = (1 + titulo.spread_mercado / 100.0) / (1 + _ipc_t0) - 1
                # 2. Aplicar shift gradual al spread (proporcional al mes en el horizonte)
                _spread_shifted = _spread_real + (shift_ipc_bps / 10000.0) * mes / meses_horizonte
                # 3. Recomponer yield con IPC vigente a la fecha de valoración (fecha_mes)
                tasa_mkt = ((1 + _ipc_mes) * (1 + _spread_shifted) - 1) * 100.0
                # Spread efectivo IPC: recomponer el spread real desplazado con el IPC de T0
                # para mantener la convención nominal de `spread_mercado` (así, cuando el
                # tramo siguiente lo deflacte con SU _ipc_t0, recupera este spread real).
                _spread_efectivo = ((1 + _ipc_t0) * (1 + _spread_shifted) - 1) * 100.0
            elif titulo.spread_mercado > 0:
                # TF, UVR, Yankees: rendimiento PIP individual + shift gradual
                if titulo.tipo_bono == 'UVR':
                    _shift_titulo = (shift_uvr_bps / 100.0) * mes / meses_horizonte
                elif titulo.asset_class in ('LEGATRUU', 'Estructuras'):
                    _shift_titulo = (shift_tesoros_bps / 100.0) * mes / meses_horizonte
                elif titulo.tipo_bono == 'YANKEE' or curva_nombre == 'USD':
                    _shift_titulo = (shift_subyac_bps / 100.0) * mes / meses_horizonte
                elif curva_nombre == 'DP_TF':
                    _shift_titulo = (shift_cop_bps / 100.0) * mes / meses_horizonte
                else:
                    # Corporativos TF (CORP_TF)
                    _shift_titulo = (shift_fs_bps / 100.0) * mes / meses_horizonte
                tasa_mkt = titulo.spread_mercado + _shift_titulo
                # TF/UVR/Yankee: el spread efectivo es la propia tasa aplicada (spread + shift).
                _spread_efectivo = tasa_mkt
            elif curva_nombre in sendas and dias_residual > 0:
                # Fallback: curva genérica (para títulos sin PIP)
                tasa_mkt = obtener_tasa_senda(
                    sendas[curva_nombre], mes, dias_residual)
                _spread_efectivo = tasa_mkt
            else:
                tasa_mkt = titulo.tasa_mercado
                _spread_efectivo = titulo.spread_mercado

            # Registrar el spread efectivo del mes (el del último mes queda como cierre).
            titulo._spread_efectivo_cierre = _spread_efectivo

            # RE-VALORAR con la fórmula BVC real
            vm_nuevo = valorar_titulo(
                titulo=titulo,
                tasa_mercado_nueva=tasa_mkt,
                fecha_valuacion=fecha_mes,
                valor_uvr=_senda_uvr[mes],
                trm=_senda_trm[mes],
                serie_ipc=serie_ipc,
                senda_inflacion=senda_inflacion,
                fecha_inicio_senda=fecha_inicio,
            )

            # Valoración TIR (con tasa de compra constante)
            # Para IPC al vencimiento o DV: usar valorar_ipc_vencimiento con valor_tir actual
            if titulo.tipo_bono == 'IPC' and titulo.clasificacion in ('1VC', 'Vencimiento', '1DV', 'Disp. Venta'):
                try:
                    from valorar_bonos import valorar_ipc_vencimiento, _dias_nl365
                    import calendar as _cal
                    valor_inv_base100 = (titulo.valor_tir / titulo.nominal) * 100.0 if titulo.nominal > 0 else 100.0

                    # Obtener IPC proyectado del mes
                    ipc_proy = None
                    if senda_inflacion and len(senda_inflacion) > 0:
                        idx_mes = mes - 1
                        ipc_proy = senda_inflacion[min(idx_mes, len(senda_inflacion) - 1)] / 100.0
                    else:
                        # Usar último IPC de la serie
                        ipc_proy = float(serie_ipc.iloc[-1]["ipc_anual"]) if serie_ipc is not None and not serie_ipc.empty else 0.06

                    # Generar fechas de cupón
                    _meses_per = {'anual': 12, 'semestral': 6, 'trimestral': 3, 'mensual': 1}
                    _mp = _meses_per.get(titulo.frecuencia, 3)
                    _todas_fechas = []
                    _dia_b = titulo.fecha_emision.day
                    _mes_b = titulo.fecha_emision.month
                    _anio_b = titulo.fecha_emision.year
                    _per = 1
                    while True:
                        _tm = _mes_b + _mp * _per
                        _a = _anio_b + (_tm - 1) // 12
                        _mc = (_tm - 1) % 12 + 1
                        _md = _cal.monthrange(_a, _mc)[1]
                        _d = min(_dia_b, _md)
                        _fc = date(_a, _mc, _d)
                        if _fc > titulo.fecha_vencimiento:
                            break
                        _todas_fechas.append(_fc)
                        _per += 1
                    if not _todas_fechas or _todas_fechas[-1] != titulo.fecha_vencimiento:
                        _todas_fechas.append(titulo.fecha_vencimiento)

                    # Generar flujos futuros desde fecha_anterior (inicio del mes)
                    _s = titulo.spread_ipc / 100.0
                    _flujos = []
                    for _i, _fc in enumerate(_todas_fechas):
                        if _fc <= fecha_anterior:
                            continue
                        _fip = _todas_fechas[_i - 1] if _i > 0 else titulo.fecha_emision

                        # Determinar IPC para este flujo según uso_tasa y senda
                        if titulo.uso_tasa in ("F", "PREVIA"):
                            # PREVIA: IPC vigente al inicio del período
                            _fecha_ipc_ref = _fip
                        else:
                            # ACTUAL: IPC vigente a la fecha del cupón
                            _fecha_ipc_ref = _fc

                        _ipc_flujo = ipc_proy  # Default
                        if senda_inflacion and len(senda_inflacion) > 0:
                            _meses_desde = ((_fecha_ipc_ref.year - fecha_inicio.year) * 12
                                            + _fecha_ipc_ref.month - fecha_inicio.month)
                            if _meses_desde >= 0:
                                _idx_s = min(_meses_desde, len(senda_inflacion) - 1)
                                _ipc_flujo = senda_inflacion[_idx_s] / 100.0
                            else:
                                # Antes de la senda: usar IPC histórico
                                try:
                                    from valorar_bonos import _obtener_ipc_vigente as _oiv
                                    _ipc_flujo = _oiv(_fecha_ipc_ref, serie_ipc)
                                except Exception:
                                    _ipc_flujo = ipc_proy

                        _facial_ea = (1 + _ipc_flujo) * (1 + _s) - 1
                        _dp = _dias_nl365(_fip, _fc)
                        _tp = (1 + _facial_ea) ** (_dp / 365.0) - 1
                        _cupon = 100.0 * _tp
                        _flujo = _cupon + (100.0 if _fc == _todas_fechas[-1] else 0)
                        _flujos.append((_fc, _flujo))

                    if _flujos:
                        # ── OPTIMIZACIÓN (Fix 3, Req 17.1/17.4): los plazos en años
                        # `_dias_nl365(fecha_anterior, _f)/365` NO dependen de la tasa `_ym`
                        # que itera la bisección; eran recalculados 500 veces por flujo
                        # (36M llamadas a `_dias_nl365` ≈ 85% del tiempo del motor en el
                        # profiling). Se precalculan UNA sola vez como pares (monto, t_años)
                        # y la bisección solo evalúa la potencia. Resultado NUMÉRICAMENTE
                        # IDÉNTICO (mismas fechas, mismos montos, misma fórmula); solo se
                        # elimina el recálculo repetido. (verificado con el arnés de
                        # equivalencia tools/equivalencia_motor_rf.py)
                        _flujos_ini_t = [(_m, _dias_nl365(fecha_anterior, _f) / 365.0)
                                         for _f, _m in _flujos]

                        # Paso 1: Resolver TIR al inicio del mes
                        # OPTIMIZACIÓN #4a: bisección sobre [-0.05, 3.0] (ancho 3.05);
                        # con 60 iteraciones el ancho es 3.05/2^60 ≈ 2.6e-18 << tol 1e-6
                        # (y por debajo de la resolución del float64), así que el float
                        # resultante es idéntico al de 500 iteraciones.
                        _yl, _yh = -0.05, 3.0
                        for _ in range(60):
                            _ym = (_yl + _yh) / 2
                            _vp = sum(_m / (1 + _ym) ** _t for _m, _t in _flujos_ini_t)
                            if _vp > valor_inv_base100:
                                _yl = _ym
                            else:
                                _yh = _ym
                        _tir = _ym

                        # Paso 2: VP al fin del mes con misma TIR (días precalculados).
                        _flujos_fin_t = [(_m, _dias_nl365(fecha_mes, _f) / 365.0)
                                         for _f, _m in _flujos if _f > fecha_mes]
                        _vp_fin = sum(_m / (1 + _tir) ** _t for _m, _t in _flujos_fin_t) if _flujos_fin_t else 0

                        vtir_nuevo = titulo.nominal * _vp_fin / 100.0
                        titulo._tir_descuento = _tir * 100.0  # Guardar TIR en %
                    else:
                        vtir_nuevo = titulo.valor_tir
                except Exception:
                    vtir_nuevo = titulo.valor_tir  # Fallback: mantener valor anterior
            else:
                vtir_nuevo = valorar_titulo(
                    titulo=titulo,
                    tasa_mercado_nueva=titulo.tasa_compra,
                    fecha_valuacion=fecha_mes,
                    valor_uvr=_senda_uvr[mes],
                    trm=_senda_trm[mes],
                    serie_ipc=serie_ipc,
                    senda_inflacion=senda_inflacion,
                    fecha_inicio_senda=fecha_inicio,
                )

            # Actualizar el título
            titulo.valor_mercado = vm_nuevo
            titulo.valor_tir = vtir_nuevo
            titulo.tasa_mercado = tasa_mkt

            vm_total += vm_nuevo
            vtir_total += vtir_nuevo

        # ── 4. Calcular P&L ──
        # Snapshot mensual por título (para RF_Mensual contable)
        for titulo in portafolio_activo:
            if titulo.fecha_vencimiento <= fecha_mes:
                continue
            tid = titulo.id_posicion
            if tid not in snapshots_por_titulo:
                # Reinversión creada en este mes: snapshot inicial = vm_compra
                snapshots_por_titulo[tid] = {
                    mes - 1: {'vm': getattr(titulo, 'vm_compra', titulo.valor_mercado),
                              'vtir': getattr(titulo, 'vm_compra', titulo.valor_tir),
                              'cupones': 0.0}
                }
            cup_mes = cupones_por_titulo_mes.get(tid, {}).get(mes, 0.0)
            snapshots_por_titulo[tid][mes] = {
                'vm': titulo.valor_mercado,
                'vtir': titulo.valor_tir,
                'cupones': cup_mes,
            }

        # P&L a mercado y a TIR. Se suman el ORI reciclado de ventas DV (Req 8) y la
        # corrección por cupón realizado no reinvertido (Req 11). Ambos términos son
        # 0.0 cuando no hay input de reservas, de modo que el P&L es idéntico al actual
        # (retrocompatibilidad, Req 11.1/11.5).
        pnl_mercado_mes = (vm_total - vm_anterior) + ori_reciclado_mes + cupones_no_reinvertidos_mes
        pnl_tir_mes = (vtir_total - vtir_anterior) + ori_reciclado_mes + cupones_no_reinvertidos_mes
        pnl_mercado_acum += pnl_mercado_mes
        pnl_tir_acum += pnl_tir_mes

        resultado = ResultadoMes(
            mes=mes,
            fecha=fecha_etiqueta_mes,
            vm_total=vm_total,
            vtir_total=vtir_total,
            pnl_mercado_mes=pnl_mercado_mes,
            pnl_tir_mes=pnl_tir_mes,
            pnl_mercado_acum=pnl_mercado_acum,
            pnl_tir_acum=pnl_tir_acum,
            n_titulos=len([t for t in portafolio_activo
                          if t.fecha_vencimiento > fecha_mes]),
            n_reinversiones_mes=len(reinversiones_mes),
            cupones_cobrados_mes=cupones_total,
            reinversiones=reinversiones_mes,
        )
        resultados.append(resultado)

        vm_anterior = vm_total
        vtir_anterior = vtir_total

    # Agregar reinversiones a la lista original para que el caller las vea
    reinversiones_totales = [t for t in portafolio_activo if hasattr(t, 'es_reinversion') and t.es_reinversion]
    portafolio.extend(reinversiones_totales)

    # Guardar cupones cobrados en cada título para cálculo de P&L
    for t in portafolio:
        t._cupones_cobrados = cupones_por_titulo.get(t.id_posicion, 0.0)

    # Guardar snapshots mensuales para P&L contable mensual (RF_Mensual)
    for t in portafolio:
        t._snapshots_mensuales = snapshots_por_titulo.get(t.id_posicion, {})

    # Spread efectivo de cierre por título (Opción A, encadenamiento por tramos). Los
    # títulos que no pasaron por la revaloración (p. ej. vencidos antes del último mes)
    # conservan su spread_mercado original como fallback.
    for t in portafolio:
        if not hasattr(t, '_spread_efectivo_cierre'):
            t._spread_efectivo_cierre = t.spread_mercado

    return resultados


# ==============================================================================
# FUNCIÓN 6: TABLA DE FLUJOS FUTUROS (AGRUPADOS POR ESPECIE)
# ==============================================================================

def generar_tabla_flujos_futuros(
    portafolio: list[Titulo],
    fecha_inicio: date,
    meses_horizonte: int,
    serie_ipc: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Genera tabla de flujos futuros AGRUPADOS POR ESPECIE.

    Cada fila es una especie con el total de cupones que genera en el horizonte,
    su nominal total, y los montos por mes.

    Args:
        portafolio: Lista de títulos.
        fecha_inicio: Fecha de valoración.
        meses_horizonte: Horizonte de proyección.
        serie_ipc: Serie de inflación para cupones IPC.

    Returns:
        DataFrame con: Especie, Tipo_Bono, Asset_Class, Nominal_Total,
                       N_Posiciones, Total_Cupones, Compania, Ramo, Clasificacion
    """
    fecha_fin = fecha_inicio + relativedelta(months=meses_horizonte)
    cupones = detectar_cupones(portafolio, fecha_inicio, fecha_fin, serie_ipc)

    if not cupones:
        return pd.DataFrame()

    # Detalle individual de cupones
    filas = []
    for c in cupones:
        filas.append({
            "Especie": c.titulo.especie,
            "Fecha_Pago": c.fecha_pago,
            "Monto_Cupon": c.monto_cupon,
            "Moneda": c.titulo.moneda,
            "Compania": c.titulo.compania,
            "Ramo": c.titulo.ramo,
            "Clasificacion": c.titulo.clasificacion,
            "Asset_Class": c.titulo.asset_class,
            "Tipo_Bono": c.titulo.tipo_bono,
            "ID_Posicion": c.titulo.id_posicion,
            "Nominal": c.titulo.nominal,
        })

    df_detalle = pd.DataFrame(filas)

    # Agrupar por especie
    df_especie = df_detalle.groupby("Especie").agg(
        Tipo_Bono=("Tipo_Bono", "first"),
        Asset_Class=("Asset_Class", "first"),
        Compania=("Compania", "first"),
        Ramo=("Ramo", "first"),
        Clasificacion=("Clasificacion", "first"),
        Moneda=("Moneda", "first"),
        Nominal_Total=("Nominal", "sum"),
        N_Posiciones=("ID_Posicion", "nunique"),
        N_Cupones=("Monto_Cupon", "count"),
        Total_Cupones=("Monto_Cupon", "sum"),
    ).reset_index().sort_values("Total_Cupones", ascending=False)

    return df_especie


def generar_tabla_flujos_detalle(
    portafolio: list[Titulo],
    fecha_inicio: date,
    meses_horizonte: int,
    serie_ipc: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Genera tabla de flujos futuros con DETALLE por fecha de pago y especie.
    Útil para ver la distribución temporal exacta.

    Returns:
        DataFrame con: Especie, Fecha_Pago, Monto_Cupon, Moneda, etc.
    """
    fecha_fin = fecha_inicio + relativedelta(months=meses_horizonte)
    cupones = detectar_cupones(portafolio, fecha_inicio, fecha_fin, serie_ipc)

    filas = []
    for c in cupones:
        filas.append({
            "Especie": c.titulo.especie,
            "Fecha_Pago": c.fecha_pago,
            "Monto_Cupon": c.monto_cupon,
            "Monto_Total": c.monto_total,
            "Moneda": c.titulo.moneda,
            "Compania": c.titulo.compania,
            "Ramo": c.titulo.ramo,
            "Clasificacion": c.titulo.clasificacion,
            "Asset_Class": c.titulo.asset_class,
            "ID_Posicion": c.titulo.id_posicion,
            "Nominal": c.titulo.nominal,
        })

    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values(["Fecha_Pago", "Especie"]).reset_index(drop=True)
    return df


# ==============================================================================
# FUNCIÓN 7: RESUMEN POR RAMO (PARA ENCAJE PROYECTADO)
# ==============================================================================

def calcular_posicion_por_ramo(
    portafolio: list[Titulo],
    fecha_corte: date,
) -> pd.DataFrame:
    """
    Calcula la posición del portafolio por ramo a una fecha de corte.

    Returns:
        DataFrame con: Compania, Ramo, VM_Total, N_Titulos
    """
    filas = []
    for t in portafolio:
        if t.fecha_vencimiento > fecha_corte:
            filas.append({
                "Compania": t.compania,
                "Ramo": t.ramo,
                "Valor_Mercado": t.valor_mercado,
                "Clasificacion": t.clasificacion,
            })

    df = pd.DataFrame(filas)
    if df.empty:
        return pd.DataFrame(columns=["Compania", "Ramo", "VM_Total", "N_Titulos"])

    return df.groupby(["Compania", "Ramo"]).agg(
        VM_Total=("Valor_Mercado", "sum"),
        N_Titulos=("Valor_Mercado", "count"),
    ).reset_index()


# ==============================================================================
# EJEMPLO DE USO
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AXIS — Motor de Proyección v2 (con re-valoración real)")
    print("=" * 60)

    # Cargar portafolio desde portafolio_clasificado
    portafolio = cargar_portafolio_proyeccion()
    print(f"\nPortafolio cargado: {len(portafolio)} títulos de renta fija")

    # Resumen por tipo de bono
    tipos = {}
    for t in portafolio:
        tipos[t.tipo_bono] = tipos.get(t.tipo_bono, 0) + 1
    for tipo, n in sorted(tipos.items()):
        print(f"  {tipo}: {n} títulos")

    # Generar tabla de flujos
    fecha_val = date(2026, 5, 22)
    df_flujos = generar_tabla_flujos_futuros(portafolio, fecha_val, 12)
    print(f"\nFlujos futuros (12 meses): {len(df_flujos)} cupones")
    if not df_flujos.empty:
        total_flujos = df_flujos["Monto_Total"].sum()
        print(f"Monto total cupones: ${total_flujos/1e9:,.1f} MM")

    print("\n✓ Motor de proyección v2 operativo")
