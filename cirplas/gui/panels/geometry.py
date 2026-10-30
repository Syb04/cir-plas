"""Geometry panel: regions, stacks, gap, top electrode, outer boundary."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...core.materials import material_db
from ..project_model import ProjectModel
from ..widgets.paramform import BoundForm, SciSpin, group

CONDUCTOR_LABELS = {"W": "ウェハ電極 W", "F": "FR 電極 F", "G": "接地 G"}


class StackTable(QTableWidget):
    """Layer stack editor (material, thickness, radial coupling)."""

    def __init__(self, model: ProjectModel, path_getter, parent=None):
        super().__init__(0, 3, parent)
        self.model = model
        self.path_getter = path_getter  # returns the model path of the stack list (or None)
        self.setHorizontalHeaderLabels(["材料", "厚さ [mm]", "径方向結合"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(130)
        self._blocked = False
        self.cellChanged.connect(self._on_cell_changed)

    def refresh(self) -> None:
        path = self.path_getter()
        self._blocked = True
        self.setRowCount(0)
        if path is not None:
            stack = self.model.get(path, []) or []
            self.setRowCount(len(stack))
            mats = sorted(material_db())
            for row, layer in enumerate(stack):
                combo = QComboBox()
                combo.addItems(mats)
                name = layer["material"] if isinstance(layer["material"], str) else "custom"
                if name not in mats:
                    combo.addItem(name)
                combo.setCurrentText(name)
                combo.currentTextChanged.connect(lambda text, r=row: self._set_material(r, text))
                self.setCellWidget(row, 0, combo)
                self.setItem(row, 1, QTableWidgetItem(f"{layer['t']*1e3:.4g}"))
                it = QTableWidgetItem()
                it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                it.setCheckState(Qt.CheckState.Checked if layer.get("radial_coupling", True) else Qt.CheckState.Unchecked)
                self.setItem(row, 2, it)
        self._blocked = False

    def _set_material(self, row: int, text: str) -> None:
        if self._blocked:
            return
        path = self.path_getter()
        if path is not None:
            self.model.set(f"{path}.{row}.material", text)

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._blocked:
            return
        path = self.path_getter()
        if path is None:
            return
        if col == 1:
            try:
                self.model.set(f"{path}.{row}.t", SciSpin.parse(self.item(row, 1).text()) * 1e-3)
            except ValueError:
                pass
        elif col == 2:
            self.model.set(f"{path}.{row}.radial_coupling", self.item(row, 2).checkState() == Qt.CheckState.Checked)

    def add_layer(self) -> None:
        path = self.path_getter()
        if path is None:
            return
        stack = list(self.model.get(path, []) or [])
        stack.append({"material": "quartz", "t": 1.0e-3, "radial_coupling": True})
        self.model.set(path, stack)

    def remove_layer(self) -> None:
        path = self.path_getter()
        row = self.currentRow()
        if path is None or row < 0:
            return
        stack = list(self.model.get(path, []) or [])
        del stack[row]
        self.model.set(path, stack)


class GeometryPanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        lay = QVBoxLayout(self)
        # regions table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["領域", "外径 [mm]", "下部導体", "段差 z_offset [mm]"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(150)
        self.table.cellChanged.connect(self._region_cell_changed)
        self.table.currentCellChanged.connect(lambda *a: self.stack.refresh())
        btns = QHBoxLayout()
        b_add = QPushButton("領域を追加")
        b_del = QPushButton("領域を削除")
        b_add.clicked.connect(self._add_region)
        b_del.clicked.connect(self._remove_region)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch(1)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.table)
        bl.addLayout(btns)
        lay.addWidget(group("径方向領域（内側から外側へ）", box))
        # stack of the selected region
        self.stack = StackTable(model, self._selected_stack_path)
        sbtns = QHBoxLayout()
        s_add = QPushButton("層を追加")
        s_del = QPushButton("層を削除")
        s_add.clicked.connect(self.stack.add_layer)
        s_del.clicked.connect(self.stack.remove_layer)
        sbtns.addWidget(s_add)
        sbtns.addWidget(s_del)
        sbtns.addStretch(1)
        sbox = QWidget()
        sl = QVBoxLayout(sbox)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("選択中の領域の下部スタック（下から上）"))
        sl.addWidget(self.stack)
        sl.addLayout(sbtns)
        lay.addWidget(group("スタック", sbox))
        # gap / top / outer
        self.form = BoundForm(model)
        self.form.add_float("ギャップ（基準面〜上部スタック下面）", "geometry.gap.value", "mm", 1e3,
                            "基準領域（既定: 最初の領域）の表面から上部電極側表面までの距離。上部電極は平坦。")
        self.form.add_choice("上部電極", "geometry.top.conductor", [("ground", "接地"), ("driven", "駆動（ポート T）")])
        self.form.add_choice("外周境界", "geometry.outer_boundary.type", [("short", "短絡（チャンバ壁）"), ("open", "開放"), ("impedance", "インピーダンス")])
        self.form.add_float("外周境界 R", "geometry.outer_boundary.r", "Ω", 1.0)
        self.form.add_float("外周境界 C", "geometry.outer_boundary.c", "pF", 1e12)
        lay.addWidget(group("ギャップ・上部・外周", self.form))
        self.top_stack = StackTable(model, lambda: "geometry.top.stack")
        tbtns = QHBoxLayout()
        t_add = QPushButton("層を追加")
        t_del = QPushButton("層を削除")
        t_add.clicked.connect(self.top_stack.add_layer)
        t_del.clicked.connect(self.top_stack.remove_layer)
        tbtns.addWidget(t_add)
        tbtns.addWidget(t_del)
        tbtns.addStretch(1)
        tbox = QWidget()
        tl = QVBoxLayout(tbox)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.top_stack)
        tl.addLayout(tbtns)
        lay.addWidget(group("上部スタック（上から下）", tbox))
        lay.addStretch(1)
        self._blocked = False
        model.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ regions
    def _selected_stack_path(self):
        row = self.table.currentRow()
        regions = self.model.get("geometry.regions", [])
        if 0 <= row < len(regions):
            return f"geometry.regions.{row}.stack"
        return None

    def refresh(self) -> None:
        self._blocked = True
        regions = self.model.get("geometry.regions", [])
        cur = self.table.currentRow()
        self.table.setRowCount(len(regions))
        for row, reg in enumerate(regions):
            self.table.setItem(row, 0, QTableWidgetItem(reg["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(f"{reg['r_out']*1e3:.4g}"))
            combo = QComboBox()
            for k, v in CONDUCTOR_LABELS.items():
                combo.addItem(v, k)
            combo.setCurrentIndex(combo.findData(reg.get("conductor", "W")))
            combo.currentIndexChanged.connect(lambda i, r=row, c=combo: self._set_conductor(r, c.itemData(i)))
            self.table.setCellWidget(row, 2, combo)
            self.table.setItem(row, 3, QTableWidgetItem(f"{reg.get('z_offset', 0.0)*1e3:.4g}"))
        if 0 <= cur < len(regions):
            self.table.setCurrentCell(cur, 0)
        elif regions:
            self.table.setCurrentCell(0, 0)
        self._blocked = False
        self.stack.refresh()
        self.top_stack.refresh()

    def _set_conductor(self, row: int, value: str) -> None:
        if not self._blocked:
            self.model.set(f"geometry.regions.{row}.conductor", value)

    def _region_cell_changed(self, row: int, col: int) -> None:
        if self._blocked:
            return
        item = self.table.item(row, col)
        if item is None:
            return
        try:
            if col == 0:
                self.model.set(f"geometry.regions.{row}.name", item.text().strip())
            elif col == 1:
                self.model.set(f"geometry.regions.{row}.r_out", SciSpin.parse(item.text()) * 1e-3)
            elif col == 3:
                self.model.set(f"geometry.regions.{row}.z_offset", SciSpin.parse(item.text()) * 1e-3)
        except ValueError:
            pass

    def _add_region(self) -> None:
        regions = list(self.model.get("geometry.regions", []))
        r_last = regions[-1]["r_out"] if regions else 0.1
        regions.append({"name": f"region{len(regions)+1}", "r_out": r_last + 0.02, "conductor": "G", "stack": [], "z_offset": 0.0})
        data = dict(self.model.data)
        self.model.set("geometry.regions", regions)
        n_r = dict(self.model.get("numerics.mesh.n_r", {}))
        n_r[regions[-1]["name"]] = 4
        self.model.set("numerics.mesh.n_r", n_r)

    def _remove_region(self) -> None:
        row = self.table.currentRow()
        regions = list(self.model.get("geometry.regions", []))
        if row < 0 or len(regions) <= 1:
            return
        name = regions[row]["name"]
        del regions[row]
        self.model.set("geometry.regions", regions)
        n_r = dict(self.model.get("numerics.mesh.n_r", {}))
        n_r.pop(name, None)
        self.model.set("numerics.mesh.n_r", n_r)
