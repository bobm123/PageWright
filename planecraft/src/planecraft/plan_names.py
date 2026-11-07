"""Plan labels in, typeable names out.

This plan labels its formers with circled numbers (1 through 10) and
its wing ribs with circled letters (A through E). That is useful when
matching a part back to the drawing, so it is worth keeping - as a
LABEL, for display.

It must never be the key. Nobody should have to type a circled A to
name a part, and on Windows the console codepage cannot even print
one: `print` of U+24B6 raises UnicodeEncodeError under cp1252, which
would break the CLI, the status bar and every log line that mentioned
the part.

So a name is normalised on the way in. NFKC already knows these
characters - it maps the circled forms to their plain ones, and
circled ten to the two characters "10" - so there is no table here to
fall out of date.
"""

import unicodedata


def normalise(label):
    """A plan label as a typeable name.

    Circled and other decorated forms become their plain equivalents,
    and surrounding space is dropped. Everything else is left exactly
    as written: a name is the user's, not ours to tidy.
    """
    if label is None:
        return None
    return unicodedata.normalize("NFKC", str(label)).strip()


def is_decorated(label):
    """True when `label` would change under `normalise`.

    Worth knowing because it is the case where the plan's own label
    and the part's name differ, and a user matching parts to a drawing
    may want to see both.
    """
    if label is None:
        return False
    text = str(label).strip()
    return normalise(text) != text


def label_and_name(label):
    """(label as drawn, name to key on).

    The label is kept so a part can be shown the way the plan draws
    it; the name is what everything else uses. They are equal for an
    ordinary name, which is the common case.
    """
    text = "" if label is None else str(label).strip()
    return text, normalise(text)
