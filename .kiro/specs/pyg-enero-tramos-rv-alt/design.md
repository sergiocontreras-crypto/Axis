# P&G de Enero en Tramos RV/Alt — Bugfix Design

## Overview

En la proyección por tramos (`modo_proyeccion = "por_tramos"`), las series mensuales de
**Renta Variable** y **Alternativos** registran `pyg_mensual = 0.0` en el primer mes de
los tramos con `indice > 0` (los "eneros": `2027-01`, `2028-01`, …) y desplazan todo el
P&G mensual un mes tarde. El total anual por tramo y el nivel final de VM de diciembre
**ya son correctos**; el defecto es **puramente de distribución mensual / etiquetado de
fecha**.

La causa raíz está en los motores no-RF: `proyectar_bloque` (RV y Alt) y
`proyectar_bloque_ipc_spread` (Alt FCP 4G) usan `generar_periodos(fecha_inicio, fecha_fin)`
y marcan el índice `i == 0` del bucle como **fila de apertura T0** con `pyg = 0` y
`valor_mercado = VM_inicial`. Como para todo tramo `indice > 0` el primer mes es `AAAA-01`
(enero), esa fila de apertura cae en enero, dejándolo en cero y empujando el primer retorno
real (el de enero) al corte etiquetado como febrero.

RF **no** sufre el defecto: `motor_proyeccion.py` itera `mes = 1..N` valorando en
`fecha_mes = T0 + relativedelta(months=mes)` y **no** emite una fila T0; cuando el tramo
arranca el día 1 usa una **etiqueta** `fecha_etiqueta_mes = T0 + relativedelta(months=mes-1)`,
de modo que el mes 1 es enero con su P&G real y no hay fila fantasma.

**Enfoque elegido (Opción A — alinear RV/Alt al etiquetado de RF), aplicado SOLO a tramos
`indice > 0`:** se corrige la distribución mensual de RV/Alt para que el primer mes del
tramo (enero) lleve su P&G real y cada P&G quede en su mes correcto, preservando el total
anual, el VM de cierre de diciembre, y el conjunto/conteo de meses por tramo. La corrección
se realiza como **post-procesamiento en los adaptadores del orquestador por tramos**
(`_correr_rv_tramo` / `_correr_alt_tramo` en `app_dash.py`), **sin modificar los motores**,
para no afectar los flujos que no son por tramos (Req 3.5). El tramo 0 (base/parcial) queda
intacto: su primer mes conserva `pyg = 0`.

## Glossary

- **Bug_Condition (C)**: la condición que dispara el bug — serie no-RF (RV/Alt) en modo
  `por_tramos` cuyo tramo tiene `indice > 0`. Formalizada en `isBugCondition`.
- **Property (P)**: el comportamiento deseado — el primer mes del tramo (`indice > 0`)
  lleva su `pyg_mensual` real (`VM(enero) − VM_cierre_previo`), cada P&G en su mes correcto,
  sin fila fantasma.
- **Preservation**: todo lo que `F'` debe dejar idéntico a `F`: series RF, modos distintos
  de `por_tramos`, el mes base del tramo 0 (`pyg = 0`), el total anual por tramo, el VM de
  cierre de diciembre y el conjunto/conteo de meses por tramo.
- **`F` / `F'`**: función original (buggy en distribución) / función corregida que
  construye la serie mensual de un tramo RV/Alt.
- **`proyectar_bloque`**: función en `scripts/proyeccion_renta_variable.py` y en
  `scripts/proyeccion_alternativos.py` que proyecta VM mes a mes por posición; su rama
  `if i == 0:` emite la fila de apertura con `pyg = 0`.
- **`proyectar_bloque_ipc_spread`**: variante en `scripts/proyeccion_alternativos.py` para
  fondos FCP 4G (IPC + spread); tiene la misma rama de apertura `i == 0`.
- **`generar_periodos(fecha_inicio, fecha_fin)`**: genera `pd.date_range(freq="MS")` (primer
  día de cada mes) de `fecha_inicio` a `fecha_fin` inclusive.
