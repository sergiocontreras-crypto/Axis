# Capitalización de la UVR por tramos — Diseño de Bugfix

## Overview

En el flujo de proyección **por tramos** (`_ejecutar_escenario_por_tramos` en `app_dash.py`),
la UVR se fija una sola vez antes del loop de tramos (`_uvr = float(uvr_t0) if uvr_t0 else 411.0`)
y se pasa al motor RF solo como `valor_uvr=_uvr`, sin `valor_uvr_cierre`. Como el motor construye
su senda interna `_senda_uvr` interpolando linealmente entre `valor_uvr` y `valor_uvr_cierre`
(y cae a `valor_uvr` cuando el cierre es `None`), la senda queda **plana** en todos los tramos.
Resultado: la UVR no se capitaliza con la inflación de cada tramo y el valor nominal en COP de
los títulos UVR se subestima progresivamente en horizontes de varios años.

El arreglo es de **orquestación**, no de valoración. La estrategia:

1. Calcular por tramo la `uvr_inicio` (UVR al arranque del tramo) y la `uvr_cierre`
   (UVR al cierre del tramo), capitalizando `uvr_inicio` con la inflación anual del tramo
   (`InputsPorTramo.inflacion_ea`), escalada por la fracción de año que cubre el tramo.
2. Pasar **ambos** valores al motor RF (`valor_uvr=uvr_inicio`, `valor_uvr_cierre=uvr_cierre`),
   para que su senda interna evolucione en vez de quedar plana.
3. Encadenar la UVR entre tramos añadiendo `uvr_cierre` al `EstadoEncadenado` existente,
   igual que ya se hace con `trm_cierre`, `repo_cierre` e `inflacion_cierre`.
4. Eliminar el hardcode `411.0` y abortar con un error verificable cuando no haya UVR t0 real,
   consistente con el patrón ya aplicado a la TRM.

No se toca el motor RF (`proyectar_portafolio`, `valorar_titulo`, construcción de `_senda_uvr`)
ni el flujo "punta a punta" (`_calcular_uvr_cierre_desde_inflacion` y el paso de
`valor_uvr`/`valor_uvr_cierre` en el callback de escenario único).

## Glossary

- **Bug_Condition (C)**: La condición que dispara el bug — modo `por_tramos`, con títulos UVR
  en el portafolio y al menos un tramo con inflación distinta de cero, donde la UVR queda plana.
- **Property (P)**: El comportamiento deseado — la UVR crece dentro de cada tramo según la
  inflación del tramo, se encadena entre tramos (cierre de un tramo = inicio del siguiente) y
  no usa el hardcode `411.0`.
- **Preservation**: El comportamiento existente que no debe cambiar — flujo punta a punta,
  títulos no-UVR, la lógica interna del motor RF y el encadenamiento ya existente de TRM/repo/inflación.
- **F**: La orquestación actual (UVR plana, hardcode `411.0`).
- **F'**: La orquestación corregida (UVR capitalizada y encadenada por tramo).
- **`_ejecutar_escenario_por_tramos`**: Función en `app_dash.py` (~línea 7586) que orquesta la
  proyección por tramos encadenando corridas anuales del motor RF.
- **`proyectar_portafolio`**: Función en `scripts/motor_proyeccion.py` que corre el motor RF.
  Acepta `valor_uvr` y `valor_uvr_cierre` y construye internamente `_senda_uvr` por
  interpolación lineal mes a mes.
- **`_senda_uvr`**: Lista interna del motor RF `[uvr_t0 ... uvr_fin]`; `_uvr_fin = valor_uvr_cierre if valor_uvr_cierre else valor_uvr`.
  Si no se pasa `valor_uvr_cierre`, queda plana.
- **`EstadoEncadenado`**: Dataclass en `scripts/proyeccion_tramos_multiactivo.py` que arrastra el
  estado de cierre de un tramo al siguiente (`trm_cierre`, `repo_cierre`, `inflacion_cierre`, VMs).
- **`InputsPorTramo.inflacion_ea`**: Inflación anual del tramo en decimal EA (p. ej. `0.05`).
- **`interpolar_nivel_tramo`**: Helper en `proyeccion_tramos_multiactivo.py` que interpola un
  nivel macro linealmente dentro del tramo (índice 0 = inicio, índice `n_meses` = cierre).

