"""Project schema (pydantic v2) for CirPlas.

The JSON project file is the single input of a simulation. See
docs/spec/03_software_architecture.md section 4.1 for the layout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1

ConductorName = Literal["W", "F", "G"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- geometry
class MaterialProps(StrictModel):
    eps_r: float = Field(1.0, gt=0, description="relative permittivity")
    sigma: float = Field(0.0, ge=0, description="conductivity [S/m]")
    description: str = ""


MaterialRef = Union[str, MaterialProps]


class Layer(StrictModel):
    material: MaterialRef
    t: float = Field(..., gt=0, description="thickness [m]")
    radial_coupling: bool = Field(True, description="create radial branches inside this layer")


class Region(StrictModel):
    name: str
    r_out: float = Field(..., gt=0, description="outer radius [m]")
    conductor: ConductorName = "W"
    stack: list[Layer] = Field(default_factory=list, description="bottom stack, listed bottom to top")
    z_offset: float = Field(
        0.0,
        description="height of this region's bottom conductor surface relative to the reference region's [m] "
        "(negative = stepped-down pedestal so that thicker stacks stay level with the wafer)",
    )

    @property
    def stack_thickness(self) -> float:
        return sum(L.t for L in self.stack)

    @property
    def z_surface_abs(self) -> float:
        """Absolute height of the plasma-facing surface [m] (reference region datum = 0)."""
        return self.z_offset + self.stack_thickness


class GapSpec(StrictModel):
    """Gap between the reference region's plasma-facing surface and the top stack's plasma-facing surface.

    The top electrode is flat: other regions see gap(r) + (z_surface_ref - z_surface_region)."""

    type: Literal["constant", "table"] = "constant"
    value: Optional[float] = Field(None, gt=0, description="gap [m] (constant)")
    table: Optional[list[tuple[float, float]]] = Field(None, description="(r [m], gap [m]) pairs")
    reference_region: Optional[str] = Field(None, description="region whose surface defines the gap (default: first region)")

    @model_validator(mode="after")
    def _check(self) -> "GapSpec":
        if self.type == "constant" and self.value is None:
            raise ValueError("gap.value is required for type='constant'")
        if self.type == "table":
            if not self.table or len(self.table) < 2:
                raise ValueError("gap.table needs at least two (r, gap) points")
            rs = [p[0] for p in self.table]
            if any(b <= a for a, b in zip(rs, rs[1:])):
                raise ValueError("gap.table radii must be strictly increasing")
            if any(p[1] <= 0 for p in self.table):
                raise ValueError("gap.table gaps must be positive")
        return self


class TopSpec(StrictModel):
    conductor: Literal["ground", "driven"] = "ground"
    stack: list[Layer] = Field(default_factory=list, description="top stack, listed top (metal side) to bottom (plasma side)")


class OuterBoundary(StrictModel):
    type: Literal["short", "open", "impedance"] = "short"
    r: Optional[float] = Field(None, ge=0, description="series resistance [ohm] (impedance)")
    c: Optional[float] = Field(None, gt=0, description="capacitance [F] (impedance)")


class Geometry(StrictModel):
    regions: list[Region]
    gap: GapSpec
    top: TopSpec = Field(default_factory=TopSpec)
    outer_boundary: OuterBoundary = Field(default_factory=OuterBoundary)

    @model_validator(mode="after")
    def _check(self) -> "Geometry":
        if not self.regions:
            raise ValueError("at least one region is required")
        names = [r.name for r in self.regions]
        if len(set(names)) != len(names):
            raise ValueError("region names must be unique")
        radii = [r.r_out for r in self.regions]
        if any(b <= a for a, b in zip(radii, radii[1:])):
            raise ValueError("region r_out must be strictly increasing")
        if self.gap.type == "table":
            r_last = self.gap.table[-1][0]
            if r_last < radii[-1] - 1e-12:
                raise ValueError("gap.table must extend to the outer wall radius")
        if self.gap.reference_region is not None and self.gap.reference_region not in names:
            raise ValueError(f"gap.reference_region '{self.gap.reference_region}' is not a region")
        z_ref = self.reference_region().z_surface_abs
        for reg in self.regions:
            r_mid = 0.5 * (reg.r_out + (self.regions[self.regions.index(reg) - 1].r_out if self.regions.index(reg) > 0 else 0.0))
            if self.gap_at(r_mid) + z_ref - reg.z_surface_abs <= 0:
                raise ValueError(f"region '{reg.name}' surface reaches the top electrode (check z_offset / stack thickness)")
        return self

    def reference_region(self) -> Region:
        if self.gap.reference_region is None:
            return self.regions[0]
        return next(r for r in self.regions if r.name == self.gap.reference_region)

    def local_gap(self, region: Region, r: float) -> float:
        """Gap between this region's surface and the top stack surface at radius r."""
        return self.gap_at(r) + self.reference_region().z_surface_abs - region.z_surface_abs

    @property
    def r_wall(self) -> float:
        return self.regions[-1].r_out

    def region_bounds(self) -> list[tuple[float, float]]:
        r_in = 0.0
        out = []
        for reg in self.regions:
            out.append((r_in, reg.r_out))
            r_in = reg.r_out
        return out

    def gap_at(self, r: float) -> float:
        if self.gap.type == "constant":
            return float(self.gap.value)
        import numpy as np

        rs = np.array([p[0] for p in self.gap.table])
        gs = np.array([p[1] for p in self.gap.table])
        return float(np.interp(r, rs, gs))


