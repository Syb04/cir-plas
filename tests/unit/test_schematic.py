"""Free-form schematic: connectivity, port derivation, DC-floating detection, conversion from port chains."""

import pytest

from cirplas.core.project import Project, Schematic, apply_path, default_project, set_source_field
from cirplas.core.schematic import extract, derive_ports, schematic_from_ports


def comp(id, type, x, y, rot=0, value=None, **kw):
    return {"id": id, "type": type, "x": x, "y": y, "rot": rot, "value": value, **kw}


PLASMA = comp("PLASMA", "Plasma", 30, 0)
SRC = comp("V1", "Vrf", 0, 3, tones=[{"f": 13.56e6, "v": 100.0}])


def sch(components, wires):
    return Schematic.model_validate({"components": components, "wires": [{"points": w} for w in wires]})


def test_series_chain_nets_and_bias():
    s = sch([SRC, comp("GND1", "GND", 0, 6), comp("C1", "C", 10, 0, value=1e-9), PLASMA],
            [[(0, 0), (7, 0)], [(13, 0), (30, 0)]])
    ex = extract(s, ["W"])
    assert ex.pin_net[("V1", "n")] == "0"
    assert ex.pin_net[("C1", "2")] == ex.electrode_net["W"]
    assert ex.pin_net[("V1", "p")] == ex.pin_net[("C1", "1")]
    assert ex.source_port == {"V1": "W"}
    assert ex.bias_ports == ["W"] and ex.problems == []


def test_t_junction_and_dc_path_to_ground():
    # source directly on W; an inductor from the middle of the wire to ground (T junction) -> W not floating
    s = sch([SRC, comp("GND1", "GND", 0, 6), PLASMA, comp("L1", "L", 15, 3, rot=90, value=1e-6), comp("GND2", "GND", 15, 6)],
            [[(0, 0), (30, 0)]])
    ex = extract(s, ["W"])
    assert ex.pin_net[("L1", "1")] == ex.electrode_net["W"]
    assert ex.bias_ports == []
    assert (15, 0) in ex.junctions


def test_labels_join_nets_and_unconnected_pins_are_reported():
    s = sch([SRC, comp("GND1", "GND", 0, 6), comp("N1", "Label", 0, 0, label="rf"), comp("N2", "Label", 30, 0, label="rf"), PLASMA,
             comp("R1", "R", 50, 50, value=10.0)], [])
    ex = extract(s, ["W"])
    assert ex.electrode_net["W"] == "u_rf" == ex.pin_net[("V1", "p")]
    assert any("R1" in p for p in ex.problems)


def test_validation_errors():
    with pytest.raises(ValueError):
        sch([PLASMA], [])  # no RF source
    with pytest.raises(ValueError):
        sch([SRC, PLASMA, comp("X1", "R", 0, 0, value=1.0)], [])  # wrong letter
    with pytest.raises(ValueError):
        sch([SRC, PLASMA, comp("C1", "C", 0, 0, value=-1.0)], [])


@pytest.mark.parametrize("variant", ["default", "fr_couple", "fr_source", "gen", "top"])
def test_conversion_from_ports_is_consistent(variant):
    d = default_project().model_dump(mode="json")
    if variant.startswith("fr"):
        d["geometry"]["regions"][2]["conductor"] = "F"
        d["circuit"]["fr"]["type"] = "separate"
        if variant == "fr_couple":
            d["circuit"]["fr"]["tuning"] = {"type": "coupling_to_w", "c": 300e-12, "l": 50e-9, "r": 0.5}
        else:
            d["circuit"]["fr"]["tuning"] = {"type": "source"}
            d["circuit"]["ports"]["F"] = {"sources": [{"f": 60e6, "v": 150.0}], "blocking_c": 1e-9}
    if variant == "gen":
        d["circuit"]["ports"]["W"]["generator"] = {"type": "l_match", "auto": False, "l": 300e-9, "c": 400e-12}
    if variant == "top":
        d["geometry"]["top"]["conductor"] = "driven"
        d["geometry"]["outer_boundary"]["type"] = "open"
        d["circuit"]["ports"]["T"] = {"sources": [{"f": 2e6, "v": 200.0}], "blocking_c": 5e-9}
    p = Project.model_validate(d)
    s = schematic_from_ports(p.circuit, p.electrodes())
    ex = extract(s, p.electrodes())
    assert not [q for q in ex.problems if "未接続" in q], ex.problems
    d["circuit"]["mode"] = "schematic"
    d["circuit"]["schematic"] = s.model_dump(mode="json")
    q = Project.model_validate(d)
    for port, ps in p.circuit.ports.items():
        assert [t.f for t in q.circuit.ports[port].sources] == [t.f for t in ps.sources]
    assert set(p.bias_ports()) <= set(q.bias_ports())


def test_source_paths_in_schematic_mode():
    d = default_project().model_dump(mode="json")
    p = Project.model_validate(d)
    d["circuit"]["mode"] = "schematic"
    d["circuit"]["schematic"] = schematic_from_ports(p.circuit, p.electrodes()).model_dump(mode="json")
    apply_path(d, "circuit.ports.W.sources.0.v", 250.0)
    assert Project.model_validate(d).circuit.ports["W"].sources[0].v == 250.0
    set_source_field(d, "W", 0, "f", 13.56e6)
    q = Project.model_validate(d)
    assert q.frequencies() == [13.56e6]
    ports, mapping, _ = derive_ports(q.circuit.schematic, q.electrodes())
    assert mapping["W"] == [("VW", 0)]
