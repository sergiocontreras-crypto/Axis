# Requirements Document

## Introduction

Esta funcionalidad rehace la forma en que AXIS lee y persiste el insumo de "variación de reservas" que alimenta al motor de proyección (motor de proyección) para dirigir la compra/venta mensual de renta fija por ramo.

El lector actual (`scripts/compras_ventas_reservas.py::derivar_variaciones_reservas`) espera encabezados con NOMBRES de mes (busca el texto literal "ENERO") y exige exactamente 12 columnas contiguas Enero..Diciembre. Sin embargo, el archivo real de negocio (`Inputs_Reservas e interes a la reserva.xlsx`) trae encabezados de FECHA (datetime) con un patrón MIXTO: todos los meses del año en curso y luego solo el mes de diciembre de los años siguientes (p. ej. 2026-06, 2026-07, ..., 2026-12, 2027-12, 2028-12). Con este archivo el lector actual falla.

La funcionalidad estandariza la lectura sobre encabezados de FECHA, clasifica cada salto entre columnas consecutivas por diferencia de meses (1 = salto mensual, 12 = salto anual diciembre-a-diciembre), persiste la variación de forma VERSIONADA usando `mes` como fecha ABSOLUTA (YYYY-MM) anclada a la fecha de inicio del corte, conecta los saltos anuales derivados a la entrada `variacion_anual_por_tramo` del motor por tramos, y muestra en el dashboard alertas de error ESPECÍFICAS y accionables que bloquean la corrida cuando el formato del archivo es inválido.

El alcance es quirúrgico: reutiliza la maquinaria probada existente (`prorratear_reservas_anual`, `calcular_tramos`, los dos diccionarios de mapeo, y las rutas mensual/tramos del motor), y conserva la estructura de dos hojas ("Saldo Reservas" / "Interés a la Reserva") y las tablas de mapeo.

**Extensión — coexistencia de dos disposiciones de entrada.** El insumo real también puede llegar en una disposición DISTINTA que debe COEXISTIR con la disposición mensual ya implementada. Ambas usan encabezados de FECHA. La disposición existente (`Layout_Mensual`) se conserva tal cual. La nueva disposición (`Layout_Por_Tramo`) representa, tras un punto de partida, una única columna por fecha destino que corresponde a un `Periodo_Acumulado` a prorratear entre N meses (no un solo mes). El tamaño del primer tramo parcial se determina por la `Fecha_Corte` del dashboard (no solo por la diferencia de meses del archivo), por lo que el `Lector_Reservas` ahora depende de recibir la `Fecha_Corte`. En el `Layout_Por_Tramo` cada hoja tiene semántica distinta: la hoja de saldo trae un NIVEL de fin de periodo que se prorratea de forma UNIFORME, mientras que la hoja de interés trae una variación ya ACUMULADA que se prorratea de forma LINEAL cuya DIRECCIÓN (creciente o decreciente) depende del signo de la variación de saldo del periodo. La variación de reserva mensual que consume el motor sigue siendo Δsaldo mensual − Δinterés mensual. Esta extensión no elimina ni debilita ningún comportamiento existente.

## Glossary

