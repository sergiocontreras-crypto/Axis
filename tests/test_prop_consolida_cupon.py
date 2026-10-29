"""
Property-based test de la feature `optimizacion-agrupacion-negociables`.

Property 8: Equivalencia del cupón del período del consolidado.

Para cualquier `Grupo_Fungible`, el cupón del período calculado sobre el
`Titulo_Consolidado` (reconstruido como t0 del tramo siguiente) es igual a la suma de
los cupones del mismo período de los miembros del grupo, dentro de la
`Tolerancia_Equivalencia` (1e-6 relativo).

Mecánica (ver design.md, sección "Corrección de cupones" / nota sobre `fecha_emision`):
el calendario de cupones del motor se ancla al VENCIMIENTO (+ tasa_facial +
periodicidad), no a `fecha_emision`; esta última solo actúa como cota inferior
(`detectar_cupones` corta cuando `fecha_cupon <= fecha_emision`). El `Titulo_Consolidado`
se reconstruye con `nominal = Σ nominales` del grupo y `fecha_emision = MÍN` del grupo,
de modo que:
  1. el calendario del consolidado es un superconjunto del de cada miembro (no recorta
     cupones), y
  2. el monto del cupón de cada fecha iguala la suma de los montos de los miembros que
     pagan en esa fecha (linealidad del cupón en el nominal para misma tasa/fechas).

Property test con Hypothesis (>= 100 iteraciones). El fixture `conftest.py` pone
`scripts/` en `sys.path`. Se usa `detectar_cupones` y la dataclass `Titulo` del motor
(`scripts/motor_proyeccion.py`), SIN modificar el motor (test-only).
"""

from datetime import date

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from motor_proyeccion import Titulo, detectar_cupones


# ── Alfabetos acotados para los atributos de la Clave_Fungibilidad ──
_ESPECIES = st.sampled_from(["TES2030", "TES2032", "CORP_A", "YANKEE_X"])
_ISINES = st.sampled_from(["COABC", "COXYZ", "US123"])
_FRECUENCIAS = st.sampled_from(["anual", "semestral", "trimestral", "mensual"])


def _construir_titulo(
    *,
    id_pos: str,
    especie: str,
    isin: str,
    fecha_emision: date,
    fecha_vencimiento: date,
    nominal: float,
    tasa_cupon: float,
    frecuencia: str,
) -> Titulo:
    """
    Construye un `Titulo` Negociable TF que varía SOLO en `fecha_emision`, `nominal`
    e `id_posicion`; comparte el resto de atributos de la `Clave_Fungibilidad`.

    Se usa `tipo_bono="TF"` para que el cupón sea determinista y lineal en el nominal
    sin depender de series externas (IPC/UVR). Igualmente aplicaría a cualquier tipo,
    pues `_calcular_monto_cupon` es lineal en `nominal` para iguales tasa/fechas.
    """
    return Titulo(
        id_posicion=id_pos,
        especie=especie,
        emisor="EMISOR",
        isin=isin,
        moneda="COP",
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        nominal=nominal,
        tasa_cupon=tasa_cupon,
        spread_ipc=0.0,
        tipo_bono="TF",
        frecuencia=frecuencia,
        tipo_tasa="EFECTIVO",
        base_anual=365,
        uso_tasa="ACTUAL",
        clasificacion="Negociable",
        compania="C1",
        ramo="R1",
        portafolio="P1",
        asset_class="TES",
        tasa_compra=tasa_cupon,
        tasa_mercado=tasa_cupon,
        valor_mercado=nominal,
        valor_tir=nominal,
    )


