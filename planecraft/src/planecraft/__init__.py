"""PlaneCraft: 2D tracing -> 3D model -> 2D parts for the cutter.

PageWright traces a plan's PARTS sheet. PlaneCraft reads the ASSEMBLY
drawing: where each part goes, which way it faces, and what has to be
added that the plan never had - motor mounts, servo bays, linkage
runs - before flattening the result back to sheets.

Geometry decisions live in the Rust core (`planecraft/core`). This
package reads PageWright projects and drives whichever CAD tool
builds the model, through its own interface.
"""

__version__ = "0.1.0"
