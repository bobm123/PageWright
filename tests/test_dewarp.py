"""Tests for the single-page spline dewarp core (pagewright.core.dewarp).

Ported alongside the module from the dewarp project. These assert
geometric/structural properties rather than pixel hashes so they stay
valid across OpenCV/NumPy versions. NumPy/OpenCV are required; the whole
module is skipped if they are missing.
"""

import json

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from pagewright.core import dewarp as dw


# ---------------------------------------------------------------------------
# Curve utilities
# ---------------------------------------------------------------------------

def test_spline_edge_dense_hits_endpoints():
    a, b = [0.0, 0.0], [100.0, 0.0]
    d = dw.spline_edge_dense(a, [50.0, 20.0], [10.0, 5.0], b, n=400)
    assert d.shape == (400, 2)
    assert d[0] == pytest.approx(a, abs=1e-9)
    assert d[-1] == pytest.approx(b, abs=1e-6)


def test_sample_by_arclength_is_monotone_and_bounded():
    d = dw.spline_edge_dense([0, 0], [50, 30], [8, 4], [100, 0], n=500)
    s = np.linspace(0, 1, 20)
    pts, total = dw.sample_by_arclength(d, s)
    assert total > 0
    assert pts.shape == (20, 2)
    # x runs left-to-right monotonically for this convex-ish edge
    assert np.all(np.diff(pts[:, 0]) >= -1e-6)


def test_fit_smooth_curve_endpoints_exact():
    pts = [[0, 0], [25, 8], [50, 10], [75, 7], [100, 0]]
    d = dw.fit_smooth_curve(pts, n=300)
    assert d[0] == pytest.approx(pts[0], abs=1e-6)
    assert d[-1] == pytest.approx(pts[-1], abs=1e-6)


def test_fit_smooth_curve_needs_two_points():
    with pytest.raises(ValueError):
        dw.fit_smooth_curve([[1.0, 2.0]])


def test_fit_spline_edge_tracks_target():
    xs = np.linspace(0, 100, 60)
    ys = 12 * np.sin(np.linspace(0, np.pi, 60))
    target = np.stack([xs, ys], 1)
    a, b = np.array([0.0, 0.0]), np.array([100.0, 0.0])
    m, h, ta, tb = dw.fit_spline_edge(a, b, target)
    dense = dw.spline_edge_dense(a, m, h, b, ta=ta, tb=tb, n=800)
    fitted, _ = dw.sample_by_arclength(dense, np.linspace(0, 1, 60))
    # the smooth single-control-point spline should track a single arch well
    assert np.max(np.abs(fitted[:, 1] - ys)) < 2.0


# ---------------------------------------------------------------------------
# Model construction / dataclasses
# ---------------------------------------------------------------------------

def test_default_model_structure():
    m = dw.default_model(1000, 800)
    assert isinstance(m, dw.PageModel)
    assert set(m.anchors) == {"tl", "tr", "bl", "br"}
    assert set(m.edges) == {"top", "bottom"}
    assert isinstance(m.edges["top"], dw.PageEdge)


def test_model_from_traces_anchor_endpoints():
    top = [[i * 10.0, 0.0] for i in range(11)]
    bot = [[i * 10.0, 120.0] for i in range(11)]
    m = dw.model_from_traces(top, bot)
    assert m.anchors["tl"] == pytest.approx([0.0, 0.0])
    assert m.anchors["tr"] == pytest.approx([100.0, 0.0])
    assert m.anchors["bl"] == pytest.approx([0.0, 120.0])
    assert m.anchors["br"] == pytest.approx([100.0, 120.0])


def test_page_edges_start_and_end_at_anchors():
    m = dw.default_model(600, 400)
    top, bot = dw.page_edges(m)
    assert top[0] == pytest.approx(m.anchors["tl"], abs=1e-6)
    assert top[-1] == pytest.approx(m.anchors["tr"], abs=1e-6)
    assert bot[0] == pytest.approx(m.anchors["bl"], abs=1e-6)
    assert bot[-1] == pytest.approx(m.anchors["br"], abs=1e-6)


