# Implementation Plan: Optimización — Agrupación de Negociables Fungibles

## Overview

Convertimos el diseño en pasos de código incrementales en **Python 3.12+** (stack existente del proyecto). La feature añade una **función pura** `consolidar_negociables_fungibles` en `scripts/proyeccion_tramos.py` y la **invoca** desde el orquestador `_ejecutar_escenario_por_tramos` en `app_dash.py`, justo antes de `persistir_snapshot_proy`. El `Motor_Proyeccion_RF` NO se modifica.

El orden de trabajo: primero el modelo de diagnóstico y el helper de clave (base reutilizable), luego la función pura, después las propiedades y pruebas unitarias (Hypothesis + pytest), después la invocación fail-safe en el orquestador, y por último la verificación de equivalencia end-to-end (baseline/check) y la corrida de 3 tramos. Cada paso se apoya en el anterior y termina cableado al flujo real.

Como la verificación de equivalencia y la corrida de 3 tramos requieren ejecutar contra la DB real durante tiempos largos, esas corridas se dejan como comandos para que el usuario las ejecute manualmente; las tareas de código entregan el arnés y los scripts/aserciones automatizadas que las respaldan.

## Tasks

- [x] 1. Preparar el módulo de consolidación (modelo de diagnóstico y helper de clave)
  - [x] 1.1 Añadir `ResumenConsolidacion` y constantes de clasificación en `scripts/proyeccion_tramos.py`
    - Definir el dataclass `@dataclass(frozen=True) ResumenConsolidacion` con los campos `n_entrada`, `n_salida`, `n_negociables`, `n_intocables`, `n_grupos`, `n_colapsadas`
    - Definir constantes del alfabeto de clasificación: clases no-RF (`RENTA_VARIABLE`, `ALTERNATIVO`, `DERIVADO`) y marcadores de clasificación a TIR (`Vencimiento`, `Disp. Venta`)
    - Reutilizar el `logger` de módulo existente (sin crear estado global mutable)
    - _Requirements: 5.1, 5.2, 6.4_

  - [x] 1.2 Implementar el helper interno `_clave_fungibilidad(fila)`
    - Construir la tupla normalizada `(especie, isin, fecha_vencimiento_iso, tasa_facial_red, periodicidad, tipo_tasa, moneda, tipo_bono)` y devolver `None` si cualquier componente no coerciona (tratar la entrada como NO confiable)
    - Texto: `str(x).strip().upper()`; `fecha_vencimiento` a ISO `YYYY-MM-DD` (None si no parsea); `tasa_facial` a float finito redondeado a precisión fija (p. ej. 10 decimales) para evitar que ruido de punto flotante impida agrupar; None si no es finito
    - Derivar `tipo_bono` (UVR/IPC/YANKEE/TF) con la MISMA lógica que `cargar_titulos_desde_snapshot` a partir de `asset_class_definitivo`/`moneda`/`tasa_referencia`, para garantizar mismo precio por unidad de nominal
    - _Requirements: 1.5, 2.3, 5.4_

  - [x] 1.3 Escribir pruebas unitarias de `_clave_fungibilidad`
    - Casos: normalización de texto (mayúsculas/espacios/no-ASCII), fecha inválida → None, tasa no finita → None, dos filas idénticas salvo ruido de punto flotante en `tasa_facial` producen la MISMA clave, derivación correcta de `tipo_bono`
    - _Requirements: 5.4, 1.5_

