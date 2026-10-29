# Documento de Diseño

## Overview

Este diseño corrige cuatro defectos de consistencia del informe Excel en modo **por tramos**
(`modo == 'por_tramos'`) del proyecto AXIS, preservando intacto el modo **punta a punta** y las
internas del motor de valoración (salvo la propagación mínima de `id_cobertura`).

Los cambios son quirúrgicos y se concentran en cuatro puntos del pipeline ya existente:

| Defecto | Requisito | Archivo | Función | Cambio |
|---------|-----------|---------|---------|--------|
| 1 | Req 1 | `scripts/motor_proyeccion.py` | `reinvertir_cupon` (~L705-731) y reinversión por vencimiento (~L1067-1095) | Propagar `id_cobertura` del título origen a los dos `Titulo(...)` reinvertidos |
| 2 | Req 2 | `tools/export_resumen_tramos.py` | `_categoria`, `_mapa_clasificacion`, `_resumen_no_rf` | Rama "PEI" por `asset_class_definitivo='PEI'`; excluir PEI de "RV Local" |
| 3 | Req 3 | `app_dash.py` | `descargar_resultados_excel` (~L8919-9015) | Puertear las cuatro `to_excel` del store según `data['parametros']['modo']` |
| 4 | Req 4 | `tools/export_resumen_tramos.py` | `_resumen_rf`, `_resumen_no_rf` | RF reconcilia por construcción desde `rf_detalle`; no-RF con ORI=0 explícito |

No se crean filas de detalle nuevas, no se altera la regla contable NEG/DV/VC, no se introducen
dependencias nuevas y todas las consultas a BD siguen siendo parametrizadas y de solo lectura.

## Architecture

### Nota de arquitectura: pipeline del Excel por tramos

El informe por tramos se construye por **dos vías complementarias** que escriben sobre el mismo
libro de Excel:

```
                         ┌──────────────────────────────────────────────┐
   Orquestador por       │ scripts/proyeccion_tramos.py (motor_proyeccion)│
   tramos (N cortes) ───▶│  por cada tramo: valora, reinvierte cupones    │
                         │  y vencimientos (Defecto 1 vive aquí)          │
                         └───────────────────┬──────────────────────────┘
                                             │ persiste payload por corte
                                             ▼
                         ┌──────────────────────────────────────────────┐
                         │ BD SQLite: informe_pyg_por_tramo               │
                         │  (fecha_corte, payload_json con rf_detalle,    │
                         │   rv, alt, deriv_pyg, ...)  +                  │
                         │  portafolio_clasificado (asset_class_definitivo,│
                         │   id_cobertura, ramo, compania, moneda)        │
                         └───────────────────┬──────────────────────────┘
                                             │ lectura SOLO LECTURA (mode=ro)
                                             ▼
          ┌──────────────────────────────────────────────────────────────┐
          │ VÍA A (reconstruida desde BD — correcta para TODOS los tramos) │
          │ tools/exportar_excel_tramos.py                                 │
          │   · Portafolio_<corte>                                         │
          │   · ResumenPyG_<corte>  ← generar_resumen_por_tramo            │
          │       = _resumen_rf (desde rf_detalle)  +  _resumen_no_rf      │
          │   · TotalCategoria_<corte> = groupby(ResumenPyG_<corte>)       │
          │   · RF_Detalle_Titulo_<corte>                                  │
          │   · *_Tramos: Derivados_PyG_Tramos / RV_Mensual_Tramos /       │
          │               Alt_Mensual_Tramos (_anexar_series_mensuales_...) │
          │   (Defecto 2 y Defecto 4 viven aquí vía export_resumen_tramos)  │
          └──────────────────────────────────────────────────────────────┘
          ┌──────────────────────────────────────────────────────────────┐
          │ VÍA B (escrita desde el store de Dash)                         │
          │ app_dash.py::descargar_resultados_excel                        │
          │   · Hojas que SÍ aplican en por tramos (Resumen_AssetClass,    │
          │     Detalle_Ramo, Portafolio_Proyectado, Efecto_Moneda, ...)   │
          │   · Hojas del ÚLTIMO tramo del store: Derivados, RV_Mensual,    │
          │     Alt_Mensual, Creditos  ← INCORRECTAS en por tramos          │
          │   (Defecto 3 vive aquí: puertear por modo)                     │
          └──────────────────────────────────────────────────────────────┘
```

