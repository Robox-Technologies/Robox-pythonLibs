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
            {
                "white": [10, 20, 30],
                "black": [0.0, 0.0, 0.0],
                "red": None,
                "green": None,
                "blue": None,
            },
        )

    def test_a_white_and_black_dict_passes_through_without_red_green_blue(self):
        value = {"white": [200, 210, 220], "black": [5, 6, 7]}
        self.assertEqual(
            c.normalise(value),
            dict(value, red=None, green=None, blue=None),
        )

    def test_a_full_dict_passes_through_including_red_green_blue(self):
        value = {
            "white": [200, 210, 220],
            "black": [5, 6, 7],
            "red": [190, 20, 15],
            "green": [30, 200, 25],
            "blue": [15, 20, 210],
        }
        self.assertEqual(c.normalise(value), value)

    def test_anything_else_falls_back_to_the_default(self):
        self.assertEqual(c.normalise(None), c.DEFAULT)
        self.assertEqual(c.normalise("garbage"), c.DEFAULT)


class TestDiagonalScale(unittest.TestCase):
    """Covers the white/black-only fallback used when a board hasn't run
    the red/green/blue calibration yet."""

    def calibration(self, white=(200, 200, 200), black=(0, 0, 0)):
        return {
            "white": list(white),
            "black": list(black),
            "red": None,
            "green": None,
            "blue": None,
        }

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


class TestMatrixScale(unittest.TestCase):
    """Covers the red/green/blue correction matrix, which is what fixes
    crosstalk between channels - a per-channel scale cannot."""

    def calibration(self, red, green, blue, black=(0, 0, 0), white=(1, 1, 1)):
        return {
            "white": list(white),
            "black": list(black),
            "red": list(red),
            "green": list(green),
            "blue": list(blue),
        }

    def assertTupleAlmostEqual(self, actual, expected):
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want)

    def test_reference_readings_reproduce_their_own_targets(self):
        # A sensor with real crosstalk: each reference surface's raw
        # reading has noticeable signal in the "wrong" channels.
        cal = self.calibration(
            red=(300, 20, 40), green=(30, 280, 90), blue=(15, 60, 260)
        )
        self.assertTupleAlmostEqual(c.scale((300, 20, 40), cal), (255, 0, 0))
        self.assertTupleAlmostEqual(c.scale((30, 280, 90), cal), (0, 255, 0))
        self.assertTupleAlmostEqual(c.scale((15, 60, 260), cal), (0, 0, 255))

    def test_green_crosstalk_into_blue_no_longer_reads_as_cyan(self):
        """The reported bug: a diagonal white/black scale amplifies blue
        crosstalk instead of removing it, so a green surface reads as
        cyan. The correction matrix is built from the actual crosstalk
        seen in the green reference reading, so it cancels it out."""
        cal = self.calibration(
            red=(300, 20, 40), green=(30, 280, 90), blue=(15, 60, 260)
        )
        r, g, b = c.scale((30, 280, 90), cal)
        self.assertGreater(g, b + 100)

    def test_black_point_is_subtracted_before_correction(self):
        cal = self.calibration(
            red=(320, 40, 60),
            green=(50, 300, 110),
            blue=(35, 80, 280),
            black=(20, 20, 20),
        )
        self.assertTupleAlmostEqual(c.scale((20, 20, 20), cal), (0, 0, 0))
        self.assertTupleAlmostEqual(c.scale((320, 40, 60), cal), (255, 0, 0))

    def test_missing_any_reference_point_falls_back_to_diagonal_scale(self):
        cal = {
            "white": [200, 200, 200],
            "black": [0, 0, 0],
            "red": [300, 20, 40],
            "green": [30, 280, 90],
            "blue": None,
        }
        self.assertEqual(c.scale((200, 200, 200), cal), (255, 255, 255))

    def test_collinear_references_fall_back_to_diagonal_scale_not_a_crash(self):
        # Red and green calibrated on the same surface by mistake: the
        # matrix is singular and cannot be inverted.
        cal = self.calibration(
            red=(300, 20, 40), green=(300, 20, 40), blue=(15, 60, 260),
            white=(300, 300, 300),
        )
        self.assertEqual(c.scale((300, 300, 300), cal), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
