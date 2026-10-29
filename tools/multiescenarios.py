"""
AXIS — Multiescenarios de estrés (utilidad periférica)
======================================================

Script AUTÓNOMO para directivas. NO toca app_dash.py, ni el motor, ni el flujo
core. Se corre a mano desde consola:

    python tools/multiescenarios.py

Lee una batería de escenarios definidos por el usuario (una fila = un escenario
COMPLETO, con TODAS las palancas fijadas a la vez, NO una matriz combinatoria) y
saca a Excel el P&L del portafolio POR TIPO DE ACTIVO para cada escenario.

Columnas de salida (P&L en COP):
  - pnl_RF_PyG   : P&L a resultados de RF (NEG y VC a mercado + DV a causación TIR)
  - pnl_RF_ORI   : ORI de RF (solo títulos DV: diferencia mercado - TIR)
  - pnl_RF_local : P&L de RF en COP/UVR
  - pnl_RF_int   : P&L de RF internacional (moneda USD/globales)
  - pnl_RV_local : P&L RV local COLCAP (NO incluye PEI)
  - pnl_RV_int   : P&L RV internacional (MXWD)
  - pnl_PEI      : P&L del PEI (COP; va aparte de RV local pero SÍ suma al TOTAL)
  - pnl_ALT_local / pnl_ALT_int : P&L alternativos local / internacional
  - pnl_Creditos : costo de intereses de créditos (negativo)
  - pnl_Forwards / pnl_Opciones / pnl_Swaps : derivados desglosados
  - efecto_moneda: INFORMATIVO. Variación del dólar aplicada al VM inicial de los
                   activos del exterior (RF int + RV int + ALT int):
                       efecto_moneda = VM_ini_ext * (trm_final/trm_t0 - 1)
                   Ya está EMBEBIDO dentro de los P&L internacionales (no se suma
                   al TOTAL). El retorno en USD de un bloque int se obtiene como
                   (pnl_bloque_int - su parte de efecto_moneda).
  - pnl_TOTAL    : suma de todos los bloques de P&L (sin efecto_moneda).

Hojas del Excel de salida:
  - Resumen        : una fila por escenario con todas las columnas de P&L.
  - Resumen_Compania : una fila por (escenario × compañía) con TODAS las columnas
                     de P&L desglosadas (RF/RV/PEI/ALT/derivados + fx + TOTAL).
                     Derivados se mapean por portafolio (01=Capitalizadora,
                     02=Bolívar, 03=Comerciales). NO incluye créditos.
  - <Escenario>    : detalle por posición (RF+RV+ALT) al cierre del horizonte.
  - <Escenario>_Fwd/_Opc/_Swp : SÁBANA de derivados (forwards/opciones/swaps) a
                     TODOS los cortes de valoración (una fila por instrumento y corte).

Supuesto contable clave
-----------------------
Supuestos macro por escenario (columnas del input):
  - ipc_ini / ipc_fin  : senda de inflación (% EA decimal) LINEAL. Alimenta RF
                         (bonos IPC), alternativos FCP 4G y la UVR de cierre.
  - repo_ini / repo_fin: tasa BanRep (% anual decimal) ESCALONADA (solo cambia en
                         meses de decisión de junta; rige el mes siguiente). Aplica
                         a cuentas vista COP (FIC, Cta Ahorro) en RF.
  - shift_cop/uvr/tesoros/fed_bps: mueven las curvas de valoración de DERIVADOS
                         (forwards, opciones, swaps) escalado por progreso, igual
                         que el motor principal. shift_fed_bps disponible (default 0).

Criterio por clasificación contable de cada título de RF:
  - Negociables (NEG):    a MERCADO, todo a P&G.
  - Al vencimiento (VC):  a MERCADO, todo a P&G.
  - Disponibles venta (DV): P&G a causación (TIR); la diferencia mercado - TIR va
                            a ORI (no impacta resultados).
Es un P&L ECONÓMICO/de mercado para NEG y VC; los DV amortiguan vía ORI.

Base del P&L de RF (importante)
-------------------------------
P&L ECONÓMICO del horizonte, por título:

    P&L_RF = VM(t+horizonte) − VM(t0) + cupones cobrados

VM(t0) es la valoración a MERCADO en t0 (ancla PIP: revaloración BVC al rendimiento
PIP individual, shift=0), aplicada a TODOS los títulos INCLUIDOS los clasificados a
vencimiento. Así los VC quedan valorados económicamente en t0 (no a su costo
amortizado contable) y su P&L refleja el cambio de mercado del horizonte.

NO se usa contrafactual/baseline: el P&L es el resultado económico TOTAL del
escenario (carry del horizonte + efecto de mover TRM/curvas/spreads). Por eso el
consolidado cuadra con la suma del detalle por título. El ancla PIP en t0 (misma
base que el motor usa mes a mes) evita la brecha t0 597-vs-PIP, de modo que la resta
VM(t+h) − VM(t0) no arrastra un salto de base espurio.

Reutiliza el motor existente (mismas funciones que el dashboard) para que los
números sean consistentes con la valoración oficial. La orquestación es una
réplica ligera de `_ejecutar_escenario` de app_dash (que no se puede reutilizar
sin tocar el monolito).

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

# Progreso visible aunque stdout no sea una terminal (evita que parezca "colgado":
# la proyección RF de todo el portafolio toma ~1 min por mes de horizonte).
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ── Rutas del proyecto ──────────────────────────────────────────────────────
# tools/ está directamente bajo la raíz del proyecto.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# scripts/ debe estar en el path: los módulos del motor se importan entre sí
# sin prefijo de paquete (igual que hace app_dash.py).
for _p in (str(PROJECT_ROOT), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comun.rutas import DB_PATH  # noqa: E402

# ── Motor (mismas funciones que el dashboard) ───────────────────────────────
from motor_proyeccion import (  # noqa: E402
    cargar_portafolio_proyeccion,
    proyectar_portafolio,
    valorar_titulo,
)
from curvas_rendimiento import (  # noqa: E402
    extraer_curva_base_pip_db,
    generar_senda_lineal,
)
from proyeccion_renta_variable import proyectar_renta_variable  # noqa: E402
from proyeccion_alternativos import proyectar_alternativos  # noqa: E402
from proyeccion_creditos import proyectar_creditos_contratados  # noqa: E402
from proyeccion_tramos_multiactivo import normalizar_trm_nivel  # noqa: E402


# =============================================================================
# CONFIGURACIÓN GLOBAL (constantes del script, no por escenario)
# =============================================================================
RUTA_INPUT = PROJECT_ROOT / "tools" / "escenarios_input.xlsx"
RUTA_OUTPUT = PROJECT_ROOT / "tools" / "resultado_multiescenarios.xlsx"

# Horizonte de proyección en meses (12 / 24 / 60). Config global, no por escenario.
HORIZONTE_MESES = 12

# Ancho del collar (spread Call-Put en COP) para renovaciones de opciones.
# Put strike = TRM ; Call strike = TRM + CANAL_OPC.
CANAL_OPC = 1100

# Serie de inflación (opcional). Si el archivo no existe, el motor usa IPC vigente
# constante como fallback (mismo comportamiento tolerante del dashboard).
RUTA_SERIE_IPC = PROJECT_ROOT / "Serie Inflación.xlsx"

# IPC anual vigente a la fecha de corte (decimal). Se usa como fallback si no se puede
# leer de la fuente. A julio-2026 el IPC vigente es 6.14%.
IPC_T0_FALLBACK = 0.0614

# Palancas esperadas en el Excel de entrada (columnas). Orden = orden visible en
# la salida (las palancas van a la izquierda del bloque de P&L).
COLUMNAS_PALANCAS = [
    "escenario",
    "trm_final",
    "shift_cop_bps",
    "shift_uvr_bps",
    "shift_fs_bps",
    "shift_ipc_bps",
    "shift_subyac_bps",
    "shift_tesoros_bps",
    "shift_fed_bps",     # delta FED (bps) para curvas de derivados (LUTIBR corto / q USD)
    "ipc_ini",           # IPC anual inicial (% EA en decimal, ej. 0.0584)
    "ipc_fin",           # IPC anual final (senda lineal ipc_ini -> ipc_fin)
    "repo_ini",          # Tasa BanRep inicial (% anual en decimal, ej. 0.0975)
    "repo_fin",          # Tasa BanRep final (escalonada en meses de decisión)
    "rv_local",
    "rv_intl",
    "rv_pei",
    "ret_alt_local",
    "ret_alt_intl",
    "spread_4g",
    "cred_ipc",
    "cred_ibr",
]

# Meses con decisión de tasa de BanRep (junta). La decisión de un mes rige el mes
# siguiente. Igual que el motor principal.
MESES_DECISION_REPO = {1, 3, 4, 6, 7, 9, 10, 12}


# =============================================================================
# HELPERS DE CONTEXTO T0 (fecha de corte, TRM/UVR/IPC vigentes)
# =============================================================================
def obtener_fecha_corte(ruta_db: str) -> date:
    """Fecha de corte vigente = MAX(fecha_reporte) de hist_titulos."""
    import sqlite3

    conn = sqlite3.connect(ruta_db)
    try:
        row = conn.execute("SELECT MAX(fecha_reporte) FROM hist_titulos").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise RuntimeError("No hay datos en hist_titulos para determinar la fecha de corte.")
    return pd.to_datetime(row[0]).date()


def obtener_trm_t0(fecha_corte: date) -> float:
    """TRM implícita vigente (fuente oficial gestor_informes). Fallback razonable."""
    try:
        from gestor_informes import obtener_trm_implicita

        trm, _ = obtener_trm_implicita(fecha_corte)
        if trm:
            return float(trm)
    except Exception:
        pass
    return 4000.0


def obtener_uvr_t0(fecha_corte: date) -> float:
    """UVR vigente (implícita de TUVT). Fallback razonable si no se puede calcular."""
    try:
        from gestor_informes import obtener_uvr_vigente

        uvr, _ = obtener_uvr_vigente(fecha_corte)
        if uvr:
            return float(uvr)
    except Exception:
        pass
    return 411.2813


def obtener_ipc_t0(fecha_corte: date) -> float:
    """
    IPC anual vigente en decimal a la fecha de corte. Se usa para la base neutral y
    para deflactar los spreads IPC de los bonos. Fallback IPC_T0_FALLBACK si no hay
    fuente (Serie Inflación.xlsx ausente).
    """
    try:
        from gestor_informes import obtener_ipc_vigente

        ipc_pct, _ = obtener_ipc_vigente(fecha_corte)
        if ipc_pct is not None:
            return float(ipc_pct) / 100.0
    except Exception:
        pass
    return IPC_T0_FALLBACK


def cargar_serie_ipc_opcional() -> pd.DataFrame:
    """Carga la serie de inflación si existe; si no, DataFrame vacío (fallback tolerante)."""
    if not RUTA_SERIE_IPC.exists():
        print(f"  [WARN] {RUTA_SERIE_IPC.name} no encontrado. IPC tratado como constante.")
        return pd.DataFrame()
    try:
        from valorar_bonos import cargar_serie_ipc

        return cargar_serie_ipc(str(RUTA_SERIE_IPC))
    except Exception as e:
        print(f"  [WARN] No se pudo cargar la serie IPC: {e}")
        return pd.DataFrame()


def calcular_uvr_cierre(uvr_t0: float, ipc_t0: float, n_meses: int) -> float:
    """UVR de cierre capitalizando la UVR inicial con el IPC vigente (aprox. plana)."""
    return float(uvr_t0) * (1.0 + ipc_t0) ** (n_meses / 12.0)


# =============================================================================
# ARMADO DE SENDAS (curvas base planas para RF; los shifts los aplica el motor)
# =============================================================================
def construir_sendas(curvas_base: dict, n_meses: int) -> dict:
    """
    Senda PLANA por curva desde la curva vigente. Los movimientos de curva del
    escenario se aplican por los parámetros shift_*_bps (que el motor aplica
    título a título), NO modificando la curva base.
    """
    sendas: dict = {}
    for nombre, curva in curvas_base.items():
        sendas[nombre] = generar_senda_lineal(curva, curva, n_meses)
    return sendas


def senda_ipc_mensual(esc: dict, ctx: dict, n_meses: int) -> list:
    """
    Senda de inflación mensual (lista de IPC % EA por mes, longitud n_meses+1),
    lineal desde ipc_ini hasta ipc_fin del escenario. Se entrega en % EA (ej. 5.84)
    porque así lo espera el motor (senda_inflacion) y alternativos (dividido /100).
    Si ipc_ini/ipc_fin no vienen (0), cae al IPC vigente T0 plano.
    """
    ipc_ini = float(esc.get("ipc_ini") or 0.0)
    ipc_fin = float(esc.get("ipc_fin") or 0.0)
    if ipc_ini <= 0 and ipc_fin <= 0:
        base = ctx["ipc_t0"] * 100.0  # % EA
        return [base] * (n_meses + 1)
    if ipc_fin <= 0:
        ipc_fin = ipc_ini
    # Los inputs vienen en decimal (0.0584); el motor espera % EA.
    a, b = ipc_ini * 100.0, ipc_fin * 100.0
    return [a + (b - a) * m / n_meses for m in range(n_meses + 1)]


def senda_repo_mensual(esc: dict, fecha_corte: date, n_meses: int) -> list:
    """
    Senda de tasa BanRep mensual (% anual, longitud n_meses+1) ESCALONADA: solo
    cambia en los meses de decisión de junta (MESES_DECISION_REPO) y la decisión
    rige el mes siguiente. Distribuye el paso de repo_ini a repo_fin repartiendo
    el cambio en partes iguales entre las decisiones del horizonte.

    Si repo_ini/repo_fin no vienen (0), devuelve senda plana en 0 (comportamiento
    actual: vista COP sin rendimiento).
    """
    from dateutil.relativedelta import relativedelta as _rd

    repo_ini = float(esc.get("repo_ini") or 0.0) * 100.0  # a % anual
    repo_fin = float(esc.get("repo_fin") or 0.0) * 100.0
    if repo_ini <= 0 and repo_fin <= 0:
        return [0.0] * (n_meses + 1)
    if repo_fin <= 0:
        repo_fin = repo_ini

    # Fechas de cada mes del horizonte (mes 0 = corte).
    fechas = [fecha_corte + _rd(months=m) for m in range(n_meses + 1)]
    # Meses donde RIGE un cambio = mes siguiente a una decisión dentro del horizonte.
    idx_vigencia = [m for m in range(1, n_meses + 1)
                    if fechas[m - 1].month in MESES_DECISION_REPO]
    senda = [repo_ini] * (n_meses + 1)
    if not idx_vigencia:
        # Sin decisiones en el horizonte: se mantiene repo_ini plano.
        return senda
    # Repartir el cambio total en escalones iguales por cada decisión.
    paso = (repo_fin - repo_ini) / len(idx_vigencia)
    nivel = repo_ini
    j = 0
    for m in range(1, n_meses + 1):
        if j < len(idx_vigencia) and m == idx_vigencia[j]:
            nivel += paso
            j += 1
        senda[m] = nivel
    return senda


def metodo_params_deriv(esc: dict) -> dict | None:
    """
    Construye metodo_params para mover las curvas de derivados con los shifts del
    escenario (igual que el motor principal):
      - delta_tes_cop: de shift_cop_bps  (mueve SWTIBR y r COP de fwd/opciones)
      - delta_tes_uvr: de shift_uvr_bps  (mueve UVTIBR)
      - delta_10t_bps: de shift_tesoros_bps (LUTIBR largo / q USD)
      - delta_fed_bps: de shift_fed_bps  (LUTIBR corto / q USD)
    Devuelve None si no hay ningún shift (curvas planas).
    """
    nodos = [30, 90, 180, 365, 730, 1095, 1825, 2555, 3650]
    mp: dict = {}
    scop = float(esc.get("shift_cop_bps") or 0.0)
    suvr = float(esc.get("shift_uvr_bps") or 0.0)
    s10t = float(esc.get("shift_tesoros_bps") or 0.0)
    sfed = float(esc.get("shift_fed_bps") or 0.0)
    if scop:
        mp["delta_tes_cop"] = {n: scop / 10000.0 for n in nodos}
    if suvr:
        mp["delta_tes_uvr"] = {n: suvr / 10000.0 for n in nodos}
    if s10t:
        mp["delta_10t_bps"] = s10t
    if sfed:
        mp["delta_fed_bps"] = sfed
    return mp or None


def senda_trm_dict(esc: dict, ctx: dict) -> dict:
    """
    Dict {fecha_str 'YYYY-MM-DD': TRM} lineal desde la TRM vigente (T0) hasta la
    TRM final del escenario, por corte mensual. Función a nivel de módulo (no una
    clausura) para que el contexto sea picklable y pueda usarse en subprocesos.
    """
    periodos = pd.date_range(
        start=f"{ctx['fecha_corte'].strftime('%Y-%m')}-01",
        end=f"{ctx['fecha_fin'].strftime('%Y-%m')}-01",
        freq="MS",
    )
    niveles = np.linspace(ctx["trm_t0"], float(esc["trm_final"]), len(periodos))
    return {p.strftime("%Y-%m-%d"): float(v) for p, v in zip(periodos, niveles)}


# =============================================================================
# CÁLCULO DE P&L POR BLOQUE (un escenario)
# =============================================================================
def _es_titulo_int(t) -> bool:
    """Un título RF es internacional si su moneda no es COP ni UVR (USD/Yankees/globales)."""
    return str(getattr(t, "moneda", "COP")).strip().upper() not in ("COP", "UVR")


def _es_subyacente(t) -> bool:
    """Título cubierto con swap (subyacente): tiene id_cobertura no vacío."""
    return bool(str(getattr(t, "id_cobertura", "") or "").strip())


def _categoria_contable(clasif: str) -> str:
    """NEG / DV / VC según la clasificación contable del título (lógica del dashboard)."""
    s = str(clasif or "").strip()
    if "Disp" in s or "Venta" in s:
        return "DV"
    if "Venc" in s:
        return "VC"
    return "NEG"


def _anclar_vm_pip_t0(port, fecha_corte, ctx) -> dict:
    """
    Revalora cada título de RF a su base PIP en la fecha de corte (t0) y devuelve
    {id_posicion: VM_pip_t0}.

    El ancla t0 = revaloración BVC con la MISMA base que el motor usa mes a mes
    (rendimiento PIP individual `spread_mercado`, shift=0). Esto reemplaza el VM
    del 597 como base inicial, de modo que el detalle compare mercado (PIP) contra
    mercado (PIP) y NO arrastre la brecha t0 597-vs-PIP (concentrada en IPC/UVR).

    Réplica de la tasa de mercado del motor en mes=0, shift=0:
      - IPC:         tasa = spread_mercado (recomponer con IPC t0 == spread_mercado).
      - TF/UVR/YKE:  tasa = spread_mercado (+ shift 0).
      - sin PIP:     se conserva el VM del 597 (fallback, igual que el motor).
    """
    vm_pip: dict = {}
    for t in port:
        # Sin rendimiento PIP: el motor cae al valor_mercado del 597. Mismo fallback.
        if not getattr(t, "spread_mercado", 0) or t.spread_mercado <= 0:
            vm_pip[t.id_posicion] = t.valor_mercado
            continue
        try:
            vm_pip[t.id_posicion] = valorar_titulo(
                titulo=t,
                tasa_mercado_nueva=t.spread_mercado,  # mes=0, shift=0 -> tasa base PIP
                fecha_valuacion=fecha_corte,
                valor_uvr=ctx["uvr_t0"],
                trm=ctx["trm_t0"],
                serie_ipc=ctx["serie_ipc"],
                senda_inflacion=None,
                fecha_inicio_senda=fecha_corte,
            )
        except Exception:
            # Ante cualquier fallo de valoración, no romper: usar base 597.
            vm_pip[t.id_posicion] = t.valor_mercado
    return vm_pip


def _anclar_vtir_tir_t0(port, fecha_corte, ctx) -> dict:
    """
    Ancla t0 de CAUSACIÓN (valor a TIR) por título: {id_posicion: VTir_tir_t0}.

    A diferencia del ancla de mercado (`_anclar_vm_pip_t0`, que descuenta al
    rendimiento PIP), este ancla descuenta a la TASA DE COMPRA (TIR), que es la
    MISMA base que el motor usa para el `valor_tir` mes a mes durante el horizonte.

    Es la base correcta del PyG de los títulos que causan a TIR (DV): así el PyG
    es causación PURA (VTir_fin@TIR - VTir_t0@TIR) y NO absorbe el salto
    mercado-vs-TIR de t0. Ese salto (mercado PIP t0 - TIR t0) queda en el ORI, que
    es donde debe ir (Total mercado - PyG causación).

      - IPC:   el motor arranca su recursión TIR desde el `valor_tir` del 597, así
               que se usa ese mismo valor como ancla (consistencia con el motor).
      - resto: revaloración BVC a `tasa_compra` en t0 (misma fórmula del horizonte).
      - sin tasa_compra: fallback al `valor_tir` (o VM) del 597.
    """
    vtir_t0: dict = {}
    for t in port:
        _vtir_597 = t.valor_tir if getattr(t, "valor_tir", 0) else t.valor_mercado
        # IPC: el motor parte del valor_tir del 597 para su recursión de causación.
        if getattr(t, "tipo_bono", "") == "IPC":
            vtir_t0[t.id_posicion] = _vtir_597
            continue
        # Sin tasa de compra: no se puede descontar a TIR; usar base 597.
        if not getattr(t, "tasa_compra", 0) or t.tasa_compra <= 0:
            vtir_t0[t.id_posicion] = _vtir_597
            continue
        try:
            vtir_t0[t.id_posicion] = valorar_titulo(
                titulo=t,
                tasa_mercado_nueva=t.tasa_compra,  # descuento a TIR (igual que el motor)
                fecha_valuacion=fecha_corte,
                valor_uvr=ctx["uvr_t0"],
                trm=ctx["trm_t0"],
                serie_ipc=ctx["serie_ipc"],
                senda_inflacion=None,
                fecha_inicio_senda=fecha_corte,
            )
        except Exception:
            vtir_t0[t.id_posicion] = _vtir_597
    return vtir_t0


def _desglose_rf(port, fecha_corte, fecha_fin, n_meses, sendas, esc, ctx,
                 senda_inflacion=None, senda_repo=None, uvr_cierre=None,
                 vm_ini=None, vtir_ini=None) -> dict:
    """
    Corre la proyección RF de un escenario y devuelve el P&L descompuesto por
    título en DOS vistas (económica y contable). P&G = VM(t+h) − VM(t0) + cupones.

    Diferencia entre vistas SOLO en los títulos al vencimiento (VC):

      Vista ECONÓMICA (economico):
        - NEG: mercado.  pyg = (VM_mkt_fin − VM_pip_t0) + cup; ori = 0
        - VC : mercado.  pyg = (VM_mkt_fin − VM_pip_t0) + cup; ori = 0
        - DV : causación. pyg = (VTir_fin − VTir_t0) + cup; ori = total_mkt − pyg

      Vista CONTABLE (contable):
        - NEG: mercado (igual).
        - VC : TIR.      pyg = (VTir_fin − VTir_t0) + cup; ori = 0  (sin ORI)
        - DV : causación (igual que económica).

    Bases t0 (una por criterio de valoración, no por vista):
      - vm_ini   = ANCLA MERCADO PIP t0 (para el componente a mercado: NEG/VC econ y
                   el Total de DV que alimenta su ORI).
      - vtir_ini = ANCLA CAUSACIÓN TIR t0 (para el PyG de DV y para VC en la vista
                   contable). Se pueden inyectar precomputadas (misma base para los 3
                   escenarios) vía argumentos vm_ini / vtir_ini; si son None se calculan.

    Agrega por: PyG/ORI total (ambas vistas), y PyG por ámbito local/int/subyacente
    (internacional = moneda != COP/UVR). Devuelve un dict con esas agregaciones.
    """
    # Anclas t0: usar las precomputadas (una sola vez para los 3 escenarios) o
    # calcularlas aquí si no se inyectaron.
    if vm_ini is None:
        vm_ini = _anclar_vm_pip_t0(port, fecha_corte, ctx)     # MERCADO PIP t0
    if vtir_ini is None:
        vtir_ini = _anclar_vtir_tir_t0(port, fecha_corte, ctx)  # CAUSACIÓN TIR t0

    proyectar_portafolio(
        portafolio=port,
        fecha_inicio=fecha_corte,
        meses_horizonte=n_meses,
        sendas=sendas,
        valor_uvr=ctx["uvr_t0"],
        valor_uvr_cierre=uvr_cierre if uvr_cierre is not None else ctx["uvr_cierre"],
        trm=ctx["trm_t0"],
        # Normaliza la UNIDAD de la TRM de cierre (bugfix 2B): misma fuente de verdad
        # que la ruta por tramos (`InputsPorTramo.from_valores`). La TRM t0 (`trm_t0`,
        # implícita del 597) NO se normaliza (dato del sistema, siempre en nivel).
        trm_cierre=normalizar_trm_nivel(float(esc["trm_final"])),
        serie_ipc=ctx["serie_ipc"],
        senda_inflacion=senda_inflacion,
        shift_ipc_bps=float(esc["shift_ipc_bps"]),
        shift_cop_bps=float(esc["shift_cop_bps"]),
        shift_uvr_bps=float(esc["shift_uvr_bps"]),
        shift_fs_bps=float(esc["shift_fs_bps"]),
        shift_subyac_bps=float(esc["shift_subyac_bps"]),
        shift_tesoros_bps=float(esc["shift_tesoros_bps"]),
        ipc_vigente_t0=ctx["ipc_t0"],
        senda_repo=senda_repo,
    )

    # Agregados por las DOS vistas (eco = económica, con = contable). El ámbito
    # (local/int/sub) y el VM inicial de int/sub NO dependen de la vista.
    agg = {
        # Vista económica
        "pyg_eco": 0.0, "ori_eco": 0.0,
        "pyg_local_eco": 0.0, "pyg_int_eco": 0.0, "pyg_sub_eco": 0.0,
        # Vista contable
        "pyg_con": 0.0, "ori_con": 0.0,
        "pyg_local_con": 0.0, "pyg_int_con": 0.0, "pyg_sub_con": 0.0,
        # VM inicial internacional (efecto moneda), común a ambas vistas.
        "vm_ini_int": 0.0, "vm_ini_sub": 0.0,
    }
    filas_det = []
    for t in port:
        # Solo títulos vivos al final del horizonte (mismos que reporta el dashboard).
        if t.fecha_vencimiento <= fecha_fin:
            continue
        _vm_i = vm_ini.get(t.id_posicion, 0.0)          # ancla MERCADO PIP t0
        # Reinversiones creadas en el horizonte: base = monto reinvertido (vm_compra).
        if _vm_i == 0 and getattr(t, "es_reinversion", False):
            _vm_i = getattr(t, "vm_compra", t.nominal)
        _vtir_i = vtir_ini.get(t.id_posicion, _vm_i)    # ancla CAUSACIÓN TIR t0
        _vm_mkt = t.valor_mercado
        _vtir = t.valor_tir if getattr(t, "valor_tir", 0) else _vm_i
        _cup = getattr(t, "_cupones_cobrados", 0.0)

        total_mkt = (_vm_mkt - _vm_i) + _cup            # P&G a mercado (base PIP t0)
        pyg_tir = (_vtir - _vtir_i) + _cup              # P&G por causación (base TIR t0)

        cat = _categoria_contable(getattr(t, "clasificacion", ""))
        if cat == "DV":
            # DV: en AMBAS vistas, PyG a causación (TIR) y ORI = mercado − TIR.
            pyg_eco = pyg_tir;  ori_eco = total_mkt - pyg_tir
            pyg_con = pyg_tir;  ori_con = total_mkt - pyg_tir
        elif cat == "VC":
            # VC: económica a MERCADO (sin ORI); contable a TIR (sin ORI).
            pyg_eco = total_mkt;  ori_eco = 0.0
            pyg_con = pyg_tir;    ori_con = 0.0
        else:
            # NEG: mercado en ambas vistas.
            pyg_eco = total_mkt;  ori_eco = 0.0
            pyg_con = total_mkt;  ori_con = 0.0

        # Ámbito: subyacente (int cubierto por swap) > int (no cubierto) > local.
        if _es_titulo_int(t) and _es_subyacente(t):
            ambito = "subyacente"
        elif _es_titulo_int(t):
            ambito = "int"
        else:
            ambito = "local"

        agg["pyg_eco"] += pyg_eco;  agg["ori_eco"] += ori_eco
        agg["pyg_con"] += pyg_con;  agg["ori_con"] += ori_con
        if ambito == "subyacente":
            agg["pyg_sub_eco"] += pyg_eco;  agg["pyg_sub_con"] += pyg_con
            agg["vm_ini_sub"] += _vm_i
        elif ambito == "int":
            agg["pyg_int_eco"] += pyg_eco;  agg["pyg_int_con"] += pyg_con
            agg["vm_ini_int"] += _vm_i
        else:
            agg["pyg_local_eco"] += pyg_eco;  agg["pyg_local_con"] += pyg_con

        filas_det.append({
            "id_posicion": t.id_posicion,
            "especie": t.especie,
            "compania": getattr(t, "compania", ""),
            "asset_class": getattr(t, "asset_class", ""),
            "moneda": getattr(t, "moneda", ""),
            "id_cobertura": str(getattr(t, "id_cobertura", "") or ""),
            "clasificacion": cat,
            "ambito": ambito,
            "vm_inicial": _vm_i,          # mercado PIP t0
            "vtir_inicial": _vtir_i,      # TIR t0
            "vm_mercado_fin": _vm_mkt,
            "vtir_fin": _vtir,
            "cupones": _cup,
            # Vista económica
            "pyg_eco": pyg_eco,
            "ori_eco": ori_eco,
            # Vista contable
            "pyg_con": pyg_con,
            "ori_con": ori_con,
        })
    agg["detalle"] = pd.DataFrame(filas_det)
    agg["por_cia"] = _rf_por_cia(agg["detalle"])
    return agg


def _rf_por_cia(det: pd.DataFrame) -> dict:
    """
    Desglosa el detalle RF por compañía, en las DOS vistas. Por compañía devuelve:
      {pyg_eco, ori_eco, local_eco, int_eco, sub_eco,
       pyg_con, ori_con, local_con, int_con, sub_con,
       vm_ini_int, vm_ini_sub}
    local/int/sub son PyG por ámbito; vm_ini_* es el VM inicial (efecto moneda).
    """
    out: dict = {}
    if det is None or det.empty:
        return out
    for cia, g in det.groupby("compania"):
        gi = g[g["ambito"] == "int"]
        gs = g[g["ambito"] == "subyacente"]
        gl = g[g["ambito"] == "local"]
        out[str(cia)] = {
            # Vista económica
            "pyg_eco": float(g["pyg_eco"].sum()),
            "ori_eco": float(g["ori_eco"].sum()),
            "local_eco": float(gl["pyg_eco"].sum()),
            "int_eco": float(gi["pyg_eco"].sum()),
            "sub_eco": float(gs["pyg_eco"].sum()),
            # Vista contable
            "pyg_con": float(g["pyg_con"].sum()),
            "ori_con": float(g["ori_con"].sum()),
            "local_con": float(gl["pyg_con"].sum()),
            "int_con": float(gi["pyg_con"].sum()),
            "sub_con": float(gs["pyg_con"].sum()),
            # VM inicial internacional (efecto moneda), común a ambas vistas.
            "vm_ini_int": float(gi["vm_inicial"].sum()),
            "vm_ini_sub": float(gs["vm_inicial"].sum()),
        }
    return out


def pnl_rf(port, fecha_corte, fecha_fin, n_meses, sendas, esc, ctx) -> dict:
    """
    RF → P&G del escenario (sin baseline), en DOS vistas. Por título:

        P&G = VM(t+horizonte) − VM(t0) + cupones cobrados

    - ECONÓMICA (claves de primer nivel): VC valorados a MERCADO en t0 y al cierre.
    - CONTABLE  (sub-dict 'contable'):    VC valorados a TIR (costo amortizado, sin ORI).
    NEG y DV son idénticos en ambas. NO se resta contrafactual: es el P&G total del
    horizonte (carry + efecto de mercado) y el consolidado cuadra con el detalle.

    Las anclas t0 (mercado PIP y TIR) se toman precomputadas de ctx si existen
    (ctx['ancla_vm_pip'], ctx['ancla_vtir_tir']); si no, se calculan en _desglose_rf.
    """
    senda_inf = senda_ipc_mensual(esc, ctx, n_meses)   # % EA por mes
    senda_repo = senda_repo_mensual(esc, fecha_corte, n_meses)  # % anual por mes
    # UVR de cierre del escenario: capitaliza UVR t0 con el IPC promedio de la senda.
    ipc_prom = (sum(senda_inf) / len(senda_inf)) / 100.0 if senda_inf else ctx["ipc_t0"]
    uvr_cierre_esc = calcular_uvr_cierre(ctx["uvr_t0"], ipc_prom, n_meses)
    d = _desglose_rf(port, fecha_corte, fecha_fin, n_meses, sendas, esc, ctx,
                     senda_inflacion=senda_inf, senda_repo=senda_repo,
                     uvr_cierre=uvr_cierre_esc,
                     vm_ini=ctx.get("ancla_vm_pip"),
                     vtir_ini=ctx.get("ancla_vtir_tir"))
    por_cia = d.get("por_cia", {})
    # Sub-dict de la vista CONTABLE por compañía (claves *_con -> nombres neutros).
    por_cia_con = {
        cia: {
            "pyg": v.get("pyg_con", 0.0), "ori": v.get("ori_con", 0.0),
            "local": v.get("local_con", 0.0), "int": v.get("int_con", 0.0),
            "sub": v.get("sub_con", 0.0),
            "vm_ini_int": v.get("vm_ini_int", 0.0), "vm_ini_sub": v.get("vm_ini_sub", 0.0),
        }
        for cia, v in por_cia.items()
    }
    return {
        # Vista ECONÓMICA (por defecto en el resumen/VM_Final).
        "pyg": d["pyg_eco"],
        "ori": d["ori_eco"],
        "local": d["pyg_local_eco"],
        "int": d["pyg_int_eco"],
        "sub": d["pyg_sub_eco"],
        "vm_ini_int": d["vm_ini_int"],
        "vm_ini_sub": d["vm_ini_sub"],
        "detalle": d.get("detalle", pd.DataFrame()),
        "por_cia": por_cia,   # trae claves *_eco y *_con por compañía
        # Vista CONTABLE (agregados de primer nivel para el resumen).
        "contable": {
            "pyg": d["pyg_con"],
            "ori": d["ori_con"],
            "local": d["pyg_local_con"],
            "int": d["pyg_int_con"],
            "sub": d["pyg_sub_con"],
            "por_cia": por_cia_con,
        },
    }


def _split_local_int(df: pd.DataFrame, col_tipo: str, val_int) -> dict:
    """
    Descompone P&L (VM_final - VM_inicial) en local vs internacional según el valor
    de `col_tipo`. `val_int` es el valor (o tupla de valores) que marca lo internacional.
    Devuelve {local, int, vm_ini_int} en COP.
    """
    vacio = {"local": 0.0, "int": 0.0, "vm_ini_int": 0.0, "detalle": pd.DataFrame()}
    if df is None or df.empty or "fecha_proyeccion" not in df.columns:
        return vacio
    df = df.copy()
    df["valor_mercado"] = pd.to_numeric(df["valor_mercado"], errors="coerce").fillna(0)
    f_ini = df["fecha_proyeccion"].min()
    f_fin = df["fecha_proyeccion"].max()
    vals_int = val_int if isinstance(val_int, (tuple, list, set)) else (val_int,)
    mask_int = df[col_tipo].isin(vals_int)

    def _pnl(sub):
        vi = sub.loc[sub["fecha_proyeccion"] == f_ini, "valor_mercado"].sum()
        vf = sub.loc[sub["fecha_proyeccion"] == f_fin, "valor_mercado"].sum()
        return float(vf - vi), float(vi)

    pnl_int, vm_ini_int = _pnl(df[mask_int])
    pnl_local, _ = _pnl(df[~mask_int])

    # Detalle por posición: nombre, compania (si existe), tipo, VM inicial/final, P&L.
    col_nom = "titulo" if "titulo" in df.columns else ("fondo" if "fondo" in df.columns else col_tipo)
    llaves = [col_nom, col_tipo]
    if "compania" in df.columns:
        llaves = [col_nom, "compania", col_tipo]
    ini = df[df["fecha_proyeccion"] == f_ini].groupby(llaves, as_index=False)["valor_mercado"].sum()
    fin = df[df["fecha_proyeccion"] == f_fin].groupby(llaves, as_index=False)["valor_mercado"].sum()
    det = ini.merge(fin, on=llaves, how="outer", suffixes=("_ini", "_fin")).fillna(0)
    det["pnl"] = det["valor_mercado_fin"] - det["valor_mercado_ini"]
    det = det.rename(columns={col_nom: "nombre", col_tipo: "tipo",
                              "valor_mercado_ini": "vm_inicial", "valor_mercado_fin": "vm_final"})

    # Desglose por compañía (si el df trae compania): {cia: {local, int, pei, vm_ini_int}}.
    por_cia: dict = {}
    if "compania" in df.columns:
        for cia, g in df.groupby("compania"):
            gi = g[g[col_tipo].isin(vals_int)]
            gl = g[~g[col_tipo].isin(vals_int)]

            def _p(sub):
                a = sub.loc[sub["fecha_proyeccion"] == f_ini, "valor_mercado"].sum()
                b = sub.loc[sub["fecha_proyeccion"] == f_fin, "valor_mercado"].sum()
                return float(b - a), float(a)

            p_int, vmi_int = _p(gi)
            p_loc, _ = _p(gl)
            por_cia[str(cia)] = {"local": p_loc, "int": p_int, "vm_ini_int": vmi_int}
    return {"local": pnl_local, "int": pnl_int, "vm_ini_int": vm_ini_int,
            "detalle": det, "por_cia": por_cia}


def pnl_rv(fecha_corte, fecha_fin, esc, ctx) -> dict:
    """
    RV → P&L (VM_final - VM_inicial) separado en COLCAP local, MXWD internacional y
    PEI (aparte). El motor re-marca el PEI como 'rv_local', así que se identifica por
    su especie (asset_class_definitivo='PEI' en portafolio_clasificado).
    Devuelve {local, int, pei, vm_ini_int, detalle}.
      - local = COLCAP (RV local SIN PEI)
      - int   = MXWD (rv_internacional)
      - pei   = PEI
    """
    df = proyectar_renta_variable(
        fecha_inicio=fecha_corte.strftime("%Y-%m"),
        fecha_fin=fecha_fin.strftime("%Y-%m"),
        escenarios_rv_local={"base": float(esc["rv_local"])},
        escenarios_rv_intl={"base": float(esc["rv_intl"])},
        tasa_pei=float(esc["rv_pei"]),
        ruta_db=str(DB_PATH),
        senda_trm=senda_trm_dict(esc, ctx),
    )
    base = _split_local_int(df, "tipo_rv", "rv_internacional")
    if df is None or df.empty or "fecha_proyeccion" not in df.columns:
        base["pei"] = 0.0
        return base

    # Aislar el PEI por nombre de especie y restarlo del 'local' (que hoy trae COLCAP+PEI).
    especies_pei = _especies_pei(DB_PATH)
    d = df.copy()
    d["valor_mercado"] = pd.to_numeric(d["valor_mercado"], errors="coerce").fillna(0)
    f_ini = d["fecha_proyeccion"].min()
    f_fin = d["fecha_proyeccion"].max()
    mask_pei = d["titulo"].astype(str).str.strip().isin(especies_pei)
    vi = d.loc[mask_pei & (d["fecha_proyeccion"] == f_ini), "valor_mercado"].sum()
    vf = d.loc[mask_pei & (d["fecha_proyeccion"] == f_fin), "valor_mercado"].sum()
    pnl_pei = float(vf - vi)

    base["pei"] = pnl_pei
    base["local"] = base["local"] - pnl_pei  # local queda solo con COLCAP

    # PEI por compañía: restar del local de cada compañía y registrar 'pei'.
    if "compania" in d.columns and base.get("por_cia"):
        pei_por_cia = {}
        for cia, g in d[mask_pei].groupby("compania"):
            a = g.loc[g["fecha_proyeccion"] == f_ini, "valor_mercado"].sum()
            b = g.loc[g["fecha_proyeccion"] == f_fin, "valor_mercado"].sum()
            pei_por_cia[str(cia)] = float(b - a)
        for cia, val in pei_por_cia.items():
            if cia in base["por_cia"]:
                base["por_cia"][cia]["local"] -= val
                base["por_cia"][cia]["pei"] = val
        # Asegurar clave 'pei' en todas las compañías.
        for cia in base["por_cia"]:
            base["por_cia"][cia].setdefault("pei", 0.0)
    return base


def _especies_pei(ruta_db) -> set:
    """Conjunto de especies (titulo) cuyo asset_class_definitivo es 'PEI'."""
    import sqlite3

    try:
        conn = sqlite3.connect(str(ruta_db))
        df = pd.read_sql_query(
            "SELECT DISTINCT especie FROM portafolio_clasificado "
            "WHERE asset_class_definitivo = 'PEI'",
            conn,
        )
        conn.close()
        return set(df["especie"].astype(str).str.strip().tolist())
    except Exception:
        return set()


def _spreads_4g_dict(esc, ruta_db) -> dict | None:
    """
    Construye {id_posicion: spread_4g} para los fondos FCP 4G (es_ipc='SI'), usando
    el spread_4g del escenario. Si no se puede, devuelve None (el motor usa su default).
    """
    try:
        from proyeccion_alternativos import cargar_portafolio_alternativos

        port_alt = cargar_portafolio_alternativos(str(ruta_db))
        ids_ipc = port_alt[port_alt["es_ipc"] == "SI"]["id_posicion"].tolist()
        spread = float(esc["spread_4g"])
        return {int(i): spread for i in ids_ipc}
    except Exception:
        return None


def pnl_alternativos(fecha_corte, fecha_fin, esc, ctx) -> dict:
    """
    Alternativos → P&L (VM_final - VM_inicial) separado local vs internacional.
    Internacional = tipo_fondo 'alt_internacional'. Los FCP 4G (IPC+spread) usan el
    spread_4g del escenario.
    Devuelve {local, int, vm_ini_int, df}.
    """
    periodos = pd.date_range(
        start=f"{fecha_corte.strftime('%Y-%m')}-01",
        end=f"{fecha_fin.strftime('%Y-%m')}-01",
        freq="MS",
    )
    # Senda IPC del escenario (decimal por corte). senda_ipc_mensual entrega % EA.
    n_meses = len(periodos) - 1
    inf_pct = senda_ipc_mensual(esc, ctx, n_meses)
    senda_ipc = {
        p.strftime("%Y-%m-%d"): inf_pct[min(i, len(inf_pct) - 1)] / 100.0
        for i, p in enumerate(periodos)
    }
    df = proyectar_alternativos(
        fecha_inicio=fecha_corte.strftime("%Y-%m"),
        fecha_fin=fecha_fin.strftime("%Y-%m"),
        tasa_ea_local=float(esc["ret_alt_local"]),
        tasa_ea_intl=float(esc["ret_alt_intl"]),
        ruta_db_portafolio=str(DB_PATH),
        senda_ipc=senda_ipc,
        spreads_4g=_spreads_4g_dict(esc, DB_PATH),
        senda_trm=senda_trm_dict(esc, ctx),
    )
    # Enriquecer con compañía (el cargador de alternativos no la trae): cruzar por
    # id_posicion con portafolio_clasificado.
    if df is not None and not df.empty and "id_posicion" in df.columns and "compania" not in df.columns:
        try:
            comp_map = _compania_por_id(DB_PATH)
            df = df.copy()
            df["compania"] = df["id_posicion"].map(comp_map).fillna("")
        except Exception:
            pass
    return _split_local_int(df, "tipo_fondo", "alt_internacional")


def _compania_por_id(ruta_db) -> dict:
    """Mapa {id_posicion: compania} desde portafolio_clasificado."""
    import sqlite3

    try:
        conn = sqlite3.connect(str(ruta_db))
        d = pd.read_sql_query(
            "SELECT id_posicion, compania FROM portafolio_clasificado", conn
        )
        conn.close()
        d["id_posicion"] = pd.to_numeric(d["id_posicion"], errors="coerce")
        return dict(zip(d["id_posicion"], d["compania"].astype(str)))
    except Exception:
        return {}


def pnl_creditos(fecha_corte, fecha_fin, esc) -> float:
    """Créditos → costo acumulado de intereses (signo negativo)."""
    periodos = pd.date_range(
        start=f"{fecha_corte.strftime('%Y-%m')}-01", end=fecha_fin, freq="MS"
    )
    fechas = [p.strftime("%Y-%m-%d") for p in periodos]
    df_ipc = pd.DataFrame({"fecha": fechas, "ipc": [float(esc["cred_ipc"])] * len(fechas)})
    df_ibr = pd.DataFrame({"fecha": fechas, "ibr": [float(esc["cred_ibr"])] * len(fechas)})
    df = proyectar_creditos_contratados(
        fecha_inicio=fecha_corte.strftime("%Y-%m"),
        df_senda_ipc=df_ipc,
        df_senda_ibr=df_ibr,
    )
    if df is None or df.empty or "interes_mes" not in df.columns:
        return 0.0
    # Costo acumulado de intereses → P&L negativo.
    return -float(pd.to_numeric(df["interes_mes"], errors="coerce").fillna(0).sum())


def pnl_derivados(fecha_corte, fecha_fin, esc, ctx) -> float:
    """Derivados → P&L de valorar_portafolio_derivados (suma de p_y_g del último corte)."""
    try:
        from valoracion_derivados import valorar_portafolio_derivados, calcular_pyg
    except Exception as e:
        print(f"  [WARN] Derivados no disponible: {e}")
        return 0.0

    import sqlite3

    trm_actual = ctx["trm_t0"]
    conn = sqlite3.connect(str(DB_PATH))
    try:
        try:
            trm_df = pd.read_sql_query("SELECT trm FROM hist_opciones LIMIT 1", conn)
            trm_actual = float(str(trm_df["trm"].iloc[0]).replace(",", ""))
        except Exception:
            pass
        try:
            df_port = pd.read_sql_query(
                "SELECT moneda, valor_mercado, clase_origen FROM portafolio_clasificado", conn
            )
        except Exception:
            df_port = pd.DataFrame()
    finally:
        conn.close()

    activos_usd, port_pesos = 0.0, 0.0
    if not df_port.empty:
        df_port["valor_mercado"] = pd.to_numeric(df_port["valor_mercado"], errors="coerce").fillna(0)
        df_tit = (
            df_port[df_port["clase_origen"] == "TITULO"]
            if "clase_origen" in df_port.columns
            else df_port
        )
        mask_usd = ~df_tit["moneda"].isin(["COP", "UVR"])
        if trm_actual:
            activos_usd = float(df_tit.loc[mask_usd, "valor_mercado"].sum()) / trm_actual
        port_pesos = float(df_tit.loc[df_tit["moneda"].isin(["COP", "UVR"]), "valor_mercado"].sum())

    # Senda TRM lineal desde TRM vigente a TRM final del escenario.
    trm_fin = float(esc["trm_final"])
    n_meses = (fecha_fin.year - fecha_corte.year) * 12 + (fecha_fin.month - fecha_corte.month)
    senda_trm = list(np.linspace(trm_actual, trm_fin, max(1, n_meses) + 1))

    res = valorar_portafolio_derivados(
        fecha_corte=str(fecha_corte),
        fecha_fin=str(fecha_fin),
        metodo=1,
        nivel_actual=trm_actual,
        activos_usd=activos_usd,
        portafolio_pesos=port_pesos,
        senda_trm=senda_trm,
        canal_opc=CANAL_OPC,
        metodo_params=metodo_params_deriv(esc),
    )
    pyg = calcular_pyg(res)
    trm_fin_esc = float(esc["trm_final"])
    swap_desc = _swap_descomposicion(res.get("swaps", pd.DataFrame()), senda_trm, trm_fin_esc)
    salida = {"Forwards": 0.0, "Opciones": 0.0, "Swaps": 0.0,
              "sabana_forwards": res.get("forwards", pd.DataFrame()),
              "sabana_opciones": res.get("opciones", pd.DataFrame()),
              "sabana_swaps": res.get("swaps", pd.DataFrame()),
              "por_cia": _deriv_por_cia(res),
              "swap_efecto_curva_obl": swap_desc["curva_total"],
              "swap_efecto_moneda_obl": swap_desc["moneda_total"],
              "swap_desc_por_cia": swap_desc["por_cia"],
              "vm_final": _deriv_vm_nominal(res)}
    if pyg is None or pyg.empty:
        return salida
    # P&L del horizonte = p_y_g del último corte de valoración, por tipo.
    ultima = pyg["fecha_valoracion"].max()
    pyg_fin = pyg[pyg["fecha_valoracion"] == ultima]
    for tipo in ("Forwards", "Opciones", "Swaps"):
        salida[tipo] = float(pyg_fin.loc[pyg_fin["tipo"] == tipo, "p_y_g"].sum())
    return salida


# Mapeo prefijo de portafolio -> compañía (igual que PORT_FWD/PORT_OPC del core).
_MAP_PORT_CIA = {"01": "Capitalizadora", "02": "Bolívar", "03": "Comerciales"}


def _cia_de_portafolio(valor) -> str:
    """
    Mapea el portafolio a compañía por su PREFIJO (2 primeros dígitos tras quitar
    espacios): 01=Capitalizadora, 02=Bolívar, 03=Comerciales. Match por prefijo
    exacto (no substring) para evitar ambigüedades.
    """
    s = str(valor).strip()
    pref = s[:2]
    return _MAP_PORT_CIA.get(pref, "")


def _swap_descomposicion(swaps: pd.DataFrame, senda_trm: list, trm_fin: float) -> dict:
    """
    Descompone (informativo) el P&L de la OBLIGACIÓN de los swaps entre el primer
    corte (t0) y el último (t+1). La obligación es un pasivo: si sube de valor, es
    pérdida, por eso se resta (t0 - t+1).

    Reconstruye el VM de la obligación en USD dividiendo el VM en COP (vpn_obl_calc)
    por la TRM del corte correspondiente.

      efecto_curva_obl  = (VM_obl_usd(t0) - VM_obl_usd(t+1)) * trm_fin
      efecto_moneda_obl = (trm_t0 - trm_fin) * VM_obl_usd(t0)

    Devuelve totales y desglose por compañía (mapeada del portafolio).
    """
    vacio = {"curva_total": 0.0, "moneda_total": 0.0, "por_cia": {}}
    if (swaps is None or swaps.empty or "fecha_valoracion" not in swaps.columns
            or "vpn_obl_calc" not in swaps.columns):
        return vacio
    trm_t0 = float(senda_trm[0]) if senda_trm else trm_fin
    d = swaps.copy()
    d["vpn_obl_calc"] = pd.to_numeric(d["vpn_obl_calc"], errors="coerce").fillna(0)
    f_ini = d["fecha_valoracion"].min()
    f_fin = d["fecha_valoracion"].max()
    ini = d[d["fecha_valoracion"] == f_ini].copy()
    fin = d[d["fecha_valoracion"] == f_fin].copy()

    # VM obligación en USD = VM_cop / TRM del corte.
    ini["obl_usd"] = ini["vpn_obl_calc"] / (trm_t0 if trm_t0 else 1.0)
    fin["obl_usd"] = fin["vpn_obl_calc"] / (trm_fin if trm_fin else 1.0)

    # Emparejar por instrumento (id_posicion si existe, si no por portafolio).
    key = "id_posicion" if "id_posicion" in d.columns else "portafolio"
    ini_g = ini.groupby(key).agg(obl_usd_ini=("obl_usd", "sum"),
                                 portafolio=("portafolio", "first")).reset_index()
    fin_g = fin.groupby(key)["obl_usd"].sum().rename("obl_usd_fin").reset_index()
    m = ini_g.merge(fin_g, on=key, how="outer").fillna(0)

    m["efecto_curva"] = (m["obl_usd_ini"] - m["obl_usd_fin"]) * trm_fin
    m["efecto_moneda"] = (trm_t0 - trm_fin) * m["obl_usd_ini"]
    m["_cia"] = m["portafolio"].map(_cia_de_portafolio)

    por_cia = {}
    for cia, g in m.groupby("_cia"):
        if not str(cia).strip():
            continue
        por_cia[str(cia)] = {
            "curva": float(g["efecto_curva"].sum()),
            "moneda": float(g["efecto_moneda"].sum()),
        }
    return {
        "curva_total": float(m["efecto_curva"].sum()),
        "moneda_total": float(m["efecto_moneda"].sum()),
        "por_cia": por_cia,
    }


def _nominal_neto(df, mask=None) -> float:
    """Nominal neto = suma con signo por sentido (Compra +, Venta -)."""
    d = df if mask is None else df[mask]
    if d.empty or "nominal" not in d.columns:
        return 0.0
    signo = d["sentido"].map(lambda s: 1.0 if str(s).strip().lower().startswith("compra") else -1.0)
    return float((pd.to_numeric(d["nominal"], errors="coerce").fillna(0) * signo).sum())


def _solo_vigentes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra a instrumentos VIGENTES en el corte: dias al vencimiento > 0. Si no
    existe la columna 'dias', devuelve el df tal cual (no se puede filtrar).
    """
    if df is None or df.empty or "dias" not in df.columns:
        return df
    return df[pd.to_numeric(df["dias"], errors="coerce").fillna(0) > 0]


