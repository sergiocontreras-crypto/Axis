# Verificación — optimización motor de proyección AXIS (iteración 1)

Estado: **BLOQUEADO en ORDER C** (eliminación de doble ejecución RV/Alt en Sección 7).
Pendiente decisión del usuario/revisor. ORDER A y ORDER B aplicados y verificados.

## ORDER A — motor RF `range(500)`→`range(60)` (scripts/motor_proyeccion.py)

- Cambio: bloque IPC-VC/DV dentro de `proyectar_portafolio`, Paso 1 de bisección.
  `for _ in range(500):` → `for _ in range(60):` (línea ~1446). No se tocó nada más.
- Iteraciones finales usadas: **60**. Justificación: bisección sobre [-0.05, 3.0]
  (ancho 3.05); tras 60 iteraciones el ancho es 3.05/2^60 ≈ 2.6e-18 << tol 1e-6, y por
  debajo de la resolución del float64 → float idéntico al de 500.
- Verificación arnés RF (`tools\equivalencia_motor_rf.py check`), salida literal de
  `_eqcheck.txt`:

  ```
    PIP INT: 40 titulos internacionales enriquecidos con rendimiento
  OK EQUIVALENTE: el resultado es identico al baseline (tol=1e-6).
  EXIT=0
  ```

  (el acento de "idéntico" aparece como `id├®ntico` por el encoding del eco de PowerShell;
  el texto real impreso por el arnés es
  `OK EQUIVALENTE: el resultado es idéntico al baseline (tol=1e-6).`)

## ORDER B — #3 + #4b (app_dash.py, re-valoración a fecha_end, bloque IPC-VC/DV)

- #3: antes del bucle de bisección se precalcula una sola vez
  `_flujos2_ini_t = [(_m2, _dnl(fecha_ultimo_mes, _f2)/365.0) for _f2,_m2 in _flujos2]`;
  la bisección evalúa `sum(_m2/(1+_ym2)**_t2 for _m2,_t2 in _flujos2_ini_t)`. El VP al
  fecha_end precalcula `_flujos_end_t` análogamente. Mismas fechas, montos, fórmula y
  orden de suma → numéricamente idéntico (sólo se extrae `_dnl/365` fuera del bucle).
- #4b: `for _ in range(500):` → `for _ in range(60):` (línea ~6583). Misma justificación
  matemática que ORDER A.
- Iteraciones finales usadas: **60**.
- Verificación: este bloque NO lo cubre el arnés RF. Falta la captura de VM a nivel
  app_dash (df_proy antes/después con fecha_end a mitad de mes). Pendiente de completar
  tras resolver ORDER C.

## ORDER C / ORDER D (RV, Alt) — BLOQUEADO: la reutilización cambia números

Diagnóstico ejecutado (script temporal `tools\_diag_equiv_s7.py`, ya marcado para borrar)
comparando, por `id_posicion` al último mes, el VM que quedaría en `df_proy` si se
**reutiliza** `df_rv_res` (Sección 4) / `df_alt` (Sección 5) aplicando la post-transformación
de la Sección 7 (×trm_final para `rv_internacional`), contra el VM que produce **hoy** la
Sección 7 (que re-ejecuta `_prv`/`_pa` SIN `senda_trm` y luego ×trm_final):

- **Caso A — `store_sendas` = None (sin senda TRM en la UI; caso headless/común):**
  - RV: max |Δrel| = 0.0
  - ALT: max |Δrel| = 0.000e+00
  - → reutilizar es **numéricamente idéntico**. Causa: con `senda_trm=None`,
    `df_rv_res`/`df_alt` de la Sección 4/5 son las MISMAS DataFrames que `_prv`/`_pa`
    de la Sección 7 (mismos argumentos efectivos), y ×trm_final reproduce el VM exacto.

- **Caso B — `store_sendas` con senda TRM NO plana (el usuario fija una senda TRM):**
  - RV: max |Δrel| = **5.797e-02 (5.8%)**  peor id 873813: reuse=664.118e9 vs hoy=625.618e9
  - ALT: max |Δrel| = **5.797e-02 (5.8%)** peor id 776852: reuse=8.186e9 vs hoy=7.711e9
  - → reutilizar **CAMBIA los números** de las posiciones internacionales (MXWD / fondos
    USD). Causa raíz: la Sección 4/5 corren CON `senda_trm` (convierten el VM internacional
    a COP por corte, TRM del mes a mes), mientras la Sección 7 corre SIN `senda_trm`
    (deja el VM internacional en COP@T0) y aplica un único ×trm_final al final. Son dos
    caminos numéricos distintos salvo que la senda TRM sea plana == trm_final.

Conclusión: ORDER C/D (reutilización incondicional) es seguro SOLO cuando no hay senda TRM
(Caso A). Con senda TRM presente (Caso B, ruta real de uso por la UI) altera el VM
internacional hasta ~5.8% → viola "sin cambiar ni un número". Se escaló al usuario con
`send_message` (warning) antes de tocar la Sección 7. Pendiente de su decisión.
