"""Named sheet stock: thickness resolution, persistence, assignment.

Most parts of a model come off one sheet and a couple come off another,
so thickness belongs to the sheet rather than being retyped per part.
These check the three-level fallback and that a sheet reference survives
the project file - a sheet name that did not round-trip would silently
build every part at the default.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import project_io
from pagewright.core.project_io import ProjectIOError
from pagewright.model import Build, Contour, Project, Sheet, TracedObject


def _project():
    p = Project()
    p.pixel_width, p.pixel_height = 100, 100
    p.calibration.mm_per_pixel = 0.1
    return p


def _part(name, **build):
    return TracedObject(name, [Contour([(0, 0), (10, 0), (10, 10)])],
                        build=Build(**build))


def test_thickness_comes_from_the_assigned_sheet():
    p = _project()
    p.sheets = [Sheet("1/16 ply", 1.5875), Sheet("1/8 ply", 3.175)]
    assert p.part_thickness_mm(_part("F2", sheet="1/16 ply")) == 1.5875
    assert p.part_thickness_mm(_part("Fin", sheet="1/8 ply")) == 3.175


def test_a_parts_own_thickness_beats_its_sheet():
    p = _project()
    p.sheets = [Sheet("1/8 ply", 3.175)]
    part = _part("Odd", sheet="1/8 ply", thickness_mm=0.8)
    assert p.part_thickness_mm(part) == 0.8


def test_unassigned_parts_use_the_sheet_wide_default():
    p = _project()
    p.sheet_thickness_mm = 1.5875
    p.sheets = [Sheet("1/8 ply", 3.175)]
    assert p.part_thickness_mm(_part("Bare")) == 1.5875


def test_a_dangling_sheet_name_falls_back_rather_than_failing():
    # The sheet was deleted but the parts still name it. A buildable
    # part at the default thickness beats a part that cannot build.
    p = _project()
    p.sheet_thickness_mm = 1.5875
    p.sheets = [Sheet("1/16 ply", 1.5875)]
    assert p.part_thickness_mm(_part("Ghost", sheet="1/4 balsa")) == 1.5875


def test_a_sheet_with_no_thickness_yet_falls_back():
    p = _project()
    p.sheet_thickness_mm = 1.5875
    p.sheets = [Sheet("not measured", None)]
    assert p.part_thickness_mm(_part("X", sheet="not measured")) == 1.5875


def test_thickness_is_unknown_when_nothing_says_otherwise():
    assert _project().part_thickness_mm(_part("X")) is None


def test_sheets_and_assignments_round_trip(tmp_path):
    p = _project()
    p.sheets = [Sheet("1/16 ply", 1.5875), Sheet("1/8 ply", 3.175)]
    p.objects = [_part("F2", sheet="1/16 ply"), _part("Fin", sheet="1/8 ply")]
    path = str(tmp_path / "t.tiproj.json")
    project_io.save_project(p, path)

    q = project_io.load_project(path)
    assert [(s.name, s.thickness_mm) for s in q.sheets] == [
        ("1/16 ply", 1.5875), ("1/8 ply", 3.175)]
    assert [o.build.sheet for o in q.objects] == ["1/16 ply", "1/8 ply"]
    assert q.part_thickness_mm(q.objects[1]) == 3.175


def test_a_project_with_no_sheets_writes_no_sheets_key(tmp_path):
    # Unchanged projects stay byte-identical to before the feature.
    p = _project()
    p.objects = [_part("X")]
    path = str(tmp_path / "t.tiproj.json")
    project_io.save_project(p, path)
    import json
    with open(path) as fh:
        assert "sheets" not in json.load(fh)


def test_an_unnamed_sheet_is_rejected_on_load(tmp_path):
    import json
    path = str(tmp_path / "bad.tiproj.json")
    p = _project()
    project_io.save_project(p, path)
    with open(path) as fh:
        d = json.load(fh)
    d["sheets"] = [{"thickness_mm": 1.5}]
    with open(path, "w") as fh:
        json.dump(d, fh)
    with pytest.raises(ProjectIOError):
        project_io.load_project(path)


def test_a_negative_sheet_thickness_is_rejected_on_load(tmp_path):
    import json
    path = str(tmp_path / "bad.tiproj.json")
    p = _project()
    project_io.save_project(p, path)
    with open(path) as fh:
        d = json.load(fh)
    d["sheets"] = [{"name": "ply", "thickness_mm": -1.0}]
    with open(path, "w") as fh:
        json.dump(d, fh)
    with pytest.raises(ProjectIOError):
        project_io.load_project(path)


def test_build_with_only_a_sheet_is_not_empty():
    # is_empty decides whether the build is saved at all, so a part whose
    # ONLY setting is its sheet must not be treated as unset.
    assert not Build(sheet="1/16 ply").is_empty()
    assert Build().is_empty()


def test_build_copy_carries_the_sheet():
    assert Build(sheet="1/8 ply").copy().sheet == "1/8 ply"
