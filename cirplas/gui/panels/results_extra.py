"""Additional result views: 2D maps, edge zoom, spectrum, density iteration."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QVBoxLayout, QWidget

from ...core.post.signals import harmonics, resample, uniform_grid
from ..results_io import ResultData

COLORS = ["#1565c0", "#ef6c00", "#2e7d32", "#6a1b9a", "#c62828", "#00838f", "#4e342e", "#37474f", "#827717", "#ad1457"]


class MapView(QWidget):
    """r-z pseudo-color map of per-layer quantities (bulk layers only)."""

    QUANTITIES = [("p_ohm", "オーミック加熱密度 [W/m³]"), ("jz_1", "軸方向電流密度 |J_z| 基本波 [A/m²]"), ("jr_1", "径方向電流密度 |J_r| 基本波 [A/m²]")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ResultData | None = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.quantity = QComboBox()
        for k, label in self.QUANTITIES:
            self.quantity.addItem(label, k)
        self.quantity.currentIndexChanged.connect(self.redraw)
        self.info = QLabel("")
        top.addWidget(QLabel("量"))
        top.addWidget(self.quantity)
        top.addWidget(self.info, 1)
        lay.addLayout(top)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "r [mm]")
        self.plot.setLabel("left", "z [mm]（ウェハ電極面基準）")
        self.plot.setAspectLocked(False)
        lay.addWidget(self.plot, 1)
        self.colorbar = None

    def set_data(self, data: ResultData) -> None:
        self.data = data
        self.redraw()

    def redraw(self) -> None:
        self.plot.clear()
        if self.data is None or self.data.tran is None or "map" not in self.data.tran:
            self.info.setText("2D マップは TRAN 結果でのみ利用できます")
            return
        m = self.data.tran["map"]
        key = self.quantity.currentData()
        val = np.asarray(m[key], dtype=float)
        z_bulk = np.asarray(self.data.mesh["z_bulk"], dtype=float)  # (n_c, nz+1) relative to each cell's conductor
        z_off = np.asarray(self.data.mesh.get("z_offset", np.zeros(z_bulk.shape[0])), dtype=float)
        r_edges = np.asarray(self.data.mesh["r_edges"], dtype=float)
        if key == "jr_1":
            # boundary quantity: use boundary radii as x centers -> build edges from cell centers
            rc = np.asarray(m["r_centers"], dtype=float)
            x_edges = rc  # len n_c = n_boundaries + 1
            n_x = val.shape[0]
            z_rows = 0.5 * (z_bulk[:-1] + z_bulk[1:])  # average adjacent cells
            z_abs = z_rows + 0.5 * (z_off[:-1] + z_off[1:])[:, None]
        else:
            x_edges = r_edges
            n_x = val.shape[0]
            z_abs = z_bulk + z_off[:, None]
        nz = val.shape[1]
        vmax = float(np.nanmax(val)) if val.size else 1.0
        cmap = pg.colormap.get("viridis")
        lut = cmap.getLookupTable(0.0, 1.0, 256)
        for i in range(n_x):
            for j in range(nz):
                x0, x1 = x_edges[i] * 1e3, x_edges[i + 1] * 1e3
                y0, y1 = z_abs[i, j] * 1e3, z_abs[i, j + 1] * 1e3
                c = lut[int(255 * min(max(val[i, j] / vmax, 0.0), 1.0))] if vmax > 0 else lut[0]
                rect = pg.QtWidgets.QGraphicsRectItem(x0, y0, x1 - x0, y1 - y0)
                rect.setBrush(pg.mkBrush(int(c[0]), int(c[1]), int(c[2])))
                rect.setPen(pg.mkPen(None))
                self.plot.addItem(rect)
        self.info.setText(f"最大 {vmax:.4g}（色: viridis, 0〜最大）  層数 {nz}")
        if self.colorbar is None:
            self.colorbar = pg.ColorBarItem(values=(0, vmax), colorMap=cmap)
        self.colorbar.setLevels((0, vmax))
        self.plot.setXRange(0, float(x_edges[-1]) * 1e3)


class EdgeView(QWidget):
    """Zoom around the wafer edge: sheath boundary profile, tilt and sheath voltages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ResultData | None = None
        lay = QVBoxLayout(self)
        self.info = QLabel("")
        lay.addWidget(self.info)
        self.p1 = pg.PlotWidget(title="シース境界（下部）と傾き")
        self.p1.setLabel("bottom", "r [mm]")
        self.p1.setLabel("left", "z [mm]")
        self.p1.addLegend()
        self.p2 = pg.PlotWidget(title="下部シース電圧（DC・RF 振幅）")
        self.p2.setLabel("bottom", "r [mm]")
        self.p2.setLabel("left", "V")
        self.p2.addLegend()
        lay.addWidget(self.p1, 1)
        lay.addWidget(self.p2, 1)

    def set_data(self, data: ResultData) -> None:
        self.data = data
        self.p1.clear()
        self.p2.clear()
        mesh = data.mesh
        r = np.asarray(mesh["r_centers"]) * 1e3
        z_surf = np.asarray(mesh.get("z_surface_abs", mesh["z_surface_b"])) * 1e3
        if data.tran is not None:
            s = np.asarray(data.tran["cells"]["s_b_mean"]) * 1e3
            v_dc = np.asarray(data.tran["cells"]["vsh_b_dc"])
            v_1 = np.asarray(data.tran["cells"]["vsh_b_1"])
        elif data.ac is not None:
            s = np.asarray(mesh["s_b"]) * 1e3
            v_dc = None
            v_1 = np.abs(np.asarray(data.ac["cells"]["vsh_b"]))
        else:
            return
        z_s = z_surf + s
        r_w = np.asarray(mesh["r_edges"])[np.argmax(np.diff(np.asarray(mesh["region_index"])) != 0) + 1] * 1e3 if len(mesh["region_index"]) > 1 else r[-1]
        sel = (r > r_w - 40) & (r < r_w + 40)
        self.p1.plot(r[sel], z_surf[sel], pen=pg.mkPen("#9e9e9e", width=2), name="表面")
        self.p1.plot(r[sel], z_s[sel], pen=pg.mkPen("#1565c0", width=2), symbol="o", symbolSize=5, name="シース境界（平均厚）")
        theta = np.degrees(np.arctan(np.gradient(z_s, r)))
        self.p1.addItem(pg.InfiniteLine(pos=r_w, angle=90, pen=pg.mkPen("#9e9e9e", style=Qt.PenStyle.DashLine)))
        if v_dc is not None:
            self.p2.plot(r[sel], v_dc[sel], pen=pg.mkPen("#2e7d32", width=2), symbol="o", symbolSize=5, name="DC")
        self.p2.plot(r[sel], v_1[sel], pen=pg.mkPen("#ef6c00", width=2), symbol="s", symbolSize=5, name="RF 振幅")
        self.p2.addItem(pg.InfiniteLine(pos=r_w, angle=90, pen=pg.mkPen("#9e9e9e", style=Qt.PenStyle.DashLine)))
        near = sel & (r < r_w)
        self.info.setText(f"ウェハ端 r = {r_w:.1f} mm   エッジ 10 mm 内の最大傾き |θ| = {np.max(np.abs(theta[(r > r_w - 10) & (r < r_w)])) if np.any((r > r_w - 10) & (r < r_w)) else float('nan'):.2f}°   "
                          f"シース厚: 中心 {s[0]:.2f} mm / エッジ {s[near][-1] if near.any() else float('nan'):.2f} mm")


