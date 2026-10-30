"""Mesh generation (spec 01 section 2 and 4): graded radial cells, per-cell axial
structure and the radial-branch levels / flux-chain inductances per cell boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from .constants import MU0
from .materials import resolve_material
from .project import Geometry, Layer, MaterialProps, MeshSpec, Project

RefineMode = Literal["inner", "outer", "none"]


# ------------------------------------------------------------------ radial mesh
def graded_widths(n: int, total: float, ratio: float, refine: RefineMode) -> np.ndarray:
    """Cell widths summing to `total`; adjacent widths differ by `ratio`, smallest at the refined end."""
    if n < 1:
        raise ValueError("n must be >= 1")
    if n == 1 or refine == "none" or abs(ratio - 1.0) < 1e-12:
        return np.full(n, total / n)
    w0 = total * (ratio - 1.0) / (ratio**n - 1.0)
    w = w0 * ratio ** np.arange(n)  # increasing
    if refine == "outer":
        w = w[::-1]  # smallest cells at the outer edge
    return w


def default_refine(region_name: str, conductor: str) -> RefineMode:
    if region_name == "wafer":
        return "outer"
    if region_name in ("fr", "edge_gap"):
        return "inner"
    return "none"


@dataclass
class RadialMesh:
    r_edges: np.ndarray
    region_index: np.ndarray
    region_names: list[str]

    @property
    def n_cells(self) -> int:
        return len(self.region_index)

    @property
    def r_centers(self) -> np.ndarray:
        return 0.5 * (self.r_edges[:-1] + self.r_edges[1:])

    @property
    def widths(self) -> np.ndarray:
        return np.diff(self.r_edges)

    @property
    def areas(self) -> np.ndarray:
        return np.pi * (self.r_edges[1:] ** 2 + 0.0 - self.r_edges[:-1] ** 2)

    @property
    def r_boundaries(self) -> np.ndarray:
        """Radii of interior cell boundaries (between cell i and i+1)."""
        return self.r_edges[1:-1]

    @property
    def dr_boundaries(self) -> np.ndarray:
        """Center-to-center distances across interior boundaries."""
        rc = self.r_centers
        return rc[1:] - rc[:-1]


def build_radial_mesh(geometry: Geometry, spec: MeshSpec) -> RadialMesh:
    edges = [0.0]
    region_index = []
    r_in = 0.0
    for k, reg in enumerate(geometry.regions):
        n = spec.n_r[reg.name]
        ratio = spec.grading.get(reg.name, 1.0)
        refine = spec.refine.get(reg.name, default_refine(reg.name, reg.conductor))
        w = graded_widths(n, reg.r_out - r_in, ratio, refine)
        for wi in w:
            edges.append(edges[-1] + wi)
            region_index.append(k)
        edges[-1] = reg.r_out  # avoid round-off drift
        r_in = reg.r_out
    return RadialMesh(np.array(edges), np.array(region_index, dtype=int), [r.name for r in geometry.regions])


# ------------------------------------------------------------------ axial structure
@dataclass
class LayerInstance:
    name: str
    material: MaterialProps
    t: float
    z0: float  # bottom height [m] (cell datum = bottom conductor surface)
    radial_coupling: bool

    @property
    def z_mid(self) -> float:
        return self.z0 + 0.5 * self.t

    @property
    def z1(self) -> float:
        return self.z0 + self.t


def bulk_layer_fractions(n_z: int, ratio: float) -> np.ndarray:
    """Layer thickness fractions, graded geometrically away from both sheaths."""
    if n_z == 1:
        return np.array([1.0])
    k = np.arange(n_z)
    m = np.minimum(k, n_z - 1 - k)
    w = ratio**m
    return w / w.sum()


@dataclass
class CellStructure:
    index: int
    region: str
    conductor: str
    r_in: float
    r_out: float
    area: float
    bottom_layers: list[LayerInstance]
    s_b: float
    s_t: float
    l_gap: float
    z_bulk: np.ndarray  # N_z + 1 heights of bulk layer boundaries
    top_layers: list[LayerInstance]  # listed bottom (plasma side) to top (metal side)
    z_top_conductor: float

    @property
    def r_center(self) -> float:
        return 0.5 * (self.r_in + self.r_out)

    @property
    def z_surface_b(self) -> float:
        return self.bottom_layers[-1].z1 if self.bottom_layers else 0.0

    @property
    def z_surface_t(self) -> float:
        return self.z_surface_b + self.l_gap

    @property
    def l_p(self) -> float:
        return self.l_gap - self.s_b - self.s_t

    @property
    def n_z(self) -> int:
        return len(self.z_bulk) - 1

    @property
    def bulk_thickness(self) -> np.ndarray:
        return np.diff(self.z_bulk)

    @property
    def bulk_mid(self) -> np.ndarray:
        return 0.5 * (self.z_bulk[:-1] + self.z_bulk[1:])

    def bottom_stack_signature(self) -> tuple:
        return tuple((L.material.eps_r, L.material.sigma, round(L.t, 12)) for L in self.bottom_layers)


# ------------------------------------------------------------------ radial boundaries
@dataclass
class RadialLevel:
    kind: Literal["layer_b", "bulk", "layer_t", "top"]
    index: int  # layer index (bottom stack / bulk layer / top stack); 0 for top conductor
    zeta: float  # height above the bottom conductor used for the flux chain [m]
    thickness: float  # radial branch cross-section height [m] (0 for the perfect top conductor)
    material: Optional[MaterialProps] = None  # None for bulk (plasma) and top conductor


@dataclass
class RadialBoundary:
    index: int  # between cell `index` and `index + 1`
    r: float
    dr: float  # center-to-center distance
    levels: list[RadialLevel]
    chain_dl: np.ndarray  # flux-chain inductances [H], one per level (sorted by zeta)

    @property
    def c_factor(self) -> float:
        return MU0 * self.dr / (2.0 * np.pi * self.r)

    def mutual_inductance_matrix(self) -> np.ndarray:
        z = np.array([lv.zeta for lv in self.levels])
        return self.c_factor * np.minimum.outer(z, z)


@dataclass
class Mesh:
    radial: RadialMesh
    cells: list[CellStructure]
    boundaries: list[RadialBoundary]
    n_z: int
    notes: dict = field(default_factory=dict)

    @property
    def n_cells(self) -> int:
        return len(self.cells)

    def summary(self) -> str:
        lines = [f"cells={self.n_cells} n_z={self.n_z} r_wall={self.radial.r_edges[-1]:.4f} m"]
        for c in self.cells:
            lines.append(
                f"  cell {c.index:3d} {c.region:9s} cond={c.conductor} r=[{c.r_in:.4f},{c.r_out:.4f}] A={c.area:.3e} "
                f"zs={c.z_surface_b*1e3:.2f}mm s_b={c.s_b*1e3:.2f}mm l_p={c.l_p*1e3:.1f}mm ztop={c.z_top_conductor*1e3:.1f}mm"
            )
        return "\n".join(lines)


def _instantiate_layers(layers: list[Layer], z0: float, prefix: str) -> list[LayerInstance]:
    out = []
    z = z0
    for k, L in enumerate(layers):
        mat = resolve_material(L.material)
        out.append(LayerInstance(name=f"{prefix}{k+1}", material=mat, t=L.t, z0=z, radial_coupling=L.radial_coupling))
        z += L.t
    return out


def build_mesh(project: Project, s_bottom, s_top) -> Mesh:
    """Build the full mesh. `s_bottom`/`s_top` are nominal sheath widths [m] (scalar or per-cell array)."""
    geo = project.geometry
    spec = project.numerics.mesh
    radial = build_radial_mesh(geo, spec)
    n = radial.n_cells
    s_b = np.broadcast_to(np.asarray(s_bottom, dtype=float), (n,)).copy()
    s_t = np.broadcast_to(np.asarray(s_top, dtype=float), (n,)).copy()
    if spec.sheath_nominal is not None:
        s_b[:] = spec.sheath_nominal
        s_t[:] = spec.sheath_nominal

    fractions = bulk_layer_fractions(spec.n_z, spec.z_grading)
    # top stack is given top (metal) -> bottom (plasma); instantiate bottom-up
    top_layers_spec = list(reversed(geo.top.stack))

    cells: list[CellStructure] = []
    for i in range(n):
        reg = geo.regions[int(radial.region_index[i])]
        r_in, r_out = radial.r_edges[i], radial.r_edges[i + 1]
        rc = 0.5 * (r_in + r_out)
        bottom = _instantiate_layers(reg.stack, 0.0, "Sb")
        z_surf = bottom[-1].z1 if bottom else 0.0
        l_gap = geo.local_gap(reg, rc)
        l_p = l_gap - s_b[i] - s_t[i]
        if l_p <= 0:
            raise ValueError(f"cell {i}: nominal sheaths exceed the gap (l_gap={l_gap}, s_b={s_b[i]}, s_t={s_t[i]})")
        z_bulk = z_surf + s_b[i] + np.concatenate([[0.0], np.cumsum(fractions * l_p)])
        top = _instantiate_layers(top_layers_spec, z_surf + l_gap, "St")
        z_top_cond = top[-1].z1 if top else z_surf + l_gap
        cells.append(
            CellStructure(
                index=i, region=reg.name, conductor=reg.conductor, r_in=r_in, r_out=r_out,
                area=float(radial.areas[i]), bottom_layers=bottom, s_b=float(s_b[i]), s_t=float(s_t[i]),
                l_gap=l_gap, z_bulk=z_bulk, top_layers=top, z_top_conductor=z_top_cond,
            )
        )

    boundaries: list[RadialBoundary] = []
    for i in range(n - 1):
        a, b = cells[i], cells[i + 1]
        levels: list[RadialLevel] = []
        # bottom stack layers: only when both cells share the stack and the conductor
        same_stack = a.bottom_stack_signature() == b.bottom_stack_signature() and a.conductor == b.conductor
        if same_stack:
            for k, (La, Lb) in enumerate(zip(a.bottom_layers, b.bottom_layers)):
                if La.radial_coupling and Lb.radial_coupling:
                    levels.append(RadialLevel("layer_b", k, 0.5 * (La.z_mid + Lb.z_mid), 0.5 * (La.t + Lb.t), La.material))
        for j in range(spec.n_z):
            zeta = 0.5 * (a.bulk_mid[j] + b.bulk_mid[j])
            th = 0.5 * (a.bulk_thickness[j] + b.bulk_thickness[j])
            levels.append(RadialLevel("bulk", j, zeta, th, None))
        for k, (La, Lb) in enumerate(zip(a.top_layers, b.top_layers)):
            if La.radial_coupling and Lb.radial_coupling:
                levels.append(RadialLevel("layer_t", k, 0.5 * (La.z_mid + Lb.z_mid), 0.5 * (La.t + Lb.t), La.material))
        levels.append(RadialLevel("top", 0, 0.5 * (a.z_top_conductor + b.z_top_conductor), 0.0, None))
        levels.sort(key=lambda lv: lv.zeta)
        r_b = float(radial.r_edges[i + 1])
        dr = b.r_center - a.r_center
        c = MU0 * dr / (2.0 * np.pi * r_b)
        zetas = np.array([lv.zeta for lv in levels])
        chain = c * np.diff(np.concatenate([[0.0], zetas]))
        if np.any(chain <= 0):
            raise ValueError(f"boundary {i}: non-increasing level heights {zetas}")
        boundaries.append(RadialBoundary(index=i, r=r_b, dr=dr, levels=levels, chain_dl=chain))

    return Mesh(radial=radial, cells=cells, boundaries=boundaries, n_z=spec.n_z)
