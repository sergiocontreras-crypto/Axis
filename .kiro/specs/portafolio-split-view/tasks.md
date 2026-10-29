# Tasks: Split Vertical — Portafolio Actual vs Proyectado

## Fase 1: Generación del portafolio proyectado
- [ ] 1.1 En `_ejecutar_escenario`, después de todos los motores, construir DataFrame con posiciones finales de RF (incluyendo reinversiones)
- [ ] 1.2 Agregar posiciones finales de RV (COLCAP + MXWD + PEI con VM proyectado)
- [ ] 1.3 Agregar posiciones finales de Alternativos (locales + intl + 4G)
- [ ] 1.4 Agregar posiciones finales de Derivados (forwards + opciones + swaps con valoración final)
- [ ] 1.5 Agregar simultáneas con VM final
- [ ] 1.6 Guardar en `resultados['portafolio_proyectado']`

## Fase 2: Clasificación en memoria
- [ ] 2.1 Extraer `calcular_tramos_plazo` como función pura (ya es pura)
- [ ] 2.2 Crear `clasificar_portafolio_en_memoria(df, fecha_fin)` que recalcula tramo + asset class sin tocar DB
- [ ] 2.3 Aplicar clasificación al DF proyectado antes de guardarlo en el store

## Fase 3: Layout split vertical
- [ ] 3.1 Refactorizar `layout_portafolio` para que las gráficas se generen via función reutilizable `_generar_graficas_portafolio(df, titulo, fecha)`
- [ ] 3.2 Agregar un `html.Div(id='portafolio-proyectado-container')` al layout
- [ ] 3.3 En `renderizar_resultados` o callback separado: si hay portafolio proyectado en el store, renderizar la columna derecha
- [ ] 3.4 Sincronizar escalas de ejes entre ambas columnas

## Fase 4: (DESCARTADO — no implementar por ahora)
~~KPIs de cambio~~
