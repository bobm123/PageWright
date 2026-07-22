"""Interactive segmentation engines (Phase 2).

Workflow (see PLAN.md sec. 3): the user paints foreground (inside) and
background (outside) seed strokes. Those strokes are rasterised into a GrabCut
label mask; GrabCut refines it into a binary foreground mask. Watershed is kept
as an alternate engine.

For multi-megapixel photos GrabCut runs on a downscaled copy and the resulting
mask is mapped back to full resolution, keeping the UI responsive.

Implemented with OpenCV (cv2) and NumPy so calls port to the C++ API. Heavy
lifting stays in cv2 calls; surrounding code is plain arithmetic/loops.
"""

import cv2
import numpy as np

# Seed labels (match OpenCV's GrabCut conventions).
LABEL_BG = int(cv2.GC_BGD)        # definite background
LABEL_FG = int(cv2.GC_FGD)        # definite foreground
LABEL_PR_BG = int(cv2.GC_PR_BGD)  # probable background
LABEL_PR_FG = int(cv2.GC_PR_FGD)  # probable foreground


class Stroke:
    """A single brush stroke of seed points, all of one label.

    label: "fg" or "bg".
    points: list of (x_px, y_px).
    radius_px: brush radius in pixels.
    """

    def __init__(self, label, points, radius_px):
        self.label = label
        self.points = list(points)
        self.radius_px = float(radius_px)


def _paint_stroke(mask, stroke, value):
    """Rasterise one stroke onto `mask` with the given label `value`."""
    r = max(1, int(round(stroke.radius_px)))
    pts = stroke.points
    if not pts:
        return
    if len(pts) == 1:
        x, y = pts[0]
        cv2.circle(mask, (int(round(x)), int(round(y))), r, value, -1)
        return
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cv2.line(mask, (int(round(x0)), int(round(y0))),
                 (int(round(x1)), int(round(y1))), value, thickness=2 * r)
        cv2.circle(mask, (int(round(x0)), int(round(y0))), r, value, -1)
    xl, yl = pts[-1]
    cv2.circle(mask, (int(round(xl)), int(round(yl))), r, value, -1)


def build_seed_mask(shape_hw, strokes):
    """Build a GrabCut label mask from seed strokes.

    shape_hw: (height, width) of the source image.
    strokes:  iterable of Stroke.
    Returns:  HxW uint8 mask of LABEL_* values, initialised to probable bg.
    """
    h, w = shape_hw
    mask = np.full((h, w), LABEL_PR_BG, dtype=np.uint8)
    # Paint background first, then foreground, so overlapping fg wins.
    for s in strokes:
        if s.label == "bg":
            _paint_stroke(mask, s, LABEL_BG)
    for s in strokes:
        if s.label == "fg":
            _paint_stroke(mask, s, LABEL_FG)
    return mask


def has_foreground(strokes):
    """True if at least one foreground stroke with points is present."""
    for s in strokes:
        if s.label == "fg" and s.points:
            return True
    return False


def _scaled_strokes(strokes, scale):
    """Return copies of `strokes` with points and radius multiplied by scale."""
    out = []
    for s in strokes:
        pts = [(x * scale, y * scale) for (x, y) in s.points]
        out.append(Stroke(s.label, pts, max(1.0, s.radius_px * scale)))
    return out


def grabcut_from_strokes(image_bgr, strokes, iterations=5, max_dim=800):
    """Run GrabCut seeded by `strokes` and return a full-resolution mask.

    image_bgr: HxWx3 uint8 array.
    max_dim:   if the longest edge exceeds this, GrabCut runs on a downscaled
               copy and the result is mapped back, so multi-megapixel photos
               stay responsive. Set to None/0 to disable downscaling.
    Returns:   HxW uint8 mask (1 = foreground, 0 = background) at full size.
    Raises ValueError if no foreground seed was provided.
    """
    if not has_foreground(strokes):
        raise ValueError("mark at least one foreground (inside) stroke")
    h, w = image_bgr.shape[:2]
    long_edge = max(h, w)

    if not max_dim or long_edge <= max_dim:
        label_mask = build_seed_mask((h, w), strokes)
        return grabcut_mask(image_bgr, label_mask, iterations=iterations)

    # Downscale image + seeds, run GrabCut, then map the mask back to full size.
    scale = float(max_dim) / float(long_edge)
    sw = max(1, int(round(w * scale)))
    sh = max(1, int(round(h * scale)))
    small = cv2.resize(image_bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    label_mask = build_seed_mask((sh, sw), _scaled_strokes(strokes, scale))
    small_mask = grabcut_mask(small, label_mask, iterations=iterations)
    # Linear upscale, then blur+threshold so the boundary doesn't inherit the
    # blocky stair-steps of the low-res mask (which would otherwise become
    # hundreds of contour vertices).
    up = cv2.resize(small_mask.astype(np.float32), (w, h),
                    interpolation=cv2.INTER_LINEAR)
    blur = max(1.0, 1.0 / scale)              # smoothing scales with upsampling
    up = cv2.GaussianBlur(up, (0, 0), blur)
    return (up >= 0.5).astype(np.uint8)


def grabcut_mask(image_bgr, seed_mask, iterations=5):
    """Run GrabCut on a prepared label mask.

    seed_mask: HxW array of LABEL_* values (a copy is made before GrabCut).
    Returns:   HxW uint8 mask (1 = foreground, 0 = background).
    """
    mask = seed_mask.copy()
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(image_bgr, mask, None, bgd_model, fgd_model,
                iterations, cv2.GC_INIT_WITH_MASK)
    fg = (mask == LABEL_FG) | (mask == LABEL_PR_FG)
    return fg.astype(np.uint8)


def watershed_mask(image_bgr, seed_mask):
    """Alternate engine: marker-based Watershed segmentation."""
    raise NotImplementedError("Watershed engine is planned but not yet wired")
