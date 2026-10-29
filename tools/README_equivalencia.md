# Verificación de equivalencia numérica — Agrupación de Negociables fungibles

Procedimiento de **no-regresión** para la feature `optimizacion-agrupacion-negociables`
(spec en `.kiro/specs/optimizacion-agrupacion-negociables/`).

La consolidación de reinversiones Negociables fungibles es una **optimización de
rendimiento, NO un cambio de valoración**. Este documento deja los comandos exactos para
confirmar, contra la **DB real** (`portafolio.db`), que:

1. El motor RF sigue produciendo los mismos números tras el cambio
   (`tools/equivalencia_motor_rf.py` → `baseline` / `check`, tolerancia `1e-6`).
2. Los snapshots de cierre CON vs SIN consolidación son equivalentes por corte y por
   grupo fungible (`tools/comparar_snapshots_equivalencia.py`).
3. El horizonte completo de 3 tramos (**2026→2028**) termina, persiste los tres cortes
   (incluido **dic-2028** con sus hojas de Excel) y los dos primeros tramos quedan dentro
   de tolerancia.

> **Importante — estas corridas son MANUALES.** Se ejecutan contra la **DB real** y son
> **procesos largos** (minutos para el arnés corto; horas para el horizonte de 3 tramos y
> para `check` a 12 meses). **No se automatizan como parte del flujo del agente.** El
> agente entrega el arnés y las aserciones; la ejecución efectiva la hace el usuario.

Referencia del spec: Requirement 3 (equivalencia), Requirement 7 (tercer tramo), tabla
"Testing Strategy" de `design.md`, y el Glosario (`Arnes_Equivalencia`,
`Tolerancia_Equivalencia`). `Tolerancia_Equivalencia = 1e-6` (relativo, valor de
referencia histórico del proyecto).

Todos los comandos se ejecutan **desde la raíz del repositorio** (`g:\Mi unidad\AXIS\AXIS`),
con el entorno Python del proyecto (Python 3.12+). Ninguno de estos scripts modifica la DB
(solo lectura).

---

## 1. Equivalencia del motor RF (`equivalencia_motor_rf.py`)

Subcomandos reales del arnés (ver cabecera del script):

| Subcomando | Qué hace |
| ---------- | -------- |
| `baseline` | Corre el motor sobre el portafolio real y guarda el resultado en `tools/baseline_motor_rf.json`. |
| `check` | Vuelve a correr el motor y compara contra el baseline con **tolerancia `1e-6`**. Sale con código `1` y lista las diferencias si NO es equivalente. |
| `profile` | Perfila el motor (cProfile, top 30 funciones por tiempo acumulado). Diagnóstico; no interviene en la equivalencia. |

Parámetros del caso (fijados en el script, no por flags):

- `FECHA_BASE = "2026-07-31"` (fecha base real con datos).
- Horizonte en meses: variable de entorno **`AXIS_EQUIV_MESES`** (por defecto `3`).
  La verificación final conviene repetirla con `12` meses.
- Tolerancia `TOL = 1e-6` (absoluta/relativa combinada), fija en el script.

### 1.1 ANTES del cambio — capturar baseline

Ejecutar con el código **SIN** la consolidación (p. ej. en la rama/commit previo a
integrar la feature):

```powershell
# Horizonte corto por defecto (3 meses): ciclo rápido baseline -> optimizar -> check
python tools/equivalencia_motor_rf.py baseline

# (Opcional, recomendado para la verificación final) horizonte de 12 meses
$env:AXIS_EQUIV_MESES = "12"; python tools/equivalencia_motor_rf.py baseline
```

Esto escribe `tools/baseline_motor_rf.json`. **Conservar ese archivo** hasta terminar la
verificación (es el patrón de comparación).

### 1.2 DESPUÉS del cambio — verificar equivalencia (tolerancia `1e-6`)

Con el código **CON** la consolidación ya integrada, y **el mismo** `AXIS_EQUIV_MESES`
que se usó para el baseline:

