"""Deterministic, fail-closed Verilator and Yosys adapters.

These tools run HDL code and are not a security sandbox. Only execute candidates
inside an appropriately isolated environment (container or dedicated runner).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .oracle import DEFAULT_TOLERANCE, check, expected, vectors

ROOT = Path(__file__).resolve().parent.parent
TESTBENCH = ROOT / "tasks" / "cordic" / "tb_cordic.sv"
TIMEOUT = 180


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                              timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Tool timed out after {TIMEOUT}s: {args[0]}") from exc


def tool_version(executable: str) -> str:
    tool = shutil.which(executable)
    if not tool:
        raise RuntimeError(f"Required EDA tool unavailable: {executable}")
    out = run_command([tool, "--version" if executable == "verilator" else "-V"], ROOT)
    if out.returncode != 0:
        raise RuntimeError(f"Cannot determine {executable} version: {out.stderr}")
    return (out.stdout.strip() or out.stderr.strip()).splitlines()[0]


def base_report(candidate: Path, kind: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "task_id": "cordic.rotation.q14.v1",
        "kind": kind,
        "candidate_sha256": hash_file(candidate),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "limitations": [],
    }


def verify(candidate: Path, output: Path, seed: int = 20261112) -> dict:
    tool = shutil.which("verilator")
    if not tool:
        raise RuntimeError("Verilator unavailable; verification NOT run")
    test_angles = vectors(seed=seed)
    report = base_report(candidate, "functional")
    report.update(tool="verilator", tool_version=tool_version("verilator"),
                  testbench_sha256=hash_file(TESTBENCH), seed=seed,
                  tolerance_lsb=DEFAULT_TOLERANCE)
    with tempfile.TemporaryDirectory(prefix="chiprd-verify-") as temp:
        work = Path(temp)
        shutil.copyfile(candidate, work / "candidate.sv")
        with (work / "vectors.mem").open("w", encoding="ascii") as f:
            for angle in test_angles:
                f.write(f"{angle & 0xffff:04x}\n")
        compile_result = run_command(
            [tool, "--binary", "--timing", "-Wno-fatal", "--top-module", "tb_cordic",
             "-GNUM_VECTORS=" + str(len(test_angles)),
             "--Mdir", str(work / "obj_dir"), "-o", "simv",
             str(work / "candidate.sv"), str(TESTBENCH)], work
        )
        (output / "verilator_compile.log").write_text(
            compile_result.stdout + "\n[stderr]\n" + compile_result.stderr, encoding="utf-8")
        if compile_result.returncode != 0:
            report.update(status="fail", reason="compile_error", compile_exit=compile_result.returncode)
            return report
        sim = run_command([str(work / "obj_dir" / "simv")], work)
        (output / "verilator_sim.log").write_text(
            sim.stdout + "\n[stderr]\n" + sim.stderr, encoding="utf-8")
        if sim.returncode != 0:
            report.update(status="fail", reason="simulation_error", sim_exit=sim.returncode)
            return report
        observed: dict[int, tuple[int, int]] = {}
        for line in sim.stdout.splitlines():
            match = re.fullmatch(r"OUT\s+(\d+)\s+(-?\d+)\s+(-?\d+)", line.strip())
            if match:
                index, x, y = map(int, match.groups())
                if index in observed or index >= len(test_angles):
                    report.update(status="fail", reason="duplicate_or_invalid_output_index")
                    return report
                observed[index] = (x, y)
        failures = []
        for index, angle in enumerate(test_angles):
            got = observed.get(index)
            if got is None or not check(angle, *got):
                failures.append({
                    "index": index, "angle_q3_13": angle,
                    "expected_q2_14": list(expected(angle)),
                    "observed_q2_14": list(got) if got is not None else None,
                })
        report.update(status="pass" if not failures else "fail",
                      tested=len(test_angles), received=len(observed),
                      failures=failures, reason=None if not failures else "oracle_mismatch")
        return report


def synthesize(candidate: Path, output: Path) -> dict:
    tool = shutil.which("yosys")
    if not tool:
        raise RuntimeError("Yosys unavailable; synthesis NOT run")
    report = base_report(candidate, "synthesis")
    report.update(tool="yosys", tool_version=tool_version("yosys"),
                  limitations=["Generic cell count is not standard-cell area, timing or power."])
    with tempfile.TemporaryDirectory(prefix="chiprd-synth-") as temp:
        work = Path(temp)
        shutil.copyfile(candidate, work / "candidate.sv")
        (work / "flow.ys").write_text(
            "read_verilog -sv candidate.sv\n"
            "hierarchy -check -top cordic_rotator\n"
            "synth -top cordic_rotator\n"
            "write_json mapped.json\n", encoding="utf-8")
        proc = run_command([tool, "-Q", "-T", "-s", "flow.ys"], work)
        (output / "yosys.log").write_text(
            proc.stdout + "\n[stderr]\n" + proc.stderr, encoding="utf-8")
        if proc.returncode != 0 or not (work / "mapped.json").exists():
            report.update(status="fail", reason="synthesis_error", synthesis_exit=proc.returncode)
            return report
        design = json.loads((work / "mapped.json").read_text(encoding="utf-8"))
        module = design.get("modules", {}).get("cordic_rotator")
        if not module:
            report.update(status="fail", reason="missing_top_module")
            return report
        cells = module.get("cells", {})
        report.update(status="pass", generic_cell_count=len(cells),
                      cell_type_histogram={
                          cell_type: sum(c.get("type") == cell_type for c in cells.values())
                          for cell_type in sorted({c.get("type") for c in cells.values()})
                      })
        return report


def run(candidate: Path, output: Path, mode: str = "all", seed: int = 20261112) -> dict:
    candidate = candidate.resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("Candidate must be a regular file")
    output.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": "0.1.0", "task_id": "cordic.rotation.q14.v1",
              "candidate": str(candidate), "candidate_sha256": hash_file(candidate),
              "results": {}, "status": "error"}
    try:
        if mode in {"verify", "all"}:
            result["results"]["functional"] = verify(candidate, output, seed=seed)
        # Fail closed: no synthesis after functional failure when using all.
        if mode in {"synth", "all"} and (
            mode == "synth" or result["results"]["functional"]["status"] == "pass"
        ):
            result["results"]["synthesis"] = synthesize(candidate, output)
        statuses = [item["status"] for item in result["results"].values()]
        result["status"] = "pass" if statuses and all(x == "pass" for x in statuses) else "fail"
        if mode == "all" and result["results"].get("functional", {}).get("status") != "pass":
            result["status"] = "fail"
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    (output / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                                         encoding="utf-8")
    return result
