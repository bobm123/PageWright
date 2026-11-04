"""Command-line front end: the headless half of PageWright.

`core/jobs.py` holds the work; this module only turns argv into job
arguments and job results into text. Keeping the split strict means the
GUI and the CLI can call the same job with the same meaning - the thing
that makes a scriptable app scriptable, rather than two code paths that
drift.

    pagewright info pattern.svg
    pagewright tile pattern.svg --page A4 --out ./tiles
    pagewright tile scan.jpg --dpi 300 --overlap 15mm
    pagewright tile photo.png --width 300mm --no-embed-image
    pagewright export-svg job.tiproj.json --inkscape
    pagewright import-svg pattern.svg -o pattern.tiproj.json
    pagewright flatten scans/*.jpg --out ./flat
    pagewright ocr page.png --out page.txt
    pagewright find-parts plan.png --dpi 300 --trace
    pagewright export-recipe parts.tiproj.json --thickness 1/16in
    pagewright import-dxf former.dxf -o former.tiproj.json

Qt is never imported here. `main.py` dispatches to this module BEFORE it
touches PySide6, so these commands run on a machine with no display.

Exit codes: 0 success, 1 a JobError (bad input, no scale, unwritable
output), 2 argparse usage errors.
"""

import argparse
import os
import sys

from .core import jobs
from .core.tiling import PAGE_SIZES_MM

EXIT_OK = 0
EXIT_ERROR = 1

# Subcommands this module provides. `main.py` checks membership to
# decide between CLI and GUI, so a new command only needs adding here.
COMMANDS = ("info", "tile", "export-svg", "import-svg",
            "flatten", "ocr", "find-parts", "export-recipe",
            "import-dxf")


def _add_scale_flags(parser):
    """The four ways to supply a scale, which are mutually exclusive.

    A photo carries no real-world size (in the GUI that is the
    two-click Calibrate gesture), so a headless job has to be told.
    SVG and PDF inputs already know, and these then override.
    """
    group = parser.add_argument_group(
        "scale",
        "required for image inputs; SVG/PDF/project inputs already "
        "know their size (these override it)")
    excl = group.add_mutually_exclusive_group()
    excl.add_argument("--dpi", type=float, metavar="N",
                      help="the image was scanned at N dots per inch")
    excl.add_argument("--mm-per-px", type=float, metavar="MM",
                      help="millimetres per pixel, directly")
    excl.add_argument("--width", metavar="LEN",
                      help="intended real width of the whole image, "
                           "e.g. 300mm, 29.7cm, 11.7in")
    excl.add_argument("--height", metavar="LEN",
                      help="intended real height of the whole image")
    group.add_argument("--pdf-page", type=int, default=1, metavar="N",
                       help="which PDF page to use (1-based, default 1)")
    group.add_argument("--pdf-dpi", type=float, default=300.0, metavar="N",
                       help="DPI to rasterize a PDF page at (default 300)")


def _scale_kwargs(args):
    return {"mm_per_px": args.mm_per_px, "dpi": args.dpi,
            "width": args.width, "height": args.height,
            "pdf_page": args.pdf_page, "pdf_dpi": args.pdf_dpi}


