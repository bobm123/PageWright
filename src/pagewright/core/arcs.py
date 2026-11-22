"""Find the circles hiding in a traced polygon, and redraw them.

A traced circle is a lie told in straight lines. `approxPolyDP` is
given a tolerance and obeys it exactly, so a 22 mm disc at the 0.35 mm
default comes back as a 16-gon whose chords fall up to 0.344 mm INSIDE
the drawn arc - the polygon is 2.6% under area, and every one of those
sixteen corners is a corner the cutter will actually cut.

Making the tolerance finer does not fix the kind of error, only its
size: the result is still a polygon approximating a circle, still
inscribed, still under area, and now with hundreds of vertices to
carry around. The fix is to stop approximating. A circle that is
RECOGNISED is stored as a centre and a radius, and the facets are
generated at the moment something needs facets - which is what
OpenSCAD's `$fn` does, and the reason it is a global there rather than
baked into each primitive.

So this module does two separable things:

  * `fit_circle` / `detect_circles` decide whether a loop of points is
    an arc, and measure it. That is a question about the drawing.
  * `circle_points` turns a centre and radius back into a polygon at a
    chosen facet count. That is a question about the output device,
    and the two should not be answered at the same time.

CIRCUMSCRIBED, NOT INSCRIBED. `circle_points` puts the facet MIDPOINTS
on the true circle, so half the error lies outside the arc and half
inside instead of all of it inside. For the same facet count that
roughly halves the AREA error - though not the worst radial
departure, which moves to the vertices and grows slightly. Measured
on an 11 mm radius:

      facets   inscribed   circumscribed   worst radial dev
           8     -9.97%          +5.48%          0.906 mm
          16     -2.55%          +1.31%          0.215 mm
          32     -0.64%          +0.32%          0.053 mm
          64     -0.16%          +0.08%          0.013 mm

Note the sign as well as the size: an inscribed polygon is always
UNDER, so a traced disc is always too small and a traced hole always
too tight, and the error accumulates in one direction across a
drawing. Straddling the arc does not make the error vanish - nothing
short of a real arc does - but it stops it being systematic.

PARTIAL ARCS ARE REAL AND ARE NOT HANDLED HERE. Two of the four round
things on the Pogo sheet are a disc with something cut into it - the
nose plug and the cowling each carry half of a shared keel notch.
Measured at this module's own tolerance they report on_arc 0.44 and
0.76 against 1.00 for the two true discs, with max_dev 1.3 mm against
0.05. So they are clearly distinguishable, but NOT by a tidy
threshold: how much of the loop survives depends on how far the notch
drags the fitted centre, and a deep notch on a small disc drags it a
long way. Use max_dev as well as on_arc, and do not read a middling
on_arc as "mostly a circle" without looking.

`detect_circles` reports both and leaves the judgement to the caller.
It will not SPLIT a loop into arcs and segments; that wants a proper
arc/line decomposition and is a larger piece of work than this.

cv2/NumPy only, plain loops: ports with the rest of core.
"""

import math

import numpy as np


# Default facet count, the `$fn` of this module. 32 is not a rounding
# of taste: at 11 mm radius (the Pogo wheel discs and nose plug) it
# keeps the worst radial error near 0.013 mm, which is a tenth of a
# scan pixel at 200 dpi and far finer than anything a knife or a laser
# will reproduce. Callers that are exporting for a coarse device, or
# want a visibly faceted part, pass their own.
DEFAULT_FACETS = 32

# Smallest facet count that still means anything. Three points make a
# triangle, which is a legitimate - if brutal - rendering of a circle,
# and OpenSCAD allows it for the same reason.
MIN_FACETS = 3

# How closely the points must hug the fitted circle, as a FRACTION of
# the radius, before the loop is called a circle. 0.02 is generous
# against what a real traced circle gives (measured on the Pogo discs:
# radial standard deviation 0.002 and 0.009 of the radius) and tight
# enough to reject the rounded-off parts that are not circles.
DEFAULT_TOLERANCE_FRACTION = 0.02

