"""Entry point: the GUI, or a headless subcommand.

From the project root, run the script directly:

    python src/pagewright/main.py
    python src/pagewright/main.py samples/IMG_4705cropped.png
    python src/pagewright/main.py my_trace.tiproj.json

With a subcommand it runs headless instead (no Qt imported, so it works
with no display) - see cli.py:

    python src/pagewright/main.py info pattern.svg
    python src/pagewright/main.py tile pattern.svg --page A4 --out ./tiles

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
import logging
import os
import sys


def _ensure_src_on_path():
    """Allow `python src/pagewright/main.py` to run without installing."""
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(here)  # .../src
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def _parse_args(args):
    """Parse the GUI options; unknown args are passed through to Qt.

    Only reached when there is no subcommand - `cli.build_parser` owns
    the headless side, and its own --help lists both."""
    parser = argparse.ArgumentParser(
        prog="pagewright",
        description="Trace object outlines from a photo and export "
                    "true-scale SVG. Run a subcommand (info, tile) to "
                    "work headlessly.")
    parser.add_argument(
        "file", nargs="?", metavar="FILE",
        help="photo to trace, or a .tiproj.json project to reopen")
    parser.add_argument(
        "--debug", action="store_true",
        help="verbose diagnostics on stderr (also PAGEWRIGHT_DEBUG=1); "
             "includes printer page geometry for tiled-print issues")
    # The subcommands live in cli.py and are dispatched before this
    # parser runs, so list them here or --help hides half the program.
    from pagewright.cli import COMMANDS
    parser.epilog = (
        "headless subcommands: %s\n"
        "run 'pagewright COMMAND --help' for their options, e.g.\n"
        "  pagewright info pattern.svg\n"
        "  pagewright tile pattern.svg --page A4 --out ./tiles"
        % ", ".join(COMMANDS))
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser.parse_known_args(args)


def _setup_logging(debug):
    """Quiet by default; -debug/PAGEWRIGHT_DEBUG turns the dumps on."""
    if not debug and os.environ.get("PAGEWRIGHT_DEBUG", "") not in ("", "0"):
        debug = True
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")


def _wants_cli(rest):
    """True when the first non-flag argument names a subcommand.

    Checked BEFORE Qt is imported, so a headless run never needs a
    display - and before argparse, because the GUI parser would reject
    the subcommand as an unexpected positional."""
    from pagewright.cli import COMMANDS
    for token in rest:
        if not token.startswith("-"):
            return token in COMMANDS
        if token in ("-h", "--help"):
            return False
    return False


def main(argv=None):
    _ensure_src_on_path()

    argv = list(sys.argv if argv is None else argv)
    rest = argv[1:]

    if _wants_cli(rest):
        from pagewright.cli import run
        # --debug is shared, so honour it before handing over.
        _setup_logging("--debug" in rest)
        return run([a for a in rest if a != "--debug"])

    args, qt_args = _parse_args(rest)
    _setup_logging(args.debug)

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