def test_page_size_px_flat_page():
    # a flat, axis-aligned page: width ~ 100, height ~ 120
    top = [[i * 10.0, 0.0] for i in range(11)]
    bot = [[i * 10.0, 120.0] for i in range(11)]
    m = dw.model_from_traces(top, bot)
    w, h = dw.page_size_px(m)
    assert w == pytest.approx(100.0, abs=1.0)
    assert h == pytest.approx(120.0, abs=1.0)


def test_model_to_from_dict_roundtrip():
    m = dw.default_model(800, 600)
    d = m.to_dict()
    # JSON-serialisable and lossless
    m2 = dw.PageModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d


def test_model_copy_is_deep():
    m = dw.default_model(800, 600)
    c = m.copy()
    c.anchors["tl"] = [1.0, 1.0]
    c.edges["top"].mid = [2.0, 2.0]
    assert m.anchors["tl"] != [1.0, 1.0]
    assert m.edges["top"].mid != [2.0, 2.0]


def test_toggle_corner_tip_on_is_curve_neutral():
    # activating a corner handle seeds it at the default (chord/3), so
    # the curve must not move until the handle is dragged
    m = dw.default_model(800, 600)
    for name in ("top", "bottom"):
        m.edges[name].tip_a = None
        m.edges[name].tip_b = None
    before = dw.edge_dense(m, "top", 500).copy()
    assert dw.toggle_corner_tip(m, "tl") is True
    assert dw.corner_tip_active(m, "tl")
    after = dw.edge_dense(m, "top", 500)
    assert np.max(np.abs(after - before)) < 1e-9


def test_toggle_corner_tip_off_and_mapping():
    m = dw.default_model(800, 600)
    # default_model sets all tips; toggling turns each OFF
    for corner, (edge, end) in dw.CORNER_EDGE_END.items():
        assert dw.corner_tip_active(m, corner)
        assert dw.toggle_corner_tip(m, corner) is False
        e = m.edges[edge]
        assert (e.tip_a if end == "a" else e.tip_b) is None
    # and back on
    assert dw.toggle_corner_tip(m, "br") is True
    assert m.edges["bottom"].tip_b is not None


def test_broken_mid_tangent_forms_v():
    # a broken mid tangent lets the curve fold into a V at mid (the book
    # gutter). Handles pointing down-left and up-right make a sharp kink.
    a, b, mid = [0.0, 0.0], [100.0, 0.0], [50.0, 30.0]
    m = dw.model_from_traces([[0, 0], [50, 0], [100, 0]],
                             [[0, 100], [50, 100], [100, 100]])
    e = m.edges["top"]
    e.mid = list(mid)
    e.handle = [-10.0, -10.0]        # incoming tip at mid - h = (60, 40)?
    dw.break_mid_tangent(m, "top")
    e.handle_out = [10.0, -10.0]     # outgoing rises: sharp V at mid
    e.tip_a = None
    e.tip_b = None
    dense = dw.edge_dense(m, "top", 1001)
    i = np.argmin(np.abs(dense[:, 0] - 50.0))
    # curve passes through mid (continuity)
    assert dense[i] == pytest.approx(mid, abs=1.0)
    # slope flips sign across mid: dy/dx < 0 before, > 0 after? incoming
    # tangent 3*h = (-30,-30) points down-left along curve direction ->
    # curve descends INTO mid from the left going down... measure kink:
    before = dense[i] - dense[i - 20]
    after = dense[i + 20] - dense[i]
    ang_before = np.arctan2(before[1], before[0])
    ang_after = np.arctan2(after[1], after[0])
    assert abs(ang_after - ang_before) > 0.5   # pronounced direction change


def test_broken_equal_handles_identical_to_smooth():
    m = dw.default_model(800, 600)
    smooth = dw.edge_dense(m, "top", 500).copy()
    dw.break_mid_tangent(m, "top")      # handle_out seeded = handle
    broken = dw.edge_dense(m, "top", 500)
    assert np.max(np.abs(broken - smooth)) < 1e-12
    dw.smooth_mid_tangent(m, "top")
    assert m.edges["top"].handle_out is None


