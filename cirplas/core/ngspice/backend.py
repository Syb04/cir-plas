"""Backend selection: shared library (preferred) or batch executable."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from ..project import NgspiceSpec
from .rawfile import read_rawfile
from .shared import NgspiceError, NgspiceShared, RawResult


class BatchBackend:
    def __init__(self, executable: Optional[str] = None):
        self.exe = executable or shutil.which("ngspice")
        if not self.exe:
            raise NgspiceError("ngspice executable not found")
        self.version = "batch"

    def run(self, netlist: str, cancel: Optional[Callable[[], bool]] = None, timeout_s: Optional[float] = None) -> RawResult:
        with tempfile.TemporaryDirectory(prefix="cirplas_") as td:
            cir = Path(td) / "circuit.cir"
            raw = Path(td) / "out.raw"
            cir.write_text(netlist, encoding="utf-8")
            t0 = time.time()
            proc = subprocess.run([self.exe, "-b", "-r", str(raw), str(cir)], capture_output=True, text=True, timeout=timeout_s, cwd=td)
            msgs = (proc.stdout + proc.stderr).splitlines()
            errors = [m for m in msgs if ("error" in m.lower() and "no error" not in m.lower()) or "timestep too small" in m.lower()]
            if not raw.exists():
                res = RawResult(plot_name="", plot_type="none", scale_name="", vectors={})
            else:
                plots = read_rawfile(raw)
                res = plots[-1] if plots else RawResult(plot_name="", plot_type="none", scale_name="", vectors={})
            res.messages = msgs
            res.errors = errors
            res.runtime_s = time.time() - t0
            return res


def get_backend(spec: NgspiceSpec):
    if spec.backend in ("auto", "shared"):
        try:
            return NgspiceShared.get(spec.library_path)
        except NgspiceError:
            if spec.backend == "shared":
                raise
    return BatchBackend(spec.executable)
