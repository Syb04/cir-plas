"""V6: self-consistent density iteration (spec 05 section V6)."""

import numpy as np

from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.density import ambipolar_diffusion, radial_power_balance, run_density_iteration
from cirplas.core.constants import E_CHARGE
from cirplas.core.project import (AutoOrValue, CircuitSpec, DensityIterationSpec, DensitySpec, GapSpec, Geometry, MeshSpec,
                                  NumericsSpec, OuterBoundary, PlasmaSpec, PortSpec, Project, Region, SourceSpec, TopSpec)


def small_project(n0=1e16, f=13.56e6, v=200.0, relax=0.4):
    return Project(
        name="v6",
        geometry=Geometry(regions=[Region(name="wafer", r_out=0.10, conductor="W", stack=[])], gap=GapSpec(value=0.03),
                          top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=6.67, density=DensitySpec(model="uniform", n0=n0)),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=f, v=v)])}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": 12}, refine={"wafer": "none"}), analysis="sc",
                              density_iteration=DensityIterationSpec(enabled=True, relax=relax, tol=1e-3, max_iter=40)),
    )


def test_v6_discrete_balance_matches_global_power_balance(backend):
    """Integrated over the volume, the FV solution satisfies: total ionization = axial Bohm losses + wall loss,
    i.e. the global electron power balance P_e = e eps_T [2 h_l u_B sum(n A) + h_R u_B n_wall 2 pi R l_p]."""
    p = small_project()
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    res = run_density_iteration(ctx)
    assert res.converged
    ac = res.ac
    plasma = ac.model.plasma
    cells = ac.model.mesh.cells
    rm = ac.model.mesh.radial
    s_e = ac.radial("s_e")
    l_p = np.array([c.l_p for c in cells])
    n = res.n
    eps_t = plasma.gas.eps_c(plasma.te) + 2 * plasma.te
    p_e = float(np.sum(s_e * rm.areas))
    axial = 2 * plasma.h_l * plasma.u_b * float(np.sum(n * rm.areas))
    wall = plasma.h_r * plasma.u_b * n[-1] * 2 * np.pi * rm.r_edges[-1] * l_p[-1]
    assert abs(p_e - E_CHARGE * eps_t * (axial + wall)) / p_e < 0.02


def test_v6_independent_of_initial_guess(backend):
    nn = []
    for n0 in (3e15, 3e16):
        p = small_project(n0=n0)
        ctx = RunContext(project=p, backend=backend, log=lambda m: None)
        res = run_density_iteration(ctx)
        assert res.converged and res.iterations <= 40
        nn.append(res.n)
    assert np.max(np.abs(nn[0] - nn[1]) / nn[1]) < 5e-3


def test_v6_profile_smoother_than_heating(backend):
    """At 60 MHz / 150 mm the center-high heating gives a density profile flatter than S_e (diffusion)."""
    from cirplas.core.project import default_project

    p = default_project()
    for port in p.circuit.ports.values():
        port.l_feed = 0.0
        port.c_stray = 100e-12
    p.numerics.density_iteration.enabled = True
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    res = run_density_iteration(ctx)
    assert res.converged
    cells = res.ac.model.mesh.cells
    wafer = np.array([c.region == "wafer" for c in cells])
    s_e = res.ac.radial("s_e")[wafer]
    n = res.n[wafer]
    u = lambda y: (y.max() - y.min()) / (2 * y.mean())
    assert u(n) < u(s_e)
