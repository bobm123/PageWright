"""Find the drawn parts on a sheet, with no seed marks.

Why this exists: tracing needs a human to paint inside and outside. On
a plan that holds sixty parts that is sixty gestures before any work
starts, and it is the one step an agent driving PageWright could not
do for itself. But a drawn part is a CLOSED LOOP of ink, so the sheet
already says where the parts are: flood the paper inward from the page
border, and whatever paper the flood cannot reach is walled in by an
outline.

Three things make the naive version fail, all handled here:

  * HAIRLINE GAPS. A scanned outline is rarely watertight - the sample
    former is a clean loop except for one break where a line trails
    off, and one break lets the interior leak to the page. A
    morphological close of `gap_mm` bridges those without moving the
    geometry that matters; on the sample it took detection from 3 of 5
    known parts to 5 of 5.
  * TEXT BOXES. A ruled box around a title encloses a region exactly
    as a former does. Shape filters (aspect ratio, how solidly the
    region fills its bounding box) drop most of them.
  * PARTS THAT NEVER SEAL. An outline crossed by a dimension line, or
    sharing ink with its neighbour, has no enclosed interior and will
    not be found. These are mostly the thin ribs and spars.

What comes out is the LARGE parts: 15 on the sample sheet, covering
72% of the drawing - every notched former plus the rudder, stabilizer
and wheel. That is the useful half of the trade. The reader of this
output is a designer with CAD skills, for whom tracing sixty parts off
a scan is expensive and drawing one missing rib is not, so a head
start on the big parts is worth more than completeness.

GUI-free: cv2 + numpy, and no Python-only idioms that would block the
C++ port.
"""

import cv2
import numpy as np

from . import contours as cont

# Ink is anything darker than this. Plans are black on white; a photo
# of one should be flattened and thresholded before it gets here.
INK_THRESHOLD = 128

# How wide a break in an outline may be and still be bridged. Big
# enough for scan dropouts, small enough not to weld a part to the
# dimension line beside it. Tuned on a real plan: 0.3 mm found 4 of 5
# known parts, while 0.6 mm dropped to 3 of 5 because the wider close
# started joining parts to their neighbours.
DEFAULT_GAP_MM = 0.3

# Smallest enclosed area worth reporting, in square millimetres. Below
# this it is a letter counter ("o", "e") or a speck.
DEFAULT_MIN_AREA_MM2 = 150.0

# A part is roughly as wide as it is tall. Anything longer than this is
# a rule, a leader line or a line of boxed text.
DEFAULT_MAX_ASPECT = 4.0

# How much of its bounding box the enclosed region fills. A real part
# is a solid blob; a hollow frame (a table, a bracket) is not.
DEFAULT_MIN_FILL = 0.35


class Candidate(object):
    """One region the sheet appears to enclose.

    `bbox` is (x, y, w, h) in image pixels and `area_mm2` the enclosed
    paper area, not the bounding box. `outline` is the traced polygon
    when one was asked for, else None. `fill` and `aspect` are the
    shape measures the filters used, kept so a caller can re-rank
    without running the search again.
    """

    def __init__(self, bbox, area_mm2, fill, aspect, centroid,
                 outline=None):
        self.bbox = bbox
        self.area_mm2 = area_mm2
        self.fill = fill
        self.aspect = aspect
        self.centroid = centroid
        self.outline = outline
        self._mm_per_pixel = None

    @property
    def size_mm(self):
        """(w, h) in mm. The INK component's bounding box, which is the
        outer edge of the drawn line - about one line width larger than
        the part. `outline_size_mm` is the real figure when traced."""
        return self._size_mm

    @property
    def outline_size_mm(self):
        """(w, h) in mm of the traced outline, or None when not traced.

        This is the number to trust: the enclosed region stops at the
        INSIDE of the drawn line, which is where a cut part actually
        ends. On the sample former it came out 0.006 in from the known
        dimension, against 0.047 in for the ink bounding box."""
        if not self.outline or self._mm_per_pixel is None:
            return None
        xs = [p[0] for p in self.outline]
        ys = [p[1] for p in self.outline]
        return ((max(xs) - min(xs)) * self._mm_per_pixel,
                (max(ys) - min(ys)) * self._mm_per_pixel)

    def __repr__(self):
        return ("Candidate(bbox=%s area=%.1fmm2 fill=%.2f aspect=%.1f)"
                % (self.bbox, self.area_mm2, self.fill, self.aspect))


