# Requirements Document

## Introduction

Actualmente el mismo conocimiento de negocio —"traducir la etiqueta de reserva del Excel operativo al ramo del diccionario de portafolios"— está duplicado en tres definiciones distintas dentro de AXIS:

1. `HOMOLOGACION_RESERVAS_A_DICCIONARIO`: diccionario Python hardcodeado en `scripts/calcular_encaje.py`, mezcla saldos e interés. Lo importan `scripts/generar_inputs_reservas.py` y `scripts/lector_reservas.py`.
2. `MAPEO_INTERES_A_RAMO`: diccionario Python hardcodeado en `scripts/generar_inputs_reservas.py` (solo interés). También lo importa `scripts/lector_reservas.py`.
3. `_SEED_MAPEO_SALDO` y `_SEED_MAPEO_INTERES`: semillas en `scripts/compras_ventas_reservas.py` que pueblan las tablas SQLite editables `mapeo_ramos_saldo_reservas` y `mapeo_ramos_interes_reservas`, consumidas por el motor de proyección vía `cargar_mapa_reservas`.

Esta triple definición es deuda técnica con un bug latente ya observable: el ramo "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" se **ignora** (destino `""`) en `_SEED_MAPEO_SALDO`, pero se **conserva** (destino `"VIDA INDIVIDUAL-RESERVA INSUFICIENCIA RIA"`) en `HOMOLOGACION_RESERVAS_A_DICCIONARIO`. El mismo ramo se trata de forma distinta según qué parte del sistema lo lea. Además, existe una diferencia de granularidad en Capitalizadora (sub-ramos DABUENAVIDA/MAGNATE/GANAMAS vs. fila `TOTAL CAPI`) y diferencias menores de espaciado en las etiquetas.

Esta feature consolida el mapeo en **una sola fuente de verdad**: las tablas SQLite editables `mapeo_ramos_saldo_reservas` y `mapeo_ramos_interes_reservas` (ya introducidas por `compras_ventas_reservas.py`), por ser editables desde el dashboard y no hardcodeadas. Los dos flujos que hoy leen los diccionarios hardcodeados (pestaña Encaje y generación de inputs) migrarán a leer de esas tablas, los diccionarios hardcodeados se deprecarán, y se aplicarán de forma única las dos decisiones de negocio ya confirmadas por Gerencia Financiera. Ambos flujos deben preservar su comportamiento observable (retrocompatibilidad), salvo la corrección intencional de la divergencia.

**Decisiones de negocio confirmadas por Gerencia Financiera:**
- **Discrepancia RIA:** la etiqueta "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" se **ignora** (destino `""`).
- **Agregación de Capitalizadora:** se usa la fila `TOTAL CAPI` como valor del ramo CAPITALIZADORA (no la suma de sub-ramos DABUENAVIDA/MAGNATE/GANAMAS).

## Glossary

- **AXIS**: Simulador de proyección de portafolio de renta fija (Python 3.12+, Dash, SQLite) de la Gerencia Financiera.
- **Etiqueta_De_Reserva**: Nombre de un ramo/segmento tal como aparece en las hojas del Excel operativo de reservas ("Saldo Reservas", "Interés a la Reserva"). Es entrada NO confiable.
- **Ramo_De_Portafolio**: Nombre canónico del ramo en el diccionario de portafolios; coincide con la columna `ramo` de `portafolio_clasificado` (mayúsculas, sin tildes).
- **Mapeo_De_Reservas**: Correspondencia `Etiqueta_De_Reserva → Ramo_De_Portafolio`. Un destino vacío (`""`) significa ignorar la etiqueta.
- **Fuente_Unica_De_Verdad**: Las tablas SQLite `mapeo_ramos_saldo_reservas` y `mapeo_ramos_interes_reservas`, que almacenan el Mapeo_De_Reservas y son editables desde el dashboard.
- **Tabla_Mapeo_Saldo**: Tabla SQLite `mapeo_ramos_saldo_reservas` (`formato_origen TEXT PRIMARY KEY`, `ramo_portafolio TEXT NOT NULL`) para el mapeo de saldos.
- **Tabla_Mapeo_Interes**: Tabla SQLite `mapeo_ramos_interes_reservas` (`formato_origen TEXT PRIMARY KEY`, `ramo_portafolio TEXT NOT NULL`) para el mapeo de interés.
- **Lector_Mapeo**: Componente de acceso a datos que asegura el esquema de forma idempotente, lee las tablas de mapeo con queries parametrizadas y devuelve el Mapeo_De_Reservas normalizado y validado.
- **Flujo_Encaje**: Flujo de la pestaña Encaje del dashboard: `lector_reservas.py` + `generar_inputs_reservas.py` leen SALDOS ABSOLUTOS y aplican mes caído y proyección CAGR.
- **Flujo_Motor**: Motor de proyección; `compras_ventas_reservas.py` deriva VARIACIONES (deltas) de reserva para decidir compras/ventas.
- **Diccionarios_Hardcodeados**: `HOMOLOGACION_RESERVAS_A_DICCIONARIO` (en `calcular_encaje.py`) y `MAPEO_INTERES_A_RAMO` (en `generar_inputs_reservas.py`).
- **Discrepancia_RIA**: Divergencia existente en el tratamiento de la Etiqueta_De_Reserva "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" (ignorada en la semilla de saldos vs. conservada en la homologación).
- **Regla_Agregacion_Capitalizadora**: Regla única que decide cómo se obtiene el agregado CAPITALIZADORA (sub-ramos DABUENAVIDA/MAGNATE/GANAMAS vs. fila `TOTAL CAPI`).
- **Gerencia_Financiera**: Área de negocio propietaria de las reglas de mapeo; autoridad para confirmar decisiones de negocio pendientes.
- **Dashboard**: Aplicación Dash (`app_dash.py`) desde la que se editan las tablas de configuración.
- **Normalizacion_Etiqueta**: Transformación de una Etiqueta_De_Reserva a su forma de clave: `strip` de espacios y `upper` (mayúsculas).

