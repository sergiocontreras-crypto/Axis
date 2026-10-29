# Plan — Multiescenarios de estrés (utilidad periférica)

> Estado: PLANEADO, no implementado. Prioridad BAJA (utilidad puntual para directivas).
> No es parte del core del proyecto. Se documenta aquí para ejecutarlo después, sin
> perder las decisiones ya acordadas.

## Objetivo

Correr, por fuera del dashboard, una batería de escenarios de estrés definidos por el
usuario y sacar a Excel el P&L del portafolio **por tipo de activo** para cada escenario.
Ejemplo de solicitud típica de directivas: "¿cómo se mueve el P&L con el dólar a 5000 vs
2000, con las curvas subiendo/bajando 50pbs, RV −40%/+40%, etc.?".

## Naturaleza y restricciones (decisiones acordadas)

- **Utilidad periférica y "escondida":** un script autónomo en `tools/`. NO toca
  `app_dash.py`, ni el motor, ni el flujo core. Quien lo necesite lo corre a mano:
  `python tools/multiescenarios.py`.
- **Reutiliza el motor existente** (mismas funciones que el dashboard), para que los
  números sean consistentes con la valoración oficial.
- Habrá algo de lógica de orquestación NUEVA en `tools/` (replica ligera de lo que hoy
  hace `_ejecutar_escenario` dentro de app_dash, que no se puede reutilizar sin tocar el
  monolito). Se acepta esta duplicación acotada a cambio de no tocar el core.

## Supuesto contable clave

**TODOS los títulos se valoran a mercado.** Para RF se usa `pnl_mercado_acum` del motor
(no `pnl_tir_acum`). Esto significa que incluso los títulos clasificados "a vencimiento"
se estresan a mercado. Es un P&L ECONÓMICO/de mercado (impacto pleno), NO el P&L contable
real (que amortiguaría los títulos a costo amortizado). Es el supuesto correcto para un
análisis de sensibilidad de directivas.

Nota: el motor NO desglosa ORI vs TIR. `pnl_mercado_acum` = cambio total a mercado;
`pnl_tir_acum` = parte de causación/TIR. El ORI implícito sería la diferencia, pero no se
entrega como campo. Para este ejercicio solo interesa el total a mercado.

## Tipo de escenarios: Interpretación A (definidos por el usuario)

Cada fila del Excel de entrada = un escenario COMPLETO donde el usuario fija TODAS las
palancas a la vez. NO es una matriz combinatoria (no se cruzan todos los niveles entre sí).

## Entrada — `tools/escenarios_input.xlsx`

Una fila por escenario. Columnas = todas las palancas del motor (la tabla resumen que
mostró el usuario debe expandirse a TODOS los shifts que el motor acepta, no solo 3):

| Columna | Mapea a (motor) | Unidad |
|---------|-----------------|--------|
| `escenario` | etiqueta | texto |
| `trm_final` | `trm_cierre` (TRM inicio = vigente) | nivel COP |
| `shift_cop_bps` | curva TES COP / TF (`shift_cop_bps`) | bps |
| `shift_uvr_bps` | curva UVR (`shift_uvr_bps`) | bps |
| `shift_fs_bps` | spread corp TF (`shift_fs_bps`) | bps |
| `shift_ipc_bps` | spread corp IPC (`shift_ipc_bps`) | bps |
| `shift_subyac_bps` | subyacente USD/Yankees (`shift_subyac_bps`) | bps |
| `shift_tesoros_bps` | tesoros 10y (`shift_tesoros_bps`) | bps |
| `rv_local` | RV local COLCAP (`escenarios_rv_local={'base':...}`) | % EA |
| `rv_intl` | RV intl MXWD (`escenarios_rv_intl`) | % EA |
| `rv_pei` | PEI (`tasa_pei`) | % EA |
| `ret_alt_local` | alternativos local (`tasa_ea_local`) | % EA |
| `ret_alt_intl` | alternativos intl (`tasa_ea_intl`) | % EA |
| `spread_4g` | spread FCP 4G IPC (`spreads_4g`) | % |
| `cred_ipc` | senda IPC créditos | % |
| `cred_ibr` | senda IBR créditos | % |

Config global (constantes del script, no por escenario):
- **Fecha de corte = vigente** (MAX `fecha_reporte` de `hist_titulos`).
- **Horizonte = input** (`HORIZONTE_MESES`): 12 / 24 / 60, o hasta fin de año.
- **TRM/UVR/IPC iniciales (T0) = vigentes** de la fuente (obtener_*_para / gestor_informes).

## Salida — `tools/resultado_multiescenarios.xlsx`

