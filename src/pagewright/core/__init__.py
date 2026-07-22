"""Core, GUI-free logic: image I/O, calibration, geometry, segmentation,
contours, SVG export and tiling.

These modules are kept deliberately free of Python-only idioms so they can be
ported to C/C++ with little more than syntax changes (see PLAN.md §2).
"""