- **`_correr_rv_tramo` / `_correr_alt_tramo`**: adaptadores por tramo en `app_dash.py`
  (~líneas 7254 y 7297) que invocan los motores acotados al sub-periodo del tramo y aplican
  encadenamiento.
- **`_aplicar_encadenamiento_resultado(df, vm_cierre_previo, col_id)`**: post-proceso en
  `app_dash.py` (~línea 7211) que escala cada posición por
  `VM_cierre_previo / VM_inicial_original`. Es identidad si `vm_cierre_previo` está vacío
  (primer tramo).
- **`extraer_vm_cierre(df, col_id)`**: en `scripts/proyeccion_tramos_multiactivo.py`
  (~línea 951); devuelve `{id: VM del último corte}` (diciembre del tramo).
- **`VM_cierre_previo`**: VM de cierre (31-dic) del tramo anterior por posición, llave del
  encadenamiento (`estado_previo.vm_rv_por_posicion` / `vm_alt_por_posicion`).
- **`tramo.indice`**: índice 0-based del tramo; `tramo 0` es el tramo base/parcial.

## Bug Details

### Bug Condition

El bug se manifiesta cuando la proyección es por tramos, la serie es RV o Alternativos y el
tramo NO es el primero (`indice > 0`). En ese caso el primer mes del tramo (enero) es una
fila de apertura fantasma con `pyg = 0` que duplica el VM de cierre de diciembre del tramo
previo, y cada `pyg_mensual` real queda desplazado un mes.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X = (modo, serie, tramo, serie_mensual)
  OUTPUT: boolean

  RETURN X.modo = "por_tramos"
         AND X.serie IN { "renta_variable", "alternativos" }
         AND X.tramo.indice > 0
END FUNCTION
```

### Mecánica exacta del defecto (verificada en código)

Para el tramo 1 (año 2027), el adaptador pasa `fecha_inicio = "2027-01"` y
`fecha_fin = "2027-12"`. Entonces:

```
generar_periodos("2027-01", "2027-12")
  = [2027-01-01, 2027-02-01, ..., 2027-12-01]   # 12 periodos
```

El bucle `for i, periodo in enumerate(periodos)` de `proyectar_bloque`:

| i  | periodo (label) | rama      | VM registrado                | pyg_mensual        |
|----|-----------------|-----------|------------------------------|--------------------|
| 0  | 2027-01-01      | `i == 0`  | `VM0` (= VM dic-2026)        | **0.0 (fantasma)** |
| 1  | 2027-02-01      | `i > 0`   | `VM0·(1+td)^31`             | retorno de 31 días → **es el P&G de enero, mal etiquetado** |
| …  | …               | `i > 0`   | …                            | …                  |
| 11 | 2027-12-01      | `i > 0`   | `VM11` (cierre dic correcto) | P&G de noviembre   |

Observaciones clave (verdad base):
1. La serie tiene **12 filas** `ene..dic`. La fila `ene` es la fantasma.
2. Hay solo **11 pasos de capitalización reales** (ene→feb, …, nov→dic). El mes de enero no
   produce retorno propio; el retorno de los 31 días de enero aparece etiquetado como
   febrero.
3. `VM(ene) = VM0 = VM_cierre_previo` ⇒ enero "duplica" el cierre de diciembre previo.
4. `suma(pyg) = VM11 − VM0 = VM(dic) − VM_cierre_previo` = total anual **correcto**.
5. `VM(dic)` (último corte) es **correcto** y se usa como `VM_cierre_previo` del tramo
   siguiente vía `extraer_vm_cierre`.

> **Reconciliación del conteo (resuelve la nuance del conteo de meses):** el motor produce
> **12 periodos** `ene..dic` (no 13). `i == 0` (ene) es la fila fantasma y los 11 pasos de
> retorno cubren feb..dic. Por tanto, el fix NO debe borrar filas (eso dejaría 11 meses y
> tiraría un mes). El fix es un **re-etiquetado + recomputo de `pyg`/`VM`** que mantiene las
> **mismas 12 etiquetas `ene..dic`** pero hace que `ene` lleve su P&G real y cada mes su
> retorno correcto, conservando `VM(dic)` y el total anual.

### Examples

Contraejemplo de referencia (verificado contra el Excel real) — tramo 2027, RV local
**AO PROTECCION**:
- **Actual (`F`)**: `pyg(2027-01) = 0`, `VM(2027-01) = VM(2026-12)`; el P&G real de enero
  aparece en `2027-02`. Suma 2027 = `332.542.499,41` ≈ ancla `332.542.499,40`.
- **Esperado (`F'`)**: `pyg(2027-01) = VM(2027-01 cierre) − VM(2026-12 cierre) ≠ 0`; cada
  P&G en su mes; suma 2027 **sin cambio** (`≈ 332.542.499,40`); `VM(2027-12)` sin cambio;
  diciembre-2027 presente.