**Problema de fondo:** en modo por tramos, el *store* del callback de Dash solo contiene el
estado del **último** tramo. La Vía B escribe hojas (`Derivados`, `RV_Mensual`, `Alt_Mensual`,
`Creditos`) a partir del store, de modo que esas hojas muestran cifras de un solo tramo mientras
que las series `*_Tramos` de la Vía A ya cubren todos los tramos. El Defecto 3 elimina esa
duplicidad engañosa.

### Lenguaje y convenciones

Código en **Python 3.12+** (stack aprobado para backend). Se reutilizan `pandas`, `sqlite3` y
`hypothesis` ya presentes en el proyecto; **no** se agregan dependencias. Se respeta el patrón
existente del repositorio (imports diferidos en `tools/`, consultas `mode=ro`, agregación por
`groupby`).

---

## Components and Interfaces

Cada defecto corresponde a un componente del pipeline con una interfaz (función) concreta. Las
subsecciones siguientes describen, por componente, su causa raíz, el cambio exacto (archivo,
función, boceto de código) y cómo satisface el requisito.

### Defecto 1 — Herencia de `id_cobertura` en cupones reinvertidos

### Causa raíz

El `dataclass Titulo` ya declara el campo `id_cobertura: str = ""` (`scripts/motor_proyeccion.py`
L92). Al reinvertir, tanto `reinvertir_cupon` como la reinversión por vencimiento construyen el
nuevo `Titulo(...)` **sin** pasar `id_cobertura`, por lo que toma el default `""`. Más abajo,
`tools/detalle_pyg_tramo.py::construir_rf_detalle` deriva el flag:

```python
"es_subyacente": bool(str(getattr(t, "id_cobertura", "") or "").strip()),
```

Como el cupón reinvertido llega con `id_cobertura=""`, su `es_subyacente` queda en `False` y su
P&G deja de atribuirse a "Subyacentes de Swaps".

### Cambio exacto

Agregar una sola línea en cada uno de los dos constructores de `Titulo` para heredar el
`id_cobertura` del título origen.

**1a. `reinvertir_cupon` (~L705-731).** El origen es la variable `original`:

```python
    nuevo = Titulo(
        id_posicion=f"{original.id_posicion}_R{cupon.fecha_pago.strftime('%Y%m%d')}",
        especie=config.especie_destino,
        # ... (resto sin cambios) ...
        vm_compra=cupon.monto_total,
        spread_mercado=original.spread_mercado,
        id_cobertura=original.id_cobertura,   # ← Defecto 1: hereda condición de subyacente
    )
```

**1b. Reinversión por vencimiento (~L1067-1095).** El origen es la variable `titulo`:

```python
                nuevo_venc = Titulo(
                    id_posicion=f"{titulo.id_posicion}_V{titulo.fecha_vencimiento.strftime('%Y%m%d')}",
                    especie=_especie_reinv,
                    # ... (resto sin cambios) ...
                    vm_compra=monto_vencimiento,
                    spread_mercado=_spread_reinv_venc,
                    id_cobertura=titulo.id_cobertura,   # ← Defecto 1: hereda condición de subyacente
                )
```

### Cómo satisface el requisito

- **AC 1.1 / 1.2:** ambos constructores reciben el `id_cobertura` del origen (`original` /
  `titulo`).
- **AC 1.3 / 1.4:** como `es_subyacente` se deriva únicamente de `bool(id_cobertura.strip())`, el
  cupón reinvertido queda `es_subyacente=True` si y solo si el padre tenía `id_cobertura` no
  vacío. Un padre sin cobertura propaga `""` (default) y no introduce marca falsa.
- **AC 1.5:** no se toca el mecanismo `_cupones_cobrados`; el cupón sigue plegado en el padre y
  no se añaden filas a `rf_detalle` (solo cambia el valor de un campo ya existente).

Satisface además la preservación del Req 5.2: **ningún otro campo** del `Titulo` reinvertido
cambia.

