"""Import an existing SVG file as traced objects (vector-only job).

Why this exists: the tiled-print machinery works on `model.Project`
objects - closed polylines in pixel coordinates plus a mm/px scale. A
drawing that is ALREADY vector art (an Inkscape file, a downloaded
pattern, a CAD export) therefore only needs its paths converted into
Contours and its document size turned into a calibration, and the whole
existing print/preview/export pipeline applies unchanged.

Two consequences of the target model are worth stating up front:

  * Curves are FLATTENED. Contour stores points, and `svg_export._path_d`
    emits "M ... L ... Z", so Beziers and arcs are sampled into line
    segments (see `FLATTEN_TOL_MM`). At print resolution the difference
    is invisible; a re-exported file, however, has polylines where the
    original had curve handles. That is a deliberate trade, not an
    oversight.
  * Only CLOSED shapes print. `_path_d` always closes a subpath and
    skips anything with fewer than 3 points, so open paths are imported
    as closed polylines (stroke-only art still prints correctly) and
    degenerate ones are dropped.

Coordinates: SVG user units are converted to MILLIMETRES using the
document's width/height + viewBox, then stored as "pixels" at a fixed
`PX_PER_MM`, so the project's mm_per_pixel is exact and the rest of the
app needs no special case for vector jobs.

GUI-free: standard library only (xml.etree + math), no new dependency,
and no Python-only idioms that would block the C++ port.
"""

import math
import re
import xml.etree.ElementTree as ET

from ..model import Contour, Style, TracedObject

SVG_NS = "http://www.w3.org/2000/svg"

# Internal raster-equivalent resolution for the stored "pixel" coords.
# 10 px/mm keeps integer-ish numbers and sub-0.1 mm precision; the exact
# value is irrelevant to output because mm_per_pixel is its reciprocal.
PX_PER_MM = 10.0

# Chord tolerance when sampling curves, in millimetres. 0.05 mm is finer
# than any printer dot and far finer than the 0.4 mm stroke widths used
# in output, so flattening is invisible on paper.
FLATTEN_TOL_MM = 0.05

_MIN_SEGS = 4           # per curve, even when it looks flat
_MAX_SEGS = 256         # guard against absurd tolerances/huge curves


class SvgImportError(Exception):
    pass


# ----- unit handling --------------------------------------------------------

# Absolute CSS/SVG units in mm. "px" is the CSS reference pixel: 96 per
# inch, which is also what Inkscape >=1.0 and browsers assume.
_UNITS_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "q": 25.4 / 101.6,          # quarter-millimetre
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0,
    "": 25.4 / 96.0,            # unitless = user units = px
}

_LEN_RE = re.compile(r"^\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
                     r"\s*([a-zA-Z%]*)\s*$")


def parse_length_mm(text, percent_of=None):
    """SVG length -> millimetres. None/'' -> None.

    `percent_of` supplies the reference length (mm) for '%' values; a
    percentage without one is unresolvable and returns None."""
    if text is None:
        return None
    m = _LEN_RE.match(str(text))
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "%":
        if percent_of is None:
            return None
        return value / 100.0 * percent_of
    if unit not in _UNITS_MM:
        return None
    return value * _UNITS_MM[unit]


def _parse_viewbox(text):
    """'min-x min-y width height' -> 4 floats, or None."""
    if not text:
        return None
    parts = [p for p in re.split(r"[\s,]+", text.strip()) if p]
    if len(parts) != 4:
        return None
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if vals[2] <= 0.0 or vals[3] <= 0.0:
        return None
    return vals


