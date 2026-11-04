# PageWright — Project Plan

A desktop GUI tool for tracing the outlines of objects in photographs and exporting
**true-scale SVG** files, with ruler-based calibration and tiled large-format printing.

> **Project location:** `D:\Projects\ClaudeSessions\PageWright`
> **Python package:** `pagewright`
> **Origin:** began as `TraceImage`; copied and rebranded into this repo
> on 2026-07-22 to serve as the unified studio shell (see section 13).
> **Status:** Phases 0-7 complete (trace/calibrate/SVG/tiling/projects/undo,
> carried over from TraceImage). Studio work starts at P3 in section 13.

---

## 1. Goal

Given a photograph of a roughly flat object (optionally pre-corrected by the sibling
`dewarp` app), let the user:

1. Calibrate real-world scale by measuring a known feature (e.g. a ruler) in the image.
2. Trace one or more object outlines, starting from a click-inside / click-outside
   automatic guess and refining by hand.
3. Export a vector SVG sized to the object's *real* dimensions (in millimetres), with
   the source photo optionally embedded as a background layer.
4. Print that SVG at **1:1 scale** across multiple standard printer pages, with
   registration marks so the tiles can be assembled accurately.

---

## 2. Technology choices and rationale

A future port to C or C++ is anticipated, so every major dependency is a C/C++ library
accessed through a thin Python binding. The Python code should translate with little
more than syntax changes.

| Concern | Library | Why it ports cleanly |
|---|---|---|
| Image processing | **OpenCV** (`cv2`) | C++ library; Python calls map ~1:1 onto the C++ API. |
| GUI | **Qt** via **PySide6** | Qt is C++; PySide6 mirrors the C++ API. LGPL license. |
| Numerics | **NumPy** | Pixel data interops with `cv::Mat`; keep idioms simple for porting. |

**Deliberately avoided in core logic:** scikit-image, Pillow-specific tricks, and clever
NumPy broadcasting — these are Python-only and would not survive a port. Geometry, SVG
generation, and tiling are plain arithmetic and port trivially.

---

## 3. Interactive segmentation approach

The source image is assumed fairly high-contrast, but glare and shadow may degrade parts
of the boundary, so the workflow is automatic-first, manual-always-available:

1. User paints/clicks **foreground (inside)** and **background (outside)** seeds.
2. Seeds initialise a GrabCut mask (`GC_FGD` / `GC_BGD` / probable variants for the rest).
3. **GrabCut** runs and returns a binary mask. (Watershed is kept as an alternate engine.)
4. `findContours` with `RETR_CCOMP` extracts the outer boundary **and interior holes**
   (hierarchy preserved).
5. `approxPolyDP` (Douglas–Peucker) simplifies each contour to an editable polygon.
6. User refines: add/move/delete vertices, add more seeds and re-run on problem areas,
   and (later feature) fit smooth Bézier curves.

This is exactly what handles "a wall with windows" or letters like **O, P, B** — an outer
loop plus one or more interior loops — and "two paths next to each other" as multiple
disjoint contours under one object.

---

## 4. Data model

All geometry is stored in **pixel coordinates** (the source of truth, matching the photo)
and converted to millimetres only at export time.

```
Project
  source_image: path, pixel_width, pixel_height, (optional DPI)
  calibration: mm_per_pixel, display_unit (mm | cm | in)
  margin_mm: float
  objects: [TracedObject]

TracedObject
  name / label
  contours: [Contour]          # 1 outer + 0..n holes, or several disjoint loops
  style: stroke / fill settings

Contour
  points: [(x_px, y_px)]       # ordered
  closed: bool
  role: outer | hole
  representation: polyline | bezier
  bezier_handles: optional, per-point
```

Coordinate convention: origin top-left, **y increases downward** — consistent across the
photo, Qt's scene, and SVG, so no axis flipping is needed.

---

## 5. SVG output specification

- Root `<svg>` with `width="{W}mm"`, `height="{H}mm"`, and a `viewBox` whose numbers equal
  the millimetre extents, so the file opens and prints at true physical size.
- `W`, `H` = object bounding box (across all contours) **plus the configured margin**,
  multiplied by `mm_per_pixel`.
- **Background layer** `<g id="photo">` — the source photo base64-embedded in an `<image>`,
  positioned and scaled to register with the vector layer. Toggling the layer's visibility
  (or omitting the group) gives the "with / without photo" option. An option to downscale
  the embedded copy keeps file size reasonable.
- **Vector layer** `<g id="trace">` — one `<path>` per object using compound subpaths
  (`M … Z` for the outer loop, `M … Z` per hole) with `fill-rule="evenodd"` so holes
  render as holes. Default style: stroked outline, no fill; filled mode available.

---

## 6. Calibration

- User clicks two points on a known feature; the pixel distance `d_px` is measured.
- User enters the real distance and unit; it is normalised to mm.
- `mm_per_pixel = d_real_mm / d_px`.
- The current scale and the resulting real image size are shown; re-calibration is allowed.
- Unit conversions for display: cm = mm / 10, in = mm / 25.4. Internal math stays in mm.

The single-scale model assumes a flat subject shot straight-on. Perspective/lens
correction is delegated to the upstream `dewarp` app and intentionally **not** duplicated.

---

## 7. Tiled printing specification

