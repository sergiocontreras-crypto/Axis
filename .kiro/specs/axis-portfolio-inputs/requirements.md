# Requirements Document

## Introduction

AXIS (Asset-Liability eXecution & Investment Simulator) es un sistema de proyecciones de portafolio de inversiones para la Gerencia Financiera del sector asegurador colombiano. Esta Fase 1 se enfoca en la integración de inputs externos e internos al modelo de proyecciones, permitiendo consolidar datos de múltiples fuentes almacenadas en archivos de la unidad G: para generar proyecciones precisas del portafolio bajo diferentes vistas analíticas.

El sistema debe procesar cuatro fuentes de datos externos (Bloomberg, Estudios Económicos, Índices Asset Class, y Curvas PIP) y seis tipos de inputs internos (cambios de portafolio, retornos, tasas de swaps, coberturas, proyecciones de deuda y dividendos) desde archivos Excel/CSV ubicados en la unidad G:, permitiendo análisis por ramo, compañía, asset class, clasificación contable, solvencia y régimen de inversiones.

## Glossary

- **AXIS_System**: Asset-Liability eXecution & Investment Simulator - Sistema de proyecciones de portafolio de inversiones para la Gerencia Financiera
- **Input_Manager**: Componente responsable de la integración y validación de inputs externos e internos
- **Bloomberg_Loader**: Módulo de lectura de archivos de datos Bloomberg desde la unidad G:
- **Economic_Studies_Loader**: Módulo de lectura de archivos de Estudios Económicos desde la unidad G:
- **Asset_Class_Index_Loader**: Módulo de lectura de archivos de Índices Asset Class desde la unidad G:
- **PIP_Curves_Loader**: Módulo de lectura de archivos de Curvas PIP desde la unidad G:
- **G_Drive**: Unidad de red compartida (G:) donde se almacenan todos los archivos de entrada del sistema
- **Portfolio_View**: Vista analítica del portafolio (ramo, compañía, asset class, clasificación contable, solvencia, régimen de inversiones)
- **External_Input**: Dato proveniente de fuentes externas (Bloomberg, Estudios Económicos, Índices Asset Class, Curvas PIP)
- **Internal_Input**: Dato proveniente de fuentes internas de la organización
- **Data_Validator**: Componente que verifica la integridad y calidad de los datos ingresados
- **Projection_Model**: Modelo matemático que genera proyecciones del portafolio
- **Technical_Reserve**: Reserva técnica que afecta la composición del portafolio
- **Asset_Class**: Clase de activo (renta fija, renta variable, derivados, etc.)
- **Swap_Rate**: Tasa de interés aplicable a contratos de swap del portafolio
- **Forward_Coverage**: Cobertura mediante contratos forward
- **Option_Coverage**: Cobertura mediante contratos de opciones

## Requirements

### Requirement 1: Integración de Datos de Bloomberg

**User Story:** Como analista de la Gerencia Financiera, quiero cargar datos históricos de activos e índices desde archivos de Bloomberg almacenados en la unidad G:, para que el modelo de proyecciones utilice información actualizada del mercado.

#### Acceptance Criteria

1. WHEN el usuario solicita importar datos de Bloomberg, THE Bloomberg_Loader SHALL leer el archivo de datos Bloomberg (formato Excel o CSV) ubicado en la ruta configurada de la unidad G:
2. THE Bloomberg_Loader SHALL soportar archivos con extensiones .xlsx, .xls y .csv con delimitadores configurables
3. THE Data_Validator SHALL verificar que los datos de Bloomberg contengan fechas, precios y volúmenes válidos
4. IF el archivo de Bloomberg no existe o no es accesible en la unidad G:, THEN THE Bloomberg_Loader SHALL registrar el error y notificar al usuario con la ruta esperada
5. WHEN los datos de Bloomberg son validados, THE Input_Manager SHALL almacenar los datos en el formato interno del sistema
6. THE Bloomberg_Loader SHALL completar la lectura de datos dentro de 5 minutos para archivos de hasta 1000 activos

### Requirement 2: Integración de Proyecciones Macroeconómicas

**User Story:** Como analista de la Gerencia Financiera, quiero cargar proyecciones macroeconómicas desde archivos de Estudios Económicos almacenados en la unidad G:, para que el modelo considere escenarios económicos futuros.

