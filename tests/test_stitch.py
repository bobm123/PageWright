"""Page stitching: core/stitch.py."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from pagewright.core import stitch as st  # noqa: E402

POGO = r"D:\Projects\Airplanes\OwlRacer"
POGO_P16 = os.path.join(POGO, "Scan20261118_0102.png")
POGO_P17 = os.path.join(POGO, "Scan20261118_0103.png")
POGO_SPREAD = (r"D:\Projects\ClaudeSessions\ModelCraft\sample\Pogo"
               r"\scans\p16-17-spread.png")


def _page(w, h, seed):
    """A white page with a few dark marks, all different, so any
    misplacement or mirror shows."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 255, np.uint8)
    for _ in range(12):
        x, y = rng.integers(0, w - 3), rng.integers(0, h - 3)
        img[y:y + 3, x:x + 3] = rng.integers(0, 150)
    return img


def test_an_integer_shift_copies_both_pages_exactly():
    a, b = _page(40, 30, 1), _page(25, 20, 2)
    r = st.stitch(a, b, st.Placement(dx=30, dy=5))
    assert r.size == (55, 30)
    out = r.image
    # A where B does not reach, B where A does not reach
    assert np.array_equal(out[0:30, 0:30], a[:, 0:30])
    assert np.array_equal(out[5:25, 40:55], b[:, 10:25])
    # the overlap keeps the darker of the two
    assert np.array_equal(out[5:25, 30:40],
                          np.minimum(a[5:25, 30:40], b[:, 0:10]))


def test_a_negative_shift_grows_the_canvas_up_and_left():
    a, b = _page(20, 20, 3), _page(20, 20, 4)
    r = st.stitch(a, b, st.Placement(dx=-15, dy=-4))
    assert r.size == (35, 24)
    assert r.a_origin == (15.0, 4.0)
    assert np.array_equal(r.image[0:20, 0:15], b[:, 0:15])


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_turn_matrix_agrees_with_the_turned_image(turn):
    img = _page(7, 4, 5)
    img[0, 0] = 1                    # a corner, so a mirror would show
    turned = st.turn_image(img, turn)
    m = st._turn_matrix((7, 4), turn)
    for y in range(4):
        for x in range(7):
            tx, ty = st._apply(m, (x, y))
            assert turned[int(round(ty)), int(round(tx))] == img[y, x]


def test_a_positive_angle_is_clockwise_on_screen():
    # a point right of B's centre moves DOWN (y grows, y is down)
    p = st.Placement(angle_deg=10)
    cx = (21 - 1) / 2.0
    x, y = st.map_b(p, (21, 21), (cx + 8, cx))
    assert y > cx + 1


def test_a_quarter_angle_on_a_square_page_is_a_quarter_turn():
    b = _page(15, 15, 6)
    by_angle = st.stitch(np.full((15, 15), 255, np.uint8), b,
                         st.Placement(angle_deg=90))
    by_turn = st.stitch(np.full((15, 15), 255, np.uint8), b,
                        st.Placement(turn_b=90))
    assert np.abs(by_angle.image.astype(int)
                  - by_turn.image.astype(int)).max() <= 1


def test_turning_page_a_moves_the_frame_the_offset_is_read_in():
    a, b = _page(30, 10, 7), _page(10, 10, 8)
    r = st.stitch(a, b, st.Placement(dx=10, turn_a=90))
    assert r.size == (20, 30)          # A is 10 wide once turned
    assert np.array_equal(r.image[0:30, 0:10], st.turn_image(a, 90))


def test_measure_across_reads_through_the_placement():
    p = st.Placement(dx=100, dy=7)
    d = st.measure_across(p, (120, 50), (80, 50), (90, 20), (30, 20))
    # (90, 20) on A to (130, 27) in A's frame
    assert d == pytest.approx(np.hypot(40, 7))


