"""Tests for core.dxf_import -- pure Python (no Qt/cv2).

The shapes here are the ones SolidCraft actually writes, checked
against real files from `sketch.export_dxf` and `sheet.export_dxf`:
LINE, ARC and CIRCLE in a flat ENTITIES section, with splines already
flattened into short LINEs (a four-point fit spline came out as 72).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import dxf_import as dx        # noqa: E402

HEADER = """0
SECTION
2
HEADER
9
$INSUNITS
70
%d
0
ENDSEC
"""


def _line(x0, y0, x1, y1):
    return ("0\nLINE\n8\n0\n10\n%f\n20\n%f\n11\n%f\n21\n%f\n"
            % (x0, y0, x1, y1))


def _circle(cx, cy, r):
    return "0\nCIRCLE\n8\n0\n10\n%f\n20\n%f\n40\n%f\n" % (cx, cy, r)


def _arc(cx, cy, r, a0, a1):
    return ("0\nARC\n8\n0\n10\n%f\n20\n%f\n40\n%f\n50\n%f\n51\n%f\n"
            % (cx, cy, r, a0, a1))


def _dxf(body, units=4):
    return (HEADER % units) + "0\nSECTION\n2\nENTITIES\n" + body + \
        "0\nENDSEC\n0\nEOF\n"


def _square(size=10.0):
    return (_line(0, 0, size, 0) + _line(size, 0, size, size) +
            _line(size, size, 0, size) + _line(0, size, 0, 0))


# ----- parsing ------------------------------------------------------------

def test_a_square_of_lines_becomes_one_closed_loop():
    loops, info = dx.read_dxf(_dxf(_square()))
    assert info["loops"] == 1 and info["open"] == 0
    assert len(loops[0]) == 4


def test_segments_join_regardless_of_direction():
    """A DXF is a soup of entities with no profile structure, and
    nothing says the segments run the same way round."""
    body = (_line(0, 0, 10, 0) + _line(10, 10, 10, 0) +     # reversed
            _line(10, 10, 0, 10) + _line(0, 0, 0, 10))      # reversed
    loops, info = dx.read_dxf(_dxf(body))
    assert info["loops"] == 1 and info["open"] == 0
    assert len(loops[0]) == 4


def test_the_biggest_loop_comes_first():
    """The outline is the largest closed loop; the rest are holes."""
    body = _square(40) + _circle(20, 20, 5)
    loops, _info = dx.read_dxf(_dxf(body))
    assert len(loops[0]) == 4                     # the square
    assert len(loops) == 2


def test_an_arc_is_flattened_into_points():
    loops, _ = dx.read_dxf(_dxf(_arc(0, 0, 10, 0, 90)))
    assert len(loops[0]) > 4


def test_an_unsupported_entity_is_counted_not_dropped():
    """A silently ignored entity is a hole missing from a cut part."""
    body = _square() + "0\nSPLINE\n8\n0\n"
    _loops, info = dx.read_dxf(_dxf(body))
    assert info["skipped"] == {"SPLINE": 1}


def test_a_file_with_no_usable_entity_is_an_error():
    with pytest.raises(dx.DxfImportError):
        dx.read_dxf(_dxf("0\nSPLINE\n8\n0\n"))


def test_an_open_chain_is_reported():
    """A part with a gap is still worth seeing, but the caller has to
    be told the outline will not close."""
    body = _line(0, 0, 10, 0) + _line(10, 0, 10, 10)
    _loops, info = dx.read_dxf(_dxf(body))
    assert info["open"] == 1


# ----- units --------------------------------------------------------------

def test_inches_are_converted_to_millimetres():
    loops, info = dx.read_dxf(_dxf(_square(1.0), units=1))
    assert info["units_mm"] == pytest.approx(25.4)
    xs = [p[0] for p in loops[0]]
    assert max(xs) - min(xs) == pytest.approx(25.4)


def test_millimetres_pass_through():
    loops, info = dx.read_dxf(_dxf(_square(10.0), units=4))
    assert info["units_mm"] == 1.0
    assert max(p[0] for p in loops[0]) == pytest.approx(10.0)


def test_mm_per_pixel_converts_to_pixel_space():
    """Contour is a pixel-space type, so a caller storing one needs
    pixels, not mm."""
    loops, _ = dx.read_dxf(_dxf(_square(10.0)), mm_per_pixel=0.5)
    assert max(p[0] for p in loops[0]) == pytest.approx(20.0)


# ----- curve detail -------------------------------------------------------

def test_the_default_detail_suits_a_hand_drawn_plan():
    """These plans were drawn at this size with a French curve through
    a couple of control points, so sub-tenth-millimetre fidelity
    reproduces the draughtsman's hand, not the aeroplane. The default
    is also finer than a laser kerf."""
    assert dx.DEFAULT_SAGITTA_MM == 0.2


@pytest.mark.parametrize("sagitta,at_most", [(0.05, 40), (0.2, 20),
                                             (0.5, 14)])
def test_looser_detail_gives_fewer_points(sagitta, at_most):
    loops, _ = dx.read_dxf(_dxf(_circle(0, 0, 3.5)),
                           sagitta_mm=sagitta)
    assert len(loops[0]) <= at_most


def test_detail_is_a_real_tolerance_not_a_point_count():
    """The deviation is what is held constant, so the same setting
    behaves the same on a 3 mm hole and a 300 mm curve."""
    import math
    r = 20.0
    loops, _ = dx.read_dxf(_dxf(_circle(0, 0, r)), sagitta_mm=0.2)
    loop = loops[0]
    # measured from the points themselves: a closed loop has as many
    # chords as points, which is easy to get wrong by one.
    worst = 0.0
    for i in range(len(loop)):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % len(loop)]
        half = math.hypot(x1 - x0, y1 - y0) / 2.0
        worst = max(worst, r - math.sqrt(max(0.0, r * r - half * half)))
    assert worst <= 0.2 + 1e-9


def test_a_tiny_circle_still_has_points():
    loops, _ = dx.read_dxf(_dxf(_circle(0, 0, 0.05)))
    assert len(loops[0]) >= 4


# ----- the richer DXF that sheet.export_dxf writes ------------------------

def test_tables_and_blocks_do_not_hide_the_entities():
    """sheet.export_dxf writes TABLES, BLOCKS and OBJECTS sections
    whose own code-2 values broke an early version of the section
    tracking: it read the geometry as if it were outside ENTITIES and
    returned two loops instead of four."""
    doc = ((HEADER % 4) +
           "0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n0\nENDTAB\n"
           "0\nENDSEC\n"
           "0\nSECTION\n2\nBLOCKS\n0\nBLOCK\n2\nX\n0\nENDBLK\n"
           "0\nENDSEC\n"
           "0\nSECTION\n2\nENTITIES\n" + _square(40) +
           _circle(20, 20, 4) + "0\nENDSEC\n"
           "0\nSECTION\n2\nOBJECTS\n0\nDICTIONARY\n0\nENDSEC\n0\nEOF\n")
    loops, info = dx.read_dxf(doc)
    assert info["loops"] == 2
    assert len(loops[0]) == 4


def test_insunits_is_read_only_from_the_header():
    """A 9/70 pair elsewhere is a different variable."""
    doc = ((HEADER % 4) +
           "0\nSECTION\n2\nENTITIES\n9\n$INSUNITS\n70\n1\n" +
           _square(10.0) + "0\nENDSEC\n0\nEOF\n")
    _loops, info = dx.read_dxf(doc)
    assert info["units_mm"] == 1.0
