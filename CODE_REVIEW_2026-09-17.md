# Code review - PageWright (81911c0, 2026-09-17)

Facts: 9,954 lines of Python in src/ across 37 modules (core/ 14,
ui/ 20, model.py, main.py), plus 11 test files, 133 tests, all
passing locally (`python -m pytest tests/`, 17 s, 2 benign numpy
RuntimeWarnings from the intentional blank-image dewarp test). 79
commits. Five runtime dependencies, unchanged. PySide6 IS installed
in this environment, so unlike the 2026-08-11 review the UI smoke
test actually ran; the interactive GUI still was not driven by hand.

Verdict: still a healthy codebase, and the two structural debts the
last review called most urgent are FIXED - `ui/display.py` is
extracted (the backwards import edge is gone) and `PageEntry` is a
real class with `to_dict`/`from_dict`. The core/shell split remains
the project's best property. Since then the surface has grown by
~1,150 lines (Measure tool, project lifecycle, preferences, SVG
annotations, PDF-import fix, mark colors) without a matching
structural pass, so the debts now are: (1) a REAL, already-realized
data-loss bug - derived/imported pages live in temp dirs that are
never cleaned up and are referenced by absolute path in saved
projects; 4 of Robert's 7 sample projects are already broken;
(2) `main_window.py` grew 930 -> 1,159 lines and the pages
controller the last review asked for was never extracted;
(3) doc drift - README Status stops at Phase 7 and HANDOFF.md is
five weeks stale.

---

## 1. Temp-file page storage loses user work (HIGH - fix first)

This is the one finding that is not stylistic. Four code paths write
images to `tempfile.mkdtemp()`:

- `main_window.py:400`  `add_derived_pages`  (`pagewright_derived_`)
- `main_window.py:618`  `import_pdf`         (`pagewright_pdf_`)
- `main_window.py:661`  `paste_image`        (`pagewright_paste_`)
- `main_window.py:728`  `_on_dewarp_applied` (`pagewright_dewarp_`)

Nothing ever deletes these directories (no `rmtree`/
`TemporaryDirectory` anywhere in src/), and `PageEntry.source_path`
stores the absolute temp path, which `project_io` writes verbatim
into the `.tiproj.json`. So the project file's durability depends on
`%LOCALAPPDATA%\Temp` surviving - which it does not, across reboots
or disk cleanup.

Measured on the repo's own sample projects: 11 referenced page
images, **4 already missing**.

```
GONE  GaryPipSheath_flat.tiproj.json         -> ...\pagewright_dewarp__rgy638k\...
GONE  GaryPipSheath_flat08172026.tiproj.json -> ...\pagewright_derived_65r589lw\...
GONE  LeathercraftPattern_flat.tiproj.json   -> ...\pagewright_dewarp_7kyzddtu\...
GONE  WayfarerOvedrviewPlan-p001.tiproj.json -> ...\pagewright_pdf_k8wjyqfi\...
```

Two of those are single-page projects whose ONLY image is gone - the
project file is now unopenable except via the relocate prompt, and
there is nothing to relocate to. The flattened pixels were never
written anywhere else.

The code knows: `_on_dewarp_applied`'s docstring says "a future
revision should offer to save it somewhere permanent", and the status
bar tells the user "Temporary file - use Save/Export to keep it".
That warning is doing load-bearing work it cannot do - the user has
already been told the flatten was *applied*, and the Pages panel
shows a normal-looking page.

It also compounds: leaked dirs are never reclaimed. A 100-page PDF
import at 600 DPI leaves ~GBs in Temp with no owner.

Recommended fix, cheapest first:

1. **Sidecar assets dir on save.** When a project is saved to
   `<name>.tiproj.json`, copy any page whose `source_path` is under
   the temp root into `<name>_assets/` next to the project file and
   rewrite the entry. Makes saved projects self-contained. Do this
   in `project_controller.save_project`, not in `core/project_io`
   (core stays I/O-pure and GUI-free).
2. **Store page paths relative to the project file** when they sit
   beside it, so a moved/copied job folder still opens. Absolute
   paths for anything outside.
3. **One session temp dir**, created once and removed in
   `closeEvent`, instead of four ad-hoc `mkdtemp` calls - and skip
   the removal when any surviving page still points into it.
