"""Naming an RGB reading the way a person would. No hardware imports, so it
runs on CPython too.
"""

# Hue of each chromatic name, in degrees around the standard colour wheel.
# Black, white and grey aren't hues - a reading only gets one of these names
# once it fails the brightness/saturation checks below.
HUES = {
    "red": 0,
    "orange": 30,
    "yellow": 60,
    "green": 120,
    "blue": 240,
    "purple": 285,
}

# A reading this dark or this washed-out reads as black/white/grey to a
# person no matter its hue: near black, the hue is dominated by sensor
# noise rather than the surface's actual colour, and near white the surface
# is reflecting all wavelengths roughly evenly.
_BLACK_MAX_VALUE = 0.15
_WHITE_MIN_VALUE = 0.85
_GRAY_MAX_SATURATION = 0.15


def _rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    hi, lo = max(r, g, b), min(r, g, b)
    delta = hi - lo

    if delta == 0:
        h = 0
    elif hi == r:
        h = (60 * ((g - b) / delta)) % 360
    elif hi == g:
        h = (60 * ((b - r) / delta)) + 120
    else:
        h = (60 * ((r - g) / delta)) + 240

    s = 0 if hi == 0 else delta / hi
    v = hi
    return h, s, v


def _hue_distance(a, b):
    """The shorter way around the colour wheel between two hues."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def closest_color_name(rgb):
    """The colour name a person would give an (r, g, b) reading.

    Black, white and grey are judged by brightness and saturation rather
    than distance to a reference RGB triple: a dim, saturated red is still
    "red" to a person, even though (0, 0, 0) is the nearer point in RGB
    space. Everything else is named after whichever hue on the colour
    wheel it sits closest to.
    """
    h, s, v = _rgb_to_hsv(*rgb)

    if v <= _BLACK_MAX_VALUE:
        return "black"
    if s <= _GRAY_MAX_SATURATION:
        return "white" if v >= _WHITE_MIN_VALUE else "gray"

    return min(HUES, key=lambda name: _hue_distance(h, HUES[name]))
