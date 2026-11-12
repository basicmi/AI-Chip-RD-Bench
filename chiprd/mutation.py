"""Mutation testing for the public CORDIC evaluator.

The suite injects a small, explicit set of functional faults into a sealed copy
of the candidate and checks that the independent Verilator + analytic-oracle
flow rejects each one. This measures evaluator sensitivity to these mutations;
it is not a substitute for hidden tests or broad fault-model coverage.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT, TESTBENCH, verify

MUTATIONS = (
    {
        "id": "zero_initial_gain",
        "description": "Remove the CORDIC gain compensation by setting the initial x value to zero.",
        "before": "x <= 9949;",
        "after": "x <= 0;",
    },
    {
        "id": "reverse_x_rotation",
        "description": "Reverse the x-coordinate add/subtract decision.",
        "before": "wire signed [31:0] x_next = (z >= 0) ? x - y_shift : x + y_shift;",
        "after": "wire signed [31:0] x_next = (z >= 0) ? x + y_shift : x - y_shift;",
    },
    {
        "id": "corrupt_first_atan",
        "description": "Corrupt the first arctangent lookup-table entry.",
        "before": "0: atan_value = 6434;",
        "after": "0: atan_value = 0;",
    },
    {
        "id": "swap_outputs",
        "description": "Swap the sine and cosine output assignments.",
        "before": "                    x_out <= x_next[15:0];\n                    y_out <= y_next[15:0];",
        "after": "                    x_out <= y_next[15:0];\n                    y_out <= x_next[15:0];",
    },
    {
        "id": "suppress_valid",
        "description": "Suppress the completion-valid pulse while leaving the datapath unchanged.",
        "before": "                    output_valid <= 1'b1;",
        "after": "                    output_valid <= 1'b0;",
    },
)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_mutant(source: str, mutation: dict[str, str]) -> str:
    """Apply one exact, unique source mutation; fail if the RTL has drifted."""
    before = mutation["before"]
    if source.count(before) != 1:
        raise ValueError(
            f"Mutation {mutation['id']} expected exactly one occurrence of its target; "
            f"found {source.count(before)}"
        )
    return source.replace(before, mutation["after"], 1)


def run_mutations(candidate: Path, output: Path, seed: int = 20261112) -> dict:
    """Seal the candidate, run the baseline, then attempt to kill every mutant."""
    candidate = candidate.resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("Candidate must be a regular file")
    output.mkdir(parents=True, exist_ok=True)

    # Work from an immutable byte snapshot so concurrent edits cannot change
    # the source midway through this evaluation.
    source_bytes = candidate.read_bytes()
    source = source_bytes.decode("utf-8")
    snapshot = output / "candidate_snapshot.sv"
    snapshot.write_bytes(source_bytes)
    candidate_sha = sha256_bytes(source_bytes)

    manifest = {
        "schema_version": "0.1.0",
        "task_id": "cordic.rotation.q14.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(candidate),
        "candidate_sha256": candidate_sha,
        "testbench_sha256": sha256_bytes(TESTBENCH.read_bytes()),
        "oracle_sha256": sha256_bytes((ROOT / "chiprd" / "oracle.py").read_bytes()),
        "seed": seed,
        "mutation_ids": [item["id"] for item in MUTATIONS],
        "evaluation_policy": "baseline must pass; every non-equivalent public mutant must fail functional verification",
    }
    (output / "mutation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    report = {
        "schema_version": "0.1.0",
        "task_id": "cordic.rotation.q14.v1",
        "candidate_sha256": candidate_sha,
        "seed": seed,
        "baseline": {},
        "mutations": [],
        "mutation_score": None,
        "status": "error",
    }

    baseline_dir = output / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    try:
        baseline = verify(snapshot, baseline_dir, seed=seed)
    except Exception as exc:
        report["error"] = f"Baseline verification could not run: {exc}"
        (output / "mutation_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report
    report["baseline"] = baseline
    (baseline_dir / "report.json").write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if baseline.get("status") != "pass":
        report["status"] = "fail" if baseline.get("status") == "fail" else "error"
        report["reason"] = "baseline_not_passed; mutation results are not meaningful"
        (output / "mutation_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report

    for mutation in MUTATIONS:
        mutation_dir = output / "mutants" / mutation["id"]
        mutation_dir.mkdir(parents=True, exist_ok=True)
        mutant_path = mutation_dir / "candidate.sv"
        try:
            mutant_source = build_mutant(source, mutation)
            mutant_path.write_text(mutant_source, encoding="utf-8")
            verification = verify(mutant_path, mutation_dir, seed=seed)
            verification_status = verification.get("status")
            if verification_status == "fail":
                outcome = "killed"
            elif verification_status == "pass":
                outcome = "survived"
            else:
                outcome = "error"
            item = {
                "id": mutation["id"],
                "description": mutation["description"],
                "candidate_sha256": sha256_bytes(mutant_source.encode("utf-8")),
                "outcome": outcome,
                "verification_status": verification_status,
                "verification_reason": verification.get("reason"),
                "verification": verification,
            }
        except Exception as exc:
            item = {
                "id": mutation["id"],
                "description": mutation["description"],
                "outcome": "error",
                "error": str(exc),
            }
        report["mutations"].append(item)

    total = len(report["mutations"])
    killed = sum(item["outcome"] == "killed" for item in report["mutations"])
    survived = sum(item["outcome"] == "survived" for item in report["mutations"])
    errors = sum(item["outcome"] == "error" for item in report["mutations"])
    report["summary"] = {
        "total": total,
        "killed": killed,
        "survived": survived,
        "errors": errors,
    }
    report["mutation_score"] = killed / total if total else 0.0
    report["status"] = "pass" if killed == total and errors == 0 else (
        "error" if errors else "fail"
    )
    (output / "mutation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
