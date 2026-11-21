"""Lay parts out on stock sheets, ready to cut.

A traced plan is a nesting the DRAUGHTSMAN made, for his paper and
his printing. What the builder needs is a nesting for the stock he
actually has: a 3 x 36 in sheet of 1/16 balsa, say, with every part
on it the right way round and the right number of times. Those are
different problems, and the second one is this module.

WHAT A PART NEEDS BESIDES ITS OUTLINE. Two things the outline cannot
say, both of which must be told to this module rather than guessed:

  * HOW MANY. A plan says "MAKE TWO" in text beside the part. More
    quietly, the Pogo fuselage formers are HALF formers - a left and
    a right meeting at the centreline, each carrying a 1/32 in notch
    there so the two together make the 1/16 in slot the crutch keel
    passes through - so every one of them needs a mirrored twin even
    though nothing on the drawing says so.
  * WHICH WAY ROUND. A mirrored twin is not a second copy of the
    same cut: it is the outline flipped. Cutting two identical halves
    gives two left halves and no fuselage.

So a `Piece` carries `count` and `mirror`, and the caller sets them.
This module will not read "MAKE TWO" off the drawing and will not
infer that a narrow part must be a half former: both are guesses
that would be wrong somewhere, and a wrongly doubled part wastes
stock while a wrongly single one is found at the bench.

WHY BOUNDING BOXES. Parts are packed by their bounding box, in rows,
tallest first. True shape nesting - letting a former's curve sit
inside another's - would save material, but it is a hard packing
problem and the saving is balsa, which is cheap, against cut time
and verification effort, which are not. The rows are predictable
enough to check by eye against the emitted sheet, which matters more
here than the last 15% of a sheet of balsa. `nest` returns
placements, so a better packer can replace the inside of this
function without anything downstream noticing.

Rotation is deliberately NOT applied to make parts fit. A part that
only fits turned sideways is reported as not fitting, because
turning a part across the grain is a decision about the model's
strength, not about packing - balsa is many times stiffer along the
grain, and a former turned 90 degrees to save stock can be the one
that breaks. The caller may pre-rotate a part if it wants to.

Pure arithmetic on point lists: no Qt, no cv2, no I/O.
"""

from . import transform

# Space left between neighbouring parts, in millimetres. Enough for a
# knife or a laser kerf plus the wobble of cutting by hand, without
# giving away sheet area: 1.5 mm is a shade under 1/16 in.
DEFAULT_GAP_MM = 1.5

# Space left at the sheet's edges, in millimetres. Sheet stock is
# often ragged or slightly out of square at the ends, and a part
# placed hard against the edge is the one that comes out short.
DEFAULT_MARGIN_MM = 2.0


class NestingError(Exception):
    pass


class Piece:
    """One part to be cut, and how many of it.

    `loops` is a list of point lists in millimetres, the first being
    the outline and any others holes, exactly as a traced object's
    contours convert to.

    `count` is how many of this part the model needs IN TOTAL,
    mirrored ones included. `mirror` says the extras are mirror
    images rather than copies: with count=2 and mirror=True one is
    emitted as traced and one flipped. A count above 2 with mirror
    set alternates, which is what a four-piece set of wheel pants
    wants - two left, two right.
    """

    def __init__(self, name, loops, count=1, mirror=False):
        if count < 1:
            raise NestingError("%s: count must be at least 1" % name)
        self.name = name
        self.loops = [list(loop) for loop in loops]
        self.count = int(count)
        self.mirror = bool(mirror)

    def instances(self):
        """The individual cuts this part becomes, in order.

        Each is (label, loops). A single part keeps its own name; a
        set gets a suffix, "-L"/"-R" when mirrored because that is
        what the builder needs to tell them apart, and a plain number
        when they are identical and it does not matter which is which.
        """
        out = []
        for i in range(self.count):
            if self.count == 1:
                label = self.name
            elif self.mirror:
                label = "%s-%s" % (self.name, "LR"[i % 2])
            else:
                label = "%s-%d" % (self.name, i + 1)
            loops = self.loops
            if self.mirror and i % 2 == 1:
                loops = transform.flip(self.loops, horizontal=True)
            out.append((label, loops))
        return out


class Placement:
    """One cut, positioned on a sheet.

    `loops` are already moved into sheet coordinates, so a caller
    writing an SVG needs no further arithmetic. `dx`/`dy` are kept
    as well so a report can say where the part went.
    """

    def __init__(self, label, loops, dx, dy, width, height):
        self.label = label
        self.loops = loops
        self.dx = dx
        self.dy = dy
        self.width = width
        self.height = height

    def __repr__(self):
        return ("Placement(%r, at (%.1f, %.1f), %.1f x %.1f)"
                % (self.label, self.dx, self.dy, self.width, self.height))


class Sheet:
    """One piece of stock, with what ended up on it."""

    def __init__(self, width, height, placements=None):
        self.width = width
        self.height = height
        self.placements = list(placements or [])

    def used_fraction(self):
        """Fraction of the sheet's area covered by part bounding boxes.

        Bounding boxes, not outlines: this says how much of the sheet
        the NESTING consumed, which is the number that tells you
        whether to try a different sheet size. It is not how much
        balsa becomes aeroplane.
        """
        if self.width <= 0 or self.height <= 0:
            return 0.0
        area = sum(p.width * p.height for p in self.placements)
        return area / (self.width * self.height)