# A loop needs at least this many points before a circle fit means
# anything. Below it almost any small polygon fits some circle.
MIN_POINTS = 8


class Circle(object):
    """A fitted circle and how well it describes the points it came from.

    `on_arc` is the fraction of the loop's vertices within tolerance of
    the fitted circle, and it is what separates a disc (1.0) from a
    disc with a notch in it (0.84-0.87 on this sheet) from something
    that merely has a roundish end. `max_dev` is the worst radial
    departure, in the same units as the points.
    """

    def __init__(self, centre, radius, on_arc, max_dev, rms_dev):
        self.centre = (float(centre[0]), float(centre[1]))
        self.radius = float(radius)
        self.on_arc = float(on_arc)
        self.max_dev = float(max_dev)
        self.rms_dev = float(rms_dev)

    def is_full_circle(self, min_on_arc=0.98):
        """True when essentially every vertex lies on the arc."""
        return self.on_arc >= float(min_on_arc)

    def __repr__(self):
        return ("Circle(centre=(%.2f, %.2f), r=%.3f, on_arc=%.2f, "
                "max_dev=%.3f)" % (self.centre[0], self.centre[1],
                                   self.radius, self.on_arc, self.max_dev))


def fit_circle(points):
    """Least-squares circle through `points`. Returns (centre, radius).

    Algebraic (Kasa) fit: solve for D, E, F in
    x^2 + y^2 + Dx + Ey + F = 0, which is linear in the unknowns and
    so needs no starting guess and cannot fail to converge. It is
    slightly biased when the points cover only a short arc - a
    geometric fit would not be - but every case this is used for
    covers most of a circle, where the two agree to far less than a
    scan pixel.

    Returns (None, 0.0) when the points are degenerate (fewer than
    three, or collinear).
    """
    p = np.asarray(points, dtype=float)
    if len(p) < 3:
        return (None, 0.0)
    x, y = p[:, 0], p[:, 1]
    a = np.stack([x, y, np.ones_like(x)], axis=1)
    b = -(x * x + y * y)
    try:
        sol, _res, rank, _sv = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return (None, 0.0)
    if rank < 3:
        return (None, 0.0)              # collinear: no circle
    d, e, f = sol
    cx, cy = -d / 2.0, -e / 2.0
    inner = cx * cx + cy * cy - f
    if inner <= 0.0:
        return (None, 0.0)
    return ((cx, cy), math.sqrt(inner))


def measure_circle(points, tolerance_fraction=None, facets_known=None):
    """Fit a circle and say how well it fits. Returns Circle or None.

    Does NOT decide whether the loop "is" a circle - it reports
    `on_arc` and lets the caller apply its own threshold, because a
    disc with a notch and a disc without one are both worth knowing
    about and only the caller knows which it wants.

    A NOTE ON WHICH RADIUS COMES BACK. A least-squares fit recovers
    the radius THROUGH THE VERTICES, which is the true radius only for
    an inscribed polygon. Points from `circle_points` are
    circumscribed by default, so their vertices sit at r/cos(pi/n) and
    a plain fit reads high - by 3.5% at 12 facets, which is not
    nothing. Pass `facets_known` when the facet count is known (the
    polygon came from `circle_points`, or a project recorded it) and
    the radius is corrected back to the arc. When a polygon was traced
    from ink there is nothing to pass and nothing to correct: a trace
    follows the drawn line, not a construction.
    """
    if tolerance_fraction is None:
        tolerance_fraction = DEFAULT_TOLERANCE_FRACTION
    p = np.asarray(points, dtype=float)
    if len(p) < MIN_POINTS:
        return None
    centre, radius = fit_circle(p)
    if centre is None or radius <= 0.0:
        return None
    # Deviation is always measured against the radius the points
    # themselves describe. Correcting the reported radius for a known
    # facet count and then measuring against the corrected value would
    # call every vertex an outlier, since they legitimately sit at
    # r/cos(pi/n).
    dev = np.abs(np.hypot(p[:, 0] - centre[0], p[:, 1] - centre[1]) - radius)
    tol = float(tolerance_fraction) * radius
    on_arc = float((dev <= tol).mean())
    max_dev = float(dev.max())
    rms = float(math.sqrt(float((dev ** 2).mean())))
    if facets_known:
        n = int(facets_known)
        if n >= MIN_FACETS:
            radius *= math.cos(math.pi / n)
    return Circle(centre, radius, on_arc, max_dev, rms)


