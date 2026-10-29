# Deuda técnica — AXIS

Registro de trabajo identificado pero aún no ejecutado. Cada entrada debe tener
contexto suficiente para retomarse sin depender de conversaciones previas.

---

## Fase 4 — Optimización del motor de proyección

**Estado:** pendiente (aplazada intencionalmente).
**Prioridad:** media. No bloquea funcionalidad, pero mejora la experiencia con
horizontes largos y habilita cómodamente la proyección por tramos (varios años).

### Contexto

Se rediseñó el manejo del horizonte de proyección en tres fases previas:

1. **Fase 2** — se eliminó el recorte silencioso a 12 meses (`if n_meses > 12`) en
   `app_dash.py`; ahora el motor respeta el horizonte real y lo reporta en el mensaje
   de estado.
2. **Fase 3** — el callback del escenario (`correr_escenario_consolidado`) se migró a
   `@app.callback(background=True)` con `DiskcacheManager`, sacando la corrida a un
   worker y eliminando el timeout HTTP del navegador en horizontes largos.

Con eso, una proyección de 60 meses ya corre completa y sin cortarse. **Pero sigue
siendo lenta**: el costo del motor crece aproximadamente lineal con N_títulos × M_meses,
porque re-valora cada título mes a mes con las fórmulas BVC completas. La Fase 4 ataca
ese costo para que horizontes largos (y tramos encadenados) sean cómodos.

### Focos de optimización (en `scripts/motor_proyeccion.py`)

1. **Bisección de TIR de 500 iteraciones por título/mes (bloque IPC).**
   - Ubicación: `proyectar_portafolio`, bloque de re-valoración IPC para clasificación
     1VC/1DV — `for _ in range(500):` (aprox. línea 1360).
   - Problema: 500 iteraciones de bisección por cada título IPC en cada mes es mucho más
     de lo necesario para la precisión requerida.
   - Propuesta: reducir a ~50–60 iteraciones (la bisección converge exponencialmente;
     con `[-0.05, 3.0]` de rango, ~40 iteraciones dan precisión < 1e-9), o cambiar a
     Newton-Raphson con fallback a bisección. Validar que el VTIR no cambia material.

2. **Recálculo del calendario de cupones en cada iteración.**
   - Ubicaciones: `detectar_cupones` (bucle `while True` que arma `fechas_cupon`,
     aprox. línea 541) y el bloque IPC (segundo `while True`, aprox. línea 1305).
   - Problema: las fechas de cupón de un título son estables (dependen de emisión,
     vencimiento y frecuencia), pero se reconstruyen cada mes para cada título.
   - Propuesta: precalcular y **cachear** el calendario de cupones por título una sola
     vez al inicio de la corrida (p. ej. un dict `{id_posicion: [fechas]}`), y reutilizarlo
     en cada mes. Cuidado con las reinversiones/vencimientos que crean títulos nuevos:
     invalidar/añadir su calendario al crearse.

3. **Valoración título a título secuencial.**
   - Ubicación: el `for titulo in portafolio_activo:` de re-valoración mensual
     (aprox. línea 1174).
   - Problema: cada título se valora en serie dentro de cada mes.
   - Propuesta: valorar en paralelo los títulos independientes (p. ej.
     `concurrent.futures` o vectorización por tipo de bono). Ojo: la valoración muta el
     `Titulo` (VM/VTIR/tasa) y comparte `serie_ipc`/sendas; hay que asegurar que no haya
     estado compartido mutable entre workers (pasar copias o resultados inmutables).

### Criterios de aceptación al retomar

- Los resultados numéricos (VM, VTIR, P&L por mes) deben ser equivalentes a los actuales
  dentro de una tolerancia acordada (no cambiar el comportamiento observable, solo el
  tiempo). Comparar contra una corrida de referencia antes/después.
- La suite de tests (`pytest tests/`) debe seguir en verde.
- Medir el tiempo de una corrida representativa (p. ej. 60 meses) antes y después para
  cuantificar la mejora.

### Referencias

- `scripts/motor_proyeccion.py` — `proyectar_portafolio`, `detectar_cupones`.
- Fases previas: ver historial de la rama `proyeccion-por-tramos` (commits de Fase 2 y 3).

---

## Proyección por tramos multiactivo — deuda técnica acumulada

Registro de decisiones tomadas "sobre la marcha" durante la implementación y afinamiento
de la feature `proyeccion-por-tramos-multiactivo` (modo por tramos con RF + RV +
Alternativos + Créditos, inputs por tramo, efecto moneda). Todo lo listado FUNCIONA en
producción hoy; son mejoras de calidad/UX aplazadas, no bugs abiertos.

### D1. Sincronizar el spec con el código real

**Prioridad:** alta (mantenibilidad).
Durante el afinamiento se hicieron múltiples cambios directos al código que NO se
reflejaron de vuelta en `.kiro/specs/proyeccion-por-tramos-multiactivo/` (design.md y
tasks.md). El spec quedó desfasado respecto a la implementación. Cambios no documentados
en el spec:

- Eliminación del hardcode `3900.0` de la TRM t0 y del uso de `trm_final` como fallback;
  ahora la TRM t0 es la implícita del 597 y, si falta, se aborta con error verificable.
- Repo e inflación por tramo pasaron de senda PLANA a INTERPOLADA desde el cierre del
  tramo anterior (helper `interpolar_nivel_tramo`), con nuevos campos `repo_cierre` /
  `inflacion_cierre` en `EstadoEncadenado`.
- Arranque del primer tramo: repo desde `esc-repo-ini` (Sección 2), inflación desde
  `esc-ipc-vigente` (campo nuevo en Sección 2) → `obtener_ipc_para` → fallback al input.
- Eliminación de las filas "Spread Corp IPC/TF" del grid (redundantes); "Mov FS/IPC"
  renombradas a "Mov Corp TF/IPC" y son las que llegan al motor.
- `step='any'` en los inputs numéricos del grid por tramo (evita el marcado en rojo).
- Fix de `persistir_snapshot_proy`: serialización de `pd.Timestamp`/`datetime`/`date` a
  ISO y de escalares numpy a nativos (antes: `ProgrammingError` al bindear Timestamp).
