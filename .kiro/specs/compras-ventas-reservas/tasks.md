# Implementation Plan: Compras y Ventas por Variación de Reservas

## Overview

Implementación en Python (coherente con el motor existente `scripts/motor_proyeccion.py`,
pandas y SQLite `portafolio.db`) de la lógica de compras/ventas de renta fija dirigidas
por la variación mensual de reserva por ramo. La lógica se aísla en un módulo nuevo
(`scripts/compras_ventas_reservas.py`) con funciones puras y testeables, se persiste el
input y la clasificación de ramos en tablas SQLite gestionadas desde la capa de
aplicación, y se integra en el loop mensual del motor en un único punto de enganche,
antes de la reinversión de cupones. Las pruebas de propiedad usan Hypothesis (versión
pineada en `requirements.txt`).

Cada tarea construye sobre las anteriores y termina cableando la funcionalidad en el
motor y el dashboard, sin código huérfano.

## Tasks

- [x] 1. Esquema de datos y helpers de acceso (tablas SQLite)
  - [x] 1.1 Crear tabla `input_variacion_reservas` en `init_db.py`
    - Añadir `CREATE TABLE IF NOT EXISTS input_variacion_reservas` con columnas `ramo TEXT`, `mes TEXT`, `delta_reserva REAL`, `fecha_carga TEXT`, `PRIMARY KEY (ramo, mes)`
    - No sembrar datos; solo el DDL de la tabla
    - _Requirements: 1.1, 1.2, 1.3, 1.5_

  - [x] 1.2 Implementar `asegurar_tabla_config_ramos(conn)` en `scripts/compras_ventas_reservas.py`
    - Crear el módulo nuevo `scripts/compras_ventas_reservas.py`
    - Implementar `asegurar_tabla_config_ramos` con `CREATE TABLE IF NOT EXISTS config_ramos_reserva` (`ramo TEXT PK`, `es_reserva_matematica INTEGER NOT NULL`, `instrumento TEXT`) desde la capa de aplicación (no en `init_db.py`), idempotente, sin eliminar ni sembrar datos
    - Reutilizar `get_conn()` de `comun/db.py` cuando aplique
    - _Requirements: 3.1, 4.1_

  - [x] 1.3 Escribir property test de idempotencia del esquema
    - **Property 22: Creación idempotente del esquema sin destruir datos**
    - **Validates: Requirements 3.1, 4.1**

- [x] 2. Enum, dataclass de configuración y normalización de ramos
  - [x] 2.1 Definir `InstrumentoCompra`, `_normalizar_ramo` y `ConfigRamosReserva`
    - Añadir el enum `InstrumentoCompra` con miembros `TES_UVR` y `TF`
    - Implementar `_normalizar_ramo(ramo)` como `str(ramo).strip().upper()`
    - Implementar el dataclass `ConfigRamosReserva` con `es_reserva_matematica_por_ramo`, `instrumento_por_ramo` y los métodos `esta_clasificado`, `es_reserva_matematica` (devuelve `None` si sin clasificar) e `instrumento`
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 2.2 Escribir unit tests de `ConfigRamosReserva` y normalización
    - Casos: ramo clasificado True/False, ramo sin clasificar (`None`), normalización de mayúsculas/espacios
    - _Requirements: 3.1, 3.2, 4.1_

