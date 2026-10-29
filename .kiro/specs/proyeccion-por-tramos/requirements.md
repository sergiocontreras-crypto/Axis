# Requirements Document

## Introduction

AXIS es un simulador de proyección de portafolio de renta fija (Python 3.12+, Dash, SQLite). Hoy la proyección se ejecuta "de punta a punta": el orquestador calcula un horizonte en meses entre la fecha de inicio y la fecha de fin, y llama UNA sola vez a `proyectar_portafolio(...)` (en `scripts/motor_proyeccion.py`) con un ÚNICO juego de retornos/shifts. El motor reparte esos shifts LINEALMENTE sobre todo el horizonte (`shift * mes / meses_horizonte`) y corre un loop MENSUAL en el que detecta cupones, procesa vencimientos, reinvierte, aplica compras/ventas por variación de reserva (vía `cargar_mapa_reservas` de `scripts/compras_ventas_reservas.py`) y re-valora cada título con las fórmulas BVC. El resultado se arma como un `df_proy`, se reclasifica en memoria con `clasificar_portafolio_en_memoria(df_proy, fecha_fin)` y se guarda en el store del dashboard.

Esta feature agrega un modo de proyección OPCIONAL "por tramos", en el que se define un juego de retornos/shifts por CADA fin de año dentro del horizonte, se ENCADENAN corridas anuales (el cierre de un tramo es el estado inicial del siguiente) y se PERSISTEN los portafolios proyectados a cada corte en una nueva tabla `portafolio_clasificado_proy`. El modo "de punta a punta" actual permanece como comportamiento por defecto y debe ser 100% retrocompatible. Adicionalmente, el input de reservas puede tener periodicidad configurable por tramo (mensual por defecto, o anual con imputación prorrateada), afectando SOLO el mapa de variaciones de reserva y NUNCA el paso mensual del motor.

## Glossary

- **AXIS**: Simulador de proyección de portafolio de renta fija (aplicación completa).
- **Motor_Proyeccion**: Componente `proyectar_portafolio(...)` en `scripts/motor_proyeccion.py` que simula la evolución mensual del portafolio re-valorando cada título con las fórmulas BVC. Recibe un portafolio de títulos, una fecha de inicio, un horizonte en meses y un juego de shifts/sendas.
- **Orquestador_Proyeccion**: Capa de aplicación (en el callback del escenario de `app_dash.py`) que prepara los inputs, invoca el `Motor_Proyeccion`, arma el `df_proy`, lo reclasifica en memoria y lo entrega al `Store_Dashboard`. Es el componente que esta feature extiende para soportar el modo por tramos.
- **Modo_Proyeccion**: Selector de la estrategia de proyección con dos valores: `punta_a_punta` (comportamiento actual, por defecto) y `por_tramos`.
- **Modo_Punta_A_Punta**: Modo de proyección actual: una única corrida del `Motor_Proyeccion` sobre todo el horizonte con un solo juego de shifts.
- **Modo_Por_Tramos**: Modo de proyección que divide el horizonte en tramos anuales y ejecuta una corrida encadenada del `Motor_Proyeccion` por cada tramo.
- **Tramo**: Sub-periodo del horizonte que va desde el inicio del tramo hasta el fin del año calendario correspondiente (o hasta la fecha de fin del horizonte para el último tramo). Ejemplo: un horizonte que termina en 2028 iniciado en 2026 produce los tramos [inicio→fin 2026, inicio 2027→fin 2027, inicio 2028→fecha de fin en 2028].
- **Fecha_Corte_Proyeccion**: Fecha de cierre de un tramo (fin de año o fecha de fin del horizonte para el último tramo). Identifica cada snapshot persistido.
- **Sub_Horizonte**: Número de meses de un tramo, calculado entre el inicio del tramo y su `Fecha_Corte_Proyeccion`.
- **Shifts_Tramo**: Juego de retornos/shifts (shift_cop, shift_uvr, shift_fs, shift_ipc, shift_subyac, shift_tesoros, sliders de curva, TRM, UVR, inflación, repo) aplicable a UN tramo, entendido como el movimiento de ESE periodo (no acumulado punta a punta).
- **Encadenamiento**: Regla por la cual el portafolio proyectado de cierre de un tramo se usa como portafolio de inicio (t0) del tramo siguiente.
- **Portafolio_Clasificado**: Tabla SQLite `portafolio_clasificado` con el portafolio vigente clasificado (asset_class_definitivo, tramo_plazo, clasificación de solvencia, régimen, ramo, compañía, etc.). Es la fuente del portafolio inicial del primer tramo.
- **Portafolio_Clasificado_Proy**: Nueva tabla SQLite `portafolio_clasificado_proy`, con el mismo contenido de un portafolio clasificado más una columna `fecha_corte_proyeccion`, que almacena los snapshots proyectados a cada corte.
- **Df_Proy**: DataFrame en memoria que representa el portafolio proyectado de un tramo (VM del motor actualizado, vencidos eliminados, reinversiones añadidas), reclasificado con `clasificar_portafolio_en_memoria` a la `Fecha_Corte_Proyeccion` del tramo.
- **Clasificacion_En_Memoria**: Función `clasificar_portafolio_en_memoria(df, fecha_corte)` de `clasificar_portafolio.py` que recalcula tramo_plazo y asset_class_definitivo de un DataFrame sin acceso a la DB.
- **Mapa_Variaciones_Reserva**: Estructura `{ramo: {mes_offset: delta_reserva}}` que consume el `Motor_Proyeccion`; los meses sin dato se asumen con Δ=0. Se deriva de `cargar_mapa_reservas`.
- **Periodicidad_Reserva_Tramo**: Parámetro por tramo con dos valores: `mensual` (por defecto) y `anual`, que determina cómo se deriva el `Mapa_Variaciones_Reserva` de ese tramo.
- **Imputacion_Prorrateada**: Regla que reparte linealmente una variación anual de reserva sobre los meses del tramo, en lugar de concentrarla en un solo mes.
- **Store_Dashboard**: Almacenamiento en memoria del dashboard Dash donde vive el resultado de la proyección para render y exportación a Excel.
- **Background_Callback**: Callback de Dash gestionado por `DiskcacheManager` que ejecuta la proyección en un proceso worker con reporte de progreso.
- **Init_Db**: Script `init_db.py`, bootstrap destructivo de un solo uso; NO debe crear las tablas de esta feature.

