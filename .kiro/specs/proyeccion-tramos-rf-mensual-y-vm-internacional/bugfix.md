# Documento de Requisitos de Bugfix

## Introduction

El modo de proyección "por_tramos" del dashboard AXIS (`app_dash.py`) trocea el horizonte por fin de año y ejecuta una corrida del motor (`scripts/motor_proyeccion.py::proyectar_portafolio`) por cada año, orquestado en `app_dash.py::_ejecutar_escenario_por_tramos`. La exportación a Excel por tramos vive en `tools/exportar_excel_tramos.py`.

Esta spec corrige DOS defectos distintos del modo por tramos:

- **Defecto 1 — Falta la hoja RF_Mensual y se pierde el P&G de enero de cada año.** En el Excel por tramos no existe una hoja mensual de Renta Fija (sí la hay para RV, Alternativos y Derivados). Además, en cada frontera de año se pierde el mes de ENERO del año que inicia el tramo, de modo que un tramo que el dashboard reporta como 12 meses sale con solo 11 meses de renta fija en el Excel. La causa raíz tiene dos capas: (a) no se captura el retorno mensual de `proyectar_portafolio` en el orquestador por tramos, y (b) el motor fecha cada mes con un desplazamiento de +1 mes porque `mes` arranca en 1 y cada tramo arranca el 1-ene. La regla de negocio acordada es que "el cierre de un año es el T0 del año siguiente".

- **Defecto 2 — El VM de cierre de RF internacional (USD/Yankees) en modo por tramos. [NO REPRODUCIBLE — CERRADO].** El síntoma original reportado era que el VM de cierre de los títulos de RF en USD quedaba en una escala ~TRM veces menor que el VM inicial del snapshot `portafolio_clasificado_proy`. **Resolución (verificada):** la re-investigación de TODO el proceso por tramos (motor + orquestación + encadenamiento + persistencia) y la inspección del `portafolio.db` real NO reproducen el defecto en ninguna ruta viva. Para el caso documentado (id 817856 "BN ECOPETROL USD"), el snapshot oficial ya registra el VM de cierre en escala COP correcta: 14.389.842.600,95 COP en el cierre 2026-12-31 (tramo 0) y 15.094.118.182 COP en el cierre 2027-12-31 (tramo i>0, multi-año). El valor "14.389.842" documentado era ese mismo número leído con la escala equivocada (÷1000). Todas las ramas de asignación de `valor_mercado` para USD (TIME DEP, VISTA USD, YANKEE vía `valorar_titulo`, FIC) aplican `×_senda_trm[mes]`; ninguna deja el VM en escala USD. Por tanto el Defecto 2 queda cerrado como no reproducible; la Tarea 5 se reconvierte en un test de integración multi-tramo que blinda este comportamiento y en la decisión sobre la mitigación de `tools/detalle_pyg_tramo.py`.

El impacto de ambos defectos es una proyección por tramos incorrecta: series mensuales incompletas de RF y valores de mercado de cierre erróneos para RF internacional, afectando el P&G reportado y el snapshot oficial.

Esta fase documenta únicamente los requisitos. La implementación se hará después. No se modifica código fuente durante la fase de requisitos.

## Bug Analysis

### Current Behavior (Defect)

Lo que ocurre hoy cuando se dispara el bug.

**Defecto 1 — RF_Mensual ausente y enero perdido en fronteras de año:**

1.1 WHEN se ejecuta una proyección en modo "por_tramos" THEN el sistema NO genera ninguna hoja RF_Mensual en el Excel exportado por `tools/exportar_excel_tramos.py`, a diferencia de RV, Alternativos y Derivados que sí tienen hoja mensual.

1.2 WHEN `app_dash.py::_ejecutar_escenario_por_tramos` llama a `proyectar_portafolio(...)` THEN el sistema descarta la lista mensual de ResultadoMes retornada (no la captura), y el dict final del modo por tramos NO incluye la clave 'rf_mensual_contable'.

1.3 WHEN un tramo abarca un año completo (sub_horizonte = 12) y el tramo arranca el 1-ene THEN el sistema fecha el mes 1 como febrero (`fecha_mes = fecha_inicio + relativedelta(months=mes)` con `mes` desde 1) y nunca emite una fila para el enero de arranque de ese tramo.