- [x] 3. Carga y persistencia de la clasificación de ramos
  - [x] 3.1 Implementar `cargar_ramos_reserva_matematica(ruta_db)`
    - Asegurar el esquema (`asegurar_tabla_config_ramos`) antes de leer
    - Leer `config_ramos_reserva` con query parametrizada de solo lectura sobre allowlist de columnas (`ramo`, `es_reserva_matematica`, `instrumento`); nunca interpolar SQL
    - Tratar cada fila como entrada no confiable: normalizar ramo, interpretar booleano, validar instrumento contra `InstrumentoCompra` cuando es reserva matemática; descartar filas con ramo vacío o instrumento no reconocido (log de advertencia), sin abortar
    - Tabla vacía ⇒ `ConfigRamosReserva` vacío (sin default hardcodeado)
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 3.2 Escribir property test de carga sin default hardcodeado
    - **Property 20: Carga de la clasificación desde la tabla, sin default hardcodeado**
    - **Validates: Requirements 3.1, 3.2, 4.1**

  - [x] 3.3 Implementar `persistir_clasificacion_ramo(ramo, es_reserva_matematica, instrumento)`
    - Asegurar el esquema; validar allowlist de columnas, normalizar ramo (no vacío), booleano, exigir instrumento válido solo cuando `es_reserva_matematica` es True (NULL en caso contrario)
    - INSERT/UPSERT parametrizado; reintentar ante `database is locked` (backoff simple); agotados los reintentos, error genérico al UI y detalle en log estructurado sin PII
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 3.4 Escribir property test de round-trip de la clasificación
    - **Property 23: Round-trip de persistencia de la clasificación**
    - **Validates: Requirements 3.1, 3.2, 4.1**

  - [x] 3.5 Escribir unit tests de validación de la clasificación
    - Rechazo de claves extra, ramo vacío, instrumento no reconocido con reserva matemática True; aceptar NULL cuando es False; DB bloqueada mockeada ⇒ reintento y error genérico
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 3.6 Implementar esquema, dataclass y carga de parámetros de plazo configurables
    - Implementar `asegurar_tabla_parametros_reservas(conn)` con `CREATE TABLE IF NOT EXISTS config_parametros_reservas` (`clave TEXT PK`, `valor REAL NOT NULL`) desde la capa de aplicación (no en `init_db.py`), idempotente, sin eliminar ni sembrar datos
    - Definir el dataclass `ParametrosReservas` con `nodo_compra_no_matematica_anios=5` y `plazo_max_venta_anios=10` (defaults)
    - Implementar `cargar_parametros_reservas(ruta_db) -> ParametrosReservas`: asegurar el esquema, leer con query parametrizada sobre allowlist de claves; tratar cada valor como entrada no confiable (numérico y estrictamente positivo); aplicar el valor por defecto ante clave ausente, valor no numérico, no positivo o clave desconocida, sin abortar la corrida
    - _Requirements: 13.1, 13.2, 13.3, 13.6_

  - [x] 3.7 Implementar `persistir_parametros_reservas(nodo_compra_no_matematica_anios, plazo_max_venta_anios)`
    - Asegurar el esquema; validar allowlist de claves y que cada valor sea numérico y positivo; INSERT/UPSERT parametrizado; reintentar ante `database is locked` (backoff simple); agotados los reintentos, error genérico al UI y detalle en log estructurado sin PII
    - _Requirements: 13.3, 13.4_

  - [x] 3.8 Escribir property test de defaulting y equivalencia con el comportamiento fijo
    - **Property 29: Defaulting de parámetros y equivalencia con el comportamiento fijo**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.6**

  - [x] 3.9 Escribir unit tests de parámetros configurables
    - Defaults 5/10 con tabla vacía (13.2); valor inválido/no positivo ⇒ default (13.6); round-trip persistir/cargar de valor positivo; DB bloqueada mockeada ⇒ reintento y error genérico
    - _Requirements: 13.2, 13.4, 13.6_

