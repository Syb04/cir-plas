"""Linear (quasi-linear) AC analysis (spec 02 section 3.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..elements import CircuitModel
from ..project import Project
from ..netlist.builder import AcSettings, NetlistBuilder, NetlistIndex
from ..ngspice.shared import RawResult
from .common import Estimates, RunContext, build_model, check_errors, initial_estimates


@dataclass
class AcCellResult:
    index: int
    r_center: float
    area: float
    vsh_b: complex  # bottom sheath voltage phasor (plasma - wall)
    vsh_t: complex
    i_sc_b: complex  # capacitive-path current phasors
    i_sc_t: complex
    j_z: complex  # axial current density at the bulk mid node [A/m^2]
    p_ohm_bulk: float  # W (0.5 |I|^2 R, axial halves + half of adjacent radial bulk branches)
    p_stoc_b: float
    p_stoc_t: float
    c_lin_b: float
    c_lin_t: float

    @property
    def s_e(self) -> float:
        return (self.p_ohm_bulk + self.p_stoc_b + self.p_stoc_t) / self.area


@dataclass
class AcPortResult:
    port: str
    v: complex
    i: complex
    z: complex  # impedance at the port node (V_port / I_port)
    p: float
    z_chain: complex = complex("nan")  # impedance seen at the start of the port chain (feed L, blocking C, port): used for matching


@dataclass
class AcResult:
    frequency: float
    cells: list[AcCellResult]
    ports: list[AcPortResult]
    model: CircuitModel
    index: NetlistIndex
    raw: RawResult
    estimates: Estimates
    iterations: int
    diagnostics: dict = field(default_factory=dict)
    radial_currents: dict = field(default_factory=dict)  # (boundary, level k) -> complex current
    matching: dict = field(default_factory=dict)  # port -> {"l", "c", "topology", "z_load", "z_in"} for auto-tuned L matches

    def radial(self, name: str) -> np.ndarray:
        return np.array([getattr(c, name) for c in self.cells])

    @property
    def r(self) -> np.ndarray:
        return self.radial("r_center")


def _first(v) -> complex:
    return complex(v[0]) if np.ndim(v) else complex(v)


def extract_ac(res: RawResult, idx: NetlistIndex, model: CircuitModel, frequency: float, est: Estimates, iterations: int) -> AcResult:
    def V(node: str) -> complex:
        return 0j if node == "0" else _first(res.get(f"v({node})"))

    def I(name: str) -> complex:
        return _first(res.get(name))

    radial_share = np.zeros(model.n_cells)
    radial_currents = {}
    for be in model.boundaries:
        b = be.boundary
        for k, le in enumerate(be.levels, start=1):
            key = (b.index, k)
            if key not in idx.i_radial:
                continue
            i_r = I(idx.i_radial[key])
            radial_currents[key] = i_r
            if le.drude is not None:
                p = 0.5 * abs(i_r) ** 2 * le.drude.r
                radial_share[b.index] += 0.5 * p
                radial_share[b.index + 1] += 0.5 * p
    cells = []
    for i, ce in enumerate(model.cells):
        c = ce.cell
        n1 = i + 1
        vsh_b = V(idx.node_bulk[i][0]) - V(idx.node_sb[i])
        vsh_t = V(idx.node_bulk[i][-1]) - V(idx.node_st[i])
        i_sc_b = I(idx.i_sheath_b[i])
        i_sc_t = I(idx.i_sheath_t[i])
        p_ohm = 0.0
        jz = 0j
        for j, d in enumerate(ce.bulk):
            ia = I(idx.i_bulk[i][j])
            ib = I(idx.i_bulk[i][j].replace(f"lbz_{n1}_{j+1}a", f"lbz_{n1}_{j+1}b"))
            p_ohm += 0.5 * d.r * 0.5 * (abs(ia) ** 2 + abs(ib) ** 2)
            if j == len(ce.bulk) // 2:
                jz = ia / c.area
        p_ohm += radial_share[i]
        cells.append(AcCellResult(
            index=i, r_center=c.r_center, area=c.area, vsh_b=vsh_b, vsh_t=vsh_t, i_sc_b=i_sc_b, i_sc_t=i_sc_t, j_z=jz,
            p_ohm_bulk=p_ohm, p_stoc_b=0.5 * abs(i_sc_b) ** 2 * ce.sheath_b.rst, p_stoc_t=0.5 * abs(i_sc_t) ** 2 * ce.sheath_t.rst,
            c_lin_b=ce.c_lin_b, c_lin_t=ce.c_lin_t,
        ))
    ports = []
    for port in idx.ports:
        vp = V(port)
        ip = I(idx.i_port[port])
        z = vp / ip if abs(ip) > 0 else complex("nan")
        v_chain = V(idx.node_chain[port]) if port in idx.node_chain else vp
        z_chain = v_chain / ip if abs(ip) > 0 else complex("nan")
        ports.append(AcPortResult(port=port, v=vp, i=ip, z=z, p=0.5 * (vp * np.conj(ip)).real, z_chain=z_chain))
    gap = float(np.mean([c.l_gap for c in model.mesh.cells]))
    l_p = float(np.mean([c.l_p for c in model.mesh.cells]))
    s_tot = float(np.mean([c.s_b + c.s_t for c in model.mesh.cells]))
    diag = model.plasma.diagnostics(2 * np.pi * frequency, s_tot=s_tot, l_total=gap + float(np.mean([c.z_surface_b for c in model.mesh.cells])), l_p=l_p)
    r_w = model.project.geometry.regions[0].r_out
    if diag.get("k_eff") == diag.get("k_eff"):
        diag["kR_wafer"] = diag["k_eff"] * r_w
    diag["delta_over_half_bulk"] = diag["delta"] / (0.5 * l_p)
    return AcResult(frequency=frequency, cells=cells, ports=ports, model=model, index=idx, raw=res, estimates=est,
                    iterations=iterations, diagnostics=diag, radial_currents=radial_currents)


def _auto_match_ports(project: Project) -> list[str]:
    return [port for port, ps in project.circuit.ports.items() if ps.generator.type == "l_match" and ps.generator.auto]


def _without_generators(project: Project, ports: list[str]) -> Project:
    d = project.model_dump()
    for port in ports:
        d["circuit"]["ports"][port]["generator"]["type"] = "none"
    return Project.model_validate(d)


def tune_matching(ctx: RunContext, frequency: float, est: Estimates | None = None) -> dict:
    """Design the L matches of the auto-tuned ports from the quasi-linear AC impedance seen at the port
    (sources driving the port chain directly)."""
    from .matching import design_l_match

    ports = _auto_match_ports(ctx.project)
    if not ports:
        return {}
    plain = _without_generators(ctx.project, ports)
    ctx_plain = RunContext(project=plain, backend=ctx.backend, log=ctx.log, progress=lambda d: None, cancel=ctx.cancel)
    ac = run_ac(ctx_plain, frequency=frequency, est=est)
    out = {}
    for pr in ac.ports:
        if pr.port in ports:
            gen = ctx.project.circuit.ports[pr.port].generator
            m = design_l_match(pr.z_chain, frequency, gen.r_gen, gen.topology)
            out[pr.port] = {"l": m.l, "c": m.c, "topology": m.topology, "z_load": pr.z_chain}
            ctx.log(f"port {pr.port}: L-match {m.topology} L={m.l*1e9:.1f} nH C={m.c*1e12:.1f} pF for Z_load={pr.z_chain:.2f} ohm")
    return out


def run_ac(ctx: RunContext, frequency: float | None = None, est: Estimates | None = None, max_iter: int = 12, relax: float = 0.85,
           tol: float = 0.02, matching: dict | None = None) -> AcResult:
    """Quasi-linear AC solution at one frequency: iterate sheath linearization until the sheath
    voltage amplitudes used for C_lin agree with the solved ones."""
    p = ctx.project
    if frequency is None:
        frequency = p.f_max()
    if matching is None and _auto_match_ports(p):
        matching = tune_matching(ctx, frequency, est)
        # the plasma impedance depends on the drive level: re-design on the matched solution until the generator
        # sees its own impedance (a few passes)
        from .matching import design_l_match

        import math as _m

        for k in range(10):
            trial = run_ac(ctx, frequency=frequency, est=est, max_iter=max_iter, relax=relax, tol=tol, matching=matching)
            est = trial.estimates
            done = True
            for pr in trial.ports:
                if pr.port in matching:
                    gen = p.circuit.ports[pr.port].generator
                    z_in = trial.matching[pr.port].get("z_in", complex("nan"))
                    if not (abs(z_in - gen.r_gen) <= 0.03 * gen.r_gen):
                        done = False
                        m = design_l_match(pr.z_chain, frequency, gen.r_gen, gen.topology)
                        old = matching[pr.port]
                        # relaxed update in log space (the plasma load moves with the drive level)
                        a = 0.5
                        l_new = _m.exp((1 - a) * _m.log(old["l"]) + a * _m.log(m.l)) if old["topology"] == m.topology else m.l
                        c_new = _m.exp((1 - a) * _m.log(old["c"]) + a * _m.log(m.c)) if old["topology"] == m.topology else m.c
                        matching[pr.port] = {"l": l_new, "c": c_new, "topology": m.topology, "z_load": pr.z_chain}
            ctx.progress({"stage": "match", "iteration": k, "z_in": {q: f"{trial.matching[q].get('z_in', complex('nan')):.2f}" for q in matching}})
            if done:
                break
    matching = matching or {}
    match_values = {k: (v["l"], v["c"]) for k, v in matching.items()}
    match_topology = {k: v["topology"] for k, v in matching.items()}
    model = None
    n_cells = None
    result = None
    for it in range(1, max_iter + 1):
        if est is None:
            from ..mesh import build_radial_mesh

            n_cells = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
            est = initial_estimates(p, n_cells)
        model = build_model(p, est)
        netlist, idx = NetlistBuilder(model, "ac", ac=AcSettings(frequency=frequency), match_values=match_values, match_topology=match_topology).build()
        res = ctx.backend.run(netlist, cancel=ctx.cancel)
        check_errors(res, "AC analysis")
        result = extract_ac(res, idx, model, frequency, est, it)
        result.matching = {k: dict(v) for k, v in matching.items()}
        for port, node_in in idx.node_match_in.items():
            v_in = _first(res.get(f"v({node_in})"))
            i_src = -_first(res.get(idx.i_source[port]))  # current leaving the generator into r_gen
            if port in result.matching and abs(i_src) > 0:
                result.matching[port]["z_in"] = v_in / i_src  # impedance seen after the generator resistance
        v1_b_new = np.abs(result.radial("vsh_b"))
        v1_t_new = np.abs(result.radial("vsh_t"))
        change = max(float(np.max(np.abs(v1_b_new - est.v1_b) / np.maximum(est.v1_b, 1.0))),
                     float(np.max(np.abs(v1_t_new - est.v1_t) / np.maximum(est.v1_t, 1.0))))
        ctx.progress({"stage": "ac", "iteration": it, "change": change, "f": frequency})
        est = Estimates(v1_b=np.maximum((1 - relax) * est.v1_b + relax * v1_b_new, 1.0),
                        v1_t=np.maximum((1 - relax) * est.v1_t + relax * v1_t_new, 1.0),
                        s_b=est.s_b, s_t=est.s_t, v_dc_guess=dict(est.v_dc_guess))
        if change < tol:
            break
    # DC self-bias guess for ports with blocking capacitors: electrode-side sheath minus ground-side sheath
    v1b = np.abs(result.radial("vsh_b"))
    v1t = np.abs(result.radial("vsh_t"))
    areas = result.radial("area")
    cond = np.array([c.conductor for c in model.mesh.cells])
    for port in ("W", "F"):
        m = cond == port
        if m.any():
            vb = float(np.sum(v1b[m] * areas[m]) / np.sum(areas[m]))
            vt = float(np.sum(v1t * areas) / np.sum(areas))
            est.v_dc_guess[port] = -(vb - vt)
    result.estimates = est
    return result
