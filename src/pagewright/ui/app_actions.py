"""Construction of the window's QActions, menus, toolbar and canvas menu.

Pure construction split out of main_window: each function takes the window,
creates widgets/actions on it and wires them to the window's handlers. Keeping
this here leaves main_window as a coordinator rather than a wall of setup.
"""

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMenu

_BRUSH_PRESETS = (10, 20, 40, 80, 120)


def build_actions(w):
    """Create every QAction on the window `w`."""
    w.act_open_project = QAction("&Open Project…", w)
    w.act_open_project.setShortcut(QKeySequence.Open)        # Ctrl+O
    w.act_open_project.triggered.connect(w.open_project_file)

    w.act_save_project = QAction("&Save Project…", w)
    w.act_save_project.setShortcut(QKeySequence.Save)        # Ctrl+S
    w.act_save_project.triggered.connect(w.save_project_file)

    w.act_paste = QAction("&Paste Image", w)
    w.act_paste.setShortcut(QKeySequence.Paste)
    w.act_paste.setToolTip("Start from a screenshot or image on the "
                           "clipboard")
    w.act_paste.triggered.connect(w.paste_image)

    # first-class tool switcher (trace / flatten / OCR share the image)
    w.tool_group = QActionGroup(w)
    w.tool_group.setExclusive(True)
    w.act_tool_trace = QAction("&Trace", w, checkable=True)
    w.act_tool_trace.setChecked(True)
    w.act_tool_trace.triggered.connect(lambda: w.switch_tool("trace"))
    w.act_tool_flatten = QAction("F&latten", w, checkable=True)
    w.act_tool_flatten.triggered.connect(lambda: w.switch_tool("flatten"))
    w.act_tool_ocr = QAction("&OCR", w, checkable=True)
    w.act_tool_ocr.triggered.connect(lambda: w.switch_tool("ocr"))
    w.act_tool_scale = QAction("&Scale", w, checkable=True)
    w.act_tool_scale.setToolTip(
        "Set the document's real-world size (calibrate / type a size), "
        "then print or save at true scale")
    w.act_tool_scale.triggered.connect(lambda: w.switch_tool("scale"))
    for a in (w.act_tool_trace, w.act_tool_flatten, w.act_tool_scale,
              w.act_tool_ocr):
        w.tool_group.addAction(a)

    w.act_open = QAction("&Load Image…", w)
    w.act_open.setShortcut("Ctrl+Shift+O")
    w.act_open.triggered.connect(w.open_photo)

    w.act_export = QAction("&Export SVG…", w)
    w.act_export.setShortcut("Ctrl+E")
    w.act_export.triggered.connect(w.export_svg)

    w.act_print_tiles = QAction("Print &Tiles…", w)
    w.act_print_tiles.setToolTip(
        "Preview the tile pages, then print or save as PDF")
    w.act_print_tiles.triggered.connect(w.print_tiles)

    w.act_export_tiles = QAction("&Export Tiles…", w)
    w.act_export_tiles.setToolTip(
        "Preview the tile pages, then write them as SVG files")
    w.act_export_tiles.triggered.connect(w.export_tiles)

    w.act_preferences = QAction("&Preferences…", w)
    w.act_preferences.setShortcut("Ctrl+,")
    w.act_preferences.setToolTip("Application settings (brush size)")
    w.act_preferences.triggered.connect(w.open_preferences)

    w.act_quit = QAction("&Quit", w)
    w.act_quit.setShortcut(QKeySequence.Quit)
    w.act_quit.triggered.connect(w.close)

    w.act_zoom_in = QAction("Zoom &In", w)
    w.act_zoom_in.setShortcut(QKeySequence.ZoomIn)
    w.act_zoom_in.triggered.connect(w.canvas.zoom_in)

    w.act_zoom_out = QAction("Zoom &Out", w)
    w.act_zoom_out.setShortcut(QKeySequence.ZoomOut)
    w.act_zoom_out.triggered.connect(w.canvas.zoom_out)

    w.act_rotate_cw = QAction("Rotate Image 90 deg CW", w)
    w.act_rotate_cw.setShortcut("R")
    w.act_rotate_cw.triggered.connect(lambda: w.rotate_working(True))
    w.act_rotate_ccw = QAction("Rotate Image 90 deg CCW", w)
    w.act_rotate_ccw.setShortcut("L")
    w.act_rotate_ccw.triggered.connect(lambda: w.rotate_working(False))

    w.act_fit = QAction("&Fit to Window", w)
    w.act_fit.triggered.connect(w.canvas.fit_to_view)

    w.act_show_bbox = QAction("Show &Bounding Box", w, checkable=True)
    w.act_show_bbox.setChecked(True)
    w.act_show_bbox.triggered.connect(w._update_bbox)

    w.act_view_tiles = QAction("View &Tiles", w, checkable=True)
    w.act_view_tiles.setToolTip(
        "Overlay the page-tile grid for the current print settings")
    w.act_view_tiles.triggered.connect(
        lambda checked: w.set_tiles_overlay(checked))

    w.act_calibrate = QAction("&Calibrate Scale…", w)
    w.act_calibrate.triggered.connect(w.start_calibration)

    w.act_dewarp = QAction("&Flatten Page (Dewarp)…", w)
    w.act_dewarp.setToolTip("Perspective-flatten a photographed page; the "
                            "result becomes the working image")
    w.act_dewarp.triggered.connect(w.dewarp_page)

    # Mutually exclusive interaction modes.
    w.mode_group = QActionGroup(w)
    w.mode_group.setExclusive(True)
    w.act_mode_pan = QAction("&Pan / Zoom", w, checkable=True)
    w.act_mode_pan.setChecked(True)
    w.act_mode_pan.triggered.connect(w._mode_pan)
    w.act_mode_fg = QAction("Mark &Foreground (inside)", w, checkable=True)
    w.act_mode_fg.triggered.connect(w._mode_seed_fg)
    w.act_mode_bg = QAction("Mark &Background (outside)", w, checkable=True)
    w.act_mode_bg.triggered.connect(w._mode_seed_bg)
    w.act_mode_edit = QAction("&Edit Vertices", w, checkable=True)
    w.act_mode_edit.triggered.connect(w._mode_edit)
    w.act_mode_roi = QAction("Select &Area", w, checkable=True)
    w.act_mode_roi.setToolTip(
        "Drag a box to zoom to it and restrict tracing to that area")
    w.act_mode_roi.triggered.connect(w._mode_roi)
    for a in (w.act_mode_pan, w.act_mode_roi, w.act_mode_fg, w.act_mode_bg,
              w.act_mode_edit):
        w.mode_group.addAction(a)

    w.act_clear_roi = QAction("Clear &Trace Area", w)
    w.act_clear_roi.setToolTip("Trace the whole image again")
    w.act_clear_roi.triggered.connect(w.clear_roi)

    w.act_run_seg = QAction("Trace &Poly", w)
    w.act_run_seg.setToolTip("Trace a polygon from the seeds (GrabCut)")
    w.act_run_seg.triggered.connect(w.run_segmentation)

    w.act_new_object = QAction("New &Polygon", w)
    w.act_new_object.setShortcut("Ctrl+N")
    w.act_new_object.setToolTip(
        "Start a new polygon and begin marking inside it")
    w.act_new_object.triggered.connect(w.add_polygon)

    # Undo / redo: dispatch to seed strokes or the edit command stack.
    w.act_undo = QAction("&Undo", w)
    w.act_undo.setShortcut(QKeySequence.Undo)        # Ctrl+Z
    w.act_undo.triggered.connect(w._do_undo)
    w.act_redo = QAction("&Redo", w)
    w.act_redo.setShortcut("Ctrl+Y")
    w.act_redo.triggered.connect(w._do_redo)
    w.act_clear_seeds = QAction("Clear &Seeds", w)
    w.act_clear_seeds.triggered.connect(w.clear_seeds)

    w.act_units = {}
    for unit in ("mm", "cm", "in"):
        a = QAction(unit, w, checkable=True)
        a.setChecked(unit == w.project.calibration.display_unit)
        a.triggered.connect(lambda _=False, u=unit: w.set_unit(u))
        w.act_units[unit] = a


