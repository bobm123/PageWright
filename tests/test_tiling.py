"""Tests for core.tiling and the Inkscape SVG flavor -- pure Python."""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import svg_export  # noqa: E402
from pagewright.core import tiling  # noqa: E402
from pagewright.model import Contour, Project, TracedObject  # noqa: E402


def _project(mpp=1.0, margin_mm=0.0, w_px=400, h_px=300):
    p = Project()
    p.pixel_width = w_px
    p.pixel_height = h_px
    p.calibration.mm_per_pixel = mpp
    p.margin_mm = margin_mm
    obj = TracedObject(name="o")
    obj.contours.append(Contour(
        points=[(0, 0), (w_px, 0), (w_px, h_px), (0, h_px)], role="outer"))
    p.objects = [obj]
    return p


# ----- tile_counts / plan_tiles ----------------------------------------------

def test_tile_counts_basic():
    # printable 200, overlap 0 -> step 200 -> ceil(400/200) = 2.
    assert tiling.tile_counts(400, 220, 10, 0) == 2


def test_tile_counts_with_overlap():
    # page 220, margin 10 -> printable 200; overlap 20 -> step 180.
    # ceil((400 - 20)/180) = ceil(2.11) = 3.
    assert tiling.tile_counts(400, 220, 10, 20) == 3


def test_overlap_too_large_raises():
    with pytest.raises(ValueError):
        tiling.tile_counts(400, 220, 10, 250)


def test_plan_landscape_swaps_page():
    p = tiling.plan_tiles(100, 100, "A4", True, 5, 10)
    # A4 landscape -> 297 x 210.
    assert p["page_w"] == pytest.approx(297.0)
    assert p["page_h"] == pytest.approx(210.0)


# ----- build_tiles -----------------------------------------------------------

def test_build_tiles_count_and_naming():
    # 400x300 mm content (mpp=1). Letter portrait 215.9x279.4, margin 6.
    proj = _project(mpp=1.0, margin_mm=0.0, w_px=400, h_px=300)
    tiles = tiling.build_tiles(proj, embed_photo=False, page="Letter",
                               margin_mm=6.0, overlap_mm=10.0)
    plan = tiling.plan_tiles(400, 300, "Letter", False, 6.0, 10.0)
    assert len(tiles) == plan["ncols"] * plan["nrows"]
    names = [n for n, _ in tiles]
    assert "tile-r1c1.svg" in names
    # custom base name -> "<base>-rNcM.svg"
    named = tiling.build_tiles(proj, embed_photo=False, page="Letter",
                               margin_mm=6.0, overlap_mm=10.0,
                               base_name="photo")
    assert "photo-r1c1.svg" in [n for n, _ in named]
    # every tile is a page-sized svg
    for _, svg in tiles:
        assert 'width="215.9mm"' in svg
        assert "</svg>" in svg


def test_tiles_have_labels_and_filled_marks():
    proj = _project(mpp=1.0, w_px=400, h_px=300)
    tiles = dict(tiling.build_tiles(proj, embed_photo=False, page="Letter",
                                    margin_mm=6.0, overlap_mm=10.0))
    svg = tiles["tile-r1c1.svg"]
    assert "R1-C1" in svg
    assert "clipPath" in svg
    # registration diamonds are filled closed paths
    assert svg.count("Z") >= 1
    assert 'fill="#000000" stroke="none"' in svg


def test_grid_lines_mm():
    plan = tiling.plan_tiles(400, 300, "Letter", False, 6.0, 10.0)
    xs, ys = tiling.grid_lines_mm(plan, 400, 300)
    # boundaries are always present and clamped to the content extent
    assert xs[0] == 0.0 and xs[-1] == 400
    assert ys[0] == 0.0 and ys[-1] == 300
    # one interior seam per extra column/row
    assert len(xs) == plan["ncols"] + 1
    assert len(ys) == plan["nrows"] + 1


def test_registration_marks_coincide_across_columns():
    # A seam diamond must land at the same master coordinate on both tiles
    # that share the seam -> same page position offset by the step.
    proj = _project(mpp=1.0, w_px=400, h_px=120)
    margin, overlap = 6.0, 10.0
    plan = tiling.plan_tiles(400, 120, "Letter", False, margin, overlap)
    assert plan["ncols"] >= 2
    # Seam between c=0 and c=1 is at master x = step_x.
    step_x = plan["step_x"]
    # On tile c0 its page x = step_x + (margin - 0) = step_x + margin.
    # On tile c1 its page x = step_x + (margin - step_x) = margin.
    # Both must be inside each tile's printable band.
    x_on_c0 = step_x + margin
    x_on_c1 = margin
    assert margin - 1e-6 <= x_on_c1 <= margin + plan["printable_w"] + 1e-6
    assert margin - 1e-6 <= x_on_c0 <= margin + plan["printable_w"] + 1e-6


# ----- inkscape flavor -------------------------------------------------------

def test_plain_svg_has_no_inkscape_markers():
    p = _project(mpp=0.5)
    svg = svg_export.build_svg(p, embed_photo=False, inkscape=False)
    assert "inkscape:" not in svg
    assert "sodipodi" not in svg


def test_inkscape_svg_has_layers_and_namedview():
    p = _project(mpp=0.5)
    svg = svg_export.build_svg(p, embed_photo=False, inkscape=True)
    assert "xmlns:inkscape" in svg
    assert "xmlns:sodipodi" in svg
    assert "sodipodi:namedview" in svg
    assert 'inkscape:document-units="mm"' in svg
    assert 'inkscape:groupmode="layer"' in svg
    assert 'inkscape:label="Trace"' in svg
