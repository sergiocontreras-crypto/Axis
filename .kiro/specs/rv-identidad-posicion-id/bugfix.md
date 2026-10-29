# Documento de Requisitos del Bugfix

## Introduction

En el modo de proyección por tramos (multiactivo), el motor de Renta Variable (RV) y todo su flujo de encadenamiento y de escritura del snapshot `portafolio_clasificado_proy` identifican cada posición por `titulo` (que es igual a `especie`). La especie NO es un identificador único: en `portafolio.db` existen, por ejemplo, 59 posiciones con especie "AO ISA", 40 "PEI" y 5 "CIBEST". El identificador único correcto es `id_posicion`, columna real de `portafolio_clasificado` que los flujos de Renta Fija (RF) y de Alternativos ya utilizan de extremo a extremo.

El uso de `titulo`/`especie` como clave de identidad produce cuatro síntomas confirmados con datos reales:

1. **Colapso por especie:** la operación `groupby("titulo").idxmax()` colapsa N posiciones de una misma especie a una sola fila (siempre 41 filas `RENTA_VARIABLE` por corte en 2026, 2027 y 2028, pese a existir decenas de posiciones por especie).
2. **Snapshot con identificador mal poblado:** las filas con `clase_origen='RENTA_VARIABLE'` guardan la ESPECIE en la columna `id_posicion` (p. ej. `('AO ISA', None, ...)`, `('AP GRUPO CIBEST', None, ...)`) y dejan `especie=None`.
3. **Deduplicación fallida y duplicación:** la deduplicación de fuente única por posición compara el `id_posicion` numérico de las filas base (clase_origen `TITULO`) contra el string de especie de las filas `RENTA_VARIABLE`, obteniendo 0 coincidencias; cada acción aparece dos veces (una `TITULO` sin proyectar y una `RENTA_VARIABLE` colapsada), y además aparecen 421 filas con `clase_origen` NULL.
4. **Inflación de P&G de RV Local en el último tramo:** el factor de encadenamiento `VM_cierre_previo / VM_inicial_original` se calcula sobre una base agregada por especie (`groupby` sobre `titulo`) y se aplica incorrectamente a N filas con denominadores que no corresponden, lo que explota el resultado y se compone a lo largo de los tramos.

La causa raíz es única: RV usa `titulo`/`especie` (no único) como identificador en lugar de `id_posicion`. El patrón correcto ya existe y está probado en el flujo de Alternativos, que usa `id_posicion` de extremo a extremo.

Aclaración de alcance: `RENTA_VARIABLE` NO es una categoría de input. `portafolio_clasificado` solo contiene `clase_origen` de tipo `TITULO`, `OPCION`, `FORWARD` y `SWAP`. Los literales `RENTA_VARIABLE`, `ALTERNATIVO` y `DERIVADO` son valores que el código escribe por diseño en el snapshot `portafolio_clasificado_proy` (Req 14.2 de la spec de snapshot por tramos); eso NO cambia. Lo que se corrige es exclusivamente la IDENTIDAD de posición de RV.

## Bug Analysis

### Bug Condition (Condición del Bug)

La condición del bug se define formalmente sobre cualquier consumidor del flujo de proyección por tramos de RV que identifique una posición por `titulo`/`especie` en lugar de `id_posicion`.

```pascal
FUNCTION isBugCondition(X)
  INPUT:  X = conjunto de posiciones de RV de un corte que fluye por el modo por tramos
  OUTPUT: boolean

  // La condición se cumple cuando la clave de identidad usada por el flujo RV
  // es `titulo`/`especie` (no único), de modo que dos o más posiciones distintas
  // comparten la misma clave y se colapsan o confunden.
  RETURN EXISTS p1, p2 IN X TAL QUE
           p1.id_posicion <> p2.id_posicion
           AND p1.especie = p2.especie
           AND clave_identidad_usada_por_RV(X) = "titulo"
END FUNCTION
```

```pascal
// Property: Fix Checking — identidad única por posición en el flujo RV
FOR ALL X WHERE isBugCondition(X) DO
  resultado <- F'(X)   // flujo RV por tramos corregido
  ASSERT numero_filas_RENTA_VARIABLE(resultado) = numero_posiciones_RV(X)
  ASSERT PARA TODA fila r EN resultado CON r.clase_origen = "RENTA_VARIABLE":
           es_numerico(r.id_posicion) AND r.id_posicion = id_posicion_real(r)
  ASSERT no_hay_duplicados_TITULO_vs_RENTA_VARIABLE(resultado)
  ASSERT no_hay_filas_con_clase_origen_NULL_espurias(resultado)
END FOR
```

- **F**: el flujo RV por tramos actual (sin corregir), que usa `titulo`/`especie` como clave.
- **F'**: el flujo RV por tramos corregido, que usa `id_posicion` como clave y conserva `titulo`/`especie` como columna de etiqueta.

