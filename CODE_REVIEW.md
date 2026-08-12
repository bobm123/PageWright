# Code review - PageWright (6acb937, 2026-08-11)

Facts: 8,806 lines of Python in src/ across 25 modules (core/ 8 files,
ui/ 15 files, model.py, main.py), plus 10 test files (~1,400 lines,
117 tests passing). Five runtime dependencies: opencv-python, numpy,
PySide6, pytesseract (optional at runtime), pymupdf (optional at
runtime). 56 commits, consistently well-written messages. Reviewed
statically plus by running the core test suite; the GUI cannot be
executed in the review environment, so all UI findings are
static-analysis-only.

Verdict: this is a healthy codebase with an unusually clean
core/shell split - the numeric core (dewarp, tiling, svg_export,
project_io) is pure, GUI-free, and genuinely tested, which is the
single most valuable property here and must be preserved. The top
debts, in order: (1) dewarp_stage.py is a 1,713-line monolith that
also exports general-purpose display helpers other modules import
from it; (2) the M1 pages[] list re-introduces the bare-dict domain
model this project's own history (BookScan's OutlineModel refactor)
argues against; (3) main_window.py (930 lines) is quietly accreting
page-management logic that belongs in a controller like its four
siblings. All three are cheap to fix now and expensive after M3.

## 1. Idiomatic Python?

Mostly yes. PEP 8 layout, module docstrings that explain *why* (the
best ones in export_controller._paint_tiles and tiling.build_tiles
read like postmortems - keep writing these), consistent naming, and
deliberate module boundaries. Real violations, priority order:

- Bare dicts as a domain model. project.pages entries are dicts with
  stringly keys "source_path", "objects", "roi". The "roi" key was
  bolted on after the fact (commit 400173a) with no single place
  documenting the schema - exactly how BookScan's outline dicts
  decayed before the OutlineModel dataclass fixed them. Fix: a
  PageEntry dataclass in model.py with to_dict/from_dict, mirroring
  how Contour/TracedObject already work. One evening, and it makes
  M3's derived-pages work type-checked instead of key-guessed.
- tempfile.mktemp in tests (test_project_io.py:51,112,
  test_pdf_import.py). mktemp is deprecated and racy; use
  tempfile.mkstemp or a tmp-dir helper. Harmless in practice here,
  but it will trip a future linter and is a two-line fix.
- Type hints are nearly absent (15 annotated functions out of ~450).
  Full annotation is NOT worth it for the Qt shell, but the core/
  package is pure functions over ndarrays and dataclasses - hints
  there are cheap and would document the ndarray dtype/shape
  contracts that currently live only in docstrings.
- Broad `except Exception` at UI boundaries (import_pdf, save paths).
  Acceptable where they are - a crash dialog beats a traceback in a
  GUI - but keep them out of core/; core currently raises cleanly,
  which is right.

Deliberate style choices that are fine as-is: %-formatting over
f-strings (consistent everywhere), function-local Qt imports in
controllers (keeps import-time light and optional deps optional),
ASCII-only source (enforced project convention).

## 2. Dependencies to eliminate

None. All five earn their keep: opencv and numpy ARE the core;
PySide6 is the shell; pytesseract and pymupdf are optional,
lazily imported, and guarded by availability_error() functions with
install hints - that pattern (core/ocr.py, core/pdf_import.py) is
exactly right and should be the template for any future optional
engine. pytesseract could in principle be replaced by a subprocess
call to tesseract, but it earns its ~1-file weight handling image
marshalling and parameter quoting; keep it.

## 3. Libraries that could replace custom code

Mostly the custom code should stay:

- SVG generation by string assembly (svg_export.py, tiling.py):
  keep. svgwrite/lxml would add a dependency to produce the same
  bytes with less control over the mm-exact viewBox output that the
  true-scale printing depends on. The string builder is tested.
- Polygon/spline math (core/dewarp.py): keep. It is verified
  bitwise against the BookScan reference implementation - a library
  swap would forfeit that baseline for zero functional gain.
- Sutherland-Hodgman/shapely for geometric tile clipping: do NOT add
  yet. The painter-level clip (4a4aa19) solved the visible bug; add
  shapely only if exported SVGs ever need per-tile clipped paths for
  a consumer that ignores clip-path the way QtSvg does.
- One genuine adoption candidate: pytest as a dev dependency, run
  for real on the dev machine (`pip install pytest; pytest tests/`).
  The tests are already pytest-idiomatic; the exec-based shim is a
  sandbox artifact, not a project design choice, and real pytest
  adds fixtures, -k selection, and better failure output for free.

