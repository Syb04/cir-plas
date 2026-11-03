"""Optimization tab: defaults, CLI arguments, progress display, a real worker run and applying the result."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def model():
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel

    return ProjectModel(default_project())


def test_defaults_and_arguments(qtbot, model):
    from cirplas.gui.panels.optimize import OptimizePanel

    panel = OptimizePanel(model)
    qtbot.addWidget(panel)
    # representative project: the coupling-ring thickness under the FR, with the surface kept by a z_offset tie
    assert panel.params.rowCount() == 1 and panel.params.item(0, 0).text() == "geometry.regions.2.stack.1.t"
    assert panel.ties.rowCount() == 1 and panel.ties.item(0, 0).text() == "geometry.regions.2.z_offset"
    z0 = model.get("geometry.regions.2.z_offset")
    t0 = model.get("geometry.regions.2.stack.1.t")
    assert float(panel.ties.item(0, 3).text()) == pytest.approx(z0 + t0)
    args = panel.cli_arguments()
    assert args[0] == "--param" and args[1].endswith(":log") and "--tie" in args and args[args.index("--objective") + 1] == "fr_step"


def test_progress_display(qtbot, model):
    from cirplas.gui.panels.optimize import OptimizePanel

    panel = OptimizePanel(model)
    qtbot.addWidget(panel)
    for k, (v, obj) in enumerate([(1e-3, 0.3), (2e-3, 0.1), (3e-3, float("inf"))], start=1):
        panel.handle_progress({"stage": "optimize", "evaluation": k, "objective": obj, "values": {"geometry.regions.2.stack.1.t": v},
                               "error": "bad" if obj == float("inf") else ""})
    assert panel.table.rowCount() == 3 and panel.table.item(2, 2).text().startswith("失敗")
    assert len(panel.plot.getPlotItem().listDataItems()) == 1
    panel.handle_progress({"stage": "optimize_done", "best": {"x": 2e-3}, "objective": 0.1, "evaluations": 3, "cancelled": True})
    assert "中止" in panel.status.text()


def test_worker_run_and_apply(qtbot, model, tmp_path):
    from cirplas.gui.panels.optimize import OptimizePanel

    model.save(tmp_path / "p.cirplas.json")  # runs go next to the project
    panel = OptimizePanel(model)
    qtbot.addWidget(panel)
    panel.params.setRowCount(0)
    panel.ties.setRowCount(0)
    panel.add_param("circuit.ports.W.sources.0.v", 300.0, 500.0)
    panel.objective.setCurrentIndex(panel.objective.findData("uniformity_se"))
    panel.grid.setValue(2)
    panel.max_eval.setValue(4)
    panel.run()
    assert panel.proc is not None, panel.status.text()
    qtbot.waitUntil(lambda: panel.btn_run.isEnabled(),
                    timeout=180000)
    assert panel.best_project is not None, panel.status.text()
    assert 2 <= len(panel.evaluations) <= 4
    v_best = panel.best_project["circuit"]["ports"]["W"]["sources"][0]["v"]
    panel.apply_best()
    assert model.get("circuit.ports.W.sources.0.v") == v_best
    model.undo()
    assert model.get("circuit.ports.W.sources.0.v") == 400.0
