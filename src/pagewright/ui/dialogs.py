"""Dialogs: calibration entry (Phase 1) and SVG export options (Phase 4).
Tiling/print dialogs are added in later phases.
"""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..core.calibration import MM_PER_UNIT, parse_length_mm
from ..model import Sheet


class CalibrationDialog(QDialog):
    """Ask the user for the real-world length of the segment they measured."""

    def __init__(self, pixel_distance, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calibrate Scale")

        form = QFormLayout(self)
        form.addRow(QLabel(
            "Measured segment: %.1f px.\n"
            "Enter its real-world length:" % pixel_distance))

        self._value = QDoubleSpinBox(self)
        self._value.setDecimals(3)
        self._value.setRange(0.001, 1_000_000.0)
        self._value.setValue(100.0)
        form.addRow("Length:", self._value)

        self._unit = QComboBox(self)
        # Stable, predictable order rather than dict order.
        for unit in ("mm", "cm", "in"):
            if unit in MM_PER_UNIT:
                self._unit.addItem(unit)
        form.addRow("Unit:", self._unit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        """Return (real_length, unit) entered by the user."""
        return self._value.value(), self._unit.currentText()


class ExportSvgDialog(QDialog):
    """Options for exporting a true-scale SVG (Phase 4)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export SVG")

        form = QFormLayout(self)
        form.addRow(QLabel(
            "Exports at true physical size (mm). Print with\n"
            "'fit to page' / auto-scaling OFF for 1:1 output."))

        self._embed = QCheckBox("Embed source photo as a background layer", self)
        self._embed.setChecked(True)
        form.addRow(self._embed)

        self._downscale = QCheckBox("Downscale embedded photo", self)
        self._downscale.setChecked(True)
        form.addRow(self._downscale)

        self._max_edge = QSpinBox(self)
        self._max_edge.setRange(256, 20000)
        self._max_edge.setSingleStep(256)
        self._max_edge.setValue(2000)
        self._max_edge.setSuffix(" px (longest edge)")
        form.addRow("Max photo size:", self._max_edge)

        self._filled = QCheckBox("Fill objects (otherwise outline only)", self)
        form.addRow(self._filled)

        self._annotations = QCheckBox(
            "Include annotation layers (rulers, select area, tile grid)",
            self)
        self._annotations.setChecked(True)
        self._annotations.setToolTip(
            "Exports the on-screen overlays on their own layers: "
            "measurement rulers go to a 'Measurements' layer, and the "
            "Select Area rectangle plus the tile grid (when its overlay "
            "is shown) to an 'Annotations' layer - so rulers can be "
            "hidden separately in Inkscape.")
        form.addRow(self._annotations)

        self._inkscape = QCheckBox(
            "Inkscape format (named layers, mm document units)", self)
        self._inkscape.setToolTip(
            "Adds Inkscape layers and metadata so you can fine-tune nodes "
            "there. Leave off for a plain SVG any browser opens.")
        form.addRow(self._inkscape)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._embed.toggled.connect(self._sync_enabled)
        self._downscale.toggled.connect(self._sync_enabled)
        self._sync_enabled()

    def _sync_enabled(self):
        self._downscale.setEnabled(self._embed.isChecked())
        self._max_edge.setEnabled(
            self._embed.isChecked() and self._downscale.isChecked())

    def values(self):
        """(embed_photo, downscale_max_or_None, filled, inkscape,
        annotations)."""
        embed = self._embed.isChecked()
        downscale_max = None
        if embed and self._downscale.isChecked():
            downscale_max = self._max_edge.value()
        return (embed, downscale_max, self._filled.isChecked(),
                self._inkscape.isChecked(),
                self._annotations.isChecked())


class TilingDialog(QDialog):
    """Options for tiled 1:1 printing (Phase 5)."""

    def __init__(self, page_sizes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Print Tiles")

        form = QFormLayout(self)
        form.addRow(QLabel(
            "Splits the drawing across pages at true 1:1 scale.\n"
            "Print with 'fit to page' / auto-scaling OFF."))

        self._page = QComboBox(self)
        for name in page_sizes:
            self._page.addItem(name)
        form.addRow("Page size:", self._page)

        self._orientation = QComboBox(self)
        self._orientation.addItems(["Portrait", "Landscape"])
        form.addRow("Orientation:", self._orientation)

        self._margin = QDoubleSpinBox(self)
        self._margin.setRange(0.0, 50.0)
        self._margin.setDecimals(1)
        self._margin.setValue(6.0)
        self._margin.setSuffix(" mm")
        form.addRow("Printer margin:", self._margin)

        self._overlap = QDoubleSpinBox(self)
        self._overlap.setRange(0.0, 100.0)
        self._overlap.setDecimals(1)
        self._overlap.setValue(10.0)
        self._overlap.setSuffix(" mm")
        form.addRow("Overlap:", self._overlap)

        self._embed = QCheckBox("Include the photo on each tile", self)
        form.addRow(self._embed)

        self._filled = QCheckBox("Fill objects (otherwise outline only)", self)
        form.addRow(self._filled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        """Return (page, landscape, margin_mm, overlap_mm, embed, filled)."""
        return (self._page.currentText(),
                self._orientation.currentText() == "Landscape",
                self._margin.value(),
                self._overlap.value(),
                self._embed.isChecked(),
                self._filled.isChecked())


class PreferencesDialog(QDialog):
    """Application preferences (currently the seed brush size)."""

    def __init__(self, brush_radius, brush_auto=True,
                 preferred_unit="mm", mark_color="#ff00ff",
                 mark_opacity=0.45, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")

        form = QFormLayout(self)
        form.addRow(QLabel(
            "Settings that apply to the app rather than the project."))

        self._brush = QSpinBox(self)
        self._brush.setRange(1, 500)
        self._brush.setValue(int(round(brush_radius)))
        self._brush.setSuffix(" px")
        self._brush.setToolTip(
            "Radius of the foreground/background marking brush. The ring at "
            "the cursor previews this size while marking.")
        form.addRow("Brush size:", self._brush)

        self._brush_auto = QCheckBox("Auto (screen size, follows zoom)",
                                     self)
        self._brush_auto.setChecked(bool(brush_auto))
        self._brush_auto.setToolTip(
            "Auto: the size is in screen pixels - zoom in for fine "
            "marks, out for broad ones, on any size document.\n"
            "Unchecked: the size is fixed in image pixels.")
        form.addRow("", self._brush_auto)

        self._units = QComboBox(self)
        self._units.addItems(["mm", "cm", "in"])
        i = self._units.findText(preferred_unit)
        self._units.setCurrentIndex(i if i >= 0 else 0)
        self._units.setToolTip(
            "Default display unit for new jobs (readouts, Measure, "
            "size fields). A project keeps its own unit once saved; "
            "View > Units still switches per project.")
        form.addRow("Preferred units:", self._units)

        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget
        self._mark_color = QColor(mark_color)
        self._mark_color.setAlphaF(float(mark_opacity))
        self._mark_btn = QPushButton(self)
        self._mark_btn.setFixedSize(48, 22)
        self._mark_btn.setToolTip(
            "Color of the printed tile marks: the dashed live-area "
            "outline, the R-C label, and the registration diamonds. "
            "The opacity (alpha) applies to the diamond FILL so the "
            "pattern under a diamond stays visible.")
        self._mark_btn.clicked.connect(self._pick_mark_color)
        btn_reset = QPushButton("Reset to default", self)
        btn_reset.clicked.connect(self._reset_mark_color)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._mark_btn)
        row.addWidget(btn_reset)
        row.addStretch(1)
        host = QWidget(self)
        host.setLayout(row)
        form.addRow("Tile marks:", host)
        self._update_mark_swatch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def brush_radius(self):
        return self._brush.value()

    def brush_auto(self):
        return self._brush_auto.isChecked()

    def preferred_unit(self):
        return self._units.currentText()

    def _update_mark_swatch(self):
        c = self._mark_color
        self._mark_btn.setStyleSheet(
            "background-color: rgba(%d,%d,%d,%d); border: 1px solid #888;"
            % (c.red(), c.green(), c.blue(), c.alpha()))

    def _pick_mark_color(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(
            self._mark_color, self, "Tile mark color",
            QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self._mark_color = c
            self._update_mark_swatch()

    def _reset_mark_color(self):
        from PySide6.QtGui import QColor
        from ..core.tiling import MARK_COLOR, MARK_OPACITY
        self._mark_color = QColor(MARK_COLOR)
        self._mark_color.setAlphaF(MARK_OPACITY)
        self._update_mark_swatch()

    def mark_values(self):
        """("#rrggbb", opacity 0..1)."""
        c = self._mark_color
        return (c.name(), round(c.alphaF(), 3))


class SheetStockDialog(QDialog):
    """Name the stock sheets a model is cut from, with a thickness each.

    Two columns, free text in both: thickness is entered the way plans
    and suppliers state it ("1/16 in", "3/32"), and parsed on OK so a
    half-typed fraction never reaches the model.

    Sheets are matched to parts BY NAME, so renaming one here would
    orphan its parts. The window therefore reports renames alongside
    the new list and lets the caller carry the parts across - the sheet
    list is not the system of record for which part is on which sheet.
    """

    def __init__(self, sheets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sheet Stock")
        self._original = [sh.name for sh in sheets]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Sheets this model is cut from. Thickness accepts "
            "fractions: 1/16 in, 3/32, 1.5 mm."))

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["Sheet", "Thickness"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        for sh in sheets:
            self._append(sh.name,
                         "" if sh.thickness_mm is None
                         else "%g mm" % sh.thickness_mm)
        layout.addWidget(self._table, 1)

        row = QHBoxLayout()
        btn_add = QPushButton("Add", self)
        btn_add.clicked.connect(lambda: self._append("", ""))
        btn_del = QPushButton("Remove", self)
        btn_del.clicked.connect(self._remove_current)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sheets = list(sheets)
        self._renames = {}
        self.resize(360, 260)

    # ----- results ---------------------------------------------------------

    def sheets(self):
        """The edited [Sheet] list (valid after accept())."""
        return self._sheets

    def renames(self):
        """{old name: new name} for sheets the user renamed in place.

        Positional: row i was row i, so a changed name in that row is a
        rename rather than a delete plus an add. The caller uses this to
        move the parts over."""
        return self._renames

    # ----- internals -------------------------------------------------------

    def _append(self, name, thickness):
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(name))
        self._table.setItem(r, 1, QTableWidgetItem(thickness))

    def _remove_current(self):
        r = self._table.currentRow()
        if r >= 0:
            self._table.removeRow(r)

    def _cell(self, row, col):
        item = self._table.item(row, col)
        return "" if item is None else item.text().strip()

    def _try_accept(self):
        sheets, renames, seen = [], {}, set()
        for r in range(self._table.rowCount()):
            name = self._cell(r, 0)
            raw = self._cell(r, 1)
            if not name and not raw:
                continue                      # an untouched blank row
            if not name:
                QMessageBox.warning(self, "Sheet Stock",
                                    "Row %d has a thickness but no name."
                                    % (r + 1,))
                return
            if name in seen:
                # Names are the key parts point at, so duplicates would
                # make the assignment ambiguous.
                QMessageBox.warning(
                    self, "Sheet Stock",
                    "There are two sheets called %r. Names must differ."
                    % (name,))
                return
            seen.add(name)
            mm = None
            if raw:
                try:
                    mm = parse_length_mm(raw)
                except ValueError as exc:
                    QMessageBox.warning(
                        self, "Sheet Stock", "Sheet %r: %s" % (name, exc))
                    return
            sheets.append(Sheet(name, mm))
            if r < len(self._original) and self._original[r] != name:
                renames[self._original[r]] = name
        self._sheets = sheets
        self._renames = renames
        self.accept()
