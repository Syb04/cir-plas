"""Results views: overview table, radial profiles, waveforms, edge zoom."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..results_io import ResultData
from .results_extra import DensityView, EdgeView, MapView, SpectrumView

RADIAL_QUANTITIES = [
    # key, label, unit, scale, which (tran/ac/both)
    ("vsh_b_dc", "下部シース DC 電圧", "V", 1.0, "tran"),
    ("vsh_b_1", "下部シース RF 振幅", "V", 1.0, "tran"),
    ("vsh_b_min", "下部シース最小電圧", "V", 1.0, "tran"),
    ("vsh_t_dc", "上部シース DC 電圧", "V", 1.0, "tran"),
    ("vsh_t_1", "上部シース RF 振幅", "V", 1.0, "tran"),
    ("s_e", "電子加熱面密度 S_e", "W/m²", 1.0, "both"),
    ("p_ohm_bulk", "オーミック加熱（セル）", "W", 1.0, "both"),
    ("p_stoc_b", "確率的加熱 下部（セル）", "W", 1.0, "both"),
    ("p_stoc_t", "確率的加熱 上部（セル）", "W", 1.0, "both"),
    ("s_b_mean", "下部シース厚（平均）", "mm", 1e3, "tran"),
    ("s_b_max", "下部シース厚（最大）", "mm", 1e3, "tran"),
    ("j_z_1", "軸方向電流密度 基本波", "A/m²", 1.0, "tran"),
    ("|vsh_b|", "下部シース電圧 |V|（AC）", "V", 1.0, "ac"),
    ("|vsh_t|", "上部シース電圧 |V|（AC）", "V", 1.0, "ac"),
    ("|j_z|", "軸方向電流密度 |J|（AC）", "A/m²", 1.0, "ac"),
]

COLORS = ["#1565c0", "#ef6c00", "#2e7d32", "#6a1b9a", "#c62828", "#00838f", "#4e342e", "#37474f", "#827717", "#ad1457"]


class RadialPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ResultData | None = None
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        for key, label, unit, scale, which in RADIAL_QUANTITIES:
            cb = QCheckBox(f"{label} [{unit}]")
            cb.setProperty("key", key)
            cb.toggled.connect(self.redraw)
            from PySide6.QtWidgets import QListWidgetItem

            item = QListWidgetItem()
            self.list.addItem(item)
            self.list.setItemWidget(item, cb)
        self.plot = pg.PlotWidget()
        self.plot.addLegend()
        self.plot.setLabel("bottom", "r [mm]")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.normalize = QCheckBox("中心値で正規化")
        self.normalize.toggled.connect(self.redraw)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.normalize)
        rl.addWidget(self.plot, 1)
        lay.addWidget(self.list)
        lay.addWidget(right, 1)
        self._region_lines = []
        # default selections
        self._set_checked({"vsh_b_dc", "vsh_b_1", "s_e"})

    def _set_checked(self, keys: set[str]) -> None:
        for i in range(self.list.count()):
            cb = self.list.itemWidget(self.list.item(i))
            cb.blockSignals(True)
            cb.setChecked(cb.property("key") in keys)
            cb.blockSignals(False)

    def set_data(self, data: ResultData) -> None:
        self.data = data
        has_tran = data.tran is not None
        for i in range(self.list.count()):
            cb = self.list.itemWidget(self.list.item(i))
            key = cb.property("key")
            which = next(q[4] for q in RADIAL_QUANTITIES if q[0] == key)
            ok = (which == "both") or (which == "tran" and has_tran) or (which == "ac" and data.ac is not None)
            cb.setEnabled(ok)
        if not has_tran:
            self._set_checked({"|vsh_b|", "s_e"})
        self.redraw()

    def _series(self, key: str):
        d = self.data
        if key.startswith("|") and d.ac is not None:
            k = key.strip("|")
            return np.abs(d.ac["cells"][k])
        src = d.tran["cells"] if d.tran is not None else (d.ac["cells"] if d.ac is not None else None)
        if src is None or key not in src:
            return None
        return np.asarray(src[key], dtype=float)

    def redraw(self) -> None:
        self.plot.clear()
        if self.data is None:
            return
        r = self.data.r * 1e3
        k = 0
        for i in range(self.list.count()):
            cb = self.list.itemWidget(self.list.item(i))
            if not cb.isChecked() or not cb.isEnabled():
                continue
            key = cb.property("key")
            q = next(q for q in RADIAL_QUANTITIES if q[0] == key)
            y = self._series(key)
            if y is None:
                continue
            y = y * q[3]
            if self.normalize.isChecked() and abs(y[0]) > 0:
                y = y / y[0]
            self.plot.plot(r, y, pen=pg.mkPen(COLORS[k % len(COLORS)], width=2), symbol="o", symbolSize=4,
                           symbolBrush=COLORS[k % len(COLORS)], name=f"{q[1]} [{q[2]}]")
            k += 1
        # region boundaries
        edges = self.data.mesh.get("r_edges")
        idx = self.data.mesh.get("region_index")
        if edges is not None and idx is not None:
            for i in range(1, len(idx)):
                if idx[i] != idx[i - 1]:
                    self.plot.addItem(pg.InfiniteLine(pos=edges[i] * 1e3, angle=90, pen=pg.mkPen("#9e9e9e", style=Qt.PenStyle.DashLine)))


class WaveformPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ResultData | None = None
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setMaximumWidth(260)
        self.list.itemSelectionChanged.connect(self.redraw)
        self.plot = pg.PlotWidget()
        self.plot.addLegend()
        self.plot.setLabel("bottom", "t [ns]")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        lay.addWidget(self.list)
        lay.addWidget(self.plot, 1)

    def set_data(self, data: ResultData) -> None:
        self.data = data
        self.list.clear()
        if data.window is None:
            self.list.addItem("（波形は保存されていません）")
            return
        names = [n for n in data.window["names"] if not n.startswith("a_") and "ea_" not in n and "#branch" not in n]
        names = sorted(names, key=lambda s: (not s.startswith("sb_"), s))
        self.list.addItems(names)
        branches = sorted(n for n in data.window["names"] if "#branch" in n and ("vsc" in n or "vcb" in n or "lbz" in n))
        self.list.addItems(branches)
        for i in range(self.list.count()):
            if self.list.item(i).text() in ("w", "sb_1", "b_1_1"):
                self.list.item(i).setSelected(True)
        self.redraw()

    def redraw(self) -> None:
        self.plot.clear()
        if self.data is None or self.data.window is None:
            return
        t = self.data.window["time"] * 1e9
        for k, item in enumerate(self.list.selectedItems()):
            y = self.data.vector(item.text())
            if y is None:
                continue
            self.plot.plot(t, y, pen=pg.mkPen(COLORS[k % len(COLORS)], width=1.5), name=item.text())


class OverviewTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["項目", "値"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)

    def set_data(self, data: ResultData) -> None:
        rows: list[tuple[str, str]] = []
        rows.append(("ファイル", str(data.path)))
        rows.append(("プロジェクト", f"{data.meta.get('project_name', '')} (hash {data.meta.get('project_hash', '')})"))
        rows.append(("モード / ngspice", f"{data.meta.get('mode', '')} / {data.meta.get('ngspice_version', '')}"))
        pa = data.plasma.get("attrs", {})
        if pa:
            rows.append(("Te [eV] / ν_m [1/s] / h_l", f"{float(pa.get('te', 0)):.3f} / {float(pa.get('nu_m', 0)):.3e} / {float(pa.get('h_l', 0)):.3f}"))
        if data.ac is not None:
            d = data.ac["diagnostics"]
            rows.append(("AC 周波数 [MHz]", f"{data.ac['frequency']/1e6:.3f}"))
            rows.append(("f_pe / f_PSR [MHz]", f"{float(d.get('f_pe', 0))/1e6:.0f} / {float(d.get('f_psr', 0))/1e6:.0f}"))
            rows.append(("λ_eff [m] / kR_wafer", f"{float(d.get('lambda_eff', float('nan'))):.3f} / {float(d.get('kR_wafer', float('nan'))):.3f}"))
            rows.append(("表皮深さ δ [mm] / δ/(l_p/2)", f"{float(d.get('delta', 0))*1e3:.1f} / {float(d.get('delta_over_half_bulk', 0)):.2f}"))
            for port, pr in data.ac["ports"].items():
                z = complex(pr["z"])
                rows.append((f"AC ポート {port}: |V| / |I| / Z / P", f"{abs(complex(pr['v'])):.1f} V / {abs(complex(pr['i'])):.2f} A / {z.real:.2f}{z.imag:+.2f}j Ω / {float(pr['p']):.1f} W"))
            s_e = data.ac["cells"]["s_e"]
            rows.append(("AC 均一性 (max-min)/(2 mean) of S_e（ウェハ領域）", self._uniformity(data, s_e)))
        if data.tran is not None:
            a, m = data.tran["attrs"], data.tran["metrics"]
            rows.append(("TRAN 周期数 / 周期定常", f"{int(a.get('cycles', 0))} / {'達成' if bool(a.get('pss_converged', False)) else '未達'} (変化 {float(a.get('pss_change', 0)):.2e})"))
            rows.append(("自己バイアス反復 / クランプ電圧", f"{int(a.get('bias_iterations', 0))} / {a.get('v_dc_clamp', '')}"))
            rows.append(("電源電力 / 散逸 [W]", f"{float(m.get('p_source_total', 0)):.1f} / {float(m.get('p_total_dissipated', 0)):.1f}"))
            rows.append(("電力収支誤差", f"{float(m.get('power_balance_error', 0))*100:.2f} %"))
            rows.append(("径方向オーミック加熱 [W]", f"{float(m.get('p_ohm_radial_total', 0)):.1f}"))
            for port, pr in data.tran["ports"].items():
                rows.append((f"ポート {port}: V_dc / V_1 / I_1 / P", f"{float(pr['v_dc']):.1f} V / {float(pr['v_1']):.1f} V / {float(pr['i_1']):.2f} A / {float(pr['p']):.1f} W"))
                rows.append((f"ポート {port}: Z（基本波）", f"{float(pr['z_re']):.2f}{float(pr['z_im']):+.2f}j Ω"))
            rows.append(("均一性 (max-min)/(2 mean) of S_e（ウェハ領域）", self._uniformity(data, data.tran["cells"]["s_e"])))
            rows.append(("均一性 of 下部シース DC 電圧（ウェハ領域）", self._uniformity(data, data.tran["cells"]["vsh_b_dc"])))
            rows.append(("計算時間 [s]", f"{float(a.get('runtime_s', 0)):.1f}"))
        self.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.setItem(i, 0, QTableWidgetItem(k))
            self.setItem(i, 1, QTableWidgetItem(v))

    @staticmethod
    def _uniformity(data: ResultData, y) -> str:
        idx = data.mesh.get("region_index")
        if idx is None:
            return "-"
        m = idx == idx[0]
        yy = np.asarray(y, dtype=float)[m]
        if len(yy) < 2 or yy.mean() == 0:
            return "-"
        return f"{(yy.max() - yy.min()) / (2 * yy.mean()) * 100:.1f} %  (中心/エッジ = {yy[0]/yy[-1]:.3f})"


class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.label = QLabel("結果はまだありません。実行するか、ラン履歴から選択してください。")
        self.tabs = QTabWidget()
        self.overview = OverviewTable()
        self.radial = RadialPlot()
        self.wave = WaveformPlot()
        self.map = MapView()
        self.edge = EdgeView()
        self.spectrum = SpectrumView()
        self.density = DensityView()
        self.tabs.addTab(self.overview, "概要")
        self.tabs.addTab(self.radial, "径方向プロファイル")
        self.tabs.addTab(self.map, "2D マップ")
        self.tabs.addTab(self.wave, "波形")
        self.tabs.addTab(self.spectrum, "スペクトル")
        self.tabs.addTab(self.edge, "エッジ")
        self.tabs.addTab(self.density, "密度")
        lay.addWidget(self.label)
        lay.addWidget(self.tabs, 1)
        self.data: ResultData | None = None

    def load(self, path: str) -> None:
        self.data = ResultData(path)
        self.label.setText(f"結果: {path}")
        self.overview.set_data(self.data)
        self.radial.set_data(self.data)
        self.map.set_data(self.data)
        self.wave.set_data(self.data)
        self.spectrum.set_data(self.data)
        self.edge.set_data(self.data)
        self.density.set_data(self.data)
