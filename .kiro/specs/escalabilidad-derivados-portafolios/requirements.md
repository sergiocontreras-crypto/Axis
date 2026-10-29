# Requirements Document

## Introduction

AXIS (Python + Dash + SQLite) valora y proyecta un portafolio de inversiones (renta fija, forwards, opciones, swaps) para la Gerencia Financiera. Esta funcionalidad agrupa cuatro mejoras de escalabilidad y correctitud sobre funcionalidad existente:

1. Dar de alta portafolios de Porfin nuevos (ramo, tipo, excluir) desde el dashboard, en lugar de editar la tabla `param_portafolios` a mano. La compañía se sigue derivando por el prefijo del código de portafolio.
2. Asignar el subyacente de swaps nuevos desde el dashboard, en lugar de editar la tabla `param_diccionario_swaps` a mano.
3. Reemplazar la devaluación implícita plana (`dev_forecast`) por una devaluación derivada del diferencial de tasas COP–USD (SWTIBR menos LUTIBR en efectivo anual, cada una con su shift), y calcular dinámicamente el punto forward de descuento a partir de esa devaluación en cada corte, en lugar de leer los puntos forward estáticos de PIP. Los shifts (TES COP para SWTIBR, FED/tesoros para LUTIBR) se interpolan linealmente mes a mes como en renta fija, con `dev_forecast` como fallback configurable.
4. Reemplazar el canal fijo del collar (`canal_opc`) por un buscar-objetivo (solver) que resuelva el strike de la Call para lograr un collar de prima neta cero, reutilizando las curvas r/q que ya se proyectan al mes del corte (mecanismo existente) y la volatilidad interpolada, con `canal_opc` como fallback configurable.

El movimiento mensual de las curvas de derivados reutiliza el mismo mecanismo de senda lineal de las curvas de renta fija: un shift total de S puntos básicos a N meses se acumula como `S * m / N` en el mes `m` (p.ej. 120 pbs a 12 meses ⇒ 10 pbs por mes).

El alcance transversal exige mantener el esquema de las tablas `param_*` e `hist_*`, ser consistente con la clasificación existente y conservar trazabilidad auditable de los parámetros calculados.

## Glossary

