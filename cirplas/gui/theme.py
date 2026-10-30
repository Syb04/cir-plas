"""Warm "clay" colour theme (light / dark / follow-OS) for the Qt GUI and the pyqtgraph plots.

All colours used by the GUI come from the token dictionaries below; widgets never hard-code hex values.
The theme is applied through a Fusion ``QPalette`` plus a stylesheet generated from the tokens, and the
selected mode is persisted with ``QSettings``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QSettings, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication

LIGHT = dict(
    bg="#FAF9F5", surface="#F0EEE6", surface_2="#E8E6DC", border="#E0DDD4",
    text="#141413", text_muted="#6B6A65", text_disabled="#A8A69E",
    accent="#C96442", accent_strong="#B4532F", accent_hover="#A84F31",
    accent_fg="#FFFFFF", accent_text="#A84F31", focus="#C96442",
    selection="rgba(201, 100, 66, 0.15)",
    success="#2F7D4F", warning="#9A6700", error="#B42318", info="#3B6EA8",
)
DARK = dict(
    bg="#262624", surface="#30302E", surface_2="#3A3A37", border="#45453F",
    text="#F5F4EE", text_muted="#A6A39A", text_disabled="#6B6A65",
    accent="#D97757", accent_strong="#D97757", accent_hover="#E08A6A",
    accent_fg="#1F1E1D", accent_text="#E08A6A", focus="#D97757",
    selection="rgba(217, 119, 87, 0.20)",
    success="#6FBF8B", warning="#E3B341", error="#F07167", info="#7AA7D9",
)
SERIES = {
    "light": ["#C96442", "#3B6EA8", "#2F7D4F", "#9A6700", "#7B5EA7", "#5F7A7A"],
    "dark": ["#D97757", "#7AA7D9", "#6FBF8B", "#E3B341", "#B39DDB", "#9DB8B8"],
}
MODES = ("system", "light", "dark")
UI_FONTS = ["Segoe UI", "Yu Gothic UI", "Meiryo UI", "Noto Sans CJK JP", "Noto Sans", "sans-serif"]
MONO_FONTS = "'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace"


class ThemeManager(QObject):
    """Holds the current mode, applies palette + stylesheet, and notifies views on change."""

    changed = Signal(str)  # resolved mode: "light" | "dark"

    def __init__(self):
        super().__init__()
        self._mode = "system"
        self._resolved = "light"
        self._app: Optional[QApplication] = None
        self._hints_connected = False

    # ------------------------------------------------------------------ state
    @property
    def mode(self) -> str:
        """Configured mode: system / light / dark."""
        return self._mode

    @property
    def resolved(self) -> str:
        """Mode in effect after resolving ``system``."""
        return self._resolved

    @property
    def tokens(self) -> dict:
        return DARK if self._resolved == "dark" else LIGHT

    def __getitem__(self, key: str) -> str:
        return self.tokens[key]

    def series(self, k: int) -> str:
        colors = SERIES[self._resolved]
        return colors[k % len(colors)]

    def series_pen(self, k: int, width: float = 2.0):
        """pyqtgraph pen for series k (dashed after the palette has been cycled once)."""
        import pyqtgraph as pg

        colors = SERIES[self._resolved]
        style = Qt.PenStyle.SolidLine if k < len(colors) else Qt.PenStyle.DashLine
        return pg.mkPen(self.series(k), width=width, style=style)

    # ------------------------------------------------------------------ apply
    def load_settings(self) -> str:
        mode = str(QSettings().value("ui/theme", "system"))
        self._mode = mode if mode in MODES else "system"
        return self._mode

    def set_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown theme mode {mode!r}")
        self._mode = mode
        if persist:
            QSettings().setValue("ui/theme", mode)
        if self._app is not None:
            self.apply(self._app)

    def _system_is_dark(self) -> bool:
        app = self._app
        if app is None:
            return False
        try:
            return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:  # noqa: BLE001  (older Qt)
            return False

    def apply(self, app: QApplication) -> str:
        """Apply the configured mode to the application; returns the resolved mode."""
        self._app = app
        if not self._hints_connected:
            try:
                app.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
                self._hints_connected = True
            except Exception:  # noqa: BLE001
                pass
        resolved = "dark" if (self._mode == "dark" or (self._mode == "system" and self._system_is_dark())) else "light"
        self._resolved = resolved
        t = self.tokens
        app.setStyle("Fusion")
        font = QFont()
        font.setFamilies(UI_FONTS)
        if font.pointSize() < 9:
            font.setPointSize(9)
        app.setFont(font)
        app.setPalette(build_palette(t))
        app.setStyleSheet(build_qss(t))
        try:
            import pyqtgraph as pg

            pg.setConfigOptions(background=t["bg"], foreground=t["text"], antialias=True)
            for w in app.allWidgets():
                if isinstance(w, pg.PlotWidget):
                    restyle_plot(w, t)
        except Exception:  # noqa: BLE001  (pyqtgraph optional at import time)
            pass
        self.changed.emit(resolved)
        return resolved

    def _on_system_scheme_changed(self, *_) -> None:
        if self._mode == "system" and self._app is not None:
            self.apply(self._app)


_MANAGER: Optional[ThemeManager] = None


def manager() -> ThemeManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ThemeManager()
    return _MANAGER


def tokens() -> dict:
    return manager().tokens


def series(k: int) -> str:
    return manager().series(k)


def series_pen(k: int, width: float = 2.0):
    return manager().series_pen(k, width)


# ---------------------------------------------------------------------- generated indicator images
def _chevron_png(path: Path, color: str, direction: str, size: int, scale: int) -> None:
    px = size * scale
    img = QImage(px, px, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6 * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    w = px * 0.30
    h = px * 0.16
    cx, cy = px / 2, px / 2
    if direction == "down":
        pts = [QPointF(cx - w, cy - h), QPointF(cx, cy + h), QPointF(cx + w, cy - h)]
    elif direction == "up":
        pts = [QPointF(cx - w, cy + h), QPointF(cx, cy - h), QPointF(cx + w, cy + h)]
    else:  # right
        pts = [QPointF(cx - h, cy - w), QPointF(cx + h, cy), QPointF(cx - h, cy + w)]
    p.drawPolyline(pts)
    p.end()
    img.setDevicePixelRatio(scale)
    img.save(str(path))


def indicator_images(t: dict) -> dict[str, str]:
    """Write chevron images for the current tokens into the temp folder; returns stylesheet url() paths."""
    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)) / "cirplas-theme"
    out: dict[str, str] = {}
    try:
        base.mkdir(parents=True, exist_ok=True)
        for key, color in (("normal", t["text_muted"]), ("disabled", t["text_disabled"])):
            for direction in ("down", "up", "right"):
                name = f"chevron_{direction}_{color.lstrip('#').lower()}"
                for scale in (1, 2):
                    path = base / (name + ("@2x" if scale == 2 else "") + ".png")
                    if not path.exists():
                        _chevron_png(path, color, direction, 10, scale)
                out[f"{direction}_{key}"] = (base / (name + ".png")).as_posix()
    except Exception:  # noqa: BLE001  (read-only temp: fall back to the style's own arrows)
        return {}
    return out


# ---------------------------------------------------------------------- palette / stylesheet
def build_palette(t: dict) -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    for role, key in [
        (R.Window, "bg"), (R.Base, "bg"), (R.AlternateBase, "surface"), (R.WindowText, "text"), (R.Text, "text"),
        (R.Button, "surface"), (R.ButtonText, "text"), (R.Highlight, "accent"), (R.HighlightedText, "accent_fg"),
        (R.ToolTipBase, "surface"), (R.ToolTipText, "text"), (R.PlaceholderText, "text_muted"), (R.Link, "accent_text"),
        (R.Light, "surface_2"), (R.Midlight, "surface_2"), (R.Mid, "border"), (R.Dark, "border"), (R.Shadow, "border"),
    ]:
        p.setColor(role, QColor(t[key]))
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["text_disabled"]))
    p.setColor(QPalette.ColorGroup.Disabled, R.Base, QColor(t["surface"]))
    p.setColor(QPalette.ColorGroup.Disabled, R.Button, QColor(t["surface"]))
    return p


def build_qss(t: dict) -> str:
    img = indicator_images(t)

    def arrow(sub: str, direction: str, size: int) -> str:
        if f"{direction}_normal" not in img:
            return ""
        return (f'{sub} {{ image: url("{img[f"{direction}_normal"]}"); width: {size}px; height: {size}px; }}\n'
                f'{sub}:disabled {{ image: url("{img[f"{direction}_disabled"]}"); }}\n')

    arrows = (arrow("QComboBox::down-arrow", "down", 10) + arrow("QSpinBox::up-arrow, QDoubleSpinBox::up-arrow", "up", 8)
              + arrow("QSpinBox::down-arrow, QDoubleSpinBox::down-arrow", "down", 8) + arrow("QMenu::right-arrow", "right", 10)
              + arrow("QToolButton::menu-indicator", "down", 8))
    spin = ("QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 16px; border: none; background: transparent; }\n"
            "QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 16px; border: none; background: transparent; }\n"
            if img else "")
    dropdown = ("QComboBox::drop-down { border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }\n"
                if img else "")
    return f"""
