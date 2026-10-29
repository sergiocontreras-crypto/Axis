# Bugfix Requirements Document — TRM inicial de tramo consistente (modo "por_tramos")

## Introduction

En el modo de proyección "por_tramos" (multiactivo) existen DOS definiciones incompatibles
del concepto "TRM inicial del tramo" (nivel de la TRM al arranque de cada tramo), que
conviven en el mismo flujo y producen resultados inconsistentes entre los distintos
consumidores de ese nivel:

- La definición **correcta** la usa la valoración de **Renta Fija (RF)** mediante
  `_trm_tramo_ini` en `app_dash._ejecutar_escenario_por_tramos` (~L7971-7973): para el
  tramo 0 es `_trm_t0` = la TRM implícita real tomada del informe 597
  (`obtener_trm_para(_fecha_pip_str)`), p. ej. **3144.14** para el corte del 31-jul-2026; y
  para el tramo i>0 es el cierre del tramo anterior (`estado_previo.trm_cierre`,
  encadenado). RF pasa este valor a `proyectar_portafolio(trm=_trm_tramo_ini, ...)` (~L8072)
  y es **correcto**.

- La definición **incorrecta** es el PRIMER punto de la senda interpolada del tramo,
  `_extremos_senda_trm(sendas_tramo.senda_trm)[0]` (`app_dash` ~L8127), que alimenta tanto
  al **efecto moneda** (`calcular_efecto_moneda_tramo` vía `trm_inicio=_trm_ini_tr`,
  `app_dash` ~L8158-8165, y `_reg["trm_inicio"]` ~L8228) como —de forma análoga, al leer el
  primer punto de la senda— a los **derivados** (`_correr_derivados_tramo`:
  `nivel_actual = senda_trm[0]`, `app_dash` ~L7552) y a la valoración de **RV / Alternativos
  internacional** (vía el primer punto de la senda que consume `proyectar_bloque`).

**Causa raíz del desfase.** La función `interpolar_trm_tramo`
(`scripts/proyeccion_tramos_multiactivo.py` ~L254-320) construye deliberadamente el PRIMER
nodo de la senda ya avanzado un paso: `senda[0] = trm_inicio + (trm_cierre - trm_inicio)/n`,
NO `trm_inicio`. Por tanto la TRM real de t0 nunca es un nodo de la senda. Además, para el
tramo 0, `construir_sendas_tramo` (`scripts/proyeccion_tramos_multiactivo.py` ~L728-731)
fija `trm_inicio = trm_cierre` cuando `estado_previo is None`, dejando la senda del tramo 0
PLANA en `trm_cierre` (≈3337 en la corrida real), de modo que `senda[0]` ≈ 3337 en vez de la
TRM real de t0 (3144.14).

**Evidencia verificada (corrida real, tabla `informe_pyg_por_tramo`).**
- TRM real de t0 (31-jul-2026) tomada del 597 = **3144.14**.
- El efecto moneda registró `trm_inicio = 3337.0` para el tramo 2026 (incorrecto) y los
  tramos no encadenan limpiamente (2026 cierra 3337 pero 2027 arranca 3349.25).
- Derivados pasan `nivel_actual ≈ 3337` como spot de m=0 mientras las curvas de mercado se
  cargan para la fecha base REAL (3144.14) vía `_cargar_curvas(_fecha_mkt)` →
  inconsistencia spot/curva (~193 COP de desfase), y el cociente de cobertura
  `activos_usd = sum(usd_vm)/nivel_actual` queda subestimado ~6%.
- Consecuencia: en el tramo 0 la senda es plana, por lo que los forwards casi no se mueven
  mientras las opciones sí (vol/theta/strike), produciendo un resultado inverosímil de
  "P&G Opciones > P&G Forwards" con TRM al alza.

**Impacto.** Los resultados de efecto moneda, derivados y RV/Alternativos internacional del
modo por tramos parten de un nivel de TRM distinto (y erróneo) respecto al que usa RF,
rompiendo la consistencia interna del tramo y el encadenamiento entre tramos.

