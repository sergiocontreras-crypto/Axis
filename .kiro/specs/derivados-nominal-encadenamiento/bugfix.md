# Documento de Requisitos del Bugfix — Derivados: nominal y encadenamiento por tramos

## Introduction

En el modo de proyección **"por_tramos"** (multiactivo), el P&G de derivados sale mal a través de los tramos: **"las opciones dominan a los forwards"** y el **nominal de derivados se infla y se compone** 2026→2027→2028, con **flags de control de collares que se pierden o se resetean** al persistir y recargar el plano entre tramos.

El spec previo `trm-inicial-tramo-consistente` (B) ya ancló el spot del tramo 0 (`nivel_actual = trm_inicio_real`). Este bugfix es sobre un defecto DISTINTO y posterior: la **renovación/encadenamiento del nominal** y la **persistencia de los flags de control**.

**Principio rector (requisito del usuario):** el modo "por_tramos" debe usar los MISMOS motores de valoración que el modo "punta a punta", con la MISMA propagación de shifts (TES COP/UVR) y deltas (FED, Tesoros 10Y) a la curva de valoración período a período. Nunca se pidió un comportamiento distinto en tramos. Las desviaciones de este principio son defectos.

Este bugfix corrige **tres defectos** del flujo de derivados por tramos:

1. **Doble renovación → inflación/composición del nominal.** Un plano de derivados que **ya fue renovado** en el tramo i-1 (y persistido como estado de cierre) se **vuelve a renovar** al inyectarse como `posiciones_iniciales` del tramo i, porque `valorar_portafolio_derivados` ejecuta la renovación de forma incondicional, sin distinguir un plano ya renovado de un plano fresco. Cada tramo añade una nueva tanda de bloques de renovación sobre filas que ya eran renovaciones, cargando el mismo `nominal` del padre; el motor agrega `nominal` sobre todas las filas constituidas por corte, de modo que el nominal vivo crece y se compone por tramo.

2. **Reset/pérdida de flags de control de collares.** Los flags `_renovado`, `_collar_id`, `_call_resuelto` (solo presentes en opciones) se re-escriben al re-renovar (un collar ya renovado vuelve a marcarse como "existente") y no se restauran a su tipo booleano al recargar el plano desde SQLite. Los forwards no tienen ningún flag que permita distinguir un contrato original de uno renovado.

3. **La curva de valoración NO acumula los shifts/deltas entre tramos.** Los deltas del tramo (TES COP/UVR, FED, Tesoros 10Y) SÍ se pasan y SÍ se aplican dentro de cada tramo (escalados por `progreso` mes a mes), igual que punta a punta. PERO cada tramo re-ancla a la MISMA curva base fija (`fecha_mercado = _fecha_pip_str`, calculada una sola vez) y el `progreso` se reinicia 0→1 dentro de cada tramo. Resultado: la curva de valoración de derivados "se devuelve" al nivel base al inicio de cada tramo, en vez de arrastrar el shift/delta acumulado de los tramos previos. En punta a punta la curva del mes m es `curva_base + deltas_totales × (m/N)` (senda continua); en tramos, el tramo 2027 arranca en la curva base SIN el shift de 2026, y 2028 sin el de 2026 ni 2027. Esto contradice el principio rector (misma propagación continua que punta a punta). Contraste: RF sí arrastra su efecto entre tramos porque encadena el estado vía snapshot (spreads/niveles re-valuados y `estado_previo.repo_cierre`/`inflacion_cierre`); derivados encadena solo el PLANO DE POSICIONES, no el ESTADO DE LA CURVA.

El síntoma observable "options > forwards" es consecuencia principal de (1): el payoff de las opciones (collares put/call) es convexo/asimétrico frente al forward casi lineal, de modo que un nominal de opciones inflado/doble-contado sobrestima el P&G de opciones frente al de forwards, y se agrava en escenarios de TRM creciente. El defecto (3) agrava adicionalmente la incoherencia del P&G de derivados en los tramos i>0.

