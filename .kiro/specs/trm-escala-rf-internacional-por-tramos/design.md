# Escala de la TRM en RF Internacional por tramos — Bugfix Design

## Overview

En la proyección por tramos (`modo_proyeccion = "por_tramos"`) y en multiescenarios, el
`valor_mercado` (VM) en COP de los títulos de **Renta Fija Internacional en USD** (Yankees /
globales) sale escalado por un factor de unidad erróneo (típicamente **÷1000**) cuando la
**TRM de cierre** se ingresa con la unidad equivocada (p. ej. `3.9` en escala "miles" en vez
de `3900` como nivel COP/USD). La valoración es correcta; el defecto es **puramente de
unidad del input de TRM de cierre**.

**Enfoque elegido:** normalizar la **unidad** de `trm_cierre` en el **punto de ingreso** del
input de usuario, mediante un **helper puro compartido** (`normalizar_trm_nivel`) reutilizado
por:
1. `InputsPorTramo.from_valores` (ruta por tramos), y
2. `tools/multiescenarios.py` (campo `trm_final`).

El helper:
- Es **identidad** cuando la TRM ya está en la banda plausible de nivel COP/USD (Property 2).
- **Normaliza por factores de 1000** (×1000 o ÷1000) cuando la TRM está fuera de banda pero
  es un error de unidad recuperable (Property 1 y 3).
- **Aborta con error verificable** (`ValueError`) cuando la TRM es finita, no vacía, está
  fuera de banda y NO es normalizable por 1000 (dato arbitrario): NO se valora en silencio
  con una TRM dudosa (Property 4).
- Deja el caso **vacío/ausente/no numérico** al flujo actual de `from_valores` (default
  documentado `4000.0` con warning), que NO forma parte del defecto 2B (Req 3.2).

La **valoración NO se toca** (`valorar_titulo`, `valorar_bono_yankee`), por lo que todos los
flujos quedan idénticos cuando la TRM ya es un nivel correcto (Property 5 + Req 3.x).

> **Alcance:** únicamente la **TRM de cierre** (`trm_cierre` por tramo / `trm_final` de
> multiescenarios), que es el input del usuario/Excel. La **TRM t0** (`_trm_t0`) NO se
> normaliza: proviene de `obtener_trm_implicita` del 597 (dato oficial del sistema, siempre
> en nivel correcto). Este bugfix NO aborda el sub-defecto 2A (semántica `tasa_mercado` =
> T.Dcto) ni el 2C (usos sistémicos de `tasa_mercado`).

## Glossary

- **Bug_Condition (C)**: la condición que dispara el bug — un ingreso de `trm_cierre`
  numérico y finito, fuera de la banda plausible de nivel COP/USD, pero normalizable a la
  banda por factores de 1000 (error de unidad). Formalizada en `isBugCondition`.
- **Property (P)**: el comportamiento deseado — la TRM de cierre llega al motor como un
  nivel COP/USD plausible, de modo que el VM en COP de los títulos USD queda en escala
  correcta.
- **Preservation**: todo lo que `F'` debe dejar idéntico a `F`: TRM ya en banda (identidad),
  TRM vacía (default actual), valoración, títulos COP, demás campos de `InputsPorTramo`, y
  cualquier flujo sin títulos en moneda extranjera.
- **`F` / `F'`**: función original (sin normalizar la unidad de TRM) / función corregida que
  construye el `InputsPorTramo` / `trm_cierre` de multiescenarios desde el input crudo.
- **`normalizar_trm_nivel(trm)`**: helper puro NUEVO (en
  `scripts/proyeccion_tramos_multiactivo.py`) que normaliza la unidad de un nivel de TRM.
- **`en_banda(x)`**: predicado de banda plausible de nivel COP/USD. Banda elegida:
  **`500 ≤ x ≤ 20000`** (ver "Decisión de diseño: banda plausible").
- **`InputsPorTramo.from_valores`**: punto de ingreso de inputs no confiables por tramo en
  `scripts/proyeccion_tramos_multiactivo.py` (ya coerciona cada campo con
  `_coerce_float_finito` y defaults `_DEFAULTS_INPUTS_TRAMO`; `trm_cierre` default `4000.0`).
- **`valorar_titulo` / `valorar_bono_yankee`**: valoración (rama YANKEE
  `nominal * precio_sucio/100 * trm`), CORRECTA; NO se toca.
