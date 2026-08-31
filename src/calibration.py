"""Two-point colour calibration: mapping a raw sensor reading onto 0-255
using a white and a black reference. No hardware imports, so it runs on
CPython too.
"""

DEFAULT = {
    "white": [160.14, 87.63174, 62.94521],
    "black": [0.0, 0.0, 0.0],
}


def remove_infrared(rgb, clear):
    """Estimate and subtract the infrared common to all three channels.

    Per AMS application note DN40 ("Lux and CCT Calculations using ams
    Color Sensors"): the R, G and B photodiodes all pass some infrared,
    but the clear channel's filter passes less of it, so R+G+B
    overshooting C is the signature of that leakage, split evenly three
    ways since it affects each channel about equally. Needs no
    calibration data - it is computed fresh from every reading.
    """
    r, g, b = rgb
    ir = max(0, (r + g + b - clear) / 2)
    return tuple(max(0, value - ir) for value in rgb)


def normalise(value):
    """Accept older firmware's calibration too: a flat [r, g, b], which was
    a white point only, with no black point ever recorded."""
    if isinstance(value, list):
        return {"white": value, "black": list(DEFAULT["black"])}
    if isinstance(value, dict) and "white" in value and "black" in value:
        return {"white": value["white"], "black": value["black"]}
    return {name: list(values) for name, values in DEFAULT.items()}


def scale(rgb, calibration):
    """Map a raw (r, g, b) reading onto 0-255 using the white/black points.

    Per channel, linearly: black maps to 0, white maps to 255, and anything
    outside that range clamps rather than overflows. A white point alone
    only fixes per-channel gain; it cannot correct the sensor's dark offset,
    which is why dark colours read as a washed-out grey without a black
    point too.
    """
    white = calibration["white"]
    black = calibration["black"]

    calibrated = []
    for value, lo, hi in zip(rgb, black, white):
        span = hi - lo
        # A zero or negative span means white and black were calibrated the
        # same (or backwards), which cannot happen with real light. Treat it
        # as uncalibrated rather than dividing by zero.
        scaled = 0 if span <= 0 else (value - lo) / span * 255
        calibrated.append(min(255, max(0, scaled)))

    return tuple(calibrated)
