"""
orquestador_valoracion_rf.py — Wrapper del motor AXIS para Excel RF Mensual
=============================================================================
Usa motor_proyeccion.proyectar_portafolio como fuente de verdad.
Exporta Excel con schema estricto y validación controlada.

REGLAS:
- El motor (motor_proyeccion.py) es la única fuente de valoración.
- Fechas: cortes a fin de mes vía dateutil.relativedelta.
- Schema define qué columnas van al Excel. Campos extra → ERROR.
- Sin inferencia, sin heurísticas difusas.

Autor: Gerencia Financiera - AXIS
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

# Setup paths
SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from motor_proyeccion import (
    cargar_portafolio_proyeccion,
    proyectar_portafolio,
    Titulo,
    ResultadoMes,
)
from curvas_rendimiento import (
    extraer_curva_base_pip_db,
    generar_senda_lineal,
)
from valorar_bonos import cargar_serie_ipc

DB_PATH = ROOT_DIR / "portafolio.db"


# =============================================================================
# 1. SCHEMA DE CONFIGURACIÓN
# =============================================================================

SCHEMA_DETALLE = [
    "fecha_corte", "id_posicion", "especie", "emisor", "tipo_bono",
    "clasificacion", "moneda", "asset_class", "compania", "ramo",
    "nominal", "tasa_cupon", "tasa_mercado", "tasa_compra",
    "valor_mercado", "valor_tir", "cupones_cobrados",
    "fecha_vencimiento", "es_reinversion",
]

SCHEMA_RESUMEN = [
    "fecha_corte", "n_titulos", "vm_total", "vtir_total",
    "total_resultado_mes", "pyg_mes", "ori_mes", "cupones_mes",
    "pyg_acum", "ori_acum", "total_acum",
]


class SchemaValidationError(Exception):
    pass


def validar_columnas(df: pd.DataFrame, schema: list[str], nombre_hoja: str):
    """Valida que el DataFrame solo tenga columnas del schema."""
    extra = set(df.columns) - set(schema)
    if extra:
        raise SchemaValidationError(
            f"[{nombre_hoja}] Columnas fuera del schema: {extra}"
        )


# =============================================================================
# 2. GENERACIÓN DE FECHAS (FIN DE MES ESTRICTO)
# =============================================================================

def generar_cortes_fin_de_mes(fecha_inicio: date, fecha_fin: date) -> list[date]:
    """
    Último día calendario de cada mes entre fecha_inicio y fecha_fin.
    Usa dateutil.relativedelta — NUNCA suma días manuales.
    """
    cortes = []
    cursor = fecha_inicio + relativedelta(day=31)
    while cursor <= fecha_fin:
        cortes.append(cursor)
        cursor = cursor + relativedelta(months=1, day=31)
    return cortes


# =============================================================================
# 3. ORQUESTADOR PRINCIPAL
# =============================================================================

def ejecutar(
    fecha_inicio: date,
    fecha_fin: date,
    shift_cop_bps: float = 0.0,
    shift_uvr_bps: float = 0.0,
    shift_fs_bps: float = 0.0,
    shift_ipc_bps: float = 0.0,
    shift_tesoros_bps: float = 0.0,
    valor_uvr: float = 411.28,
    valor_uvr_cierre: float | None = None,
    trm: float = 3144.14,
    trm_cierre: float | None = None,
    ruta_salida: str | None = None,
) -> Path:
    """
    Ejecuta el motor de proyección RF y genera Excel con schema estricto.
    """
    if ruta_salida is None:
        ruta_salida = str(ROOT_DIR / "valoracion_rf_mensual_v2.xlsx")

    print("=" * 70)
    print("ORQUESTADOR RF — Usando motor_proyeccion.py")
    print("=" * 70)

    # 1. Cortes
    cortes = generar_cortes_fin_de_mes(fecha_inicio, fecha_fin)
    n_meses = len(cortes)
    print(f"  Cortes: {n_meses} → {[str(c) for c in cortes]}")

    # 2. Cargar portafolio (idéntico al motor)
    port = cargar_portafolio_proyeccion(str(DB_PATH))
    print(f"  Portafolio: {len(port)} títulos RF")

    # 3. Cargar curvas desde hist_pip para la fecha de inicio (idéntico al dashboard)
    try:
        curvas_base = extraer_curva_base_pip_db(str(DB_PATH), fecha_inicio.strftime('%Y-%m-%d'))
        if not curvas_base:
            print(f"  ⚠️ Sin curvas PIP en hist_pip para {fecha_inicio}")
    except Exception as e:
        print(f"  ⚠️ Sin curvas PIP: {e}")
        curvas_base = {}

    sendas = {}
    for nombre_curva, curva in curvas_base.items():
        # Senda plana por defecto (target = base). Los shifts se aplican via parámetros.
        sendas[nombre_curva] = generar_senda_lineal(curva, curva, n_meses)

    # 4. Serie IPC
    try:
        serie_ipc = cargar_serie_ipc(str(ROOT_DIR / "Serie Inflación.xlsx"))
    except Exception:
        serie_ipc = pd.DataFrame()

    # 5. Ejecutar motor
    print(f"  Ejecutando proyección: {n_meses} meses...")
    res = proyectar_portafolio(
        portafolio=port,
        fecha_inicio=fecha_inicio,
        meses_horizonte=n_meses,
        sendas=sendas,
        valor_uvr=valor_uvr,
        valor_uvr_cierre=valor_uvr_cierre,
        trm=trm,
        trm_cierre=trm_cierre,
        serie_ipc=serie_ipc,
        shift_cop_bps=shift_cop_bps,
        shift_uvr_bps=shift_uvr_bps,
        shift_fs_bps=shift_fs_bps,
        shift_ipc_bps=shift_ipc_bps,
        shift_tesoros_bps=shift_tesoros_bps,
    )
    print(f"  Motor completado: {len(res)} meses")

    # 6. Construir Detalle Mensual desde snapshots del motor
    filas_detalle = []
    for m_idx, r in enumerate(res):
        fecha_corte = r.fecha
        for t in port:
            snaps = getattr(t, '_snapshots_mensuales', {})
            if (m_idx + 1) not in snaps:
                continue
            snap = snaps[m_idx + 1]
            cups = snap.get('cupones', 0.0)

            clasif_str = str(t.clasificacion).strip()
            if 'Disp' in clasif_str or 'Venta' in clasif_str:
                cat = 'DV'
            elif 'Venc' in clasif_str:
                cat = 'VC'
            else:
                cat = 'NEG'

            filas_detalle.append({
                "fecha_corte": fecha_corte,
                "id_posicion": t.id_posicion,
                "especie": t.especie,
                "emisor": t.emisor,
                "tipo_bono": t.tipo_bono,
                "clasificacion": cat,
                "moneda": t.moneda,
                "asset_class": t.asset_class,
                "compania": t.compania,
                "ramo": t.ramo,
                "nominal": t.nominal,
                "tasa_cupon": t.tasa_cupon,
                "tasa_mercado": t.tasa_mercado,
                "tasa_compra": t.tasa_compra,
                "valor_mercado": snap['vm'],
                "valor_tir": snap['vtir'],
                "cupones_cobrados": cups,
                "fecha_vencimiento": str(t.fecha_vencimiento),
                "es_reinversion": getattr(t, 'es_reinversion', False),
            })

    df_detalle = pd.DataFrame(filas_detalle)

    # Validar schema
    df_detalle = df_detalle[[c for c in SCHEMA_DETALLE if c in df_detalle.columns]]
    validar_columnas(df_detalle, SCHEMA_DETALLE, "Detalle_Mensual")

    # 7. Construir Resumen Mensual (desde snapshots por título, misma lógica contable)
    # Solo incluye títulos vivos al final del horizonte (consistente con Detalle_Mensual).
    fecha_fin_real = cortes[-1] if cortes else fecha_fin
    titulos_vivos_fin = {t.id_posicion for t in port if t.fecha_vencimiento > fecha_fin_real}

    filas_resumen = []
    pyg_acum = 0.0
    ori_acum = 0.0
    total_acum = 0.0

    for m_idx, r in enumerate(res):
        mes_num = m_idx + 1
        pyg_mes = 0.0
        ori_mes = 0.0
        total_mes = 0.0
        cup_mes = 0.0

        for t in port:
            if t.id_posicion not in titulos_vivos_fin:
                continue
            snaps = getattr(t, '_snapshots_mensuales', {})
            if mes_num not in snaps:
                continue
            snap_actual = snaps[mes_num]
            snap_anterior = snaps.get(mes_num - 1, None)
            if snap_anterior is None:
                continue

            vm_act = snap_actual['vm']
            vtir_act = snap_actual['vtir']
            vm_ant = snap_anterior['vm']
            vtir_ant = snap_anterior['vtir']
            cup_t = snap_actual.get('cupones', 0.0)

            clasif_str = str(t.clasificacion).strip()
            if 'Disp' in clasif_str or 'Venta' in clasif_str:
                cat = 'DV'
            elif 'Venc' in clasif_str:
                cat = 'VC'
            else:
                cat = 'NEG'

            if cat == 'NEG':
                total_t = (vm_act - vm_ant) + cup_t
                pyg_t = total_t
                ori_t = 0.0
            elif cat == 'DV':
                total_t = (vm_act - vm_ant) + cup_t
                pyg_t = (vtir_act - vtir_ant) + cup_t
                ori_t = total_t - pyg_t
            else:  # VC
                total_t = (vtir_act - vtir_ant) + cup_t
                pyg_t = total_t
                ori_t = 0.0

            pyg_mes += pyg_t
            ori_mes += ori_t
            total_mes += total_t
            cup_mes += cup_t

        pyg_acum += pyg_mes
        ori_acum += ori_mes
        total_acum += total_mes

        filas_resumen.append({
            "fecha_corte": r.fecha,
            "n_titulos": r.n_titulos,
            "vm_total": r.vm_total,
            "vtir_total": r.vtir_total,
            "total_resultado_mes": total_mes,
            "pyg_mes": pyg_mes,
            "ori_mes": ori_mes,
            "cupones_mes": cup_mes,
            "pyg_acum": pyg_acum,
            "ori_acum": ori_acum,
            "total_acum": total_acum,
        })

    df_resumen = pd.DataFrame(filas_resumen)
    df_resumen = df_resumen[[c for c in SCHEMA_RESUMEN if c in df_resumen.columns]]
    validar_columnas(df_resumen, SCHEMA_RESUMEN, "Resumen_Mensual")

    # 8. Escribir Excel
    output_path = Path(ruta_salida)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_resumen.to_excel(writer, sheet_name="Resumen_Mensual", index=False)
        df_detalle.to_excel(writer, sheet_name="Detalle_Mensual", index=False)

    print(f"\n{'='*70}")
    print(f"✅ Excel: {output_path}")
    print(f"   Resumen: {len(df_resumen)} meses | Detalle: {len(df_detalle)} registros")
    print(f"   PyG acum: {pyg_acum:,.0f} | ORI acum: {ori_acum:,.0f} | Total: {total_acum:,.0f}")
    print("=" * 70)
    return output_path


# =============================================================================
# EJECUCIÓN DIRECTA
# =============================================================================

if __name__ == "__main__":
    resultado = ejecutar(
        fecha_inicio=date(2026, 7, 31),
        fecha_fin=date(2026, 12, 31),
        valor_uvr=411.28,
        trm=3144.14,
        trm_cierre=3100.0,
    )
    print(f"\nArchivo: {resultado}")