- **Lector_Reservas**: Componente responsable de leer el workbook de reservas de dos hojas y derivar la variación de reserva por ramo. Corresponde a la lógica de `scripts/compras_ventas_reservas.py::derivar_variaciones_reservas`.
- **Motor_Proyeccion**: Motor que ejecuta la proyección del portafolio mes a mes y consume la variación de reservas (`scripts/motor_proyeccion.py`).
- **Orquestador**: Capa de aplicación que coordina la corrida de proyección y arma los parámetros por tramo (`app_dash.py`).
- **Dashboard**: Interfaz Dash de AXIS que recibe la carga del archivo de reservas y muestra los mensajes de resultado (`app_dash.py`, callback `cargar_excel_reservas`).
- **Persistidor_Reservas**: Componente que valida y guarda la variación derivada en la base SQLite (`persistir_input_reservas`).
- **Cargador_Mapa_Reservas**: Componente que lee la variación persistida y la entrega al motor (`cargar_mapa_reservas`).
- **Variación_Reserva**: Cambio de la reserva técnica de un ramo entre dos puntos de tiempo, calculado como Δsaldo − Δinterés (variación de saldo menos variación de interés a la reserva).
- **Encabezado_Fecha**: Celda de encabezado de columna cuyo valor es una fecha (tipo datetime), que identifica el mes/año al que corresponde la columna de datos.
- **Salto_Mensual**: Salto entre dos columnas consecutivas cuya diferencia es de 1 mes; produce una variación mensual.
- **Salto_Anual**: Salto entre dos columnas consecutivas cuya diferencia es de 12 meses (diciembre-a-diciembre); produce una variación anual.
- **Mes_Absoluto**: Representación del mes como fecha calendario absoluta en formato "YYYY-MM" (por ejemplo "2026-07"), a diferencia de un offset relativo sin ancla.
- **Mes_Caído**: Convención por la cual, para el mes de proyección t, el motor usa la variación de reserva correspondiente al mes t-1.
- **Tramo**: Sub-periodo del horizonte de proyección delimitado por fin de año calendario, según `calcular_tramos`. Un horizonte contenido en un único año calendario produce exactamente un tramo.
- **Versión_Carga**: Registro identificable de una carga del archivo de reservas, con marca temporal, preservado para auditoría en lugar de ser sobrescrito.
- **Fecha_Inicio_Proyeccion**: Fecha de inicio de la proyección (corte del portafolio) a la que se ancla el Mes_Absoluto al momento de leer la versión relevante.
- **Diccionarios_Mapeo**: Los dos diccionarios de homologación etiqueta→ramo de portafolio (`mapeo_ramos_saldo_reservas`, `mapeo_ramos_interes_reservas`) almacenados en SQLite.
- **Prorrateo_Anual**: Reparto de una variación anual entre los meses de un tramo, realizado por `prorratear_reservas_anual`, con invariante de que la suma de los deltas prorrateados es igual a la variación anual (residuo en el último mes).
- **Layout_Mensual**: Disposición de entrada EXISTENTE en la que las columnas de datos son fechas consecutivas separadas 1 mes (niveles), con la variación derivada por resta entre columnas consecutivas, más saltos anuales diciembre-a-diciembre (12 meses). Es el comportamiento ya implementado y se conserva sin cambios.
- **Layout_Por_Tramo**: Disposición de entrada NUEVA en la que, tras un punto de partida, una única columna por fecha destino representa un `Periodo_Acumulado` a prorratear entre varios meses, en lugar de un solo mes.
- **Periodo_Acumulado**: Intervalo de N meses representado por una única columna destino en el `Layout_Por_Tramo`, cuyo valor debe repartirse entre los N meses del intervalo.
- **Fecha_Corte**: Fecha de corte del dashboard (`Fecha_Inicio_Proyeccion`, fecha de corte de la proyección) que define el tamaño del primer tramo parcial en el `Layout_Por_Tramo`. Es una entrada que el `Lector_Reservas` requiere para dimensionar dicho tramo parcial.
- **Mes_Corte**: Mes calendario de la `Fecha_Corte` (mes `t`). Es la ÚLTIMA columna mensual presente en el `Layout_Por_Tramo` antes del salto a la primera columna de diciembre destino (por ejemplo, con `Fecha_Corte` 31-jul-2026, el Mes_Corte es julio-2026). El Mes_Corte NO se proyecta: constituye el NIVEL BASE (saldo e interés de partida) del `Tramo_Parcial`.
- **Tramo_Parcial**: Primer tramo del `Layout_Por_Tramo`, que abarca los meses POSTERIORES al Mes_Corte (`t`) hasta la primera columna de diciembre destino inclusive, con una duración de N meses. El nivel base del tramo es el del Mes_Corte (`t`), que no se prorratea. Por ejemplo, con `Fecha_Corte` 31-jul-2026 (Mes_Corte julio-2026) y primera columna destino dic-2026, N = 5 meses: ago, sep, oct, nov, dic de 2026 (base = julio-2026).
- **Tramo_Anual**: Tramo del `Layout_Por_Tramo` de duración 12 meses (diciembre-a-diciembre) posterior al `Tramo_Parcial`.
- **Prorrateo_Uniforme**: Reparto de una variación de periodo (delta de nivel de saldo) de forma uniforme entre los N meses del tramo, aplicado al lado de saldo; corresponde conceptualmente a `prorratear_reservas_anual`. La suma de los deltas mensuales es igual al delta de nivel del periodo.
- **Prorrateo_Lineal_Direccional**: Reparto de una variación acumulada de periodo (interés acumulado del periodo) de forma lineal entre los N meses del tramo, cuya DIRECCIÓN depende del signo de la variación de saldo del periodo (Δsaldo_periodo). Cuando Δsaldo_periodo ≥ 0, el reparto es CRECIENTE: el mes de posición `i` (i = 1..N) recibe `Interes_Acumulado_Periodo * i / (1 + 2 + ... + N)` (pesos 1, 2, ..., N). Cuando Δsaldo_periodo < 0, el reparto es DECRECIENTE: el mes de posición `i` recibe `Interes_Acumulado_Periodo * (N − i + 1) / (1 + 2 + ... + N)` (pesos N, ..., 2, 1). En ambos casos la suma de los deltas mensuales es igual al Interes_Acumulado_Periodo (se preserva el signo del interés acumulado; solo se invierte el ORDEN de los pesos). Justificación económica: el interés sigue al saldo de la reserva; si el saldo del periodo decrece, la contribución mensual de interés decrece de forma lineal.
- **Nivel_Saldo**: Valor de saldo al final del periodo en el `Layout_Por_Tramo` (por ejemplo, el saldo en dic-2026), a partir del cual se obtiene la variación de periodo por resta contra el punto de partida.
- **Interes_Acumulado_Periodo**: Valor de la columna destino de la hoja de interés en el `Layout_Por_Tramo`, que representa la variación de interés ya acumulada para todo el periodo (no un nivel).

