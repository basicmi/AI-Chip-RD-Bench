"""Offline final study scorecard: selected PPA evidence AND independent audit.

Never calls an Agent, provider or EDA. A candidate only contributes to an
'audited improvement' when it beats its Golden mapped area, satisfies setup
WNS, passes the six public gates, and passes the post-selection audit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .harness import ROOT
from .model_comparison import _read, _sha, _safe_child, compare_campaigns

AUDIT_REPORT_TYPE = "chiprd_two_model_ppa_post_selection_audit"


def _existing_under(root: Path, path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked evidence file")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes pilot root")
    return path


def summarize_study(pilot_dir: Path) -> dict:
    """Revalidate every archived control, selection and audit before counting."""
    pilot_dir = Path(pilot_dir).absolute()
    root = ROOT.resolve()
    if (not pilot_dir.is_dir() or pilot_dir.is_symlink()
            or not pilot_dir.resolve().is_relative_to(root)
            or pilot_dir.resolve() == root):
        raise ValueError("Study must live in a regular directory under repository")
    terminal = _read(_existing_under(pilot_dir, pilot_dir / "pilot_report.json"))
    if (terminal.get("schema_version") != "1.0.0"
            or terminal.get("report_type") != "chiprd_two_model_ppa_pilot"
            or terminal.get("pilot_id") != pilot_dir.name
            or terminal.get("status") not in ("completed", "partial")):
        raise ValueError("Invalid terminal pilot metadata")
    folders = [pilot_dir / "model-1", pilot_dir / "model-2"]
    comparison = compare_campaigns(folders)
    models = [row["model"] for row in comparison["models"]]
    if terminal.get("models") != models:
        raise ValueError("Pilot model identities disagree with archived campaigns")
    if terminal.get("comparison_ready") is not comparison["comparison_ready"]:
        raise ValueError("Pilot comparison flag disagrees with verified campaigns")
    existing_comparison = pilot_dir / "comparison.json"
    if existing_comparison.exists():
        archived_comparison = _read(_existing_under(pilot_dir, existing_comparison))
        if archived_comparison != comparison:
            raise ValueError("Archived model comparison differs from recomputed evidence")
    elif terminal.get("comparison_ready") is True:
        raise ValueError("Pilot claims comparison-ready but archived comparison is missing")

    audit_folder = pilot_dir / "post_selection_audit"
    audit_path = _existing_under(pilot_dir, audit_folder / "audit_summary.json")
    audit = _read(audit_path)
    if (audit.get("schema_version") != "1.0.0"
            or audit.get("report_type") != AUDIT_REPORT_TYPE
            or audit.get("pilot_id") != pilot_dir.name
            or audit.get("not_private_holdout") is not True
            or audit.get("feedback_to_agent") is not False):
        raise ValueError("Invalid post-selection audit metadata")
    seed = audit.get("audit_seed")
    vectors = audit.get("vectors_per_selection")
    if (type(seed) is not int or not 0 <= seed < 2**32
            or seed == terminal.get("seed")
            or type(vectors) is not int or not 32 <= vectors <= 4096):
        raise ValueError("Invalid independent audit controls")
    entries = audit.get("runs")
    expected_count = sum(row["runs"] for row in comparison["models"])
    if not isinstance(entries, list) or len(entries) != expected_count:
        raise ValueError("Missing post-selection run records")
    keyed = {}
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Malformed audit row")
        key = (row.get("model_index"), row.get("run"))
        if (type(key[0]) is not int or type(key[1]) is not int
                or key in keyed):
            raise ValueError("Duplicate or malformed audit run identity")
        keyed[key] = row

    results = []
    all_passed = True
    for index, (name, folder) in enumerate(zip(models, folders), 1):
        campaign = _read(_existing_under(pilot_dir, folder / "campaign_report.json"))
        scored = []
        for archive in campaign["runs"]:
            number = archive["run"]
            item = keyed.pop((index, number), None)
            if item is None:
                raise ValueError("Missing audit row for an archived run")
            evidence = archive.get("evidence")
            ppa_valid = evidence is not None
            digest = evidence.get("selected_sha256") if ppa_valid else None
            if (item.get("candidate_sha256") != digest
                    or item.get("loop_report_sha256") != (
                        archive.get("loop_report_sha256") if ppa_valid else None)):
                raise ValueError("Audit row selection identity mismatch")
            if ppa_valid:
                loop = _safe_child(folder, archive["loop_report_path"])
                if _sha(loop) != archive["loop_report_sha256"]:
                    raise ValueError("Frozen loop report mutated since campaign")
            status = item.get("status")
            audit_pass = False
            if status in ("pass", "fail"):
                if not ppa_valid or not isinstance(digest, str):
                    raise ValueError("Audit cannot pass or fail an absent selection")
                file = audit_folder / f"model-{index}-run-{number:03d}" / "audit_report.json"
                report = _read(_existing_under(pilot_dir, file))
                if (item.get("audit_report") != file.relative_to(root).as_posix()
                        or report.get("report_type") != "chiprd_post_selection_audit"
                        or report.get("candidate", {}).get("sha256") != digest
                        or report.get("selection", {}).get("loop_report_sha256")
                        != archive["loop_report_sha256"]
                        or report.get("audit", {}).get("seed") != seed
                        or report.get("audit", {}).get("requested_vectors") != vectors
                        or report.get("status") != item.get("audit_status")):
                    raise ValueError("Audit summary not supported by archived audit report")
                audit_pass = report["status"] == "pass" and status == "pass"
                if status == "pass" and not audit_pass:
                    raise ValueError("False positive independent audit")
            elif status == "missing_evidence":
                if ppa_valid:
                    raise ValueError("Audit marks valid campaign evidence as missing")
            elif status != "error":
                raise ValueError("Unknown or unfinished audit row state")
            base_area = evidence.get("baseline_area") if ppa_valid else None
            area_pct = evidence.get("area_reduction_percent") if ppa_valid else None
            improved = evidence.get("improved_vs_baseline") is True if ppa_valid else False
            audited_improvement = bool(ppa_valid and audit_pass and improved
                                       and base_area is not None and area_pct is not None
                                       and area_pct > 0)
            if not audit_pass:
                all_passed = False
            scored.append({
                "run": number, "candidate_sha256": digest,
                "ppa_evidence_valid": ppa_valid,
                "timing_constrained_area_improved": improved,
                "area_reduction_percent": area_pct,
                "post_selection_audit": "pass" if audit_pass else status,
                "audited_area_improvement": audited_improvement,
            })
        results.append({
            "model": name, "runs_requested": len(scored),
            "ppa_evidence_runs": sum(x["ppa_evidence_valid"] for x in scored),
            "ppa_improved_runs": sum(x["timing_constrained_area_improved"] for x in scored),
            "post_selection_passed_runs": sum(x["post_selection_audit"] == "pass" for x in scored),
            "audited_improved_runs": sum(x["audited_area_improvement"] for x in scored),
            "audited_improvement_rate": (
                sum(x["audited_area_improvement"] for x in scored) / len(scored)
                if scored else None),
            "runs": scored,
        })
    if keyed:
        raise ValueError("Unexpected audit rows without matching campaign")
    passes = sum(x["post_selection_passed_runs"] for x in results)
    if (audit.get("audited_run_count") != expected_count
            or audit.get("passed_audits") != passes
            or audit.get("status") != ("pass" if passes == expected_count else "fail")):
        raise ValueError("Post-selection audit aggregate inconsistent")
    complete = comparison["comparison_ready"] and all_passed and (
        terminal.get("status") == "completed")
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_evidence_backed_model_ppa_study",
        "pilot_id": terminal["pilot_id"],
        "status": "complete" if complete else "incomplete",
        "complete_evidence": complete,
        "comparison_ready": comparison["comparison_ready"],
        "pilot_report_sha256": _sha(pilot_dir / "pilot_report.json"),
        "audit_summary_sha256": _sha(audit_path),
        "audit_seed": seed, "audit_vectors": vectors,
        "models": results, "winner": None,
        "notes": [
            "Audited improvement requires six public gates, mapped-area improvement under WNS >= 0, and post-selection sampled numerical audit.",
            "Missing, failing or ambiguous audits contribute zero verified improvements; incomplete studies are not model rankings.",
            "Public sampled audit is not private holdout, formal completeness, power measurement or physical signoff.",
            "No statistical significance or model superiority is asserted from a small sample.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.is_symlink() or args.output.exists():
        parser.error("Refusing to overwrite study report")
    try:
        result = summarize_study(args.pilot_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (ValueError, TypeError, KeyError, OSError, IndexError, json.JSONDecodeError) as exc:
        parser.error(f"Study evidence rejected: {type(exc).__name__}")
    print(json.dumps({"status": result["status"], "complete_evidence": result["complete_evidence"]}))
    return 0 if result["complete_evidence"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
