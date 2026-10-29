# Implementation Plan: Proyección por Tramos Multiactivo

## Overview

Extiende el modo `por_tramos` existente para: (1) corregir el conteo de meses en ambos modos, (2) convertir retornos EA a la duración del tramo, (3) interpolar TRM y propagar repo/inflación por tramo, (4) encadenar RV, Alternativos y Créditos junto a RF, y (5) reestructurar la UI (selector al inicio, grid por tramo, botón único, eliminar botones individuales).

La **extensión** (grupos 10–12) añade: (6) integrar el `Motor_Derivados` en el encadenamiento por tramos con Δ Fed y Δ Tesoros 10Y por tramo (Req 13), y (7) persistir un snapshot de cierre por tramo con TODOS los activos —RF + RV + Alternativos + Derivados— en `portafolio_clasificado_proy` (Req 14).

Los cinco motores de valoración (`proyectar_portafolio`, `proyectar_renta_variable`, `proyectar_alternativos`, `proyectar_creditos_contratados`, `valorar_portafolio_derivados`) NO se modifican en su lógica interna. La orquestación vive en `app_dash.py` y en un módulo nuevo `scripts/proyeccion_tramos_multiactivo.py`. Se reutiliza `proyeccion_tramos.py` (con la corrección de `_meses_entre`), la tabla `portafolio_clasificado_proy` y el `Store_Dashboard`, todos sin cambios de contrato salvo lo indicado.

Property-based tests con `hypothesis` (≥100 iteraciones), etiquetados:
`# Feature: proyeccion-por-tramos-multiactivo, Property {N}: {texto}`

## Tasks

- [ ] 1. Corregir el conteo de meses (Conteo_Meses_Inclusivo) en ambos modos
  - [ ] 1.1 Implementar `meses_horizonte_inclusivo(inicio, fin)` en `scripts/proyeccion_tramos_multiactivo.py`
    - Fórmula `delta.years*12 + delta.months + (1 if delta.days > 0 else 0)` con `dateutil.relativedelta`
    - Año calendario completo (1-ene → 31-dic) devuelve 12; devuelve `>= 0` (el llamador aplica su mínimo)
    - _Requirements: 10.1, 10.2, 10.3, 10.7_

  - [ ] 1.2 Corregir `_meses_entre` en `scripts/proyeccion_tramos.py` para usar `meses_horizonte_inclusivo`
    - Reemplazar `delta.years*12 + delta.months` por la fórmula inclusiva (delegar en el helper de 1.1)
    - Mantener el resto de `calcular_tramos` sin cambios; el `sub_horizonte` de un tramo anual pasa a ser 12
    - _Requirements: 10.1, 10.3, 10.4, 10.5_

  - [ ] 1.3 Corregir el conteo del orquestador consolidado punta a punta en `app_dash.py`
    - Localizar el `n_meses = delta.years*12 + delta.months` del orquestador consolidado (~línea 6343) y unificarlo con `meses_horizonte_inclusivo`
    - No cambiar otros callizados que ya usan la fórmula correcta
    - _Requirements: 10.2, 10.6, 10.7, 9.1_

  - [x]* 1.4 Property test del conteo de meses inclusivo
    - **Property 1: Conteo de meses inclusivo — año completo = 12**
    - **Validates: Requirements 10.1, 10.2, 10.3**
    - Generar `(inicio, fin)`; assert equivale a la fórmula inclusiva y que año completo ⇒ 12

  - [x]* 1.5 Actualizar tests de regresión de `proyeccion-por-tramos` que asumían 11 meses
    - Ajustar los tests de `calcular_tramos`/`sub_horizonte` que esperaban 11 para un año completo a esperar 12
    - _Requirements: 10.1, 9.1_

