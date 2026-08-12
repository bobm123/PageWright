"""Shared image-display helpers: ndarray -> Qt, and display proxies.

These are general utilities used by the trace canvas, the dewarp stage,
the OCR stage and the project controller alike. They used to live in
dewarp_stage.py, which made every other module import display plumbing
FROM one particular stage - extracted here so the dependency points the
right way.
"""

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 always present in the app
    cv2 = None

from PySide6.QtGui import QImage, QPixmap

DISPLAY_MAX = 2200    # cap the on-screen pixmap's longest side; big photos
                      # are shown via a downscaled proxy (full-image scene
                      # coords preserved) so repaints stay fast


def ndarray_to_qimage(bgr):
    """Convert a BGR (OpenCV) uint8 ndarray to a QImage (RGB888).

    A copy is returned so the QImage owns its buffer (the source array may
    be freed). Grayscale (2-D) input is accepted too.
    """
    arr = np.ascontiguousarray(bgr)
    if arr.ndim == 2:
        h, w = arr.shape
        img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
        return img.copy()
    h, w, ch = arr.shape
    if ch == 4:
        rgb = arr[:, :, [2, 1, 0, 3]]
        rgb = np.ascontiguousarray(rgb)
        img = QImage(rgb.data, w, h, 4 * w, QImage.Format_RGBA8888)
        return img.copy()
    rgb = np.ascontiguousarray(arr[:, :, ::-1])   # BGR -> RGB
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


def ndarray_to_qpixmap(bgr):
    return QPixmap.fromImage(ndarray_to_qimage(bgr))


def display_downscale(bgr, max_side=DISPLAY_MAX):
    """Downscale a BGR image for ON-SCREEN display only.

    Returns (display_bgr, full_w, full_h). Big photos are shrunk so Qt
    never has to build or (smooth-)scale a tens-of-megapixels pixmap - the
    caller keeps the full-res array for the actual image math and scales
    the display item back up to full_w x full_h so scene coordinates stay
    in full-image pixels."""
    full_h, full_w = bgr.shape[:2]
    longest = max(full_w, full_h)
    if cv2 is not None and longest > max_side:
        f = max_side / float(longest)
        small = cv2.resize(bgr,
                           (max(1, int(round(full_w * f))),
                            max(1, int(round(full_h * f)))),
                           interpolation=cv2.INTER_AREA)
        return small, full_w, full_h
    return bgr, full_w, full_h