## Requirements

### Requirement 1: Fuente única de verdad del mapeo de ramos de reservas

**User Story:** Como ingeniero de la Gerencia Financiera, quiero una única fuente de verdad para el mapeo de ramos de reservas, para eliminar la redundancia entre las tres definiciones actuales y evitar divergencias futuras.

#### Acceptance Criteria

1. THE AXIS SHALL usar la Tabla_Mapeo_Saldo como única fuente del Mapeo_De_Reservas para saldos.
2. THE AXIS SHALL usar la Tabla_Mapeo_Interes como única fuente del Mapeo_De_Reservas para interés.
3. THE Lector_Mapeo SHALL crear la Tabla_Mapeo_Saldo y la Tabla_Mapeo_Interes de forma idempotente mediante `CREATE TABLE IF NOT EXISTS` desde la capa de aplicación.
4. IF `init_db.py` se ejecuta, THEN THE AXIS SHALL dejar la creación de la Tabla_Mapeo_Saldo y la Tabla_Mapeo_Interes fuera del bootstrap destructivo `init_db.py`.
5. WHERE la Tabla_Mapeo_Saldo o la Tabla_Mapeo_Interes están vacías, THE AXIS SHALL sembrarlas una única vez con las semillas de definición sin sobrescribir ediciones posteriores del usuario.

### Requirement 2: Lector centralizado del mapeo

**User Story:** Como ingeniero de la Gerencia Financiera, quiero un componente único que lea el mapeo desde las tablas, para que todos los flujos obtengan el mismo resultado normalizado.

#### Acceptance Criteria

1. THE Lector_Mapeo SHALL exponer una operación que devuelva el Mapeo_De_Reservas de saldos leído de la Tabla_Mapeo_Saldo.
2. THE Lector_Mapeo SHALL exponer una operación que devuelva el Mapeo_De_Reservas de interés leído de la Tabla_Mapeo_Interes.
3. WHEN el Lector_Mapeo lee una fila de una tabla de mapeo, THE Lector_Mapeo SHALL aplicar Normalizacion_Etiqueta a la Etiqueta_De_Reserva antes de usarla como clave.
4. WHEN el Lector_Mapeo encuentra dos filas cuya Etiqueta_De_Reserva normalizada coincide, THE Lector_Mapeo SHALL conservar una única entrada por clave normalizada de forma determinista.
5. WHERE una fila de mapeo tiene destino vacío, THE Lector_Mapeo SHALL representar esa Etiqueta_De_Reserva como ignorada en el Mapeo_De_Reservas.

### Requirement 3: Migración de `lector_reservas.py` a la fuente única

**User Story:** Como ingeniero de la Gerencia Financiera, quiero que `lector_reservas.py` obtenga el mapeo desde las tablas, para que la pestaña Encaje deje de depender de diccionarios hardcodeados.

#### Acceptance Criteria

1. THE Modulo_Lector_Reservas SHALL obtener el Mapeo_De_Reservas de saldos desde el Lector_Mapeo.
2. THE Modulo_Lector_Reservas SHALL obtener el Mapeo_De_Reservas de interés desde el Lector_Mapeo.
3. THE Modulo_Lector_Reservas SHALL dejar de importar `HOMOLOGACION_RESERVAS_A_DICCIONARIO` desde `calcular_encaje.py`.
4. THE Modulo_Lector_Reservas SHALL dejar de importar `MAPEO_INTERES_A_RAMO` desde `generar_inputs_reservas.py`.