## 4. Maintainability

What stops a competent newcomer today:

- dewarp_stage.py (1,713 lines) mixes three QGraphicsView subclasses,
  the stage widget, mode logic for quad/one-page/two-page, and -
  worst - the general display helpers display_downscale and
  ndarray_to_qpixmap that canvas.py, project_controller.py and
  main_window.py all import FROM the dewarp stage. Split:
  ui/display.py (the two helpers + PREVIEW constants),
  ui/dewarp_views.py (_AutoFitView, _SourceView, _ResultView),
  ui/dewarp_stage.py (the widget). The display.py extraction alone
  removes the weirdest import edge in the codebase and takes an hour.
- main_window.py (930 lines): the M1 page methods
  (_store_current_page, activate_page, _append_pages, add_page_*)
  are a controller trapped in the coordinator. The project already
  has the right house pattern - project_controller, export_controller,
  editing_controller, overlay_controller - so a pages_controller.py
  is a mechanical move. Do it before M3 doubles that code.
- The stage-widget protocol (set_image / cancelled / applied) is
  informal. That is fine at four tools - do not formalize it into a
  base class - but write it down in a short comment block in
  main_window so the next stage author copies the contract instead
  of reverse-engineering ocr_stage.
- UI is untested and currently untestable in CI. Cheapest win: one
  smoke test that sets QT_QPA_PLATFORM=offscreen, instantiates
  MainWindow, switches all four tools, and exits. It would have
  caught every "missing attribute after rename" class of bug at
  commit time on the dev machine.

Worth explicit preservation credit: PLAN.md and HANDOFF.md are the
best onboarding documents this reviewer has seen in a project this
size; the why-docstrings on every hard-won fix (print hang, square
image freeze, QtSvg clip-path) turn past debugging into permanent
institutional memory; the synthetic ground-truth tests in
test_dewarp.py pin numerics to the vendored reference. Also note:
dewarp/BookScan/TraceImage copies of the spline core are now
strictly historical - PageWright's core is canonical. Stop
backporting; add a line to those repos' READMEs saying so.

## 5. Architecture: functional vs OOP

The balance is close to textbook and mostly needs defending, not
changing: a functional core (pure ndarray transformations, PageModel/
SpreadModel dataclasses with no behavior beyond conversion/scaling)
under an OOP Qt shell with a thin coordinator and per-concern
controllers. Two corrections, both on the under-OOP side:

- pages[] entries: dataclass, as above (section 1).
- Tiling parameters travel as dicts through tiling_panel.params() /
  to_dict() with ~12 string keys duplicated across tiling_panel,
  export_controller and core/tiling call sites. A TilingParams
  dataclass (with to_dict/from_dict for the project file) would kill
  the whole key-typo bug class. Evidence this class of bug is live:
  the old-tiling-dict back-compat tests exist precisely because keys
  drift.

Nothing here is over-OOP; keep it that way (next section).

## 6. Design patterns worth employing (and avoiding)

Already present and correct: Command/undo (core/undo.py + edit
sink), Observer via Qt signals, the controller-per-concern layout,
and cached pre-parsed renderers (the flyweight lesson from the print
hang). The one pattern to ADD: extend the existing controller
pattern to pages (pages_controller.py) - concrete payoff is that M3
(derived pages, batch apply) lands in a 200-line controller instead
of pushing main_window past 1,200 lines.

Patterns that would HURT this project - do not add: an abstract
Tool/Stage base class or plugin registry (four concrete tools, one
author; YAGNI), dependency-injection ceremony, a signal bus
(direct Qt connections are traceable), or splitting core/ into a
separately-versioned package before a second consumer actually
exists (the Pyodide webapp idea is that consumer - split THEN).

## Performance notes (no action needed yet)

The three big lessons are already institutionalized: display proxies
capped at 2200 px, per-tile photo crops with bleed instead of
full-photo embeds, and parse-once renderer caching. Two watch items:
import_pdf renders pages serially under a wait cursor - a 100-page
PDF at 600 DPI will feel hung (a page-count * DPI sanity prompt or a
progress dialog is enough when M3 makes big imports common); and
_store_current_page serializes all objects on every page switch,
which is fine at tens of pages and worth profiling only if jobs
reach hundreds.

## First move if refactoring starts tomorrow

Extract ui/display.py from dewarp_stage.py (one hour, removes the
backwards import edge), then introduce the PageEntry dataclass while
the pages[] schema is three keys old. Both are prerequisites that
make M3 cheaper, neither risks the numeric core, and the 117-test
suite pins everything that matters during the move.