- Reporte de estado/progreso del callback: `msg-escenario` salió del `running` para que
  el error/éxito final siempre se muestre; barra de progreso por tramo con `%`.
- Cargador de inputs por tramo desde Excel (ver D5).

**Acción:** actualizar design.md y tasks.md del spec para reflejar el código real, y/o
registrar estos cambios como una iteración documentada.

### D2. Progreso intra-tramo real (opción B no implementada)

**Prioridad:** media (UX).
La barra de progreso avanza a nivel de TRAMO (`tramo_completados / n_tramos`). Dentro de
un tramo, el motor RF corre varios minutos sin reportar avance, por lo que la barra
parece congelada durante ese tiempo. Para un `%` fino intra-tramo habría que instrumentar
`motor_proyeccion.proyectar_portafolio` (que por diseño NO se modifica) para que reporte
su loop mensual hacia afuera, o envolver su ejecución. Aplazado.

### D3. Layout de UI: dividir las secciones originales por tramo

**Prioridad:** media (UX).
Lo solicitado por el usuario era que, al elegir "por tramos", CADA sección original del
dashboard (Sección 1 curvas, Sección 2 macro, etc.) se dividiera en columnas por tramo
(p. ej. 3 shifts COP y 3 UVR en la Sección 1). Lo implementado es un GRID ÚNICO separado
al inicio con todas las variables por tramo. Funciona, pero no es la disposición pedida.
Rehacer como división por sección es un trabajo de UI mayor.

### D4. Encadenamiento no-RF por escalado del resultado (Opción c)

**Prioridad:** baja (documentar/validar).
RV y Alternativos NO aceptan un DataFrame de portafolio por parámetro (leen de
`portafolio_clasificado`), así que el encadenamiento se hace escalando el resultado del
motor por el factor `VM_cierre_previo / VM_inicial_original` por posición
(`_aplicar_encadenamiento_resultado`). Es correcto porque el retorno es multiplicativo,
pero es una vía indirecta; si algún día los motores aceptan un portafolio inyectado, la
Opción (a) (inyectar el VM en la columna `valor_mercado`) sería más limpia. Créditos y
los stores de estrés (`store-*-base`) quedaron sin recablear al flujo consolidado tras
eliminar los botones individuales.

### D5. Cargador de inputs por tramo desde Excel (implementado directo, formalizar en spec)

**Prioridad:** media (calidad).
Se implementó DIRECTO (sin fase de spec) para desbloquear la operación: botón
`upload-inputs-tramos` + callback `cargar_inputs_tramos_excel` +
`_parsear_excel_inputs_tramos` + `store-inputs-tramos`, que llena el grid por tramo desde
un Excel (fila por variable mapeada por nombre, columna por tramo). Limitaciones actuales:

- El número de tramos lo define la fecha inicio/fin del sidebar; el Excel solo llena; si
  las columnas no coinciden con los tramos, avisa pero no bloquea.
- Mapeo de nombres de fila por un diccionario fijo (`_MAPA_FILAS_INPUTS_TRAMO`); nombres
  fuera de ese set se ignoran con aviso.
- Convención de unidades: porcentajes como número (6.89 = 6.89%), bps y TRM tal cual.
- No hay tests automatizados de esta funcionalidad ni está en el spec.

**Acción:** formalizar en el spec `proyeccion-por-tramos-multiactivo` (requirements +
design breve + tasks) y añadir tests del parser (mapeo por nombre, coerción de celdas,
tolerancia a columnas faltantes/sobrantes) y un archivo de plantilla de ejemplo.

---

## RESUELTO — Fix 3: cuello de botella del motor RF (bisección TIR de bonos IPC)

**Estado:** CERRADO y verificado por equivalencia. (2026-10)

Cierra el **foco de optimización #1** de la "Fase 4" (bisección de TIR de 500 iteraciones
por título/mes en el bloque IPC) con una corrección de rendimiento que NO cambia la
valoración.

### Diagnóstico (profiling real, no a ciegas)

Con el arnés `tools/equivalencia_motor_rf.py profile` sobre el portafolio real:

- `valorar_bonos.py:97 _dias_nl365` se llamaba **36.121.955 veces ≈ 167.7 s (≈85% del
  tiempo total del motor)**.
- Origen: en `motor_proyeccion.py` (~líneas 1395-1415), la valoración a TIR de bonos IPC
  a Vencimiento/Disponible iteraba una bisección de 500 pasos y, dentro de cada paso,
  recalculaba `_dias_nl365(fecha, flujo)` por cada flujo. Los **días no dependen de la
  tasa `_ym`** que itera la bisección, así que ese recálculo era 100% redundante.

### Corrección aplicada (matemáticamente idéntica)

Se precalculan los pares `(monto, t_años)` UNA sola vez antes del bucle de bisección (y
análogamente para el VP de fin de mes). La bisección solo evalúa la potencia
`monto / (1+ym)**t`. Mismas fechas, mismos montos, misma fórmula → mismo resultado.

**NO se agrupan los títulos a TIR** (restricción inviolable: cada reinversión de
Vencimiento/Disponible conserva su tasa de compra y costo amortizado individual). La
optimización es intra-título, no toca el encadenamiento ni la amortización.

### Resultado verificado

- `_dias_nl365`: 36.121.955 → **303.735 llamadas (~119x menos), 0.978 s**.
- Motor RF (horizonte de 3 meses, caso del arnés): **7.6 s** de wall-clock.
- Equivalencia estricta: `python tools/equivalencia_motor_rf.py check` →
  **"OK EQUIVALENTE: el resultado es idéntico al baseline (tol=1e-6)"**.

### Herramienta de regresión dejada en el repo

- `tools/equivalencia_motor_rf.py` — comandos `baseline` / `check` / `profile`; horizonte
  configurable con env `AXIS_EQUIV_MESES` (default 3). Captura VM, VTIR, P&L (mes/acum,
  mercado/TIR), conteos, cupones y reinversiones por mes.
- `tools/baseline_motor_rf.json` — baseline de referencia (NO borrar). Volver a correr
  `check` tras cualquier cambio futuro al motor RF.

### Nota sobre el crecimiento inherente del tiempo

