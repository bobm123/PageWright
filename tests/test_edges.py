"""Tests for core.edges -- require NumPy (skipped if unavailable).

The fixtures here draw ink the way a plan does: OUTLINES, a few
pixels thick, not filled blobs. That matters more than it sounds.
The bug this module exists to prevent only appears when a column
crosses four strokes of ink, which cannot happen in a filled shape -
a test built on filled rectangles would pass against the broken
implementation and prove nothing.
"""

import json
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import edges as ed  # noqa: E402


LINE = 3          # ink thickness, px: a 300 DPI plan line is 8-11


def _bar(h=120, w=600, top=40, bot=80, line=LINE):
    """A straight outlined bar: two horizontal strokes, open ends.

    Open ends are deliberate. A keel piece on a printwood sheet is
    drawn as two long edges that meet at the tips, and the interior
    is paper, not ink.
    """
    m = np.zeros((h, w), dtype=np.uint8)
    m[top:top + line, :] = 1
    m[bot - line + 1:bot + 1, :] = 1
    return m


def _notch(m, x, width, depth, edge, line=LINE):
    """Cut a notch into one edge of `_bar`: the edge steps inward.

    The outline itself is re-routed, which is what a notch in a plan
    actually looks like along the edge it is cut into - the stroke
    leaves the straight run, crosses the part, runs along the floor,
    and crosses back. The WALLS are near-vertical strokes, and they
    are where the trace can go wrong (see `_broken_wall`).
    """
    rows = np.where(m[:, x])[0]
    if edge == "top":
        y0, y1 = rows[0], rows[0] + depth
        m[y0:y1 + line, x:x + line] = 1                          # wall
        m[y0:y1 + line, x + width - line:x + width] = 1          # wall
        m[y1:y1 + line, x:x + width] = 1                         # floor
        m[y0:y0 + line, x + line:x + width - line] = 0           # old edge
    else:
        y1, y0 = rows[-1] - depth, rows[-1]
        m[y1 - line + 1:y0 + 1, x:x + line] = 1
        m[y1 - line + 1:y0 + 1, x + width - line:x + width] = 1
        m[y1 - line + 1:y1 + 1, x:x + width] = 1
        m[y0 - line + 1:y0 + 1, x + line:x + width - line] = 0
    return m


def _broken_wall(m, x, edge, line=LINE):
    """Thin a notch wall until it no longer touches the far stroke.

    This is the case the outermost-pair rule exists for, and it is
    taken from the scan, not invented. On the real top keel front the
    lower edge steps down at columns 244-246 and the wall's ink is
    broken by anti-aliasing:

        col 243  802 803 | 822 823
        col 246  802 803 | 824..828  830  832 833 834
        col 247  802 803 | 833 834

    At column 246 the wall arrives as several separated runs. Taking
    the FIRST gap there reads the lower edge at 823 - the level it
    had before the step - when the ink at that column actually
    reaches 834. The outermost pair reads 831, on the wall where it
    belongs. An 8 px error, and at every such wall along the part it
    is what makes a traced outline zigzag.
    """
    rows = np.where(m[:, x])[0]
    gaps = np.where(np.diff(rows) > 1)[0]
    if edge == "top":
        span = rows[:gaps[0] + 1] if len(gaps) else rows
    else:
        span = rows[gaps[-1] + 1:] if len(gaps) else rows
    # Punch single-pixel holes through the wall, as the scanner did.
    for k in range(1, len(span) - 1, 2):
        m[span[k], x] = 0
    return m


# --------------------------------------------------------------- tracing

def test_straight_bar_edges_are_flat_and_inside_the_ink():
    m = _bar(top=40, bot=80)
    cols, top, bot = ed.trace_edge_pair(m)
    assert len(cols) == m.shape[1]
    # Inside the ink: the top stroke occupies rows 40..42, so the
    # first paper row is 43; the bottom stroke is 78..80, so 77.
    assert np.all(top == 43.0)
    assert np.all(bot == 77.0)


