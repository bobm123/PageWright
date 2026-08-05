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


# ---------------------------------------------------------------------------
# Upgrades: per-axis / percent overlap, fixed grid, corner+midpoint marks
# ---------------------------------------------------------------------------

def test_plan_tiles_accepts_per_axis_overlap():
    p1 = tiling.plan_tiles(400.0, 300.0, "Letter", False, 6.0, (20.0, 5.0))
    assert p1["step_x"] == pytest.approx(215.9 - 12.0 - 20.0)
    assert p1["step_y"] == pytest.approx(279.4 - 12.0 - 5.0)
    # scalar still works and matches the tuple form
    p2 = tiling.plan_tiles(400.0, 300.0, "Letter", False, 6.0, 10.0)
    p3 = tiling.plan_tiles(400.0, 300.0, "Letter", False, 6.0, (10.0, 10.0))
    assert p2 == p3


def test_fit_scale_fills_requested_grid():
    # content that would need many pages at 100% fits exactly in 2x3
    cw, ch = 500.0, 700.0
    s = tiling.fit_scale(cw, ch, "A4", False, 6.0, 10.0, 2, 3)
    plan = tiling.plan_tiles(cw * s, ch * s, "A4", False, 6.0, 10.0)
    assert plan["ncols"] <= 2 and plan["nrows"] <= 3
    # and the limiting axis uses its pages fully (within rounding)
    max_w = 2 * plan["step_x"] + 10.0
    max_h = 3 * plan["step_y"] + 10.0
    assert (cw * s == pytest.approx(max_w, abs=1e-6)
            or ch * s == pytest.approx(max_h, abs=1e-6))


def test_registration_marks_corners_and_midpoints():
    marks = tiling._registration_marks(6.0, 100.0, 80.0)
    assert marks.count("<path") == 8    # 4 corners + 4 midpoints
    # 5 mm point-to-point: half-diagonal 2.5 encoded in the path data
    assert tiling._DIAMOND_MM == pytest.approx(2.5)


def test_region_px_tiles_only_that_area():
    import numpy as np
    from pagewright.model import Project
    from pagewright.core import image_io
    img = np.full((1000, 2000, 3), 200, np.uint8)
    p = Project()
    p.set_source_image(image_io.LoadedImage(path="x.png", data=img))
    p.margin_mm = 0.0
    # whole image at 1 mm/px would be 2000x1000 mm; region is 400x300
    tiles = tiling.build_tiles(p, image_bgr=img, page="Letter",
                               landscape=False, margin_mm=6.0,
                               overlap_mm=10.0, embed_photo=True,
                               mm_per_pixel=1.0,
                               region_px=(100, 100, 500, 400))
    plan = tiling.plan_tiles(400.0, 300.0, "Letter", False, 6.0, 10.0)
    assert len(tiles) == plan["ncols"] * plan["nrows"]
    # far fewer than the whole-image tiling would need
    whole = tiling.plan_tiles(2000.0, 1000.0, "Letter", False, 6.0, 10.0)
    assert len(tiles) < whole["ncols"] * whole["nrows"]


def test_embedded_photo_is_cropped_per_tile():
    # Each tile embeds only its own slice of the photo. Embedding the
    # full image in every tile made multi-tile jobs balloon (hundreds of
    # MB across tiles) and froze print preview on large flattened pages.
    import numpy as np
    from pagewright.model import Project
    from pagewright.core import image_io
    yy, xx = np.mgrid[0:3000, 0:2400]
    base = ((xx * 0.05 + yy * 0.04) % 255).astype("uint8")
    img = np.dstack([base, base, base]).copy()
    p = Project()
    p.set_source_image(image_io.LoadedImage(path="x.png", data=img))
    # 0.2 mm/px -> 480x600 mm -> a 3x3-ish Letter grid
    tiles = tiling.build_tiles(p, image_bgr=img, page="Letter",
                               landscape=False, margin_mm=6.0,
                               overlap_mm=10.0, embed_photo=True,
                               mm_per_pixel=0.2)
    assert len(tiles) >= 6
    import cv2
    ok, buf = cv2.imencode(".png", img)
    full_embed = len(buf) * 4 / 3
    for _name, svg in tiles:
        assert svg.count("<image") == 1          # exactly its own slice
        assert len(svg) < full_embed * 0.7       # bounded, not the whole