def test_broken_tips_move_independently():
    m = dw.default_model(800, 600)
    dw.break_mid_tangent(m, "top")
    e = m.edges["top"]
    mid = np.array(e.mid)
    dw.move_handle(m, ("tip", "top", +1), mid + [40.0, -25.0])
    assert e.handle_out == pytest.approx([40.0, -25.0])
    old_out = list(e.handle_out)
    dw.move_handle(m, ("tip", "top", -1), mid + [-10.0, 30.0])
    assert e.handle == pytest.approx([10.0, -30.0])   # sgn * (pos - mid)
    assert e.handle_out == pytest.approx(old_out)     # unaffected
    # handle_pos reflects the independent sides
    assert dw.handle_pos(m, ("tip", "top", +1)) == pytest.approx(
        mid + [40.0, -25.0])
    assert dw.handle_pos(m, ("tip", "top", -1)) == pytest.approx(
        mid - [10.0, -30.0])


def test_broken_edge_roundtrips_and_scales():
    m = dw.default_model(800, 600)
    dw.break_mid_tangent(m, "top")
    m.edges["top"].handle_out = [12.0, -8.0]
    d = m.to_dict()
    assert d["edges"]["top"]["handle_out"] == [12.0, -8.0]
    m2 = dw.PageModel.from_dict(json.loads(json.dumps(d)))
    assert m2.edges["top"].handle_out == [12.0, -8.0]
    s = m2.scaled(0.5)
    assert s.edges["top"].handle_out == pytest.approx([6.0, -4.0])
    # smooth edge omits the key entirely (older files stay loadable)
    assert "handle_out" not in d["edges"]["bottom"]


def test_model_from_straight_traces_stays_straight():
    # seeding a spline from straight lines (the quad -> spline promotion)
    # must reproduce the straight edges: dewarping with it is then
    # equivalent to the plain quad transform until the user bends it
    tl, tr = np.array([100.0, 80.0]), np.array([500.0, 110.0])
    bl, br = np.array([90.0, 330.0]), np.array([520.0, 360.0])
    top = np.linspace(tl, tr, dw.N_PTS)
    bot = np.linspace(bl, br, dw.N_PTS)
    m = dw.model_from_traces(top, bot)
    dense_top, dense_bot = dw.page_edges(m, 400)
    for dense, a, b in ((dense_top, tl, tr), (dense_bot, bl, br)):
        # max distance of the dense curve from the straight segment
        v = b - a
        L2 = float(v @ v)
        t = np.clip(((dense - a) @ v) / L2, 0.0, 1.0)
        proj = a + t[:, None] * v
        assert np.max(np.linalg.norm(dense - proj, axis=1)) < 0.5


def test_model_scaled_scales_coords_and_vectors():
    m = dw.default_model(800, 600)
    s = m.scaled(0.5)
    # anchors scale
    assert s.anchors["br"] == pytest.approx(
        [v * 0.5 for v in m.anchors["br"]])
    # mid, handle, and corner tips scale too
    e, es = m.edges["top"], s.edges["top"]
    assert es.mid == pytest.approx([v * 0.5 for v in e.mid])
    assert es.handle == pytest.approx([v * 0.5 for v in e.handle])
    assert es.tip_a == pytest.approx([v * 0.5 for v in e.tip_a])
    # scaling the model scales the dense curve identically
    top_full, _ = dw.page_edges(m)
    top_half, _ = dw.page_edges(s)
    assert np.max(np.abs(top_half - top_full * 0.5)) < 1e-9
    # original untouched
    assert m.edges["top"].mid != es.mid


def test_edge_optional_tips_serialise_only_when_set():
    e = dw.PageEdge(mid=[1, 2], handle=[3, 4])
    assert "tip_a" not in e.to_dict() and "tip_b" not in e.to_dict()
    e.tip_a = [0.5, 0.5]
    assert e.to_dict()["tip_a"] == [0.5, 0.5]


