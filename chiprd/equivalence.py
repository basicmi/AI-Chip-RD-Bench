"""Cycle-accurate sequential equivalence checks using YosysHQ EQY."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT, run_command

DEFAULT_REFERENCE = ROOT / "tasks" / "cordic" / "cordic_rotator.sv"
DEFAULT_REFACTOR = ROOT / "tasks" / "cordic" / "cordic_rotator_refactor.sv"
MUTATION_ANCHOR = (
    "wire signed [31:0] y_delta = (z >= 0) ? x_shift : -x_shift;"
)
MUTATION_REPLACEMENT = (
    "wire signed [31:0] y_delta = (z >= 0) ? -x_shift : x_shift;"
)
EQY_TIMEOUT = 300


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_version(executable: str, args: list[str]) -> str:
    path = shutil.which(executable)
    if not path:
        raise RuntimeError(f"Required equivalence tool unavailable: {executable}")
    proc = run_command([path, *args], ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"Cannot query {executable} version: {proc.stderr}")
    return (proc.stdout.strip() or proc.stderr.strip()).splitlines()[0]


def make_eqy_config() -> str:
    """Cycle-accurate EQY setup with PDR and automatic matching of stable state names."""
    return (
        "[gold]\n"
        "read_verilog -sv gold.sv\n"
        "prep -top cordic_rotator\n"
        "memory_map\n"
        "\n[gate]\n"
        "read_verilog -sv gate.sv\n"
        "prep -top cordic_rotator\n"
        "memory_map\n"
        "\n[strategy pdr]\n"
        "use sby\n"
        "engine abc pdr\n"
        "timeout 180\n"
    )


def classify_eqy(returncode: int, log_text: str) -> tuple[str, str]:
    """Require EQY's explicit terminal marker; a zero exit alone is insufficient."""
    if returncode == 0 and "DONE (PASS, rc=0)" in log_text:
        return "pass", "equivalent"
    if "DONE (FAIL, rc=2)" in log_text or "Status: failed" in log_text:
        return "fail", "not_equivalent"
    if "UNKNOWN" in log_text or "Status: unknown" in log_text:
        return "inconclusive", "inconclusive"
    return "error", "error"


