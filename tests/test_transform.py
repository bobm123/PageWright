"""Rotating and flipping a traced outline (core, GUI-free).

The properties worth protecting: a transform must not move the part
off its own centre, must not lose precision when repeated, and must
not quietly reverse a contour's winding.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import transform as tf        # noqa: E402

# a 40 x 10 rectangle, clockwise as drawn (y down)
RECT = [[(0.0, 0.0), (40.0, 0.0), (40.0, 10.0), (0.0, 10.0)]]


def _bbox(loops):
    xs = [x for lp in loops for x, _ in lp]
    ys = [y for lp in loops for _, y in lp]
    return min(xs), min(ys), max(xs), max(ys)


def _area(loop):
    """Signed area; the sign IS the winding."""
    a = 0.0
    for i in range(len(loop)):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % len(loop)]
        a += x0 * y1 - x1 * y0
    return a / 2.0


# ----- rotation ------------------------------------------------------------

def test_a_quarter_turn_swaps_width_and_height():
    x0, y0, x1, y1 = _bbox(tf.rotate(RECT, 1))
    assert (x1 - x0) == pytest.approx(10.0)
    assert (y1 - y0) == pytest.approx(40.0)


def test_rotation_keeps_the_part_on_its_own_centre():
    """Otherwise a turned part wanders off across the sheet."""
    before = _bbox(RECT)
    cx0 = (before[0] + before[2]) / 2.0
    cy0 = (before[1] + before[3]) / 2.0
    after = _bbox(tf.rotate(RECT, 1))
    assert (after[0] + after[2]) / 2.0 == pytest.approx(cx0)
    assert (after[1] + after[3]) / 2.0 == pytest.approx(cy0)


def test_four_quarter_turns_are_exactly_the_original():
    """Right angles are exact, so repeating must not drift. A free
    rotation could not promise this."""
    out = tf.rotate(RECT, 4)
    assert out == [[(0.0, 0.0), (40.0, 0.0), (40.0, 10.0), (0.0, 10.0)]]


def test_turning_one_way_four_times_matches_turning_none():
    a = RECT
    for _ in range(4):
        a = tf.rotate(a, 1)
    assert a == tf.rotate(RECT, 0)


def test_a_quarter_turn_goes_clockwise_as_drawn():
    # y grows downward, so the corner at the origin - the top-left of
    # the rectangle as drawn - must land at the TOP-RIGHT.
    out = tf.rotate(RECT, 1)
    x0, y0, x1, y1 = _bbox(out)
    assert out[0][0][0] == pytest.approx(x1)
    assert out[0][0][1] == pytest.approx(y0)


def test_rotation_does_not_change_the_winding():
    turned = tf.rotate(RECT, 1)[0]
    assert (_area(turned) > 0) == (_area(RECT[0]) > 0)


def test_rotation_carries_a_hole_with_its_outline():
    loops = RECT + [[(5.0, 2.0), (10.0, 2.0), (10.0, 6.0), (5.0, 6.0)]]
    out = tf.rotate(loops, 1)
    assert len(out) == 2
    # the hole is still inside the outline's box
    ox0, oy0, ox1, oy1 = _bbox([out[0]])
    hx0, hy0, hx1, hy1 = _bbox([out[1]])
    assert ox0 <= hx0 and hx1 <= ox1
    assert oy0 <= hy0 and hy1 <= oy1


# ----- flipping ------------------------------------------------------------

def test_a_horizontal_flip_keeps_the_bounding_box():
    assert _bbox(tf.flip(RECT, horizontal=True)) == pytest.approx(
        _bbox(RECT))


def test_a_vertical_flip_keeps_the_bounding_box():
    assert _bbox(tf.flip(RECT, horizontal=False)) == pytest.approx(
        _bbox(RECT))


def test_flipping_twice_is_the_original():
    once = tf.flip(RECT, horizontal=True)
    assert tf.flip(once, horizontal=True) == [
        [(0.0, 0.0), (40.0, 0.0), (40.0, 10.0), (0.0, 10.0)]]


def test_a_flip_keeps_the_winding_by_reversing_the_order():
    """A mirror reverses winding. The points are reversed to undo
    that, because a contour that silently flipped its winding would
    come out inside-out wherever winding is read."""
    flipped = tf.flip(RECT, horizontal=True)[0]
    assert (_area(flipped) > 0) == (_area(RECT[0]) > 0)


def test_a_flip_actually_mirrors_the_shape():
    # an asymmetric part, so the mirror is visible rather than a no-op
    tri = [[(0.0, 0.0), (40.0, 0.0), (0.0, 10.0)]]
    out = tf.flip(tri, horizontal=True)
    assert sorted(out[0]) == sorted(
        [(40.0, 0.0), (0.0, 0.0), (40.0, 10.0)])


def test_flipping_carries_a_hole_with_its_outline():
    loops = RECT + [[(5.0, 2.0), (10.0, 2.0), (10.0, 6.0), (5.0, 6.0)]]
    out = tf.flip(loops, horizontal=True)
    hx0, _hy0, hx1, _hy1 = _bbox([out[1]])
    # the hole was near the left edge; mirrored it is near the right
    assert hx0 == pytest.approx(30.0)
    assert hx1 == pytest.approx(35.0)


# ----- translation ---------------------------------------------------------

def test_translate_moves_every_loop_by_the_same_amount():
    loops = RECT + [[(5.0, 2.0), (10.0, 6.0)]]
    out = tf.translate(loops, 100.0, -5.0)
    assert out[0][0] == (100.0, -5.0)
    assert out[1][0] == (105.0, -3.0)


# ----- empty input ---------------------------------------------------------

def test_an_empty_outline_survives_every_transform():
    assert tf.rotate([], 1) == []
    assert tf.flip([], horizontal=True) == []
    assert tf.translate([], 1.0, 1.0) == []