## Requirements

### Requirement 1: Selección del modo de proyección con retrocompatibilidad

**User Story:** Como analista de la Gerencia Financiera, quiero elegir entre proyección "de punta a punta" y "por tramos" al inicio del dashboard, para conservar el comportamiento actual por defecto y activar el modo por tramos solo cuando lo necesite.

#### Acceptance Criteria

1. THE AXIS SHALL presentar en el dashboard un control de selección de `Modo_Proyeccion` con los valores `punta_a_punta` y `por_tramos`.
2. WHERE el usuario no ha seleccionado un `Modo_Proyeccion`, THE AXIS SHALL usar `punta_a_punta` como valor por defecto.
3. WHERE el `Modo_Proyeccion` es `punta_a_punta`, THE Orquestador_Proyeccion SHALL ejecutar una única corrida del `Motor_Proyeccion` sobre el horizonte completo con un único juego de shifts, produciendo un resultado idéntico al del comportamiento previo a esta feature para los mismos inputs.
4. WHERE el `Modo_Proyeccion` es `por_tramos`, THE Orquestador_Proyeccion SHALL ejecutar el flujo encadenado por tramos definido en el Requirement 4.
5. THE Motor_Proyeccion SHALL conservar su firma y su lógica interna sin modificaciones, de modo que ambos modos lo invoquen por igual.

### Requirement 2: Definición de cortes por fin de año

**User Story:** Como analista, quiero que el horizonte se divida en tramos por fin de año calendario, para asignar un juego de retornos/shifts a cada periodo anual.

#### Acceptance Criteria

1. WHEN el `Modo_Proyeccion` es `por_tramos`, THE Orquestador_Proyeccion SHALL calcular la lista de `Tramo` cuyos cierres (`Fecha_Corte_Proyeccion`) son el último día de cada año calendario contenido en el horizonte.
2. WHEN el horizonte no termina en fin de año, THE Orquestador_Proyeccion SHALL asignar como `Fecha_Corte_Proyeccion` del último `Tramo` la fecha de fin del horizonte.
3. THE Orquestador_Proyeccion SHALL calcular el `Sub_Horizonte` de cada `Tramo` como el número de meses entre el inicio del `Tramo` y su `Fecha_Corte_Proyeccion`.
4. WHEN el horizonte completo está contenido dentro de un único año calendario, THE Orquestador_Proyeccion SHALL producir exactamente un `Tramo` cuyo `Sub_Horizonte` equivale al horizonte completo.
5. IF el horizonte calculado es menor a un mes, THEN THE Orquestador_Proyeccion SHALL rechazar la ejecución del modo por tramos y reportar un mensaje de error verificable al usuario.

