"""Project save/load as JSON (Phase 6).

Serialises a model.Project -- calibration, margin, display unit, the source
image reference, the tiled-printing settings, and every traced object's
contours (points in pixel coordinates, role, style) -- to a plain JSON document
and back. Pure Python + json, so it is fully round-trippable and testable
without Qt or OpenCV.

The pixel image itself is *not* embedded; only its path and dimensions are
stored, and the UI reloads the image from that path when opening a project.
"""

import json

from ..model import (Build, Calibration, Contour, PageEntry, Project,
                     Style, TracedObject, default_tiling)

FORMAT_VERSION = 1


class ProjectIOError(Exception):
    pass


# ----- model -> dict --------------------------------------------------------

def _style_to_dict(style):
    return {
        "stroke": style.stroke,
        "stroke_width_mm": style.stroke_width_mm,
        "fill": style.fill,
    }


def _contour_to_dict(contour):
    return {
        "points": [[float(x), float(y)] for (x, y) in contour.points],
        "closed": bool(contour.closed),
        "role": contour.role,
        "representation": contour.representation,
    }


def _build_to_dict(build):
    """Only the fields that were set, and nothing at all when none were.

    Keeps a project that has no CAD intent serialising exactly as it did
    before Build existed.
    """
    if build is None or build.is_empty():
        return None
    d = {}
    if build.thickness_mm is not None:
        d["thickness_mm"] = float(build.thickness_mm)
    if build.plane is not None:
        d["plane"] = build.plane
    if build.station_mm is not None:
        d["station_mm"] = float(build.station_mm)
    if build.rotate_deg is not None:
        d["rotate_deg"] = float(build.rotate_deg)
    if build.assembly:
        d["assembly"] = str(build.assembly)
    return d


def _object_to_dict(obj):
    d = {
        "name": obj.name,
        "style": _style_to_dict(obj.style),
        "contours": [_contour_to_dict(c) for c in obj.contours],
    }
    build = _build_to_dict(getattr(obj, "build", None))
    if build:
        d["build"] = build
    return d


def natural_key(name):
    """Sort key treating digit runs numerically (page2 < page10)."""
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def objects_to_list(objects):
    """Serialize TracedObjects to the schema used inside pages[]."""
    return [_object_to_dict(o) for o in objects]


def objects_from_list(lst):
    return [_object_from_dict(o) for o in (lst or [])]


def project_to_dict(project):
    """Serialise a Project to a JSON-ready dict."""
    d = {
        "version": FORMAT_VERSION,
        "source_image": project.source_image_path,
        "pixel_width": project.pixel_width,
        "pixel_height": project.pixel_height,
        "dpi": project.dpi,
        "calibration": {
            "mm_per_pixel": project.calibration.mm_per_pixel,
            "display_unit": project.calibration.display_unit,
        },
        "margin_mm": project.margin_mm,
        "tiling": dict(project.tiling),
        "objects": [_object_to_dict(o) for o in project.objects],
        # canonical form: a project is always at least a one-page job
        "pages": ([pg.to_dict() for pg in project.pages] if project.pages
                  else [PageEntry(
                      source_path=project.source_image_path,
                      objects=[_object_to_dict(o)
                               for o in project.objects]).to_dict()]),
        "current_page": int(getattr(project, "current_page", 0)),
    }
    # optional tile-mark styling: written only when customized
    if getattr(project, "mark_color", None):
        d["marks"] = {"color": project.mark_color,
                      "opacity": project.mark_opacity}
    # sheet stock thickness: written only when the user set one
    if getattr(project, "sheet_thickness_mm", None):
        d["sheet_thickness_mm"] = float(project.sheet_thickness_mm)
    return d


# ----- dict -> model --------------------------------------------------------

def _style_from_dict(d):
    if not d:
        return Style()
    return Style(
        stroke=d.get("stroke", "#000000"),
        stroke_width_mm=d.get("stroke_width_mm", 0.5),
        fill=d.get("fill", "none"),
    )


