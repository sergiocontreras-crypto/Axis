# Requirements Document

## Introduction

El informe Excel en modo **por tramos** (`modo == 'por_tramos'`) del proyecto AXIS consolida el resultado de la proyección por cada tramo del horizonte (típicamente un corte por fin de año). A diferencia del modo **punta a punta**, el informe por tramos reconstruye sus hojas desde la base de datos (`informe_pyg_por_tramo`) para abarcar TODOS los tramos, no solo el último.

Este spec corrige cuatro defectos que afectan la consistencia de ese informe por tramos:

1. Los cupones de subyacentes de swap reinvertidos pierden el flag `es_subyacente`, por lo que su P&G no se atribuye a la categoría "Subyacentes de Swaps".
2. El PEI no se separa de RV Local: la clasificación de categoría agrupa PEI dentro de RV Local porque solo discrimina renta variable por moneda.
3. Las hojas `Derivados`, `RV_Mensual`, `Alt_Mensual` y `Creditos` del informe por tramos muestran números incorrectos, porque se escriben a partir del store, que en modo por tramos solo contiene el ÚLTIMO tramo.
4. La hoja `TotalCategoria_<corte>` no cuadra con el detalle de P&G/ORI por activo, porque la agregación RF se construye por una vía distinta a la del detalle por título.

Las correcciones deben ser quirúrgicas y preservar intacto el comportamiento del modo punta a punta y las internas del motor de valoración, salvo la propagación mínima de `id_cobertura` requerida por el Defecto 1.

Archivos y funciones de referencia:

- `scripts/motor_proyeccion.py` — reinversión de cupón `reinvertir_cupon` (~L705-731) y reinversión por vencimiento `Titulo(...)` (~L1067-1095).
- `tools/detalle_pyg_tramo.py::construir_rf_detalle` — deriva `es_subyacente` de `id_cobertura` (L148) y aplica la regla contable NEG/DV/VC vía `scripts/rf_contable_mensual.py`.
- `tools/export_resumen_tramos.py::_categoria` (~L36-57) — clasificación de categoría; `_especies_pei`/`asset_class_definitivo='PEI'` como patrón de identificación de PEI (`tools/multiescenarios.py` L841-852).
- `app_dash.py::descargar_resultados_excel` (~L8919-9015) — escritura de hojas del Excel según presencia de datos del store; `data['parametros']['modo']` indica el modo (`app_dash.py` L8296).
- `tools/exportar_excel_tramos.py` (~L136-148) — `TotalCategoria_<corte>` como groupby de `ResumenPyG_<corte>`; `generar_resumen_por_tramo` (`_resumen_rf` + `_resumen_no_rf`).

## Glossary

