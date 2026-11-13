"""SymbiYosys runner with fail-closed proof-result semantics."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT, run_command
from .mutation import MUTATIONS, build_mutant

FORMAL_TOP = ROOT / "formal" / "cordic_protocol_formal.sv"
FORMAL_DEPTH = 40
LIMITATIONS = [
    "Proves protocol, one-cycle valid pulse, completion within 32 cycles, and output-register stability only.",
    "Does not formally prove sine/cosine numerical accuracy; that remains covered by the independent simulation oracle.",
    "Does not prove physical timing, area, power, CDC, reset recovery/removal, or silicon correctness.",
    "Assumes synchronous reset on the first formal clock and deasserted reset thereafter.",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_version(executable: str, args: list[str]) -> str:
    path = shutil.which(executable)
    if not path:
        raise RuntimeError(f"Required formal tool unavailable: {executable}")
    proc = run_command([path, *args], ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"Cannot query {executable} version: {proc.stderr}")
    return (proc.stdout.strip() or proc.stderr.strip()).splitlines()[0]


def make_sby_config(mode: str = "prove", depth: int = FORMAL_DEPTH) -> str:
    if mode not in {"prove", "bmc", "cover"}:
        raise ValueError(f"Unsupported SBY mode: {mode}")
    return (
        "[options]\n" + f"mode {mode}\n" + f"depth {depth}\n"
        + "\n[engines]\nsmtbmc z3\n"
        + "\n[script]\nread -formal -sv candidate.sv cordic_protocol_formal.sv\nprep -top cordic_formal\n"
        + "\n[files]\ncandidate.sv\ncordic_protocol_formal.sv\n"
    )


def classify_sby(returncode: int, log_text: str, mode: str = "prove") -> tuple[str, str]:
    if returncode == 0 and "DONE (PASS, rc=0)" in log_text:
        return "pass", {"prove": "proven", "bmc": "bounded_pass", "cover": "cover_reached"}.get(mode, "pass")
    if "DONE (FAIL, rc=2)" in log_text or "Status: failed" in log_text:
        return "fail", "failed"
    if "UNKNOWN" in log_text or "Status: unknown" in log_text:
        return "inconclusive", "inconclusive"
    return "error", "error"


def run_formal(candidate: Path, output: Path, mode: str = "prove",
               depth: int = FORMAL_DEPTH) -> dict:
    candidate = candidate.resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("Candidate must be a regular file")
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "0.1.0", "task_id": "cordic.rotation.q14.v1",
        "kind": "formal_protocol", "mode": mode, "depth": depth,
        "candidate_sha256": sha256_file(candidate),
        "formal_harness_sha256": sha256_file(FORMAL_TOP),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error", "proof_status": "error",
        "properties": [
            "completion_valid_is_one_cycle", "no_spurious_completion",
            "accepted_request_completes_within_32_cycles",
            "no_completion_timeout", "output_registers_stable_without_valid",
            "back_to_back_request_acceptance",
        ],
        "limitations": LIMITATIONS,
    }
    try:
        if mode not in {"prove", "bmc", "cover"}:
            raise ValueError(f"Unsupported SBY mode: {mode}")
        sby = shutil.which("sby")
        if not sby:
            raise RuntimeError("SymbiYosys (sby) unavailable; formal proof NOT run")
        report["tool_versions"] = {
            "yosys": tool_version("yosys", ["-V"]),
            "z3": tool_version("z3", ["--version"]),
            "sby": tool_version("sby", ["--version"]),
        }
        with tempfile.TemporaryDirectory(prefix="chiprd-formal-") as temp:
            work = Path(temp)
            shutil.copyfile(candidate, work / "candidate.sv")
            shutil.copyfile(FORMAL_TOP, work / "cordic_protocol_formal.sv")
            config = work / "cordic_protocol.sby"
            config.write_text(make_sby_config(mode, depth), encoding="utf-8")
            proc = subprocess.run(
                [sby, "-f", "-d", "sby-out", config.name], cwd=work,
                text=True, capture_output=True, timeout=300, check=False,
            )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            (output / "sby_stdout.log").write_text(stdout, encoding="utf-8")
            (output / "sby_stderr.log").write_text(stderr, encoding="utf-8")
            sby_dir = work / "sby-out"
            logfile = sby_dir / "logfile.txt"
            log_text = logfile.read_text(encoding="utf-8", errors="replace") if logfile.exists() else ""
            (output / "sby_logfile.txt").write_text(log_text, encoding="utf-8")
            # Preserve counterexample/induction traces for review; the temporary
            # work directory is removed after this function returns.
            if sby_dir.exists():
                shutil.copytree(sby_dir, output / "sby-out", dirs_exist_ok=True)
            combined = "\n".join((stdout, stderr, log_text))
            status, proof_status = classify_sby(proc.returncode, combined, mode)
            report.update(
                status=status, proof_status=proof_status, returncode=proc.returncode,
                sby_summary=next((line.strip() for line in reversed(combined.splitlines())
                                  if "DONE (" in line), None),
            )
            if status == "error":
                report["error"] = "Unable to establish an SBY PASS/FAIL result; inspect logs"
    except subprocess.TimeoutExpired:
        report.update(status="inconclusive", proof_status="inconclusive",
                      error="SymbiYosys timed out after 300 seconds")
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        report.update(status="error", proof_status="error", error=str(exc))
    (output / "formal_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def run_formal_negative_control(candidate: Path, output: Path,
                                depth: int = FORMAL_DEPTH) -> dict:
    candidate = candidate.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    baseline = run_formal(candidate, output / "baseline", mode="prove", depth=depth)
    report = {
        "schema_version": "0.1.0", "task_id": "cordic.rotation.q14.v1",
        "kind": "formal_negative_control", "candidate_sha256": sha256_file(candidate),
        "baseline": baseline, "negative_control": {}, "status": "error",
    }
    if baseline.get("proof_status") != "proven":
        report.update(status=baseline.get("status", "error"),
                      reason="golden_candidate_not_proven; negative control not meaningful")
    else:
        source = candidate.read_text(encoding="utf-8")
        mutation = next(item for item in MUTATIONS if item["id"] == "suppress_valid")
        mutant_source = build_mutant(source, mutation)
        mutant_dir = output / "mutant"
        mutant_dir.mkdir(parents=True, exist_ok=True)
        mutant_path = mutant_dir / "candidate.sv"
        mutant_path.write_text(mutant_source, encoding="utf-8")
        mutant = run_formal(mutant_path, mutant_dir, mode="prove", depth=depth)
        report["negative_control"] = {
            "mutation_id": mutation["id"], "candidate_sha256": sha256_file(mutant_path),
            "formal_result": mutant, "expected": "failed",
        }
        if mutant.get("proof_status") == "failed":
            report["status"] = "pass"
            report["negative_control"]["outcome"] = "detected"
        elif mutant.get("proof_status") == "proven":
            report["status"] = "fail"
            report["negative_control"]["outcome"] = "survived"
        else:
            report["status"] = "error"
            report["negative_control"]["outcome"] = "inconclusive_or_error"
    (output / "formal_negative_control_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