def document_scale(root):
    """Work out how to map SVG user units to millimetres.

    Returns (mm_per_unit_x, mm_per_unit_y, offset_x, offset_y) where the
    offsets are the viewBox origin in user units (subtracted before
    scaling, so content starts at 0,0).

    The rules follow the SVG sizing model: with both a physical
    width/height and a viewBox, the viewBox defines the user-unit extent
    that those physical dimensions cover; with only a viewBox, user
    units are CSS px; with only width/height, user units are px too.
    """
    vb = _parse_viewbox(root.get("viewBox"))
    w_mm = parse_length_mm(root.get("width"))
    h_mm = parse_length_mm(root.get("height"))
    if vb is not None:
        ox, oy, vw, vh = vb
        sx = (w_mm / vw) if w_mm else _UNITS_MM["px"]
        sy = (h_mm / vh) if h_mm else _UNITS_MM["px"]
        # A viewBox with only one physical dimension keeps aspect ratio.
        if w_mm and not h_mm:
            sy = sx
        elif h_mm and not w_mm:
            sx = sy
        return sx, sy, ox, oy
    px = _UNITS_MM["px"]
    return px, px, 0.0, 0.0


# ----- transforms -----------------------------------------------------------
# A 2x3 affine as a flat tuple (a, b, c, d, e, f):
#   x' = a*x + c*y + e
#   y' = b*x + d*y + f

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_XFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")