## Requirements

### Requirement 1: Lectura por encabezados de fecha

**User Story:** Como analista financiero, quiero que el lector reconozca las columnas del insumo por sus encabezados de fecha, para que el archivo real de negocio (con fechas datetime en vez de nombres de mes) pueda procesarse sin fallar.

#### Acceptance Criteria

1. WHEN se procesa un workbook de reservas, THE Lector_Reservas SHALL identificar las columnas de datos por encabezados de tipo fecha (datetime) en cada una de las dos hojas ("Saldo Reservas" e "Interés a la Reserva").
2. WHERE una columna tiene un Encabezado_Fecha válido, THE Lector_Reservas SHALL derivar el mes y el año de esa columna a partir del valor de fecha del encabezado.
3. THE Lector_Reservas SHALL ordenar las columnas de datos por su Encabezado_Fecha en orden cronológico ascendente antes de derivar variaciones.
4. THE Lector_Reservas SHALL procesar el número de columnas de fecha presente en el archivo sin exigir una cantidad fija de 12 columnas.

### Requirement 2: Rechazo de formatos de encabezado inválidos

**User Story:** Como analista financiero, quiero que el sistema estandarice sobre encabezados de fecha y rechace cualquier otro formato, para que un archivo mal formado no produzca resultados silenciosamente incorrectos.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL aceptar únicamente columnas de datos identificadas por Encabezado_Fecha.
2. IF una hoja de datos presenta encabezados con nombres de mes (por ejemplo "ENERO") en lugar de fechas, THEN THE Lector_Reservas SHALL señalar un error de formato de encabezado y no derivar variaciones.
3. IF una hoja de datos no contiene ninguna columna con Encabezado_Fecha reconocible, THEN THE Lector_Reservas SHALL señalar un error de formato de encabezado y no derivar variaciones.
4. IF falta alguna de las dos hojas requeridas ("Saldo Reservas" o "Interés a la Reserva"), THEN THE Lector_Reservas SHALL señalar un error de hojas faltantes distinto del error de formato de encabezado.

### Requirement 3: Clasificación de saltos entre columnas consecutivas

**User Story:** Como analista financiero, quiero que el sistema distinga automáticamente entre pasos mensuales y saltos anuales según las fechas reales, para que se deriven correctamente tanto las variaciones mensuales del año en curso como las variaciones diciembre-a-diciembre de los años siguientes.

#### Acceptance Criteria

1. WHEN dos columnas consecutivas (ordenadas cronológicamente) difieren en 1 mes, THE Lector_Reservas SHALL clasificar el salto como Salto_Mensual y derivar una variación mensual.
2. WHEN dos columnas consecutivas (ordenadas cronológicamente) difieren en 12 meses, THE Lector_Reservas SHALL clasificar el salto como Salto_Anual y derivar una variación anual diciembre-a-diciembre.
3. WHERE el archivo contiene primero los meses del año en curso y luego solo diciembre de los años siguientes, THE Lector_Reservas SHALL derivar variaciones mensuales para el tramo del año en curso y variaciones anuales para los tramos de años siguientes.
4. IF dos columnas consecutivas difieren en un número de meses distinto de 1 y de 12, THEN THE Lector_Reservas SHALL señalar un error de formato de encabezado y no derivar variaciones.
5. THE Lector_Reservas SHALL calcular cada Variación_Reserva como Δsaldo − Δinterés respecto de la columna cronológicamente anterior, aplicando los Diccionarios_Mapeo existentes.