def _add_page_flags(parser, with_defaults):
    """Page/layout flags. `info` leaves them unset so it can report the
    saved tiling of a project; `tile` needs concrete defaults."""
    parser.add_argument("--page", choices=sorted(PAGE_SIZES_MM),
                        default="Letter" if with_defaults else None,
                        help="sheet size (default Letter)")
    parser.add_argument("--landscape", action="store_true",
                        default=False if with_defaults else None,
                        help="rotate the sheet")
    parser.add_argument("--margin", metavar="LEN",
                        default="6mm" if with_defaults else None,
                        help="unprintable page margin (default 6mm)")
    parser.add_argument("--overlap", metavar="LEN",
                        default="10mm" if with_defaults else None,
                        help="overlap between sheets (default 10mm)")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pagewright",
        description="Trace object outlines from a photo and export "
                    "true-scale SVG. With no subcommand, launches the GUI.")
    parser.add_argument("--debug", action="store_true",
                        help="verbose diagnostics on stderr "
                             "(also PAGEWRIGHT_DEBUG=1)")
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_info = subs.add_parser(
        "info", help="describe an input and how many sheets it would need",
        description="Report pixel size, real-world size, scale and "
                    "contents, plus the tile plan when the input is to "
                    "scale. Reads only - writes nothing.")
    p_info.add_argument("file", metavar="FILE",
                        help="image, SVG, PDF or .tiproj.json project")
    p_info.add_argument("--json", action="store_true",
                        help="machine-readable output")
    _add_page_flags(p_info, with_defaults=False)
    _add_scale_flags(p_info)

    p_tile = subs.add_parser(
        "tile", help="split an input into page-size SVG tiles",
        description="Write one page-size SVG per sheet, at true scale, "
                    "with registration marks - the headless form of "
                    "Export Tiles.")
    p_tile.add_argument("file", metavar="FILE",
                        help="image, SVG, PDF or .tiproj.json project")
    p_tile.add_argument("-o", "--out", default=".", metavar="DIR",
                        help="directory for the tile SVGs (default .)")
    p_tile.add_argument("--base-name", metavar="NAME",
                        help="tile filename stem (default: from the input)")
    p_tile.add_argument("--scale", type=float, default=100.0, metavar="PCT",
                        help="resize the output to PCT percent (default 100)")
    p_tile.add_argument("--filled", action="store_true",
                        help="fill shapes instead of outlining them")
    embed = p_tile.add_mutually_exclusive_group()
    embed.add_argument("--embed-image", dest="embed_image",
                       action="store_true", default=None,
                       help="embed the photo in each tile (default for "
                            "image inputs)")
    embed.add_argument("--no-embed-image", dest="embed_image",
                       action="store_false",
                       help="vector only, no photo")
    p_tile.add_argument("-n", "--dry-run", action="store_true",
                        help="report the plan without writing files")
    p_tile.add_argument("-q", "--quiet", action="store_true",
                        help="only report errors")
    _add_page_flags(p_tile, with_defaults=True)
    _add_scale_flags(p_tile)

    p_exp = subs.add_parser(
        "export-svg", help="write one true-scale SVG of the whole drawing",
        description="The headless form of Export SVG: a single mm-sized "
                    "document, not page tiles.")
    p_exp.add_argument("file", metavar="FILE",
                       help="image, SVG, PDF or .tiproj.json project")
    p_exp.add_argument("-o", "--out", metavar="FILE",
                       help="output path (default: beside the input)")
    p_exp.add_argument("--inkscape", action="store_true",
                       help="Inkscape flavour: named layers, mm units")
    p_exp.add_argument("--filled", action="store_true",
                       help="fill shapes instead of outlining them")
    p_exp.add_argument("--downscale-max", type=int, metavar="PX",
                       help="cap the embedded image longest side")
    emb = p_exp.add_mutually_exclusive_group()
    emb.add_argument("--embed-image", dest="embed_image",
                     action="store_true", default=None,
                     help="embed the photo (default for image inputs)")
    emb.add_argument("--no-embed-image", dest="embed_image",
                     action="store_false", help="vector only")
    p_exp.add_argument("-q", "--quiet", action="store_true",
                       help="only report errors")
    _add_scale_flags(p_exp)

    p_imp = subs.add_parser(
        "import-svg", help="turn existing vector art into a project",
        description="Read an SVG and save it as a .tiproj.json the rest "
                    "of the app and the other subcommands can work on. "
                    "The document carries its own size, so the result is "
                    "already to scale.")
    p_imp.add_argument("file", metavar="FILE", help="an SVG drawing")
    p_imp.add_argument("-o", "--out", metavar="FILE",
                       help="output project path (default: beside it)")
    p_imp.add_argument("-q", "--quiet", action="store_true",
                       help="only report errors")

    p_flat = subs.add_parser(
        "flatten", help="straighten photographed pages, unattended",
        description="Detect each page outline, flatten it and write a "
                    "PNG. Needs no human input, so a folder of book "
                    "photographs can be processed in one go.")
    p_flat.add_argument("files", metavar="FILE", nargs="+",
                        help="page photographs")
    p_flat.add_argument("-o", "--out", default=".", metavar="DIR",
                        help="output directory (default .)")
    p_flat.add_argument("--dpi", type=float, default=300.0, metavar="N",
                        help="output resolution (default 300)")
    p_flat.add_argument("--spread", action="store_true",
                        help="two pages per photo, split at the spine")
    p_flat.add_argument("--no-refine", dest="refine", action="store_false",
                        help="skip the text-line refinement pass")
    p_flat.add_argument("--no-propagate", dest="propagate",
                        action="store_false",
                        help="detect every page from scratch instead of "
                             "seeding from the previous one")
    p_flat.add_argument("-n", "--dry-run", action="store_true",
                        help="report what would be written")
    p_flat.add_argument("-q", "--quiet", action="store_true",
                        help="only report errors")

    p_ocr = subs.add_parser(
        "ocr", help="read the text on a page",
        description="Run Tesseract over an image and print or save the "
                    "text. Needs pytesseract and the Tesseract engine.")
    p_ocr.add_argument("file", metavar="FILE",
                       help="image, PDF or .tiproj.json project")
    p_ocr.add_argument("-o", "--out", metavar="FILE",
                       help="write the text here instead of stdout")
    p_ocr.add_argument("--lang", default="eng", metavar="CODE",
                       help="Tesseract language (default eng)")
    p_ocr.add_argument("--region", nargs=4, type=float,
                       metavar=("X0", "Y0", "X1", "Y1"),
                       help="only read this rectangle, in image pixels")
    p_ocr.add_argument("-q", "--quiet", action="store_true",
                       help="only report errors")
    _add_scale_flags(p_ocr)

    p_find = subs.add_parser(
        "find-parts", help="locate the drawn parts on a sheet",
        description="Find the closed outlines on a plan without any "
                    "seed marks, by flooding the paper inward from the "
                    "page border: whatever it cannot reach is walled in "
                    "by a part outline. Reports where each one is, so a "
                    "caller can trace it by its bounding box instead of "
                    "painting inside and outside first. Parts whose "
                    "outline is crossed by a dimension line never seal "
                    "and will not be found.")
    p_find.add_argument("file", metavar="FILE",
                        help="image, PDF or .tiproj.json project")
    p_find.add_argument("--trace", action="store_true",
                        help="also extract each outline as a polygon")
    p_find.add_argument("-o", "--out", metavar="FILE",
                        help="save the traced parts as a project "
                             "(implies --trace)")
    p_find.add_argument("--json", action="store_true",
                        help="machine-readable output")
    p_find.add_argument("--gap", metavar="LEN",
                        help="widest break in an outline to bridge "
                             "(default 0.3mm)")
    p_find.add_argument("--min-area", type=float, metavar="MM2",
                        help="smallest part to report (default 150 mm2)")
    p_find.add_argument("--max-aspect", type=float, metavar="R",
                        help="reject shapes longer than this (default 4)")
    p_find.add_argument("--min-fill", type=float, metavar="F",
                        help="reject hollow frames below this (default "
                             "0.35)")
    p_find.add_argument("--limit", type=int, default=0, metavar="N",
                        help="report only the N largest")
    p_find.add_argument("-q", "--quiet", action="store_true",
                        help="only report errors")
    _add_scale_flags(p_find)

    p_rec = subs.add_parser(
        "export-recipe", help="write the parts as a CAD recipe.json",
        description="Write the traced parts as a MyArchive "
                    "recipe.json: one sketch and one extrude per "
                    "part, placed by the plane, station and "
                    "thickness recorded on each. SolidCraft, Fusion "
                    "and OpenSCAD all rebuild from this format. A "
                    "part with no thickness is reported, not "
                    "guessed at.")
    p_rec.add_argument("file", metavar="FILE",
                       help="a .tiproj.json project, or an SVG")
    p_rec.add_argument("-o", "--out", metavar="FILE",
                       help="recipe.json to write (default: "
                            "<name>_recipe/recipe.json beside the input)")
    p_rec.add_argument("--design", metavar="NAME",
                       help="design name recorded in the recipe")
    p_rec.add_argument("--thickness", metavar="LEN",
                       help="sheet stock for parts with no thickness "
                            "of their own (accepts 1/16in, 1.5mm)")
    p_rec.add_argument("--as-drawn", action="store_true",
                       help="keep each unplaced part where it sits on "
                            "the sheet, instead of centring it. Without "
                            "this they all centre on the origin and "
                            "come out as one stack of solids.")
    p_rec.add_argument("--plane", choices=["XY", "YZ", "XZ"],
                       help="plane for parts that are not placed "
                            "(default XY)")
    p_rec.add_argument("-q", "--quiet", action="store_true",
                       help="only report errors")
    _add_scale_flags(p_rec)

    p_dxf = subs.add_parser(
        "import-dxf", help="read a DXF of flat parts back in",
        description="Read a DXF back into a project: the return leg "
                    "of the CAD round trip. A part modified in CAD "
                    "comes back as DXF and can be printed 1:1 as a "
                    "pattern again. LINE, ARC and CIRCLE are read; "
                    "anything else is reported, never dropped "
                    "silently. The largest closed loop is the "
                    "outline and the rest are holes.")
    p_dxf.add_argument("file", metavar="FILE", help="the .dxf to read")
    p_dxf.add_argument("-o", "--out", metavar="FILE",
                       help="save as this .tiproj.json project")
    p_dxf.add_argument("--name", metavar="NAME",
                       help="name for the part (default: the filename)")
    p_dxf.add_argument("--detail", type=float, metavar="MM",
                       help="how far a flattened curve may deviate "
                            "(default 0.2 mm, finer than a laser "
                            "kerf; these plans were drawn with a "
                            "French curve, so tighter rarely helps)")
    p_dxf.add_argument("--px-per-mm", type=float, default=10.0,
                       metavar="N",
                       help="internal pixel resolution (default 10)")
    p_dxf.add_argument("-q", "--quiet", action="store_true",
                       help="only report errors")
    return parser


