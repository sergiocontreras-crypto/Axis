# Bugfix Requirements Document

## Introduction

En la proyección por tramos (`modo_proyeccion = "por_tramos"`), las hojas de Excel de
Renta Variable y Alternativos muestran `pyg_mensual = 0.0` en el **primer mes de los
tramos posteriores al primero** (tramos con `indice > 0`, es decir los "eneros": p. ej.
`2027-01-01`, `2028-01-01`). En consecuencia, las hojas `RV_Mensual_Tramos` /
`Alt_Mensual_Tramos` y los resúmenes `RV_Mensual` / `Alt_Mensual` no registran P&G en el
primer mes de esos tramos, y el P&G que realmente corresponde a enero aparece **corrido un
mes** (etiquetado como febrero, etc.).

El **primer tramo** (`indice = 0`, el tramo base o parcial) **NO** sufre este defecto: su
primer mes es el **mes base / ancla** (el snapshot de apertura), que legítimamente debe
tener `pyg = 0` independientemente de si arranca el día 1 o el día 31 del mes. El fix
**no** debe inyectar P&G en el primer mes del tramo 0.

> **Verificación con `proyeccion_tramos.calcular_tramos` (verdad base):**
> - Una corrida que arranca el **2026-07-31** (corte típico 597, fin de mes) produce:
>   `tramo 0` inicio `2026-07-31` (día=31, `arranca_dia_1 = FALSE`, `sub_horizonte = 5`),
>   `tramo 1` inicio `2027-01-01` (día=1), `tramo 2` inicio `2028-01-01` (día=1).
> - Una corrida que arranca el **2026-07-01** produce: `tramo 0` inicio `2026-07-01`
>   (día=1, `arranca_dia_1 = TRUE`, `sub_horizonte = 6`), `tramo 1` y `tramo 2` igual.
>
> Esto demuestra que **el criterio "arranca día 1" es INCORRECTO**: en la corrida
> `2026-07-01` marcaría erróneamente el **primer tramo (base)** e inyectaría P&G en
> julio-2026, lo cual NO debe ocurrir. El criterio correcto es **`tramo.indice > 0`**:
> solo los tramos posteriores al primero (los que arrancan donde cerró el tramo previo)
> deben llevar P&G real en su primer mes. El tramo 0 es siempre el tramo base cuyo primer
> mes es el snapshot de apertura con `pyg = 0` (legítimo), arranque día 1 o día 31.

### Evidencia cuantitativa / Impacto (verificado contra el Excel real descargado)

La evidencia nueva, verificada contra la corrida por tramo con tres tipos de activo,
**redefine la naturaleza del bug**: el **total anual del P&G por tramo es CORRECTO**
(se preserva) y la serie de VM crece correctamente hasta alcanzar el cierre de diciembre
esperado. El defecto es **puramente de distribución mensual / etiquetado de fecha**, no
de magnitud anual.

Hechos verificados (tramo 2 = año 2027; la suma de los 12 `pyg_mensual` iguala el ancla
`VM(cierre dic-2027) − VM(cierre dic-2026)` con diferencia ~0%):

| Activo | Serie | Suma `pyg` 2027 | Ancla `VM(dic-2027) − VM(dic-2026)` | Diferencia |
|--------|-------|-----------------|--------------------------------------|-----------|
| RV internacional (ACWV iShares) | RV | 117.671.762,08 | 117.671.762,09 | ~0% |
| RV local (AO PROTECCION) | RV | 332.542.499,41 | 332.542.499,40 | ~0% |
| Alternativos (BROOKFIELD) | Alt | 1.318.634,14 | 1.318.634,14 | 0% |

En los tres casos, `VM(ene-2027)` es **idéntico** a `VM(dic-2026)` (la "fila fantasma"
de apertura) y `pyg(ene-2027) = 0`. El primer mes de **cada** tramo (incluido el primer
tramo parcial `2026-07`) es una fila de apertura fantasma con `pyg = 0` que duplica el
cierre de diciembre del tramo previo.