### Requirement 4: Mes de inicio dinámico

**User Story:** Como analista financiero, quiero que el lector funcione tanto si el archivo trae Enero–Diciembre como si empieza en el mes previo al corte del portafolio, para que corridas iniciadas en meses distintos (por ejemplo junio frente a enero) se lean correctamente sin cambios manuales.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL determinar el primer mes de datos a partir del Encabezado_Fecha más temprano presente en el archivo.
2. WHERE el archivo comienza en enero, THE Lector_Reservas SHALL derivar variaciones a partir de la serie disponible sin requerir configuración adicional.
3. WHERE el archivo comienza en un mes distinto de enero, THE Lector_Reservas SHALL derivar variaciones a partir de la serie disponible sin requerir configuración adicional.
4. THE Lector_Reservas SHALL derivar el eje temporal exclusivamente de las fechas presentes en los encabezados, sin asumir un mes de inicio fijo.

### Requirement 5: Mes persistido como fecha absoluta

**User Story:** Como analista financiero, quiero que la variación se persista con el mes como fecha absoluta, para que corridas iniciadas en meses distintos no se confundan y los datos sean auditables sin ambigüedad.

#### Acceptance Criteria

1. WHEN el Persistidor_Reservas guarda una variación, THE Persistidor_Reservas SHALL almacenar el campo de mes como Mes_Absoluto en formato "YYYY-MM".
2. THE Persistidor_Reservas SHALL derivar cada Mes_Absoluto del Encabezado_Fecha de la columna de la que proviene la variación.
3. WHERE existen varias columnas de diciembre de años distintos (por ejemplo 2026-12, 2027-12, 2028-12), THE Persistidor_Reservas SHALL almacenar cada una como un Mes_Absoluto distinto ("2026-12", "2027-12", "2028-12").
4. THE Persistidor_Reservas SHALL rechazar valores de mes que no cumplan el formato "YYYY-MM" con año de cuatro dígitos y mes entre 01 y 12.

### Requirement 6: Persistencia versionada con historial

**User Story:** Como analista financiero responsable de auditoría, quiero que cada carga del archivo se conserve como una versión, para que las cargas anteriores no se pierdan y pueda auditar qué insumo alimentó cada corrida.

#### Acceptance Criteria

1. WHEN el Persistidor_Reservas guarda una carga, THE Persistidor_Reservas SHALL crear una Versión_Carga nueva con una marca temporal de carga en lugar de borrar las cargas anteriores.
2. THE Persistidor_Reservas SHALL preservar las Versiones_Carga anteriores tras guardar una nueva carga.
3. WHEN el Cargador_Mapa_Reservas provee la variación al Motor_Proyeccion, THE Cargador_Mapa_Reservas SHALL seleccionar la Versión_Carga más reciente disponible.
4. THE Persistidor_Reservas SHALL registrar la marca temporal de cada Versión_Carga con precisión suficiente para ordenar cronológicamente las cargas.

### Requirement 7: Lectura anclada a la fecha de inicio del corte

**User Story:** Como analista financiero, quiero que el motor lea la variación relevante anclando el mes absoluto a la fecha de inicio de la proyección, para que las variaciones se apliquen al mes calendario correcto de la corrida.

#### Acceptance Criteria

1. WHEN el Cargador_Mapa_Reservas entrega la variación al Motor_Proyeccion, THE Cargador_Mapa_Reservas SHALL anclar cada Mes_Absoluto a la Fecha_Inicio_Proyeccion para ubicar la variación en el mes de proyección correspondiente.
2. THE Cargador_Mapa_Reservas SHALL aplicar la convención de Mes_Caído, entregando para el mes de proyección t la variación correspondiente al mes t-1.
3. IF un mes de proyección no tiene una Variación_Reserva persistida para un ramo, THEN THE Cargador_Mapa_Reservas SHALL omitir ese ramo/mes, de modo que el Motor_Proyeccion asuma Δ=0.

### Requirement 8: Conexión de la variación anual a los tramos