- [x] 2. Implementar la función pura `consolidar_negociables_fungibles`
  - [x] 2.1 Implementar la partición CONSOLIDABLES vs INTOCABLES y el colapso por grupo
    - Firma: `consolidar_negociables_fungibles(df, *, tolerancia: float = 1e-9) -> tuple[pd.DataFrame, ResumenConsolidacion]`; NO mutar la entrada (devolver DataFrame nuevo con las mismas columnas)
    - Partición: CONSOLIDABLE = RF (`clase_origen` en {TITULO, vacío}) AND `clasificacion_contable == Negociable` AND `id_cobertura` vacío (si la columna no existe, tratar como vacío/intocable según la tabla de Error Handling del diseño); todo lo demás a INTOCABLES
    - Para cada CONSOLIDABLE: calcular `_clave_fungibilidad`; si es `None`, mover a INTOCABLES (fallback por fila, nunca se pierde) + `logger.warning` genérico (sin volcar contenido)
    - Grupos con ≥2 miembros → `Titulo_Consolidado`: `nominal`/`valor_mercado`/`valor_tir` = suma del grupo; atributos de la clave = comunes; `clasificacion_contable = Negociable`; `fecha_emision` = MÍNIMA del grupo; `id_posicion = CONS_<hash corto determinista de la clave>`; columnas no sumables (p. ej. `tasa_compra`) tomadas del miembro de mayor nominal
    - Grupos de 1 miembro → emitir la fila sin cambios
    - Salida en orden estable y determinista: INTOCABLES + consolidados + singletons; poblar y devolver `ResumenConsolidacion`
    - Casos degenerados: DataFrame vacío o sin columnas esperadas → devolver df tal cual con resumen en ceros (no lanzar)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 4.2, 4.4, 5.1, 5.3, 6.5_

  - [x] 2.2 Write property test — conservación de sumas por grupo y totales globales
    - **Property 1: Conservación de sumas por grupo (nominal, VM, VTIR)**
    - **Validates: Requirements 1.2, 1.3, 1.4, 4.4**
    - Hypothesis, ≥100 iteraciones; comentario `# Feature: optimizacion-agrupacion-negociables, Property 1: ...`

  - [x] 2.3 Write property test — atributos de la clave preservados e invariantes del grupo
    - **Property 2: Atributos de la clave preservados e invariantes del grupo**
    - **Validates: Requirements 1.5**
    - Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 2

  - [x] 2.4 Write property test — títulos a TIR y filas no-RF nunca se alteran
    - **Property 3: Los títulos a TIR y las filas no-RF nunca se alteran**
    - **Validates: Requirements 2.1, 2.2, 2.4**
    - Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 3

  - [x] 2.5 Write property test — solo se fusionan Negociables con clave idéntica
    - **Property 4: Solo se fusionan Negociables con clave idéntica**
    - **Validates: Requirements 2.3**
    - Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 4

  - [x] 2.6 Write property test — subyacentes de swap no se consolidan
    - **Property 5: Los subyacentes de swap no se consolidan**
    - **Validates: Requirements 2.5**
    - Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 5

  - [x] 2.7 Write property test — grupos unitarios inalterados
    - **Property 6: Grupos unitarios inalterados**
    - **Validates: Requirements 1.6**
    - Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 6

  - [x] 2.8 Write property test — idempotencia de la consolidación
    - **Property 7: Idempotencia de la consolidación**
    - **Validates: Requirements 1.1, 1.2**
    - Hypothesis, ≥100 iteraciones; `consolidar(consolidar(x)) ≡ consolidar(x)`

  - [x] 2.9 Write unit tests de borde para `consolidar_negociables_fungibles`
    - Casos: DataFrame vacío, un único grupo fungible grande, grupo con tasas que difieren por ruido de punto flotante (deben agrupar), filas con clave degenerada (fallback a intocable + warning), columnas `clasificacion_contable`/`clase_origen`/`id_cobertura` ausentes (valor conservador), verificación del `ResumenConsolidacion`
    - _Requirements: 5.4, 5.5, 6.5_

- [x] 3. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Verificar la equivalencia del cupón del consolidado al encadenar (Req 4)
  - [x] 4.1 Confirmar y, si es necesario, ajustar el anclaje de cupones al reconstruir el consolidado
    - Verificar que `cargar_titulos_desde_snapshot` reconstruye el `Titulo_Consolidado` anclando el calendario de cupones a `fecha_vencimiento` + `tasa_facial` + `periodicidad` (no a `fecha_emision`), y que la `fecha_emision` MÍNIMA del grupo no recorta cupones futuros
    - NO modificar la lógica de valoración del motor; limitarse a asegurar la reconstrucción correcta del consolidado (si no requiere cambios, dejar comentario justificando)
    - _Requirements: 4.1, 4.2_

  - [x] 4.2 Write property test — equivalencia del cupón del período del consolidado
    - **Property 8: Equivalencia del cupón del período del consolidado**
    - **Validates: Requirements 4.1, 4.3**
    - Reconstruir el consolidado como t0 y comparar el cupón del período contra la suma de los cupones de los miembros dentro de la tolerancia; Hypothesis, ≥100 iteraciones; comentario con el texto de la Property 8

