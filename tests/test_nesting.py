"""Tests for core.nesting -- pure geometry, no NumPy or Qt needed."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import nesting as nst  # noqa: E402


def _rect(w, h, x=0.0, y=0.0):
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]


def _bounds(loops):
    xs = [p[0] for loop in loops for p in loop]
    ys = [p[1] for loop in loops for p in loop]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b):
    ax0, ay0, ax1, ay1 = _bounds(a.loops)
    bx0, by0, bx1, by1 = _bounds(b.loops)
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


# ------------------------------------------------------------- instances

def test_a_single_part_keeps_its_own_name():
    p = nst.Piece("F1", _rect(10, 10))
    assert [lbl for lbl, _l in p.instances()] == ["F1"]


def test_a_mirrored_pair_is_labelled_left_and_right():
    p = nst.Piece("F1", _rect(10, 10), count=2, mirror=True)
    assert [lbl for lbl, _l in p.instances()] == ["F1-L", "F1-R"]


def test_identical_copies_are_numbered_not_lettered():
    """Numbering says the builder need not tell them apart; L/R says
    they are different cuts and he must."""
    p = nst.Piece("Disc", _rect(10, 10), count=3)
    assert [lbl for lbl, _l in p.instances()] == ["Disc-1", "Disc-2",
                                                  "Disc-3"]


def test_the_mirrored_twin_is_actually_flipped():
    """The point of the whole feature: two identical halves give two
    left halves and no fuselage."""
    loops = [[(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (2.0, 4.0)]]
    inst = nst.Piece("F", loops, count=2, mirror=True).instances()
    a = inst[0][1][0]
    b = inst[1][1][0]
    assert a != b, "the twin is the same cut as the original"
    # A mirror preserves the bounding box but reverses the shape: the
    # short side moves from the right to the left.
    assert _bounds(inst[0][1]) == _bounds(inst[1][1])
    top_a = sorted(p[0] for p in a if p[1] == 4.0)
    top_b = sorted(p[0] for p in b if p[1] == 4.0)
    assert top_a == [2.0, 10.0]
    assert top_b == [0.0, 8.0]


def test_a_set_of_four_alternates_left_and_right():
    """Four wheel pants are two left and two right, not four left."""
    p = nst.Piece("Pant", _rect(10, 10), count=4, mirror=True)
    assert [lbl for lbl, _l in p.instances()] == ["Pant-L", "Pant-R",
                                                  "Pant-L", "Pant-R"]


def test_a_count_below_one_is_refused():
    with pytest.raises(nst.NestingError):
        nst.Piece("F1", _rect(10, 10), count=0)


# ----------------------------------------------------------------- nest

def test_parts_do_not_overlap_and_stay_on_the_sheet():
    pieces = [nst.Piece("P%d" % i, _rect(20 + i, 15 + i)) for i in range(8)]
    sheets, unplaced = nst.nest(pieces, 200.0, 150.0)
    assert unplaced == []
    for sh in sheets:
        for i, a in enumerate(sh.placements):
            x0, y0, x1, y1 = _bounds(a.loops)
            assert x0 >= -1e-9 and y0 >= -1e-9
            assert x1 <= sh.width + 1e-9 and y1 <= sh.height + 1e-9
            for b in sh.placements[i + 1:]:
                assert not _overlap(a, b), "%s overlaps %s" % (a.label,
                                                               b.label)


def test_the_gap_between_neighbours_is_respected():
    pieces = [nst.Piece("A", _rect(30, 20)), nst.Piece("B", _rect(30, 20))]
    sheets, _u = nst.nest(pieces, 200.0, 100.0, gap_mm=5.0, margin_mm=0.0)
    a, b = sheets[0].placements
    ax1 = _bounds(a.loops)[2]
    bx0 = _bounds(b.loops)[0]
    assert bx0 - ax1 == pytest.approx(5.0)


def test_the_margin_keeps_parts_off_the_ragged_edge():
    sheets, _u = nst.nest([nst.Piece("A", _rect(10, 10))],
                          100.0, 100.0, margin_mm=7.0)
    x0, y0, _x1, _y1 = _bounds(sheets[0].placements[0].loops)
    assert x0 == pytest.approx(7.0)
    assert y0 == pytest.approx(7.0)


def test_a_part_is_moved_not_reshaped():
    """Nesting translates; it must not scale, rotate or resample."""
    loops = [[(3.0, 7.0), (23.0, 7.0), (23.0, 17.0), (3.0, 17.0)]]
    sheets, _u = nst.nest([nst.Piece("A", loops)], 100.0, 100.0)
    out = sheets[0].placements[0].loops[0]
    assert len(out) == len(loops[0])
    dx = out[0][0] - loops[0][0][0]
    dy = out[0][1] - loops[0][1 - 1][1]
    for (ox, oy), (ix, iy) in zip(out, loops[0]):
        assert ox - ix == pytest.approx(dx)
        assert oy - iy == pytest.approx(dy)


def test_holes_travel_with_their_outline():
    outer = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]
    hole = [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)]
    sheets, _u = nst.nest([nst.Piece("A", [outer, hole])],
                          100.0, 100.0, margin_mm=5.0)
    got = sheets[0].placements[0].loops
    assert len(got) == 2
    # The hole must keep its offset inside the outline.
    assert got[1][0][0] - got[0][0][0] == pytest.approx(10.0)
    assert got[1][0][1] - got[0][0][1] == pytest.approx(10.0)


def test_a_new_row_starts_when_the_first_is_full():
    pieces = [nst.Piece("P%d" % i, _rect(40, 20)) for i in range(4)]
    sheets, _u = nst.nest(pieces, 100.0, 100.0, gap_mm=0.0, margin_mm=0.0)
    assert len(sheets) == 1
    ys = sorted({_bounds(p.loops)[1] for p in sheets[0].placements})
    assert len(ys) == 2, "expected two rows of two"
    assert ys[1] == pytest.approx(20.0)


def test_a_second_sheet_is_started_when_the_first_is_full():
    pieces = [nst.Piece("P%d" % i, _rect(45, 45)) for i in range(5)]
    sheets, unplaced = nst.nest(pieces, 100.0, 100.0,
                                gap_mm=0.0, margin_mm=0.0)
    assert unplaced == []
    assert len(sheets) == 2
    assert sum(len(s.placements) for s in sheets) == 5


def test_an_oversize_part_is_reported_not_silently_dropped():
    pieces = [nst.Piece("Huge", _rect(500, 10)),
              nst.Piece("Fine", _rect(10, 10))]
    sheets, unplaced = nst.nest(pieces, 100.0, 100.0)
    assert [lbl for lbl, _why in unplaced] == ["Huge"]
    assert "exceeds" in unplaced[0][1]
    placed = [p.label for s in sheets for p in s.placements]
    assert placed == ["Fine"], "the rest of the job must still be nested"


def test_an_oversize_part_is_not_quietly_rotated_to_fit():
    """Grain direction is a strength decision, not a packing one.

    A 200 x 10 part fits a 100 x 300 sheet perfectly well turned 90
    degrees. It is still reported, because turning a former across
    the grain can be the one that breaks.
    """
    sheets, unplaced = nst.nest([nst.Piece("Long", _rect(200, 10))],
                                100.0, 300.0, margin_mm=0.0)
    assert [lbl for lbl, _w in unplaced] == ["Long"]
    assert sheets == [] or sheets[0].placements == []


def test_tallest_is_packed_first_so_no_part_is_stranded():
    """With shelves, a short part placed first would set a short row
    height and the tall parts would each need a row of their own."""
    pieces = [nst.Piece("short", _rect(20, 5)),
              nst.Piece("tall", _rect(20, 40))]
    sheets, _u = nst.nest(pieces, 100.0, 100.0, gap_mm=0.0, margin_mm=0.0)
    first = sheets[0].placements[0]
    assert first.label == "tall"
    # Both land in the same row, so the short one sits at y = 0 too.
    assert _bounds(sheets[0].placements[1].loops)[1] == pytest.approx(0.0)


def test_mirrored_instances_are_nested_as_separate_cuts():
    """The twin needs its own space on the sheet, not a shared slot."""
    sheets, unplaced = nst.nest(
        [nst.Piece("F1", _rect(30, 20), count=2, mirror=True)],
        200.0, 100.0)
    assert unplaced == []
    labels = sorted(p.label for s in sheets for p in s.placements)
    assert labels == ["F1-L", "F1-R"]


def test_used_fraction_reports_the_bounding_box_coverage():
    sheets, _u = nst.nest([nst.Piece("A", _rect(50, 50))],
                          100.0, 100.0, margin_mm=0.0)
    assert sheets[0].used_fraction() == pytest.approx(0.25)


def test_an_empty_job_gives_no_sheets():
    sheets, unplaced = nst.nest([], 100.0, 100.0)
    assert sheets == []
    assert unplaced == []


def test_a_nonpositive_sheet_is_refused():
    with pytest.raises(nst.NestingError):
        nst.nest([nst.Piece("A", _rect(1, 1))], 0.0, 100.0)


def test_nesting_is_free_of_qt_cv2_and_numpy():
    """core/ stays portable: see PLAN.md sec. 2."""
    path = os.path.join(os.path.dirname(__file__), "..", "src",
                        "pagewright", "core", "nesting.py")
    with open(path) as fh:
        src = fh.read()
    for name in ("PySide", "QtCore", "import cv2", "import numpy"):
        assert name not in src, "nesting.py now depends on %s" % name


# ------------------------------------------------- the real Pogo printwood

def _pogo_pieces():
    """The thirteen traced Pogo parts, in millimetres.

    Every former is a HALF former - a left and a right meeting at the
    centreline - so each needs a mirrored twin; the side keel is
    marked "make two" on the plan. Ten formers doubled is 20, the
    side keel makes 22, and the single bottom and top keels make 24.
    """
    import json
    path = os.path.join(os.path.dirname(__file__), "data",
                        "pogo_printwood.json")
    with open(path) as fh:
        raw = json.load(fh)
    out = []
    for rec in raw["parts"]:
        out.append(nst.Piece(rec["name"], rec["loops"],
                             count=rec["count"], mirror=rec["mirror"]))
    return out


def test_the_real_pogo_parts_nest_onto_balsa_stock():
    """A 3 x 36 in sheet, which is what the keels force.

    The keels are 12.3 to 12.9 in long, so a 12 in sheet cannot take
    them and a 36 in one can take all three end to end. This is the
    check that the whole job is physically cuttable, not just that
    the arithmetic closes.
    """
    pieces = _pogo_pieces()
    assert sum(p.count for p in pieces) == 24, "13 parts, 24 cuts"

    sheet_w = 36.0 * 25.4
    sheet_h = 3.0 * 25.4
    sheets, unplaced = nst.nest(pieces, sheet_w, sheet_h)
    assert unplaced == [], "nothing should be too big for 3 x 36 in"
    assert len(sheets) <= 3, "24 cuts should not need more than 3 sheets"


def test_the_keels_do_not_fit_a_twelve_inch_sheet():
    """The constraint that picks the stock, kept where it can fail.

    If a change ever makes this pass, either the keels got shorter or
    the nester started rotating parts - both worth knowing.
    """
    pieces = [p for p in _pogo_pieces() if "Keel" in p.name]
    assert pieces, "no keels in the fixture"
    _sheets, unplaced = nst.nest(pieces, 12.0 * 25.4, 3.0 * 25.4)
    assert len(unplaced) == sum(p.count for p in pieces), (
        "every keel cut should be reported as too long")


# ---------------------------------------------------------- choose_stock

def test_choose_stock_prefers_one_sheet_over_less_waste():
    """Sheet count beats waste, and the Pogo job is the case for it.

    3 x 36 in wastes 77% over two sheets; 4 x 36 in wastes 65% over
    one. A pure waste ranking would also put 3 x 24 in (two sheets,
    65%) level with the winner, and it is not level at the bench.
    """
    best = nst.choose_stock(_pogo_pieces())
    assert best, "no stock size could take the job"
    width, height, sheets, unplaced = best[0]
    assert unplaced == []
    assert len(sheets) == 1
    assert round(width / 25.4) == 36
    assert round(height / 25.4) == 4


def test_choose_stock_reports_the_alternatives_in_order():
    ranked = nst.choose_stock(_pogo_pieces())
    counts = [len(sheets) for _w, _h, sheets, _u in ranked]
    assert counts == sorted(counts), "sheet counts must not go down then up"
    assert len(ranked) >= 3, "the builder wants to see his options"


def test_choose_stock_leaves_out_sizes_that_cannot_take_the_job():
    """A 2 x 24 in sheet cannot hold a 12.9 in keel across its width
    and must not be offered at all."""
    ranked = nst.choose_stock(_pogo_pieces())
    offered = {(round(h / 25.4), round(w / 25.4))
               for w, h, _s, _u in ranked}
    assert (2, 24) not in offered


def test_choose_stock_nests_along_the_grain():
    """Stock is sold with the grain along its LENGTH, so a 3 x 36 in
    sheet is 36 in in the direction parts run, not 3."""
    piece = nst.Piece("Long", _rect(500.0, 10.0))
    ranked = nst.choose_stock([piece])
    assert ranked, "a 500 mm part fits a 36 in sheet lengthwise"
    width, height, _s, _u = ranked[0]
    assert width > height, "the long side must be the nesting direction"


# ------------------------------------------------------------ svg output

def _one_sheet():
    outer = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]
    hole = [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)]
    sheets, _u = nst.nest([nst.Piece("A", [outer, hole]),
                           nst.Piece("B", _rect(20, 20))],
                          200.0, 100.0)
    return sheets[0]


def test_the_svg_is_true_scale_in_millimetres():
    """A cutter must need no scaling step: width/height in mm with a
    matching viewBox, the same contract as svg_export."""
    svg = nst.sheet_svg(_one_sheet())
    assert 'width="200mm"' in svg
    assert 'height="100mm"' in svg
    assert 'viewBox="0 0 200 100"' in svg


def test_each_part_is_one_compound_path_with_evenodd():
    """Outline and holes in one path, so a hole cuts as a hole."""
    svg = nst.sheet_svg(_one_sheet())
    assert svg.count('<path id="A"') == 1
    path = svg.split('<path id="A"')[1].split("/>")[0]
    assert 'fill-rule="evenodd"' in path
    assert path.count("M") == 2, "outline and hole in the same path"


def test_cut_paths_are_not_filled():
    """A filled shape tells a cutter to engrave the inside."""
    svg = nst.sheet_svg(_one_sheet())
    cut = svg.split('<g id="cut"')[1].split("</g>")[0]
    assert 'fill="none"' in svg.split('<g id="cut"')[1][:120]
    assert "fill=\"#" not in cut, "a cut path must not be filled"


def test_labels_are_a_separate_layer_that_can_be_dropped():
    with_labels = nst.sheet_svg(_one_sheet(), label_parts=True)
    without = nst.sheet_svg(_one_sheet(), label_parts=False)
    assert '<g id="labels"' in with_labels
    assert '<g id="labels"' not in without
    # Dropping labels must not touch the geometry.
    assert (with_labels.split('<g id="cut"')[1].split("</g>")[0]
            == without.split('<g id="cut"')[1].split("</g>")[0])


def test_the_stock_outline_can_be_dropped_for_a_bare_cutter():
    assert '<g id="stock"' in nst.sheet_svg(_one_sheet())
    assert '<g id="stock"' not in nst.sheet_svg(_one_sheet(),
                                                outline_sheet=False)


def test_a_label_with_xml_special_characters_is_escaped():
    """A part named off a plan can hold anything; an unescaped & or <
    makes the file unopenable."""
    sheets, _u = nst.nest([nst.Piece('R&D <1>', _rect(20, 20))],
                          100.0, 100.0)
    svg = nst.sheet_svg(sheets[0])
    assert "R&amp;D &lt;1&gt;" in svg
    assert "R&D <1>" not in svg


def test_the_svg_geometry_is_where_the_nester_put_it():
    """Parse the emitted path back and compare to the placement.

    An earlier version of this test matched a formatted coordinate
    as a SUBSTRING of the document, with an `or` fallback. It caught
    nothing: a short decimal appears somewhere in a sheet of numbers
    whatever the geometry is. This reads the path.
    """
    sheet = _one_sheet()
    svg = nst.sheet_svg(sheet)
    for p in sheet.placements:
        frag = svg.split('<path id="%s"' % p.label)[1].split("/>")[0]
        d = frag.split('d="')[1].split('"')[0]
        loops = []
        for sub in d.split("M"):
            sub = sub.strip().rstrip("Z").strip()
            if not sub:
                continue
            loops.append([tuple(float(v) for v in tok.split(","))
                          for tok in sub.split() if "," in tok])
        assert len(loops) == len(p.loops), "%s: lost a loop" % p.label
        for got, want in zip(loops, p.loops):
            assert len(got) == len(want)
            for (gx, gy), (wx, wy) in zip(got, want):
                assert gx == pytest.approx(wx, abs=0.002)
                assert gy == pytest.approx(wy, abs=0.002)


# ---------------------------------------------------------------- report

def test_the_report_accounts_for_every_cut():
    """The failure that matters is quiet: a part needed twice and
    emitted once. The report totals them so it cannot pass unseen."""
    pieces = _pogo_pieces()
    _w, _h, sheets, unplaced = nst.choose_stock(pieces)[0]
    text = nst.nesting_report(sheets, unplaced)
    assert "Total: 24 cuts" in text
    for piece in pieces:
        for label, _loops in piece.instances():
            assert label in text, "%s missing from the report" % label


def test_the_report_names_what_did_not_fit():
    sheets, unplaced = nst.nest([nst.Piece("Huge", _rect(999, 10))],
                                100.0, 100.0)
    text = nst.nesting_report(sheets, unplaced)
    assert "NOT PLACED: Huge" in text
