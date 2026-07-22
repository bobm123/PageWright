"""Image loading and basic metadata.

Uses OpenCV (cv2) so the same calls port directly to the C++ API.
"""

import os

import cv2


class LoadedImage:
    """An image loaded from disk together with its pixel dimensions.

    `data` is a BGR `numpy.ndarray` (OpenCV's native order). DPI is optional and
    only used as a hint; real-world scale comes from calibration, not DPI.
    """

    def __init__(self, path, data, dpi=None):
        self.path = path
        self.data = data
        self.dpi = dpi

    @property
    def pixel_width(self):
        return int(self.data.shape[1])

    @property
    def pixel_height(self):
        return int(self.data.shape[0])

    def __repr__(self):
        return "LoadedImage(%r, %dx%d)" % (
            os.path.basename(self.path), self.pixel_width, self.pixel_height)


def load_image(path):
    """Load an image from `path`. Raises IOError if it cannot be read."""
    # IMREAD_COLOR drops any alpha channel and gives a consistent 3-channel BGR
    # image, which is what the segmentation engines expect downstream.
    data = cv2.imread(path, cv2.IMREAD_COLOR)
    if data is None:
        raise IOError("could not read image: %s" % (path,))
    return LoadedImage(path=path, data=data)
