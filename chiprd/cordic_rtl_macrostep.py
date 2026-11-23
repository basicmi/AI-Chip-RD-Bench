"""Independently elaborate RTL-derived Golden and unrolled CORDIC datapaths.

This is a combinational macrostep miter, not sequential transaction proof.
The Golden and candidate expressions are extracted separately from checked
SystemVerilog source, then parsed and proved by Yosys' own RTL frontend/SAT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .cordic_macrostep_proof import _check_sources
from .harness import ROOT

FUNCTION_RE = re.compile(r"    function automatic signed \[31:0\] atan_value\b.*?    endfunction", re.S)
WIRE_RE = re.compile(r"^    wire signed \[31:0\] (\w+) = (.*);$", re.M)
GOLD_WIRES = ("x_shift", "y_shift", "x_next", "y_next", "z_next")
ATOM_RE = re.compile(r"\b(?:x_shift|y_shift|x_next|y_next|z_next|stage|x|y|z)\b")


def _function(source: str) -> str:
    match = FUNCTION_RE.search(source)
    if not match:
        raise ValueError("Missing atan_value RTL function")
    return match.group(0)


def _wires(source: str) -> dict[str, str]:
    result = {}
    for key, expr in WIRE_RE.findall(source):
        if key in result:
            raise ValueError(f"Duplicate RTL wire: {key}")
        result[key] = expr
    return result


def make_miter(golden: str, candidate: str, factor: int, start: int) -> str:
    """Build an RTL miter for arbitrary 32-bit initial states at a legal boundary."""
    if factor not in (2, 4) or start not in range(0, 16, factor):
        raise ValueError("Unsupported factor/iteration boundary")
    _check_sources(golden, candidate, factor)
    gwires = _wires(golden)
    cwires = _wires(candidate)
    if not all(name in gwires for name in GOLD_WIRES):
        raise ValueError("Incomplete Golden combinational RTL")
    lines = [
        "module gold_macro(input signed [31:0] qx, qy, qz,",
        "  output signed [31:0] ox, oy, oz);",
        _function(golden),
    ]
    px, py, pz = "qx", "qy", "qz"
    for index in range(factor):
        names = {name: f"gold_{index}_{name}" for name in GOLD_WIRES}
        names.update({"stage": f"4'd{start + index}", "x": px, "y": py, "z": pz})
        for key in GOLD_WIRES:
            expression = ATOM_RE.sub(lambda m: names[m.group(0)], gwires[key])
            lines.append(f"  wire signed [31:0] {names[key]} = {expression};")
        px, py, pz = names["x_next"], names["y_next"], names["z_next"]
    lines += [f"  assign ox = {px};", f"  assign oy = {py};",
              f"  assign oz = {pz};", "endmodule",
              "module gate_macro(input signed [31:0] qx, qy, qz,",
              "  output signed [31:0] ox, oy, oz);",
              _function(candidate)]
    for index in range(factor):
        for key in (f"gx_x_shift_{index}", f"gx_y_shift_{index}",
                    f"gx_x_{index}", f"gx_y_{index}", f"gx_z_{index}"):
            if key not in cwires:
                raise ValueError(f"Missing candidate RTL wire {key}")
            expression = re.sub(r"\biteration_base\b", f"5'd{start}", cwires[key])
            lines.append(f"  wire signed [31:0] {key} = {expression};")
    lines += [f"  assign ox = gx_x_{factor - 1};",
              f"  assign oy = gx_y_{factor - 1};",
              f"  assign oz = gx_z_{factor - 1};",
              "endmodule",
              "module macrostep_miter(input signed [31:0] x, y, z,",
              "  output mismatch);",
              "  wire signed [31:0] gx, gy, gz, cx, cy, cz;",
              "  gold_macro golden(.qx(x), .qy(y), .qz(z), .ox(gx), .oy(gy), .oz(gz));",
              "  gate_macro candidate(.qx(x), .qy(y), .qz(z), .ox(cx), .oy(cy), .oz(cz));",
              "  assign mismatch = (gx != cx) || (gy != cy) || (gz != cz);",
              "endmodule", ""]
    return "\n".join(lines)


def prove_rtl_macrosteps(golden: Path, candidates: dict[int, Path],
                         output: Path, *, timeout_seconds: int = 90) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "0.1.0",
        "kind": "cordic_rtl_macrostep_sat",
        "status": "error",
        "scope": "RTL-frontend-derived combinational macrostep comparison",
        "limitations": [
            "Not a sequential-equivalence, induction, reset, handshake or latency proof.",
            "Requires independent RTL transition and boundary reachability proof.",
            "Never replaces the mandatory full EQY gate."
        ],
        "cases": [],
    }
    try:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        yosys = shutil.which("yosys")
        if not yosys:
            raise RuntimeError("yosys is required")
        gtext = golden.read_text(encoding="utf-8")
        for factor, rtl in sorted(candidates.items()):
            ctext = rtl.read_text(encoding="utf-8")
            for start in range(0, 16, factor):
                source = make_miter(gtext, ctext, factor, start)
                name = f"iter{factor}_start{start}"
                path = output / f"{name}.sv"
                path.write_text(source, encoding="utf-8")
                script = (
                    f"read_verilog -sv {path.name}; "
                    "hierarchy -check -top macrostep_miter; "
                    "proc; flatten; opt; "
                    "sat -verify -prove mismatch 0"
                )
                case = {"factor": factor, "iteration_start": start,
                        "miter_sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "golden_sha256": hashlib.sha256(gtext.encode()).hexdigest(),
                        "candidate_sha256": hashlib.sha256(ctext.encode()).hexdigest()}
                try:
                    proc = subprocess.run([yosys, "-Q", "-T", "-p", script],
                        cwd=output, capture_output=True, text=True,
                        timeout=timeout_seconds, check=False)
                    log = proc.stdout + "\n" + proc.stderr
                    (output / f"{name}.log").write_text(log, encoding="utf-8")
                    case["returncode"] = proc.returncode
                    case["status"] = ("pass" if proc.returncode == 0
                        and "SUCCESS!" in log and "no model found" in log.lower()
                        else "fail" if proc.returncode != 0 else "inconclusive")
                except subprocess.TimeoutExpired:
                    case["status"] = "inconclusive"
                    case["error"] = f"Yosys SAT timed out after {timeout_seconds}s"
                report["cases"].append(case)
        report["status"] = ("pass" if report["cases"] and
            all(c["status"] == "pass" for c in report["cases"]) else "inconclusive")
    except (OSError, RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
    (output / "rtl_macrostep_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/architecture/rtl-macrostep"))
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()
    result = prove_rtl_macrosteps(ROOT / "tasks/cordic/cordic_rotator.sv",
        {n: ROOT / f"tasks/cordic/generated/cordic_iter{n}.sv" for n in (2, 4)},
        args.output, timeout_seconds=args.timeout_seconds)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