- [x] 4. Cómputo de ramos pendientes por clasificar
  - [x] 4.1 Implementar `ramos_pendientes_por_clasificar(ramos_portafolio, config_ramos)`
    - Devolver ramos del portafolio (normalizados) sin fila en `config_ramos_reserva`
    - _Requirements: 3.1, 4.1_

  - [x] 4.2 Escribir property test de cómputo de pendientes
    - **Property 24: Pendientes = ramos del portafolio menos los ya clasificados**
    - **Validates: Requirements 3.1, 4.1**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Derivación desde el Excel, persistencia y carga del input de variación de reservas
  - [x] 6.1 Implementar `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)`
    - Leer el workbook Excel de dos hojas (`Saldo Reservas` e `Interés a la Reserva`); tratar el workbook como entrada NO confiable: validar los nombres de las hojas (ante hojas faltantes lanzar `ErrorInputReservas`), normalizar etiquetas de ramo (strip/upper), coercionar valores a numérico (`pd.to_numeric(errors='coerce')` ⇒ NaN tratado como 0.0), ignorar filas TOTAL de bloque y cabeceras (usar `TOTAL CAPI` SOLO para el agregado Capitalizadora)
    - Derivar la variación mes contra mes por compañía: Comerciales = `saldo(t) − saldo(t-1)` (sin interés); Bolívar = `(saldo(t) − saldo(t-1)) − (interés(t) − interés(t-1))` con interés 0 cuando falta la línea; Capitalizadora = ramo único `CAPITALIZADORA` derivado de `TOTAL CAPI` menos la línea de interés `Capitalizadora`
    - Aplicar la agrupación demás-ramos usando `ramos_portafolio` (del portafolio INICIAL, `portafolio_clasificado`, al inicio de la corrida): ramos de Bolívar/Comerciales presentes ⇒ ramo individual; ramos NO presentes ⇒ sumar por compañía en `DEMÁS RAMOS BOLÍVAR` / `DEMÁS RAMOS COMERCIALES`
    - Devolver un `DataFrame` con columnas EXACTAS `{ramo, mes, delta_reserva}` (ramos individuales, `DEMÁS RAMOS BOLÍVAR`/`DEMÁS RAMOS COMERCIALES` y `CAPITALIZADORA`), listo para `persistir_input_reservas`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 1.4_

  - [x] 6.2 Implementar `persistir_input_reservas(df, *, reintentos=3)`
    - Validar allowlist de columnas (`ramo`, `mes`, `delta_reserva`), rechazar claves extra, normalizar ramo (strip/upper), validar/normalizar mes, `delta_reserva` float finito (positivos y negativos)
    - Recibe el `DataFrame` ya derivado por `derivar_variaciones_reservas` (la forma de la tabla `input_variacion_reservas` no cambia)
    - Persistir con `to_sql`/query parametrizada; reintentar ante `database is locked`; lanzar `ErrorPersistenciaReservas` agotados los reintentos
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6_

  - [x] 6.3 Implementar `cargar_mapa_reservas(ruta_db, fecha_inicio, meses_horizonte)`
    - Devolver `{ramo: {mes_offset: delta_reserva}}`; ramos/meses sin registro NO se incluyen (consumidor asume Δ=0); `{}` si la tabla no existe o está vacía
    - _Requirements: 1.4, 10.3_

  - [x] 6.4 Escribir property test de derivación mes a mes por regla de compañía
    - **Property 27: Corrección de la derivación mes a mes por regla de compañía**
    - **Validates: Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.10**

  - [x] 6.5 Escribir property test de conservación de la agrupación "demás ramos"
    - **Property 28: Conservación de la agrupación "demás ramos" por compañía**
    - **Validates: Requirements 12.7, 12.8, 12.9**

  - [x] 6.6 Escribir unit tests de la derivación del Excel
    - Parse estructural del workbook de ejemplo (`Inputs_Reservas e interes a la reserva.xlsx`) produce columnas `{ramo, mes, delta_reserva}` (12.1); workbook con hojas faltantes ⇒ `ErrorInputReservas`; ramo de Bolívar sin línea de interés ⇒ variación = Δsaldo (12.5); agregado `CAPITALIZADORA` derivado de `TOTAL CAPI` − interés Capitalizadora (12.6)
    - _Requirements: 12.1, 12.5, 12.6_

  - [x] 6.7 Escribir property test de round-trip del input de reservas
    - **Property 1: Round-trip de persistencia del input de reservas**
    - **Validates: Requirements 1.1**

  - [x] 6.8 Escribir unit tests de persistencia del input
    - Rechazo de registros con campos faltantes/extra (1.2); DB bloqueada mockeada ⇒ reintento y luego `ErrorPersistenciaReservas` (1.6)
    - _Requirements: 1.2, 1.6_

- [x] 7. Universo de compra desde `hist_pip` y selección de títulos
  - [x] 7.1 Implementar `UniversoCompra` y `cargar_universo_compra(ruta_db, fecha_pip)`
    - Dataclass con `tes_uvr` (TUVT) y `tf`; cargar desde `hist_pip` con query parametrizada (nemo LIKE 'TUVT%' para UVR)
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 7.2 Implementar selectores de título de compra
    - `seleccionar_tes_uvr_largo` (mayor plazo), `seleccionar_tf_largo` (mayor plazo TF), `seleccionar_tes_uvr_5y` (nodo objetivo configurable `nodo_compra_no_matematica_anios` en lugar del literal 5; si no hay nodo exacto, el de plazo mayor más cercano al nodo objetivo)
    - _Requirements: 3.1, 3.2, 4.1, 4.2, 13.7_

  - [x] 7.3 Escribir property test de selección de plazo máximo (reserva matemática)
    - **Property 6: Selección de plazo máximo para ramos de reserva matemática**
    - **Validates: Requirements 3.1, 3.2**

  - [x] 7.4 Escribir property test de selección del nodo de compra configurable (demás ramos)
    - **Property 7: Selección del nodo de compra configurable para los demás ramos**
    - **Validates: Requirements 4.1, 4.2, 13.7**

