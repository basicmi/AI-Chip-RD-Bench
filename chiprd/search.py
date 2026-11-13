"""Budgeted evaluation of agent-submitted RTL candidate pools.

This module schedules and evaluates proposals; it does not generate RTL.
Candidate generation remains the responsibility of an external Agent adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import signal
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT

SEARCH_SCHEMA_VERSION = "1.0.0"
SEARCH_MANIFEST = "search_manifest.json"
SEARCH_REPORT = "search_report.json"


@dataclass(frozen=True)
class SearchBudget:
    max_experiments: int = 4
    max_wall_clock_seconds: float = 1800.0
    max_experiment_seconds: float = 600.0
    max_candidate_bytes: int = 1_048_576
    elite_parent_fraction: float = 0.5

    def validate(self) -> None:
        if isinstance(self.max_experiments, bool) or not isinstance(self.max_experiments, int) or self.max_experiments < 1:
            raise ValueError("max_experiments must be an integer of at least 1")
        if isinstance(self.max_candidate_bytes, bool) or not isinstance(self.max_candidate_bytes, int) or self.max_candidate_bytes < 1:
            raise ValueError("max_candidate_bytes must be an integer of at least 1")
        if (
            isinstance(self.max_wall_clock_seconds, bool)
            or not isinstance(self.max_wall_clock_seconds, (int, float))
            or not math.isfinite(self.max_wall_clock_seconds)
            or self.max_wall_clock_seconds <= 0
        ):
            raise ValueError("max_wall_clock_seconds must be finite and positive")
        if (
            isinstance(self.max_experiment_seconds, bool)
            or not isinstance(self.max_experiment_seconds, (int, float))
            or not math.isfinite(self.max_experiment_seconds)
            or self.max_experiment_seconds <= 0
        ):
            raise ValueError("max_experiment_seconds must be finite and positive")
        if (
            isinstance(self.elite_parent_fraction, bool)
            or not isinstance(self.elite_parent_fraction, (int, float))
            or not math.isfinite(self.elite_parent_fraction)
            or not 0 < self.elite_parent_fraction <= 1
        ):
            raise ValueError("elite_parent_fraction must be finite and in (0, 1]")


@dataclass(frozen=True)
class CandidateProposal:
    candidate_id: str
    candidate_path: Path
    parent_id: str | None
    generation: int
    predicted_improvement: float
    candidate_sha256: str
    size_bytes: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_candidate(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        raise ValueError("candidate_path must be repository-relative")
    candidate = (ROOT / path).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("candidate_path escapes the repository root") from exc
    if not candidate.is_file():
        raise ValueError("candidate_path must identify a regular file")
    return candidate


def load_proposals(path: Path, budget: SearchBudget) -> tuple[list[CandidateProposal], str]:
    """Load and validate a versioned JSON proposal pool."""
    path = path.resolve(strict=True)
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Candidate pool root must be a JSON object")
    if payload.get("schema_version") != SEARCH_SCHEMA_VERSION:
        raise ValueError("Unsupported candidate-pool schema_version")
    if payload.get("task_id") != "cordic.rotation.q14.v1":
        raise ValueError("Candidate pool task_id does not match the Phase 0 task")
    entries = payload.get("proposals")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Candidate pool must contain a non-empty proposals array")

    proposals: list[CandidateProposal] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Every proposal must be a JSON object")
        candidate_id = entry.get("candidate_id")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in {".", ".."}
            or len(candidate_id) > 64
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in candidate_id)
        ):
            raise ValueError("candidate_id must be 1-64 safe identifier characters")
        if candidate_id in seen:
            raise ValueError(f"Duplicate candidate_id: {candidate_id}")
        seen.add(candidate_id)

        relative_path = entry.get("candidate_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError(f"{candidate_id}: candidate_path is required")
        candidate = _repo_candidate(relative_path)
        size_bytes = candidate.stat().st_size
        if size_bytes > budget.max_candidate_bytes:
            raise ValueError(
                f"{candidate_id}: candidate size {size_bytes} exceeds "
                f"max_candidate_bytes={budget.max_candidate_bytes}"
            )
        generation = entry.get("generation", 0)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"{candidate_id}: generation must be a non-negative integer")
        parent_id = entry.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
            raise ValueError(f"{candidate_id}: parent_id must be a non-empty string or null")
        if generation == 0 and parent_id is not None:
            raise ValueError(f"{candidate_id}: generation 0 proposals cannot have a parent")
        if generation > 0 and parent_id is None:
            raise ValueError(f"{candidate_id}: non-zero generations require parent_id")
        priority = entry.get("predicted_improvement", 0.0)
        if isinstance(priority, bool) or not isinstance(priority, (int, float)):
            raise ValueError(f"{candidate_id}: predicted_improvement must be numeric")
        if not math.isfinite(float(priority)):
            raise ValueError(f"{candidate_id}: predicted_improvement must be finite")
        proposals.append(CandidateProposal(
            candidate_id=candidate_id,
            candidate_path=candidate,
            parent_id=parent_id,
            generation=generation,
            predicted_improvement=float(priority),
            candidate_sha256=_sha256(candidate),
            size_bytes=size_bytes,
        ))

    by_id = {item.candidate_id: item for item in proposals}
    for item in proposals:
        if item.parent_id is not None:
            parent = by_id.get(item.parent_id)
            if parent is None:
                raise ValueError(f"{item.candidate_id}: unknown parent_id {item.parent_id}")
            if parent.generation != item.generation - 1:
                raise ValueError(f"{item.candidate_id}: parent must belong to the immediately preceding generation")
    return proposals, hashlib.sha256(raw_bytes).hexdigest()


def _run_sealed_experiment(
    candidate: Path, output: Path, seed: int, timeout_seconds: float
) -> dict:
    """Run one sealed experiment in a killable process group."""
    command = [
        sys.executable, "-m", "chiprd.cli", "experiment",
        "--candidate", str(candidate),
        "--output", str(output),
        "--seed", str(seed),
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return {
            "status": "error",
            "report": None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "exit_code": None,
            "timed_out": False,
            "error": f"could not start experiment runner: {exc}",
        }
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        stdout, stderr = process.communicate()
    elapsed = time.monotonic() - started
    output.mkdir(parents=True, exist_ok=True)
    (output / "search_runner.stdout.log").write_text(stdout, encoding="utf-8")
    (output / "search_runner.stderr.log").write_text(stderr, encoding="utf-8")

    report_path = output / "experiment_report.json"
    report = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = None
    if timed_out:
        status = "timeout"
    elif report is not None:
        status = report.get("status", "error")
    else:
        status = "error"
    return {
        "status": status,
        "report": report,
        "elapsed_seconds": round(elapsed, 3),
        "exit_code": process.returncode,
        "timed_out": timed_out,
    }


def _metrics(execution: dict, expected_candidate_sha256: str) -> dict:
    report = execution.get("report") or {}
    checks = report.get("checks", {})
    signature = report.get("result_signature", {})
    synthesis = signature.get("functional_synthesis", {}).get("synthesis", {})
    cell_count = synthesis.get("generic_cell_count")
    required_gates = {
        "functional_synthesis", "mutation", "formal",
        "formal_negative_control", "equivalence", "equivalence_selftest",
    }
    all_gates_pass = set(checks) == required_gates and all(
        item.get("status") == "pass" for item in checks.values()
    )
    source_hash_matches = report.get("candidate_sha256") == expected_candidate_sha256
    feasible = (
        execution.get("status") == "pass"
        and report.get("candidate_snapshot_intact") is True
        and source_hash_matches
        and all_gates_pass
        and isinstance(cell_count, int)
        and not isinstance(cell_count, bool)
        and cell_count >= 0
    )
    return {
        "feasible": feasible,
        "source_hash_matches": source_hash_matches,
        "generic_cell_count": cell_count if isinstance(cell_count, int) else None,
        # Lower generic cell count is better. This is only a synthesis proxy,
        # not standard-cell area, timing, power or physical PPA.
        "fitness": -cell_count if feasible else None,
        "gate_statuses": {
            name: item.get("status") for name, item in sorted(checks.items())
        },
    }


def _campaign_manifest(
    proposals_path: Path,
    proposals_sha256: str,
    strategy: str,
    seed: int,
    budget: SearchBudget,
) -> dict:
    return {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "manifest_type": "chiprd_agent_search",
        "created_at_utc": _utc_now(),
        "task_id": "cordic.rotation.q14.v1",
        "strategy": strategy,
        "seed": seed,
        "proposal_pool": {
            "path": str(proposals_path),
            "sha256": proposals_sha256,
        },
        "scheduler_sha256": _sha256(Path(__file__)),
        "cli_sha256": _sha256(ROOT / "chiprd" / "cli.py"),
        "budget": asdict(budget),
        "objective": {
            "hard_constraint": "all six public experiment checks must pass",
            "metric": "minimize Yosys generic_cell_count",
            "metric_boundary": "generic synthesis proxy only; not physical PPA",
        },
        "search_semantics": {
            "random": "seeded random order within each generation",
            "greedy": "descending agent-supplied predicted_improvement within each generation",
            "evolutionary": "generation-based evaluation; only children of the top feasible parent fraction are eligible",
        },
        "candidate_generation": "external_agent_or_user; this scheduler does not generate RTL",
    }


def run_search(
    proposals_path: Path,
    output: Path,
    strategy: str = "random",
    seed: int = 20261113,
    budget: SearchBudget | None = None,
) -> dict:
    """Evaluate a versioned proposal pool under a hard experiment/time budget."""
    if strategy not in {"random", "greedy", "evolutionary"}:
        raise ValueError("strategy must be random, greedy or evolutionary")
    budget = budget or SearchBudget()
    budget.validate()
    proposals_path = proposals_path.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / SEARCH_MANIFEST
    report_path = output / SEARCH_REPORT
    if manifest_path.exists() or report_path.exists():
        raise FileExistsError("Refusing to overwrite an existing search campaign")

    proposals, proposals_sha256 = load_proposals(proposals_path, budget)
    manifest = _campaign_manifest(
        proposals_path, proposals_sha256, strategy, seed, budget
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    started = time.monotonic()
    deadline = started + budget.max_wall_clock_seconds
    rng = random.Random(seed)
    by_generation: dict[int, list[CandidateProposal]] = defaultdict(list)
    for proposal in proposals:
        by_generation[proposal.generation].append(proposal)
    generations = sorted(by_generation)
    executions: dict[str, dict] = {}
    records: list[dict] = []
    skipped: list[dict] = []
    budget_exhausted = False
    stop_reason = None

    def evaluate(proposal: CandidateProposal) -> bool:
        nonlocal budget_exhausted, stop_reason
        elapsed = time.monotonic() - started
        remaining = deadline - time.monotonic()
        if len(records) >= budget.max_experiments:
            budget_exhausted = True
            stop_reason = "max_experiments"
            return False
        if remaining <= 0:
            budget_exhausted = True
            stop_reason = "max_wall_clock_seconds"
            return False
        timeout_seconds = min(budget.max_experiment_seconds, remaining)
        experiment_dir = output / "experiments" / proposal.candidate_id
        if _sha256(proposal.candidate_path) != proposal.candidate_sha256:
            execution = {
                "status": "error",
                "report": None,
                "elapsed_seconds": 0.0,
                "exit_code": None,
                "timed_out": False,
                "error": "candidate source changed after proposal-pool validation",
            }
        else:
            execution = _run_sealed_experiment(
                proposal.candidate_path, experiment_dir, seed, timeout_seconds
            )
        metrics = _metrics(execution, proposal.candidate_sha256)
        record = {
            "candidate_id": proposal.candidate_id,
            "candidate_path": str(proposal.candidate_path.relative_to(ROOT)),
            "candidate_sha256": proposal.candidate_sha256,
            "size_bytes": proposal.size_bytes,
            "parent_id": proposal.parent_id,
            "generation": proposal.generation,
            "predicted_improvement": proposal.predicted_improvement,
            "experiment_directory": str(experiment_dir.relative_to(output)),
            **execution,
            **metrics,
        }
        records.append(record)
        executions[proposal.candidate_id] = record
        return True

    for generation in generations:
        current = list(by_generation[generation])
        if generation == 0:
            eligible = current
        elif strategy == "evolutionary":
            previous = [
                item for item in records
                if item["generation"] == generation - 1 and item["feasible"]
            ]
            previous.sort(key=lambda item: (item["fitness"], item["candidate_id"]), reverse=True)
            if not previous:
                eligible = []
                for item in current:
                    skipped.append({
                        "candidate_id": item.candidate_id,
                        "reason": "parent_not_selected_or_not_feasible",
                    })
            else:
                elite_count = max(
                    1, math.ceil(len(previous) * budget.elite_parent_fraction)
                )
                elite_ids = {item["candidate_id"] for item in previous[:elite_count]}
                eligible = [item for item in current if item.parent_id in elite_ids]
                for item in current:
                    if item.parent_id not in elite_ids:
                        skipped.append({
                            "candidate_id": item.candidate_id,
                            "reason": "parent_not_selected_or_not_feasible",
                        })
                parent_fitness = {
                    item["candidate_id"]: item["fitness"] for item in previous
                }
                eligible.sort(
                    key=lambda item: (
                        parent_fitness.get(item.parent_id, float("-inf")),
                        item.predicted_improvement,
                        item.candidate_id,
                    ),
                    reverse=True,
                )
        else:
            eligible = [
                item for item in current
                if item.parent_id is None or (
                    item.parent_id in executions and executions[item.parent_id]["feasible"]
                )
            ]
            for item in current:
                if item not in eligible:
                    skipped.append({
                        "candidate_id": item.candidate_id,
                        "reason": "parent_not_evaluated_or_not_feasible",
                    })

        if strategy == "random":
            rng.shuffle(eligible)
        elif strategy == "greedy":
            eligible.sort(
                key=lambda item: (item.predicted_improvement, item.candidate_id),
                reverse=True,
            )

        for proposal in eligible:
            if not evaluate(proposal):
                skipped.append({
                    "candidate_id": proposal.candidate_id,
                    "reason": stop_reason or "budget_exhausted",
                })
                budget_exhausted = True
                break
        if budget_exhausted:
            break

    attempted_ids = {item["candidate_id"] for item in records}
    skipped_ids = {item["candidate_id"] for item in skipped}
    for proposal in proposals:
        if proposal.candidate_id not in attempted_ids and proposal.candidate_id not in skipped_ids:
            skipped.append({
                "candidate_id": proposal.candidate_id,
                "reason": "not_reached_before_campaign_stop",
            })

    feasible = [item for item in records if item["feasible"]]
    best = min(
        feasible,
        key=lambda item: (item["generic_cell_count"], item["candidate_id"]),
        default=None,
    )
    baseline = executions.get("baseline")
    if baseline is None:
        baseline = next(
            (item for item in records if item["generation"] == 0), None
        )
    baseline_cells = baseline.get("generic_cell_count") if baseline and baseline["feasible"] else None
    for item in records:
        item["improvement_vs_baseline_percent"] = (
            round((baseline_cells - item["generic_cell_count"]) / baseline_cells * 100.0, 4)
            if baseline_cells and item["feasible"] else None
        )

    elapsed = time.monotonic() - started
    status = "pass" if feasible else ("fail" if records else "error")
    report = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "campaign_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{proposals_sha256[:12]}",
        "status": status,
        "strategy": strategy,
        "seed": seed,
        "proposal_pool_sha256": proposals_sha256,
        "budget": asdict(budget),
        "budget_consumed": {
            "experiments": len(records),
            "wall_clock_seconds": round(elapsed, 3),
            "candidate_bytes_validated": sum(item.size_bytes for item in proposals),
        },
        "budget_exhausted": budget_exhausted,
        "stop_reason": stop_reason,
        "baseline_candidate": baseline.get("candidate_id") if baseline else None,
        "baseline_generic_cell_count": baseline_cells,
        "best_candidate": (
            {
                "candidate_id": best["candidate_id"],
                "candidate_sha256": best["candidate_sha256"],
                "generic_cell_count": best["generic_cell_count"],
                "improvement_vs_baseline_percent": best["improvement_vs_baseline_percent"],
                "experiment_directory": best["experiment_directory"],
            }
            if best else None
        ),
        "results": records,
        "skipped": skipped,
        "limitations": [
            "The search runner schedules a supplied proposal pool; it does not synthesize or generate RTL.",
            "Greedy ordering uses agent-supplied predicted_improvement; measured feasibility and fitness come only from evaluator results.",
            "Evolutionary mode performs lineage-aware elite-parent selection over supplied generations; candidate mutation/recombination is external.",
            "Fitness uses Yosys generic cell count only; it is not physical area, timing, power or PPA.",
            "Each candidate is evaluated with the same sealed public experiment workflow; public tests are not hidden tests.",
        ],
        "manifest_path": SEARCH_MANIFEST,
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
