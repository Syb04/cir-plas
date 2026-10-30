"""Run panel: mode, run/cancel, progress, convergence plot, log and run history."""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..project_model import ProjectModel
from ..runmanager import RunManager


class RunPanel(QWidget):
    results_ready = Signal(str)  # path of an HDF5 file to display

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.manager = RunManager(self)
        self.manager.progress.connect(self._on_progress)
        self.manager.log.connect(self._on_log)
        self.manager.finished.connect(self._on_finished)
        self.manager.failed.connect(self._on_failed)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("AC（線形・瞬時）", "ac")
        self.mode.addItem("TRAN（非線形）", "tran")
        self.btn_run = QPushButton("実行 (F5)")
        self.btn_cancel = QPushButton("中止 (Esc)")
        self.btn_cancel.setEnabled(False)
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self.manager.cancel)
        self.status = QLabel("待機中")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        top.addWidget(QLabel("モード"))
        top.addWidget(self.mode)
        top.addWidget(self.btn_run)
        top.addWidget(self.btn_cancel)
        top.addWidget(self.status, 1)
        top.addWidget(self.progress)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.plot = pg.PlotWidget(title="収束履歴")
        self.plot.setLogMode(y=True)
        self.plot.addLegend()
        self.plot.setLabel("bottom", "反復 / 周期")
        self.curve_ac = self.plot.plot([], [], pen=pg.mkPen("#1565c0", width=2), symbol="o", name="AC 振幅変化")
        self.curve_bias = self.plot.plot([], [], pen=pg.mkPen("#ef6c00", width=2), symbol="s", name="バイアス残留 |I_dc/I_1|")
        self.curve_pss = self.plot.plot([], [], pen=pg.mkPen("#2e7d32", width=2), symbol="t", name="周期定常の変化")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self._open_history)
        split.addWidget(self.plot)
        split.addWidget(self.log)
        hist_box = QWidget()
        hl = QVBoxLayout(hist_box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(QLabel("ラン履歴（ダブルクリックで表示）"))
        hl.addWidget(self.history)
        split.addWidget(hist_box)
        split.setSizes([400, 500, 250])
        lay.addWidget(split, 1)
        self._reset_curves()

    def _reset_curves(self) -> None:
        self._ac = []
        self._bias = []
        self._pss = []
        self.curve_ac.setData([], [])
        self.curve_bias.setData([], [])
        self.curve_pss.setData([], [])

    def run(self, mode: str | None = None) -> None:
        if self.manager.running:
            return
        if not self.model.is_valid:
            self._on_log("プロジェクトに検証エラーがあります: " + self.model.error.splitlines()[0])
            return
        mode = mode or self.mode.currentData()
        self._reset_curves()
        self.log.clear()
        try:
            self.manager.start(self.model, mode)
        except Exception as exc:  # noqa: BLE001
            self._on_log(f"起動失敗: {exc}")
            return
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setVisible(True)
        self.status.setText(f"実行中 ({mode})…")

    def _on_progress(self, d: dict) -> None:
        stage = d.get("stage")
        if stage == "ac":
            self._ac.append(max(float(d.get("change", 0.0)), 1e-9))
            self.curve_ac.setData(list(range(1, len(self._ac) + 1)), self._ac)
            self.status.setText(f"AC 反復 {d.get('iteration')} 変化 {d.get('change', 0):.3g}")
        elif stage == "tran_bias":
            res = d.get("residual", {})
            if res:
                self._bias.append(max(abs(float(list(res.values())[0])), 1e-9))
                self.curve_bias.setData(list(range(len(self._bias))), self._bias)
            self.status.setText(f"自己バイアス反復 {d.get('iteration')} ({d.get('cycles')} 周期) V_dc={ {k: round(v,1) for k,v in d.get('v_dc', {}).items()} }")
        elif stage == "tran_pss":
            self._pss.append(max(float(d.get("pss_change", 0.0)), 1e-9))
            self.curve_pss.setData(list(range(1, len(self._pss) + 1)), self._pss)
            self.status.setText(f"周期定常判定 {d.get('cycles')} 周期 変化 {d.get('pss_change', 0):.3g}")

    def _on_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _on_finished(self, path: str) -> None:
        self._done(f"完了: {Path(path).parent.name}")
        item = QListWidgetItem(f"{Path(path).parent.name}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.history.insertItem(0, item)
        self.results_ready.emit(path)

    def _on_failed(self, msg: str) -> None:
        self._done("失敗: " + msg)

    def _done(self, text: str) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setVisible(False)
        self.status.setText(text)

    def _open_history(self, item: QListWidgetItem) -> None:
        self.results_ready.emit(item.data(Qt.ItemDataRole.UserRole))
