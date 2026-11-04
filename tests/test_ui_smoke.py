"""Offscreen UI smoke test (QT_QPA_PLATFORM=offscreen).

Catches the 'attribute initialized on one path but read on another'
bug class that static checks miss - e.g. canvas._measurements living
only in set_photo, which crashed PDF import on a fresh launch. Skipped
where PySide6 is not installed (the dev sandbox); it runs on the
Windows dev machine as part of the normal suite.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _window():
    from PySide6.QtWidgets import QApplication
    from pagewright.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    assert app is not None
    return MainWindow()


def test_fresh_window_state_is_complete():
    w = _window()
    # every accessor a controller may hit BEFORE any photo is loaded
    assert w.canvas.measurements() == []
    assert not w.canvas.has_measurements()
    assert w.canvas.roi_rect() is None
    assert not w.canvas.has_photo()
    w.canvas.clear_measurements()
    w.canvas.clear_all()
    w._store_current_page()          # no pages: must be a no-op
    w._refresh_pages_panel()
    assert w._current_tool_name() == "trace"


def test_store_current_page_before_any_photo():
    # the PDF-import first-page activation path in miniature
    from pagewright.model import PageEntry
    w = _window()
    w.project.pages = [PageEntry(source_path="x.png")]
    w.project.current_page = 0
    w._store_current_page()          # crashed before the _measurements fix
    assert w.project.pages[0].measurements == []


def _window_with_photo(tmp_path):
    """A window with a real photo loaded, so the canvas accepts overlays."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    w = _window()
    img = np.full((400, 600, 3), 255, np.uint8)
    fp = str(tmp_path / "smoke.png")
    cv2.imwrite(fp, img)
    w.projects.load_photo(fp, start_dewarp=False)
    w.project.calibration.mm_per_pixel = 0.5     # 1 px = 0.5 mm
    w.canvas.set_measure_formatter(w._format_measure)
    return w


def test_ruler_handles_follow_measure_mode(tmp_path):
    from PySide6.QtCore import Qt
    w = _window_with_photo(tmp_path)
    w.canvas.set_measurements([[100, 100, 300, 100]])
    # A finished ruler stays editable in the non-point-placing modes, so
    # it can be moved or deleted without re-arming the Measure tool.
    for enter in (w.canvas.enter_pan_mode, w.canvas.enter_edit_mode,
                  w.start_measure):
        enter()
        handles = w.canvas._measurements[0]["handles"]
        assert len(handles) == 2
        assert [h.which for h in handles] == [0, 1]

    # ...but not while placing calibration points, where a click on the
    # ruler must land on the canvas instead of grabbing the ruler.
    w.canvas.start_calibration()
    assert w.canvas._measurements[0]["handles"] == []
    body = w.canvas._measurements[0]["items"][0]
    assert body.acceptedMouseButtons() == Qt.NoButton


def test_dragging_an_endpoint_updates_the_measured_value(tmp_path):
    from PySide6.QtCore import QPointF
    w = _window_with_photo(tmp_path)
    w.canvas.set_measurements([[100, 100, 300, 100]])   # 200 px = 100 mm
    w.start_measure()
    assert w.canvas._measurements[0]["items"][1].text() == "100.00 mm"
    # drag the second endpoint: 200x200 px -> 282.84 px -> 141.42 mm
    w.canvas._measurements[0]["handles"][1].setPos(QPointF(300, 300))
    assert w.canvas.measurement_at(0) == (100.0, 100.0, 300.0, 300.0)
    assert w.canvas._measurements[0]["items"][1].text() == "141.42 mm"


def test_moving_a_whole_ruler_preserves_its_length(tmp_path):
    w = _window_with_photo(tmp_path)
    w.canvas.set_measurements([[100, 100, 300, 100]])
    w.start_measure()
    before = w.canvas._measurements[0]["items"][1].text()
    q = w.canvas.measurement_at(0)
    w.canvas.set_measurement(0, (q[0] + 50, q[1] + 20,
                                 q[2] + 50, q[3] + 20))
    assert w.canvas.measurement_at(0) == (150.0, 120.0, 350.0, 120.0)
    assert w.canvas._measurements[0]["items"][1].text() == before


