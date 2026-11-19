"""Turning an airframe into a 3D model.

Covers the two things that silently build the wrong aeroplane: a
station that does not reach the model, and a lamination whose spacing
is guessed rather than derived.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from planecraft import core                     # noqa: E402

CORE_BUILT = core.core_path() is not None
needs_core = pytest.mark.skipif(
    not CORE_BUILT, reason="planecraft-core not built (cargo build)")


def _pagewright():
    try:
        from planecraft import parts
        parts._ensure_pagewright()
        import pagewright.core.recipe_export     # noqa: F401
        return True
    except Exception:
        return False


needs_pw = pytest.mark.skipif(not _pagewright(),
                              reason="PageWright not importable")


def _project():
    from pagewright.model import Build, Contour, Project, TracedObject
    p = Project()
    p.pixel_width = p.pixel_height = 400
    p.calibration.mm_per_pixel = 1.0
    p.sheet_thickness_mm = 1.6
    for name in ("1", "2"):
        o = TracedObject(name=name, build=Build())
        o.contours.append(Contour(points=[(0, 0), (20, 0), (20, 20),
                                          (0, 20)]))
        p.objects.append(o)
    return p


def _frame(name, mm, base="YZ"):
    return {"name": name,
            "def": {"kind": "offset", "base": base, "offset_mm": mm}}


# ----- laminations ---------------------------------------------------------

@needs_core
def test_a_lamination_sits_one_thickness_from_its_base_part():
    """The F-86 nose: two 1/8 in formers glued face to face. Their
    centres are one thickness apart - derived, not measured off a
    crowded drawing where the pair reads as one thick line."""
    air = {
        "frames": [_frame("F-1", 6.93)],
        "placements": [
            {"part": "1", "frame": "F-1"},
            {"part": "2", "frame": "F-1",
             "laminate": {"on": "1", "face": "positive"}},
        ],
        "thickness_mm": {"1": 3.175, "2": 3.175},
    }
    assert core.check(air)["ok"] is True
    got = {p["part"]: p["offset_mm"] for p in core.plan(air)}
    assert got["1"] == pytest.approx(6.93)
    assert got["2"] == pytest.approx(6.93 + 3.175)


@needs_core
def test_the_face_decides_which_way_a_stack_grows():
    def air(face):
        return {
            "frames": [_frame("F", 10.0)],
            "placements": [
                {"part": "a", "frame": "F"},
                {"part": "b", "frame": "F",
                 "laminate": {"on": "a", "face": face}},
            ],
            "thickness_mm": {"a": 2.0, "b": 2.0},
        }
    fwd = {p["part"]: p["offset_mm"] for p in core.plan(air("positive"))}
    aft = {p["part"]: p["offset_mm"] for p in core.plan(air("negative"))}
    assert fwd["b"] == pytest.approx(12.0)
    assert aft["b"] == pytest.approx(8.0)


@needs_core
def test_gluing_to_a_part_that_is_not_placed_is_reported():
    air = {
        "frames": [_frame("F", 0.0)],
        "placements": [{"part": "a", "frame": "F",
                        "laminate": {"on": "nope", "face": "positive"}}],
    }
    report = core.check(air)
    assert report["ok"] is False
    assert "not placed" in report["problems"][0]


@needs_core
def test_a_plan_says_what_each_part_is_laminated_on():
    air = {
        "frames": [_frame("F", 0.0)],
        "placements": [
            {"part": "a", "frame": "F"},
            {"part": "b", "frame": "F",
             "laminate": {"on": "a", "face": "positive"}},
        ],
        "thickness_mm": {"a": 1.0, "b": 1.0},
    }
    got = {p["part"]: p.get("laminated_on") for p in core.plan(air)}
    assert got == {"a": None, "b": "a"}


# ----- applying an airframe ------------------------------------------------

@needs_pw
@needs_core
def test_applying_an_airframe_sets_the_plane_and_station():
    from planecraft import build3d
    air = {"frames": [_frame("F-1", 7.0), _frame("F-2", 40.0)],
           "placements": [{"part": "1", "frame": "F-1"},
                          {"part": "2", "frame": "F-2"}]}
    out = build3d.apply_airframe(_project(), air)
    got = {o.name: (o.build.plane, o.build.station_mm) for o in out.objects}
    assert got == {"1": ("YZ", 7.0), "2": ("YZ", 40.0)}


@needs_pw
@needs_core
def test_applying_an_airframe_leaves_the_users_project_alone():
    """Placement is an opinion about the traced file, not part of it.
    Writing planes back would make a tracing tool's file depend on an
    assembly decision taken elsewhere."""
    from planecraft import build3d
    p = _project()
    air = {"frames": [_frame("F-1", 7.0)],
           "placements": [{"part": "1", "frame": "F-1"}]}
    build3d.apply_airframe(p, air)
    assert all(o.build.plane is None for o in p.objects)


@needs_pw
@needs_core
def test_only_placed_parts_reach_the_model_by_default():
    from planecraft import build3d
    air = {"frames": [_frame("F-1", 7.0)],
           "placements": [{"part": "1", "frame": "F-1"}]}
    assert [o.name for o in
            build3d.apply_airframe(_project(), air).objects] == ["1"]
    both = build3d.apply_airframe(_project(), air, only_placed=False)
    assert sorted(o.name for o in both.objects) == ["1", "2"]


@needs_pw
@needs_core
def test_a_part_built_twice_gets_one_object_per_placement():
    from planecraft import build3d
    air = {"frames": [_frame("L", -20.0, "XZ"), _frame("R", 20.0, "XZ")],
           "placements": [{"part": "1", "frame": "L"},
                          {"part": "1", "frame": "R", "mirrored": True}]}
    names = sorted(o.name for o in
                   build3d.apply_airframe(_project(), air).objects)
    assert names == ["1@L", "1@R"]


@needs_pw
@needs_core
def test_placing_an_untraced_part_is_refused_by_name():
    from planecraft import build3d
    air = {"frames": [_frame("F", 0.0)],
           "placements": [{"part": "99", "frame": "F"}]}
    with pytest.raises(build3d.BuildError) as e:
        build3d.apply_airframe(_project(), air)
    assert "99" in str(e.value)


@needs_pw
@needs_core
def test_a_reference_frame_cannot_be_built_into_a_recipe():
    """A recipe cannot create a plane it only has a name for, so this
    says so rather than quietly building the part at the origin."""
    from planecraft import build3d
    air = {"frames": [{"name": "Firewall"}],
           "placements": [{"part": "1", "frame": "Firewall"}]}
    with pytest.raises(build3d.BuildError) as e:
        build3d.apply_airframe(_project(), air)
    assert "Firewall" in str(e.value)


# ----- the SolidCraft script -----------------------------------------------

@needs_pw
@needs_core
def test_the_script_makes_one_offset_plane_per_station():
    """The recipe route loses this. solidcraft_import's sketch_plane
    returns the plane NAME and never reads its offset, so every
    former lands at zero: a 10-former recipe with offsets 0.693 to
    30.803 cm rebuilt as ten bodies all at x = 0. PlaneCraft emits
    the construction planes itself."""
    from planecraft import solidcraft
    air = {"frames": [_frame("F-1", 7.0), _frame("F-2", 40.0)],
           "placements": [{"part": "1", "frame": "F-1"},
                          {"part": "2", "frame": "F-2"}]}
    cmds = solidcraft.commands(_project(), air)
    planes = [c for c in cmds if c["command"] == "construct.plane.offset"]
    assert len(planes) == 2
    assert {c["params"]["offset"] for c in planes} == {"7 mm", "40 mm"}


@needs_pw
@needs_core
def test_the_script_never_names_a_sketch_with_only_digits():
    # SolidCraft reads an all-digit name as a timeline index, so "2"
    # is not a sketch once a body exists.
    from planecraft import solidcraft
    air = {"frames": [_frame("F-1", 7.0)],
           "placements": [{"part": "1", "frame": "F-1"}]}
    names = [c["params"]["name"]
             for c in solidcraft.commands(_project(), air)
             if c["command"] == "sketch.create"]
    assert names == ["p1"]


@needs_pw
@needs_core
def test_a_station_of_zero_uses_the_base_plane_itself():
    from planecraft import solidcraft
    air = {"frames": [_frame("F", 0.0)],
           "placements": [{"part": "1", "frame": "F"}]}
    cmds = solidcraft.commands(_project(), air)
    assert not [c for c in cmds
                if c["command"] == "construct.plane.offset"]
    sk = next(c for c in cmds if c["command"] == "sketch.create")
    assert sk["params"]["plane"] == "YZ"


@needs_pw
@needs_core
def test_two_parts_at_one_station_share_a_plane():
    from planecraft import solidcraft
    air = {"frames": [_frame("F", 12.0)],
           "placements": [{"part": "1", "frame": "F"},
                          {"part": "2", "frame": "F"}]}
    cmds = solidcraft.commands(_project(), air)
    assert len([c for c in cmds
                if c["command"] == "construct.plane.offset"]) == 1


@needs_pw
@needs_core
def test_the_extrude_uses_the_parts_own_stock():
    from planecraft import solidcraft
    p = _project()
    p.objects[0].build.thickness_mm = 3.175
    air = {"frames": [_frame("F", 0.0)],
           "placements": [{"part": "1", "frame": "F"}]}
    ex = [c for c in solidcraft.commands(p, air)
          if c["command"] == "solid.extrude"]
    assert ex[0]["params"]["distance"] == "3.175 mm"


@needs_pw
@needs_core
def test_an_uncalibrated_project_is_refused():
    from planecraft import solidcraft
    p = _project()
    p.calibration.mm_per_pixel = None
    air = {"frames": [_frame("F", 0.0)],
           "placements": [{"part": "1", "frame": "F"}]}
    with pytest.raises(solidcraft.ScriptError):
        solidcraft.commands(p, air)


# ----- rotation ------------------------------------------------------------

@needs_pw
@needs_core
def test_a_placements_rotation_reaches_the_sketch():
    """A plan sheet is nested to save balsa, so some parts are printed
    sideways: formers 5, 7 and 10 of the F-86 are. The rotation was
    being DROPPED here - solidcraft.py read `mirrored` and never
    looked at `rotate_deg` - so those three were built lying down."""
    from planecraft import solidcraft
    air = {"frames": [_frame("F", 0.0)],
           "placements": [{"part": "1", "frame": "F", "rotate_deg": 90.0}]}
    cmds = solidcraft.commands(_project(), air)
    pts = [c["params"]["points"] for c in cmds
           if c["command"] == "sketch.line"]
    xs = [p[0][0] for p in pts] + [p[1][0] for p in pts]
    ys = [p[0][1] for p in pts] + [p[1][1] for p in pts]
    # the fixture is a 20 x 20 square, so turning it must still give a
    # square - but every vertex has moved
    assert max(xs) - min(xs) == pytest.approx(20.0)
    assert max(ys) - min(ys) == pytest.approx(20.0)
    plain = solidcraft.commands(_project(), {
        "frames": [_frame("F", 0.0)],
        "placements": [{"part": "1", "frame": "F"}]})
    plain_pts = [c["params"]["points"] for c in plain
                 if c["command"] == "sketch.line"]
    assert pts != plain_pts


@needs_pw
@needs_core
def test_rotation_turns_a_non_square_part_through_ninety_degrees():
    from planecraft import solidcraft
    from pagewright.model import Contour
    p = _project()
    # a 40 wide x 10 tall part
    p.objects[0].contours = [Contour(points=[(0, 0), (40, 0), (40, 10),
                                             (0, 10)])]
    def spans(rot):
        air = {"frames": [_frame("F", 0.0)],
               "placements": [{"part": "1", "frame": "F",
                               "rotate_deg": rot}]}
        pts = [c["params"]["points"] for c in solidcraft.commands(p, air)
               if c["command"] == "sketch.line"]
        xs = [q[0][0] for q in pts]; ys = [q[0][1] for q in pts]
        return max(xs) - min(xs), max(ys) - min(ys)
    w0, h0 = spans(0)
    w90, h90 = spans(90)
    assert (w0, h0) == (pytest.approx(40.0), pytest.approx(10.0))
    assert (w90, h90) == (pytest.approx(10.0), pytest.approx(40.0))


@needs_pw
@needs_core
def test_a_hole_turns_with_its_part():
    """The hole must keep its place in the part, not stay put while
    the outline turns around it."""
    from planecraft import solidcraft
    from pagewright.model import Contour
    p = _project()
    p.objects[0].contours = [
        Contour(points=[(0, 0), (40, 0), (40, 40), (0, 40)]),
        Contour(points=[(2, 2), (10, 2), (10, 10), (2, 10)], role="hole"),
    ]
    def hole_centre(rot):
        air = {"frames": [_frame("F", 0.0)],
               "placements": [{"part": "1", "frame": "F",
                               "rotate_deg": rot}]}
        cmds = solidcraft.commands(p, air)
        pts = [c["params"]["points"] for c in cmds
               if c["command"] == "sketch.line"]
        assert len(pts) == 8          # 4 outline edges + 4 hole edges
        hole = pts[4:]                # holes follow the outline
        xs = [q[0][0] for q in hole]; ys = [q[0][1] for q in hole]
        return (sum(xs) / 4.0, sum(ys) / 4.0)

    hx0, hy0 = hole_centre(0)
    hx90, hy90 = hole_centre(90)
    # the hole sits in one corner; turning the part must carry it
    # round, not leave it where it was
    assert (hx90, hy90) != (pytest.approx(hx0), pytest.approx(hy0))
    assert hx90 == pytest.approx(-hy0)
    assert hy90 == pytest.approx(hx0)


@needs_pw
@needs_core
def test_an_in_plane_offset_reaches_the_sketch():
    """A part is centred on its own centroid, so it straddles the
    frame origin. `offset_mm` is how a keel gets carried back to
    where the fuselage actually is - and solidcraft.py was dropping
    it, the same way it dropped rotate_deg.

    The offset is checked as a DISPLACEMENT, not as a signed sketch
    coordinate: on XZ the sketch's local y runs opposite world Z, so
    asserting the raw sign here would pin down the plane's axis
    convention as a side effect - and did, until the convention was
    measured and found to be the other way."""
    from planecraft import solidcraft
    def span(off):
        air = {"frames": [_frame("F", 0.0, "XZ")],
               "placements": [{"part": "1", "frame": "F",
                               "offset_mm": off}]}
        pts = [c["params"]["points"] for c in solidcraft.commands(
            _project(), air) if c["command"] == "sketch.line"]
        xs = [q[0][0] for q in pts]; ys = [q[0][1] for q in pts]
        return min(xs), max(xs), min(ys), max(ys)
    x0, x1, y0, y1 = span([0.0, 0.0])
    sx0, sx1, sy0, sy1 = span([100.0, -17.0])
    assert sx0 == pytest.approx(x0 + 100.0)
    assert sx1 == pytest.approx(x1 + 100.0)
    # 17 mm of movement, whichever way the plane's y axis points
    assert abs(sy0 - y0) == pytest.approx(17.0)
    assert abs(sy1 - y1) == pytest.approx(17.0)
    assert (sy0 - y0) == pytest.approx(sy1 - y1)


@needs_pw
@needs_core
def test_the_offset_is_applied_after_the_rotation():
    """Otherwise the rotation would turn the offset too, and a part
    nudged along the fuselage would swing sideways instead."""
    from planecraft import solidcraft
    air = {"frames": [_frame("F", 0.0, "XZ")],
           "placements": [{"part": "1", "frame": "F",
                           "rotate_deg": 90.0,
                           "offset_mm": [50.0, 0.0]}]}
    pts = [c["params"]["points"] for c in solidcraft.commands(
        _project(), air) if c["command"] == "sketch.line"]
    xs = [q[0][0] for q in pts]
    # the 20x20 fixture turned and then moved +50 in u
    assert sum(xs) / len(xs) == pytest.approx(50.0, abs=1e-6)


# ----- the editable wing SVG -----------------------------------------------

@needs_pw
@needs_core
def test_the_wing_svg_round_trips_a_position(tmp_path):
    """Reading a rib's chordwise position off a dense 1952 plan kept
    picking the wrong feature, so the positions go out as an SVG, get
    dragged into place by hand, and come back as numbers. The round
    trip is the whole point, so it is what the test covers."""
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ"),
                      _frame("Rib-B-R", 45.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R",
                           "offset_mm": [100.0, 0.0]},
                          {"part": "2", "frame": "Rib-B-R",
                           "offset_mm": [118.0, 0.0]}]}
    out = str(tmp_path / "wing.svg")
    assert wing_svg.write_svg(p, air, out) == 2
    rows = wing_svg.compare(p, wing_svg.read_svg(out))
    # the rib is drawn rotate(-90), so its chord runs BACK from the
    # translate point: aft_mm is the minimum y, anchor minus chord.
    assert rows["1"]["aft_mm"] == pytest.approx(80.0, abs=0.01)
    assert rows["2"]["aft_mm"] == pytest.approx(98.0, abs=0.01)
    # the gap between them is what the drag changed
    assert (rows["2"]["aft_mm"] - rows["1"]["aft_mm"]
            == pytest.approx(18.0, abs=0.01))


@needs_pw
@needs_core
def test_an_edited_transform_is_what_comes_back(tmp_path):
    # Inkscape writes a drag as a change to the group's transform.
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R",
                           "offset_mm": [100.0, 0.0]}]}
    out = str(tmp_path / "wing.svg")
    wing_svg.write_svg(p, air, out)
    text = open(out).read().replace("translate(22.0000,100.0000)",
                                    "translate(22.0000,73.5000)")
    open(out, "w").write(text)
    rows = wing_svg.compare(p, wing_svg.read_svg(out))
    assert rows["1"]["aft_mm"] == pytest.approx(53.5, abs=0.01)


@needs_pw
@needs_core
def test_the_svg_is_well_formed_xml(tmp_path):
    """It failed twice while being written: a duplicated xmlns:xlink,
    then inkscape: and sodipodi: prefixes with no namespace declared.
    Either makes the file unopenable."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R"}]}
    out = str(tmp_path / "wing.svg")
    wing_svg.write_svg(p, air, out, image_path="plan.png",
                       image_size_mm=(100.0, 80.0))
    ET.parse(out)          # raises if the prefixes are unbound


