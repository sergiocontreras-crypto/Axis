# RV Identidad por id_posicion — Bugfix Design

## Overview

En el modo de proyección por tramos (multiactivo), el motor de Renta Variable (RV) y toda su cadena de encadenamiento, dedup y escritura de snapshot identifican cada posición por `titulo` (= `especie`). La especie NO es única (p. ej. 59 posiciones "AO ISA", 40 "PEI", 5 "CIBEST"), por lo que posiciones distintas se colapsan o confunden. El identificador único correcto es `id_posicion`, columna real de `portafolio_clasificado` que Renta Fija (RF) y Alternativos ya usan de extremo a extremo.

El fix sustituye la CLAVE de identidad de RV de `titulo`/`especie` por `id_posicion`, conservando `titulo`/`especie` como columna de etiqueta. La estrategia replica el patrón ya probado de Alternativos (`proyeccion_alternativos.py`), que emite `int(posicion["id_posicion"])` en cada fila y pasa `col_id='id_posicion'` a los helpers del modo por tramos. El cambio es mínimo y dirigido: tocar la carga, la emisión de filas, la escritura del snapshot (`_filas_cierre_no_rf`), la deduplicación de fuente única, y el encadenamiento/efecto moneda para que operen por `id_posicion`.

La corrección no cambia el diseño del snapshot: los literales de `clase_origen` `RENTA_VARIABLE`, `ALTERNATIVO` y `DERIVADO` se siguen escribiendo por diseño (Req 14.2). Solo cambia la identidad de posición de RV.

## Glossary

- **Bug_Condition (C)**: La clave de identidad usada por el flujo RV por tramos es `titulo`/`especie` (no única), de modo que dos o más posiciones distintas (`id_posicion` diferente) con la misma `especie` se colapsan o confunden.
- **Property (P)**: Comportamiento deseado — cada posición de RV se identifica por su `id_posicion` único; el snapshot produce una fila `RENTA_VARIABLE` por posición real, con `id_posicion` numérico real y `especie` poblada, sin duplicados ni `clase_origen` NULL espurias, y el encadenamiento usa el denominador propio de cada posición.
- **Preservation**: Comportamiento que NO debe cambiar — Alternativos/Derivados/RF, modo punta a punta, firma pública de `proyectar_renta_variable`, presencia de la columna de etiqueta `titulo`/`especie` en la salida, literales de `clase_origen` por diseño, y las suites de regresión del modo por tramos.
- **F**: Flujo RV por tramos actual (sin corregir), que usa `titulo`/`especie` como clave.
- **F'**: Flujo RV por tramos corregido, que usa `id_posicion` como clave.
- **id_posicion**: Columna numérica única de `portafolio_clasificado` que identifica cada posición. Patrón de referencia: Alternativos (`proyeccion_alternativos.py:290`, `int(posicion["id_posicion"])`).
- **cargar_portafolio_rv**: Función en `scripts/proyeccion_renta_variable.py` (~L113-143) que ejecuta el SELECT sobre `portafolio_clasificado` con `especie AS titulo` y SIN `id_posicion`.
- **proyectar_bloque**: Función en `scripts/proyeccion_renta_variable.py` (~L146-270) que emite las filas T0 y de cortes sin `id_posicion`.
- **_filas_cierre_no_rf**: Función en `app_dash.py` (~L8520) que arma las filas de cierre no-RF para el snapshot; recibe `col_id` (`'titulo'` para RV, `'id_posicion'` para Alt).
- **extraer_vm_cierre / calcular_efecto_moneda_tramo**: Helpers en `scripts/proyeccion_tramos_multiactivo.py` (~L1039, ~L1237) con `col_id` por defecto `'titulo'`.

## Bug Details

### Bug Condition

El bug se manifiesta cuando el flujo RV por tramos identifica una posición por `titulo`/`especie` en lugar de `id_posicion`. Como la especie no es única, el `groupby("titulo").idxmax()` colapsa N posiciones a 1, el writer del snapshot guarda la especie (string) en la columna `id_posicion`, la dedup de fuente única compara id numérico contra string (0 coincidencias) y el factor de encadenamiento se calcula sobre una base agregada por especie.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT:  X = conjunto de posiciones de RV de un corte que fluye por el modo por tramos
  OUTPUT: boolean

  RETURN EXISTS p1, p2 IN X TAL QUE
           p1.id_posicion <> p2.id_posicion
           AND p1.especie = p2.especie
           AND clave_identidad_usada_por_RV(X) = "titulo"