# --------------------------------------------------------------------------- plasma
class DensitySpec(StrictModel):
    model: Literal["uniform", "parabolic", "table", "file"] = "uniform"
    n0: float = Field(1.0e16, gt=0, description="center (or uniform) electron density [m^-3]")
    alpha: float = Field(0.0, ge=0, lt=1, description="parabolic: n0*(1 - alpha*(r/R)^2)")
    table: Optional[list[tuple[float, float]]] = Field(None, description="(r [m], n [m^-3]) pairs")
    path: Optional[str] = Field(None, description="CSV file with r,n columns")

    @model_validator(mode="after")
    def _check(self) -> "DensitySpec":
        if self.model == "table" and (not self.table or len(self.table) < 2):
            raise ValueError("density.table needs at least two points")
        if self.model == "file" and not self.path:
            raise ValueError("density.path is required for model='file'")
        return self


class AutoOrValue(StrictModel):
    mode: Literal["auto", "manual"] = "auto"
    value: Optional[float] = None

    @model_validator(mode="after")
    def _check(self) -> "AutoOrValue":
        if self.mode == "manual" and self.value is None:
            raise ValueError("value is required when mode='manual'")
        return self


class StochasticSpec(StrictModel):
    model: Literal["hardwall", "off"] = "hardwall"
    kappa: float = Field(1.0, ge=0, description="multiplier on the hard-wall stochastic resistance")


class SheathSpec(StrictModel):
    model: Literal["matrix", "child"] = "matrix"
    alpha_min: float = Field(2.0, gt=0, description="minimum sheath width in Debye lengths")
    stochastic: StochasticSpec = Field(default_factory=StochasticSpec)
    ohmic_sheath: bool = False
    kappa_sh: float = Field(1.0, ge=0)
    electron_current: Literal["bsource", "diode"] = Field(
        "bsource", description="SPICE realization of the Boltzmann electron current: smooth-exponential B source (default) or parallel diodes"
    )
    smoothing: float = Field(0.1, gt=0, le=1, description="softplus width for Q(V) guard, in units of Vmin")
    linear_fixed_width: Optional[float] = Field(
        None, gt=0, description="validation aid: in AC mode use C = eps0 A / width instead of the linearized Q(V)"
    )
    cap_form: Literal["charge", "capacitance", "ecf"] = Field(
        "ecf", description="SPICE realization of the nonlinear sheath capacitor: 'ecf' (charge via auxiliary capacitor and "
        "controlled sources), 'charge' (ngspice Q='...' device) or 'capacitance' (C='...' device)"
    )


class PlasmaSpec(StrictModel):
    gas: str = "ar"
    pressure_pa: float = Field(..., gt=0)
    gas_temperature_k: float = Field(400.0, gt=0)
    density: DensitySpec = Field(default_factory=DensitySpec)
    te: AutoOrValue = Field(default_factory=AutoOrValue, description="electron temperature [eV]")
    sheath: SheathSpec = Field(default_factory=SheathSpec)
    h_l: AutoOrValue = Field(default_factory=AutoOrValue)
    nu_m: AutoOrValue = Field(default_factory=AutoOrValue, description="electron momentum transfer frequency [1/s]")


