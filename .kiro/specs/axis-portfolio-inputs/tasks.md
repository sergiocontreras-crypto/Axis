# Plan de Implementación: AXIS Portfolio Inputs Integration

## Resumen

Este plan implementa la Fase 1 del sistema AXIS (Asset-Liability eXecution & Investment Simulator), enfocándose en la integración de inputs externos e internos desde archivos almacenados en la unidad G: para alimentar el modelo de proyecciones de portafolio. El sistema procesará cuatro fuentes externas (Bloomberg, Estudios Económicos, Índices Asset Class, Curvas PIP) y seis tipos de inputs internos, con validación robusta, versionado y soporte para seis vistas analíticas del portafolio.

## Tareas

- [ ] 1. Configurar estructura del proyecto y dependencias
  - Crear estructura de directorios para módulos (loaders, validators, managers, views, config, utils)
  - Configurar entorno virtual Python y archivo requirements.txt con dependencias (pandas, openpyxl, xlrd, pydantic, pytest)
  - Crear archivo de configuración principal (config.yaml) con rutas de la unidad G: y parámetros del sistema
  - Configurar logging con rotación diaria y niveles configurables
  - _Requerimientos: 15.1, 15.6_

- [ ]* 1.1 Configurar framework de testing
  - Configurar pytest con estructura de tests (unit, integration)
  - Crear fixtures para datos de prueba y mocks de archivos
  - _Requerimientos: 15.1_

- [ ] 2. Implementar módulo de configuración y utilidades base
  - [ ] 2.1 Crear clase ConfigManager para lectura de config.yaml
    - Implementar carga de configuración con validación de rutas de unidad G:
    - Implementar getters para rutas de archivos, timeouts, límites y parámetros de validación
    - Soportar variables de entorno para override de configuración
    - _Requerimientos: 1.1, 2.1, 3.1, 4.1_
  
  - [ ]* 2.2 Escribir unit tests para ConfigManager
    - Probar carga de configuración válida e inválida
    - Probar override con variables de entorno
    - Probar validación de rutas
    - _Requerimientos: 1.1_
  
  - [ ] 2.3 Crear módulo de utilidades para manejo de archivos
    - Implementar función para verificar acceso a unidad G: con reintentos
    - Implementar función para lectura segura de Excel/CSV con manejo de errores
    - Implementar función para detección automática de formato (xlsx, xls, csv)
    - _Requerimientos: 1.1, 1.2, 15.2, 15.3_
  
  - [ ]* 2.4 Escribir unit tests para utilidades de archivos
    - Probar lectura de diferentes formatos
    - Probar manejo de errores de acceso
    - Probar lógica de reintentos
    - _Requerimientos: 15.2_

- [ ] 3. Checkpoint - Verificar configuración base
  - Asegurar que todos los tests pasen, preguntar al usuario si surgen dudas.

- [ ] 4. Implementar Data Validator (validación estructural y semántica)
  - [ ] 4.1 Crear clase DataValidator con métodos de validación base
    - Implementar validación de tipos de datos (fechas, numéricos, strings)
    - Implementar validación de rangos numéricos configurables
    - Implementar validación de fechas (formato, rango temporal)
    - Implementar sistema de reportes de validación (errores críticos vs advertencias)
    - _Requerimientos: 1.3, 2.3, 3.3, 4.3, 12.1, 12.2_
  
  - [ ] 4.2 Implementar reglas de validación semántica
    - Implementar validación de consistencia entre inputs (fechas alineadas, sin duplicados)
    - Implementar validación de catálogos maestros (ramos, compañías, asset classes)
    - Implementar validación de restricciones de arbitraje para curvas
    - Implementar validación de consistencia de tasas de swap con curvas de mercado
    - _Requerimientos: 4.4, 4.5, 5.6, 7.3, 7.4, 12.1, 12.3_
  
  - [ ]* 4.3 Escribir unit tests para DataValidator
    - Probar validación de tipos de datos
    - Probar validación de rangos y fechas
    - Probar detección de inconsistencias
    - Probar generación de reportes de validación
    - _Requerimientos: 12.4_
  
  - [ ] 4.4 Implementar sistema de alertas y reportes de validación
    - Crear clase ValidationReport con listas de errores y advertencias
    - Implementar exportación de reportes a Excel
    - Implementar lógica para bloquear ejecución con errores críticos
    - _Requerimientos: 2.4, 7.4, 12.4, 12.5, 12.6_