def ink_mask(image, threshold=INK_THRESHOLD):
    """Binary ink mask from a BGR or grayscale image."""
    if image is None:
        raise ValueError("no image")
    gray = image
    if getattr(image, "ndim", 2) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return (gray < threshold).astype(np.uint8)


def _enclosed_mask(component):
    """Paper walled in by `component`, as a mask the same size.

    The component is padded by one pixel so the flood always has an
    outside to start from, even when the shape touches the edge of its
    own bounding box.
    """
    pad = cv2.copyMakeBorder(component, 1, 1, 1, 1,
                             cv2.BORDER_CONSTANT, value=0)
    paper = (1 - pad).astype(np.uint8)
    flooded = paper.copy()
    mask = np.zeros((pad.shape[0] + 2, pad.shape[1] + 2), np.uint8)
    cv2.floodFill(flooded, mask, (0, 0), 0)
    return (flooded > 0).astype(np.uint8)[1:-1, 1:-1]


def _orientation(mask, sigma=4.0):
    """Local LINE direction at each pixel, in degrees 0..180.

    From the structure tensor: the gradient points across a line, so
    its minor eigenvector points along one. Smoothed over `sigma`, a
    few line-widths, so the direction is the curve's and not one
    pixel's noise.
    """
    f = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 1.0)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=5)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=5)
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), sigma)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), sigma)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), sigma)
    ang = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    return (np.rad2deg(ang) + 90.0) % 180.0


def _line_kernel(length, angle_deg):
    """A one-pixel-wide line of `length`, at `angle_deg`."""
    s = int(length) | 1
    k = np.zeros((s, s), np.uint8)
    c = s // 2
    a = np.deg2rad(angle_deg)
    dx, dy = np.cos(a), np.sin(a)
    for t in np.linspace(-s / 2.0, s / 2.0, s * 3):
        x = int(round(c + t * dx))
        y = int(round(c + t * dy))
        if 0 <= x < s and 0 <= y < s:
            k[y, x] = 1
    return k


def close_along_contours(mask, length_px, bins=12):
    """Bridge breaks ALONG each line, never across it.

    A square close reaches equally in every direction, so a kernel
    big enough to span a break also spans the paper to the part
    beside it. On a nested printwood sheet that is fatal: measured on
    a peanut-scale fuselage sheet, the dashes in the formers' inner
    curves were 19 px while the lane between two formers was 11, so
    NO square kernel could bridge the one without welding the other -
    the block went from 33 ink components to 1 as the kernel grew.

    A break and a lane differ in DIRECTION, though. The break lies
    along the curve; the lane lies across it. So each pixel is
    dilated only along its own local line direction: a pixel on a
    north-south curve reaches north-south, towards the next dash, and
    never east-west towards its neighbour.

    On that sheet this took find_parts from 9 parts and no formers to
    21 parts including five formers traced through their notches.

    `length_px` is how far to reach - about the longest break to
    bridge. `bins` quantises direction; 12 (15 degrees) was enough.
    """
    ang = _orientation(mask)
    out = mask.copy()
    for b in range(int(bins)):
        lo = 180.0 * b / bins
        hi = 180.0 * (b + 1) / bins
        sel = ((ang >= lo) & (ang < hi) & (mask > 0)).astype(np.uint8)
        if int(sel.sum()) < 50:
            continue                    # too little ink to be a curve
        out |= cv2.dilate(sel, _line_kernel(length_px, 0.5 * (lo + hi)))
    return out


