"""Check a traced part against the same part drawn somewhere else.

A plan sheet draws most parts TWICE. Once on the printwood, as the
flat shape to cut out, and once in a view of the assembled model -
ribs standing in a wing panel, formers stationed along a fuselage,
tail parts lying flat. The two drawings are independent: different
place on the paper, usually a different orientation, drawn at
different times by the same hand.

That redundancy is the only external check a tracer has. Everything
else it can do is self-consistent by construction: a simplification
tolerance is obeyed exactly, a snapped angle is snapped exactly, and
neither can tell you the result is WRONG. Measuring the same part in
two places can.

WHAT THIS CATCHES, concretely, from this project's own history:

  * A rib's bird's mouth flattened away by a simplification tolerance
    coarser than the feature. The printwood trace said the nose was
    three vertices; the wing view still showed a notch.
  * A circle traced as a 16-gon, 2.6% under area. The plan view of
    the same disc is a different polygon with the same diameter.
  * A part traced from merged ink - two neighbouring parts welded
    into one outline - which has no counterpart anywhere.

None of those were found by assertions. All were found by a human
looking at a render, which does not scale past a few dozen parts.

WHAT IT CANNOT DO. It cannot say which of two disagreeing
measurements is right. A mismatch is a question, not a verdict: the
printwood trace may be wrong, the plan trace may be wrong, or they
may be different parts that happen to look alike. This module reports
the disagreement and its size, and leaves the judgement to whoever
knows the aeroplane.

MATCHING BY LABEL. Both drawings letter their parts - F1-F10, H1, H2,
V1-V3, R<n> for ribs, S<n> for stabiliser ribs - and the label is
what the builder uses, so it is what this uses. `core/ocr.py` reads
them where it can. Be warned that it is not dependable on these
sheets: measured here, whole-page OCR found R1-R4 on the printwood
and nothing on the plans, while the same pages tiled and upscaled
found F8/H2/V3 on the plans and lost the R's. So a label is taken as
a HINT that can be absent, never as a precondition, and the geometric
comparison below works without one.

cv2/NumPy only: ports with the rest of core.
"""

import math

import numpy as np


# How far two measurements of one length may differ before it is worth
# a human's attention, as a FRACTION of the length. Both drawings are
# pencil on paper traced through a scanner, so a per-cent or so is
# ordinary. 2% is about the size of the circle error this is meant to
# catch (measured: 2.6%), so it sits just under it.
DEFAULT_LENGTH_TOLERANCE = 0.02

# The same, as an absolute floor in millimetres, so a short edge is
# not flagged for a fraction of a pixel. One scan pixel at 200 dpi is
# 0.127 mm; two of them is a safe floor.
DEFAULT_LENGTH_FLOOR_MM = 0.25

# Area is the check that catches a feature which does not change the
# part's envelope. A notch cut into an edge can leave both principal
# extents exactly as they were - the part is still as long and as wide
# as its widest point - while removing real material. Measured on the
# synthetic case: a mouth that shaves a corner moves the extents by
# nothing at all and the area by several per cent.
DEFAULT_AREA_TOLERANCE = 0.03

# Vertex counts differ legitimately - the two drawings are simplified
# separately, and a part seen edge-on in a plan view shows fewer
# features than the same part flat. Only a LARGE disparity means
# something was lost, and this is the ratio that counts as large.
#
# OFF BY DEFAULT, and that is the important part. The count depends
# almost entirely on the simplification tolerance each trace was given,
# so comparing a 0.35 mm trace with a 0.08 mm one flags EVERY part - a
# fine trace is supposed to have more vertices. Measured: 31 of 31
# parts flagged on `detail` alone, which is a check that tells you
# nothing. Pass `vertex_ratio` explicitly, and only when the two
# drawings really were traced the same way.
DEFAULT_VERTEX_RATIO = None


class Mismatch(object):
    """One disagreement between two drawings of a part.

    `kind` is a short machine-readable tag, `detail` a sentence for a
    human, and `size` the magnitude in whatever unit the kind implies
    (millimetres for lengths, a ratio for counts). `size` is what to
    sort by when there are more mismatches than anyone will read.
    """

    def __init__(self, name, kind, detail, size):
        self.name = str(name)
        self.kind = str(kind)
        self.detail = str(detail)
        self.size = float(size)

    def __repr__(self):
        return "Mismatch(%r, %s, %.3f)" % (self.name, self.kind, self.size)


def extent_mm(points, mm_per_pixel):
    """A part's two principal dimensions, longest first.

    NOT the bounding box, which depends on how the part happens to lie
    on the sheet: the same rib pinned at a slight angle would measure
    differently. These are the extents along the part's own principal
    axes, so they survive the rotation between one drawing and the
    other - which is the whole difficulty in comparing them.
    """
    p = np.asarray(points, dtype=float)
    if len(p) < 3 or not mm_per_pixel or mm_per_pixel <= 0.0:
        return (0.0, 0.0)
    c = p - p.mean(axis=0)
    # Principal axes of the OUTLINE's points. Good enough here because
    # a traced outline samples its own shape fairly evenly; a moment
    # of the filled area would be better for a part with one dense
    # cluster of detail, and is the thing to change if that bites.
    try:
        v = np.linalg.svd(c, full_matrices=False)[2]
    except np.linalg.LinAlgError:
        return (0.0, 0.0)
    a = c @ v[0]
    b = c @ v[1]
    la = float(a.max() - a.min()) * float(mm_per_pixel)
    lb = float(b.max() - b.min()) * float(mm_per_pixel)
    return (max(la, lb), min(la, lb))


