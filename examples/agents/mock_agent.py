"""Deterministic fixture Agent for CI; not an LLM or PPA optimizer."""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path


def main() -> int:
    request = json.load(sys.stdin)
    if request.get("task_id") != "cordic.rotation.q14.v1":
        raise SystemExit("unexpected task_id")
    iteration = request.get("iteration")
    if iteration == 1:
        template_path = Path("examples/patches/signed_delta_refactor.json")
        proposal = json.loads(template_path.read_text(encoding="utf-8"))
        parent = request["parent_candidate"]
        old_path = proposal["parent_candidate_path"]
        new_path = parent["candidate_path"]
        proposal["parent_candidate_id"] = parent["candidate_id"]
        proposal["parent_candidate_path"] = new_path
        proposal["parent_candidate_sha256"] = parent["candidate_sha256"]
        proposal["generation"] = parent["generation"] + 1
        # A campaign runs independent loops in one repository-wide patch store.
        # Include a short loop suffix so each run gets a unique candidate lineage.
        proposal["patch_id"] = "ci_signed_delta_" + str(request.get("loop_id", "loop"))[-12:]
        proposal["patch"] = proposal["patch"].replace(
            f"--- a/{old_path}\n+++ b/{old_path}",
            f"--- a/{new_path}\n+++ b/{new_path}",
        )
        response = {
            "schema_version": "1.0.0",
            "action": "patch",
            "proposal": proposal,
            "rationale": "CI fixture: submit one behavior-preserving signed-delta refactor.",
        }
    elif iteration == 2:
        if not request.get("previous_experiment"):
            raise SystemExit("closed-loop feedback is missing")
        parent = request["parent_candidate"]
        parent_path = parent["candidate_path"]
        source = Path(parent_path).read_text(encoding="utf-8")
        old_line = "    wire signed [31:0] x_next = x + x_delta;\\n"
        new_line = "    wire signed [31:0] x_next = x_delta + x;\\n"
        if source.count(old_line) != 1:
            raise SystemExit("expected first-round CORDIC architecture expression not found")
        candidate = source.replace(old_line, new_line, 1)
        patch = "".join(difflib.unified_diff(
            source.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"a/{parent_path}",
            tofile=f"b/{parent_path}",
        ))
        proposal = json.loads(Path("examples/patches/signed_delta_refactor.json").read_text(encoding="utf-8"))
        proposal.update({
            "patch_id": "ci_operand_order_" + str(request.get("loop_id", "loop"))[-12:],
            "parent_candidate_id": parent["candidate_id"],
            "parent_candidate_path": parent_path,
            "parent_candidate_sha256": parent["candidate_sha256"],
            "generation": parent["generation"] + 1,
            "patch": patch,
            "description": "CI fixture second generation: explore commutative operand ordering after first-round PPA feedback.",
        })
        response = {
            "schema_version": "1.0.0",
            "action": "patch",
            "proposal": proposal,
            "rationale": "CI fixture uses first-round feedback to submit a second equivalent RTL candidate.",
        }
    elif iteration == 3:
        if not request.get("previous_experiment"):
            raise SystemExit("second-round experiment feedback is missing")
        response = {
            "schema_version": "1.0.0",
            "action": "stop",
            "rationale": "CI fixture has completed two feedback-driven candidate generations.",
        }
    else:
        response = {
            "schema_version": "1.0.0",
            "action": "stop",
            "rationale": "CI fixture iteration limit reached.",
        }
    sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
