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

Applying this twice is the same as applying it once. That is not free:
the obvious implementation keeps moving points every time it is
called, because squaring the edges changes the very angles the part's
grid is measured from. See `snap_notches` for what had to be fixed.

Needs a calibrated project: without mm per pixel there is no 1/16 inch
to snap to. GUI-free, numpy only.
"""

import math

import numpy as np

# Default stock step. 1/32 in, not 1/16: a notch is cut to HALF the
# stock it takes where two pieces cross - a former sitting on a
# 1/16 in keel is notched 1/32 in deep so the two halve into each
# other - so the sizes that actually appear on a sheet step in
# thirty-seconds even when every stick is a sixteenth. A drawing
# using some other module sets its own.
DEFAULT_STEP_MM = 25.4 / 32.0

# How far an edge may be from a multiple and still be snapped, in mm.
# Must stay below half a step or an edge could be pulled to the wrong
# multiple. The observed residual on a real trace was ~0.27 mm median,
# so 0.5 mm catches the honest cases and leaves genuine oddities alone
# (half of a 1/16 in step is 0.79 mm).
DEFAULT_TOLERANCE_MM = 0.5

# Longest edge whose LENGTH is a stock dimension, in steps. A notch
# wall is as wide as the stick it takes, so it is one or two steps and
# rarely more than four. A longer straight edge is structure - a joint
# line where two halves butt, a keel seat, a part's own depth - and
# its length is whatever the design says, not a multiple of anything.
# Measured on the Pogo formers: F8's joint edge came to 5.59 steps of
# 1/16 in and F6's keel seat to 5.74, both between multiples and no
# better on 3/32 or 1/8, and snapping moved them 0.18 and 0.49 mm for
# no reason.
#
# Such an edge is still SQUARED onto the part axis - it ought to be
# straight - but it is pinned where it lies, so the spacing snap works
# around it instead of dragging it.
MAX_SNAP_STEPS = 4.0

# How far from the part axes an edge may be and still count as one of
# the straight, squared-off edges.
AXIS_TOLERANCE_DEG = 6.0

# Edges shorter than this are simplification noise, not notch walls.
MIN_EDGE_MM = 0.4

# How many internal passes `snap_notches` may take to reach a fixed
# point, and how still the geometry must be to count as settled.
#
# A single pass does not always land on its own output. Squaring an
# edge changes its angle, so it can tip an edge that was just outside
# the angle tolerance to just inside it (measured: 6.57 deg -> 5.77
# deg against a 6.0 deg window), and the next pass then squares an
# edge this one did not. Looping until the geometry stops moving
# settles that here instead of leaving it for the caller's next call.
# Two passes is the common case on the sample formers and four the
# most seen, so the cap is slack - a guard against a pathological
# shape, not a budget anything normal is expected to spend. The
# threshold is in mm and is far under what any cutting process holds.
MAX_PASSES = 8
SETTLED_MM = 1e-9

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
        self.pinned = 0              # long edges squared but not snapped
        self.max_move_mm = 0.0       # largest single vertex move
        self.passes = 0              # internal passes used to settle
        self.converged = True        # reached a fixed point within the cap

    def __repr__(self):
        return ("NotchReport(grid=%.2fdeg axis=%d snapped=%d squared=%d "
                "pinned=%d skipped=%d max_move=%.3fmm passes=%d%s)"
                % (self.grid_deg, self.axis_edges, self.snapped,
                   self.squared, self.pinned, self.out_of_tolerance,
                   self.max_move_mm, self.passes,
                   "" if self.converged else " UNCONVERGED"))


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
        self._pinned = set()     # clusters whose value must not move
        self._long = set()       # clusters a long edge contributed to
        self._short = set()      # clusters a short edge contributed to
        self._next = 0

    def mark_long(self, i):
        """Note that a LONG edge contributed vertex `i`."""
        cid = self._of.get(i)
        if cid is not None:
            self._long.add(cid)

    def mark_short(self, i):
        """Note that a SHORT edge contributed vertex `i`.

        One short edge is enough to disqualify a cluster from being
        pinned: a line that a notch wall sits on is a notch line,
        however many long edges also lie along it.
        """
        cid = self._of.get(i)
        if cid is not None:
            self._short.add(cid)

    def all_long(self, cid):
        return cid in self._long and cid not in self._short

    def pin_id(self, cid):
        """Fix this cluster's value against the spacing snap.

        A pinned cluster is still squared - its members are given one
        common coordinate - but the snap leaves that coordinate
        alone. This is how a structural edge stays straight without
        having its position forced to a stock multiple.
        """
        self._pinned.add(cid)

    def is_pinned(self, cid):
        return cid in self._pinned

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
                for tag in (self._pinned, self._long, self._short):
                    if old in tag:
                        tag.discard(old)
                        tag.add(cid)
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


def _axis_edges(work, mm_per_pixel, axis_tol_deg):
    """Indices of the edges that count as straight, squared-off ones.

    One definition, used both by `dominant_angle_deg` to decide what to
    average and by `_snap_once` to decide what to move. They have to
    agree: the grid is only a fixed point of the squaring if the edges
    it is measured from are exactly the edges that get squared onto it.

    `work` is already in the part frame.
    """
    n = len(work)
    min_edge_px = MIN_EDGE_MM / float(mm_per_pixel)
    corner = _sharp_corners(work)
    out = []
    for i in range(n):
        j = (i + 1) % n
        dx, dy = work[j] - work[i]
        if math.hypot(dx, dy) < min_edge_px:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 90.0
        if min(abs(ang), 90.0 - abs(ang)) >= axis_tol_deg:
            continue               # not aligned with the part axes
        if not (corner[i] or corner[j]):
            continue               # a chord of the curve, not a notch
        out.append(i)
    return out


def _snap_spacing(values, step_px, tol_px, _recursing=False):
    """Snap the SPACING between parallel lines to whole steps.

    Idempotent: the result, fed back in, is returned unchanged. That is
    not automatic here and is the property most of this function's
    complexity buys, so it is checked explicitly at the end rather than
    assumed.

    Three things had to be got right, all found by testing:

      * The grid origin is fitted to all the lines, not taken from the
        first. Anchoring on an arbitrary member makes every other line
        inherit that one error, which measurably undid the snap.
      * The tolerance is checked on how far each line MOVES, not on its
        distance from the fitted origin. With only two lines a fitted
        origin always fits perfectly, so an origin-based check would
        happily drag a 2.18 mm gap down to 1.59 mm.
      * The de-drift that keeps the group in place is folded into the
        ORIGIN, before snapping, not subtracted from the snapped
        values afterwards. Subtracting afterwards slides every line
        back off the grid it had just been snapped to, which is one of
        the things that made `snap_notches` creep on every call.

    On the last point, honestly: with the fixed-point check below in
    place, the post-hoc version cannot be told apart from this one. It
    was searched for - 40000 random groups, and the ten real formers -
    and no input gives a different answer, because any result that is
    not a fixed point is rejected and the lines are left as traced.
    Correcting the origin first is kept because it reaches the right
    answer directly instead of relying on that rejection, but no test
    can distinguish it, so do not expect one to fail if it is changed
    back.
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

    def fit(org):
        """Snap to the grid at `org`; report the mean move of the
        lines that were close enough to be snapped at all."""
        out, moved = [], []
        for v in values:
            want = org + round((v - org) / step_px) * step_px
            if abs(want - v) <= tol_px:
                out.append(want)
                moved.append(want - v)
            else:
                out.append(v)
        drift = sum(moved) / float(len(moved)) if moved else 0.0
        return out, drift

    ang = [2.0 * math.pi * (v % step_px) / step_px for v in values]
    sx = sum(math.cos(a) for a in ang)
    sy = sum(math.sin(a) for a in ang)
    if abs(sx) < 1e-12 and abs(sy) < 1e-12:
        origin = 0.0
    else:
        origin = (math.atan2(sy, sx) / (2.0 * math.pi)) * step_px

    # Keep the group where it was - a notch is defined by its width,
    # not its position - by moving the origin against the drift until
    # the lines that snap to it no longer move the group. The drift
    # must be measured over only the lines that actually MOVE: the
    # tolerance check freezes the rest, so a drift averaged over all of
    # them is not the drift the group really takes, and correcting by
    # it leaves the result off its own grid.
    out, drift = fit(origin)
    for _ in range(MAX_PASSES):
        if abs(drift) <= 1e-12:
            break
        origin -= drift
        out, drift = fit(origin)

    # Only return a snap that is its own fixed point, and prove it the
    # way a caller would: by running the whole function on the result
    # and checking nothing moves. Checking against the origin fitted
    # HERE is not enough, because the next call re-derives the origin
    # from the snapped values and can land somewhere else.
    #
    # Where the result is not a fixed point - a degenerate group with
    # no real grid, where the circular mean is meaningless and the
    # origin fit cannot settle - leave the lines exactly as traced
    # rather than return something that keeps moving. Stability is
    # worth more here than a snap nobody asked for; real notch lines
    # do have a grid and are unaffected.
    #
    # The recursion is one level deep only: this call passes values it
    # has already proved stable, so the inner call returns them
    # unchanged and does not recurse again.
    if _recursing:
        return out
    stable = _snap_spacing(out, step_px, tol_px, _recursing=True)
    if max((abs(a - b) for a, b in zip(out, stable)), default=0.0) > 1e-9:
        return list(values)
    return out