Inputs: master SVG real size (W×H mm), page size (Letter 215.9×279.4 mm or A4 210×297 mm),
orientation (portrait/landscape), printable margin, and overlap.

1. Printable area per page = page size − printer margins.
2. Tile step = printable area − overlap.
3. Tile counts = `ceil((dimension − overlap) / step)` in each axis.
4. For each tile, emit a page-sized SVG containing the correct slice of the master content,
   plus:
   - a **rectangle** outlining this tile's live (non-overlap) area;
   - a **page number / grid label** (e.g. `R2-C3`);
   - **diamond registration marks** placed along each edge *within the overlap band*, at
     positions fixed in master coordinates so adjacent tiles' diamonds coincide when the
     sheets are overlapped;
   - the photo background included or omitted per the toggle.
5. Output: one SVG per tile (`tile_r1_c1.svg`, …); the OS / printer driver handles printing.

> Note: true 1:1 output depends on printing with "fit to page" / auto-scaling **off**.
> The registration diamonds double as a scale check — if they don't line up at the stated
> overlap, scaling was applied somewhere.

---

## 8. Module / file layout

```
PageWright/
  README.md
  PLAN.md
  requirements.txt
  src/pagewright/
    __init__.py
    main.py              # entry point, launches the Qt app
    model.py             # Project / TracedObject / Contour
    core/
      image_io.py        # load image, pixel dims, DPI
      calibration.py     # two-point scale, unit conversion
      segmentation.py    # GrabCut / Watershed wrappers, seed mask
      contours.py        # findContours + hierarchy, approxPolyDP, smoothing
      geometry.py        # bounding box, margin, pixel<->mm transforms
      svg_export.py      # SVG builder, embedded photo layer, compound paths
      tiling.py          # page tiling, registration marks, per-tile SVG
    ui/
      main_window.py     # window, toolbars, menus
      canvas.py          # QGraphicsView/Scene: photo + editable overlays
      dialogs.py         # calibration, tiling/print, units, export
  tests/
  samples/
  output/                # generated SVGs (git-ignored)
```

The UI uses `QGraphicsScene`/`QGraphicsView` for smooth zoom/pan over a large photo, with
the photo as a background pixmap item and contours as editable polygon items with
draggable vertex handles.

---

## 9. Milestones

- **Phase 0 — Scaffold:** repo, dependencies, app skeleton that loads a photo with
  zoom/pan.
- **Phase 1 — Calibration:** two-point measurement, unit selection, live scale + real-size
  readout.
- **Phase 2 — Segmentation:** seed marking, GrabCut, contour extraction + simplification,
  editable polygons, refine loop (re-seed, add/move/delete vertices).
- **Phase 3 — Object model:** multiple contours with holes and disjoint pieces; bounding
  box + margin UI.
- **Phase 4 — SVG export:** mm-scaled output, embedded photo layer toggle, compound paths.
- **Phase 5 — Tiled printing:** page size/orientation/overlap, per-tile rectangle, page
  number, registration diamonds, photo on/off.
- **Phase 6 — Polish:** ✅ save/load project (JSON). ✅ command/undo-redo stack
  (`core/undo.py`) covering vertex move/add/delete and object delete; Ctrl+Z/Y
  dispatch by mode (seed strokes while seeding, edit commands otherwise). ✅ marquee
  (rubber-band) multi-vertex selection in Edit Vertices mode with group deletion via
  Delete (one undo step, respecting the per-contour minimum). Remaining: folding
  object-create / Trace-Poly into the undo history (currently those clear the stack).
  (Bézier smoothing deprioritized in favor of the Inkscape export flavor, which lets
  users adjust control points in Inkscape directly.)
- **Phase 7 — Port readiness:** ✅ audited `core/` for Python-only idioms (none found —
  only library bindings need C++ equivalents: NumPy→cv::Mat, json→a JSON lib, base64
  helper, closures→std::function) and sketched the C++/Qt structure. See
  [`PORTING.md`](PORTING.md).

---

## 10. Risks and mitigations

- **GrabCut struggles on glare/shadow** → mitigated by the re-seed loop and full manual
  vertex editing; Watershed available as an alternate engine.
- **Printer auto-scaling breaks 1:1** → mm-based SVG + visible registration diamonds let
  the user detect and correct it.
- **Large embedded photos bloat the SVG** → option to downscale the embedded background
  copy without affecting the vector geometry.
- **NumPy/Python idioms leaking into core logic** → code review against the "ports cleanly"
  rule; keep heavy lifting in OpenCV calls.

---

## 11. Dependencies

```
opencv-python>=4.8
numpy>=1.24
PySide6>=6.6
```

---

## 12. Handoff notes (for the Cowork / Code session)

- Confirm the stack imports before coding:
  `python -c "import cv2, numpy, PySide6"`
- Begin at **Phase 0 → Phase 1**: app skeleton with photo load + zoom/pan, then the
  two-point calibration, since every downstream feature depends on `mm_per_pixel`.
- The sibling `dewarp` app (also under `D:\Projects`) handles perspective/flattening
  upstream; do **not** duplicate that here. If its calibration UI or conventions should be
  reused, open this project in the same context as `dewarp` so its code is visible.

---

## 13. Roadmap: unified capture-to-OCR studio (mirrored from dewarp/CLAUDE.md, 2026-07-13)

