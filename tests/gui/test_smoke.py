"""Offscreen GUI smoke tests (pytest-qt)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402


@pytest.fixture
def window(qtbot):
    from cirplas.core.project import default_project
    from cirplas.gui.mainwindow import MainWindow
    from cirplas.gui.project_model import ProjectModel

    model = ProjectModel(default_project())
    win = MainWindow(model, restore_state=False)
    qtbot.addWidget(win)
    win.show()
    return win


def test_main_window_builds(window):
    assert window.params.count() == 4
    assert window.tabs.count() == 6
    assert window.model.is_valid


def test_layout_side_docks(window):
    # settings pane on the left, run pane on the right, both spanning the full height (corners owned by the side areas)
    assert window.dockWidgetArea(window.dock_settings) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(window.dock_run) == Qt.DockWidgetArea.RightDockWidgetArea
    assert window.corner(Qt.Corner.BottomLeftCorner) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.corner(Qt.Corner.BottomRightCorner) == Qt.DockWidgetArea.RightDockWidgetArea
    assert window.dock_settings.height() > 0.8 * window.centralWidget().height()


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
    sw = window.sweep
    sw.path.setCurrentIndex(0)
    assert sw._current_path() == "circuit.ports.W.sources.0.v"
    assert float(sw.v_start.text()) < float(sw.v_stop.text())
    assert window.compare.list.count() == 0


def test_theme_modes(window, qapp):
    from PySide6.QtGui import QPalette

    from cirplas.gui import theme

    mgr = theme.manager()
    mgr.set_mode("dark", persist=False)
    mgr.apply(qapp)
    assert mgr.resolved == "dark" and mgr.tokens is theme.DARK
    assert qapp.palette().color(QPalette.ColorRole.Window).name().upper() == theme.DARK["bg"].upper()
    mgr.set_mode("light", persist=False)
    assert mgr.resolved == "light" and mgr.tokens is theme.LIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).name().upper() == theme.LIGHT["bg"].upper()
    assert theme.series(0) == theme.SERIES["light"][0]
    mgr.set_mode("system", persist=False)
