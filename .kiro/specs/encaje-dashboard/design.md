# Design: Tab Encaje por Ramo

## Arquitectura

### 1. Lectura de reservas (`_cargar_reservas_actuales`)
- Usa openpyxl para leer `Formato Información Reservas (13).xlsx` hoja "Reservas"
- Row 2 tiene fechas (para identificar la columna más reciente)
- Row 3 tiene headers: repite (MON. NACIONAL, MON. EXTRANJERA, TOTAL) por cada mes
- Row 4+ tiene datos: col B = nombre ramo, última col TOTAL = saldo actual
- Separadores de compañía: rows con col B = "BOLIVAR" / "COMERCIALES" / "CAPITALIZADORA"
- Aplica `HOMOLOGACION_RESERVAS_A_DICCIONARIO` de `calcular_encaje.py`
- Agrupa ramos duplicados (post-homologación) sumando saldos

### 2. Posición del portafolio (`_cargar_posicion_encaje`)
- `SELECT ramo, compania, SUM(valor_mercado) FROM portafolio_clasificado WHERE encaje='SI' GROUP BY ramo, compania`
- Los ramos ya están asignados por `clasificar_portafolio.py` (usa `param_portafolios` para mapear)

### 3. Cruce posición vs reservas
- Join por ramo (normalizado a UPPER + STRIP)
- Calcula ratio encaje = posición / reservas
- Aplica semáforo

### 4. Layout en app_dash.py
```
┌─────────────────────────────────────────────────────┐
│  Cards: Encaje Global | # Alertas | Pos Total | Res │
├─────────────────────────────────────────────────────┤
│  [Bolívar]  [Comerciales]  [Capitalizadora]         │
├─────────────────────────────────────────────────────┤
│  Ramo 1: ████████████████ Posición ($X MdM)        │
│          ██████████████████████ Reservas ($Y MdM)   │
│          Encaje: 105% 🟢                            │
│─────────────────────────────────────────────────────│
│  Ramo 2: ██████████ Posición ($X MdM)              │
│          ████████████████████████ Reservas ($Y MdM) │
│          Encaje: 98% 🔴                             │
│─────────────────────────────────────────────────────│
│  ...                                                │
└─────────────────────────────────────────────────────┘
```

### 5. Colores (paleta SB)
- Posición cumple: #008f4f (verde primario SB)
- Posición no cumple: #e74c3c (rojo)
- Reservas: #2c3e50 (gris oscuro) con opacidad 0.6
- Cards: borde #008f4f, fondo #f0faf5
- Semáforo: 🟢 #008f4f | 🟡 #c8a415 | 🔴 #e74c3c

### 6. Integración
- Nuevo tab "Encaje" añadido a la barra de tabs existente
- Se renderiza con `layout_encaje()` que retorna el Div completo
- Sin callbacks pesados al inicio (prevent_initial_call=True)
- Las reservas se leen una vez y se cachean

## Archivos a modificar
- `app_dash.py`: Agregar tab "Encaje", función `layout_encaje()`, callback render
- No se modifica `calcular_encaje.py` (se reutiliza su homologación y umbrales)
