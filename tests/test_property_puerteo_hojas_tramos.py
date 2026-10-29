"""Pruebas de puerteo de hojas del store por modo de proyección (Defecto 3).

Feature: excel-por-tramos-consistencia, Property 3: Puerteo de hojas del store según el modo.

Property 3 (P3)
---------------
Para todo store que contenga datos de ``derivados``, ``rv``, ``alternativos`` y ``creditos``:
  * cuando ``modo == 'por_tramos'`` el libro resultante NO contiene las hojas ``Derivados``,
    ``RV_Mensual``, ``Alt_Mensual``, ``Creditos`` ni ``Creditos_Tramos``;
  * cuando ``modo == 'punta_a_punta'`` esas cuatro hojas SÍ están presentes (comportamiento
    inalterado).

Validates: Requirements 3.2, 3.4, 3.5 (y 3.1 vía el ejemplo de lectura de modo).

Seam de prueba
--------------
El escritor del Excel vive en el callback de Dash ``app_dash.descargar_resultados_excel``, que
además lee la BD real (``anexar_hojas_detalle_tramos``) y envuelve el resultado en
``dcc.send_bytes``. Para ejercitar ÚNICAMENTE la lógica de puerteo real sin tocar la BD ni levantar
el servidor, el callback delega el puerteo de las cuatro hojas del store en el helper puro
``app_dash._escribir_hojas_store_por_modo(writer, data)`` (mismo origen de verdad para callback y
pruebas). Las pruebas invocan ese helper con un ``data`` fabricado, escriben a un ``BytesIO`` y leen
los nombres de hoja del libro producido con ``openpyxl.load_workbook(...).sheetnames``.

Estado esperado en código SIN corregir: la rama ``por_tramos`` FALLABA porque las cuatro hojas se
escribían siempre que había datos en el store (sin considerar el modo). Tras el Defecto 3, la rama
``por_tramos`` las omite.

Convención (Windows / Google Drive): ``@settings(deadline=None)`` y >=100 ejemplos.
"""

import io

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from openpyxl import load_workbook

# Dashboard pesado; saltar con gracia si el entorno no puede importarlo.
app_dash = pytest.importorskip("app_dash")


# Las cuatro hojas del store puerteadas por modo (Defecto 3) y la serie que NO debe crearse.
HOJAS_STORE = ("Derivados", "RV_Mensual", "Alt_Mensual", "Creditos")
HOJA_PROHIBIDA = "Creditos_Tramos"


