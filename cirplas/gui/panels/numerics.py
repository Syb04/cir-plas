"""Numerics panel: mesh, analysis mode, transient and bias settings, ngspice options."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QSpinBox, QVBoxLayout, QWidget, QComboBox, QDoubleSpinBox

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
                from PySide6.QtWidgets import QHBoxLayout

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
        a.add_choice("解析モード", "numerics.analysis", [("ac", "AC（線形・瞬時）"), ("tran", "TRAN（非線形時間領域）"), ("sc", "SC（密度自己無撞着, 未実装）")])
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
        n = BoundForm(model)
        n.add_choice("積分法", "numerics.ngspice.method", [("gear", "Gear（2 次）"), ("trap", "台形")])
        n.add_bool("KLU ソルバ", "numerics.ngspice.use_klu")
        n.add_choice("バックエンド", "numerics.ngspice.backend", [("auto", "自動"), ("shared", "共有ライブラリ"), ("batch", "バッチ実行")])
        lay.addWidget(group("ngspice", n))
        lay.addStretch(1)
