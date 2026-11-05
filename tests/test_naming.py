"""The plan's own numbering convention, and sorting the parts list.

The expensive mistake here is quiet: a part whose name is read as the
wrong KIND of part gets stood up on the wrong plane, and nothing says
so until the model is wrong. The F-86 sheet is the case that forced
this - it numbers formers 1..10 and spends F1..F6 on longerons, with
the higher F numbers on cockpit details.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import naming                       # noqa: E402
from pagewright.core import project_io as pio            # noqa: E402
from pagewright.core import recipe_export as rx          # noqa: E402
from pagewright.model import Project                     # noqa: E402

# How the F-86 sheet numbers things: bare numbers are the formers,
# F and T are longerons, S is a stringer. Not the usual convention.
F86 = {"": "YZ", "f": None, "t": None, "s": None}


# ----- splitting a name into prefix + number ------------------------------

@pytest.mark.parametrize("name,expect", [
    ("F3", ("f", 3)), ("F-3", ("f", 3)), ("f 3", ("f", 3)),
    ("former 2", ("former", 2)),
    ("10", ("", 10)), ("1", ("", 1)),
    ("T4", ("t", 4)), ("Stabilizer-2", ("stabilizer", 2)),
])
def test_a_part_number_splits_into_prefix_and_number(name, expect):
    assert naming.split_prefix(name) == expect


@pytest.mark.parametrize("name", ["Rudder", "Fin", "B", "F", "",
                                  None, "F3a", "6a"])
def test_a_name_without_a_trailing_number_is_not_a_part_number(name):
    """A named part says nothing about its plane, and must not be
    guessed at."""
    assert naming.split_prefix(name) is None


# ----- the default convention ---------------------------------------------

def test_the_default_scheme_is_the_usual_plan_convention():
    s = naming.NamingScheme()
    assert s.plane_for("F-3") == "YZ"
    assert s.plane_for("R2") == "XZ"
    assert s.plane_for("rib 4") == "XZ"
    assert s.plane_for("bulkhead 1") == "YZ"


def test_the_default_scheme_says_nothing_about_bare_numbers():
    # On a typical plan "6" could be anything, so guessing is worse
    # than declining.
    assert naming.NamingScheme().plane_for("6") is None


# ----- a plan that numbers differently ------------------------------------

def test_bare_numbers_can_be_the_formers():
    assert naming.NamingScheme(F86).plane_for("6") == "YZ"
    assert naming.NamingScheme(F86).plane_for("10") == "YZ"


@pytest.mark.parametrize("name", ["F1", "F6", "T3", "T4", "S1", "S4"])
def test_a_prefix_mapped_to_none_implies_nothing(name):
    """THE bug this prevents: on this sheet F is a longeron, running
    lengthwise. Reading it as a former would stand it on end, and the
    higher F numbers are cockpit details - not structure at all."""
    assert naming.NamingScheme(F86).plane_for(name) is None


def test_the_default_would_have_got_this_sheet_backwards():
    # Kept as a test because it is the whole reason the scheme is
    # per-project rather than a constant.
    default = naming.NamingScheme()
    assert default.plane_for("F1") == "YZ"      # wrong for this plan
    assert default.plane_for("6") is None       # also wrong
    sheet = naming.NamingScheme(F86)
    assert sheet.plane_for("F1") is None        # right
    assert sheet.plane_for("6") == "YZ"         # right


def test_an_unlisted_prefix_implies_nothing():
    assert naming.NamingScheme(F86).plane_for("R2") is None


# ----- the project carries the scheme -------------------------------------

def test_a_project_defaults_to_the_usual_convention():
    assert naming.scheme_for(Project()).plane_for("F-3") == "YZ"


def test_a_projects_own_convention_wins():
    p = Project()
    p.name_planes = F86
    assert naming.scheme_for(p).plane_for("F1") is None
    assert naming.scheme_for(p).plane_for("3") == "YZ"


def test_plane_from_name_uses_the_scheme_it_is_given():
    assert rx.plane_from_name("F1") == "YZ"
    assert rx.plane_from_name("F1", naming.NamingScheme(F86)) is None


# ----- it survives a save/load -------------------------------------------

def _saveload(project, tmp_path):
    path = str(tmp_path / "p.tiproj.json")
    pio.save_project(project, path)
    return pio.load_project(path)


def test_the_convention_round_trips(tmp_path):
    p = Project()
    p.pixel_width = p.pixel_height = 100
    p.name_planes = F86
    assert _saveload(p, tmp_path).name_planes == F86


def test_a_none_value_round_trips_as_none(tmp_path):
    """The None is the meaningful part - it is how the sheet says 'F is
    not a former here'. Losing it on save would restore the wrong
    convention silently."""
    p = Project()
    p.pixel_width = p.pixel_height = 100
    p.name_planes = {"": "YZ", "f": None}
    q = _saveload(p, tmp_path)
    assert q.name_planes["f"] is None
    assert naming.scheme_for(q).plane_for("F1") is None


def test_an_unchanged_project_writes_no_convention(tmp_path):
    import json
    p = Project()
    p.pixel_width = p.pixel_height = 100
    path = str(tmp_path / "p.tiproj.json")
    pio.save_project(p, path)
    with open(path) as fh:
        assert "name_planes" not in json.load(fh)


def test_a_bad_convention_is_rejected_not_ignored(tmp_path):
    import json
    path = str(tmp_path / "p.tiproj.json")
    p = Project()
    p.pixel_width = p.pixel_height = 100
    pio.save_project(p, path)
    with open(path) as fh:
        d = json.load(fh)
    d["name_planes"] = ["f", "YZ"]
    with open(path, "w") as fh:
        json.dump(d, fh)
    with pytest.raises(pio.ProjectIOError):
        pio.load_project(path)


# ----- sorting the parts list --------------------------------------------

def test_sorting_puts_the_formers_in_sequence():
    """Alphabetical order is worst exactly when it matters: scanning
    two dozen rows for the part you named twice."""
    names = ["Rudder", "F6", "6", "8", "3", "1", "10", "F4", "2",
             "T4", "T3", "B", "F1"]
    assert sorted(names, key=pio.natural_key) == [
        "1", "2", "3", "6", "8", "10", "B", "F1", "F4", "F6",
        "Rudder", "T3", "T4"]


def test_sorting_brings_duplicates_together():
    names = ["6", "F1", "6", "3"]
    out = sorted(names, key=pio.natural_key)
    assert out[1] == out[2] == "6"


# ----- the plane actually reaches the recipe -------------------------------

def _square(x, y, size=40):
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def _project_named(names):
    """A project of same-size parts, one per name."""
    from pagewright.model import Build, Contour, TracedObject
    p = Project()
    p.pixel_width = p.pixel_height = 2000
    p.calibration.mm_per_pixel = 0.5
    p.sheet_thickness_mm = 1.5875
    for i, name in enumerate(names):
        obj = TracedObject()
        obj.name = name
        obj.build = Build()
        obj.contours = [Contour(_square(100 + i * 60, 100))]
        p.objects.append(obj)
    return p


NAMES = ["1", "2", "3", "F1", "F2", "T3", "Rudder"]


def _planes(project):
    doc, skipped = rx.build_recipe(project, as_drawn=True)
    assert not skipped, skipped
    return [e["sketch"]["referencePlane"]["name"]
            for e in doc["timeline"] if e["type"] == "Sketch"]


def test_the_default_convention_stands_the_longerons_on_end():
    """What the bug looked like: F1/F2 are longerons on this plan, and
    the usual convention builds them as formers."""
    assert _planes(_project_named(NAMES)) == [
        "XY", "XY", "XY", "YZ", "YZ", "XY", "XY"]


def test_this_sheets_convention_stands_the_formers_up_instead():
    p = _project_named(NAMES)
    p.name_planes = F86
    assert _planes(p) == [
        "YZ", "YZ", "YZ", "XY", "XY", "XY", "XY"]


def test_a_parts_own_plane_still_wins_over_the_convention():
    # The convention only fills in what was never set; a part placed by
    # hand must stay where it was put.
    p = _project_named(["1"])
    p.name_planes = F86
    p.objects[0].build.plane = "XZ"
    assert _planes(p) == ["XZ"]


# ----- an all-digit name is not a usable CAD reference --------------------

def test_an_all_digit_part_name_is_prefixed():
    """SolidCraft 0.30.1 reads an all-digit name as a timeline index:
    `sketch.inspect {"sketch": "2"}` fails with "`2` is not a sketch"
    once a body exists. Reproduced in a 15-line script, so this is a
    real constraint, not a guess."""
    used = set()
    assert rx._safe("2", used) == "p2"
    assert rx._safe("10", used) == "p10"
    # a leading zero does not help - it is still all digits
    assert rx._safe("02", used) == "p02"


def test_a_name_with_any_non_digit_is_left_alone():
    used = set()
    assert rx._safe("F1", used) == "F1"
    assert rx._safe("Rudder", used) == "Rudder"
    assert rx._safe("Stabilizer-1", used) == "Stabilizer-1"


def test_the_prefixed_names_stay_unique():
    used = set()
    assert rx._safe("2", used) == "p2"
    assert rx._safe("2", used) == "p2_2"


def test_numbered_formers_reach_the_recipe_with_usable_names():
    # The failure this prevents is a rebuild that stops at the second
    # part, having already built the first.
    p = _project_named(["1", "2", "3", "F1"])
    p.name_planes = F86
    doc, skipped = rx.build_recipe(p, as_drawn=True)
    assert not skipped
    names = [e["name"] for e in doc["timeline"] if e["type"] == "Sketch"]
    assert names == ["p1", "p2", "p3", "F1"]
    assert not any(n.isdigit() for n in names)
