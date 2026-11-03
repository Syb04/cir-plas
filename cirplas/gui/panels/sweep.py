"""Sweep tab: one or two parameter axes (Cartesian product), parallel jobs, metric-vs-parameter (1D) or heatmap (2D)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.analysis.sweep import SweepAxis, SweepSpec, get_path
from .. import theme
from ..project_model import ProjectModel
from ..runmanager import worker_command

COMMON_PATHS = [
    ("circuit.ports.W.sources.0.v", "ウェハ電極 RF 振幅 [V]"),
    ("circuit.ports.W.sources.0.f", "ウェハ電極 周波数 [Hz]"),
    ("circuit.ports.W.sources.0.power", "ウェハ電極 電力指定 [W]"),
    ("geometry.regions.2.stack.1.t", "FR 結合リング厚さ [m]（既定プロジェクト）"),
    ("geometry.gap.value", "ギャップ [m]"),
    ("plasma.pressure_pa", "圧力 [Pa]"),
    ("plasma.density.n0", "中心密度 n0 [m^-3]"),
    ("plasma.density.alpha", "密度放物係数 a"),
    ("circuit.ports.W.c_stray", "ストレイ容量 [F]"),
]
METRIC_LABELS = {
    "ac_uniformity_se": "AC: S_e 均一性 (max-min)/2mean",
    "ac_center_edge_se": "AC: S_e 中心/エッジ",
    "ac_uniformity_vsh": "AC: シース電圧 均一性",
    "ac_center_edge_vsh": "AC: シース電圧 中心/エッジ",
    "ac_vsh_center": "AC: 中心シース電圧 [V]",
    "ac_p_W": "AC: ポート W 電力 [W]",
    "ac_lambda_eff": "AC: λ_eff [m]",
    "ac_kR_wafer": "AC: kR（ウェハ）",
    "tran_uniformity_se": "TRAN: S_e 均一性",
    "tran_center_edge_se": "TRAN: S_e 中心/エッジ",
    "tran_uniformity_vsh_dc": "TRAN: シース DC 電圧 均一性",
    "tran_v_dc_W": "TRAN: 自己バイアス V_dc(W) [V]",
    "tran_p_source": "TRAN: 電源電力 [W]",
    "tran_s_b_center_mm": "TRAN: 中心シース厚 [mm]",
    "tran_power_balance_error": "TRAN: 電力収支誤差",
    "sc_n_center": "SC: 中心密度 [m^-3]",
    "sc_n_mean": "SC: 平均密度 [m^-3]",
    "sc_uniformity_n": "SC: 密度均一性",
    "sc_center_edge_n": "SC: 密度 中心/エッジ",
    "sc_te": "SC: Te [eV]",
    "sc_iterations": "SC: 反復回数",
}


class AxisEditor(QGroupBox):
    """Parameter path, range, number of points and spacing of one sweep axis."""

    def __init__(self, title: str, model: ProjectModel, checkable: bool = False, parent=None):
        super().__init__(title, parent)
        self.model = model
        if checkable:
            self.setCheckable(True)
            self.setChecked(False)
        form = QFormLayout(self)
        self.path = QComboBox()
        self.path.setEditable(True)
        for p, label in COMMON_PATHS:
            self.path.addItem(f"{label}  ({p})", p)
        self.path.currentIndexChanged.connect(self.fill_current)
        self.v_start = QLineEdit("")
        self.v_stop = QLineEdit("")
        self.n = QSpinBox()
        self.n.setRange(2, 200)
        self.n.setValue(5)
        self.log = QCheckBox("対数間隔")
        rng = QHBoxLayout()
        rng.addWidget(self.v_start)
        rng.addWidget(QLabel("〜"))
        rng.addWidget(self.v_stop)
        pts = QHBoxLayout()
        pts.addWidget(self.n)
        pts.addWidget(self.log)
        pts.addStretch(1)
        form.addRow("パラメータ", self.path)
        form.addRow("範囲", rng)
        form.addRow("点数", pts)
        self.fill_current()

    def current_path(self) -> str:
        text = self.path.currentText().strip()
        data = self.path.currentData()
        # an edited path replaces the preset (the combo text then no longer matches the preset label)
        return data if data and text == self.path.itemText(self.path.currentIndex()) else text

    def fill_current(self) -> None:
        try:
            v = get_path(self.model.data, self.current_path())
            if isinstance(v, (int, float)):
                self.v_start.setText(f"{0.5*v:.6g}")
                self.v_stop.setText(f"{1.5*v:.6g}")
        except Exception:  # noqa: BLE001
            pass

    def axis(self) -> SweepAxis:
        start, stop = float(self.v_start.text()), float(self.v_stop.text())
        if self.log.isChecked():
            if start <= 0 or stop <= 0:
                raise ValueError("対数間隔には正の範囲が必要です")
            values = [float(v) for v in np.geomspace(start, stop, self.n.value())]
            return SweepAxis(path=self.current_path(), values=values, label=self.current_path())
        return SweepAxis.linspace(self.current_path(), start, stop, self.n.value())


class SweepPanel(QWidget):
    open_result = Signal(str)

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.proc: QProcess | None = None
        self.outdir: Path | None = None
        self.cancel_file: Path | None = None
        self.results: dict | None = None
        self._points: list[dict] = []
        lay = QVBoxLayout(self)
        axes = QHBoxLayout()
        self.axis1 = AxisEditor("軸 1", model)
        self.axis2 = AxisEditor("軸 2（2D スイープ）", model, checkable=True)
        axes.addWidget(self.axis1, 1)
        axes.addWidget(self.axis2, 1)
        lay.addLayout(axes)
        run_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("AC", "ac")
        self.mode.addItem("TRAN", "tran")
        self.mode.addItem("SC", "sc")
        self.jobs = QSpinBox()
        self.jobs.setRange(1, 64)
        self.jobs.setValue(max(1, (os.cpu_count() or 2) - 1))
        self.btn_run = QPushButton("スイープ実行")
        self.btn_run.setProperty("primary", "true")
        self.btn_cancel = QPushButton("中止")
        self.btn_cancel.setEnabled(False)
        self.btn_load = QPushButton("結果を開く…")
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_load.clicked.connect(self._load_dialog)
        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        run_row.addWidget(QLabel("モード"))
        run_row.addWidget(self.mode)
        run_row.addWidget(QLabel("並列数"))
        run_row.addWidget(self.jobs)
        run_row.addWidget(self.btn_run)
        run_row.addWidget(self.btn_cancel)
        run_row.addWidget(self.btn_load)
        run_row.addWidget(self.status, 1)
        lay.addLayout(run_row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["軸 1", "軸 2", "状態", "メトリクス"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._open_row)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.metric_combo = QComboBox()
        self.metric_combo.currentIndexChanged.connect(self.redraw)
        self._fill_metrics([])
        hint = QLabel("点（2D はセル）をクリックするとそのランを結果タブで開きます")
        hint.setProperty("role", "muted")
        self.plot = theme.make_plot()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_click)
        self.colorbar = None
        rl.addWidget(self.metric_combo)
        rl.addWidget(self.plot, 1)
        rl.addWidget(hint)
        split.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([450, 700])
        lay.addWidget(split, 1)

    # ------------------------------------------------------------------ run
    def spec(self) -> SweepSpec:
        axes = [self.axis1.axis()]
        if self.axis2.isChecked():
            axes.append(self.axis2.axis())
        return SweepSpec(mode=self.mode.currentData(), axes=axes)

    def run(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            spec = self.spec()
        except ValueError as exc:
            self.status.setText(f"軸の設定が不正です: {exc}")
            return
        base = self.model.project()
        base_dir = self.model.path.parent if self.model.path else Path.cwd()
        self.outdir = base_dir / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_sweep"
        self.outdir.mkdir(parents=True, exist_ok=True)
        proj_path = self.outdir / "base.cirplas.json"
        proj_path.write_text(base.to_json(), encoding="utf-8")
        (self.outdir / "sweep.json").write_text(spec.to_json(), encoding="utf-8")
        self.cancel_file = self.outdir / "CANCEL"
        program, prefix = worker_command()
        if prefix:
            prefix = ["-m", "cirplas.worker.sweep_cli"]
        else:
            program = str(Path(program).with_name("cirplas-sweep.exe" if sys.platform.startswith("win") else "cirplas-sweep"))
        args = prefix + [str(proj_path), "--sweep", str(self.outdir / "sweep.json"), "-o", str(self.outdir), "--jobs", str(self.jobs.value()),
                         "--progress", "jsonl", "--cancel-file", str(self.cancel_file)]
        self.proc = QProcess(self)
        self.proc.setProgram(program)
        self.proc.setArguments(args)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.finished.connect(self._on_finished)
        self._buf = ""
        self._fill_pending(spec)
        self.results = None
        self.plot.clear()
        self.proc.start()
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.status.setText(f"実行中… → {self.outdir}")

    def _fill_pending(self, spec: SweepSpec) -> None:
        a1 = spec.axes[0].values
        a2 = spec.axes[1].values if len(spec.axes) > 1 else [None]
        self.table.setRowCount(len(a1) * len(a2))
        k = 0
        for v1 in a1:
            for v2 in a2:
                self.table.setItem(k, 0, QTableWidgetItem(f"{v1:.6g}"))
                self.table.setItem(k, 1, QTableWidgetItem("" if v2 is None else f"{v2:.6g}"))
                self.table.setItem(k, 2, QTableWidgetItem("待機"))
                self.table.setItem(k, 3, QTableWidgetItem(""))
                k += 1

    def cancel(self) -> None:
        if self.cancel_file is not None:
            self.cancel_file.write_text("cancel", encoding="utf-8")
        self.status.setText("中止を要求しました…")

    def _on_stdout(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("stage") == "sweep_job":
                i = int(d["index"])
                if i < self.table.rowCount():
                    self.table.setItem(i, 2, QTableWidgetItem("完了" if d.get("ok") else "失敗"))
                self.status.setText(f"{d.get('done')}/{d.get('total')} 完了")

    def _on_finished(self, code: int, status) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        res_path = self.outdir / "results.json" if self.outdir else None
        if res_path and res_path.exists():
            self.show_results(json.loads(res_path.read_text(encoding="utf-8")))
        else:
            self.status.setText(f"失敗（終了コード {code}）")

    # ------------------------------------------------------------------ results
    def show_results(self, results: dict) -> None:
        self.results = results
        spec = SweepSpec.from_json(json.dumps(results["spec"]))
        self._fill_pending(spec)
        keys: list[str] = []
        for r in results["runs"]:
            row = r["index"]
            m = r.get("metrics", {})
            for k in m:
                if k not in keys:
                    keys.append(k)
            txt = ", ".join(f"{k.split('_', 1)[1]}={v:.3g}" for k, v in m.items() if isinstance(v, float)
                            and ("uniformity" in k or k.endswith("p_W") or k.endswith("p_source") or k == "sc_n_center"))
            self.table.setItem(row, 2, QTableWidgetItem("完了" if r.get("ok") else "失敗"))
            self.table.setItem(row, 3, QTableWidgetItem(txt if r.get("ok") else r.get("error", "")[-200:]))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, r["output"] if r.get("ok") else None)
        self._fill_metrics(keys)
        self.status.setText(f"完了（{sum(r['ok'] for r in results['runs'])}/{len(results['runs'])} 成功）")
        self.redraw()

    def _fill_metrics(self, keys: list[str]) -> None:
        current = self.metric_combo.currentData()
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        ordered = [k for k in METRIC_LABELS if k in keys] + [k for k in keys if k not in METRIC_LABELS] if keys else list(METRIC_LABELS)
        for k in ordered:
            self.metric_combo.addItem(METRIC_LABELS.get(k, k), k)
        i = self.metric_combo.findData(current)
        self.metric_combo.setCurrentIndex(i if i >= 0 else 0)
        self.metric_combo.blockSignals(False)

    def redraw(self) -> None:
        self.plot.clear()
        self._points = []
        if self.colorbar is not None:
            self.plot.getPlotItem().layout.removeItem(self.colorbar)
            if self.colorbar.scene() is not None:
                self.colorbar.scene().removeItem(self.colorbar)
            self.colorbar = None
        if not self.results:
            return
        key = self.metric_combo.currentData()
        axes = self.results["spec"]["axes"]
        runs = [r for r in self.results["runs"] if r.get("ok") and key in r.get("metrics", {})]
        if len(axes) == 1:
            pts = sorted(((r["values"][axes[0]["path"]], r["metrics"][key], r["output"]) for r in runs), key=lambda t: t[0])
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                self.plot.plot(xs, ys, pen=theme.series_pen(1), symbol="o", symbolSize=8, symbolBrush=theme.series(1), symbolPen=theme.series(1))
                self._points = [{"x": x, "y": y, "output": o} for x, y, o in pts]
            self.plot.setLabel("bottom", axes[0].get("label") or axes[0]["path"])
            self.plot.setLabel("left", self.metric_combo.currentText())
            self.plot.setLogMode(x=self._is_log(axes[0]["values"]), y=False)
            return
        self._draw_heatmap(axes, runs, key)

    @staticmethod
    def _is_log(values: list) -> bool:
        v = np.asarray(values, dtype=float)
        if len(v) < 3 or np.any(v <= 0):
            return False
        r = v[1:] / v[:-1]
        return bool(np.allclose(r, r[0], rtol=1e-6) and not np.allclose(np.diff(v), np.diff(v)[0], rtol=1e-6))

    def _draw_heatmap(self, axes: list[dict], runs: list[dict], key: str) -> None:
        """2D sweep: one cell per point, cell edges halfway between the axis values (log-aware)."""
        self.plot.setLogMode(x=False, y=False)
        v1 = np.asarray(axes[0]["values"], dtype=float)
        v2 = np.asarray(axes[1]["values"], dtype=float)
        log1, log2 = self._is_log(axes[0]["values"]), self._is_log(axes[1]["values"])
        x = np.log10(v1) if log1 else v1
        y = np.log10(v2) if log2 else v2
        img = np.full((len(v1), len(v2)), np.nan)
        outputs = {}
        for r in runs:
            i, j = r["indices"]
            img[i, j] = r["metrics"][key]
            outputs[(i, j)] = r["output"]

        def edges(c: np.ndarray) -> np.ndarray:
            if len(c) == 1:
                return np.array([c[0] - 0.5, c[0] + 0.5])
            mid = 0.5 * (c[1:] + c[:-1])
            return np.concatenate([[c[0] - (mid[0] - c[0])], mid, [c[-1] + (c[-1] - mid[-1])]])

        xe, ye = edges(x), edges(y)
        finite = img[np.isfinite(img)]
        lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        if hi <= lo:
            hi = lo + 1.0
        cmap = pg.colormap.get("viridis")
        mesh = pg.PColorMeshItem(xe[:, None].repeat(len(ye), 1), ye[None, :].repeat(len(xe), 0), img,
                                 colorMap=cmap, levels=(lo, hi), enableAutoLevels=False)
        self.plot.addItem(mesh)
        self.colorbar = pg.ColorBarItem(values=(lo, hi), colorMap=cmap, interactive=False, label=self.metric_combo.currentText())
        self.colorbar.setImageItem(mesh, insert_in=self.plot.getPlotItem())
        self._grid = {"xe": xe, "ye": ye, "outputs": outputs}
        self.plot.setLabel("bottom", ("log10 " if log1 else "") + (axes[0].get("label") or axes[0]["path"]))
        self.plot.setLabel("left", ("log10 " if log2 else "") + (axes[1].get("label") or axes[1]["path"]))
        self.plot.autoRange()

    def _on_plot_click(self, ev) -> None:
        if not self.results:
            return
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        pt = vb.mapSceneToView(ev.scenePos())
        axes = self.results["spec"]["axes"]
        path = None
        if len(axes) == 1 and self._points:
            log = self._is_log(axes[0]["values"])
            xs = np.array([p["x"] for p in self._points], dtype=float)
            xv = np.log10(xs) if log else xs
            k = int(np.argmin(np.abs(xv - pt.x())))
            span = float(np.ptp(xv)) or 1.0
            if abs(xv[k] - pt.x()) < 0.05 * span:
                path = self._points[k]["output"]
        elif len(axes) == 2 and getattr(self, "_grid", None):
            i = int(np.searchsorted(self._grid["xe"], pt.x())) - 1
            j = int(np.searchsorted(self._grid["ye"], pt.y())) - 1
            path = self._grid["outputs"].get((i, j))
        if path:
            self.open_result.emit(path)

    def _open_row(self, item: QTableWidgetItem) -> None:
        path = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if path:
            self.open_result.emit(path)

    def _load_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "スイープ結果を開く", "", "results.json (results.json);;JSON (*.json)")
        if path:
            self.load_results(path)

    def load_results(self, results_json: str | Path) -> None:
        self.outdir = Path(results_json).parent
        self.show_results(json.loads(Path(results_json).read_text(encoding="utf-8")))
