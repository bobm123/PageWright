"""Turn a list of stations into frames.

On the F-86 plan the formers are circled numbers along a side view,
each at its own station. Typing ten frames by hand is the kind of
work that invites a transcription error nobody notices until the
fuselage will not close, so the stations are given once - as a list,
or as a spacing - and the frames are generated.

Stations are measured along the frame's own normal, which for a
fuselage former (YZ) is the X axis: down the length of the aeroplane.
"""

from .core import CoreError  # noqa: F401  (re-exported for callers)

BASE_PLANES = ("XY", "YZ", "XZ")


def _check_base(base):
    up = (base or "").strip().upper()
    if up not in BASE_PLANES:
        raise ValueError("unknown base plane %r (expected %s)"
                         % (base, ", ".join(BASE_PLANES)))
    return up


def frames_at(names, stations_mm, base="YZ", prefix=""):
    """One frame per name, at the matching station.

    `names` and `stations_mm` must be the same length: a frame with a
    guessed station is worse than no frame, because it builds a
    fuselage that looks right and is not.
    """
    base = _check_base(base)
    names = list(names)
    stations = list(stations_mm)
    if len(names) != len(stations):
        raise ValueError(
            "%d part(s) but %d station(s): give one station per part"
            % (len(names), len(stations)))
    return [{"name": "%s%s" % (prefix, n),
             "def": {"kind": "offset", "base": base,
                     "offset_mm": float(s)}}
            for n, s in zip(names, stations)]


def frames_every(names, spacing_mm, base="YZ", start_mm=0.0, prefix=""):
    """Frames at a constant spacing - evenly spaced formers.

    A convenience for the common case, NOT a default: a plan whose
    formers are not evenly spaced must use `frames_at`, and reading
    the stations off the drawing is the only way to know which it is.
    """
    names = list(names)
    stations = [start_mm + i * spacing_mm for i in range(len(names))]
    return frames_at(names, stations, base=base, prefix=prefix)


def place(parts_to_frames, rotate_deg=None, mirrored=()):
    """Placements from {part name: frame name}."""
    rotate = rotate_deg or {}
    mirror = set(mirrored)
    out = []
    for part, frame in parts_to_frames.items():
        p = {"part": part, "frame": frame}
        if rotate.get(part):
            p["rotate_deg"] = float(rotate[part])
        if part in mirror:
            p["mirrored"] = True
        out.append(p)
    return out