#### Acceptance Criteria

1. WHEN el usuario solicita importar proyecciones de Estudios Económicos, THE Economic_Studies_Loader SHALL leer el archivo de proyecciones (formato Excel) ubicado en la ruta configurada de la unidad G:
2. THE Economic_Studies_Loader SHALL extraer proyecciones de variables macroeconómicas (inflación, PIB, tasas de interés, tipo de cambio) desde hojas específicas del archivo Excel
3. THE Data_Validator SHALL verificar que las proyecciones contengan horizontes temporales y valores numéricos válidos
4. IF las proyecciones contienen valores fuera de rangos esperados, THEN THE Data_Validator SHALL generar alertas de validación
5. WHEN las proyecciones son validadas, THE Input_Manager SHALL almacenar las proyecciones asociadas a sus horizontes temporales
6. THE Economic_Studies_Loader SHALL soportar múltiples escenarios (base, optimista, pesimista) en hojas separadas del mismo archivo Excel

### Requirement 3: Integración de Índices de Asset Class

**User Story:** Como analista de la Gerencia Financiera, quiero cargar benchmarks de índices por asset class desde archivos almacenados en la unidad G:, para que el modelo compare el desempeño del portafolio contra referencias del mercado.

#### Acceptance Criteria

1. WHEN el usuario solicita importar índices de asset class, THE Asset_Class_Index_Loader SHALL leer el archivo de índices (formato Excel o CSV) ubicado en la ruta configurada de la unidad G:
2. THE Asset_Class_Index_Loader SHALL extraer datos históricos de índices para cada asset class relevante desde columnas específicas del archivo
3. THE Data_Validator SHALL verificar que cada índice esté asociado a un asset class válido según la tabla de mapeo configurada
4. THE Data_Validator SHALL verificar que los datos de índices contengan series temporales completas sin gaps mayores a 5 días hábiles
5. WHEN los índices son validados, THE Input_Manager SHALL almacenar los índices asociados a sus respectivos asset classes
6. THE Asset_Class_Index_Loader SHALL soportar archivos con múltiples índices en columnas separadas con encabezados descriptivos

### Requirement 4: Integración de Curvas PIP

**User Story:** Como analista de la Gerencia Financiera, quiero cargar curvas PIP de derivados y precios de renta fija desde archivos almacenados en la unidad G:, para que el modelo valore correctamente instrumentos financieros complejos.

#### Acceptance Criteria

1. WHEN el usuario solicita importar curvas PIP, THE PIP_Curves_Loader SHALL leer el archivo de curvas (formato Excel) ubicado en la ruta configurada de la unidad G:
2. THE PIP_Curves_Loader SHALL extraer curvas de valoración para derivados y renta fija desde hojas específicas del archivo Excel
3. THE Data_Validator SHALL verificar que las curvas contengan puntos de plazo y tasas válidas en formato tabular (plazo, tasa)
4. THE Data_Validator SHALL verificar que las curvas sean monótonas crecientes o cumplan con restricciones de arbitraje
5. IF una curva contiene inconsistencias de arbitraje, THEN THE Data_Validator SHALL rechazar la curva y notificar al usuario con detalles del punto problemático
6. WHEN las curvas son validadas, THE Input_Manager SHALL almacenar las curvas con sus fechas de vigencia extraídas del nombre del archivo o metadatos
7. THE PIP_Curves_Loader SHALL soportar múltiples curvas (TES, IBR, swaps) en hojas separadas del mismo archivo Excel

### Requirement 5: Integración de Cambios de Portafolio por Reservas Técnicas

**User Story:** Como analista de la Gerencia Financiera, quiero cargar cambios proyectados en el portafolio debido a reservas técnicas desde archivos almacenados en la unidad G:, para que el modelo refleje ajustes regulatorios y operativos por ramo y compañía.

#### Acceptance Criteria