**Aclaración de alcance:** este bugfix NO cambia la metodología de valoración de un contrato individual, ni la lógica del solver del collar, ni los literales/tablas de persistencia. Corrige: (a) que el nominal vivo se **conserve** entre tramos (no se renueve dos veces); (b) que los flags de control **sobrevivan** el round-trip y NO se reseteen; y (c) que el shift/delta de curva se **acumule** entre tramos, de modo que la curva de valoración de derivados avance de forma continua como en punta a punta.

## Bug Analysis

### Bug Condition (Condición del Bug)

```pascal
FUNCTION isBugCondition(X)
  INPUT:  X = una cadena de >= 2 tramos del modo "por_tramos" con derivados vivos,
              donde el plano de cierre del tramo i-1 (ya renovado) se inyecta como
              posiciones_iniciales del tramo i
  OUTPUT: boolean

  // nominal_vivo_conservado(i): nominal vivo esperado del tramo i si el plano inyectado
  //   NO se vuelve a renovar (solo se renueva lo necesario para cubrir el horizonte del
  //   tramo, sin re-sembrar las renovaciones ya existentes).
  // nominal_vivo_observado(i): nominal vivo que hoy produce el motor para el tramo i.
  // flags_persistidos / flags_recargados: {_renovado,_collar_id,_call_resuelto} del plano
  //   de opciones tal como se persistieron vs. tal como quedan tras recargar/re-renovar.

  RETURN  (EXISTE tramo i>0 TAL QUE
             nominal_vivo_observado(i) > nominal_vivo_conservado(i))      // inflacion/composicion
          OR
          (EXISTE una opcion renovada p TAL QUE
             flags_recargados(p) <> flags_persistidos(p))                 // reset/perdida de flags
          OR
          (EXISTE un collar ya renovado p TAL QUE
             tras el tramo i se re-marca p._renovado == False)            // re-renovacion indebida
          OR
          (EXISTE tramo i>0 TAL QUE
             curva_valoracion_inicio(i) == curva_base_fija                // la curva NO acumula
             AND curva_valoracion_inicio(i) <> curva_base + deltas_acum(0..i-1))  // shift/delta previos
END FUNCTION
```

```pascal
// Property: Fix Checking — conservacion de nominal y supervivencia de flags
FOR ALL X WHERE isBugCondition(X) DO
  resultado <- F'(X)   // flujo de derivados por tramos corregido
  ASSERT PARA TODO i>0: nominal_vivo(resultado, i) == nominal_vivo_conservado(i)
  ASSERT el nominal vivo NO es monotonicamente creciente por el solo hecho de encadenar tramos
  ASSERT flags_recargados(p) == flags_persistidos(p) (tipo booleano preservado)
  ASSERT ningun collar ya renovado se re-marca _renovado=False ni se re-resuelve su strike
END FOR
```

- **F**: flujo de derivados por tramos actual (sin corregir), que re-renueva el plano inyectado.
- **F'**: flujo corregido, que conserva el nominal vivo entre tramos y preserva los flags.

Contraejemplos (confirmados por lectura de código):
- Nominal vivo de derivados creciente 2026→2027→2028 por doble renovación del plano inyectado.
- "options > forwards": P&G de opciones sobrestimado frente a forwards por nominal de opciones doble-contado.
- Un collar ya renovado recargado del tramo anterior queda re-marcado `_renovado == False` tras re-renovar en el tramo siguiente.
- Forwards sin ningún flag para distinguir original de renovado al recargar.
- La curva de valoración del tramo 2027 arranca en la curva base del PIP (fecha fija) SIN el shift/delta acumulado de 2026; la de 2028 sin el de 2026 ni 2027 (el `progreso` se reinicia 0→1 por tramo sobre una base fija), a diferencia de punta a punta donde la curva del mes m lleva el delta acumulado `base + deltas_totales × (m/N)`.

### Current Behavior (Defect)

1.1 WHEN se ejecuta una cadena de tramos y el tramo i (i>0) recibe como `posiciones_iniciales` el plano de cierre ya renovado del tramo i-1 THEN el sistema ejecuta `_renovar_fwds`/`_renovar_opciones` de forma INCONDICIONAL sobre ese plano (`valoracion_derivados.py:1962-1991`), sin distinguir un plano ya renovado de un plano fresco.

