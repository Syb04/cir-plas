"""Cross-section drawing of the reactor (r-z) with the radial mesh overlay."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from ...core.project import Project
from .. import theme

COLORS = {
    "W": QColor("#b0bec5"), "F": QColor("#90a4ae"), "G": QColor("#78909c"),
    "metal_top": QColor("#b0bec5"), "plasma": QColor("#e3f2fd"), "sheath": QColor("#bbdefb"),
}
MATERIAL_COLORS = {
    "al2o3": "#ffe0b2", "aln": "#ffe0b2", "quartz": "#e1bee7", "y2o3": "#f8bbd0", "si_wafer_p": "#9fa8da",
    "si_wafer_hr": "#9fa8da", "si_fr": "#7986cb", "sic": "#5c6bc0", "conductor": "#b0bec5", "vacuum": "#ffffff",
}


class CrossSectionView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._z_scale = 3.0  # vertical exaggeration
        self.show_mesh = True
        self._project: Project | None = None

    def wheelEvent(self, event):  # zoom
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def set_project(self, project: Project | None, r_edges=None) -> None:
        self._project = project
        self._r_edges = r_edges
        self.redraw()

    def redraw(self) -> None:
        sc = self.scene()
        sc.clear()
        t = theme.tokens()
        self.setBackgroundBrush(QBrush(QColor(t["bg"])))
        p = self._project
        if p is None:
            return
        geo = p.geometry
        S = 1000.0  # m -> mm
        Z = self._z_scale
        pen_thin = QPen(QColor(t["text_muted"]), 0)
        ink = QColor("#141413")  # labels drawn over the (always light) material fills
        # reference datum: wafer-region bottom conductor surface at z = 0 (drawn as y = 0, y grows downward -> negative)
        r_in = 0.0
        y_top_plate = None
        for reg in geo.regions:
            r0, r1 = r_in * S, reg.r_out * S
            # bottom conductor block (thickness drawn 4 mm)
            z0 = reg.z_offset
            rect = QRectF(r0, -(z0) * S * Z, r1 - r0, 4.0 * Z)
            sc.addRect(rect, pen_thin, QBrush(COLORS[reg.conductor]))
            if r1 - r0 > 20:
                label = sc.addText(f"{reg.name} ({reg.conductor})", QFont("sans", 7))
                label.setDefaultTextColor(ink)
                label.setPos(0.5 * (r0 + r1) - 20, -(z0) * S * Z - 2)
            z = z0
            for layer in reg.stack:
                name = layer.material if isinstance(layer.material, str) else "custom"
                col = QColor(MATERIAL_COLORS.get(name, "#cfd8dc"))
                rect = QRectF(r0, -(z + layer.t) * S * Z, r1 - r0, layer.t * S * Z)
                sc.addRect(rect, pen_thin, QBrush(col))
                z += layer.t
            # plasma gap above this region
            gap = geo.local_gap(reg, 0.5 * (r_in + reg.r_out))
            rect = QRectF(r0, -(z + gap) * S * Z, r1 - r0, gap * S * Z)
            sc.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(COLORS["plasma"]))
            y_top_plate = -(z + gap) * S * Z
            r_in = reg.r_out
        # top stack and top conductor (flat)
        r_wall = geo.r_wall * S
        z = geo.reference_region().z_surface_abs + geo.gap_at(0.0)
        for layer in reversed(geo.top.stack):  # listed top->bottom; draw bottom-up
            name = layer.material if isinstance(layer.material, str) else "custom"
            rect = QRectF(0, -(z + layer.t) * S * Z, r_wall, layer.t * S * Z)
            sc.addRect(rect, pen_thin, QBrush(QColor(MATERIAL_COLORS.get(name, "#cfd8dc"))))
            z += layer.t
        rect = QRectF(0, -(z + 4e-3) * S * Z, r_wall, 4.0 * Z)
        sc.addRect(rect, pen_thin, QBrush(COLORS["metal_top"]))
        top_label = sc.addText("上部電極 " + ("(接地)" if geo.top.conductor == "ground" else "(駆動 T)"), QFont("sans", 8))
        top_label.setDefaultTextColor(QColor(t["text"]))
        top_label.setPos(2, -(z + 4e-3) * S * Z - 16)
        # axis / wall
        sc.addLine(0, 20, 0, -(z + 8e-3) * S * Z, QPen(QColor(t["text_muted"]), 0, Qt.PenStyle.DashLine))
        sc.addLine(r_wall, 20, r_wall, -(z + 8e-3) * S * Z, QPen(QColor(t["text"]), 0))
        # mesh lines
        if self.show_mesh and self._r_edges is not None:
            mesh_color = QColor(t["text"])
            mesh_color.setAlpha(70)
            pen = QPen(mesh_color, 0)
            for r in self._r_edges[1:-1]:
                sc.addLine(r * S, 12, r * S, y_top_plate if y_top_plate is not None else -60, pen)
        # radius ticks
        for r_mm in range(0, int(r_wall) + 1, 50):
            sc.addLine(r_mm, 12, r_mm, 18, pen_thin)
            lab = sc.addText(f"{r_mm}", QFont("sans", 7))
            lab.setDefaultTextColor(QColor(t["text_muted"]))
            lab.setPos(r_mm - 8, 18)
        lab = sc.addText("r [mm]", QFont("sans", 7))
        lab.setDefaultTextColor(QColor(t["text_muted"]))
        lab.setPos(r_wall - 30, 30)
        self.setSceneRect(sc.itemsBoundingRect().adjusted(-10, -10, 10, 10))
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene().items():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
