"""Report generation (M6): a self-contained HTML page (inline SVG charts, no external files or libraries) summarizing
one result file: warnings, operating point, uniformity, radial profiles, density iteration, IEDF and the inputs."""

from __future__ import annotations

import html
import json
import math
import time
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .. import __version__
from .result_data import ResultData, result_warnings

# warm clay palette (same tokens as the GUI theme; light and dark)
_CSS = """
:root { --bg:#FAF9F5; --surface:#F0EEE6; --border:#E0DDD4; --text:#141413; --muted:#6B6A65; --accent:#C96442; --warn:#B4532F;
        --s0:#C96442; --s1:#3B6EA8; --s2:#2F7D4F; --s3:#9A6700; --s4:#7B5EA7; --s5:#5F7A7A; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#262624; --surface:#30302E; --border:#45453F; --text:#F5F4EE; --muted:#A6A39A; --accent:#D97757; --warn:#E08A6A;
          --s0:#D97757; --s1:#7AA7D9; --s2:#6FBF8B; --s3:#E3B341; --s4:#B39DDB; --s5:#9DB8B8; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.55 "Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 36px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
.meta { color: var(--muted); font-size: 13px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
td, th { text-align: left; padding: 5px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; } td.num { font-variant-numeric: tabular-nums; }
.warn { border-left: 4px solid var(--warn); background: var(--surface); padding: 8px 12px; margin: 6px 0; }
.ok { color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 18px; }
figure { margin: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px 6px; }
figcaption { font-size: 13px; color: var(--muted); margin-bottom: 4px; }
svg { width: 100%; height: auto; display: block; } svg text { fill: var(--muted); font-size: 11px; }
.legend span { display: inline-block; margin-right: 12px; font-size: 12px; color: var(--muted); }
.legend i { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin-right: 4px; }
@media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
"""


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.ceil(lo / step) * step
    return [start + k * step for k in range(int((hi - start) / step + 1e-9) + 1)]


def _fmt_tick(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    return f"{v:.3g}" if 1e-2 <= a < 1e4 else f"{v:.1e}"


def svg_chart(series: Sequence[tuple[str, np.ndarray, np.ndarray]], xlabel: str, ylabel: str, log_y: bool = False,
              width: int = 480, height: int = 260) -> str:
    """Line chart as inline SVG. series: (name, x, y)."""
    ml, mr, mt, mb = 58, 12, 10, 40
    pw, ph = width - ml - mr, height - mt - mb
    xs = np.concatenate([np.asarray(s[1], dtype=float) for s in series]) if series else np.array([0.0, 1.0])
    ys = np.concatenate([np.asarray(s[2], dtype=float) for s in series]) if series else np.array([0.0, 1.0])
    if log_y:
        ys = np.log10(np.maximum(ys[np.isfinite(ys)], 1e-300))
    ys = ys[np.isfinite(ys)]
    x0, x1 = float(np.min(xs)), float(np.max(xs))
    y0, y1 = (float(np.min(ys)), float(np.max(ys))) if ys.size else (0.0, 1.0)
    if x1 <= x0:
        x1 = x0 + 1.0
    if y1 <= y0:
        y0, y1 = y0 - 0.5 * (abs(y0) or 1.0), y1 + 0.5 * (abs(y1) or 1.0)
    pad = 0.05 * (y1 - y0)
    y0, y1 = y0 - pad, y1 + pad
    if not log_y and min(np.min(ys), 0) >= 0 and y0 < 0:
        y0 = 0.0

    def X(v):
        return ml + (v - x0) / (x1 - x0) * pw

    def Y(v):
        return mt + ph - (v - y0) / (y1 - y0) * ph

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(ylabel)} vs {html.escape(xlabel)}">',
           f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" stroke="var(--border)"/>']
    for t in _nice_ticks(x0, x1):
        out.append(f'<line x1="{X(t):.1f}" y1="{mt}" x2="{X(t):.1f}" y2="{mt + ph}" stroke="var(--border)" stroke-width="0.6"/>'
                   f'<text x="{X(t):.1f}" y="{mt + ph + 14}" text-anchor="middle">{_fmt_tick(t)}</text>')
    yt = list(range(math.ceil(y0), math.floor(y1) + 1)) if log_y else _nice_ticks(y0, y1)
    for t in yt:
        lab = f"1e{int(t)}" if log_y else _fmt_tick(t)
        out.append(f'<line x1="{ml}" y1="{Y(t):.1f}" x2="{ml + pw}" y2="{Y(t):.1f}" stroke="var(--border)" stroke-width="0.6"/>'
                   f'<text x="{ml - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{lab}</text>')
    out.append(f'<text x="{ml + pw / 2}" y="{height - 6}" text-anchor="middle">{html.escape(xlabel)}</text>')
    out.append(f'<text x="12" y="{mt + ph / 2}" text-anchor="middle" transform="rotate(-90 12 {mt + ph / 2})">{html.escape(ylabel)}</text>')
    for k, (name, x, y) in enumerate(series):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        if log_y:
            y = np.log10(np.maximum(y, 1e-300))
        ok = np.isfinite(x) & np.isfinite(y)
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(x[ok], y[ok]))
        out.append(f'<polyline points="{pts}" fill="none" stroke="var(--s{k % 6})" stroke-width="1.8" stroke-linejoin="round"/>')
    out.append("</svg>")
    if len(series) > 1:
        out.append('<div class="legend">' + "".join(f'<span><i style="background:var(--s{k % 6})"></i>{html.escape(s[0])}</span>'
                                                    for k, s in enumerate(series)) + "</div>")
    return "".join(out)


