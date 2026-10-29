# Implementation Plan: Lectura de reservas por encabezados de fecha

## Overview

Convert the feature design into a series of incremental coding steps that refactor the reservas reader/persistor/loader over date headers, versioned persistence, and wire the annual jumps into the tramos orchestrator and dashboard alerts. Work is surgical: it reuses the existing aggregation-by-ramo machinery, `Diccionarios_Mapeo`, `calcular_tramos`, `prorratear_reservas_anual`, and the motor's monthly (`Mes_Caído`) and tramos (`Prorrateo_Anual`) consumption paths. Each step builds on the previous one and ends by wiring components together so there is no orphaned code.

**Base (Req 1–13) — DONE.** Tasks 1–12 below implement the date-header reader, versioned persistence, anchored loading, tramos wiring and dashboard alerts. That code is already in place and green in `scripts/compras_ventas_reservas.py`, `app_dash.py` and `scripts/proyeccion_tramos.py`; those tasks are marked `[x]` and MUST NOT be re-done.

**Extension (Req 14–20 / Properties 17–23) — TODO.** Tasks 13–22 add ONLY the `Layout_Por_Tramo` disposition on top of the finished base, without altering the `Layout_Mensual` behaviour already in production. They add an optional `fecha_corte` parameter to `derivar_variaciones_reservas`, the new `detectar_tramos_por_corte` / `prorratear_interes_lineal_direccional` functions and the `Segmento` structure, wire the by-tranche derivation, pass `Fecha_Corte` from the dashboard, and cover Properties 17–23 plus the worked examples. These tasks are unchecked `[ ]`. They reuse `prorratear_reservas_anual`, `calcular_tramos`, the versioned persistor (`persistir_input_reservas`), the loader (`cargar_mapa_reservas`) and the motor UNCHANGED.

Property-based tests use `hypothesis` (minimum 100 iterations per property test) and each is tagged with the format:
`# Feature: lectura-reservas-encabezados-fecha, Property {N}: {texto de la propiedad}`

## Tasks

- [x] 1. Establish domain error hierarchy and date-header detection primitives
  - [x] 1.1 Add typed domain exceptions in `scripts/compras_ventas_reservas.py`
    - Define `ErrorFormatoEncabezado(ErrorInputReservas)` and `ErrorHojasFaltantes(ErrorInputReservas)` per the Error Handling hierarchy
    - Keep both inheriting from `ErrorInputReservas` for capture retrocompatibility
    - _Requirements: 2.2, 2.3, 2.4, 11.1, 11.2, 11.4_

  - [x] 1.2 Implement `_es_encabezado_fecha(valor)` date detection helper
    - Return True for `datetime`/`date`/`pd.Timestamp` or text parseable to date that is NOT purely numeric nor a month name
    - Guard against false positives (integer offset `1`) and false negatives (Excel-serialized dates) per Decisión 4
    - _Requirements: 1.1, 1.2, 2.1, 2.2_

  - [x] 1.3 Write property test for date-header detection robustness
    - **Property 1: Localización de columnas por Encabezado_Fecha** (partial: cell-level detection)
    - **Validates: Requirements 1.1**
    - Generate datetime/serialized/date cells (true) mixed with numeric and month-name noise cells (false)

- [x] 2. Implement column localization and chronological ordering
  - [x] 2.1 Implement `_localizar_columnas_fecha(df)` localization by date header
    - Return `(fila_encabezados, col_etiqueta, columnas)` with `columnas` as list of `(indice_columna, primer_dia_del_mes)` sorted chronologically ascending
    - Scan top rows for the row with >= 2 date cells; label column is first text column left of first date header
    - Raise `ErrorFormatoEncabezado` when headers are month names or when no date header exists
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.2, 2.3_

  - [x] 2.2 Write property test for column localization
    - **Property 1: Localización de columnas por Encabezado_Fecha**
    - **Validates: Requirements 1.1, 1.4**
    - Generate sheets with arbitrary date columns plus non-date noise columns; assert exactly the date columns are located

  - [x] 2.3 Write property test for temporal axis derivation and ordering
    - **Property 2: Derivación y orden cronológico del eje temporal**
    - **Validates: Requirements 1.2, 1.3, 4.1, 4.4**
    - Present date headers in arbitrary order; assert Mes_Absoluto = (año, mes), ascending order, first month = min, independent of start month

  - [x] 2.4 Write property test for header-format rejection (missing/month-name headers)
    - **Property 4: Saltos inválidos señalan error de formato** (partial: header shape)
    - **Validates: Requirements 2.1, 2.2, 2.3**
    - Generate sheets with month-name headers or no recognizable date; assert `ErrorFormatoEncabezado` and no variations derived

