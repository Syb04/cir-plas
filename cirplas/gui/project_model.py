"""Observable wrapper around the pydantic Project used by the GUI panels."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal

from ..core.project import Project, default_project, load_project, save_project


class ProjectModel(QObject):
    """Holds the project dictionary, validates on every change and notifies views.

    Panels edit the *dict* form (so that transiently invalid states can be shown); ``project``
    returns the validated pydantic object or raises.
    """

    changed = Signal()  # data changed (valid or not)
    validity_changed = Signal(bool, str)  # is_valid, message
    path_changed = Signal(str)

    def __init__(self, project: Optional[Project] = None):
        super().__init__()
        self._data: dict = (project or default_project()).model_dump(mode="json")
        self._path: Optional[Path] = None
        self._valid = True
        self._error = ""
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self.dirty = False

    # ------------------------------------------------------------------ access
    @property
    def data(self) -> dict:
        return self._data

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def is_valid(self) -> bool:
        return self._valid

    @property
    def error(self) -> str:
        return self._error

    def project(self) -> Project:
        return Project.model_validate(self._data)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in path.split("."):
            if isinstance(node, list):
                node = node[int(key)]
            elif isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node

    # ------------------------------------------------------------------ edits
    def set(self, path: str, value: Any) -> None:
        if self.get(path, object()) == value:
            return
        self._push_undo()
        node: Any = self._data
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[int(key)] if isinstance(node, list) else node.setdefault(key, {})
        last = keys[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
        self._after_change()

    def replace(self, data: dict) -> None:
        self._push_undo()
        self._data = copy.deepcopy(data)
        self._after_change()

    def set_project(self, project: Project, path: Optional[Path] = None) -> None:
        self._undo.clear()
        self._redo.clear()
        self._data = project.model_dump(mode="json")
        self._path = Path(path) if path else None
        self.dirty = False
        self._validate()
        self.changed.emit()
        self.path_changed.emit(str(self._path) if self._path else "")

    def undo(self) -> None:
        if self._undo:
            self._redo.append(copy.deepcopy(self._data))
            self._data = self._undo.pop()
            self._after_change(push=False)

    def redo(self) -> None:
        if self._redo:
            self._undo.append(copy.deepcopy(self._data))
            self._data = self._redo.pop()
            self._after_change(push=False)

    def _push_undo(self) -> None:
        self._undo.append(copy.deepcopy(self._data))
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()

    def _after_change(self, push: bool = True) -> None:
        self.dirty = True
        self._validate()
        self.changed.emit()

    def _validate(self) -> None:
        try:
            Project.model_validate(self._data)
            self._valid, self._error = True, ""
        except Exception as exc:  # noqa: BLE001
            self._valid, self._error = False, str(exc)
        self.validity_changed.emit(self._valid, self._error)

    # ------------------------------------------------------------------ files
    def load(self, path: str | Path) -> None:
        self.set_project(load_project(path), Path(path))

    def save(self, path: Optional[str | Path] = None) -> Path:
        target = Path(path) if path else self._path
        if target is None:
            raise ValueError("no path")
        save_project(self.project(), target)
        self._path = target
        self.dirty = False
        self.path_changed.emit(str(target))
        return target
