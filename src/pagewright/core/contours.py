"""Contour extraction and simplification (Phase 2).

findContours with RETR_CCOMP gives a two-level hierarchy: outer object
boundaries at the top level and interior holes as their children (see PLAN.md
sec. 3). approxPolyDP (Douglas-Peucker) then simplifies each contour into an
editable polygon. cv2/NumPy only; the surrounding code is plain loops so it
ports cleanly to C/C++.

Two controls decide whether a trace is usable, and both were originally
derived from the region's pixel size, which is the wrong unit:

  * SIMPLIFICATION. `epsilon_px` is how far a vertex may move when the
    polygon is simplified. Expressed in pixels it changes meaning with
    every scan resolution; `tolerance_mm_to_px` converts a REAL-WORLD
    tolerance (the default TRACE_TOLERANCE_MM) into pixels, so the same
    setting gives the same vertex count on the same drawing at any DPI.
    Measured on a 300 DPI model-aircraft former: 0.13 mm gave 254
    vertices, 0.34 mm gave 72 (a hand trace of that part is ~64).
  * SPECKLE REJECTION. An absolute `min_area_px` cannot tell "250x
    smaller than the real shape" from "small but real". A drawing sheet
    holds hundreds of unrelated ink fragments, and any that fall inside
    the selected area come back as extra polygons. `min_area_frac`
    drops contours below a fraction of the LARGEST contour found, which
    scales with whatever was actually traced; `keep_largest` goes
    further and keeps only the biggest outer contour with its holes.
"""

import cv2

ROLE_OUTER = "outer"
ROLE_HOLE = "hole"

# Default simplification tolerance, in millimetres of real-world size.
# 0.35 mm is finer than a pencil line on a full-size plan and lands near
# a hand trace's vertex count; the UI exposes a Coarse..Fine slider.
TRACE_TOLERANCE_MM = 0.35

# Contours smaller than this fraction of the largest one are debris from
# neighbouring parts caught inside the trace area. On the sample former
# the real shape was 250x the biggest stray, so 2% is not a close call.
MIN_AREA_FRACTION = 0.02


def tolerance_mm_to_px(tolerance_mm, mm_per_pixel, fallback_px=2.0):
    """Real-world simplification tolerance -> pixels.

    Falls back to `fallback_px` when the project is not calibrated, so
    tracing still works before the scale is known (the result then just
    is not scale-independent).
    """
    if not mm_per_pixel or mm_per_pixel <= 0.0 or not tolerance_mm:
        return float(fallback_px)
    return max(0.5, float(tolerance_mm) / float(mm_per_pixel))


def extract_contours(binary_mask, epsilon_px=2.0, min_area_px=25.0,
                     min_area_frac=0.0, keep_largest=False):
    """Extract and simplify contours from a binary (0/1) mask.

    epsilon_px:     Douglas-Peucker tolerance (larger = simpler polygons).
                    Use `tolerance_mm_to_px` to set it from a real-world
                    tolerance instead of guessing in pixels.
    min_area_px:    absolute area floor (speckle removal).
    min_area_frac:  area floor as a fraction of the LARGEST contour found
                    (e.g. MIN_AREA_FRACTION). Catches debris from
                    neighbouring parts, which an absolute floor cannot:
                    a stray is small RELATIVE TO the traced shape, and
                    how small that is in pixels depends on the zoom and
                    the drawing.
    keep_largest:   keep only the biggest outer contour and the holes
                    that belong to it. Right when the user seeded one
                    object, which is the normal case.

    Returns a list of (points, role) where points is [(x, y), ...] and role is
    ROLE_OUTER or ROLE_HOLE. A hole always follows the outer contour it
    belongs to, so callers can rely on that order.
    """
    # findContours expects an 8-bit single-channel image; scale 1 -> 255.
    img = (binary_mask.astype("uint8")) * 255
    found = cv2.findContours(img, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    # OpenCV 3 returns (image, contours, hierarchy); 4 returns (contours, hier).
    contours, hierarchy = found[-2], found[-1]

    if hierarchy is None or len(contours) == 0:
        return []
    hierarchy = hierarchy[0]  # shape (N, 4): [next, prev, first_child, parent]

    areas = [abs(cv2.contourArea(c)) for c in contours]

    # The relative floor is measured against the largest OUTER contour:
    # a big hole must not raise the bar for the shape that contains it.
    outer_areas = [a for i, a in enumerate(areas) if hierarchy[i][3] == -1]
    biggest = max(outer_areas) if outer_areas else 0.0
    floor = float(min_area_px)
    if min_area_frac and biggest > 0.0:
        floor = max(floor, float(min_area_frac) * biggest)

    keep_parent = None
    if keep_largest and biggest > 0.0:
        for i, a in enumerate(areas):
            if hierarchy[i][3] == -1 and a == biggest:
                keep_parent = i
                break

    results = []
    for i, cnt in enumerate(contours):
        parent = hierarchy[i][3]
        is_hole = parent != -1
        if keep_parent is not None:
            # only the chosen outer contour and its own holes
            if not (i == keep_parent or parent == keep_parent):
                continue
        if areas[i] < floor:
            # A hole is kept or dropped on its own size: a real cutout can
            # be far smaller than its part and still be wanted.
            if not is_hole or areas[i] < min_area_px:
                continue
        approx = cv2.approxPolyDP(cnt, epsilon_px, True)
        points = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(points) < 3:
            continue
        role = ROLE_HOLE if is_hole else ROLE_OUTER
        results.append((points, role))
    return results