- [ ] 5. Implementar Bloomberg Loader
  - [ ] 5.1 Crear clase BloombergLoader
    - Implementar método read_file() para lectura de Excel/CSV desde unidad G:
    - Implementar parsing de columnas (fecha, precio, volumen, ticker)
    - Implementar validación estructural de formato Bloomberg
    - Implementar manejo de errores con logging detallado
    - _Requerimientos: 1.1, 1.2, 1.4_
  
  - [ ] 5.2 Implementar validación específica de datos Bloomberg
    - Integrar con DataValidator para verificar fechas, precios y volúmenes válidos
    - Implementar verificación de límite de tiempo (5 minutos para 1000 activos)
    - _Requerimientos: 1.3, 1.6_
  
  - [ ]* 5.3 Escribir unit tests para BloombergLoader
    - Probar lectura de archivos válidos (xlsx, xls, csv)
    - Probar manejo de archivos inexistentes o inaccesibles
    - Probar validación de datos
    - _Requerimientos: 1.1, 1.4_

- [ ] 6. Implementar Economic Studies Loader
  - [ ] 6.1 Crear clase EconomicStudiesLoader
    - Implementar método read_file() para lectura de Excel desde unidad G:
    - Implementar extracción de proyecciones desde hojas específicas (inflación, PIB, tasas, tipo de cambio)
    - Implementar soporte para múltiples escenarios (base, optimista, pesimista) en hojas separadas
    - Implementar parsing de horizontes temporales y valores numéricos
    - _Requerimientos: 2.1, 2.2, 2.6_
  
  - [ ] 6.2 Implementar validación específica de proyecciones macroeconómicas
    - Integrar con DataValidator para verificar horizontes temporales y valores numéricos
    - Implementar generación de alertas para valores fuera de rangos esperados
    - _Requerimientos: 2.3, 2.4_
  
  - [ ]* 6.3 Escribir unit tests para EconomicStudiesLoader
    - Probar extracción de múltiples hojas y escenarios
    - Probar validación de proyecciones
    - Probar generación de alertas
    - _Requerimientos: 2.1, 2.6_

- [ ] 7. Implementar Asset Class Index Loader
  - [ ] 7.1 Crear clase AssetClassIndexLoader
    - Implementar método read_file() para lectura de Excel/CSV desde unidad G:
    - Implementar extracción de índices desde columnas con encabezados descriptivos
    - Implementar mapeo de índices a asset classes según tabla de configuración
    - _Requerimientos: 3.1, 3.2, 3.6_
  
  - [ ] 7.2 Implementar validación específica de índices
    - Integrar con DataValidator para verificar asociación a asset classes válidos
    - Implementar verificación de series temporales completas (gaps máximo 5 días hábiles)
    - _Requerimientos: 3.3, 3.4_
  
  - [ ]* 7.3 Escribir unit tests para AssetClassIndexLoader
    - Probar lectura de múltiples índices en columnas
    - Probar mapeo a asset classes
    - Probar detección de gaps en series temporales
    - _Requerimientos: 3.1, 3.4_

- [ ] 8. Implementar PIP Curves Loader
  - [ ] 8.1 Crear clase PIPCurvesLoader
    - Implementar método read_file() para lectura de Excel desde unidad G:
    - Implementar extracción de curvas desde hojas específicas (TES, IBR, swaps)
    - Implementar parsing de puntos de plazo y tasas en formato tabular
    - Implementar extracción de fechas de vigencia desde nombre de archivo o metadatos
    - _Requerimientos: 4.1, 4.2, 4.6, 4.7_
  
  - [ ] 8.2 Implementar validación específica de curvas PIP
    - Integrar con DataValidator para verificar formato tabular (plazo, tasa)
    - Implementar verificación de monotonía creciente y restricciones de arbitraje
    - Implementar rechazo de curvas con inconsistencias y notificación detallada
    - _Requerimientos: 4.3, 4.4, 4.5_
  
  - [ ]* 8.3 Escribir unit tests para PIPCurvesLoader
    - Probar extracción de múltiples curvas desde hojas
    - Probar validación de arbitraje
    - Probar rechazo de curvas inconsistentes
    - _Requerimientos: 4.5_

- [ ] 9. Checkpoint - Verificar loaders externos
  - Asegurar que todos los tests pasen, preguntar al usuario si surgen dudas.

