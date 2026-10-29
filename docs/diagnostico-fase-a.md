# Diagnóstico AXIS — Fase A

> Auditoría técnica del proyecto para planear su limpieza y escalabilidad.
> Fecha: septiembre 2026. Alcance: solo lectura, sin cambios de código.

## Objetivo del proyecto de limpieza

Ordenar el proyecto antes de agregar nuevas funcionalidades (compras/ventas,
`portafolio_clasificado` proyectado). Plan de fases acordado:

- **Fase A — Diagnóstico** (este documento): entender el estado actual. ✅
- **Fase B — Centralización de rutas** (`comun/rutas.py`) en los módulos limpios. ✅
- **Fase D — Refactor de fondo** (des-spaghetti): leer datos de mercado desde la DB,
  quitar los `sys.path.insert`, unificar fuentes de mercado (TRM/IPC/UVR), partir
  monolitos, quitar duplicación. 🔄 EN CURSO — ver "Estado consolidado Fase D" abajo.
- **Fase C — Reorganización física** en carpetas (`etl/`, `motor/`, `comun/`, `dashboard/`).
- **Fase E — Nuevas funcionalidades**: compras/ventas y portafolio proyectado.

> **Nota de reordenamiento (D antes que C):** originalmente C iba antes que D. Se
> invirtió porque mover archivos a carpetas (C) es riesgoso mientras persista el
> acoplamiento por `sys.path.insert` e imports planos. La Fase D limpia ese acoplamiento;
> una vez los imports son absolutos y predecibles, la reorganización física (C) se vuelve
> mecánica y de bajo riesgo (la herramienta de mover archivos actualiza imports sola).

Regla de oro: una fase = uno o varios commits verificados. Nunca dejar el proyecto en estado roto.

---

## Limpieza ya ejecutada (previa a Fases B–E)

Eliminaciones de código muerto/obsoleto confirmadas con la compañera del proyecto:

- `motor_kiro.py` — versión vieja de `init_db`, apuntaba a `axis_portfolio.db` (inexistente).
- `scripts/dashboard_encaje.py` — dashboard Streamlit standalone, no usado.
- `scripts/dashboard_causacion.py` — dashboard Dash standalone, no usado.
- `scripts/crear_tablas_swaps.py` — apuntaba a base y CSV inexistentes.
- `scripts/generar_diccionario_swaps.py` — flujo manual obsoleto.
- `src/` (carpeta) — quedó vacía tras reorganización previa.
- `app_proyecciones.py` — dashboard paralelo obsoleto (2008 líneas). `app_dash.py` es el único oficial.
- `scripts/causacion_cierre.py` — ejercicio puntual. Se removió también su pestaña, layout
  y callback (`layout_causacion`, `correr_causacion_axis`) de `app_dash.py`. Verificado que compila.
- `Slides GIT, GitHub/` (carpeta completa) — material de capacitación de Git (3 PDFs) + dos
  `.py` huérfanos (`libro_de_funciones.py` copia vieja, `funcion_bono_yankee.py`). No pertenecía al repo.

Módulos de apoyo CONSERVADOS porque `app_dash.py` los usa:
`calcular_encaje.py`, `motor_proyeccion.py`, `curvas_rendimiento.py`,
`proyeccion_renta_variable.py`, `proyeccion_creditos.py`, `proyeccion_alternativos.py`.

---

## 1. Tamaño y complejidad

Líneas por archivo `.py` (mayor a menor):

| Líneas | Archivo |
|-------:|---------|
| **7277→~7000** | `app_dash.py` ⚠️ monolito crítico — único dashboard oficial (se removió la tab Causación) |
| ~~2008~~ | ~~`app_proyecciones.py`~~ — ELIMINADO (dashboard paralelo obsoleto) |
| 1398 | `scripts/valorar_bonos.py` |
| 1331 | `scripts/motor_proyeccion.py` |
| 1133 | `valoracion_derivados.py` |
| 939  | `gestor_informes.py` |
| 661  | `scripts/calcular_encaje.py` |
| 579  | `scripts/proyeccion_creditos.py` |
| 550  | `libro_de_funciones.py` |
| ~~539~~ | ~~`scripts/causacion_cierre.py`~~ — ELIMINADO (ejercicio puntual) |
| 523  | `clasificar_portafolio.py` |
| 512  | `scripts/curvas_rendimiento.py` |
| 503  | `scripts/proyeccion_alternativos.py` |
| ~~368~~ | ~~`Slides GIT, GitHub/libro_de_funciones.py`~~ — ELIMINADO (copia huérfana) |
| 326  | `diccionario_titulos.py` |
| 319  | `scripts/proyeccion_renta_variable.py` |
| 286  | `proyeccion_simultaneas_pasivas.py` |
| 279  | `scripts/orquestador_valoracion_rf.py` |
| 193  | `portfolio_loader_beta.py` |