El tiempo por tramo sigue creciendo de forma acumulativa (efecto bola de nieve) porque
las reinversiones de cupones crean títulos sintéticos legítimos (p. ej. 2763 → 44986
títulos). Esta optimización **reduce drásticamente el costo por título** pero no elimina
el crecimiento del número de títulos, que es correcto por diseño. Los focos #2 (cachear
calendario de cupones) y #3 (paralelizar) de la Fase 4 siguen abiertos como mejoras
adicionales si se requieren horizontes aún más largos.

---

## RESUELTO — Fix 2: snapshot completo no-RF por tramo (RV + Alt + Derivados, sin duplicar)

**Estado:** CERRADO (núcleo). Tests automatizados formales (14.4) quedan como opcionales.
(2026-10)

El modo por tramos debía persistir en el snapshot de cierre TODOS los activos, no solo
Renta Fija, para que el Excel/exportable mostrara derivados, RV y alternativos. No lo
hacía por dos problemas encadenados:

### Problema 1 — las filas no-RF se descartaban al persistir (causa raíz)

`persistir_snapshot_proy` validaba `nominal` como obligatorio y descartaba toda fila con
`nominal` NaN. Las filas no-RF (RV / Alternativos / Derivados) no tienen "nominal" en el
sentido de un bono de RF y llegaban con NaN → se descartaban en silencio y el snapshot
quedaba RF-only.

**Corregido:** solo `valor_mercado` es obligatorio; `nominal` NaN/ausente se persiste
como NULL sin descartar la fila.

### Problema 2 — duplicación por posición (tarea 14.3)

`df_proy_rf` arranca de `df_base.copy()` sobre `portafolio_clasificado`, que CONSERVA
todas las posiciones del base, incluidas las no-RF:

- **Derivados** con `clase_origen` ∈ {FORWARD, OPCION, SWAP} (en el portafolio real: 83
  forwards, 170 opciones, 68 swaps).
- **RV y Alternativos** que viven con `clase_origen='TITULO'` pero `asset_class` de acción/
  fondo (COLCAP, MXWD, PEI, Estructuras, Fondos de Capital Privado, etc.). Confirmado:
  dentro de `clase_origen='TITULO'` hay 1191 "Renta Fija" + 447 "Renta Variable" + otros.

Esas filas quedan con el VM INICIAL del base (sin proyectar, porque el motor RF solo
actualiza el VM de los títulos de RF). Al anexar las filas valoradas por los motores
no-RF (`DERIVADO`/`RENTA_VARIABLE`/`ALTERNATIVO`), cada posición no-RF aparecía DOS veces.

**Corregido (regla de fuente única por posición)** en `_construir_snapshot_completo_tramo`
(`app_dash.py`): antes de anexar, se eliminan de `df_proy_rf`:
  (a) los derivados del base (por `clase_origen` FORWARD/OPCION/SWAP), y
  (b) las posiciones RV/Alt aportadas por los motores (por `id_posicion`).
Cada posición no-RF queda una sola vez, con el VM valorado por su motor. Diagnóstico
`[SNAPSHOT-DEDUP]` deja rastro de cuántas filas se removieron.

**Invariante preservado (Req 14.4):** las filas RF puras NO se tocan, y
`cargar_titulos_desde_snapshot` sigue filtrando no-RF (por `clase_origen` y por
`asset_class`), así que la deduplicación NO altera la reconstrucción de títulos del tramo
siguiente. Verificado con test aislado: snapshot sin duplicados + VM no-RF = valorado por
motor + reconstrucción RF del tramo siguiente intacta.

### Pendiente (menor)

- **Verificación en corrida real**: confirmar sobre una corrida por tramos (con ambos
  fixes aplicados) que la tabla `portafolio_clasificado_proy` queda con filas
  `RENTA_VARIABLE`/`ALTERNATIVO`/`DERIVADO` y sin duplicados. La lógica está verificada en
  aislamiento; falta la evidencia end-to-end.
- **Test automatizado 14.4** (opcional, marcado con `*` en el spec).


---

## RESUELTO — VM de cierre de títulos USD (Yankees) en COP (por tramos) — NO REPRODUCIBLE

**Estado:** CERRADO (no reproducible). Verificado contra el `portafolio.db` real y por lectura
de todo el proceso por tramos (motor + orquestación + encadenamiento + persistencia).
Spec: `.kiro/specs/proyeccion-tramos-rf-mensual-y-vm-internacional` (Defecto 2).
Detectado: 2026-10. Cerrado tras re-investigación.

### Resolución (resumen)

El síntoma reportado ("VM de cierre USD sin ×TRM") NO se reproduce en ninguna ruta viva. El
dato real del id 817856 "BN ECOPETROL USD" está en escala COP correcta en todos los tramos:

- Base `portafolio_clasificado`: 12.657.628.505,76 COP.
- Snapshot cierre 2026-12-31 (tramo 0): **14.389.842.600,95 COP** (escala COP correcta).
- Snapshot cierre 2027-12-31 (tramo i>0, multi-año): **15.094.118.182 COP** (escala COP correcta).

El "14.389.842" documentado abajo era ESE MISMO valor (14.389.842.600,95 COP) leído con la
escala equivocada (÷1000), no un VM sin ×TRM. Todas las ramas de asignación de `valor_mercado`
para USD en el bucle mensual (TIME DEP, VISTA USD, YANKEE vía `valorar_titulo`, FIC) aplican
`×_senda_trm[mes]`; el armado del snapshot y el encadenamiento entre tramos copian el VM en COP
sin reescalar. Se blinda con un test de integración multi-tramo (Tarea 5.1 del spec). El texto
original del reporte se conserva abajo como registro histórico.

---

### [HISTÓRICO] Reporte original (síntoma mal diagnosticado por escala ÷1000)

**Estado original:** ABIERTO. Severidad: ALTA (afecta el snapshot oficial `portafolio_clasificado_proy`
y cualquier P&G que compare VM inicial vs cierre de títulos en moneda extranjera).
Detectado: 2026-10, al construir el informe de P&G por tramo.

### Síntoma

