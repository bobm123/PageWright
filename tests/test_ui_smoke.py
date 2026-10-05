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
