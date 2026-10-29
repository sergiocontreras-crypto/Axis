# Requirements Document

## Introduction

AXIS es un simulador de proyección de portafolio (Python 3.12+, Dash, SQLite `portafolio.db`). En el modo de proyección `por_tramos` (features `proyeccion-por-tramos` y `proyeccion-por-tramos-multiactivo`), el horizonte se divide en tramos anuales y se ENCADENAN corridas del `Motor_Proyeccion_RF`: el snapshot de cierre de un tramo es el estado inicial (t0) del tramo siguiente.

Ese encadenamiento tiene hoy un problema de ESCALA que impide completar horizontes de 3 tramos (p. ej. 2026→2028): cada reinversión de cupón crea un título sintético nuevo (correcto por diseño), por lo que el conteo de títulos de Renta Fija crece de forma acumulativa y explosiva de un tramo al siguiente. En una corrida real medida, el portafolio RF pasó de **~3.194 títulos al cierre de 2026 a ~39.760 al cierre de 2027**; el tercer tramo (2028), que arranca con esas ~40.000 posiciones y sigue reinvirtiendo, **no termina** (más de 6 horas de cómputo, ~9 GB de RAM, CPU al 100% en una corrida observada). El negocio NECESITA los datos del tercer tramo y hoy no puede obtenerlos.

Esta feature ataca la causa de escala **consolidando, al cierre de cada tramo (antes de persistir el snapshot y encadenar al siguiente), las reinversiones Negociables FUNGIBLES** en un solo título por grupo. Una medición sobre el snapshot real de cierre de 2027 confirma el potencial: de **39.760 títulos RF, 34.630 son Negociables** y colapsan a **~118 grupos fungibles** (clave: especie + fecha de vencimiento + tasa facial + periodicidad), una **reducción de ~34.512 filas (~87%)**. Así, el tramo 3 arrancaría con ~5.400 títulos en lugar de ~40.000 (reducción ~7×), haciéndolo computable.

**Restricción de negocio INVIOLABLE (confirmada por el usuario):** los títulos a TIR / costo amortizado (clasificación `Vencimiento` y `Disp. Venta`) NO se pueden agrupar ni consolidar, porque cada reinversión nace con una tasa de compra única (p. ej. 6.2% vs 6.3%) que define su costo amortizado individual. SOLO los títulos `Negociable` (valorados a precio de mercado, cuyo VM NO depende de la tasa de compra individual) son candidatos a consolidación.

**Principio de no-regresión numérica:** la consolidación es una optimización de rendimiento, NO un cambio de valoración. El resultado observable de los tramos que hoy sí terminan (VM, VTIR, P&L por mes y por título agregado por grupo, cupones, encaje) debe ser EQUIVALENTE al actual dentro de una tolerancia acordada. La valoración de los motores NO se modifica.

## Glossary

