"""
Property 22: Round-trip de persistencia de la auditoría.

**Validates: Requirements 5.3**

Para toda lista de `Registro_Auditoria` generada durante una corrida, tras
persistirla en `audit_derivados_corrida` (vía `persistir_auditoria_derivados`) y
releerla filtrando por `corrida_id`, el conjunto de registros leídos es igual al
conjunto generado (sin re-ejecutar la valoración).

Notas de diseño verificadas contra `valoracion_derivados.py`:
- `persistir_auditoria_derivados` crea la tabla con `CREATE TABLE IF NOT EXISTS`
  usando el esquema EXACTO del diseño y hace binding posicional (`?`) de TODOS los
  valores; los nombres de columna provienen del allowlist fijo
  `_COLUMNAS_AUDIT_DERIVADOS`, nunca de datos de entrada (Req 5.2).
- Al persistir, `corrida_id` se inyecta desde el argumento, `uso_fallback` se
  normaliza a 0/1 (INTEGER NOT NULL) y las claves ausentes se guardan como NULL.

Por eso el "conjunto generado" con el que se compara la relectura es la proyección
canónica de cada registro al esquema (misma normalización que aplica la escritura):
esto es precisamente lo que debe recuperarse intacto tras el round-trip.

Se incluyen valores adversarios de inyección SQL en los campos de texto para
confirmar que el binding parametrizado los almacena de forma literal, sin alterar
otras filas ni el esquema de la tabla.

La DB es un archivo SQLite temporal creado con `tempfile` (nunca la
`portafolio.db` real). Se reutilizan estrategias numéricas de `tests/conftest.py`.
"""

import math
import os as _os
import sqlite3
import tempfile as _tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import plazos_strategy, tasas_strategy, trm_strategy

from valoracion_derivados import (
    TABLA_AUDIT_DERIVADOS,
    _COLUMNAS_AUDIT_DERIVADOS,
    _fila_audit_por_binding,
    persistir_auditoria_derivados,
)

# Columnas del esquema, excluyendo `corrida_id` (que se inyecta desde el argumento
# de `persistir_auditoria_derivados`, no desde el registro).
_COLUMNAS_SIN_CORRIDA = tuple(
    c for c in _COLUMNAS_AUDIT_DERIVADOS if c != "corrida_id"
)

# Cadenas adversarias de inyección SQL para los campos de texto (Req 5.2). Deben
# almacenarse literalmente gracias al binding parametrizado.
_INYECCIONES = (
    "'; DROP TABLE audit_derivados_corrida;--",
    'FWD"); DELETE FROM audit_derivados_corrida; --',
    "Robert'); DROP TABLE Students;--",
    "normal_value",
    "FORWARD_DEV",
    "COLLAR_SOLVER",
)


def _texto_adversario():
    """Texto que mezcla valores normales con cadenas de inyección SQL."""
    return st.one_of(
        st.sampled_from(_INYECCIONES),
        st.text(alphabet="abcABC 0'\";-|", min_size=0, max_size=16),
    )


def _reales_opcionales():
    """Un REAL finito o None (campo NULL en el esquema)."""
    return st.one_of(
        st.none(),
        st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
    )


@st.composite
def _registro_auditoria(draw):
    """
    Genera un `Registro_Auditoria` como el que acumula la corrida: un dict con un
    subconjunto de las columnas del esquema (algunas ausentes → NULL), campos de
    texto adversarios y `uso_fallback` como bool.
    """
    registro = {
        "tipo_registro": draw(_texto_adversario()),
        "fecha_constitucion": draw(st.one_of(st.none(), _texto_adversario())),
        "mes_proyeccion": draw(st.one_of(st.none(), st.integers(min_value=0, max_value=360))),
        "plazo_dias": draw(st.one_of(st.none(), plazos_strategy())),
        "trm_corte": draw(st.one_of(st.none(), trm_strategy())),
        "swtibr_ea": draw(_reales_opcionales()),
        "lutibr_ea": draw(_reales_opcionales()),
        "dev": draw(st.one_of(st.none(), st.floats(min_value=-0.5, max_value=2.0, allow_nan=False, allow_infinity=False))),
        "punto_forward": draw(_reales_opcionales()),
        "shift_tes_bps": draw(_reales_opcionales()),
        "shift_fed_bps": draw(_reales_opcionales()),
        "strike_put": draw(_reales_opcionales()),
        "strike_call": draw(_reales_opcionales()),
        "prima_neta": draw(_reales_opcionales()),
        "uso_fallback": draw(st.booleans()),
    }
    # Simular campos ausentes (se persisten como NULL): eliminar aleatoriamente
    # algunas claves opcionales del dict.
    for clave in draw(
        st.lists(
            st.sampled_from([c for c in _COLUMNAS_SIN_CORRIDA if c != "tipo_registro"]),
            unique=True,
            max_size=4,
        )
    ):
        registro.pop(clave, None)
    return registro


