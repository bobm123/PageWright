"""Tests for core.undo -- pure Python."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core.undo import FnCommand, UndoStack  # noqa: E402


def _cmd(name, log):
    return FnCommand(name,
                     undo=lambda: log.append(("undo", name)),
                     redo=lambda: log.append(("redo", name)))


def test_empty_stack():
    s = UndoStack()
    assert not s.can_undo() and not s.can_redo()
    assert s.undo() is False and s.redo() is False


def test_push_undo_redo_order():
    log = []
    s = UndoStack()
    s.push(_cmd("a", log))
    s.push(_cmd("b", log))
    assert s.undo_label() == "b"
    assert s.undo() and log[-1] == ("undo", "b")
    assert s.undo() and log[-1] == ("undo", "a")
    assert not s.can_undo()
    assert s.redo() and log[-1] == ("redo", "a")
    assert s.redo() and log[-1] == ("redo", "b")


def test_push_clears_redo():
    log = []
    s = UndoStack()
    s.push(_cmd("a", log))
    s.undo()
    assert s.can_redo()
    s.push(_cmd("b", log))
    assert not s.can_redo()


def test_capacity_drops_oldest():
    log = []
    s = UndoStack(capacity=3)
    for n in "wxyz":
        s.push(_cmd(n, log))
    assert len(s) == 3
    assert s.undo_label() == "z"


def test_on_change_fires():
    s = UndoStack()
    hits = []
    s.on_change(lambda: hits.append(1))
    log = []
    s.push(_cmd("a", log))
    s.undo()
    s.redo()
    s.clear()
    assert len(hits) >= 4


def test_index_ops_round_trip():
    arr = [10, 20, 30]
    s = UndoStack()
    arr[1] = 99
    s.push(FnCommand("move", undo=lambda: arr.__setitem__(1, 20),
                     redo=lambda: arr.__setitem__(1, 99)))
    arr.insert(3, 40)
    s.push(FnCommand("ins", undo=lambda: arr.pop(3),
                     redo=lambda: arr.insert(3, 40)))
    arr.pop(0)
    s.push(FnCommand("del", undo=lambda: arr.insert(0, 10),
                     redo=lambda: arr.pop(0)))
    assert arr == [99, 30, 40]
    s.undo(); s.undo(); s.undo()
    assert arr == [10, 20, 30]
    s.redo(); s.redo(); s.redo()
    assert arr == [99, 30, 40]
