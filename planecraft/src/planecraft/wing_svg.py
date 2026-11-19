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
              image_origin_mm=(0.0, 0.0), image_size_mm=None, side="R"):
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

    ribs = []
    for p in core_plan(airframe):
        fr = p["frame"]
        if not fr.startswith("Rib-") or not fr.endswith("-" + side):
            continue
        obj = traced.get(p["part"])
        if obj is None:
            continue
        ribs.append((p, obj))
    if not ribs:
        raise ValueError("no ribs on side %r" % (side,))

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
        span = abs(p["offset_mm"])
        aft = (p.get("in_plane_mm") or [0.0, 0.0])[0]

        g = ET.SubElement(layer, "{%s}g" % SVG_NS, {
            "id": "rib-%s" % name,
            "{%s}label" % INK_NS:
                "rib %s  (span %.1f mm)" % (name, span),
            # The rib is drawn ROTATED 90: its chord runs along y,
            # because a streamwise rib lies fore-and-aft on the plan.
            "transform": "translate(%.4f,%.4f) rotate(-90)" % (span, aft),
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


_TRANSLATE = re.compile(
    r"translate\(\s*(-?[\d.eE+]+)[ ,]+(-?[\d.eE+]+)\s*\)")


def read_svg(path):
    """{rib name: (span_mm, aft_mm)} from an edited file.

    Reads the `translate` on each `rib-<name>` group. Inkscape writes
    a drag as a change to that transform, so what comes back is where
    the user put it.
    """
    tree = ET.parse(path)
    out = {}
    for g in tree.iter("{%s}g" % SVG_NS):
        gid = g.get("id") or ""
        if not gid.startswith("rib-"):
            continue
        m = _TRANSLATE.search(g.get("transform") or "")
        if m:
            out[gid[4:]] = (float(m.group(1)), float(m.group(2)))
    return out
