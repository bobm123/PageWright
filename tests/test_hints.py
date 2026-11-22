"""Geometric hints: V-notches and local-frame squaring (core, GUI-free).

These run on SYNTHETIC geometry, where the right answer is known
exactly. That is deliberate. The feature these primitives exist for -
a rib's bird's mouth - is drawn with a 0.3 mm pencil line and is about
1.4 mm deep, so measuring it off a scan is its own problem with its
own error budget. Testing the squaring against a scan would conflate
"does the maths work" with "can we find the feature", and only the
first is this module's job.
"""

import math
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import hints as H          # noqa: E402

MMPP = 0.12662013958125623           # the Pogo scan's scale


def _mm(v):
    return v / MMPP


def _vee_part(included_deg=90.0, bisector_deg=0.0, depth_mm=4.0,
              body_mm=30.0, height_mm=12.0):
    """A rectangle with one V cut into its left edge.

    The V's apex points along `bisector_deg` (0 = the notch opens to
    the LEFT, apex to the right), so the part is a crude rib: flat
    bottom, flat top, a mouth in the nose.
    """
    half = math.radians(included_deg / 2.0)
    b = math.radians(bisector_deg)
    d = _mm(depth_mm)
    apex = np.array([_mm(0.0), _mm(0.0)])
    # the two faces run out of the apex, symmetric about the bisector
    up = apex + d * np.array([math.cos(b + half), math.sin(b + half)])
    dn = apex + d * np.array([math.cos(b - half), math.sin(b - half)])
    w, h = _mm(body_mm), _mm(height_mm)
    # The body is a TAPERED blob, not a rectangle: a rectangle's own
    # corners are 90 degree Vs and `find_vees` rightly reports them,
    # which would make this fixture unable to tell a found notch from
    # a found corner. Rounding the far end with several shallow turns
    # leaves exactly one sharp V in the part - the notch under test.
    far = max(up[0], dn[0]) + w
    body = []
    for t in np.linspace(-1.0, 1.0, 9):
        body.append((far - h * 0.25 * (1.0 - t * t),
                     (up[1] + dn[1]) / 2.0 + t * h))
    return ([(up[0], up[1])] + body + [(dn[0], dn[1]),
                                       (apex[0], apex[1])])


def test_find_vees_finds_a_square_notch():
    pts = _vee_part(included_deg=90.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0)
    assert len(vs) >= 1
    v = max(vs, key=lambda w: w.depth_mm)
    assert abs(v.included_deg - 90.0) < 0.5, v


def test_find_vees_reports_the_bisector_not_the_part_axis():
    """The whole point of the module: a feature has its own frame.

    A notch bisected at -30 degrees is still a 90 degree notch. Its
    two faces then sit at +15 and -75 to the part's x axis, and no
    axis-relative rule can square them; the bisector can.
    """
    pts = _vee_part(included_deg=90.0, bisector_deg=-30.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0)
    # The notch apex is the LAST vertex by construction, so identify
    # it by index rather than by depth: with a tilted bisector a body
    # vertex can be just as deep, and picking the deepest would test
    # the fixture rather than the function.
    apex = len(pts) - 1
    v = [w for w in vs if w.index == apex]
    assert v, [str(w) for w in vs]
    v = v[0]
    assert abs(v.included_deg - 90.0) < 0.5
    bis = (v.bisector_deg + 180.0) % 360.0 - 180.0
    assert abs(bis - (-30.0)) < 1.0, bis


def test_find_vees_ignores_a_notch_of_the_wrong_angle():
    """A hint asserts an angle; a feature far from it is not that
    feature. A 40 degree dart must not be squared as a 90 degree
    mouth just because both are notches."""
    pts = _vee_part(included_deg=40.0)
    assert H.find_vees(pts, MMPP, included_deg=90.0,
                       angle_tol_deg=18.0) == []


def test_find_vees_rejects_a_single_noisy_vertex():
    """Two long edges meeting at 90 degrees are a notch; a 0.1 mm
    spur between two long edges is scanner noise. The min_face_mm
    floor is what separates them."""
    pts = _vee_part(included_deg=90.0, depth_mm=0.10)
    assert H.find_vees(pts, MMPP, included_deg=90.0,
                       min_face_mm=0.35) == []


def test_square_vee_sets_the_included_angle_exactly():
    pts = _vee_part(included_deg=78.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0, angle_tol_deg=20.0)
    v = max(vs, key=lambda w: w.depth_mm)
    out = H.square_vee(pts, v, MMPP, included_deg=90.0)
    again = H.find_vees(out, MMPP, included_deg=90.0)
    w = max(again, key=lambda z: z.depth_mm)
    assert abs(w.included_deg - 90.0) < 1e-6, w.included_deg


def test_square_vee_keeps_the_faces_their_own_length():
    """A mouth is cut to a depth. Squaring it must change the ANGLE
    and not the depth, or the spar no longer seats."""
    pts = _vee_part(included_deg=78.0, depth_mm=4.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0, angle_tol_deg=20.0)
    v = max(vs, key=lambda w: w.depth_mm)
    out = H.square_vee(pts, v, MMPP, included_deg=90.0)
    for end in (v.upper, v.lower):
        before = math.hypot(pts[end][0] - pts[v.index][0],
                            pts[end][1] - pts[v.index][1])
        after = math.hypot(out[end][0] - out[v.index][0],
                           out[end][1] - out[v.index][1])
        assert abs(before - after) < 1e-9, (before, after)