**Conclusión:** el P&G mensual de cada tramo está desplazado un mes tarde (el P&G real de
enero aparece etiquetado como febrero, etc.) y la fila de enero es un duplicado del cierre
de diciembre previo con `pyg = 0`. El total anual y el nivel final de VM son correctos; lo
único incorrecto es **en qué mes** se registra cada P&G.

### Causa raíz (diagnosticada y verificada — verdad base, no re-investigar)

- Los motores no-RF construyen la serie con
  `generar_periodos(fecha_inicio, fecha_fin)` donde `fecha_inicio = tramo.fecha_inicio`,
  y el bucle marca `i == 0` (el primer mes del tramo) como **fila de apertura T0** con
  `pyg_mensual = 0.0` y `valor_mercado = VM inicial` (no aplica retorno).
  - `scripts/proyeccion_renta_variable.py` → `proyectar_bloque` (rama `if i == 0:`,
    emite la fila con `pyg_mensual = 0.0` / `pyg_acumulado = 0.0`).
  - `scripts/proyeccion_alternativos.py` → `proyectar_alternativos` (misma estructura
    `generar_periodos` + apertura `i == 0`).
- Como el primer mes de todo tramo `i>0` es `AAAA-01` (enero), esa fila de apertura cae
  justo en enero: enero queda a `pyg = 0` y el retorno real de enero se contabiliza en el
  siguiente corte (febrero).
- RF **no** tiene este defecto: `scripts/motor_proyeccion.py` usa un **etiquetado de
  fecha especial** para arranques en día 1 (`fecha_etiqueta_mes = fecha_inicio +
  relativedelta(months=mes - 1)`, ~líneas 924-927), de modo que el mes 1 **es** enero con
  P&G real y no existe fila fantasma de apertura.

### Dirección del fix elegida (orientativa; se finaliza en `design.md`)

**Opción A — alinear RV/Alt al etiquetado de RF:** eliminar la fila de apertura fantasma
en RV/Alt de modo que el primer mes del tramo sea enero con su P&G real (igual que RF),
desplazando la serie para que cada P&G quede en su mes correcto. Los requisitos de abajo
se mantienen **suficientemente agnósticos de la solución** para que `design.md` pueda
finalizar el mecanismo exacto (p. ej. etiquetado tipo RF en los motores, o
post-procesamiento en el orquestador), siempre que se cumplan las propiedades de
distribución y de preservación del total anual.

**Alcance del fix:** únicamente RV y Alternativos. **No** se modifica RF.

## Bug Analysis

### Definición de la condición del bug

Notación: `X` es una serie mensual de P&G/VM producida por un motor no-RF para un tramo.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = (modo, serie, tramo, serie_mensual)
  OUTPUT: boolean

  // El bug ocurre SOLO cuando:
  //  1. La proyeccion es por tramos.
  //  2. La serie es no-RF (Renta Variable o Alternativos).
  //  3. El tramo NO es el primero: tramo.indice > 0.
  //     El tramo 0 (base/parcial) tiene un primer mes de apertura legitimo con
  //     pyg = 0 (snapshot ancla), arranque dia 1 o dia 31; NO debe tocarse.
  //     Solo los tramos posteriores (indice > 0), que arrancan donde cerro el
  //     tramo previo, deben llevar P&G real en su primer mes.
  RETURN X.modo = "por_tramos"
     AND X.serie IN { "renta_variable", "alternativos" }
     AND X.tramo.indice > 0
