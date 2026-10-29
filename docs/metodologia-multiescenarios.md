# Metodología de valoración — Multiescenarios de estrés

> Utilidad periférica `tools/multiescenarios.py`. Corre por fuera del dashboard una
> batería de escenarios de estrés definidos por el usuario (una fila = un escenario
> completo) y saca a Excel el P&L del portafolio por tipo de activo y por compañía.
> Reutiliza el motor de valoración oficial del proyecto (mismas funciones que el
> dashboard) para que los números sean consistentes con la valoración del sistema.

Fecha de corte del análisis: `MAX(fecha_reporte)` de `hist_titulos` (T0 vigente).
Horizonte por defecto: 12 meses (configurable con `--meses`).

---

## 1. Supuestos macro por escenario

Cada escenario (fila del `escenarios_input.xlsx`) define sus propias palancas:

| Columna | Significado | Unidad |
|---|---|---|
| `trm_final` | TRM al final del horizonte | nivel COP |
| `shift_cop_bps` | desplazamiento paralelo del yield de TES COP / deuda pública TF | bps |
| `shift_uvr_bps` | desplazamiento del yield de TES UVR | bps |
| `shift_fs_bps` | desplazamiento del yield de corporativos TF | bps |
| `shift_ipc_bps` | desplazamiento del spread real de corporativos IPC | bps |
| `shift_subyac_bps` | desplazamiento del yield de subyacentes USD (Yankees/globales) | bps |
| `shift_tesoros_bps` | desplazamiento de tesoros 10Y (LEGATRUU/Estructuras) | bps |
| `shift_fed_bps` | desplazamiento de la tasa FED (curvas de derivados) | bps |
| `ipc_ini` / `ipc_fin` | senda de inflación anual | % EA (decimal) |
| `repo_ini` / `repo_fin` | tasa BanRep (TPM) | % anual (decimal) |
| `rv_local` / `rv_intl` / `rv_pei` | retorno EA de COLCAP / MXWD / PEI | % EA (decimal) |
| `ret_alt_local` / `ret_alt_intl` | retorno EA de alternativos local / internacional | % EA (decimal) |
| `spread_4g` | spread de los FCP 4G (IPC + spread) | % (decimal) |
| `cred_ipc` / `cred_ibr` | sendas IPC / IBR para créditos | % (decimal) |

### Sendas temporales

- **TRM:** lineal desde la TRM vigente (T0) hasta `trm_final`, corte a corte mensual.
- **IPC:** lineal desde `ipc_ini` hasta `ipc_fin` (% EA por mes). Alimenta RF (bonos IPC),
  alternativos FCP 4G y la UVR de cierre.
- **UVR:** se capitaliza la UVR vigente (T0) con el IPC promedio de la senda del escenario.
- **Tasa BanRep (repo/TPM):** **escalonada**, no lineal. Solo cambia en los meses con
  decisión de junta {Ene, Mar, Abr, Jun, Jul, Sep, Oct, Dic}; la decisión rige el mes
  siguiente. El paso total `repo_ini → repo_fin` se reparte en escalones iguales entre
  las decisiones del horizonte. Aplica a las cuentas vista COP (FIC y Cuenta de Ahorro).

---

## 2. Valoración por tipo de activo

### 2.1 Renta Fija (RF)

- Motor: `proyectar_portafolio` (mismo del dashboard). Cada título se **re-valora mes a
  mes** con las fórmulas BVC/Deceval, descontando al rendimiento de mercado del papel.
- **Punto de partida (yield):** el rendimiento PIP individual del título (`spread_mercado`),
  tomado de `hist_pip` / `hist_pip_int` a la fecha de corte. El shift del escenario se
  **suma directamente** a ese yield (desplazamiento absoluto en bps), aplicado de forma
  gradual a lo largo del horizonte:
  - TES COP / deuda pública TF → `shift_cop_bps`
  - TES UVR → `shift_uvr_bps`
  - Corporativos TF → `shift_fs_bps`
  - Corporativos IPC → `shift_ipc_bps` (se aplica sobre el **spread real** deflactado y se
    recompone con el IPC vigente de cada mes)
  - Subyacentes USD / Yankees → `shift_subyac_bps`
  - Tesoros 10Y (LEGATRUU / Estructuras) → `shift_tesoros_bps`
