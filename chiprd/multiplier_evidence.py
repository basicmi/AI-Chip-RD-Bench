"""Integrity-checked evidence manifest for the experimental signed multiplier.

The manifest binds sampled functional evaluation, combinational equivalence,
mutation sensitivity, Liberty mapping and clockless OpenSTA timing to one RTL
source digest. Hashes provide reproducibility/integrity checks, not signatures
or protection against an actor who can rewrite every artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT

SCHEMA_VERSION = "1.0.0"
MAX_REPORT_BYTES = 32 * 1024 * 1024
EVIDENCE_KEYS = ("functional", "formal", "mutation", "ppa", "timing")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(path: Path, root: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside repository root") from exc
    if not resolved.is_file() or resolved.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError(f"{label} must be a regular file <=32 MiB")
    return resolved


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _inspect(candidate: Path, reports: dict[str, Path], root: Path) -> dict:
    source = _safe_file(candidate, root, "candidate RTL")
    source_hash = _sha(source)
    loaded = {}
    records = {}
    for key in EVIDENCE_KEYS:
        path = _safe_file(reports[key], root, f"{key} report")
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError(f"{key} report exceeds size limit")
        data = _load_json(path, f"{key} report")
        _require(data.get("status") == "pass", f"{key} evidence is not passing")
        loaded[key] = data
        records[key] = {"path": _relative(path, root), "sha256": _sha(path)}

    functional = loaded["functional"]
    _require(functional.get("report_type") == "chiprd_multiplier_experimental_evaluation",
             "functional report type mismatch")
    _require(functional.get("candidate_sha256") == source_hash,
             "functional report candidate hash mismatch")
    _require(functional.get("functional_status") == "pass",
             "functional simulation did not pass")
    _require(functional.get("generic_synthesis_status") == "pass",
             "generic synthesis did not pass")

    formal = loaded["formal"]
    _require(formal.get("task_id") == "multiplier.signed16x16.v1"
             and formal.get("kind") == "combinational_equivalence"
             and formal.get("proof_status") == "equivalent",
             "formal evidence is not a passing combinational equivalence proof")
    _require(formal.get("candidate_sha256") == source_hash,
             "formal report candidate hash mismatch")

    mutation = loaded["mutation"]
    _require(mutation.get("task_id") == "multiplier.signed16x16.v1"
             and mutation.get("kind") == "mutation_sensitivity",
             "mutation report type mismatch")
    _require(mutation.get("candidate_sha256") == source_hash,
             "mutation report candidate hash mismatch")
    rows = mutation.get("mutations")
    _require(isinstance(rows, list) and bool(rows), "mutation report has no mutation rows")
    _require(mutation.get("detected_count") == len(rows)
             and mutation.get("mutation_score") == 1.0,
             "mutation summary does not match its detected rows")
    _require(all(isinstance(row, dict) and row.get("outcome") == "detected" for row in rows),
             "one or more declared mutations survived")

    ppa = loaded["ppa"]
    _require(ppa.get("report_type") == "chiprd_liberty_mapped_area_comparison",
             "PPA report type mismatch")
    _require(ppa.get("candidate", {}).get("sha256") == source_hash,
             "PPA report candidate hash mismatch")
    mapping = ppa.get("candidate", {}).get("mapping", {})
    _require(mapping.get("status") == "pass"
             and mapping.get("source_sha256") == source_hash,
             "Liberty mapping does not bind to candidate RTL")
    area = mapping.get("mapped_cell_area")
    cell_count = mapping.get("mapped_cell_count")
    _require(isinstance(area, (int, float)) and not isinstance(area, bool)
             and math.isfinite(area) and area > 0,
             "mapped cell area is missing or invalid")
    _require(isinstance(cell_count, int) and not isinstance(cell_count, bool) and cell_count > 0,
             "mapped standard-cell count is missing or invalid")
    library = ppa.get("library", {})
    library_hash = library.get("sha256")
    _require(isinstance(library_hash, str) and len(library_hash) == 64,
             "PPA Liberty hash is missing")
    ppa_netlist_hash = mapping.get("mapped_netlist_sha256")
    _require(isinstance(ppa_netlist_hash, str) and len(ppa_netlist_hash) == 64,
             "PPA mapped netlist hash is missing")
    ppa_netlist = _safe_file(
        Path(reports["ppa"]).parent / "candidate" / "mapped.v", root, "PPA mapped netlist"
    )
    _require(_sha(ppa_netlist) == ppa_netlist_hash, "PPA mapped netlist hash mismatch")
    records["ppa_mapped_netlist"] = {
        "path": _relative(ppa_netlist, root), "sha256": ppa_netlist_hash
    }

    timing = loaded["timing"]
    _require(timing.get("report_type") == "chiprd_combinational_pre_layout_timing"
             and timing.get("timing_mode") == "input_to_output_max_delay_no_clock_no_parasitics",
             "timing report type or timing mode mismatch")
    _require(timing.get("top_module") == "int_mul16", "timing top module mismatch")
    _require(timing.get("inputs", {}).get("candidate", {}).get("sha256") == source_hash,
             "timing report candidate hash mismatch")
    _require(timing.get("inputs", {}).get("liberty", {}).get("sha256") == library_hash,
             "PPA and timing reports use different Liberty libraries")
    _require(timing.get("platform_label") == ppa.get("platform_label"),
             "PPA and timing platform labels differ")
    timing_candidate = timing.get("candidate", {})
    timing_mapping = timing_candidate.get("mapping", {})
    timing_data = timing_candidate.get("timing", {})
    _require(timing_candidate.get("status") == "pass"
             and timing_mapping.get("status") == "pass",
             "combinational Liberty mapping did not pass")
    _require(timing_mapping.get("source_sha256") == source_hash,
             "combinational mapping source hash mismatch")
    _require(timing_mapping.get("mapped_netlist_sha256") == ppa_netlist_hash,
             "PPA and timing mapped netlists differ; refuse to combine unlike implementations")
    _require(timing_mapping.get("mapped_cell_area") == area
             and timing_mapping.get("mapped_cell_count") == cell_count,
             "PPA and timing mapped-cell statistics differ")
    _require(timing_data.get("critical_path_reported") is True
             and timing_data.get("meets_max_delay") is True,
             "combinational max-delay constraint did not pass")
    delay = timing_data.get("estimated_worst_path_delay_ns")
    slack = timing_data.get("worst_max_delay_slack_ns")
    _require(isinstance(delay, (int, float)) and math.isfinite(delay) and delay >= 0,
             "combinational path delay is missing or invalid")
    _require(isinstance(slack, (int, float)) and math.isfinite(slack) and slack >= 0,
             "combinational max-delay slack is missing or negative")
    timing_netlist = _safe_file(
        Path(reports["timing"]).parent / "candidate" / "mapped.v",
        root, "timing mapped netlist"
    )
    _require(_sha(timing_netlist) == timing_mapping.get("mapped_netlist_sha256"),
             "timing mapped netlist hash mismatch")
    records["timing_mapped_netlist"] = {
        "path": _relative(timing_netlist, root), "sha256": _sha(timing_netlist)
    }

    return {
        "candidate": {"path": _relative(source, root), "sha256": source_hash},
        "evidence": records,
        "summary": {
            "functional_vectors": functional.get("tested_vectors"),
            "mutation_count": len(rows),
            "mutation_detected_count": mutation.get("detected_count"),
            "mutation_score": mutation.get("mutation_score"),
            "formal_proof": formal.get("proof_status"),
            "platform_label": ppa.get("platform_label"),
            "liberty_sha256": library_hash,
            "mapped_cell_area": area,
            "mapped_cell_count": cell_count,
            "estimated_worst_path_delay_ns": delay,
            "worst_max_delay_slack_ns": slack,
            "critical_startpoint": timing_data.get("critical_startpoint"),
            "critical_endpoint": timing_data.get("critical_endpoint"),
            "max_delay_budget_ns": timing.get("constraints", {}).get("max_delay_ns"),
            "input_transition_ns": timing.get("constraints", {}).get("input_transition_ns"),
            "output_load_pf": timing.get("constraints", {}).get("output_load_pf"),
        },
    }


def build_manifest(candidate: Path, functional_report: Path, formal_report: Path,
                   mutation_report: Path, ppa_report: Path, timing_report: Path,
                   output: Path) -> dict:
    root = ROOT.resolve()
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Manifest output must not be a symlink")
    output = output.resolve()
    _require(output != root, "Manifest output must be a dedicated directory")
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Manifest output must remain inside repository root") from exc
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing multiplier evidence manifest")
    reports = {
        "functional": Path(functional_report), "formal": Path(formal_report),
        "mutation": Path(mutation_report), "ppa": Path(ppa_report),
        "timing": Path(timing_report),
    }
    inspected = _inspect(candidate, reports, root)
    output.mkdir(parents=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "chiprd_multiplier_evidence_manifest",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "integrity_model": "sha256_file_binding_not_a_digital_signature",
        "task_id": "multiplier.signed16x16.v1",
        **inspected,
        "limitations": [
            "The manifest binds public CI artifacts to one exact RTL source digest.",
            "SHA-256 detects accidental or partial tampering; it is not a signature or trusted timestamp.",
            "Mutation sensitivity covers only the declared public mutations.",
            "Liberty area and OpenSTA delay are pre-layout estimates, not routed PPA or silicon signoff.",
            "This experimental multiplier task is not directly comparable to the sequential CORDIC task.",
        ],
    }
    manifest_path = output / "multiplier_evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    verification = verify_manifest(manifest_path)
    (output / "verification_report.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if verification.get("status") != "pass":
        raise RuntimeError("Newly generated multiplier evidence manifest failed self-verification")
    return manifest


def verify_manifest(manifest_path: Path) -> dict:
    root = ROOT.resolve()
    path = _safe_file(Path(manifest_path), root, "manifest")
    manifest = _load_json(path, "manifest")
    if (manifest.get("report_type") != "chiprd_multiplier_evidence_manifest"
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("status") != "pass"
            or manifest.get("task_id") != "multiplier.signed16x16.v1"):
        raise ValueError("Unsupported or non-passing multiplier evidence manifest")
    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("Manifest evidence index is missing")
    report_paths = {}
    for key in EVIDENCE_KEYS:
        record = evidence.get(key)
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError(f"Manifest {key} evidence path is missing")
        artifact = _safe_file(root / record["path"], root, f"{key} artifact")
        if _sha(artifact) != record.get("sha256"):
            raise ValueError(f"Manifest {key} artifact hash mismatch")
        report_paths[key] = artifact
    candidate_record = manifest.get("candidate", {})
    candidate = _safe_file(root / candidate_record.get("path", ""), root, "candidate RTL")
    if _sha(candidate) != candidate_record.get("sha256"):
        raise ValueError("Manifest candidate RTL hash mismatch")
    fresh = _inspect(candidate, report_paths, root)
    if fresh.get("candidate") != manifest.get("candidate"):
        raise ValueError("Manifest candidate binding changed")
    if fresh.get("summary") != manifest.get("summary"):
        raise ValueError("Manifest summary does not match source evidence")
    for key in ("functional", "formal", "mutation", "ppa", "timing",
                "ppa_mapped_netlist", "timing_mapped_netlist"):
        if fresh["evidence"].get(key) != evidence.get(key):
            raise ValueError(f"Manifest {key} evidence binding changed")
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "chiprd_multiplier_evidence_verification",
        "status": "pass",
        "manifest_path": _relative(path, root),
        "manifest_sha256": _sha(path),
        "candidate_sha256": candidate_record["sha256"],
        "verified_evidence_count": len(evidence),
        "verified_evidence": list(evidence),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--candidate", type=Path, required=True)
    for key in EVIDENCE_KEYS:
        build.add_argument(f"--{key}-report", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_manifest(
                args.candidate, args.functional_report, args.formal_report,
                args.mutation_report, args.ppa_report, args.timing_report, args.output,
            )
        else:
            result = verify_manifest(args.manifest)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(f"Multiplier evidence {args.command} failed: {type(exc).__name__}: {exc}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