- **AXIS**: Sistema de valoración y proyección de portafolios de inversión objeto de esta especificación.
- **Dashboard**: Aplicación Dash (`app_dash.py`) que expone las pestañas de usuario, incluida la pestaña Inputs.
- **Tab_Inputs**: Pestaña "Inputs" del Dashboard (`layout_inputs` en `app_dash.py`) donde se gestionan insumos y parámetros.
- **Portafolio_Porfin**: Identificador de portafolio proveniente de Porfin, columna `portafolio_porfin` en la tabla `param_portafolios`.
- **Ramo**: Atributo de un Portafolio_Porfin (columna `ramo` en `param_portafolios`) usado en reglas de encaje.
- **Tipo**: Atributo de un Portafolio_Porfin (columna `tipo` en `param_portafolios`) usado en reglas de encaje.
- **Excluir**: Atributo de un Portafolio_Porfin (columna `excluir` en `param_portafolios`, valores 'SI'/'NO') usado por el cargador de portafolio para excluir posiciones.
- **Compania**: Compañía asociada a un portafolio (Capitalizadora, Bolívar, Comerciales o Sin Compañía), derivada por el prefijo del código de portafolio ('01'→Capitalizadora, '02'→Bolívar, '03'→Comerciales, otro→Sin Compañía). No se captura desde el dashboard.
- **Portafolio_Nuevo**: Portafolio_Porfin presente en el portafolio del corte seleccionado cuyo `portafolio_porfin` no existe aún en `param_portafolios`.
- **Swap_Sin_Subyacente**: Swap presente en el portafolio del corte seleccionado cuyo `id_posicion` no existe aún en `param_diccionario_swaps`.
- **Clasificador**: Módulo `clasificar_portafolio.py` que asigna encaje, compañía, asset class, régimen e `id_cobertura`.
- **Encaje**: Marca 'SI'/'NO' que indica si una posición computa para encaje, calculada por el Clasificador a partir de estado, Tipo y Ramo.
- **Diccionario_Swaps**: Tabla `param_diccionario_swaps` con columnas `id_posicion` (id del swap) y `Subyacente` (uno o varios ids separados por '|').
- **Subyacente**: Título o posición cubierta por un swap, referenciado por su `id_posicion`.
- **Id_Cobertura**: Relación de cobertura asignada por el Clasificador (swap→subyacente y título→swap).
- **Valorador_Derivados**: Módulo `valoracion_derivados.py` que renueva y valora forwards y opciones.
- **Forward_Renovado**: Forward generado por `_renovar_fwds` como renovación sucesiva de un forward existente.
- **Forward_Nuevo**: Forward generado por el método 3 (Exposición Objetivo) del Valorador_Derivados.
- **Dev_Forecast**: Devaluación anual implícita (parámetro plano, default 0.085) usada hoy para calcular la tasa negociada de forwards renovados/nuevos.
- **Curva_Forward_COP**: Puntos forward USD/COP por plazo (`fwd_cop`, indicador `FWPCOP`), expresados en pesos colombianos que se SUMAN a la TRM (no son una tasa en %). Hoy se cargan estáticamente de PIP por `_cargar_curvas(fecha_corte)`; en esta funcionalidad se reemplazan por el Punto_Forward_Dinamico.
- **Punto_Forward_Dinamico**: Punto forward en pesos calculado en cada corte como `TRM_corte * (1 + Devaluacion_Implicita)^(dias / 365) - TRM_corte`, donde `dias` es el plazo del corte al vencimiento del forward y la Devaluacion_Implicita se evalúa a ese plazo con las curvas proyectadas al mes del corte. Sustituye al valor estático de la Curva_Forward_COP en la valoración de forwards.
- **Curva_SWTIBR**: Curva de tasa de interés doméstica COP (indicador `SWTIBR`), expresada en % nominal simple base 360. Es la tasa que descuenta los forwards (`r_fwd`) y, convertida a continua, la tasa `r` de Black-Scholes.
- **Curva_LUTIBR**: Curva de tasa de interés foránea USD (indicador `LUTIBR`), expresada en % nominal simple base 360. Es la tasa foránea que, convertida a continua, actúa como `q` en Black-Scholes.
- **Curva_R_Corte**: Curva `r` continua ya proyectada al mes del corte que `valorar_portafolio_derivados` calcula hoy a partir de la Curva_SWTIBR en EA más el delta TES COP escalado por progreso, convertida a continua (variable `r_df_corte`). Mecanismo existente que esta funcionalidad reutiliza sin modificar.
- **Curva_Q_Corte**: Curva `q` continua ya proyectada al mes del corte que `valorar_portafolio_derivados` calcula hoy a partir de la Curva_LUTIBR en EA más el delta FED/tesoros escalado por progreso, convertida a continua (variable `q_df_corte`). Mecanismo existente que esta funcionalidad reutiliza sin modificar.
- **Serie_TRM**: Serie temporal de la Tasa Representativa del Mercado disponible por fecha.
- **Tasa_EA**: Tasa efectiva anual derivada de una tasa nominal simple base 360 para un plazo en días, mediante `(1 + tasa_nom * dias/360)^(365/dias) - 1`.
- **Devaluacion_Implicita**: Devaluación efectiva anual derivada por paridad de tasas de interés como `((1 + SWTIBR_EA) / (1 + LUTIBR_EA)) - 1`, donde SWTIBR_EA y LUTIBR_EA son las Tasa_EA de la Curva_SWTIBR y la Curva_LUTIBR (cada una con su shift aplicado) al plazo en días del forward. Reemplaza a Dev_Forecast en la fórmula de tasa negociada `TRM * (1 + Devaluacion_Implicita)^(dias/365)`.
- **Shift_TES**: Desplazamiento total (en puntos básicos) que se SUMA a la Curva_SWTIBR (tasa en %) a lo largo del horizonte, expresado como `shift_cop_bps` en el motor RF. Nunca se aplica a la Curva_Forward_COP (puntos en pesos).
- **Shift_FED**: Desplazamiento (en puntos básicos) que se SUMA a la Curva_LUTIBR (tasa en %), definido por dos anclas por plazo: el shift FED en el corto plazo (1 día) y el shift de tesoros 10 años en el largo plazo (3650 días), interpolado linealmente por plazo entre ambas anclas y flat más allá de 10 años.
- **Progreso_Mensual**: Fracción `m / N` del horizonte para el mes `m` de `N` meses, usada para interpolar linealmente un shift total en un shift acumulado mensual (`shift_total * m / N`).
- **Curva_Tasa_Proyectada**: Curva de tasa (Curva_SWTIBR o Curva_LUTIBR) desplazada al mes `m` sumando el shift correspondiente en % escalado por el Progreso_Mensual, análoga a la senda lineal mensual de las curvas de renta fija y al mecanismo ya existente de proyección de curvas de swaps (`proyectar_curvas_swaps`).
- **Canal_Opc**: Distancia (COP) entre el strike de la Call y el strike de la Put en un collar (parámetro plano, default 1650).
- **Collar**: Estructura de opciones compuesta por una Put y una Call sobre USD/COP.
- **Opciones_Existentes**: Opciones cargadas desde `hist_opciones` (provenientes del informe), cuyo strike viene dado y no lo determina Canal_Opc ni el Solver_Canal.
- **Collar_Zero_Cost**: Collar cuya prima neta (Call menos Put, con signos por sentido) es cero dentro de una tolerancia.
- **Interpolador_Vol**: Función `interpol_vol(strike, dias)` construida por `construir_interpolador_vol` que devuelve volatilidad implícita.
- **Curva_R**: Curva de tasa doméstica continua (`r_df`) por plazo usada en Black-Scholes.
- **Curva_Q**: Curva de tasa foránea continua (`q_df`) por plazo usada en Black-Scholes.
- **Precio_BS**: Función `precio_bs_signed` que calcula el precio Black-Scholes firmado de una opción.
- **Solver_Canal**: Rutina de buscar-objetivo que resuelve el strike de la Call para lograr un Collar_Zero_Cost.
- **Tolerancia_Prima**: Cota máxima admitida del valor absoluto de la prima neta del collar para considerarlo Collar_Zero_Cost.
- **Registro_Auditoria**: Registro persistido que documenta el valor calculado de un parámetro, sus insumos y su origen.

