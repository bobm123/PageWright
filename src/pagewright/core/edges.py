"""Trace one part's two long edges column by column, and find the
notches cut into them.

Why this is not `contours.py`. That module extracts CLOSED polygons:
an outer loop and the holes inside it, each a single ordered ring.
That is the right shape for a former, which is a blob. It is the
wrong shape for the question asked here, which is "where along this
keel is each edge, and where has a notch been cut into which edge".
A long thin part is better described as two functions of x than as
one ring, and the notch search below needs exactly that: a notch is
a local excursion in ONE named edge, and a ring has lost the
distinction between the two sides by the time you hold it.

A keel reaching the far end of a knife taper also has NO enclosed
interior at all - the two edges merge into a single stroke of ink -
so a tool that only reports closed regions cannot measure it. This
module measures from the ink directly and simply drops the columns
where the inside has run out.

THE OUTERMOST PAIR. The one subtle thing here. A column crossing the
part's body cuts the outline twice, so it is tempting to take the
first gap between ink runs as the inside. That is wrong wherever a
notch has been cut: a column through a notch WALL crosses ink four
times, and the first gap then lies between the notch's own two
strokes rather than between the part's edges. Taking it produces an
outline that zigzags into the part at every notch - which is exactly
the artifact that sent me looking. On the Pogo top keel 37 of ~900
columns crossed four runs. The inside is always the OUTERMOST pair:
below the topmost stroke, above the bottommost.

INSIDE, NOT OUTSIDE. Edges are reported at the inside of the drawn
line. A plan's lines here are 8-11 px at 200 DPI (0.04-0.055 in), so
inside-versus-outside shifts every measured length by about 0.1 in -
larger than any plausible laser kerf, and so not a choice that can
be left implicit. `cv2.findContours` makes the opposite choice for
its outer contour (it follows the OUTSIDE of the ink and the inside
only on hole contours), which is a second reason not to build this
on top of it.

cv2/NumPy only, plain loops, no Python-only idioms: it ports to C++
with the rest of core (see PLAN.md sec. 2).
"""

import numpy as np

# How far an edge must be displaced, in pixels, before a run of
# columns counts as a notch rather than scan noise. Measured on the
# 27 notches of the six Pogo keel pieces: depths run 4 to 17 px with
# a median of 11 px (0.055 in at this scan's 0.1266 mm/px, 200 DPI),
# so these are cut about a full 1/16 in deep - the thickness of the
# former passing through - rather than half of it. 4 px keeps the
# shallowest real notch while staying clear of the 1-2 px wander of a
# threshold boundary on a scanned line.
DEFAULT_MIN_DEPTH_PX = 4.0

# A notch is this many columns wide, at least and at most. The lower
# bound rejects a single ragged column; the upper bound is what keeps
# a notch distinct from the part getting genuinely narrower - beyond
# ~40 px (0.17 in) an inward step is the shape of the part, not a
# slot cut in it. A 1/16 in former crossing at a slant is 19 px.
DEFAULT_MIN_WIDTH_PX = 4
DEFAULT_MAX_WIDTH_PX = 40

# Window for the rolling median that says where an edge "would have
# run" with no notch in it. It must be comfortably wider than the
# widest notch (above) so a notch cannot dominate its own baseline,
# and narrower than the length over which the part's own curve
# changes. 151 px is ~0.75 in at the Pogo scan's 200 DPI.
DEFAULT_TREND_WINDOW_PX = 151


def trace_edge_pair(mask):
    """The part's two long edges, measured at the inside of the ink.

    `mask` is a boolean/0-1 array, non-zero on INK, holding one part
    (e.g. one label from connectedComponents). The part is assumed
    long in x: edges are found per column.

    Returns (cols, top, bot) as float arrays of equal length, where
    cols are the x of each measured column, `top` the y just inside
    the upper edge and `bot` the y just inside the lower. Columns
    where the two edges have merged - a knife tip, where no inside
    remains - are absent, so `cols` can have gaps and is not simply
    a range. Returns three empty arrays when nothing is measurable.

    Uses the OUTERMOST pair of ink runs, not the first gap; see the
    module docstring for why that distinction decides correctness.
    """
    m = np.asarray(mask) != 0
    if m.ndim != 2 or m.size == 0:
        return (np.empty(0), np.empty(0), np.empty(0))

    cols = []
    top = []
    bot = []
    for c in range(m.shape[1]):
        rows = np.where(m[:, c])[0]
        if len(rows) == 0:
            continue                      # column misses the part
        brk = np.where(np.diff(rows) > 1)[0]
        if len(brk) == 0:
            continue                      # one solid run: no inside left
        # The outermost gap: just below the topmost stroke and just
        # above the bottommost. Any gaps between them are notch
        # strokes or interior detail, never the part's own edges.
        cols.append(float(c))
        top.append(float(rows[brk[0]] + 1))
        bot.append(float(rows[brk[-1] + 1] - 1))
    return (np.array(cols), np.array(top), np.array(bot))


