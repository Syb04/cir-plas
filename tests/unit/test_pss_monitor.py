"""Periodic-steady-state monitor (analysis/pss.py) on a synthetic decaying transient."""

import math

import numpy as np

from cirplas.core.analysis.pss import PssMonitor, normalize_name

PERIOD = 1.0
TAU = 3.0  # amplitude transient decays as exp(-t/TAU)


def _signal(t):
    return [(1.0 + 0.5 * math.exp(-t / TAU)) * math.cos(2 * math.pi * t / PERIOD), 0.2 + 0.3 * math.sin(2 * math.pi * t / PERIOD)]


def _windowed_change(k, compare):
    """Exact relative change of the fundamental of signal 0 between period k and period k - compare + 1."""
    amp = lambda c: 1.0 + 0.5 * math.exp(-(c - 0.5) / TAU) * (TAU / PERIOD) * (1 - math.exp(-PERIOD / TAU))
    return abs(amp(k) - amp(k - compare + 1)) / amp(k)


def test_monitor_stops_at_first_steady_period():
    tol, compare = 1e-3, 4
    mon = PssMonitor(["V(a)", "b"], PERIOD, tol, min_cycles=5, compare_cycles=compare, n_per=64)
    t = np.arange(0, 60 * PERIOD, PERIOD / 200)
    stop_t = None
    for tk in t:
        if mon.push(float(tk), _signal(float(tk))):
            stop_t = tk
            break
    assert mon.stop_cycle is not None and stop_t is not None
    expected = next(k for k in range(compare, 60) if _windowed_change(k, compare) <= tol)
    assert abs(mon.stop_cycle - expected) <= 1, (mon.stop_cycle, expected)
    assert stop_t < (mon.stop_cycle + 0.01) * PERIOD
    assert mon.changes[mon.stop_cycle - 1] <= tol < mon.changes[mon.stop_cycle - 2]


def test_replay_predicts_remaining_periods():
    tol, compare = 1e-4, 4
    t = np.arange(0, 12 * PERIOD, PERIOD / 200)
    cols = [np.array([_signal(x)[j] for x in t]) for j in range(2)]
    mon = PssMonitor(["a", "b"], PERIOD, tol, min_cycles=1, compare_cycles=compare).replay(t, cols)
    assert mon.stop_cycle is None
    extra = mon.predict_extra_cycles()
    needed = next(k for k in range(compare, 200) if _windowed_change(k, compare) <= tol) - mon.cycles_done
    assert extra is not None and needed <= extra <= needed * 1.4 + 2, (extra, needed)


def test_normalize_names():
    assert normalize_name("V(W)") == "w" and normalize_name("w") == "w"
    assert normalize_name("i(vcb_w)") == "vcb_w#branch" == normalize_name("vcb_w#branch")
