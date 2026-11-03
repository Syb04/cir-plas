"""Compare tab: overlay a radial quantity from several result files, with the ratio (or difference) to a reference
run in a subplot and the parameter differences to the reference in the legend (spec 04 sections 4.6, 5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from .. import theme
from ..results_io import ResultData
from .results import RADIAL_QUANTITIES

# the converged (or prescribed) electron density of the run is part of every result file
QUANTITIES = RADIAL_QUANTITIES + [("n", "電子密度 n", "m⁻³", 1.0, "both")]
_IGNORED_PREFIXES = ("name", "circuit.schematic.wires", "circuit.schematic.labels")


def flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    """Dotted-path view of a project dict (lists by index)."""
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list) and d and all(isinstance(v, dict) for v in d):
        for i, v in enumerate(d):
            out.update(flatten(v, f"{prefix}.{i}"))
    else:
        out[prefix] = d
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, list) and len(v) > 3:
        return f"[{len(v)} 点]"
    return str(v)


def parameter_diff(project: dict, reference: dict, limit: int = 3) -> str:
    """Short legend text listing the parameters that differ from the reference run."""
    a, b = flatten(project), flatten(reference)
    ignored = _IGNORED_PREFIXES
    if a.get("plasma.density.model") == "table" and b.get("plasma.density.model") == "table":
        ignored += ("plasma.density",)  # SC results store their converged profile: a result, not an input
    keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k) and not k.startswith(ignored))
    if not keys:
        return ""
    parts = [f"{k.split('.')[-1] if not k.split('.')[-1].isdigit() else '.'.join(k.split('.')[-2:])}={_fmt(a.get(k))}" for k in keys[:limit]]
    if len(keys) > limit:
        parts.append(f"他 {len(keys) - limit} 件")
    return ", ".join(parts)


class ComparePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: dict[str, ResultData] = {}
        lay = QHBoxLayout(self)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        btns = QHBoxLayout()
        b_add = QPushButton("結果ファイルを追加…")
        b_del = QPushButton("削除")
        b_add.clicked.connect(self._add_files)
        b_del.clicked.connect(self._remove)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self.redraw)
        self.quantity = QComboBox()
        for key, label, unit, scale, which in QUANTITIES:
            self.quantity.addItem(f"{label} [{unit}]", key)
        self.quantity.setCurrentIndex([q[0] for q in QUANTITIES].index("s_e"))
        self.quantity.currentIndexChanged.connect(self.redraw)
        self.reference = QComboBox()
        self.reference.currentIndexChanged.connect(self.redraw)
        self.sub_mode = QComboBox()
        self.sub_mode.addItem("基準に対する比 [%]", "ratio")
        self.sub_mode.addItem("基準との差", "diff")
        self.sub_mode.addItem("なし", "none")
        self.sub_mode.currentIndexChanged.connect(self.redraw)
        self.normalize = QCheckBox("中心値で正規化")
        self.normalize.toggled.connect(self.redraw)
        self.auto_legend = QCheckBox("凡例にパラメータ差分を表記")
        self.auto_legend.setChecked(True)
        self.auto_legend.toggled.connect(self.redraw)
        form = QFormLayout()
        form.addRow("量", self.quantity)
        form.addRow("基準ラン", self.reference)
        form.addRow("下段", self.sub_mode)
        ll.addLayout(btns)
        ll.addWidget(self.list, 1)
        ll.addLayout(form)
        ll.addWidget(self.normalize)
        ll.addWidget(self.auto_legend)
        left.setMaximumWidth(380)
        self.plot = theme.make_plot()
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.sub = theme.make_plot()
        self.sub.setLabel("bottom", "r [mm]")
        self.sub.showGrid(x=True, y=True, alpha=0.3)
        self.sub.setXLink(self.plot)
        plots = QSplitter(Qt.Orientation.Vertical)
        plots.addWidget(self.plot)
        plots.addWidget(self.sub)
        plots.setSizes([600, 250])
        lay.addWidget(left)
        lay.addWidget(plots, 1)

    # ------------------------------------------------------------------ run list
    def add_result(self, path: str | Path, label: str | None = None) -> None:
        path = str(path)
        if path in self.data:
            return
        self.data[path] = ResultData(path)
        item = QListWidgetItem(label or Path(path).parent.name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.list.addItem(item)
        self._refresh_reference()
        item.setSelected(True)
        self.redraw()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "結果ファイルを追加", "", "HDF5 (*.h5)")
        for p in paths:
            self.add_result(p)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.data.pop(item.data(Qt.ItemDataRole.UserRole), None)
            self.list.takeItem(self.list.row(item))
        self._refresh_reference()
        self.redraw()

    def _refresh_reference(self) -> None:
        current = self.reference.currentData()
        self.reference.blockSignals(True)
        self.reference.clear()
        for i in range(self.list.count()):
            it = self.list.item(i)
            self.reference.addItem(it.text(), it.data(Qt.ItemDataRole.UserRole))
        k = self.reference.findData(current)
        self.reference.setCurrentIndex(k if k >= 0 else 0)
        self.reference.blockSignals(False)

    # ------------------------------------------------------------------ data
    def _series(self, d: ResultData, key: str):
        if key == "n":
            n = d.plasma.get("n0")
            return None if n is None else np.asarray(n, dtype=float)
        if key.startswith("|") and d.ac is not None:
            return np.abs(d.ac["cells"][key.strip("|")])
        src = d.tran["cells"] if d.tran is not None else (d.ac["cells"] if d.ac is not None else None)
        if src is None or key not in src:
            return None
        return np.asarray(src[key], dtype=float)

    def _curve(self, d: ResultData, q) -> np.ndarray | None:
        y = self._series(d, q[0])
        if y is None:
            return None
        y = y * q[3]
        if self.normalize.isChecked() and y[0]:
            y = y / y[0]
        return y

    def redraw(self) -> None:
        self.plot.clear()
        self.sub.clear()
        key = self.quantity.currentData()
        q = next(q for q in QUANTITIES if q[0] == key)
        ref_path = self.reference.currentData()
        ref = self.data.get(ref_path) if ref_path else None
        y_ref = self._curve(ref, q) if ref is not None else None
        mode = self.sub_mode.currentData()
        for k, item in enumerate(self.list.selectedItems()):
            path = item.data(Qt.ItemDataRole.UserRole)
            d = self.data.get(path)
            if d is None:
                continue
            y = self._curve(d, q)
            if y is None:
                continue
            name = item.text()
            if self.auto_legend.isChecked() and ref is not None and path != ref_path:
                diff = parameter_diff(d.project, ref.project)
                if diff:
                    name = f"{name}: {diff}"
            elif path == ref_path:
                name = f"{name}（基準）"
            r = d.r * 1e3
            self.plot.plot(r, y, pen=theme.series_pen(k), symbol="o", symbolSize=4,
                           symbolBrush=theme.series(k), symbolPen=theme.series(k), name=name)
            if mode != "none" and y_ref is not None:
                yr = np.interp(d.r, ref.r, y_ref)  # reference on this run's radial grid
                if mode == "ratio":
                    with np.errstate(divide="ignore", invalid="ignore"):
                        dy = np.where(yr != 0, 100.0 * y / yr, np.nan)
                else:
                    dy = y - yr
                self.sub.plot(r, dy, pen=theme.series_pen(k), symbol="o", symbolSize=3, symbolBrush=theme.series(k), symbolPen=theme.series(k))
        unit = "-" if self.normalize.isChecked() else q[2]
        self.plot.setLabel("left", f"{q[1]} [{unit}]")
        self.sub.setLabel("left", {"ratio": "基準比 [%]", "diff": f"差 [{unit}]", "none": ""}[mode])
        self.sub.setVisible(mode != "none")
