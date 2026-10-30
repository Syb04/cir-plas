"""Main window: full-height settings pane (left), central tabs, run pane (right)."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..core.project import default_project
from . import theme
from .panels.circuit import CircuitPanel
from .panels.compare import ComparePanel
from .panels.geometry import GeometryPanel
from .panels.numerics import NumericsPanel
from .panels.plasma import PlasmaPanel
from .panels.results import ResultsPanel
from .panels.run import RunPanel
from .panels.sweep import SweepPanel
from .project_model import ProjectModel
from .widgets.crosssection import CrossSectionView
from .widgets.schematic import CircuitEditor

STATE_KEY = "ui/mainwindow_state_v2"
GEOMETRY_KEY = "ui/mainwindow_geometry_v2"


def _scroll(w: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(w)
    return s


class NetlistPreview(QWidget):
    def __init__(self, model: ProjectModel, parent=None):
        super().__init__(parent)
        self.model = model
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("AC（線形）", "ac")
        self.mode.addItem("TRAN（非線形）", "tran")
        btn = QPushButton("再生成")
        btn.clicked.connect(self.regenerate)
        self.info = QLabel("")
        self.info.setProperty("role", "muted")
        top.addWidget(QLabel("モード"))
        top.addWidget(self.mode)
        top.addWidget(btn)
        top.addWidget(self.info, 1)
        lay.addLayout(top)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setProperty("mono", "true")
        lay.addWidget(self.text, 1)

    def regenerate(self) -> None:
        try:
            from ..core.analysis.common import build_model, initial_estimates
            from ..core.mesh import build_radial_mesh
            from ..core.netlist.builder import AcSettings, NetlistBuilder, TranSettings

            p = self.model.project()
            n = build_radial_mesh(p.geometry, p.numerics.mesh).n_cells
            model = build_model(p, initial_estimates(p, n))
            if self.mode.currentData() == "ac":
                net, idx = NetlistBuilder(model, "ac", ac=AcSettings(frequency=p.f_max())).build()
            else:
                T = p.base_period()
                tmax = (1.0 / p.f_max()) / p.numerics.tran.pts_per_cycle
                net, idx = NetlistBuilder(model, "tran", tran=TranSettings(tmax, p.numerics.tran.cycles_min * T, tmax, p.numerics.tran.ramp_cycles / p.f_max())).build()
            self.text.setPlainText(net)
            self.info.setText(f"{len(net.splitlines())} 行, セル {n}, N_z {p.numerics.mesh.n_z}")
        except Exception as exc:  # noqa: BLE001
            self.text.setPlainText(f"生成できません: {exc}")


class MainWindow(QMainWindow):
    def __init__(self, model: ProjectModel | None = None, restore_state: bool = True):
        super().__init__()
        self.model = model or ProjectModel(default_project())
        self.setWindowTitle(f"CirPlas {__version__}")
        self.resize(1680, 980)
        # side docks take the full height; nothing is docked at the top/bottom
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.TopRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowNestedDocks)
        # central tabs
        self.cross = CrossSectionView()
        self.schematic = CircuitEditor(self.model)
        self.netlist = NetlistPreview(self.model)
        self.results = ResultsPanel()
        self.sweep = SweepPanel(self.model)
        self.compare = ComparePanel()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setProperty("flat", "true")
        self.tabs.addTab(self.cross, "断面図")
        self.tabs.addTab(self.schematic, "回路図")
        self.tabs.addTab(self.netlist, "ネットリスト")
        self.tabs.addTab(self.results, "結果")
        self.tabs.addTab(self.sweep, "スイープ")
        self.tabs.addTab(self.compare, "比較")
        self.sweep.open_result.connect(self._show_results)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(8, 6, 8, 8)
        cl.addWidget(self.tabs)
        self.setCentralWidget(central)
        # left dock: parameter panels, full height
        self.params = QTabWidget()
        self.params.setDocumentMode(True)
        self.params.setProperty("flat", "true")
        self.params.addTab(_scroll(GeometryPanel(self.model)), "幾何")
        self.params.addTab(_scroll(PlasmaPanel(self.model)), "プラズマ")
        self.params.addTab(_scroll(CircuitPanel(self.model)), "回路")
        self.params.addTab(_scroll(NumericsPanel(self.model)), "数値設定")
        self.dock_settings = QDockWidget("設定", self)
        self.dock_settings.setObjectName("dock_settings")
        self.dock_settings.setWidget(self.params)
        self.dock_settings.setMinimumWidth(480)
        self.dock_settings.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_settings)
        # right dock: run pane
        self.run_panel = RunPanel(self.model)
        self.dock_run = QDockWidget("実行", self)
        self.dock_run.setObjectName("dock_run")
        self.dock_run.setWidget(self.run_panel)
        self.dock_run.setMinimumWidth(340)
        self.dock_run.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_run)
        self.resizeDocks([self.dock_settings, self.dock_run], [520, 380], Qt.Orientation.Horizontal)
        self.run_panel.results_ready.connect(self._show_results)
        self._menus()
        self.model.changed.connect(self._on_model_changed)
        self.model.validity_changed.connect(self._on_validity)
        self.model.path_changed.connect(lambda p: self._update_title())
        theme.manager().changed.connect(self._on_theme_changed)
        self._on_model_changed()
        self._update_title()
        self._persist_state = restore_state
        if restore_state:
            self._restore_state()

    # ------------------------------------------------------------------ menus
    def _menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("ファイル(&F)")
        for text, slot, key in (("新規(&N)", self.new_project, QKeySequence.StandardKey.New), ("開く(&O)…", self.open_project, QKeySequence.StandardKey.Open),
                                ("保存(&S)", self.save_project, QKeySequence.StandardKey.Save), ("名前を付けて保存(&A)…", self.save_project_as, QKeySequence.StandardKey.SaveAs)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("結果ファイルを開く(&R)…", self)
        a.triggered.connect(self.open_results)
        m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("終了(&X)", self)
        a.setShortcut(QKeySequence.StandardKey.Quit)
        a.triggered.connect(self.close)
        m_file.addAction(a)
        m_edit = mb.addMenu("編集(&E)")
        a = QAction("元に戻す(&U)", self)
        a.setShortcut(QKeySequence.StandardKey.Undo)
        a.triggered.connect(self.model.undo)
        m_edit.addAction(a)
        a = QAction("やり直す(&R)", self)
        a.setShortcut(QKeySequence.StandardKey.Redo)
        a.triggered.connect(self.model.redo)
        m_edit.addAction(a)
        m_view = mb.addMenu("表示(&V)")
        m_theme = m_view.addMenu("テーマ")
        self.theme_actions = QActionGroup(self)
        self.theme_actions.setExclusive(True)
        for mode, text in (("system", "OS 設定に従う"), ("light", "ライト"), ("dark", "ダーク")):
            a = QAction(text, self)
            a.setCheckable(True)
            a.setData(mode)
            a.setChecked(theme.manager().mode == mode)
            a.triggered.connect(lambda _c, m=mode: theme.manager().set_mode(m))
            self.theme_actions.addAction(a)
            m_theme.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.dock_settings.toggleViewAction())
        m_view.addAction(self.dock_run.toggleViewAction())
        a = QAction("レイアウトを初期化", self)
        a.triggered.connect(self.reset_layout)
        m_view.addAction(a)
        m_run = mb.addMenu("実行(&R)")
        a = QAction("実行 (選択モード)", self)
        a.setShortcut(QKeySequence(Qt.Key.Key_F5))
        a.triggered.connect(lambda: self.run_panel.run())
        m_run.addAction(a)
        a = QAction("AC 解析", self)
        a.triggered.connect(lambda: self.run_panel.run("ac"))
        m_run.addAction(a)
        a = QAction("TRAN 解析", self)
        a.triggered.connect(lambda: self.run_panel.run("tran"))
        m_run.addAction(a)
        a = QAction("SC 解析（密度自己無撞着）", self)
        a.triggered.connect(lambda: self.run_panel.run("sc"))
        m_run.addAction(a)
        a = QAction("中止", self)
        a.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        a.triggered.connect(self.run_panel.manager.cancel)
        m_run.addAction(a)
        m_help = mb.addMenu("ヘルプ(&H)")
        a = QAction("CirPlas について", self)
        a.triggered.connect(lambda: QMessageBox.about(self, "CirPlas", f"CirPlas {__version__}\nCCP 装置向けプラズマ回路シミュレータ（ngspice バックエンド）"))
        m_help.addAction(a)

    # ------------------------------------------------------------------ window state
    def _restore_state(self) -> None:
        s = QSettings()
        geo = s.value(GEOMETRY_KEY)
        state = s.value(STATE_KEY)
        try:
            if geo is not None:
                self.restoreGeometry(geo)
            if state is not None:
                self.restoreState(state)
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:
        if self._persist_state:
            s = QSettings()
            s.setValue(GEOMETRY_KEY, self.saveGeometry())
            s.setValue(STATE_KEY, self.saveState())
        super().closeEvent(event)

    def reset_layout(self) -> None:
        for dock, area in ((self.dock_settings, Qt.DockWidgetArea.LeftDockWidgetArea), (self.dock_run, Qt.DockWidgetArea.RightDockWidgetArea)):
            dock.setFloating(False)
            dock.show()
            self.addDockWidget(area, dock)
        self.resizeDocks([self.dock_settings, self.dock_run], [520, 380], Qt.Orientation.Horizontal)

    # ------------------------------------------------------------------ slots
    def _on_theme_changed(self, mode: str) -> None:
        for a in self.theme_actions.actions():
            a.setChecked(a.data() == theme.manager().mode)
        self.cross.redraw()
        for panel in (self.results.radial, self.results.wave, self.results.map, self.results.spectrum, self.compare, self.sweep):
            try:
                panel.redraw()
            except Exception:  # noqa: BLE001
                pass
        self.run_panel.restyle()

    def _on_model_changed(self) -> None:
        try:
            p = self.model.project()
            from ..core.mesh import build_radial_mesh

            edges = build_radial_mesh(p.geometry, p.numerics.mesh).r_edges
            self.cross.set_project(p, edges)
        except Exception:  # noqa: BLE001
            pass

    def _on_validity(self, ok: bool, msg: str) -> None:
        if ok:
            self.statusBar().showMessage("プロジェクトは有効です")
        else:
            self.statusBar().showMessage("検証エラー: " + msg.splitlines()[0][:160])

    def _update_title(self) -> None:
        name = self.model.path.name if self.model.path else "無題"
        self.setWindowTitle(f"CirPlas {__version__} — {name}")

    def _show_results(self, path: str) -> None:
        try:
            self.results.load(path)
            self.compare.add_result(path)
            self.tabs.setCurrentWidget(self.results)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "結果の読み込み", str(exc))

    def new_project(self) -> None:
        self.model.set_project(default_project())

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "プロジェクトを開く", "", "CirPlas project (*.cirplas.json *.json)")
        if path:
            try:
                self.model.load(path)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "読み込みエラー", str(exc))

    def save_project(self) -> None:
        if self.model.path is None:
            self.save_project_as()
            return
        try:
            self.model.save()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存エラー", str(exc))

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", "project.cirplas.json", "CirPlas project (*.cirplas.json)")
        if path:
            try:
                self.model.save(path)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "保存エラー", str(exc))

    def open_results(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "結果ファイルを開く", "", "HDF5 (*.h5)")
        if path:
            self._show_results(path)
