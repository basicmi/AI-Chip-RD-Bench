"""Offline network-contract tests of model list availability probing."""
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from chiprd import model_probe


class Response:
    def __init__(self, data):
        self.data = io.BytesIO(data)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, count=-1):
        return self.data.read(count)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.config = {"endpoint": "https://example.net/custom/v1/chat/completions",
                       "api_key": "PRIVATE_TOKEN", "timeout": 90}

    def test_get_not_completion(self):
        calls = []
        def fake(req, timeout):
            calls.append((req.get_method(), req.full_url, timeout, req.get_header("Authorization")))
            return Response(b'{"data":[{"id":"a"},{"id":"b"}]}')
        result = model_probe.probe(self.config, ["a", "b"], opener=fake)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["completion_calls"], 0)
        self.assertEqual(calls, [("GET", "https://example.net/custom/v1/models",
                                  10.0, "Bearer PRIVATE_TOKEN")])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))

    def test_missing_id(self):
        result = model_probe.probe(self.config, ["a", "b"],
            opener=lambda req, timeout: Response(b'{"data":[{"id":"a"}]}'))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_model_ids"], ["b"])

    def test_http_error_redacted(self):
        def fake(req, timeout):
            raise HTTPError(req.full_url, 401, "PRIVATE_TOKEN", {}, io.BytesIO(b"PRIVATE_TOKEN"))
        result = model_probe.probe(self.config, ["a"], opener=fake)
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))

    def test_transport_error_redacted(self):
        def fake(req, timeout):
            raise URLError("PRIVATE_TOKEN")
        result = model_probe.probe(self.config, ["a"], opener=fake)
        self.assertEqual(result["reason"], "transport_error")
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))

    def test_malformed_and_oversized_fail_closed(self):
        for data, reason in [
            (b"<html>PRIVATE_TOKEN</html>", "invalid_model_list"),
            (b'{"data":[{"wrong":"field"}]}', "invalid_model_list"),
            (b"a" * (model_probe.MAX_RESPONSE_BYTES + 1), "oversized_response"),
        ]:
            result = model_probe.probe(self.config, ["a"],
                opener=lambda req, timeout, data=data: Response(data))
            self.assertEqual(result["reason"], reason)
            self.assertNotIn("PRIVATE_TOKEN", json.dumps(result))

    def test_invalid_endpoint_no_network(self):
        with self.assertRaisesRegex(ValueError, "Chat Completions"):
            model_probe.probe({"endpoint": "https://example.net/v1"}, ["a"],
                opener=lambda *args: self.fail("Unexpected network access"))


if __name__ == "__main__":
    unittest.main()