@needs_pw
@needs_core
def test_only_one_side_is_written(tmp_path):
    # The two wings are mirror images; editing one is less work than
    # keeping two in step.
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ"),
                      _frame("Rib-A-L", -22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R"},
                          {"part": "1", "frame": "Rib-A-L",
                           "mirrored": True}]}
    out = str(tmp_path / "wing.svg")
    assert wing_svg.write_svg(p, air, out, side="R") == 1


@needs_pw
@needs_core
def test_an_uncalibrated_project_is_refused_here_too(tmp_path):
    from planecraft import wing_svg
    p = _project()
    p.calibration.mm_per_pixel = None
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R"}]}
    with pytest.raises(ValueError):
        wing_svg.write_svg(p, air, str(tmp_path / "w.svg"))


# ----- reading an EDITED svg back -----------------------------------------

def test_a_matrix_transform_is_resolved():
    """An editor writes matrix(...) once a part has been flipped, so
    reading only `translate` stops working the moment the user does
    the thing the file was made for."""
    from planecraft import wing_svg
    m = wing_svg.parse_transform("matrix(0,1,1,0,14.5,56.9)")
    assert wing_svg._apply(m, 0.0, 0.0) == (pytest.approx(14.5),
                                            pytest.approx(56.9))
    # (0,1,1,0) swaps x and y: a flip about the diagonal
    assert wing_svg._apply(m, 1.0, 0.0) == (pytest.approx(14.5),
                                            pytest.approx(57.9))


