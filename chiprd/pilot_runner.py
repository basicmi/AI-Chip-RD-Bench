"""Explicit opt-in, two-model PPA pilot on the same EDA/RTL control set.

Default mode only prints a redacted plan. --execute permits live model calls;
a failed preflight prevents *both* campaigns from starting.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .agent_loop import AgentLoopBudget
from .agent_ppa import PPAObjective
from .harness import ROOT
from .model_comparison import compare_campaigns
from .model_probe import probe_configured_models
from .pilot_preflight import assess
from .ppa_campaign import run_ppa_campaign

SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
AGENT_COMMAND = ["python3", "examples/agents/llm_agent.py"]


def _models(values):
    if not isinstance(values, (tuple, list)) or len(values) != 2:
        raise ValueError("Exactly two model IDs are required")
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError("Each model ID must be a nonempty string <=200 chars")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Model IDs must not contain control characters")
    if values[0] == values[1]:
        raise ValueError("Two distinct model IDs are required")
    return list(values)


def _check_destination(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not SAFE_LABEL.fullmatch(name) or name in (".", ".."):
        raise ValueError("Pilot name must be a safe 1-48 character identifier")
    root = Path(root).resolve()
    if root == ROOT.resolve() or not root.is_relative_to(ROOT.resolve()):
        raise ValueError("Pilot output must be a dedicated location inside repository")
    destination = root / name
    if destination.is_symlink() or destination.exists():
        raise FileExistsError("Pilot output already exists; refusing to overwrite")
    return destination


def _preflight_for_model(model: str, objective: PPAObjective) -> dict:
    original = os.environ.get("CHIPRD_LLM_MODEL")
    try:
        os.environ["CHIPRD_LLM_MODEL"] = model
        report = assess(objective)
    finally:
        if original is None:
            os.environ.pop("CHIPRD_LLM_MODEL", None)
        else:
            os.environ["CHIPRD_LLM_MODEL"] = original
    return report


def pilot(models, output_root: Path, pilot_id: str, objective: PPAObjective, *,
          execute=False, runs=3, budget=None, seed=20261113,
          skip_model_probe=False):
    names = _models(models)
    if type(skip_model_probe) is not bool:
        raise ValueError("skip_model_probe must be boolean")
    if type(execute) is not bool:
        raise ValueError("execute must be a boolean")
    if isinstance(runs, bool) or not isinstance(runs, int) or not 1 <= runs <= 20:
        raise ValueError("runs must be in [1,20]")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    budget = budget or AgentLoopBudget(max_iterations=2)
    budget.validate()
    destination = _check_destination(output_root, pilot_id)
    # Offline preflight for every model happens before any API call.
    preflights = [_preflight_for_model(name, objective) for name in names]
    public = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_two_model_ppa_pilot",
        "pilot_id": pilot_id, "models": names, "runs_per_model": runs,
        "seed": seed, "budget": budget.__dict__,
        "preflights": preflights,
        "live_execution_requested": execute,
        "scope": "CORDIC public gates + pre-layout Liberty/OpenSTA; not signoff PPA",
    }
    if not execute:
        return {**public, "status": "dry_run", "started_model_campaigns": 0,
                "message": "Dry run: no model API calls and no EDA execution."}
    if not all(row["local_configuration_ready"] for row in preflights):
        return {**public, "status": "blocked_by_preflight",
                "started_model_campaigns": 0}
    # Before any paid/inference work, perform a bounded GET /models for both IDs.
    # Providers without this endpoint require an explicit opt-out.
    discovery = None
    if not skip_model_probe:
        try:
            discovery = probe_configured_models(names)
        except (OSError, ValueError, ImportError, TypeError) as exc:
            discovery = {"status": "fail", "reason": "probe_error",
                         "error_type": type(exc).__name__, "completion_calls": 0}
        if discovery.get("status") != "pass":
            return {**public, "status": "blocked_by_model_probe",
                    "model_probe": discovery, "started_model_campaigns": 0}
    # Repeat no-overwrite check immediately before any campaign is launched.
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Pilot output was created between preflight and execution")
    destination.mkdir(parents=True)
    public["model_probe"] = discovery if discovery is not None else {
        "status": "skipped_by_explicit_opt_out", "completion_calls": 0}
    (destination / "preflight.json").write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    original = os.environ.get("CHIPRD_LLM_MODEL")
    folders = []
    completed = []
    attempted = 0
    def checkpoint():
        # Preserve progress even if a later model run fails or execution is interrupted.
        (destination / "pilot_progress.json").write_text(json.dumps({
            "schema_version": "1.0.0", "pilot_id": pilot_id,
            "attempted_model_campaigns": attempted, "campaigns": completed,
            "state": "running", "comparison_ready": False,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checkpoint()
    try:
        for index, model in enumerate(names):
            os.environ["CHIPRD_LLM_MODEL"] = model
            campaign_id = f"{pilot_id}-m{index+1}"
            folder = destination / f"model-{index+1}"
            try:
                attempted += 1
                checkpoint()
                run_ppa_campaign(
                    AGENT_COMMAND, folder, objective, runs=runs,
                    budget=budget, seed=seed, campaign_id=campaign_id
                )
                folders.append(folder)
                completed.append({"index": index + 1, "state": "finished"})
                checkpoint()
            except Exception as exc:
                # Errors may contain provider response data or credentials.
                completed.append({"index": index + 1, "state": "error",
                                  "error_type": type(exc).__name__})
                checkpoint()
                break
    finally:
        if original is None:
            os.environ.pop("CHIPRD_LLM_MODEL", None)
        else:
            os.environ["CHIPRD_LLM_MODEL"] = original
    comparison = None
    if len(folders) == 2:
        try:
            comparison = compare_campaigns(folders)
            (destination / "comparison.json").write_text(
                json.dumps(comparison, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            completed.append({"state": "comparison_error",
                              "error_type": type(exc).__name__})
    final = {
        **public, "status": (
            "completed" if comparison and comparison["comparison_ready"]
            else "partial"
        ),
        "started_model_campaigns": attempted,
        "campaigns": completed,
        "comparison_ready": bool(comparison and comparison["comparison_ready"]),
        "comparison_file": (
            (destination / "comparison.json").relative_to(ROOT).as_posix()
            if comparison is not None else None
        ),
        "raw_artifacts": "private; inspect for model prompts, generated RTL and secrets before sharing",
    }
    (destination / "pilot_report.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "pilot_progress.json").unlink(missing_ok=True)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-a", required=True)
    parser.add_argument("--model-b", required=True)
    parser.add_argument("--pilot-id", default="first-measured-pilot")
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts/real-model-pilots")
    parser.add_argument("--liberty", type=Path, required=True)
    parser.add_argument("--tech-lef", type=Path, required=True)
    parser.add_argument("--cells-lef", type=Path, required=True)
    parser.add_argument("--clock-period-ns", type=float, default=10)
    parser.add_argument("--io-delay-ns", type=float, default=0.5)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-iterations", type=int, default=2)
    parser.add_argument("--max-agent-seconds", type=float, default=120)
    parser.add_argument("--max-experiment-seconds", type=float, default=600)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=3600)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--skip-model-probe", action="store_true",
                        help="explicit opt-out for providers without GET /models")
    parser.add_argument("--execute", action="store_true",
                        help="explicitly allow live model calls and EDA experiments")
    args = parser.parse_args(argv)
    try:
        result = pilot(
            [args.model_a, args.model_b], args.output_root, args.pilot_id,
            PPAObjective(args.liberty, args.tech_lef, args.cells_lef,
                         args.clock_period_ns, args.io_delay_ns),
            execute=args.execute, runs=args.runs, seed=args.seed,
            skip_model_probe=args.skip_model_probe,
            budget=AgentLoopBudget(
                max_iterations=args.max_iterations,
                max_agent_seconds=args.max_agent_seconds,
                max_experiment_seconds=args.max_experiment_seconds,
                max_wall_clock_seconds=args.max_wall_clock_seconds,
            ),
        )
    except (ValueError, OSError, TypeError) as exc:
        # Do not leak provider error details into argparse output.
        parser.error(f"Pilot setup rejected: {type(exc).__name__}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("dry_run", "completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
