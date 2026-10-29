# Feature: Proyección con Curvas por Nodos y Reinversión de Cupones

## Requisitos

### R1: Modelado de Curvas de Rendimiento por Nodos
- [ ] El sistema carga curvas base desde un archivo Excel con nodos (plazo en días → tasa)
- [ ] Soporta múltiples curvas: Deuda Pública TF, Deuda Pública UVR, Corporativos 360, Corporativos 365, Yankees
- [ ] El usuario define un punto final (target) por cada nodo de la curva (horizonte de proyección)
- [ ] El sistema interpola linealmente entre la tasa actual (T+0) y el target para generar la senda de tasas
- [ ] Los puntos intermedios interpolados son sensibilizables mediante sliders independientes por nodo
- [ ] La curva interpolada determina la tasa de descuento de cada título según su plazo residual

### R2: Reinversión Automática de Cupones
- [ ] TODOS los títulos (1CP, 1DV y 1VC) pagan cupón y reinvierten
- [ ] Al cumplirse una fecha de cupón, el flujo se reinvierte al 100% en la misma especie (por defecto)
- [ ] Se crea un "título clonado" con: misma especie, mismas características faciales, mismo vencimiento
- [ ] El nominal del nuevo título = cupón_cobrado / (precio_sucio / 100)
- [ ] La tasa de compra del nuevo título = tasa interpolada de la senda en la fecha del cupón
- [ ] El nuevo título hereda la misma clasificación contable (1CP/1DV/1VC), ramo, portafolio y compañía
- [ ] El usuario puede ver una tabla de "Flujos Futuros" que muestra: cuánto paga cada especie, cuándo

### R3: Panel de Reinversión Configurable
- [ ] Tabla interactiva que muestra todos los cupones futuros por especie, fecha y monto
- [ ] Por defecto: reinvierte en la misma especie (comportamiento automático)
- [ ] El usuario puede sobrescribir la reinversión: elegir otra especie destino, otro ramo, otra clasificación contable
- [ ] Permite simular escenarios tipo: "Los cupones del TES 2031 los reinvierto en CDT Bancolombia clasificado como Disponible en el ramo Automóviles"
- [ ] Los cambios de reinversión impactan: P&L, encaje por ramo, valor de mercado del portafolio

### R4: Motor de Proyección Mensual
- [ ] Simula el portafolio paso a paso por cada período de cupón
- [ ] En cada step: valora, detecta cupones, reinvierte, agrega nuevos títulos al portafolio
- [ ] Calcula P&L período a período (Mercado y TIR)
- [ ] Soporta horizonte configurable (1 a 60 meses)
- [ ] Los nuevos títulos reinvertidos afectan encaje, posición por ramo y P&L en períodos subsiguientes

### R5: Integración con Dashboard
- [ ] Nueva pestaña "📐 Proyección" con:
  - Carga de curvas base y target (Excel)
  - Sliders por nodo para sensibilizar puntos intermedios
  - Visualización de senda lineal por curva
  - Tabla de flujos futuros (qué paga cada título, cuándo)
  - Panel de configuración de reinversión
  - Tabla de reinversiones generadas
  - P&L acumulado por período
  - Impacto en encaje por ramo
  - Comparación de escenarios
