"""Headless jobs: the work the GUI does, callable without Qt.


Why this exists: every GUI action in PageWright is a thin shell around
`core/` - validate, ask the user for parameters, then compute. The
compute half was always Qt-free; what was missing was a way to supply
the parameters from somewhere other than a dialog. These functions are
that way in, so the same job can be driven by the window or by argv
(see `cli.py`).

Design rules, to keep this layer honest:

  * NO Qt, and no imports from `pagewright.ui`. A job must run on a
    machine with no display. The test suite asserts this.
  * NO printing to stdout and no `sys.exit`. Jobs return result objects
    or raise `JobError`; the caller decides how to report. (A `progress`
    callback is offered for long runs.)
  * Jobs take plain Python values, not parsed argparse namespaces, so
    the GUI can call them just as easily as the CLI.

Anything needing a human gesture (seed painting, vertex dragging,
two-point calibration, dragging quad corners) is deliberately absent -
those are not jobs, and the automatic equivalents
(`dewarp.detect_page`, `segmentation.grabcut_mask` over a prepared
mask) are what batch work uses instead.
"""

import os

from . import calibration as calib
from . import image_io, project_io, svg_export, tiling
from ..model import Project

# Input kinds a job can open. Keep in step with `load_source`.
SVG_EXTS = (".svg",)
PDF_EXTS = (".pdf",)
PROJECT_EXTS = (".tiproj.json", ".json")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")


class JobError(Exception):
    """A job could not run, with a message fit to show a user."""


class Source(object):
    """What a job opened, normalised.

    `project` is always a usable Project. `image_bgr` is the raster for
    image/PDF sources and None for vector ones. `kind` is one of
    "svg" / "pdf" / "project" / "image", and `calibrated_by` records
    WHERE the scale came from so a caller can explain itself.
    """

    def __init__(self, kind, path, project, image_bgr=None,
                 calibrated_by=None):
        self.kind = kind
        self.path = path
        self.project = project
        self.image_bgr = image_bgr
        self.calibrated_by = calibrated_by

    @property
    def base_name(self):
        base = os.path.basename(self.path or "job")
        for ext in PROJECT_EXTS:          # strip the double extension
            if base.lower().endswith(ext):
                return base[:-len(ext)]
        return os.path.splitext(base)[0]

    @property
    def size_mm(self):
        """(w, h) of the whole page/image in mm, or None if uncalibrated."""
        return self.project.real_size_mm()


def classify(path):
    """Which kind of input `path` is, by extension."""
    low = (path or "").lower()
    for ext in PROJECT_EXTS:          # before IMAGE: .json is not an image
        if low.endswith(ext):
            return "project"
    if low.endswith(SVG_EXTS):
        return "svg"
    if low.endswith(PDF_EXTS):
        return "pdf"
    if low.endswith(IMAGE_EXTS):
        return "image"
    return None


# ----- scale resolution ---------------------------------------------------

def resolve_scale(project, pixel_w, pixel_h, mm_per_px=None, dpi=None,
                  width=None, height=None):
    """Work out mm-per-pixel for a source that has no inherent scale.

    Exactly one of mm_per_px / dpi / width / height may be given. Width
    and height accept a unit suffix ("300mm", "11.7in") and are the
    intended REAL size of the whole image. Returns
    (mm_per_pixel, description) or (None, None) when nothing was given.

    This exists because calibration in the GUI is a two-click gesture:
    a photo carries no real-world size, so a headless job has to be
    told. SVG and PDF sources skip this - their size is in the file.
    """
    given = [("--mm-per-px", mm_per_px), ("--dpi", dpi),
             ("--width", width), ("--height", height)]
    given = [(flag, v) for (flag, v) in given if v is not None]
    if not given:
        return None, None
    if len(given) > 1:
        raise JobError("give only one of %s"
                       % ", ".join(flag for flag, _v in given))
    flag, value = given[0]
    if flag == "--mm-per-px":
        mpp = float(value)
        if mpp <= 0.0:
            raise JobError("--mm-per-px must be positive")
        return mpp, "%g mm/px (given)" % mpp
    if flag == "--dpi":
        d = float(value)
        if d <= 0.0:
            raise JobError("--dpi must be positive")
        return 25.4 / d, "%g DPI (given)" % d
    # width / height: a real length for the whole image
    try:
        want_mm = calib.parse_length_mm(value)
    except ValueError as exc:
        raise JobError("%s: %s" % (flag, exc))
    span_px = pixel_w if flag == "--width" else pixel_h
    if not span_px:
        raise JobError("%s needs a known pixel size" % flag)
    mpp = want_mm / float(span_px)
    return mpp, "%s = %s" % (flag.lstrip("-"),
                             calib.format_length(want_mm, "mm"))

