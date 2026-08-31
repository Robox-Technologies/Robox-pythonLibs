"""Offline tests for src/calibration.py. No hardware required.

Run with: ./tools/run-tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calibration as c  # noqa: E402


class TestNormalise(unittest.TestCase):
    def test_a_flat_list_is_treated_as_a_white_only_point(self):
        self.assertEqual(
            c.normalise([10, 20, 30]),
            {"white": [10, 20, 30], "black": [0.0, 0.0, 0.0]},
        )

    def test_a_full_white_and_black_dict_passes_through(self):
        value = {"white": [200, 210, 220], "black": [5, 6, 7]}
        self.assertEqual(c.normalise(value), value)

    def test_anything_else_falls_back_to_the_default(self):
        self.assertEqual(c.normalise(None), c.DEFAULT)
        self.assertEqual(c.normalise("garbage"), c.DEFAULT)


class TestRemoveInfrared(unittest.TestCase):
    def test_no_excess_over_clear_removes_nothing(self):
        # R+G+B not exceeding C is what a sensor with no IR leakage looks
        # like: there is nothing to attribute to infrared.
        self.assertEqual(c.remove_infrared((50, 60, 40), 150), (50, 60, 40))
        self.assertEqual(c.remove_infrared((50, 60, 40), 200), (50, 60, 40))

    def test_excess_over_clear_is_split_evenly_and_subtracted(self):
        # R+G+B = 210, C = 150: an excess of 60, so an IR estimate of 30,
        # subtracted from every channel equally.
        self.assertEqual(c.remove_infrared((70, 80, 60), 150), (40, 50, 30))

    def test_a_channel_the_estimate_exceeds_clamps_to_zero_not_negative(self):
        # IR estimate is (10+100+100-50)/2 = 80, which is more red than
        # there is: red must clamp to 0, not go negative.
        self.assertEqual(c.remove_infrared((10, 100, 100), 50), (0, 20, 20))


class TestScale(unittest.TestCase):
    def calibration(self, white=(200, 200, 200), black=(0, 0, 0)):
        return {"white": list(white), "black": list(black)}

    def test_black_point_reads_as_zero(self):
        cal = self.calibration(black=(10, 20, 30))
        self.assertEqual(c.scale((10, 20, 30), cal), (0, 0, 0))

    def test_white_point_reads_as_255(self):
        cal = self.calibration(white=(150, 160, 170))
        self.assertEqual(c.scale((150, 160, 170), cal), (255, 255, 255))

    def test_midpoint_reads_as_half_scale(self):
        cal = self.calibration(white=(200, 200, 200), black=(0, 0, 0))
        self.assertEqual(c.scale((100, 100, 100), cal), (127.5, 127.5, 127.5))

    def test_readings_outside_the_calibrated_range_clamp_instead_of_overflow(self):
        cal = self.calibration(white=(100, 100, 100), black=(0, 0, 0))
        self.assertEqual(c.scale((150, -20, 50), cal), (255, 0, 127.5))

    def test_a_dark_reading_is_not_a_washed_out_grey(self):
        """The bug a black point exists to fix.

        A white-only calibration divides every channel by the same-ish
        white magnitude, so a dim surface's channel ratios stay close to
        each other even when its true colour is saturated - it reads as
        a grey smear. Subtracting a real black point first restores the
        separation between channels.
        """
        # A dim red surface: R well above the sensor's dark floor, G and B
        # only barely above it.
        cal = self.calibration(white=(200, 200, 200), black=(20, 20, 20))
        r, g, b = c.scale((100, 25, 25), cal)
        self.assertGreater(r, g + 50)
        self.assertGreater(r, b + 50)

    def test_zero_or_negative_span_is_treated_as_uncalibrated_not_a_crash(self):
        cal = self.calibration(white=(50, 50, 50), black=(50, 50, 50))
        self.assertEqual(c.scale((50, 50, 50), cal), (0, 0, 0))

        cal = self.calibration(white=(10, 10, 10), black=(50, 50, 50))
        self.assertEqual(c.scale((30, 30, 30), cal), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