- [ ] 2. Checkpoint - Conteo de meses corregido y suite verde
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Implementar las primitivas puras de conversión y sendas por tramo
  - [ ] 3.1 Implementar `convertir_ea_a_tramo(retorno_ea, meses_tramo)` en `scripts/proyeccion_tramos_multiactivo.py`
    - `(1 + retorno_ea) ** (meses_tramo/12) - 1`; `meses_tramo == 12` ⇒ identidad; coerción a float finito (no numérico → 0.0); `meses_tramo < 1` ⇒ `ValueError`
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ] 3.2 Implementar `interpolar_trm_tramo(trm_inicio, trm_cierre, periodos)` en el mismo módulo
    - Senda `{'YYYY-MM-DD': trm}` interpolada linealmente desde `trm_inicio` (cierre del tramo anterior o TRM t0) hasta `trm_cierre` (nivel de cierre del tramo); último valor == `trm_cierre`
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ] 3.3 Implementar `SendasTramo` y `construir_sendas_tramo(tramo, inputs_tramo, estado_previo)`
    - Devolver `SendasTramo(senda_trm, senda_ipc, senda_repo, senda_ibr, senda_sofr)` del tramo, reutilizando el patrón de `construir_senda_trm_dict`/`_sendas_planas_tramo`
    - `senda_ipc`/`senda_repo` derivadas de `Inflacion_Por_Tramo`/`Repo_Por_Tramo` (planas al nivel del tramo o interpoladas desde el cierre previo)
    - _Requirements: 6.1, 7.1, 7.2, 7.3_

  - [ ] 3.4 Implementar `InputsPorTramo` (dataclass) con coerción/validación y defaults por campo
    - Campos de la tabla del usuario (inflación, repo, mov TES COP/UVR, spread corp IPC/TF, TRM, ret acc local, ret alt local/intl, ret alt IPC, PEI)
    - Coerción a float finito; no numérico/vacío/no finito ⇒ default documentado + `logger.warning` genérico (sin volcar valor)
    - _Requirements: 1.1, 1.3, 12.1, 12.4_

  - [ ]* 3.5 Property test de conversión EA (identidad en 12, monotonía, cota por duración)
    - **Property 3 y 4: Conversión EA identidad en año completo; monótona y ≈ (1+r)^(m/12)−1**
    - **Validates: Requirements 5.2, 5.3, 5.4**

  - [ ]* 3.6 Property test de interpolación TRM (ancla extremos, último == cierre)
    - **Property 5: Interpolación TRM ancla extremos**
    - **Validates: Requirements 6.1, 6.2, 6.3**

  - [ ]* 3.7 Property test de validación de `InputsPorTramo`
    - **Property 12: Validación de inputs no confiables**
    - **Validates: Requirements 12.1**

