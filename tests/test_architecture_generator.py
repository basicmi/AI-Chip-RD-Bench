"""Tests for deterministic CORDIC microarchitecture generation."""
import tempfile
import unittest
from pathlib import Path

from chiprd.architecture_generator import (
    SUPPORTED_ITERATIONS_PER_CYCLE,
    generate_cordic_rtl,
    write_generated_architectures,
)
from chiprd.harness import ROOT


class ArchitectureGeneratorTests(unittest.TestCase):
    def test_supported_unroll_factors_are_deterministic(self):
        for factor in SUPPORTED_ITERATIONS_PER_CYCLE:
            first = generate_cordic_rtl(factor)
            second = generate_cordic_rtl(factor)
            self.assertEqual(first, second)
            self.assertIn(f"iterations_per_cycle={factor}", first)
            self.assertIn("cycle_count == 5'd15", first)
            self.assertIn("output_valid <= 1'b1", first)

    def test_unroll_factor_changes_combinational_depth(self):
        two = generate_cordic_rtl(2)
        four = generate_cordic_rtl(4)
        self.assertEqual(two.count("wire signed [31:0] gx_x_"), 4)
        self.assertEqual(four.count("wire signed [31:0] gx_x_"), 8)
        self.assertIn("gx_x_1", two)
        self.assertIn("gx_x_3", four)

    def test_rejects_unsupported_and_boolean_factors(self):
        for factor in (0, 3, 8, True, False, "2"):
            with self.subTest(factor=factor):
                with self.assertRaises(ValueError):
                    generate_cordic_rtl(factor)

    def test_committed_generated_files_match_generator(self):
        report = write_generated_architectures(
            ROOT / "tasks/cordic/generated", configurations=(2, 4), check=True
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual([x["status"] for x in report["configurations"]],
                         ["verified", "verified"])

    def test_write_then_check_round_trip(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            target = Path(directory) / "generated"
            written = write_generated_architectures(target, configurations=(2, 4))
            self.assertTrue(all(x["status"] == "generated" for x in written["configurations"]))
            checked = write_generated_architectures(target, configurations=(2, 4), check=True)
            self.assertTrue(all(x["status"] == "verified" for x in checked["configurations"]))

    def test_rejects_output_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                write_generated_architectures(Path(directory) / "generated")

    def test_rejects_duplicate_or_empty_configurations(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            write_generated_architectures(ROOT / "tasks/cordic/generated",
                                          configurations=(2, 2), check=True)
        with self.assertRaisesRegex(ValueError, "At least one"):
            write_generated_architectures(ROOT / "tasks/cordic/generated",
                                          configurations=(), check=True)


if __name__ == "__main__":
    unittest.main()