def _suma_col(df: pd.DataFrame, col: str, mask=None) -> float:
    """Suma segura de una columna numérica (0 si no existe o df vacío)."""
    if df is None or df.empty or col not in df.columns:
        return 0.0
    d = df if mask is None else df[mask]
    return float(pd.to_numeric(d[col], errors="coerce").fillna(0).sum())


def _deriv_vm_nominal(res: dict) -> dict:
    """
    VM (valoración en COP) y nominal de derivados en el primer y último corte,
    considerando SOLO instrumentos VIGENTES (dias al vencimiento > 0):

      - Forwards: VM = Σ valoracion de forwards vigentes.
                  nominal = Σ delta_monto (delta*monto con signo) de forwards vigentes.
      - Opciones: VM = Σ valoracion de opciones vigentes.
                  nominal = Σ nominal de las PUT vigentes.
                  nominal_delta = Σ delta_monto (delta*monto) de TODAS las opciones
                                  vigentes (put y call).
      - Swaps:    VM = Σ (derecho − obligacion) = Σ valoracion de TODOS los swaps.
                  nominal derecho/obligación (sin cambios).

    Devuelve {tipo: {vm_ini, vm_fin, nom_ini, nom_fin, ...}}.
    """
    out: dict = {}

    def cortes(df):
        f_ini = df["fecha_valoracion"].min()
        f_fin = df["fecha_valoracion"].max()
        return df[df["fecha_valoracion"] == f_ini], df[df["fecha_valoracion"] == f_fin]

    fwd = res.get("forwards")
    if fwd is not None and not fwd.empty and "fecha_valoracion" in fwd.columns:
        ini, fin = cortes(fwd)
        # Solo forwards vigentes (dias > 0).
        ini_v, fin_v = _solo_vigentes(ini), _solo_vigentes(fin)
        out["Forwards"] = {
            "vm_ini": _suma_col(ini_v, "valoracion"),
            "vm_fin": _suma_col(fin_v, "valoracion"),
            "nom_ini": _suma_col(ini_v, "delta_monto"),   # Σ delta*monto vigentes
            "nom_fin": _suma_col(fin_v, "delta_monto"),
        }

    opc = res.get("opciones")
    if opc is not None and not opc.empty and "fecha_valoracion" in opc.columns:
        ini, fin = cortes(opc)
        ini_v, fin_v = _solo_vigentes(ini), _solo_vigentes(fin)

        def _mask_put(df):
            if df is None or df.empty or "call_put" not in df.columns:
                return None
            return df["call_put"].astype(str).str.strip().str.lower() == "put"

        out["Opciones"] = {
            "vm_ini": _suma_col(ini_v, "valoracion"),
            "vm_fin": _suma_col(fin_v, "valoracion"),
            # nominal = Σ nominal de las PUT vigentes.
            "nom_ini": _suma_col(ini_v, "nominal", _mask_put(ini_v)),
            "nom_fin": _suma_col(fin_v, "nominal", _mask_put(fin_v)),
            # nominal_delta = Σ delta*monto de TODAS las opciones vigentes (put + call).
            "nom_delta_ini": _suma_col(ini_v, "delta_monto"),
            "nom_delta_fin": _suma_col(fin_v, "delta_monto"),
        }

    swp = res.get("swaps")
    if swp is not None and not swp.empty and "fecha_valoracion" in swp.columns:
        ini, fin = cortes(swp)
        # Swaps: VM = Σ (derecho − obligacion) = Σ valoracion de TODOS los swaps.
        out["Swaps"] = {
            "vm_ini": _suma_col(ini, "valoracion"),
            "vm_fin": _suma_col(fin, "valoracion"),
            "nom_der_ini": _suma_col(ini, "nominal_der"),
            "nom_der_fin": _suma_col(fin, "nominal_der"),
            "nom_obl_ini": _suma_col(ini, "nominal_obl"),
            "nom_obl_fin": _suma_col(fin, "nominal_obl"),
        }
    return out


