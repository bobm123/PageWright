"""Shared wheel-zoom behavior for every QGraphicsView in the app.

One definition of "how zooming feels": the mouse wheel zooms by
ZOOM_STEP per notch, anchored at the cursor position (the dewarp-stage
behavior), in every tool - trace canvas, dewarp panes, calibration
picking included. Views call :func:`wheel_zoom` from their wheelEvent
and :func:`anchor_under_cursor` once at construction.
"""

from PySide6.QtWidgets import QGraphicsView

ZOOM_STEP = 1.25


def anchor_under_cursor(view):
    """Make programmatic scaling zoom toward the mouse position."""
    view.setTransformationAnchor(
        QGraphicsView.ViewportAnchor.AnchorUnderMouse)


def wheel_zoom(view, event, min_scale=None, max_scale=None):
    """Apply one wheel notch of cursor-anchored zoom to `view`.

    Optional min/max clamp the view's absolute scale (transform m11).
    Returns True when the event was consumed (callers should not fall
    through to the default wheel-scroll)."""
    delta = event.angleDelta().y() or event.angleDelta().x()
    if delta == 0:
        return False
    factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
    if min_scale is not None or max_scale is not None:
        cur = view.transform().m11()
        new = cur * factor
        if min_scale is not None and new < min_scale:
            factor = min_scale / cur
        if max_scale is not None and new > max_scale:
            factor = max_scale / cur
        if abs(factor - 1.0) < 1e-9:
            return True
    view.scale(factor, factor)
    return True