---

### Defecto 2 — Categoría propia PEI separada de RV Local

### Causa raíz

`tools/export_resumen_tramos.py::_categoria` solo discrimina renta variable por moneda:

```python
    if co == "RENTA_VARIABLE":
        return "RV Internacional" if es_intl else "RV Local"
```

No hay rama para PEI, así que todo PEI local cae en "RV Local". El PEI se identifica, igual que
en `tools/multiescenarios.py::_especies_pei`, por `asset_class_definitivo = 'PEI'` en
`portafolio_clasificado`. `_mapa_clasificacion` ya renombra `asset_class_definitivo` →
`asset_class`, de modo que el `asset_class` que llega a `_categoria` **ya es** el definitivo.

### Cambio exacto

**2a. Nuevo parámetro `es_pei` en `_categoria`** (identificación explícita, sin hardcode de
nombres de especie):

```python
def _categoria(clase_origen: str, moneda: str, asset_class: str,
               es_subyacente: bool, es_pei: bool = False) -> str:
    co = (clase_origen or "").strip().upper()
    ac = (asset_class or "").strip()
    mon = (moneda or "").strip().upper()
    es_intl = mon not in ("", "COP", "UVR")

    if co == "SWAP":
        return "Swaps"
    if co == "FORWARD":
        return "Forwards"
    if co == "OPCION":
        return "Opciones"
    if co == "RENTA_VARIABLE":
        # Defecto 2: PEI es categoría propia; RV Local queda solo con COLCAP (no PEI).
        if es_pei or ac.upper() == "PEI":
            return "PEI"
        return "RV Internacional" if es_intl else "RV Local"
    if co == "ALTERNATIVO":
        return "Alt Internacional" if es_intl else "Alt Local"
    if es_subyacente:
        return "Subyacentes de Swaps"
    return "RF Internacional" if es_intl else "RF Local"
```

La identificación de PEI se deriva del dato (`asset_class` == 'PEI', que proviene de
`asset_class_definitivo`), nunca de un literal de especie embebido (cumple Req 5.7).

**2b. `_mapa_clasificacion`:** ya selecciona `asset_class_definitivo`→`asset_class`. Se añade una
columna booleana `es_pei` derivada de ese mismo campo, para disponer del flag en el momento de
clasificar RV:

```python
    if "asset_class" not in df.columns:
        df["asset_class"] = ""
    df["es_pei"] = df["asset_class"].astype(str).str.strip().str.upper().eq("PEI")
```

**2c. Bucle RV de `_resumen_no_rf`:** la categoría de cada posición RV se calcula hoy "a mano"
(`cat = "RV Internacional" if es_intl else "RV Local"`). Se enruta por `_categoria` usando el
flag PEI resuelto desde `df_clasif` por especie (misma fuente que el resto del mapa):

```python
            es_pei_especie = False
            if len(match):
                es_pei_especie = bool(match["es_pei"].any()) if "es_pei" in match.columns else False
            if es_rv:
                cat = _categoria("RENTA_VARIABLE", moneda, "PEI" if es_pei_especie else "",
                                 es_subyacente=False, es_pei=es_pei_especie)
            else:
                cat = "Alt Internacional" if es_intl else "Alt Local"
```

Así una especie PEI nunca se agrega bajo "RV Local": su P&G se acumula en la categoría "PEI".

### Cómo satisface el requisito

- **AC 2.1 / 2.2:** `_categoria` devuelve "PEI" cuando `es_pei`/`asset_class=='PEI'`, y "RV Local"
  para RV local no-PEI.
- **AC 2.3:** al enrutar por `_categoria`, ninguna posición PEI puede caer en "RV Local" (RV Local
  = solo COLCAP).
- **AC 2.4:** PEI se identifica por `asset_class_definitivo='PEI'`, el mismo criterio que
  `_especies_pei`.
- **AC 2.5:** como la categoría se produce dentro de `generar_resumen_por_tramo` para cada corte,
  "PEI" aparece en `ResumenPyG_<corte>` y, por groupby, en `TotalCategoria_<corte>` de todos los
  tramos que tengan posiciones PEI.