- [x] 3. Implement jump classification between consecutive columns
  - [x] 3.1 Implement `_clasificar_saltos(columnas)` classification by month difference
    - For consecutive ordered columns return `(col_desde, col_hasta, tipo, mes_absoluto_hasta)` with `tipo` in `{'mensual','anual'}`
    - Δmeses == 1 → `'mensual'`; Δmeses == 12 → `'anual'`; any other → `ErrorFormatoEncabezado`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 Write property test for jump classification
    - **Property 3: Clasificación de saltos por diferencia de meses**
    - **Validates: Requirements 3.1, 3.2, 3.3**
    - Use mixed-pattern generator (current-year months then subsequent Decembers); assert monthly vs annual classification

  - [x] 3.3 Write property test for invalid jump rejection
    - **Property 4: Saltos inválidos señalan error de formato** (jump portion)
    - **Validates: Requirements 3.4**
    - Generate consecutive columns whose month difference is neither 1 nor 12; assert `ErrorFormatoEncabezado`

- [x] 4. Refactor `derivar_variaciones_reservas` to consume date headers
  - [x] 4.1 Add `_validar_hojas` and wire sheet validation first
    - Raise `ErrorHojasFaltantes` if "Saldo Reservas" or "Interés a la Reserva" is missing, before any column localization
    - _Requirements: 2.4, 12.1_

  - [x] 4.2 Generalize `_variacion_mes_a_mes` and refactor `derivar_variaciones_reservas`
    - Localize date columns per sheet, order chronologically, classify each jump, and for each jump derive `Δsaldo − Δinterés` per ramo vs the previous column
    - Reuse existing `_construir_saldos_por_ramo`, `_construir_intereses`, and Capitalizadora (`TOTAL CAPI`) aggregation unchanged; keep applying `Diccionarios_Mapeo`
    - Coerce non-numeric/empty saldo and interés cells to `0.0` (finite); normalize labels (`strip`/`upper`) before mapping
    - Return `DataFrame[ramo, mes ("YYYY-MM"), delta_reserva, tipo_salto]` labeling `mes` as the Mes_Absoluto of the jump destination column
    - _Requirements: 1.1, 3.5, 4.1, 4.2, 4.3, 4.4, 12.1, 12.2, 13.1, 13.2_

  - [x] 4.3 Write property test for variation as Δsaldo − Δinterés via Diccionarios_Mapeo
    - **Property 6: Variación como Δsaldo − Δinterés homologada por los Diccionarios_Mapeo**
    - **Validates: Requirements 3.5, 12.2**
    - Generate consecutive columns with per-label saldo/interés levels; assert per-ramo delta equals aggregated `(saldo_hasta − saldo_desde) − (interés_hasta − interés_desde)`

  - [x] 4.4 Write property test for label normalization
    - **Property 7: Normalización de etiquetas insensible a ruido**
    - **Validates: Requirements 13.2**
    - Generate ramo labels with whitespace/case variations; assert same output ramo as canonical form

  - [x] 4.5 Write property test for non-numeric coercion to zero
    - **Property 8: Coerción de valores no numéricos a cero**
    - **Validates: Requirements 13.1**
    - Generate empty/non-numeric saldo/interés cells; assert level used is `0.0` and derivation does not abort

  - [x] 4.6 Write property test for distinct-year Decembers (reader keys)
    - **Property 10: Inyectividad de meses absolutos para diciembres de años distintos** (reader portion)
    - **Validates: Requirements 5.3, 9.4**
    - Generate December columns of distinct years; assert distinct `mes` keys in the output DataFrame

  - [x] 4.7 Write unit test for missing-sheet error
    - **Property 5: Hojas faltantes señalan un error distinto del de formato**
    - **Validates: Requirements 2.4**
    - Build workbook missing each required sheet; assert `ErrorHojasFaltantes` raised and distinct from `ErrorFormatoEncabezado`

