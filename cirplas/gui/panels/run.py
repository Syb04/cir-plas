"""Run pane (right side): mode, run/cancel, progress, convergence plot, log and run history, stacked vertically."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .. import theme
from ..project_model import ProjectModel
from ..runmanager import RunManager


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "section")
    return lab


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
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.mode = QComboBox()
        self.mode.addItem("AC（線形・瞬時）", "ac")
        self.mode.addItem("TRAN（非線形）", "tran")
        self.mode.addItem("SC（密度自己無撞着 + AC）", "sc")
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("モード"))
        mode_row.addWidget(self.mode, 1)
        lay.addLayout(mode_row)
        btns = QHBoxLayout()
        self.btn_run = QPushButton("実行 (F5)")
        self.btn_run.setProperty("primary", "true")
        self.btn_run.setMinimumHeight(34)
        self.btn_cancel = QPushButton("中止 (Esc)")
        self.btn_cancel.setEnabled(False)
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self.manager.cancel)
        btns.addWidget(self.btn_run, 2)
        btns.addWidget(self.btn_cancel, 1)
        lay.addLayout(btns)
        self.status = QLabel("待機中")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        lay.addWidget(self.status)
        lay.addWidget(self.progress)
        split = QSplitter(Qt.Orientation.Vertical)
        plot_box = QWidget()
        pl = QVBoxLayout(plot_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        pl.addWidget(_section("収束履歴"))
        self.plot = theme.make_plot()
        self.plot.setLogMode(y=True)
        self.plot.addLegend(offset=(-10, 10))
        self.plot.setLabel("bottom", "反復 / 周期")
        self.plot.setMinimumHeight(160)
        self.curve_ac = self.plot.plot([], [], pen=theme.series_pen(1), symbol="o", symbolSize=6, name="AC 振幅変化")
        self.curve_bias = self.plot.plot([], [], pen=theme.series_pen(0), symbol="s", symbolSize=6, name="バイアス残留 |I_dc/I_1|")
        self.curve_pss = self.plot.plot([], [], pen=theme.series_pen(2), symbol="t", symbolSize=6, name="周期定常の変化")
        pl.addWidget(self.plot, 1)
        log_box = QWidget()
        ll = QVBoxLayout(log_box)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        ll.addWidget(_section("ログ"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setProperty("mono", "true")
        self.log.setProperty("flat", "true")
        ll.addWidget(self.log, 1)
        hist_box = QWidget()
        hl = QVBoxLayout(hist_box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        hl.addWidget(_section("ラン履歴（ダブルクリックで表示）"))
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(self._open_history)
        hl.addWidget(self.history, 1)
        split.addWidget(plot_box)
        split.addWidget(log_box)
        split.addWidget(hist_box)
        split.setSizes([280, 320, 160])
        lay.addWidget(split, 1)
        self._reset_curves()

    def restyle(self) -> None:
        """Re-colour the convergence curves after a theme change."""
        theme.restyle_plot(self.plot)
        for k, curve in ((1, self.curve_ac), (0, self.curve_bias), (2, self.curve_pss)):
            curve.setPen(theme.series_pen(k))
            curve.setSymbolBrush(theme.series(k))
            curve.setSymbolPen(theme.series(k))

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
