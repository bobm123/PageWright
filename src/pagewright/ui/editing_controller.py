"""Tracing and vertex editing: run GrabCut into the active polygon, and the
marquee group-delete.

A deliberate collaborator of MainWindow (it drives the window's layers, canvas
and undo stack). Split out of main_window to keep that file focused.
"""

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ..core import contours as cont
from ..core import segmentation as seg
from ..core import undo
from .editable import VertexHandle

MIN_VERTICES = 3   # fewer than this is not a polygon -> drop the contour


class EditingController:
    def __init__(self, window):
        self._w = window

    # ----- tracing ---------------------------------------------------------

    def _trace_region(self, raw_strokes):
        """Resolve the image region to segment from the canvas trace area.

        Returns (image, strokes, x0, y0) with stroke points moved into the
        region's coordinates, or None if the area is unusable.
        """
        w = self._w
        img = w._loaded.data
        ih, iw = img.shape[:2]
        roi = w.canvas.roi_rect()
        if roi is None:
            strokes = [seg.Stroke(l, pts, r) for (l, pts, r) in raw_strokes]
            return img, strokes, 0, 0

        x0 = max(0, int(math.floor(roi.left())))
        y0 = max(0, int(math.floor(roi.top())))
        x1 = min(iw, int(math.ceil(roi.right())))
        y1 = min(ih, int(math.ceil(roi.bottom())))
        if x1 - x0 < 2 or y1 - y0 < 2:
            QMessageBox.warning(w, "Trace Poly",
                                "The selected trace area is too small.")
            return None
        inside = any(label == "fg" and any(x0 <= x < x1 and y0 <= y < y1
                                           for (x, y) in pts)
                     for (label, pts, _r) in raw_strokes)
        if not inside:
            QMessageBox.warning(
                w, "Trace Poly",
                "Mark some foreground inside the selected trace area first.")
            return None
        strokes = [seg.Stroke(l, [(x - x0, y - y0) for (x, y) in pts], r)
                   for (l, pts, r) in raw_strokes]
        return img[y0:y1, x0:x1], strokes, x0, y0

    def run_segmentation(self):
        w = self._w
        if w._loaded is None:
            return
        region = self._trace_region(w.canvas.seed_strokes())
        if region is None:
            return
        image, strokes, x0, y0 = region

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                mask = seg.grabcut_from_strokes(image, strokes)
            finally:
                QApplication.restoreOverrideCursor()
        except ValueError as exc:
            QMessageBox.warning(w, "Run Segmentation", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(w, "Run Segmentation",
                                 "Segmentation failed: %s" % (exc,))
            return

        # Scale simplification + speckle thresholds to the region actually
        # segmented -- using the whole image here would filter out everything
        # inside a small trace area.
        region_h, region_w = image.shape[:2]
        long_edge = max(region_w, region_h)
        # TODO(ui): expose a "Trace complexity / number of points" control.
        # epsilon_px is the approxPolyDP tolerance that decides how many
        # vertices the traced polygon keeps: larger -> simpler (fewer
        # points), smaller -> more detailed (more points). It is currently
        # auto-derived from the region size (factor 0.0012). Plan: add a
        # toolbar/right-click slider (e.g. Coarse..Fine) or a point-count
        # target that scales this factor, so the user can trade smoothness
        # against fidelity per object without re-seeding.
        epsilon_px = max(1.5, long_edge * 0.0012)
        min_area_px = max(50.0, 0.0005 * region_w * region_h)
        results = cont.extract_contours(
            mask, epsilon_px=epsilon_px, min_area_px=min_area_px)
        if not results:
            QMessageBox.information(
                w, "Run Segmentation",
                "No object boundary found. Add more inside/outside seeds "
                "and run again.")
            return

        if x0 or y0:   # region coords -> full-image coords
            results = [([(x + x0, y + y0) for (x, y) in pts], role)
                       for (pts, role) in results]

        if w._active_index < 0:
            w.new_object()
        w._objects[w._active_index].set_contours(results)
        w.canvas.clear_seeds()
        # Tracing replaces the active object's contours, invalidating any edit
        # commands that referenced the old ones; start the history fresh.
        w.undo_stack.clear()

        w.act_mode_edit.setChecked(True)
        w._mode_edit()
        n_holes = sum(1 for _, role in results if role == cont.ROLE_HOLE)
        scope = " in the trace area" if w.canvas.roi_rect() is not None else ""
        w.statusBar().showMessage(
            "Found %d contour(s) (%d hole(s)) for '%s'%s. Refine, or re-seed "
            "and run again." % (len(results), n_holes,
                                w._objects[w._active_index].name, scope), 6000)

    # ----- marquee group delete --------------------------------------------

    def _layer_of_contour(self, contour):
        for layer in self._w._objects:
            if contour in layer.contours:
                return layer
        return None

    def delete_selected_vertices(self):
        """Delete rubber-band-selected vertices as one undo step.

        If a contour would be left with fewer than 3 vertices it is removed
        entirely (a sub-3 contour is not a polygon); undo restores it.
        """
        w = self._w
        if not w._is_edit_mode():
            return
        scene = w.canvas.scene_obj()
        groups = {}
        for item in scene.selectedItems():
            if isinstance(item, VertexHandle):
                contour = item.owner_contour()
                groups.setdefault(contour, []).append(contour.index_of(item))
        if not groups:
            return

        vertex_dels = []   # (contour, index, (x, y)) in deletion order
        removals = []      # mutable records describing removed contours

        for contour, indices in groups.items():
            indices = sorted(set(indices), reverse=True)
            remaining = contour.vertex_count() - len(indices)
            if remaining >= MIN_VERTICES:
                for idx in indices:
                    xy = contour.get_point(idx)
                    contour.delete_vertex(idx)
                    vertex_dels.append((contour, idx, xy))
            else:
                layer = self._layer_of_contour(contour)
                if layer is None:
                    continue
                rec = {"layer": layer,
                       "index": layer.contours.index(contour),
                       "points": contour.points(),
                       "role": contour.role,
                       "closed": contour.closed,
                       "contour": contour}
                layer.remove_contour(contour)
                rec["contour"] = None
                removals.append(rec)

        if not vertex_dels and not removals:
            return

        def _undo():
            for rec in reversed(removals):
                rec["contour"] = rec["layer"].insert_contour(
                    rec["index"], rec["points"], rec["role"], rec["closed"])
            for contour, idx, xy in reversed(vertex_dels):
                contour.insert_vertex_at(idx, xy[0], xy[1])
            w._refresh_editability()
            w._update_bbox()

        def _redo():
            for contour, idx, xy in vertex_dels:
                contour.delete_vertex(idx)
            for rec in removals:
                rec["index"] = rec["layer"].remove_contour(rec["contour"])
                rec["contour"] = None
            w._refresh_editability()
            w._update_bbox()

        n = len(vertex_dels) + sum(len(r["points"]) for r in removals)
        if removals:
            # Removing whole contours invalidates older commands that
            # referenced them, so keep the history simple and safe.
            w.undo_stack.clear()
        w.undo_stack.push(undo.FnCommand(
            "delete %d vertices" % n, _undo, _redo))
        scene.clearSelection()
        w._refresh_editability()
        w._update_bbox()
        msg = "Deleted %d vertices" % n
        if removals:
            msg += " (%d contour(s) removed)" % len(removals)
        w.statusBar().showMessage(msg + ".", 4000)
