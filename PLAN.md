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
