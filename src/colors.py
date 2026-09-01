"""Naming an RGB reading. No hardware imports, so it runs on CPython too."""

STANDARD_COLORS = {
    "red": (255, 0, 0),
    "orange": (255, 165, 0),
    "yellow": (255, 255, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "purple": (128, 0, 128),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
}

# Below this total, channel ratios are sensor noise rather than a hue: the
# sensor is looking at (near) nothing, which is what black means anyway.
_DARK_TOTAL = 30


def _chromaticity(rgb):
    """(r, g, b) rescaled so channel ratios survive but overall brightness
    does not: None if there is nothing to rescale.

    A reading taken further from a swatch than its calibration point has
    lower intensity but the same colour. Matching on raw RGB would tell
    them apart on distance alone; matching on chromaticity does not, which
    is what makes it robust to exactly how the sensor is held.
    """
    total = sum(rgb)
    if total <= 0:
        return None
    return tuple(channel / total for channel in rgb)


def closest_color_name(rgb, palette=None):
    """The palette colour nearest an (r, g, b) reading, by chromaticity.

    `palette` supplies a calibrated reference point per colour name, learned
    from this sensor and these actual swatches rather than assumed from an
    idealised sRGB value. Any name missing from it falls back to
    STANDARD_COLORS, so a partial calibration still helps the colours it
    does cover.

    Black has no defined hue, so it is settled by total brightness alone
    rather than competing on chromaticity, which would otherwise have to
    tell it apart from a merely dim version of every other colour.
    """
    palette = dict(STANDARD_COLORS, **(palette or {}))

    total = sum(rgb)
    if total <= _DARK_TOTAL:
        return "black"

    # total is checked above, so this is never None like the palette side
    # below can be.
    reading = tuple(channel / total for channel in rgb)
    best_name, best_distance = None, None
    for name, reference_rgb in palette.items():
        reference = _chromaticity(reference_rgb)
        if reference is None:
            continue
        distance = sum((a - b) ** 2 for a, b in zip(reading, reference))
        if best_distance is None or distance < best_distance:
            best_name, best_distance = name, distance

    return best_name
