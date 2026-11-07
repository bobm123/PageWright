//! The airframe: which traced part goes where.
//!
//! PageWright answers "what shape is this part and what stock is it
//! cut from" - that is the PARTS sheet. This answers "where does it
//! go and which way does it face", which on a real plan is a SECOND
//! drawing: formers as circled numbers at their stations, longerons
//! as lines along a side view.
//!
//! So a `Placement` is a deliberate record, never inferred from where
//! a part was drawn. A plan sheet is nested to save balsa: the ribs
//! sit in a row and the stabiliser is turned ninety degrees.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::frame::Frame;

/// One part put somewhere.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Placement {
    /// The traced part's name, as PageWright knows it ("3", "R1").
    pub part: String,
    /// The `Frame` it sits on, by name.
    pub frame: String,
    /// Turn within the frame, degrees counter-clockwise.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub rotate_deg: f64,
    /// Shift within the frame's own plane, millimetres.
    #[serde(default, skip_serializing_if = "is_origin")]
    pub offset_mm: [f64; 2],
    /// Mirrored across the frame's u axis - a left wing rib built
    /// from the right wing's outline.
    #[serde(default, skip_serializing_if = "is_false")]
    pub mirrored: bool,
}

fn is_zero(v: &f64) -> bool {
    *v == 0.0
}

fn is_origin(v: &[f64; 2]) -> bool {
    v[0] == 0.0 && v[1] == 0.0
}

fn is_false(v: &bool) -> bool {
    !*v
}

impl Placement {
    pub fn new(part: impl Into<String>, frame: impl Into<String>) -> Self {
        Placement {
            part: part.into(),
            frame: frame.into(),
            rotate_deg: 0.0,
            offset_mm: [0.0, 0.0],
            mirrored: false,
        }
    }
}

/// What is wrong with an airframe, reported rather than guessed at.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Problem {
    /// A placement names a frame that does not exist. Left unplaced
    /// rather than dropped onto a default: a part in the wrong place
    /// is worse than a part obviously still flat.
    MissingFrame { part: String, frame: String },
    /// Two placements claim the same part.
    DuplicatePart { part: String },
    /// Two frames share a name, so a placement naming it is ambiguous.
    DuplicateFrame { name: String },
}

impl Problem {
    pub fn message(&self) -> String {
        match self {
            Problem::MissingFrame { part, frame } => format!(
                "{part}: no frame called {frame:?} - it stays flat until one exists"
            ),
            Problem::DuplicatePart { part } => {
                format!("{part}: placed more than once")
            }
            Problem::DuplicateFrame { name } => {
                format!("two frames called {name:?}; names must differ")
            }
        }
    }
}

/// The model: frames, and the parts placed on them.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Airframe {
    #[serde(default)]
    pub name: Option<String>,
    /// The project this takes its parts from - a PageWright
    /// `.tiproj.json`. Recorded so the two files can be matched up
    /// again without the user remembering which went with which.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parts_source: Option<String>,
    #[serde(default)]
    pub frames: Vec<Frame>,
    #[serde(default)]
    pub placements: Vec<Placement>,
}

impl Airframe {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn frame_named(&self, name: &str) -> Option<&Frame> {
        self.frames.iter().find(|f| f.name == name)
    }

    pub fn placement_of(&self, part: &str) -> Option<&Placement> {
        self.placements.iter().find(|p| p.part == part)
    }

    /// Everything wrong with this airframe, in a stable order.
    ///
    /// Reported together rather than failing on the first, because a
    /// user fixing a plan wants the whole list, not one item per run.
    pub fn problems(&self) -> Vec<Problem> {
        let mut out = Vec::new();

        let mut seen_frames: BTreeMap<&str, usize> = BTreeMap::new();
        for f in &self.frames {
            *seen_frames.entry(f.name.as_str()).or_insert(0) += 1;
        }
        for (name, n) in &seen_frames {
            if *n > 1 {
                out.push(Problem::DuplicateFrame { name: (*name).to_string() });
            }
        }

        let mut seen_parts: BTreeMap<&str, usize> = BTreeMap::new();
        for p in &self.placements {
            *seen_parts.entry(p.part.as_str()).or_insert(0) += 1;
        }
        for (part, n) in &seen_parts {
            if *n > 1 {
                out.push(Problem::DuplicatePart { part: (*part).to_string() });
            }
        }

        for p in &self.placements {
            if self.frame_named(&p.frame).is_none() {
                out.push(Problem::MissingFrame {
                    part: p.part.clone(),
                    frame: p.frame.clone(),
                });
            }
        }
        out.sort();
        out
    }

