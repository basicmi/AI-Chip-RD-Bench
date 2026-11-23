"""Bit-vector macrostep lemma for CORDIC unrolling.

This establishes only a combinational identity over arbitrary 32-bit states
at legal iteration boundaries. It is NOT sequential/top-level equivalence.
Both source signatures are checked before proving to prevent an unrelated
source change from silently reusing an old proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .harness import ROOT

ATAN = (6434, 3798, 2007, 1019, 511, 256, 128, 64, 32, 16, 8, 4, 2, 1, 0, 0)


def _signed32(value: int) -> str:
    return f"(_ bv{value % (1 << 32)} 32)"


def _select(z: str, positive: str, negative: str) -> str:
    return f"(ite (bvsge {z} (_ bv0 32)) {positive} {negative})"


def _step(x: str, y: str, z: str, index: int) -> tuple[str, str, str]:
    sx = f"(bvashr {x} (_ bv{index} 32))"
    sy = f"(bvashr {y} (_ bv{index} 32))"
    return (
        _select(z, f"(bvsub {x} {sy})", f"(bvadd {x} {sy})"),
        _select(z, f"(bvadd {y} {sx})", f"(bvsub {y} {sx})"),
        _select(z, f"(bvsub {z} {_signed32(ATAN[index])})",
                f"(bvadd {z} {_signed32(ATAN[index])})"),
    )


def _check_sources(golden: str, candidate: str, factor: int) -> None:
    """Fail closed unless the specific RTL expressions being modeled are present."""
    if factor not in (2, 4):
        raise ValueError("Only 2x and 4x factors are supported")
    gold_required = (
        "wire signed [31:0] x_shift = x >>> stage;",
        "wire signed [31:0] y_shift = y >>> stage;",
        "wire signed [31:0] x_next = (z >= 0) ? x - y_shift : x + y_shift;",
        "wire signed [31:0] y_next = (z >= 0) ? y + x_shift : y - x_shift;",
        "wire signed [31:0] z_next = (z >= 0) ? z - atan_value(stage) : z + atan_value(stage);",
    )
    if any(line not in golden for line in gold_required):
        raise ValueError("Golden microstep source changed; lemma translation needs review")
    for i in range(factor):
        px, py, pz = ("qx", "qy", "qz") if i == 0 else (
            f"gx_x_{i-1}", f"gx_y_{i-1}", f"gx_z_{i-1}")
        shift = f"(iteration_base + 5'd{i})"
        expected = (
            f"wire signed [31:0] gx_x_shift_{i} = {px} >>> {shift};",
            f"wire signed [31:0] gx_y_shift_{i} = {py} >>> {shift};",
            f"wire signed [31:0] gx_x_{i} = ({pz} >= 0) ? ({px} - gx_y_shift_{i}) : ({px} + gx_y_shift_{i});",
            f"wire signed [31:0] gx_y_{i} = ({pz} >= 0) ? ({py} + gx_x_shift_{i}) : ({py} - gx_x_shift_{i});",
            f"wire signed [31:0] gx_z_{i} = ({pz} >= 0) ? ({pz} - atan_value({shift})) : ({pz} + atan_value({shift}));",
        )
        if any(line not in candidate for line in expected):
            raise ValueError(f"Generated RTL macrostep {i} changed; lemma translation needs review")
    for i, angle in enumerate(ATAN):
        if f"5'd{i}: atan_value = 32'sd{angle};" not in candidate:
            raise ValueError(f"Candidate angle table entry {i} changed")
        if not re.search(rf"(?m)^\s*{i}: atan_value = {angle};$", golden):
            raise ValueError(f"Golden angle table entry {i} changed")


def _lemma_smt(factor: int, start: int) -> str:
    """Build an unsigned bit-vector disequality query over all input states."""
    lines = ["(set-logic QF_BV)", "(declare-fun x () (_ BitVec 32))",
             "(declare-fun y () (_ BitVec 32))",
             "(declare-fun z () (_ BitVec 32))"]
    gx, gy, gz = "x", "y", "z"
    mx, my, mz = "x", "y", "z"
    # Independent symbolic definitions, mapped to the checked Golden and
    # candidate source expressions. All arithmetic wraps modulo 2^32.
    for i in range(factor):
        idx = start + i
        gs = _step(gx, gy, gz, idx)
        ms = _step(mx, my, mz, idx)
        for name, value in zip((f"gold_x{i}", f"gold_y{i}", f"gold_z{i}"), gs):
            lines.append(f"(define-fun {name} () (_ BitVec 32) {value})")
        for name, value in zip((f"macro_x{i}", f"macro_y{i}", f"macro_z{i}"), ms):
            lines.append(f"(define-fun {name} () (_ BitVec 32) {value})")
        gx, gy, gz = (f"gold_x{i}", f"gold_y{i}", f"gold_z{i}")
        mx, my, mz = (f"macro_x{i}", f"macro_y{i}", f"macro_z{i}")
    checks = " ".join(f"(not (= {a} {b}))" for a, b in
                      zip((gx, gy, gz), (mx, my, mz)))
    lines.extend((f"(assert (or {checks}))", "(check-sat)"))
    return "\n".join(lines) + "\n"


def prove_macrosteps(golden: Path, candidates: dict[int, Path],
                     output: Path, *, timeout_seconds: int = 60) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "0.1.0", "kind": "cordic_macrostep_bv_lemma",
              "status": "error", "proof_scope": "combinational boundary-state only",
              "limitations": [
                  "Does not prove boundary-state reachability, control, resets, handshake or top-level traces.",
                  "RTL expressions are source-signature-checked; this is not an RTL netlist equivalence proof.",
                  "An inconclusive result never substitutes for the mandatory EQY gate.",
              ], "cases": []}
    try:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        solver = shutil.which("z3")
        if not solver:
            raise RuntimeError("z3 executable required")
        source = golden.read_text(encoding="utf-8")
        for factor, candidate_path in sorted(candidates.items()):
            gate = candidate_path.read_text(encoding="utf-8")
            _check_sources(source, gate, factor)
            for start in range(0, 16, factor):
                query = _lemma_smt(factor, start)
                result = subprocess.run([solver, "-in", "-smt2"], input=query,
                                        capture_output=True, text=True,
                                        timeout=timeout_seconds, check=False)
                status = "pass" if result.returncode == 0 and result.stdout.strip() == "unsat" else "inconclusive"
                report["cases"].append({"factor": factor, "iteration_start": start,
                    "status": status, "solver_result": result.stdout.strip(),
                    "solver_stderr": result.stderr.strip(),
                    "golden_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "candidate_sha256": hashlib.sha256(gate.encode()).hexdigest(),
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest()})
        report["status"] = "pass" if (report["cases"] and all(
            case["status"] == "pass" for case in report["cases"])) else "inconclusive"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    (output / "macrostep_lemma.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/architecture/macrostep-lemma"))
    args = parser.parse_args()
    report = prove_macrosteps(ROOT / "tasks/cordic/cordic_rotator.sv",
        {n: ROOT / f"tasks/cordic/generated/cordic_iter{n}.sv" for n in (2, 4)}, args.output)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