def test_the_output_matrices_send_page_pixels_to_their_pixels():
    a, b = _page(40, 30, 9), _page(25, 20, 10)
    p = st.Placement(dx=-12, dy=9, turn_b=270, angle_deg=0)
    r = st.stitch(a, b, p)
    bt = st.turn_image(b, 270)
    for (x, y) in ((0, 0), (24, 0), (3, 19)):
        ox, oy = st._apply(r.b_to_out, (x, y))
        assert r.image[int(round(oy)), int(round(ox))] <= b[y, x]
    assert bt.shape == (25, 20)


def test_trim_crops_to_the_ink_with_a_margin_and_clamps():
    img = np.full((50, 60), 255, np.uint8)
    img[10:12, 20:30] = 0
    assert st.ink_box(img, 5) == (15, 5, 34, 16)
    assert st.ink_box(img, 100) == (0, 0, 59, 49)
    assert st.ink_box(np.full((5, 5), 255, np.uint8), 3) is None


def test_trim_moves_the_output_matrices_with_the_crop():
    a = np.full((40, 40), 255, np.uint8)
    a[20, 20] = 0
    r = st.stitch(a, np.full((10, 10), 255, np.uint8),
                  st.Placement(dx=40), trim=2)
    assert r.size == (5, 5)
    assert r.a_origin == (-18.0, -18.0)
    assert r.image[2, 2] == 0


def test_a_grey_page_joins_a_colour_page():
    a = np.full((10, 10, 3), 255, np.uint8)
    b = _page(10, 10, 11)
    r = st.stitch(a, b, st.Placement(dx=10))
    assert r.image.shape == (10, 20, 3)


def test_a_quarter_turn_must_be_a_quarter_turn():
    with pytest.raises(ValueError):
        st.Placement(turn_b=45)
    assert st.Placement(turn_b=-90).turn_b == 270


def test_the_sidecar_round_trips_with_relative_paths(tmp_path):
    (tmp_path / "scans").mkdir()
    a_path = str(tmp_path / "scans" / "p16.png")
    b_path = str(tmp_path / "scans" / "p17.png")
    out = str(tmp_path / "spread.png")
    p = st.Placement(dx=1624, dy=147, angle_deg=0.25, turn_b=180)
    r = st.stitch(_page(20, 20, 12), _page(20, 20, 13),
                  st.Placement(dx=10))
    d = st.sidecar_dict(r, p, a_path, b_path, out, trim=12,
                        mm_per_px=0.127)
    assert d["page_a"] == "scans/p16.png"
    path = st.sidecar_path(out)
    assert path.endswith("spread.stitch.json")
    st.write_sidecar(path, d)
    back = st.read_sidecar(path)
    assert os.path.normcase(back["page_b"]) == os.path.normcase(b_path)
    assert back["placement"].to_dict() == p.to_dict()
    assert back["trim_px"] == 12


def test_a_foreign_json_is_not_a_sidecar(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"kind": "something"}')
    with pytest.raises(ValueError):
        st.read_sidecar(str(path))


@pytest.mark.skipif(not all(os.path.exists(p) for p in
                            (POGO_P16, POGO_P17, POGO_SPREAD)),
                    reason="the Pogo scans are not on this machine")
def test_the_pogo_spread_is_reproduced_pixel_for_pixel():
    """The hand-made spread ModelCraft traces from: p17 at (1624, 147)
    in p16, trimmed to the ink with a 12 px margin. The scans are
    colour; the hand stitch was made from them in grey."""
    a = cv2.imread(POGO_P16, cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(POGO_P17, cv2.IMREAD_GRAYSCALE)
    want = cv2.imread(POGO_SPREAD, cv2.IMREAD_GRAYSCALE)
    r = st.stitch(a, b, st.Placement(dx=1624, dy=147), trim=12)
    assert r.size == (3308, 2316)
    assert r.a_origin == (-16.0, -45.0)
    assert st._apply(r.b_to_out, (0, 0)) == (1608.0, 102.0)
    assert np.array_equal(r.image, want)