- **TRM t0 (`_trm_t0`)**: nivel de TRM al inicio (implícita del 597 vía
  `obtener_trm_implicita` / `obtener_trm_para`); dato del sistema, NO se normaliza.
- **TRM de cierre (`trm_cierre` / `trm_final`)**: nivel de TRM al corte del tramo
  (`TRM_Por_Tramo`), input del usuario/Excel; ES lo que se normaliza.

## Bug Details

### Bug Condition

El bug se manifiesta cuando el ingreso de `trm_cierre` es un número finito fuera de la banda
plausible de nivel COP/USD, pero normalizable a la banda por factores de 1000 (error de
unidad). En ese caso el VM en COP de los títulos USD queda escalado por el factor erróneo.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X = (origen, trm_cierre_crudo)
  OUTPUT: boolean

  trm := coerce_float_finito(X.trm_cierre_crudo, default = NaN)
  RETURN is_finite(trm)
         AND NOT en_banda(trm)                   // fuera de [500, 20000]
         AND es_normalizable_por_1000(trm)       // ∃ k≠0 entero: en_banda(trm * 1000^k)
END FUNCTION
```

### Mecánica exacta del defecto (verificada)

- `valorar_titulo` (rama YANKEE) y `valorar_bono_yankee` calculan
  `VM_cop = nominal * precio_sucio/100 * trm`. Con `trm = 3900` el VM de un nominal de
  1.000.000 USD queda en miles de millones de COP; con `trm = 3.9` queda ÷1000.
- La TRM de cierre llega al motor sin normalizar su unidad:
  - **por tramos:** `app_dash.py` grid `inputs_por_tramo['trm_cierre'] = _g(tramo_trm, i)`
    (~L6065) → `InputsPorTramo.from_valores(..., trm_cierre=...)` → `InputsPorTramo.trm_cierre`
    → `_trm_tramo_fin` → `proyectar_portafolio(..., trm_cierre=...)`.
  - **multiescenarios:** `tools/multiescenarios.py` `trm_cierre = float(esc["trm_final"])`
    (~L533) → `proyectar_portafolio(..., trm_cierre=...)`.
- `InputsPorTramo.from_valores` hoy solo hace `_coerce_float_finito` (float finito), sin
  validar la **escala** del nivel.

### Examples

- Título 859370 `BN YANKEES` (ISIN `US195325DP79`), nominal 1.000.000 USD:
  - `trm = 3900` → VM ≈ `4.543.211.556` COP (correcto).
  - `trm = 3.9`  → VM ≈ `4.543.211,56` COP (÷1000, defectuoso).
  - `normalizar_trm_nivel(3.9) = 3900` ⇒ VM vuelve a ≈ `4.543.211.556` COP.
- `normalizar_trm_nivel(3900) = 3900` (identidad; TRM ya correcta no cambia).
- `normalizar_trm_nivel(150)` ⇒ `ValueError` (150, 150000, 150000000, 0.15 … ninguno cae en
  `[500, 20000]`): dato arbitrario, no se valora en silencio.

## Expected Behavior

### Preservation Requirements

**Comportamientos que NO deben cambiar (idénticos a `F`):**
- **TRM ya en banda** (`500 ≤ trm ≤ 20000`): identidad; mismo nivel y mismo VM para TODOS
  los títulos (Req 3.1).
- **TRM vacía / `None` / no numérica / no finita**: default documentado `4000.0` con warning
  genérico, exactamente como hoy (Req 3.2) — NO es parte del defecto 2B.
- **Valoración**: `valorar_titulo` / `valorar_bono_yankee` sin cambios (Req 3.3).
- **Títulos COP**: mismo VM (no multiplican por TRM) (Req 3.4).
- **Demás campos de `InputsPorTramo`** (inflación, repo, bps, retornos EA): intactos; la
  guarda SOLO aplica a `trm_cierre` (Req 3.5).
- **Flujos sin moneda extranjera**: resultado idéntico (Req 3.6).
- **TRM t0** (`_trm_t0`, implícita del 597): NO se normaliza (dato del sistema).

## Hypothesized Root Cause

Diagnóstico ya verificado (no requiere re-investigación):

1. **Unidad del input de TRM de cierre no validada (causa principal, confirmada):**
   `InputsPorTramo.from_valores` y `tools/multiescenarios.py` pasan `trm_cierre` /
   `trm_final` tal cual como nivel, sin normalizar la escala. Un `3.9` (miles) produce VM
   USD ÷1000.
2. **No es causa:** la valoración (`valorar_titulo` / `valorar_bono_yankee`) es correcta
   (aplica la TRM como nivel COP/USD). El fix NO la toca.
3. **Fuera de alcance (no es este bug):** la semántica de `tasa_mercado` (2A) y sus usos
   sistémicos (2C). Son defectos separados y más delicados.

## Correctness Properties

Property 1: Bug Condition — Normalización de TRM fuera de banda por factores de 1000

_For any_ ingreso `X` que cumple `isBugCondition` (TRM finita, fuera de banda, normalizable
por 1000), `normalizar_trm_nivel` SHALL devolver un nivel `trm'` tal que `en_banda(trm')` y
`trm' = trm_crudo × 1000^k` para algún entero `k ≠ 0` (solo se ajusta la unidad, no el valor
relativo).

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation — Identidad para TRM ya en banda

