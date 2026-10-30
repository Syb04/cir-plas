"""The free-form schematic mode reproduces the port-chain mode (same circuit drawn as a schematic)."""

import numpy as np
import pytest

from cirplas.core.analysis.ac import run_ac
from cirplas.core.analysis.common import RunContext
from cirplas.core.project import Project, default_project
from cirplas.core.schematic import schematic_from_ports


def _ctx(p, ngspice):
    from cirplas.core.ngspice.backend import get_backend

    return RunContext(project=p, backend=get_backend(p.numerics.ngspice), log=lambda *a: None, progress=lambda *a: None)


def _as_schematic(p: Project) -> Project:
    d = p.model_dump(mode="json")
    d["circuit"]["mode"] = "schematic"
    d["circuit"]["schematic"] = schematic_from_ports(p.circuit, p.electrodes()).model_dump(mode="json")
    return Project.model_validate(d)


def _variants():
    out = {"default": default_project()}
    d = default_project().model_dump(mode="json")
    d["geometry"]["regions"][2]["conductor"] = "F"
    d["circuit"]["fr"] = {"type": "separate", "tuning": {"type": "coupling_to_w", "c": 300e-12, "l": 50e-9, "r": 0.0}}
    out["fr_couple"] = Project.model_validate(d)
    d = default_project().model_dump(mode="json")
    d["circuit"]["ports"]["W"]["generator"] = {"type": "l_match", "auto": False, "l": 300e-9, "c": 400e-12}
    out["generator"] = Project.model_validate(d)
    return out


@pytest.mark.parametrize("name", ["default", "fr_couple", "generator"])
def test_ac_power_matches_port_mode(ngspice, name):
    p = _variants()[name]
    a = run_ac(_ctx(p, ngspice))
    b = run_ac(_ctx(_as_schematic(p), ngspice))
    pa = sum(pr.p for pr in a.ports)
    pb = sum(pr.p for pr in b.ports)
    assert abs(pb - pa) <= 1e-3 * abs(pa) + 1e-6, (pa, pb)
    # sheath voltages (the physics seen by the plasma) are identical
    np.testing.assert_allclose(np.abs(b.radial("vsh_b")), np.abs(a.radial("vsh_b")), rtol=1e-3)
