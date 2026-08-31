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


def closest_color_name(rgb):
    """The standard colour nearest an (r, g, b) reading, by RGB distance."""
    r, g, b = rgb
    return min(
        STANDARD_COLORS,
        key=lambda name: sum(
            (channel - reference) ** 2
            for channel, reference in zip((r, g, b), STANDARD_COLORS[name])
        ),
    )
