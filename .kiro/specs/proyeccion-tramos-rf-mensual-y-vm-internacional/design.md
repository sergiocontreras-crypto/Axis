# Proyección por Tramos — RF Mensual y VM Internacional — Diseño de Bugfix

## Overview

Esta spec corrige DOS defectos del modo de proyección "por_tramos" del dashboard AXIS. El
orquestador `app_dash.py::_ejecutar_escenario_por_tramos` trocea el horizonte por fin de año
y corre el motor de Renta Fija (`scripts/motor_proyeccion.py::proyectar_portafolio`) una vez
por tramo, encadenando el snapshot de cierre de un tramo como base del siguiente. La
exportación a Excel por tramos vive en `tools/exportar_excel_tramos.py`.

- **Defecto 1 (aditivo, bajo riesgo):** el modo por tramos NO exporta la hoja mensual de
  Renta Fija (RF_Mensual) y, además, pierde el mes de ENERO de arranque de cada tramo porque
  el motor fecha cada mes con un desplazamiento de +1 mes (`fecha_mes = fecha_inicio +
  relativedelta(months=mes)` con `mes` arrancando en 1 y el tramo arrancando el 1-ene). El
  orquestador por tramos además descarta la lista mensual de `ResultadoMes` que retorna
  `proyectar_portafolio`. La estrategia de fix es: (a) extraer la lógica contable mensual
  NEG/DV/VC —hoy embebida en `_ejecutar_escenario` y DUPLICADA en `tools/detalle_pyg_tramo.py`—
  a un helper compartido y testeable; (b) capturar y acumular la serie mensual de RF por
  tramo; (c) persistirla en el payload por corte (`informe_pyg_por_tramo`) para que
  `exportar_excel_tramos.py` reconstruya una hoja `RF_Mensual_Tramos` continua; (d) corregir
  el fechado del mes de arranque bajo la regla de negocio "el cierre de un año es el T0 del
  año siguiente".

- **Defecto 2 (toca valoración/encadenamiento, requiere tests de equivalencia):** el VM de
  cierre de los títulos de RF internacional en USD (p.ej. YANKEE) queda en el snapshot oficial
  `portafolio_clasificado_proy` en una escala ~TRM veces menor que el VM inicial, produciendo
  P&G disparatados. La valoración unitaria (`valorar_titulo`, rama YANKEE) ya es correcta
  (devuelve COP = `nominal × precio/100 × trm`). La causa raíz exacta de la pérdida de escala
  debe CONFIRMARSE durante la implementación (ver Hypothesized Root Cause); existe una
  mitigación TEMPORAL en `tools/detalle_pyg_tramo.py` que re-valora los USD solo para el
  informe, sin tocar el motor ni el snapshot oficial.

El orden sugerido de implementación es Defecto 1 primero (aditivo) y Defecto 2 después (toca
valoración). El diseño prioriza NO cambiar el comportamiento del modo "punta_a_punta" ni la
valoración de títulos en COP.

## Glossary

- **Bug_Condition (C)**: condición que dispara el bug. Para el Defecto 1, que la corrida sea
  en modo `por_tramos` (la serie mensual de RF no se exporta o pierde el enero de arranque de
  un tramo). Para el Defecto 2, que un título de RF internacional en USD se valore al cierre
  de un tramo en escala no-COP.
- **Property (P)**: comportamiento deseado. D1: serie RF mensual continua y completa, con el
  enero de cada año presente y la hoja RF_Mensual exportada. D2: VM de cierre USD registrado
  en COP (×TRM), consistente con el VM inicial.
- **Preservation**: comportamiento existente que NO debe cambiar — modo `punta_a_punta`
  (RF_Mensual y VM de cierre), hojas mensuales RV/Alt/Derivados por tramos, y la valoración
  unitaria de títulos en COP y la de títulos USD aislados.
- **F / F'**: código actual (sin corregir) / código corregido.
- **`proyectar_portafolio`**: función del motor en `scripts/motor_proyeccion.py` que proyecta
  RF mes a mes y retorna una lista de `ResultadoMes`; además muta `portafolio` (extiende con
  reinversiones y actualiza `valor_mercado`/`valor_tir` por título).
- **`ResultadoMes`**: dataclass con `mes`, `fecha`, `vm_total`, `vtir_total`, P&L del mes, etc.
- **`_ejecutar_escenario`**: ruta "punta_a_punta"; contiene el bloque que compone
  `rf_mensual_contable` a partir de `_snapshots_mensuales` por título (lógica NEG/DV/VC).