- [x] 5. Checkpoint - Reader complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement versioned persistence
  - [x] 6.1 Create versioned schema and `persistir_input_reservas`
    - Idempotently `CREATE TABLE IF NOT EXISTS input_variacion_reservas_versiones (version_id, ramo, mes, delta_reserva, tipo_salto, fecha_carga, PRIMARY KEY (version_id, ramo, mes))` and its `ix_ivrv_carga` index
    - Assign a new `version_id` (`MAX(version_id)+1`) and ISO-8601 second-precision `fecha_carga` inside the transaction; preserve prior versions (no DELETE)
    - Validate column allowlist `{ramo, mes, delta_reserva}` (ignore `tipo_salto` if absent, else persist it); validate `mes` with `_normalizar_mes_reserva` enforcing `"YYYY-MM"` (4-digit year, month 01-12) → `ValueError` otherwise
    - Discard rows with non-finite `delta_reserva` logging a generic warning (no row content); use parameterized queries only; retry on `database is locked`
    - Return the number of persisted rows
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.4, 13.3, 13.4_

  - [x] 6.2 Write property test for Mes_Absoluto validity and malformed-month rejection
    - **Property 9: Persistencia con Mes_Absoluto válido y rechazo de meses mal formados**
    - **Validates: Requirements 5.1, 5.4**
    - Round-trip valid DataFrames (all recovered `mes` match `"YYYY-MM"`); generate malformed months (year ≠ 4 digits, month `00`/`13`, free text) → `ValueError` without persisting

  - [x] 6.3 Write property test for distinct-year December injectivity in storage
    - **Property 10: Inyectividad de meses absolutos para diciembres de años distintos**
    - **Validates: Requirements 5.3, 9.4**
    - Persist multiple distinct-year Decembers; assert each stored under a distinct `mes` key with no collision

  - [x] 6.4 Write property test for invalid-row discard without affecting valid rows
    - **Property 11: Descarte de filas no coercionables sin afectar a las válidas**
    - **Validates: Requirements 13.4**
    - Mix rows with non-finite `delta_reserva` and valid rows; assert only invalid rows discarded (generic warning) and all valid rows persisted

  - [x] 6.5 Write property test for non-destructive versioning and latest selection
    - **Property 12: Versionado no destructivo y selección de la más reciente**
    - **Validates: Requirements 6.1, 6.2, 6.3**
    - Perform successive loads; assert each creates a new timestamped version preserving previous ones and latest is selectable by `MAX(fecha_carga)`

