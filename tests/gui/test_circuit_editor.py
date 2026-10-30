"""Interactive schematic editor (offscreen): placing, wiring, moving with rubber-band wires, rotating, deleting."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402


@pytest.fixture
def editor(qtbot):
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel
    from cirplas.gui.widgets.schematic import CircuitEditor

    model = ProjectModel(default_project())
    ed = CircuitEditor(model)
    ed.resize(1200, 800)
    qtbot.addWidget(ed)
    ed.show()
    return ed


def test_port_mode_shows_equivalent_schematic(editor):
    assert editor.model.get("circuit.mode") == "ports"
    types = sorted(c["type"] for c in editor.comps)
    assert types.count("Vrf") == 1 and types.count("Plasma") == 1 and "C" in types and "L" in types
    assert editor.banner.isVisible()
    assert editor.extraction is not None and editor.extraction.bias_ports == ["W"]


def test_first_edit_switches_to_schematic_mode_and_undo_returns(editor):
    m = editor.model
    c = editor.place("R", (0, -20), 0)
    assert m.get("circuit.mode") == "schematic"
    assert any(x["id"] == c["id"] for x in m.get("circuit.schematic.components"))
    assert not editor.banner.isVisible()
    assert m.is_valid  # an unconnected resistor is only a warning
    assert any(c["id"] in p for p in editor.problem_text)
    m.undo()
    assert m.get("circuit.mode") == "ports"


def test_wire_tool_connects_pins_and_netlist_uses_them(editor):
    m = editor.model
    r = editor.place("R", (0, -20), 0)  # pins at (-3,-20), (3,-20)
    g = editor.place("GND", (10, -20))
    editor.set_tool("wire")
    editor.wire_click((3, -20))
    editor.wire_click((10, -20))  # ends on the ground pin -> wire finished
    assert editor.wire_points == []
    ex = editor.extraction
    assert ex.pin_net[(r["id"], "2")] == "0"
    # connect the other resistor pin to the W electrode wire with a T junction
    plasma = next(c for c in editor.comps if c["type"] == "Plasma")
    editor.wire_click((-3, -20))
    editor.wire_click((-6, -20))
    editor.wire_click((plasma["x"] - 2, 0))  # elbow; ends on the horizontal W wire
    ex = editor.extraction
    assert ex.pin_net[(r["id"], "1")] == ex.electrode_net["W"]
    assert m.is_valid
    from cirplas.core.analysis.common import build_model, initial_estimates
    from cirplas.core.mesh import build_radial_mesh
    from cirplas.core.netlist.builder import AcSettings, NetlistBuilder

    p = m.project()
    n = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
    text, idx = NetlistBuilder(build_model(p, initial_estimates(p, n)), "ac", ac=AcSettings(frequency=p.f_max())).build()
    assert f"{r['id']} {ex.electrode_net['W']} 0 50" in text
    assert "Vcb_W" in text and idx.ports == ["W"]
    assert g["id"].startswith("GND")


def test_mouse_wiring_on_the_canvas(editor, qtbot):
    editor.place("C", (0, -30), 0)
    editor.place("GND", (12, -30))
    editor.view.fit()
    editor.set_tool("wire")
    vp = editor.view.viewport()

    def click(pt, double=False):
        pos = editor.view.mapFromScene(pt[0] * 10.0, pt[1] * 10.0)
        qtbot.mouseMove(vp, pos)
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=pos)

    click((3, -30))
    click((12, -30))
    c = next(x for x in editor.comps if x["type"] == "C" and x["y"] == -30)
    assert editor.extraction.pin_net[(c["id"], "2")] == "0"


def test_drag_rubber_band_and_rotate(editor):
    from cirplas.gui.widgets.schematic import rubber_band

    wires = [[(0, 0), (10, 0)]]
    out = rubber_band(wires, {(10, 0): (10, 5)}, set())
    assert out[0][0] == (0, 0) and out[0][-1] == (10, 5)
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(out[0], out[0][1:]))  # still orthogonal
    r = editor.place("R", (0, -20), 0)
    editor.place("GND", (10, -20))
    editor.add_wire([(3, -20), (10, -20)])
    # move the resistor by dragging its item
    item = editor.comp_items[r["id"]]
    editor._drag = {"moved": False}
    item.setPos(item.pos().x(), item.pos().y() - 40)  # 4 grid units up
    editor.handle_release(None)
    moved = next(c for c in editor.comps if c["id"] == r["id"])
    assert (moved["x"], moved["y"]) == (0, -24)
    assert editor.extraction.pin_net[(r["id"], "2")] == "0"  # the wire followed the pin
    editor.select(r["id"])
    editor.rotate_selection()
    rot = next(c for c in editor.comps if c["id"] == r["id"])
    assert rot["rot"] == 90
    assert editor.extraction.pin_net[(r["id"], "2")] == "0"


def test_properties_rename_value_delete(editor, qapp):
    r = editor.place("R", (0, -20), 0)
    qapp.processEvents()
    assert editor.props.current == f"comp:{r['id']}:R"
    editor.update_component(r["id"], value=22.0)
    assert next(c for c in editor.comps if c["id"] == r["id"])["value"] == 22.0
    assert editor.rename_component(r["id"], "Rload")
    assert not editor.rename_component("Rload", "Cbad")  # wrong letter
    editor.select("Rload")
    editor.delete_selection()
    assert all(c["id"] != "Rload" for c in editor.comps)
    editor.select("PLASMA")
    editor.delete_selection()  # the plasma block cannot be deleted
    assert any(c["type"] == "Plasma" for c in editor.comps)


def test_rf_source_tones_and_power_target(editor):
    v = editor.place("Vrf", (-10, -30), 0)
    editor.update_component(v["id"], tones=[{"f": 2e6, "v": 500.0, "phase_deg": 0.0, "power": None}])
    editor.add_wire([(-10, -33), (-10, -40)])
    p = editor.model.project() if editor.model.is_valid else None
    assert p is not None
    assert 2e6 in p.frequencies()