def edge_trend(y, window_px=None):
    """Where an edge would run if nothing had been cut into it.

    A rolling MEDIAN, not a mean or a fit. The edge either side of a
    notch is the real line, and a median over a window several times
    the notch width ignores the notch while still following the
    part's own curve and taper. A mean would be dragged down into
    every notch it spans; a straight fit cannot follow a curve.

    Returns an array the same length as `y`.
    """
    if window_px is None:
        window_px = DEFAULT_TREND_WINDOW_PX
    y = np.asarray(y, dtype=float)
    if y.size == 0:
        return np.empty(0)
    # The window need not be odd: pad = win // 2 with a win-length
    # slice stays centred either way, and forcing it odd was tried and
    # removed again because no input could be built where it changed
    # the result.
    win = int(window_px)
    if win < 1:
        win = 1
    if win > y.size:
        return np.full(y.shape, float(np.median(y)))
    pad = win // 2
    q = np.pad(y, pad, mode="edge")
    out = np.empty(y.size, dtype=float)
    for j in range(y.size):
        out[j] = np.median(q[j:j + win])
    return out


def find_notches(y, inward, trend=None, min_depth_px=None,
                 min_width_px=None, max_width_px=None):
    """Indices of the notches cut into ONE edge.

    `y` is that edge's position per column (from `trace_edge_pair`).
    `inward` is the direction, in y, that cutting into the part moves
    this edge: +1 for the top edge (a notch pushes it DOWN, towards
    larger y) and -1 for the bottom edge. Getting this wrong finds
    nothing rather than finding the wrong thing, because an edge is
    never displaced outward.

    `trend` is the no-notch baseline; computed with `edge_trend` when
    not given. Pass one in to share it with other callers.

    Returns a list of indices INTO `y`, one per notch, at the middle
    of each run. Convert to x with the `cols` from `trace_edge_pair`.

    Why per-edge, against this edge's own trend, rather than against
    the part's total depth: three earlier approaches failed on real
    keels, and each failure was a wrong model rather than a bad
    threshold.

      * A COLUMN SUM cannot say which edge was cut, and both edges
        of a keel carry notches.
      * A GLOBAL FULL DEPTH does not exist. These keels narrow along
        their length, so "full depth" is a different number at each
        end and a single threshold false-positives at the thin one.
      * EXCLUDING THE TAPERED ENDS is wrong too. The Pogo bottom
        keel has a notch a few tenths of an inch from its tip: the
        formers keep crossing the keel where the taper thins it, so
        of course a notch lands there.

    KNOWN LIMIT. A notch cut flush with the END of the part is not
    found. `edge_trend` pads with the edge value, so a displaced run
    reaching the last column fills the right half of its own baseline
    window and its offset collapses to zero. This is inherent to
    measuring against a local trend rather than a bug to fix: a step
    that never returns is indistinguishable from the part's edge
    genuinely stepping, which is what the median is built to follow.
    A keel normally ends in a tip or a cut face rather than mid-notch
    so it has not mattered, but do not read an empty result near an
    end as proof that nothing is cut there.

    A STEEP RAMP weakens it for the same reason, and that case DOES
    occur: a scarf-jointed keel carries a notch on the long diagonal
    running out to its knife tip, so that the two halves share one
    former slot across the glued joint. Two things are needed to see
    those, both learned the hard way on the Pogo keels:

      * A SHORTER TREND WINDOW. The default 151 px is wider than the
        whole taper, so the median follows the diagonal and the notch
        never departs from its own baseline. About 31 px works: still
        several times a notch width, but short enough to track the
        taper's curvature. (The diagonals are not straight, so fitting
        a line to them instead does not help - that was tried.)
      * A FINER `min_depth_px`. These are shallow, around 5 px against
        the 11 px median of a body notch, so the 4 px default rejects
        them. 2 px finds them. Checked on all six Pogo keel pieces:
        at 2 px the two rear halves each gain exactly one notch, the
        scarf one, and no piece gains anything else.

    `inward` IS PER EDGE AND PER REGION, not per piece. It says which
    way lies OUT of the part, and on a scarf face the part's body is
    on the opposite side from the body further along the same edge.
    A keel's bottom edge therefore wants inward=-1 along the body and
    inward=+1 on the scarf face. Passing one sign for a whole edge
    silently finds only half the notches, which is exactly how the
    scarf notches came to be missed.
    """
    if min_depth_px is None:
        min_depth_px = DEFAULT_MIN_DEPTH_PX
    if min_width_px is None:
        min_width_px = DEFAULT_MIN_WIDTH_PX
    if max_width_px is None:
        max_width_px = DEFAULT_MAX_WIDTH_PX

    y = np.asarray(y, dtype=float)
    if y.size == 0:
        return []
    base = edge_trend(y) if trend is None else np.asarray(trend, float)
    if base.size != y.size:
        raise ValueError("trend must be the same length as y")

    off = float(inward) * (y - base)
    out = []
    run = -1
    for j in range(off.size + 1):
        # One past the end closes a run that reaches the last column.
        deep = j < off.size and off[j] >= min_depth_px
        if deep:
            if run < 0:
                run = j
        elif run >= 0:
            if min_width_px <= j - run <= max_width_px:
                out.append((run + j) // 2)
            run = -1
    return out

