"""Tiled large-format printing (Phase 5).

Splits the master, millimetre-sized drawing into page-sized tiles with overlap
(see PLAN.md sec. 7). Each tile is a full-page SVG containing:

  * the correct slice of the master content (the master fragment translated and
    clipped to the page's printable area);
  * a rectangle outlining this tile's live (non-overlap) area;
  * a grid label (e.g. R2-C3);
  * a filled diamond at the midpoint of each live-area edge, placed so a mark on
    a shared edge coincides on both neighbouring tiles when overlapped.

True 1:1 output depends on printing with auto-scaling / "fit to page" OFF; the
registration diamonds double as a scale check.

Pure arithmetic + string building, so it ports cleanly to C/C++.
"""

import math

from . import svg_export
from .svg_export import _num

# Standard page sizes in millimetres (portrait).
PAGE_SIZES_MM = {
    "Letter": (215.9, 279.4),
    "A4": (210.0, 297.0),
    "Legal": (215.9, 355.6),
    "A3": (297.0, 420.0),
}

_DIAMOND_MM = 2.5          # half-diagonal: 5 mm point-to-point diamonds
_EPS = 1e-6


def _overlaps(overlap_mm):
    """Normalise an overlap spec to (overlap_x, overlap_y) in mm.
    A scalar applies to both axes; a (x, y) pair is used as-is."""
    try:
        ox, oy = overlap_mm
        return float(ox), float(oy)
    except TypeError:
        v = float(overlap_mm)
        return v, v


def tile_counts(content_mm, page_mm, margin_mm, overlap_mm):
    """Number of tiles needed along one axis (see PLAN.md sec. 7)."""
    printable = page_mm - 2.0 * margin_mm
    if printable <= 0.0:
        raise ValueError("printer margins leave no printable area")
    step = printable - float(overlap_mm)
    if step <= 0.0:
        raise ValueError("overlap is larger than the printable area")
    return max(1, int(math.ceil((content_mm - float(overlap_mm)) / step)))


def plan_tiles(content_w, content_h, page, landscape, margin_mm, overlap_mm):
    """Compute the tiling geometry without rendering.

    overlap_mm may be a scalar or an (x, y) pair (percent-of-page overlaps
    resolve to different mm per axis). Returns a dict with page/printable
    sizes, step sizes and tile counts.
    """
    if page not in PAGE_SIZES_MM:
        raise ValueError("unknown page size: %r" % (page,))
    pw, ph = PAGE_SIZES_MM[page]
    if landscape:
        pw, ph = ph, pw
    ov_x, ov_y = _overlaps(overlap_mm)
    printable_w = pw - 2.0 * margin_mm
    printable_h = ph - 2.0 * margin_mm
    if printable_w <= 0.0 or printable_h <= 0.0:
        raise ValueError("printer margins leave no printable area")
    step_x = printable_w - ov_x
    step_y = printable_h - ov_y
    if step_x <= 0.0 or step_y <= 0.0:
        raise ValueError("overlap is larger than the printable area")
    ncols = max(1, int(math.ceil((content_w - ov_x) / step_x)))
    nrows = max(1, int(math.ceil((content_h - ov_y) / step_y)))
    return {
        "page_w": pw, "page_h": ph,
        "printable_w": printable_w, "printable_h": printable_h,
        "step_x": step_x, "step_y": step_y,
        "ncols": ncols, "nrows": nrows,
    }


def fit_scale(content_w, content_h, page, landscape, margin_mm, overlap_mm,
              ncols, nrows):
    """Largest scale factor at which the content fits an EXPLICIT grid of
    ncols x nrows pages (the 'number of repeats' mode). content_w/h are
    the unscaled content size in mm."""
    plan = plan_tiles(1.0, 1.0, page, landscape, margin_mm, overlap_mm)
    ov_x, ov_y = _overlaps(overlap_mm)
    max_w = ncols * plan["step_x"] + ov_x
    max_h = nrows * plan["step_y"] + ov_y
    if content_w <= 0.0 or content_h <= 0.0:
        raise ValueError("content has no size")
    return min(max_w / content_w, max_h / content_h)


