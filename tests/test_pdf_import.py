"""Tests for core.pdf_import (skipped when PyMuPDF is absent)."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import pdf_import  # noqa: E402

try:
    import pymupdf as fitz
except ImportError:
    fitz = pytest.importorskip("fitz")


def _make_pdf(pages_mm):
    """Build a tiny PDF with the given (w_mm, h_mm) pages."""
    doc = fitz.open()
    for w, h in pages_mm:
        page = doc.new_page(width=w * 72 / 25.4, height=h * 72 / 25.4)
        page.draw_rect(fitz.Rect(10, 10, 40, 40),
                       color=(0, 0, 0), fill=(0, 0, 0))
    fp = tempfile.mktemp(suffix=".pdf")
    doc.save(fp)
    doc.close()
    return fp


def test_availability_ok_when_fitz_present():
    assert pdf_import.availability_error() is None


def test_page_sizes_mm():
    fp = _make_pdf([(210.0, 297.0), (215.9, 279.4)])
    sizes = pdf_import.page_sizes_mm(fp)
    assert len(sizes) == 2
    assert sizes[0][0] == pytest.approx(210.0, abs=0.1)
    assert sizes[1][1] == pytest.approx(279.4, abs=0.1)


def test_render_page_dpi_sets_pixel_size():
    fp = _make_pdf([(210.0, 297.0)])
    bgr = pdf_import.render_page(fp, 0, dpi=100)
    # A4 at 100 DPI: 8.27 x 11.69 in -> ~827 x 1169 px, BGR
    assert bgr.shape[2] == 3
    assert abs(bgr.shape[1] - 827) <= 2
    assert abs(bgr.shape[0] - 1169) <= 2
    # the drawn rectangle makes the page non-blank
    assert bgr.min() < 128 < bgr.max()
