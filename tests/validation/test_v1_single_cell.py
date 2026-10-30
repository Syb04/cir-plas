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
    """The series resonance of the linearized single cell equals the zero of Im Z of the element chain
    (sheath C, bulk Drude L-R in parallel with the displacement C, sheath C), and agrees with the textbook
    estimate w_pe sqrt(s_tot/l_p) corrected by the plasma dielectric factor (1 - w^2/w_pe^2)."""
    from scipy.optimize import brentq

    p = single_cell_project(f=13.56e6, v=200.0)
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    ac = run_ac(ctx)
    model = ac.model
    cell = model.mesh.cells[0]
    ce = model.cells[0]
    d = ce.bulk[0]

    def im_z(f):
        w = 2 * math.pi * f
        z_bulk = 1.0 / (1.0 / (1j * w * d.l + d.r) + 1j * w * d.c)
        z = 1.0 / (1j * w * ce.c_lin_b) + 1.0 / (1j * w * ce.c_lin_t) + z_bulk + 2 * ce.sheath_b.rst
        return z.imag

    # bracket below the electron plasma frequency (pole of the Drude L || C_disp element)
    f_pe = plasma_frequency(model.plasma.n0[0]) / (2 * math.pi)
    f_root = brentq(im_z, 50e6, 0.9 * f_pe)
    sweep = AcSettings(frequency=f_root, sweep=(f_root / 4, f_root * 4, 400))
    netlist, idx = NetlistBuilder(model, "ac", ac=sweep).build()
    res = backend.run(netlist)
    assert not res.errors
    f_vec = res.get("frequency").real
    i_port = np.abs(res.get(idx.i_port["W"]))
    f_peak = f_vec[np.argmax(i_port)]
    assert abs(f_peak - f_root) / f_root < 0.01, (f_peak, f_root)
    # textbook: w_psr = w_pe sqrt(s_tot/l_p) with the capacitances taken as eps0 A / s_lin
    w_pe = plasma_frequency(model.plasma.n0[0])
    s_lin_b = C.EPS0 * cell.area / ce.c_lin_b
    s_lin_t = C.EPS0 * cell.area / ce.c_lin_t
    w_text = w_pe * math.sqrt((s_lin_b + s_lin_t) / cell.l_p)
    w_corr = w_text / math.sqrt(1.0 + (w_text / w_pe) ** 2)  # resonance of L_e || C_disp in series with the sheath C
    assert abs(2 * math.pi * f_root - w_corr) / w_corr < 0.03, (f_root, w_corr / (2 * math.pi))


def test_v1_symmetric_sheath_waveform_and_power_balance(backend):
    """Symmetric cell (equal areas, no blocking cap): the two sheaths are anti-phase copies, the sheath follows
    the matrix law Q = Kq sqrt(V + Vmin) with a nearly sinusoidal charge oscillation (L&L homogeneous model),
    the time average obeys <V+Vmin>/max(V+Vmin) = (Q0^2 + q1^2/2)/(Q0+q1)^2, and the power balance closes."""
    from cirplas.core.post.signals import harmonics, resample, uniform_grid

    p = single_cell_project(f=13.56e6, v=300.0, stochastic=True)
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    tr = run_tran(ctx)
    po = tr.post
    c = po.cells[0]
    assert abs(c.vsh_b_dc - c.vsh_t_dc) / c.vsh_b_dc < 0.02
    assert abs(c.vsh_b_1 - c.vsh_t_1) / c.vsh_b_1 < 0.02
    # charge waveform from the sheath voltage
    res, idx = tr.raw, tr.index
    t = res.scale
    t_u = uniform_grid(t[-1] - tr.meas_cycles * tr.period, tr.period, tr.meas_cycles, 200)
    vsh = resample(t, res.get(f"v({idx.node_bulk[0][0]})") - res.get(f"v({idx.node_sb[0]})"), t_u)
    sp = tr.model.cells[0].sheath_b
    q = sp.charge(vsh)
    h = harmonics(q, 200, 6)
    q0, q1 = h.dc, h.amplitude(1)
    assert h.amplitude(2) / q1 < 0.05, "charge oscillation should be nearly sinusoidal (sinusoidal current)"
    x = vsh + sp.vmin
    ratio = x.mean() / x.max()
    predicted = (q0**2 + 0.5 * q1**2) / (q0 + q1) ** 2
    assert abs(ratio - predicted) < 0.02, (ratio, predicted)
    assert abs(po.power_balance_error) < 0.01
    assert c.p_stoc_b > 0


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
