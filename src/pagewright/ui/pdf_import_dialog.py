"""Page picker for PDF import (M2): choose pages + render DPI."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QVBoxLayout)


class PdfImportDialog(QDialog):
    """Lists the PDF's pages with checkboxes and a render-DPI choice.
    values() -> (selected 0-based indices, dpi)."""

    def __init__(self, sizes_mm, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import PDF Pages")
        self.resize(420, 480)
        lay = QVBoxLayout(self)
        self._list = QListWidget(self)
        for i, (w, h) in enumerate(sizes_mm):
            item = QListWidgetItem(
                "Page %d   (%.0f x %.0f mm)" % (i + 1, w, h))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)
        lay.addWidget(self._list, 1)

        row = QHBoxLayout()
        b_all = QPushButton("Select All", self)
        b_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        b_none = QPushButton("Select None", self)
        b_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row.addWidget(b_all)
        row.addWidget(b_none)
        row.addStretch(1)
        row.addWidget(QLabel("Render at:", self))
        self._dpi = QSpinBox(self)
        self._dpi.setRange(72, 1200)
        self._dpi.setValue(300)
        self._dpi.setSuffix(" DPI")
        row.addWidget(self._dpi)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _set_all(self, state):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def values(self):
        idx = [i for i in range(self._list.count())
               if self._list.item(i).checkState() == Qt.Checked]
        return idx, self._dpi.value()
