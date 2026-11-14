"""Bounded, non-generating OpenAI-compatible /models availability probe.

This explicitly performs a network GET. It never sends a chat completion,
writes credentials, or includes endpoint path/provider body in error reports.
"""
from __future__ import annotations

import importlib.util
import json
from urllib import error, request

from .pilot_preflight import ADAPTER

MAX_RESPONSE_BYTES = 1_048_576


def probe_configured_models(models: list[str]) -> dict:
    """Load the same credential validator as the live model adapter."""
    spec = importlib.util.spec_from_file_location("_chiprd_model_probe_adapter", ADAPTER)
    if spec is None or spec.loader is None:
        raise ValueError("Model adapter unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module._config()
    return probe(config, models)




def probe(config: dict, models: list[str], *, opener=None) -> dict:
    endpoint = config["endpoint"]
    if not isinstance(endpoint, str) or not endpoint.endswith("/chat/completions"):
        raise ValueError("Configured endpoint is not Chat Completions")
    if not isinstance(models, list) or not models or any(
        not isinstance(m, str) or not m for m in models
    ):
        raise ValueError("At least one model ID is required")
    url = endpoint[:-len("/chat/completions")] + "/models"
    headers = {"Accept": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = "Bearer " + config["api_key"]
    req = request.Request(url, headers=headers, method="GET")
    opener = opener or request.urlopen
    try:
        with opener(req, timeout=min(10.0, float(config["timeout"]))) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        return {"status": "fail", "reason": "http_error", "http_status": exc.code,
                "models_available": False, "completion_calls": 0}
    except (OSError, error.URLError, TimeoutError, ValueError):
        return {"status": "fail", "reason": "transport_error", "models_available": False,
                "completion_calls": 0}
    if len(raw) > MAX_RESPONSE_BYTES:
        return {"status": "fail", "reason": "oversized_response",
                "models_available": False, "completion_calls": 0}
    try:
        payload = json.loads(raw)
        ids = [row["id"] for row in payload["data"]]
        if not isinstance(payload["data"], list) or not all(
            isinstance(m, str) for m in ids
        ):
            raise ValueError("Invalid model list")
    except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
        return {"status": "fail", "reason": "invalid_model_list",
                "models_available": False, "completion_calls": 0}
    missing = [m for m in models if m not in ids]
    return {
        "status": "pass" if not missing else "fail",
        "reason": None if not missing else "models_not_listed",
        "models_available": not missing,
        "requested_models": list(models),
        "missing_model_ids": missing,
        "completion_calls": 0,
        "note": "GET /models checks advertised IDs, not completion compatibility or actual inference",
    }
