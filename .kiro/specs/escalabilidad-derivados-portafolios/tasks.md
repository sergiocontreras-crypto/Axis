# Implementation Plan: Escalabilidad de Derivados y Portafolios

## Overview

El plan implementa las cuatro mejoras del diseño de forma incremental y quirúrgica, priorizando las **funciones puras** (validadores, fórmulas de devaluación/punto forward, proyección de curvas, prima neta, solver de canal) para poder validarlas temprano con pruebas basadas en propiedades (Hypothesis), y luego integrándolas en `app_dash.py`, `valoracion_derivados.py` y la tabla de auditoría. Cada tarea construye sobre la anterior y termina cableando los componentes en el flujo existente, sin romper contratos de retorno ni esquemas de tablas.

Lenguaje de implementación: **Python** (proyecto existente Python + Dash + SQLite). PBT: **Hypothesis** + `pytest` (mínimo 100 ejemplos por propiedad), versión exacta fijada en `requirements.txt`.

## Tasks

- [x] 1. Fijar dependencias de testing y estructura de pruebas
  - Agregar `hypothesis` y `pytest` con versión exacta (sin `^`/`~`/`*`/`latest`) a `requirements.txt`
  - Crear el directorio `tests/` con `__init__.py` y un `conftest.py` con utilidades comunes (fábrica de DB SQLite temporal con `tempfile`, generadores/estrategias Hypothesis para tasas, plazos, TRM, listas de ids y curvas sintéticas)
  - _Requirements: 5.1, 5.2_

- [x] 2. Validadores puros de altas de portafolios y swaps (`comun/validaciones_inputs.py`)
  - [x] 2.1 Implementar helpers de normalización y detección
    - Crear `comun/validaciones_inputs.py` con `normalizar_id`, `detectar_portafolios_nuevos` y `detectar_swaps_sin_subyacente` como funciones puras (allowlists, trim/normalización de entrada)
    - _Requirements: 1.1, 1.2, 2.1, 2.2_

  - [x] 2.2 Implementar validadores de alta de portafolio y asignación de swap
    - Implementar `validar_alta_portafolio` (rechaza ramo/tipo vacíos, `excluir ∉ {'SI','NO'}`, `portafolio_porfin` normalizado duplicado; devuelve payload normalizado) y `parsear_subyacentes` + `validar_asignacion_swap` (rechaza 0 subyacentes, fuera de [1,50], subyacentes inexistentes identificándolos, swap ya registrado; `Subyacente = '|'.join(...)`)
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 2.3, 2.4, 2.5, 2.6_

  - [x] 2.3 Escribir property test de detección y validación de alta de portafolio
    - **Property 1: Detección de nuevos y validación de alta de portafolio**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**

  - [x] 2.4 Escribir property test de detección de swaps y validación de asignación
    - **Property 2: Detección de swaps nuevos y validación de asignación de subyacentes**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

- [x] 3. Persistencia parametrizada y atómica de altas (`comun/validaciones_inputs.py` o `comun/db.py`)
  - [x] 3.1 Implementar `persistir_portafolio` y `persistir_subyacente`
    - Insertar en `param_portafolios` y `param_diccionario_swaps` usando **solo binding `?`** para valores y nombres de columna desde un allowlist fijo; envolver en transacción atómica (`with conn:`), con `rollback` y re-raise ante excepción; preservar el esquema existente sin alterarlo
    - _Requirements: 1.10, 2.9, 5.1, 5.2, 5.5_

  - [x] 3.2 Escribir property test de preservación de esquema `param_*`
    - **Property 5: Preservación de esquema de tablas param_***
    - **Validates: Requirements 1.10, 2.9, 5.1**

  - [x] 3.3 Escribir property test de escritura parametrizada resistente a inyección
    - **Property 6: Escritura parametrizada resistente a inyección**
    - **Validates: Requirements 5.2**

  - [x] 3.4 Escribir property test de atomicidad (rollback)
    - **Property 7: Atomicidad de las escrituras (rollback)**
    - **Validates: Requirements 5.5**

