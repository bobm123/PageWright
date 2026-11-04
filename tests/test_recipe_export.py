"""Tests for core.recipe_export -- pure Python (no Qt/cv2).

The contract these protect is that a PageWright recipe is rebuildable by
the CadRoundTrip importer. That was verified by running the real
solidcraft_import.py over this writer's output (PASS, 8/8 items, body
volumes matching the traced areas to 0.01%). These tests keep the shape
that importer reads.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import recipe_export as rx        # noqa: E402
from pagewright.model import (Build, Contour, Project,  # noqa: E402
                              TracedObject)


def _project(builds=None, sheet_mm=None, size=(100, 80)):
    p = Project()
    p.pixel_width, p.pixel_height = 400, 300
    p.calibration.mm_per_pixel = 0.5
    p.sheet_thickness_mm = sheet_mm
    w, h = size
    for i, build in enumerate(builds or [Build()]):
        o = TracedObject(name="Part %d" % (i + 1), build=build)
        o.contours.append(Contour(points=[(10, 10), (10 + w, 10),
                                          (10 + w, 10 + h), (10, 10 + h)]))
        p.objects.append(o)
    return p


# ----- geometry -----------------------------------------------------------

def test_area_and_centroid_of_a_square():
    a, cx, cy = rx.polygon_area_centroid([(0, 0), (10, 0), (10, 10),
                                          (0, 10)])
    assert a == pytest.approx(100.0)
    assert (cx, cy) == (pytest.approx(5.0), pytest.approx(5.0))


def test_centroid_is_the_area_centroid_not_the_vertex_mean():
    """The importer matches a profile on this, so the vertex mean -
    which an L-shape's extra vertices skew - would miss."""
    pts = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)]
    a, cx, cy = rx.polygon_area_centroid(pts)
    assert a == pytest.approx(500.0)
    assert (cx, cy) == (pytest.approx(11.0), pytest.approx(11.0))


def test_winding_does_not_flip_the_area():
    cw = [(0, 0), (0, 10), (10, 10), (10, 0)]
    assert rx.polygon_area_centroid(cw)[0] == pytest.approx(100.0)


# ----- document shape -----------------------------------------------------

def test_each_part_becomes_a_sketch_and_an_extrude():
    doc, skipped = rx.build_recipe(_project([Build(thickness_mm=1.6),
                                             Build(thickness_mm=1.6)]))
    assert skipped == []
    assert [t["type"] for t in doc["timeline"]] == [
        "Sketch", "ExtrudeFeature", "Sketch", "ExtrudeFeature"]
    assert doc["units"]["internal_length"] == "cm"


def test_lengths_are_written_in_centimetres():
    """A recipe is cm and PageWright is mm. A missing factor of ten
    here is a part ten times too big."""
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)],
                                      size=(100, 80)))
    # 100 px at 0.5 mm/px = 50 mm = 5 cm wide
    xs = [p["xyz"][0] for p in doc["timeline"][0]["sketch"]["points"]]
    assert max(xs) - min(xs) == pytest.approx(5.0)


def test_the_extrude_distance_is_the_thickness():
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    ext = doc["timeline"][1]["extrude"]
    assert ext["extentOne"]["distance"]["value"] == pytest.approx(0.16)
    assert ext["extentOne"]["distance"]["expression"] == "1.6 mm"


def test_start_extent_is_a_bare_string():
    """The importer compares startExtent against a string; a dict there
    made it report 'unsupported' and stop at the first extrude."""
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    assert doc["timeline"][1]["extrude"]["startExtent"] == \
        "ProfilePlaneStartDefinition"


def test_the_profile_carries_area_and_centroid():
    """Our tokens are not Fusion's, so the importer falls back to
    matching a profile by area and centroid. Both are in cm."""
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)],
                                      size=(100, 80)))
    prof = doc["timeline"][1]["extrude"]["profile"]
    assert prof["area"] == pytest.approx(5.0 * 4.0)        # cm^2
    assert len(prof["centroid"]) == 3
    assert prof["sketch"] == doc["timeline"][0]["name"]


def test_a_line_is_a_degree_one_nurbs():
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    c = doc["timeline"][0]["sketch"]["curves"][0]
    assert c["type"] == "SketchLine"
    assert c["nurbs"]["degree"] == 1
    assert len(c["nurbs"]["controlPoints"]) == 2


def test_a_square_closes_into_four_lines():
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    assert len(doc["timeline"][0]["sketch"]["curves"]) == 4


# ----- placement ----------------------------------------------------------

@pytest.mark.parametrize("plane,flat_axis", [("XY", 2), ("YZ", 0),
                                             ("XZ", 1)])
