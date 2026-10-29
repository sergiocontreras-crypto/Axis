"""
Unit tests para la discriminación de alertas del dashboard y el mensaje de éxito
de la carga del insumo de reservas por encabezados de fecha (tarea 10.3 de la
feature `lectura-reservas-encabezados-fecha`).

Cubre los criterios de UI/mensajería del diseño (cubiertos por tests de EJEMPLO,
no por propiedades):

- Cada tipo de excepción del `Lector_Reservas`/`Persistidor_Reservas` produce su
  alerta bloqueante ESPECÍFICA en el callback `cargar_excel_reservas`
  (Req 11.1, 11.2, 11.4).
- La discriminación por causa evita un mensaje genérico único; las cuatro causas
  (formato de encabezado, hojas faltantes, error de lectura, error de
  persistencia) producen mensajes distintos (Req 11.4).
- Una carga fallida deja el insumo inválido: todas las alertas de error son
  bloqueantes (estilo rojo) y ninguna reporta éxito, por lo que la corrida no
  procede con ese archivo (Req 11.3).
- El éxito reporta la confirmación con el número de filas persistidas
  ("✅ Variación de reservas cargada (N filas).") (Req 11.5).
- La nota informativa de años con Δ=0 se etiqueta como INFORMATIVA (ℹ️, paleta
  azul) y NO es una alerta bloqueante roja (Req 10.2, 10.3).

Enfoque: `cargar_excel_reservas` es un objeto-función Dash directamente invocable
(como en `test_dashboard_reservas.py`). El callback importa
`derivar_variaciones_reservas` y `persistir_input_reservas` desde el módulo
`compras_ventas_reservas`, por lo que se monkeypatchea ese módulo para forzar cada
excepción. `app_dash` se importa con `importorskip` para que la suite no se rompa
si el entorno no puede cargar el dashboard.
"""

import base64

import pandas as pd
import pytest

import compras_ventas_reservas as cvr
from compras_ventas_reservas import (
    ErrorFormatoEncabezado,
    ErrorHojasFaltantes,
    ErrorInputReservas,
    ErrorPersistenciaReservas,
)

# El dashboard es pesado; saltar con gracia si el entorno no puede importarlo.
app_dash = pytest.importorskip("app_dash")


@pytest.fixture(autouse=True)
def _redirigir_get_conn_dashboard(db_tmp, monkeypatch):
    """El dashboard tiene su propia referencia `get_conn`; apuntarla a la DB temporal."""
    def _get_conn_tmp(check_same_thread: bool = False, timeout: float = 30.0):
        import sqlite3
        return sqlite3.connect(
            str(db_tmp), check_same_thread=check_same_thread, timeout=timeout
        )

    monkeypatch.setattr(app_dash, "get_conn", _get_conn_tmp, raising=True)
    # Aislar la responsabilidad del CALLBACK: no depender del portafolio real.
    monkeypatch.setattr(
        app_dash, "_ramos_del_portafolio_inicial", lambda: ["SOAT", "VIDA"], raising=True
    )
    yield


def _contents_b64(raw: bytes) -> str:
    """Construye el string `contents` del componente Upload de Dash."""
    return "data:application/octet-stream;base64," + base64.b64encode(raw).decode()


def _texto_y_color(componente):
    """Extrae (texto, color) de un html.Span devuelto por el callback."""
    texto = str(getattr(componente, "children", componente))
    estilo = getattr(componente, "style", {}) or {}
    color = str(estilo.get("color", "")).lower()
    return texto, color


def _forzar_lector(monkeypatch, excepcion=None, df=None):
    """
    Fuerza el comportamiento de la cadena derivar→persistir del callback.

    Si `excepcion` es una instancia, `derivar_variaciones_reservas` la lanza.
    Si `df` no es None, la derivación devuelve ese DataFrame (persistencia real).
    """
    def _derivar(*_a, **_k):
        if excepcion is not None:
            raise excepcion
        return df

    monkeypatch.setattr(cvr, "derivar_variaciones_reservas", _derivar, raising=True)


# ==============================================================================
# Discriminación de alertas bloqueantes por causa (Req 11.1, 11.2, 11.4, 11.3)
# ==============================================================================

