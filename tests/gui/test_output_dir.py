"""Where runs are written: an installed application starts in the protected Program Files folder, so the current
directory must never be used, and a failed start must be visible."""

import os
import subprocess
import sys
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def model():
    from cirplas.core.project import default_project
    from cirplas.gui.project_model import ProjectModel

    return ProjectModel(default_project())


def test_output_base_never_uses_the_current_directory(model, tmp_path, monkeypatch):
    from cirplas.gui import runmanager

    docs = tmp_path / "Documents" / "CirPlas"
    monkeypatch.setattr(runmanager, "default_output_base", lambda: docs)
    cwd = tmp_path / "ProgramFiles"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert runmanager.output_base(model) == docs  # new project without a path

    project_dir = tmp_path / "work"
    project_dir.mkdir()
    model.save(project_dir / "p.cirplas.json")
    assert runmanager.output_base(model) == project_dir  # next to the project file

    # project in a protected folder (e.g. an example under Program Files): fall back to the documents
    real = runmanager.is_writable
    monkeypatch.setattr(runmanager, "is_writable", lambda d: d != project_dir and real(d))
    assert runmanager.output_base(model) == docs


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL")
def test_is_writable_returns_for_a_protected_folder(tmp_path):
    """A folder denied by its ACL (like Program Files) must give False promptly, not hang."""
    from cirplas.gui.runmanager import is_writable

    locked = tmp_path / "locked"
    locked.mkdir()
    user = os.environ["USERNAME"]
    subprocess.run(["icacls", str(locked), "/deny", f"{user}:(WD,AD)"], check=True, capture_output=True)
    try:
        result = []
        t = threading.Thread(target=lambda: result.append(is_writable(locked)), daemon=True)
        t.start()
        t.join(20)
        assert result == [False]
    finally:
        subprocess.run(["icacls", str(locked), "/remove:d", user], capture_output=True)


def test_failed_start_is_visible_and_recoverable(qtbot, model, monkeypatch):
    from cirplas.gui import runmanager
    from cirplas.gui.panels.run import RunPanel

    def denied(_):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(runmanager, "output_base", denied)
    panel = RunPanel(model)
    qtbot.addWidget(panel)
    panel.run("ac")
    assert "起動失敗" in panel.status.text() and "denied" in panel.log.toPlainText()
    assert panel.btn_run.isEnabled() and not panel._tick.isActive() and panel.eta.text() == ""


def test_worker_that_cannot_start_reports_failure(qtbot, model, tmp_path, monkeypatch):
    from cirplas.gui import runmanager
    from cirplas.gui.panels.run import RunPanel

    monkeypatch.setattr(runmanager, "default_output_base", lambda: tmp_path)
    monkeypatch.setattr(runmanager, "worker_command", lambda: (str(tmp_path / "missing.exe"), []))
    panel = RunPanel(model)
    qtbot.addWidget(panel)
    panel.run("ac")
    qtbot.waitUntil(lambda: panel.btn_run.isEnabled(), timeout=10000)
    assert "失敗" in panel.status.text()


def test_run_without_a_project_path_completes(qtbot, model, tmp_path, monkeypatch):
    """The reported case: a new project, started from a folder that cannot be written."""
    from cirplas.gui import runmanager
    from cirplas.gui.panels.run import RunPanel

    monkeypatch.setattr(runmanager, "default_output_base", lambda: tmp_path / "CirPlas")
    panel = RunPanel(model)
    qtbot.addWidget(panel)
    done = []
    panel.results_ready.connect(done.append)
    panel.run("ac")
    qtbot.waitUntil(lambda: bool(done), timeout=120000)
    assert done[0].startswith(str(tmp_path / "CirPlas" / "runs")) and os.path.exists(done[0])


def test_app_icon_loads_from_the_package_data():
    from cirplas.gui.app import app_icon, create_app

    app = create_app(["cirplas"])
    assert not app_icon().isNull() and not app.windowIcon().isNull()
    assert app_icon().pixmap(16, 16).width() == 16
