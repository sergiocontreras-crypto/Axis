"""
tools/medir_speedup_rf.py
-------------------------
Mide el impacto REAL de la optimización del motor RF (Fix 3):
  - Tiempo de pared (wall-clock) del motor sobre el portafolio real.
  - Número de llamadas a `_dias_nl365` (antes ~36M = 85% del tiempo).

Reutiliza el caso del arnés de equivalencia para no inventar datos.
Uso:
    python tools/medir_speedup_rf.py            # horizonte por AXIS_EQUIV_MESES (default 3)
"""
import sys
import time
import cProfile
import pstats
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Reutiliza el cargador y runner del arnés oficial (misma lógica, mismo caso).
from equivalencia_motor_rf import _cargar_caso, _correr_motor, MESES_HORIZONTE  # noqa


def main():
    port, fi = _cargar_caso()

    # 1) Wall-clock limpio (sin profiler).
    t0 = time.perf_counter()
    _correr_motor(port, fi)
    t1 = time.perf_counter()
    wall = t1 - t0
    print(f"[WALL] motor RF ({MESES_HORIZONTE} meses): {wall:.3f} s")

    # 2) Conteo de llamadas a _dias_nl365 (con profiler, solo para el conteo).
    pr = cProfile.Profile()
    pr.enable()
    _correr_motor(port, fi)
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s)
    for func, stat in ps.stats.items():
        # func = (filename, lineno, funcname)
        if func[2] == "_dias_nl365":
            ncalls = stat[0]
            tottime = stat[2]
            print(f"[CALLS] _dias_nl365: {ncalls:,} llamadas, "
                  f"tottime={tottime:.3f}s ({func[0]}:{func[1]})")
            break
    else:
        print("[CALLS] _dias_nl365: no apareció en el profiling (0 llamadas relevantes).")


if __name__ == "__main__":
    main()