- [ ] 4. Checkpoint - Primitivas puras completas
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implementar el encadenamiento no-RF y la invocación de los cuatro motores por tramo
  - [ ] 5.1 Implementar `EstadoEncadenado` y los helpers de escalado de VM/saldo por activo
    - Estructura con `vm_rv_por_posicion`, `vm_alt_por_posicion`, `saldo_credito_por_id`
    - Implementar la ecuación de encadenamiento: `VM_inicio(tramo_i) = VM_cierre(tramo_{i-1})`, INYECTANDO el VM de cierre del tramo anterior en la columna `valor_mercado` del portafolio que se pasa al motor (verificado: `proyectar_bloque` lee el VM inicial de esa columna y compone `(1+EA)^(m/12)`)
    - Extraer del resultado de cada motor no-RF el VM/saldo de cierre del tramo (último `fecha_proyeccion`, agregado por posición) para poblar el estado del siguiente tramo
    - Para internacionales: el VM de cierre ya incluye el efecto TRM del tramo; se arrastra el VM_COP de cierre como VM_inicio del siguiente (el motor lo reconvierte a USD con la TRM inicial del nuevo tramo)
    - _Requirements: 4.1, 4.2_

  - [ ] 5.2 Implementar `_correr_rv_tramo`, `_correr_alt_tramo`, `_correr_creditos_tramo` (adaptadores por tramo) en `app_dash.py`
    - Cada adaptador arma `fecha_inicio`/`fecha_fin` del tramo (`YYYY-MM`), las tasas del tramo (EA → periodo según D3, verificado por motor), las sendas del tramo (`SendasTramo`) y el VM/saldo encadenado, e invoca el motor correspondiente SIN modificar su firma
    - Verificar contra el código de cada `proyectar_bloque` si la tasa EA se capitaliza por los meses del sub-periodo (vía `fecha_inicio`/`fecha_fin`) o si debe pre-convertirse con `convertir_ea_a_tramo`; elegir la vía correcta y dejar el tramo parcial (5 meses) cuadrando
    - _Requirements: 4.1, 5.1, 5.2, 6.3, 7.3_

  - [ ] 5.3 Extender `_ejecutar_escenario_por_tramos` para invocar los 4 motores por tramo y encadenar
    - Tras la corrida RF ya existente de cada tramo, invocar RV/Alt/Créditos con los adaptadores; actualizar `EstadoEncadenado` con los cierres
    - Propagar `senda_repo`/`senda_inflacion` del tramo a `proyectar_portafolio` (cerrar la brecha del Req 7); interpolar TRM por tramo hacia los 4 motores
    - Manejo de error por tramo: si un motor falla, detener e informar activo + `Fecha_Corte_Tramo`, sin exponer fin de horizonte
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 6.2, 7.1, 7.2, 7.3_

  - [ ] 5.4 Consolidar los resultados del último tramo en el store
    - Añadir claves `rv`, `alternativos`, `creditos` (y detalles opcionales) al dict de resultados del modo por tramos, con el mismo formato que consume el renderizador actual
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ] 5.8 Calcular la descomposición del efecto moneda por activo internacional y tramo
    - Implementar `EfectoMonedaTramo` y el cálculo en la orquestación: `efecto_moneda = (VM_inicio_COP / TRM_inicio) × (TRM_cierre − TRM_inicio)`; `efecto_retorno = PyG_total − efecto_moneda`; local ⇒ efecto_moneda = 0
    - Acumular por tramo y total del horizonte por activo; usar la TRM inicio/cierre de la `senda_trm` del tramo (D8), sin tocar los motores
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [x]* 5.9 Property tests del efecto moneda
    - **Property 13, 14, 15: efecto moneda + retorno reconstruyen el PyG; cero para locales; = ΔTRM × VM inicial en origen por tramo**
    - **Validates: Requirements 11.3, 11.4, 11.5, 11.6**

  - [x]* 5.5 Property test de encadenamiento no-RF e invocación por tramo (dobles de motor)
    - **Property 6 y 7: Encadenamiento usa el cierre anterior; los 4 motores se invocan por tramo**
    - **Validates: Requirements 4.1, 4.2**

  - [ ]* 5.6 Property test de propagación de repo/inflación al motor RF (doble de motor)
    - **Property 11: Propagación de repo e inflación al motor**
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [ ]* 5.7 Test de ejemplo de detención y reporte ante fallo de un tramo
    - **Property 8: Detención y reporte ante fallo de un tramo**
    - **Validates: Requirements 4.4**

- [ ] 6. Checkpoint - Encadenamiento multiactivo completo (backend)
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Reestructurar la UI de la pestaña Inputs
  - [ ] 7.1 Subir el `Selector_Modo` al inicio de `layout_inputs()` en `app_dash.py`
    - Mover el control `modo-proyeccion` antes de las secciones de variables; conservar default `punta_a_punta`
    - _Requirements: 2.1, 2.4, 1.4_

  - [ ] 7.2 Ampliar `render_controles_por_tramo` para emitir el grid completo de `Inputs_Por_Tramo` por tramo
    - Una columna por tramo con todas las variables de la tabla (no solo los 4 shifts RF); ids dinámicos `{'type': 'tramo-<var>', 'index': i}`
    - Regenerar columnas cuando cambia el número de tramos; defaults por campo cuando vacío
    - _Requirements: 1.1, 1.2, 1.3, 2.2, 2.3_

  - [x] 7.3 Añadir el `Boton_Consolidado` único y cablearlo a `correr_escenario_consolidado`
    - Un solo botón que ejecuta RF + Derivados + RV + Alternativos + Créditos en el modo seleccionado; leer las variables por tramo como `State({'type':'tramo-<var>','index':ALL})`
    - _Requirements: 3.1, 3.4_

  - [x] 7.4 Eliminar los botones individuales por activo y sus callbacks
    - Retirar `btn-proy-alt`, `btn-proy-cred`, `btn-proy-rv` (o equivalentes) de la UI y desactivar/retirar sus callbacks sin dejar `Output`/`Input` colgados que rompan el arranque
    - _Requirements: 3.2, 3.3, 9.2, 9.3_

  - [x]* 7.5 Unit tests de UI (grid por tramo, botón único, arranque sin callbacks colgados)
    - Assert que en `por_tramos` se genera una columna por tramo; que el botón único dispara la corrida en ambos modos; que el dashboard importa sin errores tras eliminar los botones
    - _Requirements: 2.2, 3.1, 3.4, 9.3_

