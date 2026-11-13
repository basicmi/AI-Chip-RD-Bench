import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.search import SearchBudget, load_proposals, run_search


def write_pool(root: Path, entries: list[dict]) -> Path:
    (root / "baseline.sv").write_text("module baseline; endmodule\n")
    (root / "variant_a.sv").write_text("module variant_a; endmodule\n")
    (root / "variant_b.sv").write_text("module variant_b; endmodule\n")
    path = root / "pool.json"
    path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "task_id": "cordic.rotation.q14.v1",
        "proposals": entries,
    }))
    return path


def fake_execution(candidate_id: str, cells: int, status: str = "pass") -> dict:
    gates = {name: status for name in (
        "functional_synthesis", "mutation", "formal",
        "formal_negative_control", "equivalence", "equivalence_selftest",
    )}
    return {
        "status": status,
        "report": {
            "status": status,
            "candidate_snapshot_intact": True,
            "checks": {name: {"status": value} for name, value in gates.items()},
            "result_signature": {
                "functional_synthesis": {
                    "synthesis": {"generic_cell_count": cells}
                }
            },
        },
        "elapsed_seconds": 0.01,
        "exit_code": 0,
        "timed_out": False,
    }


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.mkdir(exist_ok=True)
        self.baseline = {"candidate_id": "baseline", "candidate_path": "baseline.sv",
                         "generation": 0, "predicted_improvement": 0.0}
        self.pool = write_pool(self.root, [self.baseline])

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_versioned_pool_and_hashes_candidates(self):
        with patch("chiprd.search.ROOT", self.root):
            proposals, pool_hash = load_proposals(self.pool, SearchBudget())
        self.assertEqual([item.candidate_id for item in proposals], ["baseline"])
        self.assertEqual(len(proposals[0].candidate_sha256), 64)
        self.assertEqual(len(pool_hash), 64)

    def test_rejects_path_traversal(self):
        pool = write_pool(self.root, [{
            "candidate_id": "escape", "candidate_path": "../outside.sv",
            "generation": 0,
        }])
        with patch("chiprd.search.ROOT", self.root):
            with self.assertRaises(ValueError):
                load_proposals(pool, SearchBudget())

    def test_rejects_unknown_parent_and_generation_mismatch(self):
        pool = write_pool(self.root, [{
            "candidate_id": "orphan", "candidate_path": "variant_a.sv",
            "parent_id": "missing", "generation": 1,
        }])
        with patch("chiprd.search.ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "unknown parent_id"):
                load_proposals(pool, SearchBudget())

    def test_rejects_oversized_candidate(self):
        pool = write_pool(self.root, [self.baseline])
        with patch("chiprd.search.ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "exceeds max_candidate_bytes"):
                load_proposals(pool, SearchBudget(max_candidate_bytes=1))

    def test_budget_caps_number_of_experiments(self):
        entries = [
            self.baseline,
            {"candidate_id": "variant_a", "candidate_path": "variant_a.sv",
             "generation": 0, "predicted_improvement": 1.0},
            {"candidate_id": "variant_b", "candidate_path": "variant_b.sv",
             "generation": 0, "predicted_improvement": 2.0},
        ]
        pool = write_pool(self.root, entries)
        with patch("chiprd.search.ROOT", self.root), patch(
            "chiprd.search._run_sealed_experiment",
            side_effect=[
                fake_execution("variant_b", 120),
                fake_execution("variant_a", 130),
            ],
        ) as runner:
            report = run_search(
                pool, self.root / "search", strategy="greedy",
                budget=SearchBudget(max_experiments=2, max_wall_clock_seconds=30),
            )
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(report["budget_consumed"]["experiments"], 2)
        self.assertTrue(report["budget_exhausted"])
        self.assertEqual(report["best_candidate"]["candidate_id"], "variant_b")
        self.assertEqual(report["stop_reason"], "max_experiments")

    def test_greedy_orders_by_agent_priority_not_claimed_measured_score(self):
        entries = [
            self.baseline,
            {"candidate_id": "variant_a", "candidate_path": "variant_a.sv",
             "generation": 0, "predicted_improvement": 1.0},
            {"candidate_id": "variant_b", "candidate_path": "variant_b.sv",
             "generation": 0, "predicted_improvement": 2.0},
        ]
        pool = write_pool(self.root, entries)
        seen = []

        def execute(candidate, output, seed, timeout):
            seen.append(candidate.name)
            return fake_execution(candidate.stem, 100 if candidate.stem == "variant_a" else 200)

        with patch("chiprd.search.ROOT", self.root), patch(
            "chiprd.search._run_sealed_experiment", side_effect=execute
        ):
            run_search(pool, self.root / "search", strategy="greedy",
                       budget=SearchBudget(max_experiments=3))
        self.assertEqual(seen, ["variant_b.sv", "variant_a.sv", "baseline.sv"])

    def test_evolutionary_only_evaluates_children_of_elite_feasible_parent(self):
        entries = [
            self.baseline,
            {"candidate_id": "other_root", "candidate_path": "variant_a.sv",
             "generation": 0, "predicted_improvement": 0.0},
            {"candidate_id": "child_best", "candidate_path": "variant_b.sv",
             "parent_id": "baseline", "generation": 1, "predicted_improvement": 0.0},
            {"candidate_id": "child_other", "candidate_path": "variant_a.sv",
             "parent_id": "other_root", "generation": 1, "predicted_improvement": 0.0},
        ]
        pool = write_pool(self.root, entries)
        seen = []

        def execute(candidate, output, seed, timeout):
            seen.append(candidate.name)
            cells = 100 if candidate.name == "baseline.sv" else 200
            return fake_execution(candidate.stem, cells)

        with patch("chiprd.search.ROOT", self.root), patch(
            "chiprd.search._run_sealed_experiment", side_effect=execute
        ):
            report = run_search(
                pool, self.root / "search", strategy="evolutionary",
                budget=SearchBudget(max_experiments=4, elite_parent_fraction=0.5),
            )
        self.assertEqual(seen, ["baseline.sv", "variant_a.sv", "variant_b.sv"])
        self.assertEqual(report["best_candidate"]["candidate_id"], "baseline")
        self.assertEqual(
            [item["candidate_id"] for item in report["skipped"]],
            ["child_other"],
        )

    def test_children_of_failed_parents_are_not_evaluated(self):
        entries = [
            self.baseline,
            {"candidate_id": "child", "candidate_path": "variant_a.sv",
             "parent_id": "baseline", "generation": 1},
        ]
        pool = write_pool(self.root, entries)
        with patch("chiprd.search.ROOT", self.root), patch(
            "chiprd.search._run_sealed_experiment",
            return_value=fake_execution("baseline", 100, status="fail"),
        ) as runner:
            report = run_search(
                pool, self.root / "search", strategy="evolutionary",
                budget=SearchBudget(max_experiments=3),
            )
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(report["skipped"][0]["reason"], "parent_not_selected_or_not_feasible")
        self.assertIsNone(report["best_candidate"])

    def test_random_strategy_is_reproducible_for_same_seed(self):
        entries = [
            self.baseline,
            {"candidate_id": "variant_a", "candidate_path": "variant_a.sv",
             "generation": 0},
            {"candidate_id": "variant_b", "candidate_path": "variant_b.sv",
             "generation": 0},
        ]
        pool = write_pool(self.root, entries)

        def run_once(name):
            seen = []

            def execute(candidate, output, seed, timeout):
                seen.append(candidate.stem)
                return fake_execution(candidate.stem, 100)

            with patch("chiprd.search.ROOT", self.root), patch(
                "chiprd.search._run_sealed_experiment", side_effect=execute
            ):
                run_search(pool, self.root / name, strategy="random", seed=19,
                           budget=SearchBudget(max_experiments=3))
            return seen

        self.assertEqual(run_once("random_a"), run_once("random_b"))

    def test_rejects_campaign_output_overwrite(self):
        output = self.root / "search"
        output.mkdir()
        (output / "search_report.json").write_text("{}")
        with patch("chiprd.search.ROOT", self.root):
            with self.assertRaises(FileExistsError):
                run_search(self.pool, output)

    def test_invalid_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            SearchBudget(max_experiments=0).validate()


if __name__ == "__main__":
    unittest.main()