- **Títulos internacionales (moneda ≠ COP/UVR):** además del efecto de tasa (subyacente /
  tesoros), se revalúan a la TRM del escenario, por lo que el P&L internacional incluye el
  efecto de tasa **y** el efecto moneda.
- **Subyacentes cubiertos con swap:** los títulos internacionales que están cubiertos con un
  swap se identifican por `id_cobertura` (no vacío) en `portafolio_clasificado` y se reportan
  aparte en `pnl_RF_subyacente` (con su propio efecto moneda `fx_RF_subyacente`). Así
  `pnl_RF_int` queda solo con los internacionales SIN cobertura. El P&L del subyacente en
  moneda origen (sin efecto moneda) = `pnl_RF_subyacente − fx_RF_subyacente`.
- **Cuentas vista COP:** FIC rinde la tasa repo del período; Cuenta de Ahorro rinde
  `max(repo − 6%, 0)`; ambas capitalizan desde su VM inicial.
- **Time deposits / vista USD:** nominal × TRM del corte.

**Criterio contable del P&L (por clasificación):**

| Clasificación | Valoración | P&G | ORI |
|---|---|---|---|
| Negociables (NEG) | Mercado | (VM_mkt − VM_ini) + cupones | 0 |
| Al vencimiento (VC) | Mercado | (VM_mkt − VM_ini) + cupones | 0 |
| Disponibles venta (DV) | Mercado | (VTir − VTir_ini) + cupones | Total_mercado − P&G_TIR |

Es decir, NEG y VC van pleno a resultados a mercado; los DV llevan a P&G solo la causación
a TIR y la diferencia contra mercado queda en ORI (no impacta resultados).

**Base del P&L (contrafactual):** el P&L de RF NO se toma como `pnl_mercado_acum` del motor
(que compara contra el VM contable del 597 y tiene una brecha de base en t0 concentrada en
IPC/UVR). En su lugar se corre **una vez** un contrafactual neutral (todos los shifts en 0,
TRM plana, IPC vigente plano, repo 0) y el P&L de cada escenario es la diferencia contra
esa base. Así se aísla el efecto económico de mover TRM / curvas / spreads / IPC / repo,
cancelando la brecha de base t0 y el carry neutro del horizonte.

### 2.2 Renta Variable (RV)

- Motor: `proyectar_renta_variable`. Composición diaria (base 365) sobre el VM inicial.
- **COLCAP (local):** retorno directo en COP a la tasa `rv_local`.
- **MXWD (internacional):** retorno en USD a la tasa `rv_intl`, convertido a COP con la TRM
  del corte (incluye efecto moneda).
- **PEI:** retorno a la tasa `rv_pei`. Se reporta en columna aparte (`pnl_PEI`), separado de
  `pnl_RV_local` (que queda solo con COLCAP). El PEI se identifica por su especie
  (`asset_class_definitivo = 'PEI'`).

### 2.3 Alternativos (Capital Privado)

- Motor: `proyectar_alternativos`. Composición diaria sobre el VM inicial.
- **Locales:** tasa EA fija `ret_alt_local`.
- **Internacionales:** tasa EA fija `ret_alt_intl` en USD, convertida a COP con la senda TRM.
- **FCP 4G (IPC + spread):** se valoran con la senda de IPC del escenario más el `spread_4g`.

### 2.4 Créditos contratados

- Motor: `proyectar_creditos_contratados`. Sendas IPC/IBR del escenario (`cred_ipc`,
  `cred_ibr`). El P&L es el **costo acumulado de intereses** (signo negativo).
- **Nota:** los créditos usan datos de ejemplo (input definitivo no disponible) y NO se
  desglosan por compañía.

### 2.5 Derivados

