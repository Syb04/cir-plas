"""V5 (linear part): coupling between the wafer region and the capacitively fed focus-ring region
(spec 05 section V5, items 1-3, evaluated with the fast quasi-linear AC solution)."""

import numpy as np
import pytest

from cirplas.core.analysis.ac import run_ac
from cirplas.core.analysis.common import RunContext
from cirplas.core.project import Layer, Project, default_project


def _project(coupling_ring_t=2.0e-3, same_stack=False, n_wafer=24, n_fr=8):
    p = default_project()
    for port in p.circuit.ports.values():
        port.l_feed = 0.0
        port.c_stray = 100e-12
    d = p.model_dump()
    regions = d["geometry"]["regions"]
    wafer = next(r for r in regions if r["name"] == "wafer")
    fr = next(r for r in regions if r["name"] == "fr")
    if same_stack:
        fr["stack"] = list(wafer["stack"])
        fr["z_offset"] = 0.0
        gap = next(r for r in regions if r["name"] == "edge_gap")
        gap["stack"] = list(wafer["stack"])
        gap["z_offset"] = 0.0
    else:
        fr["stack"][1]["t"] = coupling_ring_t
        fr["z_offset"] = -(1.0e-3 + coupling_ring_t + 4.0e-3 - 1.775e-3)
    d["numerics"]["mesh"]["n_r"] = {"wafer": n_wafer, "edge_gap": 1, "fr": n_fr, "outer": 6}
    return Project.model_validate(d)


def _ac(backend, p):
    ctx = RunContext(project=p, backend=backend, log=lambda m: None)
    return run_ac(ctx)


def test_v5_continuity_with_identical_stacks(backend):
    ac = _ac(backend, _project(same_stack=True))
    r = ac.r
    v = np.abs(ac.radial("vsh_b"))
    cells = ac.model.mesh.cells
    # jump across the wafer | edge_gap | fr boundaries compared with the typical neighbouring difference
    i_w = max(i for i, c in enumerate(cells) if c.region == "wafer")
    i_f = min(i for i, c in enumerate(cells) if c.region == "fr")
    inner_diffs = np.abs(np.diff(v[: i_w + 1]))
    typical = np.median(inner_diffs[-6:])
    jump = abs(v[i_f] - v[i_w])
    assert jump < 2.0 * typical + 1e-3 * v[i_w], (jump, typical)


def test_v5_coupling_ring_thickness_trend(backend):
    """A thicker coupling ring (smaller coupling capacitance) lowers the FR sheath voltage monotonically and
    steepens the sheath-width gradient inside the wafer edge."""
    ts = [1.0e-3, 2.0e-3, 4.0e-3, 8.0e-3]
    v_fr, grad_edge = [], []
    for t in ts:
        ac = _ac(backend, _project(coupling_ring_t=t))
        cells = ac.model.mesh.cells
        fr_idx = [i for i, c in enumerate(cells) if c.region == "fr"]
        w_idx = [i for i, c in enumerate(cells) if c.region == "wafer"]
        v = np.abs(ac.radial("vsh_b"))
        areas = ac.radial("area")
        v_fr.append(np.average(v[fr_idx], weights=areas[fr_idx]))
        # sheath width ~ V^0.5 for matrix sheaths: use the voltage slope over the last 10 mm of the wafer as a proxy
        r = ac.r
        sel = [i for i in w_idx if r[i] > 0.14]
        grad_edge.append(abs(np.polyfit(r[sel], v[sel], 1)[0]))
    assert all(a > b for a, b in zip(v_fr, v_fr[1:])), v_fr
    assert grad_edge[-1] > grad_edge[0], grad_edge


def test_v5_mesh_refinement_stability(backend):
    a = _ac(backend, _project(n_wafer=24, n_fr=8))
    b = _ac(backend, _project(n_wafer=48, n_fr=16))
    for ac in (a, b):
        assert ac.iterations <= 12
    va = np.abs(a.radial("vsh_b"))
    vb = np.abs(b.radial("vsh_b"))
    # compare on the coarse grid by interpolation; edge metrics within 5 %
    vb_i = np.interp(a.r, b.r, vb)
    wafer = np.array([c.region == "wafer" for c in a.model.mesh.cells])
    rel = np.abs(va[wafer] - vb_i[wafer]) / va[wafer]
    assert np.max(rel) < 0.05, np.max(rel)


def test_v5_separate_electrode_equivalence(backend):
    """A conductive FR fed through the ESC (esc_coupled) must agree with the separate-electrode representation
    in which the FR electrode F is tied to W through the lumped coupling capacitance C_WF = sum of the per-cell
    series capacitances of the dielectric layers under the FR (spec 05 V5 item 4)."""
    from cirplas.core.constants import EPS0
    from cirplas.core.materials import resolve_material

    p = _project()
    ac0 = _ac(backend, p)
    cells = ac0.model.mesh.cells
    fr_cells = [c for c in cells if c.region == "fr"]
    # distributed coupling capacitance: ESC ceramic + coupling ring in series, per cell, summed over the FR area
    c_wf = 0.0
    for c in fr_cells:
        inv = 0.0
        for L in c.bottom_layers[:-1]:  # all layers below the (conductive) FR
            inv += L.t / (L.material.eps_r * EPS0 * c.area)
        c_wf += 1.0 / inv
    d = p.model_dump()
    fr = next(r for r in d["geometry"]["regions"] if r["name"] == "fr")
    fr["conductor"] = "F"
    fr["stack"] = [fr["stack"][-1]]  # only the conductive FR itself sits on the F electrode
    fr["z_offset"] = fr["z_offset"] + sum(L["t"] for L in next(r for r in p.model_dump()["geometry"]["regions"] if r["name"] == "fr")["stack"][:-1])
    d["circuit"]["fr"] = {"type": "separate", "tuning": {"type": "coupling_to_w", "c": c_wf, "r": 0.0}}
    d["circuit"]["c_wf"] = {"mode": "manual", "value": 0.0}
    p2 = Project.model_validate(d)
    ac1 = _ac(backend, p2)
    v0 = np.abs(ac0.radial("vsh_b"))
    v1 = np.abs(ac1.radial("vsh_b"))
    fr_idx = [i for i, c in enumerate(cells) if c.region == "fr"]
    areas = ac0.radial("area")
    vfr0 = np.average(v0[fr_idx], weights=areas[fr_idx])
    vfr1 = np.average(v1[fr_idx], weights=areas[fr_idx])
    assert abs(vfr1 - vfr0) / vfr0 < 0.03, (vfr0, vfr1)
    wafer = [i for i, c in enumerate(cells) if c.region == "wafer"]
    assert np.max(np.abs(v1[wafer] - v0[wafer]) / v0[wafer]) < 0.03
