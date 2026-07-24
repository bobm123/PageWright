"""Single-page spline dewarp core (GUI-free).

Ported into PageWright from the `dewarp` project's ``lib/book_model.py``
(itself a single-page subset of the BookScan ``book_dewarp.py`` core) as
the first stage of the unified capture-to-OCR studio. The raw nested-dict
model has been replaced with the ``PageModel`` / ``PageEdge`` dataclasses,
consistent with PageWright's typed ``model.py`` and the OutlineModel
refactor in BookScan.

Physical model: a page is a rectangular sheet bent into a generalized
cylinder. Its side edges stay STRAIGHT lines; the top and bottom edges
become curves of EQUAL physical length. Each curved edge is a spline
with one on-curve control point and a mirrored tangent handle, plus an
optional tangent handle at each corner (for pages that dive steeply into
the gutter).

Dewarp is two-stage:
1. PERSPECTIVE: the corner quad (straight side edges) defines a
   homography to an upright rectangle, removing camera obliquity.
2. CYLINDER: in the rectified frame both edge curves are
   re-parameterized by fractional arc length; straight rulings are
   interpolated between them and output columns are placed at equal
   arc-length steps, undoing the squeeze where the page curves away.

Detection: silhouette (brightest blob in the ROI) for the initial
outline; optional text-line refinement bends the model so its isolines
match the printed lines (for a ruled surface, edge-curve error shifts
isolines affinely in the page fraction t, so an affine-in-t solve on a
trial dewarp, extrapolated to t=0/t=1, IS the edge correction).

All functions take/return image coordinates of the FULL image; pass
roi=(x0, y0, x1, y1) to restrict detection.

Kept deliberately dependency-light (OpenCV + NumPy only) so it ports to
C/C++ with little more than syntax changes, matching the rest of the
core.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2
import numpy as np

DENSE = 2000          # samples for arc-length tables
N_PTS = 7             # control points per traced edge
EDGE_ANCHORS = {"top": ("tl", "tr"), "bottom": ("bl", "br")}
# corner anchor -> (curved edge it terminates, which end)
CORNER_EDGE_END = {"tl": ("top", "a"), "tr": ("top", "b"),
                   "bl": ("bottom", "a"), "br": ("bottom", "b")}


# ---------------------------------------------------------------------------
# Model dataclasses: 4 anchors + 2 edge splines
# ---------------------------------------------------------------------------


@dataclass
class PageEdge:
    """One curved page edge (top or bottom).

    mid         on-curve control point [x, y] (full-image pixels)
    handle      tangent handle at mid; when handle_out is None the tangent
                is MIRRORED (smooth), drawn as mid +/- handle
    handle_out  optional independent OUTGOING tangent handle at mid.
                When set, the incoming side (toward the start anchor)
                uses `handle` and the outgoing side uses `handle_out`,
                allowing a sharp V at mid - e.g. the gutter fold at the
                center of an open book. None = smooth (mirrored).
    tip_a       optional tangent handle at the START corner, stored
                pointing INTO the curve from that anchor (intuitive to
                drag); None means "use the chord tangent"
    tip_b       optional tangent handle at the END corner, same convention
    """
    mid: List[float]
    handle: List[float]
    tip_a: Optional[List[float]] = None
    tip_b: Optional[List[float]] = None
    handle_out: Optional[List[float]] = None

    @property
    def broken(self) -> bool:
        """True when the mid tangent is broken (independent sides)."""
        return self.handle_out is not None

    def copy(self) -> "PageEdge":
        return PageEdge(list(self.mid), list(self.handle),
                        None if self.tip_a is None else list(self.tip_a),
                        None if self.tip_b is None else list(self.tip_b),
                        None if self.handle_out is None
                        else list(self.handle_out))

    def to_dict(self) -> dict:
        d = {"mid": list(self.mid), "handle": list(self.handle)}
        if self.tip_a is not None:
            d["tip_a"] = list(self.tip_a)
        if self.tip_b is not None:
            d["tip_b"] = list(self.tip_b)
        if self.handle_out is not None:
            d["handle_out"] = list(self.handle_out)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PageEdge":
        ta, tb = d.get("tip_a"), d.get("tip_b")
        ho = d.get("handle_out")
        return cls(list(d["mid"]), list(d["handle"]),
                   None if ta is None else list(ta),
                   None if tb is None else list(tb),
                   None if ho is None else list(ho))


@dataclass
class PageModel:
    """Single-page outline: 4 corner anchors + top/bottom edge splines.

    anchors  name -> [x, y]; names are tl, tr, bl, br
    edges    "top" / "bottom" -> PageEdge

    Coordinates are full-image pixels. to_dict / from_dict give a plain
    JSON-friendly shape (identical to the original book_model dict), so a
    page outline can be persisted in a project file and reloaded.
    """
    anchors: Dict[str, List[float]]
    edges: Dict[str, "PageEdge"]

    def copy(self) -> "PageModel":
        return PageModel({k: list(v) for k, v in self.anchors.items()},
                         {k: e.copy() for k, e in self.edges.items()})

    def to_dict(self) -> dict:
        return {"anchors": {k: list(v) for k, v in self.anchors.items()},
                "edges": {k: e.to_dict() for k, e in self.edges.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "PageModel":
        return cls({k: list(v) for k, v in d["anchors"].items()},
                   {k: PageEdge.from_dict(e) for k, e in d["edges"].items()})

    def scaled(self, s: float) -> "PageModel":
        """Deep copy scaled by factor s. Scales every stored coordinate
        AND vector (mid handle and corner tips included - forgetting
        those warps the curves). Used to render previews on downscaled
        images."""
        out = self.copy()
        for k in out.anchors:
            out.anchors[k] = [v * s for v in out.anchors[k]]
        for e in out.edges.values():
            e.mid = [v * s for v in e.mid]
            e.handle = [v * s for v in e.handle]
            if e.tip_a is not None:
                e.tip_a = [v * s for v in e.tip_a]
            if e.tip_b is not None:
                e.tip_b = [v * s for v in e.tip_b]
            if e.handle_out is not None:
                e.handle_out = [v * s for v in e.handle_out]
        return out


# ---------------------------------------------------------------------------
# Curve utilities
# ---------------------------------------------------------------------------

def _hermite_seg(p0, p1, t0, t1, n):
    u = np.linspace(0, 1, n)[:, None]
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2
    return h00 * p0 + h10 * t0 + h01 * p1 + h11 * t1


def spline_edge_dense(a, m, h, b, ta=None, tb=None, n=DENSE, h_out=None):
    """Edge curve from corner a to corner b through on-curve control
    point m with tangent handle h (drawn as m +/- h). Two C1-joined
    Hermite segments. ta/tb are optional endpoint TANGENTS (curve
    direction, left-to-right); they default to the chords. The corner
    handles let the curve dive steeply where a page turns into the
    gutter - a chord tangent cannot follow that.

    h_out, when given, is an independent OUTGOING tangent handle at m:
    the a->m segment ends with tangent 3h and the m->b segment starts
    with tangent 3*h_out, breaking C1 at m into a sharp V (the gutter
    fold at the center of an open book). h_out=None keeps the mirrored
    (smooth) behavior, numerically identical to before."""
    a = np.asarray(a, np.float64)
    m = np.asarray(m, np.float64)
    b = np.asarray(b, np.float64)
    h = np.asarray(h, np.float64)
    ta = (m - a) if ta is None else np.asarray(ta, np.float64)
    tb = (b - m) if tb is None else np.asarray(tb, np.float64)
    tm_in = h * 3.0
    tm_out = tm_in if h_out is None else np.asarray(h_out, np.float64) * 3.0
    n1 = n // 2
    s1 = _hermite_seg(a, m, ta, tm_in, n1)
    s2 = _hermite_seg(m, b, tm_out, tb, n - n1 + 1)[1:]
    return np.vstack([s1, s2])


def sample_by_arclength(dense, s):
    seg = np.sqrt(((dense[1:] - dense[:-1]) ** 2).sum(axis=1))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total == 0:
        return np.repeat(dense[:1], len(s), axis=0), 0.0
    cum /= total
    x = np.interp(s, cum, dense[:, 0])
    y = np.interp(s, cum, dense[:, 1])
    return np.stack([x, y], axis=1), total


def fit_smooth_curve(pts, degree=4, n=DENSE):
    """Smooth polynomial through noisy trace points, endpoints exact."""
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < 2:
        raise ValueError("curve needs at least 2 points")
    if len(pts) == 2:
        u = np.linspace(0, 1, n)[:, None]
        return pts[0] * (1 - u) + pts[1] * u
    chord = np.sqrt(((pts[1:] - pts[:-1]) ** 2).sum(axis=1))
    u = np.concatenate([[0.0], np.cumsum(chord)])
    if u[-1] == 0:
        raise ValueError("degenerate curve")
    u /= u[-1]
    deg = int(min(degree, len(pts) - 1))
    ud = np.linspace(0, 1, n)
    dense = np.empty((n, 2))
    for k in range(2):
        coef = np.polyfit(u, pts[:, k], deg)
        dense[:, k] = np.polyval(coef, ud)
        r0 = pts[0, k] - dense[0, k]
        r1 = pts[-1, k] - dense[-1, k]
        dense[:, k] += r0 * (1 - ud) + r1 * ud
    return dense


def fit_spline_edge(a, b, target, n_fit=200, iters=3):
    """Least-squares fit of (mid, mid-tangent, both end tangents) so the
    edge spline tracks a target dense curve; the spline is LINEAR in
    all four unknowns, so each coordinate is a small lstsq solve.
    Returns (mid, mid_handle, ta, tb) with ta/tb the endpoint tangents
    in curve direction."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    s_frac = np.linspace(0.0, 1.0, n_fit)
    tgt, _ = sample_by_arclength(target, s_frac)
    u_match = s_frac.copy()
    m_fit = (a + b) / 2
    tm_fit = (b - a) / 2
    ta_fit = tb_fit = None
    for _ in range(iters):
        seg2 = u_match > 0.5
        u = np.where(seg2, (u_match - 0.5) * 2, u_match * 2)[:, None]
        h00 = (2 * u ** 3 - 3 * u ** 2 + 1).ravel()
        h10 = (u ** 3 - 2 * u ** 2 + u).ravel()
        h01 = (-2 * u ** 3 + 3 * u ** 2).ravel()
        h11 = (u ** 3 - u ** 2).ravel()
        # seg1: h00*a + h10*ta + h01*m + h11*tm
        # seg2: h00*m + h10*tm + h01*b + h11*tb
        cm = np.where(seg2, h00, h01)
        ct = np.where(seg2, h10, h11)
        cta = np.where(seg2, 0.0, h10)
        ctb = np.where(seg2, h11, 0.0)
        const = np.where(seg2[:, None], h01[:, None] * b,
                         h00[:, None] * a)
        A = np.stack([cm, ct, cta, ctb], axis=1)
        sol, *_ = np.linalg.lstsq(A, tgt - const, rcond=None)
        m_fit, tm_fit, ta_fit, tb_fit = sol[0], sol[1], sol[2], sol[3]
        dense = spline_edge_dense(a, m_fit, tm_fit / 3.0, b,
                                  ta=ta_fit, tb=tb_fit, n=800)
        d2 = ((dense[None, :, :] - tgt[:, None, :]) ** 2).sum(axis=2)
        seg_len = np.sqrt(((dense[1:] - dense[:-1]) ** 2).sum(axis=1))
        cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        cum /= max(cum[-1], 1e-9)
        u_match = cum[np.argmin(d2, axis=1)]
    return m_fit, tm_fit / 3.0, ta_fit, tb_fit


