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
    /// Glued to the face of another placement rather than sitting on
    /// the frame's own plane.
    ///
    /// Laminations are ordinary construction near a nose: several
    /// 1/8 in discs face to face give the builder material to sand
    /// the compound curves from, or structure for a rubber-power
    /// propeller. Recording it as "stacked on THAT part's front face"
    /// keeps the stack together when the part it is glued to moves -
    /// which a hand-copied station does not.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub laminate: Option<Laminate>,
}

/// Which face of which placement this part is glued to.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Laminate {
    /// The part it is glued to, by name. That part's own placement
    /// supplies the station; this one is offset from it.
    pub on: String,
    /// Which way the stack grows from that part.
    pub face: Face,
}

/// The side of a part a lamination is glued to.
///
/// Named by the frame's normal rather than by "front" and "back",
/// because which of those is forward depends on the plane: a former
/// on YZ has its normal down the fuselage, a rib on XZ across the
/// span.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Face {
    /// The face the frame normal points towards.
    Positive,
    /// The other one.
    Negative,
}

impl Face {
    /// +1 or -1: the direction a stack grows along the frame normal.
    pub fn sign(self) -> f64 {
        match self {
            Face::Positive => 1.0,
            Face::Negative => -1.0,
        }
    }
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
            laminate: None,
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
    /// Two placements put the same part on the same frame. Placing
    /// one traced part on SEVERAL frames is the normal case - a plan
    /// draws one rib and the wing uses it a dozen times, mirrored for
    /// the other side - so what must be unique is the placement, not
    /// the part.
    DuplicatePlacement { part: String, frame: String },
    /// Two frames share a name, so a placement naming it is ambiguous.
    DuplicateFrame { name: String },
    /// A lamination is glued to a part that is not placed anywhere.
    LaminateMissing { part: String, on: String },
    /// Laminations that refer to each other in a loop, so no station
    /// can be worked out for any of them.
    LaminateCycle { part: String },
}