En el flujo por tramos, el `valor_mercado` de CIERRE de los títulos de RF en USD (p. ej.
`BN ECOPETROL USD`, `BN YANKEES`) queda en una escala ~TRM veces MENOR que el VM inicial.
Ejemplo real (corte 2026-12-31, id 817856):
- Base (`portafolio_clasificado`): VM = 12.657.628.506 COP (correcto, = VM×TRM).
- Snapshot cierre (`portafolio_clasificado_proy`): VM = 14.389.842  → **sin ×TRM**.

El VM total USD cae de ~3.28 billones (base) a ~0.35 billones (cierre). No es liquidación:
es que el VM de cierre quedó en escala "precio×nominal/100" (USD) sin reconvertir a COP.

### Causa raíz (hipótesis, requiere confirmación en spec)

`valorar_titulo` (motor_proyeccion.py) convierte Yankees a COP con `× trm` en la rama de
valoración por precio (`return nominal * res["precio_sucio"] / 100.0 * trm`). En la rama de
re-valoración mensual de la proyección por tramos, el VM de cierre de estos títulos NO está
quedando multiplicado por la TRM del mes (o se guarda el valor en USD). Hay que localizar en
`proyectar_portafolio` el punto donde se asigna `titulo.valor_mercado` para Yankees y
asegurar la conversión a COP consistente con el VM inicial.

### Impacto

- El snapshot oficial por corte subvalúa la RF internacional (afecta totales de portafolio).
- Cualquier P&G por diferencia de VM (informe por tramo) da un P&G negativo disparatado para
  RF internacional.

### Mitigación temporal en el INFORME (no toca el motor)

`tools/detalle_pyg_tramo.py` normaliza, SOLO para el cálculo del P&G del informe, el VM de
cierre de títulos en moneda extranjera a COP usando la TRM de cierre del tramo, de modo que
VM inicial y final queden en la misma escala. Esto NO corrige el snapshot oficial (eso va en
el fix del motor, spec formal). Marca: ver `informe-portafolio-por-tramo-batch`.

### Acción (spec formal)

1. Reproducir con un Yankee aislado y confirmar el punto exacto del motor donde se pierde la
   conversión a COP en la re-valoración mensual por tramos.
2. Corregir en el motor (con test de equivalencia; es valoración → máxima precaución).
3. Verificar que el snapshot oficial de cierre recupera la escala COP correcta.


### ACTUALIZACION del diagnóstico (VM-USD): la causa NO es valorar_titulo

Prueba aislada (corte 2026-12-31, id 817856 `BN ECOPETROL USD`, YANKEE, Negociable):
- VM inicial del objeto: 12.657.628.506 COP.
- `valorar_titulo(..., trm=4100)` directo: **18.894.696.660 COP** → CORRECTO (aplica ×TRM).
- VM que quedó en el snapshot/objeto tras `proyectar_portafolio`: **14.389.842** → SIN ×TRM.

Conclusión: `valorar_titulo` (rama YANKEE: `nominal * precio/100 * trm`) es CORRECTO. El VM
sin TRM proviene de una **sobrescritura posterior** de `titulo.valor_mercado` dentro del
bucle mensual de `proyectar_portafolio` para títulos USD/Estructuras (una rama distinta a la
de `valorar_titulo`, p. ej. el bloque de re-valoración TIR o una rama especial de
Estructuras/subyacentes), que NO multiplica por la TRM. Hay que localizar esa asignación y
corregirla (con test de equivalencia; es el motor).

**Mitigación definitiva para el INFORME (sin tocar el motor):** en lugar de reescalar por un
factor heurístico, el informe debe **re-valorar** los títulos en moneda extranjera llamando
`valorar_titulo(titulo, tasa_mercado, fecha_corte, trm=trm_cierre)` — igual que el flujo
oficial — para obtener el VM de cierre correcto en COP. Pendiente de implementar en
`tools/detalle_pyg_tramo.py` (reemplazar el reescalado por ratio por una re-valoración
explícita de los USD/Yankee).


---

## RF Internacional (USD) — mala valoración por semántica de tasas + escala TRM

**Estado:** ABIERTO. Diagnóstico de causa raíz CERRADO (verificado con datos reales de
`portafolio.db` y pruebas aisladas de `valorar_titulo`). Pendiente de implementar el fix.
**Severidad:** ALTA. Afecta la valoración de RF internacional (Yankees/globales USD) tanto
en el flujo **por tramos** como en **multiescenarios**. También hay un riesgo sistémico que
toca RF local (ver sub-defecto 2C).
**Prioridad:** alta, pero es un fix DELICADO (es valoración financiera; máxima precaución y
tests de equivalencia). Aplazado intencionalmente a petición del usuario.
Detectado: 2026-10, al revisar el P&G por tramos del título 859370 (BN YANKEES).

### Resumen ejecutivo

El VM de los títulos de RF internacional en USD sale mal por DOS causas separables, más una
tercera de alcance sistémico descubierta durante el diagnóstico:

- **2A — Tasa de descuento incorrecta** (semántica de columnas de tasa).
- **2B — Escala de la TRM** (unidad miles vs nivel) → factor ×1000.
- **2C — Riesgo sistémico**: `tasa_mercado` (que hoy = columna `tir`) se usa como tasa de
  descuento en varios puntos creyendo que es "la tasa de mercado del título".

### Evidencia (datos reales, ISIN US195325DP79, fecha 2026-07-31)

Tres títulos del MISMO ISIN en `hist_titulos` (597 ya ingerido):

| id | clasif | nominal | valor_mercado (COP) | tasa_compra | tir | tasa_mercado (T.Mer) | tasa_dcto (T.Dcto) | precio |
|----|--------|---------|---------------------|-------------|-----|----------------------|--------------------|--------|
| 846015 | Negociable  | 250.000     | 781.204.000    | 2,46 |  0,60 |  3,61  | 5,53 | 99,3854 |
| 859370 | Vencimiento | 1.000.000   | 3.022.809.121  | 6,96 | -2,16 | -2,16  | 6,97 | NaN     |
| 875767 | Negociable  | 10.000.000  | 31.248.160.000 | 6,22 | -11,68| -12,25 | 5,53 | 99,3854 |

