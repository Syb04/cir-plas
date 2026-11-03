"""V6: self-consistent density iteration (spec 05 section V6)."""

import numpy as np

from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.density import global_model_density, run_density_iteration
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


def test_v6_mean_density_matches_global_model(backend):
    """Criterion 1: the converged radial mean of n agrees with the Lieberman global model within 5 %."""
    p = small_project()
    res = run_density_iteration(RunContext(project=p, backend=backend, log=lambda m: None))
    assert res.converged
    rm = res.ac.model.mesh.radial
    p_e = float(np.sum(res.ac.radial("s_e") * rm.areas))
    n_global = global_model_density(p_e, res.ac.model.plasma, rm.r_edges[-1], res.ac.model.mesh.cells[0].l_p)
    n_mean = float(np.sum(res.n * rm.areas) / np.sum(rm.areas))
    assert abs(n_mean / n_global - 1) < 0.05


def test_v6_independent_of_initial_guess(backend):
    """Criterion 2: default relaxation converges within 30 iterations from 1/3x and 3x the initial density,
    and both runs reach the same solution within 1e-3."""
    nn = []
    for n0 in (1e16 / 3, 3e16):
        p = small_project(n0=n0)
        p.numerics.density_iteration.max_iter = 30
        res = run_density_iteration(RunContext(project=p, backend=backend, log=lambda m: None))
        assert res.converged and res.iterations <= 30
        nn.append(res.n)
    assert np.max(np.abs(nn[0] - nn[1]) / nn[1]) < 1e-3


def test_v6_power_specified_density(backend):
    """With a power target the amplitude is adjusted inside the density loop: the converged state delivers the target."""
    p = small_project()
    p.circuit.ports["W"].sources[0].power = 5.0
    res = run_density_iteration(RunContext(project=p, backend=backend, log=lambda m: None))
    assert res.converged
    port = next(pr for pr in res.ac.ports if pr.port == "W")
    assert abs(port.p / 5.0 - 1) < 0.02
    assert res.notes["amplitudes"]["W"] != 200.0


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


def test_v6_tran_refinement(backend):
    """The TRAN confirmation step (refine_with_tran) corrects the AC-converged density only slightly at low power
    and records the TRAN steps in the iteration history."""
    from cirplas.core.analysis.density import refine_with_tran

    p = small_project()
    p.circuit.ports["W"].blocking_c = 1e-8
    p.numerics.density_iteration.refine_with_tran = True
    p.numerics.density_iteration.tran_refine_iter = 1
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    res = run_density_iteration(ctx)
    n_ac = res.n.copy()
    proj, ac, tran = refine_with_tran(ctx, res)
    assert tran is not None and res.notes["stages"][-1] == "tran"
    assert len(res.n_history) == res.iterations + 2
    assert np.max(np.abs(res.n - n_ac) / n_ac) < 0.05
    assert proj.plasma.density.model == "table"