def test_ruler_edits_and_deletes_are_undoable_and_persisted(tmp_path):
    w = _window_with_photo(tmp_path)
    w.canvas.set_measurements([[100, 100, 300, 100], [50, 200, 250, 200]])
    w.start_measure()

    def page():
        return [list(q) for q in w.project.pages[0].measurements]

    before = w.canvas.measurement_at(0)
    w.canvas.set_measurement(0, (100, 100, 300, 300))
    w._on_measurement_edited(0, before, w.canvas.measurement_at(0))
    assert page() == w.canvas.measurements()      # stored on edit
    w._do_undo()
    assert w.canvas.measurement_at(0) == before
    assert page() == w.canvas.measurements()      # and on undo
    w._do_redo()
    assert w.canvas.measurement_at(0) == (100.0, 100.0, 300.0, 300.0)

    # delete restores at the SAME index, keeping item indices in step
    w.delete_measurement(0)
    assert len(w.canvas.measurements()) == 1
    w._do_undo()
    assert len(w.canvas.measurements()) == 2
    assert w.canvas.measurement_at(0) == (100.0, 100.0, 300.0, 300.0)
    assert [m["items"][0].index
            for m in w.canvas._measurements] == [0, 1]
    assert page() == w.canvas.measurements()


def test_ruler_labels_follow_the_unit_preference(tmp_path):
    w = _window_with_photo(tmp_path)
    w.canvas.set_measurements([[100, 100, 300, 100]])   # 100 mm
    assert w.canvas._measurements[0]["items"][1].text() == "100.00 mm"
    w.set_unit("cm")
    assert w.canvas._measurements[0]["items"][1].text() == "10.00 cm"


