"""Offline scorecard and provider token-usage contract tests."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.agent_loop import _parse_response
from chiprd.benchmark_report import summarize, compare

ADAPTER = Path(__file__).resolve().parents[1] / "examples/agents/llm_agent.py"
spec = importlib.util.spec_from_file_location("chiprd_token_usage_agent", ADAPTER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def fixture(ident="run-a", cells=90, token_usage=None):
    proposal = {
        "iteration": 1, "agent_status": "pass",
        "status": "evaluated",
        "proposal_sha256": "a" * 64,
        "patch": {"patch_id": "candidate-a"},
        "model_id": "test-model",
    }
    if token_usage is not None:
        proposal["usage"] = token_usage
    gates = {
        name: "pass" for name in (
            "functional_synthesis", "mutation", "formal",
            "formal_negative_control", "equivalence", "equivalence_selftest",
        )
    }
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_agent_closed_loop",
        "loop_id": ident,
        "status": "pass",
        "stop_reason": "max_iterations",
        "budget": {"max_iterations": 2},
        "budget_consumed": {"agent_iterations": 1, "candidate_experiments": 2},
        "baseline": {"candidate_id": "baseline", "feasible": True, "generic_cell_count": 100},
        "best_candidate": {
            "candidate_id": "candidate-a", "generic_cell_count": cells
        },
        "iterations": [proposal],
        "experiments": [
            {
                "candidate_id": "baseline", "feasible": True,
                "generic_cell_count": 100, "gate_statuses": gates,
            },
            {
                "candidate_id": "candidate-a", "feasible": True,
                "generic_cell_count": cells, "gate_statuses": gates,
            },
        ],
    }


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, obj, filename="run.json"):
        path = self.folder / filename
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    def test_feasible_improvement_and_full_token_usage(self):
        path = self.write(fixture(token_usage={
            "prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
        }))
        score = summarize(path)
        self.assertEqual(score["candidates"]["proposals_submitted"], 1)
        self.assertEqual(score["candidates"]["feasible"], 1)
        self.assertEqual(score["candidates"]["strictly_improved"], 1)
        self.assertEqual(score["gates"]["equivalence"]["pass_rate"], 1.0)
        self.assertEqual(score["best_improvement_percent"], 10.0)
        self.assertEqual(score["token_usage"]["total_tokens"]["reported_sum"], 140)
        self.assertEqual(score["token_usage"]["total_tokens"]["unreported_calls"], 0)

    def test_missing_tokens_remain_unknown(self):
        score = summarize(self.write(fixture()))
        self.assertIsNone(score["token_usage"]["total_tokens"]["reported_sum"])
        self.assertEqual(score["token_usage"]["total_tokens"]["unreported_calls"], 1)

    def test_infeasible_candidate_has_no_false_improvement(self):
        data = fixture(cells=70)
        data["experiments"][1]["feasible"] = False
        data["experiments"][1]["gate_statuses"]["formal"] = "fail"
        data["best_candidate"] = {
            "candidate_id": "baseline", "generic_cell_count": 100
        }
        score = summarize(self.write(data))
        self.assertEqual(score["candidates"]["feasible"], 0)
        self.assertEqual(score["candidates"]["strictly_improved"], 0)
        self.assertEqual(score["gates"]["formal"]["pass_rate"], 0.0)
        self.assertEqual(score["best_improvement_percent"], 0.0)

    def test_compare_requires_unique_run_identifiers(self):
        p1 = self.write(fixture("run-a"), "a.json")
        p2 = self.write(fixture("run-b"), "b.json")
        comparison = compare([p1, p2])
        self.assertEqual(len(comparison["runs"]), 2)
        with self.assertRaisesRegex(ValueError, "Duplicate run_id"):
            compare([p1, p1])

    def test_invalid_report_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "chiprd_agent_closed_loop"):
            summarize(self.write({"schema_version": "1.0.0"}))


class TokenContractTests(unittest.TestCase):
    def test_provider_usage_is_validated(self):
        self.assertEqual(agent._validated_usage({
            "prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40
        })["total_tokens"], 40)
        for value in (True, -1, 1.5):
            with self.assertRaises(agent.AgentError):
                agent._validated_usage({"total_tokens": value})
        with self.assertRaises(agent.AgentError):
            agent._validated_usage({
                "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 40
            })

    def test_controller_accepts_telemetry_on_patch_or_stop(self):
        response = {
            "schema_version": "1.0.0",
            "action": "stop",
            "rationale": "enough",
            "model_id": "local",
            "usage": {"prompt_tokens": 45, "completion_tokens": 5, "total_tokens": 50},
        }
        parsed = _parse_response(json.dumps(response))
        self.assertEqual(parsed["usage"]["total_tokens"], 50)

    def test_controller_validates_model_generation_parameters(self):
        response = {
            "schema_version": "1.0.0", "action": "stop",
            "model_id": "local",
            "model_config": {
                "temperature": 0.5, "max_tokens": 2048, "timeout_seconds": 30.0,
            },
        }
        self.assertEqual(
            _parse_response(json.dumps(response))["model_config"]["temperature"], 0.5
        )
        for config in (
            {"temperature": 3, "max_tokens": 512, "timeout_seconds": 10},
            {"temperature": True, "max_tokens": 512, "timeout_seconds": 10},
            {"temperature": 0, "max_tokens": True, "timeout_seconds": 10},
            {"temperature": 0, "max_tokens": 512, "timeout_seconds": float("inf")},
            {"temperature": 0, "max_tokens": 512, "timeout_seconds": 10, "api_key": "x"},
        ):
            with self.assertRaises(ValueError):
                _parse_response(json.dumps({
                    "schema_version": "1.0.0", "action": "stop",
                    "model_config": config,
                }))

    def test_controller_rejects_invalid_token_metadata(self):
        for usage in ({"total_tokens": -1}, {"total_tokens": True},
                      {"bad_token_counter": 5},
                      {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4}):
            response = {"schema_version": "1.0.0", "action": "stop", "usage": usage}
            with self.assertRaises(ValueError):
                _parse_response(json.dumps(response))


if __name__ == "__main__":
    unittest.main()
