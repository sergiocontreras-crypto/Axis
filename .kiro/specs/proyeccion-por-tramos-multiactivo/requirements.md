# Requirements Document

## Introduction

AXIS es un simulador de proyección de portafolio (Python 3.12+, Dash, SQLite). Hoy existe un modo de proyección `por_tramos` (feature `proyeccion-por-tramos`) que divide el horizonte en tramos anuales y ENCADENA corridas del `Motor_Proyeccion` de RENTA FIJA (el cierre de un tramo es el estado inicial del siguiente), persistiendo un snapshot por corte. Sin embargo, ese modo por tramos hoy tiene tres limitaciones que esta feature resuelve:

1. **Solo proyecta renta fija.** Los demás activos —Renta Variable (acciones locales, internacionales, PEI), Alternativos (locales, internacionales, 4G/IPC) y Créditos contratados— NO se incluyen en el encadenamiento por tramos; solo corren en el modo punta a punta o mediante botones individuales por activo.
2. **Las macro no se toman por tramo.** TRM, inflación, tasa repo y los retornos de RV/Alternativos/PEI se ingresan como un ÚNICO valor global para todo el horizonte. El negocio necesita un valor POR TRAMO (uno por cada fin de año), como en la tabla de inputs real del usuario.
3. **Repo e inflación no se propagan al motor por tramos.** El orquestador por tramos actual RECIBE `repo_ini/repo_fin` y la senda de inflación pero NO los usa en la corrida de cada tramo (solo aplica TRM y UVR); la inflación se arma con una senda plana y el repo se ignora. Esta feature CONECTA repo e inflación por tramo al motor.

Adicionalmente, esta feature reestructura la pestaña de Inputs del dashboard para que el `Selector_Modo` aparezca al inicio y, en modo `por_tramos`, cada sección de inputs se presente DIVIDIDA EN COLUMNAS POR TRAMO (una celda por tramo por variable), evitando secciones duplicadas; y para que UN SOLO botón consolidado ejecute todos los activos (RF + Derivados + RV + Alternativos + Créditos), eliminando los botones individuales por activo. El modo `punta_a_punta` permanece como comportamiento por defecto y 100% retrocompatible.

**Extensión (derivados por tramo + snapshot completo).** La versión inicial de esta feature integró RF + RV + Alternativos + Créditos en el encadenamiento por tramos, pero dejó DOS brechas que esta extensión cierra:

4. **Derivados NO se integran en el encadenamiento por tramos.** Hoy el `Orquestador_Por_Tramos` deja `derivados = None`; solo el modo punta a punta valora derivados. Además, el grid por tramo NO pide Δ Fed ni Δ Tesoros 10Y, movimientos de curva que el `Motor_Derivados` necesita para revaluar. Esta extensión integra derivados tramo a tramo (revaluación con TRM/curvas de cada tramo) y añade al grid las variables `Delta_Fed_Tramo` y `Delta_Tesoros10Y_Tramo`.
5. **El snapshot de cierre por tramo solo tiene RF.** `portafolio_clasificado_proy` persiste exclusivamente renta fija; el VM de cierre de RV/Alternativos vive solo en memoria y derivados no se persiste. El negocio necesita un `Snapshot_Completo_Tramo` con TODOS los activos (RF + RV + Alternativos + Derivados) al cierre de cada tramo, distinguibles por `clase_origen`.

**Correcciones post-implementación (bugs detectados en la primera corrida real por tramos).** La primera corrida completa (horizonte 2026→2028, 3 tramos) reveló tres defectos reales que esta sección de corrección resuelve:

6. **Las filas RF de los tramos i>0 se persistían sin `clase_origen`.** `_titulos_a_dataframe_base` (que reconstruye el portafolio del tramo i>0 desde el snapshot del tramo anterior) NO asignaba `clase_origen='TITULO'` a las filas reconstruidas. Resultado: el snapshot del tramo 2027 tenía 44.986 filas con `clase_origen = NULL`, y el Excel no discriminaba tipos de activo fuera del primer tramo. (El tramo 0 sí quedaba etiquetado porque su base viene de `portafolio_clasificado`, que ya trae `clase_origen`.)
7. **El `Snapshot_Completo_Tramo` no-RF nunca se materializó.** Lo que se vio como "derivados en 2026" (FORWARD/OPCION/SWAP) eran en realidad las posiciones del `portafolio_clasificado` base copiadas tal cual en el tramo 0, NO las filas valoradas que `_construir_snapshot_completo_tramo` intenta anexar (`DERIVADO`/`RENTA_VARIABLE`/`ALTERNATIVO`). Esas etiquetas nuevas no aparecen en ningún corte, lo que indica que los DataFrames de RV/Alt/Derivados no se están anexando (columnas esperadas ausentes o `vm_cierre` vacío). El snapshot completo multiactivo, tal como se diseñó, no funciona.
8. **El tiempo por tramo crece de forma insostenible por el volumen de reinversiones.** Cada reinversión de cupón genera un título sintético nuevo (correcto: cada uno con su tasa de compra y costo amortizado individual). El portafolio pasó de 2.763 títulos (2026) a 44.986 (2027), y el tramo 3 (que hereda esas ~45.000 posiciones y sigue reinvirtiendo) no terminó tras más de 3 horas de cómputo. NO es un bug de recomputo (el motor ya continúa la amortización desde el valor de cierre, no desde la emisión) ni de arrastre de vencidos (todas las posiciones del snapshot están vivas). Es un problema de ESCALA + implementación en Python puro (bucle título-por-título). La corrección optimiza el rendimiento SIN alterar la valoración ni agrupar títulos.

