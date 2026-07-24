"""Dewarp stage: flatten a photographed page before tracing.

A first-class stage widget (swapped into the main window's central
stack, not a modal dialog) with the studio's left-right layout:

    +-----------------------------+-----------------------------+
    |  source photo (left)        |  live dewarped result       |
    |  quad corners OR spline     |  (right, updates live)      |
    |  outline, both draggable    |                             |
    +-----------------------------+-----------------------------+
    | mode / [Auto-detect] size controls / options [Apply][X]  |
    +----------------------------------------------------------+

Two modes:

* QUAD (4 corners) - drop/drag four page corners; the right pane shows
  ``core.dewarp.dewarp_quad`` (straight perspective transform) live.
* CURVED PAGE (spline) - a full page outline (4 corner anchors + curved
  top/bottom edges, each with an on-curve mid point, mirrored tangent
  tips and per-corner tangent tips). The right pane shows
  ``core.dewarp.dewarp_page`` (perspective + cylinder unwrap). Preview
  renders on a downscaled copy for responsiveness (updated when a drag
  ends); Apply renders at full quality. "Refine with text lines" bends
  the outline so its isolines match the printed lines.

"Auto-detect" seeds either mode from the silhouette detector.

Usage: construct :class:`DewarpStageWidget` once, call
:meth:`set_source_image` when entering the stage, and listen for
:attr:`applied` (read :meth:`result_image` for the flattened BGR
ndarray) and :attr:`cancelled`. Both panes auto-fit the image to the
available space on show/resize until the user zooms manually.

NOTE: PySide6 cannot run in the porting sandbox, so this module is
static-checked only. Expect to test-drive and adjust it on a real desktop.
"""

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 always present in the app
    cv2 = None

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGraphicsEllipseItem,
                               QGraphicsItem, QGraphicsPolygonItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from ..core import dewarp as dw

MODE_QUAD = 0
MODE_SPLINE = 1

HANDLE_R = 7          # corner handle radius, in view pixels
TIP_R = 5             # tangent-tip handle radius, in view pixels
HIT_R = 12            # click tolerance for grabbing a handle, view pixels
PREVIEW_H = 700       # spline preview renders at this image height

_CORNER_COLOR = QColor("#22cc66")
_LINE_COLOR = QColor("#22cc66")
_TOP_COLOR = QColor("#50dc50")
_BOT_COLOR = QColor("#ffb450")
_TIP_COLOR = QColor("#ffffff")

# scoped enum name, robust across PySide6 versions
_IGNORE_XFORM = QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations


def ndarray_to_qimage(bgr):
    """Convert a BGR (OpenCV) uint8 ndarray to a QImage (RGB888).

    A copy is returned so the QImage owns its buffer (the source array may
    be freed). Grayscale (2-D) input is accepted too.
    """
    arr = np.ascontiguousarray(bgr)
    if arr.ndim == 2:
        h, w = arr.shape
        img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
        return img.copy()
    h, w, ch = arr.shape
    if ch == 4:
        rgb = arr[:, :, [2, 1, 0, 3]]
        rgb = np.ascontiguousarray(rgb)
        img = QImage(rgb.data, w, h, 4 * w, QImage.Format_RGBA8888)
        return img.copy()
    rgb = np.ascontiguousarray(arr[:, :, ::-1])   # BGR -> RGB
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


def ndarray_to_qpixmap(bgr):
    return QPixmap.fromImage(ndarray_to_qimage(bgr))