def test_the_plane_decides_which_axis_is_flat(plane, flat_axis):
    doc, _ = rx.build_recipe(
        _project([Build(thickness_mm=1.6, plane=plane)]))
    pts = doc["timeline"][0]["sketch"]["points"]
    assert {round(p["xyz"][flat_axis], 9) for p in pts} == {0.0}


def test_the_station_offsets_along_the_normal():
    doc, _ = rx.build_recipe(
        _project([Build(thickness_mm=1.6, plane="YZ", station_mm=40.0)]))
    pts = doc["timeline"][0]["sketch"]["points"]
    assert {round(p["xyz"][0], 9) for p in pts} == {4.0}     # 40 mm = 4 cm


def test_parts_are_centred_on_their_own_centroid():
    """A plan sheet is nested, so where a part was drawn is only where
    there was room on the paper. Placement comes from Build."""
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    xs = [p["xyz"][0] for p in doc["timeline"][0]["sketch"]["points"]]
    assert sum(xs) / len(xs) == pytest.approx(0.0, abs=1e-9)


def test_rotation_turns_the_part_in_its_plane():
    doc, _ = rx.build_recipe(
        _project([Build(thickness_mm=1.6, rotate_deg=90.0)],
                 size=(100, 40)))
    pts = doc["timeline"][0]["sketch"]["points"]
    xs = [p["xyz"][0] for p in pts]
    ys = [p["xyz"][1] for p in pts]
    # 50 x 20 mm turned a quarter turn is 20 wide and 50 tall
    assert max(xs) - min(xs) == pytest.approx(2.0)
    assert max(ys) - min(ys) == pytest.approx(5.0)


# ----- thickness ----------------------------------------------------------

def test_the_sheet_stock_supplies_a_missing_thickness():
    doc, skipped = rx.build_recipe(_project([Build()], sheet_mm=1.5875))
    assert skipped == []
    assert doc["timeline"][1]["extrude"]["extentOne"]["distance"]["value"] \
        == pytest.approx(0.15875)


def test_a_part_overrides_the_sheet_stock():
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=3.0)],
                                      sheet_mm=1.5875))
    assert doc["timeline"][1]["extrude"]["extentOne"]["distance"]["value"] \
        == pytest.approx(0.3)


def test_a_part_with_no_thickness_is_reported_not_guessed():
    """Guessing a thickness wastes material; reporting it does not."""
    doc, skipped = rx.build_recipe(
        _project([Build(thickness_mm=1.6), Build()]))
    assert len(doc["timeline"]) == 2                  # only the first
    assert skipped[0][0] == "Part 2" and "thickness" in skipped[0][1]


def test_no_buildable_part_is_an_error_naming_why():
    with pytest.raises(rx.RecipeError) as exc:
        rx.build_recipe(_project([Build()]))
    assert "thickness" in str(exc.value)


# ----- guards -------------------------------------------------------------

def test_an_uncalibrated_project_is_refused():
    p = _project([Build(thickness_mm=1.6)])
    p.calibration.mm_per_pixel = None
    with pytest.raises(rx.RecipeError) as exc:
        rx.build_recipe(p)
    assert "scale" in str(exc.value)


def test_an_empty_project_is_refused():
    p = Project()
    p.calibration.mm_per_pixel = 0.5
    with pytest.raises(rx.RecipeError):
        rx.build_recipe(p)


def test_duplicate_names_stay_distinct():
    """Names are reference keys, so a collision would point two
    extrudes at one sketch."""
    p = _project([Build(thickness_mm=1.6), Build(thickness_mm=1.6)])
    for o in p.objects:
        o.name = "Former 3"
    doc, _ = rx.build_recipe(p)
    names = [t["name"] for t in doc["timeline"] if t["type"] == "Sketch"]
    assert names == ["Former_3", "Former_3_2"]
    for i in (1, 3):
        ext = doc["timeline"][i]["extrude"]
        assert ext["profile"]["sketch"] == doc["timeline"][i - 1]["name"]