- [x] 4. Integrar controles y callbacks de alta en el dashboard (`app_dash.py`)
  - [x] 4.1 Agregar layout de portafolios nuevos y swaps sin subyacente en `layout_inputs`
    - Añadir dos secciones (`dash_table.DataTable` editable + inputs + botón guardar + `html.Div` de mensaje) que aparecen solo cuando hay elementos nuevos en el corte, siguiendo el patrón `detectar_incompletos`; `excluir` restringido a {'SI','NO'} y sin captura de compañía
    - _Requirements: 1.1, 1.2, 1.4, 2.1, 2.2_

  - [x] 4.2 Implementar callbacks `alta_portafolio` y `asignar_subyacente`
    - Detectar nuevos comparando el corte contra `param_portafolios`/`param_diccionario_swaps`, validar con las funciones puras del paso 2, persistir con las funciones del paso 3, devolver mensaje de confirmación (identificando el id afectado) o error específico (campo faltante / duplicado / subyacentes inexistentes) y mensaje genérico ante fallo de DB
    - _Requirements: 1.5, 1.6, 1.7, 2.4, 2.5, 2.6, 2.7, 5.2, 5.5_

  - [x] 4.3 Escribir unit tests de callbacks del dashboard
    - Invocar los callbacks directamente (sin navegador): confirmar mensajes de confirmación con identificador, rechazos por validación, ocultamiento de controles sin elementos nuevos, y entradas adversarias de inyección SQL
    - _Requirements: 1.1, 1.2, 1.7, 2.2, 2.7_

- [x] 5. Verificar no-regresión de la clasificación (`clasificar_portafolio.py` sin cambios)
  - [x] 5.1 Escribir property test de compañía derivada por prefijo
    - **Property 3: Compañía derivada por prefijo (sin regresión)**
    - **Validates: Requirements 1.9, 5.4**

  - [x] 5.2 Escribir property test de no-regresión de ramo/tipo e id_cobertura
    - **Property 4: No-regresión de la clasificación (ramo/tipo e id_cobertura)**
    - **Validates: Requirements 1.8, 2.8, 5.4**

  - [x] 5.3 Escribir test de regresión por snapshot de `portafolio_clasificado`
    - Comparar la salida del Clasificador sobre insumos iguales contra un baseline capturado (encaje/compañía/id_cobertura inalterados tras las altas)
    - _Requirements: 5.4_

- [x] 6. Checkpoint - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Fórmulas puras de devaluación y punto forward (`libro_de_funciones.py`, `valoracion_derivados.py`)
  - [x] 7.1 Implementar conversiones y fórmulas base de tasa
    - Añadir en `valoracion_derivados.py` (y auxiliares nominal↔EA / EA↔continua en `libro_de_funciones.py` sin tocar firmas existentes) `tasa_ea`, `devaluacion_implicita`, `dev_valida` y `punto_forward_dinamico`, protegiendo `dias <= 0` y bases negativas
    - _Requirements: 3.1, 3.2, 3.3, 3.6, 3.7, 3.8_

  - [x] 7.2 Escribir property test de la tasa negociada del forward
    - **Property 8: Fórmula de la tasa negociada del forward**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 7.3 Escribir property test de devaluación implícita por paridad de tasas
    - **Property 9: Devaluación implícita por paridad de tasas**
    - **Validates: Requirements 3.3**

  - [x] 7.4 Escribir property test de punto forward dinámico e identidad de descuento
    - **Property 12: Punto forward dinámico e identidad de descuento**
    - **Validates: Requirements 3.6, 3.7**

  - [x] 7.5 Escribir property test del rango de validez de la devaluación
    - **Property 14: Rango de validez de la devaluación**
    - **Validates: Requirements 3.8**

- [x] 8. Proyección de curvas con shift mensual (`valoracion_derivados.py`)
  - [x] 8.1 Implementar interpolación y proyección de curvas
    - Añadir `shift_lutibr_por_plazo` (interpolación lineal FED↔tesoros 10a con clamp plano) y `curva_proyectada_pct` (suma del shift en % escalado por progreso `m/N`, sin aplicar shift a `FWPCOP`)
    - _Requirements: 3.4, 3.5_

  - [x] 8.2 Escribir property test de proyección lineal e interpolación por plazo
    - **Property 10: Proyección lineal del shift por progreso e interpolación por plazo**
    - **Validates: Requirements 3.4**

  - [x] 8.3 Escribir property test de que el shift nunca se aplica a FWPCOP
    - **Property 11: El shift nunca se aplica a los puntos forward en pesos**
    - **Validates: Requirements 3.5**