# ----- formatting ---------------------------------------------------------

def _mm(value):
    return "%.1f mm" % value


def _format_info(r):
    lines = ["%s  (%s)" % (r.path, r.kind)]
    lines.append("  pixels      : %d x %d" % (r.pixel_width, r.pixel_height))
    if r.is_calibrated:
        lines.append("  scale       : %.6g mm/px  (%s)"
                     % (r.mm_per_pixel, r.calibrated_by or "unknown source"))
        if r.size_mm:
            lines.append("  real size   : %s x %s"
                         % (_mm(r.size_mm[0]), _mm(r.size_mm[1])))
    else:
        lines.append("  scale       : NOT CALIBRATED - pass --dpi, "
                     "--mm-per-px, --width or --height")
    if r.content_size_mm:
        lines.append("  traced area : %s x %s"
                     % (_mm(r.content_size_mm[0]),
                        _mm(r.content_size_mm[1])))
    lines.append("  objects     : %d  (%d shapes, %d points)"
                 % (r.object_count, r.contour_count, r.point_count))
    if r.object_names:
        shown = ", ".join(r.object_names[:6])
        if len(r.object_names) > 6:
            shown += ", ..."
        lines.append("  named       : %s" % shown)
    lines.append("  pages       : %d%s"
                 % (r.page_count, "" if r.has_image else "  (no raster)"))
    plan = r.tile_plan
    if plan:
        lines.append("  tile plan   : %d sheet(s) = %d col x %d row on %s%s"
                     % (plan["sheets"], plan["cols"], plan["rows"],
                        plan["page"],
                        " landscape" if plan["landscape"] else ""))
        lines.append("                margin %s, overlap %s, "
                     "sheet %s x %s"
                     % (_mm(plan["margin_mm"]), _mm(plan["overlap_mm"]),
                        _mm(plan["page_w_mm"]), _mm(plan["page_h_mm"])))
    return "\n".join(lines)


