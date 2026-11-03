"""Square and snap the straight, axis-aligned parts of a traced outline.

Why this exists: on a model-aircraft plan (and most sheet-goods
drawings) the notches that take a stick of stock are drawn square and
to a stock size - 1/16 in for 1/16 in square balsa, or a multiple of
it. A traced outline reproduces them as whatever the pixels happened to
give: walls a degree or two off square, floors 1.39 mm where the
draughtsman meant 1.5875 mm. Cleaning that up by hand is tedious and
the intent is unambiguous, so this does it.

What it deliberately does NOT do:

  * It never touches edges that are not near the part's own axes. On
    the sample former only 19 of 78 edges are near-axis; the other 59
    are the curved profile, and snapping those would wreck the shape.
  * It works in the PART frame, not the screen frame. A drawing is
    rarely pasted down perfectly square (the sample sits 0.8 degrees
    off), so the dominant direction is measured first and everything
    is squared to that.
  * It only moves an edge that is already close to a multiple. The
    window is under half a step wide, so an edge cannot be dragged to
    the wrong multiple; one further out than the tolerance is left
    exactly as traced and reported.

Needs a calibrated project: without mm per pixel there is no 1/16 inch
to snap to. GUI-free, numpy only.
"""

import math

import numpy as np

# Default stock step. 1/16 in is the common balsa square section; a
# drawing using 3/32 or 1/8 in sets its own.
DEFAULT_STEP_MM = 25.4 / 16.0

# How far an edge may be from a multiple and still be snapped, in mm.
# Must stay below half a step or an edge could be pulled to the wrong
# multiple. The observed residual on a real trace was ~0.27 mm median,
# so 0.5 mm catches the honest cases and leaves genuine oddities alone
# (half of a 1/16 in step is 0.79 mm).
DEFAULT_TOLERANCE_MM = 0.5

# How far from the part axes an edge may be and still count as one of
# the straight, squared-off edges.
AXIS_TOLERANCE_DEG = 6.0

# Edges shorter than this are simplification noise, not notch walls.
MIN_EDGE_MM = 0.4

# An edge only counts as part of a notch if at least one of its ends is
# a sharp corner. Short chords of a curve are near-axis purely by
# chance - on a 64-gon a dozen of them are - and squaring those would
# flatten the profile. Measured: a traced circle has NO turns above 60
# degrees, while the sample former has 57.
CORNER_DEG = 60.0


class NotchReport(object):
    """What snap_notches did, for the status bar and for tests."""

    def __init__(self, grid_deg=0.0):
        self.grid_deg = grid_deg
        self.axis_edges = 0          # edges considered
        self.snapped = 0             # edges whose length changed
        self.squared = 0             # edges rotated onto the axis
        self.out_of_tolerance = 0    # near-axis but too far off to snap
        self.max_move_mm = 0.0       # largest single vertex move

    def __repr__(self):
        return ("NotchReport(grid=%.2fdeg axis=%d snapped=%d squared=%d "
                "skipped=%d max_move=%.3fmm)"
                % (self.grid_deg, self.axis_edges, self.snapped,
                   self.squared, self.out_of_tolerance, self.max_move_mm))


def _sharp_corners(pts):
    """Per-vertex: is the turn here a real corner rather than a curve?"""
    n = len(pts)
    out = [False] * n
    for i in range(n):
        a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        v1, v2 = a - b, c - b
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        interior = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
        out[i] = abs(180.0 - interior) >= CORNER_DEG
    return out


class _Clusters(object):
    """Vertices that an axis-aligned edge says must share a coordinate.

    Two edges that pin overlapping vertices describe one line, so the
    clusters are merged as they are added (a tiny union-find).
    """

    def __init__(self):
        self._of = {}            # vertex index -> cluster id
        self._members = {}       # cluster id -> [vertex indices]
        self._sum = {}
        self._count = {}
        self._next = 0

    def add(self, i, j, value):
        cid = self._of.get(i, self._of.get(j))
        if cid is None:
            cid = self._next
            self._next += 1
            self._members[cid] = []
            self._sum[cid] = 0.0
            self._count[cid] = 0
        for v in (i, j):
            if self._of.get(v) is None:
                self._of[v] = cid
                self._members[cid].append(v)
            elif self._of[v] != cid:
                old = self._of[v]
                for m in self._members[old]:
                    self._of[m] = cid
                    self._members[cid].append(m)
                self._sum[cid] += self._sum.pop(old)
                self._count[cid] += self._count.pop(old)
                del self._members[old]
        self._sum[cid] += value
        self._count[cid] += 1

    def ids(self):
        return sorted(self._members, key=lambda c: self._sum[c] /
                      max(1, self._count[c]))

    def values(self):
        return [self._sum[c] / max(1, self._count[c]) for c in self.ids()]

    def assign(self, values):
        out = []
        for cid, value in zip(self.ids(), values):
            for m in self._members[cid]:
                out.append((m, value))
        return out