```powershell
# Debe reportar: "OK EQUIVALENTE: el resultado es idéntico al baseline (tol=1e-6)."
python tools/equivalencia_motor_rf.py check

# Verificación final a 12 meses (debe seguir siendo EQUIVALENTE)
$env:AXIS_EQUIV_MESES = "12"; python tools/equivalencia_motor_rf.py check
```

Criterio de aceptación (Req 3.5, 3.6):

- **PASA** si imprime `OK EQUIVALENTE` (sin diferencias, dentro de `1e-6`).
- **FALLA** si imprime `DIFERENCIAS (...)` y termina con código `1`. En ese caso la
  implementación se considera **incorrecta y no se integra** (Req 3.6).

> El baseline y el check deben capturarse con el **mismo** `AXIS_EQUIV_MESES`; comparar
> horizontes distintos produce un falso `n_meses` distinto.

---

## 2. Equivalencia de snapshots por corte (`comparar_snapshots_equivalencia.py`)

Complementa al arnés del motor: compara **dos snapshots del mismo corte**, uno SIN
consolidación y otro CON consolidación, agregando por `Clave_Fungibilidad`. Verifica VM
total, VTIR total (Req 3.1, 3.2) y las sumas por grupo (Req 3.3). Lanza
`EquivalenciaExcedidaError` si algún desvío relativo supera `1e-6` (Req 3.6).

```powershell
# Self-test con snapshots sintéticos (sin DB): valida la lógica del comparador
python tools/comparar_snapshots_equivalencia.py

# Comparación real: dos CSV exportados del mismo corte (sin vs con consolidación)
python tools/comparar_snapshots_equivalencia.py snapshot_sin_consolidar.csv snapshot_con_consolidar.csv
```

Códigos de salida: `0` equivalente, `1` excede tolerancia, `2` error al cargar. Los CSV
deben contener las columnas del snapshot (`valor_mercado`, `valor_tir`, `nominal`,
`clasificacion_contable`, `clase_origen`, etc.); se obtienen exportando
`portafolio_clasificado_proy` por corte de cada corrida.

---

## 3. Corrida end-to-end por tramos 2026→2028 (Req 7)

Esta es la verificación del **tercer tramo**: correr el horizonte completo de 3 tramos en
**modo `por_tramos` con la consolidación activada** y confirmar que los tres cortes
persisten y que los dos primeros tramos quedan dentro de tolerancia.

> **Proceso largo (horas) contra la DB real. Ejecución MANUAL del usuario.** El modo
> `por_tramos` encadena las corridas del `Motor_Proyeccion_RF` por tramo y persiste el
> snapshot de cierre de cada uno en `portafolio_clasificado_proy`.

### 3.1 Lanzar la corrida de 3 tramos

Desde la UI del dashboard (`app_dash.py`):

1. Iniciar el dashboard (`python app_dash.py`).
2. Seleccionar **Modo de proyección = `por_tramos`**.
3. Fijar el horizonte **inicio 2026 → fin 2028** (3 tramos anuales: cortes
   **dic-2026, dic-2027, dic-2028**).
4. Ejecutar la proyección y esperar a que termine (el log del orquestador muestra, por
   tramo, el diagnóstico **`[TRAMOS-CONSOLIDA]`** con `n_antes->n_despues`, `grupos`,
   `colapsadas`, `negociables`, `intocables`).

Al cierre de cada tramo, antes de persistir el snapshot, el orquestador invoca
`consolidar_negociables_fungibles`; el diagnóstico `[TRAMOS-CONSOLIDA]` debe mostrar una
**reducción material** del conteo RF (objetivo de referencia: de ~40.000 a ~5.400 filas
al inicio del tramo 3, reducción ~7×; el valor exacto depende de la data — Req 7.2).

### 3.2 Confirmar que los tres cortes persisten

Verificar que `portafolio_clasificado_proy` tiene los tres cortes (incluido dic-2028):

```powershell
python -c "import sqlite3; c=sqlite3.connect('file:portafolio.db?mode=ro', uri=True); print(c.execute('SELECT DISTINCT fecha_corte_proyeccion FROM portafolio_clasificado_proy ORDER BY fecha_corte_proyeccion').fetchall())"
```