> **Nota**: la carpeta `Slides GIT, GitHub/` (material de capacitación + copias `.py`
> huérfanas) fue eliminada. Ver sección "Limpieza ya ejecutada".

Puntos de spaghetti:
- **`app_dash.py` (7277 líneas)**: mezcla layout, callbacks, lógica de negocio, valoración e imports diferidos. Foco principal de deuda técnica.
- Funciones largas en `app_dash.py`: `_correr_validacion_impl` (~1606–1790), `layout_inputs` (1916+), callbacks de proyección (3440+, 5088+, 5934+).
- `motor_proyeccion.py`: función de proyección gigante (~787–1290) con ramas TD/VISTA USD/IPC/UVR.
- Copia obsoleta: `Slides GIT, GitHub/libro_de_funciones.py`.

---

## 2. Origen de inputs críticos — INCONSISTENCIA (hallazgo grave)

No hay una única fuente de verdad para los valores de mercado.

### TRM (4+ fuentes)
- `gestor_informes.py:260` `obtener_trm_implicita`: TRM implícita = valor_mercado/nominal de USD en `hist_titulos`.
- `gestor_informes.py:490` `_actualizar_trm_xlsx`: archivo `TRM.xlsx` (fallback CSV `Tasa de cambio del peso colombiano.csv:516`).
- `valoracion_derivados.py:128-134` `_cargar_trm_hist`: `Inputs Py/TRM.xlsx`.
- `valoracion_derivados.py:1283-1286`, `app_dash.py:1923`: tabla `hist_opciones`.
- Hardcodes en UI: `4012` (dashboard_causacion:95), `4000` (app_dash:1399,1572), `3796.78` (app_dash:1917), `4500/4800` estrés (2394/2399).
- Proyecciones: senda_trm reimplementada en `motor_proyeccion.py:788`, `proyeccion_renta_variable.py:172`, `proyeccion_alternativos.py:268`, `proyeccion_creditos.py:392`.

### IPC (2 implementaciones paralelas)
- `gestor_informes.py:315` `obtener_ipc_vigente`: `Serie Inflación.xlsx` con regla 6º día hábil.
- `scripts/valorar_bonos.py:588` `cargar_serie_ipc` + `:610` `_obtener_ipc_vigente`: mismo Excel, regla reimplementada aparte.
- Hardcodes: `7.07` (app_proyecciones:226), `9.0` estrés (:267), `6.14` (app_dash:1919).

### UVR (2 fuentes)
- `gestor_informes.py:410` `obtener_uvr_vigente`: implícita de TUVT en `hist_titulos`.
- Hardcodes: `416.3343`/`427.0` (dashboard_causacion:85/90, app_dash:1436).

### Curvas PIP
- `scripts/curvas_rendimiento.py:95` y `:179`: default hardcodeado `"Preciospip_Cons_T+0_20260522.csv"` (fecha inexistente en inputs; hay 20 y 27).
- `app_dash.py:1644`: `glob("Preciospip_*.csv")` toma el más reciente.
- `curvas_rendimiento.py:530`, `extraer_curva_con_nombres_db`: curvas desde tabla `hist_pip`.
- `CORP_IPC` no existe como curva real; se copia de `CORP_TF` como proxy (`curvas_rendimiento.py:170,259,590`).

---

## 3. Manejo de rutas

`Path(__file__)` (inconsistente):
- Raíz: `portfolio_loader_beta.py:17`, `gestor_informes.py:17`, `diccionario_titulos.py:21`, `clasificar_portafolio.py:20`, `app_proyecciones.py:19`, `app_dash.py:58`.
- `valoracion_derivados.py:41-44`: `SCRIPT_DIR=PROJECT_ROOT=INPUTS_DIR` iguales (CSV en raíz, no en Inputs Py).
- `scripts/*`: varios.

`sys.path.insert` (acoplamiento):
- `scripts/causacion_cierre.py:28-29`, `orquestador_valoracion_rf.py:28`, `dashboard_causacion.py:25-26`, `tools/cobertura_valoracion.py:11`.
- **`app_dash.py` inserta `scripts/` 20+ veces** dentro de callbacks: líneas 22, 1424, 1596, 1608, 2518, 3441, 3610, 3721, 4651, 4884, 4942, 5089, 5935, 7390, 7472, 7635, 7740, 7798, 7913, 7987.

`os.chdir` (peligroso, CWD global):
- `scripts/dashboard_causacion.py:30`, `tools/cobertura_valoracion.py:12`.

Fechas hardcodeadas en rutas (PROBLEMA):
- `scripts/curvas_rendimiento.py:95` y `:179`: `"Preciospip_Cons_T+0_20260522.csv"`.
- `scripts/dashboard_causacion.py:207,225`: `"Causacion_Cierre_2026.xlsx"`.