1.2 WHEN el tramo 0 renueva el plano THEN el `plano_cierre` devuelto YA contiene los bloques de renovacion filtrados a `vencimiento > fecha_fin` (`valoracion_derivados.py:2362-2374`), y ese plano se persiste en `hist_forwards_proy`/`hist_opciones_proy`/`hist_swaps_proy` (`scripts/proyeccion_tramos.py` `persistir_plano_derivados_proy` ~L1088-1130) y se recarga para el tramo siguiente (`cargar_plano_derivados_proy` ~L1134-1174; inyeccion en `app_dash.py` ~L8215-8225).

1.3 WHEN `_renovar_fwds` genera los bloques de renovacion THEN cada bloque nuevo carga el MISMO `nominal` de su fila padre (`valoracion_derivados.py:~L1192`, `'nominal': src['nominal']`), encadenandose por `vencimiento` sin reemplazar la fila original.

1.4 WHEN `_renovar_opciones` genera el collar de renovacion THEN cada fila nueva carga el MISMO `nominal` de la opcion padre (`valoracion_derivados.py:~L1301`, `'nominal': opts['nominal']`).

1.5 WHEN el motor agrega el nominal vivo por corte THEN suma `nominal` sobre TODAS las filas constituidas (`valoracion_derivados.py:1996-2000`, `opts_plan.loc[opts_plan['constitucion'] <= fecha_cal]`), de modo que al re-renovar en cada tramo el nominal vivo CRECE y se COMPONE 2026→2027→2028.

1.6 WHEN se calcula el P&G de derivados THEN `calcular_pyg` usa `p_y_g = valoracion - valor_base` con `valor_base` del primer corte m=0 por tipo (`valoracion_derivados.py:2470-2473`); como el plano inyectado se re-renueva, tanto la valoracion como el baseline m=0 se desplazan por tramo, y las opciones (payoff convexo) quedan sobrestimadas frente a los forwards ("options > forwards"), agravado con TRM creciente.

1.7 WHEN `_renovar_opciones` se re-ejecuta sobre el plano inyectado del tramo i THEN fuerza `base['_renovado'] = False` (`valoracion_derivados.py:~L1286`) sobre filas que YA eran renovaciones del tramo anterior, re-marcando collares ya renovados como "existentes" y violando el contrato de inmutabilidad del collar renovado.

1.8 WHEN el plano de opciones se recarga desde SQLite THEN `_normalizar_plano_opts` (`valoracion_derivados.py:1697-1710`) coacciona `constitucion/vencimiento/strike/nominal` pero NO restaura `_renovado`/`_call_resuelto` a tipo booleano (quedan como int 0/1), de modo que los flags recargados difieren en tipo de los persistidos.

1.9 WHEN se persiste/recarga el plano de FORWARDS THEN este no contiene NINGUN flag de control (`_renovar_fwds` produce solo `constitucion/vencimiento/nominal/sentido/portafolio/tasa_negociada`), por lo que es imposible distinguir un forward original de uno renovado al recargarlo.

1.10 WHEN el orquestador invoca `_correr_derivados_tramo` para CADA tramo THEN pasa `fecha_mercado = _fecha_pip_str` (`app_dash.py:8222`), un valor GLOBAL fijo calculado una sola vez (`_fecha_pip_str = fecha_ini.strftime('%Y-%m-%d')`, `app_dash.py:7846`), de modo que `_cargar_curvas(_fecha_mkt, ...)` (`valoracion_derivados.py:1849,1853`) recarga la MISMA curva base para todos los tramos y la base NUNCA avanza.

1.11 WHEN `_correr_derivados_tramo` construye `metodo_params` THEN lo arma FRESCO por tramo solo con los deltas de ESE tramo (`app_dash.py:7573-7586`: `mov_tes_cop_bps`->`delta_tes_cop`, `mov_tes_uvr_bps`->`delta_tes_uvr`, `delta_fed_bps`, `delta_tesoros10y_bps`->`delta_10t_bps`), y esos valores son celdas crudas del grid por tramo sin acumulación (`app_dash.py:6054-6075`), de modo que los deltas NO se acumulan entre tramos.

