"""Small form helpers bound to ProjectModel paths."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from ..project_model import ProjectModel


class SciSpin(QLineEdit):
    """Line edit for floating point values with engineering notation (accepts 1e-3, 2.5n, 300p ...)."""

    _SUFFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}

    def __init__(self, value: float = 0.0, parent=None):
        super().__init__(parent)
        self.set_value(value)

    @classmethod
    def parse(cls, text: str) -> float:
        t = text.strip().replace(",", "")
        if not t:
            raise ValueError("empty")
        if t[-1] in cls._SUFFIX and not t[-1].isdigit():
            return float(t[:-1]) * cls._SUFFIX[t[-1]]
        return float(t)

    def value(self) -> float:
        return self.parse(self.text())

    def set_value(self, v: float) -> None:
        if v == 0:
            self.setText("0")
        elif 1e-3 <= abs(v) < 1e6:
            self.setText(f"{v:.6g}")
        else:
            self.setText(f"{v:.4e}")


class BoundForm(QWidget):
    """A QFormLayout whose rows are bound to model paths."""

    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.form = QFormLayout(self)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._updaters: list[Callable[[], None]] = []
        self._blocked = False
        model.changed.connect(self.refresh)

    # ------------------------------------------------------------------ rows
    def add_float(self, label: str, path: str, unit: str = "", scale: float = 1.0, tooltip: str = "") -> SciSpin:
        """scale: displayed = stored * scale (e.g. 1e3 to show mm for metres)."""
        w = SciSpin()
        if tooltip:
            w.setToolTip(tooltip)

        def commit():
            if self._blocked:
                return
            try:
                self.model.set(path, w.value() / scale)
                _mark_invalid(w, False)
            except ValueError:
                _mark_invalid(w, True)

        def update():
            v = self.model.get(path)
            if v is not None:
                w.set_value(float(v) * scale)

        w.editingFinished.connect(commit)
        self._updaters.append(update)
        self.form.addRow(f"{label}{' [' + unit + ']' if unit else ''}", w)
        update()
        return w

    def add_optional_float(self, label: str, path: str, unit: str = "", scale: float = 1.0, tooltip: str = "",
                           placeholder: str = "（なし）") -> SciSpin:
        """Like add_float, but an empty field stores ``None``."""
        w = SciSpin()
        w.setPlaceholderText(placeholder)
        if tooltip:
            w.setToolTip(tooltip)

        def commit():
            if self._blocked:
                return
            text = w.text().strip()
            try:
                self.model.set(path, None if text == "" else SciSpin.parse(text) / scale)
                _mark_invalid(w, False)
            except ValueError:
                _mark_invalid(w, True)

        def update():
            v = self.model.get(path)
            if v is None:
                w.setText("")
            else:
                w.set_value(float(v) * scale)

        w.editingFinished.connect(commit)
        self._updaters.append(update)
        self.form.addRow(f"{label}{' [' + unit + ']' if unit else ''}", w)
        update()
        return w

    def add_int(self, label: str, path: str, lo: int = 1, hi: int = 10000, tooltip: str = "") -> QSpinBox:
        w = QSpinBox()
        w.setRange(lo, hi)
        if tooltip:
            w.setToolTip(tooltip)
        w.valueChanged.connect(lambda v: None if self._blocked else self.model.set(path, int(v)))
        self._updaters.append(lambda: w.setValue(int(self.model.get(path, lo))))
        self.form.addRow(label, w)
        self._updaters[-1]()
        return w

    def add_choice(self, label: str, path: str, choices: Sequence[tuple[str, str]], tooltip: str = "") -> QComboBox:
        """choices: (value, display text)."""
        w = QComboBox()
        for value, text in choices:
            w.addItem(text, value)
        if tooltip:
            w.setToolTip(tooltip)
        w.currentIndexChanged.connect(lambda i: None if self._blocked else self.model.set(path, w.itemData(i)))

        def update():
            v = self.model.get(path)
            idx = w.findData(v)
            if idx >= 0:
                w.setCurrentIndex(idx)

        self._updaters.append(update)
        self.form.addRow(label, w)
        update()
        return w

    def add_bool(self, label: str, path: str, tooltip: str = "") -> QCheckBox:
        w = QCheckBox()
        if tooltip:
            w.setToolTip(tooltip)
        w.toggled.connect(lambda b: None if self._blocked else self.model.set(path, bool(b)))
        self._updaters.append(lambda: w.setChecked(bool(self.model.get(path, False))))
        self.form.addRow(label, w)
        self._updaters[-1]()
        return w

    def add_text(self, label: str, path: str, tooltip: str = "") -> QLineEdit:
        w = QLineEdit()
        if tooltip:
            w.setToolTip(tooltip)
        w.editingFinished.connect(lambda: None if self._blocked else self.model.set(path, w.text()))
        self._updaters.append(lambda: w.setText(str(self.model.get(path, ""))))
        self.form.addRow(label, w)
        self._updaters[-1]()
        return w

    def add_info(self, label: str, getter: Callable[[], str]) -> QLabel:
        w = QLabel()
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._updaters.append(lambda: w.setText(getter()))
        self.form.addRow(label, w)
        self._updaters[-1]()
        return w

    def add_widget(self, label: str, widget: QWidget) -> None:
        self.form.addRow(label, widget)

    def refresh(self) -> None:
        self._blocked = True
        try:
            for u in self._updaters:
                try:
                    u()
                except Exception:  # noqa: BLE001  (model may be transiently invalid)
                    pass
        finally:
            self._blocked = False

    def detach(self) -> None:
        """Stop following the model (call before discarding a dynamically created form)."""
        try:
            self.model.changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass


def _mark_invalid(widget: QWidget, invalid: bool) -> None:
    widget.setProperty("invalid", "true" if invalid else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def group(title: str, widget: QWidget) -> QGroupBox:
    g = QGroupBox(title)
    lay = QVBoxLayout(g)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.addWidget(widget)
    return g
