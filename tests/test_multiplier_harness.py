import unittest

from chiprd.multiplier_harness import _vectors


class MultiplierVectorTests(unittest.TestCase):
    def test_corner_cases_and_seeded_random_vectors(self):
        first = _vectors(1234, 64)
        second = _vectors(1234, 64)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 72 + 64)
        self.assertIn((-32768, -32768), first)
        self.assertIn((-32768, 32767), first)
        self.assertIn((32767, 32767), first)
        for a, b in first:
            result = a * b
            self.assertGreaterEqual(result, -(2**31))
            self.assertLessEqual(result, 2**31 - 1)

    def test_rejects_invalid_seed_and_vector_budget(self):
        for seed in (-1, 2**32, True):
            with self.assertRaises(ValueError):
                _vectors(seed, 10)
        for count in (0, 100001, True):
            with self.assertRaises(ValueError):
                _vectors(1, count)


if __name__ == "__main__":
    unittest.main()
