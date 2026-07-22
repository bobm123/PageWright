"""Contour extraction and simplification (Phase 2).

findContours with RETR_CCOMP gives a two-level hierarchy: outer object
boundaries at the top level and interior holes as their children (see PLAN.md
sec. 3). approxPolyDP (Douglas-Peucker) then simplifies each contour into an
editable polygon. cv2/NumPy only; the surrounding code is plain loops so it
ports cleanly to C/C++.
"""

import cv2

ROLE_OUTER = "outer"
ROLE_HOLE = "hole"


def extract_contours(binary_mask, epsilon_px=2.0, min_area_px=25.0):
    """Extract and simplify contours from a binary (0/1) mask.

    epsilon_px:   Douglas-Peucker tolerance (larger = simpler polygons).
    min_area_px:  drop contours whose area is below this (speckle removal).

    Returns a list of (points, role) where points is [(x, y), ...] and role is
    ROLE_OUTER or ROLE_HOLE. Order preserves cv2's, so a hole follows the outer
    contour it belongs to.
    """
    # findContours expects an 8-bit single-channel image; scale 1 -> 255.
    img = (binary_mask.astype("uint8")) * 255
    found = cv2.findContours(img, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    # OpenCV 3 returns (image, contours, hierarchy); 4 returns (contours, hier).
    contours, hierarchy = found[-2], found[-1]

    results = []
    if hierarchy is None:
        return results
    hierarchy = hierarchy[0]  # shape (N, 4): [next, prev, first_child, parent]

    for i, cnt in enumerate(contours):
        if cv2.contourArea(cnt) < min_area_px:
            continue
        approx = cv2.approxPolyDP(cnt, epsilon_px, True)
        points = [(float(p[0][0]), float(p[0][1])) for p in approx]
        if len(points) < 3:
            continue
        parent = hierarchy[i][3]
        role = ROLE_HOLE if parent != -1 else ROLE_OUTER
        results.append((points, role))
    return results
