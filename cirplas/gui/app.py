"""CirPlas GUI entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ..core.project import default_project
from . import theme
from .mainwindow import MainWindow
from .project_model import ProjectModel


def create_app(argv=None) -> QApplication:
    """Create the QApplication with the persisted theme applied (shared by the GUI and the tests)."""
    argv = list(sys.argv if argv is None else argv)
    QCoreApplication.setOrganizationName("CirPlas")
    QCoreApplication.setApplicationName("CirPlas")
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("CirPlas")
    app.setOrganizationName("CirPlas")
    mgr = theme.manager()
    mgr.load_settings()
    mgr.apply(app)
    return app


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = create_app(argv)
    model = ProjectModel(default_project())
    win = MainWindow(model)
    if len(argv) > 1 and argv[1].endswith(".json"):
        model.load(argv[1])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