- [x] 7. Implement anchored loading with Mes_Caído
  - [x] 7.1 Refactor `cargar_mapa_reservas` to read latest version and anchor to Fecha_Inicio_Proyeccion
    - Select the most recent version via `MAX(fecha_carga)` (tie-break `MAX(version_id)`)
    - For each `(ramo, mes="YYYY-MM", delta)` anchor Mes_Absoluto to `fecha_inicio` applying Mes_Caído: delta of calendar month `M` maps to projection offset `t` where projection month `t-1 == M`
    - Keep only offsets in `[1, meses_horizonte]`; omit the rest; discard rows with non-`"YYYY-MM"` mes or non-finite delta with a generic warning
    - Return `dict[str, dict[int, float]]`
    - _Requirements: 6.3, 7.1, 7.2, 7.3, 10.1, 12.4_

  - [x] 7.2 Implement `cargar_variacion_anual_por_ano` for annual jumps
    - Read the most recent version and return `{ano_diciembre: {ramo: delta_anual}}` for `tipo_salto == 'anual'` only
    - Use parameterized queries; select latest version consistently with `cargar_mapa_reservas`
    - _Requirements: 8.1, 8.2, 6.3_

  - [x] 7.3 Write property test for anchoring with Mes_Caído
    - **Property 13: Anclaje del Mes_Absoluto con convención Mes_Caído**
    - **Validates: Requirements 7.1, 7.2**
    - For arbitrary Fecha_Inicio_Proyeccion and persisted Mes_Absoluto `M`, assert delta placed at offset `t` where calendar month of `t-1 == M`, keeping only `[1, meses_horizonte]`

  - [x] 7.4 Write property test for absent ramo/mes omission
    - **Property 14: Ramo/mes ausente se omite (motor asume Δ=0)**
    - **Validates: Requirements 7.3, 10.1**
    - For any ramo/mes without persisted variation (incl. horizon years without a column), assert the key is absent from the returned map

- [x] 8. Checkpoint - Persistence and loading complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Wire annual variation into the tramos orchestrator
  - [x] 9.1 Connect `variacion_anual_por_tramo` in `_ejecutar_proyeccion_por_tramos` (`app_dash.py`)
    - Build `variacion_anual_por_ano = cargar_variacion_anual_por_ano(str(DB_PATH))`
    - Assign to each tramo the annual variation of its `fecha_corte.year` (or `{}` when the year has no annual jump)
    - Reuse `calcular_tramos` for tramo delimitation and `prorratear_reservas_anual` for annual splitting (no reimplementation)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 12.3_

  - [x] 9.2 Write property test for annual-variation-per-tramo construction
    - **Property 15: Construcción de la variación anual por tramo desde los Salto_Anual**
    - **Validates: Requirements 8.1, 8.2**
    - Generate sets of annual jumps; assert grouping by destination December year and per-tramo assignment (empty when year lacks an annual jump)

  - [x] 9.3 Write property test for annual proration sum invariant
    - **Property 16: Prorrateo anual conserva la suma**
    - **Validates: Requirements 8.3**
    - For any annual variation per ramo and `sub_horizonte ≥ 1`, assert the sum of monthly deltas from `prorratear_reservas_anual` equals the annual variation within tolerance

- [x] 10. Wire dashboard alerts and informative note
  - [x] 10.1 Discriminate error types in `cargar_excel_reservas` callback (`app_dash.py`)
    - Catch `ErrorHojasFaltantes`, `ErrorFormatoEncabezado`, `ErrorInputReservas`, `ErrorPersistenciaReservas` and return specific blocking alerts per the Dashboard section
    - On success return confirmation with the number of persisted rows; keep messages generic (no file content)
    - Ensure a failed load leaves the input state invalid so the projection run cannot proceed with that file
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 10.2 Add non-blocking informative note for horizon years without a column
    - Compute horizon years lacking a source column and render an informative (non-blocking) note listing them; these years use Δ=0
    - _Requirements: 10.1, 10.2, 10.3_

  - [x] 10.3 Write unit tests for dashboard alert discrimination and success message
    - Assert each exception type yields its specific blocking alert, success yields the row-count confirmation, and the informative note is non-blocking
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 10.2, 10.3_

- [x] 11. Integration and regression wiring
  - [x] 11.1 End-to-end wiring of reader → persistor → loader → motor paths
    - Verify `motor_proyeccion.proyectar_portafolio` consumes the anchored monthly map (Mes_Caído) and the tramos path consumes `prorratear_reservas_anual`, both unchanged
    - _Requirements: 7.2, 8.3, 12.4_

  - [x] 11.2 Write integration test with the mixed-pattern real file shape
    - Build a workbook with current-year months then subsequent Decembers; run reader → persist → load and assert monthly and annual paths produce the expected map/tramo inputs
    - _Requirements: 3.3, 8.1, 8.2, 9.3, 10.1_

  - [x] 11.3 Write smoke tests for reuse and architecture constraints
    - Assert two-sheet structure and Diccionarios_Mapeo are preserved and that `calcular_tramos`/`prorratear_reservas_anual` are reused (not reimplemented)
    - _Requirements: 9.1, 12.1, 12.3_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Tasks — Extension: Layout_Por_Tramo (Req 14–20 / Properties 17–23)

