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


def test_snap_angle_locks_to_horizontal_and_diagonal():
    from pagewright.core.geometry import snap_angle
    import math
    # 10 degrees off horizontal -> horizontal, length preserved
    p = snap_angle((0, 0), (10 * math.cos(0.17), 10 * math.sin(0.17)))
    assert abs(p[1]) < 1e-9 and abs(p[0] - 10.0) < 1e-9
    # near 45 degrees -> exactly 45
    p = snap_angle((0, 0), (10.0, 9.0))
    assert abs(p[0] - p[1]) < 1e-9
    # zero-length: unchanged, no division blowup
    assert snap_angle((5, 5), (5, 5)) == (5, 5)
    # direction preserved (points left stay left)
    p = snap_angle((0, 0), (-10.0, 0.5))
    assert p[0] < 0


# ----- aligning selected vertices -----------------------------------------

def test_aligning_vertically_gives_the_points_one_x():
    out = geo.align_points([(10.0, 5.0), (20.0, 50.0)], "vertical")
    assert out[0][0] == out[1][0]


def test_aligning_vertically_keeps_each_y():
    """Only the one coordinate moves: aligning is a correction, not a
    collapse, and a point that also jumped along the line would lose
    the spacing the trace recorded."""
    out = geo.align_points([(10.0, 5.0), (20.0, 50.0)], "vertical")
    assert out[0][1] == 5.0
    assert out[1][1] == 50.0


def test_aligning_horizontally_gives_the_points_one_y():
    out = geo.align_points([(10.0, 5.0), (20.0, 50.0)], "horizontal")
    assert out[0][1] == out[1][1] == pytest.approx(27.5)
    assert out[0][0] == 10.0 and out[1][0] == 20.0


def test_the_shared_value_is_the_mean_not_the_first_point():
    """Otherwise the result depends on which point was clicked first,
    and the same two vertices align differently on different days."""
    out = geo.align_points([(10.0, 0.0), (20.0, 0.0)], "vertical")
    assert out[0][0] == pytest.approx(15.0)


def test_aligning_is_order_independent():
    a = geo.align_points([(10.0, 0.0), (20.0, 8.0)], "vertical")
    b = geo.align_points([(20.0, 8.0), (10.0, 0.0)], "vertical")
    assert sorted(a) == sorted(b)


def test_more_than_two_points_align_too():
    out = geo.align_points(
        [(0.0, 1.0), (10.0, 2.0), (20.0, 3.0)], "vertical")
    assert all(p[0] == pytest.approx(10.0) for p in out)


def test_a_single_point_is_unchanged():
    assert geo.align_points([(3.0, 4.0)], "vertical") == [(3.0, 4.0)]


def test_an_unknown_axis_is_refused():
    with pytest.raises(ValueError):
        geo.align_points([(0.0, 0.0), (1.0, 1.0)], "diagonal")