- [ ] 8. Integración y verificación consolidada
  - [ ] 8.0 Exportar la discriminación del efecto moneda al Excel de output
    - Añadir a la hoja de activos internacionales las columnas `VM inicial (COP)`, `TRM inicio`, `TRM cierre`, `VM inicial (USD)`, `PyG total (COP)`, `Efecto moneda (COP)`, `Efecto retorno (COP)` por tramo, más el total del horizonte por activo (desde `EfectoMonedaTramo` de 5.8)
    - Localizar la función de export a Excel existente y extenderla sin romper las hojas actuales
    - _Requirements: 11.3, 11.4, 11.5, 11.6_

  - [x]* 8.1 Test de integración con la tabla del usuario (Ago-Dic 2026, 2027, 2028)
    - Correr el flujo por tramos con dobles/valores de la tabla; assert que el tramo parcial capitaliza EA a 5/12, que los 4 activos se encadenan (cierre → base siguiente) y que el store consolida `rf_resumen`, `rv`, `alternativos`, `creditos`
    - **Property 9 y 10: Retrocompatibilidad punta a punta (salvo conteo); consolidación incluye todos los activos**
    - _Requirements: 8.1, 8.2, 8.3, 9.1, 4.2, 5.4, 10.6_

  - [x]* 8.2 Smoke tests de reuso y restricciones de arquitectura
    - Assert que los 4 motores NO fueron modificados en su firma/lógica; que la orquestación reutiliza `calcular_tramos`, `portafolio_clasificado_proy` y el store; que no hay dependencias nuevas sin pinear
    - _Requirements: 4.3, 9.2, 12.2, 12.4_

- [ ] 9. Final checkpoint - Proyección por tramos multiactivo completa
  - Ensure all tests pass, ask the user if questions arise.

## Extensión: Derivados por tramo + Snapshot completo (Req 13, 14)

