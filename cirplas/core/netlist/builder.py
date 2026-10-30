"""Netlist text generation (spec 02 section 2).

The builder emits the circuit description, the analysis statement and the
options; control blocks (for batch mode) are appended by the ngspice backend.
"""

from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from ..elements import CellElements, CircuitModel
from ..project import PortSpec, Project
from . import naming as nm

FORBIDDEN_USER_STATEMENTS = (".tran", ".ac", ".dc", ".op", ".control", ".endc", ".option", ".options", ".include", ".lib", ".save", ".ic", ".end", "shell")


def fmt(x: float) -> str:
    return f"{x:.6e}"


@dataclass
class TranSettings:
    t_step: float
    t_stop: float
    t_max: float
    ramp_time: float
    # DC bias clamp: port -> (voltage, nodes). Each node is tied to a DC source through a resistor
    # R(t) = 1 + 1e12 (1 - exp(-t/tau)) ohm: a stiff clamp at the operating point that releases smoothly
    # within a few tau (spec 02 section 3.3).
    bias_clamp: dict[str, tuple[float, list[str]]] = field(default_factory=dict)
    clamp_tau: float = 2e-10


@dataclass
class AcSettings:
    frequency: float
    sweep: Optional[tuple[float, float, int]] = None  # (f_start, f_stop, points/decade)


@dataclass
class NetlistIndex:
    """Where to find things in the simulator output (vector names are lowercase)."""

    mode: str
    n_cells: int
    n_z: int
    node_sb: list[str]
    node_bulk: list[list[str]]  # per cell: B_1..B_{Nz+1}
    node_bulk_mid: list[list[str]]  # per cell: Bc_1..Bc_Nz
    node_st: list[str]
    node_top: list[str]
    node_conductor: list[str]
    i_sheath_b: list[str]  # vector names of the bottom sheath capacitive-path currents
    i_sheath_t: list[str]
    i_bulk: list[list[str]]  # per cell, per layer: vector of the first half-inductor current
    i_radial: dict[tuple[int, int], str]  # (boundary, level k) -> vector name
    i_port: dict[str, str]  # port -> vector of the series sense source (current into the port node)
    i_source: dict[str, str]  # port -> vector of the source branch current
    node_source: dict[str, str]  # port -> source node name
    i_outer: Optional[str]
    ports: list[str]
    chain_levels: dict[int, list[str]]  # boundary -> level kinds in order
    sheath_instances_b: list[str]
    sheath_instances_t: list[str]


# Nonlinear sheath capacitor. u = softplus(V) = (V + sqrt(V^2 + eps^2))/2  (>= 0, -> V for V >> eps, -> 0 for V << -eps)
#   charge form:       Q(V) = Kq (u + Vmin)^(1-beta) + Cmax (V - u)        [slope Cmax = C(0) for V < 0]
#   capacitance form:  C(V) = dQ/dV = [Kq (1-beta) (u + Vmin)^(-beta) - Cmax] u' + Cmax,  u' = (1 + V/sqrt(V^2+eps^2))/2
_U = "0.5*(V(P,M)+sqrt(pow(V(P,M),2)+pow(eps,2)))"
_UP = "0.5*(1+V(P,M)/sqrt(pow(V(P,M),2)+pow(eps,2)))"
_CAP_CHARGE = f"Csh  P M  Q = 'Kq*pow({_U}+Vmin, 1-beta) + Cmax*(V(P,M)-{_U})'"
_CAP_CAPACITANCE = f"Csh  P M  C = '(Kq*(1-beta)*pow({_U}+Vmin, -beta) - Cmax)*{_UP} + Cmax'"
# auxiliary-capacitor realization: V(Q0) = Q(V)/Cmax, the current through C0 = Cmax is dQ/dt and is copied into P->M
_CAP_ECF = "\n".join([
    f"Eq   Q0 0  value = {{(Kq*pow({_U}+Vmin, 1-beta) + Cmax*(V(P,M)-{_U}))/Cmax}}",
    "C0   Q0 Qx {Cmax}",
    "Vq   Qx 0  0",
    "Fq   P  M  Vq 1",
])