1. THE Input_Manager SHALL leer cambios proyectados de portafolio desde un archivo Excel ubicado en la unidad G: con columnas: fecha, ramo, compañía, monto, tipo_reserva
2. THE Data_Validator SHALL verificar que cada cambio especifique fecha, monto, ramo, compañía y tipo de reserva técnica
3. THE Data_Validator SHALL verificar que los montos de cambios sean numéricos y estén dentro de límites configurables
4. WHEN un cambio de portafolio es validado, THE Input_Manager SHALL asociar el cambio a la vista de portafolio correspondiente (ramo, compañía, asset class)
5. THE Input_Manager SHALL permitir cargar múltiples escenarios de cambios desde archivos separados en la unidad G:
6. THE Data_Validator SHALL verificar que los ramos y compañías especificados existan en el catálogo maestro del sistema

### Requirement 6: Integración de Retornos por Título y Asset Class

**User Story:** Como analista de la Gerencia Financiera, quiero cargar retornos esperados por título individual y por asset class desde archivos almacenados en la unidad G:, para que el modelo proyecte rendimientos del portafolio.

#### Acceptance Criteria

1. THE Input_Manager SHALL leer retornos esperados desde un archivo Excel ubicado en la unidad G: con hojas separadas para retornos por título y por asset class
2. THE Input_Manager SHALL soportar retornos a nivel de título individual con columnas: título, ISIN, retorno_esperado, horizonte_temporal
3. THE Input_Manager SHALL soportar retornos a nivel de asset class con columnas: asset_class, retorno_esperado, horizonte_temporal
4. THE Data_Validator SHALL verificar que los retornos sean valores numéricos expresados como porcentajes
5. THE Data_Validator SHALL verificar que cada retorno esté asociado a un horizonte temporal válido (mensual, trimestral, anual)
6. WHEN un retorno es cargado a nivel de título, THE Input_Manager SHALL verificar que el título exista en el portafolio actual
7. WHEN un retorno es cargado a nivel de asset class, THE Input_Manager SHALL aplicar el retorno a todos los títulos de ese asset class que no tengan retorno específico

### Requirement 7: Integración de Tasas de Portafolio de Swaps

**User Story:** Como analista de la Gerencia Financiera, quiero cargar tasas aplicables a swaps del portafolio desde archivos almacenados en la unidad G:, para que el modelo valore correctamente posiciones en derivados de tasas de interés (estructuras).

#### Acceptance Criteria

1. THE Input_Manager SHALL leer tasas de swap desde un archivo Excel ubicado en la unidad G: con columnas: contrato_swap, plazo, moneda, tasa_fija, tasa_variable, fecha_inicio, fecha_vencimiento
2. THE Data_Validator SHALL verificar que cada tasa de swap especifique plazo, moneda y valor numérico
3. THE Data_Validator SHALL verificar que las tasas de swap sean consistentes con curvas de mercado dentro de un margen de tolerancia configurable
4. IF una tasa de swap difiere significativamente de curvas de mercado, THEN THE Data_Validator SHALL generar una alerta de validación con el diferencial detectado
5. WHEN las tasas de swap son validadas, THE Input_Manager SHALL almacenar las tasas asociadas a sus plazos, monedas y contratos específicos
6. THE Input_Manager SHALL asociar cada swap a la compañía correspondiente según columna de identificación en el archivo

### Requirement 8: Integración de Coberturas con Forwards y Opciones

**User Story:** Como analista de la Gerencia Financiera, quiero cargar el portafolio de coberturas mediante forwards y opciones desde archivos almacenados en la unidad G:, para que el modelo refleje estrategias de mitigación de riesgo.

#### Acceptance Criteria

1. THE Input_Manager SHALL leer contratos de forward desde un archivo Excel ubicado en la unidad G: con columnas: contrato, subyacente, monto, precio_forward, fecha_vencimiento, compañía
2. THE Input_Manager SHALL leer contratos de opciones desde un archivo Excel ubicado en la unidad G: con columnas: contrato, subyacente, monto, strike, prima, tipo (call/put), fecha_vencimiento, compañía
3. THE Data_Validator SHALL verificar que cada contrato de cobertura contenga todos los campos requeridos
4. THE Data_Validator SHALL verificar que las fechas de vencimiento sean futuras respecto a la fecha de valoración
5. WHEN una cobertura es validada, THE Input_Manager SHALL asociar la cobertura a los activos subyacentes del portafolio y a la compañía correspondiente
6. THE Input_Manager SHALL calcular el ratio de cobertura como porcentaje del portafolio cubierto por compañía y consolidado
7. THE Input_Manager SHALL soportar archivos con múltiples hojas para forwards y opciones en el mismo archivo Excel