def _info_dict(r):
    return {
        "path": r.path, "kind": r.kind,
        "pixel_width": r.pixel_width, "pixel_height": r.pixel_height,
        "mm_per_pixel": r.mm_per_pixel,
        "calibrated": r.is_calibrated,
        "calibrated_by": r.calibrated_by,
        "size_mm": list(r.size_mm) if r.size_mm else None,
        "content_size_mm": (list(r.content_size_mm)
                            if r.content_size_mm else None),
        "objects": r.object_count, "contours": r.contour_count,
        "points": r.point_count, "object_names": r.object_names,
        "pages": r.page_count, "has_image": r.has_image,
        "tile_plan": r.tile_plan,
    }


# ----- commands -----------------------------------------------------------

def _cmd_info(args, out, err):
    result = jobs.info(
        args.file, page=args.page, landscape=args.landscape,
        margin=args.margin, overlap=args.overlap, **_scale_kwargs(args))
    if args.json:
        import json
        out.write(json.dumps(_info_dict(result), indent=2) + "\n")
    else:
        out.write(_format_info(result) + "\n")
    return EXIT_OK


def _cmd_tile(args, out, err):
    progress = None
    if not args.quiet:
        def progress(message):
            err.write("  %s\n" % message)
    result = jobs.tile(
        args.file, out_dir=args.out, page=args.page,
        landscape=bool(args.landscape), margin=args.margin,
        overlap=args.overlap, embed_image=args.embed_image,
        filled=args.filled, scale_percent=args.scale,
        base_name=args.base_name, dry_run=args.dry_run,
        progress=progress, **_scale_kwargs(args))
    if not args.quiet:
        plan = result.plan
        scaled = ""
        if plan["scale_percent"] != 100.0:
            scaled = " (scaled to %g percent)" % plan["scale_percent"]
        out.write("%d sheet(s) on %s%s at %s x %s%s\n"
                  % (result.sheets, plan["page"],
                     " landscape" if plan["landscape"] else "",
                     _mm(result.size_mm[0]), _mm(result.size_mm[1]),
                     scaled))
        if args.dry_run:
            out.write("dry run - nothing written\n")
        else:
            out.write("written to %s\n" % result.out_dir)
    return EXIT_OK


