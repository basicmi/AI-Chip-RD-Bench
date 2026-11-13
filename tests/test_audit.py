"""Offline tests for post-selection independent-seed numerical audit."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tasks/cordic").mkdir(parents=True)
        (self.root / "chiprd").mkdir(parents=True)
        self.source = self.root / "tasks/cordic/cordic_rotator.sv"
        self.source.write_text("module cordic_rotator; endmodule\n", encoding="utf-8")
        (self.root / "chiprd/oracle.py").write_text("# fake oracle\n", encoding="utf-8")
        (self.root / "chiprd/harness.py").write_text("# fake harness\n", encoding="utf-8")
        self.testbench = self.root / "tasks/cordic/tb_cordic.sv"
        self.testbench.write_text("module tb_cordic; endmodule\n", encoding="utf-8")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.output = self.root / "artifacts/audit"
        self.root_patch = patch.object(audit, "ROOT", self.root)
        self.tb_patch = patch.object(audit.harness, "TESTBENCH", self.testbench)
        self.root_patch.start()
        self.tb_patch.start()

    def tearDown(self):
        self.tb_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def loop_report(self, *, feasible=True, digest=None, status="pass", path=None):
        exp_dir = self.root / "artifacts/audit-source-experiment"
        (exp_dir / "sealed").mkdir(parents=True, exist_ok=True)
        (exp_dir / "sealed/candidate.sv").write_bytes(self.source.read_bytes())
        (exp_dir / "experiment_manifest.json").write_text(json.dumps({
            "candidate": {"sha256": digest or self.digest},
        }), encoding="utf-8")
        (exp_dir / "experiment_report.json").write_text(json.dumps({
            "candidate_sha256": digest or self.digest,
            "candidate_snapshot_intact": True,
            "status": "pass",
            "checks": {
                name: {"status": "pass"} for name in (
                    "functional_synthesis", "mutation", "formal",
                    "formal_negative_control", "equivalence", "equivalence_selftest",
                )
            },
        }), encoding="utf-8")
        data = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_agent_closed_loop",
            "loop_id": "test-loop",
            "best_candidate": {
                "candidate_id": "baseline",
                "candidate_path": path or "tasks/cordic/cordic_rotator.sv",
                "candidate_sha256": digest or self.digest,
            },
            "experiments": [
                {"candidate_id": "baseline",
                 "candidate_sha256": digest or self.digest,
                 "status": status,
                 "feasible": feasible,
                 "generic_cell_count": 100,
                 "experiment_directory": "artifacts/audit-source-experiment",
                 "gate_statuses": {"functional_synthesis": "pass"}},
            ],
        }
        rp = self.root / "artifacts/agent-loops/test-loop/agent_loop_report.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(data), encoding="utf-8")
        return rp

    def verify_stub(self, candidate, output, seed, count):
        self.assertEqual(candidate, self.source)
        self.assertEqual(seed, 91919)
        self.assertEqual(count, 128)
        return {
            "status": "pass", "tested": count, "received": count,
            "tool": "verilator", "tool_version": "fixture",
            "failures": [], "reason": None,
        }

    def test_audits_selected_feasible_candidate_and_preserves_seed(self):
        source_report = self.loop_report()
        with patch.object(audit.harness, "verify", side_effect=self.verify_stub) as verify:
            result = audit.audit_candidate(
                None, self.output, audit_seed=91919,
                count=128, loop_report=source_report,
            )
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["not_private_holdout"])
        self.assertFalse(result["feedback_to_agent"])
        self.assertEqual(result["selection"]["loop_id"], "test-loop")
        saved = json.loads((self.output / "audit_report.json").read_text())
        self.assertEqual(saved["audit"]["requested_vectors"], 128)
        self.assertEqual(saved["candidate"]["sha256"], self.digest)

    def test_explicit_candidate_mode(self):
        with patch.object(audit.harness, "verify", side_effect=self.verify_stub):
            result = audit.audit_candidate(self.source, self.output, audit_seed=91919)
        self.assertEqual(result["candidate"]["selection_mode"], "explicit_candidate")
        self.assertIsNone(result["selection"])

    def test_rejects_stale_selected_rtl(self):
        rp = self.loop_report(digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "sealed evidence"):
            audit.audit_candidate(None, self.output, audit_seed=91919, loop_report=rp)

    def test_rejects_unverified_selection(self):
        rp = self.loop_report(feasible=False)
        with self.assertRaisesRegex(ValueError, "uniquely backed"):
            audit.audit_candidate(None, self.output, audit_seed=91919, loop_report=rp)

    def test_rejects_forged_paths(self):
        rp = self.loop_report(path="../outside.sv")
        with self.assertRaises((ValueError, FileNotFoundError)):
            audit.audit_candidate(None, self.output, audit_seed=91919, loop_report=rp)

    def test_rejects_tampered_sealed_evaluator_report(self):
        rp = self.loop_report()
        report_path = self.root / "artifacts/audit-source-experiment/experiment_report.json"
        evidence = json.loads(report_path.read_text())
        evidence["checks"]["equivalence"]["status"] = "fail"
        report_path.write_text(json.dumps(evidence))
        with self.assertRaisesRegex(ValueError, "sealed evidence"):
            audit.audit_candidate(None, self.output, audit_seed=91919, loop_report=rp)

    def test_requires_independent_seed_and_bounded_count(self):
        with self.assertRaisesRegex(ValueError, "audit_seed"):
            audit.audit_candidate(self.source, self.output, audit_seed=-1)
        with self.assertRaisesRegex(ValueError, "audit count"):
            audit.audit_candidate(self.source, self.output, audit_seed=7, count=4)

    def test_refuses_overwrite(self):
        with patch.object(audit.harness, "verify", side_effect=self.verify_stub):
            audit.audit_candidate(self.source, self.output, audit_seed=91919)
        with self.assertRaises(FileExistsError):
            audit.audit_candidate(self.source, self.output, audit_seed=91919)

    def test_failed_audit_fails_closed(self):
        with patch.object(audit.harness, "verify", return_value={
            "status": "fail", "reason": "oracle_mismatch",
            "tested": 128, "received": 128,
            "failures": [{"index": 9}], "tool": "verilator",
        }):
            result = audit.audit_candidate(self.source, self.output, audit_seed=91919)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["functional"]["failure_count"], 1)

    def test_tool_error_is_not_a_pass(self):
        with patch.object(audit.harness, "verify", side_effect=RuntimeError("missing Verilator")):
            result = audit.audit_candidate(self.source, self.output, audit_seed=91919)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