### Requirement 9: Integración de Proyecciones de Deuda y Fondeo

**User Story:** Como analista de la Gerencia Financiera, quiero cargar proyecciones de deuda y necesidades de fondeo por compañía desde archivos almacenados en la unidad G:, para que el modelo considere flujos de caja futuros y restricciones de liquidez.

#### Acceptance Criteria

1. THE Input_Manager SHALL leer proyecciones de deuda desde un archivo Excel ubicado en la unidad G: con columnas: compañía, tipo_deuda, monto, plazo, tasa_interes, fecha_inicio, fecha_vencimiento
2. THE Input_Manager SHALL leer proyecciones de fondeo desde un archivo Excel ubicado en la unidad G: con columnas: compañía, período, monto_fondeo, fuente_fondeo
3. THE Data_Validator SHALL verificar que las proyecciones de deuda contengan fechas de vencimiento y tasas válidas
4. THE Data_Validator SHALL verificar que las necesidades de fondeo sean valores numéricos positivos
5. WHEN las proyecciones son validadas, THE Input_Manager SHALL calcular flujos de caja proyectados de servicio de deuda por compañía
6. THE Input_Manager SHALL identificar períodos con déficit de liquidez basado en proyecciones de fondeo por compañía
7. THE Input_Manager SHALL soportar archivos con hojas separadas para deuda y fondeo en el mismo archivo Excel

### Requirement 10: Integración de Proyecciones de Dividendos

**User Story:** Como analista de la Gerencia Financiera, quiero cargar proyecciones de dividendos a pagar o recibir por compañía desde archivos almacenados en la unidad G:, para que el modelo considere ingresos y egresos futuros por distribuciones de utilidades.

#### Acceptance Criteria

1. THE Input_Manager SHALL leer proyecciones de dividendos desde un archivo Excel ubicado en la unidad G: con columnas: compañía, título, ISIN, tipo (pagar/recibir), monto, fecha_pago_esperada
2. THE Data_Validator SHALL verificar que cada proyección de dividendo especifique compañía, título, tipo, monto y fecha de pago esperada
3. THE Data_Validator SHALL verificar que los títulos especificados existan en el portafolio y sean de renta variable cuando el tipo sea "recibir"
4. WHEN una proyección de dividendo es validada, THE Input_Manager SHALL asociar el dividendo al título y compañía correspondiente
5. THE Input_Manager SHALL calcular el dividend yield proyectado para cada título con dividendos esperados a recibir
6. THE Input_Manager SHALL calcular el flujo neto de dividendos por compañía (recibir - pagar) para cada período proyectado

### Requirement 11: Vistas Analíticas del Portafolio

**User Story:** Como analista de la Gerencia Financiera, quiero visualizar el portafolio bajo diferentes dimensiones analíticas (ramo, compañía, asset class, clasificación contable, solvencia, régimen de inversiones), para que pueda analizar composición y riesgos desde múltiples perspectivas regulatorias y de negocio.

#### Acceptance Criteria

1. THE AXIS_System SHALL permitir al usuario seleccionar entre seis vistas de portafolio: ramo (con encaje/no encaje), compañía, asset class, clasificación contable, categorías de solvencia y régimen de inversiones
2. WHEN el usuario selecciona una vista de portafolio, THE AXIS_System SHALL agregar todos los inputs y posiciones según la dimensión seleccionada
3. THE AXIS_System SHALL calcular métricas agregadas (valor total, retorno esperado, duración, riesgo) para cada vista de portafolio
4. THE AXIS_System SHALL permitir al usuario cambiar entre vistas sin perder datos ingresados
5. WHEN el usuario cambia de vista, THE AXIS_System SHALL actualizar la visualización en menos de 3 segundos
6. THE AXIS_System SHALL permitir exportar cada vista de portafolio a formato Excel con todos sus datos y métricas
7. FOR la vista por ramo, THE AXIS_System SHALL distinguir entre títulos que encajan y no encajan con las reservas técnicas del ramo

### Requirement 12: Validación Integral de Inputs

**User Story:** Como analista de la Gerencia Financiera, quiero que el sistema valide la consistencia entre todos los inputs cargados desde la unidad G:, para que el modelo de proyecciones opere con datos coherentes.

#### Acceptance Criteria