- **AXIS**: Simulador de proyección de portafolio (Dash + motores en `scripts/` + SQLite `portafolio.db`).
- **Motor_Proyeccion_RF**: Componente `proyectar_portafolio(...)` en `scripts/motor_proyeccion.py`. Simula la evolución mensual del portafolio de renta fija y crea reinversiones de cupones. Su lógica de valoración NO se modifica en esta feature.
- **Orquestador_Por_Tramos**: Función `_ejecutar_escenario_por_tramos(...)` en `app_dash.py`. Encadena las corridas por tramo y persiste el snapshot de cierre de cada uno. Es el punto donde se INVOCA la consolidación.
- **Tramo**: Sub-periodo del horizonte, desde su inicio hasta el fin del año calendario correspondiente (o la fecha fin del horizonte para el último tramo).
- **Fecha_Corte_Tramo**: Fecha de cierre de un tramo; identifica su snapshot (`fecha_corte_proyeccion`).
- **Snapshot_Tramo**: Portafolio clasificado proyectado al cierre de un tramo, persistido en `portafolio_clasificado_proy` y reconstruido como t0 del tramo siguiente (`cargar_titulos_desde_snapshot`).
- **Titulo**: Objeto `motor_proyeccion.Titulo` (dataclass). Representa un título de renta fija con su clasificación contable, nominal, VM, VTIR, tasa de compra, calendario de cupones implícito, etc.
- **Clasificacion_Contable**: Atributo del `Titulo` con tres valores relevantes: `Negociable` (NEG, a precio de mercado), `Disp. Venta` (DV, a TIR con ORI) y `Vencimiento` (VC, a TIR / costo amortizado).
- **Titulo_Negociable**: `Titulo` con `Clasificacion_Contable = Negociable`. ÚNICO tipo elegible para consolidación.
- **Titulo_A_TIR**: `Titulo` con `Clasificacion_Contable ∈ {Disp. Venta, Vencimiento}`. NUNCA se agrupa (restricción inviolable).
- **Reinversion**: `Titulo` sintético creado por `reinvertir_cupon(...)` cuando se cobra un cupón; tiene `es_reinversion = True`, `id_posicion` sintético (p. ej. `723324_R20261231`) y `fecha_emision` igual a la fecha de pago del cupón.
- **Grupo_Fungible**: Conjunto de `Titulo_Negociable` que comparten la `Clave_Fungibilidad` y por tanto son económicamente indistinguibles a precio de mercado (mismo flujo futuro por unidad de nominal).
- **Clave_Fungibilidad**: Tupla que define la fungibilidad de dos `Titulo_Negociable`. Clave base propuesta: `(especie, isin, fecha_vencimiento, tasa_facial, periodicidad, tipo_bono, moneda)`. La clave exacta se fija en diseño; debe garantizar que todos los miembros produzcan idéntico precio de mercado por unidad de nominal.
- **Titulo_Consolidado**: `Titulo` único que reemplaza a un `Grupo_Fungible`. Su `nominal` es la SUMA de los nominales del grupo; su VM/VTIR es la suma de los VM/VTIR del grupo; conserva los atributos comunes de la `Clave_Fungibilidad`.
- **Consolidar_Negociables**: Función pura propuesta (p. ej. `consolidar_negociables_fungibles(...)`) que recibe el conjunto de títulos/filas de cierre del tramo y devuelve el mismo conjunto con los `Titulo_Negociable` fungibles colapsados en `Titulo_Consolidado`, dejando intactos los `Titulo_A_TIR`.
- **Equivalencia_Numerica**: Igualdad de los resultados observables (VM, VTIR, P&L mercado/TIR por mes, cupones, encaje) entre la corrida con consolidación y la corrida sin consolidación, dentro de una `Tolerancia_Equivalencia`.
- **Tolerancia_Equivalencia**: Holgura numérica máxima aceptada en la verificación de equivalencia (valor de referencia histórico del proyecto: `1e-6` relativo; el valor final se fija en diseño).
- **Arnes_Equivalencia**: Herramienta `tools/equivalencia_motor_rf.py` (comandos `baseline` / `check` / `profile`) que captura y compara VM, VTIR, P&L, conteos, cupones y reinversiones por mes, usada para verificar la no-regresión.
- **Snapshot_Completo_Tramo**: Snapshot de cierre que incluye RF + RV + Alternativos + Derivados (feature `proyeccion-por-tramos-multiactivo`). La consolidación aplica EXCLUSIVAMENTE a las filas de Renta Fija Negociable; las filas no-RF no se tocan.

## Requirements

### Requirement 1: Consolidación de reinversiones Negociables fungibles al cierre de tramo

**User Story:** Como analista financiero que corre proyecciones por tramos, quiero que las reinversiones Negociables fungibles se consoliden al cierre de cada tramo, para que el conteo de títulos del tramo siguiente no crezca de forma explosiva y el horizonte completo (incluido el tercer tramo) termine en un tiempo razonable.

#### Acceptance Criteria

1. WHEN el `Orquestador_Por_Tramos` termina la corrida de un tramo y antes de persistir su `Snapshot_Tramo`, THE AXIS SHALL ejecutar `Consolidar_Negociables` sobre el conjunto de títulos de cierre de renta fija del tramo.
2. WHERE dos o más `Titulo_Negociable` comparten la `Clave_Fungibilidad`, THE `Consolidar_Negociables` SHALL reemplazarlos por un único `Titulo_Consolidado` cuyo `nominal` sea la suma de los nominales del grupo.
3. THE `Titulo_Consolidado` SHALL tener un `valor_mercado` igual a la suma de los `valor_mercado` de los títulos del `Grupo_Fungible`.
4. THE `Titulo_Consolidado` SHALL tener un `valor_tir` igual a la suma de los `valor_tir` de los títulos del `Grupo_Fungible`.
5. THE `Titulo_Consolidado` SHALL conservar los atributos de la `Clave_Fungibilidad` (especie, vencimiento, tasa facial, periodicidad, tipo de bono, moneda) idénticos a los de los miembros del grupo.
6. WHERE un `Grupo_Fungible` tiene un solo miembro, THE `Consolidar_Negociables` SHALL dejar ese título sin cambios.