- **`_ejecutar_escenario_por_tramos`**: orquestador por tramos; corre el motor por tramo pero
  DESCARTA la lista `ResultadoMes` y NO produce `rf_mensual_contable`.
- **`_titulos_a_dataframe_base`**: construye el DataFrame base de cada tramo; en tramos i>0
  reconstruye `valor_mercado` desde `t.valor_mercado` (encadenamiento — punto a vigilar en D2).
- **NEG / DV / VC**: clasificación contable (Negociable / Disponible para Venta / al
  Vencimiento) que determina la descomposición P&G/ORI mensual.
- **TRM**: tasa representativa del mercado (USD→COP). La escala COP de un título USD es
  `nominal × precio/100 × trm`.

## Bug Details

### Bug Condition

**Defecto 1 — fechado del mes + captura/export de la serie mensual de RF.** El bug se
manifiesta en toda corrida en modo `por_tramos`: la hoja mensual de RF no se exporta y, cuando
un tramo arranca el 1-ene, el motor nunca emite una fila para ese enero (desplaza todas las
fechas +1 mes), de modo que un tramo que el dashboard reporta como 12 meses sale con 11 meses
de RF en el Excel.

**Formal Specification (Defecto 1):**
```
FUNCTION isBugCondition_D1(X)
  INPUT: X de tipo CorridaPorTramos
  OUTPUT: boolean

  RETURN X.modo = "por_tramos"
         AND (
              NOT existe_hoja_RF_Mensual(X.excel)
              OR EXISTS tramo IN X.tramos TAL QUE
                   enero_de_arranque(tramo) NOT IN serie_mensual_RF(tramo)
             )
END FUNCTION
```

**Defecto 2 — escala COP del VM de cierre de RF internacional (USD).** El bug se manifiesta
cuando un título de RF internacional en USD (YANKEE o estructura USD) se registra en el
snapshot de cierre de un tramo en una escala ~TRM veces menor que su VM inicial.

**Formal Specification (Defecto 2):**
```
FUNCTION isBugCondition_D2(X)
  INPUT: X de tipo TituloEnCierrePorTramos
  OUTPUT: boolean

  RETURN X.moneda = "USD"
         AND X.tipo IN {YANKEE, EstructuraUSD}
         AND X.es_cierre_de_tramo
         AND vm_cierre_en_snapshot(X) NO está en escala COP   // falta ×TRM
END FUNCTION
```

### Examples

- **D1 — enero perdido:** corrida `por_tramos` con fin 31-dic-2027. El tramo que arranca el
  1-ene-2027 debería emitir ene-2027..dic-2027 (12 filas). Hoy emite feb-2027..ene-2028
  (desplazado), y en el Excel por tramos ese año sale con 11 meses de RF. Esperado: 12 filas
  ene..dic.
- **D1 — hoja ausente:** en el Excel por tramos existen `RV_Mensual_Tramos`,
  `Alt_Mensual_Tramos` y `Derivados_PyG_Tramos`, pero NO existe `RF_Mensual_Tramos`. Esperado:
  la hoja mensual de RF debe existir, análoga a las demás.
