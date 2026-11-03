"""cirplas-optimize: automatic search of FR tuning parameters for a flat sheath / uniform heating (M6).

usage: cirplas-optimize base.cirplas.json --param circuit.ports.F.sources.0.v:200:800 [--param PATH:LO:HI[:log] ...]
                        [--tie PATH:OF:SCALE:OFFSET ...] [--objective fr_step|uniformity_se|uniformity_vsh]
                        [--mode ac|tran|sc] [--grid 5] [--max-eval 40] -o best.cirplas.json [--history history.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..core.analysis.common import RunContext
from ..core.analysis.optimize import OBJECTIVES, OptimizeSpec, OptParam, Tie, optimize
from ..core.ngspice.backend import get_backend
from ..core.project import load_project


def parse_param(text: str) -> OptParam:
    parts = text.split(":")
    if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "log"):
        raise argparse.ArgumentTypeError(f"--param expects PATH:LO:HI[:log], got '{text}'")
    lo, hi = float(parts[1]), float(parts[2])
    if not hi > lo:
        raise argparse.ArgumentTypeError(f"--param {parts[0]}: HI must be greater than LO")
    if len(parts) == 4 and lo <= 0:
        raise argparse.ArgumentTypeError(f"--param {parts[0]}: a log range needs LO > 0")
    return OptParam(path=parts[0], lo=lo, hi=hi, log=len(parts) == 4)


def parse_tie(text: str) -> Tie:
    parts = text.split(":")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"--tie expects PATH:OF:SCALE:OFFSET, got '{text}'")
    return Tie(path=parts[0], of=int(parts[1]), scale=float(parts[2]), offset=float(parts[3]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cirplas-optimize", description="CirPlas FR tuning / uniformity optimization")
    ap.add_argument("project")
    ap.add_argument("--param", action="append", type=parse_param, required=True, help="PATH:LO:HI[:log] (repeat for more parameters)")
    ap.add_argument("--tie", action="append", type=parse_tie, default=[], help="dependent value PATH <- SCALE * param[OF] + OFFSET")
    ap.add_argument("--objective", choices=OBJECTIVES, default="fr_step")
    ap.add_argument("--mode", choices=["ac", "tran", "sc"], default="ac")
    ap.add_argument("--grid", type=int, default=5, help="initial scan points per parameter")
    ap.add_argument("--max-eval", type=int, default=40)
    ap.add_argument("--fr-region", default="fr")
    ap.add_argument("-o", "--output", required=True, help="optimized project JSON")
    ap.add_argument("--history", default=None, help="write every evaluation to this JSON file")
    ap.add_argument("--progress", choices=["jsonl", "text", "none"], default="text")
    args = ap.parse_args(argv)

    base = load_project(args.project)
    spec = OptimizeSpec(params=args.param, objective=args.objective, mode=args.mode, ties=args.tie, grid=args.grid,
                        max_eval=args.max_eval, fr_region=args.fr_region)
    t0 = time.time()

    def progress(d: dict) -> None:
        if args.progress == "jsonl":
            print(json.dumps({"t": round(time.time() - t0, 3), **d}, default=str), flush=True)
        elif args.progress == "text":
            print(", ".join(f"{k}={v}" for k, v in d.items()), file=sys.stderr, flush=True)

    try:
        ctx = RunContext(project=base, backend=get_backend(base.numerics.ngspice), log=lambda m: None, progress=progress)
        res = optimize(ctx, spec)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = Path(args.output)
    out.write_text(res.project.to_json(), encoding="utf-8")
    if args.history:
        Path(args.history).write_text(json.dumps({"objective": spec.objective, "mode": spec.mode, "best": res.values,
                                                  "best_objective": res.objective, "evaluations": res.history}, indent=2, default=str),
                                      encoding="utf-8")
    progress({"stage": "optimize_done", "best": res.values, "objective": res.objective, "evaluations": res.evaluations,
              "output": str(out), "done": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
