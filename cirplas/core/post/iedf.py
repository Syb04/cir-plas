"""Ion energy distribution at the electrode (spec 01 section 9, M6).

Damped-potential model (Miller & Riley 1997): ions entering the sheath at phase t0 respond to the sheath voltage
through a first-order low-pass with the ion transit time tau_i,

    tau_i dV_eff/dt = V_sh(t) - V_eff(t),

so in periodic steady state every harmonic n of the base period is scaled by H_n = 1/(1 + j n w0 tau_i). Ions enter
uniformly in time (constant Bohm flux), hence the IEDF is the distribution of e V_eff(t) over one period. The limits
are the instantaneous response (tau_i -> 0, IEDF = distribution of e V_sh) and the single peak at e<V_sh> for
w0 tau_i >> 1, with the bimodal peak separation 2 e V_1 |H_1|.

Ion-neutral collisions in the sheath are not resolved: the fraction of ions crossing without a collision,
exp(-s_mean/lambda_i), is reported with the distribution of the collisionless ions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..constants import E_CHARGE


def ion_transit_time(s_mean: float, v_dc: float, mass_kg: float) -> float:
    """Collisionless transit time through a Child-like sheath, tau_i = 3 s sqrt(M/(2 e V)) [s]."""
    return 3.0 * s_mean * math.sqrt(mass_kg / (2.0 * E_CHARGE * max(v_dc, 1e-3)))


def damped_voltage(v_period: np.ndarray, w0: float, tau_i: float, n_out: int = 2048) -> np.ndarray:
    """V_eff over one base period (uniform samples of V_sh over exactly one period in, n_out samples out)."""
    v = np.asarray(v_period, dtype=float)
    c = np.fft.rfft(v) / len(v)
    n = np.arange(len(c))
    c = c / (1.0 + 1j * n * w0 * tau_i)
    # zero-padded synthesis on a finer grid (the response is smooth: no new harmonics)
    m = n_out // 2 + 1
    cf = np.zeros(m, dtype=complex)
    k = min(m, len(c))
    cf[:k] = c[:k]
    if len(v) % 2 == 0 and k == len(c) and k < m:
        cf[k - 1] *= 0.5  # Nyquist bin of an even-length input is shared by +/- frequencies
    return np.fft.irfft(cf * n_out, n_out)


@dataclass
class Iedf:
    energy: np.ndarray  # bin centers [eV]
    f: np.ndarray  # (n_cells, n_bins), normalized: sum(f) * dE = 1
    collisionless_fraction: np.ndarray  # (n_cells,) exp(-s_mean / lambda_i)
    peak_low: np.ndarray  # (n_cells,) min e V_eff [eV]
    peak_high: np.ndarray  # (n_cells,) max e V_eff [eV]


def build_iedf(v_eff: list[np.ndarray], s_mean: np.ndarray, lambda_i: float, n_bins: int = 200) -> Iedf:
    """Histogram the damped voltages of all cells on a common energy grid."""
    e_max = max(float(np.max(v)) for v in v_eff) if v_eff else 1.0
    e_max = 1.05 * max(e_max, 1e-3)
    edges = np.linspace(0.0, e_max, n_bins + 1)
    de = edges[1] - edges[0]
    f = np.zeros((len(v_eff), n_bins))
    for i, v in enumerate(v_eff):
        h, _ = np.histogram(np.clip(v, 0.0, e_max), bins=edges)
        f[i] = h / (h.sum() * de) if h.sum() else 0.0
    frac = np.exp(-np.asarray(s_mean, dtype=float) / lambda_i) if lambda_i > 0 else np.ones(len(v_eff))
    return Iedf(energy=0.5 * (edges[1:] + edges[:-1]), f=f, collisionless_fraction=frac,
                peak_low=np.array([float(np.min(v)) for v in v_eff]), peak_high=np.array([float(np.max(v)) for v in v_eff]))