def _progress_to(err, quiet):
    if quiet:
        return None

    def progress(message):
        err.write("  %s\n" % message)
    return progress


def _cmd_export_svg(args, out, err):
    result = jobs.export_svg(
        args.file, out_path=args.out, embed_image=args.embed_image,
        filled=args.filled, inkscape=args.inkscape,
        downscale_max=args.downscale_max,
        progress=_progress_to(err, args.quiet), **_scale_kwargs(args))
    if not args.quiet:
        size = ""
        if result.size_mm:
            size = " at %s x %s" % (_mm(result.size_mm[0]),
                                    _mm(result.size_mm[1]))
        out.write("%d object(s)%s -> %s (%.1f kB)\n"
                  % (result.objects, size, result.path,
                     result.bytes_written / 1024.0))
    return EXIT_OK


def _cmd_import_svg(args, out, err):
    result = jobs.import_svg(
        args.file, out_path=args.out,
        progress=_progress_to(err, args.quiet))
    if not args.quiet:
        out.write("%s x %s, %d object(s), %d shape(s), %d point(s)\n"
                  % (_mm(result.size_mm[0]), _mm(result.size_mm[1]),
                     result.objects, result.contours, result.points))
        out.write("written to %s\n" % result.path)
    return EXIT_OK


def _cmd_flatten(args, out, err):
    result = jobs.flatten(
        args.files, out_dir=args.out, dpi=args.dpi, spread=args.spread,
        refine=args.refine, propagate=args.propagate,
        dry_run=args.dry_run, progress=_progress_to(err, args.quiet))
    for src, why in result.failed:
        err.write("  skipped %s: %s\n" % (os.path.basename(src), why))
    if not args.quiet:
        out.write("%d page(s) flattened" % result.pages)
        if result.failed:
            out.write(", %d skipped" % len(result.failed))
        out.write("\n")
        if args.dry_run:
            out.write("dry run - nothing written\n")
        else:
            out.write("written to %s\n" % (args.out or "."))
    # Some pages failing is worth a non-zero status for a script.
    return EXIT_ERROR if result.failed else EXIT_OK


def _cmd_ocr(args, out, err):
    result = jobs.ocr(
        args.file, out_path=args.out, lang=args.lang,
        region=tuple(args.region) if args.region else None,
        progress=_progress_to(err, args.quiet), **_scale_kwargs(args))
    if args.out:
        if not args.quiet:
            out.write("%d word(s), %d character(s) -> %s\n"
                      % (result.words, result.characters, result.out_path))
    else:
        out.write(result.text)
        if result.text and not result.text.endswith("\n"):
            out.write("\n")
    return EXIT_OK


