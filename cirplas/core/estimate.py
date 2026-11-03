"""Problem size and run-time estimate (spec 04 section 4.4 "見積り", ETA of the run pane).

The number of unknowns is counted from the generated AC netlist (MNA: one equation per node plus one per branch
current of V/L/E/H/B-voltage elements, subcircuits expanded). Run times use per-unknown cost coefficients that the
GUI calibrates from finished runs (`calibrate`); the defaults are the M1 measurements (02 section 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# seconds per unknown per AC solve, per unknown per transient time point
DEFAULT_COEFFICIENTS = {"ac": 6.0e-5, "tran": 1.9e-6}
_BRANCH_ELEMENTS = ("v", "l", "e", "h")
_FOUR_NODE = ("e", "g")


def _element_nodes(tokens: list[str], subckts: dict) -> tuple[list[str], int, str]:
    """Nodes of one element line, the number of its branch unknowns and the subcircuit name (X lines)."""
    kind = tokens[0][0].lower()
    if kind == "x":
        for k in range(1, len(tokens)):
            if tokens[k].lower() in subckts:
                return tokens[1:k], 0, tokens[k].lower()
        return [], 0, ""
    if kind in _FOUR_NODE:
        return tokens[1:5], int(kind == "e"), ""
    if kind == "b":
        # "Bx n1 n2 V = expr" is a voltage source (branch unknown), "I = expr" a current source
        return tokens[1:3], int("".join(tokens[3:]).lower().startswith("v")), ""
    if kind == "d":
        return tokens[1:3], 0, ""
    if kind == "k":
        return [], 0, ""
    return tokens[1:3], int(kind in _BRANCH_ELEMENTS), ""


def count_unknowns(netlist: str) -> int:
    """MNA unknowns of a flat-or-hierarchical SPICE netlist (ground excluded)."""
    lines = []
    for raw in netlist.splitlines():
        s = raw.split(";", 1)[0].strip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("+") and lines:
            lines[-1] += " " + s[1:]
        else:
            lines.append(s)
    # subcircuit bodies
    subckts: dict[str, dict] = {}
    top: list[list[str]] = []
    current = None
    for s in lines:
        tok = s.replace("(", " ").replace(")", " ").replace(",", " ").split()
        head = tok[0].lower()
        if head == ".subckt":
            ports = [t for t in tok[2:] if "=" not in t]
            current = {"ports": [p.lower() for p in ports], "elements": []}
            subckts[tok[1].lower()] = current
        elif head == ".ends":
            current = None
        elif head.startswith("."):
            continue
        elif current is not None:
            current["elements"].append(tok)
        else:
            top.append(tok)
    cache: dict[str, int] = {}

    def internal(name: str) -> int:
        """Internal nodes + branch unknowns of one instance of subcircuit `name`."""
        if name in cache:
            return cache[name]
        sub = subckts[name]
        nodes, extra = set(), 0
        for tok in sub["elements"]:
            ns, br, child = _element_nodes(tok, subckts)
            nodes.update(n.lower() for n in ns)
            extra += br + (internal(child) if child else 0)
        nodes -= set(sub["ports"]) | {"0", "gnd"}
        cache[name] = len(nodes) + extra
        return cache[name]

    nodes, extra = set(), 0
    for tok in top:
        ns, br, child = _element_nodes(tok, subckts)
        nodes.update(n.lower() for n in ns)
        extra += br + (internal(child) if child else 0)
    nodes -= {"0", "gnd"}
    return len(nodes) + extra


@dataclass
class Estimate:
    unknowns: int
    cells: int
    layers: int
    seconds: dict[str, float] = field(default_factory=dict)  # mode -> estimated wall time [s]
    tran_points: int = 0
    points_per_period: float = 0.0  # transient time points per base period


def expected_tran_cycles(project) -> int:
    """Typical number of simulated base periods: the bias iteration re-runs cycles_min a few times, then the
    steady-state extension adds about half of it."""
    t = project.numerics.tran
    bias = project.bias_ports() if project.numerics.dc_bias.method != "none" else []
    n_bias = min(project.numerics.dc_bias.max_iter + 1, 3) if bias else 1
    return int(min(t.cycles_max * n_bias, t.cycles_min * (n_bias + 0.5)))


def estimate(project, coefficients: dict | None = None) -> Estimate:
    """Unknowns and wall-time estimates for AC, TRAN and SC (density iteration) of a validated project."""
    from .analysis.common import build_model, initial_estimates
    from .mesh import build_radial_mesh
    from .netlist.builder import AcSettings, NetlistBuilder

    coef = {**DEFAULT_COEFFICIENTS, **(coefficients or {})}
    rm = build_radial_mesh(project.geometry, project.numerics.mesh)
    model = build_model(project, initial_estimates(project, rm.n_cells))
    try:
        netlist = NetlistBuilder(model, "ac", ac=AcSettings(frequency=project.f_max())).build()[0]
    except ValueError:  # e.g. auto-tuned matching values not known before the AC pre-solve
        d = project.model_dump()
        for ps in d["circuit"]["ports"].values():
            ps["generator"]["type"] = "none"
        model = build_model(type(project).model_validate(d), initial_estimates(project, rm.n_cells))
        netlist = NetlistBuilder(model, "ac", ac=AcSettings(frequency=project.f_max())).build()[0]
    n = count_unknowns(netlist)
    t_ac = coef["ac"] * n * 6  # quasi-linear iteration: ~6 AC solves
    per_period = project.numerics.tran.pts_per_cycle * max(project.base_period() * project.f_max(), 1.0)  # steps follow f_max
    points = int(expected_tran_cycles(project) * per_period)
    t_tran = t_ac + coef["tran"] * n * points
    di = project.numerics.density_iteration
    t_sc = t_ac * min(di.max_iter, 12) + (t_tran * (di.tran_refine_iter + 1) if di.refine_with_tran else 0.0)
    return Estimate(unknowns=n, cells=rm.n_cells, layers=project.numerics.mesh.n_z, tran_points=points, points_per_period=per_period,
                    seconds={"ac": t_ac, "tran": t_tran, "sc": t_sc})


def calibrate(coefficients: dict, mode: str, unknowns: int, wall_s: float, tran_points: int = 0, weight: float = 0.5) -> dict:
    """Update the cost coefficients from a finished run (exponential average in log space)."""
    import math

    coef = {**DEFAULT_COEFFICIENTS, **coefficients}
    if unknowns <= 0 or wall_s <= 0:
        return coef
    if mode == "ac":
        new = wall_s / (unknowns * 6)
        key = "ac"
    elif mode == "tran" and tran_points > 0:
        new = max(wall_s - coef["ac"] * unknowns * 6, 0.1 * wall_s) / (unknowns * tran_points)
        key = "tran"
    else:
        return coef
    coef[key] = math.exp((1 - weight) * math.log(coef[key]) + weight * math.log(new))
    return coef


def format_seconds(s: float) -> str:
    if s < 1:
        return "< 1 秒"
    if s < 90:
        return f"約 {s:.0f} 秒"
    if s < 5400:
        return f"約 {s / 60:.0f} 分"
    return f"約 {s / 3600:.1f} 時間"
