"""Tests for core.calibration -- pure math, no Qt or OpenCV required."""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import calibration as calib  # noqa: E402


def test_unit_conversions_round_trip():
    for unit in ("mm", "cm", "in"):
        assert calib.from_mm(calib.to_mm(7.0, unit), unit) == pytest.approx(7.0)


def test_known_unit_factors():
    assert calib.to_mm(1.0, "mm") == pytest.approx(1.0)
    assert calib.to_mm(1.0, "cm") == pytest.approx(10.0)
    assert calib.to_mm(1.0, "in") == pytest.approx(25.4)


def test_unknown_unit_raises():
    with pytest.raises(ValueError):
        calib.to_mm(1.0, "furlong")


def test_pixel_distance():
    assert calib.pixel_distance((0, 0), (3, 4)) == pytest.approx(5.0)
    assert calib.pixel_distance((1, 1), (1, 1)) == pytest.approx(0.0)


def test_mm_per_pixel_basic():
    # 100 px measured as 50 mm -> 0.5 mm/px.
    mpp = calib.mm_per_pixel((0, 0), (100, 0), 50.0, "mm")
    assert mpp == pytest.approx(0.5)


def test_mm_per_pixel_with_cm():
    # 200 px measured as 10 cm (=100 mm) -> 0.5 mm/px.
    mpp = calib.mm_per_pixel((0, 0), (0, 200), 10.0, "cm")
    assert mpp == pytest.approx(0.5)


def test_mm_per_pixel_diagonal_inch():
    # 3-4-5 -> 5 px measured as 1 in (25.4 mm) -> 5.08 mm/px.
    mpp = calib.mm_per_pixel((0, 0), (3, 4), 1.0, "in")
    assert mpp == pytest.approx(25.4 / 5.0)


def test_mm_per_pixel_zero_distance_raises():
    with pytest.raises(ValueError):
        calib.mm_per_pixel((5, 5), (5, 5), 10.0, "mm")


def test_mm_per_pixel_nonpositive_real_raises():
    with pytest.raises(ValueError):
        calib.mm_per_pixel((0, 0), (10, 0), 0.0, "mm")


def test_format_length():
    assert calib.format_length(25.4, "in", decimals=2) == "1.00 in"
    assert calib.format_length(100.0, "cm", decimals=1) == "10.0 cm"
