# PageWright

A desktop GUI tool for tracing the outlines of objects in photographs and exporting
**true-scale SVG** files, with ruler-based calibration and tiled large-format printing.

> PageWright began as **TraceImage** and was rebranded to become a unified
> capture-to-OCR studio. Today the hub has four first-class tools over one
> working image - **Trace**, **Flatten** (quad + one/two-page book dewarp),
> **Scale**, and **OCR** - plus tiled true-scale printing, multi-page jobs
> (folders of scans, PDF import, derived pages, batch flatten).

See [`PLAN.md`](PLAN.md) for the full design and rationale, and
[`PORTING.md`](PORTING.md) for the anticipated C++/Qt port.

**Long-term direction:** this project is slated to grow into a unified
capture-to-OCR studio, absorbing the sibling `dewarp` and `BookScan`
tools as pipeline stages. See [`PLAN.md` section 13](PLAN.md#13-roadmap-unified-capture-to-ocr-studio-mirrored-from-dewarpclaudemd-2026-07-13) for the roadmap.

## Install

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Confirm the stack imports:

```bash
python -c "import cv2, numpy, PySide6; print('ok')"
```

## Run

From the project root, run the launcher directly — no install needed:

```bash
python PageWright.py
```

(`python src/pagewright/main.py` also still works; the entry point puts
`src/` on `sys.path` itself.)

It takes an optional file to open at startup — either a photo or a saved
`.tiproj.json` project (chosen by extension):

```bash
python src/pagewright/main.py samples/IMG_4705cropped.png
python src/pagewright/main.py my_trace.tiproj.json
```

Or install the package (editable) and use the console entry point:

```bash
pip install -e .
pagewright samples/IMG_4705cropped.png
```

Note: `python -m pagewright.main` only works if `src/` is already on
`sys.path` (i.e. after `pip install -e .`, or with `PYTHONPATH=src`). With
`-m`, Python must import the `pagewright` package *before* `main.py` runs, so
the `sys.path` fix inside it comes too late. On Windows/PowerShell:

```powershell
$env:PYTHONPATH="src"; python -m pagewright.main
```

## Command line (headless)

Seven subcommands run without a display — they never import Qt, so they work
over SSH, in a container, or in a shell loop:

```bash
pagewright info pattern.svg
pagewright tile pattern.svg --page A4 --out ./tiles
```

`info` reads an input and reports its pixel size, real-world size, scale,
contents and — the useful part — how many sheets it would take, before you
commit to tiling it:

```
NFFS2027_flat.svg  (svg)
  pixels      : 4069 x 2044
  scale       : 0.1 mm/px  (SVG document units)
  real size   : 406.9 mm x 204.4 mm
  objects     : 1  (3 shapes, 12 points)
  tile plan   : 3 sheet(s) = 3 col x 1 row on Letter
                margin 6.0 mm, overlap 10.0 mm, sheet 215.9 mm x 279.4 mm
```

`--json` gives the same data machine-readably. `tile` writes one page-size
SVG per sheet, at true scale with registration marks (the headless form of
Export Tiles); `-n`/`--dry-run` reports the plan without writing.

Inputs are images, SVG, PDF or `.tiproj.json` projects, chosen by extension.
**A photo carries no real-world size** (in the GUI that is the two-click
Calibrate gesture), so image inputs need a scale — one of `--dpi`,
`--mm-per-px`, `--width` or `--height`. SVG, PDF and saved projects already
know their size, and these flags override it:

```bash
pagewright tile scan.jpg --dpi 300 --overlap 15mm
pagewright tile photo.png --width 300mm --page A4 --landscape
pagewright tile job.tiproj.json --scale 50 -o ./half-size
```

The rest of the set:

```bash
pagewright import-svg pattern.svg -o pattern.tiproj.json
pagewright export-svg pattern.tiproj.json --inkscape
pagewright flatten scans/*.jpg --out ./flat
pagewright ocr page.png --out page.txt
```

`find-parts` locates the drawn parts on a plan with **no seed marks at all**.
A part is a closed loop of ink, so flooding the paper inward from the page
border leaves the part interiors unreachable:

```bash
pagewright find-parts plan.png --dpi 300 --trace
pagewright find-parts plan.png --dpi 300 -o parts.tiproj.json
```

With `-o` the traced outlines are saved as a project, one object per part,
ready for `tile` or `export-svg` - so a raster plan becomes printable tiles
without anyone marking anything. `--json` returns each part's bounding box,
centroid and polygon, which is what an agent needs to drive the rest.

Its limit is worth knowing: a part whose outline is crossed by a dimension
line never seals and is not found. On a real model-aircraft sheet it found
about a quarter of the parts, including every notched former. It is a way to
skip the easy ones, not a replacement for marking the hard ones.

`flatten` is the one worth knowing about: page-outline detection needs no
human input, so a folder of book photographs is straightened in one go, each
page seeded from the previous one (consecutive pages of a book share a binding
and opening angle). A page that fails is reported and the run carries on, with
a non-zero exit so a script notices.

Lengths take a unit (`10mm`, `1cm`, `0.5in`); a bare number is millimetres.
`pagewright COMMAND --help` lists every flag.

The work lives in `core/jobs.py`, which is Qt-free by contract (a test
asserts it in a clean interpreter) — so the GUI and the CLI can drive the
same job rather than drifting apart.

## Usage so far

1. **File → Import Photo…** (Ctrl+Shift+O) to load an image. Scroll wheel
   zooms; in Pan/Zoom mode drag to pan, and from *any* mode you can hold the
   middle mouse button (wheel) to pan. **Fit** rescales to the window.
2. **Tools → Calibrate Scale…** then click two points on a feature of known
   length (e.g. a ruler). Enter the real distance and unit in the dialog.
3. The status bar shows `mm/px` and the computed real-world size of the photo.
4. **Trace an object:** pick **Mark Foreground (inside)** and paint a few
   strokes inside the object, then **Mark Background (outside)** and paint
   outside it (the brush defaults to 40 px; change it in the toolbar or the
   right-click menu, and a ring previews its size). Click **Trace Poly** to run
   GrabCut and extract the outline (plus any interior holes). A right-click on
   the canvas gives quick access to the marking tools, Trace Poly, and zoom.
5. **Edit Vertices:** drag a handle to move a point, right-click a handle to
   delete it, double-click an edge to insert a point. Drag a box over empty
   canvas to marquee-select several vertices, then press **Delete** to remove
   them at once (dropping a contour entirely if it would fall below 3 points).
   To improve a poor result, add more seeds and Trace Poly again.
6. **More objects:** in the **Objects** panel click **New**, then seed and Run
   Segmentation again to trace another object. Set the **Margin** there; the
   dashed bounding box shows the exported extent and real size.
7. **File → Export SVG…** choose whether to embed the photo (and downscale it),
   outline vs filled, and plain vs **Inkscape** format. The SVG is sized in
   millimetres at true 1:1 scale. Plain SVG opens in any browser; Inkscape
   format adds named layers (photo locked) and mm document units so you can
   fine-tune control points there.
8. **Tiling** is controlled from the **Tiling** group in the side panel: page
   size, orientation, printer margin, overlap, scale (%), include-photo and
   fill. Tick **Show tile grid** (or **View → View Tiles**) to overlay the
   page-tile split on the canvas; it updates live as you change any setting.
   **File → Export Print Tiles…** then just asks for a folder and writes one
   SVG per tile using those settings. Each tile carries a live-area rectangle,
   an R#-C# label, and a filled diamond at the middle of each edge; the
   diamonds on a shared edge line up when you overlap the printed sheets. Files
   are named `<photo>-rNcM.svg`. Tiling does **not** require calibration — if
   the scale isn't set, the image's own pixels are used at its DPI (or 96 DPI
   if unknown); the **Scale** control resizes the output (50% = half, 200% =
   double). Print with auto-scaling / "fit to page" OFF.
9. **Projects:** **File → Save Project** (Ctrl+S) writes a `.tiproj.json` with
   the calibration, margin, units, traced polygons, the tiling settings, and a
   reference to the source image; **File → Open Project** (Ctrl+O) reloads it
   (prompting you to locate the image if it has moved).

Notes / current rough edges:

- In Edit Vertices mode, left-drag on empty canvas makes a selection box rather
  than panning; pan with the wheel/zoom or by holding the middle mouse button.
  Double-clicking an edge to insert a point is easiest when zoomed in.
- Seeds map 1:1 onto image pixels. If a photo carries EXIF rotation that Qt
  applies but OpenCV does not, pre-rotate the file before tracing (handled more
  robustly in a later phase).
- Deleting a contour's only contour leaves the object empty in the Objects
  panel; remove it there if you don't want it (export ignores empty objects).

## Web app / iPhone

PageWright's desktop UI (PySide6) does not run in a browser or on a
phone. The browser story is the **Pyodide web app in the sibling
`dewarp` repo** (`feature/webapp` branch, `webapp/index.html`): it runs
the same single-page spline dewarp core, unmodified, in the browser via
WebAssembly (numpy + OpenCV wasm). Per PLAN sec. 13 decision 4, it
stays a thin separate front-end over the shared core. Status: it was
mid-debug (Pyodide/opencv version matrix) when parked - test on desktop
Safari/Chrome first.

To use it from an iPhone:

1. **Quick LAN test** (no install on the phone): on the PC, from the
   dewarp repo:

       git checkout feature/webapp
       python -m http.server 8000

   then on the iPhone (same Wi-Fi) open Safari to
   `http://<pc-ip>:8000/webapp/`. Find the PC's IP with `ipconfig`
   (Windows). First load downloads the Python/OpenCV wasm runtime
   (tens of MB) - be on Wi-Fi and be patient; it is cached after that.

2. **Real deployment - GitHub Pages** (free static hosting; the web
   app is pure static files): push the `webapp/` folder to a
   `gh-pages` branch (or enable Pages on the repo, folder `/webapp`),
   then open `https://<user>.github.io/dewarp/webapp/` in Safari.
   HTTPS matters: iOS only allows **camera capture**
   (`getUserMedia` / `<input capture>`) on secure origins, so
   photographing a page straight from the phone needs Pages (or any
   HTTPS host), not the LAN server.

3. **Home-screen app**: in Safari, Share -> **Add to Home Screen**.
   The page opens full-screen with its own icon like a native app.
   (A `manifest.json` + apple-touch-icon in `webapp/` would polish
   this - not yet done.)

iOS caveats worth knowing: Safari gives a wasm page limited memory, so
very large photos may fail where the desktop succeeds (the app's
downscale path helps); and iOS clears cached wasm for rarely-used
sites, re-downloading the runtime on next visit.

## Tests

Pure-Python tests cover the GUI-free core: calibration, geometry, SVG export,
tiling, the undo stack, and project save/load. Segmentation/contour tests use
NumPy + OpenCV and are skipped if those aren't installed.

```bash
python -m pytest tests/
```

## Status

- **Phase 0 — Scaffold:** ✅ app skeleton that loads a photo with zoom/pan.
- **Phase 1 — Calibration:** ✅ two-point measurement, unit selection, live scale + real-size readout.
- **Phase 2 — Segmentation:** ✅ foreground/background seed painting, GrabCut, contour extraction with holes, editable polygons (move/add/delete vertices), re-seed/re-run refine loop.
- **Phase 3 — Object model:** ✅ multiple objects (each with outer + holes + disjoint loops) via the Objects panel (new/delete/rename/select), margin control, bounding-box preview, real-size readout.
- **Phase 4 — SVG export:** ✅ mm-scaled `<svg>` with matching viewBox, optional base64-embedded (downscalable) photo layer, one compound `<path>` per object with `fill-rule="evenodd"`, outline or filled. Plain (browser-friendly) or **Inkscape** flavor (named layers + mm document units) so nodes can be fine-tuned in Inkscape.
- **Phase 5 — Tiled printing:** ✅ split the drawing across Letter/A4/Legal/A3 pages (portrait/landscape) at true 1:1, with per-tile live-area rectangle, R#-C# label, and diamond registration marks that coincide across overlapping sheets; photo on/off.
- **Phase 6 — Polish (in progress):** ✅ save/load project as JSON (File → Save/Open Project; calibration, margin, units, traced polygons, source-image reference); ✅ undo/redo command stack for vertex edits (move/add/delete) and object delete; ✅ marquee (rubber-band) multi-vertex selection in Edit Vertices mode with **Delete** to group-delete (one undo step); if a delete would leave a contour with fewer than 3 vertices the whole contour is removed (and restored on undo), since a sub-3 contour is not a polygon. Ctrl+Z/Y dispatch by context — while seeding they undo/redo seed strokes, otherwise the edit command stack. Still to come: folding object-create/trace into the undo history. (Bézier smoothing deprioritized in favor of the Inkscape export flavor.)
- **Phase 7 — Port readiness:** ✅ audited the core for Python-only idioms (none — only library bindings need C++ equivalents) and sketched the C++/Qt structure in [`PORTING.md`](PORTING.md).

Keyboard: **Ctrl+O** open project, **Ctrl+S** save project, **Ctrl+Shift+O** import photo, **Ctrl+E** export SVG, **Ctrl+Z/Ctrl+Y** undo/redo (seed strokes while seeding; vertex/object edits otherwise).

## Layout

```
src/pagewright/
  main.py          entry point
  model.py         Project / TracedObject / Contour
  core/            image_io, calibration, segmentation, contours,
                   geometry, svg_export, tiling, project_io, undo
  ui/              main_window, canvas, objects, editable, dialogs
tests/             unit tests for the GUI-free core
samples/           sample photos
output/            generated SVGs (git-ignored)
```