def mat_mul(m, n):
    """Compose: apply `n` first, then `m`."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(text):
    """SVG transform attribute -> 2x3 matrix (left-to-right composition)."""
    if not text:
        return IDENTITY
    out = IDENTITY
    for name, args in _XFORM_RE.findall(text):
        nums = [float(v) for v in
                re.split(r"[\s,]+", args.strip()) if v]
        key = name.strip().lower()
        if key == "matrix" and len(nums) == 6:
            m = tuple(nums)
        elif key == "translate" and nums:
            tx = nums[0]
            ty = nums[1] if len(nums) > 1 else 0.0
            m = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif key == "scale" and nums:
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else nums[0]
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif key == "rotate" and nums:
            ang = math.radians(nums[0])
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            r = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                m = mat_mul(mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), r),
                            (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                m = r
        elif key == "skewx" and nums:
            m = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif key == "skewy" and nums:
            m = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        out = mat_mul(out, m)
    return out


# ----- path data parsing ----------------------------------------------------

_NUM_RE = re.compile(r"[+-]?(?:[0-9]*\.[0-9]+|[0-9]+\.?)(?:[eE][+-]?[0-9]+)?")
_CMD_RE = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]")


def _tokenize_path(d):
    """Path data -> [(command_letter, [numbers...]), ...]."""
    out = []
    i = 0
    n = len(d)
    while i < n:
        ch = d[i]
        if _CMD_RE.match(ch):
            cmd = ch
            i += 1
            nums = []
            while i < n:
                if _CMD_RE.match(d[i]):
                    break
                m = _NUM_RE.match(d, i)
                if m:
                    nums.append(float(m.group(0)))
                    i = m.end()
                else:
                    i += 1       # skip separators / stray characters
            out.append((cmd, nums))
        else:
            i += 1
    return out


def _flatten_steps(points_mm_span):
    """Segment count for a curve whose control polygon spans this many mm."""
    if points_mm_span <= 0.0:
        return _MIN_SEGS
    n = int(math.ceil(math.sqrt(points_mm_span / FLATTEN_TOL_MM)))
    return max(_MIN_SEGS, min(_MAX_SEGS, n))


def _cubic(p0, p1, p2, p3, steps):
    """Sample a cubic Bezier, excluding the start point."""
    out = []
    for k in range(1, steps + 1):
        t = float(k) / steps
        u = 1.0 - t
        bx = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
              + 3 * u * t * t * p2[0] + t * t * t * p3[0])
        by = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
              + 3 * u * t * t * p2[1] + t * t * t * p3[1])
        out.append((bx, by))
    return out


def _quad(p0, p1, p2, steps):
    out = []
    for k in range(1, steps + 1):
        t = float(k) / steps
        u = 1.0 - t
        bx = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        by = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        out.append((bx, by))
    return out


def _arc(p0, rx, ry, phi_deg, large_arc, sweep, p1, steps_hint):
    """Endpoint-parameterised elliptical arc -> sampled points.

    Implements the SVG F.6.5 conversion to centre parameterisation.
    Degenerate radii collapse to a straight line, per the spec."""
    x0, y0 = p0
    x1, y1 = p1
    if rx == 0.0 or ry == 0.0:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    # F.6.5.1: midpoint in the ellipse's own frame
    dx2 = (x0 - x1) / 2.0
    dy2 = (y0 - y1) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2
    # F.6.6: scale radii up if they cannot span the chord
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    # F.6.5.2: centre in the ellipse frame
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if den == 0.0:
        return [p1]
    coef = math.sqrt(max(0.0, num / den))
    if large_arc == sweep:
        coef = -coef
    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)
    # F.6.5.3: centre back in user space
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) / 2.0
    # F.6.5.5/6: start angle and sweep
    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        len_u = math.hypot(ux, uy)
        len_v = math.hypot(vx, vy)
        if len_u == 0.0 or len_v == 0.0:
            return 0.0
        c = max(-1.0, min(1.0, dot / (len_u * len_v)))
        a = math.acos(c)
        if ux * vy - uy * vx < 0.0:
            a = -a
        return a

    theta1 = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                   (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0.0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0.0:
        dtheta += 2.0 * math.pi

    steps = max(_MIN_SEGS, min(_MAX_SEGS, steps_hint))
    out = []
    for k in range(1, steps + 1):
        th = theta1 + dtheta * (float(k) / steps)
        ex = rx * math.cos(th)
        ey = ry * math.sin(th)
        out.append((cos_p * ex - sin_p * ey + cx,
                    sin_p * ex + cos_p * ey + cy))
    return out


def path_to_subpaths(d, span_hint_mm=100.0):
    """Path data -> [[(x, y), ...], ...] in USER UNITS.

    Each inner list is one subpath; curves are flattened. `span_hint_mm`
    scales the segment count (how many mm a user unit spans) so the
    flattening tolerance means the same thing regardless of document
    units."""
    subs = []
    cur = []
    start = (0.0, 0.0)
    pos = (0.0, 0.0)
    prev_ctrl = None        # for S/T reflection
    prev_cmd = ""

    def flush():
        if len(cur) >= 2:
            subs.append(list(cur))

    for cmd, nums in _tokenize_path(d):
        rel = cmd.islower()
        up = cmd.upper()
        if up == "M":
            # First pair moves; any extras are implicit L commands.
            for k in range(0, len(nums) - 1, 2):
                x, y = nums[k], nums[k + 1]
                if rel:
                    x += pos[0]
                    y += pos[1]
                if k == 0:
                    flush()
                    cur = [(x, y)]
                    start = (x, y)
                else:
                    cur.append((x, y))
                pos = (x, y)
            prev_ctrl = None
        elif up == "Z":
            if cur:
                cur.append(start)       # explicit close
                flush()
                cur = []
                pos = start
            prev_ctrl = None
        elif up == "L":
            for k in range(0, len(nums) - 1, 2):
                x, y = nums[k], nums[k + 1]
                if rel:
                    x += pos[0]
                    y += pos[1]
                cur.append((x, y))
                pos = (x, y)
            prev_ctrl = None
        elif up in ("H", "V"):
            for v in nums:
                if up == "H":
                    x = pos[0] + v if rel else v
                    y = pos[1]
                else:
                    x = pos[0]
                    y = pos[1] + v if rel else v
                cur.append((x, y))
                pos = (x, y)
            prev_ctrl = None
        elif up in ("C", "S"):
            stride = 6 if up == "C" else 4
            for k in range(0, len(nums) - stride + 1, stride):
                seg = nums[k:k + stride]
                if up == "C":
                    c1 = (seg[0], seg[1])
                    c2 = (seg[2], seg[3])
                    end = (seg[4], seg[5])
                else:
                    # S: first control is the reflection of the previous
                    if prev_cmd.upper() in ("C", "S") and prev_ctrl:
                        c1 = (2 * pos[0] - prev_ctrl[0],
                              2 * pos[1] - prev_ctrl[1])
                    else:
                        c1 = pos
                    c2 = (seg[0], seg[1])
                    end = (seg[2], seg[3])
                if rel:
                    if up == "C":
                        c1 = (c1[0] + pos[0], c1[1] + pos[1])
                    c2 = (c2[0] + pos[0], c2[1] + pos[1])
                    end = (end[0] + pos[0], end[1] + pos[1])
                span = (abs(c1[0] - pos[0]) + abs(c2[0] - c1[0])
                        + abs(end[0] - c2[0])
                        + abs(c1[1] - pos[1]) + abs(c2[1] - c1[1])
                        + abs(end[1] - c2[1])) * span_hint_mm
                if not cur:
                    cur = [pos]
                cur.extend(_cubic(pos, c1, c2, end, _flatten_steps(span)))
                pos = end
                prev_ctrl = c2
                prev_cmd = cmd
            continue
        elif up in ("Q", "T"):
            stride = 4 if up == "Q" else 2
            for k in range(0, len(nums) - stride + 1, stride):
                seg = nums[k:k + stride]
                if up == "Q":
                    c1 = (seg[0], seg[1])
                    end = (seg[2], seg[3])
                    if rel:
                        c1 = (c1[0] + pos[0], c1[1] + pos[1])
                        end = (end[0] + pos[0], end[1] + pos[1])
                else:
                    if prev_cmd.upper() in ("Q", "T") and prev_ctrl:
                        c1 = (2 * pos[0] - prev_ctrl[0],
                              2 * pos[1] - prev_ctrl[1])
                    else:
                        c1 = pos
                    end = (seg[0], seg[1])
                    if rel:
                        end = (end[0] + pos[0], end[1] + pos[1])
                span = (abs(c1[0] - pos[0]) + abs(end[0] - c1[0])
                        + abs(c1[1] - pos[1]) + abs(end[1] - c1[1])
                        ) * span_hint_mm
                if not cur:
                    cur = [pos]
                cur.extend(_quad(pos, c1, end, _flatten_steps(span)))
                pos = end
                prev_ctrl = c1
                prev_cmd = cmd
            continue
        elif up == "A":
            for k in range(0, len(nums) - 6, 7):
                rx, ry, rot = nums[k], nums[k + 1], nums[k + 2]
                large = bool(nums[k + 3])
                sweep = bool(nums[k + 4])
                end = (nums[k + 5], nums[k + 6])
                if rel:
                    end = (end[0] + pos[0], end[1] + pos[1])
                span = (abs(rx) + abs(ry)) * span_hint_mm
                if not cur:
                    cur = [pos]
                cur.extend(_arc(pos, rx, ry, rot, large, sweep, end,
                                _flatten_steps(span)))
                pos = end
            prev_ctrl = None
        prev_cmd = cmd
    flush()
    return subs


# ----- shape elements -------------------------------------------------------

def _f(el, name, default=0.0):
    try:
        return float(el.get(name, default))
    except (TypeError, ValueError):
        return default


def _circle_steps(r_mm):
    """Segments for a full circle of radius r so the chord sagitta stays
    within FLATTEN_TOL_MM.

    sagitta = r * (1 - cos(pi/n)), so n = pi / acos(1 - tol/r). Using the
    exact relation (rather than a span heuristic) keeps circles - the
    shape people actually measure - inside tolerance."""
    if r_mm <= FLATTEN_TOL_MM:
        return _MIN_SEGS
    ratio = 1.0 - FLATTEN_TOL_MM / r_mm
    ratio = max(-1.0, min(1.0, ratio))
    n = int(math.ceil(math.pi / math.acos(ratio)))
    return max(16, min(_MAX_SEGS, n))


def _ellipse_points(cx, cy, rx, ry, span_hint_mm):
    # Use the LARGER radius: it sets the worst-case chord error.
    steps = _circle_steps(max(abs(rx), abs(ry)) * span_hint_mm)
    return [(cx + rx * math.cos(2.0 * math.pi * k / steps),
             cy + ry * math.sin(2.0 * math.pi * k / steps))
            for k in range(steps)]


def _rounded_rect(x, y, w, h, rx, ry, span_hint_mm):
    """Rect with corner radii, flattened. rx/ry already clamped."""
    # quarter-circle each: a full-circle count / 4, min 2 for smoothness
    steps = max(2, _circle_steps(max(rx, ry) * span_hint_mm) // 4)

    def corner(cx, cy, a0, a1):
        return [(cx + rx * math.cos(a0 + (a1 - a0) * k / steps),
                 cy + ry * math.sin(a0 + (a1 - a0) * k / steps))
                for k in range(steps + 1)]

    pi = math.pi
    pts = []
    pts += corner(x + w - rx, y + ry, -pi / 2, 0.0)          # top-right
    pts += corner(x + w - rx, y + h - ry, 0.0, pi / 2)       # bottom-right
    pts += corner(x + rx, y + h - ry, pi / 2, pi)            # bottom-left
    pts += corner(x + rx, y + ry, pi, 1.5 * pi)              # top-left
    return pts


def _points_attr(text):
    vals = [float(v) for v in re.split(r"[\s,]+", (text or "").strip()) if v]
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


def element_subpaths(el, tag, span_hint_mm):
    """Geometry of one SVG shape element, in user units."""
    if tag == "path":
        return path_to_subpaths(el.get("d") or "", span_hint_mm)
    if tag == "rect":
        x, y = _f(el, "x"), _f(el, "y")
        w, h = _f(el, "width"), _f(el, "height")
        if w <= 0.0 or h <= 0.0:
            return []
        rx = el.get("rx")
        ry = el.get("ry")
        rxv = _f(el, "rx") if rx is not None else (_f(el, "ry") if ry else 0.0)
        ryv = _f(el, "ry") if ry is not None else rxv
        rxv = min(rxv, w / 2.0)
        ryv = min(ryv, h / 2.0)
        if rxv > 0.0 and ryv > 0.0:
            return [_rounded_rect(x, y, w, h, rxv, ryv, span_hint_mm)]
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
    if tag == "circle":
        r = _f(el, "r")
        if r <= 0.0:
            return []
        return [_ellipse_points(_f(el, "cx"), _f(el, "cy"), r, r,
                                span_hint_mm)]
    if tag == "ellipse":
        rx, ry = _f(el, "rx"), _f(el, "ry")
        if rx <= 0.0 or ry <= 0.0:
            return []
        return [_ellipse_points(_f(el, "cx"), _f(el, "cy"), rx, ry,
                                span_hint_mm)]
    if tag == "line":
        return [[(_f(el, "x1"), _f(el, "y1")),
                 (_f(el, "x2"), _f(el, "y2"))]]
    if tag in ("polyline", "polygon"):
        pts = _points_attr(el.get("points"))
        if len(pts) < 2:
            return []
        if tag == "polygon":
            pts = pts + [pts[0]]
        return [pts]
    return []


_SHAPE_TAGS = ("path", "rect", "circle", "ellipse", "line",
               "polyline", "polygon")


# ----- traversal ------------------------------------------------------------

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _layer_label(el):
    """Inkscape layer name, when the group is a layer."""
    for key, val in el.attrib.items():
        if _local(key) == "label":
            return val
    return None


def _is_hidden(el):
    """display:none / visibility:hidden, in the attribute or in style."""
    if el.get("display") == "none":
        return True
    style = el.get("style") or ""
    if not style:
        return False
    low = style.replace(" ", "").lower()
    return "display:none" in low or "visibility:hidden" in low


# Layers PageWright itself writes as non-printing guides. Re-opening our
# own export should bring back the artwork, not the tile grid and rulers
# drawn over it (which also extend past the trace and would inflate the
# page size).
_GUIDE_LAYERS = ("annotations", "measurements")


def _collect(el, xform, span_hint_mm, out, group_name, skip_hidden,
             skip_guides=True):
    for child in el:
        tag = _local(child.tag)
        if tag in ("defs", "symbol", "marker", "clipPath", "mask",
                   "metadata", "title", "desc", "style", "script"):
            continue
        if skip_hidden and _is_hidden(child):
            continue
        if skip_guides and tag in ("g", "a", "svg", "switch"):
            ident = (child.get("id") or "").strip().lower()
            label = (_layer_label(child) or "").strip().lower()
            if ident in _GUIDE_LAYERS or label in _GUIDE_LAYERS:
                continue
        m = mat_mul(xform, parse_transform(child.get("transform")))
        if tag in ("g", "a", "svg", "switch"):
            name = _layer_label(child) or group_name
            _collect(child, m, span_hint_mm, out, name, skip_hidden,
                     skip_guides)
            continue
        if tag in _SHAPE_TAGS:
            subs = element_subpaths(child, tag, span_hint_mm)
            if not subs:
                continue
            placed = [[mat_apply(m, x, y) for (x, y) in sub] for sub in subs]
            label = (child.get("id") or group_name or tag)
            out.append((label, placed))


def read_svg_shapes(path, skip_hidden=True, skip_guides=True):
    """Parse `path`; return (shapes, mm_per_unit_x, mm_per_unit_y).

    `shapes` is [(label, [subpath_user_units, ...]), ...] with all group
    and element transforms already applied."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise SvgImportError("not a readable SVG file: %s" % (exc,))
    root = tree.getroot()
    if _local(root.tag) != "svg":
        raise SvgImportError("root element is <%s>, not <svg>"
                             % _local(root.tag))
    sx, sy, ox, oy = document_scale(root)
    base = mat_mul((sx, 0.0, 0.0, sy, 0.0, 0.0),
                   (1.0, 0.0, 0.0, 1.0, -ox, -oy))
    # After `base` the coordinates ARE millimetres, so the flattening
    # hint is 1 mm per unit.
    shapes = []
    _collect(root, base, 1.0, shapes, None, skip_hidden, skip_guides)
    return shapes, sx, sy