def test_clicking_a_ruler_edits_it_instead_of_starting_a_new_one(tmp_path):
    # Regression: the measure-mode press handler used to swallow every
    # click, so grabbing a ruler started a second ruler on top of it.
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = _window_with_photo(tmp_path)
    w.resize(800, 600)
    w.show()
    w.canvas.set_measurements([[100, 100, 300, 100]])
    w.start_measure()
    c = w.canvas

    def press(scene_x, scene_y):
        vp = c.mapFromScene(QPointF(scene_x, scene_y))
        c.mousePressEvent(QMouseEvent(
            QEvent.MouseButtonPress, QPointF(vp),
            c.viewport().mapToGlobal(vp),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    press(200, 100)          # on the ruler's line
    assert c._measure_p0 is None
    press(100, 100)          # on an endpoint handle
    assert c._measure_p0 is None
    press(400, 300)          # empty canvas: this one does arm a ruler
    assert c._measure_p0 is not None
    assert len(c.measurements()) == 1


def test_dragging_a_ruler_in_pan_mode_moves_it_instead_of_panning(tmp_path):
    # Pan mode is ScrollHandDrag; without an explicit hand-off the view
    # swallows the press and the ruler could never be grabbed there.
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = _window_with_photo(tmp_path)
    w.resize(800, 600)
    w.show()
    w.canvas.set_measurements([[100, 100, 300, 100]])
    w.canvas.enter_pan_mode()
    c = w.canvas
    h_scroll = c.horizontalScrollBar().value()
    v_scroll = c.verticalScrollBar().value()

    def send(kind, scene_x, scene_y, button=Qt.LeftButton):
        vp = c.mapFromScene(QPointF(scene_x, scene_y))
        ev = QMouseEvent(kind, QPointF(vp), c.viewport().mapToGlobal(vp),
                         button, button, Qt.NoModifier)
        {QEvent.MouseButtonPress: c.mousePressEvent,
         QEvent.MouseMove: c.mouseMoveEvent,
         QEvent.MouseButtonRelease: c.mouseReleaseEvent}[kind](ev)

    # Drag the body: both ends shift by the same delta, length holds.
    send(QEvent.MouseButtonPress, 200, 100)
    send(QEvent.MouseMove, 240, 125)
    send(QEvent.MouseButtonRelease, 240, 125)
    x0, y0, x1, y1 = c.measurement_at(0)
    assert x0 > 100.0 and y0 > 100.0            # actually moved
    # Rigid move: same length, and both ends shifted by the same delta.
    assert (x1 - x0) == pytest.approx(200.0) and (y1 - y0) == pytest.approx(0.0)
    assert (x1 - 300.0) == pytest.approx(x0 - 100.0)
    assert (y1 - 100.0) == pytest.approx(y0 - 100.0)
    # The view itself must not have scrolled.
    assert c.horizontalScrollBar().value() == h_scroll
    assert c.verticalScrollBar().value() == v_scroll


def test_landscape_tiling_gives_a_landscape_printer(tmp_path):
    # Regression: QPrinter.setPageLayout() keeps a custom page SIZE but
    # drops the orientation, so a landscape tiling opened a portrait
    # preview and then warned about a 216x279 / 279x216 mismatch.
    from PySide6.QtGui import QPageLayout
    w = _window_with_photo(tmp_path)
    w.tiling_panel.from_dict({"page": "Letter", "landscape": True})
    c = w.exports

    pw_mm, ph_mm = c._tile_page_mm()
    assert pw_mm > ph_mm                       # tiles laid out wide

    printer = c._make_printer()
    layout = printer.pageLayout()
    assert layout.orientation() == QPageLayout.Landscape
    paper = layout.fullRect(QPageLayout.Millimeter)
    assert paper.width() > paper.height()
    # The paper now matches the layout, so no mismatch warning fires.
    # (_check_paper_match would block on a modal QMessageBox otherwise.)
    assert abs(paper.width() - pw_mm) <= 2.0
    assert abs(paper.height() - ph_mm) <= 2.0


def test_portrait_tiling_still_gives_a_portrait_printer(tmp_path):
    from PySide6.QtGui import QPageLayout
    w = _window_with_photo(tmp_path)
    w.tiling_panel.from_dict({"page": "Letter", "landscape": False})
    c = w.exports
    printer = c._make_printer()
    assert printer.pageLayout().orientation() == QPageLayout.Portrait
    paper = printer.pageLayout().fullRect(QPageLayout.Millimeter)
    assert paper.height() > paper.width()


def test_trace_detail_settings_round_trip(tmp_path, monkeypatch):
    # The Trace detail control and "one shape only" are app settings, so
    # they must survive a new window (QSettings), not just this one.
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(
        QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    monkeypatch.setattr(
        QSettings, "value", lambda self, k, d=None: store.get(k, d))
    store = {}
    w = _window()
    assert w.trace_tolerance_mm() == pytest.approx(0.35)
    assert w.trace_keep_largest() is True
    w.set_trace_tolerance_mm(0.5)
    w.set_trace_keep_largest(False)
    assert store["trace_tolerance_mm"] == pytest.approx(0.5)
    assert store["trace_keep_largest"] is False
    # a fresh window reads them back
    fresh = _window()
    assert fresh.trace_tolerance_mm() == pytest.approx(0.5)
    assert fresh.trace_keep_largest() is False


def test_trace_tolerance_is_clamped_positive(tmp_path):
    w = _window()
    before = w.trace_tolerance_mm()
    w.set_trace_tolerance_mm(-5.0)
    assert w.trace_tolerance_mm() > 0.0
    w.set_trace_tolerance_mm(before)


def _notched_object(w, mm_per_px=0.0846667):
    """Give the window one object: a rectangle with an off-size notch."""
    from pagewright.model import Contour, TracedObject
    step = 25.4 / 16.0 / mm_per_px        # 1/16 in in pixels
    bad = step * 0.88                     # an off-size notch floor
    h = 40.0 / mm_per_px
    pts = [(0.0, 0.0), (0.0, h), (bad, h), (bad, h - step * 2),
           (bad * 2, h - step * 2), (bad * 2, h), (bad * 3, h),
           (bad * 3, 0.0)]
    obj = TracedObject(name="Former")
    obj.contours = [Contour(points=pts)]
    w.project.calibration.mm_per_pixel = mm_per_px
    w.project.objects = [obj]
    w.project.pixel_width, w.project.pixel_height = 2000, 2000
    w.canvas.show_blank(2000, 2000)
    w._load_layers_from_project()
    w._active_index = 0
    return w._objects[0]


def test_clean_up_notches_is_one_undoable_step(tmp_path):
    w = _window()
    layer = _notched_object(w)
    before = [list(c.points()) for c in layer.contours]
    w.clean_up_notches()
    after = [list(c.points()) for c in layer.contours]
    assert after != before
    assert w.undo_stack.can_undo()
    assert w.undo_stack.undo_label() == "clean up notches"
    w._do_undo()
    restored = [list(c.points()) for c in layer.contours]
    for b, r in zip(before[0], restored[0]):
        assert b[0] == pytest.approx(r[0], abs=1e-6)
        assert b[1] == pytest.approx(r[1], abs=1e-6)
    w._do_redo()
    again = [list(c.points()) for c in layer.contours]
    for a, g in zip(after[0], again[0]):
        assert a[0] == pytest.approx(g[0], abs=1e-6)


def test_clean_up_notches_needs_a_calibrated_scale(tmp_path, monkeypatch):
    w = _window()
    layer = _notched_object(w)
    w.project.calibration.mm_per_pixel = None
    shown = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    before = [list(c.points()) for c in layer.contours]
    w.clean_up_notches()
    assert shown, "the user was not told why nothing happened"
    assert [list(c.points()) for c in layer.contours] == before
    assert not w.undo_stack.can_undo()


def test_clean_up_notches_with_no_selection_is_a_no_op(tmp_path):
    w = _window()
    w._active_index = -1
    w.clean_up_notches()
    assert not w.undo_stack.can_undo()

# ----- Export SVG through the shared job layer ---------------------------

def _window_with_traced_object(tmp_path):
    from pagewright.model import Contour, TracedObject
    w = _window_with_photo(tmp_path)
    w.project.objects = [TracedObject(
        name="part",
        contours=[Contour(points=[(50, 50), (250, 50), (250, 200),
                                  (50, 200)])])]
    # objects live on the canvas; _sync_model rebuilds project.objects FROM
    # the layers, so setting the model alone would be wiped on export.
    w._load_layers_from_project()
    return w


def test_export_svg_writes_a_real_file(tmp_path, monkeypatch):
    """Drives File > Export SVG with only the dialogs stubbed, so the
    whole path below them is the real one. There was no test here
    before, which is how the _loaded-is-None bug reached a release."""
    from pagewright.ui import export_controller as ec
    w = _window_with_traced_object(tmp_path)
    out = str(tmp_path / "out.svg")

    class _Dlg:
        def __init__(self, _parent):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Accepted

        def values(self):
            # embed, downscale_max, filled, inkscape, annotations
            return (False, None, False, True, False)

    monkeypatch.setattr(ec, "ExportSvgDialog", _Dlg)
    monkeypatch.setattr(ec.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))
    w.exports.export_svg()

    assert os.path.isfile(out)
    text = io.open(out, encoding="utf-8").read()
    assert "inkscape:label" in text          # the dialog choice took effect
    assert "mm" in text                      # sized in real units


def test_export_svg_reports_a_job_error_in_a_dialog(tmp_path, monkeypatch):
    """A JobError from the shared layer has to surface as a message box,
    not a traceback: the GUI and the CLI differ only in how they
    report."""
    from pagewright.ui import export_controller as ec
    w = _window_with_traced_object(tmp_path)

    class _Dlg:
        def __init__(self, _parent):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Accepted

        def values(self):
            return (False, None, False, False, False)

    seen = {}

    def _warn(_parent, title, text):
        seen["title"], seen["text"] = title, text

    bad = str(tmp_path / "no_such_dir" / "deep" / "x.svg")
    monkeypatch.setattr(ec, "ExportSvgDialog", _Dlg)
    monkeypatch.setattr(ec.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (bad, "")))
    monkeypatch.setattr(ec.QMessageBox, "warning", staticmethod(_warn))
    # make the write fail inside the job layer
    monkeypatch.setattr(ec.jobs.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("denied")))
    w.exports.export_svg()
    assert seen.get("title") == "Export SVG"
    assert "cannot write" in seen.get("text", "")