### Requirement 3: Inputs de retornos/shifts por tramo

**User Story:** Como analista, quiero definir un juego de retornos/shifts por cada tramo, para que cada periodo anual refleje su propio movimiento de mercado en vez de un único movimiento acumulado.

#### Acceptance Criteria

1. WHERE el `Modo_Proyeccion` es `por_tramos`, THE AXIS SHALL permitir capturar un juego de `Shifts_Tramo` por cada `Tramo` del horizonte.
2. THE Orquestador_Proyeccion SHALL interpretar los `Shifts_Tramo` como el movimiento aplicable únicamente al periodo de ese `Tramo`, no como un valor acumulado desde el inicio del horizonte.
3. WHEN el Orquestador_Proyeccion invoca el `Motor_Proyeccion` para un `Tramo`, THE Orquestador_Proyeccion SHALL pasar los `Shifts_Tramo` de ese `Tramo` junto con el `Sub_Horizonte` del `Tramo`, de modo que el reparto lineal interno del motor aplique el shift del tramo sobre los meses del tramo.
4. WHERE el usuario no captura `Shifts_Tramo` para un `Tramo`, THE AXIS SHALL aplicar shifts en cero para ese `Tramo`.

### Requirement 4: Encadenamiento de tramos

**User Story:** Como analista, quiero que el portafolio de cierre de un tramo sea el punto de partida del siguiente, para que la proyección anual acumule correctamente el estado del portafolio a lo largo del horizonte.

#### Acceptance Criteria

1. WHEN el Orquestador_Proyeccion procesa el primer `Tramo`, THE Orquestador_Proyeccion SHALL cargar como portafolio de inicio el `Portafolio_Clasificado` real vigente.
2. WHEN el Orquestador_Proyeccion procesa un `Tramo` posterior al primero, THE Orquestador_Proyeccion SHALL cargar como portafolio de inicio el snapshot de cierre del `Tramo` inmediatamente anterior almacenado en `Portafolio_Clasificado_Proy`.
3. WHEN el Motor_Proyeccion termina la corrida de un `Tramo`, THE Orquestador_Proyeccion SHALL armar el `Df_Proy` del `Tramo` con el VM actualizado por el motor, los títulos vencidos eliminados y las reinversiones añadidas.
4. THE Orquestador_Proyeccion SHALL reclasificar el `Df_Proy` de cada `Tramo` mediante `Clasificacion_En_Memoria` usando la `Fecha_Corte_Proyeccion` de ese `Tramo`.
5. THE Orquestador_Proyeccion SHALL procesar los `Tramo` en orden cronológico ascendente por `Fecha_Corte_Proyeccion`.

### Requirement 5: Persistencia de snapshots por corte

**User Story:** Como analista, quiero que cada portafolio proyectado a un corte anual quede persistido con su fecha de corte, para reutilizarlo como entrada del tramo siguiente y para analizarlo después.

#### Acceptance Criteria

1. THE AXIS SHALL crear la tabla `Portafolio_Clasificado_Proy` de forma idempotente mediante `CREATE TABLE IF NOT EXISTS` desde la capa de aplicación.
2. THE AXIS SHALL abstenerse de crear, sembrar o modificar la tabla `Portafolio_Clasificado_Proy` dentro de `Init_Db`.
3. THE Portafolio_Clasificado_Proy SHALL incluir una columna `fecha_corte_proyeccion` que identifica el corte al que corresponde cada fila, además de las columnas equivalentes a `Portafolio_Clasificado`.
4. WHEN el Orquestador_Proyeccion completa el `Df_Proy` de un `Tramo`, THE Orquestador_Proyeccion SHALL persistir ese `Df_Proy` en `Portafolio_Clasificado_Proy` con su `Fecha_Corte_Proyeccion` correspondiente.
5. THE AXIS SHALL ejecutar todas las operaciones de lectura y escritura sobre `Portafolio_Clasificado_Proy` mediante consultas parametrizadas, sin interpolar entrada en el texto SQL.
6. WHEN el Orquestador_Proyeccion inicia una nueva corrida en `Modo_Por_Tramos`, THE Orquestador_Proyeccion SHALL reemplazar los snapshots de una corrida previa antes de escribir los nuevos, de modo que `Portafolio_Clasificado_Proy` contenga solo los cortes de la corrida vigente.