1.4 WHEN hay una frontera de año entre tramos (p.ej. corrida con fin 31-dic-2027) THEN el sistema omite ENERO del año nuevo del tramo (ene-2027), y lo repite en CADA frontera de año (ene-2028, ene-2029, ...) en corridas multi-año, de modo que la serie mensual de RF presenta 11 meses por año en lugar de 12.

1.5 WHEN el dashboard reporta que un tramo proyecta 12 meses THEN el Excel exportado muestra solo 11 meses de renta fija para ese tramo, generando incoherencia entre el conteo del dashboard y la serie mensual exportada.

**Defecto 2 — VM de cierre de RF internacional (USD/Yankees). [NO REPRODUCIBLE — CERRADO]:**

> **Nota de resolución:** los requisitos 1.6–1.9 describen el síntoma ORIGINAL reportado. La
> re-investigación de todo el proceso por tramos y la inspección del `portafolio.db` real NO
> reproducen el defecto: el VM de cierre USD ya está en escala COP (×TRM) tanto en el tramo 0
> como en el multi-tramo (i>0). Se conservan como registro histórico del reporte.

1.6 ~~WHEN se ejecuta una proyección en modo "por_tramos" y existen títulos de RF en USD THEN el sistema registra el VM de cierre en escala ~TRM veces menor.~~ **No reproducible:** el VM de cierre USD se registra en COP (×TRM).

1.7 WHEN se toma el caso documentado (corte 2026-12-31, id 817856 BN ECOPETROL USD, YANKEE, Negociable) THEN el snapshot oficial reporta VM de cierre = 14.389.842.600,95 COP (en escala COP, consistente con el VM base = 12.657.628.506 COP). El "14.389.842" del reporte original fue una lectura del mismo valor con escala errónea (÷1000).

1.8 ~~WHEN se agregan los títulos de RF en USD THEN el VM total USD cae de ~3.28 billones a ~0.35 billones.~~ **No reproducible:** el VM total USD se conserva en escala COP.

1.9 WHEN se consulta el informe de P&G por tramos THEN existe una mitigación en `tools/detalle_pyg_tramo.py` que re-valora los USD con `valorar_titulo`; dado que el snapshot oficial ya es correcto, esa re-valoración es idempotente (defensa documentada) y su destino se decide en la Tarea 5.2.

### Expected Behavior (Correct)

Lo que debería ocurrir en su lugar.

**Defecto 1 — RF_Mensual presente y continua, enero de cada año incluido:**

2.1 WHEN se ejecuta una proyección en modo "por_tramos" THEN el sistema SHALL generar y exportar una hoja RF_Mensual en el Excel por tramos, análoga a las hojas mensuales de los otros activos.

2.2 WHEN `app_dash.py::_ejecutar_escenario_por_tramos` ejecuta cada tramo THEN el sistema SHALL capturar la lista mensual de ResultadoMes retornada por `proyectar_portafolio(...)` e incluir la clave 'rf_mensual_contable' en el dict final del modo por tramos.

2.3 WHEN un tramo arranca el 1-ene THEN el sistema SHALL emitir una fila mensual para el enero de arranque de ese tramo, de modo que las 12 fechas de un año completo correspondan a ene..dic de ese año (sin desplazamiento +1 mes).

2.4 WHEN existe una frontera de año entre tramos THEN el sistema SHALL calcular el P&G de enero de cada año como VM(fin de enero) − VM(cierre del año anterior / 31-dic), aplicando la regla "el cierre de un año es el T0 del año siguiente", e incluirlo en la serie mensual.

2.5 WHEN la proyección abarca múltiples tramos (multi-año) THEN el sistema SHALL producir una serie mensual de RF continua a través de TODOS los tramos, sin huecos ni duplicados en las fronteras de año.

2.6 WHEN se cuentan las filas de la serie mensual de RF THEN el sistema SHALL hacer que el total de filas iguale la suma de sub_horizonte de todos los tramos, coherente con lo que muestra el dashboard.

