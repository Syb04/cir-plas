"""V3: skin effect in a dense bulk resolved with several bulk layers (spec 05 section V3).

Linear AC, uniform density high enough that the skin depth is smaller than the bulk half
thickness. For the TM mode of a symmetric slab the axial field E_z is symmetric about the
mid-plane while E_r (and hence J_r) is antisymmetric: J_r(z) ~ sinh(k (z - l_p/2)) with the
complex propagation constant k = sqrt(j w mu0 sigma_p) of the Drude medium. The radial
current therefore flows in opposite directions in the two skin layers and vanishes at the
mid-plane. The single-layer model must agree with the multi-layer model when delta >> l_p.
"""

import math

import numpy as np
import pytest

from cirplas.core import constants as C
from cirplas.core.analysis.ac import extract_ac
from cirplas.core.analysis.common import Estimates, build_model
from cirplas.core.mesh import build_radial_mesh
from cirplas.core.netlist.builder import AcSettings, NetlistBuilder
from cirplas.core.plasma import complex_conductivity
from cirplas.core.project import (AutoOrValue, CircuitSpec, DensitySpec, GapSpec, Geometry, MeshSpec, NumericsSpec, OuterBoundary,
                                  PlasmaSpec, PortSpec, Project, Region, SheathSpec, SourceSpec, StochasticSpec, TopSpec)


def slab_project(n0, n_z, f=60e6, radius=0.15, gap=0.030, s_fixed=1.0e-3, n_r=30, nu_m=1e7, z_grading=1.0):
    return Project(
        name="v3_skin",
        geometry=Geometry(regions=[Region(name="wafer", r_out=radius, conductor="W", stack=[])], gap=GapSpec(value=gap),
                          top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=1.0, density=DensitySpec(model="uniform", n0=n0),
                          te=AutoOrValue(mode="manual", value=3.0), nu_m=AutoOrValue(mode="manual", value=nu_m),
                          sheath=SheathSpec(linear_fixed_width=s_fixed, stochastic=StochasticSpec(model="off"))),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=f, v=100.0)])}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": n_r}, refine={"wafer": "none"}, n_z=n_z, z_grading=z_grading), analysis="ac"),
    )


def solve(backend, p):
    n = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
    est = Estimates(v1_b=np.full(n, 50.0), v1_t=np.full(n, 50.0), s_b=np.full(n, 1e-3), s_t=np.full(n, 1e-3))
    model = build_model(p, est)
    f = p.f_max()
    netlist, idx = NetlistBuilder(model, "ac", ac=AcSettings(frequency=f)).build()
    res = backend.run(netlist)
    assert not res.errors, res.errors
    return model, extract_ac(res, idx, model, f, est, 1)


def test_v3_radial_current_decays_with_skin_depth(backend):
    n0, f = 1.0e18, 60e6
    w = 2 * math.pi * f
    p = slab_project(n0, n_z=12, f=f)
    model, ac = solve(backend, p)
    cells = model.mesh.cells
    l_p = cells[0].l_p
    sigma = complex_conductivity(n0, 1e7, w)
    k = np.sqrt(1j * w * C.MU0 * sigma)
    delta = 1.0 / k.real
    assert delta < 0.4 * l_p, (delta, l_p)
    # radial current density per bulk layer at a mid radius boundary
    b_index = len(cells) // 2
    be = model.boundaries[b_index]
    z = []
    j = []
    for kk, le in enumerate(be.levels, start=1):
        if le.level.kind != "bulk":
            continue
        area = 2 * math.pi * be.boundary.r * le.level.thickness
        j.append(abs(ac.radial_currents[(b_index, kk)]) / area)
        z.append(le.level.zeta - (cells[b_index].z_surface_b + cells[b_index].s_b))  # height above the bottom sheath edge
    z = np.array(z)
    j = np.array(j)
    ref = np.abs(np.sinh(k * (z - l_p / 2)))
    ref = ref / ref[0]
    got = j / j[0]
    # antisymmetric profile: nearly zero at the mid-plane, symmetric magnitudes at both edges
    assert got[len(got) // 2] < 0.1 * got[0] or got[len(got) // 2 - 1] < 0.1 * got[0]
    assert abs(got[-1] - got[0]) < 0.05
    half = len(got) // 2
    err = np.max(np.abs(np.log(got[:half]) - np.log(ref[:half])))
    assert err < 0.1, (err, got, ref)


def test_v3_single_layer_agrees_when_skin_depth_large(backend):
    """delta >> l_p and well below the series resonance (n0 = 1e16: delta = 5 cm, w^2/w_PSR^2 ~ 0.06).
    Near the PSR (n0 ~ 1e15) the profile is sensitive to the small antisymmetric bulk currents that a single
    mid-plane branch cannot carry, so the comparison is made away from it."""
    n0 = 1.0e16
    p1 = slab_project(n0, n_z=1)
    p8 = slab_project(n0, n_z=8)
    _, a1 = solve(backend, p1)
    _, a8 = solve(backend, p8)
    v1 = np.abs(a1.radial("vsh_b")) / abs(a1.cells[-1].vsh_b)
    v8 = np.abs(a8.radial("vsh_b")) / abs(a8.cells[-1].vsh_b)
    assert np.max(np.abs(v1 - v8)) < 0.02
    s1 = a1.radial("s_e")
    s8 = a8.radial("s_e")
    areas = a1.radial("area")
    # total electron heating within 10 %; the profile away from the shorted outer edge (where the small antisymmetric
    # bulk currents of the multi-layer model add local radial ohmic heating) within 15 %
    assert abs(np.sum(s1 * areas) - np.sum(s8 * areas)) / np.sum(s1 * areas) < 0.1
    interior = slice(0, len(s1) - 4)
    assert np.max(np.abs(s1[interior] - s8[interior]) / s1[interior]) < 0.15
