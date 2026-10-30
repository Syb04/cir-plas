"""Assemble per-cell / per-boundary circuit element values from mesh + plasma state
(spec 01 section 3). Pure numbers; no netlist text here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .constants import EPS0
from .mesh import CellStructure, Mesh, RadialBoundary, RadialLevel
from .plasma import PlasmaState, drude_inductance, drude_resistance
from .project import MaterialProps, Project
from .sheath_models import SheathCellParams, make_sheath_params


@dataclass(frozen=True)
class RC:
    c: float
    r: Optional[float]  # None -> no resistor (perfect dielectric)


@dataclass(frozen=True)
class Drude:
    l: float
    r: float
    c: float


def layer_rc(mat: MaterialProps, t: float, area: float) -> RC:
    c = mat.eps_r * EPS0 * area / t
    r = t / (mat.sigma * area) if mat.sigma > 0 else None
    return RC(c=c, r=r)


def radial_layer_rc(mat: MaterialProps, t: float, dr: float, r_b: float) -> RC:
    area = 2.0 * np.pi * r_b * t
    c = mat.eps_r * EPS0 * area / dr
    r = dr / (mat.sigma * area) if mat.sigma > 0 else None
    return RC(c=c, r=r)


def drude_segment(n: float, nu_m: float, length: float, area: float) -> Drude:
    return Drude(l=drude_inductance(n, length, area), r=drude_resistance(n, nu_m, length, area), c=EPS0 * area / length)


@dataclass
class CellElements:
    cell: CellStructure
    n0: float
    ns: float
    bottom_layers: list[RC]
    sheath_b: SheathCellParams
    bulk: list[Drude]  # one per bulk layer (full-layer values)
    sheath_t: SheathCellParams
    top_layers: list[RC]
    v1_b: float  # RF sheath voltage amplitude estimate (linearization / nominal width)
    v1_t: float

    @property
    def c_lin_b(self) -> float:
        return self.sheath_b.linearized_capacitance(self.v1_b)

    @property
    def c_lin_t(self) -> float:
        return self.sheath_t.linearized_capacitance(self.v1_t)


@dataclass
class LevelElements:
    level: RadialLevel
    rc: Optional[RC] = None  # layer levels
    drude: Optional[Drude] = None  # bulk levels


@dataclass
class BoundaryElements:
    boundary: RadialBoundary
    levels: list[LevelElements]


@dataclass
class CircuitModel:
    project: Project
    mesh: Mesh
    plasma: PlasmaState
    cells: list[CellElements]
    boundaries: list[BoundaryElements]
    notes: dict = field(default_factory=dict)

    @property
    def n_cells(self) -> int:
        return len(self.cells)


def build_circuit_model(project: Project, mesh: Mesh, plasma: PlasmaState, v1_b, v1_t) -> CircuitModel:
    """v1_b / v1_t: per-cell (or scalar) RF sheath voltage amplitude estimates [V]."""
    n = mesh.n_cells
    v1_b = np.broadcast_to(np.asarray(v1_b, dtype=float), (n,))
    v1_t = np.broadcast_to(np.asarray(v1_t, dtype=float), (n,))
    spec = project.plasma.sheath
    mass = plasma.gas.mass_kg
    cells: list[CellElements] = []
    for i, c in enumerate(mesh.cells):
        ns = float(plasma.ns[i])
        n0 = float(plasma.n0[i])
        bottom = [layer_rc(L.material, L.t, c.area) for L in c.bottom_layers]
        top = [layer_rc(L.material, L.t, c.area) for L in c.top_layers]
        bulk = [drude_segment(n0, plasma.nu_m, float(dz), c.area) for dz in c.bulk_thickness]
        sh_b = make_sheath_params(c.area, ns, plasma.te, mass, spec)
        sh_t = make_sheath_params(c.area, ns, plasma.te, mass, spec)
        cells.append(CellElements(c, n0, ns, bottom, sh_b, bulk, sh_t, top, float(v1_b[i]), float(v1_t[i])))

    boundaries: list[BoundaryElements] = []
    for b in mesh.boundaries:
        a_i, b_i = b.index, b.index + 1
        n_b = 0.5 * (float(plasma.n0[a_i]) + float(plasma.n0[b_i]))
        levels: list[LevelElements] = []
        for lv in b.levels:
            if lv.kind in ("layer_b", "layer_t"):
                levels.append(LevelElements(lv, rc=radial_layer_rc(lv.material, lv.thickness, b.dr, b.r)))
            elif lv.kind == "bulk":
                area = 2.0 * np.pi * b.r * lv.thickness
                levels.append(LevelElements(lv, drude=drude_segment(n_b, plasma.nu_m, b.dr, area)))
            else:
                levels.append(LevelElements(lv))
        boundaries.append(BoundaryElements(b, levels))
    return CircuitModel(project=project, mesh=mesh, plasma=plasma, cells=cells, boundaries=boundaries)
