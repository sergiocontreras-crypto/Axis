# Implementation Plan: proyeccion-por-tramos

## Overview

Agregar un modo de proyección OPCIONAL "por tramos" que divide el horizonte en tramos por
fin de año y ENCADENA corridas anuales (el cierre de un tramo es el t0 del siguiente),
persistiendo cada snapshot en la nueva tabla `portafolio_clasificado_proy`. El modo actual
"punta a punta" permanece por defecto y 100% retrocompatible. La periodicidad del input de
reservas puede ser mensual (default) o anual por tramo (prorrateada), afectando SOLO el
mapa de variaciones de reserva y NUNCA el paso mensual del motor.

La nueva lógica de orquestación/helpers vive en `scripts/proyeccion_tramos.py` y en la capa
de aplicación (`app_dash.py`). El `Motor_Proyeccion` recibe un único cambio mínimo y
retrocompatible: un argumento opcional `mapa_reservas=None`. No se modifica `init_db.py`.

**Lenguaje de implementación:** Python 3.12+ (stack existente; el diseño no usa pseudocódigo).

## Tasks

- [x] 1. Trocear el horizonte en tramos (`calcular_tramos`)
  - [x] 1.1 Crear `scripts/proyeccion_tramos.py` con `Tramo`, `HorizonteInvalidoError` y `calcular_tramos`
    - Definir `@dataclass(frozen=True) class Tramo` con `indice`, `fecha_inicio`, `fecha_corte`, `sub_horizonte`
    - `calcular_tramos(fecha_inicio, fecha_fin) -> list[Tramo]`: cortes intermedios = 31-dic de cada año contenido; último corte = `fecha_fin`; `fecha_inicio` de cada tramo = día siguiente al corte previo; `sub_horizonte` con la misma fórmula del orquestador actual (`delta.years*12 + delta.months + (1 si delta.days>0)`); orden cronológico ascendente
    - Lanzar `HorizonteInvalidoError` si el horizonte total < 1 mes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.5_

  - [x]* 1.2 Property test — cobertura del horizonte sin solapamiento
    - **Property 2: Cobertura del horizonte sin solapamiento**
    - **Validates: Requirements 2.1, 2.2, 2.3, 4.5**
    - `tests/test_proyeccion_tramos.py`; `@settings(max_examples=100)`; comentario `# Feature: proyeccion-por-tramos, Property 2: ...`

  - [x]* 1.3 Property test — un solo tramo si el horizonte cabe en un año
    - **Property 3: Un solo tramo si el horizonte cabe en un año**
    - **Validates: Requirements 2.4**

  - [x]* 1.4 Property test — rechazo de horizonte < 1 mes
    - **Property 4: Rechazo de horizonte < 1 mes**
    - **Validates: Requirements 2.5**

- [x] 2. Prorrateo de la variación anual de reserva (`prorratear_reservas_anual`)
  - [x] 2.1 Implementar `prorratear_reservas_anual` en `scripts/proyeccion_tramos.py`
    - Firma `(variacion_anual_por_ramo: dict[str, float], sub_horizonte: int, tolerancia: float = 1e-6) -> dict[str, dict[int, float]]`
    - Reparto lineal sobre meses `1..sub_horizonte`; acumular el residuo de redondeo en el último mes para preservar la suma exacta
    - Omitir ramos con variación 0 o sin dato (el motor asume Δ=0 para lo ausente)
    - Entrada NO confiable: normalizar ramo (strip/upper), coercionar delta a float finito; descartar filas inválidas con `logger.warning` genérico (sin volcar contenido)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 12.3, 12.4_

  - [x]* 2.2 Property test — suma de deltas prorrateados = variación anual
    - **Property 5: Suma de deltas prorrateados = variación anual**
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 2.3 Property test — formato y dominio del mapa prorrateado
    - **Property 6: Formato y dominio del mapa prorrateado**
    - **Validates: Requirements 8.3, 8.4**

  - [x]* 2.4 Property test — reparto uniforme entre meses
    - **Property 7: Reparto lineal ≈ uniforme entre meses**
    - **Validates: Requirements 8.1**