_For any_ `trm` tal que `en_banda(trm)`, `normalizar_trm_nivel(trm) = trm` (exacto); una TRM
correcta nunca se reescala.

**Validates: Requirements 2.3, 3.1**

Property 3: Fix — Escala correcta del VM USD (consistencia extremo a extremo)

_For any_ `X` que es un error de ×1000 (`trm_crudo = trm_correcta / 1000`), el VM en COP de
un título USD valorado con `normalizar_trm_nivel(trm_crudo)` SHALL ser ≈ `1000 ×` el VM
valorado con `trm_crudo` (recupera el factor de unidad), e igual (tol. relativa ~1e-9) al VM
valorado con la `trm_correcta` directa.

**Validates: Requirements 2.1, 2.2**

Property 4: Fix — Rechazo verificable de TRM no normalizable

_For any_ `trm` finita, fuera de banda y NO normalizable por 1000 (dato arbitrario),
`normalizar_trm_nivel(trm)` SHALL lanzar `ValueError` (política explícita y trazable): no se
valora en silencio ni se enmascara con un default.

**Validates: Requirements 2.4**

Property 5: Preservation — Campos no-TRM y valoración intactos

_For any_ ingreso `X`, `F'` SHALL dejar idénticos todos los campos de `InputsPorTramo`
distintos de `trm_cierre`, y NO modificar la valoración; para TRM en banda, el VM de todos
los títulos (USD y COP) es idéntico a `F`.

**Validates: Requirements 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Decisión de diseño: banda plausible

**Banda elegida: `500 ≤ trm ≤ 20000`.** Justificación:
- La TRM COP/USD histórica y previsible vive holgadamente dentro de `[500, 20000]` (nunca ha
  estado cerca de esos extremos; el nivel real ronda 3000–5000).
- La guarda histórica citada en la deuda técnica (`2000–8000`) es más estrecha y podría
  rechazar escenarios de estrés legítimos (p. ej. una TRM de pronóstico de 9000–12000). Se
  opta por una banda **más amplia** `[500, 20000]` para no bloquear escenarios extremos
  legítimos, pero suficientemente estrecha para detectar errores de unidad de ×1000 (un
  `3.9` o un `3.900.000` quedan claramente fuera).
- La banda se define como **constantes de módulo** (`TRM_MIN_PLAUSIBLE = 500.0`,
  `TRM_MAX_PLAUSIBLE = 20000.0`) para una sola fuente de verdad y fácil ajuste.

> Nota: la banda es un criterio de **escala/unidad**, no de pronóstico. No se ancla al nivel
> exacto de `obtener_trm_implicita` (que es t0); eso añadiría acoplamiento innecesario y
> podría rechazar pronósticos legítimos lejanos del t0. La banda ancha cumple el objetivo
> (detectar ×1000) sin ese acoplamiento.

### Decisión de diseño: política de fuera-de-banda no normalizable

**Abortar con `ValueError`** (no default silencioso). Justificación: es valoración
financiera; un valor como `150` no es ni nivel ni un error claro de ×1000, y valorar con él
(o con un default `4000`) produciría un VM plausible pero **incorrecto** sin que nadie lo
note. Un error explícito y verificable obliga a corregir el input. (El caso vacío/no numérico
NO llega aquí: lo intercepta antes el flujo de default de `from_valores`.)

### Archivos y funciones a cambiar

**Archivo 1:** `scripts/proyeccion_tramos_multiactivo.py`