_SHEATH_HEAD = """\
.subckt sheath P W A=1 ns=1e16 Te=3 beta=0.5 s1=1e-4 Vmin=6 eps=0.6 Rst=1 Ii=1 Ie0=1 nd=116
* electron-depleted sheath: P = plasma side, W = wall side, V = V(P,W) >= 0
.param e0=8.8541878128e-12
.param Kq={e0*A*Te**beta/(s1*(1-beta))}
.param Cmax={Kq*(1-beta)*Vmin**(-beta)}
"""
_SHEATH_COMMON = """\
Rst  M N  {Rst}
Vsc  N W  0
Iion P W  {Ii}
"""
# Electron Boltzmann current I_e = Ie0 exp(-V/Te), V = V(P,W).
# (a) B-source (default): exponential for V > -3 Te, linear continuation below (C1-continuous, no overflow).
# (b) SPICE diodes (N = Te/VT) split into 16 parallel devices so that each device's saturation current stays
#     below ~1 A and its junction-limiting voltage vcrit = N VT ln(N VT/(sqrt(2) IS)) remains positive
#     (a single device with IS of several amperes makes ngspice's pnjlim produce NaNs at sheath collapse).
#     The constant source Iel cancels the diodes' -IS reverse term.
_N_PARALLEL_DIODES = 16
_ELECTRON_DIODE = "".join(f"Del{k} W P  dsh\n" for k in range(1, _N_PARALLEL_DIODES + 1)) + f"""\
Iel  W P  {{Ie0}}
.model dsh D(IS={{Ie0/{_N_PARALLEL_DIODES}}} N={{nd}} RS=0 CJO=0)
"""
_ELECTRON_BSOURCE = """\
Bel  W P  I = 'Ie0*((-V(P,W)/Te) < 3 ? exp(-V(P,W)/Te) : 20.0855369232*(1 - V(P,W)/Te - 3))'
"""


def sheath_subckt(electron_current: str, cap_form: str) -> str:
    cap = {"charge": _CAP_CHARGE, "capacitance": _CAP_CAPACITANCE, "ecf": _CAP_ECF}[cap_form]
    el = _ELECTRON_DIODE if electron_current == "diode" else _ELECTRON_BSOURCE
    return _SHEATH_HEAD + cap + "\n" + _SHEATH_COMMON + el + ".ends sheath\n"

SHEATH_SUBCKT_LINEAR = """\
.subckt sheath_lin P W C=1e-10 Rst=1
Csh  P M  {C}
Rst  M N  {Rst}
Vsc  N W  0
.ends sheath_lin
"""


def validate_user_netlist(text: str) -> list[str]:
    """Return a list of problems (empty if acceptable)."""
    problems = []
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip().lower()
        if not s or s.startswith("*"):
            continue
        for bad in FORBIDDEN_USER_STATEMENTS:
            if s == bad or s.startswith(bad + " ") or s.startswith(bad + "\t"):
                problems.append(f"line {ln}: statement '{bad}' is not allowed in extra_netlist")
    return problems


