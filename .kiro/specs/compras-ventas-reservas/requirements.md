# Requirements Document

## Introduction

Esta funcionalidad integra en el motor de proyección de renta fija (`scripts/motor_proyeccion.py`)
la ejecución automática de compras y ventas de títulos de renta fija dirigidas por la
variación mensual de la reserva de cada ramo. El cambio de reserva por ramo es un input
que el usuario arrastra en el dashboard y que queda persistido en una tabla de la base
de datos. Cada mes, por ramo, el motor decide:

- **Comprar** títulos de renta fija si la reserva del ramo aumentó.
- **Vender** títulos de renta fija (en cascada) si la reserva del ramo disminuyó.

El objetivo es que el portafolio proyectado refleje el efecto de los movimientos de
reserva sobre la composición y el P&L, respetando reglas contables de clasificación
(Negociable, Disponible para la Venta / DV, Vencimiento) y el reciclaje del ORI al vender
títulos DV.

### Alcance y supuestos base

- Aplica **solo a Renta Fija (RF)**. Renta variable, alternativos y créditos NO se ven
  afectados por el cambio de reservas.
- La lógica está **siempre activa** en la proyección de RF (no es un modo opcional).
- Cada ramo se procesa de forma **independiente**: en un mismo mes un ramo puede comprar
  y otro vender.
- **Convención de mes caído**: la reserva se maneja con un rezago de un mes. Al inicio del
  mes `t`, se evalúa la variación de reserva del mes `t-1` para decidir las operaciones a
  ejecutar durante el mes `t`. Esta convención (evaluar al inicio del mes) se elige sobre
  la alternativa de revertir reinversiones al cierre porque implica menos reproceso.
- Las operaciones se evalúan **antes de la reinversión de cupones** del mes.
- El P&L del portafolio se sigue calculando **todo a mercado** (consistente con el resto
  del motor); al vender un DV, su ORI acumulado se recicla al P&L realizado.

## Glossary

- **Reserva**: pasivo técnico de un ramo. Su variación mensual (Δreserva) es el driver de
  compras/ventas.
- **Saldo de reserva**: nivel (monto) mensual de la reserva de un ramo tal como aparece en
  la hoja "Saldo Reservas" del input Excel. Está organizado en bloques por compañía
  (BOLIVAR, COMERCIALES, CAPITALIZADORA) con los ramos como filas y los 12 meses
  (Enero..Diciembre) como columnas, e incluye filas TOTAL por bloque (TOTAL BOLIVAR,
  TOTAL COMERCIALES, TOTAL CAPI, TOTAL COMPAÑIAS). Es un nivel, no una variación.
- **Interés a la reserva**: monto mensual del interés reconocido sobre la reserva, tomado de
  la hoja "Interés a la Reserva" del input Excel. Existe para líneas de Bolívar (p. ej.
  PENSIONES LEY 100, PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA, RIESGOS
  PROFESIONALES-MATEMATICA, PREVISIONAL - NEGOCIO PERÚ, VIDA INDIVIDUAL - MATEMÁTICA) y una
  línea "Capitalizadora", con los 12 meses como columnas. Comerciales NO tiene líneas de
  interés (se asume interés cero).
- **Δreserva(ramo, t-1)**: variación de la reserva del ramo entre el mes t-2 y el mes t-1.
  Es una cantidad DERIVADA (ya no un input directo): se calcula a partir de los niveles de
  saldo (y de interés cuando aplique) según las reglas del Requirement 12.
- **DEMÁS RAMOS &lt;compañía&gt;**: ramo agregado por compañía que agrupa la suma de las
  variaciones de los ramos de esa compañía que NO están presentes en el portafolio activo.
  Se materializa como "DEMÁS RAMOS BOLÍVAR" y "DEMÁS RAMOS COMERCIALES". Se clasifica y
  opera como cualquier otro ramo.
- **CAPITALIZADORA (ramo agregado)**: ramo único que representa a toda la compañía
  Capitalizadora. Sus ramos individuales del saldo NO se usan; su variación se deriva del
  TOTAL CAPI del saldo menos la línea "Capitalizadora" de interés. Se clasifica y opera
  como cualquier otro ramo.
