# TRM inicial de tramo consistente — Bugfix Design

## Overview

En el modo de proyección **"por_tramos"** (multiactivo) conviven dos definiciones
incompatibles del "nivel de TRM al arranque del tramo":

- La **correcta** (`_trm_tramo_ini` en `app_dash._ejecutar_escenario_por_tramos`): tramo 0 =
  `_trm_t0` (TRM implícita real del informe 597, p. ej. 3144.14), tramo i>0 =
  `estado_previo.trm_cierre` (encadenado). La usa **Renta Fija (RF)** vía
  `proyectar_portafolio(trm=_trm_tramo_ini, ...)` (~L8072). **RF es correcta y debe
  permanecer idéntica.**
- La **incorrecta** (el primer nodo de la senda interpolada, `senda[0]`): la leen el
  **efecto moneda** (`_extremos_senda_trm(sendas_tramo.senda_trm)[0]` ~L8127 →
  `calcular_efecto_moneda_tramo(trm_inicio=_trm_ini_tr, ...)` ~L8158-8165 y
  `_reg["trm_inicio"]` ~L8228), los **derivados** (`nivel_actual = senda_trm[0]` ~L7552) y
  **RV / Alternativos internacional** (vía `proyectar_bloque`, que ancla la conversión a USD
  en `trm_t0 = senda_trm[primera_fecha]`).

El arreglo introduce una **única fuente de verdad** del nivel inicial del tramo
(`trm_inicio_real`), ya existente y correcta (`_trm_tramo_ini`), y la propaga de forma
consistente a derivados, efecto moneda y al ancla de conversión USD de RV/Alt internacional,
**sin tocar RF** y **sin cambiar la forma de la senda interpolada** (su último nodo sigue
siendo exactamente `trm_cierre`).

### Recapitulación de la causa raíz

1. `interpolar_trm_tramo` (`scripts/proyeccion_tramos_multiactivo.py` ~L254-320) construye
   deliberadamente el PRIMER nodo ya avanzado un paso:
   `senda[0] = trm_inicio + (trm_cierre - trm_inicio)/n`. La TRM real de t0 nunca es un nodo
   de la senda. El ÚLTIMO nodo sí es exactamente `trm_cierre` (invariante correcto).
2. `construir_sendas_tramo` (~L672-760) fija `trm_inicio = trm_cierre` para el tramo 0
   (`estado_previo is None`), dejando la senda del tramo 0 **plana** en `trm_cierre`
   (≈3337), de modo que `senda[0]` ≈ 3337 en vez de 3144.14.
3. Los consumidores basados en senda (`derivados`, `efecto moneda`, `RV/Alt intl`) leen ese
   `senda[0]` desfasado, mientras RF usa el nivel real → inconsistencia interna del tramo y
   encadenamiento sucio entre tramos.

## Glossary

- **Bug_Condition (C)**: condición que dispara el bug — algún consumidor del nivel inicial
  del tramo (derivados `nivel_actual`, efecto moneda `trm_inicio`, ancla USD de RV/Alt intl)
  usa `senda[0]` en lugar de la TRM real de arranque del tramo.
- **Property (P)**: comportamiento deseado — todos esos consumidores usan la MISMA
  `trm_inicio_real` que ya usa RF.
- **Preservation**: comportamiento que NO debe cambiar — valoración RF numéricamente
  idéntica, `trm_cierre` sin cambios, último nodo de la senda == `trm_cierre`, modo punta a
  punta inalterado, semántica documentada de `interpolar_trm_tramo` preservada.
- **`trm_inicio_real(X)`**: fuente de verdad del nivel inicial del tramo X. Tramo 0 =
  `_trm_t0 = obtener_trm_para(fecha_base)` (597). Tramo i>0 = `estado_previo.trm_cierre`.
  **Ya existe hoy** como `_trm_tramo_ini` en `app_dash._ejecutar_escenario_por_tramos`.
- **`_trm_tramo_ini`**: variable local en `_ejecutar_escenario_por_tramos` (~L7971-7975) que
  ya materializa `trm_inicio_real`; la consume RF correctamente.
- **`senda[0]` / `_trm_ini_tr`**: primer nodo de la senda interpolada (avanzado un paso; o
  plano en `trm_cierre` para el tramo 0). Es el valor ERRÓNEO que leen hoy los consumidores
  no-RF.
- **`_correr_derivados_tramo`**: adaptador por tramo del Motor_Derivados en `app_dash`
  (~L7511) que calcula `nivel_actual = senda_trm[0]` (~L7552) como spot de m=0.
- **`calcular_efecto_moneda_tramo`**: función de `scripts/proyeccion_tramos_multiactivo.py`
  que descompone PyG en efecto moneda vs efecto retorno usando `trm_inicio`.