def polygon_area_mm2(points, mm_per_pixel):
    """Enclosed area by the shoelace formula, in square millimetres.

    Sign is discarded: the two drawings need not wind the same way,
    and a part traced clockwise is not a different part.
    """
    p = np.asarray(points, dtype=float)
    if len(p) < 3 or not mm_per_pixel or mm_per_pixel <= 0.0:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    s = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    return abs(s) * 0.5 * float(mm_per_pixel) ** 2


def compare(name, a_points, a_mm_per_pixel, b_points, b_mm_per_pixel,
            length_tolerance=None, length_floor_mm=None,
            vertex_ratio=None, area_tolerance=None):
    """Compare two drawings of one part. Returns [Mismatch].

    `a` is conventionally the printwood trace (the shape that will be
    cut) and `b` the same part drawn in an assembly view, but nothing
    here depends on which is which - the comparison is symmetric, and
    deliberately so, because neither drawing is authoritative.

    An empty list means the two agree within tolerance. It does NOT
    mean the part is right: both could be traced from the same bad
    ink, and a part drawn only once cannot be checked at all.
    """
    if length_tolerance is None:
        length_tolerance = DEFAULT_LENGTH_TOLERANCE
    if length_floor_mm is None:
        length_floor_mm = DEFAULT_LENGTH_FLOOR_MM
    if vertex_ratio is None:
        vertex_ratio = DEFAULT_VERTEX_RATIO     # None = do not check
    if area_tolerance is None:
        area_tolerance = DEFAULT_AREA_TOLERANCE

    out = []
    la, sa = extent_mm(a_points, a_mm_per_pixel)
    lb, sb = extent_mm(b_points, b_mm_per_pixel)
    if la <= 0.0 or lb <= 0.0:
        out.append(Mismatch(name, "degenerate",
                            "one of the two outlines has no extent", 0.0))
        return out

    for what, x, y in (("length", la, lb), ("width", sa, sb)):
        d = abs(x - y)
        if d <= float(length_floor_mm):
            continue
        rel = d / max(x, y)
        if rel > float(length_tolerance):
            out.append(Mismatch(
                name, what,
                "%s differs: %.2f mm on one drawing, %.2f mm on the "
                "other (%.1f%%)" % (what, x, y, 100.0 * rel), d))

    aa = polygon_area_mm2(a_points, a_mm_per_pixel)
    ab = polygon_area_mm2(b_points, b_mm_per_pixel)
    if aa > 0.0 and ab > 0.0:
        rel = abs(aa - ab) / max(aa, ab)
        if rel > float(area_tolerance):
            out.append(Mismatch(
                name, "area",
                "area differs: %.1f mm2 on one drawing, %.1f mm2 on the "
                "other (%.1f%%); a feature may be missing from the "
                "larger one" % (aa, ab, 100.0 * rel), abs(aa - ab)))

    na, nb = len(a_points), len(b_points)
    if vertex_ratio and na and nb:
        r = max(na, nb) / float(min(na, nb))
        if r > float(vertex_ratio):
            out.append(Mismatch(
                name, "detail",
                "one drawing carries %d vertices and the other %d; the "
                "sparser one may have lost a feature" % (na, nb), r))
    return out


def match_by_label(a_labels, b_labels):
    """Pair up parts named on both drawings.

    `a_labels` and `b_labels` are {label: anything}. Returns
    (pairs, only_a, only_b) where `pairs` is [(label, a_value,
    b_value)] sorted by label.

    A part in only one of the two is NOT an error and is not reported
    as a mismatch: plenty of parts are drawn once (the printwood shows
    a dihedral gauge that appears in no assembly view), and a label
    the OCR failed to read looks exactly the same as one that is not
    there. The caller gets the lists and decides.
    """
    a = dict(a_labels or {})
    b = dict(b_labels or {})
    both = sorted(set(a) & set(b))
    pairs = [(k, a[k], b[k]) for k in both]
    return (pairs, sorted(set(a) - set(b)), sorted(set(b) - set(a)))


def station_spacings_mm(xs, mm_per_pixel):
    """Gaps between a row of stations, in millimetres.

    Ribs in a wing view and formers along a fuselage are a ROW, and
    the row's spacings are a measurement in their own right - one that
    needs no label, no outline and no match. Two bays that should be
    equal and are not point at a mis-located line; a bay that
    disagrees with the printwood's own notch spacing points at one of
    the two being wrong.

    `xs` are station positions in pixels, in any order.
    """
    v = sorted(float(x) for x in xs)
    if len(v) < 2 or not mm_per_pixel or mm_per_pixel <= 0.0:
        return []
    return [(v[i + 1] - v[i]) * float(mm_per_pixel)
            for i in range(len(v) - 1)]