### Definiciones del método de condición de bug

- **F**: Función original (sin corregir) — el flujo por tramos tal como está hoy, donde los
  consumidores del nivel inicial del tramo (efecto moneda, derivados, RV/Alt internacional)
  leen `senda[0]` en lugar de la TRM real de arranque del tramo.
- **F'**: Función corregida — el flujo donde existe una ÚNICA fuente de verdad del nivel
  inicial del tramo, usada de forma consistente por RF, derivados, RV/Alt internacional y
  efecto moneda.
- **X**: Un tramo del escenario por tramos, caracterizado por su índice, su TRM real de
  arranque (`trm_inicio_real`), su `trm_cierre` y la senda interpolada que consumen los
  motores.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = un tramo del escenario "por_tramos" (multiactivo)
  OUTPUT: boolean

  // trm_inicio_real(X): nivel de TRM al arranque del tramo según la fuente de verdad.
  //   - tramo 0  -> TRM t0 real del informe 597 (obtener_trm_para(fecha_base))
  //   - tramo i>0 -> trm_cierre del tramo anterior (encadenado)
  // consumidores_nivel_inicio(X): nivel que efectivamente usan el efecto moneda,
  //   los derivados (nivel_actual) y la valoración RV/Alt internacional.

  RETURN consumidores_nivel_inicio(X) != trm_inicio_real(X)
         // es decir, algún consumidor lee senda[0] (ya avanzado un paso, o plano en
         // trm_cierre para el tramo 0) en lugar del nivel real de arranque del tramo.
