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


def _load_svg(path):
    from . import svg_import
    try:
        objects, size_mm, mpp = svg_import.import_svg(path)
    except svg_import.SvgImportError as exc:
        raise JobError(str(exc))
    project = Project()
    project.objects = objects
    project.calibration.mm_per_pixel = mpp
    project.pixel_width = max(1, int(round(size_mm[0] / mpp)))
    project.pixel_height = max(1, int(round(size_mm[1] / mpp)))
    project.source_image_path = path
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
            "no scale for %s - give --dpi, --mm-per-px, --width or "
            "--height (SVG and PDF inputs carry their own size)"
            % os.path.basename(path))
    if not project.objects and source.image_bgr is None:
        raise JobError("nothing to tile: no traced objects and no image")

    margin_mm = _as_mm(margin, "--margin")
    overlap_mm = _as_mm(overlap, "--overlap")
    if embed_image is None:
        embed_image = source.image_bgr is not None
    if embed_image and source.image_bgr is None:
        raise JobError("--embed-image needs a raster source "
                       "(this input has no pixels)")

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
