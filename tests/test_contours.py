"""Tests for core.contours -- require NumPy + OpenCV (skipped if unavailable)."""

import os
import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import contours as cont  # noqa: E402


def _square_mask(size=120, lo=20, hi=100):
    m = np.zeros((size, size), dtype=np.uint8)
    m[lo:hi, lo:hi] = 1
    return m


def _square_with_hole(size=160, lo=20, hi=140, hlo=60, hhi=100):
    m = np.zeros((size, size), dtype=np.uint8)
    m[lo:hi, lo:hi] = 1
    m[hlo:hhi, hlo:hhi] = 0   # interior hole
    return m


def test_single_square_is_one_outer_contour():
    results = cont.extract_contours(_square_mask(), epsilon_px=1.0)
    assert len(results) == 1
    points, role = results[0]
    assert role == cont.ROLE_OUTER
    # A simplified axis-aligned square should reduce to ~4 corners.
    assert 4 <= len(points) <= 6


def test_square_with_hole_yields_outer_and_hole():
    results = cont.extract_contours(_square_with_hole(), epsilon_px=1.0)
    roles = sorted(role for _, role in results)
    assert cont.ROLE_OUTER in roles
    assert cont.ROLE_HOLE in roles
    assert roles.count(cont.ROLE_HOLE) == 1


def test_tiny_speckle_is_filtered():
    m = np.zeros((100, 100), dtype=np.uint8)
    m[10:13, 10:13] = 1   # 3x3 = area 9, below default min_area 25
    results = cont.extract_contours(m)
    assert results == []


def test_empty_mask_returns_nothing():
    m = np.zeros((50, 50), dtype=np.uint8)
    assert cont.extract_contours(m) == []


# ----- real-world tolerance (mm, not pixels) ------------------------------

def test_tolerance_mm_to_px_converts_and_falls_back():
    # 0.35 mm at 0.0847 mm/px (a 300 DPI plan) is ~4.1 px
    assert cont.tolerance_mm_to_px(0.35, 0.08466667) == pytest.approx(
        4.134, abs=0.01)
    # finer scan -> more pixels for the same real tolerance
    assert cont.tolerance_mm_to_px(0.35, 0.0423) > cont.tolerance_mm_to_px(
        0.35, 0.0847)
    # uncalibrated: fall back rather than refuse to trace
    assert cont.tolerance_mm_to_px(0.35, None) == 2.0
    assert cont.tolerance_mm_to_px(0.35, 0.0) == 2.0
    assert cont.tolerance_mm_to_px(0.35, -1.0, fallback_px=3.0) == 3.0


def test_same_real_tolerance_gives_the_same_shape_at_any_scale():
    # The point of mm tolerance: a drawing scanned at twice the
    # resolution must simplify to the same polygon, not twice the
    # vertices (the old pixel rule did exactly that).
    import cv2
    small = _square_with_hole(size=160)
    big = cv2.resize(small, (320, 320), interpolation=cv2.INTER_NEAREST)
    mpp_small, mpp_big = 0.2, 0.1          # big is 2x the resolution
    a = cont.extract_contours(
        small, epsilon_px=cont.tolerance_mm_to_px(0.5, mpp_small))
    b = cont.extract_contours(
        big, epsilon_px=cont.tolerance_mm_to_px(0.5, mpp_big))
    assert len(a) == len(b)
    assert [len(p) for p, _ in a] == [len(p) for p, _ in b]


# ----- speckle rejection that scales with the traced shape ----------------

def _shape_with_debris():
    """A big square plus three small unrelated blobs, as a trace area
    on a drawing sheet picks up bits of neighbouring parts."""
    m = np.zeros((300, 300), dtype=np.uint8)
    m[50:250, 50:250] = 1            # the real part, area 40000
    m[5:20, 5:20] = 1                # debris, area 225
    m[280:295, 10:25] = 1            # debris
    m[10:28, 275:293] = 1            # debris
    return m


def test_absolute_floor_alone_keeps_the_debris():
    # Documents WHY a relative floor was needed: these blobs are far
    # above any sane absolute threshold yet are obviously not the part.
    results = cont.extract_contours(_shape_with_debris(), epsilon_px=1.0,
                                    min_area_px=25.0)
    assert len(results) == 4


