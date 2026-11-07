"""Talk to the Rust core.

The geometry decisions live in Rust; this runs it and hands back
plain dicts. A subprocess rather than a native extension on purpose:
the toolchain stays `cargo build` plus `python`, with no binding
layer to keep in step. The JSON shapes here are already the
interface, so pyo3 is a drop-in later if the call rate ever makes the
process cost real - it is one call per airframe today, not per part.
"""

import json
import os
import shutil
import subprocess

BINARY = "planecraft-core"


class CoreError(Exception):
    """The core refused the input, and said why."""


def _candidates():
    """Where the built binary might be, nearest first."""
    env = os.environ.get("PLANECRAFT_CORE")
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.normpath(os.path.join(here, "..", "..", "core"))
    exe = ".exe" if os.name == "nt" else ""
    for profile in ("release", "debug"):
        yield os.path.join(root, "target", profile, BINARY + exe)
    # cargo honours CARGO_TARGET_DIR, which moves target/ elsewhere
    target = os.environ.get("CARGO_TARGET_DIR")
    if target:
        for profile in ("release", "debug"):
            yield os.path.join(target, profile, BINARY + exe)
    found = shutil.which(BINARY)
    if found:
        yield found


def core_path():
    """The core binary, or None when it has not been built."""
    for path in _candidates():
        if path and os.path.exists(path):
            return path
    return None


def _run(command, airframe):
    path = core_path()
    if path is None:
        raise CoreError(
            "planecraft-core is not built: run `cargo build --release` "
            "in planecraft/core, or set PLANECRAFT_CORE to the binary")
    proc = subprocess.run(
        [path, command],
        input=json.dumps(airframe),
        capture_output=True, text=True)
    # Exit 1 means "read it fine, found problems" and the report is on
    # stdout; only 2+ is a refusal.
    if proc.returncode >= 2:
        raise CoreError(proc.stderr.strip() or "the core failed")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise CoreError("the core returned nothing readable: %r"
                        % (proc.stdout[:200],))


def check(airframe):
    """Everything wrong with `airframe`, as {"ok": bool, "problems": []}."""
    return _run("check", airframe)


def plan(airframe):
    """The buildable placements, each with its frame resolved."""
    return _run("plan", airframe)["placements"]
