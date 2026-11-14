"""Offline, audit-first comparison of archived model-backed PPA campaigns.

No network/model/EDA calls. A comparison is descriptive, never a claim that
either model is statistically superior or that pre-layout PPA is signoff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .ppa_campaign import _evidence, _rollup
from .benchmark_report import summarize

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CAMPAIGNS = 12


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Missing, symlinked, or oversized campaign evidence")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed campaign JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("Campaign JSON must contain an object")
    return obj


def _digest(value):
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("Missing SHA-256 provenance")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("Malformed SHA-256 provenance") from exc
    return value.lower()


def _safe_child(root, path_string):
    """Only relative artifacts rooted in this campaign are permitted."""
    if not isinstance(path_string, str):
        raise ValueError("Missing run report path")
    path = Path(path_string)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe run report path")
    # Existing Phase 16 reports use repository-root-relative run paths.
    marker = root.name
    if marker not in path.parts:
        raise ValueError("Run report is outside campaign")
    suffix = path.parts[path.parts.index(marker) + 1:]
    resolved = root.joinpath(*suffix)
    if not resolved.is_relative_to(root) or any(p.is_symlink() for p in (
        root, *list(resolved.parents)[:-1], resolved
    ) if p.exists()):
        raise ValueError("Symlinked campaign evidence path")
    if not resolved.resolve().is_relative_to(root.resolve()):
        raise ValueError("Run report escapes campaign")
    return resolved


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is not numeric")
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite")
    return x


def _equivalent(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(
        b, sort_keys=True, allow_nan=False
    )


def inspect_campaign(folder):
    folder = Path(folder).absolute()
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Campaign directory must be a non-symlink directory")
    manifest_file = folder / "campaign_manifest.json"
    report_file = folder / "campaign_report.json"
    manifest, report = _read(manifest_file), _read(report_file)
    if manifest.get("report_type") != "chiprd_agent_ppa_campaign_manifest" or (
        report.get("report_type") != "chiprd_agent_ppa_campaign"
    ) or manifest.get("schema_version") != "1.0.0" or report.get("schema_version") != "1.0.0":
        raise ValueError("Unsupported PPA campaign schema")
    if manifest.get("campaign_id") != report.get("campaign_id"):
        raise ValueError("Campaign identity mismatch")
    if _digest(report.get("manifest_sha256")) != _sha(manifest_file):
        raise ValueError("Campaign manifest SHA-256 mismatch")
    fingerprints = manifest.get("evaluator_fingerprints")
    if not isinstance(fingerprints, dict) or set(fingerprints) != {
        "agent_loop_sha256", "agent_ppa_sha256", "timing_sha256",
        "ppa_scorecard_sha256", "sealed_evidence_sha256"
    }:
        raise ValueError("Campaign lacks Phase 17 evaluator fingerprints")
    fingerprints = {key: _digest(v) for key, v in fingerprints.items()}
    for key in ("controller_sha256", "baseline_sha256", "task_spec_sha256"):
        _digest(manifest.get(key))
    ppa = manifest.get("ppa_configuration")
    if not isinstance(ppa, dict) or not isinstance(ppa.get("inputs"), dict):
        raise ValueError("Missing PPA conditions")
    for item in ("liberty", "tech_lef", "cells_lef"):
        _digest(ppa["inputs"][item]["sha256"])
    _number(ppa.get("clock_period_ns"), "clock period")
    _number(ppa.get("io_delay_ns"), "I/O delay")
    config = manifest.get("agent", {}).get("model_configuration", {})
    if not isinstance(config, dict) or not isinstance(config.get("model"), str) or not config["model"]:
        raise ValueError("Model must be identified in manifest")
    if not isinstance(manifest.get("budget"), dict):
        raise ValueError("Missing experimental budget")
    expected = manifest.get("runs")
    if isinstance(expected, bool) or not isinstance(expected, int) or not 1 <= expected <= 20:
        raise ValueError("Invalid run count")
    rows = report.get("runs")
    if not isinstance(rows, list) or len(rows) != expected:
        raise ValueError("Campaign runs are incomplete")
    if report.get("state") not in ("completed", "completed_with_errors"):
        raise ValueError("Campaign has not reached a terminal state")
    checked = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or row.get("run") != index:
            raise ValueError("Run IDs must be ordered and unique")
        if row.get("evidence") is None:
            checked.append(row)
            continue
        file = _safe_child(folder, row.get("loop_report_path"))
        if not file.is_file() or _sha(file) != _digest(row.get("loop_report_sha256")):
            raise ValueError("Run report SHA-256 mismatch")
        loop = _read(file)
        if loop.get("loop_id") != row.get("loop_id"):
            raise ValueError("Loop identity mismatch")
        actual = _evidence(loop)
        if not _equivalent(actual, row["evidence"]):
            raise ValueError("Campaign row diverges from measured loop evidence")
        # This also checks that summary is computable from the archived run.
        score = summarize(file)
        if not _equivalent(row.get("token_usage"),
                           score.get("token_usage", {}).get("total_tokens", {})):
            raise ValueError("Token telemetry diverges from archived run")
        checked.append(row)
    if not _equivalent(_rollup(checked, expected), report.get("aggregate")):
        raise ValueError("Campaign aggregate does not match archived runs")
    if report.get("state") == "completed" and any(x.get("evidence") is None for x in checked):
        raise ValueError("Completed campaign contains invalid runs")
    if report.get("status") != ("pass" if all(x.get("evidence") is not None for x in checked) else "fail"):
        raise ValueError("Campaign completion status inconsistent")
    # Deliberately exclude model ID/endpoint from the equivalence key.
    # Temperature, generation cap, budget, reference and PPA must match.
    controls = {
        "baseline_sha256": manifest["baseline_sha256"],
        "task_spec_sha256": manifest["task_spec_sha256"],
        "seed": manifest.get("seed"),
        "runs": expected,
        "budget": manifest["budget"],
        "ppa_configuration": ppa,
        "controller_sha256": manifest["controller_sha256"],
        "evaluator_fingerprints": fingerprints,
        "model_generation": {k: config.get(k) for k in (
            "temperature", "max_tokens", "timeout_seconds")},
    }
    return {
        "model": config["model"],
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": _sha(manifest_file),
        "report_sha256": _sha(report_file),
        "controls": controls,
        "aggregate": report["aggregate"],
        "valid_runs": sum(x.get("evidence") is not None for x in checked),
        "runs": expected,
        "partial": report["state"] != "completed",
    }


def compare_campaigns(folders):
    if not 2 <= len(folders) <= MAX_CAMPAIGNS:
        raise ValueError("Compare between 2 and 12 campaigns")
    campaigns = [inspect_campaign(p) for p in folders]
    first = campaigns[0]["controls"]
    for entry in campaigns[1:]:
        if not _equivalent(first, entry["controls"]):
            raise ValueError("Campaigns have mismatched task, PPA, controller or generation controls")
    models = [entry["model"] for entry in campaigns]
    if len(set(models)) != len(models):
        raise ValueError("Duplicate model identifiers: use separate IDs for model/config variants")
    rows = []
    for entry in campaigns:
        agg = entry["aggregate"]
        rows.append({
            "model": entry["model"], "campaign_id": entry["campaign_id"],
            "manifest_sha256": entry["manifest_sha256"],
            "report_sha256": entry["report_sha256"],
            "runs": entry["runs"], "valid_runs": entry["valid_runs"],
            "partial": entry["partial"],
            "improving_runs": agg["runs_with_improvement"],
            "mean_area_reduction_percent": agg["area_reduction_percent"]["mean"],
            "mean_candidate_ppa_feasible_rate": agg["candidate_ppa_feasible_rate"]["mean"],
            "reported_total_tokens_mean": agg["provider_reported_total_tokens"]["mean"],
            "runs_with_unknown_token_usage": agg["runs_with_unknown_token_usage"],
        })
    complete = all(not row["partial"] and row["valid_runs"] == row["runs"] for row in rows)
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_cross_model_ppa_comparison",
        "status": "pass" if complete else "partial",
        "comparison_ready": complete,
        "control_policy": "exact manifest-controlled task, PPA, evaluator and generation settings",
        "controls_sha256": hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest(),
        "models": rows,
        "winner": None,
        "limitations": [
            "No winner or statistical superiority is asserted from descriptive campaign statistics.",
            "Model API endpoints and underlying runtime/EDA tool binaries are not independently attested.",
            "Token counts are provider-reported and may be missing; missing does not mean zero.",
            "Public verification gates and pre-layout ideal-clock STA are not blind signoff PPA.",
            "Complete comparable campaign metadata does not prove identical hardware or silicon performance.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_directories", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.is_symlink() or args.output.exists():
        parser.error("Refusing to overwrite output")
    try:
        result = compare_campaigns(args.campaign_directories)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": result["status"], "models": len(result["models"]),
                      "comparison_ready": result["comparison_ready"]}))


if __name__ == "__main__":
    main()
