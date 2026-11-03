"""Numerics panel: mesh, analysis mode, transient and bias settings, ngspice options."""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.estimate import format_seconds
from ...core.netlist.builder import DEFAULT_SPICE_OPTIONS
from ...core.project import NgspiceSpec
from ..estimates import EstimateRunner
from ..project_model import ProjectModel
from ..widgets.paramform import BoundForm, group


class MeshPerRegion(QWidget):
    """Cells / grading / refinement per region (rows follow the geometry)."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.form = QFormLayout(self)
        self._widgets: dict[str, tuple[QSpinBox, QDoubleSpinBox, QComboBox]] = {}
        self._blocked = False
        model.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        regions = [r["name"] for r in self.model.get("geometry.regions", [])]
        if list(self._widgets) != regions:
            while self.form.rowCount():
                self.form.removeRow(0)
            self._widgets.clear()
            for name in regions:
                n = QSpinBox()
                n.setRange(1, 500)
                g = QDoubleSpinBox()
                g.setRange(1.0, 3.0)
                g.setSingleStep(0.05)
                g.setDecimals(2)
                r = QComboBox()
                for v, t in (("none", "一様"), ("outer", "外側を細密"), ("inner", "内側を細密")):
                    r.addItem(t, v)
                n.valueChanged.connect(lambda v, nm=name: self._set("n_r", nm, int(v)))
                g.valueChanged.connect(lambda v, nm=name: self._set("grading", nm, float(v)))
                r.currentIndexChanged.connect(lambda i, nm=name, w=r: self._set("refine", nm, w.itemData(i)))
                row = QWidget()
                hl = QHBoxLayout(row)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.addWidget(n)
                hl.addWidget(g)
                hl.addWidget(r)
                self.form.addRow(f"{name}: 分割数 / 比 / 細密化", row)
                self._widgets[name] = (n, g, r)
        self._blocked = True
        n_r = self.model.get("numerics.mesh.n_r", {})
        grading = self.model.get("numerics.mesh.grading", {})
        refine = self.model.get("numerics.mesh.refine", {})
        for name, (n, g, r) in self._widgets.items():
            n.setValue(int(n_r.get(name, 4)))
            g.setValue(float(grading.get(name, 1.0)))
            default = "outer" if name == "wafer" else ("inner" if name in ("fr", "edge_gap") else "none")
            idx = r.findData(refine.get(name, default))
            r.setCurrentIndex(max(idx, 0))
        self._blocked = False

    def _set(self, key: str, name: str, value) -> None:
        if self._blocked:
            return
        d = dict(self.model.get(f"numerics.mesh.{key}", {}))
        d[name] = value
        self.model.set(f"numerics.mesh.{key}", d)


class SpiceOptionsEditor(QWidget):
    """User overrides of the ngspice .options (numerics.ngspice.options) and a reset of the ngspice settings."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self._blocked = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        defaults = QLabel("既定: " + " ".join(f"{k}={v}" for k, v in DEFAULT_SPICE_OPTIONS.items()))
        defaults.setWordWrap(True)
        defaults.setProperty("role", "muted")
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["オプション", "値"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(110)
        self.table.itemChanged.connect(self._commit)
        btns = QHBoxLayout()
        b_add = QPushButton("追加")
        b_del = QPushButton("削除")
        b_reset = QPushButton("既定値に戻す")
        b_reset.setToolTip("上書きオプションを消し、積分法・KLU・バックエンドも既定値に戻します")
        b_add.clicked.connect(self._add)
        b_del.clicked.connect(self._remove)
        b_reset.clicked.connect(self.reset)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch(1)
        btns.addWidget(b_reset)
        lay.addWidget(defaults)
        lay.addWidget(self.table)
        lay.addLayout(btns)
        model.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        opts = self.model.get("numerics.ngspice.options", {}) or {}
        self._blocked = True
        self.table.setRowCount(len(opts))
        for i, (k, v) in enumerate(opts.items()):
            self.table.setItem(i, 0, QTableWidgetItem(str(k)))
            self.table.setItem(i, 1, QTableWidgetItem(str(v)))
        self._blocked = False

    @staticmethod
    def _parse(text: str):
        for conv in (int, float):
            try:
                return conv(text)
            except ValueError:
                pass
        return text

    def _rows(self) -> dict:
        out = {}
        for i in range(self.table.rowCount()):
            k = (self.table.item(i, 0).text() if self.table.item(i, 0) else "").strip()
            v = (self.table.item(i, 1).text() if self.table.item(i, 1) else "").strip()
            if k and v:
                out[k] = self._parse(v)
        return out

    def _commit(self, *_) -> None:
        if not self._blocked:
            self.model.set("numerics.ngspice.options", self._rows())

    def _add(self) -> None:
        self._blocked = True
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem("reltol"))
        self.table.setItem(r, 1, QTableWidgetItem(""))
        self._blocked = False
        self.table.editItem(self.table.item(r, 1))

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._commit()

    def reset(self) -> None:
        spec = NgspiceSpec().model_dump(mode="json")
        # keep paths to the ngspice installation (machine-specific, not a numerical setting)
        for key in ("library_path", "executable"):
            spec[key] = self.model.get(f"numerics.ngspice.{key}")
        self.model.set("numerics.ngspice", spec)


