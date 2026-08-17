"""Dialogs: calibration entry (Phase 1) and SVG export options (Phase 4).
Tiling/print dialogs are added in later phases.
"""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QSpinBox,
)

from ..core.calibration import MM_PER_UNIT


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
        """Return (embed_photo, downscale_max_or_None, filled, inkscape)."""
        embed = self._embed.isChecked()
        downscale_max = None
        if embed and self._downscale.isChecked():
            downscale_max = self._max_edge.value()
        return (embed, downscale_max, self._filled.isChecked(),
                self._inkscape.isChecked())


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

    def __init__(self, brush_radius, brush_auto=True, parent=None):
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def brush_radius(self):
        return self._brush.value()

    def brush_auto(self):
        return self._brush_auto.isChecked()
