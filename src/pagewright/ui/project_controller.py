"""Photo import and project save/load.

Each user action is split in two: an `open_*` entry point that asks for a file,
and a `load_*` worker that does the work for a known path. The command line
(main.py) reuses the workers via open_path().

A deliberate collaborator of MainWindow: it drives the window's state
(project, loaded image, layers) rather than owning it, so the window stays a
thin coordinator.
"""

import os

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import image_io
from ..core import project_io
from ..model import Project
from .dewarp_stage import display_downscale, ndarray_to_qpixmap

IMAGE_FILTER = (
    "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)")
PROJECT_FILTER = "PageWright project (*.tiproj.json *.json);;All files (*)"


class ProjectController:
    def __init__(self, window):
        self._w = window

    # ----- entry points ----------------------------------------------------

    def open_path(self, path):
        """Open a photo or a .json project, chosen by extension.

        Used for the optional command-line file argument.
        """
        if not os.path.isfile(path):
            QMessageBox.warning(self._w, "Open",
                                "File not found:\n%s" % (path,))
            return
        if path.lower().endswith(".json"):
            self.load_project(path)
        else:
            self.load_photo(path)

    def open_photo(self):
        path, _ = QFileDialog.getOpenFileName(
            self._w, "Load Image", "", IMAGE_FILTER)
        if path:
            self.load_photo(path)

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self._w, "Open Project", "", PROJECT_FILTER)
        if path:
            self.load_project(path)

    # ----- workers ---------------------------------------------------------

    def load_photo(self, path, start_dewarp=True):
        """Load a photo as the working image.

        start_dewarp: freshly imported photos go straight into the
        dewarp stage (flatten first, then trace). Pass False when the
        photo IS a dewarp result being adopted."""
        w = self._w
        loaded, pixmap = self._read_image(path, "Load Image")
        if loaded is None:
            return
        w._leave_dewarp_stage()   # no-op when already on the trace view

        w.project = Project()
        w.project.set_source_image(loaded)
        w._loaded = loaded
        w._objects = []          # scene.clear() in set_photo drops the items
        w._active_index = -1
        w._polygon_counter = 0
        w.undo_stack.clear()

        w.canvas.set_photo(pixmap,
                           (loaded.pixel_width, loaded.pixel_height))
        w.objects_panel.set_margin(w.project.margin_mm)
        w.act_mode_pan.setChecked(True)
        w._mode_pan()
        w._set_tools_enabled(True)
        w._refresh_object_list()
        w.set_unit(w.project.calibration.display_unit)
        w.setWindowTitle("PageWright — %s" % os.path.basename(path))
        w._refresh_scale_readout()
        if start_dewarp:
            w.dewarp_page()   # dewarp-first workflow: flatten, then trace

    def load_project(self, path):
        w = self._w
        try:
            project = project_io.load_project(path)
        except project_io.ProjectIOError as exc:
            QMessageBox.critical(w, "Open Project", str(exc))
            return
        except (IOError, OSError) as exc:
            QMessageBox.critical(w, "Open Project", str(exc))
            return

        img_path = self._locate_image(project.source_image_path)
        if not img_path:
            return
        loaded, pixmap = self._read_image(img_path, "Open Project")
        if loaded is None:
            return
        w._leave_dewarp_stage()   # no-op when already on the trace view

        saved_w, saved_h = project.pixel_width, project.pixel_height

        w.project = project
        w.project.set_source_image(loaded)   # match actual image dims/path
        w._loaded = loaded
        w.undo_stack.clear()

        w.canvas.set_photo(pixmap,
                           (loaded.pixel_width, loaded.pixel_height))
        w._load_layers_from_project()
        w._polygon_counter = w._max_polygon_number()

        w._set_tools_enabled(True)
        w.act_mode_pan.setChecked(True)
        w._mode_pan()
        w.objects_panel.set_margin(w.project.margin_mm)
        w.tiling_panel.from_dict(w.project.tiling)
        w.set_unit(w.project.calibration.display_unit)
        w._refresh_object_list()
        w._refresh_scale_readout()
        w._update_bbox()
        w.setWindowTitle("PageWright — %s" % os.path.basename(path))

        if saved_w and (saved_w != loaded.pixel_width
                        or saved_h != loaded.pixel_height):
            QMessageBox.warning(
                w, "Open Project",
                "The source image size differs from when the project was "
                "saved; traced points may not line up.")
        w.statusBar().showMessage("Opened project %s" % path, 6000)

    def save_project(self):
        w = self._w
        if w._loaded is None:
            return
        w._sync_model()
        w.project.tiling = w.tiling_panel.to_dict()
        base = os.path.splitext(
            os.path.basename(w._loaded.path or "project"))[0]
        default_path = os.path.join(os.getcwd(), base + ".tiproj.json")
        path, _ = QFileDialog.getSaveFileName(
            w, "Save Project", default_path, PROJECT_FILTER)
        if not path:
            return
        try:
            project_io.save_project(w.project, path)
        except Exception as exc:
            QMessageBox.critical(w, "Save Project",
                                 "Could not save: %s" % (exc,))
            return
        w.statusBar().showMessage("Saved project %s" % path, 6000)

    # ----- helpers ---------------------------------------------------------

    def _read_image(self, path, title):
        """Load `path` and return (LoadedImage, QPixmap).

        The display pixmap is built from the ALREADY-DECODED OpenCV array
        rather than decoding the file a second time via QPixmap(path).
        For large images (esp. PNG, whose decode is slow - ~2s for an
        18 MP file) that halves load time, and it keeps the displayed
        pixels identical to the ones the dewarp/trace math uses (no EXIF
        orientation mismatch between the two decoders)."""
        w = self._w
        try:
            loaded = image_io.load_image(path)
        except IOError as exc:
            QMessageBox.critical(w, title, str(exc))
            return None, None
        # display proxy: never hand Qt the full-res (possibly 18 MP) image
        disp, _, _ = display_downscale(loaded.data)
        pixmap = ndarray_to_qpixmap(disp)
        if pixmap.isNull():
            QMessageBox.critical(w, title, "Qt could not display this image.")
            return None, None
        return loaded, pixmap

    def _locate_image(self, img_path):
        """Return a usable image path, prompting if the saved one is gone."""
        if img_path and os.path.exists(img_path):
            return img_path
        QMessageBox.warning(
            self._w, "Open Project",
            "Source image not found:\n%s\n\nPlease locate it." % (img_path,))
        img_path, _ = QFileDialog.getOpenFileName(
            self._w, "Locate Source Image", "", IMAGE_FILTER)
        return img_path
