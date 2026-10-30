"""Post-processing of a transient run on the measurement window (spec 01 sections 7, 9)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..elements import CircuitModel
from ..netlist.builder import NetlistIndex
from ..ngspice.shared import RawResult
from .signals import Harmonics, harmonics, resample, time_average, uniform_grid


@dataclass
class CellResult:
    index: int
    r_center: float
    area: float
    vsh_b_dc: float
    vsh_b_1: float  # fundamental amplitude
    vsh_b_min: float
    vsh_b_max: float
    vsh_t_dc: float
    vsh_t_1: float
    s_b_mean: float
    s_b_max: float
    s_t_mean: float
    p_ohm_bulk: float  # W (axial + half of adjacent radial)
    p_stoc_b: float  # W
    p_stoc_t: float
    p_ion_b: float
    p_ion_t: float
    p_el_b: float  # electron conduction loss (I_e * V) [W]
    p_el_t: float
    j_z_1: float  # fundamental amplitude of axial current density at the bulk center [A/m^2]
    e_ion_mean: float = 0.0  # mean ion energy at the bottom surface ~ e <Vsh> [eV]
    e_ion_width: float = 0.0  # bimodal IEDF width estimate [eV] (RF modulation, ion transit time)

    @property
    def s_e(self) -> float:
        """Electron heating per unit area [W/m^2]."""
        return (self.p_ohm_bulk + self.p_stoc_b + self.p_stoc_t) / self.area


@dataclass
class PortResult:
    port: str
    v_dc: float
    v_1: float
    i_1: float
    phase_deg: float  # phase of V relative to I at the fundamental
    p: float  # time-averaged power delivered into the port node [W]
    p_source: float  # power delivered by the ideal source [W]
    z_re: float
    z_im: float
    i_dc: float  # DC current through the series sense source (blocking-cap residual)


@dataclass
class TranPostResult:
    t_window: np.ndarray
    cells: list[CellResult]
    ports: list[PortResult]
    p_ohm_radial_total: float
    p_total_dissipated: float
    p_source_total: float
    power_balance_error: float
    extra: dict = field(default_factory=dict)
    maps: dict = field(default_factory=dict)  # 2D (r, z) arrays: p_ohm [W/m^3], jz_1 [A/m^2], jr_1 [A/m^2], z_mid [m]

    def radial(self, name: str) -> np.ndarray:
        return np.array([getattr(c, name) for c in self.cells])

    @property
    def r(self) -> np.ndarray:
        return self.radial("r_center")


def _window_arrays(res: RawResult, t_start: float, t_end: float):
    t = res.scale
    m = (t >= t_start - 1e-15) & (t <= t_end + 1e-15)
    return t[m], m


def _fund_amplitude(t_u: np.ndarray, y: np.ndarray, n_per: int) -> Harmonics:
    return harmonics(y, n_per, n_harm=12)


def postprocess_tran(res: RawResult, idx: NetlistIndex, model: CircuitModel, period: float, meas_cycles: int,
                     n_per_period: int = 200) -> TranPostResult:
    t_all = res.scale
    t_end = float(t_all[-1])
    t_start = t_end - meas_cycles * period
    t_w, mask = _window_arrays(res, t_start, t_end)
    t_u = uniform_grid(t_start, period, meas_cycles, n_per_period)

    def v(name: str) -> np.ndarray:
        return res.get(name)[mask]

    def vec(name: str) -> np.ndarray:
        return res.get(name)[mask]

    def uni(y: np.ndarray) -> np.ndarray:
        return resample(t_w, y, t_u)

    cells: list[CellResult] = []
    p_diss = 0.0
    nz = idx.n_z
    n_c = idx.n_cells
    # 2D maps on (cell, bulk layer)
    map_p_ohm = np.zeros((n_c, nz))
    map_jz = np.zeros((n_c, nz))
    map_jr = np.zeros((max(n_c - 1, 1), nz))
    map_pr = np.zeros((max(n_c - 1, 1), nz))  # radial ohmic power density per boundary level [W/m^3]
    # radial ohmic power per boundary level (distributed half/half to the adjacent cells)
    radial_cell_share = np.zeros(idx.n_cells)
    radial_layer_share = np.zeros((n_c, nz))
    p_rad_total = 0.0
    for be in model.boundaries:
        b = be.boundary
        for k, le in enumerate(be.levels, start=1):
            key = (b.index, k)
            if key not in idx.i_radial:
                continue
            r_val = None
            if le.drude is not None:
                r_val = le.drude.r
            elif le.rc is not None and le.rc.r is not None:
                r_val = le.rc.r
            if r_val is None:
                continue
            i_r = vec(idx.i_radial[key])
            p = time_average(t_w, i_r * i_r) * r_val
            p_rad_total += p
            if le.level.kind == "bulk":
                radial_cell_share[b.index] += 0.5 * p
                radial_cell_share[b.index + 1] += 0.5 * p
                j = le.level.index
                radial_layer_share[b.index, j] += 0.5 * p
                radial_layer_share[b.index + 1, j] += 0.5 * p
                area_r = 2.0 * np.pi * b.r * le.level.thickness
                map_jr[b.index, j] = harmonics(uni(i_r), n_per_period).amplitude(1) / area_r
                map_pr[b.index, j] = p / (area_r * b.dr)
            else:
                p_diss += p  # layer losses counted globally
    for i, ce in enumerate(model.cells):
        c = ce.cell
        n1 = i + 1
        v_sb = v(f"v({idx.node_sb[i]})") if idx.node_sb[i] != "0" else np.zeros_like(t_w)
        v_b1 = v(f"v({idx.node_bulk[i][0]})")
        v_bt = v(f"v({idx.node_bulk[i][-1]})")
        v_st = v(f"v({idx.node_st[i]})") if idx.node_st[i] != "0" else np.zeros_like(t_w)
        vsh_b = v_b1 - v_sb
        vsh_t = v_bt - v_st
        hb = harmonics(uni(vsh_b), n_per_period)
        ht = harmonics(uni(vsh_t), n_per_period)
        i_sc_b = vec(idx.i_sheath_b[i])
        i_sc_t = vec(idx.i_sheath_t[i])
        p_stoc_b = time_average(t_w, i_sc_b**2) * ce.sheath_b.rst
        p_stoc_t = time_average(t_w, i_sc_t**2) * ce.sheath_t.rst
        p_ion_b = ce.sheath_b.ii * time_average(t_w, vsh_b)
        p_ion_t = ce.sheath_t.ii * time_average(t_w, vsh_t)
        p_el_b = time_average(t_w, ce.sheath_b.electron_current(vsh_b) * vsh_b)
        p_el_t = time_average(t_w, ce.sheath_t.electron_current(vsh_t) * vsh_t)
        # bulk ohmic: two half segments per layer; current through the first half inductor
        p_ohm = 0.0
        jz1 = 0.0
        for j in range(nz):
            i_a = vec(idx.i_bulk[i][j])
            i_b = vec(idx.i_bulk[i][j].replace(f"lbz_{n1}_{j+1}a", f"lbz_{n1}_{j+1}b"))
            d = ce.bulk[j]
            p_layer = 0.5 * d.r * (time_average(t_w, i_a**2) + time_average(t_w, i_b**2))
            p_ohm += p_layer
            amp = harmonics(uni(i_a), n_per_period).amplitude(1) / c.area
            map_jz[i, j] = amp
            map_p_ohm[i, j] = (p_layer + radial_layer_share[i, j]) / (c.area * c.bulk_thickness[j])
            if j == nz // 2:
                jz1 = amp
        p_ohm += radial_cell_share[i]
        s_b = ce.sheath_b.width(vsh_b)
        s_t = ce.sheath_t.width(vsh_t)
        # IEDF estimate (bottom surface): collisionless ions, mean energy e<Vsh>; modulation width from the RF
        # amplitude and the ion sheath transit time tau_i = 3 s_mean sqrt(M/(2 e <Vsh>)) (Benoit-Cattin/Bernard form)
        from ..constants import E_CHARGE as _E

        s_mean_b = float(time_average(t_w, s_b))
        v_dc_b = max(hb.dc, 1e-3)
        tau_i = 3.0 * s_mean_b * math.sqrt(model.plasma.gas.mass_kg / (2.0 * _E * v_dc_b))
        w0 = 2.0 * math.pi / period
        e_width = 2.0 * hb.amplitude(1) / (w0 * tau_i) if tau_i > 0 else 0.0
        cells.append(CellResult(
            e_ion_mean=hb.dc, e_ion_width=min(e_width, 2.0 * hb.amplitude(1)),
            index=i, r_center=c.r_center, area=c.area,
            vsh_b_dc=hb.dc, vsh_b_1=hb.amplitude(1), vsh_b_min=float(vsh_b.min()), vsh_b_max=float(vsh_b.max()),
            vsh_t_dc=ht.dc, vsh_t_1=ht.amplitude(1),
            s_b_mean=float(time_average(t_w, s_b)), s_b_max=float(s_b.max()), s_t_mean=float(time_average(t_w, s_t)),
            p_ohm_bulk=p_ohm, p_stoc_b=p_stoc_b, p_stoc_t=p_stoc_t, p_ion_b=p_ion_b, p_ion_t=p_ion_t,
            p_el_b=p_el_b, p_el_t=p_el_t, j_z_1=jz1,
        ))
        # ions gain e*Vsh crossing the sheath (dissipation); electrons reaching the wall lose e*Vsh to the field,
        # i.e. the electron conduction element delivers power to the circuit -> enters with a negative sign
        p_diss += p_ohm + p_stoc_b + p_stoc_t + p_ion_b + p_ion_t - p_el_b - p_el_t
    # layer (dielectric/semiconductor) axial losses
    for i, ce in enumerate(model.cells):
        n1 = i + 1
        m_b = len(ce.bottom_layers)
        nodes_b = [idx.node_conductor[i]] + [f"S{k}_{n1}" for k in range(1, m_b)] + ([idx.node_sb[i]] if m_b > 0 else [])
        for k, rc in enumerate(ce.bottom_layers):
            if rc.r is not None:
                va = v(f"v({nodes_b[k]})") if nodes_b[k] != "0" else np.zeros_like(t_w)
                vb = v(f"v({nodes_b[k+1]})")
                p_diss += time_average(t_w, (va - vb) ** 2) / rc.r
        m_t = len(ce.top_layers)
        nodes_t = ([idx.node_st[i]] if m_t > 0 else [idx.node_top[i]]) + [f"U{k}_{n1}" for k in range(1, m_t)] + ([idx.node_top[i]] if m_t > 0 else [])
        for k, rc in enumerate(ce.top_layers):
            if rc.r is not None:
                va = v(f"v({nodes_t[k]})")
                vb = v(f"v({nodes_t[k+1]})")
                p_diss += time_average(t_w, (va - vb) ** 2) / rc.r
    # ports
    ports: list[PortResult] = []
    p_src_total = 0.0
    def node_v(name: str) -> np.ndarray:
        return np.zeros_like(t_w) if name == "0" else v(f"v({name})")

    def term_power(terms) -> float:
        # source branch current is defined into the source's + node
        return sum(time_average(t_w, (node_v(a) - node_v(b)) * -vec(br)) for a, b, br in terms)

    for port in idx.ports:
        vp = v(f"v({port})")
        ip = vec(idx.i_port[port])
        hv = harmonics(uni(vp), n_per_period)
        hi = harmonics(uni(ip), n_per_period)
        p_port = time_average(t_w, vp * ip)
        if port in idx.source_terms:
            p_src = term_power(idx.source_terms[port])
        else:
            vs = v(f"v({idx.node_source[port]})")
            i_src = -vec(idx.i_source[port])
            p_src = time_average(t_w, vs * i_src)
            gen = model.project.circuit.ports[port].generator
            if gen.type == "l_match":
                p_diss += time_average(t_w, i_src * i_src) * gen.r_gen  # generator internal resistance
        z = hv.c[1] / hi.c[1] if abs(hi.c[1]) > 0 else complex("nan")
        ports.append(PortResult(port=port, v_dc=hv.dc, v_1=hv.amplitude(1), i_1=hi.amplitude(1),
                                phase_deg=float(np.degrees(np.angle(z))) if z == z else float("nan"),
                                p=p_port, p_source=p_src, z_re=float(z.real), z_im=float(z.imag), i_dc=hi.dc))
        p_src_total += p_src
    # schematic mode: DC sources deliver power, external resistors dissipate it
    if idx.dc_source_terms:
        p_src_total += term_power(idx.dc_source_terms)
    for a, b, r in idx.ext_resistors:
        p_diss += time_average(t_w, (node_v(a) - node_v(b)) ** 2) / r
    # DC power flowing into the series blocking capacitors (charging): (V_src,dc - V_port,dc) * I_dc with V_src,dc = 0
    cap_ports = idx.bias_ports if idx.bias_ports is not None else [pr.port for pr in ports]
    p_cap_dc = sum(-pr.v_dc * pr.i_dc for pr in ports if pr.port in cap_ports)
    pbe = (p_src_total - p_diss - p_cap_dc) / p_src_total if p_src_total else float("nan")
    z_mid = np.array([c.bulk_mid for c in model.mesh.cells])  # (n_c, nz) heights above each cell's bottom conductor
    maps = {"p_ohm": map_p_ohm, "jz_1": map_jz, "jr_1": map_jr, "p_ohm_radial": map_pr, "z_mid": z_mid,
            "r_centers": np.array([c.r_center for c in model.mesh.cells]), "r_boundaries": np.array([b.boundary.r for b in model.boundaries])}
    return TranPostResult(t_window=t_u, cells=cells, ports=ports, p_ohm_radial_total=p_rad_total,
                          p_total_dissipated=p_diss, p_source_total=p_src_total, power_balance_error=pbe,
                          extra={"p_capacitor_dc": p_cap_dc}, maps=maps)
