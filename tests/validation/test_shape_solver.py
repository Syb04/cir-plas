"""Top-electrode shape optimization on the circuit solver: flatter S_e under the limits, in each evaluation mode."""

import json

import numpy as np
import pytest

from cirplas.core.analysis.ac import run_ac
from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.shape import ShapeSpec, evaluate_profile, optimize_shape
from cirplas.core.project import Project, default_project


def _ctx(project, backend):
    return RunContext(project=project, backend=backend, log=lambda m: None)


def _coarse():
    """Few cells and cycles: a transient in seconds rather than minutes."""
    d = default_project().model_dump(mode="json")
    d["numerics"]["mesh"]["n_r"] = {"wafer": 8, "fr": 3, "outer": 3}
    d["numerics"]["tran"].update(pts_per_cycle=100, cycles_min=6, cycles_max=30, meas_cycles=2)
    d["numerics"]["dc_bias"].update(cycles_a=10, max_iter=1)
    return Project.model_validate(d)


def test_no_heating_spike_at_the_region_boundary(backend):
    """The wafer-edge cell is much narrower than its neighbor across the boundary: its S_e must still follow the profile."""
    ac = run_ac(_ctx(default_project(), backend))
    s = ac.radial("s_e")
    edge = max(i for i, c in enumerate(ac.model.mesh.cells) if c.region == "wafer")
    assert abs(s[edge] / s[edge - 1] - 1.0) < 0.1
    assert abs(s[edge + 1] / s[edge] - 1.0) < 0.2


def test_ac_mode_reduces_the_nonuniformity_under_the_limits(backend):
    p = default_project()
    res = optimize_shape(_ctx(p, backend), ShapeSpec(mode="ac", dz_max=10e-3, max_slope=0.15))
    assert res.best.rms < 0.75 * res.start.rms
    assert np.max(np.abs(res.gap_best - res.gap_start)) <= 10e-3 + 1e-6
    assert np.max(np.abs(np.gradient(res.gap_best, res.r_shape))) <= 0.15 + 1e-3
    # the reported profile belongs to the returned project
    again = evaluate_profile(_ctx(res.project, backend), res.project, "ac", ShapeSpec())
    assert again.rms == pytest.approx(res.best.rms, rel=0.02)


def test_sc_mode_reaches_the_target(backend):
    res = optimize_shape(_ctx(default_project(), backend), ShapeSpec(mode="sc", target_rms=0.005))
    assert res.start.rms > 0.02
    assert res.best.rms <= 0.005, (res.best.rms, res.stop_reason)
    assert res.project.plasma.density == default_project().plasma.density  # the warm-start density stays internal


def test_density_objective_flattens_the_density(backend):
    res = optimize_shape(_ctx(default_project(), backend), ShapeSpec(mode="sc", objective="density"))
    assert res.best.rms < 0.35 * res.start.rms, (res.best.rms, res.stop_reason)
    assert res.project.plasma.density == default_project().plasma.density


def test_density_objective_with_the_tran_heating(backend):
    p = _coarse()
    res = optimize_shape(_ctx(p, backend), ShapeSpec(mode="sc", objective="density", tran_heating=True, max_eval=3))
    assert res.best.rms < res.start.rms, res.history
    assert not res.project.numerics.density_iteration.refine_with_tran  # the choice belongs to the search, not to the project


def test_tran_mode_improves_the_transient_profile(backend):
    p = _coarse()
    res = optimize_shape(_ctx(p, backend), ShapeSpec(mode="tran", max_eval=4))
    assert res.evaluations <= 4 and res.jacobian_evaluations > 0
    assert res.best.rms < res.start.rms, res.history


def test_power_specified_drive_is_honored(backend):
    d = default_project().model_dump(mode="json")
    d["circuit"]["ports"]["W"]["sources"][0]["power"] = 500.0
    p = Project.model_validate(d)
    fixed = evaluate_profile(_ctx(default_project(), backend), default_project(), "ac", ShapeSpec())
    prof = evaluate_profile(_ctx(p, backend), p, "ac", ShapeSpec())
    assert abs(prof.power - 500.0) < 0.6 * abs(fixed.power - 500.0)  # AC corrects the amplitude once (P ~ V^2), it does not iterate


def test_worker_cli_density_defaults_to_sc(backend, tmp_path):
    from cirplas.worker.cli import main

    base = tmp_path / "p.cirplas.json"
    base.write_text(default_project().to_json(), encoding="utf-8")
    hist = tmp_path / "shape.json"
    assert main(["shape", str(base), "-o", str(tmp_path / "b.json"), "--history", str(hist), "--objective", "density", "--max-eval", "2", "--progress", "none"]) == 0
    h = json.loads(hist.read_text(encoding="utf-8"))
    assert h["mode"] == "sc" and h["objective"] == "density" and h["best"]["values"] == h["best"]["density"]
    # a combination the model cannot answer is refused, not silently run
    assert main(["shape", str(base), "-o", str(tmp_path / "c.json"), "--objective", "density", "--mode", "tran", "--progress", "none"]) == 1


def test_worker_cli_writes_project_and_history(backend, tmp_path):
    from cirplas.worker.cli import main

    base = tmp_path / "p.cirplas.json"
    base.write_text(default_project().to_json(), encoding="utf-8")
    out, hist = tmp_path / "best.cirplas.json", tmp_path / "shape.json"
    assert main(["shape", str(base), "-o", str(out), "--history", str(hist), "--mode", "ac", "--max-eval", "3", "--progress", "none"]) == 0
    q = Project.model_validate_json(out.read_text(encoding="utf-8"))
    assert q.geometry.gap.type == "table"
    h = json.loads(hist.read_text(encoding="utf-8"))
    assert h["best"]["rms"] <= h["start"]["rms"] and len(h["r_shape"]) == len(h["gap_best"])
