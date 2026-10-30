"""Free-form schematic of the external circuit: pin geometry, connectivity, port derivation, SPICE lines.

Coordinates are integer grid units (the GUI draws 10 px per unit). Rotation is clockwise on screen (y grows
downwards). Two-terminal components span 6 units; their pins are the connection points. Pins and wire vertices
at the same grid point are connected, and a pin or wire end lying on a wire segment joins that wire (T junction).
Labels with the same name connect their nets.

The plasma block exposes one pin per driven electrode (W, F, T). The netlist builder joins each electrode pin's net
to the electrode node through a 0 V current sense source ``Vcb_<electrode>``, so the analysis sees the same
port quantities as in the port-chain mode.
"""

from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .project import CircuitSpec, PortSpec, Schematic, SchComponent, SchWire, SourceSpec

TWO_TERMINAL = ("R", "L", "C", "Vdc", "Vrf")
ELECTRODE_PIN_Y = {"T": -4, "W": 0, "F": 4}
PLASMA_SIZE = (12, 16)  # body width/height in grid units (left edge at the pins, vertically centred on W)
GROUND = "0"


# ---------------------------------------------------------------------------- geometry
def rotate(dx: int, dy: int, rot: int) -> tuple[int, int]:
    for _ in range((rot // 90) % 4):
        dx, dy = -dy, dx
    return dx, dy


def local_pins(ctype: str, electrodes: list[str] | None = None) -> list[tuple[str, int, int]]:
    """Pins (name, dx, dy) of a component type before rotation."""
    if ctype in ("R", "L", "C"):
        return [("1", -3, 0), ("2", 3, 0)]
    if ctype in ("Vdc", "Vrf"):
        return [("p", 0, -3), ("n", 0, 3)]
    if ctype in ("GND", "Label"):
        return [("a", 0, 0)]
    if ctype == "Plasma":
        return [(e, 0, ELECTRODE_PIN_Y[e]) for e in (electrodes or ["W"])]
    raise ValueError(f"unknown component type {ctype}")


def pins(comp: SchComponent, electrodes: list[str] | None = None) -> list[tuple[str, tuple[int, int]]]:
    rot = 0 if comp.type == "Plasma" else comp.rot
    out = []
    for name, dx, dy in local_pins(comp.type, electrodes):
        rx, ry = rotate(dx, dy, rot)
        out.append((name, (comp.x + rx, comp.y + ry)))
    return out


def on_segment(p: tuple[int, int], a: tuple[int, int], b: tuple[int, int]) -> bool:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) - (by - ay) * (px - ax) != 0:
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


# ---------------------------------------------------------------------------- connectivity
class _DSU:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def sanitize_label(text: str) -> str:
    t = re.sub(r"[^A-Za-z0-9_]", "_", text.strip())
    return t if t.lower().startswith("u_") else f"u_{t}"


@dataclass
class Extraction:
    electrodes: list[str]
    pin_net: dict[tuple[str, str], str]  # (component id, pin) -> net name
    nets: dict[str, list[tuple[str, str]]]  # net -> pins
    point_net: dict[tuple[int, int], str]  # every pin / wire vertex -> net
    electrode_net: dict[str, str]
    source_port: dict[str, str]  # Vrf id -> electrode
    bias_ports: list[str] = field(default_factory=list)
    clamp_nets: dict[str, list[str]] = field(default_factory=dict)  # bias port -> external nets DC-tied to it
    problems: list[str] = field(default_factory=list)
    junctions: list[tuple[int, int]] = field(default_factory=list)  # points where 3+ connections meet


def extract(sch: Schematic, electrodes: list[str]) -> Extraction:
    dsu = _DSU()
    comp_pins: list[tuple[SchComponent, str, tuple[int, int]]] = []
    for c in sch.components:
        for name, pt in pins(c, electrodes):
            comp_pins.append((c, name, pt))
            dsu.find(("pt", pt))
    degree: dict[tuple[int, int], int] = {}
    for _c, _n, pt in comp_pins:
        degree[pt] = degree.get(pt, 0) + 1
    points = {pt for _c, _n, pt in comp_pins}
    for w in sch.wires:
        pts = [tuple(p) for p in w.points]
        for a, b in zip(pts, pts[1:]):
            dsu.union(("pt", a), ("pt", b))
        points.update(pts)
        for end in (pts[0], pts[-1]):
            degree[end] = degree.get(end, 0) + 1
        for mid in pts[1:-1]:
            degree[mid] = degree.get(mid, 0) + 2
    for w in sch.wires:
        pts = [tuple(p) for p in w.points]
        for a, b in zip(pts, pts[1:]):
            for p in points:
                if p != a and p != b and on_segment(p, a, b):
                    dsu.union(("pt", a), ("pt", p))
                    degree[p] = degree.get(p, 0) + 2  # a wire passes through
    # labels with the same name join their nets
    label_root: dict[str, object] = {}
    for c, _n, pt in comp_pins:
        if c.type == "Label":
            name = sanitize_label(c.label or c.id)
            if name in label_root:
                dsu.union(label_root[name], ("pt", pt))
            else:
                label_root[name] = ("pt", pt)
    # group
    groups: dict[object, list[tuple[SchComponent, str, tuple[int, int]]]] = {}
    for item in comp_pins:
        groups.setdefault(dsu.find(("pt", item[2])), []).append(item)
    root_name: dict[object, str] = {}
    counter = 0
    used: set[str] = set()
    for root, items in groups.items():
        types = {c.type for c, _n, _p in items}
        if "GND" in types:
            name = GROUND
        else:
            labels = sorted(sanitize_label(c.label or c.id) for c, _n, _p in items if c.type == "Label")
            el = sorted({n for c, n, _p in items if c.type == "Plasma"}, key="WFT".index)
            if labels:
                name = labels[0]
            elif el:
                name = "sch_" + "".join(el).lower()
            else:
                counter += 1
                name = f"sch_{counter}"
        while name in used and name != GROUND:
            counter += 1
            name = f"sch_{counter}"
        used.add(name)
        root_name[root] = name
    pin_net: dict[tuple[str, str], str] = {}
    nets: dict[str, list[tuple[str, str]]] = {}
    for root, items in groups.items():
        for c, n, _p in items:
            pin_net[(c.id, n)] = root_name[root]
            nets.setdefault(root_name[root], []).append((c.id, n))
    point_net = {}
    for p in points:
        r = dsu.find(("pt", p))
        if r in root_name:
            point_net[p] = root_name[r]
    plasma = next(c for c in sch.components if c.type == "Plasma")
    electrode_net = {e: pin_net[(plasma.id, e)] for e in electrodes}
    ex = Extraction(electrodes=list(electrodes), pin_net=pin_net, nets=nets, point_net=point_net, electrode_net=electrode_net,
                    source_port={}, junctions=sorted(p for p, d in degree.items() if d >= 3))
    # wires that connect to nothing
    for k, w in enumerate(sch.wires):
        r = dsu.find(("pt", tuple(w.points[0])))
        if r not in root_name:
            ex.problems.append(f"ワイヤ {k + 1} はどのピンにも接続されていません")
    # unconnected pins
    for net, members in nets.items():
        if net == GROUND or len(members) > 1:
            continue
        cid, pin = members[0]
        comp = next(c for c in sch.components if c.id == cid)
        if comp.type == "Plasma":
            ex.problems.append(f"電極 {pin} は外部回路に接続されていません（開放）")
        elif comp.type != "Label":
            ex.problems.append(f"{cid} のピン {pin} が未接続です")
    # graph of two-terminal elements between nets
    edges: list[tuple[SchComponent, str, str]] = []
    for c in sch.components:
        if c.type in TWO_TERMINAL:
            a, b = (pin_net[(c.id, n)] for n, _dx, _dy in local_pins(c.type))
            edges.append((c, a, b))
    adj: dict[str, list[tuple[str, SchComponent]]] = {}
    for c, a, b in edges:
        adj.setdefault(a, []).append((b, c))
        adj.setdefault(b, []).append((a, c))
    net_electrodes: dict[str, list[str]] = {}
    for e, n in electrode_net.items():
        net_electrodes.setdefault(n, []).append(e)

    def nearest_electrode(start: str) -> Optional[str]:
        seen = {start}
        frontier = [start]
        while frontier:
            hits = sorted({e for n in frontier for e in net_electrodes.get(n, [])}, key="WFT".index)
            if hits:
                return hits[0]
            nxt = []
            for n in frontier:
                if n == GROUND:
                    continue
                for m, _c in adj.get(n, []):
                    if m not in seen:
                        seen.add(m)
                        nxt.append(m)
            frontier = nxt
        return None

    for c in sch.components:
        if c.type != "Vrf":
            continue
        if c.port is not None and c.port in electrodes:
            ex.source_port[c.id] = c.port
            continue
        e = nearest_electrode(pin_net[(c.id, "p")]) or nearest_electrode(pin_net[(c.id, "n")])
        if e is None:
            ex.problems.append(f"RF 電源 {c.id} はどの電極にもつながっていません（W として集計）")
            e = "W"
        ex.source_port[c.id] = e
    # DC connectivity (capacitors open, everything else conducting)
    dsu_dc = _DSU()
    for net in nets:
        dsu_dc.find(net)
    for c, a, b in edges:
        if c.type != "C":
            dsu_dc.union(a, b)
    for e in electrodes:
        n = electrode_net[e]
        comp_nets = [m for m in nets if dsu_dc.find(m) == dsu_dc.find(n)]
        if GROUND in comp_nets:
            continue
        others = [o for o in electrodes if o != e and dsu_dc.find(electrode_net[o]) == dsu_dc.find(n)]
        if others:
            if e == min([e] + others, key="WFT".index):
                ex.problems.append(f"電極 {', '.join([e] + others)} は直流的に互いにつながり接地から浮いています（自己バイアス反復は行いません）")
            continue
        if len(nets[n]) == 1:
            continue  # open electrode: no self-bias iteration needed (no DC path at all)
        ex.bias_ports.append(e)
        # nets reachable from the electrode net through wires, R and L only (no sources)
        reach = {n}
        frontier = [n]
        while frontier:
            m = frontier.pop()
            for o, c in adj.get(m, []):
                if c.type in ("R", "L") and o not in reach and o != GROUND:
                    reach.add(o)
                    frontier.append(o)
        ex.clamp_nets[e] = sorted(reach)
    return ex


def derive_ports(sch: Schematic, electrodes: list[str]) -> tuple[dict[str, PortSpec], dict[str, list[tuple[str, int]]], Extraction]:
    """Port specs (sources = tones of the RF sources accounted to each electrode) and the tone mapping."""
    ex = extract(sch, electrodes)
    ports: dict[str, PortSpec] = {e: PortSpec(sources=[]) for e in electrodes}
    mapping: dict[str, list[tuple[str, int]]] = {e: [] for e in electrodes}
    for c in sch.components:
        if c.type != "Vrf":
            continue
        e = ex.source_port[c.id]
        for k, tone in enumerate(c.tones):
            ports[e].sources.append(SourceSpec.model_validate(tone.model_dump()))
            mapping[e].append((c.id, k))
    return ports, mapping, ex


# ---------------------------------------------------------------------------- SPICE
def _fmt(x: float) -> str:
    return f"{x:.9g}"


@dataclass
class SpiceSchematic:
    lines: list[str]
    sources: dict[str, list[tuple[str, str, str]]]  # electrode -> [(node+, node-, branch vector)]
    dc_sources: list[tuple[str, str, str]]
    resistors: list[tuple[str, str, float]]
    extraction: Extraction


def spice_lines(circ: CircuitSpec, electrodes: list[str], mode: str, ramp_time: float = 0.0, ac_frequency: Optional[float] = None,
                ac_sweep: bool = False) -> SpiceSchematic:
    """Netlist lines of the schematic. RF tone values are taken from ``circ.ports`` (derived, possibly modified by the
    power drive), so that changes of the derived port sources take effect."""
    sch = circ.schematic
    _ports, mapping, ex = derive_ports(sch, electrodes)
    tone_value: dict[tuple[str, int], SourceSpec] = {}
    for e, items in mapping.items():
        srcs = circ.ports.get(e).sources if e in circ.ports else []
        for k, key in enumerate(items):
            tone_value[key] = srcs[k] if k < len(srcs) else next(c for c in sch.components if c.id == key[0]).tones[key[1]]
    L = ["* ---- external circuit (schematic)"]
    sources: dict[str, list[tuple[str, str, str]]] = {e: [] for e in electrodes}
    dc_sources = []
    resistors = []
    for c in sch.components:
        if c.type not in TWO_TERMINAL:
            continue
        names = [n for n, _dx, _dy in local_pins(c.type)]
        a, b = ex.pin_net[(c.id, names[0])], ex.pin_net[(c.id, names[1])]
        if c.type in ("R", "L", "C"):
            L.append(f"{c.id} {a} {b} {_fmt(c.value)}")
            if c.type == "R":
                resistors.append((a, b, float(c.value)))
        elif c.type == "Vdc":
            L.append(f"{c.id} {a} {b} DC {_fmt(c.value)}")
            dc_sources.append((a, b, f"{c.id.lower()}#branch"))
        else:  # Vrf
            tones = [tone_value[(c.id, k)] for k in range(len(c.tones))]
            if mode == "tran":
                ramp = f"min(1,time/{_fmt(ramp_time)})*" if ramp_time > 0 else ""
                expr = " + ".join(f"{_fmt(t.v)}*sin(2*pi*{_fmt(t.f)}*time+{_fmt(math.radians(t.phase_deg))})" for t in tones) or "0"
                name = f"Bsrc_{c.id}"
                L.append(f"{name} {a} {b} V = '{ramp}({expr})'")
            else:
                if ac_sweep:
                    ph = sum((t.v * cmath.exp(1j * math.radians(t.phase_deg)) for t in tones), 0j)
                else:
                    f0 = ac_frequency or 0.0
                    ph = sum((t.v * cmath.exp(1j * math.radians(t.phase_deg)) for t in tones if abs(t.f - f0) <= 1e-6 * f0), 0j)
                amp, phase = abs(ph), (math.degrees(cmath.phase(ph)) if abs(ph) > 0 else 0.0)
                name = f"Vsrc_{c.id}"
                L.append(f"{name} {a} {b} DC 0 AC {_fmt(amp)} {phase:.6f}")
            sources[ex.source_port[c.id]].append((a, b, f"{name.lower()}#branch"))
    for e in electrodes:
        L.append(f"Vcb_{e} {ex.electrode_net[e]} {e} 0   ; current into electrode {e}")
    return SpiceSchematic(lines=L, sources=sources, dc_sources=dc_sources, resistors=resistors, extraction=ex)


# ---------------------------------------------------------------------------- conversion from the port chains
class _Builder:
    def __init__(self):
        self.components: list[SchComponent] = []
        self.wires: list[SchWire] = []
        self.count: dict[str, int] = {}

    def name(self, prefix: str) -> str:
        self.count[prefix] = self.count.get(prefix, 0) + 1
        return f"{prefix}{self.count[prefix]}"

    def add(self, ctype: str, x: int, y: int, rot: int = 0, value: Optional[float] = None, cid: Optional[str] = None, **kw) -> SchComponent:
        prefix = {"R": "R", "L": "L", "C": "C", "Vdc": "V", "Vrf": "V", "GND": "GND", "Label": "N", "Plasma": "P"}[ctype]
        c = SchComponent(id=cid or self.name(prefix), type=ctype, x=x, y=y, rot=rot, value=value, **kw)
        self.components.append(c)
        return c

    def wire(self, *pts: tuple[int, int]) -> None:
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(pts) >= 2:
            self.wires.append(SchWire(points=[tuple(p) for p in pts]))

    def hang(self, x: int, y: int, elements: list[tuple[str, float, str]], to_ground: bool = True) -> int:
        """Vertical stack of elements from (x, y) downwards; returns the bottom y."""
        for ctype, value, cid in elements:
            if ctype in ("Vdc",):
                self.add(ctype, x, y + 3, 0, value, cid)
            else:
                self.add(ctype, x, y + 3, 90, value, cid)
            y += 6
        if to_ground:
            self.add("GND", x, y)
        return y

    def stack_up(self, x: int, y: int, elements: list[tuple[str, float, str]]) -> int:
        for ctype, value, cid in elements:
            self.add(ctype, x, y - 3, 90, value, cid)
            y -= 6
        return y


def _row(b: _Builder, circ: CircuitSpec, port: str, y: int) -> int:
    """Port chain laid out along y from x = 0; returns the x of the port-side end."""
    ps = circ.ports[port]
    x = 0
    b.add("Vrf", x, y + 3, 0, cid=f"V{port}", tones=[SourceSpec.model_validate(s.model_dump()) for s in ps.sources], port=port)
    b.add("GND", x, y + 6)
    b.wire((0, y), (4, y))  # room for the source labels
    x = 4
    gen = ps.generator

    def series(ctype: str, value: float, cid: str):
        nonlocal x
        b.wire((x, y), (x + 2, y))
        b.add(ctype, x + 5, y, 0, value, cid)
        x += 8

    def shunt(ctype: str, value: float, cid: str):
        b.hang(x, y, [(ctype, value, cid)])

    if gen.type == "l_match":
        series("R", gen.r_gen, f"Rgen{port}")
        l_val = gen.l or 100e-9
        c_val = gen.c or 100e-12
        if gen.topology in ("auto", "shunt_c_generator"):
            b.wire((x, y), (x + 2, y))
            x += 2
            shunt("C", c_val, f"Cmat{port}")
            series("L", l_val, f"Lmat{port}")
        else:
            series("L", l_val, f"Lmat{port}")
            b.wire((x, y), (x + 2, y))
            x += 2
            shunt("C", c_val, f"Cmat{port}")
    if ps.l_feed > 0:
        series("L", ps.l_feed, f"Lfeed{port}")
    if ps.blocking_c is not None:
        series("C", ps.blocking_c, f"Cb{port}")
    b.wire((x, y), (x + 4, y))
    x += 4
    if ps.c_stray > 0:
        shunt("C", ps.c_stray, f"Cstray{port}")
    return x


def schematic_from_ports(circ: CircuitSpec, electrodes: list[str]) -> Schematic:
    """Equivalent free-form schematic of the port-chain description (generator auto-tuning is replaced by the
    manual L/C values, or 100 nH / 100 pF placeholders when they are not set)."""
    b = _Builder()
    rows = {"W": 0, "T": -22, "F": 22}
    ends: dict[str, int] = {}
    for e in electrodes:
        if e in circ.ports and circ.ports[e].sources:
            ends[e] = _row(b, circ, e, rows[e])
    t = circ.fr.tuning
    f_items: list[tuple[str, float, str]] = []
    couple: list[tuple[str, float, str]] = []
    if "F" in electrodes and "F" not in ends:
        if t.type == "cap_to_gnd":
            f_items = ([("R", t.r, "Rtune")] if t.r > 0 else []) + [("C", t.c or 100e-12, "Ctune")]
        elif t.type == "series_lc_to_gnd":
            f_items = [("L", t.l or 1e-6, "Ltune")] + ([("R", t.r, "Rtune")] if t.r > 0 else []) + [("C", t.c or 100e-12, "Ctune")]
        elif t.type == "dc_bias":
            f_items = [("L", t.l or 1e-3, "Lchoke"), ("Vdc", t.v_dc or 0.0, "Vbias")]
        elif t.type == "coupling_to_w":
            couple = ([("L", t.l, "Lcpl")] if t.l else []) + ([("R", t.r, "Rcpl")] if t.r > 0 else []) + [("C", t.c or 100e-12, "Ccpl")]
    xp = max([ends.get(e, 0) for e in electrodes] + [20]) + 14
    b.add("Plasma", xp, 0, cid="PLASMA")
    for e in electrodes:
        py = ELECTRODE_PIN_Y[e]
        if e == "W":
            b.wire((ends.get("W", xp - 4), 0), (xp, 0))
        elif e in ends:
            b.wire((ends[e], rows[e]), (xp - 3, rows[e]), (xp - 3, py), (xp, py))
    if "F" in electrodes and "F" not in ends:
        yF = rows["F"]
        if t.type == "tie":
            b.wire((xp, 4), (xp - 6, 4), (xp - 6, 0))
        elif f_items:
            b.wire((xp, 4), (xp - 3, 4), (xp - 3, yF), (xp - 8, yF))
            b.hang(xp - 8, yF, f_items, to_ground=True)
        elif couple:
            b.wire((xp, 4), (xp - 3, 4), (xp - 3, yF), (xp - 8, yF))
            top = b.stack_up(xp - 8, yF, couple)
            b.wire((xp - 8, top), (xp - 8, 0))
        if circ.c_wf.mode == "manual" and circ.c_wf.value:
            if not (f_items or couple or t.type == "tie"):
                b.wire((xp, 4), (xp - 3, 4), (xp - 3, yF), (xp - 12, yF))
            elif f_items or couple:
                b.wire((xp - 8, yF), (xp - 12, yF))
            if t.type != "tie":
                top = b.stack_up(xp - 12, yF, [("C", circ.c_wf.value, "Cwf")])
                b.wire((xp - 12, top), (xp - 12, 0))
    return Schematic(components=b.components, wires=b.wires)


def default_schematic(electrodes: list[str]) -> Schematic:
    """Minimal schematic: 60 MHz source -> blocking capacitor -> W."""
    circ = CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=60e6, v=300.0)], blocking_c=2e-9)})
    return schematic_from_ports(circ, electrodes)
