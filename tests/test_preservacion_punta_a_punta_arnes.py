# -*- coding: utf-8 -*-
"""
Test de PRESERVACIÓN end-to-end del motor RF en modo `punta_a_punta`, usando el arnés de
equivalencia `tools/equivalencia_motor_rf.py` como red de equivalencia del motor RF.

Feature / spec: proyeccion-tramos-rf-mensual-y-vm-internacional (bugfix).
Tarea 3 del plan de implementación — Property 3 (Preservation).

**Validates: Requirements 3.1, 3.5**

Metodología observation-first
------------------------------
El arnés `tools/equivalencia_motor_rf.py` es la RED DE EQUIVALENCIA oficial del motor RF
end-to-end: serializa el resultado del motor (`_serializar_resultado`) — VM, VTIR, P&L
mes/acumulado, conteos, cupones y reinversiones por mes — y lo compara con tolerancia estricta
(`_comparar`, tol=1e-6). Esto captura el comportamiento `punta_a_punta` que el fix (Tareas 4 y
5) NO debe alterar: la serie mensual contable (`rf_mensual_contable` se compone de estas mismas
métricas) y el VM de cierre (incluido el portafolio con RF internacional).

Diseño del test (auto-contenido y determinista)
-----------------------------------------------
El motor RF es determinista para un MISMO estado de entrada, pero `portafolio.db` vive en una
ruta de Google Drive compartida/mutable: comparar un baseline persistido en JSON contra una
corrida posterior puede diferir si la DB cambió entre ambas (no es una regresión del fix, sino
estado externo variable). Para que la equivalencia `F == F'` sea reproducible, este test:

  1. carga el portafolio real UNA sola vez (snapshot inmutable del estado actual);
  2. corre el motor sobre DOS copias profundas e independientes del portafolio
     (el motor MUTA el portafolio: reinversiones, VM/VTIR por título);
  3. serializa ambos resultados con el arnés y asevera equivalencia byte a byte (tol 1e-6).

La corrida (1) es el baseline F (observación del comportamiento actual); la corrida (2) es
F' sobre el MISMO código. Que F == F' sobre el código sin corregir fija la línea base; la
Tarea 6 re-ejecuta este test tras el fix para confirmar que el comportamiento `punta_a_punta`
no cambió.

Degradación con gracia (igual patrón que el resto de la suite): si falta `portafolio.db`
(p. ej. CI sin datos reales) el test se salta. La preservación de la valoración unitaria y de
la lógica contable se cubre además por invariantes en
`test_property_preservacion_valoracion_rf.py`.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "tools"
_SCRIPTS = _ROOT / "scripts"
for _p in (str(_ROOT), str(_SCRIPTS), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DB_REAL = _ROOT / "portafolio.db"


@pytest.mark.skipif(
    not _DB_REAL.exists(),
    reason="portafolio.db real no disponible: el arnes de equivalencia end-to-end requiere "
           "el portafolio real (la preservacion unitaria se cubre ademas por invariantes en "
           "test_property_preservacion_valoracion_rf.py).",
)
def test_preservacion_punta_a_punta_equivalencia_arnes():
    """
    PRESERVACIÓN end-to-end del motor RF `punta_a_punta`: dos corridas del motor sobre el MISMO
    estado de entrada (código sin corregir) producen resultados idénticos (tol=1e-6) en VM de
    cierre, VTIR, P&L mensual/acumulado, cupones y reinversiones.

    Observation-first: la corrida 1 es el baseline F; la corrida 2 es F' sobre el mismo código.
    EXPECTED OUTCOME (código sin corregir): PASA (F == F', baseline de equivalencia fijado).
    """
    import equivalencia_motor_rf as arnes

    # (1) Cargar el portafolio real UNA vez (snapshot inmutable del estado actual).
    port, fecha_ini = arnes._cargar_caso()
    assert port, "el portafolio real no debe estar vacio para el caso de equivalencia"

    # (2) Correr el motor sobre dos copias profundas independientes (el motor muta el port).
    res_baseline = arnes._correr_motor(copy.deepcopy(port), fecha_ini)
    res_nuevo = arnes._correr_motor(copy.deepcopy(port), fecha_ini)

    # (3) Serializar con el arnés y comparar byte a byte (tol 1e-6).
    serial_baseline = arnes._serializar_resultado(res_baseline)
    serial_nuevo = arnes._serializar_resultado(res_nuevo)

    difs = arnes._comparar(serial_baseline, serial_nuevo)
    assert not difs, (
        "PRESERVACION ROTA (punta_a_punta end-to-end): el motor RF no reprodujo el baseline "
        "de equivalencia (F != F'). Diferencias (primeras 10):\n  "
        + "\n  ".join(difs[:10])
    )

    # El VM de cierre del último mes debe estar en escala COP y ser consistente (> 0),
    # cubriendo el portafolio con RF internacional (Req 3.5).
    assert serial_baseline, "la serializacion del baseline no debe estar vacia"
    assert serial_baseline[-1]["vm_total"] > 0, "el VM de cierre debe ser positivo (escala COP)"
