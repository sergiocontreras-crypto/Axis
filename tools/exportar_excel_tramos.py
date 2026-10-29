"""
tools/exportar_excel_tramos.py
==============================
Arma, LEYENDO LA BD (independiente del store del dash), las hojas de DETALLE COMPLETO por
tramo que el usuario necesita en el Excel descargable:

  1. `Portafolio_<corte>`  — el portafolio clasificado proyectado COMPLETO al cierre de cada
     tramo (todas las columnas de `portafolio_clasificado_proy`), una hoja por corte.

  2. `ResumenPyG_<corte>`  — tabla resumen de P&G y ORI por CATEGORÍA de activo × compañía ×
     ramo × clasificación, una hoja por corte (vía `export_resumen_tramos`).

Estas hojas se AÑADEN a un `pd.ExcelWriter` ya abierto por el callback de descarga del dash
(`descargar_resultados_excel`), de modo que el botón "Descargar Excel Detallado" entregue el
detalle completo por tramo además de las hojas que ya producía.

Principios del proyecto:
  - SOLO LECTURA de la BD (`mode=ro`): no interfiere con una corrida en curso ni con el dash.
  - Queries parametrizadas; nombres de hoja saneados (allowlist de caracteres).
  - Entrada NO confiable (fechas de corte de la BD): se sanean antes de usarse como nombre
    de hoja de Excel.
  - NO toca motores ni el snapshot oficial; es extracción/formato puro.

LIMITACIONES CONOCIDAS (documentadas en docs/deuda-tecnica.md):
  - El efecto moneda solo se calcula para RV/Alt internacionales (no para RF Int/Subyacentes).
  - La TRM capturada en el informe puede estar mal escalada (bug conocido); no afecta el
    detalle del portafolio ni el P&G por diferencia de VM en COP.
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd


# Longitud máxima de un nombre de hoja de Excel (límite de openpyxl/Excel).
_MAX_SHEET = 31
# Caracteres prohibidos en nombres de hoja de Excel.
_CHARS_PROHIBIDOS = re.compile(r"[:\\/?*\[\]]")


def _nombre_hoja(prefijo: str, corte: str) -> str:
    """Nombre de hoja seguro: prefijo + corte saneado, truncado a 31 chars."""
    corte_safe = _CHARS_PROHIBIDOS.sub("-", str(corte).strip())
    nombre = f"{prefijo}{corte_safe}"
    return nombre[:_MAX_SHEET]


def _cortes_disponibles(conn) -> list[str]:
    """Cortes presentes en portafolio_clasificado_proy, en orden cronológico."""
    try:
        filas = conn.execute(
            "SELECT DISTINCT fecha_corte_proyeccion FROM portafolio_clasificado_proy "
            "ORDER BY fecha_corte_proyeccion"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [f[0] for f in filas]


def anexar_hojas_detalle_tramos(writer, db_path: str) -> dict:
    """
    Añade al `writer` (pd.ExcelWriter abierto) las hojas de detalle por tramo leídas de la
    BD en `db_path`. Devuelve un dict {nombre_hoja: n_filas} con lo que se escribió.

    No lanza si faltan tablas/datos: simplemente no añade esas hojas (deja rastro en el
    dict de retorno con n=0 o ausencia de la clave).
    """
    escritas: dict = {}

    # ── 1. Portafolio clasificado COMPLETO por corte ──
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cortes = _cortes_disponibles(conn)
        for corte in cortes:
            df = pd.read_sql_query(
                "SELECT * FROM portafolio_clasificado_proy "
                "WHERE fecha_corte_proyeccion = ? ORDER BY clase_origen, compania, ramo",
                conn, params=(corte,),
            )
            if df.empty:
                continue
            hoja = _nombre_hoja("Portafolio_", corte)
            df.to_excel(writer, sheet_name=hoja, index=False)
            escritas[hoja] = len(df)
    finally:
        conn.close()

    # ── 2. Resumen P&G/ORI por categoría × compañía × ramo por corte ──
    # Import diferido (el módulo vive en tools/, igual que este): evita acoplar el import a
    # nivel de módulo y respeta el patrón del proyecto.
    try:
        from export_resumen_tramos import generar_resumen_por_tramo
    except ImportError:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from export_resumen_tramos import generar_resumen_por_tramo

    try:
        resumen_por_corte = generar_resumen_por_tramo(db_path)
    except Exception:
        resumen_por_corte = {}

    # Columnas legibles para el negocio (orden fijo; solo las presentes se escriben).
    _COLS_LEGIBLES = {
        "categoria": "Categoría",
        "compania": "Compañía",
        "ramo": "Ramo",
        "clasificacion": "Clasificación",
        "n": "# Posiciones",
        "vm_inicial": "VM inicial (COP)",
        "vm_final": "VM final (COP)",
        "pyg": "P&G (ER)",
        "ori": "ORI",
        "total_resultado": "Total resultado",
        "efecto_moneda": "Efecto moneda (COP)",
    }
    for corte, df in (resumen_por_corte or {}).items():
        if df is None or df.empty:
            continue
        orden = [c for c in _COLS_LEGIBLES if c in df.columns]
        df_out = df[orden].rename(columns=_COLS_LEGIBLES)
        # Orden de lectura: por categoría, compañía, ramo.
        claves_orden = [c for c in ("Categoría", "Compañía", "Ramo") if c in df_out.columns]
        if claves_orden:
            df_out = df_out.sort_values(claves_orden).reset_index(drop=True)
        hoja = _nombre_hoja("ResumenPyG_", corte)
        df_out.to_excel(writer, sheet_name=hoja, index=False)
        escritas[hoja] = len(df_out)

        # Totales por categoría (una fila por categoría) → hoja compacta por corte.
        if {"Categoría", "P&G (ER)", "ORI"}.issubset(df_out.columns):
            tot = df_out.groupby("Categoría", as_index=False).agg(
                **{
                    "# Posiciones": ("# Posiciones", "sum") if "# Posiciones" in df_out.columns else ("Categoría", "count"),
                    "P&G (ER)": ("P&G (ER)", "sum"),
                    "ORI": ("ORI", "sum"),
                    "Total resultado": ("Total resultado", "sum") if "Total resultado" in df_out.columns else ("P&G (ER)", "sum"),
                }
            )
            hoja_tot = _nombre_hoja("TotalCategoria_", corte)
            tot.to_excel(writer, sheet_name=hoja_tot, index=False)
            escritas[hoja_tot] = len(tot)

    # ── 3. Series MENSUALES consolidadas (RV / Alt / Derivados) de TODOS los tramos ──
    # Las hojas mensuales que salen del store solo traen el ÚLTIMO tramo (el store por
    # tramos no acumula los meses de tramos previos). Aquí se reconstruyen desde
    # `informe_pyg_por_tramo` (payload por corte), concatenando los meses de TODOS los
    # cortes, de modo que el Excel muestre la serie completa (p. ej. jul-2026 → dic-2027).
    _hojas_mensuales = _anexar_series_mensuales_tramos(writer, db_path)
    escritas.update(_hojas_mensuales)

    # ── 4. Detalle de P&G/ORI por TÍTULO de RF por corte (RF_Detalle_Titulo_<corte>) ──
    # El detalle por título (`rf_detalle`) ya se calcula y se persiste por corte en
    # `informe_pyg_por_tramo` (clave "rf_detalle"); aquí solo se LEE y se escribe como hoja
    # por corte, análogo a `Portafolio_<corte>` / `ResumenPyG_<corte>`. No se recalcula.
    _hojas_detalle = _anexar_rf_detalle_titulo_tramos(writer, db_path)
    escritas.update(_hojas_detalle)

    # ── 5. Detalle de DERIVADOS por tipo (swaps/forwards/opciones) por corte ──
    # El detalle completo (`deriv_detalle`) ya se calcula y se persiste por corte en
    # `informe_pyg_por_tramo` (clave "deriv_detalle"); aquí solo se LEE y se vuelca como
    # hojas por corte y tipo, análogo a `_anexar_rf_detalle_titulo_tramos`. No se recalcula.
    _hojas_deriv = _anexar_derivados_detalle_tramos(writer, db_path)
    escritas.update(_hojas_deriv)

    return escritas


def _anexar_series_mensuales_tramos(writer, db_path: str) -> dict:
    """
    Reconstruye, desde `informe_pyg_por_tramo`, las series mensuales de Renta Fija, Renta
    Variable, Alternativos y P&G de derivados de TODOS los cortes (no solo el último), y las
    escribe como hojas `RF_Mensual_Tramos`, `RV_Mensual_Tramos`, `Alt_Mensual_Tramos`,
    `Derivados_PyG_Tramos`.

    La serie RF se reconstruye concatenando la clave `rf_mensual_contable` que el orquestador
    por tramos (`app_dash._ejecutar_escenario_por_tramos`, Tarea 4.3) persiste por corte,
    exactamente igual al patrón ya usado para `rv`/`alt`/`deriv_pyg`. Con el fechado corregido
    del motor (Tarea 4.2) cada tramo aporta ene..dic, de modo que la serie continua suma
    Σ sub_horizonte de todos los tramos sin perder el enero de arranque.

    Lectura RO; parametrizada. Si no existe la tabla o los datos, devuelve {} sin romper.
    """
    import json

    escritas: dict = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            registros = list(conn.execute(
                "SELECT fecha_corte, payload_json FROM informe_pyg_por_tramo "
                "ORDER BY indice"
            ))
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()

    acum_rf: list = []
    acum_rv: list = []
    acum_alt: list = []
    acum_deriv: list = []
    for fecha_corte, payload in registros:
        try:
            reg = json.loads(payload)
        except (TypeError, ValueError):
            continue
        for destino, clave in (
            (acum_rf, "rf_mensual_contable"),
            (acum_rv, "rv"),
            (acum_alt, "alt"),
            (acum_deriv, "deriv_pyg"),
        ):
            filas = reg.get(clave) or []
            for fila in filas:
                if isinstance(fila, dict):
                    # Marca el corte/tramo de origen para trazabilidad en el Excel.
                    fila = {**fila, "tramo_corte": fecha_corte}
                    destino.append(fila)

    def _escribir(acum, hoja, col_fecha_candidatas):
        if not acum:
            return
        df = pd.DataFrame(acum)
        # Ordenar por la columna de fecha disponible para una lectura cronológica.
        col_fecha = next((c for c in col_fecha_candidatas if c in df.columns), None)
        if col_fecha:
            df = df.sort_values([col_fecha]).reset_index(drop=True)
        df.to_excel(writer, sheet_name=hoja, index=False)
        escritas[hoja] = len(df)

    # La serie RF usa la columna `fecha` ('YYYY-MM') que emite
    # rf_contable_mensual.construir_rf_mensual_contable; ordenar por ella da la lectura
    # cronológica continua a través de todos los tramos (ene..dic por año).
    _escribir(acum_rf, "RF_Mensual_Tramos", ("fecha",))
    _escribir(acum_rv, "RV_Mensual_Tramos", ("fecha_proyeccion",))
    _escribir(acum_alt, "Alt_Mensual_Tramos", ("fecha_proyeccion",))
    _escribir(acum_deriv, "Derivados_PyG_Tramos", ("fecha_valoracion",))

    return escritas


def _anexar_rf_detalle_titulo_tramos(writer, db_path: str) -> dict:
    """
    Escribe, desde `informe_pyg_por_tramo`, el DETALLE de P&G/ORI por TÍTULO de Renta Fija de
    CADA corte como una hoja `RF_Detalle_Titulo_<corte>`, análoga a la hoja `RF_Detalle_Titulo`
    del modo punta_a_punta y consistente con `Portafolio_<corte>` / `ResumenPyG_<corte>`.

    El detalle (`rf_detalle`) YA se calcula (`tools/detalle_pyg_tramo.construir_rf_detalle`) y
    se persiste por corte en el payload JSON de `informe_pyg_por_tramo` (clave "rf_detalle"),
    junto a rv/alt/deriv_pyg/rf_mensual_contable. Aquí solo se LEE y se vuelca como hoja: NO se
    recalcula valoración ni se vuelve a correr el motor.

    Mismo patrón que `_anexar_series_mensuales_tramos`:
      - Lectura RO (`file:...?mode=ro`), consulta parametrizada/recorrido del payload JSON.
      - Nombre de hoja saneado con `_nombre_hoja("RF_Detalle_Titulo_", corte)` (límite 31 chars).
      - Falla-segura: si no existe la tabla o un corte no trae `rf_detalle`, se omite sin romper.

    Devuelve {nombre_hoja: n_filas} con lo escrito.
    """
    import json

    escritas: dict = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            registros = list(conn.execute(
                "SELECT fecha_corte, payload_json FROM informe_pyg_por_tramo "
                "ORDER BY indice"
            ))
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()

    for fecha_corte, payload in registros:
        try:
            reg = json.loads(payload)
        except (TypeError, ValueError):
            continue
        filas = reg.get("rf_detalle") or []
        # Solo filas tipo dict (por título); un corte sin detalle (lista vacía) se omite.
        filas = [f for f in filas if isinstance(f, dict)]
        if not filas:
            continue
        df = pd.DataFrame(filas)
        hoja = _nombre_hoja("RF_Detalle_Titulo_", fecha_corte)
        df.to_excel(writer, sheet_name=hoja, index=False)
        escritas[hoja] = len(df)

    return escritas


def _anexar_derivados_detalle_tramos(writer, db_path: str) -> dict:
    """
    Escribe, desde `informe_pyg_por_tramo`, el DETALLE COMPLETO de derivados por tipo
    (swaps/forwards/opciones) de CADA corte como hojas separadas por tipo y corte,
    análogo a `_anexar_rf_detalle_titulo_tramos`.

    El detalle (`deriv_detalle`) YA se captura y se persiste por corte en el payload JSON
    de `informe_pyg_por_tramo` (clave "deriv_detalle" = dict {tipo: records}; ver
    `app_dash._correr_derivados_tramo`). Aquí solo se LEE y se vuelca como hojas: NO se
    recalcula valoración ni se vuelve a correr el motor.

    Hojas por tipo (nombre saneado, límite 31 chars):
      - swaps     → `_nombre_hoja("Swaps_Detalle_", corte)`
      - forwards  → `_nombre_hoja("Fwd_Detalle_", corte)`
      - opciones  → `_nombre_hoja("Opc_Detalle_", corte)`
    Cada hoja trae TODAS las filas (posición × mes) y TODAS las columnas del detalle.

    SOLO para swaps se añaden DOS columnas DERIVADAS antes de escribir:
      - `cupon_neto_acum_cop = cupones_der_acum_cop - cupones_obl_acum_cop`
        (solo si ambas columnas existen).
      - `pyg_corte`: P&G por posición consistente con D1
          = (valoracion - valoracion_base_por_id)
            + (cupon_neto_acum_cop - cupon_neto_base_por_id)
        donde la base por `id_posicion` es el valor del PRIMER corte de ese id. Como el
        detalle por corte trae todos los cortes del tramo, la base se obtiene con
        groupby('id_posicion') ordenado por 'fecha_valoracion' y transform('first').
        `pyg_corte` RECONCILIA con el total de Swaps del ResumenPyG, que ya incluye el
        efecto cupón vía D1 (valoración + cupón neto acumulado).

    Mismo patrón que `_anexar_rf_detalle_titulo_tramos`:
      - Lectura RO (`file:...?mode=ro`), recorrido del payload JSON.
      - Falla-segura: si no existe la tabla, o un corte no trae `deriv_detalle`, o un tipo
        falta/está vacío, se omite sin romper.

    Devuelve {nombre_hoja: n_filas} con lo escrito.
    """
    import json

    escritas: dict = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            registros = list(conn.execute(
                "SELECT fecha_corte, payload_json FROM informe_pyg_por_tramo "
                "ORDER BY indice"
            ))
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()

    # Prefijo de hoja por tipo de derivado.
    _PREFIJO_POR_TIPO = {
        "swaps": "Swaps_Detalle_",
        "forwards": "Fwd_Detalle_",
        "opciones": "Opc_Detalle_",
    }

    for fecha_corte, payload in registros:
        try:
            reg = json.loads(payload)
        except (TypeError, ValueError):
            continue
        detalle = reg.get("deriv_detalle") or {}
        # Un corte sin detalle (dict ausente/vacío o tipo inválido) se omite.
        if not isinstance(detalle, dict) or not detalle:
            continue

        for tipo, prefijo in _PREFIJO_POR_TIPO.items():
            try:
                records = detalle.get(tipo) or []
                # Solo filas tipo dict; un tipo sin registros se omite.
                records = [r for r in records if isinstance(r, dict)]
                if not records:
                    continue
                df = pd.DataFrame(records)
                if df.empty:
                    continue

                # SOLO swaps: columnas derivadas (cupón neto acumulado y P&G por posición).
                if tipo == "swaps":
                    if {"cupones_der_acum_cop", "cupones_obl_acum_cop"}.issubset(df.columns):
                        df["cupon_neto_acum_cop"] = (
                            df["cupones_der_acum_cop"] - df["cupones_obl_acum_cop"]
                        )
                    # P&G por posición consistente con D1: (valoración - base_valoración)
                    # + (cupón neto acum - base_cupón_neto). La base por id_posicion es el
                    # valor del PRIMER corte (menor 'fecha_valoracion') de ese id.
                    if {"id_posicion", "valoracion"}.issubset(df.columns):
                        try:
                            if "fecha_valoracion" in df.columns:
                                df_ord = df.sort_values(["id_posicion", "fecha_valoracion"])
                            else:
                                df_ord = df.sort_values(["id_posicion"])
                            # Reindexar df al orden calculado para alinear las bases.
                            df = df_ord.reset_index(drop=True)
                            base_val = df.groupby("id_posicion")["valoracion"].transform(
                                "first"
                            )
                            pyg = df["valoracion"] - base_val
                            if "cupon_neto_acum_cop" in df.columns:
                                base_cup = df.groupby("id_posicion")[
                                    "cupon_neto_acum_cop"
                                ].transform("first")
                                pyg = pyg + (df["cupon_neto_acum_cop"] - base_cup)
                            df["pyg_corte"] = pyg
                        except Exception:
                            # Falla-segura: si no se puede calcular, se omite la columna.
                            pass

                hoja = _nombre_hoja(prefijo, fecha_corte)
                df.to_excel(writer, sheet_name=hoja, index=False)
                escritas[hoja] = len(df)
            except Exception:
                # Falla-segura por tipo/corte: no interrumpe el resto del volcado.
                continue

    return escritas
