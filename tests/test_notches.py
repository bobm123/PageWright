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
    out, report = nt.snap_notches(pts, MMPP, step_mm=SIX)
    lengths = sorted(_edge_lengths_mm(out))
    assert lengths[0] == pytest.approx(SIX, abs=0.01)
    assert lengths[1] == pytest.approx(SIX, abs=0.01)
    assert report.snapped > 0


def test_snapping_reaches_multiples_not_just_one_step():
    pts = _rect(4.6, 10.0)          # ~3/16 in wide
    out, _r = nt.snap_notches(pts, MMPP, step_mm=SIX)
    width = min(_edge_lengths_mm(out))
    assert width == pytest.approx(3 * SIX, abs=0.01)


def test_an_edge_too_far_off_is_left_alone():
    # Half a step is 0.79 mm; 1.0 mm off must not be dragged anywhere.
    # Stated against the 1/16 in step the numbers were chosen for: on
    # the 1/32 in default the same width is only 0.19 mm from a
    # multiple, which is well inside tolerance and SHOULD snap.
    target = 2 * SIX
    pts = _rect(target - 1.0, 10.0)
    out, report = nt.snap_notches(pts, MMPP, step_mm=SIX)
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


# ----- the step, and what is too long to snap -----------------------------

def test_the_default_step_is_one_thirtysecond():
    """A notch is cut to HALF the stock where two pieces cross: a
    former on a 1/16 in keel is notched 1/32 in deep. So the sizes
    that appear on a sheet step in thirty-seconds even when every
    stick is a sixteenth."""
    assert nt.DEFAULT_STEP_MM == pytest.approx(25.4 / 32.0)


def test_a_wide_gap_is_not_snapped_to_a_multiple():
    """A joint line or a keel seat stands well clear of anything, and
    its position is whatever the design says. Measured on the Pogo
    formers, F8's joint edge sat at 5.59 steps of 1/16 in and F6's
    keel seat at 5.74 - between multiples, no better on 3/32 or 1/8 -
    and snapping moved them 0.18 and 0.49 mm for nothing."""
    # two lines 9 mm apart: far wider than four steps
    pts = _rect(9.0, 10.0)
    out, report = nt.snap_notches(pts, MMPP)
    width = min(_edge_lengths_mm(out))
    assert width == pytest.approx(9.0, abs=0.01), (
        "a 9 mm gap should be left where it is, got %.3f" % width)
    assert report.pinned > 0


def test_a_narrow_gap_still_snaps():
    """The rule must not switch snapping off: a notch wall has its
    partner a stick's width away, and that is the case worth
    correcting."""
    pts = _rect(0.70, 10.0)          # ~1/32 in, traced short
    out, report = nt.snap_notches(pts, MMPP)
    width = min(_edge_lengths_mm(out))
    assert width == pytest.approx(25.4 / 32.0, abs=0.01)
    assert report.snapped > 0


def test_long_walls_do_not_stop_a_narrow_notch_snapping():
    """The rule is about the GAP, not any edge's own length. A notch
    1.4 mm wide and 8 mm deep has two 8 mm walls, and those walls are
    exactly what define the width worth snapping - an earlier version
    judged them by their own length, pinned them, and killed the
    snap that mattered."""
    pts = _rect(0.70, 12.0)          # narrow gap, very long walls
    out, _r = nt.snap_notches(pts, MMPP)
    assert min(_edge_lengths_mm(out)) == pytest.approx(25.4 / 32.0,
                                                       abs=0.01)


def test_a_pinned_line_is_still_squared():
    """Pinning holds a line's POSITION, not its angle: a joint edge
    ought to come out straight even though its length is nobody's
    stock size."""
    pts = _rect(9.0, 10.0, jitter=[(0, 0), (0, 2.0), (0, 0), (0, 0)])
    out, report = nt.snap_notches(pts, MMPP)
    assert report.squared > 0
    assert report.pinned > 0
    # measured against the PART's own grid, not the image's: a
    # drawing is rarely pasted down square, and squaring works to the
    # dominant angle it finds (0.26 deg here, induced by the jitter)
    grid = math.radians(nt.dominant_angle_deg(out, MMPP))
    c, sn = math.cos(grid), math.sin(grid)
    rot = [(x * c + y * sn, -x * sn + y * c) for x, y in out]
    assert rot[0][1] == pytest.approx(rot[1][1], abs=0.5)


def test_a_pinned_line_keeps_its_position():
    """The point of the pin: the gap is not dragged to a multiple."""
    out, _r = nt.snap_notches(_rect(9.0, 10.0), MMPP)
    assert min(_edge_lengths_mm(out)) == pytest.approx(9.0, abs=0.01)


# ----- idempotency on the ten real formers ---------------------------------
#
# snap_notches used to move points on every call: applying it to
# already-cleaned geometry kept squaring and re-snapping, decaying but
# never settling, and on some parts the set of edges it squared grew
# between calls so the movement jumped back up. These tests pin the
# fixed-point property down on real traced outlines.

_FORMERS = os.path.join(os.path.dirname(__file__), "data",
                        "pogo_formers.json")


def _pogo():
    """The ten Pogo formers and their calibration, from the plan."""
    import json
    with open(_FORMERS) as fh:
        d = json.load(fh)
    return d["mm_per_pixel"], [(f["name"], [tuple(p) for p in f["points"]])
                               for f in d["formers"]]


def _max_move_mm(a, b, mm_per_pixel):
    return max(math.hypot(p[0] - q[0], p[1] - q[1])
               for p, q in zip(a, b)) * mm_per_pixel


