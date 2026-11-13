"""Post-selection CORDIC numerical audit, separate from Agent feedback.

The audit requires a candidate source path or a completed Agent-loop report.
It rechecks source hashes and evaluates NEW analytic-oracle stimulus vectors.
It NEVER calls an Agent and is NOT a private holdout service: all implementation
and checks remain public in this repository.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import harness
from .harness import ROOT

AUDIT_SCHEMA = "1.0.0"
MIN_VECTORS = 32
MAX_VECTORS = 4096
MAX_SOURCE_BYTES = 1_048_576


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_repo_file(root: Path, path: Path) -> Path:
    source = path if path.is_absolute() else root / path
    if source.is_symlink():
        raise ValueError("Audit source must not be a symlink")
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Audit source must be a regular, bounded-size file")
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("Audit source must remain inside the repository root") from exc
    return source


def _load_selected(loop_report: Path, root: Path) -> dict:
    report_path = _safe_repo_file(root, loop_report)
    raw = report_path.read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Loop report exceeds size limit")
    try:
        report = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Agent-loop JSON report") from exc
    if (not isinstance(report, dict) or
            report.get("report_type") != "chiprd_agent_closed_loop" or
            report.get("schema_version") != "1.0.0"):
        raise ValueError("Expected a versioned chiprd_agent_closed_loop report")
    best = report.get("best_candidate")
    if not isinstance(best, dict):
        raise ValueError("No best feasible candidate in loop report")
    candidate_id = best.get("candidate_id")
    digest = best.get("candidate_sha256")
    path = best.get("candidate_path")
    if not all(isinstance(x, str) for x in (candidate_id, digest, path)):
        raise ValueError("Selected candidate has no ID, hash or path")
    if len(digest) != 64 or any(x not in "0123456789abcdef" for x in digest):
        raise ValueError("Selected candidate SHA-256 invalid")
    experiments = report.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("Loop report has no verified experiment records")
    matches = [
        item for item in experiments if isinstance(item, dict)
        and item.get("candidate_id") == candidate_id
        and item.get("candidate_sha256") == digest
        and item.get("feasible") is True
        and item.get("status") == "pass"
    ]
    if len(matches) != 1:
        raise ValueError("Selected candidate is not uniquely backed by a passing feasible experiment")
    source = _safe_repo_file(root, Path(path))
    if _sha(source) != digest:
        raise ValueError("Selected RTL hash no longer matches the frozen experiment")
    return {
        "candidate": source,
        "candidate_id": candidate_id,
        "candidate_sha256": digest,
        "loop_report": report_path,
        "loop_report_sha256": hashlib.sha256(raw).hexdigest(),
        "loop_id": report.get("loop_id"),
        "selection_experiment": matches[0],
    }


def audit_candidate(
    candidate: Path | None,
    output: Path,
    *,
    audit_seed: int,
    count: int = 128,
    loop_report: Path | None = None,
) -> dict:
    """Re-evaluate a frozen RTL candidate without providing feedback to its Agent."""
    if isinstance(audit_seed, bool) or not isinstance(audit_seed, int) or not 0 <= audit_seed <= 2**32 - 1:
        raise ValueError("audit_seed must be a nonnegative 32-bit integer")
    if isinstance(count, bool) or not isinstance(count, int) or not MIN_VECTORS <= count <= MAX_VECTORS:
        raise ValueError(f"audit count must be in [{MIN_VECTORS}, {MAX_VECTORS}]")
    if (candidate is None) == (loop_report is None):
        raise ValueError("Provide exactly one of candidate or loop_report")

    root = ROOT.resolve()
    output = output.resolve()
    if output == root:
        raise ValueError("Audit output must be a dedicated directory")
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Audit output must remain inside the repository root") from exc
    if output.exists():
        raise FileExistsError("Refusing to overwrite prior audit output")

    selected = _load_selected(loop_report, root) if loop_report is not None else None
    source = selected["candidate"] if selected else _safe_repo_file(root, candidate)
    source_digest = _sha(source)
    output.mkdir(parents=True)
    report = {
        "schema_version": AUDIT_SCHEMA,
        "report_type": "chiprd_post_selection_audit",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "audit_class": "public_code_post_selection_independent_seed",
        "not_private_holdout": True,
        "feedback_to_agent": False,
        "candidate": {
            "path": source.relative_to(root).as_posix(),
            "sha256": source_digest,
            "selection_mode": "loop_best" if selected else "explicit_candidate",
            "candidate_id": selected["candidate_id"] if selected else None,
        },
        "selection": {
            "loop_id": selected["loop_id"],
            "loop_report_sha256": selected["loop_report_sha256"],
            "selected_experiment": selected["selection_experiment"],
        } if selected else None,
        "audit": {
            "seed": audit_seed, "requested_vectors": count,
            "oracle": "analytic math.cos/math.sin, 32 LSB tolerance",
            "testbench_sha256": _sha(harness.TESTBENCH),
            "oracle_sha256": _sha(root / "chiprd" / "oracle.py"),
            "harness_sha256": _sha(root / "chiprd" / "harness.py"),
        },
        "limitations": [
            "Audit seed must be chosen independently after candidate selection.",
            "The audit code and task specification are public; this is NOT a secret hidden test.",
            "This is numerical simulation on sampled inputs, not complete functional proof.",
            "No independent physical timing, area, power, PDK, or silicon measurement.",
            "Agent access controls require a separate isolated execution environment.",
        ],
    }
    try:
        result = harness.verify(source, output, seed=audit_seed, count=count)
        if _sha(source) != source_digest:
            report["status"] = "error"
            report["error"] = "Candidate source changed during audit"
        else:
            report["status"] = result.get("status", "error")
            report["functional"] = {
                "status": result.get("status"),
                "reason": result.get("reason"),
                "tested": result.get("tested"),
                "received": result.get("received"),
                "failure_count": len(result.get("failures", [])),
                "failures": result.get("failures", [])[:30],
                "tool": result.get("tool"),
                "tool_version": result.get("tool_version"),
            }
    except (RuntimeError, OSError, ValueError) as exc:
        report["status"] = "error"
        report["error_type"] = type(exc).__name__
    report_path = output / "audit_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
