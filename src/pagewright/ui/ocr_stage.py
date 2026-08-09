"""OCR stage: recognize text on the working image (studio roadmap P4).

A first-class stage in the main window's central stack, like the dewarp
stage: image on the left (wheel zoom / drag pan, same feel as every
other view), recognized text on the right in an editable text pane.

Run OCR reads the whole image; OCR Selected Area reads only the Select
Area rectangle chosen in the Trace view (when one is set). The text can
be edited, copied, or saved to a .txt file.

NOTE: PySide6 cannot run in the porting sandbox, so this module is
static-checked only.
"""

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout,
                               QWidget)

from ..core import ocr as ocr_core
from .dewarp_stage import _ResultView


class OcrStageWidget(QWidget):
    """First-class OCR stage. Call set_source_image() on entry; the
    optional region (Select Area, image px) enables area OCR. Emits
    :attr:`cancelled` when the user goes back."""

    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src = None
        self._region = None

        self._image = _ResultView(self)
        self._text = QPlainTextEdit(self)
        self._text.setPlaceholderText(
            "Recognized text appears here. Run OCR to start.")

        split = QSplitter(Qt.Horizontal, self)
        split.addWidget(self._image)
        split.addWidget(self._text)
        split.setSizes([550, 450])

        self._status = QLabel("", self)

        row = QHBoxLayout()
        self._btn_run = QPushButton("Run OCR", self)
        self._btn_run.clicked.connect(lambda: self._run(False))
        self._btn_area = QPushButton("OCR Selected Area", self)
        self._btn_area.setToolTip(
            "Recognize only the Select Area rectangle from the Trace view")
        self._btn_area.clicked.connect(lambda: self._run(True))
        btn_copy = QPushButton("Copy Text", self)
        btn_copy.clicked.connect(self._copy)
        btn_save = QPushButton("Save Text…", self)
        btn_save.clicked.connect(self._save)
        btn_back = QPushButton("Back", self)
        btn_back.clicked.connect(self.cancelled.emit)
        for b in (self._btn_run, self._btn_area, btn_copy, btn_save):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addWidget(self._status, 1)
        row.addWidget(btn_back)

        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addLayout(row)

    # ----- entry ------------------------------------------------------------
    def set_source_image(self, bgr, region_px=None):
        self._src = np.ascontiguousarray(bgr)
        self._region = region_px
        self._image.set_image(self._src)
        self._btn_area.setEnabled(region_px is not None)
        self._status.setText(
            "Area set: OCR Selected Area reads only it." if region_px
            else "")

    # ----- actions ----------------------------------------------------------
    def _run(self, area_only):
        if self._src is None:
            return
        err = ocr_core.availability_error()
        if err:
            QMessageBox.warning(self, "OCR", err)
            return
        region = self._region if area_only else None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text = ocr_core.image_to_text(self._src, region_px=region)
        except RuntimeError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "OCR", str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "OCR", "OCR failed: %s" % (exc,))
            return
        QApplication.restoreOverrideCursor()
        self._text.setPlainText(text)
        words = len(text.split())
        self._status.setText("Recognized ~%d words%s." % (
            words, " in the selected area" if region is not None else ""))

    def _copy(self):
        QApplication.clipboard().setText(self._text.toPlainText())
        self._status.setText("Text copied to the clipboard.")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Recognized Text", "ocr.txt", "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._text.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, "Save Text", str(exc))
            return
        self._status.setText("Saved %s" % path)
