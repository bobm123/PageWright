"""Tests for core.segmentation -- require NumPy + OpenCV (skipped otherwise)."""

import os
import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import segmentation as seg  # noqa: E402


def test_build_seed_mask_labels():
    fg = seg.Stroke("fg", [(50, 50)], radius_px=5)
    bg = seg.Stroke("bg", [(10, 10)], radius_px=5)
    mask = seg.build_seed_mask((100, 100), [fg, bg])

    assert mask.shape == (100, 100)
    # Untouched pixels are probable background.
    assert mask[80, 80] == seg.LABEL_PR_BG
    # Painted centres carry their definite labels.
    assert mask[50, 50] == seg.LABEL_FG
    assert mask[10, 10] == seg.LABEL_BG


def test_foreground_wins_overlap():
    # bg painted first, fg over the same spot -> fg should win.
    bg = seg.Stroke("bg", [(40, 40)], radius_px=8)
    fg = seg.Stroke("fg", [(40, 40)], radius_px=4)
    mask = seg.build_seed_mask((80, 80), [bg, fg])
    assert mask[40, 40] == seg.LABEL_FG


def test_has_foreground():
    assert not seg.has_foreground([seg.Stroke("bg", [(1, 1)], 3)])
    assert not seg.has_foreground([seg.Stroke("fg", [], 3)])
    assert seg.has_foreground([seg.Stroke("fg", [(1, 1)], 3)])


def test_grabcut_requires_foreground():
    img = np.zeros((40, 40, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        seg.grabcut_from_strokes(img, [seg.Stroke("bg", [(5, 5)], 3)])


def test_grabcut_returns_binary_mask():
    # A bright square on a dark field; seed inside (fg) and the corner (bg).
    img = np.zeros((80, 80, 3), dtype=np.uint8)
    img[25:55, 25:55] = (240, 240, 240)
    fg = seg.Stroke("fg", [(40, 40)], radius_px=5)
    bg = seg.Stroke("bg", [(5, 5)], radius_px=5)
    mask = seg.grabcut_from_strokes(img, [fg, bg], iterations=3)
    assert mask.shape == (80, 80)
    assert set(np.unique(mask)).issubset({0, 1})
    # The seeded interior should be classified foreground.
    assert mask[40, 40] == 1


def test_grabcut_downscale_returns_full_resolution():
    # Image larger than max_dim must still return a full-resolution mask.
    img = np.zeros((600, 800, 3), dtype=np.uint8)
    img[150:450, 250:550] = (240, 240, 240)
    fg = seg.Stroke("fg", [(400, 300)], radius_px=20)
    bg = seg.Stroke("bg", [(20, 20)], radius_px=20)
    mask = seg.grabcut_from_strokes(img, [fg, bg], iterations=3, max_dim=200)
    assert mask.shape == (600, 800)            # mapped back to full size
    assert set(np.unique(mask)).issubset({0, 1})
    assert mask[300, 400] == 1


def test_scaled_strokes_scales_points_and_radius():
    s = seg.Stroke("fg", [(10, 20), (30, 40)], radius_px=8)
    scaled = seg._scaled_strokes([s], 0.5)[0]
    assert scaled.points == [(5.0, 10.0), (15.0, 20.0)]
    assert scaled.radius_px == 4.0