def test_broken_wall_does_not_pull_the_edge_into_the_part():
    """The outermost-pair fix, stated as a measurement.

    A wall whose ink the scanner broke arrives as several separated
    runs. Taking the first gap reads the edge at the level it had
    BEFORE the step, far inside the part; the outermost pair reads it
    on the wall. See `_broken_wall` for the scanned original.
    """
    m = _notch(_bar(top=40, bot=80), x=300, width=60, depth=10, edge="bot")
    m = _broken_wall(m, x=300, edge="bot")
    cols, _top, bot = ed.trace_edge_pair(m)
    wall = list(cols).index(300.0)

    rows = np.where(m[:, 300])[0]
    brk = np.where(np.diff(rows) > 1)[0]
    naive = float(rows[brk[0] + 1] - 1)    # what the first gap would give

    assert bot[wall] > naive + 5.0, (
        "first-gap tracing reads the lower edge %.0f px inside the part"
        % (bot[wall] - naive))
    # The bar's lower edge is at 80, so just inside it is 79.
    assert bot[wall] == 79.0


def test_multi_run_column_really_occurs_in_the_fixture():
    """Guards the FIXTURE, not the code.

    If this fails the wall is not being broken, and the test above
    would be passing against the broken implementation too.
    """
    m = _notch(_bar(), x=300, width=60, depth=10, edge="bot")
    m = _broken_wall(m, x=300, edge="bot")
    rows = np.where(m[:, 300])[0]
    runs = 1 + int(np.sum(np.diff(rows) > 1))
    assert runs >= 4, "a broken wall column should cross many strokes"


def test_notch_displaces_only_the_edge_it_was_cut_into():
    m = _notch(_bar(top=40, bot=80), x=300, width=20, depth=10, edge="top")
    cols, top, bot = ed.trace_edge_pair(m)
    mid = list(cols).index(310.0)
    plain = list(cols).index(100.0)
    assert top[mid] > top[plain], "a top notch pushes the top edge down"
    assert bot[mid] == bot[plain] == 77.0, "the far edge must not move"


def test_merged_edges_are_dropped_not_guessed():
    """A knife tip has no inside, and must not be invented."""
    m = _bar(w=200, top=40, bot=80)
    m[40:81, 150:] = 1                      # solid: edges merged
    cols, _top, _bot = ed.trace_edge_pair(m)
    assert len(cols) > 0
    assert max(cols) < 150.0


def test_empty_and_blank_masks_give_empty_results():
    for m in (np.zeros((0, 0), dtype=np.uint8), np.zeros((50, 50), np.uint8)):
        cols, top, bot = ed.trace_edge_pair(m)
        assert len(cols) == len(top) == len(bot) == 0


# ----------------------------------------------------------------- trend

def test_trend_ignores_a_notch_but_follows_a_taper():
    y = np.full(600, 50.0)
    y[300:320] = 60.0                       # a notch
    t = ed.edge_trend(y, window_px=151)
    assert np.allclose(t, 50.0), "a notch must not move the baseline"

    ramp = np.linspace(40.0, 80.0, 600)     # a taper
    tr = ed.edge_trend(ramp, window_px=151)
    # The baseline must track the ramp, not flatten it.
    assert tr[500] - tr[100] > 20.0


def test_trend_window_wider_than_the_data_is_the_median():
    y = np.array([1.0, 2.0, 3.0, 10.0, 11.0])
    t = ed.edge_trend(y, window_px=999)
    assert np.allclose(t, 3.0)


# --------------------------------------------------------------- notches

def test_finds_notches_on_the_edge_they_were_cut_into():
    m = _bar(top=40, bot=80)
    _notch(m, x=150, width=20, depth=10, edge="top")
    _notch(m, x=400, width=20, depth=10, edge="bot")
    cols, top, bot = ed.trace_edge_pair(m)

    t = ed.find_notches(top, inward=+1)
    b = ed.find_notches(bot, inward=-1)
    assert len(t) == 1 and len(b) == 1
    assert abs(cols[t[0]] - 160.0) < 12.0   # middle of the 150..170 notch
    assert abs(cols[b[0]] - 410.0) < 12.0


def test_wrong_inward_direction_finds_nothing():
    """An edge is never displaced outward, so the sign is checkable."""
    m = _notch(_bar(), x=300, width=20, depth=10, edge="top")
    _cols, top, _bot = ed.trace_edge_pair(m)
    assert len(ed.find_notches(top, inward=+1)) == 1
    assert ed.find_notches(top, inward=-1) == []


