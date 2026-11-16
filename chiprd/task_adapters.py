"""Registry-backed task definitions for task-specific evaluator adapters.

This layer resolves immutable task metadata and artifact paths. It deliberately
does not claim the CORDIC-only Agent/PPA campaign has become generic.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .task_registry import validate_registry
from .harness import ROOT


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    track_id: str
    maturity: str
    title: str
    top_module: str
    language: str
    spec_path: Path
    golden_rtl: Path
    reference_rtl: Path
    interface: dict
    evaluation: dict
    objective: dict


def load_task(task_id: str, *, root: Path = ROOT) -> TaskDefinition:
    catalog = validate_registry(root=root)
    matches = [row for row in catalog["tasks"] if row["task_id"] == task_id]
    if len(matches) != 1:
        raise KeyError(f"Unknown task_id: {task_id}")
    row = matches[0]
    if row["maturity"] not in {"runnable", "experimental"}:
        raise ValueError(f"Task is not executable: {row['maturity']}")
    interface = row.get("interface")
    if not isinstance(interface, dict):
        raise ValueError("Executable task requires an interface contract")
    base = Path(root).resolve()
    return TaskDefinition(
        task_id=row["task_id"], track_id=row["track_id"],
        maturity=row["maturity"], title=row["title"],
        top_module=interface["top_module"],
        language=interface.get("language", "systemverilog"),
        spec_path=(base / row["spec"]).resolve(),
        golden_rtl=(base / row["golden_rtl"]).resolve(),
        reference_rtl=(base / row["reference_rtl"]).resolve(),
        interface=interface, evaluation=row.get("evaluation", {}),
        objective=row.get("objective", {}),
    )
