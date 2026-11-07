//! Where a part sits: a named frame that resolves to a plane.
//!
//! The thing that must stay open-ended. A part may sit on a base
//! plane, on a base plane offset to a station, or on a plane defined
//! by geometry nobody has designed yet - an angled firewall, a plane
//! through three points, something a CAD tool offers that this crate
//! has never heard of.
//!
//! So a `Frame` is a NAME plus, optionally, enough definition to build
//! it. A frame with no definition is a reference: the CAD side already
//! has a plane by that name and PlaneCraft just points at it. That is
//! what keeps an unanticipated construction from being a blocker -
//! naming it is always possible, even when defining it here is not.

use serde::{Deserialize, Serialize};

/// The three base planes every CAD tool agrees on.
///
/// Spelled out rather than taken as strings so a typo cannot reach the
/// exporter. Anything beyond these is a named `Frame`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum BasePlane {
    /// Plan view: the sheet as drawn.
    XY,
    /// Across the fuselage - a former stands here.
    YZ,
    /// Across the wing - a rib stands here.
    XZ,
}

impl BasePlane {
    pub fn as_str(self) -> &'static str {
        match self {
            BasePlane::XY => "XY",
            BasePlane::YZ => "YZ",
            BasePlane::XZ => "XZ",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_ascii_uppercase().as_str() {
            "XY" => Some(BasePlane::XY),
            "YZ" => Some(BasePlane::YZ),
            "XZ" => Some(BasePlane::XZ),
            _ => None,
        }
    }

    /// (u, v, normal) in world space: the two in-plane axes a 2D
    /// sketch coordinate maps onto, and the direction a station
    /// offset runs along.
    pub fn axes(self) -> ([f64; 3], [f64; 3], [f64; 3]) {
        match self {
            BasePlane::XY => ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]),
            BasePlane::YZ => ([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]),
            BasePlane::XZ => ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]),
        }
    }
}

/// How a frame is built, when PlaneCraft knows how.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum FrameDef {
    /// A base plane, optionally offset along its normal. This covers
    /// every former station on a typical plan: "YZ at 76.2 mm".
    Offset {
        base: BasePlane,
        #[serde(default)]
        offset_mm: f64,
    },
    /// A plane through a point with a given normal, and an `up`
    /// direction fixing the rotation of a sketch drawn on it.
    /// An angled firewall or a swept rib lives here.
    Oriented {
        origin_mm: [f64; 3],
        normal: [f64; 3],
        up: [f64; 3],
    },
}

/// A named place to put a part.
///
/// `def` is optional ON PURPOSE. Without it this is a reference to a
/// plane the CAD side owns, which is how PlaneCraft stays usable for
/// constructions it cannot express.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Frame {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub def: Option<FrameDef>,
    /// Free text from the plan: "former 3 station, sheet 1".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

impl Frame {
    /// A frame that only names a plane the CAD side already has.
    pub fn reference(name: impl Into<String>) -> Self {
        Frame { name: name.into(), def: None, note: None }
    }

    /// A base plane offset to a station.
    pub fn station(name: impl Into<String>, base: BasePlane, offset_mm: f64) -> Self {
        Frame {
            name: name.into(),
            def: Some(FrameDef::Offset { base, offset_mm }),
            note: None,
        }
    }

    /// True when this frame tells CAD how to build itself. A reference
    /// frame does not, and the exporter must emit a plain name for it
    /// rather than inventing geometry.
    pub fn is_buildable(&self) -> bool {
        self.def.is_some()
    }

    /// The base plane this frame reduces to, if any. An oriented frame
    /// has none - that is the point of it.
    pub fn base(&self) -> Option<BasePlane> {
        match self.def {
            Some(FrameDef::Offset { base, .. }) => Some(base),
            _ => None,
        }
    }

    /// The offset along the base normal, for a frame that has one.
    pub fn offset_mm(&self) -> Option<f64> {
        match self.def {
            Some(FrameDef::Offset { offset_mm, .. }) => Some(offset_mm),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_frame_with_no_definition_is_a_reference() {
        // The case that keeps an unanticipated construction from
        // blocking: name it, let CAD own it.
        let f = Frame::reference("FirewallAngled");
        assert!(!f.is_buildable());
        assert_eq!(f.base(), None);
    }

    #[test]
    fn a_station_frame_knows_its_base_and_offset() {
        let f = Frame::station("Former-3", BasePlane::YZ, 76.2);
        assert!(f.is_buildable());
        assert_eq!(f.base(), Some(BasePlane::YZ));
        assert_eq!(f.offset_mm(), Some(76.2));
    }

    #[test]
    fn an_oriented_frame_has_no_base_plane() {
        // An angled plane does not reduce to XY/YZ/XZ; asking for a
        // base must answer None rather than guessing the nearest.
        let f = Frame {
            name: "Firewall".into(),
            def: Some(FrameDef::Oriented {
                origin_mm: [0.0, 0.0, 0.0],
                normal: [0.0, 0.7071, 0.7071],
                up: [0.0, 0.0, 1.0],
            }),
            note: None,
        };
        assert!(f.is_buildable());
        assert_eq!(f.base(), None);
        assert_eq!(f.offset_mm(), None);
    }

    #[test]
    fn base_planes_round_trip_through_text() {
        for p in [BasePlane::XY, BasePlane::YZ, BasePlane::XZ] {
            assert_eq!(BasePlane::parse(p.as_str()), Some(p));
        }
        assert_eq!(BasePlane::parse("yz"), Some(BasePlane::YZ));
        assert_eq!(BasePlane::parse("  XZ "), Some(BasePlane::XZ));
        assert_eq!(BasePlane::parse("ZZ"), None);
    }

    #[test]
    fn the_station_axis_is_the_plane_normal() {
        // A former at station 76.2 must move ALONG the fuselage, not
        // across it. YZ's normal is X, so that is where the offset goes.
        let (_u, _v, n) = BasePlane::YZ.axes();
        assert_eq!(n, [1.0, 0.0, 0.0]);
        let (_u, _v, n) = BasePlane::XZ.axes();
        assert_eq!(n, [0.0, -1.0, 0.0]);
    }

    #[test]
    fn a_reference_frame_serialises_without_a_null_def() {
        // The file is read by humans matching parts against a plan;
        // a null def would read as "defined as nothing".
        let json = serde_json::to_string(&Frame::reference("Rib-Sta-3")).unwrap();
        assert!(!json.contains("def"), "{json}");
        assert!(json.contains("Rib-Sta-3"));
    }

    #[test]
    fn a_station_frame_round_trips_through_json() {
        let f = Frame::station("Former-7", BasePlane::YZ, -12.5);
        let text = serde_json::to_string(&f).unwrap();
        assert_eq!(serde_json::from_str::<Frame>(&text).unwrap(), f);
    }
}