    /// The placements that can actually be built, paired with their
    /// frame. A placement whose frame is missing is left out - and
    /// `problems()` says why.
    pub fn resolved(&self) -> Vec<(&Placement, &Frame)> {
        self.placements
            .iter()
            .filter_map(|p| self.frame_named(&p.frame).map(|f| (p, f)))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::BasePlane;

    fn f86() -> Airframe {
        let mut a = Airframe::new();
        a.frames.push(Frame::station("Former-1", BasePlane::YZ, 0.0));
        a.frames.push(Frame::station("Former-2", BasePlane::YZ, 25.4));
        a.placements.push(Placement::new("1", "Former-1"));
        a.placements.push(Placement::new("2", "Former-2"));
        a
    }

    #[test]
    fn a_clean_airframe_has_no_problems() {
        assert!(f86().problems().is_empty());
        assert_eq!(f86().resolved().len(), 2);
    }

    #[test]
    fn a_part_on_a_missing_frame_is_reported_and_left_out() {
        // Not dropped onto a default plane: a part in the WRONG place
        // is worse than one that is obviously still flat.
        let mut a = f86();
        a.placements.push(Placement::new("R1", "Rib-Sta-1"));
        let probs = a.problems();
        assert_eq!(probs.len(), 1);
        assert!(probs[0].message().contains("Rib-Sta-1"));
        assert_eq!(a.resolved().len(), 2); // the good two still build
    }

    #[test]
    fn placing_a_part_twice_is_reported() {
        let mut a = f86();
        a.placements.push(Placement::new("1", "Former-2"));
        assert!(a
            .problems()
            .iter()
            .any(|p| matches!(p, Problem::DuplicatePart { .. })));
    }

    #[test]
    fn two_frames_with_one_name_are_reported() {
        // The name IS the key a placement points at, so a duplicate
        // makes the answer depend on list order.
        let mut a = f86();
        a.frames.push(Frame::station("Former-1", BasePlane::YZ, 99.0));
        assert!(a
            .problems()
            .iter()
            .any(|p| matches!(p, Problem::DuplicateFrame { .. })));
    }

    #[test]
    fn every_problem_is_reported_not_just_the_first() {
        let mut a = f86();
        a.placements.push(Placement::new("R1", "nope"));
        a.placements.push(Placement::new("1", "Former-2"));
        assert_eq!(a.problems().len(), 2);
    }

    #[test]
    fn a_reference_frame_resolves_like_any_other() {
        // PlaneCraft cannot build an angled plane, but it can still
        // place a part on one the CAD side owns.
        let mut a = Airframe::new();
        a.frames.push(Frame::reference("FirewallAngled"));
        a.placements.push(Placement::new("B", "FirewallAngled"));
        assert!(a.problems().is_empty());
        let (_p, frame) = a.resolved()[0];
        assert!(!frame.is_buildable());
    }

    #[test]
    fn an_airframe_round_trips_through_json() {
        let a = f86();
        let text = serde_json::to_string_pretty(&a).unwrap();
        assert_eq!(serde_json::from_str::<Airframe>(&text).unwrap(), a);
    }

    #[test]
    fn a_plain_placement_serialises_without_its_defaults() {
        // These files are read next to a plan sheet; zero rotation and
        // no offset are noise that hides the fields that were set.
        let text = serde_json::to_string(&Placement::new("3", "Former-3")).unwrap();
        assert!(!text.contains("rotate_deg"), "{text}");
        assert!(!text.contains("offset_mm"), "{text}");
        assert!(!text.contains("mirrored"), "{text}");
    }
}

#[cfg(test)]
mod order_tests {
    use super::*;
    use crate::frame::BasePlane;

    fn air() -> Airframe {
        let mut a = Airframe::new();
        a.frames.push(Frame::station("F-1", BasePlane::YZ, 0.0));
        a.frames.push(Frame::station("F-2", BasePlane::YZ, 25.4));
        a.frames.push(Frame::reference("Wing"));
        a.placements.push(Placement::new("1", "F-1"));
        a.placements.push(Placement::new("2", "F-2"));
        a.placements.push(Placement::new("R1", "Wing"));
        a
    }

    /// Re-nesting a sheet re-orders parts. Nothing may be keyed on
    /// that order, or a part moved to another sheet loses its frame.
    #[test]
    fn reordering_changes_nothing_that_matters() {
        let a = air();
        let mut b = air();
        b.frames.reverse();
        b.placements.reverse();

        assert_eq!(a.problems(), b.problems());
        for part in ["1", "2", "R1"] {
            let fa = a.placement_of(part).unwrap();
            let fb = b.placement_of(part).unwrap();
            assert_eq!(fa, fb);
            assert_eq!(
                a.frame_named(&fa.frame).unwrap(),
                b.frame_named(&fb.frame).unwrap()
            );
        }
        // same set of resolved pairs, whatever the order
        let mut ra: Vec<_> = a.resolved().iter().map(|(p, f)| (&p.part, &f.name)).collect();
        let mut rb: Vec<_> = b.resolved().iter().map(|(p, f)| (&p.part, &f.name)).collect();
        ra.sort();
        rb.sort();
        assert_eq!(ra, rb);
    }

    /// A part keeps its identity across a round trip, so a nester may
    /// rewrite the file wholesale without breaking references.
    #[test]
    fn identity_survives_a_json_round_trip_in_any_order() {
        let mut b = air();
        b.placements.reverse();
        let text = serde_json::to_string(&b).unwrap();
        let back: Airframe = serde_json::from_str(&text).unwrap();
        assert_eq!(back.placement_of("R1").unwrap().frame, "Wing");
        assert!(back.problems().is_empty());
    }
}