- [x] 3. Persistencia de snapshots (`portafolio_clasificado_proy`)
  - [x] 3.1 Implementar el esquema idempotente y las operaciones de snapshot
    - `_COLUMNAS_PROY`: allowlist fija (columnas de `portafolio_clasificado` + `fecha_corte_proyeccion`)
    - `asegurar_tabla_portafolio_proy(conn)`: `CREATE TABLE IF NOT EXISTS` + índice por `fecha_corte_proyeccion`; NO siembra; NO se invoca desde `init_db.py`
    - `persistir_snapshot_proy(conn, df, fecha_corte) -> int`: escribe solo columnas de la allowlist (ausentes → NULL, extra → descartadas); `executemany` parametrizado; valida VM/nominal a float finito (descarta fila no coercionable con log genérico); devuelve nº de filas
    - `limpiar_snapshots_proy(conn)`: `DELETE FROM portafolio_clasificado_proy` (sin entrada externa) para reemplazar la corrida previa
    - `cargar_snapshot_proy(conn, fecha_corte) -> pd.DataFrame`: `SELECT` de la allowlist `WHERE fecha_corte_proyeccion = ?` (parametrizado); lanza `SnapshotAusenteError` si no hay filas
    - Definir `SnapshotAusenteError`
    - _Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 6.3, 11.2, 12.1, 12.2, 12.3, 12.4_

  - [x]* 3.2 Property test — idempotencia del esquema
    - **Property 12: Idempotencia del esquema**
    - **Validates: Requirements 5.1**

  - [x]* 3.3 Property test — reemplazo de corrida previa
    - **Property 11: Reemplazo de corrida previa**
    - **Validates: Requirements 5.6**

  - [x]* 3.4 Test de ejemplo — seguridad SQL con valores hostiles
    - **Property 13: Seguridad de acceso a datos** (test de ejemplo, no property)
    - **Validates: Requirements 12.1, 12.2, 12.5**
    - Insertar `ramo`/`fecha_corte` con comillas y `;DROP TABLE`; verificar que se tratan como datos

  - [x]* 3.5 Test de ejemplo — snapshot ausente lanza `SnapshotAusenteError`
    - _Requirements: 11.2_

- [x] 4. Checkpoint - Ensure all tests pass
  - Suite de tramos: 12 passed. Helpers de troceo/prorrateo/persistencia en verde. Los property tests detectaron y corrigieron una suposición de test ante colisiones de normalización de ramos.

- [x] 5. Motor: argumento opcional `mapa_reservas` (cambio mínimo retrocompatible)
  - [x] 5.1 Añadir `mapa_reservas: dict[str, dict[int, float]] | None = None` a `proyectar_portafolio`
    - Si `mapa_reservas is None`: llamar a `cargar_mapa_reservas(...)` EXACTAMENTE como hoy (retrocompatibilidad total)
    - Si se pasa: usarlo directo sin abrir la DB
    - NO cambiar ninguna otra lógica del motor (cupones, vencimientos, reinversión, re-valoración, paso mensual)
    - _Requirements: 1.3, 1.5, 9.1, 9.2, 9.3_

  - [x]* 5.2 Test de ejemplo — retrocompatibilidad del motor con `mapa_reservas=None`
    - Confirmar que con `None` el motor produce el mismo resultado que antes (misma ruta `cargar_mapa_reservas`)
    - _Requirements: 1.3, 9.1_

