"""Write a wing plan with the placed ribs over it, for editing by hand.

Why this exists: reading a rib's chordwise position off a dense 1952
plan by image analysis kept picking the wrong feature - the trailing
edge mistaken for the leading edge, panel lines mistaken for ribs.
A person can see at a glance what a column scan cannot, so the
positions go out as an SVG, get dragged into place in Inkscape or
VectorCraft, and come back as numbers.

Each rib is its own group with `id="rib-<name>"`, so a drag writes a
`transform` on that group and nothing else moves. `read_svg` reads
those transforms back.

Lengths are millimetres throughout, with the SVG user unit set to
1 mm so the drawing opens at true size.
"""

import math
import os
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
# Inkscape's own namespaces. The label and lock attributes need them
# declared, or the file is not well-formed XML ("unbound prefix").
INK_NS = "http://www.inkscape.org/namespaces/inkscape"
SODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"


def _path_d(points):
    """A closed path from (x, y) millimetre points."""
    head = "M %.4f %.4f" % points[0]
    rest = " ".join("L %.4f %.4f" % p for p in points[1:])
    return "%s %s Z" % (head, rest)


def write_svg(project, airframe, path, image_path=None,
              image_origin_mm=(0.0, 0.0), image_size_mm=None, side="R",
              parts=None, upright=False):
    """Write one wing's ribs over the plan.

    `image_path` is the plan scan to place underneath, and
    `image_origin_mm` is where its top-left corner sits in the same
    millimetre frame as the ribs. The image is referenced, not
    embedded, so the file stays small and the scan is never copied.

    Only one side is written (`side` is "R" or "L"): the two are
    mirror images, so editing one and mirroring is less work than
    keeping two in step.
    """
    from .core import plan as core_plan

    mpp = project.calibration.mm_per_pixel
    if not mpp:
        raise ValueError("the project has no scale: calibrate it first")
    traced = {o.name: o for o in project.objects}

    svg = ET.Element("{%s}svg" % SVG_NS, {"version": "1.1"})
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    ET.register_namespace("inkscape", INK_NS)
    ET.register_namespace("sodipodi", SODI_NS)

    # `parts` names what to draw and overrides the rib filter, which
    # is how the fuselage keels get a drawing of their own: they sit
    # on one shared frame rather than one frame each, so there is no
    # "-R"/"-L" to select on.
    ribs = []
    for p in core_plan(airframe):
        if parts is not None:
            if p["part"] not in parts:
                continue
        else:
            fr = p["frame"]
            if not fr.startswith("Rib-") or not fr.endswith("-" + side):
                continue
        obj = traced.get(p["part"])
        if obj is None:
            continue
        ribs.append((p, obj))
    if not ribs:
        raise ValueError("nothing to draw (side %r, parts %r)"
                         % (side, parts))

    # The drawing frame: x is span, y is along the fuselage (aft is +y,
    # matching the scan, where the nose is at the top).
    layer_img = ET.SubElement(svg, "{%s}g" % SVG_NS, {
        "id": "plan",
        "{%s}label" % INK_NS: "plan (locked)",
        "{%s}insensitive" % SODI_NS: "true"})
    if image_path:
        ox, oy = image_origin_mm
        attrs = {"x": "%.4f" % ox, "y": "%.4f" % oy, "id": "plan-scan"}
        if image_size_mm:
            # An <image> with no width/height renders as nothing in
            # most viewers, so the size is required in practice.
            attrs["width"] = "%.4f" % image_size_mm[0]
            attrs["height"] = "%.4f" % image_size_mm[1]
        img = ET.SubElement(layer_img, "{%s}image" % SVG_NS, attrs)
        img.set("{%s}href" % XLINK_NS, image_path)
        img.set("href", image_path)

    layer = ET.SubElement(svg, "{%s}g" % SVG_NS, {
        "id": "ribs", "{%s}label" % INK_NS: "ribs (drag these)"})

    xs, ys = [], []
    for p, obj in ribs:
        name = p["part"]
        contour = next((c for c in obj.contours
                        if getattr(c, "role", "outer") != "hole"), None)
        if contour is None:
            continue
        pts = [(x * mpp, y * mpp) for x, y in contour.points]
        x0 = min(q[0] for q in pts)
        y0 = min(q[1] for q in pts)
        # the rib drawn nose-up at the origin, then moved into place
        local = [(q[0] - x0, q[1] - y0) for q in pts]
        inplane = p.get("in_plane_mm") or [0.0, 0.0]
        if upright:
            # A keel IS a side profile already: it is drawn as traced,
            # positioned by its in-plane offset. A rib is a section
            # seen edge-on, so it gets turned a quarter and placed by
            # its span.
            span, aft = inplane[0], -inplane[1]
        else:
            span, aft = abs(p["offset_mm"]), inplane[0]

        g = ET.SubElement(layer, "{%s}g" % SVG_NS, {
            "id": "rib-%s" % name,
            "{%s}label" % INK_NS:
                "rib %s  (span %.1f mm)" % (name, span),
            # The rib is drawn ROTATED 90: its chord runs along y,
            # because a streamwise rib lies fore-and-aft on the plan.
            "transform": ("translate(%.4f,%.4f)" % (span, aft)
                          if upright else
                          "translate(%.4f,%.4f) rotate(-90)" % (span, aft)),
        })
        ET.SubElement(g, "{%s}path" % SVG_NS, {
            "d": _path_d(local),
            "fill": "none",
            "stroke": "#d00000",
            "stroke-width": "0.5",
        })
        ET.SubElement(g, "{%s}text" % SVG_NS, {
            "x": "2", "y": "-2",
            "font-size": "4", "fill": "#d00000",
            "transform": "rotate(90)",
        }).text = name
        xs.append(span)
        ys.append(aft)

    # The canvas has to hold the plan image as well as the ribs, so
    # size it to whichever reaches further.
    pad = 15.0
    img_w = image_size_mm[0] if image_size_mm else 0.0
    img_h = image_size_mm[1] if image_size_mm else 0.0
    w = max(max(xs) + pad, img_w + image_origin_mm[0]) + pad
    h = max(max(ys) + pad, img_h + image_origin_mm[1]) + pad
    svg.set("width", "%.3fmm" % w)
    svg.set("height", "%.3fmm" % h)
    svg.set("viewBox", "%.3f %.3f %.3f %.3f"
            % (image_origin_mm[0] - pad, -pad, w, h))

    ET.ElementTree(svg).write(path, encoding="utf-8",
                              xml_declaration=True)
    return len(ribs)


