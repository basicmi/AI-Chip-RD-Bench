"""Sealed candidate experiments, provenance manifests and reproducibility checks."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .equivalence import (
    DEFAULT_REFACTOR,
    DEFAULT_REFERENCE,
    EQY_TIMEOUT,
    run_equivalence,
    run_equivalence_selftest,
)
from .formal import FORMAL_DEPTH, run_formal, run_formal_negative_control
from .harness import ROOT, run
from .mutation import run_mutations
from .oracle import DEFAULT_TOLERANCE

MANIFEST_NAME = "experiment_manifest.json"
REPORT_NAME = "experiment_report.json"
LEDGER_NAME = "experiment_ledger.jsonl"
REPRODUCTION_REPORT_NAME = "reproducibility_report.json"
MANIFEST_SCHEMA_VERSION = "1.0.0"

EVALUATOR_INPUTS = (
    "chiprd/cli.py",
    "chiprd/evidence.py",
    "chiprd/harness.py",
    "chiprd/mutation.py",
    "chiprd/formal.py",
    "chiprd/equivalence.py",
    "chiprd/oracle.py",
    "tasks/cordic/tb_cordic.sv",
    "tasks/cordic/spec.md",
    "tasks/cordic/cordic_rotator.sv",
    "tasks/cordic/cordic_rotator_refactor.sv",
    "formal/cordic_protocol_formal.sv",
    "pyproject.toml",
)

TOOL_COMMANDS = {
    "verilator": ("verilator", ["--version"]),
    "yosys": ("yosys", ["-V"]),
    "eqy": ("eqy", ["--version"]),
    "sby": ("sby", ["--version"]),
    "z3": ("z3", ["--version"]),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_evaluator_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for relative in EVALUATOR_INPUTS:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required evaluator input is missing: {relative}")
        files[relative] = sha256_file(path)
    return files


def collect_toolchain() -> dict:
    versions: dict[str, str] = {}
    missing: list[str] = []
    for name, (executable, args) in TOOL_COMMANDS.items():
        resolved = shutil.which(executable)
        if not resolved:
            missing.append(executable)
            continue
        try:
            proc = subprocess.run(
                [resolved, *args], cwd=ROOT, text=True, capture_output=True,
                timeout=20, check=False,
            )
            if proc.returncode != 0:
                missing.append(f"{executable}:version-query-failed")
                continue
            output = (proc.stdout.strip() or proc.stderr.strip()).splitlines()
            if not output:
                missing.append(f"{executable}:empty-version")
                continue
            versions[name] = output[0]
        except (OSError, subprocess.SubprocessError):
            missing.append(f"{executable}:version-query-failed")
    return {
        "status": "pass" if not missing else "error",
        "versions": versions,
        "missing": missing,
        "python": sys.version.splitlines()[0],
        "platform": platform.platform(),
    }


def _append_ledger(path: Path, event: str, **fields: object) -> None:
    record = {"timestamp_utc": _utc_now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _safe_relative_file(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError("Manifest path escapes its experiment directory") from exc
    return candidate


def _signature_for(name: str, result: dict) -> dict:
    if name == "functional_synthesis":
        functional = result.get("results", {}).get("functional", {})
        synthesis = result.get("results", {}).get("synthesis", {})
        return {
            "status": result.get("status"),
            "functional": {
                "status": functional.get("status"),
                "tested": functional.get("tested"),
                "received": functional.get("received"),
                "failures": functional.get("failures"),
            },
            "synthesis": {
                "status": synthesis.get("status"),
                "generic_cell_count": synthesis.get("generic_cell_count"),
                "cell_type_histogram": synthesis.get("cell_type_histogram"),
            },
        }
    if name == "mutation":
        return {
            "status": result.get("status"),
            "mutation_score": result.get("mutation_score"),
            "summary": result.get("summary"),
            "outcomes": [
                {"id": item.get("id"), "outcome": item.get("outcome")}
                for item in result.get("mutations", [])
            ],
        }
    if name == "formal":
        return {
            "status": result.get("status"),
            "proof_status": result.get("proof_status"),
            "mode": result.get("mode"),
            "depth": result.get("depth"),
            "properties": result.get("properties"),
        }
    if name == "formal_negative_control":
        negative = result.get("negative_control", {})
        formal = negative.get("formal_result", {})
        return {
            "status": result.get("status"),
            "baseline_proof_status": result.get("baseline", {}).get("proof_status"),
            "negative_outcome": negative.get("outcome"),
            "negative_proof_status": formal.get("proof_status"),
        }
    if name == "equivalence":
        return {
            "status": result.get("status"),
            "proof_status": result.get("proof_status"),
            "equivalence_semantics": result.get("equivalence_semantics"),
        }
    if name == "equivalence_selftest":
        negative = result.get("negative_control", {})
        return {
            "status": result.get("status"),
            "positive_proof_status": result.get("positive", {}).get("proof_status"),
            "negative_outcome": negative.get("outcome"),
            "negative_proof_status": negative.get("formal_result", {}).get("proof_status"),
        }
    return {"status": result.get("status")}


def result_signature(check_results: dict[str, dict]) -> dict:
    return {
        name: _signature_for(name, result)
        for name, result in sorted(check_results.items())
    }


def _aggregate_status(check_results: dict[str, dict]) -> str:
    statuses = [item.get("status", "error") for item in check_results.values()]
    if statuses and all(status == "pass" for status in statuses):
        return "pass"
    if "fail" in statuses:
        return "fail"
    return "error"


def run_experiment(candidate: Path, output: Path, seed: int = 20261112) -> dict:
    """Run every public gate against one sealed RTL snapshot and emit an evidence ledger."""
    candidate = candidate.resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("Candidate must be a regular file")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / MANIFEST_NAME
    if manifest_path.exists():
        raise FileExistsError(f"Experiment already exists: {manifest_path}")

    source_bytes = candidate.read_bytes()
    candidate_hash = sha256_bytes(source_bytes)
    sealed_dir = output / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    snapshot = sealed_dir / "candidate.sv"
    if snapshot.exists():
        raise FileExistsError(f"Refusing to overwrite sealed candidate: {snapshot}")
    snapshot.write_bytes(source_bytes)
    if sha256_file(snapshot) != candidate_hash:
        raise RuntimeError("Candidate changed while creating the sealed snapshot")
    snapshot.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    ledger_path = output / LEDGER_NAME
    evaluator_files = _collect_evaluator_files()
    toolchain = collect_toolchain()
    created_at = _utc_now()
    experiment_id = created_at.replace("-", "").replace(":", "").replace(".", "") + "-" + candidate_hash[:12]
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_type": "chiprd_experiment",
        "experiment_id": experiment_id,
        "task_id": "cordic.rotation.q14.v1",
        "created_at_utc": created_at,
        "candidate": {
            "source_name": candidate.name,
            "snapshot_path": "sealed/candidate.sv",
            "sha256": candidate_hash,
            "size_bytes": len(source_bytes),
        },
        "evaluator": {
            "files": evaluator_files,
            "fingerprint_sha256": _canonical_hash(evaluator_files),
        },
        "toolchain": toolchain,
        "parameters": {
            "seed": seed,
            "functional_tolerance_lsb": DEFAULT_TOLERANCE,
            "formal_mode": "prove",
            "formal_depth": FORMAL_DEPTH,
            "equivalence_semantics": "cycle_accurate_sequential",
            "eqy_timeout_seconds": EQY_TIMEOUT,
        },
        "source_control": {},
        "commands": [
            ["python", "-m", "chiprd.cli", "all", "--candidate", "sealed/candidate.sv"],
            ["python", "-m", "chiprd.cli", "mutation", "--candidate", "sealed/candidate.sv"],
            ["python", "-m", "chiprd.cli", "formal", "--candidate", "sealed/candidate.sv"],
            ["python", "-m", "chiprd.cli", "formal-negative-control", "--candidate", "sealed/candidate.sv"],
            ["python", "-m", "chiprd.cli", "equivalence", "--reference", "tasks/cordic/cordic_rotator.sv", "--candidate", "sealed/candidate.sv"],
            ["python", "-m", "chiprd.cli", "equivalence-selftest"],
        ],
    }
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            capture_output=True, timeout=10, check=False,
        )
        if proc.returncode == 0:
            manifest["source_control"]["git_commit"] = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _append_ledger(
        ledger_path, "experiment_started", experiment_id=experiment_id,
        candidate_sha256=candidate_hash, manifest_sha256=sha256_file(manifest_path),
    )
    _append_ledger(
        ledger_path, "candidate_sealed", snapshot_path="sealed/candidate.sv",
        candidate_sha256=candidate_hash, size_bytes=len(source_bytes),
    )

    check_results: dict[str, dict] = {}
    check_paths = {
        "functional_synthesis": "functional-synthesis/report.json",
        "mutation": "mutation/mutation_report.json",
        "formal": "formal/formal_report.json",
        "formal_negative_control": "formal-negative-control/formal_negative_control_report.json",
        "equivalence": "equivalence-candidate/equivalence_report.json",
        "equivalence_selftest": "equivalence-selftest/equivalence_selftest_report.json",
    }

    def execute(name: str, function: Callable[[], dict]) -> None:
        _append_ledger(
            ledger_path, "check_started", experiment_id=experiment_id,
            check=name, candidate_sha256=candidate_hash,
        )
        try:
            result = function()
            if not isinstance(result, dict):
                result = {"status": "error", "error": "Check returned a non-object result"}
        except Exception as exc:
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        check_results[name] = result
        relative_report = check_paths[name]
        report_file = output / relative_report
        _append_ledger(
            ledger_path, "check_completed", experiment_id=experiment_id,
            check=name, status=result.get("status", "error"),
            report_path=relative_report,
            report_sha256=sha256_file(report_file) if report_file.is_file() else None,
        )

    if toolchain["status"] != "pass":
        check_results["preflight"] = {
            "status": "error",
            "error": "Required toolchain incomplete; no experiment checks were run",
            "missing_tools": toolchain["missing"],
        }
    else:
        execute(
            "functional_synthesis",
            lambda: run(snapshot, output / "functional-synthesis", mode="all", seed=seed),
        )
        execute("mutation", lambda: run_mutations(snapshot, output / "mutation", seed=seed))
        execute("formal", lambda: run_formal(snapshot, output / "formal"))
        execute(
            "formal_negative_control",
            lambda: run_formal_negative_control(snapshot, output / "formal-negative-control"),
        )
        execute(
            "equivalence",
            lambda: run_equivalence(DEFAULT_REFERENCE, snapshot, output / "equivalence-candidate"),
        )
        execute(
            "equivalence_selftest",
            lambda: run_equivalence_selftest(
                DEFAULT_REFERENCE, DEFAULT_REFACTOR, output / "equivalence-selftest"
            ),
        )

    signatures = result_signature(check_results)
    status = _aggregate_status(check_results)
    report = {
        "report_schema_version": "1.0.0",
        "experiment_id": experiment_id,
        "task_id": manifest["task_id"],
        "candidate_sha256": candidate_hash,
        "status": status,
        "checks": {
            name: {
                "status": result.get("status", "error"),
                "report_path": check_paths.get(name),
            }
            for name, result in sorted(check_results.items())
        },
        "result_signature": signatures,
        "manifest_path": MANIFEST_NAME,
        "ledger_path": LEDGER_NAME,
    }
    report_path = output / REPORT_NAME
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _append_ledger(
        ledger_path, "experiment_completed", experiment_id=experiment_id,
        status=status, report_sha256=sha256_file(report_path),
        result_signature_sha256=_canonical_hash(signatures),
    )
    return {
        **report,
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "ledger_path": str(ledger_path),
    }


def reproduce_experiment(manifest_path: Path, output: Path) -> dict:
    """Validate provenance, rerun a sealed candidate and compare normalized results."""
    manifest_path = manifest_path.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reproduction_path = output / REPRODUCTION_REPORT_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_type") != "chiprd_experiment" or manifest.get(
        "manifest_schema_version"
    ) != MANIFEST_SCHEMA_VERSION:
        report = {
            "status": "blocked",
            "reason": "unsupported_or_invalid_manifest",
            "manifest_path": str(manifest_path),
        }
        reproduction_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report

    experiment_dir = manifest_path.parent
    candidate = _safe_relative_file(
        experiment_dir, manifest.get("candidate", {}).get("snapshot_path", "")
    )
    candidate_exists = candidate.is_file()
    candidate_hash = sha256_file(candidate) if candidate_exists else None
    expected_candidate_hash = manifest.get("candidate", {}).get("sha256")
    candidate_matches = candidate_exists and candidate_hash == expected_candidate_hash

    try:
        current_inputs = _collect_evaluator_files()
    except (OSError, FileNotFoundError) as exc:
        current_inputs = {}
        input_error = str(exc)
    else:
        input_error = None
    expected_inputs = manifest.get("evaluator", {}).get("files", {})
    evaluator_inputs_match = current_inputs == expected_inputs
    current_toolchain = collect_toolchain()
    expected_versions = manifest.get("toolchain", {}).get("versions", {})
    tool_versions_match = (
        current_toolchain.get("status") == "pass"
        and current_toolchain.get("versions") == expected_versions
        and current_toolchain.get("python") == manifest.get("toolchain", {}).get("python")
    )

    mismatches = []
    if not candidate_matches:
        mismatches.append("sealed_candidate_missing_or_hash_mismatch")
    if not evaluator_inputs_match:
        mismatches.append("evaluator_input_hash_mismatch")
    if input_error:
        mismatches.append(f"evaluator_input_error:{input_error}")
    if not tool_versions_match:
        mismatches.append("toolchain_version_mismatch_or_missing")

    if mismatches:
        report = {
            "status": "blocked",
            "original_experiment_id": manifest.get("experiment_id"),
            "candidate_matches": candidate_matches,
            "evaluator_inputs_match": evaluator_inputs_match,
            "tool_versions_match": tool_versions_match,
            "mismatches": mismatches,
            "original_manifest_sha256": sha256_file(manifest_path),
        }
        reproduction_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report

    original_report_path = experiment_dir / REPORT_NAME
    if not original_report_path.is_file():
        report = {
            "status": "blocked",
            "reason": "original_experiment_report_missing",
            "original_experiment_id": manifest.get("experiment_id"),
        }
        reproduction_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report
    original_report = json.loads(original_report_path.read_text(encoding="utf-8"))
    seed = int(manifest.get("parameters", {}).get("seed", 20261112))
    reproduced = run_experiment(candidate, output, seed=seed)
    original_signature = original_report.get("result_signature", {})
    reproduced_signature = reproduced.get("result_signature", {})
    signatures_match = original_signature == reproduced_signature
    report = {
        "reproducibility_schema_version": "1.0.0",
        "status": "pass" if signatures_match else "fail",
        "original_experiment_id": manifest.get("experiment_id"),
        "reproduced_experiment_id": reproduced.get("experiment_id"),
        "candidate_matches": candidate_matches,
        "evaluator_inputs_match": evaluator_inputs_match,
        "tool_versions_match": tool_versions_match,
        "result_signatures_match": signatures_match,
        "original_experiment_status": original_report.get("status"),
        "reproduced_experiment_status": reproduced.get("status"),
        "original_result_signature": original_signature,
        "reproduced_result_signature": reproduced_signature,
        "original_manifest_sha256": sha256_file(manifest_path),
    }
    reproduction_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
