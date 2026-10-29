# Implementation Plan

## Overview

Plan de implementación del bugfix de identidad de posición de RV. La causa raíz única es que el flujo RV por tramos usa `titulo`/`especie` (no único) como clave en vez de `id_posicion`. El plan sigue la metodología exploratoria: primero un test de exploración de la condición del bug (debe FALLAR en el código sin corregir), luego tests de preservación observation-first (deben PASAR en el código sin corregir), después el fix dirigido que replica el patrón de Alternativos (`id_posicion` de extremo a extremo), y finalmente la verificación de que ambas propiedades se cumplen sin regresiones.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1", "2"],
      "description": "Tests escritos antes del fix: exploración (Property 1, debe fallar) y preservación (Property 2, debe pasar) sobre el código sin corregir. Independientes entre sí."
    },
    {
      "wave": 2,
      "tasks": ["3.1", "3.2"],
      "description": "Implementación del fix: incluir id_posicion en carga/emisión (3.1) y corregir snapshot/dedup/encadenamiento por id_posicion (3.2). Dependen de las waves 1."
    },
    {
      "wave": 3,
      "tasks": ["3.3", "3.4"],
      "description": "Verificación post-fix: Property 1 ahora pasa (3.3) y Property 2 sigue pasando (3.4). Dependen de 3.1 y 3.2."
    },
    {
      "wave": 4,
      "tasks": ["4"],
      "description": "Checkpoint final: toda la suite pasa."
    }
  ]
}
```

## Tasks

- [x] 1. Escribir test de exploración de la condición del bug (ANTES de implementar el fix)
  - **Property 1: Bug Condition** - Identidad única de posición en el flujo RV por tramos
  - **CRITICAL**: Este test DEBE FALLAR en el código sin corregir — el fallo confirma que el bug existe
  - **DO NOT attempt to fix the test or the code when it fails** — el fallo es el resultado esperado en esta fase
  - **NOTE**: Este test codifica el comportamiento esperado; validará el fix cuando pase tras la implementación
  - **GOAL**: Surgir contraejemplos que demuestren el bug (confirmar/refutar la causa raíz; si se refuta, re-hipotetizar)
  - **Scoped PBT Approach**: Para este bug determinista, acotar la propiedad a casos concretos reproducibles: construir un portafolio sintético de RV con varias posiciones de la MISMA especie y distinto `id_posicion` y VM (p. ej. 3 posiciones "AO ISA" con ids 1001/1002/1003)
  - Implementar el test a partir de `isBugCondition(X)` del design: existen `p1, p2` con `p1.id_posicion <> p2.id_posicion AND p1.especie = p2.especie` y la clave usada por RV es `titulo`
  - Las aserciones deben coincidir con `expectedBehavior` del design:
    - `numero_filas_RENTA_VARIABLE(resultado) = numero_posiciones_RV(X)` (3 filas, no 1 colapsada)
    - Para toda fila `clase_origen='RENTA_VARIABLE'`: `id_posicion` es numérico real y `especie <> None` (no `id_posicion='AO ISA'` ni `especie=None`)
    - `no_hay_duplicados_TITULO_vs_RENTA_VARIABLE(resultado)` (dedup compara numérico vs numérico)
    - `no_hay_filas_con_clase_origen_NULL_espurias(resultado)` (sin las 421 filas NULL)
  - Edge case de inflación: dos posiciones de misma especie con VM muy distinto a lo largo de dos tramos → el P&G de RV Local no debe inflarse ni componerse
  - Ejecutar el test sobre el código SIN corregir
  - **EXPECTED OUTCOME**: El test FALLA (esto es correcto — demuestra que el bug existe)
  - Documentar los contraejemplos encontrados (p. ej. "41 filas `RENTA_VARIABLE` en vez de N", "fila con `id_posicion='AO ISA'`, `especie=None`", "0 coincidencias en la dedup y filas `clase_origen` NULL")
  - Marcar la tarea completa cuando el test esté escrito, ejecutado y el fallo documentado
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_

- [x] 2. Escribir tests de preservación basados en propiedades (ANTES de implementar el fix)
  - **Property 2: Preservation** - Flujos no-RV y contratos existentes inalterados
  - **IMPORTANT**: Seguir la metodología observation-first — primero observar el comportamiento del código SIN corregir y registrarlo, luego escribir las propiedades que lo asertan
  - Observar en código SIN corregir (casos donde `isBugCondition` retorna false):
    - Posiciones de Alternativos/Derivados/RF: su clave de identidad y salida — registrar `F(X)`
    - Modo punta a punta (no por tramos): registrar la salida exacta
    - RV sin especies repetidas: comportamiento actual
  - Escribir tests de propiedad que capturen esos patrones observados (de Preservation Requirements del design):
    - Para toda entrada no-buggy: `flujo_original(X) = flujo_fixed(X)` (igualdad F vs F')
    - La salida de RV sigue exponiendo la columna de etiqueta `titulo`/`especie` (consumida por `tools/export_resumen_tramos.py` y `tools/multiescenarios.py`)
    - La firma pública de `proyectar_renta_variable` no cambia (contrato de `tests/test_tramos_multiactivo_smoke.py:47-54`)
    - El snapshot sigue escribiendo los literales de `clase_origen` `RENTA_VARIABLE`/`ALTERNATIVO`/`DERIVADO` por diseño (Req 14.2)
  - El testing basado en propiedades genera muchos casos sobre el dominio no-buggy para garantías fuertes
  - Ejecutar los tests sobre el código SIN corregir
  - **EXPECTED OUTCOME**: Los tests PASAN (confirma la línea base de comportamiento a preservar)
  - Marcar la tarea completa cuando los tests estén escritos, ejecutados y pasando en el código sin corregir
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Fix para la identidad de posición de RV (usar `id_posicion` en lugar de `titulo`/`especie`)

  - [x] 3.1 Incluir `id_posicion` en la carga y emisión de filas de RV
    - En `cargar_portafolio_rv` (`scripts/proyeccion_renta_variable.py` ~L113-143): añadir `id_posicion` al SELECT sobre `portafolio_clasificado` (p. ej. `SELECT id_posicion, especie AS titulo, especie, compania, ...`), manteniendo el SQL de solo lectura y parametrizado (sin concatenación de input), y conservar `especie`/`titulo` como etiqueta
    - En `proyectar_bloque` (filas T0 ~L219-230 y de cortes ~L253-264): añadir `"id_posicion": int(posicion["id_posicion"])`, replicando el patrón de Alternativos (`proyeccion_alternativos.py:290`), conservando `titulo`/`especie`
    - En `proyectar_renta_variable` (~L273-399): propagar la columna `id_posicion` por los sub-DataFrames (`rv_local`/`rv_intl`/`rv_pei`) SIN añadir parámetros requeridos a la firma pública
    - _Bug_Condition: isBugCondition(X) — clave de identidad de RV = "titulo" con especies no únicas (from design)_
    - _Expected_Behavior: expectedBehavior(resultado, X) — id_posicion numérico real por fila, especie poblada (from design)_
    - _Preservation: Preservation Requirements — firma pública intacta, columna etiqueta `titulo`/`especie` presente (from design)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.3_

  - [x] 3.2 Corregir el snapshot y la deduplicación de fuente única para operar por `id_posicion`
    - En `_filas_cierre_no_rf` (llamada para RV en `app_dash.py` ~L8520): cambiar la llamada de `col_id='titulo'` a `col_id='id_posicion'`, produciendo una fila `RENTA_VARIABLE` por posición real, con `id_posicion` numérico y `especie` poblada (no `None`)
    - En la deduplicación de fuente única (~L8558-8572): construir `_ids_no_rf` desde las filas RV usando el `id_posicion` numérico, de modo que la máscara `isin` compare numérico contra numérico, elimine las filas `TITULO` sin proyectar de posiciones RV ya proyectadas y no deje duplicados ni filas `clase_origen` NULL espurias
    - En encadenamiento/dispatch/efecto moneda (`_aplicar_encadenamiento_resultado`/`_correr_rv_tramo` ~L7210-7247/~L7339-7397, dispatch ~L8151, `calcular_efecto_moneda_tramo` ~L8180, `extraer_vm_cierre` ~L8189): cambiar `col_id="titulo"` por `col_id="id_posicion"`, de modo que `EstadoEncadenado.vm_rv_por_posicion` y `EfectoMonedaTramo.id_posicion` queden indexados por `id_posicion` y el factor `VM_cierre_previo / VM_inicial_original` use el denominador propio de cada posición
    - En `scripts/proyeccion_tramos_multiactivo.py` (`extraer_vm_cierre` ~L1039, `calcular_efecto_moneda_tramo` ~L1237): mantener `col_id` como parámetro con valor por defecto retrocompatible (sin defaults hardcodeados de negocio); RV pasa explícitamente `col_id="id_posicion"`
    - Mantener logs genéricos sin datos sensibles
    - _Bug_Condition: isBugCondition(X) — groupby("titulo").idxmax() colapsa y dedup compara id numérico vs string (from design)_
    - _Expected_Behavior: expectedBehavior(resultado, X) — una fila por posición, dedup numérica, sin NULL espurias, denominador propio por id_posicion (from design)_
    - _Preservation: Preservation Requirements — literales de clase_origen por diseño (Req 14.2); Alt/RF/Derivados sin cambio (from design)_
    - _Requirements: 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.1, 3.5, 3.6, 3.7, 3.8_

  - [x] 3.3 Verificar que el test de exploración de la condición del bug ahora pasa
    - **Property 1: Expected Behavior** - Identidad única de posición en el flujo RV por tramos
    - **IMPORTANT**: Re-ejecutar el MISMO test de la tarea 1 — NO escribir un test nuevo
    - El test de la tarea 1 codifica el comportamiento esperado; cuando pasa, confirma que el comportamiento esperado se satisface
    - Ejecutar el test de exploración de la condición del bug del paso 1
    - **EXPECTED OUTCOME**: El test PASA (confirma que el bug está corregido: N filas por posición, `id_posicion` numérico, `especie` poblada, dedup numérica, sin inflación)
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.7_

  - [x] 3.4 Verificar que los tests de preservación siguen pasando
    - **Property 2: Preservation** - Flujos no-RV y contratos existentes inalterados
    - **IMPORTANT**: Re-ejecutar los MISMOS tests de la tarea 2 — NO escribir tests nuevos
    - Ejecutar los tests de propiedad de preservación del paso 2
    - **EXPECTED OUTCOME**: Los tests PASAN (confirma que no hay regresiones)
    - Actualizar en lockstep las suites de regresión del modo por tramos que usan la factory RV (`tests/test_tramos_multiactivo_integracion.py` factory `_doble_no_rf_factory` ~L247,316,395,442 y asserts `vm_rv_por_posicion` ~L296,330; `tests/test_tramos_multiactivo_orquestador.py` ~L319,328; `tests/test_tramos_multiactivo_smoke.py` ~L47-54) de `col_id="titulo"` a `col_id="id_posicion"` sin romper los contratos existentes
    - Confirmar que todos los tests siguen pasando tras el fix (sin regresiones)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 4. Checkpoint - Asegurar que todos los tests pasan
  - Ejecutar la suite completa de tests del modo por tramos de RV (exploración, preservación, unitarios, propiedad e integración)
  - Confirmar que el test de la Property 1 pasa (bug corregido) y los de la Property 2 pasan (sin regresiones)
  - Asegurar que todos los tests pasan; preguntar al usuario si surgen dudas

## Notes

- **Property 1 (Bug Condition)**: la tarea 1 escribe el test que DEBE FALLAR en el código sin corregir y, tras el fix, DEBE PASAR (tarea 3.3). No reescribir el test tras el fix; es el mismo.
- **Property 2 (Preservation)**: la tarea 2 sigue la metodología observation-first (observar `F(X)` en el código sin corregir y asertarlo); DEBE PASAR antes y después del fix (tarea 3.4).
- **Patrón de referencia**: el fix replica el flujo de Alternativos (`proyeccion_alternativos.py:268,290`), que usa `id_posicion` de extremo a extremo. `titulo`/`especie` se conserva como columna de etiqueta.
- **Reglas del proyecto**: SQL de solo lectura y parametrizado (sin concatenación de input), nuevos parámetros de clave con valor por defecto retrocompatible (sin defaults hardcodeados de negocio), logs genéricos sin datos sensibles, y firma pública de `proyectar_renta_variable` intacta.
- **Sin cambio de diseño del snapshot**: los literales de `clase_origen` `RENTA_VARIABLE`/`ALTERNATIVO`/`DERIVADO` se siguen escribiendo por diseño (Req 14.2).