Observaciones clave:
- `tir` y `tasa_mercado` (T.Mer) vienen **negativas y DISTINTAS por título** del mismo ISIN
  (−2,16 / −12,25 / 3,61) → NO son una tasa de mercado del instrumento.
- `tasa_dcto` (**T.Dcto**) es **positiva y consistente** (~5,53 / 6,97) → es la tasa de
  descuento real a mercado.
- **Semántica definida por el usuario (dueño del proyecto):** `tasa_mercado` del objeto
  `Titulo` DEBE ser la **T.Dcto** del 597. La columna **T.Mer** del 597 no se sabe qué es y
  NO se usa. `tasa_compra` (T.Compra) y `tir` (TIR) están bien como están.

### Sub-defecto 2A — Tasa de descuento (semántica de columnas)

Flujo actual (incorrecto):
- `clasificar_portafolio.py` -> `extraer_portafolio_dia` (línea ~32) hace
  `SELECT ... tasa_compra, tir, tasa_mercado ... FROM hist_titulos`. **NO selecciona
  `tasa_dcto` ni `precio`** -> se pierden al construir `portafolio_clasificado`
  (confirmado: `portafolio_clasificado` NO tiene columnas `tasa_dcto` ni `precio`;
  `hist_titulos` SÍ las tiene).
- `scripts/motor_proyeccion.py` -> `cargar_portafolio_proyeccion` (línea ~294) construye el
  objeto `Titulo` con `tasa_mercado = float(row["tir"])` -> el atributo `Titulo.tasa_mercado`
  queda cargado con la **columna TIR** (negativa en globales), no con T.Dcto.
- En la revaloración mensual (línea ~1330), cuando el título NO tiene `spread_mercado`
  (rendimiento PIP) ni curva en `sendas`, el fallback de la tasa de descuento del VM es
  `tasa_mkt = titulo.tasa_mercado` -> usa la TIR negativa -> precio sucio inflado.

Verificación cuantitativa (859370, TRM=3900, valorado a la fecha base):
- Con `tir = -2,16` (actual): VM = 4.672.727.007 (ratio 1,55 sobre el VM base 3.022 MM).
- Con `T.Dcto = 6,97` (correcta): VM = 3.748.481.295 (ratio 1,24).
- Con `spread PIP = 5,525`: VM = 3.876.030.366 (ratio 1,28).
- NOTA: ninguna reproduce EXACTAMENTE el VM base (precio implícito del base ~77,5 vs ~96–120
  de las tasas). Queda PENDIENTE confirmar cómo se calculó el `valor_mercado` base del 597
  (precio reportado directo vs revaloración) para replicarlo a T0. El 859370 trae
  `precio = NaN` en el 597, lo que complica el anclaje por precio directo para ese título.

**Fix de raíz 2A (propuesto, NO implementado):**
1. En `clasificar_portafolio.py` (`extraer_portafolio_dia`, SELECT ~línea 32): agregar
   `tasa_dcto` (y `precio`) al SELECT desde `hist_titulos`, y persistirlas en
   `portafolio_clasificado`.
2. En `motor_proyeccion.py` (`cargar_portafolio_proyeccion`, ~línea 294): cargar
   `Titulo.tasa_mercado` desde `tasa_dcto` (T.Dcto), NO desde `tir`.
3. NO sobrescribir ni confundir: `tasa_compra` y `tir` se conservan con su significado.
4. Guarda de sanidad: una tasa de descuento de mercado para USD no debería ser negativa; si
   lo es, es señal de dato corrupto (log + fallback explícito, nunca TIR).

### Sub-defecto 2B — Escala de la TRM (×1000)

- La valoración Yankee es CORRECTA: `motor_proyeccion.valorar_titulo` rama YANKEE
  `return nominal * res["precio_sucio"]/100.0 * trm` (línea ~481) y
  `valorar_bonos.valorar_bono_yankee` multiplica precio_sucio (base 100) por `trm`.
- El ÷1000 es un problema de UNIDAD de la **TRM de cierre**: con `trm=3900` el VM sale en
  miles de millones (correcto); con `trm=3.9` sale ÷1000 (verificado: 4.543.211.556 vs
  4.543.211,56). Solo afecta títulos USD (los COP no multiplican por TRM).
- Causa compartida por por_tramos y multiescenarios: la TRM de cierre se toma de un input de
  usuario/grid y se pasa TAL CUAL como nivel, sin normalizar contra la TRM implícita (nivel
  ~3900 vía `gestor_informes.obtener_trm_implicita`, guarda 2000–8000).
  - por_tramos: `app_dash.py` grid `inputs_por_tramo['trm_cierre'] = _g(tramo_trm, i)`
    (~línea 6065) -> `InputsPorTramo.trm_cierre` -> `_trm_tramo_fin` -> `proyectar_portafolio`.
  - multiescenarios: `tools/multiescenarios.py` `trm_cierre=float(esc["trm_final"])` (~533).

**Fix de raíz 2B (propuesto, NO implementado):** normalizar/validar la unidad de la TRM en
el punto de ingreso del input de usuario (grid por tramos y `trm_final` de multiescenarios),
o guarda en `InputsPorTramo.from_valores` que rechace/escale TRM fuera de la banda plausible
(2000–8000). NO tocar `valorar_titulo` ni `valorar_bono_yankee` (son correctos).

### Sub-defecto 2C — Riesgo sistémico de `tasa_mercado` (afecta también RF local)

`tasa_mercado` (hoy = columna `tir`, de significado dudoso) se usa como tasa de valoración
en varios puntos ASUMIENDO que es la tasa de mercado del título:
- `app_dash.py:1607-1608` — valoración AXIS de títulos **1CP/1DV** (comentario literal:
  "usar tasa_mercado del 597 directamente (es la tasa PIP del día)" -> creencia equivocada).
- `app_dash.py:4980 / 4987` — base de los **shocks de corporativos** CORP_TF / CORP_IPC.
- `app_dash.py:6517` y `motor_proyeccion.py:~1330` — fallback de revaloración.
- `scripts/proyeccion_tramos.py:987` — el snapshot por tramos recarga `tir -> tasa_mercado`
  (propaga el cruce entre tramos).

