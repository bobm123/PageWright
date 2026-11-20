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


# ----- squaring a run of points -------------------------------------------

def _seg_angles(pts):
    import math
    return [math.degrees(math.atan2(pts[i+1][1]-pts[i][1],
                                    pts[i+1][0]-pts[i][0])) % 90.0
            for i in range(len(pts)-1)]


def test_squaring_a_notch_makes_every_segment_axial():
    out = geo.square_up([(10.0, 0.0), (10.4, 8.0),
                         (16.0, 8.3), (16.2, 0.0)])
    for a in _seg_angles(out):
        assert min(a, 90.0 - a) == pytest.approx(0.0, abs=0.01)


def test_squaring_keeps_the_notch_depth():
    """The bug this replaces: the axis was chosen from the run END TO
    END, which for a notch cut into a wall is mostly sideways - so
    the floor was flattened onto the wall and the notch vanished."""
    out = geo.square_up([(10.0, 0.0), (10.4, 8.0),
                         (16.0, 8.3), (16.2, 0.0)])
    depth = abs((out[1][1] + out[2][1]) / 2.0
                - (out[0][1] + out[3][1]) / 2.0)
    assert depth == pytest.approx(8.15, abs=0.1)


def test_squaring_works_on_a_notch_in_a_horizontal_edge():
    """The same notch turned 90 degrees must behave the same way."""
    out = geo.square_up([(0.0, 10.0), (8.0, 10.4),
                         (8.3, 16.0), (0.0, 16.2)])
    depth = abs((out[1][0] + out[2][0]) / 2.0
                - (out[0][0] + out[3][0]) / 2.0)
    assert depth == pytest.approx(8.15, abs=0.1)


def test_the_ends_of_the_run_never_move():
    """The run is a piece of a longer contour: moving an end would
    break the segment joining it to the rest, which the caller
    cannot see."""
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.square_up(pts)
    assert out[0] == pytest.approx(pts[0])
    assert out[-1] == pytest.approx(pts[-1])


def test_three_points_square_one_corner():
    out = geo.square_up([(0.0, 0.0), (0.3, 9.0), (7.0, 9.2)])
    a = _seg_angles(out)
    assert min(a[0], 90.0 - a[0]) == pytest.approx(0.0, abs=0.01)
    assert min(a[1], 90.0 - a[1]) == pytest.approx(0.0, abs=0.01)


def test_squaring_uses_the_part_grid_not_the_image():
    """A drawing is rarely pasted down square - these traces sit 0.26
    degrees off - and squaring to the image axes would fight the part
    and undo what the notch cleaner did."""
    import math
    g = 10.0
    r = math.radians(g)
    c, s = math.cos(r), math.sin(r)
    square = [(0.0, 0.0), (0.0, 9.0), (7.0, 9.0), (7.0, 0.0)]
    tilted = [(x * c - y * s, x * s + y * c) for x, y in square]
    out = geo.square_up(tilted, grid_deg=g)
    # already square ON THE PART GRID, so nothing should move
    for a, b in zip(tilted, out):
        assert a[0] == pytest.approx(b[0], abs=0.01)
        assert a[1] == pytest.approx(b[1], abs=0.01)


def test_two_or_five_points_are_refused():
    with pytest.raises(ValueError):
        geo.square_up([(0.0, 0.0), (1.0, 1.0)])
    with pytest.raises(ValueError):
        geo.square_up([(0.0, 0.0)] * 5)


# ----- squaring that keeps the segment lengths ----------------------------

def _lengths(pts):
    import math
    return [math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1])
            for i in range(len(pts)-1)]


def test_keeping_lengths_changes_no_length():
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.square_keeping_lengths(pts)
    for a, b in zip(_lengths(pts), _lengths(out)):
        assert b == pytest.approx(a, abs=1e-9)


