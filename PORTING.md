# PageWright — C++/Qt Port Readiness (Phase 7)

This document records the Phase 7 review: an audit of the `core/` modules for
Python-only idioms, and a sketch of the anticipated C++/Qt structure. The aim
stated in `PLAN.md` was that core logic should translate to C++ "with little
more than syntax changes." This review confirms that holds, and pins down the
few places that need a real library/binding choice rather than a syntax change.

## 1. Strategy

Split the port into two artifacts, mirroring today's `core/` vs `ui/` split:

- **`pagewright_core`** — a Qt-free C++ static library: data model, calibration,
  geometry, segmentation, contours, SVG export, tiling, project I/O, undo.
  Depends only on OpenCV (already C++), a JSON library, and the C++ standard
  library. This is a near-mechanical translation of `core/` + `model.py`.
- **`pagewright` (GUI)** — a Qt Widgets/C++ app translating `ui/`. Qt is C++
  already; PySide6 calls map ~1:1 onto the Qt C++ API (`QGraphicsView`,
  `QGraphicsScene`, `QAction`, `QGraphicsEllipseItem`, etc.).

Keeping the core Qt-free (as it is now) is what makes the port low-risk: the
GUI can be rewritten or replaced without touching the geometry/export logic.

## 2. Library equivalents

| Concern | Python (now) | C++ equivalent |
|---|---|---|
| Image processing | `cv2` | OpenCV C++ (`cv::Mat`, `cv::grabCut`, `cv::findContours`, `cv::approxPolyDP`, `cv::resize`, `cv::GaussianBlur`, `cv::imencode`) |
| Numerics / arrays | NumPy | `cv::Mat` (pixel masks) and `std::vector` (point lists) |
| GUI | PySide6 (Qt) | Qt Widgets (C++) — same classes |
| JSON (project I/O) | `json` | nlohmann/json **or** `QJsonDocument` (if the core may depend on Qt::Core) |
| Base64 (embedded photo) | `base64` | a small base64 encoder, or `QByteArray::toBase64` |
| Commands/callbacks | closures | `std::function<void()>` |
| Number formatting | `"%.3f" % x` + `rstrip` | `std::ostringstream` / `std::format` with trailing-zero trim |

Recommendation: use **nlohmann/json** and a tiny base64 helper so the core has
**no Qt dependency at all**. (Using Qt::Core for JSON/base64 is fine too, but it
couples the core to Qt.)

## 3. Proposed layout

```
pagewright/
  CMakeLists.txt
  core/                      # pagewright_core static lib (no Qt)
    include/pagewright/
      model.hpp              Project / TracedObject / Contour / Style / Calibration
      calibration.hpp
      geometry.hpp           BBox + transforms
      image_io.hpp           LoadedImage
      segmentation.hpp       Stroke, grabcut_from_strokes, ...
      contours.hpp           extract_contours
      svg_export.hpp         build_svg / build_content
      tiling.hpp             build_tiles / plan_tiles
      project_io.hpp         save/load + to_json/from_json
      undo.hpp               Command, FnCommand, UndoStack
    src/*.cpp
    third_party/json.hpp     (nlohmann) + base64.hpp
  gui/                       # Qt app
    main.cpp
    MainWindow.{hpp,cpp}
    Canvas.{hpp,cpp}         QGraphicsView subclass
    ObjectLayer.{hpp,cpp}
    EditableContour.{hpp,cpp}  VertexHandle / outline items
    dialogs/*.{hpp,cpp}
  tests/                     # GoogleTest, mirrors tests/test_*.py
```

## 4. Data model as C++ structs

The model is plain data — a direct struct translation:

```cpp
struct Style { std::string stroke = "#000000"; double stroke_width_mm = 0.5;
               std::string fill = "none"; };

struct Contour {
  std::vector<std::array<double,2>> points;   // (x_px, y_px)
  bool closed = true;
  std::string role = "outer";                 // or an enum Role { Outer, Hole }
  std::string representation = "polyline";
};

struct TracedObject { std::string name; std::vector<Contour> contours; Style style; };

struct Calibration { std::optional<double> mm_per_pixel; std::string display_unit = "mm"; };

struct Project {
  std::string source_image_path; int pixel_width = 0, pixel_height = 0;
  std::optional<double> dpi; Calibration calibration; double margin_mm = 5.0;
  std::vector<TracedObject> objects;
};
```

`role` is a good candidate to become an `enum class Role { Outer, Hole }` in C++
(strings were convenient in Python); the JSON layer maps it to "outer"/"hole".

## 5. Module-by-module notes

**calibration** — pure arithmetic. `MM_PER_UNIT` dict → `static const
std::map<std::string,double>` or a `switch` on a `Unit` enum. `%`-formatting →
`std::format`. Trivial.

