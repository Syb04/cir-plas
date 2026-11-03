"""Automatic saving of the edited project and recovery after a crash or an unsaved close (spec 04 section 7).

While the project has unsaved changes, a snapshot (project dict, its file path, time) is written periodically to the
user data folder. Saving the project or closing it without changes removes the snapshot; a snapshot left behind is
offered for restoration at the next start."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QStandardPaths, QTimer

from .project_model import ProjectModel

FILE_NAME = "autosave.cirplas.json"


def default_directory() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or str(Path.home() / ".cirplas")
    return Path(base) / "autosave"


class AutoSaver(QObject):
    def __init__(self, model: ProjectModel, directory: Optional[Path] = None, interval_ms: int = 60_000, parent=None):
        super().__init__(parent)
        self.model = model
        self.directory = Path(directory) if directory else default_directory()
        self.file = self.directory / FILE_NAME
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.save_now)
        self.timer.start()

    def save_now(self) -> bool:
        """Write a snapshot if there are unsaved changes; returns True when one was written."""
        if not self.model.dirty:
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        snap = {"saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "path": str(self.model.path) if self.model.path else None,
                "valid": self.model.is_valid, "data": self.model.data}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)  # atomic: a crash while writing never leaves a truncated snapshot
        return True

    def clear(self) -> None:
        try:
            self.file.unlink()
        except FileNotFoundError:
            pass

    def pending(self) -> Optional[dict]:
        """The snapshot left by a previous session, if any."""
        try:
            snap = json.loads(self.file.read_text(encoding="utf-8"))
            return snap if isinstance(snap.get("data"), dict) else None
        except (FileNotFoundError, ValueError):
            return None

    def restore(self, snap: dict) -> None:
        """Load the snapshot into the model (marked as unsaved; its original path is kept)."""
        self.model.replace(snap["data"])
        if snap.get("path"):
            self.model._path = Path(snap["path"])
            self.model.path_changed.emit(snap["path"])
        self.model.dirty = True
