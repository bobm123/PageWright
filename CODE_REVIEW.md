# Code review — PageWright (cf4fb07, 2026-07)

**Facts.** 3,414 lines of Python across 18 source files (largest by far:
`ui/main_window.py` at 1,205 lines), plus 669 lines of tests in 8 modules.
Three runtime deps (`opencv-python`, `numpy`, `PySide6`); `pytest` for tests.
Clean two-layer split: a Qt-free `core/` (calibration, geometry, segmentation,
contours, svg_export, tiling, project_io, undo) and a PySide6 `ui/`. Good docs
(PLAN.md, PORTING.md, README.md).

**Verdict.** This is a well-structured, genuinely maintainable small app — the
functional-core / imperative-shell separation is real (the core imports no Qt),
the domain model is typed classes, undo is a proper Command stack, and the pure
core is well tested. The top debts are all in the shell, in priority order:
(1) `main_window.py` is a 1,200-line God object that should be split; (2) the
tiling settings are represented by **two different bare-dict schemas**, a live
bug magnet; (3) a little dead code (`TilingDialog`, `_EPS`, duplicated
`_MIN_VERTICES`). None of these are urgent, but #1 and #2 will bite as the app
grows. No dependencies to add or remove.

---

## 1. Idiomatic Python?

Mostly yes, with two deliberate, notable deviations:

- **`%`-formatting everywhere, zero f-strings** (0 f-strings, ~confirmed). This
  is *intentional* — PLAN/PORTING call out a future C++ port, and `"%.3f" % x`
  maps to `printf`/`ostringstream` more directly than f-strings. Defensible;
  keep it, but it's worth a one-line comment at the top of `svg_export`/`tiling`
  so a newcomer doesn't "modernize" it. Not a violation.
- **No type hints anywhere** (0 `->` annotations in `src/`). For a 3.4k-line
  project with a pure numeric core, adding hints to the core signatures
  (`geometry`, `calibration`, `svg_export.build_content`, `tiling.build_tiles`)
  would be cheap and would document the contracts the port depends on. Low
  priority, high readability payoff. Not required.

Genuinely idiomatic wins: context managers for file I/O (`project_io`),
comprehensions used tastefully, `@property` for read-only contour role/closed,
generators (`ObjectLayer.iter_points`). No stringly-typed control flow, no
hand-rolled deep copies, no silent `except: pass`.

## 2. Dependencies to eliminate

None. The three deps are all load-bearing:
- `PySide6` — the GUI, unavoidable.
- `opencv-python` — GrabCut, findContours, approxPolyDP, imencode/resize. Not
  replaceable by stdlib.
- `numpy` — used directly in `segmentation.py` (`np.full`, `np.zeros`,
  `astype`, boolean-mask ops). cv2 pulls it transitively, but you use it in your
  own code, so keeping it explicit in `requirements.txt` is correct.

This is a lean dependency set for what the app does. Do **not** add a JSON lib,
an SVG lib, or a base64 lib — the hand-rolled versions here are small, correct,
tested, and (per PORTING.md) intentionally port-friendly. Resisting those is the
right call.

## 3. Libraries that could replace custom code

Nothing worth swapping. Candidates considered and rejected:
- SVG generation via `lxml`/`svgwrite`: the current string-builder in
  `svg_export.py` is ~100 lines, tested, and produces smaller output. A library
  would add a dep and *reduce* portability. Custom wins.
- Base64/JSON: stdlib already (`base64`, `json`). Fine.

## 4. Maintainability

Priority order:

1. **`ui/main_window.py` is a God object (1,205 lines).** It builds the menus,
   toolbar, the objects dock, the tiling panel, *and* owns every handler: photo
   load, calibration, segmentation, object CRUD, undo dispatch, the edit-sink,
   bbox/overlay refresh, and all export logic. A newcomer has to read the whole
   file to change anything. Concrete split (names): move the side-panel widgets
   + their handlers into `ui/objects_panel.py` (the Objects list) and
   `ui/tiling_panel.py` (the Tiling group + `_tiling_params`/capture/apply);
   move export orchestration into `ui/export_actions.py` or a small
   `ExportController`. Target: `main_window.py` under ~500 lines that wires
   panels together. This is the single highest-value refactor.

2. **Two schemas for one concept — the tiling dict.** `model.default_tiling()`
   uses keys `scale_percent / embed_photo / crop_photo / filled`, while the live
   `MainWindow._tiling_params()` returns `scale / embed / crop / filled` (scale
   as a *fraction* vs *percent*). `_capture_tiling_to_project` /
   `_apply_tiling_to_panel` hand-translate between them. Two representations of
   the same data, translated by hand in three places, is exactly where a typo or
   unit mix-up (fraction vs percent) will silently ship. Fix: a single
   `TilingSettings` dataclass (see §5) used by the model, the panel, and
   `build_tiles`; JSON (de)serialization becomes `asdict`/`**d`.