- [x] 8. Lógica de compra por reserva
  - [x] 8.1 Implementar `comprar_por_reserva(ramo, monto, fecha_mes, universo_compra, config_ramos, parametros, ...)`
    - Resolver instrumento/clasificación por dato: reserva matemática ⇒ DV + instrumento de mayor plazo (TES UVR o TF); no matemática ⇒ Negociable + TES UVR al nodo objetivo `parametros.nodo_compra_no_matematica_anios` (en lugar del literal 5)
    - Crear posición nueva (`id_posicion` único, no aumentar nominal existente), heredar ramo/compañía/portafolio, `valor_mercado == monto == Δ`, valorada a `fecha_mes` con `valorar_titulo`
    - Fallback de mercado ausente (Req 4.6): usar tasa/fecha por defecto sin interrumpir
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.3, 4.4, 4.5, 4.6, 13.7_

  - [x] 8.2 Escribir property test de corrección de la compra
    - **Property 5: Corrección de la compra (instrumento, plazo, clasificación, metadatos, fecha)**
    - **Validates: Requirements 3.1, 3.2, 3.5, 3.6, 4.4, 4.5**

  - [x] 8.3 Escribir property test del monto de la compra
    - **Property 8: El monto de la compra iguala la variación positiva de reserva**
    - **Validates: Requirements 3.3, 4.3**

  - [x] 8.4 Escribir property test de posición nueva
    - **Property 9: La compra es una posición nueva**
    - **Validates: Requirements 3.4, 9.2**

  - [x] 8.5 Escribir unit test de fallback de mercado ausente
    - Valoración con datos por defecto sin crash cuando falta `rendimiento`/fecha
    - _Requirements: 4.6_

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Lógica de venta (waterfall) y selección de títulos a vender
  - [x] 10.1 Implementar `PasoWaterfall` y selección de títulos a vender
    - Enum `PasoWaterfall` (MONTHLY_LIQUIDITY, NEGOCIABLES, DV)
    - `seleccionar_titulo_a_vender(candidatos, monto_pendiente, plazo_max_venta_anios)`: elige el elegible (plazo ≤ `plazo_max_venta_anios`, configurable, en lugar del literal 10y) que minimiza `|valor_mercado − pendiente|`
    - `ordenar_dv_por_ori(candidatos_dv, fecha_venta)`: orden descendente por ORI (`vm − vtir`), excluye títulos con 0 días al vencimiento
    - _Requirements: 6.1, 6.3, 7.1, 7.2, 7.3, 13.7_

  - [x] 10.2 Implementar `vender_por_reserva(ramo, monto_a_cubrir, portafolio_ramo, liquidez_mes_ramo, fecha_mes, parametros, ...)`
    - Cascada estricta: (1) MONTHLY_LIQUIDITY (solo consume liquidez del mes en este paso), (2) Negociables RF con plazo ≤ `parametros.plazo_max_venta_anios`, (3) DV RF con plazo ≤ `parametros.plazo_max_venta_anios` (plazo máximo configurable en lugar del literal 10y); detenerse apenas se cubra el monto
    - Retención parcial en el paso MONTHLY_LIQUIDITY: retener (no reinvertir) SOLO `min(liquidez_mes_ramo, monto_a_cubrir)`; el excedente de liquidez del mes por encima de la disminución NO se bloquea (queda para reinversión normal). La regla es "no reinvertir hasta completar la disminución", no "no reinvertir nada del mes"
    - Sin orden de prelación dentro del paso 1: tratar la liquidez del mes (vencimientos + amortizaciones + cupones) como monto agregado; el monto cubierto y la liquidez retenida son independientes del orden de consumo de esos flujos
    - Vender títulos completos; retirarlos del portafolio y sumar `valor_mercado` a la liquidez del ramo; en DV, si requerido excede disponible, vender exactamente lo disponible
    - Devolver `ResultadoVenta` con liquidez consumida, títulos vendidos y ORI reciclado (suma de `vm − vtir` de DV vendidos; Negociables aportan cero)
    - En el paso MONTHLY_LIQUIDITY, identificar y reportar la porción del monto RETENIDO que corresponde a pagos de cupón de RF del ramo en el mes, determinada por monto/proporción (no por orden), acotada a `min(cupon_mes_ramo, liquidez_consumida[ramo])` (a partir de `cupones_por_titulo_mes` / la liquidez de cupón del ramo): ese cupón realizado NO se reinvierte y debe exponerse para que el motor lo sume al P&L (invariante de no-pérdida)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 6.2, 6.4, 8.1, 8.2, 8.3, 11.2, 13.7_

  - [x] 10.10 Escribir property test de independencia del orden de consumo en el paso de liquidez del mes
    - **Property 26: El monto cubierto por el paso de liquidez del mes es independiente del orden de consumo**
    - **Validates: Requirements 5.10**

  - [x] 10.3 Escribir property test del orden y terminación del waterfall
    - **Property 10: Orden y terminación del waterfall de ventas**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.5, 5.7**

  - [x] 10.4 Escribir property test de elegibilidad por plazo y cobertura exacta
    - **Property 11: Elegibilidad por plazo y cobertura exacta en ventas**
    - **Validates: Requirements 5.4, 5.6, 6.3, 13.7**

  - [x] 10.5 Escribir property test de selección del título más cercano al pendiente
    - **Property 12: Selección del título cuyo valor se acerca más al monto pendiente**
    - **Validates: Requirements 6.1**

  - [x] 10.6 Escribir property test de venta de títulos completos
    - **Property 13: Venta de títulos completos**
    - **Validates: Requirements 6.2**

  - [x] 10.7 Escribir property test de conservación de liquidez al vender
    - **Property 14: Conservación de liquidez al vender**
    - **Validates: Requirements 6.4**

  - [x] 10.8 Escribir property test de ordenamiento DV por ORI y exclusión de 0 días
    - **Property 15: Ordenamiento de DV por ORI descendente y exclusión de 0 días**
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [x] 10.9 Escribir property test de reciclaje de ORI al P&L realizado
    - **Property 16: Reciclaje de ORI de DV al P&L realizado**
    - **Validates: Requirements 8.1, 8.2, 8.3**

  - [x] 10.11 Escribir property test de uso de los valores configurados en selección y elegibilidad
    - **Property 30: La selección y la elegibilidad usan los valores configurados**
    - **Validates: Requirements 13.4, 13.7**

