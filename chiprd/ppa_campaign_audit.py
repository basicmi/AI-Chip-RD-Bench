"""Independent post-selection audit for repeated PPA-aware Agent campaigns.

The audit runs only after campaign selection is complete. It does not return
results to the optimizing Agent and never upgrades missing/failed evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .audit import audit_candidate
from .harness import ROOT
from .ppa_campaign import _evidence

SCHEMA_VERSION = "1.0.0"
MAX_RUNS = 20
MAX_VECTORS = 4096
MIN_VECTORS = 32


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"{label} must be a bounded regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def audit_ppa_campaign(campaign_dir: Path, *, audit_seed: int,
                       vectors: int = 256) -> dict:
    """Audit every selected candidate with fresh seeds after PPA selection."""
    if type(audit_seed) is not int or not 0 <= audit_seed <= 2**32 - 1:
        raise ValueError("audit_seed must be a uint32")
    if type(vectors) is not int or not MIN_VECTORS <= vectors <= MAX_VECTORS:
        raise ValueError(f"audit vectors must be in [{MIN_VECTORS}, {MAX_VECTORS}]")

    root = ROOT.resolve()
    campaign_dir = Path(campaign_dir)
    if campaign_dir.is_symlink():
        raise ValueError("Campaign directory must not be a symlink")
    campaign_dir = campaign_dir.resolve(strict=True)
    try:
        campaign_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("Campaign directory must remain inside repository root") from exc
    if campaign_dir == root or not campaign_dir.is_dir():
        raise ValueError("Campaign directory must be a dedicated directory")

    manifest_path = campaign_dir / "campaign_manifest.json"
    report_path = campaign_dir / "campaign_report.json"
    manifest = _json(manifest_path, "Campaign manifest")
    report = _json(report_path, "Campaign report")
    if (manifest.get("report_type") != "chiprd_agent_ppa_campaign_manifest"
            or report.get("report_type") != "chiprd_agent_ppa_campaign"
            or manifest.get("campaign_id") != report.get("campaign_id")
            or report.get("state") != "completed" or report.get("status") != "pass"):
        raise ValueError("Only a completed, passing PPA campaign can be audited")
    manifest_hash = _sha(manifest_path)
    if report.get("manifest_sha256") != manifest_hash:
        raise ValueError("Campaign manifest hash mismatch")
    search_seed = manifest.get("seed")
    if type(search_seed) is not int or search_seed == audit_seed:
        raise ValueError("Independent audit seed must differ from the campaign search seed")

    runs = report.get("runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_RUNS:
        raise ValueError("Campaign must contain 1-20 run records")
    if len(runs) != manifest.get("runs"):
        raise ValueError("Campaign run count differs from its manifest")

    output = campaign_dir / "post_selection_audit"
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite prior post-selection audit")
    output.mkdir()

    rows = []
    for index, row in enumerate(runs, start=1):
        run_seed = audit_seed + index - 1
        if run_seed > 2**32 - 1 or run_seed == search_seed:
            raise ValueError("Derived per-run audit seed is invalid or overlaps search seed")
        try:
            if not isinstance(row, dict) or row.get("run") != index:
                raise ValueError("Malformed or unordered campaign run record")
            relative = row.get("loop_report_path")
            if not isinstance(relative, str) or not relative.startswith("artifacts/"):
                raise ValueError("Missing repository-relative Agent loop report")
            loop_path = (root / relative).resolve(strict=True)
            try:
                loop_path.relative_to(campaign_dir)
            except ValueError as exc:
                raise ValueError("Agent loop report escapes campaign directory") from exc
            if _sha(loop_path) != row.get("loop_report_sha256"):
                raise ValueError("Agent loop report hash mismatch")
            loop_report = _json(loop_path, "Agent loop report")
            verified = _evidence(loop_report)
            if verified != row.get("evidence"):
                raise ValueError("Campaign PPA summary differs from archived loop evidence")
            audit_report = audit_candidate(
                None, output / f"run-{index:03d}",
                audit_seed=run_seed, count=vectors, loop_report=loop_path,
            )
            rows.append({
                "run": index, "loop_report_sha256": _sha(loop_path),
                "candidate_sha256": audit_report.get("candidate", {}).get("sha256"),
                "audit_seed": run_seed, "status": audit_report.get("status"),
                "audit_report": f"run-{index:03d}/audit_report.json",
            })
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            rows.append({
                "run": index, "audit_seed": run_seed,
                "status": "error", "error_type": type(exc).__name__,
            })

    passed = sum(row["status"] == "pass" for row in rows)
    final = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "chiprd_agent_ppa_campaign_post_selection_audit",
        "campaign_id": report["campaign_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if passed == len(runs) else "fail",
        "campaign_manifest_sha256": manifest_hash,
        "campaign_report_sha256": _sha(report_path),
        "search_seed": search_seed,
        "audit_seed_start": audit_seed,
        "vectors_per_run": vectors,
        "runs_requested": len(runs),
        "runs_audited": len(rows),
        "runs_passed": passed,
        "runs_failed_or_error": len(rows) - passed,
        "runs": rows,
        "independence": {
            "audit_runs_after_campaign_completion": True,
            "audit_seed_differs_from_search_seed": True,
            "audit_feedback_to_agent": False,
            "not_a_private_holdout": True,
        },
        "limitations": [
            "Fresh public Verilator/oracle samples are an independent post-selection check, not a hidden holdout or formal proof.",
            "PPA remains Liberty-mapped area and ideal-clock pre-layout STA, not routed physical signoff or power.",
            "The audit does not establish statistical significance or generalization beyond the audited task and vectors.",
        ],
    }
    (output / "audit_summary.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return final