# --------------------------------------------------------------------------- circuit
class SourceSpec(StrictModel):
    f: float = Field(..., gt=0, description="frequency [Hz]")
    v: float = Field(..., ge=0, description="voltage amplitude [V] (initial guess when power is given)")
    phase_deg: float = 0.0
    power: Optional[float] = Field(None, gt=0, description="delivered power target [W]; the amplitude is then iterated")


class GeneratorSpec(StrictModel):
    """50-ohm generator with an L-type matching network between the generator and the port chain
    (spec 01 section 5.1, Phase 2). With auto=True the elements are tuned from the AC port impedance."""

    type: Literal["none", "l_match"] = "none"
    r_gen: float = Field(50.0, gt=0, description="generator source impedance [ohm]")
    auto: bool = Field(True, description="tune L/C from the AC impedance of the port at the fundamental")
    l: Optional[float] = Field(None, gt=0, description="series inductance [H] (manual)")
    c: Optional[float] = Field(None, gt=0, description="shunt capacitance [F] (manual)")
    topology: Literal["auto", "shunt_c_generator", "shunt_c_load"] = Field(
        "auto", description="where the shunt capacitor sits: on the generator side (load R < r_gen) or on the load side (load R > r_gen)"
    )


class PortSpec(StrictModel):
    generator: GeneratorSpec = Field(default_factory=GeneratorSpec)
    sources: list[SourceSpec] = Field(default_factory=list)
    blocking_c: Optional[float] = Field(None, gt=0, description="series blocking capacitor [F]")
    l_feed: float = Field(0.0, ge=0, description="series feed inductance [H]")
    c_stray: float = Field(0.0, ge=0, description="stray capacitance to ground [F]")
    dc_bias_guess: Optional[float] = Field(None, description="initial guess of the blocking-cap DC voltage [V]")


class FrTuningSpec(StrictModel):
    type: Literal["none", "tie", "cap_to_gnd", "series_lc_to_gnd", "coupling_to_w", "dc_bias", "source"] = "none"
    c: Optional[float] = Field(None, gt=0)
    l: Optional[float] = Field(None, gt=0)
    r: float = Field(0.0, ge=0)
    v_dc: Optional[float] = None


class FrSpec(StrictModel):
    type: Literal["esc_coupled", "separate"] = "esc_coupled"
    tuning: FrTuningSpec = Field(default_factory=FrTuningSpec)


SchType = Literal["R", "L", "C", "Vdc", "Vrf", "GND", "Label", "Plasma"]


class SchComponent(StrictModel):
    """A component of the free-form schematic (positions in grid units, see core/schematic.py)."""

    id: str = Field(..., description="instance name; R/L/C/Vdc names start with their SPICE letter")
    type: SchType
    x: int = 0
    y: int = 0
    rot: Literal[0, 90, 180, 270] = 0
    value: Optional[float] = Field(None, description="R [ohm], L [H], C [F], Vdc [V]")
    tones: list[SourceSpec] = Field(default_factory=list, description="Vrf: frequency components")
    port: Optional[Literal["W", "F", "T"]] = Field(None, description="Vrf: electrode it is accounted to (None = nearest)")
    label: Optional[str] = Field(None, description="Label: net name (stored as u_<label> in the netlist)")


class SchWire(StrictModel):
    points: list[tuple[int, int]] = Field(..., min_length=2, description="polyline vertices in grid units")