- RV internacional **ACWV iShares**: suma 2027 `117.671.762,08` (sin cambio tras el fix).
- Alternativos **BROOKFIELD**: suma 2027 `1.318.634,14` (sin cambio tras el fix).

Edge case — tramo 0 (parcial, `2026-07-31 → 2026-12-31`): `pyg(2026-07) = 0` (mes base) y
**debe seguir en 0** tras el fix (no está en `isBugCondition`).

## Expected Behavior

### Preservation Requirements

**Comportamientos que NO deben cambiar (idénticos a `F`):**
- Series de **Renta Fija (RF)**: calculadas por `motor_proyeccion.py`; el fix no las toca
  (Req 3.2).
- Cualquier modo **distinto de `por_tramos`** (punta a punta, multiescenarios): RV/Alt se
  comportan exactamente igual que hoy (Req 3.5). Por eso el fix vive en el orquestador por
  tramos y no en los motores.
- **Tramo 0** (base/parcial, `indice = 0`): su primer mes (mes base/ancla) conserva
  `pyg = 0` y `VM = VM_apertura`; el fix NUNCA inyecta P&G ahí (Req 2.5, 3.1, 3.8).
- **Total anual por tramo** (RV y Alt, cualquier tramo): `suma(pyg)` de `F'` = `suma(pyg)`
  de `F` (Req 2.3, 3.7).
- **VM de cierre de diciembre** por tramo: idéntico (Req 3.6); garantiza que
  `extraer_vm_cierre` y el encadenamiento del tramo siguiente no cambien.
- **Conjunto y conteo de meses** por tramo: `set_of_months(F') = set_of_months(F)`,
  `count_months` idéntico (6/12/12, total 30 para `2026-07-31 → 2028-12-31`), diciembre de
  cada tramo presente (Req 2.6, 3.9).

**Alcance:** únicamente RV y Alternativos, y únicamente la **distribución mensual** de
tramos con `indice > 0`. No se altera magnitud anual ni el conjunto de meses.

> El comportamiento correcto esperado se define formalmente en **Correctness Properties**
> (Property 1). Esta sección enumera lo que debe permanecer invariante.

## Hypothesized Root Cause

Diagnóstico ya verificado (no requiere re-investigación); se enumeran como causas raíz
confirmadas:

1. **Fila de apertura T0 en el primer mes del tramo (causa principal, confirmada):** la
   rama `if i == 0:` de `proyectar_bloque` (RV y Alt) y de `proyectar_bloque_ipc_spread`
   (Alt FCP 4G) emite `pyg_mensual = 0.0` y `valor_mercado = VM_inicial` para el primer
   `periodo`. Como el primer `periodo` de todo tramo `indice > 0` es `AAAA-01`, enero queda
   en cero.
2. **Desfase de etiquetado vs. RF (confirmada):** los motores no-RF etiquetan el corte
   `i` con el `periodo` del `pd.date_range` (`T0 + i` meses, primer día de mes), mientras RF
   usa `fecha_etiqueta_mes = T0 + (mes-1)` para arranques en día 1. Esa diferencia es lo que
   mueve el P&G de enero al casillero de febrero.