def test_a_notch_in_the_taper_is_still_found():
    """The Pogo bottom keel has one a few tenths from its tip."""
    h, w = 140, 600
    m = np.zeros((h, w), dtype=np.uint8)
    for x in range(w):
        # Taper: the bar closes from 40 px deep to 8 px at the right.
        half = 20.0 - 12.0 * (x / float(w - 1))
        t = int(round(60.0 - half))
        b = int(round(60.0 + half))
        m[t:t + LINE, x] = 1
        m[b - LINE + 1:b + 1, x] = 1
    _notch(m, x=520, width=16, depth=8, edge="top")   # out in the thin end

    cols, top, _bot = ed.trace_edge_pair(m)
    found = ed.find_notches(top, inward=+1)
    assert len(found) == 1, "a global depth threshold fails here"
    assert abs(cols[found[0]] - 528.0) < 14.0


def test_a_taper_alone_is_not_a_notch():
    """The other half of the previous test: no false positive."""
    h, w = 140, 600
    m = np.zeros((h, w), dtype=np.uint8)
    for x in range(w):
        half = 20.0 - 12.0 * (x / float(w - 1))
        t = int(round(60.0 - half))
        b = int(round(60.0 + half))
        m[t:t + LINE, x] = 1
        m[b - LINE + 1:b + 1, x] = 1
    cols, top, bot = ed.trace_edge_pair(m)
    assert ed.find_notches(top, inward=+1) == []
    assert ed.find_notches(bot, inward=-1) == []


def test_a_shallow_excursion_is_rejected():
    """Threshold noise on a scanned line is 1-2 px; a notch is 6.3."""
    m = _notch(_bar(top=40, bot=80), x=300, width=20, depth=1, edge="top")
    _cols, top, _bot = ed.trace_edge_pair(m)
    assert ed.find_notches(top, inward=+1) == []


def test_an_overwide_step_is_rejected_by_the_width_bound():
    """A long inward step is the part's shape, not a slot cut in it.

    The run length here matters and is not arbitrary. Below ~45
    columns the step is accepted; above ~80 the rolling median simply
    follows it and the offset collapses, so the width bound is never
    consulted. Between those the median still sees the step and the
    BOUND is the thing that rejects it - which is the only band where
    this test can testify about `max_width_px` at all. A first
    version used a 90-column notch and proved nothing: removing the
    bound entirely left it passing.
    """
    y = np.full(600, 50.0)
    y[300:360] = 60.0                       # 60 columns: inside the band
    assert ed.find_notches(y, inward=+1) == []
    # Same data with the bound lifted is found, so the bound is what did it.
    assert len(ed.find_notches(y, inward=+1, max_width_px=10 ** 9)) == 1


def test_a_notch_at_the_width_bound_is_still_accepted():
    """The other side of the bound, so it cannot drift silently."""
    y = np.full(600, 50.0)
    y[300:340] = 60.0                       # 40 columns: within the bound
    assert len(ed.find_notches(y, inward=+1)) == 1


def test_a_run_reaching_the_last_column_is_still_closed():
    """A notch must not be dropped for lack of a falling edge.

    Exercised against a supplied trend, because `edge_trend` cannot
    produce this input - see the test below for why.
    """
    y = np.concatenate([np.zeros(300), np.full(10, 9.0)])
    found = ed.find_notches(y, inward=+1, trend=np.zeros(310))
    assert len(found) == 1
    assert 300 <= found[0] <= 310


def test_a_notch_flush_with_the_end_is_not_found_and_does_not_pretend():
    """A real limit of trend-relative detection, recorded deliberately.

    `edge_trend` pads with the edge value, so a displaced run that
    reaches the last column fills the right half of its own baseline
    window and the offset collapses to zero. Such a notch is simply
    not found - and cannot be, by this method: a step that never
    returns is indistinguishable from the part's edge genuinely
    stepping, which the median is built to follow.

    Measured: with the default 151 px window, a tail run is missed at
    every length from 5 to 80 columns. In practice a keel ends in a
    tip or a cut face rather than mid-notch, so this has not bitten;
    the test exists so nobody reads a zero count as proof there is no
    notch there.
    """
    m = _bar(w=400, top=40, bot=80)
    m[40:43, 380:] = 0                      # old edge gone
    m[50:53, 380:] = 1                      # floor runs off the end
    m[40:53, 380:383] = 1                   # the single wall
    _cols, top, _bot = ed.trace_edge_pair(m)
    assert top[-1] == 53.0, "the edge really is displaced at the end"
    assert ed.find_notches(top, inward=+1) == []


