# Feature: Tab Encaje por Ramo — Dashboard AXIS

## Descripción
Nuevo tab "Encaje" en el dashboard Dash que muestra el ratio de encaje (posición del portafolio / reservas) por ramo y compañía. Incluye semáforo de alertas, barras visuales comparativas, y soporte para proyecciones mensuales.

## Fuentes de datos
1. `portafolio_clasificado` (SQLite) — posiciones con `encaje='SI'`, agrupadas por `ramo` y `compania`
2. `Formato Información Reservas (13).xlsx` — hoja "Reservas": saldo actual por ramo (última columna TOTAL)
3. `Inputs_Planeacion_Reservas.xlsx` — variaciones mensuales proyectadas por ramo/escenario
4. `calcular_encaje.py` — lógica existente (homologación, umbrales, semáforo)

## Requisitos

### R1: Carga de reservas actuales
- Leer la hoja "Reservas" del `Formato Información Reservas (13).xlsx`
- Extraer última columna TOTAL (la más reciente) por cada ramo
- Identificar compañía (BOLIVAR, COMERCIALES, CAPITALIZADORA) como separador de filas
- Aplicar homologación de nombres (tabla `HOMOLOGACION_RESERVAS_A_DICCIONARIO` en `calcular_encaje.py`)

### R2: Cálculo de posición del portafolio por ramo
- Desde `portafolio_clasificado` filtrar `encaje='SI'`
- Agrupar por `ramo` y sumar `valor_mercado`
- Mapear a la misma estructura de ramos que las reservas

### R3: Cálculo del encaje
- `Encaje_Ramo = Posición_Portafolio / Reservas`
- Umbrales por compañía: Bolívar 102%, Comerciales 103%, Capitalizadora 100%
- Semáforo: 🟢 >= umbral+5pp, 🟡 entre umbral y umbral+5pp, 🔴 < umbral

### R4: Visualización — Barras dobles horizontales
- Para cada ramo, una barra doble:
  - Barra superior (verde SB #008f4f): posición del portafolio
  - Barra inferior (gris oscuro #2c3e50): reservas requeridas
  - Si posición < reservas: barra posición se pinta roja (#e74c3c)
- Agrupado por compañía (3 secciones o tabs internos)
- Ordenado de mayor a menor por reservas
- Labels con formato _fmt_cop()

### R5: Cards resumen
- Encaje global (total posición / total reservas)
- # Ramos en alerta (🔴)
- Posición total vs Reservas totales
- Fecha de las reservas (del Excel)

### R6: Soporte de proyecciones (segunda iteración)
- Selector de mes de proyección (enero a diciembre)
- Selector de escenario (Base, Negativo, Positivo)
- Acumula variaciones desde enero hasta el mes seleccionado
- Actualiza barras y semáforo dinámicamente

## Criterios de Aceptación
- [ ] El tab aparece como "Encaje" en la barra de tabs
- [ ] Las reservas se cargan correctamente del Excel (homologación de nombres)
- [ ] La posición del portafolio se lee de portafolio_clasificado (encaje=SI)
- [ ] Barras dobles posición vs reservas con paleta SB
- [ ] Semáforo correcto según umbrales por compañía
- [ ] Cards resumen visibles arriba
- [ ] Sin errores si no hay reservas cargadas (mostrar mensaje informativo)
