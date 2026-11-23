"""Offline tests for source-bound CORDIC macrostep lemma construction."""
import unittest

from chiprd.cordic_macrostep_proof import _check_sources, _lemma_smt
from chiprd.harness import ROOT


class CORDICMacrostepLemmaTests(unittest.TestCase):
    def setUp(self):
        self.golden = (ROOT / "tasks/cordic/cordic_rotator.sv").read_text()

    def test_supported_generated_candidates_match_source_contract(self):
        for factor in (2, 4):
            candidate = (ROOT / f"tasks/cordic/generated/cordic_iter{factor}.sv").read_text()
            _check_sources(self.golden, candidate, factor)

    def test_mutated_step_rejected_before_proof(self):
        candidate = (ROOT / "tasks/cordic/generated/cordic_iter2.sv").read_text()
        candidate = candidate.replace("gx_y_0 = (qz >= 0) ? (qy + gx_x_shift_0)",
                                      "gx_y_0 = (qz >= 0) ? (qy - gx_x_shift_0)")
        with self.assertRaisesRegex(ValueError, "macrostep"):
            _check_sources(self.golden, candidate, 2)

    def test_modified_golden_rejected(self):
        with self.assertRaisesRegex(ValueError, "Golden microstep"):
            _check_sources(self.golden.replace("x - y_shift", "x + y_shift"),
                           (ROOT / "tasks/cordic/generated/cordic_iter2.sv").read_text(), 2)

    def test_smt_is_full_width_and_asks_for_counterexample(self):
        for factor in (2, 4):
            for start in range(0, 16, factor):
                query = _lemma_smt(factor, start)
                self.assertIn("(set-logic QF_BV)", query)
                self.assertIn("(_ BitVec 32)", query)
                self.assertIn("(bvashr", query)
                self.assertIn("(check-sat)", query)
                self.assertIn("(assert (or (not (=", query)

    def test_reject_unsupported_factor(self):
        with self.assertRaisesRegex(ValueError, "Only 2x"):
            _check_sources(self.golden, "", 3)


if __name__ == "__main__":
    unittest.main()