- [ ] 10. Implementar Internal Inputs Loader (inputs internos consolidados)
  - [ ] 10.1 Crear clase InternalInputsLoader con métodos especializados
    - Implementar read_portfolio_changes() para cambios de portafolio por reservas técnicas
    - Implementar read_returns() para retornos por título y asset class
    - Implementar read_swap_rates() para tasas de swaps
    - Implementar read_coverages() para forwards y opciones
    - Implementar read_debt_projections() para proyecciones de deuda y fondeo
    - Implementar read_dividend_projections() para proyecciones de dividendos
    - _Requerimientos: 5.1, 6.1, 6.2, 7.1, 8.1, 8.2, 9.1, 9.2, 10.1_
  
  - [ ] 10.2 Implementar validación específica para cambios de portafolio
    - Validar campos requeridos (fecha, ramo, compañía, monto, tipo_reserva)
    - Validar montos dentro de límites configurables
    - Validar existencia de ramos y compañías en catálogo maestro
    - _Requerimientos: 5.2, 5.3, 5.6_
  
  - [ ] 10.3 Implementar validación específica para retornos
    - Validar retornos como porcentajes numéricos
    - Validar horizontes temporales (mensual, trimestral, anual)
    - Validar existencia de títulos en portafolio actual
    - Implementar lógica de aplicación de retornos por asset class a títulos sin retorno específico
    - _Requerimientos: 6.4, 6.5, 6.6, 6.7_
  
  - [ ] 10.4 Implementar validación específica para swaps
    - Validar campos requeridos (plazo, moneda, tasas)
    - Validar consistencia con curvas de mercado dentro de tolerancia
    - Generar alertas para diferenciales significativos
    - Asociar swaps a compañías
    - _Requerimientos: 7.2, 7.3, 7.4, 7.6_
  
  - [ ] 10.5 Implementar validación específica para coberturas
    - Validar campos requeridos para forwards y opciones
    - Validar fechas de vencimiento futuras
    - Asociar coberturas a activos subyacentes y compañías
    - Calcular ratios de cobertura por compañía y consolidado
    - _Requerimientos: 8.3, 8.4, 8.5, 8.6_
  
  - [ ] 10.6 Implementar validación específica para deuda y fondeo
    - Validar proyecciones de deuda (fechas, tasas)
    - Validar necesidades de fondeo (valores positivos)
    - Calcular flujos de caja proyectados de servicio de deuda
    - Identificar períodos con déficit de liquidez
    - _Requerimientos: 9.3, 9.4, 9.5, 9.6_
  
  - [ ] 10.7 Implementar validación específica para dividendos
    - Validar campos requeridos (compañía, título, tipo, monto, fecha)
    - Validar existencia de títulos en portafolio
    - Validar que títulos de tipo "recibir" sean de renta variable
    - Calcular dividend yield proyectado
    - Calcular flujo neto de dividendos por compañía
    - _Requerimientos: 10.2, 10.3, 10.4, 10.5, 10.6_
  
  - [ ]* 10.8 Escribir unit tests para InternalInputsLoader
    - Probar lectura de cada tipo de input interno
    - Probar validaciones específicas de cada tipo
    - Probar cálculos (ratios de cobertura, flujos de caja, dividend yield)
    - _Requerimientos: 5.1, 6.1, 7.1, 8.1, 9.1, 10.1_

- [ ] 11. Implementar Input Manager (orquestación del flujo de carga)
  - [ ] 11.1 Crear clase InputManager con orquestación de loaders
    - Implementar método load_inputs() que coordina todos los loaders
    - Implementar caché en memoria para datos cargados
    - Implementar guardado automático de progreso cada 5 minutos en carpeta temporal de unidad G:
    - Implementar recuperación de último guardado automático al reiniciar
    - _Requerimientos: 1.5, 2.5, 3.5, 4.6, 5.4, 6.6, 7.5, 8.5, 9.5, 10.4, 15.4, 15.5_
  
  - [ ] 11.2 Implementar flujo de validación integral
    - Integrar DataValidator para validación post-carga
    - Implementar lógica de bloqueo con errores críticos
    - Implementar lógica de confirmación de usuario con advertencias
    - _Requerimientos: 12.1, 12.5, 12.6_
  
  - [ ] 11.3 Implementar soporte para múltiples escenarios
    - Permitir carga de múltiples escenarios desde archivos separados
    - Mantener escenarios en caché con identificadores únicos
    - _Requerimientos: 2.6, 5.5_
  
  - [ ]* 11.4 Escribir integration tests para InputManager
    - Probar flujo completo de carga de todos los inputs
    - Probar guardado y recuperación automática
    - Probar manejo de errores y reintentos
    - _Requerimientos: 15.4, 15.5_

