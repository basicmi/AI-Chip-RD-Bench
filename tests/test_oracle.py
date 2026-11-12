import math
import unittest

from chiprd.oracle import ANGLE_SCALE, MAX_ANGLE, SCALE, check, expected, vectors


class OracleTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(expected(0), (SCALE, 0))
        self.assertAlmostEqual(expected(MAX_ANGLE)[0], 0, delta=3)
        self.assertAlmostEqual(expected(MAX_ANGLE)[1], SCALE, delta=3)

    def test_determinism_and_range(self):
        self.assertEqual(vectors(7), vectors(7))
        self.assertNotEqual(vectors(7), vectors(8))
        self.assertEqual(len(set(vectors())), 24)
        self.assertTrue(all(-MAX_ANGLE <= a <= MAX_ANGLE for a in vectors()))

    def test_reject_incorrect_result(self):
        self.assertTrue(check(0, SCALE, 0))
        self.assertFalse(check(0, 0, SCALE))
        self.assertFalse(check(ANGLE_SCALE // 2, 0, 0))

    def test_invalid_angle(self):
        with self.assertRaises(ValueError):
            expected(MAX_ANGLE + 1)


if __name__ == "__main__":
    unittest.main()
