"""CirPlas GUI entry point."""

from __future__ import annotations

import sys
from importlib import resources

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..core.project import default_project
from . import theme
from .mainwindow import MainWindow
from .project_model import ProjectModel


def app_icon() -> QIcon:
    return QIcon(str(resources.files("cirplas").joinpath("data", "cirplas_icon.png")))


def create_app(argv=None) -> QApplication:
    """Create the QApplication with the persisted theme applied (shared by the GUI and the tests)."""
    argv = list(sys.argv if argv is None else argv)
    QCoreApplication.setOrganizationName("CirPlas")
    QCoreApplication.setApplicationName("CirPlas")
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("CirPlas")
    app.setOrganizationName("CirPlas")
    app.setWindowIcon(app_icon())
    mgr = theme.manager()
    mgr.load_settings()
    mgr.apply(app)
    return app


def show_unhandled(exc_type, exc, tb) -> None:
    """A windowed build has no console: without this an exception in a slot would look like nothing happened."""
    import traceback

    from PySide6.QtWidgets import QMessageBox

    text = "".join(traceback.format_exception(exc_type, exc, tb))
    if sys.stderr is not None:
        sys.stderr.write(text)
    QMessageBox.critical(None, "内部エラー", f"{exc_type.__name__}: {exc}\n\n{text[-1500:]}")


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if sys.platform == "win32":  # otherwise a source run is grouped under python.exe and shows its taskbar icon
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CirPlas.CirPlas")
    app = create_app(argv)
    sys.excepthook = show_unhandled
    model = ProjectModel(default_project())
    win = MainWindow(model)
    if len(argv) > 1 and argv[1].endswith(".json"):
        model.load(argv[1])
    win.show()
    QTimer.singleShot(0, win.offer_restore)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