- **AXIS**: Proyecto Python de proyección y valoración de portafolio sobre el que se corrigen los defectos.
- **por_tramos**: Modo de proyección que produce un corte de resultado por cada tramo del horizonte (p. ej. fin de año) y reconstruye el informe desde la base de datos.
- **punta_a_punta**: Modo de proyección con un solo juego de retornos de principio a fin del horizonte; su informe Excel se escribe directamente desde el store.
- **id_cobertura**: Atributo de un `Titulo` que identifica la relación de cobertura con un derivado. Su presencia (valor no vacío) marca el título como subyacente.
- **es_subyacente**: Flag booleano derivado de `id_cobertura` (`bool(str(id_cobertura).strip())`); indica que el título es subyacente de un swap.
- **Subyacente**: Título de renta fija asociado a una cobertura (swap), cuyo P&G se atribuye a la categoría "Subyacentes de Swaps".
- **Cupón reinvertido**: Nuevo `Titulo` generado por el motor al reinvertir un cupón cobrado o el monto de un vencimiento (`es_reinversion=True`).
- **_cupones_cobrados**: Mecanismo por el cual los cupones reinvertidos quedan plegados (contabilizados) dentro del título padre, sin generar filas de detalle independientes.
- **PEI**: Clase de activo de renta variable identificada por `asset_class_definitivo = 'PEI'` en `portafolio_clasificado`; debe reportarse en su propia categoría.
- **RV Local**: Categoría de renta variable local que, tras la corrección, comprende únicamente COLCAP (sin PEI).
- **Categoría**: Etiqueta de agrupación del informe por tramos (p. ej. RF Local, RF Internacional, RV Local, RV Internacional, PEI, Subyacentes de Swaps, Swaps, Forwards, Opciones, Alt Local, Alt Internacional).
- **ResumenPyG_<corte>**: Hoja del informe por tramos con el resumen de P&G/ORI por categoría, compañía y ramo para un corte dado.
- **TotalCategoria_<corte>**: Hoja compacta del informe por tramos con una fila por categoría, obtenida por agregación de `ResumenPyG_<corte>`.
- **rf_detalle**: Detalle de P&G/ORI por título de renta fija del tramo (`tools/detalle_pyg_tramo.py::construir_rf_detalle`); fuente de verdad del resultado RF por activo.
- **Regla contable NEG/DV/VC**: Regla de descomposición P&G/ORI por clasificación contable del título (Negociable, Disponible para Venta, al Vencimiento), definida en `scripts/rf_contable_mensual.py`.
- **Series `*_Tramos`**: Series mensuales consolidadas de todos los tramos leídas desde la base de datos (p. ej. `Derivados_PyG_Tramos`, `RV_Mensual_Tramos`, `Alt_Mensual_Tramos`).
- **ORI**: Otro Resultado Integral; componente del resultado separado del P&G según la clasificación contable.
- **store**: Almacén de estado del callback de Dash que, en modo por tramos, solo contiene datos del último tramo.

## Bug Analysis

### Defecto 1 — Cupones de subyacentes mal marcados

- **Actual**: En `scripts/motor_proyeccion.py`, tanto `reinvertir_cupon` como la reinversión por vencimiento construyen el nuevo `Titulo(...)` sin asignar `id_cobertura`. Como `construir_rf_detalle` deriva `es_subyacente` de `id_cobertura`, el cupón reinvertido de un título subyacente deja de marcarse como subyacente.
- **Esperado**: El cupón reinvertido hereda el `id_cobertura` del título original, de modo que conserva su condición de subyacente al agregarse al informe. No se generan filas de detalle nuevas; los cupones siguen plegados en el padre vía `_cupones_cobrados`.

### Defecto 2 — PEI no separado de RV Local

- **Actual**: `tools/export_resumen_tramos.py::_categoria` clasifica la renta variable únicamente por moneda ("RV Local" / "RV Internacional"), sin rama para PEI, por lo que el PEI cae en "RV Local".
- **Esperado**: El PEI se asigna a una categoría propia "PEI", identificada por `asset_class_definitivo = 'PEI'` (mismo patrón que `_especies_pei`), y queda EXCLUIDO de "RV Local" (que pasa a comprender solo COLCAP). La categoría "PEI" persiste en las hojas `ResumenPyG_<corte>` y `TotalCategoria_<corte>` de TODOS los tramos.

### Defecto 3 — Hojas con números incorrectos en por tramos

- **Actual**: En `app_dash.py::descargar_resultados_excel`, las hojas `Derivados`, `RV_Mensual`, `Alt_Mensual` y `Creditos` se escriben según la presencia de datos del store. En modo por tramos el store solo trae el último tramo, por lo que esas hojas muestran números incorrectos.
- **Esperado**: En modo por tramos esas cuatro hojas se omiten del Excel escrito desde el store. Las series correctas de todos los tramos ya existen como `Derivados_PyG_Tramos`, `RV_Mensual_Tramos` y `Alt_Mensual_Tramos` (reconstruidas desde la base de datos). Créditos no aparece en el informe por tramos. El modo punta a punta no cambia.

### Defecto 4 — TotalCategoria no cuadra con pyg/ORI por activo