3. **No es causa:** el encadenamiento `_aplicar_encadenamiento_resultado` NO introduce el
   defecto — solo escala por posición; el VM de cierre de diciembre y el total anual que
   produce son correctos. El fix debe operar **después** del encadenamiento o de forma
   independiente de él, preservando el factor ya aplicado.

## Correctness Properties

Property 1: Bug Condition — Distribución mensual correcta en tramos `indice > 0`

_For any_ serie RV/Alt en modo `por_tramos` cuyo tramo cumple `isBugCondition`
(`indice > 0`), la función corregida `F'` SHALL registrar en el primer mes del tramo
(enero, `m0`) su P&G mensual real `pyg(m0) = VM(m0) − VM_cierre_previo` (sin fila de
apertura fantasma), y ubicar cada `pyg_mensual` en su mes correcto, de modo que ya no
exista el caso `VM(m0) = VM_cierre_previo AND pyg(m0) = 0` cuando el retorno real del mes
es distinto de cero.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 2: Preservation — Total anual del tramo y VM de cierre invariantes

_For any_ serie RV/Alt en modo `por_tramos` (cualquier tramo, cumpla o no
`isBugCondition`), `F'` SHALL producir el mismo total anual que `F`
(`suma(pyg) de F' ≈ suma(pyg) de F`, tolerancia relativa ~1e-6) y el mismo VM del último
corte del tramo (`VM(ultimo_mes(F')) = VM(ultimo_mes(F))`), de modo que el fix solo
redistribuye el P&G por mes sin cambiar magnitudes anuales ni el nivel de cierre.

**Validates: Requirements 2.3, 3.6, 3.7**

Property 3: Preservation — Mes base del tramo 0 permanece en `pyg = 0`

_For any_ serie RV/Alt en modo `por_tramos` cuyo tramo tiene `indice = 0`, `F'` SHALL
mantener `pyg(primer_mes) = 0` y `VM(primer_mes) = VM_apertura` (el fix NUNCA inyecta P&G
en el mes base; p. ej. julio-2026 permanece en 0), arranque el tramo día 1 o día 31.

**Validates: Requirements 2.5, 3.1, 3.8**

Property 4: Preservation — Invarianza del conjunto/conteo de meses por tramo

_For any_ serie RV/Alt en modo `por_tramos` (cualquier tramo), `F'` SHALL producir el mismo
conjunto y conteo de meses que `F` (`set_of_months(F') = set_of_months(F)`,
`count_months(F') = count_months(F)`, p. ej. 6/12/12 y total 30) y el mismo último mes
(`last_month(F') = last_month(F)`), garantizando que diciembre de cada tramo
(`2026-12`, `2027-12`, `2028-12`) siga presente y ningún mes desaparezca o quede fuera de
rango.

**Validates: Requirements 2.6, 3.9**

Property 5: Preservation — Fuera de la condición del bug, nada cambia

_For any_ entrada que NO cumple `isBugCondition` (series RF, modos distintos de
`por_tramos`, o tramo 0), `F'` SHALL producir exactamente el mismo resultado que `F`
(`F(X) = F'(X)`).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Decisión de diseño: ¿dónde se aplica el fix?

Se evaluaron dos opciones:

**Opción (a) — Post-proceso en los adaptadores del orquestador (ELEGIDA).**
Añadir una función de realineamiento en `app_dash.py` que, SOLO cuando `tramo.indice > 0`,
transforme el `df_resultado` de RV/Alt eliminando el efecto de la fila fantasma de enero y
reubicando cada `pyg`/`VM` en su mes correcto, usando `VM_cierre_previo`
(`estado_previo.vm_*_por_posicion`) como ancla del primer mes.

- **Ventajas:** NO toca los motores, que también se usan en flujos no-tramos
  (multiescenarios, punta a punta) que deben permanecer idénticos (Req 3.5, Property 5). El
  orquestador ya conoce `tramo.indice` y `VM_cierre_previo`, que son exactamente los dos
  insumos que el fix necesita. Centraliza la corrección en un único punto por activo.
