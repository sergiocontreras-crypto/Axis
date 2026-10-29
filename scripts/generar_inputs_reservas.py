# -*- coding: utf-8 -*-
"""
================================================================================
GENERADOR DE INPUTS DE RESERVAS PARA EL DASHBOARD AXIS
================================================================================

PROPOSITO
---------
Transforma el insumo operativo de reservas (saldos mensuales + interes a la
reserva) al formato que consume el dashboard de encaje de AXIS
(`Inputs_Planeacion_Reservas.xlsx`).

Este script NO se ejecuta solo. Debe correrse manualmente cada vez que se
actualice el insumo de saldos, para regenerar el archivo que el dashboard lee.

FLUJO DE DATOS
--------------
    ENTRADA (fuente operativa)                 SALIDA (la lee el dashboard)
    Plantillas Input/                    ->    AXIS/
    Inputs_Reservas e interes a la              Inputs_Planeacion_Reservas.xlsx
    reserva actualizado.xlsx                    (hojas Capitalizadora/Bolivar/
    (hojas 'Saldo Reservas' e                    Comerciales; filas
     'Interés a la Reserva')                     Cambio_reserva + Interes_reserva)

COMO USARLO
-----------
    # Desde la raiz del proyecto:
    python AXIS/scripts/generar_inputs_reservas.py

    # O importable:
    from generar_inputs_reservas import generar
    generar()

El dashboard (AXIS/app_dash.py) lee el resultado con la ruta:
    PROJECT_ROOT / "Inputs_Planeacion_Reservas.xlsx"
donde PROJECT_ROOT es la carpeta AXIS/. Por eso la salida se escribe ahi.

--------------------------------------------------------------------------------
REGLAS DE NEGOCIO (confirmadas con Gerencia Financiera)
--------------------------------------------------------------------------------

1. HOMOLOGACION DE NOMBRES (ambos tipos):
   Los ramos/segmentos del insumo se traducen a los nombres del diccionario de
   portafolios usando HOMOLOGACION_RESERVAS_A_DICCIONARIO (definida en
   calcular_encaje.py). Esto es OBLIGATORIO: el dashboard cruza estos nombres
   contra las reservas actuales (que tambien vienen homologadas). Si no se
   homologa, el cruce falla silenciosamente.

2. CAMBIO_RESERVA (hoja 'Saldo Reservas' del insumo):
   El insumo trae SALDOS absolutos por mes (col C = Dic-2025 base, cols D..O =
   Ene..Dic 2026). El dashboard espera VARIACIONES (cambios netos), asi que:
       Cambio_neto(mes) = Saldo(mes) - Saldo(mes-1)
   Enero usa Dic-2025 como base. Los valores se convierten a MILLONES (/1e6)
   porque el dashboard multiplica x1e6 al acumular.

   Reglas de agrupacion por bloque:
     - BOLIVAR:        ramos individuales homologados (filas 3-21).
     - COMERCIALES:    ramos individuales homologados (filas 25-46). Para el
                       destino 'DEMAS RAMOS DE SEGUROS GENERALES' aplica REGLA B:
                       si los sub-ramos (CORRIENTE DEBIL, LUCROCESANTE, etc.)
                       tienen datos, su SUMA REEMPLAZA a la fila DEMAS (los
                       sub-ramos son el detalle del consolidado -> evitar doble
                       conteo). Hoy los sub-ramos estan vacios, asi que se usa
                       la fila DEMAS tal cual.
     - CAPITALIZADORA: se usa la fila 'TOTAL CAPI' (fila 53) -> CAPITALIZADORA,
                       porque los sub-ramos (DABUENAVIDA/MAGNATE/GANAMAS) solo
                       tienen datos hasta julio; el TOTAL CAPI tiene la serie
                       completa.

3. INTERES_RESERVA (hoja 'Interés a la Reserva' del insumo):
   Trae el interes MENSUAL por segmento (ya es el valor del mes, NO se calcula
   diferencia). Valores YA en millones de COP. Mapeo segmento -> ramo confirmado
   en MAPEO_INTERES_A_RAMO (abajo). Las filas 'Bolivar' (agregado) y
   'TOTAL COMPAÑIAS' (control) se IGNORAN para no duplicar.

4. ESCENARIO: por ahora solo se puebla la columna 'Base'. Las columnas
   Negativo/Positivo/Alternativos se dejan vacias.

--------------------------------------------------------------------------------
FORMATO DE SALIDA (replicado del original; el codigo hace skiprows=3)
--------------------------------------------------------------------------------
    R1: [NombreHoja, None, 'Escenario', 'Base', 'Negativo', 'Positivo',
         'Esc Altern 1', 'Esc Altern 2', 'Esc Altern 3']
    R2: [None, None, 'Año proyectado', 2026, 2026, 2026, 2026, 2026, 2026]
    R3: ['A Reportar', 'Ramo', 'Mes Proyectado', 'Valor Millones de COP' x6]
    R4+: [<Tipo>, <ramo homologado>, <mes>, <valor Base>, (resto vacio)]
         donde <Tipo> es 'Cambio_reserva' o 'Interes_reserva'.

Autor: Gerencia Financiera - AXIS
"""

