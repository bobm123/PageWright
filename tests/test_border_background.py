"""The trace area's border standing in for 'outside' marks.

Boxing one part already says where it ends, so requiring an outside
stroke as well asks the same question twice. These pin the two halves
of that: the border seeds appear when nobody marked outside, and they
stay away when somebody did.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

pytest.importorskip("PySide6")

from pagewright.core import segmentation as seg
from pagewright.ui.editing_controller import _with_border_background


def _fg():
    return [seg.Stroke("fg", [(50, 50)], 5.0)]


def test_border_background_is_added_when_none_was_marked():
    out = _with_border_background(_fg(), 200, 150)
    assert [s.label for s in out] == ["fg", "bg", "bg", "bg", "bg"]


def test_the_border_seeds_sit_inside_the_area():
    out = _with_border_background(_fg(), 200, 150)
    pts = [p for s in out if s.label == "bg" for p in s.points]
    xs = [x for (x, _y) in pts]
    ys = [y for (_x, y) in pts]
    # Inside the box on all four sides, so a part drawn hard against
    # the edge still has paper between it and the seed.
    assert min(xs) >= 1 and max(xs) <= 198
    assert min(ys) >= 1 and max(ys) <= 148


def test_the_border_covers_all_four_edges():
    out = _with_border_background(_fg(), 200, 150)
    edges = [s.points for s in out if s.label == "bg"]
    horizontal = [e for e in edges if len({y for (_x, y) in e}) == 1]
    vertical = [e for e in edges if len({x for (x, _y) in e}) == 1]
    assert len(horizontal) == 2 and len(vertical) == 2


def test_a_users_own_outside_mark_is_left_alone():
    # One deliberate outside stroke means the user is steering the
    # segmenter; four more would override them.
    strokes = _fg() + [seg.Stroke("bg", [(10, 10)], 5.0)]
    assert _with_border_background(strokes, 200, 150) is strokes


def test_a_tiny_area_gets_no_border():
    # There is no room for a border that is not also the part.
    strokes = _fg()
    assert _with_border_background(strokes, 6, 6) is strokes


def test_the_border_makes_a_mask_grabcut_can_use():
    # The real contract: these strokes must paint actual background
    # into the seed mask, or GrabCut gains nothing from them.
    np = pytest.importorskip("numpy")
    out = _with_border_background(_fg(), 120, 90)
    mask = seg.build_seed_mask((90, 120), out)
    assert np.any(mask == seg.LABEL_BG)
    assert np.any(mask == seg.LABEL_FG)
    # ...and the background is at the rim, not over the middle.
    assert mask[45, 60] != seg.LABEL_BG


def test_a_brush_wider_than_the_area_cannot_bury_the_border():
    """The crash this guards: the default brush is sized for a whole
    sheet, so boxing one small part gives a foreground stroke wider
    than the box. Painted in the usual order the fg covers the implied
    border, GrabCut gets no background at all, and it fails with
    bgdSamples.empty(). Implied seeds therefore paint LAST."""
    np = pytest.importorskip("numpy")
    # 150 px brush on a 140 px region, as the GUI really does it
    strokes = [seg.Stroke("fg", [(70, 70), (78, 78)], 150.0)]
    out = _with_border_background(strokes, 140, 140)
    mask = seg.build_seed_mask((140, 140), out)
    assert np.any(mask == seg.LABEL_BG), "the border was buried"
    assert np.any(mask == seg.LABEL_FG)


def test_a_deliberate_inside_mark_still_beats_a_deliberate_outside_one():
    # Only IMPLIED seeds move to the back. Between two real marks the
    # inside one is still the later word.
    np = pytest.importorskip("numpy")
    strokes = [seg.Stroke("bg", [(50, 50)], 20.0),
               seg.Stroke("fg", [(50, 50)], 10.0)]
    mask = seg.build_seed_mask((120, 120), strokes)
    assert mask[50, 50] == seg.LABEL_FG


def test_implied_seeds_survive_the_downscale_path():
    # Big regions are segmented at reduced size; the flag has to travel
    # with the stroke or the ordering silently reverts there.
    out = _with_border_background(
        [seg.Stroke("fg", [(500, 500)], 900.0)], 1600, 1200)
    scaled = seg._scaled_strokes(out, 0.5)
    implied = [s for s in scaled if getattr(s, "implied", False)]
    assert len(implied) == 4