def _deriv_por_cia(res: dict) -> dict:
    """
    P&L de derivados por compañía: {cia: {Forwards, Opciones, Swaps}}.
    P&L = valoración(último corte) - valoración(primer corte), agrupado por
    compañía (mapeada del portafolio) y por tipo.
    """
    out: dict = {}
    fuentes = (("Forwards", res.get("forwards")),
               ("Opciones", res.get("opciones")),
               ("Swaps", res.get("swaps")))
    for tipo, df in fuentes:
        if df is None or df.empty or "fecha_valoracion" not in df.columns:
            continue
        if "portafolio" not in df.columns or "valoracion" not in df.columns:
            continue
        d = df.copy()
        d["_cia"] = d["portafolio"].map(_cia_de_portafolio)
        d["valoracion"] = pd.to_numeric(d["valoracion"], errors="coerce").fillna(0)
        f_ini = d["fecha_valoracion"].min()
        f_fin = d["fecha_valoracion"].max()
        vi = d[d["fecha_valoracion"] == f_ini].groupby("_cia")["valoracion"].sum()
        vf = d[d["fecha_valoracion"] == f_fin].groupby("_cia")["valoracion"].sum()
        pnl = vf.subtract(vi, fill_value=0.0)
        for cia, val in pnl.items():
            if not str(cia).strip():
                continue
            out.setdefault(str(cia), {})[tipo] = float(val)
    return out