---

## 4. Imports y acoplamiento

- **`valorar_bonos` solo existe en `scripts/`** (no hay copia en raíz). Todos lo importan plano vía `sys.path.insert(scripts/)`.
- Imports diferidos dentro de funciones: `app_dash.py` (1611, 1749, 4731, 6101, 6142, 6197 + ~20 sys.path); `causacion_cierre.py` (283, 312, 360, 548); `motor_proyeccion.py` (595, 601, 1173, 1234).
- Mapa core:
  - `app_dash.py` → `gestor_informes` (plano) + `scripts/*` (vía path).
  - `valoracion_derivados.py` → `valorar_bonos` (scripts).
  - `scripts/motor_proyeccion.py`, `causacion_cierre.py`, `orquestador_valoracion_rf.py` → `valorar_bonos` + `curvas_rendimiento`.

---

## 5. Duplicación de código y cálculos

- `generar_senda_lineal`: `scripts/curvas_rendimiento.py:313` y callback en `app_dash.py:3017`.
- ~~Regla IPC 6º día hábil: `gestor_informes.py:315` vs `scripts/valorar_bonos.py:610`~~
  **RESUELTO (D.3):** la regla del 6º día hábil (que estaba escrita 3 veces: dos en
  `valorar_bonos` + una en `gestor_informes`) se unificó en `comun/fuentes_mercado.py`
  (`sexto_dia_habil`, `mes_ipc_vigente`). Ambos consumidores la importan; se conservan sus
  firmas y unidades de salida (gestor devuelve %, valorar_bonos decimal). Verificado idéntico
  en 5844 fechas (2015–2030) antes de reemplazar.
- Conversión nominal→EA repetida: `app_dash.py:3420,3556,3759` + scripts.
- Senda TRM reimplementada: `motor_proyeccion.py:788`, `proyeccion_renta_variable.py:172`, `proyeccion_alternativos.py:268`, `proyeccion_creditos.py:392`, `app_dash.py:3454,3771`.
- TRM implícita: `gestor_informes.py:260` + inline en `valoracion_derivados.py:1285`, `app_dash.py:1923`.
- Archivo duplicado completo: `Slides GIT, GitHub/libro_de_funciones.py` vs raíz.

---

## 6. Conexiones a base de datos

Sin patrón centralizado. Cada módulo abre su propia `sqlite3.connect`. Los dashboards tienen `get_conn()` local (`app_dash.py:67`, `app_proyecciones.py:24`) pero no compartido.

`sqlite3.connect` por módulo:
- **`gestor_informes.py`: 12** (78, 139, 234, 245, 260, 410, 672, 735, 804, 882, 952, 1009, 1054).
- `valoracion_derivados.py`: 3 (682, 707, 1283).
- `clasificar_portafolio.py`: 2 (245, 572); `diccionario_titulos.py`: 2 (228, 375); `portfolio_loader_beta.py`: 2 (31, 92).
- `proyeccion_simultaneas_pasivas.py`: 3 (87, 310).
- scripts: `motor_proyeccion.py:163`, `causacion_cierre.py:74,227`, `curvas_rendimiento.py:608`, `proyeccion_alternativos.py:190,219,596`, `proyeccion_creditos.py:597`, `proyeccion_renta_variable.py:123`.
- `proyeccion_alternativos.py:596` escribe a `axis_proyeccion_alternativos.db` y `scripts/resultado_proyeccion.db` (DBs distintas a portafolio.db).

Riesgo de seguridad: `valoracion_derivados.py:706-707` construía SQL por f-string con
`fecha_corte` interpolada (posible inyección). **RESUELTO (D.4):** parametrizado con `?`
y conexión gestionada con `with`. También se corrigió el mismo patrón en
`clasificar_portafolio.py` (WHERE fecha_reporte por f-string → `?`). Los f-strings que
quedan (`app_dash.load_table`, `guardar_diccionario`, `cargar_senda_repo`,
`gestor_informes` semáforo) interpolan solo NOMBRES de tabla/columna fijos del código
(no input de usuario, no parametrizables con `?`); los valores ya van parametrizados.

---

## Hallazgos priorizados por severidad

1. **[Crítico]** Múltiples fuentes de verdad para TRM/IPC/UVR (§2). 🔄 PARCIAL:
   regla IPC unificada y hardcodes de mercado en app_dash eliminados; pendiente unificar
   la fuente TRM (D.1d) y una capa `fuentes_mercado` para UVR/IPC vigentes.
