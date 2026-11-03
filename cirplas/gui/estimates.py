"""Background problem-size / run-time estimates for the GUI (numerics panel, run pane ETA).

`core.estimate.estimate` builds the model and the netlist (about a second for the representative case), so it runs in
the thread pool; the cost coefficients are calibrated from finished runs and kept in the user settings."""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Signal

from ..core.estimate import DEFAULT_COEFFICIENTS, Estimate, calibrate, estimate

SETTINGS_KEY = "benchmark/coefficients"


def load_coefficients() -> dict:
    try:
        raw = QSettings().value(SETTINGS_KEY)
        return {**DEFAULT_COEFFICIENTS, **(json.loads(raw) if raw else {})}
    except (ValueError, TypeError):
        return dict(DEFAULT_COEFFICIENTS)


def save_coefficients(coef: dict) -> None:
    QSettings().setValue(SETTINGS_KEY, json.dumps(coef))


def record_run(mode: str, unknowns: int, wall_s: float, tran_points: int = 0) -> dict:
    """Calibrate the stored coefficients with a finished run; returns the new coefficients."""
    coef = calibrate(load_coefficients(), mode, unknowns, wall_s, tran_points)
    save_coefficients(coef)
    return coef


class _Job(QRunnable):
    def __init__(self, runner: "EstimateRunner", seq: int, data: dict, coef: dict):
        super().__init__()
        self.runner, self.seq, self.data, self.coef = runner, seq, data, coef

    def run(self) -> None:
        from ..core.project import Project

        try:
            est, error = estimate(Project.model_validate(self.data), self.coef), ""
        except Exception as exc:  # noqa: BLE001
            est, error = None, str(exc)
        try:
            self.runner._done.emit(self.seq, est, error)
        except RuntimeError:  # the runner was deleted while the job ran (window closed)
            pass


class EstimateRunner(QObject):
    """Debounced asynchronous estimate of a project dict; only the latest request is reported."""

    ready = Signal(object)  # Estimate
    failed = Signal(str)
    _done = Signal(int, object, str)

    def __init__(self, delay_ms: int = 600, parent=None):
        super().__init__(parent)
        self._seq = 0
        self._pending: Optional[dict] = None
        self.last: Optional[Estimate] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._start)
        self._done.connect(self._on_done)

    def request(self, data: dict, immediate: bool = False) -> None:
        self._pending = json.loads(json.dumps(data))  # detached copy (the job runs in another thread)
        self._seq += 1
        if immediate:
            self._timer.stop()
            self._start()
        else:
            self._timer.start()

    def _start(self) -> None:
        if self._pending is None:
            return
        QThreadPool.globalInstance().start(_Job(self, self._seq, self._pending, load_coefficients()))
        self._pending = None

    def _on_done(self, seq: int, est, error: str) -> None:
        if seq != self._seq:
            return  # superseded by a newer request
        if est is None:
            self.failed.emit(error)
        else:
            self.last = est
            self.ready.emit(est)
