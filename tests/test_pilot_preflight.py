"""Preflight must remain offline, secret-free and conservative."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd import pilot_preflight as preflight
from chiprd.agent_ppa import PPAObjective


class PilotPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("cells.lib", "tech.lef", "cells.lef"):
            (self.root / name).write_text("test fixture\n")
        (self.root / "tasks/cordic").mkdir(parents=True)
        (self.root / "tasks/cordic/cordic_rotator.sv").write_text("module cordic_rotator; endmodule\n")
        (self.root / "tasks/cordic/spec.md").write_text("# Spec\n")
        self.objective = PPAObjective(
            self.root / "cells.lib", self.root / "tech.lef",
            self.root / "cells.lef", 10, 0.5)

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_ready_is_not_a_live_probe(self):
        mock_model = {"model": "local-code-model", "endpoint_origin": "http://127.0.0.1:8000",
                      "temperature": 0, "max_tokens": 4096, "timeout_seconds": 90,
                      "api_key": "not captured"}
        with patch.object(preflight, "ROOT", self.root), patch.object(
            preflight, "_model_configuration", return_value=mock_model
        ), patch.object(preflight.shutil, "which", return_value="/usr/bin/tool"):
            result = preflight.assess(self.objective)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["local_configuration_ready"])
        self.assertIn("NOT model reachability", result["checks_scope"])
        self.assertNotIn("secret", json.dumps(result))

    def test_missing_tools_and_invalid_library_fail_closed(self):
        with patch.object(preflight, "ROOT", self.root), patch.object(
            preflight, "_model_configuration", return_value={"model": "fixture"}
        ), patch.object(preflight.shutil, "which",
                        side_effect=lambda name: None if name in ("openroad", "eqy") else "/usr/bin/tool"):
            result = preflight.assess(self.objective)
        self.assertEqual(result["missing_executables"], ["openroad", "eqy"])
        self.assertFalse(result["local_configuration_ready"])

        (self.root / "cells.lef").unlink()
        with patch.object(preflight, "ROOT", self.root), patch.object(
            preflight, "_model_configuration", return_value={"model": "fixture"}
        ), patch.object(preflight.shutil, "which", return_value="/usr/bin/tool"):
            result = preflight.assess(self.objective)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["configuration_errors"], ["PPA inputs: ValueError"])

    def test_bad_provider_config_not_persisted(self):
        with patch.object(preflight, "ROOT", self.root), patch.object(
            preflight, "_model_configuration",
            side_effect=ValueError("api-key-is-secret")
        ), patch.object(preflight.shutil, "which", return_value="/usr/bin/tool"):
            result = preflight.assess(self.objective)
        self.assertEqual(result["configuration_errors"], ["Model configuration: ValueError"])
        self.assertNotIn("api-key-is-secret", json.dumps(result))

    def test_symlink_golden_source_rejected(self):
        source = self.root / "tasks/cordic/cordic_rotator.sv"
        source.unlink()
        source.symlink_to(self.root / "cells.lib")
        with patch.object(preflight, "ROOT", self.root), patch.object(
            preflight, "_model_configuration", return_value={"model": "fixture"}
        ), patch.object(preflight.shutil, "which", return_value="/usr/bin/tool"):
            result = preflight.assess(self.objective)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("cordic_rotator.sv" in x for x in result["configuration_errors"]))


if __name__ == "__main__":
    unittest.main()