- **`proyectar_bloque`**: bloque de valoración internacional en
  `scripts/proyeccion_renta_variable.py` (~L143) y `scripts/proyeccion_alternativos.py`
  (~L243) que convierte VM COP→USD con `trm_t0 = senda_trm[primera_fecha]`.
- **`fecha_mercado` / `_fecha_pip_str`**: fecha base real de las curvas de mercado que cargan
  los derivados (`_cargar_curvas`); la TRM del spot debe ser consistente con ella.

## Bug Details

### Bug Condition

El bug se manifiesta cuando se ejecuta un tramo del escenario "por_tramos" (multiactivo) y
algún consumidor del nivel inicial del tramo (derivados, efecto moneda, RV/Alt internacional)
lee el primer nodo de la senda interpolada (`senda[0]`) —que está avanzado un paso o es plano
en `trm_cierre` para el tramo 0— en lugar de la TRM real de arranque del tramo
(`trm_inicio_real`).

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X = un tramo del escenario "por_tramos" (multiactivo)
  OUTPUT: boolean

  // trm_inicio_real(X):
  //   tramo 0  -> obtener_trm_para(fecha_base)   (597, p. ej. 3144.14)
  //   tramo i>0 -> estado_previo.trm_cierre        (encadenado)
  // consumidores_nivel_inicio(X): nivel que usan efecto moneda, derivados (nivel_actual)
  //   y la conversión USD de RV/Alt internacional (proyectar_bloque.trm_t0).

  RETURN consumidores_nivel_inicio(X) != trm_inicio_real(X)
