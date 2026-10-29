"""
Módulo de valoración de bonos de renta fija — Mercado Colombiano.

Usa la fórmula BVC/Deceval:
    TF y UVR: descuento con tasa efectiva anual, base NL/365 (No Leap).
        P = Σ [Flujo_i / (1 + y)^(días_NL365_i / 365)]
    IPC: descuento con tasa compuesta, base 30/360.
        P = Σ [Flujo_i / (1 + y)^(días_i / 360)]

Soporta bonos Tasa Fija (TF), UVR e IPC.
"""

import calendar
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pandas as pd

# La raíz del proyecto (padre de scripts/) debe estar en el path para importar `comun`.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from comun.fuentes_mercado import sexto_dia_habil, mes_ipc_vigente


# ==============================================================================
# FUNCIÓN AUXILIAR: GENERAR FECHAS DE CUPÓN
# ==============================================================================

def _generar_fechas_cupon(
    fecha_emision: date,
    fecha_vencimiento: date,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "anual",
) -> list[date]:
    """
    Genera la lista de fechas de pago de cupón retrocediendo desde el vencimiento.

    Convención estándar: las fechas de cupón se determinan por el ciclo del
    vencimiento hacia atrás en saltos de la periodicidad, deteniéndose cuando
    la fecha generada sea anterior o igual a la fecha de emisión.

    Args:
        fecha_emision: Fecha de emisión del bono.
        fecha_vencimiento: Fecha de vencimiento del bono.
        frecuencia: Periodicidad de pago.

    Returns:
        Lista ordenada de fechas de cupón (de más antigua a más reciente).

    Nota de rendimiento: el cálculo real se delega a `_generar_fechas_cupon_cache`,
    memoizado con `lru_cache` por `(fecha_emision, fecha_vencimiento, frecuencia)`. Esas
    fechas son DETERMINISTAS (solo dependen de esos tres datos) y se recalculaban en cada
    valoración de cada título en cada mes — el perfilado mostró ~1.6M llamadas a
    `calendar.monthrange` dominando el tiempo del motor. El caché NO cambia ningún
    resultado (mismas fechas → mismos flujos → mismo VM); solo evita recomputar lo idéntico.
    Se devuelve una LISTA NUEVA (copia de la tupla cacheada) para que los llamadores que
    conservan la referencia no puedan mutar el valor memoizado por accidente.
    """
    return list(_generar_fechas_cupon_cache(fecha_emision, fecha_vencimiento, frecuencia))


@lru_cache(maxsize=None)
def _generar_fechas_cupon_cache(
    fecha_emision: date,
    fecha_vencimiento: date,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "anual",
) -> tuple:
    """Versión pura memoizada de `_generar_fechas_cupon`. Devuelve una tupla inmutable
    (apta para `lru_cache`). La lógica es IDÉNTICA a la original; solo se cachea."""
    meses_por_periodo = {
        "anual": 12,
        "semestral": 6,
        "trimestral": 3,
        "mensual": 1,
    }
    meses = meses_por_periodo[frecuencia]

    dia_base = fecha_vencimiento.day
    mes_base = fecha_vencimiento.month
    anio_base = fecha_vencimiento.year

    cupones = []
    periodo = 0
    while True:
        total_meses = mes_base - meses * periodo
        anio = anio_base + (total_meses - 1) // 12
        mes = (total_meses - 1) % 12 + 1

        max_dia = calendar.monthrange(anio, mes)[1]
        dia = min(dia_base, max_dia)

        fecha_cupon = date(anio, mes, dia)

        if fecha_cupon <= fecha_emision:
            break

        cupones.append(fecha_cupon)
        periodo += 1

    cupones.sort()

    # Asegurar que el vencimiento esté como último pago
    if not cupones or cupones[-1] != fecha_vencimiento:
        cupones.append(fecha_vencimiento)

    return tuple(cupones)


# ==============================================================================
# FUNCIÓN AUXILIAR: DÍAS NL/365 (NO LEAP)
# ==============================================================================

def _dias_nl365(fecha_inicio: date, fecha_fin: date) -> int:
    """
    Calcula días entre dos fechas ignorando los 29 de febrero (No Leap / 365).

    Es la convención estándar de la BVC para TES colombianos.
    Un año siempre tiene 365 días independientemente de si es bisiesto.

    Args:
        fecha_inicio: Fecha de inicio.
        fecha_fin: Fecha de fin.

    Returns:
        Número de días sin contar los 29 de febrero en el rango.
    """
    dias = (fecha_fin - fecha_inicio).days
    for yr in range(fecha_inicio.year, fecha_fin.year + 1):
        try:
            feb29 = date(yr, 2, 29)
            if fecha_inicio < feb29 <= fecha_fin:
                dias -= 1
        except ValueError:
            pass  # no es bisiesto
    return dias


# ==============================================================================
# FUNCIÓN PRINCIPAL: VALORAR BONO TASA FIJA (TF)
# ==============================================================================

def valorar_bono_tf(
    tasa_cupon: float,
    yield_mercado: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    valor_nominal: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "anual",
    fecha_valuacion: date | None = None,
) -> dict:
    """
    Valora un bono bullet de tasa fija con la fórmula BVC colombiana.

    Fórmula: P_sucio = Σ [Flujo_i / (1 + y)^(días_NL365_i / 365)]
    Base de descuento: NL/365 (No Leap — ignora 29 de febrero).

    Args:
        tasa_cupon: Tasa cupón anual como porcentaje (ej. 7.0 = 7%).
        yield_mercado: Yield / T.Dcto como porcentaje (ej. 14.65 = 14.65%).
        fecha_emision: Fecha de emisión del bono.
        fecha_vencimiento: Fecha de vencimiento del bono.
        valor_nominal: Valor nominal del bono (default 100).
        frecuencia: Frecuencia de pago del cupón.
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.

    Returns:
        Diccionario con precio sucio, precio limpio, intereses acumulados,
        duración modificada y convexidad.
    """
    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    y = yield_mercado / 100.0
    cupon = tasa_cupon / 100.0

    # Pago por período según frecuencia
    periodos_por_anio = {"anual": 1, "semestral": 2, "trimestral": 4, "mensual": 12}
    pago_cupon = (cupon / periodos_por_anio[frecuencia]) * valor_nominal

    # Generar fechas de cupón futuras
    todas_las_fechas = _generar_fechas_cupon(fecha_emision, fecha_vencimiento, frecuencia)
    fechas_futuras = [f for f in todas_las_fechas if f > fecha_valuacion]

    if not fechas_futuras:
        return {
            "precio_sucio": valor_nominal,
            "precio_limpio": valor_nominal,
            "intereses_acumulados": 0.0,
            "duracion_modificada": 0.0,
            "convexidad": 0.0,
            "yield_mercado_pct": yield_mercado,
            "tasa_cupon_pct": tasa_cupon,
        }

    # Calcular precio sucio: descontar cada flujo
    precio_sucio = 0.0
    sum_t_pv = 0.0      # para duración
    sum_t2_pv = 0.0     # para convexidad

    for fc in fechas_futuras:
        dias = _dias_nl365(fecha_valuacion, fc)
        t = dias / 365.0
        factor = (1 + y) ** t

        flujo = pago_cupon
        if fc == fechas_futuras[-1]:
            flujo += valor_nominal  # último cupón + principal

        vp = flujo / factor
        precio_sucio += vp
        sum_t_pv += t * vp
        sum_t2_pv += t * (t + 1 / 365.0) * vp

    # Intereses acumulados (NL/365)
    # Buscar la fecha del último cupón pagado (anterior a fecha_valuacion)
    fechas_pasadas = [f for f in todas_las_fechas if f <= fecha_valuacion]
    if fechas_pasadas:
        ultimo_cupon_pagado = fechas_pasadas[-1]
    else:
        ultimo_cupon_pagado = fecha_emision

    dias_accrued = _dias_nl365(ultimo_cupon_pagado, fecha_valuacion)
    # Días del período completo (para prorratear el cupón)
    idx_siguiente = todas_las_fechas.index(fechas_futuras[0])
    if idx_siguiente > 0:
        dias_periodo = _dias_nl365(todas_las_fechas[idx_siguiente - 1], todas_las_fechas[idx_siguiente])
    else:
        dias_periodo = _dias_nl365(fecha_emision, fechas_futuras[0])

    intereses_acumulados = pago_cupon * dias_accrued / dias_periodo

    precio_limpio = precio_sucio - intereses_acumulados

    # Duración modificada: Macaulay / (1 + y)
    duracion_macaulay = sum_t_pv / precio_sucio if precio_sucio > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + y)

    # Convexidad
    convexidad = sum_t2_pv / (precio_sucio * (1 + y) ** 2) if precio_sucio > 0 else 0.0

    return {
        "precio_sucio": round(precio_sucio, 6),
        "precio_limpio": round(precio_limpio, 6),
        "intereses_acumulados": round(intereses_acumulados, 6),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "convexidad": round(convexidad, 6),
        "yield_mercado_pct": yield_mercado,
        "tasa_cupon_pct": tasa_cupon,
        "valor_nominal": valor_nominal,
        "dias_al_vencimiento": _dias_nl365(fecha_valuacion, fecha_vencimiento),
        "cupones_pendientes": len(fechas_futuras),
    }