**User Story:** Como analista financiero, quiero que los saltos anuales derivados alimenten la entrada de variación anual por tramo del motor, para que la proyección multianual por tramos use las variaciones diciembre-a-diciembre reales del insumo.

#### Acceptance Criteria

1. WHEN el Orquestador prepara una corrida por tramos, THE Orquestador SHALL construir la variación anual por tramo a partir de los Saltos_Anuales derivados por el Lector_Reservas.
2. WHERE un tramo corresponde a un año con un Salto_Anual diciembre-a-diciembre disponible, THE Orquestador SHALL asignar la variación anual de ese año al tramo correspondiente.
3. WHEN un tramo recibe una variación anual, THE Orquestador SHALL repartirla entre los meses del tramo mediante `prorratear_reservas_anual`, conservando la invariante de que la suma de los deltas prorrateados es igual a la variación anual.
4. THE Orquestador SHALL reutilizar `calcular_tramos` para delimitar los tramos, sin fijar de forma rígida el número de tramos.

### Requirement 9: Número de tramos dinámico por Decembers y fecha fin

**User Story:** Como analista financiero, quiero que el número de tramos siga dinámicamente la cantidad de diciembres del insumo y la fecha fin de la proyección, para que proyecciones de distinta duración (por ejemplo 5 o 7 años) funcionen sin cambios de código.

#### Acceptance Criteria

1. THE Orquestador SHALL derivar el número de tramos anuales del horizonte de proyección mediante `calcular_tramos`, en función de la Fecha_Inicio_Proyeccion y la fecha fin.
2. WHERE el horizonte de proyección está contenido en un único año calendario, THE Orquestador SHALL producir exactamente un tramo mensual (comportamiento clásico de 12 meses sin tramos anuales).
3. WHERE el horizonte abarca varios años calendario, THE Orquestador SHALL producir un tramo por cada año, tratando cada diciembre de años siguientes como un corte anual distinto.
4. THE Lector_Reservas SHALL tratar cada columna de diciembre de años distintos como una clave de Mes_Absoluto distinta, de modo que múltiples diciembres no se confundan entre sí.

### Requirement 10: Política de años sin columna en el insumo

**User Story:** Como analista financiero, quiero una política clara cuando la proyección abarca años para los que el insumo no tiene columna, para que la corrida no falle y quede claro qué años usan variación cero.

#### Acceptance Criteria

1. IF la proyección abarca un año para el que el insumo no tiene una columna de fecha, THEN THE Motor_Proyeccion SHALL asumir Variación_Reserva igual a 0 para los ramos/meses de ese año.
2. WHEN existan años del horizonte sin columna en el insumo, THE Dashboard SHALL mostrar una nota informativa que indique que dichos años usan variación cero.
3. THE nota informativa SHALL identificarse como informativa y no bloquear la corrida.

### Requirement 11: Alertas de error específicas que bloquean la corrida

**User Story:** Como analista financiero, quiero que el dashboard muestre alertas específicas y accionables cuando el archivo tiene un formato inválido, para saber exactamente qué corregir y para que un archivo mal formado no avance a la proyección.

#### Acceptance Criteria

1. IF el Lector_Reservas señala un error de formato de encabezado, THEN THE Dashboard SHALL mostrar una alerta bloqueante que indique que el archivo debe traer encabezados de fecha y no nombres de mes ni otros formatos.
2. IF el Lector_Reservas señala un error de hojas faltantes, THEN THE Dashboard SHALL mostrar una alerta bloqueante que identifique la ausencia de las hojas "Saldo Reservas" o "Interés a la Reserva" como causa, distinta del error de formato de encabezado.
3. WHEN el Dashboard muestra una alerta bloqueante por formato inválido, THE Dashboard SHALL impedir que la corrida de proyección proceda con ese archivo.
4. THE Dashboard SHALL diferenciar el mensaje según la causa real del error (formato de encabezado, hojas faltantes, error de lectura del archivo, error de persistencia) en lugar de mostrar un único mensaje genérico.
5. WHEN la carga y la persistencia se completan correctamente, THE Dashboard SHALL mostrar un mensaje de confirmación con el número de filas de variación persistidas.

### Requirement 12: Reutilización de la maquinaria existente