def test_write_recipe_makes_the_folder_and_valid_json(tmp_path):
    out = str(tmp_path / "deep" / "recipe.json")
    path, parts, skipped, doc_out = rx.write_recipe(
        _project([Build(thickness_mm=1.6)]), out, design="Sabre")
    assert (path, parts, skipped) == (out, 1, [])
    assert doc_out["design"] == "Sabre"     # returned, not rebuilt
    with open(out, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["design"] == "Sabre"
    assert doc["archiver"]["version"] == "1.3.0"


# ----- plane from the plan's own part numbers ----------------------------

@pytest.mark.parametrize("name,plane", [
    ("F-3", "YZ"), ("F3", "YZ"), ("f 3", "YZ"), ("former 2", "YZ"),
    ("bulkhead 1", "YZ"),
    ("R-2", "XZ"), ("R2", "XZ"), ("rib 4", "XZ"), ("W1", "XZ"),
])
def test_a_part_number_implies_a_plane(name, plane):
    """Plans number parts by what they are, and those names are typed
    in by hand while annotating - so the convention is free
    information about which way a part stands."""
    assert rx.plane_from_name(name) == plane


@pytest.mark.parametrize("name", ["Rudder", "Fin", "Stabilizer",
                                  "Part 1", "F", "3", "Wing", "FS-3",
                                  "", None])
def test_a_name_that_is_not_a_part_number_implies_nothing(name):
    """It wants a letter then a number. Guessing from 'Rudder' or
    'Fin' would place a part wrongly and quietly."""
    assert rx.plane_from_name(name) is None


def test_the_name_only_fills_in_an_unset_plane():
    p = _project([Build(thickness_mm=1.6, plane="XY")])
    p.objects[0].name = "F-3"
    doc, _ = rx.build_recipe(p)
    assert doc["timeline"][0]["sketch"]["referencePlane"]["name"] == "XY"


def test_the_name_beats_the_default():
    p = _project([Build(thickness_mm=1.6)])
    p.objects[0].name = "R-2"
    doc, _ = rx.build_recipe(p, default_plane="XY")
    assert doc["timeline"][0]["sketch"]["referencePlane"]["name"] == "XZ"


def test_an_unplaced_part_still_exports():
    """Partial output beats refusing: a sheet where only some parts are
    placed should still build the ones that are."""
    p = _project([Build(thickness_mm=1.6), Build(thickness_mm=1.6)])
    p.objects[0].name, p.objects[1].name = "F-1", "Rudder"
    doc, skipped = rx.build_recipe(p)
    assert skipped == []
    planes = [t["sketch"]["referencePlane"]["name"]
              for t in doc["timeline"] if t["type"] == "Sketch"]
    assert planes == ["YZ", "XY"]


# ----- major assemblies --------------------------------------------------

def _grouped():
    p = _project([Build(thickness_mm=1.6, assembly="Fuselage"),
                  Build(thickness_mm=1.6, assembly="Fuselage"),
                  Build(thickness_mm=1.6, assembly="LandingGear")])
    return p


def test_parts_are_grouped_by_assembly():
    doc, _ = rx.build_recipe(_grouped())
    groups = doc["_pagewright"]["assemblies"]
    assert groups == {"Fuselage": ["Body1", "Body2"],
                      "LandingGear": ["Body3"]}


def test_groups_name_bodies_not_sketches():
    """component.move_bodies takes BODY names, and the importer calls
    them Body1, Body2... - not after our sketches."""
    doc, _ = rx.build_recipe(_grouped())
    for names in doc["_pagewright"]["assemblies"].values():
        assert all(n.startswith("Body") for n in names)


def test_the_assembly_script_makes_siblings_under_root():
    """component.create defaults to the ACTIVE component and creating
    one activates it, so without an explicit parent the second
    assembly nests inside the first."""
    doc, _ = rx.build_recipe(_grouped())
    script = rx.assembly_script(doc)
    creates = [c for c in script["commands"]
               if c["command"] == "component.create"]
    assert len(creates) == 2
    assert all(c["params"]["parent"] == "root" for c in creates)


def test_assemblies_are_ungrounded_so_they_can_be_swapped():
    """The point: replacing landing gear with floats is deleting one
    component and adding another."""
    doc, _ = rx.build_recipe(_grouped())
    for c in rx.assembly_script(doc)["commands"]:
        if c["command"] == "component.create":
            assert c["params"]["grounded"] is False


def test_every_component_gets_its_bodies_moved():
    doc, _ = rx.build_recipe(_grouped())
    cmds = rx.assembly_script(doc)["commands"]
    moves = {c["params"]["component"]: c["params"]["bodies"]
             for c in cmds if c["command"] == "component.move_bodies"}
    assert moves == {"Fuselage": ["Body1", "Body2"],
                     "LandingGear": ["Body3"]}


def test_no_assembly_means_no_script():
    """Most jobs are not assemblies; they should not gain an empty
    component tree."""
    doc, _ = rx.build_recipe(_project([Build(thickness_mm=1.6)]))
    assert doc["_pagewright"]["assemblies"] == {}
    assert rx.assembly_script(doc)["commands"] == []


def test_an_unplaced_part_keeps_its_assembly():
    """'Left flat' means lying in the plan plane as drawn, which is the
    right state for an assembly nobody has positioned yet - it still
    belongs to its group."""
    p = _project([Build(thickness_mm=1.6, assembly="Wing")])
    doc, _ = rx.build_recipe(p)
    assert doc["timeline"][0]["sketch"]["referencePlane"]["name"] == "XY"
    assert doc["_pagewright"]["assemblies"] == {"Wing": ["Body1"]}
