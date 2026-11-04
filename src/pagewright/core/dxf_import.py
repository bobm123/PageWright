"""Read a DXF of flat parts back into traced outlines.

The return leg of the CAD round trip. A part goes out as a recipe, a
designer cuts a servo bay or a lightening hole into it, and it comes
back as DXF - the format every laser cutter and CAM tool speaks. This
reads that file so the modified part can be printed as a 1:1 pattern
again.

Deliberately small. A DXF can describe almost anything; what SolidCraft
writes for a flat part is LINE, ARC and CIRCLE entities in a flat
ENTITIES section, with splines already flattened into short LINEs
(measured: a four-point fit spline exported as 72 LINEs). Those three
are handled. Anything else is COUNTED AND REPORTED rather than ignored,
because a silently dropped entity is a hole missing from a cut part.

Entities are chained into closed loops by their endpoints, since a DXF
has no notion of which lines form a profile - only a soup of segments.
"""

import math

# Scale to millimetres by the header's $INSUNITS. 1=inches, 4=mm,
# 5=cm, 6=m. Anything else (or absent) is treated as mm, which is what
# SolidCraft writes.
_INSUNITS_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}

# How finely an arc or circle is flattened, as the chord sagitta. The
# model is polyline-only (see svg_import), so every curve becomes
# points and this decides how many.
#
# 0.2 mm is the default rather than something tighter because of what
# these drawings are: a 1950s plan was drawn at this size with a French
# curve through a couple of control points, so sub-tenth-millimetre
# fidelity is reproducing the draughtsman's hand, not the aeroplane. It
# is also finer than a laser kerf. A 7 mm hole costs 19 points at
# 0.05 mm and 10 at 0.2 mm.
DEFAULT_SAGITTA_MM = 0.2

# Kept as the old name so existing callers keep working.
SAGITTA_MM = DEFAULT_SAGITTA_MM

# Endpoints this close are the same point. Chosen above SolidCraft's
# printed precision (1e-9 mm) and below any real feature.
JOIN_TOL_MM = 1e-4

SUPPORTED = ("LINE", "ARC", "CIRCLE")


class DxfImportError(Exception):
    pass


def _pairs(text):
    """DXF group codes as (code, value), two lines at a time."""
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        code = lines[i].strip()
        if not code.lstrip("-").isdigit():
            continue
        yield int(code), lines[i + 1].strip()


def _arc_points(cx, cy, r, start_deg, end_deg, scale,
                sagitta_mm=None):
    """An arc as a polyline, fine enough that the chord error stays
    under `sagitta_mm` once scaled to mm."""
    sagitta_mm = DEFAULT_SAGITTA_MM if not sagitta_mm else sagitta_mm
    sweep = (end_deg - start_deg) % 360.0
    if sweep <= 0.0:
        sweep = 360.0
    r_mm = r * scale
    if r_mm <= sagitta_mm:
        steps = 4
    else:
        # chord sagitta: s = r(1 - cos(step/2))
        step = 2.0 * math.acos(max(-1.0, 1.0 - sagitta_mm / r_mm))
        steps = max(2, int(math.ceil(math.radians(sweep) / step)))
    out = []
    for i in range(steps + 1):
        a = math.radians(start_deg + sweep * i / steps)
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def parse_entities(text, sagitta_mm=None):
    """DXF text -> (segments, scale_to_mm, unsupported counts).

    A segment is a list of (x, y) in the file's own units; the caller
    scales. Closed entities (CIRCLE, a full ARC) come back as one
    segment whose ends coincide.
    """
    scale = _insunits_scale(text)   # before any arc: it sets the sagitta
    segments = []
    skipped = {}

    current = None          # (type, {code: value})
    in_entities = False
    section = None
    awaiting_name = False   # the code 2 right after SECTION names it

    def flush():
        if current is None:
            return
        kind, g = current
        try:
            if kind == "LINE":
                segments.append([(g[10], g[20]), (g[11], g[21])])
            elif kind == "CIRCLE":
                segments.append(_arc_points(g[10], g[20], g[40],
                                            0.0, 360.0, scale,
                                            sagitta_mm))
            elif kind == "ARC":
                segments.append(_arc_points(g[10], g[20], g[40],
                                            g[50], g[51], scale,
                                            sagitta_mm))
        except KeyError:
            skipped[kind] = skipped.get(kind, 0) + 1

    for code, value in _pairs(text):
        # Only the code 2 DIRECTLY after SECTION names it. TABLES and
        # BLOCKS carry their own code 2s, which is what made an early
        # version lose the geometry in a sheet.export_dxf file.
        if awaiting_name:
            awaiting_name = False
            if code == 2:
                section = value
                in_entities = (value == "ENTITIES")
                continue
        if code == 0:
            if value == "SECTION":
                flush()
                current, in_entities, section = None, False, None
                awaiting_name = True
                continue
            if value in ("ENDSEC", "EOF"):
                flush()
                current, in_entities, section = None, False, None
                continue
            flush()
            current = None
            if in_entities:
                if value in SUPPORTED:
                    current = (value, {})
                else:
                    skipped[value] = skipped.get(value, 0) + 1
            continue
        if current is not None and code in (10, 20, 11, 21, 40, 50, 51):
            try:
                current[1][code] = float(value)
            except ValueError:
                pass

    return segments, scale, skipped


