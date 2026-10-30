"""Sweep tab: define one parameter axis, run jobs in parallel, show metric-vs-parameter."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.analysis.sweep import SweepAxis, SweepSpec, get_path
from .. import theme
from ..project_model import ProjectModel
from ..runmanager import worker_command

COMMON_PATHS = [
    ("circuit.ports.W.sources.0.v", "ウェハ電極 RF 振幅 [V]"),
    ("circuit.ports.W.sources.0.f", "ウェハ電極 周波数 [Hz]"),
    ("geometry.regions.2.stack.1.t", "FR 結合リング厚さ [m]（既定プロジェクト）"),
    ("geometry.gap.value", "ギャップ [m]"),
    ("plasma.pressure_pa", "圧力 [Pa]"),
    ("plasma.density.n0", "中心密度 n0 [m^-3]"),
    ("plasma.density.alpha", "密度放物係数 a"),
    ("circuit.ports.W.c_stray", "ストレイ容量 [F]"),
]
METRICS = [
    ("ac_uniformity_se", "AC: S_e 均一性 (max-min)/2mean"),
    ("ac_center_edge_vsh", "AC: シース電圧 中心/エッジ"),
    ("ac_p_W", "AC: ポート W 電力 [W]"),
    ("tran_uniformity_se", "TRAN: S_e 均一性"),
    ("tran_uniformity_vsh_dc", "TRAN: シース DC 電圧 均一性"),
    ("tran_v_dc_W", "TRAN: 自己バイアス V_dc(W) [V]"),
    ("tran_p_source", "TRAN: 電源電力 [W]"),
    ("tran_s_b_center_mm", "TRAN: 中心シース厚 [mm]"),
    ("sc_n_center", "SC: 中心密度 [m^-3]"),
    ("sc_uniformity_n", "SC: 密度均一性"),
]


class SweepPanel(QWidget):
    open_result = Signal(str)

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.proc: QProcess | None = None
        self.outdir: Path | None = None
        self.results: dict | None = None
        lay = QVBoxLayout(self)
        form_box = QGroupBox("スイープ設定（1 軸）")
        form = QFormLayout(form_box)
        self.path = QComboBox()
        self.path.setEditable(True)
        for p, label in COMMON_PATHS:
            self.path.addItem(f"{label}  ({p})", p)
        self.path.currentIndexChanged.connect(self._fill_current)
        self.v_start = QLineEdit("")
        self.v_stop = QLineEdit("")
        self.n = QSpinBox()
        self.n.setRange(2, 200)
        self.n.setValue(5)
        self.mode = QComboBox()
        self.mode.addItem("AC", "ac")
        self.mode.addItem("TRAN", "tran")
        self.mode.addItem("SC", "sc")
        self.jobs = QSpinBox()
        self.jobs.setRange(1, 64)
        import os

        self.jobs.setValue(max(1, (os.cpu_count() or 2) - 1))
        form.addRow("パラメータ", self.path)
        form.addRow("開始値", self.v_start)
        form.addRow("終了値", self.v_stop)
        form.addRow("点数", self.n)
        form.addRow("モード", self.mode)
        form.addRow("並列数", self.jobs)
        btns = QHBoxLayout()
        self.btn_run = QPushButton("スイープ実行")
        self.btn_cancel = QPushButton("中止")
        self.btn_cancel.setEnabled(False)
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self.cancel)
        self.status = QLabel("")
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.status, 1)
        form.addRow(btns)
        lay.addWidget(form_box)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["値", "状態", "メトリクス"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(self._open_row)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.metric_combo = QComboBox()
        for k, label in METRICS:
            self.metric_combo.addItem(label, k)
        self.metric_combo.currentIndexChanged.connect(self.redraw)
        self.plot = theme.make_plot()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        rl.addWidget(self.metric_combo)
        rl.addWidget(self.plot, 1)
        split.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([450, 700])
        lay.addWidget(split, 1)
        self._fill_current()

    def _current_path(self) -> str:
        data = self.path.currentData()
        return data if data else self.path.currentText().strip()

    def _fill_current(self) -> None:
        try:
            v = get_path(self.model.data, self._current_path())
            if isinstance(v, (int, float)):
                self.v_start.setText(f"{0.5*v:.6g}")
                self.v_stop.setText(f"{1.5*v:.6g}")
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            start, stop = float(self.v_start.text()), float(self.v_stop.text())
        except ValueError:
            self.status.setText("開始値・終了値が数値ではありません")
            return
        base = self.model.project()
        base_dir = self.model.path.parent if self.model.path else Path.cwd()
        self.outdir = base_dir / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_sweep"
        self.outdir.mkdir(parents=True, exist_ok=True)
        proj_path = self.outdir / "base.cirplas.json"
        proj_path.write_text(base.to_json(), encoding="utf-8")
        spec = SweepSpec(mode=self.mode.currentData(), axes=[SweepAxis.linspace(self._current_path(), start, stop, self.n.value())])
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
        self.table.setRowCount(self.n.value())
        for i, v in enumerate(spec.axes[0].values):
            self.table.setItem(i, 0, QTableWidgetItem(f"{v:.6g}"))
            self.table.setItem(i, 1, QTableWidgetItem("待機"))
            self.table.setItem(i, 2, QTableWidgetItem(""))
        self.results = None
        self.plot.clear()
        self.proc.start()
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.status.setText(f"実行中… → {self.outdir}")

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
                self.table.setItem(i, 1, QTableWidgetItem("完了" if d.get("ok") else "失敗"))
                self.status.setText(f"{d.get('done')}/{d.get('total')} 完了")

    def _on_finished(self, code: int, status) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        res_path = self.outdir / "results.json" if self.outdir else None
        if res_path and res_path.exists():
            self.results = json.loads(res_path.read_text(encoding="utf-8"))
            for r in self.results["runs"]:
                m = r.get("metrics", {})
                txt = ", ".join(f"{k.split('_', 1)[1]}={v:.3g}" for k, v in m.items() if isinstance(v, float) and ("uniformity" in k or k.endswith("p_W") or k.endswith("p_source") or k.startswith("sc_n_center")))
                self.table.setItem(r["index"], 2, QTableWidgetItem(txt))
                self.table.item(r["index"], 0).setData(Qt.ItemDataRole.UserRole, r["output"])
            self.status.setText(f"完了（{sum(r['ok'] for r in self.results['runs'])}/{len(self.results['runs'])} 成功）")
            self.redraw()
        else:
            self.status.setText(f"失敗（終了コード {code}）")

    def redraw(self) -> None:
        self.plot.clear()
        if not self.results:
            return
        key = self.metric_combo.currentData()
        xs, ys = [], []
        for r in self.results["runs"]:
            if r.get("ok") and key in r.get("metrics", {}):
                xs.append(list(r["values"].values())[0])
                ys.append(r["metrics"][key])
        if xs:
            self.plot.plot(xs, ys, pen=theme.series_pen(1), symbol="o", symbolBrush=theme.series(1), symbolPen=theme.series(1))
            self.plot.setLabel("bottom", self.results["spec"]["axes"][0]["label"])
            self.plot.setLabel("left", self.metric_combo.currentText())

    def _open_row(self, item: QTableWidgetItem) -> None:
        path = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if path:
            self.open_result.emit(path)

    def load_results(self, results_json: str | Path) -> None:
        self.outdir = Path(results_json).parent
        self._on_finished(0, None)
