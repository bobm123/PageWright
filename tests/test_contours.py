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
