"""Dewarp stage: flatten a photographed page before tracing.

A self-contained modal dialog with the studio's left-right layout:

    +-----------------------------+-----------------------------+
    |  source photo (left)        |  live dewarped result       |
    |  drop / drag 4 corners      |  (right, updates live)      |
    +-----------------------------+-----------------------------+
    | [Auto-detect] size controls / options        [Apply][X]  |
    +----------------------------------------------------------+

The left pane lets the user place and drag four corners of the page; the
right pane shows ``core.dewarp.dewarp_quad`` applied live. "Auto-detect"
seeds the four corners from the silhouette detector. On Apply the flattened
image is returned via :meth:`result_image` (BGR ndarray) so the caller can
make it the working image for calibration/tracing.

This is the QUAD (straight 4-corner perspective) stage. The curved-page
spline path (``core.dewarp.PageModel`` / ``dewarp_page``) is the planned
next mode; the mode selector reserves a slot for it.

NOTE: PySide6 cannot run in the porting sandbox, so this module is
static-checked only. Expect to test-drive and adjust it on a real desktop.
"""

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 always present in the app
    cv2 = None

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPolygonItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView,
                               QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from ..core import dewarp as dw

# scoped enum name, robust across PySide6 versions
_IGNORE_XFORM = QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations

HANDLE_R = 7          # corner handle radius, in view pixels
HIT_R = 12            # click tolerance for grabbing a handle, view pixels
_CORNER_COLOR = QColor("#22cc66")
_LINE_COLOR = QColor("#22cc66")


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


