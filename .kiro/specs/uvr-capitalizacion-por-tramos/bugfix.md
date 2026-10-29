# Bugfix Requirements Document

## Introduction

En el flujo de proyección **por tramos** del proyecto AXIS (cortes anuales encadenados,
donde el cierre de un tramo es el t0 del siguiente), la **UVR** (Unidad de Valor Real;
índice que se capitaliza con la inflación en Colombia) **no se compone con la inflación de
cada tramo y queda plana durante todo el horizonte**. Como consecuencia, el valor nominal
en COP de los títulos UVR se subestima progresivamente en horizontes de varios años.

El defecto está en la orquestación `_ejecutar_escenario_por_tramos` (app_dash.py), que:

1. Fija la UVR **una sola vez** antes del loop de tramos
   (`_uvr = float(uvr_t0) if uvr_t0 else 411.0`) y nunca la recalcula ni la encadena.
2. Pasa al motor RF solo `valor_uvr=_uvr` (sin `valor_uvr_cierre`), por lo que la senda
   interna de UVR del motor queda **plana** (`uvr_t0 == uvr_fin`).
3. Usa un **default hardcodeado (411.0)**, inconsistente con el patrón ya aplicado a la TRM
   (que aborta con un error verificable si no hay dato real).

El motor RF (`scripts/motor_proyeccion.py`, `proyectar_portafolio`) **ya soporta** una UVR
que evoluciona: acepta `valor_uvr` y `valor_uvr_cierre` y construye internamente la senda
`_senda_uvr` interpolando linealmente mes a mes. El flujo "punta a punta" ya lo aprovecha;
el flujo por tramos no. Otros índices macro (TRM, repo, inflación) **sí** se encadenan por
tramo vía `EstadoEncadenado` (`trm_cierre`, `repo_cierre`, `inflacion_cierre`); la UVR quedó
fuera de ese mecanismo.

El arreglo es de **orquestación**: calcular por tramo la `uvr_inicio` y `uvr_cierre`,
pasarlas al motor y encadenarlas entre tramos, sin tocar la lógica de valoración del motor
RF ni el flujo punta a punta.

### Definición de la condición de bug C(X)

```pascal
FUNCTION isBugCondition(X)
  INPUT: X de tipo EscenarioProyeccion
  OUTPUT: boolean

  // El bug SOLO ocurre en el modo por tramos, cuando hay títulos UVR
  // y al menos un tramo tiene inflación != 0 (que debería capitalizar la UVR).
  RETURN X.modo_proyeccion = "por_tramos"
         AND existe_titulo_UVR(X.portafolio)
         AND existe_tramo_con_inflacion_no_cero(X.tramos)
END FUNCTION
```

```pascal
// Propiedad: Fix Checking — la UVR se capitaliza y encadena por tramos
FOR ALL X WHERE isBugCondition(X) DO
  senda_uvr ← F'(X).senda_uvr_por_tramo
  ASSERT uvr_crece_segun_inflacion_de_cada_tramo(senda_uvr)
     AND primer_tramo_escalado_por_fraccion_de_anio(senda_uvr)
     AND uvr_cierre(tramo_i) = uvr_inicio(tramo_{i+1})   // encadenamiento
     AND NOT contiene_hardcode(411.0)
END FOR
```