- **Ramo de reserva matemática**: uno de {Rentas Vitalicias, Rentas Voluntarias, Vida
  Individual Matemática, Conmutación Pensional, Riesgos Profesionales} (campo `ramo`).
- **DV**: Disponible para la Venta (clasificación contable "Disp. Venta" / "1DV"). Sus
  cambios de valor de mercado se acumulan en el ORI (patrimonio) hasta la venta.
- **Negociable**: clasificación contable "Negociable" / "1CP". Cambios de mercado van al P&L.
- **Vencimiento**: clasificación contable "Vencimiento" / "1VC".
- **ORI**: Otro Resultado Integral. Para un título DV se aproxima como
  `ORI = valor_mercado − valor_tir` en el motor.
- **TES UVR (TUVT)**: título de deuda pública indexado a UVR (nemotécnico inicia por TUVT).
- **Liquidez del mes (RF)**: suma de vencimientos + amortizaciones + pagos de cupón de RF
  del ramo en el mes.
- **Ingreso por cupón**: monto del cupón pagado por un título de RF que el motor ya reconoce
  en el P&L. La invariante de no-pérdida (Req 11) exige que ese reconocimiento se conserve
  aunque el flujo no se reinvierta por consumirse en el waterfall (el motor no modela caja).
- **Waterfall de ventas**: orden de prelación para generar liquidez cuando la reserva baja.
- **Nodo de compra (parámetro configurable)**: plazo al vencimiento objetivo para la compra de
  ramos que no son de reserva matemática (por defecto 5 años). Editable desde el dashboard
  (Requirement 13).
- **Plazo máximo de venta (parámetro configurable)**: plazo al vencimiento máximo elegible para
  vender títulos en el waterfall (por defecto 10 años). Editable desde el dashboard
  (Requirement 13).
- **Portafolio inicial**: el portafolio de partida de la corrida (portafolio_clasificado) antes
  de proyectar el primer mes. Es la referencia para decidir qué ramos son individuales y cuáles
  se agrupan en "demás ramos"; no se usa el estado del portafolio al final del horizonte.

## Requirements

### Requirement 1: Input de reservas (saldo e interés) y persistencia de la variación derivada por ramo

**User Story:** Como analista de inversiones, quiero cargar desde el dashboard el input de
reservas —un archivo Excel con el saldo mensual de reservas y el interés a la reserva— y
que el sistema derive y persista la variación mensual de reserva por ramo en la base de
datos, para que el motor de proyección la use como driver de compras y ventas.

#### Acceptance Criteria

1. WHEN el usuario arrastra/carga el input de reservas en el dashboard THEN el sistema SHALL leer el archivo Excel de reservas (con la hoja de saldo de reserva y la hoja de interés a la reserva), derivar la variación mensual por ramo y persistir la variación derivada en una tabla de la base de datos.
2. THE input de reservas SHALL ser un archivo Excel de dos hojas (Saldo de reserva e Interés a la reserva) que contiene niveles mensuales (no una variación pre-calculada); THE registro persistido resultante SHALL contener, por cada ramo/grupo y mes, la variación de reserva (Δreserva) derivada de ese mes respecto al mes anterior.
3. THE variación de reserva persistida SHALL interpretarse como un valor mes a mes (no un nivel absoluto acumulado), derivado como la diferencia mes contra mes del saldo (y del interés cuando aplique) según las reglas del Requirement 12.
4. WHERE un ramo no tenga fila de saldo (o de interés) para un mes dado THEN el sistema SHALL asumir el valor cero para ese ramo y mes al calcular la variación derivada.
5. THE sistema SHALL soportar valores de variación derivada positivos (aumento de reserva) y negativos (disminución de reserva).
6. IF la lectura del archivo Excel, la derivación de la variación o la persistencia del input de reservas falla THEN el sistema SHALL notificar el error al usuario y/o reintentar automáticamente la operación de persistencia.

### Requirement 2: Convención de mes caído y momento de evaluación

**User Story:** Como analista, quiero que la evaluación de compras/ventas respete la
convención de mes caído (reserva rezagada un mes), para que el portafolio de cierre de un
mes refleje la reserva del mes anterior, tal como ocurre en la operación real.

