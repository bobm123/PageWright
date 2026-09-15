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
