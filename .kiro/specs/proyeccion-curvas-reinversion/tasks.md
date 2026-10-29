# Tasks: Proyección con Curvas y Reinversión

## Fase 1: Módulo de Curvas de Rendimiento
- [ ] 1.1 Crear `scripts/curvas_rendimiento.py` con funciones de carga desde Excel
- [ ] 1.2 Implementar interpolación lineal entre nodos
- [ ] 1.3 Implementar generación de senda lineal (T0 → Target)
- [ ] 1.4 Implementar función de shock por nodo individual
- [ ] 1.5 Crear Excel template con curvas base (extraer desde PIP actual)

## Fase 2: Motor de Proyección
- [ ] 2.1 Crear `scripts/motor_proyeccion.py` con loop mensual
- [ ] 2.2 Implementar detección de cupones por período (para todos: 1CP, 1DV, 1VC)
- [ ] 2.3 Implementar clonación de títulos (reinversión)
- [ ] 2.4 Implementar cálculo de nominal nuevo (cupón / precio_sucio)
- [ ] 2.5 Implementar asignación de tasa de compra desde senda interpolada
- [ ] 2.6 Implementar herencia de clasificación (contable, ramo, portafolio)
- [ ] 2.7 Implementar config de override de reinversión (especie destino, ramo, clasificación)
- [ ] 2.8 Implementar acumulación de P&L por período (mercado y TIR)
- [ ] 2.9 Implementar cálculo de encaje por ramo por período
- [ ] 2.10 Integrar con funciones existentes de valorar_bonos.py

## Fase 3: Dashboard - Pestaña Proyección
- [ ] 3.1 Agregar pestaña "📐 Proyección" al dashboard
- [ ] 3.2 Implementar carga de Excel de curvas (base y target)
- [ ] 3.3 Implementar sliders por nodo con visualización de senda
- [ ] 3.4 Implementar gráfico de curvas (actual vs target vs intermedia)
- [ ] 3.5 Implementar tabla de flujos futuros (qué paga cada título, cuándo, cuánto)
- [ ] 3.6 Implementar panel de configuración de reinversión con overrides
- [ ] 3.7 Implementar tabla de reinversiones generadas
- [ ] 3.8 Implementar tabla/gráfico de P&L acumulado
- [ ] 3.9 Implementar tabla de encaje por ramo proyectado
- [ ] 3.10 Implementar filtros por compañía, tipo, horizonte

## Fase 4: Validación
- [ ] 4.1 Validar que sin shocks, la proyección a 1 mes coincide con motor actual
- [ ] 4.2 Validar que los cupones se detectan correctamente por fecha
- [ ] 4.3 Validar que la reinversión genera nominales coherentes
- [ ] 4.4 Validar senda lineal vs cálculo manual
- [ ] 4.5 Validar que el encaje proyectado suma correctamente
- [ ] 4.6 Validar que un override de ramo mueve el encaje del ramo destino