# ==============================================================================
# FUNCIÓN 1B: VALORAR CDT / DEUDA CORPORATIVA (BASE 30/360, TASA NOMINAL)
# ==============================================================================

def valorar_bono_corporativo(
    tasa_nominal: float,
    yield_mercado: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    valor_nominal: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "trimestral",
    fecha_valuacion: date | None = None,
    tipo_tasa: Literal["NOMINAL", "EFECTIVO"] = "NOMINAL",
) -> dict:
    """
    Valora un CDT o bono corporativo con convención 30/360.

    Fórmula BVC para corporativos:
        P_sucio = Σ [Flujo_i / (1 + y)^(días_30_360_i / 360)]

    donde y = yield efectiva anual (T.Dcto del 597) y los días se calculan
    con la convención 30/360.

    El cupón periódico depende de tipo_tasa:
        - NOMINAL: cupón = (tasa / n_periodos) * Nominal
        - EFECTIVO: cupón = [(1 + tasa)^(dias_periodo/360) - 1] * Nominal

    Args:
        tasa_nominal: Tasa cupón anual como porcentaje (ej. 9.692).
        yield_mercado: Yield / T.Dcto como porcentaje EA (ej. 13.94).
        fecha_emision: Fecha de emisión.
        fecha_vencimiento: Fecha de vencimiento.
        valor_nominal: Valor nominal del título (default 100).
        frecuencia: Frecuencia de pago del cupón.
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.
        tipo_tasa: "NOMINAL" o "EFECTIVO" — determina cómo calcular el cupón.

    Returns:
        Diccionario con precio sucio, precio limpio, intereses acumulados,
        duración modificada y convexidad.
    """
    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    y = yield_mercado / 100.0
    tasa = tasa_nominal / 100.0

    # Pago por período según tipo de tasa
    periodos_por_anio = {"anual": 1, "semestral": 2, "trimestral": 4, "mensual": 12}
    n_periodos = periodos_por_anio[frecuencia]
    dias_periodo_base = 360 // n_periodos  # 90 para trimestral

    if tipo_tasa == "NOMINAL":
        pago_cupon = (tasa / n_periodos) * valor_nominal
    else:
        # EFECTIVO: cupón = [(1 + tasa_EA)^(dias_periodo/360) - 1] * Nominal
        pago_cupon = ((1 + tasa) ** (dias_periodo_base / 360.0) - 1) * valor_nominal

    # Generar fechas de cupón futuras
    todas_las_fechas = _generar_fechas_cupon(fecha_emision, fecha_vencimiento, frecuencia)
    fechas_futuras = [f for f in todas_las_fechas if f > fecha_valuacion]

    if not fechas_futuras:
        return {
            "precio_sucio": valor_nominal,
            "precio_limpio": valor_nominal,
            "intereses_acumulados": 0.0,
            "duracion_modificada": 0.0,
            "convexidad": 0.0,
            "yield_mercado_pct": yield_mercado,
            "tasa_nominal_pct": tasa_nominal,
        }

    # Calcular precio sucio: descontar cada flujo con base 30/360
    precio_sucio = 0.0
    sum_t_pv = 0.0
    sum_t2_pv = 0.0

    for fc in fechas_futuras:
        dias = _dias_30_360(fecha_valuacion, fc)
        t = dias / 360.0
        factor = (1 + y) ** t

        flujo = pago_cupon
        if fc == fechas_futuras[-1]:
            flujo += valor_nominal  # último cupón + principal

        vp = flujo / factor
        precio_sucio += vp
        sum_t_pv += t * vp
        sum_t2_pv += t * (t + 1 / 360.0) * vp

    # Intereses acumulados (30/360)
    fechas_pasadas = [f for f in todas_las_fechas if f <= fecha_valuacion]
    if fechas_pasadas:
        ultimo_cupon_pagado = fechas_pasadas[-1]
    else:
        ultimo_cupon_pagado = fecha_emision

    dias_accrued = _dias_30_360(ultimo_cupon_pagado, fecha_valuacion)
    # Días del período completo
    idx_siguiente = todas_las_fechas.index(fechas_futuras[0])
    if idx_siguiente > 0:
        dias_periodo = _dias_30_360(todas_las_fechas[idx_siguiente - 1], todas_las_fechas[idx_siguiente])
    else:
        dias_periodo = _dias_30_360(fecha_emision, fechas_futuras[0])

    if tipo_tasa == "NOMINAL":
        intereses_acumulados = pago_cupon * dias_accrued / dias_periodo if dias_periodo > 0 else 0.0
    else:
        # EFECTIVO: intereses = Nominal * [(1+tasa)^(dias_accrued/360) - 1]
        intereses_acumulados = valor_nominal * ((1 + tasa) ** (dias_accrued / 360.0) - 1)

    precio_limpio = precio_sucio - intereses_acumulados

    # Duración modificada
    duracion_macaulay = sum_t_pv / precio_sucio if precio_sucio > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + y)

    # Convexidad
    convexidad = sum_t2_pv / (precio_sucio * (1 + y) ** 2) if precio_sucio > 0 else 0.0

    return {
        "precio_sucio": round(precio_sucio, 6),
        "precio_limpio": round(precio_limpio, 6),
        "intereses_acumulados": round(intereses_acumulados, 6),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "convexidad": round(convexidad, 6),
        "yield_mercado_pct": yield_mercado,
        "tasa_nominal_pct": tasa_nominal,
        "valor_nominal": valor_nominal,
        "dias_al_vencimiento": _dias_30_360(fecha_valuacion, fecha_vencimiento),
        "cupones_pendientes": len(fechas_futuras),
    }


# ==============================================================================
# FUNCIÓN 1C: VALORAR YANKEE (USD, 30/360, TASA NOMINAL SV)
# ==============================================================================

