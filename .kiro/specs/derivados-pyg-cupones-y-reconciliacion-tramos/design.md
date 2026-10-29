# Diseño — Derivados: P&G con cupones realizados y reconciliación de las tres vistas por tramos

## Visión general

El arreglo se organiza en tres frentes independientes que convergen en la reconciliación de las
tres vistas (detalle, ResumenPyG, TotalCategoria):

- **D1 (transversal):** inyectar el cupón neto realizado de swaps en el P&G de derivados que
  produce `calcular_pyg`, reutilizando el insumo que ya expone `valorar_swaps`
  (`cupones_der_acum_cop` / `cupones_obl_acum_cop`). Afecta todos los modos porque `calcular_pyg`
  es el único punto de cálculo del P&G de derivados.
- **D2 (por tramos):** cambiar la agregación de RV/Alt en `_resumen_no_rf` de `titulo`/especie a
  `id_posicion` (identidad desde Spec A), heredando ramo/compañía del base por id, sin prorrateo.
- **D3 (por tramos):** persistir el detalle de derivados por posición y mes en el payload del
  tramo, y añadir hojas `*_Detalle_<corte>` al Excel por tramos con el desglose completo
  (incluye cupones de swaps vía D1).

Principio rector: el P&G SIEMPRE incluye flujos/cupones realizados (no suman en `nominal` pero sí
en P&G). Los cambios son retrocompatibles (nuevos parámetros/columnas con defaults) y la lectura
de BD es solo lectura con consultas parametrizadas.

---

## D1 — Cupón neto realizado de swaps en `calcular_pyg`

### Estado actual

`calcular_pyg(resultados)` (`valoracion_derivados.py:2703`) concatena opciones/forwards/activos/
swaps, agrupa por `fecha_valoracion` × `tipo` y calcula `p_y_g = valoracion − valor_base` (solo
variación de MtM acumulada respecto al primer corte). El DataFrame de swaps ya trae por corte
`cupones_der_acum_cop` y `cupones_obl_acum_cop`.

### Diseño

Añadir, SOLO para `tipo == 'Swaps'`, el cupón neto acumulado al P&G, de forma que el P&G de
swaps sea `variación de VPN acumulada + cupón neto realizado acumulado` respecto al primer corte.

Definiciones por corte t (para un swap):
- `cupon_neto_acum(t) = cupones_der_acum_cop(t) − cupones_obl_acum_cop(t)`

Agregado por corte (suma sobre swaps):
- `cupon_neto_acum_total(t) = Σ_swaps cupon_neto_acum(t)`

P&G de swaps en el corte t (coherente con el patrón `valor − valor_base`):
- `p_y_g_swaps(t) = (valoracion_total(t) − valoracion_total(t0))
                   + (cupon_neto_acum_total(t) − cupon_neto_acum_total(t0))`

Como `cupon_neto_acum_total(t0)` es el cupón ya devengado hasta el primer corte (base), restarlo
mantiene la convención de P&G relativo a la base, igual que `valor_base`. La suma telescópica de
`Δcupon_neto_acum` entre cortes equivale al cupón neto realizado acumulado desde la base, que es
lo que pide el Req D1.

Implementación propuesta (sin romper la firma):
1. En `calcular_pyg`, al incluir swaps, conservar las columnas de cupones si existen:
   construir `cup_neto = cupones_der_acum_cop − cupones_obl_acum_cop` por fila ANTES del groupby.
2. Agrupar swaps por `fecha_valoracion` sumando `valoracion` y `cup_neto` → `cup_neto_acum_total`.
3. Para el tipo Swaps, `p_y_g = (valoracion − valoracion_base) + (cup_neto_acum − cup_neto_base)`.
4. Forwards/opciones/activos: sin cambio.

Robustez: si el DataFrame de swaps NO trae las columnas de cupones (compatibilidad hacia atrás),
`cup_neto = 0` ⇒ P&G idéntico al actual. Si no hay swaps, el flujo no se toca.

### Alternativa descartada

Reemplazar `calcular_pyg` por `calcular_pyg_swaps_contable` en los call sites: esa función exige
`senda_trm`/`senda_uvr`/`fecha_corte_dt` y produce un esquema NEG/DV distinto; cambiar 4 call
sites transversales es más invasivo y arriesgado. Se opta por un cambio quirúrgico y aditivo en
`calcular_pyg`, que ya es el único punto de verdad del P&G de derivados.

