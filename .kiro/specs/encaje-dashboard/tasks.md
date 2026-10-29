# Tasks: Tab Encaje por Ramo

## Fase 1: Datos
- [ ] 1.1 Crear función `_cargar_reservas_excel()` que lea el Formato Reservas (13).xlsx, extraiga última columna TOTAL por ramo, identifique compañía, y aplique homologación
- [ ] 1.2 Crear función `_cargar_posicion_encaje()` que agrupe portafolio_clasificado por ramo y compania para encaje=SI
- [ ] 1.3 Crear función `_calcular_encaje()` que cruce posición vs reservas y calcule ratio + semáforo

## Fase 2: Layout
- [ ] 2.1 Agregar tab "Encaje" a la barra de tabs en app_dash.py
- [ ] 2.2 Crear `layout_encaje()` con cards resumen arriba + gráfico de barras dobles abajo
- [ ] 2.3 Implementar barras dobles horizontales (Plotly go.Bar grouped) con paleta SB
- [ ] 2.4 Agregar selector de compañía (Bolívar/Comerciales/Capitalizadora/Todas)

## Fase 3: Interactividad
- [ ] 3.1 Callback para filtrar por compañía y actualizar gráfica
- [ ] 3.2 Cards dinámicas con encaje global y conteo de alertas

## Fase 4: Proyecciones (segunda iteración)
- [ ] 4.1 Agregar selector de mes y escenario
- [ ] 4.2 Integrar con `Inputs_Planeacion_Reservas.xlsx` para variaciones acumuladas
- [ ] 4.3 Recalcular encaje proyectado y actualizar visualización
