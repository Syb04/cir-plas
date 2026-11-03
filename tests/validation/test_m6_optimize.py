"""Uniformity optimization (M6): automatic FR tuning on the representative configuration (AC objective)."""

import numpy as np

from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.optimize import OptimizeSpec, OptParam, Tie, apply_values, optimize
from cirplas.core.project import Project

from test_v5_fr_coupling import _project  # noqa: E402 (tests/validation is on sys.path)


def _separate_fr_source(v_f=400.0):
    """FR on its own electrode F driven by a second 60 MHz source (spec 01 FR tuning 'source')."""
    d = _project().model_dump()
    fr = next(r for r in d["geometry"]["regions"] if r["name"] == "fr")
    fr["conductor"] = "F"
    fr["z_offset"] += sum(L["t"] for L in fr["stack"][:-1])
    fr["stack"] = [fr["stack"][-1]]
    d["circuit"]["fr"] = {"type": "separate", "tuning": {"type": "source"}}
    d["circuit"]["ports"]["F"] = {"sources": [{"f": 60e6, "v": v_f, "phase_deg": 0.0}], "blocking_c": 2e-9, "c_stray": 100e-12}
    d["circuit"]["c_wf"] = {"mode": "manual", "value": 0.0}
    return Project.model_validate(d)


def test_optimize_fr_source_amplitude_nulls_the_step(backend):
    spec = OptimizeSpec(params=[OptParam("circuit.ports.F.sources.0.v", 200.0, 800.0)], objective="fr_step", grid=5, max_eval=25)
    res = optimize(RunContext(project=_separate_fr_source(), backend=backend, log=lambda m: None), spec)
    assert res.objective < 2e-3, res.objective
    assert res.evaluations <= 25
    steps = [h["metrics"]["fr_step_signed"] for h in res.history[:5]]
    assert steps[0] < 0 < steps[-1]  # the scan brackets the optimum
    assert res.project.circuit.ports["F"].sources[0].v == res.values["circuit.ports.F.sources.0.v"]


def test_optimize_two_parameters_amplitude_and_phase(backend):
    spec = OptimizeSpec(params=[OptParam("circuit.ports.F.sources.0.v", 200.0, 800.0), OptParam("circuit.ports.F.sources.0.phase_deg", -60.0, 60.0)],
                        objective="fr_step", grid=3, max_eval=40)
    res = optimize(RunContext(project=_separate_fr_source(), backend=backend, log=lambda m: None), spec)
    assert res.objective < 0.01, res.objective
    assert min(h["objective"] for h in res.history[:9]) >= res.objective


def test_ties_move_dependent_values():
    p = _project()
    spec = OptimizeSpec(params=[OptParam("geometry.regions.2.stack.1.t", 1e-3, 8e-3, log=True)],
                        ties=[Tie("geometry.regions.2.z_offset", 0, -1.0, -(1.0e-3 + 4.0e-3 - 1.775e-3))])
    q = apply_values(p, spec, [3e-3])
    assert q.geometry.regions[2].stack[1].t == 3e-3
    assert np.isclose(q.geometry.regions[2].z_offset, -(1.0e-3 + 3e-3 + 4.0e-3 - 1.775e-3))
    assert np.isclose(spec.params[0].from_unit(spec.params[0].to_unit(3e-3)), 3e-3)
