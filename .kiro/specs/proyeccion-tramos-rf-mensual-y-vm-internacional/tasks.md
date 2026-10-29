# Implementation Plan

## Overview

Plan de implementación del bugfix del modo `por_tramos` siguiendo la metodología de bug condition. Se corrigen DOS defectos: (D1) ausencia de la hoja RF_Mensual y pérdida del enero de arranque de cada tramo, y (D2) escala COP perdida (falta ×TRM) en el VM de cierre de RF internacional en USD. El flujo es exploratorio: primero tests que fallan sobre el código sin corregir (fix-checking), luego tests de preservación que pasan sobre el código sin corregir, después la implementación del fix, y finalmente la validación de que los tests exploratorios pasan y los de preservación siguen pasando.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1", "2", "3"],
      "description": "Tests exploratorios (D1, D2) y de preservación sobre el código SIN corregir. 1 y 2 deben fallar; 3 debe pasar. Independientes entre sí."
    },
    {
      "wave": 2,
      "tasks": ["4"],
      "description": "Fix del Defecto 1 (aditivo, bajo riesgo). Depende de las tareas 1 y 3."
    },
    {
      "wave": 3,
      "tasks": ["5"],
      "description": "Fix del Defecto 2 (toca valoración/encadenamiento). Depende de las tareas 2 y 3; se hace después de 4."
    },
    {
      "wave": 4,
      "tasks": ["6"],
      "description": "Verificación de preservación tras los fixes. Depende de las tareas 4 y 5."
    },
    {
      "wave": 5,
      "tasks": ["7"],
      "description": "Checkpoint final: toda la suite pasa. Depende de todas las anteriores."
    }
  ]
}
```

## Tasks

- [x] 1. Escribir test exploratorio de condición de bug — Defecto 1 (serie RF mensual por tramos)
  - **Property 1: Bug Condition** - Serie RF mensual continua y completa en modo por tramos
  - **CRITICAL**: Este test DEBE FALLAR sobre el código sin corregir — la falla confirma que el bug existe
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: Este test codifica el comportamiento esperado — validará el fix cuando pase tras la implementación
  - **GOAL**: Surgir contraejemplos que demuestren que el modo `por_tramos` pierde el enero de arranque y no exporta la hoja RF_Mensual
  - **Scoped PBT Approach**: para este bug determinista, acotar la propiedad a casos concretos de frontera de año (p.ej. corrida `por_tramos` con fin 31-dic-2027 y un tramo que arranca el 1-ene) para asegurar reproducibilidad; luego generalizar a configuraciones de tramos (nº tramos, sub_horizontes) con Hypothesis
  - Condición de bug (del diseño): `isBugCondition_D1(X)` es verdadero cuando `X.modo = "por_tramos"` Y (no existe hoja RF_Mensual en el Excel O algún tramo pierde su enero de arranque en la serie mensual de RF)
  - Las aserciones deben coincidir con las Expected Behavior Properties (Property 1 del diseño):
    - `ASSERT existe_hoja_RF_Mensual(F'(X))` (hoja `RF_Mensual_Tramos` presente)
    - `ASSERT total_filas(rf_mensual) == SUMA(sub_horizonte de todos los tramos de X)`
    - `ASSERT para_cada_tramo, enero_de_arranque ESTÁ_PRESENTE en rf_mensual` (sin desplazamiento +1 mes)
    - `ASSERT serie_continua_sin_huecos_ni_duplicados(rf_mensual)`
    - `ASSERT pyg_enero == VM(fin_enero) - VM(cierre_31_dic_anterior)` (regla "el cierre de un año es el T0 del siguiente")
  - Ejecutar el test sobre el código SIN corregir (usar `pytest` + Hypothesis, patrón de `tests/`)
  - **EXPECTED OUTCOME**: El test FALLA (correcto — prueba que el bug existe: serie arranca en feb en vez de ene; falta la hoja; conteo 11 en vez de 12)
  - Documentar contraejemplos encontrados para entender la causa raíz (confirmar descarte de `ResultadoMes` en `_ejecutar_escenario_por_tramos` y el `+1 mes` en `motor_proyeccion.py`)
  - Marcar la tarea como completa cuando el test esté escrito, ejecutado y la falla documentada
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. Escribir test exploratorio de condición de bug — Defecto 2 (escala COP del VM de cierre USD)
  - **Property 2: Bug Condition** - VM de cierre de RF internacional en COP (×TRM)
  - **CRITICAL**: Este test DEBE FALLAR sobre el código sin corregir — la falla confirma que el bug existe
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: Este test codifica el comportamiento esperado — validará el fix cuando pase tras la implementación
  - **GOAL**: Surgir contraejemplos que demuestren que el VM de cierre USD queda ~TRM veces menor que el VM inicial, e instrumentar el punto exacto donde se pierde la escala para confirmar la causa raíz (rama del bucle mensual vs. armado de `df_proy` vs. encadenamiento `_titulos_a_dataframe_base`)
  - **Scoped PBT Approach**: para este bug, acotar la propiedad al caso documentado (corte 2026-12-31, id 817856 "BN ECOPETROL USD", YANKEE, Negociable) para asegurar reproducibilidad; luego generalizar con Hypothesis a títulos USD (nominal, precio, TRM)
  - Condición de bug (del diseño): `isBugCondition_D2(X)` es verdadero cuando `X.moneda = "USD"` Y `X.tipo IN {YANKEE, EstructuraUSD}` Y `X.es_cierre_de_tramo` Y el VM de cierre en el snapshot NO está en escala COP (falta ×TRM)
  - Las aserciones deben coincidir con las Expected Behavior Properties (Property 2 del diseño):
    - `ASSERT vm_cierre ≈ nominal * precio/100 * trm` (escala COP, consistente con el VM inicial)
    - Para el caso documentado: VM de cierre esperado ≈ 12.657.628.506 COP (= VM×TRM), NO 14.389.842
  - Ejecutar el test sobre el código SIN corregir
  - **EXPECTED OUTCOME**: El test FALLA (correcto — `vm_cierre_snapshot ≈ vm_base / trm`, escala USD en lugar de COP)
  - Documentar contraejemplos y el punto de pérdida de escala para confirmar/refutar la hipótesis de causa raíz; si se refuta, re-hipotetizar
  - Marcar la tarea como completa cuando el test esté escrito, ejecutado y la falla documentada
  - **HALLAZGO (reinvestigación de causa raíz):** el test PASÓ inesperadamente — el Defecto 2 **ya no se reproduce** en el código/datos actuales. Con `portafolio.db` real (solo lectura), el snapshot oficial `portafolio_clasificado_proy` del id 817856 (corte 2026-12-31) ya registra el VM de cierre en **escala COP**: `valor_mercado = 1.438984e+10` (14.389.840.000 COP), consistente con el VM base `1.265763e+10` (12.657.630.000 COP). El "14.389.842" de `docs/deuda-tecnica.md` es ese mismo valor **dividido ~1000** (el defecto fue corregido o el snapshot se regeneró con el fix). Causa raíz descartada en motor/snapshot/encadenamiento (`valorar_titulo` YANKEE devuelve COP correctamente). **Decisión del usuario:** D2 se considera **resuelto**; el test `tests/test_property_vm_cierre_usd_por_tramos.py` se **conserva como test de REGRESIÓN** (garantiza que el VM de cierre USD siga en escala COP). Las tareas 5 (fix D2) y su mitigación quedan como no requeridas salvo que D2 reaparezca. Evidencia: `tests/_diag_d2_real_817856.py`, `tests/_diag_d2_vm_usd_e2e.py`, `tests/_diag_d2_dump_snapshot.py`.
  - _Requirements: 1.6, 1.7, 1.8, 1.9_

- [x] 3. Escribir tests de preservación (ANTES de implementar el fix)
  - **Property 3: Preservation** - Comportamiento no-buggy inalterado
  - **IMPORTANT**: Seguir la metodología observation-first (observar primero el comportamiento sobre el código SIN corregir, luego codificarlo)
  - Observar y registrar sobre el código SIN corregir, usando el arnés `tools/equivalencia_motor_rf.py` (modo `baseline`) como red de equivalencia del motor RF:
    - `valorar_titulo` para títulos en COP/UVR (valor de mercado exacto)
    - `valorar_titulo` para títulos YANKEE/USD aislados (`≈ nominal × precio/100 × trm`, ya correcto)
    - `rf_mensual_contable` y VM de cierre en modo `punta_a_punta` (incluyendo portafolio con RF internacional)
    - Hojas mensuales RV/Alt/Derivados por tramos (`RV_Mensual_Tramos`, `Alt_Mensual_Tramos`, `Derivados_PyG_Tramos`)
  - Escribir tests property-based (Hypothesis) que aseveren el comportamiento observado para todas las entradas donde NI `isBugCondition_D1` NI `isBugCondition_D2` se cumplen (de Preservation Requirements del diseño):
    - `FOR ALL T WHERE T.moneda = "COP": valorar_titulo_antes(T) == valorar_titulo_despues(T)`
    - Equivalencia de `valorar_titulo` YANKEE/USD aislado (ya correcto, no debe cambiar)
    - Equivalencia de la serie `rf_mensual_contable` y VM de cierre en `punta_a_punta` (`F(X) == F'(X)`)
    - Preservación byte a byte de los agregados mensuales de RV/Alt/Derivados por tramos
  - El property-based testing genera muchos casos y da garantías fuertes de que el comportamiento no-buggy es idéntico
  - Ejecutar los tests sobre el código SIN corregir
  - **EXPECTED OUTCOME**: Los tests PASAN (confirma el baseline de comportamiento a preservar)
  - Marcar la tarea como completa cuando los tests estén escritos, ejecutados y pasando sobre el código sin corregir
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 4. Fix para el Defecto 1 — RF_Mensual presente y continua con enero de cada año (aditivo, bajo riesgo)

  - [x] 4.1 Extraer la lógica contable mensual NEG/DV/VC a un helper compartido
    - Crear un helper puro (p.ej. `scripts/rf_contable_mensual.py`) que reciba la lista de `ResultadoMes`, el `port` tras la corrida y la fecha de fin, y devuelva las filas `rf_mensual_contable` (misma forma: `mes`, `fecha`, `pyg_mes`, `ori_mes`, `total_resultado_mes`, `cupones_mes`, `vm_total`, `vtir_total`, `n_titulos`)
    - Reemplazar la lógica embebida en `_ejecutar_escenario` (`app_dash.py`) por una llamada al helper (sin cambio de resultado → equivalencia)
    - Eliminar la duplicación de la regla contable con `tools/detalle_pyg_tramo.py::construir_rf_detalle` (consolidar en un único punto)
    - _Bug_Condition: isBugCondition_D1(X) where X.modo = "por_tramos"_
    - _Expected_Behavior: Property 1 — serie RF mensual continua y completa (del diseño)_
    - _Preservation: Preservation Requirements — mismos valores contables mensuales que la lógica embebida original_
    - _Requirements: 2.7, 3.6_

  - [x] 4.2 Corregir el fechado del mes de arranque en el motor
    - En `scripts/motor_proyeccion.py` corregir el desplazamiento +1 mes (`fecha_mes = fecha_inicio + relativedelta(months=mes)` con `mes` desde 1) de modo que un tramo que arranca el 1-ene emita el enero de arranque (ene..dic → 12 etiquetas correctas)
    - Preservar la semántica de `punta_a_punta` (equivalencia en P&L mensual para los casos ya soportados)
    - Garantizar que el conteo de filas == `sub_horizonte` por tramo, coherente con el dashboard
    - Documentar la regla "el cierre de un año es el T0 del año siguiente"
    - _Bug_Condition: isBugCondition_D1(X) — tramo arranca 1-ene, enero perdido_
    - _Expected_Behavior: Property 1 — enero de arranque presente, Σ filas = Σ sub_horizonte (del diseño)_
    - _Preservation: Preservation Requirements — punta_a_punta sin cambio de P&L mensual_
    - _Requirements: 2.3, 2.6_

  - [x] 4.3 Capturar y persistir la serie RF mensual por tramo en el orquestador
    - En `app_dash.py::_ejecutar_escenario_por_tramos` capturar el retorno de `proyectar_portafolio(...)` (lista `ResultadoMes`) en cada tramo
    - Construir `rf_mensual_contable` del tramo con el helper de la tarea 4.1 e incluir la clave `rf_mensual_contable` en el dict final del modo por tramos
    - Anexar la serie mensual de RF al registro del tramo y persistirla en el payload por corte (`informe_pyg_por_tramo`), igual que `rv`/`alt`/`deriv_pyg`, aplicando la regla de frontera de año (P&G enero = VM fin enero − VM cierre 31-dic anterior)
    - _Bug_Condition: isBugCondition_D1(X) where X.modo = "por_tramos"_
    - _Expected_Behavior: Property 1 — serie continua, clave rf_mensual_contable presente (del diseño)_
    - _Preservation: Preservation Requirements — hojas RV/Alt/Derivados sin alteración_
    - _Requirements: 2.1, 2.2, 2.4, 2.5_

  - [x] 4.4 Exportar la hoja RF_Mensual_Tramos en el Excel por tramos
    - En `tools/exportar_excel_tramos.py::_anexar_series_mensuales_tramos` reconstruir y escribir la hoja `RF_Mensual_Tramos` concatenando la serie RF mensual de TODOS los cortes desde `informe_pyg_por_tramo`, análoga a `RV_Mensual_Tramos` (solo lectura RO, parametrizada, nombre de hoja saneado)
    - No alterar las hojas RV/Alt/Derivados existentes
    - _Bug_Condition: isBugCondition_D1(X) — no existe hoja RF_Mensual_
    - _Expected_Behavior: Property 1 — hoja RF_Mensual_Tramos existe, Σ filas = Σ sub_horizonte (del diseño)_
    - _Preservation: Preservation Requirements — hojas RV/Alt/Derivados por tramos inalteradas_
    - _Requirements: 2.1, 2.6_

  - [x] 4.5 Verificar que el test exploratorio del Defecto 1 ahora pasa
    - **Property 1: Expected Behavior** - Serie RF mensual continua y completa en modo por tramos
    - **IMPORTANT**: Re-ejecutar el MISMO test de la tarea 1 — NO escribir un test nuevo
    - El test de la tarea 1 codifica el comportamiento esperado; cuando pasa, confirma que se satisface
    - **EXPECTED OUTCOME**: El test PASA (confirma que el Defecto 1 está corregido)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 5. Defecto 2 — VM de cierre de RF internacional en COP (×TRM) — [NO REPRODUCIBLE: blindaje + decisión]

  > **Reconvertida.** La re-investigación de todo el proceso por tramos (motor + orquestación +
  > encadenamiento + persistencia) y la inspección del `portafolio.db` real confirman que D2 NO se
  > reproduce: el VM de cierre USD ya está en escala COP (×TRM) en tramo 0 y multi-tramo (i>0). El
  > fix del motor/snapshot no es necesario. Esta tarea pasa a BLINDAR el comportamiento correcto con
  > un test de integración multi-tramo y a decidir el destino de la mitigación.

  - [x] 5.1 Test de integración multi-tramo del VM de cierre USD en COP (blindaje)
    - **Property 2: Expected Behavior** - VM de cierre de RF internacional en COP (×TRM)
    - Escribir un test de integración que ejercite la RUTA REAL (no solo el pipeline de snapshot aislado): correr `proyectar_portafolio` por tramo con un título YANKEE/USD y recorrer el round-trip completo `_titulos_a_dataframe_base → _construir_df_proy_rf → _enriquecer_snapshot_tramo → _finalizar_df_proy → persistir_snapshot_proy → cargar_snapshot_proy → cargar_titulos_desde_snapshot`
    - Aseverar en CADA tramo (incluido i>0, multi-año) que `valor_mercado ≈ nominal × precio/100 × trm_cierre` (escala COP), cerrando la brecha de cobertura del test de la Tarea 2 (que no corre el motor ni el round-trip de persistencia)
    - **EXPECTED OUTCOME**: El test PASA sobre el código actual (confirma que D2 no se reproduce y blinda contra regresiones futuras)
    - _Bug_Condition: isBugCondition_D2(X) — no reproducible; se blinda que NO reaparezca_
    - _Expected_Behavior: Property 2 — vm_cierre ≈ nominal × precio/100 × trm en tramo 0 e i>0 (del diseño)_
    - _Preservation: Preservation Requirements — valoración unitaria COP y USD aislada inalterada; punta_a_punta sin cambio_
    - _Requirements: 2.8, 2.9, 2.11_

  - [x] 5.2 Decidir el destino de la mitigación temporal
    - Verificar (con el test de 5.1) que el P&G por diferencia de VM de la RF internacional es coherente usando el snapshot oficial, SIN depender de la mitigación de `tools/detalle_pyg_tramo.py`
    - Dado que el snapshot oficial ya está en COP, la re-valoración de la mitigación es idempotente: decidir si se conserva como defensa documentada (actualizando su docstring para quitar el lenguaje de "VM corrupto") o se retira; documentar la decisión
    - _Bug_Condition: isBugCondition_D2(X) — no reproducible_
    - _Expected_Behavior: Property 2 — P&G coherente usando el snapshot oficial (del diseño)_
    - _Preservation: Preservation Requirements — sin regresiones en los informes existentes_
    - _Requirements: 2.10, 2.11_

  - [x] 5.3 Verificar que el test de regresión del Defecto 2 (Tarea 2) sigue pasando
    - **Property 2: Expected Behavior** - VM de cierre de RF internacional en COP (×TRM)
    - **IMPORTANT**: Re-ejecutar el MISMO test de la tarea 2 (ahora test de regresión) + el nuevo test de integración de 5.1 — NO reescribir el test de la tarea 2
    - **EXPECTED OUTCOME**: Ambos PASAN (confirma que el VM de cierre USD está en escala COP para el caso id 817856 y en multi-tramo)
    - _Requirements: 2.8, 2.9, 2.10, 2.11_

- [x] 6. Verificar que los tests de preservación siguen pasando
  - **Property 3: Preservation** - Comportamiento no-buggy inalterado
  - **IMPORTANT**: Re-ejecutar los MISMOS tests de la tarea 3 — NO escribir tests nuevos
  - Ejecutar los tests de preservación property-based de la tarea 3 y el arnés `tools/equivalencia_motor_rf.py` (modo `check`)
  - **EXPECTED OUTCOME**: Los tests PASAN (confirma que no hay regresiones): valoración COP idéntica, VM de cierre y `rf_mensual_contable` de `punta_a_punta` idénticos, hojas RV/Alt/Derivados por tramos inalteradas
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 7. Checkpoint — Asegurar que todos los tests pasan
  - Ejecutar la suite completa (fix-checking D1 y D2, preservation-checking, unit tests e integration tests del diseño)
  - Tests de integración: corrida `por_tramos` multi-año con `RF_Mensual_Tramos` continua jul-2026 → dic-2027 sin huecos ni duplicados; corrida `por_tramos` con RF internacional con VM total USD consistente; corrida `punta_a_punta` de regresión con `RF_Mensual` y VM de cierre idénticos
  - Asegurar que todos los tests pasan; preguntar al usuario si surgen dudas
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

## Notes

- **Orden de ejecución:** las tareas 1–3 (exploración y preservación) se ejecutan sobre el código SIN corregir. Las tareas 1 y 2 DEBEN FALLAR (confirman los bugs); la tarea 3 DEBE PASAR (fija el baseline a preservar). No corregir el código ni los tests cuando fallen las tareas 1 y 2.
- **Defecto 1 primero (aditivo, bajo riesgo), Defecto 2 después (toca valoración/encadenamiento, requiere tests de equivalencia).**
- **PBT acotado:** para bugs deterministas (ambos defectos tienen casos concretos documentados), acotar la propiedad al caso reproducible y luego generalizar con Hypothesis.
- **Arnés de equivalencia:** `tools/equivalencia_motor_rf.py` (modos `baseline`/`check`) es la red de seguridad del motor RF end-to-end para el preservation-checking.
- **Regla de negocio clave:** "el cierre de un año es el T0 del año siguiente" → P&G de enero = VM(fin enero) − VM(cierre 31-dic anterior).
- **Causa raíz de D2 por confirmar:** la valoración unitaria YANKEE ya es correcta; la pérdida de escala está en rama alternativa del bucle, armado de `df_proy`, encadenamiento `_titulos_a_dataframe_base`, o senda TRM del tramo. Confirmar en la tarea 2 y corregir en 5.1.

## Fase 2 — Tareas (Defectos 3-7, hallazgos de corrida real)

- [x] 8. Defecto 3 — Corregir el fechado del PRIMER tramo parcial (regresión del fix 4.2)
  - **Property 4: Fechado del primer tramo parcial**
  - Escribir primero un test exploratorio que FALLE sobre el código actual: para un primer tramo que arranca a mitad de año (31-jul-2026, sub_horizonte=5), la serie del motor debe etiquetar ago..dic (sin jul como mes de P&G, sin perder dic). Hoy etiqueta jul..nov (corrido -1 mes).
  - Corregir en `scripts/motor_proyeccion.py` la etiqueta `fecha_etiqueta_mes`: usar `months=mes-1` SOLO si `fecha_inicio.day == 1` (frontera de año); usar `fecha_mes` (months=mes, fecha de valoración) si `fecha_inicio.day != 1` (primer tramo parcial / punta_a_punta).
  - Re-ejecutar `test_prop1_motor_no_pierde_enero_de_arranque` (1-ene -> ene..dic) para confirmar no-regresión; añadir el test gemelo del primer tramo parcial.
  - Verificar preservación de `punta_a_punta` (arnés de equivalencia + tests de preservación).
  - _Bug_Condition: primer tramo con fecha_inicio.day != 1 — serie corrida -1 mes_
  - _Expected_Behavior: Property 4 — ago..dic en el primer tramo; ene..dic en tramos 1-ene_
  - _Preservation: punta_a_punta sin cambio; tramos 1-ene sin regresión_
  - _Requirements: 3.1, 3.2, 3.3, 3bis_

- [x] 9. Defecto 4 — Preservar `id_cobertura` en el round-trip del snapshot (i>0)
  - **Property 5: Subyacentes separados en todos los tramos**
  - Escribir test que FALLE hoy: tras el round-trip (persistir + reconstruir) de un título con `id_cobertura` no vacío, el título reconstruido pierde `id_cobertura` -> `es_subyacente=False` en i>0.
  - Corregir los 3 puntos: añadir `"id_cobertura"` a `_COLUMNAS_PROY` (`scripts/proyeccion_tramos.py`); emitir `'id_cobertura'` en `app_dash._titulos_a_dataframe_base` (rama i>0); pasar `id_cobertura` al `Titulo(...)` en `cargar_titulos_desde_snapshot`.
  - Verificar que el test ahora pasa (subyacentes separados en i>0) y que no se altera la reconstrucción RF ni la valoración.
  - _Bug_Condition: id_cobertura no sobrevive el round-trip en i>0_
  - _Expected_Behavior: Property 5 — "Subyacentes de Swaps" separado en todos los cortes_
  - _Preservation: reconstrucción RF y valoración inalteradas_
  - _Requirements: 4.1, 4bis_

- [x] 10. Defecto 5 — Retirar la advertencia estática del bug VM-USD
  - **Property 6: Sin advertencia estática de bug VM-USD**
  - Escribir test que FALLE hoy: el resumen por corte contiene la advertencia estática para "RF Internacional"/"Subyacentes de Swaps".
  - Corregir `tools/export_resumen_tramos.py`: retirar la columna `advertencia` (o dejarla neutra) y eliminar `_CATS_USD_EN_REVISION`; quitar el mapeo "Advertencia" en `tools/exportar_excel_tramos.py::_COLS_LEGIBLES`.
  - Verificar que el test ahora pasa (sin advertencia estática).
  - _Bug_Condition: advertencia estática residual (D2 cerrado como no reproducible)_
  - _Expected_Behavior: Property 6 — ninguna categoría lleva la advertencia_
  - _Preservation: resto del resumen por corte inalterado_
  - _Requirements: 5.1_

- [x] 11. Defecto 6 — Exportar la hoja RF_Detalle_Titulo por tramo
  - Escribir test del exportador: con `rf_detalle` sembrado en `informe_pyg_por_tramo`, verificar que se escribe una hoja `RF_Detalle_Titulo_<corte>` por corte con el detalle por título (sin recalcular).
  - Implementar en `tools/exportar_excel_tramos.py` una función análoga a `_anexar_series_mensuales_tramos` que lea la clave `"rf_detalle"` y escriba la(s) hoja(s), con nombre saneado y lectura RO; engancharla en `anexar_hojas_detalle_tramos`.
  - Verificar que no altera las hojas existentes (Portafolio_/ResumenPyG_/series mensuales).
  - _Expected_Behavior: Requirement 6.1 — detalle por título de RF en el Excel por tramos_
  - _Preservation: hojas existentes inalteradas_
  - _Requirements: 6.1_

- [x] 12. Defecto 7 — Discriminar RF Local vs Internacional en RF_Mensual
  - **Property 7: Discriminación local/intl aditiva**
  - Escribir test de equivalencia: `pyg_mes_local + pyg_mes_intl == pyg_mes` (y ori/total/vm análogas); las columnas de TOTAL existentes NO cambian (preservación de la hoja `RF_Mensual` de punta_a_punta).
  - Implementar en `scripts/rf_contable_mensual.py::construir_rf_mensual_contable` la partición por moneda (local = {"", COP, UVR}; internacional = resto) añadiendo columnas `*_local`/`*_intl` de forma ADITIVA, sin tocar las columnas de total existentes.
  - Verificar que la hoja `RF_Mensual` de punta_a_punta queda idéntica en sus columnas actuales y que `RF_Mensual_Tramos` ahora discrimina local/intl.
  - _Bug_Condition: no hay discriminación local/intl en RF_Mensual_
  - _Expected_Behavior: Property 7 — columnas local/intl aditivas, suma = total_
  - _Preservation: columnas de total existentes idénticas; punta_a_punta sin cambio_
  - _Requirements: 7.1, 7.2, 7bis_

- [x] 13. Checkpoint Fase 2 — Toda la suite (Fase 1 + Fase 2) pasa
  - Re-ejecutar la suite completa del spec (Fase 1 + los nuevos tests de los Defectos 3-7) y el arnés de equivalencia del motor RF.
  - Confirmar: fechado correcto del primer tramo parcial y de los tramos completos; subyacentes separados en todos los cortes; sin advertencia estática; hoja RF_Detalle_Titulo por tramo presente; RF_Mensual con discriminación local/intl; sin regresiones en punta_a_punta.
  - _Requirements: 3.1, 3.2, 3.3, 4.1, 5.1, 6.1, 7.1, 7.2_
