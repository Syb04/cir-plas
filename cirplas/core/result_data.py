"""Load an HDF5 result file into plain dictionaries (GUI views, reports)."""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np


class ResultData:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.meta: dict = {}
        self.mesh: dict = {}
        self.plasma: dict = {}
        self.ac: dict | None = None
        self.tran: dict | None = None
        self.window: dict | None = None
        self.iteration: dict | None = None
        with h5py.File(self.path, "r") as f:
            if "iteration" in f:
                g = f["iteration"]
                self.iteration = {k: g[k][()] for k in g}
                self.iteration["attrs"] = dict(g.attrs)
            self.meta = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in f["meta"].attrs.items()}
            self.project: dict = {}
            if "project" in f:
                raw = f["project"][()]
                try:
                    self.project = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
                except (ValueError, TypeError):
                    self.project = {}
            if "mesh" in f:
                self.mesh = {k: f["mesh"][k][()] for k in f["mesh"]}
                self.mesh["attrs"] = dict(f["mesh"].attrs)
            if "plasma" in f:
                self.plasma = {k: f["plasma"][k][()] for k in f["plasma"]}
                self.plasma["attrs"] = dict(f["plasma"].attrs)
            if "ac" in f:
                g = f["ac"]
                self.ac = {"frequency": float(g.attrs["frequency"]), "diagnostics": dict(g["diagnostics"].attrs),
                           "cells": {k: g["cells"][k][()] for k in g["cells"]},
                           "ports": {p: dict(g["ports"][p].attrs) for p in g["ports"]}}
            if "tran" in f:
                g = f["tran"]
                self.tran = {"attrs": dict(g.attrs), "metrics": dict(g["metrics"].attrs),
                             "cells": {k: g["cells"][k][()] for k in g["cells"]},
                             "ports": {p: dict(g["ports"][p].attrs) for p in g["ports"]},
                             "history": json.loads(g["history"][()].decode() if isinstance(g["history"][()], bytes) else str(g["history"][()]))}
                if "map" in g:
                    self.tran["map"] = {k: g["map"][k][()] for k in g["map"]}
                if "iedf" in g:
                    self.tran["iedf"] = {k: g["iedf"][k][()] for k in g["iedf"]}
                if "window" in g:
                    w = g["window"]
                    names = [n.decode() if isinstance(n, bytes) else str(n) for n in w["names"][()]]
                    self.window = {"time": w["time"][()], "names": names, "vectors": w["vectors"]}
                    # load lazily-ish: read all (window is small)
                    self.window["vectors"] = w["vectors"][()]

    @property
    def r(self) -> np.ndarray:
        return self.mesh["r_centers"]

    def vector(self, name: str) -> np.ndarray | None:
        if not self.window:
            return None
        key = name.lower()
        names = self.window["names"]
        if key in names:
            return self.window["vectors"][:, names.index(key)]
        if key.startswith("v(") and key[2:-1] in names:
            return self.window["vectors"][:, names.index(key[2:-1])]
        return None


def result_warnings(data: ResultData) -> list[str]:
    """Warning badges of a result (spec 04 section 8): power balance, periodic steady state, density convergence."""
    out = []
    if data.tran is not None:
        a, m = data.tran["attrs"], data.tran["metrics"]
        pbe = abs(float(m.get("power_balance_error", 0)))
        if pbe > 0.01:
            out.append(f"電力収支誤差 {pbe * 100:.2f} % > 1 %")
        if not bool(a.get("pss_converged", True)):
            out.append("周期定常に未達（cycles_max に到達）")
    if data.iteration is not None and not bool(data.iteration["attrs"].get("converged", True)):
        out.append("密度反復が未収束（緩和係数を下げるか最大反復を増やしてください）")
    return out
