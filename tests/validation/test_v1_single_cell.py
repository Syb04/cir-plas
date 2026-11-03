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


@pytest.mark.parametrize("model,tol", [("matrix", 0.02), ("child", 0.015), ("collisional", 0.015)])
def test_v1_symmetric_sheath_waveform_and_power_balance(backend, model, tol):
    """Symmetric cell (equal areas, no blocking cap): the two sheaths are anti-phase copies, the sheath follows
    Q = Kq (V + Vmin)^(1-beta) with a nearly sinusoidal charge oscillation (L&L homogeneous model), the time
    average obeys <V+Vmin>/max(V+Vmin) = <(Q0 + q1 cos)^p>/(Q0+q1)^p with p = 1/(1-beta) (matrix: p = 2,
    (Q0^2 + q1^2/2)/(Q0+q1)^2), and the power balance closes. For the Child laws (p = 4, 5/2) the small second
    harmonic of the charge is amplified by the power, hence the absolute tolerance on the ratio."""
    from cirplas.core.post.signals import harmonics, resample, uniform_grid

    p = single_cell_project(f=13.56e6, v=300.0, stochastic=True)
    p.plasma.sheath.model = model
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
    pw = 1.0 / (1.0 - sp.beta)
    th = np.linspace(0.0, 2 * np.pi, 400, endpoint=False)
    predicted = float(np.mean((q0 + q1 * np.cos(th)) ** pw) / (q0 + q1) ** pw)
    assert abs(ratio - predicted) < tol, (ratio, predicted)
    assert abs(po.power_balance_error) < 0.01
    assert c.p_stoc_b > 0


@pytest.mark.parametrize("model", ["matrix", "child", "collisional"])
def test_v1_asymmetric_area_dc_bias(backend, model):
    """Driven electrode of radius 0.10 m against a grounded electrode of radius 0.20 m (area ratio 4) with a
    blocking capacitor: the bias converges and the sheath voltage ratio follows the matrix-sheath scaling."""
    p = Project(
        name="v1_asym",
        geometry=Geometry(regions=[Region(name="wafer", r_out=0.10, conductor="W", stack=[]),
                                   Region(name="outer", r_out=0.20, conductor="G", stack=[])],
                          gap=GapSpec(value=0.03), top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=6.67, density=DensitySpec(model="uniform", n0=1e16), te=AutoOrValue(mode="manual", value=3.0),
                          sheath=SheathSpec(model=model)),
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


def test_v1_iedf_frequency_dependence(backend):
    """IEDF at the electrode (damped-potential model): the mean energy equals e<Vsh>, the bimodal width follows the
    first-order ion response, and it shrinks from 2 MHz (w tau_i ~ 4 for argon) to 27 MHz (w tau_i ~ 50)."""
    from cirplas.core.post.iedf import ion_transit_time

    widths = {}
    for f in (2e6, 27.12e6):
        p = single_cell_project(f=f, v=300.0)
        tr = run_tran(RunContext(project=p, backend=backend, log=lambda m: None))
        iedf, c = tr.post.iedf, tr.post.cells[0]
        de = iedf.energy[1] - iedf.energy[0]
        assert abs(float(np.sum(iedf.f[0]) * de) - 1.0) < 1e-9
        e_mean = float(np.sum(iedf.energy * iedf.f[0]) * de)
        assert abs(e_mean - c.vsh_b_dc) / c.vsh_b_dc < 0.03, (e_mean, c.vsh_b_dc)
        assert iedf.peak_low[0] < c.vsh_b_dc < iedf.peak_high[0]
        assert 0 < iedf.collisionless_fraction[0] <= 1
        # peak separation of a sinusoidal sheath voltage through the first-order ion response: 2 V_1 / sqrt(1 + (w tau_i)^2)
        wt = 2 * math.pi * f * ion_transit_time(c.s_b_mean, c.vsh_b_dc, tr.model.plasma.gas.mass_kg)
        assert abs(c.e_ion_width / (2 * c.vsh_b_1 / math.sqrt(1 + wt * wt)) - 1) < 0.05
        widths[f] = c.e_ion_width
    assert widths[27.12e6] < 0.3 * widths[2e6]
