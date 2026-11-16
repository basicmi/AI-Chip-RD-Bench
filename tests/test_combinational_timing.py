"""Offline contracts for clockless combinational OpenSTA analysis."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chiprd import combinational_timing as ct


class CombinationalTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "candidate.sv"
        self.liberty = self.root / "cells.lib"
        self.tech = self.root / "tech.lef"
        self.cells = self.root / "cells.lef"
        self.output = self.root / "out"
        for path, data in (
            (self.candidate, "module int_mul16(input [15:0] a,b, output [31:0] product); endmodule\n"),
            (self.liberty, "library (fixture) { cell (INV_X1) { area : 1.0; } }\n"),
            (self.tech, "VERSION 5.8 ;\n"),
            (self.cells, "VERSION 5.8 ;\n"),
        ):
            path.write_text(data, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_constraints_are_clockless_and_explicit(self):
        sdc = ct._constraints(["a", "b"], ["product"], 10, 0.05, 0.002)
        self.assertIn("set_max_delay 10.000000", sdc)
        self.assertIn("-from [get_ports {a b}]", sdc)
        self.assertIn("-to [get_ports {product*}]", sdc)
        self.assertNotIn("create_clock", sdc)
        self.assertIn("set_input_transition 0.050000", sdc)
        self.assertIn("set_load 0.002000", sdc)

    def test_port_names_reject_tcl_injection_and_duplicates(self):
        with self.assertRaises(ValueError):
            ct._ports(["a", "b; exit"], "input_ports")
        with self.assertRaises(ValueError):
            ct._ports(["a", "a"], "input_ports")
        with self.assertRaises(ValueError):
            ct._ports([], "output_ports")

    def test_parse_finite_max_delay_slack_and_path(self):
        log = (
            "Startpoint: input port a\nEndpoint: output port product[0]\n"
            "CHIPRD_WNS_NS 2.250000\n"
        )
        result = ct._parse_timing(log, 10.0)
        self.assertEqual(result["worst_max_delay_slack_ns"], 2.25)
        self.assertEqual(result["estimated_worst_path_delay_ns"], 7.75)
        self.assertTrue(result["meets_max_delay"])
        self.assertFalse(ct._parse_timing(log.replace("2.250000", "-0.5"), 10.0)["meets_max_delay"])
        with self.assertRaises(RuntimeError):
            ct._parse_timing("CHIPRD_WNS_NS INF", 10.0)
        with self.assertRaises(RuntimeError):
            ct._parse_timing("CHIPRD_WNS_NS 1.0", 10.0)

    def test_runs_mapping_and_clockless_sta(self):
        def map_one(source, liberty, folder, known_cells, *, top_module):
            self.assertEqual(top_module, "int_mul16")
            (folder / "mapped.v").write_text(
                "module int_mul16(input [15:0] a,b, output [31:0] product); endmodule\n",
                encoding="utf-8",
            )
            return {"status": "pass", "mapped_cell_area": 1812.258,
                    "mapped_cell_count": 1593, "yosys_version": "Yosys fixture"}
        calls = []
        def run(command, cwd):
            calls.append((command, (cwd / "timing.tcl").read_text() if (cwd / "timing.tcl").exists() else ""))
            if "-version" in command:
                return SimpleNamespace(returncode=0, stdout="OpenROAD fixture\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout="Startpoint: input port a\nEndpoint: output port product[0]\nCHIPRD_WNS_NS 2.25\n",
                stderr="",
            )
        with patch.object(ct.ppa, "_library_cells", return_value={"INV_X1"}), \
             patch.object(ct.ppa, "_map_one", side_effect=map_one), \
             patch.object(ct.shutil, "which", return_value="/usr/bin/openroad"), \
             patch.object(ct.ppa, "_run", side_effect=run):
            report = ct.analyze_combinational_timing(
                self.candidate, self.liberty, self.tech, self.cells, self.output,
                top_module="int_mul16", input_ports="a,b", output_ports="product",
                max_delay_ns=10.0,
            )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["candidate"]["timing"]["estimated_worst_path_delay_ns"], 7.75)
        self.assertEqual(report["top_module"], "int_mul16")
        script = calls[0][1]
        self.assertIn("link_design int_mul16", script)
        self.assertNotIn("create_clock", script)
        self.assertTrue((self.output / "combinational_timing_report.json").is_file())
        saved = json.loads((self.output / "combinational_timing_report.json").read_text())
        self.assertEqual(saved["status"], "pass")

    def test_rejects_invalid_constraints_and_overwrite(self):
        for kwargs in (
            {"top_module": "int_mul16; exit", "input_ports": "a,b", "output_ports": "product"},
            {"top_module": "int_mul16", "input_ports": "a; exit", "output_ports": "product"},
            {"top_module": "int_mul16", "input_ports": "a,b", "output_ports": "b"},
            {"top_module": "int_mul16", "input_ports": "a,b", "output_ports": "product", "max_delay_ns": float("inf")},
        ):
            with self.assertRaises(ValueError):
                ct.analyze_combinational_timing(
                    self.candidate, self.liberty, self.tech, self.cells,
                    self.root / ("bad-" + str(len(list(self.root.iterdir())))),
                    **kwargs,
                )
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            ct.analyze_combinational_timing(
                self.candidate, self.liberty, self.tech, self.cells, self.output,
                top_module="int_mul16", input_ports="a,b", output_ports="product",
            )


if __name__ == "__main__":
    unittest.main()
