//! `planecraft-core`: the Rust core as a subprocess.
//!
//! Python drives this over stdin/stdout rather than through a native
//! extension, so the toolchain stays `cargo build` and `python` with
//! no binding layer to keep in step. If the call rate ever makes that
//! cost real, pyo3 is the next step and the JSON shapes here are
//! already the interface.
//!
//! Usage:
//!   planecraft-core check  < airframe.json   # problems, one per line
//!   planecraft-core plan   < airframe.json   # resolved placements

use std::io::{self, Read, Write};

use planecraft_core::Airframe;

fn main() {
    let mut args = std::env::args().skip(1);
    let cmd = args.next().unwrap_or_default();

    let mut text = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut text) {
        fail(&format!("cannot read stdin: {e}"));
    }

    let air: Airframe = match serde_json::from_str(&text) {
        Ok(a) => a,
        Err(e) => fail(&format!("not a readable airframe: {e}")),
    };

    match cmd.as_str() {
        "check" => {
            let problems = air.problems();
            let out = serde_json::json!({
                "ok": problems.is_empty(),
                "problems": problems.iter().map(|p| p.message()).collect::<Vec<_>>(),
            });
            println!("{out}");
            if !problems.is_empty() {
                std::process::exit(1);
            }
        }
        "plan" => {
            // What to build, with each placement's frame resolved.
            // A placement whose frame is missing is absent here and
            // named by `check` - never silently defaulted.
            let items: Vec<_> = air
                .resolved()
                .into_iter()
                .map(|(p, f)| {
                    serde_json::json!({
                        "part": p.part,
                        "frame": f.name,
                        "buildable": f.is_buildable(),
                        "base": f.base().map(|b| b.as_str()),
                        "offset_mm": f.offset_mm(),
                        "rotate_deg": p.rotate_deg,
                        "in_plane_mm": p.offset_mm,
                        "mirrored": p.mirrored,
                    })
                })
                .collect();
            println!("{}", serde_json::json!({ "placements": items }));
        }
        other => fail(&format!(
            "unknown command {other:?} (expected check or plan)"
        )),
    }
}

fn fail(msg: &str) -> ! {
    let _ = writeln!(io::stderr(), "planecraft-core: {msg}");
    std::process::exit(2);
}
