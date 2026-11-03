"""Autosave / restore, the estimate view, the ngspice options editor and the ETA of the run pane."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def _window(qtbot, tmp_path, model=None):
    from cirplas.core.project import default_project
    from cirplas.gui.mainwindow import MainWindow
    from cirplas.gui.project_model import ProjectModel

    win = MainWindow(model or ProjectModel(default_project()), restore_state=False, autosave=True, autosave_dir=tmp_path)
    qtbot.addWidget(win)
    return win


def test_autosave_and_restore(qtbot, tmp_path):
    win = _window(qtbot, tmp_path)
    saver = win.autosaver
    assert not saver.save_now()  # nothing to save before an edit
    win.model.set("geometry.gap.value", 0.041)
    assert saver.save_now() and saver.pending()["data"]["geometry"]["gap"]["value"] == 0.041
    win.close()  # unsaved changes: the snapshot stays for the next start
    assert saver.file.exists()

    win2 = _window(qtbot, tmp_path)
    assert win2.model.get("geometry.gap.value") != 0.041
    assert win2.offer_restore(ask=False)
    assert win2.model.get("geometry.gap.value") == 0.041 and win2.model.dirty
    win2.model.save(tmp_path / "p.cirplas.json")
    win2.autosaver.clear()
    assert win2.autosaver.pending() is None
    win2.close()
    assert not win2.autosaver.file.exists()


def test_spice_options_editor_and_reset(qtbot, tmp_path):
    from cirplas.gui.panels.numerics import SpiceOptionsEditor

    win = _window(qtbot, tmp_path)
    m = win.model
    ed = SpiceOptionsEditor(m)
    qtbot.addWidget(ed)
    m.set("numerics.ngspice.options", {"reltol": 3e-3})
    assert ed.table.rowCount() == 1 and ed.table.item(0, 1).text() == "0.003"
    ed.table.item(0, 1).setText("5e-4")
    assert m.get("numerics.ngspice.options") == {"reltol": 5e-4}
    m.set("numerics.ngspice.method", "trap")
    m.set("numerics.ngspice.executable", "C:/x/ngspice_con.exe")
    ed.reset()
    assert m.get("numerics.ngspice.options") == {} and m.get("numerics.ngspice.method") == "gear"
    assert m.get("numerics.ngspice.executable") == "C:/x/ngspice_con.exe"  # installation path kept
    assert m.is_valid


def test_estimate_view_and_eta(qtbot, tmp_path):
    from cirplas.gui.panels.numerics import EstimateView
    from cirplas.gui.panels.run import RunPanel

    win = _window(qtbot, tmp_path)
    view = EstimateView(win.model)
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: view.runner.last is not None, timeout=20000)
    assert view.runner.last.unknowns > 1000 and "TRAN" in view.times.text()
    # ETA: model-based, then refined from the density-iteration residuals
    rp = RunPanel(win.model)
    qtbot.addWidget(rp)
    import time

    rp._t0, rp._run_mode, rp._run_data = time.monotonic() - 5.0, "tran", win.model.data
    rp._on_estimate(view.runner.last)
    rem = rp.remaining_seconds()
    assert rem is not None and abs(rem - max(view.runner.last.seconds["tran"] - 5.0, 0.0)) < 1.0
    rp._run_mode = "sc"
    rp._density_t = [(1.0, 1e-1), (2.0, 1e-2)]  # one decade per second-long iteration, tol 1e-3 -> one more iteration
    assert abs(rp.remaining_seconds() - 1.0) < 1e-6
    rp._update_eta()
    assert "残り" in rp.eta.text()