class _AutoFitView(QGraphicsView):
    """QGraphicsView that keeps its image fitted to the viewport.

    set_image is often called before the widget has real geometry (the
    stage may be hidden in a stacked layout), so a one-shot fitInView at
    that moment computes against a tiny default viewport and the image
    shows up minuscule. Instead, refit on every show/resize until the
    user zooms manually (wheel); a new image re-arms auto-fit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = None
        self._user_zoomed = False
        self._panning = False
        self._pan_last = None
        self.setBackgroundBrush(QBrush(QColor("#202020")))
        # zoom toward the cursor rather than the view center
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def fit(self):
        if self._pix_item is not None:
            self.fitInView(self._pix_item, Qt.KeepAspectRatio)

    def refit(self):
        """Fit and re-arm auto-fit (forgets any manual zoom)."""
        self._user_zoomed = False
        self.fit()

    def zoom_in(self):
        self._user_zoomed = True
        self.scale(1.25, 1.25)

    def zoom_out(self):
        self._user_zoomed = True
        self.scale(0.8, 0.8)

    def _autofit(self):
        if not self._user_zoomed:
            self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._autofit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._autofit()

    def wheelEvent(self, event):
        self._user_zoomed = True
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def _start_pan(self, view_pos):
        self._panning = True
        self._pan_last = view_pos
        self.setCursor(Qt.ClosedHandCursor)

    # middle-mouse drag pans in any mode (matches the trace canvas)
    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._start_pan(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            d = event.position() - self._pan_last
            self._pan_last = event.position()
            hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
            hbar.setValue(int(hbar.value() - d.x()))
            vbar.setValue(int(vbar.value() - d.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() in (Qt.MiddleButton,
                                                Qt.LeftButton):
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_pixmap(self, pm, rearm_fit=True):
        self._scene.clear()
        self._pix_item = self._scene.addPixmap(pm)
        # Margin around the image serves two purposes: the view can pan
        # PAST the image edges (corners near an edge can be brought to a
        # comfortable spot), and cursor-centered wheel zoom stays stable
        # when the image is smaller than the viewport (Qt force-centers
        # content that fits inside the scene rect otherwise).
        r = QRectF(pm.rect())
        m = 0.25 * max(r.width(), r.height())
        self.setSceneRect(r.adjusted(-m, -m, m, m))
        if rearm_fit:
            self._user_zoomed = False
        self.fit()


class _ResultView(_AutoFitView):
    """Read-only image pane with wheel zoom and fit-to-window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)

    def show_placeholder(self, text):
        self._scene.clear()
        self._pix_item = None
        t = self._scene.addText(text)
        t.setDefaultTextColor(QColor("#bbbbbb"))
        self.setSceneRect(t.boundingRect())

    def set_image(self, bgr):
        # keep the user's zoom while they tweak the outline; a fresh
        # source image (via _SourceView.set_image) re-arms auto-fit there
        self._set_pixmap(ndarray_to_qpixmap(bgr),
                         rearm_fit=not self._user_zoomed)


