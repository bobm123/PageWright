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
        self._overlap_spin.setSuffix(" mm")
        form.addRow("Overlap:", self._overlap_spin)

        self._scale_spin = QSpinBox(self)
        self._scale_spin.setRange(5, 400)
        self._scale_spin.setSingleStep(5)
        self._scale_spin.setValue(100)
        self._scale_spin.setSuffix(" %")
        form.addRow("Scale:", self._scale_spin)

        self._embed_check = QCheckBox("Include photo", self)
        form.addRow(self._embed_check)
        self._crop_check = QCheckBox("Crop photo to bounding box", self)
        self._crop_check.setToolTip(
            "Embed only the photo inside the traced bounding box, so it does "
            "not extend past the trace on the printed tiles.")
        form.addRow(self._crop_check)
        self._filled_check = QCheckBox("Fill objects", self)
        form.addRow(self._filled_check)

        # Any geometry-affecting change refreshes the overlay live.
        self._page_combo.currentIndexChanged.connect(self._emit_changed)
        self._orient_combo.currentIndexChanged.connect(self._emit_changed)
        self._tmargin_spin.valueChanged.connect(self._emit_changed)
        self._overlap_spin.valueChanged.connect(self._emit_changed)
        self._scale_spin.valueChanged.connect(self._emit_changed)

    def _emit_changed(self, *args):
        self.changed.emit()

    # ----- state -----------------------------------------------------------

    def params(self):
        """Live settings; `scale` is a factor (1.0 == 100%)."""
        return {
            "page": self._page_combo.currentText(),
            "landscape": self._orient_combo.currentText() == "Landscape",
            "margin_mm": self._tmargin_spin.value(),
            "overlap_mm": self._overlap_spin.value(),
            "scale": self._scale_spin.value() / 100.0,
            "embed": self._embed_check.isChecked(),
            "crop": self._crop_check.isChecked(),
            "filled": self._filled_check.isChecked(),
        }

    def to_dict(self):
        """Settings in the persisted project-file schema."""
        return {
            "page": self._page_combo.currentText(),
            "landscape": self._orient_combo.currentText() == "Landscape",
            "margin_mm": self._tmargin_spin.value(),
            "overlap_mm": self._overlap_spin.value(),
            "scale_percent": self._scale_spin.value(),
            "embed_photo": self._embed_check.isChecked(),
            "crop_photo": self._crop_check.isChecked(),
            "filled": self._filled_check.isChecked(),
        }

    def from_dict(self, t):
        """Apply persisted settings, emitting `changed` once at the end."""
        t = t or {}
        widgets = (self._page_combo, self._orient_combo, self._tmargin_spin,
                   self._overlap_spin, self._scale_spin, self._embed_check,
                   self._crop_check, self._filled_check)
        for w in widgets:
            w.blockSignals(True)
        self._page_combo.setCurrentText(t.get("page", "Letter"))
        self._orient_combo.setCurrentText(
            "Landscape" if t.get("landscape") else "Portrait")
        self._tmargin_spin.setValue(float(t.get("margin_mm", 6.0)))
        self._overlap_spin.setValue(float(t.get("overlap_mm", 10.0)))
        self._scale_spin.setValue(int(t.get("scale_percent", 100)))
        self._embed_check.setChecked(bool(t.get("embed_photo", False)))
        self._crop_check.setChecked(bool(t.get("crop_photo", False)))
        self._filled_check.setChecked(bool(t.get("filled", False)))
        for w in widgets:
            w.blockSignals(False)
        self.changed.emit()

    def overlay_checked(self):
        return self._show_tiles_check.isChecked()

    def set_overlay_checked(self, on):
        """Set the checkbox without re-emitting overlayToggled."""
        self._show_tiles_check.blockSignals(True)
        self._show_tiles_check.setChecked(on)
        self._show_tiles_check.blockSignals(False)
