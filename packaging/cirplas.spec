# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds the GUI (cirplas-gui) and the worker (cirplas-solve) into one onedir bundle.

Usage (from the repository root):  pyinstaller packaging/cirplas.spec
The ngspice shared library and its runtime DLLs are copied from packaging/ngspice/ (see the CI workflow).
"""

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
NGSPICE_DIR = ROOT / "packaging" / "ngspice"

# bundled data (material / gas databases, example projects)
datas = [(str(ROOT / "cirplas" / "data"), "cirplas/data")]
binaries = []
if NGSPICE_DIR.exists():
    for f in NGSPICE_DIR.iterdir():
        if f.suffix.lower() in (".dll", ".so", ".dylib"):
            binaries.append((str(f), "."))
        elif f.is_file():
            datas.append((str(f), "."))

# Qt modules the GUI does not use (keeps the bundle small)
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtQml", "QtQuick", "QtQuick3D",
    "QtQuickControls2", "QtQuickWidgets", "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtMultimedia", "QtMultimediaWidgets", "QtCharts", "QtDataVisualization", "QtGraphs", "QtPdf", "QtPdfWidgets", "QtLocation",
    "QtPositioning", "QtRemoteObjects", "QtSensors", "QtSerialPort", "QtSerialBus", "QtTest", "QtBluetooth", "QtNfc", "QtDesigner",
    "QtHelp", "QtSql", "QtNetworkAuth", "QtScxml", "QtStateMachine", "QtTextToSpeech", "QtSpatialAudio", "QtHttpServer", "QtUiTools",
    "QtOpenGLWidgets", "QtAxContainer", "QtAsyncio",
)]
hidden = ["h5py.defs", "h5py.utils", "h5py.h5ac", "h5py._proxy", "scipy.optimize", "cirplas.core.analysis.tran", "cirplas.core.results"]

a_gui = Analysis(
    [str(ROOT / "packaging" / "entry_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["cirplas.gui.app"],
    excludes=["tkinter", "matplotlib", "IPython", "notebook"] + QT_EXCLUDES,
    noarchive=False,
)
a_cli = Analysis(
    [str(ROOT / "packaging" / "entry_solve.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "notebook", "PySide6", "pyqtgraph"],
    noarchive=False,
)
MERGE((a_gui, "cirplas-gui", "cirplas-gui"), (a_cli, "cirplas-solve", "cirplas-solve"))

pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="cirplas-gui", console=False,
              icon=str(ROOT / "packaging" / "cirplas.ico") if (ROOT / "packaging" / "cirplas.ico").exists() else None)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="cirplas-solve", console=True)
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas, strip=False, upx=False, name="CirPlas")