### Pruebas D1

- Propiedad: para cualquier senda de cupones acumulados monótona por pata, el P&G de swaps en el
  último corte = variación de VPN + cupón neto realizado acumulado; con cupones nulos ⇒ igual al
  actual. (property-based, Hypothesis).
- Unit: forwards/opciones/activos invariantes; sin swaps, salida idéntica.
- Regresión: baseline punta-a-punta del P&G de swaps capturado antes del cambio; la diferencia es
  exactamente el cupón neto realizado acumulado.

---

## D2 — Reconciliación RV/Alt por `id_posicion` en `_resumen_no_rf`

### Estado actual

`tools/export_resumen_tramos.py::_resumen_no_rf`, bloque RV/Alt (~L190-230):
- `col_id = "titulo" if "titulo" in df.columns else ("id_posicion" ...)` prioriza `titulo`.
- `groupby(col_id).idxmax()` colapsa posiciones de la misma especie.
- Prorratea el P&G de la especie entre posiciones del base por VM inicial.

### Diseño

Reescribir el bloque RV/Alt para que:
1. Clave de identidad: `col_id = "id_posicion" if "id_posicion" in df.columns else "titulo"`
   (invertir la preferencia; fallback a `titulo` solo si no hay `id_posicion`).
2. Último corte por `id_posicion` (no por especie): `groupby("id_posicion")["fecha_proyeccion"].idxmax()`.
3. Herencia de ramo/compañía/moneda del base POR `id_posicion` (mismo patrón que derivados con
   `_id_base_desde_snapshot`): `base = df_clasif.set_index("id_posicion")`; si el id del snapshot
   existe en el base, tomar `ramo`/`compania`/`moneda` de ahí.
4. Eliminar el prorrateo por especie: cada posición aporta su propio P&G a una única fila
   (categoría × compañía × ramo). El efecto moneda internacional se calcula sobre ese P&G por
   posición igual que hoy.
5. Mantener invariante no-RF: `ori = 0.0` y `total_resultado == pyg` en todas las filas.
6. Ruteo PEI: el flag `es_pei` se resuelve desde `df_clasif` por `id_posicion` (no por especie),
   conservando el Defecto 2 ya resuelto (PEI es categoría propia).

Compatibilidad hacia atrás: si el snapshot no trae `id_posicion` (corridas viejas), el fallback a
`titulo` reproduce el comportamiento previo (y el prorrateo deja de aplicar: con `titulo` como id,
el groupby produce una fila por título, que es el caso que el test actual ya cubre).

### Pruebas D2

- Extender `tests/test_property_reconciliacion_total_categoria.py` con un caso de especie
  NO-única: N posiciones (`id_posicion` distintos) con la MISMA especie y P&G distintos. Verificar
  que `Σ detalle (por id) == ResumenPyG == TotalCategoria` para la categoría, y que no se colapsan
  posiciones.
- Mantener los casos existentes verdes (especies únicas), ahora por la vía `id_posicion`.

---

## D3 — Detalle completo de derivados por tramo

### Estado actual

El payload del tramo (`informe_pyg_por_tramo`) captura `deriv_pyg` (P&G por tipo, último corte) y
`deriv_vm_cierre` (VM de cierre por posición), pero NO el detalle por posición y por mes de
forwards/opciones/swaps. `_correr_derivados_tramo` (`app_dash.py:7619-7656`) tiene acceso a los
DataFrames completos `res_deriv['swaps' | 'forwards' | 'opciones']` (todos los cortes), pero solo
deriva `pyg` y `vm_cierre`.

### Diseño

Dos partes: (a) capturar y persistir el detalle; (b) escribir las hojas.

#### (a) Captura y persistencia

- En `_correr_derivados_tramo`, además de `pyg` y `vm_cierre`, construir un `detalle` serializable:
  por cada tipo con DataFrame no vacío, seleccionar un subconjunto estable de columnas de desglose
  (todas las disponibles del motor) y volcarlas a `records`. Para swaps incluir `vpn_der_calc`,
  `vpn_obl_calc`, `valoracion`, `cupones_der_acum_cop`, `cupones_obl_acum_cop`, nominales y
  monedas; para forwards/opciones incluir las columnas de valoración y la columna `liquidacion`
  cuando exista. Nuevo retorno: `{'pyg', 'vm_cierre', 'plano_cierre', 'detalle': {tipo: records}}`
  (clave nueva, retrocompatible).
