"""Offline tests for repeated controlled Agent campaigns."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import agent_campaign as campaign
from chiprd.agent_loop import AgentLoopBudget


def report_fixture(loop_id: str, cells: int = 90) -> dict:
    gates = {
        name: "pass" for name in (
            "functional_synthesis", "mutation", "formal",
            "formal_negative_control", "equivalence", "equivalence_selftest",
        )
    }
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_agent_closed_loop",
        "loop_id": loop_id,
        "status": "pass",
        "stop_reason": "max_iterations",
        "budget": {"max_iterations": 1},
        "budget_consumed": {"agent_iterations": 1, "candidate_experiments": 2},
        "baseline": {"candidate_id": "baseline", "feasible": True, "generic_cell_count": 100},
        "best_candidate": {"candidate_id": "candidate", "generic_cell_count": cells},
        "iterations": [{
            "iteration": 1, "status": "evaluated", "agent_status": "pass",
            "proposal_sha256": "a" * 64, "patch": {"patch_id": "candidate"},
            "model_id": "fixture-model",
            "model_config": {"temperature": 0.0, "max_tokens": 512, "timeout_seconds": 5.0},
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }],
        "experiments": [
            {"candidate_id": "baseline", "feasible": True,
             "generic_cell_count": 100, "gate_statuses": gates},
            {"candidate_id": "candidate", "feasible": True,
             "generic_cell_count": cells, "gate_statuses": gates},
        ],
    }


class AgentCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tasks/cordic").mkdir(parents=True)
        (self.root / "tasks/cordic/cordic_rotator.sv").write_text(
            "module cordic_rotator; endmodule\n", encoding="utf-8"
        )
        (self.root / "tasks/cordic/spec.md").write_text(
            "# CORDIC specification\n", encoding="utf-8"
        )
        self.root_patch = patch.object(campaign, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def fake_run(self, command, output, budget=None, seed=0, loop_id=None):
        output.mkdir(parents=True)
        baseline_hash = campaign._sha_file(self.root / campaign.BASELINE)
        manifest = {
            "baseline": {"candidate_sha256": baseline_hash},
            "seed": seed,
            "budget": budget.__dict__,
            "controller_sha256": "b" * 64,
            "agent": {"local_file_fingerprints": []},
        }
        manifest_path = output / "agent_loop_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        baseline_experiment = self.root / "artifacts/fake-baseline-exp"
        baseline_experiment.mkdir(parents=True, exist_ok=True)
        (baseline_experiment / "experiment_manifest.json").write_text(json.dumps({
            "toolchain": {"yosys": "fixture-1", "verilator": "fixture-1"},
            "evaluator": {"fingerprint_sha256": "c" * 64},
        }), encoding="utf-8")
        report = report_fixture(loop_id)
        report["experiments"][0]["experiment_directory"] = "artifacts/fake-baseline-exp"
        report_path = output / "agent_loop_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return {
            "status": "pass",
            "report_path": str(report_path),
            "manifest_path": str(manifest_path),
        }

    def test_repeated_runs_share_controls_and_aggregate(self):
        with patch.object(campaign, "run_agent_loop", side_effect=self.fake_run) as run:
            result = campaign.run_agent_campaign(
                ["mock-agent"], self.root / "artifacts/campaign",
                runs=3, budget=AgentLoopBudget(max_iterations=1),
                seed=123, campaign_id="repeat-test",
            )
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            [call.kwargs["seed"] for call in run.call_args_list], [123, 123, 123]
        )
        self.assertEqual(
            [call.kwargs["loop_id"] for call in run.call_args_list],
            ["repeat-test-r001", "repeat-test-r002", "repeat-test-r003"],
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["aggregate"]["runs_completed_with_scorecard"], 3)
        self.assertEqual(result["aggregate"]["runs_with_strict_improvement"], 3)
        self.assertEqual(result["aggregate"]["best_improvement_percent"]["mean"], 10.0)
        self.assertTrue(result["comparability"]["same_seed"])
        self.assertTrue(result["comparability"]["same_baseline_sha256"])
        self.assertTrue(result["comparability"]["same_toolchain_fingerprint"])
        self.assertTrue(result["comparability"]["same_evaluator_fingerprint"])

    def test_model_config_capture_redacts_credentials_and_url_path(self):
        with patch.dict(os.environ, {
            "CHIPRD_LLM_MODEL": "private-model",
            "CHIPRD_LLM_BASE_URL": "https://tenant.example/private/team?token=not-for-manifest",
            "CHIPRD_LLM_API_KEY": "top-secret-api-key",
            "CHIPRD_LLM_TEMPERATURE": "0.4",
        }):
            config = campaign._safe_model_config_environment()
        serialized = json.dumps(config)
        self.assertEqual(config["endpoint_origin"], "https://tenant.example")
        self.assertEqual(config["temperature"], "0.4")
        self.assertNotIn("top-secret-api-key", serialized)
        self.assertNotIn("not-for-manifest", serialized)
        self.assertEqual(config["api_key"], "not captured")

    def test_one_failed_run_does_not_discard_later_runs(self):
        def flaky(command, output, budget=None, seed=0, loop_id=None):
            if loop_id.endswith("r001"):
                raise RuntimeError("must not persist provider or secret details")
            return self.fake_run(command, output, budget, seed, loop_id)

        with patch.object(campaign, "run_agent_loop", side_effect=flaky):
            result = campaign.run_agent_campaign(
                ["mock-agent"], self.root / "artifacts/partial",
                runs=2, budget=AgentLoopBudget(max_iterations=1),
                seed=123, campaign_id="partial-test",
            )
        self.assertEqual(result["state"], "completed_with_errors")
        self.assertEqual(result["aggregate"]["run_status_counts"]["error"], 1)
        self.assertEqual(result["aggregate"]["runs_completed_with_scorecard"], 1)
        self.assertNotIn("must not persist", json.dumps(result))

    def test_rejects_existing_output_and_invalid_run_count(self):
        existing = self.root / "artifacts/existing"
        existing.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            campaign.run_agent_campaign(["mock-agent"], existing, runs=1)
        with self.assertRaisesRegex(ValueError, "runs must be"):
            campaign.run_agent_campaign(["mock-agent"], self.root / "artifacts/bad", runs=0)


if __name__ == "__main__":
    unittest.main()
