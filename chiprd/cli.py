"""CLI for the evidence-gated CORDIC benchmark."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .equivalence import (
    DEFAULT_REFACTOR,
    DEFAULT_REFERENCE,
    run_equivalence,
    run_equivalence_selftest,
)
from .formal import run_formal, run_formal_negative_control
from .harness import ROOT, run
from .mutation import run_mutations
from .oracle import expected, vectors


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-gated CORDIC benchmark")
    parser.add_argument("mode", choices=[
        "smoke", "verify", "synth", "all", "mutation", "formal",
        "formal-negative-control", "equivalence", "equivalence-selftest"
    ])
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=Path("artifacts/cordic"))
    parser.add_argument("--seed", type=int, default=20261112)
    args = parser.parse_args()
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
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
