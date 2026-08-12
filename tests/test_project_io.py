"""Tests for core.project_io -- pure Python (no Qt/cv2)."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import project_io as pio  # noqa: E402
from pagewright.model import (Contour, PageEntry, Project, Style,  # noqa: E402
                              TracedObject)


def _sample_project():
    p = Project()
    p.source_image_path = "/photos/IMG.png"
    p.pixel_width, p.pixel_height, p.dpi = 3636, 3770, 300
    p.calibration.mm_per_pixel = 0.123
    p.calibration.display_unit = "cm"
    p.margin_mm = 7.5
    o1 = TracedObject(name="Polygon 1",
                      style=Style(stroke="#ff0000", stroke_width_mm=0.8))
    o1.contours.append(Contour(points=[(0, 0), (100, 0), (100, 100), (0, 100)],
                               role="outer"))
    o1.contours.append(Contour(points=[(30, 30), (70, 30), (70, 70), (30, 70)],
                               role="hole"))
    o2 = TracedObject(name="Polygon 2")
    o2.contours.append(Contour(points=[(200, 200), (260, 210), (240, 300)],
                               role="outer"))
    p.objects = [o1, o2]
    return p


def test_dict_round_trip_preserves_everything():
    p = _sample_project()
    p2 = pio.project_from_dict(pio.project_to_dict(p))
    assert p2.source_image_path == "/photos/IMG.png"
    assert (p2.pixel_width, p2.pixel_height, p2.dpi) == (3636, 3770, 300)
    assert p2.calibration.mm_per_pixel == pytest.approx(0.123)
    assert p2.calibration.display_unit == "cm"
    assert p2.margin_mm == pytest.approx(7.5)
    assert [o.name for o in p2.objects] == ["Polygon 1", "Polygon 2"]
    assert p2.objects[0].style.stroke == "#ff0000"
    assert [c.role for c in p2.objects[0].contours] == ["outer", "hole"]
    assert p2.objects[0].contours[0].points[2] == (100.0, 100.0)


def test_file_round_trip_is_stable():
    p = _sample_project()
    fp = tempfile.mktemp(suffix=".tiproj.json")
    pio.save_project(p, fp)
    p3 = pio.load_project(fp)
    assert pio.project_to_dict(p3) == pio.project_to_dict(p)


def test_uncalibrated_round_trip():
    p = Project()
    d = pio.project_to_dict(p)
    p2 = pio.project_from_dict(d)
    assert p2.calibration.mm_per_pixel is None
    assert p2.objects == []


def test_tiling_round_trip():
    p = _sample_project()
    p.tiling = {"page": "A4", "landscape": True, "margin_mm": 8.0,
                "overlap_mm": 15.0, "overlap_unit": "percent",
                "fixed_grid": True, "grid_cols": 3, "grid_rows": 4,
                "scale_percent": 75, "embed_photo": True,
                "crop_photo": True, "filled": True}
    p2 = pio.project_from_dict(pio.project_to_dict(p))
    assert p2.tiling == p.tiling


def test_old_tiling_dict_gains_new_defaults():
    # a pre-upgrade tiling dict (no overlap_unit/grid keys) loads with
    # the new keys defaulted, keeping older project files working
    p = _sample_project()
    d = pio.project_to_dict(p)
    d["tiling"] = {"page": "A4", "landscape": True, "margin_mm": 8.0,
                   "overlap_mm": 15.0, "scale_percent": 75,
                   "embed_photo": True, "crop_photo": True, "filled": True}
    t = pio.project_from_dict(d).tiling
    assert t["page"] == "A4" and t["overlap_mm"] == 15.0
    assert t["overlap_unit"] == "mm" and t["fixed_grid"] is False
    assert t["grid_cols"] == 2 and t["grid_rows"] == 2


def test_missing_tiling_gets_defaults():
    from pagewright.model import default_tiling
    p = _sample_project()
    d = pio.project_to_dict(p)
    d.pop("tiling")                       # simulate an older project file
    assert pio.project_from_dict(d).tiling == default_tiling()


def test_partial_tiling_merges_defaults():
    p = _sample_project()
    d = pio.project_to_dict(p)
    d["tiling"] = {"page": "Legal"}
    t = pio.project_from_dict(d).tiling
    assert t["page"] == "Legal" and t["overlap_mm"] == 10.0


def test_unsupported_version_raises():
    with pytest.raises(pio.ProjectIOError):
        pio.project_from_dict({"version": 999})


def test_bad_file_raises():
    bad = tempfile.mktemp(suffix=".json")
    with open(bad, "w") as fh:
        fh.write("{not valid json")
    with pytest.raises(pio.ProjectIOError):
        pio.load_project(bad)


def test_natural_key_orders_pages():
    names = ["page10.png", "page2.png", "Page1.png"]
    assert sorted(names, key=pio.natural_key) == [
        "Page1.png", "page2.png", "page10.png"]


def test_pages_round_trip():
    p = _sample_project()
    p.pages = [PageEntry("a.png", pio.objects_to_list(p.objects)),
               PageEntry("b.png")]
    p.current_page = 1
    d = pio.project_to_dict(p)
    p2 = pio.project_from_dict(d)
    assert len(p2.pages) == 2 and p2.current_page == 1
    assert p2.pages[0].source_path == "a.png"
    # per-page objects deserialize identically to top-level ones
    objs = pio.objects_from_list(p2.pages[0].objects)
    assert len(objs) == len(p.objects)


def test_old_project_becomes_one_page_job():
    p = _sample_project()
    d = pio.project_to_dict(p)
    d.pop("pages", None)
    d.pop("current_page", None)
    p2 = pio.project_from_dict(d)
    assert len(p2.pages) == 1 and p2.current_page == 0
    assert p2.pages[0].source_path == p.source_image_path


def test_page_roi_round_trips():
    p = _sample_project()
    p.pages = [PageEntry("a.png", roi=[10.0, 20.0, 300.0, 400.0]),
               PageEntry("b.png")]
    p2 = pio.project_from_dict(pio.project_to_dict(p))
    assert p2.pages[0].roi == [10.0, 20.0, 300.0, 400.0]
    assert p2.pages[1].roi is None
