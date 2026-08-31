"""Colour calibration: mapping a raw sensor reading onto 0-255.

Two schemes, tried in order:

- A 3x3 colour-correction matrix, built from raw readings of red, green and
  blue reference surfaces plus a black point. This is the only one of the
  two that can fix crosstalk - a sensor channel picking up some of its
  neighbours' light, e.g. green bleeding into blue and making green surfaces
  read as cyan. A per-channel scale cannot fix that: crosstalk mixes the
  channels together, so undoing it means solving for how much of each raw
  channel came from each real channel, which is exactly what the matrix
  inversion below does.
- A per-channel white/black scale, kept for boards that have only run the
  older two-point calibration. It fixes gain and dark offset but leaves
  crosstalk uncorrected.

No hardware imports, so it runs on CPython too.
"""

DEFAULT = {
    "white": [160.14, 87.63174, 62.94521],
    "black": [0.0, 0.0, 0.0],
    "red": None,
    "green": None,
    "blue": None,
}


def normalise(value):
    """Accept older firmware's calibration too: a flat [r, g, b], which was
    a white point only, with no black point ever recorded."""
    if isinstance(value, list):
        return dict(DEFAULT, white=value, black=list(DEFAULT["black"]))
    if isinstance(value, dict) and "white" in value and "black" in value:
        return dict(
            DEFAULT,
            white=value["white"],
            black=value["black"],
            red=value.get("red"),
            green=value.get("green"),
            blue=value.get("blue"),
        )
    return {
        name: (list(value) if isinstance(value, list) else value)
        for name, value in DEFAULT.items()
    }


def scale(rgb, calibration):
    """Map a raw (r, g, b) reading onto 0-255, clamping out-of-range results
    instead of overflowing."""
    matrix = _correction_matrix(calibration)
    if matrix is not None:
        black = calibration["black"]
        corrected = _apply_matrix(matrix, [v - b for v, b in zip(rgb, black)])
    else:
        corrected = _scale_diagonal(rgb, calibration)
    return tuple(min(255, max(0, value)) for value in corrected)


def _scale_diagonal(rgb, calibration):
    """Per-channel linear scale: black maps to 0, white maps to 255. A white
    point alone only fixes per-channel gain; it cannot correct the sensor's
    dark offset, which is why dark colours read as a washed-out grey without
    a black point too - and it cannot correct crosstalk at all, which is
    what the correction matrix above is for.
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
        calibrated.append(scaled)

    return calibrated


def _correction_matrix(calibration):
    """The 3x3 matrix M such that M . (raw - black) reproduces the reference
    (255, 0, 0), (0, 255, 0) and (0, 0, 255) targets for the red, green and
    blue reference readings - or None if any reference point is missing or
    the three references are too close to collinear to invert.
    """
    black = calibration["black"]
    references = [calibration[name] for name in ("red", "green", "blue")]
    if any(reference is None for reference in references):
        return None

    # Columns are the black-subtracted red/green/blue reference readings.
    raw = [
        [reference[row] - black[row] for reference in references]
        for row in range(3)
    ]
    inverse = _invert3x3(raw)
    if inverse is None:
        return None
    return [[255 * value for value in row] for row in inverse]


def _apply_matrix(matrix, vector):
    return [sum(m * v for m, v in zip(row, vector)) for row in matrix]


def _determinant3x3(m):
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _invert3x3(m):
    det = _determinant3x3(m)
    # Near-zero means the three reference readings are (near) collinear -
    # e.g. two calibration steps were run on the same surface by mistake -
    # and cannot be inverted into a correction matrix.
    if abs(det) < 1e-9:
        return None

    return [
        [
            (m[1][1] * m[2][2] - m[1][2] * m[2][1]) / det,
            (m[0][2] * m[2][1] - m[0][1] * m[2][2]) / det,
            (m[0][1] * m[1][2] - m[0][2] * m[1][1]) / det,
        ],
        [
            (m[1][2] * m[2][0] - m[1][0] * m[2][2]) / det,
            (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det,
            (m[0][2] * m[1][0] - m[0][0] * m[1][2]) / det,
        ],
        [
            (m[1][0] * m[2][1] - m[1][1] * m[2][0]) / det,
            (m[0][1] * m[2][0] - m[0][0] * m[2][1]) / det,
            (m[0][0] * m[1][1] - m[0][1] * m[1][0]) / det,
        ],
    ]