import sys
from pathlib import Path
import openpyxl

# ------------------------------------------------------------------------------
# RUTAS
# ------------------------------------------------------------------------------
# Este script vive en AXIS/scripts/. La carpeta AXIS/ es PROJECT_ROOT del
# Este script vive en <raiz>/scripts/. PROYECTO_DIR es la raiz del proyecto
# (un nivel por encima de scripts/), que tambien es el PROJECT_ROOT que usa
# app_dash.py de la raiz.
SCRIPT_DIR = Path(__file__).resolve().parent            # <raiz>/scripts
PROYECTO_DIR = SCRIPT_DIR.parent                        # raiz del proyecto

# Insumo operativo (fuente). Se mantiene en 'Plantillas Input' de la raiz.
RUTA_INSUMO = PROYECTO_DIR / "Plantillas Input" / "Inputs_Reservas e interes a la reserva actualizado.xlsx"

# Salida que consume el dashboard (misma carpeta que app_dash.py, la raiz).
RUTA_SALIDA = PROYECTO_DIR / "Inputs_Planeacion_Reservas.xlsx"

# Fuente única de verdad del mapeo de ramos: tablas SQLite editables leídas vía el
# Lector_Mapeo de compras_ventas_reservas.py. Ya NO se importa el diccionario
# hardcodeado HOMOLOGACION_RESERVAS_A_DICCIONARIO (deprecado).
sys.path.insert(0, str(SCRIPT_DIR))
from compras_ventas_reservas import cargar_mapeo_saldo, cargar_mapeo_interes


def _normalizar_ramo_local(ramo: str) -> str:
    """Normaliza una etiqueta a clave (strip/upper), igual que en la Fuente_Unica."""
    return str(ramo).strip().upper()


def _derivar_compania_interes(ramo: str) -> str:
    """
    Deriva la compañía de una línea de interés a partir de su ramo de portafolio.

    La hoja 'Interés a la Reserva' solo contiene Bolívar + Capitalizadora, por lo que
    la compañía se deriva por regla ramo→compañía sin hardcodear el mapa completo:
    CAPITALIZADORA para el ramo agregado CAPITALIZADORA; BOLIVAR en cualquier otro caso.
    Esto replica el comportamiento del antiguo MAPEO_INTERES_A_RAMO (Req 4.4, 8.1).
    """
    return "CAPITALIZADORA" if str(ramo).strip().upper() == "CAPITALIZADORA" else "BOLIVAR"

# ------------------------------------------------------------------------------
# CONSTANTES DE ESTRUCTURA DEL INSUMO (hoja 'Saldo Reservas')
# ------------------------------------------------------------------------------
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Bloques de compania y su rango de filas (col B = nombre ramo).
# Confirmado por inspeccion del insumo.
BLOQUES_SALDO = {
    "BOLIVAR": (3, 21),
    "COMERCIALES": (25, 46),
}
FILA_TOTAL_CAPI = 53                 # Capitalizadora se toma de esta fila
DESTINO_DEMAS = "DEMAS RAMOS DE SEGUROS GENERALES"
FILAS_TOTAL_IGNORAR = {"TOTAL BOLIVAR", "TOTAL COMERCIALES", "TOTAL CAPI", "TOTAL COMPAÑIAS"}

# Indices de columna (0-based) en la hoja 'Saldo Reservas':
#   col C (idx 2) = Dic-2025 (base para el cambio de enero)
#   col D..O (idx 3..14) = Ene..Dic 2026
COL_DIC_PREV = 2
COLS_MESES_SALDO = list(range(3, 15))   # 12 columnas