def valorar_bono_yankee(
    tasa_nominal: float,
    yield_mercado,
    fecha_emision: date,
    fecha_vencimiento: date,
    valor_nominal_usd: float = 100.0,
    trm: float = 1.0,
    fecha_valuacion: date | None = None,
) -> dict:
    """
    Valora un bono Yankee (emitido en USD) con convención 30/360 y cupón
    semestral nominal. También sirve para valorar patas de swaps con curva.

    El precio se calcula en moneda original y se convierte a COP multiplicando por trm.

    Fórmula:
        P_sucio = Σ [Flujo_i / (1 + y_i)^(días_30_360_i / 360)]
        VM_COP = P_sucio * trm

    Args:
        tasa_nominal: Tasa cupón nominal anual como porcentaje (ej. 5.875).
        yield_mercado: Puede ser:
            - float: Yield único en % EA (ej. 8.81). Se usa para bonos.
            - dict {plazo_dias: tasa_ea}: Curva de tasas EA ya convertidas.
              Cada flujo se descuenta con la tasa de su plazo exacto.
        fecha_emision: Fecha de emisión.
        fecha_vencimiento: Fecha de vencimiento.
        valor_nominal_usd: Valor nominal (default 100 para % de par).
        trm: Factor de conversión a COP (TRM para USD, valor UVR para pata UVR).
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.

    Returns:
        Diccionario con precio sucio (moneda original y COP), duración, etc.
    """
    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    es_curva = isinstance(yield_mercado, dict)
    if not es_curva:
        y = yield_mercado / 100.0
    else:
        y = None  # se determina por flujo

    tasa_nom = tasa_nominal / 100.0

    # Cupón semestral: tasa nominal / 2
    pago_cupon = (tasa_nom / 2) * valor_nominal_usd

    # Generar fechas de cupón semestrales (desde vencimiento hacia atrás)
    todas_las_fechas = _generar_fechas_cupon(fecha_emision, fecha_vencimiento, "semestral")
    fechas_futuras = [f for f in todas_las_fechas if f > fecha_valuacion]

    if not fechas_futuras:
        return {
            "precio_sucio": valor_nominal_usd,
            "precio_sucio_cop": valor_nominal_usd * trm,
            "precio_limpio": valor_nominal_usd,
            "intereses_acumulados": 0.0,
            "duracion_modificada": 0.0,
            "convexidad": 0.0,
            "yield_mercado_pct": yield_mercado,
            "tasa_nominal_pct": tasa_nominal,
        }

    # Calcular precio sucio con base 30/360
    precio_sucio = 0.0
    sum_t_pv = 0.0
    sum_t2_pv = 0.0

    for fc in fechas_futuras:
        dias = _dias_30_360(fecha_valuacion, fc)
        t = dias / 360.0

        # Determinar tasa de descuento
        if es_curva:
            # Buscar tasa en la curva por plazo exacto o más cercano
            if dias in yield_mercado:
                y_flujo = yield_mercado[dias]
            else:
                closest = min(yield_mercado.keys(), key=lambda p: abs(p - dias))
                y_flujo = yield_mercado[closest]
        else:
            y_flujo = y

        factor = (1 + y_flujo) ** t

        flujo = pago_cupon
        if fc == fechas_futuras[-1]:
            flujo += valor_nominal_usd

        vp = flujo / factor
        precio_sucio += vp
        sum_t_pv += t * vp
        sum_t2_pv += t * (t + 1 / 360.0) * vp

    # Intereses acumulados (30/360)
    fechas_pasadas = [f for f in todas_las_fechas if f <= fecha_valuacion]
    if fechas_pasadas:
        ultimo_cupon_pagado = fechas_pasadas[-1]
    else:
        ultimo_cupon_pagado = fecha_emision

    dias_accrued = _dias_30_360(ultimo_cupon_pagado, fecha_valuacion)
    idx_siguiente = todas_las_fechas.index(fechas_futuras[0])
    if idx_siguiente > 0:
        dias_periodo = _dias_30_360(todas_las_fechas[idx_siguiente - 1], todas_las_fechas[idx_siguiente])
    else:
        dias_periodo = _dias_30_360(fecha_emision, fechas_futuras[0])

    intereses_acumulados = pago_cupon * dias_accrued / dias_periodo if dias_periodo > 0 else 0.0

    precio_limpio = precio_sucio - intereses_acumulados

    # Duración y convexidad
    duracion_macaulay = sum_t_pv / precio_sucio if precio_sucio > 0 else 0.0
    if es_curva:
        y_dur = y_flujo  # usar última tasa como aproximación
        duracion_modificada = duracion_macaulay / (1 + y_dur) if y_dur else 0.0
        convexidad = sum_t2_pv / (precio_sucio * (1 + y_dur) ** 2) if precio_sucio > 0 and y_dur else 0.0
    else:
        duracion_modificada = duracion_macaulay / (1 + y)
        convexidad = sum_t2_pv / (precio_sucio * (1 + y) ** 2) if precio_sucio > 0 else 0.0

    return {
        "precio_sucio": round(precio_sucio, 6),
        "precio_sucio_cop": round(precio_sucio * trm, 2),
        "precio_limpio": round(precio_limpio, 6),
        "intereses_acumulados": round(intereses_acumulados, 6),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "convexidad": round(convexidad, 6),
        "yield_mercado_pct": yield_mercado,
        "tasa_nominal_pct": tasa_nominal,
        "valor_nominal_usd": valor_nominal_usd,
        "trm": trm,
        "dias_al_vencimiento": _dias_30_360(fecha_valuacion, fecha_vencimiento),
        "cupones_pendientes": len(fechas_futuras),
    }


# ==============================================================================
# FUNCIÓN 2: VALORAR BONO UVR
# ==============================================================================

def valorar_bono_uvr(
    tasa_cupon_real: float,
    yield_real: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    valor_uvr: float,
    valor_nominal_uvr: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "anual",
    fecha_valuacion: date | None = None,
) -> dict:
    """
    Valora un bono indexado a UVR (TES UVR) con la fórmula BVC colombiana.

    Valora en unidades reales (UVR) y convierte a pesos multiplicando por
    el valor de la UVR del día.

    Args:
        tasa_cupon_real: Tasa cupón real anual como porcentaje (ej. 3.0 = 3%).
        yield_real: Yield real / T.Dcto como porcentaje (ej. 7.10 = 7.10%).
        fecha_emision: Fecha de emisión del bono.
        fecha_vencimiento: Fecha de vencimiento del bono.
        valor_uvr: Valor de la UVR a la fecha de valoración (ej. 411.2813).
        valor_nominal_uvr: Valor nominal en unidades de UVR (default 100).
        frecuencia: Frecuencia de pago del cupón.
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.

    Returns:
        Diccionario con precios en UVR y en pesos, duración y convexidad.
    """
    # Valorar en términos reales (UVR) usando la misma fórmula BVC
    resultado_real = valorar_bono_tf(
        tasa_cupon=tasa_cupon_real,
        yield_mercado=yield_real,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        valor_nominal=valor_nominal_uvr,
        frecuencia=frecuencia,
        fecha_valuacion=fecha_valuacion,
    )

    # Convertir a pesos colombianos
    precio_sucio_cop = resultado_real["precio_sucio"] * valor_uvr
    precio_limpio_cop = resultado_real["precio_limpio"] * valor_uvr
    intereses_cop = resultado_real["intereses_acumulados"] * valor_uvr

    return {
        # En UVR
        "precio_sucio_uvr": resultado_real["precio_sucio"],
        "precio_limpio_uvr": resultado_real["precio_limpio"],
        "intereses_acumulados_uvr": resultado_real["intereses_acumulados"],
        # En pesos colombianos
        "precio_sucio_cop": round(precio_sucio_cop, 2),
        "precio_limpio_cop": round(precio_limpio_cop, 2),
        "intereses_acumulados_cop": round(intereses_cop, 2),
        # Métricas de riesgo
        "duracion_macaulay": resultado_real["duracion_macaulay"],
        "duracion_modificada": resultado_real["duracion_modificada"],
        "convexidad": resultado_real["convexidad"],
        # Inputs de referencia
        "valor_uvr": valor_uvr,
        "tasa_cupon_real_pct": tasa_cupon_real,
        "yield_real_pct": yield_real,
        "valor_nominal_uvr": valor_nominal_uvr,
        "dias_al_vencimiento": resultado_real["dias_al_vencimiento"],
        "cupones_pendientes": resultado_real["cupones_pendientes"],
    }


# ==============================================================================
# FUNCIÓN AUXILIAR: CARGAR SERIE DE INFLACIÓN
# ==============================================================================

def cargar_serie_ipc(ruta: str = "Serie Inflación.xlsx") -> pd.DataFrame:
    """
    Carga la serie histórica de IPC desde el Excel del Banco de la República.

    El Excel tiene columnas [Fecha, Inflación total] donde Fecha es fin de mes
    y la inflación es la variación anual (%) vigente ese mes.

    La inflación de un mes M se publica a inicios del mes M+1, por lo tanto
    es "vigente" durante todo el mes M+1. Ejemplo: IPC de abril (publicado en
    mayo) es vigente para cualquier fecha en mayo.

    Returns:
        DataFrame con columnas ['fecha', 'ipc_anual'] indexado por fecha,
        donde cada fila representa el IPC vigente durante el mes siguiente.
    """
    df = pd.read_excel(ruta, header=0)
    df.columns = ["fecha", "ipc_anual"]
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["ipc_anual"] = df["ipc_anual"] / 100.0  # convertir a decimal
    return df


def _obtener_ipc_vigente(fecha_consulta: date, serie_ipc: pd.DataFrame) -> float:
    """
    Obtiene el IPC anual vigente para una fecha dada, considerando que el DANE
    publica el IPC del mes M-1 el 6to día hábil del mes M.

    Regla:
    - Si fecha_consulta >= 6to día hábil del mes → IPC vigente = mes anterior (M-1)
    - Si fecha_consulta <  6to día hábil del mes → IPC vigente = dos meses antes (M-2)

    La serie del Banrep tiene el IPC con fecha fin de mes (ej. 2026-03-31 = IPC marzo).

    Args:
        fecha_consulta: Fecha para la cual se necesita el IPC vigente.
        serie_ipc: DataFrame con ['fecha', 'ipc_anual'].

    Returns:
        IPC anual como decimal (ej. 0.0584 = 5.84%).
    """
    # Regla del 6to día hábil (canónica en comun.fuentes_mercado)
    anio_ipc, mes_ipc = mes_ipc_vigente(fecha_consulta)

    # Buscar en la serie el IPC de ese mes/año
    serie_ipc["_mes"] = serie_ipc["fecha"].dt.month
    serie_ipc["_anio"] = serie_ipc["fecha"].dt.year
    fila = serie_ipc[(serie_ipc["_anio"] == anio_ipc) & (serie_ipc["_mes"] == mes_ipc)]

    if fila.empty:
        # Fallback: usar el último IPC disponible
        return float(serie_ipc.iloc[-1]["ipc_anual"])

    return float(fila.iloc[0]["ipc_anual"])


