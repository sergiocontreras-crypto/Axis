# Design: Split Vertical — Portafolio Actual vs Proyectado

## Arquitectura de datos

### 1. Generación del portafolio proyectado (en `_ejecutar_escenario`)
Después de correr todos los motores, construir un DataFrame `portafolio_proyectado` con:

```
| Fuente         | Campos que aporta                                      |
|----------------|--------------------------------------------------------|
| Motor RF       | títulos con vm_mercado final + reinversiones            |
| Motor RV       | posiciones con vm final (local + intl + PEI)           |
| Alternativos   | fondos con vm final (local + intl + 4G)                |
| Derivados      | forwards/opciones/swaps con valoración final           |
| Simultáneas    | posiciones con vm final                                |
```

Estructura del DF proyectado (mismas columnas que portafolio_clasificado):
- especie, compania, moneda, valor_mercado, asset_class_definitivo, 
  clasificacion_contable, numeral_regimen, tramo_plazo, encaje, 
  categoria, ramo, portafolio, fecha_vencimiento

### 2. Clasificación rápida (sin reescribir en DB)
Crear función `clasificar_portafolio_en_memoria(df, fecha_fin)` que:
- Recalcula `dias_vencimiento` y `tramo_plazo` respecto a `fecha_fin`
- Re-resuelve `asset_class_definitivo` con la nueva tramo
- No toca la DB — opera sobre el DataFrame en memoria
- Retorna el DF clasificado

### 3. Almacenamiento temporal
Guardar el portafolio proyectado en `store-resultados-escenario` (ya existe) como key adicional:
```python
resultados['portafolio_proyectado'] = df_proyectado.to_dict('records')
```

### 4. Renderización split
En `layout_portafolio` y `update_graficos`:
- Leer `store-resultados-escenario` para obtener el portafolio proyectado
- Si existe: renderizar dos columnas (48% cada una)
- Si no existe: renderizar solo columna izquierda (100% width) como ahora

### 5. Sincronización de escalas
Para cada par de gráficas (actual vs proyectado):
```python
max_range = max(df_actual['valor_mercado'].max(), df_proyectado['valor_mercado'].max())
fig_actual.update_layout(xaxis_range=[0, max_range * 1.1])
fig_proyectado.update_layout(xaxis_range=[0, max_range * 1.1])
```

## Impacto en archivos
- `app_dash.py`: Modificar `_ejecutar_escenario` para generar el DF proyectado, 
  modificar `layout_portafolio` y `update_graficos` para renderizar split
- `clasificar_portafolio.py`: Extraer funciones de clasificación puras 
  (`calcular_tramos_plazo`, `resolver_asset_class`) para uso en memoria
- No se modifica la DB — el portafolio proyectado vive solo en el store de Dash