2.7 WHEN se corrige la captura y el fechado THEN el sistema SHALL extraer la lógica contable mensual NEG/DV/VC (hoy embebida en `_ejecutar_escenario`) a un helper compartido y testeable, reutilizado por los modos punta_a_punta y por_tramos.

**Defecto 2 — VM de cierre de RF internacional en COP (×TRM). [YA SATISFECHO — se blinda con test]:**

> **Nota de resolución:** el comportamiento esperado 2.8–2.10 YA se cumple en el código y datos
> actuales (verificado en tramo 0 y multi-tramo). El fix del motor/snapshot que planteaba 2.11
> NO es necesario; en su lugar se añade un test de integración multi-tramo (Tarea 5.1) que
> blinda la escala COP y se decide el destino de la mitigación (Tarea 5.2).

2.8 WHEN se ejecuta una proyección en modo "por_tramos" y existen títulos de RF en USD THEN el sistema SHALL registrar el VM de cierre en COP (×TRM), consistente en escala con el VM inicial, en el snapshot oficial `portafolio_clasificado_proy`. **(Ya satisfecho; se blinda con test de integración.)**

2.9 WHEN se toma el caso documentado (corte 2026-12-31, id 817856 BN ECOPETROL USD) THEN el sistema SHALL reportar un VM de cierre con escala COP coherente con el VM base (= VM×TRM). **(Ya satisfecho: 14.389.842.600,95 COP.)**

2.10 WHEN se calcula el P&G por diferencia de VM de la RF internacional THEN el sistema SHALL producir valores coherentes usando el snapshot oficial. **(Ya satisfecho; el snapshot oficial está en escala COP sin depender de la mitigación.)**

2.11 WHEN se blinde el comportamiento ya correcto THEN el sistema SHALL incluir un test de integración multi-tramo que ejercite `proyectar_portafolio` por tramo + el round-trip de persistencia (tramo 0 e i>0) verificando el VM en COP, y SHALL decidir el destino de la mitigación de `tools/detalle_pyg_tramo.py` (retirarla o conservarla como defensa documentada e idempotente).

### Unchanged Behavior (Regression Prevention)

Comportamiento existente que debe preservarse.

3.1 WHEN se ejecuta una proyección en modo "punta_a_punta" THEN el sistema SHALL CONTINUAR generando la clave 'rf_mensual_contable' en `_ejecutar_escenario` y exportando la hoja 'RF_Mensual' en `descargar_resultados_excel` sin cambios de comportamiento.

3.2 WHEN se exportan las series mensuales de RV, Alternativos y Derivados en modo por tramos THEN el sistema SHALL CONTINUAR generando esas hojas (RV_Mensual_Tramos, etc.) en `tools/exportar_excel_tramos.py::_anexar_series_mensuales_tramos` sin alteración.

3.3 WHEN se valora un título de RF en COP (no USD) con `valorar_titulo` THEN el sistema SHALL CONTINUAR produciendo exactamente el mismo valor de mercado que antes del fix (valoración idéntica, verificada por test de equivalencia).

3.4 WHEN se valora un título YANKEE/USD de forma aislada con `valorar_titulo` (p.ej. nominal × precio/100 × trm) THEN el sistema SHALL CONTINUAR produciendo el valor correcto en COP (la valoración unitaria ya es correcta y no debe cambiar).

3.5 WHEN se ejecuta una proyección en modo "punta_a_punta" sobre un portafolio con RF internacional THEN el sistema SHALL CONTINUAR produciendo el mismo VM de cierre que antes del fix (el defecto 2 es específico del modo por tramos / encadenamiento).

3.6 WHEN se extrae la lógica contable mensual NEG/DV/VC a un helper compartido THEN el sistema SHALL CONTINUAR produciendo los mismos valores contables mensuales que la lógica embebida original para los casos ya soportados.

---

## Derivación de las condiciones de bug

### Defecto 1 — Condición de bug (fechado + captura de serie mensual RF)

```pascal
FUNCTION isBugCondition_D1(X)
  INPUT: X de tipo CorridaPorTramos
  OUTPUT: boolean

  // Verdadero cuando la corrida es en modo por tramos:
  // la serie mensual de RF o no se exporta, o pierde el enero de arranque de un tramo.
  RETURN X.modo = "por_tramos"
END FUNCTION
```