class SpectrumView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ResultData | None = None
        lay = QHBoxLayout(self)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self.redraw)
        from PySide6.QtWidgets import QCheckBox

        self.log = QCheckBox("対数表示 (log10)")
        self.log.setChecked(True)
        self.log.toggled.connect(self.redraw)
        ll.addWidget(self.log)
        ll.addWidget(self.list, 1)
        left.setMaximumWidth(260)
        self.plot = pg.PlotWidget(title="高調波スペクトル（基本周期基準）")
        self.plot.setLabel("bottom", "高調波次数（0 = DC）")
        self.plot.addLegend()
        lay.addWidget(left)
        lay.addWidget(self.plot, 1)

    def set_data(self, data: ResultData) -> None:
        self.data = data
        self.list.clear()
        if data.window is None or data.tran is None:
            self.list.addItem("（波形がありません）")
            return
        names = sorted(n for n in data.window["names"] if n.startswith(("sb_", "b_", "w", "t_")) and "#" not in n)
        names = sorted(names, key=lambda s: (s not in ("w",), not s.startswith("sb_"), s))
        self.list.addItems(names)
        for i in range(self.list.count()):
            if self.list.item(i).text() in ("w", "sb_1"):
                self.list.item(i).setSelected(True)
        self.redraw()

    def redraw(self) -> None:
        self.plot.clear()
        if self.data is None or self.data.window is None or self.data.tran is None:
            return
        period = float(self.data.tran["attrs"]["period"])
        meas = int(self.data.tran["attrs"]["meas_cycles"])
        t = self.data.window["time"]
        n_per = 256
        t_u = uniform_grid(t[-1] - meas * period, period, meas, n_per)
        items = self.list.selectedItems()
        width = 0.8 / max(1, len(items))
        use_log = self.log.isChecked()
        self.plot.setLabel("left", "log10(振幅 [V])" if use_log else "振幅 [V]")
        for k, item in enumerate(items):
            y = self.data.vector(item.text())
            if y is None:
                continue
            h = harmonics(resample(t, y, t_u), n_per, n_harm=20)
            amps = np.abs(h.c)
            amps[0] = abs(h.dc)
            vals = np.log10(np.maximum(amps, 1e-3)) + 3.0 if use_log else amps  # log scale floored at 1 mV, offset so bars are positive
            x = np.arange(len(amps)) + (k - 0.5 * (len(items) - 1)) * width
            edges = np.concatenate([x - width / 2, [x[-1] + width / 2]])
            self.plot.plot(edges, vals, stepMode="center", fillLevel=0, brush=pg.mkBrush(COLORS[k % len(COLORS)] + "a0"),
                           pen=pg.mkPen(COLORS[k % len(COLORS)]), name=item.text())
        if use_log:
            ax = self.plot.getAxis("left")
            ax.setTicks([[(v, f"{10**(v-3):g}") for v in range(0, 7)]])
        else:
            self.plot.getAxis("left").setTicks(None)