class _SourceView(_AutoFitView):
    """Photo pane hosting either quad corners or a spline page outline.

    QUAD mode: click empty space to drop a corner (up to four); drag a
    corner to move it. Emits :attr:`cornersChanged` on any edit. Once all
    four corners are placed, the quad can be PROMOTED to a spline outline:
    right-click a corner for a "convert to curved page" menu, or left-click
    ON the top or bottom edge line to promote AND pull the new on-curve
    mid point to the clicked spot (:attr:`promoteRequested`).

    SPLINE mode: shows a ``core.dewarp.PageModel`` outline with all its
    draggable handles (corner anchors, on-curve mids, mirrored tangent
    tips, per-corner tangent tips). Right-click a mid handle (or its
    tips) to BREAK the tangent into a V fold - the two tips then move
    independently, modeling the steep gutter curve at a book's center -
    or to re-smooth it. Emits :attr:`modelChanged` while dragging
    (overlay redraw) and :attr:`modelEdited` when a drag ends (preview
    re-render). Coordinates are image pixels throughout.
    """

    cornersChanged = Signal()
    modelChanged = Signal()
    modelEdited = Signal()
    # promotion out of quad mode:
    #   ("top"/"bottom", scene QPointF)  edge-line right-click: mid at click
    #   ("corner:tl" etc., None)         corner right-click: activate that
    #                                    corner's tangent handle
    promoteRequested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self._mode = MODE_QUAD
        # quad state
        self._corners = []       # list[QPointF] in scene/image coords
        self._drag = None        # quad: corner index | spline: handle tuple
        # spline state
        self._model = None       # core.dewarp.PageModel or None
        self._overlay = []       # all overlay items

    # ----- image -----------------------------------------------------------
    def set_image(self, bgr):
        self._overlay = []
        self._set_pixmap(ndarray_to_qpixmap(bgr))

    # ----- mode ------------------------------------------------------------
    def set_mode(self, mode):
        self._mode = mode
        self._drag = None
        self._redraw_overlay()

    # ----- quad corners ----------------------------------------------------
    def corners(self):
        return [(p.x(), p.y()) for p in self._corners]

    def set_corners(self, pts):
        self._corners = [QPointF(float(x), float(y)) for x, y in pts][:4]
        self._redraw_overlay()
        self.cornersChanged.emit()

    def clear_corners(self):
        self._corners = []
        self._redraw_overlay()
        self.cornersChanged.emit()

    # ----- spline model ----------------------------------------------------
    def model(self):
        return self._model

    def set_model(self, model):
        self._model = model
        self._redraw_overlay()
        self.modelChanged.emit()
        self.modelEdited.emit()

    # ----- interaction -----------------------------------------------------
    def _nearest_quad_corner(self, view_pos):
        best, best_d = None, HIT_R
        for i, p in enumerate(self._corners):
            d = (self.mapFromScene(p) - view_pos).manhattanLength()
            if d <= best_d:
                best, best_d = i, d
        return best

    def _edge_line_hit(self, view_pos):
        """If view_pos lies on the quad's top or bottom edge line (and not
        on a corner), return (edge_name, scene_point); else None."""
        if len(self._corners) != 4:
            return None
        rect = dw.order_points(self.corners())    # tl, tr, br, bl
        tl, tr, br, bl = [self.mapFromScene(QPointF(float(x), float(y)))
                          for x, y in rect]
        px, py = view_pos.x(), view_pos.y()

        def seg_dist(a, b):
            ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            if L2 <= 0:
                return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
            cx, cy = ax + t * vx, ay + t * vy
            return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

        for name, (a, b) in (("top", (tl, tr)), ("bottom", (bl, br))):
            if seg_dist(a, b) <= HIT_R:
                return name, self.mapToScene(view_pos)
        return None

    def _nearest_spline_handle(self, view_pos):
        if self._model is None:
            return None
        best, best_d = None, HIT_R
        for h in dw.handles(self._model):
            if h[0] == "ctip":
                # inactive corner tips are not shown; don't hit-test them
                edge_name, end = h[1], h[2]
                e = self._model.edges[edge_name]
                if (e.tip_a if end == "a" else e.tip_b) is None:
                    continue
            p = dw.handle_pos(self._model, h)
            vp = self.mapFromScene(QPointF(float(p[0]), float(p[1])))
            d = (vp - view_pos).manhattanLength()
            if d <= best_d:
                best, best_d = h, d
        return best

    def _corner_label(self, idx):
        """Ordered label (tl/tr/bl/br) of self._corners[idx]."""
        rect = dw.order_points(self.corners())
        p = self._corners[idx]
        labels = ("tl", "tr", "br", "bl")
        d = [(rect[i][0] - p.x()) ** 2 + (rect[i][1] - p.y()) ** 2
             for i in range(4)]
        return labels[int(np.argmin(d))]

    def mousePressEvent(self, event):
        if self._pix_item is None:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        if event.button() == Qt.RightButton:
            if self._right_click(pos, event):
                event.accept()
                return
            return super().mousePressEvent(event)
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if self._mode == MODE_QUAD:
            idx = self._nearest_quad_corner(pos)
            if idx is not None:
                self._drag = idx
            elif len(self._corners) < 4:
                self._corners.append(self.mapToScene(pos))
                self._redraw_overlay()
                self.cornersChanged.emit()
            else:
                # all corners placed: left-drag on empty space pans
                self._start_pan(event.position())
        else:
            h = self._nearest_spline_handle(pos)
            if h is not None:
                self._drag = h
            else:
                # not on a handle: left-drag pans
                self._start_pan(event.position())

    def _right_click(self, pos, event):
        """Right-click dispatch. Returns True when handled.

        Quad mode (4 corners placed): a corner promotes to book (spline)
        mode with THAT corner's tangent handle active; the top/bottom
        edge line promotes with the mid point added at the click.
        Spline mode: a corner anchor (or its tip) toggles between normal
        corner and spline handle; the mid handle / its tips get the
        break-into-V / re-smooth menu.
        """
        if self._mode == MODE_QUAD:
            if len(self._corners) != 4:
                return False
            idx = self._nearest_quad_corner(pos)
            if idx is not None:
                self.promoteRequested.emit(
                    "corner:%s" % self._corner_label(idx), None)
                return True
            hit = self._edge_line_hit(pos)
            if hit is not None:
                self.promoteRequested.emit(hit[0], hit[1])
                return True
            return False
        if self._model is None:
            return False
        h = self._nearest_spline_handle(pos)
        if h is None:
            return False
        kind, key, sgn = h
        if kind == "anchor":
            dw.toggle_corner_tip(self._model, key)
            self._notify_model_edit()
            return True
        if kind == "ctip":
            # right-click an active corner handle: back to a normal corner
            corner = dw.EDGE_ANCHORS[key][0 if sgn == "a" else 1]
            dw.toggle_corner_tip(self._model, corner)
            self._notify_model_edit()
            return True
        if kind in ("mid", "tip"):
            e = self._model.edges[key]
            menu = QMenu(self)
            if e.broken:
                act = menu.addAction("Make smooth (mirror tangents)")
            else:
                act = menu.addAction("Break tangents (V fold)")
            chosen = menu.exec(event.globalPosition().toPoint())
            if chosen is act:
                if e.broken:
                    dw.smooth_mid_tangent(self._model, key)
                else:
                    dw.break_mid_tangent(self._model, key)
                self._notify_model_edit()
            return True
        return False

    def _notify_model_edit(self):
        self._redraw_overlay()
        self.modelChanged.emit()
        self.modelEdited.emit()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return super().mouseMoveEvent(event)
        sp = self.mapToScene(event.position().toPoint())
        if self._mode == MODE_QUAD:
            self._corners[self._drag] = sp
            self._redraw_overlay()
            self.cornersChanged.emit()
        else:
            dw.move_handle(self._model, self._drag, (sp.x(), sp.y()))
            self._redraw_overlay()
            self.modelChanged.emit()

    def mouseReleaseEvent(self, event):
        was = self._drag
        self._drag = None
        if was is not None and self._mode == MODE_SPLINE:
            self.modelEdited.emit()      # drag finished -> re-render preview
        super().mouseReleaseEvent(event)

    # ----- overlay ---------------------------------------------------------
    def _clear_overlay(self):
        for it in self._overlay:
            self._scene.removeItem(it)
        self._overlay = []

    def _redraw_overlay(self):
        self._clear_overlay()
        if self._mode == MODE_QUAD:
            self._draw_quad_overlay()
        elif self._model is not None:
            self._draw_spline_overlay()

    # -- quad --
    def _draw_quad_overlay(self):
        if not self._corners:
            return
        pen = QPen(_LINE_COLOR)
        pen.setCosmetic(True)
        pen.setWidth(2)
        if len(self._corners) >= 2:
            if len(self._corners) == 4:
                rect = dw.order_points(self.corners())
                poly = QPolygonF([QPointF(float(x), float(y))
                                  for x, y in rect])
            else:
                poly = QPolygonF(list(self._corners))
            poly_item = QGraphicsPolygonItem(poly)
            poly_item.setPen(pen)
            poly_item.setBrush(QBrush(QColor(34, 204, 102, 40)))
            self._scene.addItem(poly_item)
            self._overlay.append(poly_item)
        font = QFont()
        font.setPointSize(10)
        for i, p in enumerate(self._corners):
            self._add_round_handle(p, HANDLE_R, _CORNER_COLOR)
            label = QGraphicsSimpleTextItem(str(i + 1))
            label.setBrush(QBrush(QColor("#ffffff")))
            label.setFont(font)
            label.setFlag(_IGNORE_XFORM, True)
            label.setPos(p)
            self._scene.addItem(label)
            self._overlay.append(label)

    # -- spline --
    def _draw_spline_overlay(self):
        m = self._model
        # straight side edges tl-bl, tr-br (dashed)
        side_pen = QPen(QColor("#dddddd"))
        side_pen.setCosmetic(True)
        side_pen.setStyle(Qt.DashLine)
        for a, b in (("tl", "bl"), ("tr", "br")):
            p, q = m.anchors[a], m.anchors[b]
            ln = self._scene.addLine(p[0], p[1], q[0], q[1], side_pen)
            self._overlay.append(ln)
        # curved edges as painter paths from the dense samples
        for name, col in (("top", _TOP_COLOR), ("bottom", _BOT_COLOR)):
            dense = dw.edge_dense(m, name, 200)
            path = QPainterPath(QPointF(float(dense[0, 0]),
                                        float(dense[0, 1])))
            for x, y in dense[1:]:
                path.lineTo(float(x), float(y))
            pen = QPen(col)
            pen.setCosmetic(True)
            pen.setWidth(2)
            item = self._scene.addPath(path, pen)
            self._overlay.append(item)
            # mid tangent lines mid->each tip, dashed. Drawn per side (via
            # handle_pos) so a broken tangent shows its true V shape.
            e = m.edges[name]
            mid = np.asarray(e.mid, float)
            hpen = QPen(QColor("#bbbbbb"))
            hpen.setCosmetic(True)
            hpen.setStyle(Qt.DashLine)
            for sgn in (1, -1):
                tp = dw.handle_pos(m, ("tip", name, sgn))
                ln = self._scene.addLine(mid[0], mid[1],
                                         float(tp[0]), float(tp[1]), hpen)
                self._overlay.append(ln)
            # corner tip lines anchor -> tip (active corners only; a
            # normal corner keeps its chord tangent and shows no handle)
            for end in ("a", "b"):
                if (e.tip_a if end == "a" else e.tip_b) is None:
                    continue
                tip = dw.handle_pos(m, ("ctip", name, end))
                base = m.anchors[dw.EDGE_ANCHORS[name][0 if end == "a"
                                                       else 1]]
                cpen = QPen(col)
                cpen.setCosmetic(True)
                cpen.setStyle(Qt.DashLine)
                ln = self._scene.addLine(base[0], base[1],
                                         float(tip[0]), float(tip[1]), cpen)
                self._overlay.append(ln)
        # handles on top: tips first, then mids, anchors last (drawn last =
        # on top, matching the hit-test priority in core handles())
        for name, col in (("top", _TOP_COLOR), ("bottom", _BOT_COLOR)):
            for sgn in (1, -1):
                p = dw.handle_pos(m, ("tip", name, sgn))
                self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                       TIP_R, _TIP_COLOR)
            for end in ("a", "b"):
                p = dw.handle_pos(m, ("ctip", name, end))
                self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                       TIP_R, col)
            p = dw.handle_pos(m, ("mid", name, None))
            self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                   HANDLE_R, col)
        for k in ("tl", "tr", "bl", "br"):
            p = m.anchors[k]
            self._add_square_handle(QPointF(float(p[0]), float(p[1])),
                                    HANDLE_R)

    # -- shared handle drawing --
    def _add_round_handle(self, pos, r, color):
        h = QGraphicsEllipseItem(QRectF(-r, -r, 2 * r, 2 * r))
        pen = QPen(QColor("#202020"))
        pen.setCosmetic(True)
        h.setPen(pen)
        h.setBrush(QBrush(color))
        h.setFlag(_IGNORE_XFORM, True)
        h.setPos(pos)
        self._scene.addItem(h)
        self._overlay.append(h)

    def _add_square_handle(self, pos, r):
        item = self._scene.addRect(QRectF(-r, -r, 2 * r, 2 * r),
                                   QPen(QColor("#202020")),
                                   QBrush(QColor("#ffffff")))
        item.setFlag(_IGNORE_XFORM, True)
        item.setPos(pos)
        self._overlay.append(item)


