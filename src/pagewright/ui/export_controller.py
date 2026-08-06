"""Export actions: a single true-scale SVG, and tiled 1:1 print pages.

A deliberate collaborator of MainWindow (it reads the window's project, loaded
image and tiling panel). Split out of main_window to keep that file focused.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from ..core import svg_export
from ..core import tiling
from .dialogs import ExportSvgDialog


class ExportController:
    def __init__(self, window):
        self._w = window

    def export_svg(self):
        w = self._w
        if w._loaded is None:
            return
        if not w.project.calibration.is_calibrated:
            QMessageBox.warning(
                w, "Export SVG",
                "Calibrate the scale first so the SVG can be sized in mm.")
            return
        w._sync_model()
        if not w.project.objects:
            QMessageBox.information(
                w, "Export SVG", "Trace at least one object first.")
            return

        dlg = ExportSvgDialog(w)
        if dlg.exec() != QDialog.Accepted:
            return
        embed, downscale_max, filled, inkscape = dlg.values()

        base = os.path.splitext(os.path.basename(w._loaded.path or "trace"))[0]
        default_path = os.path.join(os.getcwd(), base + ".svg")
        path, _ = QFileDialog.getSaveFileName(
            w, "Export SVG", default_path, "SVG files (*.svg)")
        if not path:
            return

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                svg = svg_export.build_svg(
                    w.project,
                    image_bgr=w._loaded.data if embed else None,
                    embed_photo=embed,
                    downscale_max=downscale_max,
                    filled=filled,
                    inkscape=inkscape)
            finally:
                QApplication.restoreOverrideCursor()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg)
        except svg_export.ExportError as exc:
            QMessageBox.warning(w, "Export SVG", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(w, "Export SVG", "Export failed: %s" % (exc,))
            return

        w.statusBar().showMessage("Exported %s" % path, 6000)

    def _tile_region(self):
        """The Select Area rectangle as (x0, y0, x1, y1) image px, clamped
        to the image, or None. When set it defines the tiled region."""
        w = self._w
        rect = w.canvas.roi_rect()
        if rect is None or w._loaded is None:
            return None
        pw, ph = w._loaded.pixel_width, w._loaded.pixel_height
        x0 = max(0.0, min(float(rect.left()), float(pw)))
        y0 = max(0.0, min(float(rect.top()), float(ph)))
        x1 = max(0.0, min(float(rect.right()), float(pw)))
        y1 = max(0.0, min(float(rect.bottom()), float(ph)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return (x0, y0, x1, y1)

    def _resolved_tile_params(self, title):
        """Common preflight for tile export/printing. Returns (params,
        mm_per_pixel) or None. Image-only tiling (no traces) is allowed
        when the photo is included."""
        w = self._w
        if w._loaded is None:
            return None
        w._sync_model()
        p = w.tiling_panel.params()
        region = self._tile_region()
        if not w.project.objects and not p["embed"]:
            QMessageBox.information(
                w, title,
                "Nothing to print: trace an object, or turn on "
                "'Include image' to tile the image itself.")
            return None
        scale = p["scale"]
        if scale is None:
            # Fixed grid: derive the scale that fills cols x rows pages
            from ..core import geometry as geo
            if region is not None:
                pts = [(region[0], region[1]), (region[2], region[3])]
            else:
                pts = []
                for obj in w.project.objects:
                    for c in obj.contours:
                        pts.extend(c.points)
                if not pts:
                    pts = [(0.0, 0.0), (float(w._loaded.pixel_width),
                                        float(w._loaded.pixel_height))]
            box = geo.bbox_of_points(pts)
            mpp1 = w.effective_mm_per_pixel(1.0)
            margin_px1 = w.project.margin_mm / mpp1
            cw1 = (box.width + 2.0 * margin_px1) * mpp1
            ch1 = (box.height + 2.0 * margin_px1) * mpp1
            cols, rows = p["grid"]
            scale = tiling.fit_scale(cw1, ch1, p["page"], p["landscape"],
                                     p["margin_mm"], p["overlap_mm"],
                                     cols, rows)
        return p, w.effective_mm_per_pixel(scale)

    def _build_tiles(self, title):
        """Build the tile SVGs from the current settings, or None."""
        w = self._w
        pre = self._resolved_tile_params(title)
        if pre is None:
            return None
        p, mpp = pre
        base_name = os.path.splitext(
            os.path.basename(w._loaded.path or "tile"))[0]
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                return tiling.build_tiles(
                    w.project,
                    image_bgr=w._loaded.data if p["embed"] else None,
                    page=p["page"], landscape=p["landscape"],
                    margin_mm=p["margin_mm"], overlap_mm=p["overlap_mm"],
                    embed_photo=p["embed"], filled=p["filled"],
                    base_name=base_name, mm_per_pixel=mpp,
                    region_px=self._tile_region())
            finally:
                QApplication.restoreOverrideCursor()
        except (svg_export.ExportError, ValueError) as exc:
            QMessageBox.warning(w, title, str(exc))
            return None
        except Exception as exc:
            QMessageBox.critical(w, title, "Tiling failed: %s" % (exc,))
            return None

    def export_tiles(self):
        w = self._w
        tiles = None
        out_dir = None
        if w._loaded is None:
            return
        out_dir = QFileDialog.getExistingDirectory(
            w, "Choose a folder for the tile SVGs", os.getcwd())
        if not out_dir:
            return
        tiles = self._build_tiles("Export Print Tiles")
        if tiles is None:
            return
        try:
            for name, svg in tiles:
                with open(os.path.join(out_dir, name), "w",
                          encoding="utf-8") as fh:
                    fh.write(svg)
        except OSError as exc:
            QMessageBox.critical(w, "Export Print Tiles", str(exc))
            return
        QMessageBox.information(
            w, "Export Print Tiles",
            "Wrote %d tile(s) to:\n%s" % (len(tiles), out_dir))
        w.statusBar().showMessage(
            "Wrote %d tile(s) to %s" % (len(tiles), out_dir), 6000)

    def _tile_page_mm(self):
        """The tiling page size in mm, orientation applied."""
        p = self._w.tiling_panel.params()
        pw_mm, ph_mm = tiling.PAGE_SIZES_MM[p["page"]]
        if p["landscape"]:
            pw_mm, ph_mm = ph_mm, pw_mm
        return pw_mm, ph_mm

    def _make_printer(self):
        """A QPrinter preset to the tiling page size AND orientation (so
        the print dialog opens matching the tile layout)."""
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter
        p = self._w.tiling_panel.params()
        pw_mm, ph_mm = tiling.PAGE_SIZES_MM[p["page"]]   # portrait base
        printer = QPrinter(QPrinter.HighResolution)
        layout = QPageLayout(
            QPageSize(QSizeF(pw_mm, ph_mm), QPageSize.Millimeter),
            (QPageLayout.Landscape if p["landscape"]
             else QPageLayout.Portrait),
            QMarginsF(0, 0, 0, 0))
        printer.setPageLayout(layout)
        printer.setFullPage(True)
        return printer

    def _make_renderers(self, tiles):
        """Pre-parse each tile SVG into a QSvgRenderer ONCE (under a wait
        cursor). The preview widget repaints pages repeatedly - parsing
        the SVGs on every paint froze the UI on photo-embedding jobs."""
        from PySide6.QtSvg import QSvgRenderer
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return [QSvgRenderer(bytearray(svg, "utf-8"))
                    for _name, svg in tiles]
        finally:
            QApplication.restoreOverrideCursor()

    def _paint_tiles(self, printer, renderers, pages=None):
        """Paint pre-parsed tile renderers onto the printer at TRUE mm
        scale. `pages` is an optional list of 0-based page indices (print
        range support); None paints all pages.

        The device-pixel-per-mm factor comes from the printer's ACTUAL
        paper, so a mismatched paper size or orientation can only clip
        the tile - never stretch it (the 'ovals' failure mode)."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPageLayout, QPainter
        from PySide6.QtPrintSupport import QPrinter

        if pages is None:
            chosen = list(renderers)
        else:
            chosen = [renderers[i] for i in pages
                      if 0 <= i < len(renderers)]
        if not chosen:
            return False
        pw_mm, ph_mm = self._tile_page_mm()
        painter = QPainter()
        if not painter.begin(printer):
            return False
        try:
            for i, renderer in enumerate(chosen):
                if i:
                    printer.newPage()
                paper_mm = printer.pageLayout().fullRect(
                    QPageLayout.Millimeter)
                dev = printer.pageRect(QPrinter.DevicePixel)
                sx = dev.width() / max(1e-6, paper_mm.width())
                sy = dev.height() / max(1e-6, paper_mm.height())
                target = QRectF(0.0, 0.0, pw_mm * sx, ph_mm * sy)
                renderer.render(painter, target)
        finally:
            painter.end()
        return True

    def _check_paper_match(self, printer, title):
        """Warn when the chosen paper/orientation does not match the tile
        layout. Returns False if the user cancels."""
        from PySide6.QtGui import QPageLayout
        pw_mm, ph_mm = self._tile_page_mm()
        paper = printer.pageLayout().fullRect(QPageLayout.Millimeter)
        if (abs(paper.width() - pw_mm) <= 2.0
                and abs(paper.height() - ph_mm) <= 2.0):
            return True
        resp = QMessageBox.warning(
            self._w, title,
            "The selected paper is %.0f x %.0f mm but the tiles were "
            "laid out for %.0f x %.0f mm.\n\nTiles will still print at "
            "TRUE scale (never stretched) but may be clipped. Match the "
            "page size/orientation in the tiling panel or the print "
            "dialog for best results." % (paper.width(), paper.height(),
                                          pw_mm, ph_mm),
            QMessageBox.Ok | QMessageBox.Cancel)
        return resp == QMessageBox.Ok

    def print_tiles(self):
        """Preview-first printing at true 1:1 (see _TilePreviewDialog)."""
        self._open_tile_preview("Print Tiles")

    def print_preview_tiles(self):
        """Same dialog as Print Tiles - one preview to learn."""
        self._open_tile_preview("Print Preview")

    def _open_tile_preview(self, title):
        w = self._w
        tiles = self._build_tiles(title)
        if not tiles:
            return
        printer = self._make_printer()
        renderers = self._make_renderers(tiles)
        dlg = _TilePreviewDialog(self, printer, renderers, w)
        dlg.setWindowTitle("%s - %d page(s)" % (title, len(renderers)))
        dlg.exec()
        if dlg.printed:
            w.statusBar().showMessage(
                "Sent tile page(s) to the printer at true scale. Make "
                "sure driver scaling / 'fit to page' is OFF.", 8000)