- Una fila por escenario, con sus palancas visibles a la izquierda (formato tipo la imagen
  del usuario: niveles de dólar + movimientos + retornos + spreads).
- Columnas de P&L **a mercado** por bloque: **RF | RV | Alternativos | Créditos | Derivados | TOTAL**.
- TOTAL = suma de los bloques = P&L del portafolio en ese escenario.

## P&L por bloque (cómo se extrae de cada motor)

- **RF** → `proyectar_portafolio(...)` → `pnl_mercado_acum` del último `ResultadoMes`.
- **RV** → `proyectar_renta_variable(...)` → VM_final − VM_inicial del bloque.
- **Alternativos** → `proyectar_alternativos(...)` → VM_final − VM_inicial del bloque.
- **Créditos** → `proyectar_creditos_contratados(...)` → costo acumulado de intereses (signo negativo).
- **Derivados** → `valorar_portafolio_derivados(...)` → P&L de la función.

(Regla general: P&L de cada bloque = variación de valor de mercado en el horizonte;
créditos = costo acumulado.)

## Firmas reales del motor (verificadas)

- `proyectar_portafolio(portafolio, fecha_inicio, meses_horizonte, sendas, valor_uvr,
  valor_uvr_cierre, trm, trm_cierre, serie_ipc, senda_inflacion, shift_ipc_bps,
  shift_cop_bps, shift_uvr_bps, shift_fs_bps, shift_subyac_bps, shift_tesoros_bps,
  ipc_vigente_t0, senda_repo)` → list[ResultadoMes] (tiene vm_total, pnl_mercado_acum, pnl_tir_acum).
- `proyectar_renta_variable(fecha_inicio, fecha_fin, escenarios_rv_local, escenarios_rv_intl,
  tasa_pei, ruta_db, senda_trm)` → DataFrame.
- `proyectar_alternativos(fecha_inicio, fecha_fin, tasa_ea_local, tasa_ea_intl,
  ruta_db_portafolio, senda_ipc, spreads_4g, senda_trm)` → DataFrame.
- `proyectar_creditos_contratados(fecha_inicio, df_creditos, df_senda_ipc, df_senda_ibr,
  df_senda_sofr, df_senda_trm)` → DataFrame.
- `valorar_portafolio_derivados(fecha_corte, fecha_fin, ..., senda_trm=list, ...)` → dict de DataFrames.

## Armado de `sendas` (curvas base para RF)

Igual que el escenario del dashboard:
```python
curvas_base = extraer_curva_base_pip_db(DB_PATH, fecha_corte)  # curvas PIP vigentes
sendas = {}
for nombre, curva in curvas_base.items():
    sendas[nombre] = generar_senda_lineal(curva, curva, n_meses)  # plana; los shifts
                                                                  # los aplican los shift_*_bps
```
Los movimientos de curva del escenario se aplican por los parámetros `shift_*_bps` (que el
motor aplica título a título), no modificando la curva base. Así "curva +50pbs" = shift de
50 bps sobre la curva vigente.

## Esqueleto del script `tools/multiescenarios.py`

```
CONFIG: HORIZONTE_MESES, RUTA_INPUT, RUTA_OUTPUT
1. fecha_corte = MAX(fecha_reporte) de hist_titulos
2. TRM/UVR/IPC vigentes T0
3. curvas_base = extraer_curva_base_pip_db(...) (una vez)
4. leer escenarios_input.xlsx -> lista de escenarios
5. por cada escenario:
     - sendas (plana desde curva base)
     - RF   = proyectar_portafolio(..., shifts, trm=vigente, trm_cierre=trm_final) -> pnl_mercado_acum
     - RV   = proyectar_renta_variable(...)   -> VM_fin - VM_ini
     - ALT  = proyectar_alternativos(...)     -> VM_fin - VM_ini
     - CRED = proyectar_creditos_contratados(...) -> costo intereses acum
     - DERIV= valorar_portafolio_derivados(...) -> P&L
6. volcar a Excel: filas = escenarios (palancas visibles) x columnas P&L por bloque + TOTAL
```

## Pendientes al implementar

- Generar plantilla `escenarios_input.xlsx` con 2-3 escenarios de ejemplo (dólar 5000/2000
  con los movimientos del ejemplo del usuario) para validar formato.
- Confirmar límites de tiempo: cada escenario corre los 5 motores; N escenarios = N× tiempo.
  Con horizontes largos (60m) y muchos escenarios puede ser lento (correr en consola, no en UI).
- Manejar fechas: horizonte "hasta fin de año" vs 12/24/60 meses fijos.
