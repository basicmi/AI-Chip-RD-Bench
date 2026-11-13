import difflib
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiprd.patches import PATCH_TARGET_PATH, apply_patch_proposal


def make_proposal(
    root: Path, old: str, new: str,
    parent_path: str = PATCH_TARGET_PATH, **overrides
) -> Path:
    target = root / parent_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(old, encoding="utf-8")
    patch_text = "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{parent_path}",
        tofile=f"b/{parent_path}",
    ))
    payload = {
        "schema_version": "1.0.0",
        "task_id": "cordic.rotation.q14.v1",
        "patch_id": "candidate_delta",
        "parent_candidate_id": "baseline",
        "generation": 1,
        "parent_candidate_path": parent_path,
        "parent_candidate_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest(),
        "patch_format": "unified_diff",
        "patch": patch_text,
        "description": "Test-only RTL patch",
        "predicted_improvement": 0.25,
    }
    payload.update(overrides)
    proposal_path = root / "proposal.json"
    proposal_path.write_text(json.dumps(payload), encoding="utf-8")
    return proposal_path


@unittest.skipUnless(shutil.which("git"), "git is required for patch application tests")
class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = "module cordic_rotator;\n  wire value = 1'b0;\nendmodule\n"
        self.new = "module cordic_rotator;\n  wire value = 1'b1;\nendmodule\n"
        self.proposal = make_proposal(self.root, self.old, self.new)

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, proposal=None, output=None):
        with patch("chiprd.patches.ROOT", self.root):
            return apply_patch_proposal(
                proposal or self.proposal,
                output or self.root / "artifacts" / "agent-patches",
            )

    def test_applies_patch_to_copy_and_preserves_parent(self):
        result = self.apply()
        candidate = Path(result["candidate_path"])
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(candidate.read_text(), self.new)
        self.assertEqual((self.root / PATCH_TARGET_PATH).read_text(), self.old)
        self.assertEqual(
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
            manifest["candidate"]["sha256"],
        )
        self.assertEqual(
            manifest["parent"]["sha256"],
            hashlib.sha256(self.old.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(manifest["search_proposal"]["parent_id"], "baseline")
        self.assertTrue(Path(result["patch_path"]).is_file())

    def test_rejects_stale_parent_hash(self):
        payload = json.loads(self.proposal.read_text())
        payload["parent_candidate_sha256"] = "0" * 64
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.apply()

    def test_rejects_patch_path_traversal(self):
        payload = json.loads(self.proposal.read_text())
        payload["patch"] = payload["patch"].replace(
            f"a/{PATCH_TARGET_PATH}", "a/../../outside.sv"
        ).replace(f"b/{PATCH_TARGET_PATH}", "b/../../outside.sv")
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "approved CORDIC RTL file"):
            self.apply()

    def test_rejects_multi_file_patch(self):
        payload = json.loads(self.proposal.read_text())
        payload["patch"] += (
            f"--- a/{PATCH_TARGET_PATH}\n+++ b/{PATCH_TARGET_PATH}\n"
            "@@ -1 +1 @@\n-module cordic_rotator;\n+module cordic_rotator;\n"
        )
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "exactly one old-file header"):
            self.apply()

    def test_rejects_unknown_proposal_fields(self):
        payload = json.loads(self.proposal.read_text())
        payload["shell_command"] = "touch /tmp/should-not-run"
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.apply()

    def test_rejects_output_outside_repository(self):
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaisesRegex(ValueError, "inside the repository root"):
                self.apply(output=Path(outside) / "patches")

    def test_refuses_to_overwrite_previous_patch_output(self):
        self.apply()
        with self.assertRaises(FileExistsError):
            self.apply()

    def test_rejects_invalid_generation_and_identifier(self):
        payload = json.loads(self.proposal.read_text())
        payload["patch_id"] = "../escape"
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "safe identifier"):
            self.apply()


    def test_applies_incremental_patch_to_a_prior_generated_candidate(self):
        first = self.apply()
        parent_path = Path(first["candidate_path"]).relative_to(self.root).as_posix()
        second_new = self.new.replace("endmodule", "// generation two" + chr(10) + "endmodule")
        second_proposal = make_proposal(
            self.root,
            self.new,
            second_new,
            parent_path=parent_path,
            patch_id="candidate_delta_2",
            parent_candidate_id="candidate_delta",
            generation=2,
        )
        second = self.apply(second_proposal)
        self.assertEqual(Path(second["candidate_path"]).read_text(), second_new)
        second_manifest = json.loads(Path(second["manifest_path"]).read_text())
        self.assertEqual(second_manifest["parent"]["candidate_id"], "candidate_delta")
        self.assertEqual(second_manifest["parent"]["path"], parent_path)

    def test_rejects_boolean_generation(self):
        payload = json.loads(self.proposal.read_text())
        payload["generation"] = True
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.apply()

    def test_rejects_parent_outside_allowlist(self):
        payload = json.loads(self.proposal.read_text())
        payload["parent_candidate_path"] = "chiprd/cli.py"
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "golden RTL or a prior"):
            self.apply()

    def test_rejects_invalid_utf8_surrogate_in_patch(self):
        payload = json.loads(self.proposal.read_text())
        payload["patch"] = chr(0xD800)
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            self.apply()

    def test_rejects_wrong_parent_path(self):
        payload = json.loads(self.proposal.read_text())
        payload["parent_candidate_path"] = "../other.sv"
        self.proposal.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "parent_candidate_path"):
            self.apply()


if __name__ == "__main__":
    unittest.main()
