"""Periodic-steady-state early stop (M6) on the asymmetric single cell (slow amplitude transient after the bias iteration).

The shared-library path halts the run from the SendData callback. Here it is emulated on top of the available
backend: the full run is replayed through the same monitor and truncated where the callback would have halted.
The batch path extrapolates the per-period decay to the number of periods needed."""

import pytest

from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.pss import normalize_name
from cirplas.core.analysis.tran import run_tran
from cirplas.core.ngspice.shared import RawResult
from cirplas.core.project import (AutoOrValue, CircuitSpec, DcBiasSpec, DensitySpec, GapSpec, Geometry, MeshSpec, NumericsSpec,
                                  OuterBoundary, PlasmaSpec, PortSpec, Project, Region, SourceSpec, TopSpec, TranSpec)


def _project(cycles_min=6, cycles_max=120, tol=3e-4):
    return Project(
        name="m6_pss",
        geometry=Geometry(regions=[Region(name="wafer", r_out=0.10, conductor="W", stack=[]),
                                   Region(name="outer", r_out=0.20, conductor="G", stack=[])],
                          gap=GapSpec(value=0.03), top=TopSpec(conductor="ground", stack=[]), outer_boundary=OuterBoundary(type="short")),
        plasma=PlasmaSpec(gas="ar", pressure_pa=6.67, density=DensitySpec(model="uniform", n0=1e16), te=AutoOrValue(mode="manual", value=3.0)),
        circuit=CircuitSpec(ports={"W": PortSpec(sources=[SourceSpec(f=13.56e6, v=300.0)], blocking_c=5e-9)}),
        numerics=NumericsSpec(mesh=MeshSpec(n_r={"wafer": 1, "outer": 1}), analysis="tran",
                              tran=TranSpec(pts_per_cycle=200, ramp_cycles=1.0, cycles_min=cycles_min, cycles_max=cycles_max, meas_cycles=3, pss_tol=tol),
                              dc_bias=DcBiasSpec(method="two_stage", tol=2e-3, max_iter=4)),
    )


class EarlyStopEmulation:
    """Backend wrapper with the shared-library interface: run(monitor=...) returns the vectors up to the halt."""

    supports_monitor = True

    def __init__(self, inner):
        self.inner = inner
        self.version = getattr(inner, "version", "")
        self.halted_at = None

    def run(self, netlist, cancel=None, monitor=None):
        res = self.inner.run(netlist, cancel=cancel)
        if monitor is None:
            return res
        t = res.scale
        index = {normalize_name(k): k for k in res.vectors}
        cols = [res.vectors[index[n]] for n in monitor.names]
        stop = len(t)
        for k in range(len(t)):
            if monitor.push(float(t[k]), [float(c[k]) for c in cols]):
                stop = k + 1
                break
        self.halted_at = float(t[stop - 1])
        return RawResult(plot_name=res.plot_name, plot_type=res.plot_type, scale_name=res.scale_name,
                         vectors={k: v[:stop] for k, v in res.vectors.items()}, messages=res.messages, errors=res.errors)


def test_early_stop_halts_at_steady_state(backend):
    emu = EarlyStopEmulation(backend)
    tr = run_tran(RunContext(project=_project(), backend=emu, log=lambda m: None))
    pss = [h for h in tr.history if h["stage"] == "pss"]
    assert pss and pss[-1]["early_stop"]
    assert emu.halted_at is not None and tr.cycles < 120
    assert tr.pss_change <= 3e-4 * 1.5  # post-run check agrees with the monitor (same criterion, phase grid differs)
    assert abs(tr.post.power_balance_error) < 0.01


class WithoutMonitor:
    """Backend wrapper without the callback interface: forces the batch extrapolation path on any backend."""

    def __init__(self, inner):
        self.inner = inner
        self.version = getattr(inner, "version", "")

    def run(self, netlist, cancel=None):
        return self.inner.run(netlist, cancel=cancel)


def test_batch_prediction_needs_few_reruns(backend):
    """With enough periods after the ramp the decay is extrapolated: the steady state is reached in at most two reruns."""
    tr = run_tran(RunContext(project=_project(cycles_min=12), backend=WithoutMonitor(backend), log=lambda m: None))
    pss = [h for h in tr.history if h["stage"] == "pss"]
    assert tr.pss_converged
    assert len(pss) <= 2, [(h["cycles"], h["pss_change"], h.get("predicted_extra")) for h in pss]
    assert all(h.get("predicted_extra") for h in pss)


def test_shared_library_callback_early_stop(backend):
    """Real SendData-callback early stop (runs where the shared library is available, e.g. CI on Linux)."""
    if not getattr(backend, "supports_monitor", False):
        pytest.skip("ngspice shared library not available (batch backend)")
    tr = run_tran(RunContext(project=_project(), backend=backend, log=lambda m: None))
    pss = [h for h in tr.history if h["stage"] == "pss"]
    assert pss and pss[-1].get("early_stop") and tr.pss_converged
    assert tr.cycles < 120
