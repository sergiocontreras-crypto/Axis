"""
tools/sembrar_clasificacion_ramos.py
------------------------------------
Carga inicial (siembra por DATO, de un solo uso) de la clasificación de ramos de
reserva en la tabla `config_ramos_reserva`.

Contexto (feature compras-ventas-reservas): la tabla `config_ramos_reserva` es la
Fuente_Unica_De_Verdad de la clasificación matemática/no-matemática de cada ramo y su
instrumento de compra. El motor y el dashboard NO llevan una lista por defecto en
código: la clasificación vive como DATO en la base. Este script deja el estado inicial
poblado UNA sola vez, escribiendo cada ramo con el mismo mecanismo que usa el dashboard
(`persistir_clasificacion_ramo`: normalización + validación + UPSERT parametrizado).

Comportamiento:
- Es IDEMPOTENTE y CONSERVADOR: por defecto NO pisa ramos ya clasificados (respeta
  ediciones hechas desde el dashboard). Usa --forzar para sobrescribir.
- Solo escribe ramos de esta lista de arranque; los ramos nuevos que aparezcan en el
  futuro seguirán mostrándose como "pendientes" en la pestaña Ajustes, tal como se diseñó.

Uso:
    python tools/sembrar_clasificacion_ramos.py            # siembra solo los faltantes
    python tools/sembrar_clasificacion_ramos.py --forzar   # sobrescribe también los existentes
    python tools/sembrar_clasificacion_ramos.py --dry-run  # muestra qué haría, sin escribir

Clasificación de arranque (confirmada por negocio):
- Ramos de reserva matemática:
    RENTAS VITALICIAS                          -> TES_UVR
    VIDA INDIVIDUAL-MATEMATICA                 -> TF   (caso especial)
    RIESGOS PROFESIONALES-MATEMATICA           -> TES_UVR
    PENSIONES CONMUTACION PENSIONAL            -> TES_UVR
    PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA -> TES_UVR
    PREVISIONAL - PERU                         -> TES_UVR
    RENTAS VOLUNTARIAS                         -> TES_UVR
- El resto de ramos del portafolio se clasifican como NO matemática (instrumento NULL);
  su compra sigue la regla del Requirement 4 (TES UVR nodo configurable, Negociable).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permitir importar `scripts/` y `comun/` al ejecutar desde la raíz o desde tools/.
_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comun.db import get_conn  # noqa: E402
from compras_ventas_reservas import (  # noqa: E402
    InstrumentoCompra,
    _normalizar_ramo,
    asegurar_tabla_config_ramos,
    persistir_clasificacion_ramo,
)

# Ramos de reserva matemática y su instrumento. Vida Individual-Matemática compra TF;
# los demás matemáticos compran TES UVR (Requirement 3). El resto del portafolio se
# siembra como NO matemática.
_RAMOS_MATEMATICOS: tuple[tuple[str, InstrumentoCompra], ...] = (
    ("RENTAS VITALICIAS", InstrumentoCompra.TES_UVR),
    ("VIDA INDIVIDUAL-MATEMATICA", InstrumentoCompra.TF),
    ("RIESGOS PROFESIONALES-MATEMATICA", InstrumentoCompra.TES_UVR),
    ("PENSIONES CONMUTACION PENSIONAL", InstrumentoCompra.TES_UVR),
    ("PREVISIONALES DE INVALIDEZ Y SOBREVIVENCIA", InstrumentoCompra.TES_UVR),
    ("PREVISIONAL - PERU", InstrumentoCompra.TES_UVR),
    ("RENTAS VOLUNTARIAS", InstrumentoCompra.TES_UVR),
)


def _ramos_del_portafolio() -> list[str]:
    """
    Devuelve los ramos DISTINTOS de `portafolio_clasificado`, normalizados. Lectura de
    solo lectura y parametrizada (sin interpolar entrada externa).
    """
    conn = get_conn()
    try:
        filas = conn.execute(
            "SELECT DISTINCT ramo FROM portafolio_clasificado"
        ).fetchall()
    finally:
        conn.close()

    ramos: set[str] = set()
    for fila in filas:
        norm = _normalizar_ramo(fila[0]) if fila[0] is not None else ""
        if norm:
            ramos.add(norm)
    return sorted(ramos)


def _ramos_ya_clasificados() -> set[str]:
    """Conjunto de ramos (normalizados) que ya tienen fila en `config_ramos_reserva`."""
    conn = get_conn()
    try:
        asegurar_tabla_config_ramos(conn)
        filas = conn.execute("SELECT ramo FROM config_ramos_reserva").fetchall()
    finally:
        conn.close()
    return {_normalizar_ramo(f[0]) for f in filas if f[0] is not None and _normalizar_ramo(f[0])}


def construir_plan() -> list[tuple[str, bool, "InstrumentoCompra | None"]]:
    """
    Construye el plan de siembra (ramo_normalizado, es_matematica, instrumento).

    Universo = ramos del portafolio ∪ los ramos matemáticos de la lista de arranque
    (por si alguno no estuviera presente en el portafolio actual). Cada ramo matemático
    lleva su instrumento; el resto se marca como no matemática (instrumento None).
    """
    mat_por_ramo = {_normalizar_ramo(r): instr for r, instr in _RAMOS_MATEMATICOS}

    universo: set[str] = set(_ramos_del_portafolio())
    universo |= set(mat_por_ramo.keys())

    plan: list[tuple[str, bool, "InstrumentoCompra | None"]] = []
    for ramo in sorted(universo):
        if ramo in mat_por_ramo:
            plan.append((ramo, True, mat_por_ramo[ramo]))
        else:
            plan.append((ramo, False, None))
    return plan


def sembrar(*, forzar: bool = False, dry_run: bool = False) -> None:
    """
    Aplica el plan de siembra. Por defecto NO pisa ramos ya clasificados (idempotente
    y conservador). Con `forzar=True` sobrescribe. Con `dry_run=True` solo reporta.
    """
    plan = construir_plan()
    ya = _ramos_ya_clasificados()

    escritos = 0
    omitidos = 0
    for ramo, es_mat, instr in plan:
        if ramo in ya and not forzar:
            print(f"  = OMITIDO (ya clasificado): {ramo}")
            omitidos += 1
            continue

        etiqueta = f"MATEMATICA/{instr.value}" if es_mat else "NO-matematica"
        if dry_run:
            accion = "SOBRESCRIBIRIA" if ramo in ya else "INSERTARIA"
            print(f"  ~ {accion}: {ramo} -> {etiqueta}")
            continue

        persistir_clasificacion_ramo(ramo, es_mat, instr)
        accion = "ACTUALIZADO" if ramo in ya else "INSERTADO"
        print(f"  + {accion}: {ramo} -> {etiqueta}")
        escritos += 1

    print()
    if dry_run:
        print(f"[dry-run] Plan: {len(plan)} ramos ({omitidos} ya clasificados se omitirían).")
    else:
        print(f"Siembra completada: {escritos} escritos, {omitidos} omitidos "
              f"(de {len(plan)} ramos del plan).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Siembra inicial de config_ramos_reserva (Opción A: siembra por dato)."
    )
    parser.add_argument(
        "--forzar", action="store_true",
        help="Sobrescribe ramos ya clasificados (por defecto se conservan).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra el plan sin escribir en la base de datos.",
    )
    args = parser.parse_args()

    print("Siembra de clasificación de ramos (config_ramos_reserva)")
    print("--------------------------------------------------------")
    sembrar(forzar=args.forzar, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