def _sexto_dia_habil(anio: int, mes: int) -> date:
    """
    Calcula el 6to día hábil de un mes (excluyendo sábados y domingos).

    Wrapper delgado sobre `comun.fuentes_mercado.sexto_dia_habil` (fuente única de
    la regla). Se conserva el nombre para los usos internos del módulo.
    """
    return sexto_dia_habil(anio, mes)


# ==============================================================================
# FUNCIÓN AUXILIAR: DÍAS BASE 30/360
# ==============================================================================

def _dias_30_360(fecha_inicio: date, fecha_fin: date) -> int:
    """
    Calcula días entre dos fechas con convención 30/360 (Bond Basis).

    Fórmula: 360*(Y2-Y1) + 30*(M2-M1) + (D2-D1)
    Con ajustes: si D1=31 -> D1=30; si D2=31 y D1>=30 -> D2=30.
    """
    d1 = min(fecha_inicio.day, 30)
    d2 = fecha_fin.day
    if d2 == 31 and d1 >= 30:
        d2 = 30

    return (
        360 * (fecha_fin.year - fecha_inicio.year)
        + 30 * (fecha_fin.month - fecha_inicio.month)
        + (d2 - d1)
    )


# ==============================================================================
# FUNCIÓN 3: VALORAR BONO IPC (INDEXADO A INFLACIÓN)
# ==============================================================================

def valorar_bono_ipc(
    spread: float,
    yield_mercado: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    serie_ipc: pd.DataFrame,
    uso_tasa: Literal["F", "I"] = "F",
    valor_nominal: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "trimestral",
    fecha_valuacion: date | None = None,
    base_dias: Literal["30/360", "NL/365"] = "30/360",
    senda_inflacion: list | None = None,
    fecha_inicio_senda: date | None = None,
) -> dict:
    """
    Valora un bono indexado a IPC con la fórmula BVC colombiana.

    Los flujos de cupón se calculan con la inflación real (histórica) para
    cupones ya causados, y con la senda de inflación proyectada (o el último
    IPC conocido como fallback) para cupones futuros.
    El descuento usa base 30/360 con tasa compuesta.

    Fórmula cupón: Nominal × [(1 + IPC)^(dias_cupon/365) × (1 + spread)^(dias_cupon/360) - 1]
    Fórmula descuento: factor = (1 + T.Dcto)^(dias_30_360 / 360)

    Args:
        spread: Spread sobre IPC como porcentaje (ej. 3.64 = 3.64%).
        yield_mercado: T.Dcto compuesta como porcentaje (ej. 13.23 = 13.23%).
        fecha_emision: Fecha de emisión del bono.
        fecha_vencimiento: Fecha de vencimiento del bono.
        serie_ipc: DataFrame de inflación (de cargar_serie_ipc()).
        uso_tasa: "F" (Final/Previa: IPC al inicio del período) o
                  "I" (Inicio/Actual: IPC a la fecha del cupón).
        valor_nominal: Valor nominal del bono (default 100).
        frecuencia: Frecuencia de pago del cupón.
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.
        senda_inflacion: Lista de inflaciones mensuales proyectadas (% EA, ej [5.5, 5.3, ...]).
                         Representa IPC anual vigente para cada mes a partir de fecha_inicio_senda.
                         Si None, usa el último IPC vigente para todos los flujos futuros.
        fecha_inicio_senda: Fecha de inicio de la senda (primer mes). Si None, usa fecha_valuacion.

    Returns:
        Diccionario con precio sucio, precio limpio, intereses acumulados,
        duración y convexidad.
    """
    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    y = yield_mercado / 100.0
    s = spread / 100.0

    periodos_por_anio = {"anual": 1, "semestral": 2, "trimestral": 4, "mensual": 12}
    n_periodos = periodos_por_anio[frecuencia]
    divisor = 365 if base_dias == "NL/365" else 360
    dias_periodo_base = divisor // n_periodos  # 91 para trimestral NL/365, 90 para 30/360

    # Función de días según base
    _dias_fn = _dias_nl365 if base_dias == "NL/365" else _dias_30_360

    # Generar todas las fechas de cupón
    todas_las_fechas = _generar_fechas_cupon(fecha_emision, fecha_vencimiento, frecuencia)
    fechas_futuras = [f for f in todas_las_fechas if f > fecha_valuacion]

    if not fechas_futuras:
        return {
            "precio_sucio": valor_nominal,
            "precio_limpio": valor_nominal,
            "intereses_acumulados": 0.0,
            "duracion_modificada": 0.0,
            "convexidad": 0.0,
            "yield_mercado_pct": yield_mercado,
            "spread_pct": spread,
        }

    # Obtener el último IPC conocido para proyectar flujos futuros
    ultimo_ipc = float(serie_ipc.iloc[-1]["ipc_anual"])

    # Preparar senda de inflación proyectada
    _senda_inf = None
    _senda_inicio = fecha_inicio_senda or fecha_valuacion
    if senda_inflacion and len(senda_inflacion) > 0:
        _senda_inf = [v / 100.0 for v in senda_inflacion]  # Convertir % a decimal

    # Calcular precio sucio
    precio_sucio = 0.0
    sum_t_pv = 0.0
    sum_t2_pv = 0.0
    flujos_detalle = []

    # ──────────────────────────────────────────────────────────────────────────
    # Determinar IPC para los flujos de cupón.
    # Regla:
    #   "F" (Final): IPC vigente a la FECHA DE VALORACIÓN → TODOS los flujos
    #   "I" (Previa):
    #       - Primer flujo: IPC vigente al inicio del período vigente (último cupón pagado)
    #       - Demás flujos (fuera del período proyectado): IPC vigente a fecha de valoración
    # ──────────────────────────────────────────────────────────────────────────

    # Encontrar último cupón pagado (anterior o igual a fecha_valuacion)
    fechas_pasadas_cupon = [f for f in todas_las_fechas if f <= fecha_valuacion]
    ultimo_cupon_pagado_fecha = fechas_pasadas_cupon[-1] if fechas_pasadas_cupon else fecha_emision

    # Última fecha con dato real en la serie histórica
    _ultima_fecha_serie = serie_ipc["fecha"].max()
    if hasattr(_ultima_fecha_serie, 'date'):
        _ultima_fecha_serie = _ultima_fecha_serie.date()

    def _get_ipc_for_date(fecha_ref):
        """
        Obtiene IPC vigente para una fecha, aplicando la regla del 6to día hábil.
        La senda está indexada por mes de MEDICIÓN:
          senda[0] = Corte (medido en jun-26, vigente en jul-26 desde 6to hábil)
          senda[1] = jul-26 medido, vigente desde 6to hábil ago-26
          etc.
        Para encontrar el IPC vigente en una fecha:
          - Si fecha >= 6to hábil del mes → IPC medido el mes anterior (M-1)
          - Si fecha <  6to hábil del mes → IPC medido dos meses antes (M-2)
        En la senda: buscar el índice del mes de MEDICIÓN relativo al inicio.
        """
        if _senda_inf and fecha_ref > _ultima_fecha_serie:
            # Regla del 6to día hábil (canónica en comun.fuentes_mercado):
            # (anio_medicion, mes_medicion) = mes cuyo IPC está vigente en fecha_ref.
            anio_medicion, mes_medicion = mes_ipc_vigente(fecha_ref)

            # Índice en la senda (mes de medición relativo al inicio de la senda)
            # La senda[0] = "Corte" = IPC medido el mes ANTERIOR al inicio del escenario
            # Entonces el mes de medición del primer punto = _senda_inicio.month - 1
            _mes_inicio_medicion = _senda_inicio.month - 1
            _anio_inicio_medicion = _senda_inicio.year
            if _mes_inicio_medicion == 0:
                _mes_inicio_medicion = 12
                _anio_inicio_medicion -= 1
            meses_d = ((anio_medicion - _anio_inicio_medicion) * 12
                       + mes_medicion - _mes_inicio_medicion)
            idx_s = max(0, meses_d)
            return _senda_inf[min(idx_s, len(_senda_inf) - 1)]
        else:
            try:
                return _obtener_ipc_vigente(fecha_ref, serie_ipc)
            except (ValueError, IndexError):
                return float(serie_ipc.iloc[-1]["ipc_anual"]) if not serie_ipc.empty else 0.06

    # IPC vigente a la fecha de valoración (usado para "F" siempre, y "I" flujos 2+)
    ipc_fecha_valoracion = _get_ipc_for_date(fecha_valuacion)

    # IPC del primer flujo para "I" (Previa): vigente al inicio del período vigente
    ipc_primer_flujo = _get_ipc_for_date(ultimo_cupon_pagado_fecha)

    for i_flujo, fc in enumerate(fechas_futuras):
        # Días para descuento
        dias_dcto = _dias_fn(fecha_valuacion, fc)
        t = dias_dcto / float(divisor)
        factor_dcto = (1 + y) ** t

        # Fecha inicio del período (para calcular días del cupón)
        idx_fc = todas_las_fechas.index(fc)
        if idx_fc > 0:
            fecha_inicio_periodo = todas_las_fechas[idx_fc - 1]
        else:
            fecha_inicio_periodo = fecha_emision

        # Determinar IPC para este flujo
        if uso_tasa == "F":
            # Final: IPC vigente a fecha de valoración para TODOS los flujos
            ipc_periodo = ipc_fecha_valoracion
        else:
            # Previa/Inicio:
            # - Primer flujo: IPC vigente al inicio del período (último cupón pagado)
            # - Demás flujos: IPC vigente a la fecha de valoración (último proyectado)
            if i_flujo == 0:
                ipc_periodo = ipc_primer_flujo
            else:
                ipc_periodo = ipc_fecha_valoracion

        # Cupón del período:
        # facial_EA = (1 + IPC) * (1 + spread) - 1 (composición multiplicativa)
        # cupón = Nominal * [(1 + facial_EA)^(dias_cupon / divisor) - 1]
        facial_ea = (1 + ipc_periodo) * (1 + s) - 1
        if base_dias == "NL/365":
            dias_cupon_real = _dias_nl365(fecha_inicio_periodo, fc)
            tasa_cupon_periodo = (1 + facial_ea) ** (dias_cupon_real / 365.0) - 1
        else:
            tasa_cupon_periodo = (1 + facial_ea) ** (dias_periodo_base / 360.0) - 1

        flujo = valor_nominal * tasa_cupon_periodo
        if fc == fechas_futuras[-1]:
            flujo += valor_nominal  # último cupón + devolución del principal

        vp = flujo / factor_dcto
        precio_sucio += vp
        sum_t_pv += t * vp
        sum_t2_pv += t * (t + 1 / float(divisor)) * vp

        flujos_detalle.append({
            "fecha": fc,
            "ipc_usado": round(ipc_periodo * 100, 4),
            "tasa_cupon_periodo": round(tasa_cupon_periodo * 100, 4),
            "flujo": round(flujo, 6),
            "dias_dcto_30_360": dias_dcto,
            "vp": round(vp, 6),
        })

    # Intereses acumulados
    # Período actual: desde último cupón pagado hasta fecha de valuación
    fechas_pasadas = [f for f in todas_las_fechas if f <= fecha_valuacion]
    if fechas_pasadas:
        ultimo_cupon_pagado = fechas_pasadas[-1]
    else:
        ultimo_cupon_pagado = fecha_emision

    dias_accrued = _dias_fn(ultimo_cupon_pagado, fecha_valuacion)

    # IPC para el período en curso (intereses acumulados)
    # Para F (Final): el IPC se conocerá al final del período (fecha próximo cupón)
    # Para accrued, usamos el último IPC conocido a la fecha de valoración
    try:
        if uso_tasa == "F":
            ipc_actual = _obtener_ipc_vigente(fecha_valuacion, serie_ipc)
        else:
            ipc_actual = _obtener_ipc_vigente(ultimo_cupon_pagado, serie_ipc)
    except ValueError:
        ipc_actual = ultimo_ipc

    dias_periodo_actual = (fechas_futuras[0] - ultimo_cupon_pagado).days
    facial_ea_accrued = (1 + ipc_actual) * (1 + s) - 1
    if base_dias == "NL/365":
        dias_accrued_real = _dias_nl365(ultimo_cupon_pagado, fecha_valuacion)
        tasa_cupon_accrued = (1 + facial_ea_accrued) ** (dias_accrued_real / 365.0) - 1
    else:
        tasa_cupon_accrued = (1 + facial_ea_accrued) ** (dias_accrued / float(divisor)) - 1
    intereses_acumulados = valor_nominal * tasa_cupon_accrued

    precio_limpio = precio_sucio - intereses_acumulados

    # Duración y convexidad
    duracion_macaulay = sum_t_pv / precio_sucio if precio_sucio > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + y)
    convexidad = sum_t2_pv / (precio_sucio * (1 + y) ** 2) if precio_sucio > 0 else 0.0

    return {
        "precio_sucio": round(precio_sucio, 6),
        "precio_limpio": round(precio_limpio, 6),
        "intereses_acumulados": round(intereses_acumulados, 6),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "convexidad": round(convexidad, 6),
        "yield_mercado_pct": yield_mercado,
        "spread_pct": spread,
        "valor_nominal": valor_nominal,
        "dias_al_vencimiento": _dias_fn(fecha_valuacion, fecha_vencimiento),
        "cupones_pendientes": len(fechas_futuras),
        "ipc_ultimo_conocido_pct": round(ultimo_ipc * 100, 4),
        "flujos": flujos_detalle,
    }


