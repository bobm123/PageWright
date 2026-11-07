//! PlaneCraft core: where a traced part sits in an airframe.
//!
//! The workflow this serves: trace a plan in PageWright, place the
//! parts into a 3D structure, modify it for things the plan never had
//! (motor mounts, servo bays, linkage runs), then flatten the result
//! back to sheets for the laser cutter.
//!
//! PageWright owns the PARTS sheet: what shape a part is and what
//! stock it comes from. This owns the ASSEMBLY drawing: where each
//! part goes and which way it faces. Those are different questions
//! answered by different drawings, which is why they are different
//! programs.
//!
//! No CAD dependency lives here. This crate decides WHAT should be
//! built; the Python layer talks to whichever tool builds it (the
//! SolidCraft CLI, the Fusion MCP), because that is a question of
//! stringing tools together rather than of geometry.

pub mod airframe;
pub mod frame;

pub use airframe::{Airframe, Placement, Problem};
pub use frame::{BasePlane, Frame, FrameDef};
