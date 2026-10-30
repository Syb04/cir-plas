"""An auto-tuned L match makes the generator see its own impedance at the fundamental."""

import numpy as np

from cirplas.core.analysis.ac import run_ac
from cirplas.core.analysis.common import RunContext
from cirplas.core.project import GeneratorSpec, default_project


def test_auto_match_input_impedance(ngspice):
    p = default_project()
    for port in p.circuit.ports.values():
        port.l_feed = 0.0
        port.c_stray = 100e-12
    p.circuit.ports["W"].generator = GeneratorSpec(type="l_match", r_gen=50.0, auto=True)
    p.circuit.ports["W"].sources[0].v = 300.0
    ctx = RunContext(project=p, backend=ngspice, log=lambda m: None)
    ac = run_ac(ctx)
    m = ac.matching["W"]
    assert m["l"] > 0 and m["c"] > 0
    z_in = m["z_in"]
    # the quasi-linear iteration changes the load slightly after the design -> a few percent mismatch is expected
    assert abs(z_in - 50.0) / 50.0 < 0.15, z_in
    pw = next(pr for pr in ac.ports if pr.port == "W")
    assert abs(pw.p - 300.0**2 / 400.0) / (300.0**2 / 400.0) < 0.2