### Requirement 2: Preservación inviolable de los títulos a TIR

**User Story:** Como responsable contable, quiero que los títulos a Vencimiento y Disponible para la Venta NUNCA se agrupen, para que cada uno conserve su tasa de compra y su costo amortizado individual, tal como exige la contabilidad.

#### Acceptance Criteria

1. THE `Consolidar_Negociables` SHALL dejar inalterado todo `Titulo_A_TIR` (clasificación `Vencimiento` o `Disp. Venta`): mismo `id_posicion`, mismo `nominal`, misma `tasa_compra`, mismo `valor_tir`.
2. THE `Consolidar_Negociables` SHALL NOT fusionar un `Titulo_A_TIR` con ningún otro título bajo ninguna circunstancia.
3. THE `Consolidar_Negociables` SHALL NOT consolidar dos `Titulo_Negociable` que difieran en cualquier componente de la `Clave_Fungibilidad`.
4. WHERE una fila del snapshot no es Renta Fija (clase_origen `RENTA_VARIABLE`, `ALTERNATIVO`, `DERIVADO`), THE `Consolidar_Negociables` SHALL dejarla intacta.
5. WHERE un `Titulo_Negociable` es subyacente de un swap (tiene `id_cobertura` no vacío), THE `Consolidar_Negociables` SHALL dejarlo intacto (no se consolida, para preservar el enlace de cobertura).

### Requirement 3: Equivalencia numérica verificable (no-regresión)

**User Story:** Como analista que confía en los números del simulador, quiero que la consolidación no altere los resultados de las proyecciones que hoy sí terminan, para seguir obteniendo las mismas cifras con mejor rendimiento.

#### Acceptance Criteria

1. THE AXIS SHALL producir, para un horizonte de 2 tramos (2026→2027) con consolidación activada, un VM total de cierre por tramo igual al de la corrida sin consolidación dentro de la `Tolerancia_Equivalencia`.
2. THE AXIS SHALL producir un VTIR total de cierre por tramo igual al de la corrida sin consolidación dentro de la `Tolerancia_Equivalencia`.
3. THE AXIS SHALL producir un P&L (mercado y TIR) por mes igual al de la corrida sin consolidación dentro de la `Tolerancia_Equivalencia`, agregado al nivel que permita comparar (total y por grupo fungible).
4. THE AXIS SHALL producir cupones cobrados por mes iguales a los de la corrida sin consolidación dentro de la `Tolerancia_Equivalencia`.
5. THE verificación de equivalencia SHALL realizarse con el `Arnes_Equivalencia` (`tools/equivalencia_motor_rf.py check`) contra un baseline capturado ANTES de implementar la consolidación.
6. IF la verificación de equivalencia excede la `Tolerancia_Equivalencia`, THEN THE implementación SHALL considerarse incorrecta y no integrarse.

### Requirement 4: Corrección de cupones y VM del título consolidado tras encadenar

**User Story:** Como analista, quiero que el título consolidado siga generando los mismos cupones y la misma valoración que la suma de sus partes en el tramo siguiente, para que la consolidación sea transparente en el resultado del horizonte completo.

#### Acceptance Criteria

1. WHEN el `Titulo_Consolidado` se reconstruye como t0 del tramo siguiente (`cargar_titulos_desde_snapshot`), THE AXIS SHALL calcular sus cupones futuros a partir de la `fecha_vencimiento`, la `tasa_facial` y la `periodicidad` comunes del grupo (el calendario de cupones se ancla al vencimiento, no a la fecha de emisión).
2. WHERE los miembros del `Grupo_Fungible` tenían distinta `fecha_emision` (por ser reinversiones de meses distintos), THE `Consolidar_Negociables` SHALL fijar en el `Titulo_Consolidado` una `fecha_emision` que NO altere el calendario de cupones ni el primer cupón devengado respecto a la suma de los miembros, o SHALL documentar y acotar en diseño el impacto (debe quedar dentro de la `Tolerancia_Equivalencia`).
3. THE cupón del período del `Titulo_Consolidado` SHALL ser igual a la suma de los cupones del mismo período de los miembros del `Grupo_Fungible` dentro de la `Tolerancia_Equivalencia`.
4. THE valoración a precio de mercado del `Titulo_Consolidado` SHALL ser igual a la suma de las valoraciones de los miembros dentro de la `Tolerancia_Equivalencia` (consecuencia de la linealidad del VM en el nominal para un mismo precio por unidad).

