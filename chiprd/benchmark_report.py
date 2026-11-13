"""Auditable scorecard for one or more Agent-loop experiment reports.

This is a measurement/aggregation tool, not a model benchmark claim.
Missing token usage and missing verification results are reported explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

SCHEMA = "1.0.0"
GATES = (
    "functional_synthesis", "mutation", "formal",
    "formal_negative_control", "equivalence", "equivalence_selftest",
)
COUNTERS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def _parse_run(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Report does not exist: {path}")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Report exceeds maximum size")
    raw = path.read_bytes()
    try:
        report = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON experiment report") from exc
    if not isinstance(report, dict) or report.get("report_type") != "chiprd_agent_closed_loop":
        raise ValueError("Input must be a chiprd_agent_closed_loop report")
    if report.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported report schema")
    if not isinstance(report.get("iterations"), list) or not isinstance(report.get("experiments"), list):
        raise ValueError("Report must contain iteration and experiment arrays")
    baseline = report.get("baseline")
    if not isinstance(baseline, dict):
        raise ValueError("Report must contain baseline")
    return {
        "source_file": str(path.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "report": report,
    }


def summarize(path: Path) -> dict:
    """Build a single-run scorecard, preserving missing/failed outcome categories."""
    source = _parse_run(path)
    report = source["report"]
    iterations = report["iterations"]
    experiments = report["experiments"]
    tested = [item for item in experiments if isinstance(item, dict) and item.get("candidate_id") != "baseline"]
    if len(tested) != len(experiments) - 1:
        raise ValueError("Malformed experiments array")
    baseline = report["baseline"]
    baseline_cells = baseline.get("generic_cell_count")
    if not _int(baseline_cells) or baseline_cells == 0 or baseline.get("feasible") is not True:
        baseline_cells = None

    attempts = len(iterations)
    submitted = sum(isinstance(i, dict) and (
        "proposal_sha256" in i or i.get("status") == "proposal_rejected"
    ) for i in iterations)
    patch_applied = sum(isinstance(i, dict) and isinstance(i.get("patch"), dict) for i in iterations)
    feasible = sum(x.get("feasible") is True for x in tested)
    improved = sum(
        x.get("feasible") is True and baseline_cells is not None
        and _int(x.get("generic_cell_count"))
        and x["generic_cell_count"] < baseline_cells for x in tested
    )
    gate_counts = {}
    for gate in GATES:
        observed = [x.get("gate_statuses", {}).get(gate) for x in tested
                    if isinstance(x.get("gate_statuses"), dict)]
        passed = sum(s == "pass" for s in observed)
        gate_counts[gate] = {
            "pass": passed,
            "attempted_candidates": len(tested),
            "pass_rate": _rate(passed, len(tested)),
            "missing_or_unreported": len(tested) - len(observed),
        }

    tokens = {key: 0 for key in COUNTERS}
    covered = {key: 0 for key in COUNTERS}
    models = set()
    model_configs = []
    for item in iterations:
        if not isinstance(item, dict):
            raise ValueError("Each iteration must be an object")
        model = item.get("model_id")
        if isinstance(model, str):
            models.add(model)
        config = item.get("model_config")
        if isinstance(config, dict) and config not in model_configs:
            model_configs.append(config)
        usage = item.get("usage")
        if usage is None:
            continue
        if not isinstance(usage, dict):
            raise ValueError("Invalid iteration usage")
        for key in COUNTERS:
            if key not in usage:
                continue
            if not _int(usage[key]):
                raise ValueError(f"Invalid token usage: {key}")
            tokens[key] += usage[key]
            covered[key] += 1

    best = report.get("best_candidate") or {}
    best_cells = best.get("generic_cell_count") if isinstance(best, dict) else None
    best_valid = _int(best_cells) and best.get("candidate_id") is not None
    best_improvement = (
        round((baseline_cells - best_cells) / baseline_cells * 100, 4)
        if baseline_cells is not None and best_valid else None
    )

    return {
        "schema_version": SCHEMA,
        "report_type": "chiprd_agent_benchmark_scorecard",
        "run_id": report.get("loop_id"),
        "input_report": source["source_file"],
        "input_report_sha256": source["source_sha256"],
        "run_status": report.get("status"),
        "stop_reason": report.get("stop_reason"),
        "model_ids": sorted(models),
        "model_configs": model_configs,
        "run_environment": "not verified by this scorecard; inspect experiment manifests",
        "budget": report.get("budget"),
        "budget_consumed": report.get("budget_consumed"),
        "candidates": {
            "agent_calls": attempts,
            "proposals_submitted": submitted,
            "proposals_applied": patch_applied,
            "evaluated": len(tested),
            "feasible": feasible,
            "strictly_improved": improved,
            "patch_acceptance_rate": _rate(patch_applied, submitted),
            "feasible_rate": _rate(feasible, len(tested)),
            "strict_improvement_rate": _rate(improved, len(tested)),
        },
        "gates": gate_counts,
        "baseline_generic_cell_count": baseline_cells,
        "best_candidate_id": best.get("candidate_id") if isinstance(best, dict) else None,
        "best_generic_cell_count": best_cells if best_valid else None,
        "best_improvement_percent": best_improvement,
        "iteration_status_counts": dict(sorted(Counter(
            str(item.get("status", "missing")) for item in iterations
        ).items())),
        "token_usage": {
            key: {
                "reported_sum": tokens[key] if covered[key] else None,
                "reported_calls": covered[key],
                "unreported_calls": attempts - covered[key],
            }
            for key in COUNTERS
        },
        "limitations": [
            "Token usage is optional provider-reported telemetry and is not independently verified.",
            "Unreported tokens are unknown, not zero. Provider cost cannot be inferred.",
            "No held-out verification is used. Public gates are not full functional proof.",
            "Yosys generic_cell_count is not physical area, timing or power.",
            "This scorecard summarizes archived runs; it does not execute an Agent or EDA tools.",
        ],
    }


def compare(paths: list[Path]) -> dict:
    if not paths:
        raise ValueError("At least one report path is required")
    runs = [summarize(path) for path in paths]
    unique = [row["run_id"] for row in runs]
    if len(unique) != len(set(unique)):
        raise ValueError("Duplicate run_id in comparison; use unique Agent loop IDs")
    return {
        "schema_version": SCHEMA,
        "report_type": "chiprd_agent_benchmark_comparison",
        "runs": runs,
        "comparability": {
            "same_baseline_cell_count": (
                len({row["baseline_generic_cell_count"] for row in runs}) == 1
                and all(row["baseline_generic_cell_count"] is not None for row in runs)
            ),
            "warning": (
                "Matching baseline cell count alone does not establish comparability. "
                "Confirm task, toolchain, evaluator hash, seeds and budgets in source manifests."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent RTL benchmark scorecard")
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = summarize(args.reports[0]) if len(args.reports) == 1 else compare(args.reports)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    output = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        if args.output.exists():
            parser.error("Refusing to overwrite an existing scorecard")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