> All tasks below are NEW and build on the finished base (tasks 1–12). They reuse the existing machinery and do NOT modify the `Layout_Mensual` path, the persistor, the loader, `calcular_tramos`, `prorratear_reservas_anual` or the motor.

- [x] 13. Implement the directional linear interest proration (pure function)
  - [x] 13.1 Implement `prorratear_interes_lineal_direccional(interes_acumulado, n_meses, delta_saldo_periodo, tolerancia=1e-6)` in `scripts/compras_ventas_reservas.py`
    - Return `list[float]` of N monthly interest deltas using weights `1..N` when `delta_saldo_periodo >= 0` (CRECIENTE: month `i` proportional to `i`) and weights `N..1` when `delta_saldo_periodo < 0` (DECRECIENTE: month `i` proportional to `N-i+1`)
    - Divide each weight by the triangular number `1+2+...+N`; preserve the SIGN of `interes_acumulado` (only the ORDER of weights flips), and absorb the rounding residual in the highest-weight month (last month for CRECIENTE, first month for DECRECIENTE) so `sum(deltas) == interes_acumulado` exactly, mirroring `prorratear_reservas_anual`
    - Coerce `interes_acumulado` and `delta_saldo_periodo` to finite `float` (non-numeric/empty → `0.0`); raise `ValueError` when `n_meses < 1`
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.7, 19.3, 20.1_

  - [ ]* 13.2 Write property test for directional linear interest proration
    - **Property 22: Prorrateo lineal direccional del interés — suma invariante y dirección por signo**
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.7**
    - Generate arbitrary `interes_acumulado` (positive/negative/zero), `N ∈ 1..12`, `delta_saldo_periodo` of both signs; assert `sum(deltas) == interes_acumulado` within tolerance, weight monotonicity per sign, and that flipping the sign of `delta_saldo_periodo` reverses the order of the deltas without changing the sum

  - [ ]* 13.3 Write unit tests for the worked interest examples
    - CRECIENTE: `interes_acumulado=75`, `N=5`, `Δsaldo≥0` ⇒ `[5, 10, 15, 20, 25]`
    - DECRECIENTE: `interes_acumulado=75`, `N=5`, `Δsaldo<0` ⇒ `[25, 20, 15, 10, 5]`
    - `n_meses < 1` ⇒ `ValueError`
    - _Requirements: 17.5, 17.6_

