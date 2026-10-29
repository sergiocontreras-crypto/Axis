# Bugfix — Derivados: P&G con cupones realizados y reconciliación de las tres vistas por tramos

## Contexto

El informe por tramos presenta el P&G (pérdidas y ganancias) de cada clase de activo en
TRES vistas que deben ser consistentes entre sí:

1. **Detalle** — la vista posición por posición (p. ej. `RF_Detalle_Titulo_<corte>`, y las
   hojas de detalle de derivados que esta spec añade).
2. **ResumenPyG** — la hoja `ResumenPyG_<corte>`: P&G y ORI por categoría × compañía × ramo ×
   clasificación.
3. **TotalCategoria** — la hoja `TotalCategoria_<corte>`: un `groupby` por categoría del
   ResumenPyG.

El usuario reporta que para derivados (swaps, forwards, opciones) y para renta variable /
alternativos (RV/Alt) **estas tres vistas NO cuadran**: la suma del detalle no coincide con el
ResumenPyG ni con el TotalCategoria. Además, los **cupones realizados de los swaps no se están
contabilizando en el P&G** en ningún modo (ni punta a punta ni por tramos).

Esta spec corrige tres defectos relacionados. Dos son transversales (afectan todos los modos
de proyección) y uno es específico de la agregación por tramos de RV/Alt.

## Alcance

- **Transversal (todos los modos):** inclusión de cupones realizados de swaps en el P&G de
  derivados.
- **Por tramos:** reconciliación de las tres vistas para RV/Alt (agregación por `id_posicion`)
  y creación de hojas de detalle completo de derivados por tramo (fwd/opc/swap) mes a mes con
  todo el desglose, incluyendo cupones/liquidaciones.

## Fuera de alcance

- No se modifican los motores de valoración de RF, forwards u opciones (su P&G ya refleja
  liquidaciones al vencimiento vía la columna `liquidacion`).
- No se cambia la clasificación contable NEG/DV/VC (aplica solo a RF).
- No se altera `interpolar_trm_tramo` ni la lógica de encadenamiento de nominales/curvas
  (Spec E).

---

## Defecto 1 — Cupones realizados de swaps ausentes del P&G (transversal)

### Condición de bug

```
isBugCondition(swap, ventana) :=
    cupon_neto_realizado(swap, ventana) != 0
    AND pyg_derivados_reportado(swap, ventana) == variacion_vpn(swap, ventana)
    AND pyg_derivados_reportado NO incluye cupon_neto_realizado
```

En palabras: existe un swap que pagó/recibió cupón dentro de una ventana de valoración, y el
P&G reportado para derivados solo refleja la variación de VPN/MtM, perdiendo el cupón neto
efectivamente realizado (caja que salió del precio sucio).

### Comportamiento actual

- `calcular_pyg` (`valoracion_derivados.py:2703`) agrega por `fecha_valoracion` × `tipo` y
  calcula `p_y_g = valoracion − valor_base`. Solo mide variación de VPN/MtM. **No suma el
  cupón neto realizado.** Es la función usada en TODAS partes:
  `app_dash.py:4694, 6775, 7629`; `tools/multiescenarios.py:1006`.
- `calcular_pyg_swaps_contable` (`valoracion_derivados.py:2738`) SÍ incluye el cupón neto
  realizado (`cupon_neto_realizado = cupon_der_realizado − cupon_obl_realizado`) tanto en la
  rama Negociable como en Disp. Venta, pero está **huérfana: nadie la invoca.**
- `valorar_swaps` ya expone por corte `cupones_der_acum_cop` y `cupones_obl_acum_cop`
  (`valoracion_derivados.py:1054-1055`), insumo suficiente para derivar el cupón realizado por
  ventana (variación del acumulado entre cortes).

### Comportamiento esperado (EARS)

- WHEN se calcula el P&G de derivados en cualquier modo (punta a punta o por tramos), the
  system SHALL incluir, en el P&G de la categoría Swaps, el cupón neto realizado en la ventana
  (`Σ (Δcupones_der_acum_cop − Δcupones_obl_acum_cop)` por corte) además de la variación de
  VPN/MtM.
- WHERE un swap no registra cupones en una ventana, the system SHALL reportar el mismo P&G que
  hoy (cupón neto realizado = 0 ⇒ sin cambio).
- WHEN no existen swaps en los resultados, the system SHALL comportarse exactamente igual que
  hoy (sin regresión en forwards/opciones/activos).

---

## Defecto 2 — Reconciliación RV/Alt por `id_posicion` (por tramos)

### Condición de bug

```
isBugCondition(corte) :=
    EXISTE una especie con N posiciones (N > 1) en el base
    AND _resumen_no_rf agrega el P&G de RV/Alt por `titulo`/especie (no por `id_posicion`)
    AND la suma del detalle por posición != ResumenPyG de esa categoría
```

### Comportamiento actual

En `tools/export_resumen_tramos.py::_resumen_no_rf`:

- Línea ~198: `col_id = "titulo" if "titulo" in df.columns else ("id_posicion" ...)` — toma
  `titulo` primero, suposición OBSOLETA previa a Spec A (RV ahora tiene identidad por
  `id_posicion`).
