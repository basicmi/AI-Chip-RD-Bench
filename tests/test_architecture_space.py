"""Tests for the task-bound architecture search-space contract."""
import json
import tempfile
import unittest
from pathlib import Path

from chiprd.architecture_space import validate_architecture_space


class ArchitectureSpaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tasks/cordic").mkdir(parents=True)
        for name in ("baseline.sv", "variant.sv"):
            (self.root / "tasks/cordic" / name).write_text(
                "module cordic_rotator(input logic clk); endmodule\n",
                encoding="utf-8",
            )
        self.payload = {
            "schema_version": "1.0.0",
            "space_id": "cordic-space-v1",
            "task_id": "cordic.rotation.q14.v1",
            "top_module": "cordic_rotator",
            "baseline_candidate_id": "conditional",
            "invariants": {
                "interface_immutable": True,
                "latency_max_cycles": 32,
                "numerical_tolerance_lsb": 32,
            },
            "dimensions": [{
                "id": "datapath_update_form", "type": "enum",
                "values": ["conditional_add_sub", "signed_delta_shared_add"],
            }],
            "candidates": [
                {"candidate_id": "conditional", "rtl": "tasks/cordic/baseline.sv",
                 "configuration": {"datapath_update_form": "conditional_add_sub"}},
                {"candidate_id": "signed_delta", "rtl": "tasks/cordic/variant.sv",
                 "configuration": {"datapath_update_form": "signed_delta_shared_add"}},
            ],
        }
        self.path = self.root / "tasks/cordic/architecture_space.json"
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_validates_candidate_bindings_and_enum_coverage(self):
        result = validate_architecture_space(self.path, root=self.root)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["dimension_count"], 1)
        self.assertEqual(len(result["candidates"][0]["rtl_sha256"]), 64)
        self.assertEqual(result["coverage"]["datapath_update_form"], [
            "conditional_add_sub", "signed_delta_shared_add"
        ])

    def test_rejects_missing_dimension_value_coverage(self):
        self.payload["candidates"].pop()
        self.write()
        with self.assertRaisesRegex(ValueError, "unimplemented declared values"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_candidate_with_missing_or_extra_dimension(self):
        self.payload["candidates"][0]["configuration"] = {}
        self.write()
        with self.assertRaisesRegex(ValueError, "every dimension exactly once"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_unsupported_dimension_value(self):
        self.payload["candidates"][0]["configuration"]["datapath_update_form"] = "magic"
        self.write()
        with self.assertRaisesRegex(ValueError, "unsupported value"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_duplicate_configurations(self):
        self.payload["candidates"][1]["configuration"] = dict(
            self.payload["candidates"][0]["configuration"]
        )
        self.write()
        with self.assertRaisesRegex(ValueError, "Duplicate architecture configuration"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_candidate_path_escape(self):
        self.payload["candidates"][0]["rtl"] = "../../outside.sv"
        self.write()
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_wrong_top_module(self):
        (self.root / "tasks/cordic/variant.sv").write_text(
            "module another_top(input logic clk); endmodule\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "does not declare top module"):
            validate_architecture_space(self.path, root=self.root)

    def test_rejects_baseline_not_in_candidate_pool(self):
        self.payload["baseline_candidate_id"] = "missing"
        self.write()
        with self.assertRaisesRegex(ValueError, "baseline_candidate_id"):
            validate_architecture_space(self.path, root=self.root)


if __name__ == "__main__":
    unittest.main()
