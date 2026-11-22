"""Geometric hints: what a caller knows that the pixels cannot say.

A traced outline is a measurement, and a measurement of a drawn line
cannot recover the draughtsman's intent. The ink says a corner is
89.3 degrees; it does not say whether that is a right angle drawn by
hand or a deliberate 89. It says a rib's nose is a rounded V; it does
not say the V seats a square spar turned on edge and is therefore
exactly 90 degrees about its own bisector.

WHO SUPPLIES THE INTENT. Not this module, and not PageWright. A hint
is the vocabulary an APPLICATION uses to tell core what it already
knows about the drawing in front of it - a model-aircraft program
knows what a rib is, what a former is, and that a leading edge is
square stock on the diagonal. `core` stays free of that domain
knowledge and offers only the geometry to act on it. The same
primitives serve a furniture program that knows about dados, or a
dressmaking one that knows about darts.

So there is no table of part types here, and there should not be one.
`birds_mouth` is not "a rib's leading edge"; it is "a V-shaped notch
whose two faces meet at a known included angle". The caller decides
that its ribs have one.

WHY NOT JUST SNAP TO THE PART AXES. `notches.snap_notches` squares
near-axis edges to ONE part-wide grid, which is right for a compact
part whose edges really do share a grid, and wrong for everything
else. Measured on this project:

  * A SWEPT part has no single axis. The two halves of a keel
    reported grid angles 9 degrees apart, and squaring their notch
    walls to a part-wide angle splayed the slots into Vs.
  * A FEATURE may have its own frame. A bird's mouth bisected at
    -30 degrees is still a 90 degree notch; measured against the
    part axes it looks like two unrelated 15 and 75 degree edges and
    no axis rule will square it.

The fix in both cases is the same: square each feature in ITS OWN
frame. That frame can come from the local edge direction, from a
feature's bisector, or from the caller. This module supplies the
frame; `notches` supplies the squaring.

MEASURED, NOT ASSUMED. The bird's mouth default of 90 degrees is not
a guess: on the Pogo wing ribs the two faces of R1's mouth fit to
+45.0 and -45.0 degrees against its own bisector, an included angle
of 90.0. That is square stock turned on edge, exactly as the builder
would cut it.

cv2/NumPy only, plain loops: ports with the rest of core.
"""

import math

import numpy as np

# A hint may ask for a tolerance finer than the trace's own. The
# default simplification tolerance is one number for a whole part,
# which is the wrong granularity for a part that is mostly long
# straight edges with one small precise feature. Measured on this
# project: at the 0.35 mm default a 22 mm circle came out a 16-gon
# whose chords fell 0.344 mm inside the arc, 2.6% under area, and a
# rib's bird's mouth was flattened into a single near-vertical face.
# A feature that carries a tolerance gets traced at that tolerance
# and the rest of the outline is left alone.
DEFAULT_FEATURE_TOLERANCE_MM = 0.08

# How far from the nominal angle a measured V may sit and still be
# treated as that feature. Generous: the point of a hint is that the
# caller is ASSERTING the angle, so this only has to reject a feature
# that was mis-located, not decide a close call. A hand-drawn 90
# degree notch measured 89.3 and 90.6 on this sheet.
DEFAULT_ANGLE_TOLERANCE_DEG = 18.0

# Shortest edge, in millimetres, that can be a real face of a feature
# rather than a simplification artifact. Below this a "face" is one or
# two vertices of noise and fitting a direction to it is meaningless.
MIN_FACE_MM = 0.35