- Motor: `valorar_portafolio_derivados`, método 1 (Nominal Estable). Valoración mes a mes.
- **Forwards:** a precio forward con la curva FWPCOP y la tasa COP; se revalúan a la TRM del
  corte. Los que vencen se **renuevan** con una **devaluación implícita** calculada por paridad
  cubierta a partir de las curvas COP (SWTIBR) y USD (LUTIBR) proyectadas al mes de la
  renovación: `tasa_negociada = TRM × (1 + dev)^(100/365)`, con `dev` derivada del diferencial
  de tasas COP−USD (no un valor fijo). El parámetro `dev_forecast = 8,5% EA` queda solo como
  **fallback** si no se puede calcular la dev implícita.
  Comportamiento por escenario: si suben las curvas COP (`shift_cop_bps`) crece la devaluación
  implícita; si suben las curvas USD (`shift_fed_bps` / `shift_tesoros_bps`) disminuye; con
  curvas planas queda casi constante.
- **Opciones (collares):** Black-Scholes con la TRM del corte, volatilidad interpolada y
  curvas r (COP) y q (USD). Ancho del collar (spread Call−Put) = **1.100 COP** (`CANAL_OPC`).
  Las opciones **vencidas** se liquidan con el payoff intrínseco a la TRM del **día de
  vencimiento** y quedan congeladas (no se mueven en cortes posteriores).
- **Descomposición informativa del swap (obligación):** además del `pnl_Swaps` (P&L total),
  se reportan dos columnas informativas de la pata obligación (pasivo), reconstruyendo su VM
  en USD como `vpn_obl_calc / TRM(corte)`:
  - `swap_efecto_curva_obl` = `(VM_obl_usd(t0) − VM_obl_usd(t+1)) × TRM_final`
  - `swap_efecto_moneda_obl` = `(TRM_t0 − TRM_final) × VM_obl_usd(t0)`
  El orden de la resta (t0 − t+1) refleja que un mayor valor del pasivo es pérdida (signo
  negativo). El efecto de curva del **subyacente** ya está en `pnl_RF_subyacente` (no se
  duplica aquí).
- **Swaps:** cross-currency / IRS valorados con las curvas OTC (SWTIBR, UVTIBR, LUTIBR).
- El P&L reportado (`pnl_Forwards`, `pnl_Opciones`, `pnl_Swaps`) es solo el del instrumento
  derivado. **NO** incluye el tipo 'Activos' (posición de contado en USD × TRM), que se
  excluye para no duplicar el efecto moneda ya contenido en RF int y en `efecto_moneda`.

---

## 3. Curvas de valoración de derivados: ¿se mueven? ¿qué toman de referencia?

**Sí se mueven** con los shifts del escenario, escaladas por el progreso del horizonte
(0 en T0 → 1 al final), igual que el motor principal. La referencia es la curva vigente a
la fecha de corte, sobre la que se aplica el delta del escenario:

| Curva | Instrumento | Se mueve con | Referencia base |
|---|---|---|---|
| SWTIBR | Swaps COP / forwards / r COP de opciones | `shift_cop_bps` (delta TES COP) | curva OTC vigente T0 |
| UVTIBR | Swaps UVR | `shift_uvr_bps` (delta TES UVR) | curva OTC vigente T0 |
| LUTIBR (q USD) | Swaps USD / q de opciones | `shift_fed_bps` (corto) + `shift_tesoros_bps` (largo, 10Y) | curva OTC vigente T0 |
| FWPCOP | Puntos forward | — (implícita de r COP / q USD) | curva vigente T0 |

Notas:

- El delta se aplica **proporcional al progreso**: en T0 la curva es la vigente; al final del
  horizonte tiene el shift completo del escenario.
- La **devaluación de renovación de forwards** también se deriva de las curvas (paridad
  cubierta COP−USD), con `dev_forecast = 8,5% EA` solo como fallback. Toda la valoración de
  derivados (forwards vivos y renovados, opciones, swaps) responde al movimiento de curvas.
- Lo único que además varía es la **TRM** (senda del escenario) y, para swaps, la **UVR**
  está fija en el valorador del core.

---

## 4. Efecto moneda (informativo)