- [x] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Orquestación por mes
  - [x] 12.1 Implementar `ResultadoCompraVenta` y `aplicar_compras_ventas_reservas(...)`
    - Dataclass `ResultadoCompraVenta` (portafolio, liquidez_consumida por ramo, ori_reciclado, `cupones_no_reinvertidos: dict[str, float]`, compras, ventas) y ramos omitidos por clasificación pendiente
    - Recibir `parametros: ParametrosReservas` (nodo de compra y plazo máximo de venta configurables) y propagarlo a `comprar_por_reserva` y `vender_por_reserva` en lugar de literales 5/10
    - Para cada ramo con `Δreserva(t-1) != 0` de forma independiente: si no está clasificado ⇒ omitir con advertencia; si clasificado y Δ>0 ⇒ `comprar_por_reserva`; Δ<0 ⇒ `vender_por_reserva`; Δ=0/ausente ⇒ no-op
    - Consultar mes caído `mapa[ramo].get(t-1, 0.0)`
    - Poblar `cupones_no_reinvertidos[ramo]` con `cupones_detectados(ramo, mes) − cupones_reinvertidos(ramo, mes)`, reutilizando el seguimiento existente del motor (`cupones_por_titulo_mes` / `cupones_cobrados_mes`, `n_reinversiones_mes`) y la `liquidez_consumida` por ramo del waterfall; NO inventar un mecanismo paralelo (solo la porción no reinvertida, para no doble contar)
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 9.1, 9.2, 9.3, 11.2, 11.3, 13.4, 13.7_

  - [x] 12.2 Escribir property test de la convención de mes caído
    - **Property 3: Convención de mes caído**
    - **Validates: Requirements 2.1**

  - [x] 12.3 Escribir property test de interpretación mes a mes
    - **Property 2: La variación se interpreta mes a mes (no acumulada)**
    - **Validates: Requirements 1.3**

  - [x] 12.4 Escribir property test del mapeo de decisión por signo
    - **Property 4: Mapeo de decisión por signo de la variación**
    - **Validates: Requirements 2.3, 2.4, 2.5, 1.4**

  - [x] 12.5 Escribir property test de independencia por ramo
    - **Property 17: Independencia por ramo**
    - **Validates: Requirements 9.1, 9.3**

  - [x] 12.6 Escribir property test de omisión de ramos sin clasificar
    - **Property 21: Los ramos sin clasificar con variación ≠ 0 se omiten con advertencia**
    - **Validates: Requirements 2.3, 2.4, 3.1, 4.1**