- [ ] 12. Checkpoint - Verificar carga y validación de inputs
  - Asegurar que todos los tests pasen, preguntar al usuario si surgen dudas.

- [ ] 13. Implementar Version Manager (versionado y auditoría)
  - [ ] 13.1 Crear clase VersionManager
    - Implementar método save_version() que crea carpeta con timestamp en unidad G:
    - Implementar serialización de inputs a formato interno
    - Implementar asignación de nombres descriptivos a versiones
    - Implementar registro de auditoría (fecha, usuario, descripción) en archivo log
    - _Requerimientos: 13.1, 13.2, 13.5_
  
  - [ ] 13.2 Implementar listado y carga de versiones
    - Implementar método list_versions() con metadatos (fecha, usuario, descripción)
    - Implementar método load_version() que restaura inputs desde carpeta de versión
    - _Requerimientos: 13.3, 13.4_
  
  - [ ] 13.3 Implementar comparación de versiones
    - Implementar método compare_versions() que identifica diferencias entre dos versiones
    - Implementar exportación de reporte de diferencias a Excel
    - _Requerimientos: 13.6_
  
  - [ ]* 13.4 Escribir unit tests para VersionManager
    - Probar guardado y carga de versiones
    - Probar listado de versiones
    - Probar comparación de versiones
    - _Requerimientos: 13.1, 13.4, 13.6_

- [ ] 14. Implementar Export Manager (exportación e importación)
  - [ ] 14.1 Crear clase ExportManager
    - Implementar método export_to_excel() con hojas separadas por tipo de input
    - Implementar método export_to_json() con estructura jerárquica
    - Implementar escritura a rutas especificadas en unidad G:
    - _Requerimientos: 14.1, 14.2_
  
  - [ ] 14.2 Implementar importación de inputs
    - Implementar método import_from_excel() con validación de formato y estructura
    - Implementar método import_from_json() con validación de esquema
    - Implementar generación de reporte de errores de formato en Excel
    - Implementar importación parcial por tipos seleccionados
    - _Requerimientos: 14.3, 14.4, 14.5, 14.6_
  
  - [ ]* 14.3 Escribir unit tests para ExportManager
    - Probar exportación a Excel y JSON
    - Probar importación desde Excel y JSON
    - Probar validación de formatos
    - Probar importación parcial
    - _Requerimientos: 14.1, 14.3_

- [ ] 15. Implementar Portfolio Views (vistas analíticas)
  - [ ] 15.1 Crear clase base PortfolioView
    - Definir interfaz común para todas las vistas (aggregate, calculate_metrics, export)
    - Implementar estructura de datos para métricas agregadas (valor total, retorno esperado, duración, riesgo)
    - _Requerimientos: 11.2, 11.3_
  
  - [ ] 15.2 Implementar vista por Ramo
    - Crear clase RamoView que hereda de PortfolioView
    - Implementar agregación por ramo con distinción encaje/no encaje
    - Implementar cálculo de métricas por ramo
    - _Requerimientos: 11.1, 11.7_
  
  - [ ] 15.3 Implementar vista por Compañía
    - Crear clase CompaniaView que hereda de PortfolioView
    - Implementar agregación por compañía
    - Implementar cálculo de métricas por compañía
    - _Requerimientos: 11.1_
  
  - [ ] 15.4 Implementar vista por Asset Class
    - Crear clase AssetClassView que hereda de PortfolioView
    - Implementar agregación por asset class
    - Implementar cálculo de métricas por asset class
    - _Requerimientos: 11.1_
  
  - [ ] 15.5 Implementar vista por Clasificación Contable
    - Crear clase ClasificacionContableView que hereda de PortfolioView
    - Implementar agregación por clasificación contable
    - Implementar cálculo de métricas por clasificación
    - _Requerimientos: 11.1_
  
  - [ ] 15.6 Implementar vista por Solvencia
    - Crear clase SolvenciaView que hereda de PortfolioView
    - Implementar agregación por categorías de solvencia
    - Implementar cálculo de métricas por categoría
    - _Requerimientos: 11.1_
  
  - [ ] 15.7 Implementar vista por Régimen de Inversiones
    - Crear clase RegimenInversionesView que hereda de PortfolioView
    - Implementar agregación por régimen de inversiones
    - Implementar cálculo de métricas por régimen
    - _Requerimientos: 11.1_
  
  - [ ] 15.8 Implementar ViewManager para gestión de vistas
    - Crear clase ViewManager que coordina las seis vistas
    - Implementar cambio entre vistas sin pérdida de datos
    - Implementar actualización de visualización en menos de 3 segundos
    - Implementar exportación de cada vista a Excel
    - _Requerimientos: 11.2, 11.4, 11.5, 11.6_
  
  - [ ]* 15.9 Escribir unit tests para Portfolio Views
    - Probar agregación de cada vista
    - Probar cálculo de métricas
    - Probar cambio entre vistas
    - Probar exportación de vistas
    - _Requerimientos: 11.2, 11.3, 11.6_

