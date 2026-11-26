"""Tools > Stitch Pages: join two page images side by side, by hand.

Page A is fixed; page B is drawn over it, half transparent, and is
placed by dragging it, by the arrow keys (1 px, Shift 10 px), by
[ and ] (rotate 0.05 deg, Shift 0.5 deg) or by typing the numbers.
Each page can be given a quarter turn first.

The check is a known length across the seam: Measure Across Seam,
click one end on A and the other on B, type what the drawing says it
is. The measurement then follows B as it moves, so a scale bar that
spans the join can be brought to its printed length. That is the test
that found the Pogo spread's last error; pixel agreement did not (see
core/stitch.py).

Save writes the image and its .stitch.json through `jobs.stitch`, the
same code the `stitch` subcommand runs, so the GUI and the CLI cannot
disagree about what a placement means.

Scene coordinates are page A's displayed pixels with pixel CORNERS on
integers (Qt's convention); core/stitch.py puts pixel CENTRES on
integers. The two differ by exactly half a pixel, applied in
`_to_core` / `_to_scene`.
"""

import os

import numpy as np

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPushButton, QSlider, QSpinBox,
    QVBoxLayout, QWidget)

from ..core import calibration as calib
from ..core import image_io, jobs
from ..core import stitch as st
from .display import ndarray_to_qpixmap
from .zoom import anchor_under_cursor, wheel_zoom

NUDGE_PX = 1.0
NUDGE_BIG_PX = 10.0
TURN_STEP_DEG = 0.05
TURN_BIG_DEG = 0.5
DEFAULT_TRIM_PX = 12
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)"


def _to_core(pt):
    return (pt[0] - 0.5, pt[1] - 0.5)


def _to_scene(pt):
    return QPointF(pt[0] + 0.5, pt[1] + 0.5)


def _inverse_apply(m, pt):
    inv = np.linalg.inv(m)
    return (float(inv[0, 0] * pt[0] + inv[0, 1] * pt[1] + inv[0, 2]),
            float(inv[1, 0] * pt[0] + inv[1, 1] * pt[1] + inv[1, 2]))


class _PageB(QGraphicsPixmapItem):
    """Page B: draggable, and reports where it was dragged to."""

    def __init__(self, on_moved):
        super().__init__()
        self._on_moved = on_moved
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.OpenHandCursor)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._on_moved(self.pos())
        return super().itemChange(change, value)


