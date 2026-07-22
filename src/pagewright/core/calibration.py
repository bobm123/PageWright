"""Two-point scale calibration and unit conversion.

Internal math stays in millimetres. Display units are converted only at the
edges (see PLAN.md sec. 6). Pure arithmetic -- ports cleanly to C/C++.
"""

import math

# Supported display units and their size in millimetres.
MM_PER_UNIT = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
}


def to_mm(value, unit):
    """Convert a value expressed in `unit` to millimetres."""
    try:
        factor = MM_PER_UNIT[unit]
    except KeyError:
        raise ValueError("unknown unit: %r" % (unit,))
    return value * factor


def from_mm(value_mm, unit):
    """Convert a millimetre value to `unit`."""
    try:
        factor = MM_PER_UNIT[unit]
    except KeyError:
        raise ValueError("unknown unit: %r" % (unit,))
    return value_mm / factor


def pixel_distance(p0, p1):
    """Euclidean distance in pixels between two (x, y) points."""
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    return math.sqrt(dx * dx + dy * dy)


def mm_per_pixel(p0, p1, real_distance, unit):
    """Compute mm-per-pixel from two clicked points and a known real distance.

    `real_distance` is given in `unit`; the result is always millimetres per
    pixel. Raises ValueError if the two points coincide.
    """
    d_px = pixel_distance(p0, p1)
    if d_px <= 0.0:
        raise ValueError("the two calibration points must be distinct")
    d_mm = to_mm(real_distance, unit)
    if d_mm <= 0.0:
        raise ValueError("the real distance must be positive")
    return d_mm / d_px


def format_length(value_mm, unit, decimals=2):
    """Human-readable length in the requested display unit, e.g. '152.40 mm'."""
    return "%.*f %s" % (decimals, from_mm(value_mm, unit), unit)
