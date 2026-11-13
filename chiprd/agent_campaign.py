"""Repeated, controlled Agent-loop campaigns.

Each run starts from the same checked-in golden candidate and uses the same
EDA seed and budget. Model calls are explicit: this module never supplies
credentials or contacts a provider except through the configured Agent.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import statistics
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import agent_loop as _agent_loop_module
from .agent_loop import (
    AgentLoopBudget,
    _adapter_files,
    _canonical_hash,
    _safe_id,
    _sha_file,
    _write_json,
    run_agent_loop,
)
from .benchmark_report import summarize
from .harness import ROOT

CAMPAIGN_SCHEMA = "1.0.0"
MAX_RUNS = 20
BASELINE = Path("tasks/cordic/cordic_rotator.sv")
TASK_SPEC = Path("tasks/cordic/spec.md")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_model_config_environment() -> dict:
    """Capture useful non-secret model settings; never read the API key."""
    raw_url = os.environ.get("CHIPRD_LLM_BASE_URL", "https://api.openai.com/v1")
    origin = None
    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
            host = parsed.hostname.lower()
            port = f":{parsed.port}" if parsed.port is not None else ""
            origin = f"{parsed.scheme.lower()}://{host}{port}"
    except ValueError:
        pass
    return {
        "model": os.environ.get("CHIPRD_LLM_MODEL") or None,
        "endpoint_origin": origin,
        "temperature": os.environ.get("CHIPRD_LLM_TEMPERATURE", "0"),
        "max_tokens": os.environ.get("CHIPRD_LLM_MAX_TOKENS", "4096"),
        "timeout_seconds": os.environ.get("CHIPRD_LLM_TIMEOUT_SECONDS", "90"),
        "api_key": "not captured",
    }


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _numbers(values: list[object]) -> dict:
    numbers = [float(value) for value in values
               if isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(float(value))]
    if not numbers:
        return {"n": 0, "mean": None, "median": None, "min": None,
                "max": None, "sample_stdev": None}
    return {
        "n": len(numbers),
        "mean": round(statistics.mean(numbers), 6),
        "median": round(statistics.median(numbers), 6),
        "min": round(min(numbers), 6),
        "max": round(max(numbers), 6),
        "sample_stdev": round(statistics.stdev(numbers), 6) if len(numbers) > 1 else None,
    }


def _aggregate(run_records: list[dict], requested: int) -> dict:
    scorecards = [row["scorecard"] for row in run_records if isinstance(row.get("scorecard"), dict)]
    best_improvements = [row.get("best_improvement_percent") for row in scorecards]
    feasible_rates = [row.get("candidates", {}).get("feasible_rate") for row in scorecards]
    candidate_improvement_rates = [
        row.get("candidates", {}).get("strict_improvement_rate") for row in scorecards
    ]
    run_statuses = {}
    for row in run_records:
        status = str(row.get("status", "unknown"))
        run_statuses[status] = run_statuses.get(status, 0) + 1

    token_totals = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        observed = []
        covered_calls = 0
        unknown_calls = 0
        for score in scorecards:
            item = score.get("token_usage", {}).get(key, {})
            value = item.get("reported_sum")
            if isinstance(value, int) and not isinstance(value, bool):
                observed.append(value)
                covered_calls += item.get("reported_calls", 0)
            unknown_calls += item.get("unreported_calls", 0)
        token_totals[key] = {
            "reported_sum": sum(observed) if observed else None,
            "runs_with_reported_usage": len(observed),
            "reported_calls": covered_calls,
            "unreported_calls": unknown_calls,
        }

    return {
        "runs_requested": requested,
        "runs_completed_with_scorecard": len(scorecards),
        "run_status_counts": dict(sorted(run_statuses.items())),
        "runs_with_strict_improvement": sum(
            isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            for value in best_improvements
        ),
        "best_improvement_percent": _numbers(best_improvements),
        "candidate_feasible_rate": _numbers(feasible_rates),
        "candidate_strict_improvement_rate": _numbers(candidate_improvement_rates),
        "token_usage": token_totals,
        "statistics_note": (
            "Descriptive run-level statistics only; no significance claim. "
            "Unknown measurements are excluded, not treated as zero."
        ),
    }


def _campaign_report(
    campaign_id: str, manifest_sha256: str, requested: int,
    run_records: list[dict], state: str,
) -> dict:
    return {
        "schema_version": CAMPAIGN_SCHEMA,
        "report_type": "chiprd_agent_campaign",
        "campaign_id": campaign_id,
        "state": state,
        "status": "pass" if state == "completed" else "fail",
        "updated_at_utc": _now(),
        "campaign_manifest_sha256": manifest_sha256,
        "aggregate": _aggregate(run_records, requested),
        "runs": run_records,
        "limitations": [
            "Repeated runs use the same benchmark seed and budget, but model providers may be nondeterministic.",
            "Provider-reported token usage may be missing or provider-specific; unknown is not zero.",
            "Generic Yosys cell count is a synthesis proxy, not physical area, timing or power.",
            "Public verification gates are not a secret holdout suite or complete functional proof.",
            "Run-to-run descriptive statistics do not establish statistical significance or generalization.",
        ],
    }


def run_agent_campaign(
    command: list[str],
    output: Path,
    runs: int = 3,
    budget: AgentLoopBudget | None = None,
    seed: int = 20261113,
    campaign_id: str | None = None,
) -> dict:
    """Run N independent loops from the same baseline, then aggregate evidence."""
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= MAX_RUNS:
        raise ValueError(f"runs must be an integer from 1 to {MAX_RUNS}")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer in [0, 2**32 - 1]")
    if not isinstance(command, list) or not command or any(
        not isinstance(arg, str) or not arg or "\x00" in arg for arg in command
    ):
        raise ValueError("command must be a non-empty argv list of strings")
    budget = budget or AgentLoopBudget()
    budget.validate()
    campaign_id = _safe_id(
        campaign_id or datetime.now(timezone.utc).strftime("campaign-%Y%m%dT%H%M%S%fZ"),
        "campaign_id",
    )
    if len(campaign_id) > 48:
        raise ValueError("campaign_id must be at most 48 characters")

    root = ROOT.resolve()
    output = output.resolve()
    try:
        relative_output = output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Agent campaign output must be inside the repository root") from exc
    if output == root:
        raise ValueError("Agent campaign output must be a dedicated subdirectory")
    if output.exists():
        raise FileExistsError(f"Refusing to reuse existing campaign directory: {output}")

    baseline = root / BASELINE
    task_spec = root / TASK_SPEC
    if baseline.is_symlink() or not baseline.is_file() or task_spec.is_symlink() or not task_spec.is_file():
        raise ValueError("Golden RTL and task specification must be regular, non-symlink files")
    baseline_hash = _sha_file(baseline)
    task_spec_hash = _sha_file(task_spec)
    controller_hash = _sha_file(Path(__file__).resolve())
    output.mkdir(parents=True)

    manifest = {
        "schema_version": CAMPAIGN_SCHEMA,
        "manifest_type": "chiprd_agent_campaign",
        "campaign_id": campaign_id,
        "created_at_utc": _now(),
        "task_id": "cordic.rotation.q14.v1",
        "output_directory": relative_output.as_posix(),
        "agent": {
            "command_argv": command,
            "local_file_fingerprints": _adapter_files(command),
            "model_configuration": _safe_model_config_environment(),
            "environment_secrets": "API keys and other credentials are never captured",
        },
        "experimental_controls": {
            "runs": runs,
            "same_seed_each_run": seed,
            "same_agent_command": True,
            "same_budget": budget.__dict__,
            "fresh_golden_baseline_each_run": True,
        },
        "baseline_sha256": baseline_hash,
        "task_spec_sha256": task_spec_hash,
        "campaign_controller_sha256": controller_hash,
        "agent_loop_controller_sha256": _sha_file(Path(_agent_loop_module.__file__).resolve()),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "evidence_boundary": (
            "This campaign orchestrates public-gate experiments; it does not create "
            "a private holdout set or prove physical PPA."
        ),
    }
    manifest_path = output / "campaign_manifest.json"
    _write_json(manifest_path, manifest)
    manifest_hash = _sha_file(manifest_path)
    run_records: list[dict] = []
    report_path = output / "campaign_report.json"
    _atomic_write(report_path, _campaign_report(
        campaign_id, manifest_hash, runs, run_records, "running"
    ))

    for index in range(1, runs + 1):
        run_id = f"{campaign_id}-r{index:03d}"
        run_output = output / "runs" / f"run-{index:03d}"
        row = {
            "run_index": index,
            "loop_id": run_id,
            "status": "error",
            "report_path": None,
            "report_sha256": None,
            "scorecard": None,
            "error_type": None,
        }
        try:
            result = run_agent_loop(
                command, run_output, budget=budget, seed=seed, loop_id=run_id
            )
            run_report = Path(result["report_path"])
            row["report_path"] = run_report.relative_to(root).as_posix()
            row["report_sha256"] = _sha_file(run_report)
            scorecard = summarize(run_report)
            row["scorecard"] = scorecard
            run_manifest_path = Path(result["manifest_path"])
            row["run_manifest_path"] = run_manifest_path.relative_to(root).as_posix()
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            run_report_data = json.loads(run_report.read_text(encoding="utf-8"))
            baseline_run = next(
                (item for item in run_report_data.get("experiments", [])
                 if item.get("candidate_id") == "baseline"),
                {},
            )
            toolchain_fingerprint = None
            evaluator_fingerprint = None
            experiment_dir = baseline_run.get("experiment_directory")
            if isinstance(experiment_dir, str):
                sealed_dir = (root / experiment_dir).resolve()
                try:
                    sealed_dir.relative_to(root)
                    experiment_manifest_path = sealed_dir / "experiment_manifest.json"
                    if experiment_manifest_path.is_file() and not experiment_manifest_path.is_symlink():
                        experiment_manifest = json.loads(
                            experiment_manifest_path.read_text(encoding="utf-8")
                        )
                        toolchain = experiment_manifest.get("toolchain")
                        if isinstance(toolchain, dict):
                            toolchain_fingerprint = _canonical_hash(toolchain)
                        evaluator = experiment_manifest.get("evaluator")
                        if isinstance(evaluator, dict):
                            evaluator_fingerprint = evaluator.get("fingerprint_sha256")
                except (ValueError, OSError, json.JSONDecodeError):
                    pass
            row["provenance"] = {
                "baseline_sha256": run_manifest.get("baseline", {}).get("candidate_sha256"),
                "seed": run_manifest.get("seed"),
                "budget": run_manifest.get("budget"),
                "controller_sha256": run_manifest.get("controller_sha256"),
                "agent_file_fingerprints": run_manifest.get("agent", {}).get("local_file_fingerprints"),
                "toolchain_fingerprint": toolchain_fingerprint,
                "evaluator_fingerprint": evaluator_fingerprint,
            }
            row["status"] = result.get("status", scorecard.get("run_status", "unknown"))
            row["best_improvement_percent"] = scorecard.get("best_improvement_percent")
            row["model_ids"] = scorecard.get("model_ids", [])
        except Exception as exc:
            # Keep the campaign usable after a single failed run; do not serialize
            # exception text, which could contain paths, provider data, or secrets.
            row["error_type"] = type(exc).__name__
        run_records.append(row)
        _atomic_write(report_path, _campaign_report(
            campaign_id, manifest_hash, runs, run_records,
            "running" if index < runs else "finalizing",
        ))

    state = (
        "partial" if len(run_records) < runs else
        "completed_with_errors" if any(row["status"] in {"error", "report_error"} for row in run_records)
        else "completed"
    )
    final = _campaign_report(campaign_id, manifest_hash, runs, run_records, state)
    provenance = [row.get("provenance") for row in run_records if isinstance(row.get("provenance"), dict)]
    def consistent(field: str) -> bool | None:
        values = [item.get(field) for item in provenance]
        if not values:
            return None
        canonical = {json.dumps(value, sort_keys=True) for value in values}
        return (len(canonical) == 1 and len(values) == len(run_records)
                and all(value is not None for value in values))

    final["comparability"] = {
        "same_baseline_sha256": consistent("baseline_sha256"),
        "same_seed": consistent("seed"),
        "same_budget": consistent("budget"),
        "same_controller_sha256": consistent("controller_sha256"),
        "same_agent_file_fingerprints": consistent("agent_file_fingerprints"),
        "same_toolchain_fingerprint": consistent("toolchain_fingerprint"),
        "same_evaluator_fingerprint": consistent("evaluator_fingerprint"),
        "note": (
            "A campaign uses controlled settings, but model/provider nondeterminism remains. "
            "Inspect toolchain manifests and do not infer physical PPA or statistical significance."
        ),
    }
    final["campaign_report_sha256"] = _canonical_hash(final)
    _atomic_write(report_path, final)
    return {
        **final,
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
    }