**Restricción de negocio INVIOLABLE (confirmada por el usuario):** los títulos a TIR / costo amortizado (clasificación `Vencimiento` y `Disp. Venta`) NO se pueden agrupar ni consolidar, porque cada reinversión nace con una tasa de compra única (p. ej. 6.2% vs 6.3%) que define su costo amortizado individual. Toda optimización de rendimiento (Req 17) DEBE preservar cada título como fila independiente con su tasa de compra y su costo amortizado propios.

## Glossary

- **AXIS**: Simulador de proyección de portafolio (aplicación completa: Dash + motores en `scripts/` + SQLite `portafolio.db`).
- **Motor_Proyeccion_RF**: Componente `proyectar_portafolio(...)` en `scripts/motor_proyeccion.py` que simula la evolución mensual del portafolio de renta fija. NO se modifica en esta feature.
- **Motor_RV**: Componente `proyectar_renta_variable(...)` en `scripts/proyeccion_renta_variable.py` que proyecta acciones locales/internacionales y PEI con retornos EA. NO se modifica su lógica interna.
- **Motor_Alternativos**: Componente `proyectar_alternativos(...)` en `scripts/proyeccion_alternativos.py` que proyecta fondos alternativos locales/internacionales y posiciones 4G (indexadas a IPC + spread). NO se modifica su lógica interna.
- **Motor_Creditos**: Componente `proyectar_creditos_contratados(...)` en `scripts/proyeccion_creditos.py` que proyecta créditos contratados con sendas de IPC e IBR. NO se modifica su lógica interna.
- **Orquestador_Por_Tramos**: Función `_ejecutar_escenario_por_tramos(...)` en `app_dash.py`. Es el componente que esta feature EXTIENDE para encadenar los cuatro motores por tramo.
- **Modo_Proyeccion**: Selector de estrategia con dos valores: `punta_a_punta` (por defecto, retrocompatible) y `por_tramos`.
- **Selector_Modo**: Control de UI (`modo-proyeccion`) que elige el `Modo_Proyeccion`. Esta feature lo REUBICA al inicio de la pestaña de Inputs.
- **Tramo**: Sub-periodo del horizonte desde el inicio del tramo hasta el fin del año calendario correspondiente (o la fecha de fin del horizonte para el último tramo). Delimitado por `calcular_tramos` (sin cambios).
- **Tramo_Parcial**: Primer tramo cuando el horizonte no arranca en enero. Tiene menos de 12 meses (ej. Ago-Dic 2026 = 5 meses). Sus retornos EA se convierten a la duración del tramo.
- **Fecha_Corte_Tramo**: Fecha de cierre de un tramo. Identifica cada snapshot y el año al que pertenecen los inputs de ese tramo.
- **Sub_Horizonte**: Número de meses de un tramo (entre el inicio del tramo y su `Fecha_Corte_Tramo`). Un tramo de año completo (ej. 1-ene a 31-dic) representa 12 meses de proyección.
- **Conteo_Meses_Inclusivo**: Regla para contar los meses de un periodo `[inicio, corte]` (tanto un tramo como el horizonte completo de punta a punta) de modo que un año calendario completo cuente 12 (no 11). Corrige el off-by-one de la fórmula `delta.years*12 + delta.months`, que descarta el residuo de días (`relativedelta(31-dic, 1-ene)` = 11 meses + 30 días → 11). El conteo correcto suma el mes residual cuando hay días sobrantes (`+ 1 si delta.days > 0`), consistente con el cálculo ya usado en `_calcular_uvr_cierre_desde_inflacion` y otros callizados. Aplica a `calcular_tramos._meses_entre` (modo por tramos) y al orquestador consolidado `punta_a_punta` (que hoy omite el `+1`).
- **Encadenamiento**: Regla por la cual el estado de cierre de un tramo (VM/saldo proyectado) es el estado inicial (t0) del tramo siguiente. Aplica a los CUATRO activos.
- **Inputs_Por_Tramo**: Juego completo de variables aplicable a UN tramo (un valor por cada fin de año), según la tabla del usuario: Inflación, Tasa repo, Mov. TES COP (bps), Mov. TES UVR (bps), Spread Corp IPC (bps), Spread Corp TF (bps), TRM, Ret Acciones Locales, Ret Alternativos (local/intl), Ret Alternativos IPC (bps), PEI.
- **Retorno_EA**: Retorno Efectivo Anual. TODOS los retornos de la tabla del usuario (RV local, alternativos, PEI, etc.) son EA.
- **Conversion_EA_A_Tramo**: Regla que convierte un `Retorno_EA` a la duración del tramo: para un tramo de `m` meses, `r_tramo = (1 + r_EA)^(m/12) − 1`. Para tramos completos `m=12` ⇒ `r_tramo = r_EA`.
- **TRM_Por_Tramo**: TRM de cierre de cada tramo (nivel objetivo al `Fecha_Corte_Tramo`). Dentro del tramo el `Motor_Proyeccion_RF` interpola desde la TRM de cierre del tramo anterior (o la TRM t0 para el primer tramo).
- **Repo_Por_Tramo**: Tasa repo aplicable a un tramo (EA). Se propaga a la corrida del motor de ese tramo (arreglo de la brecha actual).
- **Inflacion_Por_Tramo**: Inflación (IPC) EA aplicable a un tramo. Se propaga a la corrida del motor de ese tramo (arreglo de la brecha actual) y alimenta activos indexados a IPC (RF IPC, Alternativos 4G, Créditos IPC).
- **Boton_Consolidado**: Único botón de la pestaña de Inputs que ejecuta la corrida completa (RF + Derivados + RV + Alternativos + Créditos) en el `Modo_Proyeccion` seleccionado.
- **Botones_Individuales**: Botones actuales por activo (▶️ Proyectar Alternativos, Créditos, RV). Esta feature los ELIMINA.
- **Layout_Por_Tramo_UI**: Presentación de las secciones de inputs dividida en columnas por tramo cuando el `Modo_Proyeccion` es `por_tramos`.
- **Resultados_Consolidados**: Estructura de resultados en el `Store_Dashboard` con las claves de todos los activos (`rf_resumen`, `derivados`, `simultaneas`, `rv`, `alternativos`, `creditos`, ...), producida por la corrida por tramos igual que hoy la produce el modo punta a punta.
- **Store_Dashboard**: Almacenamiento en memoria del dashboard donde vive el resultado para render y exportación.
- **Motor_Derivados**: Componente `valorar_portafolio_derivados(...)` en `valoracion_derivados.py`. Valora opciones, forwards, swaps y activos USD bajo un escenario de TRM y curvas. Es STATELESS por corrida: lee las posiciones vigentes de `hist_forwards`/`hist_opciones`/`hist_swaps WHERE fecha_reporte=?` y los activos USD de `portafolio_clasificado`, y recalcula internamente las renovaciones (`_renovar_fwds`, `_renovar_opciones`) desde ese plano base para la `fecha_corte`. NO se modifica su lógica interna.
- **Deltas_Curvas_Tramo**: Movimientos de curvas que consume el `Motor_Derivados` por tramo, vía `metodo_params`: `delta_tes_cop` y `delta_tes_uvr` (shift paralelo en bps por nodo, derivado de Mov. TES COP/UVR del tramo), `delta_fed_bps` (Δ tasa FED del tramo, ancla corta de la Curva_LUTIBR) y `delta_10t_bps` (Δ tesoros 10Y del tramo, ancla larga de la Curva_LUTIBR). Son DELTAS por tramo (no niveles absolutos t0).
- **Delta_Fed_Tramo / Delta_Tesoros10Y_Tramo**: Nuevas variables de `Inputs_Por_Tramo` (en bps) que capturan el movimiento de la tasa FED y de los tesoros 10Y por tramo, requeridas por el `Motor_Derivados` y hoy ausentes del grid por tramo.
- **PyG_Derivados_Tramo**: PyG de derivados de un tramo, calculado en la orquestación como `valoración(cierre_tramo) − valoración(inicio_tramo)` más los flujos del tramo (vencimientos/cupones), consistente con `calcular_pyg` (que ya devuelve el PyG relativo a base por tipo). Las renovaciones se resuelven internamente en el motor tal como hoy.
- **Snapshot_Completo_Tramo**: Extensión del snapshot de cierre por tramo (`portafolio_clasificado_proy`) para incluir TODOS los activos (RF + RV + Alternativos + Derivados), no solo RF. Cada fila se distingue por `clase_origen`. Hoy el snapshot es exclusivamente RF (la reconstrucción `cargar_titulos_desde_snapshot` filtra no-RF).
- **Estado_Derivados_Tramo**: Plano de posiciones de derivados VIVAS al cierre de un tramo (forwards/opciones/swaps con vencimiento posterior al `Fecha_Corte_Tramo`, incluidas las renovaciones que el motor generó internamente). Se persiste en tablas de proyección SEPARADAS (`hist_forwards_proy`/`hist_opciones_proy`/`hist_swaps_proy`) —nunca en las `hist_*` reales— y es el plano de INICIO del tramo siguiente, logrando un encadenamiento real del plano de posiciones (análogo al snapshot RF de `portafolio_clasificado_proy`).

