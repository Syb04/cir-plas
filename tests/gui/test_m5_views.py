"""Offscreen tests of the M5 views: 2D sweep heatmap, compare reference/ratio/legend, density history, overview warnings."""

import json
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def _fake_result(path, v=200.0, n_scale=1.0, converged=True, stages=("ac", "tran", "tran")):
    """Minimal HDF5 result with the groups the views read (AC + SC iteration)."""
    import h5py

    from cirplas.core.project import default_project

    p = default_project()
    p.circuit.ports["W"].sources[0].v = v
    r = np.linspace(0.005, 0.145, 8)
    n = n_scale * 1e16 * (1 - 0.3 * (r / 0.15) ** 2)
    with h5py.File(path, "w") as f:
        f.create_group("meta").attrs.update({"project_name": p.name, "mode": "sc"})
        f.create_dataset("project", data=p.to_json())
        m = f.create_group("mesh")
        m.create_dataset("r_centers", data=r)
        m.create_dataset("region_index", data=np.zeros(len(r), dtype=int))
        pl = f.create_group("plasma")
        pl.create_dataset("n0", data=n)
        pl.attrs.update({"te": 3.0, "nu_m": 1e7, "h_l": 0.2})
        ac = f.create_group("ac")
        ac.attrs["frequency"] = 13.56e6
        ac.create_group("diagnostics").attrs.update({"lambda_eff": 1.0})
        cells = ac.create_group("cells")
        cells.create_dataset("s_e", data=v / 200.0 * (1 + 0.1 * np.cos(r / 0.15 * np.pi)))
        cells.create_dataset("vsh_b", data=np.full(len(r), 100.0 + 0j))
        ports = ac.create_group("ports")
        ports.create_group("W").attrs.update({"v": v + 0j, "i": 1.0 + 0j, "z": 10 - 5j, "p": 10.0})
        it = f.create_group("iteration")
        hist = np.array([n * 0.5, n * 0.9, n * 0.98, n])  # initial + one profile per update
        it.create_dataset("r_centers", data=r)
        it.create_dataset("n", data=hist)
        it.create_dataset("te", data=np.full(len(stages), 3.0))
        it.create_dataset("residual", data=np.array([0.5, 0.01, 0.0005])[: len(stages)])
        it.create_dataset("relax", data=np.full(len(stages), 0.4))
        it.create_dataset("stage", data=np.array(list(stages), dtype=h5py.string_dtype()))
        it.attrs.update({"converged": converged, "iterations": 2, "d_a": 1.0, "amplitudes": "{}"})
    return str(path)


@pytest.fixture
def model():
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel

    return ProjectModel(default_project())


def test_sweep_2d_heatmap_and_click(qtbot, model, tmp_path):
    from cirplas.gui.panels.sweep import SweepPanel

    panel = SweepPanel(model)
    qtbot.addWidget(panel)
    v1, v2 = [100.0, 200.0, 300.0], [1.0, 10.0, 100.0]
    runs = []
    k = 0
    for i, a in enumerate(v1):
        for j, b in enumerate(v2):
            runs.append({"index": k, "indices": [i, j], "values": {"circuit.ports.W.sources.0.v": a, "plasma.pressure_pa": b},
                         "name": f"p{k}", "ok": True, "seconds": 1.0, "output": str(tmp_path / f"{k}.h5"),
                         "metrics": {"sc_n_center": a * b * 1e13, "ac_uniformity_se": 0.1 * i}, "error": ""})
            k += 1
    results = {"spec": {"mode": "sc", "name": "sweep", "axes": [
        {"path": "circuit.ports.W.sources.0.v", "values": v1, "label": "V"},
        {"path": "plasma.pressure_pa", "values": v2, "label": "p"}]}, "runs": runs}
    (tmp_path / "results.json").write_text(json.dumps(results), encoding="utf-8")
    panel.load_results(tmp_path / "results.json")
    assert panel.table.rowCount() == 9
    keys = [panel.metric_combo.itemData(i) for i in range(panel.metric_combo.count())]
    assert set(keys) == {"sc_n_center", "ac_uniformity_se"}
    assert panel.colorbar is not None  # heatmap drawn
    assert SweepPanel._is_log(v2) and not SweepPanel._is_log(v1)
    # clicking inside cell (i=2, j=1) opens that run (y axis in log10 for the log-spaced axis)
    xe, ye = panel._grid["xe"], panel._grid["ye"]
    i = int(np.searchsorted(xe, 300.0)) - 1
    j = int(np.searchsorted(ye, 1.0)) - 1
    assert panel._grid["outputs"][(i, j)].endswith("7.h5")
    # switching the metric redraws without error
    panel.metric_combo.setCurrentIndex(1)
    assert panel.colorbar is not None


