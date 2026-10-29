# AXIS — Continuación de sesión

## Estado actual del proyecto (27 ago 2026)

### Lo que funciona
- **Tab Portafolio**: Split visual Actual vs Proyectado con grid 2x2 idéntico (AC, Solvencia, Régimen, Plazo).
- **Tab Inputs**: Secciones 1-9. Botón "CORRER ESCENARIO" ejecuta 6 motores. Senda TRM/Inflación desde Sección 2. UVR auto-calculada de TUVT. Curvas EEUU como delta bps con gráfica reactiva.
- **Tab Resultados**: P&L/ORI/Total por compañía, asset class, clasificación. Créditos NO suman al P&L. Derivados excluyen Activos.
- **Tab Encaje**: Barras dobles posición vs reservas por ramo, semáforo.
- **Tab Causación**: Resumen por clasificación contable.
- **Derivados**: Motor completo determinista (forwards + opciones + swaps). Curva EEUU (Δ Fed, Δ Tesoros 10Y) afecta opciones y swaps. TRM nivel_actual = primer punto de senda.
- **RV**: Conectado a portafolio_clasificado. Internacionales proyectan en USD + conversión con senda TRM. T0 sin retorno.
- **Alternativos**: IPC+Spread usa senda inflación de Sección 2 (desfasada 1 mes). Internacionales con senda TRM. GLDM excluido (es MXWD). T0 sin retorno.
- **Créditos**: Proyección base.
- **Motor RF**: Excluye RV, Alternativos, PEI, COCAP, ETFs del motor de valoración. Total resultado incluye cupones cobrados. Guarda snapshots mensuales por título.
- **Gestor Informes**: Procesa 597, PIP, FWD (carga en hist_forwards), OPC (carga en hist_opciones), SWP (695, carga en hist_swaps), CURV, VOL, OTC, IPC. Semáforos incluyen SWP.
- **Servidor**: `debug=False, port=8063, threaded=True`

---

## Cambios sesión 27 ago 2026

### Principales
1. **Nuevo asset class "Estructuras"** — 69 subyacentes de Swaps + 68 Swaps reclasificados de LEGATRUU/Derivados a "Estructuras". Usan curva USD. Los 10 Yankees independientes conservan LEGATRUU.
2. **RF_Mensual contable — lógica punta a punta por período** — La hoja RF_Mensual del Excel ahora usa: `total_mes = (VM_mes - VM_ant) + cupones`, `pyg_mes = (VTIR_mes - VTIR_ant) + cupones`, `ori_mes = total - pyg`. Usa los agregados del motor directamente (no snapshots individuales por título, que tenían salto de base en VTIR).
3. **Gráfica Curva EEUU reactiva con inputs T0** — Se agregan inputs de "Fed T0 (%)" y "Tesoros 10Y T0 (%)" para que el usuario defina los niveles actuales de la curva. La gráfica interpola nodos intermedios (2Y, 5Y) entre Fed y 10Y. El motor sigue usando solo los deltas.
4. **ETF UCITS Ibond 2028** → Excepción en `param_excepciones`: asset_class=LEGATRUU, tipo_entidad=Vehiculo_Inversion, numeral_regimen=2.5. Ya no se clasifica como RV.
5. **01-TE insertado en param_portafolios** — ramo=CAPITALIZADORA, excluir=SI.
6. **Orquestador RF (`scripts/orquestador_valoracion_rf.py`)** — Wrapper del motor_proyeccion.py que genera Excel con schema estricto y validación controlada. Usa fin de mes con dateutil. En revisión (ver pendientes).

---

## 🔴 EN REVISIÓN: RF_Mensual — Problema de consistencia mes a mes

### Contexto
La hoja RF_Mensual del Excel muestra PyG/ORI/Total por mes. El cálculo "punta a punta" (horizonte completo) coincide con RF_Detalle_Titulo. Pero la **distribución mensual** del ORI muestra un spike en el mes 1 (~6,720 MM) que no es real.

### Causa raíz
El motor de RF (`motor_proyeccion.py`) inicializa VTIR con `t.valor_tir` del portafolio (viene de `hist_titulos`). Al re-valorar en el mes 1 con su propia fórmula interna (especialmente para bonos IPC con método QES-TIR), el VTIR recalculado difiere del valor cargado → salto de base en el primer período.

### ✅ SOLUCIÓN APLICADA (27 ago 2026 — sesión nocturna)
Se reemplazó la lógica de RF_Mensual para que use **snapshots por título con la misma fórmula contable de RF_Detalle_Titulo** (NEG/DV/VC). La suma telescópica mensual ahora coincide exactamente con el detalle título a título:
- Solo se incluyen títulos vivos al final del horizonte (misma población que RF_Detalle_Titulo).
- Para cada título, se calcula: `total_mes = (VM[m] - VM[m-1]) + cupones[m]`, `pyg_mes = (VTIR[m] - VTIR[m-1]) + cupones[m]` (para DV), etc.
- La suma acumulada de `total_resultado_mes` en RF_Mensual == suma de `total_resultado` en RF_Detalle_Titulo.
- El orquestador (`orquestador_valoracion_rf.py`) también usa la misma lógica.