- [x] 14. Implement tranche detection by cut-off date
  - [x] 14.1 Add the `Segmento` data structure in `scripts/compras_ventas_reservas.py`
    - Define `Segmento(tipo, mes_base, meses)` (dataclass/NamedTuple) where `tipo ∈ {'parcial','anual','mensual'}`, `mes_base: date` is the Mes_Absoluto of the base level, and `meses: list[date]` are the ascending destination Mes_Absoluto covered (N for `'parcial'`, 12 for `'anual'`, 1 for `'mensual'`)
    - _Requirements: 14.3, 15.2, 15.3_

  - [x] 14.2 Implement `detectar_tramos_por_corte(columnas, mes_corte) -> list[Segmento]`
    - Input `columnas` is the ordered `list[tuple[int, date]]` produced by `_localizar_columnas_fecha`; `mes_corte` is the first day of the Mes_Corte
    - Mes_Corte is the base level (NOT projected, NOT emitted as a row); when Mes_Corte is not among the columns, use the level of the last column `<= Mes_Corte` as synthetic base (Req 15.2)
    - `Tramo_Parcial`: months strictly after Mes_Corte up to and including the FIRST destination December (N months), created only when that first destination is a December and `N != 1` (`N == 1` is a plain Salto_Mensual) (Req 15.3)
    - `Tramo_Anual`: each subsequent December-to-December interval = 12 months (Req 15.5)
    - `mensual`: 1-month jumps outside the Tramo_Parcial stay monthly (Req 15.6); monthly columns strictly before Mes_Corte are read as valid monthly columns without error (Req 15.8)
    - Any jump outside the Tramo_Parcial whose month difference ∉ `{1, 12}` ⇒ `ErrorFormatoEncabezado` (Req 15.7); the Mes_Corte→December jump inside the partial tranche is valid by definition
    - _Requirements: 14.3, 15.2, 15.3, 15.5, 15.6, 15.7, 15.8_

  - [ ]* 14.3 Write property test for tranche detection and membership by cut-off
    - **Property 17: Detección y membresía del tramo por la Fecha_Corte**
    - **Validates: Requirements 14.3, 15.1, 15.2, 15.3, 15.5, 15.6, 18.5**
    - Generate ordered date columns with a Mes_Corte followed by a destination December (`N ∈ 2..12`) and optional subsequent annual Decembers, including monthly columns before Mes_Corte; assert the Tramo_Parcial spans exactly the months strictly after Mes_Corte to the December inclusive, Mes_Corte emits no row, each Dec-to-Dec is a 12-month Tramo_Anual, and tranche months are consecutive `YYYY-MM`

  - [ ]* 14.4 Write property test for invalid jump outside the Tramo_Parcial
    - **Property 19: Salto inválido fuera del Tramo_Parcial señala error**
    - **Validates: Requirements 15.7**
    - Generate Layout_Por_Tramo sequences that insert a jump with month difference ∉ `{1, 12}` OUTSIDE the Tramo_Parcial; assert `ErrorFormatoEncabezado` and no variations derived

  - [ ]* 14.5 Write unit test for partial-tranche sizing (worked example)
    - Fecha_Corte 31-jul-2026 (Mes_Corte julio-2026), first destination column dic-2026 ⇒ `N = 5`, meses `2026-08..2026-12`, base `2026-07`
    - _Requirements: 15.4_

- [x] 15. Checkpoint - Pure tranche primitives complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. Add optional `fecha_corte` parameter to `derivar_variaciones_reservas`
  - [x] 16.1 Extend the signature retrocompatibly
    - Add `fecha_corte: "object | None" = None` to `derivar_variaciones_reservas` in `scripts/compras_ventas_reservas.py`
    - When `fecha_corte is None`, keep the EXACT current `Layout_Mensual` path (no observable change)
    - When `fecha_corte` is present but not coercible to a valid date, raise the generic `ErrorInputReservas` BEFORE sizing tranches or deriving, without leaking the received value (Req 20.3); reuse `_primer_dia_del_mes` for the valid-date path to obtain `mes_corte`
    - Activate the by-tranche path only when `fecha_corte` is present AND a December column immediately follows the Mes_Corte column; otherwise fall back to the pure `Layout_Mensual` path (Decisión 6)
    - _Requirements: 15.1, 20.3, 14.1, 14.2_

  - [ ]* 16.2 Write property test for coexistence (monthly layout unchanged)
    - **Property 18: Coexistencia — el Layout_Mensual no cambia**
    - **Validates: Requirements 14.1, 14.2**
    - Generate pure `Layout_Mensual` inputs (jumps of 1 and 12 months); assert the derived DataFrame with `fecha_corte=None` is identical (same `ramo, mes, delta_reserva, tipo_salto` rows) to the one with a `Fecha_Corte` that does not produce a Tramo_Parcial

  - [ ]* 16.3 Write unit test for invalid Fecha_Corte
    - Pass a non-coercible `fecha_corte` ⇒ generic `ErrorInputReservas`, no variations derived, value not leaked
    - _Requirements: 20.3_

