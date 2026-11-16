"""Offline tests for independent PPA campaign post-selection audit."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import ppa_campaign, ppa_campaign_audit


class PPACampaignAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.campaign = self.root / "artifacts/ppa-campaign"
        (self.campaign / "runs/run-001").mkdir(parents=True)
        self.loop_path = self.campaign / "runs/run-001/agent_loop_report.json"
        self.loop = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_agent_closed_loop",
            "objective_mode": "timing_constrained_mapped_area",
            "baseline": {"candidate_id": "baseline"},
            "best_candidate": {
                "candidate_id": "baseline", "candidate_sha256": "a" * 64,
                "mapped_cell_area": 100.0, "worst_setup_slack_ns": 0.5,
            },
            "improved_vs_baseline": False,
            "experiments": [{
                "candidate_id": "baseline", "candidate_sha256": "a" * 64,
                "feasible": True, "ppa": {
                    "status": "pass", "feasible": True,
                    "mapped_cell_area": 100.0, "worst_setup_slack_ns": 0.5,
                },
            }],
        }
        self.loop_path.write_text(json.dumps(self.loop))
        evidence = ppa_campaign._evidence(self.loop)
        self.manifest = {
            "report_type": "chiprd_agent_ppa_campaign_manifest",
            "campaign_id": "audit-test", "seed": 123, "runs": 1,
        }
        manifest_path = self.campaign / "campaign_manifest.json"
        manifest_path.write_text(json.dumps(self.manifest))
        self.report = {
            "report_type": "chiprd_agent_ppa_campaign",
            "campaign_id": "audit-test", "state": "completed", "status": "pass",
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "runs": [{
                "run": 1, "loop_report_path": self.loop_path.relative_to(self.root).as_posix(),
                "loop_report_sha256": hashlib.sha256(self.loop_path.read_bytes()).hexdigest(),
                "evidence": evidence,
            }],
        }
        (self.campaign / "campaign_report.json").write_text(json.dumps(self.report))
        self.root_patch = patch.object(ppa_campaign_audit, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def test_audits_every_selected_run_after_campaign(self):
        def fake_audit(candidate, output, *, audit_seed, count, loop_report):
            output.mkdir(parents=True)
            (output / "audit_report.json").write_text("{}")
            return {"status": "pass", "candidate": {"sha256": "a" * 64}}
        with patch.object(ppa_campaign_audit, "audit_candidate", side_effect=fake_audit) as audit:
            result = ppa_campaign_audit.audit_ppa_campaign(
                self.campaign, audit_seed=991977, vectors=128
            )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["runs_passed"], 1)
        self.assertEqual(audit.call_count, 1)
        self.assertEqual(audit.call_args.kwargs["audit_seed"], 991977)
        self.assertFalse(result["independence"]["audit_feedback_to_agent"])
        self.assertTrue((self.campaign / "post_selection_audit/audit_summary.json").is_file())

    def test_rejects_search_seed_reuse(self):
        with self.assertRaisesRegex(ValueError, "differ from"):
            ppa_campaign_audit.audit_ppa_campaign(self.campaign, audit_seed=123)

    def test_rejects_tampered_loop_report(self):
        self.loop_path.write_text(json.dumps({**self.loop, "improved_vs_baseline": True}))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            ppa_campaign_audit.audit_ppa_campaign(self.campaign, audit_seed=991977)

    def test_failed_independent_audit_fails_campaign_audit(self):
        def fake_audit(candidate, output, *, audit_seed, count, loop_report):
            output.mkdir(parents=True)
            return {"status": "fail", "candidate": {"sha256": "a" * 64}}
        with patch.object(ppa_campaign_audit, "audit_candidate", side_effect=fake_audit):
            result = ppa_campaign_audit.audit_ppa_campaign(self.campaign, audit_seed=991977)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["runs_failed_or_error"], 1)


if __name__ == "__main__":
    unittest.main()