Long-term goal (Robert): ONE utility that does what `dewarp`, `BookScan`
(`D:\Projects\ClaudeSessions\BookScan`) and PageWright (this project) do,
plus OCR, with a consistent UI. Target workflow, end to end:

    dewarp a book's pages (flat or curved)
      -> size and scale them (calibration, mm/DPI)
      -> perform OCR and capture patterns (text out, figures traced)

Evidence this is the natural shape: PageWright's own sample projects
(`IMG_*_dewarp.tiproj.json`) already consume dewarp's output - the pipeline
exists today as manual file handoffs between three apps.

What each piece brings:
- **dewarp**: quad + single-page spline dewarp, mm/DPI output sizing,
  input-DPI awareness (tkinter, `lib/` modules)
- **BookScan** `book_dewarp.py`: two-page spread detection (silhouette/
  text/hybrid strategies), text-line model refinement, raster refinement,
  synthetic ground-truth test harness (tkinter)
- **PageWright (formerly TraceImage)**: most mature UI of the three
  (PySide6/Qt), ruler calibration, GrabCut segmentation + editable polygon
  tracing, true-scale SVG export, tiled 1:1 printing, `.tiproj.json`
  project files, undo/redo command stack. This repo IS that codebase,
  copied and rebranded to be the studio shell.
- **NEW**: OCR stage (tesseract via `pytesseract` is the obvious engine;
  BookScan already used tesseract as a quality oracle)

### Decisions already made (Robert, 2026-07-13)

1. **UI toolkit: PageWright is the studio shell.** PageWright began as
   the TraceImage codebase (PySide6/Qt) and was copied + rebranded into
   this repo (2026-07-22) to be the studio. dewarp/BookScan features get
   ported in as pipeline stages; the tk UIs retire gradually.
   **Hard requirement:** keep the Left-Right pane structure (original
   photo left, live dewarped result right) for the dewarp stage once it's
   ported in - that layout is what makes interactive dewarping work in the
   source apps. (Rebranding pass: DONE - TraceImage -> PageWright.)
2. **Project file**: extend `.tiproj.json` (see `model.py` / `Project` in
   section 4 above) to hold the whole pipeline - source photo, page
   outline models, dewarp results, calibration, traces, OCR text - so a
   job is resumable at any stage. This is additive to the existing schema,
   not a breaking change.
3. **Shared core**: dewarp's and BookScan's numeric cores are already
   GUI-free and proven portable (`book_model.py` vendoring worked); unify
   them as one library package this project imports, alongside
   `core/` here - stop maintaining two/three copies of the spline/dewarp
   math.
4. **Webapp story**: PySide6 will not run in a browser; a separate Pyodide
   webapp (currently prototyped from dewarp, `feature/webapp` branch)
   stays a thin front-end over the same shared core - not this Qt app.

### Suggested phasing (each step useful on its own)

- **P1** merge dewarp's `feature/book-spline`; BookScan refactor debts
  that make code sharing clean (`OutlineModel` dataclass, module split)
- **P2** unified core library (spline model, detection strategies, dewarp,
  refinement) consumed by BOTH tk apps + this project
- **P3** dewarp stage inside PageWright (open photo -> page outline ->
  dewarped image becomes the working image for calibration/trace) -
  this is the first phase that actually touches this codebase
- **P4** OCR stage (`pytesseract`): per-page text export, region OCR;
  "capture patterns" = existing trace tools here applied to
  figures/diagrams
- **P5** batch/book mode: many photos -> ordered pages -> searchable PDF
  (page images + OCR text layer)

Book-spline idea for batch mode: when working through an entire book, the
spline curves are likely similar from one page to the next (same binding,
similar opening angle). Seed each new page's outline from the previous
page's model (per side - left and right pages of a spread differ from
each other but recur), then run only the cheap text/silhouette refinement
instead of detection from scratch. Faster, and keeps output geometry
consistent across the book. Possible extension: fit a per-book prior
(average model + small per-page correction) once a few pages are
confirmed.

### What this means for PageWright's own roadmap (sections 9-10 above)

Phases 0-7 above stand as-is and are not blocked by the studio plan -
they're the UI and export machinery the studio will build on. P3 (dewarp
stage inside PageWright) is the first studio phase that lands in this
repo; when it starts, add it here as **Phase 8** and update the Layout
(section 8) and README Status accordingly. Until then, this section is a
pointer, not new work in flight.