END FUNCTION
```

### Examples

- **Tramo 0 (31-jul-2026), efecto moneda**: esperado `trm_inicio = 3144.14`; actual
  `trm_inicio = 3337.0` (registrado en `informe_pyg_por_tramo`). → bug.
- **Tramo 0, derivados**: esperado spot m=0 = 3144.14 (consistente con las curvas cargadas en
  la fecha base real); actual `nivel_actual ≈ 3337` → desfase spot/curva ~193 COP y
  `activos_usd = sum(usd_vm)/nivel_actual` subestimado ~6%.
- **Tramo 0, RV/Alt intl**: `proyectar_bloque` convierte VM COP→USD con `trm_t0 ≈ 3337` en
  vez de 3144.14 → VM inicial en USD mal anclado.
- **Encadenamiento**: el tramo 2026 cierra 3337 pero el 2027 "arranca" 3349.25 según el
  efecto moneda (observado por consumidores de senda), rompiendo
  `trm_inicio(i) == trm_cierre(i-1)`.
- **Caso límite (correcto, a preservar)**: el ÚLTIMO nodo de la senda == `trm_cierre` exacto;
  RF usa `_trm_tramo_ini` correcto. Estos no deben cambiar.

## Expected Behavior

### Preservation Requirements

**Comportamientos que NO deben cambiar:**
- **RF idéntica**: la valoración de Renta Fija de cualquier tramo (que ya usa
  `_trm_tramo_ini` y lo pasa a `proyectar_portafolio`) produce resultados numéricamente
  idénticos a los actuales (Req 3.1).
- **`trm_cierre` sin cambios**: el nivel de cierre del tramo (input/pronóstico
  `TRM_Por_Tramo`) se usa igual que hoy; solo se corrige el anclaje del INICIO (Req 3.2).
- **Último nodo de la senda**: `interpolar_trm_tramo` sigue anclando el último corte
  EXACTAMENTE en `trm_cierre` (Req 3.3).
- **Punta a punta inalterado**: el modo no-por-tramos no se ve afectado (Req 3.4).
- **TRM t0 del 597 sin hardcodes**: la TRM t0 se sigue tomando de
  `obtener_trm_para(fecha_base)`, con logs genéricos y SQL de solo lectura parametrizado
  (Req 3.5).

**Alcance:** todo input que NO corresponda a los consumidores del nivel inicial del tramo
(RF, el cuerpo de la senda intermedia, el modo punta a punta) queda completamente inalterado.

## Hypothesized Root Cause

1. **Doble definición del "inicio del tramo"**: `_trm_tramo_ini` (correcto, RF) vs `senda[0]`
   (incorrecto, resto). La semántica de "primer nodo avanzado un paso" de
   `interpolar_trm_tramo` hace que `senda[0] ≠ trm_inicio_real` por diseño.
2. **Tramo 0 plano**: `construir_sendas_tramo` fija `trm_inicio = trm_cierre` cuando
   `estado_previo is None`, por lo que `senda[0]` del tramo 0 nunca refleja la TRM t0 real.
3. **Consumidores acoplados a la senda**: derivados (`senda_trm[0]`), efecto moneda
   (`_extremos_senda_trm(...)[0]`) y RV/Alt intl (`proyectar_bloque.trm_t0 =
   senda[primera_fecha]`) toman su ancla inicial de la senda en vez de la fuente de verdad.
4. **Spot/curva desacoplados en derivados**: las curvas se cargan en la fecha base real
   (`_cargar_curvas(_fecha_mkt)`) pero el spot m=0 viene de `senda[0]` → inconsistencia.

## Decisión de diseño: Opción A vs B (vs híbrida)

El mecanismo exacto quedó abierto en los requisitos. Se evalúan tres mecanismos.

### Opción A — Pasar `trm_inicio_real` explícito a los consumidores

Reutilizar `_trm_tramo_ini` (ya correcto) y pasarlo como `nivel_actual` a derivados y como
`trm_inicio` al efecto moneda, en vez de `senda[0]`/`_extremos_senda_trm(...)[0]`. La senda
interpolada no cambia.

- ✔ Mínima y quirúrgica; no toca `interpolar_trm_tramo` ni sus tests.
- ✔ Arregla derivados y efecto moneda.
- ✖ **No arregla RV/Alt internacional por sí sola**: `_correr_rv_tramo` /
  `_correr_alt_tramo` pasan `sendas_tramo.senda_trm` al motor, y `proyectar_bloque` deriva su
  ancla USD de `senda[primera_fecha]` internamente. Sin tocar ese ancla, RV/Alt intl seguiría
  mal anclada. Es decir, A deja fuera a un consumidor clave (viola P1 para RV/Alt).

### Opción B — Hacer que la senda arranque en el `trm_inicio_real`

Cambiar la construcción de la senda para que `senda[0] == trm_inicio_real` (y para el tramo 0
pasar `trm_inicio=_trm_t0` a `construir_sendas_tramo` en vez del default `trm_cierre`), de
modo que TODOS los consumidores lean un inicio consistente.

- ✔ Arregla todos los consumidores de un solo lugar, incluido el ancla USD de RV/Alt.
- ✖ **Cambia la semántica documentada de `interpolar_trm_tramo`** ("primer nodo avanzado un
  paso"), que está **fijada por tests existentes**:
  - `tests/test_tramos_multiactivo_primitivas.py` (~L207): afirma explícitamente que el paso
    es `(trm_cierre - trm_inicio)/n` y que "el primer corte ya avanzó un paso".
  - `tests/test_property_uvr_preservacion_por_tramos.py` (~L234): declara que esta lógica
    "NO debe cambiar".
- ✖ Riesgo de romper la monotonicidad/forma de la senda intermedia que consume RF-indexado y
  el cuerpo de la senda para los cortes mensuales de los motores (añadir un nodo de anclaje o
  redefinir el primer paso altera el número de puntos y el PyG mensual de RV/Alt).
- ✖ Mayor superficie de regresión por un objetivo (el ancla inicial) que es puntual.

### Opción híbrida (RECOMENDADA)

Mantener `interpolar_trm_tramo` **sin cambios** (preserva su semántica documentada, sus tests
y el invariante último nodo == `trm_cierre`) e introducir un **único `trm_inicio_real`**
—reutilizando `_trm_tramo_ini`, que ya existe y es correcto— que se propaga a los tres
consumidores no-RF:

1. **Derivados**: pasar `trm_inicio_real` como `nivel_actual` (spot m=0), en vez de
   `senda_trm[0]`.
2. **Efecto moneda**: pasar `trm_inicio_real` como `trm_inicio` a
   `calcular_efecto_moneda_tramo` y a `_reg["trm_inicio"]`, en vez de
   `_extremos_senda_trm(...)[0]`. El `trm_cierre` del efecto moneda se sigue tomando de la
   senda (`_extremos_senda_trm(...)[1]`, == `trm_cierre`, correcto).
3. **RV/Alt internacional**: anclar la conversión USD en `trm_inicio_real`. Como
   `proyectar_bloque` ya acepta `senda_trm` como fuente del ancla, el ancla se corrige
   **sin cambiar la forma de la senda** inyectando el nodo inicial real **solo para el ancla
   de conversión** (ver mecanismo abajo), de modo que `proyectar_bloque.trm_t0 ==
   trm_inicio_real` mientras los cortes mensuales de la senda que mueven el VM intermedio
   permanecen idénticos.

**Mecanismo para el ancla USD de RV/Alt (sin alterar la senda intermedia).** Hoy
`proyectar_bloque` toma `trm_t0 = senda_trm[periodos[0]]`. Para que el ancla sea
`trm_inicio_real` **sin** modificar los nodos mensuales de la senda, se añade a `SendasTramo`
el nivel inicial real como campo explícito (`trm_inicio_real: float`) y los adaptadores
`_correr_rv_tramo`/`_correr_alt_tramo` pasan ese valor a `proyectar_bloque` como parámetro de
ancla (`trm_inicio_ancla`), con **default = comportamiento actual** (`senda[primera_fecha]`)
para no afectar a ningún otro llamador. Así:
- El ancla de conversión USD = `trm_inicio_real` (corrige el bug).
- Los cortes mensuales que mueven el VM (`senda_trm[periodo]`) siguen siendo los mismos nodos
  interpolados (preserva la forma y el PyG mensual).
- `interpolar_trm_tramo` queda intacto (preserva P2 y sus tests).

**Justificación de la elección.** La híbrida es la única que (a) arregla TODOS los
consumidores —incluida RV/Alt intl, que A no cubre—; (b) preserva RF idéntica (no toca su
ruta); (c) preserva el invariante último nodo == `trm_cierre` y la semántica documentada de
`interpolar_trm_tramo` —que B rompería y que está fijada por tests—; (d) garantiza el
encadenamiento `trm_inicio(i) == trm_cierre(i-1)` porque todos leen el mismo
`_trm_tramo_ini`, que para i>0 ya es `estado_previo.trm_cierre`. Es la de menor superficie de
regresión por consumidor arreglado.

## Correctness Properties

Property 1: Bug Condition — Nivel inicial consistente en todos los consumidores

_For any_ tramo X donde se cumple la condición de bug (`isBugCondition(X)` == true), la
función corregida F' SHALL hacer que el nivel inicial usado por los derivados (`nivel_actual`,
spot m=0), por el efecto moneda (`trm_inicio`) y por el ancla de conversión USD de RV/Alt
internacional (`proyectar_bloque.trm_t0`) sea EXACTAMENTE `trm_inicio_real(X)` (TRM t0 real
del 597 para el tramo 0; `trm_cierre` del tramo anterior para i>0), coincidiendo con el nivel
que ya usa RF (`_trm_tramo_ini`).

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — Invariante del último nodo de la senda

_For any_ tramo X, el último nodo de la senda interpolada por `interpolar_trm_tramo` SHALL
seguir siendo EXACTAMENTE `trm_cierre(X)`, sin residuo de redondeo; la función corregida F'
produce la misma senda que F para esta función (no se modifica `interpolar_trm_tramo`).

**Validates: Requirements 3.3**

Property 3: Preservation — Valoración RF idéntica

_For any_ tramo X, la valoración de Renta Fija de F' SHALL coincidir numéricamente con la de F
(la ruta RF usa `_trm_tramo_ini`, que no cambia).

**Validates: Requirements 3.1**

Property 4: Fix Checking — Encadenamiento de tramos

_For any_ escenario con tramos [t0, t1, ..., tN] y para todo i de 1 a N, el nivel inicial del
tramo i observado por TODOS los consumidores SHALL ser EXACTAMENTE `trm_cierre(t_{i-1})`.

**Validates: Requirements 2.5**

Property 5: Fix Checking — Spot de derivados consistente con la fecha de curvas

_For any_ tramo X donde se cumple la condición de bug, el spot m=0 usado por los derivados
(`nivel_actual`) SHALL ser igual a la TRM de la fecha base de las curvas de mercado cargadas
(`obtener_trm_para(fecha_mercado_curvas(X))`), eliminando el desfase spot/curva.

**Validates: Requirements 2.2**

Property 6: Preservation — Modo punta a punta inalterado

_For any_ escenario E en modo "punta a punta" (no por tramos), F'(E) SHALL producir
exactamente el mismo resultado que F(E).

**Validates: Requirements 3.4**

## Fix Implementation

### Changes Required

Asumiendo que el análisis de causa raíz es correcto, hay **una única fuente de verdad**
(`_trm_tramo_ini`) que debe alimentar a los tres consumidores no-RF.

#### 1. Derivados — `nivel_actual`

**Archivo**: `app_dash.py`
**Función**: `_correr_derivados_tramo` (~L7511), más la llamada en
`_ejecutar_escenario_por_tramos` (~L7987-8006).

El spot m=0 debe ser `trm_inicio_real`, no `senda_trm[0]`. Se añade un parámetro explícito
`trm_inicio_real` al adaptador (default = comportamiento actual) y se pasa `_trm_tramo_ini`
desde el orquestador.

```python
# app_dash._correr_derivados_tramo  (~L7549-7552)
def _correr_derivados_tramo(tramo, inputs_tramo, sendas_tramo, conn,
                            metodo_deriv=1, metodo_params_extra=None,
                            plano_inicial=None, fecha_mercado=None,
                            trm_inicio_real=None):          # NUEVO (default: senda[0])
    ...
    senda_items = sorted(sendas_tramo.senda_trm.items())
    senda_trm = [float(v) for _, v in senda_items]
    # Spot m=0 = TRM real de arranque del tramo (fuente de verdad), consistente con
    # la fecha de las curvas de mercado. Fallback retrocompatible: primer nodo de la senda.
    if trm_inicio_real is not None:
        nivel_actual = float(trm_inicio_real)
    else:
        nivel_actual = senda_trm[0] if senda_trm else float(inputs_tramo.trm_cierre)
