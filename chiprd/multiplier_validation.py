"""Combinational formal equivalence and mutation sensitivity for signed mul16.

These checks are intentionally separate from the sequential CORDIC protocol
formal harness. Yosys proves the arithmetic top-level combinational miter.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT

TASK_ID = "multiplier.signed16x16.v1"
TOP = "int_mul16"
ANCHOR = "always_comb product = a * b;"
MUTATIONS = (
    {"id": "replace_multiply_with_add", "replacement": "always_comb product = a + b;"},
    {"id": "shift_product_right", "replacement": "always_comb product = (a * b) >>> 1;"},
    {"id": "unsigned_operands", "replacement": "always_comb product = $unsigned(a) * $unsigned(b);"},
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("RTL input must not be a symlink")
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("RTL input must be a regular file <=1 MiB")
    return path


def _equivalence_script() -> str:
    # always_comb is lowered to RTLIL processes by read_verilog.  Each design
    # must be run through proc before equiv_make can compare its cells.
    return (
        "read_verilog -sv gold.sv\n"
        "proc\n"
        "alumacc\n"
        "opt\n"
        "rename int_mul16 gold\n"
        "read_verilog -sv gate.sv\n"
        "proc\n"
        "alumacc\n"
        "opt\n"
        "rename int_mul16 gate\n"
        "equiv_make gold gate equiv\n"
        "prep -top equiv\n"
        "equiv_simple\n"
        "equiv_status -assert\n"
    )


def prove_equivalence(reference: Path, candidate: Path, output: Path) -> dict:
    reference, candidate = _regular(reference), _regular(candidate)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite multiplier formal output")
    output.mkdir(parents=True)
    report = {
        "schema_version": "1.0.0", "task_id": TASK_ID,
        "kind": "combinational_equivalence", "semantics": "combinational_all_inputs",
        "reference_sha256": _sha(reference), "candidate_sha256": _sha(candidate),
        "status": "error", "proof_status": "error",
        "limitations": [
            "Yosys combinational equivalence of the declared 16-bit input/32-bit output function.",
            "Does not prove timing, area, power, CDC, or physical implementation.",
        ],
    }
    yosys = shutil.which("yosys")
    if not yosys:
        report["error"] = "Yosys unavailable; proof not run"
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="chiprd-mul-equiv-") as tmp:
            work = Path(tmp)
            shutil.copyfile(reference, work / "gold.sv")
            shutil.copyfile(candidate, work / "gate.sv")
            script = _equivalence_script()
            (work / "equiv.ys").write_text(script, encoding="utf-8")
            try:
                proc = subprocess.run([yosys, "-Q", "-T", "-s", "equiv.ys"],
                                      cwd=work, text=True, capture_output=True,
                                      timeout=180, check=False)
                log = proc.stdout + "\n[stderr]\n" + proc.stderr
                (output / "yosys_equivalence.log").write_text(log, encoding="utf-8")
                proven = "Equivalence successfully proven!" in log
                failed = "Unproven" in log or "Equivalence FAILED" in log
                if proc.returncode == 0 and proven:
                    report.update(status="pass", proof_status="equivalent")
                elif failed:
                    report.update(status="fail", proof_status="not_equivalent")
                else:
                    report.update(status="error", proof_status="error",
                                  error="Yosys did not emit an unambiguous equivalence result")
                report["returncode"] = proc.returncode
            except subprocess.TimeoutExpired:
                report.update(status="inconclusive", proof_status="inconclusive",
                              error="Yosys equivalence timed out after 180 seconds")
    (output / "equivalence_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def run_multiplier_mutations(candidate: Path, output: Path, *, seed: int = 20261116,
                             random_count: int = 512) -> dict:
    from .multiplier_harness import evaluate
    candidate = _regular(candidate)
    source = candidate.read_text(encoding="utf-8")
    if source.count(ANCHOR) != 1:
        raise ValueError("Multiplier mutation anchor missing or ambiguous")
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite multiplier mutation output")
    output.mkdir(parents=True)
    rows = []
    for mutation in MUTATIONS:
        mutant_dir = output / mutation["id"]
        mutant_dir.mkdir()
        mutant = mutant_dir / "candidate.sv"
        mutant.write_text(source.replace(ANCHOR, mutation["replacement"], 1),
                          encoding="utf-8")
        result = evaluate(mutant, mutant_dir / "evaluation", seed=seed,
                          random_count=random_count)
        detected = (
            result.get("functional_status") == "fail"
            and result.get("generic_synthesis_status") == "pass"
        )
        rows.append({
            "mutation_id": mutation["id"],
            "mutant_sha256": _sha(mutant),
            "expected": "rejected",
            "functional_status": result.get("functional_status", "not_run"),
            "generic_synthesis_status": result.get("generic_synthesis_status", "not_run"),
            "outcome": "detected" if detected else "survived",
        })
    detected_count = sum(row["outcome"] == "detected" for row in rows)
    report = {
        "schema_version": "1.0.0", "task_id": TASK_ID,
        "kind": "mutation_sensitivity", "candidate_sha256": _sha(candidate),
        "seed": seed, "random_vector_count_per_mutant": random_count,
        "mutation_count": len(rows), "detected_count": detected_count,
        "mutation_score": detected_count / len(rows) if rows else 0.0,
        "mutations": rows,
        "status": "pass" if detected_count == len(rows) else "fail",
        "limitations": [
            "Only the listed public source-level mutations are measured.",
            "This is evaluator sensitivity, not hidden-test coverage or proof of correctness.",
        ],
    }
    (output / "mutation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
