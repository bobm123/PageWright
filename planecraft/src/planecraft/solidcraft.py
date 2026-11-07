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

from .core import plan as core_plan
from .parts import _ensure_pagewright

MM_PER_CM = 10.0


class ScriptError(Exception):
    """The airframe cannot be turned into a script, and why."""


def _outline_mm(obj, mm_per_pixel, pixel_height, mirrored=False):
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
    _a, cx, cy = polygon_area_centroid(loops[0])
    loops = [[(x - cx, y - cy) for x, y in loop] for loop in loops]
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
        outline, holes = _outline_mm(obj, mpp, project.pixel_height,
                                     mirrored=p.get("mirrored", False))
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