#### Acceptance Criteria

1. WHEN el motor inicia el procesamiento del mes `t` THEN el sistema SHALL evaluar la variación de reserva correspondiente al mes `t-1` para cada ramo.
2. THE decisión de comprar o vender de un ramo SHALL tomarse antes de ejecutar la reinversión de cupones de ese mes.
3. IF la variación de reserva del mes `t-1` de un ramo es positiva THEN el sistema SHALL ejecutar compras para ese ramo durante el mes `t` Y SHALL abstenerse de ejecutar ventas por reserva para ese ramo en el mes `t`.
4. IF la variación de reserva del mes `t-1` de un ramo es negativa THEN el sistema SHALL ejecutar ventas para ese ramo durante el mes `t`.
5. IF la variación de reserva del mes `t-1` de un ramo es cero THEN el sistema SHALL no ejecutar ni compras ni ventas por reserva para ese ramo en el mes `t`.
6. IF el sistema no logra tomar la decisión de comprar o vender a tiempo THEN el sistema SHALL proceder con la reinversión de cupones aunque no exista la decisión.

### Requirement 3: Compras cuando la reserva aumenta (ramos de reserva matemática)

**User Story:** Como analista, quiero que cuando aumente la reserva de un ramo de reserva
matemática se compre el título de más largo plazo disponible clasificado como DV, para
replicar la política de inversión de calce de la compañía.

#### Acceptance Criteria

1. WHERE el ramo pertenece al conjunto de reserva matemática (Rentas Vitalicias, Rentas Voluntarias, Conmutación Pensional, Riesgos Profesionales) AND la reserva aumentó THEN el sistema SHALL crear una posición nueva del TES UVR (nemotécnico TUVT) de mayor plazo al vencimiento disponible en `hist_pip`, clasificado como Disponible para la Venta (DV).
2. WHERE el ramo es Vida Individual Matemática AND la reserva aumentó THEN el sistema SHALL crear una posición nueva del título Tasa Fija (TF) de mayor plazo al vencimiento disponible, clasificado como Disponible para la Venta (DV).
3. THE monto de la compra SHALL ser igual a la variación positiva de la reserva del mes `t-1` del ramo.
4. THE compra SHALL crearse como una posición nueva (no aumentar el nominal de un título existente).
5. THE título comprado SHALL asignarse al ramo, compañía y portafolio correspondientes al ramo cuya reserva aumentó.
6. THE compra SHALL valorarse a la fecha del mes `t` con las condiciones de mercado (curva/senda) vigentes para ese mes.

### Requirement 4: Compras cuando la reserva aumenta (demás portafolios)

**User Story:** Como analista, quiero que para los ramos que no son de reserva matemática
la compra se haga en un TES UVR de nodo 5 años negociable, para mantener liquidez y una
duración intermedia.

#### Acceptance Criteria

1. WHERE el ramo NO pertenece al conjunto de reserva matemática AND la reserva aumentó THEN el sistema SHALL crear una posición nueva de un TES UVR (nemotécnico TUVT) con plazo al vencimiento cercano al nodo objetivo configurable (por defecto 5 años, Requirement 13), clasificado como Negociable.
2. IF no existe un TES UVR con plazo al vencimiento exactamente igual al nodo objetivo (por defecto 5 años) en `hist_pip` THEN el sistema SHALL seleccionar el TES UVR con el plazo al vencimiento mayor más cercano al nodo objetivo (opción de plazo más largo, no la opción más cercana en cualquier dirección).
3. THE monto de la compra SHALL ser igual a la variación positiva de la reserva del mes `t-1` del ramo.
4. THE compra SHALL crearse como una posición nueva clasificada como Negociable, asignada al ramo, compañía y portafolio correspondientes.
5. THE compra SHALL valorarse a la fecha del mes `t` con las condiciones de mercado vigentes para ese mes.
6. IF las condiciones de mercado no están disponibles O la fecha de valoración no puede fijarse en el mes `t` THEN el sistema SHALL proceder con la valoración usando los datos disponibles o una fecha por defecto.

