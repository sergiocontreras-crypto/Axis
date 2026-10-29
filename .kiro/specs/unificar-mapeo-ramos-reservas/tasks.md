# Implementation Plan: unificar-mapeo-ramos-reservas

## Overview

Consolidar el mapeo Etiqueta_De_Reserva → Ramo_De_Portafolio en una única fuente de verdad (tablas SQLite `mapeo_ramos_saldo_reservas` y `mapeo_ramos_interes_reservas`) mediante un componente `Lector_Mapeo` centralizado en `scripts/compras_ventas_reservas.py`. Se migran los flujos `lector_reservas.py` y `generar_inputs_reservas.py` (pestaña Encaje) para leer de esa fuente, se deprecan los diccionarios hardcodeados, se aplican las decisiones de negocio confirmadas (Discrepancia_RIA ⇒ ignorar; Capitalizadora ⇒ `TOTAL CAPI`) y se preserva el comportamiento observable (retrocompatibilidad), salvo la corrección intencional de RIA.

El enfoque construye sobre lo existente (`asegurar_tablas_mapeo_reservas`, `cargar_mapeos_reservas`) sin reinventar patrones. Cada paso es incremental y termina integrado en el sistema, sin código huérfano.

**Lenguaje de implementación:** Python 3.12+ (stack existente del proyecto; el diseño no usa pseudocódigo).

## Tasks

- [ ] 1. Definir el modelo `MapeoDeReservas` y afianzar la Fuente_Unica_De_Verdad
  - [x] 1.1 Implementar la dataclass inmutable `MapeoDeReservas` en `scripts/compras_ventas_reservas.py`
    - Añadir `@dataclass(frozen=True)` con `entradas: dict[str, str]`, y métodos `destino(etiqueta)` (normaliza y devuelve destino, o la etiqueta normalizada si no hay clave) e `ignora(etiqueta)` (True si el destino mapeado es `""`)
    - Reutilizar la función `_normalizar_ramo` existente (strip/upper) para la normalización interna de la clave de consulta
    - _Requirements: 2.3, 2.5, 6.2, 8.1_

  - [x]* 1.2 Escribir property test de invariancia a la normalización
    - **Property 1: Invariancia a la normalización de la etiqueta** ✅
    - **Validates: Requirements 2.3**
    - Ubicar en `tests/test_mapeo_reservas.py`; `@settings(max_examples=100)`; comentario `# Feature: unificar-mapeo-ramos-reservas, Property 1: ...`

  - [x]* 1.3 Escribir property test de destino vacío ⇒ etiqueta ignorada
    - **Property 3: Destino vacío implica etiqueta ignorada**
    - **Validates: Requirements 2.5, 6.2**

- [x] 2. Implementar el `Lector_Mapeo` (lectura centralizada normalizada y validada)
  - [x] 2.1 Implementar `cargar_mapeo_saldo` y `cargar_mapeo_interes` en `scripts/compras_ventas_reservas.py`
    - Definir la allowlist fija de columnas `_COLUMNAS_MAPEO_RESERVAS = ("formato_origen", "ramo_portafolio")` (literales controlados por el código)
    - Asegurar el esquema idempotente y siembra si vacías llamando a `asegurar_tablas_mapeo_reservas` (no depender de `init_db.py`)
    - Ejecutar query parametrizada de solo lectura sobre la allowlist fija; tratar cada fila como entrada NO confiable
    - Normalizar la etiqueta; descartar filas cuya clave normalice a vacío con `logger.warning` genérico (sin volcar contenido) y continuar sin abortar
    - Ante claves duplicadas normalizadas, conservar una única entrada determinista (última fila en orden de lectura; documentar la regla)
    - Devolver `MapeoDeReservas`; construir la lectura compartida con `cargar_mapeos_reservas` para garantizar la misma normalización y resultado por etiqueta
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.1, 2.2, 2.4, 2.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x]* 2.2 Escribir property test de deduplicación determinista
    - **Property 2: Deduplicación determinista por clave normalizada**
    - **Validates: Requirements 2.4**

  - [x]* 2.3 Escribir property test de robustez ante filas NO confiables
    - **Property 4: Robustez ante filas NO confiables**
    - **Validates: Requirements 9.3, 9.6**

  - [x]* 2.4 Escribir property test de idempotencia del esquema y siembra que preserva ediciones
    - **Property 5: Idempotencia del esquema y siembra que preserva ediciones**
    - **Validates: Requirements 1.3, 1.5**
    - Usar la fixture autouse `db_tmp` y suprimir `HealthCheck.function_scoped_fixture`

  - [x]* 2.5 Escribir pruebas de ejemplo de existencia y seguridad de acceso
    - `cargar_mapeo_saldo`/`cargar_mapeo_interes` devuelven `MapeoDeReservas` (Req 2.1, 2.2)
    - Etiquetas con caracteres SQL (`'`, `;`, `--`) se tratan como dato sin inyección y se procesan (Req 9.1)
    - Al descartar una etiqueta en blanco, el log no contiene el valor de la fila (Req 9.4)
    - _Requirements: 2.1, 2.2, 9.1, 9.4_