4. **Warn on save** if any page still references a temp path and the
   user declines copying.

Item 1 alone converts this from data loss to a recoverable state.

## 2. `activate_page` bypasses the missing-file handling (MEDIUM)

`project_controller._locate_image` exists precisely to prompt the
user when a saved image has moved, but it is called from exactly one
place - `load_project` (project_controller.py:123), for the project's
single `source_image_path`. Page switching does not use it:

```python
# main_window.py:510
loaded, pixmap = self.projects._read_image(path or "", "Open Page")
if loaded is None:
    return
```

So with finding #1, opening a multi-page project whose pages are gone
gives an error dialog per page and a silent `return` that leaves the
Pages panel pointing at a page that never loaded - no relocate
offer, no entry marked broken. Route page loads through
`_locate_image` too, and persist the relocated path back into the
`PageEntry`.

Note also the reach across boundaries: `main_window` calls
`self.projects._read_image(...)`, a private method of another
controller (same for `_locate_image` if reused). Promote both to
public names on `ProjectController` - they are already the de-facto
shared image-loading API.

## 3. `main_window.py` is still the coordinator that does everything (MEDIUM)

930 -> **1,159 lines, 76 methods**, against the last review's
explicit advice to extract a pages controller "before M3 doubles
that code". M3 landed; the code doubled.

The page cluster is intact and mechanically movable -
`_refresh_pages_panel`, `_store_current_page`, `add_derived_pages`,
`batch_flatten`, `_restore_page_roi`, `activate_page`,
`_append_pages`, `add_page_images`, `add_page_folder`,
`remove_page`, `import_pdf` (main_window.py:364-642) - roughly 280
lines, contiguous, and already the natural home for the fixes in
findings #1 and #2. The house pattern (`project_controller`,
`export_controller`, `editing_controller`, `overlay_controller`)
makes `pages_controller.py` a move, not a design question.

Do finding #1 *inside* that extraction rather than before it; the
temp-path logic belongs in the new controller.

`dewarp_stage.py` (1,693 lines, 5 classes, `DewarpStageWidget` alone
holding 45 methods) is the other monolith. The last review's
`display.py` split landed and was the valuable half. The remaining
`ui/dewarp_views.py` split (`_AutoFitView`, `_SourceView`,
`_ResultView` = ~690 lines) is still worth doing, and is now better
motivated: `ocr_stage.py` and `scale_stage.py` both import
`_ResultView` - and `scale_stage` also imports `MmSpinBox` - from
`dewarp_stage`, i.e. three modules import shared widgets through a
private, underscore-prefixed name from a sibling *stage*. That is
the same backwards edge `display.py` just fixed, one level down.

## 4. `FORMAT_VERSION` is a hard equality gate (LOW, but a trap)

```python
if version != FORMAT_VERSION:
    raise ProjectIOError("unsupported project version: %r" % (version,))
```

The schema has grown repeatedly (`pages`, `roi`, `measurements`,
`marks`) while `FORMAT_VERSION` stayed 1 - which is the right call,
since every addition was read with `.get()` defaults and old files
keep loading. But the moment anyone bumps it to 2, every existing
`.tiproj.json` becomes unopenable, with no migration path and an
error message that offers no recourse.

Make the intent explicit now, while the cost is three lines: accept
`version <= FORMAT_VERSION`, reject only *newer* files (with a
"saved by a newer PageWright" message). The tolerant-read/`.get()`
discipline already in `project_from_dict` is what actually provides
compatibility; the version gate should only catch the forward case.

## 5. Test suite: strong core, thin shell (LOW)

133 tests, genuinely good where they exist - `test_dewarp.py` pins
numerics against the vendored reference, and the M3 batch helpers
(`propagate_model`, `seed_and_refine`, `flatten_with_model`) each got
a test. Gaps worth closing, in order:

- **`core/ocr.py` has no test file at all** - the only untested core
  module. Even an `importorskip`-guarded test of
  `availability_error()` and the text-cleanup path would match how
  `pdf_import` is covered.