- **Desventajas:** el realineamiento se expresa como transformación del DataFrame ya
  calculado (recomputar `pyg` a partir de diferencias de `VM`), no como una regeneración de
  la serie; debe implementarse con cuidado para preservar el VM de cierre y el total anual
  (ver garantías abajo).

**Opción (b) — Fix dentro de los motores (`proyectar_bloque`), pasando un flag/`VM_previo`.**
Replicar en los motores la lógica de `fecha_etiqueta_mes` de RF y suprimir la fila `i == 0`
para tramos `indice > 0`.

- **Ventajas:** la serie nace correcta; conceptualmente más cercano a RF.
- **Desventajas (decisivas):** los motores `proyectar_bloque` / `proyectar_bloque_ipc_spread`
  son compartidos por los flujos **no** por tramos; introducir un flag arriesga regresiones
  en multiescenarios y punta a punta (Req 3.5), y obliga a propagar `VM_cierre_previo` y el
  carácter del tramo hasta la firma del motor, acoplando el motor al concepto de "tramo"
  que hoy vive solo en el orquestador. Mayor superficie de regresión.

**Conclusión:** se elige la **Opción (a)** por minimizar la superficie de cambio y aislar
la corrección al único flujo afectado (por tramos), satisfaciendo Property 5 por
construcción (el código de los motores no cambia).

### Archivos y funciones a cambiar

**Archivo:** `app_dash.py`

1. **Nueva función** `_realinear_pyg_enero_tramo(df_resultado, vm_cierre_previo, col_id, aplicar)`
   (helper privado, junto a `_aplicar_encadenamiento_resultado`):
   - Si `aplicar` es `False` (tramo 0 o `vm_cierre_previo` vacío) ⇒ devuelve `df_resultado`
     sin cambios (identidad → Property 3 y Property 5 por construcción).
   - Si `aplicar` es `True`, para cada posición (`groupby(col_id)`), ordenada por
     `fecha_proyeccion` ascendente, con VMs `[VM_0(fantasma=VM_prev), VM_1, …, VM_{n}]`
     sobre las etiquetas `[m0=ene, m1=feb, …, m_n=dic]`:
     - **Reancla y recomputa** la serie de P&G por diferencias de VM consecutivas,
       desplazando el VM real un casillero hacia enero. Dado que la serie del motor ya
       satisface `VM_{n} = VM(dic)` correcto, el realineamiento se implementa como:
       - `pyg'(m0=ene) = VM_1 − VM_cierre_previo`  (P&G real de enero)
       - `pyg'(m_k)   = VM_{k+1} − VM_k`           para `1 ≤ k ≤ n-1`
       - `pyg'(m_n=dic) = VM_{n} − VM_{n-1}`        (último paso real; sin inventar mes)
       - `VM'(m_k) = VM_{k+1}` para `k = 0..n-1`, y `VM'(m_n) = VM_{n}` (diciembre intacto)
       - `pyg_acumulado'` se recalcula como suma acumulada de `pyg'`.
     - **Nota de conservación:** `Σ pyg'(m_k) = VM_{n} − VM_cierre_previo = Σ pyg(m_k)` (los
       términos telescopan), por lo que el total anual se preserva exactamente, y
       `VM'(m_n) = VM_{n}` deja diciembre intacto (ver garantías por propiedad).
   - La implementación concreta (que no se escribe aún) debe operar vectorizada por posición
     y reutilizar los `valor_mercado` ya calculados por el motor (no recomponer tasas) para
     garantizar igualdad numérica de `VM(dic)` y del total anual.

2. **`_correr_rv_tramo`** (~línea 7254): tras `_aplicar_encadenamiento_resultado(... col_id="titulo")`,
   invocar `_realinear_pyg_enero_tramo(df, vm_previo, col_id="titulo", aplicar=(tramo.indice > 0))`.
   El realineamiento se aplica **sobre el DataFrame ya encadenado** (el factor de
   encadenamiento preserva relaciones multiplicativas, así que las diferencias de VM siguen
   siendo P&G válidos tras el escalado).

