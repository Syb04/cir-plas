"""Damped-potential IEDF (post/iedf.py)."""

import numpy as np

from cirplas.core.post.iedf import build_iedf, damped_voltage

N = 200
TH = np.arange(N) / N * 2 * np.pi
V = 100.0 + 80.0 * np.cos(TH)


def test_damped_voltage_limits():
    w0 = 2 * np.pi * 13.56e6
    inst = damped_voltage(V, w0, 0.0)
    assert np.isclose(inst.mean(), 100.0) and np.isclose(inst.max() - inst.min(), 160.0, rtol=1e-3)
    for wt in (0.5, 3.0, 30.0):
        ve = damped_voltage(V, w0, wt / w0)
        assert np.isclose(ve.mean(), 100.0)
        assert np.isclose(ve.max() - ve.min(), 160.0 / np.sqrt(1 + wt**2), rtol=2e-3)


def test_iedf_bimodal_and_normalized():
    ve_fast = damped_voltage(V, 1.0, 0.0)  # instantaneous response: arcsine distribution, peaks at the extremes
    ve_slow = damped_voltage(V, 1.0, 10.0)
    d = build_iedf([ve_fast, ve_slow], s_mean=np.array([1e-3, 4e-3]), lambda_i=2e-3)
    de = d.energy[1] - d.energy[0]
    assert np.allclose(d.f.sum(axis=1) * de, 1.0)
    f = d.f[0]
    lo, hi = np.argmax(f[: len(f) // 2]), len(f) // 2 + np.argmax(f[len(f) // 2:])
    assert abs(d.energy[lo] - 20.0) < 3 * de and abs(d.energy[hi] - 180.0) < 3 * de
    assert f[np.argmin(abs(d.energy - 100.0))] < 0.2 * f[hi]
    # slow ions: narrow distribution around the mean
    assert d.peak_high[1] - d.peak_low[1] < 20.0 and d.peak_low[1] < 100.0 < d.peak_high[1]
    assert np.allclose(d.collisionless_fraction, np.exp([-0.5, -2.0]))
