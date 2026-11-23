"""Generation and adversarial checks for RTL-derived macrostep miters."""
import unittest

from chiprd.cordic_rtl_macrostep import make_miter
from chiprd.harness import ROOT


class RTLMacrostepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = (ROOT / "tasks/cordic/cordic_rotator.sv").read_text()

    def candidate(self, factor):
        return (ROOT / f"tasks/cordic/generated/cordic_iter{factor}.sv").read_text()

    def test_all_boundaries_emit_two_distinct_modules(self):
        for factor in (2, 4):
            for start in range(0, 16, factor):
                miter = make_miter(self.golden, self.candidate(factor), factor, start)
                self.assertIn("module gold_macro", miter)
                self.assertIn("module gate_macro", miter)
                self.assertIn("module macrostep_miter", miter)
                self.assertIn("assign mismatch =", miter)
                self.assertIn(f"5'd{start}", miter)
                self.assertIn(f"4'd{start}", miter)
                self.assertIn(f"gx_z_{factor - 1}", miter)
                self.assertNotIn("iteration_base", miter)

    def test_mutated_candidate_is_rejected(self):
        candidate = self.candidate(2).replace(
            "(qy + gx_x_shift_0)", "(qy - gx_x_shift_0)")
        with self.assertRaisesRegex(ValueError, "macrostep"):
            make_miter(self.golden, candidate, 2, 0)

    def test_mutated_golden_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Golden"):
            make_miter(self.golden.replace("x - y_shift", "x + y_shift"),
                       self.candidate(2), 2, 0)

    def test_reject_unsupported_boundary(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            make_miter(self.golden, self.candidate(4), 4, 2)


if __name__ == "__main__":
    unittest.main()
