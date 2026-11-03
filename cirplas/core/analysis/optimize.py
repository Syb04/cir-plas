"""Uniformity optimization (M6): automatic search of FR tuning parameters (coupling capacitance, ring thickness,
separate-FR source amplitude/phase, ...) for a flat sheath across the wafer edge or a uniform heating profile.

The search runs on the project JSON paths (as the sweeps do): a coarse grid scan over the box (log-spaced where
asked) followed by a bounded local refinement (Brent in 1D, Powell otherwise) from the best grid point. Dependent
values that must move with a parameter (e.g. the `z_offset` of a region whose stack thickness is optimized) are given
as linear ties `value = scale * x + offset`.
"""

from __future__ import annotations

import copy
import itertools
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from ..project import Project, apply_path
from .ac import run_ac
from .common import RunContext

OBJECTIVES = ("fr_step", "uniformity_se", "uniformity_vsh")


@dataclass
class OptParam:
    path: str
    lo: float
    hi: float
    log: bool = False

    def to_unit(self, x: float) -> float:
        if self.log:
            return (math.log(x) - math.log(self.lo)) / (math.log(self.hi) - math.log(self.lo))
        return (x - self.lo) / (self.hi - self.lo)

    def from_unit(self, u: float) -> float:
        u = min(max(u, 0.0), 1.0)
        if self.log:
            return math.exp(math.log(self.lo) + u * (math.log(self.hi) - math.log(self.lo)))
        return self.lo + u * (self.hi - self.lo)


@dataclass
class Tie:
    """Dependent value: path <- scale * x[of] + offset."""

    path: str
    of: int = 0
    scale: float = 1.0
    offset: float = 0.0


@dataclass
class OptimizeSpec:
    params: list[OptParam]
    objective: str = "fr_step"
    mode: str = "ac"
    ties: list[Tie] = field(default_factory=list)
    grid: int = 5  # points per parameter in the initial scan
    max_eval: int = 40
    fr_region: str = "fr"
    tol: float = 1e-3  # relative tolerance on the parameters (unit box)


@dataclass
class OptimizeResult:
    values: dict[str, float]
    objective: float
    project: Project
    history: list[dict]
    evaluations: int
    metrics: dict[str, float]
    cancelled: bool = False


class _Cancelled(Exception):
    pass


def apply_values(base: Project, spec: OptimizeSpec, x: list[float]) -> Project:
    d = copy.deepcopy(base.model_dump(mode="json"))
    for prm, v in zip(spec.params, x):
        apply_path(d, prm.path, float(v))
    for t in spec.ties:
        apply_path(d, t.path, float(t.scale * x[t.of] + t.offset))
    return Project.model_validate(d)


def profile_metrics(r: np.ndarray, regions: list[str], vsh: np.ndarray, s_e: np.ndarray, areas: np.ndarray, fr_region: str = "fr") -> dict[str, float]:
    """Scalar metrics of one solution: relative FR step of the sheath voltage at the wafer edge, and uniformities
    (max-min)/(2 mean) over the wafer (first region)."""
    wafer = [i for i, g in enumerate(regions) if g == regions[0]]
    fr = [i for i, g in enumerate(regions) if g == fr_region]

    def uni(y):
        y = np.asarray(y, dtype=float)[wafer]
        return float((y.max() - y.min()) / (2 * y.mean())) if y.mean() else float("nan")

    out = {"uniformity_se": uni(s_e), "uniformity_vsh": uni(vsh)}
    if fr:
        v_edge = float(vsh[wafer[-1]])
        v_fr = float(np.average(np.asarray(vsh)[fr], weights=np.asarray(areas)[fr]))
        out["fr_step_signed"] = (v_fr - v_edge) / v_edge
        out["fr_step"] = abs(out["fr_step_signed"])
    return out