# ----- Flatten Checked through the shared per-page seam ------------------

def test_batch_flatten_runs_the_shared_per_page_transform(tmp_path,
                                                          monkeypatch):
    """Flatten Checked had no test, which is how batch.seed_and_refine
    could return a tuple on one path and a model on the other and still
    ship. It now calls jobs.flatten_page, the same seam the headless
    `flatten` uses, so one test covers both."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from pagewright.model import PageEntry
    from pagewright.ui import main_window as mw

    w = _window()
    srcs = []
    for n in range(2):
        img = np.full((300, 400, 3), 255, np.uint8)
        cv2.putText(img, "text line", (40, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        fp = str(tmp_path / ("pg%d.png" % n))
        cv2.imwrite(fp, img)
        srcs.append(fp)
    w.project.pages = [PageEntry(source_path=p) for p in srcs]
    w.project.current_page = 0

    # stand in for the Flatten tool's confirmed outline and the panel
    from pagewright.core import dewarp
    seed = dewarp.default_spread_model(400, 300)
    monkeypatch.setattr(w.dewarp_stage, "current_outline", lambda: seed)
    monkeypatch.setattr(w.dewarp_stage, "source_wh", lambda: (400, 300))
    monkeypatch.setattr(w.pages_panel, "checked_rows", lambda: [0, 1])

    shown = {}
    monkeypatch.setattr(
        mw.QMessageBox, "information",
        staticmethod(lambda _p, title, text: shown.update(t=text)))

    before = len(w.project.pages)
    w.batch_flatten()

    # a spread model yields L+R per page, so 2 pages -> 4 derived
    assert len(w.project.pages) > before
    assert "Flattened 2 page(s)" in shown.get("t", "")
    assert "Problems:" not in shown.get("t", "")

def test_open_svg_agrees_with_the_cli_on_the_sheet_size(tmp_path):
    """File > Open SVG and `pagewright import-svg` read the same file
    through the same helper, so the sheet is the same size either way.
    The mm->px arithmetic used to exist in three copies."""
    from pagewright.core import jobs
    svg = tmp_path / "card.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200mm" '
        'height="150mm" viewBox="0 0 200 150">'
        '<rect x="10" y="10" width="180" height="130"/></svg>',
        encoding="utf-8")

    headless, size_mm, _mpp = jobs.project_from_svg(str(svg))
    w = _window()
    w.open_svg(str(svg))

    # size_mm is the CONTENT bounding box (the rect), not the document:
    # a 180x130 rect inside a 200x150 mm sheet.
    assert round(size_mm[0]) == 180 and round(size_mm[1]) == 130
    gui_mpp = w.project.calibration.mm_per_pixel
    assert gui_mpp == headless.calibration.mm_per_pixel
    assert len(w.project.objects) == len(headless.objects)
    # the window swaps in a blank working sheet, but it has to stay the
    # same real-world size the headless path computed
    assert w.project.real_size_mm()[0] == pytest.approx(
        headless.real_size_mm()[0], abs=1.0)
    assert w.project.real_size_mm()[1] == pytest.approx(
        headless.real_size_mm()[1], abs=1.0)

# ----- Build survives the canvas round trip ------------------------------

def test_build_survives_sync_model(tmp_path):
    """_sync_model rebuilds project.objects FROM the canvas layers, so a
    Build set on the model alone would be silently wiped on the next
    export. The layer has to carry it."""
    from pagewright.model import Build, Contour, TracedObject
    w = _window_with_photo(tmp_path)
    w.project.objects = [TracedObject(
        name="Former 3",
        contours=[Contour(points=[(50, 50), (250, 50), (250, 200)])],
        build=Build(thickness_mm=1.6, plane="YZ", station_mm=120.0))]
    w._load_layers_from_project()

    assert w._objects[0].build.plane == "YZ"      # reached the layer
    w._sync_model()                               # and back again
    b = w.project.objects[0].build
    assert (b.thickness_mm, b.plane, b.station_mm) == (1.6, "YZ", 120.0)


def test_build_survives_save_and_reload(tmp_path):
    """The whole point: a designer sets thickness and station, saves,
    and finds them there tomorrow."""
    from pagewright.core import project_io
    from pagewright.model import Build, Contour, TracedObject
    w = _window_with_photo(tmp_path)
    w.project.objects = [TracedObject(
        name="Former 3",
        contours=[Contour(points=[(50, 50), (250, 50), (250, 200)])],
        build=Build(thickness_mm=1.6, plane="YZ", station_mm=120.0))]
    w._load_layers_from_project()
    w._sync_model()

    out = str(tmp_path / "job.tiproj.json")
    project_io.save_project(w.project, out)
    reloaded = project_io.load_project(out)
    b = reloaded.objects[0].build
    assert (b.thickness_mm, b.plane, b.station_mm) == (1.6, "YZ", 120.0)


# ----- stock and placement panel -----------------------------------------

def _window_with_two_parts(tmp_path):
    from pagewright.model import Contour, TracedObject
    w = _window_with_photo(tmp_path)
    w.project.objects = [
        TracedObject(name="Former 3",
                     contours=[Contour(points=[(50, 50), (250, 50),
                                               (250, 200)])]),
        TracedObject(name="Rib A",
                     contours=[Contour(points=[(60, 60), (260, 60),
                                               (260, 210)])]),
    ]
    w._load_layers_from_project()
    w._refresh_object_list()
    return w


def test_typing_a_fraction_sets_the_sheet_stock(tmp_path):
    """Balsa is sold in sixteenths and the plan prints '1/16', so that
    is what a modeller will type."""
    w = _window_with_two_parts(tmp_path)
    w.objects_panel._sheet.setText("1/16 in")
    w.objects_panel._emit_sheet()
    assert w.project.sheet_thickness_mm == pytest.approx(1.5875)


def test_unparseable_stock_marks_the_field_and_changes_nothing(tmp_path):
    w = _window_with_two_parts(tmp_path)
    w.objects_panel._sheet.setText("1/16 in")
    w.objects_panel._emit_sheet()
    w.objects_panel._sheet.setText("about yay thick")
    w.objects_panel._emit_sheet()
    assert w.project.sheet_thickness_mm == pytest.approx(1.5875)
    assert "ffe4e4" in w.objects_panel._sheet.styleSheet()


def test_build_edits_land_on_the_selected_layer(tmp_path):
    """Onto the LAYER: _sync_model rebuilds project.objects from the
    layers, so a value written to the model would be lost on export."""
    w = _window_with_two_parts(tmp_path)
    w._on_object_row_changed(1)                    # select "Rib A"
    w.objects_panel._plane.setCurrentIndex(
        w.objects_panel._plane.findData("XZ"))
    w.objects_panel._station.setText("-25 mm")
    w.objects_panel._emit_len("station_mm", w.objects_panel._station,
                              allow_negative=True)

    assert w._objects[1].build.plane == "XZ"
    assert w._objects[0].build.plane is None       # the other is untouched
    w._sync_model()
    assert w.project.objects[1].build.station_mm == pytest.approx(-25.0)


def test_selecting_a_part_shows_its_own_settings(tmp_path):
    from pagewright.model import Build
    w = _window_with_two_parts(tmp_path)
    w._objects[0].build = Build(thickness_mm=3.0, plane="YZ")
    w._objects[1].build = Build(plane="XZ")
    w._on_object_row_changed(0)
    assert w.objects_panel._thick.text() == "3 mm"
    w._on_object_row_changed(1)
    assert w.objects_panel._thick.text() == ""     # falls back to sheet
    assert w.objects_panel._plane.currentData() == "XZ"


def test_the_note_shows_the_thickness_that_will_be_used(tmp_path):
    """A part with no thickness of its own still builds, at the sheet
    stock, so the panel has to say which number applies."""
    w = _window_with_two_parts(tmp_path)
    w.project.sheet_thickness_mm = 1.5875
    w._on_object_row_changed(0)
    w.objects_panel._plane.setCurrentIndex(
        w.objects_panel._plane.findData("YZ"))
    note = w.objects_panel._build_note.text()
    assert "1.5875 mm on YZ" in note


def test_the_group_is_disabled_with_nothing_selected(tmp_path):
    w = _window_with_photo(tmp_path)
    w._refresh_object_list()
    assert not w.objects_panel._build_box.isEnabled()


def test_sheet_stock_survives_save_and_reload(tmp_path):
    from pagewright.core import project_io
    w = _window_with_two_parts(tmp_path)
    w.objects_panel._sheet.setText("1/16 in")
    w.objects_panel._emit_sheet()
    w._sync_model()
    out = str(tmp_path / "stock.tiproj.json")
    project_io.save_project(w.project, out)
    assert project_io.load_project(out).sheet_thickness_mm == \
        pytest.approx(1.5875)


# ----- Find Parts in the GUI ---------------------------------------------

def _window_with_plan(tmp_path):
    """A window holding a sheet with three closed outlines on it."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((600, 900, 3), 255, np.uint8)
    for x in (60, 360, 660):
        cv2.rectangle(img, (x, 120), (x + 180, 420), (0, 0, 0), 3)
    fp = str(tmp_path / "plan.png")
    cv2.imwrite(fp, img)
    w = _window()
    w.projects.load_photo(fp, start_dewarp=False)
    w.project.calibration.mm_per_pixel = 25.4 / 300.0
    return w