2. **[Crítico]** `app_dash.py` de ~7000 líneas con ~20 `sys.path.insert` e imports diferidos (§1,§3,§4). ⏳ pendiente (D.5/Fase C).
3. **[Alto]** Fechas hardcodeadas en rutas (§3). ✅ RESUELTO: fechas PIP del sidebar; sin fechas mudas en callbacks.
4. **[Alto]** Sin capa DB centralizada; 40+ `sqlite3.connect` (§6). ⏳ pendiente (resto de D.4).
5. **[Alto]** SQL por f-string en `valoracion_derivados.py:707` (§6). ✅ RESUELTO (parametrizado con `?`; ídem clasificar_portafolio).
6. **[Medio]** Duplicación de senda TRM, regla IPC, archivo `libro_de_funciones.py` duplicado (§5).
   ✅ regla IPC unificada (comun/fuentes_mercado); ✅ senda TRM (patrón dict) unificada en
   comun/fuentes_mercado; `libro_de_funciones` duplicado ya se eliminó en la limpieza previa.

---

## Recomendación de ejecución (Fase D, por impacto)

1. Unificar origen de TRM/IPC/UVR en un único módulo (`fuentes_mercado.py`) que todos consuman; eliminar hardcodes.
2. Centralizar rutas y DB (`comun/rutas.py` + `get_conn` compartido).
3. Desmontar `app_dash.py`: separar layout, callbacks y lógica de negocio.
4. Eliminar duplicación (senda TRM, regla IPC, archivo duplicado en Slides).

---

## Decisiones de diseño para Fase D (acordadas en revisión)

### D.1 — Leer datos de mercado desde la DB, no desde archivos físicos

**Problema:** hoy `gestor_informes.py` guarda cada informe subido por el dashboard
en DOS lugares: la tabla correspondiente de la DB Y una copia física en la raíz del
proyecto. `valoracion_derivados.py` lee esas copias físicas (curvas, vol, TRM). Como
los nombres llevan fecha, cada corte deja archivos nuevos en la raíz que nunca se
borran → basura acumulada.

Archivos escritos redundantemente a la raíz (ya están en la DB):
- `Preciospip_Cons_T+0_{fecha}.csv` → tabla `hist_pip`
- Curvas Forward `.csv` → `hist_curvas_derivados`
- Vol `usdcop_strike*.csv` → `hist_vol_derivados`
- Curvas OTC `.csv` → `hist_curvas_otc`

**Decisión:** migrar `valoracion_derivados.py` (y demás consumidores) a leer los datos
de mercado desde las tablas de la DB, indexando por `fecha_reporte`. Una vez migrado,
eliminar la escritura de archivos físicos redundantes en `gestor_informes.py`.

**Beneficios:** fuente única de verdad, sin basura en la raíz, valoración multi-fecha
nativa (WHERE fecha_reporte = X), consistencia con el resto del sistema.

**Cuidado:** `valoracion_derivados` transforma el CSV crudo (tasas continuas/EA/simples).
Verificar que las tablas guardan las columnas necesarias (`Nombre_Indicador`,
`Plazo_en_Dias`, `Valor` — confirmado que sí) y comparar resultados de valoración
antes/después (validación contra PORFIN ya existente).

### D.2 — Fuente de TRM: pendiente de decidir (archivo local vs API SuperFinanciera)

`TRM.xlsx` hoy lo GENERA `gestor_informes.py` (TRM implícita del 597, función
`_actualizar_trm_xlsx`) y lo CONSUME `valoracion_derivados.py` (`_cargar_trm_hist`,
serie histórica para forwards vencidos).

**Alternativa investigada — API oficial (viable técnicamente):**
- TRM oficial de la SuperFinanciera publicada en datos.gov.co (portal de datos abiertos),
  dataset **`mcec-87by`**, vía **SODA API (Socrata)**:
  `https://www.datos.gov.co/resource/mcec-87by.json` — endpoint REST-JSON estable,
  filtrable por fecha (`$where`, `$order`, `$limit`). Integración trivial con `requests`.
- Existe también un web service SOAP legacy de la SuperFinanciera (fallback, más engorroso).

**Decisión recomendada — arquitectura híbrida:**
- La valoración SIEMPRE lee la TRM de la DB local (rápido, determinista, sin red en el camino crítico).
- Proceso separado/opcional (botón o script) consulta el endpoint SODA para rellenar la
  tabla local con fechas faltantes. Con HTTPS, timeout bajo, allowlist del dominio y
  FALLBACK: si datos.gov.co no responde, se usa lo que ya está en la DB.

**Nota conceptual:** la TRM implícita del 597 y la oficial de la SuperFinanciera
representan el MISMO dato (el 597 valora sus USD con la TRM oficial; despejarla del
reporte la recupera). Posibles diferencias menores solo por: (a) redondeos al despejar
valor_mercado/nominal, y (b) regla de vigencia de fecha (la TRM certificada un día aplica
al día hábil siguiente) — hay que alinear la fecha al integrar. Conclusión: la API oficial
es una forma más limpia y directa de obtener el mismo número. Uso adicional recomendado:
comparar implícita vs oficial como control de calidad (si difieren > umbral pequeño → alerta de datos).