def _turns(pts):
    """The turn at each interior vertex, in degrees."""
    import math
    b = [math.degrees(math.atan2(pts[i+1][1]-pts[i][1],
                                 pts[i+1][0]-pts[i][0]))
         for i in range(len(pts)-1)]
    return [((b[i+1]-b[i]+180.0) % 360.0) - 180.0
            for i in range(len(b)-1)]


def test_keeping_lengths_makes_every_turn_a_right_angle():
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.square_keeping_lengths(pts)
    for t in _turns(out):
        assert abs(abs(t) - 90.0) == pytest.approx(0.0, abs=1e-6)


def test_keeping_lengths_does_not_snap_to_an_axis():
    """The run keeps the orientation it was traced at. A notch cut
    into a curved former runs at whatever angle that part of the
    curve runs, and forcing it upright tears it off the feature it
    was tracing - which is what an earlier version did."""
    import math
    # a run tilted well off the axes
    pts = [(575.0, 631.0), (729.0, 729.0), (659.0, 882.0)]
    out = geo.square_keeping_lengths(pts)
    b0 = math.degrees(math.atan2(out[1][1]-out[0][1],
                                 out[1][0]-out[0][0]))
    assert 15.0 < b0 < 50.0, (
        "the first segment should stay near its traced +32 deg, "
        "got %+.1f" % b0)


def test_keeping_lengths_shares_the_error_between_the_segments():
    """Neither segment is moved more than it must be: an 8 degree
    error becomes about 4 degrees each, not 8 on one of them."""
    import math
    pts = [(575.0, 631.0), (729.0, 729.0), (659.0, 882.0)]
    out = geo.square_keeping_lengths(pts)
    before = [math.degrees(math.atan2(pts[i+1][1]-pts[i][1],
                                      pts[i+1][0]-pts[i][0]))
              for i in range(2)]
    after = [math.degrees(math.atan2(out[i+1][1]-out[i][1],
                                     out[i+1][0]-out[i][0]))
             for i in range(2)]
    moves = [abs(a - b) for a, b in zip(after, before)]
    assert max(moves) < 6.0, moves
    assert min(moves) > 2.0, moves


def test_keeping_lengths_holds_the_first_point():
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.square_keeping_lengths(pts)
    assert out[0] == pytest.approx(pts[0])


def test_keeping_lengths_moves_the_last_point():
    """It HAS to: both ends cannot be held while every length is
    kept. The caller is choosing the lengths over the surrounding
    contour, and that is the cost."""
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.square_keeping_lengths(pts)
    assert out[-1] != pytest.approx(pts[-1])


def test_keeping_lengths_preserves_each_segment_direction():
    """A segment that ran downward must not come back running up.

    Both signs on BOTH axes, because a run that only ever goes right
    cannot tell whether the x sign was honoured: dropping it entirely
    passed an earlier version of this test.
    """
    pts = [(0.0, 0.0), (0.2, -9.0), (-7.0, -9.1), (-7.1, 4.0),
           (3.0, 4.2)]
    out = geo.square_keeping_lengths(pts)
    assert out[1][1] < out[0][1]          # down
    assert out[2][0] < out[1][0]          # LEFT
    assert out[3][1] > out[2][1]          # up
    assert out[4][0] > out[3][0]          # right


def test_an_already_square_run_is_left_alone():
    """At any tilt: the run's own turns are already right angles, so
    there is nothing to correct and nothing should move."""
    import math
    r = math.radians(10.0)
    c, s = math.cos(r), math.sin(r)
    square = [(0.0, 0.0), (0.0, 9.0), (7.0, 9.0), (7.0, 0.0)]
    tilted = [(x * c - y * s, x * s + y * c) for x, y in square]
    out = geo.square_keeping_lengths(tilted)
    for a, b in zip(tilted, out):
        assert a[0] == pytest.approx(b[0], abs=0.01)
        assert a[1] == pytest.approx(b[1], abs=0.01)


# ----- quad snap: a closed rectangle on stock-sized sides ----------------