class _ResultView(QGraphicsView):
    """Read-only image pane with wheel zoom and fit-to-window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix_item = None
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setBackgroundBrush(QBrush(QColor("#202020")))
        self._placeholder = None

    def show_placeholder(self, text):
        self._scene.clear()
        self._pix_item = None
        t = self._scene.addText(text)
        t.setDefaultTextColor(QColor("#bbbbbb"))
        self.setSceneRect(t.boundingRect())

    def set_image(self, bgr):
        pm = ndarray_to_qpixmap(bgr)
        self._scene.clear()
        self._pix_item = self._scene.addPixmap(pm)
        self.setSceneRect(QRectF(pm.rect()))
        self.fit()

    def fit(self):
        if self._pix_item is not None:
            self.fitInView(self._pix_item, Qt.KeepAspectRatio)

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)


class _SourceView(QGraphicsView):
    """Photo pane where the user places and drags four page corners.

    Click empty space to drop a corner (up to four); click-drag a corner
    to move it. Emits :attr:`cornersChanged` on any edit. Corner
    coordinates are in image pixels.
    """

    cornersChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setBackgroundBrush(QBrush(QColor("#202020")))
        self.setMouseTracking(True)
        self._pix_item = None
        self._corners = []       # list[QPointF] in scene/image coords
        self._drag = None        # index being dragged, or None
        self._overlay = []       # handle/label/polygon items

    # ----- image -----------------------------------------------------------
    def set_image(self, bgr):
        pm = ndarray_to_qpixmap(bgr)
        self._scene.clear()
        self._overlay = []
        self._pix_item = self._scene.addPixmap(pm)
        self.setSceneRect(QRectF(pm.rect()))
        self.fitInView(self._pix_item, Qt.KeepAspectRatio)

    # ----- corners ---------------------------------------------------------
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

    # ----- interaction -----------------------------------------------------
    def _nearest_corner(self, view_pos):
        """Index of the corner within HIT_R of view_pos, else None."""
        best, best_d = None, HIT_R
        for i, p in enumerate(self._corners):
            vp = self.mapFromScene(p)
            d = (vp - view_pos).manhattanLength()
            if d <= best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, event):
        if self._pix_item is None or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        idx = self._nearest_corner(event.position().toPoint())
        if idx is not None:
            self._drag = idx
        elif len(self._corners) < 4:
            self._corners.append(self.mapToScene(event.position().toPoint()))
            self._redraw_overlay()
            self.cornersChanged.emit()
        # else: ignore clicks on empty space once 4 corners exist

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self._corners[self._drag] = self.mapToScene(
                event.position().toPoint())
            self._redraw_overlay()
            self.cornersChanged.emit()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    # ----- overlay ---------------------------------------------------------
    def _redraw_overlay(self):
        for it in self._overlay:
            self._scene.removeItem(it)
        self._overlay = []
        if not self._corners:
            return
        # ordered outline (once all four are present) as a closed polygon
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
        # handles + numbers (drawn cosmetically so they keep size on zoom)
        font = QFont()
        font.setPointSize(10)
        for i, p in enumerate(self._corners):
            r = HANDLE_R
            h = QGraphicsEllipseItem(QRectF(p.x() - r, p.y() - r, 2 * r, 2 * r))
            hp = QPen(QColor("#003311"))
            hp.setCosmetic(True)
            h.setPen(hp)
            h.setBrush(QBrush(_CORNER_COLOR))
            h.setFlag(_IGNORE_XFORM, True)
            h.setPos(p)
            h.setRect(QRectF(-r, -r, 2 * r, 2 * r))
            self._scene.addItem(h)
            self._overlay.append(h)
            label = QGraphicsSimpleTextItem(str(i + 1))
            label.setBrush(QBrush(QColor("#ffffff")))
            label.setFont(font)
            label.setFlag(_IGNORE_XFORM, True)
            label.setPos(p)
            self._scene.addItem(label)
            self._overlay.append(label)


class DewarpStage(QDialog):
    """Modal dewarp/flatten stage. Construct with a BGR image; after
    ``exec()`` returns Accepted, :meth:`result_image` gives the flattened
    BGR ndarray (or None if nothing was produced)."""

    def __init__(self, bgr_image, dpi=300, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Flatten Page (Dewarp)")
        self._src = np.ascontiguousarray(bgr_image)
        self._result = None
        self.resize(1000, 640)

        self._source = _SourceView(self)
        self._resultv = _ResultView(self)
        self._resultv.show_placeholder("Place 4 page corners on the left")

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(self._source)
        split.addWidget(self._resultv)
        split.setSizes([500, 500])

        # ----- controls -----
        self._mode = QComboBox()
        self._mode.addItem("Quad (4 corners)")
        self._mode.addItem("Curved page (spline) - coming soon")
        self._mode.model().item(1).setEnabled(False)

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
        self._dpi.setValue(int(dpi) if dpi else 300)
        self._dpi.setSuffix(" DPI")
        for w in (self._w_mm, self._h_mm, self._dpi):
            w.setEnabled(False)                  # auto-size on by default
            w.valueChanged.connect(self._update_preview)

        self._full = QCheckBox("Keep whole image (don't crop)")
        self._full.toggled.connect(self._update_preview)

        self._auto_btn = QPushButton("Auto-detect page")
        self._auto_btn.clicked.connect(self._auto_detect)
        self._reset_btn = QPushButton("Reset corners")
        self._reset_btn.clicked.connect(self._source.clear_corners)

        self._size_label = QLabel("Output: -")

        form = QFormLayout()
        form.addRow("Mode:", self._mode)
        form.addRow("Output size:", self._auto_size)
        form.addRow("Width:", self._w_mm)
        form.addRow("Height:", self._h_mm)
        form.addRow("Resolution:", self._dpi)

        controls = QVBoxLayout()
        controls.addWidget(self._auto_btn)
        controls.addWidget(self._reset_btn)
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

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self._apply_btn = self._buttons.button(QDialogButtonBox.Apply)
        self._apply_btn.setText("Use Flattened Image")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(top, 1)
        layout.addWidget(self._buttons)

        self._source.set_image(self._src)
        self._source.cornersChanged.connect(self._update_preview)

    # ----- result ----------------------------------------------------------
    def result_image(self):
        return self._result

    # ----- helpers ---------------------------------------------------------
    def _on_auto_size_toggled(self, checked):
        for w in (self._w_mm, self._h_mm, self._dpi):
            w.setEnabled(not checked)
        self._update_preview()

    def _output_size(self, corners):
        """(out_w, out_h) in pixels from the current sizing mode."""
        if self._auto_size.isChecked():
            rect = dw.order_points(corners)
            tl, tr, br, bl = rect
            w = 0.5 * (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl))
            h = 0.5 * (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr))
            return max(2, int(round(w))), max(2, int(round(h)))
        dpi = self._dpi.value()
        w = self._w_mm.value() / 25.4 * dpi
        h = self._h_mm.value() / 25.4 * dpi
        return max(2, int(round(w))), max(2, int(round(h)))

    def _auto_detect(self):
        if cv2 is None:
            return
        gray = cv2.cvtColor(self._src, cv2.COLOR_BGR2GRAY)
        try:
            model = dw.detect_page(gray)
        except Exception as exc:   # RuntimeError and friends
            self._size_label.setText("Auto-detect failed: %s" % exc)
            return
        a = model.anchors
        self._source.set_corners([a["tl"], a["tr"], a["br"], a["bl"]])

    def _update_preview(self, *_):
        corners = self._source.corners()
        if len(corners) != 4:
            self._resultv.show_placeholder(
                "Place 4 page corners on the left (%d/4)" % len(corners))
            self._apply_btn.setEnabled(False)
            self._result = None
            self._size_label.setText("Output: -")
            return
        out_w, out_h = self._output_size(corners)
        try:
            flat = dw.dewarp_quad(self._src, corners, out_w, out_h,
                                  full_image=self._full.isChecked())
        except Exception as exc:
            self._resultv.show_placeholder("Dewarp error: %s" % exc)
            self._apply_btn.setEnabled(False)
            self._result = None
            return
        self._result = flat
        self._resultv.set_image(flat)
        self._apply_btn.setEnabled(True)
        h, w = flat.shape[:2]
        self._size_label.setText("Output: %d x %d px" % (w, h))

    def _on_apply(self):
        if self._result is not None:
            self.accept()
