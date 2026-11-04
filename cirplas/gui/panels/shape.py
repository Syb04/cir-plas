"""Electrode shape tab: top-electrode profile that flattens S_e or n (core/analysis/shape.py), run as a worker process."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import theme
from ..project_model import ProjectModel
from ..runmanager import output_base, worker_command

MODE_LABELS = [("tran", "TRAN（推奨：高調波を含む。勾配は AC から、1 ステップ数分）"), ("sc", "SC（密度自己無撞着、速い）"),
               ("ac", "AC（密度固定・高調波なし、最速の下見）")]
OBJECTIVE_LABELS = [("s_e", "電子加熱 S_e"), ("density", "電子密度 n（SC のみ）")]


def _spin(lo: float, hi: float, value: float, step: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(decimals)
    w.setSingleStep(step)
    w.setValue(value)
    w.setSuffix(suffix)
    return w


class ShapePanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.proc: QProcess | None = None
        self.outdir: Path | None = None
        self.best_project: dict | None = None
        self.summary: dict | None = None
        self.evaluations: list[dict] = []
        lay = QVBoxLayout(self)
        note = QLabel("上部スタック下面の高さ分布を探索し、対象領域の電子加熱 S_e(r) または電子密度 n(r) を平坦にします。上部スタックは厚さ一定のまま面に沿います。")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        form = QFormLayout()
        self.objective = QComboBox()
        for k, label in OBJECTIVE_LABELS:
            self.objective.addItem(label, k)
        self.mode = QComboBox()
        for k, label in MODE_LABELS:
            self.mode.addItem(label, k)
        self.tran_heating = QCheckBox("TRAN の加熱で密度を補正（推奨）")
        self.tran_heating.setChecked(True)
        self.tran_heating.setToolTip("オフでは AC の加熱だけで密度を解きます。高調波が入らず、最適形状が逆向きになることがあります")
        self.mode.currentIndexChanged.connect(lambda: self.tran_heating.setEnabled(self.mode.currentData() == "sc"))
        self.tran_heating.setEnabled(self.mode.currentData() == "sc")
        self.region = QComboBox()
        self.n_ctrl = QSpinBox()
        self.n_ctrl.setRange(3, 12)
        self.n_ctrl.setValue(6)
        self.n_ctrl.setToolTip("形状の自由度（スプラインの係数の数）。多いほど細かい補正ができるが、波打ちやすくなる")
        self.max_eval = QSpinBox()
        self.max_eval.setRange(2, 100)
        self.max_eval.setValue(12)
        self.max_eval.setToolTip("選んだモデルの評価回数の上限（勾配計算の AC は数えない）")
        self.dz = _spin(0.1, 100.0, 10.0, 1.0, 1, " mm")
        self.dz.setToolTip("現在の形状からの変位の上限（上下とも）")
        self.slope = _spin(0.01, 1.0, 0.2, 0.05, 2)
        self.slope.setToolTip("電極面の傾き |dz/dr| の上限。電極全面で保証される")
        self.min_gap = _spin(1.0, 200.0, 15.0, 1.0, 1, " mm")
        self.min_gap.setToolTip("電極面と下部表面の距離の下限")
        self.smooth = _spin(0.0, 1.0, 0.001, 0.001, 4)
        self.smooth.setToolTip("曲率の重み。大きいほど滑らかだが、均一性は下がる")
        self.free_rim = QCheckBox("外周端も動かす")
        self.free_rim.setToolTip("オフのとき、チャンバ壁の位置で電極の高さを現在の値に固定する")
        row1 = QHBoxLayout()
        for label, w in (("変位の上限", self.dz), ("傾斜の上限", self.slope), ("最小ギャップ", self.min_gap)):
            row1.addWidget(QLabel(label))
            row1.addWidget(w)
        row1.addStretch(1)
        row2 = QHBoxLayout()
        for label, w in (("自由度", self.n_ctrl), ("滑らかさ", self.smooth), ("最大評価回数", self.max_eval)):
            row2.addWidget(QLabel(label))
            row2.addWidget(w)
        row2.addWidget(self.free_rim)
        row2.addStretch(1)
        form.addRow("平坦にする分布", self.objective)
        form.addRow("評価モード", self.mode)
        form.addRow("", self.tran_heating)
        form.addRow("平坦化する領域", self.region)
        form.addRow(row1)
        form.addRow(row2)
        lay.addLayout(form)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("形状を最適化")
        self.btn_run.setProperty("primary", "true")
        self.btn_cancel = QPushButton("中止")
        self.btn_cancel.setEnabled(False)
        self.btn_apply = QPushButton("形状をプロジェクトに適用")
        self.btn_apply.setEnabled(False)
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_apply.clicked.connect(self.apply_best)
        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        for w in (self.btn_run, self.btn_cancel, self.btn_apply):
            run_row.addWidget(w)
        run_row.addWidget(self.status, 1)
        lay.addLayout(run_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "RMS", "(max-min)/2mean", "領域平均"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        plots = QSplitter(Qt.Orientation.Vertical)
        self.plot_gap = theme.make_plot()
        self.plot_gap.showGrid(x=True, y=True, alpha=0.3)
        self.plot_gap.setLabel("bottom", "r [mm]")
        self.plot_gap.setLabel("left", "電極面の変位 [mm]")
        self.plot_se = theme.make_plot()
        self.plot_se.showGrid(x=True, y=True, alpha=0.3)
        self.plot_se.setLabel("bottom", "r [mm]")
        self.plot_se.addLegend()
        plots.addWidget(self.plot_gap)
        plots.addWidget(self.plot_se)
        split.addWidget(self.table)
        split.addWidget(plots)
        split.setSizes([380, 720])
        lay.addWidget(split, 1)
        model.changed.connect(self._refresh_regions)
        self._refresh_regions()
        self.objective.currentIndexChanged.connect(self._objective_changed)
        self._objective_changed()

    def _objective_changed(self) -> None:
        """The density is an input of AC and TRAN: only the self-consistent solution can flatten it."""
        density = self.objective.currentData() == "density"
        if density:
            self.mode.setCurrentIndex(self.mode.findData("sc"))
        self.mode.setEnabled(not density)
        self.mode.setToolTip("密度は AC・TRAN では入力値なので、SC で評価します" if density else "")
        self.plot_se.setLabel("left", ("n" if density else "S_e") + " / 領域平均")

    def _refresh_regions(self) -> None:
        names = [r.get("name", "") for r in self.model.data.get("geometry", {}).get("regions", [])]
        if names != [self.region.itemText(i) for i in range(self.region.count())]:
            current = self.region.currentText()
            self.region.clear()
            self.region.addItems(names)
            if current in names:
                self.region.setCurrentText(current)

    def cli_arguments(self) -> list[str]:
        args = ["--objective", self.objective.currentData(), "--mode", self.mode.currentData(), "--n-ctrl", str(self.n_ctrl.value()), "--dz-mm", str(self.dz.value()),
                "--max-slope", str(self.slope.value()), "--min-gap-mm", str(self.min_gap.value()), "--smooth", str(self.smooth.value()),
                "--max-eval", str(self.max_eval.value()), "--region", self.region.currentText()]
        if self.mode.currentData() == "sc":
            args.append("--tran-heating" if self.tran_heating.isChecked() else "--no-tran-heating")
        return args + (["--free-rim"] if self.free_rim.isChecked() else [])

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.model.is_valid:
            self.status.setText("プロジェクトに検証エラーがあります")
            return
        try:
            self.outdir = output_base(self.model) / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_shape"
            self.outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.status.setText(f"起動失敗: {exc}")
            return
        proj = self.outdir / "base.cirplas.json"
        proj.write_text(self.model.project().to_json(), encoding="utf-8")
        self.cancel_file = self.outdir / "CANCEL"
        program, prefix = worker_command()
        args = prefix + ["shape", str(proj)] + self.cli_arguments() + ["-o", str(self.outdir / "best.cirplas.json"),
                                                                       "--history", str(self.outdir / "shape.json"),
                                                                       "--progress", "jsonl", "--cancel-file", str(self.cancel_file)]
        self.evaluations, self.best_project, self.summary = [], None, None
        self.table.setRowCount(0)
        self.plot_gap.clear()
        self.plot_se.clear()
        self._buf = ""
        self.proc = QProcess(self)
        self.proc.setProgram(program)
        self.proc.setArguments(args)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error, Qt.ConnectionType.QueuedConnection)  # may fire inside start()
        self.proc.start()
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_apply.setEnabled(False)
        self.status.setText(f"実行中… → {self.outdir}")

    def cancel(self) -> None:
        if getattr(self, "cancel_file", None) is not None:
            self.cancel_file.write_text("cancel", encoding="utf-8")
            self.status.setText("中止を要求しました（それまでの最良の形状を返します）…")

    def _on_stdout(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.handle_progress(d)

    def handle_progress(self, d: dict) -> None:
        stage = d.get("stage")
        if stage == "shape":
            self.evaluations.append(d)
            r = self.table.rowCount()
            self.table.insertRow(r)
            rms = float(d["rms"])
            ok = math.isfinite(rms)
            cells = (str(d["evaluation"]), f"{rms:.2%}" if ok else f"失敗 {d.get('error', '')[:60]}",
                     f"{float(d['uniformity']):.2%}" if ok else "", f"{float(d['mean']):.4g}" if ok else "")
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
            best = min((float(e["rms"]) for e in self.evaluations), default=math.inf)
            self.status.setText(f"評価 {d['evaluation']}  最良 RMS {best:.2%}")
        elif stage == "shape_jacobian":
            self.status.setText(f"勾配を計算中 {d['done']}/{d['total']}")
        elif stage == "shape_done":
            limits = "、".join(d.get("active", []))
            self.status.setText(("中止（それまでの最良の形状）" if d.get("cancelled") else "完了") +
                                f"：RMS {float(d['rms_start']):.2%} → {float(d['rms']):.2%}、"
                                f"(max-min)/2mean {float(d['uniformity_start']):.2%} → {float(d['uniformity']):.2%}"
                                f"（{d['evaluations']} 評価、{d['stop_reason']}" + (f"、制約に到達: {limits}" if limits else "") + "）")

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.status.setText("ワーカを起動できません: " + self.proc.errorString())

    def _on_finished(self, code: int, status) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        best = self.outdir / "best.cirplas.json" if self.outdir else None
        if code == 0 and best is not None and best.exists():
            self.best_project = json.loads(best.read_text(encoding="utf-8"))
            self.summary = json.loads((self.outdir / "shape.json").read_text(encoding="utf-8"))
            self.btn_apply.setEnabled(True)
            self.redraw()
        else:
            err = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace").strip().splitlines()
            self.status.setText(f"失敗（終了コード {code}）" + (f": {err[-1]}" if err else ""))

    def apply_best(self) -> None:
        if self.best_project is None:
            return
        self.model.set("geometry.gap", self.best_project["geometry"]["gap"])  # undoable; only the electrode changes
        self.status.setText("形状をプロジェクトに適用しました（Ctrl+Z で戻せます）")

    def redraw(self) -> None:
        self.plot_gap.clear()
        self.plot_se.clear()
        s = self.summary
        if not s:
            return
        r = np.array(s["r_shape"]) * 1e3
        dz = (np.array(s["gap_best"]) - np.array(s["gap_start"])) * 1e3
        self.plot_gap.plot(r, dz, pen=theme.series_pen(1))
        for key, k, name in (("start", 0, "開始"), ("best", 1, "最適化後")):
            p = s[key]
            m = np.array(p["target"], dtype=bool)
            self.plot_se.plot(np.array(p["r"])[m] * 1e3, np.array(p["values"])[m] / float(p["mean"]), pen=theme.series_pen(k), name=name)
