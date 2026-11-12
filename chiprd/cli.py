"""CLI: python -m chiprd.cli {smoke,verify,synth,all,mutation}."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .harness import ROOT, run
from .mutation import run_mutations
from .oracle import expected, vectors


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-gated CORDIC Phase 0 benchmark")
    parser.add_argument("mode", choices=["smoke", "verify", "synth", "all", "mutation"])
    parser.add_argument("--candidate", type=Path,
                        default=ROOT / "tasks" / "cordic" / "cordic_rotator.sv")
    parser.add_argument("--output", type=Path, default=Path("artifacts/cordic"))
    parser.add_argument("--seed", type=int, default=20261112)
    args = parser.parse_args()
    if args.mode == "smoke":
        checks = [(angle, expected(angle)) for angle in vectors(args.seed)]
        print(json.dumps({"status": "pass", "oracle_vectors": len(checks),
                          "first_vector": checks[0]}))
        return 0
    if args.mode == "mutation":
        result = run_mutations(args.candidate, args.output, seed=args.seed)
    else:
        result = run(args.candidate, args.output, mode=args.mode, seed=args.seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