END FUNCTION
```

## Bug Analysis

### Current Behavior (Defect)

Comportamiento actual, por consumidor del "nivel inicial del tramo":

1.1 WHEN se calcula el **efecto moneda** de un tramo (`calcular_efecto_moneda_tramo`,
`app_dash` ~L8158-8165, con `trm_inicio=_trm_ini_tr` obtenido de
`_extremos_senda_trm(sendas_tramo.senda_trm)[0]` en ~L8127) THEN el sistema usa como
`trm_inicio` el PRIMER punto de la senda interpolada (ya avanzado un paso; o el nivel plano
`trm_cierre` en el tramo 0) en lugar de la TRM real de arranque del tramo, registrando
`trm_inicio = 3337.0` cuando el valor real es 3144.14.

1.2 WHEN se corren los **derivados** de un tramo (`_correr_derivados_tramo`:
`nivel_actual = senda_trm[0]`, `app_dash` ~L7552) THEN el sistema usa como spot de m=0 el
primer punto de la senda (≈3337) mientras las curvas de mercado se cargan para la fecha base
real (3144.14) vía `_cargar_curvas(_fecha_mkt)`, generando una inconsistencia spot/curva
(~193 COP) y subestimando el cociente de cobertura `activos_usd = sum(usd_vm)/nivel_actual`
en ~6%.

1.3 WHEN se valoran **RV / Alternativos internacional** de un tramo (vía el primer punto de
la senda que consume `proyectar_bloque`) THEN el sistema arranca la valoración desde
`senda[0]` (≈3337) en lugar de la TRM real de arranque del tramo (3144.14).

1.4 WHEN el tramo es el tramo 0 y `estado_previo is None`
(`construir_sendas_tramo`, `scripts/proyeccion_tramos_multiactivo.py` ~L728-731) THEN el
sistema fija `trm_inicio = trm_cierre`, dejando la senda del tramo 0 PLANA en `trm_cierre`,
de modo que ningún nodo de la senda refleja la TRM real de t0.

1.5 WHEN se encadenan tramos consecutivos THEN el sistema produce un encadenamiento
inconsistente en los consumidores basados en senda (p. ej. el tramo 2026 cierra 3337 pero el
tramo 2027 arranca 3349.25 según el efecto moneda), porque el nivel de arranque observado por
esos consumidores no coincide con el cierre del tramo anterior.

### Expected Behavior (Correct)

El "TRM inicial del tramo" debe ser una ÚNICA fuente de verdad, usada de forma consistente
por RF, derivados, RV/Alternativos internacional y efecto moneda:

2.1 WHEN se calcula el **efecto moneda** de un tramo THEN el sistema SHALL usar como
`trm_inicio` la TRM real de arranque del tramo (`trm_inicio_real`): para el tramo 0 la TRM t0
del informe 597 (3144.14), y para el tramo i>0 el `trm_cierre` del tramo anterior.

2.2 WHEN se corren los **derivados** de un tramo THEN el sistema SHALL usar como spot de m=0
la TRM real de arranque del tramo (`trm_inicio_real`), consistente con la fecha de las curvas
de mercado cargadas (`_cargar_curvas(_fecha_mkt)`), eliminando el desfase spot/curva.

2.3 WHEN se valoran **RV / Alternativos internacional** de un tramo THEN el sistema SHALL
arrancar la valoración desde la TRM real de arranque del tramo (`trm_inicio_real`),
coincidiendo con el nivel que ya usa RF (`_trm_tramo_ini`).

2.4 WHEN el tramo es el tramo 0 THEN el sistema SHALL anclar el nivel inicial del tramo a la
TRM t0 real del informe 597 (`obtener_trm_para(fecha_base)`), sin asumir que el inicio del
tramo 0 es su propio cierre.

2.5 WHEN se encadenan tramos consecutivos THEN el sistema SHALL garantizar que el nivel de
arranque del tramo i observado por TODOS los consumidores sea exactamente el `trm_cierre` del
tramo i-1 (encadenamiento limpio).

> **Nota de alcance.** El mecanismo exacto (hacer que la senda interpolada arranque en el
> nodo real `trm_inicio` de modo que `senda[0] == trm_inicio_real`, o bien que los
> consumidores —`nivel_actual` de derivados, `trm_inicio` de efecto moneda— lean la TRM real
> de arranque en lugar de `senda[0]`) se decidirá en el diseño. Los requisitos permanecen
> agnósticos a la solución, pero deben garantizar: (a) spot m=0 de derivados == TRM t0 real y
> consistente con la fecha de las curvas; (b) `trm_inicio` del efecto moneda == TRM real de
> arranque del tramo; (c) comportamiento de RF sin cambios (ya correcto); (d) encadenamiento:
> `trm_inicio(tramo i) == trm_cierre(tramo i-1)`.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN se valora la **Renta Fija (RF)** de cualquier tramo (que ya usa el `_trm_tramo_ini`
correcto en `app_dash` ~L7971-7973 y lo pasa a `proyectar_portafolio(trm=_trm_tramo_ini,...)`
~L8072) THEN el sistema SHALL CONTINUE TO producir resultados numéricamente idénticos a los
actuales.

3.2 WHEN se define el **TRM de cierre** de un tramo (`trm_cierre`, input del usuario /
pronóstico `TRM_Por_Tramo`) THEN el sistema SHALL CONTINUE TO usarlo sin cambios; solo se
corrige el anclaje del INICIO del tramo.

3.3 WHEN se construye la senda interpolada de un tramo (`interpolar_trm_tramo`,
`scripts/proyeccion_tramos_multiactivo.py` ~L254-320) THEN el sistema SHALL CONTINUE TO
anclar el ÚLTIMO nodo de la senda EXACTAMENTE en `trm_cierre` (invariante correcto que debe
preservarse).

3.4 WHEN se ejecuta el modo **punta a punta** (no por tramos) THEN el sistema SHALL CONTINUE
TO comportarse exactamente como hoy, sin verse afectado por este arreglo.

3.5 WHEN se obtiene la TRM t0 THEN el sistema SHALL CONTINUE TO tomarla del informe 597 vía
`obtener_trm_para(fecha_base)`, sin introducir defaults de TRM hardcodeados; con logs
genéricos y SQL de solo lectura parametrizado.

## Correctness Properties (Property-Based Testing)

Propiedades formales que debe satisfacer la función corregida F' y que son adecuadas para
pruebas basadas en propiedades (p. ej. Hypothesis). Cada propiedad se cuantifica sobre
tramos/escenarios por tramos generados aleatoriamente dentro de rangos válidos.

```pascal
// ── P1: Fix Checking — nivel inicial consistente en todos los consumidores ──
// Para TODO tramo, el trm_inicio usado por derivados (nivel_actual), efecto moneda y
// RV/Alt internacional es igual a la TRM real de arranque del tramo.
FOR ALL X WHERE isBugCondition(X) DO
  nivel_derivados   ← nivel_actual_usado(F', X)      // spot m=0 de derivados
  nivel_efecto_mon  ← trm_inicio_efecto_moneda(F', X)
  nivel_rv_alt      ← trm_inicio_rv_alt_intl(F', X)
  esperado          ← trm_inicio_real(X)             // t0 real (tramo 0) o cierre previo
  ASSERT nivel_derivados  == esperado
  ASSERT nivel_efecto_mon == esperado
  ASSERT nivel_rv_alt     == esperado
END FOR
```

```pascal
// ── P2: Preservación — invariante del último nodo de la senda ──
// El último nodo de la senda interpolada sigue siendo exactamente trm_cierre.
FOR ALL X DO
  senda ← interpolar_trm_tramo(trm_inicio_real(X), trm_cierre(X), cortes(X))
  ASSERT last(senda) == trm_cierre(X)
END FOR
```

```pascal
// ── P3: Preservación — valoración RF idéntica ──
// Para TODO tramo, la valoración RF de F' coincide numéricamente con la de F.
FOR ALL X DO
  ASSERT valoracion_RF(F', X) == valoracion_RF(F, X)
END FOR
```

```pascal
// ── P4: Fix Checking — encadenamiento de tramos ──
// El nivel inicial del tramo i es exactamente el cierre del tramo i-1.
FOR ALL escenario WITH tramos [t0, t1, ..., tN] DO
  FOR i FROM 1 TO N DO
    ASSERT trm_inicio_real(t_i) == trm_cierre(t_{i-1})
  END FOR
END FOR
```

```pascal
// ── P5: Fix Checking — spot de derivados consistente con la fecha de curvas ──
// El spot m=0 que usan los derivados coincide con la TRM de la fecha base de las curvas
// de mercado cargadas (sin desfase spot/curva).
FOR ALL X WHERE isBugCondition(X) DO
  spot_m0        ← nivel_actual_usado(F', X)
  trm_fecha_curva ← obtener_trm_para(fecha_mercado_curvas(X))
  ASSERT spot_m0 == trm_fecha_curva
END FOR
```

```pascal
// ── P6 (Preservación): modo punta a punta inalterado ──
// Para TODO escenario en modo punta a punta, F' == F.
FOR ALL E WHERE modo(E) = "punta_a_punta" DO
  ASSERT F'(E) == F(E)
END FOR
```

### Referencias de código

- `app_dash._ejecutar_escenario_por_tramos`
  - `_trm_t0` / `_trm_tramo_ini` (fuente de verdad correcta, RF): ~L7960-7985
  - `proyectar_portafolio(trm=_trm_tramo_ini, ...)` (RF): ~L8072
  - `_trm_ini_tr, _trm_cie_tr = _extremos_senda_trm(sendas_tramo.senda_trm)`: ~L8127
  - `calcular_efecto_moneda_tramo(..., trm_inicio=_trm_ini_tr, ...)`: ~L8158-8165
  - `_reg["trm_inicio"] = float(_trm_ini_tr) ...`: ~L8228
  - `_correr_derivados_tramo` con `fecha_mercado=_fecha_pip_str` (curvas en fecha base real):
    ~L7987-8006
- `app_dash._correr_derivados_tramo`
  - `nivel_actual = senda_trm[0]`: ~L7552
- `scripts/proyeccion_tramos_multiactivo.py`
  - `interpolar_trm_tramo` (primer nodo ya avanzado; último nodo == trm_cierre): ~L254-320
  - `construir_sendas_tramo` (tramo 0: `trm_inicio = trm_cierre` cuando
    `estado_previo is None`): ~L728-731
