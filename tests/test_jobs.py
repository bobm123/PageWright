"""Headless jobs (core/jobs.py): no Qt, no display, no stdout.

These tests are the contract that makes the CLI possible. The most
important one is test_jobs_do_not_import_qt - if that ever fails, the
headless half of the app has quietly become GUI-only.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright.core import jobs        # noqa: E402

SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
    'viewBox="%s">%s</svg>')
DEFAULT_INNER = '<rect x="10" y="10" width="380" height="280"/>'


def _svg(tmp_path, name="art.svg", width="400mm", height="300mm",
         viewbox="0 0 400 300", inner=DEFAULT_INNER):
    fp = tmp_path / name
    fp.write_text(SVG_TEMPLATE % (width, height, viewbox, inner),
                  encoding="utf-8")
    return str(fp)


def _png(tmp_path, name="photo.png", w=600, h=400):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (w - 20, h - 20), (0, 0, 0), 3)
    fp = str(tmp_path / name)
    cv2.imwrite(fp, img)
    return fp


def _read(path):
    return io.open(path, encoding="utf-8").read()


# ----- the headless contract ----------------------------------------------

def test_jobs_do_not_import_qt(tmp_path):
    """A full job must run with no Qt loaded at all.

    Checked in a FRESH interpreter: another test in this process may
    already have imported PySide6 for the GUI smoke tests, so reading
    sys.modules here would pass or fail on test ORDER rather than on
    what the job itself pulls in.
    """
    import subprocess
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    script = "; ".join([
        "import sys",
        "sys.path.insert(0, %r)" % src,
        "from pagewright.core import jobs",
        "jobs.tile(%r, out_dir=%r)" % (_svg(tmp_path),
                                       str(tmp_path / "out")),
        "leaked = [m for m in sys.modules if 'PySide6' in m]",
        "print('LEAKED' if leaked else 'CLEAN')",
    ])
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "CLEAN" in proc.stdout, proc.stdout

def test_jobs_module_has_no_ui_imports():
    # Parse the real import statements rather than grepping the text -
    # the module docstring names pagewright.ui to explain the rule.
    import ast
    path = os.path.join(os.path.dirname(__file__), "..", "src",
                        "pagewright", "core", "jobs.py")
    tree = ast.parse(_read(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level 2 + "ui" would be pagewright.ui from core/
            imported.add("%s%s" % ("." * (node.level or 0),
                                   node.module or ""))
    banned = [m for m in imported
              if "PySide6" in m or m.endswith("ui") or ".ui." in m]
    assert banned == [], "jobs imports GUI modules: %s" % banned


# ----- classify / scale ----------------------------------------------------

def test_classify_knows_the_input_kinds():
    assert jobs.classify("a.svg") == "svg"
    assert jobs.classify("a.PDF") == "pdf"
    assert jobs.classify("a.png") == "image"
    assert jobs.classify("a.tiproj.json") == "project"
    # a bare .json is a project too, and must not read as an image
    assert jobs.classify("a.json") == "project"
    assert jobs.classify("a.txt") is None


def test_resolve_scale_accepts_one_spec_at_a_time():
    from pagewright.model import Project
    p = Project()
    mpp, why = jobs.resolve_scale(p, 1000, 500, dpi=254.0)
    assert mpp == pytest.approx(0.1)
    assert "254" in why
    mpp, _ = jobs.resolve_scale(p, 1000, 500, mm_per_px=0.25)
    assert mpp == pytest.approx(0.25)
    # width/height are the REAL size of the whole image
    mpp, _ = jobs.resolve_scale(p, 1000, 500, width="200mm")
    assert mpp == pytest.approx(0.2)
    mpp, _ = jobs.resolve_scale(p, 1000, 500, height="10cm")
    assert mpp == pytest.approx(0.2)
    # nothing given -> no opinion
    assert jobs.resolve_scale(p, 1000, 500) == (None, None)


def test_resolve_scale_rejects_conflicts_and_nonsense():
    from pagewright.model import Project
    p = Project()
    with pytest.raises(jobs.JobError):
        jobs.resolve_scale(p, 100, 100, dpi=300, width="10mm")
    with pytest.raises(jobs.JobError):
        jobs.resolve_scale(p, 100, 100, dpi=0)
    with pytest.raises(jobs.JobError):
        jobs.resolve_scale(p, 100, 100, mm_per_px=-1)
    with pytest.raises(jobs.JobError):
        jobs.resolve_scale(p, 100, 100, width="not a length")


# ----- load_source ---------------------------------------------------------

def test_svg_source_is_calibrated_from_the_document(tmp_path):
    src = jobs.load_source(_svg(tmp_path))
    assert src.kind == "svg"
    assert src.project.calibration.is_calibrated
    assert src.size_mm == pytest.approx((380.0, 280.0))
    assert src.image_bgr is None          # vector: no pixels
    assert "SVG" in src.calibrated_by


def test_image_source_has_no_scale_until_told(tmp_path):
    src = jobs.load_source(_png(tmp_path))
    assert src.kind == "image"
    assert not src.project.calibration.is_calibrated
    assert src.image_bgr is not None
    src2 = jobs.load_source(_png(tmp_path, "b.png"), dpi=254.0)
    assert src2.project.calibration.mm_per_pixel == pytest.approx(0.1)


def test_explicit_scale_overrides_the_svg_header(tmp_path):
    # A wrong header must be correctable from the command line.
    src = jobs.load_source(_svg(tmp_path), mm_per_px=1.0)
    assert src.project.calibration.mm_per_pixel == pytest.approx(1.0)


def test_missing_and_unsupported_files_raise_joberror(tmp_path):
    with pytest.raises(jobs.JobError):
        jobs.load_source(str(tmp_path / "nope.svg"))
    bad = tmp_path / "x.txt"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(jobs.JobError):
        jobs.load_source(str(bad))


def test_base_name_strips_the_double_extension(tmp_path):
    from pagewright.core import project_io
    from pagewright.model import Project
    p = Project()
    p.pixel_width, p.pixel_height = 100, 100
    p.calibration.mm_per_pixel = 1.0
    fp = str(tmp_path / "job.tiproj.json")
    project_io.save_project(p, fp)
    src = jobs.load_source(fp)
    assert src.base_name == "job"


# ----- info ----------------------------------------------------------------

def test_info_reports_size_contents_and_tile_plan(tmp_path):
    r = jobs.info(_svg(tmp_path))
    assert r.kind == "svg"
    assert r.is_calibrated
    assert r.size_mm == pytest.approx((380.0, 280.0))
    assert r.object_count == 1
    assert r.contour_count == 1
    plan = r.tile_plan
    assert plan["sheets"] == plan["cols"] * plan["rows"]
    assert plan["sheets"] > 1          # 380x280 mm exceeds one Letter sheet


def test_info_predicts_the_same_sheet_count_that_tile_writes(tmp_path):
    # If these ever disagree, info is lying about the job.
    path = _svg(tmp_path)
    for page, land in (("Letter", False), ("A4", False), ("A4", True)):
        predicted = jobs.info(path, page=page, landscape=land)
        actual = jobs.tile(path, page=page, landscape=land, dry_run=True)
        assert predicted.tile_plan["sheets"] == actual.sheets, (page, land)


def test_info_on_an_uncalibrated_image_says_so(tmp_path):
    r = jobs.info(_png(tmp_path))
    assert not r.is_calibrated
    assert r.tile_plan is None
    assert r.pixel_width == 600


def test_info_writes_nothing(tmp_path):
    path = _svg(tmp_path)
    before = sorted(os.listdir(str(tmp_path)))
    jobs.info(path)
    assert sorted(os.listdir(str(tmp_path))) == before


# ----- tile ----------------------------------------------------------------

def test_tile_writes_one_svg_per_sheet(tmp_path):
    out = str(tmp_path / "tiles")
    r = jobs.tile(_svg(tmp_path), out_dir=out)
    assert r.sheets >= 4
    on_disk = sorted(os.listdir(out))
    assert len(on_disk) == r.sheets
    assert sorted(r.names) == on_disk
    letter_w = 'width="215.9mm"'
    letter_h = 'height="279.4mm"'
    for name in on_disk:
        text = _read(os.path.join(out, name))
        assert letter_w in text
        assert letter_h in text


def test_tile_honours_page_and_orientation(tmp_path):
    out = str(tmp_path / "a4")
    jobs.tile(_svg(tmp_path), out_dir=out, page="A4", landscape=True)
    text = _read(os.path.join(out, os.listdir(out)[0]))
    assert 'width="297mm"' in text
    assert 'height="210mm"' in text


def test_tile_dry_run_writes_nothing(tmp_path):
    out = str(tmp_path / "nope")
    r = jobs.tile(_svg(tmp_path), out_dir=out, dry_run=True)
    assert r.sheets > 0
    assert not os.path.exists(out)


def test_tile_is_idempotent(tmp_path):
    out = str(tmp_path / "twice")
    first = jobs.tile(_svg(tmp_path), out_dir=out)
    second = jobs.tile(_svg(tmp_path), out_dir=out)
    assert sorted(os.listdir(out)) == sorted(first.names)
    assert first.names == second.names


def test_tile_overlap_and_margin_accept_units(tmp_path):
    path = _svg(tmp_path)
    a = jobs.tile(path, overlap="10mm", dry_run=True)
    b = jobs.tile(path, overlap="1cm", dry_run=True)
    assert a.sheets == b.sheets
    assert a.plan["overlap_mm"] == pytest.approx(b.plan["overlap_mm"])
    # a bigger overlap needs at least as many sheets
    c = jobs.tile(path, overlap="40mm", dry_run=True)
    assert c.sheets >= a.sheets


def test_tile_scale_percent_changes_the_output_size(tmp_path):
    path = _svg(tmp_path)
    full = jobs.tile(path, dry_run=True)
    half = jobs.tile(path, scale_percent=50, dry_run=True)
    assert half.size_mm[0] == pytest.approx(full.size_mm[0] / 2.0)
    assert half.sheets <= full.sheets


def test_tile_refuses_an_image_with_no_scale(tmp_path):
    with pytest.raises(jobs.JobError) as exc:
        jobs.tile(_png(tmp_path), out_dir=str(tmp_path / "o"))
    # the message must say how to fix it
    assert "--dpi" in str(exc.value)


def test_tile_embeds_the_photo_for_image_sources(tmp_path):
    out = str(tmp_path / "emb")
    jobs.tile(_png(tmp_path), out_dir=out, dpi=254.0)
    text = _read(os.path.join(out, os.listdir(out)[0]))
    assert "data:image/png;base64," in text


def test_tile_needs_something_to_draw(tmp_path):
    # An image with no traces and no embedded photo has no content.
    with pytest.raises(jobs.JobError):
        jobs.tile(_png(tmp_path), out_dir=str(tmp_path / "o2"),
                  dpi=254.0, embed_image=False)


def test_embed_image_needs_pixels(tmp_path):
    with pytest.raises(jobs.JobError) as exc:
        jobs.tile(_svg(tmp_path), out_dir=str(tmp_path / "o3"),
                  embed_image=True)
    assert "raster" in str(exc.value)


def test_tile_progress_callback_is_called(tmp_path):
    seen = []
    jobs.tile(_svg(tmp_path), out_dir=str(tmp_path / "p"),
              progress=seen.append)
    assert seen
    assert any("wrote" in m for m in seen)


def test_tile_base_name_controls_the_filenames(tmp_path):
    out = str(tmp_path / "named")
    r = jobs.tile(_svg(tmp_path), out_dir=out, base_name="pattern")
    assert all(n.startswith("pattern-r") for n in r.names)