def dominant_angle_deg(points, mm_per_pixel, axis_tol_deg=AXIS_TOLERANCE_DEG):
    """The part's own grid direction, in degrees within [0, 90).

    Found by trying every candidate and keeping the one that the most
    EDGE LENGTH lines up with - length-weighted so a few short noisy
    edges cannot outvote the long straight ones.
    """
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n < 3:
        return 0.0
    seg = np.roll(pts, -1, axis=0) - pts
    all_lengths = np.hypot(seg[:, 0], seg[:, 1]) * float(mm_per_pixel)
    all_angles = np.degrees(np.arctan2(seg[:, 1], seg[:, 0])) % 90.0
    keep = all_lengths >= MIN_EDGE_MM
    if not keep.any():
        return 0.0
    angles, lengths = all_angles[keep], all_lengths[keep]
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
    #
    # The refinement is run to self-consistency, over exactly the
    # edges that snap_notches will SQUARE and no others.
    #
    # Both halves of that matter. Averaging every edge within
    # axis_tol_deg sweeps in short chords of the curved profile that
    # squaring never touches; they keep their original angles, so after
    # squaring they drag the mean somewhere new and each call squares
    # to a different axis (measured on the Pogo formers: up to 1.06
    # degrees per call, walking several degrees and never settling).
    # And the selected set depends on the angle while the angle is the
    # mean of the set, so one shot off the coarse scan can land on an
    # angle that would have chosen a different set. Iterating until the
    # set repeats removes the dependence on where the 0.25 degree scan
    # happened to stop.
    #
    # The result is that squaring these edges onto this angle and
    # measuring again returns the same angle, to ~1e-14, which is what
    # makes snap_notches idempotent.
    refined = best_angle
    seen = []
    for _ in range(MAX_PASSES):
        sel = _axis_edges(pts @ _rot(refined)[0].T, mm_per_pixel,
                          axis_tol_deg)
        if not sel:
            return refined
        # unwrap around the 0/90 seam before averaging
        chosen = all_angles[sel].copy()
        chosen[chosen - refined > 45.0] -= 90.0
        chosen[refined - chosen > 45.0] += 90.0
        refined = float(np.average(chosen,
                                   weights=all_lengths[sel])) % 90.0
        key = tuple(sel)
        if key in seen:
            break
        seen.append(key)
    return refined


