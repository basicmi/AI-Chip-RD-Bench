"""Validate declarative, task-bound RTL architecture search spaces.

This inventory layer does not generate RTL or claim that a configuration is
implemented merely because it is declared. Each configuration is bound to a
concrete RTL file whose hash and top-module declaration are checked.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .harness import ROOT
from .task_registry import validate_registry

SCHEMA_VERSION = "1.0.0"
MAX_SPACE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATES = 256
MAX_DIMENSIONS = 32
MAX_VALUES = 64
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a 1-64 character safe identifier")
    return value


def _repo_file(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} must be a repository-relative path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be repository-relative and must not traverse parent directories")
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} must not traverse symlinks")
    resolved = cursor.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the repository root") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must identify a regular file")
    return resolved


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_architecture_space(path: Path, *, root: Path | None = None) -> dict:
    """Validate dimensions, candidate configurations and RTL provenance."""
    root = (root or ROOT).resolve(strict=True)
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SPACE_BYTES:
        raise ValueError("Architecture-space definition must be a bounded regular non-symlink file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Architecture-space root must be a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported architecture-space schema_version")
    space_id = _safe_id(payload.get("space_id"), "space_id")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id is required")
    registry = validate_registry(root=root)
    task = next((item for item in registry["tasks"] if item["task_id"] == task_id), None)
    if task is None or task.get("maturity") != "runnable":
        raise ValueError("Architecture space must reference an existing runnable task")
    top_module = _safe_id(payload.get("top_module"), "top_module")
    task_interface = task.get("interface", {})
    if top_module != task_interface.get("top_module"):
        raise ValueError("top_module does not match the task registry interface")
    registry_latency = task_interface.get("latency_max_cycles")
    contract = payload.get("invariants")
    if not isinstance(contract, dict) or not contract:
        raise ValueError("invariants must be a non-empty object")
    if contract.get("interface_immutable") is not True:
        raise ValueError("interface_immutable must be true")
    latency = contract.get("latency_max_cycles")
    if isinstance(latency, bool) or not isinstance(latency, int) or latency < 1:
        raise ValueError("latency_max_cycles must be a positive integer")
    if latency != registry_latency:
        raise ValueError("latency_max_cycles does not match the task registry interface")
    tolerance = contract.get("numerical_tolerance_lsb")
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError("numerical_tolerance_lsb must be a non-negative integer")

    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= MAX_DIMENSIONS:
        raise ValueError(f"dimensions must contain 1-{MAX_DIMENSIONS} entries")
    allowed: dict[str, set[str]] = {}
    dimension_rows = []
    for item in dimensions:
        if not isinstance(item, dict):
            raise ValueError("Every architecture dimension must be an object")
        dimension_id = _safe_id(item.get("id"), "dimension id")
        if dimension_id in allowed:
            raise ValueError(f"Duplicate architecture dimension: {dimension_id}")
        if item.get("type") != "enum":
            raise ValueError(f"{dimension_id}: only enum dimensions are supported in schema v1")
        values = item.get("values")
        if (not isinstance(values, list) or not 1 <= len(values) <= MAX_VALUES
                or any(not isinstance(v, str) or not v or len(v) > 128 for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"{dimension_id}: values must be 1-{MAX_VALUES} unique non-empty strings")
        allowed[dimension_id] = set(values)
        dimension_rows.append({"id": dimension_id, "type": "enum", "values": values})

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError(f"candidates must contain 1-{MAX_CANDIDATES} entries")
    seen_ids: set[str] = set()
    seen_configs: set[str] = set()
    seen_paths: set[str] = set()
    observed = {key: set() for key in allowed}
    rows = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Every architecture candidate must be an object")
        candidate_id = _safe_id(candidate.get("candidate_id"), "candidate_id")
        if candidate_id in seen_ids:
            raise ValueError(f"Duplicate architecture candidate: {candidate_id}")
        seen_ids.add(candidate_id)
        config = candidate.get("configuration")
        if not isinstance(config, dict) or set(config) != set(allowed):
            raise ValueError(f"{candidate_id}: configuration must specify every dimension exactly once")
        for key, value in config.items():
            if not isinstance(value, str) or value not in allowed[key]:
                raise ValueError(f"{candidate_id}: unsupported value {value!r} for {key}")
            observed[key].add(value)
        config_key = json.dumps(config, sort_keys=True, separators=(",", ":"))
        if config_key in seen_configs:
            raise ValueError(f"Duplicate architecture configuration: {config_key}")
        seen_configs.add(config_key)

        rtl = _repo_file(root, candidate.get("rtl"), f"{candidate_id}.rtl")
        rtl_rel = rtl.relative_to(root).as_posix()
        if rtl_rel in seen_paths:
            raise ValueError(f"Multiple candidate IDs reference the same RTL file: {rtl_rel}")
        seen_paths.add(rtl_rel)
        source = rtl.read_text(encoding="utf-8")
        if not re.search(rf"\bmodule\s+{re.escape(top_module)}\s*\(", source):
            raise ValueError(f"{candidate_id}: RTL does not declare top module {top_module}")
        rows.append({
            "candidate_id": candidate_id,
            "rtl": rtl_rel,
            "rtl_sha256": _sha(rtl),
            "configuration": config,
        })

    missing = {
        key: sorted(values - observed[key])
        for key, values in allowed.items() if values - observed[key]
    }
    if missing:
        raise ValueError(f"Architecture space contains unimplemented declared values: {missing}")
    baseline_id = _safe_id(payload.get("baseline_candidate_id"), "baseline_candidate_id")
    if baseline_id not in seen_ids:
        raise ValueError("baseline_candidate_id must reference a declared candidate")
    baseline_row = next(row for row in rows if row["candidate_id"] == baseline_id)
    if baseline_row["rtl"] != task.get("golden_rtl"):
        raise ValueError("baseline candidate RTL must match the task registry golden_rtl")
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "chiprd_architecture_space_validation",
        "status": "pass",
        "space_id": space_id,
        "task_id": task_id,
        "top_module": top_module,
        "baseline_candidate_id": baseline_id,
        "dimension_count": len(dimension_rows),
        "candidate_count": len(rows),
        "dimensions": dimension_rows,
        "candidates": rows,
        "coverage": {key: sorted(value) for key, value in observed.items()},
        "limitations": [
            "This validates declared configurations and their RTL provenance; it does not generate RTL.",
            "Enum coverage is inventory coverage, not proof that the dimensions are orthogonal or exhaustive.",
            "Every candidate still requires the task's independent functional, formal, mutation and PPA gates.",
        ],
    }
