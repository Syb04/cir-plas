"""Periodic-steady-state detection while (or after) the transient runs (spec 02 section 3.3, M6).

`PssMonitor` receives the time points of a few representative signals (port voltages and currents, bulk potentials of
three cells), resamples every completed base period onto a uniform phase grid and compares the fundamental and second
harmonic amplitudes of the latest period with those `compare_cycles - 1` periods earlier -- the same criterion as the
post-run check (`tran._pss_change`).

* Shared library: the monitor is fed from ngspice's SendData callback and the run is halted as soon as the criterion is
  met (early stop at the first steady period after `min_cycles`).
* Batch executable (no callback): the monitor replays the finished run; the per-period changes decay geometrically,
  so `predict_extra_cycles` extrapolates how many more periods the next run needs instead of extending blindly.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


def normalize_name(name: str) -> str:
    """'V(w)' / 'w' -> 'w'; 'vcb_w#branch' / 'i(vcb_w)' -> 'vcb_w#branch' (names differ between the interfaces)."""
    n = name.lower()
    if n.startswith("v(") and n.endswith(")"):
        return n[2:-1]
    if n.startswith("i(") and n.endswith(")"):
        return n[2:-1] + "#branch"
    return n


class PssMonitor:
    def __init__(self, names: list[str], period: float, tol: float, min_cycles: int, compare_cycles: int, n_per: int = 64):
        self.names = [normalize_name(n) for n in names]
        self.period = period
        self.tol = tol
        self.min_cycles = max(int(min_cycles), compare_cycles)
        self.compare = max(int(compare_cycles), 2)
        self.n_per = n_per
        self._t: list[float] = []
        self._v: list[list[float]] = []
        self._cycle = 0  # index of the period being filled
        self.features: list[np.ndarray] = []  # per completed period: (n_signals, 3) = dc, |h1|, |h2|
        self.changes: list[float] = []  # windowed change after each completed period (nan until enough periods)
        self.stop_cycle: Optional[int] = None

    @property
    def cycles_done(self) -> int:
        return len(self.features)

    def push(self, t: float, values: list[float]) -> bool:
        """Add one time point (values in the order of `names`). Returns True once the steady state is reached."""
        if self.stop_cycle is not None:
            return True
        boundary = (self._cycle + 1) * self.period
        if t >= boundary and self._t:
            self._t.append(t)
            self._v.append(values)
            self._close_cycle(boundary)
            # keep the boundary point to start the next period
            self._t, self._v = [t], [values]
            self._cycle += 1
            if self.stop_cycle is not None:
                return True
        else:
            self._t.append(t)
            self._v.append(values)
        return False

    def _close_cycle(self, t_end: float) -> None:
        t = np.asarray(self._t)
        v = np.asarray(self._v, dtype=float)
        grid = t_end - self.period + (np.arange(self.n_per) + 0.5) * (self.period / self.n_per)
        if t[0] > grid[0] + 0.5 * self.period / self.n_per:
            self.features.append(np.full((len(self.names), 3), np.nan))  # incomplete first period
            self.changes.append(float("nan"))
            return
        feats = np.empty((v.shape[1], 3))
        for j in range(v.shape[1]):
            c = np.fft.rfft(np.interp(grid, t, v[:, j])) / self.n_per
            feats[j] = (abs(c[0]), 2 * abs(c[1]), 2 * abs(c[2]))
        self.features.append(feats)
        k = len(self.features)
        if k >= self.compare:
            a, b = self.features[k - self.compare], feats
            scale = np.maximum(np.maximum(b[:, 0], b[:, 1]), 1e-30)
            ch = float(np.nanmax(np.abs(b[:, 1:] - a[:, 1:]) / scale[:, None]))
            self.changes.append(ch)
            if k >= self.min_cycles and ch <= self.tol:
                self.stop_cycle = k
        else:
            self.changes.append(float("nan"))

    def replay(self, t: np.ndarray, columns: list[np.ndarray]) -> "PssMonitor":
        """Feed a whole finished run (batch interface)."""
        data = np.column_stack(columns)
        for k in range(len(t)):
            self.push(float(t[k]), list(data[k]))
        return self

    def predict_extra_cycles(self, n_fit: int = 6, skip: int = 0, max_extra: int = 10_000) -> Optional[int]:
        """Periods still needed for the windowed change to fall below tol, from the geometric decay of the last
        changes (the first `skip` periods, e.g. the source ramp, are ignored); None when the history is too short
        or not decaying."""
        ch = np.array([c for c in self.changes[skip:] if math.isfinite(c) and c > 0])
        if self.stop_cycle is not None:
            return 0
        if len(ch) < 3:
            return None
        y = np.log(ch[-n_fit:])
        slope = float(np.polyfit(np.arange(len(y)), y, 1)[0])
        if slope >= -1e-3:
            return None
        extra = (math.log(self.tol) - y[-1]) / slope
        return int(min(max(math.ceil(extra * 1.15) + 1, 1), max_extra))