_NUM = r"-?[\d.]+(?:[eE][-+]?\d+)?"


def _mul(a, b):
    """Compose two 2x3 matrices: a, then b, applied to a point."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a2 * b1, a1 * b0 + a3 * b1,
            a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
            a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5)


def parse_transform(text):
    """An SVG transform list as one 2x3 matrix.

    Reading only `translate` was enough while the file came straight
    back, but an editor writes `matrix(...)` or `rotate(a,cx,cy)` as
    soon as a part is flipped or turned. Resolving the whole list and
    applying it answers where the geometry ENDED UP, whatever was
    used to put it there.
    """
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not text:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        v = [float(x) for x in re.findall(_NUM, args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            sy = v[1] if len(v) > 1 else v[0]
            t = (v[0], 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            c, s = math.cos(a), math.sin(a)
            t = (c, s, -s, c, 0.0, 0.0)
            if len(v) == 3:
                cx, cy = v[1], v[2]
                t = _mul(_mul((1, 0, 0, 1, cx, cy), t),
                         (1, 0, 0, 1, -cx, -cy))
        else:
            continue
        m = _mul(m, t)
    return m


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _path_points(d):
    """Points of an M/L/Z path, which is what `write_svg` emits."""
    out = []
    for _cmd, args in re.findall(r"([MLZmlz])([^MLZmlz]*)", d):
        v = [float(x) for x in re.findall(_NUM, args)]
        for i in range(0, len(v) - 1, 2):
            out.append((v[i], v[i + 1]))
    return out


def read_svg(path):
    """{rib name: [(x_mm, y_mm), ...]} as the geometry now sits.

    The points come back with every transform already applied, so a
    rib that was dragged, flipped and turned reads as where it is -
    there is no need to know which of those the editor recorded.
    """
    tree = ET.parse(path)
    out = {}
    for g in tree.iter("{%s}g" % SVG_NS):
        gid = g.get("id") or ""
        if not gid.startswith("rib-"):
            continue
        gm = parse_transform(g.get("transform"))
        for p in g.iter("{%s}path" % SVG_NS):
            m = _mul(gm, parse_transform(p.get("transform")))
            pts = [_apply(m, x, y) for x, y in _path_points(p.get("d", ""))]
            if pts:
                out[gid[4:]] = pts
    return out


def _span_of(pts):
    xs = [p[0] for p in pts]
    return (min(xs) + max(xs)) / 2.0


def compare(project, edited, traced_names=None):
    """What each edited rib says about PLACEMENT and about SHAPE.

    Returns {name: dict} with:
      span_mm   the rib's spanwise centre, as edited
      aft_mm    its leading edge along the fuselage
      chord_mm  as edited, and chord_traced_mm from the sheet
      shape_changed  True when the outline no longer matches the
                     traced one, beyond `tol`

    The split is deliberate and matches who owns what: a drag, flip
    or turn is PLACEMENT and belongs in the airframe, while a moved
    vertex is SHAPE and belongs on the parts sheet. Telling them
    apart by comparing chord and point count is enough to say which
    happened - saying exactly WHICH vertex moved is the job of
    whatever writes the shape back.
    """
    mpp = project.calibration.mm_per_pixel
    traced = {o.name: o for o in project.objects}
    out = {}
    for name, pts in edited.items():
        if traced_names and name not in traced_names:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        row = {
            "span_mm": round(_span_of(pts), 3),
            "aft_mm": round(min(ys), 3),
            "chord_mm": round(max(ys) - min(ys), 3),
            "width_mm": round(max(xs) - min(xs), 3),
            "points": len(pts),
        }
        o = traced.get(name)
        if o is not None and mpp:
            c = next((x for x in o.contours
                      if getattr(x, "role", "outer") != "hole"), None)
            if c is not None:
                tx = [q[0] for q in c.points]
                row["chord_traced_mm"] = round(
                    (max(tx) - min(tx)) * mpp, 3)
                row["shape_changed"] = (
                    abs(row["chord_mm"] - row["chord_traced_mm"]) > 0.5)
        out[name] = row
    return out
