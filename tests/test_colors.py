"""Offline tests for src/colors.py. No hardware required.

Run with: ./tools/run-tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import colors  # noqa: E402


class TestClosestColorName(unittest.TestCase):
    def test_a_saturated_reading_is_named_after_its_hue(self):
        self.assertEqual(colors.closest_color_name((255, 0, 0)), "red")
        self.assertEqual(colors.closest_color_name((255, 165, 0)), "orange")
        self.assertEqual(colors.closest_color_name((255, 255, 0)), "yellow")
        self.assertEqual(colors.closest_color_name((0, 200, 0)), "green")
        self.assertEqual(colors.closest_color_name((0, 0, 255)), "blue")
        self.assertEqual(colors.closest_color_name((160, 0, 200)), "purple")

    def test_a_near_miss_still_finds_the_right_neighbour(self):
        self.assertEqual(colors.closest_color_name((250, 5, 5)), "red")

    def test_very_dark_is_black_regardless_of_hue(self):
        self.assertEqual(colors.closest_color_name((5, 5, 5)), "black")
        self.assertEqual(colors.closest_color_name((30, 2, 2)), "black")

    def test_very_bright_and_unsaturated_is_white(self):
        self.assertEqual(colors.closest_color_name((250, 250, 250)), "white")
        self.assertEqual(colors.closest_color_name((240, 245, 235)), "white")

    def test_mid_brightness_and_unsaturated_is_gray_not_black_or_white(self):
        self.assertEqual(colors.closest_color_name((128, 128, 128)), "gray")

    def test_a_dim_saturated_colour_keeps_its_hue_instead_of_reading_black(self):
        """The bug this module exists to fix: RGB-distance classification
        put a dim, saturated red closer to black than to red, because the
        nearer reference point ignores how saturated the reading is."""
        self.assertEqual(colors.closest_color_name((100, 10, 10)), "red")

    def test_a_pale_saturated_colour_keeps_its_hue_instead_of_reading_white(self):
        self.assertEqual(colors.closest_color_name((255, 200, 200)), "red")


if __name__ == "__main__":
    unittest.main()