## Requirements

### Requirement 1: Inputs por tramo en el dashboard

**User Story:** Como analista, quiero ingresar un juego completo de variables por cada tramo (uno por fin de año), para reflejar supuestos macro y de retorno que cambian año a año.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `por_tramos` THEN el dashboard SHALL presentar, por cada `Tramo` del horizonte, una celda de entrada para cada variable de `Inputs_Por_Tramo`: Inflación, Tasa repo, Mov. TES COP (bps), Mov. TES UVR (bps), Spread Corp IPC (bps), Spread Corp TF (bps), TRM, Ret Acciones Locales, Ret Alternativos Local, Ret Alternativos Internacional, Ret Alternativos IPC (bps) y PEI.
2. WHEN el número de tramos cambia (por cambio de fecha de inicio/fin) THEN el dashboard SHALL regenerar las columnas de inputs por tramo para que haya exactamente una columna por tramo.
3. WHERE una variable de `Inputs_Por_Tramo` no es ingresada para un tramo THE dashboard SHALL usar un valor por defecto documentado y no bloquear la corrida por ese campo vacío.
4. WHEN el `Modo_Proyeccion` es `punta_a_punta` THEN el dashboard SHALL conservar el layout actual de una sola columna de valores (retrocompatibilidad).

### Requirement 2: Selector de modo al inicio y layout dividido por tramo