def _cmd_find_parts(args, out, err):
    result = jobs.find_parts(
        args.file, trace=args.trace, gap=args.gap,
        min_area=args.min_area, max_aspect=args.max_aspect,
        min_fill=args.min_fill, limit=args.limit, out_path=args.out,
        progress=_progress_to(err, args.quiet), **_scale_kwargs(args))
    if args.json:
        import json
        rows = []
        for c in result.candidates:
            rows.append({
                "bbox": list(c.bbox),
                "centroid": list(c.centroid),
                "area_mm2": round(c.area_mm2, 2),
                "size_mm": [round(v, 2) for v in c.size_mm],
                "outline_size_mm": ([round(v, 2) for v in c.outline_size_mm]
                                    if c.outline_size_mm else None),
                "fill": round(c.fill, 3),
                "aspect": round(c.aspect, 2),
                "points": len(c.outline) if c.outline else 0,
                "outline": ([[round(x, 2), round(y, 2)]
                             for x, y in c.outline] if c.outline else None),
            })
        out.write(json.dumps({"path": result.path,
                              "mm_per_pixel": result.mm_per_pixel,
                              "count": result.count,
                              "project": result.out_path,
                              "parts": rows}, indent=2) + "\n")
        return EXIT_OK
    if not args.quiet:
        if not result.count:
            out.write("no closed outlines found\n")
            return EXIT_OK
        out.write("%d part(s):\n" % result.count)
        for i, c in enumerate(result.candidates):
            size = c.outline_size_mm or c.size_mm
            pts = ("  %3d pts" % len(c.outline)) if c.outline else ""
            out.write("  %2d  %7.1f mm2  %6.1f x %-6.1f mm  at %4d,%-4d%s\n"
                      % (i + 1, c.area_mm2, size[0], size[1],
                         c.bbox[0], c.bbox[1], pts))
        if result.out_path:
            out.write("written to %s\n" % result.out_path)
    return EXIT_OK


def _cmd_export_recipe(args, out, err):
    result = jobs.export_recipe(
        args.file, out_path=args.out, design=args.design,
        plane=args.plane, thickness=args.thickness,
        as_drawn=args.as_drawn,
        progress=_progress_to(err, args.quiet), **_scale_kwargs(args))
    for name, why in result.skipped:
        err.write("  skipped %s: %s\n" % (name, why))
    if not args.quiet:
        out.write("%d part(s) -> %s\n" % (result.parts,
                                        result.out_path))
        if result.assemblies:
            out.write("%d assembly component(s): %s\n"
                      % (len(result.assemblies),
                         ", ".join(sorted(result.assemblies))))
            out.write("run %s after importing the recipe\n"
                      % result.assembly_path)
    return EXIT_OK


def _cmd_import_dxf(args, out, err):
    result = jobs.import_dxf(
        args.file, out_path=args.out, name=args.name,
        px_per_mm=args.px_per_mm, detail_mm=args.detail,
        progress=_progress_to(err, args.quiet))
    for kind, n in sorted(result.info.get("skipped", {}).items()):
        err.write("  ignored %d %s entity(s)\n" % (n, kind))
    if result.info.get("open"):
        err.write("  %d open chain(s): the outline may have a gap\n"
                  % result.info["open"])
    if not args.quiet:
        out.write("%d loop(s), %d points, %.1f x %.1f mm\n"
                  % (result.loops, result.points, result.size_mm[0],
                     result.size_mm[1]))
        if result.out_path:
            out.write("written to %s\n" % result.out_path)
    return EXIT_OK


_HANDLERS = {"info": _cmd_info, "tile": _cmd_tile,
             "export-svg": _cmd_export_svg, "import-svg": _cmd_import_svg,
             "flatten": _cmd_flatten, "ocr": _cmd_ocr,
             "find-parts": _cmd_find_parts,
             "export-recipe": _cmd_export_recipe,
             "import-dxf": _cmd_import_dxf}


def _hint(exc):
    """Add the CLI-only remedy to a job error.

    The job layer serves both front ends, so its messages name no
    flags - "supply one" means a dialog in the GUI and a flag here.
    This is where the flags get named.
    """
    text = str(exc)
    if "calibrate it, or supply one" in text:
        text += " [--dpi, --mm-per-px, --width or --height]"
    return text


def run(argv, out=None, err=None):
    """Run one CLI command. Returns an exit code; never calls sys.exit.

    Streams are injectable so tests can capture output without
    touching the real stdout.
    """
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS.get(args.command)
    if handler is None:              # no subcommand: the caller runs the GUI
        parser.print_help(out)
        return EXIT_OK
    try:
        return handler(args, out, err)
    except jobs.JobError as exc:
        err.write("pagewright %s: %s\n" % (args.command, _hint(exc)))
        return EXIT_ERROR
    except KeyboardInterrupt:
        err.write("\ninterrupted\n")
        return EXIT_ERROR
