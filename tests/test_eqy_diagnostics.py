"""Offline tests: diagnostic summary is informative but never a proof override."""
import json
import tempfile
import unittest
from pathlib import Path

from chiprd.eqy_diagnostics import summarize_candidate, summarize_candidates


class EQYDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = [self.root / name for name in ("iter2", "iter4")]
        for p in self.paths:
            (p / "equivalence-candidate/eqy-work/cordic_equiv/partitions").mkdir(parents=True)
            (p / "equivalence-candidate/equivalence_report.json").write_text(json.dumps({
                "status": "inconclusive", "proof_status": "inconclusive",
                "candidate_sha256": "a" * 64, "reference_sha256": "b" * 64,
                "error": "EQY timed out after 300 seconds", "timeout_seconds": 300,
            }))
            (p / "equivalence-candidate/eqy-work/cordic_equiv/partitions/logfile.txt").write_text(
                "partition outputs: engine abc pdr UNKNOWN\n"
            )

            (p / "equivalence-candidate/eqy-work/cordic_equiv/partition.list").write_text(
                "cordic_rotator cordic_rotator.busy xbits : busy[0]\n"
                "cordic_rotator cordic_rotator.x_out xbits : x_out[15:0]\n"
                "cordic_rotator cordic_rotator.y_out xbits : y_out[15:0]\n"
            )
            (p / "equivalence-candidate/eqy-work/cordic_equiv/logfile.txt").write_text(
                "Proved equivalence of partition 'cordic_rotator.busy' using strategy 'pdr'\n"
                "Could not prove equivalence of partition 'cordic_rotator.x_out' using strategy 'pdr': timeout\n"
                "Running strategy 'pdr' on 'cordic_rotator.y_out'..\n"
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_inconclusive_remains_failure_with_partition_details(self):
        summary = summarize_candidates(self.paths[0], self.paths[1],
                                       self.root / "summary.json")
        self.assertEqual(summary["status"], "fail")
        for name in ("iter2", "iter4"):
            row = summary["candidates"][name]
            self.assertEqual(row["proof_status"], "inconclusive")
            self.assertTrue(row["diagnostic_worktree_present"])
            self.assertEqual(row["worktree_file_count"], 3)
            self.assertEqual(row["partitions"]["cordic_rotator.busy"], "pass")
            self.assertEqual(row["partitions"]["cordic_rotator.x_out"], "timeout")
            self.assertEqual(row["partitions"]["cordic_rotator.y_out"], "running_or_interrupted")
            self.assertTrue(any("UNKNOWN" in line for excerpt in row["log_excerpts"]
                                for line in excerpt.get("last_status_lines", [])))
        self.assertTrue((self.root / "summary.json").exists())

    def test_requires_both_candidates_to_have_explicit_pass(self):
        first = self.paths[0] / "equivalence-candidate/equivalence_report.json"
        first.write_text(json.dumps({"status": "pass", "proof_status": "equivalent"}))
        self.assertEqual(summarize_candidates(*self.paths, self.root / "partial.json")["status"],
                         "fail")
        second = self.paths[1] / "equivalence-candidate/equivalence_report.json"
        second.write_text(json.dumps({"status": "pass", "proof_status": "equivalent"}))
        self.assertEqual(summarize_candidates(*self.paths, self.root / "pass.json")["status"],
                         "pass")

    def test_rejects_unsubstantiated_pass(self):
        path = self.paths[0] / "equivalence-candidate/equivalence_report.json"
        path.write_text(json.dumps({"status": "pass", "proof_status": "inconclusive"}))
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            summarize_candidate(self.paths[0])

    def test_missing_candidate_report_is_not_success(self):
        (self.paths[1] / "equivalence-candidate/equivalence_report.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing mandatory"):
            summarize_candidates(*self.paths, self.root / "missing.json")


if __name__ == "__main__":
    unittest.main()
