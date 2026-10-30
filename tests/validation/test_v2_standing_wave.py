"""V2: linear standing wave in the radial transmission-line limit (spec 05 section V2).

Uniform density, fixed linear sheath capacitance, single solid electrode facing a grounded top,
outer short. With only the top-conductor radial branches (pure Lieberman transmission line) the
shunt voltage follows J0(k r) with k from the line parameters; with the bulk radial branches
included the profile must stay close to it.
"""

import math

import numpy as np
import pytest
from scipy.special import j0, jn_zeros

from cirplas.core import constants as C
from cirplas.core.analysis.ac import extract_ac, run_ac
from cirplas.core.analysis.common import Estimates, RunContext, build_model
from cirplas.core.netlist.builder import AcSettings, NetlistBuilder
from cirplas.core.project import (AutoOrValue, CircuitSpec, DensitySpec, GapSpec, Geometry, MeshSpec, NumericsSpec, OuterBoundary,
                                  PlasmaSpec, PortSpec, Project, Region, SheathSpec, SourceSpec, StochasticSpec, TopSpec)


def tl_project(f, radius, gap, n0, s_fixed, n_r, nu_m=1e6):
    return Project(
        name="v2_standing_wave",
        geometry=Geometry(regions=[Region(name="wafer", r_out=radius, conductor="W", stack=[])], gap=GapSpec(value=gap),
                          top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=1.0, density=DensitySpec(model="uniform", n0=n0),
                          te=AutoOrValue(mode="manual", value=3.0), nu_m=AutoOrValue(mode="manual", value=nu_m),
                          sheath=SheathSpec(linear_fixed_width=s_fixed, stochastic=StochasticSpec(model="off"))),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=f, v=100.0)])}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": n_r}, refine={"wafer": "none"}), analysis="ac"),
    )


def analytic_k(f, n0, s_tot, l_tot, l_p, nu_m=1e6):
    """Wavenumber of the radial line built from the same lumped physics as the circuit:
    series L' = mu0 l_tot/(2 pi r); shunt impedance per unit area = sheath capacitances in series with the
    bulk Drude element (electron inertia + collisions in parallel with the vacuum displacement current).
    k^2 = -j w mu0 l_tot / Z'_area; the J0 fit uses Re(k)."""
    w = 2 * math.pi * f
    w_pe2 = n0 * C.E_CHARGE**2 / (C.EPS0 * C.M_E)
    l_e = C.M_E * l_p / (C.E_CHARGE**2 * n0)  # per unit area [H m^2]
    r_e = nu_m * l_e
    y_bulk = 1.0 / (1j * w * l_e + r_e) + 1j * w * C.EPS0 / l_p
    z_area = s_tot / (1j * w * C.EPS0) + 1.0 / y_bulk
    k2 = -1j * w * C.MU0 * l_tot / z_area
    k = np.sqrt(k2)
    return float(k.real)


def profile(backend, p, radial_kinds):
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    from cirplas.core.mesh import build_radial_mesh

    n = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
    est = Estimates(v1_b=np.full(n, 50.0), v1_t=np.full(n, 50.0), s_b=np.full(n, 1e-3), s_t=np.full(n, 1e-3))
    model = build_model(p, est)
    f = p.f_max()
    netlist, idx = NetlistBuilder(model, "ac", ac=AcSettings(frequency=f), radial_kinds=radial_kinds).build()
    res = backend.run(netlist)
    assert not res.errors, res.errors
    ac = extract_ac(res, idx, model, f, est, 1)
    # shunt (column) voltage = V_W - V_T(r)
    v_w = complex(res.get("v(w)")[0])
    v_t = np.array([complex(res.get(f"v({node})")[0]) for node in idx.node_top])
    return model, ac, v_w - v_t


@pytest.mark.parametrize("f,radius", [(60e6, 0.15), (100e6, 0.15), (60e6, 0.225)])
def test_v2_j0_profile_pure_transmission_line(backend, f, radius):
    gap, n0, s_fixed = 0.030, 1e16, 1.0e-3
    p = tl_project(f, radius, gap, n0, s_fixed, n_r=60)
    model, ac, v_col = profile(backend, p, radial_kinds={"top"})
    cells = model.mesh.cells
    r = np.array([c.r_center for c in cells])
    l_p = cells[0].l_p
    k = analytic_k(f, n0, 2 * s_fixed, cells[0].z_top_conductor, l_p)
    kR = k * radius
    assert kR < jn_zeros(0, 1)[0], "test configured below the first J0 zero"
    ref = j0(k * r) / j0(k * r[-1])
    got = np.abs(v_col) / abs(v_col[-1])
    err = np.max(np.abs(got - ref))
    assert err < 0.02, (f, radius, kR, err)
    # sheath voltage follows the same profile (uniform series division)
    vsh = np.abs(ac.radial("vsh_b")) / abs(ac.cells[-1].vsh_b)
    assert np.max(np.abs(vsh - ref)) < 0.02


def test_v2_second_order_convergence(backend):
    f, radius, gap, n0, s_fixed = 100e6, 0.15, 0.030, 1e16, 1.0e-3
    errs = []
    for n_r in (20, 40, 80):
        p = tl_project(f, radius, gap, n0, s_fixed, n_r=n_r)
        model, ac, v_col = profile(backend, p, radial_kinds={"top"})
        cells = model.mesh.cells
        r = np.array([c.r_center for c in cells])
        k = analytic_k(f, n0, 2 * s_fixed, cells[0].z_top_conductor, cells[0].l_p)
        ref = j0(k * r) / j0(k * r[-1])
        errs.append(np.max(np.abs(np.abs(v_col) / abs(v_col[-1]) - ref)))
    assert errs[0] > errs[1] > errs[2]
    assert errs[0] / errs[2] > 8.0, errs  # ~16 for second order


def test_v2_bulk_radial_branches_small_effect(backend):
    """Including the bulk radial branches (Drude) changes the linear profile only slightly for delta >> l_p."""
    f, radius, gap, n0, s_fixed = 60e6, 0.15, 0.030, 1e16, 1.0e-3
    p = tl_project(f, radius, gap, n0, s_fixed, n_r=40)
    _, _, v_pure = profile(backend, p, radial_kinds={"top"})
    _, _, v_full = profile(backend, p, radial_kinds=None)
    a = np.abs(v_pure) / abs(v_pure[-1])
    b = np.abs(v_full) / abs(v_full[-1])
    assert np.max(np.abs(a - b)) < 0.03, np.max(np.abs(a - b))