# ----- opening sources ----------------------------------------------------

def load_source(path, mm_per_px=None, dpi=None, width=None, height=None,
                pdf_page=1, pdf_dpi=300):
    """Open any supported input as a Source ready for other jobs.

    Scale comes from the file where the file knows it (SVG units, PDF
    page size, a saved project calibration) and from the scale flags
    otherwise. Explicit flags always win, so a wrong SVG header can be
    overridden.
    """
    if not path or not os.path.isfile(path):
        raise JobError("file not found: %s" % (path,))
    kind = classify(path)
    if kind is None:
        raise JobError("unsupported file type: %s"
                       % (os.path.basename(path),))

    if kind == "svg":
        src = _load_svg(path)
    elif kind == "pdf":
        src = _load_pdf(path, pdf_page, pdf_dpi)
    elif kind == "project":
        src = _load_project(path)
    else:
        src = _load_image(path)

    override, why = resolve_scale(
        src.project, src.project.pixel_width, src.project.pixel_height,
        mm_per_px=mm_per_px, dpi=dpi, width=width, height=height)
    if override is not None:
        src.project.calibration.mm_per_pixel = override
        src.calibrated_by = why
    return src


def source_from_project(project, image_bgr=None, path=None, kind=None):
    """Wrap an ALREADY-OPEN project as a Source, with no file access.

    This is the seam the GUI calls through. A Source is only a project
    plus optional pixels, so the window's live project is already one;
    without this, the GUI would have to save to a temp file just to
    reach the job layer, and the two front ends would keep their own
    copies of every rule (which is how Export SVG and Print Tiles drifted
    apart).
    """
    if kind is None:
        kind = "image" if image_bgr is not None else "svg"
    return Source(kind, path, project, image_bgr=image_bgr)


def project_from_svg(path):
    """Read an SVG into a fresh Project sized in pixels.

    One place for the size arithmetic: an SVG carries mm, the model
    works in pixels, and mm_per_pixel ties them together. This lived in
    three copies (_load_svg, import_svg and the GUI's File > Open SVG),
    which is three chances for the rounding to disagree about how big
    the sheet is.

    Returns (project, size_mm, mm_per_pixel). Raises JobError.
    """
    from . import svg_import as svgi
    try:
        objects, size_mm, mpp = svgi.import_svg(path)
    except svgi.SvgImportError as exc:
        raise JobError(str(exc))
    project = Project()
    project.objects = objects
    project.calibration.mm_per_pixel = mpp
    project.pixel_width = max(1, int(round(size_mm[0] / mpp)))
    project.pixel_height = max(1, int(round(size_mm[1] / mpp)))
    project.source_image_path = path
    return project, size_mm, mpp


def _load_svg(path):
    project, _size_mm, _mpp = project_from_svg(path)
    return Source("svg", path, project,
                  calibrated_by="SVG document units")


def _load_pdf(path, page_number, render_dpi):
    from . import pdf_import
    err = pdf_import.availability_error()
    if err:
        raise JobError(err)
    sizes = pdf_import.page_sizes_mm(path)
    if not sizes:
        raise JobError("PDF has no pages: %s" % (path,))
    if not (1 <= page_number <= len(sizes)):
        raise JobError("--pdf-page %d out of range (1..%d)"
                       % (page_number, len(sizes)))
    image = pdf_import.render_page(path, page_number - 1, dpi=render_dpi)
    project = Project()
    project.pixel_height, project.pixel_width = image.shape[:2]
    project.source_image_path = path
    # A PDF page has a known physical size, so the render DPI IS the scale.
    project.calibration.mm_per_pixel = 25.4 / float(render_dpi)
    return Source("pdf", path, project, image_bgr=image,
                  calibrated_by="PDF page at %g DPI" % render_dpi)


