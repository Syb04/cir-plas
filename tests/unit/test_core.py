import math

import numpy as np
import pytest

from cirplas.core import constants as C
from cirplas.core.materials import gas_db, resolve_material
from cirplas.core.mesh import build_mesh, bulk_layer_fractions, graded_widths
from cirplas.core.plasma import (bohm_speed, compute_plasma_state, debye_length, drude_inductance, h_l_factor,
                                 plasma_frequency, te_particle_balance, gas_density, d_eff, h_r_factor)
from cirplas.core.project import Project, SheathSpec, default_project
from cirplas.core.sheath_models import make_sheath_params


def test_project_roundtrip_and_hash():
    p = default_project()
    q = Project.model_validate_json(p.to_json())
    assert q.hash() == p.hash()
    assert q.f_max() == 60e6
    assert math.isclose(q.base_period(), 1 / 60e6)


def test_project_validation_errors():
    d = default_project().model_dump()
    d["circuit"]["ports"]["F"] = {"sources": []}
    with pytest.raises(ValueError):
        Project.model_validate(d)
    d = default_project().model_dump()
    d["numerics"]["mesh"]["n_r"].pop("outer")
    with pytest.raises(ValueError):
        Project.model_validate(d)


def test_argon_rates_and_te():
    g = gas_db("ar")
    assert math.isclose(g.K_iz(3.0), 2.34e-14 * 3.0**0.59 * math.exp(-17.44 / 3.0))
    n_g = gas_density(6.67, 400.0)
    assert math.isclose(n_g, 6.67 / (C.K_B * 400.0))
    te = te_particle_balance(g, n_g, d_eff(0.25, 0.028, 0.19, 0.3))
    assert 1.5 < te < 4.0
    # ionization balance holds at the root
    assert math.isclose(g.K_iz(te) * n_g * d_eff(0.25, 0.028, 0.19, 0.3), bohm_speed(te, g.mass_kg), rel_tol=1e-5)


def test_elementary_formulas():
    n = 1e16
    assert math.isclose(plasma_frequency(n) / (2 * math.pi), 8.98e8, rel_tol=2e-3)
    assert math.isclose(debye_length(3.0, 1e16), 7.43e3 * math.sqrt(3.0 / 1e16), rel_tol=1e-3)
    assert math.isclose(bohm_speed(3.0, 40 * C.AMU), 2690.0, rel_tol=1e-2)
    assert math.isclose(h_l_factor(0.028, 8.3e-4), 0.86 / math.sqrt(3 + 0.028 / (2 * 8.3e-4)))
    assert math.isclose(drude_inductance(1e16, 0.028, 0.0707), C.M_E * 0.028 / (C.E_CHARGE**2 * 1e16 * 0.0707))


def test_sheath_matrix_model_against_analytic():
    area = math.pi * 0.15**2
    ns, te = 1.7e15, 3.0
    sp = make_sheath_params(area, ns, te, 40 * C.AMU, SheathSpec(model="matrix", alpha_min=2.0))
    # matrix sheath: s = sqrt(2 eps0 V/(e ns)) far from the minimum width
    for v in (50.0, 100.0, 400.0):
        s_analytic = math.sqrt(2 * C.EPS0 * v / (C.E_CHARGE * ns))
        # the smooth guard around V=0 perturbs the power law by O(eps^2/V^2)
        assert math.isclose(sp.width(v), math.sqrt(2 * C.EPS0 * (v + sp.vmin) / (C.E_CHARGE * ns)), rel_tol=1e-3)
        assert abs(sp.width(v) - s_analytic) / s_analytic < sp.vmin / v
    # minimum width = alpha_min * lambda_D at V = 0
    assert math.isclose(sp.width(0.0), 2.0 * debye_length(te, ns), rel_tol=2e-2)
    # capacitance is the derivative of the charge
    v = np.linspace(-20, 400, 50)
    h = 1e-4
    num = (sp.charge(v + h) - sp.charge(v - h)) / (2 * h)
    assert np.allclose(num, sp.capacitance(v), rtol=1e-6)
    # constant capacitance for negative voltages
    assert math.isclose(sp.capacitance(-30.0), sp.c_max, rel_tol=1e-6)
    # stochastic resistance R = m v_e /(e^2 ns A); ion current Bohm
    v_e = math.sqrt(8 * C.E_CHARGE * te / (math.pi * C.M_E))
    assert math.isclose(sp.rst, C.M_E * v_e / (C.E_CHARGE**2 * ns * area))
    assert math.isclose(sp.ii, C.E_CHARGE * ns * bohm_speed(te, 40 * C.AMU) * area)
    assert math.isclose(sp.ie0, 0.25 * C.E_CHARGE * ns * v_e * area)
    # linearized capacitance tends to the small-signal value for small amplitude
    c_small = sp.linearized_capacitance(0.5)
    assert math.isclose(c_small, sp.capacitance(0.5), rel_tol=0.05)


