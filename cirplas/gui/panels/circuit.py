"""Circuit panel: ports, FR feed, user netlist."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...core.netlist.builder import validate_user_netlist
from ..project_model import ProjectModel
from ..widgets.paramform import BoundForm, SciSpin, group


class SourcesTable(QTableWidget):
    def __init__(self, model: ProjectModel, port: str, parent=None):
        super().__init__(0, 3, parent)
        self.model = model
        self.port = port
        self.setHorizontalHeaderLabels(["周波数 [MHz]", "振幅 [V]", "位相 [deg]"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(110)
        self._blocked = False
        self.cellChanged.connect(self._changed)
        model.changed.connect(self.refresh)
        self.refresh()

    @property
    def path(self) -> str:
        return f"circuit.ports.{self.port}.sources"

    def refresh(self) -> None:
        self._blocked = True
        srcs = self.model.get(self.path, []) or []
        self.setRowCount(len(srcs))
        for row, s in enumerate(srcs):
            self.setItem(row, 0, QTableWidgetItem(f"{s['f']/1e6:.6g}"))
            self.setItem(row, 1, QTableWidgetItem(f"{s['v']:.6g}"))
            self.setItem(row, 2, QTableWidgetItem(f"{s.get('phase_deg', 0.0):.6g}"))
        self._blocked = False

    def _changed(self, row: int, col: int) -> None:
        if self._blocked:
            return
        try:
            val = SciSpin.parse(self.item(row, col).text())
        except ValueError:
            return
        key = ["f", "v", "phase_deg"][col]
        if key == "f":
            val *= 1e6
        self.model.set(f"{self.path}.{row}.{key}", val)

    def add(self) -> None:
        srcs = list(self.model.get(self.path, []) or [])
        srcs.append({"f": 2e6, "v": 100.0, "phase_deg": 0.0})
        self.model.set(self.path, srcs)

    def remove(self) -> None:
        row = self.currentRow()
        srcs = list(self.model.get(self.path, []) or [])
        if 0 <= row < len(srcs) and len(srcs) > 1:
            del srcs[row]
            self.model.set(self.path, srcs)


class CircuitPanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        lay = QVBoxLayout(self)
        self.sources = SourcesTable(model, "W")
        btns = QHBoxLayout()
        b_add = QPushButton("電源を追加")
        b_del = QPushButton("削除")
        b_add.clicked.connect(self.sources.add)
        b_del.clicked.connect(self.sources.remove)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch(1)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.sources)
        bl.addLayout(btns)
        f = BoundForm(model)
        f.add_float("ブロッキング容量 C_b", "circuit.ports.W.blocking_c", "nF", 1e9, "空欄不可。無しにする場合は非常に大きな値を入れてください")
        f.add_float("給電インダクタンス L_feed", "circuit.ports.W.l_feed", "nH", 1e9,
                    "理想電圧源と直列。共振により電極電圧が大きく増幅されることがあるため既定は 0")
        f.add_float("ストレイ容量 C_stray", "circuit.ports.W.c_stray", "pF", 1e12)
        bl.addWidget(f)
        lay.addWidget(group("ポート W（ウェハ電極）", box))
        fr = BoundForm(model)
        fr.add_choice("FR への給電", "circuit.fr.type", [("esc_coupled", "ESC から容量結合（既定）"), ("separate", "独立 FR 電極（ポート F）")],
                      "esc_coupled: FR 領域の下部導体は W。結合容量は幾何（ESC・結合リング）で決まる")
        fr.add_choice("FR 調整回路（separate 時）", "circuit.fr.tuning.type",
                      [("none", "開放"), ("tie", "W と短絡"), ("cap_to_gnd", "接地へ可変 C"), ("series_lc_to_gnd", "接地へ直列 LC"),
                       ("coupling_to_w", "W へ結合 C"), ("dc_bias", "DC バイアス"), ("source", "独立電源")])
        fr.add_float("調整 C", "circuit.fr.tuning.c", "pF", 1e12)
        fr.add_float("調整 L", "circuit.fr.tuning.l", "nH", 1e9)
        fr.add_float("DC バイアス", "circuit.fr.tuning.v_dc", "V")
        lay.addWidget(group("フォーカスリング", fr))
        self.extra = QPlainTextEdit()
        self.extra.setPlaceholderText("* 追加ネットリスト（ノード W, F, T, 0 と u_ で始まる新規ノードが使えます）")
        self.extra.setPlainText(model.get("circuit.extra_netlist", ""))
        self.extra.textChanged.connect(self._extra_changed)
        self.lint = QLabel("")
        self.lint.setStyleSheet("color: #c62828;")
        ebox = QWidget()
        el = QVBoxLayout(ebox)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(self.extra)
        el.addWidget(self.lint)
        lay.addWidget(group("ユーザ定義ネットリスト（上級）", ebox))
        lay.addStretch(1)

    def _extra_changed(self) -> None:
        text = self.extra.toPlainText()
        problems = validate_user_netlist(text)
        self.lint.setText("\n".join(problems))
        if not problems:
            self.model.set("circuit.extra_netlist", text)