QWidget {{ color: {t['text']}; }}
QMainWindow, QDialog, QMessageBox, QFileDialog {{ background: {t['bg']}; }}
QWidget[surface="true"] {{ background: {t['surface']}; }}
QWidget[panel="true"] {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {t['text_muted']}; }}
QLabel[role="error"] {{ color: {t['error']}; }}
QLabel[role="heading"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="section"] {{ color: {t['text_muted']}; font-weight: 600; letter-spacing: 0.5px; }}

QMenuBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 6px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['surface_2']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['surface_2']}; }}
QMenu::item:disabled {{ color: {t['text_disabled']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; }}

QDockWidget {{ color: {t['text_muted']}; font-weight: 600; }}
QDockWidget::title {{ background: {t['surface']}; padding: 7px 12px; border-bottom: 1px solid {t['border']}; text-align: left; }}
QDockWidget::close-button, QDockWidget::float-button {{ border: none; background: transparent; }}
QDockWidget > QWidget {{ background: {t['surface']}; }}
QMainWindow::separator {{ background: {t['border']}; width: 1px; height: 1px; }}
QMainWindow::separator:hover {{ background: {t['accent']}; }}

QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['bg']}; top: -1px; }}
QTabWidget[flat="true"]::pane {{ border: none; border-top: 1px solid {t['border']}; border-radius: 0; background: transparent; }}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {t['text_muted']}; padding: 7px 14px; margin-right: 2px; margin-bottom: 1px;
               border: 1px solid transparent; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: {t['bg']}; color: {t['text']}; border-color: {t['border']}; margin-bottom: 0; }}