def evaluate(ctx: RunContext, project: Project, mode: str, fr_region: str = "fr") -> dict[str, float]:
    """Run one solution (AC: |V_sh| amplitude; TRAN: DC sheath voltage; SC: density iteration + AC) and return its metrics."""
    sub = RunContext(project=project, backend=ctx.backend, log=lambda m: None, progress=lambda d: None, cancel=ctx.cancel)
    if mode == "sc":
        from .density import run_density_iteration

        ac = run_density_iteration(sub).ac
    else:
        ac = run_ac(sub)
    if mode == "tran":
        from .tran import run_tran

        tr = run_tran(sub, ac=ac)
        cells = tr.post.cells
        regions = [c.region for c in tr.model.mesh.cells]
        return profile_metrics(np.array([c.r_center for c in cells]), regions, tr.post.radial("vsh_b_dc"), tr.post.radial("s_e"),
                               tr.post.radial("area"), fr_region)
    regions = [c.region for c in ac.model.mesh.cells]
    return profile_metrics(ac.r, regions, np.abs(ac.radial("vsh_b")), ac.radial("s_e"), ac.radial("area"), fr_region)


def optimize(ctx: RunContext, spec: OptimizeSpec, on_eval: Optional[Callable[[dict], None]] = None) -> OptimizeResult:
    if spec.objective not in OBJECTIVES:
        raise ValueError(f"unknown objective '{spec.objective}' (choose from {', '.join(OBJECTIVES)})")
    if not spec.params:
        raise ValueError("optimization needs at least one parameter")
    base = ctx.project
    history: list[dict] = []
    cache: dict[tuple, float] = {}

    def f(u: np.ndarray) -> float:
        x = [prm.from_unit(float(ui)) for prm, ui in zip(spec.params, np.atleast_1d(u))]
        key = tuple(round(v, 12) for v in x)
        if key in cache:
            return cache[key]
        if len(history) >= spec.max_eval:
            return cache.get(key, float("inf"))
        try:
            m = evaluate(ctx, apply_values(base, spec, x), spec.mode, spec.fr_region)
            val = float(m.get(spec.objective, float("nan")))
            if not math.isfinite(val):
                val = float("inf")
        except Exception as exc:  # noqa: BLE001 - an infeasible point (e.g. invalid geometry) is just a bad point
            m, val = {"error": str(exc)}, float("inf")
        rec = {"values": {prm.path: v for prm, v in zip(spec.params, x)}, "objective": val, "metrics": m}
        history.append(rec)
        cache[key] = val
        ctx.progress({"stage": "optimize", "evaluation": len(history), "objective": val, "values": rec["values"],
                      "metrics": {k: v for k, v in m.items() if isinstance(v, (int, float))}, "error": m.get("error", "")})
        ctx.log(f"optimize eval {len(history)}: {spec.objective}={val:.4g} at {rec['values']}")
        if on_eval:
            on_eval(rec)
        if ctx.cancel():
            raise _Cancelled
        return val

    n = len(spec.params)
    grid = np.linspace(0.0, 1.0, max(spec.grid, 2))
    cancelled = False
    try:
        best_u, best_v = None, float("inf")
        for u in itertools.product(grid, repeat=n):
            v = f(np.array(u))
            if v < best_v:
                best_u, best_v = np.array(u), v
        if best_u is None or not math.isfinite(best_v):
            raise RuntimeError("no feasible point in the parameter box")
        h = 1.0 / (len(grid) - 1)
        if n == 1:
            lo, hi = max(0.0, best_u[0] - h), min(1.0, best_u[0] + h)
            minimize_scalar(lambda u: f(np.array([u])), bounds=(lo, hi), method="bounded", options={"xatol": spec.tol, "maxiter": spec.max_eval})
        else:
            minimize(f, best_u, method="Powell", bounds=[(0.0, 1.0)] * n, options={"xtol": spec.tol, "maxfev": spec.max_eval})
    except _Cancelled:
        cancelled = True  # report the best point evaluated so far
    feasible = [r for r in history if math.isfinite(r["objective"])]
    if not feasible:
        raise RuntimeError("cancelled before a feasible point was evaluated" if cancelled else "no feasible point in the parameter box")
    best = min(feasible, key=lambda r: r["objective"])
    x = [best["values"][prm.path] for prm in spec.params]
    return OptimizeResult(values=best["values"], objective=best["objective"], project=apply_values(base, spec, x), history=history,
                          evaluations=len(history), metrics=best["metrics"], cancelled=cancelled)
