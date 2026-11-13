import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from chiprd.formal import classify_sby, make_sby_config, run_formal, run_formal_negative_control
from chiprd.harness import ROOT

CANDIDATE = ROOT / "tasks/cordic/cordic_rotator.sv"


class FormalTests(unittest.TestCase):
    def test_prove_config_is_explicit(self):
        config = make_sby_config("prove", 40)
        self.assertIn("mode prove", config)
        self.assertIn("depth 40", config)
        self.assertIn("smtbmc z3", config)
        self.assertIn("prep -top cordic_formal", config)

    def test_proof_modes_are_not_conflated(self):
        self.assertEqual(classify_sby(0, "DONE (PASS, rc=0)", "prove"), ("pass", "proven"))
        self.assertEqual(classify_sby(0, "DONE (PASS, rc=0)", "bmc"), ("pass", "bounded_pass"))
        self.assertEqual(classify_sby(0, "DONE (PASS, rc=0)", "cover"), ("pass", "cover_reached"))
        self.assertEqual(classify_sby(2, "DONE (FAIL, rc=2)"), ("fail", "failed"))
        self.assertEqual(classify_sby(1, "Status: unknown"), ("inconclusive", "inconclusive"))
        self.assertEqual(classify_sby(0, "process exited 0"), ("error", "error"))

    def test_missing_sby_fails_closed_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.formal.shutil.which", return_value=None):
                report = run_formal(CANDIDATE, Path(directory) / "formal")
            self.assertEqual(report["status"], "error")
            self.assertEqual(report["proof_status"], "error")
            self.assertIn("NOT run", report["error"])
            saved = json.loads((Path(directory) / "formal/formal_report.json").read_text())
            self.assertEqual(saved["proof_status"], "error")

    def test_negative_control_requires_baseline_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.formal.run_formal",
                       return_value={"status": "fail", "proof_status": "failed"}) as run_mock:
                report = run_formal_negative_control(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "fail")
            self.assertEqual(run_mock.call_count, 1)
            self.assertEqual(report["negative_control"], {})

    def test_negative_control_must_be_detected(self):
        outcomes = [
            {"status": "pass", "proof_status": "proven"},
            {"status": "fail", "proof_status": "failed"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.formal.run_formal", side_effect=outcomes):
                report = run_formal_negative_control(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["negative_control"]["outcome"], "detected")

    def test_surviving_formal_mutant_fails_negative_control(self):
        outcomes = [
            {"status": "pass", "proof_status": "proven"},
            {"status": "pass", "proof_status": "proven"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.formal.run_formal", side_effect=outcomes):
                report = run_formal_negative_control(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["negative_control"]["outcome"], "survived")


if __name__ == "__main__":
    unittest.main()
