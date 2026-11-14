"""Offline regression of optional timing-constrained PPA Agent selection."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.agent_loop import AgentLoopBudget, run_agent_loop
from tests.test_agent_loop import fake_execution, write_fixture


class FakePPA:
    def __init__(self, baseline_area=100.0, candidate_area=90.0,
                 candidate_wns=0.2, candidate_status="pass"):
        self.baseline_area = baseline_area
        self.candidate_area = candidate_area
        self.candidate_wns = candidate_wns
        self.candidate_status = candidate_status
        self.calls = []

    def validate(self):
        return {"objective": "test", "inputs": {}, "clock_period_ns": 10}

    def evaluate(self, candidate, reference, output):
        self.calls.append(candidate.name)
        baseline = candidate.name == "cordic_rotator.sv"
        area = self.baseline_area if baseline else self.candidate_area
        wns = 0.5 if baseline else self.candidate_wns
        status = "pass" if baseline else self.candidate_status
        return {"status": status, "feasible": status == "pass" and wns >= 0,
                "mapped_cell_area": area, "worst_setup_slack_ns": wns}


@unittest.skipUnless(shutil.which("git"), "git required by patch intake")
class TimingAwareLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.command = write_fixture(self.root)
        self.output = self.root / "artifacts/agent-loops/test-ppa"

    def tearDown(self):
        self.tmp.cleanup()

    def run_loop(self, objective, *, candidate_cells=950, candidate_gate_pass=True):
        def sealed(candidate, output, seed, timeout):
            value = fake_execution(candidate, 1000 if candidate.name == "cordic_rotator.sv" else candidate_cells)
            if candidate.name != "cordic_rotator.sv" and not candidate_gate_pass:
                value["report"]["checks"]["equivalence"]["status"] = "fail"
                value["status"] = "fail"
            return value
        with patch("chiprd.agent_loop.ROOT", self.root), patch(
            "chiprd.patches.ROOT", self.root
        ), patch("chiprd.agent_loop._run_sealed_experiment", side_effect=sealed):
            return run_agent_loop(self.command, self.output,
                                  budget=AgentLoopBudget(max_iterations=2),
                                  seed=123, loop_id="test-ppa", ppa_objective=objective)

    def test_area_beats_generic_cell_count_when_timing_feasible(self):
        # Candidate uses MORE generic cells but LESS technology-mapped area.
        objective = FakePPA(candidate_area=90.0, candidate_wns=0.2)
        result = self.run_loop(objective, candidate_cells=1100)
        self.assertEqual(result["objective_mode"], "timing_constrained_mapped_area")
        self.assertTrue(result["improved_vs_baseline"])
        self.assertEqual(result["best_candidate"]["mapped_cell_area"], 90)
        self.assertEqual(result["best_candidate"]["generic_cell_count"], 1100)
        self.assertEqual(len(objective.calls), 2)
        request = json.loads((self.output / "agent-io/iteration-002/request.json").read_text())
        self.assertEqual(request["previous_experiment"]["ppa"]["mapped_cell_area"], 90)
        self.assertEqual(request["objective"]["metric"], "minimize Liberty-mapped standard-cell area")

    def test_timing_failure_not_selected_even_with_lower_area(self):
        result = self.run_loop(FakePPA(candidate_area=60, candidate_wns=-0.1))
        self.assertFalse(result["improved_vs_baseline"])
        self.assertEqual(result["best_candidate"]["candidate_id"], "baseline")
        self.assertFalse(result["experiments"][-1]["ppa"]["feasible"])

    def test_ppa_error_never_becomes_success(self):
        result = self.run_loop(FakePPA(candidate_area=50, candidate_status="error"))
        self.assertEqual(result["best_candidate"]["candidate_id"], "baseline")
        self.assertFalse(result["improved_vs_baseline"])

    def test_failed_formal_gates_skip_timing(self):
        objective = FakePPA(candidate_area=50)
        result = self.run_loop(objective, candidate_gate_pass=False)
        self.assertEqual(len(objective.calls), 1)
        self.assertEqual(result["experiments"][-1]["ppa"]["status"], "skipped")
        self.assertEqual(result["best_candidate"]["candidate_id"], "baseline")

    def test_larger_area_not_selected_despite_better_generic_cells(self):
        result = self.run_loop(FakePPA(candidate_area=120, candidate_wns=0.7))
        self.assertFalse(result["improved_vs_baseline"])
        self.assertEqual(result["best_candidate"]["candidate_id"], "baseline")


if __name__ == "__main__":
    unittest.main()
