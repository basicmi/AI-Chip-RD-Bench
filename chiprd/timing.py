"""Same-library, same-SDC, pre-layout timing comparison for CORDIC.

Requires Yosys mapping and OpenROAD's OpenSTA engine. No placement, CTS or
parasitics are used; this intentionally does NOT claim routed delay/Fmax.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import ppa

DEFAULT_REFERENCE = ppa.ROOT / "tasks/cordic/cordic_rotator.sv"
MAX_PERIOD_NS = 1000.0


def _bounded(value: float, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and within [{low}, {high}]")
    return value


def _sha(path: Path) -> str:
    return ppa._sha(path)


def _constraints(period_ns: float, io_delay_ns: float) -> str:
    # All timing-bearing inputs/outputs are constrained; reset is a synchronous input.
    # No clock uncertainty, parasitics or physical wire-delay model is applied.
    return (
        f"create_clock -name core_clk -period {period_ns:.6f} [get_ports clk]\n"
        f"set_input_delay {io_delay_ns:.6f} -clock core_clk [get_ports {{rst input_valid angle_in*}}]\n"
        f"set_output_delay {io_delay_ns:.6f} -clock core_clk [get_ports {{output_valid x_out* y_out*}}]\n"
        "set_input_transition 0.05 [get_ports {rst input_valid angle_in*}]\n"
        "set_load 0.002 [get_ports {output_valid x_out* y_out*}]\n"
    )


def _parse_timing(log: str) -> dict:
    match = re.search(r"(?m)^CHIPRD_WNS_NS\s+([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)\s*$", log)
    if not match:
        raise RuntimeError("OpenSTA did not report a finite setup worst slack")
    wns = float(match.group(1))
    if not math.isfinite(wns):
        raise RuntimeError("OpenSTA worst slack was not finite")
    if not re.search(r"(?m)^\s*Startpoint:\s+\S", log) or not re.search(r"(?m)^\s*Endpoint:\s+\S", log):
        raise RuntimeError("OpenSTA returned no complete constrained critical path")
    return {
        "worst_setup_slack_ns": round(wns, 6),
        "meets_provided_clock_period": wns >= 0,
        "critical_path_reported": True,
    }


def _sta_netlist_text(yosys_verilog: str) -> str:
    """Remove RTL-only signed wire qualifiers from a fully mapped gate netlist.

    Older OpenSTA Verilog readers reject ANSI/non-ANSI 'input signed' syntax.
    This is safe only after tech mapping: combinational arithmetic has already
    been decomposed into standard cells and signedness cannot change cell wiring.
    """
    return re.sub(r"\b(input|output|wire)\s+signed\b", r"\1", yosys_verilog)


def _analyze_one(source: Path, liberty: Path, tech_lef: Path, cells_lef: Path,
                 output: Path, sdc: str, known_cells: set[str]) -> dict:
    output.mkdir()
    mapped = ppa._map_one(source, liberty, output, known_cells)
    openroad = shutil.which("openroad")
    if not openroad:
        raise RuntimeError("OpenROAD unavailable; no timing analysis was performed")
    with tempfile.TemporaryDirectory(prefix="chiprd-timing-") as directory:
        work = Path(directory)
        for original, name in (
            (liberty, "cells.lib"),
            (tech_lef, "tech.lef"),
            (cells_lef, "cells.lef"),
            # Source netlist is post-techmap; signed qualifiers have no role in gate connectivity.
        ):
            shutil.copyfile(original, work / name)
        (work / "mapped.v").write_text(_sta_netlist_text((output / "mapped.v").read_text(encoding="utf-8")), encoding="utf-8")
        (work / "constraints.sdc").write_text(sdc, encoding="utf-8")
        script = (
            "read_lef tech.lef\n"
            "read_lef cells.lef\n"
            "read_liberty cells.lib\n"
            "read_verilog mapped.v\n"
            f"link_design {ppa.TOP}\n"
            "read_sdc constraints.sdc\n"
            "report_checks -path_delay max -group_path_count 5 -digits 4\n"
            "report_worst_slack -max -digits 6\n"
            "puts \"CHIPRD_WNS_NS [sta::worst_slack -max]\"\n"
        )
        (work / "timing.tcl").write_text(script, encoding="utf-8")
        proc = ppa._run([openroad, "-exit", "timing.tcl"], work)
        log = proc.stdout + "\n[stderr]\n" + proc.stderr
        (output / "opensta.log").write_text(log, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"OpenROAD/OpenSTA failed (exit {proc.returncode})")
        timing = _parse_timing(log)
        version = ppa._run([openroad, "-version"], work)
        if version.returncode != 0:
            raise RuntimeError("Cannot determine OpenROAD version")
        return {
            "status": "pass",
            "mapping": mapped,
            "timing": timing,
            "openroad_version": (version.stdout.strip() or version.stderr.strip()).splitlines()[0],
        }


def compare_timing(
    candidate: Path, reference: Path, liberty: Path,
    tech_lef: Path, cells_lef: Path, output: Path,
    *, clock_period_ns: float = 10.0, io_delay_ns: float = 0.5,
) -> dict:
    """Run common-Liberty mapping and OpenSTA pre-layout timing for both sources."""
    period = _bounded(clock_period_ns, "clock_period_ns", 0.05, MAX_PERIOD_NS)
    io_delay = _bounded(io_delay_ns, "io_delay_ns", 0.0, period / 2.0)
    for name, path, max_bytes in (
        ("candidate", candidate, 1_048_576), ("reference", reference, 1_048_576),
        ("liberty", liberty, 64 * 1024 * 1024),
        ("tech_lef", tech_lef, 16 * 1024 * 1024),
        ("cells_lef", cells_lef, 32 * 1024 * 1024),
    ):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            raise ValueError(f"{name} must be a bounded, non-symlink regular file")
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite a prior timing report")
    if output == ppa.ROOT.resolve():
        raise ValueError("Output cannot be the repository root")
    source_paths = {"candidate": candidate.resolve(), "reference": reference.resolve()}
    files = {"liberty": liberty.resolve(), "tech_lef": tech_lef.resolve(), "cells_lef": cells_lef.resolve()}
    sdc = _constraints(period, io_delay)
    known_cells = ppa._library_cells(files["liberty"])
    output.mkdir(parents=True)
    (output / "constraints.sdc").write_text(sdc, encoding="utf-8")
    report = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_pre_layout_timing_comparison",
        "status": "error",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "timing_mode": "ideal_clock_pre_layout_no_parasitics",
        "clock_period_ns": period,
        "io_delay_ns": io_delay,
        "sdc_sha256": hashlib.sha256(sdc.encode("utf-8")).hexdigest(),
        "inputs": {
            key: {"filename": path.name, "sha256": _sha(path)}
            for key, path in {**files, **source_paths}.items()
        },
        "limitations": [
            "Gate-level OpenSTA with mapped standard cells and explicit SDC; not placed/routed STA.",
            "No wire parasitics or SPEF, no clock tree, no multi-corner signoff or production PDK.",
            "WNS is relative to the supplied clock and I/O assumptions, not a measured Fmax.",
            "Both designs use identical Liberty, LEF, SDC and synthesis mapping recipe.",
        ],
    }
    try:
        candidate_result = _analyze_one(
            source_paths["candidate"], files["liberty"], files["tech_lef"], files["cells_lef"],
            output / "candidate", sdc, known_cells,
        )
        reference_result = _analyze_one(
            source_paths["reference"], files["liberty"], files["tech_lef"], files["cells_lef"],
            output / "reference", sdc, known_cells,
        )
        report["candidate"] = candidate_result
        report["reference"] = reference_result
        candidate_wns = candidate_result["timing"]["worst_setup_slack_ns"]
        reference_wns = reference_result["timing"]["worst_setup_slack_ns"]
        report["comparison"] = {
            "candidate_minus_reference_wns_ns": round(candidate_wns - reference_wns, 6),
            "candidate_minus_reference_area": round(
                candidate_result["mapping"]["mapped_cell_area"]
                - reference_result["mapping"]["mapped_cell_area"], 6
            ),
            "reference_meets_timing": reference_wns >= 0,
            "candidate_meets_timing": candidate_wns >= 0,
        }
        report["status"] = "pass"
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        report["status"] = "error"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
    (output / "timing_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