def test_find_parts_adds_every_outline_as_an_object(tmp_path):
    w = _window_with_plan(tmp_path)
    w.find_parts()
    assert len(w._objects) == 3
    assert len(w.project.objects) == 3        # reached the model too


def test_find_parts_is_one_undo_step(tmp_path):
    """15 parts on a real sheet: undoing them one at a time would be
    unusable."""
    w = _window_with_plan(tmp_path)
    w.find_parts()
    w.undo_stack.undo()
    assert w._objects == [] and w.project.objects == []
    w.undo_stack.redo()
    assert len(w._objects) == 3


def test_find_parts_adds_rather_than_replaces(tmp_path):
    """A sheet gets worked in passes: run it, keep what it found, then
    paint seeds for the ones it missed."""
    w = _window_with_plan(tmp_path)
    w.find_parts()
    w.find_parts()
    assert len(w._objects) == 6
    assert [o.name for o in w._objects][:4] == [
        "Part 1", "Part 2", "Part 3", "Part 4"]


def test_select_area_limits_the_search(tmp_path):
    from PySide6.QtCore import QRectF
    w = _window_with_plan(tmp_path)
    w.canvas.set_roi(QRectF(40, 100, 230, 340))     # the first box only
    w.find_parts()
    assert len(w._objects) == 1
    xs = [p[0] for p in w._objects[0].contours[0].points()]
    # and the points are in FULL-image coordinates, not region ones
    assert min(xs) > 40


