"""Editable schematic of the external circuit.

The plasma / electrode ladder is generated from the physics model and shown as one block; everything
around it (RF sources, generator and matching network, feed inductance, blocking and stray capacitors,
focus-ring tuning, user-defined elements of ``circuit.extra_netlist``) is drawn as symbols that can be
selected, edited in the property panel, added, and removed. All edits go through :class:`ProjectModel`
so they are undoable and stay in sync with the form-based circuit panel.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGraphicsItem, QGraphicsObject, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget)

from ...core.netlist.user_elements import KINDS, UNITS, UserElement, UserNetlist, format_eng
from ...core.project import PortSpec, SourceSpec
from .. import theme
from ..project_model import ProjectModel
from .paramform import BoundForm, SciSpin

U = 44.0  # grid unit [px]
ELEMENT_LEN = 2 * U


# ---------------------------------------------------------------------------- descriptors
@dataclass
class Field:
    kind: str  # float | opt_float | int | choice | bool | info
    label: str
    path: str = ""
    unit: str = ""
    scale: float = 1.0
    tooltip: str = ""
    choices: list = field(default_factory=list)
    getter: Optional[Callable[[], str]] = None


@dataclass
class Descriptor:
    id: str
    title: str
    description: str = ""
    fields: list[Field] = field(default_factory=list)
    user_index: Optional[int] = None  # index into UserNetlist.elements
    actions: list = field(default_factory=list)  # (text, callable)
    remove: Optional[Callable[[], None]] = None
    remove_text: str = "削除"


# ---------------------------------------------------------------------------- graphics items
def _font(size: int = 9, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(theme.UI_FONTS)
    f.setPointSize(size)
    f.setBold(bold)
    return f


def _text(scene: QGraphicsScene, s: str, x: float, y: float, color: str, size: int = 9, bold: bool = False, parent=None) -> QGraphicsSimpleTextItem:
    it = QGraphicsSimpleTextItem(s, parent) if parent is not None else scene.addSimpleText(s)
    it.setFont(_font(size, bold))
    it.setBrush(QBrush(QColor(color)))
    it.setPos(x, y)
    return it


class ElementItem(QGraphicsObject):
    """Two-terminal symbol (R, L, C, Vac, Vdc, short) drawn from (0, 0) along +x (``h``) or +y (``v``)."""

    double_clicked = Signal(object)

    def __init__(self, symbol: str, orientation: str, desc: Descriptor, label: str, value: str, length: float = ELEMENT_LEN,
                 label_side: str = "right"):
        super().__init__()
        self.symbol = symbol
        self.orient = orientation
        self.desc = desc
        self.length = length
        self.label_side = label_side  # vertical elements: labels to the right (default) or left
        self._hover = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(5)
        t = theme.tokens()
        self.label = _text(None, label, 0, 0, t["text"], 9, True, parent=self)
        self.value = _text(None, value, 0, 0, t["text_muted"], 8, False, parent=self)
        self._place_labels()
        tip = desc.description or desc.title
        self.setToolTip(f"{desc.title}\n{tip}" if tip != desc.title else desc.title)

    def _place_labels(self) -> None:
        L = self.length
        lw = self.label.boundingRect().width()
        lh = self.label.boundingRect().height()
        vw = self.value.boundingRect().width()
        if self.orient == "h":
            self.label.setPos(L / 2 - lw / 2, -0.55 * U - lh - 2)
            self.value.setPos(L / 2 - vw / 2, 0.42 * U)
        elif self.label_side == "left":
            self.label.setPos(-0.5 * U - lw, L / 2 - 2)
            self.value.setPos(-0.5 * U - vw, L / 2 + lh - 2)
        else:
            self.label.setPos(0.5 * U, L / 2 - 2)
            self.value.setPos(0.5 * U, L / 2 + lh - 2)

    def boundingRect(self) -> QRectF:
        if self.orient == "h":
            return QRectF(-2, -0.6 * U, self.length + 4, 1.2 * U)
        return QRectF(-0.6 * U, -2, 1.2 * U, self.length + 4).united(self.childrenBoundingRect())

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        a = (self.length - 0.9 * U) / 2 - 4
        if self.orient == "h":
            p.addRect(QRectF(a, -0.45 * U, self.length - 2 * a, 0.9 * U))
        else:
            p.addRect(QRectF(-0.45 * U, a, 0.9 * U, self.length - 2 * a))
        return p

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = theme.tokens()
        color = t["accent"] if self.isSelected() else (t["accent_text"] if self._hover else t["text"])
        pen = QPen(QColor(color), 2.2 if self.isSelected() else 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.save()
        if self.orient == "v":
            painter.rotate(90)  # +x -> +y
        if self.isSelected() or self._hover:
            halo = QColor(t["accent"])
            halo.setAlpha(28 if self.isSelected() else 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawRoundedRect(QRectF(0.2 * U, -0.5 * U, self.length - 0.4 * U, 1.0 * U), 8, 8)
        _draw_symbol(painter, self.symbol, self.length, pen, QColor(t["surface"]))
        painter.restore()


def _draw_symbol(p: QPainter, symbol: str, L: float, pen: QPen, fill: QColor) -> None:
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if symbol in ("short", "wire"):
        p.drawLine(QPointF(0, 0), QPointF(L, 0))
        if symbol == "short":
            p.setBrush(QBrush(pen.color()))
            p.drawEllipse(QPointF(L / 2, 0), 3.0, 3.0)
        return
    body = 0.9 * U
    a = (L - body) / 2
    p.drawLine(QPointF(0, 0), QPointF(a, 0))
    p.drawLine(QPointF(L - a, 0), QPointF(L, 0))
    if symbol == "R":
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(QRectF(a, -0.18 * U, body, 0.36 * U), 3, 3)
    elif symbol == "L":
        n = 4
        w = body / n
        path = QPainterPath(QPointF(a, 0))
        for i in range(n):
            path.arcTo(QRectF(a + i * w, -w / 2, w, w), 180, -180)
        p.drawPath(path)
    elif symbol == "C":
        gap = 0.22 * U
        xm = L / 2
        p.drawLine(QPointF(a, 0), QPointF(xm - gap / 2, 0))
        p.drawLine(QPointF(xm + gap / 2, 0), QPointF(L - a, 0))
        p.drawLine(QPointF(xm - gap / 2, -0.34 * U), QPointF(xm - gap / 2, 0.34 * U))
        p.drawLine(QPointF(xm + gap / 2, -0.34 * U), QPointF(xm + gap / 2, 0.34 * U))
    elif symbol in ("Vac", "Vdc"):
        r = 0.4 * U
        c = QPointF(L / 2, 0)
        p.drawLine(QPointF(a, 0), QPointF(L / 2 - r, 0))
        p.drawLine(QPointF(L / 2 + r, 0), QPointF(L - a, 0))
        p.setBrush(QBrush(fill))
        p.drawEllipse(c, r, r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if symbol == "Vac":
            path = QPainterPath(QPointF(L / 2 - 0.26 * U, 0))
            for k in range(1, 17):
                x = -0.26 * U + 0.52 * U * k / 16
                path.lineTo(QPointF(L / 2 + x, -0.18 * U * math.sin(2 * math.pi * k / 16)))
            p.drawPath(path)
        else:
            # "+" at the first terminal, "-" at the second (local +x)
            p.drawLine(QPointF(L / 2 - 0.18 * U, 0), QPointF(L / 2 - 0.06 * U, 0))
            p.drawLine(QPointF(L / 2 - 0.12 * U, -0.06 * U), QPointF(L / 2 - 0.12 * U, 0.06 * U))
            p.drawLine(QPointF(L / 2 + 0.06 * U, 0), QPointF(L / 2 + 0.18 * U, 0))


class BlockItem(QGraphicsObject):
    """The generated plasma / electrode ladder, shown as a selectable block."""

    double_clicked = Signal(object)

    def __init__(self, rect: QRectF, desc: Descriptor, title: str, lines: list[str]):
        super().__init__()
        self.rect = rect
        self.desc = desc
        self.title = title
        self.lines = lines
        self._hover = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setToolTip(desc.description)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-2, -2, 2, 2)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = theme.tokens()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = t["accent"] if (self.isSelected() or self._hover) else t["border"]
        painter.setPen(QPen(QColor(color), 2 if self.isSelected() else 1.2))
        painter.setBrush(QBrush(QColor(t["surface"])))
        painter.drawRoundedRect(self.rect, 10, 10)
        painter.setPen(QColor(t["text"]))
        painter.setFont(_font(10, True))
        x0, y0 = self.rect.left() + 12, self.rect.top() + 10
        painter.drawText(QRectF(x0, y0, self.rect.width() - 24, 20), Qt.AlignmentFlag.AlignLeft, self.title)
        painter.setFont(_font(8))
        painter.setPen(QColor(t["text_muted"]))
        y = y0 + 24
        for line in self.lines:
            painter.drawText(QRectF(x0, y, self.rect.width() - 24, 16), Qt.AlignmentFlag.AlignLeft, line)
            y += 15


class SchematicView(QGraphicsView):
    context_requested = Signal(object, object)  # item (or None), global pos

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self._fit_pending = True

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if 0.25 < self.transform().m11() * factor < 4.0:
            self.scale(factor, factor)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        t = theme.tokens()
        painter.fillRect(rect, QColor(t["bg"]))
        dot = QColor(t["border"])
        painter.setPen(QPen(dot, 1.5))
        step = U / 2
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        x = x0
        while x < rect.right():
            y = y0
            while y < rect.bottom():
                painter.drawPoint(QPointF(x, y))
                y += step
            x += step

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, (ElementItem, BlockItem)):
            item = item.parentItem()
        self.context_requested.emit(item, event.globalPos())

    def fit(self) -> None:
        rect = self.scene().itemsBoundingRect().adjusted(-U, -U, U, U)
        if rect.isEmpty():
            return
        self.setSceneRect(rect)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.25:  # do not blow small circuits up
            self.resetTransform()
            self.scale(1.25, 1.25)
            self.centerOn(rect.center())
        self._fit_pending = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._fit_pending:
            self.fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_pending:
            self.fit()


# ---------------------------------------------------------------------------- property panel
class UserElementForm(QWidget):
    def __init__(self, editor: "CircuitEditor", index: int, element: UserElement, nodes: list[str], parent=None):
        super().__init__(parent)
        self.editor = editor
        self.index = index
        self._blocked = False
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.kind = QLabel(f"{KINDS[element.kind]} ({element.kind})")
        self.name = QLineEdit(element.name)
        self.n1 = QComboBox()
        self.n2 = QComboBox()
        for cb in (self.n1, self.n2):
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            cb.addItems(nodes)
        self.n1.setCurrentText(element.n1)
        self.n2.setCurrentText(element.n2)
        self.value = SciSpin(element.value)
        form.addRow("種類", self.kind)
        form.addRow("名前", self.name)
        form.addRow("ノード 1", self.n1)
        form.addRow("ノード 2", self.n2)
        form.addRow(f"値 [{UNITS[element.kind]}]", self.value)
        self.name.editingFinished.connect(lambda: self._commit(name=self.name.text().strip()))
        self.value.editingFinished.connect(self._commit_value)
        self.n1.currentTextChanged.connect(lambda s: self._commit(n1=s.strip()))
        self.n2.currentTextChanged.connect(lambda s: self._commit(n2=s.strip()))
        if element.tail:
            form.addRow("追加パラメータ", QLabel(element.tail))

    def _commit_value(self) -> None:
        try:
            self._commit(value=self.value.value())
        except ValueError:
            pass

    def _commit(self, **changes) -> None:
        if self._blocked or not changes:
            return
        self.editor.update_user_element(self.index, **changes)

    def update_from(self, element: UserElement, nodes: list[str]) -> None:
        self._blocked = True
        try:
            if not self.name.hasFocus():
                self.name.setText(element.name)
            for cb, n in ((self.n1, element.n1), (self.n2, element.n2)):
                cur = [cb.itemText(i) for i in range(cb.count())]
                if cur != nodes:
                    cb.clear()
                    cb.addItems(nodes)
                if not cb.hasFocus():
                    cb.setCurrentText(n)
            if not self.value.hasFocus():
                self.value.set_value(element.value)
        finally:
            self._blocked = False

    def focus_first(self) -> None:
        self.value.setFocus()
        self.value.selectAll()


class PropertyPanel(QWidget):
    def __init__(self, editor: "CircuitEditor", parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setProperty("surface", "true")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        self.title = QLabel("外部回路")
        self.title.setProperty("role", "heading")
        self.title.setWordWrap(True)
        self.desc = QLabel("")
        self.desc.setProperty("role", "muted")
        self.desc.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.desc)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(self.body)
        self.remove_btn = QPushButton("削除")
        self.remove_btn.setVisible(False)
        lay.addWidget(self.remove_btn)
        lay.addStretch(1)
        self.notes = QLabel("")
        self.notes.setProperty("role", "muted")
        self.notes.setWordWrap(True)
        lay.addWidget(self.notes)
        self._form: Optional[QWidget] = None
        self._desc: Optional[Descriptor] = None
        self._sig: tuple = ()
        self._remove_cb: Optional[Callable[[], None]] = None
        self.remove_btn.clicked.connect(self._on_remove)

    @property
    def current_id(self) -> Optional[str]:
        return self._desc.id if self._desc else None

    def _clear_form(self) -> None:
        if self._form is not None:
            if isinstance(self._form, BoundForm):
                self._form.detach()
            self.body.removeWidget(self._form)
            self._form.hide()
            self._form.setParent(None)
            self._form.deleteLater()
            self._form = None

    def show_default(self, summary: str, notes: str) -> None:
        self._clear_form()
        self._desc = None
        self.title.setText("外部回路")
        self.desc.setText(summary)
        self.remove_btn.setVisible(False)
        self.notes.setText(notes)

    def show_descriptor(self, desc: Descriptor, netlist: UserNetlist, nodes: list[str]) -> None:
        sig = tuple((f.kind, f.path) for f in desc.fields)
        same = self._desc is not None and self._desc.id == desc.id and self._form is not None
        if same and isinstance(self._form, UserElementForm) and desc.user_index is not None:
            self._form.update_from(netlist.elements[desc.user_index], nodes)
            self._desc = desc
            self.desc.setText(desc.description)
            self._bind_remove(desc)
            return
        if same and isinstance(self._form, BoundForm) and sig == self._sig:
            self._desc = desc
            self._bind_remove(desc)
            return
        self._clear_form()
        self._desc = desc
        self._sig = sig
        self.title.setText(desc.title)
        self.desc.setText(desc.description)
        if desc.user_index is not None:
            self._form = UserElementForm(self.editor, desc.user_index, netlist.elements[desc.user_index], nodes)
        else:
            f = BoundForm(self.editor.model)
            for fd in desc.fields:
                if fd.kind == "float":
                    f.add_float(fd.label, fd.path, fd.unit, fd.scale, fd.tooltip)
                elif fd.kind == "opt_float":
                    f.add_optional_float(fd.label, fd.path, fd.unit, fd.scale, fd.tooltip)
                elif fd.kind == "int":
                    f.add_int(fd.label, fd.path, 1, 100000, fd.tooltip)
                elif fd.kind == "choice":
                    f.add_choice(fd.label, fd.path, fd.choices, fd.tooltip)
                elif fd.kind == "bool":
                    f.add_bool(fd.label, fd.path, fd.tooltip)
                elif fd.kind == "info":
                    f.add_info(fd.label, fd.getter or (lambda: ""))
            f.form.setContentsMargins(0, 0, 0, 0)
            self._form = f
        self.body.addWidget(self._form)
        self._bind_remove(desc)
        self.notes.setText("")

    def _bind_remove(self, desc: Descriptor) -> None:
        self._remove_cb = desc.remove
        self.remove_btn.setVisible(desc.remove is not None)
        self.remove_btn.setText(desc.remove_text)

    def _on_remove(self) -> None:
        if self._remove_cb is not None:
            self._remove_cb()

    def focus_first(self) -> None:
        if isinstance(self._form, UserElementForm):
            self._form.focus_first()
        elif isinstance(self._form, BoundForm):
            for i in range(self._form.form.rowCount()):
                it = self._form.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if it is not None and it.widget() is not None and it.widget().isEnabled():
                    it.widget().setFocus()
                    if isinstance(it.widget(), QLineEdit):
                        it.widget().selectAll()
                    return


class AddElementDialog(QDialog):
    def __init__(self, netlist: UserNetlist, nodes: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("素子を追加")
        self.netlist = netlist
        form = QFormLayout(self)
        self.kind = QComboBox()
        for k, label in KINDS.items():
            self.kind.addItem(f"{label} ({k})", k)
        self.kind.addItem("短絡（電流プローブ, V = 0）", "short")
        self.name = QLineEdit()
        self.n1 = QComboBox()
        self.n2 = QComboBox()
        for cb in (self.n1, self.n2):
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            cb.addItems(nodes)
        self.n1.setCurrentText(nodes[0] if nodes else "W")
        self.n2.setCurrentText("0")
        self.value = SciSpin(50.0)
        self.unit = QLabel("Ω")
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.value, 1)
        hl.addWidget(self.unit)
        form.addRow("種類", self.kind)
        form.addRow("名前", self.name)
        form.addRow("ノード 1", self.n1)
        form.addRow("ノード 2", self.n2)
        form.addRow("値", row)
        hint = QLabel("ノード: W / F / T は電極ポート、0 は接地。新しい中間ノードは u_ で始まる名前にします。")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        k = self.kind.currentData()
        defaults = {"R": (50.0, "Ω"), "L": (100e-9, "H"), "C": (100e-12, "F"), "V": (0.0, "V"), "short": (0.0, "V")}
        v, unit = defaults[k]
        self.value.set_value(v)
        self.value.setEnabled(k != "short")
        self.unit.setText(unit)
        self.name.setText(self.netlist.next_name("V" if k == "short" else k))

    def result_spec(self) -> tuple[str, str, str, str, float]:
        k = self.kind.currentData()
        kind = "V" if k == "short" else k
        value = 0.0 if k == "short" else self.value.value()
        return kind, self.name.text().strip() or self.netlist.next_name(kind), self.n1.currentText().strip(), self.n2.currentText().strip(), value


# ---------------------------------------------------------------------------- editor
class CircuitEditor(QWidget):
    """Schematic editor widget (toolbar, canvas, property panel)."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.scene = QGraphicsScene(self)
        self.view = SchematicView(self.scene)
        self.props = PropertyPanel(self)
        self.netlist = UserNetlist()
        self._items: dict[str, QGraphicsObject] = {}
        self._building = False
        self._selected_id: Optional[str] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_add = QToolButton()
        self.btn_add.setText("素子を追加 ▾")
        self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_add = QMenu(self)
        self.btn_add.setMenu(self.menu_add)
        self.btn_struct = QToolButton()
        self.btn_struct.setText("構成 ▾")
        self.btn_struct.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_struct = QMenu(self)
        self.menu_struct.aboutToShow.connect(lambda: self._fill_structure_menu(self.menu_struct))
        self.btn_struct.setMenu(self.menu_struct)
        self.btn_remove = QPushButton("削除")
        self.btn_remove.setEnabled(False)
        self.btn_fit = QPushButton("全体表示")
        self.hint = QLabel("クリックで選択 ・ ダブルクリックで編集 ・ 右クリックでメニュー")
        self.hint.setProperty("role", "muted")
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_struct)
        bar.addWidget(self.btn_remove)
        bar.addWidget(self.btn_fit)
        bar.addStretch(1)
        bar.addWidget(self.hint)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.view)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.props)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(420)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 340])
        lay.addWidget(split, 1)
        self._fill_add_menu()
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_fit.clicked.connect(self.view.fit)
        self.scene.selectionChanged.connect(self._on_selection)
        self.view.context_requested.connect(self._context_menu)
        model.changed.connect(self.rebuild)
        theme.manager().changed.connect(self._on_theme_changed)
        self.rebuild()

    def _on_theme_changed(self, _mode: str) -> None:
        self.rebuild()

    # ------------------------------------------------------------------ helpers
    def _data(self) -> dict:
        return self.model.data

    def _circuit(self) -> dict:
        return self._data()["circuit"]

    def fr_separate(self) -> bool:
        return self._circuit().get("fr", {}).get("type") == "separate"

    def top_driven(self) -> bool:
        return self._data()["geometry"].get("top", {}).get("conductor") == "driven"

    def available_nodes(self) -> list[str]:
        nodes = ["W"]
        if self.fr_separate():
            nodes.append("F")
        if self.top_driven():
            nodes.append("T")
        nodes.append("0")
        nodes += [n for n in self.netlist.user_nodes() if n not in nodes]
        nodes.append(self.netlist.next_node())
        return nodes

    def _set_netlist(self) -> None:
        self.model.set("circuit.extra_netlist", self.netlist.to_text())

    # ------------------------------------------------------------------ user elements API
    def add_user_element(self, kind: str, n1: str, n2: str, value: float, name: Optional[str] = None) -> UserElement:
        self.netlist = UserNetlist.parse(self._circuit().get("extra_netlist", "") or "")
        el = self.netlist.add(kind, n1, n2, value, name)
        self._selected_id = f"user.{self.netlist.index_of(el)}"
        self._set_netlist()
        return el

    def update_user_element(self, index: int, **changes) -> None:
        self.netlist = UserNetlist.parse(self._circuit().get("extra_netlist", "") or "")
        els = self.netlist.elements
        if not 0 <= index < len(els):
            return
        el = els[index]
        for k, v in changes.items():
            if k in ("name", "n1", "n2", "value") and v not in (None, ""):
                setattr(el, k, v)
        self.netlist.touch(el)
        self._selected_id = f"user.{index}"
        self._set_netlist()

    def remove_user_element(self, index: int) -> None:
        self.netlist = UserNetlist.parse(self._circuit().get("extra_netlist", "") or "")
        els = self.netlist.elements
        if 0 <= index < len(els):
            self.netlist.remove(els[index])
            self._selected_id = None
            self._set_netlist()

    # ------------------------------------------------------------------ structural edits
    def _replace(self, mutate: Callable[[dict], None]) -> None:
        data = copy.deepcopy(self._data())
        mutate(data)
        self.model.replace(data)

    def toggle_l_feed(self, port: str = "W") -> None:
        cur = float(self._circuit()["ports"][port].get("l_feed", 0.0) or 0.0)
        self.model.set(f"circuit.ports.{port}.l_feed", 0.0 if cur > 0 else 20e-9)

    def toggle_c_stray(self, port: str = "W") -> None:
        cur = float(self._circuit()["ports"][port].get("c_stray", 0.0) or 0.0)
        self.model.set(f"circuit.ports.{port}.c_stray", 0.0 if cur > 0 else 100e-12)

    def toggle_blocking_c(self, port: str = "W") -> None:
        cur = self._circuit()["ports"][port].get("blocking_c")
        self.model.set(f"circuit.ports.{port}.blocking_c", None if cur is not None else 2e-9)

    def toggle_generator(self, port: str = "W") -> None:
        cur = self._circuit()["ports"][port].get("generator", {}).get("type", "none")
        self.model.set(f"circuit.ports.{port}.generator.type", "none" if cur == "l_match" else "l_match")

    def add_source(self, port: str) -> None:
        srcs = list(self._circuit()["ports"][port].get("sources", []))
        f = 2e6 if srcs else 60e6
        srcs.append(SourceSpec(f=f, v=100.0).model_dump(mode="json"))
        self.model.set(f"circuit.ports.{port}.sources", srcs)

    def remove_source(self, port: str, index: int) -> None:
        srcs = list(self._circuit()["ports"][port].get("sources", []))
        if len(srcs) > 1 and 0 <= index < len(srcs):
            del srcs[index]
            self.model.set(f"circuit.ports.{port}.sources", srcs)

    def set_top_driven(self, driven: bool) -> None:
        def mutate(d: dict) -> None:
            d["geometry"]["top"]["conductor"] = "driven" if driven else "ground"
            ports = d["circuit"]["ports"]
            if driven and "T" not in ports:
                f = ports["W"]["sources"][0]["f"] if ports["W"].get("sources") else 60e6
                ports["T"] = PortSpec(sources=[SourceSpec(f=f, v=100.0, phase_deg=180.0)], c_stray=0.0).model_dump(mode="json")
            if not driven:
                ports.pop("T", None)
                if d["geometry"]["outer_boundary"].get("type") not in ("short", "open", "impedance"):
                    d["geometry"]["outer_boundary"]["type"] = "short"

        self._replace(mutate)

    def set_fr_type(self, fr_type: str) -> None:
        def mutate(d: dict) -> None:
            d["circuit"]["fr"]["type"] = fr_type
            regions = d["geometry"]["regions"]
            if fr_type == "separate":
                if not any(r.get("conductor") == "F" for r in regions):
                    cand = next((r for r in regions if "fr" in r["name"].lower()), None)
                    if cand is None and len(regions) > 1:
                        cand = regions[1]
                    if cand is not None:
                        cand["conductor"] = "F"
            else:
                for r in regions:
                    if r.get("conductor") == "F":
                        r["conductor"] = "W"
                d["circuit"]["ports"].pop("F", None)
                if d["circuit"]["fr"]["tuning"].get("type") == "source":
                    d["circuit"]["fr"]["tuning"]["type"] = "none"

        self._replace(mutate)

    def set_fr_tuning(self, tuning_type: str) -> None:
        def mutate(d: dict) -> None:
            t = d["circuit"]["fr"]["tuning"]
            t["type"] = tuning_type
            if tuning_type in ("cap_to_gnd", "series_lc_to_gnd", "coupling_to_w") and not t.get("c"):
                t["c"] = 100e-12
            if tuning_type == "series_lc_to_gnd" and not t.get("l"):
                t["l"] = 1e-6
            if tuning_type == "dc_bias":
                t["l"] = t.get("l") or 1e-3
                t["v_dc"] = t.get("v_dc") if t.get("v_dc") is not None else 0.0
            ports = d["circuit"]["ports"]
            if tuning_type == "source" and "F" not in ports:
                f = ports["W"]["sources"][0]["f"] if ports["W"].get("sources") else 60e6
                ports["F"] = PortSpec(sources=[SourceSpec(f=f, v=200.0)], blocking_c=2e-9).model_dump(mode="json")
            if tuning_type != "source":
                ports.pop("F", None)

        self._replace(mutate)

    def set_c_wf_manual(self, manual: bool) -> None:
        def mutate(d: dict) -> None:
            d["circuit"]["c_wf"]["mode"] = "manual" if manual else "auto"
            if manual and not d["circuit"]["c_wf"].get("value"):
                d["circuit"]["c_wf"]["value"] = 100e-12

        self._replace(mutate)

    # ------------------------------------------------------------------ menus
    def _fill_add_menu(self) -> None:
        m = self.menu_add
        m.clear()
        for k, label in KINDS.items():
            m.addAction(f"{label} ({k})", lambda k=k: self._add_dialog(k))
        m.addAction("短絡（電流プローブ）", lambda: self._add_dialog("short"))

    def _add_dialog(self, kind: str) -> None:
        dlg = AddElementDialog(self.netlist, self.available_nodes(), self)
        idx = dlg.kind.findData(kind)
        if idx >= 0:
            dlg.kind.setCurrentIndex(idx)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            k, name, n1, n2, value = dlg.result_spec()
            if n1 and n2 and n1 != n2:
                self.add_user_element(k, n1, n2, value, name)

    def _fill_structure_menu(self, m: QMenu) -> None:
        m.clear()
        circ = self._circuit()
        for port in ("W", "F", "T"):
            if port not in circ["ports"]:
                continue
            ps = circ["ports"][port]
            sub = m.addMenu(f"ポート {port}")
            a = sub.addAction("50 Ω 発振器 + L 型整合器")
            a.setCheckable(True)
            a.setChecked(ps.get("generator", {}).get("type") == "l_match")
            a.triggered.connect(lambda _c, p=port: self.toggle_generator(p))
            a = sub.addAction("給電インダクタンス L_feed")
            a.setCheckable(True)
            a.setChecked(float(ps.get("l_feed", 0) or 0) > 0)
            a.triggered.connect(lambda _c, p=port: self.toggle_l_feed(p))
            a = sub.addAction("ブロッキング容量 C_b")
            a.setCheckable(True)
            a.setChecked(ps.get("blocking_c") is not None)
            a.triggered.connect(lambda _c, p=port: self.toggle_blocking_c(p))
            a = sub.addAction("ストレイ容量 C_stray")
            a.setCheckable(True)
            a.setChecked(float(ps.get("c_stray", 0) or 0) > 0)
            a.triggered.connect(lambda _c, p=port: self.toggle_c_stray(p))
            sub.addSeparator()
            sub.addAction("電源（周波数成分）を追加", lambda p=port: self.add_source(p))
        m.addSeparator()
        a = m.addAction("上部電極を駆動（ポート T）")
        a.setCheckable(True)
        a.setChecked(self.top_driven())
        a.triggered.connect(lambda c: self.set_top_driven(bool(c)))
        a = m.addAction("フォーカスリングを独立電極にする（ポート F）")
        a.setCheckable(True)
        a.setChecked(self.fr_separate())
        a.triggered.connect(lambda c: self.set_fr_type("separate" if c else "esc_coupled"))
        tune = m.addMenu("FR 調整回路")
        tune.setEnabled(self.fr_separate())
        cur = circ.get("fr", {}).get("tuning", {}).get("type", "none")
        for value, text in FR_TUNING_CHOICES:
            a = tune.addAction(text)
            a.setCheckable(True)
            a.setChecked(cur == value)
            a.triggered.connect(lambda _c, v=value: self.set_fr_tuning(v))
        a = m.addAction("W–F 間容量 C_WF を手動指定")
        a.setCheckable(True)
        a.setEnabled(self.fr_separate())
        a.setChecked(circ.get("c_wf", {}).get("mode") == "manual")
        a.triggered.connect(lambda c: self.set_c_wf_manual(bool(c)))

    def _context_menu(self, item, global_pos) -> None:
        menu = QMenu(self)
        if item is not None:
            item.setSelected(True)
            desc = item.desc
            menu.addAction("編集…", self.props.focus_first)
            for text, fn in desc.actions:
                menu.addAction(text, fn)
            if desc.remove is not None:
                menu.addAction(desc.remove_text, desc.remove)
            menu.addSeparator()
        add = menu.addMenu("素子を追加")
        for k, label in KINDS.items():
            add.addAction(f"{label} ({k})", lambda k=k: self._add_dialog(k))
        add.addAction("短絡（電流プローブ）", lambda: self._add_dialog("short"))
        struct = menu.addMenu("外部回路の構成")
        self._fill_structure_menu(struct)
        menu.addSeparator()
        menu.addAction("全体表示", self.view.fit)
        menu.exec(global_pos)

    def _remove_selected(self) -> None:
        item = self._selected_item()
        if item is not None and item.desc.remove is not None:
            item.desc.remove()

    def _selected_item(self):
        for it in self.scene.selectedItems():
            if isinstance(it, (ElementItem, BlockItem)):
                return it
        return None

    def _on_selection(self) -> None:
        if self._building:
            return
        item = self._selected_item()
        if item is None:
            self._selected_id = None
            self.btn_remove.setEnabled(False)
            self._show_default()
            return
        self._selected_id = item.desc.id
        self.btn_remove.setEnabled(item.desc.remove is not None)
        self.props.show_descriptor(item.desc, self.netlist, self.available_nodes()[:-1] + [self.netlist.next_node()])

    def select(self, desc_id: str) -> bool:
        item = self._items.get(desc_id)
        if item is None:
            return False
        self.scene.clearSelection()
        item.setSelected(True)
        return True

    def _show_default(self) -> None:
        circ = self._circuit()
        n_user = len(self.netlist.elements)
        n_raw = len(self.netlist.raw_lines)
        parts = ["要素をクリックすると右側で値を編集できます。「構成」メニューまたは右クリックで要素の追加・削除、"
                 "「素子を追加」で任意の R / L / C / DC 源をノード W, F, T, 0, u_* の間に追加できます。"]
        parts.append(f"ポート: {', '.join(circ['ports'])}  ／  FR 給電: {'独立電極' if self.fr_separate() else 'ESC 容量結合'}")
        parts.append(f"ユーザ素子 {n_user} 個" + (f"（テキストのみの行 {n_raw} 行は左の回路パネルで編集）" if n_raw else ""))
        notes = self.netlist.problems(set(self.available_nodes()))
        self.props.show_default("\n".join(parts), "\n".join("⚠ " + p for p in notes))

    # ------------------------------------------------------------------ building the scene
    def rebuild(self) -> None:
        self._building = True
        try:
            sel = self._selected_id
            self.scene.clear()
            self._items.clear()
            try:
                self.netlist = UserNetlist.parse(self._circuit().get("extra_netlist", "") or "")
                self._build()
            except Exception as exc:  # noqa: BLE001  (model transiently invalid)
                t = theme.tokens()
                _text(self.scene, f"回路図を生成できません: {exc}", 0, 0, t["error"], 9)
            self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-U, -U, U, U))
            if sel is not None and sel in self._items:
                self._items[sel].setSelected(True)
        finally:
            self._building = False
        self._on_selection()

    def _add_item(self, item) -> None:
        self.scene.addItem(item)
        self._items[item.desc.id] = item
        item.double_clicked.connect(lambda _it: self.props.focus_first())

    def _wire(self, x0: float, y0: float, x1: float, y1: float, width: float = 1.6) -> None:
        t = theme.tokens()
        pen = QPen(QColor(t["text"]), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        ln = self.scene.addLine(x0, y0, x1, y1, pen)
        ln.setZValue(1)

    def _wire_v_hops(self, x: float, y0: float, y1: float, cross: list[float]) -> None:
        t = theme.tokens()
        pen = QPen(QColor(t["text"]), 1.6)
        r = 6.0
        path = QPainterPath(QPointF(x, y0))
        for yc in sorted(c for c in cross if y0 + r < c < y1 - r):
            path.lineTo(QPointF(x, yc - r))
            path.arcTo(QRectF(x - r, yc - r, 2 * r, 2 * r), 90, -180)
        path.lineTo(QPointF(x, y1))
        it = self.scene.addPath(path, pen)
        it.setZValue(1)

    def _dot(self, x: float, y: float, label: str = "", above: bool = True) -> None:
        t = theme.tokens()
        d = self.scene.addEllipse(x - 3.5, y - 3.5, 7, 7, QPen(Qt.PenStyle.NoPen), QBrush(QColor(t["text"])))
        d.setZValue(6)
        if label:
            it = _text(self.scene, label, 0, 0, t["accent_text"], 9, True)
            w = it.boundingRect().width()
            h = it.boundingRect().height()
            it.setPos(x - w / 2, y - h - 6 if above else y + 6)

    def _ground(self, x: float, y: float) -> None:
        t = theme.tokens()
        pen = QPen(QColor(t["text"]), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for i, w in enumerate((0.42 * U, 0.26 * U, 0.1 * U)):
            ln = self.scene.addLine(x - w, y + i * 6, x + w, y + i * 6, pen)
            ln.setZValue(1)

    def _element(self, symbol: str, orient: str, x: float, y: float, desc: Descriptor, label: str, value: str, length: float = ELEMENT_LEN,
                 label_side: str = "right") -> ElementItem:
        it = ElementItem(symbol, orient, desc, label, value, length, label_side)
        it.setPos(x, y)
        self._add_item(it)
        return it

    def _chain_entries(self, port: str, ps: dict) -> list[tuple]:
        """(kind 'series'|'shunt', symbol, label, value_text, descriptor)."""
        gen = ps.get("generator", {}) or {}
        out: list[tuple] = []
        gd = self._generator_desc(port, gen)
        if gen.get("type") == "l_match":
            out.append(("series", "R", "R_gen", format_eng(float(gen.get("r_gen", 50.0)), "Ω"), gd))
            auto = bool(gen.get("auto", True))
            l_txt = "auto" if auto else format_eng(gen.get("l"), "H")
            c_txt = "auto" if auto else format_eng(gen.get("c"), "F")
            topo = gen.get("topology", "auto")
            if topo in ("auto", "shunt_c_generator"):
                out.append(("shunt", "C", "C_match", c_txt, gd))
                out.append(("series", "L", "L_match", l_txt, gd))
            else:
                out.append(("series", "L", "L_match", l_txt, gd))
                out.append(("shunt", "C", "C_match", c_txt, gd))
        l_feed = float(ps.get("l_feed", 0.0) or 0.0)
        if l_feed > 0:
            out.append(("series", "L", "L_feed", format_eng(l_feed, "H"), Descriptor(
                f"port.{port}.l_feed", f"給電インダクタンス L_feed（ポート {port}）",
                "電源（または整合器）と電極の間の直列インダクタンス。共振により電極電圧が増幅されることがあります。",
                [Field("float", "L_feed", f"circuit.ports.{port}.l_feed", "nH", 1e9)],
                remove=lambda p=port: self.model.set(f"circuit.ports.{p}.l_feed", 0.0), remove_text="L_feed を外す")))
        if ps.get("blocking_c") is not None:
            out.append(("series", "C", "C_b", format_eng(float(ps["blocking_c"]), "F"), Descriptor(
                f"port.{port}.blocking_c", f"ブロッキング容量 C_b（ポート {port}）",
                "直列ブロッキング容量。TRAN 解析ではこの容量に自己バイアス電圧が立ちます。",
                [Field("opt_float", "C_b", f"circuit.ports.{port}.blocking_c", "nF", 1e9),
                 Field("opt_float", "DC バイアス初期推定", f"circuit.ports.{port}.dc_bias_guess", "V", 1.0, "空欄なら自動推定")],
                remove=lambda p=port: self.model.set(f"circuit.ports.{p}.blocking_c", None), remove_text="C_b を外す")))
        return out

    def _generator_desc(self, port: str, gen: dict) -> Descriptor:
        return Descriptor(
            f"port.{port}.generator", f"発振器と L 型整合器（ポート {port}）",
            "電源振幅は発振器の開放電圧。自動チューニングでは AC 解析のポートインピーダンスから L と C を設計します。",
            [Field("float", "発振器インピーダンス r_gen", f"circuit.ports.{port}.generator.r_gen", "Ω"),
             Field("bool", "自動チューニング", f"circuit.ports.{port}.generator.auto"),
             Field("opt_float", "整合 L（手動）", f"circuit.ports.{port}.generator.l", "nH", 1e9),
             Field("opt_float", "整合 C（手動）", f"circuit.ports.{port}.generator.c", "pF", 1e12),
             Field("choice", "トポロジ", f"circuit.ports.{port}.generator.topology",
                   choices=[("auto", "自動"), ("shunt_c_generator", "並列 C を発振器側"), ("shunt_c_load", "並列 C を負荷側")])],
            remove=lambda p=port: self.model.set(f"circuit.ports.{p}.generator.type", "none"), remove_text="整合器を外す")

    def _source_desc(self, port: str, srcs: list[dict]) -> Descriptor:
        fields: list[Field] = []
        for i, _s in enumerate(srcs):
            base = f"circuit.ports.{port}.sources.{i}"
            fields += [Field("float", f"電源 {i+1}: 周波数", f"{base}.f", "MHz", 1e-6),
                       Field("float", f"電源 {i+1}: 振幅", f"{base}.v", "V"),
                       Field("float", f"電源 {i+1}: 位相", f"{base}.phase_deg", "deg"),
                       Field("opt_float", f"電源 {i+1}: 電力指定", f"{base}.power", "W", 1.0, "空欄なら電圧指定。値を入れると供給電力が一定になるよう振幅を反復します")]
        actions = [("電源（周波数成分）を追加", lambda p=port: self.add_source(p))]
        if len(srcs) > 1:
            actions += [(f"電源 {i+1} を削除", lambda p=port, i=i: self.remove_source(p, i)) for i in range(len(srcs))]
        return Descriptor(f"port.{port}.sources", f"RF 電源（ポート {port}）",
                          "理想電圧源（複数行で多周波）。発振器を有効にすると開放電圧として扱われます。", fields, actions=actions)

    def _build(self) -> None:
        t = theme.tokens()
        d = self._data()
        circ = d["circuit"]
        ports: dict = circ["ports"]
        fr_sep = self.fr_separate()
        driven = self.top_driven()
        rows = ["W"] + (["F"] if fr_sep else []) + (["T"] if driven else [])
        chains = {p: (self._chain_entries(p, ports[p]) if p in ports else []) for p in rows}
        n_series_max = max([sum(1 for e in chains[p] if e[0] == "series") for p in rows] + [0])
        x_src = 1.5 * U
        x_port = x_src + ELEMENT_LEN * n_series_max + (U if n_series_max == 0 else 0.5 * U)
        # ---- between-row stacks (W–F) and F hang (tuning)
        tuning = circ.get("fr", {}).get("tuning", {}) or {}
        ttype = tuning.get("type", "none") if fr_sep else "none"
        between: list[list[tuple]] = []  # stacks of (symbol, label, value, desc)
        hang_f: list[tuple] = []
        tdesc = Descriptor("fr.tuning", f"FR 調整回路: {dict(FR_TUNING_CHOICES).get(ttype, ttype)}",
                           "独立 FR 電極（ポート F）の外部回路。種類は「構成」メニューまたは右クリックで変更します。",
                           [Field("choice", "種類", "circuit.fr.tuning.type", choices=FR_TUNING_CHOICES),
                            Field("opt_float", "調整 C", "circuit.fr.tuning.c", "pF", 1e12),
                            Field("opt_float", "調整 L", "circuit.fr.tuning.l", "nH", 1e9),
                            Field("float", "直列抵抗 R", "circuit.fr.tuning.r", "Ω"),
                            Field("opt_float", "DC バイアス", "circuit.fr.tuning.v_dc", "V")],
                           remove=lambda: self.set_fr_tuning("none"), remove_text="調整回路を外す（開放）")
        if ttype == "tie":
            between.append([("short", "短絡", "F = W", tdesc)])
        elif ttype == "coupling_to_w":
            st = []
            if tuning.get("l"):
                st.append(("L", "L_cpl", format_eng(tuning["l"], "H"), tdesc))
            if float(tuning.get("r", 0) or 0) > 0:
                st.append(("R", "R_cpl", format_eng(tuning["r"], "Ω"), tdesc))
            st.append(("C", "C_cpl", format_eng(tuning.get("c"), "F"), tdesc))
            between.append(st)
        elif ttype == "cap_to_gnd":
            if float(tuning.get("r", 0) or 0) > 0:
                hang_f.append(("R", "R_tune", format_eng(tuning["r"], "Ω"), tdesc))
            hang_f.append(("C", "C_tune", format_eng(tuning.get("c"), "F"), tdesc))
        elif ttype == "series_lc_to_gnd":
            hang_f.append(("L", "L_tune", format_eng(tuning.get("l"), "H"), tdesc))
            if float(tuning.get("r", 0) or 0) > 0:
                hang_f.append(("R", "R_tune", format_eng(tuning["r"], "Ω"), tdesc))
            hang_f.append(("C", "C_tune", format_eng(tuning.get("c"), "F"), tdesc))
        elif ttype == "dc_bias":
            hang_f.append(("L", "L_choke", format_eng(tuning.get("l") or 1e-3, "H"), tdesc))
            hang_f.append(("Vdc", "V_dc", format_eng(tuning.get("v_dc") or 0.0, "V"), tdesc))
        cwf = circ.get("c_wf", {}) or {}
        if fr_sep:
            cdesc = Descriptor("c_wf", "W–F 間容量 C_WF", "ウェハ電極と FR 電極の間の直接結合容量（独立 FR 電極のみ）。自動では幾何から推定します。",
                               [Field("choice", "指定", "circuit.c_wf.mode", choices=[("auto", "自動（幾何から）"), ("manual", "手動")]),
                                Field("opt_float", "C_WF（手動）", "circuit.c_wf.value", "pF", 1e12)],
                               remove=lambda: self.set_c_wf_manual(False), remove_text="自動に戻す")
            if cwf.get("mode") == "manual" and cwf.get("value"):
                between.append([("C", "C_WF", format_eng(cwf["value"], "F"), cdesc)])
        # ---- row positions
        ys: dict[str, float] = {}
        hang: dict[str, float] = {}
        y = 0.0
        prev: Optional[str] = None
        for p in rows:
            if prev is not None:
                m_between = max([len(st) for st in between] + [0]) if (prev, p) == ("W", "F") else 0
                y = ys[prev] + max(4.5 * U, hang[prev] + 2.2 * U, ELEMENT_LEN * m_between + 1.0 * U)
            ys[p] = y
            has_shunt = any(e[0] == "shunt" for e in chains[p]) or (p in ports and float(ports[p].get("c_stray", 0) or 0) > 0)
            h = 2.7 * U if (p in ports or has_shunt) else 0.6 * U
            if p == "F" and hang_f:
                h = max(h, ELEMENT_LEN * len(hang_f) + 0.8 * U)
            hang[p] = h
            prev = p
        # ---- block geometry
        n_cols = len(between)
        x_block = x_port + 1.6 * U + 1.5 * U * n_cols
        y_first, y_last = ys[rows[0]], ys[rows[-1]]
        block_rect = QRectF(x_block, y_first - 1.4 * U, 4.8 * U, max(3.4 * U, (y_last - y_first) + 2.8 * U))
        # ---- draw rows
        for p in rows:
            yr = ys[p]
            x = x_src
            if p in ports:
                ps = ports[p]
                srcs = ps.get("sources", []) or []
                if len(srcs) == 1:
                    vtxt = f"{format_eng(srcs[0]['f'], 'Hz')}, {format_eng(srcs[0]['v'], 'V')}" + (" (P)" if srcs[0].get("power") else "")
                else:
                    vtxt = f"{len(srcs)} 周波数"
                self._element("Vac", "v", x, yr, self._source_desc(p, srcs), f"V_{p}", vtxt, label_side="left")
                self._ground(x, yr + ELEMENT_LEN)
                self._dot(x, yr)
                for kind, sym, label, val, desc in chains[p]:
                    if kind == "series":
                        self._element(sym, "h", x, yr, desc, label, val)
                        x += ELEMENT_LEN
                        self._dot(x, yr)
                    else:
                        self._element(sym, "v", x, yr, desc, label, val)
                        self._ground(x, yr + ELEMENT_LEN)
            if x < x_port:
                self._wire(x, yr, x_port, yr)
            self._wire(x_port, yr, x_block, yr)
            self._dot(x_port, yr, p)
            if p in ports and float(ports[p].get("c_stray", 0) or 0) > 0:
                ps = ports[p]
                cdesc_s = Descriptor(f"port.{p}.c_stray", f"ストレイ容量 C_stray（ポート {p}）", "電極ノードから接地への浮遊容量。",
                                     [Field("float", "C_stray", f"circuit.ports.{p}.c_stray", "pF", 1e12)],
                                     remove=lambda pp=p: self.model.set(f"circuit.ports.{pp}.c_stray", 0.0), remove_text="C_stray を外す")
                self._element("C", "v", x_port, yr, cdesc_s, "C_stray", format_eng(float(ps["c_stray"]), "F"))
                self._ground(x_port, yr + ELEMENT_LEN)
            if p == "F" and hang_f:
                xh = x_port + (0.75 * U if (p in ports and float(ports[p].get("c_stray", 0) or 0) > 0) else 0.0)
                yy = yr
                if xh != x_port:
                    self._dot(xh, yr)
                for sym, label, val, desc in hang_f:
                    self._element(sym, "v", xh, yy, desc, label, val)
                    yy += ELEMENT_LEN
                self._ground(xh, yy)
        # ---- between W and F
        if fr_sep and between:
            yw, yf = ys["W"], ys["F"]
            for k, stack in enumerate(between):
                xk = x_port + 1.5 * U * (k + 1)
                yy = yw
                for sym, label, val, desc in stack:
                    if sym == "short":
                        self._element("short", "v", xk, yy, desc, label, val, yf - yw)
                        yy = yf
                        break
                    self._element(sym, "v", xk, yy, desc, label, val)
                    yy += ELEMENT_LEN
                if yy < yf:
                    self._wire(xk, yy, xk, yf)
                self._dot(xk, yw)
                self._dot(xk, yf)
        # ---- plasma block
        n_cells = sum(int(v) for v in d["numerics"]["mesh"].get("n_r", {}).values())
        lines = [f"FR: {'独立電極（ポート F）' if fr_sep else 'ESC 容量結合（自動）'}",
                 f"上部電極: {'駆動（ポート T）' if driven else '接地'}",
                 f"{n_cells} セル × N_z = {d['numerics']['mesh'].get('n_z', 1)}",
                 "物理モデルから自動生成"]
        bdesc = Descriptor("plasma", "プラズマ・電極ラダー（自動生成）",
                           "幾何・プラズマ・メッシュ設定から生成される等価回路。ここでは給電構成だけを切り替えます。",
                           [Field("info", "ポート", lambda: ", ".join(self._circuit()["ports"])),
                            Field("info", "セル数", lambda: str(sum(int(v) for v in self._data()["numerics"]["mesh"].get("n_r", {}).values())))],
                           actions=[("上部電極を駆動 / 接地", lambda: self.set_top_driven(not self.top_driven())),
                                    ("FR を独立電極 / ESC 結合", lambda: self.set_fr_type("esc_coupled" if self.fr_separate() else "separate"))])
        block = BlockItem(block_rect, bdesc, "プラズマ・電極ラダー", lines)
        self._add_item(block)
        for p in rows:
            _text(self.scene, p, x_block + 6, ys[p] - 16, t["accent_text"], 8, True)
        xg = block_rect.center().x()
        self._wire(xg, block_rect.bottom(), xg, block_rect.bottom() + 0.6 * U)
        self._ground(xg, block_rect.bottom() + 0.6 * U)
        _text(self.scene, "G", xg + 8, block_rect.bottom() + 0.1 * U, t["accent_text"], 8, True)
        # ---- user elements (rails)
        y_rail0 = max(ys[rows[-1]] + hang[rows[-1]], block_rect.bottom() + 1.0 * U) + 1.8 * U
        self._build_rails(y_rail0, x_block + block_rect.width())

    def _build_rails(self, y0: float, x_min_end: float) -> None:
        t = theme.tokens()
        els = self.netlist.elements
        _text(self.scene, "ユーザ定義素子（circuit.extra_netlist）", 0.2 * U, y0 - 1.3 * U, t["text_muted"], 9, True)
        if not els:
            _text(self.scene, "まだありません。「素子を追加」または右クリックで R / L / C / DC 源をノード間に追加できます。",
                  0.2 * U, y0 - 0.8 * U, t["text_muted"], 8)
            return
        order: list[str] = []
        for n in ("W", "F", "T"):
            if any(n in e.nodes() for e in els):
                order.append(n)
        for n in self.netlist.all_nodes():
            if n not in order and n != "0":
                order.append(n)
        order.append("0")
        spacing = 2.4 * U
        rail_y = {n: y0 + i * spacing for i, n in enumerate(order)}
        x_start = 2.4 * U
        x_end = max(x_min_end, 3.5 * U + ELEMENT_LEN * len(els) + 0.5 * U)
        pen = QPen(QColor(t["border"]), 1.4)
        for n, yr in rail_y.items():
            ln = self.scene.addLine(x_start, yr, x_end, yr, pen)
            ln.setZValue(0)
            lab = _text(self.scene, "0 (GND)" if n == "0" else n, 0, 0, t["accent_text"] if n != "0" else t["text_muted"], 9, True)
            lab.setPos(x_start - lab.boundingRect().width() - 10, yr - lab.boundingRect().height() / 2)
        self._ground(x_end, rail_y["0"])
        avail = set(self.available_nodes())
        for k, e in enumerate(els):
            xk = 3.5 * U + ELEMENT_LEN * k
            ya, yb = rail_y.get(e.n1, y0), rail_y.get(e.n2, y0)
            if ya > yb:
                ya, yb = yb, ya
            if ya == yb:
                yb = ya + spacing
            kind_label = KINDS[e.kind]
            if e.kind == "V" and e.value == 0:
                sym, vtxt = "short", "短絡 (0 V)"
            else:
                sym, vtxt = {"R": "R", "L": "L", "C": "C", "V": "Vdc"}[e.kind], format_eng(e.value, e.unit)
            desc = Descriptor(f"user.{k}", f"{kind_label} {e.name}", f"ユーザ定義素子: {e.to_line()}", user_index=k,
                              remove=lambda i=k: self.remove_user_element(i), remove_text="この素子を削除")
            if e.n1 not in avail or e.n2 not in avail:
                desc.description += "\n⚠ 現在の構成に無いノードを参照しています"
            span = yb - ya
            crossing = [yy for yy in rail_y.values() if ya < yy < yb]
            if span <= ELEMENT_LEN * 1.5 or sym == "short":
                self._element(sym, "v", xk, ya, desc, e.name, vtxt, span)
            else:
                self._element(sym, "v", xk, ya, desc, e.name, vtxt)
                self._wire_v_hops(xk, ya + ELEMENT_LEN, yb, crossing)
            self._dot(xk, ya)
            self._dot(xk, yb)


FR_TUNING_CHOICES = [("none", "開放"), ("tie", "W と短絡"), ("cap_to_gnd", "接地へ可変 C"), ("series_lc_to_gnd", "接地へ直列 LC"),
                     ("coupling_to_w", "W へ結合 C"), ("dc_bias", "DC バイアス（チョーク経由）"), ("source", "独立電源（ポート F）")]