- **Actual**: `TotalCategoria_<corte>` es un groupby de `ResumenPyG_<corte>` (`tools/exportar_excel_tramos.py` ~L136-148), alimentado por `generar_resumen_por_tramo` = `_resumen_rf` + `_resumen_no_rf`. La agregación RF se construye por una vía que no coincide exactamente con el detalle por título, de modo que `TotalCategoria` no cuadra con el P&G/ORI del detalle por activo.
- **Esperado**: La agregación RF de `ResumenPyG_<corte>` y `TotalCategoria_<corte>` se reconstruye desde el MISMO `rf_detalle` por título que usan las hojas por activo, con el ruteo de categoría (PEI y subyacentes) alineado con los Defectos 1 y 2, de modo que RF reconcilia por construcción. Para no-RF (RV/Alt/derivados) el ORI es 0 explícito y todo el resultado va a P&G, documentado como limitación conocida.

## Requirements

### Requirement 1: Herencia de id_cobertura en cupones reinvertidos

**User Story:** Como analista de portafolio, quiero que los cupones reinvertidos de títulos subyacentes conserven su condición de subyacente, para que su P&G se atribuya a la categoría correcta en el informe por tramos.

#### Acceptance Criteria

1. WHEN el motor de proyección reinvierte un cupón mediante `reinvertir_cupon`, THE Motor_Proyeccion SHALL asignar al `Titulo` reinvertido el valor de `id_cobertura` del título original.
2. WHEN el motor de proyección reinvierte el monto de un vencimiento construyendo un nuevo `Titulo`, THE Motor_Proyeccion SHALL asignar al `Titulo` reinvertido el valor de `id_cobertura` del título original.
3. WHERE el título original tiene `id_cobertura` no vacío, THE Informe_Por_Tramos SHALL marcar el cupón reinvertido como `es_subyacente = verdadero`.
4. WHERE el título original tiene `id_cobertura` vacío o ausente, THE Motor_Proyeccion SHALL propagar ese mismo `id_cobertura` vacío al cupón reinvertido, sin introducir una marca de subyacente.
5. THE Motor_Proyeccion SHALL mantener los cupones reinvertidos plegados en el título padre vía `_cupones_cobrados`, sin generar filas de detalle adicionales en `rf_detalle`.

### Requirement 2: Categoría propia PEI separada de RV Local

**User Story:** Como analista de portafolio, quiero que el PEI se reporte en su propia categoría y no dentro de RV Local, para leer correctamente el resultado de renta variable local.

#### Acceptance Criteria

1. WHEN la función de clasificación `_categoria` evalúa una posición de renta variable con `asset_class_definitivo = 'PEI'`, THE Clasificador_Categoria SHALL asignar la categoría "PEI".
2. WHEN la función de clasificación `_categoria` evalúa una posición de renta variable local que no es PEI, THE Clasificador_Categoria SHALL asignar la categoría "RV Local".
3. THE Clasificador_Categoria SHALL excluir toda posición PEI de la categoría "RV Local".
4. THE Clasificador_Categoria SHALL identificar el PEI por `asset_class_definitivo = 'PEI'` en `portafolio_clasificado`, siguiendo el mismo patrón que `_especies_pei`.
5. THE Informe_Por_Tramos SHALL incluir la categoría "PEI" en las hojas `ResumenPyG_<corte>` y `TotalCategoria_<corte>` de todos los tramos en los que existan posiciones PEI.

### Requirement 3: Omisión de hojas incorrectas en modo por tramos

**User Story:** Como analista de portafolio, quiero que el Excel por tramos no muestre hojas con números del último tramo, para evitar interpretar cifras incorrectas.

#### Acceptance Criteria