## Bug Details

### Bug Condition

El bug se manifiesta cuando el modo de proyección es `por_tramos`, existen títulos UVR en el
portafolio y al menos un tramo tiene inflación distinta de cero (que debería capitalizar la UVR).
En ese caso, la orquestación fija la UVR una sola vez antes del loop, no la recalcula ni la
encadena, y la pasa al motor sin `valor_uvr_cierre`, dejando la senda interna de UVR plana
(`uvr_t0 == uvr_fin`).

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X de tipo EscenarioProyeccion
  OUTPUT: boolean

  RETURN X.modo_proyeccion = "por_tramos"
         AND existe_titulo_UVR(X.portafolio)
         AND existe_tramo_con_inflacion_no_cero(X.tramos)
END FUNCTION
```

### Examples

- **Horizonte de 3 años, inflación 5% EA por tramo, UVR t0 = 400**: hoy la UVR permanece en 400
  los tres años (senda plana); debería crecer hasta ≈ 463 (400 × 1.05³) al cierre del tercer tramo.
- **Primer tramo parcial (~5 meses), inflación 6% EA**: hoy la UVR queda plana en los 5 meses;
  debería crecer `(1 + 0.06) ^ (5/12) ≈ 1.0245`, es decir ≈ 2.45% en ese tramo parcial.
- **Encadenamiento entre tramos**: hoy el inicio del tramo 2 vuelve a ser `_uvr` (el valor global
  fijo); debería ser exactamente la UVR de cierre del tramo 1.
- **Sin UVR t0 real (uvr_t0 vacío)**: hoy el sistema usa el hardcode `411.0` y proyecta con un
  dato inventado; debería abortar con un error verificable (edge case).
- **Inflación de tramo = 0** (comportamiento correcto preservado): la UVR debe quedar constante en
  ese tramo, equivalente al comportamiento plano para ese tramo específico.

## Expected Behavior

### Preservation Requirements

**Comportamientos que NO deben cambiar:**
- El flujo **punta a punta** debe seguir calculando la UVR de cierre con
  `_calcular_uvr_cierre_desde_inflacion` y pasando `valor_uvr`/`valor_uvr_cierre` exactamente
  como hoy (Req 3.1).
- La valoración de títulos **no-UVR** (COP, IPC, moneda extranjera, etc.) en cualquier modo debe
  producir los mismos resultados (Req 3.2).
- La **lógica interna del motor RF** (`valorar_titulo` y la construcción de `_senda_uvr` por
  interpolación lineal) no se modifica; el arreglo es solo de orquestación (Req 3.3).
- El **encadenamiento existente** de TRM, repo e inflación vía `EstadoEncadenado` debe
  mantenerse idéntico; el nuevo encadenamiento de UVR se suma sin alterar los demás (Req 3.4).
- En modo `por_tramos` **sin títulos UVR**, los resultados de proyección deben ser iguales a los
  de antes del arreglo (Req 3.5).
- Cuando la inflación de un tramo sea **exactamente 0**, la UVR debe quedar constante en ese tramo
  (Req 3.6).

**Alcance:**
Toda entrada que NO cumpla la condición de bug (modo punta a punta; portafolios sin títulos UVR;
tramos con inflación cero; títulos no-UVR) debe quedar completamente inalterada por el arreglo.

**Nota:** El comportamiento correcto esperado para las entradas con condición de bug está definido
en la sección Correctness Properties (Property 1).

## Hypothesized Root Cause

Con base en el análisis del código verificado, las causas del defecto son:

1. **UVR fijada una sola vez fuera del loop**: en `_ejecutar_escenario_por_tramos` la línea
   `_uvr = float(uvr_t0) if uvr_t0 else 411.0` (~línea 7747, antes del loop de tramos) establece
   un único valor de UVR que nunca se recalcula por tramo.

2. **Falta `valor_uvr_cierre` en la invocación del motor**: la llamada a `proyectar_portafolio`
   dentro del loop pasa solo `valor_uvr=_uvr`. Como el motor hace
   `_uvr_fin = valor_uvr_cierre if valor_uvr_cierre else valor_uvr`, la senda `_senda_uvr` queda
   plana (`uvr_t0 == uvr_fin`).

3. **UVR ausente del mecanismo de encadenamiento**: el `EstadoEncadenado` construido entre tramos
   (~líneas 8023-8032) arrastra `trm_cierre`, `repo_cierre` e `inflacion_cierre`, pero **no**
   un `uvr_cierre`. Por eso el inicio de cada tramo no toma la UVR de cierre del tramo anterior.

4. **Default hardcodeado `411.0`**: el fallback `else 411.0` inyecta un dato inventado cuando no
   hay UVR t0 real, inconsistente con el patrón de la TRM, que aborta con un error verificable
   (`❌ No hay TRM implícita para la fecha de corte...`) cuando falta el dato.

## Correctness Properties

Property 1: Bug Condition — La UVR se capitaliza y encadena por tramos

_For any_ entrada donde la condición de bug se cumple (`isBugCondition` devuelve true), la función
corregida F' SHALL hacer crecer la UVR dentro de cada tramo según la inflación anual del tramo
(`inflacion_ea`), escalando el primer tramo por su fracción de año, encadenando la UVR de cierre
de cada tramo como UVR de inicio del siguiente, garantizando que la UVR no decrezca cuando la
inflación es >= 0, y sin usar el hardcode `411.0`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

Property 2: Preservation — No cambia nada fuera de la condición de bug

_For any_ entrada donde la condición de bug NO se cumple (`isBugCondition` devuelve false —
modo punta a punta, portafolios sin títulos UVR, tramos con inflación cero, títulos no-UVR), la
función corregida F' SHALL producir exactamente el mismo resultado que la función original F,
preservando el flujo punta a punta, la valoración de títulos no-UVR, la lógica interna del motor
RF y el encadenamiento existente de TRM/repo/inflación.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

Asumiendo que el análisis de causa raíz es correcto, todos los cambios están contenidos en la
orquestación. **No** se modifica `scripts/motor_proyeccion.py` ni el flujo punta a punta.

**Archivo**: `app_dash.py`

**Función**: `_ejecutar_escenario_por_tramos`

**Cambios específicos**:

1. **Eliminar el hardcode y abortar sin UVR t0 real** (Req 2.6, 1.4):
   Reemplazar `_uvr = float(uvr_t0) if uvr_t0 else 411.0` por una guarda que, si no hay `uvr_t0`
   válido (> 0 y finito), devuelva el mismo contrato de error que la TRM:
   ```python
   if not uvr_t0:
       return (None, html.Span(
           "❌ No hay UVR t0 para la fecha de corte; ingresa la UVR vigente.",
           style={'color': 'red'}))
   _uvr_t0 = float(uvr_t0)
   ```
   Mantener `_uvr_t0` como nivel de inicio del PRIMER tramo (igual que `_trm_t0` para la TRM).

2. **Calcular `uvr_inicio` y `uvr_cierre` por tramo** (Req 2.1, 2.3):
   Dentro del loop, en la rama `multiactivo`, usar la inflación anual del tramo ya disponible
   (`_infl_cierre_pct = float(inputs_tramo_i.inflacion_ea) * 100.0`) y el factor de capitalización:
   ```python
   meses_tramo = tramo.sub_horizonte
   infl_ea = float(inputs_tramo_i.inflacion_ea)          # decimal EA del tramo
   factor_tramo = (1.0 + infl_ea) ** (meses_tramo / 12.0)  # escala por fracción de año
   _uvr_inicio = (float(estado_previo.uvr_cierre)
                  if estado_previo is not None
                     and getattr(estado_previo, "uvr_cierre", None) is not None
                  else _uvr_t0)                           # encadenamiento / primer tramo
   _uvr_cierre_tramo = _uvr_inicio * factor_tramo
   ```
   El exponente `meses_tramo / 12` cubre tanto el primer tramo parcial (p. ej. 5/12) como los
   tramos anuales completos (12/12 = 1), de modo que el escalado del primer tramo (Req 2.3) es un
   caso particular de la misma fórmula.

3. **Pasar ambos valores al motor RF** (Req 2.2):
   En la llamada a `proyectar_portafolio`, cambiar `valor_uvr=_uvr` por:
   ```python
   valor_uvr=_uvr_inicio, valor_uvr_cierre=_uvr_cierre_tramo,
   ```
   El motor construye entonces `_senda_uvr` interpolando entre inicio y cierre (lógica existente,
   sin tocar). Actualizar también el uso de `_uvr` en el detalle de informe
   (`_rf_det_tramo(..., uvr_cierre=_uvr)`) para que use `_uvr_cierre_tramo`.

4. **Encadenar `uvr_cierre` en `EstadoEncadenado`** (Req 2.4):
   Añadir el campo `uvr_cierre` al `EstadoEncadenado` que se construye entre tramos
   (~líneas 8023-8032), análogo a `trm_cierre`:
   ```python
   estado_previo = EstadoEncadenado(
       ...,
       trm_cierre=float(inputs_tramo_i.trm_cierre),
       repo_cierre=float(inputs_tramo_i.repo_ea) * 100.0,
       inflacion_cierre=float(inputs_tramo_i.inflacion_ea) * 100.0,
       uvr_cierre=_uvr_cierre_tramo,
   )
   ```

5. **Añadir el campo `uvr_cierre` a la dataclass `EstadoEncadenado`**
   (`scripts/proyeccion_tramos_multiactivo.py`): nuevo campo opcional
   `uvr_cierre: float | None = None`, documentado igual que `trm_cierre`/`inflacion_cierre`
   (nivel de UVR al cierre del tramo, consumido como UVR de inicio del tramo siguiente; `None` en
   el estado inicial del primer tramo). Es una ampliación aditiva que no altera el encadenamiento
   existente (Req 3.4).

**Nota sobre monotonía (Req 2.5):** como `uvr_cierre = uvr_inicio * (1 + infl_ea) ^ (meses/12)` y
el motor interpola linealmente entre `uvr_inicio` y `uvr_cierre`, cuando `infl_ea >= 0` se cumple
`factor_tramo >= 1`, por lo que la senda es no decreciente dentro del tramo. Con `infl_ea == 0`,
`factor_tramo == 1` ⇒ UVR constante en el tramo (Req 3.6).

## Testing Strategy

### Validation Approach

La estrategia de pruebas sigue dos fases: primero, hacer aflorar contraejemplos que demuestren el
bug sobre el código sin corregir (confirmar/refutar la causa raíz); después, verificar que el
arreglo funciona (Fix Checking) y que preserva el comportamiento existente (Preservation Checking).
Se usan pruebas de propiedad (property-based testing con Hypothesis, ya presente en el proyecto —
ver `scripts/.hypothesis`) alineadas con C(X), Fix Checking y Preservation Checking de bugfix.md.

### Exploratory Bug Condition Checking

**Objetivo**: Hacer aflorar contraejemplos que demuestren el bug ANTES de implementar el arreglo.
Confirmar o refutar la causa raíz. Si se refuta, re-hipotetizar.

**Plan de prueba**: Construir un escenario `por_tramos` multiactivo con al menos un título UVR y
tramos con inflación > 0; capturar la senda de UVR efectiva (`_senda_uvr` del motor y/o la
`uvr_cierre` por tramo) y verificar que, en el código sin corregir, la UVR queda plana. Ejecutar
sobre el código SIN corregir para observar las fallas.

**Casos de prueba**:
1. **UVR plana multi-tramo** (fallará en el código sin corregir): horizonte de varios años,
   inflación 5% EA por tramo; se observa `uvr_cierre(tramo_i) == uvr_t0` para todo i.
2. **Sin encadenamiento** (fallará en el código sin corregir): el inicio del tramo i+1 no es el
   cierre del tramo i.
3. **Primer tramo parcial** (fallará en el código sin corregir): el tramo parcial no aplica
   `(1 + inflacion_ea) ^ (meses/12)`.
4. **Hardcode 411.0** (edge case, fallará en el código sin corregir): con `uvr_t0` vacío el
   sistema no aborta sino que inyecta `411.0`.

**Contraejemplos esperados**:
- La senda de UVR no crece pese a inflación positiva (`uvr_t0 == uvr_fin`).
- Posibles causas: `valor_uvr_cierre` ausente en la invocación del motor; UVR fijada fuera del
  loop; `uvr_cierre` ausente de `EstadoEncadenado`.

### Fix Checking

**Objetivo**: Verificar que para toda entrada donde la condición de bug se cumple, la función
corregida produce el comportamiento esperado (Property 1).

**Pseudocódigo:**
```
FOR ALL X WHERE isBugCondition(X) DO
  senda_uvr := F'(X).senda_uvr_por_tramo
  ASSERT uvr_crece_segun_inflacion_de_cada_tramo(senda_uvr)
     AND primer_tramo_escalado_por_fraccion_de_anio(senda_uvr)
     AND uvr_cierre(tramo_i) = uvr_inicio(tramo_{i+1})
     AND (infl_tramo >= 0 IMPLICA senda_uvr no decreciente)
     AND NOT contiene_hardcode(411.0)