- En el ensamblado del payload (`app_dash.py` ~L8277), añadir
  `_reg["deriv_detalle"] = (deriv_ultimo or {}).get("detalle", {})` (clave nueva, falla-segura).

#### (b) Hojas de detalle

- En `tools/exportar_excel_tramos.py` añadir `_anexar_derivados_detalle_tramos(writer, db_path)`,
  análogo a `_anexar_rf_detalle_titulo_tramos`:
  - Lee RO `informe_pyg_por_tramo`, recorre cada corte, toma `deriv_detalle`.
  - Por tipo (`swaps`/`forwards`/`opciones`) escribe una hoja
    `_nombre_hoja("Swaps_Detalle_", corte)` / `Fwd_Detalle_` / `Opc_Detalle_` con TODAS las filas
    (posición × mes) y todas las columnas del desglose.
  - Para swaps, añadir una columna derivada `cupon_neto_acum_cop = cupones_der_acum_cop −
    cupones_obl_acum_cop` y una columna `pyg_corte` consistente con D1 (variación de valoracion +
    variación de cupón neto respecto al primer corte), de modo que la fila de cierre del tramo por
    posición reconcilie con el total de Swaps del `ResumenPyG_<corte>`.
  - Falla-segura: si no hay `deriv_detalle` en un corte, se omite ese corte sin romper.
- `anexar_hojas_detalle_tramos` invoca la nueva función y acumula sus hojas en el dict de salida.

### Reconciliación (la propiedad central)

Para cada corte y cada categoría de derivado:
`Σ pyg_corte (fila de cierre del tramo por posición en Swaps_Detalle_<corte>)`
`== pyg de Swaps en ResumenPyG_<corte> == pyg de Swaps en TotalCategoria_<corte>`.

El P&G del tramo de la categoría Swaps en el resumen viene de `deriv_pyg` (último corte de
`calcular_pyg`), que tras D1 YA incluye el cupón neto. El detalle usa la misma definición de
`pyg_corte`, por lo que ambas vistas comparten la fuente y reconcilian por construcción.

### Pruebas D3

- Integración: sembrar un payload por tramo con `deriv_detalle` de swaps multi-posición y verificar
  que la hoja `Swaps_Detalle_<corte>` existe, trae todas las posiciones/meses, y que la suma de la
  fila de cierre reconcilia con el total de Swaps del ResumenPyG/TotalCategoria.
- Falla-segura: corte sin `deriv_detalle` ⇒ no se crea hoja, no rompe las demás.

---

## Orden de olas (ejecución)

- **Ola 1 (exploración/pruebas que fallan):** property test D1 (cupón en `calcular_pyg`) + extensión
  del test de reconciliación D2 (multi-posición). Confirman los bugs antes del arreglo.
- **Ola 2 (D1):** modificar `calcular_pyg`; capturar baseline punta-a-punta; verificar property +
  invariancia de fwd/opc.
- **Ola 3 (D2):** reescribir bloque RV/Alt de `_resumen_no_rf`; verificar Property 4 extendida.
- **Ola 4 (D3):** captura/persistencia del detalle en `_correr_derivados_tramo` + payload; hojas en
  `exportar_excel_tramos`; test de integración de reconciliación del detalle de derivados.
- **Ola 5 (verificación final):** suite completa + regresión de collar + confirmar solo fallan los
  7 pre-existentes documentados.

## Trazabilidad diseño → requisitos

| Componente | Defecto / Req |
|---|---|
| `calcular_pyg` + cupón neto | D1 |
| `_resumen_no_rf` por `id_posicion` | D2 |
| captura `deriv_detalle` + hojas `*_Detalle_<corte>` | D3 |
| property cupón, baseline punta-a-punta | D1 (preservación) |
| extensión Property 4 multi-posición | D2 |
| integración reconciliación detalle derivados | D3 |

## Riesgos y mitigaciones

- **Cambio de P&G punta-a-punta de swaps:** esperado y correcto; se documenta y se captura baseline
  para cuantificar que la diferencia == cupón neto realizado.
- **Columnas de cupones ausentes en corridas viejas:** defaults a 0 ⇒ sin cambio.
- **Volumen de hojas de detalle:** una hoja por tipo por corte; nombres saneados y truncados a 31
  chars con `_nombre_hoja` (patrón ya existente).