# ==============================================================================
# FUNCIÓN 3C: VALORAR IPC AL VENCIMIENTO (MÉTODO QES-TIR DE PORFIN)
# ==============================================================================

def valorar_ipc_vencimiento(
    spread: float,
    valor_inversion: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    serie_ipc: pd.DataFrame,
    uso_tasa: Literal["F", "I"] = "I",
    valor_nominal: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "trimestral",
    fecha_valuacion: date | None = None,
    senda_inflacion: list | None = None,
    fecha_inicio_senda: date | None = None,
) -> dict:
    """
    Valora un bono IPC al vencimiento con el método QES-TIR de PORFIN.

    Proceso:
    1. Genera todos los flujos de cupón desde emisión hasta vencimiento.
    2. Calcula cada cupón con el IPC real histórico (para períodos pasados)
       o con la inflación proyectada de la senda EE (para períodos futuros).
    3. Resuelve la TIR implícita por bisección: VP(flujos futuros) = valor_inversion.
    4. Descuenta flujos futuros con esa TIR, base NL/365.

    La TIR se recalcula cada vez que el IPC cambia (sexto día hábil del mes).

    Args:
        spread: Spread sobre IPC como porcentaje (ej. 6.5).
        valor_inversion: Valor TIR actual (VPN recibido como parámetro del 597).
        fecha_emision: Fecha de emisión del bono.
        fecha_vencimiento: Fecha de vencimiento del bono.
        serie_ipc: DataFrame de inflación (de cargar_serie_ipc()).
        uso_tasa: "I" (Inicio) o "F" (Final).
        valor_nominal: Valor nominal base (100 para cálculo en %).
        frecuencia: Frecuencia de pago del cupón.
        fecha_valuacion: Fecha de valoración.
        senda_inflacion: Lista de inflaciones mensuales proyectadas (% EA, ej [5.5, 5.3, ...]).
                         Si None, usa el último IPC vigente para períodos futuros.
        fecha_inicio_senda: Fecha de inicio de la senda (primer mes). Si None, usa fecha_valuacion.

    Returns:
        Dict con TIR recalculada, VPN, duración, flujos detallados.
    """
    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    s = spread / 100.0

    # Generar fechas de cupón desde emisión hacia adelante (PORFIN usa emisión + período)
    meses_por_periodo = {"anual": 12, "semestral": 6, "trimestral": 3, "mensual": 1}
    meses = meses_por_periodo[frecuencia]

    import calendar
    todas_las_fechas = []
    dia_base = fecha_emision.day
    mes_base = fecha_emision.month
    anio_base = fecha_emision.year
    periodo = 1
    while True:
        total_meses = mes_base + meses * periodo
        anio = anio_base + (total_meses - 1) // 12
        mes_calc = (total_meses - 1) % 12 + 1
        max_dia = calendar.monthrange(anio, mes_calc)[1]
        dia = min(dia_base, max_dia)
        fc = date(anio, mes_calc, dia)
        if fc > fecha_vencimiento:
            break
        todas_las_fechas.append(fc)
        periodo += 1
    # Agregar fecha de vencimiento como redención si no coincide
    if not todas_las_fechas or todas_las_fechas[-1] != fecha_vencimiento:
        todas_las_fechas.append(fecha_vencimiento)

    # Obtener último IPC vigente a la fecha de valoración
    ipc_vigente = float(serie_ipc.iloc[-1]["ipc_anual"])
    try:
        ipc_vigente = _obtener_ipc_vigente(fecha_valuacion, serie_ipc)
    except (ValueError, IndexError):
        pass

    # Preparar senda de inflación proyectada para períodos futuros
    # senda_inflacion es una lista de IPC EA % mensuales [5.5, 5.3, 5.1, ...]
    # fecha_inicio_senda es el primer día del primer mes de la senda
    _senda_inf = None
    _senda_inicio = fecha_inicio_senda or fecha_valuacion
    if senda_inflacion and len(senda_inflacion) > 0:
        _senda_inf = [v / 100.0 for v in senda_inflacion]  # Convertir % a decimal

    # Calcular flujos de cupón
    flujos = []  # (fecha, monto) para cada cupón futuro + redención
    # Si el vencimiento fue agregado como fecha extra (no coincide con ciclo de cupones),
    # agregarlo solo como redención de principal
    vencimiento_es_extra = (todas_las_fechas[-1] == fecha_vencimiento and
                            len(todas_las_fechas) >= 2 and
                            todas_las_fechas[-2] != fecha_vencimiento)
    # Verificar si la penúltima fecha + período llegaría al vencimiento
    # Si sí, entonces el vencimiento NO es extra (es un cupón regular que coincide)
    if len(todas_las_fechas) >= 2:
        # El vencimiento fue generado por el while loop, no es extra
        vencimiento_es_extra = False

    for i, fc in enumerate(todas_las_fechas):
        # Determinar IPC para este período
        if i == 0:
            fecha_inicio_periodo = fecha_emision
        else:
            fecha_inicio_periodo = todas_las_fechas[i - 1]

        # Obtener IPC según uso_tasa
        if uso_tasa == "I":
            # Inicio: IPC vigente al inicio del período
            try:
                if fecha_inicio_periodo <= fecha_valuacion:
                    ipc_periodo = _obtener_ipc_vigente(fecha_inicio_periodo, serie_ipc)
                else:
                    # Período futuro: usar senda proyectada si disponible
                    if _senda_inf:
                        meses_desde_inicio = ((fecha_inicio_periodo.year - _senda_inicio.year) * 12
                                              + fecha_inicio_periodo.month - _senda_inicio.month)
                        idx_senda = max(0, meses_desde_inicio)
                        ipc_periodo = _senda_inf[min(idx_senda, len(_senda_inf) - 1)]
                    else:
                        ipc_periodo = ipc_vigente
            except (ValueError, IndexError):
                ipc_periodo = ipc_vigente
        else:
            # Final: IPC vigente a la fecha del cupón
            try:
                if fc <= fecha_valuacion:
                    ipc_periodo = _obtener_ipc_vigente(fc, serie_ipc)
                else:
                    # Período futuro: usar senda proyectada si disponible
                    if _senda_inf:
                        meses_desde_inicio = ((fc.year - _senda_inicio.year) * 12
                                              + fc.month - _senda_inicio.month)
                        idx_senda = max(0, meses_desde_inicio)
                        ipc_periodo = _senda_inf[min(idx_senda, len(_senda_inf) - 1)]
                    else:
                        ipc_periodo = ipc_vigente
            except (ValueError, IndexError):
                ipc_periodo = ipc_vigente

        # Fórmula PORFIN: facial_EA = (1 + IPC) * (1 + spread) - 1
        facial_ea = (1 + ipc_periodo) * (1 + s) - 1

        # Días del período (base 365 real)
        dias_periodo = _dias_nl365(fecha_inicio_periodo, fc)

        # Tasa del período: (1 + facial_EA)^(dias/365) - 1
        tasa_periodo = (1 + facial_ea) ** (dias_periodo / 365.0) - 1

        # Cupón
        cupon = valor_nominal * round(tasa_periodo, 8)

        # Si es el último flujo de cupón antes del vencimiento, agregar principal
        flujo_total = cupon
        if fc == todas_las_fechas[-1]:
            flujo_total += valor_nominal

        flujos.append((fc, flujo_total))

    # Filtrar flujos futuros (después de fecha de valoración)
    flujos_futuros = [(f, m) for f, m in flujos if f > fecha_valuacion]

    if not flujos_futuros:
        return {"error": "No hay flujos futuros"}

    # Resolver TIR por bisección: VP(flujos_futuros, TIR) = valor_inversion / (nominal_posicion / valor_nominal)
    # valor_inversion viene normalizado a base 100
    target_vp = valor_inversion

    y_low, y_high = -0.05, 3.00
    for _ in range(500):
        y_mid = (y_low + y_high) / 2
        vp = sum(m / (1 + y_mid) ** (_dias_nl365(fecha_valuacion, f) / 365.0)
                 for f, m in flujos_futuros)
        if vp > target_vp:
            y_low = y_mid
        else:
            y_high = y_mid
    tir_calculada = y_mid

    # Calcular VP final con la TIR
    vp_total = 0.0
    sum_t_pv = 0.0
    for f, m in flujos_futuros:
        dias = _dias_nl365(fecha_valuacion, f)
        t = dias / 365.0
        factor = (1 + tir_calculada) ** t
        vp_flujo = m / factor
        vp_total += vp_flujo
        sum_t_pv += t * vp_flujo

    duracion_macaulay = sum_t_pv / vp_total if vp_total > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + tir_calculada)

    return {
        "precio_sucio": round(vp_total, 6),
        "tir_recalculada_pct": round(tir_calculada * 100, 10),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "valor_inversion": valor_inversion,
        "ipc_vigente_pct": round(ipc_vigente * 100, 4),
        "flujos_futuros": len(flujos_futuros),
        "spread_pct": spread,
    }