# ----- project assembly -----------------------------------------------------

def _dedup(points):
    """Drop consecutive duplicates and a repeated closing point."""
    out = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > 1e-9 \
                or abs(p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    if len(out) > 2 and abs(out[0][0] - out[-1][0]) <= 1e-9 \
            and abs(out[0][1] - out[-1][1]) <= 1e-9:
        out.pop()
    return out


def import_svg(path, skip_hidden=True, group_by_layer=True,
               skip_guides=True):
    """Read an SVG and return (objects, size_mm, mm_per_pixel).

    `objects` are TracedObjects whose contour points are in the app's
    pixel space (PX_PER_MM per millimetre) with the drawing's bounding
    box translated to the origin; `size_mm` is (width, height) of that
    bounding box; `mm_per_pixel` is the calibration to store.

    Shapes are grouped into one TracedObject per Inkscape layer (or per
    element id when there are no layers) so the Objects panel is
    navigable instead of showing hundreds of rows. Raises SvgImportError
    if nothing printable is found.
    """
    shapes, _sx, _sy = read_svg_shapes(path, skip_hidden=skip_hidden,
                                       skip_guides=skip_guides)

    # mm -> pixel space, then shift so the content starts at (0, 0).
    groups = []          # [(name, [contour_points_px, ...])]
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for label, subs in shapes:
        rings = []
        for sub in subs:
            pts = _dedup([(x * PX_PER_MM, y * PX_PER_MM) for (x, y) in sub])
            if len(pts) < 3:
                continue        # _path_d would drop it anyway
            rings.append(pts)
            for (x, y) in pts:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
        if rings:
            groups.append((label, rings))

    if not groups:
        raise SvgImportError(
            "no printable shapes found (only text, images or open "
            "single-segment paths?)")

    merged = []
    if group_by_layer:
        order = []
        by_name = {}
        for name, rings in groups:
            if name not in by_name:
                by_name[name] = []
                order.append(name)
            by_name[name].extend(rings)
        merged = [(n, by_name[n]) for n in order]
    else:
        merged = groups

    objects = []
    for name, rings in merged:
        obj = TracedObject(name=str(name), style=Style())
        obj.contours = [
            Contour(points=[(x - min_x, y - min_y) for (x, y) in r],
                    closed=True, role="outer")
            for r in rings]
        objects.append(obj)

    w_px = max_x - min_x
    h_px = max_y - min_y
    return (objects,
            (w_px / PX_PER_MM, h_px / PX_PER_MM),
            1.0 / PX_PER_MM)