def build_menus(w):
    mb = w.menuBar()

    m_file = mb.addMenu("&File")
    m_file.addAction(w.act_open_project)
    m_file.addAction(w.act_save_project)
    m_file.addSeparator()
    m_file.addAction(w.act_open)
    m_file.addAction(w.act_paste)
    m_file.addAction(w.act_export)
    m_file.addAction(w.act_export_tiles)
    m_file.addAction(w.act_print_tiles)
    m_file.addSeparator()
    m_file.addAction(w.act_preferences)
    m_file.addSeparator()
    m_file.addAction(w.act_quit)

    m_edit = mb.addMenu("&Edit")
    m_edit.addAction(w.act_undo)
    m_edit.addAction(w.act_redo)

    m_view = mb.addMenu("&View")
    m_view.addAction(w.act_zoom_in)
    m_view.addAction(w.act_zoom_out)
    m_view.addAction(w.act_fit)
    m_view.addSeparator()
    m_view.addAction(w.act_rotate_cw)
    m_view.addAction(w.act_rotate_ccw)
    m_view.addSeparator()
    m_view.addAction(w.act_show_bbox)
    m_view.addAction(w.act_view_tiles)
    m_view.addSeparator()
    m_units = m_view.addMenu("Display &Units")
    for unit in ("mm", "cm", "in"):
        m_units.addAction(w.act_units[unit])

    m_tools = mb.addMenu("&Tools")
    m_tools.addAction(w.act_tool_trace)
    m_tools.addAction(w.act_tool_flatten)
    m_tools.addAction(w.act_tool_scale)
    m_tools.addAction(w.act_tool_ocr)
    m_tools.addSeparator()
    m_tools.addAction(w.act_dewarp)
    m_tools.addAction(w.act_calibrate)
    m_tools.addSeparator()
    m_tools.addAction(w.act_mode_pan)
    m_tools.addAction(w.act_mode_roi)
    m_tools.addAction(w.act_mode_fg)
    m_tools.addAction(w.act_mode_bg)
    m_tools.addAction(w.act_mode_edit)
    m_tools.addSeparator()
    m_tools.addAction(w.act_new_object)
    m_tools.addAction(w.act_run_seg)
    m_tools.addAction(w.act_clear_seeds)
    m_tools.addAction(w.act_clear_roi)