def test_square_vee_moves_only_the_two_face_vertices():
    """Surgical: a hint about the nose must not stir the trailing
    edge. Every other vertex has to come back bit-identical."""
    pts = _vee_part(included_deg=78.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0, angle_tol_deg=20.0)
    v = max(vs, key=lambda w: w.depth_mm)
    out = H.square_vee(pts, v, MMPP, included_deg=90.0)
    for i, (a, b) in enumerate(zip(pts, out)):
        if i in (v.upper, v.lower):
            continue
        assert a == b, i


def test_square_vee_is_idempotent():
    pts = _vee_part(included_deg=78.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0, angle_tol_deg=20.0)
    v = max(vs, key=lambda w: w.depth_mm)
    once = H.square_vee(pts, v, MMPP, included_deg=90.0)
    twice = H.square_vee(once, v, MMPP, included_deg=90.0)
    worst = max(math.hypot(a[0] - b[0], a[1] - b[1])
                for a, b in zip(once, twice))
    assert worst < 1e-9, worst


def test_square_vee_does_not_flip_the_notch_over():
    """The face that was above the bisector must stay above it.
    Swapping them would turn a mouth into a mirrored mouth, which
    still measures 90 degrees and is still wrong."""
    pts = _vee_part(included_deg=70.0)
    vs = H.find_vees(pts, MMPP, included_deg=90.0, angle_tol_deg=25.0)
    v = max(vs, key=lambda w: w.depth_mm)
    out = H.square_vee(pts, v, MMPP, included_deg=90.0)
    before = pts[v.upper][1] - pts[v.lower][1]
    after = out[v.upper][1] - out[v.lower][1]
    assert math.copysign(1, before) == math.copysign(1, after)


def _curved_strip(sag_mm=15.0, length_mm=200.0, height_mm=10.0, n=240):
    """A long swept strip, like a keel: no single grid axis."""
    xs = np.linspace(0.0, length_mm, n)
    top = sag_mm * (xs / length_mm) ** 2
    up = [(_mm(x), _mm(y)) for x, y in zip(xs, top)]
    dn = [(_mm(x), _mm(y + height_mm)) for x, y in zip(xs, top)][::-1]
    return up + dn


def test_square_to_local_edge_follows_a_curve():
    """The keel case. An edge squared to the LOCAL outline direction
    comes out perpendicular to the curve where it sits, which a
    part-wide axis cannot do on a part whose direction changes by
    tens of degrees end to end.
    """
    pts = _curved_strip()
    n = len(pts)
    # put a deliberately skewed wall partway along the top edge
    i = n // 4
    a, b = pts[i], pts[i + 1]
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    skew = math.radians(70.0)          # should be 90 to the local edge
    half = _mm(1.5)
    pts = list(pts)
    pts[i] = (mid[0] - half * math.cos(skew), mid[1] - half * math.sin(skew))
    pts[i + 1] = (mid[0] + half * math.cos(skew), mid[1] + half * math.sin(skew))

    out = H.square_to_local_edge(pts, i, MMPP, reference_mm=6.0,
                                 angle_deg=90.0, angle_tol_deg=40.0)
    ref = H._local_direction(out, i, (i + 1) % len(out), _mm(6.0))
    got = H._edge_dir_deg(out[i], out[i + 1])
    rel = abs(H._ang_diff(got, ref)) % 180.0
    assert abs(rel - 90.0) < 2.0, rel


def test_square_to_local_edge_leaves_a_far_off_edge_alone():
    """A tolerance means something. An edge nowhere near the asked-for
    angle is not that feature, and silently rotating it would be the
    worst kind of wrong: plausible and invisible."""
    pts = _curved_strip()
    i = len(pts) // 4
    before = list(pts)
    out = H.square_to_local_edge(pts, i, MMPP, angle_deg=90.0,
                                 angle_tol_deg=5.0)
    assert out == [tuple(float(v) for v in p) for p in before]


def test_square_vee_squares_about_the_BISECTOR_not_the_part_axis():
    """The mutation that survived everything else.

    Replacing the measured bisector with a fixed part axis passes
    every other test here, because those use a notch that happens to
    be bisected along x. A tilted notch tells them apart: squared
    about its own bisector it keeps pointing where it was drawn and
    merely opens to 90 degrees, while squaring about the part axis
    swings the whole notch round to point along x.

    A mouth that has been rotated is worse than one left alone: it
    still measures 90 degrees, so every angle check passes, and the
    spar no longer meets the rib where the drawing put it.
    """
    want = -34.0
    pts = _vee_part(included_deg=72.0, bisector_deg=want)
    apex = len(pts) - 1
    vs = [w for w in H.find_vees(pts, MMPP, included_deg=90.0,
                                 angle_tol_deg=25.0) if w.index == apex]
    assert vs, "fixture did not produce the notch under test"
    v = vs[0]
    out = H.square_vee(pts, v, MMPP, included_deg=90.0)

    again = [w for w in H.find_vees(out, MMPP, included_deg=90.0)
             if w.index == apex]
    assert again, "the notch stopped being findable after squaring"
    w = again[0]
    assert abs(w.included_deg - 90.0) < 1e-6, w.included_deg

    bis = (w.bisector_deg + 180.0) % 360.0 - 180.0
    assert abs(bis - want) < 1.0, (
        "the notch was rotated to %.1f, not kept at %.1f" % (bis, want))
