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
