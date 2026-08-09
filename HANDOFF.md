# PageWright - Implementation Handoff

Written 2026-08-06 for the next working session (any model). Read this
plus PLAN.md sections 13-15 before coding. PLAN.md holds the designs;
this file holds state, priorities, and the working practices that kept
quality high.

## Current state

- Branch: `main` (feature/dewarp-core was merged fast-forward and
  deleted). Local tip: `52cf15d`. Robert pushes manually; check
  `git status -sb` for ahead-count before assuming origin is current.
- Everything through the "workflow hub" is DONE and committed:
  quad/one-page/two-page(spread w/ spine) flatten, calibration with
  unit parsing, tiling (repeats, % overlap, corner+midpoint 5mm
  diamonds, region tiling via Select Area, image-only jobs), print
  system (shared preview dialog, single-sheet nav, print ranges,
  Save-as-PDF, never-stretch true-mm painting), OCR basic stage,
  clipboard paste, tool switcher Trace|Flatten|OCR, R/L rotate keys,
  shared wheel-zoom (ui/zoom.py), context menus trimmed.
- Test suite: 113 tests, all passing, GUI-free (core only). Run:
  `python -m pytest tests/` on a machine with pytest, or see
  "Testing without pytest" below.
- The Qt/UI layer is STATIC-CHECKED ONLY when developed in the Cowork
  sandbox (no PySide6 there). Every UI change ships expecting a
  test-drive by Robert; keep changes reviewable.

## Priorities for next session (Robert's order of value)

### 1. Scale tool (smallest, do first) - PLAN sec. 14 "Scale as a
   first-class tool"
   - New `ui/scale_stage.py` (pattern: ocr_stage.py) added to the
     MainWindow stack + tool switcher (`switch_tool("scale")`,
     act_tool_scale).
   - Contents: image view (reuse `dewarp_stage._ResultView`),
     Calibrate button (arm two-point gesture - reuse the
     `_AutoFitView` calibration machinery, already in the base class),
     unit-aware Width/Height fields (`dewarp_stage.MmSpinBox`) that
     DISPLAY the image's real size from project calibration and, when
     edited, set `project.calibration.mm_per_pixel = value_mm /
     pixel_dim` (one scalar - editing W updates H display and vice
     versa), an output DPI spinbox, and buttons: Print Tiles...
     (`w.print_tiles()`), Save Scaled Image... (resample to
     mm-size*DPI via cv2.resize, cv2.imwrite).
   - Calibration set here must refresh trace-view readouts
     (`w._refresh_scale_readout()`, `w._update_tile_grid()`).

### 2. Multi-page M1: Pages panel + folder load - PLAN sec. 15
   - `model.py`: Job/Page-lite: add `pages` concept. Minimal M1:
     a `PageEntry` (source_path, kind="image", state dict) list on
     Project + current_page index; existing single-image fields become
     the current page's view. Keep project_io back-compatible (absent
     pages[] -> one-page job).
   - `ui/pages_panel.py`: dock section above Objects: thumbnail list
     (QListWidget, iconmode or small rows), checkboxes, double-click
     switches page (save current page state -> load target page via
     the existing load_photo worker path, but WITHOUT resetting the
     job). "Add Images..." (multi-select file dialog), "Add Folder..."
     (natural sort - implement `natural_key()` in core).
   - Keep per-page state minimal in M1: traces + calibration +
     tiling settings shared job-wide; page switch = image switch +
     per-page traces. Persist in .tiproj.json pages[].
### 3. M2: PDF import via PyMuPDF (`pip install pymupdf`) - page picker
   dialog (thumbnails, check pages, render DPI spin), rasterize to
   temp PNGs, enqueue as pages. Covers ideas.md #005.
### 4. M3: derived pages + batch apply (outline propagation - seed each
   page's outline from previous page's model, then refine; see PLAN
   sec. 13 book-spline reuse note). Re-enqueue outputs (flatten
   results, spread L/R pages, print tiles) as new pages.
### 5. M4 / P5: searchable PDF export (page images + OCR text layer;
   PyMuPDF can write the text layer).

### Smaller backlog (fit in when touched area is nearby)
- Trace Poly complexity control (TODO(ui) at epsilon_px in
  editing_controller.py; Coarse..Fine slider or point-count target).
- Flatten: offer permanent save of adopted flattened image (currently
  temp file; note in _on_dewarp_applied docstring).
- Smarter two-page detection (BookScan detect_boundaries port: spine
  dip cues) instead of split-at-mid seeding.
- OCR follow-ons: language selector, positioned regions/boxes.
- Alt+wheel page-flip in tile preview: verify on Windows (drivers may
  report Alt-ed wheel as horizontal delta; both axes are read).

## Working practices that matter (do not skip)

- **Verify, don't assume.** Every core port in this project was proven
  by NUMERIC BASELINE: capture hashes from the source implementation
  first, then require bitwise/1e-9 equality after the port (see
  tests/test_dewarp.py and the BookScan cross-checks). Keep doing this
  for any math that moves.
- **GUI-free core.** New logic goes in core/ (cv2+numpy only, C++-
  portable idioms) with pytest tests; ui/ stays a thin shell.
- **Static checks for UI** (when no Qt available): py_compile, AST scan
  for undefined self._* per class, dw.* name existence, no stray refs
  after refactors.
- **Big images are the norm** (18+ MP). Never hand Qt a full-res
  pixmap (use `display_downscale`), never embed the full photo per
  tile (per-tile crops), cache QSvgRenderers, cap preview rasters.
- **Scene coords are FULL-IMAGE pixels everywhere**; display proxies
  are scaled items. All overlay math relies on this.
- **Calibration invariant**: adopted flatten results are calibrated as
  mm_per_pixel = 25.4/output_dpi (exact in all sizing modes).

## Sandbox quirks (Cowork sessions only)

- The mounted folders BLOCK file deletion/unlink. Git index writes
  fail; commit via the /tmp recipe:
  1. `tar --exclude='./index*' -cf - -C .git . | tar -xf - -C /tmp/g`
  2. `GIT_DIR=/tmp/g GIT_WORK_TREE=$PWD git read-tree HEAD && git add
     <files> && git commit` (with -c user.name=Robert -c
     user.email=bobm123@gmail.com)
  3. copy back: `cp -rn /tmp/g/objects/. .git/objects/`, write ref
     file, `cp /tmp/g/index .git/index` (NEVER forget the index).
- A stale `.git/index.lock` may exist that only Robert can delete.
- pytest is unavailable; use the tiny shim (raises/approx/importorskip/
  skip/mark/fixture; approx needs __array_priority__=100000 and
  numpy allclose) on PYTHONPATH, with a loader that exec's each
  tests/test_*.py and calls test_* functions. /tmp is wiped between
  some calls - recreate the shim when ModuleNotFoundError: pytest.
- ASCII-only in new code/docs lines (existing files carry some legacy
  em-dashes/ellipses; don't add more except Qt menu "..." style).

## Conventions

- Commits: imperative subject, body explains WHY + evidence (measured
  numbers, test counts). Robert's identity: Robert <bobm123@gmail.com>.
- Every commit leaves the suite green; state the count in the body.
- Update PLAN.md when a design lands or changes; HANDOFF.md (this
  file) at session end.
