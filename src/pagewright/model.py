"""Project data model: Project / TracedObject / Contour.

All geometry is stored in pixel coordinates and converted to millimetres only at
export time (see PLAN.md sec. 4). Kept simple and dependency-free so it ports to
C/C++ structs with little change.

Coordinate convention: origin top-left, y increases downward.
"""


def default_tiling():
    """Default tiled-printing settings (saved with the project)."""
    return {
        "page": "Letter",
        "landscape": False,
        "margin_mm": 6.0,
        "overlap_mm": 10.0,
        "overlap_unit": "mm",       # or "percent" (of the printable page)
        "fixed_grid": False,        # True: use grid_cols x grid_rows pages
        "grid_cols": 2,
        "grid_rows": 2,
        "scale_percent": 100,
        "embed_photo": False,
        "crop_photo": False,
        "filled": False,
    }


class PageEntry:
    """One page of a multi-page job (M1).

    `objects` holds PRE-SERIALIZED traced objects (the project_io
    schema), because pages other than the current one are never
    hydrated into TracedObject instances. `roi` is the page's Select
    Area as [x, y, w, h] in image pixels, or None."""

    def __init__(self, source_path="", objects=None, roi=None):
        self.source_path = source_path or ""
        self.objects = list(objects) if objects else []
        self.roi = list(roi) if roi is not None else None

    def to_dict(self):
        return {"source_path": self.source_path,
                "objects": list(self.objects),
                "roi": (list(self.roi) if self.roi is not None else None)}

    @classmethod
    def from_dict(cls, d):
        return cls(source_path=d.get("source_path"),
                   objects=d.get("objects"),
                   roi=d.get("roi"))


class Contour:
    """An ordered loop of pixel points.

    role: "outer" (object boundary) or "hole" (interior cutout).
    representation: "polyline" or "bezier".
    """

    def __init__(self, points=None, closed=True, role="outer",
                 representation="polyline", bezier_handles=None):
        self.points = list(points) if points else []   # [(x_px, y_px)]
        self.closed = closed
        self.role = role
        self.representation = representation
        self.bezier_handles = bezier_handles            # optional, per-point


class Style:
    """Stroke / fill settings for a traced object."""

    def __init__(self, stroke="#ff0000", stroke_width_mm=0.5,
                 fill="none"):
        self.stroke = stroke
        self.stroke_width_mm = stroke_width_mm
        self.fill = fill


class TracedObject:
    """A single traced object: one outer contour plus zero or more holes, or
    several disjoint loops grouped under one label."""

    def __init__(self, name="object", contours=None, style=None):
        self.name = name
        self.contours = list(contours) if contours else []
        self.style = style if style is not None else Style()


class Calibration:
    """Real-world scale state. `mm_per_pixel` is None until calibrated."""

    def __init__(self, mm_per_pixel=None, display_unit="mm"):
        self.mm_per_pixel = mm_per_pixel
        self.display_unit = display_unit

    @property
    def is_calibrated(self):
        return self.mm_per_pixel is not None and self.mm_per_pixel > 0.0


class Project:
    """Top-level container for a tracing session."""

    def __init__(self):
        self.source_image_path = None
        self.pixel_width = 0
        self.pixel_height = 0
        self.dpi = None
        self.calibration = Calibration()
        # Multi-page job (M1): [PageEntry]. The classic single-image
        # fields describe the CURRENT page.
        self.pages = []
        self.current_page = 0
        self.margin_mm = 5.0
        self.objects = []   # [TracedObject]
        self.tiling = default_tiling()   # tiled-printing settings

    def set_source_image(self, loaded_image):
        """Record the loaded image's path and pixel dimensions."""
        self.source_image_path = loaded_image.path
        self.pixel_width = loaded_image.pixel_width
        self.pixel_height = loaded_image.pixel_height
        self.dpi = loaded_image.dpi

    def real_size_mm(self):
        """(width_mm, height_mm) of the whole photo, or None if uncalibrated."""
        if not self.calibration.is_calibrated:
            return None
        mpp = self.calibration.mm_per_pixel
        return (self.pixel_width * mpp, self.pixel_height * mpp)
