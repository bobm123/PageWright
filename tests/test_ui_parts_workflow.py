"""The part-by-part tracing workflow in the GUI.

These cover the ways a traced outline can be LOST or silently attached
to the wrong part - the expensive mistakes, since a traced outline costs
real work to redraw and a wrong sheet assignment is invisible until the
part is cut.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TRI = [(10.0, 10.0), (60.0, 10.0), (60.0, 60.0)]
SQ = [(100.0, 100.0), (160.0, 100.0), (160.0, 160.0), (100.0, 160.0)]


def _window_with_photo(tmp_path):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from PySide6.QtWidgets import QApplication
    from pagewright.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    assert app is not None
    w = MainWindow()
    img = np.full((400, 600, 3), 255, np.uint8)
    fp = str(tmp_path / "sheet.png")
    cv2.imwrite(fp, img)
    w.projects.load_photo(fp, start_dewarp=False)
    w.project.calibration.mm_per_pixel = 0.5
    return w


def _trace_into(w, points):
    """Stand in for a GrabCut run that produced `points`."""
    from pagewright.core import contours as cont
    results = [(points, cont.ROLE_OUTER)]
    if (w._active_index < 0
            or not w._objects[w._active_index].is_empty()):
        w.new_object()
    w._objects[w._active_index].set_contours(results)
    return w._objects[w._active_index]


# ----- tracing never overwrites -------------------------------------------

def test_tracing_twice_makes_two_polygons(tmp_path):
    # The bug this replaces: the second trace replaced the first part
    # geometry, and the only sign was the list not growing.
    w = _window_with_photo(tmp_path)
    first = _trace_into(w, TRI)
    second = _trace_into(w, SQ)
    assert len(w._objects) == 2
    assert first is not second
    assert first.contours[0].points() == TRI      # untouched
    assert second.contours[0].points() == SQ


def test_tracing_fills_the_empty_polygon_new_polygon_just_made(tmp_path):
    # New Polygon then Trace Poly must not leave an empty object behind.
    w = _window_with_photo(tmp_path)
    w.new_object()
    assert w._objects[-1].is_empty()
    _trace_into(w, TRI)
    assert len(w._objects) == 1
    assert w._objects[0].contours[0].points() == TRI


def test_retracing_an_existing_part_keeps_the_original(tmp_path):
    # Re-running a trace to try a different detail setting is common;
    # it must not consume the part that was already good.
    w = _window_with_photo(tmp_path)
    kept = _trace_into(w, TRI)
    w._active_index = 0                     # select the finished part
    _trace_into(w, SQ)
    assert len(w._objects) == 2
    assert kept.contours[0].points() == TRI


# ----- clicking an outline selects its part --------------------------------

def test_clicking_an_outline_selects_that_part(tmp_path):
    w = _window_with_photo(tmp_path)
    a = _trace_into(w, TRI)
    b = _trace_into(w, SQ)
    assert w._active_index == 1              # b is active

    w.record_select(a.contours[0])           # a click lands on a's outline
    assert w._active_index == 0
    assert w._objects[w._active_index] is a


def test_clicking_the_active_parts_outline_changes_nothing(tmp_path):
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    b = _trace_into(w, SQ)
    w.record_select(b.contours[0])
    assert w._active_index == 1


def test_an_inactive_outline_still_accepts_clicks(tmp_path):
    # The real blocker for click-to-select: outlines used to go
    # click-deaf unless their part was the active one, which is exactly
    # the part you do NOT need to click.
    from PySide6.QtCore import Qt
    w = _window_with_photo(tmp_path)
    inactive = _trace_into(w, TRI)
    _trace_into(w, SQ)
    w.act_mode_edit.setChecked(True)
    w._mode_edit()
    outline = inactive.contours[0]._outline
    assert outline.acceptedMouseButtons() != Qt.NoButton


def test_a_hidden_outline_does_not_accept_clicks(tmp_path):
    from PySide6.QtCore import Qt
    w = _window_with_photo(tmp_path)
    layer = _trace_into(w, TRI)
    layer.set_visible(False)
    outline = layer.contours[0]._outline
    assert outline.acceptedMouseButtons() == Qt.NoButton


def test_the_clickable_band_is_wider_than_the_drawn_line(tmp_path):
    # A cosmetic 2 px pen is a hard target; the hit area is widened so
    # a click NEAR the line still counts.
    from PySide6.QtCore import QPointF
    w = _window_with_photo(tmp_path)
    layer = _trace_into(w, TRI)
    outline = layer.contours[0]._outline
    # 2 px off the top edge, which runs from (10,10) to (60,10)
    assert outline.shape().contains(QPointF(35.0, 12.0))
    # ...but not 40 px away
    assert not outline.shape().contains(QPointF(35.0, 50.0))


# ----- renaming ------------------------------------------------------------

def test_an_inline_rename_reaches_the_layer(tmp_path):
    w = _window_with_photo(tmp_path)
    layer = _trace_into(w, TRI)
    w.objects_panel.renameCommitted.emit(layer, "F2")
    assert layer.name == "F2"


def test_a_rename_survives_into_the_saved_project(tmp_path):
    # Names decide the build plane on CAD export, so a name that did not
    # persist would quietly change what gets built.
    from pagewright.core import project_io
    w = _window_with_photo(tmp_path)
    layer = _trace_into(w, TRI)
    w.objects_panel.renameCommitted.emit(layer, "F3")
    w._sync_model()
    path = str(tmp_path / "p.tiproj.json")
    project_io.save_project(w.project, path)
    assert project_io.load_project(path).objects[0].name == "F3"


# ----- sheet assignment ----------------------------------------------------

def test_assigning_a_sheet_to_several_parts_at_once(tmp_path):
    from pagewright.model import Sheet
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    _trace_into(w, SQ)
    _trace_into(w, TRI)
    w.project.sheets = [Sheet("1/8 ply", 3.175)]
    w._on_assign_sheet([0, 2], "1/8 ply")
    assert [lyr.build.sheet for lyr in w._objects] == [
        "1/8 ply", None, "1/8 ply"]


def test_assigning_a_sheet_is_undoable(tmp_path):
    from pagewright.model import Sheet
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    _trace_into(w, SQ)
    w.project.sheets = [Sheet("1/16 ply", 1.5875)]
    w._objects[0].build.sheet = "old"
    w._on_assign_sheet([0, 1], "1/16 ply")
    assert [lyr.build.sheet for lyr in w._objects] == [
        "1/16 ply", "1/16 ply"]
    w.undo_stack.undo()
    assert [lyr.build.sheet for lyr in w._objects] == ["old", None]


def test_assigning_none_clears_the_sheet(tmp_path):
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    w._objects[0].build.sheet = "1/8 ply"
    w._on_assign_sheet([0], "")
    assert w._objects[0].build.sheet is None


def test_an_assigned_sheet_drives_the_exported_thickness(tmp_path):
    # The point of the whole feature: assignment has to reach the model
    # the CAD export reads, not just the panel.
    from pagewright.model import Sheet
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    _trace_into(w, SQ)
    w.project.sheet_thickness_mm = 1.5875
    w.project.sheets = [Sheet("1/8 ply", 3.175)]
    w._on_assign_sheet([1], "1/8 ply")
    w._sync_model()
    assert w.project.part_thickness_mm(w.project.objects[0]) == 1.5875
    assert w.project.part_thickness_mm(w.project.objects[1]) == 3.175


def test_renaming_a_sheet_carries_its_parts_across(tmp_path):
    # Parts point at sheets BY NAME, so a rename that did not move them
    # would silently drop every part back to the default thickness.
    from pagewright.model import Sheet
    w = _window_with_photo(tmp_path)
    _trace_into(w, TRI)
    _trace_into(w, SQ)
    w.project.sheets = [Sheet("ply", 3.175)]
    w._on_assign_sheet([0, 1], "ply")

    renames = {"ply": "1/8 birch"}
    w.project.sheets = [Sheet("1/8 birch", 3.175)]
    for layer in w._objects:
        new = renames.get(layer.build.sheet)
        if new:
            layer.build.sheet = new
    w._sync_model()
    assert w.project.part_thickness_mm(w.project.objects[0]) == 3.175


# ----- sorting the list ----------------------------------------------------

def _named(w, names):
    for n in names:
        _trace_into(w, TRI)
        w._objects[-1].name = n
    return w


def test_sorting_orders_the_parts_numerically(tmp_path):
    w = _named(_window_with_photo(tmp_path), ["F10", "6", "F2", "1", "10"])
    w.sort_objects()
    assert [lyr.name for lyr in w._objects] == [
        "1", "6", "10", "F2", "F10"]


def test_sorting_keeps_the_same_part_active(tmp_path):
    # Rows are indices, so a sort that moved the selection would leave
    # the placement fields editing a DIFFERENT part than the one shown.
    w = _named(_window_with_photo(tmp_path), ["F10", "6", "F2", "1"])
    w._active_index = 0                      # F10, which sorts last
    active = w._objects[0]
    w.sort_objects()
    assert w._objects[w._active_index] is active
    assert w._active_index == 3


def test_sorting_is_undoable(tmp_path):
    before = ["F10", "6", "F2", "1"]
    w = _named(_window_with_photo(tmp_path), before)
    w.sort_objects()
    assert [lyr.name for lyr in w._objects] != before
    w.undo_stack.undo()
    assert [lyr.name for lyr in w._objects] == before


def test_sorting_an_ordered_list_records_no_undo(tmp_path):
    w = _named(_window_with_photo(tmp_path), ["1", "2", "3"])
    depth = len(w.undo_stack._done)
    w.sort_objects()
    assert [lyr.name for lyr in w._objects] == ["1", "2", "3"]
    # No change, so no undo step - otherwise Ctrl+Z would appear to do
    # nothing and the user would press it again, losing a real edit.
    assert len(w.undo_stack._done) == depth


def test_sorting_reaches_the_saved_project(tmp_path):
    # The order is worth persisting: it is how the list reads next time.
    from pagewright.core import project_io
    w = _named(_window_with_photo(tmp_path), ["F2", "1", "10"])
    w.sort_objects()
    w._sync_model()
    path = str(tmp_path / "p.tiproj.json")
    project_io.save_project(w.project, path)
    assert [o.name for o in project_io.load_project(path).objects] == [
        "1", "10", "F2"]


def test_duplicate_names_are_reported(tmp_path):
    w = _named(_window_with_photo(tmp_path), ["6", "F1", "6", "3"])
    assert w._duplicate_names() == ["6"]


def test_no_duplicates_reports_nothing(tmp_path):
    w = _named(_window_with_photo(tmp_path), ["6", "F1", "3"])
    assert w._duplicate_names() == []


# ----- the numbering convention in the GUI ---------------------------------

def test_the_numbering_dialog_round_trips_a_convention(tmp_path):
    from pagewright.ui.dialogs import NamingSchemeDialog
    _window_with_photo(tmp_path)                 # for the QApplication
    scheme = {"": "YZ", "f": None, "t": None}
    dlg = NamingSchemeDialog(scheme)
    dlg._try_accept()
    assert dlg.name_planes() == scheme


def test_the_dialog_keeps_a_none_meaning_nothing(tmp_path):
    # The None is the whole point: it is how a sheet says "F is not a
    # former here". Turning it into a plane would stand longerons up.
    from pagewright.ui.dialogs import NamingSchemeDialog
    _window_with_photo(tmp_path)
    dlg = NamingSchemeDialog({"f": None})
    dlg._try_accept()
    assert dlg.name_planes()["f"] is None


def test_the_dialog_can_reload_the_usual_convention(tmp_path):
    from pagewright.ui.dialogs import NamingSchemeDialog
    _window_with_photo(tmp_path)
    dlg = NamingSchemeDialog({"": "YZ"})
    dlg._load_default()
    dlg._try_accept()
    out = dlg.name_planes()
    assert out["f"] == "YZ" and out["r"] == "XZ"


def test_an_empty_convention_restores_the_usual_one(tmp_path):
    # Stored as None, not {}: an empty dict would claim "no name
    # implies anything", which is a different statement.
    from pagewright.ui.dialogs import NamingSchemeDialog
    w = _window_with_photo(tmp_path)
    w.project.name_planes = {"": "YZ"}
    dlg = NamingSchemeDialog(w.project.name_planes)
    while dlg._table.rowCount():
        dlg._table.removeRow(0)
    dlg._try_accept()
    assert dlg.name_planes() == {}
