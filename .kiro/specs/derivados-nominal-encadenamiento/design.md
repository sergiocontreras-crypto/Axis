# Derivados: nominal y encadenamiento por tramos — Bugfix Design

## Overview

En el modo "por_tramos" (multiactivo) el P&G de derivados sale mal a través de los tramos por **tres defectos** confirmados en el código, todos consecuencia de que el encadenamiento de derivados entre tramos arrastra el PLANO DE POSICIONES pero no el ESTADO necesario para que la valoración sea continua (como sí lo hace Renta Fija vía snapshot):

1. **Doble renovación del nominal.** `valorar_portafolio_derivados` ejecuta `_renovar_fwds`/`_renovar_opciones` de forma INCONDICIONAL sobre el plano base (`valoracion_derivados.py:1962-1991`). Cuando el plano inyectado (`posiciones_iniciales`) ya venía renovado del tramo anterior, se vuelve a renovar: se añaden más bloques con el mismo `nominal` del padre (`_renovar_fwds` ~L1192; `_renovar_opciones` ~L1301) y el motor suma `nominal` sobre todas las filas constituidas por corte (`valoracion_derivados.py:1996-2000`), inflando/componiendo el nominal vivo 2026->2027->2028.
2. **Reset/pérdida de flags de control de collares.** `_renovar_opciones` fuerza `base['_renovado']=False` sobre filas ya renovadas al re-ejecutarse (~L1286), y `_normalizar_plano_opts` (~L1697-1710) no restaura `_renovado`/`_call_resuelto` a booleano tras recargar de SQLite. Los forwards no tienen flag para distinguir original de renovado.
3. **La curva de valoración no acumula los shifts/deltas entre tramos.** El orquestador pasa `fecha_mercado=_fecha_pip_str` (fecha base global fija, `app_dash.py:8222`/`7846`) para TODOS los tramos y los deltas se arman frescos por tramo sin acumular (`app_dash.py:7573-7586`, `6054-6075`); dentro del motor el `progreso` se reinicia 0->1 por tramo (`valoracion_derivados.py:2114`). Resultado: la curva "se devuelve" al base al inicio de cada tramo, en vez de arrastrar el delta acumulado de los tramos previos (en punta a punta la curva del mes m es `base + deltas_totales x (m/N)`, senda continua).

El fix encadena el ESTADO que falta (plano con marca de original/renovado + delta de curva acumulado) de forma análoga a cómo RF arrastra `repo_cierre`/`inflacion_cierre` en `EstadoEncadenado`, **sin cambiar la lógica de valoración de un contrato individual** ni la carga cruda de curvas (`_cargar_curvas` sigue leyendo la base del PIP).

## Glossary

- **Bug_Condition (C)**: en una cadena de >=2 tramos, (a) el plano ya renovado del tramo i-1 se re-renueva en el tramo i (nominal vivo observado > conservado); y/o (b) los flags de control de collares difieren tras el round-trip o se resetean al re-renovar; y/o (c) la curva de valoración del tramo i arranca en la base fija sin el delta acumulado de 0..i-1.
- **Property (P)**: nominal vivo conservado entre tramos; flags de control preservados e inmutables; curva de valoración con delta acumulado continuo entre tramos (como punta a punta).
- **Preservation**: tramo 0 idéntico; inmutabilidad del collar renovado; opciones no renovadas nunca entran al solver; idempotencia de persistencia; RF/RV/Alt/punta a punta inalterados; spot del tramo 0 (spec B) sin cambios.
- **F / F'**: flujo de derivados por tramos actual (sin corregir) / corregido.
- **plano / plano_cierre**: dict `{fwds, opts, swaps}` de DataFrames de posiciones vivas; `plano_cierre` es el estado al `fecha_fin` del tramo que alimenta al siguiente.
- **`EstadoEncadenado`**: dataclass en `scripts/proyeccion_tramos_multiactivo.py:895` que ya arrastra `trm_cierre`/`repo_cierre`/`inflacion_cierre`/`uvr_cierre` entre tramos. Es el lugar natural para arrastrar también el **delta de curva acumulado** de derivados.
- **delta_acumulado de curva**: suma de los `delta_tes_cop`/`delta_tes_uvr`/`delta_fed_bps`/`delta_10t_bps` de los tramos 0..i-1, que debe aplicarse como nivel de ARRANQUE de la curva de valoración del tramo i (progreso=0), encima del cual el tramo i aplica su propio delta escalado por progreso.
- **marca original/renovado**: indicador por fila del plano (`_es_renovacion` para fwds; `_renovado`/`_collar_id`/`_call_resuelto` ya existentes para opts) que permite NO re-renovar lo ya renovado.