- [ ] 10. Integrar derivados en el encadenamiento por tramos (Req 13)
  - [x] 10.1 Añadir `delta_fed_bps` y `delta_tesoros10y_bps` a `InputsPorTramo`
    - Dos campos nuevos (bps, default 0.0) con la misma coerción/validación que el resto (no numérico/vacío/no finito ⇒ default + `logger.warning` genérico sin volcar valor)
    - _Requirements: 13.4, 12.1_

  - [x] 10.2 Añadir las filas "Δ Fed (bps)" (`tramo-fed`) y "Δ Tesoros 10Y (bps)" (`tramo-tesoros10y`) al grid por tramo
    - Ampliar `render_controles_por_tramo` con ambas variables, una celda por tramo, ids dinámicos `{'type':'tramo-fed','index':i}` y `{'type':'tramo-tesoros10y','index':i}`; `step='any'`; default 0
    - _Requirements: 13.4, 1.1, 1.2_

  - [x] 10.3 Cablear las dos nuevas variables al callback consolidado
    - Añadir los `State({'type':'tramo-fed','index':ALL})` y `State({'type':'tramo-tesoros10y','index':ALL})` a `correr_escenario_consolidado` y a la construcción de `InputsPorTramo` por tramo (firma + parsing), preservando orden/consistencia con el resto de variables por tramo
    - _Requirements: 13.4_

  - [x] 10.4 Cambio quirúrgico retrocompatible en `valorar_portafolio_derivados` (Opción B, carga inyectable)
    - Añadir parámetro opcional `posiciones_iniciales: dict | None`: si viene (`{'fwds','opts','swaps'}` DataFrames), el motor USA ese plano en vez de `_cargar_posiciones`/`_cargar_swaps`; si es `None`, comportamiento IDÉNTICO al actual (lee `hist_*` reales)
    - Añadir parámetro opcional `devolver_plano_cierre: bool = False`: si `True`, incluir en el dict de retorno `plano_cierre = {'fwds','opts','swaps'}` con las posiciones VIVAS al `fecha_fin` (vencimiento > fecha_fin) extraídas de los planos ya proyectados/renovados (`fwds_plan`/`opts_plan`/`swaps_base`)
    - NO tocar la lógica de renovación (`_renovar_fwds`/`_renovar_opciones`), valoración por corte, ni construcción de `metodo_params`. Solo la CAPA DE CARGA y el retorno opcional
    - _Requirements: 13.3, 13.8_

  - [x] 10.5 Crear los helpers de persistencia del plano de derivados en tablas `_proy` (patrón RF)
    - En `scripts/proyeccion_tramos.py` (o módulo análogo): `asegurar_tablas_derivados_proy(conn)` (CREATE TABLE IF NOT EXISTS de `hist_forwards_proy`/`hist_opciones_proy`/`hist_swaps_proy`), `limpiar_planos_derivados_proy(conn)`, `persistir_plano_derivados_proy(conn, plano, fecha_corte)` y `cargar_plano_derivados_proy(conn, fecha_corte) -> {'fwds','opts','swaps'}`
    - Idempotente por corte, parametrizado; NUNCA escribir en las tablas `hist_*` reales
    - _Requirements: 13.3, 13.9, 12.2_

  - [x] 10.6 Implementar `_correr_derivados_tramo(...)` con encadenamiento del plano (Opción B) en `app_dash.py`
    - `fecha_corte`=inicio del tramo, `fecha_fin`=`Fecha_Corte_Tramo`; `nivel_actual`=primer punto de la senda; `senda_trm`=senda interpolada del tramo (D4)
    - `metodo_params` con `Deltas_Curvas_Tramo` (shift paralelo TES `bps/10000` por nodo; `delta_fed_bps`/`delta_10t_bps` directos)
    - Tramo 0: `posiciones_iniciales=None` (lee `hist_*`). Tramo i>0: `posiciones_iniciales`=`cargar_plano_derivados_proy` del corte anterior. Siempre `devolver_plano_cierre=True`
    - `calcular_pyg` para el PyG del tramo; devolver `{'pyg': [...], 'vm_cierre': {...}, 'plano_cierre': {...}}`; respetar `DERIVADOS_DISPONIBLE`
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.8_

  - [x] 10.7 Invocar `_correr_derivados_tramo` desde `_ejecutar_escenario_por_tramos`, persistir el plano de cierre y consolidar
    - `limpiar_planos_derivados_proy` al inicio de la corrida; por tramo, tras correr derivados persistir su `plano_cierre` con `persistir_plano_derivados_proy(conn, plano, fecha_corte)`; pasar el plano del corte anterior como `posiciones_iniciales` del siguiente
    - En el último tramo, poblar la clave `derivados` del store con el PyG (reemplazando el `None` actual)
    - Manejo de error por tramo: si derivados falla, detener e informar "Derivados" + `Fecha_Corte_Tramo`, sin exponer fin de horizonte
    - _Requirements: 13.1, 13.6, 13.7_

  - [ ]* 10.8 Property test: retrocompatibilidad del motor y captura del plano de cierre
    - **Property 22: sin `posiciones_iniciales` el motor produce el MISMO resultado que hoy; con `devolver_plano_cierre` el plano de cierre solo contiene posiciones vivas (venc > fecha_fin)**
    - **Validates: Requirements 13.8, 13.3**

  - [ ]* 10.9 Property test: encadenamiento del plano (cierre del tramo i = inicio del tramo i+1)
    - **Property 21: el `posiciones_iniciales` del tramo i+1 es EXACTAMENTE el `plano_cierre` persistido del tramo i (ida y vuelta por las tablas `_proy`)**
    - **Validates: Requirements 13.3, 13.9**

  - [ ]* 10.10 Property test: construcción de `Deltas_Curvas_Tramo`
    - **Property 17: Deltas de curva del tramo se construyen y pasan correctamente**
    - **Validates: Requirements 13.4, 13.5**

  - [ ]* 10.11 Tests [EJ]: consolidación incluye derivados y detención ante fallo
    - **Property 18 y 20: Consolidación incluye derivados; detención y reporte ante fallo de derivados**
    - **Validates: Requirements 13.6, 13.2, 13.7**