def test_rotate_about_a_point_is_resolved():
    from planecraft import wing_svg
    m = wing_svg.parse_transform("rotate(90,10,10)")
    # the centre is a fixed point
    assert wing_svg._apply(m, 10.0, 10.0) == (pytest.approx(10.0),
                                              pytest.approx(10.0))
    assert wing_svg._apply(m, 20.0, 10.0) == (pytest.approx(10.0),
                                              pytest.approx(20.0))


def test_a_transform_list_composes_left_to_right():
    from planecraft import wing_svg
    m = wing_svg.parse_transform("translate(10,0) scale(2)")
    assert wing_svg._apply(m, 3.0, 0.0) == (pytest.approx(16.0),
                                            pytest.approx(0.0))


@needs_pw
@needs_core
def test_a_flipped_rib_reads_back_at_its_edited_place(tmp_path):
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R",
                           "offset_mm": [100.0, 0.0]}]}
    out = str(tmp_path / "w.svg")
    wing_svg.write_svg(p, air, out)
    # stand in for a flip-and-drag in the editor
    text = open(out).read().replace(
        'transform="translate(22.0000,100.0000) rotate(-90)"',
        'transform="matrix(0,-1,-1,0,30.0,70.0)"')
    open(out, "w").write(text)
    pts = wing_svg.read_svg(out)["1"]
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    # the 20x20 fixture, mirrored and turned, still spans 20 each way
    assert max(xs) - min(xs) == pytest.approx(20.0)
    assert max(ys) - min(ys) == pytest.approx(20.0)
    assert max(xs) == pytest.approx(30.0)
    assert max(ys) == pytest.approx(70.0)


