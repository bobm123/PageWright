# Page stitching — a feature request from ModelCraft

**Raised:** 2026-11-26, by Robert, while tracing the Pogo plan in
ModelCraft.

**Status:** not started. This is a brief, not a design.

## What is being asked for

PageWright should be able to **join two page images side by side**
into one, rotating either as needed to bring them into line.

It belongs here rather than in a downstream app for the same reason
dewarping and calibration do: it is **image work**. PageWright
already owns scaling, `dewarp.py`, `calibration.py` and `tiling.py`.
A plan spread across two scanner beds is the same kind of problem as
a plan photographed at an angle — the drawing is fine, the capture
is not.

## Why it came up

ModelCraft's Pogo sample traces a fuselage plan that spans pages 16
and 17 of the magazine. Those two scans were stitched **by hand**,
outside any tool, before tracing could start:

| file | size | note |
|---|---|---|
| `p16-17-spread.png` | 3308 × 2316 | two pages joined |
| `p18-printwood.png` | 1700 × 2340 | one page |

Two things to read off that. The spread is a little under twice one
page wide, so the join **overlapped** rather than abutted. And it is
**24 px shorter** than the single page, so bringing the two into
line cost a trim — the pages did not arrive square to each other.

A part traced across such a seam is one part. Getting the join wrong
puts a kink in the middle of a fuselage side, and nothing downstream
can tell that from a kink the draughtsman drew.

## What already exists to build on

- `Project.pages` — a list of `PageEntry` (the "M1" multi-page job).
  Each has `source_path`, `objects`, `roi`, `measurements`. So a
  project can already hold the two pages that are to be joined.
- `core/dewarp.py`, `core/calibration.py`, `core/image_io.py` —
  the image-side neighbours this would sit among.
- `core/transform.py` — rotate/flip/translate, but for **loops**
  (traced geometry), not images. Image rotation is separate work.

Nothing in `src/` mentions stitching today.

## Things worth deciding early

**Rotation is not optional.** Two scans of facing pages are rarely
square to each other. The Pogo spread's 24 px height loss is the
evidence. Whatever the join does, it has to allow a small rotation
of at least one page — and a quarter turn when a page was scanned
sideways.

**What drives the alignment?** Three candidates, in rising order of
ambition:
1. The user drags one page over the other and says when it looks
   right. Honest, always works, and probably the thing to build
   first.
2. Two corresponding points picked on each page — the ends of a
   line that crosses the seam, say — which gives rotation and
   offset exactly.
3. Automatic feature matching across the overlap. Attractive, and
   the one most likely to fail quietly on a plan that is mostly
   white paper with thin lines.

**Scale between the pages.** Two scans at the same DPI need no
scaling. Two **photographs** will. If the pages disagree, say so
rather than silently resampling: a plan that has been quietly
stretched 2% is worse than one that obviously does not line up,
because the first kind of error survives into the cut parts.

**What the output is.** A new image on disk is simplest and is what
the hand-stitch produced. A *virtual* spread — two pages with a
recorded transform between them — keeps the originals and lets the
join be corrected later, but every consumer then has to understand
it. The first is probably right; the second is worth a thought
before committing.

**Calibration after the join.** The stitched image's scale is
whatever the pages had, if they agreed. Worth re-measuring across
the seam as a check: a known length that spans the join is the best
test that the join is right, and it is free — ModelCraft's whole
cross-check idea rests on measuring the same part twice.

## Not asked for

Nothing here is about more than two pages, or about joining top to
bottom rather than side by side. Both are obvious extensions; neither
was requested, and the Pogo case needs neither.

## Who consumes this

ModelCraft takes PageWright's output as a sheet image plus a
`mm_per_pixel`. It does not care how the image was made. A stitched
spread is just a wider sheet to it — which is the point: the seam
should stop being something a human has to deal with before the
real work starts.
