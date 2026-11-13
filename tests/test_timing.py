"""Offline contracts for the pre-layout OpenSTA timing runner."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import timing


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "candidate.sv"
        self.reference = self.root / "reference.sv"
        self.liberty = self.root / "cells.lib"
        self.tech = self.root / "tech.lef"
        self.cells = self.root / "cells.lef"
        self.output = self.root / "out"
        for path, content in (
            (self.candidate, "module cordic_rotator; endmodule\n"),
            (self.reference, "module cordic_rotator; endmodule\n"),
            (self.liberty, "library (fixture) { cell (INV_X1) {area: 1.0;} }\n"),
            (self.tech, "VERSION 5.8 ;\n"),
            (self.cells, "VERSION 5.8 ;\n"),
        ):
            path.write_text(content, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self):
        return timing.compare_timing(
            self.candidate, self.reference, self.liberty,
            self.tech, self.cells, self.output,
            clock_period_ns=10, io_delay_ns=0.5,
        )

    def test_constraints_clock_inputs_outputs(self):
        sdc = timing._constraints(10, 0.5)
        self.assertIn("create_clock -name core_clk -period 10.000000", sdc)
        self.assertIn("input_valid angle_in*", sdc)
        self.assertIn("output_valid x_out* y_out*", sdc)
        self.assertIn("set_load 0.002", sdc)

    def test_finite_slack_and_critical_path_required(self):
        data = (
            "Startpoint: stage_reg\nEndpoint: x_out_reg\n"
            "slack (MET) 0.45\nCHIPRD_WNS_NS 0.4500\n"
        )
        self.assertEqual(timing._parse_timing(data)["worst_setup_slack_ns"], 0.45)
        self.assertTrue(timing._parse_timing(data)["meets_provided_clock_period"])
        neg = data.replace("0.4500", "-0.2500")
        self.assertFalse(timing._parse_timing(neg)["meets_provided_clock_period"])
        with self.assertRaises(RuntimeError):
            timing._parse_timing("CHIPRD_WNS_NS 0.5")
        with self.assertRaises(RuntimeError):
            timing._parse_timing("Startpoint: x\nEndpoint: y\nCHIPRD_WNS_NS INF")

    def test_compare_same_constraints_and_area(self):
        calls = []
        def mock_one(source, lib, tech, cells, folder, sdc, known):
            folder.mkdir()
            calls.append((source.name, sdc, known))
            idx = len(calls)
            return {
                "status": "pass",
                "mapping": {"mapped_cell_area": 90.0 if idx == 1 else 100.0},
                "timing": {"worst_setup_slack_ns": 0.3 if idx == 1 else 0.5},
            }
        with patch.object(timing, "_analyze_one", side_effect=mock_one):
            report = self.call()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["comparison"]["candidate_minus_reference_wns_ns"], -0.2)
        self.assertEqual(report["comparison"]["candidate_minus_reference_area"], -10.0)
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(calls[0][2], {"INV_X1"})
        self.assertEqual(json.loads((self.output / "timing_report.json").read_text())["status"], "pass")

    def test_mapping_error_does_not_become_pass(self):
        with patch.object(timing, "_analyze_one", side_effect=RuntimeError("timing not evaluated")):
            report = self.call()
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("comparison", report)

    def test_rejects_overwrite_and_invalid_constraints(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.call()
        self.output.rmdir()
        for period, io in ((-1, 0.5), (10, 6), (float("inf"), 0.5)):
            with self.assertRaises(ValueError):
                timing.compare_timing(self.candidate, self.reference, self.liberty,
                                      self.tech, self.cells, self.output,
                                      clock_period_ns=period, io_delay_ns=io)

    def test_no_symlink_inputs(self):
        alias = self.root / "alias.lib"
        alias.symlink_to(self.liberty)
        with self.assertRaisesRegex(ValueError, "liberty"):
            timing.compare_timing(self.candidate, self.reference, alias,
                                  self.tech, self.cells, self.output)

    def test_executable_missing_fails_closed(self):
        folder = self.root / "mapped"
        with patch.object(timing.ppa, "_map_one", return_value={"mapped_cell_area": 12.0}):
            with patch.object(timing.shutil, "which", return_value=None):
                folder.mkdir()
                with self.assertRaisesRegex(RuntimeError, "OpenROAD unavailable"):
                    timing._analyze_one(self.candidate, self.liberty, self.tech,
                                        self.cells, folder, "create_clock ...", {"INV_X1"})


if __name__ == "__main__":
    unittest.main()
