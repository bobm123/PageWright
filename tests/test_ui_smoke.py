"""Offscreen UI smoke test (QT_QPA_PLATFORM=offscreen).

Catches the 'attribute initialized on one path but read on another'
bug class that static checks miss - e.g. canvas._measurements living
only in set_photo, which crashed PDF import on a fresh launch. Skipped
where PySide6 is not installed (the dev sandbox); it runs on the
Windows dev machine as part of the normal suite.
"""

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


def test_open_svg_makes_a_printable_vector_job(tmp_path):
    # The headline case: open existing art, get a to-scale job with no
    # photo and no calibration gesture, ready for Print Tiles.
    fp = tmp_path / "art.svg"
    fp.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'width="300mm" height="200mm" viewBox="0 0 300 200">'
        '<g inkscape:groupmode="layer" inkscape:label="Outline">'
        '<rect x="10" y="10" width="280" height="180"/></g>'
        '<g inkscape:groupmode="layer" inkscape:label="Detail">'
        '<circle cx="150" cy="100" r="50"/></g></svg>',
        encoding="utf-8")
    w = _window()
    w.open_svg(str(fp))

    assert [o.name for o in w.project.objects] == ["Outline", "Detail"]
    # calibrated straight from the document: 280x180 mm of content
    assert w.project.calibration.is_calibrated
    assert w.project.real_size_mm() == pytest.approx((280.0, 180.0))
    # a blank white sheet stands in for the missing photo, so the canvas,
    # overlays, mouse handling AND the export/tiling controllers (which
    # bail out when _loaded is None) all work unchanged
    assert w.canvas.has_photo()
    assert w._loaded is not None
    assert w._loaded.pixel_width == w.project.pixel_width
    assert w._loaded.pixel_height == w.project.pixel_height
    # it is a one-page job, and the layers reached the Objects panel
    assert len(w.project.pages) == 1
    assert len(w._objects) == 2

    from pagewright.core import tiling
    tiles = tiling.build_tiles(w.project, image_bgr=None, embed_photo=False,
                               page="Letter", margin_mm=6.0, overlap_mm=10.0,
                               base_name="t")
    assert len(tiles) >= 2


def test_open_svg_reports_a_bad_file_without_crashing(tmp_path, monkeypatch):
    fp = tmp_path / "bad.svg"
    fp.write_text("<html>not an svg</html>", encoding="utf-8")
    w = _window()
    shown = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda *a, **k: shown.append(a[2] if len(a) > 2 else ""))
    w.open_svg(str(fp))
    assert shown                       # the user was told
    assert w.project.objects == []      # and nothing was half-imported


def test_svg_files_are_not_accepted_as_raster_pages(tmp_path):
    # Pages are decoded bitmaps; an SVG must be routed to Open SVG
    # instead of failing later with "could not read image".
    fp = tmp_path / "vec.svg"
    fp.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" '
        'height="10mm" viewBox="0 0 10 10"><rect width="10" height="10"/>'
        '</svg>', encoding="utf-8")
    w = _window()
    before = len(w.project.pages)
    w._append_pages([str(fp)])
    assert len(w.project.pages) == before


def test_print_tiles_works_on_an_imported_svg(tmp_path):
    # Regression: the export/tiling controllers read w._loaded.path and
    # bail out when _loaded is None, so a vector job built without a
    # working image produced no tiles at all (Print Tiles did nothing).
    fp = tmp_path / "pat.svg"
    fp.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="350mm" '
        'height="250mm" viewBox="0 0 350 250">'
        '<path d="M 10 10 C 100 0 250 0 340 10 L 340 240 L 10 240 Z"/>'
        '<circle cx="175" cy="125" r="60"/></svg>', encoding="utf-8")
    w = _window()
    w.open_svg(str(fp))
    tiles = w.exports._build_tiles("Print Tiles")
    assert tiles, "Print Tiles produced nothing for a vector job"
    assert any("<path" in svg for _n, svg in tiles)


def test_oversized_svg_is_capped_but_keeps_its_real_size(tmp_path):
    # A 2 m banner at 10 px/mm would be 20000 px wide; the blank sheet is
    # capped and mm_per_pixel rescaled so the PRINTED size is unchanged.
    fp = tmp_path / "banner.svg"
    fp.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2000mm" '
        'height="1000mm" viewBox="0 0 2000 1000">'
        '<rect x="0" y="0" width="2000" height="1000"/></svg>',
        encoding="utf-8")
    w = _window()
    w.open_svg(str(fp))
    assert max(w.project.pixel_width,
               w.project.pixel_height) <= w.SVG_SHEET_MAX_PX
    assert w.project.real_size_mm() == pytest.approx((2000.0, 1000.0))
    assert w.exports._build_tiles("Print Tiles")