---

### Defecto 3 — Omisión de hojas incorrectas del store en modo por tramos

### Causa raíz

En `app_dash.py::descargar_resultados_excel`, cuatro hojas se escriben directamente desde el
store, condicionadas solo a la presencia de datos:

```python
        if data.get('derivados'):
            pd.DataFrame(data['derivados']).to_excel(writer, sheet_name='Derivados', index=False)
        ...
        if data.get('rv'):
            pd.DataFrame(data['rv']).to_excel(writer, sheet_name='RV_Mensual', index=False)
        ...
        if data.get('alternativos'):
            pd.DataFrame(data['alternativos']).to_excel(writer, sheet_name='Alt_Mensual', index=False)
        if data.get('creditos'):
            pd.DataFrame(data['creditos']).to_excel(writer, sheet_name='Creditos', index=False)
```

En por tramos el store solo trae el último tramo, por lo que esas hojas muestran cifras de un solo
corte. Las cifras correctas de todos los tramos ya existen como `*_Tramos` (Vía A). El modo está
disponible en `data['parametros']['modo']` (fijado a `'por_tramos'` en L8296).

### Cambio exacto

Leer el modo una sola vez al inicio de la escritura y puertear las cuatro `to_excel` del store:

```python
    # Defecto 3: en por_tramos el store solo trae el ÚLTIMO tramo; esas hojas serían
    # engañosas. Las series correctas de todos los tramos ya existen como *_Tramos (Vía A).
    _modo = str((data.get('parametros') or {}).get('modo') or 'punta_a_punta')
    _es_por_tramos = (_modo == 'por_tramos')
```

```python
        if not _es_por_tramos and data.get('derivados'):
            pd.DataFrame(data['derivados']).to_excel(writer, sheet_name='Derivados', index=False)
        ...
        if not _es_por_tramos and data.get('rv'):
            pd.DataFrame(data['rv']).to_excel(writer, sheet_name='RV_Mensual', index=False)
        ...
        if not _es_por_tramos and data.get('alternativos'):
            pd.DataFrame(data['alternativos']).to_excel(writer, sheet_name='Alt_Mensual', index=False)
        if not _es_por_tramos and data.get('creditos'):
            pd.DataFrame(data['creditos']).to_excel(writer, sheet_name='Creditos', index=False)
```

Las demás hojas del store (`Simultaneas`, `RV_Detalle_Titulo`, `Alt_Detalle_Titulo`,
`Portafolio_Proyectado`, `Efecto_Moneda`, `Resumen_AssetClass`, `Detalle_Ramo`) **no** se tocan,
y las series `*_Tramos` escritas por la Vía A permanecen intactas. No se crea `Creditos_Tramos`.

### Cómo satisface el requisito

- **AC 3.1:** el modo se lee de `data['parametros']['modo']`.
- **AC 3.2 / 3.4:** en `por_tramos` las cuatro hojas del store se omiten; Créditos no se escribe ni
  se construye `Creditos_Tramos`.
- **AC 3.3:** las series `Derivados_PyG_Tramos`, `RV_Mensual_Tramos`, `Alt_Mensual_Tramos` (Vía A)
  no se modifican.
- **AC 3.5 / 5.1:** en `punta_a_punta` el guard `not _es_por_tramos` es verdadero y el
  comportamiento es idéntico al actual. El default seguro es `'punta_a_punta'` solo si falta el
  campo, pero el flujo por tramos siempre lo fija a `'por_tramos'`.

---

### Defecto 4 — Reconciliación de `TotalCategoria`/`ResumenPyG` con el detalle por título

### Causa raíz

`TotalCategoria_<corte>` es un `groupby("Categoría")` de `ResumenPyG_<corte>`
(`tools/exportar_excel_tramos.py` ~L136-148). `ResumenPyG` = `_resumen_rf` + `_resumen_no_rf`.
`_resumen_rf` **ya** consume `rf_detalle` (la misma fuente que `RF_Detalle_Titulo_<corte>`), de modo
que la parte RF reconcilia **por construcción** una vez que el ruteo de categoría es coherente con
los Defectos 1 y 2. El riesgo de descuadre nace de dos puntos:

1. Un ruteo de categoría RF distinto del detalle por activo (p. ej. subyacente mal clasificado).
   Con el Defecto 1 (herencia de `id_cobertura`) y el uso de `_categoria` sobre el mismo
   `es_subyacente` que trae `rf_detalle`, el ruteo queda alineado.
2. La parte no-RF, cuyo `rf_detalle` no existe: ahí el ORI debe fijarse en 0 explícito y todo el
   resultado va a P&G (limitación documentada), para que la columna ORI de las categorías no-RF no
   mezcle valores espurios.

### Diseño: flujo de datos de la reconciliación

```
informe_pyg_por_tramo.payload["rf_detalle"]   (una fila por título RF)
        │  columnas: id_posicion, moneda, asset_class, es_subyacente,
        │            clasificacion (NEG/DV/VC), pyg, ori, total_resultado
        ▼
_resumen_rf(rf_detalle, df_clasif)
        │  categoria = _categoria("TITULO", moneda, asset_class, es_subyacente)
        │  groupby(categoria, compania, ramo, clasificacion) → sum(pyg, ori, total, ...)
        ▼
ResumenPyG_<corte>  (parte RF)   ⊕   _resumen_no_rf (RV/Alt/derivados, ORI=0)
        ▼
TotalCategoria_<corte> = groupby("Categoría").agg(sum P&G, sum ORI, sum Total)
```

Reconciliación garantizada: para cada categoría RF `C`,

```
TotalCategoria[C].pyg = Σ_(filas RF con categoria=C) rf_detalle.pyg
TotalCategoria[C].ori = Σ_(filas RF con categoria=C) rf_detalle.ori
```

porque la suma de un `groupby` de un `groupby` sobre las mismas filas es la suma directa de esas
filas (asociatividad de la suma), sin recálculo ni fuente alterna. La igualdad se evalúa dentro de
la tolerancia numérica del informe (float64).

### Cambio exacto

`_resumen_rf` ya es la vía autoritativa; el ruteo de subyacentes vía `_categoria`
("Subyacentes de Swaps") y PEI (Defecto 2) lo deja coherente. Para la parte **no-RF**, se fija ORI
explícito en 0 en todas las filas de `_resumen_no_rf` (hoy ya se emite `"ori": 0.0` en cada fila;
el diseño lo **formaliza y documenta** como invariante):

```python
    # Defecto 4 (limitación conocida): las posiciones no-RF (RV/Alt/derivados) no tienen
    # descomposición contable NEG/DV/VC, por lo que su ORI es 0 por definición y todo el
    # resultado se reporta en P&G. Esto NO afecta la reconciliación RF, que se construye
    # desde el mismo rf_detalle por título.
    filas.append({... "pyg": <pyg>, "ori": 0.0, "total_resultado": <pyg>, ...})
```

No se añade ninguna fuente nueva ni se recalcula RF por otra vía. `TotalCategoria` permanece como
`groupby` de `ResumenPyG` (AC 4.6), sin cambios en `exportar_excel_tramos.py`.

### Cómo satisface el requisito

- **AC 4.1:** la agregación RF se construye desde el mismo `rf_detalle` por título.
- **AC 4.2:** cada título RF se enruta con `_categoria` sobre su propio `es_subyacente` (Req 1) y su
  `asset_class`/PEI (Req 2).
- **AC 4.3:** por asociatividad de la suma, P&G y ORI por categoría RF en `TotalCategoria`
  coinciden con la suma del detalle por título dentro de la tolerancia float.
- **AC 4.4 / 4.5:** no-RF con ORI=0 y todo a P&G, documentado como limitación conocida.
- **AC 4.6:** `TotalCategoria_<corte>` sigue siendo `groupby("Categoría")` de `ResumenPyG_<corte>`.

---

## Data Models

### Modelo de datos (sin cambios de esquema)

No se altera ningún esquema de BD. Se usan campos ya existentes:

- `Titulo.id_cobertura: str` (dataclass, ya presente).
- `portafolio_clasificado`: `asset_class_definitivo`, `id_cobertura`, `ramo`, `compania`, `moneda`,
  `clase_origen`, `especie` (ya consultados por `_mapa_clasificacion`).
