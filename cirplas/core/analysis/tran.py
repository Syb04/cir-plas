"""Nonlinear transient analysis driver (spec 02 sections 3.2-3.3).

Strategy
--------
1. Quasi-linear AC pre-solve -> sheath voltage amplitudes (nominal sheath widths) and a
   first guess of the DC self-bias of every electrode with a blocking capacitor.
2. Transient runs start from a DC operating point in which the electrode-side nodes are
   *held* at the guessed self-bias (``.ic`` without ``uic``), so the blocking capacitor
   starts pre-charged and only small corrections remain.
3. The residual DC current through the blocking capacitor (measured over the last
   ``meas_cycles``) is driven to zero by a secant iteration on the bias guess.
4. Periodic steady state is checked by comparing the first and last cycle of the
   measurement window; if not converged the run is repeated with more cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..elements import CircuitModel
from ..netlist.builder import NetlistBuilder, NetlistIndex, TranSettings
from ..ngspice.shared import RawResult
from ..post.signals import harmonics, resample, time_average, uniform_grid
from ..post.tran_post import TranPostResult, postprocess_tran
from .ac import AcResult, run_ac
from .common import Estimates, RunContext, build_model, check_errors


@dataclass
class TranResult:
    raw: RawResult
    index: NetlistIndex
    model: CircuitModel
    post: TranPostResult
    period: float
    cycles: int
    meas_cycles: int
    v_dc_clamp: dict[str, float]
    bias_iterations: int
    pss_converged: bool
    pss_change: float
    ac: Optional[AcResult] = None
    history: list[dict] = field(default_factory=list)


def _forced_nodes(model: CircuitModel, idx: NetlistIndex, port: str) -> list[str]:
    """Electrode node plus every bottom-stack node of the cells on that electrode."""
    nodes = [port]
    for i, c in enumerate(model.mesh.cells):
        if c.conductor != port:
            continue
        n1 = i + 1
        m_b = len(c.bottom_layers)
        nodes += [f"S{k}_{n1}" for k in range(1, m_b)]
        if m_b > 0:
            nodes.append(idx.node_sb[i])
    return nodes


def _pss_change(res: RawResult, idx: NetlistIndex, period: float, meas_cycles: int, n_per: int = 100, include_dc: bool = False) -> float:
    """Relative change of RF amplitudes (and optionally DC levels) between the first and last cycle of the window.

    The DC levels of a discharge with blocking capacitors settle on the slow self-bias time scale; that part is
    controlled by the bias iteration (residual DC current), so by default only the RF amplitudes are compared.
    """
    if meas_cycles < 2:
        return 0.0
    t = res.scale
    t_end = t[-1]
    vals = []
    names = [f"v({p})" for p in idx.ports] + [f"v({idx.node_bulk[i][0]})" for i in (0, idx.n_cells // 2, idx.n_cells - 1)]
    names += [idx.i_port[p] for p in idx.ports]
    for name in names:
        y = res.get(name)
        c1 = uniform_grid(t_end - meas_cycles * period, period, 1, n_per)
        c2 = uniform_grid(t_end - period, period, 1, n_per)
        y1, y2 = resample(t, y, c1), resample(t, y, c2)
        h1, h2 = harmonics(y1, n_per, 3), harmonics(y2, n_per, 3)
        scale = max(abs(h2.dc), h2.amplitude(1), 1e-30)
        if include_dc:
            vals.append(abs(h2.dc - h1.dc) / scale)
        vals.append(abs(h2.amplitude(1) - h1.amplitude(1)) / scale)
        vals.append(abs(h2.amplitude(2) - h1.amplitude(2)) / scale)
    return float(max(vals))


def run_tran(ctx: RunContext, ac: Optional[AcResult] = None) -> TranResult:
    p = ctx.project
    spec = p.numerics.tran
    period = p.base_period()
    f_max = p.f_max()
    t_max = (1.0 / f_max) / spec.pts_per_cycle
    if ac is None:
        ctx.log("AC pre-solve for sheath estimates")
        ac = run_ac(ctx)
    est: Estimates = ac.estimates
    model = build_model(p, est)
    # which ports need a self-bias iteration
    bias_ports = [port for port, ps in p.circuit.ports.items() if ps.blocking_c is not None] if p.numerics.dc_bias.method != "none" else []
    v_dc = {port: (p.circuit.ports[port].dc_bias_guess if p.circuit.ports[port].dc_bias_guess is not None else est.v_dc_guess.get(port, 0.0))
            for port in bias_ports}
    history: list[dict] = []
    cycles = spec.cycles_min
    ramp = spec.ramp_cycles * (1.0 / f_max)

    _, idx0 = NetlistBuilder(model, "tran", tran=TranSettings(t_max, 1.0, t_max, ramp)).build()
    clamp_nodes = {port: _forced_nodes(model, idx0, port) for port in bias_ports}

    def one_run(n_cycles: int, bias: dict[str, float]):
        clamp = {port: (vb, clamp_nodes[port]) for port, vb in bias.items()}
        t_stop = n_cycles * period
        settings = TranSettings(t_step=t_max, t_stop=t_stop, t_max=t_max, ramp_time=ramp, bias_clamp=clamp)
        netlist, idx = NetlistBuilder(model, "tran", tran=settings).build()
        res = ctx.backend.run(netlist, cancel=ctx.cancel)
        check_errors(res, "transient analysis")
        post = postprocess_tran(res, idx, model, period, spec.meas_cycles, n_per_period=spec.pts_per_cycle)
        return res, idx, post

    # ---- self-bias secant iteration (short runs) ----
    n_iter = 0
    prev: dict[str, tuple[float, float]] = {}
    res = idx = post = None
    for n_iter in range(0, p.numerics.dc_bias.max_iter + 1):
        res, idx, post = one_run(cycles, v_dc)
        resid = {}
        for pr in post.ports:
            if pr.port in bias_ports:
                resid[pr.port] = (pr.i_dc, pr.i_1)
        change = _pss_change(res, idx, period, spec.meas_cycles)
        history.append({"stage": "bias", "iteration": n_iter, "cycles": cycles, "v_dc": dict(v_dc),
                        "i_dc": {k: v[0] for k, v in resid.items()}, "i_1": {k: v[1] for k, v in resid.items()}, "pss_change": change})
        ctx.progress({"stage": "tran_bias", "iteration": n_iter, "cycles": cycles, "v_dc": dict(v_dc),
                      "residual": {k: v[0] / max(v[1], 1e-30) for k, v in resid.items()}, "pss_change": change})
        if not bias_ports:
            break
        done = all(abs(i_dc) <= p.numerics.dc_bias.tol * max(i_1, 1e-30) for i_dc, i_1 in resid.values())
        if done or n_iter >= p.numerics.dc_bias.max_iter:
            break
        new_v = {}
        for port, (i_dc, i_1) in resid.items():
            v_old = v_dc[port]
            if port in prev:
                v_prev, i_prev = prev[port]
                g = (i_dc - i_prev) / (v_old - v_prev) if abs(v_old - v_prev) > 1e-9 else None
            else:
                g = None
            if g is None or g <= 0:
                # DC current into the electrode grows with its potential: slope ~ sheath DC conductance I_i/Te summed over cells
                ii_tot = sum(ce.sheath_b.ii for ce in model.cells if ce.cell.conductor == port)
                g = ii_tot / model.plasma.te
            step = -i_dc / g
            step = float(np.clip(step, -0.5 * max(abs(v_old), 50.0), 0.5 * max(abs(v_old), 50.0)))
            prev[port] = (v_old, i_dc)
            new_v[port] = v_old + step
        v_dc = new_v

    # ---- periodic steady state: extend the run if needed ----
    change = _pss_change(res, idx, period, spec.meas_cycles)
    while change > spec.pss_tol and cycles < spec.cycles_max:
        cycles = min(spec.cycles_max, int(np.ceil(cycles * 1.6)))
        ctx.log(f"periodic steady state not reached (change={change:.3e}); extending to {cycles} cycles")
        res, idx, post = one_run(cycles, v_dc)
        change = _pss_change(res, idx, period, spec.meas_cycles)
        history.append({"stage": "pss", "cycles": cycles, "pss_change": change})
        ctx.progress({"stage": "tran_pss", "cycles": cycles, "pss_change": change})
    return TranResult(raw=res, index=idx, model=model, post=post, period=period, cycles=cycles, meas_cycles=spec.meas_cycles,
                      v_dc_clamp=v_dc, bias_iterations=n_iter, pss_converged=change <= spec.pss_tol, pss_change=change,
                      ac=ac, history=history)