- [x] 9. Orquestador de devaluación por corte con fallback y auditoría (`valoracion_derivados.py`)
  - [x] 9.1 Implementar `calcular_dev_y_punto_forward`
    - Combinar curvas proyectadas + fórmulas para devolver `(dev_usada, punto_forward, registro_auditoria)`; aplicar fallback a `dev_forecast` cuando no haya curva al plazo, el resultado no sea finito o quede fuera de [-0.5, 2.0], marcando `uso_fallback`; construir el `Registro_Auditoria` de forwards
    - _Requirements: 3.9, 3.11_

  - [x] 9.2 Escribir property test del fallback a dev_forecast
    - **Property 15: Fallback a dev_forecast**
    - **Validates: Requirements 3.9**

  - [x] 9.3 Escribir property test del contenido del registro de auditoría de forwards
    - **Property 16: Contenido del registro de auditoría de forwards**
    - **Validates: Requirements 3.11**

- [x] 10. Integrar devaluación dinámica en el flujo de forwards (`valoracion_derivados.py`)
  - [x] 10.1 Sustituir dev plana en renovación y forwards nuevos
    - Modificar `_renovar_fwds` (renovaciones a 100 días) y el bloque de forwards nuevos del método 3 (plazo 93 días) para calcular `tasa_negociada` con `Devaluacion_Implicita` en el mes de constitución, con `dev_forecast` como fallback
    - _Requirements: 3.1, 3.2_

  - [x] 10.2 Calcular `fwdcop` dinámico por corte en `_valorar_fwds_corte`
    - Reemplazar el merge con `fwd_cop` estático de PIP por `punto_forward_dinamico` por fila (plazo del corte al vencimiento), preservando la firma de `valorar_fwds`
    - _Requirements: 3.6, 3.7_

  - [x] 10.3 Escribir property test de un forward nuevo a mercado valorado ≈ cero
    - **Property 13: Un forward recién constituido a mercado vale aproximadamente cero**
    - **Validates: Requirements 3.7**

- [x] 11. Checkpoint - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Prima neta del collar y solver de canal (`libro_de_funciones.py`, `valoracion_derivados.py`)
  - [x] 12.1 Implementar `prima_neta_collar` en `libro_de_funciones.py`
    - Envolver `precio_bs_signed` (sin cambiar su firma) para sumar Put + Call con signos por sentido y por pata
    - _Requirements: 4.2_

  - [x] 12.2 Implementar `resolver_canal_collar` en `valoracion_derivados.py`
    - Resolver `k_call` con `scipy.optimize.brentq` (bisección de respaldo) sobre la prima neta usando `interpol_vol` y `r_corte`/`q_corte` proyectadas; devolver `(k_call, prima_neta, uso_fallback)`; fallback `k_call = k_put + canal_opc` sin lanzar excepción ante no convergencia
    - _Requirements: 4.1, 4.5, 4.7_

  - [x] 12.3 Escribir property test del collar de prima neta acotada
    - **Property 17: El solver produce un collar de prima neta acotada**
    - **Validates: Requirements 4.1, 4.2, 4.5**

  - [x] 12.4 Escribir property test del fallback del solver ante no convergencia
    - **Property 20: Fallback del solver ante no convergencia**
    - **Validates: Requirements 4.7**

