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


def run_density_iteration(ctx: RunContext, est: Optional[Estimates] = None) -> DensityIterationResult:
    p = ctx.project
    spec = p.numerics.density_iteration
    from ..mesh import build_radial_mesh

    rm = build_radial_mesh(p.geometry, p.numerics.mesh)
    r = rm.r_centers
    if est is None:
        est = initial_estimates(p, rm.n_cells)
    # initial profile from the prescribed density
    from ..plasma import density_profile

    n = density_profile(p.plasma.density, r, p.geometry.r_wall)
    alpha = spec.relax
    history = [n.copy()]
    te_hist: list[float] = []
    residuals: list[float] = []
    relax_hist: list[float] = []
    converged = False
    ac = None
    prev_delta = None
    for k in range(1, spec.max_iter + 1):
        proj_k = _project_with_density(p, r, n)
        ctx_k = RunContext(project=proj_k, backend=ctx.backend, log=ctx.log, progress=lambda d: None, cancel=ctx.cancel)
        ac = run_ac(ctx_k, est=est, max_iter=6)
        est = ac.estimates
        plasma = ac.model.plasma
        s_e = ac.radial("s_e")
        l_p = np.array([c.l_p for c in ac.model.mesh.cells])
        n_hat = radial_power_balance(rm.r_edges, s_e, l_p, plasma)
        delta = n_hat - n
        res = float(np.max(np.abs(delta) / np.maximum(n, 1e-30)))
        if prev_delta is not None:
            # oscillation -> damp, monotone -> accelerate (bounded)
            if np.dot(delta, prev_delta) < 0:
                alpha = max(0.1, 0.5 * alpha)
            else:
                alpha = min(0.8, 1.3 * alpha)
        prev_delta = delta
        n = (1.0 - alpha) * n + alpha * n_hat
        n = np.maximum(n, 1e-3 * n.max())
        history.append(n.copy())
        te_hist.append(plasma.te)
        residuals.append(res)
        relax_hist.append(alpha)
        ctx.progress({"stage": "density", "iteration": k, "residual": res, "relax": alpha, "te": plasma.te,
                      "n_center": float(n[0]), "n_mean": float(np.mean(n))})
        ctx.log(f"density iteration {k}: residual={res:.3e} alpha={alpha:.2f} Te={plasma.te:.3f} n0={n[0]:.3e}")
        if res < spec.tol:
            converged = True
            break
        if ctx.cancel():
            break
    # final AC with the converged profile (for consistent reporting)
    proj_f = _project_with_density(p, r, n)
    ctx_f = RunContext(project=proj_f, backend=ctx.backend, log=ctx.log, progress=lambda d: None, cancel=ctx.cancel)
    ac = run_ac(ctx_f, est=est, max_iter=6)
    return DensityIterationResult(r_centers=r, n_history=history, te_history=te_hist, residuals=residuals, relax_history=relax_hist,
                                  converged=converged, iterations=len(residuals), ac=ac,
                                  notes={"d_a": ambipolar_diffusion(ac.model.plasma), "final_project": proj_f.to_json()})
