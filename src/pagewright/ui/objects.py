"""Object layer (Phase 3): groups the editable contours that make up one traced
object -- an outer boundary plus zero or more holes, or several disjoint loops.

Keeps the on-canvas EditableContour items and can project them back into the
plain model classes (model.Contour / model.TracedObject) used by SVG export.

An optional `edit_sink` is forwarded to every EditableContour so interactive
vertex edits can be recorded on the application's undo stack (Phase 6).
"""

from ..model import Contour, Style, TracedObject
from .editable import EditableContour


class ObjectLayer:
    """A named object: a collection of EditableContour items on one scene."""

    def __init__(self, scene, name, edit_sink=None):
        self._scene = scene
        self.name = name
        self.contours = []          # list[EditableContour]
        self.style = Style()
        self.visible = True
        self._edit_sink = edit_sink

    # ----- contour population ----------------------------------------------

    def set_contours(self, results):
        """Replace all contours from a list of (points, role)."""
        self.clear()
        for points, role in results:
            self.add_contour(points, role)
        # New contours inherit the layer's current visibility.
        self.set_visible(self.visible)

    def add_contour(self, points, role="outer", closed=True):
        self.contours.append(
            EditableContour(self._scene, points, role=role, closed=closed,
                            edit_sink=self._edit_sink))

    def clear(self):
        for c in self.contours:
            c.remove()
        self.contours = []

    def remove_contour(self, contour):
        """Remove one contour from this layer; return its former index."""
        index = self.contours.index(contour)
        contour.remove()
        del self.contours[index]
        return index

    def insert_contour(self, index, points, role='outer', closed=True):
        """Recreate a contour at `index` (used to undo a contour removal)."""
        c = EditableContour(self._scene, points, role=role, closed=closed,
                            edit_sink=self._edit_sink)
        c.set_visible(self.visible)
        self.contours.insert(index, c)
        return c

    def remove(self):
        self.clear()

    def is_empty(self):
        return len(self.contours) == 0

    # ----- display ---------------------------------------------------------

    def set_editable(self, editable):
        for c in self.contours:
            c.set_editable(editable)

    def set_visible(self, visible):
        self.visible = visible
        for c in self.contours:
            c.set_visible(visible)

    # ----- geometry / model projection -------------------------------------

    def iter_points(self):
        """Yield every vertex (x, y) across all contours."""
        for c in self.contours:
            for pt in c.points():
                yield pt

    def to_model(self):
        """Build a model.TracedObject snapshot of the current vertices."""
        obj = TracedObject(name=self.name, style=self.style)
        for c in self.contours:
            obj.contours.append(
                Contour(points=c.points(), closed=True, role=c.role))
        return obj
