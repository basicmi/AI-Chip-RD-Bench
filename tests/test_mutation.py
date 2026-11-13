import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.harness import ROOT
from chiprd.mutation import (
    MUTATIONS, MutationNotApplicable, build_mutant, run_mutations,
)

CANDIDATE = ROOT / "tasks" / "cordic" / "cordic_rotator.sv"
REFACTOR = ROOT / "tasks" / "cordic" / "cordic_rotator_refactor.sv"


class MutationTests(unittest.TestCase):
    def test_each_mutation_changes_source_exactly(self):
        source = CANDIDATE.read_text(encoding="utf-8")
        for mutation in MUTATIONS:
            with self.subTest(mutation=mutation["id"]):
                mutant = build_mutant(source, mutation)
                self.assertNotEqual(mutant, source)
                self.assertEqual(
                    mutant.count(mutation["after"]),
                    source.count(mutation["after"]) + 1,
                )

    def test_mutation_fails_closed_on_source_drift(self):
        mutation = MUTATIONS[0]
        with self.assertRaises(ValueError):
            build_mutant("// changed RTL", mutation)

    def test_structural_refactor_reports_non_applicable_source_pattern(self):
        mutation = next(item for item in MUTATIONS if item["id"] == "reverse_x_rotation")
        source = REFACTOR.read_text(encoding="utf-8")
        with self.assertRaises(MutationNotApplicable):
            build_mutant(source, mutation)

    def test_campaign_excludes_non_applicable_mutations_but_reports_coverage(self):
        baseline = {"status": "pass", "tool": "verilator"}
        killed = {"status": "fail", "reason": "oracle_mismatch"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.mutation.verify", side_effect=[baseline] + [killed] * 4):
                report = run_mutations(REFACTOR, Path(directory) / "run")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["total"], 5)
        self.assertEqual(report["summary"]["applicable"], 4)
        self.assertEqual(report["summary"]["killed"], 4)
        self.assertEqual(report["summary"]["not_applicable"], 1)
        self.assertEqual(report["mutation_score"], 1.0)

    def test_all_mutants_must_be_killed(self):
        baseline = {"status": "pass", "tool": "verilator"}
        killed = {"status": "fail", "reason": "oracle_mismatch"}
        outcomes = [baseline] + [killed for _ in MUTATIONS]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.mutation.verify", side_effect=outcomes):
                report = run_mutations(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["summary"]["killed"], len(MUTATIONS))
            self.assertEqual(report["mutation_score"], 1.0)
            saved = json.loads(
                (Path(directory) / "run" / "mutation_report.json").read_text()
            )
            self.assertEqual(saved["candidate_sha256"], report["candidate_sha256"])

    def test_surviving_mutant_fails_suite(self):
        baseline = {"status": "pass"}
        killed = {"status": "fail", "reason": "oracle_mismatch"}
        survived = {"status": "pass"}
        outcomes = [baseline, killed, survived] + [killed for _ in MUTATIONS[2:]]
        with tempfile.TemporaryDirectory() as directory:
            with patch("chiprd.mutation.verify", side_effect=outcomes):
                report = run_mutations(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["summary"]["survived"], 1)
            self.assertLess(report["mutation_score"], 1.0)

    def test_baseline_failure_stops_mutation_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "chiprd.mutation.verify", return_value={"status": "fail"}
            ) as verify_mock:
                report = run_mutations(CANDIDATE, Path(directory) / "run")
            self.assertEqual(report["status"], "fail")
            self.assertEqual(verify_mock.call_count, 1)
            self.assertEqual(report["mutations"], [])


if __name__ == "__main__":
    unittest.main()