def grid_lines_mm(plan, content_w, content_h):
    """Master-mm positions of the tile (live-area) grid lines.

    Returns (xs, ys): sorted, de-duplicated line positions clamped to the
    content extent, including the 0 and content_w/content_h boundaries. Used by
    the GUI to draw a tile-grid preview overlay.
    """
    step_x, step_y = plan["step_x"], plan["step_y"]
    xs = {0.0, content_w}
    for k in range(plan["ncols"] + 1):
        xs.add(min(content_w, k * step_x))
    ys = {0.0, content_h}
    for j in range(plan["nrows"] + 1):
        ys.add(min(content_h, j * step_y))
    return sorted(xs), sorted(ys)


def _diamond(cx, cy):
    """A small filled diamond (rotated square) centred at (cx, cy), in mm."""
    d = _DIAMOND_MM
    pts = [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)]
    body = " ".join(
        "%s %s %s" % ("M" if i == 0 else "L", _num(x), _num(y))
        for i, (x, y) in enumerate(pts))
    return ('    <path d="%s Z" fill="#000000" stroke="none" />\n' % body)


def _registration_marks(margin, live_w, live_h):
    """5 mm filled diamonds at the CORNERS and edge MIDPOINTS of the
    tile's live area (the overlap frame).

    Because the live-area edges sit on the master tile-grid lines, a mark
    on a shared edge lands at the same master coordinate on both
    neighbouring tiles, so the diamonds coincide when the printed sheets
    are overlapped - corners register two axes at once.
    """
    if live_w <= 0.0 or live_h <= 0.0:
        return ""
    x0, x1 = margin, margin + live_w
    y0, y1 = margin, margin + live_h
    cx = margin + live_w / 2.0
    cy = margin + live_h / 2.0
    marks = [
        _diamond(x0, y0), _diamond(x1, y0),     # top corners
        _diamond(x0, y1), _diamond(x1, y1),     # bottom corners
        _diamond(cx, y0), _diamond(cx, y1),     # top/bottom midpoints
        _diamond(x0, cy), _diamond(x1, cy),     # left/right midpoints
    ]
    return "".join(marks)


def _tile_svg(content, plan, r, c, content_w, content_h):
    pw, ph = plan["page_w"], plan["page_h"]
    printable_w, printable_h = plan["printable_w"], plan["printable_h"]
    step_x, step_y = plan["step_x"], plan["step_y"]
    margin = (pw - printable_w) / 2.0

    dx = margin - c * step_x
    dy = margin - r * step_y

    live_w = min(step_x, content_w - c * step_x)
    live_h = min(step_y, content_h - r * step_y)
    live_w = max(0.0, live_w)
    live_h = max(0.0, live_h)

    clip_id = "clip_r%dc%d" % (r + 1, c + 1)
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="%smm" height="%smm" viewBox="0 0 %s %s">\n'
        % (_num(pw), _num(ph), _num(pw), _num(ph)))
    out.append('  <defs><clipPath id="%s">'
               '<rect x="%s" y="%s" width="%s" height="%s" /></clipPath>'
               '</defs>\n'
               % (clip_id, _num(margin), _num(margin),
                  _num(printable_w), _num(printable_h)))

    # Master content, translated into this tile and clipped to the page.
    out.append('  <g clip-path="url(#%s)">\n' % clip_id)
    out.append('    <g transform="translate(%s,%s)">\n' % (_num(dx), _num(dy)))
    out.append(content)
    out.append('    </g>\n')
    out.append('  </g>\n')

    # Live (non-overlap) area outline.
    out.append(
        '  <rect x="%s" y="%s" width="%s" height="%s" fill="none" '
        'stroke="#ff00ff" stroke-width="0.2" stroke-dasharray="2,2" />\n'
        % (_num(margin), _num(margin), _num(live_w), _num(live_h)))

    # Registration diamonds at the midpoint of each live-area edge.
    out.append(_registration_marks(margin, live_w, live_h))

    # Grid label.
    out.append(
        '  <text x="%s" y="%s" font-family="sans-serif" font-size="4" '
        'fill="#ff00ff">R%d-C%d</text>\n'
        % (_num(margin + 1.5), _num(margin + 5.0), r + 1, c + 1))

    out.append('</svg>\n')
    return "".join(out)


