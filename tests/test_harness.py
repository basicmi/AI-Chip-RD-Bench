import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.harness import ROOT, hash_file, run


CANDIDATE = ROOT / "tasks" / "cordic" / "cordic_rotator.sv"


class HarnessTests(unittest.TestCase):
    def test_hash_is_stable(self):
        self.assertEqual(len(hash_file(CANDIDATE)), 64)
        self.assertEqual(hash_file(CANDIDATE), hash_file(CANDIDATE))

    def test_invalid_candidate_does_not_succeed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                run(Path(d) / "missing.sv", Path(d) / "out", "verify")

    def test_missing_tool_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("chiprd.harness.shutil.which", return_value=None):
                result = run(CANDIDATE, Path(d) / "out", "verify")
            self.assertEqual(result["status"], "error")
            self.assertIn("unavailable", result["error"])
            saved = json.loads((Path(d) / "out" / "report.json").read_text())
            self.assertEqual(saved["status"], "error")


if __name__ == "__main__":
    unittest.main()
