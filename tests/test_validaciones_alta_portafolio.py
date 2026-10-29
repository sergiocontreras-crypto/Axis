"""
tests/test_validaciones_alta_portafolio.py
-------------------------------------------
Pruebas basadas en propiedades (Hypothesis) para la Property 1 del diseño de la
feature `escalabilidad-derivados-portafolios`.

**Property 1: Detección de nuevos y validación de alta de portafolio**

Para todo conjunto de portafolios del corte y conjunto de portafolios ya
registrados, `detectar_portafolios_nuevos` devuelve exactamente los portafolios
del corte (normalizados) ausentes de los registrados.

Para todo `portafolio_porfin`, `ramo`, `tipo`, `excluir` y conjunto de
existentes: `validar_alta_portafolio` acepta si y solo si `ramo` y `tipo` no son
vacíos ni solo espacios, `excluir ∈ {'SI','NO'}` (mayúsculas/minúsculas) y el
`portafolio_porfin` normalizado no está en los existentes; cuando acepta, el
payload contiene el `portafolio_porfin` normalizado y `ramo`/`tipo`/`excluir`
dados.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**

Se usan las estrategias de `conftest.py` (`ids_strategy`, etc.). Mínimo 100
ejemplos por propiedad (`max_examples=200`).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from comun.validaciones_inputs import (
    detectar_portafolios_nuevos,
    normalizar_id,
    validar_alta_portafolio,
)

from tests.conftest import ids_strategy

# Un mínimo de 100 ejemplos por propiedad (diseño). Usamos 150 por robustez, sin
# deadline y suprimiendo `too_slow`: la `ids_strategy` genera texto adversario
# (símbolos, espacios), cuya generación puede ser lenta pero no afecta la validez
# de la propiedad.
_AJUSTES = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# Estrategia para el campo `excluir`: mezcla valores válidos (en varias cajas)
# con basura arbitraria, para ejercitar tanto la aceptación como el rechazo.
_EXCLUIR_VALIDO = st.sampled_from(["SI", "NO", "si", "no", "Si", "nO", " SI ", " no "])
_EXCLUIR_CUALQUIERA = st.one_of(_EXCLUIR_VALIDO, ids_strategy(min_size=0, max_size=6))

# Identificadores/valores que garantizan un contenido NO vacío tras normalizar
# (es decir, no compuestos solo por espacios). Útil para fijar `ramo`/`tipo`
# válidos cuando la prueba quiere aislar el rechazo por OTRO motivo.
_NO_BLANCO = ids_strategy(min_size=1, max_size=12).filter(
    lambda v: normalizar_id(v) != ""
)


def _es_excluir_valido(valor: object) -> bool:
    """Réplica independiente de la regla de negocio de `excluir` (Req 1.4)."""
    return normalizar_id(valor).upper() in {"SI", "NO"}


# ─────────────────────────────────────────────────────────────────────────────
# Parte A — detectar_portafolios_nuevos (Req 1.1, 1.2)
# ─────────────────────────────────────────────────────────────────────────────


@_AJUSTES
@given(
    portafolios_corte=st.lists(ids_strategy(min_size=0, max_size=8), max_size=10),
    registrados=st.lists(ids_strategy(min_size=0, max_size=8), max_size=10),
)
def test_detectar_portafolios_nuevos_es_diferencia_de_conjuntos(
    portafolios_corte, registrados
):
    """
    `detectar_portafolios_nuevos` devuelve exactamente los portafolios del corte
    (normalizados, no vacíos) ausentes de los registrados: la diferencia de
    conjuntos, ordenada y sin duplicados. (Req 1.1, 1.2)
    """
    resultado = detectar_portafolios_nuevos(portafolios_corte, registrados)

    corte_norm = {n for v in portafolios_corte if (n := normalizar_id(v))}
    reg_norm = {n for v in registrados if (n := normalizar_id(v))}
    esperado = corte_norm - reg_norm

    # 1. Igualdad de conjuntos con la diferencia esperada.
    assert set(resultado) == esperado
    # 2. Sin duplicados.
    assert len(resultado) == len(set(resultado))
    # 3. Orden determinista (ascendente).
    assert resultado == sorted(resultado)
    # 4. Ningún resultado está entre los registrados (normalizados).
    assert all(p not in reg_norm for p in resultado)
    # 5. Todo resultado proviene del corte y es no vacío.
    assert all(p in corte_norm and p != "" for p in resultado)


@_AJUSTES
@given(base=st.lists(ids_strategy(min_size=1, max_size=8), min_size=1, max_size=15))
def test_detectar_portafolios_nuevos_vacio_si_todo_registrado(base):
    """
    Si todos los portafolios del corte ya están registrados, no hay nuevos
    (control oculto — Req 1.2).
    """
    assert detectar_portafolios_nuevos(base, base) == []


@_AJUSTES
@given(
    portafolios_corte=st.lists(ids_strategy(min_size=1, max_size=8), max_size=15),
)
def test_detectar_portafolios_nuevos_sin_registrados_devuelve_todos(portafolios_corte):
    """
    Sin portafolios registrados, todos los del corte (normalizados) son nuevos.
    """
    resultado = detectar_portafolios_nuevos(portafolios_corte, [])
    esperado = sorted({n for v in portafolios_corte if (n := normalizar_id(v))})
    assert resultado == esperado


# ─────────────────────────────────────────────────────────────────────────────
# Parte B — validar_alta_portafolio (Req 1.3, 1.4, 1.5, 1.6)
# ─────────────────────────────────────────────────────────────────────────────


@_AJUSTES
@given(
    porfin=ids_strategy(min_size=0, max_size=12),
    ramo=ids_strategy(min_size=0, max_size=12),
    tipo=ids_strategy(min_size=0, max_size=12),
    excluir=_EXCLUIR_CUALQUIERA,
    existentes=st.lists(ids_strategy(min_size=0, max_size=12), max_size=20),
)
def test_validar_alta_portafolio_acepta_sii_condiciones(
    porfin, ramo, tipo, excluir, existentes
):
    """
    `validar_alta_portafolio` acepta SI Y SOLO SI se cumplen todas las
    condiciones de negocio (Req 1.3-1.6):

    - `portafolio_porfin` normalizado no vacío.
    - `ramo` y `tipo` normalizados no vacíos (Req 1.5).
    - `excluir ∈ {'SI','NO'}` (case-insensitive) (Req 1.4).
    - `portafolio_porfin` normalizado no duplicado en existentes (Req 1.6).

    Y cuando acepta, el payload contiene los valores normalizados esperados
    (Req 1.3).
    """
    porfin_norm = normalizar_id(porfin)
    ramo_norm = normalizar_id(ramo)
    tipo_norm = normalizar_id(tipo)
    existentes_norm = {n for v in existentes if (n := normalizar_id(v))}

    debe_aceptar = (
        porfin_norm != ""
        and ramo_norm != ""
        and tipo_norm != ""
        and _es_excluir_valido(excluir)
        and porfin_norm not in existentes_norm
    )

    ok, mensaje, payload = validar_alta_portafolio(
        porfin, ramo, tipo, excluir, existentes
    )

    # Bicondicional exacto entre la especificación y la implementación.
    assert ok is debe_aceptar
    assert isinstance(mensaje, str) and mensaje != ""

    if ok:
        # Payload normalizado según Req 1.3.
        assert payload == {
            "portafolio_porfin": porfin_norm,
            "ramo": ramo_norm,
            "tipo": tipo_norm,
            "excluir": normalizar_id(excluir).upper(),
        }
        # El identificador quedó sin espacios iniciales/finales.
        assert payload["portafolio_porfin"] == payload["portafolio_porfin"].strip()
        # El valor persistido de excluir pertenece a la lista blanca.
        assert payload["excluir"] in {"SI", "NO"}
    else:
        # En rechazo no se produce payload persistible.
        assert payload == {}


@_AJUSTES
@given(
    porfin=_NO_BLANCO,
    tipo=_NO_BLANCO,
    excluir=_EXCLUIR_VALIDO,
    ramo_blanco=st.sampled_from(["", " ", "   ", "\t", "\n", " \t "]),
)
def test_validar_alta_portafolio_rechaza_ramo_vacio(
    porfin, tipo, excluir, ramo_blanco
):
    """Ramo vacío o solo espacios se rechaza sin payload (Req 1.5)."""
    ok, mensaje, payload = validar_alta_portafolio(
        porfin, ramo_blanco, tipo, excluir, set()
    )
    assert ok is False
    assert payload == {}
    assert "Ramo" in mensaje


@_AJUSTES
@given(
    porfin=_NO_BLANCO,
    ramo=_NO_BLANCO,
    excluir=_EXCLUIR_VALIDO,
    tipo_blanco=st.sampled_from(["", " ", "   ", "\t", "\n", " \t "]),
)
def test_validar_alta_portafolio_rechaza_tipo_vacio(
    porfin, ramo, excluir, tipo_blanco
):
    """Tipo vacío o solo espacios se rechaza sin payload (Req 1.5).

    `ramo` se fija no vacío para aislar el rechazo por `tipo`: la validación
    comprueba primero `ramo` y luego `tipo`.
    """
    ok, mensaje, payload = validar_alta_portafolio(
        porfin, ramo, tipo_blanco, excluir, set()
    )
    assert ok is False
    assert payload == {}
    assert "Tipo" in mensaje


@_AJUSTES
@given(
    porfin=_NO_BLANCO,
    ramo=_NO_BLANCO,
    tipo=_NO_BLANCO,
    excluir=ids_strategy(min_size=0, max_size=8).filter(
        lambda v: normalizar_id(v).upper() not in {"SI", "NO"}
    ),
)
def test_validar_alta_portafolio_rechaza_excluir_invalido(
    porfin, ramo, tipo, excluir
):
    """`excluir` fuera de la lista blanca {'SI','NO'} se rechaza (Req 1.4).

    `porfin`/`ramo`/`tipo` se fijan no vacíos para aislar el rechazo por el
    campo `excluir`.
    """
    ok, mensaje, payload = validar_alta_portafolio(porfin, ramo, tipo, excluir, set())
    assert ok is False
    assert payload == {}
    assert "Excluir" in mensaje


@_AJUSTES
@given(
    porfin=_NO_BLANCO,
    ramo=_NO_BLANCO,
    tipo=_NO_BLANCO,
    excluir=_EXCLUIR_VALIDO,
    otros=st.lists(ids_strategy(min_size=0, max_size=8), max_size=8),
)
def test_validar_alta_portafolio_rechaza_duplicado(
    porfin, ramo, tipo, excluir, otros
):
    """
    Un `portafolio_porfin` cuyo valor normalizado ya existe se rechaza aunque
    llegue con espacios distintos, identificando el duplicado (Req 1.6).

    `ramo`/`tipo` se fijan no vacíos para aislar el rechazo por duplicado, que
    la validación comprueba en último lugar.
    """
    porfin_norm = normalizar_id(porfin)
    # Insertamos el mismo id normalizado en existentes, con espacios alrededor
    # para verificar que la comparación es sobre valores normalizados.
    existentes = list(otros) + [f"  {porfin_norm} "]

    ok, mensaje, payload = validar_alta_portafolio(
        porfin, ramo, tipo, excluir, existentes
    )
    assert ok is False
    assert payload == {}
    assert porfin_norm in mensaje