## Requirements

### Requirement 1: Alta de portafolios de Porfin nuevos desde el dashboard

**User Story:** Como analista de la Gerencia Financiera, quiero dar de alta los portafolios de Porfin nuevos desde el dashboard, para que su ramo, tipo y exclusión queden registrados y la clasificación sea correcta sin editar tablas a mano.

#### Acceptance Criteria

1. WHILE existe al menos un Portafolio_Nuevo en el portafolio del corte seleccionado, THE Dashboard SHALL mostrar en la Tab_Inputs un control que lista cada Portafolio_Nuevo con su `portafolio_porfin` ya identificado y campos para indicar Ramo, Tipo y Excluir.
2. WHILE no existe ningún Portafolio_Nuevo en el portafolio del corte seleccionado, THE Dashboard SHALL ocultar ese control.
3. WHEN el analista confirma el alta de un Portafolio_Nuevo con valores válidos, THE Dashboard SHALL insertar en la tabla `param_portafolios` un registro que contiene el `portafolio_porfin`, el `ramo`, el `tipo` y el `excluir` indicados, previa eliminación de espacios iniciales y finales del `portafolio_porfin`.
4. THE Dashboard SHALL restringir el valor de Excluir al conjunto {'SI', 'NO'}.
5. IF el analista confirma un alta con Ramo o Tipo vacío o compuesto solo por espacios, THEN THE Dashboard SHALL rechazar la operación, no escribir ningún dato en `param_portafolios` y mostrar un mensaje que indica el campo faltante.
6. IF el analista intenta dar de alta un `portafolio_porfin` que, sin espacios iniciales ni finales, ya existe en `param_portafolios`, THEN THE Dashboard SHALL rechazar el alta, no escribir ningún dato en `param_portafolios` y mostrar un mensaje que identifica el `portafolio_porfin` duplicado.
7. WHEN el Dashboard completa el alta de un Portafolio_Nuevo, THE Dashboard SHALL mostrar un mensaje de confirmación que identifica el `portafolio_porfin` afectado.
8. WHEN el Clasificador ejecuta la clasificación después del alta de un Portafolio_Nuevo, THE Clasificador SHALL asignar el Ramo y el Tipo registrados a las posiciones cuyo `portafolio` coincide con el `portafolio_porfin`.
9. THE Clasificador SHALL seguir derivando la Compania por el prefijo del código de portafolio ('01'→Capitalizadora, '02'→Bolívar, '03'→Comerciales, cualquier otro prefijo→Sin Compañía), sin capturar la compañía desde el Dashboard.
10. THE Dashboard SHALL preservar el esquema de columnas existente de la tabla `param_portafolios` (`portafolio_porfin`, `ramo`, `tipo`, `excluir`).