**geometry** — `BBox` struct + free functions. `bbox_of_points` uses
`iter()/next()` only to seed from the first point; in C++ just check
`points.empty()` then loop from index 1. Trivial.

**image_io** — `cv::imread` returns `cv::Mat`; `LoadedImage` wraps it with
width/height accessors (`mat.cols`/`mat.rows`). Trivial.

**segmentation** — the one module that is "NumPy-shaped." Translations:
- `np.full((h,w), v, uint8)` → `cv::Mat(h, w, CV_8U, cv::Scalar(v))`.
- stroke rasterization already uses `cv::line`/`cv::circle` — identical in C++.
- `(mask==FG) | (mask==PR_FG)` → `cv::Mat fg = (mask==FG) | (mask==PR_FG);`
  (OpenCV supports these element-wise ops) then `fg /= 255` or threshold to 0/1.
- downscale path: `cv::resize(..., INTER_AREA)`, run GrabCut, `cv::resize(...,
  INTER_LINEAR)`, `cv::GaussianBlur`, then threshold `>= 0.5`. All direct.
No Python-only control flow; the comprehension in `_scaled_strokes` → a loop.

**contours** — `cv::findContours(img, contours, hierarchy, RETR_CCOMP,
CHAIN_APPROX_SIMPLE)`; hierarchy is `std::vector<cv::Vec4i>`; `cv::approxPolyDP`
and `cv::contourArea` are identical. The role test `hierarchy[i][3] != -1`
ports verbatim.

**svg_export** — string assembly with `std::ostringstream`. `_num` (3 decimals,
trailing-zero trim) is a small helper. `base64.b64encode(cv2.imencode(".png"))`
→ `cv::imencode(".png", img, buf)` + a base64 encoder. Compound-path / evenodd
logic is plain string building. The lazy `import cv2` becomes unconditional
(OpenCV is always linked); guard the photo path on whether an image was passed.

**tiling** — `math.ceil` → `std::ceil`; `PAGE_SIZES_MM` dict → `std::map`;
per-tile SVG is string building; reuses `svg_export::build_content`. Direct.

**project_io** — replace the `json` module with nlohmann/json: give each struct
`to_json`/`from_json` (nlohmann's ADL hooks) and `save_project`/`load_project`
become file read/write + parse. The dict-building comprehensions become loops.
Version checking and the `ProjectIOError` → a `std::runtime_error` subclass.

**undo** — `Command` is an abstract base with `undo()`/`redo()`; `FnCommand`
holds two `std::function<void()>`; `UndoStack` holds `std::vector<
std::unique_ptr<Command>>` for done/undone plus a capacity and a change
callback (`std::function<void()>`). The closures used in the GUI (vertex
move/insert/delete, object delete, group delete) become lambdas capturing the
contour/index/coordinates — exactly as today, with `std::function`.

## 6. GUI notes (ui/ → Qt C++)

- `Canvas(QGraphicsView)` — overrides `wheelEvent`, `mousePressEvent`,
  `mouseMoveEvent`, `mouseReleaseEvent`, `keyPressEvent`, `contextMenuEvent`;
  all exist in Qt C++ with the same names. Signals become Qt signals declared
  with the `signals:` section + `Q_OBJECT`.
- `VertexHandle(QGraphicsEllipseItem)` — flags (`ItemIsMovable`,
  `ItemIsSelectable`, `ItemSendsScenePositionChanges`,
  `ItemIgnoresTransformations`) are identical; `itemChange` override identical.
- The `edit_sink` duck-typed protocol becomes a small abstract interface
  (`IEditSink` with `recordMove/recordInsert/recordDelete`) that `MainWindow`
  implements — cleaner than Python's duck typing.
- Middle-button pan, rubber-band selection (`RubberBandDrag`), brush ring, and
  the bounding-box item all use the same Qt classes.

## 7. Gotchas to preserve in the port

- **Coordinate convention:** origin top-left, y increases downward, consistent
  across photo, scene, and SVG — no axis flip. Keep this.
- **Pixels are the source of truth;** mm is derived at export via
  `mm_per_pixel`. Keep geometry in pixels until export/tiling.
- **True 1:1 printing** depends on disabling printer auto-scaling; the
  registration diamonds are the scale check. Document in the GUI as today.
- **EXIF rotation:** Qt's `QPixmap`/`QImage` may auto-apply EXIF orientation
  while OpenCV does not, which would misalign seeds vs. the cv::Mat. The port
  should normalize orientation on load (decode EXIF once, apply to both, or
  strip it) — this is a known rough edge noted in the README.

## 8. Verdict

The core is port-ready: it is free of Python-only language idioms. The work to
port is (a) mechanical syntax translation, (b) swapping three library bindings
(NumPy→cv::Mat, json→nlohmann/json, base64 helper), and (c) re-expressing the
GUI against Qt C++ (which is the same toolkit). No structural redesign of the
core is required.