### Requirement 6: Snapshot del portafolio a fin de horizonte para análisis

**User Story:** Como analista, quiero disponer del portafolio clasificado al final del horizonte, para analizar cómo se movieron el régimen, la solvencia y los asset classes.

#### Acceptance Criteria

1. WHEN el Orquestador_Proyeccion completa el último `Tramo`, THE Orquestador_Proyeccion SHALL exponer el `Df_Proy` del último `Tramo` al `Store_Dashboard` como portafolio proyectado a fin de horizonte.
2. THE Df_Proy del último `Tramo` SHALL contener las columnas de clasificación de régimen, solvencia y asset_class necesarias para el análisis, equivalentes a las de `Portafolio_Clasificado`.
3. THE Portafolio_Clasificado_Proy SHALL conservar el snapshot correspondiente a la `Fecha_Corte_Proyeccion` de fin de horizonte una vez finalizada la corrida.

### Requirement 7: Periodicidad de reservas por tramo

**User Story:** Como analista, quiero definir la periodicidad del input de reservas por tramo (mensual por defecto, anual cuando solo hay dato de fin de periodo), para reflejar que a partir de cierto año solo se dispone de la reserva anual.

#### Acceptance Criteria

1. WHERE el `Modo_Proyeccion` es `por_tramos`, THE AXIS SHALL permitir asignar una `Periodicidad_Reserva_Tramo` a cada `Tramo`, con los valores `mensual` y `anual`.
2. WHERE no se especifica una `Periodicidad_Reserva_Tramo` para un `Tramo`, THE AXIS SHALL usar `mensual` como valor por defecto.
3. WHERE la `Periodicidad_Reserva_Tramo` de un `Tramo` es `mensual`, THE Orquestador_Proyeccion SHALL derivar el `Mapa_Variaciones_Reserva` de ese `Tramo` con el mismo comportamiento del modo actual.
4. WHERE la `Periodicidad_Reserva_Tramo` de un `Tramo` es `anual`, THE Orquestador_Proyeccion SHALL derivar el `Mapa_Variaciones_Reserva` de ese `Tramo` aplicando `Imputacion_Prorrateada` de la variación anual de reserva sobre los meses del `Tramo`.
5. THE Periodicidad_Reserva_Tramo SHALL afectar únicamente la derivación del `Mapa_Variaciones_Reserva` y no cualquier otro input del `Tramo`.

### Requirement 8: Imputación prorrateada de la variación anual de reserva

**User Story:** Como analista, quiero que la variación anual de reserva se reparta linealmente entre los meses del tramo, para evitar concentrar todo el efecto en un solo mes y distorsionar la senda de compras/ventas.

#### Acceptance Criteria

1. WHEN la `Periodicidad_Reserva_Tramo` de un `Tramo` es `anual`, THE Orquestador_Proyeccion SHALL repartir la variación anual de reserva de cada ramo en partes iguales entre los meses del `Sub_Horizonte` del `Tramo`.
2. THE Orquestador_Proyeccion SHALL producir un `Mapa_Variaciones_Reserva` cuya suma de deltas prorrateados por ramo, sobre los meses del `Tramo`, equivale a la variación anual de reserva de ese ramo dentro de una tolerancia de redondeo definida.
3. THE Orquestador_Proyeccion SHALL entregar el `Mapa_Variaciones_Reserva` prorrateado en el formato `{ramo: {mes_offset: delta_reserva}}` que consume el `Motor_Proyeccion`.
4. WHERE un mes del `Tramo` no recibe parte prorrateada por ausencia de dato de ese ramo, THE Orquestador_Proyeccion SHALL omitir ese mes del `Mapa_Variaciones_Reserva`, apoyándose en que el motor asume Δ=0 para los meses ausentes.

### Requirement 9: Preservación del paso mensual del motor

**User Story:** Como analista, quiero que el motor siga corriendo en paso mensual bajo cualquier periodicidad de reserva, para que cupones, vencimientos, reinversiones y re-valoración se calculen en las fechas reales de cada título.

#### Acceptance Criteria

