import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.equivalence import (
    DEFAULT_REFACTOR,
    DEFAULT_REFERENCE,
    classify_eqy,
    make_eqy_config,
    run_equivalence,
    run_equivalence_selftest,
)


class EquivalenceTests(unittest.TestCase):
    def test_eqy_config_uses_gold_gate_and_pdr(self):
        config = make_eqy_config()
        self.assertIn("[gold]", config)
        self.assertIn("read_verilog -sv gold.sv", config)
        self.assertIn("[gate]", config)
        self.assertIn("read_verilog -sv gate.sv", config)
        self.assertIn("engine abc pdr", config)

    def test_eqy_classification_is_fail_closed(self):
        self.assertEqual(classify_eqy(0, "DONE (PASS, rc=0)"), ("pass", "equivalent"))
        self.assertEqual(classify_eqy(2, "DONE (FAIL, rc=2)"), ("fail", "not_equivalent"))
        self.assertEqual(classify_eqy(1, "Status: unknown"), ("inconclusive", "inconclusive"))
        self.assertEqual(classify_eqy(0, "process exited 0"), ("error", "error"))

    def test_missing_eqy_fails_closed_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.equivalence.shutil.which", return_value=None):
                report = run_equivalence(
                    DEFAULT_REFERENCE, DEFAULT_REFACTOR, Path(directory) / "run"
                )
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["proof_status"], "error")
            self.assertIn("NOT run", report["error"])
            saved = json.loads(
                (Path(directory) / "run/equivalence_report.json").read_text()
            )
            self.assertEqual(saved["proof_status"], "error")

    def test_selftest_requires_positive_and_negative_outcomes(self):
        outcomes = [
            {"status": "pass", "proof_status": "equivalent"},
            {"status": "fail", "proof_status": "not_equivalent"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.equivalence.run_equivalence", side_effect=outcomes):
                report = run_equivalence_selftest(
                    DEFAULT_REFERENCE, DEFAULT_REFACTOR, Path(directory) / "selftest"
                )
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["negative_control"]["outcome"], "detected")

    def test_mutant_survival_fails_selftest(self):
        outcomes = [
            {"status": "pass", "proof_status": "equivalent"},
            {"status": "pass", "proof_status": "equivalent"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.equivalence.run_equivalence", side_effect=outcomes):
                report = run_equivalence_selftest(
                    DEFAULT_REFERENCE, DEFAULT_REFACTOR, Path(directory) / "selftest"
                )
            self.assertEqual(report["status"], "fail")
            self.assertEqual(
                report["negative_control"]["outcome"],
                "positive_failed_or_mutant_survived",
            )


if __name__ == "__main__":
    unittest.main()