```

```python
# app_dash._ejecutar_escenario_por_tramos  (~L7998, en la llamada a _correr_derivados_tramo)
deriv_ultimo = _correr_derivados_tramo(
    tramo, inputs_tramo_i, sendas_tramo, conn,
    metodo_deriv=metodo_deriv, plano_inicial=_plano_ini,
    fecha_mercado=_fecha_pip_str,
    trm_inicio_real=_trm_tramo_ini,      # NUEVO: misma fuente de verdad que RF
)
```

#### 2. Efecto moneda — `trm_inicio` y `_reg["trm_inicio"]`

**Archivo**: `app_dash.py`
**Función**: `_ejecutar_escenario_por_tramos` (~L8127, ~L8158-8165, ~L8228).

Se deja de usar `_trm_ini_tr` (= `senda[0]`) como inicio del efecto moneda; se usa
`_trm_tramo_ini`. El cierre (`_trm_cie_tr` == `trm_cierre`) se mantiene.

```python
# ~L8127  (se conserva _trm_cie_tr; _trm_ini_tr deja de alimentar el inicio)
_trm_ini_tr, _trm_cie_tr = _extremos_senda_trm(sendas_tramo.senda_trm)
# Nivel de arranque REAL del tramo = fuente de verdad (igual que RF). El inicio del
# efecto moneda NO es senda[0] (avanzado un paso / plano en el tramo 0).
_trm_inicio_real = _trm_tramo_ini