### Requirement 5: Función pura, aislada y testeable

**User Story:** Como mantenedor del código, quiero que la consolidación viva en una función pura y aislada, para poder probarla con tests unitarios y de propiedad sin correr todo el dashboard.

#### Acceptance Criteria

1. THE `Consolidar_Negociables` SHALL implementarse como una función pura (sin efectos secundarios: sin I/O, sin estado global) que recibe el conjunto de títulos/filas de cierre y devuelve el conjunto consolidado.
2. THE `Consolidar_Negociables` SHALL ubicarse en un módulo de `scripts/` (p. ej. `scripts/proyeccion_tramos.py` o un módulo nuevo), NO dentro de `app_dash.py`.
3. THE `Motor_Proyeccion_RF` (`proyectar_portafolio`) SHALL NOT modificarse por esta feature (la consolidación ocurre en la orquestación, después de la corrida del motor).
4. THE `Consolidar_Negociables` SHALL tratar su entrada como NO confiable: normalizar/coercionar los campos de la `Clave_Fungibilidad` y descartar filas inválidas con un `logger.warning` genérico (sin volcar contenido sensible).
5. THE feature SHALL incluir pruebas automatizadas que cubran: fungibilidad correcta, no-fusión de títulos a TIR, no-fusión de Negociables con clave distinta, suma de nominales/VM/VTIR, y preservación de subyacentes de swap.

### Requirement 6: Activación controlada y retrocompatibilidad

**User Story:** Como usuario del dashboard, quiero poder correr el modo por tramos como hoy y activar la consolidación sin cambiar mi flujo, para adoptar la mejora sin riesgo.

#### Acceptance Criteria

1. THE consolidación SHALL aplicarse únicamente en el `Modo_Proyeccion = por_tramos` (el modo `punta_a_punta` no la usa).
2. THE comportamiento del modo `punta_a_punta` SHALL permanecer sin cambios.
3. WHERE la consolidación esté activada, THE `Snapshot_Tramo` persistido SHALL contener los `Titulo_Consolidado` en lugar de los `Titulo_Negociable` individuales del grupo, sin afectar las filas a TIR ni las no-RF.
4. THE AXIS SHALL registrar en el log, por tramo, un diagnóstico de la consolidación (p. ej. número de títulos antes y después, filas colapsadas), análogo al diagnóstico `[SNAPSHOT-DEDUP]` existente.
5. IF la consolidación falla por datos degenerados, THEN THE `Orquestador_Por_Tramos` SHALL continuar con el conjunto sin consolidar (fallback no destructivo) dejando rastro en el log, para no abortar la corrida.

### Requirement 7: Verificación end-to-end del tercer tramo

**User Story:** Como analista que necesita los datos de 2028, quiero correr el horizonte completo de 3 tramos con consolidación y obtener el snapshot y el Excel del tercer tramo, para analizar la evolución hasta dic-2028.

#### Acceptance Criteria

1. WHEN se corre el horizonte 2026→2028 (3 tramos) con consolidación activada, THE AXIS SHALL completar los tres tramos y persistir el `Snapshot_Tramo` del corte dic-2028.
2. THE conteo de títulos de renta fija al inicio del tercer tramo SHALL reducirse de forma material respecto al conteo sin consolidación (objetivo de referencia: de ~40.000 a ~5.400, reducción ~7×; el valor exacto depende de la data).
3. THE Excel detallado por tramo SHALL incluir las hojas del corte dic-2028 (portafolio completo y resumen P&G/ORI por categoría × compañía × ramo), igual que para los cortes anteriores.
4. THE resultado de los dos primeros tramos del horizonte de 3 tramos SHALL permanecer dentro de la `Tolerancia_Equivalencia` respecto a la corrida de 2 tramos sin consolidación (la consolidación no cambia los tramos previos de forma observable).