### Requirement 2: Asignación de subyacente a swaps nuevos desde el dashboard

**User Story:** Como analista de la Gerencia Financiera, quiero asignar el subyacente de los swaps nuevos desde el dashboard, para que el swap tenga cobertura y clasificación correctas sin editar tablas a mano.

#### Acceptance Criteria

1. WHILE existe al menos un Swap_Sin_Subyacente en el portafolio del corte seleccionado, THE Dashboard SHALL mostrar en la Tab_Inputs un control que lista cada Swap_Sin_Subyacente con su `id_posicion` ya identificado y un campo para indicar entre 1 y 50 `id_posicion` de Subyacente.
2. WHILE no existe ningún Swap_Sin_Subyacente en el portafolio del corte seleccionado, THE Dashboard SHALL ocultar ese control.
3. WHEN el analista confirma la asignación de subyacentes para un Swap_Sin_Subyacente cuyos `id_posicion` de Subyacente existen todos en el portafolio del corte seleccionado, THE Dashboard SHALL persistir en la tabla `param_diccionario_swaps` el `id_posicion` del swap y los ids de Subyacente concatenados con '|'.
4. IF alguno de los ids de Subyacente indicados no existe en el portafolio del corte seleccionado, THEN THE Dashboard SHALL rechazar la operación, no escribir en `param_diccionario_swaps` y mostrar un mensaje que identifica cada id de Subyacente inexistente.
5. IF el analista confirma la asignación con cero ids de Subyacente indicados, THEN THE Dashboard SHALL rechazar la operación, no escribir en `param_diccionario_swaps` y mostrar un mensaje que indica que se requiere al menos un Subyacente.
6. IF el analista intenta asignar subyacentes para un `id_posicion` de swap que ya existe en `param_diccionario_swaps`, THEN THE Dashboard SHALL rechazar la operación, no modificar el registro existente y mostrar un mensaje que identifica el swap ya registrado.
7. WHEN el Dashboard completa la asignación de subyacentes de un Swap_Sin_Subyacente, THE Dashboard SHALL mostrar un mensaje de confirmación que identifica el `id_posicion` del swap afectado.
8. WHEN el Clasificador ejecuta después de la asignación de subyacentes, THE Clasificador SHALL asignar el Id_Cobertura del swap y de sus Subyacentes conforme al Diccionario_Swaps.
9. THE Dashboard SHALL preservar el esquema de columnas existente de la tabla `param_diccionario_swaps`.

### Requirement 3: Devaluación implícita de forwards derivada de curvas

**User Story:** Como analista de la Gerencia Financiera, quiero que los forwards renovados y nuevos usen una devaluación derivada del diferencial de tasas COP–USD (con el shift mensual incluido) en lugar de un parámetro plano, para que la tasa negociada se mueva coherentemente con el movimiento mensual de las tasas.

#### Acceptance Criteria