# Afinidad declarada de cada columna del esquema `audit_derivados_corrida`
# (Data Models del diseño). SQLite aplica *type affinity* al ALMACENAR: en una
# columna con afinidad NUMERIC/INTEGER, un TEXT que represente exactamente un
# entero/real se convierte a INTEGER/REAL; el resto de TEXT se conserva como
# texto. La comparación del round-trip debe modelar esta coerción para no
# reportar un falso negativo (p. ej. fecha_constitucion='0' TEXT se relee como 0
# INTEGER, lo cual es correcto y esperado, no un bug de persistencia).
#
# Mapa columna -> afinidad efectiva:
#   TEXT     : corrida_id, tipo_registro
#   NUMERIC  : fecha_constitucion (DATE)  -> coerción numérica de texto exacto
#   INTEGER  : mes_proyeccion, plazo_dias, uso_fallback
#   REAL     : trm_corte, swtibr_ea, lutibr_ea, dev, punto_forward,
#              shift_tes_bps, shift_fed_bps, strike_put, strike_call, prima_neta
_AFINIDAD_COLUMNA = {
    "corrida_id": "TEXT",
    "tipo_registro": "TEXT",
    "fecha_constitucion": "NUMERIC",
    "mes_proyeccion": "INTEGER",
    "plazo_dias": "INTEGER",
    "trm_corte": "REAL",
    "swtibr_ea": "REAL",
    "lutibr_ea": "REAL",
    "dev": "REAL",
    "punto_forward": "REAL",
    "shift_tes_bps": "REAL",
    "shift_fed_bps": "REAL",
    "strike_put": "REAL",
    "strike_call": "REAL",
    "prima_neta": "REAL",
    "uso_fallback": "INTEGER",
}


def _coercer_afinidad(valor, afinidad):
    """
    Replica la conversión de *type affinity* de SQLite al ALMACENAR `valor` en una
    columna con la `afinidad` dada. Solo afecta a valores TEXT que representan
    exactamente un número en columnas NUMERIC/INTEGER/REAL; en cualquier otro caso
    devuelve el valor sin cambios. Modela el contrato real del round-trip.
    """
    if valor is None or not isinstance(valor, str):
        return valor
    if afinidad in ("NUMERIC", "INTEGER"):
        # SQLite convierte texto que sea un entero exacto a INTEGER, y un real
        # exacto a REAL (afinidad NUMERIC). INTEGER prefiere el entero cuando el
        # texto es un entero bien formado.
        try:
            entero = int(valor)
            # Solo si el texto representa el entero de forma canónica (int(str)
            # ida y vuelta), coincide con lo que SQLite almacena como INTEGER.
            if str(entero) == valor:
                return entero
        except (TypeError, ValueError):
            pass
        try:
            real = float(valor)
            return real
        except (TypeError, ValueError):
            return valor
    if afinidad == "REAL":
        try:
            return float(valor)
        except (TypeError, ValueError):
            return valor
    return valor


def _norm_valor(v):
    """
    Normaliza un valor escalar para comparar igualdad de forma robusta:
    - `-0.0` se normaliza a `0.0` (SQLite guarda el REAL como cero positivo; en
      Python `-0.0 == 0.0` pero `repr(-0.0) != repr(0.0)`).
    - Los `float` se representan por su valor (no por su repr) preservando NaN.
    Devuelve una clave hashable/ordenable estable por valor.
    """
    if isinstance(v, float):
        if math.isnan(v):
            return ("float", "nan")
        return ("float", v + 0.0)  # +0.0 colapsa -0.0 a 0.0
    return (type(v).__name__, v)


def _clave_fila(fila):
    """
    Clave de comparación por valor de una fila completa (tupla de columnas),
    aplicando primero la coerción de afinidad de SQLite por columna para que la
    comparación refleje lo que la base de datos realmente almacena.
    """
    coercidos = [
        _coercer_afinidad(valor, _AFINIDAD_COLUMNA[col])
        for col, valor in zip(_COLUMNAS_AUDIT_DERIVADOS, fila)
    ]
    return tuple(_norm_valor(v) for v in coercidos)