1. WHEN el callback `descargar_resultados_excel` genera el Excel, THE Exportador_Excel SHALL leer el modo de proyección actual desde `data['parametros']['modo']`.
2. WHILE el modo de proyección es `por_tramos`, THE Exportador_Excel SHALL omitir las hojas `Derivados`, `RV_Mensual`, `Alt_Mensual` y `Creditos` escritas desde el store.
3. WHILE el modo de proyección es `por_tramos`, THE Exportador_Excel SHALL conservar las series consolidadas `Derivados_PyG_Tramos`, `RV_Mensual_Tramos` y `Alt_Mensual_Tramos` reconstruidas desde la base de datos.
4. WHILE el modo de proyección es `por_tramos`, THE Exportador_Excel SHALL excluir del Excel la hoja de Créditos, sin construir una serie `Creditos_Tramos`.
5. WHILE el modo de proyección es `punta_a_punta`, THE Exportador_Excel SHALL escribir las hojas `Derivados`, `RV_Mensual`, `Alt_Mensual` y `Creditos` con el mismo comportamiento actual.

### Requirement 4: Reconciliación de TotalCategoria y ResumenPyG con el detalle por título

**User Story:** Como analista de portafolio, quiero que `TotalCategoria_<corte>` cuadre con el P&G/ORI por activo, para confiar en los totales por categoría del informe por tramos.

#### Acceptance Criteria

1. WHEN se genera la agregación RF de `ResumenPyG_<corte>` y `TotalCategoria_<corte>`, THE Resumen_Por_Tramo SHALL construir dicha agregación a partir del mismo `rf_detalle` por título que alimenta las hojas por activo.
2. WHEN la agregación RF se calcula desde `rf_detalle`, THE Resumen_Por_Tramo SHALL rutear la categoría de cada título RF de forma coherente con la categoría "PEI" (Requirement 2) y con la condición de subyacente (Requirement 1).
3. THE Resumen_Por_Tramo SHALL hacer que el P&G y el ORI de las categorías RF en `TotalCategoria_<corte>` coincidan, dentro de la tolerancia numérica del informe, con la suma del P&G y el ORI del detalle por título de las mismas categorías.
4. WHEN se agregan posiciones no-RF (renta variable, alternativos, derivados), THE Resumen_Por_Tramo SHALL fijar el ORI en 0 y asignar todo el resultado a P&G.
5. THE Resumen_Por_Tramo SHALL documentar como limitación conocida que las posiciones no-RF reportan ORI = 0 y todo el resultado en P&G.
6. THE Informe_Por_Tramos SHALL obtener `TotalCategoria_<corte>` como agregación por categoría de `ResumenPyG_<corte>`.

### Requirement 5: Preservación del comportamiento existente (prevención de regresiones)

**User Story:** Como responsable del modelo, quiero que las correcciones no alteren el comportamiento ya validado, para evitar regresiones en el motor y en el modo punta a punta.

#### Acceptance Criteria

1. THE Exportador_Excel SHALL producir el informe Excel del modo `punta_a_punta` sin cambios respecto al comportamiento actual.
2. THE Motor_Proyeccion SHALL mantener sin cambios la lógica interna de valoración, salvo la propagación mínima de `id_cobertura` descrita en el Requirement 1.
3. THE Informe_Por_Tramos SHALL preservar las series `*_Tramos` existentes (`Derivados_PyG_Tramos`, `RV_Mensual_Tramos`, `Alt_Mensual_Tramos`) sin alterar su contenido.
4. THE Informe_Por_Tramos SHALL mantener sin cambios la regla contable NEG/DV/VC definida en `scripts/rf_contable_mensual.py` para la descomposición P&G/ORI de renta fija.
5. WHERE una consulta a la base de datos sea necesaria para reconstruir series o agregaciones, THE Informe_Por_Tramos SHALL usar consultas parametrizadas de solo lectura.
6. IF se registra un evento en logs durante estas operaciones, THEN THE Informe_Por_Tramos SHALL emitir mensajes genéricos sin exponer datos sensibles.
7. THE Informe_Por_Tramos SHALL evitar valores por defecto fijados en código (hardcoded) para la identificación de PEI, del modo de proyección y de la condición de subyacente.