def build_tiles(project, image_bgr=None, page="Letter", landscape=False,
                margin_mm=6.0, overlap_mm=10.0, embed_photo=True,
                downscale_max=None, filled=False, base_name="tile",
                mm_per_pixel=None, crop_photo=False, region_px=None):
    """Build one page-sized SVG per tile.

    Returns a list of (filename, svg_text); filenames look like
    "<base_name>-r1c1.svg" (row, then column). `mm_per_pixel` overrides the
    project's calibration (for an uncalibrated default size or a scale factor).
    `crop_photo` clips the embedded photo to the content bounding box.
    `region_px` = (x0, y0, x1, y1) tiles exactly that image region (the
    Select Area rectangle) instead of the trace bbox / whole image.

    When the photo is embedded, each tile embeds ONLY the slice of the
    photo its page shows (plus a small bleed). A tile SVG therefore stays
    about one page's worth of pixels no matter how large the source
    image is - embedding the full photo into every tile made multi-tile
    jobs balloon to hundreds of MB and hang print preview/rendering.
    """
    embed = embed_photo and image_bgr is not None
    # Vector-only master content (traces); photo slices are added per
    # tile below. allow_image_bbox keeps the image-only case working.
    content_vec, content_w, content_h, geom = svg_export.build_content(
        project, image_bgr=None, embed_photo=False,
        downscale_max=None, filled=filled, as_layers=False,
        mm_per_pixel=mm_per_pixel, region_px=region_px,
        allow_image_bbox=embed, return_geometry=True)

    plan = plan_tiles(content_w, content_h, page, landscape,
                      margin_mm, overlap_mm)

    ox, oy, mpp = geom["ox"], geom["oy"], geom["mpp"]
    img_h = image_bgr.shape[0] if embed else 0
    img_w = image_bgr.shape[1] if embed else 0
    pw, ph = plan["page_w"], plan["page_h"]
    margin = (pw - plan["printable_w"]) / 2.0
    bleed_mm = 3.0

    tiles = []
    for r in range(plan["nrows"]):
        for c in range(plan["ncols"]):
            content = content_vec
            if embed:
                # master-mm rect this page shows, with bleed
                x0m = c * plan["step_x"] - margin - bleed_mm
                y0m = r * plan["step_y"] - margin - bleed_mm
                x1m = x0m + pw + 2.0 * bleed_mm
                y1m = y0m + ph + 2.0 * bleed_mm
                # -> image px, clamped
                cx0 = max(0, int(math.floor(x0m / mpp + ox)))
                cy0 = max(0, int(math.floor(y0m / mpp + oy)))
                cx1 = min(img_w, int(math.ceil(x1m / mpp + ox)))
                cy1 = min(img_h, int(math.ceil(y1m / mpp + oy)))
                if cx1 > cx0 and cy1 > cy0:
                    sub = image_bgr[cy0:cy1, cx0:cx1]
                    tag = svg_export._photo_image_tag(
                        sub, cx0, cy0, cx1 - cx0, cy1 - cy0,
                        ox, oy, mpp, downscale_max)
                    content = ('  <g id="photo">\n%s  </g>\n' % tag
                               ) + content_vec
            name = "%s-r%dc%d.svg" % (base_name, r + 1, c + 1)
            tiles.append((name, _tile_svg(content, plan, r, c,
                                          content_w, content_h)))
    return tiles
