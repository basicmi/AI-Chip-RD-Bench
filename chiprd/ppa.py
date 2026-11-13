"""Liberty-based standard-cell mapping and same-library area comparison.

This is a technology-mapping estimate, not physical-design PPA sign-off. The
caller supplies the Liberty file; no foundry PDK or proprietary library ships here.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT

TIMEOUT = 300
TOP = "cordic_rotator"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _library_cells(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r"\bcell\s*\(\s*([A-Za-z0-9_$./-]+)\s*\)\s*\{", text))
    if not names:
        raise ValueError("Liberty file contains no recognizable cell declarations")
    return names


def _area_from_log(log: str) -> float:
    matches = re.findall(
        r"Chip area for module\s+[^:\n]+:\s*([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)",
        log,
    )
    if not matches:
        raise RuntimeError("Yosys stat -liberty did not report a chip area")
    area = float(matches[-1])
    if not (area > 0):
        raise RuntimeError("Yosys reported a non-positive mapped cell area")
    return area


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                              timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Yosys timed out after {TIMEOUT}s") from exc


def _map_one(candidate: Path, liberty: Path, output: Path, known_cells: set[str]) -> dict:
    yosys = shutil.which("yosys")
    if not yosys:
        raise RuntimeError("Yosys unavailable; Liberty mapping NOT run")
    with tempfile.TemporaryDirectory(prefix="chiprd-liberty-map-") as temp:
        work = Path(temp)
        shutil.copyfile(candidate, work / "candidate.sv")
        shutil.copyfile(liberty, work / "cells.lib")
        script = (
            "read_verilog -sv candidate.sv\n"
            "read_liberty -lib cells.lib\n"
            f"hierarchy -check -top {TOP}\n"
            f"synth -top {TOP} -noabc\n"
            "dfflibmap -liberty cells.lib\n"
            "abc -liberty cells.lib\n"
            "clean\n"
            "check -assert\n"
            "stat -liberty cells.lib\n"
            "write_json mapped.json\n"
        )
        (work / "map.ys").write_text(script, encoding="utf-8")
        proc = _run([yosys, "-Q", "-T", "-s", "map.ys"], work)
        log = proc.stdout + "\n[stderr]\n" + proc.stderr
        (output / "yosys_mapping.log").write_text(log, encoding="utf-8")
        if proc.returncode != 0 or not (work / "mapped.json").is_file():
            raise RuntimeError(f"Yosys Liberty mapping failed (exit {proc.returncode})")
        design = json.loads((work / "mapped.json").read_text(encoding="utf-8"))
        module = design.get("modules", {}).get(TOP)
        if not isinstance(module, dict):
            raise RuntimeError("Mapped JSON does not contain the expected top module")
        cells = module.get("cells", {})
        if not isinstance(cells, dict) or not cells:
            raise RuntimeError("Mapped top module contains no standard cells")
        histogram = Counter()
        unmapped = []
        for cell in cells.values():
            cell_type = cell.get("type") if isinstance(cell, dict) else None
            if not isinstance(cell_type, str):
                unmapped.append("<invalid-cell-type>")
            elif cell_type not in known_cells:
                unmapped.append(cell_type)
            else:
                histogram[cell_type] += 1
        if unmapped:
            raise RuntimeError(
                "Technology mapping left non-Liberty cells: " + ", ".join(sorted(set(unmapped)))
            )
        area = _area_from_log(log)
        version = _run([yosys, "-V"], work)
        if version.returncode != 0:
            raise RuntimeError("Cannot determine Yosys version")
        return {
            "status": "pass",
            "source_sha256": _sha(candidate),
            "mapped_cell_count": sum(histogram.values()),
            "mapped_cell_area": area,
            "cell_type_histogram": dict(sorted(histogram.items())),
            "yosys_version": (version.stdout.strip() or version.stderr.strip()).splitlines()[0],
        }


def compare_area(
    candidate: Path,
    liberty: Path,
    output: Path,
    *,
    reference: Path | None = None,
    platform_label: str = "user-supplied-liberty",
) -> dict:
    """Map candidate and optional reference with the same Liberty and compare area."""
    if candidate.is_symlink() or liberty.is_symlink():
        raise ValueError("Candidate and Liberty inputs must not be symlinks")
    candidate = candidate.resolve(strict=True)
    liberty = liberty.resolve(strict=True)
    if not candidate.is_file() or not liberty.is_file():
        raise ValueError("Candidate and Liberty inputs must be regular files")
    if candidate.stat().st_size > 1_048_576:
        raise ValueError("Candidate RTL exceeds 1 MiB")
    if liberty.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Liberty file exceeds 64 MiB")
    if not platform_label or len(platform_label) > 128:
        raise ValueError("platform_label must contain 1–128 characters")
    if reference is not None and reference.is_symlink():
        raise ValueError("Reference RTL must not be a symlink")
    ref = reference.resolve(strict=True) if reference is not None else None
    if ref is not None and not ref.is_file():
        raise ValueError("Reference RTL must be a regular, non-symlink file")
    if ref is not None and ref.stat().st_size > 1_048_576:
        raise ValueError("Reference RTL exceeds 1 MiB")
    if output.is_symlink():
        raise ValueError("PPA output must not be a symlink")
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing PPA output")
    if output == ROOT.resolve():
        raise ValueError("PPA output must not be the repository root")
    output.mkdir(parents=True)
    library_hash = _sha(liberty)
    known_cells = _library_cells(liberty)
    report = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_liberty_mapped_area_comparison",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "platform_label": platform_label,
        "top_module": TOP,
        "library": {
            "filename": liberty.name,
            "sha256": library_hash,
            "size_bytes": liberty.stat().st_size,
            "declared_cell_count": len(known_cells),
            "area_units": "as defined by Liberty; no unit conversion applied",
        },
        "candidate": {"path": candidate.name, "sha256": _sha(candidate)},
        "reference": ({"path": ref.name, "sha256": _sha(ref)} if ref else None),
        "limitations": [
            "Liberty-mapped synthesis area only; not placed or routed area.",
            "No SDC constraints, static timing analysis, wire parasitics or clock-tree synthesis.",
            "No dynamic/leakage power, IR drop, EM, DRC, LVS or silicon validation.",
            "A public Nangate45 reference library is not a proxy for a modern production PDK.",
        ],
    }
    try:
        candidate_dir = output / "candidate"
        candidate_dir.mkdir()
        candidate_result = _map_one(candidate, liberty, candidate_dir, known_cells)
        report["candidate"]["mapping"] = candidate_result
        if ref is not None:
            reference_dir = output / "reference"
            reference_dir.mkdir()
            reference_result = _map_one(ref, liberty, reference_dir, known_cells)
            report["reference"]["mapping"] = reference_result
            baseline_area = reference_result["mapped_cell_area"]
            candidate_area = candidate_result["mapped_cell_area"]
            report["comparison"] = {
                "same_liberty_sha256": library_hash,
                "reference_area": baseline_area,
                "candidate_area": candidate_area,
                "area_delta": round(candidate_area - baseline_area, 6),
                "area_change_percent": round(
                    (candidate_area - baseline_area) / baseline_area * 100.0, 4
                ),
                "area_reduction_percent": round(
                    (baseline_area - candidate_area) / baseline_area * 100.0, 4
                ),
                "interpretation": (
                    "positive area_reduction_percent means the candidate maps smaller; "
                    "this is synthesis-only Liberty area, not physical PPA."
                ),
            }
        report["status"] = "pass"
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        report["status"] = "error"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
    (output / "ppa_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