# ==============================================================================
# FUNCIÓN 3B: VALORAR BONO AMORTIZABLE (CON TABLA DE AMORTIZACIONES)
# ==============================================================================

def _cargar_tabla_amortizaciones(
    ruta: str = "scripts/8.Tablas_amortizaciones.xlsx",
) -> dict:
    """
    Carga las tablas de amortización desde Excel.

    Returns:
        Dict con ISIN como clave y lista de (fecha, pct_amortizacion) como valor.
    """
    from pathlib import Path
    ruta_abs = Path(__file__).parent.parent / "scripts" / "8.Tablas_amortizaciones.xlsx"
    if not ruta_abs.exists():
        ruta_abs = Path(ruta)
    if not ruta_abs.exists():
        return {}

    df = pd.read_excel(ruta_abs, sheet_name="Hoja1")
    df["FECHA PAGO"] = pd.to_datetime(df["FECHA PAGO"], errors="coerce")

    tabla = {}
    for isin, grupo in df.groupby("ISIN"):
        tabla[isin.strip()] = [
            (row["FECHA PAGO"].date(), float(row["AMORTIZACION"]))
            for _, row in grupo.iterrows()
            if pd.notna(row["FECHA PAGO"])
        ]
    return tabla


_TABLAS_AMORT = {}  # Se recarga siempre