def run_equivalence(reference: Path, candidate: Path, output: Path,
                    timeout: int = EQY_TIMEOUT) -> dict:
    reference = reference.resolve(strict=True)
    candidate = candidate.resolve(strict=True)
    if not reference.is_file() or not candidate.is_file():
        raise ValueError("Reference and candidate must be regular files")
    output.mkdir(parents=True, exist_ok=True)

    report = {
        "schema_version": "0.1.0",
        "task_id": "cordic.rotation.q14.v1",
        "kind": "sequential_equivalence",
        "equivalence_semantics": "cycle_accurate_sequential",
        "reference_sha256": sha256_file(reference),
        "candidate_sha256": sha256_file(candidate),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "proof_status": "error",
        "limitations": [
            "Checks cycle-by-cycle sequential equivalence at the top-level interface.",
            "Does not establish latency-insensitive or transaction-level equivalence; pipeline retiming that changes externally visible cycle timing needs a separate contract.",
            "Does not prove numerical correctness against the analytic oracle; run the independent functional verification as well.",
            "Does not prove physical timing, area, power, CDC, reset recovery/removal, or silicon correctness.",
        ],
    }

    try:
        eqy = shutil.which("eqy")
        if not eqy:
            raise RuntimeError("EQY unavailable; equivalence proof NOT run")
        report["tool_versions"] = {
            "eqy": tool_version("eqy", ["--version"]),
            "yosys": tool_version("yosys", ["-V"]),
            "sby": tool_version("sby", ["--version"]),
        }

        # Keep the EQY work directory until its terminal status is known.  In
        # particular, never let TemporaryDirectory erase solver/partition state
        # when the proof times out.
        work = Path(tempfile.mkdtemp(prefix="chiprd-eqy-"))
        report["eqy_workdir"] = str(output / "eqy-work")
        shutil.copyfile(reference, work / "gold.sv")
        shutil.copyfile(candidate, work / "gate.sv")
        config = work / "cordic_equiv.eqy"
        config.write_text(make_eqy_config(), encoding="utf-8")

        proc = subprocess.Popen(
            [eqy, "-f", config.name],
            cwd=work,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            # EQY launches SBY/solver children. Kill the whole process group so
            # CI does not leave orphaned proof processes behind.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            partial_out, partial_err = proc.communicate()
            stdout = partial_out or (exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or "")
            stderr = partial_err or (exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or "")
            timed_out = True

        (output / "eqy_stdout.log").write_text(stdout or "", encoding="utf-8")
        (output / "eqy_stderr.log").write_text(stderr or "", encoding="utf-8")
        eqy_work = work / "cordic_equiv"
        eqy_log = eqy_work / "logfile.txt"
        log_text = (
            eqy_log.read_text(encoding="utf-8", errors="replace")
            if eqy_log.exists() else ""
        )
        (output / "eqy_logfile.txt").write_text(log_text, encoding="utf-8")
        if work.exists():
            shutil.copytree(work, output / "eqy-work", dirs_exist_ok=True)

        combined = "\n".join((stdout or "", stderr or "", log_text))
        if timed_out:
            status, proof_status = "inconclusive", "inconclusive"
            report.update(
                status=status,
                proof_status=proof_status,
                error=f"EQY timed out after {timeout} seconds",
                timeout_seconds=timeout,
                eqy_summary="timeout; diagnostic work directory preserved",
            )
        else:
            status, proof_status = classify_eqy(proc.returncode, combined)
            report.update(
                status=status,
                proof_status=proof_status,
                returncode=proc.returncode,
                eqy_summary=next(
                    (line.strip() for line in reversed(combined.splitlines())
                     if "DONE (" in line),
                    None,
                ),
            )
            if status == "error":
                report["error"] = "Unable to establish an EQY PASS/FAIL result; inspect logs"
        # Successful/definitive runs need only the compact logs; retain the
        # full solver tree for inconclusive/error results.
        if status in {"pass", "fail"}:
            shutil.rmtree(work, ignore_errors=True)
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        report.update(status="error", proof_status="error", error=str(exc))

    (output / "equivalence_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def run_equivalence_selftest(reference: Path, refactor: Path, output: Path) -> dict:
    """Require a structurally refactored design to pass and a faulty variant to fail."""
    reference = reference.resolve(strict=True)
    refactor = refactor.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)

    positive = run_equivalence(reference, refactor, output / "positive")
    mutant_source = refactor.read_text(encoding="utf-8")
    if mutant_source.count(MUTATION_ANCHOR) != 1:
        report = {
            "schema_version": "0.1.0",
            "task_id": "cordic.rotation.q14.v1",
            "kind": "equivalence_negative_control",
            "status": "error",
            "reason": "mutation_anchor_missing_or_ambiguous",
            "positive": positive,
            "negative_control": {},
        }
    else:
        mutant_path = output / "cordic_rotator_equivalence_mutant.sv"
        mutant_path.write_text(
            mutant_source.replace(MUTATION_ANCHOR, MUTATION_REPLACEMENT, 1),
            encoding="utf-8",
        )
        negative = run_equivalence(reference, mutant_path, output / "negative")
        if positive.get("proof_status") == "equivalent" and negative.get("proof_status") == "not_equivalent":
            status = "pass"
            outcome = "detected"
        elif positive.get("status") in {"error", "inconclusive"} or negative.get("status") in {"error", "inconclusive"}:
            status = "error"
            outcome = "inconclusive_or_error"
        else:
            status = "fail"
            outcome = "positive_failed_or_mutant_survived"
        report = {
            "schema_version": "0.1.0",
            "task_id": "cordic.rotation.q14.v1",
            "kind": "equivalence_negative_control",
            "reference_sha256": sha256_file(reference),
            "refactor_sha256": sha256_file(refactor),
            "mutant_sha256": sha256_file(mutant_path),
            "positive": positive,
            "negative_control": {
                "mutation": "invert_y_rotation_delta",
                "expected": "not_equivalent",
                "formal_result": negative,
                "outcome": outcome,
            },
            "status": status,
        }

    (output / "equivalence_selftest_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