def _snap_spacing(values, step_px, tol_px):
    """Snap the SPACING between parallel lines to whole steps.

    Two things had to be got right here, both found by testing:

      * The grid origin is fitted to all the lines, not taken from the
        first. Anchoring on an arbitrary member makes every other line
        inherit that one error, which measurably undid the snap.
      * The tolerance is checked on how far each line MOVES, not on its
        distance from the fitted origin. With only two lines a fitted
        origin always fits perfectly, so an origin-based check would
        happily drag a 2.18 mm gap down to 1.59 mm.
    """
    if not values:
        return []
    if len(values) < 3:
        # Two parallel lines define their own phase perfectly, so a
        # fitted grid would "confirm" any gap at all. With too little
        # evidence, measure the gap against whole steps directly.
        if len(values) == 2:
            gap = values[1] - values[0]
            want = round(gap / step_px) * step_px
            if abs(want - gap) <= tol_px and abs(want - gap) > 1e-12:
                half = (want - gap) / 2.0
                return [values[0] - half, values[1] + half]
        return list(values)
    ang = [2.0 * math.pi * (v % step_px) / step_px for v in values]
    sx = sum(math.cos(a) for a in ang)
    sy = sum(math.sin(a) for a in ang)
    if abs(sx) < 1e-12 and abs(sy) < 1e-12:
        origin = 0.0
    else:
        origin = (math.atan2(sy, sx) / (2.0 * math.pi)) * step_px
    out = []
    for v in values:
        want = origin + round((v - origin) / step_px) * step_px
        out.append(want if abs(want - v) <= tol_px else v)
    # Keep the group where it was: a notch is defined by its width, not
    # its position, so remove any overall drift the fit introduced.
    shift = sum(w - v for w, v in zip(out, values)) / float(len(values))
    out = [w - shift for w in out]
    # Re-check after the shift; anything now moving too far stays put.
    return [w if abs(w - v) <= tol_px else v for w, v in zip(out, values)]


def dominant_angle_deg(points, mm_per_pixel, axis_tol_deg=AXIS_TOLERANCE_DEG):
    """The part's own grid direction, in degrees within [0, 90).

    Found by trying every candidate and keeping the one that the most
    EDGE LENGTH lines up with - length-weighted so a few short noisy
    edges cannot outvote the long straight ones.
    """
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return 0.0
    seg = np.roll(pts, -1, axis=0) - pts
    lengths = np.hypot(seg[:, 0], seg[:, 1]) * float(mm_per_pixel)
    angles = np.degrees(np.arctan2(seg[:, 1], seg[:, 0])) % 90.0
    keep = lengths >= MIN_EDGE_MM
    if not keep.any():
        return 0.0
    angles, lengths = angles[keep], lengths[keep]
    best_angle, best_score = 0.0, -1.0
    for cand in np.arange(0.0, 90.0, 0.25):
        off = np.minimum(np.abs(angles - cand), 90.0 - np.abs(angles - cand))
        score = lengths[off < axis_tol_deg].sum()
        if score > best_score:
            best_angle, best_score = float(cand), float(score)
    # The coarse scan ties across the whole tolerance band - every
    # candidate within axis_tol_deg of the same edges scores alike - so
    # take the length-weighted mean of the edges it selected. Without
    # this the winner is whichever candidate the loop happened to reach
    # first, which is arbitrary and, on a shape with no straight edges,
    # meaningless.
    off = np.minimum(np.abs(angles - best_angle),
                     90.0 - np.abs(angles - best_angle))
    sel = off < axis_tol_deg
    if not sel.any():
        return best_angle
    # unwrap around the 0/90 seam before averaging
    chosen = angles[sel].copy()
    chosen[chosen - best_angle > 45.0] -= 90.0
    chosen[best_angle - chosen > 45.0] += 90.0
    refined = float(np.average(chosen, weights=lengths[sel])) % 90.0
    return refined