class Schematic(StrictModel):
    components: list[SchComponent] = Field(default_factory=list)
    wires: list[SchWire] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "Schematic":
        ids = [c.id for c in self.components]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"schematic: duplicate component names {sorted(dup)}")
        if sum(1 for c in self.components if c.type == "Plasma") != 1:
            raise ValueError("schematic: exactly one plasma block is required")
        letters = {"R": "R", "L": "L", "C": "C", "Vdc": "V"}
        for c in self.components:
            if c.type in letters:
                if not c.id or c.id[0].upper() != letters[c.type] or not c.id.replace("_", "").isalnum():
                    raise ValueError(f"schematic: {c.type} name '{c.id}' must start with '{letters[c.type]}' and be alphanumeric")
                if c.value is None:
                    raise ValueError(f"schematic: {c.id} has no value")
                if c.type != "Vdc" and c.value <= 0:
                    raise ValueError(f"schematic: {c.id} value must be positive")
            if c.type == "Vrf":
                if not c.id.replace("_", "").isalnum():
                    raise ValueError(f"schematic: source name '{c.id}' must be alphanumeric")
                if not c.tones:
                    raise ValueError(f"schematic: RF source {c.id} has no frequency component")
            if c.type == "Label" and not (c.label or "").strip():
                raise ValueError(f"schematic: label {c.id} has no name")
        if not any(c.type == "Vrf" for c in self.components):
            raise ValueError("schematic: at least one RF source is required")
        return self


class CircuitSpec(StrictModel):
    mode: Literal["ports", "schematic"] = Field(
        "ports", description="ports: external circuit from the per-port chains below; schematic: free-form schematic")
    ports: dict[str, PortSpec] = Field(default_factory=dict, description="derived from the schematic in schematic mode")
    fr: FrSpec = Field(default_factory=FrSpec)
    c_wf: AutoOrValue = Field(default_factory=AutoOrValue, description="W-F capacitance (separate FR electrode only)")
    schematic: Optional[Schematic] = None
    extra_netlist: str = ""


# --------------------------------------------------------------------------- numerics
class MeshSpec(StrictModel):
    n_r: dict[str, int] = Field(..., description="radial cells per region")
    grading: dict[str, float] = Field(default_factory=dict, description="geometric ratio of adjacent cell widths (>1 refines)")
    refine: dict[str, Literal["inner", "outer", "none"]] = Field(default_factory=dict)
    n_z: int = Field(1, ge=1, description="bulk layers")
    z_grading: float = Field(1.0, ge=1.0, description="geometric ratio of bulk layer thickness away from the sheaths")
    sheath_nominal: Optional[float] = Field(None, gt=0, description="override nominal sheath width [m] used for mesh heights")