def valorar_bono_amortizable(
    tasa_cupon: float,
    yield_mercado: float,
    fecha_emision: date,
    fecha_vencimiento: date,
    isin: str,
    valor_nominal: float = 100.0,
    frecuencia: Literal["anual", "semestral", "trimestral", "mensual"] = "mensual",
    fecha_valuacion: date | None = None,
    base_dias: Literal["30/360", "NL/365"] = "30/360",
    tipo_tasa: Literal["NOMINAL", "EFECTIVO"] = "NOMINAL",
) -> dict:
    """
    Valora un bono con amortización de capital usando tabla de pagos predefinida.

    En cada fecha de pago:
        flujo = cupón_sobre_saldo_vigente + amortización_capital

    El cupón se calcula sobre el saldo vigente (nominal menos amortizaciones
    ya pagadas). El descuento usa la base indicada.

    Args:
        tasa_cupon: Tasa cupón anual como porcentaje.
        yield_mercado: T.Dcto como porcentaje EA.
        fecha_emision: Fecha de emisión.
        fecha_vencimiento: Fecha de vencimiento.
        isin: ISIN del título (para buscar en tabla de amortizaciones).
        valor_nominal: Valor nominal del título (default 100 para %).
        frecuencia: Periodicidad de pago del cupón.
        fecha_valuacion: Fecha de valoración.
        base_dias: "30/360" o "NL/365".
        tipo_tasa: "NOMINAL" o "EFECTIVO".

    Returns:
        Dict con precio sucio, limpio, duración, etc.
    """
    global _TABLAS_AMORT

    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    # Siempre recargar tabla (asegura datos frescos)
    _TABLAS_AMORT = _cargar_tabla_amortizaciones()
    # Forzar recarga si está vacío para este ISIN
    if isin not in _TABLAS_AMORT:
        _TABLAS_AMORT.update(_cargar_tabla_amortizaciones())

    amortizaciones = _TABLAS_AMORT.get(isin, [])
    if not amortizaciones:
        return {"error": f"No hay tabla de amortización para ISIN {isin}"}

    y = yield_mercado / 100.0
    tasa = tasa_cupon / 100.0

    _dias_fn = _dias_nl365 if base_dias == "NL/365" else _dias_30_360
    divisor = 365.0 if base_dias == "NL/365" else 360.0

    periodos_por_anio = {"anual": 1, "semestral": 2, "trimestral": 4, "mensual": 12}
    n_periodos = periodos_por_anio[frecuencia]
    dias_periodo_base = int(divisor) // n_periodos

    # Filtrar amortizaciones futuras
    amort_futuras = [(f, pct) for f, pct in amortizaciones if f > fecha_valuacion]

    if not amort_futuras:
        return {"error": "No hay amortizaciones futuras"}

    # El nominal del 597 ya es el saldo vigente actual.
    # Los % de la tabla se aplican directamente sobre este nominal.
    # No restar amortizaciones pasadas porque el nominal ya lo refleja.
    saldo_vigente = valor_nominal

    # Precio sucio: VP de cada flujo futuro
    precio_sucio = 0.0
    sum_t_pv = 0.0
    saldo_corriente = saldo_vigente

    # Construir lista de fechas para calcular días reales entre períodos
    todas_fechas_amort = [f for f, _ in amortizaciones]
    fechas_pasadas_list = [f for f in todas_fechas_amort if f <= fecha_valuacion]
    if fechas_pasadas_list:
        fecha_inicio_periodo_prev = max(fechas_pasadas_list)
    elif amort_futuras:
        # Si no hay fechas pasadas en la tabla, el período actual empezó ~1 mes antes del primer flujo
        from dateutil.relativedelta import relativedelta
        fecha_inicio_periodo_prev = amort_futuras[0][0] - relativedelta(months=1)
    else:
        fecha_inicio_periodo_prev = fecha_valuacion

    for fecha_pago, pct_amort in amort_futuras:
        dias_dcto = _dias_fn(fecha_valuacion, fecha_pago)
        t = dias_dcto / divisor
        factor = (1 + y) ** t

        # Capital amortizado: pct aplicado sobre el NOMINAL ORIGINAL
        # (los % de la tabla representan flujos fijos proporcionales al nominal)
        capital_pago = saldo_vigente * pct_amort

        # Cupón sobre el saldo corriente (que va disminuyendo)
        # Usar días reales del período para tasa efectiva
        dias_periodo_real = _dias_fn(fecha_inicio_periodo_prev, fecha_pago)
        if tipo_tasa == "NOMINAL":
            cupon_pago = saldo_corriente * (tasa / n_periodos)
        else:
            cupon_pago = saldo_corriente * ((1 + tasa) ** (dias_periodo_real / divisor) - 1)

        flujo_total = capital_pago + cupon_pago
        vp = flujo_total / factor
        precio_sucio += vp
        sum_t_pv += t * vp

        # Reducir saldo para el próximo período
        saldo_corriente -= capital_pago
        fecha_inicio_periodo_prev = fecha_pago

    # Normalizar precio a porcentaje del saldo vigente
    precio_pct = (precio_sucio / saldo_vigente * 100.0) if saldo_vigente > 0 else 0.0

    # Intereses acumulados (período parcial desde último pago hasta valuación)
    amort_pasadas_fechas = [f for f, _ in amortizaciones if f <= fecha_valuacion]
    if amort_pasadas_fechas:
        ultimo_pago = max(amort_pasadas_fechas)
    else:
        ultimo_pago = fecha_emision

    dias_accrued = _dias_fn(ultimo_pago, fecha_valuacion)
    if tipo_tasa == "NOMINAL":
        intereses_acumulados = saldo_vigente * (tasa / n_periodos) * dias_accrued / (divisor / n_periodos)
    else:
        intereses_acumulados = saldo_vigente * ((1 + tasa) ** (dias_accrued / divisor) - 1)

    precio_limpio = precio_sucio - intereses_acumulados

    # Duración
    duracion_macaulay = sum_t_pv / precio_sucio if precio_sucio > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + y)

    return {
        "precio_sucio": round(precio_sucio, 6),
        "precio_sucio_pct": round(precio_pct, 6),
        "precio_limpio": round(precio_limpio, 6),
        "intereses_acumulados": round(intereses_acumulados, 6),
        "duracion_macaulay": round(duracion_macaulay, 6),
        "duracion_modificada": round(duracion_modificada, 6),
        "yield_mercado_pct": yield_mercado,
        "tasa_cupon_pct": tasa_cupon,
        "saldo_vigente": round(saldo_vigente, 6),
        "amort_futuras": len(amort_futuras),
    }


# ==============================================================================
# FUNCIÓN 4: VALORAR TIPS (AMORTIZABLES MENSUALES)
# ==============================================================================

# Tablas de flujos precargadas por ISIN (interés + capital por fecha)
# Extraídas de reportes PORFIN. Los flujos están en la unidad del título:
# COP para TIPS N, UVR para TIPS U.
_TIPS_FLUJOS = {}  # Se carga bajo demanda con _cargar_flujos_tips()