# ---------------------------------------------------------------------------
# Model construction and evaluation
# ---------------------------------------------------------------------------

def edge_dense(model, name, n=DENSE):
    a_name, b_name = EDGE_ANCHORS[name]
    e = model.edges[name]
    # corner tangent handles are stored pointing INTO the curve from
    # each anchor (intuitive to drag); convert to curve-direction
    # tangents: start tip points along the curve, end tip points back
    ta = tb = None
    if e.tip_a is not None:
        ta = 3.0 * np.asarray(e.tip_a, np.float64)
    if e.tip_b is not None:
        tb = -3.0 * np.asarray(e.tip_b, np.float64)
    return spline_edge_dense(model.anchors[a_name], e.mid,
                             e.handle, model.anchors[b_name],
                             ta=ta, tb=tb, n=n, h_out=e.handle_out)


def page_edges(model, n=DENSE):
    """(top_dense, bottom_dense), both left-to-right."""
    return edge_dense(model, "top", n), edge_dense(model, "bottom", n)


def model_from_traces(top_pts, bot_pts):
    """Build the model from traced edge point lists (left-to-right)."""
    top_pts = np.asarray(top_pts, np.float64)
    bot_pts = np.asarray(bot_pts, np.float64)
    anchors = {"tl": top_pts[0].tolist(), "tr": top_pts[-1].tolist(),
               "bl": bot_pts[0].tolist(), "br": bot_pts[-1].tolist()}
    edges = {}
    for name, pts in (("top", top_pts), ("bottom", bot_pts)):
        a_name, b_name = EDGE_ANCHORS[name]
        a = np.array(anchors[a_name])
        b = np.array(anchors[b_name])
        target = fit_smooth_curve(pts)
        m, h, ta, tb = fit_spline_edge(a, b, target)
        edges[name] = PageEdge(mid=m.tolist(), handle=h.tolist(),
                               tip_a=(ta / 3.0).tolist(),
                               tip_b=(-tb / 3.0).tolist())
    return PageModel(anchors, edges)


