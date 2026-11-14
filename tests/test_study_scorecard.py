"""Offline final-scorecard tests with archived campaigns and audited run fixtures."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import study_scorecard as score
from chiprd.model_comparison import compare_campaigns, _sha
from tests.test_model_comparison import ModelComparisonTests


class StudyScorecardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ModelComparisonTests(methodName="test_compares_two_models_without_inventing_winner")
        self.fixture.setUp()
        self.root = self.fixture.root
        self.pilot = self.root / "pilots" / "study"
        self.pilot.mkdir(parents=True)
        self.a = self.fixture.campaign("model-1", model="model-a", area=90)
        self.b = self.fixture.campaign("model-2", model="model-b", area=80)
        # Fixtures were generated at root; move both directories under pilot
        self.a.rename(self.pilot / "model-1")
        self.b.rename(self.pilot / "model-2")
        self.a = self.pilot / "model-1"
        self.b = self.pilot / "model-2"
        # Repo-relative loop paths need the pilot prefix; evidence still bound by SHA.
        for folder in (self.a, self.b):
            report_file = folder / "campaign_report.json"
            report = json.loads(report_file.read_text())
            report["runs"][0]["loop_report_path"] = (
                f"artifacts/pilots/study/{folder.name}/runs/run-001/agent_loop_report.json"
            )
            report_file.write_text(json.dumps(report))
        # Run locations use the mocked repository root: filename marker handles the suffix.
        comparison = compare_campaigns([self.a, self.b])
        (self.pilot / "comparison.json").write_text(json.dumps(comparison))
        self.terminal = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_two_model_ppa_pilot",
            "pilot_id": "study", "models": ["model-a", "model-b"],
            "seed": 42, "status": "completed", "comparison_ready": True,
        }
        (self.pilot / "pilot_report.json").write_text(json.dumps(self.terminal))
        folder = self.pilot / "post_selection_audit"
        folder.mkdir()
        self.audit_rows = []
        for index, path in enumerate((self.a, self.b), 1):
            campaign = json.loads((path / "campaign_report.json").read_text())
            archive = campaign["runs"][0]
            audit_dir = folder / f"model-{index}-run-001"
            audit_dir.mkdir()
            audit = {
                "schema_version": "1.0.0",
                "report_type": "chiprd_post_selection_audit",
                "candidate": {"sha256": archive["evidence"]["selected_sha256"]},
                "selection": {"loop_report_sha256": archive["loop_report_sha256"]},
                "audit": {"seed": 91919, "requested_vectors": 128},
                "status": "pass",
            }
            (audit_dir / "audit_report.json").write_text(json.dumps(audit))
            self.audit_rows.append({
                "model_index": index, "run": 1, "status": "pass",
                "audit_status": "pass",
                "candidate_sha256": archive["evidence"]["selected_sha256"],
                "loop_report_sha256": archive["loop_report_sha256"],
                "audit_report": (
                    f"pilots/study/post_selection_audit/model-{index}-run-001/audit_report.json"
                ),
            })
        self.audit = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_two_model_ppa_post_selection_audit",
            "pilot_id": "study", "audit_seed": 91919,
            "vectors_per_selection": 128,
            "audited_run_count": 2, "passed_audits": 2,
            "status": "pass", "runs": self.audit_rows,
            "not_private_holdout": True, "feedback_to_agent": False,
        }
        self.write_audit()

    def tearDown(self):
        self.fixture.tearDown()

    def write_audit(self):
        (self.pilot / "post_selection_audit/audit_summary.json").write_text(
            json.dumps(self.audit))

    def report(self):
        with patch.object(score, "ROOT", self.root):
            return score.summarize_study(self.pilot)

    def test_complete_audited_ppa_improvements_and_no_winner(self):
        result = self.report()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete_evidence"])
        self.assertEqual([x["audited_improved_runs"] for x in result["models"]], [1, 1])
        self.assertIsNone(result["winner"])
        self.assertEqual([x["audited_improvement_rate"] for x in result["models"]], [1, 1])

    def test_audit_failure_excludes_improvement_even_if_ppa_is_good(self):
        row = self.audit_rows[1]
        row["status"] = row["audit_status"] = "fail"
        self.audit["passed_audits"] = 1
        self.audit["status"] = "fail"
        audit_file = self.pilot / "post_selection_audit/model-2-run-001/audit_report.json"
        raw = json.loads(audit_file.read_text())
        raw["status"] = "fail"
        audit_file.write_text(json.dumps(raw))
        self.write_audit()
        result = self.report()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["models"][1]["ppa_improved_runs"], 1)
        self.assertEqual(result["models"][1]["audited_improved_runs"], 0)

    def test_rejects_forged_audit_pass_and_candidate_hash(self):
        file = self.pilot / "post_selection_audit/model-1-run-001/audit_report.json"
        raw = json.loads(file.read_text())
        raw["status"] = "fail"
        file.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "Audit summary"):
            self.report()
        raw["status"] = "pass"
        file.write_text(json.dumps(raw))
        self.audit_rows[0]["candidate_sha256"] = "f" * 64
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "selection identity"):
            self.report()

    def test_rejects_modified_campaign_or_stored_comparison(self):
        file = self.a / "campaign_manifest.json"
        file.write_text(file.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "manifest SHA"):
            self.report()
        file.write_text(file.read_text()[:-1])
        comparison_file = self.pilot / "comparison.json"
        row = json.loads(comparison_file.read_text())
        row["models"][0]["improving_runs"] = 999
        comparison_file.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, "comparison"):
            self.report()

    def test_rejects_duplicate_missing_and_unsupported_audit_rows(self):
        self.audit["runs"] = [self.audit_rows[0], self.audit_rows[0]]
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.report()
        self.audit["runs"] = [self.audit_rows[0]]
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "Missing post-selection"):
            self.report()

    def test_reject_same_seed_as_search(self):
        self.audit["audit_seed"] = 42
        self.write_audit()
        with self.assertRaisesRegex(ValueError, "independent audit"):
            self.report()


if __name__ == "__main__":
    unittest.main()
