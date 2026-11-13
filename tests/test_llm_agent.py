"""Offline contract tests for the LLM adapter. No API keys or network calls."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ADAPTER = Path(__file__).resolve().parents[1] / "examples/agents/llm_agent.py"
spec = importlib.util.spec_from_file_location("chiprd_llm_agent_fixture", ADAPTER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class FakeHTTP:
    def __init__(self, obj):
        self.payload = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return None
    def read(self, count=-1):
        return self.payload.read(count)


def chat(answer, reason="stop"):
    return {"choices": [{"message": {"content": json.dumps(answer)},
                         "finish_reason": reason}]}


class LLMAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        target = self.root / "tasks/cordic/cordic_rotator.sv"
        target.parent.mkdir(parents=True)
        self.old = "module cordic_rotator;\n  wire ok = 1'b0;\nendmodule\n"
        self.new = "module cordic_rotator;\n  wire ok = 1'b1;\nendmodule\n"
        target.write_text(self.old, encoding="utf-8")
        (self.root / "tasks/cordic/spec.md").write_text("# CORDIC Golden Specification\n")
        self.payload = {
            "schema_version": "1.0.0", "task_id": agent.TASK_ID,
            "iteration": 1, "loop_id": "test",
            "parent_candidate": {
                "candidate_id": "baseline", "generation": 0,
                "candidate_path": "tasks/cordic/cordic_rotator.sv",
                "candidate_sha256": hashlib.sha256(self.old.encode()).hexdigest(),
            },
            "previous_experiment": {"status": "pass", "generic_cell_count": 132},
            "objective": {"metric": "generic cells"},
        }
        self.root_override = patch.object(agent, "ROOT", self.root)
        self.root_override.start()

    def tearDown(self):
        self.root_override.stop()
        self.temp.cleanup()

    def test_generates_single_file_diff_with_exact_parent_binding(self):
        parent, _, original = agent._validated_input(self.payload)
        result = agent._build_response(
            {"action": "patch", "rtl": self.new, "rationale": "refactor"},
            self.payload, parent, original,
        )
        proposal = result["proposal"]
        self.assertEqual(result["action"], "patch")
        self.assertEqual(proposal["generation"], 1)
        self.assertEqual(proposal["parent_candidate_sha256"], parent["candidate_sha256"])
        self.assertTrue(proposal["patch"].startswith("--- a/tasks/cordic/cordic_rotator.sv\n"))
        self.assertIn("+  wire ok = 1'b1;", proposal["patch"])
        self.assertEqual((self.root / parent["candidate_path"]).read_text(), self.old)

    def test_includes_task_spec_and_previous_evidence(self):
        messages = agent._messages(self.payload, self.old)
        self.assertIn("CORDIC Golden Specification", messages[1]["content"])
        self.assertIn("generic_cell_count", messages[1]["content"])
        self.assertIn(self.old, messages[1]["content"])

    def test_rejects_stale_hash_and_traversal(self):
        self.payload["parent_candidate"]["candidate_sha256"] = "0" * 64
        with self.assertRaisesRegex(agent.AgentError, "SHA-256 mismatch"):
            agent._validated_input(self.payload)
        self.payload["parent_candidate"]["candidate_path"] = "../secret.sv"
        with self.assertRaisesRegex(agent.AgentError, "not approved"):
            agent._validated_input(self.payload)

    def test_rejects_noop_non_terminated_and_invalid_action(self):
        parent, _, original = agent._validated_input(self.payload)
        for changed in (self.old, self.new.rstrip("\n"), "module\x00\n"):
            with self.assertRaises(agent.AgentError):
                agent._build_response(
                    {"action": "patch", "rtl": changed},
                    self.payload, parent, original,
                )
        with self.assertRaises(agent.AgentError):
            agent._build_response({"action": []}, self.payload, parent, original)

    def test_explicit_stop(self):
        parent, _, original = agent._validated_input(self.payload)
        response = agent._build_response(
            {"action": "stop", "rationale": "No safe next hypothesis"},
            self.payload, parent, original,
        )
        self.assertEqual(response["action"], "stop")
        self.assertNotIn("proposal", response)

    def test_local_endpoint_and_http_optin(self):
        with patch.dict(os.environ, {
            "CHIPRD_LLM_MODEL": "local", "CHIPRD_LLM_BASE_URL": "http://localhost:1234/v1",
            "CHIPRD_LLM_API_KEY": "",
        }):
            self.assertEqual(
                agent._config()["endpoint"],
                "http://localhost:1234/v1/chat/completions",
            )
        with patch.dict(os.environ, {
            "CHIPRD_LLM_MODEL": "local", "CHIPRD_LLM_BASE_URL": "http://example.com/v1",
            "CHIPRD_LLM_API_KEY": "secret", "CHIPRD_LLM_ALLOW_HTTP": "0",
        }):
            with self.assertRaisesRegex(agent.AgentError, "Plaintext remote HTTP"):
                agent._config()

    def test_mocked_http_chat_completions(self):
        cfg = {"model": "fixture", "endpoint": "https://example.org/v1/chat/completions",
               "api_key": "dummy", "timeout": 2, "max_tokens": 1024}
        with patch.object(agent.request, "urlopen", return_value=FakeHTTP(
            chat({"action": "patch", "rtl": self.new, "rationale": "hypothesis"})
        )) as send:
            result = agent._call_model(cfg, agent._messages(self.payload, self.old))
        self.assertEqual(result["action"], "patch")
        req = send.call_args.args[0]
        self.assertEqual(json.loads(req.data)["model"], "fixture")
        self.assertEqual(req.get_header("Authorization"), "Bearer dummy")

    def test_truncated_model_response_fails_closed(self):
        cfg = {"model": "fixture", "endpoint": "https://example.org/v1/chat/completions",
               "api_key": "dummy", "timeout": 2, "max_tokens": 1024}
        with patch.object(agent.request, "urlopen", return_value=FakeHTTP(
            chat({"action": "patch", "rtl": self.new}, reason="length")
        )):
            with self.assertRaisesRegex(agent.AgentError, "truncated"):
                agent._call_model(cfg, [])

    def test_http_errors_do_not_expose_credentials(self):
        cfg = {"model": "fixture", "endpoint": "https://example.org/v1/chat/completions",
               "api_key": "secret-token", "timeout": 2, "max_tokens": 1024}
        with patch.object(agent.request, "urlopen", side_effect=HTTPError(
            cfg["endpoint"], 401, "secret-token", {}, io.BytesIO(b"secret-token")
        )):
            with self.assertRaises(agent.AgentError) as exc:
                agent._call_model(cfg, [])
        self.assertNotIn("secret-token", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
