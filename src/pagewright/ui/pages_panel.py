"""Pages panel (multi-page jobs, M1): the job's page list in the dock.

A pure view over the window's page list: rows show file names with a
checkbox for future batch actions; the current page is highlighted.
Double-click (or Activate) switches the working image to that page.
Signals only - MainWindow owns the actual page state.
"""

import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QListWidget,
                               QListWidgetItem, QPushButton, QToolButton,
                               QVBoxLayout)


def _eye_pixmap(color, crossed=False):
    """A 16x16 eye glyph drawn in code (no icon assets; ASCII rule)."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.drawEllipse(QRectF(2, 5, 12, 6))          # eye outline
    p.setBrush(color)
    p.drawEllipse(QRectF(6.5, 6.5, 3, 3))       # pupil
    if crossed:
        p.drawLine(3, 14, 13, 2)                # hidden = slash
    p.end()
    return pm


def _eye_icon(color):
    """Checked (visible) = open eye; unchecked (hidden) = slashed."""
    icon = QIcon()
    icon.addPixmap(_eye_pixmap(color), QIcon.Normal, QIcon.On)
    icon.addPixmap(_eye_pixmap(color, crossed=True), QIcon.Normal, QIcon.Off)
    return icon


class PagesPanel(QGroupBox):
    pageActivated = Signal(int)
    imageVisibilityToggled = Signal(bool)
    addImagesRequested = Signal()
    addFolderRequested = Signal()
    removeRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__("Pages", parent)
        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(
            lambda item: self.pageActivated.emit(self._list.row(item)))

        self._btn_eye = QToolButton(self)
        self._btn_eye.setCheckable(True)
        self._btn_eye.setChecked(True)
        self._btn_eye.setAutoRaise(True)
        self._btn_eye.setIcon(_eye_icon(self.palette().text().color()))
        self._btn_eye.setToolTip(
            "Show / hide the image (polygon traces stay visible)")
        self._btn_eye.toggled.connect(self.imageVisibilityToggled)

        btn_imgs = QPushButton("Add Images…", self)
        btn_imgs.clicked.connect(self.addImagesRequested)
        btn_dir = QPushButton("Add Folder…", self)
        btn_dir.clicked.connect(self.addFolderRequested)
        btn_rm = QPushButton("Remove", self)
        btn_rm.clicked.connect(
            lambda: self.removeRequested.emit(self._list.currentRow()))

        row = QHBoxLayout()
        row.addWidget(self._btn_eye)
        for b in (btn_imgs, btn_dir, btn_rm):
            row.addWidget(b)

        lay = QVBoxLayout(self)
        lay.addWidget(self._list)
        lay.addLayout(row)

    # ----- view state -------------------------------------------------------
    def set_pages(self, paths, current):
        """Rebuild the list from page source paths; highlight `current`."""
        self._list.blockSignals(True)
        self._list.clear()
        for i, path in enumerate(paths):
            item = QListWidgetItem(
                "%d. %s" % (i + 1, os.path.basename(path or "(pasted)")))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setToolTip(path or "")
            self._list.addItem(item)
        if 0 <= current < self._list.count():
            self._list.setCurrentRow(current)
            f = self._list.item(current).font()
            f.setBold(True)
            self._list.item(current).setFont(f)
        self._list.blockSignals(False)

    def checked_rows(self):
        """Rows ticked for (future) batch actions."""
        return [i for i in range(self._list.count())
                if self._list.item(i).checkState() == Qt.Checked]
