"""OCR stage core (studio roadmap P4, basic version).

Thin GUI-free wrapper around pytesseract. The pytesseract package only
bridges to the Tesseract ENGINE, which must be installed separately on
the machine (Windows: the UB-Mannheim installer or `winget install
UB-Mannheim.TesseractOCR`); we report a helpful message when either
piece is missing instead of crashing.
"""


def availability_error():
    """None when OCR is ready, else a human-readable explanation."""
    try:
        import pytesseract
    except ImportError:
        return ("The pytesseract package is not installed.\n"
                "Install it with:  pip install pytesseract")
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return ("The Tesseract OCR engine was not found.\n"
                "Windows: install it with\n"
                "  winget install UB-Mannheim.TesseractOCR\n"
                "or from https://github.com/UB-Mannheim/tesseract/wiki,\n"
                "then restart PageWright (or add tesseract.exe to PATH).")
    return None


def image_to_text(bgr, region_px=None, lang="eng"):
    """OCR a BGR ndarray (optionally only region_px = (x0, y0, x1, y1))
    and return the recognized text. Raises RuntimeError with a readable
    message when OCR is unavailable."""
    err = availability_error()
    if err:
        raise RuntimeError(err)
    import cv2
    import pytesseract
    img = bgr
    if region_px is not None:
        x0, y0, x1, y1 = [int(round(v)) for v in region_px]
        h, w = img.shape[:2]
        x0, x1 = max(0, min(x0, x1)), min(w, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(h, max(y0, y1))
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise RuntimeError("the selected area is too small to OCR")
        img = img[y0:y1, x0:x1]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return pytesseract.image_to_string(rgb, lang=lang)
