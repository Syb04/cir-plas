"""Plasma panel with derived diagnostics."""

from __future__ import annotations

import math

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...core.constants import C_LIGHT
from ..project_model import ProjectModel
from ..widgets.paramform import BoundForm, group

MTORR = 0.133322368


class PlasmaPanel(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        lay = QVBoxLayout(self)
        f = BoundForm(model)
        f.add_choice("ガス", "plasma.gas", [("ar", "Ar")])
        f.add_float("圧力", "plasma.pressure_pa", "mTorr", 1.0 / MTORR)
        f.add_float("ガス温度", "plasma.gas_temperature_k", "K")
        lay.addWidget(group("ガス", f))
        d = BoundForm(model)
        d.add_choice("密度モデル", "plasma.density.model", [("uniform", "一様"), ("parabolic", "放物 n0(1-a(r/R)^2)"), ("table", "テーブル"), ("file", "ファイル")])
        d.add_float("中心密度 n0", "plasma.density.n0", "m^-3")
        d.add_float("放物係数 a", "plasma.density.alpha")
        d.add_choice("電子温度", "plasma.te.mode", [("auto", "自動（粒子バランス）"), ("manual", "手動")])
        d.add_float("Te（手動）", "plasma.te.value", "eV")
        d.add_choice("h_l", "plasma.h_l.mode", [("auto", "自動"), ("manual", "手動")])
        d.add_float("h_l（手動）", "plasma.h_l.value")
        d.add_choice("ν_m", "plasma.nu_m.mode", [("auto", "自動"), ("manual", "手動")])
        d.add_float("ν_m（手動）", "plasma.nu_m.value", "1/s")
        lay.addWidget(group("電子密度・温度", d))
        s = BoundForm(model)
        s.add_choice("シースモデル", "plasma.sheath.model", [("matrix", "matrix（一様イオン）"), ("child", "Child–Langmuir")])
        s.add_float("最小シース幅 α_min", "plasma.sheath.alpha_min", "λ_D")
        s.add_choice("確率的加熱", "plasma.sheath.stochastic.model", [("hardwall", "hard-wall"), ("off", "オフ")])
        s.add_float("κ_stoc", "plasma.sheath.stochastic.kappa")
        s.add_choice("電子電流の実装", "plasma.sheath.electron_current", [("bsource", "B 源（滑らかな指数）"), ("diode", "並列ダイオード")])
        s.add_choice("非線形容量の実装", "plasma.sheath.cap_form", [("ecf", "補助容量方式（推奨）"), ("charge", "ngspice Q= 素子"), ("capacitance", "C= 素子")])
        lay.addWidget(group("シース", s))
        info = BoundForm(model)
        info.add_info("導出量", self._derived)
        lay.addWidget(group("診断量（中心密度・最高周波数で評価）", info))
        lay.addStretch(1)

    def _derived(self) -> str:
        try:
            from ...core.mesh import build_radial_mesh
            from ...core.plasma import compute_plasma_state, skin_depth

            p = self.model.project()
            mesh = build_radial_mesh(p.geometry, p.numerics.mesh)
            gap = p.geometry.gap_at(0.0)
            st = compute_plasma_state(p, mesh.r_centers, l_p_ref=0.85 * gap)
            f = p.f_max()
            w = 2 * math.pi * f
            d = st.diagnostics(w, s_tot=2.5e-3, l_total=gap, l_p=0.85 * gap)
            n_z = max(1, int(math.ceil(3 * (0.85 * gap / 2) / d["delta"]))) if d["delta"] < 0.85 * gap / 2 else 1
            lines = [
                f"Te = {st.te:.2f} eV   n_g = {st.n_g:.3e} m⁻³   λ_i = {st.lambda_i*1e3:.2f} mm   h_l = {st.h_l:.3f}",
                f"ν_m = {st.nu_m:.3e} 1/s   u_B = {st.u_b:.0f} m/s   f_pe = {d['f_pe']/1e6:.0f} MHz   λ_D = {d['lambda_d']*1e3:.3f} mm",
                f"表皮深さ δ = {d['delta']*1e3:.1f} mm（バルク半厚との比 {d['delta']/(0.425*gap):.2f}）  推奨 N_z = {n_z}",
                f"f_PSR ≈ {d['f_psr']/1e6:.0f} MHz（公称シース 2.5 mm）  λ_eff ≈ {d['lambda_eff']:.2f} m  kR_wafer ≈ {d['k_eff']*p.geometry.regions[0].r_out:.2f}",
            ]
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            return f"（計算不可: {exc}）"
