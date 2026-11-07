"""Notch squaring and 1/16 in snapping (core, GUI-free).

The safety property these tests exist to protect: snapping must change
the straight, squared-off notch edges and NOTHING else. On the sample
former 48 of 78 vertices belong to the curved profile, and moving any
of them would wreck the part.
"""

import math
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import notches as nt        # noqa: E402

MMPP = 0.0846667                 # a 300 DPI plan
SIX = 25.4 / 16.0                # 1/16 inch in mm


def _mm(v):
    return v / MMPP              # mm -> px at the test scale


def _rect(w_mm, h_mm, angle_deg=0.0, jitter=None):
    """A rectangle in pixels, optionally rotated and perturbed."""
    w, h = _mm(w_mm), _mm(h_mm)
    pts = np.array([(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])
    if jitter is not None:
        pts = pts + np.array(jitter, dtype=float)
    if angle_deg:
        r = math.radians(angle_deg)
        c, s = math.cos(r), math.sin(r)
        pts = pts @ np.array([[c, -s], [s, c]]).T
    return [tuple(p) for p in pts]


def _edge_lengths_mm(points):
    p = np.asarray(points, dtype=float)
    seg = np.roll(p, -1, axis=0) - p
    return np.hypot(seg[:, 0], seg[:, 1]) * MMPP


# ----- grid detection ------------------------------------------------------

def test_dominant_angle_finds_an_axis_aligned_part():
    assert nt.dominant_angle_deg(_rect(10, 20), MMPP) == pytest.approx(
        0.0, abs=0.3)


def test_dominant_angle_finds_a_rotated_part():
    # A drawing is rarely pasted down square; the sample sits 0.8 deg off.
    for angle in (0.75, 3.0, 12.5):
        found = nt.dominant_angle_deg(_rect(10, 20, angle), MMPP)
        assert min(abs(found - angle), 90 - abs(found - angle)) < 0.4, angle


# ----- snapping ------------------------------------------------------------

def test_an_off_size_notch_snaps_to_the_stock_size():
    # 1.39 mm is what a 1/16 in notch traces as; it should become 1.5875.
    pts = _rect(1.39, 8.0)
    out, report = nt.snap_notches(pts, MMPP)
    lengths = sorted(_edge_lengths_mm(out))
    assert lengths[0] == pytest.approx(SIX, abs=0.01)
    assert lengths[1] == pytest.approx(SIX, abs=0.01)
    assert report.snapped > 0


def test_snapping_reaches_multiples_not_just_one_step():
    pts = _rect(4.6, 10.0)          # ~3/16 in wide
    out, _r = nt.snap_notches(pts, MMPP)
    width = min(_edge_lengths_mm(out))
    assert width == pytest.approx(3 * SIX, abs=0.01)


def test_an_edge_too_far_off_is_left_alone():
    # Half a step is 0.79 mm; 1.0 mm off must not be dragged anywhere.
    target = 2 * SIX
    pts = _rect(target - 1.0, 10.0)
    out, report = nt.snap_notches(pts, MMPP)
    assert report.out_of_tolerance > 0
    assert min(_edge_lengths_mm(out)) == pytest.approx(target - 1.0, abs=0.02)


def test_tolerance_cannot_exceed_half_a_step():
    # A caller asking for a huge window must not be able to make an edge
    # jump to the wrong multiple.
    pts = _rect(1.5 * SIX, 10.0)     # exactly ambiguous
    out, _r = nt.snap_notches(pts, MMPP, tolerance_mm=99.0)
    width = min(_edge_lengths_mm(out))
    assert width == pytest.approx(1.5 * SIX, abs=0.05)


def test_squaring_straightens_a_skewed_edge():
    # One corner pulled sideways: the edge should come back square TO
    # THE PART, which is not the same as level on screen - the skew
    # tilts the detected grid slightly, and squaring is relative to it.
    pts = _rect(3 * SIX, 8.0, jitter=[(0, 0), (0, _mm(0.3)), (0, 0), (0, 0)])
    out, report = nt.snap_notches(pts, MMPP)
    assert report.squared > 0

    def skew_of(poly):
        """How far the top edge is from parallel with the bottom one."""
        q = np.asarray(poly, dtype=float)
        top = q[1] - q[0]
        bottom = q[2] - q[3]
        a = math.degrees(math.atan2(top[1], top[0]))
        b = math.degrees(math.atan2(bottom[1], bottom[0]))
        return abs((a - b + 180.0) % 360.0 - 180.0)

    assert skew_of(out) < skew_of(pts) / 2.0
    assert skew_of(out) < 0.5


def test_a_rotated_part_is_squared_in_its_own_frame():
    # Squaring to SCREEN axes would rotate the part; it must not.
    angle = 5.0
    pts = _rect(2 * SIX, 10.0, angle)
    out, report = nt.snap_notches(pts, MMPP)
    assert min(abs(report.grid_deg - angle),
               90 - abs(report.grid_deg - angle)) < 0.5
    before = sorted(_edge_lengths_mm(pts))
    after = sorted(_edge_lengths_mm(out))
    assert after[0] == pytest.approx(before[0], abs=0.05)


def test_a_custom_step_is_honoured():
    # Not every plan uses 1/16 in stock.
    eighth = 25.4 / 8.0
    pts = _rect(eighth - 0.3, 10.0)
    out, _r = nt.snap_notches(pts, MMPP, step_mm=eighth)
    assert min(_edge_lengths_mm(out)) == pytest.approx(eighth, abs=0.01)


# ----- what must NOT change ------------------------------------------------

def _circle(radius_mm, n=64):
    r = _mm(radius_mm)
    return [(r * math.cos(2 * math.pi * k / n),
             r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_a_curve_is_left_completely_alone():
    pts = _circle(20.0)
    out, report = nt.snap_notches(pts, MMPP)
    moved = np.hypot(*(np.asarray(out) - np.asarray(pts)).T) * MMPP
    # a circle has a few edges that happen to run near an axis; nothing
    # may move by a meaningful amount
    assert moved.max() < 0.05, report


def test_vertex_count_and_winding_are_preserved():
    pts = _rect(1.39, 8.0)
    out, _r = nt.snap_notches(pts, MMPP)
    assert len(out) == len(pts)

    def area(p):
        p = np.asarray(p)
        x, y = p[:, 0], p[:, 1]
        return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)

    assert math.copysign(1, area(out)) == math.copysign(1, area(pts))


def test_uncalibrated_input_is_returned_unchanged():
    # Without mm per pixel there is no 1/16 inch to snap to.
    pts = _rect(1.39, 8.0)
    for bad in (None, 0.0, -1.0):
        out, report = nt.snap_notches(pts, bad)
        assert out == [tuple(p) for p in pts]
        assert report.snapped == 0


def test_a_degenerate_contour_is_safe():
    for pts in ([], [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)]):
        out, report = nt.snap_notches(pts, MMPP)
        assert len(out) == len(pts)
        assert report.snapped == 0


# ----- used from CAD, not from a scan --------------------------------------

def test_snapping_works_in_millimetres_for_geometry_with_no_scan():
    """PlaneCraft's case: a longeron drawn in the 3D model, wanted
    true to stock size. mm_per_pixel=1.0 makes the "pixels" mm, so
    the same function serves without a copy of it living elsewhere."""
    stock = 25.4 / 16.0
    pts = [(0.0, 0.0), (60.0, 0.12), (60.0, 1.70), (0.0, 1.72)]
    out, report = nt.snap_notches(pts, mm_per_pixel=1.0)
    height = abs(out[3][1] - out[0][1])
    assert height == pytest.approx(stock, abs=1e-6)
    assert report.squared == 4          # and it was squared up too


def test_other_stock_sizes_snap_to_their_own_step():
    pts = [(0.0, 0.0), (40.0, 0.0), (40.0, 3.30), (0.0, 3.30)]
    out, _r = nt.snap_notches(pts, 1.0, step_mm=25.4 / 8.0)
    assert abs(out[3][1] - out[0][1]) == pytest.approx(25.4 / 8.0,
                                                       abs=1e-6)


def test_an_edge_too_far_from_a_multiple_is_left_alone():
    """3.30 mm against a 3/32 step is not an honest snap, so the
    tolerance declines it rather than inventing a dimension."""
    pts = [(0.0, 0.0), (40.0, 0.0), (40.0, 3.30), (0.0, 3.30)]
    out, _r = nt.snap_notches(pts, 1.0, step_mm=25.4 * 3 / 32.0)
    assert abs(out[3][1] - out[0][1]) == pytest.approx(3.30, abs=1e-6)


def test_the_module_needs_nothing_from_pagewrights_model():
    """The property that makes it shareable: points in, points out.
    A model import here would tie it to one app."""
    import io
    src = io.open(nt.__file__.replace(".pyc", ".py"),
                  encoding="utf-8").read()
    for name in ("Contour", "TracedObject", "Project", "cv2", "PySide"):
        assert name not in src, "notches.py now depends on %s" % name
