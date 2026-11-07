"""Read a PageWright project: the PARTS sheet.

PlaneCraft does not re-implement tracing. It reads what PageWright
saved - outlines, stock thickness, and the plan's own numbering
convention - and adds the one thing that sheet cannot tell you: where
each part goes.

PageWright is imported as a library rather than copied, so there is
one definition of a project file. It is a sibling checkout, hence the
path nudge.
"""

import os
import sys


def _ensure_pagewright():
    """Put PageWright's src on the path if it is not installed."""
    try:
        import pagewright  # noqa: F401
        return
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.normpath(os.path.join(here, "..", "..", "..", "src"))
    if os.path.isdir(os.path.join(src, "pagewright")):
        sys.path.insert(0, src)


def load(path):
    """A PageWright project, as its own model objects."""
    _ensure_pagewright()
    from pagewright.core import project_io
    return project_io.load_project(path)


def part_names(project):
    """Every traced part's name, in the project's own order."""
    return [o.name for o in project.objects]


def implied_planes(project):
    """{part name: plane or None} from the plan's numbering convention.

    This is PageWright's answer, not a second opinion: the convention
    is recorded in the project file and `naming` owns reading it. It
    is a STARTING POINT for placement - it says a part is a former,
    not which station it sits at.
    """
    _ensure_pagewright()
    from pagewright.core import naming
    scheme = naming.scheme_for(project)
    return {o.name: scheme.plane_for(o.name) for o in project.objects}


def thicknesses(project):
    """{part name: thickness in mm or None}, resolved as PageWright does."""
    return {o.name: project.part_thickness_mm(o) for o in project.objects}


def summary(project):
    """One row per part: name, implied plane, thickness, hole count."""
    planes = implied_planes(project)
    thick = thicknesses(project)
    rows = []
    for o in project.objects:
        holes = sum(1 for c in o.contours
                    if getattr(c, "role", "outer") == "hole")
        rows.append({
            "part": o.name,
            "implied_plane": planes.get(o.name),
            "thickness_mm": thick.get(o.name),
            "holes": holes,
        })
    return rows