def test_child_law_sheath_width():
    sp = make_sheath_params(0.01, 1e15, 3.0, 40 * C.AMU, SheathSpec(model="child"))
    lam = debye_length(3.0, 1e15)
    v = 300.0
    s_child = (math.sqrt(2) / 3) * lam * (2 * (v + sp.vmin) / 3.0) ** 0.75
    assert math.isclose(sp.width(v), s_child, rel_tol=1e-3)


def test_graded_widths_and_bulk_fractions():
    w = graded_widths(5, 1.0, 1.2, "outer")
    assert math.isclose(w.sum(), 1.0)
    assert np.all(np.diff(w) < 0)  # smallest at the outer edge
    w = graded_widths(5, 1.0, 1.2, "inner")
    assert np.all(np.diff(w) > 0)
    assert np.allclose(graded_widths(4, 2.0, 1.3, "none"), 0.5)
    f = bulk_layer_fractions(5, 1.5)
    assert math.isclose(f.sum(), 1.0) and f[0] == f[-1] and f[2] > f[0]


def test_mesh_geometry_and_flux_chain():
    p = default_project()
    m = build_mesh(p, 1e-3, 1e-3)
    assert m.n_cells == sum(p.numerics.mesh.n_r.values())
    assert math.isclose(m.radial.areas.sum(), math.pi * 0.25**2, rel_tol=1e-12)
    # region boundaries coincide with cell edges
    for reg in p.geometry.regions:
        assert np.any(np.isclose(m.radial.r_edges, reg.r_out))
    # chain inductance sums to mu0 dr zeta_top /(2 pi r) and the mutual matrix is min(zeta_i, zeta_j)
    b = m.boundaries[10]
    assert math.isclose(b.chain_dl.sum(), b.c_factor * b.levels[-1].zeta, rel_tol=1e-12)
    L = b.mutual_inductance_matrix()
    z = np.array([lv.zeta for lv in b.levels])
    assert np.allclose(L, b.c_factor * np.minimum.outer(z, z))
    assert np.all(np.linalg.eigvalsh(L) > 0)
    # flat top electrode: all cells share the same absolute top height
    ref = p.geometry.reference_region()
    tops = [reg.z_offset + c.z_top_conductor for c in m.cells for reg in [p.geometry.regions[int(m.radial.region_index[c.index])]]]
    assert np.allclose(tops, tops[0])


def test_plasma_state_profiles():
    p = default_project()
    m = build_mesh(p, 1e-3, 1e-3)
    st = compute_plasma_state(p, m.radial.r_centers, 0.028)
    assert st.n0[0] > st.n0[-1]  # parabolic profile decreases outward
    assert np.allclose(st.ns, st.h_l * st.n0)
    d = st.diagnostics(2 * math.pi * 60e6, s_tot=2e-3, l_total=0.032, l_p=0.028)
    assert d["lambda_eff"] < 2 * math.pi * C.C_LIGHT / (2 * math.pi * 60e6)
