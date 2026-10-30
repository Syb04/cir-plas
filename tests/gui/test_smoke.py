"""Offscreen GUI smoke tests (pytest-qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def window(qtbot):
    from cirplas.core.project import default_project
    from cirplas.gui.mainwindow import MainWindow
    from cirplas.gui.project_model import ProjectModel

    model = ProjectModel(default_project())
    win = MainWindow(model)
    qtbot.addWidget(win)
    win.show()
    return win


def test_main_window_builds(window):
    assert window.params.count() == 4
    assert window.tabs.count() == 5
    assert window.model.is_valid


def test_model_edit_updates_cross_section_and_validation(window, qtbot):
    m = window.model
    m.set("geometry.gap.value", 0.040)
    assert m.is_valid
    # invalid: outer radius smaller than the previous region
    m.set("geometry.regions.1.r_out", 0.01)
    assert not m.is_valid
    m.undo()
    assert m.is_valid and abs(m.get("geometry.regions.1.r_out") - 0.151) < 1e-9


def test_netlist_preview(window):
    window.netlist.regenerate()
    text = window.netlist.text.toPlainText()
    assert ".ac" in text and "sheath_lin" in text


def test_add_remove_region_keeps_mesh_consistent(window):
    m = window.model
    n0 = len(m.get("geometry.regions"))
    panel = window.params.widget(0).widget()
    panel._add_region()
    assert len(m.get("geometry.regions")) == n0 + 1 and m.is_valid
    panel.table.setCurrentCell(n0, 0)
    panel._remove_region()
    assert len(m.get("geometry.regions")) == n0 and m.is_valid


def test_sweep_and_compare_tabs_exist(window):
    assert window.tabs.count() == 5
    sw = window.sweep
    sw.path.setCurrentIndex(0)
    assert sw._current_path() == "circuit.ports.W.sources.0.v"
    assert float(sw.v_start.text()) < float(sw.v_stop.text())
    assert window.compare.list.count() == 0
