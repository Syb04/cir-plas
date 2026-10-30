"""ctypes binding to the ngspice shared library (sharedspice API).

One ngspice instance per process. Runs are executed with ``bg_run`` so that a
cancel request can stop them (``bg_halt``); results are read back through
``ngGet_Vec_Info`` after the background thread has finished.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
import time
from ctypes import CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_double, c_int, c_short, c_void_p
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np


class _VecValues(Structure):
    _fields_ = [("name", c_char_p), ("creal", c_double), ("cimag", c_double), ("is_scale", c_bool), ("is_complex", c_bool)]


class _VecValuesAll(Structure):
    _fields_ = [("veccount", c_int), ("vecindex", c_int), ("vecsa", POINTER(POINTER(_VecValues)))]


class _VecInfo(Structure):
    _fields_ = [("number", c_int), ("vecname", c_char_p), ("is_real", c_bool), ("pdvec", c_void_p), ("pdvecscale", c_void_p)]


class _VecInfoAll(Structure):
    _fields_ = [("name", c_char_p), ("title", c_char_p), ("date", c_char_p), ("type", c_char_p), ("veccount", c_int), ("vecs", POINTER(POINTER(_VecInfo)))]


class _NgComplex(Structure):
    _fields_ = [("cx_real", c_double), ("cx_imag", c_double)]


class _VectorInfo(Structure):
    _fields_ = [("v_name", c_char_p), ("v_type", c_int), ("v_flags", c_short), ("v_realdata", POINTER(c_double)),
                ("v_compdata", POINTER(_NgComplex)), ("v_length", c_int)]


_SendChar = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
_SendStat = CFUNCTYPE(c_int, c_char_p, c_int, c_void_p)
_ControlledExit = CFUNCTYPE(c_int, c_int, c_bool, c_bool, c_int, c_void_p)
_SendData = CFUNCTYPE(c_int, POINTER(_VecValuesAll), c_int, c_int, c_void_p)
_SendInitData = CFUNCTYPE(c_int, POINTER(_VecInfoAll), c_int, c_void_p)
_BGThreadRunning = CFUNCTYPE(c_int, c_bool, c_int, c_void_p)


class NgspiceError(RuntimeError):
    pass


@dataclass
class RawResult:
    """Vectors of one ngspice plot (tran or ac)."""

    plot_name: str
    plot_type: str  # "tran" | "ac" | "op" | ...
    scale_name: str
    vectors: dict[str, np.ndarray]
    messages: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    runtime_s: float = 0.0

    @property
    def scale(self) -> np.ndarray:
        if not self.scale_name or self.scale_name not in self.vectors:
            return np.zeros(0)
        return self.vectors[self.scale_name]

    def get(self, name: str) -> np.ndarray:
        key = name.lower()
        if key in self.vectors:
            return self.vectors[key]
        # allow v(node) <-> node
        if key.startswith("v(") and key.endswith(")") and key[2:-1] in self.vectors:
            return self.vectors[key[2:-1]]
        raise KeyError(f"vector '{name}' not found (have {len(self.vectors)} vectors, e.g. {list(self.vectors)[:5]})")

    def has(self, name: str) -> bool:
        key = name.lower()
        return key in self.vectors or (key.startswith("v(") and key[2:-1] in self.vectors)


def find_library(explicit: Optional[str] = None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("CIRPLAS_NGSPICE_LIB")
    if env:
        candidates.append(env)
    if sys.platform.startswith("win"):
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("ngspice.dll", "libngspice-0.dll", "libngspice.dll"):
            candidates += [os.path.join(here, name), os.path.join(os.path.dirname(sys.executable), name), name]
        candidates += [r"C:\Spice64\bin_dll\ngspice.dll", r"C:\msys64\mingw64\bin\libngspice-0.dll"]
    else:
        for name in ("ngspice", "libngspice.so.0", "libngspice.so", "libngspice.dylib"):
            found = ctypes.util.find_library(name) if not name.startswith("lib") else name
            if found:
                candidates.append(found)
    for c in candidates:
        try:
            ctypes.CDLL(c)
            return c
        except OSError:
            continue
    raise NgspiceError("ngspice shared library not found (set numerics.ngspice.library_path or CIRPLAS_NGSPICE_LIB)")


class NgspiceShared:
    """Thin wrapper around libngspice. Not thread-safe; one instance per process."""

    _instance: Optional["NgspiceShared"] = None

    def __init__(self, library_path: Optional[str] = None):
        if NgspiceShared._instance is not None:
            raise NgspiceError("only one NgspiceShared instance per process is supported; use NgspiceShared.get()")
        self.path = find_library(library_path)
        self.lib = ctypes.CDLL(self.path)
        self._messages: list[str] = []
        self._errors: list[str] = []
        self._exit_requested = False
        self._bg_running = False
        self._lock = threading.Lock()
        self._setup_prototypes()
        # keep references to callbacks (ctypes requires it)
        self._cb_char = _SendChar(self._on_char)
        self._cb_stat = _SendStat(self._on_stat)
        self._cb_exit = _ControlledExit(self._on_exit)
        self._cb_data = _SendData(self._on_data)
        self._cb_init = _SendInitData(self._on_init)
        self._cb_bg = _BGThreadRunning(self._on_bg)
        rc = self.lib.ngSpice_Init(self._cb_char, self._cb_stat, self._cb_exit, self._cb_data, self._cb_init, self._cb_bg, None)
        if rc != 0:
            raise NgspiceError(f"ngSpice_Init failed with code {rc}")
        self.version = self._query_version()
        NgspiceShared._instance = self

    @classmethod
    def get(cls, library_path: Optional[str] = None) -> "NgspiceShared":
        if cls._instance is None:
            cls(library_path)
        return cls._instance

    # ------------------------------------------------------------------ setup
    def _setup_prototypes(self) -> None:
        L = self.lib
        L.ngSpice_Init.restype = c_int
        L.ngSpice_Init.argtypes = [_SendChar, _SendStat, _ControlledExit, _SendData, _SendInitData, _BGThreadRunning, c_void_p]
        L.ngSpice_Command.restype = c_int
        L.ngSpice_Command.argtypes = [c_char_p]
        L.ngSpice_Circ.restype = c_int
        L.ngSpice_Circ.argtypes = [POINTER(c_char_p)]
        L.ngGet_Vec_Info.restype = POINTER(_VectorInfo)
        L.ngGet_Vec_Info.argtypes = [c_char_p]
        L.ngSpice_CurPlot.restype = c_char_p
        L.ngSpice_AllPlots.restype = POINTER(c_char_p)
        L.ngSpice_AllVecs.restype = POINTER(c_char_p)
        L.ngSpice_AllVecs.argtypes = [c_char_p]
        L.ngSpice_running.restype = c_bool

    def _query_version(self) -> str:
        self._messages.clear()
        self.lib.ngSpice_Command(b"version -s")
        for m in self._messages:
            if "ngspice-" in m:
                return m.strip().split("ngspice-", 1)[1].split()[0].strip(":")
        return "unknown"

    # ------------------------------------------------------------------ callbacks
    def _on_char(self, msg: bytes, ident: int, user: c_void_p) -> int:
        text = msg.decode("utf-8", errors="replace")
        if text.startswith("stdout "):
            text = text[7:]
        elif text.startswith("stderr "):
            text = text[7:]
        self._messages.append(text)
        low = text.lower()
        if ("error" in low and "no error" not in low) or "timestep too small" in low or "singular matrix" in low:
            self._errors.append(text)
        return 0

    def _on_stat(self, msg: bytes, ident: int, user: c_void_p) -> int:
        return 0

    def _on_exit(self, status: int, immediate: bool, quit_: bool, ident: int, user: c_void_p) -> int:
        self._exit_requested = True
        self._errors.append(f"ngspice requested exit (status {status})")
        return 0

    def _on_data(self, vals: POINTER(_VecValuesAll), count: int, ident: int, user: c_void_p) -> int:
        return 0

    def _on_init(self, info: POINTER(_VecInfoAll), ident: int, user: c_void_p) -> int:
        return 0

    def _on_bg(self, not_running: bool, ident: int, user: c_void_p) -> int:
        self._bg_running = not not_running
        return 0

    # ------------------------------------------------------------------ commands
    def command(self, cmd: str) -> None:
        rc = self.lib.ngSpice_Command(cmd.encode("utf-8"))
        if rc != 0:
            raise NgspiceError(f"ngspice command failed: {cmd}")

    def load_circuit(self, netlist: str) -> None:
        lines = [ln for ln in netlist.splitlines()]
        arr = (c_char_p * (len(lines) + 1))()
        for k, ln in enumerate(lines):
            arr[k] = ln.encode("utf-8")
        arr[len(lines)] = None
        self._messages.clear()
        self._errors.clear()
        rc = self.lib.ngSpice_Circ(arr)
        if rc != 0 or self._errors:
            raise NgspiceError("circuit load failed:\n" + "\n".join(self._errors or self._messages[-10:]))

    def run(self, netlist: str, cancel: Optional[Callable[[], bool]] = None, poll_s: float = 0.02, timeout_s: Optional[float] = None) -> RawResult:
        """Load and simulate a netlist (bg_run), then collect all vectors of the current plot."""
        with self._lock:
            self.command("destroy all")
            try:
                self.command("remcirc")
            except NgspiceError:
                pass
            self.load_circuit(netlist)
            self._messages.clear()
            self._errors.clear()
            t0 = time.time()
            self._bg_running = True
            self.command("bg_run")
            halted = False
            while True:
                time.sleep(poll_s)
                if not self.lib.ngSpice_running():
                    break
                if cancel is not None and cancel():
                    self.command("bg_halt")
                    halted = True
                    break
                if timeout_s is not None and time.time() - t0 > timeout_s:
                    self.command("bg_halt")
                    self._errors.append(f"timeout after {timeout_s} s")
                    break
            # wait for the background thread to settle
            for _ in range(500):
                if not self.lib.ngSpice_running():
                    break
                time.sleep(poll_s)
            runtime = time.time() - t0
            if halted:
                self._errors.append("cancelled")
            res = self._collect()
            res.messages = list(self._messages)
            res.errors = list(self._errors)
            res.runtime_s = runtime
            return res

    # ------------------------------------------------------------------ vectors
    def _collect(self) -> RawResult:
        plot = self.lib.ngSpice_CurPlot()
        plot_name = plot.decode() if plot else ""
        if not plot_name or plot_name.startswith("const"):
            return RawResult(plot_name=plot_name, plot_type="none", scale_name="", vectors={})
        names_ptr = self.lib.ngSpice_AllVecs(plot_name.encode())
        names = []
        k = 0
        while True:
            nm = names_ptr[k]
            if not nm:
                break
            names.append(nm.decode())
            k += 1
        vectors: dict[str, np.ndarray] = {}
        scale_name = ""
        plot_type = "unknown"
        for nm in names:
            info = self.lib.ngGet_Vec_Info(f"{plot_name}.{nm}".encode())
            if not info:
                continue
            vi = info.contents
            n = vi.v_length
            if vi.v_realdata:
                arr = np.ctypeslib.as_array(vi.v_realdata, shape=(n,)).astype(np.float64, copy=True)
            elif vi.v_compdata:
                raw = np.ctypeslib.as_array(ctypes.cast(vi.v_compdata, POINTER(c_double)), shape=(2 * n,)).astype(np.float64, copy=True)
                arr = raw[0::2] + 1j * raw[1::2]
            else:
                continue
            vectors[nm.lower()] = arr
            if nm.lower() == "time":
                scale_name, plot_type = "time", "tran"
            elif nm.lower() == "frequency":
                scale_name, plot_type = "frequency", "ac"
        return RawResult(plot_name=plot_name, plot_type=plot_type, scale_name=scale_name, vectors=vectors)

    def destroy(self) -> None:
        try:
            self.command("destroy all")
        except NgspiceError:
            pass
