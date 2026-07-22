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

from ..model import (Calibration, Contour, Project, Style, TracedObject,
                     default_tiling)

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


def _object_to_dict(obj):
    return {
        "name": obj.name,
        "style": _style_to_dict(obj.style),
        "contours": [_contour_to_dict(c) for c in obj.contours],
    }


def project_to_dict(project):
    """Serialise a Project to a JSON-ready dict."""
    return {
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
    }


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


def _object_from_dict(d):
    obj = TracedObject(name=d.get("name", "object"),
                       style=_style_from_dict(d.get("style")))
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
