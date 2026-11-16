"""Tests for cross-artifact multiplier evidence binding."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import multiplier_evidence as evidence


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MultiplierEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "int_mul16.sv"
        self.candidate.write_text(
            "module int_mul16(input signed [15:0] a,b, output signed [31:0] product);\n"
            "assign product = a * b;\nendmodule\n", encoding="utf-8"
        )
        self.functional_dir = self.root / "functional"
        self.formal_dir = self.root / "formal"
        self.mutation_dir = self.root / "mutation"
        self.ppa_dir = self.root / "ppa"
        self.timing_dir = self.root / "timing"
        for folder in (self.functional_dir, self.formal_dir, self.mutation_dir,
                       self.ppa_dir / "candidate", self.timing_dir / "candidate"):
            folder.mkdir(parents=True)
        (self.ppa_dir / "candidate" / "mapped.v").write_text("mapped netlist\n")
        (self.timing_dir / "candidate" / "mapped.v").write_text("mapped netlist\n")
        self.source_hash = sha(self.candidate)
        self.netlist_hash = sha(self.ppa_dir / "candidate" / "mapped.v")
        self.lib_hash = "a" * 64

        self.functional = self.functional_dir / "report.json"
        self.functional.write_text(json.dumps({
            "report_type": "chiprd_multiplier_experimental_evaluation",
            "candidate_sha256": self.source_hash, "status": "pass",
            "functional_status": "pass", "generic_synthesis_status": "pass",
            "tested_vectors": 136,
        }))
        self.formal = self.formal_dir / "equivalence_report.json"
        self.formal.write_text(json.dumps({
            "task_id": "multiplier.signed16x16.v1", "kind": "combinational_equivalence",
            "candidate_sha256": self.source_hash, "proof_status": "equivalent", "status": "pass",
        }))
        self.mutation = self.mutation_dir / "mutation_report.json"
        self.mutation.write_text(json.dumps({
            "task_id": "multiplier.signed16x16.v1", "kind": "mutation_sensitivity",
            "candidate_sha256": self.source_hash, "status": "pass",
            "detected_count": 2, "mutation_score": 1.0,
            "mutations": [{"outcome": "detected"}, {"outcome": "detected"}],
        }))
        self.ppa = self.ppa_dir / "ppa_report.json"
        self.ppa.write_text(json.dumps({
            "report_type": "chiprd_liberty_mapped_area_comparison", "status": "pass",
            "platform_label": "nangate45-reference", "top_module": "int_mul16",
            "candidate": {"sha256": self.source_hash, "mapping": {
                "status": "pass", "source_sha256": self.source_hash,
                "mapped_netlist_sha256": self.netlist_hash,
                "mapped_cell_area": 1812.258, "mapped_cell_count": 1593,
            }},
            "library": {"sha256": self.lib_hash},
        }))
        self.timing = self.timing_dir / "combinational_timing_report.json"
        self.timing.write_text(json.dumps({
            "report_type": "chiprd_combinational_pre_layout_timing", "status": "pass",
            "timing_mode": "input_to_output_max_delay_no_clock_no_parasitics",
            "top_module": "int_mul16", "platform_label": "nangate45-reference",
            "inputs": {
                "candidate": {"sha256": self.source_hash},
                "liberty": {"sha256": self.lib_hash},
            },
            "candidate": {"status": "pass", "mapping": {
                "status": "pass", "source_sha256": self.source_hash,
                "mapped_netlist_sha256": self.netlist_hash,
                "mapped_cell_area": 1812.258, "mapped_cell_count": 1593,
            }, "timing": {
                "critical_path_reported": True, "meets_max_delay": True,
                "estimated_worst_path_delay_ns": 1.47236,
                "worst_max_delay_slack_ns": 8.52764,
                "critical_startpoint": "b[1]", "critical_endpoint": "product[30]",
            }},
            "constraints": {
                "max_delay_ns": 10.0, "input_transition_ns": 0.05,
                "output_load_pf": 0.002,
            },
        }))
        self.output = self.root / "manifest"
        self.args = (
            self.candidate, self.functional, self.formal, self.mutation,
            self.ppa, self.timing, self.output,
        )
        self.patch_root = patch.object(evidence, "ROOT", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_builds_and_verifies_complete_manifest(self):
        with self.patch_root:
            manifest = evidence.build_manifest(*self.args)
            path = self.output / "multiplier_evidence_manifest.json"
            result = evidence.verify_manifest(path)
        self.assertEqual(manifest["status"], "pass")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["verified_evidence_count"], 7)
        self.assertEqual(manifest["summary"]["mapped_cell_area"], 1812.258)
        self.assertEqual(manifest["summary"]["estimated_worst_path_delay_ns"], 1.47236)

    def test_rejects_candidate_hash_mismatch(self):
        data = json.loads(self.functional.read_text())
        data["candidate_sha256"] = "0" * 64
        self.functional.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "functional report candidate hash"):
            evidence.build_manifest(*self.args)

    def test_rejects_different_mapped_netlists(self):
        (self.timing_dir / "candidate" / "mapped.v").write_text("different mapping\n")
        data = json.loads(self.timing.read_text())
        data["candidate"]["mapping"]["mapped_netlist_sha256"] = sha(
            self.timing_dir / "candidate" / "mapped.v"
        )
        self.timing.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "mapped netlists differ"):
            evidence.build_manifest(*self.args)

    def test_rejects_failed_mutation_row_even_when_report_claims_pass(self):
        data = json.loads(self.mutation.read_text())
        data["mutations"][0]["outcome"] = "survived"
        self.mutation.write_text(json.dumps(data))
        with self.patch_root, self.assertRaisesRegex(ValueError, "mutations survived"):
            evidence.build_manifest(*self.args)

    def test_detects_post_manifest_report_tampering(self):
        with self.patch_root:
            evidence.build_manifest(*self.args)
            data = json.loads(self.functional.read_text())
            data["tested_vectors"] = 999
            self.functional.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "functional artifact hash mismatch"):
                evidence.verify_manifest(self.output / "multiplier_evidence_manifest.json")

    def test_rejects_report_outside_repository_root(self):
        outside = Path(self.tmp.name).parent / "outside-evidence.json"
        outside.write_text("{}")
        try:
            with self.patch_root, self.assertRaisesRegex(ValueError, "inside repository root"):
                evidence.build_manifest(
                    self.candidate, outside, self.formal, self.mutation,
                    self.ppa, self.timing, self.output,
                )
        finally:
            outside.unlink(missing_ok=True)

    def test_refuses_overwrite(self):
        with self.patch_root:
            evidence.build_manifest(*self.args)
            with self.assertRaises(FileExistsError):
                evidence.build_manifest(*self.args)


if __name__ == "__main__":
    unittest.main()