### Requirement 5: Ventas cuando la reserva disminuye — orden de cascada (waterfall)

**User Story:** Como analista, quiero que cuando disminuya la reserva de un ramo se genere
la liquidez requerida siguiendo un orden de prelación definido, para minimizar el impacto
en el portafolio y realizar primero la liquidez natural.

#### Acceptance Criteria

1. WHEN la reserva de un ramo disminuye en el mes `t-1` THEN el sistema SHALL cubrir la disminución generando liquidez del ramo en el siguiente orden: (1) liquidez del mes de RF, (2) venta de Negociables RF, (3) venta de DV RF.
2. WHILE el paso actual de la cascada es el de liquidez del mes (MONTHLY_LIQUIDITY) THEN el sistema SHALL usar la liquidez de RF generada en el mes por vencimientos, amortizaciones (reintegros) y pagos de cupón, reteniéndola (absteniéndose de reinvertirla) únicamente hasta cubrir el monto de la disminución de reserva; el excedente de liquidez del mes por encima de ese monto SHALL reinvertirse normalmente. La lógica del paso 1 SHALL aplicarse únicamente cuando el paso actual es MONTHLY_LIQUIDITY.
3. IF la liquidez del mes no alcanza a cubrir la disminución THEN el sistema SHALL proceder al paso 2 (venta de Negociables RF).
4. THE paso 2 SHALL vender títulos Negociables de RF con plazo al vencimiento menor o igual al plazo máximo configurable (por defecto 10 años, Requirement 13), excluyendo los de plazo mayor a ese máximo, vendiendo exactamente el monto calculado necesario para cubrir la brecha de liquidez pendiente.
5. IF la venta de Negociables no alcanza a cubrir la disminución THEN el sistema SHALL proceder al paso 3 (venta de DV RF).
6. THE paso 3 SHALL vender títulos DV de RF con plazo al vencimiento menor o igual al plazo máximo configurable (por defecto 10 años, Requirement 13), excluyendo los de plazo mayor a ese máximo; IF el monto requerido excede lo disponible THEN el sistema SHALL vender exactamente el monto disponible.
7. THE proceso de venta SHALL detenerse tan pronto la liquidez acumulada cubra la disminución de reserva del ramo.
8. WHEN los pagos de cupón de RF se consumen en el paso de liquidez del mes para cubrir la disminución de reserva THEN el sistema SHALL preservar el reconocimiento de esos cupones en el P&L existente, de modo que consumirlos en el waterfall en lugar de reinvertirlos no los elimine del P&L (invariante de no-pérdida del Requirement 11).
9. WHERE la liquidez del mes de RF del ramo excede el monto de la disminución de reserva THEN el sistema SHALL retener exactamente el monto de la disminución y reinvertir el excedente, sin retener la totalidad de la liquidez del mes.
10. THE consumo de la liquidez del mes en el paso MONTHLY_LIQUIDITY SHALL tratarse como un monto agregado a cubrir; el sistema SHALL no imponer un orden de prelación entre los distintos flujos del mes (cupones, vencimientos, amortizaciones), de modo que el monto cubierto sea independiente del orden en que se consuman esos flujos.

### Requirement 6: Selección de títulos a vender

**User Story:** Como analista, quiero que la venta de títulos elija el título cuyo valor se
acerque más al monto por cubrir y venda títulos completos, para simplificar la operación y
evitar la complejidad de ventas parciales.

#### Acceptance Criteria

1. WHEN el sistema deba vender un título para cubrir un monto pendiente THEN el sistema SHALL seleccionar el título (elegible, con plazo ≤ plazo máximo configurable, por defecto 10 años) cuyo valor de mercado se acerque más al monto pendiente por cubrir.
2. THE venta SHALL ser del título completo (no venta parcial de una porción del nominal).
3. THE sistema SHALL excluir de la venta todos los títulos con plazo al vencimiento mayor al plazo máximo configurable (por defecto 10 años, Requirement 13).
4. WHEN se vende un título THEN el sistema SHALL retirarlo del portafolio proyectado y sumar a la liquidez generada del ramo un monto exactamente igual a su valor de mercado.

### Requirement 7: Orden de venta de DV por menor pérdida en ORI

