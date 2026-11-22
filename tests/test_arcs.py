"""Circle detection and facet generation (core, GUI-free).

The property worth protecting: a circle recognised as a circle must
survive the round trip - fit it, regenerate it at any facet count, and
the centre and radius come back. Everything else here is about not
lying about the error.
"""

import json
import math
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import arcs as A          # noqa: E402


def _poly_circle(cx, cy, r, n, inscribed=True, start=0.0):
    """A polygon on (inscribed) or around (circumscribed) a circle."""
    rr = r if inscribed else r / math.cos(math.pi / n)
    return [(cx + rr * math.cos(start + 2 * math.pi * i / n),
             cy + rr * math.sin(start + 2 * math.pi * i / n))
            for i in range(n)]


def _area(pts):
    p = np.asarray(pts, dtype=float)
    x, y = p[:, 0], p[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) -
                     np.dot(y, np.roll(x, -1)))) / 2.0


def test_fit_recovers_a_known_circle():
    pts = _poly_circle(37.0, -12.5, 11.0, 64)
    centre, r = A.fit_circle(pts)
    assert abs(centre[0] - 37.0) < 1e-6
    assert abs(centre[1] - (-12.5)) < 1e-6
    assert abs(r - 11.0) < 1e-6


def test_fit_rejects_collinear_points():
    """Three points in a line lie on a circle of infinite radius. The
    fit must say 'no circle' rather than return a huge one."""
    centre, r = A.fit_circle([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0),
                              (3.0, 0.0)])
    assert centre is None and r == 0.0


def test_a_traced_disc_is_recognised():
    """16 vertices is what the default tolerance gives on a 22 mm
    disc, and it must still be identified as a circle."""
    pts = _poly_circle(0.0, 0.0, 10.99, 16)
    c = A.measure_circle(pts)
    assert c is not None
    assert c.is_full_circle()
    assert abs(c.radius - 10.99) < 1e-6


def test_a_rib_is_not_a_circle():
    """A long tapered part has a roundish nose and must not be
    mistaken for a disc on the strength of it."""
    pts = [(0.0, 0.0), (90.0, 1.0), (90.0, 4.0), (40.0, 9.0),
           (10.0, 7.0), (2.0, 3.0), (0.5, 1.0), (0.0, 0.5)]
    c = A.measure_circle(pts)
    assert c is None or not c.is_full_circle(), c


def test_a_notched_disc_is_reported_but_not_called_a_circle():
    """The real case: the nose plug and the cowling each carry half
    of a shared keel notch, so most of the loop is an arc and a few
    vertices are not. It must not pass as a full circle, and the
    caller must still be able to see that it is mostly round."""
    # The real parts measure on_arc 0.44 (nose plug B) and 0.76
    # (cowling) with max_dev around 1.3 mm, against 1.00 and 0.05 for
    # the two true discs. The spread is wide because a notch drags the
    # fitted centre, so the useful assertions are "not a full circle"
    # and "a large deviation" rather than a narrow on_arc band.
    pts = _poly_circle(0.0, 0.0, 7.8, 32)
    pts[8] = (pts[8][0] * 0.82, pts[8][1] * 0.82)     # notch
    pts[9] = (pts[9][0] * 0.82, pts[9][1] * 0.82)
    c = A.measure_circle(pts)
    assert c is not None
    assert not c.is_full_circle()
    assert 0.3 < c.on_arc < 0.98, c.on_arc
    assert c.max_dev > 1.0


def test_detect_circles_picks_the_round_ones_out_of_a_mixed_bag():
    loops = [
        _poly_circle(0.0, 0.0, 11.0, 16),                      # disc
        [(0, 0), (90, 1), (90, 4), (40, 9), (10, 7), (2, 3),
         (0.5, 1), (0, 0.5)],                                  # rib
        _poly_circle(50.0, 50.0, 3.2, 24),                     # small disc
    ]
    got = A.detect_circles(loops)
    assert sorted(got) == [0, 2]
    assert abs(got[0].radius - 11.0) < 1e-6
    assert abs(got[2].radius - 3.2) < 1e-6


def test_facets_round_trip_through_a_fit():
    """The round trip that makes this worth doing: generate at one
    facet count, fit, regenerate at another, and the circle is the
    same circle.

    The facet count has to be passed back in. A least-squares fit
    recovers the radius through the VERTICES, and circumscribed
    vertices sit at r/cos(pi/n) - 3.5% high at 12 facets."""
    pts = A.circle_points((5.0, -3.0), 9.25, facets=12)
    c = A.measure_circle(pts, facets_known=12)
    assert c is not None and c.is_full_circle()
    assert abs(c.radius - 9.25) < 0.02, c.radius
    again = A.circle_points(c.centre, c.radius, facets=64)
    c2 = A.measure_circle(again, facets_known=64)
    assert abs(c2.radius - 9.25) < 0.02, c2.radius
    assert abs(c2.centre[0] - 5.0) < 0.02
    assert abs(c2.centre[1] - (-3.0)) < 0.02


def test_a_plain_fit_reads_a_circumscribed_polygon_high():
    """The trap the facets_known argument exists for, pinned so it
    cannot be quietly removed: without it the radius is wrong by a
    knowable amount, and silently."""
    pts = A.circle_points((0.0, 0.0), 10.0, facets=12)
    naive = A.measure_circle(pts)
    told = A.measure_circle(pts, facets_known=12)
    assert naive.radius > 10.3, naive.radius
    assert abs(told.radius - 10.0) < 1e-9