def _roughen(points, mm_per_pixel, seed):
    """Undo the cleanup: put trace-like noise back on a former.

    The saved project was written AFTER a notch pass, so its formers
    are already square and already on the stock grid. A test that only
    used them would pass against a snap_notches that did nothing at
    all. Roughening gives the function real work to do, so the second
    call has something to get wrong.
    """
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.35, (len(points), 2))
    return [(p[0] + d[0], p[1] + d[1]) for p, d in zip(points, noise)]


def test_every_real_former_is_idempotent():
    """The property: snap(snap(p)) == snap(p), to floating point."""
    mmpp, formers = _pogo()
    assert len(formers) == 10
    for name, pts in formers:
        once, _r1 = nt.snap_notches(pts, mmpp)
        twice, _r2 = nt.snap_notches(once, mmpp)
        assert _max_move_mm(once, twice, mmpp) < 1e-9, name


def test_every_real_former_is_idempotent_when_rough():
    """Same, on geometry that still needs the cleanup.

    This is the case that actually exercised the bug: the function has
    to square and snap on the first call, then leave its own output
    alone on the second.
    """
    mmpp, formers = _pogo()
    for seed, (name, pts) in enumerate(formers):
        rough = _roughen(pts, mmpp, seed)
        once, rep = nt.snap_notches(rough, mmpp)
        # it really did work, or the test below proves nothing
        assert rep.squared > 0 or rep.snapped > 0, name
        twice, _r = nt.snap_notches(once, mmpp)
        assert _max_move_mm(once, twice, mmpp) < 1e-9, name


def test_repeat_application_never_drifts():
    """Eight passes must be the same as one.

    The original symptom was not just movement but movement that came
    BACK: on F7 the squared/snapped counts sat at 24/21 for six passes
    with the movement decaying 0.40 -> 0.13 mm, then jumped to 25/22
    and 0.36 mm as a new edge was tipped inside the angle tolerance.
    """
    mmpp, formers = _pogo()
    for seed, (name, pts) in enumerate(formers):
        settled, _r = nt.snap_notches(_roughen(pts, mmpp, seed), mmpp)
        pts_i = settled
        for i in range(8):
            pts_i, _ri = nt.snap_notches(pts_i, mmpp)
            assert _max_move_mm(settled, pts_i, mmpp) < 1e-9, (name, i)


def test_the_grid_angle_is_a_fixed_point():
    """Why idempotency holds: squaring must not move the grid.

    dominant_angle_deg averages the edges that snap_notches squares, so
    once they lie on the angle, measuring again returns it. Averaging
    every near-axis edge instead - curve chords included, which are
    never squared - moved the angle up to 1.06 deg per call on these
    formers and never settled.
    """
    mmpp, formers = _pogo()
    for seed, (name, pts) in enumerate(formers):
        rough = _roughen(pts, mmpp, seed)
        once, rep = nt.snap_notches(rough, mmpp)
        assert abs(nt.dominant_angle_deg(once, mmpp)
                   - rep.grid_deg) < 1e-9, name


def test_a_settled_part_reports_no_work_and_one_pass():
    """Handed its own output, the function should find nothing to do."""
    mmpp, formers = _pogo()
    for seed, (name, pts) in enumerate(formers):
        once, _r = nt.snap_notches(_roughen(pts, mmpp, seed), mmpp)
        _twice, rep = nt.snap_notches(once, mmpp)
        assert rep.squared == 0, name
        assert rep.snapped == 0, name
        assert rep.passes == 1, name
        assert rep.max_move_mm < 1e-9, name


def test_it_reports_convergence():
    """The report must not claim a fixed point it did not reach."""
    mmpp, formers = _pogo()
    for seed, (name, pts) in enumerate(formers):
        _once, rep = nt.snap_notches(_roughen(pts, mmpp, seed), mmpp)
        assert rep.converged, name
        assert 1 <= rep.passes <= nt.MAX_PASSES, name


def test_snap_spacing_is_its_own_fixed_point():
    """The cluster-spacing snap underneath, on its own.

    Fuzzed rather than hand-picked: the de-drift step that broke
    idempotency only misbehaves when the tolerance check freezes some
    lines and not others, which is tedious to construct by hand and
    easy to hit at random.
    """
    import random
    rng = random.Random(11)
    step, tol = 37.8, 11.3
    for _ in range(2000):
        vals = sorted(rng.uniform(0.0, 10 * step)
                      for _ in range(rng.randint(2, 14)))
        once = nt._snap_spacing(vals, step, tol)
        twice = nt._snap_spacing(once, step, tol)
        assert max(abs(a - b) for a, b in zip(once, twice)) < 1e-9, vals


def test_snap_spacing_lands_on_its_own_grid():
    """A snapped group must sit ON the stock grid, not near it.

    This is the property the whole spacing snap exists to produce, and
    it is checked on the residual rather than by re-running the
    function, so the fixed-point self-check inside _snap_spacing
    cannot satisfy it by quietly abandoning the snap.
    """
    step, tol = 37.8, 11.3
    # three lines a hair off a 2-step and a 3-step spacing
    vals = [10.4, 10.4 + 2 * step + 0.9, 10.4 + 5 * step - 1.1]
    out = nt._snap_spacing(vals, step, tol)
    assert max(abs(a - b) for a, b in zip(vals, out)) > 1e-9, "no snap"
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            gap = abs(out[i] - out[j]) / step
            assert abs(gap - round(gap)) < 1e-9, (i, j, gap)


def test_snap_spacing_keeps_the_group_in_place():
    """The de-drift has to do its job: a notch is defined by its
    width, not its position, so the group must not translate."""
    step, tol = 37.8, 11.3
    vals = [10.4, 10.4 + 2 * step + 0.9, 10.4 + 5 * step - 1.1]
    out = nt._snap_spacing(vals, step, tol)
    assert abs(sum(out) / len(out) - sum(vals) / len(vals)) < 1e-9