class _TilePreviewDialog(QDialog):
    """Shared tile preview/print dialog (used by both Print Tiles and
    Print Preview, so they look and work the same).

    Layout modes: Single page / Facing / All pages (overview). The mouse
    wheel ZOOMS (anchored at the cursor, like the dewarp panes and the
    trace canvas); Alt+wheel steps through the sheets; left-drag pans.
    Print... opens the printer dialog with All / Current page / Custom
    range, then prints through the true-mm paint path."""

    def __init__(self, controller, printer, renderers, parent=None):
        super().__init__(parent)
        from PySide6.QtPrintSupport import QPrintPreviewWidget
        from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton,
                                       QVBoxLayout)
        self._c = controller
        self._printer = printer
        self._renderers = renderers
        self._n = len(renderers)
        self.printed = False
        self.resize(1000, 720)

        lay = QVBoxLayout(self)
        self._preview = QPrintPreviewWidget(printer, self)
        self._preview.setViewMode(QPrintPreviewWidget.SinglePageView)
        self._preview.paintRequested.connect(
            lambda pr: self._c._paint_tiles(pr, self._renderers))
        lay.addWidget(self._preview, 1)
        self._tune_inner_view()

        row = QHBoxLayout()
        # layout modes (radio-style), like QPrintPreviewDialog's toolbar
        self._mode_btns = []
        for label, mode in (
                ("Single page", QPrintPreviewWidget.SinglePageView),
                ("Facing", QPrintPreviewWidget.FacingPagesView),
                ("All pages", QPrintPreviewWidget.AllPagesView)):
            b = QPushButton(label, self)
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.clicked.connect(
                lambda _=False, m=mode, btn=b: self._set_mode(m, btn))
            row.addWidget(b)
            self._mode_btns.append(b)
        self._mode_btns[0].setChecked(True)
        row.addSpacing(16)

        self._btn_prev = QPushButton("< Prev", self)
        self._btn_prev.setAutoDefault(False)
        self._btn_next = QPushButton("Next >", self)
        self._btn_next.setAutoDefault(False)
        self._page_lbl = QLabel("", self)
        self._btn_prev.clicked.connect(lambda: self._step_page(-1))
        self._btn_next.clicked.connect(lambda: self._step_page(+1))
        self._preview.previewChanged.connect(self._update_nav)
        row.addWidget(self._btn_prev)
        row.addWidget(self._page_lbl)
        row.addWidget(self._btn_next)
        row.addSpacing(16)
        for label, slot in (("Fit page", self._preview.fitInView),
                            ("Fit width", self._preview.fitToWidth)):
            b = QPushButton(label, self)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        btn_print = QPushButton("Print…", self)
        btn_print.setDefault(True)
        btn_print.clicked.connect(self._do_print)
        btn_close = QPushButton("Close", self)
        btn_close.setAutoDefault(False)
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_print)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self._update_nav()

    # ----- inner-view behavior ---------------------------------------------
    def _tune_inner_view(self):
        """Make the preview feel like the app's other views: left-drag
        pans; zooming anchors at the cursor. QPrintPreviewWidget hosts an
        internal QGraphicsView we can reach as a child."""
        from PySide6.QtWidgets import QGraphicsView
        gv = self._preview.findChild(QGraphicsView)
        self._gv = gv
        if gv is not None:
            gv.setDragMode(QGraphicsView.ScrollHandDrag)
            gv.setTransformationAnchor(
                QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            gv.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Wheel:
            delta = event.angleDelta().y() or event.angleDelta().x()
            if event.modifiers() & Qt.AltModifier:
                # Alt+wheel steps through the sheets
                self._step_page(-1 if delta > 0 else +1)
            elif delta > 0:
                self._preview.zoomIn(1.25)
            elif delta < 0:
                self._preview.zoomOut(1.25)
            return True    # the wheel never scrolls the page list
        return super().eventFilter(obj, event)

    # ----- controls --------------------------------------------------------
    def _set_mode(self, mode, active_btn):
        self._preview.setViewMode(mode)
        for b in self._mode_btns:
            b.setChecked(b is active_btn)
        self._update_nav()

    def _step_page(self, d):
        cur = self._preview.currentPage()
        self._preview.setCurrentPage(max(1, min(self._n, cur + d)))
        self._update_nav()

    def _update_nav(self, *args):
        self._page_lbl.setText(
            "Sheet %d of %d" % (self._preview.currentPage(), self._n))
        self._btn_prev.setEnabled(self._preview.currentPage() > 1)
        self._btn_next.setEnabled(self._preview.currentPage() < self._n)

    # ----- printing --------------------------------------------------------
    def _do_print(self):
        from PySide6.QtPrintSupport import (QAbstractPrintDialog,
                                            QPrintDialog, QPrinter)
        printer = self._printer
        printer.setFromTo(1, self._n)
        pd = QPrintDialog(printer, self)
        pd.setWindowTitle("Print Tiles")
        pd.setOption(QAbstractPrintDialog.PrintPageRange, True)
        pd.setOption(QAbstractPrintDialog.PrintCurrentPage, True)
        if pd.exec() != QDialog.Accepted:
            self._preview.updatePreview()   # reflect settings changes
            return
        if not self._c._check_paper_match(printer, "Print Tiles"):
            self._preview.updatePreview()
            return
        rng = printer.printRange()
        if rng == QPrinter.CurrentPage:
            pages = [self._preview.currentPage() - 1]
        elif rng == QPrinter.PageRange:
            lo = printer.fromPage() or 1
            hi = printer.toPage() or self._n
            pages = list(range(lo - 1, min(hi, self._n)))
        else:
            pages = None
        if not self._c._paint_tiles(printer, self._renderers, pages):
            QMessageBox.critical(self, "Print Tiles",
                                 "Could not start the print job.")
            return
        self.printed = True
        self.accept()
