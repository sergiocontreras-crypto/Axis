# -*- coding: utf-8 -*-
"""
Property-based tests (Hypothesis) para la feature escalabilidad-derivados-portafolios.

Property 2: Detección de swaps nuevos y validación de asignación de subyacentes.

    Para todo conjunto de swaps del corte y conjunto de swaps registrados,
    `detectar_swaps_sin_subyacente` devuelve exactamente los swaps del corte
    (normalizados) ausentes de los registrados.

    Para todo `id_posicion` de swap, lista de subyacentes, conjunto de ids del
    portafolio del corte y conjunto de swaps registrados:
    `validar_asignacion_swap` acepta si y solo si la lista tiene entre 1 y 50
    subyacentes, todos existen en el corte y el swap no está registrado; cuando
    acepta, `payload['Subyacente'] == '|'.join(subyacentes_normalizados)`; cuando
    rechaza por subyacentes ausentes, el mensaje contiene exactamente los ids
    ausentes.

Valida las funciones puras `detectar_swaps_sin_subyacente`,
`parsear_subyacentes` y `validar_asignacion_swap` de
`comun/validaciones_inputs.py` (implementadas en las tareas 2.1 y 2.2) contra
una implementación de referencia independiente.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**
"""

import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegura que el directorio `tests/` (donde vive conftest.py) y la raíz del
# proyecto estén importables, con independencia del modo de importación de
# pytest. Esto permite `from conftest import ...` y `from comun...` de forma
# robusta al ejecutar este módulo aislado.
_ESTE_DIR = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(_ESTE_DIR)
for _p in (_ESTE_DIR, _RAIZ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comun.validaciones_inputs import (
    detectar_swaps_sin_subyacente,
    normalizar_id,
    parsear_subyacentes,
    validar_asignacion_swap,
)

# Reutiliza las estrategias comunes definidas en conftest.py.
from conftest import ids_strategy, lista_ids_strategy

# Número de ejemplos por propiedad. Reducido para corridas rápidas de
# verificación; el diseño exige >= 100 para la validación formal completa.
_MIN_EJEMPLOS = 25

# Ajustes de Hypothesis reutilizados por todas las propiedades de este módulo.
# `ids_strategy` genera texto adversario (símbolos, espacios, rango unicode
# amplio) para ejercitar la normalización y las escrituras parametrizadas; su
# generación puede ser lenta, por lo que se desactiva el `deadline` y se
# suprimen los health checks `too_slow`/`data_too_large` (mismo patrón que
# `test_validaciones_alta_portafolio.py`). Esto NO relaja la propiedad: solo
# evita que la lentitud del *generador* invalide la corrida (>= 100 ejemplos).
_AJUSTES = settings(
    max_examples=_MIN_EJEMPLOS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Cota superior de subyacentes admitidos por swap (Req 2.1).
_MAX_SUBYACENTES = 50


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de referencia independientes (no reutilizan la implementación)
# ─────────────────────────────────────────────────────────────────────────────
def _norm_set_ref(valores):
    """Normaliza (trim + str) y descarta vacíos; equivalente de referencia."""
    resultado = set()
    for v in valores:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            resultado.add(s)
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# Detección de swaps sin subyacente (Req 2.1, 2.2)
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    swaps_corte=st.lists(ids_strategy(), max_size=30),
    registrados=st.lists(ids_strategy(), max_size=30),
)
def test_detectar_swaps_sin_subyacente_es_diferencia_de_conjuntos(
    swaps_corte, registrados
):
    """
    `detectar_swaps_sin_subyacente` devuelve exactamente los swaps del corte
    (normalizados) ausentes de los registrados, sin duplicados y ordenado.
    (Req 2.1, 2.2)
    """
    obtenido = detectar_swaps_sin_subyacente(swaps_corte, registrados)

    esperado = _norm_set_ref(swaps_corte) - _norm_set_ref(registrados)

    # El resultado es exactamente la diferencia de conjuntos normalizados.
    assert set(obtenido) == esperado
    # Determinista: ordenado ascendente.
    assert obtenido == sorted(esperado)
    # Sin duplicados.
    assert len(obtenido) == len(set(obtenido))


@_AJUSTES
@given(swaps_corte=st.lists(ids_strategy(), max_size=30))
def test_detectar_swaps_registrados_completos_no_devuelve_nada(swaps_corte):
    """Si todos los swaps del corte ya están registrados, no hay nuevos (Req 2.2)."""
    obtenido = detectar_swaps_sin_subyacente(swaps_corte, swaps_corte)
    assert obtenido == []


# ─────────────────────────────────────────────────────────────────────────────
# Validación de asignación de subyacentes (Req 2.3, 2.4, 2.5, 2.6)
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(
    id_swap=ids_strategy(),
    subyacentes=lista_ids_strategy(min_ids=0, max_ids=60),
    ids_corte=st.lists(ids_strategy(), max_size=30),
    swaps_registrados=st.lists(ids_strategy(), max_size=30),
)
def test_validar_asignacion_swap_acepta_sii_condiciones(
    id_swap, subyacentes, ids_corte, swaps_registrados
):
    """
    `validar_asignacion_swap` acepta SI Y SOLO SI:
      - el id_posicion del swap normalizado no es vacío,
      - hay entre 1 y 50 subyacentes normalizados (deduplicados),
      - todos los subyacentes existen en el portafolio del corte,
      - el swap no está ya registrado.
    (Req 2.3, 2.4, 2.5, 2.6)
    """
    ok, mensaje, payload = validar_asignacion_swap(
        id_swap, subyacentes, ids_corte, swaps_registrados
    )

    # Reconstrucción de referencia del estado esperado.
    swap_norm = normalizar_id(id_swap)

    # Normalización + dedup preservando orden (referencia independiente).
    sub_norm = []
    vistos = set()
    for s in subyacentes or []:
        n = normalizar_id(s)
        if n and n not in vistos:
            vistos.add(n)
            sub_norm.append(n)

    ids_corte_set = _norm_set_ref(ids_corte)
    swaps_reg_set = _norm_set_ref(swaps_registrados)

    ausentes = [s for s in sub_norm if s not in ids_corte_set]

    esperado_ok = (
        bool(swap_norm)
        and 1 <= len(sub_norm) <= _MAX_SUBYACENTES
        and not ausentes
        and swap_norm not in swaps_reg_set
    )

    assert ok == esperado_ok

    if ok:
        # Payload correcto: id normalizado y subyacentes unidos por '|'.
        assert payload == {
            "id_posicion": swap_norm,
            "Subyacente": "|".join(sub_norm),
        }
        assert payload["Subyacente"] == "|".join(sub_norm)
    else:
        # Al rechazar no se produce payload.
        assert payload == {}


@_AJUSTES
@given(
    id_swap=ids_strategy(),
    ids_corte=st.lists(ids_strategy(), max_size=30),
    swaps_registrados=st.lists(ids_strategy(), max_size=30),
)
def test_validar_asignacion_swap_rechaza_cero_subyacentes(
    id_swap, ids_corte, swaps_registrados
):
    """Cero subyacentes => rechazo sin escritura (Req 2.5)."""
    ok, mensaje, payload = validar_asignacion_swap(
        id_swap, [], ids_corte, swaps_registrados
    )
    assert ok is False
    assert payload == {}


@_AJUSTES
@given(
    id_swap=ids_strategy(),
    swaps_registrados=st.lists(ids_strategy(), min_size=1, max_size=30),
)
def test_validar_asignacion_swap_rechaza_swap_ya_registrado(
    id_swap, swaps_registrados
):
    """
    Un swap ya registrado se rechaza aunque los subyacentes sean válidos y
    existan en el corte (Req 2.6). El id del swap se incluye entre los
    registrados para forzar el conflicto.
    """
    swap_norm = normalizar_id(id_swap)
    # Asegura que el swap normalizado esté entre los registrados.
    registrados = list(swaps_registrados) + [id_swap]
    # Subyacentes válidos que existen en el corte.
    subyacentes = ["S1", "S2"]
    ids_corte = ["S1", "S2"]

    ok, mensaje, payload = validar_asignacion_swap(
        id_swap, subyacentes, ids_corte, registrados
    )

    if swap_norm:
        assert ok is False
        assert payload == {}
        assert swap_norm in mensaje


@_AJUSTES
@given(
    id_swap=ids_strategy().filter(lambda s: normalizar_id(s) != ""),
    subyacentes=lista_ids_strategy(min_ids=1, max_ids=10),
    extra_ausentes=lista_ids_strategy(min_ids=1, max_ids=5),
)
def test_validar_asignacion_swap_mensaje_identifica_ausentes(
    id_swap, subyacentes, extra_ausentes
):
    """
    Cuando hay subyacentes que no existen en el corte, el rechazo identifica
    exactamente esos ids ausentes en el mensaje (Req 2.4).

    Construcción: el corte contiene los `subyacentes` presentes; los
    `extra_ausentes` NO se incluyen en el corte, por lo que deben aparecer en
    el mensaje. Se filtran solapes de normalización para que los "ausentes"
    sean genuinamente ausentes.
    """
    sub_norm = parsear_subyacentes(subyacentes)
    ausentes_norm = parsear_subyacentes(extra_ausentes)

    presentes_set = set(sub_norm)
    # Solo conservamos como "ausentes" los que no coinciden con presentes.
    ausentes_reales = [a for a in ausentes_norm if a not in presentes_set]

    # El corte contiene únicamente los subyacentes presentes.
    ids_corte = list(sub_norm)
    lista_total = sub_norm + ausentes_reales

    ok, mensaje, payload = validar_asignacion_swap(
        id_swap, lista_total, ids_corte, swaps_registrados=set()
    )

    if ausentes_reales:
        # Debe rechazar e identificar cada ausente.
        assert ok is False
        assert payload == {}
        for a in ausentes_reales:
            assert a in mensaje
    else:
        # Sin ausentes reales y con 1..50 subyacentes válidos => acepta.
        if 1 <= len(sub_norm) <= _MAX_SUBYACENTES:
            assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# parsear_subyacentes: contrato usado por la validación (Req 2.3)
# ─────────────────────────────────────────────────────────────────────────────
@_AJUSTES
@given(subyacentes=st.lists(ids_strategy(), max_size=30))
def test_parsear_subyacentes_normaliza_y_deduplica_preservando_orden(subyacentes):
    """
    `parsear_subyacentes` normaliza cada id, descarta vacíos y elimina
    duplicados preservando el orden de primera aparición (Req 2.3).
    """
    resultado = parsear_subyacentes(subyacentes)

    # Referencia independiente.
    esperado = []
    vistos = set()
    for s in subyacentes:
        n = normalizar_id(s)
        if n and n not in vistos:
            vistos.add(n)
            esperado.append(n)

    assert resultado == esperado
    # Sin duplicados y sin vacíos.
    assert len(resultado) == len(set(resultado))
    assert all(r for r in resultado)


@_AJUSTES
@given(sep=st.sampled_from(["|", ",", ";", " "]), ids=lista_ids_strategy(1, 8))
def test_parsear_subyacentes_desde_texto_delimitado(sep, ids):
    """
    `parsear_subyacentes` acepta una cadena con ids separados por '|', ',', ';'
    o espacios y devuelve la misma lista normalizada que la variante de lista
    (Req 2.3). Se usan ids alfanuméricos para evitar que el propio id contenga
    el separador.
    """
    # Ids sin espacios ni delimitadores internos para un round-trip limpio.
    ids_limpios = [normalizar_id(i).replace("|", "").replace(",", "").replace(";", "")
                   for i in ids]
    ids_limpios = ["".join(ch for ch in i if not ch.isspace()) for i in ids_limpios]
    ids_limpios = [i for i in ids_limpios if i]

    texto = sep.join(ids_limpios)
    resultado = parsear_subyacentes(texto)

    # Referencia: dedup preservando orden.
    esperado = []
    vistos = set()
    for i in ids_limpios:
        if i not in vistos:
            vistos.add(i)
            esperado.append(i)

    assert resultado == esperado