@needs_pw
@needs_core
def test_compare_separates_shape_from_placement(tmp_path):
    """A drag is PLACEMENT and belongs in the airframe; a moved vertex
    is SHAPE and belongs on the parts sheet. Telling them apart is
    what lets each go to the right file."""
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Rib-A-R", 22.0, "XZ")],
           "placements": [{"part": "1", "frame": "Rib-A-R",
                           "offset_mm": [100.0, 0.0]}]}
    out = str(tmp_path / "w.svg")
    wing_svg.write_svg(p, air, out)
    rows = wing_svg.compare(p, wing_svg.read_svg(out))
    assert rows["1"]["shape_changed"] is False
    assert rows["1"]["chord_mm"] == pytest.approx(
        rows["1"]["chord_traced_mm"], abs=0.01)


@needs_pw
@needs_core
def test_parts_filter_draws_something_other_than_ribs(tmp_path):
    """The fuselage keels share one frame, so there is no -R/-L to
    select on; `parts` names them instead."""
    from planecraft import wing_svg
    p = _project()
    air = {"frames": [_frame("Keel-centre", 0.0, "XZ")],
           "placements": [{"part": "1", "frame": "Keel-centre",
                           "offset_mm": [30.0, -10.0]},
                          {"part": "2", "frame": "Keel-centre"}]}
    out = str(tmp_path / "k.svg")
    assert wing_svg.write_svg(p, air, out, parts={"1"}, upright=True) == 1
    assert set(wing_svg.read_svg(out)) == {"1"}