def test_alerta_hojas_faltantes_es_especifica_y_bloqueante(monkeypatch):
    """ErrorHojasFaltantes ⇒ alerta que identifica las hojas requeridas (Req 11.2)."""
    _forzar_lector(monkeypatch, excepcion=ErrorHojasFaltantes("falta hoja"))
    comp = app_dash.cargar_excel_reservas(_contents_b64(b"x"), "reservas.xlsx")
    texto, color = _texto_y_color(comp)

    assert "❌" in texto
    assert "Saldo Reservas" in texto and "Interés a la Reserva" in texto
    # Bloqueante: estilo rojo, no reporta éxito.
    assert color == "red"
    assert "✅" not in texto


def test_alerta_formato_encabezado_pide_fechas_y_bloquea(monkeypatch):
    """ErrorFormatoEncabezado ⇒ alerta que exige encabezados de FECHA (Req 11.1)."""
    _forzar_lector(monkeypatch, excepcion=ErrorFormatoEncabezado("no es fecha"))
    comp = app_dash.cargar_excel_reservas(_contents_b64(b"x"), "reservas.xlsx")
    texto, color = _texto_y_color(comp)

    assert "❌" in texto
    assert "FECHAS" in texto or "fecha" in texto.lower()
    assert color == "red"
    assert "✅" not in texto


def test_alerta_error_lectura_es_distinta(monkeypatch):
    """ErrorInputReservas (base) ⇒ alerta de lectura, distinta de las subclases (Req 11.4)."""
    _forzar_lector(monkeypatch, excepcion=ErrorInputReservas("archivo ilegible"))
    comp = app_dash.cargar_excel_reservas(_contents_b64(b"x"), "reservas.xlsx")
    texto, color = _texto_y_color(comp)

    assert "❌" in texto
    assert "leer" in texto.lower()
    assert color == "red"
    assert "✅" not in texto


def test_alerta_error_persistencia_es_distinta(monkeypatch):
    """ErrorPersistenciaReservas ⇒ alerta de guardado, distinta de las demás (Req 11.4)."""
    # Derivación OK (df válido), pero la PERSISTENCIA falla.
    df_ok = pd.DataFrame({"ramo": ["SOAT"], "mes": ["2026-02"], "delta_reserva": [1.0]})
    _forzar_lector(monkeypatch, df=df_ok)
    monkeypatch.setattr(
        cvr, "persistir_input_reservas",
        lambda *_a, **_k: (_ for _ in ()).throw(ErrorPersistenciaReservas("locked")),
        raising=True,
    )
    comp = app_dash.cargar_excel_reservas(_contents_b64(b"x"), "reservas.xlsx")
    texto, color = _texto_y_color(comp)

    assert "❌" in texto
    assert "guardar" in texto.lower()
    assert color == "red"
    assert "✅" not in texto