## Bug Details

### Bug Condition

```
FUNCTION isBugCondition(X)
  INPUT:  X = cadena de >=2 tramos "por_tramos" con derivados vivos; el plano de cierre
              del tramo i-1 (ya renovado) se inyecta como posiciones_iniciales del tramo i
  OUTPUT: boolean

  RETURN  (EXISTE i>0: nominal_vivo_observado(i) > nominal_vivo_conservado(i))          // D1
          OR (EXISTE opcion renovada p: flags_recargados(p) <> flags_persistidos(p))    // D2
          OR (EXISTE collar renovado p: tras tramo i, p._renovado == False)             // D2
          OR (EXISTE i>0: curva_valoracion_inicio(i) == curva_base_fija                 // D3
                          AND curva_valoracion_inicio(i) <> curva_base + deltas_acum(0..i-1))
END FUNCTION
```

### Examples

- **D1 nominal**: nominal vivo de derivados 2026 < 2027 < 2028 por re-renovación; "options > forwards" por nominal de opciones doble-contado.
- **D2 flags**: un collar renovado en 2026 llega a 2027 con `_renovado=False` tras re-renovar; forwards sin forma de distinguir original de renovado al recargar.
- **D3 curva**: la curva de valoración del tramo 2027 arranca en la curva base del PIP (fija) sin el shift/delta de 2026; 2028 sin el de 2026 ni 2027. En punta a punta la curva avanza continua.

## Expected Behavior

### Preservation Requirements

- **Tramo 0 idéntico**: la valoración de derivados del tramo 0 (sin plano inyectado, lee `hist_*`, renueva una vez) no cambia (Req 3.1).
- **Collar inmutable**: el Call renovado se resuelve una sola vez y su strike permanece inmutable (Req 3.2; `tests/test_inmutabilidad_collar_renovado.py`).
- **Opciones existentes**: nunca entran al solver; `_renovar_opciones` solo añade filas (Req 3.3; `tests/test_no_regresion_opciones_existentes.py`).
- **Idempotencia de persistencia**: `persistir_plano_derivados_proy` sigue borrando el corte antes de insertar; `limpiar_planos_derivados_proy` sigue vaciando las `_proy` sin tocar `hist_*` (Req 3.4).
- **RF/RV/Alt/punta a punta**: inalterados (Req 3.5).
- **Spot del tramo 0**: `nivel_actual = trm_inicio_real` (spec B) sin cambios (Req 3.6).

**Alcance:** el fix solo toca la ruta de derivados por tramos (renovación condicionada, round-trip de flags, acumulación del delta de curva). No cambia la valoración de un contrato individual, el solver del collar, ni `_cargar_curvas`.

## Hypothesized Root Cause

1. **Renovación incondicional** (`valoracion_derivados.py:1962-1991`): no distingue plano fresco (tramo 0, lee `hist_*`) de plano ya renovado inyectado (tramo i>0), por lo que re-renueva.
2. **Flags no retipados / resobrescritos** (`valoracion_derivados.py:1286`, `1697-1710`): el re-renovado pisa `_renovado=False`; el reload no restaura booleanos; forwards sin flag.
3. **Curva no acumulada** (`app_dash.py:7846`, `8222`, `7573-7586`, `6054-6075`; `valoracion_derivados.py:2114`): base de curva fija por tramo + deltas por-tramo no acumulados + `progreso` reiniciado por tramo. El estado de curva no se encadena (a diferencia de RF, que encadena `repo_cierre`/`inflacion_cierre` en `EstadoEncadenado`).

## Decisión de diseño

Para los tres defectos se elige el enfoque de **MÍNIMA superficie y máxima analogía con RF**: encadenar en la capa de aplicación el estado que falta y condicionar la renovación con una marca por fila, sin tocar la valoración de contratos.

### Defecto 1 — No re-renovar un plano ya renovado