def _sheetnames_tras_escribir(data):
    """Invoca el helper REAL de puerteo y devuelve los nombres de hoja del libro producido.

    Escribe primero una hoja ancla (``_Ancla``) para que el libro nunca quede sin hojas visibles
    (openpyxl exige al menos una), de modo que la ausencia de las hojas del store sea atribuible al
    puerteo y no a un libro vacío.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"ancla": [1]}).to_excel(writer, sheet_name="_Ancla", index=False)
        app_dash._escribir_hojas_store_por_modo(writer, data)
    buffer.seek(0)
    return list(load_workbook(buffer, read_only=True).sheetnames)


# ── Estrategias ────────────────────────────────────────────────────────────────────────────────

# Filas no vacías para cada bloque del store (claves genéricas; el contenido es irrelevante para el
# puerteo, que solo depende de la verdad/falsedad de ``data.get(clave)`` y del modo).
def _bloque_registros():
    return st.lists(
        st.fixed_dictionaries(
            {
                "mes": st.integers(min_value=1, max_value=120),
                "valor": st.floats(
                    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
                ),
            }
        ),
        min_size=1,
        max_size=6,
    )


@st.composite
def _store_con_cuatro_bloques(draw, modo):
    """Genera un ``data`` con los cuatro bloques del store presentes y el modo indicado."""
    return {
        "parametros": {"modo": modo},
        "derivados": draw(_bloque_registros()),
        "rv": draw(_bloque_registros()),
        "alternativos": draw(_bloque_registros()),
        "creditos": draw(_bloque_registros()),
    }


# ── Property 3 ───────────────────────────────────────────────────────────────────────────────────

@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(data=_store_con_cuatro_bloques(modo="por_tramos"))
def test_p3_por_tramos_omite_las_cuatro_hojas_del_store(data):
    """P3 (por_tramos): el libro NO contiene las cuatro hojas del store ni ``Creditos_Tramos``."""
    hojas = _sheetnames_tras_escribir(data)
    for hoja in HOJAS_STORE:
        assert hoja not in hojas, f"'{hoja}' no debe escribirse en modo por_tramos"
    assert HOJA_PROHIBIDA not in hojas, "no debe construirse 'Creditos_Tramos'"


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(data=_store_con_cuatro_bloques(modo="punta_a_punta"))
def test_p3_punta_a_punta_conserva_las_cuatro_hojas_del_store(data):
    """P3 (punta_a_punta): las cuatro hojas del store SÍ están presentes (comportamiento inalterado)."""
    hojas = _sheetnames_tras_escribir(data)
    for hoja in HOJAS_STORE:
        assert hoja in hojas, f"'{hoja}' debe escribirse en modo punta_a_punta"
    assert HOJA_PROHIBIDA not in hojas, "'Creditos_Tramos' nunca se construye"


@settings(deadline=None, max_examples=150, suppress_health_check=[HealthCheck.too_slow])
@given(
    modo=st.sampled_from(["por_tramos", "punta_a_punta"]),
    data=_store_con_cuatro_bloques(modo="punta_a_punta"),  # bloques; el modo real se fija abajo
)
def test_p3_particion_presencia_por_modo(modo, data):
    """P3 combinada: con los mismos datos, el conjunto de hojas del store depende solo del modo."""
    data = dict(data)
    data["parametros"] = {"modo": modo}
    hojas = set(_sheetnames_tras_escribir(data))
    presentes = hojas.intersection(HOJAS_STORE)
    if modo == "por_tramos":
        assert presentes == set()
    else:
        assert presentes == set(HOJAS_STORE)
    assert HOJA_PROHIBIDA not in hojas


# ── Ejemplos / unit (AC 3.1): lectura del modo y fallback seguro ─────────────────────────────────

def test_ejemplo_modo_leido_de_parametros_por_tramos():
    """El modo se lee de ``data['parametros']['modo']``: 'por_tramos' omite las cuatro hojas."""
    data = {
        "parametros": {"modo": "por_tramos"},
        "derivados": [{"mes": 1, "valor": 10.0}],
        "rv": [{"mes": 1, "valor": 20.0}],
        "alternativos": [{"mes": 1, "valor": 30.0}],
        "creditos": [{"mes": 1, "valor": 40.0}],
    }
    hojas = _sheetnames_tras_escribir(data)
    assert all(h not in hojas for h in HOJAS_STORE)
    assert HOJA_PROHIBIDA not in hojas


def test_ejemplo_modo_leido_de_parametros_punta_a_punta():
    """El modo 'punta_a_punta' escribe las cuatro hojas del store (sin cambios)."""
    data = {
        "parametros": {"modo": "punta_a_punta"},
        "derivados": [{"mes": 1, "valor": 10.0}],
        "rv": [{"mes": 1, "valor": 20.0}],
        "alternativos": [{"mes": 1, "valor": 30.0}],
        "creditos": [{"mes": 1, "valor": 40.0}],
    }
    hojas = _sheetnames_tras_escribir(data)
    assert all(h in hojas for h in HOJAS_STORE)


def test_ejemplo_fallback_punta_a_punta_cuando_falta_modo():
    """Sin la clave 'modo' (ni 'parametros'), el fallback es 'punta_a_punta': se escriben las hojas."""
    # Falta 'parametros' por completo.
    data_sin_parametros = {
        "derivados": [{"mes": 1, "valor": 1.0}],
        "rv": [{"mes": 1, "valor": 2.0}],
        "alternativos": [{"mes": 1, "valor": 3.0}],
        "creditos": [{"mes": 1, "valor": 4.0}],
    }
    hojas = _sheetnames_tras_escribir(data_sin_parametros)
    assert all(h in hojas for h in HOJAS_STORE)

    # 'parametros' presente pero sin 'modo'.
    data_sin_modo = dict(data_sin_parametros, parametros={"horizonte": 36})
    hojas2 = _sheetnames_tras_escribir(data_sin_modo)
    assert all(h in hojas2 for h in HOJAS_STORE)


def test_ejemplo_por_tramos_sin_datos_no_crea_hojas():
    """En por_tramos, aunque haya datos en el store, no se escribe ninguna de las cuatro hojas."""
    data = {"parametros": {"modo": "por_tramos"}, "derivados": [], "rv": [],
            "alternativos": [], "creditos": []}
    hojas = _sheetnames_tras_escribir(data)
    assert all(h not in hojas for h in HOJAS_STORE)