**User Story:** Como analista, quiero que al vender títulos DV se prefiera realizar primero
los de menor pérdida (o mayor ganancia) en el ORI, para minimizar el impacto negativo en
el patrimonio al reciclar el ORI al P&L.

#### Acceptance Criteria

1. WHEN el sistema deba vender títulos DV de RF THEN el sistema SHALL ordenarlos por su ORI acumulado de mayor a menor, vendiendo primero los de mayor ganancia y, cuando ningún título tenga ganancia, los de menor pérdida (el orden descendente por ORI cubre ambos casos con una sola regla).
2. THE sistema SHALL excluir de la venta de títulos DV los títulos con 0 días al vencimiento (ya vencidos o que vencen hoy).
3. THE ORI acumulado de un título DV SHALL calcularse como `valor_mercado − valor_tir` a la fecha de la venta.

### Requirement 8: Efecto en P&L al vender un título DV (reciclaje de ORI)

**User Story:** Como analista, quiero que al vender un título DV el ORI acumulado se
reconozca en el P&L realizado, para reflejar correctamente el impacto contable de la venta
(reclassification adjustment de NIIF).

#### Acceptance Criteria

1. WHEN se vende un título clasificado como DV THEN el sistema SHALL reciclar su ORI acumulado (`valor_mercado − valor_tir` al momento de la venta) al P&L realizado del portafolio.
2. THE P&L del portafolio SHALL seguir calculándose todo a mercado, y el ORI reciclado de las ventas de DV SHALL sumarse al P&L de la clasificación negociable/realizada, incrementando el P&L realizado de forma inmediata al momento de vender el título DV (sin requerir pasos de procesamiento adicionales).
3. THE venta de un título Negociable SHALL reconocer su cambio de valor de mercado directamente en el P&L (sin ORI que reciclar).
4. THE reconocimiento existente del ingreso por cupón de RF en el P&L SHALL preservarse cuando el cupón se consume en el waterfall en lugar de reinvertirse, sin que dicho consumo lo reduzca ni lo elimine (invariante de no-pérdida del Requirement 11).

### Requirement 9: Independencia por ramo y reflejo en el portafolio proyectado

**User Story:** Como analista, quiero que cada ramo procese sus compras/ventas de forma
independiente y que el portafolio proyectado refleje esos movimientos, para tener una
proyección fiel por ramo.

#### Acceptance Criteria

1. THE sistema SHALL procesar las compras/ventas de cada ramo de forma independiente en el mismo mes (un ramo puede comprar mientras otro vende).
2. WHEN se ejecuta una compra o venta de un ramo THEN el sistema SHALL reflejar el cambio en el portafolio proyectado de ese ramo (nueva posición para compras, retiro de posición para ventas).
3. THE liquidez generada o el título comprado de un ramo SHALL no afectar las decisiones ni el portafolio de otro ramo.

### Requirement 10: Integración con la reinversión de cupones existente

**User Story:** Como analista, quiero que la lógica de compras/ventas se integre con el
flujo de reinversión de cupones existente sin romperlo, para que el motor mantenga su
comportamiento actual cuando no hay cambios de reserva.

#### Acceptance Criteria

1. WHEN la variación de reserva de un ramo es positiva THEN el sistema SHALL ejecutar la reinversión de cupones normal Y ADEMÁS la compra por el monto del aumento de reserva (la compra es adicional a la reinversión).
2. WHEN la variación de reserva de un ramo es negativa THEN el sistema SHALL usar la liquidez del mes (que normalmente se reinvertiría) para cubrir la disminución antes de reinvertir, reteniendo únicamente el monto necesario para cubrir la disminución y reinvirtiendo el excedente de liquidez del mes, preservando el reconocimiento existente de los pagos de cupón contenidos en la liquidez retenida en el P&L (usar la liquidez en lugar de reinvertirla no elimina el ingreso por cupón del P&L — invariante de no-pérdida del Requirement 11).
3. WHERE no exista input de variación de reservas para el ramo/mes THEN el sistema SHALL comportarse como el motor actual (reinversión normal, sin compras/ventas por reserva).
4. IF la reinversión de cupones normal falla mientras la compra adicional tiene éxito THEN el sistema SHALL permitir que la compra adicional se complete, manteniendo la funcionalidad parcial.

