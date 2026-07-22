"""Tests for core.geometry -- pure math, no Qt or OpenCV required."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import geometry as geo  # noqa: E402


def test_bbox_of_points():
    b = geo.bbox_of_points([(1, 2), (5, 1), (3, 7), (-2, 4)])
    assert (b.min_x, b.min_y, b.max_x, b.max_y) == (-2, 1, 5, 7)
    assert b.width == 7
    assert b.height == 6


def test_bbox_of_points_empty_raises():
    with pytest.raises(ValueError):
        geo.bbox_of_points([])


def test_bbox_expanded():
    b = geo.BBox(0, 0, 10, 10).expanded(2)
    assert (b.min_x, b.min_y, b.max_x, b.max_y) == (-2, -2, 12, 12)


def test_union_bbox():
    a = geo.BBox(0, 0, 4, 4)
    b = geo.BBox(2, -1, 6, 3)
    u = geo.union_bbox([a, b])
    assert (u.min_x, u.min_y, u.max_x, u.max_y) == (0, -1, 6, 4)


def test_union_bbox_empty_raises():
    with pytest.raises(ValueError):
        geo.union_bbox([])


def test_px_mm_round_trip():
    mpp = 0.25
    assert geo.px_to_mm(40, mpp) == pytest.approx(10.0)
    assert geo.mm_to_px(10.0, mpp) == pytest.approx(40.0)


def test_mm_to_px_uncalibrated_raises():
    with pytest.raises(ValueError):
        geo.mm_to_px(10.0, 0.0)


def test_point_px_to_mm_with_origin():
    pt = geo.point_px_to_mm((30, 50), 0.5, origin_px=(10, 10))
    assert pt == pytest.approx((10.0, 20.0))