def default_model(w, h):
    """Fallback outline covering the middle of a w x h region."""
    top = [[w * (0.1 + 0.8 * i / (N_PTS - 1)), h * 0.15]
           for i in range(N_PTS)]
    bot = [[w * (0.1 + 0.8 * i / (N_PTS - 1)), h * 0.85]
           for i in range(N_PTS)]
    return model_from_traces(top, bot)


def page_size_px(model):
    """Physical page size estimate in source pixels (width, height)."""
    top, bot = page_edges(model)
    _, len_top = sample_by_arclength(top, np.array([0.0]))
    _, len_bot = sample_by_arclength(bot, np.array([0.0]))
    w = 0.5 * (len_top + len_bot)
    h = 0.5 * (np.linalg.norm(top[0] - bot[0])
               + np.linalg.norm(top[-1] - bot[-1]))
    return float(w), float(h)


# ---------------------------------------------------------------------------
# GUI handle support (used by the eventual Qt dewarp-stage UI)
# ---------------------------------------------------------------------------

def handles(model):
    """Draggable handles: per-edge mid + mirrored tangent tips, a corner
    tangent tip at each of the 4 corners, and the 4 corner anchors
    (listed last so they are drawn on top and win hit-test ties)."""
    out = []
    for e in ("top", "bottom"):
        out.append(("mid", e, None))
        out.append(("tip", e, 1))
        out.append(("tip", e, -1))
        out.append(("ctip", e, "a"))
        out.append(("ctip", e, "b"))
    out += [("anchor", k, None) for k in ("tl", "tr", "bl", "br")]
    return out