- Línea ~202: `idx = df.groupby(col_id)["fecha_proyeccion"].idxmax()` — colapsa N posiciones de
  la misma especie en una sola fila (pierde posiciones).
- Línea ~210 en adelante: re-prorratea el P&G por especie entre las posiciones del base según
  VM inicial. Este reparto por especie no reconcilia con el detalle real por `id_posicion`.

Los derivados ya heredan ramo/compañía del base por id (`_id_base_desde_snapshot`), ese patrón
es el correcto a replicar.

El test `tests/test_property_reconciliacion_total_categoria.py` enmascara el defecto porque
siembra especies ÚNICAS (una posición por especie), caso en que el prorrateo coincide
trivialmente con la agregación por id.

### Comportamiento esperado (EARS)

- WHEN se agrega el P&G por tramos de RV/Alt, the system SHALL agrupar por `id_posicion` (clave
  de identidad desde Spec A), heredando ramo/compañía/moneda del base por `id_posicion`, sin
  prorratear por especie.
- WHERE el snapshot de RV/Alt trae `id_posicion`, the system SHALL usarlo como clave; WHERE no
  lo trae (compatibilidad hacia atrás), the system SHALL caer a `titulo` como hoy.
- WHEN hay N posiciones de la misma especie, the system SHALL conservar las N en el resumen
  (una por `id_posicion`), de modo que `Σ detalle == ResumenPyG == TotalCategoria` para esa
  categoría.
- the system SHALL mantener el invariante no-RF existente: `ORI == 0` y
  `total_resultado == pyg` para toda fila no-RF (Property 4).

---

## Defecto 3 — Falta detalle completo de derivados por tramo (por tramos)

### Condición de bug

```
isBugCondition(corte) :=
    el Excel por tramos NO contiene una hoja de detalle de derivados por posición y por mes
    con el desglose completo (nominal, VPN/MtM, cupones/liquidaciones, P&G)
    AND por tanto el detalle de derivados no es comparable/reconciliable con ResumenPyG
```

### Comportamiento actual

`tools/exportar_excel_tramos.py::anexar_hojas_detalle_tramos` genera `Portafolio_<corte>`,
`ResumenPyG_<corte>`, `TotalCategoria_<corte>`, series mensuales y `RF_Detalle_Titulo_<corte>`.
**No existe** una hoja de detalle de derivados (forwards/opciones/swaps) posición-por-posición
y mes-a-mes con el desglose completo. El P&G de derivados en el ResumenPyG se reporta solo a
nivel de TIPO (total), sin detalle comparable.

### Comportamiento esperado (EARS)

- WHEN se genera el Excel por tramos, the system SHALL añadir, por corte, hojas de detalle de
  derivados por posición y por mes con el desglose completo: identificador de posición, tipo,
  fecha de valoración, nominal, VPN/MtM, cupones/liquidaciones realizadas y P&G del período.
- the system SHALL incluir en ese detalle de swaps el cupón neto realizado (consistente con el
  Defecto 1), de modo que la fila de cierre del tramo por posición sume al P&G del tramo
  reportado en `ResumenPyG_<corte>` y `TotalCategoria_<corte>`.
- the system SHALL leer en modo solo lectura desde la BD (sin recalcular motores), con consultas
  parametrizadas, igual que las demás hojas de detalle.

---

## Preservación (invariantes que NO deben romperse)

- **RF ya reconcilia** por construcción (`_resumen_rf` consume el mismo `rf_detalle` por título
  que `RF_Detalle_Titulo_<corte>`). No debe cambiar.
- **Punta a punta del P&G de swaps CAMBIARÁ** (ahora incluirá cupones). Capturar baseline antes
  del cambio y documentar la diferencia esperada = cupón neto realizado por ventana.
- Forwards y opciones ya liquidan al vencimiento (`_valorar_fwds_corte` ~L1753-1771,
  `_valorar_opciones_corte` ~L1824-1850, columna `liquidacion`); su P&G NO debe cambiar — se
  confirma con prueba.
- Hojas existentes (`Portafolio_`, `ResumenPyG_`, `TotalCategoria_`, series,
  `RF_Detalle_Titulo_`) se mantienen intactas; las nuevas hojas se AÑADEN.
- Firmas de funciones compatibles hacia atrás: nuevos parámetros/columnas con defaults.
- Property 4 (`tests/test_property_reconciliacion_total_categoria.py`) sigue verde y se EXTIENDE
  con un caso de especie no-única / multi-posición.

## Criterios de aceptación (resumen verificable)

1. El P&G de derivados (Swaps) incluye el cupón neto realizado en todos los modos; cuando no hay
   cupones el valor es idéntico al actual.
2. En por tramos con N posiciones de la misma especie, `Σ detalle == ResumenPyG == TotalCategoria`
   para RV/Alt.
3. El Excel por tramos contiene hojas de detalle de derivados por posición y por mes con el
   desglose completo (incluye cupones de swaps), y la fila de cierre del tramo reconcilia con el
   ResumenPyG/TotalCategoria de la categoría del derivado.
4. Property 4 verde (extendida); regresión de collar verde; sin regresión en forwards/opciones.