- [x] 6. Refactor: extraer `_construir_df_proy_tramo` de la sección 7 de `_ejecutar_escenario`
  - [x] 6.1 Extraer la construcción del df_proy a un helper reutilizable en `app_dash.py`
    - `_construir_df_proy_tramo(df_base, vm_motor_rf, ids_vencidos, filas_reinversion, fecha_corte)`: copia base, elimina vencidos, actualiza VM de vivos, concatena reinversiones, excluye colaterales/simultáneas, reclasifica con `clasificar_portafolio_en_memoria(df, fecha_corte)`
    - `_ejecutar_escenario` (modo actual) pasa a usar el helper SIN cambiar su comportamiento observable (refactor puro)
    - _Requirements: 4.3, 4.4, 6.2_

  - [x]* 6.2 Test de ejemplo — el refactor no cambia el comportamiento del modo actual
    - Verificar que `_construir_df_proy_tramo` produce el mismo df que la lógica inline previa para un caso representativo
    - _Requirements: 1.3, 4.3_

- [x] 7. Orquestador por tramos + dispatcher (`app_dash.py`)
  - [x] 7.1 Implementar `_ejecutar_escenario_por_tramos(...)`
    - `calcular_tramos` → lista de `Tramo`; `asegurar_tabla_portafolio_proy` + `limpiar_snapshots_proy` (reemplazo de corrida previa) al inicio
    - Loop cronológico por tramo: cargar t0 (tramo 0 = `portafolio_clasificado` real; i>0 = `cargar_snapshot_proy(corte_{i-1})`); derivar mapa de reservas (mensual → `None`; anual → `prorratear_reservas_anual`); invocar `proyectar_portafolio` con `sub_horizonte` y shifts del tramo y `mapa_reservas`; `_construir_df_proy_tramo`; `persistir_snapshot_proy`; `set_progress("Tramo i de N")`
    - Exponer el df_proy del ÚLTIMO tramo al store (clave `portafolio_proyectado`, mismo formato actual)
    - Manejo de errores por tramo: si un tramo falla, detener el encadenamiento y reportar la `fecha_corte` del tramo fallido con mensaje genérico (sin datos sensibles); NO exponer resultado de fin de horizonte incompleto
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.5, 5.4, 6.1, 6.3, 7.3, 7.4, 7.5, 9.3, 10.1, 10.3, 11.1, 11.3, 11.4_

  - [x] 7.2 Implementar `_ejecutar_escenario_dispatch(modo, ...)`
    - `modo='punta_a_punta'` (default) → `_ejecutar_escenario` EXISTENTE sin cambios; `modo='por_tramos'` → `_ejecutar_escenario_por_tramos`
    - _Requirements: 1.1, 1.2, 1.4_

  - [x]* 7.3 Property test — retrocompatibilidad de `punta_a_punta` (dispatcher)
    - **Property 1: Retrocompatibilidad de `punta_a_punta`**
    - **Validates: Requirements 1.3**
    - Doble de motor determinista; verificar salida idéntica al camino directo

  - [x]* 7.4 Property test — nº de snapshots persistidos = nº de tramos
    - **Property 8: Nº de snapshots persistidos = nº de tramos**
    - **Validates: Requirements 5.4, 6.3**
    - `db_tmp` + doble de motor

  - [x]* 7.5 Property test — encadenamiento usa el cierre del tramo anterior
    - **Property 9: Encadenamiento usa el cierre del tramo anterior**
    - **Validates: Requirements 4.1, 4.2**
    - Doble de motor que "marca" las filas del t0 recibido

  - [x]* 7.6 Property test — el paso del motor sigue siendo mensual
    - **Property 10: El paso del motor sigue siendo mensual**
    - **Validates: Requirements 9.1, 9.3**
    - Capturar args del doble: `meses_horizonte == sub_horizonte` y claves de mes ≤ sub_horizonte

  - [x]* 7.7 Tests de ejemplo — manejo de errores por tramo
    - E1: forzar excepción del doble en el tramo 2 → mensaje contiene la `fecha_corte`, NO VMs ni trazas; el store no recibe resultado de fin de horizonte
    - E2: snapshot previo ausente → `SnapshotAusenteError` reportado
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