1. WHEN el Valorador_Derivados genera un Forward_Renovado, THE Valorador_Derivados SHALL calcular la tasa negociada como `TRM * (1 + Devaluacion_Implicita)^(dias / 365)`, usando la Devaluacion_Implicita al plazo en días del forward y en el mes de su fecha de constitución.
2. WHEN el Valorador_Derivados genera un Forward_Nuevo en el método 3, THE Valorador_Derivados SHALL calcular la tasa negociada como `TRM * (1 + Devaluacion_Implicita)^(dias / 365)`, usando la Devaluacion_Implicita al plazo en días del forward y en el mes de su fecha de constitución.
3. THE Valorador_Derivados SHALL derivar la Devaluacion_Implicita como `((1 + SWTIBR_EA) / (1 + LUTIBR_EA)) - 1`, donde SWTIBR_EA y LUTIBR_EA son las Tasa_EA, al plazo en días del forward, de la Curva_Tasa_Proyectada de la Curva_SWTIBR y de la Curva_Tasa_Proyectada de la Curva_LUTIBR respectivamente.
4. THE Valorador_Derivados SHALL construir la Curva_Tasa_Proyectada de la Curva_SWTIBR para el mes `m` sumando a la tasa (en %) el Shift_TES escalado por el Progreso_Mensual `m / N`, y la de la Curva_LUTIBR sumando el Shift_FED (interpolado por plazo entre su ancla corta FED y su ancla larga de tesoros 10 años) escalado por el Progreso_Mensual `m / N`, con la misma interpolación lineal mensual que emplean las curvas de renta fija (para un shift total de S puntos básicos a N meses, el desplazamiento acumulado en el mes `m` es `S * m / N`).
5. THE Valorador_Derivados SHALL sumar el Shift_TES y el Shift_FED únicamente a las tasas en % de la Curva_SWTIBR y la Curva_LUTIBR, y SHALL NOT aplicar ningún shift a los puntos forward en pesos.
6. WHEN el Valorador_Derivados valora un forward en un corte de valoración, THE Valorador_Derivados SHALL calcular el Punto_Forward_Dinamico como `TRM_corte * (1 + Devaluacion_Implicita)^(dias / 365) - TRM_corte`, donde `dias` es el plazo desde la fecha del corte hasta el vencimiento del forward, `TRM_corte` es la TRM del corte y la Devaluacion_Implicita se evalúa a ese plazo con las curvas proyectadas al mes del corte.
7. WHEN el Valorador_Derivados descuenta un forward vivo en un corte, THE Valorador_Derivados SHALL usar el Punto_Forward_Dinamico en lugar del valor estático de la Curva_Forward_COP de PIP.
8. THE Valorador_Derivados SHALL restringir la Devaluacion_Implicita aceptada al rango cerrado [-0.5, 2.0]; IF el valor derivado queda fuera de ese rango, THEN THE Valorador_Derivados SHALL descartarlo y tratar la derivación como fallida.
9. IF no existe Curva_SWTIBR o Curva_LUTIBR para el plazo del forward, o la derivación de la Devaluacion_Implicita falla, o produce un valor no finito, THEN THE Valorador_Derivados SHALL usar el valor de Dev_Forecast como fallback para la Devaluacion_Implicita de ese forward.
10. THE Valorador_Derivados SHALL exponer Dev_Forecast como parámetro configurable de `valorar_portafolio_derivados` con valor por defecto 0.085 y rango válido [-0.5, 2.0].
11. WHEN el Valorador_Derivados calcula la Devaluacion_Implicita o el Punto_Forward_Dinamico de un forward, THE Valorador_Derivados SHALL producir un Registro_Auditoria con la fecha de constitución, el mes de proyección, el plazo en días, la TRM del corte, la SWTIBR_EA y la LUTIBR_EA aplicadas, la Devaluacion_Implicita resultante, el Punto_Forward_Dinamico y si se usó fallback.
12. THE Valorador_Derivados SHALL preservar las columnas y el contrato de retorno existentes de `valorar_portafolio_derivados`.

### Requirement 4: Canal del collar por buscar-objetivo de prima cero

**User Story:** Como analista de la Gerencia Financiera, quiero que el canal del collar se calcule como un buscar-objetivo de prima cero, para que los collares renovados sean zero-cost consistentes con la volatilidad y las curvas vigentes en lugar de un canal fijo.

#### Acceptance Criteria

