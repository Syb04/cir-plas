"""Shared helpers for the analysis drivers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ..elements import CircuitModel, build_circuit_model
from ..mesh import Mesh, build_mesh
from ..plasma import PlasmaState, compute_plasma_state
from ..project import Project
from ..sheath_models import make_sheath_params


@dataclass
class RunContext:
    project: Project
    backend: object  # NgspiceShared or BatchBackend
    log: Callable[[str], None] = print
    progress: Callable[[dict], None] = lambda d: None
    cancel: Callable[[], bool] = lambda: False


@dataclass
class Estimates:
    """Per-cell RF sheath voltage amplitudes and nominal sheath widths used for mesh / linearization."""

    v1_b: np.ndarray
    v1_t: np.ndarray
    s_b: np.ndarray
    s_t: np.ndarray
    v_dc_guess: dict[str, float] = field(default_factory=dict)  # port -> DC bias guess [V]


def total_source_amplitude(project: Project, port: str = "W") -> float:
    ps = project.circuit.ports.get(port)
    if ps is None:
        return 0.0
    return float(sum(s.v for s in ps.sources))


def initial_estimates(project: Project, n_cells: int) -> Estimates:
    v_rf = total_source_amplitude(project, "W")
    if project.numerics.sheath_voltage_guess is not None:
        v1_b = np.full(n_cells, project.numerics.sheath_voltage_guess)
        v1_t = np.full(n_cells, 0.3 * project.numerics.sheath_voltage_guess)
    elif project.geometry.top.conductor == "driven":
        v1_b = np.full(n_cells, 0.5 * (v_rf + total_source_amplitude(project, "T")))
        v1_t = v1_b.copy()
    else:
        v1_b = np.full(n_cells, 0.8 * v_rf)
        v1_t = np.full(n_cells, 0.2 * v_rf)
    v1_b = np.maximum(v1_b, 5.0)
    v1_t = np.maximum(v1_t, 5.0)
    return Estimates(v1_b=v1_b, v1_t=v1_t, s_b=np.full(n_cells, 1e-3), s_t=np.full(n_cells, 1e-3))


def nominal_widths(project: Project, mesh: Mesh, plasma: PlasmaState, est: Estimates) -> tuple[np.ndarray, np.ndarray]:
    spec = project.plasma.sheath
    s_b = np.zeros(mesh.n_cells)
    s_t = np.zeros(mesh.n_cells)
    if spec.linear_fixed_width:
        s_b[:] = spec.linear_fixed_width
        s_t[:] = spec.linear_fixed_width
        return s_b, s_t
    for i, c in enumerate(mesh.cells):
        sp = make_sheath_params(c.area, float(plasma.ns[i]), plasma.te, plasma.gas.mass_kg, spec)
        s_b[i] = sp.nominal_width(float(est.v1_b[i]))
        s_t[i] = sp.nominal_width(float(est.v1_t[i]))
        # keep the bulk at least 40 % of the gap
        cap = 0.3 * c.l_gap
        s_b[i] = min(s_b[i], cap)
        s_t[i] = min(s_t[i], cap)
    return s_b, s_t


def build_model(project: Project, est: Estimates, l_p_ref: Optional[float] = None) -> CircuitModel:
    """Mesh + plasma + element values consistent with the given estimates (one fixed-point pass)."""
    mesh0 = build_mesh(project, est.s_b, est.s_t)
    if l_p_ref is None:
        l_p_ref = float(np.mean([c.l_p for c in mesh0.cells]))
    plasma = compute_plasma_state(project, mesh0.radial.r_centers, l_p_ref)
    s_b, s_t = nominal_widths(project, mesh0, plasma, est)
    est.s_b, est.s_t = s_b, s_t
    mesh = build_mesh(project, s_b, s_t)
    plasma = compute_plasma_state(project, mesh.radial.r_centers, float(np.mean([c.l_p for c in mesh.cells])))
    return build_circuit_model(project, mesh, plasma, est.v1_b, est.v1_t)


def check_errors(res, what: str) -> None:
    from ..ngspice.shared import NgspiceError

    if res.errors:
        raise NgspiceError(f"{what}: " + " | ".join(res.errors[:3]))
    if res.plot_type == "none" or not res.vectors:
        raise NgspiceError(f"{what}: no output vectors (messages: {' | '.join(res.messages[-5:])})")