- [ ] 11. Persistir el Snapshot completo de todos los activos por tramo (Req 14)
  - [x] 11.1 Inspeccionar el resultado de cada motor no-RF y de derivados para mapear a `_COLUMNAS_PROY`
    - Determinar qué columnas del resultado de RV/Alt (VM de cierre por posición) y de derivados (`valor_mercado` por posición de Opciones/Forwards/Swaps) se mapean al núcleo de la allowlist (`id_posicion`, `especie`/`emisor`, `moneda`, `valor_mercado`, `clase_origen`, `asset_class_definitivo`, `portafolio`, `compania`)
    - Ampliar `_COLUMNAS_PROY` SOLO si falta una columna imprescindible; documentar cualquier ampliación
    - _Requirements: 14.2, 14.3_

  - [x] 11.2 Implementar `_construir_snapshot_completo_tramo(df_proy_rf, estado_encadenado, resultado_derivados)`
    - Unir las filas RF de cierre (sin alterarlas) con filas RV/Alt (VM de cierre) y derivados (VM de cierre), cada una con su `clase_origen`; columnas no aplicables ⇒ NULL
    - _Requirements: 14.1, 14.2, 14.6, 14.7_

  - [x] 11.3 Persistir el snapshot completo por corte reutilizando `persistir_snapshot_proy`
    - Llamar a `persistir_snapshot_proy(conn, df_completo, fecha_corte)` para cada tramo (allowlist, validación numérica, parametrizadas, idempotencia); mantener `limpiar_snapshots_proy` al inicio de la corrida
    - _Requirements: 14.1, 14.3, 14.5_

  - [x] 11.4 Verificar el invariante de reconstrucción RF
    - Confirmar que `cargar_titulos_desde_snapshot` sigue filtrando SOLO filas RF y que anexar filas no-RF NO cambia la reconstrucción de títulos del tramo siguiente
    - Se reforzó el filtro: `cargar_titulos_desde_snapshot` ahora excluye por `clase_origen` (RENTA_VARIABLE/ALTERNATIVO/DERIVADO) además del filtro por `asset_class_definitivo`
    - _Requirements: 14.4_

  - [x]* 11.5 Test [EJ]: snapshot completo con todos los activos sin romper la reconstrucción RF
    - **Property 19: Snapshot completo contiene todos los activos sin romper la reconstrucción RF**
    - **Validates: Requirements 14.1, 14.2, 14.4, 14.7**

- [ ] 12. Checkpoint final de la extensión - Derivados por tramo + snapshot completo
  - Ensure all tests pass, ask the user if questions arise.

## Correcciones post-primera-corrida (Fix 1, 2, 3 — Req 15, 16, 17)

- [ ] 13. Fix 1 — `clase_origen='TITULO'` en la reconstrucción de tramos i>0 (Req 15)
  - [x] 13.1 Añadir `'clase_origen': 'TITULO'` al dict de filas en `_titulos_a_dataframe_base` (app_dash.py)
    - Una línea; las filas RF reconstruidas de tramos i>0 quedan etiquetadas como en el tramo 0
    - _Requirements: 15.1, 15.2, 15.3_
  - [ ]* 13.2 Test: snapshot de un tramo i>0 (doble) tiene filas RF con `clase_origen='TITULO'` no nulo
    - _Requirements: 15.2_