class _View(QGraphicsView):
    """Wheel zoom; a click goes to the dialog while a measurement is
    armed; arrow keys and [ ] move page B."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self._dialog = dialog
        self.setFocusPolicy(Qt.StrongFocus)
        anchor_under_cursor(self)

    def wheelEvent(self, event):
        if not wheel_zoom(self, event):
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self._dialog.measuring() and event.button() == Qt.LeftButton:
            p = self.mapToScene(event.position().toPoint())
            self._dialog.pick(p)
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if not self._dialog.key(event.key(), event.modifiers()):
            super().keyPressEvent(event)


class StitchDialog(QDialog):
    """Place page B against page A and write the joined image.

    After exec(), `opened_path` is the saved image if the user asked
    to open it, else None."""

    def __init__(self, parent=None, path_a=None, path_b=None,
                 mm_per_px=None):
        super().__init__(parent)
        self.setWindowTitle("Stitch Pages")
        self.resize(1200, 800)
        self.opened_path = None
        self._paths = [None, None]
        self._imgs = [None, None]
        self._turns = [0, 0]
        self._check = None            # (a_file_pt, b_file_pt, expected_mm)
        self._pending_a = None        # first click of a measurement
        self._measuring = False
        self._syncing = False

        self._scene = QGraphicsScene(self)
        self._view = _View(self)
        self._view.setScene(self._scene)
        self._item_a = QGraphicsPixmapItem()
        self._item_b = _PageB(self._on_dragged)
        self._item_b.setOpacity(0.5)
        self._item_b.setZValue(1)
        self._scene.addItem(self._item_a)
        self._scene.addItem(self._item_b)
        pen = QPen(QColor(220, 0, 0))
        pen.setCosmetic(True)
        pen.setWidth(2)
        self._mark_a = self._marker(pen)
        self._mark_b = self._marker(pen)
        self._line = QGraphicsLineItem()
        self._line.setPen(pen)
        self._line.setZValue(3)
        self._line.hide()
        self._scene.addItem(self._line)

        # --- controls
        self._lbl_a = QLabel("(none)", self)
        self._lbl_b = QLabel("(none)", self)
        for lbl in (self._lbl_a, self._lbl_b):
            lbl.setWordWrap(True)
        btn_a = QPushButton("Page A…", self)
        btn_a.clicked.connect(lambda: self._choose(0))
        btn_b = QPushButton("Page B…", self)
        btn_b.clicked.connect(lambda: self._choose(1))
        btn_ta = QPushButton("Turn A 90°", self)
        btn_ta.clicked.connect(lambda: self._turn(0))
        btn_tb = QPushButton("Turn B 90°", self)
        btn_tb.clicked.connect(lambda: self._turn(1))

        self._dx = self._spin(-100000, 100000, 1.0, 1)
        self._dy = self._spin(-100000, 100000, 1.0, 1)
        self._angle = self._spin(-45.0, 45.0, TURN_STEP_DEG, 3)
        self._angle.setSuffix("°")
        for w in (self._dx, self._dy, self._angle):
            w.valueChanged.connect(self._on_numbers)

        self._opacity = QSlider(Qt.Horizontal, self)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(50)
        self._opacity.valueChanged.connect(
            lambda v: self._item_b.setOpacity(v / 100.0))

        self._mpp = QDoubleSpinBox(self)
        self._mpp.setDecimals(6)
        self._mpp.setRange(0.0, 100.0)
        self._mpp.setSingleStep(0.001)
        self._mpp.setSpecialValueText("unknown")
        self._mpp.setValue(mm_per_px or 0.0)
        self._mpp.valueChanged.connect(lambda _v: self._refresh_check())

        self._trim = QCheckBox("Trim to the ink, margin", self)
        self._trim.setChecked(True)
        self._trim_px = QSpinBox(self)
        self._trim_px.setRange(0, 1000)
        self._trim_px.setValue(DEFAULT_TRIM_PX)
        self._trim_px.setSuffix(" px")

        btn_measure = QPushButton("Measure Across Seam…", self)
        btn_measure.setToolTip("Click one end of a known length on page "
                               "A, the other end on page B, then type "
                               "its real length")
        btn_measure.clicked.connect(self.arm_measure)
        self._readout = QLabel("", self)
        self._readout.setWordWrap(True)

        btn_open = QPushButton("Open Stitch File…", self)
        btn_open.clicked.connect(self._open_sidecar)
        btn_save = QPushButton("Save…", self)
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Offset X:", self._dx)
        form.addRow("Offset Y:", self._dy)
        form.addRow("Angle:", self._angle)
        form.addRow("B opacity:", self._opacity)
        form.addRow("Scale (mm/px):", self._mpp)

        col = QVBoxLayout()
        col.addWidget(btn_a)
        col.addWidget(self._lbl_a)
        col.addWidget(btn_b)
        col.addWidget(self._lbl_b)
        turns = QHBoxLayout()
        turns.addWidget(btn_ta)
        turns.addWidget(btn_tb)
        col.addLayout(turns)
        col.addLayout(form)
        col.addWidget(QLabel("Drag B, or arrow keys (Shift ×10);\n"
                             "[ ] rotate (Shift ×10).", self))
        col.addSpacing(8)
        col.addWidget(btn_measure)
        col.addWidget(self._readout)
        trim = QHBoxLayout()
        trim.addWidget(self._trim)
        trim.addWidget(self._trim_px)
        col.addLayout(trim)
        col.addStretch(1)
        col.addWidget(btn_open)
        col.addWidget(btn_save)
        col.addWidget(btn_close)
        box = QWidget(self)
        box.setLayout(col)
        box.setMaximumWidth(300)

        lay = QHBoxLayout(self)
        lay.addWidget(self._view, 1)
        lay.addWidget(box)

        if path_a:
            self.set_page(0, path_a)
        if path_b:
            self.set_page(1, path_b)
        self._refresh_check()

    def showEvent(self, event):
        # a fit before the first show uses the view's default size
        super().showEvent(event)
        self._fit()

    def _fit(self):
        self._view.fitInView(self._scene.itemsBoundingRect(),
                             Qt.KeepAspectRatio)

    def _spin(self, lo, hi, step, decimals):
        s = QDoubleSpinBox(self)
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(decimals)
        return s

    def _marker(self, pen):
        path = QPainterPath()
        for (x0, y0, x1, y1) in ((-8, 0, 8, 0), (0, -8, 0, 8)):
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        item.setZValue(3)
        item.hide()
        self._scene.addItem(item)
        return item

    # ----- pages --------------------------------------------------------------
    def set_page(self, which, path):
        """Load page A (0) or B (1) from `path`. Returns True on success."""
        try:
            img = image_io.load_image(path).data
        except IOError as exc:
            QMessageBox.warning(self, "Stitch Pages", str(exc))
            return False
        self._paths[which] = path
        self._imgs[which] = img
        (self._lbl_a, self._lbl_b)[which].setText(os.path.basename(path))
        self._check = None
        self._redraw_page(which)
        self._fit()
        self._view.setFocus()
        return True

    def _choose(self, which):
        start = os.path.dirname(self._paths[0] or self._paths[1] or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Page %s" % "AB"[which], start, IMAGE_FILTER)
        if path:
            self.set_page(which, path)

    def _redraw_page(self, which):
        img = self._imgs[which]
        if img is None:
            return
        turned = st.turn_image(img, self._turns[which])
        pm = ndarray_to_qpixmap(turned)
        if which == 0:
            self._item_a.setPixmap(pm)
        else:
            self._item_b.setPixmap(pm)
            self._item_b.setTransformOriginPoint(pm.width() / 2.0,
                                                 pm.height() / 2.0)
        self._apply_placement()

    def _turn(self, which):
        self._turns[which] = (self._turns[which] + 90) % 360
        self._redraw_page(which)

    def _size(self, which):
        img = self._imgs[which]
        return (img.shape[1], img.shape[0]) if img is not None else None

    # ----- placement ----------------------------------------------------------
    def placement(self):
        return st.Placement(dx=self._dx.value(), dy=self._dy.value(),
                            angle_deg=self._angle.value(),
                            turn_a=self._turns[0], turn_b=self._turns[1])

    def set_placement(self, p):
        self._turns = [p.turn_a, p.turn_b]
        self._syncing = True
        self._dx.setValue(p.dx)
        self._dy.setValue(p.dy)
        self._angle.setValue(p.angle_deg)
        self._syncing = False
        self._redraw_page(0)
        self._redraw_page(1)
        self._apply_placement()

    def _apply_placement(self):
        self._syncing = True
        self._item_b.setPos(self._dx.value(), self._dy.value())
        self._item_b.setRotation(self._angle.value())
        self._syncing = False
        self._refresh_check()

    def _on_numbers(self, _value):
        if not self._syncing:
            self._apply_placement()

    def _on_dragged(self, pos):
        if self._syncing:
            return
        self._syncing = True
        self._dx.setValue(round(pos.x()))
        self._dy.setValue(round(pos.y()))
        # snap, so what the fields show is what gets saved
        self._item_b.setPos(self._dx.value(), self._dy.value())
        self._syncing = False
        self._refresh_check()

    def key(self, key, modifiers):
        """Arrow keys nudge B, [ and ] rotate it. True if handled."""
        big = bool(modifiers & Qt.ShiftModifier)
        step = NUDGE_BIG_PX if big else NUDGE_PX
        turn = TURN_BIG_DEG if big else TURN_STEP_DEG
        moves = {Qt.Key_Left: (self._dx, -step), Qt.Key_Right: (self._dx, step),
                 Qt.Key_Up: (self._dy, -step), Qt.Key_Down: (self._dy, step),
                 Qt.Key_BracketLeft: (self._angle, -turn),
                 Qt.Key_BracketRight: (self._angle, turn)}
        if key not in moves:
            return False
        spin, delta = moves[key]
        spin.setValue(spin.value() + delta)
        return True

    # ----- the seam check -----------------------------------------------------
    def measuring(self):
        return self._measuring

    def arm_measure(self):
        if self._imgs[0] is None or self._imgs[1] is None:
            self._readout.setText("Load both pages first.")
            return
        self._measuring = True
        self._pending_a = None
        self._readout.setText("Click one end of the known length on "
                              "page A.")
        self._view.setCursor(Qt.CrossCursor)
        self._view.setFocus()

    def pick(self, scene_pt):
        """One click of the measurement, in scene coordinates."""
        p = self.placement()
        core_pt = _to_core((scene_pt.x(), scene_pt.y()))
        if self._pending_a is None:
            a_file = _inverse_apply(st.a_matrix(p, self._size(0)), core_pt)
            if not self._inside(a_file, self._size(0)):
                self._readout.setText("That is not on page A - click one "
                                      "end on page A.")
                return
            self._pending_a = a_file
            self._readout.setText("Now click the other end, on page B.")
            return
        b_file = _inverse_apply(st.b_matrix(p, self._size(1)), core_pt)
        if not self._inside(b_file, self._size(1)):
            self._readout.setText("That is not on page B - click the other "
                                  "end on page B.")
            return
        a_file = self._pending_a
        self._measuring = False
        self._pending_a = None
        self._view.unsetCursor()
        text, ok = QInputDialog.getText(
            self, "Measure Across Seam",
            "What length does the drawing give for it?\n"
            "(e.g. 5 in, 127 mm, 12.7 cm):", text="5 in")
        if not ok:
            self._refresh_check()
            return
        try:
            expected = calib.parse_length_mm(text)
        except ValueError:
            self._readout.setText("Could not read a length from %r." % text)
            return
        self.set_check(a_file, b_file, expected)

    @staticmethod
    def _inside(pt, size):
        return (-0.5 <= pt[0] <= size[0] - 0.5
                and -0.5 <= pt[1] <= size[1] - 0.5)

    def set_check(self, a_file_pt, b_file_pt, expected_mm):
        """A known length across the seam: one end on each page, in that
        page's file pixels."""
        self._check = (tuple(a_file_pt), tuple(b_file_pt),
                       float(expected_mm))
        self._refresh_check()

    def check_result(self):
        """(measured_mm, expected_mm, error) for the current placement,
        or None without a check or a scale."""
        if self._check is None or not self._mpp.value():
            return None
        a_pt, b_pt, expected = self._check
        px = st.measure_across(self.placement(), self._size(0),
                               self._size(1), a_pt, b_pt)
        measured = px * self._mpp.value()
        return measured, expected, measured / expected - 1.0

    def _refresh_check(self):
        if self._check is None or any(i is None for i in self._imgs):
            for item in (self._mark_a, self._mark_b, self._line):
                item.hide()
            if not self._measuring:
                self._readout.setText("No seam check yet.")
            return
        p = self.placement()
        a_pt, b_pt, expected = self._check
        sa = _to_scene(st.map_a(p, self._size(0), a_pt))
        sb = _to_scene(st.map_b(p, self._size(1), b_pt))
        self._mark_a.setPos(sa)
        self._mark_b.setPos(sb)
        self._line.setLine(sa.x(), sa.y(), sb.x(), sb.y())
        for item in (self._mark_a, self._mark_b, self._line):
            item.show()
        r = self.check_result()
        if r is None:
            self._readout.setText("Seam check: %s expected. Set the scale "
                                  "to measure it."
                                  % calib.format_length(expected, "mm"))
            return
        measured, expected, err = r
        self._readout.setText(
            "Seam check: %s measured, %s expected (%+.2f%%)%s"
            % (calib.format_length(measured, "mm"),
               calib.format_length(expected, "mm"), 100.0 * err,
               "" if abs(err) <= 0.005 else
               "\nToo far out: the offset, the angle or the scale is "
               "wrong."))

    # ----- files --------------------------------------------------------------
    def load_sidecar(self, path):
        try:
            d = st.read_sidecar(path)
        except (IOError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Stitch Pages", str(exc))
            return False
        if not (self.set_page(0, d["page_a"])
                and self.set_page(1, d["page_b"])):
            return False
        if d.get("mm_per_px"):
            self._mpp.setValue(d["mm_per_px"])
        trim = d.get("trim_px")
        self._trim.setChecked(trim is not None)
        if trim is not None:
            self._trim_px.setValue(int(trim))
        self.set_placement(d["placement"])
        checks = d.get("checks") or []
        if checks:
            c = checks[0]
            self.set_check(c["a"], c["b"], c["expected_mm"])
        return True

    def _open_sidecar(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Stitch File", "",
            "Stitch files (*%s)" % st.SIDECAR_SUFFIX)
        if path:
            self.load_sidecar(path)

    def save_to(self, out_path):
        """Write the joined image and its stitch file. Returns the job
        result; raises jobs.JobError."""
        checks = []
        if self._check is not None and self._mpp.value():
            checks = [self._check]
        return jobs.stitch(
            self._paths[0], self._paths[1], out_path,
            placement=self.placement(),
            trim=self._trim_px.value() if self._trim.isChecked() else None,
            checks=checks, mm_per_px=self._mpp.value() or None)

    def _save(self):
        if None in self._paths:
            QMessageBox.information(self, "Stitch Pages",
                                    "Load both pages first.")
            return
        a, b = (os.path.splitext(os.path.basename(p))[0]
                for p in self._paths)
        start = os.path.join(os.path.dirname(self._paths[0]),
                             "%s+%s.png" % (a, b))
        path, _ = QFileDialog.getSaveFileName(self, "Save Stitched Image",
                                              start, IMAGE_FILTER)
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.save_to(path)
        except jobs.JobError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Stitch Pages", str(exc))
            return
        QApplication.restoreOverrideCursor()
        w, h = result.size
        note = ""
        if result.failed_checks:
            note = ("\n\nThe seam check is outside %.1f%%, so the join is "
                    "probably wrong." % (100.0 * result.tolerance))
        reply = QMessageBox.question(
            self, "Stitch Pages",
            "Saved %d x %d px to\n%s%s\n\nOpen it now?"
            % (w, h, path, note))
        if reply == QMessageBox.Yes:
            self.opened_path = path
            self.accept()