# ---------------------------------------------------------------------------
# GUI handle helpers
# ---------------------------------------------------------------------------

def test_handles_cover_all_draggables():
    m = dw.default_model(500, 400)
    hs = dw.handles(m)
    kinds = [h[0] for h in hs]
    assert kinds.count("anchor") == 4
    assert kinds.count("mid") == 2
    assert kinds.count("tip") == 4
    assert kinds.count("ctip") == 4


def test_move_handle_updates_model():
    m = dw.default_model(500, 400)
    dw.move_handle(m, ("anchor", "tl", None), [7.0, 8.0])
    assert m.anchors["tl"] == [7.0, 8.0]
    dw.move_handle(m, ("mid", "top", None), [10.0, 11.0])
    assert m.edges["top"].mid == [10.0, 11.0]
    assert dw.handle_pos(m, ("mid", "top", None)) == pytest.approx([10.0, 11.0])


# ---------------------------------------------------------------------------
# Quad (4-point) perspective dewarp
# ---------------------------------------------------------------------------

def test_order_points_orders_tl_tr_br_bl():
    # deliberately scrambled input
    pts = [(560, 360), (120, 80), (90, 330), (520, 110)]
    rect = dw.order_points(pts)
    assert rect.tolist() == [[120, 80], [520, 110], [560, 360], [90, 330]]


def test_order_points_requires_four():
    with pytest.raises(ValueError):
        dw.order_points([(0, 0), (1, 1), (2, 2)])


def test_dewarp_quad_crop_shape_and_content():
    img = np.zeros((400, 600, 3), np.uint8)
    img[:, 300:] = 255
    # axis-aligned selection: left dark, right bright, preserved after warp
    pts = [(100, 80), (500, 80), (500, 320), (100, 320)]
    out = dw.dewarp_quad(img, pts, 300, 200, full_image=False)
    assert out.shape == (200, 300, 3)
    assert out[:, 5].mean() < 60
    assert out[:, -5].mean() > 195


def test_dewarp_quad_axis_aligned_is_identity_like():
    # selecting an axis-aligned rectangle and asking for its own pixel
    # size should reproduce that crop. Use a smooth low-frequency ramp so
    # the tiny (span -> span-1) rescale + interpolation stays small.
    yy, xx = np.mgrid[0:400, 0:600].astype(np.float32)
    ramp = (0.4 * xx + 0.3 * yy)
    ramp = (ramp / ramp.max() * 255).astype(np.uint8)
    img = np.dstack([ramp, ramp, ramp])
    x0, y0, x1, y1 = 100, 50, 400, 250
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out = dw.dewarp_quad(img, pts, x1 - x0, y1 - y0, full_image=False)
    ref = img[y0:y1, x0:x1]
    assert out.shape == ref.shape
    assert np.abs(out.astype(int) - ref.astype(int)).mean() < 2.0


def test_dewarp_quad_full_image_is_larger_and_unclipped():
    img = np.full((300, 400, 3), 128, np.uint8)
    pts = [(120, 80), (300, 100), (320, 240), (90, 220)]
    crop = dw.dewarp_quad(img, pts, 200, 150, full_image=False)
    full = dw.dewarp_quad(img, pts, 200, 150, full_image=True)
    assert crop.shape == (150, 200, 3)
    # full-image canvas contains the whole warped image, so it is bigger
    assert full.shape[0] >= crop.shape[0] and full.shape[1] >= crop.shape[1]
    assert full.shape[0] > 150 or full.shape[1] > 200


def test_dewarp_quad_rejects_tiny_output():
    img = np.zeros((100, 100, 3), np.uint8)
    pts = [(10, 10), (90, 10), (90, 90), (10, 90)]
    with pytest.raises(ValueError):
        dw.dewarp_quad(img, pts, 1, 50)


# ---------------------------------------------------------------------------
# Detection and curved-page dewarp
# ---------------------------------------------------------------------------

def _synthetic_page(bg=20, fg=235):
    canvas = np.full((500, 700), bg, np.uint8)
    quad = np.array([[120, 90], [590, 110], [600, 400], [100, 410]], np.int32)
    cv2.fillConvexPoly(canvas, quad, fg)
    return canvas