class DensityView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.info = QLabel("")
        self.p1 = pg.PlotWidget(title="電子密度の反復履歴")
        self.p1.setLabel("bottom", "r [mm]")
        self.p1.setLabel("left", "n [m⁻³]")
        self.p1.addLegend()
        self.p2 = pg.PlotWidget(title="残差（相対変化の最大値）")
        self.p2.setLabel("bottom", "反復")
        self.p2.setLogMode(y=True)
        lay.addWidget(self.info)
        lay.addWidget(self.p1, 2)
        lay.addWidget(self.p2, 1)

    def set_data(self, data: ResultData) -> None:
        self.p1.clear()
        self.p2.clear()
        it = data.iteration
        if it is None:
            self.info.setText("密度反復の結果はありません（SC モードで実行してください）")
            return
        r = np.asarray(it["r_centers"]) * 1e3
        n_hist = np.asarray(it["n"])
        for k in range(n_hist.shape[0]):
            last = k == n_hist.shape[0] - 1
            pen = pg.mkPen("#c62828" if last else (120, 120, 120, 90), width=3 if last else 1)
            self.p1.plot(r, n_hist[k], pen=pen, name=("最終" if last else ("初期" if k == 0 else None)))
        res = np.asarray(it["residual"])
        self.p2.plot(np.arange(1, len(res) + 1), np.maximum(res, 1e-9), pen=pg.mkPen("#1565c0", width=2), symbol="o")
        a = it["attrs"]
        te = np.asarray(it["te"])
        self.info.setText(f"反復 {int(a.get('iterations', 0))} 回, {'収束' if bool(a.get('converged', False)) else '未収束'},  Te = {te[-1] if len(te) else float('nan'):.3f} eV,  D_a = {float(a.get('d_a', 0)):.3f} m²/s")