- [x] 5. Integrar la consolidación en el orquestador por tramos (`app_dash.py`)
  - [x] 5.1 Invocar `consolidar_negociables_fungibles` en `_ejecutar_escenario_por_tramos` con fail-safe
    - Insertar el bloque "5c" entre la construcción del snapshot (`_finalizar_df_proy` / `_construir_snapshot_completo_tramo`) y `ptr.persistir_snapshot_proy(...)`
    - Envolver en `try/except`: en éxito, reasignar `df_proy` consolidado y emitir diagnóstico `[TRAMOS-CONSOLIDA]` (análogo a `[SNAPSHOT-DEDUP]`) con `n_antes->n_despues`, `n_grupos`, `n_colapsadas`, `n_negociables`, `n_intocables`
    - En excepción: persistir el conjunto SIN consolidar (fallback no destructivo) y registrar `[TRAMOS-CONSOLIDA] ... OMITIDA por error`; nunca abortar el encadenamiento
    - Aplicar SOLO en `Modo_Proyeccion = por_tramos`; no tocar el flujo `punta_a_punta`
    - _Requirements: 1.1, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 5.2 Write integration test del orquestador (consolidación + fallback)
    - Con un `df_proy` sintético que mezcle Negociables fungibles, a TIR, no-RF y un subyacente de swap: verificar que tras el bloque el snapshot persistido contiene los consolidados en lugar de los Negociables individuales, deja intactas filas a TIR/no-RF, y que una excepción forzada en la consolidación persiste el conjunto sin consolidar
    - _Requirements: 6.3, 6.5_

- [x] 6. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Habilitar la verificación de equivalencia numérica end-to-end (Req 3, 7)
  - [x] 7.1 Añadir script/aserciones de comparación de snapshots por tramo
    - Crear un pequeño script en `tools/` (o extender el arnés) que, dado dos snapshots de un corte (con y sin consolidación), compare VM total y VTIR total de cierre, y P&L mercado/TIR y cupones por mes agregados por `Clave_Fungibilidad`, reportando desvíos > tolerancia `1e-6` relativo
    - Dejar aserciones automatizadas que fallen si se excede la `Tolerancia_Equivalencia`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6_

  - [x] 7.2 Documentar el procedimiento baseline/check y la corrida de 3 tramos
    - Dejar en el spec/README del arnés los comandos exactos: `tools/equivalencia_motor_rf.py baseline` (ANTES del cambio), `... check` (DESPUÉS) con tolerancia `1e-6`; y la corrida por tramos 2026→2028 para confirmar que los tres cortes persisten (incluido dic-2028 con sus hojas de Excel) y que los dos primeros tramos quedan dentro de tolerancia
    - Nota: estas corridas se ejecutan manualmente por el usuario contra la DB real (procesos largos); no se automatizan como parte del flujo del agente
    - _Requirements: 3.5, 7.1, 7.2, 7.3, 7.4_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Lenguaje de implementación: **Python 3.12+** (stack existente; el diseño referencia código Python real en `scripts/`).
- Las tareas marcadas con `*` son opcionales (tests de propiedad/unitarios/integración) y pueden omitirse para un MVP más rápido, pero validan las propiedades de corrección y la restricción inviolable.
- Property-based tests con **Hypothesis** (ya presente en el proyecto, ver `scripts/.hypothesis/`): una propiedad = un test, ≥100 iteraciones, cada test comentado con el número y texto de la propiedad del diseño.
- El `Motor_Proyeccion_RF` (`proyectar_portafolio`) NO se modifica (Req 5.3); la consolidación es orquestación pura antes de persistir.
- La verificación de equivalencia numérica (`baseline`/`check`) y la corrida de 3 tramos solo pueden confirmarse ejecutando contra la DB real; por eso se entregan como arnés + comandos para ejecución manual del usuario.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "4.1"] },
    { "id": 3, "tasks": ["4.2", "5.1", "7.1"] },
    { "id": 4, "tasks": ["5.2", "7.2"] }
  ]
}
```
