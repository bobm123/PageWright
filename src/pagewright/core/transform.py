"""Rotate and flip a traced outline.

A parts sheet is a nesting, not an assembly drawing: the draughtsman
turned parts sideways and mirrored them to save material, so a traced
outline often does not sit the way the part does on the aeroplane.
These put it right at tracing time, where the user can see the result
against the drawing, rather than leaving the correction to whatever
reads the file later.

All of these work on PIXEL coordinates, the same as everything else in
a traced contour, and about the outline's own bounding-box centre so a
part does not wander off when it is turned. Image y grows downward, so
"clockwise" here means clockwise AS DRAWN.

Pure geometry, no model types: the caller passes point lists and gets
point lists back.
"""


def _bounds(loops):
    xs = [x for loop in loops for x, _y in loop]
    ys = [y for loop in loops for _x, y in loop]
    return min(xs), min(ys), max(xs), max(ys)


def _centre(loops):
    x0, y0, x1, y1 = _bounds(loops)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def rotate(loops, quarter_turns):
    """Turn every loop by a multiple of 90 degrees, clockwise as drawn.

    Quarter turns rather than free angles on purpose: a nested part is
    turned by a right angle, and a right angle is exact - no rounding
    creeps into the traced points, and a part turned four times is
    exactly where it started. A free rotation would need resampling
    decisions this does not have to make.
    """
    n = int(quarter_turns) % 4
    if not loops or n == 0:
        return [list(loop) for loop in loops]
    cx, cy = _centre(loops)
    out = []
    for loop in loops:
        turned = []
        for x, y in loop:
            dx, dy = x - cx, y - cy
            for _ in range(n):
                # clockwise on screen, where y grows downward
                dx, dy = -dy, dx
            turned.append((cx + dx, cy + dy))
        out.append(turned)
    return out


def flip(loops, horizontal=True):
    """Mirror every loop about its own centre.

    `horizontal` mirrors left-to-right (about a vertical axis), which
    is what makes a left wing rib from a right one.

    Winding reverses under a mirror, so the point ORDER is reversed
    too and the loop keeps the direction it had. A contour that
    silently flipped its winding would come out inside-out wherever
    winding is read.
    """
    if not loops:
        return []
    cx, cy = _centre(loops)
    out = []
    for loop in loops:
        if horizontal:
            m = [(2.0 * cx - x, y) for x, y in loop]
        else:
            m = [(x, 2.0 * cy - y) for x, y in loop]
        out.append(list(reversed(m)))
    return out


def translate(loops, dx, dy):
    """Move every loop by (dx, dy) pixels."""
    return [[(x + dx, y + dy) for x, y in loop] for loop in loops]