# ~L8158-8165
efectos_moneda.extend(calcular_efecto_moneda_tramo(
    df_rv, activo="rv", tramo_indice=tramo.indice, fecha_corte=_corte_iso,
    trm_inicio=_trm_inicio_real, trm_cierre=_trm_cie_tr, col_id="titulo",   # CAMBIO
))
efectos_moneda.extend(calcular_efecto_moneda_tramo(
    df_alt, activo="alternativos", tramo_indice=tramo.indice,
    fecha_corte=_corte_iso, trm_inicio=_trm_inicio_real, trm_cierre=_trm_cie_tr,  # CAMBIO
    col_id="id_posicion",
))

# ~L8228
_reg["trm_inicio"] = float(_trm_inicio_real) if _trm_inicio_real else None   # CAMBIO
_reg["trm_cierre"] = float(_trm_cie_tr) if _trm_cie_tr else None             # igual
```

#### 3. RV/Alt internacional — ancla de conversión USD

**Archivos**: `scripts/proyeccion_renta_variable.py` (~L143 `proyectar_bloque`),
`scripts/proyeccion_alternativos.py` (~L243 `proyectar_bloque`),
`scripts/proyeccion_tramos_multiactivo.py` (`SendasTramo` ~L629, `construir_sendas_tramo`
~L672), `app_dash.py` (`_correr_rv_tramo` ~L7339, `_correr_alt_tramo` ~L7391).

El ancla USD (`trm_t0`) debe ser `trm_inicio_real`, no `senda[primera_fecha]`, SIN alterar los
nodos mensuales de la senda.

a) Añadir el nivel real como campo explícito de `SendasTramo` (no modifica los nodos
   mensuales de `senda_trm`):

```python
# scripts/proyeccion_tramos_multiactivo.py  (SendasTramo, ~L629)
@dataclass(frozen=True)
class SendasTramo:
    senda_trm: dict[str, float]
    senda_ipc: dict[str, float]
    senda_repo: dict[str, float]
    senda_ibr: dict[str, float]
    senda_sofr: dict[str, float]
    trm_inicio_real: float = 0.0     # NUEVO: nivel real de arranque del tramo (ancla USD)
```

b) `construir_sendas_tramo` fija `trm_inicio_real` = el inicio real del tramo. Para el tramo 0
   (`estado_previo is None`) usa la TRM t0 real inyectada; para i>0 usa `estado_previo.trm_cierre`.
   La senda interpolada (`senda_trm`) **no cambia** (sigue plana/avanzada como hoy):

```python
# scripts/proyeccion_tramos_multiactivo.py  construir_sendas_tramo  (~L720-741)
def construir_sendas_tramo(tramo, inputs_tramo, estado_previo=None,
                           trm_t0_real=None):            # NUEVO (tramo 0: TRM 597 real)
    ...
    if estado_previo is None:
        trm_inicio = trm_cierre                          # senda intermedia: SIN CAMBIOS
        trm_inicio_real = _coerce_float_finito(          # ancla real del tramo 0
            trm_t0_real, default=trm_cierre)
    else:
        trm_previa = getattr(estado_previo, "trm_cierre", estado_previo)
        trm_inicio = _coerce_float_finito(trm_previa, default=...)
        trm_inicio_real = trm_inicio                     # i>0: inicio real == cierre previo
    senda_trm = interpolar_trm_tramo(trm_inicio, trm_cierre, cortes)   # SIN CAMBIOS
    ...
    return SendasTramo(..., trm_inicio_real=trm_inicio_real)
