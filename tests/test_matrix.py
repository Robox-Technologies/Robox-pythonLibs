"""Offline tests for src/matrix.py. No hardware required.

Run with: ./tools/run-tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matrix as m  # noqa: E402


class TestTranspose(unittest.TestCase):
    def test_swaps_rows_and_columns(self):
        self.assertEqual(
            m.transpose([[1, 2, 3], [4, 5, 6]]),
            [[1, 4], [2, 5], [3, 6]],
        )


class TestMatmul(unittest.TestCase):
    def test_identity_leaves_a_matrix_unchanged(self):
        identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        a = [[1, 2, 3], [4, 5, 6]]
        self.assertEqual(m.matmul(a, identity), a)

    def test_matches_a_hand_computed_product(self):
        a = [[1, 2], [3, 4]]
        b = [[5, 6], [7, 8]]
        # [[1*5+2*7, 1*6+2*8], [3*5+4*7, 3*6+4*8]]
        self.assertEqual(m.matmul(a, b), [[19, 22], [43, 50]])


class TestSolve(unittest.TestCase):
    def test_recovers_x_in_a_simple_system(self):
        # x + y = 3, x - y = 1  =>  x=2, y=1
        a = [[1, 1], [1, -1]]
        b = [[3], [1]]
        self.assertEqual(m.solve(a, b), [[2], [1]])

    def test_solves_multiple_right_hand_sides_at_once(self):
        identity = [[1, 0], [0, 1]]
        b = [[5, 6], [7, 8]]
        self.assertEqual(m.solve(identity, b), b)

    def test_a_singular_matrix_returns_none_rather_than_dividing_by_zero(self):
        # Second row is a multiple of the first: no unique solution.
        a = [[1, 2], [2, 4]]
        b = [[1], [2]]
        self.assertIsNone(m.solve(a, b))

    def test_pivoting_avoids_a_zero_on_the_diagonal(self):
        # Without a row swap, elimination would divide by the 0 at [0][0].
        a = [[0, 1], [1, 0]]
        b = [[2], [3]]
        self.assertEqual(m.solve(a, b), [[3], [2]])


class TestFit(unittest.TestCase):
    def test_fewer_than_three_points_is_not_enough(self):
        points = [((1, 0, 0), (1, 0, 0)), ((0, 1, 0), (0, 1, 0))]
        self.assertIsNone(m.fit(points))

    def test_recovers_a_known_matrix_from_points_it_generated(self):
        """The reason this exists: given the swatches Ro/Box actually
        measures and the colours they are supposed to be, the fit has to
        find whatever transform explains that mapping."""
        known = [
            [1.2, 0.1, 0.0],
            [-0.1, 1.1, 0.05],
            [0.0, 0.05, 0.9],
        ]
        measured = [(50, 5, 5), (5, 50, 5), (5, 5, 50), (40, 40, 10)]
        points = [(x, m.apply(known, x)) for x in measured]

        fitted = m.fit(points)

        for row_known, row_fitted in zip(known, fitted):
            for expected, actual in zip(row_known, row_fitted):
                self.assertAlmostEqual(expected, actual, places=6)

    def test_collinear_points_are_not_independent_enough(self):
        # All three targets sit on the same line through the origin as their
        # measurements: infinitely many matrices fit, so none is chosen.
        points = [
            ((1, 0, 0), (2, 0, 0)),
            ((2, 0, 0), (4, 0, 0)),
            ((3, 0, 0), (6, 0, 0)),
        ]
        self.assertIsNone(m.fit(points))


class TestApply(unittest.TestCase):
    def test_identity_matrix_leaves_a_reading_unchanged(self):
        identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        self.assertEqual(m.apply(identity, (10, 20, 30)), (10, 20, 30))

    def test_off_diagonal_terms_mix_channels(self):
        # Half of red bleeds into green; blue is untouched.
        matrix = [[1, 0.5, 0], [0, 1, 0], [0, 0, 1]]
        self.assertEqual(m.apply(matrix, (10, 20, 30)), (10, 25, 30))


if __name__ == "__main__":
    unittest.main()