**Pendiente de entorno:** validar con infraestructura si la salida a `datos.gov.co`
está permitida (proxy/firewall corporativo).

---

## Hallazgo durante Fase B: falta el inicializador de la DB (init_db)

Al migrar rutas se detectó que **`init_db.py` ya no existe** en el proyecto (fue
eliminado/no incluido en la reorganización de `main`). Ese script era el que creaba
`portafolio.db` y cargaba las tablas maestras `param_*` desde los CSV de `Inputs Py/`
(`mapeo_emisores`, `mapeo_especies`, `mapeo_excepciones`, `matriz_*`, `categorias_*`,
`diccionario_columnas`, etc.).

**Estado actual:** las tablas `param_*` existen en la `portafolio.db` actual (por eso
el sistema funciona), pero **no hay código que las recree desde los CSV**. Si se borra
la DB, no se puede regenerar la configuración maestra con el código presente.

**Riesgo:** medio-alto. Es un punto único de falla no reproducible.

**RESUELTO:** `init_db.py` existía en la rama `solvencia` (como `src/init_db.py`). Se
recuperó a la raíz de la rama de trabajo, se corrigió el encoding y se migró a
`comun/rutas.py`. Carga los CSV maestros a `param_*` (incluye `es_ipc` automáticamente
al ser read_csv → to_sql completo). Verificado import OK sin ejecutarlo (borra/recrea la DB).

---

## Cierre de Fase B (centralización de rutas)

**Migrados a `comun/rutas.py`** (cambio limpio y definitivo):
- `portfolio_loader_beta.py` (INPUTS_DIR, DB_PATH)
- `diccionario_titulos.py` (DB_PATH)
- `clasificar_portafolio.py` (DB_PATH)
- `init_db.py` (INPUTS_DIR, DB_PATH) — recuperado desde rama solvencia

**Aplazados a Fases C/D** (se reescriben de fondo, no tiene sentido tocarlos dos veces):
- `gestor_informes.py` — su manejo de archivos de mercado físicos se elimina en D
  (pasa a leer/escribir solo en tablas SQL).
- `valoracion_derivados.py` — migra a leer curvas/vol/TRM desde la DB en D.
- `app_dash.py` — se le quitan los ~20 `sys.path.insert` en C y se migra el acceso a
  datos en D; migrar solo DB_PATH ahora sería cosmético y arriesgado en el monolito.
- `scripts/*` — dependen de `sys.path.insert`; se ordenan en la Fase C.

---

## Fase D.1 — Migrar valoracion_derivados a leer datos de mercado desde la DB

### REGLA TRANSVERSAL OBLIGATORIA (todas las _cargar_*)
Antes los CSV eran una sábana de UNA sola fecha (el nombre del archivo garantizaba
el día). Las tablas `hist_*` ahora acumulan HISTORIA de todas las fechas. Por lo tanto
TODA consulta migrada DEBE filtrar por `WHERE fecha_reporte = ?` (parametrizado), para no
mezclar curvas/strikes/días de fechas distintas. Excepción: `_cargar_trm_hist` es serie
histórica y usa `WHERE fecha <= ?` (acumulado hasta la fecha de corte).
Además: validar que la consulta devolvió filas; si viene vacía para esa fecha, error claro
(no valorar con datos vacíos).

Subpasos (menor a mayor riesgo), cada uno con test de equivalencia CSV vs DB:
- **D.1a** — `_cargar_curvas` ← `hist_curvas_derivados` (cols Nombre_Indicador, Plazo_en_Dias, Valor: idénticas). Riesgo bajo.
- **D.1b** — `_cargar_curvas_swaps` ← `hist_curvas_otc` (cols Curva, Plazo_en_Dias, Valor: idénticas). Riesgo bajo.
- **D.1c** — `_cargar_vol` ← `hist_vol_derivados`. Riesgo MEDIO (ver nota crítica).
- **D.1d** — TRM histórica: APLAZADA. Se mantiene por ahora leyendo `TRM.xlsx` local.
  Razones: (1) funciona y no genera basura (es UN archivo que se sobrescribe, no uno por
  fecha como las curvas); (2) la decisión de fuente (implícita del 597 vs API oficial
  datos.gov.co mcec-87by) aún no está cerrada y depende de validar acceso web corporativo;
  migrar a `hist_trm` ahora con la implícita implicaría rehacerlo si luego se adopta la API.
  Se retoma después: crear tabla `hist_trm` + decidir fuente + migrar `_cargar_trm_hist`.

