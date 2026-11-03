"""CLI front end (cli.py): argv in, text out, exit codes.

cli.run takes injectable streams and returns an exit code instead of
calling sys.exit, so these tests drive the real command path without a
subprocess and without touching stdout.
"""

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pagewright import cli        # noqa: E402

SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" '
    'viewBox="%s">%s</svg>')


def _svg(tmp_path, name="art.svg", width="400mm", height="300mm",
         viewbox="0 0 400 300",
         inner='<rect x="10" y="10" width="380" height="280"/>'):
    fp = tmp_path / name
    fp.write_text(SVG_TEMPLATE % (width, height, viewbox, inner),
                  encoding="utf-8")
    return str(fp)


def _png(tmp_path, name="photo.png", w=600, h=400):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((h, w, 3), 255, np.uint8)
    fp = str(tmp_path / name)
    cv2.imwrite(fp, img)
    return fp


def _run(argv):
    """Run a command, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


# ----- info ---------------------------------------------------------------

def test_info_prints_a_readable_report(tmp_path):
    code, out, _err = _run(["info", _svg(tmp_path)])
    assert code == cli.EXIT_OK
    assert "svg" in out
    assert "380.0 mm x 280.0 mm" in out
    assert "tile plan" in out
    assert "sheet(s)" in out


def test_info_json_is_machine_readable(tmp_path):
    code, out, _err = _run(["info", _svg(tmp_path), "--json"])
    assert code == cli.EXIT_OK
    data = json.loads(out)
    assert data["kind"] == "svg"
    assert data["calibrated"] is True
    assert data["size_mm"] == pytest.approx([380.0, 280.0])
    assert data["tile_plan"]["sheets"] >= 4


def test_info_reports_an_uncalibrated_image(tmp_path):
    code, out, _err = _run(["info", _png(tmp_path)])
    assert code == cli.EXIT_OK
    assert "NOT CALIBRATED" in out
    assert "--dpi" in out               # tells the user the way out


def test_info_accepts_a_scale_flag(tmp_path):
    code, out, _err = _run(["info", _png(tmp_path), "--dpi", "254"])
    assert code == cli.EXIT_OK
    assert "NOT CALIBRATED" not in out
    assert "60.0 mm x 40.0 mm" in out   # 600x400 px at 0.1 mm/px


def test_info_page_flag_changes_the_plan(tmp_path):
    path = _svg(tmp_path)
    _c1, letter, _e1 = _run(["info", path, "--json"])
    _c2, a4, _e2 = _run(["info", path, "--page", "A4", "--json"])
    assert json.loads(letter)["tile_plan"]["page"] == "Letter"
    assert json.loads(a4)["tile_plan"]["page"] == "A4"


# ----- tile ---------------------------------------------------------------

def test_tile_writes_the_sheets_and_reports(tmp_path):
    out_dir = str(tmp_path / "tiles")
    code, out, err = _run(["tile", _svg(tmp_path), "-o", out_dir])
    assert code == cli.EXIT_OK
    assert "written to" in out
    assert "sheet(s)" in out
    assert "wrote" in err               # progress goes to stderr
    assert len(os.listdir(out_dir)) >= 4


def test_tile_quiet_says_nothing_on_success(tmp_path):
    out_dir = str(tmp_path / "q")
    code, out, err = _run(["tile", _svg(tmp_path), "-o", out_dir, "-q"])
    assert code == cli.EXIT_OK
    assert out == ""
    assert err == ""
    assert os.listdir(out_dir)


def test_tile_dry_run_writes_nothing(tmp_path):
    out_dir = str(tmp_path / "dry")
    code, out, _err = _run(["tile", _svg(tmp_path), "-o", out_dir, "-n"])
    assert code == cli.EXIT_OK
    assert "dry run" in out
    assert not os.path.exists(out_dir)


def test_tile_missing_scale_fails_with_guidance(tmp_path):
    code, out, err = _run(["tile", _png(tmp_path),
                           "-o", str(tmp_path / "x")])
    assert code == cli.EXIT_ERROR
    assert "--dpi" in err
    assert out == ""                    # errors do not pollute stdout


def test_tile_missing_file_fails_cleanly(tmp_path):
    code, _out, err = _run(["tile", str(tmp_path / "nope.svg")])
    assert code == cli.EXIT_ERROR
    assert "not found" in err


def test_tile_units_on_overlap(tmp_path):
    path = _svg(tmp_path)
    c1, o1, _e1 = _run(["tile", path, "-n", "--overlap", "10mm"])
    c2, o2, _e2 = _run(["tile", path, "-n", "--overlap", "1cm"])
    assert (c1, c2) == (cli.EXIT_OK, cli.EXIT_OK)
    assert o1.splitlines()[0] == o2.splitlines()[0]


def test_tile_rejects_a_bad_length(tmp_path):
    code, _out, err = _run(["tile", _svg(tmp_path), "-n",
                            "--overlap", "soon"])
    assert code == cli.EXIT_ERROR
    assert "--overlap" in err


# ----- parser / dispatch --------------------------------------------------

def test_no_subcommand_prints_help():
    code, out, _err = _run([])
    assert code == cli.EXIT_OK
    assert "COMMAND" in out


def test_commands_tuple_matches_the_parser():
    # main.py dispatches on COMMANDS, so a command missing from it would
    # be swallowed by the GUI parser instead of running.
    parser = cli.build_parser()
    actions = [a for a in parser._actions
               if getattr(a, "choices", None) and a.dest == "command"]
    assert actions, "no subparsers found"
    assert set(actions[0].choices) == set(cli.COMMANDS)


def test_conflicting_scale_flags_are_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(["info", _svg(tmp_path), "--dpi", "300", "--width", "10mm"])
    assert exc.value.code == 2


def test_cli_never_imports_qt(tmp_path):
    """The CLI must work on a machine with no display.

    Run in a FRESH interpreter - the GUI smoke tests import PySide6
    into this same process, so sys.modules here reflects test order,
    not what the CLI needs.
    """
    import subprocess
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    script = "; ".join([
        "import sys",
        "sys.path.insert(0, %r)" % src,
        "from pagewright import cli",
        "cli.run(['tile', %r, '-o', %r, '-q'])"
        % (_svg(tmp_path), str(tmp_path / "noqt")),
        "leaked = [m for m in sys.modules if 'PySide6' in m]",
        "print('LEAKED' if leaked else 'CLEAN')",
    ])
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "CLEAN" in proc.stdout, proc.stdout

# ----- main.py dispatch ---------------------------------------------------

def test_wants_cli_recognises_subcommands():
    from pagewright.main import _wants_cli
    assert _wants_cli(["info", "x.svg"])
    assert _wants_cli(["tile", "x.svg"])
    assert _wants_cli(["--debug", "info", "x.svg"])
    # a plain file argument is the GUI, not a command
    assert not _wants_cli(["photo.png"])
    assert not _wants_cli([])
    # --help belongs to the GUI parser, which documents both halves
    assert not _wants_cli(["--help"])
    assert not _wants_cli(["-h"])


def test_main_runs_a_command_without_starting_qt(tmp_path, monkeypatch,
                                                 capsys):
    # The real entry point, exercised as a user would call it.
    from pagewright import main as main_mod
    svg = _svg(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pagewright", "info", svg])
    code = main_mod.main()
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert "380.0 mm" in captured.out
    # (that this needs no Qt is asserted in a clean interpreter by
    #  test_cli_never_imports_qt)


def test_top_level_help_lists_the_subcommands():
    from pagewright.main import _parse_args
    # _parse_args builds the GUI parser; its epilog must name the
    # headless commands or they are undiscoverable.
    import contextlib
    buf = io.StringIO()
    with contextlib.suppress(SystemExit):
        with contextlib.redirect_stdout(buf):
            _parse_args(["--help"])
    text = buf.getvalue()
    for command in cli.COMMANDS:
        assert command in text


# ----- export-svg / import-svg --------------------------------------------

def test_export_svg_command(tmp_path):
    out = str(tmp_path / "out.svg")
    code, stdout, _err = _run(["export-svg", _svg(tmp_path), "-o", out])
    assert code == cli.EXIT_OK
    assert os.path.isfile(out)
    assert "object(s)" in stdout
    assert "380.0 mm" in stdout


def test_import_svg_command_writes_a_loadable_project(tmp_path):
    out = str(tmp_path / "job.tiproj.json")
    code, stdout, _err = _run(["import-svg", _svg(tmp_path), "-o", out])
    assert code == cli.EXIT_OK
    assert os.path.isfile(out)
    assert "written to" in stdout
    # the project round-trips through the other commands
    code2, info_out, _e2 = _run(["info", out, "--json"])
    assert code2 == cli.EXIT_OK
    assert json.loads(info_out)["calibrated"] is True


def test_the_whole_chain_runs_from_the_command_line(tmp_path):
    # import -> tile, which is the point of having these commands.
    proj = str(tmp_path / "chain.tiproj.json")
    assert _run(["import-svg", _svg(tmp_path), "-o", proj, "-q"])[0] == 0
    tiles = str(tmp_path / "tiles")
    assert _run(["tile", proj, "-o", tiles, "-q"])[0] == 0
    assert len(os.listdir(tiles)) >= 4


def test_export_svg_without_a_scale_fails_with_guidance(tmp_path):
    code, _out, err = _run(["export-svg", _png(tmp_path),
                            "-o", str(tmp_path / "x.svg")])
    assert code == cli.EXIT_ERROR
    assert "--dpi" in err


# ----- flatten ------------------------------------------------------------

def _page_photo(tmp_path, name="page.png"):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((700, 900, 3), 30, np.uint8)
    cv2.rectangle(img, (120, 90), (780, 610), (245, 245, 240), -1)
    for k in range(8):
        y = 150 + k * 55
        cv2.line(img, (170, y), (730, y), (40, 40, 40), 4)
    fp = str(tmp_path / name)
    cv2.imwrite(fp, img)
    return fp


def test_flatten_command(tmp_path):
    out = str(tmp_path / "flat")
    code, stdout, _err = _run(["flatten", _page_photo(tmp_path), "-o", out])
    assert code == cli.EXIT_OK
    assert "1 page(s) flattened" in stdout
    assert len(os.listdir(out)) == 1


def test_flatten_dry_run_writes_nothing(tmp_path):
    out = str(tmp_path / "dry")
    code, stdout, _err = _run(["flatten", _page_photo(tmp_path),
                               "-o", out, "-n"])
    assert code == cli.EXIT_OK
    assert "dry run" in stdout
    assert not os.path.exists(out)


def test_flatten_reports_skipped_pages_and_exits_non_zero(tmp_path):
    # A script needs to know some pages did not make it.
    out = str(tmp_path / "part")
    code, stdout, err = _run(["flatten", str(tmp_path / "missing.png"),
                              _page_photo(tmp_path), "-o", out])
    assert code == cli.EXIT_ERROR
    assert "skipped" in err
    assert "1 page(s) flattened" in stdout
    assert len(os.listdir(out)) == 1       # the good one still landed


# ----- ocr ----------------------------------------------------------------

def test_ocr_without_an_engine_fails_cleanly(tmp_path):
    from pagewright.core import ocr as ocr_mod
    if ocr_mod.availability_error() is None:
        pytest.skip("Tesseract is installed here")
    code, out, err = _run(["ocr", _png(tmp_path)])
    assert code == cli.EXIT_ERROR
    assert out == ""                       # errors stay off stdout
    assert "pytesseract" in err or "Tesseract" in err


# ----- the registry stays in step -----------------------------------------

def test_every_command_has_a_handler():
    # COMMANDS drives main.py's dispatch, so a command listed there with
    # no handler would be swallowed by the GUI parser instead of running.
    assert set(cli.COMMANDS) == set(cli._HANDLERS)


# ----- find-parts ---------------------------------------------------------

def _plan(tmp_path, name="plan.png"):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    img = np.full((1500, 2000, 3), 255, np.uint8)
    for (x, y, w, h) in ((200, 200, 700, 500), (1100, 200, 400, 300),
                         (200, 800, 500, 400)):
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), 3)
    fp = str(tmp_path / name)
    cv2.imwrite(fp, img)
    return fp


def test_find_parts_command(tmp_path):
    code, stdout, _err = _run(["find-parts", _plan(tmp_path),
                               "--mm-per-px", "0.1"])
    assert code == cli.EXIT_OK
    assert "3 part(s)" in stdout
    assert "mm2" in stdout


def test_find_parts_json_gives_an_agent_what_it_needs(tmp_path):
    code, stdout, _err = _run(["find-parts", _plan(tmp_path),
                               "--mm-per-px", "0.1", "--trace", "--json"])
    assert code == cli.EXIT_OK
    data = json.loads(stdout)
    assert data["count"] == 3
    part = data["parts"][0]
    # enough to act on without a second call
    for key in ("bbox", "centroid", "area_mm2", "size_mm", "outline"):
        assert key in part
    assert len(part["bbox"]) == 4
    assert part["outline"] and len(part["outline"]) >= 4


def test_find_parts_saves_a_project_that_tiles(tmp_path):
    proj = str(tmp_path / "parts.tiproj.json")
    assert _run(["find-parts", _plan(tmp_path), "--mm-per-px", "0.1",
                 "-o", proj, "-q"])[0] == cli.EXIT_OK
    assert os.path.isfile(proj)
    tiles = str(tmp_path / "tiles")
    assert _run(["tile", proj, "-o", tiles, "-q"])[0] == cli.EXIT_OK
    assert os.listdir(tiles)


def test_find_parts_without_a_scale_fails_with_guidance(tmp_path):
    code, _out, err = _run(["find-parts", _plan(tmp_path)])
    assert code == cli.EXIT_ERROR
    assert "--dpi" in err
