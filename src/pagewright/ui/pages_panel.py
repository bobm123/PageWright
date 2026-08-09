"""Pages panel (multi-page jobs, M1): the job's page list in the dock.

A pure view over the window's page list: rows show file names with a
checkbox for future batch actions; the current page is highlighted.
Double-click (or Activate) switches the working image to that page.
Signals only - MainWindow owns the actual page state.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)


class PagesPanel(QGroupBox):
    pageActivated = Signal(int)
    addImagesRequested = Signal()
    addFolderRequested = Signal()
    removeRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__("Pages", parent)
        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(
            lambda item: self.pageActivated.emit(self._list.row(item)))

        btn_imgs = QPushButton("Add Images…", self)
        btn_imgs.clicked.connect(self.addImagesRequested)
        btn_dir = QPushButton("Add Folder…", self)
        btn_dir.clicked.connect(self.addFolderRequested)
        btn_rm = QPushButton("Remove", self)
        btn_rm.clicked.connect(
            lambda: self.removeRequested.emit(self._list.currentRow()))

        row = QHBoxLayout()
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