- `informe_pyg_por_tramo(fecha_corte, payload_json)`: el `payload_json` ya contiene `rf_detalle`,
  `rv`, `alt`, `deriv_pyg`, `deriv_vm_cierre`, `trm_inicio`, `trm_cierre`.
- `data['parametros']['modo']` en el store de Dash.

## Error Handling

### Manejo de errores

- Las lecturas de BD mantienen `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` y consultas
  parametrizadas (p. ej. `params=(corte,)`), cumpliendo Req 5.5 y la regla de inyección (nunca
  concatenar SQL con entrada).
- El `_mapa_clasificacion` ya es defensivo ante la ausencia de columnas (`asset_class_definitivo` /
  `id_cobertura`); la nueva columna `es_pei` se deriva con la misma tolerancia (default `False`).
- La lectura de `modo` usa `.get(...) or 'punta_a_punta'` para no romper si el campo falta, sin
  introducir un default que altere el flujo por tramos (que siempre fija `'por_tramos'`).
- Los logs se mantienen genéricos (sin especies, montos ni PII), cumpliendo Req 5.6.

---

## Correctness Properties

*Una propiedad es una característica o comportamiento que debe cumplirse en todas las ejecuciones
válidas del sistema: una afirmación formal sobre lo que el sistema debe hacer. Las propiedades son
el puente entre la especificación legible por humanos y las garantías verificables por máquina.*

### Property 1: Herencia de `id_cobertura` y condición de subyacente

*Para todo* `Titulo` origen y cualquier cupón/vencimiento reinvertido a partir de él, el `Titulo`
reinvertido hereda exactamente el `id_cobertura` del origen; en consecuencia, el `es_subyacente`
derivado del cupón reinvertido es verdadero **si y solo si** el `id_cobertura` del origen no está
vacío.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**

### Property 2: PEI particionado fuera de RV Local

*Para toda* colección de posiciones de renta variable y para todo corte, ninguna posición con
`asset_class_definitivo = 'PEI'` se agrega en la categoría "RV Local"; el total de "RV Local" es
igual a la suma del P&G de las posiciones RV locales no-PEI (COLCAP), y el total de la categoría
"PEI" es igual a la suma del P&G de las posiciones PEI. El invariante se cumple en todos los
tramos con posiciones PEI.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

### Property 3: Puerteo de hojas del store según el modo

*Para todo* store que contenga datos de `derivados`, `rv`, `alternativos` y `creditos`: cuando
`modo == 'por_tramos'` el libro resultante **no** contiene las hojas `Derivados`, `RV_Mensual`,
`Alt_Mensual`, `Creditos` ni `Creditos_Tramos`; cuando `modo == 'punta_a_punta'` esas cuatro hojas
**sí** están presentes (comportamiento inalterado).

**Validates: Requirements 3.2, 3.4, 3.5**

### Property 4: Reconciliación RF y ORI=0 en no-RF

*Para todo* corte, la suma del P&G y del ORI sobre las categorías RF de `TotalCategoria_<corte>`
es igual, dentro de la tolerancia numérica del informe, a la suma del P&G y del ORI del `rf_detalle`
de esas mismas categorías; y para toda fila no-RF del resumen se cumple `ORI == 0` y
`total_resultado == P&G`.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.6**

### Property 5: Regresión del motor — campos inalterados salvo `id_cobertura`

*Para toda* reinversión de cupón o vencimiento, todos los campos del `Titulo` reinvertido son
idénticos a los que producía el motor antes del cambio, con la única excepción de `id_cobertura`,
que ahora iguala al del título origen.

**Validates: Requirements 5.2**

---

## Testing Strategy

### Enfoque dual

- **Pruebas unitarias / de ejemplo:** wiring y casos concretos (p. ej. lectura de `modo`, selección
  de `asset_class_definitivo`, uso de `mode=ro`), más casos borde.
- **Pruebas basadas en propiedades (Hypothesis):** validan las cinco propiedades universales con
  entradas generadas. Mínimo **100 iteraciones** por propiedad; `deadline=None` por la variabilidad
  de I/O en Google Drive.

