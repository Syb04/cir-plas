"""Schematic editor: generated elements, user elements, structural toggles (offscreen)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def editor(qtbot):
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel
    from cirplas.gui.widgets.schematic import CircuitEditor

    model = ProjectModel(default_project())
    ed = CircuitEditor(model)
    qtbot.addWidget(ed)
    ed.show()
    return ed


def test_default_elements_drawn(editor):
    ids = set(editor._items)
    assert {"port.W.sources", "port.W.l_feed", "port.W.blocking_c", "port.W.c_stray", "plasma"} <= ids
    assert "port.W.generator" not in ids


def test_user_elements_round_trip(editor):
    m = editor.model
    editor.add_user_element("R", "W", "u_1", 10.0)
    editor.add_user_element("C", "u_1", "0", 100e-12)
    assert m.get("circuit.extra_netlist").splitlines() == ["Ru1 W u_1 10", "Cu1 u_1 0 1e-10"]
    assert "user.0" in editor._items and "user.1" in editor._items
    assert m.is_valid
    editor.update_user_element(0, value=22.0, n2="u_2")
    assert m.get("circuit.extra_netlist").splitlines()[0] == "Ru1 W u_2 22"
    editor.remove_user_element(0)
    assert m.get("circuit.extra_netlist") == "Cu1 u_1 0 1e-10"
    m.undo()
    assert m.get("circuit.extra_netlist").splitlines()[0] == "Ru1 W u_2 22"
    # the generated netlist accepts the user part
    from cirplas.core.analysis.common import build_model, initial_estimates
    from cirplas.core.mesh import build_radial_mesh
    from cirplas.core.netlist.builder import AcSettings, NetlistBuilder

    p = m.project()
    n = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
    text, _ = NetlistBuilder(build_model(p, initial_estimates(p, n)), "ac", ac=AcSettings(frequency=p.f_max())).build()
    assert "Ru1 W u_2 22" in text


def test_selection_shows_properties(editor, qapp):
    assert editor.select("port.W.blocking_c")
    qapp.processEvents()
    assert "C_b" in editor.props.title.text()
    assert editor.btn_remove.isEnabled()
    editor.add_user_element("L", "W", "0", 1e-6)
    qapp.processEvents()
    assert editor.props.current_id == "user.0"
    assert "Lu1" in editor.props.title.text()


def test_structural_toggles_keep_project_valid(editor):
    m = editor.model
    editor.toggle_l_feed("W")
    assert m.get("circuit.ports.W.l_feed") == 0.0 and "port.W.l_feed" not in editor._items
    editor.toggle_l_feed("W")
    assert m.get("circuit.ports.W.l_feed") == 20e-9 and "port.W.l_feed" in editor._items
    editor.toggle_generator("W")
    assert m.get("circuit.ports.W.generator.type") == "l_match" and "port.W.generator" in editor._items and m.is_valid
    editor.set_fr_type("separate")
    assert m.is_valid and any(r["conductor"] == "F" for r in m.get("geometry.regions"))
    editor.set_fr_tuning("coupling_to_w")
    assert m.is_valid and "fr.tuning" in editor._items and m.get("circuit.fr.tuning.c") == 100e-12
    editor.set_fr_tuning("source")
    assert m.is_valid and "F" in m.get("circuit.ports") and "port.F.sources" in editor._items
    editor.set_fr_type("esc_coupled")
    assert m.is_valid and "F" not in m.get("circuit.ports") and all(r["conductor"] != "F" for r in m.get("geometry.regions"))
    editor.set_top_driven(True)
    assert m.is_valid and "T" in m.get("circuit.ports") and "port.T.sources" in editor._items
    editor.set_top_driven(False)
    assert m.is_valid and "T" not in m.get("circuit.ports")
    assert editor.available_nodes()[:2] == ["W", "0"]


def test_source_form_grows_with_added_sources(editor, qapp):
    editor.select("port.W.sources")
    qapp.processEvents()
    rows0 = editor.props._form.form.rowCount()
    editor.add_source("W")
    qapp.processEvents()
    assert editor.props.current_id == "port.W.sources"
    assert editor.props._form.form.rowCount() == rows0 + 4
    assert editor.model.is_valid and len(editor.model.get("circuit.ports.W.sources")) == 2
