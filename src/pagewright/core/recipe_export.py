"""Write traced parts as a MyArchive `recipe.json` (archiver 1.3.0).

A recipe is the tool-neutral hub of the CadRoundTrip project: SolidCraft,
Fusion and OpenSCAD all either write it or rebuild from it. Emitting one
is how a plan sheet becomes solid parts without PageWright knowing
anything about CAD.

Two facts make this possible without Fusion:

  * `token` is only an identity key. Fusion's are opaque blobs, but
    SolidCraft's own exporter mints `sc:0` and `Sketch1/c1`, and the
    importer looks tokens up in its own map. So ours can be readable.
  * An extrude finds its profile by token first and falls back to AREA
    and CENTROID. A synthetic recipe has no Fusion profile tokens, so
    the area path is the one that runs, and it is what we fill in.

Lengths in a recipe are centimetres (`units.internal_length`), while
PageWright works in millimetres, so everything is divided by ten on the
way out.

Each part becomes a Sketch plus an ExtrudeFeature, placed by its
model.Build: `plane` picks the sketch plane, `station_mm` offsets it
along that plane's normal, and `thickness_mm` (or the project's sheet
stock) is the extrude distance.
"""

import json
import math
import os

ARCHIVER = {"name": "PageWright", "version": "1.3.0",
            "synthetic": "traced from a scanned plan by PageWright, "
                         "not exported from Fusion"}

UNITS = {"internal_length": "cm", "internal_angle": "rad",
         "display_length": "mm"}

MM_PER_CM = 10.0

# A sketch plane's (origin-relative) axes in world space. A part drawn
# on paper has two coordinates; these say which world axes they become,
# and the third is the station.
PLANE_AXES = {
    "XY": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "YZ": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "XZ": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}

DEFAULT_PLANE = "XY"

# Plans number their parts by what the part IS: F-3 is the third
# fuselage former, R-2 the second wing rib. Those names get typed in by
# hand while annotating, so the convention is free information about
# which way a part stands - a former across the fuselage (YZ), a rib
# across the wing (XZ). Only used where no plane was set explicitly.
NAME_PLANES = (
    (("f", "former", "frame", "bulkhead", "fus"), "YZ"),
    (("r", "rib", "w", "wing"), "XZ"),
)


def plane_from_name(name):
    """Guess a plane from a part name like "F-3" or "R2", or None.

    Deliberately narrow: it wants a letter (or a word) followed by a
    number, so "Rudder" and "Fin" do not read as a rib and a former.
    """
    text = (name or "").strip().lower()
    if not text:
        return None
    head = ""
    for ch in text:
        if ch.isdigit():
            break
        if ch.isalpha():
            head += ch
        elif ch in " -_.":
            if head:
                break
        else:
            return None
    tail = text[len(head):].lstrip(" -_.")
    if not head or not tail or not tail[0].isdigit():
        return None
    for prefixes, plane in NAME_PLANES:
        if head in prefixes:
            return plane
    return None


class RecipeError(Exception):
    pass


def polygon_area_centroid(points):
    """Signed-area centroid of a closed polygon, as (area, cx, cy).

    The shoelace formula. `area` is unsigned; the centroid is the true
    area centroid, not the mean of the vertices, because the importer
    matches a profile on it.
    """
    n = len(points)
    if n < 3:
        return 0.0, 0.0, 0.0
    a2 = cx = cy = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a2) < 1e-12:
        return 0.0, 0.0, 0.0
    return abs(a2) / 2.0, cx / (3.0 * a2), cy / (3.0 * a2)


