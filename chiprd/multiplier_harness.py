"""Experimental Verilator/Yosys evaluator for the signed 16x16 multiplier."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .harness import ROOT


def _vectors(seed: int, random_count: int) -> list[tuple[int, int]]:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("seed must be uint32")
    if isinstance(random_count, bool) or not isinstance(random_count, int) or not 1 <= random_count <= 100_000:
        raise ValueError("random_count must be in [1,100000]")
    edge = [-32768, -32767, -1, 0, 1, 2, 32766, 32767]
    pairs = [(a, b) for a in edge for b in edge]
    pairs += [(a, a) for a in edge]
    rng = random.Random(seed)
    pairs += [(rng.randint(-32768, 32767), rng.randint(-32768, 32767))
              for _ in range(random_count)]
    return pairs


def evaluate(candidate: Path, output: Path, *, seed: int = 20261116,
             random_count: int = 2048) -> dict:
    candidate = Path(candidate).resolve(strict=True)
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("candidate must be a regular RTL file")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    vectors = _vectors(seed, random_count)
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    report = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_multiplier_experimental_evaluation",
        "task_id": "multiplier.signed16x16.v1",
        "candidate_sha256": digest, "seed": seed,
        "edge_vector_count": 72, "random_vector_count": random_count,
        "tested_vectors": len(vectors), "status": "error",
        "maturity": "experimental",
        "limitations": [
            "Functional sampled simulation plus generic synthesis only.",
            "No formal/equivalence/mutation/mapped PPA or post-selection audit.",
            "Not comparable with the six-gate CORDIC task.",
        ],
    }
    verilator, yosys = shutil.which("verilator"), shutil.which("yosys")
    if not verilator or not yosys:
        raise RuntimeError("Verilator and Yosys are required")
    tb = ROOT / "tasks/multiplier/tb_int_mul16.sv"
    with tempfile.TemporaryDirectory(prefix="chiprd-mul16-") as tmp:
        work = Path(tmp)
        shutil.copyfile(candidate, work / "candidate.sv")
        with (work / "vectors.txt").open("w", encoding="ascii") as stream:
            for a, b in vectors:
                stream.write(f"{a & 0xffff:04x} {b & 0xffff:04x}\n")
        compile_run = subprocess.run(
            [verilator, "--binary", "--timing", "-Wno-fatal",
             "--top-module", "tb_int_mul16", "--Mdir", str(work / "obj_dir"),
             "-o", "simv", str(work / "candidate.sv"), str(tb)],
            cwd=work, text=True, capture_output=True, timeout=180, check=False)
        (output / "verilator_compile.log").write_text(
            compile_run.stdout + "\n[stderr]\n" + compile_run.stderr)
        if compile_run.returncode:
            report.update(status="fail", reason="verilator_compile_failed")
        else:
            sim = subprocess.run([str(work / "obj_dir/simv")], cwd=work,
                                 text=True, capture_output=True, timeout=60, check=False)
            (output / "verilator_sim.log").write_text(
                sim.stdout + "\n[stderr]\n" + sim.stderr)
            observed = {}
            for line in sim.stdout.splitlines():
                match = re.fullmatch(r"OUT (\\d+) ([0-9a-fA-F]{8})", line.strip())
                if match:
                    idx = int(match.group(1))
                    value = int(match.group(2), 16)
                    if value & 0x80000000:
                        value -= 0x1_0000_0000
                    if idx in observed:
                        raise ValueError("duplicate output index")
                    observed[idx] = value
            failures = []
            if sim.returncode:
                report.update(status="fail", reason="simulation_failed")
            else:
                for idx, (a, b) in enumerate(vectors):
                    if observed.get(idx) != a * b:
                        failures.append({"index": idx, "a": a, "b": b,
                                         "expected": a * b, "observed": observed.get(idx)})
                report.update(functional_status="pass" if not failures and len(observed) == len(vectors) else "fail",
                              received_vectors=len(observed), failures=failures)
        (work / "synth.ys").write_text(
            "read_verilog -sv candidate.sv\nhierarchy -check -top int_mul16\n"
            "synth -top int_mul16\nstat\n", encoding="utf-8")
        synth = subprocess.run([yosys, "-Q", "-T", "-s", "synth.ys"], cwd=work,
                               text=True, capture_output=True, timeout=180, check=False)
        (output / "yosys.log").write_text(synth.stdout + "\n[stderr]\n" + synth.stderr)
        report["generic_synthesis_status"] = "pass" if synth.returncode == 0 else "fail"
        report["status"] = "pass" if (
            report.get("functional_status") == "pass"
            and report["generic_synthesis_status"] == "pass"
        ) else "fail"
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=ROOT / "tasks/multiplier/int_mul16.sv")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261116)
    parser.add_argument("--random-vectors", type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.candidate, args.output, seed=args.seed,
                          random_count=args.random_vectors)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.error(f"Multiplier evaluation rejected: {type(exc).__name__}: {exc}")
    print(json.dumps({k: result[k] for k in (
        "task_id", "status", "tested_vectors", "generic_synthesis_status"
    )}, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
