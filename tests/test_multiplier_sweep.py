"""Unit tests for fail-closed multiplier architecture comparison."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import multiplier_sweep


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MultiplierSweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.architectures = [
            {"id": "baseline", "label": "Baseline", "path": "rtl/baseline.sv",
             "family": "operator", "description": "Reference operator"},
            {"id": "small_slow", "label": "Small", "path": "rtl/small.sv",
             "family": "booth", "description": "Smaller, slower"},
            {"id": "balanced", "label": "Balanced", "path": "rtl/balanced.sv",
             "family": "booth", "description": "Balanced candidate"},
        ]
        (self.root / "rtl").mkdir()
        for row in self.architectures:
            (self.root / row["path"]).write_text(f"module int_mul16; // {row['id']}\nendmodule\n")
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps({
            "schema_version": "1.0.0", "task_id": "multiplier.signed16x16.v1",
            "top_module": "int_mul16", "architectures": self.architectures,
        }))
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.lib_hash = "a" * 64
        self.gold_hash = digest(self.root / "rtl/baseline.sv")
        metrics = {
            "baseline": (10.0, 5.0),
            "small_slow": (8.0, 6.0),
            "balanced": (9.0, 4.0),
        }
        for row in self.architectures:
            arch_id = row["id"]
            area, delay = metrics[arch_id]
            base = self.evidence / arch_id
            for name in ("functional", "formal", "ppa", "timing", "ppa/candidate"):
                (base / name).mkdir(parents=True, exist_ok=True)
            rtl = self.root / row["path"]
            rtl_hash = digest(rtl)
            netlist = base / "ppa" / "candidate" / "mapped.v"
            netlist.write_text(f"mapped {arch_id}\n")
            net_hash = digest(netlist)
            (base / "functional" / "report.json").write_text(json.dumps({
                "task_id": "multiplier.signed16x16.v1", "status": "pass",
                "functional_status": "pass", "candidate_sha256": rtl_hash,
                "seed": 20261116, "random_vector_count": 2048, "edge_vector_count": 72,
                "tested_vectors": 2120,
            }))
            (base / "formal" / "equivalence_report.json").write_text(json.dumps({
                "task_id": "multiplier.signed16x16.v1", "status": "pass",
                "proof_status": "equivalent", "semantics": "combinational_all_inputs",
                "reference_sha256": self.gold_hash, "candidate_sha256": rtl_hash,
            }))
            (base / "ppa" / "ppa_report.json").write_text(json.dumps({
                "report_type": "chiprd_liberty_mapped_area_comparison",
                "status": "pass", "platform_label": "nangate45-reference",
                "top_module": "int_mul16", "library": {"sha256": self.lib_hash},
                "candidate": {"sha256": rtl_hash, "mapping": {
                    "status": "pass", "source_sha256": rtl_hash,
                    "mapped_netlist_sha256": net_hash,
                    "mapped_cell_area": area, "mapped_cell_count": int(area * 10),
                }},
            }))
            (base / "timing" / "combinational_timing_report.json").write_text(json.dumps({
                "report_type": "chiprd_combinational_pre_layout_timing",
                "status": "pass",
                "timing_mode": "input_to_output_max_delay_no_clock_no_parasitics",
                "top_module": "int_mul16",
                "inputs": {"candidate": {"sha256": rtl_hash},
                           "liberty": {"sha256": self.lib_hash}},
                "constraints": {"input_ports": ["a", "b"], "output_ports": ["product"],
                                "max_delay_ns": 10.0, "input_transition_ns": 0.05,
                                "output_load_pf": 0.002},
                "candidate": {"status": "pass", "mapping": {
                    "status": "pass", "source_sha256": rtl_hash,
                    "mapped_netlist_sha256": net_hash,
                    "mapped_cell_area": area, "mapped_cell_count": int(area * 10),
                }, "timing": {
                    "critical_path_reported": True, "meets_max_delay": True,
                    "estimated_worst_path_delay_ns": delay,
                    "worst_max_delay_slack_ns": 10.0 - delay,
                    "critical_startpoint": "b[1]", "critical_endpoint": "product[30]",
                }},
            }))
        self.output = self.root / "comparison"
        self.patch_root = patch.object(multiplier_sweep, "ROOT", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return multiplier_sweep.build_comparison(
            self.catalog, self.evidence, self.output
        )

    def test_builds_pareto_frontier_and_baseline_deltas(self):
        with self.patch_root:
            report = self.build()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["pareto_frontier"], ["small_slow", "balanced"])
        rows = {row["id"]: row for row in report["architectures"]}
        self.assertFalse(rows["baseline"]["pareto_optimal"])
        self.assertEqual(rows["small_slow"]["area_delta_vs_baseline_percent"], -20.0)
        self.assertEqual(rows["balanced"]["delay_delta_vs_baseline_percent"], -20.0)

    def test_rejects_different_liberty_hash(self):
        path = self.evidence / "balanced" / "timing" / "combinational_timing_report.json"
        data = json.loads(path.read_text())
        data["inputs"]["liberty"]["sha256"] = "b" * 64
        path.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "Liberty hashes differ"):
            self.build()

    def test_rejects_candidate_source_hash_mismatch(self):
        path = self.evidence / "small_slow" / "formal" / "equivalence_report.json"
        data = json.loads(path.read_text())
        data["candidate_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "formal report candidate hash"):
            self.build()

    def test_rejects_different_timing_constraints(self):
        path = self.evidence / "balanced" / "timing" / "combinational_timing_report.json"
        data = json.loads(path.read_text())
        data["constraints"]["input_transition_ns"] = 0.1
        path.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "input_transition_ns differs"):
            self.build()

    def test_rejects_passing_timing_with_different_mapped_netlist(self):
        path = self.evidence / "balanced" / "timing" / "combinational_timing_report.json"
        data = json.loads(path.read_text())
        data["candidate"]["mapping"]["mapped_netlist_sha256"] = "c" * 64
        path.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "different mapped netlists"):
            self.build()

    def test_refuses_output_overwrite(self):
        with self.patch_root:
            self.build()
            with self.assertRaises(FileExistsError):
                self.build()


if __name__ == "__main__":
    unittest.main()
