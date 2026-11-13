"""Bounded closed-loop orchestration for an external RTL design Agent.

The controller owns feedback, patch validation, candidate lineage and budgets.
The Agent is a separate executable supplied by the user.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .harness import ROOT
from .patches import PATCH_TARGET_PATH, apply_patch_proposal
from .search import _metrics, _run_sealed_experiment

SCHEMA_VERSION = "1.0.0"
MAX_AGENT_OUTPUT_BYTES = 1_048_576
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class AgentLoopBudget:
    max_iterations: int = 3
    max_wall_clock_seconds: float = 3600.0
    max_agent_seconds: float = 180.0
    max_experiment_seconds: float = 600.0
    max_candidate_bytes: int = 1_048_576

    def validate(self) -> None:
        for name in ("max_iterations", "max_candidate_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be an integer of at least 1")
        for name in (
            "max_wall_clock_seconds", "max_agent_seconds", "max_experiment_seconds"
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")


def parse_agent_command(value: str) -> list[str]:
    """Parse a JSON argv array. Commands are never passed through a shell."""
    try:
        command = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"agent command must be a JSON array: {exc}") from exc
    if (
        not isinstance(command, list) or not command
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
    ):
        raise ValueError("agent command must be a non-empty JSON array of non-empty strings")
    if len(command) > 64 or sum(len(arg) for arg in command) > 16_384:
        raise ValueError("agent command exceeds the argument count or size limit")
    return command


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(path.read_bytes())


def _canonical_hash(value: object) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Path must remain inside the repository root") from exc


def _safe_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field} must be a 1-64 character safe identifier")
    return value


def _append_event(path: Path, event: str, **fields: object) -> None:
    previous = "0" * 64
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines:
            previous = json.loads(lines[-1]).get("event_sha256", previous)
    record = {"timestamp_utc": _now(), "event": event,
              "previous_event_sha256": previous, **fields}
    record["event_sha256"] = _canonical_hash(record)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _kill_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()


def _run_agent(command: list[str], request: dict, timeout: float) -> dict:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return {"status": "error", "stdout": "", "stderr": "",
                "exit_code": None, "timed_out": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"could not start Agent command: {exc}"}
    timed_out = False
    try:
        stdout, stderr = process.communicate(
            json.dumps(request, sort_keys=True, ensure_ascii=False) + "\n",
            timeout=max(0.001, timeout),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)
    if len(stdout.encode("utf-8")) > MAX_AGENT_OUTPUT_BYTES:
        return {"status": "error", "stdout": "", "stderr": stderr[:4096],
                "exit_code": process.returncode, "timed_out": timed_out,
                "elapsed_seconds": elapsed,
                "error": f"Agent stdout exceeds {MAX_AGENT_OUTPUT_BYTES} bytes"}
    stderr = stderr[:MAX_AGENT_OUTPUT_BYTES]
    status = "timeout" if timed_out else ("pass" if process.returncode == 0 else "error")
    return {"status": status, "stdout": stdout, "stderr": stderr,
            "exit_code": process.returncode, "timed_out": timed_out,
            "elapsed_seconds": elapsed,
            "error": None if status == "pass" else (
                "Agent command timed out" if timed_out
                else f"Agent command exited with code {process.returncode}")}


def _parse_response(stdout: str) -> dict:
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent stdout must contain one JSON response: {exc}") from exc
    if not isinstance(response, dict):
        raise ValueError("Agent response root must be a JSON object")
    allowed = {"schema_version", "action", "proposal", "rationale"}
    unknown = set(response) - allowed
    if unknown:
        raise ValueError(f"Agent response contains unknown fields: {sorted(unknown)}")
    if response.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported Agent response schema_version")
    action = response.get("action")
    if not isinstance(action, str) or action not in {"patch", "stop"}:
        raise ValueError("Agent action must be patch or stop")
    rationale = response.get("rationale", "")
    if not isinstance(rationale, str) or len(rationale) > 2_000:
        raise ValueError("Agent rationale must be a string of at most 2000 characters")
    if action == "patch" and not isinstance(response.get("proposal"), dict):
        raise ValueError("Agent patch action requires a proposal object")
    if action == "stop" and "proposal" in response:
        raise ValueError("Agent stop action must not include a proposal")
    return response


def _feedback(execution: dict, metrics: dict, output: Path) -> dict:
    report = execution.get("report") or {}
    signature = report.get("result_signature", {})
    checks = report.get("checks", {})
    synthesis = signature.get("functional_synthesis", {}).get("synthesis", {})
    mutation = signature.get("mutation", {})
    formal = signature.get("formal", {})
    equivalence = signature.get("equivalence", {})
    return {
        "status": execution.get("status", "error"),
        "feasible": metrics.get("feasible", False),
        "timed_out": execution.get("timed_out", False),
        "error": execution.get("error"),
        "candidate_sha256": report.get("candidate_sha256"),
        "candidate_snapshot_intact": report.get("candidate_snapshot_intact"),
        "checks": {name: item.get("status") for name, item in sorted(checks.items())},
        "failed_gates": sorted(name for name, item in checks.items()
                               if item.get("status") != "pass"),
        "generic_cell_count": metrics.get("generic_cell_count"),
        "mutation_score": mutation.get("mutation_score"),
        "formal_proof_status": formal.get("proof_status"),
        "equivalence_proof_status": equivalence.get("proof_status"),
        "result_signature": signature,
        "experiment_report": (
            _repo_path(output / "experiment_report.json")
            if (output / "experiment_report.json").is_file() else None
        ),
        "elapsed_seconds": execution.get("elapsed_seconds"),
    }


def _execution_record(
    candidate_id: str, candidate: Path, digest: str, output: Path, execution: dict
) -> dict:
    metrics = _metrics(execution, digest)
    return {
        "candidate_id": candidate_id,
        "candidate_path": _repo_path(candidate),
        "candidate_sha256": digest,
        "experiment_directory": output.relative_to(ROOT).as_posix(),
        **execution,
        **metrics,
        "feedback": _feedback(execution, metrics, output),
    }


def _adapter_files(command: list[str]) -> list[dict]:
    """Record hashes for local executable/script files; never capture environment secrets."""
    candidates = []
    executable = shutil.which(command[0]) or command[0]
    if Path(executable).is_file():
        candidates.append(("executable", Path(executable)))
    for index, arg in enumerate(command[1:], 1):
        if arg.startswith("-"):
            continue
        path = Path(arg)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            candidates.append((f"argument_{index}", path))
    result, seen = [], set()
    for role, path in candidates:
        resolved = path.resolve()
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        result.append({"role": role, "path": str(resolved),
                       "sha256": _sha_file(resolved), "size_bytes": resolved.stat().st_size})
    return result


def run_agent_loop(
    command: list[str],
    output: Path,
    budget: AgentLoopBudget | None = None,
    seed: int = 20261113,
    loop_id: str | None = None,
) -> dict:
    """Evaluate a baseline, solicit Agent patches, and feed each result back."""
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)):
        raise ValueError("command must be a non-empty argv list")
    budget = budget or AgentLoopBudget()
    budget.validate()
    loop_id = _safe_id(
        loop_id or datetime.now(timezone.utc).strftime("loop-%Y%m%dT%H%M%S%fZ"),
        "loop_id",
    )
    root = ROOT.resolve()
    output = output.resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Agent-loop output must be inside the repository root") from exc
    output.mkdir(parents=True, exist_ok=True)
    for name in ("agent_loop_manifest.json", "agent_loop_report.json", "agent_loop_ledger.jsonl"):
        if (output / name).exists():
            raise FileExistsError(f"Refusing to overwrite existing Agent loop: {output / name}")

    baseline = root / PATCH_TARGET_PATH
    if baseline.is_symlink() or not baseline.is_file():
        raise ValueError("Golden CORDIC candidate must be a regular, non-symlink file")
    baseline = baseline.resolve(strict=True)
    baseline_rel = _repo_path(baseline)
    baseline_hash = _sha_file(baseline)
    if baseline.stat().st_size > budget.max_candidate_bytes:
        raise ValueError("Golden candidate exceeds max_candidate_bytes")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": "chiprd_agent_closed_loop",
        "loop_id": loop_id,
        "created_at_utc": _now(),
        "task_id": "cordic.rotation.q14.v1",
        "agent": {
            "command_argv": command,
            "local_file_fingerprints": _adapter_files(command),
            "protocol": "one JSON request on stdin; one JSON response on stdout",
            "environment_secrets": "not captured; pass credentials through environment variables",
        },
        "baseline": {"candidate_id": "baseline", "candidate_path": baseline_rel,
                     "candidate_sha256": baseline_hash},
        "budget": asdict(budget),
        "seed": seed,
        "objective": {
            "hard_constraint": "all six public experiment gates must pass",
            "metric": "minimize Yosys generic_cell_count",
            "metric_boundary": "generic synthesis proxy only; not physical PPA",
        },
        "controller_sha256": _sha_file(Path(__file__).resolve()),
    }
    manifest_path = output / "agent_loop_manifest.json"
    ledger_path = output / "agent_loop_ledger.jsonl"
    _write_json(manifest_path, manifest)
    _append_event(ledger_path, "loop_started",
                  manifest_sha256=_sha_file(manifest_path),
                  baseline_candidate_sha256=baseline_hash,
                  controller_sha256=manifest["controller_sha256"])

    started = time.monotonic()
    deadline = started + budget.max_wall_clock_seconds
    baseline_dir = output / "experiments" / "baseline"
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        baseline_execution = {"status": "timeout", "report": None, "elapsed_seconds": 0.0,
                              "exit_code": None, "timed_out": True,
                              "error": "wall-clock budget exhausted before baseline"}
    else:
        baseline_execution = _run_sealed_experiment(
            baseline, baseline_dir, seed, min(budget.max_experiment_seconds, remaining)
        )
    baseline_record = _execution_record(
        "baseline", baseline, baseline_hash, baseline_dir, baseline_execution
    )
    _append_event(ledger_path, "baseline_experiment_completed",
                  status=baseline_record["status"], feasible=baseline_record["feasible"],
                  candidate_sha256=baseline_hash,
                  report_sha256=(_sha_file(baseline_dir / "experiment_report.json")
                                 if (baseline_dir / "experiment_report.json").is_file() else None))

    parent = {"candidate_id": "baseline", "candidate_path": baseline_rel,
              "candidate_sha256": baseline_hash, "generation": 0}
    best = ({"candidate_id": "baseline", "candidate_path": baseline_rel,
             "candidate_sha256": baseline_hash, "generation": 0,
             "generic_cell_count": baseline_record["generic_cell_count"]}
            if baseline_record["feasible"] else None)
    previous_experiment = baseline_record["feedback"]
    previous_iteration = None
    iterations, experiments = [], [baseline_record]
    stop_reason = "max_iterations"

    for number in range(1, budget.max_iterations + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop_reason = "max_wall_clock_seconds"
            break
        io_dir = output / "agent-io" / f"iteration-{number:03d}"
        request = {
            "schema_version": SCHEMA_VERSION,
            "task_id": "cordic.rotation.q14.v1",
            "loop_id": loop_id,
            "iteration": number,
            "max_iterations": budget.max_iterations,
            "remaining_wall_clock_seconds": round(remaining, 3),
            "parent_candidate": parent,
            "best_candidate": best,
            "baseline_experiment": baseline_record["feedback"],
            "previous_experiment": previous_experiment,
            "previous_iteration": previous_iteration,
            "objective": manifest["objective"],
            "constraints": {
                "patch_format": "unified_diff",
                "one_existing_candidate_file_only": True,
                "patch_max_bytes": 262_144,
                "candidate_max_bytes": budget.max_candidate_bytes,
                "must_preserve_parent": True,
                "must_not_claim_physical_ppa": True,
            },
            "response_contract": {
                "schema_version": SCHEMA_VERSION,
                "action": "patch or stop",
                "patch": "proposal object matching the Agent RTL patch intake schema",
                "stop": "omit proposal and provide a short rationale",
            },
        }
        request_path = io_dir / "request.json"
        _write_json(request_path, request)
        agent = _run_agent(command, request, min(budget.max_agent_seconds, remaining))
        (io_dir / "agent.stdout.txt").write_text(agent.get("stdout", ""), encoding="utf-8")
        (io_dir / "agent.stderr.txt").write_text(agent.get("stderr", ""), encoding="utf-8")
        item = {
            "iteration": number,
            "parent_candidate": parent,
            "request_sha256": _sha_file(request_path),
            "agent_stdout_sha256": _sha(agent.get("stdout", "").encode()),
            "agent_stderr_sha256": _sha(agent.get("stderr", "").encode()),
            "agent_status": agent["status"],
            "agent_exit_code": agent.get("exit_code"),
            "agent_elapsed_seconds": agent.get("elapsed_seconds"),
        }
        if agent["status"] != "pass":
            item.update(status="error", error=agent.get("error"))
            iterations.append(item)
            _append_event(ledger_path, "agent_invocation_failed", iteration=number,
                          status=agent["status"], request_sha256=item["request_sha256"])
            stop_reason = "agent_timeout" if agent.get("timed_out") else "agent_execution_error"
            break
        try:
            response = _parse_response(agent["stdout"])
        except ValueError as exc:
            item.update(status="error", error=str(exc))
            iterations.append(item)
            _append_event(ledger_path, "agent_response_rejected", iteration=number,
                          error=str(exc), stdout_sha256=item["agent_stdout_sha256"])
            stop_reason = "agent_protocol_error"
            break

        response_path = io_dir / "agent_response.json"
        _write_json(response_path, response)
        item["agent_response_sha256"] = _sha_file(response_path)
        item["rationale"] = response.get("rationale", "")
        if response["action"] == "stop":
            item["status"] = "agent_stopped"
            iterations.append(item)
            _append_event(ledger_path, "agent_stopped", iteration=number,
                          rationale=item["rationale"],
                          response_sha256=item["agent_response_sha256"])
            stop_reason = "agent_stop"
            break

        proposal = response["proposal"]
        expected = {
            "parent_candidate_id": parent["candidate_id"],
            "parent_candidate_path": parent["candidate_path"],
            "parent_candidate_sha256": parent["candidate_sha256"],
            "generation": parent["generation"] + 1,
        }
        mismatched = [key for key, value in expected.items() if proposal.get(key) != value]
        if mismatched:
            item.update(status="proposal_rejected",
                        error="proposal lineage mismatch: " + ", ".join(mismatched))
            iterations.append(item)
            previous_iteration = {"iteration": number, "status": item["status"],
                                  "error": item["error"]}
            _append_event(ledger_path, "agent_proposal_rejected", iteration=number,
                          mismatched_fields=mismatched,
                          response_sha256=item["agent_response_sha256"])
            continue

        proposal_path = io_dir / "patch_proposal.json"
        _write_json(proposal_path, proposal)
        item["proposal_sha256"] = _sha_file(proposal_path)
        try:
            patch = apply_patch_proposal(
                proposal_path, root / "artifacts" / "agent-patches"
            )
        except (OSError, ValueError, RuntimeError) as exc:
            item.update(status="patch_rejected", error=f"{type(exc).__name__}: {exc}")
            iterations.append(item)
            previous_iteration = {"iteration": number, "status": item["status"],
                                  "error": item["error"]}
            _append_event(ledger_path, "patch_application_rejected", iteration=number,
                          error=item["error"], proposal_sha256=item["proposal_sha256"])
            continue

        candidate = Path(patch["candidate_path"]).resolve(strict=True)
        candidate_rel = _repo_path(candidate)
        candidate_hash = patch["candidate_sha256"]
        candidate_size = candidate.stat().st_size
        item["patch"] = {
            "patch_id": patch["patch_id"],
            "manifest_path": _repo_path(Path(patch["manifest_path"])),
            "manifest_sha256": _sha_file(Path(patch["manifest_path"])),
            "patch_sha256": _sha_file(Path(patch["patch_path"])),
            "candidate_path": candidate_rel,
            "candidate_sha256": candidate_hash,
            "candidate_size_bytes": candidate_size,
        }
        if candidate_size > budget.max_candidate_bytes:
            item.update(status="candidate_rejected",
                        error=f"candidate size {candidate_size} exceeds max_candidate_bytes")
            iterations.append(item)
            previous_iteration = {"iteration": number, "status": item["status"],
                                  "error": item["error"]}
            _append_event(ledger_path, "candidate_rejected", iteration=number,
                          candidate_sha256=candidate_hash, error=item["error"])
            continue

        remaining = deadline - time.monotonic()
        exp_dir = output / "experiments" / f"iteration-{number:03d}"
        if remaining <= 0:
            execution = {"status": "timeout", "report": None, "elapsed_seconds": 0.0,
                         "exit_code": None, "timed_out": True,
                         "error": "wall-clock budget exhausted before candidate experiment"}
        else:
            execution = _run_sealed_experiment(
                candidate, exp_dir, seed, min(budget.max_experiment_seconds, remaining)
            )
        record = _execution_record(patch["patch_id"], candidate, candidate_hash,
                                   exp_dir, execution)
        experiments.append(record)
        item["experiment"] = record["feedback"]
        item["status"] = "evaluated" if record["status"] in {"pass", "fail"} else record["status"]
        if record["feasible"]:
            candidate_best = {
                "candidate_id": patch["patch_id"], "candidate_path": candidate_rel,
                "candidate_sha256": candidate_hash,
                "generation": parent["generation"] + 1,
                "generic_cell_count": record["generic_cell_count"],
            }
            if best is None or candidate_best["generic_cell_count"] < best["generic_cell_count"]:
                best = candidate_best
        if baseline_record["feasible"] and baseline_record["generic_cell_count"] > 0:
            item["improvement_vs_baseline_percent"] = round(
                (baseline_record["generic_cell_count"] - record["generic_cell_count"])
                / baseline_record["generic_cell_count"] * 100.0, 4
            ) if record["feasible"] else None
        iterations.append(item)
        parent = {"candidate_id": patch["patch_id"], "candidate_path": candidate_rel,
                  "candidate_sha256": candidate_hash, "generation": expected["generation"]}
        previous_experiment = record["feedback"]
        previous_iteration = {"iteration": number, "status": item["status"],
                              "patch_id": patch["patch_id"],
                              "candidate_sha256": candidate_hash,
                              "experiment": record["feedback"]}
        _append_event(
            ledger_path, "candidate_experiment_completed", iteration=number,
            patch_id=patch["patch_id"], candidate_sha256=candidate_hash,
            experiment_status=record["status"], feasible=record["feasible"],
            generic_cell_count=record["generic_cell_count"],
            report_sha256=(_sha_file(exp_dir / "experiment_report.json")
                           if (exp_dir / "experiment_report.json").is_file() else None),
        )
        if time.monotonic() >= deadline:
            stop_reason = "max_wall_clock_seconds"
            break
    else:
        stop_reason = "max_iterations"

    elapsed = round(time.monotonic() - started, 3)
    status = "pass" if best is not None else "fail"
    if stop_reason in {"agent_timeout", "agent_execution_error", "agent_protocol_error"}:
        status = "partial" if best is not None else "error"
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "chiprd_agent_closed_loop",
        "loop_id": loop_id,
        "status": status,
        "stop_reason": stop_reason,
        "elapsed_seconds": elapsed,
        "budget": asdict(budget),
        "budget_consumed": {
            "agent_iterations": len(iterations),
            "candidate_experiments": len(experiments),
            "wall_clock_seconds": elapsed,
        },
        "baseline": {
            "candidate_id": "baseline", "candidate_sha256": baseline_hash,
            "status": baseline_record["status"], "feasible": baseline_record["feasible"],
            "generic_cell_count": baseline_record["generic_cell_count"],
        },
        "best_candidate": best,
        "improved_vs_baseline": bool(
            best and best["candidate_id"] != "baseline" and baseline_record["feasible"]
            and best["generic_cell_count"] < baseline_record["generic_cell_count"]
        ),
        "iterations": iterations,
        "experiments": [{
            "candidate_id": record["candidate_id"],
            "candidate_sha256": record["candidate_sha256"],
            "status": record["status"], "feasible": record["feasible"],
            "generic_cell_count": record["generic_cell_count"],
            "gate_statuses": record["gate_statuses"],
            "experiment_directory": record["experiment_directory"],
        } for record in experiments],
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "ledger_path": ledger_path.relative_to(root).as_posix(),
    }
    report_path = output / "agent_loop_report.json"
    _write_json(report_path, report)
    _append_event(ledger_path, "loop_completed", status=status,
                  stop_reason=stop_reason, report_sha256=_sha_file(report_path),
                  best_candidate_sha256=best["candidate_sha256"] if best else None)
    return {**report, "manifest_path": str(manifest_path),
            "report_path": str(report_path), "ledger_path": str(ledger_path)}
