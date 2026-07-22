"""Tests for core.svg_export -- pure Python (no cv2 needed when not embedding)."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import svg_export  # noqa: E402
from pagewright.model import Contour, Project, TracedObject  # noqa: E402


def _project_with_square(mpp=0.5, margin_mm=0.0):
    p = Project()
    p.pixel_width = 200
    p.pixel_height = 200
    p.calibration.mm_per_pixel = mpp
    p.margin_mm = margin_mm
    sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
    obj = TracedObject(name="sq")
    obj.contours.append(Contour(points=sq, role="outer"))
    p.objects = [obj]
    return p


def _viewbox(svg):
    m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
    return float(m.group(1)), float(m.group(2))


def test_requires_calibration():
    p = _project_with_square()
    p.calibration.mm_per_pixel = None
    with pytest.raises(svg_export.ExportError):
        svg_export.build_svg(p, embed_photo=False)


def test_requires_some_contours():
    p = Project()
    p.calibration.mm_per_pixel = 0.5
    with pytest.raises(svg_export.ExportError):
        svg_export.build_svg(p, embed_photo=False)


def test_viewbox_no_margin():
    p = _project_with_square(mpp=0.5, margin_mm=0.0)
    svg = svg_export.build_svg(p, embed_photo=False)
    w, h = _viewbox(svg)
    assert w == pytest.approx(50.0)   # 100 px * 0.5 mm/px
    assert h == pytest.approx(50.0)
    assert 'width="50mm"' in svg
    assert 'height="50mm"' in svg


def test_viewbox_with_margin():
    p = _project_with_square(mpp=0.5, margin_mm=10.0)
    svg = svg_export.build_svg(p, embed_photo=False)
    w, h = _viewbox(svg)
    # margin_px = 10 / 0.5 = 20 px each side -> content 100+40 = 140 px * 0.5.
    assert w == pytest.approx(70.0)
    assert h == pytest.approx(70.0)


def test_outer_path_and_style():
    p = _project_with_square()
    svg = svg_export.build_svg(p, embed_photo=False)
    assert 'fill-rule="evenodd"' in svg
    assert 'stroke="#000000"' in svg
    assert svg.count("<path") == 1
    assert "Z" in svg
    # outline mode -> no fill
    assert 'fill="none"' in svg


def test_hole_produces_two_subpaths():
    p = _project_with_square(mpp=0.5)
    hole = [(30, 30), (70, 30), (70, 70), (30, 70)]
    p.objects[0].contours.append(Contour(points=hole, role="hole"))
    svg = svg_export.build_svg(p, embed_photo=False)
    # One compound <path> with two subpaths -> two move commands.
    d = re.search(r'<path d="([^"]+)"', svg).group(1)
    assert d.count("M") == 2
    assert d.count("Z") == 2


def test_embed_without_image_errors():
    p = _project_with_square()
    with pytest.raises(svg_export.ExportError):
        svg_export.build_svg(p, embed_photo=True, image_bgr=None)


def test_filled_mode_sets_fill():
    p = _project_with_square()
    svg = svg_export.build_svg(p, embed_photo=False, filled=True)
    # filled mode uses a non-"none" fill on the path
    path = re.search(r'<path [^>]*/>', svg).group(0)
    assert 'fill="none"' not in path
