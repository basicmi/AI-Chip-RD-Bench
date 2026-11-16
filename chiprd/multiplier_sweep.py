"""Build a fair, fail-closed comparison of signed multiplier architectures."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT

MAX_JSON_BYTES = 32 * 1024 * 1024


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside repository root") from exc
    if not resolved.is_file() or resolved.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"{label} must be a regular file <=32 MiB")
    return resolved


def _json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _number(value, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{label} must be finite" + (" and positive" if positive else ""))
    return value


def _evaluate_architecture(row: dict, evidence_root: Path, root: Path) -> dict:
    architecture_id = row["id"]
    candidate = _inside(root / row["path"], root, f"{architecture_id} RTL")
    candidate_hash = _sha(candidate)
    base = evidence_root / architecture_id
    reports = {
        "functional": _inside(base / "functional" / "report.json", root, f"{architecture_id} functional report"),
        "formal": _inside(base / "formal" / "equivalence_report.json", root, f"{architecture_id} formal report"),
        "ppa": _inside(base / "ppa" / "ppa_report.json", root, f"{architecture_id} PPA report"),
        "timing": _inside(base / "timing" / "combinational_timing_report.json", root, f"{architecture_id} timing report"),
    }
    data = {key: _json(path, f"{architecture_id} {key} report") for key, path in reports.items()}
    functional, formal, ppa, timing = (data[k] for k in ("functional", "formal", "ppa", "timing"))
    if functional.get("status") != "pass" or functional.get("functional_status") != "pass":
        raise ValueError(f"{architecture_id}: sampled functional evaluation did not pass")
    if functional.get("candidate_sha256") != candidate_hash:
        raise ValueError(f"{architecture_id}: functional report candidate hash mismatch")
    if functional.get("task_id") != "multiplier.signed16x16.v1":
        raise ValueError(f"{architecture_id}: functional task id mismatch")
    if formal.get("status") != "pass" or formal.get("proof_status") != "equivalent":
        raise ValueError(f"{architecture_id}: combinational equivalence did not pass")
    if formal.get("candidate_sha256") != candidate_hash:
        raise ValueError(f"{architecture_id}: formal report candidate hash mismatch")
    if formal.get("task_id") != "multiplier.signed16x16.v1":
        raise ValueError(f"{architecture_id}: formal task id mismatch")

    if ppa.get("status") != "pass" or ppa.get("report_type") != "chiprd_liberty_mapped_area_comparison":
        raise ValueError(f"{architecture_id}: Liberty mapping did not pass")
    if ppa.get("top_module") != "int_mul16":
        raise ValueError(f"{architecture_id}: PPA top module mismatch")
    mapped = ppa.get("candidate", {}).get("mapping", {})
    if ppa.get("candidate", {}).get("sha256") != candidate_hash or mapped.get("source_sha256") != candidate_hash:
        raise ValueError(f"{architecture_id}: PPA report candidate hash mismatch")
    if mapped.get("status") != "pass":
        raise ValueError(f"{architecture_id}: mapped-cell evidence did not pass")
    area = _number(mapped.get("mapped_cell_area"), f"{architecture_id} mapped area", positive=True)
    cell_count = mapped.get("mapped_cell_count")
    if isinstance(cell_count, bool) or not isinstance(cell_count, int) or cell_count <= 0:
        raise ValueError(f"{architecture_id}: invalid mapped cell count")
    netlist_hash = mapped.get("mapped_netlist_sha256")
    if not isinstance(netlist_hash, str) or len(netlist_hash) != 64:
        raise ValueError(f"{architecture_id}: missing mapped netlist hash")
    mapped_file = _inside(base / "ppa" / "candidate" / "mapped.v", root, f"{architecture_id} mapped netlist")
    if _sha(mapped_file) != netlist_hash:
        raise ValueError(f"{architecture_id}: mapped netlist hash mismatch")

    if timing.get("status") != "pass" or timing.get("report_type") != "chiprd_combinational_pre_layout_timing":
        raise ValueError(f"{architecture_id}: combinational timing did not pass")
    if timing.get("timing_mode") != "input_to_output_max_delay_no_clock_no_parasitics":
        raise ValueError(f"{architecture_id}: timing mode mismatch")
    if timing.get("top_module") != "int_mul16":
        raise ValueError(f"{architecture_id}: timing top module mismatch")
    if timing.get("constraints", {}).get("input_ports") != ["a", "b"] or timing.get("constraints", {}).get("output_ports") != ["product"]:
        raise ValueError(f"{architecture_id}: constrained timing ports mismatch")
    if timing.get("inputs", {}).get("candidate", {}).get("sha256") != candidate_hash:
        raise ValueError(f"{architecture_id}: timing report candidate hash mismatch")
    timing_mapping = timing.get("candidate", {}).get("mapping", {})
    timing_data = timing.get("candidate", {}).get("timing", {})
    if timing.get("candidate", {}).get("status") != "pass" or timing_mapping.get("status") != "pass":
        raise ValueError(f"{architecture_id}: timing mapping did not pass")
    if timing_mapping.get("source_sha256") != candidate_hash:
        raise ValueError(f"{architecture_id}: timing mapping source hash mismatch")
    if timing_mapping.get("mapped_netlist_sha256") != netlist_hash:
        raise ValueError(f"{architecture_id}: PPA and timing use different mapped netlists")
    if timing_mapping.get("mapped_cell_area") != area or timing_mapping.get("mapped_cell_count") != cell_count:
        raise ValueError(f"{architecture_id}: PPA and timing mapped-cell statistics differ")
    if timing.get("inputs", {}).get("liberty", {}).get("sha256") != ppa.get("library", {}).get("sha256"):
        raise ValueError(f"{architecture_id}: PPA and timing Liberty hashes differ")
    if timing_data.get("critical_path_reported") is not True or timing_data.get("meets_max_delay") is not True:
        raise ValueError(f"{architecture_id}: no passing constrained critical path")
    delay = _number(timing_data.get("estimated_worst_path_delay_ns"), f"{architecture_id} delay", positive=True)
    slack = _number(timing_data.get("worst_max_delay_slack_ns"), f"{architecture_id} slack")
    if slack < 0:
        raise ValueError(f"{architecture_id}: negative max-delay slack")
    constraints = timing.get("constraints", {})
    return {
        "id": architecture_id,
        "label": row["label"],
        "family": row["family"],
        "description": row["description"],
        "rtl_path": row["path"],
        "rtl_sha256": candidate_hash,
        "functional_vectors": functional.get("tested_vectors"),
        "formal_status": formal["proof_status"],
        "platform_label": ppa.get("platform_label"),
        "liberty_sha256": ppa.get("library", {}).get("sha256"),
        "mapped_cell_area": area,
        "mapped_cell_count": cell_count,
        "estimated_worst_path_delay_ns": delay,
        "worst_max_delay_slack_ns": slack,
        "critical_startpoint": timing_data.get("critical_startpoint"),
        "critical_endpoint": timing_data.get("critical_endpoint"),
        "max_delay_budget_ns": constraints.get("max_delay_ns"),
        "input_transition_ns": constraints.get("input_transition_ns"),
        "output_load_pf": constraints.get("output_load_pf"),
        "mapped_netlist_sha256": netlist_hash,
        "evidence_sha256": {key: _sha(path) for key, path in reports.items()},
    }


def _dominates(left: dict, right: dict) -> bool:
    a1, d1 = left["mapped_cell_area"], left["estimated_worst_path_delay_ns"]
    a2, d2 = right["mapped_cell_area"], right["estimated_worst_path_delay_ns"]
    return a1 <= a2 and d1 <= d2 and (a1 < a2 or d1 < d2)


def build_comparison(catalog_path: Path, evidence_root: Path, output: Path) -> dict:
    root = ROOT.resolve()
    catalog_path = _inside(catalog_path, root, "architecture catalog")
    catalog = _json(catalog_path, "architecture catalog")
    if catalog.get("schema_version") != "1.0.0" or catalog.get("task_id") != "multiplier.signed16x16.v1":
        raise ValueError("Unsupported architecture catalog schema or task id")
    if catalog.get("top_module") != "int_mul16":
        raise ValueError("Architecture catalog top module must be int_mul16")
    rows = catalog.get("architectures")
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("Architecture catalog must contain at least two candidates")
    ids = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k] for k in ("id", "label", "path", "family", "description")):
            raise ValueError("Each architecture needs id, label, path, family and description")
        if row["id"] in ids:
            raise ValueError("Duplicate architecture id")
        ids.append(row["id"])
    evidence_root = Path(evidence_root)
    if evidence_root.is_symlink():
        raise ValueError("Evidence root must not be a symlink")
    evidence_root = evidence_root.resolve(strict=True)
    try:
        evidence_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("Evidence root must remain inside repository root") from exc

    results = [_evaluate_architecture(row, evidence_root, root) for row in rows]
    reference = results[0]
    for result in results[1:]:
        for key in ("platform_label", "liberty_sha256", "max_delay_budget_ns",
                    "input_transition_ns", "output_load_pf"):
            if result[key] != reference[key]:
                raise ValueError(f"Unfair comparison: {key} differs for {result['id']}")
        if result["functional_vectors"] != reference["functional_vectors"]:
            raise ValueError("Unfair comparison: functional vector counts differ")
    frontier = [
        row["id"] for row in results
        if not any(_dominates(other, row) for other in results if other["id"] != row["id"])
    ]
    baseline_area = reference["mapped_cell_area"]
    baseline_delay = reference["estimated_worst_path_delay_ns"]
    for row in results:
        row["area_delta_vs_baseline_percent"] = round(
            (row["mapped_cell_area"] / baseline_area - 1.0) * 100.0, 4
        )
        row["delay_delta_vs_baseline_percent"] = round(
            (row["estimated_worst_path_delay_ns"] / baseline_delay - 1.0) * 100.0, 4
        )
        row["pareto_optimal"] = row["id"] in frontier

    output = Path(output)
    if output.is_symlink() or output.exists():
        raise FileExistsError("Refusing to overwrite architecture comparison output")
    output = output.resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Output must remain inside repository root") from exc
    output.mkdir(parents=True)
    report = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_multiplier_architecture_comparison",
        "status": "pass",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "architecture_catalog_sha256": _sha(catalog_path),
        "task_id": catalog["task_id"],
        "comparison_policy": {
            "area_objective": "minimize",
            "delay_objective": "minimize",
            "pareto_dominance": "no_worse_in_both_and_strictly_better_in_at_least_one",
            "baseline_architecture": reference["id"],
            "same_liberty_and_timing_constraints_required": True,
            "all_candidates_must_pass_functional_and_formal_gates": True,
        },
        "architectures": results,
        "pareto_frontier": frontier,
        "limitations": [
            "Mapped standard-cell area and pre-layout input-to-output delay only; no power measurement.",
            "No placement, routing, wire parasitics, congestion, IR drop or silicon signoff.",
            "Nangate45 is a public reference library, not a modern production PDK.",
            "Pareto results apply only to this task, toolchain, library and declared constraints.",
            "The catalog baseline is a behavioral multiply operator; it is not guaranteed to be the best hand-designed architecture.",
        ],
    }
    (output / "architecture_comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "tasks/multiplier/architectures.json")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_comparison(args.catalog, args.evidence_root, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"Multiplier architecture comparison rejected: {type(exc).__name__}: {exc}")
    print(json.dumps({
        "status": report["status"],
        "architecture_count": len(report["architectures"]),
        "pareto_frontier": report["pareto_frontier"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