def test_sweep_1d_points(qtbot, model, tmp_path):
    from cirplas.gui.panels.sweep import SweepPanel

    panel = SweepPanel(model)
    qtbot.addWidget(panel)
    runs = [{"index": k, "indices": [k], "values": {"plasma.pressure_pa": v}, "name": f"p{k}", "ok": k != 1, "seconds": 1.0,
             "output": f"{k}.h5", "metrics": {"sc_te": 3.0 - 0.1 * k} if k != 1 else {}, "error": "boom" if k == 1 else ""}
            for k, v in enumerate([2.0, 4.0, 6.0])]
    panel.show_results({"spec": {"mode": "sc", "name": "s", "axes": [{"path": "plasma.pressure_pa", "values": [2.0, 4.0, 6.0], "label": "p"}]},
                        "runs": runs})
    assert [p["output"] for p in panel._points] == ["0.h5", "2.h5"]
    assert panel.table.item(1, 2).text() == "失敗"


def test_compare_reference_ratio_and_legend(qtbot, tmp_path):
    from cirplas.gui.panels.compare import ComparePanel, parameter_diff

    a = _fake_result(tmp_path / "a.h5", v=200.0)
    b = _fake_result(tmp_path / "b.h5", v=300.0, n_scale=2.0)
    panel = ComparePanel()
    qtbot.addWidget(panel)
    panel.add_result(a, "a")
    panel.add_result(b, "b")
    assert panel.reference.count() == 2 and panel.reference.currentData() == a
    for i in range(panel.list.count()):
        panel.list.item(i).setSelected(True)
    panel.quantity.setCurrentIndex(panel.quantity.findData("n"))
    curves = panel.sub.getPlotItem().listDataItems()
    assert len(curves) == 2
    ratio = [np.asarray(c.yData) for c in curves]
    assert any(np.allclose(r, 200.0) for r in ratio) and any(np.allclose(r, 100.0) for r in ratio)
    assert "v=300" in parameter_diff(panel.data[b].project, panel.data[a].project)
    names = [label.text for _, label in panel.plot.getPlotItem().legend.items]
    assert any("v=300" in nm for nm in names) and any("基準" in nm for nm in names)
    panel.sub_mode.setCurrentIndex(panel.sub_mode.findData("none"))
    assert not panel.sub.isVisible()


def test_density_view_and_overview_warning(qtbot, tmp_path):
    from cirplas.gui.panels.results import OverviewTable
    from cirplas.gui.panels.results_extra import DensityView
    from cirplas.gui.results_io import ResultData

    d = ResultData(_fake_result(tmp_path / "c.h5", converged=False))
    view = DensityView()
    qtbot.addWidget(view)
    view.set_data(d)
    assert "未収束" in view.info.text()
    names = [label.text for _, label in view.p1.getPlotItem().legend.items]
    assert names == ["初期", "TRAN 補正", "最終"]
    assert any("密度反復が未収束" in w for w in OverviewTable.warnings(d))
    table = OverviewTable()
    qtbot.addWidget(table)
    table.set_data(d)
    keys = [table.item(i, 0).text() for i in range(table.rowCount())]
    assert "⚠ 警告" in keys and any(k.startswith("密度反復") for k in keys)