END FUNCTION
```

```
FUNCTION expectedBehavior(resultado, X)
  INPUT:  resultado = salida de F'(X); X = posiciones de RV del corte
  OUTPUT: boolean

  RETURN numero_filas_RENTA_VARIABLE(resultado) = numero_posiciones_RV(X)
         AND PARA TODA fila r EN resultado CON r.clase_origen = "RENTA_VARIABLE":
               es_numerico(r.id_posicion)
               AND r.id_posicion = id_posicion_real(r)
               AND r.especie <> None
         AND no_hay_duplicados_TITULO_vs_RENTA_VARIABLE(resultado)
         AND no_hay_filas_con_clase_origen_NULL_espurias(resultado)
END FUNCTION
```

### Examples

- **Colapso por especie**: Con decenas de posiciones por especie, el snapshot muestra siempre 41 filas `RENTA_VARIABLE` por corte (constante en 2026/2027/2028). Esperado: tantas filas como posiciones reales.
- **Identificador mal poblado**: Fila `('AO ISA', None, ...)` con `id_posicion='AO ISA'` (string) y `especie=None`. Esperado: `id_posicion=<entero real>`, `especie='AO ISA'`.
- **Dedup fallida y duplicación**: La comparación id numérico (fila `TITULO`) vs string de especie (fila `RENTA_VARIABLE`) da 0 coincidencias; cada acción aparece dos veces y surgen 421 filas con `clase_origen` NULL. Esperado: comparación numérica, sin duplicados, sin NULL espurias.
- **Inflación de P&G (edge case)**: Dos posiciones de misma especie con distinto VM inicial reciben el factor `VM_cierre_previo / VM_inicial_original` de una base agregada por especie, inflando el P&G de RV Local en 2028. Esperado: cada posición usa su propio denominador.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Alternativos, Derivados y Renta Fija continúan usando su propia clave de identidad sin cambio (Alternativos y RF ya usan `id_posicion`).
- El modo punta a punta (no por tramos) se comporta exactamente igual que antes del fix.
- La firma pública de `proyectar_renta_variable` (`scripts/proyeccion_renta_variable.py` ~L273-399) no añade parámetros requeridos nuevos; el contrato de firma de `tests/test_tramos_multiactivo_smoke.py:47-54` sigue pasando.
- La salida de RV sigue exponiendo la columna de etiqueta `titulo`/`especie` que consumen `tools/export_resumen_tramos.py` y `tools/multiescenarios.py`.
- El snapshot sigue escribiendo los literales de `clase_origen` `RENTA_VARIABLE`, `ALTERNATIVO` y `DERIVADO` por diseño (Req 14.2).
- `tools/exportar_excel_tramos.py` sigue volcando `portafolio_clasificado_proy` tal cual (se corrige automáticamente al corregir el writer).

**Scope:**
Todas las entradas que NO pertenecen al flujo RV por tramos deben quedar completamente inalteradas por el fix. Esto incluye:
- Posiciones de Alternativos, Derivados y Renta Fija en cualquier modo.
- La proyección en modo punta a punta.
- Los contratos públicos (firma de `proyectar_renta_variable`) y las columnas de etiqueta consumidas por exports.

**Note:** El comportamiento correcto esperado para las entradas buggy se define en la sección Correctness Properties (Property 1). Esta sección se concentra en lo que NO debe cambiar.

## Hypothesized Root Cause

La causa raíz es única: RV usa `titulo`/`especie` (no único) como identificador en lugar de `id_posicion`. Se manifiesta en cinco puntos del flujo:

1. **Carga sin id_posicion (`cargar_portafolio_rv`, `proyeccion_renta_variable.py` ~L119-128)**: El SELECT hace `especie AS titulo` y nunca selecciona `id_posicion`, así la identidad única no entra al flujo.

2. **Emisión de filas sin id_posicion (`proyectar_bloque` ~L219-230 y ~L253-264)**: Las filas T0 y de cortes se emiten con `titulo/compania/tipo_rv/moneda` sin columna `id_posicion`, a diferencia de Alternativos.

3. **Colapso y mala escritura en el snapshot (`_filas_cierre_no_rf`, `app_dash.py` ~L8520)**: Se invoca con `col_id='titulo'` → `groupby('titulo').idxmax()` colapsa N posiciones a 1 (41 filas) y escribe `id_posicion = str(especie)`, dejando `especie=None`.

4. **Dedup de fuente única rota (`app_dash.py` ~L8558-8572)**: Compara `id_posicion` numérico de filas `TITULO` contra el string de especie de filas `RENTA_VARIABLE` → 0 coincidencias, duplicados y 421 filas `clase_origen` NULL.

5. **Encadenamiento/efecto moneda por especie (`_aplicar_encadenamiento_resultado`/`_correr_rv_tramo` ~L7210-7247/~L7339-7397 con `col_id="titulo"`; dispatch ~L8151, `calcular_efecto_moneda_tramo` ~L8180, `extraer_vm_cierre` ~L8189 y sus defaults en `proyeccion_tramos_multiactivo.py` ~L1039/~L1091/~L1237)**: El factor `VM_cierre_previo / VM_inicial_original` se agrega por especie y se aplica a denominadores que no corresponden, inflando y componiendo el P&G por tramo.

El patrón correcto ya existe y está probado en Alternativos (`proyeccion_alternativos.py:268,290`), que usa `id_posicion` de extremo a extremo y llama a `_filas_cierre_no_rf(df_alt, 'id_posicion', ...)`.

## Correctness Properties

Property 1: Bug Condition — Identidad única de posición en el flujo RV por tramos

_For any_ conjunto de posiciones de RV donde la condición del bug se cumple (`isBugCondition` retorna true: existen dos posiciones con distinto `id_posicion` y la misma `especie`), el flujo RV por tramos corregido SHALL identificar cada posición por su `id_posicion` único, de modo que: (a) el número de filas `RENTA_VARIABLE` del snapshot iguala al número de posiciones reales de RV; (b) toda fila `RENTA_VARIABLE` tiene `id_posicion` numérico real y `especie` poblada (no `None`); (c) la deduplicación compara `id_posicion` numérico contra numérico sin dejar duplicados `TITULO` vs `RENTA_VARIABLE` ni filas `clase_origen` NULL espurias; y (d) el factor de encadenamiento se calcula y aplica por `id_posicion`, usando cada posición su propio denominador.

**Validates: Requirements 2.1, 2.4, 2.5, 2.6, 2.7**

Property 2: Preservation — Flujos no-RV y contratos existentes inalterados

_For any_ entrada donde la condición del bug NO se cumple (`isBugCondition` retorna false: posiciones de Alternativos/Derivados/RF, ejecución en modo punta a punta, o posiciones de RV sin especies repetidas), el flujo corregido SHALL producir el mismo resultado que el flujo original, preservando: la clave de identidad propia de Alternativos/RF, el comportamiento punta a punta, la firma pública de `proyectar_renta_variable`, la presencia de la columna de etiqueta `titulo`/`especie` en la salida de RV, y la escritura por diseño de los literales de `clase_origen`.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

Asumiendo que el análisis de causa raíz es correcto, los cambios replican el patrón de Alternativos (`id_posicion` de extremo a extremo) y conservan `titulo`/`especie` como etiqueta.

**File**: `scripts/proyeccion_renta_variable.py`

**Función**: `cargar_portafolio_rv`
1. **Incluir id_posicion en el SELECT**: Añadir `id_posicion` a la consulta (p. ej. `SELECT id_posicion, especie AS titulo, especie, compania, ...`), manteniendo el SQL de solo lectura y parametrizado, sin concatenación de input. Conservar `especie`/`titulo` como etiqueta.

**Función**: `proyectar_bloque`
2. **Emitir id_posicion en cada fila**: En las filas T0 (~L219-230) y de cortes (~L253-264) añadir `"id_posicion": int(posicion["id_posicion"])`, replicando `proyeccion_alternativos.py:290`. Mantener `titulo`/`especie`.

**Función**: `proyectar_renta_variable`
3. **Propagar la columna sin cambiar la firma pública**: `id_posicion` fluye por los sub-DataFrames (`rv_local`/`rv_intl`/`rv_pei`); NO añadir parámetros requeridos a la firma pública (Req 3.3).

**File**: `app_dash.py`

**Función**: `_filas_cierre_no_rf` (llamada para RV ~L8520)
4. **Agrupar/seleccionar por id_posicion**: Cambiar la llamada de RV de `_filas_cierre_no_rf(df_rv, 'titulo', _CLASE_ORIGEN_RV, fecha_corte)` a `col_id='id_posicion'`, produciendo una fila por posición real y escribiendo `id_posicion` numérico y `especie` poblada.

**Deduplicación de fuente única** (~L8558-8572)
5. **Comparar id numérico contra id numérico**: Al construir `_ids_no_rf` desde las filas RV, usar `id_posicion` numérico (ahora disponible), de modo que la máscara `isin` encuentre coincidencias, elimine las filas `TITULO` sin proyectar de posiciones RV ya proyectadas y no deje duplicados ni `clase_origen` NULL.

**Encadenamiento, dispatch y efecto moneda** (~L7210-7247, ~L7339-7397, ~L8151, ~L8180, ~L8189)
6. **Usar id_posicion como col_id en RV**: Cambiar `col_id="titulo"` por `col_id="id_posicion"` en el encadenamiento, el realineo del P&G de enero, la tabla de dispatch, `calcular_efecto_moneda_tramo` y `extraer_vm_cierre`, de modo que `EstadoEncadenado.vm_rv_por_posicion` y `EfectoMonedaTramo.id_posicion` queden indexados por `id_posicion`.

**File**: `scripts/proyeccion_tramos_multiactivo.py`

**Funciones**: `extraer_vm_cierre` (~L1039), `calcular_efecto_moneda_tramo` (~L1237)
7. **Parámetros de clave retrocompatibles**: Mantener `col_id` como parámetro con valor por defecto retrocompatible (sin defaults hardcodeados de negocio). RV pasa explícitamente `col_id="id_posicion"`.

## Testing Strategy

### Validation Approach

La estrategia sigue dos fases: primero, surgir contraejemplos que demuestren el bug en el código SIN corregir; luego, verificar que el fix funciona (fix checking) y que preserva el comportamiento no afectado (preservation checking). Dado que el flujo opera sobre DataFrames y SQLite, los tests construyen portafolios sintéticos con especies repetidas y distintos `id_posicion`.

### Exploratory Bug Condition Checking

**Goal**: Surgir contraejemplos que demuestren el bug ANTES de implementar el fix. Confirmar o refutar el análisis de causa raíz; si se refuta, re-hipotetizar.

**Test Plan**: Construir un portafolio de RV con varias posiciones de la misma especie (distinto `id_posicion` y distinto VM), ejecutar el flujo por tramos y aplicar las aserciones de `expectedBehavior`. Ejecutar sobre el código SIN corregir para observar los fallos.

**Test Cases**:
1. **Colapso por especie**: 3 posiciones "AO ISA" (ids 1001/1002/1003) → el snapshot debe tener 3 filas `RENTA_VARIABLE`, no 1 (fallará en el código sin corregir).
2. **id_posicion mal poblado**: Verificar que ninguna fila `RENTA_VARIABLE` tiene `id_posicion` string ni `especie=None` (fallará en el código sin corregir).
3. **Dedup fallida**: Verificar que no quedan filas `TITULO` sin proyectar junto a `RENTA_VARIABLE` para la misma posición, ni filas `clase_origen` NULL (fallará en el código sin corregir).
4. **Inflación de P&G (edge case)**: Dos posiciones misma especie, VM muy distinto, dos tramos → el P&G de RV Local no debe inflarse ni componerse (puede fallar en el código sin corregir).

**Expected Counterexamples**:
- 41 (u otra constante) filas `RENTA_VARIABLE` en vez de N posiciones reales.
- Filas con `id_posicion='AO ISA'` y `especie=None`.
- 0 coincidencias en la dedup, duplicados y filas `clase_origen` NULL.
- Posibles causas: SELECT sin `id_posicion`, emisión sin `id_posicion`, `groupby('titulo')`, `col_id='titulo'`.

### Fix Checking

**Goal**: Verificar que para toda entrada donde la condición del bug se cumple, el flujo corregido produce el comportamiento esperado.

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO
  resultado := flujo_rv_por_tramos_fixed(X)
  ASSERT numero_filas_RENTA_VARIABLE(resultado) = numero_posiciones_RV(X)
  ASSERT PARA TODA fila r CON r.clase_origen = "RENTA_VARIABLE":
           es_numerico(r.id_posicion) AND r.id_posicion = id_posicion_real(r) AND r.especie <> None
  ASSERT no_hay_duplicados_TITULO_vs_RENTA_VARIABLE(resultado)
  ASSERT no_hay_filas_con_clase_origen_NULL_espurias(resultado)
END FOR
```