1. **Nuevas constantes de módulo** (junto a `_DEFAULTS_INPUTS_TRAMO`):
   ```python
   TRM_MIN_PLAUSIBLE = 500.0
   TRM_MAX_PLAUSIBLE = 20000.0
   ```

2. **Nuevo helper puro** `normalizar_trm_nivel(trm: float) -> float`:
   - Precondición: `trm` ya coercionado a `float` finito por el llamador (si no, el llamador
     aplica su default ANTES — este helper asume un número finito).
   - Si `en_banda(trm)` ⇒ devolver `trm` sin cambios (identidad, Property 2).
   - Si no, probar `trm × 1000^k` para `k` en un rango acotado (p. ej. `-3..3`): si algún
     `k ≠ 0` cae en banda, devolver ese valor normalizado y emitir `logger.warning` genérico
     (Property 1 y 3). Elegir el `k` de menor `|k|` (el ajuste de unidad más cercano).
   - Si ningún `k` cae en banda ⇒ `raise ValueError("TRM de cierre fuera de banda plausible; "
     "revisa la unidad del input.")` (mensaje genérico, sin volcar el valor; Property 4).
   - El helper NO toca ningún otro campo.

3. **`InputsPorTramo.from_valores`**: tras coercionar `trm_cierre` por el flujo actual
   (default `4000.0` si vacío/no numérico — Req 3.2), aplicar `normalizar_trm_nivel` SOLO al
   valor coercionado de `trm_cierre` cuando NO cayó al default por vacío. Es decir:
   - Si `trm_cierre` crudo era vacío/no numérico ⇒ se mantiene el default `4000.0` (ya en
     banda; `normalizar_trm_nivel(4000.0)` sería identidad de todos modos).
   - Si `trm_cierre` crudo era un número finito ⇒ pasar por `normalizar_trm_nivel` (identidad
     si en banda; normaliza si error de unidad; `ValueError` si no normalizable).
   - Los demás campos quedan EXACTAMENTE igual (Property 5, Req 3.5).

**Archivo 2:** `tools/multiescenarios.py`

4. En el punto `trm_cierre = float(esc["trm_final"])` (~L533), envolver con el mismo helper:
   `trm_cierre = normalizar_trm_nivel(float(esc["trm_final"]))` (import desde
   `proyeccion_tramos_multiactivo`). Una sola fuente de verdad para la banda y la política.

**Lo que NO se cambia:** `valorar_titulo`, `valorar_bono_yankee`, `motor_proyeccion`
(valoración), la TRM t0 (`_trm_t0`, dato del 597), ni los demás campos de `InputsPorTramo`.

### Cómo se garantiza cada propiedad

- **Property 1 (normalización):** el barrido de `k` en `-3..3` encuentra el factor de 1000
  que mete la TRM en banda; se elige el de menor `|k|`.
- **Property 2 (identidad en banda):** la primera rama `if en_banda(trm): return trm` es
  identidad exacta.
- **Property 3 (escala VM USD):** como `valorar_titulo` multiplica linealmente por `trm`,
  normalizar `trm` por ×1000 escala el VM por ×1000; con la TRM normalizada el VM iguala
  (tol. ~1e-9) al VM con la TRM correcta directa.
- **Property 4 (rechazo):** si ningún `k` cae en banda, `ValueError` explícito.
- **Property 5 (campos no-TRM / valoración):** el helper solo recibe y devuelve el nivel de
  TRM; `from_valores` lo aplica únicamente al campo `trm_cierre`; la valoración no se toca.

## Testing Strategy

### Validation Approach

Dos fases: (1) **unit + PBT del helper puro** `normalizar_trm_nivel` (aísla la lógica de
unidad, sin BD); (2) **test de equivalencia del VM** que confirme la escala correcta del VM
USD extremo a extremo (con un título Yankee representativo), y la identidad del VM para TRM
en banda.

Pruebas en `tests/`, convención existente (pytest + Hypothesis + `conftest.py`). Flaguear las
PBT. Ejecutar con ruta absoluta + `--rootdir` + `-o addopts=""` por el entorno (filesystem
Google Drive); usar `deadline=None` y suprimir `HealthCheck.too_slow`/`function_scoped_fixture`
si una prueba toca el fixture de BD.

### Fix Checking (Hypothesis)

