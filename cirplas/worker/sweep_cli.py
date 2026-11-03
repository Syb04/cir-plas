"""cirplas-sweep: run a parameter sweep with parallel worker processes.

usage: cirplas-sweep base.cirplas.json --sweep sweep.json -o outdir [--jobs N] [--progress jsonl]
   or: cirplas-sweep base.cirplas.json --param circuit.ports.W.sources.0.v --values 200,300,400 --mode ac -o outdir
       (repeat --param/--values for a 2D sweep over the Cartesian product)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..core.analysis.sweep import SweepAxis, SweepSpec, expand, summary_metrics
from ..core.project import load_project


def _run_job(args):
    job_dir, proj_path, out_path, mode, cancel_file = args
    worker = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "cirplas.worker.cli"]
    cmd = worker + [str(proj_path), "-o", str(out_path), "--mode", mode, "--progress", "none"]
    if cancel_file:
        cmd += ["--cancel-file", str(cancel_file)]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {"dir": str(job_dir), "rc": proc.returncode, "seconds": time.time() - t0, "stderr": proc.stderr[-2000:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cirplas-sweep")
    ap.add_argument("project")
    ap.add_argument("--sweep", help="sweep spec JSON")
    ap.add_argument("--param", action="append", help="parameter path (repeat for a second axis)")
    ap.add_argument("--values", action="append", help="comma separated values for the matching --param")
    ap.add_argument("--mode", default="ac", choices=["ac", "tran", "sc"])
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--progress", choices=["jsonl", "text", "none"], default="text")
    ap.add_argument("--cancel-file", default=None)
    args = ap.parse_args(argv)

    base = load_project(args.project)
    if args.sweep:
        spec = SweepSpec.from_json(Path(args.sweep).read_text(encoding="utf-8"))
    elif args.param and args.values:
        if len(args.param) != len(args.values):
            ap.error("every --param needs one --values")
        axes = []
        for path, text in zip(args.param, args.values):
            vals = []
            for v in text.split(","):
                try:
                    vals.append(float(v))
                except ValueError:
                    vals.append(v)
            axes.append(SweepAxis(path=path, values=vals, label=path))
        spec = SweepSpec(mode=args.mode, axes=axes)
    else:
        ap.error("either --sweep or --param/--values is required")
    if args.sweep is None:
        spec.mode = args.mode
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = expand(base, spec)
    (outdir / "sweep.json").write_text(spec.to_json(), encoding="utf-8")
    t0 = time.time()

    def emit(d: dict) -> None:
        if args.progress == "jsonl":
            print(json.dumps({"t": round(time.time() - t0, 3), **d}, default=str), flush=True)
        elif args.progress == "text":
            print(", ".join(f"{k}={v}" for k, v in d.items()), file=sys.stderr, flush=True)

    emit({"stage": "sweep_start", "jobs": len(jobs), "parallel": args.jobs, "mode": spec.mode})
    tasks = []
    for job in jobs:
        jd = outdir / f"{job.index:03d}_{job.name}"
        jd.mkdir(parents=True, exist_ok=True)
        pp = jd / "project.cirplas.json"
        pp.write_text(job.project.to_json(), encoding="utf-8")
        tasks.append((jd, pp, jd / "run.h5", spec.mode, args.cancel_file))
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(_run_job, t): (job, t) for job, t in zip(jobs, tasks)}
        for fut in cf.as_completed(futs):
            job, t = futs[fut]
            r = fut.result()
            ok = r["rc"] == 0 and Path(t[2]).exists()
            metrics = summary_metrics(t[2]) if ok else {}
            results.append({"index": job.index, "indices": list(job.indices), "values": job.values, "name": job.name,
                            "ok": ok, "seconds": r["seconds"], "output": str(t[2]), "metrics": metrics, "error": "" if ok else r["stderr"][-500:]})
            emit({"stage": "sweep_job", "index": job.index, "ok": ok, "seconds": round(r["seconds"], 1), "values": job.values,
                  "done": len(results), "total": len(jobs)})
    results.sort(key=lambda r: r["index"])
    (outdir / "results.json").write_text(json.dumps({"spec": json.loads(spec.to_json()), "runs": results}, indent=2, default=str), encoding="utf-8")
    emit({"stage": "sweep_done", "ok": sum(r["ok"] for r in results), "total": len(results), "output": str(outdir / "results.json"), "done": True})
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