@needs_pw
@needs_core
def test_upright_parts_are_not_turned_a_quarter(tmp_path):
    """A keel is already a side profile; a rib is a section seen
    edge-on. Turning the keel would lay it on its side."""
    from planecraft import wing_svg
    from pagewright.model import Contour
    p = _project()
    p.objects[0].contours = [Contour(points=[(0, 0), (40, 0),
                                             (40, 10), (0, 10)])]
    air = {"frames": [_frame("Keel-centre", 0.0, "XZ")],
           "placements": [{"part": "1", "frame": "Keel-centre",
                           "offset_mm": [0.0, 0.0]}]}
    out = str(tmp_path / "k.svg")
    wing_svg.write_svg(p, air, out, parts={"1"}, upright=True)
    pts = wing_svg.read_svg(out)["1"]
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    # 40 long, 10 deep - as traced, not turned
    assert max(xs) - min(xs) == pytest.approx(40.0)
    assert max(ys) - min(ys) == pytest.approx(10.0)


# ----- reading a placement back out of an edited SVG ----------------------
#
# The bug these protect against is SILENT: a height read with the
# wrong datum builds a part a few millimetres off, or on the wrong
# side of the centreline, and the render still looks plausible. F1
# and F2 were built 1.4 and 5.8 mm low exactly that way.

