"""Tiling controls for the side dock.

A pure view: it owns the tiled-printing widgets and reports changes via
signals. It knows nothing about the project, the canvas or exporting --
MainWindow connects `changed` to the overlay refresh and reads `params()`
when exporting.

Two representations are exposed on purpose:
  * params()  -- live values for the overlay/export (scale as a factor)
  * to_dict() / from_dict() -- the persisted project-file schema
    (scale_percent, embed_photo, ...), see model.default_tiling().
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QSpinBox,
)

from ..core import tiling


class TilingPanel(QGroupBox):
    """The Tiling group box: page/orientation/margin/overlap/scale + photo opts."""

    changed = Signal()             # any setting that affects the tile grid
    overlayToggled = Signal(bool)  # the "Show tile grid" checkbox

    def __init__(self, parent=None):
        super().__init__("Tiling", parent)
        form = QFormLayout(self)

        self._show_tiles_check = QCheckBox("Show tile grid", self)
        self._show_tiles_check.toggled.connect(self.overlayToggled)
        form.addRow(self._show_tiles_check)

        self._page_combo = QComboBox(self)
        for name in tiling.PAGE_SIZES_MM:
            self._page_combo.addItem(name)
        form.addRow("Page:", self._page_combo)

        self._orient_combo = QComboBox(self)
        self._orient_combo.addItems(["Portrait", "Landscape"])
        form.addRow("Orientation:", self._orient_combo)

        self._tmargin_spin = QDoubleSpinBox(self)
        self._tmargin_spin.setRange(0.0, 50.0)
        self._tmargin_spin.setDecimals(1)
        self._tmargin_spin.setValue(6.0)
        self._tmargin_spin.setSuffix(" mm")
        form.addRow("Printer margin:", self._tmargin_spin)

        self._overlap_spin = QDoubleSpinBox(self)
        self._overlap_spin.setRange(0.0, 100.0)
        self._overlap_spin.setDecimals(1)
        self._overlap_spin.setValue(10.0)
        form.addRow("Overlap:", self._overlap_spin)
        self._overlap_unit = QComboBox(self)
        self._overlap_unit.addItems(["mm", "% of page"])
        form.addRow("Overlap unit:", self._overlap_unit)

        self._layout_combo = QComboBox(self)
        self._layout_combo.addItems(["Auto (from scale)", "Fixed grid"])
        self._layout_combo.setToolTip(
            "Auto: the scale below decides how many pages are needed.\n"
            "Fixed grid: choose the page count; the drawing is scaled to "
            "fill that many pages.")
        form.addRow("Layout:", self._layout_combo)

        self._cols_spin = QSpinBox(self)
        self._cols_spin.setRange(1, 25)
        self._cols_spin.setValue(2)
        form.addRow("Pages across:", self._cols_spin)
        self._rows_spin = QSpinBox(self)
        self._rows_spin.setRange(1, 25)
        self._rows_spin.setValue(2)
        form.addRow("Pages down:", self._rows_spin)

        self._scale_spin = QSpinBox(self)
        self._scale_spin.setRange(5, 400)
        self._scale_spin.setSingleStep(5)
        self._scale_spin.setValue(100)
        self._scale_spin.setSuffix(" %")
        form.addRow("Scale:", self._scale_spin)
        self._layout_combo.currentIndexChanged.connect(self._sync_layout_mode)
        self._sync_layout_mode()

        self._embed_check = QCheckBox("Include image", self)
        self._embed_check.setToolTip(
            "Embed the image on the tiles (always cropped to the traced/"
            "selected bounding box).")
        form.addRow(self._embed_check)
        self._filled_check = QCheckBox("Fill objects", self)
        form.addRow(self._filled_check)

        # Any geometry-affecting change refreshes the overlay live.
        self._page_combo.currentIndexChanged.connect(self._emit_changed)
        self._orient_combo.currentIndexChanged.connect(self._emit_changed)
        self._tmargin_spin.valueChanged.connect(self._emit_changed)
        self._overlap_spin.valueChanged.connect(self._emit_changed)
        self._overlap_unit.currentIndexChanged.connect(self._emit_changed)
        self._layout_combo.currentIndexChanged.connect(self._emit_changed)
        self._cols_spin.valueChanged.connect(self._emit_changed)
        self._rows_spin.valueChanged.connect(self._emit_changed)
        self._scale_spin.valueChanged.connect(self._emit_changed)

    def _emit_changed(self, *args):
        self.changed.emit()

    def _sync_layout_mode(self, *args):
        fixed = self._layout_combo.currentIndex() == 1
        self._cols_spin.setEnabled(fixed)
        self._rows_spin.setEnabled(fixed)
        self._scale_spin.setEnabled(not fixed)

    def _overlap_mm_xy(self):
        """Overlap resolved to mm per axis (percent = % of the printable
        page dimension on that axis)."""
        v = self._overlap_spin.value()
        if self._overlap_unit.currentIndex() == 0:      # mm
            return (v, v)
        pw, ph = tiling.PAGE_SIZES_MM[self._page_combo.currentText()]
        if self._orient_combo.currentText() == "Landscape":
            pw, ph = ph, pw
        m = self._tmargin_spin.value()
        return (max(0.0, (pw - 2.0 * m) * v / 100.0),
                max(0.0, (ph - 2.0 * m) * v / 100.0))

    # ----- state -----------------------------------------------------------

    def params(self):
        """Live settings. `scale` is a factor (1.0 == 100%), or None in
        Fixed grid mode (compute it with tiling.fit_scale and `grid`).
        `overlap_mm` is always an (x, y) mm pair."""
        fixed = self._layout_combo.currentIndex() == 1
        return {
            "page": self._page_combo.currentText(),
            "landscape": self._orient_combo.currentText() == "Landscape",
            "margin_mm": self._tmargin_spin.value(),
            "overlap_mm": self._overlap_mm_xy(),
            "scale": None if fixed else self._scale_spin.value() / 100.0,
            "grid": ((self._cols_spin.value(), self._rows_spin.value())
                     if fixed else None),
            "embed": self._embed_check.isChecked(),
            "filled": self._filled_check.isChecked(),
        }

    def to_dict(self):
        """Settings in the persisted project-file schema."""
        return {
            "page": self._page_combo.currentText(),
            "landscape": self._orient_combo.currentText() == "Landscape",
            "margin_mm": self._tmargin_spin.value(),
            "overlap_mm": self._overlap_spin.value(),
            "overlap_unit": ("mm" if self._overlap_unit.currentIndex() == 0
                             else "percent"),
            "fixed_grid": self._layout_combo.currentIndex() == 1,
            "grid_cols": self._cols_spin.value(),
            "grid_rows": self._rows_spin.value(),
            "scale_percent": self._scale_spin.value(),
            "embed_photo": self._embed_check.isChecked(),
            "crop_photo": True,   # tiles always crop to the bounding box
            "filled": self._filled_check.isChecked(),
        }

    def from_dict(self, t):
        """Apply persisted settings, emitting `changed` once at the end."""
        t = t or {}
        widgets = (self._page_combo, self._orient_combo, self._tmargin_spin,
                   self._overlap_spin, self._overlap_unit,
                   self._layout_combo, self._cols_spin, self._rows_spin,
                   self._scale_spin, self._embed_check,
                   self._filled_check)
        for w in widgets:
            w.blockSignals(True)
        self._page_combo.setCurrentText(t.get("page", "Letter"))
        self._orient_combo.setCurrentText(
            "Landscape" if t.get("landscape") else "Portrait")
        self._tmargin_spin.setValue(float(t.get("margin_mm", 6.0)))
        self._overlap_spin.setValue(float(t.get("overlap_mm", 10.0)))
        self._overlap_unit.setCurrentIndex(
            1 if t.get("overlap_unit") == "percent" else 0)
        self._layout_combo.setCurrentIndex(
            1 if t.get("fixed_grid") else 0)
        self._cols_spin.setValue(int(t.get("grid_cols", 2)))
        self._rows_spin.setValue(int(t.get("grid_rows", 2)))
        self._scale_spin.setValue(int(t.get("scale_percent", 100)))
        self._embed_check.setChecked(bool(t.get("embed_photo", False)))
        self._filled_check.setChecked(bool(t.get("filled", False)))
        for w in widgets:
            w.blockSignals(False)
        self._sync_layout_mode()
        self.changed.emit()

    def overlay_checked(self):
        return self._show_tiles_check.isChecked()

    def set_overlay_checked(self, on):
        """Set the checkbox without re-emitting overlayToggled."""
        self._show_tiles_check.blockSignals(True)
        self._show_tiles_check.setChecked(on)
        self._show_tiles_check.blockSignals(False)
