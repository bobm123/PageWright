"""Batch flatten with outline propagation (multi-page M3).

The insight (PLAN sec. 13): consecutive pages of one book photographed
in one session have SIMILAR outlines - same binding, similar opening
angle. So instead of detecting each page's outline from scratch, seed
it from the previous page's confirmed model and run only the cheap
text-line refinement. Faster, and output geometry stays consistent
across the book.

GUI-free: the UI iterates pages and calls these per image.
"""

from . import dewarp


def propagate_model(model, prev_wh, new_wh):
    """Seed a new page's outline from `model` (fitted on an image of
    size prev_wh) for an image of size new_wh.

    Uses the models' uniform .scaled(); page images from one scanning
    session differ in size only by camera resolution settings, so a
    uniform factor (mean of the axis ratios) is the right seed - the
    refinement pass absorbs the rest."""
    sw = float(new_wh[0]) / float(prev_wh[0])
    sh = float(new_wh[1]) / float(prev_wh[1])
    return model.scaled(0.5 * (sw + sh))


def seed_and_refine(gray, model, iters=2):
    """Refine a seeded outline against this page's printed text lines.

    Falls back to the unrefined seed on ANY failure - in a batch run a
    slightly-off outline on one page beats aborting the whole book."""
    try:
        if isinstance(model, dewarp.SpreadModel):
            return dewarp.refine_spread_with_text(gray, model, iters=iters)
        return dewarp.refine_with_text(gray, model, iters=iters)
    except Exception:
        return model


def flatten_with_model(bgr, model, out_w=None, out_h=None):
    """Render the flattened output(s) for one page.

    Returns a list: [left, right] for a spread model (both pages - a
    batch run has nobody to ask), else [page]."""
    if isinstance(model, dewarp.SpreadModel):
        left, right = dewarp.dewarp_spread(bgr, model,
                                           out_w=out_w, out_h=out_h)
        return [left, right]
    return [dewarp.dewarp_page(bgr, model, out_w=out_w, out_h=out_h)]
