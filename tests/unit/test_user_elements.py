"""Round trip of the user extra netlist through the structured view used by the schematic editor."""

from cirplas.core.netlist.user_elements import UserNetlist, format_eng, format_spice_value, parse_spice_value


def test_parse_spice_values():
    assert parse_spice_value("1meg") == 1e6
    assert parse_spice_value("5mH") == 5e-3
    assert abs(parse_spice_value("2.2nF") - 2.2e-9) < 1e-20
    assert parse_spice_value("1e-9") == 1e-9
    assert parse_spice_value("abc") is None
    assert parse_spice_value("-50") == -50.0


def test_round_trip_keeps_unknown_lines():
    text = "* comment\nRu1 W u_1 10\nCu1 u_1 0 100p\nVac W 0 DC 0 AC 1 0\n.model dmod d\nXsub W 0 sub"
    n = UserNetlist.parse(text)
    assert [e.name for e in n.elements] == ["Ru1", "Cu1"]
    assert len(n.raw_lines) == 4
    assert n.to_text() == text
    assert n.user_nodes() == ["u_1"]
    assert n.next_name("R") == "Ru2" and n.next_name("C") == "Cu2" and n.next_node() == "u_2"


def test_edit_add_remove():
    n = UserNetlist.parse("Ru1 W u_1 10\nCu1 u_1 0 100p")
    e = n.elements[0]
    e.value = 22.0
    n.touch(e)
    assert n.to_text().splitlines()[0] == "Ru1 W u_1 22"
    n.add("L", "W", "0", 1e-6)
    assert n.to_text().splitlines()[-1] == "Lu1 W 0 1e-06"
    v = n.add("V", "u_2", "0", 0.0)
    assert v.to_line() == "Vu1 u_2 0 DC 0"
    n.remove(n.elements[1])
    assert [e.name for e in n.elements] == ["Ru1", "Lu1", "Vu1"]
    problems = n.problems({"W", "0"})
    assert any("u_1" in p for p in problems)  # u_1 now has a single connection
    assert any("u_2" in p for p in problems)


def test_formatting():
    assert format_spice_value(2e-9) == "2e-09"
    assert format_spice_value(0.0) == "0"
    assert format_eng(2e-9, "F") == "2 nF"
    assert format_eng(60e6, "Hz") == "60 MHz"
    assert format_eng(50, "Ω") == "50 Ω"
    assert format_eng(None) == "—"
