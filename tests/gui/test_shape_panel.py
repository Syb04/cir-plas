"""Electrode shape tab, and how a shaped electrode shows up in the cross-section, geometry panel and report."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def model():
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel

    return ProjectModel(default_project())


def _shaped(model):
    gap = dict(model.get("geometry.gap"))
    z = gap["value"]
    gap.update(type="table", table=[(0.0, z - 0.004), (0.1, z - 0.002), (0.25, z)])
    model.set("geometry.gap", gap)


def test_defaults_and_arguments(qtbot, model):
    from cirplas.gui.panels.shape import ShapePanel

    panel = ShapePanel(model)
    qtbot.addWidget(panel)
    args = panel.cli_arguments()
    assert args[args.index("--mode") + 1] == "tran" and args[args.index("--region") + 1] == "wafer"
    assert "--free-rim" not in args
    panel.free_rim.setChecked(True)
    assert "--free-rim" in panel.cli_arguments()


def test_density_objective_fixes_the_mode_to_sc(qtbot, model):
    from cirplas.gui.panels.shape import ShapePanel

    panel = ShapePanel(model)
    qtbot.addWidget(panel)
    panel.objective.setCurrentIndex(panel.objective.findData("density"))
    args = panel.cli_arguments()
    assert args[args.index("--objective") + 1] == "density" and args[args.index("--mode") + 1] == "sc"
    assert not panel.mode.isEnabled()
    assert "--tran-heating" in args and panel.tran_heating.isEnabled()
    panel.tran_heating.setChecked(False)
    assert "--no-tran-heating" in panel.cli_arguments()
    panel.objective.setCurrentIndex(panel.objective.findData("s_e"))
    assert panel.mode.isEnabled()
    panel.mode.setCurrentIndex(panel.mode.findData("tran"))
    assert not panel.tran_heating.isEnabled() and "--no-tran-heating" not in panel.cli_arguments()


def test_progress_display(qtbot, model):
    from cirplas.gui.panels.shape import ShapePanel

    panel = ShapePanel(model)
    qtbot.addWidget(panel)
    panel.handle_progress({"stage": "shape", "evaluation": 1, "rms": 0.2, "uniformity": 0.3, "mean": 1000.0, "error": ""})
    panel.handle_progress({"stage": "shape_jacobian", "done": 2, "total": 6})
    assert "2/6" in panel.status.text()
    panel.handle_progress({"stage": "shape", "evaluation": 2, "rms": float("inf"), "uniformity": float("inf"), "mean": float("nan"), "error": "bad"})
    assert panel.table.rowCount() == 2 and panel.table.item(1, 1).text().startswith("失敗")
    panel.handle_progress({"stage": "shape_done", "rms_start": 0.2, "rms": 0.05, "uniformity_start": 0.3, "uniformity": 0.08, "evaluations": 2,
                           "stop_reason": "制約内で収束", "active": ["傾斜の上限"], "cancelled": False})
    assert "傾斜の上限" in panel.status.text() and "5.00%" in panel.status.text()


def test_worker_run_and_apply(qtbot, model, tmp_path):
    from cirplas.gui.panels.shape import ShapePanel

    model.save(tmp_path / "p.cirplas.json")  # runs go next to the project
    panel = ShapePanel(model)
    qtbot.addWidget(panel)
    panel.mode.setCurrentIndex(panel.mode.findData("ac"))
    panel.max_eval.setValue(3)
    panel.run()
    assert panel.proc is not None, panel.status.text()
    qtbot.waitUntil(lambda: panel.btn_run.isEnabled(), timeout=180000)
    assert panel.best_project is not None, panel.status.text()
    assert panel.evaluations and panel.summary["best"]["rms"] <= panel.summary["start"]["rms"]
    assert panel.summary["objective"] == "s_e"
    assert len(panel.plot_se.getPlotItem().listDataItems()) == 2
    name = model.get("name")
    panel.apply_best()
    assert model.get("geometry.gap.type") == "table" and model.get("name") == name and model.is_valid
    model.undo()
    assert model.get("geometry.gap.type") == "constant"


def test_cross_section_follows_the_shape(qtbot, model):
    from cirplas.gui.widgets.crosssection import CrossSectionView

    view = CrossSectionView()
    qtbot.addWidget(view)
    view.set_project(model.project())
    flat = view.scene().itemsBoundingRect()
    _shaped(model)
    view.set_project(model.project())
    assert view.scene().itemsBoundingRect().height() == pytest.approx(flat.height(), abs=1.0)  # the rim is at the flat height
    tops = [i for i in view.scene().items() if type(i).__name__ == "QGraphicsPolygonItem"]
    assert len(tops) >= 5  # plasma above each region, top stack, conductor


def test_geometry_panel_returns_to_flat(qtbot, model):
    from cirplas.gui.panels.geometry import GeometryPanel

    panel = GeometryPanel(model)
    qtbot.addWidget(panel)
    assert not panel.btn_flat.isEnabled() and panel._shape_text() == "平坦"
    z = model.get("geometry.gap.value")
    _shaped(model)
    assert panel.btn_flat.isEnabled() and "テーブル 3 点" in panel._shape_text()
    panel.btn_flat.click()
    assert model.get("geometry.gap.type") == "constant" and model.get("geometry.gap.value") == z and model.is_valid


def test_report_lists_the_shape():
    from cirplas.core.report import input_rows

    rows = dict(input_rows({"geometry": {"regions": [], "gap": {"type": "table", "value": None, "table": [(0.0, 0.03), (0.25, 0.04)]}}}))
    assert "2 点" in rows["上部電極の形状"]