**User Story:** Como analista, quiero elegir el modo al principio de la pestaña de Inputs y que las secciones se dividan por tramo, para no ver secciones duplicadas pidiendo lo mismo.

#### Acceptance Criteria

1. THE `Selector_Modo` SHALL aparecer al inicio de la pestaña de Inputs, antes de las secciones de variables.
2. WHEN el `Modo_Proyeccion` es `por_tramos` THEN cada sección de inputs (macro, RF, RV, alternativos, créditos) SHALL presentarse en `Layout_Por_Tramo_UI` (una columna por tramo), sin secciones duplicadas para la misma variable.
3. WHEN el `Modo_Proyeccion` es `punta_a_punta` THEN las secciones SHALL presentarse con el layout actual.
4. THE cambio de `Modo_Proyeccion` SHALL actualizar la presentación sin recargar la página ni perder la fecha de inicio/fin seleccionadas.

### Requirement 3: Botón único consolidado; eliminar botones individuales

**User Story:** Como analista, quiero un solo botón que corra todo, para no ejecutar cada activo por separado.

#### Acceptance Criteria

1. THE pestaña de Inputs SHALL exponer un `Boton_Consolidado` que ejecuta RF + Derivados + RV + Alternativos + Créditos en el `Modo_Proyeccion` seleccionado.
2. THE feature SHALL eliminar los `Botones_Individuales` por activo (Alternativos, Créditos, RV) de la pestaña de Inputs.
3. WHEN se elimina un botón individual THEN los callbacks huérfanos asociados SHALL retirarse o quedar sin binding activo, sin dejar `Output`/`Input` colgados que rompan el arranque del dashboard.
4. THE `Boton_Consolidado` SHALL funcionar tanto en `punta_a_punta` como en `por_tramos`.

### Requirement 4: Encadenamiento multiactivo por tramos

**User Story:** Como analista, quiero que todos los activos se encadenen por tramos igual que la renta fija, para que el cierre de un año sea el punto de partida del siguiente.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `por_tramos` THEN el `Orquestador_Por_Tramos` SHALL ejecutar, por cada tramo y en orden cronológico, los cuatro motores: `Motor_Proyeccion_RF`, `Motor_RV`, `Motor_Alternativos` y `Motor_Creditos`.
2. THE estado de cierre de cada activo en un tramo (valor de mercado / saldo proyectado al `Fecha_Corte_Tramo`) SHALL usarse como estado inicial (t0) del mismo activo en el tramo siguiente (`Encadenamiento`).
3. THE `Encadenamiento` de renta fija SHALL conservar el comportamiento actual (snapshot de cierre con spread efectivo como base del siguiente tramo).
4. WHEN un motor de un activo falla en un tramo THEN el `Orquestador_Por_Tramos` SHALL detener el encadenamiento e informar el activo y la `Fecha_Corte_Tramo` del fallo, sin exponer resultados de fin de horizonte incompletos.

### Requirement 5: Conversión de retornos EA a la duración del tramo

**User Story:** Como analista, quiero que los retornos anuales se ajusten a la duración de cada tramo, para que el tramo parcial no aplique un año completo de retorno.

#### Acceptance Criteria