- [ ] 14. Fix 2 — Materializar el snapshot completo no-RF por tramo (Req 16)
  - [x] 14.1 Inspeccionar la salida REAL de los motores RV/Alternativos/Derivados por tramo
    - CONFIRMADO: RV usa col `titulo`, Alternativos usa `id_posicion`, Derivados aporta `vm_cierre` como dict `{"{tipo}:{id}" → vm}`; todos con `valor_mercado`/`fecha_proyeccion`. `_filas_cierre_no_rf` mapea correctamente cada esquema
    - _Requirements: 16.2, 16.3_
  - [x] 14.2 Corregir el poblado del snapshot no-RF (causa raíz: filas descartadas por nominal NaN)
    - CAUSA RAÍZ: `persistir_snapshot_proy` descartaba toda fila con `nominal` NaN; las filas no-RF (RV/Alt/Deriv) no traen `nominal` → se vaciaba el snapshot no-RF. CORREGIDO: solo `valor_mercado` es obligatorio; `nominal` NaN se guarda como NULL sin descartar la fila. `_filas_cierre_no_rf` y `_construir_snapshot_completo_tramo` mapean las columnas reales
    - _Requirements: 16.1, 16.2, 16.3_
  - [x] 14.3 Definir fuente única por posición y evitar duplicación con el portafolio base
    - HECHO: `df_proy_rf` arranca de `portafolio_clasificado` (`df_base.copy()`), que conserva las posiciones NO-RF del base SIN proyectar: derivados (clase_origen FORWARD/OPCION/SWAP) y RV/Alt (que llegan como `clase_origen='TITULO'` con asset_class de acción/fondo). Antes de anexar las filas valoradas por los motores no-RF, `_construir_snapshot_completo_tramo` elimina de `df_proy_rf`: (a) los derivados del base (por clase_origen) y (b) las posiciones RV/Alt aportadas por los motores (por id_posicion). Cada posición no-RF queda UNA vez, con el VM de su motor. Diagnóstico `[SNAPSHOT-DEDUP]`
    - VERIFICADO (test aislado): sin duplicados; VM no-RF = valorado por motor (no el VM inicial del base); las filas RF puras intactas; y `cargar_titulos_desde_snapshot` sobre el snapshot deduplicado reconstruye SOLO las filas RF (invariante Req 14.4 preservado)
    - _Requirements: 16.4, 16.5_
  - [ ]* 14.4 Test [EJ]: el snapshot de un corte contiene filas no-RF con `clase_origen` en {DERIVADO, RENTA_VARIABLE, ALTERNATIVO}, sin duplicar y sin romper la reconstrucción RF
    - _Requirements: 16.1, 16.5, 16.6_

- [ ] 15. Fix 3 — Rendimiento del encadenamiento sin alterar la valoración (Req 17)
  - [x] 15.1 Instrumentar tiempos por motor y por tramo (medir antes de optimizar)
    - Helper `CronometroTramos` en `proyeccion_tramos_multiactivo.py`; cableado en el orquestador para RF/RV/Alt/Créditos/Derivados/Snapshot_build/Persistencia_IO por tramo; resumen con ranking al final de la corrida (al stdout del worker), sin volcar datos sensibles
    - _Requirements: 17.3_
  - [ ] 15.2 Capturar un caso de referencia reproducible (2 tramos, portafolio reducido) para validación numérica
    - Guardar el resultado actual por posición (tasa_compra, valor_tir, valor_mercado, PyG) como baseline de regresión
    - _Requirements: 17.1, 17.8_
  - [x] 15.3 Vectorizar los bucles título-por-título más costosos (según medición) SIN cambiar la valoración
    - HECHO: `_construir_df_proy_rf` reinversiones — se reemplazó el filtro lineal por reinversión (`df[df['id']==origen].iloc[0]`, O(n²)) por un índice `{id_posicion: fila}` construido UNA vez (lookup O(1)), preservando la precedencia (vivos > vencidos) y los 9 campos sobrescritos
    - VERIFICADO: test de equivalencia (misma salida celda a celda en los 3 casos: origen vivo, origen vencido, sin origen) + test de rendimiento (8000 reinversiones: 4.63s → 0.028s, ~164x; crece con el volumen). NO se agrupan títulos a TIR
    - _Requirements: 17.1, 17.2, 17.4_
  - [~] 15.4 Reducir I/O: persistencia en lote + ubicación opcional de la DB fuera del FS sincronizado
    - HECHO: `comun/rutas.py` DB_PATH configurable por `AXIS_DB_PATH` (default = ruta actual, retrocompatible); permite apuntar la DB a disco local. La DB está en `.gitignore`, no afecta el repo
    - PENDIENTE: escritura en lote del snapshot (optimización de `persistir_snapshot_proy`) y centralizar los módulos que aún definen su propia `DB_PATH` sin leer de `comun.rutas` (app_dash, gestor_informes, valoracion_derivados, etc.)
    - _Requirements: 17.5_
  - [ ]* 15.5 Test de regresión numérica (baseline) + test de tiempos (evidencia de mejora)
    - El caso de referencia produce el MISMO resultado antes/después; el test de tiempos evidencia la reducción
    - _Requirements: 17.1, 17.6, 17.8_

  - [x] 15.6 Optimizar el cuello de botella REAL del motor RF (hallazgo de profiling)
    - Diagnóstico confirmado por `profile`: el cuello NO era el doble bucle genérico sino la bisección TIR de bonos IPC a venc/DV (`motor_proyeccion.py` ~1395-1415): `_dias_nl365` se llamaba **36.121.955 veces ≈ 167.7s (85% del tiempo)** porque el bucle de 500 iteraciones recalculaba los días por flujo en cada iteración, pese a que los días NO dependen de la tasa `_ym`
    - HECHO: se precalculan los pares `(monto, t_años)` UNA sola vez antes de la bisección (y análogo para el VP de fin de mes); la bisección solo evalúa la potencia. Cambio matemáticamente idéntico (mismas fechas, montos y fórmula)
    - VERIFICADO: `_dias_nl365` bajó de 36.121.955 → **303.735 llamadas (~119x menos), 0.978s**; motor RF (3 meses) corre en 7.6s. Equivalencia estricta confirmada con `tools/equivalencia_motor_rf.py check` → "OK EQUIVALENTE (tol=1e-6)"
    - Arnés de equivalencia + profiling creado: `tools/equivalencia_motor_rf.py` (baseline/check/profile, env `AXIS_EQUIV_MESES`); baseline en `tools/baseline_motor_rf.json`
    - _Requirements: 17.1, 17.2, 17.4_