- [x] 17. Wire the by-tranche derivation inside `derivar_variaciones_reservas`
  - [x] 17.1 Implement the by-tranche derivation loop
    - Reuse `_construir_saldos_por_ramo_fecha` / `_construir_intereses_fecha` (UNCHANGED, date-keyed) to obtain `{ramo: {Mes_Absoluto: nivel}}` for saldo and interés; normalize labels and coerce cells via the existing helpers (Req 20.1, 20.2)
    - Compute `segmentos = detectar_tramos_por_corte(columnas_saldo, mes_corte)`; for `tipo == 'mensual'` keep the existing per-jump `Δsaldo − Δinterés`
    - For `tipo ∈ {'parcial','anual'}` with `N = len(segmento.meses)`: `Δsaldo_periodo = nivel_saldo(Dec destino) − nivel_saldo(mes_base)`; monthly saldo deltas via `prorratear_reservas_anual({ramo: Δsaldo_periodo}, N)[ramo]` (UNIFORM, reused as Prorrateo_Uniforme); monthly interest deltas via `prorratear_interes_lineal_direccional(nivel_interes(Dec destino), N, Δsaldo_periodo)` (Interes_Acumulado_Periodo used directly, not by subtraction)
    - Emit one row per tranche month: `delta_reserva = Δsaldo_mensual − Δinteres_mensual`, `mes = Mes_Absoluto` `"YYYY-MM"`, `tipo_salto = 'mensual'` (Decisión 7); include Capitalizadora (`TOTAL CAPI`) the same way
    - Return the same `DataFrame[ramo, mes, delta_reserva, tipo_salto]` that `persistir_input_reservas` already expects (no persistor changes)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.6, 17.1, 18.1, 18.4, 18.5, 19.1, 19.2, 19.4, 19.5_

  - [ ]* 17.2 Write property test for uniform saldo proration sum invariant
    - **Property 21: Prorrateo uniforme del saldo conserva la suma**
    - **Validates: Requirements 16.1, 16.2, 16.3, 16.4, 16.6**
    - For arbitrary base/destination Nivel_Saldo (including negatives/zeros) and `N ∈ 1..12`, assert `sum(monthly saldo deltas) == Δsaldo_periodo` within tolerance for both the Tramo_Parcial (N) and each Tramo_Anual (12)

  - [ ]* 17.3 Write property test for combined monthly variation
    - **Property 23: Variación de reserva mensual = Δsaldo prorrateado − Δinterés prorrateado**
    - **Validates: Requirements 18.1**
    - For any Layout_Por_Tramo tranche, assert per month `reserva_i == saldo_i − interes_i` and `Σ reserva == Δsaldo_periodo − Interes_Acumulado_Periodo`

  - [ ]* 17.4 Write property test for pre-cutoff monthly columns not rejected and omitted by Mes_Caído
    - **Property 20: Columnas mensuales pre-corte no se rechazan y las omite Mes_Caído**
    - **Validates: Requirements 15.8**
    - Generate monthly columns (Δ=1) before Mes_Corte; assert the reader raises no error and, after anchoring to Fecha_Inicio_Proyeccion, `cargar_mapa_reservas` omits their `< 1` offsets

  - [ ]* 17.5 Write unit tests for the worked combined-variation examples
    - Saldo base 100 (jul-2026) → 150 (dic-2026), `N=5` ⇒ saldo deltas `[10,10,10,10,10]` (levels 110,120,130,140,150)
    - Combined CRECIENTE: saldo `+10/mes` and interés `5,10,15,20,25` ⇒ variación `[5, 0, -5, -10, -15]`
    - Combined DECRECIENTE: interés `25,20,15,10,5` ⇒ the corresponding decreasing combined case
    - _Requirements: 16.5, 18.2, 18.3_