```pascal
// Propiedad: Fix Checking - RF_Mensual continua y completa
FOR ALL X WHERE isBugCondition_D1(X) DO
  rf_mensual ← F'(X).rf_mensual_contable
  ASSERT existe_hoja_RF_Mensual(F'(X))
  ASSERT total_filas(rf_mensual) = SUMA(sub_horizonte de todos los tramos de X)
  ASSERT para_cada_tramo, enero_de_arranque ESTA_PRESENTE en rf_mensual
  ASSERT serie_continua_sin_huecos_ni_duplicados(rf_mensual)
  ASSERT pyg_enero = VM(fin_enero) - VM(cierre_31_dic_anterior)
END FOR
```

### Defecto 2 — Condición de bug (escala COP del VM de cierre USD)

```pascal
FUNCTION isBugCondition_D2(X)
  INPUT: X de tipo TituloEnCierrePorTramos
  OUTPUT: boolean

  // Verdadero cuando el título es de RF internacional en USD (p.ej. YANKEE)
  // valorado en el cierre de un tramo.
  RETURN X.moneda = "USD" AND X.tipo IN {YANKEE, EstructuraUSD} AND X.es_cierre_de_tramo
END FUNCTION
```

```pascal
// Propiedad: Fix Checking - VM de cierre USD en COP (×TRM)
FOR ALL X WHERE isBugCondition_D2(X) DO
  vm_cierre ← F'(X).valor_mercado_en_snapshot
  ASSERT vm_cierre ≈ nominal * precio/100 * trm   // escala COP, consistente con VM inicial
END FOR
```

### Meta de preservación (ambos defectos)

```pascal
// Propiedad: Preservation Checking
FOR ALL X WHERE NOT isBugCondition_D1(X) AND NOT isBugCondition_D2(X) DO
  ASSERT F(X) = F'(X)
END FOR

// En particular, para títulos en COP (no USD) y para el modo punta_a_punta:
FOR ALL T WHERE T.moneda = "COP" DO
  ASSERT valorar_titulo_antes(T) = valorar_titulo_despues(T)
END FOR
```

**Definiciones:**
- **F**: código actual (sin corregir) del modo por tramos y de la valoración/encadenamiento.
- **F'**: código corregido tras aplicar ambos fixes.
- **Orden sugerido:** Defecto 1 primero (aditivo, bajo riesgo); Defecto 2 después (toca valoración, requiere tests de equivalencia).

---

## Fase 2 — Hallazgos de corrida real (Defectos 3–7)

Tras la Fase 1 (Defectos 1 y 2), una corrida real del modo `por_tramos` y la revisión del Excel exportado surgieron cinco observaciones adicionales. Dos son mejoras de alcance (hojas/discriminación que no existían) y tres son defectos reales, incluido uno que es una REGRESIÓN introducida por el fix de fechado de la Fase 1 (Tarea 4.2).

### Defecto 3 — El mes de arranque del PRIMER tramo (parcial) queda corrido; se pierde diciembre

**Regresión del fix 4.2.** El fix del fechado asumió que todo tramo arranca el 1-ene (regla "el cierre de un año es el T0 del siguiente"), cierto para los tramos i>0 pero NO para el PRIMER tramo, que arranca en la fecha base real (p.ej. 31-jul-2026) y es un tramo PARCIAL. Con `fecha_etiqueta_mes = fecha_inicio + relativedelta(months=mes-1)`, el primer tramo etiqueta su mes 1 como el T0 (jul-2026, sin P&G proyectado real) y corre toda la serie un mes hacia atrás, perdiendo diciembre.

3.1 WHEN el PRIMER tramo arranca a mitad de año (fecha base real, p.ej. 31-jul-2026, día != 1) THEN el sistema SHALL etiquetar su primer mes PROYECTADO con el mes de VALORACIÓN (ago-2026), NO con el T0 (jul-2026), de modo que la serie cubra ago..dic sin perder diciembre.

3.2 WHEN un tramo arranca en frontera de año (día 1, 1-ene) THEN el sistema SHALL CONTINUAR etiquetando ene..dic (comportamiento correcto de la Fase 1, sin regresión).