```

   El orquestador pasa la TRM t0 real al construir las sendas:

```python
# app_dash._ejecutar_escenario_por_tramos  (~L7963)
sendas_tramo = construir_sendas_tramo(
    tramo, inputs_tramo_i, estado_previo, trm_t0_real=_trm_t0)   # NUEVO
```

c) `proyectar_bloque` (ambos motores) acepta un ancla explícito con default retrocompatible:

```python
# scripts/proyeccion_renta_variable.py / proyeccion_alternativos.py  (~L170 / ~L268)
def proyectar_bloque(portafolio, periodos, tasa_ea, ..., senda_trm=None,
                     trm_inicio_ancla=None):            # NUEVO (default: senda[primera_fecha])
    ...
    trm_t0 = None
    if senda_trm:
        if trm_inicio_ancla is not None and trm_inicio_ancla > 0:
            trm_t0 = float(trm_inicio_ancla)            # ancla REAL (fix)
        else:
            primera_fecha = periodos[0].strftime("%Y-%m-%d") if periodos else None
            trm_t0 = senda_trm.get(primera_fecha) if primera_fecha else None
            if trm_t0 is None:
                trm_t0 = list(senda_trm.values())[0] if senda_trm else None
    # los cortes mensuales siguen leyendo senda_trm[periodo]  → forma intacta
```

   Los adaptadores pasan el ancla real:

```python
# app_dash._correr_rv_tramo / _correr_alt_tramo  (al llamar al motor)
df_resultado = proyectar_renta_variable(
    ..., senda_trm=sendas_tramo.senda_trm,
    trm_inicio_ancla=sendas_tramo.trm_inicio_real,       # NUEVO
)
```

   (El parámetro `trm_inicio_ancla` se propaga desde `proyectar_renta_variable` /
   `proyectar_alternativos` hacia su `proyectar_bloque` interno; default `None` preserva a
   todos los demás llamadores — incluido punta a punta.)

#### 4. Tramo 0 en `construir_sendas_tramo`

Cubierto en 3b: la senda intermedia del tramo 0 se mantiene (sin cambios, para no romper
tests ni RF-indexado), pero `trm_inicio_real` del tramo 0 se ancla a la TRM t0 real del 597
(`_trm_t0`), de modo que derivados, efecto moneda y RV/Alt intl del tramo 0 usan 3144.14 en
vez de 3337.

### Fuente única de verdad (resumen)

`_trm_tramo_ini` (ya calculado en ~L7971-7975) es la ÚNICA fuente:
- RF: `proyectar_portafolio(trm=_trm_tramo_ini, ...)` — **sin cambios**.
- Derivados: `nivel_actual = _trm_tramo_ini`.
- Efecto moneda: `trm_inicio = _trm_tramo_ini`.
- RV/Alt intl: `proyectar_bloque.trm_t0 = sendas_tramo.trm_inicio_real` (==`_trm_tramo_ini`).

## Data-flow / Secuencia

```
_ejecutar_escenario_por_tramos(tramo i)
  │
  ├─ _trm_t0 = obtener_trm_para(_fecha_pip_str)            # 597, tramo 0 (3144.14)
  │
  ├─ _trm_tramo_ini  ← (i==0) _trm_t0
  │                     (i>0)  estado_previo.trm_cierre     # ENCADENAMIENTO
  │        │  (única fuente de verdad = trm_inicio_real)
  │        │
  │        ├──────────────► RF: proyectar_portafolio(trm=_trm_tramo_ini)   [SIN CAMBIOS]
  │        │
  │        ├──────────────► Derivados: nivel_actual = _trm_tramo_ini
  │        │                 (coincide con fecha_mercado=_fecha_pip_str → P5)
  │        │
  │        ├──────────────► Efecto moneda: trm_inicio = _trm_tramo_ini
  │        │                 (_reg["trm_inicio"] = _trm_tramo_ini)
  │        │
  │        └──────────────► construir_sendas_tramo(..., trm_t0_real=_trm_t0)
  │                            └─ SendasTramo.trm_inicio_real = _trm_tramo_ini
  │                               └─ RV/Alt: proyectar_bloque(trm_inicio_ancla=trm_inicio_real)
  │                                    (senda_trm intermedia: SIN CAMBIOS; último nodo==trm_cierre)
  │
  └─ estado_previo.trm_cierre = inputs_tramo_i.trm_cierre   # alimenta trm_inicio_real(i+1)
