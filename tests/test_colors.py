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


if __name__ == "__main__":
    unittest.main()