1. THE feature SHALL tratar todos los retornos de `Inputs_Por_Tramo` (RV local, RV internacional, PEI, Alternativos local, Alternativos internacional) como `Retorno_EA`.
2. WHEN un tramo tiene `m` meses THEN el retorno aplicado a ese tramo SHALL ser `Conversion_EA_A_Tramo`: `(1 + r_EA)^(m/12) − 1`.
3. WHEN un tramo es completo (`m = 12`) THEN el retorno aplicado SHALL ser exactamente `r_EA` (la conversión es identidad).
4. WHEN un tramo es `Tramo_Parcial` de `m < 12` meses THEN el retorno aplicado SHALL ser la capitalización EA a `m/12` de año.

### Requirement 6: TRM interpolada por tramo

**User Story:** Como analista, quiero una TRM por tramo que se interpole dentro del tramo, para reflejar la trayectoria de la tasa de cambio.

#### Acceptance Criteria

1. THE `TRM_Por_Tramo` de un tramo SHALL interpretarse como la TRM de cierre (nivel objetivo al `Fecha_Corte_Tramo`).
2. WHEN se corre un tramo THEN el `Motor_Proyeccion_RF` SHALL interpolar la TRM desde la TRM de cierre del tramo anterior (o la TRM t0 para el primer tramo) hasta la `TRM_Por_Tramo` del tramo.
3. THE activos con exposición internacional (RF internacional, RV internacional, Alternativos internacional) SHALL usar la misma TRM de cierre por tramo para su conversión a COP en ese tramo.

### Requirement 7: Repo e inflación por tramo propagados al motor

**User Story:** Como analista, quiero que la tasa repo y la inflación por tramo afecten realmente la proyección, para que los supuestos macro se reflejen en el resultado.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `por_tramos` THEN el `Repo_Por_Tramo` de cada tramo SHALL propagarse a la corrida del motor de ese tramo (arreglando la brecha actual en la que el repo se ignora).
2. WHEN el `Modo_Proyeccion` es `por_tramos` THEN la `Inflacion_Por_Tramo` de cada tramo SHALL propagarse a la corrida del motor de ese tramo, en lugar de una senda plana global.
3. THE `Inflacion_Por_Tramo` SHALL alimentar los activos indexados a IPC (RF IPC, Alternativos 4G/IPC, Créditos IPC) del tramo.
4. THE arreglo de repo e inflación SHALL NO alterar el resultado del modo `punta_a_punta`.

### Requirement 8: Resultados consolidados por tramos

**User Story:** Como analista, quiero que el resultado por tramos incluya todos los activos, para ver el PyG consolidado del portafolio completo.

#### Acceptance Criteria

1. WHEN termina una corrida `por_tramos` THEN el `Store_Dashboard` SHALL contener `Resultados_Consolidados` con las claves de todos los activos (`rf_resumen`, `derivados`, `simultaneas`, `rv`, `alternativos`, `creditos`).
2. THE render de resultados SHALL mostrar los activos no-RF del modo por tramos con el mismo formato con que hoy los muestra el modo punta a punta.
3. THE resultado por tramos SHALL reportar el consolidado de fin de horizonte (último tramo) además de la persistencia por corte que ya existe para RF.

### Requirement 9: Retrocompatibilidad del modo punta a punta

**User Story:** Como analista, quiero que el modo actual siga funcionando igual, para no perder el flujo existente.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `punta_a_punta` THEN la corrida SHALL conservar su flujo y contrato actuales (mismos activos, mismos inputs globales, mismo formato de resultados), CON LA ÚNICA EXCEPCIÓN INTENCIONADA del `Conteo_Meses_Inclusivo` del Requirement 10 (que corrige el número de meses del horizonte; este cambio es un arreglo deliberado, no una regresión).
2. THE eliminación de los `Botones_Individuales` SHALL NO afectar la capacidad del modo punta a punta de proyectar todos los activos vía el `Boton_Consolidado`.
3. THE cambios de UI SHALL NO romper el arranque del dashboard (sin callbacks con referencias colgadas).

### Requirement 10: Conteo correcto de meses del horizonte (12 para periodos anuales, en ambos modos)

**User Story:** Como analista, quiero que la proyección cubra todos los meses del horizonte —por tramos y punta a punta—, para que un periodo anual (de un diciembre a otro, o de enero a diciembre) proyecte los 12 meses completos y no pierda un mes.

#### Acceptance Criteria

