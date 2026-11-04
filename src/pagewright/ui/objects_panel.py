"""Objects panel for the side dock: the polygon list and its buttons, the
drawing margin, the stock/placement fields, and the traced-size readout.

A pure view. It renders whatever layer list it is handed and reports user
intent via signals; MainWindow owns the layers and does the work.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
    QWidget,
)


class ObjectsPanel(QWidget):
    """List of traced polygons + New/Delete/Rename, margin and size readout."""

    newRequested = Signal()
    deleteRequested = Signal()
    renameRequested = Signal()
    rowChanged = Signal(int)
    marginChanged = Signal(float)
    traceToleranceChanged = Signal(float)      # mm
    traceKeepLargestChanged = Signal(bool)
    visibilityToggled = Signal(object, bool)   # (ObjectLayer, visible)
    sheetThicknessChanged = Signal(object)     # mm, or None to clear
    buildChanged = Signal(str, object)         # (field name, value or None)

    def __init__(self, margin_mm=5.0, trace_tol_mm=0.35,
                 keep_largest=True, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget(self)
        self._list.currentRowChanged.connect(self.rowChanged)
        # the list is the point of the panel, so it keeps the stretch
        # the stock/placement group would otherwise take
        self._list.setMinimumHeight(140)
        layout.addWidget(self._list, 1)

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

        # Trace detail: the simplification tolerance in real-world mm.
        # Shown as a length rather than a "quality" number because that
        # is what it is - how far a vertex may move.
        tol_row = QHBoxLayout()
        tol_row.addWidget(QLabel("Trace detail:"))
        self._tol_spin = QDoubleSpinBox(self)
        self._tol_spin.setRange(0.02, 5.0)
        self._tol_spin.setDecimals(2)
        self._tol_spin.setSingleStep(0.05)
        self._tol_spin.setValue(trace_tol_mm)
        self._tol_spin.setSuffix(" mm")
        self._tol_spin.setToolTip(
            "How far a traced vertex may move when the outline is "
            "simplified.\n"
            "Smaller = more points and more detail; "
            "larger = fewer, smoother points.\n"
            "In millimetres, so the "
            "same setting behaves the same at any scan resolution.")
        self._tol_spin.valueChanged.connect(self.traceToleranceChanged)
        tol_row.addWidget(self._tol_spin)
        layout.addLayout(tol_row)

        self._keep_largest = QCheckBox("Trace one shape only", self)
        self._keep_largest.setChecked(keep_largest)
        self._keep_largest.setToolTip(
            "Keep only the largest shape found in the trace area (with "
            "its holes).\n"
            "Turn off to trace several separate shapes at "
            "once - on a busy drawing sheet that also picks up bits of "
            "neighbouring parts.")
        self._keep_largest.toggled.connect(self.traceKeepLargestChanged)
        layout.addWidget(self._keep_largest)

        # ----- stock and placement -----------------------------------------
        # A plan states one stock thickness for the whole sheet ("SHEET
        # NO.2 1/16"), so that is the project-wide default and a part
        # overrides it only where it differs.
        sheet_row = QHBoxLayout()
        sheet_row.addWidget(QLabel("Sheet stock:"))
        self._sheet = QLineEdit(self)
        self._sheet.setPlaceholderText("e.g. 1/16 in")
        self._sheet.setToolTip(
            "Stock thickness for the whole sheet.\n"
            "Fractions and units are accepted: 1/16 in, 3 1/2 in, "
            "1.5 mm.\n"
            "Leave empty if these parts are not being built.")
        self._sheet.editingFinished.connect(self._emit_sheet)
        sheet_row.addWidget(self._sheet)
        layout.addLayout(sheet_row)

        self._build_box = QGroupBox("Selected part", self)
        bl = QVBoxLayout(self._build_box)
        bl.setContentsMargins(6, 4, 6, 6)

        self._thick = QLineEdit(self._build_box)
        self._thick.setPlaceholderText("sheet default")
        self._thick.setToolTip(
            "This part's own thickness, where it differs from the "
            "sheet.\nEmpty = use the sheet stock above.")
        self._thick.editingFinished.connect(
            lambda: self._emit_len("thickness_mm", self._thick))
        bl.addLayout(self._labelled("Thickness:", self._thick))

        self._plane = QComboBox(self._build_box)
        self._plane.addItem("(not placed)", None)
        for code, hint in (("YZ", "YZ - fuselage former"),
                           ("XZ", "XZ - rib"),
                           ("XY", "XY - flat sheet")):
            self._plane.addItem(hint, code)
        self._plane.setToolTip(
            "Which plane the part stands on in the assembly.\n"
            "A plan sheet is nested to save material, so where a part "
            "belongs\ncannot be read from where it was drawn.")
        self._plane.currentIndexChanged.connect(self._emit_plane)
        bl.addLayout(self._labelled("Plane:", self._plane))

        self._station = QLineEdit(self._build_box)
        self._station.setPlaceholderText("0")
        self._station.setToolTip(
            "How far along that plane's normal the part sits.\n"
            "May be negative. Units accepted, as for thickness.")
        self._station.editingFinished.connect(
            lambda: self._emit_len("station_mm", self._station,
                                   allow_negative=True))
        bl.addLayout(self._labelled("Station:", self._station))

        self._rotate = QDoubleSpinBox(self._build_box)
        self._rotate.setRange(-180.0, 180.0)
        self._rotate.setDecimals(1)
        self._rotate.setSingleStep(5.0)
        self._rotate.setSuffix(" deg")
        self._rotate.setToolTip("Turn the part within its own plane.")
        self._rotate.valueChanged.connect(
            lambda v: self.buildChanged.emit("rotate_deg", v or None))
        bl.addLayout(self._labelled("Rotate:", self._rotate))

        self._build_note = QLabel("", self._build_box)
        self._build_note.setWordWrap(True)
        self._build_note.setStyleSheet("color: #888;")
        bl.addWidget(self._build_note)
        self._build_box.setEnabled(False)
        layout.addWidget(self._build_box)

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

    # ----- stock and placement ---------------------------------------------

    @staticmethod
    def _labelled(text, widget):
        row = QHBoxLayout()
        lab = QLabel(text)
        lab.setMinimumWidth(68)
        row.addWidget(lab)
        row.addWidget(widget, 1)
        return row

    @staticmethod
    def _read_len(field, allow_negative=False):
        """Text -> mm, None when empty, or False when unparseable.

        False rather than an exception: a half-typed "1/" deserves a
        marked field, not a dialog.
        """
        from ..core.calibration import parse_length_mm
        text = field.text().strip()
        if not text:
            return None
        negative = allow_negative and text.startswith("-")
        try:
            mm = parse_length_mm(text[1:] if negative else text)
        except ValueError:
            return False
        return -mm if negative else mm

    @staticmethod
    def _mark(field, bad):
        field.setStyleSheet("background: #ffe4e4;" if bad else "")

    def _emit_sheet(self):
        mm = self._read_len(self._sheet)
        self._mark(self._sheet, mm is False)
        if mm is not False:
            self.sheetThicknessChanged.emit(mm)

    def _emit_len(self, field_name, field, allow_negative=False):
        mm = self._read_len(field, allow_negative)
        self._mark(field, mm is False)
        if mm is not False:
            self.buildChanged.emit(field_name, mm)

    def _emit_plane(self, _index):
        self.buildChanged.emit("plane", self._plane.currentData())

    def set_sheet_thickness(self, mm):
        self._sheet.blockSignals(True)
        self._sheet.setText("" if not mm else "%g mm" % mm)
        self._sheet.blockSignals(False)
        self._mark(self._sheet, False)

    def set_build(self, build, sheet_mm=None):
        """Show a model.Build, or clear and disable the group when None."""
        fields = (self._thick, self._station, self._plane, self._rotate)
        self._build_box.setEnabled(build is not None)
        for w in fields:
            w.blockSignals(True)

        self._thick.setText(
            "" if build is None or build.thickness_mm is None
            else "%g mm" % build.thickness_mm)
        self._thick.setPlaceholderText(
            "sheet default" if not sheet_mm else "%g mm (sheet)" % sheet_mm)
        index = self._plane.findData(None if build is None else build.plane)
        self._plane.setCurrentIndex(index if index >= 0 else 0)
        self._station.setText(
            "" if build is None or build.station_mm is None
            else "%g mm" % build.station_mm)
        self._rotate.setValue(
            0.0 if build is None or build.rotate_deg is None
            else build.rotate_deg)

        for w in fields:
            w.blockSignals(False)
        for w in (self._thick, self._station):
            self._mark(w, False)
        self._build_note.setText(self._describe(build, sheet_mm))

    @staticmethod
    def _describe(build, sheet_mm):
        """One line saying what will actually be built, so the effective
        thickness is visible when it comes from the sheet default."""
        if build is None:
            return ""
        mm = (build.thickness_mm if build.thickness_mm is not None
              else sheet_mm)
        if mm is None:
            return "No thickness yet: set the sheet stock to build this."
        if not build.plane:
            return "%g mm stock; not placed yet." % mm
        return "%g mm on %s at %g mm." % (mm, build.plane,
                                          build.station_mm or 0.0)
