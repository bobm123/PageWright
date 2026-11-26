"""Join two page images side by side into one sheet.

A plan spread across two scanner beds is a capture problem, like a
plan photographed at an angle: the drawing is fine, the capture is
not. So it lives here with dewarp and calibration, and what comes out
is just a wider sheet.

How the pages line up is NOT found here. It is given: page B's
placement in page A's frame, as an optional quarter turn, a small
rotation and an offset. On the Pogo spread eight automatic attempts
converged on the best pixel match, and it was a whole former bay out
(OwlRacer/3DModels/PLAN_NOTES.md). A stitch cannot be trusted to its
own scoring, because the thing being scored is the thing in doubt. So
the placement comes from a person (the GUI's drag) or from numbers
(the CLI), and `measure_across` gives the independent check: a known
length that crosses the seam, measured through the placement.

Coordinates. Every point is in a page's FILE pixels (what an image
viewer shows for that file), with pixel centres on integers, the
convention cv2.warpAffine uses. A placement is read in page A's frame
AS DISPLAYED, after A's own quarter turn:

    B file px --turn_b--> B turned px --rotate angle_deg about its
    centre, then shift by (dx, dy)--> A display px --canvas origin-->
    output px

`angle_deg` is positive CLOCKWISE on screen (y down), the same sense
as the quarter turns, and the same sense as Qt's setRotation. With no
turn and no angle, (dx, dy) is simply where B's top-left pixel lands
in A: the Pogo spread is p17 at (1624, 147) in p16.

The overlap is blended by keeping the DARKER pixel. Ink on white
paper survives from both pages and white margins vanish; on the Pogo
pages this reproduces the hand-made spread pixel for pixel.

Scale is not adjusted. Two scans at the same DPI need none, and two
pages that disagree should be reported, not quietly resampled: a plan
stretched 2% is worse than one that visibly does not line up, because
that error survives into the cut parts. A cross-seam measurement that
comes out wrong is how the disagreement shows.

Qt-free; plain arithmetic plus cv2/numpy, so it ports with the rest.
"""

import json
import math
import os

import cv2
import numpy as np

# A pixel darker than this is ink when trimming. Chosen on the Pogo
# scans: it reproduces the hand trim exactly; 220 and up count the
# scanner's grey page edge as ink.
INK_THRESHOLD = 200

SIDECAR_SUFFIX = ".stitch.json"
SIDECAR_KIND = "pagewright-stitch"
SIDECAR_VERSION = 1

TURNS = (0, 90, 180, 270)


class Placement(object):
    """Where page B sits in page A's displayed frame.

    turn_a, turn_b : quarter turns, degrees clockwise (0/90/180/270).
    angle_deg      : B's small rotation about its own centre, after its
                     turn; positive clockwise on screen.
    dx, dy         : B's shift in A's displayed pixels.
    """

    def __init__(self, dx=0.0, dy=0.0, angle_deg=0.0, turn_a=0, turn_b=0):
        self.dx = float(dx)
        self.dy = float(dy)
        self.angle_deg = float(angle_deg)
        self.turn_a = _check_turn(turn_a)
        self.turn_b = _check_turn(turn_b)

    def to_dict(self):
        return {"dx": self.dx, "dy": self.dy, "angle_deg": self.angle_deg,
                "turn_a": self.turn_a, "turn_b": self.turn_b}

    @classmethod
    def from_dict(cls, d):
        return cls(dx=d.get("dx", 0.0), dy=d.get("dy", 0.0),
                   angle_deg=d.get("angle_deg", 0.0),
                   turn_a=d.get("turn_a", 0), turn_b=d.get("turn_b", 0))

    def __repr__(self):
        return ("Placement(dx=%g, dy=%g, angle_deg=%g, turn_a=%d, "
                "turn_b=%d)" % (self.dx, self.dy, self.angle_deg,
                                self.turn_a, self.turn_b))


def _check_turn(turn):
    t = int(turn) % 360
    if t not in TURNS:
        raise ValueError("a quarter turn is 0, 90, 180 or 270 degrees, "
                         "not %r" % (turn,))
    return t


# ----- transforms ------------------------------------------------------------