**User Story:** Como mantenedor de AXIS, quiero que la funcionalidad reutilice los componentes ya probados, para minimizar el riesgo de regresión y mantener consistencia con la arquitectura existente.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL conservar la estructura de dos hojas ("Saldo Reservas" e "Interés a la Reserva") y las tablas de Diccionarios_Mapeo existentes.
2. THE Lector_Reservas SHALL aplicar los Diccionarios_Mapeo existentes (`mapeo_ramos_saldo_reservas`, `mapeo_ramos_interes_reservas`) para homologar etiquetas del insumo a ramos de portafolio.
3. THE Orquestador SHALL reutilizar `prorratear_reservas_anual` para el reparto de variaciones anuales y `calcular_tramos` para el troceo del horizonte, sin reimplementar esa lógica.
4. THE Motor_Proyeccion SHALL conservar sus rutas de consumo mensual (Mes_Caído) y por tramos (Prorrateo_Anual) existentes.

### Requirement 13: Validación de entrada no confiable

**User Story:** Como responsable de seguridad, quiero que todo el contenido del archivo se trate como entrada no confiable, para que valores mal formados no corrompan la persistencia ni filtren datos sensibles en los logs.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL coercer cada valor de saldo e interés a numérico finito y tratar los valores no numéricos o vacíos como 0.
2. THE Lector_Reservas SHALL normalizar cada etiqueta de ramo (recorte de espacios y mayúsculas) antes de aplicar los Diccionarios_Mapeo.
3. THE Persistidor_Reservas SHALL usar consultas parametrizadas para todas las escrituras, sin interpolar contenido del archivo en las sentencias SQL.
4. IF una fila del archivo contiene un valor no coercionable a numérico finito, THEN THE Persistidor_Reservas SHALL descartar esa fila registrando una advertencia genérica que no incluya el contenido de la fila.

### Requirement 14: Coexistencia de las dos disposiciones de entrada

**User Story:** Como analista financiero, quiero que el lector reconozca y procese tanto la disposición mensual existente como la nueva disposición por tramos, para que ambos formatos reales del insumo se lean sin cambios manuales y sin romper las corridas actuales.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL soportar el Layout_Mensual y el Layout_Por_Tramo como disposiciones de entrada válidas, ambas identificadas por columnas de Encabezado_Fecha.
2. WHERE el insumo corresponde al Layout_Mensual, THE Lector_Reservas SHALL derivar variaciones con el comportamiento existente (Salto_Mensual por diferencia de 1 mes y Salto_Anual diciembre-a-diciembre por diferencia de 12 meses) sin alteración.
3. THE Lector_Reservas SHALL determinar la disposición aplicable comparando cada salto entre columnas consecutivas contra la estructura de tramos definida por la Fecha_Corte, según se especifica en el Requirement 15.
4. THE Lector_Reservas SHALL conservar la estructura de dos hojas ("Saldo Reservas" e "Interés a la Reserva") y los Diccionarios_Mapeo existentes para ambas disposiciones.

### Requirement 15: Detección de tramos por la fecha de corte del dashboard

**User Story:** Como analista financiero, quiero que el tamaño de cada tramo se defina por la fecha de corte del dashboard y no solo por la diferencia de meses del archivo, para que el primer tramo parcial y los tramos anuales se dimensionen correctamente según la corrida real.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL recibir la Fecha_Corte como entrada para dimensionar el Tramo_Parcial del Layout_Por_Tramo.
2. THE Lector_Reservas SHALL tomar el Mes_Corte (el mes de la Fecha_Corte, `t`) como NIVEL BASE del Tramo_Parcial, sin proyectar ni prorratear el Mes_Corte.
3. WHERE el Layout_Por_Tramo contiene una primera columna de diciembre destino, THE Lector_Reservas SHALL definir el Tramo_Parcial como el intervalo de los meses posteriores al Mes_Corte hasta esa columna de diciembre inclusive, con duración de N meses.
4. WHEN la Fecha_Corte es 31-jul-2026 (Mes_Corte julio-2026) y la primera columna destino es diciembre de 2026, THE Lector_Reservas SHALL dimensionar el Tramo_Parcial en 5 meses (ago, sep, oct, nov, dic de 2026), con nivel base en julio-2026.
5. WHERE existen columnas de diciembre posteriores al Tramo_Parcial, THE Lector_Reservas SHALL definir cada intervalo diciembre-a-diciembre como un Tramo_Anual de 12 meses.
6. WHILE se procesa un salto fuera del Tramo_Parcial, THE Lector_Reservas SHALL aceptar únicamente saltos de 1 mes (Salto_Mensual) o de 12 meses (Tramo_Anual).
7. IF un salto fuera del Tramo_Parcial difiere en un número de meses distinto de 1 y de 12, THEN THE Lector_Reservas SHALL señalar un error de formato de encabezado y no derivar variaciones.
8. WHERE el Layout_Por_Tramo contiene columnas mensuales anteriores al Mes_Corte que quedan fuera del horizonte de proyección, THE Lector_Reservas SHALL leerlas como columnas mensuales válidas sin señalar error, de modo que la convención de Mes_Caído conserve únicamente los offsets dentro del horizonte y omita dichas columnas.

