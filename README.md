# AXIS — Asset-Liability eXecution & Investment Simulator

Sistema de proyección y valoración del portafolio de inversiones para la Gerencia Financiera del sector asegurador colombiano. Integra valoración de renta fija, derivados, renta variable, alternativos y créditos, con proyección mes a mes bajo escenarios macroeconómicos.

## Componentes principales

| Componente | Archivo | Rol |
|-----------|---------|-----|
| Dashboard principal | `app_dash.py` | Aplicación Dash (puerto 8063): inputs, escenarios, resultados, encaje, causación |
| Proyecciones (Dash) | `app_proyecciones.py` | Tabs de renta variable, créditos y alternativos |
| Motor RF | `scripts/motor_proyeccion.py` | Valoración y proyección mensual de renta fija (reinversiones, snapshots) |
| Valoración de bonos | `scripts/valorar_bonos.py` | Fórmulas BVC: TF, UVR, IPC, Yankee, corporativo |
| Curvas de rendimiento | `scripts/curvas_rendimiento.py` | Carga PIP, interpola nodos, genera sendas |
| Motor de derivados | `valoracion_derivados.py` | Forwards, opciones y swaps (determinista) |
| Gestor de informes | `gestor_informes.py` | Carga de archivos fuente (597, PIP, FWD, OPC, SWP, CURV, VOL, OTC, IPC) |
| Clasificación | `clasificar_portafolio.py` | Clasificación diaria por asset class, solvencia, régimen, contable |
| Diccionario de títulos | `diccionario_titulos.py` | Diccionario atemporal incremental de especies |
| Encaje | `scripts/calcular_encaje.py` | Posición vs reservas por ramo |
| Causación | `scripts/causacion_cierre.py` | Causación contable de cierre |

## Estructura del proyecto

```
AXIS/
├── app_dash.py                 # Dashboard principal (entrypoint)
├── app_proyecciones.py         # Proyecciones RV / créditos / alternativos
├── gestor_informes.py          # Carga y dispatch de archivos fuente
├── clasificar_portafolio.py    # Motor de clasificación
├── diccionario_titulos.py      # Diccionario de especies
├── valoracion_derivados.py     # Motor de derivados
├── libro_de_funciones.py       # Funciones de limpieza y valoración BS
├── datos_economicos.py         # Carga de inputs macroeconómicos
├── proyeccion_simultaneas_pasivas.py
├── motor_kiro.py               # Carga inicial de parámetros a SQLite
├── portfolio_loader_beta.py    # Cargador legacy del portafolio
├── scripts/                    # Motores y utilidades de cálculo
│   ├── motor_proyeccion.py
│   ├── valorar_bonos.py
│   ├── curvas_rendimiento.py
│   ├── calcular_encaje.py
│   ├── causacion_cierre.py
│   ├── crear_tablas_swaps.py
│   ├── orquestador_valoracion_rf.py
│   ├── proyeccion_renta_variable.py
│   ├── proyeccion_creditos.py
│   ├── proyeccion_alternativos.py
│   ├── dashboard_causacion.py
│   └── dashboard_encaje.py
├── tools/                      # Utilidades y diagnósticos (no producción)
│   ├── verificar_spread_mercado.py
│   ├── cobertura_valoracion.py
│   └── convert_*.py            # Conversión Markdown → PDF
├── docs/                       # Documentación (PDF)
├── .kiro/                      # Specs y contexto del proyecto
└── archive/                    # Archivos obsoletos (no versionado)
```

## Datos

Los archivos de datos (mercado, portafolio, bases de datos) **no se versionan** —
ver `.gitignore`. Se cargan localmente en la raíz del proyecto:

- **Bases de datos:** `portafolio.db` (principal), `axis_portfolio.db` (swaps)
- **Datos de mercado:** `Preciospip_*.csv`, `Preciospip_INT_SBOL_*.csv` (internacional),
  `Curvaspip_*.csv`, `usdcop_strike*.csv`, `TRM.xlsx`, `Serie Inflación.xlsx`
- **Portafolio fuente:** `PFTIS597*.CSV`, `PFTIT597*.CSV`
- **Diccionarios:** `diccionario_especies.csv`, `Diccionario_Swaps.csv`, `Régimen_diccionario.csv`

## Instalación

```bash
git clone <repo-url>
cd AXIS
pip install -r requirements.txt
```

## Uso

```bash
# Dashboard principal
python app_dash.py       # servidor en http://localhost:8063
```

Flujo típico:
1. Cargar archivos fuente del día (597, PIP, curvas) vía el drop zone del dashboard.
2. Definir sendas macro (TRM, inflación, tasa repo) en la Sección 2 — Variables Macro.
3. Configurar shifts de curvas y método de derivados.
4. Ejecutar el escenario ("CORRER ESCENARIO") para proyectar los 6 motores.
5. Revisar resultados por compañía, asset class y clasificación contable; descargar Excel.

## Metodología de valoración (renta fija)

- **TF / Corporativo:** fórmula BVC con yield del PIP + shift gradual por escenario.
- **UVR:** valoración en unidades UVR × senda UVR.
- **IPC:** spread deflactado + recomposición con IPC vigente/proyectado.
- **Yankee / internacional:** rendimiento del PIP internacional (`Preciospip_INT_SBOL`) × TRM.
- **Vista USD / Time Deposits:** `nominal × TRM` (+ intereses para Time Deposits, renovación a 50 días).
- **FIC / Cuenta ahorro:** proyección con senda de tasa repo (Cta Ahorro = repo − 6%, piso 0%).

Lógica contable por clasificación: NEG y VC llevan todo a P&L; DV separa P&L (causación a TIR) y ORI (variación a mercado).

## Dependencias

Ver `requirements.txt`. Principales: pandas, numpy, openpyxl, dash, plotly.

## Backlog / Deuda técnica

El trabajo identificado pero aún no ejecutado se registra en
[`docs/deuda-tecnica.md`](docs/deuda-tecnica.md). Incluye, entre otros, la **Fase 4 —
optimización del motor de proyección** (bisección de TIR, caché de calendario de cupones,
valoración en paralelo) aplazada tras habilitar horizontes largos con background callbacks.

## Licencia

Uso interno — Gerencia Financiera.
