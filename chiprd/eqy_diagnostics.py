"""Fail-closed diagnostics for expensive CORDIC EQY proof attempts.

This is an evidence summarizer, NOT a proof engine. It never upgrades a
FAIL/ERROR/INCONCLUSIVE proof to PASS. The raw EQY worktree remains available
in the uploaded CI artifact for reproduction.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_FILES = 15000
MAX_LOG_BYTES = 1024 * 1024
MAX_LOG_EXCERPTS = 24
STATUS_MARKER = re.compile(r"\b(PASS|FAIL|UNKNOWN|INCONCLUSIVE|TIMEOUT|UNPROVEN)\b", re.I)
PARTITION_MARKER = re.compile(r"\b(partition|pdr|solver|engine|strategy)\b", re.I)


def _status(path: Path) -> dict:
    report_path = path / "equivalence-candidate" / "equivalence_report.json"
    if not report_path.is_file():
        raise ValueError(f"Missing mandatory EQY report: {report_path}")
    obj = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Invalid EQY report: {report_path}")
    status = obj.get("status")
    proof_status = obj.get("proof_status")
    if status not in ("pass", "fail", "error", "inconclusive"):
        raise ValueError(f"Unknown EQY status {status!r}: {report_path}")
    if status == "pass" and proof_status != "equivalent":
        raise ValueError(f"Inconsistent EQY PASS/proof status: {report_path}")
    return {
        "status": status,
        "proof_status": proof_status,
        "error": obj.get("error"),
        "timeout_seconds": obj.get("timeout_seconds"),
        "candidate_sha256": obj.get("candidate_sha256"),
        "reference_sha256": obj.get("reference_sha256"),
        "report_path": str(report_path),
    }


def summarize_candidate(path: Path) -> dict:
    path = Path(path)
    result = _status(path)
    worktree = path / "equivalence-candidate" / "eqy-work"
    result["diagnostic_worktree_present"] = worktree.is_dir()
    # Extract terminal EQY partition states from the authoritative logfile.
    # A started partition is NOT a proved partition.
    partition_log = worktree / "cordic_equiv" / "logfile.txt"
    partition_list = worktree / "cordic_equiv" / "partition.list"
    partitions = {}
    if partition_list.is_file():
        for line in partition_list.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2:
                partitions[fields[1]] = "not_started"
    if partition_log.is_file():
        for line in partition_log.read_text(encoding="utf-8", errors="replace").splitlines():
            started = re.search(r"Running strategy .* on '([^']+)'", line)
            passed = re.search(r"Proved equivalence of partition '([^']+)'", line)
            timed_out = re.search(r"Could not prove equivalence of partition '([^']+)'.*: timeout", line)
            not_proven = re.search(r"Could not prove equivalence of partition '([^']+)'", line)
            if started:
                partitions[started.group(1)] = "running_or_interrupted"
            if passed:
                partitions[passed.group(1)] = "pass"
            if timed_out:
                partitions[timed_out.group(1)] = "timeout"
            elif not_proven:
                partitions[not_proven.group(1)] = "not_proven"
    result["partitions"] = dict(sorted(partitions.items()))
    result["log_excerpts"] = []
    result["worktree_file_count"] = 0
    result["truncated"] = False
    if not worktree.is_dir():
        return result

    relevant = []
    for index, p in enumerate(sorted(worktree.rglob("*"))):
        if index >= MAX_FILES:
            result["truncated"] = True
            break
        if p.is_symlink() or not p.is_file():
            continue
        result["worktree_file_count"] += 1
        if p.name in {"logfile.txt", "logfile", "logfile.log", "log.txt"} or p.suffix == ".log":
            relevant.append(p)
    result["log_file_count"] = len(relevant)
    for p in relevant:
        if len(result["log_excerpts"]) >= MAX_LOG_EXCERPTS:
            result["truncated"] = True
            break
        try:
            # Read the last 1 MB of bounded diagnostic material.
            with p.open("rb") as handle:
                if p.stat().st_size > MAX_LOG_BYTES:
                    handle.seek(-MAX_LOG_BYTES, 2)
                content = handle.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
            matches = [line.strip()[:360] for line in content.splitlines()
                       if STATUS_MARKER.search(line) and PARTITION_MARKER.search(line)]
            if not matches:
                matches = [line.strip()[:360] for line in content.splitlines()
                           if STATUS_MARKER.search(line)]
            if matches:
                result["log_excerpts"].append({
                    "path": p.relative_to(worktree).as_posix(),
                    "last_status_lines": matches[-6:],
                })
        except OSError as exc:
            result["log_excerpts"].append({
                "path": p.relative_to(worktree).as_posix(),
                "read_error": str(exc),
            })
    return result


def summarize_candidates(iter2: Path, iter4: Path, output: Path) -> dict:
    rows = {"iter2": summarize_candidate(iter2), "iter4": summarize_candidate(iter4)}
    result = {
        "report_type": "chiprd_cordic_architecture_eqy_diagnostics",
        "schema_version": "1.0.0",
        "status": "pass" if all(row["status"] == "pass" and
                                row["proof_status"] == "equivalent" for row in rows.values())
                  else "fail",
        "candidates": rows,
        "limitations": [
            "Diagnostic parsing is not itself a formal equivalence proof.",
            "Only explicit terminal EQY PASS with equivalent proof status qualifies as success.",
        ],
    }
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Diagnostic report path must not be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iter2", type=Path, required=True)
    parser.add_argument("--iter4", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize_candidates(args.iter2, args.iter4, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
