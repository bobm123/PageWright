"""SVG import: units, path grammar, transforms, layers (core, GUI-free).

The point of these tests is DIMENSIONAL correctness: an imported drawing
must come out at its real-world size, because the whole feature exists to
print existing art at 1:1. Shape counts matter much less than millimetres.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import svg_import        # noqa: E402


def _write(tmp_path, body, name="t.svg"):
    fp = tmp_path / name
    fp.write_text(body, encoding="utf-8")
    return str(fp)


def _doc(inner, width="100mm", height="100mm", viewbox="0 0 100 100"):
    vb = ' viewBox="%s"' % viewbox if viewbox else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%s" '
            'height="%s"%s>%s</svg>' % (width, height, vb, inner))


# ----- units ---------------------------------------------------------------

def test_parse_length_mm_handles_absolute_units():
    f = svg_import.parse_length_mm
    assert f("10mm") == pytest.approx(10.0)
    assert f("1cm") == pytest.approx(10.0)
    assert f("1in") == pytest.approx(25.4)
    assert f("72pt") == pytest.approx(25.4)
    assert f("96px") == pytest.approx(25.4)
    assert f("96") == pytest.approx(25.4)        # unitless == px
    assert f(None) is None
    assert f("plainly not a length") is None
    # percentages need a reference length
    assert f("50%") is None
    assert f("50%", percent_of=80.0) == pytest.approx(40.0)


def test_millimetre_document_imports_at_true_size(tmp_path):
    fp = _write(tmp_path, _doc('<rect x="0" y="0" width="100" height="50"/>',
                               width="100mm", height="50mm",
                               viewbox="0 0 100 50"))
    objects, size_mm, mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((100.0, 50.0))
    assert mpp == pytest.approx(1.0 / svg_import.PX_PER_MM)
    assert len(objects) == 1
    # a plain rect is 4 corners (the closing point is dropped)
    assert len(objects[0].contours[0].points) == 4


def test_pixel_document_converts_at_96_dpi(tmp_path):
    fp = _write(tmp_path, _doc('<rect width="96" height="96"/>',
                               width="96", height="96", viewbox=None))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((25.4, 25.4))


def test_viewbox_scales_user_units_to_the_physical_size(tmp_path):
    # 200 user units displayed across 100 mm -> 0.5 mm per unit
    fp = _write(tmp_path, _doc('<rect width="200" height="100"/>',
                               width="100mm", height="100mm",
                               viewbox="0 0 200 200"))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((100.0, 50.0))


def test_viewbox_origin_is_subtracted(tmp_path):
    # content sits at 50..150 in a viewBox starting at 50 -> 0..100
    fp = _write(tmp_path, _doc('<rect x="50" y="50" width="100" height="100"/>',
                               width="200mm", height="200mm",
                               viewbox="50 50 200 200"))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((100.0, 100.0))


# ----- path grammar --------------------------------------------------------

def test_absolute_and_relative_line_commands(tmp_path):
    fp = _write(tmp_path, _doc('<path d="m 10 10 h 50 v 30 l -50 0 z"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((50.0, 30.0))


def test_cubic_and_quadratic_curves_are_flattened(tmp_path):
    fp = _write(tmp_path, _doc(
        '<path d="M 0 50 C 0 0 100 0 100 50 Z"/>'))
    objects, size_mm, _mpp = svg_import.import_svg(fp)
    pts = objects[0].contours[0].points
    assert len(pts) > 10                      # actually sampled, not a chord
    assert size_mm[0] == pytest.approx(100.0)

    fp2 = _write(tmp_path, _doc('<path d="M 0 0 Q 50 100 100 0 Z"/>'),
                 name="q.svg")
    objects2, _size2, _mpp2 = svg_import.import_svg(fp2)
    assert len(objects2[0].contours[0].points) > 10


def test_smooth_curve_reflects_the_previous_control_point(tmp_path):
    # S after C must mirror C's second control point; a wrong reflection
    # collapses the curve towards a straight line, shrinking the bbox.
    fp = _write(tmp_path, _doc(
        '<path d="M 0 50 C 20 0 40 0 50 50 S 80 100 100 50 Z"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm[0] == pytest.approx(100.0)
    assert size_mm[1] > 40.0          # the S arc bulges well below y=50


def test_elliptical_arc_is_sampled(tmp_path):
    # half-circle of radius 40 -> 80 mm wide, 40 mm tall
    fp = _write(tmp_path, _doc('<path d="M 10 50 A 40 40 0 1 1 90 50 Z"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((80.0, 40.0), abs=0.2)


def test_multiple_subpaths_become_multiple_contours(tmp_path):
    fp = _write(tmp_path, _doc(
        '<path d="M 0 0 H 40 V 40 H 0 Z M 50 50 H 90 V 90 H 50 Z"/>'))
    objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert len(objects[0].contours) == 2
    assert size_mm == pytest.approx((90.0, 90.0))


# ----- shape elements ------------------------------------------------------

def test_basic_shapes_import(tmp_path):
    fp = _write(tmp_path, _doc(
        '<rect x="0" y="0" width="20" height="10"/>'
        '<circle cx="50" cy="50" r="10"/>'
        '<ellipse cx="80" cy="20" rx="10" ry="5"/>'
        '<polygon points="0,90 20,90 10,70"/>'
        '<polyline points="30,90 50,90 50,70"/>'))
    objects, _size, _mpp = svg_import.import_svg(fp)
    total = sum(len(o.contours) for o in objects)
    assert total == 5


def test_circle_flattening_stays_within_tolerance():
    # The sagitta of each chord must not exceed FLATTEN_TOL_MM, or
    # printed curves would visibly facet.
    for r in (1.0, 5.0, 10.0, 50.0, 200.0):
        pts = svg_import._ellipse_points(0.0, 0.0, r, r, 1.0)
        n = len(pts)
        worst = 0.0
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            mid = math.hypot((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            worst = max(worst, r - mid)
        assert worst <= svg_import.FLATTEN_TOL_MM + 1e-9, r


def test_rounded_rect_keeps_its_outer_size(tmp_path):
    fp = _write(tmp_path, _doc(
        '<rect x="0" y="0" width="80" height="40" rx="10" ry="10"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((80.0, 40.0), abs=0.1)


def test_degenerate_shapes_are_skipped(tmp_path):
    fp = _write(tmp_path, _doc(
        '<rect x="0" y="0" width="0" height="10"/>'      # zero width
        '<circle cx="5" cy="5" r="0"/>'                  # zero radius
        '<rect x="0" y="0" width="30" height="30"/>'))   # the real one
    objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert sum(len(o.contours) for o in objects) == 1
    assert size_mm == pytest.approx((30.0, 30.0))


# ----- transforms ----------------------------------------------------------

def test_nested_transforms_compose(tmp_path):
    fp = _write(tmp_path, _doc(
        '<g transform="translate(10,10)">'
        '<rect x="0" y="0" width="20" height="20" transform="scale(2)"/>'
        '</g>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((40.0, 40.0))


def test_rotate_about_a_point(tmp_path):
    # a 90-degree rotation of a 40x10 bar gives a 10x40 bbox
    fp = _write(tmp_path, _doc(
        '<rect x="0" y="0" width="40" height="10" '
        'transform="rotate(90 20 5)"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((10.0, 40.0), abs=0.01)


def test_matrix_transform(tmp_path):
    fp = _write(tmp_path, _doc(
        '<rect x="0" y="0" width="10" height="10" '
        'transform="matrix(3 0 0 2 5 5)"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((30.0, 20.0))


# ----- document structure --------------------------------------------------

def test_inkscape_layers_become_named_objects(tmp_path):
    fp = _write(tmp_path,
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                'width="100mm" height="100mm" viewBox="0 0 100 100">'
                '<g inkscape:groupmode="layer" inkscape:label="Outline">'
                '<rect width="60" height="60"/></g>'
                '<g inkscape:groupmode="layer" inkscape:label="Detail">'
                '<circle cx="50" cy="50" r="20"/>'
                '<rect x="70" y="70" width="10" height="10"/></g>'
                '</svg>')
    objects, _size, _mpp = svg_import.import_svg(fp)
    assert [o.name for o in objects] == ["Outline", "Detail"]
    assert len(objects[1].contours) == 2      # grouped under one layer


def test_defs_and_metadata_are_ignored(tmp_path):
    # A huge shape inside <defs> is a template, not content: if it leaked
    # into the import it would blow up the page size.
    fp = _write(tmp_path, _doc(
        '<defs><rect width="9999" height="9999"/></defs>'
        '<metadata>ignored</metadata>'
        '<rect width="40" height="40"/>'))
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((40.0, 40.0))


def test_hidden_elements_are_skipped_by_default(tmp_path):
    body = _doc(
        '<g style="display:none"><rect width="90" height="90"/></g>'
        '<rect width="30" height="30"/>')
    fp = _write(tmp_path, body)
    _objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert size_mm == pytest.approx((30.0, 30.0))
    # ...but can be included on request
    _o2, size2, _m2 = svg_import.import_svg(fp, skip_hidden=False)
    assert size2 == pytest.approx((90.0, 90.0))


def test_content_is_translated_to_the_origin(tmp_path):
    fp = _write(tmp_path, _doc('<rect x="60" y="70" width="20" height="20"/>'))
    objects, _size, _mpp = svg_import.import_svg(fp)
    xs = [p[0] for p in objects[0].contours[0].points]
    ys = [p[1] for p in objects[0].contours[0].points]
    assert min(xs) == pytest.approx(0.0)
    assert min(ys) == pytest.approx(0.0)


# ----- failure modes -------------------------------------------------------

def test_non_svg_root_is_rejected(tmp_path):
    fp = _write(tmp_path, "<html><body>nope</body></html>")
    with pytest.raises(svg_import.SvgImportError):
        svg_import.import_svg(fp)


def test_malformed_xml_is_rejected(tmp_path):
    fp = _write(tmp_path, "<svg><rect")
    with pytest.raises(svg_import.SvgImportError):
        svg_import.import_svg(fp)


def test_svg_with_nothing_printable_is_rejected(tmp_path):
    # text and images carry no geometry this model can print
    fp = _write(tmp_path, _doc('<text x="10" y="10">hello</text>'))
    with pytest.raises(svg_import.SvgImportError):
        svg_import.import_svg(fp)


# ----- the actual point: tiling an imported drawing ------------------------

def test_imported_drawing_tiles_at_true_size(tmp_path):
    from pagewright.core import tiling
    from pagewright.model import Project
    fp = _write(tmp_path, _doc('<rect width="400" height="300"/>',
                               width="400mm", height="300mm",
                               viewbox="0 0 400 300"))
    objects, size_mm, mpp = svg_import.import_svg(fp)
    project = Project()
    project.objects = objects
    project.calibration.mm_per_pixel = mpp
    project.pixel_width = int(round(size_mm[0] / mpp))
    project.pixel_height = int(round(size_mm[1] / mpp))

    tiles = tiling.build_tiles(project, image_bgr=None, embed_photo=False,
                               page="Letter", margin_mm=6.0,
                               overlap_mm=10.0, base_name="art")
    # 400x300 mm of content cannot fit one Letter sheet
    assert len(tiles) > 1
    names = [n for n, _ in tiles]
    assert "art-r1c1.svg" in names
    # every tile is a true Letter page
    for _name, svg in tiles:
        assert 'width="215.9mm"' in svg
        assert 'height="279.4mm"' in svg
    # and the geometry actually made it in
    assert any("<path" in svg for _n, svg in tiles)


def test_pagewright_guide_layers_are_skipped(tmp_path):
    # Re-opening our own export must bring back the artwork, not the
    # tile grid / rulers drawn over it. Those layers also extend PAST
    # the trace, so including them would inflate the page size.
    fp = _write(tmp_path,
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                'width="100mm" height="100mm" viewBox="0 0 100 100">'
                '<g id="trace" inkscape:label="Trace">'
                '<rect x="10" y="10" width="40" height="40"/></g>'
                '<g id="annotations" inkscape:label="Annotations">'
                '<rect x="0" y="0" width="100" height="100"/></g>'
                '<g id="measurements" inkscape:label="Measurements">'
                '<rect x="0" y="0" width="90" height="90"/></g>'
                '</svg>')
    objects, size_mm, _mpp = svg_import.import_svg(fp)
    assert [o.name for o in objects] == ["Trace"]
    assert size_mm == pytest.approx((40.0, 40.0))
    # ...and can be brought back deliberately
    objects2, size2, _m2 = svg_import.import_svg(fp, skip_guides=False)
    assert len(objects2) == 3
    assert size2 == pytest.approx((100.0, 100.0))


def test_round_trip_through_our_own_export_keeps_the_size(tmp_path):
    # Export a known 80x60 mm trace, re-import it, and get the same
    # content size back (the exported document adds margin_mm per side).
    from pagewright.core import svg_export
    from pagewright.model import Contour, Project, TracedObject
    project = Project()
    project.pixel_width, project.pixel_height = 200, 200
    project.calibration.mm_per_pixel = 1.0        # 1 px == 1 mm
    project.margin_mm = 5.0
    obj = TracedObject(name="Shape")
    obj.contours = [Contour(points=[(10, 10), (90, 10), (90, 70), (10, 70)])]
    project.objects = [obj]

    out = tmp_path / "exported.svg"
    out.write_text(svg_export.build_svg(project, embed_photo=False,
                                        inkscape=True), encoding="utf-8")
    _objects, size_mm, _mpp = svg_import.import_svg(str(out))
    assert size_mm == pytest.approx((80.0, 60.0), abs=0.05)
