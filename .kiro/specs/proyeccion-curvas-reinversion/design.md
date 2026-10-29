# Diseño Técnico: Proyección con Curvas y Reinversión

## Arquitectura

```
Inputs (Excel/Dashboard)
    ├── Curva_Base_T0.xlsx (nodos actuales por curva)
    ├── Curva_Target.xlsx (nodos objetivo al horizonte)
    └── Parámetros (horizonte, periodicidad)
         │
         ▼
┌─────────────────────────────────┐
│   scripts/curvas_rendimiento.py │  ← Nuevo módulo
│   - Carga/interpola curvas      │
│   - Genera sendas lineales      │
│   - Sensibilización por nodo    │
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│   scripts/motor_proyeccion.py   │  ← Nuevo módulo
│   - Loop mensual de simulación  │
│   - Valoración con curvas       │
│   - Detección de cupones        │
│   - Reinversión (clonación)     │
│   - Acumulación de P&L          │
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│   scripts/dashboard_encaje.py   │  ← Pestaña nueva
│   - Tab "📐 Proyección"         │
│   - Sliders por nodo            │
│   - Gráficos de sendas          │
│   - Tabla de resultados         │
└─────────────────────────────────┘
```

## Módulo: curvas_rendimiento.py

### Estructura de datos
```python
Curva = dict[int, float]  # {dias_plazo: tasa_pct}

# Ejemplo:
curva_dp_tf = {
    96: 14.50,    # TES corto
    530: 14.28,   # TES medio
    1768: 14.56,  # TES largo (TFIT10260331)
    5300: 13.97,  # TES ultra largo
}
```

### Funciones principales
```python
def cargar_curvas(ruta_excel: str) -> dict[str, Curva]
def interpolar_tasa(curva: Curva, dias: int) -> float
def generar_senda_lineal(curva_t0: Curva, curva_target: Curva, 
                         meses_horizonte: int) -> dict[int, list[float]]
def aplicar_shock_nodo(senda: dict, nodo_dias: int, mes: int, delta_bps: float)
```

### Interpolación
- Entre nodos: interpolación lineal por plazo
- Para un título con 1200 días al vencimiento:
  - Encuentra nodos vecinos (ej. 936 y 1768)
  - Interpola: tasa = tasa_936 + (tasa_1768 - tasa_936) × (1200-936)/(1768-936)

### Senda lineal
- T+0: curva base (del PIP o Excel)
- T+horizonte: curva target (del Excel del usuario)
- Mes intermedio m: tasa_m = tasa_t0 + (tasa_target - tasa_t0) × m / horizonte

## Módulo: motor_proyeccion.py

### Loop principal
```python
for mes in range(1, horizonte + 1):
    fecha_mes = fecha_inicio + relativedelta(months=mes)
    
    # 1. Obtener curvas del mes (interpoladas en la senda)
    curvas_mes = obtener_curvas_mes(sendas, mes)
    
    # 2. Valorar portafolio actual con curvas del mes
    for titulo in portafolio:
        dias_residual = (titulo.vencimiento - fecha_mes).days
        tasa_mercado = interpolar_tasa(curvas_mes[titulo.tipo_curva], dias_residual)
        titulo.vm_mercado = valorar(titulo, tasa_mercado, fecha_mes)
    
    # 3. Detectar cupones pagados en el mes (TODOS: 1CP, 1DV y 1VC)
    cupones = detectar_cupones(portafolio, fecha_mes_anterior, fecha_mes)
    
    # 4. Reinvertir cada cupón
    for cupon in cupones:
        # Verificar si hay override de reinversión del usuario
        config = obtener_config_reinversion(cupon.titulo, overrides_usuario)
        
        especie_destino = config.especie  # misma por default, o la que elija el usuario
        clasificacion = config.clasificacion  # hereda, o la que elija
        ramo = config.ramo  # hereda, o el que elija
        
        # Calcular nominal del nuevo título
        precio_sucio_destino = obtener_precio_sucio(especie_destino, curvas_mes, fecha_mes)
        nominal_nuevo = cupon.monto / (precio_sucio_destino / 100)
        
        # Clonar título
        nuevo_titulo = clonar_titulo(especie_destino)
        nuevo_titulo.nominal = nominal_nuevo
        nuevo_titulo.tasa_compra = interpolar_tasa(curvas_mes[especie_destino.tipo_curva],
                                                    especie_destino.dias_residual)
        nuevo_titulo.clasificacion = clasificacion
        nuevo_titulo.ramo = ramo
        nuevo_titulo.portafolio = config.portafolio
        
        portafolio.append(nuevo_titulo)
    
    # 5. Calcular P&L y encaje del mes
    registrar_pnl(portafolio, mes)
    registrar_encaje(portafolio, mes)
```

### Valoración con curvas
Cada título se valora con la tasa de la curva correspondiente a su plazo residual:
- TES TF → curva Deuda Pública TF
- TES UVR → curva Deuda Pública UVR
- CDTs/Corporativos → curva Corporativa
- Yankees → curva USD

## Excel de Inputs esperado

### Hoja "Curva_Base" (T+0)
| Curva | Plazo_Dias | Tasa_Pct |
|-------|-----------|----------|
| DP_TF | 96 | 14.50 |
| DP_TF | 530 | 14.28 |
| DP_UVR | 1705 | 7.69 |
| CORP | 843 | 14.57 |

### Hoja "Curva_Target" (horizonte)
| Curva | Plazo_Dias | Tasa_Pct |
|-------|-----------|----------|
| DP_TF | 96 | 10.00 |
| DP_TF | 530 | 11.50 |
| DP_UVR | 1705 | 6.50 |
| CORP | 843 | 12.00 |

## Asignación de curva por título
Desde `portafolio_clasificado.asset_class_definitivo`:
- `DP LP`, `DP MP`, `DP CP` → curva DP_TF
- `DP UVR LP`, `DP UVR MP`, `DP UVR CP` → curva DP_UVR  
- `SF IPC LP`, `SF IPC CP`, `SR IPC LP`, `SR IPC CP` → curva CORP_IPC
- `SRyF FS LP`, `SRyF FS CP` → curva CORP_TF
- `LEGATRUU` → curva USD