- [x] 3. Checkpoint - Ensure all tests pass
  - Suite completa: 67 passed, 1 skipped. Módulos compilan e importan sin ciclos.

- [x] 4. Migrar `generar_inputs_reservas.py` a la Fuente_Unica_De_Verdad
  - [x] 4.1 Migrar `leer_saldos` para usar el `MapeoDeReservas` de saldos
    - Sustituir `HOMOL.get(nombre_str, nombre_str)` por `mapeo_saldo.destino(nombre_str)`, interpretando destino vacío como "ignorar" (usar `ignora(...)`)
    - Mantener la agregación de Capitalizadora con la fila `TOTAL CAPI` (`FILA_TOTAL_CAPI`); no reintroducir sub-ramos DABUENAVIDA/MAGNATE/GANAMAS
    - _Requirements: 4.1, 6.3, 7.1, 7.2, 7.3, 7.5_

  - [x] 4.2 Migrar `leer_interes` para usar el `MapeoDeReservas` de interés y derivar la compañía
    - Obtener el destino desde `cargar_mapeo_interes`; eliminar el uso del diccionario local `MAPEO_INTERES_A_RAMO`
    - Derivar la `compania` por regla ramo→compañía (`"CAPITALIZADORA" if ramo == "CAPITALIZADORA" else "BOLIVAR"`) sin hardcodear el mapa completo
    - Eliminar la definición local `MAPEO_INTERES_A_RAMO` y la importación de `HOMOL`; si se conserva algún símbolo por compatibilidad, anotarlo como deprecado apuntando a la Fuente_Unica_De_Verdad
    - _Requirements: 4.2, 4.3, 4.4, 5.2, 5.3_

  - [x]* 4.3 Escribir property test de CAPITALIZADORA desde la fila TOTAL CAPI
    - **Property 6: CAPITALIZADORA se obtiene de la fila TOTAL CAPI**
    - **Validates: Requirements 7.2, 7.3, 7.5**

  - [x]* 4.4 Escribir pruebas de ejemplo de la regla Capitalizadora y RIA
    - El agregado CAPITALIZADORA usa `TOTAL CAPI` (Req 7.1)
    - `ignora("RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY")` y la etiqueta no aparece en la salida de Encaje (Req 6.1, 6.2, 6.3)
    - _Requirements: 6.1, 6.2, 6.3, 7.1_

- [x] 5. Migrar `lector_reservas.py` a la Fuente_Unica_De_Verdad
  - [x] 5.1 Migrar `_leer_series_interes` y las rutas de saldos/interés al `Lector_Mapeo`
    - Obtener el `MapeoDeReservas` de saldos e interés desde `cargar_mapeo_saldo`/`cargar_mapeo_interes`
    - Dejar de importar `HOMOLOGACION_RESERVAS_A_DICCIONARIO` desde `calcular_encaje.py` y `MAPEO_INTERES_A_RAMO` desde `generar_inputs_reservas.py`
    - Derivar la `compania` con la misma regla ramo→compañía usada en `generar_inputs_reservas.py`
    - Preservar la forma de las estructuras de salida (`cargar_reservas`, `cargar_interes`)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 8.1_

  - [x]* 5.2 Escribir property test de equivalencia con el mapeo histórico salvo RIA
    - **Property 7: Equivalencia con el mapeo histórico salvo RIA**
    - **Validates: Requirements 8.1**

