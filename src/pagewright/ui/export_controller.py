"""Export actions: a single true-scale SVG, and tiled 1:1 print pages.

A deliberate collaborator of MainWindow (it reads the window's project, loaded
image and tiling panel). Split out of main_window to keep that file focused.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from ..core import svg_export
from ..core import tiling
from .dialogs import ExportSvgDialog


class ExportController:
    def __init__(self, window):
        self._w = window

    def export_svg(self):
        w = self._w
        if w._loaded is None:
            return
        if not w.project.calibration.is_calibrated:
            QMessageBox.warning(
                w, "Export SVG",
                "Calibrate the scale first so the SVG can be sized in mm.")
            return
        w._sync_model()
        if not w.project.objects:
            QMessageBox.information(
                w, "Export SVG", "Trace at least one object first.")
            return

        dlg = ExportSvgDialog(w)
        if dlg.exec() != QDialog.Accepted:
            return
        embed, downscale_max, filled, inkscape = dlg.values()

        base = os.path.splitext(os.path.basename(w._loaded.path or "trace"))[0]
        default_path = os.path.join(os.getcwd(), base + ".svg")
        path, _ = QFileDialog.getSaveFileName(
            w, "Export SVG", default_path, "SVG files (*.svg)")
        if not path:
            return

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                svg = svg_export.build_svg(
                    w.project,
                    image_bgr=w._loaded.data if embed else None,
                    embed_photo=embed,
                    downscale_max=downscale_max,
                    filled=filled,
                    inkscape=inkscape)
            finally:
                QApplication.restoreOverrideCursor()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg)
        except svg_export.ExportError as exc:
            QMessageBox.warning(w, "Export SVG", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(w, "Export SVG", "Export failed: %s" % (exc,))
            return

        w.statusBar().showMessage("Exported %s" % path, 6000)

    def export_tiles(self):
        w = self._w
        if w._loaded is None:
            return
        w._sync_model()
        if not w.project.objects:
            QMessageBox.information(
                w, "Export Print Tiles", "Trace at least one object first.")
            return

        # All settings come from the side-panel Tiling controls.
        p = w.tiling_panel.params()
        mpp = w.effective_mm_per_pixel(p["scale"])

        out_dir = QFileDialog.getExistingDirectory(
            w, "Choose a folder for the tile SVGs", os.getcwd())
        if not out_dir:
            return

        base_name = os.path.splitext(
            os.path.basename(w._loaded.path or "tile"))[0]
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                tiles = tiling.build_tiles(
                    w.project,
                    image_bgr=w._loaded.data if p["embed"] else None,
                    page=p["page"], landscape=p["landscape"],
                    margin_mm=p["margin_mm"], overlap_mm=p["overlap_mm"],
                    embed_photo=p["embed"], filled=p["filled"],
                    base_name=base_name, mm_per_pixel=mpp,
                    crop_photo=p["crop"])
                for name, svg in tiles:
                    with open(os.path.join(out_dir, name), "w",
                              encoding="utf-8") as fh:
                        fh.write(svg)
            finally:
                QApplication.restoreOverrideCursor()
        except (svg_export.ExportError, ValueError) as exc:
            QMessageBox.warning(w, "Export Print Tiles", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(w, "Export Print Tiles",
                                 "Tiling failed: %s" % (exc,))
            return

        QMessageBox.information(
            w, "Export Print Tiles",
            "Wrote %d tile(s) to:\n%s" % (len(tiles), out_dir))
        w.statusBar().showMessage(
            "Wrote %d tile(s) to %s" % (len(tiles), out_dir), 6000)
