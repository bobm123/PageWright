"""Checking a traced part against the same part drawn elsewhere.

The property under test is narrow and worth stating plainly: a part
measured in two independent drawings should measure the same, and
where it does not, something is wrong with one of them. These tests
build the two drawings synthetically so the disagreement is known
exactly.
"""

import math
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import crosscheck as X      # noqa: E402

MMPP = 0.12662013958125623


def _mm(v):
    return v / MMPP


def _rect(w_mm, h_mm, angle_deg=0.0, at=(0.0, 0.0)):
    w, h = _mm(w_mm), _mm(h_mm)
    p = np.array([(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])
    if angle_deg:
        r = math.radians(angle_deg)
        c, s = math.cos(r), math.sin(r)
        p = p @ np.array([[c, -s], [s, c]]).T
    p = p + np.array([_mm(at[0]), _mm(at[1])])
    return [tuple(v) for v in p]


def _circle(d_mm, n, at=(0.0, 0.0)):
    r = _mm(d_mm) / 2.0
    return [(_mm(at[0]) + r * math.cos(t), _mm(at[1]) + r * math.sin(t))
            for t in np.linspace(0.0, 2.0 * math.pi, n + 1)[:-1]]


def test_the_same_part_twice_raises_nothing():
    a = _rect(90.0, 10.0)
    b = _rect(90.0, 10.0, angle_deg=37.0, at=(400.0, 250.0))
    assert X.compare("R1", a, MMPP, b, MMPP) == []


def test_rotation_and_position_do_not_matter():
    """The two drawings are on different sheets at different angles.
    Comparing bounding boxes would flag every part; comparing the
    part's own principal extents must not."""
    a = _rect(60.0, 8.0)
    for ang in (0.0, 13.0, 45.0, 90.0, 171.0):
        b = _rect(60.0, 8.0, angle_deg=ang, at=(123.0, -45.0))
        assert X.compare("R", a, MMPP, b, MMPP) == [], ang


def test_a_part_that_is_too_short_is_caught():
    a = _rect(90.0, 10.0)
    b = _rect(85.0, 10.0, angle_deg=20.0)
    ms = X.compare("R1", a, MMPP, b, MMPP)
    byk = {m.kind: m for m in ms}
    assert "length" in byk, ms
    assert abs(byk["length"].size - 5.0) < 1e-6
    # the area check fires too, and should: a part 5 mm short has
    # less material. Both are the same defect seen two ways.
    assert "area" in byk, ms


def test_a_flattened_feature_shows_up_as_an_AREA_error():
    """The bird's mouth case, and why extents alone are not enough.

    A notch cut into one corner removes real material while leaving
    BOTH principal extents exactly as they were: the part is still as
    long and as wide as its widest point. Extents cannot see it. Area
    can, which is why `compare` measures both.

    (The real R1 mismatch did move the extents, by 8.4% on width,
    because its whole nose region changed. A corner notch is the
    harder case and the one worth pinning.)
    """
    kept = _rect(90.0, 10.0)[:2] + [(_mm(90.0), _mm(6.0)),
                                    (_mm(0.0), _mm(10.0))]
    lost = _rect(90.0, 10.0)
    la, sa = X.extent_mm(lost, MMPP)
    lb, sb = X.extent_mm(kept, MMPP)
    assert abs(la - lb) < 0.3 and abs(sa - sb) < 0.3, "fixture too crude"
    ms = X.compare("R1", lost, MMPP, kept, MMPP)
    assert any(m.kind == "area" for m in ms), ms


def test_the_tolerance_is_a_fraction_not_an_absolute():
    """A 1 mm error on a 10 mm part matters; the same 1 mm on a 500 mm
    part is scanner noise. A flat threshold gets one of those wrong."""
    small_a, small_b = _rect(10.0, 5.0), _rect(11.0, 5.0)
    big_a, big_b = _rect(500.0, 5.0), _rect(501.0, 5.0)
    assert X.compare("small", small_a, MMPP, small_b, MMPP) != []
    assert X.compare("big", big_a, MMPP, big_b, MMPP) == []


def test_a_tiny_difference_is_below_the_floor():
    """Two independent traces of one pencil line never agree to the
    micron. Without an absolute floor a short edge trips the relative
    test on a fraction of a pixel."""
    # The difference must be big enough RELATIVELY to trip the 2%
    # test and small enough ABSOLUTELY to be caught by the floor, or
    # this passes whether the floor exists or not. 3 mm vs 3.2 mm is
    # 6.3% - well over tolerance - but only 0.2 mm, under the floor.
    a = _rect(90.0, 3.0)
    b = _rect(90.0, 3.2)
    assert abs(3.2 - 3.0) / 3.2 > X.DEFAULT_LENGTH_TOLERANCE
    assert abs(3.2 - 3.0) <= X.DEFAULT_LENGTH_FLOOR_MM
    assert [m.kind for m in X.compare("tab", a, MMPP, b, MMPP)
            if m.kind in ("length", "width")] == []


def test_a_coarse_polygon_against_a_fine_one_is_flagged():
    """The circle case: 16 vertices where the other drawing has 180.
    Not proof of error on its own, which is why it is reported as
    'detail' rather than a dimension, but worth a look."""
    # Opt-in: the count depends on each trace's own tolerance, so
    # this is only meaningful when both were traced the same way.
    ms = X.compare("WheelDisc", _circle(22.0, 16), MMPP,
                   _circle(22.0, 180), MMPP, vertex_ratio=2.5)
    assert "detail" in [m.kind for m in ms], ms
    # ...and silent unless asked for.
    assert "detail" not in [m.kind for m in X.compare(
        "WheelDisc", _circle(22.0, 16), MMPP, _circle(22.0, 180), MMPP)]


def test_similar_vertex_counts_are_not_flagged():
    """Two honest traces of the same part differ in vertex count all
    the time. Only a LARGE disparity means a feature went missing."""
    ms = X.compare("R1", _circle(22.0, 30), MMPP, _circle(22.0, 40), MMPP,
                   vertex_ratio=2.5)
    assert ms == []


def test_match_by_label_separates_the_three_cases():
    a = {"F1": 1, "F2": 2, "DihedralGauge": 9}
    b = {"F1": 10, "F2": 20, "H1": 30}
    pairs, only_a, only_b = X.match_by_label(a, b)
    assert [p[0] for p in pairs] == ["F1", "F2"]
    assert only_a == ["DihedralGauge"]
    assert only_b == ["H1"]


def test_match_by_label_tolerates_empty_input():
    """OCR returning nothing is normal on these sheets, so this has
    to be a quiet no-op rather than an exception."""
    assert X.match_by_label({}, {"F1": 1}) == ([], [], ["F1"])
    assert X.match_by_label(None, None) == ([], [], [])


def test_station_spacings_need_no_labels_or_outlines():
    """A row of ribs is a measurement by itself. Positions in, bay
    widths out, in millimetres, independent of which rib is which."""
    xs = [100.0, 100.0 + _mm(34.0), 100.0 + _mm(68.0)]
    got = X.station_spacings_mm(xs, MMPP)
    assert len(got) == 2
    for g in got:
        assert abs(g - 34.0) < 1e-6


def test_station_spacings_sort_their_input():
    xs = [100.0 + _mm(68.0), 100.0, 100.0 + _mm(34.0)]
    got = X.station_spacings_mm(xs, MMPP)
    assert all(g > 0 for g in got), got


def test_a_degenerate_outline_is_reported_not_crashed():
    assert [m.kind for m in X.compare("x", [(0, 0), (1, 1)], MMPP,
                                      _rect(10.0, 5.0), MMPP)] \
        == ["degenerate"]