Full detail and session-handoff notes for the dewarp/BookScan side of this
plan live in `dewarp/CLAUDE.md` (see "Roadmap: unified capture-to-OCR
studio" and the "Session Handoff" section below it) and
`BookScan/HANDOFF.md`.

---

## 14. UX backlog

- **Trace Poly: complexity / point-count control.** The traced polygon's
  vertex count is set by the `approxPolyDP` tolerance `epsilon_px` in
  `ui/editing_controller.py` (auto-derived from region size). Add a user
  control (Coarse..Fine slider or a target point count) so the smoothness
  vs fidelity trade-off is adjustable per object without re-seeding. See
  the `TODO(ui)` at the `epsilon_px` line.
- **Dewarp stage follow-ups.** The Flatten Page dialog (`ui/dewarp_stage.py`)
  currently writes the flattened image to a temp file and adopts it as the
  working image; offer to save it permanently (and/or keep the original).
  Add the curved-page spline mode (the mode selector reserves a slot) using
  `core.dewarp.PageModel` / `dewarp_page` / `refine_with_text`.
- **Workflow hub (DONE).** Trace / Flatten / OCR are co-equal tools in a
  toolbar switcher over one shared working image, with Print Tiles one
  click away; loading no longer force-enters Flatten. Paste Image
  (Ctrl+V) starts a job from the clipboard. Context menus trimmed per
  window. OCR is the P4 basic version (pytesseract; whole image or the
  Select Area; copy/save text) - follow-ons: language choice, positioned
  text regions, searchable-PDF output (P5).
- **Scale as a first-class tool (PLANNED).** Scaling may be the ONLY
  thing a document needs: load -> calibrate -> set true size -> print or
  save. The pieces exist but are scattered (calibrate in Trace/Flatten,
  scale % in the tiling panel, mm sizes in the Flatten spinners). Add a
  SCALE tool to the hub switcher: the image with the two-point calibrate
  gesture front and center, an editable real-size readout (unit-aware
  Width/Height fields driving one mm/px scalar, so aspect is inherently
  true), an output DPI choice, and direct Print Tiles... / Save Scaled
  Image... buttons. Internally it is a thin stage over the existing
  calibration + tiling/print machinery - no new math.
- **Two-page (book spread) dewarp mode.** DONE: Book - Two Page mode
  uses a full SpreadModel (BookScan port, bitwise-verified): 6 anchors
  incl. spine_top/spine_bot, 4 corner-to-spine edges with draggable
  spine tangent handles; each page dewarps independently and Apply
  offers Left / Right / Both. Outline JSON interchanges with BookScan's
  *_points.json. Possible follow-ons: BookScan's smarter spread
  detection (spine-dip cues) instead of split-at-mid seeding; batch
  book mode (seed each page from the previous outline, roadmap P5).
  The richer two-page core already exists in BookScan
  (`book_dewarp.py` OutlineModel: 6 anchors incl. spine_top/spine_bot +
  4 edges) - the studio plan (section 13, P1/P2) is to unify that with the
  ported single-page `core.dewarp` so PageWright can consume it. Until then
  the V-fold break is the workaround.


---

- Polygon trace color from the context menu: right-click a polygon
  (or the active object) and pick its stroke color. The model already
  stores a per-object Style.stroke, so this is UI only - a color
  submenu or QColorDialog wired to the active TracedObject, refresh
  canvas + SVG export picks it up for free. (Robert, 2026-08-11)

- Themes vs mark colors (noted 2026-09-15): today, opening a project
  that carries custom tile-mark colors silently overrides the current
  session style (Robert approved; Preferences > Reset to default is
  the way back). IF the app ever grows THEMES (light / dark / follow
  system), that silent override starts fighting the theme - so at
  that point, replace it with a small "Load custom color scheme?"
  dialog shown only when opening a project WOULD change the current
  color selections (offer: use the project's colors / keep mine).
  Until themes exist, the silent override is the right simplicity.

## 15. Multi-page jobs (design sketch, not yet built)

Scenarios to support: a directory of book/magazine scan images; a
multi-page PDF with only some pages worth processing; a single large
image (or PDF page) that gets TILED and whose output pages then need
per-page post-processing (some OCR, some tracing).

### Concept: Job = ordered list of Pages

- A **Page** owns: a source reference (image file / PDF file + page
  number + render DPI / clipboard capture / DERIVED from another page,
  e.g. a tile or a flattened result), the working image, and per-page
  state: outline model (page or spread), traces, OCR text, calibration
  (inheritable job-wide since a scan session usually has one scale),
  and status flags (flattened? traced? OCR'd? printed?).
- The current single-image app is the degenerate one-page job; all
  existing tools keep operating on the CURRENT page unchanged.

### Sources

- **Folder of images**: natural-sort file names -> pages.
- **Multi-page PDF**: rasterize via PyMuPDF (fitz) at a chosen DPI;
  a page-picker dialog selects which pages become job pages (covers
  idea #005). PDF vector content is rasterized - this stays an
  image-processing app.
- **Derived pages**: any tool output can be RE-ENQUEUED as a new page
  (the flattened image, each page of a dewarped spread, each print
  tile). That is the 'multiple pages out, post-process some' loop:
  tile a large sheet -> outputs become pages -> OCR page 3, trace
  page 5.

### UI

- A **Pages panel** (dock, thumbnail filmstrip): current page
  highlighted, checkboxes for batch selection, reorder by drag,
  add/remove. Double-click switches the working image; every stage
  (Trace/Flatten/OCR/Print) follows the current page.
- **Batch actions** on the checked pages: apply the current flatten
  outline (seeding each page's outline from the previous page's, the
  book-spline reuse idea from section 13/P5), batch flatten, batch
  OCR, batch export/tiles.

### Persistence

- Extend .tiproj.json (section 13 decision #2 anticipated this):
  top-level pages[] with per-page source ref + state; the current
  single-page schema loads as a one-page job. Jobs are resumable at
  any stage.

### Outputs

- Per-page exports with numbered naming (<job>-p03-flat.png, ...).
- Combined **searchable PDF** (page images + OCR text layer) as the
  book-scan endgame = roadmap P5.

### Phasing (each useful alone)

- **M1** Pages panel + folder-of-images loading + per-page state in the
  project file (single-page tools untouched).
- **M2** PDF import (PyMuPDF) with page picker + render-DPI choice.
- **M3** Derived pages (re-enqueue flatten/spread/tile outputs) +
  batch apply with outline propagation. DONE 2026-08-11 (core/batch.py,
  tiling.tile_crop_rects, Flatten Checked, Add as Pages; outline
  persistence + quad-seed batch + thumbnails deferred).
- **M4** Searchable-PDF output (P5 complete).

---

## 16. CLI tiers and the VectorCraft comparison (2026-10-16)

Two related findings, pinned here so they survive the session.

### 16.1 Tiered plan for command-line coverage

Prompted by ArtCraft exposing every GUI action on the command line.
The obstacle in PageWright was never Qt coupling - `core/` has zero Qt
imports and a full job (SVG import -> tile -> export -> save) runs with
no Qt modules loaded. The obstacle is that MainWindow methods fuse
validate / ask-options / ask-path / compute into one method. Of ~90
MainWindow methods only 10 touch a dialog.

- **Tier 1 - job layer + subcommands. COMPLETE 2026-10-22.**
  `core/jobs.py` (Qt-free by contract, asserted by a test in a clean
  interpreter) plus `cli.py` with all six commands: `info`, `tile`,
  `export-svg`, `import-svg`, `flatten`, `ocr`. `main.py` dispatches
  before importing PySide6. Scale for raster inputs comes from
  `--dpi` / `--mm-per-px` / `--width` / `--height`; SVG and PDF carry
  their own. `flatten` is the one that earns the whole tier: page
  detection needs no human, so a folder of book photographs is
  straightened unattended, seeding each page from the previous one.
  Finishing it turned up a real bug in `core/batch.py` -
  `seed_and_refine` returned `(model, ok)` on success but a bare model
  on failure, so `flatten_with_model` died with "tuple object has no
  attribute edges". That broke the GUI's Flatten Checked too, which
  matches HANDOFF's note that batch flatten was never test-driven.
- **Tier 2 - GUI calls the same job layer. COMPLETE 2026-11-03,**
  scoped down; see sec. 16.5.
- **Tier 3 - full action registry with introspection** (~1 week+).
  NOT RECOMMENDED. Covering all 39 QActions mostly exposes mode
  toggles that are meaningless without a canvas, and it contradicts
  sec. 6's "no plugin registry for four concrete tools, one author".

Qt-only capabilities that cannot go headless: printing (QPrinter) and
preview rasterizing (QSvgRenderer). Genuinely human-only: seed
painting, vertex dragging, two-point calibration, quad corner drags.
The automatic equivalents (`dewarp.detect_page`,
`segmentation.grabcut_mask` over a prepared mask) are batch-usable.

### 16.2 VectorCraft overlap (checked against their docs, 2026-10-16)

VectorCraft (getartcraft.com, storytold/vectorcraft, Rust, alpha,
~65% "ready for real work") covers more of this workflow than
expected, INCLUDING tiled printing.

| Workflow step | VectorCraft | Note |
|---|---|---|
| Place photo | `file.place` | yes |
| 4-point flatten (flat quad) | Free Transform tool, modes `free` / `perspective` / `distort` via `tool.setOption` | yes, by hand |
| Spline flatten (book curl) | nothing equivalent | **ours alone** |
| Calibrate mm/px from a photo | Dimension tool; document units | different model |
| Trace outline | Image Trace, 13 presets, Create Strokes | auto, not seeded |
| Edit vertices | Pen / Direct Selection, real Beziers | **better than ours** |
| Measure | Dimension tool: lengths, angles, radii that FOLLOW the art | **better than ours** |
| True-scale SVG/PDF export | first-class | yes |
| Tiled printing | Print Tiling, listed done | yes |

What stays uniquely ours: the spline book flatten with text-line
refinement and batch outline propagation; interactive GrabCut tracing
of one object out of a cluttered photo; the single-scalar
mm_per_pixel calibration model built around photographing real
objects.

Worth stealing later (see "better than ours" above):
- **Live dimensions** - measurements that re-measure when the art
  moves, rather than the static rulers added 2026-09-17.
- **Bezier contours** - `Contour` is polyline-only, which is why SVG
  import must flatten curves (sec. 16.3 of svg_import's docstring).
  This is the deepest model change on the list.

Their agent story is Tier 3 done properly: a JSON control channel
(`--control 7979`) plus an MCP server where every menu item, tool
gesture, panel and dialog is drivable. If that holds up, a plausible
future is PageWright as the headless FLATTENER feeding VectorCraft -
our spline dewarp and calibration, their editing and tiling.

### 16.3 Centreline tracing: measured and rejected (2026-10-19)

Proposed because a traced outline "should" follow the middle of the
printed line. Measured against a former whose true height Robert
confirmed as 1 21/32 in, it is the WRONG correction:

| reference            | height    | error vs true |
|----------------------|-----------|---------------|
| outer edge of ink    | 1.7033 in | +0.0471 in    |
| centreline           | 1.6833 in | +0.0271 in    |
| inner edge of ink    | 1.6633 in | +0.0071 in    |
| what GrabCut gives   | 1.6600 in | +0.0038 in    |

Both axes agree, and a normal-profile scan across the traced boundary
puts the ink band at +1..+8 px INWARD of it: GrabCut already hugs the
line's inner edge. Moving to the centreline would make every part
about 0.023 in oversize.

It is also wrong in principle for this app. A cut part ends where the
line's INSIDE edge is - the draughtsman drew the line around the
shape. Centreline is the convention for a dimension or fold line, not
a cut line.

The earlier "+0.02 in improvement" claim came from a reconstruction
whose synthetic seed stopped at the OUTER edge (1.7033 in). Robert's
real seeding does not behave that way. Lesson: measure the user's own
trace, not a reconstruction of it.

Still open from that analysis: the trace lands on the inner edge by
luck of where the seed is painted, not by construction. If that
non-determinism ever bites, the fix is an explicit inner-edge snap -
NOT a centreline one.

### 16.4 find-parts: seeding is no longer the agent blocker (2026-11-03)

Section 16.1 listed seed painting as "genuinely human-only", which
was too pessimistic. A drawn part is a CLOSED LOOP of ink, so the
sheet already says where the parts are: flood the paper inward from
the page border and whatever it cannot reach is walled in by an
outline. `core/find_parts.py` does that, and `pagewright find-parts`
exposes it.

Measured on the F-86D sheet (300 DPI, 4665x2954):

- 15 candidates in 0.3 s, including every notched former.
- The known former came out with a 72-point outline against the 73
  the interactive tracer gives, and measured 1.6501 in tall against a
  true 1 21/32 = 1.6562 in - a 0.006 in error, comparable to Robert's
  hand trace at 0.004 in.
- Use `outline_size_mm`, not `size_mm`: the latter is the ink
  component's bounding box, i.e. the OUTER edge of the line, which
  reads 0.047 in large. The enclosed region stops at the line's inside,
  which is where a cut part actually ends.

Three things made the naive version fail, all handled:

- HAIRLINE GAPS. The sample former is a clean loop except one break
  where a line trails off, and one break leaks the whole interior.
  A morphological close bridges them. The kernel must be about TWICE
  the gap to bridge (it has to span the break and still touch ink on
  both sides): a 6 px break needed a 15 px kernel, not 7.
- TEXT BOXES. A ruled box around a title encloses a region exactly as
  a former does. Aspect-ratio and fill filters drop most of them; two
  still get through on the sample sheet.
- PARTS THAT NEVER SEAL. An outline crossed by a dimension line has no
  enclosed interior. These are mostly the thin ribs and spars.

What it DOES find is the large parts: 15 on the sample sheet, whose
bounding boxes cover 72% of the drawing - every notched former plus the
rudder, stabilizer and wheel. Earlier notes called this "roughly 1 in 4"
and treated it as a blocker; that counted parts rather than work, and
was the wrong measure (see sec. 16.9).

Default gap is 0.3 mm, tuned on the real plan: 0.3 found 4 of 5 known
parts, 0.6 dropped to 3 of 5 because the wider close began welding
parts to their neighbours.

What this changes for the agent story: `find-parts --json` hands back
bbox, centroid and polygon per part, so an agent can work a plan
unaided - and for the parts it misses, the marks-based hole rule
(commit 9788e10) already accepts an outside-seed MASK as data rather
than mouse gestures. Tier 2 is now done too (sec. 16.5).

### 16.5 Tier 2: one code path, two front ends (2026-11-03)

Done, but scoped down from the estimate in 16.1 after counting what
was actually there.

**The count was wrong.** 16.1 said "10 dialog-bound methods in
MainWindow". The real figure is ~45 dialog-bound functions across 8
modules: main_window 12, export_controller 10, project_controller 8,
dewarp_stage 6, ocr_stage 3, scale_stage 3, editing_controller 2,
dialogs 1. Refactoring all of them is not 2-3 days, and most of it
would buy nothing.

**What was actually shared, and is now one path:**

| Was duplicated | Now |
|---|---|
| Export SVG: calibration + empty checks, build, write | `jobs.export_svg_source` |
| Flatten: propagate -> refine -> flatten, per page | `jobs.flatten_page` |
| SVG mm->px sizing, in THREE copies | `jobs.project_from_svg` |

The enabling piece is `jobs.source_from_project`. A `Source` is only a
project plus optional pixels, so the live window already is one;
without that the GUI would have to save a temp file to reach the job
layer, which is exactly why it kept its own copy of the rules instead.

**Left alone deliberately:**

- **OCR** (`ocr_stage._run`) - works on a raw array and renders into a
  text widget. Both front ends already share all the logic there is
  (`core/ocr.py`); `jobs.ocr_source` only adds file writing, which the
  GUI does through its own Save dialog. Routing it through a `Source`
  would be a wrapper around a 3-line call.
- **Printing** (`_do_print`, `_build_tiles`, `tiles_to_pages`) - needs
  QPrinter. 16.1 already lists printing as Qt-only; `jobs.tile` is the
  headless half.
- **Pickers and gestures** (`add_page_folder`, `_pick_mark_color`,
  `_on_calibrated`, `rename_active_object`, ...) - a folder picker that
  appends to a list has no headless meaning.

**Error text had to move.** A shared message cannot name a CLI flag:
"give --dpi" is wrong in a message box. The job layer now says
"calibrate it, or supply one" and `cli._hint` appends the flag list at
the one place a JobError becomes stderr. Three job tests asserted
"--dpi"; they now assert "no scale" and the flag check moved to the
CLI tests, where it belongs.

**Proof it works, rather than just compiles.** Reintroducing the
`seed_and_refine` tuple bug (the one that shipped because batch flatten
was never test-driven) now fails 4 tests across BOTH front ends from
one defect. Before this, only the CLI tests caught it.

Finding it needed tests that drive the real methods with only the
dialogs stubbed. Writing them turned up two live bugs in code that had
no coverage at all: `open_svg` referenced an undefined `objects` after
the refactor and would have crashed on the next SVG opened, and an
offscreen `QMessageBox.information` hangs forever, which is how an
untested GUI path fails rather than reports.

### 16.6 Old plans into CAD and back (2026-11-04)

Goal: take a scanned plan, stand its sheet parts up in CAD in the
right orientation so a designer can add servos, motors and control
rods, then send the modified parts back out as patterns or DXF for a
laser cutter. NOT a lofted fuselage - flat parts only.

The route turned out to be short, because CadRoundTrip already has
the hard half. `recipe.json` is its tool-neutral hub and SolidCraft,
Fusion and OpenSCAD all rebuild from it. PageWright only had to learn
to write one.

Two things made that possible without Fusion:

- A `token` is only an identity key. SolidCraft's own exporter mints
  `sc:0` and `Sketch1/c1`, so ours can be readable.
- An extrude finds its profile by token first, then falls back to
  AREA and CENTROID. A synthetic recipe has no Fusion tokens, so the
  area path is the one that runs - which is why recipe_export
  computes a true area centroid rather than a vertex mean.

Verified by running the real `solidcraft_import.py` over our output:
**PASS, 8 of 8 items rebuilt, 0 differ**, giving four closed solids
each 1.6 mm on the axis its plane dictates, at the requested
stations, with volumes matching the traced areas to 0.01%.

One bug found that way: `startExtent` is a bare string in a recipe,
not a dict. The importer reported "unsupported" and stopped at the
first extrude. Unit tests alone would not have caught it; only
running the real consumer did.

**Why placement is recorded, not inferred.** A plan sheet is NESTED
for material efficiency. On the F-86D sheet the ribs sit in a row,
the formers are scattered and the stabilizer is turned 90 degrees, so
where a part was drawn says nothing about where it belongs. The
designer supplies plane, station and thickness; PageWright carries
them. Robert confirmed this is expected, which removed the only part
of the problem that had no sound answer.

**PageWright earns its place in this pipeline.** SolidCraft 0.28 has
`canvas.insert`, `canvas.calibrate` and `canvas.to_sketch`, which
looked like they made it redundant. On the same former crop at the
same calibration:

| | PageWright | SolidCraft canvas.to_sketch |
|---|---|---|
| entities | 72-point polygon | 408 curves (402 splines) |
| outlines | 1 - the part | 5 - part plus lettering |
| height | 41.83 mm | 44.47 mm |
| sketch DOF | n/a | 2424 |
| extrudes to | 1 body | 5 bodies |

True height is 42.07 mm. Their tracer follows the OUTER edge of the
ink (the same inner/outer distinction as sec. 16.3) and takes the
part number as geometry. Clean, few, correctly-sized polygons are the
product.

**Still to do for the full loop:** the return leg. `sheet.export_dxf`
and `sketch.export_dxf` exist and were tested by hand - a servo
pocket cut from a former exported as a 72-entity DXF - but nothing in
PageWright drives it yet, and a modified part does not come back to
tiled patterns automatically.

**Recall is NOT the bottleneck** (superseded by sec. 16.9).
`find-parts` locates 15 parts covering 72% of that sheet, and the ones
it misses are thin ribs and spars a designer adds by hand in minutes.
Placement and orientation of the parts already found are worth more
than finding more parts.

### 16.7 Backlog: un-notched copies for lofting (raised 2026-11-04)

Robert: alongside each former and rib, offer a copy with the notches
filled in. Those clean profiles are what you loft through when you
want to move structure around - a loft wants the fuselage SURFACE,
and a notched profile lofts the notches too, which is not the shape
of the aeroplane.

Not started. Recorded now because it changes what a part is: one
traced outline would carry two profiles, a cut one and a lofting
one, which is a model question rather than an export detail.

What already helps: `core/notches.py` finds sharp corners
(CORNER_DEG) and axis-aligned edges, which is the hard half of
recognising a notch, and it is already wired to the GUI as Clean Up
Notches.

What does NOT work, measured on the F-86D former: a convex hull.
Hulling the 72-point outline gives 26 points and adds 9.2% area, but
it removes every concavity, not just the notches - a curved underside
or a lightening cutout would be swallowed with them. It has to be
notch-aware: find the axis-aligned slots that break an otherwise
smooth boundary and bridge each one, leaving the rest alone.

Open questions worth settling before building it:

- Does the un-notched copy live as a second Contour with a role
  ("loft"), a second TracedObject, or a flag on Build? A second
  contour keeps the two in step when the outline is edited.
- Should it reach the recipe at all, or only the lofting tool? A
  loft profile is usually a construction sketch, not a body.
- A notch filled across a curve needs the ARC the notch interrupted,
  not a straight chord, or the loft gets a flat spot.

### 16.8 The return leg, and what people will actually do (2026-11-04)

`import-dxf` closes the loop. A part modified in CAD comes back as
DXF and becomes a project again, so it can be printed 1:1 as a
pattern. Verified end to end on the F-86D former: trace -> recipe ->
SolidCraft -> cut a servo bay and two lightening holes -> DXF -> back
in as 4 loops at 31.83 x 41.83 mm, the original traced size -> tiled
to a printable sheet.

LINE, ARC and CIRCLE are read. Splines are already flattened by
SolidCraft on export (a four-point fit spline came out as 72 LINEs),
so nothing else is needed. Anything unrecognised is COUNTED AND
REPORTED, never dropped: a silently ignored entity is a hole missing
from a cut part.

One bug worth remembering: `sheet.export_dxf` writes a far richer
file than `sketch.export_dxf` - TABLES, BLOCKS and OBJECTS sections,
whose own code-2 values broke the first section tracker. It returned
2 loops instead of 4 and looked plausible. Only counting the features
that went in caught it.

**Curve detail is a user control, not a constant** (Robert, same
day). These plans were drawn at this size with a French curve through
a couple of control points, so sub-tenth-millimetre fidelity
reproduces the draughtsman's hand rather than the aeroplane, and
complex splines make needlessly heavy models. `--detail` sets the
chord deviation; the default is 0.2 mm, which is finer than a laser
kerf. Measured on a 7 mm hole: 19 points at 0.05 mm, 10 at 0.2 mm,
6 at 0.5 mm. The same number holds the DEVIATION constant, so it
behaves the same on a 3 mm hole and a 300 mm curve.

**The use cases to design against** (Robert): adding an electric
motor, adding ailerons, and simply changing the output size - the
Sagitta glider being the example he wants to run it on.

Where each stands:

- **Change output size.** Mostly there. `tile --scale` prints at a
  percentage, and scaling `mm_per_pixel` enlarges a recipe too
  (measured: 8.85 cm -> 11.06 cm at 125%). Note that thickness
  deliberately does NOT scale with it, which is correct - balsa comes
  in fixed sizes, so a 125% model is still cut from 1/16 stock. Worth
  an explicit flag rather than making the user edit calibration.
- **Electric motor.** Needs a firewall former and a thrust line, so
  it is mostly the existing flow plus a new part the plan never had.
  Nothing blocks it.
- **Ailerons.** The hard one. It means CUTTING an existing wing
  panel - splitting a rib set and adding spars - rather than
  modifying one part in isolation. Nothing in PageWright models the
  relationship between parts, so this is CAD-side work that only
  needs the parts to arrive correctly placed. Which is the argument
  for 16.7 (un-notched lofting profiles) being the next real step.

### 16.9 Partial results are the deliverable (Robert, 2026-11-04)

Standing guidance, and a correction to how earlier sections framed
this work:

> Most of these features could be added by the human designer once
> she has a 3D model showing structures in the correct orientation.
> Initial placements are a nice-to-have. 50% of the structure placed
> on the plans is a huge win, as long as missing formers or ribs can
> be added by hand.

The user of this tool is a designer with CAD skills, not someone who
needs a finished model handed to them. What they cannot do cheaply is
trace sixty parts off a scan and stand them up in the right places.
What they can do in minutes is draw a missing rib.

So the measure is WORK SAVED, not parts found. By that measure
find-parts already does well: 15 parts covering 72% of the sheet,
and the ones it misses are the thin, simple ribs and spars - the
cheapest things to add by hand. Sections 16.4 and 16.6 called the
same number "roughly 1 in 4" and listed recall as the bottleneck.
That was counting the wrong thing.

What this means for the backlog:

- **Do not chase recall** as though it gates the rest. Better
  detection is worth having, but it is not what unblocks a designer.
- **Prefer anything that improves ORIENTATION and PLACEMENT** of the
  parts already found, since that is the part a human cannot redo
  quickly. 16.7 (un-notched lofting profiles) is the strongest
  candidate on those grounds.
- **Ship partial output rather than refusing.** A sheet where half
  the parts lack a thickness should still export the half that have
  one, naming what it skipped - which export-recipe already does.
- **Do not block on perfect.** Incomplete and correct beats complete
  and late.

### 16.10 Part numbers carry the placement (Robert, 2026-11-04)

"Hand annotated F-numbers and R-numbers is acceptable in the
workflow." That settles the naming question and pays for itself: a
plan numbers its parts by what they ARE - F-3 is the third fuselage
former, R-2 the second wing rib - so a name typed in while annotating
already says which way the part stands.

`recipe_export.plane_from_name` reads it: F/former/frame/bulkhead/fus
-> YZ, R/rib/W/wing -> XZ. It only fills in a plane nobody set, so an
explicit choice always wins, and the match is deliberately narrow -
a letter (or word) then a number. "Rudder", "Fin" and "Stabilizer"
read as nothing, because guessing from them would place a part
wrongly and quietly.

It removes the per-part plane click for the parts that follow the
convention, which on a typical sheet is most of them. No OCR is
involved: the names are typed, which is why this works at all.

Still worth doing later: use the same names to drive STATION, since
F-1 through F-9 run fore to aft in order. That needs a spacing the
plan does not state, so it would be a guess - left alone for now.
