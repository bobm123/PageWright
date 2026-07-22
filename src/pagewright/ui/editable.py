"""Editable polygon items for the canvas (Phase 2; undo hooks Phase 6).

An EditableContour draws one contour as a path with a draggable vertex handle on
every point. The user can:
  * drag a handle to move a vertex,
  * right-click a handle to delete that vertex (down to a 3-vertex minimum),
  * double-click an edge to insert a new vertex there.

Handles use ItemIgnoresTransformations so they stay a constant on-screen size at
any zoom. All coordinates are scene pixels, matching the photo and the model.

Visibility and editability are tracked independently: handles are only shown
when the contour is both visible and editable.

Editing is split into index-based *primitives* (move_vertex / insert_vertex_at /
delete_vertex) that just mutate geometry, and interactive handlers that perform
the primitive and then report it to an optional `edit_sink` so the application
can record an undo command. Undo/redo call the primitives directly, so they
never re-record.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsPathItem

_OUTER_COLOR = QColor(40, 170, 255)
_HOLE_COLOR = QColor(255, 170, 40)
_HANDLE_COLOR = QColor(255, 255, 255)
_HANDLE_R = 4.0          # on-screen radius (device px, due to IgnoresTransform)
_MIN_VERTICES = 3


def _dist_point_to_segment(p, a, b):
    """Distance from point p to segment ab, all (x, y) tuples/QPointF-likes."""
    px, py = p.x(), p.y()
    ax, ay = a.x(), a.y()
    bx, by = b.x(), b.y()
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 0.0:
        ex, ey = px - ax, py - ay
        return (ex * ex + ey * ey) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx, cy = ax + t * dx, ay + t * dy
    ex, ey = px - cx, py - cy
    return (ex * ex + ey * ey) ** 0.5


class VertexHandle(QGraphicsEllipseItem):
    """A draggable dot marking one polygon vertex."""

    def __init__(self, owner, scene_point):
        r = _HANDLE_R
        super().__init__(QRectF(-r, -r, 2 * r, 2 * r))
        self._owner = owner
        self._press_pos = None
        self.setPos(scene_point)
        self.setZValue(20)
        self.setBrush(_HANDLE_COLOR)
        pen = QPen(QColor(0, 0, 0))
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setFlag(QGraphicsEllipseItem.ItemIsMovable, True)
        self.setFlag(QGraphicsEllipseItem.ItemSendsScenePositionChanges, True)
        self.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsEllipseItem.ItemIsSelectable, True)
        self.setCursor(Qt.SizeAllCursor)

    def itemChange(self, change, value):
        if change == QGraphicsEllipseItem.ItemScenePositionHasChanged:
            self._owner._on_handle_moved()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = self.pos()   # remember drag start for undo
        super().mousePressEvent(event)

    def request_delete(self):
        """Delete this vertex (invoked by the canvas on right-click)."""
        self._owner._delete_vertex_interactive(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            start = self._press_pos
            self._press_pos = None
            end = self.pos()
            if start != end:
                self._owner._record_move(self, start, end)
        super().mouseReleaseEvent(event)

    def owner_contour(self):
        return self._owner


class _OutlineItem(QGraphicsPathItem):
    """The polygon outline; double-clicking an edge inserts a vertex."""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self.setZValue(10)

    def mouseDoubleClickEvent(self, event):
        # event.pos() is in item coords; the item sits at the scene origin.
        self._owner._insert_vertex_interactive(event.pos())
        event.accept()


class EditableContour:
    """Controller owning one outline item plus its vertex handles."""

    def __init__(self, scene, points, role="outer", closed=True,
                 edit_sink=None):
        self._scene = scene
        self._role = role
        self._closed = closed
        self._editable = True
        self._visible = True
        self._sink = edit_sink     # optional; receives interactive edits

        color = _HOLE_COLOR if role == "hole" else _OUTER_COLOR
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setWidth(2)
        if role == "hole":
            pen.setStyle(Qt.DashLine)

        self._outline = _OutlineItem(self)
        self._outline.setPen(pen)
        scene.addItem(self._outline)

        self._handles = []
        for pt in points:
            self._add_handle(QPointF(pt[0], pt[1]))
        self._rebuild_path()

    # ----- public ----------------------------------------------------------

    @property
    def role(self):
        return self._role

    @property
    def closed(self):
        return self._closed

    def points(self):
        """Current vertices as a list of (x, y) in pixel coords."""
        return [(h.pos().x(), h.pos().y()) for h in self._handles]

    def vertex_count(self):
        return len(self._handles)

    def get_point(self, index):
        h = self._handles[index]
        return (h.pos().x(), h.pos().y())

    def index_of(self, handle):
        return self._handles.index(handle)

    def set_editable(self, editable):
        """Toggle vertex editing (handles only show when also visible)."""
        self._editable = editable
        self._apply_handle_visibility()
        self._update_mouse_buttons()

    def set_visible(self, visible):
        """Show/hide the whole contour (outline and, if editable, handles)."""
        self._visible = visible
        self._outline.setVisible(visible)
        self._apply_handle_visibility()
        self._update_mouse_buttons()

    def remove(self):
        for h in self._handles:
            self._scene.removeItem(h)
        self._handles = []
        self._scene.removeItem(self._outline)

    # ----- geometry primitives (no undo recording) -------------------------

    def move_vertex(self, index, x, y):
        self._handles[index].setPos(QPointF(x, y))
        self._rebuild_path()

    def insert_vertex_at(self, index, x, y):
        self._add_handle(QPointF(x, y), index=index)
        self._rebuild_path()

    def delete_vertex(self, index):
        handle = self._handles.pop(index)
        self._scene.removeItem(handle)
        self._rebuild_path()

    # ----- interactive edits (report to the edit sink) ---------------------

    def _delete_vertex_interactive(self, handle):
        if len(self._handles) <= _MIN_VERTICES:
            return
        index = self.index_of(handle)
        xy = self.get_point(index)
        self.delete_vertex(index)
        if self._sink is not None:
            self._sink.record_delete(self, index, xy)

    def _insert_vertex_interactive(self, scene_pos):
        if not self._editable or len(self._handles) < 2:
            return
        pts = [h.pos() for h in self._handles]
        n = len(pts)
        segments = n if self._closed else n - 1
        best_i, best_d = 0, None
        for i in range(segments):
            a = pts[i]
            b = pts[(i + 1) % n]
            d = _dist_point_to_segment(scene_pos, a, b)
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        index = best_i + 1
        xy = (scene_pos.x(), scene_pos.y())
        self.insert_vertex_at(index, xy[0], xy[1])
        if self._sink is not None:
            self._sink.record_insert(self, index, xy)

    def _record_move(self, handle, start, end):
        index = self.index_of(handle)
        if self._sink is not None:
            self._sink.record_move(self, index,
                                   (start.x(), start.y()),
                                   (end.x(), end.y()))

    # ----- internals -------------------------------------------------------

    def _apply_handle_visibility(self):
        show = self._visible and self._editable
        for h in self._handles:
            h.setVisible(show)

    def _update_mouse_buttons(self):
        active = self._editable and self._visible
        self._outline.setAcceptedMouseButtons(
            Qt.AllButtons if active else Qt.NoButton)

    def _add_handle(self, scene_point, index=None):
        handle = VertexHandle(self, scene_point)
        handle.setVisible(self._visible and self._editable)
        self._scene.addItem(handle)
        if index is None:
            self._handles.append(handle)
        else:
            self._handles.insert(index, handle)

    def _on_handle_moved(self):
        # Only the outline is rebuilt here, so this does not move handles and
        # therefore does not recurse.
        self._rebuild_path()

    def _rebuild_path(self):
        path = QPainterPath()
        if self._handles:
            path.moveTo(self._handles[0].pos())
            for h in self._handles[1:]:
                path.lineTo(h.pos())
            if self._closed:
                path.closeSubpath()
        self._outline.setPath(path)