END FUNCTION
```

El punto afectado dentro de un `X` que cumple `isBugCondition` es la **distribución
mensual**: la fila de apertura (primer mes del tramo, el corte con menor
`fecha_proyeccion`) lleva `pyg = 0` y duplica el VM de cierre previo, y cada P&G mensual
queda desplazado un mes. El **total anual** del tramo NO forma parte del defecto (es
correcto). El tramo 0 queda explícitamente **fuera** de `isBugCondition` (ver 3.1 y la
invariante del mes base, abajo): su primer mes conserva `pyg = 0`.

### Current Behavior (Defect)

Comportamiento actual de la función original `F` = construcción de la serie mensual en los
motores RV/Alt (`generar_periodos` + apertura `i == 0`) para entradas que cumplen
`isBugCondition`.

1.1 WHEN la proyección es `por_tramos`, la serie es RV o Alternativos y el tramo tiene `indice > 0` (no es el tramo base) THEN el sistema registra `pyg_mensual = 0.0` en el primer mes del tramo (enero), produciendo una fila de apertura fantasma

1.2 WHEN se emite la fila de apertura del tramo THEN el sistema fija `valor_mercado(primer mes) = VM(cierre del mes previo)`, es decir `VM(ene) = VM(dic previo)`, de modo que enero no refleja ningún retorno

1.3 WHEN se construye la serie mensual del tramo THEN el sistema **desplaza un mes tarde** cada `pyg_mensual` real (el P&G que corresponde a enero aparece etiquetado como febrero, el de febrero como marzo, etc.)

1.4 WHEN se agregan las hojas `RV_Mensual` / `Alt_Mensual` (resumen) y `RV_Mensual_Tramos` / `Alt_Mensual_Tramos` THEN el sistema muestra `pyg_mensual = 0.0` en el primer mes de cada tramo con `indice > 0` (p. ej. `2027-01-01`, `2028-01-01`) y solo registra P&G a partir del segundo mes (p. ej. `2027-02-01`); el primer mes del tramo 0 (p. ej. `2026-07`) muestra `pyg = 0` de forma legítima (mes base) y NO forma parte del defecto

1.5 WHEN se compara la suma de los 12 `pyg_mensual` del tramo contra el ancla `VM(cierre último mes) − VM(cierre mes previo al tramo)` THEN el sistema YA produce un total anual correcto (diferencia ~0%); el defecto es exclusivamente **de distribución mensual**, no de magnitud anual

### Expected Behavior (Correct)

Comportamiento esperado de la función corregida `F'` para las mismas entradas que
cumplen `isBugCondition`.

2.1 WHEN la proyección es `por_tramos`, la serie es RV o Alternativos y el tramo tiene `indice > 0` THEN el sistema SHALL registrar en el primer mes del tramo (enero) su `pyg_mensual` **real** (distinto de 0.0 cuando hay retorno), sin fila de apertura fantasma, igual que lo hace RF. Esta regla NO aplica al tramo 0, cuyo primer mes (mes base) SHALL conservar `pyg = 0`

2.2 WHEN se construye la serie mensual del tramo THEN el sistema SHALL ubicar cada `pyg_mensual` en **su mes correcto** (el P&G de enero en enero, el de febrero en febrero, etc.), eliminando el desplazamiento de un mes

2.3 WHEN se corrige la distribución mensual THEN el sistema SHALL **preservar el total anual del tramo**: la suma de los `pyg_mensual` del tramo DEBE seguir siendo igual a `VM(cierre último mes del tramo) − VM(cierre del mes previo al tramo)` (para RV/Alt no hay cupones), es decir el fix **mueve** los valores a su mes correcto **sin** cambiar la magnitud anual

2.4 WHEN se agregan las hojas `RV_Mensual` / `Alt_Mensual` y `RV_Mensual_Tramos` / `Alt_Mensual_Tramos` THEN el sistema SHALL mostrar en el primer mes de cada tramo con `indice > 0` el P&G real del mes (distinto de 0.0 cuando hay retorno) y no un cero por fila fantasma

2.5 WHEN el tramo es el **tramo 0** (base/parcial, `indice = 0`) THEN el sistema SHALL CONTINUE TO mantener `pyg = 0` en su primer mes (mes base), es decir julio-2026 (o el mes base que corresponda) NO debe recibir P&G tras el fix (restricción dura del usuario)

2.6 WHEN se corrige la distribución mensual de cualquier tramo RV/Alt THEN el sistema SHALL preservar **idéntico** el conjunto y el conteo de meses por tramo (p. ej. 6 / 12 / 12 para una corrida `2026-07-31 → 2028-12-31`): ningún mes puede desaparecer, en particular diciembre de cada tramo (`2026-12`, `2027-12`, `2028-12`) DEBE seguir presente, y ningún mes puede quedar fuera de rango (restricción dura del usuario)

#### Especificación de propiedades (Fix Checking)

