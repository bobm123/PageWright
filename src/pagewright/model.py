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
        "embed_photo": True,
        "crop_photo": False,
        "filled": False,
    }


class PageEntry:
    """One page of a multi-page job (M1).

    `objects` holds PRE-SERIALIZED traced objects (the project_io
    schema), because pages other than the current one are never
    hydrated into TracedObject instances. `roi` is the page's Select
    Area as [x, y, w, h] in image pixels, or None."""

    def __init__(self, source_path="", objects=None, roi=None,
                 measurements=None):
        self.source_path = source_path or ""
        self.objects = list(objects) if objects else []
        self.roi = list(roi) if roi is not None else None
        # [[x0, y0, x1, y1], ...] image px; labels re-derive from the
        # calibration/units at load time
        self.measurements = list(measurements) if measurements else []

    def to_dict(self):
        return {"source_path": self.source_path,
                "objects": list(self.objects),
                "roi": (list(self.roi) if self.roi is not None else None),
                "measurements": [list(q) for q in self.measurements]}

    @classmethod
    def from_dict(cls, d):
        return cls(source_path=d.get("source_path"),
                   objects=d.get("objects"),
                   roi=d.get("roi"),
                   measurements=d.get("measurements"))


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


class Build:
    """How a traced outline becomes a solid part.

    A plan sheet is nested for material efficiency, not laid out like
    an assembly: on the F-86D sheet the ribs sit in a row and the
    stabilizer is turned 90 degrees. Where a part BELONGS is therefore
    not recoverable from where it was drawn, so it is recorded here
    rather than inferred.

    `thickness_mm` is the sheet stock the part is cut from.
    `plane` is the plane it stands on in the assembly: "XY", "YZ" or
    "XZ" (a fuselage former is YZ, a rib is XZ, sheet in the plan
    view is XY). `station_mm` is how far along the normal of that
    plane it sits, and `rotate_deg` turns it in its own plane.

    `assembly` names the major assembly the part belongs to -
    "Fuselage", "Wing", "LandingGear". Parts that share one become a
    component, which is what lets a whole assembly be swapped: taking
    the landing gear off and fitting floats means deleting one
    component and adding another, not picking struts out of a pile of
    loose bodies. A part with no `plane` is LEFT FLAT, lying in the
    plan plane as it was drawn, which is the right state for an
    assembly nobody has positioned yet.

    All fields are optional: an outline with no Build is still a
    perfectly good traced polygon for printing, which is what most
    jobs are.
    """

    PLANES = ("XY", "YZ", "XZ")

    def __init__(self, thickness_mm=None, plane=None, station_mm=None,
                 rotate_deg=None, assembly=None, sheet=None):
        self.thickness_mm = thickness_mm
        self.plane = plane
        self.station_mm = station_mm
        self.rotate_deg = rotate_deg
        self.assembly = assembly
        # Name of the Project.sheets entry this part is cut from. A
        # NAME, not the Sheet itself, so the project file stays flat
        # and editing a sheet needs no part to be touched.
        self.sheet = sheet

    def is_empty(self):
        """True when nothing has been set, so it need not be saved."""
        return (self.thickness_mm is None and self.plane is None
                and self.station_mm is None and self.rotate_deg is None
                and not self.assembly and not self.sheet)

    def copy(self):
        return Build(self.thickness_mm, self.plane, self.station_mm,
                     self.rotate_deg, self.assembly, self.sheet)

    def __repr__(self):
        return "Build(%r mm, %s, station=%r, rot=%r, asm=%r, sheet=%r)" % (
            self.thickness_mm, self.plane, self.station_mm,
            self.rotate_deg, self.assembly, self.sheet)


class TracedObject:
    """A single traced object: one outer contour plus zero or more holes, or
    several disjoint loops grouped under one label."""

    def __init__(self, name="object", contours=None, style=None,
                 build=None):
        self.name = name
        self.contours = list(contours) if contours else []
        self.style = style if style is not None else Style()
        self.build = build if build is not None else Build()


class Sheet:
    """A piece of stock several parts are cut from.

    Plans name their stock ("SHEET NO.2 1/16") and a model is built from
    a handful of sheets, not from N independent thicknesses - so the
    thickness belongs to the sheet and the parts point at it. Correcting
    a sheet then corrects every part on it, which is the whole reason
    this is not just a per-part number.
    """

    def __init__(self, name, thickness_mm=None):
        self.name = name
        self.thickness_mm = thickness_mm

    def copy(self):
        return Sheet(self.name, self.thickness_mm)

    def __repr__(self):
        return "Sheet(%r, %r)" % (self.name, self.thickness_mm)


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
        # Tile-mark styling override (None = app factory defaults).
        # Set from the CURRENT session style at save time only when it
        # differs from factory, so unchanged projects stay colorless.
        self.mark_color = None       # "#rrggbb" or None
        self.mark_opacity = None     # 0..1 diamond fill opacity, or None
        # Stock thickness for the whole sheet, as plans state it
        # ("SHEET NO.2 1/16"). A part's own Build.thickness_mm wins
        # where one is set; this is the default for the rest.
        self.sheet_thickness_mm = None
        # Named stock a part can be assigned to (Build.sheet holds the
        # name). Between the part's own thickness and the sheet-wide
        # default: most parts are 1/16, a couple come off a 1/8 offcut.
        self.sheets = []   # [Sheet]

    def sheet_named(self, name):
        """The Sheet called `name`, or None."""
        if not name:
            return None
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        return None

    def part_thickness_mm(self, obj):
        """The thickness to build `obj` at, or None if unknown.

        One place decides, so the panel, the CAD export and any
        future caller cannot disagree about which value wins. Most
        specific first: the part's own value, then its sheet's, then
        the sheet-wide default.
        """
        build = getattr(obj, "build", None)
        own = getattr(build, "thickness_mm", None)
        if own is not None:
            return own
        sheet = self.sheet_named(getattr(build, "sheet", None))
        if sheet is not None and sheet.thickness_mm is not None:
            return sheet.thickness_mm
        return self.sheet_thickness_mm

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