Mitigante: el camino PRINCIPAL de valoración de RF (TF/UVR/IPC/Yankee CON spread PIP) usa
`spread_mercado` (rendimiento PIP), NO `tasa_mercado`. Y `multiescenarios` usa
`spread_mercado`/`tasa_compra`. Por eso RF local "corre bien" mientras tenga PIP para la
fecha de corte; el síntoma aflora en globales USD porque su `tir` es negativa. Al corregir
2A (que `tasa_mercado` = T.Dcto), estos usos quedan correctos de forma natural.

### Doble valoración del motor (contexto confirmado, NO es defecto)

Cada título se valora cada mes a mercado Y a TIR en paralelo (`proyectar_portafolio`):
- **VM a mercado:** `valorar_titulo(..., tasa_mercado_nueva = tasa_mkt)` donde `tasa_mkt` =
  `spread_mercado (PIP) + shift`, o el fallback `tasa_mercado` si no hay PIP (línea ~1333).
- **VTIR (costo amortizado):** `valorar_titulo(..., tasa_mercado_nueva = titulo.tasa_compra)`
  (línea ~1461). SIEMPRE descuenta con `tasa_compra`.
- Para **IPC en Vencimiento/DV**, el VTIR NO usa tasa simple: resuelve la TIR interna por
  bisección a partir de `valor_tir` del 597 (líneas ~1347-1456). PENDIENTE confirmar si la
  base `valor_tir` del 597 es creíble para IPC-VC o si hay que recalcular el spread de compra
  real (el usuario sospecha que la base del IPC a TIR puede no ser creíble). Requiere una
  prueba específica sobre un IPC-VC real antes de concluir.

### Archivos que tocará el fix

- `clasificar_portafolio.py` (ingesta: traer `tasa_dcto`/`precio` a `portafolio_clasificado`).
- `scripts/motor_proyeccion.py` (`cargar_portafolio_proyeccion`: `tasa_mercado` <- T.Dcto;
  revisar los 3 fallbacks que leen `titulo.tasa_mercado`: líneas ~1045, ~1179, ~1330).
- `scripts/proyeccion_tramos.py` (snapshot: coherencia del campo recargado, ~línea 987).
- `app_dash.py` (grid TRM 2B ~6065; usos de `tasa_mercado` 1CP/1DV ~1607 y shocks corp
  ~4980/4987 — 2C).
- `tools/multiescenarios.py` (TRM `trm_final` 2B ~533).
- Datos: `hist_pip_int` estaba VACÍA; se pobló para 2026-07-31 desde
  `Preciospip_INT_SBOL_T+0_20260731.csv`. El ISIN US195325DP79 NO está en ese CSV
  internacional (deuda del Gobierno de Colombia en USD; su spread venía de `hist_pip`).
  Asegurar poblamiento de PIP internacional por fecha de corte.

### Cómo retomar / verificar

Scripts de diagnóstico (conservados en la raíz, candidatos a limpieza al final de la spec):
`_tmp_repro_controlada.py` (aisla 2A/2B), `_tmp_diag_tasas_isin.py` (compara columnas de
tasa del ISIN), `_tmp_verif_tasadcto.py` (VM con T.Dcto vs TIR vs PIP),
`_tmp_poblar_pipint.py` (puebla hist_pip_int), `tests/_diag_d2_vm_usd_e2e.py` (e2e por
tramos). Reproducir el VM del 859370 con T.Dcto a TRM nivel y comparar contra el VM base
antes y después del fix (test de equivalencia obligatorio por ser valoración).

### Sub-defecto 2B — RESUELTO (escala/unidad de la TRM de cierre). Falla conocida diferida. (2026-10)

**Estado:** sub-defecto 2B CERRADO por el bugfix `trm-escala-rf-internacional-por-tramos`
(spec en `.kiro/specs/trm-escala-rf-internacional-por-tramos`). Los sub-defectos **2A**
(semántica `tasa_mercado` = T.Dcto vs TIR) y **2C** (usos sistémicos de `tasa_mercado`)
siguen ABIERTOS; este bugfix NO los toca.

El fix normaliza la **unidad** de la TRM de cierre en el punto de ingreso de inputs no
confiables, sin tocar la valoración: helper puro `normalizar_trm_nivel` en
`scripts/proyeccion_tramos_multiactivo.py` (banda plausible `[500, 20000]`; identidad en
banda; normaliza por factores de 1000; `ValueError` si el valor está fuera de banda y no es
un error de ×1000), cableado en los dos puntos de ingreso (`InputsPorTramo.from_valores` y
el `trm_final` de `tools/multiescenarios.py`). La valoración (`valorar_titulo`,
`valorar_bono_yankee`) y la TRM t0 (implícita del 597) quedaron sin cambios.

Su suite propia pasa **11/11** (exploración de la condición del bug + preservación +
equivalencia de escala del VM USD) en `tests/test_trm_escala_rf_intl.py`.

**Falla conocida DIFERIDA (conflicto de especificaciones, NO es defecto de implementación):**

La guarda de unidad introduce semántica más estricta para `trm_cierre` que entra en
conflicto con una propiedad más antigua de otra spec. Dos pruebas de la suite previa
`tests/test_tramos_multiactivo_primitivas.py` fallan de forma ESPERADA tras el fix:

1. `test_inputs_por_tramo_valida_sin_abortar` — la Property 12 antigua afirma que
   `from_valores` nunca lanza excepción; el fix ahora lanza `ValueError` para un
   `trm_cierre` finito, fuera de banda y NO normalizable por 1000 (p. ej. `0.0`), conforme
   al requisito 2.4 del bugfix (no valorar en silencio con una TRM dudosa).
2. `test_inputs_por_tramo_conserva_validos` — la Property 12 antigua afirma que todo `float`
   finito se preserva verbatim; el fix ahora normaliza un error de unidad ×1000 (p. ej.
   `1.0 → 1000.0`) conforme a los requisitos 2.1/2.2, y lanza para `0.0`.

