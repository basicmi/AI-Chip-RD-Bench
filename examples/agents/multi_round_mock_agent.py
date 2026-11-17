"""Deterministic two-generation Agent fixture for Phase 30 CI.

This is orchestration coverage only; it is not an LLM or PPA optimizer.
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path


def _template(parent: dict, patch_id: str) -> dict:
    proposal = json.loads(Path("examples/patches/signed_delta_refactor.json").read_text(encoding="utf-8"))
    old_path = proposal["parent_candidate_path"]
    new_path = parent["candidate_path"]
    proposal.update({
        "patch_id": patch_id,
        "parent_candidate_id": parent["candidate_id"],
        "parent_candidate_path": new_path,
        "parent_candidate_sha256": parent["candidate_sha256"],
        "generation": parent["generation"] + 1,
    })
    if old_path != new_path:
        proposal["patch"] = proposal["patch"].replace(
            f"--- a/{old_path}\n+++ b/{old_path}",
            f"--- a/{new_path}\n+++ b/{new_path}",
        )
    return proposal


def main() -> int:
    request = json.load(sys.stdin)
    if request.get("task_id") != "cordic.rotation.q14.v1":
        raise SystemExit("unexpected task_id")
    iteration = request.get("iteration")
    suffix = str(request.get("loop_id", "loop"))[-12:]
    parent = request["parent_candidate"]

    if iteration == 1:
        proposal = _template(parent, "ci_signed_delta_" + suffix)
        response = {
            "schema_version": "1.0.0", "action": "patch", "proposal": proposal,
            "rationale": "Generation 1: refactor conditional add/subtract into signed deltas.",
        }
    elif iteration == 2:
        if not request.get("previous_experiment"):
            raise SystemExit("first-generation experiment feedback is missing")
        path = Path(parent["candidate_path"])
        source = path.read_text(encoding="utf-8")
        old_line = "    wire signed [31:0] x_next = x + x_delta;\n"
        new_line = "    wire signed [31:0] x_next = x_delta + x;\n"
        if source.count(old_line) != 1:
            raise SystemExit("first-generation expression was not found exactly once")
        candidate = source.replace(old_line, new_line, 1)
        patch = "".join(difflib.unified_diff(
            source.splitlines(keepends=True), candidate.splitlines(keepends=True),
            fromfile=f"a/{parent['candidate_path']}",
            tofile=f"b/{parent['candidate_path']}",
        ))
        proposal = _template(parent, "ci_operand_order_" + suffix)
        proposal.update({
            "patch": patch,
            "description": "Generation 2: test equivalent commutative operand ordering after PPA feedback.",
        })
        response = {
            "schema_version": "1.0.0", "action": "patch", "proposal": proposal,
            "rationale": "Generation 2 uses first-round feedback to submit a second equivalent RTL candidate.",
        }
    elif iteration == 3:
        if not request.get("previous_experiment"):
            raise SystemExit("second-generation experiment feedback is missing")
        response = {
            "schema_version": "1.0.0", "action": "stop",
            "rationale": "Both candidate generations were evaluated; no further proposal is made.",
        }
    else:
        response = {
            "schema_version": "1.0.0", "action": "stop",
            "rationale": "Phase 30 CI iteration budget exhausted.",
        }
    sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