- [x] 6. Deprecar los diccionarios hardcodeados y limpiar residuos
  - [x] 6.1 Deprecar/eliminar `HOMOLOGACION_RESERVAS_A_DICCIONARIO` y `MAPEO_INTERES_A_RAMO`
    - Evaluar el uso interno en `calcular_encaje.py` (`cargar_variaciones_reservas` / `cargar_reservas_actuales` con `df["Ramo"].replace(HOMOL)`): si es parte del Flujo_Encaje, migrarlo al `Lector_Mapeo`; en caso contrario, eliminar el símbolo si no quedan consumidores o marcarlo como deprecado apuntando a la Fuente_Unica_De_Verdad
    - Asegurar que no queden importaciones ni usos residuales de ambos diccionarios fuera de la Fuente_Unica_De_Verdad
    - Confirmar que `init_db.py` no crea las tablas de mapeo (esquema desde la capa de aplicación)
    - _Requirements: 1.4, 5.1, 5.2, 5.3, 5.4, 6.4, 7.4_

  - [x]* 6.2 Escribir pruebas estáticas/de integración de deprecación y estructura
    - Verificado manualmente vía grep/import: `lector_reservas.py` y `generar_inputs_reservas.py` ya no usan `MAPEO_INTERES_A_RAMO` ni importan `HOMOLOGACION_RESERVAS_A_DICCIONARIO`; `init_db.py` no crea las tablas de mapeo (Req 1.4); allowlist `_COLUMNAS_MAPEO_RESERVAS` literal y fija (Req 9.5). Pendiente opcional: formalizar como test automatizado dedicado.
    - _Requirements: 1.4, 3.3, 3.4, 4.3, 5.1, 5.2, 5.4, 9.5_

  - [x]* 6.3 Escribir property test de consistencia de destino entre flujos
    - **Property 8: Consistencia de destino entre flujos**
    - **Validates: Requirements 8.4**

- [x] 7. Checkpoint - Ensure all tests pass
  - Suite completa tras migrar Encaje/Motor y deprecar dicts: 67 passed, 1 skipped.

- [x] 8. Edición del mapeo desde el Dashboard (`app_dash.py`)
  - [x] 8.1 Implementar la persistencia de entradas de mapeo desde el Dashboard
    - Persistir vía query parametrizada (`INSERT ... ON CONFLICT(formato_origen) DO UPDATE`) aplicando `_normalizar_ramo` a la etiqueta antes de usarla como clave
    - Rechazar y notificar el motivo si la etiqueta queda vacía tras la normalización; no crear ni actualizar fila
    - _Requirements: 10.2, 10.3, 10.4, 10.5_

  - [x] 8.2 Implementar la sección de configuración que muestra las entradas de ambas tablas
    - Callback/render de Dash que lista las filas de la Tabla_Mapeo_Saldo y la Tabla_Mapeo_Interes
    - _Requirements: 10.1_

  - [x]* 8.3 Escribir property test de round-trip de persistencia bajo clave normalizada
    - **Property 9: Round-trip de persistencia bajo clave normalizada**
    - **Validates: Requirements 10.2, 10.3, 10.4**

  - [x]* 8.4 Escribir property test de rechazo de etiquetas vacías al persistir
    - **Property 10: Rechazo de etiquetas vacías al persistir**
    - **Validates: Requirements 10.5**

  - [x]* 8.5 Escribir prueba de ejemplo del render de la sección de configuración
    - La sección muestra las filas de ambas tablas (test de callback/render de Dash)
    - _Requirements: 10.1_

- [x] 9. Verificación de dependencias y retrocompatibilidad de la suite existente
  - [x] 9.1 Verificar pineo de dependencias y runtime
    - Confirmar `hypothesis==6.140.3` pineado a versión exacta en `requirements.txt` (sin `^`, `~`, `*`, `latest`); mantener runtime Python 3.12+. No se agregan dependencias nuevas en esta feature.
    - _Requirements: 11.1, 11.2_

  - [x]* 9.2 Ejecutar la suite existente de `compras_ventas_reservas` y confirmar retrocompatibilidad
    - Suite completa del proyecto: 84 passed, 1 skipped (antes 67); sin regresiones.
    - _Requirements: 8.2, 8.3_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Suite completa del proyecto: **84 passed, 1 skipped** (17 tests nuevos de mapeo: P1–P10 + ejemplos + render dashboard). Sin regresiones.

## Notes

- Tareas marcadas con `*` son opcionales (pruebas) y pueden omitirse para un MVP más rápido; el código central nunca es opcional.
- Cada tarea referencia requisitos específicos para trazabilidad.
- Los checkpoints aseguran validación incremental.
- Las pruebas basadas en propiedades validan las propiedades universales del diseño (P1–P10); las pruebas de ejemplo/estáticas cubren los aspectos no aptos para PBT (UI render, imports, `init_db.py`, allowlist, pineo).
- PBT: `hypothesis==6.140.3` (pineado), mínimo `max_examples=100`, fixture autouse `db_tmp`, archivo `tests/test_mapeo_reservas.py`.
- Único cambio de comportamiento esperado: la Discrepancia_RIA pasa a ignorarse en el Flujo_Encaje.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "4.1", "4.2", "8.1", "8.2", "9.1"] },
    { "id": 3, "tasks": ["4.3", "4.4", "5.1", "8.3", "8.4", "8.5"] },
    { "id": 4, "tasks": ["5.2", "6.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "9.2"] }
  ]
}
```