def _rotate(points, degrees):
    if not degrees:
        return list(points)
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def _centred_mm(contour, mm_per_pixel, pixel_height, rotate_deg=None):
    """Contour pixels -> mm in the sketch plane, centred on the part.

    Image y grows downward and CAD y grows up, so the row is flipped.
    Parts are centred on their own centroid: a plan sheet is nested, so
    the position a part was DRAWN at is where there was room on the
    paper, not where it belongs. Placement comes from Build.
    """
    pts = [(x * mm_per_pixel, (pixel_height - y) * mm_per_pixel)
           for x, y in contour.points]
    _area, cx, cy = polygon_area_centroid(pts)
    pts = [(x - cx, y - cy) for x, y in pts]
    return _rotate(pts, rotate_deg)


def _to_world_cm(points_mm, plane, station_mm):
    """Sketch-plane mm -> world cm triples on that plane."""
    u, v, n = PLANE_AXES[plane]
    s = (station_mm or 0.0) / MM_PER_CM
    out = []
    for x_mm, y_mm in points_mm:
        x, y = x_mm / MM_PER_CM, y_mm / MM_PER_CM
        out.append([x * u[i] + y * v[i] + s * n[i] for i in range(3)])
    return out


def _line(tag, p0, p1):
    """One SketchLine as the recipe spells it: a degree-1 NURBS."""
    length = math.sqrt(sum((b - a) ** 2 for a, b in zip(p0, p1)))
    return {
        "type": "SketchLine",
        "token": tag,
        "construction": False,
        "fixed": False,
        "nurbs": {
            "degree": 1,
            "controlPoints": [list(p0), list(p1)],
            "knots": [0.0, 0.0, length, length],
            "rational": False,
            "weights": [],
            "periodic": False,
        },
    }


def _sketch_item(index, name, plane, station_mm, loops_cm):
    points, curves, loop_tokens = [], [], []
    for li, loop in enumerate(loops_cm):
        tokens = []
        for i, p in enumerate(loop):
            points.append({"token": "%s/p%d_%d" % (name, li, i),
                           "xyz": list(p), "fixed": False})
            tag = "%s/c%d_%d" % (name, li, i)
            curves.append(_line(tag, p, loop[(i + 1) % len(loop)]))
            tokens.append(tag)
        loop_tokens.append(tokens)

    u, v, _n = PLANE_AXES[plane]
    transform = [u[0], u[1], u[2], 0.0,
                 v[0], v[1], v[2], 0.0,
                 0.0, 0.0, 0.0, 0.0,
                 0.0, 0.0, 0.0, 1.0]
    item = {
        "index": index,
        "name": name,
        "suppressed": False,
        "type": "Sketch",
        "token": "pw:%d" % index,
        "parameters": [],
        "checkpoint": False,
        "health": "HealthyFeatureHealthState",
        "sketch": {
            "parametric": True,
            "component": "PageWright",
            "referencePlane": {"type": "ConstructionPlane", "name": plane},
            "transform": transform,
            "points": points,
            "curves": curves,
            "constraints": [],
            "dimensions": [],
        },
    }
    if station_mm:
        item["sketch"]["referencePlane"]["offset"] = \
            station_mm / MM_PER_CM
    return item, loop_tokens