### Requirement 11: Invariante de no-pérdida del cupón en el P&L al no reinvertir

**User Story:** Como analista, quiero que cuando la disminución de reserva impida reinvertir
los cupones de RF, esos cupones sigan contando en el P&L igual que si se hubieran
reinvertido, para que el resultado no se distorsione por el hecho de que el motor no modela
una cuenta de caja (en la operación real el cupón simplemente pasaría a caja).

**Nota:** El reconocimiento del ingreso por cupón en el P&L ya existe en el motor actual.
Este requisito NO introduce una capacidad nueva de cálculo de cupones; su propósito es
garantizar que ese reconocimiento se preserve cuando el flujo del cupón se desvía al
waterfall en lugar de reinvertirse.

#### Acceptance Criteria

1. THE P&L del portafolio por concepto de cupones de RF SHALL ser el mismo se reinviertan o no esos cupones; el destino del flujo del cupón (reinversión o consumo por el waterfall) SHALL no alterar el ingreso por cupón reconocido en el P&L.
2. WHEN un pago de cupón de RF se consume en el waterfall de ventas para cubrir una disminución de reserva (en lugar de reinvertirse) THEN el sistema SHALL sumar el monto de ese cupón realizado al P&L del mes como una partida explícita, además del cambio de valor de mercado del portafolio (`vm_total(t) − vm_total(t-1)`), de modo que el flujo no se "pierda" por no existir un título de reinversión que lo represente.
3. THE corrección del P&L SHALL sumar únicamente los cupones detectados en el mes que NO fueron reinvertidos (cupón detectado menos cupón reinvertido), de modo que los cupones que sí se reinvierten —cuyo monto ya entra al P&L a través del valor de mercado del título de reinversión— NO se contabilicen dos veces.
4. THE corrección por cupón realizado no reinvertido SHALL aplicarse tanto al P&L a mercado (`pnl_mercado`) como al P&L a TIR (`pnl_tir`).
5. THE ausencia de una cuenta de caja explícita en el motor SHALL no provocar la pérdida del ingreso por cupón en el P&L cuando el cupón no se reinvierte.

### Requirement 12: Derivación de la variación de reserva desde el input (saldo e interés)

**User Story:** Como analista, quiero que el sistema derive la variación mensual de reserva
por ramo a partir del archivo Excel de saldo de reserva e interés a la reserva, aplicando
las reglas específicas de cada compañía y agrupando los ramos según su presencia en el
portafolio, para que la variación usada como driver refleje fielmente el input real y las
reglas contables de cada compañía.

#### Acceptance Criteria