### Preservation Checking

**Goal**: Verificar que para toda entrada donde la condición del bug NO se cumple, el flujo corregido produce el mismo resultado que el original.

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT flujo_original(X) = flujo_fixed(X)
END FOR
```

**Testing Approach**: El testing basado en propiedades es recomendable para preservation checking porque:
- Genera muchos casos automáticamente sobre el dominio de entrada (portafolios de Alternativos/Derivados/RF, modo punta a punta, RV sin especies repetidas).
- Captura casos borde que los tests unitarios podrían omitir.
- Da garantías fuertes de que el comportamiento es inalterado para todas las entradas no-buggy.

**Test Plan**: Observar el comportamiento sobre el código SIN corregir para Alternativos/Derivados/RF y modo punta a punta, y escribir tests de propiedad que capturen ese comportamiento.

**Test Cases**:
1. **Preservación Alternativos/Derivados/RF**: Observar que su clave de identidad y salida no cambian; test que verifica igualdad F vs F'.
2. **Preservación modo punta a punta**: Observar la salida punta a punta sobre código sin corregir; verificar que es idéntica tras el fix.
3. **Preservación de columna etiqueta**: Verificar que `titulo`/`especie` sigue presente en la salida de RV consumida por los exports.
4. **Preservación de firma pública**: Verificar que `proyectar_renta_variable` conserva su firma (contrato de `tests/test_tramos_multiactivo_smoke.py:47-54`).

### Unit Tests

- `cargar_portafolio_rv` incluye `id_posicion` numérico y conserva `titulo`/`especie`.
- `proyectar_bloque` emite `id_posicion` en filas T0 y de cortes.
- `_filas_cierre_no_rf` con `col_id='id_posicion'` produce una fila por posición real y escribe `id_posicion` numérico y `especie` poblada.
- Casos borde: una sola posición por especie; especie con N posiciones; VM = 0 filtrado por el SELECT.

### Property-Based Tests

- Generar portafolios de RV con especies repetidas y `id_posicion` distintos → verificar Property 1 (conteo de filas, id numérico, especie poblada, dedup sin duplicados/NULL).
- Generar portafolios de Alternativos/Derivados/RF y ejecuciones punta a punta → verificar Property 2 (F = F').
- Generar pares de posiciones misma especie, distinto VM, varios tramos → verificar que el factor de encadenamiento usa el denominador propio (sin inflación).

### Integration Tests

- Flujo por tramos completo con especies repetidas en los tres tramos (2026/2027/2028) → el conteo de filas `RENTA_VARIABLE` iguala las posiciones reales en cada corte.
- Encadenamiento entre tramos con posiciones de misma especie → el P&G de RV Local no se infla ni se compone.
- Suites de regresión del modo por tramos (`tests/test_tramos_multiactivo_integracion.py`, `test_tramos_multiactivo_orquestador.py`, `test_tramos_multiactivo_smoke.py`) → siguen pasando, actualizando en lockstep las llamadas RV de la factory de `col_id="titulo"` a `col_id="id_posicion"`.