class DewarpStageWidget(QWidget):
    """First-class dewarp stage for the main window's central stack.

    Construct once; call :meth:`set_source_image` when entering the
    stage. Emits :attr:`applied` when the user accepts (read
    :meth:`result_image` for the flattened BGR ndarray) and
    :attr:`cancelled` when they back out."""

    applied = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._pv_img = None
        self._pv_scale = 1.0
        self._result = None

        self._source = _SourceView(self)
        self._resultv = _ResultView(self)

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(self._source)
        split.addWidget(self._resultv)
        split.setSizes([500, 500])

        # ----- controls -----
        self._mode = QComboBox()
        self._mode.addItem("Quad (4 corners)")
        self._mode.addItem("Curved page (spline)")
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._auto_size = QCheckBox("Auto (from selection)")
        self._auto_size.setChecked(True)
        self._auto_size.toggled.connect(self._on_auto_size_toggled)

        self._w_mm = QDoubleSpinBox()
        self._w_mm.setRange(1.0, 5000.0)
        self._w_mm.setValue(210.0)          # A4-ish default
        self._w_mm.setSuffix(" mm")
        self._h_mm = QDoubleSpinBox()
        self._h_mm.setRange(1.0, 5000.0)
        self._h_mm.setValue(297.0)
        self._h_mm.setSuffix(" mm")
        self._dpi = QSpinBox()
        self._dpi.setRange(30, 1200)
        self._dpi.setValue(300)
        self._dpi.setSuffix(" DPI")
        for w in (self._w_mm, self._h_mm, self._dpi):
            w.setEnabled(False)                  # auto-size on by default
            w.valueChanged.connect(self._request_preview)

        self._full = QCheckBox("Keep whole image (don't crop)")
        self._full.toggled.connect(self._request_preview)

        self._auto_btn = QPushButton("Auto-detect page")
        self._auto_btn.clicked.connect(self._auto_detect)
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.clicked.connect(self._reset_selection)
        self._refine_btn = QPushButton("Refine with text lines")
        self._refine_btn.setToolTip(
            "Bend the outline so its isolines follow the printed text "
            "lines (needs at least 5 detectable lines)")
        self._refine_btn.clicked.connect(self._refine_text)
        self._refine_btn.setEnabled(False)   # spline mode only

        self._size_label = QLabel("Output: -")

        # zoom controls (wheel zooms at the cursor; middle-drag pans)
        zoom_row = QHBoxLayout()
        for text, slot in (("+", self._source.zoom_in),
                           ("-", self._source.zoom_out),
                           ("Fit", self._source.refit)):
            b = QPushButton(text)
            b.setMaximumWidth(40 if len(text) == 1 else 56)
            b.clicked.connect(slot)
            zoom_row.addWidget(b)
        fit_r = QPushButton("Fit Result")
        fit_r.clicked.connect(self._resultv.refit)
        zoom_row.addWidget(fit_r)
        zoom_row.addStretch(1)

        form = QFormLayout()
        form.addRow("Mode:", self._mode)
        form.addRow("Output size:", self._auto_size)
        form.addRow("Width:", self._w_mm)
        form.addRow("Height:", self._h_mm)
        form.addRow("Resolution:", self._dpi)

        controls = QVBoxLayout()
        controls.addWidget(self._auto_btn)
        controls.addWidget(self._refine_btn)
        controls.addWidget(self._reset_btn)
        controls.addLayout(zoom_row)
        controls.addLayout(form)
        controls.addWidget(self._full)
        controls.addWidget(self._size_label)
        controls.addStretch(1)
        controls_box = QWidget()
        controls_box.setLayout(controls)
        controls_box.setMaximumWidth(280)

        top = QHBoxLayout()
        top.addWidget(split, 1)
        top.addWidget(controls_box)

        self._apply_btn = QPushButton("Use Flattened Image")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        self._back_btn = QPushButton("Back to Tracing")
        self._back_btn.clicked.connect(self.cancelled.emit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._back_btn)
        buttons.addWidget(self._apply_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top, 1)
        layout.addLayout(buttons)

        self._source.cornersChanged.connect(self._request_preview)
        self._source.modelEdited.connect(self._request_preview)
        self._source.promoteRequested.connect(self._promote_to_spline)

    # ----- entry / result ---------------------------------------------------
    def set_source_image(self, bgr_image, dpi=300):
        """Enter the stage with a fresh source image (BGR ndarray).
        Resets the selection, result and preview; re-arms auto-fit."""
        self._src = np.ascontiguousarray(bgr_image)
        self._result = None
        if dpi:
            self._dpi.setValue(int(dpi))
        # downscaled copy for responsive spline previews
        h = self._src.shape[0]
        self._pv_scale = min(1.0, float(PREVIEW_H) / h)
        if self._pv_scale < 1.0 and cv2 is not None:
            self._pv_img = cv2.resize(self._src, None, fx=self._pv_scale,
                                      fy=self._pv_scale,
                                      interpolation=cv2.INTER_AREA)
        else:
            self._pv_img = self._src
        self._source.set_image(self._src)
        self._source.clear_corners()
        self._source.set_model(None)
        self._on_mode_changed(self._mode.currentIndex())

    def result_image(self):
        return self._result

    # ----- mode ------------------------------------------------------------
    def _spline_mode(self):
        return self._mode.currentIndex() == MODE_SPLINE

    def _on_mode_changed(self, idx):
        self._source.set_mode(idx)
        self._refine_btn.setEnabled(idx == MODE_SPLINE)
        # full-image mode only applies to the quad transform
        self._full.setEnabled(idx == MODE_QUAD)
        if self._src is None:
            return
        if idx == MODE_SPLINE and self._source.model() is None:
            h, w = self._src.shape[:2]
            self._source.set_model(dw.default_model(w, h))
        else:
            self._request_preview()

    def _reset_selection(self):
        if self._src is None:
            return
        if self._spline_mode():
            h, w = self._src.shape[:2]
            self._source.set_model(dw.default_model(w, h))
        else:
            self._source.clear_corners()

    def _promote_to_spline(self, what, scene_pos):
        """Convert the placed quad into a book (spline) outline.

        The spline is seeded STRAIGHT from the quad corners with all
        corners left "normal" (chord tangents, no visible handles), so
        the result is initially identical to the quad transform. `what`
        selects the first curve affordance:

        - "corner:<tl|tr|bl|br>": that corner's tangent handle is
          activated (right-click toggles it later)
        - "top"/"bottom": that edge's on-curve mid point is pulled to the
          clicked position, bending the curve through it immediately
        """
        corners = self._source.corners()
        if len(corners) != 4 or self._src is None:
            return
        rect = dw.order_points(corners)           # tl, tr, br, bl
        tl, tr, br, bl = [np.asarray(p, np.float64) for p in rect]
        n = dw.N_PTS
        top = np.linspace(tl, tr, n)
        bot = np.linspace(bl, br, n)
        model = dw.model_from_traces(top, bot)
        for e in model.edges.values():            # start as normal corners
            e.tip_a = None
            e.tip_b = None
        if what.startswith("corner:"):
            dw.toggle_corner_tip(model, what.split(":", 1)[1])
        elif what:
            dw.move_handle(model, ("mid", what, None),
                           (scene_pos.x(), scene_pos.y()))
        self._source.set_model(model)
        self._mode.setCurrentIndex(MODE_SPLINE)   # triggers spline preview

    # ----- sizing ----------------------------------------------------------
    def _on_auto_size_toggled(self, checked):
        for w in (self._w_mm, self._h_mm, self._dpi):
            w.setEnabled(not checked)
        self._request_preview()

    def _explicit_size(self):
        dpi = self._dpi.value()
        w = self._w_mm.value() / 25.4 * dpi
        h = self._h_mm.value() / 25.4 * dpi
        return max(2, int(round(w))), max(2, int(round(h)))

    def _quad_output_size(self, corners):
        if not self._auto_size.isChecked():
            return self._explicit_size()
        rect = dw.order_points(corners)
        tl, tr, br, bl = rect
        w = 0.5 * (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl))
        h = 0.5 * (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr))
        return max(2, int(round(w))), max(2, int(round(h)))

    # ----- actions ---------------------------------------------------------
    def _auto_detect(self):
        if cv2 is None or self._src is None:
            return
        gray = cv2.cvtColor(self._src, cv2.COLOR_BGR2GRAY)
        try:
            model = dw.detect_page(gray)
        except Exception as exc:   # RuntimeError and friends
            self._size_label.setText("Auto-detect failed: %s" % exc)
            return
        if self._spline_mode():
            self._source.set_model(model)
        else:
            a = model.anchors
            self._source.set_corners([a["tl"], a["tr"], a["br"], a["bl"]])

    def _refine_text(self):
        model = self._source.model()
        if cv2 is None or model is None or self._src is None:
            return
        gray = cv2.cvtColor(self._src, cv2.COLOR_BGR2GRAY)
        try:
            refined, ok = dw.refine_with_text(gray, model.copy())
        except Exception as exc:
            self._size_label.setText("Refine failed: %s" % exc)
            return
        if ok:
            self._source.set_model(refined)
        else:
            self._size_label.setText(
                "No usable text lines found; outline unchanged")

    # ----- preview ---------------------------------------------------------
    def _request_preview(self, *_):
        if self._src is None:
            return
        if self._spline_mode():
            self._update_spline_preview()
        else:
            self._update_quad_preview()

    def _update_quad_preview(self):
        corners = self._source.corners()
        if len(corners) != 4:
            self._resultv.show_placeholder(
                "Place 4 page corners on the left (%d/4)" % len(corners))
            self._apply_btn.setEnabled(False)
            self._result = None
            self._size_label.setText("Output: -")
            return
        out_w, out_h = self._quad_output_size(corners)
        try:
            flat = dw.dewarp_quad(self._src, corners, out_w, out_h,
                                  full_image=self._full.isChecked())
        except Exception as exc:
            self._show_error(exc)
            return
        self._result = flat
        self._resultv.set_image(flat)
        self._apply_btn.setEnabled(True)
        h, w = flat.shape[:2]
        self._size_label.setText("Output: %d x %d px" % (w, h))

    def _update_spline_preview(self):
        model = self._source.model()
        if model is None:
            self._resultv.show_placeholder("No page outline")
            self._apply_btn.setEnabled(False)
            self._result = None
            return
        # fast preview on the downscaled copy; Apply renders full-res
        pv_model = model.scaled(self._pv_scale)
        try:
            flat = dw.dewarp_page(self._pv_img, pv_model,
                                  interp=cv2.INTER_LINEAR)
        except Exception as exc:
            self._show_error(exc)
            return
        self._result = None            # full-res result made on Apply
        self._resultv.set_image(flat)
        self._apply_btn.setEnabled(True)
        out_w, out_h = self._spline_output_size(model)
        self._size_label.setText("Output: %d x %d px" % (out_w, out_h))

    def _spline_output_size(self, model):
        if not self._auto_size.isChecked():
            return self._explicit_size()
        w, h = dw.page_size_px(model)
        return max(2, int(round(w))), max(2, int(round(h)))

    def _show_error(self, exc):
        self._resultv.show_placeholder("Dewarp error: %s" % exc)
        self._apply_btn.setEnabled(False)
        self._result = None

    # ----- apply -----------------------------------------------------------
    def _on_apply(self):
        if self._src is None:
            return
        if self._spline_mode():
            model = self._source.model()
            if model is None:
                return
            out_w, out_h = self._spline_output_size(model)
            try:
                self._result = dw.dewarp_page(self._src, model,
                                              out_w=out_w, out_h=out_h)
            except Exception as exc:
                self._show_error(exc)
                return
        if self._result is not None:
            self.applied.emit()