def test_detect_page_recovers_corners():
    gray = _synthetic_page()
    m = dw.detect_page(gray)
    # corners should land near the painted quad corners (within a few px)
    assert m.anchors["tl"] == pytest.approx([120, 90], abs=6)
    assert m.anchors["tr"] == pytest.approx([590, 110], abs=6)
    assert m.anchors["br"] == pytest.approx([600, 400], abs=6)
    assert m.anchors["bl"] == pytest.approx([100, 410], abs=6)


def test_detect_page_roi_too_small_raises():
    gray = _synthetic_page()
    with pytest.raises(RuntimeError):
        dw.detect_page(gray, roi=(10, 10, 20, 20))


def test_detect_page_blank_returns_full_frame_model():
    # a uniform image has no distinguishable blob; detection degenerates
    # to a model spanning (near) the whole frame rather than crashing
    m = dw.detect_page(np.full((300, 300), 128, np.uint8))
    assert isinstance(m, dw.PageModel)
    assert set(m.anchors) == {"tl", "tr", "bl", "br"}


def test_dewarp_page_shape_and_flat_identity():
    # a page whose model is a straight axis-aligned rectangle should
    # dewarp (near) identically - rulings are straight, arc length linear
    img = np.zeros((400, 600, 3), np.uint8)
    img[:, 300:] = 255  # left half black, right half white
    top = [[100 + i * 40.0, 100.0] for i in range(11)]
    bot = [[100 + i * 40.0, 300.0] for i in range(11)]
    m = dw.model_from_traces(top, bot)
    out = dw.dewarp_page(img, m, out_w=200, out_h=150)
    assert out.shape == (150, 200, 3)
    # left column dark, right column bright (orientation preserved)
    assert out[:, 5].mean() < 60
    assert out[:, -5].mean() > 195


def test_dewarp_page_derives_size_when_none():
    img = np.zeros((400, 600, 3), np.uint8)
    m = dw.default_model(600, 400)
    out = dw.dewarp_page(img, m)
    assert out.ndim == 3 and out.shape[0] > 1 and out.shape[1] > 1


def test_refine_with_text_no_lines_returns_unrefined():
    gray = np.full((400, 600), 235, np.uint8)  # blank: no text lines
    m = dw.default_model(600, 400)
    m2, refined = dw.refine_with_text(gray, m)
    assert refined is False


def test_refine_with_text_flags_when_lines_present():
    page = np.full((500, 700), 235, np.uint8)
    for yy in range(140, 380, 26):
        cv2.rectangle(page, (150, yy), (560, yy + 6), 30, -1)
    m = dw.default_model(700, 500)
    m2, refined = dw.refine_with_text(page, m)
    assert refined is True
    assert isinstance(m2, dw.PageModel)


# ---------------------------------------------------------------------------
# Two-page spread model
# ---------------------------------------------------------------------------

def test_default_spread_structure():
    m = dw.default_spread_model(1000, 800)
    assert isinstance(m, dw.SpreadModel)
    assert set(m.anchors) == {"tl", "tr", "bl", "br",
                              "spine_top", "spine_bot"}
    assert set(m.edges) == set(dw.SPREAD_EDGES)
    for e in m.edges.values():
        assert e.spine_handle is not None   # ensure_spine_handles ran


def test_spread_edges_meet_at_spine():
    m = dw.default_spread_model(1000, 800)
    lt = dw.spread_edge_dense(m, "left_top", 300)
    rt = dw.spread_edge_dense(m, "right_top", 300)
    assert lt[-1] == pytest.approx(m.anchors["spine_top"], abs=1e-6)
    assert rt[0] == pytest.approx(m.anchors["spine_top"], abs=1e-6)


