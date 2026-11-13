"""Tests for Liberty-mapped standard-cell area comparison."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import ppa


class LibertyMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.candidate = self.root / "candidate.sv"
        self.reference = self.root / "reference.sv"
        self.liberty = self.root / "cells.lib"
        self.candidate.write_text("module cordic_rotator; endmodule\n", encoding="utf-8")
        self.reference.write_text("module cordic_rotator; endmodule\n", encoding="utf-8")
        self.liberty.write_text(
            'library (fixture) { cell (INV_X1) { area : 1.0; } '
            'cell (DFF_X1) { area : 2.0; } }\n',
            encoding="utf-8",
        )
        self.output = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def test_parses_liberty_cell_declarations(self):
        self.assertEqual(ppa._library_cells(self.liberty), {"INV_X1", "DFF_X1"})

    def test_rejects_liberty_without_cells(self):
        self.liberty.write_text("library (empty) { }\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no recognizable cell"):
            ppa._library_cells(self.liberty)

    def test_parses_yosys_area(self):
        log = "=== cordic_rotator ===\nChip area for module '\\cordic_rotator': 1234.500\n"
        self.assertEqual(ppa._area_from_log(log), 1234.5)

    def test_missing_area_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "did not report"):
            ppa._area_from_log("No area data in this output")

    def test_same_library_area_comparison(self):
        results = iter([
            {"status": "pass", "source_sha256": "a" * 64,
             "mapped_cell_count": 100, "mapped_cell_area": 120.0,
             "cell_type_histogram": {"INV_X1": 100}, "yosys_version": "Yosys fixture"},
            {"status": "pass", "source_sha256": "b" * 64,
             "mapped_cell_count": 110, "mapped_cell_area": 150.0,
             "cell_type_histogram": {"INV_X1": 110}, "yosys_version": "Yosys fixture"},
        ])
        with patch.object(ppa, "_map_one", side_effect=lambda *args: next(results)):
            report = ppa.compare_area(
                self.candidate, self.liberty, self.output,
                reference=self.reference, platform_label="fixture-platform",
            )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["comparison"]["area_delta"], 30.0)
        self.assertEqual(report["comparison"]["area_change_percent"], 25.0)
        self.assertEqual(report["comparison"]["area_reduction_percent"], -25.0)
        saved = json.loads((self.output / "ppa_report.json").read_text())
        self.assertEqual(saved["library"]["sha256"], report["library"]["sha256"])
        self.assertEqual(saved["reference"]["mapping"]["mapped_cell_area"], 120.0)

    def test_mapping_error_is_reported_not_passed(self):
        with patch.object(ppa, "_map_one", side_effect=RuntimeError("mapping failed")):
            report = ppa.compare_area(self.candidate, self.liberty, self.output)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["error_type"], "RuntimeError")
        saved = json.loads((self.output / "ppa_report.json").read_text())
        self.assertEqual(saved["status"], "error")

    def test_missing_yosys_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            with patch.object(ppa.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Yosys unavailable"):
                    ppa._map_one(self.candidate, self.liberty, out, {"INV_X1"})

    def test_refuses_overwrite(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            ppa.compare_area(self.candidate, self.liberty, self.output)

    def test_rejects_symlink_inputs(self):
        link = self.root / "candidate-link.sv"
        link.symlink_to(self.candidate)
        with self.assertRaisesRegex(ValueError, "must not be symlinks"):
            ppa.compare_area(link, self.liberty, self.output)

    def test_rejects_invalid_platform_label(self):
        with self.assertRaisesRegex(ValueError, "platform_label"):
            ppa.compare_area(
                self.candidate, self.liberty, self.output,
                platform_label="",
            )


if __name__ == "__main__":
    unittest.main()
