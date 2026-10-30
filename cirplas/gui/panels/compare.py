"""Compare tab: overlay a radial quantity from several result files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from ..results_io import ResultData
from .results import COLORS, RADIAL_QUANTITIES


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
        for key, label, unit, scale, which in RADIAL_QUANTITIES:
            self.quantity.addItem(f"{label} [{unit}]", key)
        self.quantity.setCurrentIndex([q[0] for q in RADIAL_QUANTITIES].index("s_e"))
        self.quantity.currentIndexChanged.connect(self.redraw)
        self.normalize = QCheckBox("中心値で正規化")
        self.normalize.toggled.connect(self.redraw)
        ll.addLayout(btns)
        ll.addWidget(self.list, 1)
        ll.addWidget(self.quantity)
        ll.addWidget(self.normalize)
        left.setMaximumWidth(360)
        self.plot = pg.PlotWidget()
        self.plot.addLegend()
        self.plot.setLabel("bottom", "r [mm]")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        lay.addWidget(left)
        lay.addWidget(self.plot, 1)

    def add_result(self, path: str | Path, label: str | None = None) -> None:
        path = str(path)
        if path in self.data:
            return
        self.data[path] = ResultData(path)
        item = QListWidgetItem(label or Path(path).parent.name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setSelected(True)
        self.list.addItem(item)
        self.redraw()

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "結果ファイルを追加", "", "HDF5 (*.h5)")
        for p in paths:
            self.add_result(p)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.data.pop(item.data(Qt.ItemDataRole.UserRole), None)
            self.list.takeItem(self.list.row(item))
        self.redraw()

    def _series(self, d: ResultData, key: str):
        if key.startswith("|") and d.ac is not None:
            return np.abs(d.ac["cells"][key.strip("|")])
        src = d.tran["cells"] if d.tran is not None else (d.ac["cells"] if d.ac is not None else None)
        if src is None or key not in src:
            return None
        return np.asarray(src[key], dtype=float)

    def redraw(self) -> None:
        self.plot.clear()
        key = self.quantity.currentData()
        q = next(q for q in RADIAL_QUANTITIES if q[0] == key)
        for k, item in enumerate(self.list.selectedItems()):
            d = self.data.get(item.data(Qt.ItemDataRole.UserRole))
            if d is None:
                continue
            y = self._series(d, key)
            if y is None:
                continue
            y = y * q[3]
            if self.normalize.isChecked() and y[0]:
                y = y / y[0]
            self.plot.plot(d.r * 1e3, y, pen=pg.mkPen(COLORS[k % len(COLORS)], width=2), symbol="o", symbolSize=4,
                           symbolBrush=COLORS[k % len(COLORS)], name=item.text())
        self.plot.setLabel("left", f"{q[1]} [{q[2]}]")