1.12 WHEN el motor aplica los deltas dentro de un tramo THEN usa `progreso = idx_corte / max(n_cortes-1, 1)` (`valoracion_derivados.py:2114`) con horizonte = el span del tramo (`fecha_corte`->`fecha_fin`), aplicando `base_EA + delta * progreso` (`valoracion_derivados.py:2137,2163`); como `progreso` se REINICIA 0->1 en cada tramo sobre la curva base fija, al inicio de cada tramo (progreso=0) la curva de valoración es la base SIN shift, y solo sube por el delta de ESE tramo, perdiendo el shift/delta acumulado de los tramos previos (a diferencia de la senda continua de punta a punta, `app_dash.py:6768-6773`, donde `fecha_mercado` no se pasa y el `progreso` recorre todo el horizonte).

### Expected Behavior (Correct)

2.1 WHEN el tramo i (i>0) recibe un plano ya renovado como `posiciones_iniciales` THEN el sistema SHALL NOT re-renovar las filas de renovacion ya existentes; SHALL renovar unicamente lo necesario para cubrir el horizonte del tramo i a partir de las posiciones vivas, sin re-sembrar renovaciones ya constituidas.

2.2 WHEN se encadena una cadena de tramos con posiciones de derivados vivas THEN el nominal vivo del tramo i SHALL conservarse respecto del encadenamiento esperado (igual al que resultaria sin la doble renovacion) y NO SHALL inflarse ni componerse por el solo hecho de encadenar tramos.

2.3 WHEN `_renovar_fwds`/`_renovar_opciones` operan sobre un plano inyectado THEN el `nominal` de las posiciones vivas SHALL reflejar el notional real conservado (sin acumulacion espuria por bloques de renovacion duplicados).

2.4 WHEN se calcula el P&G de derivados por tramo THEN la relacion forwards vs. opciones SHALL reflejar los nominales conservados (sin la sobrestimacion de opciones causada por el nominal doble-contado).

2.5 WHEN el plano de opciones se persiste y se recarga THEN los flags de control `_renovado`, `_collar_id` y `_call_resuelto` SHALL sobrevivir el round-trip con su tipo correcto (booleano para los flags logicos), de modo que `flags_recargados == flags_persistidos`.

2.6 WHEN un collar ya renovado atraviesa un tramo posterior THEN SHALL permanecer marcado como renovado (`_renovado == True`) y su `strike` SHALL permanecer inmutable (resuelto una sola vez), sin re-marcarse como existente ni re-entrar al solver.

2.7 WHEN se persiste/recarga el plano de FORWARDS THEN el sistema SHALL permitir distinguir un forward original de uno renovado (mediante un indicador de control preservado en el round-trip), de forma retrocompatible (parametro/columna nueva con default que no altere a los llamadores actuales).

2.8 WHEN se implementa el fix THEN los parametros/indicadores nuevos SHALL anadirse con valor por defecto retrocompatible (sin defaults de negocio hardcodeados), el SQL SHALL permanecer de solo lectura/parametrizado (sin concatenacion de input) y los logs SHALL ser genericos sin datos sensibles.

2.9 WHEN se valora un tramo i>0 THEN la curva de valoración de derivados al INICIO del tramo i SHALL reflejar el shift/delta ACUMULADO de los tramos 0..i-1 (no la curva base sin shift), de modo que la curva avance de forma continua a través de los tramos, igual que la senda continua del modo punta a punta.

2.10 WHEN se encadena una cadena de tramos THEN el efecto de los deltas (TES COP/UVR, FED, Tesoros 10Y) SHALL acumularse entre tramos (el estado de curva del cierre del tramo i-1 SHALL alimentar el inicio del tramo i), de forma análoga a cómo RF arrastra su estado vía snapshot (`estado_previo.repo_cierre`/`inflacion_cierre`), sin cambiar los motores de valoración.

2.11 WHEN se compara el P&G de derivados por tramos contra el equivalente punta a punta con los MISMOS inputs de shift/delta THEN la trayectoria de la curva de valoración SHALL ser consistente entre ambos modos (misma propagación continua), eliminando el "reinicio" de la curva al base en cada tramo.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN se valora el tramo 0 (sin plano inyectado, leyendo `hist_*` reales y renovando una sola vez) THEN el sistema SHALL CONTINUE TO producir una valoracion de derivados identica a la actual.