### Requirement 16: Prorrateo uniforme del saldo por tramo

**User Story:** Como analista financiero, quiero que la variación de saldo de un periodo acumulado se reparta uniformemente entre los meses del tramo, para que cada mes reciba una porción consistente del cambio de nivel del saldo.

#### Acceptance Criteria

1. WHERE una columna destino de la hoja de saldo corresponde a un tramo del Layout_Por_Tramo, THE Lector_Reservas SHALL interpretar el valor de esa columna como un Nivel_Saldo al final del periodo.
2. THE Lector_Reservas SHALL calcular la variación de saldo del periodo (Δsaldo_periodo) como Nivel_Saldo(columna destino) menos Nivel_Saldo(Mes_Corte, nivel base del tramo).
3. THE Lector_Reservas SHALL repartir la variación de saldo del periodo entre los N meses del tramo mediante Prorrateo_Uniforme.
4. THE Lector_Reservas SHALL conservar la invariante de que la suma de los deltas mensuales de saldo prorrateados es igual a la variación de saldo del periodo.
5. WHEN el Nivel_Saldo del Mes_Corte (julio-2026) es 100, el Nivel_Saldo de dic-2026 es 150 y el Tramo_Parcial abarca 5 meses (ago..dic 2026), THE Lector_Reservas SHALL producir deltas mensuales de saldo de +10 en cada uno de los 5 meses (niveles resultantes 110, 120, 130, 140, 150).
6. THE Lector_Reservas SHALL aplicar Prorrateo_Uniforme tanto al Tramo_Parcial de N meses como a cada Tramo_Anual de 12 meses.

### Requirement 17: Prorrateo lineal direccional del interés por tramo

**User Story:** Como analista financiero, quiero que el interés acumulado de un periodo se reparta de forma lineal cuya dirección siga el signo de la variación de saldo del periodo, para que la contribución mensual de interés crezca cuando el saldo del periodo aumenta y decrezca cuando el saldo del periodo disminuye.

#### Acceptance Criteria

1. WHERE una columna destino de la hoja de interés corresponde a un tramo del Layout_Por_Tramo, THE Lector_Reservas SHALL interpretar el valor de esa columna como el Interes_Acumulado_Periodo del tramo completo, no como un nivel.
2. IF la variación de saldo del periodo (Δsaldo_periodo) es mayor o igual que 0, THEN THE Lector_Reservas SHALL repartir el Interes_Acumulado_Periodo entre los N meses del tramo mediante Prorrateo_Lineal_Direccional en sentido CRECIENTE, asignando al mes de posición i (i = 1..N) el valor Interes_Acumulado_Periodo multiplicado por i y dividido por (1 + 2 + ... + N).
3. IF la variación de saldo del periodo (Δsaldo_periodo) es menor que 0, THEN THE Lector_Reservas SHALL repartir el Interes_Acumulado_Periodo entre los N meses del tramo mediante Prorrateo_Lineal_Direccional en sentido DECRECIENTE, asignando al mes de posición i (i = 1..N) el valor Interes_Acumulado_Periodo multiplicado por (N − i + 1) y dividido por (1 + 2 + ... + N).
4. THE Lector_Reservas SHALL conservar la invariante de que la suma de los deltas mensuales de interés prorrateados es igual al Interes_Acumulado_Periodo, preservando el signo del interés acumulado en ambos sentidos.
5. WHEN el Interes_Acumulado_Periodo de un tramo de 5 meses es 75 y la variación de saldo del periodo es mayor o igual que 0, THE Lector_Reservas SHALL producir deltas mensuales de interés de 5, 10, 15, 20 y 25 (que suman 75).
6. WHEN el Interes_Acumulado_Periodo de un tramo de 5 meses es 75 y la variación de saldo del periodo es menor que 0, THE Lector_Reservas SHALL producir deltas mensuales de interés de 25, 20, 15, 10 y 5 (que suman 75).
7. THE Lector_Reservas SHALL aplicar Prorrateo_Lineal_Direccional tanto al Tramo_Parcial de N meses como a cada Tramo_Anual de 12 meses.

