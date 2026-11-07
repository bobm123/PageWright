"""Apply an airframe to a traced project, and export it to CAD.

PageWright already knows how to turn placed parts into a recipe: it
reads `Build.plane` and `Build.station_mm` and emits one sketch and
one extrude per part. So this does NOT write a second exporter. It
applies the airframe's placement onto a COPY of the project and hands
that to `recipe_export`.

A copy, because the traced project is the user's file and placement
is PlaneCraft's opinion about it. Writing planes back into the
.tiproj.json would make a tracing tool's file depend on an assembly
decision taken elsewhere.

One traced part may be placed many times (the wing case), and a
recipe is a flat list of parts, so each placement becomes its own
object named `<part>@<frame>`. The part name alone would collide.
"""

import copy

from .core import plan as core_plan
from .parts import _ensure_pagewright


class BuildError(Exception):
    """The airframe cannot be turned into a model, and why."""


def _placed_name(part, frame, several):
    """A unique object name for one placement.

    A part placed once keeps its own name, because that is what the
    plan calls it and what the user will look for. Only a part built
    more than once needs qualifying.
    """
    return part if not several else "%s@%s" % (part, frame)


def apply_airframe(project, airframe, only_placed=True):
    """A copy of `project` with the airframe's placement applied.

    `only_placed` drops parts that no placement mentions. That is the
    usual want: a fuselage airframe should build a fuselage, not the
    fuselage plus every untraced oddment lying flat at the origin.
    """
    _ensure_pagewright()

    placements = core_plan(airframe)
    if not placements:
        raise BuildError("no placement could be resolved: check the "
                         "airframe's frames against its placements")

    by_part = {}
    for p in placements:
        by_part.setdefault(p["part"], []).append(p)

    traced = {o.name: o for o in project.objects}
    missing = sorted(set(by_part) - set(traced))
    if missing:
        raise BuildError(
            "placed but not traced: %s - trace them in PageWright "
            "first, or drop them from the airframe"
            % ", ".join(missing))

    out = copy.deepcopy(project)
    objects = []
    for obj in out.objects:
        places = by_part.get(obj.name)
        if not places:
            if not only_placed:
                objects.append(obj)
            continue
        several = len(places) > 1
        for p in places:
            if not p["buildable"]:
                # A reference frame names a plane the CAD side owns;
                # a recipe cannot create it, so say so rather than
                # quietly building the part somewhere wrong.
                raise BuildError(
                    "%s sits on frame %r, which has no definition. A "
                    "recipe cannot create that plane - give the frame "
                    "an offset or oriented definition, or place the "
                    "part by hand in CAD." % (obj.name, p["frame"]))
            one = copy.deepcopy(obj)
            one.name = _placed_name(obj.name, p["frame"], several)
            one.build.plane = p["base"]
            one.build.station_mm = p["offset_mm"]
            if p["rotate_deg"]:
                one.build.rotate_deg = p["rotate_deg"]
            objects.append(one)
    out.objects = objects
    return out


def to_recipe(project, airframe, design=None, only_placed=True):
    """(recipe dict, skipped) for `project` placed by `airframe`."""
    _ensure_pagewright()
    from pagewright.core import recipe_export

    placed = apply_airframe(project, airframe, only_placed=only_placed)
    name = design or airframe.get("name") or "PlaneCraft"
    return recipe_export.build_recipe(placed, design=name)


def write_recipe(project, airframe, path, design=None, only_placed=True):
    """Write the recipe for the placed project to `path`.

    Returns (path, parts written, skipped, doc), as PageWright's own
    `write_recipe` does.
    """
    _ensure_pagewright()
    from pagewright.core import recipe_export

    placed = apply_airframe(project, airframe, only_placed=only_placed)
    name = design or airframe.get("name") or "PlaneCraft"
    return recipe_export.write_recipe(placed, path, design=name)
