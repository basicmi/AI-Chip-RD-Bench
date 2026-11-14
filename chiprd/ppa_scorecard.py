"""Fail-closed, offline comparison of pre-layout timing experiments.

Scores only like-for-like, fully passing timing reports. This does not turn
synthesis area or ideal-clock slack into physical PPA or Fmax.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

MAX_REPORT_BYTES = 4 * 1024 * 1024
PROVENANCE_FIELDS = ("liberty", "tech_lef", "cells_lef", "reference")
VERSION_FIELDS = ("yosys_version", "openroad_version")


def _finite(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return number


def _digest(value, name):
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name}: expected SHA-256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name}: invalid SHA-256 digest") from exc
    return value.lower()


def _read_report(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{path}: report must be a bounded non-symlink file")
    raw = path.read_bytes()
    try:
        report = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid JSON report") from exc
    if not isinstance(report, dict) or report.get("status") != "pass":
        raise ValueError(f"{path}: timing report did not pass")
    if report.get("report_type") != "chiprd_pre_layout_timing_comparison":
        raise ValueError(f"{path}: unsupported report type")
    if report.get("schema_version") != "1.0.0":
        raise ValueError(f"{path}: unsupported schema version")
    if report.get("timing_mode") != "ideal_clock_pre_layout_no_parasitics":
        raise ValueError(f"{path}: unsupported timing mode")
    inputs = report.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError(f"{path}: missing provenance inputs")
    provenance = {field: _digest(inputs[field]["sha256"], field) for field in PROVENANCE_FIELDS}
    candidate_hash = _digest(inputs["candidate"]["sha256"], "candidate")
    sdc = _digest(report.get("sdc_sha256"), "sdc")
    period = _finite(report.get("clock_period_ns"), "clock period", positive=True)
    io_delay = _finite(report.get("io_delay_ns"), "I/O delay")
    if not 0 <= io_delay <= period / 2:
        raise ValueError("invalid I/O delay")
    pairs = {}
    for side in ("candidate", "reference"):
        result = report.get(side)
        if not isinstance(result, dict) or result.get("status") != "pass":
            raise ValueError(f"{path}: {side} not evaluated")
        mapping = result.get("mapping", {})
        timing = result.get("timing", {})
        area = _finite(mapping.get("mapped_cell_area"), f"{side} area", positive=True)
        slack = _finite(timing.get("worst_setup_slack_ns"), f"{side} slack")
        if timing.get("critical_path_reported") is not True:
            raise ValueError(f"{path}: {side} lacks a critical path")
        if timing.get("meets_provided_clock_period") is not (slack >= 0):
            raise ValueError(f"{path}: {side} timing-met flag contradicts WNS")
        versions = []
        for field, value in (("yosys_version", mapping.get("yosys_version")),
                             ("openroad_version", result.get("openroad_version"))):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{path}: missing {side} {field}")
            versions.append(value)
        pairs[side] = {"area": area, "wns_ns": slack, "versions": tuple(versions)}
    if pairs["candidate"]["versions"] != pairs["reference"]["versions"]:
        raise ValueError(f"{path}: candidate/reference tool versions differ")
    baseline = pairs["reference"]
    candidate = pairs["candidate"]
    # An internal check catches corrupt or selectively edited comparison fields.
    comparison = report.get("comparison", {})
    if not isinstance(comparison, dict):
        raise ValueError(f"{path}: comparison missing")
    if abs(_finite(comparison.get("candidate_minus_reference_area"), "area delta")
           - (candidate["area"] - baseline["area"])) > 1.1e-5:
        raise ValueError(f"{path}: area delta inconsistent")
    if abs(_finite(comparison.get("candidate_minus_reference_wns_ns"), "WNS delta")
           - (candidate["wns_ns"] - baseline["wns_ns"])) > 1.1e-5:
        raise ValueError(f"{path}: WNS delta inconsistent")
    if comparison.get("candidate_meets_timing") is not (candidate["wns_ns"] >= 0):
        raise ValueError(f"{path}: comparison timing flag inconsistent")
    if comparison.get("reference_meets_timing") is not (baseline["wns_ns"] >= 0):
        raise ValueError(f"{path}: reference timing flag inconsistent")
    key = {
        **provenance, "sdc": sdc, "period_ns": period, "io_delay_ns": io_delay,
        "tool_versions": list(candidate["versions"]),
    }
    return {
        "source": str(path), "report_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_sha256": candidate_hash, "key": key,
        "candidate_area": candidate["area"], "candidate_wns_ns": candidate["wns_ns"],
        "reference_area": baseline["area"], "reference_wns_ns": baseline["wns_ns"],
    }


def score_reports(paths):
    """Compare multiple reports against an identical validated baseline."""
    if not paths:
        raise ValueError("at least one timing report is required")
    if len(paths) > 100:
        raise ValueError("at most 100 timing reports are supported")
    reports = [_read_report(path) for path in paths]
    key = reports[0]["key"]
    reference = reports[0]
    for report in reports[1:]:
        if report["key"] != key:
            raise ValueError(f"{report['source']}: timing provenance/constraints/toolchain mismatch")
        for metric in ("reference_area", "reference_wns_ns"):
            if abs(report[metric] - reference[metric]) > 1e-6:
                raise ValueError(f"{report['source']}: reference result mismatch")
    rows = []
    for report in reports:
        area = report["candidate_area"]
        wns = report["candidate_wns_ns"]
        rows.append({
            "source": report["source"],
            "report_sha256": report["report_sha256"],
            "candidate_sha256": report["candidate_sha256"],
            "mapped_cell_area": area,
            "worst_setup_slack_ns": wns,
            "timing_feasible": wns >= 0,
            "area_reduction_percent": round(100 * (reference["reference_area"] - area)
                                             / reference["reference_area"], 4),
            "improves_reference_area": area < reference["reference_area"],
            "pareto_nondominated": False,
        })
    # A candidate dominates another only if it is no worse on both dimensions,
    # and strictly better on at least one. Infeasible candidates are excluded.
    feasible = [r for r in rows if r["timing_feasible"]]
    for row in feasible:
        row["pareto_nondominated"] = not any(
            (other["mapped_cell_area"] <= row["mapped_cell_area"]
             and other["worst_setup_slack_ns"] >= row["worst_setup_slack_ns"]
             and (other["mapped_cell_area"] < row["mapped_cell_area"]
                  or other["worst_setup_slack_ns"] > row["worst_setup_slack_ns"]))
            for other in feasible
        )
    # Stable input-order tie break; the benchmark objective is minimum area
    # subject to nonnegative setup WNS, not unconstrained area reduction.
    winner = min(feasible, key=lambda r: r["mapped_cell_area"]) if feasible else None
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_timing_constrained_area_scorecard",
        "status": "pass",
        "objective": "minimum_liberty_mapped_area_subject_to_nonnegative_pre_layout_setup_wns",
        "provenance": key,
        "reference": {"mapped_cell_area": reference["reference_area"],
                      "worst_setup_slack_ns": reference["reference_wns_ns"]},
        "candidates": rows,
        "feasible_count": len(feasible),
        "selected_candidate_sha256": winner["candidate_sha256"] if winner else None,
        "selected_source": winner["source"] if winner else None,
        "selected_improves_reference": winner["improves_reference_area"] if winner else False,
        "limitations": [
            "Pre-layout ideal-clock OpenSTA slack and synthesized Liberty area only.",
            "No routed parasitics, power, multi-corner closure, or silicon validation.",
            "This scorecard does not independently verify functional correctness.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="timing_report.json files")
    parser.add_argument("--output", type=Path, required=True, help="new JSON scorecard path")
    args = parser.parse_args(argv)
    if args.output.is_symlink() or args.output.exists():
        parser.error("refusing to overwrite existing/symlink output")
    try:
        result = score_reports(args.reports)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "pass", "feasible_count": result["feasible_count"],
                      "selected_candidate_sha256": result["selected_candidate_sha256"]}))


if __name__ == "__main__":
    main()
