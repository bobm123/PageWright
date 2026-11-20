"""Finding the drawn parts on a sheet with no seed marks (core).

The claim under test: a part is a closed loop of ink, so flooding the
paper inward from the border leaves the part interiors unreachable.
These tests pin both the capability and its documented limits - a part
whose outline is broken past the bridging distance is NOT found, and
the caller must be told that rather than left guessing.
"""

import os
import sys

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import find_parts as fp        # noqa: E402

MMPP = 0.0846667          # a 300 DPI plan


def _mm(v):
    return int(round(v / MMPP))


def _sheet(w_mm=200.0, h_mm=150.0):
    return np.full((_mm(h_mm), _mm(w_mm), 3), 255, np.uint8)


def _box(sheet, x_mm, y_mm, w_mm, h_mm, thickness=3, gap_mm=0.0):
    """Draw a rectangular outline, optionally with a break in its top."""
    x, y = _mm(x_mm), _mm(y_mm)
    w, h = _mm(w_mm), _mm(h_mm)
    cv2.rectangle(sheet, (x, y), (x + w, y + h), (0, 0, 0), thickness)
    if gap_mm > 0.0:
        g = _mm(gap_mm)
        cv2.line(sheet, (x + w // 2 - g // 2, y),
                 (x + w // 2 + g // 2, y), (255, 255, 255),
                 thickness + 2)
    return sheet


# ----- the basic claim ----------------------------------------------------

def test_a_closed_outline_is_found():
    sheet = _box(_sheet(), 20, 20, 60, 40)
    found = fp.find_parts(sheet, MMPP)
    assert len(found) == 1
    c = found[0]
    assert c.area_mm2 == pytest.approx(60 * 40, rel=0.1)
    assert c.size_mm[0] == pytest.approx(60, abs=1.5)
    assert c.size_mm[1] == pytest.approx(40, abs=1.5)


def test_several_parts_are_found_and_ranked_by_size():
    sheet = _sheet()
    _box(sheet, 10, 10, 70, 50)
    _box(sheet, 110, 10, 40, 30)
    _box(sheet, 10, 80, 50, 40)
    found = fp.find_parts(sheet, MMPP)
    assert len(found) == 3
    areas = [c.area_mm2 for c in found]
    assert areas == sorted(areas, reverse=True)


def test_an_empty_sheet_finds_nothing():
    assert fp.find_parts(_sheet(), MMPP) == []


# ----- the gap problem, which is the whole reason this is hard ------------

def test_a_hairline_break_is_bridged():
    # A scanned outline is rarely watertight; one break lets the whole
    # interior leak out to the page.
    sheet = _box(_sheet(), 20, 20, 60, 40, gap_mm=0.4)
    assert fp.find_parts(sheet, MMPP, gap_mm=0.0) == []
    assert len(fp.find_parts(sheet, MMPP, gap_mm=1.2)) == 1


def test_a_break_wider_than_the_bridge_is_not_found():
    # The documented limit: a part cut by a dimension line stays open.
    sheet = _box(_sheet(), 20, 20, 60, 40, gap_mm=4.0)
    assert fp.find_parts(sheet, MMPP, gap_mm=0.6) == []


# ----- telling parts from the rest of the drawing -------------------------

def test_a_long_thin_box_is_rejected_as_a_rule_or_text_frame():
    sheet = _box(_sheet(), 10, 10, 160, 8)      # aspect 20:1
    assert fp.find_parts(sheet, MMPP) == []
    # ...but the caller can ask for it anyway
    assert len(fp.find_parts(sheet, MMPP, max_aspect=0)) == 1


def test_a_tiny_region_is_below_the_area_floor():
    sheet = _box(_sheet(), 20, 20, 8, 8)        # 64 mm2
    assert fp.find_parts(sheet, MMPP) == []
    assert len(fp.find_parts(sheet, MMPP, min_area_mm2=10.0)) == 1


def test_the_fill_measure_separates_solid_shapes_from_sparse_ones():
    # A part is a solid blob inside its bounding box. A bracket or an
    # L-shape encloses far less of its own box, which is what `fill`
    # measures and what the min_fill bar is for.
    solid = _box(_sheet(), 20, 20, 80, 60)
    c = fp.find_parts(solid, MMPP)[0]
    assert c.fill > 0.85

    # a C-shape: same bounding box, much less enclosed
    sparse = _sheet()
    x, y, w, h = _mm(20), _mm(20), _mm(80), _mm(60)
    cv2.rectangle(sparse, (x, y), (x + w, y + h), (0, 0, 0), 3)
    cv2.rectangle(sparse, (x + w // 3, y + _mm(5)),
                  (x + w, y + h - _mm(5)), (255, 255, 255), -1)
    cv2.rectangle(sparse, (x + w // 3, y + _mm(5)),
                  (x + w, y + h - _mm(5)), (0, 0, 0), 3)
    found = fp.find_parts(sparse, MMPP, min_fill=0.0)
    assert found, "the C-shape should still be found"
    assert found[0].fill < c.fill


def test_limit_returns_the_largest_n():
    sheet = _sheet()
    _box(sheet, 10, 10, 70, 50)
    _box(sheet, 110, 10, 40, 30)
    _box(sheet, 10, 80, 50, 40)
    found = fp.find_parts(sheet, MMPP, limit=2)
    assert len(found) == 2
    assert found[0].area_mm2 >= found[1].area_mm2


# ----- tracing ------------------------------------------------------------

def test_trace_returns_a_usable_polygon():
    sheet = _box(_sheet(), 20, 20, 60, 40)
    c = fp.find_parts(sheet, MMPP, trace=True)[0]
    assert c.outline is not None
    assert len(c.outline) >= 4
    # it sits where the box was drawn
    xs = [p[0] for p in c.outline]
    ys = [p[1] for p in c.outline]
    assert min(xs) == pytest.approx(_mm(20), abs=_mm(2))
    assert min(ys) == pytest.approx(_mm(20), abs=_mm(2))


def test_outline_size_is_the_number_to_trust():
    # The ink bounding box is the OUTER edge of the drawn line; the
    # enclosed region stops at its inside, which is where a cut part
    # actually ends. On the sample former that was 0.006 in from the
    # known dimension against 0.047 in for the bounding box.
    sheet = _box(_sheet(), 20, 20, 60, 40, thickness=7)
    c = fp.find_parts(sheet, MMPP, trace=True)[0]
    assert c.outline_size_mm is not None
    assert c.outline_size_mm[0] < c.size_mm[0]
    assert c.outline_size_mm[1] < c.size_mm[1]


def test_outline_size_is_none_without_tracing():
    c = fp.find_parts(_box(_sheet(), 20, 20, 60, 40), MMPP)[0]
    assert c.outline is None
    assert c.outline_size_mm is None


# ----- contract -----------------------------------------------------------

def test_an_uncalibrated_sheet_is_refused():
    # Every threshold here is a real-world size, so a bare pixel count
    # would mean something different at every scan resolution.
    for bad in (None, 0.0, -1.0):
        with pytest.raises(ValueError):
            fp.find_parts(_sheet(), bad)


def test_grayscale_input_works_too():
    sheet = cv2.cvtColor(_box(_sheet(), 20, 20, 60, 40),
                         cv2.COLOR_BGR2GRAY)
    assert len(fp.find_parts(sheet, MMPP)) == 1


def test_candidates_do_not_hand_back_their_masks():
    # The search keeps a per-candidate mask while it works; handing a
    # few dozen full-size arrays back to the caller would be careless.
    c = fp.find_parts(_box(_sheet(), 20, 20, 60, 40), MMPP, trace=True)[0]
    assert not hasattr(c, "_enclosed")


# ----- closing along contours ---------------------------------------------
#
# The case these cover: two parts nested closer than their own
# outlines are broken. A square close cannot serve both - the kernel
# that spans the break also spans the lane - so the bridge has to
# know which way the line runs.

def _nested_arcs(gap_px=19, lane_px=11, thickness=3):
    """Two nested rings, each broken along one side.

    The numbers are the ones measured on a real printwood sheet: the
    BREAK (19 px) is wider than the LANE between the parts (11 px),
    which is what makes a square close impossible - the kernel that
    seals one ring has already welded it to its neighbour.

    Drawn as RECTANGULAR rings rather than arcs. The property under
    test is about the DIRECTION of a break against the direction of
    the lane, not about curvature, and a rectangle keeps the lane a
    constant width - concentric ellipses pinch together at their
    ends and quietly merge, which made an earlier version of this
    fixture one ink component and the test vacuous.
    """
    h, w = 420, 360
    sheet = np.full((h, w, 3), 255, np.uint8)
    ring = 26
    for i in range(2):
        pad = 30 + i * (ring + lane_px)
        x0, y0 = 30 + i * (ring + lane_px), pad
        x1, y1 = w - 40 - i * (ring + lane_px), h - pad
        cv2.rectangle(sheet, (x0, y0), (x1, y1), (0, 0, 0), thickness)
        cv2.rectangle(sheet, (x0 + ring, y0 + ring),
                      (x1 - ring, y1 - ring), (0, 0, 0), thickness)
        # the BREAK: in the inner rectangle's LEFT side, which runs
        # vertically, so the gap lies ALONG the line. A square close
        # bridging it must reach `gap_px` sideways too - into the
        # lane, which is narrower.
        by = (y0 + y1) // 2 - gap_px // 2
        cv2.line(sheet, (x0 + ring, by), (x0 + ring, by + gap_px),
                 (255, 255, 255), thickness + 4)
    return sheet


def test_a_square_close_cannot_bridge_a_break_wider_than_the_lane():
    """The motivating failure, pinned so the fix is not cosmetic.

    With the break wider than the gap between parts, any square
    kernel big enough to seal one arc has already welded it to its
    neighbour - so the two arcs come back as fewer than two parts."""
    sheet = _nested_arcs()
    mmpp = 0.126620
    square = fp.find_parts(sheet, mmpp, gap_mm=19 * mmpp,
                           min_fill=0.10, max_aspect=12.0,
                           min_area_mm2=50.0)
    assert len(square) < 2, (
        "the square close was expected to weld or miss the arcs, "
        "got %d parts" % len(square))


def test_closing_along_contours_keeps_nested_parts_apart():
    sheet = _nested_arcs()
    mmpp = 0.126620
    found = fp.find_parts(sheet, mmpp, gap_mm=19 * mmpp,
                          min_fill=0.10, max_aspect=12.0,
                          min_area_mm2=50.0, along_contours=True)
    assert len(found) >= 2, (
        "closing along contours should find both arcs, got %d"
        % len(found))


def test_the_directional_close_does_not_bridge_sideways():
    """The property that makes it work: a vertical line is extended
    up and down, never left and right - otherwise it is just a
    square close wearing a different name."""
    mask = np.zeros((120, 120), np.uint8)
    mask[20:50, 60] = 1                 # a vertical stub
    mask[70:100, 60] = 1                # and another, below a break
    out = fp.close_along_contours(mask, 25)
    assert out[50:70, 60].any(), "the break along the line was not bridged"
    # nothing should have reached a neighbour 11 px to the side
    assert not out[20:100, 71:].any(), "it bridged sideways"


def test_closing_along_contours_is_off_by_default():
    """The square close stays the default: it is right for an
    ordinary sheet and cheaper, and this must not change what
    existing callers get."""
    sheet = _box(_sheet(), 20, 20, 60, 40)
    a = fp.find_parts(sheet, MMPP)
    b = fp.find_parts(sheet, MMPP, along_contours=False)
    assert len(a) == len(b) == 1
