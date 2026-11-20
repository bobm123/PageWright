"""Geometry helpers: bounding boxes, margins, and pixel<->mm transforms.

All geometry is stored in pixel coordinates (the source of truth) and converted
to millimetres only at export time (see PLAN.md sec. 4). Plain arithmetic --
ports cleanly to C/C++.

Coordinate convention: origin top-left, y increases downward.
"""

import math


class BBox:
    """An axis-aligned bounding box in pixel coordinates."""

    def __init__(self, min_x, min_y, max_x, max_y):
        self.min_x = min_x
        self.min_y = min_y
        self.max_x = max_x
        self.max_y = max_y

    @property
    def width(self):
        return self.max_x - self.min_x

    @property
    def height(self):
        return self.max_y - self.min_y

    def expanded(self, margin_px):
        """Return a new box grown by `margin_px` on every side."""
        return BBox(
            self.min_x - margin_px,
            self.min_y - margin_px,
            self.max_x + margin_px,
            self.max_y + margin_px,
        )

    def __repr__(self):
        return "BBox(%g, %g, %g, %g)" % (
            self.min_x, self.min_y, self.max_x, self.max_y)


def bbox_of_points(points):
    """Bounding box of an iterable of (x, y) points. Raises if empty."""
    it = iter(points)
    try:
        first = next(it)
    except StopIteration:
        raise ValueError("cannot compute bounding box of zero points")
    min_x = max_x = first[0]
    min_y = max_y = first[1]
    for x, y in it:
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
    return BBox(min_x, min_y, max_x, max_y)


def union_bbox(boxes):
    """Smallest box containing all of `boxes`. Raises if empty."""
    it = iter(boxes)
    try:
        acc = next(it)
    except StopIteration:
        raise ValueError("cannot union zero bounding boxes")
    min_x, min_y = acc.min_x, acc.min_y
    max_x, max_y = acc.max_x, acc.max_y
    for b in it:
        if b.min_x < min_x:
            min_x = b.min_x
        if b.min_y < min_y:
            min_y = b.min_y
        if b.max_x > max_x:
            max_x = b.max_x
        if b.max_y > max_y:
            max_y = b.max_y
    return BBox(min_x, min_y, max_x, max_y)


def px_to_mm(value_px, mm_per_pixel):
    """Scalar pixel length -> millimetres."""
    return value_px * mm_per_pixel


def mm_to_px(value_mm, mm_per_pixel):
    """Scalar millimetre length -> pixels."""
    if mm_per_pixel == 0.0:
        raise ValueError("mm_per_pixel is zero; not calibrated")
    return value_mm / mm_per_pixel


def point_px_to_mm(point_px, mm_per_pixel, origin_px=(0.0, 0.0)):
    """Map a pixel point to millimetres relative to `origin_px`.

    Used at export time to place geometry in the SVG's mm coordinate space.
    """
    x = (point_px[0] - origin_px[0]) * mm_per_pixel
    y = (point_px[1] - origin_px[1]) * mm_per_pixel
    return (x, y)


def snap_angle(p0, p1, step_deg=45.0):
    """Snap p1 so the segment p0->p1 lies on the nearest multiple of
    `step_deg` degrees, preserving its length.

    Used by the Calibrate and Measure gestures while Ctrl is held: with
    the default 45-degree step the line locks to horizontal, vertical
    or the diagonals - measuring along a ruler's edge without drift.
    """
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return (p1[0], p1[1])
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    return (p0[0] + d * math.cos(ang), p0[1] + d * math.sin(ang))


def align_points(points, axis):
    """Line two or more points up on one axis, about their midpoint.

    `axis` is "vertical" for points that should share an x (they then
    sit one above the other, on a vertical line) or "horizontal" for
    points that should share a y.

    The shared value is the MEAN, not the first point's: aligning two
    traced vertices is correcting the trace, and there is no reason to
    believe either one over the other. Snapping to the first would
    also make the result depend on selection order, so the same two
    points would align differently depending on which was clicked.

    Returns a new list; the input is not modified.
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return pts
    if axis == "vertical":
        mx = sum(p[0] for p in pts) / len(pts)
        return [(mx, y) for _x, y in pts]
    if axis == "horizontal":
        my = sum(p[1] for p in pts) / len(pts)
        return [(x, my) for x, _y in pts]
    raise ValueError("axis must be 'vertical' or 'horizontal', got %r"
                     % (axis,))


def square_up(points, grid_deg=0.0):
    """Make a short run of consecutive points meet at right angles.

    `points` is 3 or 4 consecutive vertices - one corner, or the two
    corners of a notch. Each segment between them is snapped to
    whichever of the two part axes it already lies closest to, and
    the points are moved onto those axes.

    `grid_deg` is the part's own dominant angle, NOT the image's. A
    drawing is rarely pasted down square - these traces sit 0.26
    degrees off - and squaring to the image would fight the part and
    undo what the notch cleaner did.

    The ENDS are held and the interior points moved, because the run
    is a piece of a longer contour: moving an end would break the
    segment joining it to the rest, which the caller cannot see. For
    three points that means only the middle one moves; for four, the
    two in the middle.

    Returns a new list; the input is not modified.
    """
    import math
    pts = [(float(x), float(y)) for x, y in points]
    n = len(pts)
    if n < 3 or n > 4:
        raise ValueError("square_up takes 3 or 4 points, got %d" % n)

    a = math.radians(grid_deg)
    c, s = math.cos(a), math.sin(a)
    to = [(x * c + y * s, -x * s + y * c) for x, y in pts]

    out = list(to)
    if n == 3:
        # one corner: the middle point takes the x of one neighbour
        # and the y of the other, whichever way the two segments run
        dx0 = abs(to[1][0] - to[0][0])
        dy0 = abs(to[1][1] - to[0][1])
        if dy0 >= dx0:
            # first segment is the vertical one
            out[1] = (to[0][0], to[2][1])
        else:
            out[1] = (to[2][0], to[0][1])
    else:
        # A notch: the middle two share a coordinate with each other,
        # and each lines up with its own end.
        #
        # Which coordinate they share is decided by how the middle
        # pair sits relative to the ENDS, not by the direction of the
        # run from end to end. A notch cut into a vertical wall runs
        # mostly horizontally end to end while its depth is vertical,
        # so judging by the run flattened the notch to nothing.
        depth_y = abs(((to[1][1] + to[2][1]) / 2.0)
                      - ((to[0][1] + to[3][1]) / 2.0))
        depth_x = abs(((to[1][0] + to[2][0]) / 2.0)
                      - ((to[0][0] + to[3][0]) / 2.0))
        if depth_y >= depth_x:
            # the pair is offset in y: the floor between them is
            # horizontal, and each middle point keeps its end's x
            mid = (to[1][1] + to[2][1]) / 2.0
            out[1] = (to[0][0], mid)
            out[2] = (to[3][0], mid)
        else:
            mid = (to[1][0] + to[2][0]) / 2.0
            out[1] = (mid, to[0][1])
            out[2] = (mid, to[3][1])

    return [(x * c - y * s, x * s + y * c) for x, y in out]
