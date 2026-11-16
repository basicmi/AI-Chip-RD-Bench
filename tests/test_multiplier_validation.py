import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from chiprd.multiplier_validation import (
    ANCHOR, MUTATIONS, _equivalence_script, prove_equivalence, run_multiplier_mutations,
)


class MultiplierFormalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gold = self.root / "gold.sv"
        self.candidate = self.root / "candidate.sv"
        self.gold.write_text("module int_mul16(input logic signed [15:0] a,b, output logic signed [31:0] product); always_comb product = a * b; endmodule\n")
        self.candidate.write_text(self.gold.read_text())

    def tearDown(self):
        self.tmp.cleanup()

    @patch("chiprd.multiplier_validation.shutil.which", return_value="/usr/bin/yosys")
    @patch("chiprd.multiplier_validation.subprocess.run")
    def test_requires_explicit_yosys_equivalence_marker(self, run, _which):
        run.return_value = subprocess.CompletedProcess([], 0, "Equivalence successfully proven!\n", "")
        result = prove_equivalence(self.gold, self.candidate, self.root / "pass")
        self.assertEqual(result["proof_status"], "equivalent")
        run.return_value = subprocess.CompletedProcess([], 1, "Unproven $equiv cells\n", "")
        result = prove_equivalence(self.gold, self.candidate, self.root / "fail")
        self.assertEqual(result["proof_status"], "not_equivalent")

    @patch("chiprd.multiplier_validation.shutil.which", return_value=None)
    def test_missing_yosys_fails_closed(self, _which):
        result = prove_equivalence(self.gold, self.candidate, self.root / "missing")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["proof_status"], "error")

    def test_rejects_symlink_and_overwrite(self):
        link = self.root / "link.sv"
        link.symlink_to(self.gold)
        with self.assertRaisesRegex(ValueError, "symlink"):
            prove_equivalence(self.gold, link, self.root / "symlink")
        out = self.root / "existing"
        out.mkdir()
        with self.assertRaises(FileExistsError):
            prove_equivalence(self.gold, self.candidate, out)

    @patch("chiprd.multiplier_harness.evaluate")
    def test_mutation_campaign_requires_functional_detection(self, evaluate):
        evaluate.return_value = {
            "status": "fail", "functional_status": "fail",
            "generic_synthesis_status": "pass",
        }
        report = run_multiplier_mutations(
            self.candidate, self.root / "mutations", random_count=16)
        self.assertEqual(report["mutation_count"], len(MUTATIONS))
        self.assertEqual(report["detected_count"], len(MUTATIONS))
        self.assertEqual(report["status"], "pass")
        evaluate.return_value = {
            "status": "fail", "functional_status": "not_run",
            "generic_synthesis_status": "fail",
        }
        report = run_multiplier_mutations(
            self.candidate, self.root / "mutations2", random_count=16)
        self.assertEqual(report["detected_count"], 0)
        self.assertEqual(report["status"], "fail")

    def test_equivalence_script_lowers_each_design_before_equiv_make(self):
        script = _equivalence_script()
        self.assertLess(script.index("read_verilog -sv gold.sv"), script.index("proc"))
        self.assertLess(script.index("proc"), script.index("rename int_mul16 gold"))
        self.assertLess(script.index("rename int_mul16 gold"), script.index("read_verilog -sv gate.sv"))
        self.assertLess(script.index("read_verilog -sv gate.sv"), script.rindex("proc"))
        self.assertLess(script.rindex("proc"), script.index("rename int_mul16 gate"))
        self.assertLess(script.index("rename int_mul16 gate"), script.index("equiv_make gold gate equiv"))

    def test_mutation_anchor_is_unique_in_golden(self):
        self.assertEqual(self.gold.read_text().count(ANCHOR), 1)


if __name__ == "__main__":
    unittest.main()