def _corner_anchor(model, edge, end):
    a_name, b_name = EDGE_ANCHORS[edge]
    return np.array(model.anchors[a_name if end == "a" else b_name],
                    np.float64)


def handle_pos(model, h):
    kind, key, sgn = h
    if kind == "anchor":
        return np.array(model.anchors[key], np.float64)
    e = model.edges[key]
    if kind == "mid":
        return np.array(e.mid, np.float64)
    if kind == "ctip":
        tip = e.tip_a if sgn == "a" else e.tip_b
        base = _corner_anchor(model, key, sgn)
        if tip is None:
            # default: a third of the chord toward the mid point
            return base + (np.array(e.mid) - base) / 3.0
        return base + np.array(tip, np.float64)
    # mid tangent tips: +1 = outgoing side, -1 = incoming side. When the
    # tangent is broken, the outgoing tip follows handle_out.
    mid = np.array(e.mid, np.float64)
    if sgn > 0 and e.handle_out is not None:
        return mid + np.array(e.handle_out, np.float64)
    return mid + sgn * np.array(e.handle, np.float64)


def move_handle(model, h, pos):
    kind, key, sgn = h
    pos = [float(pos[0]), float(pos[1])]
    if kind == "anchor":
        model.anchors[key] = pos
    elif kind == "mid":
        model.edges[key].mid = pos
    elif kind == "ctip":
        base = _corner_anchor(model, key, sgn)
        vec = (np.array(pos, np.float64) - base).tolist()
        if sgn == "a":
            model.edges[key].tip_a = vec
        else:
            model.edges[key].tip_b = vec
    else:
        e = model.edges[key]
        m = np.array(e.mid, np.float64)
        if sgn > 0 and e.handle_out is not None:
            e.handle_out = (np.array(pos) - m).tolist()
        else:
            e.handle = (sgn * (np.array(pos) - m)).tolist()