```
FOR ALL trm_correcta en banda, k en {-2,-1,1,2} DO
  trm_cruda := trm_correcta * 1000^(-k)          // error de unidad
  ASSERT en_banda(normalizar_trm_nivel(trm_cruda))
  ASSERT normalizar_trm_nivel(trm_cruda) ~= trm_correcta   // recupera el nivel
END FOR

FOR ALL trm finita fuera de banda y NO normalizable por 1000 DO
  ASSERT raises ValueError: normalizar_trm_nivel(trm)
END FOR
```

### Preservation Checking (Hypothesis)

```
FOR ALL trm en banda DO
  ASSERT normalizar_trm_nivel(trm) == trm                  // identidad exacta

FOR ALL inputs crudos X DO
  inp := InputsPorTramo.from_valores(**X)
  FOR campo != "trm_cierre" DO
    ASSERT inp[campo] == from_valores_sin_guarda(**X)[campo]   // campos no-TRM intactos
```

### Unit Tests

- `normalizar_trm_nivel(3900) == 3900` (identidad).
- `normalizar_trm_nivel(3.9) == 3900` y `normalizar_trm_nivel(3_900_000) == 3900`.
- `normalizar_trm_nivel(150)` → `ValueError`.
- `InputsPorTramo.from_valores(trm_cierre="")` → `trm_cierre == 4000.0` (default, Req 3.2).
- `InputsPorTramo.from_valores(trm_cierre=3.9)` → `trm_cierre == 3900.0` (normalizado).
- `InputsPorTramo.from_valores(trm_cierre=4100)` → `trm_cierre == 4100.0` (en banda, identidad).

### Equivalence / Integration Test (VM USD)

- Valorar un título Yankee representativo (nominal 1.000.000 USD, precio base) con `trm=3900`
  y con `normalizar_trm_nivel(3.9)=3900`: el VM en COP debe ser idéntico (tol. ~1e-6).
- Con la TRM mal escalada SIN normalizar (`3.9`) el VM es ÷1000 del normalizado (confirma el
  factor de unidad). Esta prueba NO modifica `valorar_titulo`; solo lo invoca con la TRM
  normalizada vs. cruda para contrastar la escala.
- Con TRM ya en banda, el VM de un portafolio mixto COP+USD es idéntico antes/después de
  introducir la guarda (equivalencia).

### Property-Based Tests (Hypothesis)

- **PBT Property 1 (normalización):** generar `trm_correcta ∈ [500,20000]` y `k ∈ {-2,-1,1,2}`;
  `normalizar_trm_nivel(trm_correcta·1000^{-k}) ≈ trm_correcta` y en banda.
- **PBT Property 2 (identidad en banda):** `trm ∈ [500,20000]` ⇒ `normalizar_trm_nivel(trm)==trm`.
- **PBT Property 3 (escala VM USD):** VM USD con TRM normalizada ≈ 1000× VM con TRM ÷1000.
- **PBT Property 4 (rechazo):** `trm` finita, fuera de banda, no normalizable ⇒ `ValueError`.
- **PBT Property 5 (campos no-TRM):** `from_valores` con guarda deja todos los campos ≠
  `trm_cierre` idénticos a `from_valores` sin guarda.

## Preservation / Regression Considerations

- **Flujos con TRM correcta intactos:** la banda `[500,20000]` cubre todos los niveles reales
  y de estrés plausibles; `normalizar_trm_nivel` es identidad para ellos ⇒ cero cambio
  observable en corridas con TRM bien ingresada (la mayoría).
- **Caso vacío/no numérico:** sigue el default `4000.0` actual de `from_valores` (Req 3.2);
  la guarda no se dispara ahí.
- **Multiescenarios:** usa el MISMO helper (una sola fuente de verdad), de modo que la
  política de banda/normalización/rechazo es consistente entre por-tramos y multiescenarios.
- **TRM t0 no afectada:** `_trm_t0` viene del 597 (implícita), siempre en nivel; no se
  normaliza (evita acoplar el fix a la fuente oficial).
- **No se toca valoración:** `valorar_titulo` / `valorar_bono_yankee` quedan intactos, así
  que RF local y cualquier título COP no cambian; el único efecto es la escala correcta de
  los USD cuando el input de TRM venía mal.
- **Sub-defectos 2A/2C fuera de alcance:** este fix NO cambia `tasa_mercado` ni su carga;
  quedan documentados aparte en `docs/deuda-tecnica.md` para un bugfix posterior.