Contraejemplos reales confirmados en `portafolio_clasificado_proy`:
- 41 filas `RENTA_VARIABLE` por corte, constantes en 2026/2027/2028, pese a decenas de posiciones por especie (colapso por `groupby("titulo").idxmax()`).
- Filas `RENTA_VARIABLE` con `id_posicion='AO ISA'`, `id_posicion='AP GRUPO CIBEST'` y `especie=None`.
- Deduplicación de fuente única con 0 coincidencias (id numérico del base vs string de especie) con cada acción duplicada y 421 filas con `clase_origen` NULL.
- Inflación compuesta de P&G de RV Local en el tramo 2028.

### Current Behavior (Defect)

1.1 WHEN se ejecuta la proyección por tramos de RV y existen varias posiciones distintas (`id_posicion` diferente) con la misma `especie` THEN el flujo las identifica por `titulo`/`especie` y las trata como una sola posición.

1.2 WHEN `cargar_portafolio_rv` ejecuta su SELECT sobre `portafolio_clasificado` (scripts/proyeccion_renta_variable.py ~L119-128) THEN el sistema selecciona `especie AS titulo` SIN incluir la columna `id_posicion`, de modo que la identidad única nunca entra al flujo.

1.3 WHEN `proyectar_bloque` emite las filas de proyección T0 (~L219-230) y de cortes (~L253-264) THEN el sistema produce filas con `titulo/compania/tipo_rv/moneda` SIN columna `id_posicion`.

1.4 WHEN `_filas_cierre_no_rf` procesa las filas de RV para el snapshot (app_dash.py ~L8520) THEN el sistema ejecuta `groupby('titulo').idxmax()`, colapsando N posiciones de una especie a 1 fila (41 filas `RENTA_VARIABLE` por corte) y escribe `id_posicion = str(especie)`.

1.5 WHEN se escriben las filas `clase_origen='RENTA_VARIABLE'` en `portafolio_clasificado_proy` THEN el sistema guarda la especie (string) en la columna `id_posicion` y deja `especie=None` (p. ej. `('AO ISA', None, ...)`).

1.6 WHEN la deduplicación de fuente única por posición (app_dash.py ~L8558-8572) compara el `id_posicion` numérico de las filas base `TITULO` contra el string de especie de las filas `RENTA_VARIABLE` THEN el sistema obtiene 0 coincidencias, deja la fila `TITULO` sin proyectar junto a la fila `RENTA_VARIABLE` colapsada (cada acción duplicada) y produce 421 filas con `clase_origen` NULL.

1.7 WHEN `_aplicar_encadenamiento_resultado` (app_dash.py ~L7210-7247) y `_correr_rv_tramo` (~L7339-7397) encadenan con `col_id="titulo"` (~L7386) y realinean el P&G de enero con `col_id="titulo"` (~L7395) THEN el sistema calcula el factor `VM_cierre_previo / VM_inicial_original` sobre una base agregada por especie y lo aplica a filas con denominadores que no corresponden, inflando el P&G de RV Local y componiéndose por tramo (visible en 2028).

1.8 WHEN la tabla de dispatch por clase (app_dash.py ~L8151), el efecto moneda `calcular_efecto_moneda_tramo` (~L8180) y `extraer_vm_cierre` (~L8189) operan sobre RV THEN el sistema usa `col_id="titulo"`, de modo que `EstadoEncadenado.vm_rv_por_posicion` y `EfectoMonedaTramo.id_posicion` quedan indexados y poblados por especie en lugar de por `id_posicion`.

1.9 WHEN `extraer_vm_cierre` (scripts/proyeccion_tramos_multiactivo.py ~L1039, groupby idxmax ~L1091) y `calcular_efecto_moneda_tramo` (~L1237) procesan RV con su `col_id` por defecto THEN el sistema usa `"titulo"` y colapsa o confunde posiciones distintas que comparten especie.

### Expected Behavior (Correct)

2.1 WHEN se ejecuta la proyección por tramos de RV y existen varias posiciones distintas con la misma `especie` THEN el sistema SHALL identificar cada posición por su `id_posicion` único, tratándolas como posiciones independientes.

2.2 WHEN `cargar_portafolio_rv` ejecuta su SELECT sobre `portafolio_clasificado` THEN el sistema SHALL incluir la columna `id_posicion` en la consulta (manteniendo el SQL de solo lectura y parametrizado, sin concatenación de input) y conservar `especie`/`titulo` como columna de etiqueta.

2.3 WHEN `proyectar_bloque` emite las filas de proyección T0 y de cortes THEN el sistema SHALL incluir `id_posicion` en cada fila emitida, replicando el patrón de Alternativos (proyeccion_alternativos.py:268,290), y SHALL conservar `titulo`/`especie` como columna de etiqueta.

