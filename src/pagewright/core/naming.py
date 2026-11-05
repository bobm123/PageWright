"""What a plan sheet's part numbers MEAN.

Plans do not agree. One sheet numbers its formers F-1..F-9; the next
numbers them 1..9 and spends F1..F6 on longerons, with the higher F
numbers going to cockpit details. Guessing a build plane from a name is
free information when the convention is known and a silently misplaced
part when it is not - a longeron read as a former gets stood on end.

So the convention belongs to the project. `DEFAULT_PREFIX_PLANES` is
the usual one; a project overrides it with its own {prefix: plane}
dict, where a None value means "this prefix implies nothing".

Name ORDERING is a different question and already answered by
project_io.natural_key.
"""

import re

# What a name's prefix says about which way the part stands, for the
# common case where the plan uses the part-type letter. Keys are the
# prefix as typed (lowercased, no separator); "" is the bare-number
# case, e.g. a sheet whose formers are just "1".."9".
#
# The DEFAULT scheme is the convention most plans use: F-3 is the third
# fuselage former, R-2 the second wing rib.
DEFAULT_PREFIX_PLANES = {
    "f": "YZ", "former": "YZ", "frame": "YZ", "bulkhead": "YZ",
    "fus": "YZ",
    "r": "XZ", "rib": "XZ", "w": "XZ", "wing": "XZ",
}


def split_prefix(name):
    """Split a part name into (prefix, number) or None.

    "F-3" -> ("f", 3); "10" -> ("", 10); "Rudder" -> None.
    Wants a number at the end, optionally preceded by letters and one
    separator, because that is what a part NUMBER looks like. A name
    with no number is a named part ("Rudder", "Fin") and says nothing
    about its plane.
    """
    text = (name or "").strip().lower()
    m = re.match(r"^([a-z]*)[\s\-_.]*(\d+)$", text)
    if not m:
        return None
    return m.group(1), int(m.group(2))


class NamingScheme:
    """How one plan's part numbers map to build planes.

    `prefix_planes` maps a lowercased prefix to a plane name; the empty
    string is the bare-number case. A prefix that is present but maps
    to None means "this prefix says nothing" - which is how a sheet
    declares that its F numbers are longerons, running lengthwise, and
    must NOT be stood up as formers.
    """

    def __init__(self, prefix_planes=None):
        self.prefix_planes = (dict(DEFAULT_PREFIX_PLANES)
                              if prefix_planes is None
                              else dict(prefix_planes))

    def plane_for(self, name):
        """The plane `name` implies, or None if it implies nothing."""
        split = split_prefix(name)
        if split is None:
            return None
        prefix, _number = split
        return self.prefix_planes.get(prefix) or None

    def copy(self):
        return NamingScheme(self.prefix_planes)

    def is_default(self):
        return self.prefix_planes == DEFAULT_PREFIX_PLANES

    def __repr__(self):
        return "NamingScheme(%r)" % (self.prefix_planes,)


DEFAULT_SCHEME = NamingScheme()


def scheme_for(project):
    """The NamingScheme for `project`.

    The project stores a plain {prefix: plane} dict (or None for "use
    the usual convention") so the model stays dependency-free. This is
    the one place that turns it into behaviour.
    """
    return NamingScheme(getattr(project, "name_planes", None))