def break_mid_tangent(model, edge_name):
    """Make the mid tangent of `edge_name` independent per side (a V
    fold). Seeds handle_out = handle, so the curve is unchanged until a
    tip is dragged. No-op if already broken."""
    e = model.edges[edge_name]
    if e.handle_out is None:
        e.handle_out = list(e.handle)


def smooth_mid_tangent(model, edge_name):
    """Re-mirror the mid tangent of `edge_name` (removes the V fold).
    Keeps the incoming handle; the outgoing tip snaps back to its
    mirror. No-op if already smooth."""
    model.edges[edge_name].handle_out = None


def corner_tip_active(model, corner_name):
    """True when `corner_name` (tl/tr/bl/br) has an explicit tangent
    handle on its curved edge (a "spline corner")."""
    edge_name, end = CORNER_EDGE_END[corner_name]
    e = model.edges[edge_name]
    return (e.tip_a if end == "a" else e.tip_b) is not None


def toggle_corner_tip(model, corner_name):
    """Toggle `corner_name` between a normal corner (chord tangent, no
    visible handle) and a spline corner (explicit tangent handle).

    Activating seeds the tip at a third of the chord toward the edge's
    mid point - exactly the default tangent - so the curve does not move
    until the handle is dragged. Returns True if the tip is now active.
    """
    edge_name, end = CORNER_EDGE_END[corner_name]
    e = model.edges[edge_name]
    if corner_tip_active(model, corner_name):
        if end == "a":
            e.tip_a = None
        else:
            e.tip_b = None
        return False
    base = np.asarray(model.anchors[corner_name], np.float64)
    seed = ((np.asarray(e.mid, np.float64) - base) / 3.0).tolist()
    if end == "a":
        e.tip_a = seed
    else:
        e.tip_b = seed
    return True


# ---------------------------------------------------------------------------
# Silhouette detection (single page inside an optional ROI)
# ---------------------------------------------------------------------------

def _segment_page(gray):
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    _, mask = cv2.threshold(blur, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if gray[mask > 0].mean() < gray[mask == 0].mean():
        mask = 255 - mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    return mask


def _trace_edge(mask, x_from, x_to, which, n=41):
    h, w = mask.shape
    xs = np.linspace(x_from, x_to, n).round().astype(int)
    out = []
    for x in xs:
        col = np.where(mask[:, int(np.clip(x, 0, w - 1))] > 0)[0]
        if len(col) == 0:
            continue
        y = col.min() if which == "top" else col.max()
        out.append((float(x), float(y)))
    return out


def _median_filter_edge(pts, k=5):
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < k:
        return pts
    ys = pts[:, 1].copy()
    r = k // 2
    med = ys.copy()
    for i in range(len(ys)):
        a, b = max(0, i - r), min(len(ys), i + r + 1)
        med[i] = np.median(ys[a:b])
    pts[:, 1] = med
    return pts


def _decimate(pts, n):
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) <= n:
        return pts
    idx = np.linspace(0, len(pts) - 1, n).round().astype(int)
    return pts[idx]