def _insunits_scale(text):
    """Millimetres per file unit, from the HEADER's $INSUNITS.

    Read only inside HEADER: a 9/70 pair elsewhere is a different
    variable. Absent or unknown means mm, which is what SolidCraft
    writes ($INSUNITS 4).
    """
    in_header = False
    awaiting_name = False
    want = False
    for code, value in _pairs(text):
        if awaiting_name:
            awaiting_name = False
            if code == 2:
                in_header = (value == "HEADER")
                continue
        if code == 0:
            if value == "SECTION":
                awaiting_name = True
            elif value in ("ENDSEC", "EOF"):
                if in_header:
                    break
                in_header = False
            continue
        if not in_header:
            continue
        if code == 9:
            want = (value == "$INSUNITS")
        elif want and code == 70:
            try:
                return _INSUNITS_MM.get(int(value), 1.0)
            except ValueError:
                return 1.0
    return 1.0


def _key(p, tol):
    return (round(p[0] / tol), round(p[1] / tol))


def chain_loops(segments, tol):
    """Join segments end to end into loops.

    A DXF is a soup of entities with no profile structure, so the
    outline has to be rebuilt from coincident endpoints. Open chains
    are returned too - a part with a gap is still worth seeing, and
    the caller decides.
    """
    remaining = [list(s) for s in segments if len(s) >= 2]
    loops = []
    while remaining:
        chain = remaining.pop(0)
        grew = True
        while grew:
            grew = False
            if _key(chain[0], tol) == _key(chain[-1], tol):
                break
            for i, seg in enumerate(remaining):
                if _key(seg[0], tol) == _key(chain[-1], tol):
                    chain.extend(seg[1:])
                elif _key(seg[-1], tol) == _key(chain[-1], tol):
                    chain.extend(reversed(seg[:-1]))
                elif _key(seg[-1], tol) == _key(chain[0], tol):
                    chain[:0] = seg[:-1]
                elif _key(seg[0], tol) == _key(chain[0], tol):
                    chain[:0] = list(reversed(seg[1:]))
                else:
                    continue
                remaining.pop(i)
                grew = True
                break
        loops.append(chain)
    return loops


def _area(points):
    a2 = 0.0
    for i in range(len(points)):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % len(points)]
        a2 += x0 * y1 - x1 * y0
    return abs(a2) / 2.0


def read_dxf(text, mm_per_pixel=None, sagitta_mm=None):
    """Parse DXF text into loops.

    Returns (loops, info). Each loop is a list of (x, y); in mm when
    `mm_per_pixel` is None, otherwise converted to pixels so it can be
    stored as a Contour, which is a pixel-space type.

    The largest-area closed loop is the outline; the rest are holes.
    `info` carries counts and anything unsupported, so a caller can
    say what was dropped instead of quietly losing it.
    """
    segments, scale, skipped = parse_entities(text, sagitta_mm)
    if not segments:
        raise DxfImportError(
            "no LINE, ARC or CIRCLE entities found"
            + (" (saw %s)" % ", ".join(sorted(skipped)) if skipped else ""))

    tol = JOIN_TOL_MM / scale if scale else JOIN_TOL_MM
    loops = chain_loops(segments, tol)

    closed, open_chains = [], 0
    for loop in loops:
        if len(loop) >= 4 and _key(loop[0], tol) == _key(loop[-1], tol):
            closed.append(loop[:-1])
        else:
            open_chains += 1
            closed.append(loop)

    closed.sort(key=_area, reverse=True)
    factor = scale if mm_per_pixel is None else scale / mm_per_pixel
    out = [[(x * factor, y * factor) for x, y in loop] for loop in closed]
    info = {
        "entities": len(segments),
        "loops": len(out),
        "open": open_chains,
        "units_mm": scale,
        "skipped": skipped,
    }
    return out, info
