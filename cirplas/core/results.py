"""HDF5 results writer/reader (spec 03 section 4.2)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

from .. import __version__ as _pkg_version
from .analysis.ac import AcResult
from .project import Project


def _version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:
        return "0.0.0"


def _write_scalar_attrs(grp: h5py.Group, d: dict) -> None:
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, (dict, list, tuple)):
            grp.attrs[k] = json.dumps(v)
        else:
            grp.attrs[k] = v


def _write_mesh(f: h5py.File, model) -> None:
    g = f.require_group("mesh")
    rm = model.mesh.radial
    g.create_dataset("r_edges", data=rm.r_edges)
    g.create_dataset("r_centers", data=rm.r_centers)
    g.create_dataset("areas", data=rm.areas)
    g.create_dataset("region_index", data=rm.region_index)
    g.attrs["region_names"] = json.dumps(rm.region_names)
    g.attrs["n_z"] = model.mesh.n_z
    g.create_dataset("s_b", data=np.array([c.s_b for c in model.mesh.cells]))
    g.create_dataset("s_t", data=np.array([c.s_t for c in model.mesh.cells]))
    g.create_dataset("l_gap", data=np.array([c.l_gap for c in model.mesh.cells]))
    g.create_dataset("l_p", data=np.array([c.l_p for c in model.mesh.cells]))
    g.create_dataset("z_surface_b", data=np.array([c.z_surface_b for c in model.mesh.cells]))
    g.create_dataset("z_top_conductor", data=np.array([c.z_top_conductor for c in model.mesh.cells]))
    pl = model.plasma
    gp = f.require_group("plasma")
    gp.create_dataset("n0", data=pl.n0)
    gp.create_dataset("ns", data=pl.ns)
    _write_scalar_attrs(gp, {"te": pl.te, "n_g": pl.n_g, "lambda_i": pl.lambda_i, "h_l": pl.h_l, "h_r": pl.h_r,
                             "nu_m": pl.nu_m, "u_b": pl.u_b, "v_e": pl.v_e, "gas": pl.gas.name})


def write_results(path: str | Path, project: Project, ac: Optional[AcResult] = None, tran=None, netlists: Optional[dict[str, str]] = None,
                  log: Optional[list[str]] = None, ngspice_version: str = "", mode: str = "") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        meta = f.require_group("meta")
        _write_scalar_attrs(meta, {"schema_version": 1, "cirplas_version": _pkg_version, "ngspice_version": ngspice_version,
                                   "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "project_hash": project.hash(), "mode": mode,
                                   "project_name": project.name})
        f.create_dataset("project", data=project.to_json())
        if netlists:
            g = f.require_group("netlist")
            for k, v in netlists.items():
                g.create_dataset(k, data=v)
        model = tran.model if tran is not None else (ac.model if ac is not None else None)
        if model is not None:
            _write_mesh(f, model)
        if ac is not None:
            g = f.require_group("ac")
            g.attrs["frequency"] = ac.frequency
            g.attrs["iterations"] = ac.iterations
            _write_scalar_attrs(g.require_group("diagnostics"), ac.diagnostics)
            cells = g.require_group("cells")
            for name in ("r_center", "area", "vsh_b", "vsh_t", "i_sc_b", "i_sc_t", "j_z", "p_ohm_bulk", "p_stoc_b", "p_stoc_t", "c_lin_b", "c_lin_t"):
                cells.create_dataset(name, data=ac.radial(name))
            cells.create_dataset("s_e", data=ac.radial("s_e"))
            ports = g.require_group("ports")
            for pr in ac.ports:
                gp = ports.require_group(pr.port)
                gp.attrs["v"] = pr.v
                gp.attrs["i"] = pr.i
                gp.attrs["z"] = pr.z
                gp.attrs["p"] = pr.p
            est = g.require_group("estimates")
            est.create_dataset("v1_b", data=ac.estimates.v1_b)
            est.create_dataset("v1_t", data=ac.estimates.v1_t)
            est.create_dataset("s_b", data=ac.estimates.s_b)
            est.create_dataset("s_t", data=ac.estimates.s_t)
            est.attrs["v_dc_guess"] = json.dumps(ac.estimates.v_dc_guess)
        if tran is not None:
            g = f.require_group("tran")
            _write_scalar_attrs(g, {"period": tran.period, "cycles": tran.cycles, "meas_cycles": tran.meas_cycles,
                                    "v_dc_clamp": tran.v_dc_clamp, "bias_iterations": tran.bias_iterations,
                                    "pss_converged": bool(tran.pss_converged), "pss_change": tran.pss_change,
                                    "runtime_s": tran.raw.runtime_s})
            g.create_dataset("history", data=json.dumps(tran.history))
            po = tran.post
            cells = g.require_group("cells")
            for name in ("r_center", "area", "vsh_b_dc", "vsh_b_1", "vsh_b_min", "vsh_b_max", "vsh_t_dc", "vsh_t_1", "s_b_mean", "s_b_max",
                         "s_t_mean", "p_ohm_bulk", "p_stoc_b", "p_stoc_t", "p_ion_b", "p_ion_t", "p_el_b", "p_el_t", "j_z_1"):
                cells.create_dataset(name, data=po.radial(name))
            cells.create_dataset("s_e", data=po.radial("s_e"))
            ports = g.require_group("ports")
            for pr in po.ports:
                gp = ports.require_group(pr.port)
                _write_scalar_attrs(gp, asdict(pr))
            _write_scalar_attrs(g.require_group("metrics"), {"p_source_total": po.p_source_total, "p_total_dissipated": po.p_total_dissipated,
                                                                "power_balance_error": po.power_balance_error, "p_ohm_radial_total": po.p_ohm_radial_total})
            if project.outputs.save_waveforms:
                w = g.require_group("window")
                t = tran.raw.scale
                t_start = t[-1] - tran.meas_cycles * tran.period
                m = t >= t_start - 1e-15
                w.create_dataset("time", data=t[m], compression="gzip", compression_opts=4)
                names = sorted(tran.raw.vectors)
                data = np.stack([tran.raw.vectors[n][m] for n in names], axis=1)
                w.create_dataset("vectors", data=data, compression="gzip", compression_opts=4)
                w.create_dataset("names", data=np.array(names, dtype=h5py.string_dtype()))
        if log:
            f.create_dataset("log", data="\n".join(log))


def read_summary(path: str | Path) -> dict:
    out: dict = {}
    with h5py.File(path, "r") as f:
        out["meta"] = dict(f["meta"].attrs)
        if "ac" in f:
            out["ac"] = {"frequency": f["ac"].attrs["frequency"], "diagnostics": dict(f["ac/diagnostics"].attrs),
                         "cells": {k: f["ac/cells"][k][()] for k in f["ac/cells"]}}
        if "tran" in f:
            out["tran"] = {"attrs": dict(f["tran"].attrs), "metrics": dict(f["tran/metrics"].attrs),
                           "cells": {k: f["tran/cells"][k][()] for k in f["tran/cells"]},
                           "ports": {p: dict(f["tran/ports"][p].attrs) for p in f["tran/ports"]}}
        if "mesh" in f:
            out["mesh"] = {k: f["mesh"][k][()] for k in f["mesh"]}
    return out