Sea, para un tramo con `indice > 0` cuyos cortes mensuales son `m_0 < m_1 < ... < m_n`
(siendo `m_0` el primer mes del tramo; como `indice > 0` siempre arranca el 1-ene, es
`AAAA-01`), con `VM(m)` el `valor_mercado` del corte `m` y
`pyg(m)` el `pyg_mensual` del corte `m`. Sea `VM_cierre_previo` el VM de cierre del mes
anterior al tramo (`m_{-1}`, el 31-dic del tramo previo o el cierre del tramo parcial
anterior).

**Propiedad 1 — Distribución mensual correcta (no hay fila fantasma):**

Esta propiedad SOLO aplica a tramos con `indice > 0` (lo garantiza `isBugCondition`). El
tramo 0 queda **excluido**: su primer mes (mes base) debe conservar `pyg = 0` y la
Propiedad 3 (mes base, abajo) lo cubre.

```pascal
// Property: Fix Checking - El primer mes del tramo (indice > 0) lleva su P&G real
FOR ALL X WHERE isBugCondition(X) DO    // isBugCondition => X.tramo.indice > 0
  serie <- F'(X)
  m0 <- primer_mes(serie)                       // AAAA-01
  // El primer mes refleja el retorno real desde el cierre previo, no 0:
  ASSERT pyg(m0) = VM(m0) - VM_cierre_previo
  // Ya no se duplica el cierre de diciembre en enero (salvo retorno nulo real):
  ASSERT NOT (VM(m0) = VM_cierre_previo AND pyg(m0) = 0)   // cuando el retorno real != 0
END FOR
```

**Propiedad 2 — Preservación del total anual del tramo (invariante central):**

```pascal
// Property: El total anual del tramo NO cambia; solo se redistribuye por mes.
FOR ALL X WHERE isBugCondition(X) DO
  serie_F  <- F(X)      // serie original (buggy en distribucion)
  serie_F2 <- F'(X)     // serie corregida
  // El ancla de VM del tramo es la misma en F y F':
  ancla <- VM(ultimo_mes(serie_F2)) - VM_cierre_previo
  ASSERT suma( pyg(m) for m in serie_F2 ) ~= ancla            // tolerancia ~0% (ej. 1e-6 relativo)
  // Y el total anual se conserva entre original y corregido:
  ASSERT suma( pyg(m) for m in serie_F2 ) ~= suma( pyg(m) for m in serie_F )
END FOR
```

Contraejemplo de referencia (verificado): tramo 2027, RV local (AO PROTECCION). `F`
produce `pyg(2027-01) = 0` con `VM(2027-01) = VM(2026-12)`, y el P&G real de enero
aparece en `2027-02`. La suma anual = 332.542.499,41 ≈ ancla 332.542.499,40 (correcta en
`F`). `F'` debe producir `pyg(2027-01) ≠ 0` (P&G real de enero) manteniendo esa misma suma
anual.

**Propiedad 3 — Mes base del tramo 0 conserva `pyg = 0` (restricción dura del usuario):**

El primer mes del **tramo 0** es el mes base / ancla (snapshot de apertura), NO una
proyección. El fix NUNCA debe inyectar P&G en él. Concretamente, para una corrida
`2026-07-31 → 2028-12-31`, julio-2026 DEBE quedar en `pyg = 0` después del fix.

```pascal
// Property: Base Month Preservation - tramo 0 primer mes queda en pyg = 0
FOR ALL X WHERE X.modo = "por_tramos"
             AND X.serie IN { "renta_variable", "alternativos" }
             AND X.tramo.indice = 0 DO
  serie <- F'(X)
  m0 <- primer_mes(serie)                 // p. ej. 2026-07
  ASSERT pyg(m0) = 0                       // mes base: NUNCA se le inyecta P&G
  ASSERT VM(m0) = VM_apertura(X.tramo)     // snapshot de apertura intacto
END FOR
```

Nota: el tramo 0 está fuera de `isBugCondition` (indice 0), por lo que esta invariante ya
está cubierta por `F(X) = F'(X)` de la Preservation Checking; se enuncia aquí como
requisito **explícito y nombrado** por ser una restricción dura del usuario.

**Propiedad 4 — Invarianza del conteo / conjunto de meses por tramo (restricción dura):**