- **The smoke test does not switch tools.** The last review asked for
  a test that "switches all four tools"; `test_ui_smoke.py` checks
  fresh-window state and `_store_current_page`, but never calls
  `switch_tool`. Commit d78caab ("right-side panes vanished after
  hopping between non-trace tools") is exactly the bug that test
  would have caught. Add a loop over trace/flatten/ocr/scale
  asserting the stack index and that the dock panes stay visible -
  it is ~10 lines and PySide6 is available on this machine.
- **`tempfile.mktemp`** still in `test_project_io.py:52,113` and
  `test_pdf_import.py:26` (flagged last review, unfixed). Deprecated
  and racy; `mkstemp`/`tmp_path` is a two-line fix each.

## 6. What to preserve

Unchanged from last review and still true: the GUI-free numeric core,
the why-docstrings (`_read_image`'s note on double-decode and EXIF
mismatch, `batch.propagate_model`'s explanation of the uniform-scale
seed, `tiling.build_tiles`), the controller-per-concern layout, and
PLAN.md as a design record. One TODO comment in 9,954 lines is a
remarkable signal-to-noise ratio. Commit messages remain excellent.

Also still correct: the *absence* of a Tool/Stage abstract base
class, a plugin registry, DI, or a signal bus. Four concrete tools
and one author - keep resisting those.

---

## Roadmap status vs. HANDOFF.md and PLAN.md

HANDOFF.md (written 2026-08-13, tip `52cf15d`) is **five weeks and
~25 commits stale**; current tip is `81911c0` (2026-09-15). Its
"Priorities for next session" list is almost entirely done:

| HANDOFF priority | Status | Evidence |
|---|---|---|
| 1. Scale tool | **DONE** | `ui/scale_stage.py` (216 lines), in the tool switcher |
| 2. M1 Pages panel + folder load | **DONE** | `ui/pages_panel.py`, `PageEntry`, `add_page_folder` + `natural_key` |
| 3. M2 PDF import (PyMuPDF) | **DONE** | `core/pdf_import.py`, `ui/pdf_import_dialog.py`, crash fix aaf9407 |
| 4. M3 derived pages + batch | **DONE** (was already marked so) | `core/batch.py`, `batch_flatten` |
| 5. M4 searchable PDF export | **NOT STARTED** | no `TextWriter`/text-layer code anywhere in src/ |

Test count moved 113 -> 133 as claimed practice requires.

Work done since the HANDOFF was written, none of it in any plan doc
(all from git log): Measure tool + Ctrl angle snap (467fcaf),
measurement persistence (7cca063), project lifecycle / Save As / New
/ Open Recent (d2733cc), preferred units (55b6b07), SVG Annotations
layer (639fc4e), tile-mark color preferences (f9e0dc0), zoom-following
seed brush (ac0b03b).

**Still-open items explicitly deferred by the plans, all confirmed
absent in code:**

- M3 follow-ups: per-page outline persistence in the project file
  (nothing about `PageModel`/`SpreadModel` in `project_io.py` or
  `model.py` - refined models remain transient), batch flatten from
  plain quad corners, Pages-panel thumbnails (`pages_panel.py` is a
  plain list, no icons).
- Phase 6 remainder: folding object-create / Trace-Poly into undo
  (they still clear the stack).
- UX backlog: Trace Poly complexity control - the single TODO in the
  codebase, `editing_controller.py:110`.
- Smarter two-page detection (BookScan spine-dip port).
- OCR follow-ons: language selector, positioned regions.

**Doc drift to fix (cheap, do with the next commit):**

- README "Status" (line 182+) lists Phases 0-7 only. The Flatten,
  OCR and Scale tools and the entire multi-page M1-M3 feature set -
  the bulk of the last three months - are undocumented there. A
  reader of README believes this is still a single-image tracer.
- PLAN.md sec. 9 has no Phase 8, though sec. 13 says to add one "when
  P3 (dewarp stage inside PageWright) starts". It started and
  shipped.
- HANDOFF.md should be rewritten against `81911c0`: M4 is the single
  remaining roadmap milestone, and the temp-file issue (finding #1)
  should head the priority list ahead of it.

## Suggested order of work

1. Finding #1 (temp-file persistence) - it is losing real work today.
2. Extract `ui/pages_controller.py`, landing #1 and #2 inside it.
3. Doc refresh: README Status, PLAN Phase 8, rewritten HANDOFF.
4. Smoke test over the tool switcher; an `ocr` core test; kill
   `mktemp`.
5. `FORMAT_VERSION` comparison, then M4 (searchable PDF).