3.2 WHEN un Call renovado se resuelve THEN SHALL CONTINUE TO resolverse una sola vez y su `strike` SHALL permanecer inmutable (contrato protegido por `tests/test_inmutabilidad_collar_renovado.py`).

3.3 WHEN existen opciones NO renovadas THEN SHALL CONTINUE TO no entrar nunca al solver, y `_renovar_opciones` SHALL CONTINUE TO solo ANADIR filas (contrato protegido por `tests/test_no_regresion_opciones_existentes.py`).

3.4 WHEN se persiste el plano por corte THEN `persistir_plano_derivados_proy` SHALL CONTINUE TO ser idempotente (DELETE del corte antes de insertar, parametrizado) y `limpiar_planos_derivados_proy` SHALL CONTINUE TO vaciar las tablas `_proy` sin tocar las `hist_*` reales.

3.5 WHEN se proyectan RF, RV, Alternativos o el modo punta a punta THEN SHALL CONTINUE TO comportarse exactamente igual que antes del fix (el fix solo toca la ruta de derivados por tramos).

3.6 WHEN se consume el spot del tramo 0 THEN SHALL CONTINUE TO usar `nivel_actual = trm_inicio_real` (fix del spec B, `app_dash.py` ~L7586-7589) sin cambios.

### Nota de alcance (defecto 3) y mecanismo a decidir en diseno

El defecto 3 (curva de valoración que no acumula shifts/deltas entre tramos) ESTÁ EN ALCANCE de este bugfix (requisitos 2.9-2.11), por ser una desviación directa del principio rector "mismos motores, misma propagación continua que punta a punta". El MECANISMO exacto para acumular el estado de curva entre tramos (p. ej. encadenar el delta acumulado en `EstadoEncadenado` análogo a `repo_cierre`/`inflacion_cierre` de RF, o avanzar la base equivalente por tramo) se decide en el diseño, SIN cambiar la lógica de valoración de un contrato individual ni la carga cruda de curvas (`_cargar_curvas` sigue leyendo la base del PIP; lo que cambia es el delta acumulado que se le aplica al inicio de cada tramo).

Precisión importante: `nivel_actual` (spot) YA avanza por tramo correctamente (fix del spec B). El defecto 3 es exclusivamente sobre el DELTA/SHIFT de curva que no se acumula; no se debe alterar el anclaje del spot.

## Convencion de ejecucion de pruebas (Windows / Google Drive) — OBLIGATORIA

El repo vive en una ruta de Google Drive con espacios (`g:\Mi unidad\AXIS\AXIS`). Ejecutar SIEMPRE con ruta absoluta y aislamiento:

```
python -m pytest "g:\Mi unidad\AXIS\AXIS\tests\<archivo>.py" ^
  --rootdir "g:\Mi unidad\AXIS\AXIS" ^
  -o addopts="" ^
  -p no:cacheprovider ^
  -q
```

- Hypothesis: `@settings(deadline=None, max_examples=100)` (>=100 ejemplos); si se toca el fixture de BD, anadir `suppress_health_check=[HealthCheck.too_slow]`.
- El terminal suele devolver exit code -1 aun en exito — fiarse de la linea de resumen de pytest.
- Los tests que ejercitan el motor completo requieren fixtures de mercado `hist_pip` / `hist_curvas_derivados` AUSENTES en el DB de prueba (falla pre-existente documentada en `docs/deuda-tecnica.md`); por eso los tests de condicion de bug deben escoparse a los seams PUROS (renovacion, round-trip del plano, agregacion de nominal) sin exigir las curvas completas.

## Superficie de regresion

Tests existentes que DEBEN seguir en verde: `tests/test_inmutabilidad_collar_renovado.py`, `tests/test_no_regresion_opciones_existentes.py` (ambos puros, protegen el contrato de collares).

Gap de cobertura que el spec debera cubrir: (1) conservacion de nominal a traves de tramos encadenados; (2) round-trip del plano (columnas, flags y tipos) en `persistir_`/`cargar_plano_derivados_proy`; (3) "no doble renovacion" sobre un plano inyectado; (4) acumulacion del shift/delta de curva entre tramos (la curva de valoracion al inicio del tramo i refleja los deltas de 0..i-1, consistente con la senda continua de punta a punta).
