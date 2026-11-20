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

# How far inside the trace area's edge the implied background runs, as a
# fraction of the area's shorter side. Just inside rather than exactly on
# the edge, so a part drawn hard against the box still has paper between
# it and the seed.
_BORDER_INSET = 0.02


def _with_border_background(strokes, width, height):
    """Add background seeds round the trace area when none were marked.

    Drawing a box round one part already says where that part ends:
    everything outside the box is somebody else's. Making the user then
    paint "outside" is asking twice, so the border supplies it.

    Only when the user marked NO background at all. One deliberate
    outside stroke means they are steering the segmenter - often to push
    a boundary the border seeds would fight - and silently adding four
    more would override them.
    """
    if any(s.label == "bg" for s in strokes):
        return strokes
    if width < 8 or height < 8:
        return strokes          # no room for a border that is not the part

    inset = max(1, int(round(_BORDER_INSET * min(width, height))))
    x0, y0 = inset, inset
    x1, y1 = width - 1 - inset, height - 1 - inset
    if x1 <= x0 or y1 <= y0:
        return strokes

    # One stroke per edge: a closed loop would also work, but four edges
    # keep each seed line straight and easy to reason about.
    edges = [[(x, y0) for x in range(x0, x1 + 1)],
             [(x, y1) for x in range(x0, x1 + 1)],
             [(x0, y) for y in range(y0, y1 + 1)],
             [(x1, y) for y in range(y0, y1 + 1)]]
    radius = max(1.0, inset / 2.0)
    return list(strokes) + [seg.Stroke("bg", pts, radius, implied=True)
                            for pts in edges]


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
            has_fg = any(label == "fg" for (label, _p, _r) in raw_strokes)
            if has_fg:
                # The marks exist but the Select Area does not contain
                # them. Classic trap since areas persist per page (and
                # restore from the project file): the area was drawn for
                # a PREVIOUS object, and the user is now marking a new
                # object elsewhere. Offer one-click recovery instead of
                # a dead-end error.
                resp = QMessageBox.question(
                    w, "Trace Poly",
                    "Your foreground marks are outside the selected "
                    "trace area (the dashed rectangle - it may have "
                    "been restored from the project file).\n\n"
                    "Clear the area and trace using your marks?",
                    QMessageBox.Yes | QMessageBox.Cancel)
                if resp != QMessageBox.Yes:
                    return None
                w.clear_roi()
                strokes = [seg.Stroke(l, pts, r)
                           for (l, pts, r) in raw_strokes]
                return img, strokes, 0, 0
            QMessageBox.warning(
                w, "Trace Poly",
                "Mark some inside (foreground) in the selected trace "
                "area first." + chr(10) + chr(10) +
                "The area's border is taken as outside, so marking "
                "outside is optional.")
            return None
        strokes = [seg.Stroke(l, [(x - x0, y - y0) for (x, y) in pts], r)
                   for (l, pts, r) in raw_strokes]
        strokes = _with_border_background(strokes, x1 - x0, y1 - y0)
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

        # Simplification tolerance is a REAL-WORLD length, not a fraction
        # of the region: the old region-size rule bottomed out at 1.5 px
        # on small areas and returned hundreds of vertices (227 on a
        # model-aircraft former a hand trace draws with ~64). The speckle
        # floor is relative for the same reason an absolute one failed -
        # a drawing sheet holds hundreds of unrelated ink fragments, and
        # any inside the trace area came back as extra polygons.
        # Which enclosed regions are real holes is decided by the marks,
        # not by size: the user said where "outside" is, and a hole is
        # somewhere they said that. Printed labels inside the outline
        # read as background to GrabCut and would otherwise come back
        # as holes, but nobody marks outside on a letter.
        outside_seeds = seg.outside_seed_mask(image.shape[:2], strokes)

        region_h, region_w = image.shape[:2]
        epsilon_px = cont.tolerance_mm_to_px(
            w.trace_tolerance_mm(), w.project.calibration.mm_per_pixel)
        min_area_px = max(50.0, 0.0005 * region_w * region_h)
        results = cont.extract_contours(
            mask, epsilon_px=epsilon_px, min_area_px=min_area_px,
            min_area_frac=cont.MIN_AREA_FRACTION,
            keep_largest=w.trace_keep_largest(),
            hole_seed_mask=outside_seeds)
        if not results:
            QMessageBox.information(
                w, "Run Segmentation",
                "No object boundary found. Add more inside/outside seeds "
                "and run again.")
            return

        if x0 or y0:   # region coords -> full-image coords
            results = [([(x + x0, y + y0) for (x, y) in pts], role)
                       for (pts, role) in results]

        # Tracing NEVER overwrites a polygon that already has geometry:
        # a trace is cheap to repeat and a traced outline is expensive to
        # redraw, so the safe default is a new object. The one exception
        # is an empty active object - that is what New Polygon just made,
        # and filling it is the whole point of having made it.
        if (w._active_index < 0
                or not w._objects[w._active_index].is_empty()):
            w.new_object()
        w._objects[w._active_index].set_contours(results)
        w.canvas.clear_seeds()
        # Tracing replaces the active object's contours, invalidating any edit
        # commands that referenced the old ones; start the history fresh.
        w.undo_stack.clear()

        w.act_mode_edit.setChecked(True)
        w._mode_edit()
        n_holes = sum(1 for _, role in results if role == cont.ROLE_HOLE)
        n_pts = sum(len(pts) for pts, _ in results)
        scope = " in the trace area" if w.canvas.roi_rect() is not None else ""
        # Report the vertex count: it is what the Trace detail control
        # moves, so the user can see the effect without counting handles.
        w.statusBar().showMessage(
            "Found %d contour(s) (%d hole(s), %d points at %.2f mm detail) "
            "for '%s'%s. Refine, or adjust Trace detail and run again."
            % (len(results), n_holes, n_pts, w.trace_tolerance_mm(),
               w._objects[w._active_index].name, scope), 8000)

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

    def align_selected_vertices(self, axis):
        """Line the selected vertices up on one axis, as one undo step.

        `axis` is "vertical" (shared x) or "horizontal" (shared y).
        The arithmetic is core.geometry.align_points; this layer only
        collects the selection, applies the result and records undo.

        Points from DIFFERENT contours align together on purpose: two
        halves of a notch often belong to separate traced loops, and
        the whole point is to make them line up with each other.
        """
        w = self._w
        if not w._is_edit_mode():
            return
        scene = w.canvas.scene_obj()
        picked = [it for it in scene.selectedItems()
                  if isinstance(it, VertexHandle)]
        if len(picked) < 2:
            return
        from ..core import geometry as geo

        items = []          # (contour, index, before_xy)
        for it in picked:
            contour = it.owner_contour()
            idx = contour.index_of(it)
            items.append((contour, idx, contour.get_point(idx)))
        before = [xy for _c, _i, xy in items]
        after = geo.align_points(before, axis)
        if all(a == b for a, b in zip(before, after)):
            return                      # already aligned; no undo entry

        def _apply(points):
            for (contour, idx, _old), xy in zip(items, points):
                contour.move_vertex(idx, xy[0], xy[1])
            w._refresh_editability()
            w._update_bbox()

        _apply(after)
        w.undo_stack.push(undo.FnCommand(
            "align %d vertices %s" % (len(items), axis),
            lambda: _apply(before), lambda: _apply(after)))
        w.statusBar().showMessage(
            "Aligned %d points %sly." % (len(items), axis), 4000)

    def selected_run(self):
        """The selected vertices as a consecutive run, or None.

        Returns (contour, [indices in order]) when the selection is 3
        or 4 vertices of ONE contour that follow each other round the
        ring, else None. Squaring a scattered selection would move
        points that share no segment, which is meaningless - so the
        menu asks this first and greys the item out when it fails.

        Wrap-around counts: the run from the last vertex to the first
        is as consecutive as any other on a closed contour.
        """
        w = self._w
        if not w._is_edit_mode():
            return None
        picked = [it for it in w.canvas.scene_obj().selectedItems()
                  if isinstance(it, VertexHandle)]
        if not 3 <= len(picked) <= 4:
            return None
        contours = {it.owner_contour() for it in picked}
        if len(contours) != 1:
            return None
        contour = contours.pop()
        n = contour.vertex_count()
        idx = sorted(contour.index_of(it) for it in picked)
        if len(set(idx)) != len(idx):
            return None
        k = len(idx)
        for start in range(n):
            run = [(start + d) % n for d in range(k)]
            if sorted(run) == idx:
                return contour, run
        return None

    def _run_grid(self, contour):
        """The part's own dominant angle, or 0 without a scale."""
        from ..core import notches as nt
        try:
            mpp = self._w.project.calibration.mm_per_pixel
        except AttributeError:
            return 0.0, None
        if not mpp:
            return 0.0, None
        return nt.dominant_angle_deg(contour.points(), mpp), mpp

    def square_selected_vertices(self):
        """Square a run's turns, keeping every segment length.

        The lengths are the trustworthy part of a traced notch - it
        was cut to a stick's width - while the angles are scan noise.
        So this holds the lengths and lets the run's far end move,
        rather than holding both ends and stretching the segments.
        """
        w = self._w
        found = self.selected_run()
        if found is None:
            return
        contour, run = found
        from ..core import geometry as geo

        before = [contour.get_point(i) for i in run]
        grid, _mpp = self._run_grid(contour)
        after = geo.square_keeping_lengths(before, grid_deg=grid)
        if all(a == b for a, b in zip(before, after)):
            return

        def _apply(points):
            for i, xy in zip(run, points):
                contour.move_vertex(i, xy[0], xy[1])
            w._refresh_editability()
            w._update_bbox()

        _apply(after)
        w.undo_stack.push(undo.FnCommand(
            "square %d vertices" % len(run),
            lambda: _apply(before), lambda: _apply(after)))
        w.statusBar().showMessage(
            "Squared %d points." % len(run), 4000)

    def quad_selected_vertices(self):
        """Make 4 selected points a closed rectangle on stock sides.

        Needs a calibrated project: 1/32 in means nothing in pixels.
        The menu greys the item when there is no scale, so this
        returning early is the belt to that brace.
        """
        w = self._w
        found = self.selected_run()
        if found is None:
            return
        contour, run = found
        if len(run) != 4:
            return
        from ..core import geometry as geo
        from ..core import notches as nt

        grid, mpp = self._run_grid(contour)
        if not mpp:
            w.statusBar().showMessage(
                "Quad snap needs a calibrated scale.", 4000)
            return
        before = [contour.get_point(i) for i in run]
        after = geo.quad_snap(before, mpp, nt.DEFAULT_STEP_MM,
                              grid_deg=grid)
        if all(a == b for a, b in zip(before, after)):
            return

        def _apply(points):
            for i, xy in zip(run, points):
                contour.move_vertex(i, xy[0], xy[1])
            w._refresh_editability()
            w._update_bbox()

        _apply(after)
        w.undo_stack.push(undo.FnCommand(
            "quad snap 4 vertices",
            lambda: _apply(before), lambda: _apply(after)))
        w.statusBar().showMessage(
            "Squared and snapped to a %s rectangle."
            % "1/32 in", 4000)
