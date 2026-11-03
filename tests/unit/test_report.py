"""HTML report (core/report.py) from a synthetic result file."""

import re
import xml.dom.minidom

import numpy as np

from cirplas.core.report import build_report, svg_chart


def test_svg_chart_is_well_formed():
    x = np.linspace(0, 150, 20)
    s = svg_chart([("a", x, 1 + x), ("b", x, np.full_like(x, 2.0))], "r [mm]", "S_e [W/m²]")
    xml.dom.minidom.parseString(s[: s.index("</svg>") + 6])
    assert s.count("<polyline") == 2 and "legend" in s
    log = svg_chart([("res", np.arange(1, 6), np.array([1, 0.1, 1e-2, 1e-3, 1e-4]))], "k", "res", log_y=True)
    assert "1e-4" in log or "1e-3" in log


def test_report_sections(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
    from test_m5_views import _fake_result  # noqa: E402

    h5 = _fake_result(tmp_path / "r.h5", converged=False)
    text = build_report(h5)
    assert text.startswith("<!doctype html>")
    assert "密度反復が未収束" in text  # warning section
    caps = re.findall(r"<figcaption>(.*?)</figcaption>", text)
    assert "電子加熱面密度 S_e(r)" in caps and "電子密度 n(r)" in caps and "密度反復の残差" in caps
    assert "入力条件" in text and "ポート W" in text
    for svg in re.findall(r"<svg.*?</svg>", text):
        xml.dom.minidom.parseString(svg)