def _upright_airframe():
    """Two parts on the fuselage centre plane, one above and one
    below, so a sign error cannot pass as symmetry."""
    return {"frames": [_frame("Keel-centre", 0.0, "XZ")],
            "placements": [
                {"part": "1", "frame": "Keel-centre",
                 "offset_mm": [30.0, 12.0]},
                {"part": "2", "frame": "Keel-centre",
                 "offset_mm": [120.0, -8.0]}]}


@needs_pw
@needs_core
def test_an_untouched_svg_reads_back_the_placement_it_was_written_from(
        tmp_path):
    """The round trip has to be exact, or every edit carries an error
    that has nothing to do with the edit."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = _upright_airframe()
    out = str(tmp_path / "keels.svg")
    wing_svg.write_svg(p, air, out, parts={"1", "2"}, upright=True)

    got = wing_svg.placements_from_svg(out, upright=True)
    want = {q["part"]: q["offset_mm"] for q in air["placements"]}
    for name, row in got.items():
        a, b = row["offset_mm"]
        assert a == pytest.approx(want[name][0], abs=0.01)
        assert b == pytest.approx(want[name][1], abs=0.01)


@needs_pw
@needs_core
def test_a_shifted_drawing_still_reads_the_right_height(tmp_path):
    """Inkscape rewrote every transform and moved them 30.06 mm on
    save. The datum marker moves with the parts, so the shift cancels;
    without it the heights come back 30 mm out."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = _upright_airframe()
    out = str(tmp_path / "keels.svg")
    wing_svg.write_svg(p, air, out, parts={"1", "2"}, upright=True)

    # What Inkscape actually did: rewrite EACH part's own transform,
    # shifted by a constant, and shift the datum marker's geometry to
    # match. A shift on the shared parent would cancel by itself and
    # prove nothing, so it has to be applied per element.
    SHIFT = 30.0567
    tree = ET.parse(out)
    for g in tree.iter("{%s}g" % wing_svg.SVG_NS):
        if not (g.get("id") or "").startswith("rib-"):
            continue
        m = wing_svg.parse_transform(g.get("transform"))
        g.set("transform", "matrix(%g,%g,%g,%g,%g,%g)"
              % (m[0], m[1], m[2], m[3], m[4], m[5] + SHIFT))
    for q in tree.iter("{%s}path" % wing_svg.SVG_NS):
        if q.get("id") == "z-datum":
            q.set("transform", "translate(0,%g)" % SHIFT)
    shifted = str(tmp_path / "shifted.svg")
    tree.write(shifted)

    # the shift really is in the file, or the test is vacuous
    assert wing_svg.datum_of(ET.parse(shifted)) == pytest.approx(
        SHIFT, abs=0.01)

    got = wing_svg.placements_from_svg(shifted, upright=True)
    want = {q["part"]: q["offset_mm"] for q in air["placements"]}
    for name, row in got.items():
        assert row["offset_mm"][1] == pytest.approx(want[name][1], abs=0.01)