# =============================================================================
# LECTURA DE ESCENARIOS
# =============================================================================
def leer_escenarios(ruta_input: Path) -> list[dict]:
    """Lee el Excel de entrada. Una fila = un escenario. Valida columnas."""
    if not ruta_input.exists():
        raise FileNotFoundError(
            f"No existe {ruta_input}. Genera la plantilla con:\n"
            f"    python tools/multiescenarios.py --plantilla"
        )
    df = pd.read_excel(ruta_input)
    df.columns = [str(c).strip() for c in df.columns]
    faltantes = [c for c in COLUMNAS_PALANCAS if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas en {ruta_input.name}: {faltantes}")
    escenarios: list[dict] = []
    for _, row in df.iterrows():
        esc = {c: row[c] for c in COLUMNAS_PALANCAS}
        if pd.isna(esc.get("escenario")):
            continue
        # Normalizar numéricos (todo menos la etiqueta 'escenario').
        for c in COLUMNAS_PALANCAS:
            if c == "escenario":
                esc[c] = str(esc[c]).strip()
            else:
                esc[c] = float(pd.to_numeric(esc[c], errors="coerce") or 0.0)
        escenarios.append(esc)
    return escenarios


# =============================================================================
# PLANTILLA DE ENTRADA (2-3 escenarios de ejemplo)
# =============================================================================
def generar_plantilla(ruta_input: Path) -> None:
    """
    Genera escenarios_input.xlsx con escenarios de ejemplo:
    dólar 5000 (estrés alcista) vs 2000 (estrés bajista) vs base.
    Tasas EA en decimal (0.12 = 12%). Shifts en bps.
    """
    ejemplos = [
        {
            "escenario": "Dolar 5000 / curvas +50",
            "trm_final": 5000, "shift_cop_bps": 50, "shift_uvr_bps": 50,
            "shift_fs_bps": 50, "shift_ipc_bps": 50, "shift_subyac_bps": 50,
            "shift_tesoros_bps": 50, "shift_fed_bps": 0,
            "ipc_ini": 0.0584, "ipc_fin": 0.045, "repo_ini": 0.0975, "repo_fin": 0.075,
            "rv_local": -0.40, "rv_intl": -0.40,
            "rv_pei": 0.06, "ret_alt_local": 0.08, "ret_alt_intl": 0.10,
            "spread_4g": 0.035, "cred_ipc": 0.07, "cred_ibr": 0.09,
        },
        {
            "escenario": "Dolar 2000 / curvas -50",
            "trm_final": 2000, "shift_cop_bps": -50, "shift_uvr_bps": -50,
            "shift_fs_bps": -50, "shift_ipc_bps": -50, "shift_subyac_bps": -50,
            "shift_tesoros_bps": -50, "shift_fed_bps": 0,
            "ipc_ini": 0.0584, "ipc_fin": 0.07, "repo_ini": 0.0975, "repo_fin": 0.11,
            "rv_local": 0.40, "rv_intl": 0.40,
            "rv_pei": 0.12, "ret_alt_local": 0.14, "ret_alt_intl": 0.16,
            "spread_4g": 0.035, "cred_ipc": 0.05, "cred_ibr": 0.06,
        },
        {
            "escenario": "Base",
            "trm_final": 4000, "shift_cop_bps": 0, "shift_uvr_bps": 0,
            "shift_fs_bps": 0, "shift_ipc_bps": 0, "shift_subyac_bps": 0,
            "shift_tesoros_bps": 0, "shift_fed_bps": 0,
            "ipc_ini": 0.0584, "ipc_fin": 0.0584, "repo_ini": 0.0975, "repo_fin": 0.0975,
            "rv_local": 0.12, "rv_intl": 0.08,
            "rv_pei": 0.10, "ret_alt_local": 0.10, "ret_alt_intl": 0.12,
            "spread_4g": 0.035, "cred_ipc": 0.0707, "cred_ibr": 0.0707,
        },
    ]
    df = pd.DataFrame(ejemplos, columns=COLUMNAS_PALANCAS)
    ruta_input.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(ruta_input, index=False)
    print(f"[OK] Plantilla generada: {ruta_input}")
    print(f"     {len(df)} escenarios de ejemplo. Edítala y vuelve a correr sin --plantilla.")


# =============================================================================
# WORKER: corre TODOS los bloques de un escenario (para paralelizar)
# =============================================================================
def correr_escenario(esc, fecha_corte, fecha_fin, n_meses, sendas, ctx) -> dict:
    """
    Ejecuta los 5 bloques de P&L de UN escenario y devuelve un dict con resultados
    y detalles. Diseñada para correr en un subproceso (todos los argumentos son
    picklable: ctx NO contiene clausuras). Cada bloque va en su propio try/except.
    """
    ruta_db = str(DB_PATH)
    t_esc = time.time()

    rf = {"pyg": 0.0, "ori": 0.0, "local": 0.0, "int": 0.0, "vm_ini_int": 0.0,
          "detalle": pd.DataFrame()}
    rv = {"local": 0.0, "int": 0.0, "pei": 0.0, "vm_ini_int": 0.0, "detalle": pd.DataFrame()}
    alt = {"local": 0.0, "int": 0.0, "vm_ini_int": 0.0, "detalle": pd.DataFrame()}
    creditos = 0.0
    deriv = {"Forwards": 0.0, "Opciones": 0.0, "Swaps": 0.0}

    try:
        port = cargar_portafolio_proyeccion(ruta_db, fecha_corte.strftime("%Y-%m-%d"))
        rf = pnl_rf(port, fecha_corte, fecha_fin, n_meses, sendas, esc, ctx)
    except Exception as e:
        print(f"  [ERROR] RF ({esc['escenario']}): {e}")
    try:
        rv = pnl_rv(fecha_corte, fecha_fin, esc, ctx)
    except Exception as e:
        print(f"  [ERROR] RV ({esc['escenario']}): {e}")
    try:
        alt = pnl_alternativos(fecha_corte, fecha_fin, esc, ctx)
    except Exception as e:
        print(f"  [ERROR] Alternativos ({esc['escenario']}): {e}")
    try:
        creditos = pnl_creditos(fecha_corte, fecha_fin, esc)
    except Exception as e:
        print(f"  [ERROR] Creditos ({esc['escenario']}): {e}")
    try:
        deriv = pnl_derivados(fecha_corte, fecha_fin, esc, ctx)
    except Exception as e:
        print(f"  [ERROR] Derivados ({esc['escenario']}): {e}")

    # TOTAL = suma de bloques de P&L (NO incluye efecto_moneda, informativo).
    # PEI va aparte (pnl_RV_local ya NO lo incluye) pero SÍ suma al TOTAL.
    total = (
        rf["local"] + rf["int"] + rf.get("sub", 0.0)
        + rv["local"] + rv["int"] + rv.get("pei", 0.0)
        + alt["local"] + alt["int"] + creditos
        + deriv["Forwards"] + deriv["Opciones"] + deriv["Swaps"]
    )

    # Efecto moneda por asset class (informativo): var TRM aplicada al VM inicial de
    # cada bloque del exterior.
    var_trm = (float(esc["trm_final"]) / ctx["trm_t0"]) - 1.0
    fx_rf = rf["vm_ini_int"] * var_trm          # int SIN cobertura
    fx_sub = rf.get("vm_ini_sub", 0.0) * var_trm  # subyacentes cubiertos
    fx_rv = rv["vm_ini_int"] * var_trm
    fx_alt = alt["vm_ini_int"] * var_trm
    fx_total = fx_rf + fx_sub + fx_rv + fx_alt

    _rf_con = rf.get("contable", {})
    fila = dict(esc)
    fila.update({
        # RF vista ECONÓMICA (VC a mercado).
        "pnl_RF_PyG": rf["pyg"],
        "pnl_RF_ORI": rf["ori"],
        "pnl_RF_local": rf["local"],
        "pnl_RF_int": rf["int"],
        "pnl_RF_subyacente": rf.get("sub", 0.0),
        # RF vista CONTABLE (VC a TIR, sin ORI).
        "pnl_RF_PyG_contable": _rf_con.get("pyg", 0.0),
        "pnl_RF_ORI_contable": _rf_con.get("ori", 0.0),
        "pnl_RF_local_contable": _rf_con.get("local", 0.0),
        "pnl_RF_int_contable": _rf_con.get("int", 0.0),
        "pnl_RF_subyacente_contable": _rf_con.get("sub", 0.0),
        "pnl_RV_local": rv["local"],
        "pnl_RV_int": rv["int"],
        "pnl_PEI": rv.get("pei", 0.0),
        "pnl_ALT_local": alt["local"],
        "pnl_ALT_int": alt["int"],
        "pnl_Creditos": creditos,
        "pnl_Forwards": deriv["Forwards"],
        "pnl_Opciones": deriv["Opciones"],
        "pnl_Swaps": deriv["Swaps"],
        "swap_efecto_curva_obl": deriv.get("swap_efecto_curva_obl", 0.0),
        "swap_efecto_moneda_obl": deriv.get("swap_efecto_moneda_obl", 0.0),
        "fx_RF_int": fx_rf,
        "fx_RF_subyacente": fx_sub,
        "fx_RV_int": fx_rv,
        "fx_ALT_int": fx_alt,
        "efecto_moneda": fx_total,
        "pnl_TOTAL": total,
    })

    print(
        f"  [{esc['escenario']}] RF[PyG={rf['pyg']/1e6:,.0f} ORI={rf['ori']/1e6:,.0f} "
        f"loc={rf['local']/1e6:,.0f} int={rf['int']/1e6:,.0f} sub={rf.get('sub',0)/1e6:,.0f}] "
        f"RV[loc={rv['local']/1e6:,.0f} int={rv['int']/1e6:,.0f} pei={rv.get('pei',0)/1e6:,.0f}] "
        f"ALT[loc={alt['local']/1e6:,.0f} int={alt['int']/1e6:,.0f}] "
        f"CRED={creditos/1e6:,.0f} FWD={deriv['Forwards']/1e6:,.0f} "
        f"OPC={deriv['Opciones']/1e6:,.0f} SWP={deriv['Swaps']/1e6:,.0f} "
        f"TOTAL={total/1e6:,.0f} ({time.time()-t_esc:.0f}s)"
    )

    return {
        "fila": fila,
        "det_rf": rf.get("detalle", pd.DataFrame()),
        "det_rv": rv.get("detalle", pd.DataFrame()),
        "det_alt": alt.get("detalle", pd.DataFrame()),
        "filas_cia": _filas_por_compania(esc, rf, rv, alt, deriv, ctx["trm_t0"]),
        "filas_vm": _filas_vm_final(esc, rf, rv, alt, deriv, ctx["trm_t0"]),
        "sab_fwd": deriv.get("sabana_forwards", pd.DataFrame()),
        "sab_opc": deriv.get("sabana_opciones", pd.DataFrame()),
        "sab_swp": deriv.get("sabana_swaps", pd.DataFrame()),
    }


def _filas_por_compania(esc, rf, rv, alt, deriv, trm_t0) -> list:
    """
    Construye una fila por compañía con TODAS las columnas de P&L desglosadas
    (mismas columnas que la hoja Resumen, sin créditos). Créditos se excluye (no
    tiene compañía). Derivados se mapea por portafolio -> compañía.
    """
    rf_c = rf.get("por_cia", {})
    rv_c = rv.get("por_cia", {})
    alt_c = alt.get("por_cia", {})
    der_c = deriv.get("por_cia", {})
    companias = set(rf_c) | set(rv_c) | set(alt_c) | set(der_c)
    companias.discard("")
    var_trm = (float(esc["trm_final"]) / trm_t0) - 1.0

    swap_desc_c = deriv.get("swap_desc_por_cia", {})
    companias |= set(swap_desc_c)
    companias.discard("")

    filas = []
    for cia in sorted(companias):
        r = rf_c.get(cia, {})
        v = rv_c.get(cia, {})
        a = alt_c.get(cia, {})
        dv = der_c.get(cia, {})
        sd = swap_desc_c.get(cia, {})
        fx_rf = r.get("vm_ini_int", 0.0) * var_trm
        fx_sub = r.get("vm_ini_sub", 0.0) * var_trm
        fx_rv = v.get("vm_ini_int", 0.0) * var_trm
        fx_alt = a.get("vm_ini_int", 0.0) * var_trm
        # RF por compañía: vista económica (*_eco) para las columnas base y el TOTAL.
        total = (
            r.get("local_eco", 0.0) + r.get("int_eco", 0.0) + r.get("sub_eco", 0.0)
            + v.get("local", 0.0) + v.get("int", 0.0) + v.get("pei", 0.0)
            + a.get("local", 0.0) + a.get("int", 0.0)
            + dv.get("Forwards", 0.0) + dv.get("Opciones", 0.0) + dv.get("Swaps", 0.0)
        )
        filas.append({
            "escenario": esc["escenario"],
            "compania": cia,
            # RF económica
            "pnl_RF_PyG": r.get("pyg_eco", 0.0),
            "pnl_RF_ORI": r.get("ori_eco", 0.0),
            "pnl_RF_local": r.get("local_eco", 0.0),
            "pnl_RF_int": r.get("int_eco", 0.0),
            "pnl_RF_subyacente": r.get("sub_eco", 0.0),
            # RF contable
            "pnl_RF_PyG_contable": r.get("pyg_con", 0.0),
            "pnl_RF_ORI_contable": r.get("ori_con", 0.0),
            "pnl_RF_local_contable": r.get("local_con", 0.0),
            "pnl_RF_int_contable": r.get("int_con", 0.0),
            "pnl_RF_subyacente_contable": r.get("sub_con", 0.0),
            "pnl_RV_local": v.get("local", 0.0),
            "pnl_RV_int": v.get("int", 0.0),
            "pnl_PEI": v.get("pei", 0.0),
            "pnl_ALT_local": a.get("local", 0.0),
            "pnl_ALT_int": a.get("int", 0.0),
            "pnl_Forwards": dv.get("Forwards", 0.0),
            "pnl_Opciones": dv.get("Opciones", 0.0),
            "pnl_Swaps": dv.get("Swaps", 0.0),
            "swap_efecto_curva_obl": sd.get("curva", 0.0),
            "swap_efecto_moneda_obl": sd.get("moneda", 0.0),
            "fx_RF_int": fx_rf,
            "fx_RF_subyacente": fx_sub,
            "fx_RV_int": fx_rv,
            "fx_ALT_int": fx_alt,
            "efecto_moneda": fx_rf + fx_sub + fx_rv + fx_alt,
            "pnl_TOTAL": total,
        })
    return filas


def _filas_vm_final(esc, rf, rv, alt, deriv, trm_t0) -> list:
    """
    Filas de VM inicial y final por asset_class / tipo, para la hoja VM_Final.
    RF por asset_class; RV/ALT por tipo; derivados por tipo (con nominal neto).

    Para categorías INTERNACIONALES se agrega el nominal en USD:
      nominal_usd_ini = vm_inicial / TRM_t0 ; nominal_usd_fin = vm_final / TRM_final.
    Las categorías locales dejan esas columnas en blanco.
    """
    filas = []
    escn = esc["escenario"]
    trm_fin = float(esc["trm_final"])

    def _usd(vm_ini, vm_fin, es_intl):
        if not es_intl:
            return "", ""
        u_ini = (vm_ini / trm_t0) if trm_t0 else ""
        u_fin = (vm_fin / trm_fin) if trm_fin else ""
        return u_ini, u_fin

    # RF: agrupar el detalle por asset_class. Internacional = ambito 'int' o 'subyacente'.
    det_rf = rf.get("detalle")
    if det_rf is not None and not det_rf.empty:
        g = det_rf.groupby("asset_class").agg(
            vm_inicial=("vm_inicial", "sum"),
            vm_final=("vm_mercado_fin", "sum"),
            n_intl=("ambito", lambda s: (s.isin(["int", "subyacente"])).any())).reset_index()
        for _, r in g.iterrows():
            es_intl = bool(r["n_intl"])
            u_ini, u_fin = _usd(r["vm_inicial"], r["vm_final"], es_intl)
            filas.append({"escenario": escn, "bloque": "RF", "categoria": r["asset_class"],
                          "vm_inicial": r["vm_inicial"], "vm_final": r["vm_final"],
                          "nominal_ini": "", "nominal_fin": "",
                          "nominal_usd_ini": u_ini, "nominal_usd_fin": u_fin})

    # RV / Alternativos: agrupar por tipo. Internacional = tipo con 'internacional'.
    for bloque, d in (("RV", rv.get("detalle")), ("Alternativos", alt.get("detalle"))):
        if d is not None and not d.empty and "tipo" in d.columns:
            g = d.groupby("tipo").agg(vm_inicial=("vm_inicial", "sum"),
                                      vm_final=("vm_final", "sum")).reset_index()
            for _, r in g.iterrows():
                es_intl = "internacional" in str(r["tipo"]).lower()
                u_ini, u_fin = _usd(r["vm_inicial"], r["vm_final"], es_intl)
                filas.append({"escenario": escn, "bloque": bloque, "categoria": r["tipo"],
                              "vm_inicial": r["vm_inicial"], "vm_final": r["vm_final"],
                              "nominal_ini": "", "nominal_fin": "",
                              "nominal_usd_ini": u_ini, "nominal_usd_fin": u_fin})

    # Derivados: VM y nominal neto por tipo (opciones solo Put; swaps derecho/obligación).
    vmd = deriv.get("vm_final", {})
    if "Forwards" in vmd:
        f = vmd["Forwards"]
        filas.append({"escenario": escn, "bloque": "Derivados", "categoria": "Forwards",
                      "vm_inicial": f["vm_ini"], "vm_final": f["vm_fin"],
                      "nominal_ini": f["nom_ini"], "nominal_fin": f["nom_fin"],
                      "nominal_usd_ini": "", "nominal_usd_fin": ""})
    if "Opciones" in vmd:
        o = vmd["Opciones"]
        # Fila 1: VM total de opciones vigentes + nominal = Σ nominal de PUT vigentes.
        filas.append({"escenario": escn, "bloque": "Derivados", "categoria": "Opciones (nom=Put)",
                      "vm_inicial": o["vm_ini"], "vm_final": o["vm_fin"],
                      "nominal_ini": o["nom_ini"], "nominal_fin": o["nom_fin"],
                      "nominal_usd_ini": "", "nominal_usd_fin": ""})
        # Fila 2: nominal delta (Σ delta*monto de TODAS las opciones vigentes, put+call).
        filas.append({"escenario": escn, "bloque": "Derivados",
                      "categoria": "Opciones (delta*monto put+call)",
                      "vm_inicial": "", "vm_final": "",
                      "nominal_ini": o.get("nom_delta_ini", 0.0),
                      "nominal_fin": o.get("nom_delta_fin", 0.0),
                      "nominal_usd_ini": "", "nominal_usd_fin": ""})
    if "Swaps" in vmd:
        s = vmd["Swaps"]
        filas.append({"escenario": escn, "bloque": "Derivados", "categoria": "Swaps (derecho)",
                      "vm_inicial": s["vm_ini"], "vm_final": s["vm_fin"],
                      "nominal_ini": s["nom_der_ini"], "nominal_fin": s["nom_der_fin"],
                      "nominal_usd_ini": "", "nominal_usd_fin": ""})
        filas.append({"escenario": escn, "bloque": "Derivados", "categoria": "Swaps (obligacion)",
                      "vm_inicial": "", "vm_final": "",
                      "nominal_ini": s["nom_obl_ini"], "nominal_fin": s["nom_obl_fin"],
                      "nominal_usd_ini": "", "nominal_usd_fin": ""})
    return filas


def _hoja_detalle_escenario(res: dict) -> pd.DataFrame:
    """
    Arma la hoja de detalle de un escenario: concatena el detalle por posición de
    RF, RV y Alternativos, con una columna 'bloque' que los identifica.
    """
    partes = []
    det_rf = res.get("det_rf")
    if det_rf is not None and not det_rf.empty:
        d = det_rf.copy()
        d.insert(0, "bloque", "RF")
        d = d.rename(columns={"especie": "nombre"})
        partes.append(d)
    for bloque, key in (("RV", "det_rv"), ("Alternativos", "det_alt")):
        d = res.get(key)
        if d is not None and not d.empty:
            d = d.copy()
            d.insert(0, "bloque", bloque)
            partes.append(d)
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


# =============================================================================
# ORQUESTACIÓN PRINCIPAL
# =============================================================================
def ejecutar(ruta_input: Path, ruta_output: Path, n_meses: int,
             workers: int | None = None) -> pd.DataFrame:
    ruta_db = str(DB_PATH)
    t_global = time.time()

    # 1. Fecha de corte vigente.
    fecha_corte = obtener_fecha_corte(ruta_db)
    fecha_fin = fecha_corte + relativedelta(months=n_meses)
    print(f"[CONFIG] Fecha corte: {fecha_corte} | Horizonte: {n_meses}m | Fin: {fecha_fin}")

    # 2. Contexto T0 (vigentes).
    trm_t0 = obtener_trm_t0(fecha_corte)
    uvr_t0 = obtener_uvr_t0(fecha_corte)
    ipc_t0 = obtener_ipc_t0(fecha_corte)
    uvr_cierre = calcular_uvr_cierre(uvr_t0, ipc_t0, n_meses)
    serie_ipc = cargar_serie_ipc_opcional()
    print(f"[CONFIG] T0 → TRM={trm_t0:,.2f} UVR={uvr_t0:.4f} IPC={ipc_t0*100:.2f}% "
          f"UVR_cierre={uvr_cierre:.4f}")

    # 3. Curvas PIP vigentes (una sola vez) + sendas planas.
    curvas_base = extraer_curva_base_pip_db(ruta_db, fecha_corte.strftime("%Y-%m-%d"))
    if not curvas_base:
        raise RuntimeError(
            f"No hay curvas PIP en hist_pip para {fecha_corte}. Carga el PIP de esa fecha."
        )
    sendas = construir_sendas(curvas_base, n_meses)
    print(f"[CONFIG] Curvas PIP: {list(curvas_base.keys())}")

    # Contexto PICKLABLE (sin clausuras) para poder pasarlo a subprocesos.
    ctx = {
        "fecha_corte": fecha_corte,
        "fecha_fin": fecha_fin,
        "trm_t0": trm_t0,
        "uvr_t0": uvr_t0,
        "uvr_cierre": uvr_cierre,
        "ipc_t0": ipc_t0,
        "serie_ipc": serie_ipc,
    }

    # 3b. Bases t0 (una sola vez, reutilizadas por los 3 escenarios). Son dicts
    #     {id_posicion: valor_COP} picklables, así que viajan en ctx a los workers:
    #       - ancla_vm_pip : valoración a MERCADO en t0 (base económica de NEG/VC).
    #       - ancla_vtir_tir: valoración a TIR en t0 (base contable de VC y PyG de DV).
    print("[CONFIG] Calculando bases t0 (mercado PIP y TIR)...")
    _port_t0 = cargar_portafolio_proyeccion(ruta_db, fecha_corte.strftime("%Y-%m-%d"))
    ctx["ancla_vm_pip"] = _anclar_vm_pip_t0(_port_t0, fecha_corte, ctx)
    ctx["ancla_vtir_tir"] = _anclar_vtir_tir_t0(_port_t0, fecha_corte, ctx)
    print(f"[CONFIG] Bases t0 listas: {len(ctx['ancla_vm_pip'])} títulos")

    # 4. Leer escenarios.
    escenarios = leer_escenarios(ruta_input)
    n_esc = len(escenarios)
    print(f"[CONFIG] Escenarios a correr: {n_esc}")

    # 5. Correr escenarios EN PARALELO (cada uno es independiente). Los subprocesos
    #    reciben ctx picklable y sendas.
    if workers is None:
        cpu = os.cpu_count() or 2
        workers = max(1, min(n_esc, cpu - 1))
    print(f"[CONFIG] Paralelismo: {workers} worker(s)")

    resultados = [None] * n_esc
    if workers > 1 and n_esc > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            fut_map = {
                ex.submit(correr_escenario, esc, fecha_corte, fecha_fin, n_meses, sendas, ctx): i
                for i, esc in enumerate(escenarios)
            }
            for fut in as_completed(fut_map):
                i = fut_map[fut]
                resultados[i] = fut.result()
    else:
        for i, esc in enumerate(escenarios):
            print(f"\n[{i+1}/{n_esc}] {esc['escenario']}")
            resultados[i] = correr_escenario(esc, fecha_corte, fecha_fin, n_meses, sendas, ctx)

    # 6. Escribir Excel: hoja Resumen + una hoja de detalle por escenario.
    columnas_pnl = [
        "pnl_RF_PyG", "pnl_RF_ORI", "pnl_RF_local", "pnl_RF_int", "pnl_RF_subyacente",
        "pnl_RF_PyG_contable", "pnl_RF_ORI_contable", "pnl_RF_local_contable",
        "pnl_RF_int_contable", "pnl_RF_subyacente_contable",
        "pnl_RV_local", "pnl_RV_int", "pnl_PEI",
        "pnl_ALT_local", "pnl_ALT_int",
        "pnl_Creditos",
        "pnl_Forwards", "pnl_Opciones", "pnl_Swaps",
        "swap_efecto_curva_obl", "swap_efecto_moneda_obl",
        "fx_RF_int", "fx_RF_subyacente", "fx_RV_int", "fx_ALT_int", "efecto_moneda",
        "pnl_TOTAL",
    ]
    columnas_salida = COLUMNAS_PALANCAS + columnas_pnl
    filas = [r["fila"] for r in resultados if r]
    df_resumen = pd.DataFrame(filas, columns=columnas_salida)

    ruta_output.parent.mkdir(parents=True, exist_ok=True)

    # Resumen por compañía: una fila por (escenario × compañía) con TODAS las columnas
    # de P&L excepto créditos (que no tiene desglose por compañía).
    cols_cia = ["escenario", "compania"] + [c for c in columnas_pnl if c != "pnl_Creditos"]
    filas_cia = []
    for r in resultados:
        if r and r.get("filas_cia"):
            filas_cia.extend(r["filas_cia"])
    df_compania = pd.DataFrame(filas_cia, columns=cols_cia) if filas_cia else pd.DataFrame()

    # VM_Final consolidado: VM inicial/final por asset_class/tipo + nominal de derivados.
    cols_vm = ["escenario", "bloque", "categoria", "vm_inicial", "vm_final",
               "nominal_ini", "nominal_fin", "nominal_usd_ini", "nominal_usd_fin"]
    filas_vm = []
    for r in resultados:
        if r and r.get("filas_vm"):
            filas_vm.extend(r["filas_vm"])
    df_vm = pd.DataFrame(filas_vm, columns=cols_vm) if filas_vm else pd.DataFrame()

    def _escribir(destino: Path) -> None:
        with pd.ExcelWriter(destino, engine="openpyxl") as writer:
            df_resumen.to_excel(writer, sheet_name="Resumen", index=False)
            if not df_compania.empty:
                df_compania.to_excel(writer, sheet_name="Resumen_Compania", index=False)
            if not df_vm.empty:
                df_vm.to_excel(writer, sheet_name="VM_Final", index=False)
            usados = {"Resumen", "Resumen_Compania", "VM_Final"}
            for r in resultados:
                if not r:
                    continue
                nombre = str(r["fila"].get("escenario", "esc"))
                # Hoja de detalle (RF+RV+ALT por posición al cierre).
                hoja = _nombre_hoja(nombre, usados)
                det = _hoja_detalle_escenario(r)
                if det.empty:
                    det = pd.DataFrame({"info": ["Sin detalle disponible"]})
                det.to_excel(writer, sheet_name=hoja, index=False)
                # Sábanas de derivados a TODOS los cortes (fwd, opc, swp).
                for suf, key in (("Fwd", "sab_fwd"), ("Opc", "sab_opc"), ("Swp", "sab_swp")):
                    sab = r.get(key)
                    if sab is not None and not sab.empty:
                        h = _nombre_hoja(f"{nombre[:26]}_{suf}", usados)
                        sab.to_excel(writer, sheet_name=h, index=False)

    try:
        _escribir(ruta_output)
    except PermissionError:
        # El archivo está abierto en Excel (bloqueado). Escribir en alternativo.
        alt = ruta_output.with_name(
            f"{ruta_output.stem}_{time.strftime('%Y%m%d_%H%M%S')}{ruta_output.suffix}"
        )
        print(f"[WARN] {ruta_output.name} está abierto/bloqueado. Escribiendo en {alt.name}")
        _escribir(alt)
        ruta_output = alt

    print(f"\n[OK] Resultado escrito en: {ruta_output}")
    print(f"[OK] {len(df_resumen)} escenarios en {time.time()-t_global:.1f}s")
    return df_resumen


def _nombre_hoja(nombre: str, usados: set) -> str:
    """Sanea un nombre para hoja Excel (<=31 chars, sin []:*?/\\, único)."""
    limpio = "".join(c for c in str(nombre) if c not in "[]:*?/\\").strip()
    limpio = limpio[:31] or "esc"
    base = limpio
    k = 1
    while limpio in usados:
        suf = f"_{k}"
        limpio = base[:31 - len(suf)] + suf
        k += 1
    usados.add(limpio)
    return limpio


def _parse_int_arg(argv: list[str], flag: str, default):
    """Lee '--flag N' o '--flag=N' de la línea de comandos."""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                pass
        if a.startswith(flag + "="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                pass
    return default


def _parse_meses(argv: list[str]) -> int:
    """Lee --meses N de la línea de comandos; default HORIZONTE_MESES."""
    return max(1, _parse_int_arg(argv, "--meses", HORIZONTE_MESES))


def main(argv: list[str]) -> int:
    if "--plantilla" in argv:
        generar_plantilla(RUTA_INPUT)
        return 0
    n_meses = _parse_meses(argv)
    workers = _parse_int_arg(argv, "--workers", None)
    if workers is not None:
        workers = max(1, workers)
    try:
        ejecutar(RUTA_INPUT, RUTA_OUTPUT, n_meses, workers=workers)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