def _figure(caption: str, chart: str) -> str:
    return f"<figure><figcaption>{html.escape(caption)}</figcaption>{chart}</figure>"


def _table(rows: Sequence[tuple[str, str]], header: Optional[tuple[str, str]] = None) -> str:
    h = f"<tr><th>{html.escape(header[0])}</th><th>{html.escape(header[1])}</th></tr>" if header else ""
    return "<table>" + h + "".join(f"<tr><td>{html.escape(k)}</td><td class='num'>{html.escape(v)}</td></tr>" for k, v in rows) + "</table>"


def _uniformity(y: np.ndarray, wafer: np.ndarray) -> str:
    yy = np.asarray(y, dtype=float)[wafer]
    if len(yy) < 2 or yy.mean() == 0:
        return "-"
    return f"{(yy.max() - yy.min()) / (2 * yy.mean()) * 100:.1f} %（中心/エッジ {yy[0] / yy[-1]:.3f}）"


def summary_rows(d: ResultData) -> list[tuple[str, str]]:
    idx = np.asarray(d.mesh.get("region_index", np.zeros(len(d.r), dtype=int)))
    wafer = idx == idx[0]
    rows: list[tuple[str, str]] = [("解析モード", str(d.meta.get("mode", "")))]
    pa = d.plasma.get("attrs", {})
    if pa:
        rows.append(("ガス / Te / h_l", f"{pa.get('gas', '')} / {float(pa.get('te', 0)):.3f} eV / {float(pa.get('h_l', 0)):.3f}"))
    if d.ac is not None:
        dg = d.ac["diagnostics"]
        rows.append(("AC 周波数", f"{d.ac['frequency'] / 1e6:.3f} MHz"))
        rows.append(("λ_eff / kR（ウェハ）", f"{float(dg.get('lambda_eff', float('nan'))):.3f} m / {float(dg.get('kR_wafer', float('nan'))):.3f}"))
        for port, pr in d.ac["ports"].items():
            z = complex(pr["z"])
            rows.append((f"AC ポート {port}: |V| / P / Z", f"{abs(complex(pr['v'])):.1f} V / {float(pr['p']):.2f} W / {z.real:.2f}{z.imag:+.2f}j Ω"))
        rows.append(("AC S_e 均一性（ウェハ）", _uniformity(d.ac["cells"]["s_e"], wafer)))
    if d.tran is not None:
        a, m = d.tran["attrs"], d.tran["metrics"]
        rows.append(("TRAN 周期数 / 周期定常", f"{int(a.get('cycles', 0))} / {'達成' if bool(a.get('pss_converged', False)) else '未達'}"))
        rows.append(("電源電力 / 電力収支誤差", f"{float(m.get('p_source_total', 0)):.2f} W / {float(m.get('power_balance_error', 0)) * 100:.2f} %"))
        for port, pr in d.tran["ports"].items():
            rows.append((f"ポート {port}: V_dc / V_1 / P", f"{float(pr['v_dc']):.1f} V / {float(pr['v_1']):.1f} V / {float(pr['p']):.2f} W"))
        rows.append(("TRAN S_e 均一性（ウェハ）", _uniformity(d.tran["cells"]["s_e"], wafer)))
        rows.append(("シース DC 電圧 均一性（ウェハ）", _uniformity(d.tran["cells"]["vsh_b_dc"], wafer)))
    if d.iteration is not None:
        ia = d.iteration["attrs"]
        n_fin = np.asarray(d.iteration["n"])[-1]
        rows.append(("密度反復", f"{int(ia.get('iterations', 0))} 回, {'収束' if bool(ia.get('converged', False)) else '未収束'}"))
        rows.append(("SC 中心密度 / 均一性", f"{n_fin[0]:.3e} m⁻³ / {_uniformity(n_fin, wafer)}"))
    return rows


