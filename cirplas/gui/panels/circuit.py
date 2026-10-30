"""Circuit panel: ports, FR feed, user netlist."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...core.netlist.builder import validate_user_netlist
from ..project_model import ProjectModel
from ..widgets.paramform import BoundForm, SciSpin, group


class SourcesTable(QTableWidget):
    def __init__(self, model: ProjectModel, port: str, parent=None):
        super().__init__(0, 4, parent)
        self.model = model
        self.port = port
        self.setHorizontalHeaderLabels(["周波数 [MHz]", "振幅 [V]", "位相 [deg]", "電力指定 [W]（空欄=電圧指定）"])
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
            pw = s.get("power")
            self.setItem(row, 3, QTableWidgetItem("" if pw is None else f"{pw:.6g}"))
        self._blocked = False

    def _changed(self, row: int, col: int) -> None:
        if self._blocked:
            return
        text = self.item(row, col).text().strip()
        if col == 3:
            if text == "":
                self.model.set(f"{self.path}.{row}.power", None)
                return
        try:
            val = SciSpin.parse(text)
        except ValueError:
            return
        key = ["f", "v", "phase_deg", "power"][col]
        if key == "f":
            val *= 1e6
        self.model.set(f"{self.path}.{row}.{key}", val)

    def add(self) -> None:
        srcs = list(self.model.get(self.path, []) or [])
        srcs.append({"f": 2e6, "v": 100.0, "phase_deg": 0.0, "power": None})
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
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("外部回路の定義"))
        self.mode = QComboBox()
        self.mode.addItem("表形式（ポートごとの直列チェーン）", "ports")
        self.mode.addItem("回路図（自由配置・配線）", "schematic")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode, 1)
        lay.addLayout(mode_row)
        self.hint = QLabel("")
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
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
        f.add_choice("発振器と整合器", "circuit.ports.W.generator.type", [("none", "理想電圧源（既定）"), ("l_match", "50 Ω 発振器 + L 型整合器")],
                     "l_match: 電源振幅は発振器の開放電圧。auto なら AC 解析のポートインピーダンスから L/C を自動設計")
        f.add_float("発振器インピーダンス r_gen", "circuit.ports.W.generator.r_gen", "Ω")
        f.add_bool("自動チューニング", "circuit.ports.W.generator.auto")
        f.add_float("整合 L（手動）", "circuit.ports.W.generator.l", "nH", 1e9)
        f.add_float("整合 C（手動）", "circuit.ports.W.generator.c", "pF", 1e12)
        bl.addWidget(f)
        self.g_port = group("ポート W（ウェハ電極）", box)
        lay.addWidget(self.g_port)
        fr = BoundForm(model)
        fr.add_choice("FR への給電", "circuit.fr.type", [("esc_coupled", "ESC から容量結合（既定）"), ("separate", "独立 FR 電極（ポート F）")],
                      "esc_coupled: FR 領域の下部導体は W。結合容量は幾何（ESC・結合リング）で決まる")
        fr.add_choice("FR 調整回路（separate 時）", "circuit.fr.tuning.type",
                      [("none", "開放"), ("tie", "W と短絡"), ("cap_to_gnd", "接地へ可変 C"), ("series_lc_to_gnd", "接地へ直列 LC"),
                       ("coupling_to_w", "W へ結合 C"), ("dc_bias", "DC バイアス"), ("source", "独立電源")])
        fr.add_float("調整 C", "circuit.fr.tuning.c", "pF", 1e12)
        fr.add_float("調整 L", "circuit.fr.tuning.l", "nH", 1e9)
        fr.add_float("DC バイアス", "circuit.fr.tuning.v_dc", "V")
        self.g_fr = group("フォーカスリング", fr)
        lay.addWidget(self.g_fr)
        self.extra = QPlainTextEdit()
        self.extra.setProperty("mono", "true")
        self.extra.setPlaceholderText("* 追加ネットリスト（ノード W, F, T, 0 と u_ で始まる新規ノードが使えます）")
        self.extra.setPlainText(model.get("circuit.extra_netlist", ""))
        self.extra.textChanged.connect(self._extra_changed)
        model.changed.connect(self._refresh_extra)
        self.lint = QLabel("")
        self.lint.setProperty("role", "error")
        self.lint.setWordWrap(True)
        ebox = QWidget()
        el = QVBoxLayout(ebox)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(self.extra)
        el.addWidget(self.lint)
        lay.addWidget(group("ユーザ定義ネットリスト（上級）", ebox))
        lay.addStretch(1)

        model.changed.connect(self._refresh_mode)
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        mode = self.model.get("circuit.mode", "ports") or "ports"
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.mode.blockSignals(False)
        sch = mode == "schematic"
        self.g_port.setVisible(not sch)
        self.g_fr.setVisible(not sch)
        self.hint.setText("外部回路は中央の「回路図」タブで、部品の配置とワイヤ配線により編集します。電源の周波数・振幅もそこで設定します。"
                          if sch else "中央の「回路図」タブでは同じ回路を図で表示します。図を編集すると回路図モードに切り替わります。")

    def _mode_changed(self, _i: int) -> None:
        mode = self.mode.currentData()
        if mode == self.model.get("circuit.mode", "ports"):
            return
        import copy

        d = copy.deepcopy(self.model.data)
        circ = d["circuit"]
        conductors = {r["conductor"] for r in d["geometry"]["regions"]}
        electrodes = ["W"] + (["F"] if "F" in conductors else []) + (["T"] if d["geometry"]["top"]["conductor"] == "driven" else [])
        if mode == "schematic" and not circ.get("schematic"):
            from ...core.project import CircuitSpec
            from ...core.schematic import default_schematic, schematic_from_ports

            try:
                spec = CircuitSpec.model_validate({k: v for k, v in circ.items() if k not in ("schematic", "mode")})
                circ["schematic"] = schematic_from_ports(spec, electrodes).model_dump(mode="json")
            except Exception:  # noqa: BLE001
                circ["schematic"] = default_schematic(electrodes).model_dump(mode="json")
        if mode == "ports":
            circ["fr"]["type"] = "separate" if "F" in electrodes else "esc_coupled"
            if circ["fr"]["type"] == "esc_coupled":
                circ["ports"].pop("F", None)
            if "T" in electrodes and "T" not in circ["ports"]:
                circ["ports"]["T"] = {"sources": [{"f": 60e6, "v": 100.0, "phase_deg": 180.0, "power": None}]}
            if "T" not in electrodes:
                circ["ports"].pop("T", None)
        circ["mode"] = mode
        self.model.replace(d)

    def _extra_changed(self) -> None:
        text = self.extra.toPlainText()
        problems = validate_user_netlist(text)
        self.lint.setText("\n".join(problems))
        if not problems:
            self.model.set("circuit.extra_netlist", text)

    def _refresh_extra(self) -> None:
        """Follow edits made elsewhere (schematic editor, undo) without disturbing typing."""
        text = self.model.get("circuit.extra_netlist", "") or ""
        if text != self.extra.toPlainText() and not self.extra.hasFocus():
            self.extra.blockSignals(True)
            self.extra.setPlainText(text)
            self.extra.blockSignals(False)
            self.lint.setText("")
