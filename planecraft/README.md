# PlaneCraft

Where a traced part goes. The workflow is

    2D tracing  ->  3D model  ->  modify  ->  2D parts for the cutter

and PlaneCraft owns everything after the tracing.

## Why it is not part of PageWright

A model-aircraft plan has two drawings that answer different
questions:

* the **parts sheet** — what shape a part is, what stock it is cut
  from. PageWright traces this.
* the **assembly drawing** — where each part goes and which way it
  faces: formers as circled numbers at their stations, longerons as
  lines along a side view, stock callouts for structure that has no
  traced outline at all (`1/8 SQ LEADING EDGE`, `SPAR 1/16 x 3/16`).

Placement is reading the second drawing and matching it against the
first. None of that is tracing, and PageWright has book dewarping and
OCR still ahead of it, so the two stay apart.

## Shape

    planecraft/
      core/            Rust: frames, placement, validation
      src/planecraft/  Python: reads PageWright, drives CAD
      tests/

**Rust for the core.** Frames, stations and placement are the part
with invariants worth holding still, and the enum/serde pair makes an
unknown plane unrepresentable rather than merely discouraged.

**Python for the joints.** Everything that strings tools together
through an MCP or a CLI: the Fusion MCP takes a Python script, the
SolidCraft importer is a Python program, and PageWright is a Python
library imported directly rather than copied.

They meet over a subprocess (`planecraft-core check|plan`, JSON on
stdin and stdout). No binding layer to keep in step, and it is one
call per airframe rather than per part. The JSON shapes are already
the interface if pyo3 ever earns its place.

## Frames: the thing that must stay open

A `Frame` is a **name**, plus *optionally* enough to build it:

```json
{"name": "Former-3", "def": {"kind": "offset", "base": "YZ", "offset_mm": 76.2}}
{"name": "Firewall", "def": {"kind": "oriented", "origin_mm": [...], "normal": [...], "up": [...]}}
{"name": "SomethingNew"}
```

That last one is the important case. A frame with no definition is a
**reference**: the CAD side already has a plane by that name and
PlaneCraft just points at it. Naming a construction is always
possible even when defining it here is not, so an angled plane, a
plane through three points, or whatever a CAD tool offers next is
never a blocker.

## Stock sizes: PageWright's algorithm, called from here

Snapping an edge to 1/16 in needs a scale, and a calibrated scan is
PageWright's business, so `core/notches.py` stays there. It is
already scale-agnostic - points in, points out, with the scale as an
argument, and no PageWright model type anywhere in it - so
`planecraft.stock` calls it rather than growing a second copy:

```python
from planecraft import stock
pts, report = stock.snap_mm(longeron_mm, stock="1/16")
```

`mm_per_pixel=1.0` makes the "pixels" millimetres, which is what
geometry drawn in CAD already is. A longeron drawn 1.70 mm tall and
0.12 mm out of square comes back square at exactly 1.5875 mm. An edge
too far from a multiple is left as drawn and counted in the report,
never dragged to a size nobody asked for.

## Refusing to guess

A placement whose frame does not exist is **reported and left out**,
never dropped onto a default. A part in the wrong place is worse than
one that is obviously still flat. `frames_at` likewise refuses a
parts list and a stations list of different lengths: a frame with a
guessed station builds a fuselage that looks right and is not.

## Building

```bash
cd core && cargo build --release && cargo test
cd .. && python -m pytest tests -q
```

The Python layer finds the binary under `core/target/`, via
`CARGO_TARGET_DIR`, on `PATH`, or at `$PLANECRAFT_CORE`.

## Status

Early. The core places parts on frames and validates an airframe;
the Python layer reads a PageWright project and generates frames
from stations. Not yet written: emitting to CAD (the SolidCraft
recipe and the Fusion MCP are both reachable), structure the plan
draws but no polygon exists for, and flattening a modified model back
to sheets.
