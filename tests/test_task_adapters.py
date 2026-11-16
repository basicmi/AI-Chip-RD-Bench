import json
import tempfile
import unittest
from pathlib import Path

from chiprd.task_adapters import load_task


class TaskAdapterTests(unittest.TestCase):
    def test_loads_cordic_fully_runnable_contract(self):
        task = load_task("cordic.rotation.q14.v1")
        self.assertEqual(task.maturity, "runnable")
        self.assertEqual(task.top_module, "cordic_rotator")
        self.assertEqual(set(task.evaluation), {
            "functional", "protocol_formal", "sequential_equivalence",
            "mutation", "synthesis", "ppa",
        })

    def test_loads_multiplier_as_experimental_not_runnable(self):
        task = load_task("multiplier.signed16x16.v1")
        self.assertEqual(task.maturity, "experimental")
        self.assertEqual(task.top_module, "int_mul16")
        self.assertEqual(set(task.evaluation), {"functional", "synthesis"})

    def test_unknown_task_id_raises_key_error(self):
        with self.assertRaisesRegex(KeyError, "Unknown task_id"):
            load_task("not.a.real.task")

    def test_planned_task_is_rejected_with_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            registry = {
                "schema_version": "1.0.0",
                "registry_id": "fixture.tasks",
                "tracks": [{
                    "track_id": "future_track", "title": "Future",
                    "description": "Not implemented yet", "maturity": "planned",
                }],
                "tasks": [{
                    "task_id": "legacy.ip.future", "track_id": "future_track",
                    "title": "Future task", "maturity": "planned",
                }],
            }
            (root / "tasks/registry.json").write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "not executable"):
                load_task("legacy.ip.future", root=root)


if __name__ == "__main__":
    unittest.main()
