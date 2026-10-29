# Changelog — mariapauinforme

## [No publicado]

### Agregado
- Se creó módulo `scripts/valorar_bonos.py` para valoración de bonos de renta fija con fórmula BVC colombiana (NL/365)
- Soporta bonos Tasa Fija (`valorar_bono_tf`) y UVR (`valorar_bono_uvr`) con base NL/365
- Soporta bonos IPC (`valorar_bono_ipc`) con base 30/360 y serie de inflación del Banrep
- Validado contra PIP: TF (diff 100 COP), UVR (diff 411 COP), IPC (diff 82K COP)
- Se agregó pestaña "Sensibilización Valoración TF" al dashboard con impacto por Compañía y Ramo
- Se agregó función `valorar_bono_tips()` y `sensibilizar_tips()` para TIPS con amortización mensual
- Se creó archivo `flujos_tips.csv` con tablas de amortización de 4 ISINs (COT80CH03207, COT80CH03256, COT80CH02555, COT80CH02738)
