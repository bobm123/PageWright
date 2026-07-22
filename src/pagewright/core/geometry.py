"""Geometry helpers: bounding boxes, margins, and pixel<->mm transforms.

All geometry is stored in pixel coordinates (the source of truth) and converted
to millimetres only at export time (see PLAN.md sec. 4). Plain arithmetic --
ports cleanly to C/C++.

Coordinate convention: origin top-left, y increases downward.
"""


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