1. WHEN un tramo cubre un año calendario completo (del 1 de enero al 31 de diciembre) THEN su `Sub_Horizonte` SHALL ser 12 (no 11).
2. WHEN el modo `punta_a_punta` cubre un periodo anual (por ejemplo, de un 31 de diciembre al 31 de diciembre siguiente) THEN el `meses_horizonte` pasado al `Motor_Proyeccion_RF` SHALL ser 12 (no 11).
3. THE cálculo del número de meses (tanto el `Sub_Horizonte` por tramo como el `meses_horizonte` de punta a punta) SHALL usar `Conteo_Meses_Inclusivo`, sumando el mes residual cuando el `relativedelta` deja días sobrantes (`+ 1 si delta.days > 0`).
4. WHEN se corre una proyección (en cualquiera de los dos modos) THEN el número de iteraciones mensuales del `Motor_Proyeccion_RF` SHALL coincidir con el número de meses corregido del periodo.
5. THE número mostrado en el dashboard entre paréntesis para cada tramo SHALL coincidir exactamente con el `Sub_Horizonte` usado por el motor (mismo dato; sin discrepancia visual vs. cálculo).
6. THE `Conteo_Meses_Inclusivo` SHALL aplicarse de forma consistente al `Sub_Horizonte`, al `meses_horizonte` de punta a punta, al prorrateo de reservas por tramo, y a la `Conversion_EA_A_Tramo` (que usará `m` = meses corregidos del periodo).
7. THE corrección del conteo SHALL unificar la fórmula ya usada correctamente en otras partes del código (`delta.years*12 + delta.months + (1 if delta.days > 0 else 0)`, presente en `_calcular_uvr_cierre_desde_inflacion` y otros callizados) con el orquestador consolidado punta a punta, que hoy la omite (`delta.years*12 + delta.months`), eliminando la inconsistencia interna.

### Requirement 11: Valoración en moneda origen y efecto moneda de activos internacionales

**User Story:** Como analista, quiero que los activos internacionales apliquen su retorno en moneda origen y que el Excel de salida discrimine el efecto moneda, para separar cuánto del PyG viene del activo y cuánto del movimiento de la TRM.

#### Acceptance Criteria

1. WHEN se proyecta un `Activo_Internacional` en un tramo THEN el sistema SHALL aplicar `Valoracion_En_Moneda_Origen`: convertir el VM inicial COP a moneda origen con la TRM de inicio del tramo, aplicar el retorno del tramo en moneda origen, y reconvertir a COP con la TRM de cierre del tramo.
2. THE `Valoracion_En_Moneda_Origen` SHALL aplicarse en TODOS los tramos, usando en cada uno la `TRM_Por_Tramo` correspondiente (encadenada: la TRM de cierre de un tramo es la TRM de inicio del siguiente).
3. THE Excel de salida SHALL incluir, para cada `Activo_Internacional`, la discriminación del `Efecto_Moneda` calculado como `VM_inicial_origen × (TRM_cierre_tramo − TRM_inicio_tramo)`.
4. THE Excel de salida SHALL incluir el `Efecto_Retorno_Activo` (`PyG_total_tramo − Efecto_Moneda`), de modo que `Efecto_Moneda + Efecto_Retorno_Activo` reconstruya el PyG total del activo en el tramo.
5. THE discriminación del `Efecto_Moneda` y del `Efecto_Retorno_Activo` SHALL presentarse por tramo y con un total del horizonte.
6. WHERE un activo es local (COP) THE `Efecto_Moneda` SHALL ser cero (no aplica reconversión de TRM).

### Requirement 12: Trazabilidad y seguridad de datos (entrada no confiable)

**User Story:** Como responsable técnico, quiero que los inputs por tramo se validen y no filtren datos sensibles, para mantener la robustez del sistema.

#### Acceptance Criteria

1. THE valores de `Inputs_Por_Tramo` SHALL validarse por tipo y rango razonable; un valor no numérico o vacío SHALL caer a su valor por defecto sin abortar la corrida.
2. THE acceso a SQLite SHALL usar exclusivamente consultas parametrizadas.
3. WHEN ocurre un error en un tramo THEN el mensaje al usuario SHALL identificar activo y `Fecha_Corte_Tramo` sin volcar contenido de archivos ni datos sensibles.
4. THE nueva lógica SHALL vivir en la capa de aplicación/helpers replicando patrones existentes, sin modificar los motores.

### Requirement 13: Derivados integrados en el encadenamiento por tramos