# ------------------------------------------------------------------------------
# MAPEO DE INTERES A LA RESERVA -> RAMO
# ------------------------------------------------------------------------------
# El mapeo segmento→ramo ahora vive en la Fuente_Unica_De_Verdad (tabla SQLite
# mapeo_ramos_interes_reservas) y se consulta vía cargar_mapeo_interes(). La compañía
# se deriva del ramo destino con _derivar_compania_interes(). El diccionario local
# MAPEO_INTERES_A_RAMO fue eliminado (deprecación, Req 4.4/5.2).
#
# Estructura de la hoja 'Interés a la Reserva':
#   R2 = encabezado (col B = titulo, cols C..N = Ene..Dic)
#   R3..R11 = segmentos ; R12 = TOTAL COMPAÑIAS
COL_SEGMENTO_INTERES = 1                 # col B (idx 1) = nombre del segmento
COLS_MESES_INTERES = list(range(2, 14))  # col C..N (idx 2..13) = Ene..Dic

# ------------------------------------------------------------------------------
# FORMATO DE SALIDA
# ------------------------------------------------------------------------------
HOJA_DE_COMPANIA = {"CAPITALIZADORA": "Capitalizadora", "BOLIVAR": "Bolivar", "COMERCIALES": "Comerciales"}
ORDEN_HOJAS = ["Capitalizadora", "Bolivar", "Comerciales"]
ENC_ESCENARIOS = ["Base", "Negativo", "Positivo", "Esc Altern 1", "Esc Altern 2", "Esc Altern 3"]


# ==============================================================================
# LECTURA Y CALCULO: CAMBIO_RESERVA (saldos -> cambios netos en millones)
# ==============================================================================
def _num(v):
    """Convierte a float si es numerico; si no, None."""
    return float(v) if isinstance(v, (int, float)) else None


def leer_saldos(filas, mapeo_saldo=None):
    """
    Lee la hoja 'Saldo Reservas' (ya cargada en `filas`) y devuelve:
        { (compania, ramo_homologado): [13 saldos]  # idx 0 = Dic25, 1..12 = Ene..Dic }
    aplicando homologacion y las reglas de agrupacion por bloque.

    Args:
        filas: dict {n_fila: tupla_de_celdas} de la hoja 'Saldo Reservas'.
        mapeo_saldo: `MapeoDeReservas` de saldos (Fuente_Unica_De_Verdad). Si es None se
            carga vía `cargar_mapeo_saldo`. Las etiquetas mapeadas a destino vacío ("")
            se IGNORAN (p. ej. RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY).
    """
    if mapeo_saldo is None:
        mapeo_saldo = cargar_mapeo_saldo(str(RUTA_INSUMO))

    saldos = {}

    def serie(row):
        """Extrae los 13 saldos (Dic25 + 12 meses) de una fila."""
        vals = [_num(row[COL_DIC_PREV] if len(row) > COL_DIC_PREV else None)]
        for j in COLS_MESES_SALDO:
            vals.append(_num(row[j] if len(row) > j else None))
        return vals

    def acumular(compania, destino, s):
        """Suma la serie `s` al acumulado de (compania, destino)."""
        key = (compania, destino)
        if key not in saldos:
            saldos[key] = list(s)
        else:
            prev = saldos[key]
            saldos[key] = [
                (0.0 if a is None else a) + (0.0 if b is None else b)
                if not (a is None and b is None) else None
                for a, b in zip(prev, s)
            ]

    # --- BOLIVAR y COMERCIALES: ramos individuales ---
    for compania, (r_ini, r_fin) in BLOQUES_SALDO.items():
        demas_fila = None       # fila 'DEMAS RAMOS...' propia (consolidado actual)
        demas_subramos = []     # sub-ramos que homologan a DEMAS y tienen datos
        for r in range(r_ini, r_fin + 1):
            row = filas.get(r, ())
            nombre = row[1] if len(row) > 1 else None
            if nombre is None:
                continue
            nombre_str = str(nombre).strip()
            if not nombre_str or nombre_str in FILAS_TOTAL_IGNORAR:
                continue

            s = serie(row)
            tiene_datos = any(v is not None for v in s)
            # Etiquetas mapeadas explícitamente a destino vacío se ignoran (Req 6.3).
            if mapeo_saldo.ignora(nombre_str):
                continue
            destino = mapeo_saldo.destino(nombre_str)  # homologar (o dejar igual)

            # REGLA B para DEMAS RAMOS DE SEGUROS GENERALES (solo Comerciales)
            if compania == "COMERCIALES" and destino == DESTINO_DEMAS:
                if nombre_str == DESTINO_DEMAS:
                    demas_fila = s               # es la fila consolidada propia
                elif tiene_datos:
                    demas_subramos.append(s)     # sub-ramo con datos
                continue

            if not tiene_datos:
                continue
            acumular(compania, destino, s)

        # Resolver DEMAS con regla B
        if compania == "COMERCIALES":
            if demas_subramos:
                # Los sub-ramos definen el valor (reemplazan la fila DEMAS)
                s_final = [None] * 13
                for s in demas_subramos:
                    for k in range(13):
                        if s[k] is not None:
                            s_final[k] = (s_final[k] or 0.0) + s[k]
                acumular("COMERCIALES", DESTINO_DEMAS, s_final)
            elif demas_fila is not None:
                acumular("COMERCIALES", DESTINO_DEMAS, demas_fila)

    # --- CAPITALIZADORA: usar la fila TOTAL CAPI (serie completa) ---
    row_capi = filas.get(FILA_TOTAL_CAPI, ())
    if row_capi:
        acumular("CAPITALIZADORA", "CAPITALIZADORA", serie(row_capi))

    return saldos