def build_toolbar(w):
    """Only high-frequency, non-redundant controls; the mode toggles double as
    a current-mode indicator. Everything else is in the menus, the shortcuts
    and the right-click menu."""
    tb = w.addToolBar("Main")
    tb.setMovable(False)
    # the hub: co-equal tools over one shared working image
    tb.addAction(w.act_tool_trace)
    tb.addAction(w.act_tool_flatten)
    tb.addAction(w.act_tool_scale)
    tb.addAction(w.act_tool_ocr)
    tb.addAction(w.act_print_tiles)
    tb.addSeparator()
    tb.addAction(w.act_mode_pan)
    tb.addAction(w.act_mode_roi)
    tb.addSeparator()
    # Left-to-right in workflow order: new polygon -> mark inside ->
    # mark outside -> trace -> refine.
    tb.addAction(w.act_new_object)
    tb.addAction(w.act_mode_fg)
    tb.addAction(w.act_mode_bg)
    tb.addAction(w.act_run_seg)
    tb.addAction(w.act_mode_edit)


def show_canvas_menu(w, global_pos):
    """Right-click menu on the trace canvas: the tools that matter for
    tracing, mode-aware, plus one-click jumps to the other stages."""
    menu = QMenu(w)
    menu.addAction(w.act_run_seg)       # Trace Poly
    menu.addAction(w.act_new_object)    # New Polygon
    menu.addSeparator()
    menu.addAction(w.act_mode_pan)
    menu.addAction(w.act_mode_roi)
    menu.addAction(w.act_mode_fg)
    menu.addAction(w.act_mode_bg)
    menu.addAction(w.act_mode_edit)
    if w.canvas.roi_rect() is not None:
        menu.addAction(w.act_clear_roi)
    # brush size only matters while seeding
    from .canvas import MODE_SEED_BG, MODE_SEED_FG
    if w.canvas._mode in (MODE_SEED_FG, MODE_SEED_BG):
        brush_menu = menu.addMenu("Brush Size")
        cur = int(round(w.canvas.brush_radius()))
        for sz in _BRUSH_PRESETS:
            a = brush_menu.addAction("%d px" % sz)
            a.setCheckable(True)
            a.setChecked(sz == cur)
            a.triggered.connect(
                lambda _=False, s=sz: w.canvas.set_brush_radius(s))
        menu.addAction(w.act_clear_seeds)
    menu.addSeparator()
    menu.addAction(w.act_calibrate)
    menu.addAction(w.act_fit)
    menu.addSeparator()
    menu.addAction(w.act_tool_flatten)   # jump to the other tools
    menu.addAction(w.act_tool_scale)
    menu.addAction(w.act_tool_ocr)
    menu.addAction(w.act_print_tiles)
    menu.exec(global_pos)