---

## PENDIENTE PRÓXIMA SESIÓN

### 1. ✅ RF_Mensual — Consistencia con RF_Detalle_Titulo RESUELTA
- RF_Mensual ahora usa snapshots por título con la misma lógica contable NEG/DV/VC.
- Sum(RF_Mensual.total_resultado_mes) == Sum(RF_Detalle_Titulo.total_resultado).
- Sum(RF_Mensual.pyg_mes) == Sum(RF_Detalle_Titulo.pyg).

### 2. ✅ Senda TRM — 4 nodos extra RESUELTO
- `generar_senda_lineal` ahora extiende la senda 4 meses más allá de `fecha_fin`.
- Los nodos extra mantienen el valor final de TRM (plano).
- Renovaciones de forwards en diciembre ya tienen TRM disponible.

### 3. 🟡 Forwards — Renovaciones en diciembre
- Los forwards que vencen en diciembre podrían no estar renovándose correctamente.
- La función `_renovar_fwds` usa `trm_df.merge` y `dropna` — si la senda TRM no llega a la fecha de constitución del renovado, se pierde.
- Verificar: ¿el nominal vivo al 31-Dic es consistente con 83 forwards?

### 3. 🟡 Fórmula IPC+Spread FCP 4G
- Tu fórmula manual vs motor — definir cuál usar.

### 4. 🟢 IPC automático desde API
- Endpoint de datos.gov.co pendiente.

### 5. 🟢 Vista proyectada — ~3B diferencia
- Diagnóstico pendiente.

---

## Inputs del motor de derivados (referencia rápida)

Estos son los parámetros que recibe `valorar_portafolio_derivados()`:

| Parámetro | Qué es | De dónde sale en el dashboard |
|---|---|---|
| `fecha_corte` | Fecha del portafolio base (YYYY-MM-DD) | sidebar-fecha-inicio |
| `fecha_fin` | Fin del horizonte de proyección | sidebar-fecha-fin |
| `metodo` | 1=Nominal Estable, 3=Exp Objetivo, 4=Dejar vencer collares, 5=Delta Hedge | Sección 5 dropdown |
| `nivel_actual` | TRM spot para valorar en T0. Si hay senda en store → senda[0]. Si no → hist_opciones.trm | Sección 2 (TRM actual) o hist_opciones |
| `activos_usd` | Posición neta en USD = VM(títulos moneda≠COP,UVR) / TRM. **NO incluye Estructuras/swaps** | Calculado de portafolio_clasificado |
| `portafolio_pesos` | VM total de títulos en COP+UVR | Calculado de portafolio_clasificado |
| `senda_trm` | Lista [TRM_T0, TRM_mes1, ..., TRM_fin]. Lineal si no hay senda precargada | Sección 2 → store-sendas, o linspace(TRM_actual, TRM_fin, n_meses+1) |
| `dev_forecast` | Devaluación anual implícita para calcular strike de renovaciones: `(TRM_fin/TRM_ini)^(365/dias)-1`. Default=0.085 si no se pasa | Calculado o default |
| `canal_opc` | Spread Call-Put en collares (COP, default=1650) | Sección 5 |
| `metodo_params` | Dict con: delta_tes_cop, delta_tes_uvr, delta_fed_bps, delta_10t_bps, exposicion_obj, etc. | Sección 3 (shifts) + Sección 5 (método 3) |

### Cómo afecta cada shift a forwards:
- **Δ TES COP (Sección 3)** → mueve la curva SWTIBR que DESCUENTA forwards. Más tasa = menor VP = menor pérdida.
- **Δ Fed / Δ Tesoros 10Y (Sección 4)** → NO afecta forwards directamente. Solo afecta opciones (tasa q en Garman-Kohlhagen) y swaps (curva LUTIBR de descuento).
- **Senda TRM** → determina la TRM en cada corte mensual para valorar forwards vivos y liquidar vencidos.
- **dev_forecast** → determina el strike de las renovaciones: `Strike = TRM_constitución × (1 + dev)^(100/365)`.

---

## Inputs del motor RF (referencia rápida)

Parámetros de `proyectar_portafolio()`:

