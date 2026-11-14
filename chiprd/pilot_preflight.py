"""Offline preflight for the first real-model CORDIC PPA pilot.

Checks configuration and locally installed dependencies without contacting the
model provider, running EDA, or recording API keys or URL path/query data.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path

from .agent_ppa import PPAObjective
from .harness import ROOT
from .ppa import _sha

TOOLS = ("python3", "git", "verilator", "yosys", "openroad", "eqy", "sby", "z3")
ADAPTER = ROOT / "examples/agents/llm_agent.py"


def _model_configuration() -> dict:
    """Reuse the real model adapter's validation, but omit secret values."""
    spec = importlib.util.spec_from_file_location("_chiprd_pilot_adapter", ADAPTER)
    if spec is None or spec.loader is None:
        raise ValueError("Model Agent adapter is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module._config()
    endpoint = config["endpoint"]
    from urllib.parse import urlsplit
    parsed = urlsplit(endpoint)
    # Do not serialize path, query, credentials, or API keys.
    return {
        "model": config["model"],
        "endpoint_origin": f"{parsed.scheme}://{parsed.netloc}",
        "temperature": config["temperature"],
        "max_tokens": config["max_tokens"],
        "timeout_seconds": config["timeout"],
        "api_key": "not captured",
    }


def assess(objective: PPAObjective) -> dict:
    missing = [name for name in TOOLS if not shutil.which(name)]
    faults = []
    ppa = None
    model = None
    try:
        ppa = objective.validate()
    except (OSError, ValueError) as exc:
        # Only the argument name is retained; never serialize file contents.
        faults.append(f"PPA inputs: {type(exc).__name__}")
    try:
        model = _model_configuration()
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        faults.append(f"Model configuration: {type(exc).__name__}")
    for relative in ("tasks/cordic/cordic_rotator.sv", "tasks/cordic/spec.md"):
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            faults.append(f"Missing regular file: {relative}")
    ready = not missing and not faults
    return {
        "schema_version": "1.0.0",
        "report_type": "chiprd_real_model_ppa_preflight",
        "status": "pass" if ready else "fail",
        "local_configuration_ready": ready,
        "missing_executables": missing,
        "configuration_errors": faults,
        "ppa_configuration": ppa,
        "model_configuration": model,
        "checks_scope": "offline presence and config only; NOT model reachability or EDA execution",
        "next_action": (
            "User may explicitly start an isolated PPA campaign after reviewing model privacy/cost."
            if ready else "Resolve missing tools/configuration before starting a paid experiment."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--liberty", type=Path, required=True)
    parser.add_argument("--tech-lef", type=Path, required=True)
    parser.add_argument("--cells-lef", type=Path, required=True)
    parser.add_argument("--clock-period-ns", type=float, default=10)
    parser.add_argument("--io-delay-ns", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=None,
                        help="optional new path to write redacted JSON evidence")
    args = parser.parse_args(argv)
    objective = PPAObjective(args.liberty, args.tech_lef, args.cells_lef,
                             args.clock_period_ns, args.io_delay_ns)
    result = assess(objective)
    if args.output is not None:
        if args.output.is_symlink() or args.output.exists():
            parser.error("Refusing to overwrite an existing output")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["local_configuration_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