### Requirement 18: Variación de reserva mensual en la disposición por tramos

**User Story:** Como analista financiero, quiero que la variación de reserva mensual del layout por tramos combine el prorrateo de saldo y el de interés, para que el motor consuma la misma magnitud Δsaldo − Δinterés que en la disposición mensual existente.

#### Acceptance Criteria

1. WHERE el insumo corresponde al Layout_Por_Tramo, THE Lector_Reservas SHALL calcular la Variación_Reserva mensual como el delta mensual de saldo prorrateado (Prorrateo_Uniforme) menos el delta mensual de interés prorrateado (Prorrateo_Lineal_Direccional) para cada mes del tramo.
2. WHEN el Tramo_Parcial abarca 5 meses con deltas de saldo prorrateados de +10 por mes (Δsaldo_periodo ≥ 0) y deltas de interés prorrateados crecientes de 5, 10, 15, 20 y 25, THE Lector_Reservas SHALL producir Variación_Reserva mensual de 5, 0, -5, -10 y -15.
3. WHERE la variación de saldo del periodo es menor que 0, THE Lector_Reservas SHALL emplear los deltas de interés en sentido decreciente (por ejemplo 25, 20, 15, 10 y 5 para un tramo de 5 meses con interés acumulado 75), de modo que la combinación Δsaldo − Δinterés refleje la inversión de la dirección del interés.
4. THE Lector_Reservas SHALL aplicar los Diccionarios_Mapeo existentes para homologar las etiquetas de saldo e interés a ramos de portafolio antes de combinar los prorrateos.
5. THE Lector_Reservas SHALL etiquetar cada Variación_Reserva mensual resultante con su Mes_Absoluto ("YYYY-MM") correspondiente al mes del tramo.

### Requirement 19: Reutilización de la maquinaria de tramos y prorrateo

**User Story:** Como mantenedor de AXIS, quiero que la disposición por tramos reutilice la maquinaria de tramos y el prorrateo uniforme ya probados y añada únicamente el prorrateo lineal direccional, para minimizar el riesgo de regresión y mantener consistencia arquitectónica.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL reutilizar `calcular_tramos` para delimitar los tramos del Layout_Por_Tramo, sin reimplementar esa lógica.
2. THE Lector_Reservas SHALL reutilizar `prorratear_reservas_anual` como implementación del Prorrateo_Uniforme del lado de saldo.
3. THE Lector_Reservas SHALL incorporar una función de Prorrateo_Lineal_Direccional nueva para el lado de interés, distinta del Prorrateo_Uniforme, que seleccione el sentido creciente o decreciente según el signo de la variación de saldo del periodo.
4. THE Lector_Reservas SHALL conservar la convención de Mes_Caído y la ruta de consumo mensual existentes sin alteración.
5. THE Persistidor_Reservas SHALL conservar la persistencia versionada existente para las variaciones derivadas del Layout_Por_Tramo.

### Requirement 20: Entrada no confiable en la disposición por tramos

**User Story:** Como responsable de seguridad, quiero que la disposición por tramos trate todo el contenido del archivo como entrada no confiable de forma consistente con el resto del lector, para que valores mal formados no corrompan la persistencia ni filtren datos sensibles.

#### Acceptance Criteria

1. THE Lector_Reservas SHALL coercer cada Nivel_Saldo e Interes_Acumulado_Periodo del Layout_Por_Tramo a numérico finito, tratando los valores no numéricos o vacíos como 0.
2. THE Lector_Reservas SHALL normalizar cada etiqueta de ramo (recorte de espacios y mayúsculas) antes de aplicar los Diccionarios_Mapeo en el Layout_Por_Tramo.
3. IF la Fecha_Corte recibida no es una fecha válida, THEN THE Lector_Reservas SHALL señalar un error genérico sin dimensionar tramos ni derivar variaciones.
4. THE Persistidor_Reservas SHALL usar consultas parametrizadas para todas las escrituras derivadas del Layout_Por_Tramo, sin interpolar contenido del archivo en las sentencias SQL.
