"""Post-selection audit tests: no Agent calls; archive checks precede simulation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import pilot_audit
from tests.test_model_comparison import ModelComparisonTests


class PilotAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ModelComparisonTests(
            methodName="test_compares_two_models_without_inventing_winner")
        self.fixture.setUp()
        self.pilot = self.fixture.root
        self.fixture.campaign("model-1", model="model-a")
        self.fixture.campaign("model-2", model="model-b")
        self.terminal = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_two_model_ppa_pilot",
            "pilot_id": self.pilot.name,
            "models": ["model-a", "model-b"],
            "seed": 42,
            "status": "completed",
        }
        (self.pilot / "pilot_report.json").write_text(json.dumps(self.terminal))

    def tearDown(self):
        self.fixture.tearDown()

    def _run(self, status="pass"):
        observed = []
        def fake_audit(candidate, output, *, audit_seed, count, loop_report):
            observed.append((candidate, audit_seed, count, loop_report))
            output.mkdir(parents=True)
            payload = json.loads(loop_report.read_text())
            return {"status": status,
                    "candidate": {"sha256": payload["best_candidate"]["candidate_sha256"]}}
        with patch.object(pilot_audit, "ROOT", self.pilot.parent), patch.object(
            pilot_audit, "audit_candidate", side_effect=fake_audit
        ) as run:
            result = pilot_audit.audit_pilot(self.pilot, audit_seed=91919,
                                              vectors=128)
        return result, observed, run

    def test_audits_both_frozen_selections_after_campaigns(self):
        result, observed, invoked = self._run()
        self.assertEqual(invoked.call_count, 2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["passed_audits"], 2)
        self.assertEqual([x[1:3] for x in observed], [(91919, 128)] * 2)
        self.assertFalse(result["feedback_to_agent"])
        output = self.pilot / "post_selection_audit"
        self.assertTrue((output / "audit_summary.json").is_file())
        self.assertFalse((output / "audit_progress.json").exists())

    def test_one_failed_audit_fails_full_pilot(self):
        result, observed, _ = self._run(status="fail")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["passed_audits"], 0)
        self.assertEqual(len(observed), 2)

    def test_reject_same_seed_and_rerun_overwrite(self):
        with patch.object(pilot_audit, "ROOT", self.pilot.parent):
            with self.assertRaisesRegex(ValueError, "seed"):
                pilot_audit.audit_pilot(self.pilot, audit_seed=42)
        self._run()
        with patch.object(pilot_audit, "ROOT", self.pilot.parent):
            with self.assertRaises(FileExistsError):
                pilot_audit.audit_pilot(self.pilot, audit_seed=91919)

    def test_reject_modified_run_before_any_new_audit(self):
        report_path = self.pilot / "model-2/runs/run-001/agent_loop_report.json"
        report_path.write_text(report_path.read_text() + " ")
        with patch.object(pilot_audit, "ROOT", self.pilot.parent), patch.object(
            pilot_audit, "audit_candidate"
        ) as run:
            with self.assertRaisesRegex(ValueError, "SHA"):
                pilot_audit.audit_pilot(self.pilot, audit_seed=91919)
        run.assert_not_called()
        self.assertFalse((self.pilot / "post_selection_audit").exists())

    def test_reject_model_identity_mismatch_before_simulation(self):
        self.terminal["models"] = ["model-b", "model-a"]
        (self.pilot / "pilot_report.json").write_text(json.dumps(self.terminal))
        with patch.object(pilot_audit, "ROOT", self.pilot.parent), patch.object(
            pilot_audit, "audit_candidate"
        ) as run:
            with self.assertRaisesRegex(ValueError, "identities"):
                pilot_audit.audit_pilot(self.pilot, audit_seed=91919)
        run.assert_not_called()

    def test_partial_campaign_does_not_silently_pass(self):
        folder = self.pilot / "model-2"
        campaign_file = folder / "campaign_report.json"
        campaign = json.loads(campaign_file.read_text())
        campaign["runs"][0]["evidence"] = None
        campaign["runs"][0]["status"] = "error"
        campaign["state"] = "completed_with_errors"
        campaign["status"] = "fail"
        from chiprd.ppa_campaign import _rollup
        campaign["aggregate"] = _rollup(campaign["runs"], 1)
        campaign_file.write_text(json.dumps(campaign))
        result, _, _ = self._run()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["runs"][1]["status"], "missing_evidence")
        self.assertEqual(result["passed_audits"], 1)


if __name__ == "__main__":
    unittest.main()
