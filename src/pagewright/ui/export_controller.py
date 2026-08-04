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

    def _tile_region(self):
        """The Select Area rectangle as (x0, y0, x1, y1) image px, clamped
        to the image, or None. When set it defines the tiled region."""
        w = self._w
        rect = w.canvas.roi_rect()
        if rect is None or w._loaded is None:
            return None
        pw, ph = w._loaded.pixel_width, w._loaded.pixel_height
        x0 = max(0.0, min(float(rect.left()), float(pw)))
        y0 = max(0.0, min(float(rect.top()), float(ph)))
        x1 = max(0.0, min(float(rect.right()), float(pw)))
        y1 = max(0.0, min(float(rect.bottom()), float(ph)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return (x0, y0, x1, y1)

    def _resolved_tile_params(self, title):
        """Common preflight for tile export/printing. Returns (params,
        mm_per_pixel) or None. Image-only tiling (no traces) is allowed
        when the photo is included."""
        w = self._w
        if w._loaded is None:
            return None
        w._sync_model()
        p = w.tiling_panel.params()
        region = self._tile_region()
        if not w.project.objects and not p["embed"]:
            QMessageBox.information(
                w, title,
                "Nothing to print: trace an object, or turn on "
                "'Include photo' to tile the image itself.")
            return None
        scale = p["scale"]
        if scale is None:
            # Fixed grid: derive the scale that fills cols x rows pages
            from ..core import geometry as geo
            if region is not None:
                pts = [(region[0], region[1]), (region[2], region[3])]
            else:
                pts = []
                for obj in w.project.objects:
                    for c in obj.contours:
                        pts.extend(c.points)
                if not pts:
                    pts = [(0.0, 0.0), (float(w._loaded.pixel_width),
                                        float(w._loaded.pixel_height))]
            box = geo.bbox_of_points(pts)
            mpp1 = w.effective_mm_per_pixel(1.0)
            margin_px1 = w.project.margin_mm / mpp1
            cw1 = (box.width + 2.0 * margin_px1) * mpp1
            ch1 = (box.height + 2.0 * margin_px1) * mpp1
            cols, rows = p["grid"]
            scale = tiling.fit_scale(cw1, ch1, p["page"], p["landscape"],
                                     p["margin_mm"], p["overlap_mm"],
                                     cols, rows)
        return p, w.effective_mm_per_pixel(scale)

    def _build_tiles(self, title):
        """Build the tile SVGs from the current settings, or None."""
        w = self._w
        pre = self._resolved_tile_params(title)
        if pre is None:
            return None
        p, mpp = pre
        base_name = os.path.splitext(
            os.path.basename(w._loaded.path or "tile"))[0]
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                return tiling.build_tiles(
                    w.project,
                    image_bgr=w._loaded.data if p["embed"] else None,
                    page=p["page"], landscape=p["landscape"],
                    margin_mm=p["margin_mm"], overlap_mm=p["overlap_mm"],
                    embed_photo=p["embed"], filled=p["filled"],
                    base_name=base_name, mm_per_pixel=mpp,
                    crop_photo=p["crop"],
                    region_px=self._tile_region())
            finally:
                QApplication.restoreOverrideCursor()
        except (svg_export.ExportError, ValueError) as exc:
            QMessageBox.warning(w, title, str(exc))
            return None
        except Exception as exc:
            QMessageBox.critical(w, title, "Tiling failed: %s" % (exc,))
            return None

    def export_tiles(self):
        w = self._w
        tiles = None
        out_dir = None
        if w._loaded is None:
            return
        out_dir = QFileDialog.getExistingDirectory(
            w, "Choose a folder for the tile SVGs", os.getcwd())
        if not out_dir:
            return
        tiles = self._build_tiles("Export Print Tiles")
        if tiles is None:
            return
        try:
            for name, svg in tiles:
                with open(os.path.join(out_dir, name), "w",
                          encoding="utf-8") as fh:
                    fh.write(svg)
        except OSError as exc:
            QMessageBox.critical(w, "Export Print Tiles", str(exc))
            return
        QMessageBox.information(
            w, "Export Print Tiles",
            "Wrote %d tile(s) to:\n%s" % (len(tiles), out_dir))
        w.statusBar().showMessage(
            "Wrote %d tile(s) to %s" % (len(tiles), out_dir), 6000)

    def print_tiles(self):
        """Send the tiles straight to a printer (true 1:1: the printer
        page size is set from the tiling page choice and the tile SVG is
        painted onto the full page)."""
        w = self._w
        tiles = self._build_tiles("Print Tiles")
        if not tiles:
            return
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QPainter
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        from PySide6.QtSvg import QSvgRenderer

        p = w.tiling_panel.params()
        pw_mm, ph_mm = tiling.PAGE_SIZES_MM[p["page"]]
        if p["landscape"]:
            pw_mm, ph_mm = ph_mm, pw_mm

        printer = QPrinter(QPrinter.HighResolution)
        layout = QPageLayout(QPageSize(QSizeF(pw_mm, ph_mm),
                                       QPageSize.Millimeter),
                             QPageLayout.Portrait, QMarginsF(0, 0, 0, 0))
        printer.setPageLayout(layout)
        printer.setFullPage(True)
        dlg = QPrintDialog(printer, w)
        dlg.setWindowTitle("Print Tiles")
        if dlg.exec() != QDialog.Accepted:
            return
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.critical(w, "Print Tiles",
                                 "Could not start the print job.")
            return
        try:
            for i, (_name, svg) in enumerate(tiles):
                if i:
                    printer.newPage()
                renderer = QSvgRenderer(bytearray(svg, "utf-8"))
                renderer.render(painter, printer.pageRect(
                    QPrinter.DevicePixel))
        finally:
            painter.end()
        w.statusBar().showMessage(
            "Sent %d tile page(s) to the printer. Make sure printer "
            "scaling / 'fit to page' is OFF for true 1:1." % len(tiles),
            8000)
