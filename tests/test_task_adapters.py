import unittest

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

    def test_unknown_or_planned_tasks_are_not_loadable(self):
        with self.assertRaises(KeyError):
            load_task("not.a.real.task")
        with self.assertRaises(ValueError):
            load_task("legacy.ip.future")


if __name__ == "__main__":
    unittest.main()