**Opción elegida (A): marcar las filas de renovación y renovar solo lo necesario.**
- `_renovar_fwds` y `_renovar_opciones` marcan cada fila que generan con un indicador de renovación (`_es_renovacion=True` para fwds; opts ya tienen `_renovado`). Las filas base originales quedan `_es_renovacion=False`.
- La renovación se condiciona: al recibir un plano inyectado, NO se re-siembran bloques sobre filas que ya son renovaciones; solo se renueva para cubrir el horizonte del tramo a partir de las posiciones VIVAS que vencen dentro del tramo y que aún no tienen su renovación constituida.
- Alternativa descartada (B): recomputar todo desde `hist_*` en cada tramo con el horizonte acumulado — rompería el encadenamiento Opción B (plano vivo real con sus renovaciones) y recalcularía el solver de collares (viola inmutabilidad). Más superficie y riesgo.

### Defecto 2 — Preservar flags en el round-trip y no resetearlos

**Opción elegida: normalizar tipos al recargar + no pisar flags de filas inyectadas + añadir marca a forwards.**
- `cargar_plano_derivados_proy` / `_normalizar_plano_opts`: restaurar `_renovado`/`_call_resuelto` a `bool` y `_collar_id` a str/None tras leer de SQLite (hoy quedan int 0/1).
- `_renovar_opciones`: NO forzar `_renovado=False` sobre filas que llegan ya marcadas como renovadas desde el plano inyectado; solo inicializar el flag en las filas base NUEVAS que no lo traen.
- `_renovar_fwds`: emitir `_es_renovacion` (bool) por fila; persistir y recargar esa columna, de modo que un forward original se distinga de uno renovado tras el round-trip.
- Todo con defaults retrocompatibles: si la columna no existe (planos previos al fix), se asume `False`/original.

### Defecto 3 — Acumular el delta de curva entre tramos

**Opción elegida (análoga a RF): arrastrar el delta de curva ACUMULADO en `EstadoEncadenado` y aplicarlo como nivel de arranque de la curva del tramo.**
- Añadir a `EstadoEncadenado` un campo `delta_curva_acumulado` (dict por tipo de curva: `delta_tes_cop`, `delta_tes_uvr`, `delta_fed_bps`, `delta_10t_bps`), inicialmente vacío/0.
- Al cerrar el tramo i, acumular los deltas del tramo i sobre el acumulado previo: `delta_acum(i) = delta_acum(i-1) + delta_tramo(i)`.
- El adaptador `_correr_derivados_tramo` recibe `delta_curva_inicial` (= `estado_previo.delta_curva_acumulado`) y lo pasa al motor como un desplazamiento BASE de la curva (progreso=0 del tramo i parte de `base + delta_acum(0..i-1)`), encima del cual el delta del tramo i se aplica con el `progreso` 0->1 habitual.
- Mecanismo dentro del motor (mínimo): `valorar_portafolio_derivados` acepta un `delta_curva_base` (dict, default `None`=comportamiento actual) que se SUMA a las curvas EA cargadas ANTES de aplicar el `progreso` del tramo. NO se cambia `_cargar_curvas` (sigue leyendo la base cruda); el desplazamiento base se inyecta como parámetro.
- Alternativa descartada: avanzar `fecha_mercado` por tramo a fechas futuras — IMPOSIBLE, las curvas de mercado no existen para fechas futuras (son proyectadas vía deltas). Por eso se acumula el delta, no la fecha.

**Justificación global:** las tres opciones replican el patrón ya probado de RF (encadenar estado en `EstadoEncadenado`, aplicar shifts sin recomputar desde cero), preservan los contratos de collar y la valoración por contrato, y son retrocompatibles (parámetros/columnas nuevas con default).

## Correctness Properties

Property 1: Bug Condition / Fix Checking — Conservación de nominal entre tramos

_For any_ cadena de tramos donde `isBugCondition` es true por nominal, F' SHALL producir un nominal vivo del tramo i igual al conservado (sin re-renovar las filas ya renovadas del plano inyectado), de modo que el nominal vivo NO sea monotónicamente creciente por el solo hecho de encadenar tramos.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Bug Condition / Fix Checking — Preservación de flags de control

_For any_ plano de opciones persistido y recargado, F' SHALL preservar `_renovado`/`_collar_id`/`_call_resuelto` con su tipo correcto, y ningún collar ya renovado SHALL re-marcarse `_renovado=False` ni re-resolver su strike; los forwards SHALL distinguir original de renovado vía `_es_renovacion`.

**Validates: Requirements 2.5, 2.6, 2.7**

Property 3: Bug Condition / Fix Checking — Acumulación continua de la curva

