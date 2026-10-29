"""
Pruebas unitarias del helper puro `_clave_fungibilidad` de
`scripts/proyeccion_tramos.py` (feature `optimizacion-agrupacion-negociables`,
tarea 1.3).

`_clave_fungibilidad(fila)` recibe un mapping tipo fila (dict o fila de DataFrame)
y devuelve la `Clave_Fungibilidad` normalizada de 8 componentes

    (especie, isin, fecha_vencimiento_iso, tasa_facial_red, periodicidad,
     tipo_tasa, moneda, tipo_bono)

o `None` si algún componente no coerciona (entrada NO confiable, Req 5.4). La
clave debe garantizar que dos `Titulo_Negociable` con el MISMO precio por unidad
de nominal compartan clave (Req 1.5).

Casos cubiertos (tarea 1.3):
- Normalización de texto (mayúsculas / espacios de borde / no-ASCII).
- `fecha_vencimiento` inválida → `None`.
- `tasa_facial` no finita (NaN / inf) → `None`.
- Dos filas idénticas salvo ruido de punto flotante en `tasa_facial` producen la
  MISMA clave (redondeo a `_PRECISION_TASA_FACIAL`).
- Derivación correcta de `tipo_bono` (UVR / IPC / YANKEE / TF).

_Requirements: 5.4, 1.5_
"""

import math

import pandas as pd
import pytest

import proyeccion_tramos as pt
from proyeccion_tramos import (
    _clave_fungibilidad,
    _tipo_bono_fungibilidad,
    _PRECISION_TASA_FACIAL,
)


# ── Fila base válida (Negociable TF en pesos) ────────────────────────────────
def _fila_base(**overrides) -> dict:
    """
    Construye una fila válida mínima para `_clave_fungibilidad` y permite
    sobre-escribir campos puntuales por caso de prueba.
    """
    fila = {
        "especie": "TES",
        "isin": "CO123456",
        "fecha_vencimiento": "2030-05-20",
        "tasa_facial": 7.25,
        "periodicidad": "SEMESTRAL",
        "tipo_tasa": "FIJA",
        "moneda": "COP",
        "asset_class_definitivo": "TF",
        "tasa_referencia": "",
    }
    fila.update(overrides)
    return fila


# Índices de la tupla devuelta, para legibilidad en las aserciones.
_I_ESPECIE = 0
_I_ISIN = 1
_I_FV = 2
_I_TASA = 3
_I_PERIODICIDAD = 4
_I_TIPO_TASA = 5
_I_MONEDA = 6
_I_TIPO_BONO = 7


# ─────────────────────────────────────────────────────────────────────────────
# 1. Normalización de texto: mayúsculas, espacios de borde y no-ASCII.
# ─────────────────────────────────────────────────────────────────────────────
def test_normaliza_texto_mayusculas_y_espacios():
    """str().strip().upper() en especie/isin/periodicidad/tipo_tasa/moneda."""
    clave = _clave_fungibilidad(
        _fila_base(
            especie="  tes  ",
            isin="co123456 ",
            periodicidad=" semestral",
            tipo_tasa="fija ",
            moneda=" cop ",
        )
    )
    assert clave is not None
    assert clave[_I_ESPECIE] == "TES"
    assert clave[_I_ISIN] == "CO123456"
    assert clave[_I_PERIODICIDAD] == "SEMESTRAL"
    assert clave[_I_TIPO_TASA] == "FIJA"
    assert clave[_I_MONEDA] == "COP"


def test_normaliza_texto_no_ascii_unicode_safe():
    """La normalización es unicode-safe (acentos/ñ) y aplica upper() correctamente."""
    clave = _clave_fungibilidad(
        _fila_base(especie="  bonó ñ  ", isin="isín-á", periodicidad="anüal")
    )
    assert clave is not None
    assert clave[_I_ESPECIE] == "BONÓ Ñ"
    assert clave[_I_ISIN] == "ISÍN-Á"
    assert clave[_I_PERIODICIDAD] == "ANÜAL"


def test_dos_filas_mismo_texto_distinto_formato_misma_clave():
    """Mismo contenido con distinto casing/espaciado produce la MISMA clave."""
    c1 = _clave_fungibilidad(_fila_base(especie="tes", moneda="cop"))
    c2 = _clave_fungibilidad(_fila_base(especie="  TES ", moneda=" CoP "))
    assert c1 is not None and c2 is not None
    assert c1 == c2


def test_fecha_vencimiento_se_normaliza_a_iso():
    """fecha_vencimiento se emite como ISO YYYY-MM-DD independiente del formato de entrada."""
    c_str = _clave_fungibilidad(_fila_base(fecha_vencimiento="2030-05-20"))
    c_ts = _clave_fungibilidad(_fila_base(fecha_vencimiento=pd.Timestamp("2030-05-20")))
    assert c_str is not None and c_ts is not None
    assert c_str[_I_FV] == "2030-05-20"
    assert c_str[_I_FV] == c_ts[_I_FV]


# ─────────────────────────────────────────────────────────────────────────────
# 2. fecha_vencimiento inválida → None.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "fecha_invalida",
    [
        "no-es-fecha",
        "2030-13-99",   # mes/día fuera de rango
        None,
        float("nan"),
        "",
    ],
)
def test_fecha_vencimiento_invalida_devuelve_none(fecha_invalida):
    """Cualquier fecha no parseable degrada la fila a intocable (None), sin lanzar."""
    assert _clave_fungibilidad(_fila_base(fecha_vencimiento=fecha_invalida)) is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. tasa_facial no finita → None.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "tasa_no_finita",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "no-es-numero",
        None,
    ],
)
def test_tasa_facial_no_finita_devuelve_none(tasa_no_finita):
    """NaN/inf/no-numérico/None en tasa_facial → None (entrada no confiable)."""
    assert _clave_fungibilidad(_fila_base(tasa_facial=tasa_no_finita)) is None


