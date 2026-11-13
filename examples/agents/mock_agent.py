"""Deterministic fixture Agent for CI; not an LLM or PPA optimizer."""
from __future__ import annotations

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
        response = {
            "schema_version": "1.0.0",
            "action": "stop",
            "rationale": (
                "CI fixture received the previous candidate's experiment feedback; "
                "stop after exercising the feedback hand-off."
            ),
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