- [ ] 16. Implementar manejo robusto de errores y logging
  - [ ] 16.1 Crear clase ErrorHandler
    - Implementar registro de errores con detalles técnicos (ruta, tipo, contexto)
    - Implementar lógica de reintentos para errores de red (3 intentos, 30 segundos)
    - Implementar notificación al usuario con rutas problemáticas
    - Implementar continuación con inputs parciales tras fallos
    - _Requerimientos: 15.1, 15.2, 15.3_
  
  - [ ] 16.2 Configurar sistema de logging
    - Implementar logs de errores en unidad G: con rotación diaria
    - Implementar niveles de logging configurables (DEBUG, INFO, WARNING, ERROR)
    - Implementar formato estructurado para diagnóstico y soporte
    - _Requerimientos: 15.6_
  
  - [ ]* 16.3 Escribir unit tests para ErrorHandler
    - Probar lógica de reintentos
    - Probar registro de errores
    - Probar continuación con inputs parciales
    - _Requerimientos: 15.2, 15.3_

- [ ] 17. Checkpoint - Verificar manejo de errores y logging
  - Asegurar que todos los tests pasen, preguntar al usuario si surgen dudas.

- [ ] 18. Integración y wiring de componentes
  - [ ] 18.1 Crear módulo principal (main.py) que integra todos los componentes
    - Inicializar ConfigManager y cargar configuración
    - Inicializar InputManager con todos los loaders
    - Inicializar VersionManager y ExportManager
    - Inicializar ViewManager con todas las vistas
    - Inicializar ErrorHandler y configurar logging
    - _Requerimientos: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 10.1, 11.1, 13.1, 14.1, 15.1_
  
  - [ ] 18.2 Implementar interfaz de línea de comandos (CLI)
    - Implementar comandos para cargar inputs por tipo
    - Implementar comandos para listar y cargar versiones
    - Implementar comandos para exportar/importar inputs
    - Implementar comandos para cambiar entre vistas de portafolio
    - Implementar comando para generar reporte de validación
    - _Requerimientos: 11.1, 12.4, 13.3, 14.1, 14.3_
  
  - [ ]* 18.3 Escribir integration tests end-to-end
    - Probar flujo completo: carga → validación → guardado → exportación
    - Probar flujo de recuperación tras error
    - Probar flujo de versionado y comparación
    - Probar cambio entre vistas con datos cargados
    - _Requerimientos: 11.1, 12.1, 13.1, 14.1, 15.4_

- [ ] 19. Documentación y entrega
  - [ ] 19.1 Crear documentación de usuario
    - Escribir guía de instalación y configuración
    - Documentar comandos CLI con ejemplos
    - Documentar estructura de archivos de entrada esperados
    - Documentar mensajes de error comunes y soluciones
    - _Requerimientos: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1, 9.1, 10.1_
  
  - [ ] 19.2 Crear documentación técnica
    - Documentar arquitectura de componentes
    - Documentar APIs de clases principales
    - Documentar esquemas de datos y formatos
    - Documentar proceso de extensión (agregar nuevos loaders o vistas)
    - _Requerimientos: 11.1, 13.1, 14.1, 15.1_
  
  - [ ] 19.3 Crear archivo README.md del proyecto
    - Incluir descripción general del sistema AXIS Fase 1
    - Incluir instrucciones de instalación rápida
    - Incluir ejemplos de uso básico
    - Incluir enlaces a documentación completa
    - _Requerimientos: 1.1_

- [ ] 20. Checkpoint final - Verificar sistema completo
  - Asegurar que todos los tests pasen, preguntar al usuario si surgen dudas.

## Notas

- Las tareas marcadas con `*` son opcionales y pueden omitirse para un MVP más rápido
- Cada tarea referencia requerimientos específicos para trazabilidad
- Los checkpoints aseguran validación incremental del progreso
- El sistema está diseñado para trabajar exclusivamente con archivos en la unidad G:, sin dependencias de bases de datos o APIs
- La arquitectura modular permite agregar nuevos loaders o vistas fácilmente
- El manejo robusto de errores garantiza recuperación ante fallos de red o archivos