class NetlistBuilder:
    def __init__(self, model: CircuitModel, mode: Literal["tran", "ac"], tran: Optional[TranSettings] = None, ac: Optional[AcSettings] = None,
                 include_radial: bool = True, radial_kinds: Optional[set] = None, flux_chain: bool = True, diode_rs: float = 0.0):
        self.include_radial = include_radial  # debug: False -> no radial branches / flux chains (independent columns)
        self.radial_kinds = radial_kinds  # debug: restrict radial levels to these kinds (None = all)
        self.flux_chain = flux_chain  # debug: False -> radial branches without induced EMF
        self.diode_rs = diode_rs
        self.model = model
        self.project: Project = model.project
        self.mode = mode
        self.tran = tran
        self.ac = ac
        if mode == "tran" and tran is None:
            raise ValueError("tran settings required")
        if mode == "ac" and ac is None:
            raise ValueError("ac settings required")
        self.lines: list[str] = []
        self.index: Optional[NetlistIndex] = None

    # ------------------------------------------------------------------ public
    def build(self) -> tuple[str, NetlistIndex]:
        m = self.model
        n = m.n_cells
        self.lines = []
        self._header()
        self._sheath_subckts()
        idx = NetlistIndex(
            mode=self.mode, n_cells=n, n_z=m.mesh.n_z, node_sb=[], node_bulk=[], node_bulk_mid=[], node_st=[], node_top=[],
            node_conductor=[], i_sheath_b=[], i_sheath_t=[], i_bulk=[], i_radial={}, i_port={}, i_source={}, node_source={},
            i_outer=None, ports=[], chain_levels={}, sheath_instances_b=[], sheath_instances_t=[],
        )
        self.index = idx
        for ce in m.cells:
            self._cell(ce, idx)
        for be in m.boundaries:
            if self.include_radial:
                self._boundary(be, idx)
            else:
                self._boundary_tie_top(be, idx)
        self._top_boundary(idx)
        self._external(idx)
        self._analysis()
        self.lines.append(".end")
        return "\n".join(self.lines) + "\n", idx

    # ------------------------------------------------------------------ sections
    def _header(self) -> None:
        p = self.project
        pl = self.model.plasma
        self.lines += [
            f"* CirPlas netlist  project={p.name}  hash={p.hash()}  mode={self.mode}",
            f"* cells={self.model.n_cells} n_z={self.model.mesh.n_z}  Te={pl.te:.4f} eV  nu_m={pl.nu_m:.4e} 1/s  h_l={pl.h_l:.4f}",
            ".title CirPlas " + p.name,
        ]

    def _sheath_subckts(self) -> None:
        if self.mode == "ac":
            self.lines.append(SHEATH_SUBCKT_LINEAR)
        else:
            sh = self.project.plasma.sheath
            txt = sheath_subckt(sh.electron_current, sh.cap_form)
            if self.diode_rs > 0:
                txt = txt.replace("RS=0", f"RS={self.diode_rs:g}")
            self.lines.append(txt)

    def _sheath_instance(self, name: str, p_node: str, w_node: str, ce: CellElements, side: str) -> None:
        sh = ce.sheath_b if side == "b" else ce.sheath_t
        if self.mode == "ac":
            c_lin = ce.c_lin_b if side == "b" else ce.c_lin_t
            self.lines.append(f"{name} {p_node} {w_node} sheath_lin C={fmt(c_lin)} Rst={fmt(max(sh.rst, 1e-9))}")
            return
        self.lines.append(
            f"{name} {p_node} {w_node} sheath A={fmt(sh.area)} ns={fmt(sh.ns)} Te={fmt(sh.te)} beta={sh.beta:g} "
            f"s1={fmt(sh.s1)} Vmin={fmt(sh.vmin)} eps={fmt(sh.eps)} Rst={fmt(max(sh.rst, 1e-9))} Ii={fmt(sh.ii)} Ie0={fmt(sh.ie0)} nd={fmt(sh.nd)}"
        )

    def _cell(self, ce: CellElements, idx: NetlistIndex) -> None:
        c = ce.cell
        i = c.index + 1  # 1-based in names
        L = self.lines
        cond = nm.conductor_node(c.conductor)
        L.append(f"* ---- cell {i}: region={c.region} conductor={c.conductor} r=[{c.r_in:.5f},{c.r_out:.5f}] A={c.area:.4e} n0={ce.n0:.3e} ns={ce.ns:.3e} s_b={c.s_b:.3e} s_t={c.s_t:.3e}")
        # bottom stack: cond -> S1 -> ... -> Sb
        m_b = len(ce.bottom_layers)
        nodes_b = [cond] + [nm.s_layer(k, i) for k in range(1, m_b)] + ([nm.sb(i)] if m_b > 0 else [])
        for k, rc in enumerate(ce.bottom_layers):
            a, b = nodes_b[k], nodes_b[k + 1]
            lay = c.bottom_layers[k]
            L.append(f"Cb{k+1}_{i} {a} {b} {fmt(rc.c)}   ; {lay.name} eps_r={lay.material.eps_r} t={lay.t:.3e}")
            if rc.r is not None:
                L.append(f"Rb{k+1}_{i} {a} {b} {fmt(rc.r)}   ; sigma={lay.material.sigma}")
        sb_node = nodes_b[-1]
        idx.node_sb.append(sb_node)
        idx.node_conductor.append(cond)
        # bulk nodes
        nz = c.n_z
        bulk_nodes = [nm.bulk(i, j) for j in range(1, nz + 2)]
        mid_nodes = [nm.bulk_mid(i, j) for j in range(1, nz + 1)]
        idx.node_bulk.append(bulk_nodes)
        idx.node_bulk_mid.append(mid_nodes)
        # bottom sheath
        inst_b = f"Xshb_{i}"
        self._sheath_instance(inst_b, bulk_nodes[0], sb_node, ce, "b")
        idx.sheath_instances_b.append(inst_b)
        idx.i_sheath_b.append(nm.vec_subckt_branch(inst_b, "Vsc"))
        # bulk layers, split in halves around the mid node
        i_bulk_cell = []
        for j in range(1, nz + 1):
            d = ce.bulk[j - 1]
            top_node = bulk_nodes[j]
            bot_node = bulk_nodes[j - 1]
            mid = mid_nodes[j - 1]
            L.append(f"Lbz_{i}_{j}a {bot_node} Bx_{i}_{j}a {fmt(0.5*d.l)}   ; Le/2 (n={ce.n0:.3e})")
            L.append(f"Rbz_{i}_{j}a Bx_{i}_{j}a {mid} {fmt(0.5*d.r)}")
            L.append(f"Cbz_{i}_{j}a {bot_node} {mid} {fmt(2.0*d.c)}")
            L.append(f"Lbz_{i}_{j}b {mid} Bx_{i}_{j}b {fmt(0.5*d.l)}")
            L.append(f"Rbz_{i}_{j}b Bx_{i}_{j}b {top_node} {fmt(0.5*d.r)}")
            L.append(f"Cbz_{i}_{j}b {mid} {top_node} {fmt(2.0*d.c)}")
            i_bulk_cell.append(nm.vec_branch(f"Lbz_{i}_{j}a"))
        idx.i_bulk.append(i_bulk_cell)
        # top stack: St -> U1 -> ... -> T   (bottom-up)
        m_t = len(ce.top_layers)
        top_node_name = nm.top(i)
        nodes_t = ([nm.st(i)] if m_t > 0 else [top_node_name]) + [nm.u_layer(k, i) for k in range(1, m_t)] + ([top_node_name] if m_t > 0 else [])
        inst_t = f"Xsht_{i}"
        self._sheath_instance(inst_t, bulk_nodes[-1], nodes_t[0], ce, "t")
        idx.sheath_instances_t.append(inst_t)
        idx.i_sheath_t.append(nm.vec_subckt_branch(inst_t, "Vsc"))
        idx.node_st.append(nodes_t[0])
        for k, rc in enumerate(ce.top_layers):
            a, b = nodes_t[k], nodes_t[k + 1]
            lay = c.top_layers[k]
            L.append(f"Ct{k+1}_{i} {a} {b} {fmt(rc.c)}   ; {lay.name} eps_r={lay.material.eps_r} t={lay.t:.3e}")
            if rc.r is not None:
                L.append(f"Rt{k+1}_{i} {a} {b} {fmt(rc.r)}")
        idx.node_top.append(top_node_name)

    def _level_node(self, cell_idx0: int, level, idx: NetlistIndex) -> str:
        """Node of a radial level in a given cell (plasma-side node of layers, mid node of bulk, top conductor)."""
        c = self.model.mesh.cells[cell_idx0]
        i = cell_idx0 + 1
        if level.kind == "layer_b":
            m_b = len(c.bottom_layers)
            k = level.index  # 0-based layer
            return nm.sb(i) if k == m_b - 1 else nm.s_layer(k + 1, i)
        if level.kind == "bulk":
            return nm.bulk_mid(i, level.index + 1)
        if level.kind == "layer_t":
            k = level.index  # 0-based, bottom-up
            return nm.st(i) if k == 0 else nm.u_layer(k, i)
        return nm.top(i)

    def _boundary(self, be, idx: NetlistIndex) -> None:
        b = be.boundary
        i = b.index + 1  # boundary number (between cells i and i+1)
        L = self.lines
        L.append(f"* ---- boundary {i}: r={b.r:.5f} dr={b.dr:.4e} c={b.c_factor:.4e} H/m")
        kinds = []
        for k, le in enumerate(be.levels, start=1):
            lv = le.level
            if self.radial_kinds is not None and lv.kind not in self.radial_kinds:
                continue
            kinds.append(f"{lv.kind}:{lv.index}")
            n_from = self._level_node(b.index, lv, idx)
            n_to = self._level_node(b.index + 1, lv, idx)
            ea = f"Ea_{i}_{k}"
            chain_node = nm.chain(i, k)
            if self.flux_chain:
                L.append(f"Er_{i}_{k} {n_from} {ea} {chain_node} 0 1   ; {lv.kind} {lv.index} zeta={lv.zeta:.4e}")
            else:
                L.append(f"Ver_{i}_{k} {n_from} {ea} 0   ; (debug) no induced EMF")
            if lv.kind == "bulk":
                d = le.drude
                L.append(f"Lr_{i}_{k} {ea} Eb_{i}_{k} {fmt(d.l)}")
                L.append(f"Rr_{i}_{k} Eb_{i}_{k} Ec_{i}_{k} {fmt(d.r)}")
                L.append(f"Cr_{i}_{k} {ea} Ec_{i}_{k} {fmt(d.c)}")
                end = f"Ec_{i}_{k}"
            elif lv.kind in ("layer_b", "layer_t"):
                rc = le.rc
                L.append(f"Cr_{i}_{k} {ea} Ec_{i}_{k} {fmt(rc.c)}")
                if rc.r is not None:
                    L.append(f"Rr_{i}_{k} {ea} Ec_{i}_{k} {fmt(rc.r)}")
                end = f"Ec_{i}_{k}"
            else:
                end = ea
            L.append(f"Vr_{i}_{k} {end} {n_to} 0")
            if self.flux_chain:
                L.append(f"Fr_{i}_{k} 0 {chain_node} Vr_{i}_{k} 1")
            idx.i_radial[(b.index, k)] = nm.vec_branch(f"Vr_{i}_{k}")
        if self.flux_chain:
            for k, dl in enumerate(b.chain_dl, start=1):
                L.append(f"Lch_{i}_{k} {nm.chain(i, k-1)} {nm.chain(i, k)} {fmt(dl)}")
        idx.chain_levels[b.index] = kinds

    def _boundary_tie_top(self, be, idx: NetlistIndex) -> None:
        """Debug variant: no radial coupling; adjacent top conductor nodes are simply shorted."""
        i = be.boundary.index + 1
        self.lines.append(f"Vr_{i}_0 {nm.top(i)} {nm.top(i+1)} 0")
        idx.chain_levels[be.boundary.index] = []

    def _top_boundary(self, idx: NetlistIndex) -> None:
        """Connection of the outer end of the top conductor (spec 01 section 6)."""
        geo = self.project.geometry
        n = self.model.n_cells
        t_last = nm.top(n)
        ob = geo.outer_boundary
        L = self.lines
        L.append("* ---- outer boundary / top conductor termination")
        if geo.top.conductor == "ground":
            if ob.type == "impedance":
                node = t_last
                if ob.r:
                    L.append(f"Rob {t_last} nob {fmt(ob.r)}")
                    node = "nob"
                L.append(f"Cob {node} nob2 {fmt(ob.c)}")
                L.append("Vob nob2 0 0")
            else:
                L.append(f"Vob {t_last} 0 0")
            idx.i_outer = nm.vec_branch("Vob")
        else:
            # driven top electrode: the port node T is the outer end of the top conductor
            L.append(f"Vtop T {t_last} 0")
            if ob.type == "impedance":
                node = "T"
                if ob.r:
                    L.append(f"Rob T nob {fmt(ob.r)}")
                    node = "nob"
                L.append(f"Cob {node} 0 {fmt(ob.c)}")
            elif ob.type == "short":
                raise ValueError("outer_boundary 'short' is incompatible with a driven top electrode")
            idx.i_outer = nm.vec_branch("Vtop")

    def _source_line(self, port: str, node: str, ps: PortSpec) -> str:
        if self.mode == "tran":
            terms = []
            ramp = f"min(1,time/{fmt(self.tran.ramp_time)})*" if self.tran.ramp_time > 0 else ""
            for s in ps.sources:
                terms.append(f"{fmt(s.v)}*sin(2*pi*{fmt(s.f)}*time+{fmt(math.radians(s.phase_deg))})")
            expr = " + ".join(terms) if terms else "0"
            return f"Bsrc_{port} {node} 0 V = '{ramp}({expr})'"
        # AC: phasor sum of the sources at the analysed frequency (all sources for a frequency sweep)
        f0 = self.ac.frequency
        if self.ac.sweep is not None:
            ph = sum((s.v * cmath.exp(1j * math.radians(s.phase_deg)) for s in ps.sources), 0j)
        else:
            ph = sum((s.v * cmath.exp(1j * math.radians(s.phase_deg)) for s in ps.sources if abs(s.f - f0) <= 1e-6 * f0), 0j)
        amp, phase = abs(ph), math.degrees(cmath.phase(ph)) if abs(ph) > 0 else 0.0
        return f"Vsrc_{port} {node} 0 DC 0 AC {fmt(amp)} {phase:.6f}"

    def _port_chain(self, port: str, ps: PortSpec, idx: NetlistIndex) -> None:
        L = self.lines
        L.append(f"* ---- port {port}")
        src_node = f"n{port}s"
        L.append(self._source_line(port, src_node, ps))
        idx.i_source[port] = nm.vec_branch(f"Bsrc_{port}" if self.mode == "tran" else f"Vsrc_{port}")
        idx.node_source[port] = src_node
        cur = src_node
        if ps.l_feed > 0:
            L.append(f"Lfeed_{port} {cur} n{port}l {fmt(ps.l_feed)}")
            cur = f"n{port}l"
        if ps.blocking_c is not None:
            L.append(f"Cb_{port} {cur} n{port}c {fmt(ps.blocking_c)}")
            cur = f"n{port}c"
        L.append(f"Vcb_{port} {cur} {port} 0   ; current into the port node")
        idx.i_port[port] = nm.vec_branch(f"Vcb_{port}")
        if ps.c_stray > 0:
            L.append(f"Cstray_{port} {port} 0 {fmt(ps.c_stray)}")
        idx.ports.append(port)

    def _external(self, idx: NetlistIndex) -> None:
        circ = self.project.circuit
        for port in ("W", "T", "F"):
            if port in circ.ports:
                self._port_chain(port, circ.ports[port], idx)
        if circ.fr.type == "separate":
            t = circ.fr.tuning
            L = self.lines
            L.append(f"* ---- FR electrode tuning: {t.type}")
            if t.type == "tie":
                L.append("Vtie_F F W 0")
            elif t.type == "cap_to_gnd":
                node = "F"
                if t.r > 0:
                    L.append(f"Rtune_F F nFt {fmt(t.r)}")
                    node = "nFt"
                L.append(f"Ctune_F {node} 0 {fmt(t.c)}")
            elif t.type == "series_lc_to_gnd":
                L.append(f"Ltune_F F nFl {fmt(t.l)}")
                node = "nFl"
                if t.r > 0:
                    L.append(f"Rtune_F nFl nFt {fmt(t.r)}")
                    node = "nFt"
                L.append(f"Ctune_F {node} 0 {fmt(t.c)}")
            elif t.type == "coupling_to_w":
                node = "F"
                if t.l:
                    L.append(f"Lcpl_F F nFl {fmt(t.l)}")
                    node = "nFl"
                if t.r > 0:
                    L.append(f"Rcpl_F {node} nFr {fmt(t.r)}")
                    node = "nFr"
                L.append(f"Ccpl_F {node} W {fmt(t.c)}")
            elif t.type == "dc_bias":
                L.append(f"Lchoke_F F nFd {fmt(t.l or 1e-3)}")
                L.append(f"Vdc_F nFd 0 DC {fmt(t.v_dc or 0.0)}")
            if circ.c_wf.mode == "manual" and circ.c_wf.value:
                L.append(f"Cwf W F {fmt(circ.c_wf.value)}")
        if self.mode == "tran" and self.tran.bias_clamp:
            L = self.lines
            L.append("* ---- DC bias clamp (stiff at the operating point, released within a few tau)")
            for port, (v, nodes) in self.tran.bias_clamp.items():
                L.append(f"Vbias_{port} nb{port} 0 DC {fmt(v)}")
                for node in nodes:
                    L.append(f"Rf_{node} {node} nb{port} R = '1 + 1e12*(1-exp(-time/{fmt(self.tran.clamp_tau)}))'")
        if circ.extra_netlist.strip():
            problems = validate_user_netlist(circ.extra_netlist)
            if problems:
                raise ValueError("; ".join(problems))
            self.lines.append("* ---- user extra netlist")
            self.lines.append(circ.extra_netlist.strip())

    def _analysis(self) -> None:
        ng = self.project.numerics.ngspice
        L = self.lines
        L.append("* ---- analysis")
        opts = {"rshunt": "1e12", "reltol": "1e-3", "abstol": "1e-10", "vntol": "1e-6", "chgtol": "1e-16", "itl4": "50", "gmin": "1e-12"}
        if self.mode == "tran":
            opts["method"] = ng.method
            if ng.method == "gear":
                opts["maxord"] = "2"
        opts.update({k: str(v) for k, v in ng.options.items()})
        L.append(".options " + " ".join(f"{k}={v}" for k, v in opts.items()))
        if ng.use_klu:
            L.append(".options klu")
        if self.mode == "tran":
            t = self.tran
            L.append(f".tran {fmt(t.t_step)} {fmt(t.t_stop)} 0 {fmt(t.t_max)}")
        else:
            a = self.ac
            if a.sweep:
                f1, f2, ppd = a.sweep
                L.append(f".ac dec {int(ppd)} {fmt(f1)} {fmt(f2)}")
            else:
                L.append(f".ac lin 1 {fmt(a.frequency)} {fmt(a.frequency)}")
        L.append(".save all")