1. WHEN todos los inputs son cargados desde la unidad G:, THE Data_Validator SHALL verificar consistencia entre inputs externos e internos
2. THE Data_Validator SHALL verificar que las fechas de todos los inputs estén alineadas con el horizonte de proyección configurado
3. THE Data_Validator SHALL verificar que no existan duplicados en los inputs ingresados
4. IF el Data_Validator detecta inconsistencias, THEN THE AXIS_System SHALL generar un reporte de validación con errores y advertencias exportable a Excel
5. THE AXIS_System SHALL impedir la ejecución del modelo de proyecciones mientras existan errores de validación críticos
6. THE AXIS_System SHALL permitir la ejecución del modelo de proyecciones con advertencias de validación si el usuario confirma explícitamente

### Requirement 13: Persistencia y Versionado de Inputs

**User Story:** Como analista de la Gerencia Financiera, quiero que el sistema guarde y versione los inputs cargados desde la unidad G:, para que pueda recuperar configuraciones anteriores y auditar cambios.

#### Acceptance Criteria

1. WHEN el usuario guarda un conjunto de inputs, THE Input_Manager SHALL crear una versión con timestamp y usuario en una carpeta de versiones en la unidad G:
2. THE Input_Manager SHALL permitir al usuario asignar un nombre descriptivo a cada versión de inputs
3. THE Input_Manager SHALL permitir al usuario listar todas las versiones guardadas con sus metadatos (fecha, usuario, descripción)
4. WHEN el usuario selecciona una versión anterior, THE Input_Manager SHALL cargar todos los inputs de esa versión desde la unidad G:
5. THE Input_Manager SHALL mantener un registro de auditoría con fecha, usuario y descripción de cambios para cada versión en un archivo log
6. THE Input_Manager SHALL permitir comparar dos versiones de inputs mostrando diferencias en un reporte Excel

### Requirement 14: Exportación e Importación de Inputs

**User Story:** Como analista de la Gerencia Financiera, quiero exportar e importar inputs en formatos estándar hacia/desde la unidad G:, para que pueda compartir configuraciones y trabajar con herramientas externas.

#### Acceptance Criteria

1. THE Input_Manager SHALL permitir exportar todos los inputs a formato Excel con hojas separadas por tipo de input hacia una ruta especificada en la unidad G:
2. THE Input_Manager SHALL permitir exportar todos los inputs a formato JSON con estructura jerárquica hacia una ruta especificada en la unidad G:
3. WHEN el usuario importa inputs desde Excel ubicado en la unidad G:, THE Input_Manager SHALL validar el formato y estructura del archivo
4. WHEN el usuario importa inputs desde JSON ubicado en la unidad G:, THE Input_Manager SHALL validar el esquema contra la especificación del sistema
5. IF un archivo de importación contiene errores de formato, THEN THE Input_Manager SHALL generar un reporte detallado de errores en formato Excel
6. THE Input_Manager SHALL permitir importación parcial de inputs seleccionando tipos específicos desde archivos en la unidad G:

### Requirement 15: Manejo de Errores y Recuperación

**User Story:** Como analista de la Gerencia Financiera, quiero que el sistema maneje errores de forma robusta al leer archivos desde la unidad G:, para que pueda recuperarme de fallos sin perder trabajo.

#### Acceptance Criteria

1. IF ocurre un error durante la lectura de archivos desde la unidad G:, THEN THE AXIS_System SHALL registrar el error con detalles técnicos (ruta, tipo de error) y contexto
2. WHEN ocurre un error de acceso a la unidad G: (red no disponible, permisos insuficientes), THE AXIS_System SHALL reintentar el acceso hasta 3 veces con intervalos de 30 segundos
3. IF todos los reintentos fallan, THEN THE AXIS_System SHALL notificar al usuario con la ruta del archivo problemático y permitir continuar con inputs parciales
4. THE Input_Manager SHALL guardar automáticamente el progreso cada 5 minutos en una carpeta temporal de la unidad G:
5. IF el sistema se cierra inesperadamente, THEN THE Input_Manager SHALL recuperar el último guardado automático al reiniciar desde la carpeta temporal
6. THE AXIS_System SHALL mantener logs de errores en la unidad G: accesibles para diagnóstico y soporte técnico con rotación diaria
