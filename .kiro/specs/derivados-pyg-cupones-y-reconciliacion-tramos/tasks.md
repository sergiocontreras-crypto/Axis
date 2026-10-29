# Plan de tareas — Derivados: P&G con cupones realizados y reconciliación de las tres vistas por tramos

Flujo bugfix. Las tareas de la Ola 1 son pruebas de exploración: DEBEN fallar sobre el código sin
arreglar (confirman el bug). El resto implementa el arreglo y verifica. Las olas se ejecutan en
orden; dentro de una ola las tareas marcadas como paralelas pueden ir juntas.

Convención de pruebas (Windows / Google Drive): ruta absoluta + `--rootdir "g:\Mi unidad\AXIS\AXIS"`
+ `-o addopts="" -p no:cacheprovider`; Hypothesis `@settings(deadline=None, max_examples=100)` y
`suppress_health_check=[HealthCheck.too_slow]` si se toca fixture de BD. Capturar salida de pytest
a archivo temporal y leerlo (confiar en la línea de resumen, no en el exit code).

---

## Ola 1 — Pruebas de exploración (deben fallar sobre el código actual)

- [x] 1. Property test del cupón de swaps en `calcular_pyg` (D1)
  - Crear `tests/test_pyg_cupones_swaps_calcular_pyg.py`.
  - Construir `resultados` sintéticos con `swaps` multi-corte que traigan `cupones_der_acum_cop` /
    `cupones_obl_acum_cop` crecientes (cupón neto realizado != 0) y `valoracion` por corte.
  - Propiedad: `p_y_g(Swaps, último corte)` debe == variación de VPN + cupón neto realizado
    acumulado respecto a la base. En el código ACTUAL falla (solo mide VPN).
  - Caso de control: con cupones acumulados constantes (cupón neto realizado = 0), el P&G debe
    coincidir con el actual.
  - _Defecto: D1_

- [x] 2. Extender el test de reconciliación con caso multi-posición (D2)
  - En `tests/test_property_reconciliacion_total_categoria.py`, añadir un test (unit + propiedad)
    que siembre N posiciones RV con `id_posicion` distintos pero MISMA especie y P&G distintos.
  - Verificar que `Σ detalle (por id_posicion) == ResumenPyG == TotalCategoria` para la categoría
    RV, y que no se colapsan posiciones. En el código ACTUAL falla (agrega por especie y prorratea).
  - No romper los casos existentes (especies únicas).
  - _Defecto: D2_

## Ola 2 — Arreglo D1 (cupón de swaps en `calcular_pyg`, transversal)

- [x] 3. Capturar baseline del P&G de swaps punta a punta
  - Antes de modificar `calcular_pyg`, ejecutar la valoración de derivados en un escenario con
    swaps y guardar el P&G de Swaps por corte en un archivo temporal (`_tmp_baseline_swaps_pyg.txt`).
  - Documentar que tras D1 la diferencia esperada = cupón neto realizado acumulado por corte.
  - _Defecto: D1 (preservación)_

- [x] 4. Implementar el cupón neto en `calcular_pyg`
  - En `valoracion_derivados.py::calcular_pyg`: al incluir swaps, conservar `cupones_der_acum_cop` /
    `cupones_obl_acum_cop` si existen; construir `cup_neto = der − obl` por fila; agrupar por
    `fecha_valoracion` sumando `valoracion` y `cup_neto`.
  - Para `tipo == 'Swaps'`: `p_y_g = (valoracion − valoracion_base) + (cup_neto_acum − cup_neto_base)`.
  - Forwards/opciones/activos sin cambio. Si faltan columnas de cupones ⇒ `cup_neto = 0`.
  - _Defecto: D1_

- [x] 5. Verificar D1
  - Correr `tests/test_pyg_cupones_swaps_calcular_pyg.py` (ahora debe pasar).
  - Confirmar invariancia de forwards/opciones/activos y que sin swaps la salida es idéntica.
  - Comparar contra el baseline de la Tarea 3: la diferencia es exactamente el cupón neto realizado.
  - _Defecto: D1_