- [ ] 16. Checkpoint final de correcciones - Fix 1/2/3 verificados
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tareas marcadas con `*` son opcionales (tests) y pueden omitirse para un MVP más rápido; las tareas núcleo nunca son opcionales.
- Cada tarea referencia requisitos granulares para trazabilidad.
- Los motores NO cambian su LÓGICA interna. ÚNICA excepción acotada (Opción B, Req 13): `valorar_portafolio_derivados` recibe dos parámetros opcionales retrocompatibles en su CAPA DE CARGA (`posiciones_iniciales`, `devolver_plano_cierre`); sin ellos, el comportamiento es idéntico al actual. La orquestación, la UI y las tablas `_proy` son el resto de la superficie de cambio (más la corrección puntual de `_meses_entre` y del conteo punta a punta).
- Todo acceso a SQLite usa consultas parametrizadas; los inputs por tramo son entrada NO confiable (coerción a default + warning genérico, sin volcar valores).
- Entorno: procesos background con salida a archivo temp; suprimir `HealthCheck.too_slow` cuando sea artefacto del entorno; NO usar la DB real `portafolio.db` (DB temporal). No borrar archivos temporales hasta el final de TODA la spec.

## Task Dependency Graph

> Olas de sub-tareas hoja. La corrección de conteo (1.x) es base de todo. Las primitivas puras (3.x) son independientes entre sí y del backend de encadenamiento. `5.x` depende de 3.x. La UI (7.x) depende de 5.x (el botón consolidado debe invocar el backend ya extendido). Los tests siguen al código que ejercitan.

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["3.5", "3.6", "3.7", "5.1"] },
    { "id": 4, "tasks": ["5.2"] },
    { "id": 5, "tasks": ["5.3"] },
    { "id": 6, "tasks": ["5.4", "5.5", "5.6", "5.7", "5.8"] },
    { "id": 7, "tasks": ["5.9", "8.0", "7.1", "7.2"] },
    { "id": 8, "tasks": ["7.3", "7.4"] },
    { "id": 9, "tasks": ["7.5", "8.1", "8.2"] },
    { "id": 10, "tasks": ["10.1", "10.4", "10.5", "11.1"] },
    { "id": 11, "tasks": ["10.2", "10.6", "11.2"] },
    { "id": 12, "tasks": ["10.3", "10.7", "11.3", "11.4"] },
    { "id": 13, "tasks": ["10.8", "10.9", "10.10", "10.11", "11.5"] }
  ]
}
```

> **Extensión (olas 10–13):** los campos y filas de inputs (`10.1`, `10.2`) y la inspección de columnas del snapshot (`11.1`) no dependen del backend de derivados. `_correr_derivados_tramo` (`10.4`) depende de `InputsPorTramo` extendido (`10.1`) y de la senda por tramo (3.x). La integración en el orquestador (`10.5`) y la persistencia del snapshot completo (`11.3`) dependen de que existan el adaptador de derivados (`10.4`), el snapshot builder (`11.2`) y el orquestador multiactivo ya extendido (`5.3`). Los tests siguen al código que ejercitan.
