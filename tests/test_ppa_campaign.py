"""Pure offline tests for PPA campaign measurement and orchestration."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import ppa_campaign as campaign
from chiprd.agent_loop import AgentLoopBudget


def experiment(name, digest, area, wns, *, gates=True):
    return {
        "candidate_id": name, "candidate_sha256": digest,
        "feasible": gates, "generic_cell_count": 100 if name == "baseline" else 85,
        "ppa": ({"status": "pass", "feasible": wns >= 0,
                 "mapped_cell_area": area, "worst_setup_slack_ns": wns}
                if gates else {"status": "skipped", "feasible": False}),
    }


def loop_report(wns=0.1, candidate_area=90, candidate_gates=True):
    baseline = experiment("baseline", "a" * 64, 100, 0.5)
    candidate = experiment("candidate", "b" * 64, candidate_area, wns,
                           gates=candidate_gates)
    selected = candidate if candidate_gates and wns >= 0 and candidate_area < 100 else baseline
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_agent_closed_loop",
        "objective_mode": "timing_constrained_mapped_area",
        "status": "pass",
        "iterations": [],
        "baseline": {"candidate_id": "baseline", "feasible": True, "generic_cell_count": 100},
        "experiments": [baseline, candidate],
        "best_candidate": {
            "candidate_id": selected["candidate_id"],
            "candidate_sha256": selected["candidate_sha256"],
            "mapped_cell_area": selected["ppa"]["mapped_cell_area"],
            "worst_setup_slack_ns": selected["ppa"]["worst_setup_slack_ns"],
        },
        "improved_vs_baseline": selected is candidate,
    }


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks/cordic").mkdir(parents=True)
        (self.root / "tasks/cordic/cordic_rotator.sv").write_text("module test; endmodule")
        (self.root / "tasks/cordic/spec.md").write_text("spec")
        self.objective = type("Objective", (), {"validate": lambda self: {
            "inputs": {"liberty": {"sha256": "a" * 64}},
            "clock_period_ns": 10.0
        }})()

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_area_selection_and_constraint(self):
        e = campaign._evidence(loop_report())
        self.assertEqual(e["area_reduction_percent"], 10.0)
        self.assertEqual(e["candidate_ppa_feasible"], 1)
        self.assertTrue(e["improved_vs_baseline"])

    def test_negative_slack_excluded(self):
        e = campaign._evidence(loop_report(wns=-0.1, candidate_area=60))
        self.assertEqual(e["candidate_ppa_feasible"], 0)
        self.assertFalse(e["improved_vs_baseline"])
        self.assertEqual(e["selected_area"], 100)

    def test_gate_failure_excluded(self):
        e = campaign._evidence(loop_report(candidate_gates=False))
        self.assertEqual(e["candidate_ppa_feasible"], 0)

    def test_reject_forged_selection_and_improvement(self):
        payload = loop_report()
        payload["best_candidate"]["mapped_cell_area"] = 1
        with self.assertRaisesRegex(ValueError, "area mismatch"):
            campaign._evidence(payload)
        payload = loop_report()
        payload["experiments"][1]["feasible"] = False
        with self.assertRaisesRegex(ValueError, "PPA feasible without valid status"):
            campaign._evidence(payload)
        payload = loop_report()
        payload["improved_vs_baseline"] = False
        with self.assertRaisesRegex(ValueError, "Improvement flag"):
            campaign._evidence(payload)

    def test_rollup_unknown_tokens_not_zero(self):
        rows = [
            {"evidence": campaign._evidence(loop_report()),
             "token_usage": {"reported_sum": 120, "unreported_calls": 0}},
            {"evidence": campaign._evidence(loop_report(wns=-0.1)),
             "token_usage": {"reported_sum": None, "unreported_calls": 1}},
        ]
        agg = campaign._rollup(rows, 2)
        self.assertEqual(agg["runs_with_improvement"], 1)
        self.assertEqual(agg["provider_reported_total_tokens"]["n"], 1)
        self.assertEqual(agg["runs_with_unknown_token_usage"], 1)
        self.assertEqual(agg["candidate_ppa_feasible_rate"]["mean"], 0.5)

    def test_campaign_persists_runs_and_skips_error_secrets(self):
        calls = []
        def fake_run(command, dest, budget=None, seed=None, loop_id=None,
                     ppa_objective=None):
            calls.append((seed, loop_id, ppa_objective))
            if loop_id.endswith("r002"):
                raise RuntimeError("provider secret must not appear in reports")
            dest.mkdir(parents=True)
            report_path = dest / "agent_loop_report.json"
            report_path.write_text(json.dumps(loop_report()))
            return {"report_path": str(report_path)}

        with patch.object(campaign, "ROOT", self.root), patch.object(
            campaign, "run_agent_loop", side_effect=fake_run
        ), patch.object(campaign, "summarize", return_value={
            "token_usage": {"total_tokens": {"reported_sum": 60, "unreported_calls": 0}},
            "model_ids": ["fixture"],
        }):
            report = campaign.run_ppa_campaign(
                ["mock-agent"], self.root / "artifacts/ppa-campaign",
                self.objective, runs=2, budget=AgentLoopBudget(max_iterations=1),
                seed=42, campaign_id="fixture")
        self.assertEqual(report["state"], "completed_with_errors")
        self.assertEqual(report["aggregate"]["runs_with_valid_ppa_evidence"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual([x[0] for x in calls], [42, 42])
        self.assertNotIn("provider secret", json.dumps(report))
        self.assertTrue((self.root / "artifacts/ppa-campaign/campaign_manifest.json").is_file())

    def test_reject_invalid_runs_and_existing_output(self):
        with patch.object(campaign, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "between 1 and 20"):
                campaign.run_ppa_campaign(["mock-agent"], self.root / "out",
                                          self.objective, runs=0)
            (self.root / "out").mkdir()
            with self.assertRaises(FileExistsError):
                campaign.run_ppa_campaign(["mock-agent"], self.root / "out",
                                          self.objective, runs=1)


if __name__ == "__main__":
    unittest.main()
