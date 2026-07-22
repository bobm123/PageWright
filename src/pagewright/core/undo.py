"""A small generic command / undo-redo stack (Phase 6).

A command is anything with undo() and redo() methods. The convention is that a
command's effect is *already applied* when it is pushed (the user just did it),
so push() only records it; undo() reverts; redo() re-applies.

FnCommand wraps two callables so callers can express commands as closures
without defining a class per operation. The stack is pure Python and has no Qt
or model dependencies, so it is fully unit-testable.
"""


class Command:
    """Base class: subclasses implement undo() and redo()."""

    label = "edit"

    def undo(self):
        raise NotImplementedError

    def redo(self):
        raise NotImplementedError


class FnCommand(Command):
    """A command defined by an undo callable and a redo callable."""

    def __init__(self, label, undo, redo):
        self.label = label
        self._undo = undo
        self._redo = redo

    def undo(self):
        self._undo()

    def redo(self):
        self._redo()


class UndoStack:
    """LIFO undo/redo stack with a bounded history."""

    def __init__(self, capacity=200):
        self._done = []        # applied commands, oldest first
        self._undone = []      # undone commands available for redo
        self._capacity = max(1, int(capacity))
        self._listeners = []

    # ----- mutation --------------------------------------------------------

    def push(self, command):
        """Record an already-applied command and clear the redo history."""
        self._done.append(command)
        if len(self._done) > self._capacity:
            self._done.pop(0)
        self._undone = []
        self._notify()

    def undo(self):
        if not self._done:
            return False
        command = self._done.pop()
        command.undo()
        self._undone.append(command)
        self._notify()
        return True

    def redo(self):
        if not self._undone:
            return False
        command = self._undone.pop()
        command.redo()
        self._done.append(command)
        self._notify()
        return True

    def clear(self):
        had = bool(self._done or self._undone)
        self._done = []
        self._undone = []
        if had:
            self._notify()

    # ----- queries ---------------------------------------------------------

    def can_undo(self):
        return bool(self._done)

    def can_redo(self):
        return bool(self._undone)

    def undo_label(self):
        return self._done[-1].label if self._done else ""

    def redo_label(self):
        return self._undone[-1].label if self._undone else ""

    def __len__(self):
        return len(self._done)

    # ----- change notification ---------------------------------------------

    def on_change(self, callback):
        """Register a zero-arg callback fired whenever the stack changes."""
        self._listeners.append(callback)

    def _notify(self):
        for cb in self._listeners:
            cb()
