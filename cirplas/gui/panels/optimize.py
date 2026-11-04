"""Optimization tab: automatic FR tuning / uniformity search (core/analysis/optimize.py) run as a worker process."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.analysis.sweep import get_path
from .. import theme
from ..project_model import ProjectModel
from ..runmanager import output_base, worker_command

OBJECTIVE_LABELS = [("fr_step", "FR 段差：ウェハ端と FR のシース電圧の相対差"), ("uniformity_se", "S_e 均一性（ウェハ）"),
                    ("uniformity_vsh", "シース電圧 均一性（ウェハ）")]
_STACK_T = re.compile(r"geometry\.regions\.(\d+)\.stack\.(\d+)\.t")


class OptimizePanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.proc: QProcess | None = None
        self.outdir: Path | None = None
        self.best_project: dict | None = None
        self.evaluations: list[dict] = []
        lay = QVBoxLayout(self)

        pbox = QGroupBox("変数（プロジェクトのパス・範囲）")
        pl = QVBoxLayout(pbox)
        self.params = QTableWidget(0, 4)
        self.params.setHorizontalHeaderLabels(["パス", "下限", "上限", "対数"])
        self.params.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.params.setMaximumHeight(130)
        self.ties = QTableWidget(0, 4)
        self.ties.setHorizontalHeaderLabels(["連動するパス", "変数番号", "係数", "オフセット"])
        self.ties.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ties.setMaximumHeight(100)
        prow = QHBoxLayout()
        for text, slot in (("変数を追加", self.add_param), ("削除", lambda: self._remove(self.params)),
                           ("厚さに z_offset を連動", self.tie_surface), ("連動を削除", lambda: self._remove(self.ties))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            prow.addWidget(b)
        prow.addStretch(1)
        pl.addWidget(self.params)
        pl.addWidget(QLabel("連動（値 = 係数 × 変数 + オフセット）：スタック厚を変えるとき表面高さを保つ等"))
        pl.addWidget(self.ties)
        pl.addLayout(prow)
        lay.addWidget(pbox)

        form = QFormLayout()
        self.objective = QComboBox()
        for k, label in OBJECTIVE_LABELS:
            self.objective.addItem(label, k)
        self.mode = QComboBox()
        for k, label in (("ac", "AC（速い）"), ("tran", "TRAN"), ("sc", "SC")):
            self.mode.addItem(label, k)
        self.grid = QSpinBox()
        self.grid.setRange(2, 15)
        self.grid.setValue(5)
        self.max_eval = QSpinBox()
        self.max_eval.setRange(3, 500)
        self.max_eval.setValue(40)
        row = QHBoxLayout()
        row.addWidget(QLabel("初期走査（点/変数）"))
        row.addWidget(self.grid)
        row.addWidget(QLabel("最大評価回数"))
        row.addWidget(self.max_eval)
        row.addStretch(1)
        form.addRow("目的関数", self.objective)
        form.addRow("評価モード", self.mode)
        form.addRow(row)
        lay.addLayout(form)

        run_row = QHBoxLayout()
        self.btn_run = QPushButton("最適化を実行")
        self.btn_run.setProperty("primary", "true")
        self.btn_cancel = QPushButton("中止")
        self.btn_cancel.setEnabled(False)
        self.btn_apply = QPushButton("最適値をプロジェクトに適用")
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
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "変数の値", "目的関数"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.log_y = QCheckBox("縦軸を対数")
        self.log_y.setChecked(True)
        self.log_y.toggled.connect(self.redraw)
        self.plot = theme.make_plot()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        rl.addWidget(self.log_y)
        rl.addWidget(self.plot, 1)
        split.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([450, 650])
        lay.addWidget(split, 1)
        self.suggest()

    # ------------------------------------------------------------------ parameter tables
    def _row_values(self, table: QTableWidget, r: int) -> list[str]:
        out = []
        for c in range(table.columnCount()):
            it = table.item(r, c)
            if it is None:
                out.append("")
            elif it.data(Qt.ItemDataRole.CheckStateRole) is not None:  # checkbox cell (plain cells have no check state)
                out.append("1" if it.checkState() == Qt.CheckState.Checked else "")
            else:
                out.append(it.text().strip())
        return out

    def add_param(self, path: str = "", lo: float | None = None, hi: float | None = None, log: bool = False) -> None:
        if not isinstance(path, str):
            path = ""
        if path and (lo is None or hi is None):
            try:
                v = float(get_path(self.model.data, path))
                lo, hi = (0.3 * v, 3.0 * v) if log else (0.5 * v, 1.5 * v)
            except Exception:  # noqa: BLE001
                lo, hi = 0.0, 1.0
        r = self.params.rowCount()
        self.params.insertRow(r)
        self.params.setItem(r, 0, QTableWidgetItem(path))
        self.params.setItem(r, 1, QTableWidgetItem("" if lo is None else f"{lo:.6g}"))
        self.params.setItem(r, 2, QTableWidgetItem("" if hi is None else f"{hi:.6g}"))
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        chk.setCheckState(Qt.CheckState.Checked if log else Qt.CheckState.Unchecked)
        self.params.setItem(r, 3, chk)

    def add_tie(self, path: str, of: int, scale: float, offset: float) -> None:
        r = self.ties.rowCount()
        self.ties.insertRow(r)
        for c, v in enumerate((path, str(of), f"{scale:.6g}", f"{offset:.9g}")):
            self.ties.setItem(r, c, QTableWidgetItem(v))

    @staticmethod
    def _remove(table: QTableWidget) -> None:
        for r in sorted({i.row() for i in table.selectedItems()} or ({table.rowCount() - 1} if table.rowCount() else set()), reverse=True):
            table.removeRow(r)

    def tie_surface(self) -> None:
        """For a stack-thickness variable: move the region's z_offset so that its top surface stays where it is."""
        rows = sorted({i.row() for i in self.params.selectedItems()}) or list(range(self.params.rowCount()))
        for r in rows:
            m = _STACK_T.fullmatch(self._row_values(self.params, r)[0])
            if not m:
                continue
            reg = int(m.group(1))
            t0 = float(get_path(self.model.data, m.group(0)))
            z0 = float(get_path(self.model.data, f"geometry.regions.{reg}.z_offset") or 0.0)
            # surface = z_offset + sum(t): keep it fixed -> z_offset = (z0 + t0) - t
            self.add_tie(f"geometry.regions.{reg}.z_offset", r, -1.0, z0 + t0)
            return
        self.status.setText("スタック厚（geometry.regions.N.stack.K.t）の変数を選んでください")

    def suggest(self) -> None:
        """Default variable: the separate FR source amplitude, else the coupling-ring thickness under the FR."""
        d = self.model.data
        if "F" in d.get("circuit", {}).get("ports", {}) and d["circuit"]["ports"]["F"].get("sources"):
            self.add_param("circuit.ports.F.sources.0.v")
            return
        for i, reg in enumerate(d.get("geometry", {}).get("regions", [])):
            if reg.get("name") == "fr" and len(reg.get("stack", [])) >= 2:
                self.add_param(f"geometry.regions.{i}.stack.1.t", log=True)
                self.params.selectRow(0)
                self.tie_surface()
                self.params.clearSelection()
                return

    def cli_arguments(self) -> list[str]:
        args = []
        for r in range(self.params.rowCount()):
            path, lo, hi, log = self._row_values(self.params, r)
            if not path:
                continue
            float(lo), float(hi)  # validate
            args += ["--param", f"{path}:{lo}:{hi}" + (":log" if log else "")]
        if not args:
            raise ValueError("変数がありません")
        for r in range(self.ties.rowCount()):
            path, of, scale, offset = self._row_values(self.ties, r)
            if path:
                args += ["--tie", f"{path}:{int(of)}:{float(scale)}:{float(offset)}"]
        return args + ["--objective", self.objective.currentData(), "--mode", self.mode.currentData(),
                       "--grid", str(self.grid.value()), "--max-eval", str(self.max_eval.value())]

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        if not self.model.is_valid:
            self.status.setText("プロジェクトに検証エラーがあります")
            return
        try:
            opt_args = self.cli_arguments()
        except ValueError as exc:
            self.status.setText(f"変数の設定が不正です: {exc}")
            return
        try:
            self.outdir = output_base(self.model) / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_optimize"
            self.outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.status.setText(f"起動失敗: {exc}")
            return
        proj = self.outdir / "base.cirplas.json"
        proj.write_text(self.model.project().to_json(), encoding="utf-8")
        self.cancel_file = self.outdir / "CANCEL"
        program, prefix = worker_command()
        args = prefix + ["optimize", str(proj)] + opt_args + ["-o", str(self.outdir / "best.cirplas.json"),
                                                              "--history", str(self.outdir / "history.json"),
                                                              "--progress", "jsonl", "--cancel-file", str(self.cancel_file)]
        self.evaluations, self.best_project = [], None
        self.table.setRowCount(0)
        self.plot.clear()
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
            self.status.setText("中止を要求しました（評価中の点が終わり次第、それまでの最良点を返します）…")

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
        if d.get("stage") == "optimize":
            self.evaluations.append(d)
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = ", ".join(f"{k.split('.')[-1] if not k.split('.')[-1].isdigit() else k}={v:.4g}" for k, v in d["values"].items())
            obj = float(d["objective"])
            for c, text in enumerate((str(d["evaluation"]), vals, f"{obj:.4g}" if math.isfinite(obj) else f"失敗 {d.get('error', '')[:60]}")):
                self.table.setItem(r, c, QTableWidgetItem(text))
            best = min((float(e["objective"]) for e in self.evaluations), default=float("inf"))
            self.status.setText(f"評価 {d['evaluation']}  最良 {best:.4g}")
            self.redraw()
        elif d.get("stage") == "optimize_done":
            self.status.setText(("中止（それまでの最良点）" if d.get("cancelled") else "完了") +
                                f"：{d['evaluations']} 評価、目的関数 {float(d['objective']):.4g}、" +
                                ", ".join(f"{k}={v:.6g}" for k, v in d["best"].items()))

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
            self.btn_apply.setEnabled(True)
        elif not self.evaluations:
            err = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace").strip().splitlines()
            self.status.setText(f"失敗（終了コード {code}）" + (f": {err[-1]}" if err else ""))

    def apply_best(self) -> None:
        if self.best_project is None:
            return
        name = self.model.data.get("name")
        data = dict(self.best_project)
        if name is not None:
            data["name"] = name
        self.model.replace(data)  # undoable
        self.status.setText("最適値をプロジェクトに適用しました（Ctrl+Z で戻せます）")

    def redraw(self) -> None:
        self.plot.clear()
        if not self.evaluations:
            return
        obj = np.array([float(e["objective"]) for e in self.evaluations])
        ok = np.isfinite(obj) & (obj > 0 if self.log_y.isChecked() else np.isfinite(obj))
        paths = list(self.evaluations[0]["values"])
        if len(paths) == 1:
            x = np.array([float(e["values"][paths[0]]) for e in self.evaluations])
            order = np.argsort(x[ok])
            self.plot.plot(x[ok][order], obj[ok][order], pen=theme.series_pen(1, 1.0), symbol="o", symbolSize=7,
                           symbolBrush=theme.series(1), symbolPen=theme.series(1))
            self.plot.setLabel("bottom", paths[0])
        else:
            n = np.arange(1, len(obj) + 1)
            self.plot.plot(n[ok], obj[ok], pen=None, symbol="o", symbolSize=7, symbolBrush=theme.series(1), symbolPen=theme.series(1))
            run_best = np.minimum.accumulate(np.where(np.isfinite(obj), obj, np.inf))
            self.plot.plot(n[np.isfinite(run_best)], run_best[np.isfinite(run_best)], pen=theme.series_pen(0))
            self.plot.setLabel("bottom", "評価番号（線：それまでの最良）")
        self.plot.setLogMode(x=False, y=self.log_y.isChecked())
        self.plot.setLabel("left", self.objective.currentText().split("：")[0])
