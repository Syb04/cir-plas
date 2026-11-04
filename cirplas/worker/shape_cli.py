"""cirplas-shape: top-electrode shape that flattens the electron heating or the electron density over the wafer.

usage: cirplas-shape base.cirplas.json -o best.cirplas.json [--objective s_e|density] [--mode ac|sc|tran] [--tran-heating] [--n-ctrl 6] [--dz-mm 10] [--max-slope 0.2]
                     [--min-gap-mm 15] [--free-rim] [--smooth 1e-3] [--max-eval 12] [--target-rms 0.005] [--region wafer]
                     [--history shape.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..core.analysis.common import RunContext
from ..core.analysis.shape import MODES, OBJECTIVES, ShapeResult, ShapeSpec, optimize_shape
from ..core.ngspice.backend import get_backend
from ..core.project import load_project


def result_summary(spec: ShapeSpec, res: ShapeResult) -> dict:
    """What the GUI and scripts read back: the shape, the profiles before and after, and how the search ended."""

    def profile(p) -> dict:
        return {"r": p.r.tolist(), "s_e": p.s_e.tolist(), "density": None if p.density is None else p.density.tolist(), "values": p.values.tolist(),
                "target": (p.weights > 0).tolist(), "rms": p.rms, "uniformity": p.uniformity, "mean": p.mean, "s_e_mean": p.s_e_mean, "power": p.power}

    return {"mode": spec.mode, "objective": spec.objective, "r_shape": res.r_shape.tolist(), "gap_start": res.gap_start.tolist(), "gap_best": res.gap_best.tolist(),
            "start": profile(res.start), "best": profile(res.best), "stop_reason": res.stop_reason, "active": res.active,
            "cancelled": res.cancelled, "evaluations": res.history, "jacobian_evaluations": res.jacobian_evaluations}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cirplas-shape", description="CirPlas top-electrode shape optimization for a flat electron heating or density")
    ap.add_argument("project")
    ap.add_argument("--objective", choices=OBJECTIVES, default="s_e", help="profile to flatten (density needs mode sc)")
    ap.add_argument("--mode", choices=MODES, default=None,
                    help="model the flatness is measured with (tran: Jacobian from AC, one transient per step; default: ac, sc for density)")
    ap.add_argument("--tran-heating", action=argparse.BooleanOptionalAction, default=None,
                    help="mode sc: correct the density with the TRAN heating (default: as the project's refine_with_tran)")
    ap.add_argument("--n-ctrl", type=int, default=6, help="free control coefficients of the shape")
    ap.add_argument("--dz-mm", type=float, default=10.0, help="largest displacement from the starting shape [mm]")
    ap.add_argument("--max-slope", type=float, default=0.2, help="largest |dz/dr| of the electrode surface")
    ap.add_argument("--min-gap-mm", type=float, default=15.0, help="smallest distance to the lower surfaces [mm]")
    ap.add_argument("--free-rim", action="store_true", help="let the electrode move at the outer wall too")
    ap.add_argument("--smooth", type=float, default=1e-3, help="weight of the curvature term")
    ap.add_argument("--max-eval", type=int, default=12, help="evaluations of the chosen model (Jacobians not counted)")
    ap.add_argument("--target-rms", type=float, default=0.005, help="stop below this relative RMS of S_e")
    ap.add_argument("--region", default=None, help="region to flatten (default: the first one)")
    ap.add_argument("-o", "--output", required=True, help="project JSON with the optimized gap table")
    ap.add_argument("--history", default=None, help="write the shape, the profiles and every evaluation to this JSON file")
    ap.add_argument("--progress", choices=["jsonl", "text", "none"], default="text")
    ap.add_argument("--cancel-file", default=None, help="stop after the current solve as soon as this file exists")
    args = ap.parse_args(argv)

    base = load_project(args.project)
    spec = ShapeSpec(mode=args.mode or ("sc" if args.objective == "density" else "ac"), objective=args.objective, tran_heating=args.tran_heating, n_ctrl=args.n_ctrl, target_region=args.region, dz_max=args.dz_mm * 1e-3, max_slope=args.max_slope,
                     min_gap=args.min_gap_mm * 1e-3, pin_rim=not args.free_rim, smooth=args.smooth, max_eval=args.max_eval, target_rms=args.target_rms)
    t0 = time.time()

    def progress(d: dict) -> None:
        if args.progress == "jsonl":
            print(json.dumps({"t": round(time.time() - t0, 3), **d}, default=str), flush=True)
        elif args.progress == "text":
            print(", ".join(f"{k}={v}" for k, v in d.items()), file=sys.stderr, flush=True)

    try:
        cancel_path = Path(args.cancel_file) if args.cancel_file else None
        ctx = RunContext(project=base, backend=get_backend(base.numerics.ngspice), log=lambda m: None, progress=progress,
                         cancel=(lambda: cancel_path.exists()) if cancel_path else (lambda: False))
        res = optimize_shape(ctx, spec)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    out = Path(args.output)
    out.write_text(res.project.to_json(), encoding="utf-8")
    if args.history:
        Path(args.history).write_text(json.dumps(result_summary(spec, res), indent=2, default=str), encoding="utf-8")
    progress({"stage": "shape_done", "rms_start": res.start.rms, "rms": res.best.rms, "uniformity_start": res.start.uniformity,
              "uniformity": res.best.uniformity, "evaluations": res.evaluations, "jacobian_evaluations": res.jacobian_evaluations,
              "stop_reason": res.stop_reason, "active": res.active, "output": str(out), "cancelled": res.cancelled, "done": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