def _bounds(loops):
    xs = [x for loop in loops for x, _y in loop]
    ys = [y for loop in loops for _x, y in loop]
    return min(xs), min(ys), max(xs), max(ys)


def nest(pieces, sheet_width, sheet_height, gap_mm=None, margin_mm=None):
    """Pack `pieces` onto as many sheets as it takes.

    Sizes are millimetres. Returns (sheets, unplaced) where `sheets`
    is a list of Sheet and `unplaced` is a list of (label, reason)
    for cuts that do not fit on an empty sheet at all.

    Shelf packing, tallest first: parts are sorted by height, then
    filled left to right into a row whose height is the first part's,
    and a new row starts below when the next part will not fit. This
    is a decreasing-height shelf algorithm, which is within a small
    constant of optimal for strips and - more to the point here -
    produces a layout a human can check against the sheet.

    A cut too big for an empty sheet is reported rather than
    rotated: see the module docstring on grain.
    """
    if gap_mm is None:
        gap_mm = DEFAULT_GAP_MM
    if margin_mm is None:
        margin_mm = DEFAULT_MARGIN_MM
    if sheet_width <= 0 or sheet_height <= 0:
        raise NestingError("sheet must have a positive size")

    usable_w = sheet_width - 2.0 * margin_mm
    usable_h = sheet_height - 2.0 * margin_mm

    cuts = []
    for piece in pieces:
        for label, loops in piece.instances():
            if not loops or not loops[0]:
                continue
            x0, y0, x1, y1 = _bounds(loops)
            cuts.append((label, loops, x0, y0, x1 - x0, y1 - y0))

    # Tallest first, then widest: the shelf heights then decrease, so
    # a short part can never be stranded under a tall row.
    cuts.sort(key=lambda c: (-c[5], -c[4], c[0]))

    sheets = []
    unplaced = []
    cur = None
    row_y = margin_mm
    row_h = 0.0
    pen_x = margin_mm

    for label, loops, x0, y0, w, h in cuts:
        if w > usable_w or h > usable_h:
            unplaced.append((label, "%.1f x %.1f mm exceeds the usable "
                                    "%.1f x %.1f mm of a sheet"
                             % (w, h, usable_w, usable_h)))
            continue

        if cur is None:
            cur = Sheet(sheet_width, sheet_height)
            sheets.append(cur)
            row_y = margin_mm
            row_h = h
            pen_x = margin_mm

        # Does it fit in the row we are filling?
        if pen_x + w > margin_mm + usable_w + 1e-9:
            # Next row down on this sheet.
            next_y = row_y + row_h + gap_mm
            if next_y + h > margin_mm + usable_h + 1e-9:
                # Sheet is full: start another.
                cur = Sheet(sheet_width, sheet_height)
                sheets.append(cur)
                row_y = margin_mm
            else:
                row_y = next_y
            row_h = h
            pen_x = margin_mm

        dx = pen_x - x0
        dy = row_y - y0
        cur.placements.append(Placement(
            label, transform.translate(loops, dx, dy),
            pen_x, row_y, w, h))
        pen_x += w + gap_mm
        if h > row_h:
            # Only possible on the first part of a row, given the sort.
            row_h = h

    return sheets, unplaced


# Balsa and ply sheet sizes a model shop actually stocks, in inches.
# Width first, then length: stock is sold as "3 x 36", and the grain
# runs along the length.
COMMON_STOCK_IN = ((2.0, 24.0), (3.0, 24.0), (4.0, 24.0),
                   (2.0, 36.0), (3.0, 36.0), (4.0, 36.0), (6.0, 36.0))


def choose_stock(pieces, candidates=None, gap_mm=None, margin_mm=None):
    """Which stock size cuts this job with the least waste.

    Returns a list of (width_mm, height_mm, sheets, unplaced), best
    first, so a caller can show the alternatives rather than only the
    winner - a builder often has one size in the drawer and wants to
    know what it costs him.

    Ranked by SHEET COUNT first and waste second. Two sheets at 65%
    waste beat one sheet at 77% on the arithmetic but not at the
    bench: the second sheet is another trip to the shop, and the
    offcut from the first is still usable stock. Measured on the Pogo
    job: 3 x 36 in takes two sheets and leaves the second 4% used,
    while 4 x 36 in takes one. Sizes that cannot take the job at all
    are left out entirely rather than ranked last.
    """
    if candidates is None:
        candidates = [(w * 25.4, h * 25.4) for w, h in COMMON_STOCK_IN]
    scored = []
    for width, height in candidates:
        # Stock is sold with the grain along the LENGTH, so the long
        # dimension is the one parts run along: nest into (length,
        # width), not (width, length).
        long_side = max(width, height)
        short_side = min(width, height)
        sheets, unplaced = nest(pieces, long_side, short_side,
                                gap_mm=gap_mm, margin_mm=margin_mm)
        if unplaced:
            continue
        area = long_side * short_side * len(sheets)
        used = sum(p.width * p.height
                   for s in sheets for p in s.placements)
        waste = 1.0 - (used / area) if area > 0 else 1.0
        scored.append((len(sheets), waste, long_side, short_side,
                       sheets, unplaced))
    scored.sort(key=lambda r: (r[0], r[1]))
    return [(w, h, sh, un) for _n, _waste, w, h, sh, un in scored]