### Requirement 4: Migración de `generar_inputs_reservas.py` a la fuente única

**User Story:** Como ingeniero de la Gerencia Financiera, quiero que `generar_inputs_reservas.py` obtenga el mapeo desde las tablas, para que la generación de inputs del dashboard use la misma fuente que el motor.

#### Acceptance Criteria

1. THE Modulo_Generar_Inputs SHALL obtener el Mapeo_De_Reservas de saldos desde el Lector_Mapeo.
2. THE Modulo_Generar_Inputs SHALL obtener el Mapeo_De_Reservas de interés desde el Lector_Mapeo.
3. THE Modulo_Generar_Inputs SHALL dejar de importar `HOMOLOGACION_RESERVAS_A_DICCIONARIO` desde `calcular_encaje.py`.
4. THE Modulo_Generar_Inputs SHALL definir el mapeo de interés a partir del Lector_Mapeo en lugar del diccionario local `MAPEO_INTERES_A_RAMO`.

### Requirement 5: Deprecación de los diccionarios hardcodeados

**User Story:** Como ingeniero de la Gerencia Financiera, quiero que los diccionarios hardcodeados se deprecien, para que no exista una segunda fuente de verdad que pueda divergir.

#### Acceptance Criteria

1. WHEN la migración de los flujos ha finalizado, THE AXIS SHALL eliminar todo uso de `HOMOLOGACION_RESERVAS_A_DICCIONARIO` fuera de la Fuente_Unica_De_Verdad.
2. WHEN la migración de los flujos ha finalizado, THE AXIS SHALL eliminar todo uso de `MAPEO_INTERES_A_RAMO` fuera de la Fuente_Unica_De_Verdad.
3. WHERE se conserve temporalmente un diccionario hardcodeado por compatibilidad, THE AXIS SHALL marcarlo como deprecado con una anotación que indique la Fuente_Unica_De_Verdad como reemplazo.
4. THE AXIS SHALL contener el conocimiento de negocio del Mapeo_De_Reservas en un único lugar de definición.

### Requirement 6: Reconciliación de la discrepancia RIA

**User Story:** Como analista de la Gerencia Financiera, quiero que la etiqueta "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" tenga un único tratamiento, para que el saldo y la variación del mismo ramo sean coherentes en todo el sistema.

#### Acceptance Criteria

1. THE AXIS SHALL asignar a la Etiqueta_De_Reserva "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" el destino vacío (`""`) en la Fuente_Unica_De_Verdad, de modo que la etiqueta se ignore.
2. WHEN el Lector_Mapeo lee la Etiqueta_De_Reserva "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY", THE Lector_Mapeo SHALL representarla como ignorada en el Mapeo_De_Reservas.
3. THE Flujo_Encaje y THE Flujo_Motor SHALL ignorar la Etiqueta_De_Reserva "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" al procesar reservas.
4. WHEN se elimine el destino "VIDA INDIVIDUAL-RESERVA INSUFICIENCIA RIA" de los Diccionarios_Hardcodeados, THE AXIS SHALL alinear el tratamiento de la etiqueta con la Fuente_Unica_De_Verdad.

> **Decisión de negocio confirmada (Gerencia Financiera):** La etiqueta "RESERVA INSUFICIENCIA DE ACTIVOS VIDA LIBERTY" se **ignora** (destino `""`, como en `_SEED_MAPEO_SALDO`). Se descarta el tratamiento previo de `HOMOLOGACION_RESERVAS_A_DICCIONARIO` que la conservaba como "VIDA INDIVIDUAL-RESERVA INSUFICIENCIA RIA".

### Requirement 7: Regla única de agregación de Capitalizadora

**User Story:** Como analista de la Gerencia Financiera, quiero una regla única para agregar Capitalizadora, para que el saldo de reservas de Capitalizadora sea consistente entre la pestaña Encaje y el motor.

#### Acceptance Criteria

1. THE AXIS SHALL definir como única Regla_Agregacion_Capitalizadora el uso de la fila `TOTAL CAPI` como valor del ramo CAPITALIZADORA.
2. WHEN se calcula el agregado CAPITALIZADORA, THE Flujo_Encaje y THE Flujo_Motor SHALL usar la fila `TOTAL CAPI` como valor del ramo CAPITALIZADORA.
3. THE AXIS SHALL NOT sumar los sub-ramos DABUENAVIDA, MAGNATE y GANAMAS para obtener el agregado CAPITALIZADORA.
4. WHEN se eliminen los sub-ramos DABUENAVIDA, MAGNATE y GANAMAS de los Diccionarios_Hardcodeados, THE AXIS SHALL alinear el cálculo de CAPITALIZADORA con la Fuente_Unica_De_Verdad.
5. WHERE la fila `TOTAL CAPI` provee la serie completa de CAPITALIZADORA, THE AXIS SHALL obtener el valor de CAPITALIZADORA para cada mes sin depender de los sub-ramos.

