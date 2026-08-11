"""PDF import core (multi-page M2): rasterize PDF pages to images.

GUI-free wrapper over PyMuPDF (pip install pymupdf). A PDF page has a
known PHYSICAL size, so pages rendered at `dpi` are inherently
calibrated: mm_per_pixel = 25.4 / dpi.
"""


def _pymupdf():
    """The PyMuPDF module, preferring the modern `pymupdf` name.

    `import fitz` is the legacy alias and prints a deprecation warning
    on recent releases; fall back to it only for older installs."""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz
        return fitz


def availability_error():
    """None when PDF import is ready, else a readable explanation."""
    try:
        _pymupdf()
    except ImportError:
        return ("PDF import needs the PyMuPDF package.\n"
                "Install it with:  pip install pymupdf")
    return None


def page_sizes_mm(path):
    """[(width_mm, height_mm), ...] for every page in the PDF."""
    with _pymupdf().open(path) as doc:
        return [(p.rect.width * 25.4 / 72.0, p.rect.height * 25.4 / 72.0)
                for p in doc]


def render_page(path, index, dpi=300):
    """Rasterize page `index` (0-based) at `dpi`; returns a BGR ndarray."""
    import numpy as np
    mupdf = _pymupdf()
    with mupdf.open(path) as doc:
        page = doc[index]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=mupdf.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, pix.n)
        return arr[:, :, ::-1].copy()      # RGB -> BGR
