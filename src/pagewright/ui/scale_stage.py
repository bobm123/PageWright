"""Scale stage: set a document's real-world size - sometimes the ONLY
thing a document needs (load -> calibrate -> print or save).

A first-class tool in the main window's stack. The two-point calibrate
gesture is front and center: press Calibrate (or right-click the image),
click the two ends of a known feature (a ruler, a printed dimension),
type its real length ('5.25 in', '133.35 mm', '56 cm'). The unit-aware
Width/Height fields show the whole image's resulting real size and can
be EDITED instead - typing a known overall width is an equally valid way
to set the scale. Both paths drive the single mm/px scalar in the
project calibration, so aspect is inherently true and every other tool
(tiling, export, readouts) follows.

Outputs: Print Tiles... (the shared tile preview/print dialog) and Save
Scaled Image... (resample to the true size at a chosen DPI).

NOTE: PySide6 cannot run in the porting sandbox; static-checked only.
"""

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ..core import calibration as calib_core
from .dewarp_stage import MmSpinBox, _ResultView


class ScaleStageWidget(QWidget):
    """First-class Scale tool. Call set_source_image() on entry; emits
    :attr:`mmPerPixelChanged` whenever the user sets the scale (the
    window applies it to the project) and :attr:`cancelled` on Back."""

    mmPerPixelChanged = Signal(float)
    cancelled = Signal()
    printRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._px_w = self._px_h = 0
        self._mpp = None
        self._syncing = False

        self._image = _ResultView(self)
        self._image.calibrationPicked.connect(self._on_measured)
        self._image.menuRequested.connect(self._show_menu)

        # controls column
        btn_cal = QPushButton("Calibrate…", self)
        btn_cal.setToolTip(
            "Click the two ends of a feature of known length (a ruler, "
            "a printed dimension), then enter that length")
        btn_cal.clicked.connect(self._image.arm_calibration)

        self._w_mm = MmSpinBox(self)
        self._w_mm.setRange(1.0, 100000.0)
        self._h_mm = MmSpinBox(self)
        self._h_mm.setRange(1.0, 100000.0)
        self._w_mm.valueChanged.connect(self._on_width_edited)
        self._h_mm.valueChanged.connect(self._on_height_edited)

        self._dpi = QSpinBox(self)
        self._dpi.setRange(30, 1200)
        self._dpi.setValue(300)
        self._dpi.setSuffix(" DPI")

        self._readout = QLabel("Not calibrated", self)
        self._readout.setWordWrap(True)

        btn_print = QPushButton("Print Tiles…", self)
        btn_print.clicked.connect(self.printRequested.emit)
        btn_save = QPushButton("Save Scaled Image…", self)
        btn_save.setToolTip("Resample to the true size at the chosen DPI "
                            "and save")
        btn_save.clicked.connect(self._save_scaled)
        btn_back = QPushButton("Back", self)
        btn_back.clicked.connect(self.cancelled.emit)

        col = QVBoxLayout()
        col.addWidget(btn_cal)
        col.addSpacing(8)
        col.addWidget(QLabel("Image width:", self))
        col.addWidget(self._w_mm)
        col.addWidget(QLabel("Image height:", self))
        col.addWidget(self._h_mm)
        col.addWidget(QLabel("Output resolution:", self))
        col.addWidget(self._dpi)
        col.addSpacing(8)
        col.addWidget(self._readout)
        col.addStretch(1)
        col.addWidget(btn_print)
        col.addWidget(btn_save)
        col.addWidget(btn_back)
        box = QWidget(self)
        box.setLayout(col)
        box.setMaximumWidth(280)

        lay = QHBoxLayout(self)
        lay.addWidget(self._image, 1)
        lay.addWidget(box)

    # ----- entry ------------------------------------------------------------
    def set_source_image(self, bgr, mm_per_pixel=None):
        """Enter the stage. mm_per_pixel is the project's current
        calibration (None when uncalibrated)."""
        self._src = np.ascontiguousarray(bgr)
        self._px_h, self._px_w = self._src.shape[:2]
        self._mpp = mm_per_pixel
        self._image.set_image(self._src)
        self._sync_fields()

    # ----- scale plumbing ---------------------------------------------------
    def _sync_fields(self):
        """Show the image's real size from the current mm/px (fields stay
        editable even when uncalibrated - typing a size calibrates)."""
        self._syncing = True
        if self._mpp:
            self._w_mm.setValue(self._px_w * self._mpp)
            self._h_mm.setValue(self._px_h * self._mpp)
            self._readout.setText(
                "%.4f mm/px\n%s x %s"
                % (self._mpp,
                   calib_core.format_length(self._px_w * self._mpp, "mm"),
                   calib_core.format_length(self._px_h * self._mpp, "mm")))
        else:
            self._w_mm.setValue(self._px_w or 1)      # placeholder values
            self._h_mm.setValue(self._px_h or 1)
            self._readout.setText(
                "Not calibrated - measure a known feature with "
                "Calibrate, or type the image's real width/height.")
        self._syncing = False

    def _apply_mpp(self, mpp):
        self._mpp = mpp
        self._sync_fields()
        self.mmPerPixelChanged.emit(mpp)

    def _on_width_edited(self, value):
        if self._syncing or self._px_w <= 0:
            return
        self._apply_mpp(value / self._px_w)

    def _on_height_edited(self, value):
        if self._syncing or self._px_h <= 0:
            return
        self._apply_mpp(value / self._px_h)

    def _on_measured(self, p1, p2):
        d_px = ((p2.x() - p1.x()) ** 2 + (p2.y() - p1.y()) ** 2) ** 0.5
        if d_px < 2.0:
            self._readout.setText("The two points are too close; try "
                                  "again zoomed in.")
            return
        text, ok = QInputDialog.getText(
            self, "Calibrate Scale",
            "Real-world length of the measured feature\n"
            "(e.g. 100 mm, 5.25 in, 56 cm):", text="100 mm")
        if not ok:
            return
        try:
            real_mm = calib_core.parse_length_mm(text)
        except ValueError:
            self._readout.setText("Could not parse length: %s" % text)
            return
        self._apply_mpp(real_mm / d_px)

    # ----- outputs ----------------------------------------------------------
    def _save_scaled(self):
        if self._src is None:
            return
        if not self._mpp:
            QMessageBox.information(
                self, "Save Scaled Image",
                "Set the scale first (Calibrate, or type the real "
                "width/height).")
            return
        import cv2
        dpi = self._dpi.value()
        out_w = max(1, int(round(self._px_w * self._mpp / 25.4 * dpi)))
        out_h = max(1, int(round(self._px_h * self._mpp / 25.4 * dpi)))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Scaled Image", "scaled.png",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not path:
            return
        interp = (cv2.INTER_AREA if out_w < self._px_w
                  else cv2.INTER_CUBIC)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            out = cv2.resize(self._src, (out_w, out_h),
                             interpolation=interp)
            ok = cv2.imwrite(path, out)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            QMessageBox.critical(self, "Save Scaled Image",
                                 "Could not write %s" % path)
            return
        self._readout.setText(
            "Saved %dx%d px (%s x %s @ %d DPI)\n%s"
            % (out_w, out_h,
               calib_core.format_length(self._px_w * self._mpp, "mm"),
               calib_core.format_length(self._px_h * self._mpp, "mm"),
               dpi, path))

    # ----- context menu -----------------------------------------------------
    def _show_menu(self, global_pos):
        menu = QMenu(self)
        menu.addAction("Calibrate…", self._image.arm_calibration)
        menu.addSeparator()
        menu.addAction("Fit to Window", self._image.refit)
        menu.exec(global_pos)