class EstimateView(QWidget):
    """Problem size and estimated run times (calibrated from the finished runs on this machine)."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        form = QFormLayout(self)
        self.size = QLabel("-")
        self.times = QLabel("-")
        self.times.setWordWrap(True)
        self.note = QLabel("計算時間は直近のランから較正した係数による目安です")
        self.note.setProperty("role", "muted")
        self.note.setWordWrap(True)
        form.addRow("未知数", self.size)
        form.addRow("推定計算時間", self.times)
        form.addRow(self.note)
        self.runner = EstimateRunner(parent=self)
        self.runner.ready.connect(self._show)
        self.runner.failed.connect(lambda msg: self.times.setText("見積りできません: " + msg.splitlines()[0][:80]))
        model.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        if self.model.is_valid:
            self.times.setText("計算中…")
            self.runner.request(self.model.data)
        else:
            self.size.setText("-")
            self.times.setText("プロジェクトに検証エラーがあります")

    def _show(self, est) -> None:
        self.size.setText(f"{est.unknowns:,}（径方向 {est.cells} セル × バルク {est.layers} 層）")
        self.times.setText(f"AC {format_seconds(est.seconds['ac'])} / TRAN {format_seconds(est.seconds['tran'])} / "
                           f"SC {format_seconds(est.seconds['sc'])}")


class NumericsPanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addWidget(group("径方向メッシュ", MeshPerRegion(model)))
        m = BoundForm(model)
        m.add_int("バルク層数 N_z", "numerics.mesh.n_z", 1, 32, "表皮効果を解像する場合は 4 以上（層厚 ≲ δ/3）")
        m.add_float("層厚の比（シース側から）", "numerics.mesh.z_grading")
        lay.addWidget(group("軸方向", m))
        a = BoundForm(model)
        a.add_choice("解析モード", "numerics.analysis", [("ac", "AC（線形・瞬時）"), ("tran", "TRAN（非線形時間領域）"), ("sc", "SC（密度自己無撞着）")])
        a.add_int("点数/周期", "numerics.tran.pts_per_cycle", 50, 2000)
        a.add_float("ランプ周期数", "numerics.tran.ramp_cycles")
        a.add_int("最小周期数", "numerics.tran.cycles_min", 1, 10000)
        a.add_int("最大周期数", "numerics.tran.cycles_max", 1, 100000)
        a.add_int("測定窓（周期）", "numerics.tran.meas_cycles", 1, 100)
        a.add_float("周期定常の許容値", "numerics.tran.pss_tol")
        lay.addWidget(group("解析", a))
        b = BoundForm(model)
        b.add_choice("自己バイアス", "numerics.dc_bias.method", [("two_stage", "クランプ＋セカント反復"), ("none", "なし")])
        b.add_float("許容値（RF 電流比）", "numerics.dc_bias.tol")
        b.add_int("最大反復", "numerics.dc_bias.max_iter", 0, 20)
        lay.addWidget(group("自己バイアス", b))
        d = BoundForm(model)
        d.add_float("緩和係数 α（初期値）", "numerics.density_iteration.relax", "", 1.0, "振動検出で半減、単調収束で 1.3 倍（上限 0.8）に自動調整（02 §6）")
        d.add_float("許容値（相対変化の最大）", "numerics.density_iteration.tol")
        d.add_int("最大反復", "numerics.density_iteration.max_iter", 1, 200)
        d.add_bool("TRAN で確定・補正", "numerics.density_iteration.refine_with_tran")
        d.add_int("TRAN 補正回数", "numerics.density_iteration.tran_refine_iter", 0, 5, "0 なら収束後に TRAN を 1 回実行するだけ")
        lay.addWidget(group("密度反復（SC）", d))
        n = BoundForm(model)
        n.add_choice("積分法", "numerics.ngspice.method", [("gear", "Gear（2 次）"), ("trap", "台形")])
        n.add_bool("KLU ソルバ", "numerics.ngspice.use_klu")
        n.add_choice("バックエンド", "numerics.ngspice.backend", [("auto", "自動"), ("shared", "共有ライブラリ"), ("batch", "バッチ実行")])
        lay.addWidget(group("ngspice", n))
        lay.addWidget(group("ngspice オプションの上書き", SpiceOptionsEditor(model)))
        self.estimate = EstimateView(model)
        lay.addWidget(group("見積り", self.estimate))
        lay.addStretch(1)