**User Story:** Como analista, quiero que los derivados se revalúen tramo a tramo con la TRM y las curvas de cada tramo, para que el PyG de derivados forme parte del resultado consolidado por tramos igual que los demás activos.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `por_tramos` THEN el `Orquestador_Por_Tramos` SHALL invocar el `Motor_Derivados` por cada tramo, con `fecha_corte` = inicio del tramo, `fecha_fin` = `Fecha_Corte_Tramo`, la `senda_trm` interpolada del tramo y los `Deltas_Curvas_Tramo` del tramo en `metodo_params`.
2. THE `PyG_Derivados_Tramo` de un tramo SHALL calcularse como la valoración al cierre del tramo menos la valoración al inicio del tramo, más los flujos del tramo (vencimientos/cupones), consistente con `calcular_pyg`.
3. THE renovaciones de posiciones (forwards, opciones) SHALL resolverse internamente en el `Motor_Derivados` tal como hoy (sin cambiar su LÓGICA de renovación/valoración); el `Estado_Derivados_Tramo` (plano de posiciones VIVAS al cierre del tramo, con sus renovaciones) SHALL persistirse en tablas de proyección separadas (`hist_forwards_proy`/`hist_opciones_proy`/`hist_swaps_proy`) y usarse como plano de INICIO del tramo siguiente (`Encadenamiento` real, análogo al snapshot RF), NUNCA escribiendo en las tablas `hist_*` reales.
4. THE grid de `Inputs_Por_Tramo` SHALL incluir dos variables nuevas por tramo: `Delta_Fed_Tramo` (Δ Fed en bps, id `tramo-fed`) y `Delta_Tesoros10Y_Tramo` (Δ Tesoros 10Y en bps, id `tramo-tesoros10y`).
5. THE `Deltas_Curvas_Tramo` pasados al `Motor_Derivados` SHALL derivarse así: `delta_tes_cop`/`delta_tes_uvr` desde Mov. TES COP/UVR del tramo (shift paralelo en bps → delta EA por nodo, mismo patrón que el modo punta a punta), `delta_fed_bps` desde `Delta_Fed_Tramo` y `delta_10t_bps` desde `Delta_Tesoros10Y_Tramo`.
6. WHEN termina una corrida `por_tramos` THEN el `Store_Dashboard` SHALL incluir la clave `derivados` con el PyG de derivados (consistente con el formato que hoy produce el modo punta a punta), en lugar de `None`.
7. WHEN el `Motor_Derivados` falla en un tramo THEN el `Orquestador_Por_Tramos` SHALL detener el encadenamiento e informar "Derivados" y la `Fecha_Corte_Tramo` del fallo, sin exponer resultados de fin de horizonte incompletos.
8. THE integración de derivados por tramo SHALL limitar el cambio en `valorar_portafolio_derivados` a la CAPA DE CARGA de posiciones, mediante dos parámetros opcionales retrocompatibles: `posiciones_iniciales` (plano de inicio inyectado; si es `None`, lee `hist_*` como hoy) y `devolver_plano_cierre` (si `True`, devuelve el plano vivo al `fecha_fin`); la LÓGICA de renovación y valoración del motor SHALL permanecer intacta y el comportamiento SHALL ser idéntico al actual cuando no se inyecta plano.
9. THE encadenamiento del plano de derivados SHALL persistir el `Estado_Derivados_Tramo` de forma idempotente por corte y usar exclusivamente consultas parametrizadas; las tablas `_proy` SHALL crearse de forma idempotente (`CREATE TABLE IF NOT EXISTS`) y limpiarse al inicio de cada corrida como el snapshot RF.

### Requirement 14: Snapshot completo de todos los activos al cierre de cada tramo

**User Story:** Como analista, quiero que al cierre de cada tramo exista una base de portafolio proyectado con TODOS los activos (RF, RV, Alternativos y Derivados), para tener una foto completa del portafolio por corte y no solo de renta fija.

#### Acceptance Criteria

1. WHEN el `Orquestador_Por_Tramos` cierra un tramo THEN SHALL persistir un `Snapshot_Completo_Tramo` en `portafolio_clasificado_proy` que incluya las posiciones de RF, RV, Alternativos y Derivados de ese corte.
2. THE cada fila del `Snapshot_Completo_Tramo` SHALL distinguir el tipo de activo mediante la columna `clase_origen` (p. ej. `TITULO` para RF, y los valores de `clase_origen` correspondientes a RV, Alternativos y Derivados).
3. THE persistencia SHALL usar la allowlist de columnas existente (`_COLUMNAS_PROY`), ampliándola SOLO si es estrictamente necesario para representar RV/Alternativos/Derivados; las columnas ausentes para un tipo de activo SHALL guardarse como NULL.
4. THE `Encadenamiento` de renta fija SHALL seguir reconstruyéndose EXCLUSIVAMENTE desde las filas RF del snapshot (`cargar_titulos_desde_snapshot` sigue filtrando `clase_origen` de RF), de modo que añadir filas no-RF NO altere la reconstrucción de títulos del tramo siguiente.
5. THE persistencia del `Snapshot_Completo_Tramo` SHALL usar exclusivamente consultas parametrizadas y ser idempotente por corte (reemplazo de la corrida previa), conservando el comportamiento actual de `persistir_snapshot_proy`/`limpiar_snapshots_proy`.
6. WHERE un tipo de activo no aporta una métrica de una columna de la allowlist THE valor SHALL ser NULL sin abortar la persistencia.
7. THE VM de cierre de RV/Alternativos (hoy solo en memoria vía `EstadoEncadenado`) y el valor de mercado de cierre de Derivados SHALL quedar reflejados en el `Snapshot_Completo_Tramo` de cada corte.

### Requirement 15: Etiquetado de `clase_origen` en el snapshot RF de todos los tramos (Fix 1)