```pascal
// Propiedad: Preservation Checking — no cambia nada fuera de la condición de bug
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Definiciones:**
- **F**: la orquestación actual (UVR plana, hardcode 411.0).
- **F'**: la orquestación corregida (UVR capitalizada y encadenada por tramo).

## Bug Analysis

### Current Behavior (Defect)

Lo que ocurre hoy en el modo `por_tramos`:

1.1 WHEN el modo es `por_tramos` y existen títulos UVR THEN el sistema mantiene la UVR plana durante todo el horizonte (no la compone con la inflación de ningún tramo)

1.2 WHEN se invoca el motor RF por cada tramo THEN el sistema pasa solo `valor_uvr=_uvr` sin `valor_uvr_cierre`, de modo que la senda interna de UVR del motor queda plana (`uvr_t0 == uvr_fin`)

1.3 WHEN avanza de un tramo al siguiente THEN el sistema no encadena la UVR (la UVR de cierre de un tramo no se usa como UVR de inicio del siguiente), a diferencia de `trm_cierre`, `repo_cierre` e `inflacion_cierre`

1.4 WHEN no hay UVR t0 real disponible THEN el sistema usa el default hardcodeado `411.0` (`_uvr = float(uvr_t0) if uvr_t0 else 411.0`), inconsistente con el patrón de la TRM que aborta con error verificable

1.5 WHEN el portafolio UVR se proyecta a varios años THEN el sistema subestima el valor nominal en COP de los títulos UVR por no capitalizar la UVR con la inflación

### Expected Behavior (Correct)

Lo que debe ocurrir en el modo `por_tramos` (metodología confirmada con el usuario):

2.1 WHEN el modo es `por_tramos` y existen títulos UVR THEN el sistema SHALL hacer crecer la UVR dentro de cada tramo según la inflación anual a cierre de año de ese tramo (`InputsPorTramo.inflacion_ea`, decimal EA), de forma lineal dentro del tramo

2.2 WHEN se invoca el motor RF por cada tramo THEN el sistema SHALL pasar tanto `valor_uvr` (UVR de inicio del tramo) como `valor_uvr_cierre` (UVR de cierre del tramo), para que la senda interna de UVR del motor evolucione en lugar de quedar plana

2.3 WHEN el tramo es el PRIMER tramo y cubre un año parcial (p. ej. ~5 meses) THEN el sistema SHALL escalar la inflación anual del input a la fracción del año que cubre el tramo, aplicando un crecimiento `(1 + inflacion_ea) ^ (meses_tramo / 12)` o un prorrateo equivalente consistente con la metodología del proyecto

2.4 WHEN avanza de un tramo al siguiente THEN el sistema SHALL usar la UVR de cierre del tramo como UVR de inicio del tramo siguiente (encadenamiento análogo a `trm_cierre`, `repo_cierre` e `inflacion_cierre` en `EstadoEncadenado`)

2.5 WHEN la inflación del tramo es >= 0 THEN el sistema SHALL garantizar que la UVR no decrezca dentro del tramo (monotonía no decreciente; si la inflación baja pero sigue positiva, la UVR crece más despacio, no decrece)

2.6 WHEN no hay UVR t0 real disponible THEN el sistema SHALL abortar con un mensaje de error verificable (eliminando el hardcode `411.0`), idéntico en espíritu al patrón de la TRM

### Unchanged Behavior (Regression Prevention)

Comportamiento existente que debe preservarse intacto:

3.1 WHEN el modo es `punta a punta` THEN el sistema SHALL CONTINUAR calculando la UVR de cierre con `_calcular_uvr_cierre_desde_inflacion` y proyectando la UVR exactamente como hoy (sin cambios)

3.2 WHEN se valoran títulos no-UVR (COP, IPC, moneda extranjera, etc.) en cualquier modo THEN el sistema SHALL CONTINUAR produciendo los mismos resultados que antes del arreglo

3.3 WHEN el motor RF construye internamente la senda `_senda_uvr` por interpolación lineal entre `valor_uvr` y `valor_uvr_cierre` THEN el sistema SHALL CONTINUAR usando esa lógica de valoración sin modificarla (`valorar_titulo` y la construcción de `_senda_uvr` no se tocan; el arreglo es solo de orquestación)

3.4 WHEN se encadenan por tramo la TRM, el repo y la inflación vía `EstadoEncadenado` THEN el sistema SHALL CONTINUAR encadenándolos exactamente igual (el nuevo encadenamiento de UVR se suma al mecanismo existente sin alterarlo)

3.5 WHEN el modo es `por_tramos` pero no existen títulos UVR en el portafolio THEN el sistema SHALL CONTINUAR produciendo los mismos resultados de proyección que antes del arreglo

3.6 WHEN la inflación de un tramo es exactamente 0 THEN el sistema SHALL CONTINUAR dejando la UVR constante en ese tramo (crecimiento nulo, equivalente al comportamiento plano para ese tramo específico)