def test_relative_area_floor_drops_the_debris():
    results = cont.extract_contours(
        _shape_with_debris(), epsilon_px=1.0, min_area_px=25.0,
        min_area_frac=cont.MIN_AREA_FRACTION)
    assert len(results) == 1
    assert results[0][1] == cont.ROLE_OUTER


def test_keep_largest_keeps_one_shape_and_its_holes():
    m = _shape_with_debris()
    m[100:150, 100:150] = 0          # a real cutout in the real part
    results = cont.extract_contours(m, epsilon_px=1.0, min_area_px=25.0,
                                    keep_largest=True)
    roles = [role for _, role in results]
    assert roles.count(cont.ROLE_OUTER) == 1
    assert roles.count(cont.ROLE_HOLE) == 1
    assert len(results) == 2


def test_keep_largest_does_not_drop_a_small_real_hole():
    # A cutout can be far smaller than the part and still be wanted, so
    # the relative floor must not be applied to holes.
    m = np.zeros((300, 300), dtype=np.uint8)
    m[50:250, 50:250] = 1
    m[140:150, 140:150] = 0          # 100 px hole vs a 40000 px part
    results = cont.extract_contours(
        m, epsilon_px=1.0, min_area_px=25.0,
        min_area_frac=cont.MIN_AREA_FRACTION, keep_largest=True)
    roles = [role for _, role in results]
    assert roles.count(cont.ROLE_HOLE) == 1


def test_a_hole_still_follows_its_outer_contour():
    m = _square_with_hole()
    results = cont.extract_contours(m, epsilon_px=1.0, keep_largest=True)
    assert [role for _, role in results] == [cont.ROLE_OUTER, cont.ROLE_HOLE]


# ----- hole size fallback (used only when there are no marks) -------------

