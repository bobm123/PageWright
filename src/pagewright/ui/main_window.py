"""Main window: the coordinator.

It owns the shared state (project, loaded image, the ObjectLayer list, the
undo stack) and wires together the canvas, the side-dock panels and the
controllers. The heavy lifting lives next door:

  * app_actions.py       -- actions, menus, toolbar, canvas context menu
  * objects_panel.py     -- the polygon list / margin / size readout (view)
  * tiling_panel.py      -- the tiling controls (view)
  * project_controller.py-- import photo, save/open project
  * export_controller.py -- SVG and tiled-print export
  * editing_controller.py-- Trace Poly and marquee group delete

What stays here: mode switching, object bookkeeping, the undo/redo dispatch and
edit sink, the bounding-box / tile-grid overlays and the status readouts.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDockWidget, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import calibration as calib
from ..core import undo
from ..model import Project
from . import app_actions
from .canvas import Canvas
from .dewarp_stage import (DewarpStageWidget, display_downscale,
                           ndarray_to_qpixmap)
from .dialogs import CalibrationDialog, PreferencesDialog
from .ocr_stage import OcrStageWidget
from .scale_stage import ScaleStageWidget
from .editing_controller import EditingController
from .export_controller import ExportController
from .objects import ObjectLayer
from .objects_panel import ObjectsPanel
from .overlay_controller import OverlayController
from .pages_panel import PagesPanel
from .project_controller import ProjectController
from .tiling_panel import TilingPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PageWright")
        self.resize(1440, 860)

        self.project = Project()
        self._loaded = None            # image_io.LoadedImage (BGR array source)
        self._objects = []             # list[ObjectLayer]
        self._active_index = -1
        self._polygon_counter = 0      # monotonic, for default polygon names

        # Undo stack for vertex/object edits (seed strokes use the canvas's own
        # stack; Ctrl+Z/Y dispatch between them by mode).
        self.undo_stack = undo.UndoStack()
        self.undo_stack.on_change(self._refresh_undo_actions)

        self.canvas = Canvas(self)
        self.dewarp_stage = DewarpStageWidget(self)
        self.dewarp_stage.applied.connect(self._on_dewarp_applied)
        self.dewarp_stage.cancelled.connect(self._leave_dewarp_stage)
        self.ocr_stage = OcrStageWidget(self)
        self.ocr_stage.cancelled.connect(lambda: self.switch_tool("trace"))
        self.scale_stage = ScaleStageWidget(self)
        self.scale_stage.cancelled.connect(lambda: self.switch_tool("trace"))
        self.scale_stage.mmPerPixelChanged.connect(self._on_scale_set)
        self.scale_stage.printRequested.connect(self.print_tiles)
        # central stack of first-class tools: trace / flatten / OCR
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self.canvas)
        self._stack.addWidget(self.dewarp_stage)
        self._stack.addWidget(self.ocr_stage)
        self._stack.addWidget(self.scale_stage)
        self.setCentralWidget(self._stack)
        self.canvas.calibrationPicked.connect(self._on_calibration_picked)
        self.canvas.cursorMoved.connect(self._on_cursor_moved)
        self.canvas.contextMenuRequested.connect(
            lambda pos: app_actions.show_canvas_menu(self, pos))
        self.canvas.seedsChanged.connect(self._refresh_undo_actions)
        self.canvas.deleteSelectionRequested.connect(
            self.delete_selected_vertices)
        self.canvas.roiSelected.connect(self._on_roi_selected)
        self.canvas.roiCleared.connect(self.clear_roi)
        self.canvas.roiEdited.connect(self._on_roi_edited)

        self.projects = ProjectController(self)
        self.exports = ExportController(self)
        self.editing = EditingController(self)
        self.overlays = OverlayController(self)

        app_actions.build_actions(self)
        app_actions.build_menus(self)
        app_actions.build_toolbar(self)
        self._build_dock()
        self._build_statusbar()
        self._set_tools_enabled(False)
        self._refresh_undo_actions()
        self._refresh_scale_readout()

    # ----- construction ----------------------------------------------------

    def _build_dock(self):
        dock = QDockWidget("Objects", self)
        dock.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable)
        panel = QWidget(dock)
        layout = QVBoxLayout(panel)

        self.pages_panel = PagesPanel(panel)
        self.pages_panel.pageActivated.connect(self.activate_page)
        self.pages_panel.addImagesRequested.connect(self.add_page_images)
        self.pages_panel.addFolderRequested.connect(self.add_page_folder)
        self.pages_panel.removeRequested.connect(self.remove_page)
        layout.addWidget(self.pages_panel)

        self.objects_panel = ObjectsPanel(self.project.margin_mm, panel)
        self.objects_panel.newRequested.connect(self.add_polygon)
        self.objects_panel.deleteRequested.connect(self.delete_active_object)
        self.objects_panel.renameRequested.connect(self.rename_active_object)
        self.objects_panel.rowChanged.connect(self._on_object_row_changed)
        self.objects_panel.marginChanged.connect(self._on_margin_changed)
        self.objects_panel.visibilityToggled.connect(self._on_visibility_toggled)
        layout.addWidget(self.objects_panel)

        self.tiling_panel = TilingPanel(panel)
        self.tiling_panel.changed.connect(self._update_tile_grid)
        self.tiling_panel.overlayToggled.connect(self.set_tiles_overlay)
        layout.addWidget(self.tiling_panel)
        layout.addStretch(1)

        panel.setLayout(layout)
        dock.setWidget(panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._dock = dock                  # hidden while the dewarp stage is up
        self._dock_was_visible = True

    def _build_statusbar(self):
        self._scale_label = QLabel("", self)
        self._cursor_label = QLabel("", self)
        self.statusBar().addWidget(self._scale_label, 1)
        self.statusBar().addPermanentWidget(self._cursor_label)

    def _set_tools_enabled(self, enabled):
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_fit,
                  self.act_calibrate, self.act_mode_pan, self.act_mode_fg,
                  self.act_mode_bg, self.act_mode_edit, self.act_run_seg,
                  self.act_clear_seeds, self.act_export,
                  self.act_export_tiles, self.act_show_bbox,
                  self.act_view_tiles,
                  self.act_save_project, self.act_new_object,
                  self.act_mode_roi, self.act_clear_roi,
                  self.act_dewarp, self.act_print_tiles,
                  self.act_print_preview, self.act_rotate_cw,
                  self.act_rotate_ccw):
            a.setEnabled(enabled)

    # ----- delegated actions -----------------------------------------------

    def open_path(self, path):
        """Open a photo or .json project by path (command-line arg)."""
        self.projects.open_path(path)

    def open_preferences(self):
        """File -> Preferences: app settings (currently brush size)."""
        dlg = PreferencesDialog(self.canvas.brush_radius(), self)
        if dlg.exec() == QDialog.Accepted:
            self.canvas.set_brush_radius(dlg.brush_radius())

    def open_photo(self):
        self.projects.open_photo()

    def rotate_working(self, clockwise):
        """R / L: rotate the working image 90 degrees. Traces and the
        Select Area rectangle rotate WITH the image (calibration mm/px
        is rotation-invariant); seed strokes are cleared."""
        if self._loaded is None:
            return
        if self._stack.currentWidget() is self.dewarp_stage:
            return                  # the stage has its own R/L handling
        import cv2
        self._sync_model()
        old_h, old_w = self._loaded.data.shape[:2]
        code = (cv2.ROTATE_90_CLOCKWISE if clockwise
                else cv2.ROTATE_90_COUNTERCLOCKWISE)
        self._loaded.data = cv2.rotate(self._loaded.data, code)
        if clockwise:
            def tp(x, y):
                return (old_h - y, x)
        else:
            def tp(x, y):
                return (y, old_w - x)
        for obj in self.project.objects:
            for c in obj.contours:
                c.points = [tp(x, y) for (x, y) in c.points]
        roi = self.canvas.roi_rect()
        new_roi = None
        if roi is not None:
            from PySide6.QtCore import QRectF
            pts = [tp(roi.left(), roi.top()), tp(roi.right(), roi.bottom())]
            xs = sorted(p[0] for p in pts)
            ys = sorted(p[1] for p in pts)
            new_roi = QRectF(xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0])
        self.project.set_source_image(self._loaded)
        disp, _, _ = display_downscale(self._loaded.data)
        self.canvas.set_photo(ndarray_to_qpixmap(disp),
                              (self._loaded.pixel_width,
                               self._loaded.pixel_height))
        self._load_layers_from_project()
        if new_roi is not None:
            self.canvas.set_roi(new_roi)
        self.undo_stack.clear()
        self._refresh_object_list()
        self._update_bbox()
        self._update_tile_grid()
        self._refresh_scale_readout()
        self.statusBar().showMessage(
            "Rotated 90 deg %s - traces and area follow the image; "
            "seed strokes cleared." % ("CW" if clockwise else "CCW"), 5000)

    def _current_tool_name(self):
        cur = self._stack.currentWidget()
        if cur is self.dewarp_stage:
            return "flatten"
        if cur is self.ocr_stage:
            return "ocr"
        if cur is self.scale_stage:
            return "scale"
        return "trace"

    def switch_tool(self, name):
        """Hub switching between the first-class tools: 'trace',
        'flatten', 'ocr'. All operate on the shared working image; switch
        at will."""
        if name != "trace" and self._loaded is None:
            self.statusBar().showMessage(
                "Load or paste an image first.", 4000)
            self._sync_tool_checks(self._current_tool_name())
            return
        if name == "trace":
            self._leave_dewarp_stage()
        elif name == "flatten":
            self.dewarp_page()
        elif name == "ocr":
            self.enter_ocr()
        elif name == "scale":
            self.enter_scale()
        self._sync_tool_checks(name)

    def _sync_tool_checks(self, name):
        for act, key in ((self.act_tool_trace, "trace"),
                         (self.act_tool_flatten, "flatten"),
                         (self.act_tool_ocr, "ocr"),
                         (self.act_tool_scale, "scale")):
            act.blockSignals(True)
            act.setChecked(key == name)
            act.blockSignals(False)

    def enter_ocr(self):
        """OCR tool: recognize text on the working image (whole image
        or the Select Area rectangle)."""
        if self._loaded is None:
            return
        if self._stack.currentWidget() is self.ocr_stage:
            return
        region = self.exports._tile_region()   # Select Area, if set
        self.ocr_stage.set_source_image(self._loaded.data, region)
        self._stack.setCurrentWidget(self.ocr_stage)
        self._set_tools_enabled(False)
        self._dock_was_visible = self._dock.isVisible()
        self._dock.hide()

    def enter_scale(self):
        """Scale tool: set the document's real-world size (sometimes the
        only thing a document needs)."""
        if self._loaded is None:
            return
        if self._stack.currentWidget() is self.scale_stage:
            return
        c = self.project.calibration
        self.scale_stage.set_source_image(
            self._loaded.data,
            c.mm_per_pixel if c.is_calibrated else None)
        self._stack.setCurrentWidget(self.scale_stage)
        self._set_tools_enabled(False)
        # printing/preview stay available - they are the point here
        self.act_print_tiles.setEnabled(True)
        self.act_print_preview.setEnabled(True)
        self._dock_was_visible = self._dock.isVisible()
        self._dock.hide()

    def _on_scale_set(self, mpp):
        """The Scale tool set mm/px: apply to the project so tiling,
        exports and readouts all follow."""
        self.project.calibration.mm_per_pixel = float(mpp)
        self._refresh_scale_readout()
        self._update_bbox()
        self._update_tile_grid()

    # ----- multi-page job (M1) ---------------------------------------------
    def _refresh_pages_panel(self):
        self.pages_panel.set_pages(
            [pg.get("source_path") or "" for pg in self.project.pages],
            self.project.current_page)

    def _store_current_page(self):
        """Serialize the on-canvas state into the current page entry."""
        from ..core import project_io as pio
        if not self.project.pages:
            return
        self._sync_model()
        i = self.project.current_page
        if 0 <= i < len(self.project.pages):
            pg = self.project.pages[i]
            pg["source_path"] = self._loaded.path if self._loaded else \
                pg.get("source_path")
            pg["objects"] = pio.objects_to_list(self.project.objects)

    def activate_page(self, index):
        """Switch the working image to page `index`, preserving the
        job-wide calibration/tiling and each page's traces."""
        from ..core import project_io as pio
        if not (0 <= index < len(self.project.pages)):
            return
        if index == self.project.current_page and self._loaded is not None:
            return
        self._store_current_page()
        pg = self.project.pages[index]
        path = pg.get("source_path")
        loaded, pixmap = self.projects._read_image(path or "", "Open Page")
        if loaded is None:
            return
        self.switch_tool("trace")
        self.project.current_page = index
        self.project.set_source_image(loaded)
        self._loaded = loaded
        self.undo_stack.clear()
        self.canvas.set_photo(pixmap,
                              (loaded.pixel_width, loaded.pixel_height))
        self.project.objects = pio.objects_from_list(pg.get("objects"))
        self._load_layers_from_project()
        self._polygon_counter = self._max_polygon_number()
        self._set_tools_enabled(True)
        self.act_mode_pan.setChecked(True)
        self._mode_pan()
        self._refresh_object_list()
        self._refresh_scale_readout()
        self._update_bbox()
        self._update_tile_grid()
        self._refresh_pages_panel()
        self.statusBar().showMessage(
            "Page %d of %d." % (index + 1, len(self.project.pages)), 4000)

    def _append_pages(self, paths):
        added = [p for p in paths if p]
        if not added:
            return
        first_job_page = not self.project.pages and self._loaded is None
        self.project.pages.extend(
            {"source_path": p, "objects": []} for p in added)
        self._refresh_pages_panel()
        self.statusBar().showMessage(
            "Added %d page(s); double-click a page to open it."
            % len(added), 5000)
        if first_job_page:
            self.activate_page(0)

    def add_page_images(self):
        from PySide6.QtWidgets import QFileDialog
        from .project_controller import IMAGE_FILTER
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Images", "", IMAGE_FILTER)
        self._append_pages(paths)

    def add_page_folder(self):
        import os
        from PySide6.QtWidgets import QFileDialog
        from ..core.project_io import natural_key
        d = QFileDialog.getExistingDirectory(self, "Add Folder of Images")
        if not d:
            return
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
        names = sorted((n for n in os.listdir(d)
                        if n.lower().endswith(exts)), key=natural_key)
        self._append_pages([os.path.join(d, n) for n in names])

    def remove_page(self, index):
        if not (0 <= index < len(self.project.pages)) \
                or len(self.project.pages) <= 1:
            self.statusBar().showMessage(
                "A job keeps at least one page.", 4000)
            return
        cur = self.project.current_page
        del self.project.pages[index]
        if index < cur or cur >= len(self.project.pages):
            self.project.current_page = max(0, cur - 1)
        if index == cur:
            self.project.current_page = min(index,
                                            len(self.project.pages) - 1)
            self._refresh_pages_panel()
            self.activate_page(self.project.current_page)
        else:
            self._refresh_pages_panel()

    def import_pdf(self, path=None):
        """File -> Import PDF: rasterize chosen pages (M2). Rendered
        pages have a KNOWN physical size, so the job calibration is set
        from the render DPI when not already calibrated."""
        import os
        import tempfile
        from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
        from ..core import pdf_import
        from .pdf_import_dialog import PdfImportDialog
        err = pdf_import.availability_error()
        if err:
            QMessageBox.warning(self, "Import PDF", err)
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import PDF", "", "PDF (*.pdf)")
            if not path:
                return
        try:
            sizes = pdf_import.page_sizes_mm(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import PDF",
                                 "Could not read the PDF: %s" % (exc,))
            return
        dlg = PdfImportDialog(sizes, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        indices, dpi = dlg.values()
        if not indices:
            return
        import cv2
        stem = os.path.splitext(os.path.basename(path))[0]
        tmp_dir = tempfile.mkdtemp(prefix="pagewright_pdf_")
        out = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for i in indices:
                bgr = pdf_import.render_page(path, i, dpi)
                fp = os.path.join(tmp_dir, "%s-p%03d.png" % (stem, i + 1))
                if cv2.imwrite(fp, bgr):
                    out.append(fp)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Import PDF",
                                 "Rendering failed: %s" % (exc,))
            return
        QApplication.restoreOverrideCursor()
        self._append_pages(out)
        if not self.project.calibration.is_calibrated:
            self.project.calibration.mm_per_pixel = 25.4 / float(dpi)
            self._refresh_scale_readout()
            self._update_tile_grid()
        self.statusBar().showMessage(
            "Imported %d PDF page(s) at %d DPI - true size known, so the "
            "scale is calibrated. Temporary files; use Save/Export to "
            "keep results." % (len(out), dpi), 8000)

    def paste_image(self):
        """Edit -> Paste Image (Ctrl+V): start from a screenshot or any
        image on the clipboard."""
        from PySide6.QtWidgets import QApplication
        qimg = QApplication.clipboard().image()
        if qimg.isNull():
            self.statusBar().showMessage(
                "The clipboard has no image to paste.", 4000)
            return
        import os
        import tempfile
        import numpy as np
        import cv2
        qimg = qimg.convertToFormat(qimg.Format.Format_RGB888)
        h, w = qimg.height(), qimg.width()
        buf = np.frombuffer(qimg.constBits(), np.uint8)
        arr = buf.reshape(h, qimg.bytesPerLine())[:, :w * 3]
        bgr = arr.reshape(h, w, 3)[:, :, ::-1].copy()
        tmp_dir = tempfile.mkdtemp(prefix="pagewright_paste_")
        path = os.path.join(tmp_dir, "clipboard.png")
        if not cv2.imwrite(path, bgr):
            self.statusBar().showMessage("Could not save the pasted "
                                         "image.", 4000)
            return
        self.projects.load_photo(path)
        self.statusBar().showMessage(
            "Pasted %d x %d image from the clipboard (temporary file - "
            "use Save/Export to keep results)." % (w, h), 6000)

    def dewarp_page(self):
        """Flatten tool: switch the central view to the dewarp stage
        (left: photo + outline, right: live flattened preview)."""
        if self._loaded is None:
            return
        if self._stack.currentWidget() is self.dewarp_stage:
            return
        dpi = self._loaded.dpi or 300
        self.dewarp_stage.set_source_image(self._loaded.data, dpi=dpi)
        self._stack.setCurrentWidget(self.dewarp_stage)
        # trace-view tools act on the hidden canvas; disable while staged,
        # and hide the Objects/Tiling dock (it belongs to the trace view)
        self._set_tools_enabled(False)
        self._dock_was_visible = self._dock.isVisible()
        self._dock.hide()
        self._sync_tool_checks("flatten")
        self.statusBar().showMessage(
            "Flatten Page: place the outline on the left; the right pane "
            "previews the result. Use Flattened Image to adopt it.", 8000)

    def _leave_dewarp_stage(self):
        if self._stack.currentWidget() in (self.dewarp_stage,
                                           self.ocr_stage,
                                           self.scale_stage):
            if self._dock_was_visible:
                self._dock.show()
        self._stack.setCurrentWidget(self.canvas)
        self._set_tools_enabled(self._loaded is not None)
        if hasattr(self, "act_tool_trace"):
            self._sync_tool_checks("trace")

    def _on_dewarp_applied(self):
        """Adopt the flattened image as the new working image.

        It is written to a temp PNG and re-imported through the normal
        photo path, so all downstream state (project, calibration,
        objects) resets cleanly around the new pixels. NOTE: the temp file
        is the new source reference until the user saves; a future
        revision should offer to save it somewhere permanent (and/or keep
        the original alongside)."""
        result = self.dewarp_stage.result_image()
        if result is None:
            self._leave_dewarp_stage()
            return
        import os
        import tempfile
        import cv2
        stem = "flattened"
        if self._loaded is not None and self._loaded.path:
            stem = os.path.splitext(os.path.basename(self._loaded.path))[0]
        tmp_dir = tempfile.mkdtemp(prefix="pagewright_dewarp_")
        out_path = os.path.join(tmp_dir, "%s_flat.png" % stem)
        if not cv2.imwrite(out_path, result):
            QMessageBox.critical(self, "Flatten Page",
                                 "Could not write the flattened image.")
            return
        self._leave_dewarp_stage()
        # start_dewarp=False: this photo IS the dewarp result
        self.projects.load_photo(out_path, start_dewarp=False)
        # The dewarp stage rendered at a known DPI with a known mm size,
        # so the adopted image's real-world scale is already determined -
        # carry it into the project calibration (fixes 'not calibrated'
        # and wildly wrong tile counts after flattening).
        dpi = self.dewarp_stage.output_dpi()
        if dpi and self._loaded is not None:
            self.project.calibration.mm_per_pixel = 25.4 / float(dpi)
            self._refresh_scale_readout()
        self.statusBar().showMessage(
            "Flattened image is now the working image; scale calibrated "
            "from the dewarp output (%d DPI). Temporary file - use "
            "Save/Export to keep it." % dpi, 8000)

    def save_project_file(self):
        self.projects.save_project()

    def open_project_file(self):
        self.projects.open_project()

    def export_svg(self):
        self.exports.export_svg()

    def export_tiles(self):
        self.exports.export_tiles()

    def print_tiles(self):
        self.exports.print_tiles()

    def print_preview_tiles(self):
        self.exports.print_preview_tiles()

    def run_segmentation(self):
        self.editing.run_segmentation()

    def delete_selected_vertices(self):
        self.editing.delete_selected_vertices()

    def clear_seeds(self):
        self.canvas.clear_seeds()

    # ----- layers from the model -------------------------------------------

    def _build_layer_from_model(self, model_obj):
        """Create an on-canvas ObjectLayer from a model.TracedObject."""
        layer = ObjectLayer(self.canvas.scene_obj(), model_obj.name,
                            edit_sink=self)
        layer.style = model_obj.style
        for c in model_obj.contours:
            layer.add_contour(c.points, role=c.role, closed=c.closed)
        return layer

    def _load_layers_from_project(self):
        """Rebuild on-canvas ObjectLayers from self.project.objects."""
        self._objects = [self._build_layer_from_model(o)
                         for o in self.project.objects]
        self._active_index = 0 if self._objects else -1

    def _max_polygon_number(self):
        """Largest N among existing 'Polygon N' names (0 if none)."""
        best = 0
        for layer in self._objects:
            name = layer.name
            if name.startswith("Polygon "):
                tail = name[len("Polygon "):].strip()
                if tail.isdigit():
                    best = max(best, int(tail))
        return best

    def _sync_model(self):
        self.project.objects = [layer.to_model()
                                for layer in self._objects
                                if not layer.is_empty()]

    # ----- calibration -----------------------------------------------------

    def start_calibration(self):
        if not self.canvas.has_photo():
            return
        self.statusBar().showMessage(
            "Calibration: click two points on a feature of known length.", 0)
        self.canvas.start_calibration()

    def _on_calibration_picked(self, p0, p1):
        self.statusBar().clearMessage()
        self.act_mode_pan.setChecked(True)
        pixel_distance = calib.pixel_distance(
            (p0.x(), p0.y()), (p1.x(), p1.y()))
        dlg = CalibrationDialog(pixel_distance, self)
        if dlg.exec() != QDialog.Accepted:
            return
        value, unit = dlg.values()
        try:
            mpp = calib.mm_per_pixel(
                (p0.x(), p0.y()), (p1.x(), p1.y()), value, unit)
        except ValueError as exc:
            QMessageBox.warning(self, "Calibrate Scale", str(exc))
            return
        self.project.calibration.mm_per_pixel = mpp
        self.set_unit(unit)
        self._refresh_scale_readout()
        self._update_bbox()

    # ----- modes -----------------------------------------------------------

    def _mode_pan(self):
        self.canvas.enter_pan_mode()
        self._refresh_editability()
        self._update_bbox()
        self._refresh_undo_actions()

    def _mode_seed_fg(self):
        self.canvas.start_seed_mode("fg")
        self._refresh_editability()
        self._refresh_undo_actions()
        self.statusBar().showMessage(
            "Paint inside the object (foreground seeds).", 4000)

    def _mode_seed_bg(self):
        self.canvas.start_seed_mode("bg")
        self._refresh_editability()
        self._refresh_undo_actions()
        self.statusBar().showMessage(
            "Paint outside the object (background seeds).", 4000)

    def _mode_edit(self):
        self.canvas.enter_edit_mode()
        self._refresh_editability()
        self._refresh_undo_actions()
        self.statusBar().showMessage(
            "Edit: drag handles to move, right-click to delete, "
            "double-click an edge to add a vertex, drag a box to "
            "select then Delete to remove several.", 6000)

    def _mode_roi(self):
        self.canvas.start_roi_mode()
        self._refresh_editability()
        self._refresh_undo_actions()
        self.statusBar().showMessage(
            "Drag a box to zoom to it; tracing AND tiling are limited to "
            "that area (click once to clear it).", 6000)

    def _on_roi_selected(self, rect):
        """A trace area was dragged out: zoom so it fills the window
        (centered) and go back to panning."""
        self.canvas.zoom_to_rect(rect)
        self.act_mode_pan.setChecked(True)
        self._mode_pan()
        self._update_tile_grid()      # tiling follows the selected area
        self.statusBar().showMessage(
            "Area set (%d x %d px): Trace Poly and the print tiles now "
            "use only this region." % (round(rect.width()),
                                       round(rect.height())), 6000)

    def _on_roi_edited(self, rect):
        """The area was resized by dragging an edge: keep the view still,
        just refresh the tiling that depends on it."""
        self._update_tile_grid()
        self.statusBar().showMessage(
            "Area resized to %d x %d px." % (round(rect.width()),
                                             round(rect.height())), 4000)

    def clear_roi(self):
        self.canvas.clear_roi()
        self._update_tile_grid()      # back to traces / whole image
        self.statusBar().showMessage(
            "Area cleared; tracing and tiling use the whole image.", 4000)

    def _is_edit_mode(self):
        return self.act_mode_edit.isChecked()

    def _in_seed_mode(self):
        return self.act_mode_fg.isChecked() or self.act_mode_bg.isChecked()

    # ----- undo / redo (context dispatch) ----------------------------------

    def _do_undo(self):
        if self._in_seed_mode():
            self.canvas.undo_seed()
        else:
            self.undo_stack.undo()
            self._update_bbox()
        self._refresh_undo_actions()

    def _do_redo(self):
        if self._in_seed_mode():
            self.canvas.redo_seed()
        else:
            self.undo_stack.redo()
            self._update_bbox()
        self._refresh_undo_actions()

    def _refresh_undo_actions(self):
        if self._in_seed_mode():
            cu = self.canvas.has_photo() and self.canvas.can_undo_seed()
            cr = self.canvas.has_photo() and self.canvas.can_redo_seed()
            ulabel = rlabel = "seed stroke"
        else:
            cu = self.undo_stack.can_undo()
            cr = self.undo_stack.can_redo()
            ulabel = self.undo_stack.undo_label()
            rlabel = self.undo_stack.redo_label()
        self.act_undo.setEnabled(cu)
        self.act_redo.setEnabled(cr)
        self.act_undo.setToolTip(("Undo " + ulabel).strip() if cu else "Undo")
        self.act_redo.setToolTip(("Redo " + rlabel).strip() if cr else "Redo")

    # ----- edit sink (called by EditableContour) ---------------------------

    def record_move(self, contour, index, old_xy, new_xy):
        self.undo_stack.push(undo.FnCommand(
            "move vertex",
            undo=lambda: contour.move_vertex(index, old_xy[0], old_xy[1]),
            redo=lambda: contour.move_vertex(index, new_xy[0], new_xy[1])))

    def record_insert(self, contour, index, xy):
        self.undo_stack.push(undo.FnCommand(
            "add vertex",
            undo=lambda: contour.delete_vertex(index),
            redo=lambda: contour.insert_vertex_at(index, xy[0], xy[1])))

    def record_delete(self, contour, index, xy):
        self.undo_stack.push(undo.FnCommand(
            "delete vertex",
            undo=lambda: contour.insert_vertex_at(index, xy[0], xy[1]),
            redo=lambda: contour.delete_vertex(index)))

    # ----- object management -----------------------------------------------

    def add_polygon(self):
        """New Polygon: create a polygon and start marking inside it."""
        if not self.canvas.has_photo():
            return
        self.new_object()
        self.act_mode_fg.setChecked(True)
        self._mode_seed_fg()

    def new_object(self):
        if not self.canvas.has_photo():
            return
        # Monotonic counter so deleting a polygon never reuses a name.
        self._polygon_counter += 1
        name = "Polygon %d" % (self._polygon_counter,)
        layer = ObjectLayer(self.canvas.scene_obj(), name, edit_sink=self)
        self._objects.append(layer)
        self._active_index = len(self._objects) - 1
        self._refresh_object_list()
        self._refresh_editability()

    def delete_active_object(self):
        if self._active_index < 0:
            return
        index = self._active_index
        snapshot = self._objects[index].to_model()
        self._remove_layer_at(index)
        # Structural change: keep the history simple and free of stale targets.
        self.undo_stack.clear()
        self.undo_stack.push(undo.FnCommand(
            "delete object",
            undo=lambda: self._insert_layer_from_model(index, snapshot),
            redo=lambda: self._remove_layer_at(index)))

    def _remove_layer_at(self, index):
        if not (0 <= index < len(self._objects)):
            return
        self._objects[index].remove()
        del self._objects[index]
        if self._active_index >= len(self._objects):
            self._active_index = len(self._objects) - 1
        self._refresh_object_list()
        self._refresh_editability()
        self._update_bbox()

    def _insert_layer_from_model(self, index, model_obj):
        layer = self._build_layer_from_model(model_obj)
        index = max(0, min(index, len(self._objects)))
        self._objects.insert(index, layer)
        self._active_index = index
        self._refresh_object_list()
        self._refresh_editability()
        self._update_bbox()

    def rename_active_object(self):
        if self._active_index < 0:
            return
        current = self._objects[self._active_index].name
        name, ok = QInputDialog.getText(
            self, "Rename Object", "Name:", text=current)
        if ok and name.strip():
            self._objects[self._active_index].name = name.strip()
            self._refresh_object_list()

    def _on_object_row_changed(self, row):
        if 0 <= row < len(self._objects):
            self._active_index = row
            self._refresh_editability()

    def _refresh_object_list(self):
        self.objects_panel.set_layers(self._objects, self._active_index)

    def _on_visibility_toggled(self, layer, visible):
        layer.set_visible(visible)
        self._refresh_editability()

    def _refresh_editability(self):
        edit = self._is_edit_mode()
        for i, layer in enumerate(self._objects):
            # Visibility is owned by the per-object toggle; here we only decide
            # which object exposes its draggable vertex handles.
            layer.set_editable(edit and i == self._active_index)

    # ----- overlays / scale (delegated to OverlayController) ---------------

    def effective_mm_per_pixel(self, scale):
        return self.overlays.effective_mm_per_pixel(scale)

    def _update_bbox(self):
        self.overlays.update_bbox()

    def _update_tile_grid(self, *args):
        self.overlays.update_tile_grid()

    def set_tiles_overlay(self, on):
        self.overlays.set_tiles_overlay(on)

    def _on_margin_changed(self, value):
        self.project.margin_mm = float(value)
        self._update_bbox()

    # ----- readouts --------------------------------------------------------

    def set_unit(self, unit):
        self.project.calibration.display_unit = unit
        for u, action in self.act_units.items():
            action.setChecked(u == unit)
        self._refresh_scale_readout()

    def _refresh_scale_readout(self):
        c = self.project.calibration
        if not c.is_calibrated:
            if self.project.source_image_path:
                self._scale_label.setText(
                    "%d × %d px  —  not calibrated"
                    % (self.project.pixel_width, self.project.pixel_height))
            else:
                self._scale_label.setText("No photo loaded")
            return
        unit = c.display_unit
        size = self.project.real_size_mm()
        w = calib.from_mm(size[0], unit)
        h = calib.from_mm(size[1], unit)
        mpp_unit = calib.from_mm(c.mm_per_pixel, unit)
        self._scale_label.setText(
            "%d × %d px   |   %.5g %s/px   |   real size %.4g × %.4g %s"
            % (self.project.pixel_width, self.project.pixel_height,
               mpp_unit, unit, w, h, unit))

    def _on_cursor_moved(self, scene_pt):
        if not self.canvas.has_photo():
            return
        x, y = scene_pt.x(), scene_pt.y()
        c = self.project.calibration
        if c.is_calibrated:
            unit = c.display_unit
            mx = calib.from_mm(x * c.mm_per_pixel, unit)
            my = calib.from_mm(y * c.mm_per_pixel, unit)
            self._cursor_label.setText(
                "x %.0f, y %.0f px  (%.3g, %.3g %s)" % (x, y, mx, my, unit))
        else:
            self._cursor_label.setText("x %.0f, y %.0f px" % (x, y))