def calcular_cambios(saldos):
    """
    De saldos [Dic25, Ene..Dic] -> cambios netos mensuales en MILLONES.
        Cambio[mes] = Saldo[mes] - Saldo[mes-1]   (enero usa Dic25)
    Devuelve { (compania, ramo): [12 valores en millones o None] }.
    """
    cambios = {}
    for (compania, ramo), s in saldos.items():
        fila = []
        for m in range(1, 13):        # idx 1..12 = Ene..Dic
            actual, previo = s[m], s[m - 1]
            if actual is None or previo is None:
                fila.append(None)
            else:
                fila.append((actual - previo) / 1_000_000.0)   # pesos -> millones
        cambios[(compania, ramo)] = fila
    return cambios


# ==============================================================================
# LECTURA: INTERES_RESERVA (valores mensuales directos, ya en millones)
# ==============================================================================
def leer_interes(ws_interes, mapeo_interes=None):
    """
    Lee la hoja 'Interés a la Reserva' y devuelve:
        { (compania, ramo_homologado): [12 valores en millones o None] }
    usando el `MapeoDeReservas` de interés (Fuente_Unica_De_Verdad). Ignora 'Bolivar'
    (agregado), 'TOTAL COMPAÑIAS' (control) y cualquier segmento sin entrada explícita
    en el mapeo. El valor mensual se usa TAL CUAL (no se calcula diferencia).

    Args:
        ws_interes: worksheet 'Interés a la Reserva'.
        mapeo_interes: `MapeoDeReservas` de interés. Si es None se carga vía
            `cargar_mapeo_interes`. La compañía se deriva del ramo destino.
    """
    if mapeo_interes is None:
        mapeo_interes = cargar_mapeo_interes(str(RUTA_INSUMO))

    interes = {}
    for row in ws_interes.iter_rows(min_row=3, values_only=True):
        seg = row[COL_SEGMENTO_INTERES] if len(row) > COL_SEGMENTO_INTERES else None
        if seg is None:
            continue
        seg_str = str(seg).strip()
        # Solo se procesan segmentos con entrada EXPLÍCITA y no vacía en el mapeo.
        # 'Bolivar', 'TOTAL COMPAÑIAS' u otros no mapeados -> ignorar.
        clave = _normalizar_ramo_local(seg_str)
        ramo = mapeo_interes.entradas.get(clave)
        if not ramo:  # None (ausente) o "" (ignorar)
            continue
        compania = _derivar_compania_interes(ramo)
        vals = []
        for j in COLS_MESES_INTERES:
            vals.append(_num(row[j] if len(row) > j else None))  # ya en millones
        interes[(compania, ramo)] = vals
    return interes


# ==============================================================================
# ESCRITURA DEL ARCHIVO DE SALIDA
# ==============================================================================
def _escribir_encabezado(ws, nombre_hoja):
    """Escribe las 3 filas de encabezado que el codigo salta con skiprows=3."""
    ws.cell(row=1, column=1, value=nombre_hoja)
    ws.cell(row=1, column=3, value="Escenario")
    for i, esc in enumerate(ENC_ESCENARIOS):
        ws.cell(row=1, column=4 + i, value=esc)
    ws.cell(row=2, column=3, value="Año proyectado")
    for i in range(len(ENC_ESCENARIOS)):
        ws.cell(row=2, column=4 + i, value=2026)
    ws.cell(row=3, column=1, value="A Reportar")
    ws.cell(row=3, column=2, value="Ramo")
    ws.cell(row=3, column=3, value="Mes Proyectado")
    for i in range(len(ENC_ESCENARIOS)):
        ws.cell(row=3, column=4 + i, value="Valor Millones de COP")


