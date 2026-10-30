"""CirPlas GUI entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..core.project import default_project
from .mainwindow import MainWindow
from .project_model import ProjectModel


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    import pyqtgraph as pg

    pg.setConfigOptions(background="w", foreground="k", antialias=True)
    app = QApplication(argv)
    app.setApplicationName("CirPlas")
    app.setOrganizationName("CirPlas")
    model = ProjectModel(default_project())
    win = MainWindow(model)
    if len(argv) > 1 and argv[1].endswith(".json"):
        model.load(argv[1])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