END FOR
```

### Preservation Checking

**Objetivo**: Verificar que para toda entrada donde la condición de bug NO se cumple, la función
corregida produce el mismo resultado que la original (Property 2).

**Pseudocódigo:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Enfoque de prueba**: El property-based testing es idóneo para Preservation Checking porque:
- Genera muchos casos automáticamente sobre el dominio de entrada (modos, portafolios con/sin UVR,
  inflaciones cero y no cero).
- Captura casos límite que las pruebas unitarias manuales podrían omitir.
- Da garantías fuertes de que el comportamiento no cambia para todas las entradas no-buggy.

**Plan de prueba**: Observar primero el comportamiento sobre el código SIN corregir para el flujo
punta a punta, títulos no-UVR, tramos con inflación cero y portafolios sin UVR; luego escribir
pruebas de propiedad que capturen ese comportamiento y lo verifiquen tras el arreglo.

**Casos de prueba**:
1. **Preservación punta a punta**: `_calcular_uvr_cierre_desde_inflacion` y el paso de
   `valor_uvr`/`valor_uvr_cierre` del callback de escenario único no cambian.
2. **Preservación títulos no-UVR**: la valoración de COP/IPC/moneda extranjera es idéntica.
3. **Preservación encadenamiento TRM/repo/inflación**: `trm_cierre`, `repo_cierre`,
   `inflacion_cierre` se encadenan igual que antes.
4. **Preservación sin títulos UVR**: la proyección por tramos de un portafolio sin UVR da el
   mismo resultado.
5. **Preservación inflación cero**: con `inflacion_ea == 0`, la UVR queda constante en el tramo
   (factor = 1), equivalente al comportamiento plano para ese tramo.

### Unit Tests

- Factor de capitalización `(1 + inflacion_ea) ^ (meses_tramo / 12)` para tramo parcial (p. ej.
  5 meses) y tramo anual completo (12 meses).
- `uvr_cierre = uvr_inicio * factor_tramo` por tramo.
- Encadenamiento: `uvr_inicio(tramo_{i+1}) == uvr_cierre(tramo_i)` leyendo `EstadoEncadenado`.
- Guarda de UVR t0 ausente: devuelve el `html.Span` de error esperado y no corre el loop.
- `EstadoEncadenado` acepta y arrastra el nuevo campo `uvr_cierre` sin romper los existentes.

### Property-Based Tests

- **Fix — crecimiento según inflación**: para portafolios con UVR y tramos con inflación >= 0
  generados aleatoriamente, la UVR de cierre de cada tramo = `uvr_inicio * (1 + infl_ea) ^ (meses/12)`
  dentro de tolerancia numérica.
- **Fix — monotonía**: para inflación >= 0 generada aleatoriamente, la senda de UVR del tramo es
  no decreciente (índice 0 .. índice n_meses).
- **Fix — encadenamiento**: para horizontes multi-tramo generados aleatoriamente,
  `uvr_cierre(tramo_i) == uvr_inicio(tramo_{i+1})` para todo i.
- **Preservation — inflación cero**: para `inflacion_ea == 0`, la UVR permanece constante en el
  tramo (igual a F).
- **Preservation — sin UVR / no-UVR**: para portafolios sin títulos UVR, `F(X) == F'(X)` sobre
  los resultados de proyección.

### Integration Tests

- Corrida completa `por_tramos` multiactivo con títulos UVR y horizonte de varios años: la UVR
  nominal en COP de los títulos UVR crece de forma compuesta a través de los tramos.
- Corrida `por_tramos` con primer tramo parcial: el escalado por fracción de año se refleja en la
  senda de UVR del primer tramo y encadena correctamente al segundo.
- Corrida punta a punta de regresión: idéntica al resultado previo al arreglo (sin cambios).