@needs_pw
@needs_core
def test_the_height_sign_is_not_merely_symmetric(tmp_path):
    """A part ABOVE the centreline must not read as below it. The
    first keel pass got F2 and F3 inverted, and nothing caught it."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = _upright_airframe()
    air["placements"][0]["offset_mm"] = [20.0, 15.0]    # above
    air["placements"][1]["offset_mm"] = [20.0, -15.0]   # below
    out = str(tmp_path / "signs.svg")
    wing_svg.write_svg(p, air, out, parts={"1", "2"}, upright=True)

    got = wing_svg.placements_from_svg(out, upright=True)
    heights = {n: r["offset_mm"][1] for n, r in got.items()}
    assert max(heights.values()) == pytest.approx(15.0, abs=0.01)
    assert min(heights.values()) == pytest.approx(-15.0, abs=0.01)


@needs_pw
@needs_core
def test_a_rotation_is_read_without_moving_the_part(tmp_path):
    """Rotating about a far-off point is what an editor writes. The
    position must come from the TRANSLATION: rebuilding it from the
    bounding box disagrees part to part once things are turned, by
    7.9 mm in the case that prompted this.

    The ANGLE is checked as a round trip rather than against the raw
    SVG value, because SVG y runs down and so the drawing's angle is
    the model's negated. Asserting the drawing's sign here would pin
    down that convention as a side effect, the way the in-plane
    offset test did (16.38)."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = _upright_airframe()
    out = str(tmp_path / "rot.svg")
    wing_svg.write_svg(p, air, out, parts={"1"}, upright=True)

    tree = ET.parse(out)
    for g in tree.iter("{%s}g" % wing_svg.SVG_NS):
        if (g.get("id") or "").startswith("rib-"):
            g.set("transform", "rotate(-5,110.4,-127.3)")
    turned = str(tmp_path / "turned.svg")
    tree.write(turned)

    got = wing_svg.placements_from_svg(turned, upright=True)
    row = next(iter(got.values()))
    assert abs(row["rotate_deg"]) == pytest.approx(5.0, abs=0.01)
    assert row["mirrored"] is False

    # the round trip is the thing that has to hold: an angle written
    # from a placement must read back as that same placement
    air2 = {"frames": air["frames"],
            "placements": [dict(air["placements"][0], rotate_deg=-5.0)]}
    out2 = str(tmp_path / "rt.svg")
    wing_svg.write_svg(p, air2, out2, parts={"1"}, upright=True)
    back = next(iter(wing_svg.placements_from_svg(
        out2, upright=True).values()))
    assert back["rotate_deg"] == pytest.approx(-5.0, abs=0.01)


@needs_pw
@needs_core
def test_a_mirrored_part_is_reported_as_mirrored(tmp_path):
    """A flip is a negative determinant, not a 180 degree turn, and
    the two are not interchangeable for an asymmetric part."""
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    air = _upright_airframe()
    out = str(tmp_path / "mir.svg")
    wing_svg.write_svg(p, air, out, parts={"1"}, upright=True)

    tree = ET.parse(out)
    for g in tree.iter("{%s}g" % wing_svg.SVG_NS):
        if (g.get("id") or "").startswith("rib-"):
            g.set("transform", "matrix(-1,0,0,1,50,10)")
    flipped = str(tmp_path / "flipped.svg")
    tree.write(flipped)

    row = next(iter(wing_svg.placements_from_svg(
        flipped, upright=True).values()))
    assert row["mirrored"] is True


# ----- which way a sketch's local y points ---------------------------------

@needs_pw
@needs_core
def test_a_part_above_the_centreline_is_built_above_it():
    """MEASURED in SolidCraft: a line from local y 0 to +50 on the XZ
    plane came out spanning world Z 0 to -50, so local +y is world -Z
    there. Without the negation every XZ part builds mirrored about
    the centreline - which looks plausible in a render of a roughly
    symmetric airframe, and is exactly how it survived a first pass.

    Asserted through the EMITTED POINTS rather than the flag, so the
    test still means something if the mechanism changes.
    """
    from planecraft import solidcraft
    p = _project()
    air = {"frames": [_frame("Keel-centre", 0.0, "XZ")],
           "placements": [{"part": "1", "frame": "Keel-centre",
                           "offset_mm": [0.0, 20.0]}]}
    cmds = solidcraft.commands(p, air)
    ys = [q[1] for c in cmds if c["command"] == "sketch.line"
          for q in c["params"]["points"]]
    # placed 20 mm UP, so the sketch points must come out NEGATIVE
    assert sum(ys) / len(ys) == pytest.approx(-20.0, abs=1.0)


