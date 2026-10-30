"""Plasma parameters: electron temperature (particle balance), collision frequency,
edge factors, density profiles and electromagnetic diagnostics (spec 01 sections 3, 8, 10)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

from .constants import C_LIGHT, E_CHARGE, EPS0, K_B, M_E, MU0
from .materials import GasData, gas_db
from .project import DensitySpec, Project


# ------------------------------------------------------------------ elementary formulas
def gas_density(pressure_pa: float, temperature_k: float) -> float:
    return pressure_pa / (K_B * temperature_k)


def bohm_speed(te_ev: float, mass_kg: float) -> float:
    return math.sqrt(E_CHARGE * te_ev / mass_kg)


def electron_mean_speed(te_ev: float) -> float:
    return math.sqrt(8.0 * E_CHARGE * te_ev / (math.pi * M_E))


def plasma_frequency(n: float) -> float:
    """Electron plasma (angular) frequency [rad/s]."""
    return math.sqrt(n * E_CHARGE**2 / (EPS0 * M_E))


def debye_length(te_ev: float, n: float) -> float:
    return math.sqrt(EPS0 * te_ev / (E_CHARGE * n))


def skin_depth_collisionless(n: float) -> float:
    return C_LIGHT / plasma_frequency(n)


def complex_conductivity(n: float, nu_m: float, omega: float) -> complex:
    """Drude conductivity sigma_p = eps0 w_pe^2 / (nu_m + j w)."""
    return EPS0 * plasma_frequency(n) ** 2 / (nu_m + 1j * omega)


def skin_depth(n: float, nu_m: float, omega: float) -> float:
    """Field penetration depth 1/Re(k) with k = sqrt(j w mu0 sigma_p) (collisional + inertial)."""
    k = np.sqrt(1j * omega * MU0 * complex_conductivity(n, nu_m, omega))
    return float(1.0 / k.real)


def ion_mean_free_path(n_g: float, sigma_i: float) -> float:
    return 1.0 / (n_g * sigma_i)


def h_l_factor(l_p: float, lambda_i: float) -> float:
    """Axial edge-to-center density ratio (L&L eq. 5.3.14)."""
    return 0.86 / math.sqrt(3.0 + l_p / (2.0 * lambda_i))


def h_r_factor(radius: float, lambda_i: float) -> float:
    """Radial edge-to-center density ratio (L&L eq. 5.3.15)."""
    return 0.80 / math.sqrt(4.0 + radius / lambda_i)


def d_eff(radius: float, l_p: float, hl: float, hr: float) -> float:
    """Effective plasma size for particle balance in a cylinder (L&L eq. 10.2.11)."""
    return 0.5 * radius * l_p / (radius * hl + l_p * hr)


def te_particle_balance(gas: GasData, n_g: float, deff: float, bracket: tuple[float, float] = (0.3, 30.0)) -> float:
    """Solve K_iz(Te) n_g d_eff = u_B(Te) for Te [eV]."""

    def f(te: float) -> float:
        return math.log(gas.K_iz(te) * n_g * deff) - math.log(bohm_speed(te, gas.mass_kg))

    lo, hi = bracket
    flo, fhi = f(lo), f(hi)
    if flo > 0 or fhi < 0:
        raise ValueError(f"particle balance has no root in Te=[{lo},{hi}] eV (n_g={n_g:.3e}, d_eff={deff:.3e})")
    return float(brentq(f, lo, hi, xtol=1e-6))


def density_profile(spec: DensitySpec, r: np.ndarray, r_ref: float) -> np.ndarray:
    r = np.asarray(r, dtype=float)
    if spec.model == "uniform":
        return np.full_like(r, spec.n0)
    if spec.model == "parabolic":
        return spec.n0 * (1.0 - spec.alpha * (r / r_ref) ** 2)
    if spec.model == "table":
        rs = np.array([p[0] for p in spec.table])
        ns = np.array([p[1] for p in spec.table])
        return np.interp(r, rs, ns)
    if spec.model == "file":
        data = np.loadtxt(spec.path, delimiter=",", comments="#")
        return np.interp(r, data[:, 0], data[:, 1])
    raise ValueError(spec.model)


# ------------------------------------------------------------------ Drude element values
def drude_inductance(n: float, length: float, area: float) -> float:
    """Electron inertia inductance L_e = m_e l / (e^2 n S) [H]."""
    return M_E * length / (E_CHARGE**2 * n * area)


def drude_resistance(n: float, nu_m: float, length: float, area: float) -> float:
    return nu_m * drude_inductance(n, length, area)


def displacement_capacitance(length: float, area: float, eps_r: float = 1.0) -> float:
    return eps_r * EPS0 * area / length


# ------------------------------------------------------------------ aggregate state
@dataclass
class PlasmaState:
    """Plasma parameters evaluated on the radial cell centers."""

    gas: GasData
    te: float  # eV
    n_g: float
    lambda_i: float
    h_l: float
    h_r: float
    nu_m: float
    u_b: float
    v_e: float
    r_centers: np.ndarray
    n0: np.ndarray  # center-plane electron density per cell
    ns: np.ndarray  # sheath-edge density per cell
    l_p_ref: float
    notes: dict = field(default_factory=dict)

    @property
    def omega_pe(self) -> np.ndarray:
        return np.sqrt(self.n0 * E_CHARGE**2 / (EPS0 * M_E))

    @property
    def lambda_d(self) -> np.ndarray:
        return np.sqrt(EPS0 * self.te / (E_CHARGE * self.ns))

    def diagnostics(self, omega: float, s_tot: float, l_total: float, l_p: float) -> dict:
        """Global EM diagnostics at angular frequency omega using mean density."""
        n_mean = float(np.mean(self.n0))
        ns_mean = float(np.mean(self.ns))
        wpe = plasma_frequency(n_mean)
        delta = skin_depth(n_mean, self.nu_m, omega)
        w_psr = wpe * math.sqrt(s_tot / l_p) if l_p > 0 else float("inf")
        lam0 = 2 * math.pi * C_LIGHT / omega
        factor = 1.0 - (omega / w_psr) ** 2
        lam_eff = lam0 * math.sqrt(s_tot / l_total) * math.sqrt(factor) if factor > 0 else float("nan")
        return {
            "n_mean": n_mean,
            "ns_mean": ns_mean,
            "omega_pe": wpe,
            "f_pe": wpe / (2 * math.pi),
            "lambda_d": debye_length(self.te, ns_mean),
            "delta": delta,
            "delta_collisionless": C_LIGHT / wpe,
            "nu_m": self.nu_m,
            "omega_psr": w_psr,
            "f_psr": w_psr / (2 * math.pi),
            "lambda_eff": lam_eff,
            "k_eff": 2 * math.pi / lam_eff if lam_eff == lam_eff else float("nan"),
        }


def compute_plasma_state(project: Project, r_centers: np.ndarray, l_p_ref: float) -> PlasmaState:
    """Evaluate Te, nu_m, h_l and the density profiles for the given radial cell centers.

    l_p_ref: representative bulk thickness [m] (gap minus nominal sheaths) used in the
    particle balance and h_l.
    """
    ps = project.plasma
    gas = gas_db(ps.gas)
    n_g = gas_density(ps.pressure_pa, ps.gas_temperature_k)
    lambda_i = ion_mean_free_path(n_g, gas.sigma_i)
    r_wall = project.geometry.r_wall

    hl = ps.h_l.value if ps.h_l.mode == "manual" else h_l_factor(l_p_ref, lambda_i)
    hr = h_r_factor(r_wall, lambda_i)

    if ps.te.mode == "manual":
        te = float(ps.te.value)
    else:
        te = te_particle_balance(gas, n_g, d_eff(r_wall, l_p_ref, hl, hr))

    nu_m = ps.nu_m.value if ps.nu_m.mode == "manual" else gas.nu_m(te, n_g)

    r_centers = np.asarray(r_centers, dtype=float)
    n0 = density_profile(ps.density, r_centers, r_wall)
    if np.any(n0 <= 0):
        raise ValueError("density profile must be positive on all cells")
    ns = hl * n0

    return PlasmaState(
        gas=gas,
        te=te,
        n_g=n_g,
        lambda_i=lambda_i,
        h_l=hl,
        h_r=hr,
        nu_m=nu_m,
        u_b=bohm_speed(te, gas.mass_kg),
        v_e=electron_mean_speed(te),
        r_centers=r_centers,
        n0=n0,
        ns=ns,
        l_p_ref=l_p_ref,
    )