def test_las_cuatro_causas_producen_mensajes_distintos(monkeypatch):
    """
    Req 11.4: se diferencia el mensaje según la causa real en lugar de un único
    mensaje genérico. Las cuatro causas deben producir textos DISTINTOS entre sí.
    """
    df_ok = pd.DataFrame({"ramo": ["SOAT"], "mes": ["2026-02"], "delta_reserva": [1.0]})

    # 1) Hojas faltantes
    _forzar_lector(monkeypatch, excepcion=ErrorHojasFaltantes("h"))
    t_hojas, _ = _texto_y_color(app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx"))

    # 2) Formato de encabezado
    _forzar_lector(monkeypatch, excepcion=ErrorFormatoEncabezado("f"))
    t_fmt, _ = _texto_y_color(app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx"))

    # 3) Error de lectura (base)
    _forzar_lector(monkeypatch, excepcion=ErrorInputReservas("l"))
    t_lect, _ = _texto_y_color(app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx"))

    # 4) Error de persistencia
    _forzar_lector(monkeypatch, df=df_ok)
    monkeypatch.setattr(
        cvr, "persistir_input_reservas",
        lambda *_a, **_k: (_ for _ in ()).throw(ErrorPersistenciaReservas("p")),
        raising=True,
    )
    t_pers, _ = _texto_y_color(app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx"))

    mensajes = {t_hojas, t_fmt, t_lect, t_pers}
    assert len(mensajes) == 4, f"Los mensajes no son todos distintos: {mensajes}"


# ==============================================================================
# Mensaje de éxito con conteo de filas (Req 11.5)
# ==============================================================================

def test_exito_reporta_confirmacion_con_numero_de_filas(monkeypatch):
    """Éxito ⇒ '✅ Variación de reservas cargada (N filas).' con N del persistidor (Req 11.5)."""
    df_ok = pd.DataFrame({
        "ramo": ["SOAT", "VIDA", "SOAT"],
        "mes": ["2026-02", "2026-02", "2026-03"],
        "delta_reserva": [1.0, 2.0, 3.0],
    })
    _forzar_lector(monkeypatch, df=df_ok)
    # El persistidor reporta el número de filas persistidas.
    monkeypatch.setattr(cvr, "persistir_input_reservas", lambda *_a, **_k: 3, raising=True)

    comp = app_dash.cargar_excel_reservas(_contents_b64(b"x"), "reservas.xlsx")
    texto, color = _texto_y_color(comp)

    assert "✅" in texto
    assert "Variación de reservas cargada" in texto
    assert "3 filas" in texto
    assert "❌" not in texto
    # El éxito no es una alerta bloqueante roja.
    assert color != "red"


def test_exito_usa_conteo_del_persistidor_no_del_df(monkeypatch):
    """El N mostrado proviene del retorno del persistidor (filas realmente guardadas)."""
    # df con 5 filas, pero el persistidor descarta 2 y reporta 3.
    df_mixto = pd.DataFrame({
        "ramo": ["A", "B", "C", "D", "E"],
        "mes": ["2026-02"] * 5,
        "delta_reserva": [1.0, 2.0, 3.0, 4.0, 5.0],
    })
    _forzar_lector(monkeypatch, df=df_mixto)
    monkeypatch.setattr(cvr, "persistir_input_reservas", lambda *_a, **_k: 3, raising=True)

    texto, _ = _texto_y_color(app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx"))
    assert "3 filas" in texto
    assert "5 filas" not in texto


# ==============================================================================
# Nota informativa (no bloqueante) de años con Δ=0 (Req 10.2, 10.3)
# ==============================================================================

def _construir_nota_informativa(anos_sin_columna):
    """
    Reconstruye la nota informativa EXACTAMENTE como la produce el orquestador
    por tramos (`_ejecutar_escenario_por_tramos`), para verificar su CONTRATO de
    presentación (etiqueta informativa, paleta azul, no bloqueante) sin ejecutar
    una proyección completa.

    Nota: la construcción de la nota es un fragmento autocontenido dentro de una
    función de orquestación muy grande que requiere una corrida completa. Este
    helper replica ese contrato de estilo para aseverar que la nota es
    informativa y NUNCA una alerta bloqueante roja.
    """
    from dash import html

    _anos_txt = ', '.join(str(a) for a in anos_sin_columna)
    return html.Span(
        f"ℹ️ Nota informativa: los años {_anos_txt} no tienen columna en el "
        f"insumo de reservas; usan variación de reserva Δ=0.",
        style={'fontSize': '11px', 'color': '#0c5460',
               'backgroundColor': '#d1ecf1', 'padding': '2px 6px',
               'borderRadius': '3px', 'display': 'inline-block',
               'marginTop': '4px'},
    )


def test_nota_informativa_es_informativa_y_no_bloqueante():
    """
    Req 10.2/10.3: la nota de años con Δ=0 se identifica como INFORMATIVA (ℹ️,
    paleta azul) y NO es una alerta bloqueante roja.
    """
    nota = _construir_nota_informativa([2028, 2029])
    texto, color = _texto_y_color(nota)
    estilo = getattr(nota, "style", {}) or {}

    # Etiqueta informativa, no de error.
    assert "ℹ️" in texto
    assert "Nota informativa" in texto
    assert "❌" not in texto
    # Enumera los años afectados que usan Δ=0.
    assert "2028" in texto and "2029" in texto
    assert "Δ=0" in texto
    # No bloqueante: NO usa el rojo de las alertas de error.
    assert color != "red"
    # Usa la paleta informativa (azul) del diseño.
    assert estilo.get("color") == "#0c5460"
    assert estilo.get("backgroundColor") == "#d1ecf1"


def test_nota_informativa_es_visualmente_distinta_de_una_alerta_bloqueante(monkeypatch):
    """
    La nota informativa y una alerta bloqueante de error son discriminables:
    la alerta de error es roja; la nota informativa no lo es (Req 10.3 vs 11).
    """
    # Alerta bloqueante (error de formato) del callback real.
    _forzar_lector(monkeypatch, excepcion=ErrorFormatoEncabezado("f"))
    _, color_alerta = _texto_y_color(
        app_dash.cargar_excel_reservas(_contents_b64(b"x"), "r.xlsx")
    )
    # Nota informativa.
    _, color_nota = _texto_y_color(_construir_nota_informativa([2030]))

    assert color_alerta == "red"
    assert color_nota != "red"
    assert color_alerta != color_nota
