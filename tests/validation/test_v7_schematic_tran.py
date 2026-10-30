"""V7: a TRAN run of the demo reactor gives the same self-bias and power whether the external circuit is the
port chain or the equivalent free-form schematic (coarse mesh to keep the test short)."""

from cirplas.core.analysis.common import RunContext
from cirplas.core.analysis.tran import run_tran
from cirplas.core.project import Project, load_project
from cirplas.core.schematic import schematic_from_ports

DEMO = "cirplas/data/examples/demo_300mm_60MHz.cirplas.json"


def _run(p, backend):
    return run_tran(RunContext(project=p, backend=backend, log=lambda *a: None, progress=lambda *a: None))


def test_tran_schematic_matches_port_chain(backend):
    d = load_project(DEMO).model_dump(mode="json")
    d["numerics"]["mesh"]["n_r"] = {"wafer": 6, "edge_gap": 1, "fr": 3, "outer": 2}
    p = Project.model_validate(d)
    d["circuit"]["mode"] = "schematic"
    d["circuit"]["schematic"] = schematic_from_ports(p.circuit, p.electrodes()).model_dump(mode="json")
    s = Project.model_validate(d)
    a = _run(p, backend).post
    b = _run(s, backend).post
    pa, pb = a.ports[0], b.ports[0]
    assert abs(pb.v_1 - pa.v_1) <= 0.01 * pa.v_1
    assert abs(pb.v_dc - pa.v_dc) <= 0.08 * abs(pa.v_dc)
    assert abs(b.p_source_total - a.p_source_total) <= 0.02 * a.p_source_total
    assert abs(b.power_balance_error) < 0.03
