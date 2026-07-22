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

_DIAMOND_MM = 3.0          # half-diagonal of a registration diamond
_EPS = 1e-6


def tile_counts(content_mm, page_mm, margin_mm, overlap_mm):
    """Number of tiles needed along one axis (see PLAN.md sec. 7)."""
    printable = page_mm - 2.0 * margin_mm
    if printable <= 0.0:
        raise ValueError("printer margins leave no printable area")
    step = printable - overlap_mm
    if step <= 0.0:
        raise ValueError("overlap is larger than the printable area")
    return max(1, int(math.ceil((content_mm - overlap_mm) / step)))


def plan_tiles(content_w, content_h, page, landscape, margin_mm, overlap_mm):
    """Compute the tiling geometry without rendering.

    Returns a dict with page/printable sizes, step sizes and tile counts.
    """
    if page not in PAGE_SIZES_MM:
        raise ValueError("unknown page size: %r" % (page,))
    pw, ph = PAGE_SIZES_MM[page]
    if landscape:
        pw, ph = ph, pw
    printable_w = pw - 2.0 * margin_mm
    printable_h = ph - 2.0 * margin_mm
    if printable_w <= 0.0 or printable_h <= 0.0:
        raise ValueError("printer margins leave no printable area")
    step_x = printable_w - overlap_mm
    step_y = printable_h - overlap_mm
    if step_x <= 0.0 or step_y <= 0.0:
        raise ValueError("overlap is larger than the printable area")
    ncols = max(1, int(math.ceil((content_w - overlap_mm) / step_x)))
    nrows = max(1, int(math.ceil((content_h - overlap_mm) / step_y)))
    return {
        "page_w": pw, "page_h": ph,
        "printable_w": printable_w, "printable_h": printable_h,
        "step_x": step_x, "step_y": step_y,
        "ncols": ncols, "nrows": nrows,
    }


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
    """Filled diamonds at the midpoint of each edge of the tile's live area.

    Because the live-area edges sit on the master tile-grid lines, the mark on a
    shared edge lands at the same master coordinate on both neighbouring tiles,
    so the diamonds coincide when the printed sheets are overlapped.
    """
    if live_w <= 0.0 or live_h <= 0.0:
        return ""
    cx = margin + live_w / 2.0
    cy = margin + live_h / 2.0
    marks = [
        _diamond(cx, margin),               # top edge
        _diamond(cx, margin + live_h),      # bottom edge
        _diamond(margin, cy),               # left edge
        _diamond(margin + live_w, cy),      # right edge
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
                mm_per_pixel=None, crop_photo=False):
    """Build one page-sized SVG per tile.

    Returns a list of (filename, svg_text); filenames look like
    "<base_name>-r1c1.svg" (row, then column). `mm_per_pixel` overrides the
    project's calibration (for an uncalibrated default size or a scale factor).
    `crop_photo` clips the embedded photo to the content bounding box.
    """
    content, content_w, content_h = svg_export.build_content(
        project, image_bgr=image_bgr, embed_photo=embed_photo,
        downscale_max=downscale_max, filled=filled, as_layers=False,
        mm_per_pixel=mm_per_pixel, crop_photo=crop_photo)

    plan = plan_tiles(content_w, content_h, page, landscape,
                      margin_mm, overlap_mm)

    tiles = []
    for r in range(plan["nrows"]):
        for c in range(plan["ncols"]):
            name = "%s-r%dc%d.svg" % (base_name, r + 1, c + 1)
            tiles.append((name, _tile_svg(content, plan, r, c,
                                          content_w, content_h)))
    return tiles
