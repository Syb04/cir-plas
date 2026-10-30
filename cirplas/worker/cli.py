"""Worker CLI: cirplas-solve project.json -o run.h5 [--mode ac|tran] [--progress jsonl]"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from ..core.analysis.ac import run_ac
from ..core.analysis.common import RunContext
from ..core.analysis.tran import run_tran
from ..core.netlist.builder import AcSettings, NetlistBuilder, TranSettings
from ..core.ngspice.backend import get_backend
from ..core.project import load_project
from ..core.results import write_results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cirplas-solve", description="CirPlas solver worker")
    ap.add_argument("project", help="project JSON file")
    ap.add_argument("-o", "--output", default=None, help="output HDF5 file (default: <project>.h5)")
    ap.add_argument("--mode", choices=["ac", "tran", "sc"], default=None, help="override numerics.analysis")
    ap.add_argument("--progress", choices=["jsonl", "text", "none"], default="text")
    ap.add_argument("--netlist-out", default=None, help="also write the generated netlist(s) to this .cir path")
    args = ap.parse_args(argv)

    project = load_project(args.project)
    mode = args.mode or project.numerics.analysis
    out = Path(args.output) if args.output else Path(args.project).with_suffix(".h5")
    log_lines: list[str] = []
    t0 = time.time()

    def log(msg: str) -> None:
        log_lines.append(msg)
        if args.progress == "text":
            print(msg, file=sys.stderr, flush=True)
        elif args.progress == "jsonl":
            print(json.dumps({"t": time.time() - t0, "log": msg}), flush=True)

    def progress(d: dict) -> None:
        d = {"t": round(time.time() - t0, 3), **d}
        if args.progress == "jsonl":
            print(json.dumps(d, default=str), flush=True)
        elif args.progress == "text":
            print("  " + ", ".join(f"{k}={v}" for k, v in d.items()), file=sys.stderr, flush=True)

    try:
        backend = get_backend(project.numerics.ngspice)
        ctx = RunContext(project=project, backend=backend, log=log, progress=progress)
        log(f"CirPlas solve: project={project.name} mode={mode} ngspice={getattr(backend, 'version', '?')}")
        ac = run_ac(ctx)
        netlists = {"ac": NetlistBuilder(ac.model, "ac", ac=AcSettings(frequency=ac.frequency)).build()[0]}
        tran = None
        if mode in ("tran", "sc"):
            if mode == "sc":
                log("self-consistent density iteration is not implemented yet (Phase 2); running TRAN with the prescribed density")
            tran = run_tran(ctx, ac=ac)
            period = tran.period
            netlists["tran"] = NetlistBuilder(tran.model, "tran", tran=TranSettings(
                t_step=period / project.numerics.tran.pts_per_cycle, t_stop=tran.cycles * period,
                t_max=period / project.numerics.tran.pts_per_cycle, ramp_time=project.numerics.tran.ramp_cycles * period)).build()[0]
        write_results(out, project, ac=ac, tran=tran, netlists=netlists, log=log_lines,
                      ngspice_version=str(getattr(backend, "version", "")), mode=mode)
        if args.netlist_out:
            Path(args.netlist_out).write_text(netlists.get("tran", netlists["ac"]), encoding="utf-8")
        log(f"done in {time.time()-t0:.1f} s -> {out}")
        if args.progress == "jsonl":
            print(json.dumps({"t": time.time() - t0, "done": True, "output": str(out)}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        log("ERROR: " + str(exc))
        if args.progress == "jsonl":
            print(json.dumps({"t": time.time() - t0, "error": str(exc), "traceback": traceback.format_exc()}), flush=True)
        else:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