class Vee(object):
    """A V-shaped notch found in an outline, in its own frame.

    `index` is the vertex at the apex. `bisector_deg` is the direction
    the notch points, measured in the image frame, so a mouth opening
    to the left reads about 180. `included_deg` is the angle between
    the two faces. `upper` and `lower` are the indices of the vertices
    at the open ends of the two faces.

    The frame is what makes this useful: squaring the faces means
    moving them to +-included/2 ABOUT THE BISECTOR, which is a
    statement no part-wide axis can make.
    """

    def __init__(self, index, bisector_deg, included_deg, upper, lower,
                 depth_mm):
        self.index = int(index)
        self.bisector_deg = float(bisector_deg)
        self.included_deg = float(included_deg)
        self.upper = int(upper)
        self.lower = int(lower)
        self.depth_mm = float(depth_mm)

    def __repr__(self):
        return ("Vee(index=%d, bisector=%.1f, included=%.1f, depth=%.2fmm)"
                % (self.index, self.bisector_deg, self.included_deg,
                   self.depth_mm))


def _edge_dir_deg(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _ang_diff(a, b):
    """Signed smallest difference a-b, in (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def find_vees(points, mm_per_pixel, included_deg=90.0,
              angle_tol_deg=None, min_face_mm=None):
    """Every V-shaped notch in `points` near the given included angle.

    `points` is a closed polygon [(x, y), ...] in image pixels.
    Returns [Vee], in polygon order.

    A V is three consecutive vertices whose two edges turn INTO the
    part by about `included_deg`. Both faces must be real edges (at
    least `min_face_mm`), which is what keeps a simplification artifact
    or a single noisy vertex from reading as a notch.

    Note what this does NOT do: it does not decide which V is a
    leading edge, a dart or a dado. It reports the geometry and the
    caller, which knows the domain, decides what each one is.
    """
    if angle_tol_deg is None:
        angle_tol_deg = DEFAULT_ANGLE_TOLERANCE_DEG
    if min_face_mm is None:
        min_face_mm = MIN_FACE_MM
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3 or not mm_per_pixel or mm_per_pixel <= 0.0:
        return []
    min_face_px = float(min_face_mm) / float(mm_per_pixel)

    out = []
    for i in range(n):
        h, j = (i - 1) % n, (i + 1) % n
        a, b, c = pts[h], pts[i], pts[j]
        la = math.hypot(*(b - a))
        lb = math.hypot(*(c - b))
        if la < min_face_px or lb < min_face_px:
            continue
        # Directions of the two faces, both measured OUT of the apex,
        # so the angle between them is the included angle directly.
        da = _edge_dir_deg(b, a)
        db = _edge_dir_deg(b, c)
        inc = abs(_ang_diff(da, db))
        if abs(inc - float(included_deg)) > float(angle_tol_deg):
            continue
        # The bisector points out of the apex, between the two faces.
        half = _ang_diff(db, da) / 2.0
        bis = da + half
        depth = min(la, lb) * float(mm_per_pixel)
        out.append(Vee(i, bis % 360.0, inc, h, j, depth))
    return out


def square_vee(points, vee, mm_per_pixel, included_deg=90.0,
               symmetric=True):
    """Move a V's two faces to an exact included angle about its bisector.

    Returns a new point list; `points` is not modified. Only the two
    vertices at the open ends of the faces move, and each moves ALONG
    its own face, so the faces keep their length and the rest of the
    outline is untouched.

    `symmetric` puts both faces at half the included angle either side
    of the measured bisector, which is what a notch cut by a saw set
    to one angle looks like. With `symmetric=False` the bisector is
    recomputed so that the face that is already longer keeps its
    direction and only the other moves - use that when one face is a
    continuation of the part's own surface and should not be disturbed.

    Idempotent: squaring an already-square V returns it unchanged to
    within floating point.
    """
    pts = [tuple(float(v) for v in p) for p in points]
    n = len(pts)
    if n < 3 or vee is None:
        return pts
    i, hi, ji = vee.index, vee.upper, vee.lower
    if not (0 <= i < n and 0 <= hi < n and 0 <= ji < n):
        return pts
    apex = np.array(pts[i], dtype=float)
    a = np.array(pts[hi], dtype=float)
    c = np.array(pts[ji], dtype=float)
    la = math.hypot(*(a - apex))
    lb = math.hypot(*(c - apex))
    if la <= 0.0 or lb <= 0.0:
        return pts

    da = _edge_dir_deg(apex, a)
    db = _edge_dir_deg(apex, c)
    half = float(included_deg) / 2.0

    if symmetric:
        bis = da + _ang_diff(db, da) / 2.0
    else:
        # Keep the longer face; swing the shorter one to meet it.
        keep, other = (da, db) if la >= lb else (db, da)
        sign = 1.0 if _ang_diff(other, keep) >= 0.0 else -1.0
        bis = keep + sign * half

    # Which face is on which side of the bisector stays as measured,
    # so the notch cannot flip over.
    sa = 1.0 if _ang_diff(da, bis) >= 0.0 else -1.0
    sb = -sa
    na = math.radians(bis + sa * half)
    nb = math.radians(bis + sb * half)
    pts[hi] = (apex[0] + la * math.cos(na), apex[1] + la * math.sin(na))
    pts[ji] = (apex[0] + lb * math.cos(nb), apex[1] + lb * math.sin(nb))
    return pts


def square_to_local_edge(points, index, mm_per_pixel, reference_mm=6.0,
                         angle_deg=90.0, angle_tol_deg=None):
    """Square one edge to its NEIGHBOURHOOD, not to a part-wide axis.

    `index` is the edge from vertex `index` to `index+1`. The reference
    direction is taken from the outline `reference_mm` either side of
    it, so a notch wall on a curved part is squared to the curve where
    it actually sits.

    This is the piece `notches.snap_notches` is missing. That function
    squares every near-axis edge to one grid angle measured over the
    whole part, which is why it splayed the slots on a swept keel: the
    two halves of one keel reported grid angles 9 degrees apart, so
    neither matched the local edge direction anywhere in particular.

    Returns a new point list. The edge rotates about its own midpoint,
    so neither neighbouring edge is lengthened more than the other.
    """
    if angle_tol_deg is None:
        angle_tol_deg = DEFAULT_ANGLE_TOLERANCE_DEG
    pts = [tuple(float(v) for v in p) for p in points]
    n = len(pts)
    if n < 4 or not mm_per_pixel or mm_per_pixel <= 0.0:
        return pts
    i = int(index) % n
    j = (i + 1) % n
    reach_px = float(reference_mm) / float(mm_per_pixel)

    ref = _local_direction(pts, i, j, reach_px)
    if ref is None:
        return pts
    cur = _edge_dir_deg(pts[i], pts[j])
    want = ref + float(angle_deg)
    delta = _ang_diff(want, cur)
    # Fold onto the nearest equivalent: an edge is a direction, not an
    # arrow, so 180 degrees away is the same edge.
    if abs(delta) > 90.0:
        delta = _ang_diff(delta, 180.0 if delta > 0 else -180.0)
    if abs(delta) > float(angle_tol_deg):
        return pts                      # too far off to be this feature

    a = np.array(pts[i], dtype=float)
    b = np.array(pts[j], dtype=float)
    mid = (a + b) / 2.0
    r = math.radians(delta)
    cs, sn = math.cos(r), math.sin(r)
    for k, p in ((i, a), (j, b)):
        d = p - mid
        pts[k] = (mid[0] + d[0] * cs - d[1] * sn,
                  mid[1] + d[0] * sn + d[1] * cs)
    return pts


def _local_direction(pts, i, j, reach_px):
    """Direction of the outline either side of edge i-j, as one angle.

    Walks back from `i` and forward from `j` until `reach_px` of
    outline has been covered each way, and takes the chord between the
    two points reached. That follows a curve, which fitting a line to
    the whole part cannot do.
    """
    n = len(pts)
    def walk(start, step):
        acc = 0.0
        k = start
        for _ in range(n):
            nxt = (k + step) % n
            acc += math.hypot(pts[nxt][0] - pts[k][0],
                              pts[nxt][1] - pts[k][1])
            k = nxt
            if acc >= reach_px:
                break
        return k
    back = walk(i, -1)
    fwd = walk(j, +1)
    if back == fwd:
        return None
    return _edge_dir_deg(pts[back], pts[fwd])