El fix NO puede hacer desaparecer ningún mes ni correr meses fuera de rango. En particular,
**diciembre de cada tramo** debe seguir presente, y el número de meses por tramo debe ser
idéntico antes y después del fix.

Baseline verificado (debe preservarse exactamente) para una corrida
`2026-07-31 → 2028-12-31`, por serie de posición (RV y Alt con patrón idéntico):

| Tramo | Año/alcance | Meses | Detalle | `pyg = 0` |
|-------|-------------|-------|---------|-----------|
| 0 | parcial 2026 | 6 | jul..dic-2026 | solo jul-2026 |
| 1 | 2027 | 12 | ene..dic-2027 | ninguno (tras fix) |
| 2 | 2028 | 12 | ene..dic-2028 | ninguno (tras fix) |
| — | **Total** | **30** | — | — |

Diciembre de cada tramo (`2026-12`, `2027-12`, `2028-12`) DEBE estar presente. El único
cambio del fix es que el primer mes de los tramos `i > 0` (ene-2027, ene-2028) deja de ser
un cero fantasma y pasa a llevar su P&G mensual real; y el mes base del tramo 0 (jul-2026)
permanece en `pyg = 0`.

```pascal
// Property: Month-Set Invariance - ni se pierden meses ni se cae diciembre
FOR ALL X WHERE X.modo = "por_tramos"
             AND X.serie IN { "renta_variable", "alternativos" } DO
  // Para CADA tramo (indice 0 incluido): el conjunto de meses no cambia.
  ASSERT set_of_months(F'(X)) = set_of_months(F(X))
  ASSERT count_months(F'(X)) = count_months(F(X))   // p. ej. 6 / 12 / 12
  ASSERT last_month(F'(X))  = last_month(F(X))       // diciembre del tramo presente
  ASSERT december_of_tramo IN set_of_months(F'(X))   // ningun diciembre desaparece
END FOR
```

### Unchanged Behavior (Regression Prevention)

Comportamiento que `F'` DEBE preservar idéntico a `F` para toda entrada que **no**
cumple `isBugCondition`, más la invariante de total anual que debe preservarse en TODOS
los casos.

3.1 WHEN el tramo es el **tramo 0** (`indice = 0`, base/parcial) THEN el sistema SHALL CONTINUE TO tratar su primer mes como mes base de apertura legítimo con `pyg = 0` (no hay nivel de cierre previo real al cual anclar), sin inventar P&G que no corresponda — arranque día 1 o día 31

3.2 WHEN la serie es **Renta Fija (RF)** THEN el sistema SHALL CONTINUE TO calcular el P&G de enero mediante el etiquetado de fecha del motor RF (`motor_proyeccion.py`), sin intervención del fix

3.3 WHEN el mes **no** es el primer mes del tramo (cualquier corte distinto del arranque) THEN el sistema SHALL CONTINUE TO producir el mismo P&G mensual que corresponde a ese mes una vez corregida la distribución (no se introducen cambios adicionales fuera del realineamiento)

3.4 WHEN una posición es **nueva** en el tramo y no tiene VM de cierre en el mes previo THEN el sistema SHALL CONTINUE TO tratar su primer mes como apertura legítima (sin salto dic→ene inventado)

3.5 WHEN la proyección **no** es `por_tramos` (modo distinto) THEN el sistema SHALL CONTINUE TO comportarse exactamente igual que la implementación actual para RV y Alternativos

3.6 WHEN se corrige la distribución mensual de cualquier tramo RV/Alt THEN el sistema SHALL CONTINUE TO cerrar el tramo en el mismo `valor_mercado` de diciembre (el nivel final de VM y la serie de crecimiento de VM no cambian; solo cambia cómo se reparte el P&G por mes)

3.7 WHEN se calcula el **total anual del P&G por tramo** (en cualquier tramo, RV o Alt) THEN el sistema SHALL CONTINUE TO producir el mismo total anual que la implementación actual (invariante de preservación explícita: `suma(pyg) de F = suma(pyg) de F'`)

