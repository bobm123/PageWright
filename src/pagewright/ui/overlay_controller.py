"""Canvas overlays and print scale: the bounding box, the tile-grid preview,
the traced-size readout, and the mm-per-pixel used for tiling/export.

A deliberate collaborator of MainWindow (it reads the window's project, layers
and panels, and draws on its canvas). Split out of main_window to keep that
file focused on coordination.
"""

from ..core import calibration as calib
from ..core import geometry as geo
from ..core import tiling

DEFAULT_DPI = 96.0    # assumed when the project has no calibration


class OverlayController:
    def __init__(self, window):
        self._w = window

    # ----- scale -----------------------------------------------------------

    def base_mm_per_pixel(self):
        """mm/px from calibration, or an uncalibrated default from image DPI."""
        c = self._w.project.calibration
        if c.is_calibrated:
            return c.mm_per_pixel
        dpi = self._w.project.dpi or DEFAULT_DPI
        return 25.4 / dpi

    def effective_mm_per_pixel(self, scale):
        """Base mm/px times the print scale factor (1.0 = real size)."""
        return self.base_mm_per_pixel() * scale

    # ----- geometry --------------------------------------------------------

    def all_points(self):
        pts = []
        for layer in self._w._objects:
            pts.extend(layer.iter_points())
        return pts

    # ----- bounding box ----------------------------------------------------

    def update_bbox(self):
        w = self._w
        if not w.canvas.has_photo() or not w.act_show_bbox.isChecked():
            w.canvas.clear_bbox()
            self.refresh_size_label()
            self.update_tile_grid()
            return
        pts = self.all_points()
        if not pts:
            w.canvas.clear_bbox()
            self.refresh_size_label()
            self.update_tile_grid()
            return
        box = geo.bbox_of_points(pts)
        c = w.project.calibration
        margin_px = (w.project.margin_mm / c.mm_per_pixel
                     if c.is_calibrated else 0.0)
        box = box.expanded(margin_px)
        w.canvas.set_bbox(box.min_x, box.min_y, box.width, box.height)
        self.refresh_size_label(box)
        self.update_tile_grid()

    def refresh_size_label(self, box=None):
        w = self._w
        if box is None or not w.project.calibration.is_calibrated:
            if not w.project.calibration.is_calibrated and w._objects:
                w.objects_panel.set_size_text(
                    "Traced size: calibrate to show mm.")
            else:
                w.objects_panel.set_size_text("")
            return
        unit = w.project.calibration.display_unit
        mpp = w.project.calibration.mm_per_pixel
        width = calib.from_mm(box.width * mpp, unit)
        height = calib.from_mm(box.height * mpp, unit)
        w.objects_panel.set_size_text(
            "Traced size (incl. margin): %.4g × %.4g %s" % (width, height, unit))

    # ----- tile grid -------------------------------------------------------

    def set_tiles_overlay(self, on):
        """Toggle the tile-grid overlay, keeping menu + panel in sync."""
        w = self._w
        w.act_view_tiles.blockSignals(True)
        w.act_view_tiles.setChecked(on)
        w.act_view_tiles.blockSignals(False)
        w.tiling_panel.set_overlay_checked(on)
        self.update_tile_grid()

    def update_tile_grid(self, *args):
        """Refresh the View -> Tiles overlay (page-tile grid on the canvas)."""
        w = self._w
        if not w.canvas.has_photo() or not w.act_view_tiles.isChecked():
            w.canvas.clear_tile_grid()
            return
        pts = self.all_points()
        if not pts:
            # nothing traced: tile the WHOLE IMAGE (image-only printing)
            if w._loaded is None:
                w.canvas.clear_tile_grid()
                return
            pts = [(0.0, 0.0),
                   (float(w._loaded.pixel_width),
                    float(w._loaded.pixel_height))]
        p = w.tiling_panel.params()
        box = geo.bbox_of_points(pts)
        try:
            scale = p["scale"]
            if scale is None:
                # Fixed grid: scale so the content fills the page grid
                mpp1 = self.effective_mm_per_pixel(1.0)
                margin_px1 = w.project.margin_mm / mpp1
                cw1 = (box.width + 2.0 * margin_px1) * mpp1
                ch1 = (box.height + 2.0 * margin_px1) * mpp1
                cols, rows = p["grid"]
                scale = tiling.fit_scale(cw1, ch1, p["page"],
                                         p["landscape"], p["margin_mm"],
                                         p["overlap_mm"], cols, rows)
            mpp = self.effective_mm_per_pixel(scale)
            margin_px = w.project.margin_mm / mpp
            ox = box.min_x - margin_px
            oy = box.min_y - margin_px
            content_w_mm = (box.width + 2.0 * margin_px) * mpp
            content_h_mm = (box.height + 2.0 * margin_px) * mpp
            plan = tiling.plan_tiles(content_w_mm, content_h_mm, p["page"],
                                     p["landscape"], p["margin_mm"],
                                     p["overlap_mm"])
        except ValueError as exc:
            w.canvas.clear_tile_grid()
            w.statusBar().showMessage("Tile preview: %s" % (exc,), 4000)
            return
        xs, ys = tiling.grid_lines_mm(plan, content_w_mm, content_h_mm)
        left, right = ox, ox + content_w_mm / mpp
        top, bottom = oy, oy + content_h_mm / mpp
        segments = []
        for x_mm in xs:
            x_px = ox + x_mm / mpp
            segments.append((x_px, top, x_px, bottom))
        for y_mm in ys:
            y_px = oy + y_mm / mpp
            segments.append((left, y_px, right, y_px))
        w.canvas.set_tile_grid(segments)
        w.statusBar().showMessage(
            "Tile preview: %d x %d pages (%s%s, %d%%)."
            % (plan["ncols"], plan["nrows"], p["page"],
               ", landscape" if p["landscape"] else "",
               round(scale * 100)), 4000)