impl Problem {
    pub fn message(&self) -> String {
        match self {
            Problem::MissingFrame { part, frame } => format!(
                "{part}: no frame called {frame:?} - it stays flat until one exists"
            ),
            Problem::DuplicatePlacement { part, frame } => {
                format!("{part}: placed on {frame:?} twice")
            }
            Problem::DuplicateFrame { name } => {
                format!("two frames called {name:?}; names must differ")
            }
            Problem::LaminateMissing { part, on } => format!(
                "{part}: laminated to {on:?}, which is not placed"
            ),
            Problem::LaminateCycle { part } => format!(
                "{part}: laminations refer to each other in a loop"
            ),
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

    /// Every place this part is built, in file order.
    ///
    /// A list, not an option: one traced outline becomes many bodies.
    /// The plan draws one rib B and the wing uses it on both sides.
    pub fn placements_of(&self, part: &str) -> Vec<&Placement> {
        self.placements.iter().filter(|p| p.part == part).collect()
    }

    /// How many times `part` is built.
    pub fn count_of(&self, part: &str) -> usize {
        self.placements_of(part).len()
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

        // Keyed on (part, frame), not part: one traced outline may be
        // built many times. The same part on the SAME frame is still
        // wrong - two bodies in one place.
        let mut seen: BTreeMap<(&str, &str), usize> = BTreeMap::new();
        for p in &self.placements {
            *seen.entry((p.part.as_str(), p.frame.as_str())).or_insert(0) += 1;
        }
        for ((part, frame), n) in &seen {
            if *n > 1 {
                out.push(Problem::DuplicatePlacement {
                    part: (*part).to_string(),
                    frame: (*frame).to_string(),
                });
            }
        }

        for p in &self.placements {
            if self.frame_named(&p.frame).is_none() {
                out.push(Problem::MissingFrame {
                    part: p.part.clone(),
                    frame: p.frame.clone(),
                });
            }
            if let Some(lam) = &p.laminate {
                if self.placements_of(&lam.on).is_empty() {
                    out.push(Problem::LaminateMissing {
                        part: p.part.clone(),
                        on: lam.on.clone(),
                    });
                } else if self.laminate_depth(&p.part, 0).is_none() {
                    out.push(Problem::LaminateCycle {
                        part: p.part.clone(),
                    });
                }
            }
        }
        out.sort();
        out.dedup();
        out
    }

    /// How many laminations deep `part` sits, or None if the chain
    /// loops. Bounded so a cycle cannot spin forever.
    fn laminate_depth(&self, part: &str, seen: usize) -> Option<usize> {
        if seen > self.placements.len() {
            return None;
        }
        let p = self.placements.iter().find(|p| p.part == part)?;
        match &p.laminate {
            None => Some(seen),
            Some(lam) => {
                if lam.on == part {
                    return None;          // glued to itself
                }
                self.laminate_depth(&lam.on, seen + 1)
            }
        }
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
    fn one_part_may_be_built_on_many_frames() {
        // The wing case: the plan draws ONE rib B and the wing uses
        // it on both sides, mirrored. Rejecting that would reject
        // nearly every part on a symmetric structure.
        let mut a = f86();
        a.frames.push(Frame::station("Rib-B-R", BasePlane::XZ, -21.0));
        a.frames.push(Frame::station("Rib-B-L", BasePlane::XZ, 21.0));
        a.placements.push(Placement::new("B", "Rib-B-R"));
        let mut left = Placement::new("B", "Rib-B-L");
        left.mirrored = true;
        a.placements.push(left);

        assert!(a.problems().is_empty(), "{:?}", a.problems());
        assert_eq!(a.count_of("B"), 2);
        assert_eq!(a.resolved().len(), 4);
    }

    #[test]
    fn the_same_part_on_the_same_frame_is_still_wrong() {
        // Two bodies in one place is a mistake, not a mirror.
        let mut a = f86();
        a.placements.push(Placement::new("1", "Former-1"));
        let probs = a.problems();
        assert_eq!(probs.len(), 1);
        assert!(matches!(probs[0], Problem::DuplicatePlacement { .. }));
        assert!(probs[0].message().contains("Former-1"));
    }

    #[test]
    fn a_part_on_two_frames_keeps_each_placements_own_settings() {
        let mut a = f86();
        a.frames.push(Frame::station("Rib-R", BasePlane::XZ, -21.0));
        a.frames.push(Frame::station("Rib-L", BasePlane::XZ, 21.0));
        a.placements.push(Placement::new("B", "Rib-R"));
        let mut left = Placement::new("B", "Rib-L");
        left.mirrored = true;
        left.rotate_deg = 3.0;
        a.placements.push(left);

        let ps = a.placements_of("B");
        assert_eq!(ps.len(), 2);
        assert!(!ps[0].mirrored && ps[0].rotate_deg == 0.0);
        assert!(ps[1].mirrored && ps[1].rotate_deg == 3.0);
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
        a.placements.push(Placement::new("1", "Former-1"));   // same frame
        assert_eq!(a.problems().len(), 2, "{:?}", a.problems());
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
            let fa = a.placements_of(part)[0];
            let fb = b.placements_of(part)[0];
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
        assert_eq!(back.placements_of("R1")[0].frame, "Wing");
        assert!(back.problems().is_empty());
    }
}

impl Airframe {
    /// How far a laminated placement sits from its frame, in mm.
    ///
    /// A lamination is glued to the FACE of another part, so it is
    /// offset by half that part's thickness plus half its own - the
    /// gap between two centres of touching sheets - and the chain
    /// repeats for a stack more than two deep.
    ///
    /// `thickness_mm` maps part name to stock thickness. A part
    /// missing from it contributes nothing, which keeps a stack that
    /// is only partly specified from collapsing onto one station
    /// silently: the shift is simply smaller and visibly so.
    pub fn laminate_shift(
        &self,
        part: &str,
        thickness_mm: &std::collections::HashMap<String, f64>,
    ) -> f64 {
        self.shift_inner(part, thickness_mm, 0)
    }

    fn shift_inner(
        &self,
        part: &str,
        thick: &std::collections::HashMap<String, f64>,
        depth: usize,
    ) -> f64 {
        if depth > self.placements.len() {
            return 0.0;                     // a cycle; problems() reports it
        }
        let p = match self.placements.iter().find(|p| p.part == part) {
            Some(p) => p,
            None => return 0.0,
        };
        let lam = match &p.laminate {
            Some(l) => l,
            None => return 0.0,
        };
        if lam.on == part {
            return 0.0;
        }
        let half_base = thick.get(&lam.on).copied().unwrap_or(0.0) / 2.0;
        let half_self = thick.get(part).copied().unwrap_or(0.0) / 2.0;
        lam.face.sign() * (half_base + half_self)
            + self.shift_inner(&lam.on, thick, depth + 1)
    }
}

#[cfg(test)]
mod laminate_tests {
    use super::*;
    use crate::frame::BasePlane;
    use std::collections::HashMap;

    fn thick() -> HashMap<String, f64> {
        // the F-86 nose: 1 and 2 are 1/8 in, 3 is 1/16 in
        [("1", 3.175), ("2", 3.175), ("3", 1.5875)]
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect()
    }

    fn nose() -> Airframe {
        let mut a = Airframe::new();
        a.frames.push(Frame::station("Former-1", BasePlane::YZ, 6.93));
        a.placements.push(Placement::new("1", "Former-1"));
        let mut two = Placement::new("2", "Former-1");
        two.laminate = Some(Laminate {
            on: "1".into(),
            face: Face::Positive,
        });
        a.placements.push(two);
        a
    }

    #[test]
    fn a_lamination_sits_one_thickness_from_what_it_is_glued_to() {
        // Two 1/8 in parts face to face: centres half+half = 3.175 mm
        // apart, which is one thickness.
        let a = nose();
        assert_eq!(a.laminate_shift("1", &thick()), 0.0);
        assert!((a.laminate_shift("2", &thick()) - 3.175).abs() < 1e-9);
    }

    #[test]
    fn the_face_decides_which_way_the_stack_grows() {
        let mut a = nose();
        a.placements[1].laminate.as_mut().unwrap().face = Face::Negative;
        assert!((a.laminate_shift("2", &thick()) + 3.175).abs() < 1e-9);
    }

    #[test]
    fn a_stack_three_deep_accumulates() {
        // 1 (1/8) + 2 (1/8) + 3 (1/16): 3's centre sits
        // 3.175 + (3.175/2 + 1.5875/2) = 5.556 mm from 1's.
        let mut a = nose();
        a.placements.push({
            let mut p = Placement::new("3", "Former-1");
            p.laminate = Some(Laminate { on: "2".into(), face: Face::Positive });
            p
        });
        let want = 3.175 + (3.175 / 2.0 + 1.5875 / 2.0);
        assert!((a.laminate_shift("3", &thick()) - want).abs() < 1e-9);
        assert!(a.problems().is_empty(), "{:?}", a.problems());
    }

    #[test]
    fn a_missing_thickness_shrinks_the_shift_rather_than_guessing() {
        // Half a stack specified must not collapse to one station
        // silently; it comes out visibly short instead.
        let a = nose();
        let partial: HashMap<String, f64> =
            [("1".to_string(), 3.175)].into_iter().collect();
        let s = a.laminate_shift("2", &partial);
        assert!((s - 3.175 / 2.0).abs() < 1e-9);
    }

    #[test]
    fn gluing_to_a_part_that_is_not_placed_is_reported() {
        let mut a = nose();
        a.placements[1].laminate.as_mut().unwrap().on = "99".into();
        assert!(a.problems().iter().any(
            |p| matches!(p, Problem::LaminateMissing { .. })));
    }

    #[test]
    fn a_lamination_loop_is_reported_not_spun_on() {
        let mut a = nose();
        a.placements[0].laminate = Some(Laminate {
            on: "2".into(),
            face: Face::Positive,
        });
        let probs = a.problems();
        assert!(probs.iter().any(
            |p| matches!(p, Problem::LaminateCycle { .. })), "{probs:?}");
        // and it terminates
        let _ = a.laminate_shift("1", &thick());
    }

    #[test]
    fn gluing_a_part_to_itself_is_a_loop() {
        let mut a = nose();
        a.placements[1].laminate.as_mut().unwrap().on = "2".into();
        assert!(!a.problems().is_empty());
        assert_eq!(a.laminate_shift("2", &thick()), 0.0);
    }

    #[test]
    fn an_ordinary_placement_serialises_without_a_laminate_field() {
        let text = serde_json::to_string(&Placement::new("5", "F-5")).unwrap();
        assert!(!text.contains("laminate"), "{text}");
    }

    #[test]
    fn a_laminate_round_trips_through_json() {
        let a = nose();
        let text = serde_json::to_string(&a).unwrap();
        let back: Airframe = serde_json::from_str(&text).unwrap();
        assert_eq!(back, a);
        assert!((back.laminate_shift("2", &thick()) - 3.175).abs() < 1e-9);
    }
}
