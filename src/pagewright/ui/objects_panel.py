"""Objects panel for the side dock: the polygon list and its buttons, the
drawing margin, and the traced-size readout.

A pure view. It renders whatever layer list it is handed and reports user
intent via signals; MainWindow owns the layers and does the work.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)


class ObjectsPanel(QWidget):
    """List of traced polygons + New/Delete/Rename, margin and size readout."""

    newRequested = Signal()
    deleteRequested = Signal()
    renameRequested = Signal()
    rowChanged = Signal(int)
    marginChanged = Signal(float)
    visibilityToggled = Signal(object, bool)   # (ObjectLayer, visible)

    def __init__(self, margin_mm=5.0, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget(self)
        self._list.currentRowChanged.connect(self.rowChanged)
        layout.addWidget(self._list)

        row = QHBoxLayout()
        btn_new = QPushButton("New", self)
        btn_new.clicked.connect(lambda: self.newRequested.emit())
        btn_del = QPushButton("Delete", self)
        btn_del.clicked.connect(lambda: self.deleteRequested.emit())
        btn_ren = QPushButton("Rename", self)
        btn_ren.clicked.connect(lambda: self.renameRequested.emit())
        row.addWidget(btn_new)
        row.addWidget(btn_del)
        row.addWidget(btn_ren)
        layout.addLayout(row)

        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Margin:"))
        self._margin_spin = QDoubleSpinBox(self)
        self._margin_spin.setRange(0.0, 1000.0)
        self._margin_spin.setDecimals(1)
        self._margin_spin.setSingleStep(1.0)
        self._margin_spin.setValue(margin_mm)
        self._margin_spin.setSuffix(" mm")
        self._margin_spin.valueChanged.connect(self.marginChanged)
        margin_row.addWidget(self._margin_spin)
        layout.addLayout(margin_row)

        self._size_label = QLabel("", self)
        self._size_label.setWordWrap(True)
        layout.addWidget(self._size_label)

    # ----- view updates ----------------------------------------------------

    def set_layers(self, layers, active_index):
        """Rebuild the rows from `layers` and select `active_index`."""
        self._list.blockSignals(True)
        self._list.clear()
        for layer in layers:
            item = QListWidgetItem(self._list)
            row = self._make_row(layer)
            item.setSizeHint(row.sizeHint())
            self._list.setItemWidget(item, row)
        if 0 <= active_index < len(layers):
            self._list.setCurrentRow(active_index)
        self._list.blockSignals(False)

    def _make_row(self, layer):
        """A row: the polygon name plus a right-aligned show/hide box."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 1, 4, 1)
        h.addWidget(QLabel(layer.name))
        h.addStretch(1)
        cb = QCheckBox(w)
        cb.setChecked(layer.visible)
        cb.setToolTip("Show / hide this polygon")
        cb.toggled.connect(
            lambda checked, lyr=layer:
            self.visibilityToggled.emit(lyr, checked))
        h.addWidget(cb)
        return w

    def set_margin(self, value):
        self._margin_spin.blockSignals(True)
        self._margin_spin.setValue(float(value))
        self._margin_spin.blockSignals(False)

    def set_size_text(self, text):
        self._size_label.setText(text)
