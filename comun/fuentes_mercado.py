"""
comun/fuentes_mercado.py
------------------------
Fuente única de la lógica de vigencia de datos de mercado del proyecto AXIS.

Por ahora centraliza la REGLA DEL 6º DÍA HÁBIL del IPC, que estaba duplicada en
`gestor_informes.obtener_ipc_vigente` y en `scripts/valorar_bonos._obtener_ipc_vigente`
(dos implementaciones sobre el mismo Excel del Banrep). Tener la regla escrita dos
veces es peligroso: si se corrige una y no la otra, el sistema valora distinto según
el camino de código.

Regla del DANE/Banrep:
- El IPC del mes M-1 se publica el 6º día hábil del mes M.
- Por lo tanto, para una fecha de consulta:
    * si fecha >= 6º día hábil de su mes  -> el IPC vigente es el del mes anterior (M-1)
    * si fecha <  6º día hábil de su mes  -> el IPC vigente es el de dos meses antes (M-2)
- La serie del Banrep tiene el IPC con fecha de fin de mes (ej. 2026-03-31 = IPC de marzo).

Este módulo NO lee archivos ni conoce unidades (%, decimal): solo resuelve fechas.
Cada consumidor mantiene su propia carga de serie y su formato de salida.
"""

from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=None)
def sexto_dia_habil(anio: int, mes: int) -> date:
    """
    Devuelve el 6º día hábil de un mes, excluyendo únicamente sábados y domingos.

    No considera festivos (misma convención que las implementaciones originales que
    unifica; cambiar esto alteraría resultados de valoración).

    Rendimiento: memoizado con `lru_cache` por `(anio, mes)`. El 6º día hábil de un mes
    es constante, pero el perfilado del motor mostró ~1.6M evaluaciones de `weekday()`
    provenientes de esta función (regla IPC) dentro de la valoración mes a mes. El caché
    NO altera el resultado (misma fecha para el mismo mes); solo evita recomputarlo.

    Args:
        anio: Año.
        mes: Mes (1-12).

    Returns:
        Fecha del 6º día hábil del mes.
    """
    dia = date(anio, mes, 1)
    habiles = 0
    while habiles < 6:
        if dia.weekday() < 5:  # 0=lunes ... 4=viernes
            habiles += 1
        if habiles < 6:
            dia += timedelta(days=1)
    return dia


def mes_ipc_vigente(fecha_consulta: date) -> tuple[int, int]:
    """
    Resuelve (año, mes) del IPC vigente para una fecha, aplicando la regla del 6º
    día hábil.

    Args:
        fecha_consulta: Fecha para la cual se necesita el IPC vigente.

    Returns:
        Tupla (anio_ipc, mes_ipc) del mes cuyo IPC está vigente en esa fecha.
    """
    sexto = sexto_dia_habil(fecha_consulta.year, fecha_consulta.month)

    if fecha_consulta >= sexto:
        # Ya se publicó el IPC del mes anterior -> vigente = M-1
        mes_ipc = fecha_consulta.month - 1
        anio_ipc = fecha_consulta.year
        if mes_ipc == 0:
            mes_ipc = 12
            anio_ipc -= 1
    else:
        # Aún no se publica -> vigente = M-2
        mes_ipc = fecha_consulta.month - 2
        anio_ipc = fecha_consulta.year
        if mes_ipc <= 0:
            mes_ipc += 12
            anio_ipc -= 1

    return anio_ipc, mes_ipc


def construir_senda_trm_dict(store_sendas, periodos):
    """
    Construye el diccionario {fecha_str 'YYYY-MM-DD': TRM} de la senda TRM a partir
    del store del dashboard, alineado a una lista de periodos mensuales.

    Es el patrón que consumen las proyecciones de renta variable, alternativos y
    créditos (todas reciben `senda_trm` como dict y hacen `.get(fecha)`). Antes estaba
    copiado en varios callbacks de app_dash; se centraliza aquí.

    Reglas (idénticas al comportamiento original):
    - Si no hay store o no trae columna 'TRM', devuelve None.
    - Para cada periodo i: usa la TRM de la fila i del store; si el store tiene menos
      filas que periodos, repite (clamp) el último valor disponible.
    - Ante cualquier error de parseo, devuelve None (fallback silencioso, como antes).

    Args:
        store_sendas: lista de dicts (records del store del dashboard) con clave 'TRM'.
        periodos: iterable de fechas (pd.Timestamp/datetime) con método strftime.

    Returns:
        dict {fecha_str: float} o None.
    """
    import pandas as pd  # import local: comun no debe exigir pandas si no se usa esto

    if not store_sendas:
        return None
    try:
        df = pd.DataFrame(store_sendas)
        if 'TRM' not in df.columns:
            return None
        df['TRM'] = pd.to_numeric(df['TRM'], errors='coerce')
        senda = {}
        n = len(df)
        for i, p in enumerate(periodos):
            idx = i if i < n else n - 1
            senda[p.strftime("%Y-%m-%d")] = float(df['TRM'].iloc[idx])
        return senda
    except Exception:
        return None