3.8 WHEN el tramo es el **tramo 0** (`indice = 0`) THEN el sistema SHALL CONTINUE TO mantener `pyg = 0` en su primer mes (mes base/ancla); el fix NUNCA inyecta P&G en el mes base (p. ej. julio-2026 permanece en `pyg = 0`) — restricción dura del usuario, codificable como PBT (ver Propiedad 3)

3.9 WHEN se construye la serie mensual de **cualquier** tramo RV/Alt THEN el sistema SHALL CONTINUE TO producir el **mismo conjunto y conteo de meses** por tramo que la implementación actual: `set_of_months(F') = set_of_months(F)` y `last_month(F') = last_month(F)` (diciembre de cada tramo siempre presente; conteos 6/12/12 y total de 30 meses preservados en la corrida `2026-07-31 → 2028-12-31`) — restricción dura del usuario, codificable como PBT (ver Propiedad 4)

#### Especificación de preservación (Preservation Checking)

```pascal
// Property: Preservation Checking - fuera de la condicion del bug, nada cambia.
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR

// Property: Preservation del total anual - vale incluso DENTRO de la condicion del bug,
// porque el fix solo redistribuye, no cambia magnitudes.
FOR ALL X DO
  ASSERT suma( pyg(m) for m in F(X) ) ~= suma( pyg(m) for m in F'(X) )
  ASSERT VM(ultimo_mes(F(X))) = VM(ultimo_mes(F'(X)))
END FOR

// Property: Base-Month Preservation (tramo 0) - restriccion dura del usuario.
FOR ALL X WHERE X.tramo.indice = 0 DO
  ASSERT pyg(primer_mes(F'(X))) = 0        // mes base/ancla sin P&G (p. ej. jul-2026)
END FOR

// Property: Month-Set Invariance - ni se pierden meses ni se cae diciembre.
FOR ALL X WHERE X.modo = "por_tramos"
             AND X.serie IN { "renta_variable", "alternativos" } DO
  ASSERT set_of_months(F'(X)) = set_of_months(F(X))   // 6 / 12 / 12 preservado
  ASSERT last_month(F'(X))   = last_month(F(X))        // diciembre del tramo presente
END FOR
```

Esto garantiza que: (a) para toda entrada no-buggy (series RF, modos distintos de
`por_tramos`, y en particular el **tramo 0** cuyo mes base conserva `pyg = 0`) el código
corregido es idéntico al original; (b) para TODO tramo RV/Alt el total anual del P&G y el
nivel de cierre de VM se conservan; y (c) para TODO tramo RV/Alt el conjunto y conteo de
meses se conservan (ningún mes desaparece, diciembre siempre presente). El fix únicamente
reubica cada P&G en su mes correcto en los tramos `indice > 0` (elimina la fila fantasma de
enero), sin tocar el mes base del tramo 0.

> **Nota de diseño a resolver (flag para la fase de diseño):** La Opción A (alinear RV/Alt
> al etiquetado de RF, sin fila de apertura fantasma, SOLO para tramos `indice > 0`) es la
> dirección preferida, pero `design.md` debe finalizar: (1) si el realineamiento se hace en
> los motores (`proyectar_bloque` / `proyectar_alternativos`, replicando
> `fecha_etiqueta_mes` de RF) o en el post-procesamiento del orquestador por tramos; (2)
> cómo se enlaza `VM_cierre_previo` del tramo anterior con el primer mes del tramo actual
> para que `pyg(enero)` sea el retorno real; (3) cómo se garantiza numéricamente que la suma
> anual permanezca igual (invariante 2.3 / 3.7); y (4) cómo se garantiza que el fix se
> aplique **únicamente** a tramos `indice > 0`, dejando intacto el mes base del tramo 0
> (invariante 2.5 / 3.8) y sin alterar el conjunto de meses (invariante 2.6 / 3.9). Las
> cuatro propiedades son directamente codificables como pruebas basadas en propiedades (PBT
> con Hypothesis): **(1)** distribución mensual correcta en tramos `indice > 0`, **(2)**
> preservación del total anual, **(3)** preservación del mes base del tramo 0 en `pyg = 0`,
> y **(4)** invarianza del conjunto/conteo de meses (diciembre nunca se cae).
