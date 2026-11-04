"""Top-electrode shape optimization: find the height profile of the top stack's plasma-facing surface that flattens the
electron heating S_e(r), or the self-consistent electron density n(r), over the target region (spec 02 section 3.9).

Shape.  The displacement from the starting shape is a clamped cubic B-spline over [0, r_wall]. The control coefficients are
the unknowns, because a B-spline lies in the convex hull of its coefficients and its derivative is a B-spline of their
scaled differences: displacement, plasma-gap and slope limits are then exact linear constraints, and they hold everywhere
along the electrode, not only at sample points. The slope on the axis is zero (axisymmetry); the rim can stay at its height.

Objective.  r_i = sqrt(w_i) (y_i / mean - 1) on the target cells, y being S_e or n, with w_i proportional to the cell width,
so that every millimeter of radius counts the same on the graded mesh; ||r|| is the relative RMS of y. A curvature term keeps
the shape smooth. The density only responds to the shape in the self-consistent solution, so objective "density" needs mode
"sc". Use it with `tran_heating`: without the harmonics of the transient the heating, and with it the best shape, can even
have the opposite sign.

Search.  Constrained Gauss-Newton with Levenberg damping: each step is a small convex problem solved without the circuit
solver. The Jacobian comes from finite differences of a cheaper model where the requested one is too slow: mode "tran"
measures the residual with the transient but takes d ln S_e / dx from the AC solution. The two models disagree on S_e itself
(the sheath harmonics only exist in the transient) but respond similarly to the gap, so the search still converges with one
transient per step, and it converges to a flat *transient* profile. Mode "ac" and "sc" use their own model for both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import minimize

from ..project import Project
from .common import RunContext

MODES = ("ac", "sc", "tran")
OBJECTIVES = ("s_e", "density")
MM = 1e-3  # the unknowns are in mm: well scaled against the O(1) residuals


@dataclass
class ShapeSpec:
    mode: str = "ac"
    objective: str = "s_e"  # the profile to flatten: "s_e" or "density" (mode "sc" only)
    tran_heating: Optional[bool] = None  # mode "sc": correct the density with the TRAN heating (None: the project's refine_with_tran)
    n_ctrl: int = 6  # free control coefficients of the spline
    target_region: Optional[str] = None  # flatten the profile over this region (default: the first one)
    dz_max: float = 10e-3  # largest displacement from the starting shape, either way [m]
    max_slope: float = 0.2  # |dz/dr| of the whole electrode surface
    min_gap: float = 15e-3  # smallest distance between the electrode surface and the lower surfaces [m]
    pin_rim: bool = True  # the electrode stays at its height at the outer wall
    smooth: float = 1e-3  # weight of the curvature term
    max_eval: int = 12  # evaluations of the requested model, Jacobians not counted
    target_rms: float = 0.005  # stop when the relative RMS of the profile is below this
    tol: float = 0.01  # stop when a step improves the objective by less than this fraction
    fd_step: float = 1e-3  # finite-difference step [m]
    table_dr: float = 2.5e-3  # sampling of the resulting gap table [m]


@dataclass
class Profile:
    """The profiles of one solution, with what the search and the reports need; `quantity` is the one being flattened."""

    r: np.ndarray
    s_e: np.ndarray
    weights: np.ndarray  # zero outside the target region, sum = 1
    power: float
    density: Optional[np.ndarray] = None  # self-consistent density (mode "sc"): warm start of the next solve
    quantity: str = "s_e"

    @property
    def values(self) -> np.ndarray:
        return self.density if self.quantity == "density" else self.s_e

    @property
    def mean(self) -> float:
        return float(np.sum(self.weights * self.values))

    @property
    def s_e_mean(self) -> float:
        return float(np.sum(self.weights * self.s_e))

    @property
    def residual(self) -> np.ndarray:
        m = self.weights > 0
        return np.sqrt(self.weights[m]) * (self.values[m] / self.mean - 1.0)

    @property
    def rms(self) -> float:
        return float(np.linalg.norm(self.residual))

    @property
    def uniformity(self) -> float:
        y = self.values[self.weights > 0]
        return float((y.max() - y.min()) / (2.0 * self.mean))


@dataclass
class ShapeResult:
    project: Project  # the starting project with the optimized gap table, nothing else changed
    start: Profile
    best: Profile
    r_shape: np.ndarray  # radii of the gap table [m]
    gap_start: np.ndarray  # height of the electrode surface in gap-table units [m]
    gap_best: np.ndarray
    history: list[dict]
    evaluations: int  # of the requested model
    jacobian_evaluations: int
    stop_reason: str
    active: list[str] = field(default_factory=list)  # limits the best shape runs into
    cancelled: bool = False


class _Stop(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class ShapeBasis:
    """Displacement d(r) = sum_i c_i B_i(r), c = T x, with the limits of `spec` as linear constraints on x [mm]."""

    def __init__(self, project: Project, spec: ShapeSpec):
        if spec.n_ctrl < 3:
            raise ValueError("the shape needs at least three control coefficients")
        geo = project.geometry
        self.geometry = geo
        self.r_wall = geo.r_wall
        self.r_target = _target_bounds(project, spec)[1]
        # coefficients: free ones + the coefficient tied to c_0 (zero slope on the axis) + the pinned rim
        n_coef = spec.n_ctrl + 1 + (1 if spec.pin_rim else 0)
        self.t = _clamped_knots(n_coef, self.r_target, self.r_wall)
        self.n_coef = n_coef
        self.n = spec.n_ctrl
        tie = np.zeros((n_coef, self.n))
        tie[0, 0] = 1.0
        tie[1:1 + self.n, :] = np.eye(self.n)
        self.tie = tie  # rows beyond the free ones stay zero: the pinned rim
        self._limits(spec)

    def displacement(self, x_mm: np.ndarray, r: np.ndarray) -> np.ndarray:
        return BSpline(self.t, self.tie @ np.asarray(x_mm, dtype=float) * MM, 3, extrapolate=False)(np.clip(r, 0.0, self.r_wall))

    def base_gap(self, r: np.ndarray) -> np.ndarray:
        return np.array([self.geometry.gap_at(float(ri)) for ri in r])

    def _limits(self, spec: ShapeSpec) -> None:
        geo, t, n_coef = self.geometry, self.t, self.n_coef
        grid = np.linspace(0.0, self.r_wall, 2001)
        base = self.base_gap(grid)
        base_slope = np.gradient(base, grid)
        # height of the lower surfaces under each radius, in gap-table units
        datum = 0.0 if geo.gap.datum == "substrate" else geo.reference_region().z_surface_abs
        surface = np.empty_like(grid)
        r_in = 0.0
        for reg in geo.regions:
            surface[(grid >= r_in) & (grid <= reg.r_out)] = reg.z_surface_abs - datum
            r_in = reg.r_out
        # floor of the displacement under each coefficient, over the support of its basis function
        lo_c = np.full(n_coef, -spec.dz_max)
        floor = spec.min_gap + surface - base
        for i in range(n_coef):
            s = (grid >= t[i]) & (grid <= t[i + 4])
            lo_c[i] = max(lo_c[i], float(floor[s].max()))
        lo_c = np.minimum(lo_c, 0.0)  # the starting shape stays feasible even when it breaks a limit
        # the unknowns x_k are coefficients k+1 (x_0 is also c_0)
        self.lo = np.array([max(lo_c[k + 1], lo_c[0] if k == 0 else -math.inf) for k in range(self.n)]) / MM
        self.hi = np.full(self.n, spec.dz_max / MM)
        # slope: the derivative's coefficients are 3 (c_{i+1} - c_i) / (t_{i+4} - t_{i+1})
        rows, ub = [], []
        for i in range(n_coef - 1):
            a = np.zeros(n_coef)
            scale = 3.0 / (t[i + 4] - t[i + 1])
            a[i], a[i + 1] = -scale, scale
            row = a @ self.tie * MM
            if not row.any():
                continue
            s = (grid >= t[i + 1]) & (grid <= t[i + 4])
            rows += [row, -row]
            ub += [max(spec.max_slope - float(base_slope[s].max()), 0.0), max(spec.max_slope + float(base_slope[s].min()), 0.0)]
        self.a_slope = np.array(rows) if rows else np.zeros((0, self.n))
        self.b_slope = np.array(ub)
        # curvature: second differences of the coefficients, relative to the allowed displacement
        c_mm = self.tie
        self.curvature = (c_mm[2:] - 2.0 * c_mm[1:-1] + c_mm[:-2]) / (spec.dz_max / MM) * math.sqrt(spec.smooth)

    def active_limits(self, x: np.ndarray) -> list[str]:
        out = []
        if np.any(x >= self.hi - 1e-3):
            out.append("変位の上限")
        if np.any(x <= self.lo + 1e-3):
            out.append("変位の下限・最小ギャップ")
        if len(self.b_slope) and np.any(self.a_slope @ x >= self.b_slope - 1e-4):
            out.append("傾斜の上限")
        return out


def _clamped_knots(n_coef: int, r_target: float, r_wall: float) -> np.ndarray:
    """Knot vector with its interior breakpoints evenly spread over the target region, none beyond it."""
    n_int = n_coef - 4
    if r_wall > r_target * (1 + 1e-9):
        inner = np.linspace(0.0, r_target, n_int + 1)[1:]
    else:
        inner = np.linspace(0.0, r_wall, n_int + 2)[1:-1]
    return np.concatenate([[0.0] * 4, inner, [r_wall] * 4])


def _target_bounds(project: Project, spec: ShapeSpec) -> tuple[float, float]:
    geo = project.geometry
    name = spec.target_region or geo.regions[0].name
    for reg, (r0, r1) in zip(geo.regions, geo.region_bounds()):
        if reg.name == name:
            return r0, r1
    raise ValueError(f"unknown target region '{name}'")


def gap_table(basis: ShapeBasis, x_mm: np.ndarray, spec: ShapeSpec) -> tuple[np.ndarray, np.ndarray]:
    """Radii and heights of the electrode surface in the units of `geometry.gap.table`, including the points of a starting table."""
    n = max(int(round(basis.r_wall / spec.table_dr)), 2)
    r = np.linspace(0.0, basis.r_wall, n + 1)
    if basis.geometry.gap.type == "table":
        r = np.unique(np.concatenate([r, [p[0] for p in basis.geometry.gap.table if p[0] <= basis.r_wall]]))
    return r, basis.base_gap(r) + basis.displacement(x_mm, r)


def with_shape(project: Project, r: np.ndarray, gap: np.ndarray) -> Project:
    d = project.model_dump(mode="json")
    radii = [round(float(a), 7) for a in r[:-1]] + [float(r[-1])]  # the last point must reach the wall exactly
    d["geometry"]["gap"].update(type="table", table=[(a, round(float(g), 7)) for a, g in zip(radii, gap)])
    return Project.model_validate(d)


def evaluate_profile(ctx: RunContext, project: Project, mode: str, spec: ShapeSpec, warm_density: Optional[np.ndarray] = None,
                     refine: Optional[bool] = None) -> Profile:
    """One solution of `project`: power-specified sources are honored, and mode "sc" adds the TRAN correction if `refine`
    (None: as `refine_with_tran` of the project says)."""
    from .density import _project_with_density, refine_with_tran, run_density_iteration
    from .power_drive import run_power_drive

    sub = RunContext(project=project, backend=ctx.backend, log=lambda m: None, progress=lambda d: None, cancel=ctx.cancel)
    density = None
    if mode == "sc":
        if warm_density is not None:
            from ..mesh import build_radial_mesh

            r = build_radial_mesh(project.geometry, project.numerics.mesh).r_centers
            sub.project = _project_with_density(project, r, warm_density)
        sc = run_density_iteration(sub)
        density = sc.n.copy()
        ac, tran = sc.ac, None
        if project.numerics.density_iteration.refine_with_tran if refine is None else refine:
            final = Project.model_validate_json(sc.notes["final_project"])
            _, ac, tran = refine_with_tran(RunContext(project=final, backend=ctx.backend, log=lambda m: None, progress=lambda d: None, cancel=ctx.cancel), sc)
            density = sc.n.copy()
    else:
        _, ac, tran, _ = run_power_drive(sub, mode=mode)
    src, cells = (tran.post, tran.model.mesh.cells) if tran is not None else (ac, ac.model.mesh.cells)
    r0, r1 = _target_bounds(project, spec)
    width = np.array([c.r_out - c.r_in for c in cells])
    inside = np.array([r0 <= c.r_center <= r1 for c in cells])
    w = np.where(inside, width, 0.0)
    ports = tran.post.ports if tran is not None else ac.ports
    return Profile(r=np.array([c.r_center for c in cells]), s_e=np.asarray(src.radial("s_e"), dtype=float), weights=w / w.sum(),
                   power=float(sum(p.p for p in ports)), density=density, quantity=spec.objective)


def _step(x: np.ndarray, res: np.ndarray, jac: np.ndarray, basis: ShapeBasis, mu: float) -> np.ndarray:
    """Damped Gauss-Newton step under the limits: a small convex quadratic problem, no circuit solves."""
    curv = basis.curvature
    hess = jac.T @ jac + curv.T @ curv + mu * np.eye(len(x))
    grad = jac.T @ res + curv.T @ (curv @ x)
    cons = []
    if len(basis.b_slope):
        cons = [{"type": "ineq", "fun": lambda d: basis.b_slope - basis.a_slope @ (x + d), "jac": lambda d: -basis.a_slope}]
    sol = minimize(lambda d: 0.5 * d @ hess @ d + grad @ d, np.zeros_like(x), jac=lambda d: hess @ d + grad, method="SLSQP",
                   bounds=list(zip(basis.lo - x, basis.hi - x)), constraints=cons, options={"maxiter": 300, "ftol": 1e-12})
    return np.clip(sol.x, basis.lo - x, basis.hi - x)


def optimize_shape(ctx: RunContext, spec: ShapeSpec, on_eval: Optional[Callable[[dict], None]] = None) -> ShapeResult:
    if spec.mode not in MODES:
        raise ValueError(f"unknown mode '{spec.mode}' (choose from {', '.join(MODES)})")
    if spec.objective not in OBJECTIVES:
        raise ValueError(f"unknown objective '{spec.objective}' (choose from {', '.join(OBJECTIVES)})")
    if spec.objective == "density" and spec.mode != "sc":
        raise ValueError("objective 'density' needs mode 'sc': the density is an input of the other modes")
    base = ctx.project
    basis = ShapeBasis(base, spec)
    history: list[dict] = []
    n_jac = 0
    # a transient is too slow for finite differences: AC stands in for "tran", the AC-based iteration for a refined "sc"
    jac_mode = "ac" if spec.mode == "tran" else spec.mode
    refined = base.numerics.density_iteration.refine_with_tran if spec.tran_heating is None else spec.tran_heating
    own_jacobian = spec.mode == "ac" or (spec.mode == "sc" and not refined)

    def cost_of(x: np.ndarray, prof: Profile) -> float:
        return float(prof.rms ** 2 + np.sum((basis.curvature @ x) ** 2))

    def shaped(x: np.ndarray) -> Project:
        return with_shape(base, *gap_table(basis, x, spec))

    def fine(x: np.ndarray, warm: Optional[np.ndarray]) -> tuple[Optional[Profile], float]:
        """Evaluate the requested model; an infeasible point is just a bad point."""
        if len(history) >= spec.max_eval:
            raise _Stop("評価回数の上限")
        err = ""
        try:
            prof = evaluate_profile(ctx, shaped(x), spec.mode, spec, warm, refine=refined)
            cost = cost_of(x, prof)
            if not math.isfinite(cost):
                prof, cost, err = None, math.inf, "分布が有限でない"
        except Exception as exc:  # noqa: BLE001
            prof, cost, err = None, math.inf, str(exc)
        rec = {"x_mm": [float(v) for v in x], "cost": cost, "rms": prof.rms if prof else math.inf, "uniformity": prof.uniformity if prof else math.inf,
               "mean": prof.mean if prof else math.nan, "s_e_mean": prof.s_e_mean if prof else math.nan,
               "power": prof.power if prof else math.nan, "error": err}
        history.append(rec)
        ctx.progress({"stage": "shape", "evaluation": len(history), **rec})
        ctx.log(f"shape eval {len(history)}: rms={rec['rms']:.4g} uniformity={rec['uniformity']:.4g} x={np.round(x, 3)} mm")
        if on_eval:
            on_eval(rec)
        if ctx.cancel():
            best[2] = True
            raise _Stop("中止")
        return prof, cost

    def jacobian(x: np.ndarray, prof: Profile) -> np.ndarray:
        """d r / d x of the normalized profile `prof`, with d ln y / d x from `jac_mode`."""
        def solve(q: np.ndarray, warm: Optional[np.ndarray]) -> Profile:
            nonlocal n_jac
            n_jac += 1
            try:
                return evaluate_profile(ctx, shaped(q), jac_mode, spec, warm, refine=False)
            finally:
                if ctx.cancel():
                    best[2] = True
                    raise _Stop("中止")

        ref = prof if own_jacobian else solve(x, prof.density)
        m = prof.weights > 0
        dlog = np.zeros((int(m.sum()), len(x)))
        h = spec.fd_step / MM
        for k in range(len(x)):
            # one-sided, inward at a limit: the Jacobian must stay evaluable on the boundary of the box
            sign = 1.0 if x[k] + h <= basis.hi[k] else -1.0
            xk = x.copy()
            xk[k] += sign * h
            try:
                pert = solve(xk, ref.density)
            except _Stop:
                raise
            except Exception:  # noqa: BLE001 - next to an infeasible region: difference away from it
                sign = -sign
                xk[k] = x[k] + sign * h
                pert = solve(xk, ref.density)
            dlog[:, k] = (np.log(pert.values[m]) - np.log(ref.values[m])) / (sign * h)
            ctx.progress({"stage": "shape_jacobian", "done": k + 1, "total": len(x)})
        w, ratio = prof.weights[m], prof.values[m] / prof.mean
        return (np.sqrt(w) * ratio)[:, None] * (dlog - (w * ratio) @ dlog)

    x = np.zeros(basis.n)
    best: list = [x, None, False]  # shape, profile, cancelled
    start, cost = fine(x, None)
    if start is None:
        raise RuntimeError(f"the starting shape cannot be solved: {history[-1]['error']}")
    best[1] = start
    prof = start
    mu = None
    reason = ""
    try:
        while True:
            if prof.rms <= spec.target_rms:
                reason = "目標の均一性に到達"
                break
            jac = jacobian(x, prof)
            if mu is None:
                mu = 1e-3 * float(np.trace(jac.T @ jac)) / len(x)
            rejected = None
            for _ in range(6):
                d = _step(x, prof.residual, jac, basis, mu)
                if float(np.max(np.abs(d))) < 1e-3:  # 1 um: nothing left to move within the limits
                    reason = "制約内で収束"
                    break
                # against a limit more damping can leave the step where it was: do not pay for the same point twice
                if rejected is not None and float(np.max(np.abs(d - rejected))) < 0.01:
                    reason = "これ以上改善する方向がない"
                    break
                new, new_cost = fine(x + d, prof.density)
                if new is not None and new_cost < cost:
                    gain = (cost - new_cost) / cost
                    x, prof, cost = x + d, new, new_cost
                    best[0], best[1] = x, prof
                    mu *= 0.3
                    if gain < spec.tol:
                        reason = "改善が収束"
                    break
                rejected = d
                mu *= 6.0
            else:
                reason = "これ以上改善する方向がない"
            if reason:
                break
    except _Stop as stop:
        reason = stop.reason
    except Exception as exc:  # noqa: BLE001 - a long run keeps what it found when a finite-difference solve fails
        reason = f"ヤコビアンの計算に失敗: {exc}"
    x_best, prof_best, cancelled = best
    r_shape, gap_best = gap_table(basis, x_best, spec)
    return ShapeResult(project=with_shape(base, r_shape, gap_best), start=start, best=prof_best, r_shape=r_shape, gap_start=basis.base_gap(r_shape),
                       gap_best=gap_best, history=history, evaluations=len(history), jacobian_evaluations=n_jac, stop_reason=reason,
                       active=basis.active_limits(x_best), cancelled=cancelled)