def test_mismatched_trend_length_is_an_error_not_a_silent_wrong_answer():
    with pytest.raises(ValueError):
        ed.find_notches(np.zeros(100), inward=+1, trend=np.zeros(50))


# ------------------------------------------------------- the real drawing

def _keel_masks():
    """The six Pogo keel pieces, vendored from the scan.

    Synthetic bars prove the rules; they cannot prove the rules are
    the right ones for a scanned plan. These are the real traced
    pieces, run-length encoded per column so the fixture is exact
    rather than a re-threshold of a lossy image, and so the test is
    hermetic - it does not need the 40 MB scan on disk.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "pogo_keels.json")
    with open(path) as fh:
        raw = json.load(fh)
    out = {}
    for name, rec in raw.items():
        m = np.zeros((rec["h"], rec["w"]), dtype=np.uint8)
        for c, runs in enumerate(rec["cols"]):
            for start, length in runs:
                m[start:start + length, c] = 1
        out[name] = m
    return out


# Which edge each piece is notched on, and how many notches it
# carries. Measured off the drawing, and the asymmetry is the point:
# the top keel is notched on its LOWER edge while the other two are
# notched on top, which is why a method that only looks at total
# depth per column cannot work here.
KEEL_NOTCHES = {
    "BottomKeel-Front": ("top", 6),
    "BottomKeel-Rear": ("top", 3),
    "SideKeel-Front": ("top", 6),
    "SideKeel-Rear": ("top", 3),
    "TopKeel-Front": ("bot", 6),
    "TopKeel-Rear": ("bot", 3),
}


def test_real_keels_give_the_notch_counts_the_drawing_shows():
    for name, mask in _keel_masks().items():
        edge, count = KEEL_NOTCHES[name]
        cols, top, bot = ed.trace_edge_pair(mask)
        assert len(cols) > 1000, "%s: lost the body of the piece" % name
        t = ed.find_notches(top, inward=+1)
        b = ed.find_notches(bot, inward=-1)
        got = {"top": (len(t), len(b)), "bot": (len(b), len(t))}[edge]
        assert got == (count, 0), (
            "%s: expected %d notches on the %s edge and none on the "
            "other, got %d and %d" % (name, count, edge, got[0], got[1]))


def test_front_and_rear_body_notches_total_nine_per_keel():
    """Nine notches on the BODY edges, six front and three rear.

    Ten formers, nine body notches. An earlier version of this test
    read that as proof the halves butt rather than lap, on the
    grounds that no station is shared. That inference was wrong: the
    shared station is not on a body edge at all but on the scarf
    face, where the default settings cannot see it (see the next
    test). The count itself still holds and is worth guarding.
    """
    masks = _keel_masks()
    for keel in ("BottomKeel", "SideKeel", "TopKeel"):
        total = 0
        for half in ("Front", "Rear"):
            name = "%s-%s" % (keel, half)
            edge, _n = KEEL_NOTCHES[name]
            _cols, top, bot = ed.trace_edge_pair(masks[name])
            y, inward = (top, +1) if edge == "top" else (bot, -1)
            total += len(ed.find_notches(y, inward=inward))
        assert total == 9, "%s: %d notches for ten formers" % (keel, total)


# The scarf notch on each rear half: distance from that piece's knife
# tip, in columns, measured off the drawing. The front halves carry
# the matching notch on their OTHER edge, which this fixture's
# per-piece `inward` does not reach; see the module docstring.
REAR_SCARF_NOTCH_PX = {
    "BottomKeel-Rear": 83,
    "SideKeel-Rear": 97,
}


def test_the_scarf_notch_needs_a_short_window_and_a_fine_threshold():
    """The notch that carries a former across a scarf joint.

    A split keel's two halves overlap along a long tapered face, and
    a former slot is cut across that face so the joined keel has a
    notch there like any other station. It is shallow (about 5 px
    against a body notch's 11) and it sits on a steep ramp, so the
    defaults miss it twice over: `edge_trend`'s 151 px window is
    wider than the whole taper and follows it, and `min_depth_px` of
    4 rejects it on depth alone.

    Both corrections are needed. This test fails if either default
    silently starts working, which would mean the documented reason
    for passing them explicitly no longer holds.
    """
    masks = _keel_masks()
    for name, want_px in REAR_SCARF_NOTCH_PX.items():
        cols, top, _bot = ed.trace_edge_pair(masks[name])
        tip = cols.min()

        coarse = ed.find_notches(top, inward=+1)
        fine = ed.find_notches(top, inward=+1,
                               trend=ed.edge_trend(top, 31),
                               min_depth_px=2.0)
        assert len(fine) == len(coarse) + 1, (
            "%s: expected exactly one extra notch with the short "
            "window and fine threshold, got %d then %d"
            % (name, len(coarse), len(fine)))

        extra = sorted(set(cols[i] for i in fine)
                       - set(cols[i] for i in coarse))
        assert len(extra) == 1, "%s: %d extra notches" % (name, len(extra))
        got = extra[0] - tip
        assert abs(got - want_px) <= 12, (
            "%s: scarf notch at %d px from the tip, expected about %d"
            % (name, got, want_px))


def test_the_fine_threshold_adds_nothing_to_the_front_halves():
    """The extra notch is a real feature, not threshold noise.

    If dropping to 2 px merely let scan noise through, every piece
    would gain notches. Only the two scarfed rear halves do.
    """
    masks = _keel_masks()
    for name in ("BottomKeel-Front", "SideKeel-Front", "TopKeel-Front",
                 "TopKeel-Rear"):
        edge, _n = KEEL_NOTCHES[name]
        _cols, top, bot = ed.trace_edge_pair(masks[name])
        y, inward = (top, +1) if edge == "top" else (bot, -1)
        coarse = ed.find_notches(y, inward=inward)
        fine = ed.find_notches(y, inward=inward,
                               trend=ed.edge_trend(y, 31),
                               min_depth_px=2.0)
        assert len(fine) == len(coarse), (
            "%s: the fine threshold changed the count on a body edge, "
            "%d -> %d" % (name, len(coarse), len(fine)))


def test_a_merged_knife_tip_really_occurs_in_the_real_data():
    """Guards the claim in the module docstring.

    The bottom and side keels taper to a point, so their last columns
    have no inside left and must be absent from the trace. If this
    fails, the "no enclosed interior" reasoning is about nothing.
    """
    masks = _keel_masks()
    m = masks["BottomKeel-Front"]
    cols, _top, _bot = ed.trace_edge_pair(m)
    assert len(cols) < m.shape[1], "every column measured: no tip present"


def test_the_unnotched_edge_of_a_real_keel_is_smooth():
    """The zigzag, caught on the drawing rather than on a fixture.

    The bottom and side keels are notched on their TOP edge only, so
    their lower edge is a plain sweeping curve and must trace smooth.
    First-gap tracing spikes it wherever a wall's ink is broken:
    measured on this data, 15 columns on the bottom keel front, 17 on
    its rear and 33 on the side keel front jump by more than 5 px,
    against 0, 1 and 0 when traced correctly.

    The top keel is excluded on purpose, not for convenience: it is
    notched on its LOWER edge, so that edge is full of real steps and
    cannot witness anything. An earlier version of this test used
    "whichever edge is not notched" for all six pieces and failed on
    the correct code, because the top keel's upper edge carries the
    blunt end faces and is not quiet either.
    """
    masks = _keel_masks()
    for name in ("BottomKeel-Front", "BottomKeel-Rear",
                 "SideKeel-Front", "SideKeel-Rear"):
        assert KEEL_NOTCHES[name][0] == "top", "%s must be top-notched" % name
        _cols, _top, bot = ed.trace_edge_pair(masks[name])
        jumps = int(np.sum(np.abs(np.diff(bot)) > 5.0))
        assert jumps <= 1, (
            "%s: the un-notched lower edge jumps at %d columns, which "
            "is the first-gap zigzag" % (name, jumps))