3.3 WHEN se cuenta la serie mensual del primer tramo THEN el sistema SHALL incluir el mes de cierre del tramo (dic-2026) y NO incluir el T0 como un mes de P&G proyectado.

### Defecto 4 — Los subyacentes de swaps no se separan de RF Internacional en los tramos i>0

**Bug real.** La categoría "Subyacentes de Swaps" del resumen por corte depende del campo `id_cobertura` del título. En el tramo 0 ese campo viene de `portafolio_clasificado`; pero el round-trip del snapshot (persistencia + reconstrucción) NO lo preserva, así que en los tramos i>0 (2027, 2028) todos los títulos quedan con `id_cobertura` vacío y los subyacentes caen en "RF Internacional".

4.1 WHEN se encadena un tramo i>0 (base reconstruida del snapshot) THEN el sistema SHALL preservar el campo `id_cobertura` de cada título a través del round-trip (DataFrame base -> persistencia -> reconstrucción), de modo que los subyacentes de swaps sigan separándose de la RF internacional en 2027/2028 igual que en 2026.

### Defecto 5 — La advertencia estática del "bug VM-USD" sigue apareciendo para RF Internacional

**Bug real (texto estático residual).** El resumen por corte emite una columna `advertencia` con el literal "REVISAR: P&G afectado por bug VM-USD del motor" para las categorías "RF Internacional" y "Subyacentes de Swaps", de forma estática en cada corrida. Como el Defecto 2 fue cerrado como NO REPRODUCIBLE, esa advertencia es un residuo engañoso.

5.1 WHEN se genera el resumen por corte para RF Internacional o Subyacentes de Swaps THEN el sistema SHALL NO emitir la advertencia estática del "bug VM-USD" (el Defecto 2 fue cerrado como no reproducible); la columna de advertencia SHALL retirarse o quedar vacía/neutra para esas categorías.

### Defecto 6 (mejora de alcance) — Falta exportar el detalle de P&G por título de RF en el Excel por tramos

**Alcance no cubierto.** El detalle por título (`rf_detalle`) YA se calcula y se persiste por corte en `informe_pyg_por_tramo`, pero el exportador por tramos no lo escribe como hoja (sí existe en punta_a_punta como `RF_Detalle_Titulo`).

6.1 WHEN se exporta el Excel por tramos THEN el sistema SHALL escribir el detalle de P&G/ORI por título de RF de cada corte (hoja `RF_Detalle_Titulo_<corte>` o consolidada con columna de corte), reutilizando el `rf_detalle` ya persistido en `informe_pyg_por_tramo`, sin recalcular.

### Defecto 7 (mejora de alcance) — RF_Mensual_Tramos no discrimina RF Local vs Internacional

**Alcance no cubierto.** La hoja `RF_Mensual_Tramos` (y el helper `construir_rf_mensual_contable`) agrega todos los títulos de RF sin separar por moneda.

7.1 WHEN se construye la serie RF mensual THEN el sistema SHALL discriminar RF Local (moneda en {COP, UVR}) de RF Internacional (otras monedas) mediante columnas separadas (p.ej. `pyg_mes_local`/`pyg_mes_intl` y análogas), de modo que la suma local + internacional iguale el total mensual.

7.2 WHEN se añade la discriminación local/internacional al helper compartido THEN el sistema SHALL preservar sin cambios la hoja `RF_Mensual` del modo `punta_a_punta` (las columnas de total existentes no cambian; las nuevas columnas son aditivas).

### Preservación adicional (Fase 2)

3bis WHEN se corrige el fechado del primer tramo (Defecto 3) THEN el sistema SHALL preservar el fechado ene..dic de los tramos completos (1-ene) y el comportamiento del modo `punta_a_punta`.
4bis WHEN se preserva `id_cobertura` (Defecto 4) THEN el sistema SHALL NO alterar la reconstrucción RF del tramo siguiente ni la valoración.
7bis WHEN se discrimina local/intl (Defecto 7) THEN el sistema SHALL mantener la equivalencia byte a byte de los totales mensuales ya existentes.