def input_rows(project: dict) -> list[tuple[str, str]]:
    rows = []
    g = project.get("geometry", {})
    for reg in g.get("regions", []):
        stack = " + ".join(f"{L['material'] if isinstance(L['material'], str) else 'custom'} {L['t'] * 1e3:.3g} mm" for L in reg.get("stack", []))
        rows.append((f"領域 {reg['name']}（{reg['conductor']}）", f"r_out {reg['r_out'] * 1e3:.1f} mm, z_offset {reg.get('z_offset', 0) * 1e3:.2f} mm, {stack or '裸電極'}"))
    gap = g.get("gap", {})
    if gap:
        rows.append(("ギャップ", f"{gap.get('value', 0) * 1e3:.2f} mm"))
    pl = project.get("plasma", {})
    if pl:
        rows.append(("ガス / 圧力", f"{pl.get('gas')} / {pl.get('pressure_pa', 0):.3g} Pa"))
        rows.append(("シースモデル", str(pl.get("sheath", {}).get("model", ""))))
    for port, ps in project.get("circuit", {}).get("ports", {}).items():
        src = ", ".join(f"{s['f'] / 1e6:.3g} MHz {s['v']:.4g} V {s.get('phase_deg', 0):.3g}°" + (f" (P={s['power']} W)" if s.get("power") else "")
                        for s in ps.get("sources", []))
        gen = ps.get("generator", {}).get("type", "none")
        rows.append((f"ポート {port}", f"{src}; C_b={ps.get('blocking_c')}, 整合器 {gen}"))
    return rows


def build_report(path: str | Path, title: Optional[str] = None) -> str:
    """HTML text of the report for one HDF5 result file."""
    d = ResultData(path)
    name = title or str(d.meta.get("project_name", Path(path).stem))
    r_mm = np.asarray(d.r) * 1e3
    parts = [f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<title>{html.escape(name)} レポート</title><style>{_CSS}</style></head><body><main>",
             f"<h1>{html.escape(name)}</h1><div class='meta'>{html.escape(str(Path(path).resolve()))}<br>作成 {time.strftime('%Y-%m-%d %H:%M')} · "
             f"CirPlas {__version__} · ngspice {html.escape(str(d.meta.get('ngspice_version', '')))} · 結果作成 {html.escape(str(d.meta.get('created', '')))}</div>"]
    warns = result_warnings(d)
    parts.append("<h2>警告</h2>" + ("".join(f"<div class='warn'>⚠ {html.escape(w)}</div>" for w in warns) if warns else "<p class='ok'>警告はありません。</p>"))
    parts.append("<h2>概要</h2>" + _table(summary_rows(d)))

    figs = []
    src = d.tran["cells"] if d.tran is not None else (d.ac["cells"] if d.ac is not None else None)
    if src is not None:
        figs.append(_figure("電子加熱面密度 S_e(r)", svg_chart([("S_e", r_mm, src["s_e"])], "r [mm]", "S_e [W/m²]")))
    if d.tran is not None:
        c = d.tran["cells"]
        figs.append(_figure("下部シース電圧（DC・RF 振幅）", svg_chart([("DC", r_mm, c["vsh_b_dc"]), ("RF 振幅", r_mm, c["vsh_b_1"])], "r [mm]", "V [V]")))
        figs.append(_figure("下部シース厚（平均）", svg_chart([("s̄", r_mm, np.asarray(c["s_b_mean"]) * 1e3)], "r [mm]", "s [mm]")))
    elif d.ac is not None:
        figs.append(_figure("下部シース電圧 |V|（AC）", svg_chart([("|V_sh|", r_mm, np.abs(d.ac["cells"]["vsh_b"]))], "r [mm]", "V [V]")))
    if "n0" in d.plasma:
        figs.append(_figure("電子密度 n(r)", svg_chart([("n", r_mm, d.plasma["n0"])], "r [mm]", "n [m⁻³]")))
    if d.iteration is not None and len(d.iteration.get("residual", [])):
        res = np.asarray(d.iteration["residual"])
        figs.append(_figure("密度反復の残差", svg_chart([("残差", np.arange(1, len(res) + 1), res)], "反復", "max |Δn|/n", log_y=True)))
    iedf = d.tran.get("iedf") if d.tran is not None else None
    if iedf is not None:
        n = len(r_mm)
        cells = sorted({0, n // 2, n - 1})
        figs.append(_figure("IEDF（下部表面、無衝突イオン）", svg_chart([(f"r={r_mm[i]:.0f} mm", iedf["energy"], np.asarray(iedf["f"])[i]) for i in cells],
                                                                    "E [eV]", "f(E) [1/eV]")))
    if figs:
        parts.append("<h2>径方向プロファイル</h2><div class='grid'>" + "".join(figs) + "</div>")
    if d.project:
        parts.append("<h2>入力条件</h2>" + _table(input_rows(d.project)))
        parts.append("<details><summary>プロジェクト JSON</summary><pre style='white-space:pre-wrap;font-size:12px'>"
                     + html.escape(json.dumps(d.project, indent=1, ensure_ascii=False)) + "</pre></details>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write_report(h5_path: str | Path, out_path: str | Path, title: Optional[str] = None) -> Path:
    out = Path(out_path)
    out.write_text(build_report(h5_path, title), encoding="utf-8")
    return out