def test_find_parts_needs_a_calibrated_scale(tmp_path, monkeypatch):
    """Every threshold it uses is a real-world size."""
    from pagewright.ui import main_window as mw
    w = _window_with_plan(tmp_path)
    w.project.calibration.mm_per_pixel = None
    seen = {}
    monkeypatch.setattr(mw.QMessageBox, "information",
                        staticmethod(lambda _p, t, x: seen.update(t=t, x=x)))
    w.find_parts()
    assert seen.get("t") == "Find Parts"
    assert "alibrate" in seen.get("x", "")
    assert w._objects == []


def test_a_blank_sheet_says_so_rather_than_adding_nothing(tmp_path,
                                                          monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from pagewright.ui import main_window as mw
    img = np.full((300, 400, 3), 255, np.uint8)
    fp = str(tmp_path / "blank.png")
    cv2.imwrite(fp, img)
    w = _window()
    w.projects.load_photo(fp, start_dewarp=False)
    w.project.calibration.mm_per_pixel = 25.4 / 300.0
    seen = {}
    monkeypatch.setattr(mw.QMessageBox, "information",
                        staticmethod(lambda _p, t, x: seen.update(t=t, x=x)))
    w.find_parts()
    assert seen.get("t") == "Find Parts"
    assert "seeds" in seen.get("x", "")
