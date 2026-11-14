"""Regression tests for timing-constrained synthesis-area scorecards."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from chiprd.ppa_scorecard import score_reports


HASH = "a" * 64


def fixture(candidate_area=90.0, candidate_wns=0.3, *, candidate_hash=None):
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_pre_layout_timing_comparison",
        "status": "pass",
        "timing_mode": "ideal_clock_pre_layout_no_parasitics",
        "clock_period_ns": 10.0,
        "io_delay_ns": 0.5,
        "sdc_sha256": HASH,
        "inputs": {
            name: {"filename": name, "sha256": (candidate_hash or "b" * 64)
                   if name == "candidate" else HASH}
            for name in ("candidate", "reference", "liberty", "tech_lef", "cells_lef")
        },
        "candidate": {
            "status": "pass", "openroad_version": "OpenROAD 1.0",
            "mapping": {"mapped_cell_area": candidate_area, "yosys_version": "Yosys 0.1"},
            "timing": {"worst_setup_slack_ns": candidate_wns,
                       "meets_provided_clock_period": candidate_wns >= 0,
                       "critical_path_reported": True},
        },
        "reference": {
            "status": "pass", "openroad_version": "OpenROAD 1.0",
            "mapping": {"mapped_cell_area": 100.0, "yosys_version": "Yosys 0.1"},
            "timing": {"worst_setup_slack_ns": 0.5,
                       "meets_provided_clock_period": True,
                       "critical_path_reported": True},
        },
        "comparison": {
            "candidate_minus_reference_area": candidate_area - 100.0,
            "candidate_minus_reference_wns_ns": round(candidate_wns - 0.5, 6),
            "candidate_meets_timing": candidate_wns >= 0,
            "reference_meets_timing": True,
        },
    }


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index = 0

    def tearDown(self):
        self.temp.cleanup()

    def write(self, report):
        self.index += 1
        path = self.root / f"timing-{self.index}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_feasibility_over_unconstrained_area(self):
        bad = self.write(fixture(70, -0.25, candidate_hash="1" * 64))
        good = self.write(fixture(90, 0.2, candidate_hash="2" * 64))
        result = score_reports([bad, good])
        self.assertEqual(result["feasible_count"], 1)
        self.assertEqual(result["selected_candidate_sha256"], "2" * 64)
        self.assertFalse(result["candidates"][0]["pareto_nondominated"])
        self.assertEqual(result["candidates"][1]["area_reduction_percent"], 10.0)

    def test_no_feasible_design(self):
        result = score_reports([self.write(fixture(50, -1))])
        self.assertIsNone(result["selected_candidate_sha256"])
        self.assertEqual(result["feasible_count"], 0)

    def test_pareto_frontier_and_min_area_selection(self):
        reports = [
            self.write(fixture(90, 0.2, candidate_hash="1" * 64)),
            self.write(fixture(95, 0.5, candidate_hash="2" * 64)),
            self.write(fixture(98, 0.1, candidate_hash="3" * 64)),
        ]
        result = score_reports(reports)
        self.assertEqual([x["pareto_nondominated"] for x in result["candidates"]],
                         [True, True, False])
        self.assertEqual(result["selected_candidate_sha256"], "1" * 64)

    def test_rejects_mismatched_sdc_and_library(self):
        a = self.write(fixture())
        b = fixture()
        b["sdc_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "mismatch"):
            score_reports([a, self.write(b)])
        b = fixture()
        b["inputs"]["liberty"]["sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "mismatch"):
            score_reports([a, self.write(b)])

    def test_rejects_mismatched_tool_and_reference(self):
        a = self.write(fixture())
        b = fixture()
        b["candidate"]["mapping"]["yosys_version"] = "Yosys different"
        with self.assertRaisesRegex(ValueError, "versions differ"):
            score_reports([self.write(b)])
        b = fixture()
        b["reference"]["mapping"]["mapped_cell_area"] = 101.0
        b["comparison"]["candidate_minus_reference_area"] = -11.0
        with self.assertRaisesRegex(ValueError, "reference result mismatch"):
            score_reports([a, self.write(b)])

    def test_rejects_forged_pass_and_delta(self):
        b = fixture()
        b["candidate"]["timing"]["critical_path_reported"] = False
        with self.assertRaisesRegex(ValueError, "critical path"):
            score_reports([self.write(b)])
        b = fixture()
        b["candidate"]["timing"]["meets_provided_clock_period"] = False
        with self.assertRaisesRegex(ValueError, "contradicts"):
            score_reports([self.write(b)])
        b = fixture()
        b["comparison"]["candidate_minus_reference_area"] = 999
        with self.assertRaisesRegex(ValueError, "delta inconsistent"):
            score_reports([self.write(b)])

    def test_rejects_bad_status_and_nonfinite_values(self):
        b = fixture()
        b["status"] = "error"
        with self.assertRaisesRegex(ValueError, "did not pass"):
            score_reports([self.write(b)])
        b = fixture()
        b["candidate"]["mapping"]["mapped_cell_area"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            score_reports([self.write(b)])

    def test_rejects_symlink_and_excessive_inputs(self):
        source = self.write(fixture())
        alias = self.root / "alias.json"
        alias.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            score_reports([alias])
        with self.assertRaisesRegex(ValueError, "at most 100"):
            score_reports([source] * 101)
        with self.assertRaisesRegex(ValueError, "at least one"):
            score_reports([])


if __name__ == "__main__":
    unittest.main()
