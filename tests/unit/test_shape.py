"""Top-electrode shape optimization without a circuit solver: the spline limits, and the search on a synthetic heating model."""

import numpy as np
import pytest

from cirplas.core.analysis import shape as sh
from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.shape import Profile, ShapeBasis, ShapeSpec, gap_table, optimize_shape, with_shape
from cirplas.core.mesh import build_mesh
from cirplas.core.project import coupling_ring_project, default_project


def _random_feasible(basis, rng, n=200):
    """Points of the constraint set by rejection from the box, scaled towards the (feasible) origin."""
    out = []
    while len(out) < n:
        x = rng.uniform(basis.lo, basis.hi)
        if len(basis.b_slope):
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(basis.a_slope @ x > 0, basis.b_slope / (basis.a_slope @ x), np.inf)
            x = x * min(1.0, float(ratio.min()))
        out.append(x)
    return out


def test_limits_hold_everywhere_along_the_electrode():
    p = default_project()
    spec = ShapeSpec(dz_max=8e-3, max_slope=0.1, min_gap=26e-3)
    basis = ShapeBasis(p, spec)
    r = np.linspace(0.0, p.geometry.r_wall, 4001)
    surface = np.array([next(g.z_surface_abs for g, (r0, r1) in zip(p.geometry.regions, p.geometry.region_bounds()) if r0 <= ri <= r1) for ri in r])
    for x in _random_feasible(basis, np.random.default_rng(1)):
        d = basis.displacement(x, r)
        assert np.max(np.abs(d)) <= 8e-3 + 1e-9
        assert np.max(np.abs(np.gradient(d, r))) <= 0.1 + 1e-3
        assert np.min(p.geometry.gap_at(0.0) + d - surface) >= 26e-3 - 1e-9


def test_axis_slope_is_zero_and_the_rim_stays():
    p = default_project()
    x = np.array([3.0, -2.0, 1.0, 4.0, -1.0, 2.0])
    pinned = ShapeBasis(p, ShapeSpec())
    assert pinned.displacement(x, np.array([p.geometry.r_wall]))[0] == pytest.approx(0.0, abs=1e-12)
    d = pinned.displacement(x, np.array([0.0, 1e-5]))
    assert abs(d[1] - d[0]) / 1e-5 < 1e-3  # against slopes of order 0.1 elsewhere
    free = ShapeBasis(p, ShapeSpec(pin_rim=False))
    assert free.displacement(x, np.array([p.geometry.r_wall]))[0] == pytest.approx(2e-3)


def test_table_is_valid_and_keeps_everything_but_the_gap():
    p = default_project()
    spec = ShapeSpec()
    basis = ShapeBasis(p, spec)
    q = with_shape(p, *gap_table(basis, np.array([2.0, 2.0, 1.0, 0.0, 0.0, 0.0]), spec))
    assert q.geometry.gap.type == "table" and q.geometry.gap.table[-1][0] == p.geometry.r_wall
    assert q.geometry.gap.datum == p.geometry.gap.datum and q.geometry.gap.value == p.geometry.gap.value
    assert q.geometry.gap_at(0.0) == pytest.approx(p.geometry.gap.value + 2e-3, abs=1e-6)
    a, b = p.model_dump(mode="json"), q.model_dump(mode="json")
    a["geometry"].pop("gap"), b["geometry"].pop("gap")
    assert a == b


def test_restart_from_a_shaped_electrode():
    p = default_project()
    spec = ShapeSpec()
    basis = ShapeBasis(p, spec)
    q = with_shape(p, *gap_table(basis, np.array([4.0, 4.0, 2.0, 0.0, 0.0, 0.0]), spec))
    again = ShapeBasis(q, spec)
    r, g = gap_table(again, np.zeros(6), spec)
    assert np.allclose(g, [q.geometry.gap_at(float(v)) for v in r], atol=1e-9)
    assert np.all(again.b_slope >= 0)  # the start is feasible for the new search


def test_datum_surface_measures_the_floor_from_the_reference_region():
    p = coupling_ring_project()  # gap 30 mm above the wafer, measured from the wafer surface
    basis = ShapeBasis(p, ShapeSpec(min_gap=28e-3, dz_max=20e-3))
    assert basis.lo.max() <= 0
    assert basis.lo[0] == pytest.approx(-2.0, abs=1e-6)  # 2 mm of room above the wafer


def test_profile_measures():
    prof = Profile(r=np.array([0.1, 0.2, 0.3, 0.4]), s_e=np.array([90.0, 110.0, 100.0, 500.0]), weights=np.array([0.25, 0.25, 0.5, 0.0]), power=1.0)
    assert prof.mean == pytest.approx(100.0)
    assert prof.uniformity == pytest.approx(0.1)
    assert prof.rms == pytest.approx(np.sqrt(0.25 * 0.01 * 2))
    assert np.sum(np.sqrt(prof.weights[:3]) * prof.residual) == pytest.approx(0.0)


def test_heating_share_follows_the_cell_widths():
    """A branch between unequal cells lies mostly in the wider one; a uniform J_r then heats both cells alike."""
    p = default_project()
    mesh = build_mesh(p, 3e-3, 2e-3)
    for b in mesh.boundaries:
        wa, wb = mesh.radial.widths[b.index], mesh.radial.widths[b.index + 1]
        assert b.frac_inner == pytest.approx(wa / (wa + wb))