Cada test de propiedad referencia su propiedad del diseño con el tag:
`Feature: excel-por-tramos-consistencia, Property {N}: {texto}`.

### Mapa propiedad → prueba

| Propiedad | Tipo | Estrategia |
|-----------|------|-----------|
| P1 | PBT | Generar `Titulo` origen con `id_cobertura` vacío/no vacío + cupón; reinvertir por ambas rutas; afirmar `nuevo.id_cobertura == origen.id_cobertura` y `es_subyacente(detalle) == bool(id_cobertura)`. |
| P2 | PBT | Generar posiciones RV locales mezclando PEI/no-PEI; construir resumen; afirmar partición (RV Local sin PEI; totales por categoría). |
| P3 | PBT | Generar store con las cuatro claves + modo; afirmar presencia/ausencia de hojas por modo. |
| P4 | PBT | Generar `rf_detalle` sintético por corte; afirmar reconciliación RF por categoría (tolerancia) y ORI=0 en no-RF. |
| P5 | PBT | Comparar todos los campos del `Titulo` reinvertido contra un snapshot sin el cambio; única diferencia permitida: `id_cobertura`. |

### Ejemplos y casos borde (unit)

- `_categoria` con `asset_class='PEI'` → "PEI"; RV local no-PEI → "RV Local"; subyacente →
  "Subyacentes de Swaps" (AC 2.1/2.2/4.2).
- `_mapa_clasificacion` deriva `es_pei` de `asset_class_definitivo` (AC 2.4).
- `descargar_resultados_excel` lee `modo` del store (AC 3.1) y, sin campo, cae en `punta_a_punta`.
- Verificación de `mode=ro` y `params` parametrizados (AC 5.5).

### Reutilización de fixtures y convención de ejecución (Windows / Google Drive)

Se reutilizan los fixtures y la BD de ejemplo existentes. Por la ruta con espacios en Google Drive,
los tests se invocan con **ruta absoluta** y la convención establecida del proyecto:

```
python -m pytest "g:\Mi unidad\AXIS\AXIS\tests\test_<nombre>.py" ^
  --rootdir "g:\Mi unidad\AXIS\AXIS" -o addopts="" -p no:cacheprovider
```

y `@settings(deadline=None)` en los tests de Hypothesis. (El usuario ejecuta los comandos de
larga duración manualmente; aquí solo se documenta la invocación.)

### Suites existentes que deben seguir en verde (regresión, Req 5)

- `tests/test_proyeccion_tramos.py`
- `tests/test_tramos_multiactivo_*.py`
- `tests/test_property_rf_mensual_por_tramos.py` (regla NEG/DV/VC intacta, AC 5.4)
- `tests/test_orquestador_consolidacion.py`

---

## Preservación y consideraciones de regresión (Req 5)

- **Punta a punta inalterado (5.1):** el Defecto 3 se activa solo cuando `modo == 'por_tramos'`; en
  punta a punta el flujo de escritura es idéntico al actual.
- **Motor inalterado salvo `id_cobertura` (5.2):** el único cambio en `motor_proyeccion.py` es
  pasar `id_cobertura` a dos constructores; ningún otro campo ni lógica de valoración cambia
  (cubierto por P5).
- **Series `*_Tramos` preservadas (5.3):** la Vía A no se toca; `Derivados_PyG_Tramos`,
  `RV_Mensual_Tramos`, `Alt_Mensual_Tramos` conservan su contenido.
- **Regla contable NEG/DV/VC inalterada (5.4):** `scripts/rf_contable_mensual.py` y
  `descomponer_contable` no se modifican; `rf_detalle` sigue siendo la fuente única.
- **Consultas parametrizadas de solo lectura (5.5):** se mantienen `mode=ro` y `params=(...)`.
- **Logs genéricos (5.6):** sin especies, montos ni PII en los mensajes.
- **Sin defaults hardcoded (5.7):** PEI se identifica por `asset_class_definitivo='PEI'`, el modo
  por `data['parametros']['modo']`, y la condición de subyacente por `id_cobertura`; ninguna de las
  tres usa literales de especie/valor embebidos.