- [x] 18. Checkpoint - By-tranche reader complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 19. Wire the dashboard to pass Fecha_Corte to the reader
  - [x] 19.1 Add `State('sidebar-fecha-inicio', 'date')` to `cargar_excel_reservas` (`app_dash.py`)
    - The current callback is `cargar_excel_reservas(contents, filename)` with `Input('upload-reservas','contents')` + `State('upload-reservas','filename')`; add `State('sidebar-fecha-inicio', 'date')` and a `fecha_corte` parameter (confirmed real id `sidebar-fecha-inicio` at `app_dash.py`)
    - Coerce to date (`pd.to_datetime(fecha_corte).date() if fecha_corte else None`) and pass `fecha_corte=fc` into `derivar_variaciones_reservas`; `None` ⇒ Layout_Mensual path
    - Keep the existing error discrimination and messages unchanged (`ErrorHojasFaltantes`, `ErrorFormatoEncabezado`, `ErrorInputReservas`, `ErrorPersistenciaReservas`, success row-count)
    - _Requirements: 15.1_

- [ ] 20. Integration and reuse verification
  - [ ]* 20.1 Write an integration test with a by-tranche in-memory workbook
    - Build a workbook with columns julio-2026, then diciembre-2026, then diciembre-2027; run reader (`fecha_corte=31-jul-2026`) → `persistir_input_reservas` → `cargar_mapa_reservas`
    - Assert the prorated monthly map matches the worked example (base 2026-07, `N=5` partial tranche, +10/month saldo) and that a subsequent Tramo_Anual (dic-2026→dic-2027, 12 months) is prorated and emitted as monthly rows
    - _Requirements: 15.4, 16.5, 17.7, 18.1, 18.5, 19.4, 19.5_

  - [ ]* 20.2 Write smoke tests for reuse and architecture constraints
    - Assert the by-tranche saldo proration delegates to `prorratear_reservas_anual` (Prorrateo_Uniforme), the interest uses the NEW `prorratear_interes_lineal_direccional`, emitted rows are monthly (`tipo_salto='mensual'`) and persisted by the UNCHANGED versioned persistor, the motor consumes them via the existing `Mes_Caído` path in `cargar_mapa_reservas`, and the `Diccionarios_Mapeo` are applied via `_construir_saldos_por_ramo_fecha`/`_construir_intereses_fecha`
    - _Requirements: 14.4, 18.4, 19.1, 19.2, 19.4, 19.5_

- [x] 21. Final checkpoint - Layout_Por_Tramo extension complete
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific granular requirements for traceability.
- Checkpoints ensure incremental validation.
- Property-based tests (hypothesis, ≥100 iterations) validate the 23 universal correctness properties (Properties 1–16 for the base, 17–23 for the Layout_Por_Tramo extension); unit/integration/smoke tests cover UI, messaging, worked examples, and reuse criteria per the design's Testing Strategy.
- All SQLite access uses parameterized queries; file content is treated as untrusted and never leaked in errors or logs (Req 13, Req 20).
- The Layout_Por_Tramo extension (tasks 13–21) reuses `prorratear_reservas_anual`, `calcular_tramos`, the versioned persistor, the loader and the motor UNCHANGED; only `prorratear_interes_lineal_direccional`, `detectar_tramos_por_corte`, the `Segmento` structure and the `fecha_corte` wiring are new.

## Task Dependency Graph

> The base tasks (1.x–11.x) are complete; the graph below schedules only the NEW leaf sub-tasks of the Layout_Por_Tramo extension (tasks 13–20). Optional test sub-tasks are included. `13.1` (interest function) and `14.1`/`14.2` (Segmento + detection) are independent early waves; `17.1` depends on both plus `16.1`; tests follow the code they exercise. `19.1` (dashboard, `app_dash.py`) is independent of the reader work and can run early.

```json
{
  "waves": [
    { "id": 0, "tasks": ["13.1", "14.1", "19.1"] },
    { "id": 1, "tasks": ["13.2", "13.3", "14.2"] },
    { "id": 2, "tasks": ["14.3", "14.4", "14.5", "16.1"] },
    { "id": 3, "tasks": ["16.2", "16.3", "17.1"] },
    { "id": 4, "tasks": ["17.2", "17.3", "17.4", "17.5"] },
    { "id": 5, "tasks": ["20.1", "20.2"] }
  ]
}
```