# ---------------------------------------------------------------------- the search on a synthetic model
R = np.linspace(0.002, 0.248, 60)
TARGET = np.where(R <= 0.15, np.gradient(R), 0.0)
TARGET = TARGET / TARGET.sum()
S0 = 1000.0 * (1.0 + 0.5 * (R / 0.15) ** 2)  # edge-high heating at the flat electrode
Z0 = default_project().geometry.gap.value


def _model(gain_fine=60.0, gain_jacobian=None, fail=lambda d: False):
    """ln S_e = ln S0 - gain * dz(r) per metre of displacement; `gain_jacobian` makes the "ac" stand-in disagree with "tran"."""
    calls = {"tran": 0, "ac": 0, "sc": 0}

    def evaluate(ctx, project, mode, spec, warm=None, refine=True):
        calls[mode] += 1
        dz = np.array([project.geometry.gap_at(float(r)) for r in R]) - Z0
        if fail(dz):
            raise RuntimeError("solver failed")
        gain = gain_jacobian if (mode == "ac" and gain_jacobian is not None) else gain_fine
        scale = 1.5 if mode == "tran" else 1.0  # the two models also disagree on the level
        return Profile(r=R, s_e=scale * S0 * np.exp(-gain * dz), weights=TARGET, power=100.0)

    return evaluate, calls


def _ctx(cancel=lambda: False):
    return RunContext(project=default_project(), backend=None, log=lambda m: None, cancel=cancel)


def test_search_flattens_the_profile(monkeypatch):
    evaluate, calls = _model()
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(), ShapeSpec(mode="ac", dz_max=30e-3, target_rms=0.004))
    assert res.start.rms > 0.1
    assert res.best.rms < 0.01, (res.best.rms, res.stop_reason)
    assert res.evaluations <= 12


def test_tran_mode_converges_on_the_transient_with_a_wrong_jacobian(monkeypatch):
    """The Jacobian model is 40 % too sensitive: the transient profile still ends flat, one transient per step."""
    evaluate, calls = _model(gain_fine=60.0, gain_jacobian=85.0)
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(), ShapeSpec(mode="tran", dz_max=30e-3, target_rms=0.01, max_eval=10))
    assert res.best.rms < 0.012, (res.best.rms, res.stop_reason)
    assert calls["tran"] == res.evaluations <= 10
    assert calls["ac"] == res.jacobian_evaluations > 0


def test_the_limits_stop_the_search_and_are_reported(monkeypatch):
    evaluate, _ = _model()
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(), ShapeSpec(mode="ac", dz_max=1e-3))
    assert 0 < res.best.rms < res.start.rms
    assert np.max(np.abs(res.gap_best - res.gap_start)) <= 1e-3 + 1e-6
    assert "変位の上限" in res.active or "変位の下限・最小ギャップ" in res.active


def test_a_failing_point_is_rejected_and_the_search_goes_on(monkeypatch):
    evaluate, _ = _model(fail=lambda d: np.max(np.abs(d)) > 1.5e-3)
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(), ShapeSpec(mode="ac", dz_max=30e-3, fd_step=0.5e-3))
    assert any(h["error"] for h in res.history)
    assert res.best.rms < res.start.rms
    assert np.max(np.abs(res.gap_best - res.gap_start)) <= 1.5e-3


def test_cancel_returns_the_best_shape_so_far(monkeypatch):
    evaluate, calls = _model()
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(cancel=lambda: calls["ac"] >= 10), ShapeSpec(mode="ac", dz_max=30e-3))
    assert res.cancelled and res.stop_reason == "中止"
    assert res.best.rms <= res.start.rms


def test_the_budget_counts_only_the_requested_model(monkeypatch):
    evaluate, calls = _model(gain_jacobian=85.0)
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    res = optimize_shape(_ctx(), ShapeSpec(mode="tran", dz_max=30e-3, target_rms=1e-6, tol=0.0, max_eval=3))
    assert calls["tran"] == 3 and res.stop_reason == "評価回数の上限"


def test_a_rejected_point_is_not_evaluated_again(monkeypatch):
    """A step pinned by the limits does not shrink with the damping; the second try would repeat a slow solve for nothing."""
    evaluate, calls = _model()
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    monkeypatch.setattr(sh, "_step", lambda x, res, jac, basis, mu: np.full_like(x, -3.0))  # always the wrong way
    res = optimize_shape(_ctx(), ShapeSpec(mode="ac", dz_max=30e-3))
    assert res.evaluations == 2 and res.stop_reason == "これ以上改善する方向がない"


def test_a_start_that_cannot_be_solved_is_an_error(monkeypatch):
    evaluate, _ = _model(fail=lambda d: True)
    monkeypatch.setattr(sh, "evaluate_profile", evaluate)
    with pytest.raises(RuntimeError, match="starting shape"):
        optimize_shape(_ctx(), ShapeSpec(mode="ac"))


def test_unknown_region_and_mode():
    with pytest.raises(ValueError, match="region"):
        ShapeBasis(default_project(), ShapeSpec(target_region="nope"))
    with pytest.raises(ValueError, match="mode"):
        optimize_shape(_ctx(), ShapeSpec(mode="foo"))