1. THE input de reservas SHALL constar de dos hojas: la hoja de saldo de reserva ("Saldo Reservas"), con bloques por compañía (BOLIVAR, COMERCIALES, CAPITALIZADORA), ramos como filas, los 12 meses (Enero..Diciembre) como columnas y filas TOTAL por bloque (TOTAL BOLIVAR, TOTAL COMERCIALES, TOTAL CAPI, TOTAL COMPAÑIAS); y la hoja de interés a la reserva ("Interés a la Reserva"), con líneas de Bolívar y una línea "Capitalizadora" y los 12 meses como columnas.
2. THE sistema SHALL interpretar los valores de ambas hojas como niveles mensuales (saldo e interés), no como variaciones.
3. WHEN el sistema deriva la variación de reserva de un ramo para el mes `t` THEN el sistema SHALL calcular la variación como el valor del mes `t` menos el valor del mes `t-1` (variación mes contra mes) tanto para el saldo como para el interés.
4. WHERE el ramo pertenece a la compañía Comerciales THEN el sistema SHALL calcular la variación del mes `t` como `saldo(t) − saldo(t-1)`, sin término de interés.
5. WHERE el ramo pertenece a la compañía Bolívar THEN el sistema SHALL calcular la variación del mes `t` como `(saldo(t) − saldo(t-1)) − (interés(t) − interés(t-1))`; IF el ramo de Bolívar no tiene línea de interés THEN el sistema SHALL usar interés igual a cero, reduciendo la variación a `saldo(t) − saldo(t-1)`.
6. WHERE la compañía es Capitalizadora THEN el sistema SHALL tratarla como un único ramo agregado ("CAPITALIZADORA"), ignorando los ramos individuales del saldo de esa compañía, y SHALL calcular su variación del mes `t` como `(TOTAL_CAPI(t) − TOTAL_CAPI(t-1)) − (interés_capi(t) − interés_capi(t-1))`, usando la fila TOTAL CAPI del saldo y la línea "Capitalizadora" del interés.
7. WHERE un ramo de Bolívar o de Comerciales está presente en el portafolio INICIAL de la corrida (portafolio_clasificado, el punto de partida de la proyección) THEN el sistema SHALL conservar ese ramo como un ramo individual con su propia variación derivada. THE conjunto de ramos usado para decidir presencia/ausencia SHALL ser el del portafolio inicial (no el estado del portafolio al final del horizonte ni un estado intermedio de un mes proyectado).
8. WHERE uno o más ramos de Bolívar o de Comerciales NO están presentes en el portafolio activo THEN el sistema SHALL sumar las variaciones derivadas de esos ramos no presentes en un ramo agregado por compañía, denominado "DEMÁS RAMOS BOLÍVAR" para Bolívar y "DEMÁS RAMOS COMERCIALES" para Comerciales.
9. THE sistema SHALL exponer todos los ramos y grupos resultantes —ramos individuales, los agregados "DEMÁS RAMOS BOLÍVAR" y "DEMÁS RAMOS COMERCIALES", y el agregado "CAPITALIZADORA"— como ramos clasificables en la pestaña Ajustes (reserva matemática o no, más instrumento), de modo que operen compras/ventas según su clasificación igual que cualquier otro ramo.
10. THE variación derivada por ramo/grupo y mes SHALL ser el valor que el sistema persiste (Requirement 1) y que el motor consume como driver de compras/ventas (Requirement 2).

### Requirement 13: Parámetros configurables de plazo (nodo de compra y plazo máximo de venta)

**User Story:** Como analista, quiero que los parámetros numéricos de plazo —el nodo objetivo de
compra para ramos que no son de reserva matemática y el plazo máximo elegible para vender— sean
editables desde el dashboard, sin exponerlos en la vista principal, para poder ajustarlos en
casos excepcionales sin cambiar código y sin saturar la interfaz que se usa a diario.

#### Acceptance Criteria

1. THE sistema SHALL exponer como parámetros configurables al menos: (a) el nodo objetivo de plazo al vencimiento para la compra de ramos que no son de reserva matemática (por defecto 5 años) y (b) el plazo máximo al vencimiento elegible para vender títulos en el waterfall (por defecto 10 años).
2. THE valor por defecto del nodo de compra no matemática SHALL ser 5 años, y el valor por defecto del plazo máximo de venta SHALL ser 10 años, de modo que sin intervención del usuario el comportamiento sea idéntico al de los valores fijos actuales.
3. THE sistema SHALL persistir los valores de estos parámetros de forma que se conserven entre corridas y sean leídos por el motor al inicio de cada corrida.
4. WHEN el usuario edita uno de estos parámetros desde el dashboard THEN el motor SHALL usar el valor editado en la siguiente corrida para la selección de títulos de compra (nodo objetivo) y para la elegibilidad por plazo en la venta (plazo máximo).
5. THE controles de edición de estos parámetros SHALL ubicarse en una sección avanzada u oculta por defecto del dashboard (por ejemplo, un panel colapsable o una ventana desplegable), de modo que no aparezcan en la vista principal de uso diario.
6. IF un parámetro no tiene valor persistido O el valor persistido es inválido (no numérico, no positivo) THEN el sistema SHALL usar el valor por defecto correspondiente (5 años para el nodo de compra, 10 años para el plazo máximo de venta) sin abortar la corrida.
7. WHERE se aplican las reglas de selección de compra y de elegibilidad de venta THEN el sistema SHALL tomar el nodo objetivo y el plazo máximo de estos parámetros configurables en lugar de literales fijos en el código.
