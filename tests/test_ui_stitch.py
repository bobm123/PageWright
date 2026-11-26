"""Tools > Stitch Pages (ui/stitch_dialog.py), offscreen."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("PySide6")
np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pages(tmp_path):
    """A: 100 x 80 with a bar from x=70 to the edge; B: 60 x 80 with the
    bar's other end at x=40. With B at dx=90 the bar is 60 px long."""
    a = np.full((80, 100, 3), 255, np.uint8)
    b = np.full((80, 60, 3), 255, np.uint8)
    a[40:43, 70:100] = 0
    b[40:43, 0:41] = 0
    pa, pb = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    cv2.imwrite(pa, a)
    cv2.imwrite(pb, b)
    return pa, pb


def _dialog(tmp_path, mm_per_px=1.0):
    _app()
    from pagewright.ui.stitch_dialog import StitchDialog
    pa, pb = _pages(tmp_path)
    return StitchDialog(None, path_a=pa, path_b=pb, mm_per_px=mm_per_px)


def test_qt_places_page_b_where_the_core_says_it_is(tmp_path):
    """The view and the saved image must agree. Qt puts pixel corners
    on integers and the core puts centres there, so they differ by
    exactly half a pixel and by nothing else."""
    from PySide6.QtCore import QPointF
    from pagewright.core import stitch as st
    d = _dialog(tmp_path)
    p = st.Placement(dx=33, dy=-7, angle_deg=3.7, turn_b=90)
    d.set_placement(p)
    turn = st._turn_matrix((60, 80), 90)
    for pt in ((0, 0), (59, 0), (12, 71)):
        tx, ty = st._apply(turn, pt)
        q = d._item_b.mapToScene(QPointF(tx + 0.5, ty + 0.5))
        cx, cy = st.map_b(p, (60, 80), pt)
        assert q.x() == pytest.approx(cx + 0.5, abs=1e-6)
        assert q.y() == pytest.approx(cy + 0.5, abs=1e-6)


def test_keys_nudge_and_turn_page_b(tmp_path):
    from PySide6.QtCore import Qt
    d = _dialog(tmp_path)
    assert d.key(Qt.Key_Right, Qt.NoModifier)
    assert d.key(Qt.Key_Down, Qt.ShiftModifier)
    assert d.key(Qt.Key_BracketRight, Qt.NoModifier)
    assert not d.key(Qt.Key_A, Qt.NoModifier)
    p = d.placement()
    assert (p.dx, p.dy) == (1.0, 10.0)
    assert p.angle_deg == pytest.approx(0.05)
    assert d._item_b.pos().x() == 1.0


def test_a_drag_lands_on_whole_pixels(tmp_path):
    d = _dialog(tmp_path)
    d._item_b.setPos(12.4, 7.6)
    assert (d.placement().dx, d.placement().dy) == (12.0, 8.0)
    assert (d._item_b.pos().x(), d._item_b.pos().y()) == (12.0, 8.0)


def test_the_seam_check_is_picked_by_two_clicks_and_follows_b(tmp_path,
                                                              monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QInputDialog
    d = _dialog(tmp_path)
    d._dx.setValue(90)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("60 mm", True)))
    d.arm_measure()
    d.pick(QPointF(70.5, 41.5))          # the bar's end on A
    assert d.measuring()
    d.pick(QPointF(130.5, 41.5))         # its other end, on B (x=40)
    assert not d.measuring()
    measured, expected, err = d.check_result()
    assert (measured, expected) == (pytest.approx(60.0), 60.0)
    d._dx.setValue(85)                   # B moves; the check goes with it
    assert d.check_result()[0] == pytest.approx(55.0)


def test_a_first_click_off_page_a_is_refused(tmp_path):
    from PySide6.QtCore import QPointF
    d = _dialog(tmp_path)
    d._dx.setValue(200)
    d.arm_measure()
    d.pick(QPointF(250.0, 40.0))          # on B only
    assert d._pending_a is None and d.measuring()


def test_save_writes_what_the_cli_would_and_reopens(tmp_path):
    from pagewright import cli
    d = _dialog(tmp_path)
    d._dx.setValue(90)
    d.set_check((70, 41), (40, 41), 60.0)
    out = str(tmp_path / "gui.png")
    result = d.save_to(out)
    assert result.size == (85, 27) and not result.failed_checks

    pa, pb = d._paths
    cli_out = str(tmp_path / "cli.png")
    assert cli.run([ "stitch", pa, pb, "--offset", "90", "0", "--trim",
                     "12", "-o", cli_out, "-q"]) == cli.EXIT_OK
    assert np.array_equal(cv2.imread(out), cv2.imread(cli_out))

    from pagewright.ui.stitch_dialog import StitchDialog
    again = StitchDialog(None)
    assert again.load_sidecar(str(tmp_path / "gui.stitch.json"))
    assert again.placement().dx == 90.0
    assert again.check_result()[0] == pytest.approx(60.0)
    with open(str(tmp_path / "gui.stitch.json"), encoding="utf-8") as fh:
        assert json.load(fh)["checks"][0]["expected_mm"] == 60.0


def test_the_main_window_offers_stitch_pages(tmp_path, monkeypatch):
    _app()
    from pagewright.ui.main_window import MainWindow
    from pagewright.ui import stitch_dialog
    seen = {}

    def fake_exec(self):
        seen["a"] = self._paths[0]
        return 0
    monkeypatch.setattr(stitch_dialog.StitchDialog, "exec", fake_exec)
    w = MainWindow()
    titles = [a.text() for m in w.menuBar().actions() if m.menu()
              for a in m.menu().actions()]
    assert "&Stitch Pages…" in titles
    w.stitch_pages()
    assert seen == {"a": None}           # no image open, no page A
