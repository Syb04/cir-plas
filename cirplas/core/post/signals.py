"""Time-series utilities for periodic steady-state windows (spec 02 section 5)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def time_average(t: np.ndarray, y: np.ndarray) -> float:
    """Trapezoidal time average on a (possibly non-uniform) grid."""
    if len(t) < 2:
        return float(y[0]) if len(y) else float("nan")
    return float(np.trapezoid(y, t) / (t[-1] - t[0]))


def select_window(t: np.ndarray, t_start: float, t_end: float) -> np.ndarray:
    return np.where((t >= t_start - 1e-15) & (t <= t_end + 1e-15))[0]


def resample(t: np.ndarray, y: np.ndarray, t_new: np.ndarray) -> np.ndarray:
    return np.interp(t_new, t, y)


@dataclass
class Harmonics:
    """One-sided harmonic decomposition: y(t) ~ c0 + sum_k Re(c_k exp(j k w0 t))."""

    f0: float
    c: np.ndarray  # complex, c[0] real DC, c[k] amplitude*exp(j phase)

    @property
    def dc(self) -> float:
        return float(self.c[0].real)

    def amplitude(self, k: int = 1) -> float:
        return float(abs(self.c[k]))

    def phase(self, k: int = 1) -> float:
        return float(np.angle(self.c[k]))


def harmonics(y_uniform: np.ndarray, n_per_period: int, n_harm: int = 20) -> Harmonics:
    """Harmonics of a signal sampled uniformly over an integer number of base periods.

    y_uniform must contain an integer number of periods with n_per_period samples each.
    """
    n = len(y_uniform)
    n_periods = n // n_per_period
    if n_periods < 1 or n_periods * n_per_period != n:
        raise ValueError("signal length must be a multiple of n_per_period")
    Y = np.fft.rfft(y_uniform) / n
    # harmonic k of the base period sits at bin k*n_periods
    kmax = min(n_harm, (n // 2) // n_periods)
    c = np.zeros(kmax + 1, dtype=complex)
    c[0] = Y[0].real
    for k in range(1, kmax + 1):
        c[k] = 2.0 * Y[k * n_periods]
    return Harmonics(f0=float("nan"), c=c)


def uniform_grid(t_start: float, period: float, n_periods: int, n_per_period: int) -> np.ndarray:
    return t_start + np.arange(n_periods * n_per_period) * (period / n_per_period)
