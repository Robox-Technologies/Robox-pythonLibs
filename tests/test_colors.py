"""Offline tests for src/colors.py. No hardware required.

Run with: ./tools/run-tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import colors  # noqa: E402


class TestClosestColorName(unittest.TestCase):
    def test_exact_matches_return_their_own_name(self):
        for name, rgb in colors.STANDARD_COLORS.items():
            self.assertEqual(colors.closest_color_name(rgb), name)

    def test_a_near_miss_still_finds_the_right_neighbour(self):
        self.assertEqual(colors.closest_color_name((250, 5, 5)), "red")
        self.assertEqual(colors.closest_color_name((5, 5, 5)), "black")
        self.assertEqual(colors.closest_color_name((250, 250, 250)), "white")

    def test_a_dimmer_reading_of_the_same_colour_still_matches(self):
        """The reason matching is by chromaticity, not raw RGB distance.

        Halving every channel halves raw RGB distance to every reference
        point roughly in proportion, so it does not by itself prove
        brightness-independence; the real test is that a reading with the
        same ratios but far lower total still names correctly.
        """
        self.assertEqual(colors.closest_color_name((100, 0, 0)), "red")
        self.assertEqual(colors.closest_color_name((40, 26, 0)), "orange")

    def test_a_calibrated_point_overrides_the_default_for_its_colour_only(self):
        # A "red" swatch that this sensor actually sees as (100, 50, 10):
        # nearer STANDARD_COLORS' orange by default, so the override has to
        # be what flips the match.
        palette = {"red": (100, 50, 10)}
        self.assertEqual(colors.closest_color_name((100, 50, 10), palette), "red")
        self.assertEqual(colors.closest_color_name((100, 50, 10)), "orange")

        # Every other colour still falls back to the default, unaffected.
        self.assertEqual(colors.closest_color_name((0, 0, 255), palette), "blue")

    def test_a_bright_but_colourless_reading_is_not_forced_into_a_hue(self):
        # Equal channels, well above the dark floor: genuinely grey/white,
        # not a stray near-black misclassified as a saturated colour.
        self.assertEqual(colors.closest_color_name((40, 40, 40)), "white")


if __name__ == "__main__":
    unittest.main()
