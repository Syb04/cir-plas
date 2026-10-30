"""Power-specified drive (spec 02 section 3.6): iterate the source amplitude until the delivered power
(time-averaged power into the port node) matches the target.

Single-frequency ports only (one source per port with `power` set). The AC pre-solve gives the first
correction exactly for a linear circuit (P ~ V^2); the nonlinear transient is then corrected by a
secant iteration on log P vs log V.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..project import Project
from .ac import AcResult, run_ac
from .common import RunContext
from .tran import TranResult, run_tran


@dataclass
class PowerDriveResult:
    history: list[dict] = field(default_factory=list)
    converged: bool = False
    amplitudes: dict[str, float] = field(default_factory=dict)


def power_targets(project: Project) -> dict[str, float]:
    out = {}
    for port, ps in project.circuit.ports.items():
        pw = [s for s in ps.sources if s.power is not None]
        if len(pw) > 1 or (pw and len(ps.sources) > 1):
            raise ValueError(f"port {port}: power-specified drive supports exactly one source per port")
        if pw:
            out[port] = float(pw[0].power)
    return out


def _with_amplitudes(project: Project, amps: dict[str, float]) -> Project:
    d = project.model_dump()
    for port, v in amps.items():
        d["circuit"]["ports"][port]["sources"][0]["v"] = float(v)
    return Project.model_validate(d)


def run_power_drive(ctx: RunContext, mode: str = "tran", tol: float = 0.02, max_iter: int = 4) -> tuple[Project, Optional[AcResult], Optional[TranResult], PowerDriveResult]:
    """Return the project with the converged amplitudes and the final AC / TRAN results."""
    base = ctx.project
    targets = power_targets(base)
    res = PowerDriveResult()
    amps = {port: float(base.circuit.ports[port].sources[0].v) for port in targets}
    if not targets:
        ac = run_ac(ctx)
        tran = run_tran(ctx, ac=ac) if mode in ("tran", "sc") else None
        return base, ac, tran, res
    # --- AC pre-scaling (exact for the linear circuit) ---
    proj = _with_amplitudes(base, amps)
    ctx_k = RunContext(project=proj, backend=ctx.backend, log=ctx.log, progress=ctx.progress, cancel=ctx.cancel)
    ac = run_ac(ctx_k)
    for pr in ac.ports:
        if pr.port in targets and pr.p > 0:
            amps[pr.port] = amps[pr.port] * math.sqrt(targets[pr.port] / pr.p)
    res.history.append({"stage": "ac", "amplitudes": dict(amps)})
    if mode == "ac":
        proj = _with_amplitudes(base, amps)
        ctx_k = RunContext(project=proj, backend=ctx.backend, log=ctx.log, progress=ctx.progress, cancel=ctx.cancel)
        ac = run_ac(ctx_k)
        res.converged = True
        res.amplitudes = dict(amps)
        return proj, ac, None, res
    # --- transient secant iteration ---
    prev: dict[str, tuple[float, float]] = {}
    tran = None
    for k in range(max_iter + 1):
        proj = _with_amplitudes(base, amps)
        ctx_k = RunContext(project=proj, backend=ctx.backend, log=ctx.log, progress=ctx.progress, cancel=ctx.cancel)
        ac = run_ac(ctx_k)
        tran = run_tran(ctx_k, ac=ac)
        powers = {pr.port: pr.p for pr in tran.post.ports}
        errs = {port: (powers[port] - targets[port]) / targets[port] for port in targets}
        res.history.append({"stage": "tran", "iteration": k, "amplitudes": dict(amps), "powers": {q: powers[q] for q in targets}, "errors": errs})
        ctx.progress({"stage": "power_drive", "iteration": k, "amplitudes": dict(amps), "powers": {q: powers[q] for q in targets}})
        if all(abs(e) <= tol for e in errs.values()):
            res.converged = True
            break
        if k == max_iter:
            break
        for port in targets:
            v, pw = amps[port], powers[port]
            if port in prev and prev[port][1] > 0 and pw > 0 and abs(math.log(v / prev[port][0])) > 1e-6:
                slope = (math.log(pw) - math.log(prev[port][1])) / (math.log(v) - math.log(prev[port][0]))
                slope = min(max(slope, 1.0), 3.0)
            else:
                slope = 2.0
            prev[port] = (v, pw)
            amps[port] = v * (targets[port] / pw) ** (1.0 / slope)
    res.amplitudes = dict(amps)
    return proj, ac, tran, res