class TranSpec(StrictModel):
    pts_per_cycle: int = Field(200, ge=20)
    ramp_cycles: float = Field(3.0, ge=0)
    cycles_min: int = Field(10, ge=1)
    cycles_max: int = Field(200, ge=1)
    meas_cycles: int = Field(4, ge=1)
    pss_tol: float = Field(1e-3, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "TranSpec":
        if self.cycles_max < self.cycles_min:
            raise ValueError("cycles_max must be >= cycles_min")
        return self


class DcBiasSpec(StrictModel):
    method: Literal["two_stage", "scaled_cb_only", "none"] = "two_stage"
    kappa_b: float = Field(0.02, gt=0, le=1)
    cycles_a: int = Field(30, ge=1)
    tol: float = Field(1e-3, gt=0)
    max_iter: int = Field(4, ge=0)


class DensityIterationSpec(StrictModel):
    enabled: bool = False
    relax: float = Field(0.4, gt=0, le=1)
    tol: float = Field(1e-3, gt=0)
    max_iter: int = Field(30, ge=1)
    refine_with_tran: bool = False


class AcSweepSpec(StrictModel):
    f_start: float = Field(1e6, gt=0)
    f_stop: float = Field(1e9, gt=0)
    points_per_decade: int = Field(50, ge=1)


class NgspiceSpec(StrictModel):
    backend: Literal["auto", "shared", "batch"] = "auto"
    library_path: Optional[str] = None
    executable: Optional[str] = None
    options: dict[str, Union[str, float, int]] = Field(default_factory=dict)
    use_klu: bool = True
    method: Literal["gear", "trap"] = "gear"


class NumericsSpec(StrictModel):
    mesh: MeshSpec
    analysis: Literal["ac", "tran", "sc"] = "tran"
    tran: TranSpec = Field(default_factory=TranSpec)
    dc_bias: DcBiasSpec = Field(default_factory=DcBiasSpec)
    density_iteration: DensityIterationSpec = Field(default_factory=DensityIterationSpec)
    ngspice: NgspiceSpec = Field(default_factory=NgspiceSpec)
    ac_sweep: Optional[AcSweepSpec] = None
    sheath_voltage_guess: Optional[float] = Field(None, gt=0, description="RF sheath voltage amplitude guess [V] for linearization/mesh")


class OutputsSpec(StrictModel):
    save_waveforms: bool = True
    save_raw_timestep: bool = False


# --------------------------------------------------------------------------- project
class Project(StrictModel):
    schema_version: int = SCHEMA_VERSION
    name: str = "untitled"
    geometry: Geometry
    plasma: PlasmaSpec
    circuit: CircuitSpec
    numerics: NumericsSpec
    outputs: OutputsSpec = Field(default_factory=OutputsSpec)

    @model_validator(mode="after")
    def _check(self) -> "Project":
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version} (expected {SCHEMA_VERSION})")
        conductors = {r.conductor for r in self.geometry.regions}
        if "W" not in conductors:
            raise ValueError("at least one region must use the wafer electrode 'W'")
        if self.circuit.mode == "schematic":
            if self.circuit.schematic is None:
                raise ValueError("circuit.mode='schematic' requires circuit.schematic")
            from .schematic import derive_ports

            self.circuit.ports = derive_ports(self.circuit.schematic, self.electrodes())[0]
            self.circuit.fr.type = "separate" if "F" in conductors else "esc_coupled"
        if self.circuit.fr.type == "separate" and "F" not in conductors:
            raise ValueError("circuit.fr.type='separate' requires a region with conductor 'F'")
        if self.circuit.fr.type == "esc_coupled" and "F" in conductors:
            raise ValueError("regions with conductor 'F' require circuit.fr.type='separate'")
        allowed_ports = {"W", "F", "T"}
        for p in self.circuit.ports:
            if p not in allowed_ports:
                raise ValueError(f"unknown port '{p}' (allowed: W, F, T)")
        if "W" not in self.circuit.ports:
            raise ValueError("circuit.ports must define port 'W'")
        if "F" in self.circuit.ports and self.circuit.fr.type != "separate":
            raise ValueError("port 'F' is only valid with circuit.fr.type='separate'")
        if "T" in self.circuit.ports and self.geometry.top.conductor != "driven":
            raise ValueError("port 'T' is only valid with geometry.top.conductor='driven'")
        if self.geometry.top.conductor == "driven" and "T" not in self.circuit.ports:
            raise ValueError("geometry.top.conductor='driven' requires circuit.ports.T")
        if self.circuit.fr.type == "separate" and self.circuit.fr.tuning.type == "source" and "F" not in self.circuit.ports:
            raise ValueError("fr.tuning.type='source' requires circuit.ports.F with sources")
        region_names = {r.name for r in self.geometry.regions}
        for name in self.numerics.mesh.n_r:
            if name not in region_names:
                raise ValueError(f"numerics.mesh.n_r refers to unknown region '{name}'")
        for name in region_names:
            if name not in self.numerics.mesh.n_r:
                raise ValueError(f"numerics.mesh.n_r is missing region '{name}'")
        return self

    # ---------------------------------------------------------------- helpers
    def electrodes(self) -> list[str]:
        """Driven-electrode nodes present in the geometry (W always, F for a separate FR, T for a driven top)."""
        conductors = {r.conductor for r in self.geometry.regions}
        return ["W"] + (["F"] if "F" in conductors else []) + (["T"] if self.geometry.top.conductor == "driven" else [])

    def bias_ports(self) -> list[str]:
        """Ports whose electrode is DC-isolated by series capacitors (self-bias develops there)."""
        if self.circuit.mode == "schematic":
            from .schematic import extract

            return extract(self.circuit.schematic, self.electrodes()).bias_ports
        return [port for port, ps in self.circuit.ports.items() if ps.blocking_c is not None]

    def frequencies(self) -> list[float]:
        fs: set[float] = set()
        for port in self.circuit.ports.values():
            for s in port.sources:
                fs.add(float(s.f))
        return sorted(fs)

    def f_max(self) -> float:
        fs = self.frequencies()
        return max(fs) if fs else 0.0

    def base_period(self) -> float:
        """Fundamental period = 1/gcd of all source frequencies (frequencies rounded to 1 Hz)."""
        fs = [int(round(f)) for f in self.frequencies()]
        if not fs:
            raise ValueError("no RF sources defined")
        import math

        g = fs[0]
        for f in fs[1:]:
            g = math.gcd(g, f)
        return 1.0 / g

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=indent, ensure_ascii=False)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]