### Estado Fase D.1 (curvas/vol/TRM de valoracion_derivados)
- D.1a ✅ curvas Forward ← hist_curvas_derivados
- D.1b ✅ curvas OTC swaps ← hist_curvas_otc
- D.1c ✅ volatilidad ← hist_vol_derivados (test de valoración completa idéntico al baseline todo-CSV)
- D.1d ⏸️ TRM: aplazada, sigue en TRM.xlsx local.

Resultado: `valoracion_derivados` ya no depende de los CSV de curvas ni de vol (solo TRM.xlsx).

### Nota crítica D.1c — orientación de la matriz de volatilidad
`construir_interpolador_vol` (libro_de_funciones) espera un DataFrame con:
- **index = strikes** (float)
- **columns = 0..N-1** donde la posición = (día_al_vencimiento - 1)
Busca vol con `iloc[fila_por_strike, col=dias-1]`.

El CSV `usdcop_strike*.csv` se transpone (`raw.T[3:]`) para producir esa orientación.

PERO la tabla `hist_vol_derivados` está en la orientación OPUESTA:
- cada **fila** es un tenor (columna `'0'` = día)
- las **columnas** con nombre de strike (2138.90, 2155.57, ...) contienen la vol.

Al migrar, `_cargar_vol` debe RECONSTRUIR la orientación del CSV: strikes al índice,
días como columnas renumeradas 0..N-1. Si se deja al revés, el interpolador busca en
el lugar equivocado y la valoración de opciones sale mal.

**Test obligatorio D.1c:** comparar `interpol_vol(strike, dias)` cargado por CSV vs por
DB para varios pares (incluye strikes extremos y día máximo). Debe dar valores idénticos.

---

## Fase D.2 — Eliminar escritura de archivos físicos redundantes (BLOQUEADA por lectores no migrados)

Objetivo: que `gestor_informes` deje de copiar los CSV a la raíz (solo DB). PERO al
mapear los lectores, se encontró que varios módulos AÚN leen esos archivos de disco:

**Curvas Forward (`Curvaspip_Forward_LocalSimple_*.csv`):**
- `valoracion_derivados` ✅ ya migrado (lee de hist_curvas_derivados).
- `app_dash.py:1673` ❌ todavía lee el archivo por glob para GRAFICAR la curva PIP.
  → migrar ese gráfico a leer de hist_curvas_derivados antes de dejar de escribir el CSV.

**Vol (`usdcop_strike*.csv`) y OTC (`Curvaspip_Derivados_OTCSBOL_*.csv`):**
- Solo los leía `valoracion_derivados` ✅ ya migrado. Estos SÍ se podrían dejar de escribir
  una vez confirmado que ningún gráfico del dashboard los usa.

**PIP (`Preciospip_Cons_T+0_*.csv`):** NO tocar. Lo leen en disco:
- `app_dash.py` (varias funciones de proyección RF y gráficos)
- `scripts/motor_proyeccion.py`, `scripts/orquestador_valoracion_rf.py`,
  `scripts/curvas_rendimiento.py` (default hardcodeado 20260522).
- Toda la proyección de renta fija depende del PIP en disco. Migrarlo a `hist_pip`
  es un frente grande aparte (toca app_dash + scripts).

**Serie Inflación.xlsx:** lo leen `app_dash` y `scripts/valorar_bonos`. No migrado.

**Conclusión:** la D.2 (eliminar escritura de archivos) NO se puede hacer de forma segura
todavía. Requiere primero migrar los lectores restantes a la DB. Orden sugerido:
1. Migrar el gráfico de curva PIP de app_dash (Forward) a hist_curvas_derivados.
2. Dejar de escribir Forward/OTC/vol en disco (ya sin lectores).
3. Frente separado: migrar la cadena PIP de renta fija a hist_pip.

### Frente PIP (Preciospip_Cons_*) — estado detallado
BUENA NOTICIA: la mitad ya está hecha.
- `gestor_informes.procesar_pip` ya guarda el PIP en tabla `hist_pip` (+ copia archivo).
- `curvas_rendimiento.extraer_curva_con_nombres_db(ruta_db, fecha)` YA lee curvas de hist_pip.
- `app_dash.cargar_curvas_pip` (gráfico principal de curvas) YA usa la versión DB.
- `motor_proyeccion` ya hace LEFT JOIN hist_pip para el rendimiento.

Falta migrar los que aún leen el ARCHIVO PIP:
- `curvas_rendimiento.extraer_curva_base_pip(ruta_archivo)` → crear variante `_db(ruta_db, fecha)`
  que devuelva el mismo dict {curva: {dias: tasa}} usando `_construir_curvas_desde_df` (ya existe).
- `app_dash.py` llamadas con ruta de archivo (líneas ~1463, 4476, 4558, 4772, 5853), varias con
  fecha HARDCODEADA 20260522 → reemplazar por versión DB con la fecha del sidebar.