1. THE Motor_Proyeccion SHALL ejecutar su loop en paso mensual en ambos modos de proyección y bajo cualquier `Periodicidad_Reserva_Tramo`.
2. THE Motor_Proyeccion SHALL conservar su detección de cupones, procesamiento de vencimientos, reinversión y re-valoración por título sin alteración por causa de la `Periodicidad_Reserva_Tramo`.
3. WHEN la `Periodicidad_Reserva_Tramo` de un `Tramo` es `anual`, THE Orquestador_Proyeccion SHALL entregar al `Motor_Proyeccion` un `Mapa_Variaciones_Reserva` mensual derivado por prorrateo, sin cambiar el paso temporal del motor.

### Requirement 10: Ejecución en background y progreso

**User Story:** Como analista, quiero que la proyección por tramos corra en segundo plano con indicación de progreso, para no bloquear el navegador durante horizontes largos con varios tramos encadenados.

#### Acceptance Criteria

1. THE Orquestador_Proyeccion SHALL ejecutar la proyección por tramos dentro del `Background_Callback` gestionado por `DiskcacheManager`.
2. WHILE una proyección por tramos está en curso, THE AXIS SHALL reportar progreso al usuario indicando el `Tramo` en proceso respecto del total de tramos.
3. WHEN todos los `Tramo` han sido procesados, THE Background_Callback SHALL entregar el resultado al `Store_Dashboard`.

### Requirement 11: Manejo de errores por tramo

**User Story:** Como analista, quiero saber con claridad si un tramo falla y en cuál, para diagnosticar el problema sin recibir resultados parciales silenciosos.

#### Acceptance Criteria

1. IF la corrida de un `Tramo` falla, THEN THE Orquestador_Proyeccion SHALL detener el encadenamiento en ese `Tramo` y reportar al usuario un mensaje que identifica la `Fecha_Corte_Proyeccion` del `Tramo` fallido.
2. IF la carga del portafolio de inicio de un `Tramo` posterior al primero no encuentra el snapshot del `Tramo` anterior en `Portafolio_Clasificado_Proy`, THEN THE Orquestador_Proyeccion SHALL detener la corrida y reportar un mensaje de error verificable.
3. IF un `Tramo` falla, THEN THE Orquestador_Proyeccion SHALL abstenerse de exponer un resultado de fin de horizonte incompleto al `Store_Dashboard`.
4. WHEN el Orquestador_Proyeccion reporta un error de un `Tramo`, THE Orquestador_Proyeccion SHALL emitir un mensaje genérico al usuario sin exponer datos sensibles del portafolio ni trazas internas.

### Requirement 12: Seguridad de acceso a datos

**User Story:** Como responsable técnico, quiero que todo el acceso a datos de la feature sea seguro, para prevenir inyección SQL y tratamiento indebido de entrada no confiable.

#### Acceptance Criteria

1. THE AXIS SHALL ejecutar todas las consultas SQL de la feature mediante sentencias parametrizadas, sin concatenar ni interpolar entrada en el texto SQL.
2. THE AXIS SHALL restringir las columnas leídas y escritas de las tablas de la feature a una allowlist fija de nombres controlados por el código.
3. THE AXIS SHALL tratar cada valor de entrada (selección de modo, shifts por tramo, periodicidad por tramo y filas leídas de la base) como no confiable y validarlo por tipo y rango antes de usarlo.
4. IF un valor de entrada de la feature no supera la validación, THEN THE AXIS SHALL rechazar ese valor y registrar una advertencia genérica sin volcar datos sensibles.
5. THE AXIS SHALL abstenerse de registrar en logs, mensajes de error o trazas cualquier dato sensible del portafolio.

### Requirement 13: Gestión de dependencias

**User Story:** Como responsable técnico, quiero que la feature no introduzca dependencias sin fijar versión, para mantener builds reproducibles y auditables.

#### Acceptance Criteria

1. WHERE la feature requiere una dependencia nueva, THE AXIS SHALL declararla con versión exacta fijada, sin rangos abiertos (`^`, `~`, `*`, `latest`).
2. THE AXIS SHALL conservar el pineo exacto de la suite de pruebas existente, incluida `hypothesis==6.140.3`.
3. THE AXIS SHALL reutilizar las bibliotecas ya aprobadas en el proyecto en lugar de introducir dependencias equivalentes nuevas cuando exista una alternativa ya presente.