_For any_ cadena de tramos con deltas de curva, F' SHALL hacer que la curva de valoración al inicio del tramo i refleje el delta acumulado de 0..i-1, de modo que la trayectoria sea consistente con la senda continua de punta a punta (misma propagación).

**Validates: Requirements 2.9, 2.10, 2.11**

Property 4: Preservation — Tramo 0 y contratos de collar inalterados

_For any_ entrada donde NO se cumple `isBugCondition` (tramo 0, o sin plano inyectado), F'(X) == F(X): valoración del tramo 0 idéntica, collar inmutable, opciones no renovadas fuera del solver, punta a punta inalterado.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

**File: `valoracion_derivados.py`**
1. `_renovar_fwds` (~L1164-1211): emitir columna `_es_renovacion` (`False` en las filas base, `True` en los bloques generados). Al recibir fwds que ya traen `_es_renovacion=True`, NO re-sembrar bloques sobre esas filas (renovar solo desde las vivas originales según el horizonte).
2. `_renovar_opciones` (~L1258-1322): no pisar `_renovado=False` sobre filas que llegan con `_renovado=True`; inicializar el flag solo en filas base sin él. Conservar `_collar_id`/`_call_resuelto`.
3. Bloque de renovación (~L1962-1991): condicionar la renovación según si el plano es inyectado (ya renovado) o fresco (tramo 0). Guard: si `posiciones_iniciales` trae filas marcadas como renovación, no re-renovarlas.
4. `_normalizar_plano_opts` (~L1697-1710): restaurar tipos de `_renovado`/`_call_resuelto` a `bool` y `_collar_id` a str/None tras leer de SQLite.
5. `valorar_portafolio_derivados` (firma ~L1729-1749): añadir `delta_curva_base: dict | None = None` (default = comportamiento actual). Si se provee, sumarlo a las curvas EA cargadas ANTES del escalado por `progreso` del tramo (desplazamiento base), sin tocar `_cargar_curvas`.

**File: `scripts/proyeccion_tramos_multiactivo.py`**
6. `EstadoEncadenado` (~L895): añadir campo `delta_curva_acumulado: dict | None = None` (default None, retrocompatible). Helper puro para acumular: `acumular_delta_curva(prev, delta_tramo) -> dict`.

**File: `scripts/proyeccion_tramos.py`**
7. `persistir_plano_derivados_proy`/`cargar_plano_derivados_proy` (~L1088-1174): asegurar que la columna `_es_renovacion` (fwds) y los flags de opts se escriban/lean y se retipen; mantener SQL parametrizado e idempotencia.

**File: `app_dash.py`**
8. `_correr_derivados_tramo` (~L7520-7651): aceptar `delta_curva_base=None` y pasarlo a `valorar_portafolio_derivados`.
9. `_ejecutar_escenario_por_tramos`:
   - Construir `estado_previo.delta_curva_acumulado` al cerrar cada tramo (~L8191-8205), sumando el delta del tramo al acumulado previo.
   - En la llamada a `_correr_derivados_tramo` (~L8218-8223): pasar `delta_curva_base = estado_previo.delta_curva_acumulado` (None en el tramo 0 -> comportamiento actual).

### Fuente única de verdad (resumen)

`EstadoEncadenado` es la ÚNICA fuente del estado encadenado de derivados entre tramos:
- Plano de posiciones (ya existente, vía `_proy`) + marca original/renovado.
- `delta_curva_acumulado`: nivel base de curva del tramo i = `curva_base_PIP + delta_curva_acumulado(0..i-1)`.

## Data-flow / Secuencia

```
_ejecutar_escenario_por_tramos (tramo i)
  |
  |- _plano_ini = cargar_plano_derivados_proy(corte_prev)   # i>0; trae marcas _es_renovacion/_renovado
  |- delta_base = estado_previo.delta_curva_acumulado        # i>0; None en tramo 0
  |
  |- _correr_derivados_tramo(..., plano_inicial=_plano_ini, delta_curva_base=delta_base,
  |                          fecha_mercado=_fecha_pip_str, trm_inicio_real=_trm_tramo_ini)
  |        |
  |        \- valorar_portafolio_derivados(..., posiciones_iniciales, delta_curva_base)
  |             |- _cargar_curvas(fecha base PIP)            # SIN CAMBIOS (curva cruda)
  |             |- curva_arranque = curva_base + delta_curva_base    # D3: parte del acumulado
  |             |- renovacion CONDICIONADA (no re-renovar lo ya renovado)   # D1
  |             \- progreso 0->1 del tramo aplica delta_tramo(i) sobre curva_arranque
  |
  |- persistir_plano_derivados_proy(plano_cierre)            # conserva marcas/flags
  \- estado_previo.delta_curva_acumulado = delta_base + delta_tramo(i)   # D3: acumula
```

