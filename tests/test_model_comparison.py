"""Offline evidence and fairness checks for cross-model PPA comparisons."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from chiprd import model_comparison as compare
from chiprd.ppa_campaign import _evidence, _rollup
from chiprd.benchmark_report import summarize
from tests.test_ppa_campaign import loop_report

HASH = "a" * 64
FINGERPRINTS = {
    key: HASH for key in (
        "agent_loop_sha256", "agent_ppa_sha256", "timing_sha256",
        "ppa_scorecard_sha256", "sealed_evidence_sha256"
    )
}


class ModelComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def campaign(self, label, *, area=90, model=None, seed=42, ppa_hash=HASH,
                 temp="0", failed=False):
        folder = self.root / label
        run_dir = folder / "runs" / "run-001"
        run_dir.mkdir(parents=True)
        data = loop_report(candidate_area=area)
        data["loop_id"] = label + "-r001"
        path = run_dir / "agent_loop_report.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        rows = [{
            "run": 1, "loop_id": data["loop_id"], "status": "pass",
            "evidence": _evidence(data),
            "loop_report_path": f"artifacts/{label}/runs/run-001/agent_loop_report.json",
            "loop_report_sha256": compare._sha(path),
            "token_usage": summarize(path)["token_usage"]["total_tokens"],
        }]
        if failed:
            rows[0]["evidence"] = None
            rows[0]["status"] = "error"
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_agent_ppa_campaign_manifest",
            "campaign_id": label,
            "controller_sha256": HASH,
            "evaluator_fingerprints": FINGERPRINTS,
            "baseline_sha256": HASH,
            "task_spec_sha256": HASH,
            "seed": seed,
            "runs": 1,
            "budget": {"max_iterations": 2},
            "ppa_configuration": {
                "inputs": {k: {"sha256": ppa_hash}
                           for k in ("liberty", "tech_lef", "cells_lef")},
                "clock_period_ns": 10.0, "io_delay_ns": 0.5,
            },
            "agent": {"model_configuration": {
                "model": model or label, "temperature": temp,
                "max_tokens": "4096", "timeout_seconds": "90",
            }},
        }
        manifest_path = folder / "campaign_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = {
            "schema_version": "1.0.0",
            "report_type": "chiprd_agent_ppa_campaign",
            "campaign_id": label,
            "manifest_sha256": compare._sha(manifest_path),
            "state": "completed_with_errors" if failed else "completed",
            "status": "fail" if failed else "pass",
            "runs": rows,
            "aggregate": _rollup(rows, 1),
        }
        (folder / "campaign_report.json").write_text(json.dumps(report), encoding="utf-8")
        return folder

    def test_compares_two_models_without_inventing_winner(self):
        a = self.campaign("modelA", area=90)
        b = self.campaign("modelB", area=85)
        result = compare.compare_campaigns([a, b])
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["comparison_ready"])
        self.assertIsNone(result["winner"])
        self.assertEqual([x["mean_area_reduction_percent"] for x in result["models"]],
                         [10.0, 15.0])
        self.assertEqual(result["models"][0]["runs_with_unknown_token_usage"], 1)

    def test_rejects_changed_technology_or_generation_constraints(self):
        a = self.campaign("modelA")
        with self.assertRaisesRegex(ValueError, "mismatched"):
            compare.compare_campaigns([a, self.campaign("modelB", ppa_hash="b" * 64)])
        with self.assertRaisesRegex(ValueError, "mismatched"):
            compare.compare_campaigns([a, self.campaign("modelC", temp="0.5")])
        with self.assertRaisesRegex(ValueError, "mismatched"):
            compare.compare_campaigns([a, self.campaign("modelD", seed=123)])

    def test_refuses_unfingerprinted_legacy_campaign(self):
        a = self.campaign("modelA")
        b = self.campaign("modelB")
        manifest_file = b / "campaign_manifest.json"
        data = json.loads(manifest_file.read_text())
        del data["evaluator_fingerprints"]
        manifest_file.write_text(json.dumps(data), encoding="utf-8")
        report_file = b / "campaign_report.json"
        report = json.loads(report_file.read_text())
        report["manifest_sha256"] = compare._sha(manifest_file)
        report_file.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            compare.compare_campaigns([a, b])

    def test_detects_tampered_manifest_or_loop_and_aggregate(self):
        a = self.campaign("modelA")
        b = self.campaign("modelB")
        file = b / "campaign_manifest.json"
        file.write_text(file.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "manifest SHA"):
            compare.compare_campaigns([a, b])
        b = self.campaign("modelB")
        loop = b / "runs/run-001/agent_loop_report.json"
        loop.write_text(loop.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Run report SHA"):
            compare.compare_campaigns([a, b])
        b = self.campaign("modelB")
        report_file = b / "campaign_report.json"
        report = json.loads(report_file.read_text())
        report["aggregate"]["runs_with_improvement"] = 999
        report_file.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "aggregate"):
            compare.compare_campaigns([a, b])

    def test_fails_closed_on_fake_path_and_invalid_selected_evidence(self):
        a = self.campaign("modelA")
        b = self.campaign("modelB")
        report_file = b / "campaign_report.json"
        report = json.loads(report_file.read_text())
        report["runs"][0]["loop_report_path"] = "../../outside.json"
        report_file.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            compare.compare_campaigns([a, b])
        b = self.campaign("modelB")
        loop = b / "runs/run-001/agent_loop_report.json"
        payload = json.loads(loop.read_text())
        payload["best_candidate"]["mapped_cell_area"] = 0.1
        loop.write_text(json.dumps(payload))
        report_file = b / "campaign_report.json"
        report = json.loads(report_file.read_text())
        report["runs"][0]["loop_report_sha256"] = compare._sha(loop)
        report_file.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "area mismatch"):
            compare.compare_campaigns([a, b])

    def test_partial_campaign_not_comparison_ready(self):
        a = self.campaign("modelA")
        b = self.campaign("modelB", failed=True)
        result = compare.compare_campaigns([a, b])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["comparison_ready"])
        self.assertIsNone(result["models"][1]["mean_area_reduction_percent"])

    def test_duplicate_model_id_or_campaign_count_rejected(self):
        a = self.campaign("modelA", model="same-model")
        b = self.campaign("modelB", model="same-model")
        with self.assertRaisesRegex(ValueError, "Duplicate model"):
            compare.compare_campaigns([a, b])
        with self.assertRaisesRegex(ValueError, "between 2 and 12"):
            compare.compare_campaigns([a])


if __name__ == "__main__":
    unittest.main()
