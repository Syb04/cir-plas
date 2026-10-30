"""Sheath element models (spec 01 section 3.2).

The sheath between plasma node P and wall node W (V = V_P - V_W >= 0) is a
charge-based nonlinear capacitor Q(V) in series with the stochastic resistance,
in parallel with the ion current and the Boltzmann electron current.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .constants import E_CHARGE, EPS0, M_E, VT_27C
from .plasma import bohm_speed, debye_length, electron_mean_speed
from .project import SheathSpec


def s1_factor(model: str) -> float:
    """Sheath width at V = Te in units of the Debye length."""
    if model == "matrix":
        return math.sqrt(2.0)
    if model == "child":
        return 2.0**1.25 / 3.0
    raise ValueError(f"unknown sheath model '{model}'")


def beta_of(model: str) -> float:
    return {"matrix": 0.5, "child": 0.75}[model]


@dataclass(frozen=True)
class SheathCellParams:
    area: float
    ns: float
    te: float
    beta: float
    s1: float  # sheath width at V = Te [m]
    vmin: float  # voltage offset giving the minimum sheath width [V]
    kq: float  # charge prefactor: Q = kq * (V+vmin)^(1-beta)
    eps: float  # softplus smoothing width [V]
    rst: float  # stochastic resistance [ohm] (0 = off)
    ii: float  # ion current [A]
    ie0: float  # electron saturation current [A]
    nd: float  # diode emission coefficient N = Te/VT
    model: str

    # ------------------------------------------------------------ element functions
    @property
    def c_max(self) -> float:
        """Capacitance at V = 0 (minimum sheath width); kept constant for V < 0."""
        return self.kq * (1.0 - self.beta) * self.vmin ** (-self.beta)

    def _u(self, v):
        return 0.5 * (v + np.sqrt(v * v + self.eps * self.eps))

    def _up(self, v):
        return 0.5 * (1.0 + v / np.sqrt(v * v + self.eps * self.eps))

    def charge(self, v):
        """Sheath charge Q(V) [C]: power law for V > 0, linear with slope c_max for V < 0 (smooth blend)."""
        v = np.asarray(v, dtype=float)
        u = self._u(v)
        return self.kq * (u + self.vmin) ** (1.0 - self.beta) + self.c_max * (v - u)

    def capacitance(self, v):
        """Differential capacitance dQ/dV [F]."""
        v = np.asarray(v, dtype=float)
        u = self._u(v)
        return (self.kq * (1.0 - self.beta) * (u + self.vmin) ** (-self.beta) - self.c_max) * self._up(v) + self.c_max

    def width(self, v):
        """Sheath width s(V) = eps0 A / C(V) [m]."""
        return EPS0 * self.area / self.capacitance(v)

    def electron_current(self, v):
        """Boltzmann electron current to the wall [A]: Ie0 exp(-V/Te) for V > -3 Te, linear continuation below
        (matches the SPICE B-source realization)."""
        x = -np.asarray(v, dtype=float) / self.te
        return self.ie0 * np.where(x < 3.0, np.exp(np.minimum(x, 3.0)), np.exp(3.0) * (1.0 + x - 3.0))

    def linearized_capacitance(self, v1: float, n_theta: int = 512) -> float:
        """Fundamental-harmonic capacitance C_lin = Q_1/V_1 for V(t) = V_1 (1 + cos wt)."""
        th = np.linspace(0.0, 2 * np.pi, n_theta, endpoint=False)
        v = v1 * (1.0 + np.cos(th))
        q = self.charge(v)
        q1 = 2.0 * np.mean(q * np.cos(th))
        return float(q1 / v1)

    def nominal_width(self, v1: float, n_theta: int = 512) -> float:
        """Time-averaged sheath width for V(t) = V_1 (1 + cos wt)."""
        th = np.linspace(0.0, 2 * np.pi, n_theta, endpoint=False)
        return float(np.mean(self.width(v1 * (1.0 + np.cos(th)))))

    def dc_floating_voltage(self) -> float:
        return self.te * math.log(self.ie0 / self.ii)


def stochastic_resistance(ns: float, te: float, area: float) -> float:
    """Hard-wall stochastic heating as a series resistance: R = m_e v_e / (e^2 n_s A)."""
    return M_E * electron_mean_speed(te) / (E_CHARGE**2 * ns * area)


def ion_current(ns: float, te: float, mass_kg: float, area: float) -> float:
    return E_CHARGE * ns * bohm_speed(te, mass_kg) * area


def electron_saturation_current(ns: float, te: float, area: float) -> float:
    return 0.25 * E_CHARGE * ns * electron_mean_speed(te) * area


def make_sheath_params(area: float, ns: float, te: float, mass_kg: float, spec: SheathSpec) -> SheathCellParams:
    beta = beta_of(spec.model)
    lam_d = debye_length(te, ns)
    s1 = s1_factor(spec.model) * lam_d
    s_min = spec.alpha_min * lam_d
    vmin = te * (s_min / s1) ** (1.0 / beta)
    kq = EPS0 * area * te**beta / (s1 * (1.0 - beta))
    rst = spec.stochastic.kappa * stochastic_resistance(ns, te, area) if spec.stochastic.model == "hardwall" else 0.0
    return SheathCellParams(
        area=area,
        ns=ns,
        te=te,
        beta=beta,
        s1=s1,
        vmin=vmin,
        kq=kq,
        eps=spec.smoothing * vmin,
        rst=rst,
        ii=ion_current(ns, te, mass_kg, area),
        ie0=electron_saturation_current(ns, te, area),
        nd=te / VT_27C,
        model=spec.model,
    )