def _rot(deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, s], [-s, c]]), np.array([[c, -s], [s, c]])


def _snap_once(points, mm_per_pixel, step_mm, tolerance_mm,
               axis_tol_deg, grid_deg):
    """One square-and-snap pass against a GIVEN grid angle.

    Factored out of `snap_notches` so that function can run it to a
    fixed point. The angle is passed in rather than measured here so
    that one pass is exactly one statement about one grid; `snap_notches`
    owns the decision of when to re-measure it.

    The two parts are deliberately separate. SQUARING moves an edge
    onto the part axis, which is a statement about direction and is
    safe for any near-axis edge. SNAPPING changes its length to a
    multiple of the stock size, which is a statement about dimension
    and applies only when the edge is already within tolerance.
    """
    pts = np.asarray(points, dtype=float).copy()
    n = len(pts)
    report = NotchReport()
    report.grid_deg = grid_deg
    to_grid, from_grid = _rot(grid_deg)

    work = pts @ to_grid.T          # part frame: notches are axis-aligned
    step_px = float(step_mm) / float(mm_per_pixel)
    tol_px = float(tolerance_mm) / float(mm_per_pixel)
    max_snap_px = MAX_SNAP_STEPS * step_px

    # Per-edge snapping cannot work on a closed loop: each edge shares
    # both endpoints with a neighbour, so moving an edge end breaks the
    # edge before it, and the last edge can never close the ring again.
    # Instead, snap COORDINATES. Every near-axis edge says "these two
    # vertices share an x (or a y)"; collect those into clusters, give
    # each cluster one value, and snap the SPACING between clusters to
    # the stock size. The curved profile is never in a cluster, so it
    # never moves.
    target = work.copy()
    groups = {0: _Clusters(), 1: _Clusters()}   # 0 = share x, 1 = share y
    for i in _axis_edges(work, mm_per_pixel, axis_tol_deg):
        j = (i + 1) % n
        a, b = work[i], work[j]
        dx, dy = b - a
        report.axis_edges += 1
        horizontal = abs(dx) >= abs(dy)
        # a horizontal edge pins its two ends to a common Y
        axis = 1 if horizontal else 0
        if abs(a[axis] - b[axis]) > 1e-9:
            report.squared += 1
        groups[axis].add(i, j, (a[axis] + b[axis]) / 2.0)
        # A long edge is structure, not a notch wall: square it, but
        # do not let its position be snapped to a stock multiple.
        #
        # (the pin decision is made on the SPACING below, not here)

    for axis, clusters in groups.items():
        values = clusters.values()
        if not values:
            continue
        # Hold back any line standing across a gap too wide to be a
        # stock dimension.
        #
        # The rule is about the GAP, not about any edge's own length.
        # A notch 1.4 mm wide and 8 mm deep has two long walls, and
        # those walls are exactly what define the 1.4 mm width worth
        # snapping - judging them by their own length pins them and
        # kills the snap that matters. What marks a structural edge
        # is that the distance to its nearest neighbouring line is
        # LARGE: a joint line or a keel seat stands well clear of
        # anything, while a notch wall has its partner a stick's
        # width away.
        #
        # The pins are chosen BEFORE snapping and the pinned lines are
        # withheld from _snap_spacing, rather than snapped with
        # everything else and put back afterwards. Reverting them
        # afterwards is what used to break idempotency: _snap_spacing
        # balances the group so it does not drift, and silently
        # restoring some members afterwards unbalances it again, so the
        # whole group translated a little on every call - decaying
        # about 0.8x a pass and never stopping. Withholding them keeps
        # the de-drift honest, because it is computed over exactly the
        # lines that will actually move.
        free = []
        for k in range(len(values)):
            near = min((abs(values[k] - values[m])
                        for m in range(len(values)) if m != k),
                       default=float("inf"))
            if near > max_snap_px:
                report.pinned += 1
            else:
                free.append(k)
        moved_free = _snap_spacing([values[k] for k in free],
                                   step_px, tol_px)
        snapped = list(values)
        for k, value in zip(free, moved_free):
            snapped[k] = value
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


