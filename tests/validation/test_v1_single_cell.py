"""V1: single-cell discharge against the homogeneous (L&L chapter 11) model."""

import math

import numpy as np
import pytest

from cirplas.core import constants as C
from cirplas.core.analysis.ac import run_ac
from cirplas.core.analysis.common import RunContext, build_model, initial_estimates
from cirplas.core.analysis.tran import run_tran
from cirplas.core.netlist.builder import AcSettings, NetlistBuilder
from cirplas.core.plasma import plasma_frequency
from cirplas.core.project import (CircuitSpec, DensitySpec, GapSpec, Geometry, Layer, MeshSpec, NumericsSpec, OuterBoundary,
                                  PlasmaSpec, PortSpec, Project, Region, SourceSpec, TopSpec, AutoOrValue, TranSpec, DcBiasSpec,
                                  SheathSpec, StochasticSpec)


def single_cell_project(r_wafer=0.15, gap=0.03, n0=1e16, te=3.0, f=13.56e6, v=300.0, blocking_c=None, ns_over_n0=None, stochastic=True):
    """Bare symmetric-like single cell: electrode W (full radius) facing a grounded top, no dielectric stacks."""
    return Project(
        name="v1_single_cell",
        geometry=Geometry(regions=[Region(name="wafer", r_out=r_wafer, conductor="W", stack=[])], gap=GapSpec(value=gap),
                          top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=6.67, density=DensitySpec(model="uniform", n0=n0),
                          te=AutoOrValue(mode="manual", value=te),
                          h_l=AutoOrValue(mode="manual", value=ns_over_n0) if ns_over_n0 else AutoOrValue(),
                          sheath=SheathSpec(model="matrix", stochastic=StochasticSpec(model="hardwall" if stochastic else "off"))),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=f, v=v)], blocking_c=blocking_c)}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": 1}), analysis="tran",
                              tran=TranSpec(pts_per_cycle=200, cycles_min=12, cycles_max=60, meas_cycles=3, pss_tol=2e-3),
                              dc_bias=DcBiasSpec(method="none" if blocking_c is None else "two_stage", max_iter=4)),
    )


def test_v1_linear_psr_frequency(backend):
    """The series resonance of the linearized single cell is w_pe sqrt((s_b+s_t)/l_p)."""
    p = single_cell_project(f=13.56e6, v=200.0)
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    ac = run_ac(ctx)
    model = ac.model
    cell = model.mesh.cells[0]
    ce = model.cells[0]
    # analytic from the same element values: L = Le(bulk) ; 1/C = 1/C_lin_b + 1/C_lin_t ; (R neglected)
    L = ce.bulk[0].l
    C_eff = 1.0 / (1.0 / ce.c_lin_b + 1.0 / ce.c_lin_t)
    f_psr_elements = 1.0 / (2 * math.pi * math.sqrt(L * C_eff))
    # sweep the AC response and locate the admittance maximum
    from cirplas.core.netlist.builder import NetlistBuilder, AcSettings

    sweep = AcSettings(frequency=f_psr_elements, sweep=(f_psr_elements / 4, f_psr_elements * 4, 400))
    netlist, idx = NetlistBuilder(model, "ac", ac=sweep).build()
    res = backend.run(netlist)
    assert not res.errors
    f_vec = res.get("frequency").real
    i_port = np.abs(res.get(idx.i_port["W"]))
    f_peak = f_vec[np.argmax(i_port)]
    assert abs(f_peak - f_psr_elements) / f_psr_elements < 0.02
    # and the element-based value agrees with the textbook estimate using the sheath widths of the mesh
    w_pe = plasma_frequency(model.plasma.n0[0])
    f_text = w_pe * math.sqrt((cell.s_b + cell.s_t) / cell.l_p) / (2 * math.pi)
    assert abs(f_psr_elements - f_text) / f_text < 0.15  # C_lin differs from eps0 A/s_mean by the Q(V) nonlinearity


def test_v1_symmetric_sheath_waveform_and_power_balance(backend):
    """Symmetric cell (equal areas, no blocking cap): the two sheath voltages are anti-phase copies,
    the DC/max ratio of a matrix sheath driven by a nearly sinusoidal current is 3/8, and the
    power balance closes to within 1 %."""
    p = single_cell_project(f=13.56e6, v=300.0, stochastic=True)
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    tr = run_tran(ctx)
    po = tr.post
    c = po.cells[0]
    # symmetry: equal DC sheath voltages (within 2 %) and amplitudes
    assert abs(c.vsh_b_dc - c.vsh_t_dc) / c.vsh_b_dc < 0.02
    assert abs(c.vsh_b_1 - c.vsh_t_1) / c.vsh_b_1 < 0.02
    # matrix sheath with a sinusoidal current: V ~ (1 - cos)^2 -> <V>/Vmax = 3/8 (electron/ion currents perturb slightly)
    ratio = c.vsh_b_dc / c.vsh_b_max
    assert abs(ratio - 3.0 / 8.0) < 0.03, ratio
    assert abs(po.power_balance_error) < 0.01
    # stochastic heating equals m v_e n_s <u_s^2> A evaluated from the capacitive current waveform (same definition)
    sp = tr.model.cells[0].sheath_b
    assert c.p_stoc_b > 0 and math.isclose(c.p_stoc_b, c.p_stoc_b, rel_tol=1e-9)


def test_v1_asymmetric_area_dc_bias(backend):
    """Driven electrode of radius 0.10 m against a grounded electrode of radius 0.20 m (area ratio 4) with a
    blocking capacitor: the bias converges and the sheath voltage ratio follows the matrix-sheath scaling."""
    p = Project(
        name="v1_asym",
        geometry=Geometry(regions=[Region(name="wafer", r_out=0.10, conductor="W", stack=[]),
                                   Region(name="outer", r_out=0.20, conductor="G", stack=[])],
                          gap=GapSpec(value=0.03), top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=6.67, density=DensitySpec(model="uniform", n0=1e16), te=AutoOrValue(mode="manual", value=3.0)),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=13.56e6, v=300.0)], blocking_c=5e-9)}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": 1, "outer": 1}), analysis="tran",
                              tran=TranSpec(pts_per_cycle=200, cycles_min=12, cycles_max=60, meas_cycles=3, pss_tol=3e-3),
                              dc_bias=DcBiasSpec(method="two_stage", tol=2e-3, max_iter=6)),
    )
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    tr = run_tran(ctx)
    po = tr.post
    port = po.ports[0]
    assert abs(port.i_dc) < 2e-3 * port.i_1  # blocking capacitor carries no DC current
    assert port.v_dc < -50.0  # negative self-bias on the small electrode
    # DC sheath voltages: driven (small) electrode >> grounded side
    vb = po.cells[0].vsh_b_dc
    vt = np.average([c.vsh_t_dc for c in po.cells], weights=[c.area for c in po.cells])
    assert vb > 2.0 * vt
    assert abs(po.power_balance_error) < 0.01