3. **`_correr_alt_tramo`** (~línea 7297): análogo, con `col_id="id_posicion"` y
   `aplicar=(tramo.indice > 0)`. Cubre tanto `proyectar_bloque` como
   `proyectar_bloque_ipc_spread` porque el realineamiento opera sobre el DataFrame unificado
   de salida de `proyectar_alternativos` (que concatena ambas metodologías), por posición.

**Insumo de `VM_cierre_previo`:** ya disponible como `vm_previo`
(`estado_previo.vm_rv_por_posicion` / `vm_alt_por_posicion`), poblado por
`extraer_vm_cierre` del tramo anterior (VM del último corte = diciembre). Para la posición
cuyo id no exista en `vm_previo` (posición nueva en el tramo, Req 3.4), el realineamiento se
decide por posición: si falta `VM_cierre_previo[id]`, esa posición se deja como apertura
legítima (no se realinea) — mismo criterio que `_aplicar_encadenamiento_resultado`
(factor 1.0).

**Lo que NO se cambia:** `proyeccion_renta_variable.py`, `proyeccion_alternativos.py`,
`motor_proyeccion.py` (RF), `extraer_vm_cierre`, ni la firma de los motores.

### Cómo se garantiza cada propiedad

- **Property 1 (distribución correcta):** por construcción, `pyg'(ene) = VM_1 − VM_prev` es
  el retorno real de enero (lo que el motor calculó como primer paso de 31 días) y
  `VM'(ene) = VM_1 ≠ VM_prev` cuando el retorno es no nulo ⇒ se elimina la fila fantasma y
  el desfase de un mes. Solo se aplica cuando `tramo.indice > 0` (`aplicar=True`).
- **Property 2 (total anual + VM cierre):** la nueva serie de `pyg'` telescopa a
  `VM_{n} − VM_prev`, idéntico al total de `F`; `VM'(dic) = VM_{n}` no se toca. Se usan los
  `valor_mercado` ya producidos por el motor (sin recomputar tasas) para evitar deriva por
  redondeo.
- **Property 3 (tramo 0 en 0):** `aplicar = (tramo.indice > 0)` ⇒ para tramo 0 el helper es
  identidad; el mes base conserva `pyg = 0` y `VM_apertura`.
- **Property 4 (conjunto/conteo de meses):** el helper NO agrega ni elimina filas; solo
  recomputa `pyg`/`VM`/`pyg_acumulado` sobre las mismas etiquetas `fecha_proyeccion`
  (12 filas ene..dic → 12 filas ene..dic). `set_of_months` y `last_month` se preservan;
  diciembre sigue presente.
- **Property 5 (fuera de la condición del bug):** el fix vive solo en los adaptadores de
  tramos; RF, multiescenarios y punta a punta no invocan `_realinear_pyg_enero_tramo`. Para
  tramo 0 el helper es identidad.

## Testing Strategy

### Validation Approach

Dos fases: (1) exploración — surfacear contraejemplos que demuestren el bug sobre el código
**sin corregir**, confirmando la causa raíz; (2) verificación — comprobar que el fix produce
la distribución correcta y preserva total anual, VM de cierre, mes base y conjunto de meses.

Las pruebas se ubican en `tests/` siguiendo la convención existente
(pytest + Hypothesis + `conftest.py`), p. ej. `tests/test_pyg_enero_tramos_rv_alt.py`.
Se recomienda modelar la serie mensual de un tramo con un *stub* determinista del DataFrame
de salida del motor (filas por posición y `fecha_proyeccion`) para aislar el realineamiento,
y además una prueba de integración ligera que invoque el adaptador real con un portafolio
mínimo.

### Exploratory Bug Condition Checking

**Goal:** surfacear contraejemplos del bug ANTES de implementar el fix y confirmar la causa
raíz (fila fantasma `i == 0` en enero de tramos `indice > 0`). Si se refuta, re-hipotetizar.