- `scripts/orquestador_valoracion_rf.py` (glob del PIP).
Tamaño: MEDIANO (no enorme), porque la tabla y una función lectora ya existen.
Además: eliminar las fechas hardcodeadas 20260522 al hacerlo.


---

## HALLAZGO CRÍTICO TRANSVERSAL — Datos de mercado y fechas hardcodeadas

**Principio:** NINGÚN dato de mercado (TRM, UVR, IPC, curvas) ni fecha de corte debe
estar hardcodeado en el código. Todo debe provenir de la fuente única (DB / inputs del
dashboard) para la fecha seleccionada por el usuario. Un valor fijo en el código puede
hacer que el sistema valore con datos desactualizados SIN que el usuario lo note. Es un
error grave en un sistema de valoración en producción.

### Inventario consolidado de hardcodes (a eliminar)

**A) Fechas de PIP hardcodeadas que AFECTAN resultados (bug, no fallback):**
- `app_dash.py` `calcular_sensibilizacion` (botón Sensibilizar): línea ~4476
  `extraer_curva_base_pip(...20260522.csv)` y ~4558 `extraer_curva_con_nombres(...20260522.csv)`
  + `fecha_val = dt_date(2026,5,22)`. El callback NI SIQUIERA recibe la fecha del sidebar.
- `app_dash.py` `proyectar_renta_fija` (proyección RF): línea ~4772
  `extraer_curva_base_pip(...20260522.csv)` aunque `fecha` sí sale del sidebar (inconsistencia:
  proyecta con fecha correcta pero curvas del 22-may).
- La fecha 20260522 NI EXISTE en los inputs (hay 20260520 y 20260731).

**B) Fechas hardcodeadas como fallback (menos grave, pero a limpiar):**
- `app_dash.py`: `'2026-07-27'` (~1429), `dt_date(2026,5,22)` (~4706, 4767), `...20260727.csv` (~5852).
- Defaults de función: `curvas_rendimiento.py` `extraer_curva_base_pip` y `extraer_curva_con_nombres`
  con default `"Preciospip_Cons_T+0_20260522.csv"`.

**C) Valores de mercado hardcodeados (TRM/UVR/IPC) como fallback en la UI:**
- TRM: `3796.78` (~1733,1744,4306,4522), `4000` (~1399,1572), `3100` (~6089), `4500/4800` (estrés).
- UVR: `416.33` (múltiples), `427.0` (~2260), `411.28` (~4770).
- IPC: `6.14` (~1919), `7.07`, `9.0` (estrés).
  → Deben venir de gestor_informes (obtener_trm_implicita / obtener_ipc_vigente / obtener_uvr_vigente)
    para la fecha del sidebar, no de constantes.

**D) Solo pruebas (`if __name__ == "__main__"`) — no afectan producción, pero conviene parametrizar:**
- `valoracion_derivados.py:1333`, `scripts/motor_proyeccion.py:1509`,
  `scripts/orquestador_valoracion_rf.py:323`, `scripts/valorar_bonos.py:1616+`.

**E) Datos de ejemplo:** `proyeccion_simultaneas_pasivas.py` `SENDA_REPO_EJEMPLO`.

### Acción requerida (Fase D)
1. Todos los datos de mercado y fechas provienen de la fuente única para la fecha del sidebar.
2. Eliminar A y B (bugs de fecha PIP): usar `extraer_curva_base_pip_db` / `extraer_curva_con_nombres_db`
   con la fecha del sidebar; agregar el State de fecha a `calcular_sensibilizacion`.
3. Eliminar C: reemplazar constantes TRM/UVR/IPC por llamadas a gestor_informes.
   Si se requiere un fallback, que sea explícito y visible (aviso en UI), no un número mudo.
4. Objetivo final: CERO valores de mercado o fechas mudas en el código.


---

## Avance frente PIP + hardcodes (sesion de correcciones)

### Hardcodes eliminados en app_dash.py (hallazgo critico resuelto)
- Fechas PIP: los 4 callbacks (proyectar_renta_fija, calcular_sensibilizacion,
  _correr_validacion_impl, correr_escenario_consolidado) leen de hist_pip con la fecha
  del sidebar. Eliminado 20260522/20260727 fijos.
- Valores de mercado: TRM/UVR/IPC vienen de la fuente vigente (helpers obtener_trm_para,
  obtener_uvr_para, obtener_ipc_para) para la fecha; eliminados defaults mudos
  (3796.78, 416.33, 6.14, 4200, 3100, 411.28, 427.0).
- UVR: eliminados campos editables del escenario. UVR t0 = vigente; UVR cierre = calculada
  desde la senda de inflacion (_calcular_uvr_cierre_desde_inflacion). Nunca cae a uvr_t0.