def test_tasa_facial_finita_se_conserva_redondeada():
    """Una tasa finita normal se conserva como float finito en la clave."""
    clave = _clave_fungibilidad(_fila_base(tasa_facial=7.25))
    assert clave is not None
    assert math.isfinite(clave[_I_TASA])
    assert clave[_I_TASA] == pytest.approx(7.25)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Ruido de punto flotante en tasa_facial → MISMA clave.
# ─────────────────────────────────────────────────────────────────────────────
def test_ruido_punto_flotante_en_tasa_produce_misma_clave():
    """
    Dos filas idénticas salvo ruido de float en `tasa_facial` (por debajo de la
    precisión de redondeo `_PRECISION_TASA_FACIAL`) deben compartir clave, para
    que reinversiones con la MISMA tasa facial agrupen aunque difiera el último bit.
    """
    # Ruido más pequeño que la precisión de redondeo (10 decimales).
    ruido = 10 ** (-(_PRECISION_TASA_FACIAL + 3))
    c1 = _clave_fungibilidad(_fila_base(tasa_facial=7.25))
    c2 = _clave_fungibilidad(_fila_base(tasa_facial=7.25 + ruido))
    assert c1 is not None and c2 is not None
    assert c1[_I_TASA] == c2[_I_TASA]
    assert c1 == c2


def test_tasas_realmente_distintas_producen_claves_distintas():
    """Diferencias por encima de la precisión NO deben colapsar (clave distinta)."""
    c1 = _clave_fungibilidad(_fila_base(tasa_facial=7.25))
    c2 = _clave_fungibilidad(_fila_base(tasa_facial=7.26))
    assert c1 is not None and c2 is not None
    assert c1 != c2


# ─────────────────────────────────────────────────────────────────────────────
# 5. Derivación de tipo_bono (UVR / IPC / YANKEE / TF).
# ─────────────────────────────────────────────────────────────────────────────
def test_tipo_bono_uvr_por_moneda():
    """moneda == UVR deriva tipo_bono = UVR."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="UVR", asset_class_definitivo="TF")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "UVR"


def test_tipo_bono_uvr_por_asset_class():
    """'UVR' en asset_class deriva tipo_bono = UVR."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="COP", asset_class_definitivo="TES UVR")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "UVR"


def test_tipo_bono_ipc_por_tasa_referencia():
    """tasa_referencia == IPC deriva tipo_bono = IPC."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="COP", asset_class_definitivo="TF", tasa_referencia="IPC")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "IPC"


def test_tipo_bono_yankee_por_moneda_usd():
    """moneda == USD deriva tipo_bono = YANKEE."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="USD", asset_class_definitivo="TF")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "YANKEE"


def test_tipo_bono_yankee_por_asset_class_legatruu():
    """asset_class == LEGATRUU deriva tipo_bono = YANKEE."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="COP", asset_class_definitivo="LEGATRUU")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "YANKEE"


def test_tipo_bono_tf_por_defecto():
    """Sin señales de UVR/IPC/YANKEE, el tipo_bono por defecto es TF."""
    clave = _clave_fungibilidad(
        _fila_base(moneda="COP", asset_class_definitivo="TF", tasa_referencia="")
    )
    assert clave is not None
    assert clave[_I_TIPO_BONO] == "TF"


def test_tipo_bono_coincide_con_helper_dedicado():
    """
    La derivación embebida en `_clave_fungibilidad` debe ser IDÉNTICA a la del
    helper `_tipo_bono_fungibilidad` (misma lógica que `cargar_titulos_desde_snapshot`),
    garantía de mismo precio por unidad de nominal al reconstruir (Req 1.5).
    """
    fila = _fila_base(moneda="USD", asset_class_definitivo="Estructuras")
    clave = _clave_fungibilidad(fila)
    esperado = _tipo_bono_fungibilidad("Estructuras", "USD", "")
    assert clave is not None
    assert clave[_I_TIPO_BONO] == esperado == "YANKEE"


# ─────────────────────────────────────────────────────────────────────────────
# 6. La función acepta filas de DataFrame (soporta .get) además de dict.
# ─────────────────────────────────────────────────────────────────────────────
def test_acepta_fila_de_dataframe():
    """Una fila de pandas (Series vía .loc) produce la misma clave que el dict."""
    df = pd.DataFrame([_fila_base()])
    fila_series = df.loc[0]
    clave_series = _clave_fungibilidad(fila_series)
    clave_dict = _clave_fungibilidad(_fila_base())
    assert clave_series is not None
    assert clave_series == clave_dict


# ─────────────────────────────────────────────────────────────────────────────
# 7. Campos de texto ausentes se tratan como vacío (no lanza).
# ─────────────────────────────────────────────────────────────────────────────
def test_campos_texto_ausentes_se_tratan_como_vacio():
    """Si faltan especie/isin/periodicidad/tipo_tasa, se normalizan a '' sin lanzar."""
    clave = _clave_fungibilidad(
        {
            "fecha_vencimiento": "2030-05-20",
            "tasa_facial": 5.0,
            "moneda": "COP",
            "asset_class_definitivo": "TF",
        }
    )
    assert clave is not None
    assert clave[_I_ESPECIE] == ""
    assert clave[_I_ISIN] == ""
    assert clave[_I_PERIODICIDAD] == ""
    assert clave[_I_TIPO_TASA] == ""