def find_parts(image, mm_per_pixel, gap_mm=DEFAULT_GAP_MM,
               min_area_mm2=DEFAULT_MIN_AREA_MM2,
               max_aspect=DEFAULT_MAX_ASPECT, min_fill=DEFAULT_MIN_FILL,
               trace=False, tolerance_mm=None, limit=0, progress=None,
               along_contours=False):
    """Locate the closed outlines on a sheet. Returns [Candidate].

    `trace` also extracts each candidate's polygon, using the same
    simplification tolerance as the interactive tracer so the geometry
    matches what painting seeds would have produced. `limit` caps how
    many are returned (largest first); 0 means all.

    Needs a calibrated scale: every threshold here is a real-world
    size, because "big enough to be a part" means millimetres, not
    pixels, and a bare pixel count means nothing across scan
    resolutions.
    """
    if not mm_per_pixel or mm_per_pixel <= 0.0:
        raise ValueError("find_parts needs a calibrated scale "
                         "(mm per pixel)")
    ink = ink_mask(image)
    mm2 = float(mm_per_pixel) * float(mm_per_pixel)

    if along_contours and gap_mm and gap_mm > 0.0:
        # Directional: bridge along each line, not across it. For a
        # sheet whose parts nest closer than their own breaks are
        # wide, where a square close must weld them together.
        reach = max(3, int(round(float(gap_mm) / float(mm_per_pixel))))
        if progress:
            progress("bridging along contours up to %.2f mm (%d px)"
                     % (gap_mm, reach))
        sealed = close_along_contours(ink, reach)
    elif gap_mm and gap_mm > 0.0:
        # A square close bridges a break roughly HALF the kernel wide:
        # the element has to span the gap and still touch ink on both
        # sides of it. Measured on a 6 px break, a 7 px kernel left it
        # open and a 15 px one closed it. So size the kernel at twice
        # the gap the caller asked to bridge.
        k = max(3, int(round(2.0 * float(gap_mm) / float(mm_per_pixel))))
        if k % 2 == 0:
            k += 1                      # odd kernels stay centred
        if progress:
            progress("bridging gaps up to %.2f mm (%d px)" % (gap_mm, k))
        sealed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                                  np.ones((k, k), np.uint8))
    else:
        sealed = ink

    count, labels, stats, _cent = cv2.connectedComponentsWithStats(
        sealed, 8)
    if progress:
        progress("examining %d ink component(s)" % max(0, count - 1))

    # The smallest side a part can have: a region below the area floor
    # cannot be wider than its own square root, so this skips most
    # components before the expensive flood.
    min_side_px = (min_area_mm2 ** 0.5) / float(mm_per_pixel) * 0.5

    out = []
    for i in range(1, count):
        x, y, w, h, _area = stats[i]
        if w < min_side_px or h < min_side_px:
            continue
        component = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        enclosed = _enclosed_mask(component)
        enclosed_px = int(enclosed.sum())
        if enclosed_px <= 0:
            continue                    # an open outline encloses nothing
        area_mm2 = enclosed_px * mm2
        if area_mm2 < min_area_mm2:
            continue
        aspect = max(w, h) / float(max(1, min(w, h)))
        if max_aspect and aspect > max_aspect:
            continue                    # a rule, a leader, a line of text
        fill = enclosed_px / float(max(1, w * h))
        if min_fill and fill < min_fill:
            continue                    # a hollow frame, not a part
        ys, xs = np.nonzero(enclosed)
        centroid = (float(x + xs.mean()), float(y + ys.mean()))
        cand = Candidate((int(x), int(y), int(w), int(h)), area_mm2,
                         fill, aspect, centroid)
        cand._size_mm = (w * float(mm_per_pixel), h * float(mm_per_pixel))
        cand._mm_per_pixel = float(mm_per_pixel)
        cand._enclosed = enclosed       # kept for the optional trace
        out.append(cand)

    out.sort(key=lambda c: -c.area_mm2)
    if limit and limit > 0:
        out = out[:limit]

    if trace:
        tol = (cont.TRACE_TOLERANCE_MM if tolerance_mm is None
               else tolerance_mm)
        eps = cont.tolerance_mm_to_px(tol, mm_per_pixel)
        for cand in out:
            x, y, _w, _h = cand.bbox
            # The enclosed region IS the part's interior, so its outer
            # contour is the outline - no segmentation needed.
            found = cont.extract_contours(
                cand._enclosed, epsilon_px=eps, min_area_px=4.0,
                keep_largest=True)
            rings = [[(px + x, py + y) for (px, py) in pts]
                     for pts, role in found if role == cont.ROLE_OUTER]
            cand.outline = rings[0] if rings else None
        if progress:
            progress("traced %d outline(s)"
                     % sum(1 for c in out if c.outline))
    for cand in out:
        del cand._enclosed              # do not hand back a whole mask
    return out