QTabWidget[flat="true"] QTabBar::tab:selected {{ background: transparent; border-color: transparent; border-bottom: 2px solid {t['accent']}; border-radius: 0; margin-bottom: 0; }}
QTabBar::tab:hover:!selected {{ background: {t['surface_2']}; color: {t['text']}; }}
QTabBar::tab:disabled {{ color: {t['text_disabled']}; }}

QGroupBox {{ border: 1px solid {t['border']}; border-radius: 8px; margin-top: 16px; padding: 10px 6px 6px 6px; background: {t['bg']}; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px; color: {t['text_muted']}; font-weight: 600; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QAbstractSpinBox {{
    background: {t['surface_2']}; border: 1px solid {t['border']}; border-radius: 5px; padding: 3px 6px;
    selection-background-color: {t['accent']}; selection-color: {t['accent_fg']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {t['focus']}; padding: 2px 5px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
    color: {t['text_disabled']}; background: {t['surface']}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t['error']}; }}
QPlainTextEdit[mono="true"], QTextEdit[mono="true"] {{ font-family: {MONO_FONTS}; font-size: 11px; }}
QPlainTextEdit[flat="true"] {{ background: {t['bg']}; }}
{dropdown}QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 4px;
    selection-background-color: {t['surface_2']}; selection-color: {t['text']}; outline: 0; }}
{spin}{arrows}
QPushButton {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t['surface_2']}; }}
QPushButton:pressed {{ border-color: {t['accent']}; }}
QPushButton:disabled {{ color: {t['text_disabled']}; background: {t['surface']}; border-color: {t['border']}; }}
QPushButton:focus {{ border: 1px solid {t['focus']}; }}
QPushButton[primary="true"] {{ background: {t['accent_strong']}; color: {t['accent_fg']}; border: 1px solid {t['accent_strong']}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton[primary="true"]:disabled {{ background: {t['surface_2']}; color: {t['text_disabled']}; border-color: {t['border']}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 8px; }}
QToolButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: center right; right: 4px; }}
QToolButton:hover {{ background: {t['surface_2']}; border-color: {t['border']}; }}
QToolButton:checked {{ background: {t['surface_2']}; border-color: {t['accent']}; }}

QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t['text_disabled']}; }}

QProgressBar {{ border: none; background: {t['surface_2']}; border-radius: 3px; min-height: 6px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t['text_disabled']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['text_disabled']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; background: transparent; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {t['surface_2']}; color: {t['text_muted']}; border: none; border-bottom: 1px solid {t['border']};
    border-right: 1px solid {t['border']}; padding: 4px 6px; font-weight: 600; }}
QTableView, QTableWidget, QListWidget, QListView, QTreeView, QTreeWidget {{ background: {t['bg']}; alternate-background-color: {t['surface']};
    border: 1px solid {t['border']}; border-radius: 6px; gridline-color: {t['border']};
    selection-background-color: {t['selection']}; selection-color: {t['text']}; outline: 0; }}
QTableView::item:selected, QListWidget::item:selected, QTreeView::item:selected {{ background: {t['selection']}; color: {t['text']}; }}
QTableView::item:focus, QListWidget::item:focus {{ border: none; outline: 0; }}
QTableCornerButton::section {{ background: {t['surface_2']}; border: none; border-bottom: 1px solid {t['border']}; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}

QStatusBar {{ background: {t['surface']}; border-top: 1px solid {t['border']}; color: {t['text_muted']}; }}
QStatusBar::item {{ border: none; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 5px 7px; border-radius: 5px; }}
QGraphicsView {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['bg']}; }}
"""


# ---------------------------------------------------------------------- pyqtgraph helpers
def restyle_plot(w, t: Optional[dict] = None) -> None:
    """Apply the theme to an existing pyqtgraph PlotWidget (background, axes, title, legend)."""
    import pyqtgraph as pg

    t = t or tokens()
    try:
        w.setBackground(t["bg"])
        pi = w.getPlotItem()
    except Exception:  # noqa: BLE001
        return
    for name in ("left", "bottom", "right", "top"):
        try:
            ax = pi.getAxis(name)
            ax.setPen(pg.mkPen(t["border"]))
            ax.setTextPen(pg.mkPen(t["text_muted"]))
            if ax.labelText:
                ax.setLabel(text=ax.labelText, units=ax.labelUnits, color=t["text_muted"])
        except Exception:  # noqa: BLE001
            pass
    try:
        if pi.titleLabel is not None and pi.titleLabel.text:
            pi.titleLabel.setText(pi.titleLabel.text, color=t["text"])
    except Exception:  # noqa: BLE001
        pass
    try:
        if pi.legend is not None:
            pi.legend.setLabelTextColor(t["text"])
            pi.legend.setBrush(pg.mkBrush(t["surface"]))
            pi.legend.setPen(pg.mkPen(t["border"]))
    except Exception:  # noqa: BLE001
        pass


def make_plot(title: Optional[str] = None, **kwargs):
    """Create a PlotWidget already styled with the current tokens."""
    import pyqtgraph as pg

    w = pg.PlotWidget(title=title, **kwargs)
    restyle_plot(w)
    return w


def set_role(widget, role: str) -> None:
    """Tag a widget for the stylesheet (role selectors) and repolish it."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_flag(widget, name: str, value: bool = True) -> None:
    widget.setProperty(name, "true" if value else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