@st.composite
def _grupo_fungible(draw):
    """
    Genera un `Grupo_Fungible` de 2..5 miembros que comparten la `Clave_Fungibilidad`
    (especie, isin, fecha_vencimiento, tasa_cupon, frecuencia) y varían en
    `fecha_emision` y `nominal`. Devuelve `(miembros, fecha_inicio, fecha_fin)`.
    """
    especie = draw(_ESPECIES)
    isin = draw(_ISINES)
    frecuencia = draw(_FRECUENCIAS)
    tasa_cupon = draw(
        st.floats(min_value=0.1, max_value=25.0, allow_nan=False, allow_infinity=False)
    )

    # Vencimiento válido en el futuro respecto al horizonte de proyección.
    anio_venc = draw(st.integers(min_value=2029, max_value=2040))
    mes_venc = draw(st.integers(min_value=1, max_value=12))
    dia_venc = draw(st.integers(min_value=1, max_value=28))
    fecha_vencimiento = date(anio_venc, mes_venc, dia_venc)

    n_miembros = draw(st.integers(min_value=2, max_value=5))

    # fecha_emision de cada miembro: distintos meses de 2026 (reinversiones), todas
    # anteriores a la fecha_inicio del horizonte para no recortar cupones del período.
    miembros = []
    for i in range(n_miembros):
        mes_em = draw(st.integers(min_value=1, max_value=11))
        dia_em = draw(st.integers(min_value=1, max_value=28))
        fecha_emision = date(2026, mes_em, dia_em)
        nominal = draw(
            st.floats(
                min_value=1.0,
                max_value=1_000_000.0,
                allow_nan=False,
                allow_infinity=False,
            )
        )
        miembros.append(
            _construir_titulo(
                id_pos=f"R_{especie}_{i}",
                especie=especie,
                isin=isin,
                fecha_emision=fecha_emision,
                fecha_vencimiento=fecha_vencimiento,
                nominal=nominal,
                tasa_cupon=tasa_cupon,
                frecuencia=frecuencia,
            )
        )

    fecha_inicio = date(2026, 12, 31)
    fecha_fin = date(2040, 12, 31)
    return miembros, fecha_inicio, fecha_fin


def _montos_por_fecha(portafolio, fecha_inicio, fecha_fin):
    """Suma los montos de cupón por fecha de pago en el período dado."""
    acc: dict[date, float] = {}
    for c in detectar_cupones(portafolio, fecha_inicio, fecha_fin):
        acc[c.fecha_pago] = acc.get(c.fecha_pago, 0.0) + c.monto_cupon
    return acc


# Feature: optimizacion-agrupacion-negociables, Property 8: Equivalencia del cupón del período del consolidado
# Validates: Requirements 4.1, 4.3
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_grupo_fungible())
def test_equivalencia_cupon_periodo_consolidado(datos):
    """
    El cupón del período del `Titulo_Consolidado` (reconstruido como t0 del tramo
    siguiente) es igual a la suma de los cupones del mismo período de los miembros del
    `Grupo_Fungible`, dentro de la `Tolerancia_Equivalencia` (1e-6 relativo). Además, el
    calendario de cupones del consolidado es un superconjunto del de cada miembro (ningún
    cupón se recorta).
    """
    miembros, fecha_inicio, fecha_fin = datos
    tol = 1e-6

    # ── Reconstrucción del consolidado (regla del diseño) ──
    #   nominal = suma del grupo; fecha_emision = MÍNIMA del grupo; resto = atributos
    #   comunes de la Clave_Fungibilidad.
    ref = miembros[0]
    fe_min = min(t.fecha_emision for t in miembros)
    consolidado = _construir_titulo(
        id_pos="CONS_GRUPO",
        especie=ref.especie,
        isin=ref.isin,
        fecha_emision=fe_min,
        fecha_vencimiento=ref.fecha_vencimiento,
        nominal=sum(t.nominal for t in miembros),
        tasa_cupon=ref.tasa_cupon,
        frecuencia=ref.frecuencia,
    )

    montos_miembros = _montos_por_fecha(miembros, fecha_inicio, fecha_fin)
    montos_consol = _montos_por_fecha([consolidado], fecha_inicio, fecha_fin)

    # (1) El calendario del consolidado contiene TODAS las fechas de cupón de los
    #     miembros (no recorta cupones): superconjunto.
    fechas_miembros = set(montos_miembros)
    fechas_consol = set(montos_consol)
    faltantes = fechas_miembros - fechas_consol
    assert not faltantes, (
        f"El consolidado recorta cupones de los miembros en: "
        f"{sorted(f.isoformat() for f in faltantes)}"
    )

    # (2) Para cada fecha de cupón, el monto del consolidado iguala la suma de los
    #     montos de los miembros dentro de la tolerancia relativa 1e-6.
    for fecha in sorted(fechas_consol | fechas_miembros):
        a = montos_consol.get(fecha, 0.0)
        b = montos_miembros.get(fecha, 0.0)
        denom = abs(b) if abs(b) > 1e-12 else 1.0
        desvio_rel = abs(a - b) / denom
        assert desvio_rel <= tol, (
            f"Cupón del consolidado != suma de miembros en {fecha.isoformat()}: "
            f"consolidado={a!r} suma_miembros={b!r} desvio_rel={desvio_rel:.3e} "
            f"(tol={tol:.0e})"
        )