def _rot(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, s], [-s, c]]), np.array([[c, -s], [s, c]])


def snap_notches(points, mm_per_pixel, step_mm=DEFAULT_STEP_MM,
                 tolerance_mm=DEFAULT_TOLERANCE_MM,
                 axis_tol_deg=AXIS_TOLERANCE_DEG, grid_deg=None):
    """Square and length-snap the axis-aligned edges of one contour.

    Returns (new_points, NotchReport). `points` is [(x, y), ...] in
    image pixels; the result has the same length and winding.

    The two passes are deliberately separate. SQUARING moves an edge
    onto the part axis, which is a statement about direction and is
    safe for any near-axis edge. SNAPPING changes its length to a
    multiple of the stock size, which is a statement about dimension
    and applies only when the edge is already within tolerance.
    """
    pts = np.asarray(points, dtype=float).copy()
    n = len(pts)
    report = NotchReport()
    if n < 3 or not mm_per_pixel or mm_per_pixel <= 0.0 or step_mm <= 0.0:
        return [tuple(p) for p in pts], report

    # A tolerance at or past half a step could pull an edge to the wrong
    # multiple; clamp rather than trust the caller.
    tolerance_mm = min(float(tolerance_mm), 0.49 * float(step_mm))

    if grid_deg is None:
        grid_deg = dominant_angle_deg(pts, mm_per_pixel, axis_tol_deg)
    report.grid_deg = grid_deg
    to_grid, from_grid = _rot(grid_deg)

    work = pts @ to_grid.T          # part frame: notches are axis-aligned
    step_px = float(step_mm) / float(mm_per_pixel)
    tol_px = float(tolerance_mm) / float(mm_per_pixel)
    min_edge_px = MIN_EDGE_MM / float(mm_per_pixel)

    # Per-edge snapping cannot work on a closed loop: each edge shares
    # both endpoints with a neighbour, so moving an edge end breaks the
    # edge before it, and the last edge can never close the ring again.
    # Instead, snap COORDINATES. Every near-axis edge says "these two
    # vertices share an x (or a y)"; collect those into clusters, give
    # each cluster one value, and snap the SPACING between clusters to
    # the stock size. The curved profile is never in a cluster, so it
    # never moves.
    corner = _sharp_corners(work)
    target = work.copy()
    groups = {0: _Clusters(), 1: _Clusters()}   # 0 = share x, 1 = share y
    for i in range(n):
        j = (i + 1) % n
        a, b = work[i], work[j]
        dx, dy = b - a
        if math.hypot(dx, dy) < min_edge_px:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 90.0
        if min(abs(ang), 90.0 - abs(ang)) >= axis_tol_deg:
            continue               # not aligned with the part axes
        if not (corner[i] or corner[j]):
            continue               # a chord of the curve, not a notch
        report.axis_edges += 1
        horizontal = abs(dx) >= abs(dy)
        # a horizontal edge pins its two ends to a common Y
        axis = 1 if horizontal else 0
        if abs(a[axis] - b[axis]) > 1e-9:
            report.squared += 1
        groups[axis].add(i, j, (a[axis] + b[axis]) / 2.0)

    for axis, clusters in groups.items():
        values = clusters.values()
        if not values:
            continue
        snapped = _snap_spacing(values, step_px, tol_px)
        report.snapped += sum(1 for v, w_ in zip(values, snapped)
                              if abs(v - w_) > 1e-9)
        report.out_of_tolerance += sum(
            1 for v, w_ in zip(values, snapped) if abs(v - w_) < 1e-12
            and abs(v - round(v / step_px) * step_px) > tol_px)
        for idx, value in clusters.assign(snapped):
            target[idx][axis] = value

    delta = target - work
    moved = np.hypot(delta[:, 0], delta[:, 1]) * float(mm_per_pixel)
    report.max_move_mm = float(moved.max()) if len(moved) else 0.0
    out = target @ from_grid.T
    return [(float(x), float(y)) for (x, y) in out], report