- [x] 13. Integrar el solver en la renovación de collares con memoización (`valoracion_derivados.py`)
  - [x] 13.1 Fijar el strike Call en el momento de creación y hacerlo inmutable
    - Modificar `_renovar_opciones` para resolver el `k_call` vía `Solver_Canal` en el corte de la fecha de constitución, persistir en `opts_plan` y memoizar por `id` de collar (no recalcular en cortes posteriores); las `Opciones_Existentes` de `hist_opciones` nunca pasan por el solver
    - _Requirements: 4.3, 4.4_

  - [x] 13.2 Producir el `Registro_Auditoria` del solver
    - Acumular por resolución de collar el registro con mes de proyección, shifts TES/FED acumulados, strike Put, strike Call resuelto, prima neta y `uso_fallback`
    - _Requirements: 4.10_

  - [x] 13.3 Escribir property test de no-regresión de las opciones existentes
    - **Property 18: No-regresión de las opciones existentes**
    - **Validates: Requirements 4.3**

  - [x] 13.4 Escribir property test de inmutabilidad del collar renovado
    - **Property 19: Inmutabilidad del collar renovado tras su creación**
    - **Validates: Requirements 4.4**

  - [x] 13.5 Escribir property test del contenido del registro de auditoría del solver
    - **Property 21: Contenido del registro de auditoría del solver**
    - **Validates: Requirements 4.10**

- [x] 14. Persistencia de auditoría de la corrida (`valoracion_derivados.py`, tabla nueva)
  - [x] 14.1 Crear e implementar la escritura de `audit_derivados_corrida`
    - `CREATE TABLE IF NOT EXISTS` con el esquema del diseño; escribir los `Registro_Auditoria` acumulados con `executemany` (binding) en transacción atómica; el fallo de auditoría no invalida el resultado en memoria
    - _Requirements: 5.3_

  - [x] 14.2 Escribir property test de round-trip de persistencia de la auditoría
    - **Property 22: Round-trip de persistencia de la auditoría**
    - **Validates: Requirements 5.3**

- [x] 15. Cablear parámetros configurables y contrato de retorno (`valoracion_derivados.py`)
  - [x] 15.1 Ampliar la firma de `valorar_portafolio_derivados` de forma retrocompatible
    - Agregar keyword args con defaults y validación de rango fail-fast: `dev_forecast=0.085` [-0.5,2.0], `canal_opc=1650`, `tolerancia_prima=1.0` [0.0001,1000.0], `max_iter_canal=100` [1,1000], `persistir_auditoria=True`, `corrida_id=None`; conectar dev dinámica, solver y persistencia de auditoría; preservar el contrato de retorno `{'opciones','forwards','activos','swaps'}`
    - _Requirements: 3.10, 3.12, 4.6, 4.8, 4.9, 4.11_

  - [x] 15.2 Escribir unit tests de configuración de parámetros y rangos
    - Verificar defaults y validación de rangos (clamp/ValueError) de `dev_forecast`, `canal_opc`, `tolerancia_prima`, `max_iter_canal`
    - _Requirements: 3.10, 4.6, 4.8, 4.9_

  - [x] 15.3 Escribir property test de preservación del contrato de retorno
    - **Property 23: Preservación del contrato de retorno del valorador**
    - **Validates: Requirements 3.12, 4.11**

- [x] 16. Checkpoint final - Asegurar que todas las pruebas pasan
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (pruebas) y pueden omitirse para un MVP más rápido; las tareas de implementación núcleo nunca están marcadas como opcionales.
- Cada tarea referencia cláusulas de requisito específicas para trazabilidad.
- Los checkpoints aseguran validación incremental.
- Las pruebas de propiedad validan las 23 propiedades de correctitud del diseño con Hypothesis (mínimo 100 ejemplos por propiedad); las pruebas unitarias cubren UI, configuración y edge cases.
- Principio quirúrgico: no se alteran esquemas de `param_*`/`hist_*` ni las firmas existentes de `valorar_fwds`, `precio_bs_signed`, `delta_bs`; la única tabla nueva es `audit_derivados_corrida`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["2.2", "7.1", "8.1", "12.1"] },
    { "id": 2, "tasks": ["2.3", "2.4", "3.1", "7.2", "7.3", "7.4", "7.5", "8.2", "8.3", "12.2"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "4.1", "9.1", "10.2", "12.3", "12.4", "14.1"] },
    { "id": 4, "tasks": ["4.2", "9.2", "9.3", "10.1", "13.1", "14.2"] },
    { "id": 5, "tasks": ["4.3", "5.1", "5.2", "5.3", "10.3", "13.2", "15.1"] },
    { "id": 6, "tasks": ["13.3", "13.4", "13.5", "15.2", "15.3"] }
  ]
}
```
