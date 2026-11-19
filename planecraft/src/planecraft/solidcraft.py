"""Emit a SolidCraft command script for a placed airframe.

Why not the recipe route: a recipe records a sketch's plane as a NAME
plus an `offset`, and the round-trip importer reads only the name
(`sketch_plane` in solidcraft_import.py returns `ref['name']` and
never looks at `offset`). Every former therefore lands on the base
plane at zero and the fuselage comes out 3 mm long instead of 308.

Measured, not assumed: a 10-former recipe with offsets 0.693 to
30.803 cm rebuilt as ten bodies all at x = 0.

So PlaneCraft drives the command API itself. That is the better fit
anyway - a FRAME is PlaneCraft's own idea, and `construct.plane.offset`
is exactly it, so there is no reason to launder it through a format
that cannot hold it.
"""

import json
import math

from .core import plan as core_plan
from .parts import _ensure_pagewright

MM_PER_CM = 10.0


class ScriptError(Exception):
    """The airframe cannot be turned into a script, and why."""


def _rotate(loop, degrees):
    """Turn a loop about the sketch origin, degrees counter-clockwise."""
    if not degrees:
        return loop
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return [(x * c - y * s, x * s + y * c) for x, y in loop]


# A sketch's local y does not point the same way on every plane.
# MEASURED, not assumed: a line from local y 0 to +50 on the XZ plane
# came out spanning world Z 0 to -50, so local +y is world -Z there.
# Everything placed on XZ therefore needs its height negated, or it
# builds mirrored about the centreline - which looks plausible in a
# render of a roughly symmetric airframe, and is why it survived a
# first pass.
Y_IS_FLIPPED = {"XZ": True, "XY": False, "YZ": False}


def _outline_mm(obj, mm_per_pixel, pixel_height, mirrored=False,
                rotate_deg=0.0, shift_mm=(0.0, 0.0), flip_y=False):
    """A traced contour as sketch-plane millimetres.

    Image y grows downward and CAD y grows up, so the row is flipped -
    the same convention PageWright's own exporter uses. The part is
    centred on its outline so a station places its centre, which is
    what a station on a plan means.
    """
    _ensure_pagewright()
    from pagewright.core.recipe_export import polygon_area_centroid

    outer = next((c for c in obj.contours
                  if getattr(c, "role", "outer") != "hole"), None)
    if outer is None or len(outer.points) < 3:
        return None, []

    def mm(c):
        pts = [(x * mm_per_pixel, (pixel_height - y) * mm_per_pixel)
               for x, y in c.points]
        return [(-x, y) for x, y in pts] if mirrored else pts

    loops = [mm(outer)] + [mm(h) for h in obj.contours
                           if getattr(h, "role", "outer") == "hole"
                           and len(h.points) >= 3]
    # Centre on the OUTLINE, then turn every loop by the same angle,
    # so a hole keeps its place in the part.
    _a, cx, cy = polygon_area_centroid(loops[0])
    loops = [[(x - cx, y - cy) for x, y in loop] for loop in loops]
    loops = [_rotate(loop, rotate_deg) for loop in loops]
    # Shift in MODEL terms: the part is now where the placement says
    # it is, measured the way the aeroplane is measured.
    dx, dy = shift_mm
    if dx or dy:
        loops = [[(x + dx, y + dy) for x, y in loop] for loop in loops]
    # Then, on the FINISHED points, compensate for the plane's own
    # axis direction. MEASURED: a sketch on XZ spanning local y 10..30
    # comes out at world Z -30..-10, so local +y is world -Z there and
    # the whole sketch - part AND placement together - is negated so
    # SolidCraft's flip puts it back. Negating only the part (before
    # the shift) leaves the height right and the part upside down;
    # negating neither builds every XZ part mirrored about the
    # centreline, which is how it survived a first pass: an aerofoil
    # straddles its chord line and still looks like an aerofoil.
    if flip_y:
        loops = [[(x, -y) for x, y in loop] for loop in loops]
    return loops[0], loops[1:]


def _lines(loop):
    """One sketch.line command per edge of a closed loop."""
    out = []
    for i, p in enumerate(loop):
        q = loop[(i + 1) % len(loop)]
        out.append({"command": "sketch.line",
                    "params": {"points": [[round(p[0], 6), round(p[1], 6)],
                                          [round(q[0], 6), round(q[1], 6)]]}})
    return out


def commands(project, airframe, units="mm"):
    """The SolidCraft command list that builds this airframe.

    One construction plane per frame that needs an offset, then one
    sketch and one extrude per placement.
    """
    placements = core_plan(airframe)
    if not placements:
        raise ScriptError("no placement could be resolved")

    mpp = project.calibration.mm_per_pixel
    if not mpp:
        raise ScriptError("the project has no scale: calibrate it first")
    traced = {o.name: o for o in project.objects}

    cmds = [{"command": "document.units", "params": {"units": units}}]
    planes = {}
    for p in placements:
        if not p["buildable"]:
            raise ScriptError(
                "%s sits on frame %r, which has no definition - give it "
                "an offset, or place the part by hand in CAD"
                % (p["part"], p["frame"]))
        key = (p["base"], round(p["offset_mm"], 6))
        if key in planes:
            continue
        if abs(key[1]) < 1e-9:
            planes[key] = p["base"]          # the base plane itself
            continue
        name = "Pl_%s_%s" % (p["base"], ("%.4f" % key[1]).replace(
            ".", "_").replace("-", "n"))
        planes[key] = name
        cmds.append({"command": "construct.plane.offset",
                     "params": {"base": p["base"],
                                "offset": "%.6g mm" % key[1],
                                "name": name}})

    seen = set()
    for p in placements:
        obj = traced.get(p["part"])
        if obj is None:
            raise ScriptError("placed but not traced: %s" % p["part"])
        thickness = project.part_thickness_mm(obj)
        if not thickness:
            raise ScriptError(
                "%s has no thickness: set its sheet stock" % p["part"])
        outline, holes = _outline_mm(
            obj, mpp, project.pixel_height,
            mirrored=p.get("mirrored", False),
            rotate_deg=p.get("rotate_deg") or 0.0,
            shift_mm=tuple(p.get("in_plane_mm") or (0.0, 0.0)),
            flip_y=Y_IS_FLIPPED.get(p["base"], False))
        if outline is None:
            raise ScriptError("%s has no closed outline" % p["part"])

        # A sketch name must be unique AND not all digits: SolidCraft
        # reads an all-digit name as a timeline index, so "2" is not a
        # sketch once a body exists.
        base = "p%s" % p["part"] if p["part"].isdigit() else p["part"]
        name, n = base, 2
        while name in seen:
            name, n = "%s_%d" % (base, n), n + 1
        seen.add(name)

        plane = planes[(p["base"], round(p["offset_mm"], 6))]
        cmds.append({"command": "sketch.create",
                     "params": {"plane": plane, "name": name,
                                "project_edges": False}})
        cmds.extend(_lines(outline))
        for h in holes:
            cmds.extend(_lines(h))
        cmds.append({"command": "sketch.finish", "params": {}})
        cmds.append({
            "command": "solid.extrude",
            "params": {"sketch": name, "profiles": [0], "operation": "new",
                       "name": "%s_solid" % name,
                       "distance": "%.6g mm" % thickness,
                       "direction": "positive"}})
    return cmds


def write_script(project, airframe, path, units="mm"):
    """Write the command script to `path`. Returns the command count."""
    cmds = commands(project, airframe, units=units)
    with open(path, "w") as fh:
        json.dump({"commands": cmds}, fh, indent=1)
    return len(cmds)
