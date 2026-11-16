"""Pre-layout max-delay analysis for combinational standard-cell IP.

Unlike the sequential CORDIC timing flow, this runner constrains explicit
input-to-output paths with set_max_delay and does not invent a clock.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import ppa
from .timing import _sta_netlist_text

MAX_DELAY_NS = 1000.0
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _ports(values, name: str) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{name} must contain at least one port name")
    result = []
    for value in values:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"{name} contains an invalid HDL port identifier")
        if value in result:
            raise ValueError(f"{name} contains duplicate port {value}")
        result.append(value)
    return result


def _finite(value: float, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and within [{low}, {high}]")
    return value


def _constraints(inputs: list[str], outputs: list[str], max_delay_ns: float,
                 input_transition_ns: float, output_load_pf: float) -> str:
    # The only constrained paths are explicitly named top-level inputs -> outputs.
    # No clock, reset, launch/capture assumptions, or physical parasitics are added.
    in_ports = " ".join(inputs)
    out_ports = " ".join(f"{name}*" for name in outputs)
    return (
        f"set_max_delay {max_delay_ns:.6f} -from [get_ports {{{in_ports}}}] "
        f"-to [get_ports {{{out_ports}}}]\n"
        f"set_input_transition {input_transition_ns:.6f} "
        f"[get_ports {{{in_ports}}}]\n"
        f"set_load {output_load_pf:.6f} [get_ports {{{out_ports}}}]\n"
    )


def _parse_timing(log: str, max_delay_ns: float) -> dict:
    match = re.search(
        r"(?m)^CHIPRD_WNS_NS\s+([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)\s*$",
        log,
    )
    if not match:
        raise RuntimeError("OpenSTA did not report finite max-delay slack")
    slack = float(match.group(1))
    if not math.isfinite(slack):
        raise RuntimeError("OpenSTA max-delay slack was not finite")
    start = re.search(r"(?m)^\s*Startpoint:\s+(\S.*)$", log)
    end = re.search(r"(?m)^\s*Endpoint:\s+(\S.*)$", log)
    if not start or not end:
        raise RuntimeError("OpenSTA returned no complete constrained input-to-output path")
    return {
        "worst_max_delay_slack_ns": round(slack, 6),
        "meets_max_delay": slack >= 0,
        "estimated_worst_path_delay_ns": round(delay - slack, 6),
        "critical_startpoint": start.group(1).strip(),
        "critical_endpoint": end.group(1).strip(),
        "critical_path_reported": True,
    }


def analyze_combinational_timing(
    candidate: Path, liberty: Path, tech_lef: Path, cells_lef: Path, output: Path,
    *, top_module: str, input_ports, output_ports,
    max_delay_ns: float = 10.0, input_transition_ns: float = 0.05,
    output_load_pf: float = 0.002, platform_label: str = "user-supplied-liberty",
) -> dict:
    """Map one combinational top and run OpenSTA input-to-output max-delay analysis."""
    if not _IDENTIFIER.fullmatch(top_module):
        raise ValueError("top_module must be a valid HDL identifier")
    inputs = _ports(input_ports, "input_ports")
    outputs = _ports(output_ports, "output_ports")
    if set(inputs) & set(outputs):
        raise ValueError("input_ports and output_ports must be disjoint")
    delay = _finite(max_delay_ns, "max_delay_ns", 0.001, MAX_DELAY_NS)
    transition = _finite(input_transition_ns, "input_transition_ns", 0.0, 100.0)
    load = _finite(output_load_pf, "output_load_pf", 0.0, 1000.0)
    if not isinstance(platform_label, str) or not platform_label or len(platform_label) > 128:
        raise ValueError("platform_label must contain 1–128 characters")
    for name, path, limit in (
        ("candidate", candidate, 1_048_576), ("liberty", liberty, 64 * 1024 * 1024),
        ("tech_lef", tech_lef, 16 * 1024 * 1024), ("cells_lef", cells_lef, 32 * 1024 * 1024),
    ):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError(f"{name} must be a bounded, non-symlink regular file")
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite a prior combinational timing report")
    if output == ppa.ROOT.resolve():
        raise ValueError("Output cannot be the repository root")

    source = candidate.resolve()
    lib, tech, cells = liberty.resolve(), tech_lef.resolve(), cells_lef.resolve()
    sdc = _constraints(inputs, outputs, delay, transition, load)
    output.mkdir(parents=True)
    (output / "constraints.sdc").write_text(sdc, encoding="utf-8")
    report = {
        "schema_version": "1.0.0",
        "report_type": "chiprd_combinational_pre_layout_timing",
        "status": "error",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "timing_mode": "input_to_output_max_delay_no_clock_no_parasitics",
        "platform_label": platform_label,
        "top_module": top_module,
        "constraints": {
            "input_ports": inputs, "output_ports": outputs,
            "max_delay_ns": delay, "input_transition_ns": transition,
            "output_load_pf": load,
            "sdc_sha256": hashlib.sha256(sdc.encode("utf-8")).hexdigest(),
        },
        "inputs": {
            key: {"filename": path.name, "sha256": ppa._sha(path)}
            for key, path in {
                "candidate": source, "liberty": lib, "tech_lef": tech, "cells_lef": cells
            }.items()
        },
        "limitations": [
            "Gate-level OpenSTA max-delay analysis on mapped standard cells; not placed/routed STA.",
            "No clock, launch/capture model, wire parasitics, SPEF, placement or multi-corner signoff.",
            "Input transition and output load are explicit assumptions; results are not silicon delay.",
            "Nangate45 is a public reference library, not a modern production PDK.",
        ],
    }
    try:
        mapped_dir = output / "candidate"
        mapped_dir.mkdir()
        known_cells = ppa._library_cells(lib)
        mapping = ppa._map_one(source, lib, mapped_dir, known_cells, top_module=top_module)
        openroad = shutil.which("openroad")
        if not openroad:
            raise RuntimeError("OpenROAD unavailable; no combinational timing analysis was performed")
        with tempfile.TemporaryDirectory(prefix="chiprd-comb-timing-") as temp:
            work = Path(temp)
            for original, name in ((lib, "cells.lib"), (tech, "tech.lef"), (cells, "cells.lef")):
                shutil.copyfile(original, work / name)
            (work / "mapped.v").write_text(
                _sta_netlist_text((mapped_dir / "mapped.v").read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            (work / "constraints.sdc").write_text(sdc, encoding="utf-8")
            script = (
                "read_lef tech.lef\nread_lef cells.lef\nread_liberty cells.lib\n"
                "read_verilog mapped.v\n"
                f"link_design {top_module}\nread_sdc constraints.sdc\n"
                "report_checks -path_delay max -digits 4\n"
                "report_worst_slack -max -digits 6\n"
                "puts \\"CHIPRD_WNS_NS [sta::worst_slack -max]\\"\n"
            )
            (work / "timing.tcl").write_text(script, encoding="utf-8")
            proc = ppa._run([openroad, "-exit", "timing.tcl"], work)
            log = proc.stdout + "\n[stderr]\n" + proc.stderr
            (output / "opensta.log").write_text(log, encoding="utf-8")
            if proc.returncode != 0:
                raise RuntimeError(f"OpenROAD/OpenSTA failed (exit {proc.returncode})")
            timing = _parse_timing(log, delay)
            version = ppa._run([openroad, "-version"], work)
            if version.returncode != 0:
                raise RuntimeError("Cannot determine OpenROAD version")
            report.update({
                "candidate": {"status": "pass", "mapping": mapping, "timing": timing},
                "openroad_version": (version.stdout.strip() or version.stderr.strip()).splitlines()[0],
                "status": "pass",
            })
    except (RuntimeError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
    (output / "combinational_timing_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
