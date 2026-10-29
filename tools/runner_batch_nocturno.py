"""
tools/runner_batch_nocturno.py
==============================
Runner AUTONOMO (sin dashboard) para dejar corriendo VARIOS escenarios por tramos durante
la noche y obtener, por cada escenario, un Excel de resumen P&G por categoria x compania x
ramo, por cada corte de tramo (dic-AAAA). Si un tramo no termina, el Excel se genera con
los tramos YA terminados (lectura incremental de la tabla informe_pyg_por_tramo).

Uso (ejemplo):
    python tools/runner_batch_nocturno.py ^
        --inicio 2026-05-20 --fin 2028-12-31 ^
        --pack "Escenario1=Inputs Py/inputs_por_tramo_ejemplo.xlsx" ^
        --pack "Escenario2=Inputs Py/inputs_por_tramo_ejemplo_superniño.xlsx" ^
        --trm-final 4200 --repo-ini 9.25 --repo-fin 7.0 --ipc-vigente 5.2

Cada escenario usa su propia copia de la BD (aislada) para no mezclar resultados:
copia portafolio.db -> portafolio_<escenario>.db y apunta AXIS_DB_PATH ahi ANTES de
importar los modulos del motor.

NOTA: este runner replica el armado de argumentos del callback
`correr_escenario_consolidado` (modo por_tramos) de app_dash. No modifica el motor.
Ver `.kiro/specs/informe-portafolio-por-tramo-batch/` para el diseno formal.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
for _p in (str(PROJECT_ROOT), str(SCRIPTS_DIR), str(PROJECT_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CLAVE_A_LISTA = {
    "tramo-shift-cop": "tramo_shift_cop", "tramo-shift-uvr": "tramo_shift_uvr",
    "tramo-shift-fs": "tramo_shift_fs", "tramo-shift-ipc": "tramo_shift_ipc",
    "tramo-periodicidad": "tramo_periodicidad", "tramo-inflacion": "tramo_inflacion",
    "tramo-repo": "tramo_repo", "tramo-trm": "tramo_trm",
    "tramo-ret-acc-local": "tramo_ret_acc_local", "tramo-ret-acc-intl": "tramo_ret_acc_intl",
    "tramo-ret-alt-local": "tramo_ret_alt_local", "tramo-ret-alt-intl": "tramo_ret_alt_intl",
    "tramo-ret-alt-ipc": "tramo_ret_alt_ipc", "tramo-pei": "tramo_pei",
    "tramo-fed": "tramo_fed", "tramo-tesoros10y": "tramo_tesoros10y",
}


def _g(lst, i, d=0):
    return (lst[i] if lst and i < len(lst) and lst[i] is not None else d)


def _pct(lst, i):
    v = _g(lst, i, None)
    if v is None:
        return None
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return None


def _leer_pack(ruta_excel: Path, app_dash):
    contenido = ruta_excel.read_bytes()
    valores, reconocidas, ignoradas, n_cols = app_dash._parsear_excel_inputs_tramos(contenido)
    listas = {}
    for clave, serie in valores.items():
        nombre = _CLAVE_A_LISTA.get(clave)
        if nombre:
            listas[nombre] = serie
    return listas, reconocidas, ignoradas, n_cols


def _armar_argumentos(listas: dict):
    g = listas.get
    n = max(
        len(g("tramo_shift_cop", []) or []), len(g("tramo_periodicidad", []) or []),
        len(g("tramo_inflacion", []) or []), len(g("tramo_repo", []) or []),
        len(g("tramo_trm", []) or []), len(g("tramo_ret_acc_local", []) or []),
    )
    shifts = [{
        "shift_cop": _g(g("tramo_shift_cop"), i), "shift_uvr": _g(g("tramo_shift_uvr"), i),
        "shift_fs": _g(g("tramo_shift_fs"), i), "shift_ipc": _g(g("tramo_shift_ipc"), i),
    } for i in range(n)]
    periodicidad = [_g(g("tramo_periodicidad"), i, "mensual") for i in range(n)]
    inputs = [{
        "inflacion_ea": _pct(g("tramo_inflacion"), i), "repo_ea": _pct(g("tramo_repo"), i),
        "mov_tes_cop_bps": _g(g("tramo_shift_cop"), i, None),
        "mov_tes_uvr_bps": _g(g("tramo_shift_uvr"), i, None),
        "spread_corp_ipc_bps": _g(g("tramo_shift_ipc"), i, None),
        "spread_corp_tf_bps": _g(g("tramo_shift_fs"), i, None),
        "trm_cierre": _g(g("tramo_trm"), i, None),
        "ret_acc_local_ea": _pct(g("tramo_ret_acc_local"), i),
        "ret_acc_intl_ea": _pct(g("tramo_ret_acc_intl"), i),
        "ret_alt_local_ea": _pct(g("tramo_ret_alt_local"), i),
        "ret_alt_intl_ea": _pct(g("tramo_ret_alt_intl"), i),
        "ret_alt_ipc_bps": _g(g("tramo_ret_alt_ipc"), i, None),
        "pei_ea": _pct(g("tramo_pei"), i),
        "delta_fed_bps": _g(g("tramo_fed"), i, None),
        "delta_tesoros10y_bps": _g(g("tramo_tesoros10y"), i, None),
    } for i in range(n)]
    return shifts, periodicidad, inputs, n


def _escribir_excel(resumen_por_corte: dict, ruta_salida: Path):
    import pandas as pd
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta_salida, engine="openpyxl") as writer:
        if not resumen_por_corte:
            pd.DataFrame({"info": ["Aun no hay tramos terminados."]}).to_excel(
                writer, sheet_name="Info", index=False)
            return
        # Hoja indice.
        idx = pd.DataFrame({"corte": list(resumen_por_corte.keys())})
        idx.to_excel(writer, sheet_name="Indice", index=False)
        for corte, df in resumen_por_corte.items():
            hoja = f"PyG_{str(corte).replace('-', '')}"[:31]
            df.to_excel(writer, sheet_name=hoja, index=False)


def correr_escenario(nombre, ruta_pack, fecha_inicio, fecha_fin, args):
    """Corre un escenario completo con BD aislada y genera su Excel (incremental)."""
    import pandas as pd
    print(f"\n{'='*70}\n[ESCENARIO] {nombre}: {ruta_pack}\n{'='*70}", flush=True)

    # BD aislada por escenario (copia local de portafolio.db).
    base_db = PROJECT_ROOT / "portafolio.db"
    db_dir = Path(args.db_dir) if args.db_dir else PROJECT_ROOT
    db_esc = db_dir / f"portafolio_{nombre}.db"
    db_dir.mkdir(parents=True, exist_ok=True)
    print(f"[ESCENARIO] Copiando BD a {db_esc} ...", flush=True)
    shutil.copy2(base_db, db_esc)
    os.environ["AXIS_DB_PATH"] = str(db_esc)

    # Import DESPUES de setear AXIS_DB_PATH (comun.rutas lee el env al importar).
    import importlib
    import comun.rutas as _rutas
    importlib.reload(_rutas)
    import app_dash  # carga el monolito (una vez por proceso)

    listas, reconocidas, ignoradas, n_cols = _leer_pack(Path(ruta_pack), app_dash)
    print(f"[ESCENARIO] Pack: {len(reconocidas)} variables, {n_cols} columnas(tramos). "
          f"Ignoradas: {ignoradas or 'ninguna'}", flush=True)
    shifts, periodicidad, inputs, n = _armar_argumentos(listas)

    t0 = time.perf_counter()
    try:
        resultados, msg = app_dash._ejecutar_escenario_por_tramos(
            fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
            shifts_por_tramo=shifts,
            periodicidad_por_tramo=periodicidad,
            variacion_anual_por_tramo=None,
            trm_final=args.trm_final,
            repo_ini=args.repo_ini, repo_fin=args.repo_fin,
            store_senda_inf=None,
            ipc_vigente=args.ipc_vigente,
            inputs_por_tramo=inputs,
            metodo_deriv=args.metodo_deriv,
            set_progress=None,
        )
        print(f"[ESCENARIO] {nombre} terminado en {time.perf_counter()-t0:.0f}s", flush=True)
    except Exception as e:
        print(f"[ESCENARIO] {nombre} FALLO: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()

    # Export (incremental): lee lo que haya quedado en informe_pyg_por_tramo.
    try:
        import export_resumen_tramos as ert
        resumen = ert.generar_resumen_por_tramo(str(db_esc))
        salida = (Path(args.out_dir) if args.out_dir else PROJECT_ROOT) / f"resumen_{nombre}_AXIS.xlsx"
        _escribir_excel(resumen, salida)
        print(f"[ESCENARIO] {nombre}: Excel -> {salida} ({len(resumen)} cortes)", flush=True)
    except Exception as e:
        print(f"[ESCENARIO] {nombre}: no se pudo generar el Excel: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()


def main(argv):
    p = argparse.ArgumentParser(description="Runner batch nocturno AXIS por escenarios.")
    p.add_argument("--inicio", required=True, help="Fecha inicio YYYY-MM-DD")
    p.add_argument("--fin", required=True, help="Fecha fin YYYY-MM-DD")
    p.add_argument("--pack", action="append", required=True,
                   help="Escenario: 'Nombre=ruta.xlsx' (repetible)")
    p.add_argument("--trm-final", type=float, default=None, dest="trm_final")
    p.add_argument("--repo-ini", type=float, default=None, dest="repo_ini")
    p.add_argument("--repo-fin", type=float, default=None, dest="repo_fin")
    p.add_argument("--ipc-vigente", type=float, default=None, dest="ipc_vigente")
    p.add_argument("--metodo-deriv", type=int, default=1, dest="metodo_deriv")
    p.add_argument("--db-dir", default=None, dest="db_dir",
                   help="Carpeta para las BD por escenario (recomendado: disco local)")
    p.add_argument("--out-dir", default=None, dest="out_dir",
                   help="Carpeta de salida de los Excel")
    p.add_argument("--single", action="store_true",
                   help="(interno) corre UN solo escenario en este proceso aislado")
    args = p.parse_args(argv)

    packs = []
    for spec in args.pack:
        if "=" in spec:
            nombre, ruta = spec.split("=", 1)
        else:
            ruta = spec
            nombre = Path(spec).stem
        packs.append((nombre.strip(), ruta.strip()))

    print(f"[RUNNER] {len(packs)} escenario(s). Horizonte {args.inicio} -> {args.fin}", flush=True)

    if args.single:
        # Modo subproceso: corre UN escenario (nombre=packs[0]) en este proceso aislado.
        nombre, ruta = packs[0]
        correr_escenario(nombre, ruta, args.inicio, args.fin, args)
        return 0

    # Modo orquestador: lanza un SUBPROCESO por escenario para aislar AXIS_DB_PATH y el
    # estado del monolito. Secuencial (no en paralelo) para no saturar CPU/RAM.
    import subprocess
    for nombre, ruta in packs:
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--inicio", args.inicio, "--fin", args.fin,
               "--pack", f"{nombre}={ruta}", "--single",
               "--metodo-deriv", str(args.metodo_deriv)]
        for flag, val in (("--trm-final", args.trm_final), ("--repo-ini", args.repo_ini),
                          ("--repo-fin", args.repo_fin), ("--ipc-vigente", args.ipc_vigente),
                          ("--db-dir", args.db_dir), ("--out-dir", args.out_dir)):
            if val is not None:
                cmd += [flag, str(val)]
        print(f"\n[RUNNER] Lanzando subproceso para {nombre} ...", flush=True)
        env = dict(os.environ, PYTHONUTF8="1")
        try:
            subprocess.run(cmd, env=env, check=False)
        except Exception as e:
            print(f"[RUNNER] Subproceso {nombre} fallo: {type(e).__name__}: {e}", flush=True)
    print("[RUNNER] Fin de la bateria.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