def turn_image(img, turn):
    """`img` turned clockwise by a quarter-turn multiple (exact)."""
    t = _check_turn(turn)
    if t == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if t == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if t == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def turned_size(size, turn):
    """(w, h) of a w x h image after `turn`."""
    w, h = size
    return (h, w) if _check_turn(turn) in (90, 270) else (w, h)


def _turn_matrix(size, turn):
    """3x3: file px -> turned px, for a w x h image (pixel centres)."""
    w, h = size
    t = _check_turn(turn)
    if t == 90:                       # (x, y) -> (h-1-y, x)
        return np.array([[0, -1, h - 1], [1, 0, 0], [0, 0, 1]], float)
    if t == 180:                      # (x, y) -> (w-1-x, h-1-y)
        return np.array([[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]], float)
    if t == 270:                      # (x, y) -> (y, w-1-x)
        return np.array([[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]], float)
    return np.eye(3)


def a_matrix(placement, a_size):
    """3x3: page A file px -> A display px."""
    return _turn_matrix(a_size, placement.turn_a)


def b_matrix(placement, b_size):
    """3x3: page B file px -> A display px."""
    tw, th = turned_size(b_size, placement.turn_b)
    cx, cy = (tw - 1) / 2.0, (th - 1) / 2.0
    th_rad = math.radians(placement.angle_deg)
    c, s = math.cos(th_rad), math.sin(th_rad)
    # clockwise on screen with y down is the ordinary rotation matrix
    rot = np.array([[c, -s, cx - c * cx + s * cy + placement.dx],
                    [s, c, cy - s * cx - c * cy + placement.dy],
                    [0, 0, 1]], float)
    return rot @ _turn_matrix(b_size, placement.turn_b)


def _apply(m, pt):
    x, y = pt
    return (float(m[0, 0] * x + m[0, 1] * y + m[0, 2]),
            float(m[1, 0] * x + m[1, 1] * y + m[1, 2]))


def map_a(placement, a_size, pt):
    """A file px -> A display px."""
    return _apply(a_matrix(placement, a_size), pt)


def map_b(placement, b_size, pt):
    """B file px -> A display px."""
    return _apply(b_matrix(placement, b_size), pt)


def measure_across(placement, a_size, b_size, a_pt, b_pt):
    """Pixel distance from a point on A to a point on B, through the
    placement. Both points are in their own page's file pixels.

    This is the seam check: put one end of a known length (a scale
    bar, a dimension) on each page and compare the result with what
    the drawing says it should be."""
    ax, ay = map_a(placement, a_size, a_pt)
    bx, by = map_b(placement, b_size, b_pt)
    return math.hypot(bx - ax, by - ay)


def _bounds(m, size):
    """Display-px bounds of a page's pixel centres under 3x3 `m`."""
    w, h = size
    pts = [_apply(m, p) for p in ((-0.5, -0.5), (w - 0.5, -0.5),
                                   (w - 0.5, h - 0.5), (-0.5, h - 0.5))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    # round away float noise so an integer shift stays an integer
    return (math.floor(round(min(xs) + 0.5, 6)),
            math.floor(round(min(ys) + 0.5, 6)),
            math.ceil(round(max(xs) - 0.5, 6)),
            math.ceil(round(max(ys) - 0.5, 6)))


# ----- composing ---------------------------------------------------------------

class StitchResult(object):
    """The joined image, and where each page landed in it.

    `a_to_out` / `b_to_out` are 3x3 matrices from each page's FILE
    pixels to output pixels, so a consumer never needs the turns."""

    def __init__(self, image, a_to_out, b_to_out, trim_box=None):
        self.image = image
        self.a_to_out = a_to_out
        self.b_to_out = b_to_out
        self.trim_box = trim_box          # (x0, y0, x1, y1) canvas px, or None

    @property
    def size(self):
        return (int(self.image.shape[1]), int(self.image.shape[0]))

    @property
    def a_origin(self):
        """Where A's file pixel (0, 0) is in the output."""
        return _apply(self.a_to_out, (0, 0))


def _match_channels(a, b):
    if a.ndim == b.ndim:
        return a, b
    if a.ndim == 2:
        return cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), b
    return a, cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)