def _multiset_ordenado(filas):
    """Multiconjunto de filas como lista ordenada de claves comparables por valor."""
    return sorted((_clave_fila(f) for f in filas), key=repr)


def _forma_canonica(corrida_id, registro):
    """
    Proyección canónica de un `Registro_Auditoria` al esquema, replicando la
    normalización que aplica la escritura (corrida_id inyectado, uso_fallback→0/1,
    claves ausentes→None). Es la tupla que debe recuperarse tras el round-trip.
    Reutiliza `_fila_audit_por_binding` (la misma función que usa la escritura).
    """
    return _fila_audit_por_binding(corrida_id, registro)


def _leer_por_corrida(ruta_db, corrida_id):
    """Relee las filas de la tabla filtrando por corrida_id, en orden de columnas."""
    conn = sqlite3.connect(str(ruta_db))
    try:
        columnas_sql = ", ".join(_COLUMNAS_AUDIT_DERIVADOS)
        cur = conn.execute(
            f"SELECT {columnas_sql} FROM {TABLA_AUDIT_DERIVADOS} WHERE corrida_id = ?",
            (corrida_id,),
        )
        return [tuple(fila) for fila in cur.fetchall()]
    finally:
        conn.close()


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    registros=st.lists(_registro_auditoria(), min_size=0, max_size=6),
    corrida_id=_texto_adversario(),
)
def test_roundtrip_persistencia_auditoria(registros, corrida_id):
    """
    Property 22 — persistir la lista de Registro_Auditoria y releerla filtrando por
    `corrida_id` devuelve exactamente el mismo conjunto (multiconjunto) de registros
    en su forma canónica, sin re-ejecutar la valoración.
    **Validates: Requirements 5.3**
    """
    fd, nombre = _tempfile.mkstemp(suffix=".db", prefix="axis_audit_rt_")
    _os.close(fd)
    ruta = nombre
    try:
        conn = sqlite3.connect(ruta)
        try:
            ok = persistir_auditoria_derivados(conn, corrida_id, registros)
        finally:
            conn.close()

        assert ok is True

        leidos = _leer_por_corrida(ruta, corrida_id)
        esperados = [_forma_canonica(corrida_id, reg) for reg in registros]

        # Igualdad de multiconjuntos (el orden de las filas no es parte del contrato).
        assert _multiset_ordenado(leidos) == _multiset_ordenado(esperados)
    finally:
        try:
            _os.unlink(ruta)
        except OSError:
            pass


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    registros_a=st.lists(_registro_auditoria(), min_size=1, max_size=4),
    registros_b=st.lists(_registro_auditoria(), min_size=1, max_size=4),
)
def test_roundtrip_aislado_por_corrida_id(registros_a, registros_b):
    """
    Property 22 — al persistir dos corridas distintas en la MISMA tabla, releer por
    un `corrida_id` recupera solo los registros de esa corrida (aislamiento por
    filtro), sin mezclar los de la otra. Confirma que el filtro por `corrida_id`
    delimita exactamente el conjunto generado por esa corrida.
    **Validates: Requirements 5.3**
    """
    fd, nombre = _tempfile.mkstemp(suffix=".db", prefix="axis_audit_rt2_")
    _os.close(fd)
    ruta = nombre
    corrida_a = "corrida-A"
    corrida_b = "corrida-B'; DROP TABLE audit_derivados_corrida;--"
    try:
        conn = sqlite3.connect(ruta)
        try:
            assert persistir_auditoria_derivados(conn, corrida_a, registros_a) is True
            assert persistir_auditoria_derivados(conn, corrida_b, registros_b) is True
        finally:
            conn.close()

        leidos_a = _leer_por_corrida(ruta, corrida_a)
        leidos_b = _leer_por_corrida(ruta, corrida_b)

        esperados_a = [_forma_canonica(corrida_a, r) for r in registros_a]
        esperados_b = [_forma_canonica(corrida_b, r) for r in registros_b]

        assert _multiset_ordenado(leidos_a) == _multiset_ordenado(esperados_a)
        assert _multiset_ordenado(leidos_b) == _multiset_ordenado(esperados_b)
    finally:
        try:
            _os.unlink(ruta)
        except OSError:
            pass