def _contour_from_dict(d):
    points = [(float(p[0]), float(p[1])) for p in d.get("points", [])]
    return Contour(
        points=points,
        closed=d.get("closed", True),
        role=d.get("role", "outer"),
        representation=d.get("representation", "polyline"),
    )


def _build_from_dict(d):
    """Read a Build, rejecting values that would make a wrong part.

    Validated on load rather than at CAD-export time: a bad plane or a
    negative thickness is a typo worth catching while the file is still
    on screen, not after it has been cut.
    """
    if not d:
        return Build()
    plane = d.get("plane")
    if plane is not None:
        plane = str(plane).upper()
        if plane not in Build.PLANES:
            raise ProjectIOError(
                "unknown build plane %r (expected one of %s)"
                % (d.get("plane"), ", ".join(Build.PLANES)))

    def _num(key, positive=False):
        v = d.get(key)
        if v is None:
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ProjectIOError("build %s is not a number: %r"
                                 % (key, d.get(key)))
        if positive and v <= 0.0:
            raise ProjectIOError("build %s must be positive: %r"
                                 % (key, v))
        return v

    assembly = d.get("assembly")
    return Build(thickness_mm=_num("thickness_mm", positive=True),
                 plane=plane,
                 station_mm=_num("station_mm"),
                 rotate_deg=_num("rotate_deg"),
                 assembly=str(assembly) if assembly else None)


def _object_from_dict(d):
    obj = TracedObject(name=d.get("name", "object"),
                       style=_style_from_dict(d.get("style")),
                       build=_build_from_dict(d.get("build")))
    obj.contours = [_contour_from_dict(c) for c in d.get("contours", [])]
    return obj


def project_from_dict(d):
    """Rebuild a Project from a dict produced by project_to_dict."""
    version = d.get("version")
    if version != FORMAT_VERSION:
        raise ProjectIOError("unsupported project version: %r" % (version,))

    project = Project()
    project.source_image_path = d.get("source_image")
    project.pixel_width = int(d.get("pixel_width", 0))
    project.pixel_height = int(d.get("pixel_height", 0))
    project.dpi = d.get("dpi")

    cal = d.get("calibration") or {}
    project.calibration = Calibration(
        mm_per_pixel=cal.get("mm_per_pixel"),
        display_unit=cal.get("display_unit", "mm"))

    project.margin_mm = float(d.get("margin_mm", 5.0))

    tiling = default_tiling()
    tiling.update(d.get("tiling") or {})   # tolerate older files / missing keys
    project.tiling = tiling

    project.objects = [_object_from_dict(o) for o in d.get("objects", [])]
    marks = d.get("marks")
    if marks:
        project.mark_color = marks.get("color")
        project.mark_opacity = marks.get("opacity")

    thick = d.get("sheet_thickness_mm")
    if thick is not None:
        try:
            thick = float(thick)
        except (TypeError, ValueError):
            raise ProjectIOError("sheet_thickness_mm is not a number: %r"
                                 % (d.get("sheet_thickness_mm"),))
        if thick <= 0.0:
            raise ProjectIOError("sheet_thickness_mm must be positive: %r"
                                 % (thick,))
        project.sheet_thickness_mm = thick

    pages = d.get("pages")
    if pages:
        project.pages = [PageEntry.from_dict(pg) for pg in pages]
        project.current_page = max(
            0, min(int(d.get("current_page", 0)), len(pages) - 1))
    else:
        # older single-page file -> one-page job
        project.pages = [PageEntry(
            source_path=project.source_image_path,
            objects=d.get("objects", []))]
        project.current_page = 0
    return project


# ----- file I/O -------------------------------------------------------------

def save_project(project, path):
    """Write `project` to `path` as JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(project_to_dict(project), fh, indent=2)


def load_project(path):
    """Read a Project from a JSON file at `path`."""
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise ProjectIOError("not a valid project file: %s" % (exc,))
    return project_from_dict(data)