1. WHEN el Valorador_Derivados renueva un Collar, THE Solver_Canal SHALL resolver el strike de la Call, dado el strike de la Put fijado en la TRM del corte, tal que el valor absoluto de la prima neta del Collar resultante sea menor o igual a Tolerancia_Prima.
2. THE Solver_Canal SHALL calcular la prima de cada pata del Collar usando Precio_BS con la volatilidad de Interpolador_Vol y las tasas de la Curva_R_Corte y la Curva_Q_Corte ya proyectadas al mes del corte, aplicando el signo por sentido (Compra/Venta) y por pata (Call/Put).
3. THE Valorador_Derivados SHALL reutilizar el mecanismo existente de proyección de la Curva_R_Corte y la Curva_Q_Corte, de modo que el precio y el delta de las Opciones_Existentes (las cargadas desde `hist_opciones`, cuyo strike viene dado) permanezcan idénticos al comportamiento actual; el Solver_Canal SHALL determinar el strike de la Call únicamente en el momento de creación de un Collar renovado.
4. WHEN el Solver_Canal fija el strike de la Call al crear un Collar renovado, THE Valorador_Derivados SHALL mantener inmutables las características del Collar renovado (strike de la Put, strike de la Call, nominal, sentido, tipo Call/Put y fechas de constitución y vencimiento) durante toda su vida, recalculando entre cortes únicamente su valoración.
5. WHEN el Solver_Canal converge, THE Valorador_Derivados SHALL asignar al Collar renovado el strike de la Call resuelto y producir un Collar_Zero_Cost cuyo valor absoluto de prima neta es menor o igual a Tolerancia_Prima.
6. THE Valorador_Derivados SHALL exponer Tolerancia_Prima como parámetro configurable de `valorar_portafolio_derivados`, expresado en COP por unidad de nominal, con valor por defecto 1.0 y rango admitido de 0.0001 a 1000.0.
7. IF el Solver_Canal no converge dentro del número máximo de iteraciones configurado, THEN THE Valorador_Derivados SHALL usar el strike de la Call calculado como strike de la Put más Canal_Opc como fallback, conservar el resto de la valoración del Collar sin interrumpir la corrida y registrar la no convergencia en un Registro_Auditoria.
8. THE Valorador_Derivados SHALL exponer el número máximo de iteraciones del Solver_Canal como parámetro configurable de `valorar_portafolio_derivados` con valor por defecto 100 y rango admitido de 1 a 1000.
9. THE Valorador_Derivados SHALL exponer Canal_Opc como parámetro configurable de `valorar_portafolio_derivados` con valor por defecto 1650.
10. WHEN el Solver_Canal resuelve el strike de la Call de un Collar, THE Valorador_Derivados SHALL producir un Registro_Auditoria con el mes de proyección, el Shift_TES y el Shift_FED acumulados aplicados, el strike de la Put, el strike de la Call resuelto, la prima neta resultante y si se usó fallback.
11. THE Valorador_Derivados SHALL preservar las columnas y el contrato de retorno existentes de `valorar_portafolio_derivados`.

### Requirement 5: Consistencia, esquema y trazabilidad transversal

**User Story:** Como responsable técnico de AXIS, quiero que estas mejoras mantengan el esquema de datos y la trazabilidad, para que la clasificación existente no se rompa y los parámetros calculados sean auditables.

#### Acceptance Criteria

1. THE Dashboard SHALL preservar, en todas las tablas `param_*` e `hist_*` afectadas por estas mejoras, el mismo conjunto de columnas, el mismo orden de columnas y el mismo tipo declarado de cada columna que existían antes de estas mejoras, sin agregar ni eliminar columnas.
2. WHEN se persiste un alta o edición de Portafolio_Porfin o de relación swap–subyacente, THE Dashboard SHALL escribir en SQLite enlazando todos los valores de entrada como parámetros de la consulta, sin concatenar ningún valor de entrada en el texto de la sentencia SQL.
3. WHEN el Valorador_Derivados calcula una Devaluacion_Implicita o resuelve un Solver_Canal, THE Valorador_Derivados SHALL persistir un Registro_Auditoria por cada cálculo, de modo que la totalidad de los Registro_Auditoria de la corrida quede disponible para consulta una vez finalizada la corrida sin volver a ejecutar la valoración.
4. WHEN el Clasificador ejecuta después de estas mejoras con el mismo estado de insumos de Tipo, Ramo y Diccionario_Swaps, THE Clasificador SHALL producir para cada posición las mismas asignaciones de Encaje, Compania e Id_Cobertura que producía antes de estas mejoras.
5. IF una escritura de alta o edición de Portafolio_Porfin o de relación swap–subyacente falla antes de completarse, THEN THE Dashboard SHALL revertir los cambios parciales, preservar el estado y el esquema previos de la tabla afectada y mostrar un mensaje que indica que la operación no se completó.
