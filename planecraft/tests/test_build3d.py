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
    it, the same way it dropped rotate_deg."""
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
    assert sy0 == pytest.approx(y0 - 17.0)
    assert sy1 == pytest.approx(y1 - 17.0)


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
