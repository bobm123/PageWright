# PlaneCraft handoff — 2026-11-13

Written before a machine shutdown. Everything below is committed;
`git status` is clean at `3842c8f` plus the two fixes in this
session's final commit.

## What this is

A model-aircraft plan becomes a 3D structure and, eventually, cut
files:

    scan a plan  ->  trace parts (PageWright)
                 ->  place them in an airframe (PlaneCraft)
                 ->  modify in CAD
                 ->  flatten back to sheets for the laser

**PageWright** (this repo, Python/PySide6) owns the *parts sheet*:
what shape a part is and what stock it is cut from. Unchanged in
purpose; dewarp and OCR work is still queued for it.

**PlaneCraft** (`planecraft/`, Rust core + Python) owns the *assembly
drawing*: where each part goes and which way it faces. Started this
session series; see PLAN.md §16.16 for why it is separate.

## State: the F-86D Sabre (Comet kit R5)

Working files live in `D:\Projects\Airplanes\F86-Sabre\3DModels\`.

| file | what |
|---|---|
| `sheet2.tiproj.json` | 27 traced parts, the live PageWright project |
| `sheet2.tiproj.json.27parts` | backup of the same (earlier backups: `.24parts-stocked`, `.pre1_8`, `.20parts`, `.bak`) |
| `fuselage_script.json` | 740 SolidCraft commands, generated |
| `F86_fuselage.solidcraft` | the built model, 13 bodies |
| `F86_fuselage.stl` | exported geometry, 21328.8 mm³ |
| `plan_sheet1_300dpi.png` | the assembly drawing (side view, wing plan) |
| `parts_sheet2_300dpi.png` | the parts sheet (3 sub-sheets) |

The airframe itself is version-controlled at
`planecraft/samples/f86-fuselage.airframe.json`, with the measured
stations in `planecraft/samples/f86-stations.json`.

**Built so far: 10 formers + 3 keels = 13 parts**, 302.7 mm nose to
tail. Formers 1 and 2 are 1/8 in laminated face to face; the rest
1/16 in. Formers 5, 7 and 10 carry `rotate_deg: 90` because they are
nested sideways on the parts sheet.

## Next task, where it stopped

**Trace the remaining wing ribs.** PageWright was open for this and
nothing was traced, so the project is untouched since 2026-11-07.

To trace: **A, C, D, E** (full-chord ribs; B is done) and **W1, W2,
W3** (wing mounts). All on the top strip of the parts sheet except
W2, which is on the 1/8 sheet and needs that stock assigned.

Trace ONE of each: the sheet prints two of every rib, one per wing,
and PlaneCraft places a single outline twice, mirrored.

**Then the harder part.** The wing is SWEPT, so a rib is not a plain
XZ offset. Spanwise stations were measured at roughly ±21, ±45, ±82
and −101 mm from the centreline (the wing is mirror-symmetric to
within 0.01–0.04 in; verified independently by correlating the wing
against its own mirror, 87% pixel agreement). Each rib also needs a
rotation, or an oriented frame, to lie along the sweep.

The user's own suggestion for this, not yet acted on: identify a
polygon on the plan that serves as the **wing planform**, since the
leading and trailing edges are strip stock (1/16 × 1/4, 1/8 square)
rather than traced parts.

## How to rebuild the model

```bash
cd planecraft
PYTHONPATH=src python -c "
import json
from planecraft import parts, solidcraft
p = parts.load(r'D:\Projects\Airplanes\F86-Sabre\3DModels\sheet2.tiproj.json')
air = json.load(open('samples/f86-fuselage.airframe.json'))
air['thickness_mm'] = {k: v for k, v in parts.thicknesses(p).items() if v}
solidcraft.write_script(p, air, r'D:\Projects\Airplanes\F86-Sabre\3DModels\fuselage_script.json')
"
solidcraft-cli script "D:/Projects/Airplanes/F86-Sabre/3DModels/fuselage_script.json" \
  --out "D:/Projects/Airplanes/F86-Sabre/3DModels/F86_fuselage.solidcraft"
solidcraft-cli run --in  "D:/Projects/Airplanes/F86-Sabre/3DModels/F86_fuselage.solidcraft" \
                   --out "D:/Projects/Airplanes/F86-Sabre/3DModels/F86_fuselage.stl"
```

`solidcraft-cli inspect <design>` verifies it. **SolidCraft cannot
render here** — `snapshot` segfaults (exit 139) and `render` fails at
the PNG write. Export the STL and draw it locally instead; PLAN
§16.26 has the method.

## Tests

| suite | command | count |
|---|---|---|
| PageWright | `python -m pytest tests -q` (repo root) | 513 passed, 1 skipped |
| PlaneCraft | `python -m pytest tests -q` (in `planecraft/`) | 49 passed |
| Rust core | `cargo test` (in `planecraft/core/`) | 28 passed |

`planecraft/core` must be built (`cargo build`) or the Python tests
that need the binary skip rather than fail. `CARGO_TARGET_DIR` points
outside the repo on this machine, so `target/` never appears here.

## Things a future session should know

**Geometry measured from a scan is reliable; identity read off it is
not.** Three wrong readings in a row were all the same mistake:
taking a label from a crowded 1952 drawing as settled. `F6` was read
as "former 6" when it is the cockpit floor; leader arrows were
assumed to point rightward when they vary; an arrow landing was
measured to the wrong line. The POSITIONS never moved. A count that
has to balance — ten traced parts, ten lines — beats any single
reading of a label.

**A parts sheet is a nesting, not an assembly drawing.** Orientation
on the sheet says nothing about orientation in the aeroplane.

**The script generator has dropped two `Placement` fields in two
sessions** — `rotate_deg`, then `in_plane_mm` — each silently. Before
adding a field, check `solidcraft.py` against `Placement` field by
field rather than feature by feature.

**Craft MCP servers are USER scope**, registered by
`D:\Programs\register-craft-mcp.ps1` (idempotent). A project
`.mcp.json` duplicates them and `claude mcp list` then warns about
conflicting scopes. One was added this session and removed again;
PLAN §16.29 records the flag details in case a scoped server is ever
wanted. Never use an app's own `mcp setup` — it rewrites
`~/.claude.json`.

## Designed but deliberately not built

Recorded in PLAN so the next decision is made knowing where it leads:

* **§16.22 — the workflow is iterative.** 2D→3D runs many times;
  KiCad's "update from schematic" is the right analogy. `Placement`
  has no transform field, so there is nowhere to record "moved in CAD,
  now here", and a re-import would reset hand placement.
* **§16.24 — laminations point at a bigger idea.** A lamination is a
  part placed relative to another PART rather than to a frame. The
  same mechanism would serve the CAD round trip: a placement whose
  reference is not always a fixed plane.
* **The return leg (`planecraft/README.md`).** A modified model has to
  come back to 2D, possibly as SVG for Inkscape or VectorCraft to
  re-nest for grain and stock. Not designed, because there is no
  modified model yet — but nothing is keyed on list order, so
  re-nesting cannot break a reference.