| Parámetro | Qué es | Unidad |
|---|---|---|
| `fecha_inicio` | Fecha T0 de valoración | date |
| `meses_horizonte` | Número de meses a proyectar | int |
| `sendas` | Curvas de rendimiento por nodo por mes: {curva: {nodo_dias: [tasas_mensuales]}} | dict |
| `valor_uvr` / `valor_uvr_cierre` | UVR inicial y final (senda lineal interna) | float |
| `trm` / `trm_cierre` | TRM inicial y final (para Yankees y Estructuras) | float |
| `serie_ipc` | DataFrame con serie histórica de inflación | pd.DataFrame |
| `senda_inflacion` | Lista de IPC anual (%) por mes para bonos IPC | list[float] |
| `shift_cop_bps` | Shift paralelo de curva DP_TF (bps) | float |
| `shift_uvr_bps` | Shift paralelo de curva DP_UVR (bps) | float |
| `shift_fs_bps` | Shift paralelo de curva CORP_TF (bps) | float |
| `shift_ipc_bps` | Shift paralelo de curva CORP_IPC (bps) | float |
| `shift_tesoros_bps` | Shift de curva USD / LEGATRUU / Estructuras (bps) | float |
| `ipc_vigente_t0` | IPC vigente para deflactar spreads de mercado PIP (decimal, ej: 0.06) | float |

### Cómo funciona el motor RF mes a mes:
1. **Detectar cupones** del período → acumular por título
2. **Reinvertir vencimientos** → crear títulos clonados con plazo y tasa similar
3. **Reinvertir cupones** → crear títulos nuevos con monto del cupón
4. **Re-valorar cada título** con tasa de senda + shift aplicado gradualmente
5. **Calcular VTIR** con tasa de compra constante (para P&L contable)
6. **Guardar snapshot** por título: {vm, vtir, cupones} en cada corte

### Lógica contable mensual (agregada):
```
total_mes = (VM_mes - VM_ant) + cupones_mes
pyg_mes   = (VTIR_mes - VTIR_ant) + cupones_mes
ori_mes   = total_mes - pyg_mes
```

---

## Lógica contable RF por título (punta a punta)
| Clasificación | total_resultado | P&L (ER) | ORI (Patrimonio) |
|---|---|---|---|
| NEG | (vm_mercado - vm_ini) + cupones | = total | 0 |
| DV | (vm_mercado - vm_ini) + cupones | (vtir_fin - vtir_ini) + cupones | total - pyg |
| VC | (vtir_fin - vtir_ini) + cupones | = total | 0 |

---

## Archivos clave y sus roles

| Archivo | Rol |
|---------|-----|
| `app_dash.py` | Dashboard principal (Dash), puerto 8063 |
| `valoracion_derivados.py` | Motor Derivados (forwards + opciones + swaps) |
| `scripts/motor_proyeccion.py` | Motor RF (valoración, reinversiones, snapshots mensuales) |
| `scripts/orquestador_valoracion_rf.py` | Wrapper del motor RF para Excel independiente con schema |
| `scripts/valorar_bonos.py` | Funciones de valoración BVC (TF, Corporativo, Yankee, UVR, IPC) |
| `scripts/curvas_rendimiento.py` | Carga PIP, interpola, genera sendas. ASSET_CLASS_A_CURVA incluye "Estructuras" |
| `scripts/proyeccion_renta_variable.py` | Motor RV con senda TRM |
| `scripts/proyeccion_alternativos.py` | Motor Alternativos con IPC dinámico y senda TRM |
| `scripts/proyeccion_creditos.py` | Motor Créditos |
| `gestor_informes.py` | Dispatch del drop zone + carga FWD/OPC/SWP |
| `libro_de_funciones.py` | Funciones de limpieza y valoración BS |
| `diccionario_titulos.py` | Diccionario atemporal incremental |
| `clasificar_portafolio.py` | Clasificación diaria + en_memoria. Paso 6.6 asigna "Estructuras" |
| `portfolio_loader_beta.py` | Carga 597 a hist_titulos. Excluye portafolios con excluir=SI |

---

## Reglas críticas
- **NO duplicar IDs**: cada `id=` UNA sola vez en todo el DOM
- **`prevent_initial_call=True`**: en TODOS los callbacks pesados
- **Paleta SB**: primario #008f4f
- **Servidor**: `app.run(debug=False, port=8063, threaded=True)`
- **Cache**: borrar `__pycache__` al modificar scripts
- **NO TOCAR la regla del 6to día hábil** en valorar_bonos.py
- **DB_PATH**: Todos los archivos en raíz usan `SCRIPT_DIR / "portafolio.db"`
- **BOM**: gestor_informes.py puede coger BOM al editar con PowerShell — limpiar con `lstrip(b'\xef\xbb\xbf')`
- **Portafolios excluidos**: 01-TE (Capitalizadora), 02-TE (Rentas Vitalicias), 03-TE (Terremoto) → excluir=SI en param_portafolios
- **ETF UCITS Ibond 2028**: excepción → LEGATRUU (no RV). Tipo_entidad=Vehiculo_Inversion, numeral=2.5
- **Derivados activos_usd**: NO incluir títulos "Estructuras" (son subyacentes de swaps, no exposición libre)
