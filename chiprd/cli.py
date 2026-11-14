"""CLI for the evidence-gated CORDIC benchmark."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .agent_loop import AgentLoopBudget, parse_agent_command, run_agent_loop
from .agent_campaign import run_agent_campaign
from .ppa_campaign import run_ppa_campaign
from .audit import audit_candidate
from .equivalence import (
    DEFAULT_REFACTOR,
    DEFAULT_REFERENCE,
    run_equivalence,
    run_equivalence_selftest,
)
from .evidence import reproduce_experiment, run_experiment
from .formal import run_formal, run_formal_negative_control
from .harness import ROOT, run
from .mutation import run_mutations
from .oracle import expected, vectors
from .patches import apply_patch_proposal
from .ppa import compare_area
from .timing import compare_timing
from .search import SearchBudget, run_search


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-gated CORDIC benchmark")
    parser.add_argument("mode", choices=[
        "smoke", "verify", "synth", "all", "mutation", "formal",
        "formal-negative-control", "equivalence", "equivalence-selftest",
        "experiment", "reproduce", "search", "apply-patch", "agent-loop", "agent-campaign", "ppa-campaign", "audit", "ppa", "timing",
    ])
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--loop-report", type=Path, default=None)
    parser.add_argument("--audit-seed", type=int, default=None)
    parser.add_argument("--audit-vectors", type=int, default=128)
    parser.add_argument("--liberty", type=Path, default=None)
    parser.add_argument("--tech-lef", type=Path, default=None)
    parser.add_argument("--cells-lef", type=Path, default=None)
    parser.add_argument("--clock-period-ns", type=float, default=10.0)
    parser.add_argument("--io-delay-ns", type=float, default=0.5)
    parser.add_argument("--platform-label", type=str, default="user-supplied-liberty")
    parser.add_argument("--proposals", type=Path, default=None)
    parser.add_argument("--patch-proposal", type=Path, default=None)
    parser.add_argument("--agent-command-json", type=str, default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--max-agent-seconds", type=float, default=180.0)
    parser.add_argument("--loop-id", type=str, default=None)
    parser.add_argument("--campaign-id", type=str, default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--strategy", choices=["random", "greedy", "evolutionary"], default="random")
    parser.add_argument("--max-experiments", type=int, default=4)
    parser.add_argument("--max-wall-clock-seconds", type=float, default=1800.0)
    parser.add_argument("--max-experiment-seconds", type=float, default=600.0)
    parser.add_argument("--max-candidate-bytes", type=int, default=1_048_576)
    parser.add_argument("--elite-parent-fraction", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("artifacts/cordic"))
    parser.add_argument("--seed", type=int, default=20261112)
    args = parser.parse_args()
    if args.mode == "timing":
        if args.candidate is None or args.liberty is None or args.tech_lef is None or args.cells_lef is None:
            parser.error("timing requires --candidate, --liberty, --tech-lef and --cells-lef")
        result = compare_timing(
            args.candidate, args.reference, args.liberty,
            args.tech_lef, args.cells_lef, args.output,
            clock_period_ns=args.clock_period_ns,
            io_delay_ns=args.io_delay_ns,
        )
    elif args.mode == "ppa":
        if args.candidate is None or args.liberty is None:
            parser.error("ppa requires --candidate and --liberty")
        result = compare_area(
            args.candidate, args.liberty, args.output,
            reference=args.reference, platform_label=args.platform_label,
        )
    elif args.mode == "audit":
        if args.audit_seed is None:
            parser.error("audit requires an independently chosen --audit-seed")
        if args.loop_report is not None and args.candidate is not None:
            parser.error("audit accepts --candidate or --loop-report, not both")
        if args.loop_report is None and args.candidate is None:
            parser.error("audit requires --candidate or --loop-report")
        result = audit_candidate(
            args.candidate, args.output,
            audit_seed=args.audit_seed,
            count=args.audit_vectors,
            loop_report=args.loop_report,
        )
    elif args.mode == "reproduce":
        if args.manifest is None:
            parser.error("reproduce requires --manifest")
        result = reproduce_experiment(args.manifest, args.output)
    elif args.mode == "experiment":
        candidate = args.candidate or ROOT / "tasks/cordic/cordic_rotator.sv"
        result = run_experiment(candidate, args.output, seed=args.seed)
    elif args.mode == "search":
        if args.proposals is None:
            parser.error("search requires --proposals")
        budget = SearchBudget(
            max_experiments=args.max_experiments,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_experiment_seconds=args.max_experiment_seconds,
            max_candidate_bytes=args.max_candidate_bytes,
            elite_parent_fraction=args.elite_parent_fraction,
        )
        result = run_search(
            args.proposals, args.output, strategy=args.strategy,
            seed=args.seed, budget=budget,
        )
    elif args.mode == "apply-patch":
        if args.patch_proposal is None:
            parser.error("apply-patch requires --patch-proposal")
        result = apply_patch_proposal(args.patch_proposal, args.output)
    elif args.mode in {"agent-loop", "agent-campaign", "ppa-campaign"}:
        if args.agent_command_json is None:
            parser.error(f"{args.mode} requires --agent-command-json")
        try:
            agent_command = parse_agent_command(args.agent_command_json)
        except ValueError as exc:
            parser.error(str(exc))
        budget = AgentLoopBudget(
            max_iterations=args.max_iterations,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_agent_seconds=args.max_agent_seconds,
            max_experiment_seconds=args.max_experiment_seconds,
            max_candidate_bytes=args.max_candidate_bytes,
        )
        if args.mode == "agent-loop":
            result = run_agent_loop(
                agent_command, args.output, budget=budget,
                seed=args.seed, loop_id=args.loop_id,
            )
        elif args.mode == "ppa-campaign":
            if not all((args.liberty, args.tech_lef, args.cells_lef)):
                parser.error("ppa-campaign requires --liberty, --tech-lef, --cells-lef")
            from .agent_ppa import PPAObjective
            objective = PPAObjective(args.liberty, args.tech_lef, args.cells_lef,
                                     args.clock_period_ns, args.io_delay_ns)
            result = run_ppa_campaign(
                agent_command, args.output, objective,
                runs=args.runs, budget=budget, seed=args.seed,
                campaign_id=args.campaign_id or args.loop_id,
            )
        else:
            result = run_agent_campaign(
                agent_command, args.output, runs=args.runs, budget=budget,
                seed=args.seed, campaign_id=args.campaign_id or args.loop_id,
            )
    else:
        default_candidate = DEFAULT_REFACTOR if args.mode == "equivalence" else ROOT / "tasks/cordic/cordic_rotator.sv"
        candidate = args.candidate or default_candidate
        if args.mode == "smoke":
            checks = [(angle, expected(angle)) for angle in vectors(args.seed)]
            print(json.dumps({"status": "pass", "oracle_vectors": len(checks),
                              "first_vector": checks[0]}))
            return 0
        if args.mode == "mutation":
            result = run_mutations(candidate, args.output, seed=args.seed)
        elif args.mode == "formal":
            result = run_formal(candidate, args.output)
        elif args.mode == "formal-negative-control":
            result = run_formal_negative_control(candidate, args.output)
        elif args.mode == "equivalence":
            result = run_equivalence(args.reference, candidate, args.output)
        elif args.mode == "equivalence-selftest":
            result = run_equivalence_selftest(args.reference, DEFAULT_REFACTOR, args.output)
        else:
            result = run(candidate, args.output, mode=args.mode, seed=args.seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