## Testing Strategy

### Validation Approach

Dos fases: (1) exploración que FALLA en el código sin corregir (demuestra los 3 defectos); (2) fix checking + preservación. Escopar a seams PUROS para no requerir las curvas de mercado completas (ausentes en el DB de prueba): renovación, round-trip del plano, acumulación del delta.

### Exploratory Bug Condition Checking

- **D1**: construir un plano ya renovado (marcado) e inyectarlo; verificar que el nominal vivo NO crece por re-renovación (hoy FALLA: crece).
- **D2**: persistir/recargar un plano de opciones con `_renovado=True`; verificar tipo booleano y que un collar renovado no se re-marca `False` (hoy FALLA). Forwards distinguen original/renovado (hoy no existe la marca).
- **D3**: cadena de 2 tramos con deltas; verificar que la curva de arranque del tramo 1 = base + delta_acum(tramo 0) (hoy FALLA: arranca en base).

### Fix Checking

```
FOR ALL X WHERE isBugCondition(X) DO
  ASSERT nominal_vivo(F', i) == nominal_vivo_conservado(i)            # P1
  ASSERT flags_recargados(p) == flags_persistidos(p) (tipo bool)      # P2
  ASSERT collar renovado permanece _renovado=True, strike inmutable   # P2
  ASSERT curva_arranque(F', i) == curva_base + delta_acum(0..i-1)     # P3
END FOR
```

### Preservation Checking

```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT valoracion_derivados_tramo0(F, X) == valoracion_derivados_tramo0(F', X)   # P4
  ASSERT resultado_punta_a_punta(F) == resultado_punta_a_punta(F')                 # P4
END FOR
```

### Unit Tests

- `_renovar_fwds`/`_renovar_opciones` marcan `_es_renovacion`/`_renovado` y NO re-renuevan filas ya marcadas.
- `_normalizar_plano_opts` restaura tipos booleanos tras recargar.
- `acumular_delta_curva(prev, delta)` suma correctamente por tipo de curva.
- `valorar_portafolio_derivados` con `delta_curva_base` dado desplaza la curva de arranque; con `None` reproduce el comportamiento actual (retrocompatible).

### Property-Based Tests

- **P1**: cadenas de N tramos con planos renovados aleatorios => nominal vivo conservado (no creciente por encadenar).
- **P2**: round-trip de planos de opciones aleatorios => flags preservados; collares renovados inmutables.
- **P3**: cadenas de N tramos con deltas aleatorios => curva de arranque del tramo i == base + suma de deltas 0..i-1.
- **P4**: tramo 0 y punta a punta => F == F'.

### Integration Tests

- Flujo por tramos (2026/2027/2028) con derivados vivos y renovaciones: nominal vivo conservado; "options > forwards" corregido; curva de valoración con delta acumulado continuo.
- Comparación tramos vs. punta a punta con los mismos inputs de shift/delta: trayectoria de curva consistente.

### Tests existentes que DEBEN seguir en verde (regresión)

- `tests/test_inmutabilidad_collar_renovado.py` (inmutabilidad del collar renovado).
- `tests/test_no_regresion_opciones_existentes.py` (opciones existentes fuera del solver; `_renovar_opciones` solo añade).

### Convención de ejecución (Windows / Google Drive) — OBLIGATORIA

```
python -m pytest "g:\Mi unidad\AXIS\AXIS\tests\<archivo>.py" ^
  --rootdir "g:\Mi unidad\AXIS\AXIS" -o addopts="" -p no:cacheprovider -q
```

Hypothesis `@settings(deadline=None, max_examples=100)` (y `suppress_health_check=[HealthCheck.too_slow]` si toca BD). El terminal suele devolver exit code -1 aun en éxito — fiarse de la línea de resumen de pytest. Los tests de condición de bug se escopan a seams puros (renovación, round-trip, acumulación) sin requerir `hist_pip`/`hist_curvas_derivados` (ausentes en el DB de prueba, falla pre-existente documentada en `docs/deuda-tecnica.md`).
