"""Task registry tests: planned tracks are visible but never counted runnable."""
import json
import tempfile
import unittest
from pathlib import Path

from chiprd.task_registry import validate_registry


class TaskRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks/cordic").mkdir(parents=True)
        for name in ("spec.md", "golden.sv", "reference.sv"):
            (self.root / "tasks/cordic" / name).write_text("// fixture\n")
        self.registry = {
            "schema_version": "1.0.0",
            "registry_id": "ai-chip-rd-bench.tasks",
            "tracks": [
                {"track_id": "legacy", "title": "Legacy rewrite",
                 "description": "Legacy RTL reimplementation", "maturity": "planned"},
                {"track_id": "feature", "title": "IP feature evolution",
                 "description": "Feature and optimization", "maturity": "active"},
                {"track_id": "port", "title": "IP porting",
                 "description": "Technology porting", "maturity": "planned"},
            ],
            "tasks": [{
                "task_id": "cordic.v1", "track_id": "feature",
                "title": "CORDIC", "maturity": "runnable",
                "spec": "tasks/cordic/spec.md",
                "golden_rtl": "tasks/cordic/golden.sv",
                "reference_rtl": "tasks/cordic/reference.sv",
                "interface": {"top_module": "cordic", "latency_max_cycles": 32},
                "evaluation": {key: "declared" for key in (
                    "functional", "protocol_formal", "sequential_equivalence",
                    "mutation", "synthesis", "ppa")},
                "objective": {"primary": "minimize_area"},
            }],
        }
        self.path = self.root / "tasks/registry.json"
        self.path.write_text(json.dumps(self.registry))

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_only_runnable_tasks(self):
        result = validate_registry(self.path, root=self.root)
        self.assertEqual(result["track_count"], 3)
        self.assertEqual(result["task_count"], 1)
        self.assertEqual(result["runnable_task_count"], 1)

    def test_reject_duplicate_ids_and_unknown_track(self):
        self.registry["tracks"].append(dict(self.registry["tracks"][0]))
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_registry(self.path, root=self.root)
        self.registry["tracks"].pop()
        self.registry["tasks"][0]["track_id"] = "unknown"
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "unknown track"):
            validate_registry(self.path, root=self.root)

    def test_reject_missing_runnable_artifact(self):
        self.registry["tasks"][0]["golden_rtl"] = "tasks/cordic/missing.sv"
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_registry(self.path, root=self.root)

    def test_reject_incomplete_evaluator_contract(self):
        del self.registry["tasks"][0]["evaluation"]["mutation"]
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "evaluator"):
            validate_registry(self.path, root=self.root)

    def test_reject_path_escape_and_symlink(self):
        self.registry["tasks"][0]["spec"] = "../outside.md"
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            validate_registry(self.path, root=self.root)
        link = self.root / "tasks/cordic/link.md"
        link.symlink_to(self.root / "tasks/cordic/spec.md")
        self.registry["tasks"][0]["spec"] = "tasks/cordic/link.md"
        self.path.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError, "symlink"):
            validate_registry(self.path, root=self.root)

    def test_planned_task_does_not_require_executable_harness(self):
        self.registry["tasks"][0] = {
            "task_id": "future", "track_id": "legacy",
            "title": "Future task", "maturity": "planned",
        }
        self.path.write_text(json.dumps(self.registry))
        result = validate_registry(self.path, root=self.root)
        self.assertEqual(result["runnable_task_count"], 0)


if __name__ == "__main__":
    unittest.main()