def _part_with_island(side_mm, mm_per_px=0.0846667, tall_mm=None):
    """A big part with one enclosed island of the given real size."""
    m = np.zeros((900, 900), dtype=np.uint8)
    m[100:800, 100:800] = 1
    bw = max(1, int(round(side_mm / mm_per_px)))
    bh = max(1, int(round((tall_mm if tall_mm else side_mm) / mm_per_px)))
    m[450 - bh // 2:450 + bh // 2, 450 - bw // 2:450 + bw // 2] = 0
    return m


def test_hole_min_px_converts_and_disables_when_uncalibrated():
    assert cont.hole_min_px(0.0846667) == pytest.approx(35.4, abs=0.2)
    assert cont.hole_min_px(0.0846667, 6.0) == pytest.approx(70.9, abs=0.2)
    # no scale -> no filtering, rather than guessing which holes are text
    assert cont.hole_min_px(None) == 0.0
    assert cont.hole_min_px(0.0) == 0.0
    # explicitly off
    assert cont.hole_min_px(0.0846667, 0.0) == 0.0


def test_size_fallback_drops_a_printed_label():
    # Without marks (a mask from outside the interactive tracer) the
    # size rule still applies. The "6" label measured 2.37 x 3.05 mm -
    # note the HEIGHT exceeds 3 mm, so the narrow side is what counts.
    mpp = 0.0846667
    m = _part_with_island(2.37, mpp, tall_mm=3.05)
    kept = cont.extract_contours(m, epsilon_px=2.0, keep_largest=True,
                                 min_hole_px=cont.hole_min_px(mpp))
    assert [role for _, role in kept] == [cont.ROLE_OUTER]
    # without the filter it comes through, which is the bug being fixed
    unfiltered = cont.extract_contours(m, epsilon_px=2.0, keep_largest=True)
    assert cont.ROLE_HOLE in [role for _, role in unfiltered]


def test_real_cutouts_are_kept():
    mpp = 0.0846667
    floor = cont.hole_min_px(mpp)
    for side_mm in (25.4 / 8, 25.4 * 3 / 16, 25.4 / 4, 25.4 / 2):
        m = _part_with_island(side_mm, mpp)
        kept = cont.extract_contours(m, epsilon_px=2.0, keep_largest=True,
                                     min_hole_px=floor)
        roles = [role for _, role in kept]
        assert cont.ROLE_HOLE in roles, "%.2f mm cutout was dropped" % side_mm


def test_hole_filter_off_keeps_thin_slots():
    # A 1/16 in wide slot is narrower than the default minimum, so the
    # control has to be able to turn the filter off.
    mpp = 0.0846667
    m = _part_with_island(25.4 / 16, mpp, tall_mm=8.0)
    with_filter = cont.extract_contours(
        m, epsilon_px=2.0, keep_largest=True,
        min_hole_px=cont.hole_min_px(mpp))
    assert cont.ROLE_HOLE not in [r for _, r in with_filter]
    without = cont.extract_contours(
        m, epsilon_px=2.0, keep_largest=True,
        min_hole_px=cont.hole_min_px(mpp, 0.0))
    assert cont.ROLE_HOLE in [r for _, r in without]


# ----- the marks decide which enclosed regions are holes ------------------

def _marked_part():
    """A part with a real cutout and a letter-sized island, plus the
    seed mask a user would produce: outside dabbed in the cutout only."""
    import cv2
    m = np.zeros((900, 900), dtype=np.uint8)
    m[100:800, 100:800] = 1
    cv2.circle(m, (300, 450), 60, 0, -1)             # real cutout
    cv2.rectangle(m, (590, 430), (618, 466), 0, -1)  # a label glyph
    seeds = np.zeros((900, 900), dtype=np.uint8)
    cv2.circle(seeds, (300, 450), 12, 1, -1)         # "outside" dab in it
    cv2.rectangle(seeds, (40, 40), (860, 60), 1, -1)  # outside the part
    return m, seeds


def test_a_hole_is_real_only_where_outside_was_marked():
    # The point of marking inside and outside: the boundary runs
    # between them. An enclosed region nobody called "outside" is not
    # a hole, whatever its size - which is how printed lettering
    # trapped inside the outline gets dropped with no size constant.
    mask, seeds = _marked_part()
    kept = cont.extract_contours(mask, epsilon_px=2.0, keep_largest=True,
                                 hole_seed_mask=seeds)
    roles = [role for _, role in kept]
    assert roles.count(cont.ROLE_HOLE) == 1          # the marked cutout
    assert roles.count(cont.ROLE_OUTER) == 1
    # and it is the big one, not the glyph
    import cv2
    hole = [p for p, r in kept if r == cont.ROLE_HOLE][0]
    _x, _y, bw, bh = cv2.boundingRect(np.array(hole, np.float32))
    assert min(bw, bh) > 100


def test_without_marks_both_regions_come_through():
    # Documents what the marks are actually buying.
    mask, _seeds = _marked_part()
    kept = cont.extract_contours(mask, epsilon_px=2.0, keep_largest=True)
    assert [r for _, r in kept].count(cont.ROLE_HOLE) == 2


def test_an_empty_seed_mask_rejects_every_hole():
    # Marking nothing outside is a statement too: no holes were claimed.
    mask, _ = _marked_part()
    empty = np.zeros(mask.shape, dtype=np.uint8)
    kept = cont.extract_contours(mask, epsilon_px=2.0, keep_largest=True,
                                 hole_seed_mask=empty)
    assert [r for _, r in kept] == [cont.ROLE_OUTER]


def test_marks_beat_size_in_both_directions():
    # A TINY marked region is a hole; a LARGE unmarked one is not.
    # Neither outcome is reachable with a size threshold.
    import cv2
    m = np.zeros((900, 900), dtype=np.uint8)
    m[100:800, 100:800] = 1
    cv2.rectangle(m, (280, 440), (300, 460), 0, -1)   # tiny, marked
    cv2.circle(m, (600, 450), 80, 0, -1)              # large, unmarked
    seeds = np.zeros((900, 900), dtype=np.uint8)
    cv2.rectangle(seeds, (285, 445), (295, 455), 1, -1)
    kept = cont.extract_contours(m, epsilon_px=1.0, keep_largest=True,
                                 hole_seed_mask=seeds)
    holes = [p for p, r in kept if r == cont.ROLE_HOLE]
    assert len(holes) == 1
    _x, _y, bw, bh = cv2.boundingRect(np.array(holes[0], np.float32))
    assert max(bw, bh) < 40, "kept the large unmarked region instead"