def _load_project(path):
    try:
        project = project_io.load_project(path)
    except project_io.ProjectIOError as exc:
        raise JobError(str(exc))
    except (IOError, OSError) as exc:
        raise JobError(str(exc))
    image = None
    img_path = project.source_image_path
    # A project stores its image by path; missing is not fatal unless a
    # job actually needs the pixels (vector-only jobs do not).
    if img_path and os.path.isfile(img_path):
        try:
            image = image_io.load_image(img_path).data
        except IOError:
            image = None
    why = ("saved project calibration"
           if project.calibration.is_calibrated else None)
    return Source("project", path, project, image_bgr=image,
                  calibrated_by=why)


def _load_image(path):
    try:
        loaded = image_io.load_image(path)
    except IOError as exc:
        raise JobError(str(exc))
    project = Project()
    project.set_source_image(loaded)
    return Source("image", path, project, image_bgr=loaded.data)


def _as_mm(value, flag):
    """A length flag that may carry a unit; plain numbers are mm."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return calib.parse_length_mm(value)
    except ValueError as exc:
        raise JobError("%s: %s" % (flag, exc))


# ----- info ---------------------------------------------------------------

class InfoResult(object):
    """What `info` found. Plain data, so callers can format it freely."""

    def __init__(self, source):
        self.path = source.path
        self.kind = source.kind
        self.pixel_width = source.project.pixel_width
        self.pixel_height = source.project.pixel_height
        self.mm_per_pixel = source.project.calibration.mm_per_pixel
        self.calibrated_by = source.calibrated_by
        self.size_mm = source.size_mm
        self.object_count = len(source.project.objects)
        self.contour_count = sum(len(o.contours)
                                 for o in source.project.objects)
        self.point_count = sum(len(c.points)
                               for o in source.project.objects
                               for c in o.contours)
        self.object_names = [o.name for o in source.project.objects]
        self.page_count = len(source.project.pages)
        self.has_image = source.image_bgr is not None
        self.display_unit = source.project.calibration.display_unit
        self.content_size_mm = None
        self.tile_plan = None

    @property
    def is_calibrated(self):
        return bool(self.mm_per_pixel)


def info(path, page=None, landscape=None, margin=None, overlap=None,
         **scale_kw):
    """Describe an input: size, scale, contents, and the tile plan.

    The tile plan is included whenever the source is calibrated, because
    "how many sheets will this take?" is the question `info` exists to
    answer before committing to a tile run.
    """
    source = load_source(path, **scale_kw)
    result = InfoResult(source)
    project = source.project

    if result.is_calibrated and project.objects:
        try:
            bbox = svg_export.content_bbox_px(project)
            mpp = result.mm_per_pixel
            result.content_size_mm = (bbox.width * mpp, bbox.height * mpp)
        except svg_export.ExportError:
            pass

    if result.is_calibrated:
        content = result.content_size_mm or result.size_mm
        if content:
            t = project.tiling
            page_name = page or t.get("page", "Letter")
            land = (t.get("landscape", False) if landscape is None
                    else landscape)
            marg = t.get("margin_mm", 6.0) if margin is None else margin
            over = t.get("overlap_mm", 10.0) if overlap is None else overlap
            margin_mm = _as_mm(marg, "--margin")
            overlap_mm = _as_mm(over, "--overlap")
            # Content is padded by the project margin on every side, as
            # build_content does, or the sheet count comes out short.
            pad = 2.0 * project.margin_mm
            try:
                plan = tiling.plan_tiles(content[0] + pad, content[1] + pad,
                                         page_name, land, margin_mm,
                                         overlap_mm)
            except ValueError as exc:
                raise JobError(str(exc))
            result.tile_plan = {
                "page": page_name, "landscape": land,
                "margin_mm": margin_mm, "overlap_mm": overlap_mm,
                "cols": plan["ncols"], "rows": plan["nrows"],
                "sheets": plan["ncols"] * plan["nrows"],
                "page_w_mm": plan["page_w"], "page_h_mm": plan["page_h"],
            }
    return result


# ----- tile ---------------------------------------------------------------

class TileResult(object):
    def __init__(self, names, out_dir, plan, size_mm):
        self.names = list(names)
        self.out_dir = out_dir
        self.plan = dict(plan)
        self.size_mm = size_mm

    @property
    def sheets(self):
        return len(self.names)


def tile(path, out_dir=".", page="Letter", landscape=False, margin="6mm",
         overlap="10mm", embed_image=None, filled=False, scale_percent=100,
         base_name=None, dry_run=False, progress=None, **scale_kw):
    """Split an input into page-size SVG tiles and write them out.

    `embed_image` defaults to True for raster sources (the photo IS the
    content) and False for vector ones. `scale_percent` reproduces the
    tiling panel scale control. With `dry_run` nothing is written and
    only the plan comes back.
    """
    source = load_source(path, **scale_kw)
    project = source.project
    if not project.calibration.is_calibrated:
        raise JobError(
            "no scale for %s: calibrate it, or supply one "
            "(SVG and PDF inputs carry their own size)"
            % os.path.basename(path))
    if not project.objects and source.image_bgr is None:
        raise JobError("nothing to tile: no traced objects and no image")

    margin_mm = _as_mm(margin, "--margin")
    overlap_mm = _as_mm(overlap, "--overlap")
    if embed_image is None:
        embed_image = source.image_bgr is not None
    if embed_image and source.image_bgr is None:
        raise JobError("cannot embed the image: this input has "
                       "no pixels")

    mpp = project.calibration.mm_per_pixel
    if scale_percent is not None and float(scale_percent) != 100.0:
        if float(scale_percent) <= 0.0:
            raise JobError("--scale must be positive")
        mpp = mpp * float(scale_percent) / 100.0

    if progress:
        progress("planning tiles for %s" % os.path.basename(path))
    try:
        tiles = tiling.build_tiles(
            project,
            image_bgr=source.image_bgr if embed_image else None,
            page=page, landscape=landscape,
            margin_mm=margin_mm, overlap_mm=overlap_mm,
            embed_photo=embed_image, filled=filled,
            base_name=base_name or source.base_name,
            mm_per_pixel=mpp)
    except svg_export.ExportError as exc:
        raise JobError(str(exc))
    except ValueError as exc:
        raise JobError(str(exc))

    plan = {"page": page, "landscape": landscape, "margin_mm": margin_mm,
            "overlap_mm": overlap_mm, "embed_image": embed_image,
            "filled": filled, "scale_percent": float(scale_percent or 100),
            "mm_per_pixel": mpp}
    size_mm = (project.pixel_width * mpp, project.pixel_height * mpp)

    if dry_run:
        return TileResult([n for n, _svg in tiles], None, plan, size_mm)

    # Re-running overwrites the same tile names rather than
    # accumulating variants, so a repeated run converges.
    try:
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except OSError as exc:
        raise JobError("cannot create %s: %s" % (out_dir, exc))
    written = []
    for name, text in tiles:
        dest = os.path.join(out_dir or ".", name)
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)
        except (IOError, OSError) as exc:
            raise JobError("cannot write %s: %s" % (dest, exc))
        written.append(name)
        if progress:
            progress("wrote %s" % name)
    return TileResult(written, out_dir or ".", plan, size_mm)


# ----- export_svg ---------------------------------------------------------

class ExportResult(object):
    def __init__(self, path, size_mm, objects, bytes_written):
        self.path = path
        self.size_mm = size_mm
        self.objects = objects
        self.bytes_written = bytes_written


def export_svg(path, out_path=None, embed_image=None, filled=False,
               inkscape=False, downscale_max=None, progress=None,
               **scale_kw):
    """Write one true-scale SVG of the whole drawing.

    The headless form of File > Export SVG. `embed_image` defaults to
    True for raster sources and False for vector ones, matching what
    the source actually has to embed.
    """
    return export_svg_source(
        load_source(path, **scale_kw), out_path=out_path,
        embed_image=embed_image, filled=filled, inkscape=inkscape,
        downscale_max=downscale_max, progress=progress)


def export_svg_source(source, out_path=None, embed_image=None, filled=False,
                      inkscape=False, downscale_max=None, annotations=None,
                      progress=None):
    """Build and write the SVG for an open Source. The GUI calls this.

    Everything from the calibration check down is shared with the
    command line; only the dialogs differ. `annotations` carries the
    measurement layer, which has no headless equivalent yet.
    """
    project = source.project
    path = source.path or "drawing"
    if not project.calibration.is_calibrated:
        raise JobError(
            "no scale for %s: calibrate it, or supply one "
            "(SVG and PDF inputs carry their own size)"
            % os.path.basename(path))
    if embed_image is None:
        embed_image = source.image_bgr is not None
    if embed_image and source.image_bgr is None:
        raise JobError("cannot embed the image: this input has "
                       "no pixels")
    if not project.objects and not embed_image:
        raise JobError("nothing to export: no traced objects, and the "
                       "image is not being embedded")

    if out_path is None:
        out_path = os.path.join(os.path.dirname(path) or ".",
                                source.base_name + ".svg")
    if progress:
        progress("building %s" % os.path.basename(out_path))
    try:
        text = svg_export.build_svg(
            project, image_bgr=source.image_bgr if embed_image else None,
            embed_photo=embed_image, downscale_max=downscale_max,
            filled=filled, inkscape=inkscape, annotations=annotations)
    except svg_export.ExportError as exc:
        raise JobError(str(exc))

    parent = os.path.dirname(out_path)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except (IOError, OSError) as exc:
        raise JobError("cannot write %s: %s" % (out_path, exc))
    return ExportResult(out_path, source.size_mm, len(project.objects),
                        len(text.encode("utf-8")))


# ----- import_svg ---------------------------------------------------------

class ImportResult(object):
    def __init__(self, path, size_mm, mm_per_pixel, objects, contours,
                 points, names):
        self.path = path
        self.size_mm = size_mm
        self.mm_per_pixel = mm_per_pixel
        self.objects = objects
        self.contours = contours
        self.points = points
        self.names = list(names)


def import_svg(path, out_path=None, progress=None):
    """Read an SVG and write it as a PageWright project.

    Turns existing vector art into a job the rest of the app (and the
    other subcommands) can work on, which is otherwise only reachable
    through File > Open SVG.
    """
    if not path or not os.path.isfile(path):
        raise JobError("file not found: %s" % (path,))
    if progress:
        progress("reading %s" % os.path.basename(path))
    project, size_mm, mpp = project_from_svg(path)

    base = os.path.splitext(os.path.basename(path))[0]
    if out_path is None:
        out_path = os.path.join(os.path.dirname(path) or ".",
                                base + ".tiproj.json")
    parent = os.path.dirname(out_path)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        project_io.save_project(project, out_path)
    except (IOError, OSError) as exc:
        raise JobError("cannot write %s: %s" % (out_path, exc))
    if progress:
        progress("wrote %s" % os.path.basename(out_path))
    objects = project.objects
    return ImportResult(
        out_path, size_mm, mpp, len(objects),
        sum(len(o.contours) for o in objects),
        sum(len(c.points) for o in objects for c in o.contours),
        [o.name for o in objects])


# ----- flatten ------------------------------------------------------------

class FlattenResult(object):
    def __init__(self):
        self.written = []        # [(source_path, out_path)]
        self.failed = []         # [(source_path, reason)]
        self.dpi = None

    @property
    def pages(self):
        return len(self.written)


def flatten_page(bgr, seed_model=None, seed_wh=None, spread=False,
                 refine=True):
    """Flatten ONE page. Returns (images, model, wh).

    The seam both front ends share. `seed_model` is the previous
    page's confirmed outline: consecutive pages of a book share a
    binding and opening angle, so propagating it and running only the
    cheap text-line refinement is faster and keeps the geometry
    consistent down a run. With no seed the outline is detected from
    scratch (or started from a default spread).

    The returned model seeds the NEXT page, which is why it comes back
    rather than staying private: the GUI's Flatten Checked and the
    headless `flatten` both carry it forward, and when they each did
    their own version of this loop they drifted apart.
    """
    import cv2
    from . import batch as batch_mod
    from . import dewarp

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    wh = (bgr.shape[1], bgr.shape[0])
    if seed_model is not None:
        model = batch_mod.propagate_model(seed_model, seed_wh, wh)
    elif spread:
        model = dewarp.default_spread_model(gray.shape[1], gray.shape[0])
    else:
        model = dewarp.detect_page(gray)
    if refine:
        model = batch_mod.seed_and_refine(gray, model)
    return batch_mod.flatten_with_model(bgr, model), model, wh


def flatten(paths, out_dir=".", dpi=300, spread=False, refine=True,
            propagate=True, dry_run=False, progress=None):
    """Detect each page's outline, flatten it, and write the result.

    This is the batch book-scan path and the reason a headless mode is
    worth having: `dewarp.detect_page` needs no human input, so a whole
    folder of photographs can be straightened unattended.

    `propagate` seeds each page from the previous page's confirmed
    model (consecutive pages of one book share a binding and opening
    angle) and runs only the cheap text-line refinement, which is both
    faster and keeps geometry consistent down the run. A page that
    fails is recorded and the run carries on - one bad photo should not
    abandon a book.
    """
    import cv2                      # still used, for imwrite

    result = FlattenResult()
    result.dpi = dpi
    if not paths:
        raise JobError("no input images given")
    if dpi <= 0:
        raise JobError("--dpi must be positive")

    prev_model = None
    prev_wh = None
    for path in paths:
        name = os.path.basename(path)
        if not os.path.isfile(path):
            result.failed.append((path, "file not found"))
            continue
        try:
            loaded = image_io.load_image(path)
        except IOError as exc:
            result.failed.append((path, str(exc)))
            continue
        bgr = loaded.data
        if progress:
            progress("detecting %s" % name)
        seed = prev_model if propagate else None
        try:
            images, model, wh = flatten_page(
                bgr, seed_model=seed, seed_wh=prev_wh, spread=spread,
                refine=refine)
        except Exception as exc:       # one bad photo must not stop a book
            result.failed.append((path, "flatten failed: %s" % (exc,)))
            continue
        prev_model, prev_wh = model, wh

        stem = os.path.splitext(name)[0]
        for k, out in enumerate(images):
            tag = "" if len(images) == 1 else "-%s" % "LR"[k:k + 1]
            dest = os.path.join(out_dir or ".",
                                "%s%s_flat.png" % (stem, tag))
            if dry_run:
                result.written.append((path, dest))
                continue
            try:
                if out_dir and not os.path.isdir(out_dir):
                    os.makedirs(out_dir)
                if not cv2.imwrite(dest, out):
                    raise IOError("cv2 could not write the file")
            except (IOError, OSError) as exc:
                result.failed.append((path, "cannot write %s: %s"
                                      % (dest, exc)))
                continue
            result.written.append((path, dest))
            if progress:
                progress("wrote %s" % os.path.basename(dest))
    if not result.written and result.failed:
        raise JobError("every page failed; first reason: %s"
                       % result.failed[0][1])
    return result


# ----- ocr ----------------------------------------------------------------

class OcrResult(object):
    def __init__(self, path, text, out_path=None):
        self.path = path
        self.text = text
        self.out_path = out_path

    @property
    def characters(self):
        return len(self.text)

    @property
    def words(self):
        return len(self.text.split())


def ocr(path, out_path=None, lang="eng", region=None, progress=None,
        **scale_kw):
    """Read the text on an image (or a saved project's image).

    Not split into an `_source` half like export_svg: the GUI's OCR
    stage already shares all the logic there is (`core/ocr.py`) and
    renders into a text widget rather than writing a file, so a Source
    wrapper would only add a layer. See PLAN sec. 16.5.
    """
    from . import ocr as ocr_mod
    err = ocr_mod.availability_error()
    if err:
        raise JobError(err)
    source = load_source(path, **scale_kw)
    if source.image_bgr is None:
        raise JobError("OCR needs a raster source; %s has no pixels"
                       % os.path.basename(path))
    if progress:
        progress("reading text from %s" % os.path.basename(path))
    try:
        text = ocr_mod.image_to_text(source.image_bgr, region_px=region,
                                     lang=lang)
    except RuntimeError as exc:
        raise JobError(str(exc))

    if out_path:
        parent = os.path.dirname(out_path)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except (IOError, OSError) as exc:
            raise JobError("cannot write %s: %s" % (out_path, exc))
        if progress:
            progress("wrote %s" % os.path.basename(out_path))
    return OcrResult(path, text, out_path)


# ----- find_parts ---------------------------------------------------------

class FindPartsResult(object):
    def __init__(self, path, candidates, mm_per_pixel, out_path=None):
        self.path = path
        self.candidates = list(candidates)
        self.mm_per_pixel = mm_per_pixel
        self.out_path = out_path

    @property
    def count(self):
        return len(self.candidates)


def find_parts(path, trace=False, gap=None, min_area=None, max_aspect=None,
               min_fill=None, limit=0, out_path=None, tolerance_mm=None,
               progress=None, **scale_kw):
    """Locate the closed outlines on a sheet, with no seed marks.

    The piece that lets an agent work a plan on its own: it says WHERE
    the parts are, so the caller can trace each one by its bounding box
    instead of needing a human to paint inside and outside first.

    With `out_path` the traced outlines are saved as a project, one
    object per part, ready for `tile` or `export-svg`.
    """
    from . import find_parts as fp

    source = load_source(path, **scale_kw)
    project = source.project
    if source.image_bgr is None:
        raise JobError("find-parts needs a raster source; %s has no pixels"
                       % os.path.basename(path))
    mpp = project.calibration.mm_per_pixel
    if not mpp:
        raise JobError(
            "no scale for %s: calibrate it, or supply one. Every "
            "threshold here is a real-world size, so the sheet has to "
            "be to scale first."
            % os.path.basename(path))

    kw = {}
    if gap is not None:
        kw["gap_mm"] = _as_mm(gap, "--gap")
    if min_area is not None:
        kw["min_area_mm2"] = float(min_area)
    if max_aspect is not None:
        kw["max_aspect"] = float(max_aspect)
    if min_fill is not None:
        kw["min_fill"] = float(min_fill)

    want_trace = trace or bool(out_path)
    try:
        cands = fp.find_parts(source.image_bgr, mpp, trace=want_trace,
                              tolerance_mm=tolerance_mm, limit=limit,
                              progress=progress, **kw)
    except ValueError as exc:
        raise JobError(str(exc))

    result = FindPartsResult(path, cands, mpp)
    if out_path:
        from ..model import Contour, TracedObject
        objects = []
        for k, cand in enumerate(cands):
            if not cand.outline:
                continue
            obj = TracedObject(name="Part %d" % (k + 1))
            obj.contours = [Contour(points=list(cand.outline))]
            objects.append(obj)
        if not objects:
            raise JobError("nothing traceable found to save")
        project.objects = objects
        parent = os.path.dirname(out_path)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            project_io.save_project(project, out_path)
        except (IOError, OSError) as exc:
            raise JobError("cannot write %s: %s" % (out_path, exc))
        result.out_path = out_path
        if progress:
            progress("wrote %s" % os.path.basename(out_path))
    return result


# ----- export_recipe ------------------------------------------------------

class RecipeResult(object):
    def __init__(self, path, out_path, parts, skipped,
                 assembly_path=None, assemblies=None):
        self.path = path
        self.out_path = out_path
        self.parts = parts
        self.skipped = list(skipped)
        self.assembly_path = assembly_path
        self.assemblies = dict(assemblies or {})


def export_recipe(path, out_path=None, design=None, plane=None,
                  thickness=None, as_drawn=False, progress=None,
                  **scale_kw):
    """Write the traced parts as a MyArchive recipe.json.

    The headless end of the plan-to-CAD route: SolidCraft, Fusion and
    OpenSCAD all rebuild from a recipe, so this is where a scanned
    plan leaves PageWright and becomes solid parts.
    """
    from . import recipe_export as rx
    source = load_source(path, **scale_kw)
    project = source.project
    if thickness:
        project.sheet_thickness_mm = _as_mm(thickness, "--thickness")
    if out_path is None:
        out_path = os.path.join(os.path.dirname(path) or ".",
                                source.base_name + "_recipe",
                                "recipe.json")
    if progress:
        progress("building %s" % os.path.basename(out_path))
    try:
        written, parts, skipped, doc = rx.write_recipe(
            project, out_path, design=design or source.base_name,
            default_plane=(plane or rx.DEFAULT_PLANE).upper(),
            as_drawn=as_drawn)
    except rx.RecipeError as exc:
        raise JobError(str(exc))
    if progress:
        progress("wrote %d part(s)" % parts)

    # The component tree cannot go in the recipe (one component string
    # per sketch, and the importer moves every body to root), so it is
    # a companion script to run after the import.
    groups = (doc.get("_pagewright") or {}).get("assemblies") or {}
    asm_path = None
    if groups:
        import json
        asm_path = os.path.join(os.path.dirname(written) or ".",
                                "assembly.json")
        try:
            with open(asm_path, "w", encoding="utf-8") as fh:
                json.dump(rx.assembly_script(doc), fh, indent=1)
                fh.write(chr(10))
        except (IOError, OSError) as exc:
            raise JobError("cannot write %s: %s" % (asm_path, exc))
        if progress:
            progress("wrote %d assembly component(s)" % len(groups))
    return RecipeResult(path, written, parts, skipped, asm_path, groups)


# ----- import_dxf ---------------------------------------------------------

class DxfResult(object):
    def __init__(self, path, out_path, loops, points, size_mm, info):
        self.path = path
        self.out_path = out_path
        self.loops = loops
        self.points = points
        self.size_mm = size_mm
        self.info = dict(info)


def import_dxf(path, out_path=None, name=None, px_per_mm=10.0,
               detail_mm=None, progress=None):
    """Read a DXF of flat parts back in as a project.

    The return leg: a part modified in CAD comes back as DXF, and this
    turns it into a job the tiler can print 1:1 so the change can be
    cut by hand as well as by machine.

    The largest closed loop becomes the outline and the rest become
    holes, which is what a flat cut part looks like.

    `detail_mm` is how far a flattened curve may deviate. The default
    (0.2 mm) suits a plan drawn with a French curve at this size and
    is finer than a laser kerf; tighten it only when the part really
    is that precise.
    """
    from . import dxf_import as dxf
    from ..model import Contour, TracedObject
    if not path or not os.path.isfile(path):
        raise JobError("file not found: %s" % (path,))
    if px_per_mm <= 0.0:
        raise JobError("--px-per-mm must be positive")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError) as exc:
        raise JobError("cannot read %s: %s" % (path, exc))
    if progress:
        progress("reading %s" % os.path.basename(path))
    try:
        loops, info = dxf.read_dxf(text, mm_per_pixel=1.0 / px_per_mm,
                                   sagitta_mm=detail_mm)
    except dxf.DxfImportError as exc:
        raise JobError(str(exc))

    xs = [p[0] for loop in loops for p in loop]
    ys = [p[1] for loop in loops for p in loop]
    minx, miny = min(xs), min(ys)
    project = Project()
    project.calibration.mm_per_pixel = 1.0 / px_per_mm
    project.source_image_path = path

    obj = TracedObject(name=name or os.path.splitext(
        os.path.basename(path))[0])
    for i, loop in enumerate(loops):
        # y up in CAD, down in image space; shift so the part starts at 0
        pts = [(x - minx, (max(ys) - y)) for x, y in loop]
        obj.contours.append(Contour(points=pts, closed=True,
                                    role="outer" if i == 0 else "hole"))
    project.objects = [obj]
    project.pixel_width = max(1, int(round(max(xs) - minx)))
    project.pixel_height = max(1, int(round(max(ys) - miny)))

    size_mm = (project.pixel_width / px_per_mm,
               project.pixel_height / px_per_mm)
    if out_path:
        parent = os.path.dirname(out_path)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            project_io.save_project(project, out_path)
        except (IOError, OSError, project_io.ProjectIOError) as exc:
            raise JobError("cannot write %s: %s" % (out_path, exc))
        if progress:
            progress("wrote %s" % os.path.basename(out_path))
    return DxfResult(path, out_path, len(loops),
                     sum(len(c.points) for c in obj.contours),
                     size_mm, info)