**Test Plan:** construir la serie de un tramo `indice > 0` (p. ej. 2027, 12 periodos
ene..dic) con el motor actual (o un stub que replique su patrón) y asertar sobre la salida
**sin corregir** que `pyg(ene) = 0` y `VM(ene) = VM_cierre_previo`.

**Test Cases:**
1. **RV local tramo 2027 (AO PROTECCION)**: `pyg(2027-01) == 0` y `VM(2027-01) ==
   VM(2026-12)` en el código actual (falla contra el comportamiento esperado).
2. **RV internacional tramo 2027 (ACWV)**: mismo patrón de fila fantasma en enero.
3. **Alt tramo 2027 (BROOKFIELD, IPC+spread)**: mismo patrón en `proyectar_bloque_ipc_spread`.
4. **Edge — tramo 0 parcial (jul-2026)**: `pyg(2026-07) == 0` — este NO es defecto; sirve
   para fijar que el fix no debe tocarlo.

**Expected Counterexamples:** en tramos `indice > 0`, el primer mes (enero) tiene
`pyg = 0` y VM igual al cierre de diciembre previo; el primer retorno real aparece en
febrero. Causa: rama `i == 0` de `proyectar_bloque` / `proyectar_bloque_ipc_spread`.

### Fix Checking

**Goal:** para toda entrada que cumple `isBugCondition`, `F'` produce la distribución
esperada.

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO           // => X.tramo.indice > 0
  serie := F'(X)
  m0 := primer_mes(serie)                       // AAAA-01
  ASSERT pyg(m0) ~= VM(m0) - VM_cierre_previo
  ASSERT NOT (VM(m0) = VM_cierre_previo AND pyg(m0) = 0)   // cuando el retorno real != 0
END FOR
```

### Preservation Checking

**Goal:** para toda entrada que NO cumple `isBugCondition`, `F'` = `F`; y para TODO tramo
RV/Alt el total anual, el VM de cierre, el mes base del tramo 0 y el conjunto de meses se
preservan.

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR

FOR ALL X (cualquier tramo RV/Alt) DO
  ASSERT suma(pyg(m) for m in F'(X)) ~= suma(pyg(m) for m in F(X))     // total anual
  ASSERT VM(ultimo_mes(F'(X))) = VM(ultimo_mes(F(X)))                  // cierre diciembre
  ASSERT set_of_months(F'(X)) = set_of_months(F(X))                   // 6/12/12
  ASSERT last_month(F'(X)) = last_month(F(X))                         // diciembre presente
END FOR

FOR ALL X WHERE X.tramo.indice = 0 DO
  ASSERT pyg(primer_mes(F'(X))) = 0                                   // mes base intacto
END FOR
```

**Testing Approach:** property-based testing (Hypothesis) para la preservación, porque
genera muchos escenarios (VM iniciales, tasas EA, número de meses, posición nueva vs.
existente) y captura edge cases que las pruebas unitarias puntuales podrían omitir, dando
garantía fuerte de que nada cambia fuera de la condición del bug.

### Unit Tests

- `pyg(ene) != 0` tras el fix para un tramo `indice > 0` con retorno positivo; y
  `pyg(ene) = VM(ene) − VM_cierre_previo`.
- `pyg(jul-2026) = 0` para el tramo 0 tras el fix (no se inyecta P&G).
- Conteo de meses 6/12/12 y presencia de diciembre en cada tramo antes y después del fix.
- Caso de posición nueva (sin `VM_cierre_previo`): su primer mes se trata como apertura
  legítima (no se realinea).
- RF y modo punta a punta: salida idéntica (no se invoca el realineamiento).

### Property-Based Tests (Hypothesis)

Estrategias que generen series mensuales de tramo parametrizadas por: VM inicial por
posición (>0), tasa EA/retorno, número de posiciones, e `indice` del tramo. Codificar como
PBT las **4 propiedades** (más la de preservación general):

- **PBT Property 1 — Distribución (indice>0):** para series generadas con `indice > 0`,
  `pyg(ene) ~= VM(ene) − VM_cierre_previo` y no existe fila fantasma cuando el retorno real
  es no nulo.
- **PBT Property 2 — Preservación del total anual y VM de cierre:** `suma(pyg) de F' ~=
  suma(pyg) de F` (tol. relativa ~1e-6) y `VM(dic) de F' = VM(dic) de F`, para todo tramo.
- **PBT Property 3 — Mes base del tramo 0 en 0:** para `indice = 0`,
  `pyg(primer_mes(F')) = 0` y `VM(primer_mes) = VM_apertura`.
- **PBT Property 4 — Invarianza del conjunto/conteo de meses:** `set_of_months(F') =
  set_of_months(F)`, `count_months(F') = count_months(F)`, `last_month(F') = last_month(F)`;
  diciembre siempre presente.
- **PBT Property 5 — Preservación general:** para `NOT isBugCondition(X)` (RF, modos
  distintos, tramo 0), `F'(X) = F(X)`.

**Reproducción determinista alineada a los datos verificados** (como caso explícito /
`@example` de Hypothesis o unit test puntual):

- RV local AO PROTECCION, tramo 2027: tras el fix `pyg(2027-01) != 0` e igual a
  `VM(2027-01 cierre) − VM(2026-12 cierre)`; `suma 2027 ~= 332.542.499,40` (sin cambio);
  diciembre-2027 presente; `VM(2027-12)` sin cambio.
- RV internacional ACWV: `suma 2027 ~= 117.671.762,08` sin cambio.
- Alternativos BROOKFIELD: `suma 2027 ~= 1.318.634,14` sin cambio.

### Integration Tests

- Corrida por tramos `2026-07-31 → 2028-12-31` con portafolio mínimo RV+Alt: verificar
  conteos 6/12/12 (total 30), `pyg(jul-2026) = 0`, `pyg(ene-2027) != 0`, `pyg(ene-2028) !=
  0`, y totales anuales por tramo iguales a los de la corrida sin fix.
- Encadenamiento: el `VM(dic)` de cada tramo (insumo de `extraer_vm_cierre`) no cambia tras
  el fix ⇒ el `VM_cierre_previo` del tramo siguiente es idéntico (sin efecto cascada).
- Hojas de salida `RV_Mensual` / `RV_Mensual_Tramos` / `Alt_Mensual` / `Alt_Mensual_Tramos`:
  el primer mes de los tramos `indice > 0` muestra P&G real (no cero).

## Preservation / Regression Considerations

- **Flujos no-tramos intactos:** al no modificar los motores, multiescenarios y punta a
  punta producen exactamente la misma salida (Property 5). Prueba de regresión dedicada
  comparando salida de RV/Alt en modo punta a punta antes/después del cambio.
- **Encadenamiento inter-tramo estable:** como `VM(dic)` por posición no cambia,
  `extraer_vm_cierre` devuelve los mismos valores y el `EstadoEncadenado` del tramo
  siguiente es idéntico; no hay propagación de cambios aguas abajo salvo la redistribución
  mensual intencional.
- **Efecto moneda:** `calcular_efecto_moneda_tramo` usa VM de inicio/cierre del tramo
  (no la distribución mensual intermedia); al preservarse ambos extremos, el efecto moneda
  por tramo no cambia. Verificar con una aserción de regresión.
- **Redondeos:** el realineamiento debe reutilizar los `valor_mercado` ya calculados por el
  motor (diferencias de VM) en lugar de recomponer con tasas, para que `VM(dic)` y el total
  anual sean numéricamente idénticos (tolerancia ~1e-6 en PBT por el redondeo a 2 decimales
  que ya aplican los motores).
- **Posiciones nuevas (Req 3.4):** sin `VM_cierre_previo`, no se realinea esa posición (se
  trata como apertura legítima), evitando inventar un salto dic→ene.
- **Tramo 0, día 1 vs día 31 (Req 3.1):** el criterio de activación es `tramo.indice > 0`,
  no "arranca día 1"; por eso una corrida que arranque `2026-07-01` NO inyecta P&G en el mes
  base del tramo 0 (evita el bug del criterio incorrecto señalado en los requisitos).