- [x] 13. Integración en el loop mensual del motor
  - [x] 13.1 Cablear la carga de reservas, universo, clasificación y parámetros en `proyectar_portafolio()`
    - Antes del loop: cargar `mapa_reservas`, `universo_compra`, `config_ramos` (`cargar_ramos_reserva_matematica`) y `parametros` (`cargar_parametros_reservas`) una vez por corrida
    - _Requirements: 10.3, 13.3, 13.4_

  - [x] 13.2 Insertar el punto de enganche en el loop mensual del motor
    - Tras detectar cupones/vencimientos y antes de la reinversión: llamar `aplicar_compras_ventas_reservas` (pasando `parametros`) solo si `mapa_reservas` no está vacío; actualizar `portafolio_activo`, restar `liquidez_consumida` de los flujos reinvertibles del ramo, acumular `ori_reciclado_mes` al P&L realizado
    - Acumular `cupones_no_reinvertidos_mes += sum(resultado_cvr.cupones_no_reinvertidos.values())` y, en el bloque "── 4. Calcular P&L ──", sumar esa corrección como partida explícita TANTO a `pnl_mercado_mes` COMO a `pnl_tir_mes`, como término distinto e independiente del ORI reciclado (Req 8); cuando no hay disminución de reserva la corrección es cero y el P&L es idéntico al del motor actual
    - Emitir advertencia agregada de ramos pendientes por clasificar
    - Envolver compra y reinversión en `try/except` independientes; ante fallo de decisión, continuar con reinversión normal
    - _Requirements: 2.2, 2.6, 8.2, 8.4, 10.1, 10.2, 11.1, 11.4, 11.5_

  - [x] 13.3 Escribir property test de liquidez consumida no reinvertida (retención parcial)
    - **Property 18: La liquidez consumida por el waterfall no se reinvierte (retención parcial)**
    - **Validates: Requirements 10.2, 5.9**

  - [x] 13.4 Escribir property test de retrocompatibilidad (no-op sin input)
    - **Property 19: Retrocompatibilidad (no-op sin input)**
    - **Validates: Requirements 10.3**

  - [x] 13.5 Escribir integration tests del loop del motor
    - Compras/ventas antes de la reinversión (2.2); ante fallo de decisión la reinversión continúa (2.6); con Δ>0 coexisten reinversión y compra adicional (10.1); fallo de reinversión no revierte la compra (10.4); corrida con tabla de reservas vacía produce resultados idénticos a la línea base (10.3)
    - Cupón desviado al waterfall contabilizado en el P&L: un ramo con Δreserva<0 cuya liquidez del mes incluye cupón consumido suma ese cupón realizado al P&L (mercado y TIR) (11.2); sin doble conteo cuando todo se reinvierte o Δreserva≥0: la corrección es cero y el P&L es idéntico a reinvertir el cupón (11.3)
    - _Requirements: 2.2, 2.6, 10.1, 10.3, 10.4, 11.2, 11.3_

  - [x] 13.6 Escribir property test de invariancia del P&L del cupón bajo reinversión vs. consumo por el waterfall
    - **Property 25: Invariancia del P&L del cupón bajo reinversión vs. consumo por el waterfall (no-pérdida, sin doble conteo)**
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 5.8, 8.4, 10.2**