**Causa:** supersesión deliberada a nivel de spec. La Property 12 de la spec más antigua
`proyeccion-por-tramos-multiactivo` codificaba totalidad (`from_valores` no aborta) +
preservación verbatim de `trm_cierre`. El bugfix `trm-escala-rf-internacional-por-tramos`
otorga a `trm_cierre` semántica más estricta de unidad/banda a propósito. Ambos
comportamientos no pueden coexistir para `trm_cierre`; el bugfix es la fuente de verdad
vigente para ese campo.

**Decisión (usuario):** DEJAR AMBOS COMO ESTÁN y DOCUMENTAR EL CONFLICTO. No se modifica el
fix (`normalizar_trm_nivel` / wiring de `from_valores`) ni las pruebas antiguas de
`tests/test_tramos_multiactivo_primitivas.py`. Se registra como falla conocida y se aplaza
la reconciliación.

**Opciones de reconciliación (DIFERIDAS a una tarea futura):**

- **Preferida:** acotar el generador de `trm_cierre` de la Property 12 a la banda plausible
  (`[500, 20000]`), o excluir `trm_cierre` de los chequeos genéricos de
  totalidad + preservación verbatim de esa spec, dejando su semántica de unidad/banda
  gobernada exclusivamente por el bugfix 2B.
- **Alternativa:** actualizar la Property 12 de `proyeccion-por-tramos-multiactivo` para que
  refleje explícitamente la nueva semántica de `trm_cierre` (normalización por 1000 +
  rechazo fuera de banda), referenciando este bugfix como fuente de verdad.

Hasta entonces, los 2 fallos de `tests/test_tramos_multiactivo_primitivas.py` son una
**falla conocida esperada**, no una regresión.


---

## RF por tramos — salto de VM no reflejado en el P&G en la 3a frontera (dic-2027 -> ene-2028)

**Estado:** ABIERTO. Hallazgo verificado con datos reales (Excel
`resultados_escenario_AXIS (3).xlsx`, hoja `RF_Mensual_Tramos`). Pendiente de investigar
causa raíz.
**Severidad:** ALTA (afecta el P&G de RF en la frontera entre el tramo 2 y el tramo 3).
Detectado: 2026-10.

### Síntoma (datos reales)

Serie `RF_Mensual_Tramos` (vm_total y pyg_mes), corrida inicio 2026-07, fin 2028:

- Frontera dic-2026 -> ene-2027 (tramo 1 -> tramo 2): CORRECTA.
  VM dic-2026 = 9,13 billones; VM ene-2027 = 9,17 billones; pyg ene-2027 = +9,47e10.
  Cumple pyg(ene) = VM(cierre ene) - VM(cierre dic) + cupones.
- Frontera dic-2027 -> ene-2028 (tramo 2 -> tramo 3): INCORRECTA.
  VM dic-2027 = 11,21 billones; VM ene-2028 = 10,17 billones (CAE ~1,04 billones);
  pyg ene-2028 = +5,94e10 (POSITIVO). El salto negativo de VM (~ -1,04 billones) NO se
  refleja en el pyg del mes. Según la definición de negocio
  (pyg(mes) = VM(cierre mes) - VM(cierre mes anterior) +/- cupones), el pyg de ene-2028
  debería ser ~ -1,04 billones + cupones, no +0,059 billones.

RF NO tiene filas con pyg=0 (a diferencia de RV/Alt), así que NO es el mismo defecto del
corrimiento de fila fantasma. Es un salto de nivel de VM entre el cierre del tramo 2 y el
arranque del tramo 3 que el P&G no captura.

### Hipótesis a investigar (NO confirmadas)

1. Títulos que VENCEN al cierre del tramo 2 y salen del portafolio del tramo 3 (el VM baja
   por vencimientos, pero el P&G no contabiliza la salida como resultado — debería ser
   flujo/caja, no pérdida de VM).
2. Reinicio/recarga parcial del portafolio RF al construir el tramo 3 (el snapshot
   `portafolio_clasificado_proy` del cierre del tramo 2 no se arrastra completo como T0 del
   tramo 3).
3. Diferencia de conteo de títulos (`n_titulos` cae) entre tramos — revisar
   `n_titulos`/`n_titulos_local`/`n_titulos_intl` en la hoja alrededor de esa frontera.
4. Posible relación con el defecto de RF internacional (USD) ya documentado: si parte del
   VM del tramo 2 estaba inflado por la TIR negativa y en el tramo 3 cambia, podría producir
   un salto. Verificar si el salto se concentra en `vm_total_intl`.

### Cómo investigar / verificar

- Comparar `n_titulos`, `vm_total_local` y `vm_total_intl` en dic-2027 vs ene-2028 (la hoja
  ya trae esas columnas) para ver si el salto es local, internacional, o por caída de conteo.
- Revisar el snapshot `portafolio_clasificado_proy` del corte 2027-12-31 vs el portafolio de
  arranque del tramo 3 en `_ejecutar_escenario_por_tramos` (app_dash.py): confirmar que el
  T0 del tramo 3 = cierre del tramo 2 (encadenamiento del portafolio RF, no re-lectura de
  `portafolio_clasificado` base).
- Script de apoyo: `_tmp_diag_corrimiento.py` (ya imprime la serie RF por mes).

### Relación con otros defectos

- Distinto del corrimiento de fila fantasma de RV/Alt (ese sí tiene pyg=0 en el primer mes
  de cada tramo; RF no).
- Posible interacción con "RF Internacional (USD) — mala valoración" (ver entrada anterior):
  descartar o confirmar si el salto proviene del componente USD.

---

## P&G de títulos que VENCEN dentro del horizonte — no se contabiliza en detalle ni resumen

**Estado:** ABIERTO. Diagnóstico de causa raíz CERRADO (verificado por lectura de código y
flujo de datos). Pendiente de implementar el fix.
**Severidad:** ALTA. El P&G de RF queda subestimado en todas las superficies que se derivan
del detalle por título. Afecta AMBOS modos (punta a punta y por tramos).
**Prioridad:** alta. Es cálculo de resultado financiero; el fix toca el motor (valoración),
así que exige test de equivalencia y máxima precaución.
Detectado: 2026-10, al revisar por qué el título 888672 (y en general los que vencen a
mitad de horizonte) no aparecen con su P&G en el detalle.

### Síntoma