Columnas `fx_RF_int`, `fx_RV_int`, `fx_ALT_int` y `efecto_moneda` (total). Se calcula como:

```
efecto_moneda_bloque = VM_inicial_exterior_del_bloque × (trm_final / trm_T0 − 1)
```

Es **informativo** y NO se suma al `pnl_TOTAL`, porque el efecto moneda ya está embebido
dentro de los P&L de los bloques internacionales (RF int, RV int, ALT int). Sirve para
estimar el retorno "sin moneda" de un bloque: `pnl_bloque_int − fx_bloque_int`.

---

## 5. P&L por compañía

Además del consolidado, se produce el desglose por compañía (Bolívar, Capitalizadora,
Comerciales) en todas las columnas:

- **RF / RV / PEI / Alternativos:** por la compañía de cada posición (columna `compania` de
  `portafolio_clasificado`; en alternativos se cruza por `id_posicion`).
- **Derivados:** por el prefijo del portafolio → **01 = Capitalizadora, 02 = Bolívar,
  03 = Comerciales**.
- **Créditos:** excluidos del desglose por compañía.

---

## 6. Estructura del Excel de salida (`resultado_multiescenarios.xlsx`)

- **Resumen:** una fila por escenario con todas las columnas de P&L (consolidado).
- **Resumen_Compania:** una fila por (escenario × compañía) con todas las columnas de P&L
  (sin créditos).
- **VM_Final:** valor de mercado inicial y final por asset_class (RF) / tipo (RV, ALT) y por
  tipo de derivado, con el nominal NETO inicial y final. Opciones: nominal solo de las Put.
  Swaps: nominal del derecho y de la obligación. Para las categorías **internacionales** se
  agrega el nominal en USD: `nominal_usd_ini = vm_inicial / TRM_t0` y
  `nominal_usd_fin = vm_final / TRM_final`.
- **`<Escenario>`:** detalle por posición (RF + RV + Alternativos) al cierre del horizonte.
- **`<Escenario>_Fwd` / `_Opc` / `_Swp`:** sábana de derivados (forwards / opciones / swaps)
  a **todos los cortes** de valoración (una fila por instrumento y corte).

### Columnas de P&L (todas en COP)

`pnl_RF_PyG`, `pnl_RF_ORI`, `pnl_RF_local`, `pnl_RF_int`, `pnl_RF_subyacente`,
`pnl_RV_local`, `pnl_RV_int`, `pnl_PEI`, `pnl_ALT_local`, `pnl_ALT_int`, `pnl_Creditos`,
`pnl_Forwards`, `pnl_Opciones`, `pnl_Swaps`, `swap_efecto_curva_obl`, `swap_efecto_moneda_obl`,
`fx_RF_int`, `fx_RF_subyacente`, `fx_RV_int`, `fx_ALT_int`, `efecto_moneda`, `pnl_TOTAL`.

Donde:

- `pnl_RF_int` = internacionales SIN cobertura; `pnl_RF_subyacente` = cubiertos con swap.
- `swap_efecto_curva_obl` / `swap_efecto_moneda_obl` = descomposición informativa de la
  obligación del swap (no suman al TOTAL; `pnl_Swaps` es el P&L total del swap).
- El P&L "en moneda origen" de un bloque internacional = P&L del bloque − su `fx_*`.

`pnl_TOTAL` = suma de todos los bloques de P&L (RF local+int, RV local+int, PEI, ALT
local+int, créditos, forwards, opciones, swaps). NO incluye `efecto_moneda`.

---

## 7. Consideraciones y límites

- P&L económico/de mercado (para NEG y VC); los DV amortiguan vía ORI. No es el P&L contable
  exacto del cierre oficial.
- Créditos con datos de ejemplo; sin desglose por compañía.
- Devaluación de renovación de forwards: implícita por curvas (paridad cubierta COP−USD);
  8,5% EA solo como fallback.
- UVR de swaps fija en el valorador del core.
- Costo de cómputo: cada corrida RF de 12 meses es intensiva; el contrafactual base se corre
  una vez y los escenarios en paralelo (`--workers`).
