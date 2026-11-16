"""Versioned catalog of benchmark tracks and executable Golden Tasks.

The registry describes support status; it does not pretend planned tracks are
runnable. Paths are repository-relative and validated without executing tools.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .harness import ROOT

SCHEMA = "1.0.0"
TRACK_STATES = {"planned", "active", "complete"}
TASK_STATES = {"planned", "experimental", "runnable", "deprecated"}
REQUIRED_EVALUATORS = {
    "functional", "protocol_formal", "sequential_equivalence",
    "mutation", "synthesis", "ppa",
}


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("Registry must be a regular JSON file <=1 MiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Registry JSON is unreadable or malformed") from exc
    if not isinstance(data, dict):
        raise ValueError("Registry root must be an object")
    return data


def _repo_file(value: object, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Task artifact path must be a nonempty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Task artifact path must be repository-relative")
    resolved = root.joinpath(path)
    if any(part.is_symlink() for part in [resolved, *resolved.parents] if part.exists()):
        raise ValueError("Task artifact path must not traverse symlinks")
    if not resolved.resolve().is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("Task artifact is missing or outside repository")
    return resolved


def validate_registry(path: Path | None = None, *, root: Path = ROOT) -> dict:
    path = path or root / "tasks/registry.json"
    root = Path(root).resolve()
    data = _load(path)
    if data.get("schema_version") != SCHEMA or data.get("registry_id") != "ai-chip-rd-bench.tasks":
        raise ValueError("Unsupported task registry schema or identity")
    tracks, tasks = data.get("tracks"), data.get("tasks")
    if not isinstance(tracks, list) or not isinstance(tasks, list):
        raise ValueError("Registry tracks and tasks must be arrays")
    track_ids = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError("Track entry must be an object")
        key = track.get("track_id")
        if not isinstance(key, str) or not key or key in track_ids:
            raise ValueError("Track IDs must be nonempty and unique")
        track_ids.add(key)
        if track.get("maturity") not in TRACK_STATES:
            raise ValueError("Unknown track maturity")
        if not isinstance(track.get("title"), str) or not isinstance(track.get("description"), str):
            raise ValueError("Track title and description are required")
    task_ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Task entry must be an object")
        key = task.get("task_id")
        if not isinstance(key, str) or not key or key in task_ids:
            raise ValueError("Task IDs must be nonempty and unique")
        task_ids.add(key)
        if task.get("track_id") not in track_ids:
            raise ValueError("Task references an unknown track")
        state = task.get("maturity")
        if state not in TASK_STATES:
            raise ValueError("Unknown task maturity")
        if not isinstance(task.get("title"), str) or not task["title"]:
            raise ValueError("Task title is required")
        if state in {"experimental", "runnable"}:
            for key in ("spec", "golden_rtl", "reference_rtl"):
                _repo_file(task.get(key), root)
            interface = task.get("interface")
            if not isinstance(interface, dict) or not isinstance(interface.get("top_module"), str):
                raise ValueError("Runnable task requires a top-level interface contract")
            if state == "runnable" and (not isinstance(interface.get("latency_max_cycles"), int) or interface["latency_max_cycles"] <= 0):
                raise ValueError("Runnable task requires a positive maximum latency")
            if state == "experimental" and interface.get("combinational") is not True and (
                not isinstance(interface.get("latency_max_cycles"), int)
                or interface["latency_max_cycles"] <= 0
            ):
                raise ValueError("Experimental task requires combinational or bounded-latency interface")
            evaluation = task.get("evaluation")
            if not isinstance(evaluation, dict) or not evaluation:
                raise ValueError("Executable task must declare available evaluators")
            if state == "runnable" and set(evaluation) != REQUIRED_EVALUATORS:
                raise ValueError("Runnable task must declare every required evaluator")
            objective = task.get("objective", {})
            if state == "runnable" and (not isinstance(objective, dict) or not isinstance(objective.get("primary"), str)):
                raise ValueError("Runnable task requires an explicit objective")
    return {
        "schema_version": SCHEMA,
        "registry_id": data["registry_id"],
        "track_count": len(tracks),
        "task_count": len(tasks),
        "runnable_task_count": sum(x["maturity"] == "runnable" for x in tasks),
        "experimental_task_count": sum(x["maturity"] == "experimental" for x in tasks),
        "tracks": tracks,
        "tasks": tasks,
        "status": "pass",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "validate"])
    parser.add_argument("--registry", type=Path, default=ROOT / "tasks/registry.json")
    args = parser.parse_args(argv)
    try:
        result = validate_registry(args.registry)
    except (ValueError, OSError) as exc:
        parser.error(f"Task registry rejected: {type(exc).__name__}: {exc}")
    if args.command == "list":
        result = {key: result[key] for key in (
            "status", "track_count", "task_count", "runnable_task_count", "tracks", "tasks"
        )}
    else:
        result = {key: result[key] for key in (
            "status", "track_count", "task_count", "runnable_task_count"
        )}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
