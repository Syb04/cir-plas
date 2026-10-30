"""Interactive schematic editor for the external circuit (Qucs-S style).

Components (R, L, C, RF source, DC source, ground, net label) are placed from the tool bar, moved by dragging,
rotated with Ctrl+R and wired with the wire tool (orthogonal segments, click to add a corner, finish on a pin /
wire or with a double click). The plasma block carries one pin per driven electrode (W, F, T) and is generated
from the geometry; it can be moved but not deleted.

The schematic is stored in ``circuit.schematic`` (grid units); every edit is one undoable model change. While the
project still uses the port-chain description, the editor shows the equivalent schematic, and the first edit
switches the project to ``circuit.mode = "schematic"``.
"""

from __future__ import annotations

import copy
import math
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QGraphicsEllipseItem, QGraphicsItem, QGraphicsObject,
                               QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from ...core.netlist.user_elements import format_eng
from ...core.project import CircuitSpec, Schematic
from ...core.schematic import ELECTRODE_PIN_Y, default_schematic, extract, local_pins, on_segment, rotate, schematic_from_ports
from .. import theme
from ..project_model import ProjectModel
from .paramform import SciSpin, _mark_invalid

G = 10.0  # pixels per grid unit
UNITS = {"R": "Ω", "L": "H", "C": "F", "Vdc": "V"}
TYPE_NAMES = {"R": "抵抗", "L": "インダクタ", "C": "コンデンサ", "Vrf": "RF 電源", "Vdc": "DC 電源", "GND": "接地", "Label": "ネットラベル",
              "Plasma": "プラズマ・電極"}
PLACEABLE = ("R", "L", "C", "Vrf", "Vdc", "GND", "Label")
PREFIX = {"R": "R", "L": "L", "C": "C", "Vdc": "V", "Vrf": "V", "GND": "GND", "Label": "N", "Plasma": "PLASMA"}
DEFAULTS = {"R": 50.0, "L": 100e-9, "C": 100e-12, "Vdc": 0.0}
TOOLS = [("select", "選択", "Esc"), ("wire", "ワイヤ", "W"), ("R", "抵抗", ""), ("L", "インダクタ", ""), ("C", "コンデンサ", ""),
         ("Vrf", "RF 電源", ""), ("Vdc", "DC 電源", ""), ("GND", "接地", ""), ("Label", "ネットラベル", "")]


# ---------------------------------------------------------------------------- geometry helpers (dict based)
def pin_points(c: dict, electrodes: list[str]) -> list[tuple[str, tuple[int, int]]]:
    rot = 0 if c["type"] == "Plasma" else int(c.get("rot", 0))
    out = []
    for name, dx, dy in local_pins(c["type"], electrodes):
        rx, ry = rotate(dx, dy, rot)
        out.append((name, (int(c["x"]) + rx, int(c["y"]) + ry)))
    return out


def clean_points(pts) -> list[tuple[int, int]]:
    """Drop repeated points and straight-through vertices."""
    out: list[tuple[int, int]] = []
    for p in pts:
        p = (int(p[0]), int(p[1]))
        if out and out[-1] == p:
            continue
        out.append(p)
    k = 1
    while k < len(out) - 1:
        a, b, c = out[k - 1], out[k], out[k + 1]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        dot = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1])
        if cross == 0 and dot >= 0:
            del out[k]
        else:
            k += 1
    return out


def move_endpoint(pts: list[tuple[int, int]], end: int, new: tuple[int, int]) -> list[tuple[int, int]]:
    """Move one end of an orthogonal polyline and keep it orthogonal by inserting an elbow."""
    pts = [tuple(p) for p in pts]
    if len(pts) < 2:
        return pts
    if end == 0:
        return list(reversed(move_endpoint(list(reversed(pts)), -1, new)))
    p, q = pts[-1], pts[-2]
    horizontal = p[1] == q[1] and p[0] != q[0]
    elbow = (new[0], q[1]) if horizontal else (q[0], new[1])
    return clean_points(pts[:-1] + [elbow, new])


def rubber_band(wires, moves: dict, fixed: set[int]) -> list[list[tuple[int, int]]]:
    """Wire ends sitting on a moved pin follow it (wires listed in ``fixed`` are left alone)."""
    out = []
    for k, w in enumerate(wires):
        w = [tuple(p) for p in w]
        if k in fixed or not moves:
            out.append(w)
            continue
        a, b = w[0], w[-1]
        if a in moves and b in moves and (moves[a][0] - a[0], moves[a][1] - a[1]) == (moves[b][0] - b[0], moves[b][1] - b[1]):
            d = (moves[a][0] - a[0], moves[a][1] - a[1])
            out.append([(x + d[0], y + d[1]) for x, y in w])
            continue
        if a in moves:
            w = move_endpoint(w, 0, moves[a])
        if b in moves:
            w = move_endpoint(w, -1, moves[b])
        out.append(clean_points(w))
    return out


def next_id(comps: list[dict], ctype: str) -> str:
    prefix = PREFIX[ctype]
    used = {c["id"].upper() for c in comps}
    k = 1
    while f"{prefix}{k}".upper() in used:
        k += 1
    return f"{prefix}{k}"


def new_component(comps: list[dict], ctype: str, x: int, y: int, rot: int = 0) -> dict:
    c = {"id": next_id(comps, ctype), "type": ctype, "x": int(x), "y": int(y), "rot": int(rot) if ctype not in ("GND", "Label") else 0,
         "value": DEFAULTS.get(ctype), "tones": [], "port": None, "label": None}
    if ctype == "Vrf":
        c["tones"] = [{"f": 13.56e6, "v": 100.0, "phase_deg": 0.0, "power": None}]
    if ctype == "Label":
        c["label"] = f"net{c['id'][1:]}"
    return c


def value_text(c: dict) -> str:
    t = c["type"]
    if t in UNITS:
        return format_eng(c.get("value"), UNITS[t])
    if t == "Vrf":
        tones = c.get("tones") or []
        parts = [f"{format_eng(s['f'], 'Hz')} {format_eng(s['v'], 'V')}" + (f" ({s['power']:.4g} W)" if s.get("power") else "") for s in tones[:2]]
        return " + ".join(parts) + (" …" if len(tones) > 2 else "")
    return ""


