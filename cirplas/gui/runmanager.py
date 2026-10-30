"""Runs the worker CLI in a separate process and relays JSONL progress."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, Signal

from .project_model import ProjectModel


def worker_command() -> tuple[str, list[str]]:
    """Program and argument prefix that start the solver worker (frozen bundle or python -m)."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        name = "cirplas-solve.exe" if sys.platform.startswith("win") else "cirplas-solve"
        return str(exe_dir / name), []
    return sys.executable, ["-m", "cirplas.worker.cli"]


class RunManager(QObject):
    started = Signal(str)  # run directory
    progress = Signal(dict)
    log = Signal(str)
    finished = Signal(str)  # output h5 path
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None
        self.run_dir: Optional[Path] = None
        self.output: Optional[Path] = None
        self._buf = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def start(self, model: ProjectModel, mode: str, base_dir: Optional[Path] = None) -> Path:
        if self.running:
            raise RuntimeError("a run is already in progress")
        project = model.project()
        base = base_dir or (model.path.parent if model.path else Path.cwd())
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.run_dir = base / "runs" / f"{stamp}_{mode}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        proj_path = self.run_dir / "project.cirplas.json"
        proj_path.write_text(project.to_json(), encoding="utf-8")
        self.output = self.run_dir / "run.h5"
        self.cancel_file = self.run_dir / "CANCEL"
        args = [str(proj_path), "-o", str(self.output), "--mode", mode, "--progress", "jsonl",
                "--netlist-out", str(self.run_dir / "netlist.cir"), "--cancel-file", str(self.cancel_file)]
        self.proc = QProcess(self)
        program, prefix = worker_command()
        self.proc.setProgram(program)
        self.proc.setArguments(prefix + args)
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self._buf = ""
        self.proc.start()
        self.started.emit(str(self.run_dir))
        self.log.emit(f"開始: {mode}  →  {self.run_dir}")
        return self.run_dir

    def cancel(self) -> None:
        if self.running and self.cancel_file is not None:
            self.cancel_file.write_text("cancel", encoding="utf-8")
            self.log.emit("中止を要求しました…")

    def _on_stdout(self) -> None:
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                self.log.emit(line)
                continue
            if "log" in d:
                self.log.emit(str(d["log"]))
            if "error" in d:
                self.log.emit("ERROR: " + str(d["error"]))
            self.progress.emit(d)

    def _on_stderr(self) -> None:
        data = bytes(self.proc.readAllStandardError()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.log.emit(line)

    def _on_finished(self, code: int, status) -> None:
        if code == 0 and self.output is not None and self.output.exists():
            self.finished.emit(str(self.output))
        else:
            self.failed.emit(f"終了コード {code}")