### Migracion PIP a hist_pip
- Nueva funcion extraer_curva_base_pip_db (equivalente al CSV, validada).
- Dashboard y orquestador_valoracion_rf ya leen de hist_pip.
- Las funciones CSV extraer_curva_base_pip / extraer_curva_con_nombres solo quedan en el
  bloque __main__ de curvas_rendimiento.py (demo).

### D.2 PIP — COMPLETADO
- PIP INTERNACIONAL: se creó tabla separada `hist_pip_int`. gestor_informes clasifica
  `Preciospip_INT_SBOL` como tipo PIP_INT (patrones Cons/INT mutuamente excluyentes, no
  dependen del orden del dict) y lo guarda en `hist_pip_int` sin archivo físico.
  `motor_proyeccion` lee el spread por ISIN desde `hist_pip_int` (ya no del glob CSV).
- `cargar_portafolio_proyeccion(ruta_db, fecha_pip)`: filtra hist_titulos/hist_pip/
  hist_pip_int por la fecha de corte del sidebar (fallback a MAX si no se pasa). Las 4
  llamadas de app_dash pasan la fecha del sidebar.
- gestor_informes.procesar_pip YA NO escribe el CSV físico del PIP Cons (solo hist_pip).
- El semáforo (consultar_estado_informes) ya no usa el fallback por glob de archivo para
  el PIP; su estado sale de hist_pip / hist_pip_int (paso 1). PIP_INT agregado a tablas_directas.

Archivos de mercado que YA NO se escriben a disco: curvas Forward, OTC, vol y PIP Cons.
Se conservan en disco (por decisión/lectores no migrados): TRM.xlsx (D.1d aplazado) y
Serie Inflación.xlsx.
Nota: los CSV de PIP ya existentes en la raíz no se borran solos; solo se dejó de crear nuevos.

### Hardcodes menores pendientes (bajo impacto)
- scripts/curvas_rendimiento.py: defaults "Preciospip_Cons_T+0_20260522.csv" en las firmas
  de las funciones CSV (solo se usan en __main__).
- Fechas en bloques if __name__=="__main__" de varios scripts (solo pruebas manuales).


---

## Estado consolidado Fase D (tablero único)

Resumen de dónde va la Fase D. Los detalles de cada punto están en las secciones previas.

| Subfase | Descripción | Estado |
|---------|-------------|--------|
| D.1a | Curvas Forward de `valoracion_derivados` ← `hist_curvas_derivados` | ✅ hecho |
| D.1b | Curvas OTC swaps ← `hist_curvas_otc` | ✅ hecho |
| D.1c | Volatilidad ← `hist_vol_derivados` (orientación reconstruida) | ✅ hecho |
| D.1d | TRM histórica (fuente y tabla `hist_trm`) | ⏸️ aplazada (decisión de fuente + acceso web) |
| D.2 | Dejar de escribir CSVs físicos de mercado (curvas/vol/OTC/PIP Cons) | ✅ hecho |
| D.2-PIP INT | Tabla separada `hist_pip_int` + motor lee de DB + fecha de corte del sidebar | ✅ hecho |
| D.3 (IPC) | Regla del 6º día hábil unificada en `comun/fuentes_mercado.py` | ✅ hecho |
| D.3 (senda TRM) | `construir_senda_trm_dict` unificado (patrón dict desde store) | ✅ hecho |
| D.3 (UVR/IPC vigente) | Capa común para UVR/IPC vigentes (hoy en helpers de app_dash) | ⏭️ diferido a Fase E (YAGNI: solo los usa app_dash hoy; mover a `comun` ahora crearía import circular con gestor_informes sin un 2º consumidor real) |
| D.4 (seguridad) | SQL por f-string con `fecha_corte` → parametrizado | ✅ hecho |
| D.4 (capa DB) | `get_conn` compartido en `comun/db.py`; app_dash delega en él | ✅ infraestructura lista. Migración de los ~20 `connect(DB_PATH)` de módulos de la raíz es INCREMENTAL (se hace al tocar cada módulo, no en un barrido masivo, para no arriesgar estabilidad sin beneficio funcional). Los `scripts/*` reciben `ruta_db` por parámetro y se dejan así. |
| D.5 | Desmontar el monolito `app_dash.py` + quitar `sys.path.insert` | 🔄 EN CURSO: eliminados los 18 `sys.path.insert(scripts/)` redundantes dentro de callbacks (el path se inserta 1 sola vez a nivel de módulo, línea ~22). Verificado que compila e importa. Falta: separar layout/callbacks/lógica y convertir `scripts/` en paquete con imports absolutos (prerequisito de Fase C). |

Módulo nuevo creado en D.3: `comun/fuentes_mercado.py` (regla 6º día hábil del IPC y
construcción de la senda TRM desde el store del dashboard). Sin datos de mercado
hardcodeados: el "clamp al último valor" de la senda usa el último dato del store, no
una constante.
