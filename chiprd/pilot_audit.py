"""Post-selection independent-seed audit for a completed two-model PPA pilot.

No Agent/LLM calls. A seed supplied *after* selection drives fresh sampled
Verilator checks. Public audit code is not a hidden holdout or physical signoff.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import audit as audit_module
from .audit import MAX_VECTORS, MIN_VECTORS, audit_candidate
from .harness import ROOT
from .model_comparison import inspect_campaign, _safe_child

SCHEMA = "1.0.0"


def audit_pilot(pilot_dir: Path, *, audit_seed: int, vectors: int = 128) -> dict:
    if type(audit_seed) is not int or not 0 <= audit_seed <= 2**32 - 1:
        raise ValueError("audit_seed must be a uint32 chosen after selection")
    if type(vectors) is not int or not MIN_VECTORS <= vectors <= MAX_VECTORS:
        raise ValueError("audit_vectors outside permitted range")
    pilot_dir = Path(pilot_dir).absolute()
    root = ROOT.resolve()
    if (pilot_dir == root or not pilot_dir.resolve().is_relative_to(root)
            or pilot_dir.is_symlink() or not pilot_dir.is_dir()):
        raise ValueError("Pilot must be a non-symlink directory under repository root")
    report_path = pilot_dir / "pilot_report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("Missing terminal pilot report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (not isinstance(report, dict)
            or report.get("report_type") != "chiprd_two_model_ppa_pilot"
            or report.get("pilot_id") != pilot_dir.name
            or report.get("status") not in ("completed", "partial")):
        raise ValueError("Invalid terminal pilot report")
    if report.get("seed") == audit_seed:
        raise ValueError("Independent audit seed must differ from search seed")
    output = pilot_dir / "post_selection_audit"
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing post-selection audit")
    # Complete archival verification before starting any new Verilator work.
    campaigns = []
    for idx in (1, 2):
        folder = pilot_dir / f"model-{idx}"
        evidence = inspect_campaign(folder)
        campaigns.append((folder, evidence))
    if len({x[1]["model"] for x in campaigns}) != 2:
        raise ValueError("Both models must have distinct IDs")
    if not all(x[1]["controls"] == campaigns[0][1]["controls"] for x in campaigns):
        raise ValueError("Model campaigns have mismatched evaluation controls")
    if any(x[1]["controls"]["seed"] == audit_seed for x in campaigns):
        raise ValueError("Audit seed must differ from the campaign search seed")
    if report.get("models") != [x[1]["model"] for x in campaigns]:
        raise ValueError("Pilot model identities disagree with campaign manifests")
    selections = []
    for model_idx, (folder, inspected) in enumerate(campaigns, 1):
        campaign_report = json.loads((folder / "campaign_report.json").read_text(encoding="utf-8"))
        for row in campaign_report["runs"]:
            index = row["run"]
            if row.get("evidence") is None:
                selections.append({
                    "model_index": model_idx, "run": index, "status": "missing_evidence",
                    "candidate_sha256": None, "loop_report_sha256": None,
                    "loop_report": None,
                })
                continue
            loop_path = _safe_child(folder, row["loop_report_path"])
            selections.append({
                "model_index": model_idx, "run": index, "status": "pending",
                "candidate_sha256": row["evidence"]["selected_sha256"],
                "loop_report_sha256": row["loop_report_sha256"],
                "loop_report": loop_path,
            })
    output.mkdir()
    results = []
    for item in selections:
        row = {key: value for key, value in item.items() if key != "loop_report"}
        if item["status"] == "pending":
            audit_dir = output / f"model-{item['model_index']}-run-{item['run']:03d}"
            try:
                outcome = audit_candidate(
                    None, audit_dir, audit_seed=audit_seed,
                    count=vectors, loop_report=item["loop_report"])
                digest = outcome.get("candidate", {}).get("sha256")
                if digest != item["candidate_sha256"]:
                    raise ValueError("Audited candidate identity mismatch")
                row["status"] = "pass" if outcome.get("status") == "pass" else "fail"
                row["audit_status"] = outcome.get("status")
                row["audit_report"] = (audit_dir / "audit_report.json").relative_to(root).as_posix()
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                row["status"] = "error"
                row["error_type"] = type(exc).__name__
        results.append(row)
        # Durable checkpoint for interruption; does not invent completed audits.
        (output / "audit_progress.json").write_text(
            json.dumps({"schema_version": SCHEMA, "state": "running", "runs": results},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
    passed = sum(x["status"] == "pass" for x in results)
    final = {
        "schema_version": SCHEMA,
        "report_type": "chiprd_two_model_ppa_post_selection_audit",
        "pilot_id": report["pilot_id"],
        "audit_seed": audit_seed,
        "vectors_per_selection": vectors,
        "audited_run_count": len(results),
        "passed_audits": passed,
        "status": "pass" if passed == len(results) and results else "fail",
        "runs": results,
        "not_private_holdout": True,
        "feedback_to_agent": False,
        "limitations": [
            "The audit seed must be selected independently after model optimization concludes.",
            "Verilator sampled numerical vectors are not formal completeness or a hidden benchmark.",
            "A post-selection pass does not establish routed signoff, power or silicon performance.",
            "Original PPA comparison remains separately scoped; the audit does not alter selections.",
        ],
    }
    (output / "audit_summary.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "audit_progress.json").unlink(missing_ok=True)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", required=True, type=Path)
    parser.add_argument("--audit-seed", required=True, type=int,
                        help="choose independently AFTER the models have completed")
    parser.add_argument("--audit-vectors", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        result = audit_pilot(args.pilot_dir, audit_seed=args.audit_seed,
                             vectors=args.audit_vectors)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Exceptions may contain filenames/provider data: do not echo their text.
        parser.error(f"Audit setup rejected: {type(exc).__name__}")
    print(json.dumps({"status": result["status"], "audited": result["audited_run_count"],
                      "passed": result["passed_audits"]}, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