Un título de RF que está VIVO al inicio del tramo y VENCE dentro de él (p. ej. vive el
31-dic-2026 y vence en abril-2027) NO reporta su P&G acumulado del período en que estuvo
vivo (cupones devengados + variación de valor hasta vencer). Ese P&G simplemente desaparece
del detalle por título, del ResumenPyG/TotalCategoria y de las tarjetas del dashboard.

### Causa raíz (verificada)

Doble origen:

1. **El detalle por título excluye vencidos.**
   - `tools/detalle_pyg_tramo.py::construir_rf_detalle`: `for t in port: if t.fecha_vencimiento <= fecha_fin: continue`
     → salta todo título que vence dentro del horizonte.
   - `app_dash.py` modo punta a punta (`_ejecutar_escenario`, bloque `rf_detalle`, ~L6605):
     mismo filtro `if t.fecha_vencimiento <= fecha_end: continue`.
   - `scripts/rf_contable_mensual.py::construir_rf_mensual_contable`: filtra
     `titulos_vivos_fin = {t.id_posicion for t in port_list if t.fecha_vencimiento > fecha_fin}`
     y excluye al vencido en TODOS los meses, incluso los que estuvo vivo.

2. **El motor no graba el snapshot por título del mes de vencimiento.**
   - `scripts/motor_proyeccion.py` paso 4 (~L1479): `for titulo in portafolio_activo: if titulo.fecha_vencimiento <= fecha_mes: continue`
     → el último snapshot mensual del título es el del mes PREVIO al vencimiento; la serie
     mensual contable no tendría el tramo final del título aunque pasara el filtro.

La ÚNICA serie que SÍ conserva el P&G del vencido es el agregado del motor
`ResultadoMes.pnl_mercado_acum` (delta de totales: cuando el título vence, su valor se
reinvierte en `monto_vencimiento` y el salto queda en el delta). Pero esa serie casi no se
consume en las salidas (ver tabla).

### Alcance por modo y superficie (verificado con lectura del callback `renderizar_resultados`
y del callback de Excel `descargar_resultados_excel`)

| Superficie | Punta a punta | Por tramos |
|---|---|---|
| Tarjetas/KPIs (Resumen Ejecutivo) | EXCLUYE (`sum(rf_detalle.pyg)`) | EXCLUYE (muestra 0 para RF) |
| Excel `RF_Detalle_Titulo` | EXCLUYE | EXCLUYE |
| Excel `RF_Mensual` | EXCLUYE (de `rf_mensual_contable`) | EXCLUYE |
| Excel `ResumenPyG`/`TotalCategoria` | (no aplica) | EXCLUYE |
| Excel `Resumen_Compania`/`AssetClass`/`Ramo`/`Por_Clasificacion` | EXCLUYE | — |
| Gráfico UI "Evolución Mensual P&L" | INCLUYE (`rf_resumen → pnl_mercado_acum`) | no se dibuja |
| Tabla UI "Detalle RF Mensual" | INCLUYE (`pnl_mercado_acum`) | no se dibuja |

Implicación en punta a punta: el gráfico/tabla mensual de la UI (con vencidos) y las
tarjetas + todo el Excel (sin vencidos) NO cuadran entre sí cuando hay vencimientos.

### Nota sobre consolidación (contexto, NO es parte del defecto)

Durante el diagnóstico se confirmó que los Negociables fungibles se CONSOLIDAN en una fila
`CONS_<hash>` (feature `optimizacion-agrupacion-negociables`), por lo que un id individual
como 888672 pierde su identidad al fundirse en el consolidado. Eso es correcto por diseño y
NO pierde P&G (verificado: Σ base = Σ CONS + sueltos VC). Es independiente de este defecto.

### Relación con otro defecto abierto

Muy probablemente la MISMA causa raíz que "RF por tramos — salto de VM no reflejado en el
P&G en la 3a frontera (dic-2027 -> ene-2028)" (entrada anterior): ahí el VM cae ~1,04
billones por vencimientos y el P&G no lo refleja. Tratar ambos juntos.

### Opciones de arreglo (DIFERIDAS; decisión pendiente del usuario)

- **Opción A (preferida, más transparente):** emitir en el detalle una fila "realizada" por
  cada título vencido con su P&G acumulado hasta la fecha de vencimiento (cupones +
  variación hasta vencer), `vm_mercado = 0` al cierre. El detalle vuelve a sumar completo y
  reconcilia con el agregado del motor.
- **Opción B:** trasladar/capitalizar el P&G del padre en el título hijo de la reinversión
  (una sola "línea económica"); distorsiona el P&G futuro del hijo y mezcla resultados.

El fix debe además unificar la fuente de tarjetas y Excel con el agregado del motor
(`pnl_mercado_acum`) para eliminar la inconsistencia UI vs tarjetas en punta a punta, y
hacer que por tramos el ResumenPyG incluya el P&G de los vencidos.

### Archivos que tocará el fix

- `scripts/motor_proyeccion.py` (grabar snapshot del mes de vencimiento / emitir el P&G
  realizado del título vencido; test de equivalencia obligatorio).
- `tools/detalle_pyg_tramo.py` (`construir_rf_detalle`: incluir la fila realizada del
  vencido).
- `scripts/rf_contable_mensual.py` (`construir_rf_mensual_contable`: incluir los meses
  vivos del vencido).
- `app_dash.py` (bloque `rf_detalle` de punta a punta ~L6605; unificar la fuente del P&G de
  RF en las tarjetas y el Excel para que coincida con el agregado del motor).
- `tools/export_resumen_tramos.py` (`_resumen_rf`: hereda del `rf_detalle` corregido, sin
  cambios adicionales si el detalle ya trae la fila realizada).

### Cómo reproducir / verificar

Scripts de diagnóstico conservados en la raíz (candidatos a limpieza al final):
`_tmp_investigar_888672.py` / `_tmp_investigar_888672b.py` / `_tmp_investigar_888672c.py`
(rastreo del título y de la consolidación). Reproducir con un título RF que viva al inicio
del tramo y venza dentro de él; comparar `pnl_mercado_acum` del motor (con vencidos) contra
Σ del `rf_detalle` (sin vencidos) y cuantificar la diferencia.
