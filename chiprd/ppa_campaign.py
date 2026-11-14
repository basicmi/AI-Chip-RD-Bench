"""Repeated model-driven, timing-constrained CORDIC benchmark campaigns.

No model invocation happens without an explicitly supplied Agent command.
Archived candidate evidence is never confused with independent signoff.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import agent_campaign
from .agent_loop import AgentLoopBudget, _safe_id, _sha_file, run_agent_loop
from .agent_ppa import PPAObjective
from .benchmark_report import summarize
from .harness import ROOT

MAX_RUNS = 20


def _stats(values):
    values = [float(x) for x in values if isinstance(x, (int, float))
              and not isinstance(x, bool) and math.isfinite(float(x))]
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 6) if values else None,
        "median": round(statistics.median(values), 6) if values else None,
        "sample_stdev": round(statistics.stdev(values), 6) if len(values) > 1 else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _evidence(row):
    """Cross-check the selected candidate against the archived, gated experiments."""
    if row.get("objective_mode") != "timing_constrained_mapped_area":
        raise ValueError("Not a PPA-aware Agent loop")
    experiments = row.get("experiments")
    baseline = row.get("baseline")
    if not isinstance(experiments, list) or not isinstance(baseline, dict):
        raise ValueError("Missing Agent experiments or baseline")
    measured = []
    for item in experiments:
        if not isinstance(item, dict):
            raise ValueError("Malformed experiment")
        ppa = item.get("ppa")
        if not isinstance(ppa, dict):
            raise ValueError("Missing PPA evidence on an experiment")
        gated = item.get("feasible") is True
        valid = gated and ppa.get("status") == "pass" and ppa.get("feasible") is True
        if valid:
            area = ppa.get("mapped_cell_area")
            wns = ppa.get("worst_setup_slack_ns")
            if (isinstance(area, bool) or not isinstance(area, (float, int))
                    or not math.isfinite(area) or area <= 0
                    or isinstance(wns, bool) or not isinstance(wns, (float, int))
                    or not math.isfinite(wns) or wns < 0):
                raise ValueError("Invalid feasible PPA measurements")
            if not isinstance(item.get("candidate_sha256"), str):
                raise ValueError("Missing candidate identity")
            measured.append(item)
        elif ppa.get("feasible") is True:
            raise ValueError("PPA feasible without valid status and sealed gates")
    selected = row.get("best_candidate")
    if selected is not None:
        if not isinstance(selected, dict):
            raise ValueError("Malformed selected candidate")
        matches = [x for x in measured
                   if x.get("candidate_id") == selected.get("candidate_id")
                   and x.get("candidate_sha256") == selected.get("candidate_sha256")]
        if len(matches) != 1:
            raise ValueError("Selected candidate not uniquely supported by passing gates")
        chosen = matches[0]
        if abs(selected.get("mapped_cell_area", -1) - chosen["ppa"]["mapped_cell_area"]) > 1e-6:
            raise ValueError("Selected candidate area mismatch")
        if any(x["ppa"]["mapped_cell_area"] < chosen["ppa"]["mapped_cell_area"] - 1e-6
               for x in measured):
            raise ValueError("Selected candidate is not minimum feasible area")
    elif measured:
        raise ValueError("No selected candidate despite feasible experiments")
    candidates = [x for x in experiments if x.get("candidate_id") != "baseline"]
    base = next((x for x in measured if x.get("candidate_id") == "baseline"), None)
    base_area = base["ppa"]["mapped_cell_area"] if base else None
    if base_area is None and selected is not None:
        # Lack of feasible baseline is an explicit comparability limitation.
        pass
    improved = (selected is not None and base_area is not None
                and selected["mapped_cell_area"] < base_area)
    if row.get("improved_vs_baseline") is not improved:
        raise ValueError("Improvement flag disagrees with verified PPA evidence")
    return {
        "candidate_attempts": len(candidates),
        "candidate_sealed_feasible": sum(x.get("feasible") is True for x in candidates),
        "candidate_ppa_feasible": sum(x in measured for x in candidates),
        "candidate_ppa_evaluated": sum(
            isinstance(x.get("ppa"), dict) and x["ppa"].get("status") == "pass"
            for x in candidates),
        "baseline_area": base_area,
        "selected_area": selected["mapped_cell_area"] if selected else None,
        "selected_wns_ns": selected["worst_setup_slack_ns"] if selected else None,
        "selected_sha256": selected["candidate_sha256"] if selected else None,
        "improved_vs_baseline": improved,
        "area_reduction_percent": (
            round((base_area - selected["mapped_cell_area"]) * 100 / base_area, 6)
            if base_area and selected else None
        ),
    }


def _rollup(rows, count):
    valid = [x for x in rows if x.get("evidence") is not None]
    rates = []
    for row in valid:
        e = row["evidence"]
        if e["candidate_attempts"]:
            rates.append(e["candidate_ppa_feasible"] / e["candidate_attempts"])
    stats = {
        "runs_requested": count,
        "runs_with_valid_ppa_evidence": len(valid),
        "runs_with_improvement": sum(x["evidence"]["improved_vs_baseline"] for x in valid),
        "area_reduction_percent": _stats([x["evidence"]["area_reduction_percent"] for x in valid]),
        "candidate_ppa_feasible_rate": _stats(rates),
        "selected_area": _stats([x["evidence"]["selected_area"] for x in valid]),
        "selected_setup_wns_ns": _stats([x["evidence"]["selected_wns_ns"] for x in valid]),
        "attempted_candidates": sum(x["evidence"]["candidate_attempts"] for x in valid),
        "ppa_feasible_candidates": sum(x["evidence"]["candidate_ppa_feasible"] for x in valid),
    }
    # Never confuse unreported usage with zero tokens.
    stats["provider_reported_total_tokens"] = _stats([
        x["token_usage"]["reported_sum"] for x in valid
        if isinstance(x.get("token_usage"), dict)
        and isinstance(x["token_usage"].get("reported_sum"), int)
    ])
    stats["runs_with_unknown_token_usage"] = sum(
        not isinstance(x.get("token_usage", {}).get("reported_sum"), int)
        or x["token_usage"].get("unreported_calls", 0) > 0 for x in valid
    )
    return stats


def run_ppa_campaign(command, output: Path, objective: PPAObjective, *,
                     runs=3, budget=None, seed=20261113, campaign_id=None):
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= MAX_RUNS:
        raise ValueError("runs must be between 1 and 20")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be a uint32")
    budget = budget or AgentLoopBudget()
    budget.validate()
    if not isinstance(command, list) or not command or not all(
        isinstance(x, str) and x and "\x00" not in x for x in command
    ):
        raise ValueError("Agent command must be nonempty argv")
    config = objective.validate()
    campaign_id = _safe_id(campaign_id or datetime.now(timezone.utc).strftime(
        "ppa-%Y%m%dT%H%M%S%fZ"), "campaign_id")
    if len(campaign_id) > 48:
        raise ValueError("campaign_id must be at most 48 characters")
    root = ROOT.resolve()
    output = Path(output).resolve()
    if output == root or not output.is_relative_to(root):
        raise ValueError("Campaign output must be a dedicated directory in the repository")
    if output.exists():
        raise FileExistsError("Refusing to overwrite a previous PPA campaign")
    golden = root / "tasks/cordic/cordic_rotator.sv"
    task = root / "tasks/cordic/spec.md"
    if any(p.is_symlink() or not p.is_file() for p in (golden, task)):
        raise ValueError("Missing regular Golden RTL/task spec")
    manifest = {
        "schema_version": "1.0.0", "report_type": "chiprd_agent_ppa_campaign_manifest",
        "campaign_id": campaign_id,
        "baseline_sha256": _sha_file(golden),
        "task_spec_sha256": _sha_file(task),
        "seed": seed, "runs": runs, "budget": asdict(budget),
        "ppa_configuration": config,
        "agent": {
            "command_argv": command,
            "model_configuration": agent_campaign._safe_model_config_environment(),
            "note": "Environment credentials are not captured; Agent outputs may contain sensitive text",
        },
        "controller_sha256": _sha_file(Path(__file__)),
        "evaluator_fingerprints": {
            "agent_loop_sha256": _sha_file(Path(__import__("chiprd.agent_loop", fromlist=["__file__"]).__file__)),
            "agent_ppa_sha256": _sha_file(Path(__import__("chiprd.agent_ppa", fromlist=["__file__"]).__file__)),
            "timing_sha256": _sha_file(Path(__import__("chiprd.timing", fromlist=["__file__"]).__file__)),
            "ppa_scorecard_sha256": _sha_file(Path(__import__("chiprd.ppa_scorecard", fromlist=["__file__"]).__file__)),
            "sealed_evidence_sha256": _sha_file(Path(__import__("chiprd.evidence", fromlist=["__file__"]).__file__)),
        },
    }
    output.mkdir(parents=True)
    manifest_path = output / "campaign_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    rows = []
    for index in range(1, runs + 1):
        run_id = f"{campaign_id}-r{index:03d}"
        destination = output / "runs" / f"run-{index:03d}"
        row = {"run": index, "loop_id": run_id, "status": "error", "evidence": None}
        try:
            result = run_agent_loop(command, destination, budget=budget, seed=seed,
                                    loop_id=run_id, ppa_objective=objective)
            report_path = Path(result["report_path"])
            payload = json.loads(report_path.read_text())
            row["loop_report_sha256"] = _sha_file(report_path)
            row["loop_report_path"] = report_path.relative_to(root).as_posix()
            row["status"] = payload.get("status", "error")
            row["evidence"] = _evidence(payload)
            score = summarize(report_path)
            row["token_usage"] = score.get("token_usage", {}).get("total_tokens", {})
            row["model_ids"] = score.get("model_ids", [])
        except (ValueError, RuntimeError, OSError, KeyError, TypeError,
                json.JSONDecodeError) as exc:
            row["status"] = "error"
            # Do not serialize exception strings: provider responses can contain credentials.
            row["error_type"] = type(exc).__name__
        rows.append(row)
        # Crash-recovery evidence; all rows are preserved even if later runs fail.
        (output / "campaign_report.json").write_text(json.dumps({
            "schema_version": "1.0.0", "report_type": "chiprd_agent_ppa_campaign",
            "state": "running", "runs": rows, "aggregate": _rollup(rows, runs),
        }, indent=2, sort_keys=True) + "\n")
    final = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_agent_ppa_campaign",
        "state": ("completed" if all(x["evidence"] is not None for x in rows)
                  else "completed_with_errors"),
        "status": "pass" if all(x["evidence"] is not None for x in rows) else "fail",
        "campaign_id": campaign_id, "manifest_sha256": _sha_file(manifest_path),
        "runs": rows, "aggregate": _rollup(rows, runs),
        "comparability": {
            "same_baseline_and_sdc_config": True,
            "same_seed_and_budget": True,
            "same_agent_command": True,
            "note": "Configured controls match. Separate toolchain/evaluator drift or provider nondeterminism may still occur.",
        },
        "limitations": [
            "Provider-reported token usage can be missing or inaccurate; unknown is not zero.",
            "No live model is invoked unless caller explicitly configures an Agent.",
            "Only public gates and ideal-clock pre-layout Liberty/STA are measured, not signoff PPA.",
            "Small sample descriptive statistics do not imply significance or generalization.",
        ],
    }
    (output / "campaign_report.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n"
    )
    return final