2.4 WHEN `_filas_cierre_no_rf` procesa las filas de RV para el snapshot THEN el sistema SHALL agrupar o seleccionar por `id_posicion`, produciendo tantas filas `RENTA_VARIABLE` como posiciones reales existan (sin colapso por especie).

2.5 WHEN se escriben las filas `clase_origen='RENTA_VARIABLE'` en `portafolio_clasificado_proy` THEN el sistema SHALL guardar el `id_posicion` numérico real en la columna `id_posicion` y SHALL poblar `especie` con el valor de especie correspondiente (no `None`).

2.6 WHEN la deduplicación de fuente única por posición compara filas base `TITULO` contra filas `RENTA_VARIABLE` THEN el sistema SHALL comparar `id_posicion` numérico contra `id_posicion` numérico, eliminando las filas `TITULO` sin proyectar que correspondan a posiciones de RV ya proyectadas, sin duplicados y sin filas `clase_origen` NULL espurias.

2.7 WHEN se encadena y se realinea el P&G de enero de RV entre tramos THEN el sistema SHALL calcular y aplicar el factor `VM_cierre_previo / VM_inicial_original` por `id_posicion`, de modo que cada posición use su propio denominador y el P&G de RV Local no se infle ni se componga incorrectamente por tramo.

2.8 WHEN la tabla de dispatch por clase, el efecto moneda y `extraer_vm_cierre` operan sobre RV THEN el sistema SHALL usar `id_posicion` como `col_id`, de modo que `EstadoEncadenado.vm_rv_por_posicion` y `EfectoMonedaTramo.id_posicion` queden indexados y poblados por `id_posicion`.

2.9 WHEN `extraer_vm_cierre` y `calcular_efecto_moneda_tramo` procesan RV THEN el sistema SHALL usar `id_posicion` como clave (los nuevos parámetros de clave SHALL añadirse con valor por defecto retrocompatible, sin defaults hardcodeados de negocio).

### Unchanged Behavior (Regression Prevention)

3.1 WHEN se proyectan posiciones de Alternativos, Derivados o Renta Fija THEN el sistema SHALL CONTINUE TO usar su propia clave de identidad (Alternativos y RF ya usan `id_posicion`) sin cambio alguno de comportamiento.

3.2 WHEN los exports consumen la salida de RV — `tools/export_resumen_tramos.py` (~L194-216, prorrateo de P&G por ramo vía join `df_clasif["especie"] == str(r[col_id])`) y `tools/multiescenarios.py` (~L756,816, lectura de `titulo` como etiqueta y máscara PEI) THEN el sistema SHALL CONTINUE TO exponer la columna de etiqueta `titulo`/`especie` presente en la salida de RV, aunque la CLAVE de identidad pase a ser `id_posicion`.

3.3 WHEN se invoca `proyectar_renta_variable` (scripts/proyeccion_renta_variable.py ~L273-399) THEN el sistema SHALL CONTINUE TO respetar la firma pública actual sin añadir parámetros requeridos nuevos (el contrato de firma de tests/test_tramos_multiactivo_smoke.py:47-54 SHALL seguir pasando); añadir `id_posicion` a la SALIDA no cambia la firma.

3.4 WHEN se ejecuta la proyección en modo punta a punta (no por tramos) THEN el sistema SHALL CONTINUE TO comportarse exactamente igual que antes del fix.

3.5 WHEN el flujo por tramos escribe el snapshot `portafolio_clasificado_proy` THEN el sistema SHALL CONTINUE TO escribir los literales de `clase_origen` `RENTA_VARIABLE`, `ALTERNATIVO` y `DERIVADO` por diseño (Req 14.2); solo cambia que `id_posicion` lleve el id numérico real y que la deduplicación funcione.

3.6 WHEN `tools/exportar_excel_tramos.py` (~L78-82) vuelca `portafolio_clasificado_proy` THEN el sistema SHALL CONTINUE TO volcar la tabla tal cual, corrigiéndose automáticamente al corregir el writer del snapshot.

3.7 WHEN se ejecutan las suites de regresión del modo por tramos — `tests/test_tramos_multiactivo_integracion.py` (factory `_doble_no_rf_factory` para RV ~L247,316,395,442 y asserts `vm_rv_por_posicion` ~L296,330), `tests/test_tramos_multiactivo_orquestador.py` (~L319,328) y `tests/test_tramos_multiactivo_smoke.py` (~L47-54) THEN el sistema SHALL CONTINUE TO pasar dichas suites, actualizando en lockstep las llamadas RV de la factory de `col_id="titulo"` a `col_id="id_posicion"` sin romper los contratos existentes.

3.8 WHEN se escribe o modifica código del fix THEN el sistema SHALL CONTINUE TO cumplir las reglas del proyecto: sin defaults hardcodeados de negocio, SQL de solo lectura parametrizado (sin concatenación de input), logs genéricos sin datos sensibles, y firmas retrocompatibles (nuevos parámetros con valor por defecto).
