"""Snap CAD-drawn geometry to stock size.

A longeron added while working the 3D model should be true to the
stock it will be cut from, the same as one traced off the plan. The
algorithm for that already exists in PageWright
(`core/notches.py`) and is scale-agnostic: points in, points out,
with the scale as an argument.

So this calls it rather than growing a second copy. Passing
`mm_per_pixel=1.0` makes the "pixels" millimetres, which is what
geometry that never came off a scan is already in.

PageWright owns the algorithm because snapping needs a scale and a
calibrated scan is its business. This is the call site for geometry
with no scan behind it.
"""

from .parts import _ensure_pagewright

# Common balsa stock. A plan states its own ("1/16 SQ LEADING EDGE"),
# so these are a convenience, never an assumption.
INCH_MM = 25.4
STOCK_MM = {
    "1/32": INCH_MM / 32.0,
    "1/16": INCH_MM / 16.0,
    "3/32": INCH_MM * 3.0 / 32.0,
    "1/8": INCH_MM / 8.0,
    "3/16": INCH_MM * 3.0 / 16.0,
    "1/4": INCH_MM / 4.0,
}


def stock_mm(name):
    """A stock size by its usual name ("1/16"), in millimetres."""
    key = str(name).strip().strip('"')
    if key in STOCK_MM:
        return STOCK_MM[key]
    raise ValueError("unknown stock %r (known: %s)"
                     % (name, ", ".join(sorted(STOCK_MM))))


def snap_mm(points_mm, step_mm=None, stock=None, tolerance_mm=None):
    """Square and snap an outline given in MILLIMETRES.

    Returns (points, report) as PageWright's `snap_notches` does. An
    edge further from a multiple than the tolerance is left exactly
    as drawn and counted in the report, rather than being dragged to
    a size nobody asked for.
    """
    _ensure_pagewright()
    from pagewright.core import notches

    if step_mm is None:
        step_mm = stock_mm(stock) if stock else notches.DEFAULT_STEP_MM
    kw = {"step_mm": step_mm}
    if tolerance_mm is not None:
        kw["tolerance_mm"] = tolerance_mm
    # mm_per_pixel=1.0: the points are already millimetres.
    return notches.snap_notches(points_mm, 1.0, **kw)