def _extrude_item(index, name, sketch_name, loop_tokens, area_cm2,
                  centroid_cm, thickness_mm, param):
    return {
        "index": index,
        "name": name,
        "suppressed": False,
        "type": "ExtrudeFeature",
        "token": "pw:%d" % index,
        "parameters": [{
            "name": param,
            "expression": "%g mm" % thickness_mm,
            "unit": "mm",
            "value": thickness_mm / MM_PER_CM,
            "comment": "",
            "role": "AlongDistance",
        }],
        "checkpoint": False,
        "health": "HealthyFeatureHealthState",
        "extrude": {
            "profile": {
                "type": "Profile",
                "token": "%s/profile0" % sketch_name,
                "sketch": sketch_name,
                "loops": loop_tokens,
                "area": area_cm2,
                "centroid": list(centroid_cm),
            },
            "operation": "NewBodyFeatureOperation",
            "isSolid": True,
            "isThinExtrude": False,
            "startExtent": "ProfilePlaneStartDefinition",
            "hasTwoExtents": False,
            "extentOne": {
                "type": "DistanceExtentDefinition",
                "distance": {"parameter": param,
                             "expression": "%g mm" % thickness_mm,
                             "value": thickness_mm / MM_PER_CM},
            },
            "extentTwo": None,
            "taperAngleOne": None,
            "taperAngleTwo": None,
            "participantBodies": [],
        },
        "operation": "NewBodyFeatureOperation",
        "bodies": ["Body%d" % (index // 2 + 1)],
    }


def _safe(name, used):
    """A sketch name that is unique and free of recipe-unfriendly
    characters, since names are used as reference keys."""
    base = "".join(ch if (ch.isalnum() or ch in "_-") else "_"
                   for ch in (name or "Part")).strip("_") or "Part"
    out, n = base, 2
    while out in used:
        out, n = "%s_%d" % (base, n), n + 1
    used.add(out)
    return out


def build_recipe(project, design=None, default_plane=DEFAULT_PLANE):
    """Build the recipe document for `project`. Returns (dict, skipped).

    `skipped` is a list of (part name, reason) for parts that could not
    be built - almost always a missing thickness. They are reported
    rather than guessed at, because a wrong thickness is a wasted part.
    """
    mpp = project.calibration.mm_per_pixel
    if not mpp:
        raise RecipeError(
            "no scale: calibrate the project so parts come out the "
            "right size")
    if not project.objects:
        raise RecipeError("nothing to export: no traced objects")

    timeline, skipped, used = [], [], set()
    for obj in project.objects:
        outer = next((c for c in obj.contours
                      if getattr(c, "role", "outer") != "hole"), None)
        if outer is None or len(outer.points) < 3:
            skipped.append((obj.name, "no closed outline"))
            continue
        thickness = project.part_thickness_mm(obj)
        if not thickness:
            skipped.append((obj.name, "no thickness: set the sheet "
                                      "stock or the part's own"))
            continue

        build = getattr(obj, "build", None)
        plane = (getattr(build, "plane", None)
                 or plane_from_name(obj.name)
                 or default_plane).upper()
        if plane not in PLANE_AXES:
            skipped.append((obj.name, "unknown plane %r" % (plane,)))
            continue
        station = getattr(build, "station_mm", None) or 0.0
        rotate = getattr(build, "rotate_deg", None)

        pts_mm = _centred_mm(outer, mpp, project.pixel_height, rotate)
        area_mm2, cx, cy = polygon_area_centroid(pts_mm)
        loop_cm = _to_world_cm(pts_mm, plane, station)
        centroid_cm = _to_world_cm([(cx, cy)], plane, station)[0]

        name = _safe(obj.name, used)
        index = len(timeline)
        sketch, loop_tokens = _sketch_item(index, name, plane, station,
                                           [loop_cm])
        timeline.append(sketch)
        timeline.append(_extrude_item(
            index + 1, "%s_extrude" % name, name, loop_tokens,
            area_mm2 / (MM_PER_CM * MM_PER_CM), centroid_cm, thickness,
            "d%d" % (index + 1)))

    if not timeline:
        reasons = "; ".join("%s: %s" % r for r in skipped[:3])
        raise RecipeError("no part could be built (%s)" % reasons)

    doc = {
        "archiver": dict(ARCHIVER),
        "design": design or "PageWright",
        "units": dict(UNITS),
        "userParameters": [],
        "timeline": timeline,
        "canvases": [],
    }
    return doc, skipped


def write_recipe(project, out_path, design=None,
                 default_plane=DEFAULT_PLANE):
    """Write the recipe for `project` to `out_path`. Returns (path,
    parts written, skipped)."""
    doc, skipped = build_recipe(project, design=design,
                                default_plane=default_plane)
    parent = os.path.dirname(out_path)
    try:
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
    except (IOError, OSError) as exc:
        raise RecipeError("cannot write %s: %s" % (out_path, exc))
    return out_path, len(doc["timeline"]) // 2, skipped