def _escribir_filas(ws, fila_inicial, tipo, datos_por_ramo):
    """
    Escribe filas de un tipo ('Cambio_reserva' o 'Interes_reserva').
    datos_por_ramo: { ramo: [12 valores] }. Solo puebla la columna Base (col 4).
    Devuelve la siguiente fila libre.
    """
    r = fila_inicial
    for ramo in sorted(datos_por_ramo.keys()):
        valores = datos_por_ramo[ramo]
        for m_idx, mes in enumerate(MESES):
            ws.cell(row=r, column=1, value=tipo)
            ws.cell(row=r, column=2, value=ramo)
            ws.cell(row=r, column=3, value=mes)
            val = valores[m_idx]
            if val is not None:
                ws.cell(row=r, column=4, value=round(float(val), 6))
            r += 1
    return r


def generar(ruta_insumo=RUTA_INSUMO, ruta_salida=RUTA_SALIDA, verbose=True):
    """
    Ejecuta la transformacion completa y escribe el archivo de salida.
    Devuelve un dict con un resumen (filas escritas por hoja).
    """
    if not Path(ruta_insumo).exists():
        raise FileNotFoundError("No se encontro el insumo: %s" % ruta_insumo)

    wb_in = openpyxl.load_workbook(str(ruta_insumo), data_only=True, read_only=True)
    ws_saldo = wb_in["Saldo Reservas"]
    ws_interes = wb_in["Interés a la Reserva"]

    # Cargar filas de saldos en memoria (indexadas por numero de fila)
    filas_saldo = {}
    for i, row in enumerate(ws_saldo.iter_rows(min_row=1, max_row=60, values_only=True), start=1):
        filas_saldo[i] = row

    # Cargar el mapeo una sola vez desde la Fuente_Unica_De_Verdad y reutilizarlo.
    mapeo_saldo = cargar_mapeo_saldo(str(RUTA_INSUMO))
    mapeo_interes = cargar_mapeo_interes(str(RUTA_INSUMO))
    saldos = leer_saldos(filas_saldo, mapeo_saldo)
    cambios = calcular_cambios(saldos)
    interes = leer_interes(ws_interes, mapeo_interes)
    wb_in.close()

    # Agrupar por compania
    cambios_por_comp = {}
    for (comp, ramo), fila in cambios.items():
        cambios_por_comp.setdefault(comp, {})[ramo] = fila
    interes_por_comp = {}
    for (comp, ramo), fila in interes.items():
        interes_por_comp.setdefault(comp, {})[ramo] = fila

    # Construir el libro de salida
    wb_out = openpyxl.Workbook()
    wb_out.remove(wb_out.active)

    resumen = {}
    for hoja in ORDEN_HOJAS:
        comp_up = [k for k, v in HOJA_DE_COMPANIA.items() if v == hoja][0]
        ws = wb_out.create_sheet(title=hoja)
        _escribir_encabezado(ws, hoja)

        # Primero Cambio_reserva, luego Interes_reserva (misma convencion que el original)
        r = 4
        n_ini = r
        r = _escribir_filas(ws, r, "Cambio_reserva", cambios_por_comp.get(comp_up, {}))
        filas_cambio = r - n_ini
        n_ini2 = r
        r = _escribir_filas(ws, r, "Interes_reserva", interes_por_comp.get(comp_up, {}))
        filas_interes = r - n_ini2

        resumen[hoja] = {
            "ramos_cambio": len(cambios_por_comp.get(comp_up, {})),
            "filas_cambio": filas_cambio,
            "ramos_interes": len(interes_por_comp.get(comp_up, {})),
            "filas_interes": filas_interes,
        }

    wb_out.save(str(ruta_salida))

    if verbose:
        print("Archivo generado: %s" % ruta_salida)
        for hoja, info in resumen.items():
            print("  %-14s | Cambio_reserva: %d ramos (%d filas) | Interes_reserva: %d ramos (%d filas)" % (
                hoja, info["ramos_cambio"], info["filas_cambio"],
                info["ramos_interes"], info["filas_interes"]))

    return resumen


if __name__ == "__main__":
    generar()
