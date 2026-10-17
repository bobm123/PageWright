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

Qt is never imported here. `main.py` dispatches to this module BEFORE it
touches PySide6, so these commands run on a machine with no display.

Exit codes: 0 success, 1 a JobError (bad input, no scale, unwritable
output), 2 argparse usage errors.
"""

import argparse
import sys

from .core import jobs
from .core.tiling import PAGE_SIZES_MM

EXIT_OK = 0
EXIT_ERROR = 1

# Subcommands this module provides. `main.py` checks membership to
# decide between CLI and GUI, so a new command only needs adding here.
COMMANDS = ("info", "tile")


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


_HANDLERS = {"info": _cmd_info, "tile": _cmd_tile}


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
        err.write("pagewright %s: %s\n" % (args.command, exc))
        return EXIT_ERROR
    except KeyboardInterrupt:
        err.write("\ninterrupted\n")
        return EXIT_ERROR
