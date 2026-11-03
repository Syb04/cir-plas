"""Level 1 self-consistent electron density (spec 01 section 8.2, 02 section 6).

Electron power balance with radial ambipolar diffusion on the radial cell centers:

    -(1/r) d/dr ( r D_a dn/dr ) + (2 h_l u_B / l_p) n = S_e(r) / (l_p e eps_T)

with eps_T = eps_c(Te) + 2 Te [eV], dn/dr = 0 on the axis and the Bohm-flux (Robin) condition
-D_a dn/dr = h_R u_B n at the outer wall. The electron heating S_e(r) [W/m^2] comes from the
quasi-linear AC solution (fast inner loop); a final TRAN run can be used to report the
nonlinear heating with the converged density.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.linalg import solve_banded

from ..constants import E_CHARGE
from ..plasma import PlasmaState
from ..project import DensitySpec, Project
from .ac import AcResult, run_ac
from .common import Estimates, RunContext, initial_estimates
from .tran import TranResult, run_tran


@dataclass
class DensityIterationResult:
    r_centers: np.ndarray
    n_history: list[np.ndarray]
    te_history: list[float]
    residuals: list[float]
    relax_history: list[float]
    converged: bool
    iterations: int
    ac: Optional[AcResult] = None
    notes: dict = field(default_factory=dict)

    @property
    def n(self) -> np.ndarray:
        return self.n_history[-1]


def ambipolar_diffusion(plasma: PlasmaState) -> float:
    """D_a ~ mu_i Te with mu_i = e/(M nu_in), nu_in ~ u_B / lambda_i  [m^2/s]."""
    nu_in = plasma.u_b / plasma.lambda_i
    mu_i = E_CHARGE / (plasma.gas.mass_kg * nu_in)
    return mu_i * plasma.te


def radial_power_balance(r_edges: np.ndarray, s_e: np.ndarray, l_p: np.ndarray, plasma: PlasmaState, d_a: Optional[float] = None) -> np.ndarray:
    """Solve the steady radial balance (finite volumes on the radial cells) for the electron density [m^-3]."""
    r_edges = np.asarray(r_edges, dtype=float)
    rc = 0.5 * (r_edges[:-1] + r_edges[1:])
    n = len(rc)
    if d_a is None:
        d_a = ambipolar_diffusion(plasma)
    eps_t = plasma.gas.eps_c(plasma.te) + 2.0 * plasma.te
    source = np.asarray(s_e, dtype=float) / (np.asarray(l_p, dtype=float) * E_CHARGE * eps_t)  # m^-3 s^-1
    nu_loss = 2.0 * plasma.h_l * plasma.u_b / np.asarray(l_p, dtype=float)
    vol = np.pi * (r_edges[1:] ** 2 - r_edges[:-1] ** 2)  # per unit height
    # flux coefficients across interior faces: F = -D 2 pi r_face (n_{i+1} - n_i)/dr
    main = nu_loss * vol
    lower = np.zeros(n)
    upper = np.zeros(n)
    for i in range(n - 1):
        r_face = r_edges[i + 1]
        coef = d_a * 2.0 * np.pi * r_face / (rc[i + 1] - rc[i])
        main[i] += coef
        main[i + 1] += coef
        upper[i] = -coef
        lower[i + 1] = -coef
    # outer wall: Bohm flux h_R u_B n at r = R (extrapolate the wall density from the last cell)
    main[-1] += plasma.h_r * plasma.u_b * 2.0 * np.pi * r_edges[-1]
    rhs = source * vol
    ab = np.zeros((3, n))
    ab[0, 1:] = upper[:-1]
    ab[1, :] = main
    ab[2, :-1] = lower[1:]
    n_hat = solve_banded((1, 1), ab, rhs)
    return np.maximum(n_hat, 1e-3 * np.max(n_hat))


def _project_with_density(project: Project, r: np.ndarray, n: np.ndarray) -> Project:
    d = project.model_dump()
    d["plasma"]["density"] = {"model": "table", "n0": float(n[0]), "alpha": 0.0,
                              "table": [(float(ri), float(ni)) for ri, ni in zip(r, n)], "path": None}
    return Project.model_validate(d)


def _sub_context(ctx: RunContext, project: Project, quiet: bool = True) -> RunContext:
    return RunContext(project=project, backend=ctx.backend, log=ctx.log, progress=(lambda d: None) if quiet else ctx.progress, cancel=ctx.cancel)


def _adapt_relax(alpha: float, delta: np.ndarray, prev_delta: Optional[np.ndarray]) -> float:
    """Oscillation -> damp, monotone -> accelerate (bounded)."""
    if prev_delta is None:
        return alpha
    if np.dot(delta, prev_delta) < 0:
        return max(0.1, 0.5 * alpha)
    return min(0.8, 1.3 * alpha)


def run_density_iteration(ctx: RunContext, est: Optional[Estimates] = None) -> DensityIterationResult:
    """Level 1 iteration with the quasi-linear AC solution as the inner circuit solve.

    Sources with a power target (`power`) are rescaled after every AC solve (V <- V sqrt(P_tgt/P_AC)), so the
    converged density belongs to the specified delivered power rather than to the initial amplitude."""
    from ..mesh import build_radial_mesh
    from ..plasma import density_profile
    from .power_drive import power_targets, with_amplitudes

    p = ctx.project
    spec = p.numerics.density_iteration
    rm = build_radial_mesh(p.geometry, p.numerics.mesh)
    r = rm.r_centers
    if est is None:
        est = initial_estimates(p, rm.n_cells)
    n = density_profile(p.plasma.density, r, p.geometry.r_wall)
    targets = power_targets(p)
    amps = {port: float(p.circuit.ports[port].sources[0].v) for port in targets}
    p_tol = max(2.0 * spec.tol, 2e-3)
    alpha = spec.relax
    history = [n.copy()]
    te_hist: list[float] = []
    residuals: list[float] = []
    relax_hist: list[float] = []
    stages: list[str] = []
    power_hist: list[dict] = []
    converged = False
    ac = None
    prev_delta = None
    for k in range(1, spec.max_iter + 1):
        proj_k = _project_with_density(with_amplitudes(p, amps), r, n)
        ac = run_ac(_sub_context(ctx, proj_k), est=est, max_iter=6)
        est = ac.estimates
        plasma = ac.model.plasma
        s_e = ac.radial("s_e")
        p_err = 0.0
        if targets:
            powers = {pr.port: pr.p for pr in ac.ports if pr.port in targets}
            p_err = max(abs(powers[q] - targets[q]) / targets[q] for q in targets)
            power_hist.append({"amplitudes": dict(amps), "powers": powers})
            # the heating scales with the delivered power (P ~ V^2 for the quasi-linear solve):
            # correct the amplitudes and use the heating at the target power for this update
            scale = {q: targets[q] / powers[q] for q in targets if powers[q] > 0}
            for q, f in scale.items():
                amps[q] *= float(np.sqrt(f))
            if scale:
                s_e = s_e * float(np.mean(list(scale.values())))
        l_p = np.array([c.l_p for c in ac.model.mesh.cells])
        n_hat = radial_power_balance(rm.r_edges, s_e, l_p, plasma)
        delta = n_hat - n
        res = float(np.max(np.abs(delta) / np.maximum(n, 1e-30)))
        alpha = _adapt_relax(alpha, delta, prev_delta)
        prev_delta = delta
        n = (1.0 - alpha) * n + alpha * n_hat
        n = np.maximum(n, 1e-3 * n.max())
        history.append(n.copy())
        te_hist.append(plasma.te)
        residuals.append(res)
        relax_hist.append(alpha)
        stages.append("ac")
        ctx.progress({"stage": "density", "iteration": k, "residual": res, "relax": alpha, "te": plasma.te,
                      "n_center": float(n[0]), "n_mean": float(np.mean(n)), **({"power_error": p_err} if targets else {})})
        ctx.log(f"density iteration {k}: residual={res:.3e} alpha={alpha:.2f} Te={plasma.te:.3f} n0={n[0]:.3e}"
                + (f" power error={p_err:.2%} V={ {q: round(v, 2) for q, v in amps.items()} }" if targets else ""))
        # the amplitude correction of this pass also moves the density: require a small power error too, so that the
        # final state (corrected amplitudes, relaxed density) delivers the target within ~p_tol
        if res < spec.tol and p_err < p_tol:
            converged = True
            break
        if ctx.cancel():
            break
    # final AC with the converged profile (and amplitudes) for consistent reporting
    proj_f = _project_with_density(with_amplitudes(p, amps), r, n)
    ac = run_ac(_sub_context(ctx, proj_f), est=est, max_iter=6)
    if targets:
        final = {pr.port: pr.p for pr in ac.ports if pr.port in targets}
        power_hist.append({"amplitudes": dict(amps), "powers": final, "final": True})
        ctx.log("density iteration: final power error " + ", ".join(f"{q}={(final[q] - targets[q]) / targets[q]:+.2%}" for q in targets))
    return DensityIterationResult(r_centers=r, n_history=history, te_history=te_hist, residuals=residuals, relax_history=relax_hist,
                                  converged=converged, iterations=len(residuals), ac=ac,
                                  notes={"d_a": ambipolar_diffusion(ac.model.plasma), "final_project": proj_f.to_json(),
                                         "stages": stages, "power_history": power_hist, "amplitudes": dict(amps)})


def refine_with_tran(ctx: RunContext, density: DensityIterationResult) -> tuple[Project, AcResult, TranResult]:
    """Confirm the self-consistent state with the nonlinear TRAN solution (spec 02 section 6).

    `numerics.density_iteration.tran_refine_iter` relaxation steps use the TRAN heating profile (which includes
    the nonlinear stochastic heating) instead of the quasi-linear AC one; every step re-runs the transient
    (with the power-specified drive when power targets are set). The steps are appended to `density`."""
    from ..mesh import build_radial_mesh
    from .power_drive import power_targets, run_power_drive

    proj = Project.model_validate_json(density.notes["final_project"])
    spec = proj.numerics.density_iteration
    rm = build_radial_mesh(proj.geometry, proj.numerics.mesh)
    r = density.r_centers
    n = density.n.copy()
    alpha = spec.relax
    ac = tran = None
    stages = density.notes.setdefault("stages", ["ac"] * len(density.residuals))
    for k in range(spec.tran_refine_iter + 1):
        ctx_k = _sub_context(ctx, proj, quiet=False)
        if power_targets(proj):
            proj, ac, tran, pd = run_power_drive(ctx_k, mode="tran")
            ctx.log(f"power drive {'converged' if pd.converged else 'NOT converged'}: amplitudes={pd.amplitudes}")
        else:
            ac = run_ac(ctx_k)
            tran = run_tran(ctx_k, ac=ac)
        if k == spec.tran_refine_iter or ctx.cancel():
            break
        plasma = tran.model.plasma
        s_e = tran.post.radial("s_e")
        l_p = np.array([c.l_p for c in tran.model.mesh.cells])
        n_hat = radial_power_balance(rm.r_edges, s_e, l_p, plasma)
        res = float(np.max(np.abs(n_hat - n) / np.maximum(n, 1e-30)))
        n = (1.0 - alpha) * n + alpha * n_hat
        n = np.maximum(n, 1e-3 * n.max())
        density.n_history.append(n.copy())
        density.te_history.append(plasma.te)
        density.residuals.append(res)
        density.relax_history.append(alpha)
        stages.append("tran")
        ctx.progress({"stage": "density", "iteration": len(density.residuals), "residual": res, "relax": alpha, "te": plasma.te,
                      "n_center": float(n[0]), "n_mean": float(np.mean(n)), "tran": True})
        ctx.log(f"density TRAN refinement {k + 1}: residual={res:.3e} n0={n[0]:.3e}")
        proj = _project_with_density(proj, r, n)
    density.notes["final_project"] = proj.to_json()
    return proj, ac, tran


def global_model_density(p_abs: float, plasma: PlasmaState, radius: float, l_p: float) -> float:
    """Lieberman global model (electron power balance, L&L 10.2): n0 = P_abs / (e u_B A_eff eps_T)
    with A_eff = 2 pi R^2 h_l + 2 pi R l_p h_R and eps_T = eps_c + 2 Te (electron energy carried to the walls)."""
    a_eff = 2 * np.pi * radius**2 * plasma.h_l + 2 * np.pi * radius * l_p * plasma.h_r
    eps_t = plasma.gas.eps_c(plasma.te) + 2.0 * plasma.te
    return p_abs / (E_CHARGE * plasma.u_b * a_eff * eps_t)
