# Feature: Split Vertical — Portafolio Actual vs Proyectado

## Descripción
El Tab Portafolio muestra dos columnas lado a lado:
- **Izquierda**: Portafolio actual (597 del día)
- **Derecha**: Portafolio proyectado (resultado del escenario)

Las escalas de los ejes deben ser compartidas entre ambas columnas para que la comparación visual sea honesta.

## Requisitos

### R1: Construcción del portafolio proyectado
- Después de correr el escenario, reconstruir un "597 proyectado" con:
  - Títulos RF con VM final del motor (incluyendo reinversiones de cupones como nuevos títulos)
  - Posiciones RV con VM proyectado al cierre
  - Posiciones Alternativos con VM proyectado al cierre
  - Derivados (forwards, opciones, swaps) con valoración final
  - Simultáneas con VM proyectado
- Los títulos que vencieron durante el horizonte se eliminan
- Las reinversiones de cupones aparecen como nuevas posiciones

### R2: Clasificación del portafolio proyectado
- Aplicar la misma lógica de clasificar_portafolio al 597 proyectado:
  - Recalcular tramo_plazo (con la fecha fin como nueva fecha de corte)
  - Reasignar asset_class_definitivo (el plazo cambió → puede cambiar de LP a MP)
  - Recalcular encaje y solvencia
  - Asignar compañía/ramo

### R3: Visualización split vertical
- Columna izquierda (48% width): Portafolio actual
  - Título: "📊 Actual — {fecha_inicio}"
  - Gráficas: Asset Class (barras H), Solvencia (donut), Plazo (barras V), Régimen (barras agrupadas)
  - Tabla resumen por compañía
- Columna derecha (48% width): Portafolio proyectado
  - Título: "📈 Proyectado — {fecha_fin}"
  - Mismas gráficas con mismos estilos
  - Tabla resumen por compañía
- La columna derecha solo aparece si se ha corrido un escenario

### R4: Escalas compartidas
- Las gráficas de barras (Asset Class, Plazo) deben usar el mismo rango de eje X/Y en ambas columnas
- Calculado como max(max_actual, max_proyectado) para que la comparación no distorsione
- El donut de solvencia mantiene escala relativa (porcentajes) — no necesita sincronización

### R5: Indicadores de cambio
- Debajo de las dos columnas, una fila de KPIs con flechas:
  - VM Total: $X → $Y (↑/↓ Z%)
  - # Posiciones: N1 → N2
  - Principales cambios por asset class (top 3 que más crecieron / bajaron)

## Criterios de Aceptación
- [ ] La columna derecha no aparece hasta que se corra un escenario
- [ ] Las reinversiones del motor RF aparecen como nuevas posiciones en el proyectado
- [ ] Las escalas de ejes son iguales en ambas columnas para gráficas de barras
- [ ] Los colores y formato son consistentes (paleta SB, _fmt_cop)
- [ ] El portafolio proyectado excluye colaterales y simultáneas (misma lógica que el actual)