def _cargar_flujos_tips() -> dict:
    """
    Carga las tablas de flujos de los TIPS desde un archivo CSV.
    Si no existe el archivo, retorna las tablas hardcodeadas del primer corte.

    Returns:
        Dict con ISIN como clave y lista de (fecha, monto) como valor.
    """
    from pathlib import Path
    import csv

    ruta_csv = Path(__file__).parent.parent / "flujos_tips.csv"
    if ruta_csv.exists():
        flujos = {}
        with open(ruta_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                isin = row["isin"]
                fecha = date(int(row["anio"]), int(row["mes"]), int(row["dia"]))
                monto = float(row["monto"])
                if isin not in flujos:
                    flujos[isin] = []
                flujos[isin].append((fecha, monto))
        return flujos

    # Fallback: sin archivo CSV, retorna vacío (requiere archivo)
    return {}


def valorar_bono_tips(
    isin: str,
    valor_mercado_actual: float,
    yield_mercado: float | None = None,
    fecha_valuacion: date | None = None,
    valor_uvr: float = 1.0,
    nominal_posicion: float | None = None,
    flujos_override: list | None = None,
) -> dict:
    """
    Valora un TIPS (Título de Participación de Infraestructura) con amortización
    mensual. Usa tablas de flujos precargadas por ISIN.

    La valoración se hace por VP de flujos con NL/365. Si no se provee yield,
    se calcula la TIR implícita por bisección a partir del valor de mercado actual.

    Los flujos de la tabla son para una unidad base. Si se provee nominal_posicion,
    los flujos se escalan proporcionalmente (factor = nominal_posicion) y el VM
    resultante corresponde a la posición completa.

    Args:
        isin: Código ISIN del TIPS (ej. 'COT80CH03207').
        valor_mercado_actual: VM actual en COP (del 597 o PIP) para la posición.
        yield_mercado: Yield de descuento en porcentaje. Si None, calcula TIR implícita.
        fecha_valuacion: Fecha de valoración. Si None, usa hoy.
        valor_uvr: Valor de la UVR del día (1.0 si es en pesos).
        nominal_posicion: Nominal de la posición (para escalar flujos de tabla).
        flujos_override: Lista de (fecha, monto) para usar en vez de la tabla precargada.

    Returns:
        Dict con VP, yield implícito, duración, y datos para sensibilización.
    """
    global _TIPS_FLUJOS

    if fecha_valuacion is None:
        fecha_valuacion = date.today()

    # Obtener flujos
    if flujos_override:
        flujos = flujos_override
    else:
        if not _TIPS_FLUJOS:
            _TIPS_FLUJOS = _cargar_flujos_tips()
        flujos = _TIPS_FLUJOS.get(isin, [])

    if not flujos:
        return {"error": f"No hay flujos precargados para ISIN {isin}"}

    # Filtrar flujos futuros
    flujos_futuros = [(f, m) for f, m in flujos if f > fecha_valuacion and m > 0]

    if not flujos_futuros:
        return {"error": "No hay flujos futuros para este título"}

    # Determinar factor de escala:
    # La tabla tiene flujos para una base. Si hay nominal_posicion, calcular
    # el factor comparando el VM actual vs VP sin descontar de la tabla.
    # Enfoque: la TIR es invariante a escala, así que calculamos TIR sobre
    # flujos base y target = VM / factor_escala.
    if nominal_posicion is not None and nominal_posicion > 0:
        # Estimar factor: VM_actual / VP_tabla_sin_descontar (a tasa 0)
        suma_flujos_base = sum(m for _, m in flujos_futuros)
        if suma_flujos_base > 0:
            factor_escala = valor_mercado_actual / (suma_flujos_base * valor_uvr)
            # Si el factor es razonable (>0.3 y <3) la tabla ya está en la escala del VM
            # Si es >>1 significa que el VM es mucho mayor que la tabla → escalar
            if factor_escala > 3.0:
                # Tabla es para unidad base, VM es para posición completa
                factor_escala = nominal_posicion
            elif factor_escala < 0.3:
                factor_escala = nominal_posicion
            else:
                factor_escala = 1.0
        else:
            factor_escala = 1.0
    else:
        factor_escala = 1.0

    # Target en la unidad del título (UVR o COP), ajustado por escala
    target_vp = valor_mercado_actual / (valor_uvr * factor_escala)

    # Calcular TIR implícita si no se provee yield
    if yield_mercado is None:
        y_low, y_high = -0.20, 2.00
        for _ in range(300):
            y_mid = (y_low + y_high) / 2
            vp = sum(m / (1 + y_mid) ** (_dias_nl365(fecha_valuacion, f) / 365)
                     for f, m in flujos_futuros)
            if vp > target_vp:
                y_low = y_mid
            else:
                y_high = y_mid
        yield_calc = y_mid
    else:
        yield_calc = yield_mercado / 100.0

    # Calcular VP con el yield
    vp = 0.0
    sum_t_pv = 0.0

    for f, m in flujos_futuros:
        dias = _dias_nl365(fecha_valuacion, f)
        t = dias / 365.0
        factor = (1 + yield_calc) ** t
        vp_flujo = m / factor
        vp += vp_flujo
        sum_t_pv += t * vp_flujo

    # VM en COP (escalado a la posición)
    vm_cop = vp * valor_uvr * factor_escala

    # Duración Macaulay y modificada
    duracion_macaulay = sum_t_pv / vp if vp > 0 else 0.0
    duracion_modificada = duracion_macaulay / (1 + yield_calc) if (1 + yield_calc) > 0 else 0.0

    return {
        "valor_mercado_cop": round(vm_cop, 2),
        "vp_unidad": round(vp, 2),
        "yield_implicito_pct": round(yield_calc * 100, 6),
        "duracion_macaulay": round(duracion_macaulay, 4),
        "duracion_modificada": round(duracion_modificada, 4),
        "flujos_futuros": len(flujos_futuros),
        "valor_uvr": valor_uvr,
        "isin": isin,
    }


def sensibilizar_tips(
    isin: str,
    valor_mercado_actual: float,
    shock_bps: float,
    fecha_valuacion: date | None = None,
    valor_uvr: float = 1.0,
    nominal_posicion: float | None = None,
    flujos_override: list | None = None,
) -> dict:
    """
    Sensibiliza un TIPS: calcula TIR implícita y luego mueve ± bps.

    Args:
        isin: ISIN del TIPS.
        valor_mercado_actual: VM actual en COP.
        shock_bps: Shock en puntos básicos (ej. +100 = yield sube 1%).
        fecha_valuacion: Fecha de valoración.
        valor_uvr: Valor UVR del día.
        nominal_posicion: Nominal de la posición (para escalar flujos).
        flujos_override: Flujos manuales.

    Returns:
        Dict con VM base, VM con shock, P&L, duración.
    """
    # Calcular base (TIR implícita)
    base = valorar_bono_tips(
        isin=isin, valor_mercado_actual=valor_mercado_actual,
        yield_mercado=None, fecha_valuacion=fecha_valuacion,
        valor_uvr=valor_uvr, nominal_posicion=nominal_posicion,
        flujos_override=flujos_override,
    )

    if "error" in base:
        return base

    # Calcular con shock
    yield_shock = base["yield_implicito_pct"] + shock_bps / 100.0
    shock = valorar_bono_tips(
        isin=isin, valor_mercado_actual=valor_mercado_actual,
        yield_mercado=yield_shock, fecha_valuacion=fecha_valuacion,
        valor_uvr=valor_uvr, nominal_posicion=nominal_posicion,
        flujos_override=flujos_override,
    )

    pnl = shock["valor_mercado_cop"] - base["valor_mercado_cop"]

    return {
        "vm_base_cop": base["valor_mercado_cop"],
        "vm_shock_cop": shock["valor_mercado_cop"],
        "pnl_cop": round(pnl, 2),
        "pnl_pct": round(pnl / base["valor_mercado_cop"] * 100, 4) if base["valor_mercado_cop"] > 0 else 0,
        "yield_base_pct": base["yield_implicito_pct"],
        "yield_shock_pct": yield_shock,
        "duracion_modificada": base["duracion_modificada"],
        "shock_bps": shock_bps,
        "isin": isin,
    }


# ==============================================================================
# EJEMPLO DE USO
# ==============================================================================

if __name__ == "__main__":
    # --- Ejemplo 1: Bono Tasa Fija (TFVT10260331) ---
    print("\n" + "=" * 55)
    print("BONO TASA FIJA — TFVT10260331")
    print("=" * 55)
    resultado_tf = valorar_bono_tf(
        tasa_cupon=7.0,
        yield_mercado=14.65,
        fecha_emision=date(2021, 3, 26),
        fecha_vencimiento=date(2031, 3, 26),
        valor_nominal=100.0,
        frecuencia="anual",
        fecha_valuacion=date(2026, 5, 22),
    )
    for k, v in resultado_tf.items():
        print(f"  {k:30s}: {v}")

    # --- Ejemplo 2: Bono UVR (TRST20250333) ---
    print("\n" + "=" * 55)
    print("BONO UVR — TRST20250333")
    print("=" * 55)
    resultado_uvr = valorar_bono_uvr(
        tasa_cupon_real=3.0,
        yield_real=7.10,
        fecha_emision=date(2013, 3, 25),
        fecha_vencimiento=date(2033, 3, 25),
        valor_uvr=411.2813,
        valor_nominal_uvr=100.0,
        frecuencia="anual",
        fecha_valuacion=date(2026, 5, 22),
    )
    for k, v in resultado_uvr.items():
        print(f"  {k:30s}: {v}")

    # --- Ejemplo 3: Bono IPC (BO EMGESA SA ESP) ---
    print("\n" + "=" * 55)
    print("BONO IPC — BO EMGESA SA ESP (IPC+3.64F, TV)")
    print("=" * 55)
    serie_ipc = cargar_serie_ipc("Serie Inflación.xlsx")
    resultado_ipc = valorar_bono_ipc(
        spread=3.64,
        yield_mercado=13.23,
        fecha_emision=date(2012, 12, 13),
        fecha_vencimiento=date(2027, 12, 13),
        serie_ipc=serie_ipc,
        uso_tasa="F",
        valor_nominal=100.0,
        frecuencia="trimestral",
        fecha_valuacion=date(2026, 5, 22),
    )
    for k, v in resultado_ipc.items():
        if k == "flujos":
            print(f"  {'flujos':30s}:")
            for flujo in v:
                print(f"    {flujo['fecha']} | IPC={flujo['ipc_usado']:6.2f}% | "
                      f"cupon={flujo['tasa_cupon_periodo']:5.2f}% | "
                      f"flujo={flujo['flujo']:8.4f} | VP={flujo['vp']:8.4f}")
        else:
            print(f"  {k:30s}: {v}")

    print("\n" + "=" * 55)
    print(f"  597 reporta precio EMGESA: 96.983")
    print(f"  Nosotros calculamos:       {resultado_ipc['precio_sucio']}")
    print("=" * 55)