@needs_pw
@needs_core
def test_a_yz_part_is_not_negated():
    """The formers were already right, so the fix must not move them:
    only XZ has the flipped axis."""
    from planecraft import solidcraft
    p = _project()
    air = {"frames": [_frame("Former-1", 10.0, "YZ")],
           "placements": [{"part": "1", "frame": "Former-1",
                           "offset_mm": [0.0, 20.0]}]}
    cmds = solidcraft.commands(p, air)
    ys = [q[1] for c in cmds if c["command"] == "sketch.line"
          for q in c["params"]["points"]]
    assert sum(ys) / len(ys) == pytest.approx(20.0, abs=1.0)


# ----- the reference layer -------------------------------------------------

@needs_pw
@needs_core
def test_a_reference_part_is_drawn_at_its_resolved_station(tmp_path):
    """A lamination shares its base part's FRAME but stands one
    thickness along from it. Drawing it at frame_offset_mm put the
    two nose formers on top of each other - and a reference line in
    the wrong place is worse than none, because it is there to be
    trusted."""
    import re
    import xml.etree.ElementTree as ET
    from planecraft import wing_svg
    p = _project()
    # a third part carries the drawing, so no part appears both as a
    # reference and as the thing being dragged
    from pagewright.model import Build, Contour, TracedObject
    k = TracedObject(name="K", build=Build())
    k.contours.append(Contour(points=[(0, 0), (40, 0), (40, 8), (0, 8)]))
    p.objects.append(k)
    air = {"frames": [_frame("Former-1", 10.0, "YZ"),
                      _frame("Keel", 0.0, "XZ")],
           # a lamination only steps along if its thickness is known
           "thickness_mm": {"1": 1.6, "2": 1.6},
           "placements": [
               {"part": "1", "frame": "Former-1"},
               {"part": "2", "frame": "Former-1",
                "laminate": {"on": "1", "face": "positive"}},
               {"part": "K", "frame": "Keel", "offset_mm": [20.0, 5.0]}]}
    out = str(tmp_path / "r.svg")
    wing_svg.write_svg(p, air, out, parts={"K"}, upright=True,
                       reference={"1", "2"})

    xs = {}
    for q in ET.parse(out).iter("{%s}path" % wing_svg.SVG_NS):
        gid = q.get("id") or ""
        if gid.startswith("ref-"):
            v = re.findall(r"-?[\d.]+", q.get("d"))
            xs[gid[4:]] = float(v[0])
    assert xs["1"] == pytest.approx(10.0, abs=0.01)
    # one sheet thickness further along, NOT on top of part 1
    assert xs["2"] > xs["1"] + 1.0


@needs_pw
@needs_core
def test_a_reference_part_is_drawn_as_placed_not_as_traced(tmp_path):
    """Five formers carry a rotation (16.35) and the sheet's y runs
    the other way, so a bar drawn straight from the contour describes
    the part as it sits on the PRINTWOOD. Every one came out upside
    down that way."""
    import re
    import xml.etree.ElementTree as ET
    from pagewright.model import Contour
    from planecraft import wing_svg
    p = _project()
    # an asymmetric former: deeper below its centre than above
    p.objects[0].contours = [Contour(points=[(0, 0), (20, 0),
                                             (20, 30), (0, 10)])]
    air = {"frames": [_frame("Former-1", 10.0, "YZ"),
                      _frame("Keel", 0.0, "XZ")],
           "placements": [
               {"part": "1", "frame": "Former-1", "rotate_deg": 180.0},
               {"part": "2", "frame": "Keel", "offset_mm": [20.0, 5.0]}]}
    out = str(tmp_path / "r.svg")
    wing_svg.write_svg(p, air, out, parts={"2"}, upright=True,
                       reference={"1"})

    def bar(rot):
        air["placements"][0]["rotate_deg"] = rot
        o = str(tmp_path / ("r%s.svg" % rot))
        wing_svg.write_svg(p, air, o, parts={"2"}, upright=True,
                           reference={"1"})
        for q in ET.parse(o).iter("{%s}path" % wing_svg.SVG_NS):
            if (q.get("id") or "") == "ref-1":
                v = [float(x) for x in re.findall(r"-?[\d.]+", q.get("d"))]
                return v[1], v[3]            # svg y of top, bottom
        pytest.fail("no reference bar was drawn")

    # The part is deeper on one side of its centre than the other, so
    # turning it through 180 must MOVE the bar - asserting only that
    # it stays asymmetric would pass whether or not the rotation was
    # applied at all.
    top0, bot0 = bar(0.0)
    top1, bot1 = bar(180.0)
    assert (bot0 - top0) == pytest.approx(bot1 - top1, abs=0.01)
    centre0 = (top0 + bot0) / 2.0
    centre1 = (top1 + bot1) / 2.0
    assert abs(centre1 - centre0) > 1.0