## Ola 3 — Arreglo D2 (RV/Alt por `id_posicion`)

- [ ] 6. Reescribir el bloque RV/Alt de `_resumen_no_rf`
  - En `tools/export_resumen_tramos.py::_resumen_no_rf`:
    - `col_id = "id_posicion" if "id_posicion" in df.columns else "titulo"`.
    - Último corte por `id_posicion`.
    - Herencia de ramo/compañía/moneda del base por `id_posicion` (patrón derivados).
    - Eliminar el prorrateo por especie; una fila por posición (categoría × compañía × ramo).
    - Mantener `ori = 0.0`, `total_resultado == pyg` y el ruteo PEI por `id_posicion`.
    - Efecto moneda internacional sobre el P&G por posición (igual que hoy).
  - _Defecto: D2_

- [ ] 7. Verificar D2
  - Correr `tests/test_property_reconciliacion_total_categoria.py` completo (incluye el caso
    multi-posición de la Tarea 2): debe pasar.
  - Confirmar Property 4 (ORI=0 y total==pyg en no-RF) sigue verde.
  - _Defecto: D2_

## Ola 4 — Arreglo D3 (detalle completo de derivados por tramo)

- [x] 8. Capturar y persistir el detalle de derivados por tramo
  - En `app_dash.py::_correr_derivados_tramo`: además de `pyg` y `vm_cierre`, construir
    `detalle = {tipo: records}` por tipo con DataFrame no vacío, incluyendo todas las columnas de
    desglose (swaps: `vpn_der_calc`, `vpn_obl_calc`, `valoracion`, `cupones_der_acum_cop`,
    `cupones_obl_acum_cop`, nominales, monedas; fwd/opc: valoración + `liquidacion` si existe).
  - Devolver `detalle` en el dict de retorno (clave nueva).
  - En el ensamblado del payload (~L8277): `_reg["deriv_detalle"] = (deriv_ultimo or {}).get("detalle", {})`
    (falla-segura).
  - _Defecto: D3_

- [x] 9. Hojas de detalle de derivados en el Excel por tramos
  - En `tools/exportar_excel_tramos.py` añadir `_anexar_derivados_detalle_tramos(writer, db_path)`:
    lee RO `informe_pyg_por_tramo`, por corte toma `deriv_detalle`, y por tipo escribe
    `Swaps_Detalle_<corte>` / `Fwd_Detalle_<corte>` / `Opc_Detalle_<corte>` con todas las filas
    (posición × mes) y columnas.
  - Para swaps añadir `cupon_neto_acum_cop` y `pyg_corte` (coherente con D1).
  - Falla-segura si falta `deriv_detalle` en un corte. Invocar la función desde
    `anexar_hojas_detalle_tramos` y acumular hojas en el dict de salida.
  - _Defecto: D3_

- [x] 10. Test de integración de reconciliación del detalle de derivados
  - Crear `tests/test_detalle_derivados_tramo_reconciliacion.py`: sembrar un payload por tramo con
    `deriv_detalle` de swaps multi-posición + `deriv_pyg` coherente; verificar que la hoja
    `Swaps_Detalle_<corte>` existe, trae todas las posiciones/meses, y que la suma de la fila de
    cierre reconcilia con el total de Swaps de `ResumenPyG_<corte>` / `TotalCategoria_<corte>`.
  - Caso falla-segura: corte sin `deriv_detalle` ⇒ no crea hoja ni rompe las demás.
  - _Defecto: D3_

## Ola 5 — Verificación final

- [x] 11. Suite completa + regresión
  - Correr la suite de las pruebas tocadas/creadas (D1, D2, D3) + regresión de collar.
  - Confirmar que los ÚNICOS fallos son los 7 pre-existentes documentados en
    `docs/deuda-tecnica.md` (hist_pip/curvas Forward y allowlist smoke), sin nuevas regresiones.
  - Dejar nota de cierre con el resumen de resultados.
  - _Defectos: D1, D2, D3_