- **D1 — frontera de año:** el P&G de enero de cada año debe calcularse como
  `VM(fin de enero) − VM(cierre 31-dic del año anterior)` ("el cierre de un año es el T0 del
  siguiente"), sin huecos ni duplicados en la frontera.
- **D2 — caso documentado:** corte 2026-12-31, id 817856 "BN ECOPETROL USD" (YANKEE,
  Negociable). Hoy el snapshot reporta VM de cierre = 14.389.842 (sin ×TRM) frente a un VM base
  correcto = 12.657.628.506 COP (= VM×TRM). Esperado: VM de cierre en escala COP consistente
  con el VM base.
- **D2 — agregado:** al sumar los títulos de RF en USD, el VM total USD cae de ~3,28 billones
  a ~0,35 billones, produciendo un P&G por diferencia de VM negativo y disparatado. Esperado:
  VM total consistente, P&G coherente.

## Expected Behavior

### Preservation Requirements

**Comportamientos que NO deben cambiar:**
- El modo `punta_a_punta` SHALL seguir generando la clave `rf_mensual_contable` en
  `_ejecutar_escenario` y exportando la hoja `RF_Mensual` en `descargar_resultados_excel` sin
  cambio de comportamiento (mismos valores por mes).
- Las series mensuales de RV, Alternativos y Derivados por tramos SHALL seguir generándose
  (`RV_Mensual_Tramos`, `Alt_Mensual_Tramos`, `Derivados_PyG_Tramos`) sin alteración.
- `valorar_titulo` para títulos en COP (no USD) SHALL producir exactamente el mismo valor de
  mercado que antes del fix (verificado por test de equivalencia).
- `valorar_titulo` para títulos YANKEE/USD valorados de forma aislada SHALL seguir produciendo
  el valor correcto en COP (`nominal × precio/100 × trm`); la valoración unitaria ya es
  correcta y no debe cambiar.
- El modo `punta_a_punta` sobre un portafolio con RF internacional SHALL producir el mismo VM
  de cierre que antes del fix (el Defecto 2 es específico del encadenamiento por tramos).
- La extracción de la lógica contable NEG/DV/VC a un helper compartido SHALL producir los
  mismos valores contables mensuales que la lógica embebida original para los casos ya
  soportados (equivalencia byte a byte en los agregados).

**Scope:**
Todas las entradas que NO cumplen `isBugCondition_D1` ni `isBugCondition_D2` deben quedar
completamente inalteradas por este fix. En particular:
- Corridas en modo `punta_a_punta` (cualquier portafolio).
- Títulos en COP/UVR en cualquier modo.
- Series mensuales de RV/Alt/Derivados por tramos.
- Valoración unitaria de cualquier título (COP o USD) con `valorar_titulo`.

**Nota:** el comportamiento correcto esperado para los inputs buggy se define en la sección
Correctness Properties (Property 1 para D1, Property 2 para D2).

## Hypothesized Root Cause

### Defecto 1 (confirmado por lectura de código)

1. **Descarte de la serie mensual en el orquestador.** `_ejecutar_escenario_por_tramos` llama
   a `proyectar_portafolio(...)` sin asignar su retorno (la lista de `ResultadoMes` se pierde),
   y el dict final del modo por tramos NO incluye `rf_mensual_contable`. La ruta
   `punta_a_punta` sí captura `res_rf = proyectar_portafolio(...)` y compone
   `rf_mensual_contable` desde los `_snapshots_mensuales` por título.

2. **Desplazamiento +1 mes en el fechado.** En `motor_proyeccion.py` (bucle principal):
   `for mes in range(1, meses_horizonte + 1): fecha_mes = fecha_inicio + relativedelta(months=mes)`.
   Con `fecha_inicio = 1-ene`, el mes 1 se fecha como 1-feb, por lo que el enero de arranque
   nunca aparece y toda la serie se corre un mes. En multi-año esto repite la pérdida de enero
   en cada frontera.

3. **Duplicación de la lógica contable NEG/DV/VC.** La descomposición mensual vive embebida en
   `_ejecutar_escenario` y está DUPLICADA en `tools/detalle_pyg_tramo.py::construir_rf_detalle`.
   Esto bloquea la reutilización limpia por el orquestador por tramos (deuda técnica declarada
   en el propio docstring de `detalle_pyg_tramo.py`).

### Defecto 2 (INVESTIGADO Y CERRADO — no reproducible)

La valoración unitaria es correcta: tanto la rama YANKEE de `valorar_titulo`
(`return nominal * res["precio_sucio"]/100.0 * trm`) como el bucle mensual del motor
(`vm_nuevo = valorar_titulo(..., trm=_senda_trm[mes])`) dejan `titulo.valor_mercado` en COP.
Esto CONTRADICE la hipótesis del docstring de la mitigación. La re-investigación de TODO el
proceso por tramos (no solo el motor) y la inspección del `portafolio.db` real confirman que el
defecto NO se reproduce en ninguna ruta viva. Veredicto por hipótesis:

1. **Rama alternativa del bucle mensual — DESCARTADA.** Todas las ramas USD del bucle mensual
   aplican `×_senda_trm[mes]`: TIME DEP (`nominal × _senda_trm[mes] × factor`), VISTA USD
   (`nominal × _senda_trm[mes]`), FIC/Cta Ahorro (parten del VM base en COP), y la rama
   principal YANKEE vía `valorar_titulo(..., trm=_senda_trm[mes])`. El bloque de re-valoración
   TIR solo aplica a bonos IPC (COP/UVR), no a USD. No existe rama que deje `valor_mercado` en
   escala USD.

2. **Armado del snapshot `df_proy` — DESCARTADA.** `_construir_df_proy_rf` mapea
   `valor_mercado` desde `t.valor_mercado` (COP) sin reescalar; `_enriquecer_snapshot_tramo` no
   toca `valor_mercado`; `_finalizar_df_proy` preserva el VM.

3. **Encadenamiento entre tramos (`_titulos_a_dataframe_base`, i>0) — DESCARTADA.** La base del
   tramo i>0 reconstruye `valor_mercado` en COP sin dividir/convertir; `cargar_titulos_desde_snapshot`
   restaura el VM en COP. Verificado en el `portafolio.db` real: cierre 2027-12-31 (tramo i>0)
   = 15.094.118.182 COP (escala COP correcta).

4. **Senda TRM del tramo — DESCARTADA.** `_trm_tramo_ini`/`_trm_tramo_fin` se pueblan desde
   `estado_previo.trm_cierre` (encadenado) / `inputs_tramo_i.trm_cierre`; `_senda_trm` interpola
   en COP/USD reales. Nunca se usa TRM = 1.0 ni se omite `trm_cierre`.

**Evidencia del dato real (id 817856 "BN ECOPETROL USD"):** base = 12.657.628.505,76 COP;
cierre 2026-12-31 (tramo 0) = 14.389.842.600,95 COP; cierre 2027-12-31 (tramo i>0) =
15.094.118.182 COP — todos en escala COP. El "14.389.842" del reporte original es el mismo valor
leído con escala errónea (÷1000).

**Consecuencia para el fix:** el fix del motor/snapshot NO es necesario. La Tarea 5 se reconvierte
en (5.1) un test de integración multi-tramo que ejercita `proyectar_portafolio` por tramo + el
round-trip de persistencia (tramo 0 e i>0) y verifica el VM en COP — cerrando la única brecha de
cobertura real —, y (5.2) la decisión sobre la mitigación de `tools/detalle_pyg_tramo.py`, que al
quedar el snapshot oficial correcto es idempotente y puede conservarse como defensa documentada o
retirarse.

## Correctness Properties

Property 1: Bug Condition — Serie RF mensual continua y completa en modo por tramos

_For any_ corrida en modo `por_tramos` (donde `isBugCondition_D1` es verdadero), el sistema
corregido SHALL producir una serie mensual de RF continua a través de TODOS los tramos tal que:
la hoja `RF_Mensual_Tramos` exista en el Excel; el total de filas iguale la suma de
`sub_horizonte` de todos los tramos; el enero de arranque de cada tramo esté presente (sin el
desplazamiento +1 mes); no haya huecos ni duplicados en las fronteras de año; y el P&G de enero
de cada año se calcule como `VM(fin de enero) − VM(cierre 31-dic del año anterior)`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7**

Property 2: Bug Condition — VM de cierre de RF internacional en COP (×TRM)

_For any_ título de RF internacional en USD valorado al cierre de un tramo (donde
`isBugCondition_D2` es verdadero), el sistema corregido SHALL registrar en el snapshot oficial
`portafolio_clasificado_proy` un VM de cierre en escala COP consistente con el VM inicial, es
decir `vm_cierre ≈ nominal × precio/100 × trm`, eliminando la discrepancia de ~TRM veces y
produciendo un P&G por diferencia de VM coherente sin depender de una mitigación externa.

**Validates: Requirements 2.8, 2.9, 2.10, 2.11**

Property 3: Preservation — Comportamiento no-buggy inalterado

_For any_ entrada donde ni `isBugCondition_D1` ni `isBugCondition_D2` se cumplen (modo
`punta_a_punta`, títulos en COP/UVR, series mensuales RV/Alt/Derivados, valoración unitaria),
el sistema corregido SHALL producir exactamente el mismo resultado que el sistema original
(`F(X) = F'(X)`), preservando el valor de mercado por título, la clave `rf_mensual_contable` de
`punta_a_punta` y las hojas mensuales de los demás activos.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

Asumiendo que el análisis de causa raíz es correcto, y respetando la regla del proyecto de
replicar patrones existentes (reutilizar el patrón de `informe_pyg_por_tramo` ya usado por
RV/Alt/Derivados), los cambios son:

**1. Helper contable mensual compartido (Defecto 1 — Req 2.7, 3.6)**

**Archivo:** nuevo helper puro (p.ej. `scripts/rf_contable_mensual.py` o función en
`tools/detalle_pyg_tramo.py`), reutilizado por `_ejecutar_escenario` y por
`_ejecutar_escenario_por_tramos`.

- Extraer la lógica NEG/DV/VC embebida en `_ejecutar_escenario` (bloque "RF Mensual Contable")
  a una función pura que reciba la lista de `ResultadoMes`, el `port` tras la corrida y la
  fecha de fin, y devuelva la lista de filas `rf_mensual_contable` (misma forma: `mes`,
  `fecha`, `pyg_mes`, `ori_mes`, `total_resultado_mes`, `cupones_mes`, `vm_total`,
  `vtir_total`, `n_titulos`).
- `_ejecutar_escenario` pasa a llamar al helper (sin cambio de resultado → equivalencia).
- Eliminar la duplicación con `tools/detalle_pyg_tramo.py` (consolidar la regla contable en un
  único punto).

**2. Corrección del fechado del mes de arranque (Defecto 1 — Req 2.3)**

**Archivo:** `scripts/motor_proyeccion.py`

- Corregir el desplazamiento +1 mes de modo que un tramo que arranca el 1-ene emita el enero
  de arranque. La corrección debe preservar la semántica de `punta_a_punta` (equivalencia en
  P&L mensual para los casos ya soportados): se evaluará indexar el fechado desde `mes - 1`
  para la etiqueta de fecha, o ajustar el `fecha_inicio` que recibe el motor desde el
  orquestador, documentando la regla "el cierre de un año es el T0 del año siguiente".
- Garantizar que el conteo de filas = `sub_horizonte` por tramo (coherente con el dashboard).

**3. Captura y persistencia de la serie RF mensual por tramo (Defecto 1 — Req 2.1, 2.2, 2.5)**

**Archivo:** `app_dash.py::_ejecutar_escenario_por_tramos`

- Capturar el retorno de `proyectar_portafolio(...)` (lista `ResultadoMes`) en cada tramo.
- Construir `rf_mensual_contable` del tramo con el helper del punto 1.
- Anexar la serie mensual de RF al registro del tramo (`detalle_por_tramo[...]`) y persistirla
  en el payload por corte (`informe_pyg_por_tramo`), igual que ya se hace con `rv`/`alt`/
  `deriv_pyg`, de modo que el encadenamiento aplique la regla de frontera de año (P&G enero =
  VM fin enero − VM cierre 31-dic anterior).

**4. Exportación de la hoja RF_Mensual por tramos (Defecto 1 — Req 2.1, 2.6)**

**Archivo:** `tools/exportar_excel_tramos.py::_anexar_series_mensuales_tramos`

- Reconstruir y escribir la hoja `RF_Mensual_Tramos` concatenando la serie RF mensual de TODOS
  los cortes desde `informe_pyg_por_tramo`, análoga a `RV_Mensual_Tramos` (solo lectura RO,
  parametrizada, nombre de hoja saneado). No alterar las hojas RV/Alt/Derivados existentes.

**5. Corrección de la escala COP del VM de cierre USD (Defecto 2 — Req 2.8, 2.9, 2.10, 2.11)**

**Archivo:** `scripts/motor_proyeccion.py` y/o armado del snapshot en `app_dash.py`

- Confirmar la causa raíz (ver Hypothesized Root Cause) con el caso documentado (id 817856) y
  corregirla en el punto exacto donde el VM de cierre USD pierde la escala COP (rama del bucle,
  armado de `df_proy`, o encadenamiento `_titulos_a_dataframe_base`).
- Garantizar que `portafolio_clasificado_proy` registre el VM de cierre USD en COP (×TRM),
  consistente con el VM inicial.
- Decidir el destino de la mitigación de `tools/detalle_pyg_tramo.py`: retirarla si el snapshot
  oficial queda correcto, o conservarla como defensa documentada.

## Testing Strategy

### Validation Approach

Estrategia en dos fases: primero generar contraejemplos que demuestren el bug sobre el código
SIN corregir (fix-checking exploratorio), luego verificar que el fix funciona y preserva el
comportamiento existente (preservation-checking). Se usa `pytest` + Hypothesis (patrón ya
establecido en `tests/`) y el arnés de equivalencia `tools/equivalencia_motor_rf.py` como
baseline del motor RF.

### Exploratory Bug Condition Checking

**Goal:** Surgir contraejemplos que demuestren ambos defectos ANTES de implementar el fix.
Confirmar o refutar el análisis de causa raíz (especialmente D2, cuya causa exacta está por
confirmar). Si se refuta, re-hipotetizar.

**Test Plan (D1):** correr una proyección `por_tramos` con una frontera de año (p.ej. fin
31-dic-2027) y aseverar que la serie RF mensual contiene el enero de arranque de cada tramo y
que el conteo de filas = suma de `sub_horizonte`. Sobre código SIN corregir debe fallar (falta
enero, falta la hoja).

**Test Plan (D2):** cargar el caso documentado (corte 2026-12-31, id 817856 BN ECOPETROL USD) y
aseverar que el VM de cierre en el snapshot está en escala COP (`≈ nominal × precio/100 × trm`).
Sobre código SIN corregir debe fallar (escala ~TRM menor). Instrumentar en qué punto exacto se
pierde la escala (rama del bucle vs. snapshot vs. encadenamiento) para confirmar la causa raíz.

**Test Cases:**
1. **Enero de arranque presente (por tramos):** pulsar corrida con frontera de año; verificar
   enero en la serie RF (fallará sobre código sin corregir).
2. **Hoja RF_Mensual_Tramos existe:** exportar Excel por tramos y verificar la hoja (fallará).
3. **Conteo de filas = Σ sub_horizonte:** verificar coherencia con el dashboard (fallará).
4. **VM de cierre USD en COP:** caso id 817856 (fallará; ~TRM veces menor).

**Expected Counterexamples:**
- D1: la serie RF arranca en feb en lugar de ene; falta la hoja; conteo 11 en vez de 12.
- D2: `vm_cierre_snapshot ≈ vm_base / trm` (escala USD) en lugar de COP.
- Posibles causas D2: rama especial del bucle mensual, armado de `df_proy`, o encadenamiento.

### Fix Checking

**Goal:** Verificar que para todas las entradas donde la condición de bug se cumple, la función
corregida produce el comportamiento esperado.

**Pseudocode (Defecto 1):**
```
FOR ALL X WHERE isBugCondition_D1(X) DO
  rf_mensual := F'(X).rf_mensual_contable
  ASSERT existe_hoja_RF_Mensual(F'(X))
  ASSERT total_filas(rf_mensual) = SUMA(sub_horizonte de todos los tramos de X)
  ASSERT para_cada_tramo, enero_de_arranque ESTA_PRESENTE en rf_mensual
  ASSERT serie_continua_sin_huecos_ni_duplicados(rf_mensual)
  ASSERT pyg_enero = VM(fin_enero) - VM(cierre_31_dic_anterior)
END FOR
```

**Pseudocode (Defecto 2):**
```
FOR ALL X WHERE isBugCondition_D2(X) DO
  vm_cierre := F'(X).valor_mercado_en_snapshot
  ASSERT vm_cierre ≈ nominal * precio/100 * trm   // escala COP
END FOR
```

### Preservation Checking

**Goal:** Verificar que para todas las entradas donde la condición de bug NO se cumple, la
función corregida produce el mismo resultado que la original.

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition_D1(X) AND NOT isBugCondition_D2(X) DO
  ASSERT F(X) = F'(X)
END FOR

// En particular:
FOR ALL T WHERE T.moneda = "COP" DO
  ASSERT valorar_titulo_antes(T) = valorar_titulo_despues(T)
END FOR
```

**Testing Approach:** se recomienda property-based testing (Hypothesis) para el
preservation-checking porque genera muchos casos automáticamente, cubre bordes que los unit
tests manuales omitirían, y da garantías fuertes de que el comportamiento es idéntico para todos
los inputs no-buggy. El arnés `tools/equivalencia_motor_rf.py` (modo `baseline`/`check`) sirve
como red de equivalencia del motor RF end-to-end.

**Test Plan:** capturar baseline del comportamiento sobre el código SIN corregir (valoración COP,
VM de cierre `punta_a_punta`, hojas RV/Alt/Derivados, `rf_mensual_contable` de `punta_a_punta`),
y escribir tests de equivalencia que verifiquen que el fix no los altera.

**Test Cases:**
1. **Equivalencia valoración COP:** `valorar_titulo` sobre títulos COP/UVR idéntico antes/después.
2. **Equivalencia valoración USD aislada:** `valorar_titulo` YANKEE aislado idéntico (ya correcto).
3. **Equivalencia `punta_a_punta`:** `rf_mensual_contable` y VM de cierre idénticos antes/después.
4. **Preservación hojas RV/Alt/Derivados:** las hojas mensuales no-RF por tramos no cambian.

### Unit Tests

- Helper contable NEG/DV/VC: cada categoría con casos conocidos; igualdad con la lógica embebida.
- Fechado del mes de arranque: un tramo 1-ene..31-dic produce ene..dic (12 etiquetas correctas).
- P&G de frontera de año: `VM(fin ene) − VM(cierre 31-dic anterior)`.
- Escala COP del VM de cierre USD en el snapshot (caso id 817856).

### Property-Based Tests (Hypothesis)

- **Property 1 (fix-checking D1):** generar configuraciones de tramos (nº tramos, sub_horizontes,
  fronteras de año) y verificar continuidad/completitud de la serie RF (enero presente, Σ filas).
- **Property 2 (fix-checking D2):** generar títulos USD (nominal, precio, TRM) y verificar que el
  VM de cierre en el snapshot cumple `≈ nominal × precio/100 × trm`.
- **Property 3 (preservation):** generar títulos COP/UVR y verificar `valorar_titulo` idéntico
  antes/después; generar corridas `punta_a_punta` y verificar equivalencia de la serie mensual.

### Integration Tests

- Corrida `por_tramos` multi-año de extremo a extremo: Excel exportado contiene
  `RF_Mensual_Tramos` continua jul-2026 → dic-2027 sin huecos ni duplicados, coherente con el
  conteo del dashboard.
- Corrida `por_tramos` con RF internacional: VM total USD consistente y P&G coherente usando el
  snapshot oficial (sin depender de la mitigación de `detalle_pyg_tramo.py`).
- Corrida `punta_a_punta` de regresión: `RF_Mensual` y VM de cierre idénticos a los previos.

---

## Fase 2 — Diseño de los Defectos 3-7 (hallazgos de corrida real)

### Defecto 3 — Fechado del primer tramo parcial (regresión del fix 4.2)

**Causa raíz (confirmada numéricamente).** El bucle mensual de `scripts/motor_proyeccion.py::proyectar_portafolio` usa dos fechas por mes:
- `fecha_mes = fecha_inicio + relativedelta(months=mes)` — fecha de VALORACIÓN (fin del período). NO se toca.
- `fecha_etiqueta_mes = fecha_inicio + relativedelta(months=mes - 1)` — etiqueta del `ResultadoMes` (introducida en la Tarea 4.2).

Para un tramo que arranca el 1-ene (i>0), `months=mes-1` produce ene..dic (correcto). Pero el PRIMER tramo arranca en la fecha base real (p.ej. 31-jul-2026) y es parcial; con `months=mes-1` su mes 1 se etiqueta jul-2026 (el T0) y la serie corre -1 mes, perdiendo dic-2026.

**Fix.** Condicionar la etiqueta al carácter del arranque del tramo:
- Si `fecha_inicio.day == 1` (frontera de año / T0 = cierre del tramo anterior) -> etiqueta = `fecha_inicio + relativedelta(months=mes-1)` (ene..dic). Preserva la Fase 1.
- Si `fecha_inicio.day != 1` (primer tramo parcial, T0 = fecha base real) -> etiqueta = `fecha_mes = fecha_inicio + relativedelta(months=mes)` (mes de valoración), de modo que el primer mes proyectado sea el mes SIGUIENTE al T0 (ago..dic), sin perder diciembre.

La valoración (`fecha_mes`) NO cambia en ningún caso -> VM/VTIR/P&L/cupones idénticos; solo cambia la ETIQUETA del primer tramo parcial. Impacto en `punta_a_punta`: comparte el bucle; como su fecha base también es de mitad de mes (sidebar), la rama `day != 1` le aplica y etiqueta por valoración (comportamiento correcto). Se añade un test gemelo de `test_prop1_motor_no_pierde_enero_de_arranque` para el primer tramo parcial (31-jul -> sub=5 debe dar ago..dic).

**Archivo:** `scripts/motor_proyeccion.py` (línea del `fecha_etiqueta_mes`). _Validates: Requirements 3.1, 3.2, 3.3, 3bis._

### Defecto 4 — Preservar `id_cobertura` en el round-trip del snapshot (i>0)

**Causa raíz.** `tools/export_resumen_tramos.py::_categoria` separa "Subyacentes de Swaps" por el flag `es_subyacente`, que `construir_rf_detalle` deriva de `Titulo.id_cobertura`. El round-trip del snapshot no preserva ese campo: ni lo emite `app_dash._titulos_a_dataframe_base` (i>0), ni está en la allowlist `_COLUMNAS_PROY`, ni lo pasa `proyeccion_tramos.cargar_titulos_desde_snapshot` al reconstruir el `Titulo`.

**Fix (3 cambios coordinados, mismo patrón que `clase_origen`):**
1. `scripts/proyeccion_tramos.py`: añadir `"id_cobertura"` a `_COLUMNAS_PROY`.
2. `app_dash.py::_titulos_a_dataframe_base` (rama i>0): emitir `'id_cobertura': t.id_cobertura`.
3. `scripts/proyeccion_tramos.py::cargar_titulos_desde_snapshot`: pasar `id_cobertura=_str(row.get("id_cobertura", ""))` al constructor `Titulo(...)`.

Invariante: no altera la reconstrucción RF ni la valoración (es un campo de metadato). _Validates: Requirements 4.1, 4bis._

### Defecto 5 — Retirar la advertencia estática del bug VM-USD

**Causa raíz.** `tools/export_resumen_tramos.py::generar_resumen_por_tramo` fija una columna `advertencia` con un literal estático para `_CATS_USD_EN_REVISION = {"RF Internacional", "Subyacentes de Swaps"}`, residuo de cuando el Defecto 2 se creía activo.

**Fix.** Retirar la columna `advertencia` (y su mapeo "Advertencia" en `tools/exportar_excel_tramos.py::_COLS_LEGIBLES`) o dejarla vacía/neutra; eliminar `_CATS_USD_EN_REVISION`. Coherente con la Tarea 5.2 (D2 cerrado como no reproducible). _Validates: Requirement 5.1._

### Defecto 6 — Exportar `RF_Detalle_Titulo_<corte>` en el Excel por tramos

**Causa raíz.** `rf_detalle` ya se persiste por corte en `informe_pyg_por_tramo` (clave `"rf_detalle"`), pero `tools/exportar_excel_tramos.py` no lo escribe como hoja.

**Fix.** Añadir una función análoga a `_anexar_series_mensuales_tramos` que lea la clave `"rf_detalle"` de cada registro y escriba una hoja por corte `RF_Detalle_Titulo_<corte>` (nombre saneado con `_nombre_hoja`, lectura RO). Sin recalcular. _Validates: Requirement 6.1._

### Defecto 7 — Discriminar RF Local vs Internacional en RF_Mensual

**Causa raíz.** `scripts/rf_contable_mensual.py::construir_rf_mensual_contable` agrega todos los títulos vivos sin partición por moneda.

**Fix (aditivo, no rompe punta_a_punta).** Dentro del bucle por título, clasificar el grupo por `t.moneda` (local si está en {"", COP, UVR}; internacional en otro caso — misma regla que `export_resumen_tramos._categoria`) y acumular columnas separadas `pyg_mes_local/pyg_mes_intl`, `ori_mes_local/ori_mes_intl`, `total_resultado_mes_local/intl`, `vm_total_local/intl`. Las columnas de TOTAL existentes se conservan intactas (suma local+intl == total), de modo que la hoja `RF_Mensual` de `punta_a_punta` no cambia en sus columnas actuales. _Validates: Requirements 7.1, 7.2, 7bis._

## Correctness Properties (Fase 2)

Property 4: Fechado del primer tramo parcial (Defecto 3)
_For any_ corrida `por_tramos` cuyo primer tramo arranca a mitad de año, la serie RF mensual del primer tramo SHALL cubrir desde el mes siguiente al T0 hasta el mes de cierre (p.ej. ago..dic) sin incluir el T0 como mes de P&G ni perder el mes de cierre; y los tramos que arrancan el 1-ene SHALL seguir cubriendo ene..dic.
**Validates: Requirements 3.1, 3.2, 3.3**

Property 5: Subyacentes separados en todos los tramos (Defecto 4)
_For any_ corrida `por_tramos` con subyacentes de swap, la categoría "Subyacentes de Swaps" SHALL separarse de "RF Internacional" en TODOS los cortes (tramo 0 e i>0), es decir `id_cobertura` sobrevive el round-trip del snapshot.
**Validates: Requirement 4.1**

Property 6: Sin advertencia estática de bug VM-USD (Defecto 5)
_For any_ resumen por corte, NINGUNA categoría SHALL llevar la advertencia estática del bug VM-USD.
**Validates: Requirement 5.1**

Property 7: Discriminación local/intl aditiva (Defecto 7)
_For any_ serie RF mensual, `pyg_mes_local + pyg_mes_intl == pyg_mes` (y análogas), y los totales existentes SHALL quedar idénticos a los de antes del cambio (preservación de punta_a_punta).
**Validates: Requirements 7.1, 7.2**

## Testing Strategy (Fase 2)

- **Defecto 3:** test gemelo del motor para el primer tramo parcial (31-jul-2026, sub=5 -> ago..dic; sin jul, con dic) + re-ejecutar `test_prop1_motor_no_pierde_enero_de_arranque` (1-ene -> ene..dic) para confirmar no-regresión. Test de orquestador que verifique que la serie `rf_mensual_contable` del primer tramo arranca en el mes siguiente al T0 y termina en el cierre.
- **Defecto 4:** test del round-trip que siembre un título con `id_cobertura` no vacío, lo persista y lo reconstruya, y verifique que `es_subyacente` se preserva en i>0 (categoría "Subyacentes de Swaps").
- **Defecto 5:** test que verifique que el resumen por corte no contiene la advertencia estática.
- **Defecto 6:** test del exportador que, con `rf_detalle` sembrado en `informe_pyg_por_tramo`, verifique que se escribe la hoja `RF_Detalle_Titulo_<corte>`.
- **Defecto 7:** test de equivalencia que verifique `local + intl == total` y que las columnas de total no cambian (preservación de punta_a_punta).