**User Story:** Como analista, quiero que las filas de renta fija del snapshot queden etiquetadas con su `clase_origen` en TODOS los tramos (no solo el primero), para que el Excel y los análisis discriminen el tipo de activo en 2027, 2028 y demás cortes.

#### Acceptance Criteria

1. WHEN el `Orquestador_Por_Tramos` reconstruye el portafolio base de un tramo i>0 desde el snapshot del tramo anterior (`_titulos_a_dataframe_base`) THEN cada fila de renta fija SHALL incluir `clase_origen = 'TITULO'`.
2. THE snapshot persistido de CUALQUIER tramo (incluido i>0) SHALL tener las filas de renta fija con `clase_origen = 'TITULO'` no nulo.
3. THE corrección SHALL NO alterar el comportamiento del tramo 0 (cuya base viene de `portafolio_clasificado` con `clase_origen` ya poblado) ni la reconstrucción de títulos del tramo siguiente.

### Requirement 16: Materialización efectiva del snapshot completo no-RF por tramo (Fix 2)

**User Story:** Como analista, quiero que las posiciones de Derivados, Renta Variable y Alternativos valoradas por los motores queden efectivamente escritas en el `Snapshot_Completo_Tramo` de cada corte, para que la foto del portafolio por tramo incluya realmente todos los activos y no solo renta fija.

#### Acceptance Criteria

1. THE `Snapshot_Completo_Tramo` de cada tramo SHALL contener filas con `clase_origen` en {`DERIVADO`, `RENTA_VARIABLE`, `ALTERNATIVO`} cuando los motores respectivos produjeron posiciones de cierre en ese tramo.
2. WHEN los DataFrames de RV/Alternativos de cierre no tienen exactamente las columnas esperadas por `_filas_cierre_no_rf` (`id`, `fecha_proyeccion`, `valor_mercado`) THEN la feature SHALL mapear las columnas reales que producen los motores (verificado contra su salida) en lugar de descartar silenciosamente las filas.
3. WHEN el adaptador de derivados devuelve el plano/VM de cierre THEN el `vm_cierre` por posición SHALL poblarse de forma consistente con las columnas reales del resultado del `Motor_Derivados` (`valor_mercado` o `valoracion`).
4. THE snapshot completo SHALL evitar DUPLICAR las posiciones de derivados del `portafolio_clasificado` base (tramo 0) con las valoradas por el motor; se define una fuente única por tipo de activo y corte.
5. THE materialización no-RF SHALL preservar el invariante del Req 14.4 (la reconstrucción RF del tramo siguiente sigue filtrando solo filas RF).
6. THE feature SHALL incluir un test que verifique, con un corte de ejemplo, que el snapshot contiene filas no-RF con sus `clase_origen` correctos (no solo las del portafolio base).

### Requirement 17: Rendimiento del encadenamiento por tramos sin alterar la valoración (Fix 3)

**User Story:** Como analista, quiero que una proyección por tramos de varios años termine en un tiempo razonable, para poder iterar escenarios sin esperar horas, sin que ello cambie ningún número de la valoración.

#### Acceptance Criteria

1. THE optimización de rendimiento SHALL preservar EXACTAMENTE la valoración de cada título: misma tasa de compra, mismo costo amortizado, mismo valor de mercado y mismo P&L por posición que el resultado actual (verificado numéricamente sobre un caso de referencia dentro de una tolerancia definida).
2. THE optimización SHALL NO agrupar ni consolidar títulos a TIR (`Vencimiento`/`Disp. Venta`); cada reinversión SHALL permanecer como fila independiente con su tasa de compra individual (restricción inviolable).
3. THE feature SHALL incorporar medición de tiempos por motor y por tramo (instrumentación) que permita identificar el componente más costoso, registrada en el log sin volcar datos sensibles.
4. THE optimización SHALL atacar el cómputo intrínseco por volumen mediante vectorización de los bucles título-por-título (p. ej. en `_construir_df_proy_rf` y en la valoración del motor donde aplique), SIN cambiar la lógica de negocio.
5. THE persistencia por tramo SHALL reducir el costo de I/O (escritura en lote y, opcionalmente, ubicación de la base de datos fuera de un sistema de archivos sincronizado en la nube), sin cambiar el esquema ni el contrato de datos.
6. WHERE una posición ya venció (fecha de vencimiento <= `Fecha_Corte_Tramo`) THE no SHALL arrastrarse al tramo siguiente (comportamiento actual confirmado correcto; se mantiene como invariante).
7. THE documentación SHALL dejar explícito que el número de posiciones crece legítimamente por las reinversiones y que la optimización lo hace tolerable pero no elimina el crecimiento inherente a la metodología.
8. THE optimización SHALL ser verificable: un caso de referencia (p. ej. 2 tramos con un portafolio reducido) SHALL producir el mismo resultado antes y después, y un test de tiempos SHALL evidenciar la mejora.
