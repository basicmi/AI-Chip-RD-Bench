"""Offline tests: the pilot never contacts an LLM unless explicitly enabled."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import pilot_runner as pilot
from chiprd.agent_loop import AgentLoopBudget


class PilotRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.objective = object()
        self.ready = {"local_configuration_ready": True, "status": "pass"}
        self.blocked = {"local_configuration_ready": False, "status": "fail"}

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, **kwargs):
        with patch.object(pilot, "ROOT", self.root), patch.object(
            pilot, "assess", return_value=self.ready
        ), patch.object(pilot, "run_ppa_campaign") as run:
            output = pilot.pilot(
                ["model-a", "model-b"], self.root / "artifacts", "test-pilot",
                self.objective, **kwargs)
        return output, run

    def test_default_is_no_model_calls_no_artifacts(self):
        result, run = self._run()
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["started_model_campaigns"], 0)
        run.assert_not_called()
        self.assertFalse((self.root / "artifacts/test-pilot").exists())

    def test_preflight_blocks_both_models_before_first_call(self):
        checks = []
        def check(objective):
            checks.append(os.environ.get("CHIPRD_LLM_MODEL"))
            return self.ready if len(checks) == 1 else self.blocked
        with patch.object(pilot, "ROOT", self.root), patch.object(
            pilot, "assess", side_effect=check
        ), patch.object(pilot, "run_ppa_campaign") as run:
            with patch.dict(os.environ, {"CHIPRD_LLM_MODEL": "original"}):
                result = pilot.pilot(["model-a", "model-b"], self.root / "artifacts",
                                     "test-pilot", self.objective, execute=True)
                self.assertEqual(os.environ["CHIPRD_LLM_MODEL"], "original")
        self.assertEqual(checks, ["model-a", "model-b"])
        self.assertEqual(result["status"], "blocked_by_preflight")
        run.assert_not_called()

    def test_executes_two_campaigns_then_compares(self):
        seen = []
        def fake_campaign(command, folder, objective, **kwargs):
            seen.append((os.environ.get("CHIPRD_LLM_MODEL"), folder.name, kwargs["seed"]))
            folder.mkdir()
        with patch.object(pilot, "ROOT", self.root), patch.object(
            pilot, "assess", return_value=self.ready
        ), patch.object(pilot, "run_ppa_campaign", side_effect=fake_campaign), patch.object(
            pilot, "compare_campaigns", return_value={
                "comparison_ready": True, "status": "pass", "models": []
        }):
            with patch.dict(os.environ, {"CHIPRD_LLM_MODEL": "original"}):
                report = pilot.pilot(
                    ["model-a", "model-b"], self.root / "artifacts", "test-pilot",
                    self.objective, execute=True, runs=3, seed=42)
                self.assertEqual(os.environ["CHIPRD_LLM_MODEL"], "original")
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["comparison_ready"])
        self.assertEqual(seen, [("model-a", "model-1", 42),
                                ("model-b", "model-2", 42)])
        folder = self.root / "artifacts/test-pilot"
        self.assertTrue((folder / "preflight.json").exists())
        self.assertTrue((folder / "comparison.json").exists())
        self.assertTrue((folder / "pilot_report.json").exists())

    def test_campaign_failure_never_calls_second_model_or_comparison(self):
        def fail(*args, **kwargs):
            raise RuntimeError("PROVIDER_SECRET")
        with patch.object(pilot, "ROOT", self.root), patch.object(
            pilot, "assess", return_value=self.ready
        ), patch.object(pilot, "run_ppa_campaign", side_effect=fail) as run, patch.object(
            pilot, "compare_campaigns") as compare:
            result = pilot.pilot(
                ["model-a", "model-b"], self.root / "artifacts", "test-pilot",
                self.objective, execute=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["campaigns"][0]["error_type"], "RuntimeError")
        self.assertNotIn("PROVIDER_SECRET", json.dumps(result))
        self.assertEqual(run.call_count, 1)
        compare.assert_not_called()

    def test_rejects_duplicate_model_and_reused_output(self):
        with patch.object(pilot, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "distinct"):
                pilot.pilot(["same", "same"], self.root / "artifacts", "pilot", self.objective)
            with self.assertRaisesRegex(ValueError, "safe"):
                pilot.pilot(["a", "b"], self.root / "artifacts", "../unsafe", self.objective)
            folder = self.root / "artifacts/pilot"
            folder.mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                pilot.pilot(["a", "b"], self.root / "artifacts", "pilot", self.objective)

    def test_restores_model_environment_on_preflight_error(self):
        with patch.object(pilot, "ROOT", self.root), patch.object(
            pilot, "assess", side_effect=ValueError("secret")
        ), patch.dict(os.environ, {"CHIPRD_LLM_MODEL": "original"}):
            with self.assertRaises(ValueError):
                pilot.pilot(["a", "b"], self.root / "artifacts", "pilot", self.objective)
            self.assertEqual(os.environ["CHIPRD_LLM_MODEL"], "original")


if __name__ == "__main__":
    unittest.main()