def snap_notches(points, mm_per_pixel, step_mm=DEFAULT_STEP_MM,
                 tolerance_mm=DEFAULT_TOLERANCE_MM,
                 axis_tol_deg=AXIS_TOLERANCE_DEG, grid_deg=None):
    """Square and length-snap the axis-aligned edges of one contour.

    Returns (new_points, NotchReport). `points` is [(x, y), ...] in
    image pixels; the result has the same length and winding.

    Idempotent: snap_notches(snap_notches(p)) == snap_notches(p), to
    about 1e-14 mm. Three things had to be fixed to get that, all
    found by feeding the output back in and watching it keep moving.

      * The grid angle had to be made a fixed point of the squaring.
        `dominant_angle_deg` now averages exactly the edges that get
        squared; averaging every near-axis edge pulled the angle up to
        1.06 degrees per call, and F6 walked 4.2 degrees over twelve
        calls without settling. See that function.
      * The pinned lines are withheld from the spacing snap instead of
        being snapped and then restored, which used to leave the whole
        cluster group translating a little on every call. See the pin
        block in `_snap_once`.
      * What remains is run to a fixed point here, because one pass
        still need not land on its own output: squaring an edge can
        tip a neighbour that was just outside the angle tolerance to
        just inside it, so the pass after it squares an edge this one
        did not.

    The loop re-measures the grid each pass. That is safe now that the
    angle is stable under squaring, and it is what lets the selection
    settle: an edge that gets tipped into tolerance changes the
    selection, which moves something, which keeps the loop going.

    Note what is NOT needed. The set of axis edges does not have to be
    frozen - once the angle is a fixed point the selection settles on
    its own, in one extra pass at worst. And the axis test must NOT be
    given hysteresis, tempting though it looks: it would make the
    result depend on which edges a PREVIOUS call selected, so this
    would no longer be a function of its input. Iterating here gets
    the same stability while staying pure.
    """
    pts = np.asarray(points, dtype=float).copy()
    n = len(pts)
    if n < 3 or not mm_per_pixel or mm_per_pixel <= 0.0 or step_mm <= 0.0:
        return [tuple(p) for p in pts], NotchReport()

    # A tolerance at or past half a step could pull an edge to the wrong
    # multiple; clamp rather than trust the caller.
    tolerance_mm = min(float(tolerance_mm), 0.49 * float(step_mm))

    # Note the order: whether the CALLER fixed the grid has to be
    # recorded before defaulting it, or the flag is always true and the
    # loop below never re-measures.
    caller_fixed_grid = grid_deg is not None
    if grid_deg is None:
        grid_deg = dominant_angle_deg(pts, mm_per_pixel, axis_tol_deg)
    current = [tuple(p) for p in pts]
    report = None
    for i in range(MAX_PASSES):
        # Re-measure the grid each pass unless the caller fixed it.
        # Squaring can tip an edge that was just outside the tolerance
        # to just inside it (measured: 6.57 deg -> 5.77 deg against a
        # 6.0 deg window), so the set of axis edges, and with it the
        # angle, can still change once after the first pass. Letting
        # both settle HERE is what makes a single call idempotent;
        # leaving it to the caller's next call is what used to make
        # the second call move the geometry again.
        #
        # This stays a pure function of the input: the loop always
        # re-derives the grid from the geometry in hand, so the result
        # never depends on what a previous call selected. That is why
        # the selection is iterated rather than given hysteresis,
        # which would carry state across calls.
        if not caller_fixed_grid:
            grid_deg = dominant_angle_deg(current, mm_per_pixel,
                                          axis_tol_deg)
        nxt, rep = _snap_once(current, mm_per_pixel, step_mm, tolerance_mm,
                              axis_tol_deg, grid_deg)
        # Report the work done by the pass that ran against the
        # caller's own geometry; later passes only settle the result,
        # and counting their edges again would inflate the totals.
        if report is None:
            report = rep
        moved = max((math.hypot(a[0] - b[0], a[1] - b[1])
                     for a, b in zip(current, nxt)), default=0.0)
        current = nxt
        report.passes = i + 1
        report.grid_deg = grid_deg
        # max_move_mm is what the status bar shows the user, so it has
        # to be the distance from the geometry they handed in to the
        # settled result - not what one interior pass happened to move.
        report.max_move_mm = max(
            (math.hypot(a[0] - b[0], a[1] - b[1])
             for a, b in zip(points, current)), default=0.0
        ) * float(mm_per_pixel)
        # The geometry having stopped moving is enough: because the
        # grid is re-measured at the top of each pass, a pass that
        # tipped a new edge inside the angle tolerance changes the
        # selection and so moves something, which keeps the loop
        # going. An explicit "and the selection is stable" condition
        # here was tried and removed again - no case could be built,
        # over 500 jittered formers, where it changed the result.
        if moved * float(mm_per_pixel) <= SETTLED_MM:
            break
    else:
        # Out of passes with the geometry still moving. Hand back the
        # best result anyway - it is closer to the grid than the input
        # was - but say so, rather than claim a fixed point.
        report.converged = False
    return current, report
