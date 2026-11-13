"""Validated, single-file RTL patch intake for the Phase 0 CORDIC task.

Patches are supplied by an external Agent or user. This module applies them to
an isolated copy of the selected parent candidate; it does not generate or execute code.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .harness import ROOT

PATCH_SCHEMA_VERSION = "1.0.0"
TASK_ID = "cordic.rotation.q14.v1"
PATCH_TARGET_PATH = "tasks/cordic/cordic_rotator.sv"
PATCH_MAX_BYTES = 262_144
CANDIDATE_MAX_BYTES = 1_048_576
PATCH_TIMEOUT_SECONDS = 20
_REQUIRED_FIELDS = {
    "schema_version",
    "task_id",
    "patch_id",
    "parent_candidate_id",
    "generation",
    "parent_candidate_path",
    "parent_candidate_sha256",
    "patch_format",
    "patch",
}
_OPTIONAL_FIELDS = {"description", "predicted_improvement"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field} must be a 1-64 character safe identifier")
    return value


def _load_proposal(path: Path) -> tuple[dict, bytes]:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Patch proposal must be a regular file")
    raw = path.read_bytes()
    if len(raw) > PATCH_MAX_BYTES * 2:
        raise ValueError("Patch proposal JSON exceeds the size limit")
    try:
        proposal = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid patch proposal JSON: {exc}") from exc
    if not isinstance(proposal, dict):
        raise ValueError("Patch proposal root must be a JSON object")
    missing = _REQUIRED_FIELDS - set(proposal)
    unknown = set(proposal) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
    if missing:
        raise ValueError(f"Patch proposal missing required fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"Patch proposal contains unknown fields: {sorted(unknown)}")
    return proposal, raw


def _validate_parent_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("parent_candidate_path must be a repository-relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("parent_candidate_path must not contain traversal or non-normalized segments")
    if value == PATCH_TARGET_PATH:
        return value
    parts = path.parts
    if (
        len(parts) == 4
        and parts[0] == "artifacts"
        and parts[1] == "agent-patches"
        and parts[3] == "candidate.sv"
    ):
        _safe_identifier(parts[2], "parent_candidate_path patch id")
        return value
    raise ValueError(
        "parent_candidate_path must be the golden RTL or a prior artifacts/agent-patches/<id>/candidate.sv"
    )


def _validate_proposal(proposal: dict) -> dict:
    if proposal["schema_version"] != PATCH_SCHEMA_VERSION:
        raise ValueError("Unsupported patch proposal schema_version")
    if proposal["task_id"] != TASK_ID:
        raise ValueError("Patch proposal task_id does not match the Phase 0 task")

    patch_id = _safe_identifier(proposal["patch_id"], "patch_id")
    parent_id = _safe_identifier(proposal["parent_candidate_id"], "parent_candidate_id")
    parent_candidate_path = _validate_parent_path(proposal["parent_candidate_path"])
    if not isinstance(proposal["parent_candidate_sha256"], str) or not _SHA256.fullmatch(
        proposal["parent_candidate_sha256"]
    ):
        raise ValueError("parent_candidate_sha256 must be a lowercase SHA-256 digest")
    generation = proposal["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("generation must be a positive integer")
    if proposal["patch_format"] != "unified_diff":
        raise ValueError("patch_format must be unified_diff")

    patch = proposal["patch"]
    if not isinstance(patch, str) or not patch:
        raise ValueError("patch must be a non-empty unified diff string")
    try:
        patch_bytes = patch.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("patch must contain valid UTF-8 text") from exc
    if len(patch_bytes) > PATCH_MAX_BYTES:
        raise ValueError(f"Patch exceeds PATCH_MAX_BYTES={PATCH_MAX_BYTES}")
    if "\x00" in patch or not patch.endswith("\n"):
        raise ValueError("Patch must be UTF-8 text, NUL-free and newline-terminated")

    description = proposal.get("description", "")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("description must be a string of at most 500 characters")
    priority = proposal.get("predicted_improvement", 0.0)
    if (
        isinstance(priority, bool)
        or not isinstance(priority, (int, float))
        or not math.isfinite(float(priority))
    ):
        raise ValueError("predicted_improvement must be a finite number")

    return {
        "patch_id": patch_id,
        "parent_candidate_id": parent_id,
        "parent_candidate_path": parent_candidate_path,
        "generation": generation,
        "description": description,
        "predicted_improvement": float(priority),
        "patch": patch,
        "patch_sha256": sha256_bytes(patch_bytes),
        "patch_size_bytes": len(patch_bytes),
    }


def _validate_unified_diff(patch: str, target_path: str) -> None:
    lines = patch.splitlines()
    expected_old = f"--- a/{target_path}"
    expected_new = f"+++ b/{target_path}"
    if len(lines) < 4 or lines[0] != expected_old or lines[1] != expected_new:
        raise ValueError("Patch must target only the approved CORDIC RTL file")
    if sum(line.startswith("--- ") for line in lines) != 1:
        raise ValueError("Patch must contain exactly one old-file header")
    if sum(line.startswith("+++ ") for line in lines) != 1:
        raise ValueError("Patch must contain exactly one new-file header")
    if not any(line.startswith("@@ ") for line in lines):
        raise ValueError("Patch must contain at least one unified-diff hunk")
    forbidden = (
        "diff --git ",
        "index ",
        "new file mode ",
        "deleted file mode ",
        "old mode ",
        "new mode ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "GIT binary patch",
        "Binary files ",
        "Subproject commit ",
    )
    if any(line.startswith(forbidden) for line in lines):
        raise ValueError("Binary, multi-file, rename and file-mode patches are not supported")


def _run_git_apply(workspace: Path, patch: str, check_only: bool) -> None:
    command = ["git", "apply"]
    if check_only:
        command.append("--check")
    command.extend(["--whitespace=error", "-"])
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            input=patch,
            text=True,
            capture_output=True,
            timeout=PATCH_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is required to apply RTL patches") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("git apply timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[:2000]
        action = "check" if check_only else "apply"
        raise ValueError(f"git apply --{action} failed: {detail}")


def apply_patch_proposal(proposal_path: Path, output: Path) -> dict:
    """Apply one validated Agent patch to an isolated copy of its parent RTL."""
    proposal, raw_proposal = _load_proposal(proposal_path)
    normalized = _validate_proposal(proposal)
    _validate_unified_diff(normalized["patch"], normalized["parent_candidate_path"])

    root = ROOT.resolve()
    parent_source = root / normalized["parent_candidate_path"]
    if parent_source.is_symlink() or not parent_source.is_file():
        raise ValueError("Approved parent RTL must be a regular, non-symlink file")
    parent_source = parent_source.resolve(strict=True)
    try:
        parent_source.relative_to(root)
    except ValueError as exc:
        raise ValueError("Parent RTL escapes the repository root") from exc
    parent_bytes = parent_source.read_bytes()
    parent_sha256 = sha256_bytes(parent_bytes)
    if parent_sha256 != proposal["parent_candidate_sha256"]:
        raise ValueError("Parent candidate SHA-256 mismatch; patch is stale or based on another RTL")

    output_root = output.resolve()
    try:
        output_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("Patch output directory must be inside the repository root") from exc
    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / normalized["patch_id"]
    if final_dir.exists():
        raise FileExistsError(f"Refusing to overwrite patch output: {final_dir}")

    try:
        git_version = subprocess.run(
            ["git", "--version"], cwd=root, text=True, capture_output=True,
            timeout=PATCH_TIMEOUT_SECONDS, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Cannot determine git version: {exc}") from exc

    with tempfile.TemporaryDirectory(
        prefix=f".{normalized['patch_id']}-", dir=output_root
    ) as temporary:
        staging = Path(temporary)
        workspace = staging / "workspace"
        staged_parent = workspace / normalized["parent_candidate_path"]
        staged_parent.parent.mkdir(parents=True, exist_ok=True)
        staged_parent.write_bytes(parent_bytes)

        _run_git_apply(workspace, normalized["patch"], check_only=True)
        _run_git_apply(workspace, normalized["patch"], check_only=False)

        files = sorted(
            item.relative_to(workspace).as_posix()
            for item in workspace.rglob("*")
            if item.is_file() or item.is_symlink()
        )
        if files != [normalized["parent_candidate_path"]]:
            raise ValueError("Patch changed files outside the single approved RTL target")
        candidate_bytes = staged_parent.read_bytes()
        candidate_sha256 = sha256_bytes(candidate_bytes)
        if candidate_sha256 == parent_sha256:
            raise ValueError("Patch did not change the candidate RTL")
        if len(candidate_bytes) > CANDIDATE_MAX_BYTES:
            raise ValueError(
                f"Patched candidate exceeds CANDIDATE_MAX_BYTES={CANDIDATE_MAX_BYTES}"
            )

        result_dir = staging / "result"
        result_dir.mkdir()
        (result_dir / "candidate.sv").write_bytes(candidate_bytes)
        (result_dir / "patch.diff").write_text(normalized["patch"], encoding="utf-8")
        candidate_relative = (final_dir / "candidate.sv").relative_to(root).as_posix()
        manifest = {
            "schema_version": PATCH_SCHEMA_VERSION,
            "manifest_type": "chiprd_agent_patch",
            "status": "pass",
            "created_at_utc": _utc_now(),
            "task_id": TASK_ID,
            "patch_id": normalized["patch_id"],
            "description": normalized["description"],
            "parent": {
                "candidate_id": normalized["parent_candidate_id"],
                "path": normalized["parent_candidate_path"],
                "sha256": parent_sha256,
            },
            "patch": {
                "format": "unified_diff",
                "sha256": normalized["patch_sha256"],
                "size_bytes": normalized["patch_size_bytes"],
                "proposal_sha256": sha256_bytes(raw_proposal),
            },
            "candidate": {
                "path": candidate_relative,
                "sha256": candidate_sha256,
                "size_bytes": len(candidate_bytes),
            },
            "search_proposal": {
                "candidate_id": normalized["patch_id"],
                "candidate_path": candidate_relative,
                "parent_id": normalized["parent_candidate_id"],
                "generation": normalized["generation"],
                "predicted_improvement": normalized["predicted_improvement"],
            },
            "application": {
                "tool": "git apply",
                "tool_version": git_version,
                "patcher_sha256": sha256_bytes(Path(__file__).read_bytes()),
            },
            "limitations": [
                "Only one existing CORDIC RTL source file may be modified.",
                "The patch is supplied by an external Agent or user; this tool does not generate RTL.",
                "Patch application is not a security sandbox; evaluate untrusted HDL only in an isolated runner.",
            ],
        }
        (result_dir / "patch_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if final_dir.exists():
            raise FileExistsError(f"Refusing to overwrite patch output: {final_dir}")
        result_dir.rename(final_dir)

    return {
        "status": "pass",
        "patch_id": normalized["patch_id"],
        "candidate_path": str(final_dir / "candidate.sv"),
        "candidate_sha256": candidate_sha256,
        "manifest_path": str(final_dir / "patch_manifest.json"),
        "patch_path": str(final_dir / "patch.diff"),
        "search_proposal": manifest["search_proposal"],
    }