Debe listar los tres cortes de cierre (dic-2026, dic-2027, **dic-2028**) — Req 7.1.

### 3.3 Confirmar las hojas de Excel del corte dic-2028

Descargar el **Excel Detallado** desde el dashboard (botón "Descargar Excel Detallado",
callback `descargar_resultados_excel`, que usa `tools/exportar_excel_tramos.py`). El libro
debe incluir, **para cada corte, incluido dic-2028** (Req 7.3):

- `Portafolio_<corte>` — portafolio clasificado proyectado COMPLETO al cierre del tramo.
- `ResumenPyG_<corte>` — resumen de P&G y ORI por categoría × compañía × ramo ×
  clasificación.

Es decir, deben aparecer `Portafolio_2028-12-31` y `ResumenPyG_2028-12-31` (nombres de
hoja saneados y truncados a 31 caracteres por `exportar_excel_tramos._nombre_hoja`).

### 3.4 Confirmar que los dos primeros tramos quedan dentro de tolerancia (Req 7.4)

Comparar el resultado de los **dos primeros tramos** del horizonte de 3 tramos contra la
corrida de **2 tramos SIN consolidación** (2026→2027), por corte, usando el comparador de
snapshots de la §2:

```powershell
# Exportar a CSV los snapshots de dic-2026 y dic-2027 de ambas corridas (2 tramos sin
# consolidar vs 3 tramos con consolidar) y compararlos corte a corte:
python tools/comparar_snapshots_equivalencia.py dic2026_sin_consolidar.csv dic2026_con_consolidar.csv
python tools/comparar_snapshots_equivalencia.py dic2027_sin_consolidar.csv dic2027_con_consolidar.csv
```

Ambas comparaciones deben reportar `OK EQUIVALENTE` (ningún desvío > `1e-6`). Si alguna
excede la tolerancia, la consolidación habría alterado tramos previos de forma observable
y la implementación se considera incorrecta (Req 7.4 / Req 3.6).

---

## Resumen de comandos (copiar/pegar)

```powershell
# --- 1) Motor RF: baseline ANTES del cambio, check DESPUES (tol 1e-6) ---
python tools/equivalencia_motor_rf.py baseline          # con el codigo SIN consolidacion
python tools/equivalencia_motor_rf.py check             # con el codigo CON consolidacion
# verificacion final a 12 meses:
$env:AXIS_EQUIV_MESES = "12"; python tools/equivalencia_motor_rf.py baseline
$env:AXIS_EQUIV_MESES = "12"; python tools/equivalencia_motor_rf.py check

# --- 2) Snapshots por corte: CON vs SIN consolidacion ---
python tools/comparar_snapshots_equivalencia.py                                   # self-test
python tools/comparar_snapshots_equivalencia.py sin_consolidar.csv con_consolidar.csv

# --- 3) 3 tramos 2026->2028 (manual, por UI, modo por_tramos + consolidacion) ---
#   - lanzar la corrida desde el dashboard (python app_dash.py)
#   - confirmar los 3 cortes persistidos:
python -c "import sqlite3; c=sqlite3.connect('file:portafolio.db?mode=ro', uri=True); print(c.execute('SELECT DISTINCT fecha_corte_proyeccion FROM portafolio_clasificado_proy ORDER BY fecha_corte_proyeccion').fetchall())"
#   - descargar el Excel Detallado y confirmar hojas Portafolio_2028-12-31 / ResumenPyG_2028-12-31
#   - comparar los 2 primeros tramos contra la corrida de 2 tramos sin consolidar:
python tools/comparar_snapshots_equivalencia.py dic2026_sin.csv dic2026_con.csv
python tools/comparar_snapshots_equivalencia.py dic2027_sin.csv dic2027_con.csv
```

> Recordatorio: todas estas corridas son **manuales** y contra la **DB real**; los scripts
> solo leen la DB. No forman parte del flujo automatizado del agente.