def test_spread_roundtrip_and_scaled():
    m = dw.default_spread_model(800, 600)
    d = m.to_dict()
    m2 = dw.SpreadModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    s = m2.scaled(0.5)
    assert s.anchors["spine_top"] == pytest.approx(
        [v * 0.5 for v in m2.anchors["spine_top"]])
    assert s.edges["left_top"].spine_handle == pytest.approx(
        [v * 0.5 for v in m2.edges["left_top"].spine_handle])
    # scaled model's curves are exactly scaled curves
    a = dw.spread_edge_dense(m2, "right_bottom", 200) * 0.5
    b = dw.spread_edge_dense(s, "right_bottom", 200)
    assert np.max(np.abs(a - b)) < 1e-9


def test_quad_to_spread_matches_quad_until_edited():
    # straight-seeded spread edges must stay straight (like the quad)
    pts = [(100.0, 80.0), (500.0, 110.0), (520.0, 360.0), (90.0, 330.0)]
    m = dw.quad_to_spread(pts, gutter_t=0.5)
    rect = dw.order_points(pts)
    tl, tr = np.asarray(rect[0], float), np.asarray(rect[1], float)
    dense = np.vstack([dw.spread_edge_dense(m, "left_top", 200),
                       dw.spread_edge_dense(m, "right_top", 200)])
    v = tr - tl
    t = np.clip(((dense - tl) @ v) / float(v @ v), 0.0, 1.0)
    proj = tl + t[:, None] * v
    assert np.max(np.linalg.norm(dense - proj, axis=1)) < 0.75


def test_page_to_spread_spine_at_gutter():
    # a page model whose mid sits off-center: the spread's spine lands
    # at that mid (the gutter), not at the arc midpoint
    top = [[i * 10.0, 0.0] for i in range(11)]
    bot = [[i * 10.0, 120.0] for i in range(11)]
    pm = dw.model_from_traces(top, bot)
    pm.edges["top"].mid = [40.0, 5.0]
    pm.edges["bottom"].mid = [42.0, 118.0]
    sm = dw.page_to_spread(pm)
    assert isinstance(sm, dw.SpreadModel)
    assert sm.anchors["spine_top"][0] == pytest.approx(40.0, abs=2.0)
    assert sm.anchors["spine_bot"][0] == pytest.approx(42.0, abs=2.0)


def test_spread_to_page_roundtrip_outline():
    sm = dw.default_spread_model(900, 700)
    pm = dw.spread_to_page(sm)
    assert isinstance(pm, dw.PageModel)
    # outer corners preserved
    for k in ("tl", "tr", "bl", "br"):
        assert pm.anchors[k] == pytest.approx(sm.anchors[k], abs=1.5)


def test_dewarp_spread_two_independent_pages():
    img = np.zeros((400, 800, 3), np.uint8)
    img[:, :400] = 40           # left half dark
    img[:, 400:] = 220          # right half bright
    m = dw.quad_to_spread([(100, 50), (700, 50), (700, 350), (100, 350)],
                          gutter_t=0.5)
    left, right = dw.dewarp_spread(img, m, out_w=150, out_h=100)
    assert left.shape == (100, 150, 3)
    assert right.shape == (100, 150, 3)
    assert left.mean() < 90 and right.mean() > 170


def test_spread_handles_cover_all_draggables():
    m = dw.default_spread_model(500, 400)
    hs = dw.spread_handles(m)
    kinds = [x[0] for x in hs]
    assert kinds.count("anchor") == 6
    assert kinds.count("mid") == 4
    assert kinds.count("tip") == 8
    assert kinds.count("stip") == 4
    # move a spine anchor and a spine tip
    dw.spread_move_handle(m, ("anchor", "spine_top", None), (321.0, 45.0))
    assert m.anchors["spine_top"] == [321.0, 45.0]
    dw.spread_move_handle(m, ("stip", "left_top", None), (300.0, 60.0))
    p = dw.spread_handle_pos(m, ("stip", "left_top", None))
    assert p == pytest.approx([300.0, 60.0])


def test_refine_spread_no_lines_unrefined():
    gray = np.full((400, 800), 235, np.uint8)
    m = dw.default_spread_model(800, 400)
    m2, refined = dw.refine_spread_with_text(gray, m)
    assert refined is False