def test_circumscribed_straddles_the_arc_and_inscribed_does_not():
    """The point of the default. An inscribed polygon is entirely
    inside the circle, so its area error is one-sided and a traced
    disc comes out systematically small."""
    r = 11.0
    ins = A.circle_points((0, 0), r, facets=16, circumscribe=False)
    cir = A.circle_points((0, 0), r, facets=16, circumscribe=True)
    true = math.pi * r * r
    assert _area(ins) < true, "inscribed should be under"
    assert _area(cir) > true, "circumscribed should be over"
    # and the error should be smaller, not merely the other way round
    assert abs(_area(cir) - true) < abs(_area(ins) - true)


def test_more_facets_is_always_closer():
    r = 11.0
    true = math.pi * r * r
    prev = None
    for n in (8, 16, 32, 64, 128):
        err = abs(_area(A.circle_points((0, 0), r, facets=n)) - true)
        if prev is not None:
            assert err < prev, n
        prev = err


def test_facet_count_is_floored_not_crashed():
    """Asking for one side is nonsense, but it must give the
    brutal-but-valid answer rather than an exception or an empty
    list."""
    for bad in (0, 1, 2, -5):
        pts = A.circle_points((0, 0), 5.0, facets=bad)
        assert len(pts) == A.MIN_FACETS


def test_facets_for_tolerance_meets_the_tolerance_it_promises():
    """The exporter-facing direction: a caller states what its device
    can resolve. The returned count has to actually achieve it."""
    r = 11.0
    for tol in (0.5, 0.1, 0.05, 0.01):
        n = A.facets_for_tolerance(r, tol)
        pts = np.array(A.circle_points((0, 0), r, facets=n), dtype=float)
        mids = (pts + np.roll(pts, -1, axis=0)) / 2.0
        worst = max(float(np.abs(np.hypot(*pts.T) - r).max()),
                    float(np.abs(np.hypot(*mids.T) - r).max()))
        # No slack: the function promises the tolerance, so it has to
        # meet it. A 5% allowance here let through the exact bug this
        # test exists for - using the inscribed sagitta formula for a
        # circumscribed polygon, which under-counts facets and
        # overshoots by up to 8%.
        assert worst <= tol, (tol, n, worst)


def test_facets_for_tolerance_asks_for_more_when_told_to_be_finer():
    r = 11.0
    counts = [A.facets_for_tolerance(r, t) for t in (0.5, 0.1, 0.02)]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_a_bigger_circle_needs_more_facets_for_the_same_tolerance():
    """Facet count is not a property of the circle alone - it follows
    from radius AND tolerance together, which is why this is a
    function rather than a constant."""
    assert (A.facets_for_tolerance(100.0, 0.05) >
            A.facets_for_tolerance(5.0, 0.05))


def test_start_angle_rotates_without_changing_the_circle():
    a = A.circle_points((2.0, 1.0), 6.0, facets=12, start_deg=0.0)
    b = A.circle_points((2.0, 1.0), 6.0, facets=12, start_deg=17.0)
    ca, cb = A.measure_circle(a), A.measure_circle(b)
    assert abs(ca.radius - cb.radius) < 1e-9
    assert abs(ca.centre[0] - cb.centre[0]) < 1e-9
    assert a[0] != b[0]


def test_too_few_points_is_not_guessed_at():
    assert A.measure_circle([(0, 0), (1, 0), (1, 1), (0, 1)]) is None


def _pogo():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "data", "pogo_round_parts.json")) as fh:
        return json.load(fh)


def test_the_real_discs_are_recognised_and_the_notched_ones_are_not():
    """Against the actual traced parts, in millimetres.

    The two discs are traced as 16-gons by the default tolerance and
    must still be seen as circles; the two that carry half of a shared
    keel notch must not, or the notch would be regenerated away.
    """
    d = _pogo()
    for name in ("WheelDisc[0]", "NosePlugA[0]"):
        c = A.measure_circle([tuple(p) for p in d[name]])
        assert c is not None and c.is_full_circle(), (name, c)
        assert 10.5 < c.radius < 11.5, (name, c.radius)
        assert c.max_dev < 0.10, (name, c.max_dev)
    for name in ("NosePlugB[0]", "Cowling-Solid[1]"):
        c = A.measure_circle([tuple(p) for p in d[name]])
        assert c is not None
        assert not c.is_full_circle(), (name, c)
        assert c.max_dev > 1.0, (name, c.max_dev)


def test_regenerating_a_real_disc_recovers_its_area():
    """The defect this module exists to fix. The traced 16-gon is
    2.6% under the area of the circle it is meant to be; regenerated
    at the default facet count it is within a quarter per cent."""
    pts = [tuple(p) for p in _pogo()["WheelDisc[0]"]]
    c = A.measure_circle(pts)
    true = math.pi * c.radius * c.radius
    traced = _area(pts)
    assert (true - traced) / true > 0.02, "fixture is not the 16-gon"
    fixed = _area(A.circle_points(c.centre, c.radius))
    assert abs(fixed - true) / true < 0.005, (fixed, true)