> **Decisión de negocio confirmada (Gerencia Financiera):** La Regla_Agregacion_Capitalizadora usa la fila `TOTAL CAPI` (como en `generar_inputs_reservas.py`). Se descarta la suma de sub-ramos DABUENAVIDA/MAGNATE/GANAMAS de `HOMOLOGACION_RESERVAS_A_DICCIONARIO`, ya que esos sub-ramos solo tienen datos parciales en el año.

### Requirement 8: Retrocompatibilidad del comportamiento observable

**User Story:** Como analista de la Gerencia Financiera, quiero que la pestaña Encaje y el motor sigan produciendo los mismos resultados, para no introducir regresiones al consolidar el mapeo.

#### Acceptance Criteria

1. WHEN el Flujo_Encaje procesa las reservas con la Fuente_Unica_De_Verdad, THE Flujo_Encaje SHALL producir los mismos saldos e interés por ramo que producía con los Diccionarios_Hardcodeados, salvo para las etiquetas afectadas por la corrección de la Discrepancia_RIA.
2. WHEN el Flujo_Motor deriva variaciones con la Fuente_Unica_De_Verdad, THE Flujo_Motor SHALL producir las mismas variaciones por ramo que producía con las semillas, salvo para las etiquetas afectadas por la corrección de la Discrepancia_RIA.
3. THE AXIS SHALL preservar el comportamiento cubierto por la suite de pruebas existente de `compras_ventas_reservas`.
4. WHEN el Lector_Mapeo devuelve el Mapeo_De_Reservas, THE Flujo_Encaje y THE Flujo_Motor SHALL obtener el mismo Ramo_De_Portafolio para una misma Etiqueta_De_Reserva.

### Requirement 9: Seguridad de acceso a datos del mapeo

**User Story:** Como ingeniero responsable de seguridad, quiero que el acceso a las tablas de mapeo sea seguro, para evitar inyección SQL y datos inválidos provenientes de tablas editables.

#### Acceptance Criteria

1. WHEN el Lector_Mapeo consulta la Tabla_Mapeo_Saldo o la Tabla_Mapeo_Interes, THE Lector_Mapeo SHALL usar exclusivamente queries parametrizadas.
2. THE Lector_Mapeo SHALL tratar cada fila leída de las tablas de mapeo como entrada NO confiable.
3. WHEN el Lector_Mapeo lee una fila, THE Lector_Mapeo SHALL validar que la Etiqueta_De_Reserva normalizada no sea vacía.
4. IF una fila de mapeo tiene una Etiqueta_De_Reserva vacía tras la Normalizacion_Etiqueta, THEN THE Lector_Mapeo SHALL descartar esa fila y registrar una advertencia genérica sin volcar el contenido de la fila.
5. THE Lector_Mapeo SHALL restringir las columnas leídas a una allowlist fija de nombres literales controlados por el código.
6. IF el Lector_Mapeo encuentra una fila inválida, THEN THE Lector_Mapeo SHALL continuar la corrida sin abortar.

### Requirement 10: Edición del mapeo desde el dashboard

**User Story:** Como analista de la Gerencia Financiera, quiero editar el mapeo de ramos desde el dashboard, para ajustar las correspondencias sin modificar código.

#### Acceptance Criteria

1. WHERE el usuario abre la sección de configuración de mapeo del Dashboard, THE Dashboard SHALL mostrar las entradas de la Tabla_Mapeo_Saldo y de la Tabla_Mapeo_Interes.
2. WHEN el usuario guarda una entrada de mapeo desde el Dashboard, THE AXIS SHALL persistir la entrada en la tabla de mapeo correspondiente mediante una query parametrizada.
3. WHEN el usuario guarda una Etiqueta_De_Reserva desde el Dashboard, THE AXIS SHALL aplicar Normalizacion_Etiqueta antes de persistirla como clave.
4. WHEN el usuario guarda una entrada de mapeo, THE Flujo_Encaje y THE Flujo_Motor SHALL usar el valor persistido en la siguiente lectura del Mapeo_De_Reservas.
5. IF el usuario guarda una entrada con Etiqueta_De_Reserva vacía tras la Normalizacion_Etiqueta, THEN THE AXIS SHALL rechazar la entrada y notificar el motivo al usuario.

### Requirement 11: Gestión de dependencias

**User Story:** Como ingeniero responsable del build, quiero que las dependencias estén pineadas a versión exacta, para asegurar builds reproducibles.

#### Acceptance Criteria

1. WHERE esta feature agregue o modifique dependencias, THE AXIS SHALL fijarlas a una versión exacta.
2. THE AXIS SHALL mantener el runtime en Python 3.12 o superior.