def set_source_field(d: dict, port: str, k: int, key: str, value) -> None:
    """Set field ``key`` of source ``k`` of ``port`` in a project dict (both circuit modes).

    In schematic mode the port sources are derived from the RF source components, so the value is written to the
    corresponding tone of the component instead."""
    circ = d["circuit"]
    if circ.get("mode", "ports") == "schematic":
        from .schematic import derive_ports

        sch = Schematic.model_validate(circ["schematic"])
        conductors = {r["conductor"] for r in d["geometry"]["regions"]}
        electrodes = ["W"] + (["F"] if "F" in conductors else []) + (["T"] if d["geometry"]["top"]["conductor"] == "driven" else [])
        mapping = derive_ports(sch, electrodes)[1]
        comp_id, tone = mapping[port][k]
        comp = next(c for c in circ["schematic"]["components"] if c["id"] == comp_id)
        comp["tones"][tone][key] = value
    else:
        circ["ports"][port]["sources"][k][key] = value


def apply_path(d: dict, path: str, value) -> None:
    """Set a dotted path in a project dict; ``circuit.ports.<P>.sources.<k>.<key>`` also works in schematic mode."""
    import re

    m = re.fullmatch(r"circuit\.ports\.([WFT])\.sources\.(\d+)\.(\w+)", path)
    if m and d["circuit"].get("mode", "ports") == "schematic":
        set_source_field(d, m.group(1), int(m.group(2)), m.group(3), value)
        return
    node = d
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(keys[-1])] = value
    else:
        node[keys[-1]] = value


def load_project(path: str | Path) -> Project:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Project.model_validate(data)


def save_project(project: Project, path: str | Path) -> None:
    Path(path).write_text(project.to_json(), encoding="utf-8")


def default_project() -> Project:
    """Representative 300 mm CCP (spec 00 section 2.2): FR fed from the ESC by capacitive coupling."""
    return Project(
        name="demo_300mm_60MHz",
        geometry=Geometry(
            regions=[
                Region(name="wafer", r_out=0.150, conductor="W",
                       stack=[Layer(material="al2o3", t=1.0e-3), Layer(material="si_wafer_p", t=0.775e-3)]),
                Region(name="edge_gap", r_out=0.151, conductor="W",
                       stack=[Layer(material="al2o3", t=1.0e-3)]),
                Region(name="fr", r_out=0.180, conductor="W", z_offset=-(7.0e-3 - 1.775e-3),
                       stack=[Layer(material="al2o3", t=1.0e-3), Layer(material="quartz", t=2.0e-3), Layer(material="si_fr", t=4.0e-3)]),
                Region(name="outer", r_out=0.250, conductor="G", z_offset=-(6.0e-3 - 1.775e-3),
                       stack=[Layer(material="quartz", t=6.0e-3)]),
            ],
            gap=GapSpec(type="constant", value=0.030),
            top=TopSpec(conductor="ground", stack=[Layer(material="si_fr", t=6.0e-3)]),
            outer_boundary=OuterBoundary(type="short"),
        ),
        plasma=PlasmaSpec(
            gas="ar", pressure_pa=6.67, gas_temperature_k=400.0,
            density=DensitySpec(model="parabolic", n0=1.0e16, alpha=0.3),
        ),
        circuit=CircuitSpec(
            ports={"W": PortSpec(sources=[SourceSpec(f=60e6, v=400.0)], blocking_c=2.0e-9, l_feed=20e-9, c_stray=300e-12)},
            fr=FrSpec(type="esc_coupled"),
        ),
        numerics=NumericsSpec(
            mesh=MeshSpec(n_r={"wafer": 24, "edge_gap": 1, "fr": 8, "outer": 6}, grading={"wafer": 1.15, "fr": 1.1}, n_z=1),
            analysis="tran",
        ),
    )