def detect_page(gray, roi=None):
    """Detect a single page outline in gray (full image). roi is
    (x0, y0, x1, y1) in image coords or None for the whole frame.
    Returns the model in FULL-image coordinates. Raises RuntimeError."""
    H, W = gray.shape[:2]
    if roi is not None:
        x0, y0, x1, y1 = [int(round(v)) for v in roi]
        x0, x1 = max(0, min(x0, x1)), min(W, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H, max(y0, y1))
        if x1 - x0 < 20 or y1 - y0 < 20:
            raise RuntimeError("selection too small")
        sub = gray[y0:y1, x0:x1]
    else:
        x0 = y0 = 0
        sub = gray

    det_max = 1200
    scale = min(1.0, det_max / max(sub.shape))
    small = cv2.resize(sub, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else sub

    mask = _segment_page(small)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise RuntimeError("could not segment the page from the background")
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 0.05 * small.size:
        raise RuntimeError("no page-sized bright region in the selection")
    clean = np.zeros_like(mask)
    cv2.drawContours(clean, [contour], -1, 255, -1)

    pts = contour.reshape(-1, 2).astype(np.float64)
    tl = pts[np.argmin(pts[:, 0] + pts[:, 1])]
    tr = pts[np.argmax(pts[:, 0] - pts[:, 1])]
    bl = pts[np.argmin(pts[:, 0] - pts[:, 1])]
    br = pts[np.argmax(pts[:, 0] + pts[:, 1])]

    def edge(c_from, c_to, which):
        trc = _trace_edge(clean, c_from[0], c_to[0], which, n=41)
        if len(trc) < 4:
            raise RuntimeError("failed to trace the page %s edge" % which)
        trc = _median_filter_edge(trc, k=5)
        trc = _decimate(trc, N_PTS)
        trc[0], trc[-1] = c_from, c_to
        return trc

    top = edge(tl, tr, "top")
    bot = edge(bl, br, "bottom")
    inv = 1.0 / scale
    off = np.array([x0, y0], np.float64)
    top = top * inv + off
    bot = bot * inv + off
    return model_from_traces(top, bot)


# ---------------------------------------------------------------------------
# Quad (4-point) perspective dewarp - the simple straight-corner case
# ---------------------------------------------------------------------------

def order_points(pts):
    """Order four points as [top-left, top-right, bottom-right,
    bottom-left]. Sort by y (top two vs bottom two), then by x within
    each pair. Matches the original dewarp app's ordering so quad
    selections behave identically."""
    pts = np.asarray(pts, dtype=np.float32)
    if pts.shape != (4, 2):
        raise ValueError("order_points needs exactly 4 (x, y) points")
    sorted_by_y = pts[np.argsort(pts[:, 1])]
    top = sorted_by_y[:2]
    top = top[np.argsort(top[:, 0])]
    tl, tr = top
    bottom = sorted_by_y[2:]
    bottom = bottom[np.argsort(bottom[:, 0])]
    bl, br = bottom
    return np.array([tl, tr, br, bl], dtype=np.float32)


def dewarp_quad(image, points, out_w, out_h, full_image=False,
                interp=cv2.INTER_LINEAR):
    """Perspective-rectify a quadrilateral selection.

    points   four (x, y) corners in any order (ordered internally)
    out_w,   the rectified size of the SELECTED quad in output pixels
    out_h    (e.g. from real-world mm at a chosen DPI)
    full_image
        False -> "crop" mode: output is exactly the rectified quad,
                 out_h x out_w.
        True  -> "full image" mode: the same quad-to-rectangle mapping is
                 applied to the WHOLE image; the output canvas is enlarged
                 (and offset) so no part of the transformed image is
                 clipped. The selected quad still measures out_w x out_h
                 within that larger canvas.

    Returns the rectified image (same channel layout as the input).
    """
    out_w = int(round(out_w))
    out_h = int(round(out_h))
    if out_w < 2 or out_h < 2:
        raise ValueError("output size must be at least 2x2 px")
    rect = order_points(points)

    if not full_image:
        dst = np.array([[0, 0], [out_w - 1, 0],
                        [out_w - 1, out_h - 1], [0, out_h - 1]],
                       dtype=np.float32)
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (out_w, out_h), flags=interp)

    # full-image mode: find where the whole image lands, then offset
    img_h, img_w = image.shape[:2]
    temp_dst = np.array([[0, 0], [out_w - 1, 0],
                         [out_w - 1, out_h - 1], [0, out_h - 1]],
                        dtype=np.float32)
    m_temp = cv2.getPerspectiveTransform(rect, temp_dst)
    corners = np.array([[0, 0], [img_w - 1, 0],
                        [img_w - 1, img_h - 1], [0, img_h - 1]],
                       dtype=np.float32).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, m_temp).reshape(-1, 2)
    min_x = int(np.floor(warped[:, 0].min()))
    max_x = int(np.ceil(warped[:, 0].max()))
    min_y = int(np.floor(warped[:, 1].min()))
    max_y = int(np.ceil(warped[:, 1].max()))
    canvas_w, canvas_h = max_x - min_x, max_y - min_y
    ox, oy = -min_x, -min_y
    dst = np.array([[ox, oy], [ox + out_w - 1, oy],
                    [ox + out_w - 1, oy + out_h - 1],
                    [ox, oy + out_h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (canvas_w, canvas_h), flags=interp)


# ---------------------------------------------------------------------------
# Curved-page dewarp (spline model)
# ---------------------------------------------------------------------------

def _h_apply(M, pts):
    ph = np.concatenate([pts, np.ones(pts.shape[:-1] + (1,))], axis=-1)
    q = ph @ M.T
    return q[..., :2] / q[..., 2:3]


def _geometry(top, bot, out_w=None, out_h=None):
    """Homography + rectified equal-arc-length column samples.
    Returns (Hinv, top_s, bot_s, out_w, out_h)."""
    A, B = top[0], top[-1]
    C, D = bot[0], bot[-1]
    W0 = 0.5 * (np.linalg.norm(B - A) + np.linalg.norm(D - C))
    H0 = 0.5 * (np.linalg.norm(C - A) + np.linalg.norm(D - B))
    srcq = np.float32([A, B, C, D])
    dstq = np.float32([[0, 0], [W0, 0], [0, H0], [W0, H0]])
    Hm = cv2.getPerspectiveTransform(srcq, dstq).astype(np.float64)
    Hinv = np.linalg.inv(Hm)
    top_r = _h_apply(Hm, top)
    bot_r = _h_apply(Hm, bot)
    _, len_top = sample_by_arclength(top_r, np.array([0.0]))
    _, len_bot = sample_by_arclength(bot_r, np.array([0.0]))
    width_px = 0.5 * (len_top + len_bot)
    if out_h is None:
        out_h = int(round(H0))
    if out_w is None:
        out_w = max(2, int(round(out_h * width_px / H0)))
    s = np.linspace(0.0, 1.0, out_w)
    top_s, _ = sample_by_arclength(top_r, s)
    bot_s, _ = sample_by_arclength(bot_r, s)
    return Hinv, top_s, bot_s, out_w, out_h


def dewarp_page(image, model, out_w=None, out_h=None,
                interp=cv2.INTER_CUBIC):
    """Flatten the page to an out_w x out_h image (either may be None:
    missing dimensions come from the page's natural size/aspect)."""
    top, bot = page_edges(model)
    Hinv, top_s, bot_s, out_w, out_h = _geometry(top, bot, out_w, out_h)
    t = np.linspace(0.0, 1.0, out_h)[:, None, None]
    grid = _h_apply(Hinv, (1.0 - t) * top_s[None] + t * bot_s[None])
    return cv2.remap(image, grid[..., 0].astype(np.float32),
                     grid[..., 1].astype(np.float32), interp,
                     borderMode=cv2.BORDER_REPLICATE)


# ---------------------------------------------------------------------------
# Text-line refinement of the model
# ---------------------------------------------------------------------------

def _detect_text_lines(gray):
    H, W = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 35, 15)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, W // 20), 3))
    ln = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    ln = cv2.morphologyEx(ln, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ln)
    lines = []
    for i in range(1, n):
        x0, y0, w, h, area = stats[i]
        if w < W * 0.18 or h > H * 0.06 or h < 5:
            continue
        ys, xs = np.where(lab == i)
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]
        ux, idx = np.unique(xs, return_index=True)
        ym = (np.add.reduceat(ys.astype(np.float64), idx)
              / np.diff(np.append(idx, len(ys))))
        if len(ux) < 40:
            continue
        deg = 3 if (ux[-1] - ux[0]) > W * 0.4 else 2
        c = np.polyfit(ux, ym, deg)
        yy = np.polyval(c, ux)
        step = max(1, len(ux) // 50)
        lines.append((ux[::step], yy[::step]))
    return lines


def _solve_line_disparity(lines, xdeg, ydeg):
    rows = []
    for li, (xs, ys) in enumerate(lines):
        for xx, yy in zip(xs, ys):
            rows.append((xx, yy, li))
    arr = np.array(rows)
    xs_raw, y, li = arr[:, 0], arr[:, 1], arr[:, 2].astype(int)
    xlo, xhi = xs_raw.min(), xs_raw.max()
    ylo, yhi = y.min(), y.max()
    x = (xs_raw - xlo) / max(1.0, xhi - xlo)
    yn = (y - ylo) / max(1.0, yhi - ylo)
    basis = [(i, j) for i in range(1, xdeg + 1) for j in range(ydeg + 1)]
    A = np.stack([x ** i * yn ** j for i, j in basis], axis=1)
    B = np.zeros((len(arr), len(lines)))
    B[np.arange(len(arr)), li] = 1
    sol, *_ = np.linalg.lstsq(np.hstack([A, B]), y, rcond=None)
    return {"basis": basis, "coef": sol[:len(basis)],
            "xlo": float(xlo), "xhi": float(xhi),
            "ylo": float(ylo), "yhi": float(yhi)}


def _eval_disparity(sol, xs, y):
    xn = (np.clip(xs, sol["xlo"], sol["xhi"]) - sol["xlo"]) \
        / max(1.0, sol["xhi"] - sol["xlo"])
    yn = (y - sol["ylo"]) / max(1.0, sol["yhi"] - sol["ylo"])
    D = np.zeros_like(xn, dtype=np.float64)
    for c_, (i, j) in zip(sol["coef"], sol["basis"]):
        D += c_ * xn ** i * yn ** j
    return D


def refine_with_text(gray, model, iters=2, work_h=1100):
    """Correct the model so its isolines match the page's text lines.
    Returns (model, refined_flag). Needs >=5 detectable lines."""
    refined = False
    for _ in range(iters):
        top, bot = page_edges(model)
        Hinv, top_s, bot_s, out_w, out_h = _geometry(top, bot,
                                                     out_h=work_h)
        t = np.linspace(0.0, 1.0, out_h)[:, None, None]
        grid = _h_apply(Hinv, (1.0 - t) * top_s[None] + t * bot_s[None])
        flat = cv2.remap(gray, grid[..., 0].astype(np.float32),
                         grid[..., 1].astype(np.float32),
                         cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        lines = _detect_text_lines(flat)
        if len(lines) < 5:
            break
        sol = _solve_line_disparity(lines, xdeg=4, ydeg=1)
        xs = np.linspace(0, out_w - 1, N_PTS)
        d_top = _eval_disparity(sol, xs, np.full(N_PTS, 0.0))
        d_bot = _eval_disparity(sol, xs, np.full(N_PTS, out_h - 1.0))
        xc = np.array([out_w / 2.0])
        d_top -= _eval_disparity(sol, xc, np.array([0.0]))[0]
        d_bot -= _eval_disparity(sol, xc, np.array([out_h - 1.0]))[0]
        idx = xs.round().astype(int)
        tt = (d_top / (out_h - 1.0))[:, None]
        bb = ((out_h - 1.0 + d_bot) / (out_h - 1.0))[:, None]
        top_new = _h_apply(Hinv, (1.0 - tt) * top_s[idx] + tt * bot_s[idx])
        bot_new = _h_apply(Hinv, (1.0 - bb) * top_s[idx] + bb * bot_s[idx])
        model = model_from_traces(top_new, bot_new)
        refined = True
    return model, refined
