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

Three modes:

* QUAD (4 corners) - drop/drag four page corners; the right pane shows
  ``core.dewarp.dewarp_quad`` (straight perspective transform) live.
* BOOK - ONE PAGE - a curved-page spline outline (4 corner anchors +
  curved top/bottom edges with on-curve mid points and tangent handles);
  center control points default to SMOOTH. The right pane shows
  ``core.dewarp.dewarp_page`` (perspective + cylinder unwrap).
* BOOK - TWO PAGE - the same outline for an open spread: the center
  (gutter) control points default to broken-tangent CORNERS (V fold) so
  the crease models correctly. Output is currently the whole flattened
  spread; separate per-page output (spine anchors) is on the backlog.

Preview renders on a downscaled copy for responsiveness (updated when a
drag ends); Apply renders at full quality. "Refine with text lines"
bends the outline so its isolines match the printed lines.

"Auto-detect" seeds either mode from the silhouette detector.

Usage: construct :class:`DewarpStageWidget` once, call
:meth:`set_source_image` when entering the stage, and listen for
:attr:`applied` (read :meth:`result_image` for the flattened BGR
ndarray) and :attr:`cancelled`. Both panes auto-fit the image to the
available space on show/resize until the user zooms manually.

NOTE: PySide6 cannot run in the porting sandbox, so this module is
static-checked only. Expect to test-drive and adjust it on a real desktop.
"""

import json

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 always present in the app
    cv2 = None

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout,
                               QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPolygonItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView,
                               QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QMessageBox, QPushButton, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from ..core import calibration as calib_core
from ..core import dewarp as dw

# _SourceView interaction modes
MODE_QUAD = 0
MODE_SPLINE = 1

# stage mode-selector indices (both book modes use the spline view)
IDX_QUAD = 0
IDX_ONE_PAGE = 1     # single curved page: smooth center control points
IDX_TWO_PAGE = 2     # open book spread: corner (V fold) at the gutter

HANDLE_R = 7          # corner handle radius, in view pixels
TIP_R = 5             # tangent-tip handle radius, in view pixels
HIT_R = 12            # click tolerance for grabbing a handle, view pixels
PREVIEW_H = 700       # spline preview renders at this image height
PREVIEW_MAX = 1400    # cap the longest side of the live dewarp preview
                      # raster (full-res is rendered only on Apply)
DISPLAY_MAX = 2200    # cap the on-screen pixmap's longest side; big photos
                      # are shown via a downscaled proxy (full-image scene
                      # coords preserved) so repaints stay fast

_CORNER_COLOR = QColor("#22cc66")
_LINE_COLOR = QColor("#22cc66")
_TOP_COLOR = QColor("#50dc50")
_BOT_COLOR = QColor("#ffb450")
_TIP_COLOR = QColor("#ffffff")
# spread edge colors (match BookScan's GUI palette)
_SPREAD_COLORS = {"left_top": QColor("#50dc50"),
                  "left_bottom": QColor("#ffb450"),
                  "right_top": QColor("#3ca0ff"),
                  "right_bottom": QColor("#dc50dc")}

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


class MmSpinBox(QDoubleSpinBox):
    """A millimetre spinbox that accepts unit-suffixed input.

    Users can type '5.25 in', '56 cm', '133.35 mm' or a bare number
    (millimetres); the value is converted and displayed canonically in
    mm. Spinner arrows still step in mm."""

    def validate(self, text, pos):
        from PySide6.QtGui import QValidator
        try:
            calib_core.parse_length_mm(text)
            return (QValidator.Acceptable, text, pos)
        except ValueError:
            return (QValidator.Intermediate, text, pos)

    def valueFromText(self, text):
        try:
            return calib_core.parse_length_mm(text)
        except ValueError:
            return self.value()

    def textFromValue(self, value):
        return "%.2f mm" % value


def display_downscale(bgr, max_side=DISPLAY_MAX):
    """Downscale a BGR image for ON-SCREEN display only.

    Returns (display_bgr, full_w, full_h). Big photos are shrunk so Qt
    never has to build or (smooth-)scale a tens-of-megapixels pixmap - the
    caller keeps the full-res array for the actual image math and scales
    the display item back up to full_w x full_h so scene coordinates stay
    in full-image pixels."""
    full_h, full_w = bgr.shape[:2]
    longest = max(full_w, full_h)
    if cv2 is not None and longest > max_side:
        f = max_side / float(longest)
        small = cv2.resize(bgr,
                           (max(1, int(round(full_w * f))),
                            max(1, int(round(full_h * f)))),
                           interpolation=cv2.INTER_AREA)
        return small, full_w, full_h
    return bgr, full_w, full_h


class _AutoFitView(QGraphicsView):
    """QGraphicsView that keeps its image fitted to the viewport.

    set_image is often called before the widget has real geometry (the
    stage may be hidden in a stacked layout), so a one-shot fitInView at
    that moment computes against a tiny default viewport and the image
    shows up minuscule. Instead, refit on every show/resize until the
    user zooms manually (wheel); a new image re-arms auto-fit.

    Also hosts the shared two-point CALIBRATION gesture: arm_calibration()
    puts the pane in measuring mode; the user's first left-click sets the
    start point, a rubber line then follows the cursor, and the second
    left-click sets the end point and emits calibrationPicked(p1, p2)
    (scene coords). Right-click or Escape cancels at any stage."""

    calibrationPicked = Signal(object, object)   # QPointF, QPointF

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = None
        self._user_zoomed = False
        self._panning = False
        self._pan_last = None
        self._calib_armed = False     # waiting for the first click
        self._calib_p1 = None         # set once the first point is placed
        self._calib_line = None
        self._saved_drag_mode = None
        self._fitting = False         # re-entrancy guard for _autofit
        self.setBackgroundBrush(QBrush(QColor("#202020")))
        # zoom toward the cursor rather than the view center
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # Scrollbars OFF: fitInView() in resizeEvent() would otherwise
        # toggle a scrollbar, which resizes the viewport, which fires
        # resizeEvent again - for a PERFECTLY SQUARE image the fit lands
        # exactly on the toggle boundary and oscillates forever (100% CPU,
        # "Not Responding"). Panning still works via the scroll offset.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    # ----- calibration gesture ---------------------------------------------
    def calibrating(self):
        return self._calib_armed or self._calib_p1 is not None

    def arm_calibration(self):
        """Enter measuring mode; the next two left-clicks are the two
        calibration points."""
        self._calib_armed = True
        self._calib_p1 = None
        # hand-drag (result pane) would swallow the point clicks
        self._saved_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    def _place_first_calib_point(self, scene_p1):
        self._calib_armed = False
        self._calib_p1 = QPointF(scene_p1)
        pen = QPen(QColor("#ff5555"))
        pen.setCosmetic(True)
        pen.setWidth(2)
        self._calib_line = self._scene.addLine(
            scene_p1.x(), scene_p1.y(), scene_p1.x(), scene_p1.y(), pen)
        self._calib_line.setZValue(50)

    def cancel_calibration(self):
        if self._calib_line is not None:
            self._scene.removeItem(self._calib_line)
        self._end_calibration()

    def _end_calibration(self):
        self._calib_armed = False
        self._calib_p1 = None
        self._calib_line = None
        if self._saved_drag_mode is not None:
            self.setDragMode(self._saved_drag_mode)
            self._saved_drag_mode = None
        self.unsetCursor()

    def keyPressEvent(self, event):
        if self.calibrating() and event.key() == Qt.Key_Escape:
            self.cancel_calibration()
            event.accept()
            return
        super().keyPressEvent(event)

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
        if self._fitting or self._user_zoomed:
            return
        self._fitting = True
        try:
            self.fit()
        finally:
            self._fitting = False

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
        if self.calibrating():
            if event.button() == Qt.LeftButton:
                sp = self.mapToScene(event.position().toPoint())
                if self._calib_armed:
                    self._place_first_calib_point(sp)       # first click
                else:
                    p1 = QPointF(self._calib_p1)            # second click
                    if self._calib_line is not None:
                        self._scene.removeItem(self._calib_line)
                    self._end_calibration()
                    self.calibrationPicked.emit(p1, sp)
            else:
                self.cancel_calibration()
            event.accept()
            return
        if event.button() == Qt.MiddleButton:
            self._start_pan(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._calib_p1 is not None and self._calib_line is not None:
            p2 = self.mapToScene(event.position().toPoint())
            self._calib_line.setLine(self._calib_p1.x(), self._calib_p1.y(),
                                     p2.x(), p2.y())
            event.accept()
            return
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

    def _set_display_image(self, bgr, rearm_fit=True):
        """Show `bgr` (a BGR ndarray). Big photos are DOWNSCALED IN NUMPY
        first (cv2.resize, ~0.05s) and only the small result is converted
        to a pixmap - so we never build or smooth-scale a full-resolution
        QImage/QPixmap just to display it (that full-res conversion was
        what froze the stage right after a large photo loaded). The proxy
        item is scaled back up so SCENE COORDINATES stay in full-image
        pixels: all corner/handle/calibration math is unchanged."""
        if self.calibrating():          # scene.clear() destroys the line
            self._calib_line = None
            self.cancel_calibration()
        self._scene.clear()
        full_h, full_w = bgr.shape[:2]
        longest = max(full_w, full_h)
        if cv2 is not None and longest > DISPLAY_MAX:
            f = DISPLAY_MAX / float(longest)
            disp = cv2.resize(bgr,
                              (max(1, int(round(full_w * f))),
                               max(1, int(round(full_h * f)))),
                              interpolation=cv2.INTER_AREA)
        else:
            disp = bgr
        pm = ndarray_to_qpixmap(disp)
        self._pix_item = self._scene.addPixmap(pm)
        if pm.width() and pm.width() != full_w:
            self._pix_item.setScale(full_w / pm.width())
        # Margin around the image serves two purposes: the view can pan
        # PAST the image edges (corners near an edge can be brought to a
        # comfortable spot), and cursor-centered wheel zoom stays stable
        # when the image is smaller than the viewport (Qt force-centers
        # content that fits inside the scene rect otherwise).
        r = QRectF(0, 0, full_w, full_h)
        m = 0.25 * max(full_w, full_h)
        self.setSceneRect(r.adjusted(-m, -m, m, m))
        if rearm_fit:
            self._user_zoomed = False
        self.fit()


class _ResultView(_AutoFitView):
    """Image pane with wheel zoom, hand-drag pan and fit-to-window.
    Right-click asks the stage to show the result context menu."""

    menuRequested = Signal(object)   # QPoint (global)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if (event.button() == Qt.RightButton and not self.calibrating()
                and self._pix_item is not None):
            self.menuRequested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def show_placeholder(self, text):
        if self.calibrating():
            self._calib_line = None
            self.cancel_calibration()
        self._scene.clear()
        self._pix_item = None
        t = self._scene.addText(text)
        t.setDefaultTextColor(QColor("#bbbbbb"))
        self.setSceneRect(t.boundingRect())

    def set_image(self, bgr):
        # keep the user's zoom while they tweak the outline; a fresh
        # source image (via _SourceView.set_image) re-arms auto-fit there
        self._set_display_image(bgr, rearm_fit=not self._user_zoomed)


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
    #   ("top"/"bottom", scene QPointF)  edge-line: mid added at the click
    #   ("corner:tl" etc., None)         corner: activate that corner's
    #                                    tangent handle
    promoteRequested = Signal(str, object)
    # right-click: dict(global=QPoint, scene=QPointF, hit=tuple|None);
    # the stage builds the context menu from it
    menuRequested = Signal(object)

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
        self._set_display_image(bgr)

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

    def _is_spread(self):
        return isinstance(self._model, dw.SpreadModel)

    def _nearest_spline_handle(self, view_pos):
        if self._model is None:
            return None
        if self._is_spread():
            all_handles = dw.spread_handles(self._model)
            pos_of = lambda h: dw.spread_handle_pos(self._model, h)
        else:
            all_handles = dw.handles(self._model)
            pos_of = lambda h: dw.handle_pos(self._model, h)
        best, best_d = None, HIT_R
        for h in all_handles:
            if h[0] == "ctip":
                # inactive corner tips are not shown; don't hit-test them
                edge_name, end = h[1], h[2]
                e = self._model.edges[edge_name]
                if (e.tip_a if end == "a" else e.tip_b) is None:
                    continue
            p = pos_of(h)
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
        if self._pix_item is None or self.calibrating():
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        if event.button() == Qt.RightButton:
            # left-click is for selections/dragging only; right-click
            # always opens a context menu (built by the stage from the
            # hit information gathered here)
            info = {"global": event.globalPosition().toPoint(),
                    "scene": self.mapToScene(pos), "hit": None}
            if self._mode == MODE_QUAD:
                if len(self._corners) == 4:
                    idx = self._nearest_quad_corner(pos)
                    if idx is not None:
                        info["hit"] = ("quad_corner", self._corner_label(idx))
                    else:
                        hit = self._edge_line_hit(pos)
                        if hit is not None:
                            info["hit"] = ("quad_edge", hit[0])
                            info["scene"] = hit[1]
            elif self._model is not None:
                h = self._nearest_spline_handle(pos)
                if h is not None:
                    info["hit"] = ("spline", h)
            self.menuRequested.emit(info)
            event.accept()
            return
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

    def notify_model_edit(self):
        """Redraw the outline and announce a model change (used by the
        stage after mutating the model from a context-menu action)."""
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
            if self._is_spread():
                dw.spread_move_handle(self._model, self._drag,
                                      (sp.x(), sp.y()))
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
        elif self._model is None:
            pass
        elif self._is_spread():
            self._draw_spread_overlay()
        else:
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

    # -- spread (two-page) --
    def _draw_spread_overlay(self):
        m = self._model
        # straight side edges + the spine, dashed
        side_pen = QPen(QColor("#dddddd"))
        side_pen.setCosmetic(True)
        side_pen.setStyle(Qt.DashLine)
        for a, b in (("tl", "bl"), ("tr", "br"),
                     ("spine_top", "spine_bot")):
            p, q = m.anchors[a], m.anchors[b]
            ln = self._scene.addLine(p[0], p[1], q[0], q[1], side_pen)
            self._overlay.append(ln)
        for name, col in _SPREAD_COLORS.items():
            dense = dw.spread_edge_dense(m, name, 200)
            path = QPainterPath(QPointF(float(dense[0, 0]),
                                        float(dense[0, 1])))
            for x, y in dense[1:]:
                path.lineTo(float(x), float(y))
            pen = QPen(col)
            pen.setCosmetic(True)
            pen.setWidth(2)
            self._overlay.append(self._scene.addPath(path, pen))
            e = m.edges[name]
            mid = np.asarray(e.mid, float)
            hpen = QPen(QColor("#bbbbbb"))
            hpen.setCosmetic(True)
            hpen.setStyle(Qt.DashLine)
            for sgn in (1, -1):
                tp = dw.spread_handle_pos(m, ("tip", name, sgn))
                self._overlay.append(self._scene.addLine(
                    mid[0], mid[1], float(tp[0]), float(tp[1]), hpen))
            # spine tangent handle: dashed line fold -> tip
            sa = dw._spread_spine_anchor(m, name)
            st = dw.spread_handle_pos(m, ("stip", name, None))
            cpen = QPen(col)
            cpen.setCosmetic(True)
            cpen.setStyle(Qt.DashLine)
            self._overlay.append(self._scene.addLine(
                float(sa[0]), float(sa[1]), float(st[0]), float(st[1]),
                cpen))
        # handles: tips first, mids, then anchors on top
        for name, col in _SPREAD_COLORS.items():
            for sgn in (1, -1):
                p = dw.spread_handle_pos(m, ("tip", name, sgn))
                self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                       TIP_R, _TIP_COLOR)
            p = dw.spread_handle_pos(m, ("stip", name, None))
            self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                   TIP_R, col)
            p = dw.spread_handle_pos(m, ("mid", name, None))
            self._add_round_handle(QPointF(float(p[0]), float(p[1])),
                                   HANDLE_R, col)
        for k in ("tl", "tr", "bl", "br", "spine_top", "spine_bot"):
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
        self._mode.addItem("Quad (4 corners)")           # IDX_QUAD
        self._mode.addItem("Book - One Page (curved)")   # IDX_ONE_PAGE
        self._mode.addItem("Book - Two Page (spread)")   # IDX_TWO_PAGE
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._auto_size = QCheckBox("Auto (from selection)")
        self._auto_size.setChecked(True)
        self._auto_size.toggled.connect(self._on_auto_size_toggled)

        # unit-aware: users may type '5.25 in', '56 cm' or '133.35 mm'
        self._w_mm = MmSpinBox()
        self._w_mm.setRange(1.0, 5000.0)
        self._w_mm.setValue(210.0)          # A4-ish default
        self._h_mm = MmSpinBox()
        self._h_mm.setRange(1.0, 5000.0)
        self._h_mm.setValue(297.0)
        self._dpi = QSpinBox()
        self._dpi.setRange(30, 1200)
        self._dpi.setValue(300)
        self._dpi.setSuffix(" DPI")
        self._lock_aspect = QCheckBox("Lock aspect ratio")
        self._lock_aspect.toggled.connect(self._on_lock_toggled)
        self._aspect = None                      # H/W ratio while locked
        self._syncing_size = False
        self._w_mm.valueChanged.connect(self._on_width_changed)
        self._h_mm.valueChanged.connect(self._on_height_changed)
        self._dpi.valueChanged.connect(self._request_preview)
        # spinners stay live even while Auto is on: they DISPLAY the
        # auto-derived size, and editing one overrides it (see
        # _on_width/height_changed), so the aspect can be adjusted
        # straight away without calibrating first.

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
        form.addRow("", self._lock_aspect)
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
        self._source.calibrationPicked.connect(
            lambda p1, p2: self._on_calibrated("source", p1, p2))
        self._resultv.calibrationPicked.connect(
            lambda p1, p2: self._on_calibrated("result", p1, p2))
        self._source.menuRequested.connect(self._show_source_menu)
        self._resultv.menuRequested.connect(self._show_result_menu)

    # ----- entry / result ---------------------------------------------------
    def set_source_image(self, bgr_image, dpi=300):
        """Enter the stage with a fresh source image (BGR ndarray).
        Resets the selection, result and preview; re-arms auto-fit."""
        self._src = np.ascontiguousarray(bgr_image)
        self._result = None
        self._result_pv_scale = None
        self._pv_shape = None
        self._rotation = 0            # result rotation, degrees CW
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

    def output_dpi(self):
        """The DPI the flattened output was rendered at. Since the output
        size is known in mm (Width/Height or auto/calibrated), this fixes
        the result's real-world scale: mm_per_pixel = 25.4 / dpi."""
        return self._dpi.value()

    # ----- mode ------------------------------------------------------------
    def _spline_mode(self):
        return self._mode.currentIndex() != IDX_QUAD

    def _two_page(self):
        return self._mode.currentIndex() == IDX_TWO_PAGE

    def _on_mode_changed(self, idx):
        self._source.set_mode(MODE_QUAD if idx == IDX_QUAD
                              else MODE_SPLINE)
        self._refine_btn.setEnabled(idx != IDX_QUAD)
        # full-image mode only applies to the quad transform
        self._full.setEnabled(idx == IDX_QUAD)
        if self._src is None:
            return
        model = self._source.model()
        h, w = self._src.shape[:2]
        if idx == IDX_ONE_PAGE:
            if model is None:
                self._set_spline_model(dw.default_model(w, h))
            elif isinstance(model, dw.SpreadModel):
                # merge the spread back into one outline
                self._set_spline_model(dw.spread_to_page(model))
            else:
                self._source.notify_model_edit()
        elif idx == IDX_TWO_PAGE:
            if model is None:
                self._set_spread_model(dw.default_spread_model(w, h))
            elif isinstance(model, dw.SpreadModel):
                self._request_preview()
            else:
                # split the single outline at its mid (the gutter)
                self._set_spread_model(dw.page_to_spread(model))
        else:
            self._request_preview()

    def _set_spline_model(self, model):
        """Install a ONE-PAGE outline: smooth center control points and
        all four corner tangent handles active (seeded curve-neutral;
        right-click a corner to hide its handle)."""
        for name in ("top", "bottom"):
            dw.smooth_mid_tangent(model, name)
        for c in ("tl", "tr", "bl", "br"):
            if not dw.corner_tip_active(model, c):
                dw.toggle_corner_tip(model, c)
        self._source.set_model(model)

    def _set_spread_model(self, model):
        """Install a TWO-PAGE spread outline (spine anchors + per-edge
        spine tangent handles; each page dewarps independently)."""
        dw.ensure_spine_handles(model)
        self._source.set_model(model)

    def _reset_selection(self):
        if self._src is None:
            return
        h, w = self._src.shape[:2]
        if self._two_page():
            self._set_spread_model(dw.default_spread_model(w, h))
        elif self._spline_mode():
            self._set_spline_model(dw.default_model(w, h))
        else:
            self._source.clear_corners()

    def _promote_to_spline(self, what, scene_pos):
        """Convert the placed quad into a book (spline) outline.

        The spline is seeded STRAIGHT from the quad corners with all
        corners left "normal" (chord tangents, no visible handles), so
        the result is initially identical to the quad transform. `what`
        selects the first curve affordance:

        - "corner:<tl|tr|bl|br>": that corner's tangent handle is
          activated -> ONE-PAGE book mode (curving an outer corner)
        - "top"/"bottom": a point added on an edge is the GUTTER of a
          spread -> TWO-PAGE book mode with the spine placed at the
          clicked position along the edge
        """
        corners = self._source.corners()
        if len(corners) != 4 or self._src is None:
            return
        rect = dw.order_points(corners)           # tl, tr, br, bl
        tl, tr, br, bl = [np.asarray(p, np.float64) for p in rect]
        if what.startswith("corner:"):
            n = dw.N_PTS
            model = dw.model_from_traces(np.linspace(tl, tr, n),
                                         np.linspace(bl, br, n))
            for e in model.edges.values():        # start as normal corners
                e.tip_a = None
                e.tip_b = None
            dw.toggle_corner_tip(model, what.split(":", 1)[1])
            self._source.set_model(model)
            self._mode.setCurrentIndex(IDX_ONE_PAGE)
        elif what:
            # spine fraction from the click position along that edge
            a, b = (tl, tr) if what == "top" else (bl, br)
            v = b - a
            p = np.array([scene_pos.x(), scene_pos.y()], np.float64)
            t = float(np.dot(p - a, v) / max(1e-9, float(v @ v)))
            model = dw.quad_to_spread(corners, gutter_t=t)
            self._source.set_model(model)
            self._mode.setCurrentIndex(IDX_TWO_PAGE)

    # ----- sizing ----------------------------------------------------------
    def _on_auto_size_toggled(self, checked):
        # re-checking Auto recomputes from the selection (and re-syncs the
        # spinner display); unchecking keeps the shown values as explicit
        self._request_preview()

    def _on_lock_toggled(self, checked):
        # remember the ratio in force at the moment of locking
        self._aspect = (self._h_mm.value() / max(0.01, self._w_mm.value())
                        if checked else None)

    def _override_auto(self):
        """A manual spinner edit turns Auto off so the typed size sticks."""
        if self._auto_size.isChecked():
            self._syncing_size = True
            self._auto_size.setChecked(False)
            self._syncing_size = False

    def _on_width_changed(self, value):
        if self._syncing_size:
            return
        self._override_auto()
        if self._lock_aspect.isChecked() and self._aspect:
            self._syncing_size = True
            self._h_mm.setValue(value * self._aspect)
            self._syncing_size = False
        self._request_preview()

    def _on_height_changed(self, value):
        if self._syncing_size:
            return
        self._override_auto()
        if self._lock_aspect.isChecked() and self._aspect:
            self._syncing_size = True
            self._w_mm.setValue(value / self._aspect)
            self._syncing_size = False
        self._request_preview()

    def _sync_size_display(self, out_w, out_h):
        """While Auto is on, show the auto-derived size in the spinners
        (so they always reflect the real output and are ready to edit)."""
        if not self._auto_size.isChecked():
            return
        dpi = self._dpi.value()
        self._set_size_spinners(out_w / dpi * 25.4, out_h / dpi * 25.4)

    def _set_size_spinners(self, w_mm, h_mm):
        """Set both spinners without lock coupling or double previews."""
        self._syncing_size = True
        self._w_mm.setValue(w_mm)
        self._h_mm.setValue(h_mm)
        self._syncing_size = False
        if self._lock_aspect.isChecked():
            self._aspect = h_mm / max(0.01, w_mm)

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
        if self._two_page():
            # split the detected outline at its mid to seed the spread
            self._set_spread_model(dw.page_to_spread(model))
        elif self._spline_mode():
            self._set_spline_model(model)
        else:
            a = model.anchors
            self._source.set_corners([a["tl"], a["tr"], a["br"], a["bl"]])

    def _refine_text(self):
        model = self._source.model()
        if cv2 is None or model is None or self._src is None:
            return
        gray = cv2.cvtColor(self._src, cv2.COLOR_BGR2GRAY)
        try:
            if isinstance(model, dw.SpreadModel):
                refined, ok = dw.refine_spread_with_text(gray, model.copy())
            else:
                refined, ok = dw.refine_with_text(gray, model.copy())
        except Exception as exc:
            self._size_label.setText("Refine failed: %s" % exc)
            return
        if not ok:
            self._size_label.setText(
                "No usable text lines found; outline unchanged")
        elif isinstance(refined, dw.SpreadModel):
            self._set_spread_model(refined)
        else:
            self._set_spline_model(refined)

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
        # warpPerspective cost scales with the OUTPUT raster, so render the
        # live preview at a capped size (fast even for huge sources); the
        # full-res warp happens only on Apply.
        scale = min(1.0, PREVIEW_MAX / max(out_w, out_h))
        qw = max(2, int(round(out_w * scale)))
        qh = max(2, int(round(out_h * scale)))
        try:
            flat = dw.dewarp_quad(self._src, corners, qw, qh,
                                  full_image=self._full.isChecked())
        except Exception as exc:
            self._show_error(exc)
            return
        self._result = None            # full-res result made on Apply
        self._result_pv_scale = scale
        self._pv_shape = flat.shape[:2]
        self._resultv.set_image(self._rotate_result_img(flat))
        self._apply_btn.setEnabled(True)
        # full-res result dimensions (canvas size in full-image mode)
        res_w = int(round(flat.shape[1] / scale))
        res_h = int(round(flat.shape[0] / scale))
        self._sync_size_display(out_w, out_h)
        self._size_label.setText("Output: %d x %d px" % (res_w, res_h))

    def _update_spline_preview(self):
        model = self._source.model()
        if model is None:
            self._resultv.show_placeholder("No page outline")
            self._apply_btn.setEnabled(False)
            self._result = None
            return
        # render into the CHOSEN output aspect (so the right pane reflects
        # the Width/Height / calibration), just at a capped resolution for
        # responsiveness; Apply renders full-res at the same aspect.
        out_w, out_h = self._spline_output_size(model)
        pv_h = max(2, min(int(out_h), PREVIEW_H))
        pv_w = max(2, int(round(pv_h * out_w / max(1, out_h))))
        pv_model = model.scaled(self._pv_scale)
        try:
            if isinstance(model, dw.SpreadModel):
                left, right = dw.dewarp_spread(self._pv_img, pv_model,
                                               out_w=pv_w, out_h=pv_h,
                                               interp=cv2.INTER_LINEAR,
                                               n=800)
                pad = np.full((pv_h, 10, 3), 30, np.uint8)
                flat = np.hstack([left, pad, right])
            else:
                flat = dw.dewarp_page(self._pv_img, pv_model,
                                      out_w=pv_w, out_h=pv_h,
                                      interp=cv2.INTER_LINEAR)
        except Exception as exc:
            self._show_error(exc)
            return
        self._result = None            # full-res result made on Apply
        self._result_pv_scale = pv_h / max(1, out_h)  # result-pane calib
        self._pv_shape = flat.shape[:2]
        self._resultv.set_image(self._rotate_result_img(flat))
        self._apply_btn.setEnabled(True)
        self._sync_size_display(out_w, out_h)
        if isinstance(model, dw.SpreadModel):
            self._size_label.setText(
                "Output: 2 pages x %d x %d px" % (out_w, out_h))
        else:
            self._size_label.setText("Output: %d x %d px" % (out_w, out_h))

    def _spline_output_size(self, model):
        """Output size per page (spread) or for the whole page."""
        if not self._auto_size.isChecked():
            return self._explicit_size()
        if isinstance(model, dw.SpreadModel):
            w, h = dw.spread_size_px(model)
        else:
            w, h = dw.page_size_px(model)
        return max(2, int(round(w))), max(2, int(round(h)))

    def _show_error(self, exc):
        self._resultv.show_placeholder("Dewarp error: %s" % exc)
        self._apply_btn.setEnabled(False)
        self._result = None

    def _rotate_result_img(self, img):
        """Apply the accumulated result rotation (0/90/180/270 deg CW)."""
        rot = getattr(self, "_rotation", 0) % 360
        if rot == 90:
            return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        if rot == 180:
            return cv2.rotate(img, cv2.ROTATE_180)
        if rot == 270:
            return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return img

    def _rotate_result(self, clockwise):
        self._rotation = (getattr(self, "_rotation", 0)
                          + (90 if clockwise else -90)) % 360
        self._request_preview()

    # ----- calibration -----------------------------------------------------
    def _measured_output_px(self, which, p1, p2):
        """Length (px) and direction of the measured segment mapped into
        OUTPUT pixel space, or None when no transform is set up yet."""
        if which == "result":
            # the result pane shows a downscaled preview raster; one
            # uniform factor maps its pixels back to full output pixels
            s = getattr(self, "_result_pv_scale", None)
            if not s:
                return None
            dx = (p2.x() - p1.x()) / s
            dy = (p2.y() - p1.y()) / s
            return (dx * dx + dy * dy) ** 0.5, dx, dy
        # source pane: map through the quad homography to output px
        if self._spline_mode():
            model = self._source.model()
            if model is None:
                return None
            out_w, out_h = self._spline_output_size(model)
            # approximate with the anchor-quad homography (perspective
            # stage only; close for gently curved pages)
            a = model.anchors
            rect = np.float32([a["tl"], a["tr"], a["br"], a["bl"]])
        else:
            corners = self._source.corners()
            if len(corners) != 4:
                return None
            out_w, out_h = self._quad_output_size(corners)
            rect = dw.order_points(corners)
        dst = np.float32([[0, 0], [out_w - 1, 0],
                          [out_w - 1, out_h - 1], [0, out_h - 1]])
        M = cv2.getPerspectiveTransform(np.float32(rect), dst)
        pts = np.float32([[p1.x(), p1.y()], [p2.x(), p2.y()]])
        q = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), M).reshape(-1, 2)
        dx, dy = float(q[1, 0] - q[0, 0]), float(q[1, 1] - q[0, 1])
        return (dx * dx + dy * dy) ** 0.5, dx, dy

    def _on_calibrated(self, which, p1, p2):
        """Two calibration points picked: ask the real length and rescale
        the output so the measured feature comes out that size.

        Calibration PRESERVES the current aspect ratio - both dimensions
        scale by the same factor. (Fix a wrong aspect with the Width /
        Height spinners; use calibration to set the true overall size.)"""
        if self._src is None:
            return
        m = self._measured_output_px(which, p1, p2)
        if m is None or m[0] < 2.0:
            self._size_label.setText(
                "Calibration needs a placed outline and a longer line")
            return
        d_px = m[0]
        text, ok = QInputDialog.getText(
            self, "Calibrate Scale",
            "Real-world length of the measured line\n"
            "(e.g. 100 mm, 5.25 in, 56 cm):", text="100 mm")
        if not ok:
            return
        try:
            real_mm = calib_core.parse_length_mm(text)
        except ValueError:
            self._size_label.setText("Could not parse length: %s" % text)
            return
        dpi = self._dpi.value()
        # current output size in mm (from the active sizing mode)
        if self._spline_mode():
            out_w, out_h = self._spline_output_size(self._source.model())
        else:
            out_w, out_h = self._quad_output_size(self._source.corners())
        w_mm = out_w / dpi * 25.4
        h_mm = out_h / dpi * 25.4
        factor = real_mm / (d_px / dpi * 25.4)
        w_mm, h_mm = w_mm * factor, h_mm * factor   # preserve aspect
        # switch to explicit sizing with the calibrated dimensions
        if self._auto_size.isChecked():
            self._auto_size.setChecked(False)   # enables spinners, previews
        self._set_size_spinners(w_mm, h_mm)
        self._request_preview()
        self._size_label.setText(
            "Calibrated: %.1f x %.1f mm @ %d DPI" % (w_mm, h_mm, dpi))

    # ----- context menus ----------------------------------------------------
    def _show_source_menu(self, info):
        """Right-click menu on the source pane: hit-specific actions first,
        then the common actions (mode, detection, rotate/flip, calibrate,
        zoom, outline save/load) ported from dewarp.py / book_dewarp.py."""
        menu = QMenu(self)
        hit = info.get("hit")
        if hit is not None:
            kind = hit[0]
            if kind == "quad_corner":
                corner = hit[1]
                menu.addAction(
                    "Curve this page (book mode, handle at %s)"
                    % corner.upper(),
                    lambda: self._promote_to_spline("corner:" + corner,
                                                    None))
            elif kind == "quad_edge":
                edge, sp = hit[1], info["scene"]
                menu.addAction(
                    "Add spline point here (curve the %s edge)" % edge,
                    lambda: self._promote_to_spline(edge, sp))
            elif kind == "spline" and isinstance(self._source.model(),
                                                 dw.SpreadModel):
                # spread handles have no per-corner/V-fold toggles; the
                # spine tangent tips are directly draggable instead
                pass
            elif kind == "spline":
                h = hit[1]
                hk, key, sgn = h
                model = self._source.model()
                if hk in ("anchor", "ctip") and model is not None:
                    corner = (key if hk == "anchor"
                              else dw.EDGE_ANCHORS[key][0 if sgn == "a"
                                                        else 1])
                    label = ("Hide corner handle (%s)"
                             if dw.corner_tip_active(model, corner)
                             else "Show corner handle (%s)")
                    menu.addAction(label % corner.upper(),
                                   lambda: self._toggle_corner(corner))
                elif hk in ("mid", "tip") and model is not None:
                    e = model.edges[key]
                    if e.broken:
                        menu.addAction("Make smooth (mirror tangents)",
                                       lambda: self._toggle_fold(key))
                    else:
                        menu.addAction("Break tangents (V fold)",
                                       lambda: self._toggle_fold(key))
            menu.addSeparator()
        # mode selection (radio style), as in dewarp.py's context menu
        for idx, name in ((IDX_QUAD, "Quad Mode"),
                          (IDX_ONE_PAGE, "Book - One Page"),
                          (IDX_TWO_PAGE, "Book - Two Page")):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self._mode.currentIndex() == idx)
            act.triggered.connect(
                lambda _=False, i=idx: self._mode.setCurrentIndex(i))
        menu.addSeparator()
        menu.addAction("Auto-detect Page", self._auto_detect)
        ref = menu.addAction("Refine with Text Lines", self._refine_text)
        ref.setEnabled(self._spline_mode())
        menu.addAction("Reset Outline", self._reset_selection)
        menu.addSeparator()
        menu.addAction("Rotate 90 deg CW", lambda: self._rotate_source(True))
        menu.addAction("Rotate 90 deg CCW",
                       lambda: self._rotate_source(False))
        menu.addAction("Flip Horizontal", lambda: self._flip_source(True))
        menu.addAction("Flip Vertical", lambda: self._flip_source(False))
        menu.addSeparator()
        menu.addAction("Calibrate...", self._source.arm_calibration)
        menu.addSeparator()
        menu.addAction("Zoom In", self._source.zoom_in)
        menu.addAction("Zoom Out", self._source.zoom_out)
        menu.addAction("Fit to Window", self._source.refit)
        menu.addSeparator()
        sv = menu.addAction("Save Outline...", self._save_outline)
        sv.setEnabled(self._spline_mode()
                      and self._source.model() is not None)
        menu.addAction("Load Outline...", self._load_outline)
        menu.addSeparator()
        menu.addAction("Save Flattened Image...", self._save_flattened)
        menu.exec(info["global"])

    def _show_result_menu(self, global_pos):
        menu = QMenu(self)
        menu.addAction("Save Flattened Image...", self._save_flattened)
        menu.addSeparator()
        menu.addAction("Rotate Result 90 deg CW",
                       lambda: self._rotate_result(True))
        menu.addAction("Rotate Result 90 deg CCW",
                       lambda: self._rotate_result(False))
        menu.addSeparator()
        menu.addAction("Calibrate...", self._resultv.arm_calibration)
        menu.addSeparator()
        menu.addAction("Fit to Window", self._resultv.refit)
        menu.exec(global_pos)

    # ----- context-menu actions --------------------------------------------
    def _toggle_corner(self, corner):
        model = self._source.model()
        if model is None:
            return
        dw.toggle_corner_tip(model, corner)
        self._source.notify_model_edit()

    def _toggle_fold(self, edge_name):
        model = self._source.model()
        if model is None:
            return
        if model.edges[edge_name].broken:
            dw.smooth_mid_tangent(model, edge_name)
        else:
            dw.break_mid_tangent(model, edge_name)
        self._source.notify_model_edit()

    def _rotate_source(self, clockwise):
        """Rotate the working copy 90 degrees (from dewarp.py's context
        menu). Resets the outline - re-place or Auto-detect after."""
        if self._src is None or cv2 is None:
            return
        code = (cv2.ROTATE_90_CLOCKWISE if clockwise
                else cv2.ROTATE_90_COUNTERCLOCKWISE)
        self.set_source_image(cv2.rotate(self._src, code),
                              dpi=self._dpi.value())

    def _flip_source(self, horizontal):
        """Mirror the working copy (from dewarp.py's context menu)."""
        if self._src is None or cv2 is None:
            return
        self.set_source_image(cv2.flip(self._src, 1 if horizontal else 0),
                              dpi=self._dpi.value())

    def _save_outline(self):
        """Save the spline outline as JSON (book_dewarp.py's Save
        Outline, in the single-page PageModel format)."""
        model = self._source.model()
        if model is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Outline", "outline.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w") as fh:
                json.dump(model.to_dict(), fh, indent=2)
        except OSError as exc:
            self._size_label.setText("Save failed: %s" % exc)
            return
        self._size_label.setText("Outline saved")

    def _load_outline(self):
        """Load a previously saved outline (book_dewarp.py's Load
        Outline) and switch to book mode with it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Outline", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path) as fh:
                data = json.load(fh)
            if "spine_top" in data.get("anchors", {}):
                # BookScan-format spread outline (6 anchors, 4 edges)
                model = dw.ensure_spine_handles(
                    dw.SpreadModel.from_dict(data))
                target = IDX_TWO_PAGE
            else:
                model = dw.PageModel.from_dict(data)
                broken = any(model.edges[n].broken
                             for n in ("top", "bottom"))
                target = IDX_TWO_PAGE if broken else IDX_ONE_PAGE
        except Exception as exc:
            self._size_label.setText("Load failed: %s" % exc)
            return
        # keep the file's state exactly as saved
        self._source.set_model(model)
        if self._mode.currentIndex() == target:
            self._on_mode_changed(target)   # runs conversions + preview
        else:
            self._mode.setCurrentIndex(target)

    # ----- apply / save -----------------------------------------------------
    def _render_fullres(self, purpose):
        """Render the final full-resolution flattened image (with the
        accumulated result rotation). For a spread, asks which page(s);
        returns the image or None (not ready / cancelled)."""
        if self._src is None:
            return None
        if self._spline_mode():
            model = self._source.model()
            if model is None:
                return None
            out_w, out_h = self._spline_output_size(model)
            if isinstance(model, dw.SpreadModel):
                try:
                    left, right = dw.dewarp_spread(self._src, model,
                                                   out_w=out_w,
                                                   out_h=out_h)
                except Exception as exc:
                    self._show_error(exc)
                    return None
                box = QMessageBox(self)
                box.setWindowTitle(purpose)
                box.setText("Both pages were dewarped independently.\n"
                            "Which page(s)?")
                b_left = box.addButton("Left Page",
                                       QMessageBox.AcceptRole)
                b_right = box.addButton("Right Page",
                                        QMessageBox.AcceptRole)
                b_both = box.addButton("Both (side by side)",
                                       QMessageBox.AcceptRole)
                box.addButton(QMessageBox.Cancel)
                box.exec()
                clicked = box.clickedButton()
                if clicked is b_left:
                    out = left
                elif clicked is b_right:
                    out = right
                elif clicked is b_both:
                    out = np.hstack([left, right])
                else:
                    return None
                return self._rotate_result_img(out)
            try:
                out = dw.dewarp_page(self._src, model,
                                     out_w=out_w, out_h=out_h)
            except Exception as exc:
                self._show_error(exc)
                return None
            return self._rotate_result_img(out)
        corners = self._source.corners()
        if len(corners) != 4:
            return None
        out_w, out_h = self._quad_output_size(corners)
        try:
            out = dw.dewarp_quad(self._src, corners, out_w, out_h,
                                 full_image=self._full.isChecked())
        except Exception as exc:
            self._show_error(exc)
            return None
        return self._rotate_result_img(out)

    def _on_apply(self):
        # previews render at a capped resolution; Apply renders full-res
        result = self._render_fullres("Use Flattened Image")
        if result is not None:
            self._result = result
            self.applied.emit()

    def _save_flattened(self):
        """Save the flattened image straight to a file - the user may
        only need to flatten and/or scale, without tracing."""
        result = self._render_fullres("Save Flattened Image")
        if result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Flattened Image", "flattened.png",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not path:
            return
        if cv2 is None or not cv2.imwrite(path, result):
            self._size_label.setText("Save failed: could not write %s"
                                     % path)
            return
        self._size_label.setText("Saved %s" % path)