QMPP = 0.126620
QSTEP = 25.4 / 32.0


def test_quad_snap_closes_the_shape():
    """Every side a whole number of steps, and the fourth implied -
    so the four points really are a rectangle."""
    import math
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    sides = _lengths(out) + [math.hypot(out[0][0] - out[3][0],
                                        out[0][1] - out[3][1])]
    for s in sides:
        steps = s * QMPP / QSTEP
        assert steps == pytest.approx(round(steps), abs=1e-6)


def test_quad_snap_makes_opposite_sides_equal():
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    a, b, c = _lengths(out)
    assert a == pytest.approx(c, abs=1e-9)


def test_quad_snap_averages_the_disagreeing_pair():
    """The two sides that must match rarely trace the same. Each is
    snapped on its own and the results averaged, so neither side is
    privileged and the answer does not depend on selection order."""
    # 1 step and 3 steps -> both become 2
    one, three = QSTEP / QMPP, 3 * QSTEP / QMPP
    across = 2 * QSTEP / QMPP
    pts = [(0.0, 0.0), (0.0, one), (across, one), (across, one - three)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    a, _b, c = _lengths(out)
    assert a * QMPP / QSTEP == pytest.approx(2.0, abs=1e-6)
    assert c * QMPP / QSTEP == pytest.approx(2.0, abs=1e-6)


def test_quad_snap_is_order_independent():
    one, three = QSTEP / QMPP, 3 * QSTEP / QMPP
    across = 2 * QSTEP / QMPP
    fwd = [(0.0, 0.0), (0.0, one), (across, one), (across, one - three)]
    rev = list(reversed(fwd))
    a = _lengths(geo.quad_snap(fwd, QMPP, QSTEP))
    b = _lengths(geo.quad_snap(rev, QMPP, QSTEP))
    assert sorted(round(x, 6) for x in a) == sorted(
        round(x, 6) for x in b)


def test_quad_snap_refuses_without_a_scale():
    """1/32 in means nothing in pixels, and guessing would put a
    wrong number in a part silently."""
    pts = [(10.0, 0.0), (10.4, 8.0), (16.0, 8.3), (16.2, 0.0)]
    with pytest.raises(ValueError):
        geo.quad_snap(pts, 0.0, QSTEP)
    with pytest.raises(ValueError):
        geo.quad_snap(pts, None, QSTEP)


def test_quad_snap_wants_exactly_four_points():
    with pytest.raises(ValueError):
        geo.quad_snap([(0.0, 0.0)] * 3, QMPP, QSTEP)


def test_quad_snap_never_collapses_a_side():
    """A side rounding to zero steps would delete the notch."""
    tiny = 0.2 * QSTEP / QMPP
    pts = [(0.0, 0.0), (0.0, tiny), (tiny, tiny), (tiny, 0.0)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    for s in _lengths(out):
        assert s > 0.0


def test_quad_snap_keeps_the_runs_orientation():
    """A notch cut into a curved former runs at whatever angle that
    part of the curve runs. Snapping it upright tears it off the
    feature - which is what the first version did, taking a run
    traced at +32 degrees and returning it at 0."""
    import math
    pts = [(575.0, 631.0), (729.0, 729.0), (659.0, 882.0),
           (505.0, 784.0)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    b0 = math.degrees(math.atan2(out[1][1] - out[0][1],
                                 out[1][0] - out[0][0]))
    assert 20.0 < b0 < 45.0, (
        "should stay near the traced +32 deg, got %+.1f" % b0)


def test_quad_snap_turns_are_right_angles():
    pts = [(575.0, 631.0), (729.0, 729.0), (659.0, 882.0),
           (505.0, 784.0)]
    out = geo.quad_snap(pts, QMPP, QSTEP)
    for t in _turns(out):
        assert abs(abs(t) - 90.0) == pytest.approx(0.0, abs=1e-6)
