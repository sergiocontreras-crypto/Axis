"""
tools/equivalencia_motor_rf.py
------------------------------
Arnés de EQUIVALENCIA y PROFILING del motor de Renta Fija (`proyectar_portafolio`).

Objetivo (requisito del usuario): poder OPTIMIZAR el motor RF sin cambiar NI UN número.
Este arnés:
  1. Captura un BASELINE del resultado actual del motor sobre el portafolio real
     (VM, VTIR, P&L mes y acumulado, conteos, cupones y reinversiones por mes).
  2. Compara un resultado NUEVO contra el baseline con tolerancia estricta (1e-6),
     reportando CUALQUIER diferencia (valoración, renovaciones, P&L).
  3. Perfila el motor (cProfile) para ver qué sub-función consume el tiempo real,
     de modo que la optimización sea DIRIGIDA y no a ciegas.

Uso:
    python tools/equivalencia_motor_rf.py baseline     # captura baseline -> baseline_motor_rf.json
    python tools/equivalencia_motor_rf.py check        # corre de nuevo y compara vs baseline
    python tools/equivalencia_motor_rf.py profile      # perfila el motor (top funciones por tiempo)

NO modifica la DB (solo lee portafolio real para armar el caso). El baseline se guarda
en tools/ junto a este script.
"""
import sys
import json
import math
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

BASELINE_PATH = Path(__file__).resolve().parent / "baseline_motor_rf.json"
TOL = 1e-6  # tolerancia absoluta/relativa para comparación numérica

# Caso de referencia: fecha base real con datos + horizonte corto para iterar rápido.
FECHA_BASE = "2026-07-31"
# Horizonte CORTO para iterar rápido en el ciclo baseline→optimizar→check. Es suficiente
# para cubrir la lógica de valoración por tipo de bono y al menos un ciclo de cupón/
# reinversión. La verificación final de equivalencia se puede repetir con 12 meses.
import os as _os
MESES_HORIZONTE = int(_os.getenv("AXIS_EQUIV_MESES", "3"))


def _cargar_caso():
    """Carga el portafolio RF real y arma los argumentos del motor (deterministas)."""
    from motor_proyeccion import cargar_portafolio_proyeccion
    import proyeccion_tramos_multiactivo  # noqa (asegura import path)

    db = str(ROOT / "portafolio.db")
    port = cargar_portafolio_proyeccion(db, FECHA_BASE)
    if not port:
        raise SystemExit(f"No hay portafolio para {FECHA_BASE} en {db}")
    # Sendas planas mínimas (deterministas) para que el caso sea reproducible.
    # Se usan las sendas del helper de tramos para no inventar formato.
    from proyeccion_tramos import _meses_entre  # noqa
    sendas = {}  # el motor usa defaults internos / curvas por asset_class si vacío
    fecha_ini = date.fromisoformat(FECHA_BASE)
    return port, fecha_ini


def _correr_motor(port, fecha_ini):
    from motor_proyeccion import proyectar_portafolio
    return proyectar_portafolio(
        portafolio=list(port),
        fecha_inicio=fecha_ini,
        meses_horizonte=MESES_HORIZONTE,
        sendas={},
        trm=4000.0,
        valor_uvr=411.0,
    )


def _serializar_resultado(resultados):
    """Convierte la lista de ResultadoMes a una estructura JSON-comparable, capturando
    TODO lo relevante para la equivalencia: métricas por mes + reinversiones por id."""
    out = []
    for r in resultados:
        reinv = []
        for t in r.reinversiones:
            reinv.append({
                "id_posicion": str(t.id_posicion),
                "especie": str(t.especie),
                "nominal": float(t.nominal),
                "valor_mercado": float(t.valor_mercado),
                "valor_tir": float(t.valor_tir),
                "tasa_compra": float(getattr(t, "tasa_compra", 0.0)),
                "fecha_vencimiento": str(t.fecha_vencimiento),
            })
        reinv.sort(key=lambda d: d["id_posicion"])
        out.append({
            "mes": int(r.mes),
            "vm_total": float(r.vm_total),
            "vtir_total": float(r.vtir_total),
            "pnl_mercado_mes": float(r.pnl_mercado_mes),
            "pnl_tir_mes": float(r.pnl_tir_mes),
            "pnl_mercado_acum": float(r.pnl_mercado_acum),
            "pnl_tir_acum": float(r.pnl_tir_acum),
            "n_titulos": int(r.n_titulos),
            "n_reinversiones_mes": int(r.n_reinversiones_mes),
            "cupones_cobrados_mes": float(r.cupones_cobrados_mes),
            "reinversiones": reinv,
        })
    return out


def _casi_igual(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= TOL + TOL * max(abs(a), abs(b))
    return a == b


def _comparar(base, nuevo):
    """Devuelve lista de diferencias (vacía si equivalente)."""
    difs = []
    if len(base) != len(nuevo):
        difs.append(f"n_meses: baseline={len(base)} nuevo={len(nuevo)}")
        return difs
    campos = ["vm_total", "vtir_total", "pnl_mercado_mes", "pnl_tir_mes",
              "pnl_mercado_acum", "pnl_tir_acum", "n_titulos",
              "n_reinversiones_mes", "cupones_cobrados_mes"]
    for mb, mn in zip(base, nuevo):
        for c in campos:
            if not _casi_igual(mb[c], mn[c]):
                difs.append(f"mes {mb['mes']} campo {c}: base={mb[c]} nuevo={mn[c]}")
        # Reinversiones: mismo conjunto de ids y mismos valores
        rb = {d["id_posicion"]: d for d in mb["reinversiones"]}
        rn = {d["id_posicion"]: d for d in mn["reinversiones"]}
        if set(rb) != set(rn):
            difs.append(f"mes {mb['mes']} reinversiones ids distintos: "
                        f"solo_base={set(rb)-set(rn)} solo_nuevo={set(rn)-set(rb)}")
        else:
            for idp in rb:
                for c in ("nominal", "valor_mercado", "valor_tir", "tasa_compra"):
                    if not _casi_igual(rb[idp][c], rn[idp][c]):
                        difs.append(f"mes {mb['mes']} reinv {idp} {c}: "
                                    f"base={rb[idp][c]} nuevo={rn[idp][c]}")
    return difs


def cmd_baseline():
    port, fi = _cargar_caso()
    res = _correr_motor(port, fi)
    data = _serializar_resultado(res)
    BASELINE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"OK baseline guardado: {BASELINE_PATH} ({len(data)} meses)")


def cmd_check():
    if not BASELINE_PATH.exists():
        raise SystemExit("No existe baseline. Corre primero: python tools/equivalencia_motor_rf.py baseline")
    base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    port, fi = _cargar_caso()
    res = _correr_motor(port, fi)
    nuevo = _serializar_resultado(res)
    difs = _comparar(base, nuevo)
    if not difs:
        print("OK EQUIVALENTE: el resultado es idéntico al baseline (tol=1e-6).")
    else:
        print(f"DIFERENCIAS ({len(difs)}):")
        for d in difs[:50]:
            print("  ", d)
        raise SystemExit(1)


def cmd_profile():
    import cProfile, pstats, io
    port, fi = _cargar_caso()
    pr = cProfile.Profile()
    pr.enable()
    _correr_motor(port, fi)
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(30)
    print(s.getvalue())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"baseline": cmd_baseline, "check": cmd_check, "profile": cmd_profile}.get(cmd, cmd_check)()