def eng_text(v: float) -> str:
    """Compact engineering notation accepted by SciSpin.parse (10n, 2.2u, 50, 1.5k)."""
    if v == 0:
        return "0"
    for scale, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if abs(v) >= scale * 0.9999:
            return f"{v / scale:.6g}{suffix}"
    return f"{v:.6g}"


def _font(size: int = 9, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(theme.UI_FONTS)
    f.setPointSize(size)
    f.setBold(bold)
    return f


# ---------------------------------------------------------------------------- symbols
def paint_symbol(p: QPainter, ctype: str, pen: QPen, fill: QColor, electrodes: Optional[list[str]] = None, text: str = "") -> None:
    """Draw a symbol in local (unrotated) coordinates; pins as in core.schematic.local_pins."""
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    L = 3 * G
    if ctype == "R":
        p.drawLine(QPointF(-L, 0), QPointF(-1.5 * G, 0))
        p.drawLine(QPointF(1.5 * G, 0), QPointF(L, 0))
        p.setBrush(QBrush(fill))
        p.drawRect(QRectF(-1.5 * G, -0.55 * G, 3 * G, 1.1 * G))
    elif ctype == "L":
        p.drawLine(QPointF(-L, 0), QPointF(-1.6 * G, 0))
        p.drawLine(QPointF(1.6 * G, 0), QPointF(L, 0))
        w = 3.2 * G / 4
        path = QPainterPath(QPointF(-1.6 * G, 0))
        for i in range(4):
            path.arcTo(QRectF(-1.6 * G + i * w, -w / 2, w, w), 180, -180)
        p.drawPath(path)
    elif ctype == "C":
        p.drawLine(QPointF(-L, 0), QPointF(-0.35 * G, 0))
        p.drawLine(QPointF(0.35 * G, 0), QPointF(L, 0))
        p.drawLine(QPointF(-0.35 * G, -1.1 * G), QPointF(-0.35 * G, 1.1 * G))
        p.drawLine(QPointF(0.35 * G, -1.1 * G), QPointF(0.35 * G, 1.1 * G))
    elif ctype in ("Vrf", "Vdc"):
        r = 1.3 * G
        p.drawLine(QPointF(0, -L), QPointF(0, -r))
        p.drawLine(QPointF(0, r), QPointF(0, L))
        p.setBrush(QBrush(fill))
        p.drawEllipse(QPointF(0, 0), r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if ctype == "Vrf":
            path = QPainterPath(QPointF(-0.8 * G, 0))
            for k in range(1, 17):
                x = -0.8 * G + 1.6 * G * k / 16
                path.lineTo(QPointF(x, -0.5 * G * math.sin(2 * math.pi * k / 16)))
            p.drawPath(path)
        else:
            p.drawLine(QPointF(-0.35 * G, -0.55 * G), QPointF(0.35 * G, -0.55 * G))
            p.drawLine(QPointF(0, -0.9 * G), QPointF(0, -0.2 * G))
            p.drawLine(QPointF(-0.35 * G, 0.6 * G), QPointF(0.35 * G, 0.6 * G))
        p.drawLine(QPointF(0.6 * G, -2.2 * G), QPointF(1.2 * G, -2.2 * G))  # "+" next to the positive pin
        p.drawLine(QPointF(0.9 * G, -2.5 * G), QPointF(0.9 * G, -1.9 * G))
    elif ctype == "GND":
        p.drawLine(QPointF(0, 0), QPointF(0, 0.8 * G))
        for i, w in enumerate((1.2 * G, 0.75 * G, 0.3 * G)):
            p.drawLine(QPointF(-w, 0.8 * G + i * 0.45 * G), QPointF(w, 0.8 * G + i * 0.45 * G))
    elif ctype == "Label":
        width = max(2.0 * G, 0.75 * G * len(text))
        path = QPainterPath(QPointF(0, 0))
        path.lineTo(QPointF(0.6 * G, -0.6 * G))
        path.lineTo(QPointF(0.6 * G + width, -0.6 * G))
        path.lineTo(QPointF(0.6 * G + width, -1.9 * G))
        path.lineTo(QPointF(0.6 * G, -1.9 * G))
        path.lineTo(QPointF(0.6 * G, -0.6 * G))
        p.drawPath(path)
        if text:
            p.setFont(_font(8, True))
            p.drawText(QRectF(0.75 * G, -1.95 * G, width + 2 * G, 1.4 * G), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
    elif ctype == "Plasma":
        els = electrodes or ["W"]
        for e in els:
            y = ELECTRODE_PIN_Y[e] * G
            p.drawLine(QPointF(0, y), QPointF(2 * G, y))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(QRectF(2 * G, -8 * G, 12 * G, 16 * G), 8, 8)
        p.setFont(_font(8, True))
        for e in els:
            y = ELECTRODE_PIN_Y[e] * G
            p.drawText(QRectF(2.4 * G, y - 0.8 * G, 2 * G, 1.6 * G), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, e)
        p.drawText(QRectF(4.5 * G, -7.5 * G, 9 * G, 2 * G), Qt.AlignmentFlag.AlignLeft, "プラズマ")
        p.setFont(_font(7))
        lines = ["電極ラダー（自動生成）"] + [{"W": "W: ウェハ電極", "F": "F: FR 電極", "T": "T: 上部電極"}[e] for e in els]
        for i, line in enumerate(lines):
            p.drawText(QRectF(4.5 * G, (-5.3 + 1.5 * i) * G, 9.4 * G, 1.5 * G), Qt.AlignmentFlag.AlignLeft, line)


def symbol_rect(ctype: str, text: str = "") -> QRectF:
    if ctype in ("R", "L", "C"):
        return QRectF(-3 * G - 2, -1.4 * G, 6 * G + 4, 2.8 * G)
    if ctype in ("Vrf", "Vdc"):
        return QRectF(-1.6 * G, -3 * G - 2, 3.2 * G, 6 * G + 4)
    if ctype == "GND":
        return QRectF(-1.4 * G, -0.4 * G, 2.8 * G, 2.2 * G)
    if ctype == "Label":
        return QRectF(-0.4 * G, -2.2 * G, 1.6 * G + max(2.0 * G, 0.75 * G * len(text)) + 0.4 * G, 2.6 * G)
    return QRectF(-0.4 * G, -8.4 * G, 14.8 * G, 16.8 * G)


def tool_icon(tool: str, size: int = 28) -> QIcon:
    t = theme.tokens()
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(t["text"]), 1.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    if tool == "select":
        path = QPainterPath(QPointF(9, 6))
        for x, y in ((9, 21), (13, 17), (16, 23), (18.5, 22), (15.5, 16), (21, 16)):
            path.lineTo(QPointF(x, y))
        path.closeSubpath()
        p.setBrush(QBrush(QColor(t["text"])))
        p.drawPath(path)
    elif tool == "wire":
        pen.setWidthF(1.8)
        p.setPen(pen)
        p.drawPolyline([QPointF(5, 8), QPointF(14, 8), QPointF(14, 20), QPointF(23, 20)])
        p.setBrush(QBrush(QColor(t["accent"])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(5, 8), 2.2, 2.2)
        p.drawEllipse(QPointF(23, 20), 2.2, 2.2)
    else:
        s = size / (7.0 * G)
        if tool == "GND":
            p.translate(size / 2, size / 2 - 0.9 * G * s * 1.6)
            s *= 1.6
        elif tool == "Label":
            p.translate(size / 2 - size / 3, size / 2 + size / 5)
            s *= 1.3
        else:
            p.translate(size / 2, size / 2)
        p.scale(s, s)
        paint_symbol(p, tool, QPen(QColor(t["text"]), 1.3 / s), QColor(t["surface"]), text="ab" if tool == "Label" else "")
    p.end()
    return QIcon(pm)


def _inverse_rotate(x: float, y: float, rot: int) -> tuple[float, float]:
    for _ in range(((360 - rot) // 90) % 4):
        x, y = -y, x
    return x, y


# ---------------------------------------------------------------------------- items
class CompItem(QGraphicsObject):
    def __init__(self, editor: "CircuitEditor", comp: dict, ghost: bool = False):
        super().__init__()
        self.editor = editor
        self.comp = comp
        self.ghost = ghost
        self.text = (comp.get("label") or "") if comp["type"] == "Label" else ""
        self._hover = False
        self.setPos(comp["x"] * G, comp["y"] * G)
        if comp["type"] not in ("Plasma", "Label", "GND"):
            self.setRotation(int(comp.get("rot", 0)))
        if ghost:
            self.setOpacity(0.55)
            self.setZValue(20)
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        else:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
            self.setZValue(5)
            self.setAcceptHoverEvents(True)
        self._labels()

    def _labels(self) -> None:
        c = self.comp
        if c["type"] in ("GND", "Label", "Plasma"):
            return
        t = theme.tokens()
        rot = int(c.get("rot", 0))
        vertical = (rot in (90, 270)) if c["type"] in ("R", "L", "C") else (rot in (0, 180))
        name = QGraphicsSimpleTextItem(c["id"], self)
        name.setFont(_font(8, True))
        name.setBrush(QBrush(QColor(t["text"])))
        val = QGraphicsSimpleTextItem(value_text(c), self)
        val.setFont(_font(8))
        val.setBrush(QBrush(QColor(t["text_muted"])))
        nb, vb = name.boundingRect(), val.boundingRect()
        if vertical:
            offs = ((1.8 * G, -nb.height()), (1.8 * G, 0.0))
        else:
            offs = ((-nb.width() / 2, -1.5 * G - nb.height()), (-vb.width() / 2, 1.3 * G))
        for item, (sx, sy) in zip((name, val), offs):
            item.setPos(*_inverse_rotate(sx, sy, rot))
            item.setRotation(-rot)

    def boundingRect(self) -> QRectF:
        return symbol_rect(self.comp["type"], self.text)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(symbol_rect(self.comp["type"], self.text))
        return path

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()

    def itemChange(self, change, value):
        if not self.ghost:
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
                return QPointF(round(value.x() / G) * G, round(value.y() / G) * G)
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
                self.editor.on_item_moved()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        self.editor.focus_properties(self.comp["id"])

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = theme.tokens()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = self.isSelected()
        if sel or self._hover:
            halo = QColor(t["accent"])
            halo.setAlpha(36 if sel else 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawRoundedRect(symbol_rect(self.comp["type"], self.text), 6, 6)
        pen = QPen(QColor(t["accent"] if (sel or self.ghost) else t["text"]), 2.0 if sel else 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        paint_symbol(painter, self.comp["type"], pen, QColor(t["surface"]), self.editor.electrodes, self.text)


class WireItem(QGraphicsPathItem):
    def __init__(self, editor: "CircuitEditor", index: int, points):
        super().__init__()
        self.editor = editor
        self.index = index
        self.points = [tuple(p) for p in points]
        self.set_points(self.points)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(3)

    def set_points(self, pts) -> None:
        path = QPainterPath(QPointF(pts[0][0] * G, pts[0][1] * G))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x * G, y * G))
        self.setPath(path)

    def shape(self) -> QPainterPath:
        s = QPainterPathStroker()
        s.setWidth(8)
        return s.createStroke(self.path())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(round(value.x() / G) * G, round(value.y() / G) * G)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.editor.on_item_moved()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = theme.tokens()
        pen = QPen(QColor(t["accent"] if self.isSelected() else t["info"]), 2.2 if self.isSelected() else 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())


class SchView(QGraphicsView):
    def __init__(self, scene: "SchScene", parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pan: Optional[QPointF] = None
        self._fitted = False

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if 0.2 < self.transform().m11() * factor < 6.0:
            self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            d = event.position() - self._pan
            self._pan = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.viewport().unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        t = theme.tokens()
        painter.fillRect(rect, QColor(t["bg"]))
        if self.transform().m11() < 0.45:
            return
        painter.setPen(QPen(QColor(t["border"]), 1.4))
        x0 = math.floor(rect.left() / G) * G
        y0 = math.floor(rect.top() / G) * G
        pts = []
        x = x0
        while x < rect.right():
            y = y0
            while y < rect.bottom():
                pts.append(QPointF(x, y))
                y += G
            x += G
        painter.drawPoints(pts)

    def fit(self) -> None:
        rect = self.scene().itemsBoundingRect().adjusted(-4 * G, -4 * G, 4 * G, 4 * G)
        if rect.isEmpty():
            return
        self.setSceneRect(rect.adjusted(-80 * G, -80 * G, 80 * G, 80 * G))
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.6:
            self.resetTransform()
            self.scale(1.6, 1.6)
            self.centerOn(rect.center())
        self._fitted = True

    def showEvent(self, event):
        super().showEvent(event)
        if not self._fitted:
            self.fit()


class SchScene(QGraphicsScene):
    def __init__(self, editor: "CircuitEditor"):
        super().__init__(editor)
        self.editor = editor

    def mousePressEvent(self, event):
        if self.editor.handle_press(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.editor.handle_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.editor.handle_release(event)

    def mouseDoubleClickEvent(self, event):
        if self.editor.handle_double_click(event):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------- property panel
class TonesTable(QTableWidget):
    def __init__(self, on_change: Callable[[list[dict]], None], parent=None):
        super().__init__(0, 4, parent)
        self.on_change = on_change
        self.tones: list[dict] = []
        self.setHorizontalHeaderLabels(["f [MHz]", "振幅 [V]", "位相 [deg]", "電力 [W]"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(110)
        self._blocked = False
        self.cellChanged.connect(self._changed)

    def set_tones(self, tones: list[dict]) -> None:
        self._blocked = True
        self.tones = copy.deepcopy(tones)
        self.setRowCount(len(tones))
        for r, s in enumerate(tones):
            self.setItem(r, 0, QTableWidgetItem(f"{s['f'] / 1e6:.6g}"))
            self.setItem(r, 1, QTableWidgetItem(f"{s['v']:.6g}"))
            self.setItem(r, 2, QTableWidgetItem(f"{s.get('phase_deg', 0.0):.6g}"))
            self.setItem(r, 3, QTableWidgetItem("" if s.get("power") is None else f"{s['power']:.6g}"))
        self._blocked = False

    def _changed(self, row: int, col: int) -> None:
        if self._blocked:
            return
        text = self.item(row, col).text().strip()
        key = ["f", "v", "phase_deg", "power"][col]
        if col == 3 and text == "":
            val = None
        else:
            try:
                val = SciSpin.parse(text)
            except ValueError:
                return
            if key == "f":
                val *= 1e6
            if (key in ("f", "power") and val <= 0) or (key == "v" and val < 0):
                return
        self.tones[row][key] = val
        self.on_change(copy.deepcopy(self.tones))


DESCRIPTIONS = {
    "R": "抵抗。外部回路の損失は電力収支に含めて集計されます。",
    "L": "インダクタ。",
    "C": "コンデンサ。電極との間に直列に入ると直流が遮断され、その電極に自己バイアスが立ちます。",
    "Vrf": "RF 電圧源（複数行で多周波）。+ 側のピンが上。電力 [W] を入れると供給電力が一定になるよう振幅を反復します。",
    "Vdc": "直流電圧源。+ 側のピンが上。0 V にすると短絡（電流プローブ）として使えます。",
    "GND": "接地（ノード 0）。",
    "Label": "ネットラベル。同じ名前のラベルどうしは接続され、追加ネットリストからは u_<名前> で参照できます。",
    "Plasma": "物理モデルから生成される電極・プラズマのラダー回路。ピンは幾何で定義された電極（W、独立 FR なら F、駆動上部電極なら T）で、"
              "電極電流はピンのところで測ります。移動のみ可能です。",
}


class PropertyPanel(QWidget):
    def __init__(self, editor: "CircuitEditor", parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setProperty("surface", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.title = QLabel("")
        self.title.setProperty("role", "heading")
        self.title.setWordWrap(True)
        self.desc = QLabel("")
        self.desc.setProperty("role", "muted")
        self.desc.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.desc)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(0, 6, 0, 0)
        lay.addWidget(self.body)
        self.buttons = QHBoxLayout()
        lay.addLayout(self.buttons)
        lay.addStretch(1)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.notes)
        self.current: Optional[str] = None
        self.first_field: Optional[QWidget] = None
        self.value_edit: Optional[SciSpin] = None
        self.tones_table: Optional[TonesTable] = None
        self.nets_label: Optional[QLabel] = None

    def _reset(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.first_field = self.value_edit = self.tones_table = self.nets_label = None
        self.notes.setText("")

    def _button(self, text: str, slot) -> None:
        b = QPushButton(text)
        b.clicked.connect(slot)
        self.buttons.addWidget(b)

    def show_none(self, summary: str, notes_html: str) -> None:
        self._reset()
        self.current = None
        self.title.setText("外部回路（回路図）")
        self.desc.setText(summary)
        self.notes.setText(notes_html)

    def show_multi(self, n_comp: int, n_wire: int) -> None:
        self._reset()
        self.current = None
        self.title.setText(f"部品 {n_comp} 個・ワイヤ {n_wire} 本を選択中")
        self.desc.setText("ドラッグで移動、Ctrl+R で回転、Delete で削除します。")
        self._button("回転 (Ctrl+R)", self.editor.rotate_selection)
        self._button("削除 (Del)", self.editor.delete_selection)

    def show_wire(self, index: int) -> None:
        self._reset()
        self.current = f"wire:{index}"
        self.title.setText(f"ワイヤ {index + 1}")
        self.desc.setText("端点がピンやほかのワイヤ上にあると接続されます。ドラッグで移動、Delete で削除します。")
        self._button("削除 (Del)", self.editor.delete_selection)

    def show_component(self, c: dict, electrodes: list[str], nets: dict[str, str]) -> None:
        key = f"comp:{c['id']}:{c['type']}"
        if self.current == key and self.nets_label is not None:
            self._refresh(c, nets)
            return
        self._reset()
        self.current = key
        cid, t = c["id"], c["type"]
        self.title.setText(f"{TYPE_NAMES[t]} {cid}")
        self.desc.setText(DESCRIPTIONS[t])
        if t not in ("Plasma", "GND"):
            name = QLineEdit(cid)
            name.editingFinished.connect(lambda: self._rename(cid, name))
            self.form.addRow("名前", name)
            self.first_field = name
        if t in UNITS:
            val = SciSpin()
            val.setText(eng_text(float(c.get("value") or 0.0)))
            val.setToolTip("工学表記も使えます（例: 2.2n, 300p, 1k）")
            val.editingFinished.connect(lambda: self._set_value(cid, val, t))
            self.form.addRow(f"値 [{UNITS[t]}]", val)
            self.value_edit = val
            self.first_field = val
        if t == "Vrf":
            table = TonesTable(lambda tones: self.editor.update_component(cid, tones=tones))
            table.set_tones(c.get("tones") or [])
            self.form.addRow(table)
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            b_add = QPushButton("周波数成分を追加")
            b_del = QPushButton("選択行を削除")
            b_add.clicked.connect(lambda: self.editor.update_component(cid, tones=list(table.tones) + [{"f": 2e6, "v": 100.0, "phase_deg": 0.0, "power": None}]))
            b_del.clicked.connect(lambda: self._delete_tone(cid, table))
            hl.addWidget(b_add)
            hl.addWidget(b_del)
            self.form.addRow(row)
            port = QComboBox()
            port.addItem("自動（最寄りの電極）", None)
            for e in electrodes:
                port.addItem(f"電極 {e}", e)
            port.setCurrentIndex(max(0, port.findData(c.get("port"))))
            port.currentIndexChanged.connect(lambda i: self.editor.update_component(cid, port=port.itemData(i)))
            port.setToolTip("電源電力・ポート量をどの電極の結果として集計するか")
            self.form.addRow("集計先の電極", port)
            self.tones_table = table
            self.first_field = table
        if t == "Label":
            lab = QLineEdit(c.get("label") or "")
            lab.editingFinished.connect(lambda: self.editor.update_component(cid, label=lab.text().strip()) if lab.text().strip() else None)
            self.form.addRow("ラベル名", lab)
            self.first_field = lab
        self.nets_label = QLabel("")
        self.nets_label.setProperty("role", "muted")
        self.nets_label.setWordWrap(True)
        self.form.addRow("接続ノード", self.nets_label)
        self._refresh(c, nets)
        if t not in ("Plasma", "GND", "Label"):
            self._button("回転 (Ctrl+R)", self.editor.rotate_selection)
        if t != "Plasma":
            self._button("削除 (Del)", self.editor.delete_selection)

    def _refresh(self, c: dict, nets: dict[str, str]) -> None:
        self.nets_label.setText(", ".join(f"{pin}: {net}" for pin, net in nets.items()) if nets else "—")
        if self.value_edit is not None and not self.value_edit.hasFocus():
            self.value_edit.setText(eng_text(float(c.get("value") or 0.0)))
        if self.tones_table is not None and not self.tones_table.hasFocus():
            self.tones_table.set_tones(c.get("tones") or [])

    def _rename(self, cid: str, edit: QLineEdit) -> None:
        new = edit.text().strip()
        if new != cid:
            _mark_invalid(edit, not self.editor.rename_component(cid, new))

    def _set_value(self, cid: str, edit: SciSpin, t: str) -> None:
        try:
            v = edit.value()
        except ValueError:
            _mark_invalid(edit, True)
            return
        ok = v > 0 or t == "Vdc"
        _mark_invalid(edit, not ok)
        if ok:
            self.editor.update_component(cid, value=v)

    def _delete_tone(self, cid: str, table: TonesTable) -> None:
        row = table.currentRow()
        tones = list(table.tones)
        if 0 <= row < len(tones) and len(tones) > 1:
            del tones[row]
            self.editor.update_component(cid, tones=tones)

    def focus_first(self) -> None:
        if self.first_field is not None:
            self.first_field.setFocus()
            if isinstance(self.first_field, QLineEdit):
                self.first_field.selectAll()


# ---------------------------------------------------------------------------- editor
class CircuitEditor(QWidget):
    """Tool bar + canvas + property panel."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.electrodes: list[str] = ["W"]
        self.comps: list[dict] = []
        self.wires: list[list[tuple[int, int]]] = []
        self.extraction = None
        self.problem_text: list[str] = []
        self.tool = "select"
        self.ghost: Optional[CompItem] = None
        self.ghost_rot = 0
        self.wire_points: list[tuple[int, int]] = []
        self.wire_vertical_first = False
        self.preview: Optional[QGraphicsPathItem] = None
        self.marker: Optional[QGraphicsEllipseItem] = None
        self._last_cursor = (0, 0)
        self._drag: Optional[dict] = None
        self._building = False
        self._pending_select: Optional[tuple[list[str], list[int]]] = None
        self.comp_items: dict[str, CompItem] = {}
        self.wire_items: list[WireItem] = []
        self.scene = SchScene(self)
        self.view = SchView(self.scene)
        self.props = PropertyPanel(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setProperty("role", "muted")
        lay.addWidget(self.banner)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons: dict[str, QToolButton] = {}
        for tool, text, key in TOOLS:
            b = QToolButton()
            b.setCheckable(True)
            b.setIcon(tool_icon(tool))
            b.setIconSize(QSize(26, 26))
            tip = text + (f"（{key}）" if key else "")
            if tool == "wire":
                tip += "\nクリックで開始・角を追加、ピンやワイヤ上で終了（ダブルクリックでも終了）\nSpace で折れ方向を切替、Esc で中止"
            elif tool != "select":
                tip += "\nクリックで配置、右クリックまたは Ctrl+R で回転、Esc で終了"
            b.setToolTip(tip)
            b.setText(text)
            b.clicked.connect(lambda _c, t=tool: self.set_tool(t))
            self.tool_group.addButton(b)
            self.tool_buttons[tool] = b
            bar.addWidget(b)
            if tool == "wire":
                bar.addSpacing(10)
        self.tool_buttons["select"].setChecked(True)
        bar.addSpacing(10)
        self.btn_rotate = QPushButton("回転")
        self.btn_delete = QPushButton("削除")
        self.btn_fit = QPushButton("全体表示")
        self.btn_rotate.clicked.connect(self.rotate_selection)
        self.btn_delete.clicked.connect(self.delete_selection)
        self.btn_fit.clicked.connect(self.view.fit)
        for b in (self.btn_rotate, self.btn_delete, self.btn_fit):
            bar.addWidget(b)
        bar.addStretch(1)
        self.hint = QLabel("")
        self.hint.setProperty("role", "muted")
        bar.addWidget(self.hint)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.view)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.props)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(440)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 340])
        lay.addWidget(split, 1)
        for seq, slot in ((QKeySequence(QKeySequence.StandardKey.Delete), self.delete_selection), (QKeySequence("Backspace"), self.delete_selection),
                          (QKeySequence("Ctrl+R"), self.rotate_selection), (QKeySequence("Escape"), self.escape),
                          (QKeySequence("W"), lambda: self.set_tool("wire")), (QKeySequence("Space"), self.toggle_elbow),
                          (QKeySequence("Ctrl+A"), self.select_all)):
            sc = QShortcut(seq, self.view)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)
        self.scene.selectionChanged.connect(self._on_selection)
        model.changed.connect(self.reload)
        theme.manager().changed.connect(self._on_theme)
        self.reload()
        self._update_hint()

    # ------------------------------------------------------------------ document
    def is_schematic_mode(self) -> bool:
        circ = self.model.data["circuit"]
        return circ.get("mode", "ports") == "schematic" and bool(circ.get("schematic"))

    def _electrodes_from_data(self) -> list[str]:
        d = self.model.data
        conductors = {r.get("conductor") for r in d["geometry"]["regions"]}
        return ["W"] + (["F"] if "F" in conductors else []) + (["T"] if d["geometry"].get("top", {}).get("conductor") == "driven" else [])

    def _load_doc(self) -> None:
        self.electrodes = self._electrodes_from_data()
        circ = self.model.data["circuit"]
        if self.is_schematic_mode():
            sch = copy.deepcopy(circ["schematic"])
        else:
            try:
                spec = CircuitSpec.model_validate({k: v for k, v in circ.items() if k not in ("schematic", "mode")})
                sch = schematic_from_ports(spec, self.electrodes).model_dump(mode="json")
            except Exception:  # noqa: BLE001
                sch = default_schematic(self.electrodes).model_dump(mode="json")
        self.comps = [dict(c) for c in sch.get("components", [])]
        for c in self.comps:
            for k, v in (("rot", 0), ("value", None), ("tones", []), ("port", None), ("label", None)):
                c.setdefault(k, v)
        if not any(c["type"] == "Plasma" for c in self.comps):
            self.comps.append({"id": "PLASMA", "type": "Plasma", "x": 40, "y": 0, "rot": 0, "value": None, "tones": [], "port": None, "label": None})
        self.wires = [[tuple(p) for p in w["points"]] for w in sch.get("wires", [])]

    def _analyse(self) -> None:
        self.extraction = None
        self.problem_text = []
        try:
            sch = Schematic.model_validate({"components": self.comps, "wires": [{"points": w} for w in self.wires]})
            self.extraction = extract(sch, self.electrodes)
            self.problem_text = list(self.extraction.problems)
        except Exception as exc:  # noqa: BLE001
            lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
            msg = next((ln for ln in lines if ln.startswith("Value error")), lines[-1] if lines else str(exc))
            self.problem_text = [msg.replace("Value error, ", "").split(" [type=")[0]]

    def schematic_dict(self) -> dict:
        return {"components": copy.deepcopy(self.comps), "wires": [{"points": [list(p) for p in w]} for w in self.wires]}

    def commit(self, comps: list[dict], wires, select_ids: Optional[list[str]] = None, select_wires: Optional[list[int]] = None) -> None:
        d = copy.deepcopy(self.model.data)
        circ = d["circuit"]
        cleaned = [clean_points(w) for w in wires]
        circ["schematic"] = {"components": copy.deepcopy(comps), "wires": [{"points": [list(p) for p in w]} for w in cleaned if len(w) >= 2]}
        circ["mode"] = "schematic"
        self._pending_select = (list(select_ids or []), list(select_wires or []))
        self.model.replace(d)

    # ------------------------------------------------------------------ scene
    def reload(self) -> None:
        if self._drag is not None and self._drag.get("moved"):
            return
        self._load_doc()
        self._analyse()
        self._rebuild()

    def _on_theme(self, _mode: str) -> None:
        for tool, b in self.tool_buttons.items():
            b.setIcon(tool_icon(tool))
        self._rebuild()

    def _rebuild(self) -> None:
        self._building = True
        try:
            sel_ids, sel_w = self.selected()
            if self._pending_select is not None:
                sel_ids, sel_w = self._pending_select
                self._pending_select = None
            self.ghost = self.preview = self.marker = None
            self.scene.clear()
            self.comp_items.clear()
            self.wire_items = []
            for k, w in enumerate(self.wires):
                if len(w) >= 2:
                    it = WireItem(self, k, w)
                    self.scene.addItem(it)
                    self.wire_items.append(it)
            for c in self.comps:
                it = CompItem(self, c)
                self.scene.addItem(it)
                self.comp_items[c["id"]] = it
            self._decorations()
            for cid in sel_ids:
                if cid in self.comp_items:
                    self.comp_items[cid].setSelected(True)
            for it in self.wire_items:
                if it.index in sel_w:
                    it.setSelected(True)
            if self.tool in PLACEABLE:
                self._make_ghost()
            if self.tool == "wire" and self.wire_points:
                self._update_preview(self._last_cursor)
        finally:
            self._building = False
        text = ("" if self.is_schematic_mode() else
                "いまは「回路」タブの表形式（ポートごとの直列チェーン）で外部回路を定義しています。下の図はその等価回路です。"
                "図を編集すると回路図モードに切り替わり、以後はこの回路図が解析に使われます（Ctrl+Z で戻せます）。")
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        self._on_selection()

    def _decorations(self) -> None:
        t = theme.tokens()
        ex = self.extraction
        if ex is None:
            return
        for x, y in ex.junctions:
            d = self.scene.addEllipse(x * G - 3.2, y * G - 3.2, 6.4, 6.4, QPen(Qt.PenStyle.NoPen), QBrush(QColor(t["info"])))
            d.setZValue(6)
            d.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        for net, members in ex.nets.items():  # open pins
            if net == "0" or len(members) > 1:
                continue
            cid, pin = members[0]
            comp = next((c for c in self.comps if c["id"] == cid), None)
            if comp is None or comp["type"] == "Label":
                continue
            for name, (x, y) in pin_points(comp, self.electrodes):
                if name == pin and not self._on_any_wire((x, y)):
                    d = self.scene.addEllipse(x * G - 3, y * G - 3, 6, 6, QPen(QColor(t["error"]), 1.4), QBrush(Qt.BrushStyle.NoBrush))
                    d.setZValue(7)
                    d.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        plasma = next(c for c in self.comps if c["type"] == "Plasma")
        for e in self.electrodes:  # net names at the electrode pins
            item = QGraphicsSimpleTextItem(ex.electrode_net.get(e, ""))
            item.setFont(_font(7))
            item.setBrush(QBrush(QColor(t["text_muted"])))
            br = item.boundingRect()
            item.setPos(plasma["x"] * G - br.width() - 3, (plasma["y"] + ELECTRODE_PIN_Y[e]) * G - br.height() - 1)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setZValue(4)
            self.scene.addItem(item)

    def _on_any_wire(self, pt: tuple[int, int]) -> bool:
        for w in self.wires:
            for a, b in zip(w, w[1:]):
                if on_segment(pt, tuple(a), tuple(b)):
                    return True
        return False

    def is_connection_point(self, pt: tuple[int, int]) -> bool:
        for c in self.comps:
            for _n, p in pin_points(c, self.electrodes):
                if p == pt:
                    return True
        return self._on_any_wire(pt)

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool: str) -> None:
        self._cancel_wire()
        if self.ghost is not None:
            self.scene.removeItem(self.ghost)
            self.ghost = None
        if self.marker is not None:
            self.scene.removeItem(self.marker)
            self.marker = None
        self.tool = tool
        self.tool_buttons[tool].setChecked(True)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool == "select" else QGraphicsView.DragMode.NoDrag)
        self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor)
        if tool in PLACEABLE:
            self.ghost_rot = 0
            self._make_ghost()
        self._update_hint()
        self.view.setFocus()

    def _update_hint(self) -> None:
        hints = {"select": "クリックで選択・ドラッグで移動・ダブルクリックで編集・中ボタンでパン",
                 "wire": "クリックで開始／角を追加・ピンやワイヤ上で終了・Space で折れ方向・Esc で中止"}
        self.hint.setText(hints.get(self.tool, f"{TYPE_NAMES.get(self.tool, '')}: クリックで配置・右クリック／Ctrl+R で回転・Esc で終了"))

    def _make_ghost(self) -> None:
        c = new_component(self.comps, self.tool, self._last_cursor[0], self._last_cursor[1], self.ghost_rot)
        self.ghost = CompItem(self, c, ghost=True)
        self.scene.addItem(self.ghost)

    def escape(self) -> None:
        if self.tool == "wire" and self.wire_points:
            self._cancel_wire()
        elif self.tool != "select":
            self.set_tool("select")
        else:
            self.scene.clearSelection()

    def toggle_elbow(self) -> None:
        self.wire_vertical_first = not self.wire_vertical_first
        if self.wire_points:
            self._update_preview(self._last_cursor)

    def select_all(self) -> None:
        for it in list(self.comp_items.values()) + self.wire_items:
            it.setSelected(True)

    # ------------------------------------------------------------------ mouse
    def handle_press(self, event) -> bool:
        pt = (round(event.scenePos().x() / G), round(event.scenePos().y() / G))
        self._last_cursor = pt
        if self.tool in PLACEABLE:
            if event.button() == Qt.MouseButton.RightButton:
                self.rotate_ghost()
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                self.place(self.tool, pt, self.ghost_rot)
                return True
            return False
        if self.tool == "wire":
            if event.button() == Qt.MouseButton.RightButton:
                if len(self.wire_points) >= 2:
                    self._finish_wire()
                elif self.wire_points:
                    self._cancel_wire()
                else:
                    self.set_tool("select")
                return True
            if event.button() == Qt.MouseButton.LeftButton:
                self.wire_click(pt)
                return True
            return False
        self._drag = {"moved": False} if event.button() == Qt.MouseButton.LeftButton else None
        return False

    def handle_move(self, event) -> None:
        pt = (round(event.scenePos().x() / G), round(event.scenePos().y() / G))
        self._last_cursor = pt
        if self.ghost is not None:
            self.ghost.setPos(pt[0] * G, pt[1] * G)
        if self.tool == "wire":
            self._update_marker(pt)
            if self.wire_points:
                self._update_preview(pt)

    def on_item_moved(self) -> None:
        if self._building or self._drag is None:
            return
        self._drag["moved"] = True
        _comps, wires, fixed = self._dragged_state()
        for it in self.wire_items:  # live rubber band on the wires that are not dragged themselves
            if it.index not in fixed and it.index < len(wires) and len(wires[it.index]) >= 2:
                it.set_points(wires[it.index])

    def _dragged_state(self):
        comps = copy.deepcopy(self.comps)
        moves: dict[tuple[int, int], tuple[int, int]] = {}
        for c in comps:
            it = self.comp_items.get(c["id"])
            if it is None:
                continue
            nx, ny = round(it.pos().x() / G), round(it.pos().y() / G)
            if (nx, ny) != (c["x"], c["y"]):
                old = pin_points(c, self.electrodes)
                c["x"], c["y"] = nx, ny
                for (_n, a), (_m, b) in zip(old, pin_points(c, self.electrodes)):
                    moves.setdefault(a, b)
        wires = [list(w) for w in self.wires]
        fixed = set()
        for it in self.wire_items:
            dx, dy = round(it.pos().x() / G), round(it.pos().y() / G)
            if (dx, dy) != (0, 0):
                wires[it.index] = [(x + dx, y + dy) for x, y in wires[it.index]]
                fixed.add(it.index)
        return comps, rubber_band(wires, moves, fixed), fixed

    def handle_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None or not drag["moved"]:
            return
        comps, wires, _fixed = self._dragged_state()
        cids, wids = self.selected()
        self.commit(comps, wires, select_ids=cids, select_wires=wids)

    def handle_double_click(self, event) -> bool:
        if self.tool == "wire":
            if len(self.wire_points) >= 2:
                self._finish_wire()
            return True
        return self.tool in PLACEABLE

    # ------------------------------------------------------------------ placing
    def place(self, ctype: str, pt: tuple[int, int], rot: int = 0) -> dict:
        comps = copy.deepcopy(self.comps)
        c = new_component(comps, ctype, pt[0], pt[1], rot)
        comps.append(c)
        self.commit(comps, self.wires, select_ids=[c["id"]])
        return c

    def rotate_ghost(self) -> None:
        if self.ghost is None or self.tool in ("GND", "Label"):
            return
        self.ghost_rot = (self.ghost_rot + 90) % 360
        self.scene.removeItem(self.ghost)
        self._make_ghost()

    # ------------------------------------------------------------------ wires
    def _elbow(self, a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
        return (a[0], b[1]) if self.wire_vertical_first else (b[0], a[1])

    def wire_click(self, pt: tuple[int, int]) -> None:
        if not self.wire_points:
            self.wire_points = [pt]
            self._update_preview(pt)
            return
        last = self.wire_points[-1]
        if pt == last:
            return
        self.wire_points = clean_points(self.wire_points + [self._elbow(last, pt), pt])
        if self.is_connection_point(pt):
            self._finish_wire()
        else:
            self._update_preview(pt)

    def add_wire(self, points) -> None:
        pts = clean_points(points)
        if len(pts) >= 2:
            self.commit(self.comps, self.wires + [pts])

    def _finish_wire(self) -> None:
        pts = clean_points(self.wire_points)
        self._cancel_wire()
        self.add_wire(pts)

    def _cancel_wire(self) -> None:
        self.wire_points = []
        if self.preview is not None:
            self.scene.removeItem(self.preview)
            self.preview = None

    def _update_preview(self, pt: tuple[int, int]) -> None:
        t = theme.tokens()
        pts = self.wire_points + [self._elbow(self.wire_points[-1], pt), pt]
        path = QPainterPath(QPointF(pts[0][0] * G, pts[0][1] * G))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x * G, y * G))
        if self.preview is None:
            self.preview = QGraphicsPathItem()
            self.preview.setZValue(30)
            self.preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(self.preview)
        self.preview.setPen(QPen(QColor(t["accent"]), 1.6, Qt.PenStyle.DashLine))
        self.preview.setPath(path)

    def _update_marker(self, pt: tuple[int, int]) -> None:
        t = theme.tokens()
        if self.marker is None:
            self.marker = QGraphicsEllipseItem()
            self.marker.setZValue(31)
            self.marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(self.marker)
        conn = self.is_connection_point(pt)
        r = 5.0 if conn else 3.0
        self.marker.setRect(pt[0] * G - r, pt[1] * G - r, 2 * r, 2 * r)
        self.marker.setPen(QPen(QColor(t["accent"]), 1.8 if conn else 1.0))

    # ------------------------------------------------------------------ editing
    def selected(self) -> tuple[list[str], list[int]]:
        cids = [i.comp["id"] for i in self.scene.selectedItems() if isinstance(i, CompItem) and not i.ghost]
        wids = [i.index for i in self.scene.selectedItems() if isinstance(i, WireItem)]
        return cids, wids

    def _comp(self, cid: str) -> Optional[dict]:
        return next((c for c in self.comps if c["id"] == cid), None)

    def delete_selection(self) -> None:
        cids, wids = self.selected()
        cids = [c for c in cids if (self._comp(c) or {}).get("type") != "Plasma"]
        if not cids and not wids:
            return
        self.commit([c for c in self.comps if c["id"] not in cids], [w for k, w in enumerate(self.wires) if k not in wids])

    def rotate_selection(self) -> None:
        if self.tool in PLACEABLE:
            self.rotate_ghost()
            return
        cids, _w = self.selected()
        comps = copy.deepcopy(self.comps)
        moves = {}
        changed = False
        for c in comps:
            if c["id"] in cids and c["type"] not in ("Plasma", "GND", "Label"):
                old = pin_points(c, self.electrodes)
                c["rot"] = (int(c.get("rot", 0)) + 90) % 360
                for (_n, a), (_m, b) in zip(old, pin_points(c, self.electrodes)):
                    moves.setdefault(a, b)
                changed = True
        if changed:
            self.commit(comps, rubber_band(self.wires, moves, set()), select_ids=cids)

    def update_component(self, cid: str, **changes) -> None:
        comps = copy.deepcopy(self.comps)
        for c in comps:
            if c["id"] == cid:
                c.update(changes)
        self.commit(comps, self.wires, select_ids=[changes.get("id", cid)])

    def rename_component(self, cid: str, new: str) -> bool:
        comp = self._comp(cid)
        if comp is None or not new or not new.replace("_", "").isalnum():
            return False
        if any(c["id"].upper() == new.upper() for c in self.comps if c["id"] != cid):
            return False
        letter = {"R": "R", "L": "L", "C": "C", "Vdc": "V"}.get(comp["type"])
        if letter and new[0].upper() != letter:
            return False
        self.update_component(cid, id=new)
        return True

    def focus_properties(self, cid: str) -> None:
        self.select(cid)
        self.props.focus_first()

    def select(self, cid: str) -> bool:
        if cid not in self.comp_items:
            return False
        self.scene.clearSelection()
        self.comp_items[cid].setSelected(True)
        return True

    # ------------------------------------------------------------------ selection -> properties
    def _on_selection(self) -> None:
        if self._building:
            return
        cids, wids = self.selected()
        self.btn_delete.setEnabled(bool(wids) or any((self._comp(c) or {}).get("type") != "Plasma" for c in cids))
        self.btn_rotate.setEnabled(bool(cids))
        if len(cids) == 1 and not wids:
            c = self._comp(cids[0])
            nets = {}
            if self.extraction is not None:
                for name, _p in pin_points(c, self.electrodes):
                    if (c["id"], name) in self.extraction.pin_net:
                        nets[{"p": "+", "n": "−", "a": "ノード"}.get(name, name)] = self.extraction.pin_net[(c["id"], name)]
            self.props.show_component(c, self.electrodes, nets)
            return
        if len(wids) == 1 and not cids:
            self.props.show_wire(wids[0])
            return
        if cids or wids:
            self.props.show_multi(len(cids), len(wids))
            return
        n = {t: sum(1 for c in self.comps if c["type"] == t) for t in TYPE_NAMES}
        summary = (f"部品 {len(self.comps) - 1} 個（RF 電源 {n['Vrf']}、R {n['R']}、L {n['L']}、C {n['C']}、DC 電源 {n['Vdc']}）、ワイヤ {len(self.wires)} 本。\n\n"
                   "ツールバーで部品を選んでクリックで配置し、ワイヤツール（W）でピン同士をつなぎます。"
                   "ワイヤの端が別のワイヤの途中に乗ると T 字に接続され、同じ名前のネットラベルどうしも接続されます。")
        t = theme.tokens()
        if self.problem_text:
            notes = "<br>".join(f"<span style='color:{t['warning']}'>⚠ {p}</span>" for p in self.problem_text)
        else:
            notes = f"<span style='color:{t['success']}'>✓ 接続に問題はありません</span>"
        if self.extraction is not None:
            srcs = ", ".join(f"{k} → {v}" for k, v in self.extraction.source_port.items())
            bias = ", ".join(self.extraction.bias_ports) or "なし"
            notes += (f"<br><br><span style='color:{t['text_muted']}'>電源の集計先: {srcs or '—'}<br>"
                      f"自己バイアスが立つ電極（直流的に接地から浮いている）: {bias}</span>")
        self.props.show_none(summary, notes)