def detect_circles(contours, tolerance_fraction=None, min_on_arc=0.98):
    """Which of several contours are circles. Returns {index: Circle}.

    `contours` is a sequence of point lists. Only loops whose vertices
    essentially all lie on their own fitted circle are returned, so a
    notched disc is left out by default; lower `min_on_arc` to catch
    those too, and read `on_arc` to tell them apart.
    """
    out = {}
    for i, pts in enumerate(contours):
        c = measure_circle(pts, tolerance_fraction)
        if c is not None and c.is_full_circle(min_on_arc):
            out[i] = c
    return out


def circle_points(centre, radius, facets=None, start_deg=0.0,
                  circumscribe=True):
    """A circle as a polygon of `facets` sides - this module's `$fn`.

    With `circumscribe` (the default) the facet MIDPOINTS sit on the
    true circle, so the polygon straddles the arc instead of sitting
    inside it. The vertices then lie at radius / cos(pi/facets).
    Pass `circumscribe=False` for the inscribed polygon, which is what
    a naive trace produces and is wanted only when matching one.

    `start_deg` rotates the facets, which matters when a circle has to
    line up with something else - a notch, a flat, another part.
    """
    if facets is None:
        facets = DEFAULT_FACETS
    n = int(facets)
    if n < MIN_FACETS:
        n = MIN_FACETS
    r = float(radius)
    if circumscribe:
        r = r / math.cos(math.pi / n)
    cx, cy = float(centre[0]), float(centre[1])
    out = []
    for i in range(n):
        a = math.radians(start_deg) + 2.0 * math.pi * i / n
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def facets_for_tolerance(radius, max_deviation, circumscribe=True):
    """Fewest facets whose worst radial error is within `max_deviation`.

    The useful direction for an exporter: a caller knows what its
    device can resolve, not how many sides it wants.

    The two cases have DIFFERENT worst errors and it is not a factor
    of two. An inscribed polygon is worst at the facet midpoints, by
    the sagitta r(1 - cos(pi/n)). A circumscribed one is exact at the
    midpoints and worst at the vertices, by r(sec(pi/n) - 1), which is
    slightly LARGER - at 8 facets on an 11 mm radius, 0.906 mm against
    0.837. Circumscribing improves the AREA, not the worst radial
    departure, and conflating the two is how an earlier version of
    this function promised 0.5 mm and delivered 0.906.

    Returns MIN_FACETS at least, and is not capped - ask for a micron
    on a metre and you will get a very large number, which is the
    honest answer.
    """
    r = float(radius)
    tol = float(max_deviation)
    if r <= 0.0 or tol <= 0.0:
        return DEFAULT_FACETS
    if circumscribe:
        # Vertices sit at r/cos(pi/n) and midpoints exactly on r, so
        # the worst radial error is OUTWARD at the vertices:
        #   r(sec(pi/n) - 1) <= tol
        #   => cos(pi/n) >= 1 / (1 + tol/r)
        c = 1.0 / (1.0 + tol / r)
    else:
        # Inscribed: worst error is the sagitta at the facet midpoints,
        #   r(1 - cos(pi/n)) <= tol
        c = 1.0 - tol / r
    if c <= -1.0:
        return MIN_FACETS
    c = min(1.0, c)
    if c >= 1.0:
        return MIN_FACETS               # any polygon meets it
    n = math.ceil(math.pi / math.acos(c))
    return max(MIN_FACETS, int(n))
