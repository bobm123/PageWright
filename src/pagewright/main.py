"""Entry point: launch the PageWright Qt application.

From the project root, run the script directly:

    python src/pagewright/main.py
    python src/pagewright/main.py samples/IMG_4705cropped.png
    python src/pagewright/main.py my_trace.tiproj.json

_ensure_src_on_path() below puts src/ on sys.path, so no install is needed.

Alternatively install the package and use the console script:

    pip install -e .
    pagewright samples/IMG_4705cropped.png

Note: `python -m pagewright.main` does NOT work from the project root unless
src/ is already on sys.path (e.g. after `pip install -e .`, or with
PYTHONPATH=src). With -m, Python must import the `pagewright` package before
this module runs, so _ensure_src_on_path() is too late to help.
"""

import argparse
import os
import sys


def _ensure_src_on_path():
    """Allow `python src/pagewright/main.py` to run without installing."""
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(here)  # .../src
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def _parse_args(args):
    """Parse our own options; unknown args are passed through to Qt."""
    parser = argparse.ArgumentParser(
        prog="pagewright",
        description="Trace object outlines from a photo and export "
                    "true-scale SVG.")
    parser.add_argument(
        "file", nargs="?", metavar="FILE",
        help="photo to trace, or a .tiproj.json project to reopen")
    return parser.parse_known_args(args)


def main(argv=None):
    _ensure_src_on_path()

    argv = list(sys.argv if argv is None else argv)
    args, qt_args = _parse_args(argv[1:])

    from PySide6.QtWidgets import QApplication
    from pagewright.ui.main_window import MainWindow

    app = QApplication(argv[:1] + qt_args)
    app.setApplicationName("PageWright")
    window = MainWindow()
    window.show()
    if args.file:
        # After show() so any error dialog has a parent window on screen.
        window.open_path(args.file)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
