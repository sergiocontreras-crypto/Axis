# Bugfix Requirements Document

## Introduction

El `valor_mercado` (VM) en COP de los títulos de **Renta Fija Internacional en USD**
(Yankees / globales, p. ej. `BN YANKEES` ISIN `US195325DP79`, `BN ECOPETROL USD`) sale
mal escalado cuando la **TRM de cierre** del tramo se ingresa con la **unidad equivocada**.
La valoración financiera es correcta (`valorar_titulo` rama YANKEE multiplica
`precio_sucio/100 × trm`), pero el **nivel de la TRM** que llega al motor por tramo no se
normaliza: si el usuario (o un Excel de inputs) ingresa la TRM de cierre como `3.9` en
lugar de `3900`, el VM de los títulos USD queda **÷1000** (y análogamente ×1000 si llega
en una escala inflada). Los títulos en COP no se ven afectados (no multiplican por TRM).

Este bugfix cubre **únicamente la parte de la escala de la TRM** (sub-defecto **2B** del
registro de deuda técnica `docs/deuda-tecnica.md` → "RF Internacional (USD) — mala
valoración por semántica de tasas + escala TRM"). **NO** cubre el sub-defecto 2A
(semántica de la tasa de descuento `tasa_mercado` = T.Dcto vs TIR) ni el 2C (riesgo
sistémico de `tasa_mercado`), que son más delicados y se abordan por separado.

### Evidencia cuantitativa / Impacto (verificado, datos reales)

- Título 859370 `BN YANKEES` (ISIN `US195325DP79`), nominal 1.000.000 USD:
  - Con `trm = 3900` (nivel COP/USD correcto): VM ≈ `4.543.211.556` COP (orden de miles
    de millones, consistente con un nominal de 1 MM USD).
  - Con `trm = 3.9` (escala "miles" equivocada): VM ≈ `4.543.211,56` COP → **÷1000**,
    exactamente el síntoma reportado por el usuario ("¿cómo es posible que el valor de
    mercado sean 3 millones de pesos cuando el nominal es 1 millón de dólares?").
- El factor es **exactamente ×1000** por paso de unidad (nivel ↔ miles), y SOLO afecta
  títulos cuyo `valor_mercado` se obtiene multiplicando por TRM (USD). Los COP quedan
  idénticos.

### Causa raíz (diagnosticada y verificada — verdad base, no re-investigar)

- La valoración NO es la causa: `scripts/motor_proyeccion.py` → `valorar_titulo` (rama
  YANKEE, `nominal * precio_sucio/100 * trm`) y `scripts/valorar_bonos.py` →
  `valorar_bono_yankee` son **correctos** (aplican la TRM como nivel COP/USD).
- La causa es la **unidad del input de TRM de cierre**, que se pasa TAL CUAL como nivel
  sin validar/normalizar contra la banda plausible de una TRM COP/USD (nivel ~3900 vía
  `gestor_informes.obtener_trm_implicita`):
  - **por tramos:** el grid de `app_dash.py` arma
    `inputs_por_tramo['trm_cierre'] = _g(tramo_trm, i)` (~línea 6065) →
    `InputsPorTramo.from_valores(..., trm_cierre=...)`
    (`scripts/proyeccion_tramos_multiactivo.py`) → `InputsPorTramo.trm_cierre` →
    `_trm_tramo_fin` → `proyectar_portafolio`. `from_valores` hoy coerciona a `float`
    finito pero **NO normaliza la unidad** del nivel.
  - **multiescenarios:** `tools/multiescenarios.py` arma
    `trm_cierre = float(esc["trm_final"])` (~línea 533) sin normalizar la unidad.
- `InputsPorTramo.from_valores` ya es el punto único de ingreso de inputs no confiables
  por tramo (coerciona/valida cada campo con `_coerce_float_finito` y defaults
  documentados en `_DEFAULTS_INPUTS_TRAMO`, con `trm_cierre` default `4000.0`). Es el
  lugar natural para añadir la **guarda de normalización de unidad** de la TRM.

### Dirección del fix elegida (orientativa; se finaliza en `design.md`)

**Normalización de unidad de la TRM de cierre en el punto de ingreso**, sin tocar la
valoración:

- Añadir una guarda que, cuando `trm_cierre` cae fuera de la **banda plausible de nivel
  COP/USD** (p. ej. `[500, 20000]`), intente **normalizar por factores de 1000** (×1000 o
  ÷1000) hasta encajar en la banda.
- Si tras el ajuste por factores de 1000 el valor **sigue fuera de banda** (no es un simple
  error de ×1000, p. ej. `150`), el sistema **NO adivina**: trata el valor como dato
  corrupto. La política exacta (abortar con error verificable vs. caer al default `4000.0`
  con warning genérico) se finaliza en `design.md`; la dirección preferida para
  **valoración financiera** es **abortar con error verificable** antes que valorar con una
  TRM silenciosamente dudosa.
- Un valor **vacío/ausente/no numérico** sigue el comportamiento actual de `from_valores`
  (default documentado `4000.0` con warning genérico) — eso NO es parte del defecto 2B.
- **NO** se modifica `valorar_titulo` ni `valorar_bono_yankee` (son correctos).

Los requisitos de abajo se mantienen **suficientemente agnósticos de la solución** para que
`design.md` pueda finalizar el mecanismo exacto (guarda en `from_valores`, helper
compartido reutilizado por `multiescenarios`, banda exacta y política de fuera-de-banda),
siempre que se cumplan las propiedades de normalización y de preservación.

**Alcance del fix:** únicamente la **escala/unidad de la TRM de cierre** en el punto de
ingreso de inputs por tramo y de multiescenarios. **No** se toca la valoración, ni la
semántica de tasas (2A), ni los usos sistémicos de `tasa_mercado` (2C).

## Bug Analysis

### Definición de la condición del bug

Notación: `X` es un ingreso de inputs por tramo (o de multiescenarios) que contiene un
nivel de `trm_cierre` crudo proveniente de la UI/Excel (entrada NO confiable).

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = (origen, trm_cierre_crudo)
  OUTPUT: boolean

  // El bug (mal escalado del VM de USD) ocurre cuando:
  //  1. El ingreso define una TRM de cierre numerica y finita (no vacia).
  //  2. Ese nivel esta FUERA de la banda plausible de una TRM COP/USD,
  //     pero es normalizable a la banda multiplicando/dividiendo por 1000
  //     (es decir, es un error de UNIDAD, no un dato arbitrario).
  trm <- coerce_float_finito(X.trm_cierre_crudo, default=SENTINELA_VACIO)
  RETURN trm != SENTINELA_VACIO
     AND NOT en_banda(trm)                 // banda plausible de nivel, p.ej. [500, 20000]
     AND es_normalizable_por_1000(trm)     // ∃ k ∈ {..., -1, +1, ...}: en_banda(trm * 1000^k)
END FUNCTION
```

El efecto de un `X` que cumple `isBugCondition` es que el VM en COP de **los títulos USD**
del portafolio queda escalado por el factor de unidad erróneo (típicamente ×1000 o ÷1000).
Los ingresos con TRM **ya en banda** (nivel correcto) NO cumplen `isBugCondition` y deben
quedar **idénticos** (identidad). Los ingresos **vacíos/no numéricos** quedan fuera de
`isBugCondition` (los cubre el default actual de `from_valores`).

### Current Behavior (Defect)

Comportamiento actual de la función `F` = construcción de `InputsPorTramo` /
`trm_cierre` de multiescenarios desde la entrada cruda, para `X` que cumplen
`isBugCondition`.

1.1 WHEN el ingreso define `trm_cierre` como un número finito fuera de la banda plausible de nivel (p. ej. `3.9` en escala "miles") THEN el sistema conserva ese valor TAL CUAL como nivel de TRM (solo coerciona a `float` finito, sin normalizar la unidad)

1.2 WHEN ese `trm_cierre` fuera de escala se propaga al motor (`proyectar_portafolio` vía `_trm_tramo_fin`, o `trm_cierre` de multiescenarios) THEN el sistema valora los títulos USD multiplicando `precio_sucio/100 × trm` con el nivel equivocado, produciendo un VM en COP escalado por el factor de unidad (p. ej. ÷1000)

1.3 WHEN el usuario inspecciona el VM de cierre de un título USD (p. ej. 859370, nominal 1.000.000 USD) THEN el sistema muestra un VM ~1000× menor de lo esperado (p. ej. ~`4.543.211,56` COP en vez de ~`4.543.211.556` COP), inconsistente con el nominal en USD

1.4 WHEN el portafolio mezcla títulos COP y USD THEN el sistema deja los COP correctos (no multiplican por TRM) y solo descuadra los USD, de modo que el total de RF internacional queda subvaluado mientras RF local se ve bien

### Expected Behavior (Correct)

Comportamiento esperado de la función corregida `F'` para las mismas entradas que
cumplen `isBugCondition`.

2.1 WHEN el ingreso define `trm_cierre` como un número finito fuera de la banda plausible pero normalizable por factores de 1000 THEN el sistema SHALL normalizar el nivel al valor equivalente dentro de la banda plausible de una TRM COP/USD (p. ej. `3.9 → 3900`), antes de propagarlo al motor

2.2 WHEN la TRM de cierre se normaliza THEN el sistema SHALL propagar al motor el nivel normalizado, de modo que el VM en COP de los títulos USD quede en la escala correcta (consistente con el nominal en USD × TRM nivel)

2.3 WHEN la TRM de cierre ya está dentro de la banda plausible (nivel correcto) THEN el sistema SHALL dejarla EXACTAMENTE igual (la normalización es identidad para TRM en banda; no reescala valores correctos)

2.4 WHEN la TRM de cierre está fuera de banda y NO es normalizable por factores de 1000 (dato arbitrario, p. ej. `150`) THEN el sistema SHALL tratarla como dato corrupto con una política explícita y verificable (dirección preferida: abortar con error verificable; política final en `design.md`), SIN valorar silenciosamente con una TRM dudosa y SIN caer a un default que enmascare el error

2.5 WHEN se emite cualquier normalización o rechazo THEN el sistema SHALL registrar un `logger.warning` GENÉRICO (sin volcar el valor crudo recibido, por la regla de no-filtrado de datos sensibles) que permita trazar que hubo una corrección/validación de unidad de TRM

### Unchanged Behavior (Regression Prevention)

Comportamiento que `F'` DEBE preservar idéntico a `F` para toda entrada que **no**
cumple `isBugCondition`.

3.1 WHEN la TRM de cierre ya está dentro de la banda plausible de nivel THEN el sistema SHALL CONTINUE TO producir el mismo nivel de TRM y, por tanto, el mismo VM en COP para todos los títulos (identidad: `F(X) = F'(X)` para TRM en banda)

3.2 WHEN el ingreso de `trm_cierre` es vacío / `None` / no numérico / no finito THEN el sistema SHALL CONTINUE TO aplicar el default documentado (`4000.0`) con warning genérico, exactamente como hoy (ese caso NO es parte del defecto 2B)

3.3 WHEN se valora cualquier título (USD o COP) THEN el sistema SHALL CONTINUE TO usar `valorar_titulo` / `valorar_bono_yankee` sin cambios (la valoración es correcta; el fix NO la toca)

3.4 WHEN el título está denominado en COP (no multiplica por TRM) THEN el sistema SHALL CONTINUE TO producir el mismo VM que hoy, con o sin normalización de la TRM (la normalización solo afecta la escala de los USD)

3.5 WHEN los demás campos de `InputsPorTramo` (inflación, repo, movimientos de curva, spreads, retornos EA, bps) se coercionan THEN el sistema SHALL CONTINUE TO comportarse exactamente igual que hoy (la guarda de unidad aplica SOLO a `trm_cierre`, no a otros campos)

3.6 WHEN la proyección NO involucra títulos en moneda extranjera THEN el sistema SHALL CONTINUE TO producir resultados idénticos (la normalización de TRM no cambia nada observable si no hay USD)

#### Especificación de propiedades (Fix / Preservation Checking)

Sea `normalizar_trm(trm)` la función de normalización de unidad propuesta, `en_banda(x)`
el predicado de banda plausible de nivel COP/USD (p. ej. `500 ≤ x ≤ 20000`), y `VM_usd(trm)`
el VM en COP de un título USD valorado con nivel `trm`.

**Propiedad 1 — Normalización correcta (Fix Checking):**

```pascal
// Property: Fix Checking - una TRM fuera de banda pero normalizable por 1000 se corrige.
FOR ALL X WHERE isBugCondition(X) DO
  trm'  <- normalizar_trm(X.trm_cierre_crudo)
  ASSERT en_banda(trm')                                   // queda en nivel plausible
  ASSERT EXISTS k: trm' = X.trm_cierre_crudo * (1000 ** k) // solo se ajusta por unidad
END FOR
```

**Propiedad 2 — Identidad en banda (Preservation Checking):**

```pascal
// Property: Preservation - una TRM ya en banda no cambia.
FOR ALL trm WHERE en_banda(trm) DO
  ASSERT normalizar_trm(trm) = trm
END FOR
```

**Propiedad 3 — Escala correcta del VM USD (consistencia extremo a extremo):**

```pascal
// Property: el VM USD con la TRM normalizada es el VM "correcto" (nivel), no el ÷1000.
FOR ALL X WHERE isBugCondition(X) AND es_error_de_mil(X) DO   // trm_cruda = trm_correcta / 1000
  VM_malo    <- VM_usd(X.trm_cierre_crudo)                    // escala equivocada
  VM_bueno   <- VM_usd(normalizar_trm(X.trm_cierre_crudo))    // escala correcta
  ASSERT VM_bueno ~= VM_malo * 1000                           // recupera el factor de unidad
END FOR
```

**Propiedad 4 — Rechazo verificable de TRM no normalizable (Fix Checking):**

```pascal
// Property: una TRM fuera de banda y NO normalizable por 1000 no se valora en silencio.
FOR ALL X WHERE X.trm_cierre_crudo es finito
             AND NOT en_banda(X.trm_cierre_crudo)
             AND NOT es_normalizable_por_1000(X.trm_cierre_crudo) DO
  // Politica explicita y verificable (abortar con error, o default con warning segun design):
  ASSERT politica_fuera_de_banda_es_explicita_y_trazable(X)
END FOR
```

**Propiedad 5 — Campos no-TRM intactos (Preservation Checking):**

```pascal
// Property: la guarda de unidad SOLO afecta trm_cierre; los demas campos no cambian.
FOR ALL X DO
  inp_F  <- F(X)      // InputsPorTramo original
  inp_F2 <- F'(X)     // InputsPorTramo con guarda de TRM
  FOR campo IN campos_de_InputsPorTramo WHERE campo != "trm_cierre" DO
    ASSERT inp_F2[campo] = inp_F[campo]
  END FOR
END FOR
```

Esto garantiza que: (a) una TRM mal escalada pero normalizable por unidad se corrige al
nivel plausible (Propiedad 1 y 3); (b) una TRM ya correcta no se toca (Propiedad 2); (c)
una TRM arbitraria fuera de banda no se valora en silencio (Propiedad 4, política
verificable); y (d) ningún otro campo de inputs ni la valoración cambian (Propiedad 5 +
3.3/3.4/3.5). El fix únicamente normaliza/valida la **unidad** de `trm_cierre` en el punto
de ingreso.

> **Notas de diseño a resolver (flags para la fase de diseño):** `design.md` debe
> finalizar: (1) la **banda plausible exacta** de nivel COP/USD (propuesta inicial
> `[500, 20000]`, a confirmar contra `obtener_trm_implicita` y la guarda histórica
> 2000–8000 citada en la deuda técnica); (2) la **política de fuera-de-banda no
> normalizable** (abortar con error verificable vs. default con warning) — preferida
> abortar por ser valoración; (3) si la normalización vive en un **helper compartido**
> reutilizado por `InputsPorTramo.from_valores` y por `tools/multiescenarios.py`
> (`trm_final`), para una sola fuente de verdad; (4) cómo se **ancla** la banda a la TRM
> implícita del 597 para la fecha de corte (opcional, si se quiere validar cercanía además
> de la banda); y (5) un **test de equivalencia** del VM (snapshot antes/después) que
> confirme que, con TRM en banda, el VM de todos los títulos (USD y COP) queda idéntico, y
> que con TRM mal escalada el VM USD recupera el factor de unidad. Las cinco propiedades son
> directamente codificables como pruebas basadas en propiedades (PBT con Hypothesis).