3. **Dead code.** `ui/dialogs.py::TilingDialog` (~55 lines) is no longer
   referenced (export moved to the side panel); `tiling.py::_EPS` is unused
   after the registration-mark refactor; `_MIN_VERTICES = 3` is defined twice
   (`editable._MIN_VERTICES` and `MainWindow._MIN_VERTICES`) — if one changes
   they diverge. Delete `TilingDialog` and `_EPS`; import the single
   `_MIN_VERTICES` from `editable`.

4. **`project.dpi` plumbing is effectively dead.** `image_io.load_image` never
   sets DPI (cv2 doesn't read it), so `_base_mm_per_pixel`'s `self.project.dpi or
   96.0` always falls to 96. Either wire DPI from the file (Pillow/EXIF on load)
   or drop the `dpi` field and comment that uncalibrated == 96 DPI assumed. Right
   now it reads like it does something it doesn't.

5. **Tests: strong core, zero shell.** The pure core is well covered
   (calibration, geometry, contours, segmentation, svg_export, tiling, undo,
   project_io — real assertions, not smoke). The four largest UI files
   (`main_window`, `canvas`, `editable`, `objects`) have no automated tests —
   understandable (needs a display), but it means the 1,205-line file is
   entirely manually verified. Splitting out the panels (§1) would let you unit-
   test their pure helpers (`_tiling_params`, the group-delete index math) with a
   `QApplication` fixture or by extracting the math from the Qt objects.

Preserve deliberately: the docstrings that explain *why* (editable.py's
primitive/interactive split, the top-left/y-down coordinate convention repeated
across modules, the "ports cleanly" notes), and PLAN/PORTING.md — these are the
project's memory and are unusually good for a tool this size.

## 5. Architecture: functional vs OOP

The balance is already close to the textbook ideal — a pure functional core and
an OOP shell. One correction:

- **Make the tiling settings a record, not a dict.** Everything else in the
  domain model is a class (`Project`, `TracedObject`, `Contour`, `Style`,
  `Calibration`), but tiling is a bare dict — the one inconsistency, and the
  source of the dual-schema issue in §4.2. A `@dataclass TilingSettings` (fields:
  `page: str, landscape: bool, margin_mm, overlap_mm, scale, embed_photo,
  crop_photo, filled`) kills the magic strings and the fraction/percent
  ambiguity, and slots straight into JSON via `dataclasses.asdict`. This is the
  "typed records for domain data" prescription, and it's the only place the model
  needs it.
- Everything else is right-sized: core functions are pure and stateless; UI
  classes live at module scope (not nested in functions); state mutates through
  few points (the undo stack, `_objects`, the canvas scene). Don't add statefulness
  to the core.

## 6. Design patterns worth employing (and avoiding)

**Add:**
- **Fold structural ops into the Command stack (Memento).** Today `run_segmentation`
  and object-delete *clear* the undo stack because they replace contour objects
  the closures reference. With the model already snapshot-able (`ObjectLayer.
  to_model()` / `_build_layer_from_model` exist), a "replace object contours"
  and "add object" command is ~20-30 lines each and removes the two "history
  resets" — the last rough edge in the otherwise-complete undo system.
- **A tiny `Protocol` for the edit sink.** `EditableContour` duck-calls
  `sink.record_move/record_insert/record_delete`; a `typing.Protocol EditSink`
  documents that contract and lets a type checker catch a signature drift. Cheap,
  optional.

**Avoid (correctly absent — keep them absent):**
- No ABC hierarchy for the four shapes / roles — `role` as a string/enum is fine
  at this scale.
- No plugin system, DI framework, or event bus. This is a single concrete tool;
  that ceremony would be pure cost. The current directness is a feature.

## Performance notes (no action needed yet)

- GrabCut already downscales to an 800px working image and maps the mask back
  (`segmentation.grabcut_from_strokes`), with a wait cursor — the one real hotspot
  is handled, and perceived performance (status messages, cursor) is good.
- Embedded full-res photos make large base64 SVGs; the downscale + new crop
  options mitigate this well. Fine.
- `_refresh_object_list` rebuilds all row widgets on every change — O(objects),
  negligible for the handful of objects in practice. Leave it.

## Recommended first move

If refactoring starts tomorrow: introduce the `TilingSettings` dataclass (§5) —
it's small, it removes a whole bug class, and it makes the subsequent
`main_window.py` split (§1) cleaner because the tiling panel then has one typed
object to read/write instead of a hand-translated dict. Then extract
`ui/tiling_panel.py` and `ui/objects_panel.py`. Delete the dead code (§4.3)
along the way.