- [x] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Integración con el dashboard (`app_dash.py`)
  - [x] 15.1 Añadir callback de carga del Excel de reservas (derivación + persistencia)
    - Callback del `dcc.Upload` que recibe el workbook Excel de dos hojas, llama `derivar_variaciones_reservas(ruta_excel_o_bytes, ramos_portafolio)` usando los ramos del portafolio INICIAL (`portafolio_clasificado`) y luego persiste la variación derivada con `persistir_input_reservas`
    - Capturar `ErrorInputReservas` (workbook inválido) y `ErrorPersistenciaReservas`, notificando mensaje genérico en UI (detalle en log, sin PII)
    - _Requirements: 1.1, 1.6, 12.1_

  - [x] 15.2 Añadir pestaña *Ajustes* de clasificación de ramos
    - Asegurar el esquema (`asegurar_tabla_config_ramos`), mostrar solo ramos pendientes (`ramos_pendientes_por_clasificar`), incluyendo los grupos derivados (`DEMÁS RAMOS BOLÍVAR`, `DEMÁS RAMOS COMERCIALES`, `CAPITALIZADORA`); persistir la clasificación del usuario (`persistir_clasificacion_ramo`) con query parametrizada; un ramo clasificado desaparece de la lista
    - _Requirements: 3.1, 3.2, 4.1_

  - [x] 15.4 Añadir panel avanzado/colapsable de parámetros de plazo en *Ajustes*
    - Sección avanzada u oculta por defecto (panel colapsable) con dos inputs numéricos (`nodo_compra_no_matematica_anios`, `plazo_max_venta_anios`); asegurar `config_parametros_reservas` (`asegurar_tabla_parametros_reservas`) y precargar los valores vigentes con `cargar_parametros_reservas`; al guardar persistir con `persistir_parametros_reservas`, notificando mensaje genérico en UI ante fallo
    - _Requirements: 13.1, 13.5_

  - [x] 15.3 Escribir unit tests de los callbacks del dashboard
    - Persistencia del input con error notificado; tras persistir clasificación el ramo sale de pendientes
    - Panel de parámetros: defaults 5/10 con tabla vacía (13.2); valor inválido ⇒ default (13.6); round-trip de valores editados persistidos; los valores editados se usan en la selección/elegibilidad de la siguiente corrida (13.4, 13.7)
    - _Requirements: 1.6, 3.1, 4.1, 13.2, 13.4, 13.6, 13.7_

- [x] 16. Pineado de dependencia y checkpoint final
  - [x] 16.1 Pinear Hypothesis en `requirements.txt`
    - Añadir Hypothesis con versión exacta (sin `^`, `~`, `*`, `latest`), verificando disponibilidad en el repositorio institucional
    - _Requirements: (soporte de testing para todas las propiedades)_

  - [x] 16.2 Final checkpoint - Ensure all tests pass
    - Ensure all tests pass, ask the user if questions arise.

## Notes

- Las tareas marcadas con `*` son opcionales (tests) y pueden omitirse para un MVP más rápido.
- Cada tarea referencia requisitos específicos para trazabilidad; los property tests referencian su propiedad del diseño.
- La lógica nueva vive en `scripts/compras_ventas_reservas.py` (funciones puras); el motor y el dashboard solo la cablean.
- Toda lectura/escritura SQL usa queries parametrizadas y allowlist de columnas; el input del dashboard y las filas de `config_ramos_reserva` se tratan como entrada no confiable.
- `config_ramos_reserva` y `config_parametros_reservas` se crean de forma idempotente desde la capa de aplicación (no en `init_db.py`); `config_ramos_reserva` arranca vacía (sin default hardcodeado) y `config_parametros_reservas` aplica defaults 5/10 en la lectura.
- El input de reservas es un Excel de dos hojas (`Saldo Reservas` e `Interés a la Reserva`) tratado como entrada no confiable; `derivar_variaciones_reservas` deriva la Δreserva por ramo/grupo antes de persistir, y la forma de `input_variacion_reservas` no cambia.
- Se implementa una prueba property-based por cada una de las 30 propiedades del diseño usando Hypothesis (mínimo 100 iteraciones).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.1"] },
    { "id": 1, "tasks": ["1.3", "2.2", "3.1", "3.6", "6.1", "7.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "3.7", "6.2", "7.2"] },
    { "id": 3, "tasks": ["3.4", "3.5", "3.8", "3.9", "4.1", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "7.3", "7.4", "8.1"] },
    { "id": 4, "tasks": ["4.2", "8.2", "8.3", "8.4", "8.5", "10.1"] },
    { "id": 5, "tasks": ["10.2", "10.3", "10.4", "10.5", "10.6", "10.7", "10.8", "10.9", "10.10", "10.11"] },
    { "id": 6, "tasks": ["12.1"] },
    { "id": 7, "tasks": ["12.2", "12.3", "12.4", "12.5", "12.6", "13.1"] },
    { "id": 8, "tasks": ["13.2"] },
    { "id": 9, "tasks": ["13.3", "13.4", "13.5", "13.6", "15.1", "15.2", "15.4", "16.1"] },
    { "id": 10, "tasks": ["15.3"] }
  ]
}
```