```

Encadenamiento garantizado: `trm_inicio_real(i) == estado_previo.trm_cierre == trm_cierre(i-1)`
para todo i>0, y `trm_inicio_real(0) == _trm_t0` del 597 (P4).

## Testing Strategy

### Validation Approach

Dos fases: primero producir contraejemplos que demuestran el bug sobre el código SIN
corregir (fase exploratoria), luego verificar que el fix corrige y preserva.

### Convención de ejecución (Windows / Google Drive)

El repo vive en una ruta de Google Drive con espacios
(`g:\Mi unidad\AXIS\AXIS`). Para evitar que pytest reescanee todo el Drive y por el
`.hypothesis` ya presente en `scripts/`, ejecutar SIEMPRE con ruta absoluta y aislamiento:

```
python -m pytest "g:\Mi unidad\AXIS\AXIS\tests\test_trm_inicial_tramo_consistente.py" ^
  --rootdir "g:\Mi unidad\AXIS\AXIS" ^
  -o addopts="" ^
  -p no:cacheprovider ^
  -q
```

- `--rootdir` fijo evita descubrimiento ascendente en el Drive.
- `-o addopts=""` ignora addopts heredados de un `pytest.ini`/`pyproject` global.
- `-p no:cacheprovider` evita escribir `.pytest_cache` en el Drive.
- En las pruebas Hypothesis usar `@settings(deadline=None)` (la latencia de E/S del Drive
  dispara falsos `DeadlineExceeded`).

### Exploratory Bug Condition Checking

**Goal**: surfacear contraejemplos que demuestran el bug ANTES del fix; confirmar o refutar
la causa raíz. Si se refuta, re-hipotetizar.

**Test Plan**: con un escenario por tramos mínimo (tramo 0 con TRM t0 real 3144.14 ≠
trm_cierre 3337), observar sobre el código SIN corregir que (a) el efecto moneda registra
`trm_inicio == senda[0]` (≈3337) y no 3144.14; (b) derivados usan `nivel_actual == senda[0]`;
(c) `proyectar_bloque.trm_t0 == senda[primera_fecha]`.

**Test Cases**:
1. **Efecto moneda tramo 0** — `trm_inicio` registrado == 3337 ≠ 3144.14 (falla en código sin
   fix).
2. **Derivados tramo 0** — `nivel_actual` == senda[0] ≠ 3144.14 (falla sin fix).
3. **Ancla USD RV/Alt tramo 0** — `proyectar_bloque.trm_t0` == senda[0] ≠ 3144.14 (falla sin
   fix).
4. **Encadenamiento (caso límite)** — tramo i>0: inicio observado por consumidores ≠
   `trm_cierre(i-1)` (puede fallar sin fix).

**Expected Counterexamples**: los consumidores leen `senda[0]` (3337) en vez de
`trm_inicio_real` (3144.14). Causa confirmada: anclaje en la senda en lugar de la fuente de
verdad.

### Fix Checking

**Goal**: para todo input donde se cumple la condición de bug, F' produce el comportamiento
esperado.

```
FOR ALL X WHERE isBugCondition(X) DO
  nivel_deriv   := nivel_actual_usado(F', X)
  nivel_efmon   := trm_inicio_efecto_moneda(F', X)
  nivel_rv_alt  := trm_t0_ancla_usd(F', X)
  esperado      := trm_inicio_real(X)
  ASSERT nivel_deriv  == esperado
  ASSERT nivel_efmon  == esperado
  ASSERT nivel_rv_alt == esperado
END FOR
```

### Preservation Checking

**Goal**: para todo input donde NO se cumple la condición de bug, F' == F.

```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT valoracion_RF(F, X)        == valoracion_RF(F', X)      # P3
  ASSERT last(senda_trm(F, X))      == last(senda_trm(F', X))    # P2 (== trm_cierre)
  ASSERT resultado_punta_a_punta(F) == resultado_punta_a_punta(F')  # P6
END FOR
```

**Testing Approach**: PBT (Hypothesis) para preservación — genera muchos tramos/escenarios y
cubre bordes que los tests unitarios no alcanzan, dando garantía fuerte de que lo no-buggy no
cambia.

**Test Plan**: observar el comportamiento de RF, del último nodo de la senda y del modo punta
a punta en el código SIN corregir y capturarlo en propiedades; luego verificar que persiste
tras el fix.

**Test Cases**:
1. **RF idéntica** — misma valoración de RF antes/después (los parámetros de
   `proyectar_portafolio` no cambian).
2. **Último nodo == trm_cierre** — `interpolar_trm_tramo(...).último == trm_cierre` para
   inicios/cierres/cortes aleatorios.
3. **Semántica de `interpolar_trm_tramo`** — el primer nodo sigue siendo
   `trm_inicio + (trm_cierre-trm_inicio)/n` (no se tocó la función).
4. **Punta a punta** — el resultado del modo no-por-tramos es idéntico.

### Unit Tests

- `nivel_actual` de derivados == `trm_inicio_real` (tramo 0 y tramo i>0).
- `trm_inicio` del efecto moneda y `_reg["trm_inicio"]` == `trm_inicio_real`.
- `SendasTramo.trm_inicio_real` del tramo 0 == TRM t0 real; de i>0 == cierre previo.
- `proyectar_bloque` con `trm_inicio_ancla` dado usa ese ancla; con `None` usa
  `senda[primera_fecha]` (retrocompatible).
- Caso borde: senda de un solo corte; moneda COP (efecto moneda 0).

### Property-Based Tests

- **P1**: para tramos aleatorios con `trm_inicio_real ≠ senda[0]`, los tres consumidores usan
  `trm_inicio_real`.
- **P2**: último nodo de la senda == `trm_cierre` para inicios/cierres/cortes aleatorios.
- **P4**: cadenas de N tramos aleatorias ⇒ `trm_inicio_real(i) == trm_cierre(i-1)`.
- **P5**: `nivel_actual` de derivados == `obtener_trm_para(fecha_mercado_curvas)` (mockeado).
- **P3/P6**: preservación de RF y de punta a punta (igualdad F vs F').

### Integration Tests

- Flujo por tramos completo (RF + RV + Alt + Derivados + efecto moneda) con un tramo 0 donde
  TRM t0 (3144.14) ≠ trm_cierre (3337): verificar que `informe_pyg_por_tramo.trm_inicio ==
  3144.14` y que los tres consumidores arrancan en 3144.14.
- Encadenamiento 2026→2027: el inicio del tramo 2027 observado por todos los consumidores ==
  cierre del 2026 (no 3349.25).
- Verificación de que RF del informe no cambia respecto al baseline.

### Tests existentes que DEBEN seguir en verde (regresión)

Ejecutar con la convención Windows/Drive de arriba:

- `tests/test_tramos_multiactivo_primitivas.py` — pin de la semántica de
  `interpolar_trm_tramo` (primer nodo avanzado un paso, último nodo == trm_cierre). La
  **opción híbrida NO toca** `interpolar_trm_tramo`, por lo que debe seguir verde.
- `tests/test_property_uvr_preservacion_por_tramos.py` — declara que
  `interpolar_trm_tramo`/`construir_sendas_tramo` "NO deben cambiar"; el único cambio en
  `construir_sendas_tramo` es un parámetro NUEVO con default y un campo NUEVO en
  `SendasTramo` (ambos retrocompatibles), por lo que las sendas producidas con la firma
  previa quedan idénticas.
- `tests/test_tramos_multiactivo_smoke.py` — contratos de firma de los motores
  (`proyectar_renta_variable`, `proyectar_alternativos`): los parámetros nuevos son
  opcionales con default y no eliminan ninguno existente.
- `tests/test_property_vm_cierre_usd_por_tramos.py`,
  `tests/test_integracion_vm_cierre_usd_multitramo.py` — VM USD por tramos y encadenamiento
  (deben permanecer; el VM de cierre no cambia).
- `tests/test_pyg_enero_integracion.py` — `_correr_rv_tramo` con `senda_trm` plana: el nuevo
  `trm_inicio_ancla` tiene default y no altera este caso.
- Suites de orquestación/integración por tramos:
  `tests/test_tramos_multiactivo_integracion*.py` y
  `tests/test_tramos_multiactivo_orquestador*.py` (verificar nombres exactos al implementar).

### Nota de cobertura

El **encadenamiento del spot inicial de derivados** (`nivel_actual` == `trm_cierre(i-1)` para
i>0, y == TRM t0 real para el tramo 0) **carece de cobertura** hoy. El fix debe añadir tests
de integración/propiedad específicos para esa cadena (P4 aplicado a derivados), ya que es
precisamente el punto donde el bug se manifestaba sin que ninguna prueba lo detectara.

### Garantías de seguridad y estilo (preservación no funcional)

- Sin TRM hardcodeada: el tramo 0 usa `obtener_trm_para(fecha_base)` (597); los defaults
  nuevos son `None`/`0.0` que caen al comportamiento previo, no a un número mágico.
- SQL de solo lectura y parametrizado: la lectura de `portafolio_clasificado` en
  `_correr_derivados_tramo` sigue siendo un `SELECT` sin concatenación de input.
- Logs genéricos: los mensajes de error mantienen el patrón actual (activo + fecha de corte,
  `type(e).__name__`), sin volcar datos sensibles ni fin de horizonte.
- Firmas retrocompatibles: todos los parámetros nuevos (`trm_inicio_real`, `trm_t0_real`,
  `trm_inicio_ancla`) y el campo `SendasTramo.trm_inicio_real` tienen default y no afectan a
  punta a punta ni a otros llamadores.
