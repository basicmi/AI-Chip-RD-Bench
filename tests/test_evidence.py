import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.evidence import (
    MANIFEST_NAME,
    REPORT_NAME,
    collect_toolchain,
    reproduce_experiment,
    result_signature,
    run_experiment,
)
from chiprd.harness import ROOT

CANDIDATE = ROOT / "tasks/cordic/cordic_rotator.sv"
TOOLS = {
    "status": "pass",
    "versions": {
        "verilator": "Verilator 5.x",
        "yosys": "Yosys 0.x",
        "eqy": "EQY v0.71",
        "sby": "SBY v0.70",
        "z3": "Z3 version 4.x",
    },
    "missing": [],
    "python": "Python 3.11",
    "platform": "test-platform",
}


def mock_results():
    return {
        "functional_synthesis": {
            "status": "pass",
            "results": {
                "functional": {
                    "status": "pass", "tested": 24, "received": 24, "failures": []
                },
                "synthesis": {
                    "status": "pass", "generic_cell_count": 1549,
                    "cell_type_histogram": {"$add": 2},
                },
            },
        },
        "mutation": {
            "status": "pass", "mutation_score": 1.0,
            "summary": {"total": 5, "killed": 5, "survived": 0, "errors": 0},
            "mutations": [{"id": "m1", "outcome": "killed"}],
        },
        "formal": {
            "status": "pass", "proof_status": "proven", "mode": "prove",
            "depth": 40, "properties": ["protocol"],
        },
        "formal_negative_control": {
            "status": "pass",
            "baseline": {"proof_status": "proven"},
            "negative_control": {
                "outcome": "detected",
                "formal_result": {"proof_status": "failed"},
            },
        },
        "equivalence": {
            "status": "pass", "proof_status": "equivalent",
            "equivalence_semantics": "cycle_accurate_sequential",
        },
        "equivalence_selftest": {
            "status": "pass",
            "positive": {"proof_status": "equivalent"},
            "negative_control": {
                "outcome": "detected",
                "formal_result": {"proof_status": "not_equivalent"},
            },
        },
    }


class EvidenceTests(unittest.TestCase):
    def patch_checks(self, stack, outcomes=None):
        outcomes = outcomes or mock_results()
        stack.enter_context(patch("chiprd.evidence.collect_toolchain", return_value=TOOLS))
        stack.enter_context(patch(
            "chiprd.evidence.run", return_value=outcomes["functional_synthesis"]
        ))
        stack.enter_context(patch(
            "chiprd.evidence.run_mutations", return_value=outcomes["mutation"]
        ))
        stack.enter_context(patch(
            "chiprd.evidence.run_formal", return_value=outcomes["formal"]
        ))
        stack.enter_context(patch(
            "chiprd.evidence.run_formal_negative_control",
            return_value=outcomes["formal_negative_control"],
        ))
        stack.enter_context(patch(
            "chiprd.evidence.run_equivalence", return_value=outcomes["equivalence"]
        ))
        stack.enter_context(patch(
            "chiprd.evidence.run_equivalence_selftest",
            return_value=outcomes["equivalence_selftest"],
        ))

    def test_experiment_seals_candidate_and_writes_manifest_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            from contextlib import ExitStack
            with ExitStack() as stack:
                self.patch_checks(stack)
                report = run_experiment(CANDIDATE, Path(directory) / "experiment", seed=7)
            root = Path(directory) / "experiment"
            manifest = json.loads((root / MANIFEST_NAME).read_text())
            snapshot = root / manifest["candidate"]["snapshot_path"]
            self.assertEqual(report["status"], "pass")
            self.assertEqual(snapshot.read_bytes(), CANDIDATE.read_bytes())
            self.assertEqual(manifest["candidate"]["sha256"], report["candidate_sha256"])
            self.assertTrue(manifest["evaluator"]["fingerprint_sha256"])
            self.assertEqual(manifest["parameters"]["seed"], 7)
            self.assertEqual(len((root / "experiment_ledger.jsonl").read_text().splitlines()), 15)
            self.assertTrue((root / REPORT_NAME).is_file())

    def test_experiment_refuses_to_overwrite_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"
            root.mkdir()
            (root / MANIFEST_NAME).write_text("{}")
            with patch("chiprd.evidence.collect_toolchain", return_value=TOOLS):
                with self.assertRaises(FileExistsError):
                    run_experiment(CANDIDATE, root)

    def test_missing_toolchain_fails_closed_without_running_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = {**TOOLS, "status": "error", "missing": ["eqy"]}
            with patch("chiprd.evidence.collect_toolchain", return_value=missing):
                with patch("chiprd.evidence.run") as runner:
                    report = run_experiment(CANDIDATE, Path(directory) / "experiment")
            self.assertEqual(report["status"], "error")
            runner.assert_not_called()

    def test_reproduction_blocks_when_evaluator_hash_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            from contextlib import ExitStack
            root = Path(directory) / "experiment"
            with ExitStack() as stack:
                self.patch_checks(stack)
                run_experiment(CANDIDATE, root)
            manifest_path = root / MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            manifest["evaluator"]["files"]["chiprd/oracle.py"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest))
            with patch("chiprd.evidence.collect_toolchain", return_value=TOOLS):
                with patch("chiprd.evidence.run_experiment") as rerun:
                    report = reproduce_experiment(manifest_path, Path(directory) / "reproduced")
            self.assertEqual(report["status"], "blocked")
            self.assertIn("evaluator_input_hash_mismatch", report["mismatches"])
            rerun.assert_not_called()

    def test_reproduction_rejects_manifest_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"
            root.mkdir()
            manifest = {
                "manifest_type": "chiprd_experiment",
                "manifest_schema_version": "1.0.0",
                "candidate": {"snapshot_path": "../candidate.sv"},
            }
            path = root / MANIFEST_NAME
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                reproduce_experiment(path, Path(directory) / "reproduced")

    def test_result_signature_ignores_timestamps_and_paths(self):
        first = mock_results()
        second = mock_results()
        first["formal"]["created_at_utc"] = "today"
        second["formal"]["created_at_utc"] = "tomorrow"
        self.assertEqual(result_signature(first), result_signature(second))


if __name__ == "__main__":
    unittest.main()
