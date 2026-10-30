"""Parameter sweeps: expand a base project along one or two parameter paths (spec 02 section 3.5)."""

from __future__ import annotations

import copy
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..project import Project, apply_path


def get_path(d: dict, path: str) -> Any:
    node: Any = d
    for key in path.split("."):
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def set_path(d: dict, path: str, value: Any) -> None:
    node: Any = d
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


@dataclass
class SweepAxis:
    path: str
    values: list[Any]
    label: str = ""

    @classmethod
    def linspace(cls, path: str, start: float, stop: float, n: int, label: str = "") -> "SweepAxis":
        return cls(path=path, values=[float(v) for v in np.linspace(start, stop, n)], label=label or path)


@dataclass
class SweepSpec:
    mode: str = "ac"
    axes: list[SweepAxis] = field(default_factory=list)
    name: str = "sweep"

    def to_json(self) -> str:
        return json.dumps({"mode": self.mode, "name": self.name,
                           "axes": [{"path": a.path, "values": a.values, "label": a.label} for a in self.axes]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "SweepSpec":
        d = json.loads(text)
        return cls(mode=d.get("mode", "ac"), name=d.get("name", "sweep"),
                   axes=[SweepAxis(path=a["path"], values=list(a["values"]), label=a.get("label", a["path"])) for a in d["axes"]])


@dataclass
class SweepJob:
    index: int
    indices: tuple[int, ...]
    values: dict[str, Any]
    project: Project
    name: str


def expand(base: Project, spec: SweepSpec) -> list[SweepJob]:
    """Cartesian product of the axes; each job carries a validated project."""
    if not spec.axes:
        raise ValueError("sweep needs at least one axis")
    base_d = base.model_dump(mode="json")
    jobs: list[SweepJob] = []
    grids = [range(len(a.values)) for a in spec.axes]
    for k, idx in enumerate(itertools.product(*grids)):
        d = copy.deepcopy(base_d)
        vals = {}
        for a, i in zip(spec.axes, idx):
            apply_path(d, a.path, a.values[i])
            vals[a.path] = a.values[i]
        d["name"] = f"{base.name}_{spec.name}_{k:03d}"
        jobs.append(SweepJob(index=k, indices=tuple(idx), values=vals, project=Project.model_validate(d),
                             name="_".join(f"{a.path.split('.')[-1]}={a.values[i]:.4g}" if isinstance(a.values[i], float) else f"{a.path.split('.')[-1]}={a.values[i]}" for a, i in zip(spec.axes, idx))))
    return jobs


# ------------------------------------------------------------------ metrics for aggregation
def summary_metrics(h5_path: str | Path) -> dict[str, float]:
    """Scalar metrics of one run (for sweep tables and plots)."""
    import h5py

    out: dict[str, float] = {}
    with h5py.File(h5_path, "r") as f:
        idx = f["mesh/region_index"][()] if "mesh" in f else None
        wafer = (idx == idx[0]) if idx is not None else None

        def uniformity(y):
            y = np.asarray(y, dtype=float)
            if wafer is not None:
                y = y[wafer]
            return float((y.max() - y.min()) / (2 * y.mean())) if y.mean() else float("nan")

        def center_edge(y):
            y = np.asarray(y, dtype=float)
            if wafer is not None:
                y = y[wafer]
            return float(y[0] / y[-1]) if y[-1] else float("nan")

        if "ac" in f:
            g = f["ac"]
            s_e = g["cells/s_e"][()]
            vsh = np.abs(g["cells/vsh_b"][()])
            out["ac_uniformity_se"] = uniformity(s_e)
            out["ac_center_edge_se"] = center_edge(s_e)
            out["ac_uniformity_vsh"] = uniformity(vsh)
            out["ac_center_edge_vsh"] = center_edge(vsh)
            out["ac_vsh_center"] = float(vsh[0])
            for port in g["ports"]:
                out[f"ac_p_{port}"] = float(g["ports"][port].attrs["p"])
            d = g["diagnostics"].attrs
            for k in ("lambda_eff", "kR_wafer", "delta", "f_psr"):
                if k in d:
                    out[f"ac_{k}"] = float(d[k])
        if "tran" in f:
            g = f["tran"]
            s_e = g["cells/s_e"][()]
            out["tran_uniformity_se"] = uniformity(s_e)
            out["tran_center_edge_se"] = center_edge(s_e)
            out["tran_uniformity_vsh_dc"] = uniformity(g["cells/vsh_b_dc"][()])
            out["tran_uniformity_vsh_1"] = uniformity(g["cells/vsh_b_1"][()])
            out["tran_vsh_dc_center"] = float(g["cells/vsh_b_dc"][()][0])
            out["tran_s_b_center_mm"] = float(g["cells/s_b_mean"][()][0] * 1e3)
            m = g["metrics"].attrs
            out["tran_p_source"] = float(m["p_source_total"])
            out["tran_power_balance_error"] = float(m["power_balance_error"])
            for port in g["ports"]:
                a = g["ports"][port].attrs
                out[f"tran_v_dc_{port}"] = float(a["v_dc"])
                out[f"tran_v_1_{port}"] = float(a["v_1"])
                out[f"tran_z_re_{port}"] = float(a["z_re"])
                out[f"tran_z_im_{port}"] = float(a["z_im"])
        if "iteration" in f:
            n = f["iteration/n"][()]
            out["sc_n_center"] = float(n[-1][0])
            out["sc_n_mean"] = float(np.mean(n[-1]))
            out["sc_uniformity_n"] = uniformity(n[-1])
    return out