- [x] 8. Checkpoint - Ensure all tests pass
  - Suite completa: 109 passed, 1 skipped. Orquestador + dispatcher + encadenamiento (spread efectivo 8→9→10) + errores por tramo en verde. Sin regresiones.

- [x] 9. UI del dashboard (modo, shifts por tramo, periodicidad, progreso)
  - [x] 9.1 Añadir el control de `Modo_Proyeccion` (toggle `punta_a_punta` por defecto / `por_tramos`)
    - RadioItems `modo-proyeccion` (default punta_a_punta) conectado al callback del escenario → `_ejecutar_escenario_dispatch`.
    - _Requirements: 1.1, 1.2_

  - [x] 9.2 Añadir inputs de `Shifts_Tramo` por tramo y selector de `Periodicidad_Reserva_Tramo` por tramo
    - Callback `render_controles_tramos` genera, por cada tramo del horizonte, inputs COP/UVR/FS/IPC (bps, default 0) y dropdown de periodicidad (mensual default | anual prorrateada). El callback del escenario los lee vía States con IDs de patrón (ALL) y arma shifts_por_tramo/periodicidad_por_tramo.
    - _Requirements: 3.1, 3.4, 7.1, 7.2, 12.3, 12.4_

  - [x] 9.3 Reportar progreso por tramo vía `set_progress` en el background callback
    - Componente `escenario-progreso` en el layout + `progress=[Output('escenario-progreso','children')]` en el decorador; `set_progress` es el primer parámetro del callback y se pasa al orquestador, que lo llama por tramo con "Tramo X de N". Dashboard arranca sin errores; suite 112 passed.
    - _Requirements: 10.1, 10.2, 10.3_

  - [x]* 9.4 Test de ejemplo — modo por defecto y render de controles
    - punta_a_punta → sin controles; por_tramos → un bloque por tramo; horizonte < 1 mes → advertencia. 3 tests verdes.
    - _Requirements: 1.1, 1.2, 3.1, 7.1_

- [x] 10. Verificación de dependencias y retrocompatibilidad de la suite existente
  - [x] 10.1 Verificar pineo y no introducción de dependencias nuevas
    - La feature NO introdujo dependencias nuevas (reutiliza pandas, sqlite3 stdlib, dash==4.4.1, hypothesis==6.140.3). Runtime Python 3.12+.
    - _Requirements: 13.1, 13.2, 13.3_

  - [x]* 10.2 Ejecutar la suite completa y confirmar retrocompatibilidad
    - Suite completa: 112 passed, 1 skipped. Modo punta a punta intacto; sin regresiones.
    - _Requirements: 1.3, 1.5_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Suite completa del proyecto: **112 passed, 1 skipped**. Cobertura P1–P13 + ejemplos + UI. Retrocompatibilidad del modo punta a punta confirmada.

## Notes

- Tareas marcadas con `*` son opcionales (pruebas); el código central nunca es opcional.
- Cada tarea referencia requisitos específicos para trazabilidad.
- Property tests: `hypothesis==6.140.3` (pineado), mínimo `max_examples=100`, fixture
  `db_tmp` (SQLite temporal), doble de motor inyectable (monkeypatch/parámetro), archivo
  `tests/test_proyeccion_tramos.py`. Cada property test etiquetado con
  `# Feature: proyeccion-por-tramos, Property N: ...`.
- Único cambio al motor: argumento opcional `mapa_reservas=None` (retrocompatible). No se
  modifica `init_db.py`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2", "2.3", "2.4", "3.2", "3.3", "3.4", "3.5", "5.2", "6.1"] },
    { "id": 2, "tasks": ["6.2", "7.1", "7.2"] },
    { "id": 3, "tasks": ["7.3", "7.4", "7.5", "7.6", "7.7", "9.1", "9.2", "9.3"] },
    { "id": 4, "tasks": ["9.4", "10.1"] },
    { "id": 5, "tasks": ["10.2"] }
  ]
}
```