def ink_box(img, margin, threshold=INK_THRESHOLD):
    """(x0, y0, x1, y1) inclusive box around the ink plus `margin`,
    clamped to the image, or None if there is no ink."""
    # luminance, not the darkest channel: a colour scan's tinted edge
    # noise is dark in one channel but is not ink
    grey = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ys, xs = np.nonzero(grey < threshold)
    if len(xs) == 0:
        return None
    h, w = grey.shape
    return (max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
            min(w - 1, int(xs.max()) + margin),
            min(h - 1, int(ys.max()) + margin))


def stitch(a_img, b_img, placement, trim=None):
    """Join page B onto page A. Returns a StitchResult.

    `trim`: None keeps the whole canvas (both pages' footprints); an
    integer crops to the ink plus that many pixels of margin."""
    a_img, b_img = _match_channels(a_img, b_img)
    a_size = (a_img.shape[1], a_img.shape[0])
    b_size = (b_img.shape[1], b_img.shape[0])
    ma = a_matrix(placement, a_size)
    mb = b_matrix(placement, b_size)

    ax0, ay0, ax1, ay1 = _bounds(ma, a_size)
    bx0, by0, bx1, by1 = _bounds(mb, b_size)
    x0, y0 = min(ax0, bx0), min(ay0, by0)
    width = max(ax1, bx1) - x0 + 1
    height = max(ay1, by1) - y0 + 1
    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], float)

    shape = (height, width) + a_img.shape[2:]
    canvas = np.full(shape, 255, dtype=a_img.dtype)
    a_turned = turn_image(a_img, placement.turn_a)
    th, tw = a_turned.shape[:2]
    canvas[-y0:-y0 + th, -x0:-x0 + tw] = a_turned

    b_to_out = shift @ mb
    warped = cv2.warpAffine(
        b_img, b_to_out[:2], (width, height), flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255) if b_img.ndim == 3 else 255)
    np.minimum(canvas, warped, out=canvas)

    a_to_out = shift @ ma
    box = None
    if trim is not None:
        box = ink_box(canvas, int(trim))
        if box is not None:
            cx0, cy0, cx1, cy1 = box
            canvas = canvas[cy0:cy1 + 1, cx0:cx1 + 1].copy()
            crop = np.array([[1, 0, -cx0], [0, 1, -cy0], [0, 0, 1]], float)
            a_to_out = crop @ a_to_out
            b_to_out = crop @ b_to_out
    return StitchResult(canvas, a_to_out, b_to_out, box)


# ----- the sidecar -------------------------------------------------------------

def sidecar_path(image_path):
    """spread.png -> spread.stitch.json"""
    return os.path.splitext(image_path)[0] + SIDECAR_SUFFIX


def _rel(path, start):
    try:
        return os.path.relpath(path, start).replace(os.sep, "/")
    except ValueError:                # another drive on Windows
        return os.path.abspath(path)


def sidecar_dict(result, placement, a_path, b_path, out_path, trim=None,
                 mm_per_px=None, checks=None):
    """What the sidecar records: the two source pages, the placement,
    and each page's matrix into the output, so the join can be re-run,
    corrected, or its coordinates mapped back to the originals."""
    base = os.path.dirname(os.path.abspath(out_path))

    def m2(m):
        return [[float(v) for v in m[0]], [float(v) for v in m[1]]]

    d = {"kind": SIDECAR_KIND, "version": SIDECAR_VERSION,
         "page_a": _rel(a_path, base), "page_b": _rel(b_path, base),
         "placement": placement.to_dict(), "trim_px": trim,
         "output": {"path": _rel(out_path, base),
                    "size": list(result.size),
                    "a_to_out": m2(result.a_to_out),
                    "b_to_out": m2(result.b_to_out)}}
    if mm_per_px:
        d["mm_per_px"] = float(mm_per_px)
    if checks:
        d["checks"] = list(checks)
    return d


def write_sidecar(path, d):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
        fh.write("\n")


def read_sidecar(path):
    """Load a sidecar. Page paths come back absolute, resolved against
    the sidecar's own folder."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if d.get("kind") != SIDECAR_KIND:
        raise ValueError("%s is not a PageWright stitch file" % path)
    base = os.path.dirname(os.path.abspath(path))
    for key in ("page_a", "page_b"):
        d[key] = os.path.normpath(os.path.join(base, d[key]))
    d["placement"] = Placement.from_dict(d.get("placement") or {})
    return d
