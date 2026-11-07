"""PlaneCraft's Python layer: reading parts and building airframes.

The Rust core has its own tests (`cargo test`). These cover the seam -
that PageWright's project really is readable from here, and that the
station helpers refuse to guess.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from planecraft import core, stations          # noqa: E402

CORE_BUILT = core.core_path() is not None
needs_core = pytest.mark.skipif(
    not CORE_BUILT, reason="planecraft-core not built (cargo build)")


# ----- stations ------------------------------------------------------------

def test_frames_at_puts_each_part_at_its_own_station():
    f = stations.frames_at(["1", "2"], [0.0, 25.4])
    assert [x["name"] for x in f] == ["1", "2"]
    assert f[1]["def"] == {"kind": "offset", "base": "YZ",
                           "offset_mm": 25.4}


def test_a_missing_station_is_refused_not_guessed():
    """A frame with a guessed station is worse than no frame: it
    builds a fuselage that looks right and is not."""
    with pytest.raises(ValueError) as e:
        stations.frames_at(["1", "2", "3"], [0.0, 25.4])
    assert "one station per part" in str(e.value)


def test_frames_every_spaces_them_evenly():
    f = stations.frames_every(["1", "2", "3"], 25.0)
    assert [x["def"]["offset_mm"] for x in f] == [0.0, 25.0, 50.0]


def test_frames_every_can_start_somewhere_other_than_zero():
    f = stations.frames_every(["a", "b"], 10.0, start_mm=5.0)
    assert [x["def"]["offset_mm"] for x in f] == [5.0, 15.0]


def test_a_prefix_keeps_frame_names_clear_of_part_names():
    # Parts are called "1".."10"; frames named the same would be
    # unreadable next to a plan.
    f = stations.frames_every(["1", "2"], 25.0, prefix="Former-")
    assert [x["name"] for x in f] == ["Former-1", "Former-2"]


def test_an_unknown_base_plane_is_refused():
    with pytest.raises(ValueError):
        stations.frames_at(["1"], [0.0], base="ZZ")


def test_ribs_can_go_on_their_own_base_plane():
    f = stations.frames_at(["R1"], [50.8], base="XZ")
    assert f[0]["def"]["base"] == "XZ"


def test_place_omits_what_was_never_set():
    p = stations.place({"1": "Former-1"})
    assert p == [{"part": "1", "frame": "Former-1"}]


def test_place_records_rotation_and_mirroring():
    p = stations.place({"R1": "Rib-1"}, rotate_deg={"R1": 90},
                       mirrored=["R1"])
    assert p[0]["rotate_deg"] == 90.0
    assert p[0]["mirrored"] is True


# ----- the Rust core, over the subprocess seam -----------------------------

@needs_core
def test_a_clean_airframe_checks_out():
    air = {
        "frames": stations.frames_every(["1", "2"], 25.4, prefix="F-"),
        "placements": [{"part": "1", "frame": "F-1"},
                       {"part": "2", "frame": "F-2"}],
    }
    assert core.check(air)["ok"] is True
    assert len(core.plan(air)) == 2


@needs_core
def test_a_part_on_a_missing_frame_is_named_and_left_out():
    air = {
        "frames": stations.frames_every(["1"], 25.4, prefix="F-"),
        "placements": [{"part": "1", "frame": "F-1"},
                       {"part": "R1", "frame": "Rib-Sta-1"}],
    }
    report = core.check(air)
    assert report["ok"] is False
    assert any("Rib-Sta-1" in m for m in report["problems"])
    assert [p["part"] for p in core.plan(air)] == ["1"]


@needs_core
def test_a_reference_frame_plans_as_not_buildable():
    """PlaneCraft cannot build an angled plane, but naming one must
    still work - that is what keeps an unforeseen construction from
    being a blocker."""
    air = {
        "frames": [{"name": "FirewallAngled"}],
        "placements": [{"part": "B", "frame": "FirewallAngled"}],
    }
    assert core.check(air)["ok"] is True
    item = core.plan(air)[0]
    assert item["buildable"] is False
    assert item["base"] is None


@needs_core
def test_the_station_reaches_the_plan():
    air = {
        "frames": stations.frames_at(["3"], [76.2], prefix="F-"),
        "placements": [{"part": "3", "frame": "F-3"}],
    }
    assert core.plan(air)[0]["offset_mm"] == 76.2


# ----- reading a PageWright project ----------------------------------------

def _pagewright_available():
    try:
        from planecraft import parts
        parts._ensure_pagewright()
        import pagewright.core.project_io  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pagewright_available(),
                    reason="PageWright not importable")
def test_a_traced_project_reads_back_with_its_convention(tmp_path):
    from planecraft import parts
    from pagewright.core import project_io
    from pagewright.model import Build, Contour, Project, TracedObject

    p = Project()
    p.pixel_width = p.pixel_height = 400
    p.calibration.mm_per_pixel = 0.5
    p.sheet_thickness_mm = 1.5875
    p.name_planes = {"": "YZ", "r": "XZ", "f": None}
    for name in ("1", "R1", "F1"):
        o = TracedObject(name=name, build=Build())
        o.contours.append(Contour(points=[(0, 0), (10, 0), (10, 10)]))
        p.objects.append(o)
    path = str(tmp_path / "p.tiproj.json")
    project_io.save_project(p, path)

    q = parts.load(path)
    assert parts.part_names(q) == ["1", "R1", "F1"]
    # the plan's own convention, read by PageWright - not re-guessed
    assert parts.implied_planes(q) == {"1": "YZ", "R1": "XZ", "F1": None}
    rows = {r["part"]: r for r in parts.summary(q)}
    assert rows["1"]["thickness_mm"] == 1.5875


# ----- snapping CAD geometry to stock size ---------------------------------

def _pw():
    try:
        from planecraft import parts
        parts._ensure_pagewright()
        import pagewright.core.notches  # noqa: F401
        return True
    except Exception:
        return False


needs_pagewright = pytest.mark.skipif(
    not _pw(), reason="PageWright not importable")


@needs_pagewright
def test_a_longeron_drawn_in_cad_snaps_to_stock():
    """The case this exists for: someone adds a custom longeron while
    working the 3D model and wants it true to 1/16 in stock."""
    from planecraft import stock
    pts = [(0.0, 0.0), (60.0, 0.12), (60.0, 1.70), (0.0, 1.72)]
    out, report = stock.snap_mm(pts, stock="1/16")
    height = abs(out[3][1] - out[0][1])
    assert height == pytest.approx(25.4 / 16.0, abs=1e-6)
    assert report.squared == 4


@needs_pagewright
def test_other_stock_sizes_are_named_not_computed_by_the_caller():
    from planecraft import stock
    pts = [(0.0, 0.0), (40.0, 0.0), (40.0, 3.30), (0.0, 3.30)]
    out, _r = stock.snap_mm(pts, stock="1/8")
    assert abs(out[3][1] - out[0][1]) == pytest.approx(25.4 / 8.0,
                                                       abs=1e-6)


@needs_pagewright
def test_an_edge_too_far_from_a_multiple_is_left_as_drawn():
    from planecraft import stock
    pts = [(0.0, 0.0), (40.0, 0.0), (40.0, 3.30), (0.0, 3.30)]
    out, _r = stock.snap_mm(pts, stock="3/32")
    assert abs(out[3][1] - out[0][1]) == pytest.approx(3.30, abs=1e-6)


def test_an_unknown_stock_name_is_refused():
    from planecraft import stock
    with pytest.raises(ValueError):
        stock.stock_mm("1/7")


def test_stock_names_are_the_ones_a_plan_uses():
    from planecraft import stock
    assert stock.stock_mm("1/16") == pytest.approx(25.4 / 16.0)
    assert stock.stock_mm('1/8"') == pytest.approx(25.4 / 8.0)


# ----- one traced part, many bodies ----------------------------------------

@needs_core
def test_one_traced_rib_can_be_built_on_both_wings():
    """The plan draws ONE rib B and says BUILD BOTH WINGS OVER SHADED
    LAYOUT. Rejecting a part placed twice would reject nearly every
    part of a symmetric structure."""
    air = {
        "frames": [
            {"name": "Rib-B-R",
             "def": {"kind": "offset", "base": "XZ", "offset_mm": -21.0}},
            {"name": "Rib-B-L",
             "def": {"kind": "offset", "base": "XZ", "offset_mm": 21.0}},
        ],
        "placements": [
            {"part": "B", "frame": "Rib-B-R"},
            {"part": "B", "frame": "Rib-B-L", "mirrored": True},
        ],
    }
    assert core.check(air)["ok"] is True
    built = core.plan(air)
    assert len(built) == 2
    assert [b["offset_mm"] for b in built] == [-21.0, 21.0]
    assert [b["mirrored"] for b in built] == [False, True]


@needs_core
def test_the_same_part_on_the_same_frame_is_still_an_error():
    # Two bodies in one place is a mistake, not a mirror.
    air = {
        "frames": [{"name": "F-1",
                    "def": {"kind": "offset", "base": "YZ",
                            "offset_mm": 0.0}}],
        "placements": [{"part": "1", "frame": "F-1"},
                       {"part": "1", "frame": "F-1"}],
    }
    report = core.check(air)
    assert report["ok"] is False
    assert "twice" in report["problems"][0]


def test_place_cannot_express_one_part_on_two_frames():
    """A known limit, pinned so it is not mistaken for a bug: the
    `place` helper takes {part: frame}, so a part appears once. The
    mirrored-rib case builds its placement list directly, and a
    helper for it can come when there is a caller that needs one."""
    p = stations.place({"B": "Rib-B-R"})
    assert len(p) == 1


# ----- circled plan labels -------------------------------------------------

def test_a_circled_number_becomes_a_typeable_name():
    """The plan labels formers with circled numbers. Nobody should
    have to type one - and on Windows the console codepage cannot even
    PRINT U+24B6, so a circled name would break the CLI and the logs."""
    from planecraft import plan_names
    assert plan_names.normalise("\u2460") == "1"      # circled 1
    assert plan_names.normalise("\u2469") == "10"     # circled 10
    assert plan_names.normalise("\u24b6") == "A"      # circled A
    assert plan_names.normalise("\u24d0") == "a"


def test_an_ordinary_name_is_left_exactly_alone():
    # A name is the user's; normalising must not tidy it.
    from planecraft import plan_names
    for name in ["R1", "Former-3", "F6", "Stabilizer-2", "B"]:
        assert plan_names.normalise(name) == name


def test_surrounding_space_goes_but_inner_space_stays():
    from planecraft import plan_names
    assert plan_names.normalise("  Rib B  ") == "Rib B"


def test_a_decorated_label_is_detectable():
    from planecraft import plan_names
    assert plan_names.is_decorated("\u24b6") is True
    assert plan_names.is_decorated("A") is False
    assert plan_names.is_decorated(None) is False


def test_the_label_is_kept_beside_the_name():
    """Matching a part back to the drawing is the reason the circled
    form is worth keeping at all - as a label, never as the key."""
    from planecraft import plan_names
    label, name = plan_names.label_and_name("\u2460")
    assert label == "\u2460"
    assert name == "1"
    # equal for an ordinary name, which is the common case
    assert plan_names.label_and_name("R1") == ("R1", "R1")


def test_a_normalised_name_matches_what_pagewright_already_has():
    """The join that makes this worth doing: the user typed "1" in
    PageWright, the plan draws a circled 1, and they must be the same
    part."""
    from planecraft import plan_names
    assert plan_names.normalise("\u2460") == "1"
    assert plan_names.normalise("\u24b7") == "B"
